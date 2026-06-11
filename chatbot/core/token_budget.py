"""Dynamic token budget allocation for prompt context sections."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


TokenCounter = Callable[[Any], int]

SECTION_NAMES: tuple[str, ...] = ("persona", "memory", "history", "scene")
DEFAULT_CONTEXT_WINDOW = 8192
DEFAULT_MIN_SECTION_TOKENS = 128
DEFAULT_OVERHEAD_TOKENS = 256

DEFAULT_SECTION_WEIGHTS: dict[str, float] = {
    "persona": 0.24,
    "memory": 0.30,
    "history": 0.32,
    "scene": 0.14,
}
DEFAULT_RELEVANCE: dict[str, float] = {
    "persona": 0.85,
    "memory": 0.75,
    "history": 0.80,
    "scene": 0.65,
}
DEFAULT_MAX_SHARES: dict[str, float] = {
    "persona": 0.35,
    "memory": 0.45,
    "history": 0.55,
    "scene": 0.30,
}


@dataclass(frozen=True, slots=True)
class SectionBudget:
    """Budget assigned to one prompt context section."""

    name: str
    budget_tokens: int
    estimated_tokens: int
    relevance: float
    overflow_tokens: int = 0

    @property
    def fits(self) -> bool:
        """Return whether the estimated section content fits this budget."""

        return self.overflow_tokens == 0


@dataclass(frozen=True, slots=True)
class TokenBudget:
    """Computed prompt context budget split by section."""

    context_window: int
    available_tokens: int
    reserved_response_tokens: int
    reserved_overhead_tokens: int
    persona: SectionBudget
    memory: SectionBudget
    history: SectionBudget
    scene: SectionBudget
    model: str | None = None
    provider: str | None = None

    @property
    def sections(self) -> dict[str, SectionBudget]:
        """Return section budgets keyed by canonical section name."""

        return {
            "persona": self.persona,
            "memory": self.memory,
            "history": self.history,
            "scene": self.scene,
        }

    @property
    def allocated_tokens(self) -> int:
        """Return the number of available tokens allocated to sections."""

        return sum(section.budget_tokens for section in self.sections.values())

    @property
    def overflow_tokens(self) -> int:
        """Return estimated content tokens that do not fit assigned budgets."""

        return sum(section.overflow_tokens for section in self.sections.values())

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable budget summary."""

        return {
            "context_window": self.context_window,
            "available_tokens": self.available_tokens,
            "allocated_tokens": self.allocated_tokens,
            "reserved_response_tokens": self.reserved_response_tokens,
            "reserved_overhead_tokens": self.reserved_overhead_tokens,
            "model": self.model,
            "provider": self.provider,
            "sections": {
                name: {
                    "budget_tokens": section.budget_tokens,
                    "estimated_tokens": section.estimated_tokens,
                    "relevance": section.relevance,
                    "overflow_tokens": section.overflow_tokens,
                    "fits": section.fits,
                }
                for name, section in self.sections.items()
            },
        }


class TokenBudgetCalculator:
    """Compute context section budgets from a model window and relevance scores."""

    def __init__(
        self,
        *,
        context_window: int | None = None,
        model: str | None = None,
        provider: str | None = None,
        reserved_response_tokens: int | None = None,
        reserved_overhead_tokens: int = DEFAULT_OVERHEAD_TOKENS,
        section_weights: Mapping[str, float] | None = None,
        max_section_shares: Mapping[str, float] | None = None,
        min_section_tokens: int = DEFAULT_MIN_SECTION_TOKENS,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.context_window = context_window
        self.model = model
        self.provider = provider
        self.reserved_response_tokens = reserved_response_tokens
        self.reserved_overhead_tokens = reserved_overhead_tokens
        self.section_weights = _normalize_section_values(
            section_weights,
            defaults=DEFAULT_SECTION_WEIGHTS,
            minimum=0.0,
            maximum=None,
        )
        self.max_section_shares = _normalize_section_values(
            max_section_shares,
            defaults=DEFAULT_MAX_SHARES,
            minimum=0.0,
            maximum=1.0,
        )
        if min_section_tokens < 0:
            raise ValueError("min_section_tokens must be non-negative")
        self.min_section_tokens = min_section_tokens
        self.token_counter = token_counter or estimate_tokens

    @classmethod
    def from_settings(
        cls,
        *,
        settings: Any | None = None,
        model: str | None = None,
        provider: str | None = None,
        **kwargs: Any,
    ) -> "TokenBudgetCalculator":
        """Build a calculator using model/provider defaults from ``chatbot.config``."""

        if settings is None:
            from chatbot.config import get_settings

            settings = get_settings()

        resolved_provider = provider or getattr(settings, "default_provider", None)
        resolved_model = model or getattr(settings, "default_model", None)
        context_window = resolve_context_window(
            model=resolved_model,
            provider=resolved_provider,
            settings=settings,
        )
        return cls(
            context_window=context_window,
            model=resolved_model,
            provider=resolved_provider,
            **kwargs,
        )

    def calculate(
        self,
        *,
        persona: Any = None,
        memory: Any = None,
        history: Any = None,
        scene: Any = None,
        relevance: Mapping[str, float] | None = None,
        context_window: int | None = None,
        model: str | None = None,
        provider: str | None = None,
        reserved_response_tokens: int | None = None,
        reserved_overhead_tokens: int | None = None,
    ) -> TokenBudget:
        """Return token budgets for persona, memory, history, and scene."""

        resolved_model = model if model is not None else self.model
        resolved_provider = provider if provider is not None else self.provider
        resolved_window = resolve_context_window(
            context_window=context_window if context_window is not None else self.context_window,
            model=resolved_model,
            provider=resolved_provider,
        )
        response_reserve = _resolve_response_reserve(
            resolved_window,
            reserved_response_tokens
            if reserved_response_tokens is not None
            else self.reserved_response_tokens,
        )
        overhead_reserve = (
            reserved_overhead_tokens
            if reserved_overhead_tokens is not None
            else self.reserved_overhead_tokens
        )
        if overhead_reserve < 0:
            raise ValueError("reserved_overhead_tokens must be non-negative")

        available = max(0, resolved_window - response_reserve - overhead_reserve)
        section_content = {
            "persona": persona,
            "memory": memory,
            "history": history,
            "scene": scene,
        }
        estimates = {
            name: max(0, self.token_counter(section_content[name])) for name in SECTION_NAMES
        }
        relevance_scores = _section_relevance(
            section_content,
            explicit_relevance=relevance,
        )
        allocations = _allocate_sections(
            available_tokens=available,
            estimates=estimates,
            relevance=relevance_scores,
            weights=self.section_weights,
            max_shares=self.max_section_shares,
            min_section_tokens=self.min_section_tokens,
        )
        sections = {
            name: SectionBudget(
                name=name,
                budget_tokens=allocations[name],
                estimated_tokens=estimates[name],
                relevance=relevance_scores[name],
                overflow_tokens=max(0, estimates[name] - allocations[name]),
            )
            for name in SECTION_NAMES
        }
        return TokenBudget(
            context_window=resolved_window,
            available_tokens=available,
            reserved_response_tokens=response_reserve,
            reserved_overhead_tokens=overhead_reserve,
            persona=sections["persona"],
            memory=sections["memory"],
            history=sections["history"],
            scene=sections["scene"],
            model=resolved_model,
            provider=resolved_provider,
        )


def calculate_token_budget(
    *,
    persona: Any = None,
    memory: Any = None,
    history: Any = None,
    scene: Any = None,
    relevance: Mapping[str, float] | None = None,
    context_window: int | None = None,
    model: str | None = None,
    provider: str | None = None,
    reserved_response_tokens: int | None = None,
    reserved_overhead_tokens: int = DEFAULT_OVERHEAD_TOKENS,
    token_counter: TokenCounter | None = None,
) -> TokenBudget:
    """Convenience wrapper around :class:`TokenBudgetCalculator`."""

    calculator = TokenBudgetCalculator(
        context_window=context_window,
        model=model,
        provider=provider,
        reserved_response_tokens=reserved_response_tokens,
        reserved_overhead_tokens=reserved_overhead_tokens,
        token_counter=token_counter,
    )
    return calculator.calculate(
        persona=persona,
        memory=memory,
        history=history,
        scene=scene,
        relevance=relevance,
    )


def resolve_context_window(
    *,
    context_window: int | None = None,
    model: str | None = None,
    provider: str | None = None,
    settings: Any | None = None,
) -> int:
    """Resolve the target context window from explicit input or app settings."""

    if context_window is not None:
        return _positive_int(context_window, "context_window")

    if settings is not None:
        for name in _context_window_setting_names(provider):
            value = getattr(settings, name, None)
            if value:
                return _positive_int(value, name)

    inferred = _infer_context_window_from_model(model)
    if inferred is not None:
        return inferred
    return DEFAULT_CONTEXT_WINDOW


def estimate_tokens(value: Any) -> int:
    """Estimate token count with a deterministic, dependency-free heuristic."""

    if value is None:
        return 0
    if isinstance(value, str):
        return _estimate_text_tokens(value)
    if isinstance(value, bytes):
        return _estimate_text_tokens(value.decode("utf-8", errors="ignore"))
    if isinstance(value, Mapping):
        return _estimate_mapping_tokens(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(estimate_tokens(item) for item in value)
    return _estimate_text_tokens(str(value))


def _allocate_sections(
    *,
    available_tokens: int,
    estimates: Mapping[str, int],
    relevance: Mapping[str, float],
    weights: Mapping[str, float],
    max_shares: Mapping[str, float],
    min_section_tokens: int,
) -> dict[str, int]:
    if available_tokens <= 0:
        return {name: 0 for name in SECTION_NAMES}

    active_sections = [
        name
        for name in SECTION_NAMES
        if estimates.get(name, 0) > 0 or relevance.get(name, DEFAULT_RELEVANCE[name]) > 0
    ]
    if not active_sections:
        active_sections = list(SECTION_NAMES)

    scores = {
        name: max(0.0, weights[name]) * (0.35 + relevance[name])
        for name in active_sections
    }
    score_total = sum(scores.values()) or 1.0
    allocations = {
        name: int(available_tokens * (scores.get(name, 0.0) / score_total))
        for name in SECTION_NAMES
    }
    _distribute_rounding_remainder(allocations, available_tokens, scores)

    floors = {
        name: min(min_section_tokens, available_tokens)
        for name in active_sections
        if estimates.get(name, 0) > 0
    }
    _apply_floors(allocations, floors)
    _apply_max_shares(
        allocations,
        max_tokens={
            name: max(
                floors.get(name, 0),
                int(available_tokens * max_shares[name]),
            )
            for name in SECTION_NAMES
        },
    )
    _rebalance_to_total(
        allocations,
        target_total=available_tokens,
        scores=scores,
        estimates=estimates,
        max_tokens={
            name: max(
                floors.get(name, 0),
                int(available_tokens * max_shares[name]),
            )
            for name in SECTION_NAMES
        },
    )
    return allocations


def _section_relevance(
    content: Mapping[str, Any],
    *,
    explicit_relevance: Mapping[str, float] | None,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for name in SECTION_NAMES:
        explicit = explicit_relevance.get(name) if explicit_relevance else None
        if explicit is not None:
            scores[name] = _clamp(float(explicit), 0.0, 1.0)
            continue

        inferred = _infer_relevance(content.get(name))
        if inferred is not None:
            scores[name] = inferred
            continue
        scores[name] = DEFAULT_RELEVANCE[name]
    return scores


def _infer_relevance(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        for key in ("relevance", "relevance_score", "score", "confidence"):
            if key in value:
                return _coerce_relevance(value[key])
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        scores = [_infer_relevance(item) for item in value]
        numeric_scores = [score for score in scores if score is not None]
        if numeric_scores:
            return sum(numeric_scores) / len(numeric_scores)
    return None


def _coerce_relevance(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score > 1.0:
        score = score / 100.0
    return _clamp(score, 0.0, 1.0)


def _normalize_section_values(
    values: Mapping[str, float] | None,
    *,
    defaults: Mapping[str, float],
    minimum: float,
    maximum: float | None,
) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for name in SECTION_NAMES:
        value = defaults[name]
        if values is not None and name in values:
            value = float(values[name])
        if maximum is None:
            normalized[name] = max(minimum, value)
        else:
            normalized[name] = _clamp(value, minimum, maximum)
    return normalized


def _resolve_response_reserve(
    context_window: int,
    reserved_response_tokens: int | None,
) -> int:
    if reserved_response_tokens is not None:
        return _positive_int(reserved_response_tokens, "reserved_response_tokens", allow_zero=True)
    return max(256, min(2048, context_window // 8))


def _context_window_setting_names(provider: str | None) -> tuple[str, ...]:
    names = [
        "context_window",
        "model_context_window",
        "default_context_window",
        "max_context_tokens",
    ]
    provider_name = str(provider or "").strip().lower()
    if provider_name:
        names = [
            f"{provider_name}_context_window",
            f"{provider_name}_num_ctx",
            f"{provider_name}_max_context_tokens",
            *names,
        ]
    return tuple(names)


def _infer_context_window_from_model(model: str | None) -> int | None:
    model_name = str(model or "").strip().lower()
    if not model_name:
        return None
    if "1m" in model_name or "1-million" in model_name:
        return 1_000_000
    if "128k" in model_name or "128000" in model_name:
        return 128_000
    if "64k" in model_name or "65536" in model_name:
        return 65_536
    if "32k" in model_name or "32768" in model_name:
        return 32_768
    if "16k" in model_name or "16384" in model_name:
        return 16_384
    if "8k" in model_name or "8192" in model_name:
        return 8192
    return None


def _estimate_mapping_tokens(value: Mapping[Any, Any]) -> int:
    preferred_keys = ("content", "text", "summary", "prompt", "message")
    for key in preferred_keys:
        if key in value:
            return estimate_tokens(value[key])

    total = 0
    for key, item in value.items():
        if key in {"metadata", "embedding", "vector"}:
            continue
        total += estimate_tokens(item)
    return total


def _estimate_text_tokens(text: str) -> int:
    compact = " ".join(text.split())
    if not compact:
        return 0
    return max(1, (len(compact) + 3) // 4)


def _distribute_rounding_remainder(
    allocations: dict[str, int],
    target_total: int,
    scores: Mapping[str, float],
) -> None:
    remainder = target_total - sum(allocations.values())
    if remainder <= 0:
        return
    ordered = sorted(SECTION_NAMES, key=lambda name: (-scores.get(name, 0.0), name))
    for name in ordered:
        if remainder <= 0:
            return
        allocations[name] += 1
        remainder -= 1


def _apply_floors(allocations: dict[str, int], floors: Mapping[str, int]) -> None:
    deficit = 0
    for name, floor in floors.items():
        if allocations[name] < floor:
            deficit += floor - allocations[name]
            allocations[name] = floor
    if deficit <= 0:
        return

    donors = sorted(
        (name for name in SECTION_NAMES if name not in floors),
        key=lambda name: allocations[name],
        reverse=True,
    )
    for name in donors:
        take = min(deficit, allocations[name])
        allocations[name] -= take
        deficit -= take
        if deficit <= 0:
            return


def _apply_max_shares(allocations: dict[str, int], max_tokens: Mapping[str, int]) -> None:
    for name in SECTION_NAMES:
        allocations[name] = min(allocations[name], max_tokens[name])


def _rebalance_to_total(
    allocations: dict[str, int],
    *,
    target_total: int,
    scores: Mapping[str, float],
    estimates: Mapping[str, int],
    max_tokens: Mapping[str, int],
) -> None:
    while sum(allocations.values()) < target_total:
        candidates = [
            name for name in SECTION_NAMES if allocations[name] < max_tokens[name]
        ]
        if not candidates:
            return
        candidates.sort(
            key=lambda section: (
                estimates.get(section, 0) - allocations[section],
                scores.get(section, 0.0),
                section,
            ),
            reverse=True,
        )
        remaining = target_total - sum(allocations.values())
        for name in candidates:
            if remaining <= 0:
                break
            room = max_tokens[name] - allocations[name]
            give = min(remaining, max(1, room))
            allocations[name] += give
            remaining -= give

    while sum(allocations.values()) > target_total:
        candidates = [name for name in SECTION_NAMES if allocations[name] > 0]
        if not candidates:
            return
        candidates.sort(
            key=lambda section: (
                estimates.get(section, 0) - allocations[section],
                scores.get(section, 0.0),
                section,
            ),
        )
        excess = sum(allocations.values()) - target_total
        for name in candidates:
            if excess <= 0:
                break
            take = min(excess, allocations[name])
            allocations[name] -= take
            excess -= take


def _positive_int(value: Any, name: str, *, allow_zero: bool = False) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if allow_zero and number == 0:
        return number
    if number <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return number


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_OVERHEAD_TOKENS",
    "DEFAULT_RELEVANCE",
    "DEFAULT_SECTION_WEIGHTS",
    "SECTION_NAMES",
    "SectionBudget",
    "TokenBudget",
    "TokenBudgetCalculator",
    "TokenCounter",
    "calculate_token_budget",
    "estimate_tokens",
    "resolve_context_window",
]
