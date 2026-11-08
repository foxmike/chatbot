"""Fact and episode extraction from chat turns for the chatbot engine.

Facts are taken only from what the *user* states about themselves and their
life, one sentence at a time, by deterministic rules (no model call). A
sentence becomes a fact only when it carries a recognised durable cue (identity,
location, work, relationships, preferences, habits, beliefs, goals, ongoing
activities or a meaningful life event) *and* none of the non-factual signals
below applies:

- questions and requests;
- hypotheticals, wishes, conditionals and plans expressed with modals;
- hedged or uncertain statements;
- fiction: roleplay framing, asterisk actions, dreams, stories, quoted lines;
- jokes and retracted statements ("just kidding");
- temporary states ("I'm tired", "I'm feeling great today");
- remarks about the conversation itself ("I love that idea").

An ``OOC:`` / "out of character" prefix marks a real statement inside a
roleplay message. Assistant text never becomes a user fact: it is either an
echo of the user's statement (a duplicate) or an ungrounded claim.

One episode (a compact summary of the exchange) is still recorded per turn.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import inspect
import re
import uuid
from typing import Any, Protocol


_MAX_FACTS_PER_TURN = 8
_MAX_SENTENCE_CHARS = 500
_MAX_EPISODE_SUMMARY_CHARS = 1_200
# Kept for compatibility with existing consumers of the entry metadata;
# ``_RULES_VERSION`` identifies the rule set that produced an entry.
_FACT_EXTRACTOR = "heuristic-v1"
_RULES_VERSION = 2

_STATED_FACT_CONFIDENCE = 0.72
_REMEMBER_REQUEST_CONFIDENCE = 0.85

_IM = r"i(?:'m| am)"

_WHITESPACE_PATTERN = re.compile(r"\s+")
_SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Leading discourse words that do not change a sentence's meaning.
_LEADING_FILLER = _rx(
    r"^(?:(?:hi|hey|hello|yo|ok|okay|well|so|oh|also|and|but|btw|by the way|anyway|"
    r"honestly|tbh|to be honest|if i'?m honest|if i am honest|fyi|um|uh|haha|lol)"
    r"(?:\s+there)?\s*[,!.:;-]?\s+)+"
)
_OOC_PREFIX = _rx(r"^\(?\s*(?:ooc|out of character)\s*[:)\-]\s*\)?\s*")
# Intensifiers between the subject and its verb ("I actually live in ...")
# carry no factual content; removing them lets one cue pattern cover both forms.
_SUBJECT_ADVERBS = _rx(
    r"\b(i|i'm|i am)\s+(?:(?:actually|also|still|currently|really|totally|definitely|"
    r"absolutely|truly|honestly|seriously|literally|basically|mostly|genuinely|"
    r"originally)\s+)+"
)

# --- non-factual signals -------------------------------------------------

_REQUEST = _rx(
    r"^(?:please|pls|can you|could you|would you|will you|tell me|explain|write|make|"
    r"create|show me|help me|give me|let me know|remind me|find|list|describe|"
    r"summari[sz]e|translate)\b"
)
_INTERROGATIVE = _rx(
    r"^(?:(?:what|why|how|when|where|who|which|whose)\s+(?:is|are|was|were|do|does|did|"
    r"can|could|would|should|will|have|has|am|about|if)\b|"
    r"(?:do|does|did|are|is|am|can|could|would|should|will|have|has|was|were|shall)\s+"
    r"(?:you|i|we|it|they|he|she|there|my|your)\b)"
)
_HYPOTHETICAL = _rx(
    r"^if\b|\bif (?:i|we|only)\b|\bwhat if\b|\bsuppos(?:e|ing)\b|\bimagin(?:e|ing)\b|"
    r"\bpretend(?:ing)?\b|\blet'?s say\b|\bhypothetical(?:ly)?\b|\bin theory\b|"
    r"\bassuming\b|\bi wish\b|\bsomeday\b|\bone day\b|\bin another life\b|"
    r"\bi(?:'d|'ll| would| could| might| may| will| should| shall)\b"
)
_HEDGE = _rx(
    r"\bmaybe\b|\bperhaps\b|\bprobably\b|\bpossibly\b|\bmight\b|"
    r"\bi (?:think|guess|suppose|reckon|believe|feel like|wonder)\b(?!\s+(?:about|of|in)\b)|"
    r"\bnot (?:sure|certain)\b|\bunsure\b|\bkind of\b|\bsort of\b|\bkinda\b|\bsorta\b|"
    r"\bi (?:don'?t|do not) know\b|\bor something\b"
)
_FICTION = _rx(
    r"\bin (?:my|a|the|this|our) (?:dreams?|novel|story|book|game|campaign|script|"
    r"fanfic|fic|roleplay|role-play|scene|world)\b|\bi (?:dreamt|dreamed|had a dream)\b|"
    r"\bmy (?:character|oc|persona|avatar)\b|\bin character\b|\bplaying (?:a|an|the)\b"
)
_JOKE = _rx(r"\b(?:jk|just kidding|kidding|joking)\b|(?:^|\s)/s\b")
_RETRACTION = _rx(
    r"^(?:lol\s+)?(?:jk|just kidding|kidding|joking|not really|that was a joke|"
    r"i was (?:just )?(?:joking|kidding))[.!]*$"
)
_NEGATED_MEMORY = _rx(
    r"^(?:please\s+)?(?:do not|don'?t) (?:remember|save|store|record)\b|"
    r"(?<!don't )(?<!dont )(?<!do not )(?<!never )\bforget (?:that|this|it|what i said)\b|"
    r"\b(?:ignore|disregard) (?:that|this|what i said)\b|\bnever ?mind\b|\bscratch that\b"
)
_TRANSIENT = _rx(
    rf"\b{_IM} (?:(?:feeling|so|really|very|a bit|a little|kind of|kinda|just|pretty|"
    r"super|too|quite|getting|still|not|a lot)\s+)*(?:tired|exhausted|sleepy|hungry|"
    r"thirsty|sick|ill|sad|happy|glad|excited|thrilled|bored|busy|stressed|nervous|"
    r"anxious|angry|mad|upset|annoyed|worried|scared|lonely|fine|good|okay|ok|great|"
    r"well|alright|sorry|sure|ready|done|back|here|home|late|lost|confused|curious|"
    r"wondering|thinking|trying|looking|going|about|joking|kidding|serious|mess|wreck|"
    r"free|available|feeling|in a (?:good|bad) mood|on my way)\b|"
    rf"\bi feel\b|\b{_IM} feeling\b|"
    r"\bi have (?:a |an )?(?:question|idea|quick|problem|issue|request|favou?r|"
    r"meeting|headache|cold|fever|feeling|suggestion|bug|error|thought|minute|second|"
    r"moment|doubt|concern)\b|\bi have no (?:idea|clue)\b"
)
# Statements about the conversation, not about the user.
_ABOUT_CONVERSATION = _rx(
    r"\bi (?:really |absolutely )?(?:like|love|enjoy|hate|dislike|prefer|appreciate) "
    r"(?:it|that|this|these|those|your|yours|you|the idea|the way you|how you|"
    r"what you|when you)\b|\bi never (?:said|told|meant|asked|claimed)\b"
)
_ROLEPLAY_FRAME = _rx(
    r"\blet'?s\s+(?:pretend|roleplay|role-play|role play|play pretend|imagine)\b|"
    r"\b(?:stay|staying|back|stay in|in)\s+character\b|\broleplay\b|\brole-play\b|"
    r"\*[^*\n]*\s[^*\n]*\*"
)
_ASTERISK_SPAN = re.compile(r"\*[^*\n]*\*")
_QUOTED_SPAN = re.compile(r"\"[^\"\n]*\"")

_REMEMBER_REQUEST = _rx(
    r"^(?:please\s+)?(?:remember|note|keep in mind|don'?t forget|do not forget|never forget)"
    r"\s*,?\s*(?:that\s+)?(?=(?:i|i'm|i've|i am|my)\b)"
)
_CORRECTION = _rx(
    r"^(?:correction|wait|no|sorry)\b|\b(?:actually|no longer|not anymore|anymore|"
    r"i meant|to clarify|i misspoke|i was wrong)\b|\bnow[.!]*$"
)
_PAST = _rx(r"\bi used to\b|\bwhen i was\b|\bback (?:then|in)\b")

# --- durable cues, in priority order (first match names the kind) -------

_DURABLE_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("identity", _rx(
        rf"\bmy name(?: is|'s)\b|\bcall me\b|\bi go by\b|\b{_IM} called\b|\bmy pronouns\b"
    )),
    ("experience", _rx(
        r"\bi (?:just |recently |finally )?(?:graduated|got (?:married|engaged|divorced|"
        r"promoted|fired|laid off|hired|a new job|accepted|into|my)|lost my|"
        r"had (?:a baby|surgery|an accident|a miscarriage)|broke up|started (?:a new|my new|"
        r"working|college|university|school|a business|therapy)|quit my|retired|moved|"
        r"bought (?:a|my|our)|sold my|finished (?:my|a|the)|ran (?:a|my)|won (?:a|the|my)|"
        r"passed (?:my|the)|failed (?:my|the)|visited|traveled|travelled|came back from|"
        r"got back from|adopted|became)\b|"
        r"\bmy \w+(?: \w+)? (?:died|passed away|was born)\b|"
        r"\bi(?:'ve| have) (?:just |recently |finally )?(?:graduated|been diagnosed|moved|"
        r"started|finished|retired|quit|lost|adopted|become)\b|\bi was diagnosed\b"
    )),
    ("attribute", _rx(
        rf"\b{_IM} \d{{1,3}}(?: years old)?\b|\bi was born\b|\bmy birthday\b|"
        r"\bmy (?:email|e-mail|phone|number|address|timezone|time zone|hometown|"
        r"native language|first language)\b"
    )),
    ("location", _rx(
        rf"\bi (?:live|lived|used to live) (?:in|near|at|on|with|by)\b|"
        rf"\b{_IM} (?:from|based in|originally from)\b|\bi grew up\b|\bi(?:'ve| have) lived\b"
    )),
    ("occupation", _rx(
        rf"\bi (?:work|worked|used to work) (?:as|at|for|in|on|with|from)\b|\bi study\b|\b{_IM} studying\b|"
        rf"\bi teach\b|\bi run (?:a|an|my)\b|\b{_IM} (?:retired|unemployed|self-employed|"
        r"freelance|a student)\b|\bmy (?:job|role|company|team|boss|manager|employer|"
        r"career|major)\b"
    )),
    ("preference", _rx(
        r"\bi (?:really |absolutely |truly )?(?:like|love|enjoy|adore|prefer|hate|dislike|"
        r"loathe|detest)\b|\bi can'?t stand\b|"
        rf"\b{_IM} (?:not )?(?:a (?:big |huge )?fan of|into|obsessed with|allergic to|"
        r"vegan|vegetarian|pescatarian|lactose intolerant|gluten[- ]free)\b|"
        r"\bmy (?:favou?rite|preference)\b|"
        r"\bi (?:don'?t|do not|never) (?:like|eat|drink|smoke|enjoy|watch|use)\b"
    )),
    ("habit", _rx(
        r"\bi (?:usually|always|often|never|rarely|normally|typically|generally|tend to|"
        r"regularly|used to)\b|\bevery (?:morning|day|night|evening|week|weekend|month|"
        r"year|summer|winter)\b"
    )),
    ("relationship", _rx(
        r"\bi have (?:a|an|one|two|three|four|five|six|\d+|my|no|twin|kids|children|"
        r"siblings|a few|several)\b|\bi(?:'ve| have) got (?:a|an|two|three|\d+)\b|\bi own\b|"
        r"\bmy (?:wife|husband|partner|spouse|boyfriend|girlfriend|fianc[eé]e?|son|"
        r"daughter|kids?|child|children|mom|mum|mother|dad|father|parents?|brother|sister|"
        r"siblings?|grand\w+|aunt|uncle|cousin|family|friend|best friend|dog|cat|pet|"
        r"puppy|kitten|roommate)\b"
    )),
    ("belief", _rx(
        rf"\bi believe in\b|\bi value\b|\bi care (?:a lot |deeply )?about\b|"
        rf"\b{_IM} (?:religious|christian|muslim|jewish|hindu|buddhist|atheist|agnostic)\b"
    )),
    ("goal", _rx(
        r"\bi want to (?:be|become|learn|move|start|visit|travel|write|build|open|"
        r"get better at|run|retire|study)\b|\bmy (?:goal|dream|plan) is\b|"
        rf"\b{_IM} (?:saving|training) for\b|\bi plan to\b|\bi hope to\b"
    )),
    ("activity", _rx(
        rf"\b{_IM} (?:learning|studying|training|raising|dating|married|engaged|divorced|"
        r"single|pregnant|working (?:as|at|on|for)|living (?:in|with)|writing (?:a|my)|"
        r"building (?:a|my))\b"
    )),
    # Weakest cue last: "I'm a nurse", "I'm Canadian".
    ("attribute", _rx(rf"\b{_IM} (?:an? )\w+")),
)
# "I'm Canadian" / "I'm Bob": a capitalised word straight after "I'm" (case-sensitive).
_PROPER_ATTRIBUTE = re.compile(r"\b(?:I'm|I am) [A-Z][a-z]+\b")


@dataclass(frozen=True, slots=True)
class _FactCandidate:
    content: str
    kind: str
    confidence: float
    explicit_request: bool = False
    correction: bool = False
    past: bool = False


@dataclass(frozen=True, slots=True)
class ExtractedFact:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "fact",
            "content": self.content,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ExtractedEpisode:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "episode",
            "content": self.content,
            "metadata": dict(self.metadata),
        }


class MemoryStorageAdapter(Protocol):
    async def persist(
        self,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Persist extracted memory entries and return the entries that were written."""


class MemoryExtractor:
    def __init__(
        self,
        database: Any | None = None,
        *,
        storage: MemoryStorageAdapter | None = None,
    ) -> None:
        self.database = database
        self.storage = storage

    async def extract(
        self,
        session_id: str,
        user_message: str,
        provider_response: str,
        *,
        turn_id: str | None = None,
    ) -> list[dict]:
        if self.database is None and self.storage is None:
            return []

        entries = _extract_entries(
            session_id=session_id,
            user_message=user_message,
            provider_response=provider_response,
            turn_id=turn_id,
        )
        if not entries:
            return []

        storage = self.storage or SQLAlchemyMemoryStorage(self.database)
        try:
            return await storage.persist(session_id, entries)
        except Exception:
            return []


class SQLAlchemyMemoryStorage:
    def __init__(self, database: Any | None) -> None:
        self.database = database

    async def persist(
        self,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self.database is None:
            return []

        database = self.database
        if _is_engine_like(database):
            return await self._persist_engine(database, session_id, entries)

        if callable(database) and not hasattr(database, "execute"):
            return await self._persist_factory(database, session_id, entries)

        if hasattr(database, "execute"):
            return await self._persist_session_or_connection(database, session_id, entries)

        return []

    async def _persist_engine(
        self,
        engine: Any,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        begin = getattr(engine, "begin", None)
        if not callable(begin):
            return []

        async with begin() as connection:
            return await _persist_rows(connection, session_id, entries)

    async def _persist_factory(
        self,
        factory: Any,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        session = factory()
        if hasattr(session, "__aenter__"):
            async with session as active_session:
                return await self._persist_session_or_connection(
                    active_session,
                    session_id,
                    entries,
                )
        if hasattr(session, "__enter__"):
            with session as active_session:
                return await self._persist_session_or_connection(
                    active_session,
                    session_id,
                    entries,
                )
        return await self._persist_session_or_connection(session, session_id, entries)

    async def _persist_session_or_connection(
        self,
        session: Any,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        begin = getattr(session, "begin_nested", None) if _in_transaction(session) else None
        if not callable(begin):
            begin = getattr(session, "begin", None)

        if callable(begin):
            async with _transaction_context(begin()):
                return await _persist_rows(session, session_id, entries)

        commit = getattr(session, "commit", None)
        rollback = getattr(session, "rollback", None)
        try:
            written = await _persist_rows(session, session_id, entries)
            if callable(commit):
                await _maybe_await(commit())
            return written
        except Exception:
            if callable(rollback):
                await _maybe_await(rollback())
            raise


def _extract_entries(
    *,
    session_id: str,
    user_message: str,
    provider_response: str,
    turn_id: str | None = None,
) -> list[dict[str, Any]]:
    user_message = _clean_text(user_message)
    provider_response = _clean_text(provider_response)
    if not user_message and not provider_response:
        return []

    facts = _extract_facts(session_id, user_message, provider_response, turn_id=turn_id)
    episode = _extract_episode(session_id, user_message, provider_response, turn_id=turn_id)
    entries = [fact.as_dict() for fact in facts]
    if episode is not None:
        entries.append(episode.as_dict())
    return entries


def _extract_facts(
    session_id: str,
    user_message: str,
    provider_response: str = "",
    *,
    turn_id: str | None = None,
) -> list[ExtractedFact]:
    """Facts the user states about themselves.

    *provider_response* is accepted for interface compatibility only: assistant
    text is never evidence for a user fact.
    """
    del provider_response
    candidates = _user_fact_candidates(user_message)

    facts: list[ExtractedFact] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _dedup_key(candidate.content)
        if not key or key in seen:
            continue
        seen.add(key)
        metadata: dict[str, Any] = {
            "session_id": session_id,
            "source": "user_message",
            "source_type": "explicit",
            "confidence": candidate.confidence,
            "extractor": _FACT_EXTRACTOR,
            "rules_version": _RULES_VERSION,
            "kind": candidate.kind,
        }
        if candidate.explicit_request:
            metadata["explicit_request"] = True
        if candidate.correction:
            metadata["correction"] = True
        if candidate.past:
            metadata["temporal"] = "past"
        if turn_id:
            metadata["turn_id"] = str(turn_id)
        facts.append(ExtractedFact(content=candidate.content, metadata=metadata))

    return facts[:_MAX_FACTS_PER_TURN]


def _user_fact_candidates(user_message: str) -> list[_FactCandidate]:
    """Classify each sentence of a user message; keep only real-world facts."""
    roleplay_frame = bool(_ROLEPLAY_FRAME.search(_normalize_quotes(user_message)))

    candidates: list[_FactCandidate | None] = []
    for sentence in _split_sentences(user_message):
        if _RETRACTION.match(_strip_filler(_normalize_quotes(sentence))):
            # "I love sushi. Just kidding." retracts the previous statement.
            if candidates:
                candidates[-1] = None
            candidates.append(None)
            continue
        candidates.append(_classify_user_sentence(sentence, roleplay_frame=roleplay_frame))
    return [candidate for candidate in candidates if candidate is not None]


def _classify_user_sentence(sentence: str, *, roleplay_frame: bool) -> _FactCandidate | None:
    """Return the sentence as a fact candidate, or ``None`` when it is not a real-world fact."""
    # The stored content keeps the user's own characters; rules read a copy with
    # typographic quotes normalised (a one-to-one character mapping).
    sentence = _clean_text(sentence)
    normalized = _normalize_quotes(sentence)
    if not sentence or sentence.endswith("?"):
        return None

    out_of_character = _OOC_PREFIX.match(normalized)
    if out_of_character:
        sentence = sentence[out_of_character.end():].strip()
        normalized = normalized[out_of_character.end():].strip()
        if not sentence:
            return None
    elif roleplay_frame:
        return None

    # Cues are read outside quoted lines and asterisk actions: neither is the
    # user speaking for themselves.
    analysis = _QUOTED_SPAN.sub(" ", _ASTERISK_SPAN.sub(" ", normalized))
    analysis = _strip_filler(_clean_text(analysis))
    if not analysis:
        return None
    correction = bool(_CORRECTION.search(analysis))
    analysis = _SUBJECT_ADVERBS.sub(lambda match: f"{match.group(1)} ", analysis)

    if _NEGATED_MEMORY.search(analysis):
        return None

    explicit_request = False
    remember = _REMEMBER_REQUEST.match(analysis)
    if remember:
        explicit_request = True
        analysis = analysis[remember.end():]
    elif _REQUEST.match(analysis) or _INTERROGATIVE.match(analysis):
        return None

    if (
        _HYPOTHETICAL.search(analysis)
        or _HEDGE.search(analysis)
        or _FICTION.search(analysis)
        or _JOKE.search(analysis)
        or _ABOUT_CONVERSATION.search(analysis)
    ):
        return None

    kind = _durable_kind(analysis)
    if kind is None and explicit_request:
        kind = "explicit"
    if kind is None:
        return None
    # The weakest cues do not survive a temporary-state reading
    # ("I'm a bit tired", "I'm Happy"); every other cue names something durable.
    if kind == "attribute_weak" and _TRANSIENT.search(analysis):
        return None
    if not explicit_request and kind not in {"experience"} and _transient_only(analysis):
        return None

    return _FactCandidate(
        content=_trim_sentence(sentence, _MAX_SENTENCE_CHARS),
        kind="attribute" if kind == "attribute_weak" else kind,
        confidence=_REMEMBER_REQUEST_CONFIDENCE if explicit_request else _STATED_FACT_CONFIDENCE,
        explicit_request=explicit_request,
        correction=correction,
        past=bool(_PAST.search(analysis)),
    )


def _durable_kind(text: str) -> str | None:
    last = len(_DURABLE_CUES) - 1
    for index, (kind, pattern) in enumerate(_DURABLE_CUES):
        if pattern.search(text):
            return "attribute_weak" if index == last else kind
    if _PROPER_ATTRIBUTE.search(text):
        return "attribute_weak"
    return None


def _transient_only(text: str) -> bool:
    """True when every durable cue in *text* sits inside a temporary-state phrase."""
    if not _TRANSIENT.search(text):
        return False
    remainder = _TRANSIENT.sub(" ", text)
    return _durable_kind(remainder) is None


def _strip_filler(text: str) -> str:
    return _LEADING_FILLER.sub("", text).strip()


def _normalize_quotes(text: str) -> str:
    return text.translate(_APOSTROPHES)


def _dedup_key(text: str) -> str:
    """Normalised form used to recognise the same statement twice."""
    return _clean_text(_normalize_quotes(text)).rstrip(" .!").casefold()


def _extract_episode(
    session_id: str,
    user_message: str,
    provider_response: str,
    *,
    turn_id: str | None = None,
) -> ExtractedEpisode | None:
    if not user_message and not provider_response:
        return None

    parts: list[str] = []
    if user_message:
        parts.append(f"User said: {_trim_sentence(user_message, 520)}")
    if provider_response:
        parts.append(f"Assistant responded: {_trim_sentence(provider_response, 620)}")

    summary = _trim_sentence(" ".join(parts), _MAX_EPISODE_SUMMARY_CHARS)
    return ExtractedEpisode(
        content=summary,
        metadata={
            "session_id": session_id,
            "title": _episode_title(user_message, provider_response),
            "source": "turn",
            "extractor": _FACT_EXTRACTOR,
            **({"turn_id": str(turn_id)} if turn_id else {}),
        },
    )


async def _persist_rows(
    executor: Any,
    session_id: str,
    entries: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        import sqlalchemy as sa

        from chatbot.db import models
    except Exception:
        return []

    coerced_session_id = _coerce_uuid(session_id)
    if coerced_session_id is None:
        return []

    user_id = await _session_user_id(executor, sa, models, coerced_session_id)
    if user_id is None:
        return []

    fact_entries = [
        entry
        for entry in entries
        if entry.get("type") == "fact" and _clean_text(str(entry.get("content") or ""))
    ]
    known_facts = await _existing_fact_keys(
        executor,
        sa,
        models,
        user_id,
        [_clean_text(str(entry.get("content") or "")) for entry in fact_entries],
    )

    fact_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    writable_entries: list[dict[str, Any]] = []

    for entry in entries:
        entry_type = entry.get("type")
        content = _clean_text(str(entry.get("content") or ""))
        metadata = entry.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if not content:
            continue

        if entry_type == "fact":
            key = _dedup_key(content)
            if key in known_facts:
                # The user already has this exact active fact (or it appeared
                # twice in this batch): restating it is not a new memory.
                continue
            known_facts.add(key)
            source_type = metadata.get("source_type")
            fact_rows.append(
                {
                    "user_id": user_id,
                    "fact_text": content,
                    "source_message_id": None,
                    "source_type": source_type if source_type in _SOURCE_TYPES else "inferred",
                    "source_turn_id": _coerce_uuid(metadata.get("turn_id")),
                    "confidence": float(metadata.get("confidence", 1.0)),
                }
            )
            writable_entries.append(_copy_entry(entry, content, metadata))
        elif entry_type == "episode":
            episode_rows.append(
                {
                    "session_id": coerced_session_id,
                    "title": _trim_sentence(str(metadata.get("title") or "Conversation turn"), 160),
                    "summary": content,
                    "start_message_id": None,
                    "end_message_id": None,
                }
            )
            writable_entries.append(_copy_entry(entry, content, metadata))

    if not writable_entries:
        return []

    if fact_rows:
        await _maybe_await(executor.execute(sa.insert(models.SemanticFact), fact_rows))
    if episode_rows:
        await _maybe_await(executor.execute(sa.insert(models.Episode), episode_rows))

    flush = getattr(executor, "flush", None)
    if callable(flush):
        await _maybe_await(flush())

    return writable_entries


_SOURCE_TYPES = frozenset({"explicit", "inferred"})


async def _existing_fact_keys(
    executor: Any,
    sa: Any,
    models: Any,
    user_id: Any,
    contents: Sequence[str],
) -> set[str]:
    """Dedup keys of the user's active facts that match *contents*.

    One indexed-by-user query per turn that has fact candidates. A fact whose
    confidence is 0 was deleted by the user (``memory.controls``) and does not
    count, matching how memory search treats it.
    """
    keys = {_dedup_key(content) for content in contents}
    keys.discard("")
    if not keys:
        return set()

    lowered = sorted({variant for key in keys for variant in (key, f"{key}.", f"{key}!")})
    fact = models.SemanticFact
    result = await _maybe_await(
        executor.execute(
            sa.select(fact.fact_text).where(
                fact.user_id == user_id,
                fact.confidence > 0.0,
                sa.func.lower(fact.fact_text).in_(lowered),
            )
        )
    )
    scalars = getattr(result, "scalars", None)
    if not callable(scalars):
        return set()
    rows = await _maybe_await(scalars().all())
    return {_dedup_key(str(text)) for text in rows or () if text} & keys


async def _session_user_id(
    executor: Any,
    sa: Any,
    models: Any,
    session_id: uuid.UUID,
) -> Any | None:
    result = await _maybe_await(
        executor.execute(
            sa.select(models.Session.user_id).where(models.Session.id == session_id)
        )
    )
    scalar_one_or_none = getattr(result, "scalar_one_or_none", None)
    if callable(scalar_one_or_none):
        return scalar_one_or_none()

    scalar = getattr(result, "scalar", None)
    if callable(scalar):
        return scalar()

    return None


def _copy_entry(
    entry: dict[str, Any],
    content: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": str(entry.get("type") or ""),
        "content": content,
        "metadata": dict(metadata),
    }


def _looks_like_user_fact(sentence: str) -> bool:
    """Whether one standalone user sentence would be extracted as a fact."""
    return _classify_user_sentence(
        _normalize_quotes(sentence),
        roleplay_frame=bool(_ROLEPLAY_FRAME.search(_normalize_quotes(sentence))),
    ) is not None


def _split_sentences(text: str) -> list[str]:
    text = _clean_text(text)
    if not text:
        return []

    sentences = []
    for sentence in _SENTENCE_PATTERN.split(text):
        sentence = _clean_text(sentence)
        if sentence:
            sentences.append(sentence)
    return sentences


def _episode_title(user_message: str, provider_response: str) -> str:
    source = user_message or provider_response or "Conversation turn"
    first_sentence = _split_sentences(source)
    if first_sentence:
        return _trim_sentence(first_sentence[0], 80)
    return "Conversation turn"


def _trim_sentence(text: str, max_chars: int) -> str:
    text = _clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _clean_text(text: str | None) -> str:
    if text is None:
        return ""
    return _WHITESPACE_PATTERN.sub(" ", str(text)).strip()


def _is_engine_like(value: Any) -> bool:
    return hasattr(value, "begin") and not hasattr(value, "execute")


def _in_transaction(session: Any) -> bool:
    in_transaction = getattr(session, "in_transaction", None)
    if not callable(in_transaction):
        return False
    return bool(in_transaction())


@asynccontextmanager
async def _transaction_context(transaction: Any) -> Any:
    if hasattr(transaction, "__aenter__"):
        async with transaction:
            yield
        return
    if hasattr(transaction, "__enter__"):
        with transaction:
            yield
        return
    await _maybe_await(transaction)
    yield


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


__all__ = [
    "ExtractedEpisode",
    "ExtractedFact",
    "MemoryExtractor",
    "MemoryStorageAdapter",
    "SQLAlchemyMemoryStorage",
]
