"""On-disk Voice Library storage: candidate previews and saved reference recordings.

Layout under the voice library root (never served directly; the API streams files after
authorization):

    library/<voice_id>/reference.wav          canonical reference of a saved voice
    previews/<user_id>/<preview_id>/
        reference.wav                          designed candidate, or the normalized upload
        sample.wav                             clone preview speech (clone candidates only)
        meta.json                              kind, transcript, description, language, ...

Every path component is a canonical UUID derived server-side; clients never supply paths.
Previews are temporary: they expire, are deleted on cancel, and are moved (bytes unchanged)
into `library/` when approved. A rejected preview never becomes a library entry.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from chatbot.voices.audio import sha256, wav_duration

REFERENCE_NAME = "reference.wav"
SAMPLE_NAME = "sample.wav"
META_NAME = "meta.json"


class PreviewNotFound(LookupError):
    pass


class StorageError(RuntimeError):
    pass


def canonical_id(value: str) -> str:
    """A UUID string in canonical form, or ValueError: nothing else may become a path."""
    return str(uuid.UUID(str(value)))


@dataclass(frozen=True)
class StoredReference:
    reference_path: str      # relative to the library root
    sha256: str
    duration: float
    sample_rate: int
    size: int


class VoiceStorage:
    def __init__(self, root: Path | str, preview_ttl_seconds: int = 3600, clock=time.time):
        self.root = Path(root).resolve()
        self.preview_ttl_seconds = preview_ttl_seconds
        self._clock = clock

    # ------------------------------------------------------------------ paths

    def _inside(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise StorageError("path outside the voice library")
        return resolved

    def _preview_dir(self, user_id: str, preview_id: str) -> Path:
        return self._inside(self.root / "previews" / canonical_id(user_id) / canonical_id(preview_id))

    def _voice_dir(self, voice_id: str) -> Path:
        return self._inside(self.root / "library" / canonical_id(voice_id))

    def relative(self, path: Path) -> str:
        return self._inside(path).relative_to(self.root).as_posix()

    def absolute(self, relative_path: str) -> Path:
        """A stored reference path (from the database) back to a file inside the root."""
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise StorageError("invalid reference path")
        return self._inside(self.root / candidate)

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        partial.write_bytes(data)
        os.replace(partial, path)

    # ------------------------------------------------------------------ previews

    def create_preview(self, user_id: str, kind: str, reference_wav: bytes, meta: dict) -> dict:
        preview_id = str(uuid.uuid4())
        directory = self._preview_dir(user_id, preview_id)
        directory.mkdir(parents=True, exist_ok=False)
        self._write_atomic(directory / REFERENCE_NAME, reference_wav)
        record = {**meta, "preview_id": preview_id, "user_id": canonical_id(user_id), "kind": kind,
                  "created_at": self._clock(), "reference_sha256": sha256(reference_wav),
                  "reference_duration": wav_duration(reference_wav), "has_sample": False}
        self._write_atomic(directory / META_NAME, json.dumps(record).encode())
        return record

    def get_preview(self, user_id: str, preview_id: str) -> dict:
        try:
            directory = self._preview_dir(user_id, preview_id)
        except ValueError as exc:
            raise PreviewNotFound(preview_id) from exc
        meta_path = directory / META_NAME
        if not meta_path.is_file():
            raise PreviewNotFound(preview_id)
        record = json.loads(meta_path.read_text())
        if self._clock() - float(record.get("created_at", 0)) > self.preview_ttl_seconds:
            shutil.rmtree(directory, ignore_errors=True)
            raise PreviewNotFound(preview_id)
        return record

    def update_preview(self, user_id: str, preview_id: str, **changes) -> dict:
        record = {**self.get_preview(user_id, preview_id), **changes}
        self._write_atomic(self._preview_dir(user_id, preview_id) / META_NAME, json.dumps(record).encode())
        return record

    def set_sample(self, user_id: str, preview_id: str, sample_wav: bytes, **changes) -> dict:
        self.get_preview(user_id, preview_id)
        self._write_atomic(self._preview_dir(user_id, preview_id) / SAMPLE_NAME, sample_wav)
        return self.update_preview(user_id, preview_id, has_sample=True, **changes)

    def preview_file(self, user_id: str, preview_id: str, which: str) -> Path:
        record = self.get_preview(user_id, preview_id)
        name = {"reference": REFERENCE_NAME, "sample": SAMPLE_NAME}.get(which)
        if name is None or (which == "sample" and not record.get("has_sample")):
            raise PreviewNotFound(f"{preview_id}/{which}")
        path = self._preview_dir(user_id, preview_id) / name
        if not path.is_file():
            raise PreviewNotFound(f"{preview_id}/{which}")
        return path

    def preview_reference_relative(self, user_id: str, preview_id: str) -> str:
        return self.relative(self.preview_file(user_id, preview_id, "reference"))

    def delete_preview(self, user_id: str, preview_id: str) -> bool:
        try:
            directory = self._preview_dir(user_id, preview_id)
        except ValueError:
            return False
        existed = directory.is_dir()
        shutil.rmtree(directory, ignore_errors=True)
        return existed

    def purge_expired_previews(self) -> int:
        removed = 0
        base = self.root / "previews"
        if not base.is_dir():
            return 0
        now = self._clock()
        for meta_path in base.glob(f"*/*/{META_NAME}"):
            try:
                created = float(json.loads(meta_path.read_text()).get("created_at", 0))
            except (OSError, ValueError):
                created = 0
            if now - created > self.preview_ttl_seconds:
                shutil.rmtree(meta_path.parent, ignore_errors=True)
                removed += 1
        for directory in base.glob("*/*"):   # interrupted writes leave directories without meta
            if directory.is_dir() and not (directory / META_NAME).exists():
                shutil.rmtree(directory, ignore_errors=True)
        return removed

    # ------------------------------------------------------------------ saved voices

    def promote(self, user_id: str, preview_id: str, voice_id: str) -> StoredReference:
        """Move an approved preview's reference into the library, byte for byte."""
        record = self.get_preview(user_id, preview_id)
        source = self.preview_file(user_id, preview_id, "reference")
        data = source.read_bytes()
        digest = sha256(data)
        if digest != record["reference_sha256"]:
            raise StorageError("preview reference changed since it was generated")
        target = self._voice_dir(voice_id) / REFERENCE_NAME
        if target.exists():
            raise StorageError("voice reference already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        self.delete_preview(user_id, preview_id)
        from chatbot.voices.audio import read_wav

        _, rate = read_wav(target.read_bytes())
        return StoredReference(self.relative(target), digest, wav_duration(data), rate, len(data))

    def store_reference(self, voice_id: str, reference_wav: bytes, *, replace: bool = False) -> StoredReference:
        """Write a reference directly (initialization of built-in voices)."""
        target = self._voice_dir(voice_id) / REFERENCE_NAME
        if target.exists() and not replace:
            raise StorageError("voice reference already exists")
        self._write_atomic(target, reference_wav)
        from chatbot.voices.audio import read_wav

        _, rate = read_wav(reference_wav)
        return StoredReference(self.relative(target), sha256(reference_wav), wav_duration(reference_wav),
                               rate, len(reference_wav))

    def reference_file(self, relative_path: str) -> Path:
        path = self.absolute(relative_path)
        if not path.is_file():
            raise StorageError("voice reference recording is missing")
        return path

    def delete_voice(self, voice_id: str) -> bool:
        directory = self._voice_dir(voice_id)
        existed = directory.is_dir()
        shutil.rmtree(directory, ignore_errors=True)
        return existed
