"""Fakes for the Qwen/AVTR worker processes and a recording event sink."""
import asyncio
import base64
import functools

import numpy as np

from chatbot.avatar.config import MediaConfig
from chatbot.avatar.media import AvatarMediaEngine
from chatbot.avatar.protocol import MediaFrame, unpack_frame
from chatbot.avatar.stt import Transcriber

SR = 24000

# A resolved saved voice as the runtime passes it to the engine (see media.VOICE_FIELDS).
TEST_VOICE = {"voice_id": "voice-test", "fingerprint": "f" * 64, "reference": "library/voice-test/reference.wav",
              "transcript": "This is the reference recording.", "language": "English"}


def tone(seconds, sr=SR, lead=0.0, tail=0.0):
    body = 0.4 * np.sin(np.linspace(0, 2 * np.pi * 220 * seconds, int(seconds * sr))).astype("<f4")
    return np.concatenate((np.zeros(int(lead * sr), "<f4"), body, np.zeros(int(tail * sr), "<f4")))


class FakeVoices:
    """Stands in for the database voice resolver: a fixed voice, or a configured one/error."""

    def __init__(self, voice=None):
        self.voice = dict(voice or TEST_VOICE)
        self.error = None
        self.calls = []

    async def resolve(self, binding):
        self.calls.append(binding.session_id)
        if self.error:
            raise self.error
        return {**self.voice, "source": "session"}


class FakeWorker:
    """Stands in for `Worker`: same attributes the engine touches, no process."""

    def __init__(self, name):
        self.name = name
        self.messages = asyncio.Queue(maxsize=64)
        self.sent = []
        self.starts = 0
        self.stops = 0
        self.ready_info = None
        self.dropped_frames = 0
        self._alive = False
        self.fail_start = None

    @property
    def alive(self):
        return self._alive

    async def start(self):
        if self.fail_start:
            raise self.fail_start
        self.starts += 1
        self._alive = True

    async def stop(self):
        self.stops += 1
        self._alive = False

    async def send(self, message):
        if not self._alive:
            raise RuntimeError(f"{self.name} is not running")
        self.sent.append(message)
        await self.react(message)

    async def react(self, message):
        pass

    def reply(self, message, delay=0.0):
        async def deliver():
            if delay:
                await asyncio.sleep(delay)
            await self.messages.put(message)
        return asyncio.create_task(deliver())


class FakeTTS(FakeWorker):
    """Stands in for voice_worker.py: Base speaks saved voices, VoiceDesign designs them."""

    def __init__(self):
        super().__init__("TTS")
        self.hold = None            # asyncio.Event: replies wait for it (simulates a busy Qwen)
        self.synth_delay = 0.005
        self.replies = []
        self.prompts = set()        # (voice_id, fingerprint) with a built clone prompt
        self.design_loaded = False
        self.fail_design = None

    @staticmethod
    def speech_seconds(text):
        return 0.004 * len(text) + 0.05   # fast fake speech keeps the planner's lead small

    async def react(self, message):
        kind = message["type"]
        if kind == "design":
            if self.fail_design:
                self.reply({"type": "error", "id": message["id"], "error": self.fail_design})
                return
            self.design_loaded = True
        elif kind == "prepare":
            key = (message["voice"]["voice_id"], message["voice"]["fingerprint"])
            cache = "hit" if key in self.prompts else "built"
            self.prompts.add(key)
            self.reply({"type": "prepared", "id": message["id"], "voice_id": key[0], "fingerprint": key[1],
                        "prompt": cache, "prepare_ms": 1.0, "warm_ms": None})
            return
        elif kind == "forget":
            dropped = {k for k in self.prompts if k[0] == message["voice_id"]}
            self.prompts -= dropped
            self.reply({"type": "forgotten", "id": message["id"], "voice_id": message["voice_id"],
                        "dropped": len(dropped)})
            return
        elif kind == "unload_design":
            released, self.design_loaded = self.design_loaded, False
            self.reply({"type": "design_unloaded", "id": message["id"], "released": released,
                        "reason": "request"})
            return
        elif kind == "status":
            self.reply({"type": "status", "id": message["id"], "base_loaded": True,
                        "design_loaded": self.design_loaded, "base_loads": 1, "design_loads": 0,
                        "cached_prompts": [{"voice_id": v, "fingerprint": f} for v, f in sorted(self.prompts)]})
            return
        elif kind != "speak":
            return
        extra = {"model": "design"} if kind == "design" else {}
        if kind == "speak":
            key = (message["voice"]["voice_id"], message["voice"]["fingerprint"])
            extra = {"model": "base", "prompt": "hit" if key in self.prompts else "built",
                     "voice_id": key[0], "fingerprint": key[1]}
            self.prompts.add(key)
        text = message["text"]
        duration = self.speech_seconds(text)
        audio = tone(duration, lead=0.3, tail=0.4)   # Qwen-style edge silence for trim_silence

        async def answer():
            if self.hold is not None:
                await self.hold.wait()
            await asyncio.sleep(self.synth_delay)
            await self.messages.put({
                "type": "audio", "id": message["id"], "sr": SR, "duration": len(audio) / SR,
                "audio": base64.b64encode(audio.tobytes()).decode(), "generation": 0.8 * duration,
                "retried": False, **extra})
        self.replies.append(asyncio.create_task(answer()))


class FakeAvatar(FakeWorker):
    def __init__(self):
        super().__init__("AVTR")
        self.portraits = []
        self.reject = set()
        self.cached = set()
        self.ready_info = {"window": 6400, "step": 3200}

    async def react(self, message):
        kind = message["type"]
        if kind == "speak":
            self.reply({"type": "audio_received", "id": message["id"], "duration": 0.1, "at": 1.0})
            for index in range(3):
                self.reply({"type": "frame", "kind": "speech", "id": message["id"], "first": index == 0,
                            "pts": index * 0.04, "generated_at": 1.0,
                            "jpeg": base64.b64encode(b"\xff\xd8jpeg").decode()})
        elif kind == "interrupt":
            self.reply({"type": "interrupted", "at": 1.0})
        elif kind == "portrait":
            if message["path"] in self.reject:
                self.reply({"type": "portrait_error", "request_id": message["request_id"],
                            "portrait_id": message["portrait_id"], "error": "no face detected"})
            else:
                cached = message["portrait_id"] in self.cached
                self.cached.add(message["portrait_id"])
                self.portraits.append(message["portrait_id"])
                self.reply({"type": "portrait_loaded", "request_id": message["request_id"],
                            "portrait_id": message["portrait_id"], "cached": cached, "load_ms": 12.0})


class Harness:
    """An engine on fake workers with an auto-responding 'browser'."""

    def __init__(self, auto_playback=True, playback_delay=0.01, **config):
        self.avatar, self.tts = FakeAvatar(), FakeTTS()
        self.events = []
        self.auto_playback = auto_playback
        self.playback_delay = playback_delay
        self.transcripts = []
        self.engine = AvatarMediaEngine(
            MediaConfig(**config), sink=self.sink,
            worker_factory=lambda _: (self.avatar, self.tts),
            transcriber=Transcriber(model_factory=lambda *a, **k: self._model()),
            resampler=lambda audio, sr: audio[:: sr // 16000].astype("<f4"))
        # Turns require a resolved voice; tests get a fixed one unless they pass `voice=`.
        self.voice = dict(TEST_VOICE)
        self.engine.begin_response = functools.partial(self.engine.begin_response, voice=self.voice)

    def _model(self):
        outer = self

        class Model:
            def transcribe(self, audio, **_):
                text = outer.transcripts.pop(0) if outer.transcripts else "hello"
                return [type("Segment", (), {"text": text})()], None
        return Model()

    async def sink(self, event):
        self.events.append(event)
        if self.auto_playback and isinstance(event, MediaFrame) and event.kind == "audio":
            segment_id = unpack_frame(event.data)[1]

            async def finish():
                await asyncio.sleep(self.playback_delay)
                await self.engine.handle_client_message({"type": "playback_end", "id": segment_id})
            asyncio.create_task(finish())

    def of(self, cls):
        return [e for e in self.events if isinstance(e, cls)]

    def frames(self, kind):
        return [e for e in self.of(MediaFrame) if e.kind == kind]

    def statuses(self):
        return [e.payload["value"] for e in self.events
                if type(e).__name__ == "UiMessage" and e.payload.get("type") == "status"]

    async def ready(self, portrait="/tmp/claude-1000/portrait.png"):
        await self.engine.start()
        if portrait:
            self.make_portrait(portrait)
            result = await self.engine.set_portrait(portrait)
            assert result.ok, result.error
        return self

    @staticmethod
    def make_portrait(path, magic=b"\x89PNG\r\n\x1a\n"):
        import pathlib
        target = pathlib.Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(magic + b"fake-image-bytes" + str(path).encode())
        return target


async def wait_for(predicate, timeout=3.0, step=0.005):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(step)
