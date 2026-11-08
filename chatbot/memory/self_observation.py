"""Validation of companion self-observations proposed by the existing extraction call.

The extraction model already reads each completed turn once. Its response may
also propose *self-observations*: views the companion itself expressed in its
reply (an opinion it argued for, a preference, an interest, something it came to
understand about itself, an explicit change of mind). This module decides which
proposals are trustworthy enough to reach ``chatbot.soul.self_model``; it adds no
model call.

A proposal is kept only when all of these hold:

* ``status`` is ``"real"`` and the user's message is not a roleplay frame;
* its ``evidence`` is a verbatim span of the **companion's own reply** (the
  user's words and invented quotes cannot ground a self-view);
* the evidence sentence is not a question, quoted line, asterisk action,
  conditional, fiction or joke;
* names and numbers in the statement appear in the turn and the statement
  shares wording with its evidence;
* an opinion or preference that merely repeats the user's own position carries
  independent reasoning of the companion's, otherwise it is an echo and is
  rejected (agreement is not an earned view);
* a stated ``reason`` is kept only if the companion's reply supports it;
  an unsupported reason is dropped, which leaves the view unreasoned (the
  self-model then asks for repeated evidence) rather than inventing a rationale.

Hedged phrasing ("I think...") is deliberately allowed: that is how opinions are
voiced.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any

from chatbot.memory import llm_extraction as _llm
from chatbot.soul import self_model as sm

MAX_OBSERVATIONS_PER_TURN = 3
ECHO_OVERLAP = 0.8
REASON_SUPPORT = 0.5

_KNOWN_LINE = re.compile(r"^(opinion|preference|interest|self_understanding):([^:\n]*): ", re.M)
_CONDITIONAL = _llm._CONDITIONAL
_FICTION, _JOKE = _llm._FICTION, _llm._JOKE
_QUOTED_SPAN, _ASTERISK_SPAN = _llm._QUOTED_SPAN, _llm._ASTERISK_SPAN
_OOC_PREFIX = _llm._OOC_PREFIX


def parse_self_observations(text: Any, limits: _llm.LLMExtractionLimits | None = None) -> list[Any]:
    """The ``self`` list of a response, or ``[]`` when absent or malformed."""
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
    items = data.get("self") if isinstance(data, Mapping) else None
    return items if isinstance(items, list) else []


def known_item_keys(known_items: str) -> set[str]:
    """Item keys listed in ``chatbot.soul.self_model.known_items_text`` output."""
    return {f"{kind}:{subject}" for kind, subject in _KNOWN_LINE.findall(known_items or "")}


def validate_self_observations(
    raw_items: Sequence[Any],
    *,
    user_message: str,
    assistant_message: str,
    known_items: str = "",
    limits: _llm.LLMExtractionLimits | None = None,
) -> tuple[list[dict[str, Any]], list[_llm.RejectedCandidate]]:
    """Accepted observations (as ``SelfObservation`` field dicts) and rejections."""
    limits = limits or _llm.LLMExtractionLimits()
    keys = known_item_keys(known_items)
    sentences = _llm._sentence_spans(assistant_message)
    assistant_text = " ".join(normalized for _, normalized in sentences)
    user_text = _llm._normalize(user_message)
    turn_text = _llm._normalize(f"{user_message} {assistant_message}")
    turn_words = {_llm._stem(word) for word in _llm._words(turn_text)}
    assistant_stems = _stems(assistant_text)
    user_stems = _stems(user_text)
    roleplay = bool(_llm._ROLEPLAY_FRAME.search(_llm._normalize_quotes(user_message)))

    accepted: list[dict[str, Any]] = []
    rejected: list[_llm.RejectedCandidate] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        label = _llm._clean_text(str(raw.get("statement") or ""))
        reason, observation = _validate_one(
            raw,
            keys=keys,
            sentences=sentences,
            assistant_text=assistant_text,
            turn_text=turn_text,
            turn_words=turn_words,
            assistant_stems=assistant_stems,
            user_stems=user_stems,
            roleplay=roleplay,
            limits=limits,
        )
        if observation is None:
            rejected.append(_llm.RejectedCandidate(label, reason or "invalid"))
            continue
        identity = f"{observation['kind']}:{sm._subject_key(observation['subject'])}:{observation['relation']}"
        if identity in seen:
            rejected.append(_llm.RejectedCandidate(label, "duplicate"))
            continue
        if len(accepted) >= MAX_OBSERVATIONS_PER_TURN:
            rejected.append(_llm.RejectedCandidate(label, "over_observation_limit"))
            continue
        seen.add(identity)
        accepted.append(observation)
    return accepted, rejected


def _validate_one(
    raw: Mapping[str, Any],
    *,
    keys: set[str],
    sentences: list[tuple[str, str]],
    assistant_text: str,
    turn_text: str,
    turn_words: set[str],
    assistant_stems: set[str],
    user_stems: set[str],
    roleplay: bool,
    limits: _llm.LLMExtractionLimits,
) -> tuple[str | None, dict[str, Any] | None]:
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in sm.KINDS:
        return "bad_kind", None
    status = str(raw.get("status") or "").strip().lower()
    if status != "real":
        return (f"status_{status}" if status in _llm.STATUSES else "missing_status"), None
    if roleplay:
        return "roleplay", None

    statement = _llm._clean_text(str(raw.get("statement") or ""))
    if not statement or len(statement) > sm.MAX_STATEMENT_CHARS:
        return "bad_statement", None
    subject = _llm._clean_text(str(raw.get("subject") or ""))
    known = str(raw.get("key") or "").strip()
    if known in keys and known.startswith(f"{kind}:"):
        subject = known.split(":", 1)[1]  # continues an established item
    if not sm._subject_key(subject):
        return "bad_subject", None

    significance = str(raw.get("significance") or "ordinary").strip().lower()
    if significance not in sm.WEIGHTS:
        return "bad_significance", None
    change_of_mind = raw.get("change_of_mind") is True
    relation = "opposing" if change_of_mind else str(raw.get("relation") or "consistent").strip().lower()
    if relation not in sm.RELATIONS:
        return "bad_relation", None

    evidence = _llm._clean_text(str(raw.get("evidence") or ""))
    evidence_key = _llm._normalize(evidence).rstrip(" .!")
    if not evidence or len(evidence) > limits.max_evidence_chars or len(evidence_key) < 3:
        return "missing_evidence", None
    start = assistant_text.find(evidence_key)
    if start < 0:
        # Only the companion's own words can ground a view of the companion.
        return "evidence_not_in_assistant_reply", None

    if _llm._unsupported_details(statement, evidence_key, turn_text, turn_words):
        return "unsupported_content", None
    signal = _signal(sentences, (start, start + len(evidence_key)), evidence_key)
    if signal:
        return signal, None

    reason = _grounded_reason(_llm._clean_text(str(raw.get("reason") or "")), assistant_stems)
    if kind in {"opinion", "preference"}:
        statement_stems = _content_stems(statement)
        repeats_user = (
            bool(statement_stems)
            and len(statement_stems & user_stems) / len(statement_stems) >= ECHO_OVERLAP
        )
        reason_stems = _content_stems(reason)
        independent = bool(reason) and (
            not reason_stems or len(reason_stems & user_stems) / len(reason_stems) < ECHO_OVERLAP
        )
        if repeats_user and not independent:
            return "echo_of_user", None

    return None, {
        "kind": kind,
        "subject": subject[: sm.MAX_SUBJECT_KEY_CHARS],
        "statement": statement,
        "excerpt": evidence[: sm.MAX_EXCERPT_CHARS],
        "reason": reason,
        "significance": significance,
        "status": "real",
        "relation": relation,
        "change_of_mind": change_of_mind,
    }


def _signal(sentences: list[tuple[str, str]], span: tuple[int, int], evidence_key: str) -> str | None:
    """Why the evidence is not sincere real-world speech by the companion (hedges are fine)."""
    for _, (original, normalized) in _llm._indexed_overlapping(sentences, *span):
        if original.rstrip().endswith("?"):
            return "question"
        analysis = normalized
        out_of_character = _OOC_PREFIX.match(analysis)
        if out_of_character:
            analysis = analysis[out_of_character.end():]
        outside = _QUOTED_SPAN.sub(" ", _ASTERISK_SPAN.sub(" ", analysis))
        if evidence_key not in _llm._normalize(outside) and evidence_key in analysis:
            return "quoted_or_action"
        outside = _llm._clean_text(outside)
        if _CONDITIONAL.search(outside):
            return "hypothetical"
        if _FICTION.search(outside):
            return "fiction"
        if _JOKE.search(outside):
            return "joke"
    return None


def _grounded_reason(reason: str, assistant_stems: set[str]) -> str:
    """The reason if the companion's reply supports it, else ``""`` (never invented)."""
    stems = _content_stems(reason)
    if not reason or not stems or len(reason) > sm.MAX_STATEMENT_CHARS:
        return ""
    return reason if len(stems & assistant_stems) / len(stems) >= REASON_SUPPORT else ""


def _stems(text: str) -> set[str]:
    return {_llm._stem(word) for word in _llm._words(text) if word not in _llm._STOPWORDS}


def _content_stems(text: str) -> set[str]:
    return _stems(_llm._normalize(text))


__all__ = [
    "MAX_OBSERVATIONS_PER_TURN",
    "known_item_keys",
    "parse_self_observations",
    "validate_self_observations",
]
