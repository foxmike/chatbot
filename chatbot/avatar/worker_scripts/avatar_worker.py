"""Persistent AVTR-1 renderer process. JSON lines on stdin/stdout are private IPC.

Runs in the AVTR-1 pixi environment, never in chatbot's. The portrait is optional on the
command line and can be (re)selected at any time with a `portrait` command: AvatarLoader
registers a new Avatar from an image with its ONNX models only, so the TensorRT engines are
never rebuilt or reloaded. Loaded avatars are cached by `portrait_id`.
"""
import base64
import contextlib
import json
import os
import queue
import sys
import tempfile
import threading
import time

import cv2
import numpy as np


# During speech, render up to this far ahead of real time. The next chunk's first
# frames then exist before the previous chunk's audio ends, so the browser never has
# to wait for the avatar at a chunk boundary. Frames are still timestamped and
# presented against the browser's audio clock.
RENDER_LEAD = int(os.getenv("AVTR_RENDER_LEAD_MS", "500")) / 1000
MAX_CACHED_AVATARS = 4   # each Avatar holds ~25 MB of CUDA tensors
MIN_PORTRAIT_SIDE = 64


def emit(**message):
    print(json.dumps(message, separators=(",", ":")), flush=True)


def reader(commands, wake, interrupt):
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if command.get("type") in ("speak", "interrupt", "portrait", "pause_render", "resume_render"):
                wake.set()  # end an idle pacing sleep so new audio is picked up at once
            if command.get("type") in ("interrupt", "pause_render"):
                interrupt.set()
            if command.get("type") == "listen":
                try:
                    commands.put_nowait(command)
                except queue.Full:
                    pass  # Never let stale microphone buffers delay an interrupt.
            else:
                commands.put(command)
        except json.JSONDecodeError:
            pass
    # The parent closed our stdin (it exited or stopped us): never outlive it.
    commands.put({"type": "stop"})
    wake.set()
    interrupt.set()


def decode(message):
    return np.frombuffer(base64.b64decode(message["audio"]), dtype="<f4")


def prepare_portrait(source, directory, name, *, width=1280, height=720):
    """Re-encode `source` as a PNG named `name` in `directory`; returns its path.

    Fit the whole source into the loader's output frame with one uniform scale and
    centered padding. AvatarLoader.load otherwise stretches it to out_w/out_h before
    detecting the face, so CSS object-contain alone cannot correct the distortion.
    RGB padding matches the popup stage; RGBA padding stays transparent.

    AVTR's loader reads any cv2 format, but looks for a `<stem>.pbmask.png` beside the file.
    A private copy keeps the default pasteback mask, exactly as the original single-portrait
    worker behaved. Raises ValueError for anything that is not a usable image.
    """
    image = cv2.imread(source, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"Cannot read portrait: {source}")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise ValueError(f"Unsupported portrait channels: {image.shape}")
    if min(image.shape[:2]) < MIN_PORTRAIT_SIDE:
        raise ValueError(f"Portrait too small: {image.shape[1]}x{image.shape[0]}")
    source_h, source_w = image.shape[:2]
    if (source_w, source_h) != (width, height):
        scale = min(width / source_w, height / source_h)
        fitted_w = max(1, min(width, round(source_w * scale)))
        fitted_h = max(1, min(height, round(source_h * scale)))
        if (fitted_w, fitted_h) != (source_w, source_h):
            image = cv2.resize(
                image, (fitted_w, fitted_h),
                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
            )
        frame = np.zeros((height, width, image.shape[2]), dtype=image.dtype)
        if image.shape[2] == 3:
            frame[:] = (45, 35, 30)  # BGR: popup background #1e232d
        left, top = (width - fitted_w) // 2, (height - fitted_h) // 2
        frame[top:top + fitted_h, left:left + fitted_w] = image
        image = frame
    path = f"{directory}/{name}.png"
    if not cv2.imwrite(path, image):
        raise ValueError(f"Cannot convert portrait: {source}")
    return path


def capture_avatar_loader(from_artifacts, **kwargs):
    """Call Pipeline.from_artifacts and also return the AvatarLoader it builds internally.

    from_artifacts constructs its loader locally and only returns preloaded avatars, so a
    second loader would duplicate five ONNX sessions on the GPU. The loader is imported inside
    from_artifacts, so a recording subclass installed on the module is picked up.
    """
    from avtr1_renderer import avatar_loader

    original, captured = avatar_loader.AvatarLoader, []

    class Recording(original):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            captured.append(self)

    avatar_loader.AvatarLoader = Recording
    try:
        result = from_artifacts(**kwargs)
    finally:
        avatar_loader.AvatarLoader = original
    if not captured:
        raise RuntimeError("AVTR AvatarLoader was not constructed; cannot switch portraits")
    return result, captured[0]


def main():
    with contextlib.redirect_stdout(sys.stderr):
        from avtr1_renderer.pipeline import Pipeline
        from avtr1_renderer.types import Chunk, RenderOptions

        # Build the engines once with no preloaded avatar (an empty portraits directory also
        # keeps from_artifacts from touching the stock reference frames); portraits arrive
        # later as `portrait` commands.
        portrait_dir = tempfile.TemporaryDirectory(prefix="chatbot-avatar-")
        (pipeline, _), loader = capture_avatar_loader(
            Pipeline.from_artifacts, avatar_ids=[], portraits_dir=portrait_dir.name)
    mg = pipeline._motion_generator
    window = (mg.chunk_size + mg.future_size) * mg.frame_len + mg.audio_shift
    step = mg.chunk_size * mg.frame_len
    avatars = {}       # portrait_id -> Avatar, least recently used first
    avatar = None
    commands = queue.Queue(maxsize=128)
    wake, interrupt = threading.Event(), threading.Event()
    threading.Thread(target=reader, args=(commands, wake, interrupt), daemon=True).start()
    listen = np.zeros(window, np.float32)
    last_listen = 0.0
    speech = queue.Queue(maxsize=6)
    active = None
    offset = 0
    state = None
    picture = 0
    options = RenderOptions(pixel_format="yuv_i420", bg_id="plain_white", stream_frames=True)
    frame_count = 0
    last_frame = time.monotonic()
    compute_total = 0.0
    steps = 0
    run_start = None  # wall-clock origin of the current continuous speech run
    run_frames = 0

    def load_portrait(command):
        """Select (and if needed register) the avatar for a `portrait` command."""
        nonlocal avatar, active, offset, state, picture
        reply = {"type": "portrait_loaded", "request_id": command.get("request_id"),
                 "portrait_id": command.get("portrait_id")}
        started = time.monotonic()
        try:
            portrait_id = str(command["portrait_id"])
            if portrait_id in avatars:
                chosen = avatars.pop(portrait_id)
                reply["cached"] = True
            else:
                picture += 1
                with contextlib.redirect_stdout(sys.stderr):
                    png = prepare_portrait(
                        command["path"], portrait_dir.name, f"portrait-{picture}",
                        width=loader.out_w, height=loader.out_h,
                    )
                    try:
                        chosen = loader.load(png, avatar_id=portrait_id)
                    finally:
                        os.unlink(png)
                reply["cached"] = False
            avatars[portrait_id] = chosen
            while len(avatars) > MAX_CACHED_AVATARS:
                avatars.pop(next(iter(avatars)))
        except Exception as exc:  # an invalid portrait must leave the current avatar untouched
            emit(type="portrait_error", request_id=command.get("request_id"),
                 portrait_id=command.get("portrait_id"), error=str(exc))
            return
        avatar = chosen
        while not speech.empty():
            speech.get_nowait()
        active, offset, state = None, 0, None   # autoregressive state belongs to one avatar
        emit(**reply, load_ms=1000 * (time.monotonic() - started))

    emit(type="ready", window=window, step=step)
    rendering = False
    if len(sys.argv) > 1 and sys.argv[1]:
        load_portrait({"portrait_id": "default", "path": sys.argv[1], "request_id": None})
    while True:
        wake.clear()
        interrupt.clear()
        while True:
            try:
                command = commands.get_nowait()
            except queue.Empty:
                break
            kind = command.get("type")
            if kind == "stop":
                return
            if kind == "pause_render":
                rendering = False
                emit(type="render_paused", at=time.monotonic())
                continue
            if kind == "resume_render":
                rendering = True
                emit(type="render_resumed", at=time.monotonic())
                continue
            if kind == "listen":
                chunk = decode(command)
                listen = np.concatenate((listen, chunk))[-window:]
                last_listen = time.monotonic()
            elif kind == "portrait":
                load_portrait(command)
            elif kind == "speak":
                samples = decode(command)
                if avatar is None:
                    emit(type="error", error="AVTR has no portrait", id=command["id"])
                    continue
                if speech.full():
                    emit(type="error", error="AVTR speech queue full", id=command["id"])
                    continue
                speech.put_nowait((command["id"], samples))
                emit(type="audio_received", id=command["id"], duration=len(samples) / 16000,
                     at=time.monotonic())
            elif kind == "interrupt":
                while not speech.empty():
                    speech.get_nowait()
                active = None
                offset = 0
                state = None
                emit(type="interrupted", at=time.monotonic())
        if not rendering:
            wake.wait()
            continue
        if avatar is None:
            wake.wait(0.1)  # nothing to render until a portrait is selected
            continue
        if active is None and not speech.empty():
            active = speech.get_nowait()
            offset = 0
        kind = "speech" if active else "listening"
        if not active:
            run_start = None
        elif run_start is None:
            run_start, run_frames = time.monotonic(), 0
        if active:
            segment_id, samples = active
            piece = samples[offset:offset + window]
            sp = np.pad(piece, (0, max(0, window - len(piece)))).astype(np.float32)
            ls = np.zeros(window, np.float32)
        else:
            segment_id = None
            sp = np.zeros(window, np.float32)
            ls = listen.copy() if time.monotonic() - last_listen < 0.5 else np.zeros(window, np.float32)
        started = time.monotonic()
        state, frames = pipeline.process_chunk(avatar, Chunk(sp, ls), state, options)
        compute_total += time.monotonic() - started
        steps += 1
        for frame_index, frame in enumerate(frames):
            bgr = cv2.cvtColor(frame.data, cv2.COLOR_YUV2BGR_I420)
            bgr = cv2.resize(bgr, (640, 360), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if ok:
                emit(type="frame", kind=kind, id=segment_id, first=frame_index == 0,
                     pts=offset / 16000 + frame_index / 25 if active else 0,
                     generated_at=time.monotonic(),
                     jpeg=base64.b64encode(encoded).decode("ascii"))
            frame_count += 1
            now = time.monotonic()
            if frame_count % 25 == 0:
                emit(type="fps", value=25 / (now - last_frame), step_ms=1000 * compute_total / max(1, steps))
                last_frame = now
                compute_total, steps = 0.0, 0
            # Keep output paced to its 25 fps media clock (speech may lead by RENDER_LEAD).
            if run_start is not None:
                run_frames += 1
                target = run_start + run_frames / 25 - RENDER_LEAD
            else:
                target = started + (frame_index + 1) / 25
            if target > now:
                # Idle frames end early for new audio; speech frames only for an interrupt.
                (interrupt if active else wake).wait(target - now)
                if interrupt.is_set() or (not active and wake.is_set()):
                    break
        if active:
            offset += step
            if offset >= len(samples):
                emit(type="speech_end", id=segment_id)
                active = None


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit(type="error", error=str(exc))
        raise
