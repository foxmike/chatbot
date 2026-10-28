"""Worker lifecycle against a real stub child process (no GPU, no Qwen/AVTR)."""
import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from chatbot.avatar.config import MediaConfig
from chatbot.avatar.workers import Worker, WorkerError, build_workers

STUB = Path(__file__).with_name("stub_worker.py")


def make(mode="good", **kw):
    return Worker("STUB", sys.executable, STUB, mode, start_timeout=10, **kw)


async def message(worker):
    """Next worker message, skipping the `ready` notice the pump ignores."""
    while (m := await asyncio.wait_for(worker.messages.get(), 2))["type"] == "ready":
        pass
    return m


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return not Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0] == "Z"


async def test_start_send_receive_stop_releases_the_child():
    worker = make()
    await worker.start()
    pid = worker.process.pid
    assert worker.alive and worker.ready_info["pid"] == pid       # non-JSON noise was skipped
    await worker.send({"type": "speak", "id": "1:1"})
    assert await message(worker) == {"type": "echo", "id": "1:1"}
    await worker.stop()
    assert not worker.alive and worker.process.returncode is not None and not alive(pid)
    assert worker.reader_task is None
    with pytest.raises(WorkerError):
        await worker.send({"type": "speak"})


async def test_stop_escalates_to_sigterm_and_sigkill(monkeypatch):
    import chatbot.avatar.workers as module
    worker = make("ignore_term")
    await worker.start()
    pid = worker.process.pid
    real_wait_for = asyncio.wait_for

    async def impatient(awaitable, timeout):
        return await real_wait_for(awaitable, 0.3 if timeout else timeout)
    monkeypatch.setattr(module.asyncio, "wait_for", impatient)
    await worker.stop()                                            # ignores stop AND SIGTERM
    assert not alive(pid) and worker.process.returncode == -signal.SIGKILL


async def test_stop_after_unrequested_exit_is_safe_and_idempotent():
    worker = make()
    await worker.start()
    await worker.send({"type": "crash"})
    assert await message(worker) == {"type": "error", "error": "STUB exited"} and not worker.alive
    await worker.stop()
    await worker.stop()


async def test_failure_before_ready_raises_and_leaves_nothing_running():
    worker = make("crash_before_ready")
    with pytest.raises(WorkerError, match="exited before becoming ready"):
        await worker.start()
    assert worker.process.returncode is not None and worker.reader_task is None


async def test_start_timeout_kills_the_child():
    worker = Worker("SLOW", sys.executable, "-c", "import time; time.sleep(30)", start_timeout=0.3)
    with pytest.raises(asyncio.TimeoutError):
        await worker.start()
    assert worker.process.returncode is not None


async def test_worker_can_be_restarted_after_it_dies():
    worker = make()
    await worker.start()
    first = worker.process.pid
    await worker.send({"type": "crash"})
    assert (await message(worker))["type"] == "error"
    await worker.start()
    assert worker.alive and worker.process.pid != first
    await worker.send({"type": "ping"})
    assert (await message(worker))["type"] == "echo"
    await worker.stop()


async def test_avtr_frames_are_dropped_when_the_queue_is_full_but_first_speech_frame_is_kept():
    worker = make("frames", queue_size=4, drop_frames_when_full=True)
    await worker.start()
    await worker.send({"type": "burst", "n": 30})
    await asyncio.sleep(0.5)
    kept = []
    while not worker.messages.empty():
        kept.append(worker.messages.get_nowait())
    assert worker.dropped_frames > 0
    assert any(m.get("kind") == "speech" and m.get("first") for m in kept)
    await worker.stop()


def test_workers_are_built_with_the_known_good_environments_and_voice():
    config = MediaConfig()
    avatar, tts = build_workers(config)
    assert str(avatar.python) == "/home/mike/dev/tools/avtr-1/.pixi/envs/default/bin/python"
    assert str(tts.python) == "/home/mike/dev/tools/qwen/.venv/bin/python"
    assert Path(avatar.script).name == "avatar_worker.py" and Path(tts.script).name == "tts_worker.py"
    assert Path(avatar.script).is_file() and Path(tts.script).is_file()
    # tts_worker.py argv: checkpoint, voice description
    assert tts.args == (Path("/home/mike/dev/tools/qwen/models/Qwen3-TTS-12Hz-1.7B-VoiceDesign"),
                        "A warm, natural young adult American woman with a relaxed, expressive "
                        "conversational voice.")
    assert avatar.args == ()   # portraits arrive as commands
