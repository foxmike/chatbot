from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from chatbot.providers.base import Provider, ProviderConfig, ProviderRegistry


PolicyConfig = Mapping[str, Any]
ProviderCollection = Mapping[str, Provider] | Sequence[Any] | ProviderRegistry


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
    "select",
]
