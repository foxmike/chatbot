from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from chatbot.providers.base import Provider, ProviderConfig, ProviderRegistry


logger = logging.getLogger(__name__)

PolicyConfig = Mapping[str, Any]
ProviderCollection = Mapping[str, Provider] | Sequence[Any] | ProviderRegistry

_DEFAULT_PROVIDER_ID = "openrouter"
_DEFAULT_MODEL_ID = "openrouter/deepseek/deepseek-v4-pro"

_DEFAULT_TASK_CLASS_MODEL_IDS: Mapping[str, str] = {
    "casual": "google/gemma-4-31b-it",
    "emotional": "openrouter/deepseek/deepseek-v4-pro",
    "summarize": "openrouter/deepseek/deepseek-v3.2",
    "extract": "openrouter/qwen/qwen3-coder-next",
    "embed": "openrouter/z-ai/glm-4.7-flash",
}

_KNOWN_MODEL_REGISTRY: Mapping[str, frozenset[str]] = {
    "openrouter": frozenset(
        {
            "openrouter/deepseek/deepseek-v4-pro",
            "google/gemma-4-31b-it",
            "openrouter/google/gemma-4-31b-it",
            "openrouter/qwen/qwen3-coder-next",
            "openrouter/qwen/qwen3-235b-a22b-thinking-2507",
            "openrouter/deepseek/deepseek-v3.2",
            "openrouter/z-ai/glm-4.7-flash",
            "openrouter/phind/codellama-34b-v2",
            "openrouter/meta-llama/codellama-34b-instruct",
            "openrouter/mistralai/mistral-openorca-7b",
            "openrouter/teknium/openhermes-2-mistral-7b",
            "openrouter/nousresearch/hermes-70b",
            "openrouter/nousresearch/hermes-13b",
            "openrouter/xwin/xwin-70b",
            "openrouter/migtissera/synthia-70b",
            "openrouter/huggingfaceh4/zephyr-7b",
            "openrouter/meta-llama/llama-2-13b-chat",
            "openrouter/meta-llama/llama-2-70b-chat",
            "openrouter/jondurbin/airoboros-70b",
            "openrouter/pygmalionai/mythalion-13b",
        }
    ),
}


class TaskClass(str, Enum):
    CASUAL = "casual"
    EMOTIONAL = "emotional"
    SUMMARIZE = "summarize"
    EXTRACT = "extract"
    EMBED = "embed"


def _default_task_class_map() -> dict[str, tuple[str, str]]:
    return {
        task_class.value: (
            _DEFAULT_PROVIDER_ID,
            _DEFAULT_TASK_CLASS_MODEL_IDS.get(task_class.value, _DEFAULT_MODEL_ID),
        )
        for task_class in TaskClass
    }


@dataclass(slots=True)
class TaskClassRoutingConfig:
    enabled: bool = False
    class_map: dict[str, tuple[str, str]] = field(default_factory=_default_task_class_map)
    default_provider_id: str = _DEFAULT_PROVIDER_ID
    default_model_id: str = _DEFAULT_MODEL_ID


@dataclass(frozen=True, slots=True)
class NamedProvider:
    """Provider instance paired with its registry name."""

    name: str
    provider: Provider


class ProviderRouter:
    """Policy-driven provider selector.

    The router only chooses a provider. It does not call providers, retain request
    state, or apply orchestration-level retry behavior.
    """

    _RULE_ORDER = ("model_prefix", "cost_tier", "latency_tier")

    def __init__(self, providers: ProviderCollection, policy: PolicyConfig | None = None) -> None:
        self._providers = _coerce_providers(providers)
        self._policy = dict(policy or {})

    def select(self, prompt: str, config: ProviderConfig | Mapping[str, Any] | None = None) -> Provider:
        """Return the first available provider selected by the routing policy."""

        del prompt

        candidates = self._candidate_names(config)
        excluded = _excluded_provider_names(config)

        missing: list[str] = []
        unavailable: list[str] = []
        for name in candidates:
            provider = self._providers.get(name)
            if provider is None:
                missing.append(name)
                continue
            if name in excluded or not _provider_is_available(provider):
                unavailable.append(name)
                continue
            return provider

        if candidates:
            details = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if unavailable:
                details.append(f"unavailable: {', '.join(unavailable)}")
            suffix = f" ({'; '.join(details)})" if details else ""
            raise LookupError(f"no available provider matched routing policy{suffix}")

        raise LookupError("routing policy did not produce any provider candidates")

    def _candidate_names(self, config: ProviderConfig | Mapping[str, Any] | None) -> tuple[str, ...]:
        candidates: list[str] = []

        for rule_name in self._RULE_ORDER:
            rule_candidates = self._match_rule(rule_name, config)
            if rule_candidates:
                candidates.extend(rule_candidates)
                break

        candidates.extend(_provider_names_from_value(self._policy.get("default")))
        candidates.extend(_provider_names_from_value(self._policy.get("fallback")))

        if not candidates:
            candidates.extend(self._providers)

        return _dedupe(candidates)

    def _match_rule(
        self,
        rule_name: str,
        config: ProviderConfig | Mapping[str, Any] | None,
    ) -> tuple[str, ...]:
        rule = self._policy.get(rule_name)
        if not isinstance(rule, Mapping):
            return ()

        if rule_name == "model_prefix":
            model = _config_value(config, "model")
            if not isinstance(model, str):
                return ()
            for prefix, value in rule.items():
                if isinstance(prefix, str) and model.startswith(prefix):
                    return _provider_names_from_value(value)
            return ()

        tier = _config_value(config, rule_name)
        if tier is None:
            return ()
        return _provider_names_from_value(rule.get(str(tier)))


PolicyProviderRouter = ProviderRouter


def select(
    prompt: str,
    config: ProviderConfig | Mapping[str, Any] | None,
    *,
    providers: ProviderCollection,
    policy: PolicyConfig | None = None,
) -> Provider:
    """Select a provider without explicitly constructing a router object."""

    return ProviderRouter(providers, policy).select(prompt, config)


def select_model_for_task(
    task_class: str,
    routing_config: TaskClassRoutingConfig | None = None,
) -> tuple[str, str]:
    """Select a validated provider/model pair for a task class."""

    if routing_config is None:
        routing_config = task_class_routing_config_from_settings()

    if not routing_config.enabled:
        return _validated_or_default(
            routing_config.default_provider_id,
            routing_config.default_model_id,
        )

    normalized_task_class = _normalize_task_class(task_class)
    provider_id, model_id = routing_config.class_map.get(
        normalized_task_class,
        (routing_config.default_provider_id, routing_config.default_model_id),
    )
    return _validated_or_default(
        provider_id,
        model_id,
        fallback_provider_id=routing_config.default_provider_id,
        fallback_model_id=routing_config.default_model_id,
    )


def task_class_routing_config_from_settings(settings: Any | None = None) -> TaskClassRoutingConfig:
    """Build task-class routing config from application settings."""

    if settings is None:
        settings = _load_settings()

    if settings is None:
        return TaskClassRoutingConfig()

    default_provider_id = (
        _settings_text(settings, "task_class_default_provider")
        or _settings_text(settings, "default_provider")
        or _DEFAULT_PROVIDER_ID
    )
    default_model_id = (
        _settings_text(settings, "task_class_default_model")
        or _settings_text(settings, "default_model")
        or _DEFAULT_MODEL_ID
    )

    configured_routes = _settings_routes(settings)
    class_map: dict[str, tuple[str, str]] = {}
    for task_class in TaskClass:
        fallback_model_id = _DEFAULT_TASK_CLASS_MODEL_IDS.get(task_class.value, default_model_id)
        fallback_route = (default_provider_id, fallback_model_id)
        class_map[task_class.value] = _coerce_route_value(
            configured_routes.get(task_class.value),
            fallback_route,
        )

    return TaskClassRoutingConfig(
        enabled=_settings_bool(settings, "task_class_routing_enabled", default=True),
        class_map=class_map,
        default_provider_id=default_provider_id,
        default_model_id=default_model_id,
    )


def validate_identifier(provider_id: str, model_id: str) -> bool:
    """Return whether a provider/model pair is known to the router."""

    if not provider_id or not model_id:
        return False
    known_models = _KNOWN_MODEL_REGISTRY.get(provider_id)
    if known_models is None:
        return False
    return model_id in known_models


def _coerce_providers(providers: ProviderCollection) -> dict[str, Provider]:
    if isinstance(providers, ProviderRegistry):
        return {name: providers.get(name) for name in providers.names()}

    if isinstance(providers, Mapping):
        return {_validate_provider_name(name): _validate_provider(provider) for name, provider in providers.items()}

    coerced: dict[str, Provider] = {}
    for item in providers:
        name, provider = _coerce_named_provider(item)
        name = _validate_provider_name(name)
        if name in coerced:
            raise ValueError(f"provider already registered: {name}")
        coerced[name] = _validate_provider(provider)
    return coerced


def _coerce_named_provider(item: Any) -> tuple[str, Provider]:
    if isinstance(item, NamedProvider):
        return item.name, item.provider

    if isinstance(item, Mapping):
        return item.get("name"), item.get("provider")

    if isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) == 2:
        name, provider = item
        return name, provider

    name = getattr(item, "name", None)
    provider = getattr(item, "provider", None)
    return name, provider


def _validate_provider_name(name: Any) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("provider name must not be empty")
    return name


def _validate_provider(provider: Any) -> Provider:
    if not isinstance(provider, Provider):
        raise TypeError("provider must implement Provider")
    return provider


def _provider_names_from_value(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()

    if isinstance(value, str):
        return (value,)

    if isinstance(value, Mapping):
        names: list[str] = []
        for key in ("provider", "primary", "name"):
            names.extend(_provider_names_from_value(value.get(key)))
        for key in ("providers", "fallback", "fallbacks"):
            names.extend(_provider_names_from_value(value.get(key)))
        return _dedupe(names)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        names = []
        for item in value:
            names.extend(_provider_names_from_value(item))
        return _dedupe(names)

    return ()


def _config_value(config: ProviderConfig | Mapping[str, Any] | None, key: str) -> Any:
    if config is None:
        return None

    if isinstance(config, Mapping):
        if key in config:
            return config[key]
        extra = config.get("extra")
        if isinstance(extra, Mapping):
            return extra.get(key)
        return None

    if key == "model":
        return config.model
    return config.extra.get(key)


def _excluded_provider_names(config: ProviderConfig | Mapping[str, Any] | None) -> set[str]:
    names: set[str] = set()
    for key in (
        "exclude_providers",
        "excluded_providers",
        "failed_providers",
        "unavailable_providers",
        "skip_providers",
    ):
        names.update(_provider_names_from_value(_config_value(config, key)))
    return names


def _provider_is_available(provider: Provider) -> bool:
    for attr in ("available", "enabled"):
        value = getattr(provider, attr, None)
        if isinstance(value, bool) and not value:
            return False

    for attr in ("unavailable", "disabled", "failed"):
        value = getattr(provider, attr, None)
        if isinstance(value, bool) and value:
            return False

    is_available = getattr(provider, "is_available", None)
    if callable(is_available):
        try:
            return bool(is_available())
        except Exception:
            return False

    return True


def _normalize_task_class(task_class: str) -> str:
    if not isinstance(task_class, str):
        return ""
    return task_class.strip().lower()


def _load_settings() -> Any | None:
    try:
        from chatbot.config import get_settings
    except ImportError:
        return None

    try:
        return get_settings()
    except Exception:
        logger.warning("Unable to load settings for task-class routing", exc_info=True)
        return None


def _settings_routes(settings: Any) -> Mapping[str, Any]:
    task_class_routes = getattr(settings, "task_class_routes", None)
    if callable(task_class_routes):
        routes = task_class_routes()
        if isinstance(routes, Mapping):
            return routes

    routes = getattr(settings, "task_class_model_routes", None)
    if isinstance(routes, Mapping):
        return routes

    return {}


def _coerce_route_value(value: Any, fallback: tuple[str, str]) -> tuple[str, str]:
    fallback_provider_id, fallback_model_id = fallback

    if isinstance(value, Mapping):
        provider_id = _text_or_default(
            value.get("provider_id") or value.get("provider") or value.get("name"),
            fallback_provider_id,
        )
        model_id = _text_or_default(value.get("model_id") or value.get("model"), fallback_model_id)
        return provider_id, model_id

    if isinstance(value, str):
        return fallback_provider_id, value

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        provider_id, model_id = value
        return (
            _text_or_default(provider_id, fallback_provider_id),
            _text_or_default(model_id, fallback_model_id),
        )

    return fallback


def _settings_text(settings: Any, name: str) -> str:
    return _text_or_default(getattr(settings, name, None), "")


def _text_or_default(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _settings_bool(settings: Any, name: str, *, default: bool) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _validated_or_default(
    provider_id: str,
    model_id: str,
    *,
    fallback_provider_id: str = _DEFAULT_PROVIDER_ID,
    fallback_model_id: str = _DEFAULT_MODEL_ID,
) -> tuple[str, str]:
    if validate_identifier(provider_id, model_id):
        return provider_id, model_id

    logger.error(
        "Unknown provider/model route %s/%s; falling back to %s/%s",
        provider_id,
        model_id,
        fallback_provider_id,
        fallback_model_id,
    )
    if validate_identifier(fallback_provider_id, fallback_model_id):
        return fallback_provider_id, fallback_model_id

    logger.error(
        "Unknown fallback provider/model route %s/%s; falling back to %s/%s",
        fallback_provider_id,
        fallback_model_id,
        _DEFAULT_PROVIDER_ID,
        _DEFAULT_MODEL_ID,
    )
    return _DEFAULT_PROVIDER_ID, _DEFAULT_MODEL_ID


def _dedupe(names: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for name in names:
        if not isinstance(name, str) or name in seen:
            continue
        seen.add(name)
        deduped.append(name)
    return tuple(deduped)


__all__ = [
    "NamedProvider",
    "PolicyProviderRouter",
    "ProviderRouter",
    "TaskClass",
    "TaskClassRoutingConfig",
    "select",
    "select_model_for_task",
    "task_class_routing_config_from_settings",
    "validate_identifier",
]
