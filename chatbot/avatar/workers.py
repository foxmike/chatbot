"""Persistent child-process workers (Qwen3-TTS, AVTR-1) speaking JSON lines over stdio.

Each worker runs in its own, pre-existing Python environment and is never installed into
chatbot's. The same `Worker` class drives both; only the queue sizing and AVTR's frame-drop
policy differ.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from chatbot.avatar.metrics import log


class WorkerError(RuntimeError):
    pass


class Worker:
    def __init__(
        self,
        name: str,
        python: str | Path,
        script: str | Path,
        *args: Any,
        queue_size: int = 4,
        drop_frames_when_full: bool = False,
        start_timeout: float = 120.0,
    ):
        self.name, self.python, self.script, self.args = name, python, script, args
        self.process: asyncio.subprocess.Process | None = None
        self.messages: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self.ready = asyncio.Event()
        self.ready_message = False
        self.ready_info: dict | None = None
        self.stopping = False
        self.reader_task: asyncio.Task | None = None
        self.dropped_frames = 0
        self.drop_frames_when_full = drop_frames_when_full
        self.start_timeout = start_timeout

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None and self.ready_message

    async def start(self) -> None:
        self.stopping = False
        self.ready = asyncio.Event()
        self.ready_message = False
        while not self.messages.empty():  # keep the queue object: engine pumps hold on to it
            self.messages.get_nowait()
        self.process = await asyncio.create_subprocess_exec(
            str(self.python), str(self.script), *map(str, self.args),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=None, limit=16 * 1024 * 1024, start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
        self.reader_task = asyncio.create_task(self.read())
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=self.start_timeout)
            if not self.ready_message:
                raise WorkerError(f"{self.name} exited before becoming ready")
        except BaseException:
            await self.stop()
            raise
        log(self.name, "Ready")

    async def read(self) -> None:
        process = self.process
        while line := await process.stdout.readline():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                log(self.name, f"Worker output: {line.decode(errors='replace').strip()}")
                continue
            if message.get("type") == "ready":
                self.ready_message = True
                self.ready_info = message
                self.ready.set()
            if (self.drop_frames_when_full and message.get("type") == "frame" and self.messages.full()
                    and not (message.get("kind") == "speech" and message.get("first"))):
                self.dropped_frames += 1
                if self.dropped_frames % 25 == 1:
                    log(self.name, f"Frame queue full; dropped {self.dropped_frames} frames")
                continue
            await self.messages.put(message)
        if not self.ready.is_set():
            self.ready.set()
        if not self.stopping:
            await self.messages.put({"type": "error", "error": f"{self.name} exited"})

    async def send(self, message: dict) -> None:
        if not self.process or self.process.returncode is not None:
            raise WorkerError(f"{self.name} is not running")
        self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        await self.process.stdin.drain()

    def _signal(self, sig: int) -> None:
        """Signal the worker's whole process group (it is its own session leader)."""
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.process.pid, sig)

    async def stop(self) -> None:
        """Ask the worker to exit, escalating to SIGTERM then SIGKILL, and reap it."""
        process, self.stopping = self.process, True
        try:
            if process and process.returncode is None:
                with contextlib.suppress(WorkerError, ConnectionError, RuntimeError, OSError):
                    await self.send({"type": "stop"})
                with contextlib.suppress(Exception):
                    process.stdin.close()
                for sig, patience in ((None, 3), (signal.SIGTERM, 3), (signal.SIGKILL, None)):
                    if sig is not None:
                        self._signal(sig)
                    try:
                        await asyncio.wait_for(process.wait(), patience)
                        break
                    except asyncio.TimeoutError:
                        continue
        finally:
            task, self.reader_task = self.reader_task, None
            if task and not task.done():
                task.cancel()
            if task:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            if process and process.returncode is None:
                self._signal(signal.SIGKILL)
                await process.wait()
            self.ready_message = False


def build_workers(config) -> tuple[Worker, Worker]:
    """(AVTR, TTS) workers for `config`. Portraits are selected later, via `portrait` commands."""
    avatar = Worker("AVTR", config.avtr_python, config.avatar_script, queue_size=64,
                    drop_frames_when_full=True, start_timeout=config.worker_start_timeout)
    tts = Worker("TTS", config.qwen_python, config.tts_script, config.qwen_checkpoint, config.voice,
                 queue_size=4, start_timeout=config.worker_start_timeout)
    return avatar, tts


__all__: Sequence[str] = ("Worker", "WorkerError", "build_workers")
