"""Prompt segment hashing for observability trace emission.

Hashes prompt segments (persona, scene, memories, history, user message)
and records these hashes in observability traces so trace telemetry and
composition patterns can be analyzed without logging sensitive user or
companion prompt text directly.

Usage — import this module to auto-install prompt segment hashing into
the global trace_collector:

    import chatbot.observability.traces  # noqa: F401 — side-effecting install

After import, every trace emitted via trace_collector.complete_trace()
will include a ``prompt_segment_hashes`` entry in its routing_decision
JSON field, keyed by segment label.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public hashing helpers
# ---------------------------------------------------------------------------

_SEGMENT_HASH_ALGORITHM = "sha256"
_SEGMENT_HASH_DIGEST_LENGTH = 16  # first 16 hex chars (64 bits)


def hash_segment(text: str, *, algorithm: str = _SEGMENT_HASH_ALGORITHM) -> str:
    """Return the hex digest of *text* using *algorithm* (default SHA-256).

    The digest is truncated to the first ``_SEGMENT_HASH_DIGEST_LENGTH``
    characters to keep storage compact while retaining enough entropy to
    distinguish distinct prompt components.
    """
    if not text:
        return ""
    h = hashlib.new(algorithm)
    h.update(text.encode("utf-8", errors="replace"))
    return h.hexdigest()[:_SEGMENT_HASH_DIGEST_LENGTH]


def hash_segments(segments: Mapping[str, str]) -> dict[str, str]:
    """Hash multiple named prompt segments, returning ``{name: hash}``."""
    return {name: hash_segment(text) for name, text in segments.items()}


# ---------------------------------------------------------------------------
# Prompt segment extraction
# ---------------------------------------------------------------------------

_SEGMENT_SEPARATOR = "\n\n"


def extract_prompt_segments(prompt: str) -> dict[str, str]:
    """Parse a flat prompt string into named segments.

    Heuristic: split on double-newline and label each block by the
    leading word (e.g. ``user:``, ``assistant:``, ``system:``).  The
    final segment is labelled ``"user_message"``.
    """
    if not prompt:
        return {}

    blocks = [b.strip() for b in prompt.split(_SEGMENT_SEPARATOR) if b.strip()]
    if not blocks:
        return {}

    segments: dict[str, str] = {}
    for i, block in enumerate(blocks):
        label = _extract_dialogue_label(block) or f"segment_{i}"
        segments[label] = block

    return segments


def _extract_dialogue_label(block: str) -> str | None:
    """Return the leading role label from a dialogue block, e.g. ``"user"``."""
    colon = block.find(":")
    if colon == -1:
        return None
    label = block[:colon].strip().lower()
    if label in {"user", "assistant", "system", "bot", "companion", "message"}:
        return label
    return None


# ---------------------------------------------------------------------------
# Trace emission integration
# ---------------------------------------------------------------------------

_SEGMENT_HASHES_KEY = "prompt_segment_hashes"
_INSTALLED_FLAG = "_traces_module_segment_hashing_installed"


def _install_segment_hashing() -> None:
    """Monkey-patch the global *trace_collector* so every completed trace
    records hashed prompt segments in its ``routing_decision`` field."""
    from chatbot.observability.tracing import trace_collector

    # Ensure we only install once.
    if getattr(trace_collector, _INSTALLED_FLAG, False):
        return

    original_complete = trace_collector.complete_trace

    async def _complete_trace_with_hashes(
        self: Any,
        trace_id: str,
        model_provider: str | None,
        model_id: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd_micros: int,
        prompt: str,
        routing_decision: dict[str, Any],
        retrieval_snippets: list[str],
        total_latency_ms: float,
    ) -> Any:
        # Call the original complete_trace first.
        trace = await original_complete(
            trace_id,
            model_provider,
            model_id,
            prompt_tokens,
            completion_tokens,
            cost_usd_micros,
            prompt,
            routing_decision,
            retrieval_snippets,
            total_latency_ms,
        )
        # Compute and attach hashed prompt segments.
        try:
            segments = extract_prompt_segments(prompt)
            hashes = hash_segments(segments) if segments else {}
            # Always include an overall prompt hash so a single-segment
            # (or unparseable) prompt is still traceable.
            if prompt:
                hashes["__full__"] = hash_segment(prompt)
            if hashes:
                trace.routing_decision[_SEGMENT_HASHES_KEY] = hashes
        except Exception:
            logger.debug(
                "Prompt segment hashing failed for trace_id=%s",
                trace_id,
                exc_info=True,
            )
        return trace

    trace_collector.complete_trace = _complete_trace_with_hashes.__get__(
        trace_collector, type(trace_collector)
    )
    setattr(trace_collector, _INSTALLED_FLAG, True)
    logger.debug("Installed prompt-segment hashing on trace_collector")


# ---------------------------------------------------------------------------
# Auto-install on import
# ---------------------------------------------------------------------------

_install_segment_hashing()


__all__ = [
    "extract_prompt_segments",
    "hash_segment",
    "hash_segments",
]
