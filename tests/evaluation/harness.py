"""Scenario execution through the real standard-chat pipeline, and result assembly.

What is real: ``POST /sessions`` and ``POST /chat`` handlers, the full prompt
compilation (identity, relationship, mood, behaviour, social, expression
layers), real SQLAlchemy sessions on a throw-away SQLite file, persisted
companion rows and messages. What is replaced: only the model call, through
``provider.complete`` -- by a deterministic scripted responder or a bounded real
provider. The evaluation database never is, and never touches, the application
database; artifacts are written only where the caller says.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, Mapping, Protocol, Sequence

TESTS_DIR = Path(__file__).resolve().parent.parent
for _path in (TESTS_DIR.parent, TESTS_DIR):  # repository root (chatbot package), then the shared test fixtures
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import pytest  # noqa: E402  (MonkeyPatch is the project's established isolation tool)
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from starlette.requests import Request  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from test_completion_persistence import _TABLES, USER, USER_ID, Env, app_with_engine  # noqa: E402

from chatbot.api import chat as chat_api  # noqa: E402
from chatbot.api import conversation, sessions as sessions_api, streaming  # noqa: E402
from chatbot.core import turn as turn_module  # noqa: E402
from chatbot.db import models  # noqa: E402
from chatbot.observability.metrics import MetricsCollector  # noqa: E402
from chatbot.observability.tracing import TraceCollector  # noqa: E402
from chatbot.soul.self_model import SCHEMA_VERSION, _subject_key, empty_self_model  # noqa: E402

from . import blind, metrics  # noqa: E402
from .scenarios import CompanionSpec, Condition, Scenario, SessionSpec, slug  # noqa: E402

RESULT_FORMAT = "behavioral-evaluation/1"
# Prompt layers Phases 4-6 added on top of identity/relationship/mood/memory.
AUGMENTATION_LAYERS = ("open_threads", "emotional_associations", "character_behavior",
                       "social_context", "expression", "performance_output")
BASELINE_NAME = "without_behavior_expression_layers"
MOOD_WORDS = {"warm": "warm", "upbeat": "upbeat", "tired": "tired", "weary": "tired", "reflective": "reflective",
              "scholarly": "reflective", "direct": "direct", "gentle": "gentle", "playful": "playful"}


class BudgetExceeded(RuntimeError):
    """A generation was requested beyond the authorised budget; nothing was sent."""


class MissingScript(LookupError):
    """The scripted responder has no reply for this cell."""


class UnansweredRequest(RuntimeError):
    """An earlier run sent this cell's request but obtained no reply (none, or an error); not re-sent automatically."""


class LedgerError(RuntimeError):
    """The generation ledger cannot be used as asked (exists without resume, or belongs to another run)."""


# --------------------------------------------------------------------------
# Responders (the only thing that replaces production behaviour)
# --------------------------------------------------------------------------


@dataclass
class GenerationRequest:
    scenario_id: str
    character_id: str
    condition_id: str
    session_id: str
    prompt: str
    history: list[Mapping[str, Any]]
    config: Any
    index: int
    cell: str = ""


@dataclass
class Generated:
    text: str
    finish_reason: str = "stop"
    model: str | None = None
    usage: Mapping[str, Any] | None = None


class Responder(Protocol):
    source: str
    model: str | None

    async def generate(self, request: GenerationRequest) -> Generated: ...


class ScriptedResponder:
    """Deterministic replies keyed ``scenario/character[/condition]``, or from a callable.

    ``from_exemplars`` uses each fixture's own exemplary response. That tests
    the harness and scoring; it says nothing about any model's personality.
    """

    source = "scripted"
    model = None

    def __init__(self, script: Mapping[str, str] | Callable[[GenerationRequest], str] | None = None) -> None:
        self._script = script or {}

    @classmethod
    def from_exemplars(cls, scenarios: Iterable[Scenario]) -> "ScriptedResponder":
        script: dict[str, str] = {}
        for s in scenarios:
            for cid, text in s.exemplars.items():
                for c in s.companions if cid == "*" else [c for c in s.companions if c.character_id == cid]:
                    script.setdefault(f"{s.scenario_id}/{c.character_id}", text)
        return cls(script)

    async def generate(self, request: GenerationRequest) -> Generated:
        if callable(self._script):
            return Generated(self._script(request))
        for key in (f"{request.scenario_id}/{request.character_id}/{request.condition_id}",
                    f"{request.scenario_id}/{request.character_id}"):
            if key in self._script:
                return Generated(self._script[key])
        raise MissingScript(f"{request.scenario_id}/{request.character_id}")


class ProviderResponder:
    """A real provider behind the existing registry, bounded by the run's budget."""

    source = "provider"

    def __init__(self, provider: Any, *, model: str | None = None) -> None:
        self._provider, self.model = provider, model

    async def generate(self, request: GenerationRequest) -> Generated:
        result = await self._provider.complete(prompt=request.prompt, history=request.history, config=request.config)
        raw = getattr(result, "raw", None) or {}
        usage = raw.get("usage") if isinstance(raw, Mapping) else None
        return Generated(result.text, getattr(result, "finish_reason", "stop") or "stop",
                         getattr(result, "model", None) or self.model, usage)


def provider_responder_from_settings(settings: Any | None = None) -> ProviderResponder | None:
    """The configured conversation provider, or ``None`` when it has no credentials/endpoint."""
    from chatbot.config import Settings
    from chatbot.providers.bootstrap import build_default_registry

    settings = settings or Settings()
    name = settings.llm_provider.lower()
    if name == "openrouter" and not settings.openrouter_api_key:
        return None
    provider = build_default_registry(settings).get(name)
    return ProviderResponder(provider, model=settings.default_model or None)


# --------------------------------------------------------------------------
# Configuration, budget and recording
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RunConfig:
    name: str = "improved"
    drop_layers: tuple[str, ...] = ()  # non-empty = an explicit baseline configuration
    cast: tuple[CompanionSpec, ...] | None = None  # same companions for every scenario
    shared_companions: bool = False  # one persisted companion per character across scenarios
    max_generations: int | None = None
    max_output_tokens: int | None = None
    include_prompts: bool = True
    seed: str = "phase7"
    scenario_ids: tuple[str, ...] | None = None
    retry_unanswered: bool = False  # re-send a request an interrupted run sent but never saw answered

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "dropped_prompt_layers": list(self.drop_layers),
                "cast": None if self.cast is None else [c.character_id for c in self.cast],
                "shared_companions": self.shared_companions, "max_generations": self.max_generations,
                "max_output_tokens": self.max_output_tokens, "seed": self.seed}


LEDGER_FORMAT = "generation-ledger/1"


class Ledger:
    """Append-only record of every generation request and its outcome, flushed to disk per line.

    The request line is written *before* the responder is called and the reply
    line the moment it returns, so a crash anywhere later in the pipeline or in
    measurement cannot lose a reply that has been paid for. A resumed run
    replays recorded replies instead of generating them again, and counts every
    earlier request (answered or not, plus any ``unrecorded_requests`` noted by
    hand) against ``max_generations``. A truncated final line from an
    interrupted write is ignored.
    """

    def __init__(self, path: str | Path, header: Mapping[str, Any], *, resume: bool = False) -> None:
        self.path = Path(path)
        self.responses: dict[str, dict[str, Any]] = {}
        self.unanswered: set[str] = set()
        self.prior_requests = 0
        entries = self._read()
        if entries and not resume:
            raise LedgerError(f"{self.path} already records generations; resume it or use a new output directory")
        runs = [e for e in entries if e.get("event") == "run"]
        if runs and runs[0].get("identity") != header.get("identity"):
            raise LedgerError(f"{self.path} belongs to a different run configuration/provider; refusing to mix replies")
        for entry in entries:
            event, cell = entry.get("event"), entry.get("cell")
            if event == "request":
                self.prior_requests += 1
                self.unanswered.add(cell)
            elif event == "response":
                self.responses[cell] = entry
                self.unanswered.discard(cell)
            elif event == "error":
                self.unanswered.add(cell)  # sent, no usable reply: re-sent only on explicit request
            elif event == "unrecorded_requests":
                self.prior_requests += int(entry.get("count") or 0)
        self._write({"event": "run" if not runs else "resume", **header})

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text().splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # an interrupted final write
        return entries

    def _write(self, entry: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        torn = self.path.exists() and self.path.stat().st_size and not self.path.read_bytes().endswith(b"\n")
        with self.path.open("a") as handle:
            handle.write(("\n" if torn else "") + json.dumps(entry, ensure_ascii=False) + "\n")  # never extend a torn line
            handle.flush()
            os.fsync(handle.fileno())

    def request(self, cell: str, **meta: Any) -> None:
        self._write({"event": "request", "cell": cell, **meta})

    def response(self, cell: str, **payload: Any) -> None:
        self.responses[cell] = {"event": "response", "cell": cell, **payload}
        self._write(self.responses[cell])

    def error(self, cell: str, message: str) -> None:
        self._write({"event": "error", "cell": cell, "error": message})


def ledger_header(config: RunConfig, responder: Responder) -> dict[str, Any]:
    identity = {"format": LEDGER_FORMAT, "config": config.describe(), "source": responder.source, "model": responder.model}
    return {"identity": identity, "identity_sha256_16": _sha(json.dumps(identity, sort_keys=True))}


class Recorder:
    """Stands in for the provider at the ``provider.complete`` seam and records every call."""

    def __init__(self, responder: Responder, config: RunConfig, ledger: Ledger | None = None) -> None:
        self.responder, self.config, self.ledger = responder, config, ledger
        self.calls: list[dict[str, Any]] = []
        self.generations = 0  # new generations in this run
        self.replayed = 0
        self.context: dict[str, str] = {}
        self.layers: list[dict[str, int]] = []

    @property
    def consumed(self) -> int:
        """Every generation counted against the budget: earlier runs' requests plus this run's."""
        return self.generations + (self.ledger.prior_requests if self.ledger else 0)

    def can_generate(self, cell: str) -> bool:
        if self.ledger and cell in self.ledger.responses:
            return True  # a replay costs nothing
        return self.config.max_generations is None or self.consumed < self.config.max_generations

    async def complete(self, *, prompt: str, history: Sequence[Mapping[str, Any]], config: Any) -> Any:
        cell = self.context.get("cell", "")
        cap = self.config.max_output_tokens
        if cap is not None:
            config = dataclasses.replace(config, max_tokens=min(config.max_tokens or cap, cap))
        call: dict[str, Any] = {"history": list(history), "prompt": prompt, "config_model": getattr(config, "model", None),
                                "max_tokens": getattr(config, "max_tokens", None), "cell": cell, "replayed": False,
                                "temperature": getattr(config, "temperature", None), "layers": self.layers[-1] if self.layers else {}}
        stored = self.ledger.responses.get(cell) if self.ledger else None
        if stored is not None:
            self.replayed += 1
            self.calls.append(call)
            call.update(replayed=True, text=stored["text"], finish_reason=stored.get("finish_reason"), usage=stored.get("usage") or {},
                        model=stored.get("model") or call["config_model"], seconds=stored.get("seconds"))
            return SimpleNamespace(text=stored["text"], finish_reason=call["finish_reason"], model=call["model"], raw=None)
        refusal: Exception | None = None
        if self.ledger and cell in self.ledger.unanswered and not self.config.retry_unanswered:
            refusal = UnansweredRequest(f"{cell}: an earlier run sent this request but recorded no reply")
        elif not self.can_generate(cell):
            refusal = BudgetExceeded(f"generation budget of {self.config.max_generations} reached")
        if refusal is not None:  # nothing is sent; recorded so the cell is reported, not mistaken for a reply
            call["error"] = f"{type(refusal).__name__}: {refusal}"
            self.calls.append(call)
            raise refusal
        self.generations += 1
        request = GenerationRequest(**{k: v for k, v in self.context.items() if k != "cell"}, prompt=prompt, history=list(history),
                                    config=config, index=self.consumed, cell=cell)
        self.calls.append(call)
        if self.ledger:
            self.ledger.request(cell, model=call["config_model"], max_tokens=call["max_tokens"], n=self.consumed)
        started = perf_counter()
        try:
            generated = await self.responder.generate(request)
        except BaseException as exc:
            call["error"] = f"{type(exc).__name__}: {exc}"
            call["seconds"] = round(perf_counter() - started, 4)
            if self.ledger and isinstance(exc, Exception):
                self.ledger.error(cell, call["error"])
            raise
        call.update(text=generated.text, finish_reason=generated.finish_reason, usage=dict(generated.usage or {}),
                    model=generated.model or call["config_model"], seconds=round(perf_counter() - started, 4))
        if self.ledger:
            self.ledger.response(cell, text=generated.text, finish_reason=generated.finish_reason, usage=call["usage"],
                                 model=call["model"], seconds=call["seconds"])
        return SimpleNamespace(text=generated.text, finish_reason=generated.finish_reason, model=call["model"], raw=None)


# --------------------------------------------------------------------------
# Isolated environment
# --------------------------------------------------------------------------


@asynccontextmanager
async def evaluation_env() -> AsyncIterator[Env]:
    """A throw-away SQLite database wired in place of every production session factory."""
    with tempfile.TemporaryDirectory(prefix="behavioral-eval-") as tmp, pytest.MonkeyPatch.context() as patch:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp) / 'eval.db'}")
        assert engine.url.get_backend_name() == "sqlite"  # never the application database

        @event.listens_for(engine.sync_engine, "connect")
        def _no_implicit_transaction_control(dbapi_connection: Any, _record: Any) -> None:
            dbapi_connection.isolation_level = None

        @event.listens_for(engine.sync_engine, "begin")
        def _explicit_begin(conn: Any) -> None:
            conn.exec_driver_sql("BEGIN")

        async with engine.begin() as connection:
            for model in _TABLES:
                await connection.run_sync(lambda sync, m=model: m.__table__.create(sync))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add(models.User(id=USER_ID, external_id="ext", display_name="U", username="u",
                               email="u@example.com", password_hash="h"))
            await db.commit()
        patch.setattr(streaming, "streaming_session_factory", factory)
        patch.setattr(turn_module, "trace_collector", TraceCollector(store="memory"))
        patch.setattr(turn_module, "metrics_collector", MetricsCollector(store="memory"))
        patch.setattr(turn_module, "structured_logger", None)
        patch.setattr(turn_module, "get_due_rituals", AsyncMock(return_value=[]))
        patch.setattr(turn_module, "generate_embedding", AsyncMock(return_value=[]))
        patch.setattr(conversation, "generate_recent_message_embeddings", AsyncMock())
        try:
            yield Env(engine, factory)
        finally:
            await engine.dispose()


@asynccontextmanager
async def isolated() -> AsyncIterator[tuple[Env, pytest.MonkeyPatch]]:
    """The evaluation database plus a patch scope for the provider seam."""
    with pytest.MonkeyPatch.context() as patch:
        async with evaluation_env() as env:
            yield env, patch


def _install_recorder(patch: pytest.MonkeyPatch, recorder: Recorder, config: RunConfig) -> None:
    patch.setattr(turn_module, "_select_provider", lambda ctx: recorder)
    real_compose = turn_module.compose_prompt_layers

    def compose(layers: Any, *args: Any, **kwargs: Any) -> str:
        if isinstance(layers, Mapping):
            layers = {name: ("" if str(getattr(name, "value", name)) in config.drop_layers else value)
                      for name, value in layers.items()}
            recorder.layers.append({str(getattr(n, "value", n)): len(str(v)) for n, v in layers.items() if v})
        return real_compose(layers, *args, **kwargs)

    if real_compose is not None:
        patch.setattr(turn_module, "compose_prompt_layers", compose)


# --------------------------------------------------------------------------
# Seeding (real rows; every fixture field is reported as applied or not)
# --------------------------------------------------------------------------


def _clamp(value: Any) -> float:
    return max(0.0, min(1.0, float(value)))


def _held_item(kind: str, subject: str, statement: str, reason: str = "") -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {"kind": kind, "subject": subject, "statement": statement, "reason": reason, "status": "held", "strength": 0.7,
            "updated_at": now.isoformat(), "first_at": (now - timedelta(days=7)).isoformat(), "challenge": None}


def build_self_model(spec: CompanionSpec) -> dict[str, Any]:
    doc = empty_self_model()
    doc["schema"] = SCHEMA_VERSION
    for subject, value in (spec.self_model.get("opinions") or {}).items():
        doc["items"][f"opinion:{_subject_key(subject)}"] = _held_item("opinion", subject, value["statement"], value.get("reason", ""))
    for subject in spec.self_model.get("interests") or []:
        doc["items"][f"interest:{_subject_key(subject)}"] = _held_item("interest", subject, f"I am interested in {subject}.")
    return doc


def _mood_label(mood: Mapping[str, Any]) -> str:
    text = " ".join(str(v) for k, v in mood.items() if isinstance(v, str)).casefold()
    return next((label for word, label in MOOD_WORDS.items() if word in text), "neutral")


def seed_report_for(scenario: Scenario, spec: CompanionSpec) -> dict[str, Any]:
    applied = ["name", "persona", "voice_and_tone(description)", "personality_anchors(sliders)"]
    if spec.fingerprint:
        applied.append("linguistic_fingerprint(description)")
    if spec.self_model:
        applied.append("developed_self_model(held items)")
    if scenario.mood:
        applied.append("initial_mood(session mood_state: energy, closeness, label)")
    if scenario.relationship or "communication_directness" in spec.anchors:
        applied.append("relationship(directness, warmth, depth where present)")
    not_applied = list(scenario.unmapped) + (["behavioral_disposition"] if spec.disposition else [])
    return {"applied": applied, "not_applied": sorted(set(not_applied)),
            "note": "emotional associations, memory evidence and user facts are not seeded; durable extraction is not run"}


async def seed_companion(env: Env, spec: CompanionSpec, scenario: Scenario, condition: Condition) -> str:
    cid = f"c{hashlib.sha1(f'{scenario.scenario_id}/{spec.character_id}/{condition.condition_id}'.encode()).hexdigest()[:34]}"
    sliders = {k: _clamp(v) for k, v in spec.anchors.items()}
    speech = "; ".join(f"{k.replace('_', ' ')}: {v.replace('_', ' ')}" for k, v in spec.fingerprint.items())
    description = ". ".join(p for p in (spec.archetype, f"Voice and tone: {spec.voice_and_tone}" if spec.voice_and_tone else "",
                                        f"Speech habits - {speech}" if speech else "") if p)
    relationship = {**scenario.relationship, **{k: v for k, v in condition.relationship.items() if v is not None}}
    fields: dict[str, Any] = {}
    if "communication_directness" in spec.anchors:
        fields["directness"] = _clamp(spec.anchors["communication_directness"])
    if isinstance(scenario.mood.get("warmth"), (int, float)):
        fields["warmth"] = _clamp(scenario.mood["warmth"])
    if isinstance(relationship.get("familiarity"), (int, float)):
        fields["depth"] = _clamp(relationship["familiarity"])
    async with env.factory() as db:
        db.add(models.Companion(companion_id=cid, user_id=USER_ID, name=spec.name, persona=spec.persona or None,
                                description=description or None, personality_sliders=sliders))
        await db.flush()
        db.add(models.CompanionIdentityModel(companion_id=cid, trait_adjustments={}, self_model=build_self_model(spec) if spec.self_model else {}))
        db.add(models.CompanionRelationshipModel(companion_id=cid, **fields))
        await db.commit()
    return cid


async def open_session(env: Env, companion_id: str, scenario: Scenario, seed_mood: bool = True) -> str:
    async with env.factory() as db:
        created = await sessions_api.create_session(sessions_api.SessionCreateRequest(companion_id=companion_id), USER, db)
    sid = created.session_id
    if seed_mood and scenario.mood:
        async with env.factory() as db:
            row = await db.get(models.Session, uuid.UUID(sid))
            row.mood_state = {
                "mood": _mood_label(scenario.mood),
                "energy": _clamp(scenario.mood.get("energy", 0.5)),
                "closeness": _clamp(scenario.mood.get("warmth", 0.5)),
                "last_updated": datetime.now(timezone.utc).isoformat(), "recent_emotional_context": [],
            }
            await db.commit()
    return sid


async def seed_turns(env: Env, session_id: str, companion_id: str, turns: Sequence[Any]) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with env.factory() as db:
        for index, turn in enumerate(turns):
            db.add(models.Message(session_id=uuid.UUID(session_id), user_id=USER_ID, companion_id=companion_id,
                                  role=turn.role, content=turn.content, turn_id=f"seed-{session_id[:8]}-{index // 2}",
                                  created_at=base + timedelta(seconds=index)))
        await db.commit()


async def chat_once(env: Env, session_id: str, message: str) -> Any:
    async with env.factory() as db:
        return await chat_api.chat(
            request=Request({"type": "http", "app": app_with_engine(), "state": {}}),
            body=chat_api.ChatRequest(session_id=session_id, message=message, companion_id=None),
            current_user=USER, db=db,
        )


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _record(scenario: Scenario, spec: CompanionSpec, condition: Condition, session: SessionSpec, probe: str, prior: str,
            recorder: Recorder, responder: Responder, config: RunConfig, started_calls: int, seconds: float,
            response: str | None, error: str | None, seed_report: Mapping[str, Any]) -> dict[str, Any]:
    new_calls = recorder.calls[started_calls:]
    call = new_calls[0] if new_calls else {}
    status = ("incomplete" if re.search(r"MissingScript|BudgetExceeded|UnansweredRequest", error) else "error") if error else None
    text = "" if error else (response or "")
    record: dict[str, Any] = {
        "scenario_id": scenario.scenario_id, "condition_id": condition.condition_id, "character_id": spec.character_id,
        "session_id": session.session_id, "source": responder.source,
        "provider": {"source": responder.source, "model": call.get("model") or responder.model, "configured_model": call.get("config_model")},
        "configuration": {**config.describe(), "temperature": call.get("temperature"), "max_tokens": call.get("max_tokens")},
        "prompt_refs": {
            "system_chars": len(str((call.get("history") or [{}])[0].get("content", ""))),
            "system_sha256_16": _sha(str((call.get("history") or [{}])[0].get("content", ""))),
            "layers": call.get("layers") or {}, "history_messages": len(call.get("history") or []),
            "probe_sha256_16": _sha(probe), "seeding": seed_report,
        },
        "probe": probe, "prior_text": prior, "dialogue": text,
        "expected": {"terms": list(scenario.expected_terms.get(spec.character_id, ())),
                     "criteria": list(scenario.criteria.get(spec.character_id, ()) + scenario.criteria.get("*", ())),
                     "failure_conditions": list(scenario.failure_conditions)},
        "provider_calls": len(new_calls), "replayed": bool(call.get("replayed")), "usage": call.get("usage") or {},
        "timing": {"generation_seconds": call.get("seconds"), "turn_seconds": round(seconds, 4)},
        "finish_reason": call.get("finish_reason"), "error": error or call.get("error"),
        "observed": {}, "failures": [], "pending_human_review": [], "human_ratings": [],
    }
    if config.include_prompts and call:
        record["prompt"] = call.get("history", [{}])[0].get("content", "")
    if status is None and (call.get("error") or len(new_calls) != 1):
        status = "error"
        record["error"] = record["error"] or f"expected one provider call, saw {len(new_calls)}"
    if status is None:
        checks = metrics.automated_checks(text, scenario=scenario, spec=spec, probe=probe,
                                          finish_reason=call.get("finish_reason"))
        record.update(observed=checks["observed"], failures=checks["failures"],
                      pending_human_review=checks["pending_human_review"] + [f"failure condition: {c}" for c in scenario.failure_conditions])
        status = "failed" if checks["failures"] else "passed"
    record["status"] = status
    return record


async def run_evaluation(scenarios: Sequence[Scenario], responder: Responder, config: RunConfig | None = None,
                         on_record: Callable[[dict[str, Any]], None] | None = None,
                         ledger: Ledger | None = None) -> dict[str, Any]:
    """Execute scenarios and return the machine-readable result document.

    ``on_record`` receives every record the moment it exists. ``ledger``
    persists each reply at the provider seam and, when resuming, replays the
    replies an interrupted run already obtained (see ``Ledger``).
    """
    config = config or RunConfig()
    selected = [s for s in scenarios if config.scenario_ids is None or s.scenario_id in config.scenario_ids]
    records: list[dict[str, Any]] = []
    scenario_status: list[dict[str, Any]] = []
    persisted: dict[str, str] = {}
    cast_specs: dict[tuple[str, str], CompanionSpec] = {}
    started = perf_counter()
    async with isolated() as (env, patch):
        recorder = Recorder(responder, config, ledger)
        _install_recorder(patch, recorder, config)
        for scenario in selected:
            ok, why = scenario.executable()
            if not ok:
                scenario_status.append({"scenario_id": scenario.scenario_id, "status": "skipped", "reason": why})
                continue
            cast = config.cast if config.cast is not None else scenario.companions
            cells = done = 0
            for spec in cast:
                cast_specs[(scenario.scenario_id, spec.character_id)] = spec
                scenario_view = dataclasses.replace(scenario, companions=tuple(cast)) if config.cast is not None else scenario
                for condition in scenario.conditions:
                    if config.shared_companions and spec.character_id in persisted:
                        companion_id = persisted[spec.character_id]
                    else:
                        companion_id = await seed_companion(env, spec, scenario_view, condition)
                        persisted[spec.character_id] = companion_id
                    report = seed_report_for(scenario_view, spec)
                    history_text = ""
                    for session in condition.sessions:
                        sid = await open_session(env, companion_id, scenario_view, seed_mood=True)
                        turns = list(session.turns)
                        probe = session.probes()[-1] if session.probes() and turns and turns[-1].role == "user" else None
                        await seed_turns(env, sid, companion_id, turns[:-1] if probe else turns)
                        prior = history_text + " ".join(t.content for t in (turns[:-1] if probe else turns))
                        history_text = prior + " "
                        if probe is None:
                            continue
                        cells += 1
                        cell = f"{scenario.scenario_id}/{condition.condition_id}/{spec.character_id}/{session.session_id}"
                        recorder.context = {"scenario_id": scenario.scenario_id, "character_id": spec.character_id,
                                            "condition_id": condition.condition_id, "session_id": sid, "cell": cell}
                        if not recorder.can_generate(cell):
                            records.append(_record(scenario, spec, condition, session, probe, prior, recorder, responder, config,
                                                   len(recorder.calls), 0.0, None, "BudgetExceeded: generation budget reached", report))
                            if on_record:
                                on_record(records[-1])
                            continue
                        before, t0 = len(recorder.calls), perf_counter()
                        response = error = None
                        try:
                            result = await chat_once(env, sid, probe)
                            response = result.response
                        except Exception as exc:  # the turn itself failed; recorded, not retried
                            error = f"{type(exc).__name__}: {exc}"
                        error = error or (recorder.calls[before]["error"] if len(recorder.calls) > before and recorder.calls[before].get("error") else None)
                        args = (scenario, spec, condition, session, probe, prior, recorder, responder, config,
                                before, perf_counter() - t0, response, error, report)
                        try:
                            record = _record(*args)
                        except Exception as exc:  # a measurement bug must not discard a paid-for reply
                            record = _record(*args[:-3], response, f"measurement failed: {type(exc).__name__}: {exc}", report)
                            record["dialogue"], record["status"] = response or "", "error"
                        records.append(record)
                        if on_record:
                            on_record(record)
                        done += record["status"] in ("passed", "failed")
            scenario_status.append({"scenario_id": scenario.scenario_id, "expected_cells": cells, "completed_cells": done,
                                    "status": "complete" if cells and done == cells else "partial" if done else "incomplete"})
        message_rows = await env.count(models.Message)
        jobs = len(await env.jobs())
    accounting = {"new_generations": recorder.generations, "replayed_from_ledger": recorder.replayed,
                  "earlier_requests_in_ledger": ledger.prior_requests if ledger else 0, "total_consumed": recorder.consumed,
                  "max_generations": config.max_generations}
    return assemble(selected, records, scenario_status, responder, config, perf_counter() - started, recorder.generations, cast_specs,
                    {"messages_in_eval_db": message_rows, "background_jobs_not_run": jobs, "generation_accounting": accounting})


def samples_from(records: Iterable[Mapping[str, Any]], config: Mapping[str, Any] | None = None,
                 specs: Mapping[tuple[str, str], CompanionSpec] | None = None) -> list[metrics.Sample]:
    persisted = bool((config or {}).get("shared_companions"))
    return [metrics.Sample(r["scenario_id"], r["character_id"], r["session_id"], r["dialogue"], r.get("condition_id", "default"),
                           r.get("probe", ""), r.get("prior_text", ""), persisted, (specs or {}).get((r["scenario_id"], r["character_id"])))
            for r in records if r.get("status") in ("passed", "failed") and r.get("dialogue")]


LIMITATIONS = (
    "Dimension values are lexical/stylometric proxies over generated text; they do not replace blind human judgment.",
    "Free-text expected criteria and failure conditions are listed as pending human review; only quoted literal phrases are machine-checked.",
    "Memory extraction, emotional associations and evidence are not seeded or run; opinion/memory grounding reflects seeded self-model items only.",
    "Scripted responses test the harness and scoring, not any model's personality.",
)


def assemble(selected: Sequence[Scenario], records: list[dict[str, Any]], scenario_status: list[dict[str, Any]],
             responder: Responder, config: RunConfig, seconds: float, generations: int,
             specs: Mapping[tuple[str, str], CompanionSpec], extra: Mapping[str, Any]) -> dict[str, Any]:
    by_id = {s.scenario_id: s for s in selected}
    dims = metrics.compute_dimensions(samples_from(records, config.describe(), specs), by_id)
    counts: dict[str, int] = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    usage = [r["usage"] for r in records if r["usage"]]
    models_seen = sorted({str(r["provider"]["model"]) for r in records if r["provider"]["model"]})
    body = {
        "format": RESULT_FORMAT,
        "configuration": config.describe(),
        "provider": {"source": responder.source, "models": models_seen},
        "fixtures": sorted({s.source for s in selected}),
        "results": records, "scenarios": scenario_status, "dimensions": dims,
        "summary": {"statuses": counts, "generations": generations, "records": len(records),
                    "scenarios_complete": sum(s["status"] == "complete" for s in scenario_status),
                    "scenarios_skipped": sum(s["status"] == "skipped" for s in scenario_status),
                    "tokens": {k: sum(int(u.get(k) or 0) for u in usage) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
                    if usage else None, **extra},
        "limitations": list(LIMITATIONS),
    }
    body["run_id"] = _sha(json.dumps({"c": body["configuration"], "r": [(r["scenario_id"], r["character_id"], r["condition_id"], r["dialogue"]) for r in records]}, sort_keys=True))
    body["timing"] = {"total_seconds": round(seconds, 3)}  # volatile, kept apart from the reproducible body
    return body


def stable_view(result: Mapping[str, Any]) -> dict[str, Any]:
    """The result with run-to-run volatile fields (wall-clock timing) removed."""
    def scrub(record: Mapping[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in record.items() if k != "timing"}
    return {**{k: v for k, v in result.items() if k != "timing"}, "results": [scrub(r) for r in result["results"]]}


def write_artifacts(result: Mapping[str, Any], scenarios: Mapping[str, Scenario], out_dir: str | Path, *, seed: str = "phase7") -> dict[str, Path]:
    """results.json, blind_package.json (anonymous) and blind_truth.json (hidden ground truth)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    public, truth = blind.build_blind_package(result["results"], scenarios, seed=seed)
    paths = {"results": out / "results.json", "blind_package": out / "blind_package.json", "blind_truth": out / "blind_truth.json"}
    paths["results"].write_text(json.dumps(result, indent=2, ensure_ascii=False))
    paths["blind_package"].write_text(json.dumps(public, indent=2, ensure_ascii=False))
    paths["blind_truth"].write_text(json.dumps(truth, indent=2, ensure_ascii=False))
    return paths


def compare_runs(baseline: Mapping[str, Any], improved: Mapping[str, Any]) -> dict[str, Any]:
    """Pairwise baseline-versus-improved comparison that refuses to hide a mismatch.

    Cells are paired by scenario/condition/character. The comparison is marked
    not comparable (and says why) when models, response sources or probes differ,
    or when no cell pairs. Prompt size is reported per pair because removing
    layers necessarily changes the context budget; that is stated, never hidden.
    """
    key = lambda r: (r["scenario_id"], r["condition_id"], r["character_id"])  # noqa: E731
    b = {key(r): r for r in baseline["results"] if r["status"] in ("passed", "failed")}
    i = {key(r): r for r in improved["results"] if r["status"] in ("passed", "failed")}
    shared = sorted(set(b) & set(i))
    reasons: list[str] = []
    if not shared:
        reasons.append("no cells completed in both runs")
    if baseline["provider"]["models"] != improved["provider"]["models"]:
        reasons.append(f"different models: {baseline['provider']['models']} vs {improved['provider']['models']}")
    if baseline["provider"]["source"] != improved["provider"]["source"]:
        reasons.append("different response sources")
    if "scripted" in (baseline["provider"]["source"], improved["provider"]["source"]):
        reasons.append("scripted responses do not depend on the prompt, so behavioural differences are not evidence")
    if any(b[k]["prompt_refs"]["probe_sha256_16"] != i[k]["prompt_refs"]["probe_sha256_16"] for k in shared):
        reasons.append("different probes")
    if baseline["configuration"]["dropped_prompt_layers"] == improved["configuration"]["dropped_prompt_layers"]:
        reasons.append("both runs use the same prompt configuration")
    pairs = [{"cell": "/".join(k), "baseline_prompt_chars": b[k]["prompt_refs"]["system_chars"],
              "improved_prompt_chars": i[k]["prompt_refs"]["system_chars"],
              "context_budget_delta_chars": i[k]["prompt_refs"]["system_chars"] - b[k]["prompt_refs"]["system_chars"],
              "baseline_drift": bool(metrics.drift_hits(b[k]["dialogue"])), "improved_drift": bool(metrics.drift_hits(i[k]["dialogue"]))}
             for k in shared]
    delta = {name: (None if baseline["dimensions"][name]["value"] is None or improved["dimensions"][name]["value"] is None
                    else round(improved["dimensions"][name]["value"] - baseline["dimensions"][name]["value"], 4))
             for name in metrics.DIMENSIONS}
    return {"comparable": not reasons, "reasons": reasons,
            "scope": "behaviour and prompt size when comparable; otherwise only the prompt-size pairs are meaningful",
            "baseline": baseline["configuration"]["name"], "improved": improved["configuration"]["name"],
            "context_budget_note": "the baseline prompt omits the removed layers, so the two prompts differ in length by design",
            "pairs": pairs, "dimension_delta": delta,
            "dimension_values": {n: {"baseline": baseline["dimensions"][n]["value"], "improved": improved["dimensions"][n]["value"]}
                                 for n in metrics.DIMENSIONS}}
