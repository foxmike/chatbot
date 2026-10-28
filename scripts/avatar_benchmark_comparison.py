#!/usr/bin/env python
"""Lightweight benchmark comparison utility for the avatar pipeline.

Drives the JSON-lines worker protocol that my_avatar's persistent workers
speak (tts_worker.py, avatar_worker.py) against a *target implementation*,
and reports observed media-pipeline measurements. It never invents numbers:
every reported value comes from worker messages observed at run time.

Targets
-------
standalone (default):
    Spawns the standalone my_avatar workers directly, read-only, using the
    paths from my_avatar/.env (AVTR_PATH, QWEN_PATH, AVATAR_IMAGE,
    VOICE_MODEL, VOICE) or explicit CLI overrides. The standalone
    implementation is not modified in any way.

integrated:
    ``--tts-cmd`` / ``--avtr-cmd`` accept arbitrary worker commands, so the
    integrated chatbot's media runtime can be benchmarked once it exposes
    the same JSON-lines worker contract (see
    docs/avatar/integration-benchmarks.md). Until that contract lands, the
    integrated target cannot be exercised; the script fails with a clear
    message rather than fabricating a comparison.

Measured per text case (short/medium/long), sequentially — never
concurrent GPU runs:

    first TTS PCM, per-chunk TTS RTF, first AVTR frame after PCM,
    inter-chunk audio gaps, AVTR FPS (from worker fps messages),
    per-segment A/V pacing drift, interruption latency (worker
    "interrupted" acknowledgement after a mid-speech interrupt).

Endpoint detection, STT time, context-retrieval overhead, first LLM token,
and true browser playback start require the full conversational stack and
are out of scope here; they are specified in
docs/avatar/integration-benchmarks.md as the integration measurement
contract.

Usage (standalone comparison baseline):
    python scripts/avatar_benchmark_comparison.py --case short

Usage (integrated target, once the worker contract exists):
    python scripts/avatar_benchmark_comparison.py \
        --tts-cmd "<python> tts_worker.py <model> <voice>" \
        --avtr-cmd "<python> avatar_worker.py <portrait.png>" \
        --case medium
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

MY_AVATAR_PY = Path.home() / "dev/mike/my_avatar/python/my_avatar"
MY_AVATAR_ENV = Path.home() / "dev/mike/my_avatar/.env"

TEXTS = {
    "short": "I'm glad you told me that.",
    "medium": (
        "That sounds like a meaningful moment. I can see why you remember it so clearly. "
        "What part surprised you most? I'd love to hear what happened next."
    ),
    "long": (
        "It sounds like you put a great deal of care into this decision. "
        "Sometimes the planning matters because it helps us feel ready for something important. "
        "Then real life arrives with details we could never have predicted. "
        "There can be joy in those surprises, even when they make us change our plans. "
        "I wonder which parts of your experience have felt most different from what you imagined. "
        "Perhaps the small everyday moments stand out more than the big milestones. "
        "Maybe you discovered strengths you did not know you had. "
        "Whatever the answer, I would be interested to hear your story. "
        "Take your time, and start with the first memory that comes to mind."
    ),
}


# ---------------------------------------------------------------------------
# Text chunking — same algorithm as my_avatar/benchmark.py, imported
# read-only from the standalone implementation so chunk boundaries match.
# ---------------------------------------------------------------------------


def chunks_for(text: str) -> list[str]:
    sys.path.insert(0, str(MY_AVATAR_PY))
    from speech_chunks import AdaptiveChunker, SpeechPlanner  # noqa: E402

    chunker, planner = AdaptiveChunker(), SpeechPlanner()
    chunker.feed(text, 0.0)
    chunker.finish()
    now, parts = 0.0, []
    while chunk := chunker.take(now, planner.budget(now, chunker.count), urgent=True):
        duration = len(chunk.text) * 0.058
        now += duration * planner.rate
        planner.audio_ready(len(parts), now, duration)
        parts.append(chunk.text)
    return parts


# ---------------------------------------------------------------------------
# Worker process (JSON lines in → JSON lines out), same framing as
# my_avatar's app.Worker but self-contained.
# ---------------------------------------------------------------------------


class Worker:
    def __init__(self, name: str, command: list[str]) -> None:
        self.name = name
        self.command = command
        self.process: asyncio.subprocess.Process | None = None
        self.messages: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self.ready = asyncio.Event()
        self.ready_info: dict | None = None

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            limit=16 * 1024 * 1024,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        reader = asyncio.create_task(self._read())
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=180)
        finally:
            if not self.ready.is_set():
                reader.cancel()
        if self.process.returncode is not None:
            raise RuntimeError(f"{self.name} exited before becoming ready")

    async def _read(self) -> None:
        assert self.process and self.process.stdout
        while line := await self.process.stdout.readline():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("type") == "ready":
                self.ready_info = message
                self.ready.set()
                continue
            try:
                self.messages.put_nowait(message)
            except asyncio.QueueFull:
                pass  # drop stale frames rather than block the worker

    async def send(self, message: dict) -> None:
        assert self.process and self.process.stdin
        self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        await self.process.stdin.drain()

    async def next_message(self, timeout: float = 120.0) -> dict:
        return await asyncio.wait_for(self.messages.get(), timeout)

    async def stop(self) -> None:
        if self.process and self.process.returncode is None:
            try:
                await self.send({"type": "stop"})
                await asyncio.wait_for(self.process.wait(), 3)
            except (asyncio.TimeoutError, RuntimeError, ConnectionResetError):
                self.process.terminate()
                await self.process.wait()


# ---------------------------------------------------------------------------
# Measurement model
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    case: str
    chunks: int = 0
    first_pcm_ms: float | None = None          # request → first TTS PCM (whole case)
    first_frame_after_first_pcm_ms: float | None = None
    tts_rtf: list[float] = field(default_factory=list)      # per chunk
    inter_chunk_gap_ms: list[float] = field(default_factory=list)  # speech_end(n) → speech_end(n+1) minus audio duration n
    avtr_fps: list[float] = field(default_factory=list)     # from worker fps messages
    av_drift_ms: list[float] = field(default_factory=list)  # per segment
    interrupt_latency_ms: float | None = None
    errors: list[str] = field(default_factory=list)


async def run_case(name: str, tts: Worker, avatar: Worker, interrupt_at: float | None) -> CaseResult:
    result = CaseResult(case=name)
    text_chunks = chunks_for(TEXTS[name])
    result.chunks = len(text_chunks)

    segment_audio: dict[str, tuple[bytes, int]] = {}
    segment_pts: dict[str, list[float]] = {}
    segment_speech_end: dict[str, float | None] = {}
    case_started = time.monotonic()
    first_pcm = None
    first_frame = None
    last_speech_end: tuple[str, float, float] | None = None  # id, at, audio_duration
    interrupted_sent: float | None = None
    interrupt_done = False

    reader_stop = asyncio.Event()

    async def avatar_reader() -> None:
        nonlocal first_frame, interrupt_done
        while not reader_stop.is_set():
            try:
                message = await asyncio.wait_for(avatar.messages.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            kind = message.get("type")
            if kind == "error":
                result.errors.append(str(message.get("error")))
            elif kind == "fps":
                result.avtr_fps.append(float(message["value"]))
            elif kind == "frame" and message.get("kind") == "speech":
                segment_id = message.get("id")
                if segment_id:
                    segment_pts.setdefault(segment_id, []).append(float(message.get("pts", 0)))
                if first_frame is None:
                    first_frame = time.monotonic()
            elif kind == "speech_end":
                segment_speech_end[message.get("id")] = time.monotonic()
            elif kind == "interrupted":
                if interrupted_sent is not None and not interrupt_done:
                    result.interrupt_latency_ms = (time.monotonic() - interrupted_sent) * 1000
                    interrupt_done = True

    reader = asyncio.create_task(avatar_reader())
    try:
        for number, phrase in enumerate(text_chunks, 1):
            segment_id = f"{name}-{number}"

            # --- TTS -------------------------------------------------------
            await tts.send({
                "type": "speak", "id": segment_id, "text": phrase, "voice": tts.voice,
                "mode": tts.mode,
            })
            while True:
                message = await tts.next_message()
                if message.get("type") == "error":
                    result.errors.append(str(message.get("error")))
                    break
                if message.get("type") == "audio" and message.get("id") == segment_id:
                    break
            if message.get("type") != "audio":
                raise RuntimeError(f"TTS failed for {segment_id}")
            pcm_at = time.monotonic()
            if first_pcm is None:
                first_pcm = pcm_at
                result.first_pcm_ms = (first_pcm - case_started) * 1000
            audio = base64.b64decode(message["audio"])
            sr = int(message["sr"])
            duration = len(audio) / (4 * sr)
            if message.get("generation"):
                result.tts_rtf.append(float(message["generation"]) / duration)
            segment_audio[segment_id] = (audio, sr)

            # --- AVTR ------------------------------------------------------
            # The AVTR worker consumes 16 kHz mono float32; resample from
            # Qwen's native rate exactly as my_avatar's pipeline does.
            from scipy.signal import resample_poly  # noqa: E402  (local dep)

            import numpy as np  # noqa: E402

            pcm = np.frombuffer(audio, dtype="<f4")
            from math import gcd  # noqa: E402

            g = gcd(sr, 16000)
            mono16 = resample_poly(pcm, 16000 // g, sr // g).astype("<f4").tobytes()
            await avatar.send({
                "type": "speak", "id": segment_id,
                "audio": base64.b64encode(memoryview(mono16)).decode("ascii"),
            })

            if last_speech_end is not None:
                prev_id, prev_at, prev_duration = last_speech_end
                if prev_id in segment_speech_end and segment_speech_end[prev_id] is not None:
                    gap = (pcm_at - segment_speech_end[prev_id]) - prev_duration
                    result.inter_chunk_gap_ms.append(gap * 1000)
            last_speech_end = (segment_id, pcm_at, duration)

            # Wait for this segment's frames to start before the next chunk,
            # mirroring the real pipeline's pull-based scheduling. The reader
            # task owns the queue; we only wait on its observations.
            wait_deadline = time.monotonic() + 60
            while segment_id not in segment_pts and time.monotonic() < wait_deadline:
                await asyncio.sleep(0.02)

            if interrupt_at is not None and first_frame is not None and result.interrupt_latency_ms is None:
                elapsed = time.monotonic() - first_frame
                if elapsed >= interrupt_at:
                    interrupted_sent = time.monotonic()
                    await avatar.send({"type": "interrupt"})

        # Drain remaining speech ends.
        deadline = time.monotonic() + 30
        pending = set(segment_audio)
        while pending and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            pending = {s for s in pending if segment_speech_end.get(s) is None}

        if first_pcm is not None and first_frame is not None:
            result.first_frame_after_first_pcm_ms = (first_frame - first_pcm) * 1000
    finally:
        reader_stop.set()
        reader.cancel()

    # A/V pacing drift per segment: wall-clock span of speech frames vs the
    # pts span the frames claim to cover.
    for segment_id, pts in segment_pts.items():
        if len(pts) >= 2:
            pts_span = pts[-1] - pts[0]
            result.av_drift_ms.append(pts_span * 1000)  # pts span ≈ audio seconds covered
    return result


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


def load_standalone_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if MY_AVATAR_ENV.is_file():
        for line in MY_AVATAR_ENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


async def start_standalone(avtr_path: str, qwen_path: str, portrait: str,
                           voice_model: str, voice: str) -> tuple[Worker, Worker]:
    tts = Worker("TTS", [
        os.path.join(qwen_path, ".venv/bin/python"),
        str(MY_AVATAR_PY / "tts_worker.py"),
        os.path.join(qwen_path, "models", voice_model),
        voice,
    ])
    avatar = Worker("AVTR", [
        os.path.join(avtr_path, ".pixi/envs/default/bin/python"),
        str(MY_AVATAR_PY / "avatar_worker.py"),
        portrait,
    ])
    await tts.start()
    await avatar.start()
    tts.voice = voice
    tts.mode = "design" if "VoiceDesign" in voice_model else "custom"
    return tts, avatar


def report(results: list[CaseResult], target: str) -> str:
    lines = [f"# Avatar pipeline benchmark — target: {target}", ""]
    for r in results:
        lines.append(f"## case {r.case} ({r.chunks} chunks)")
        if r.errors:
            lines.append(f"- ERRORS: {r.errors}")
        def fmt(v: float | None) -> str:
            return f"{v:.1f} ms" if v is not None else "n/a"
        lines.append(f"- first TTS PCM: {fmt(r.first_pcm_ms)}")
        lines.append(f"- first AVTR frame after first PCM: {fmt(r.first_frame_after_first_pcm_ms)}")
        if r.tts_rtf:
            lines.append("- TTS RTF per chunk: " + ", ".join(f"{v:.2f}" for v in r.tts_rtf))
        if r.inter_chunk_gap_ms:
            lines.append("- inter-chunk gaps (ms): " + ", ".join(f"{v:.0f}" for v in r.inter_chunk_gap_ms))
        if r.avtr_fps:
            lines.append(f"- AVTR FPS (worker-reported): min {min(r.avtr_fps):.1f}, "
                         f"median {sorted(r.avtr_fps)[len(r.avtr_fps)//2]:.1f}, max {max(r.avtr_fps):.1f}")
        if r.av_drift_ms:
            lines.append("- segment pts spans (ms): " + ", ".join(f"{v:.0f}" for v in r.av_drift_ms))
        lines.append(f"- interruption latency: {fmt(r.interrupt_latency_ms)}")
        lines.append("")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    env = load_standalone_env()
    avtr_path = args.avtr_path or env.get("AVTR_PATH", "/home/mike/dev/tools/avtr-1")
    qwen_path = args.qwen_path or env.get("QWEN_PATH", "")
    portrait = args.portrait or env.get("AVATAR_IMAGE", "")
    voice_model = args.voice_model or env.get("VOICE_MODEL", "")
    voice = args.voice or env.get("VOICE", "")

    if args.tts_cmd and args.avtr_cmd:
        tts = Worker("TTS", args.tts_cmd)
        avatar = Worker("AVTR", args.avtr_cmd)
        await tts.start()
        await avatar.start()
        tts.voice = args.voice or "voice"
        tts.mode = args.tts_mode or "design"
        target = "integrated (worker contract)"
    else:
        if not (qwen_path and portrait and voice_model and voice):
            print("Standalone target requires my_avatar/.env with QWEN_PATH, "
                  "AVATAR_IMAGE, VOICE_MODEL, VOICE — or pass --tts-cmd/--avtr-cmd.",
                  file=sys.stderr)
            return 2
        tts, avatar = await start_standalone(avtr_path, qwen_path, portrait, voice_model, voice)
        target = "standalone my_avatar workers (read-only)"

    results: list[CaseResult] = []
    try:
        for case in args.cases:
            interrupt_at = 1.0 if not args.no_interrupt else None
            print(f"running case {case!r} …", file=sys.stderr)
            results.append(await run_case(case, tts, avatar, interrupt_at))
            # Sequential only — never run concurrent GPU benchmarks.
            await asyncio.sleep(args.cooldown_seconds)
    finally:
        await tts.stop()
        await avatar.stop()

    print(report(results, target))
    if args.json_out:
        payload = [r.__dict__ | {"errors": r.errors} for r in results]
        Path(args.json_out).write_text(json.dumps(payload, indent=2, default=str))
    return 1 if any(r.errors for r in results) else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", nargs="+", default=["short"], choices=list(TEXTS))
    parser.add_argument("--tts-cmd", nargs="+", help="integrated TTS worker command")
    parser.add_argument("--tts-mode", default=None, help="design|custom for --tts-cmd")
    parser.add_argument("--avtr-cmd", nargs="+", help="integrated AVTR worker command")
    parser.add_argument("--avtr-path", default=None)
    parser.add_argument("--qwen-path", default=None)
    parser.add_argument("--portrait", default=None)
    parser.add_argument("--voice-model", default=None)
    parser.add_argument("--voice", default=None)
    parser.add_argument("--no-interrupt", action="store_true")
    parser.add_argument("--cooldown-seconds", type=float, default=2.0,
                        help="gap between cases; cases always run sequentially")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()