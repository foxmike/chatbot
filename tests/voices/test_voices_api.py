"""Voice Library API + service on an isolated SQLite database (never the live database)."""
import io
import uuid
import wave
from types import SimpleNamespace

import numpy as np
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

def _import_with_real_models():
    """Bind the modules under test to the real ORM models.

    tests/test_visual.py swaps ``chatbot.db.models`` in sys.modules for a MagicMock at
    collection time and leaves it there. Import everything these tests exercise while the
    real module is in place, then restore whatever was there, so neither suite affects the other.
    """
    import importlib
    import sys
    from unittest.mock import NonCallableMock

    current = sys.modules.get("chatbot.db.models")
    if not isinstance(current, NonCallableMock):
        return
    package = importlib.import_module("chatbot.db")
    real = package.__dict__.get("models")
    if real is None or isinstance(real, NonCallableMock):
        del sys.modules["chatbot.db.models"]
        real = importlib.import_module("chatbot.db.models")
    sys.modules["chatbot.db.models"] = real
    try:
        for name in ("chatbot.voices.service", "chatbot.voices.resolver", "chatbot.voices.init_default",
                     "chatbot.companions.companion", "chatbot.api.voices"):
            module = sys.modules.get(name)
            # Already imported while the fake was active? Re-bind it (dependency order).
            if module is not None:
                importlib.reload(module)
            else:
                importlib.import_module(name)
    finally:
        sys.modules["chatbot.db.models"] = current


_import_with_real_models()

from chatbot.api import voices as voices_api  # noqa: E402
from chatbot.api.admin import require_admin
from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.db import get_db
import chatbot.db as _db_package  # noqa: E402

_models = _db_package.models          # the real module (see _import_with_real_models)
Base, Companion, Session, User, Voice = (_models.Base, _models.Companion, _models.Session, _models.User,
                                         _models.Voice)
from chatbot.voices.audio import read_wav, voice_fingerprint
from chatbot.voices.resolver import DatabaseVoiceResolver
from chatbot.voices.service import DEFAULT_VOICE_ID, VoiceService, VoiceUnavailable
from chatbot.voices.storage import VoiceStorage

A = CurrentUser(user_id=str(uuid.uuid4()), username="alice", email="alice@example.com")
B = CurrentUser(user_id=str(uuid.uuid4()), username="bob", email="bob@example.com")
SR = 24000


def tone(seconds, rate=SR):
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * 200 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t))).astype(np.float32)


def upload_wav(seconds=5.0, rate=16000):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((tone(seconds, rate) * 32767).astype("<i2").tobytes())
    return out.getvalue()


class FakeTranscriber:
    async def transcribe(self, audio):
        assert audio.dtype == np.float32
        return "suggested transcript words"


class FakeEngine:
    """The media engine's Voice Library surface; records every synthesis request."""

    def __init__(self):
        self.designs, self.speaks, self.forgotten = [], [], []
        self.unloads = 0
        self.design_seconds = 4.0
        self.busy = False
        self.fail = None
        self.transcriber = FakeTranscriber()

    def _check(self):
        from chatbot.avatar.media import EngineBusyError, MediaError

        if self.busy:
            raise EngineBusyError("The avatar is speaking; try again when it has finished")
        if self.fail:
            raise MediaError(self.fail)

    async def design_preview(self, text, instruct, language="English"):
        self._check()
        self.designs.append({"text": text, "instruct": instruct, "language": language})
        # Every candidate is different (VoiceDesign samples).
        audio = tone(self.design_seconds) * (1.0 - 0.1 * len(self.designs))
        return {"audio": audio, "sr": SR, "duration": self.design_seconds, "model": "design"}

    async def voice_preview(self, text, voice):
        self._check()
        self.speaks.append({"text": text, "voice": dict(voice)})
        return {"audio": tone(1.5), "sr": SR, "duration": 1.5, "model": "base", "prompt": "built"}

    async def forget_voice(self, voice_id):
        self.forgotten.append(voice_id)

    async def unload_design(self):
        self.unloads += 1
        return True


class World:
    """App + database + storage; `restart()` rebuilds everything except the persisted state."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.db_url = f"sqlite+aiosqlite:///{tmp_path / 'voices.db'}"
        self.storage_root = tmp_path / "voice-library"
        self.user = A
        self.engine = FakeEngine()
        self._build()

    def _build(self):
        self.sql = create_async_engine(self.db_url)

        @sa.event.listens_for(self.sql.sync_engine, "connect")
        def _fk(dbapi, _):
            dbapi.execute("PRAGMA foreign_keys=ON")

        self.sessionmaker = async_sessionmaker(self.sql, expire_on_commit=False)
        self.storage = VoiceStorage(self.storage_root)
        app = FastAPI()
        app.include_router(voices_api.voices_router)
        app.state.avatar_runtime_host = SimpleNamespace(engine=self.engine)

        async def db():
            async with self.sessionmaker() as session:
                yield session

        app.dependency_overrides[get_db] = db
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[require_admin] = lambda: self.user
        app.dependency_overrides[voices_api.get_storage] = lambda: self.storage
        self.app = app
        self.client = TestClient(app)

    def restart(self):
        """A backend restart: new engine, new app, new connections; same database and files."""
        self.client.close()
        self.engine = FakeEngine()
        self._build()

    def as_user(self, user):
        self.user = user
        return self

    async def setup(self):
        tables = [Base.metadata.tables[t] for t in ("users", "appearances", "companions", "rooms",
                                                     "sessions", "voices")]
        async with self.sql.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
        self.companion = {}
        self.session = {}
        async with self.sessionmaker() as db:
            for user in (A, B):
                db.add(User(id=uuid.UUID(user.user_id), external_id=user.username, display_name=user.username,
                            username=user.username, email=user.email, password_hash="x"))
            await db.flush()
            for user, names in ((A, ("Ava", "Mia")), (B, ("Bea",))):
                for name in names:
                    companion = Companion(companion_id=str(uuid.uuid4()), user_id=uuid.UUID(user.user_id),
                                          name=name)
                    db.add(companion)
                    self.companion[name] = companion.companion_id
            await db.flush()
            for name, user in (("Ava", A), ("Mia", A), ("Bea", B)):
                session = Session(id=uuid.uuid4(), user_id=uuid.UUID(user.user_id),
                                  companion_id=self.companion[name], title=f"chat with {name}")
                db.add(session)
                self.session[name] = str(session.id)
            await db.commit()

    async def sql_one(self, statement):
        async with self.sessionmaker() as db:
            return (await db.execute(statement)).scalar_one_or_none()

    async def set_companion_voice(self, name, voice_id):
        async with self.sessionmaker() as db:
            await db.execute(sa.update(Companion).where(Companion.companion_id == self.companion[name])
                             .values(voice_id=voice_id))
            await db.commit()

    # API shortcuts --------------------------------------------------------
    def design(self, description="A warm, low voice.", text="Hello there, this is my voice.", **extra):
        response = self.client.post("/voices/previews/design",
                                    json={"description": description, "preview_text": text, **extra})
        assert response.status_code == 201, response.text
        return response.json()

    def upload(self, data=None, name="me.wav", consent="true", **form):
        return self.client.post("/voices/references",
                                files={"file": (name, data if data is not None else upload_wav(), "audio/wav")},
                                data={"consent": consent, **form})

    def save(self, preview_id, name="My Voice", **extra):
        return self.client.post("/voices", json={"preview_id": preview_id, "name": name, **extra})

    def designed_voice(self, name="Designed"):
        preview = self.design()
        response = self.save(preview["preview_id"], name)
        assert response.status_code == 201, response.text
        return response.json()

    def cloned_voice(self, name="Cloned", transcript="These are my exact words."):
        preview = self.upload().json()
        response = self.save(preview["preview_id"], name, transcript=transcript, consent=True)
        assert response.status_code == 201, response.text
        return response.json()

    def audio(self, url):
        response = self.client.get(url)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "audio/wav"
        return response.content


@pytest.fixture
async def world(tmp_path):
    w = World(tmp_path)
    await w.setup()
    yield w
    w.client.close()
    await w.sql.dispose()


async def add_builtin_default(world):
    wav = __import__("chatbot.voices.audio", fromlist=["encode_wav"]).encode_wav(tone(5), SR)
    stored = world.storage.store_reference(DEFAULT_VOICE_ID, wav)
    async with world.sessionmaker() as db:
        db.add(Voice(voice_id=DEFAULT_VOICE_ID, user_id=None, name="Default (built-in)", method="designed",
                     description="desc", reference_path=stored.reference_path, reference_sha256=stored.sha256,
                     reference_duration=stored.duration, reference_sample_rate=SR,
                     reference_transcript="Default words.", language="English", model_family="qwen3-tts-12hz",
                     creation_model="VoiceDesign", synthesis_model="Base", status="ready", is_builtin=True))
        await db.commit()


# ======================================================================= voice design


async def test_design_preview_generates_audio_and_is_not_saved(world):
    preview = world.design(description="A bright, cheerful voice.", text="Hi! Lovely to meet you.")
    assert preview["kind"] == "design" and preview["transcript"] == "Hi! Lovely to meet you."
    assert world.engine.designs == [{"text": "Hi! Lovely to meet you.", "instruct": "A bright, cheerful voice.",
                                     "language": "English"}]
    samples, rate = read_wav(world.audio(f"/voices/previews/{preview['preview_id']}/audio/reference"))
    assert rate == SR and len(samples) == 4 * SR
    assert [v for v in world.client.get("/voices").json()["voices"] if not v["is_builtin"]] == []


async def test_multiple_candidates_remain_independent(world):
    first = world.design(text="Candidate one.")
    second = world.design(description="A softer voice.", text="Candidate two.")
    assert first["preview_id"] != second["preview_id"]
    a = world.audio(f"/voices/previews/{first['preview_id']}/audio/reference")
    b = world.audio(f"/voices/previews/{second['preview_id']}/audio/reference")
    assert a != b
    assert world.client.get(f"/voices/previews/{first['preview_id']}").json()["transcript"] == "Candidate one."


async def test_a_rejected_candidate_is_discarded_and_never_saved(world):
    preview = world.design()
    assert world.client.delete(f"/voices/previews/{preview['preview_id']}").status_code == 204
    assert world.client.get(f"/voices/previews/{preview['preview_id']}").status_code == 404
    assert not (world.storage_root / "previews" / A.user_id / preview["preview_id"]).exists()
    assert world.save(preview["preview_id"]).status_code == 404
    assert await world.sql_one(sa.select(sa.func.count()).select_from(Voice)) == 0
    assert world.engine.unloads == 1        # VoiceDesign released after the candidate is dropped


async def test_the_approved_candidate_is_saved_exactly_without_regeneration(world):
    world.design(text="Rejected candidate.")
    chosen = world.design(text="This is the one I like.")
    heard = world.audio(f"/voices/previews/{chosen['preview_id']}/audio/reference")
    response = world.save(chosen["preview_id"], "  Evening   Voice ")
    assert response.status_code == 201, response.text
    voice = response.json()
    assert len(world.engine.designs) == 2           # nothing was generated when saving
    assert voice["name"] == "Evening Voice" and voice["method"] == "designed"
    assert voice["description"] == "A warm, low voice."
    assert voice["reference_transcript"] == "This is the one I like."
    assert voice["synthesis_model"] == "Qwen3-TTS-12Hz-1.7B-Base"
    assert voice["creation_model"] == "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
    assert world.audio(f"/voices/{voice['voice_id']}/audio") == heard     # byte-identical
    assert world.client.get(f"/voices/previews/{chosen['preview_id']}").status_code == 404
    assert "reference_path" not in voice and str(world.storage_root) not in response.text


async def test_saved_voices_survive_a_restart_with_their_name(world):
    designed = world.designed_voice("Persistent Designed")
    cloned = world.cloned_voice("Persistent Clone", transcript="Words I really said.")
    designed_audio = world.audio(f"/voices/{designed['voice_id']}/audio")
    world.restart()
    listed = {v["voice_id"]: v for v in world.client.get("/voices").json()["voices"]}
    assert listed[designed["voice_id"]]["name"] == "Persistent Designed"
    assert listed[cloned["voice_id"]]["reference_transcript"] == "Words I really said."
    assert world.audio(f"/voices/{designed['voice_id']}/audio") == designed_audio
    world.client.post(f"/voices/{designed['voice_id']}/speak", json={"text": "Still me."})
    assert world.engine.speaks[0]["voice"]["reference"] == f"library/{designed['voice_id']}/reference.wav"


async def test_a_too_short_designed_reference_cannot_be_saved(world):
    world.engine.design_seconds = 1.0
    preview = world.design(text="Hi.")
    assert preview["warnings"] and "only 1.0s" in preview["warnings"][0]
    assert world.save(preview["preview_id"]).status_code == 400


async def test_design_input_validation_and_synthesis_errors(world):
    bad = world.client.post("/voices/previews/design",
                            json={"description": "A voice.", "preview_text": "Hi.", "language": "Klingon"})
    assert bad.status_code == 400 and "Unsupported language" in bad.json()["detail"]
    assert world.client.post("/voices/previews/design", json={"description": "", "preview_text": "x"}
                             ).status_code == 422
    world.engine.busy = True
    busy = world.client.post("/voices/previews/design", json={"description": "A voice.", "preview_text": "Hi."})
    assert busy.status_code == 409 and "speaking" in busy.json()["detail"]
    world.engine.busy, world.engine.fail = False, "Qwen3-TTS VoiceDesign model failed to load: OOM"
    failed = world.client.post("/voices/previews/design", json={"description": "A voice.", "preview_text": "Hi."})
    assert failed.status_code == 502 and "VoiceDesign model failed to load" in failed.json()["detail"]


# ======================================================================= voice cloning


async def test_a_valid_upload_is_accepted_normalized_and_transcribed_for_review(world):
    response = world.upload(upload_wav(6.0, 44100))
    assert response.status_code == 201, response.text
    preview = response.json()
    assert preview["kind"] == "clone" and preview["consent"] is True
    assert preview["suggested_transcript"] == "suggested transcript words"
    assert preview["transcript"] is None            # a suggestion is not assumed correct
    assert preview["source_sample_rate"] == 44100
    samples, rate = read_wav(world.audio(f"/voices/previews/{preview['preview_id']}/audio/reference"))
    assert rate == SR and abs(len(samples) / SR - 6.0) < 0.05


@pytest.mark.parametrize("data, name, status_code", [
    (b"not audio" * 50, "me.wav", 422),
    (upload_wav(1.0), "me.wav", 422),
    (upload_wav(5.0), "me.txt", 422),
    (upload_wav(5.0, 8000)[:44] + b"\x00" * 80000, "me.wav", 422),   # silent
])
async def test_invalid_audio_is_rejected(world, data, name, status_code):
    response = world.upload(data, name=name)
    assert response.status_code == status_code
    assert not list((world.storage_root / "previews").glob("*/*")) if (world.storage_root / "previews").exists() else True


async def test_uploads_require_consent_auth_and_size_limits(world, monkeypatch):
    assert world.upload(consent="false").status_code == 400
    settings = voices_api.get_settings()
    monkeypatch.setattr(settings, "voice_upload_max_bytes", 1000)
    assert world.upload(upload_wav(5.0)).status_code == 413
    world.app.dependency_overrides.pop(get_current_user)
    assert world.upload().status_code == 401
    assert world.client.get("/voices").status_code == 401


async def test_clone_preview_uses_the_uploaded_reference_and_reviewed_transcript(world):
    preview = world.upload().json()
    response = world.client.post(f"/voices/previews/{preview['preview_id']}/sample", json={
        "transcript": "My corrected reference words.", "preview_text": "How does this sound?",
        "language": "English"})
    assert response.status_code == 200, response.text
    request = world.engine.speaks[-1]
    assert request["text"] == "How does this sound?"
    voice = request["voice"]
    assert voice["reference"] == f"previews/{A.user_id}/{preview['preview_id']}/reference.wav"
    assert voice["transcript"] == "My corrected reference words."
    record = world.storage.get_preview(A.user_id, preview["preview_id"])
    assert voice["fingerprint"] == voice_fingerprint(record["reference_sha256"], "My corrected reference words.")
    assert response.json()["has_sample"] is True
    world.audio(f"/voices/previews/{preview['preview_id']}/audio/sample")


async def test_a_saved_clone_keeps_its_source_audio_and_transcript(world):
    preview = world.upload().json()
    reference = world.audio(f"/voices/previews/{preview['preview_id']}/audio/reference")
    world.client.post(f"/voices/previews/{preview['preview_id']}/sample", json={
        "transcript": "Exact spoken words.", "preview_text": "Testing.", "language": "English"})
    voice = world.save(preview["preview_id"], "Me", consent=True).json()
    assert voice["method"] == "cloned" and voice["reference_transcript"] == "Exact spoken words."
    assert voice["creation_model"] == "uploaded-recording" and voice["metadata"]["consent_confirmed"] is True
    assert world.audio(f"/voices/{voice['voice_id']}/audio") == reference


async def test_a_clone_cannot_be_saved_without_a_transcript(world):
    preview = world.upload().json()
    response = world.save(preview["preview_id"], "Me", consent=True)
    assert response.status_code == 400 and "transcript" in response.json()["detail"].lower()


async def test_other_users_cannot_see_use_or_save_someone_elses_preview(world):
    preview = world.upload().json()
    world.as_user(B)
    pid = preview["preview_id"]
    assert world.client.get(f"/voices/previews/{pid}").status_code == 404
    assert world.client.get(f"/voices/previews/{pid}/audio/reference").status_code == 404
    assert world.client.post(f"/voices/previews/{pid}/sample", json={
        "transcript": "x", "preview_text": "y"}).status_code == 404
    assert world.save(pid, "Stolen", transcript="x", consent=True).status_code == 404
    world.client.delete(f"/voices/previews/{pid}")
    world.as_user(A)
    assert world.client.get(f"/voices/previews/{pid}").status_code == 200   # B's delete did nothing


# ======================================================================= library


async def test_list_rename_edit_preview_and_delete(world):
    await add_builtin_default(world)
    voice = world.designed_voice("First")
    listed = world.client.get("/voices").json()
    assert [v["name"] for v in listed["voices"]] == ["Default (built-in)", "First"]
    assert listed["default_initialized"] is True and "English" in listed["languages"]
    renamed = world.client.patch(f"/voices/{voice['voice_id']}", json={"name": "Renamed"}).json()
    assert renamed["name"] == "Renamed" and world.engine.forgotten == []    # a rename keeps the prompt
    edited = world.client.patch(f"/voices/{voice['voice_id']}",
                                json={"transcript": "Corrected words.", "language": "French"}).json()
    assert edited["reference_transcript"] == "Corrected words." and edited["language"] == "French"
    assert world.engine.forgotten == [voice["voice_id"]]                    # transcript re-keys the voice
    spoken = world.client.post(f"/voices/{voice['voice_id']}/speak", json={"text": "Bonjour."})
    assert spoken.status_code == 200 and spoken.headers["content-type"] == "audio/wav"
    assert world.engine.speaks[-1]["voice"]["transcript"] == "Corrected words."
    reference_dir = world.storage_root / "library" / voice["voice_id"]
    assert reference_dir.is_dir()
    deleted = world.client.delete(f"/voices/{voice['voice_id']}")
    assert deleted.status_code == 200 and not reference_dir.exists()            # file cleanup
    assert world.client.get(f"/voices/{voice['voice_id']}").status_code == 404
    assert world.engine.forgotten[-1] == voice["voice_id"]


async def test_users_can_only_touch_their_own_voices_and_builtins_are_read_only(world):
    await add_builtin_default(world)
    voice = world.designed_voice("Alice's")
    world.as_user(B)
    assert [v["voice_id"] for v in world.client.get("/voices").json()["voices"]] == [DEFAULT_VOICE_ID]
    for call in (lambda: world.client.get(f"/voices/{voice['voice_id']}"),
                 lambda: world.client.get(f"/voices/{voice['voice_id']}/audio"),
                 lambda: world.client.post(f"/voices/{voice['voice_id']}/speak", json={"text": "hi"}),
                 lambda: world.client.patch(f"/voices/{voice['voice_id']}", json={"name": "Mine"}),
                 lambda: world.client.delete(f"/voices/{voice['voice_id']}")):
        assert call().status_code == 404
    assert world.client.get(f"/voices/{DEFAULT_VOICE_ID}/audio").status_code == 200
    assert world.client.patch(f"/voices/{DEFAULT_VOICE_ID}", json={"name": "Hijack"}).status_code == 403
    assert world.client.delete(f"/voices/{DEFAULT_VOICE_ID}").status_code == 403
    world.as_user(A)
    assert world.client.get(f"/voices/{voice['voice_id']}").json()["name"] == "Alice's"


async def test_a_voice_assigned_to_a_companion_cannot_be_deleted_until_reassigned(world):
    voice = world.designed_voice("Assigned")
    spare = world.designed_voice("Spare")
    await world.set_companion_voice("Ava", voice["voice_id"])
    blocked = world.client.delete(f"/voices/{voice['voice_id']}")
    assert blocked.status_code == 409
    body = blocked.json()
    assert body["companions"] == [{"companion_id": world.companion["Ava"], "name": "Ava"}]
    assert (world.storage_root / "library" / voice["voice_id"]).is_dir()          # nothing removed
    replaced = world.client.delete(f"/voices/{voice['voice_id']}",
                                   params={"replacement_voice_id": spare["voice_id"]})
    assert replaced.status_code == 200 and replaced.json()["reassigned_companions"] == 1
    assert await world.sql_one(sa.select(Companion.voice_id).where(
        Companion.companion_id == world.companion["Ava"])) == spare["voice_id"]


async def test_a_conversation_override_blocks_deletion_unless_explicitly_cleared(world):
    voice = world.designed_voice("Override")
    world.client.put(f"/voices/sessions/{world.session['Ava']}", json={"voice_id": voice["voice_id"]})
    blocked = world.client.delete(f"/voices/{voice['voice_id']}")
    assert blocked.status_code == 409 and blocked.json()["sessions"][0]["session_id"] == world.session["Ava"]
    cleared = world.client.delete(f"/voices/{voice['voice_id']}", params={"clear_session_overrides": "true"})
    assert cleared.status_code == 200
    selection = world.client.get(f"/voices/sessions/{world.session['Ava']}").json()
    assert selection["override_voice_id"] is None


async def test_the_database_itself_refuses_dangling_voice_references(world):
    voice = world.designed_voice("Guarded")
    await world.set_companion_voice("Ava", voice["voice_id"])
    async with world.sessionmaker() as db:
        with pytest.raises(sa.exc.IntegrityError):
            await db.execute(sa.delete(Voice).where(Voice.voice_id == voice["voice_id"]))
            await db.commit()


async def test_a_replacement_must_be_usable_and_not_the_same_voice(world):
    voice = world.designed_voice("A")
    await world.set_companion_voice("Ava", voice["voice_id"])
    assert world.client.delete(f"/voices/{voice['voice_id']}",
                               params={"replacement_voice_id": voice["voice_id"]}).status_code == 400
    world.as_user(B)
    bobs = world.designed_voice("Bob's")
    world.as_user(A)
    assert world.client.delete(f"/voices/{voice['voice_id']}",
                               params={"replacement_voice_id": bobs["voice_id"]}).status_code == 404


# ======================================================================= conversation voice


async def test_session_override_takes_precedence_over_the_companion_default(world):
    await add_builtin_default(world)
    companion_voice = world.designed_voice("Companion Voice")
    override = world.cloned_voice("Override Voice")
    sid = world.session["Ava"]
    selection = world.client.get(f"/voices/sessions/{sid}").json()
    assert (selection["effective_voice_id"], selection["effective_source"]) == (DEFAULT_VOICE_ID, "default")
    await world.set_companion_voice("Ava", companion_voice["voice_id"])
    selection = world.client.get(f"/voices/sessions/{sid}").json()
    assert (selection["effective_voice_id"], selection["effective_source"]) == (
        companion_voice["voice_id"], "companion")
    selection = world.client.put(f"/voices/sessions/{sid}", json={"voice_id": override["voice_id"]}).json()
    assert (selection["effective_voice_id"], selection["effective_source"]) == (override["voice_id"], "session")
    assert selection["companion_voice_id"] == companion_voice["voice_id"]
    selection = world.client.put(f"/voices/sessions/{sid}", json={"voice_id": None}).json()
    assert selection["effective_voice_id"] == companion_voice["voice_id"]


async def test_each_conversation_uses_its_own_companions_voice(world):
    ava_voice, mia_voice = world.designed_voice("Ava's"), world.designed_voice("Mia's")
    await world.set_companion_voice("Ava", ava_voice["voice_id"])
    await world.set_companion_voice("Mia", mia_voice["voice_id"])
    resolver = DatabaseVoiceResolver(world.sessionmaker, world.storage, design_model="D", clone_model="B")
    ava = await resolver.resolve(SimpleNamespace(user_id=A.user_id, session_id=world.session["Ava"]))
    mia = await resolver.resolve(SimpleNamespace(user_id=A.user_id, session_id=world.session["Mia"]))
    assert ava["voice_id"] == ava_voice["voice_id"] and mia["voice_id"] == mia_voice["voice_id"]
    assert ava["source"] == mia["source"] == "companion"


async def test_invalid_foreign_and_deleted_voices_cannot_be_selected(world):
    sid = world.session["Ava"]
    assert world.client.put(f"/voices/sessions/{sid}", json={"voice_id": "nope"}).status_code == 404
    assert world.client.put(f"/voices/sessions/{sid}", json={"voice_id": str(uuid.uuid4())}).status_code == 404
    world.as_user(B)
    bobs = world.designed_voice("Bob's")
    world.as_user(A)
    assert world.client.put(f"/voices/sessions/{sid}", json={"voice_id": bobs["voice_id"]}).status_code == 404
    gone = world.designed_voice("Gone")
    world.client.delete(f"/voices/{gone['voice_id']}")
    assert world.client.put(f"/voices/sessions/{sid}", json={"voice_id": gone["voice_id"]}).status_code == 404
    # Another user's conversation is not reachable at all.
    assert world.client.get(f"/voices/sessions/{world.session['Bea']}").status_code == 404


async def test_resolution_gives_the_exact_reference_and_never_substitutes(world):
    voice = world.cloned_voice("Mine", transcript="Reference words here.")
    sid = world.session["Ava"]
    resolver = DatabaseVoiceResolver(world.sessionmaker, world.storage, design_model="D", clone_model="B")
    binding = SimpleNamespace(user_id=A.user_id, session_id=sid)
    with pytest.raises(VoiceUnavailable, match="No voice is available"):
        await resolver.resolve(binding)                 # nothing selected and no default: an error
    world.client.put(f"/voices/sessions/{sid}", json={"voice_id": voice["voice_id"]})
    spec = await resolver.resolve(binding)
    assert spec["voice_id"] == voice["voice_id"] and spec["transcript"] == "Reference words here."
    assert spec["reference"] == f"library/{voice['voice_id']}/reference.wav"
    assert spec["fingerprint"] == voice_fingerprint(voice["reference_sha256"], "Reference words here.")
    (world.storage_root / spec["reference"]).unlink()   # the reference recording disappears
    await add_builtin_default(world)
    with pytest.raises(VoiceUnavailable, match="has no reference recording"):
        await resolver.resolve(binding)                 # not silently replaced by the default
    selection = world.client.get(f"/voices/sessions/{sid}").json()
    assert selection["effective_voice_id"] is None and "no reference recording" in selection["error"]


async def test_companion_voice_assignment_is_validated_for_ownership(world):
    from chatbot.companions.companion import CompanionManager, CompanionVoiceError

    await add_builtin_default(world)
    mine = world.designed_voice("Mine")
    world.as_user(B)
    bobs = world.designed_voice("Bob's")
    async with world.sessionmaker() as db:
        manager = CompanionManager(db)
        assert await manager._validated_voice(A.user_id, mine["voice_id"]) == mine["voice_id"]
        assert await manager._validated_voice(A.user_id, DEFAULT_VOICE_ID) == DEFAULT_VOICE_ID
        assert await manager._validated_voice(A.user_id, None) is None
        for bad in (bobs["voice_id"], str(uuid.uuid4()), "x"):
            with pytest.raises(CompanionVoiceError):
                await manager._validated_voice(A.user_id, bad)


# ======================================================================= default voice


async def test_explicit_default_voice_initialization(world):
    response = world.client.post("/voices/default/initialize")
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["voice_id"] == DEFAULT_VOICE_ID and result["replaced"] is False
    design = world.engine.designs[0]
    assert design["instruct"] == ("A warm, natural young adult American woman with a relaxed, expressive "
                                  "conversational voice.")
    voice = world.client.get(f"/voices/{DEFAULT_VOICE_ID}").json()
    assert voice["is_builtin"] and voice["method"] == "designed"
    assert voice["reference_transcript"] == design["text"]
    assert "not bit-identical" in voice["metadata"]["note"]
    assert world.client.post("/voices/default/initialize").status_code == 409   # explicit replace only
    replaced = world.client.post("/voices/default/initialize", params={"replace": "true"}).json()
    assert replaced["replaced"] is True and replaced["sha256"] != result["sha256"]
    assert DEFAULT_VOICE_ID in world.engine.forgotten and world.engine.unloads >= 2
    world.as_user(B)
    assert world.client.get("/voices").json()["default_initialized"] is True
