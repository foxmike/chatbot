"""Validation of conversational-thread updates proposed by the existing extraction call.

The extraction model already reads each completed turn once. Its response may
also propose *thread updates*: unfinished conversational business observed in
the turn (a shared project not yet done, an unanswered question, an interest of
either side, an explicit user request, a bounded goal), or an existing known
thread that the turn resolved. This module decides which proposals are
trustworthy enough to reach ``chatbot.soul.conversational_threads``; it adds no
model call.

A proposal is kept only when all of these hold:

* ``ownership`` / ``kind`` / ``status`` / ``importance`` are from the allowed
  vocabularies;
* the user's message is not a roleplay frame, and the evidence sentence carries
  no fiction, joke, asterisk action or quoted line;
* the evidence is a verbatim span of the turn — the *user's* words for
  ``user``-owned threads, the *companion's* words for ``companion``-owned ones,
  either side for ``shared`` business — so invented quotes cannot ground a thread;
* the subject adds nothing the turn does not support;
* a carried ``key`` (continues/resolves) or ``supersedes`` reference is copied
  only when it names a thread actually listed in ``KNOWN THREADS``; an unknown
  ``supersedes`` target is rejected, an unknown ``key`` is dropped (the subject
  must then match on its own).

Questions are deliberately allowed as evidence: an unanswered question is
exactly the kind of open thread a companion should remember.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any

from chatbot.memory import llm_extraction as _llm
from chatbot.soul import conversational_threads as ct

MAX_THREAD_UPDATES_PER_TURN = 3

_KNOWN_THREAD_LINE = re.compile(r"^([^:\n]{2,64}): ", re.M)


def parse_thread_observations(text: Any, limits: _llm.LLMExtractionLimits | None = None) -> list[Any]:
    """The ``threads`` list of a response, or ``[]`` when absent or malformed."""
    limits = limits or _llm.LLMExtractionLimits()
    if not isinstance(text, str) or len(text) > limits.max_response_chars:
        return []
    text = _llm._CODE_FENCE.sub("", text.strip()).strip()
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return []
    items = data.get("threads") if isinstance(data, Mapping) else None
    return items if isinstance(items, list) else []


def known_thread_keys(known_threads: str) -> set[str]:
    """Thread keys listed in ``chatbot.soul.conversational_threads.known_threads_text``."""
    return {key.strip() for key in _KNOWN_THREAD_LINE.findall(known_threads or "")}


def validate_thread_observations(
    raw_items: Sequence[Any],
    *,
    user_message: str,
    assistant_message: str,
    known_threads: str = "",
    limits: _llm.LLMExtractionLimits | None = None,
) -> tuple[list[dict[str, Any]], list[_llm.RejectedCandidate]]:
    """Accepted thread updates (as ``ThreadObservation`` field dicts) and rejections."""
    limits = limits or _llm.LLMExtractionLimits()
    keys = known_thread_keys(known_threads)
    assistant_sentences = _llm._sentence_spans(assistant_message)
    user_sentences = _llm._sentence_spans(user_message)
    assistant_text = " ".join(normalized for _, normalized in assistant_sentences)
    user_text = " ".join(normalized for _, normalized in user_sentences)
    turn_text = _llm._normalize(f"{user_message} {assistant_message}")
    turn_words = {_llm._stem(word) for word in _llm._words(turn_text)}
    roleplay = bool(_llm._ROLEPLAY_FRAME.search(_llm._normalize_quotes(user_message)))

    accepted: list[dict[str, Any]] = []
    rejected: list[_llm.RejectedCandidate] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        label = _llm._clean_text(str(raw.get("subject") or ""))
        reason, observation = _validate_one(
            raw,
            keys=keys,
            assistant_sentences=assistant_sentences,
            user_sentences=user_sentences,
            assistant_text=assistant_text,
            user_text=user_text,
            turn_text=turn_text,
            turn_words=turn_words,
            roleplay=roleplay,
            limits=limits,
        )
        if observation is None:
            rejected.append(_llm.RejectedCandidate(label, reason or "invalid"))
            continue
        identity = (
            f"{observation['ownership']}:{observation['kind']}:"
            f"{ct.subject_key(observation['subject'])}:{observation['status']}"
        )
        if identity in seen:
            rejected.append(_llm.RejectedCandidate(label, "duplicate"))
            continue
        if len(accepted) >= MAX_THREAD_UPDATES_PER_TURN:
            rejected.append(_llm.RejectedCandidate(label, "over_thread_limit"))
            continue
        seen.add(identity)
        accepted.append(observation)
    return accepted, rejected


def _validate_one(
    raw: Mapping[str, Any],
    *,
    keys: set[str],
    assistant_sentences: list[tuple[str, str]],
    user_sentences: list[tuple[str, str]],
    assistant_text: str,
    user_text: str,
    turn_text: str,
    turn_words: set[str],
    roleplay: bool,
    limits: _llm.LLMExtractionLimits,
) -> tuple[str | None, dict[str, Any] | None]:
    ownership = str(raw.get("ownership") or "").strip().lower()
    if ownership not in ct.OWNERSHIPS:
        return "bad_ownership", None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in ct.KINDS:
        return "bad_kind", None
    status = str(raw.get("status") or "open").strip().lower()
    if status not in ct.STATUSES:
        return "bad_status", None
    importance = str(raw.get("importance") or "ordinary").strip().lower()
    if importance not in ct.IMPORTANCE:
        return "bad_importance", None
    if roleplay:
        return "roleplay", None

    subject = _llm._clean_text(str(raw.get("subject") or ""))
    if not subject or len(subject) > ct.MAX_SUMMARY_CHARS:
        return "bad_subject", None
    topic = _llm._clean_text(str(raw.get("topic") or ""))[: ct.MAX_TOPIC_CHARS]

    # Only a thread actually listed as known may be referenced by key.
    key = str(raw.get("key") or "").strip()
    if key and key not in keys:
        key = ""
    supersedes = str(raw.get("supersedes") or "").strip()
    if supersedes and supersedes not in keys:
        return "unknown_supersedes", None

    evidence = _llm._clean_text(str(raw.get("evidence") or ""))
    evidence_key = _llm._normalize(evidence).rstrip(" .!")
    if not evidence or len(evidence) > limits.max_evidence_chars or len(evidence_key) < 3:
        return "missing_evidence", None
    # The owner's own side grounds the thread; shared business may be grounded
    # by either side of the conversation.
    if ownership == "companion":
        start = assistant_text.find(evidence_key)
        role, side_sentences = "assistant", assistant_sentences
        if start < 0:
            return "evidence_not_in_assistant_reply", None
    elif ownership == "user":
        start = user_text.find(evidence_key)
        role, side_sentences = "user", user_sentences
        if start < 0:
            return "evidence_not_in_user_message", None
    else:
        in_assistant = assistant_text.find(evidence_key) >= 0
        if in_assistant:
            start, role, side_sentences = assistant_text.find(evidence_key), "assistant", assistant_sentences
        else:
            start, role, side_sentences = user_text.find(evidence_key), "user", user_sentences
        if start < 0:
            return "evidence_not_in_turn", None
    # The subject is a sentence, so its first word is normally capitalised; the
    # unsupported-details check must not treat that as an invented proper noun.
    # A resolution (or a known-thread reference) is anchored to the existing
    # thread rather than derived from this turn's wording, so its evidence only
    # has to document the completion, not restate the subject.
    if status == "open" and not key and _llm._unsupported_details(
        subject[:1].lower() + subject[1:], evidence_key, turn_text, turn_words
    ):
        return "unsupported_content", None
    signal = _signal(side_sentences, (start, start + len(evidence_key)))
    if signal:
        return signal, None

    return None, {
        "ownership": ownership,
        "kind": kind,
        "subject": subject,
        "status": status,
        "topic": topic,
        "importance": importance,
        "excerpt": evidence[: ct.MAX_EXCERPT_CHARS],
        "role": role,
        "key": key,
        "supersedes": supersedes,
    }


def _signal(
    sentences: list[tuple[str, str]], span: tuple[int, int]
) -> str | None:
    """Why the evidence is not durable real-world business (questions are allowed)."""
    for _, (original, normalized) in _llm._indexed_overlapping(sentences, *span):
        analysis = normalized
        out_of_character = _OOC_PREFIX.match(analysis)
        if out_of_character:
            analysis = analysis[out_of_character.end():]
        outside = _QUOTED_SPAN.sub(" ", _ASTERISK_SPAN.sub(" ", analysis))
        if _llm._FICTION.search(_llm._clean_text(outside)):
            return "fiction"
        if _llm._JOKE.search(_llm._clean_text(outside)):
            return "joke"
        if _CONDITIONAL.search(_llm._clean_text(outside)):
            return "hypothetical"
        if original.rstrip().endswith("*") or _ASTERISK_SPAN.search(analysis):
            return "quoted_or_action"
    return None


_CONDITIONAL = _llm._CONDITIONAL
_FICTION, _JOKE = _llm._FICTION, _llm._JOKE
_QUOTED_SPAN, _ASTERISK_SPAN = _llm._QUOTED_SPAN, _llm._ASTERISK_SPAN
_OOC_PREFIX = _llm._OOC_PREFIX


__all__ = [
    "MAX_THREAD_UPDATES_PER_TURN",
    "known_thread_keys",
    "parse_thread_observations",
    "validate_thread_observations",
]
