from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from chatbot.providers.base import (
    Provider,
    ProviderAuthError,
    ProviderConfig,
    ProviderError,
    ProviderResponse,
    ProviderTimeoutError,
)


@dataclass(frozen=True, slots=True)
class ModalVolumeMount:
    """Named Modal volume mounted at a container path."""

    mount_path: str
    volume_name: str
    create_if_missing: bool = False


class ModalEphemeralProvider(Provider):
    """Provider adapter for completion calls served by an ephemeral Modal function."""

    def __init__(
        self,
        app_name: str | None = None,
        function_name: str | None = None,
        *,
        function_ref: Any | None = None,
        environment_name: str | None = None,
        default_model: str = "",
        timeout_seconds: float = 60.0,
        cold_start_timeout_seconds: float = 300.0,
        readiness_payload: Mapping[str, Any] | None = None,
        volumes: Mapping[str, Any] | None = None,
        volume_mounts: Mapping[str, str] | Sequence[ModalVolumeMount] | None = None,
        modal_module: Any | None = None,
    ) -> None:
        if function_ref is None:
            if not app_name or not app_name.strip():
                raise ValueError("app_name must not be empty when function_ref is not supplied")
            if not function_name or not function_name.strip():
                raise ValueError("function_name must not be empty when function_ref is not supplied")
        if volumes is not None and volume_mounts is not None:
            raise ValueError("volumes and volume_mounts are mutually exclusive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if cold_start_timeout_seconds <= 0:
            raise ValueError("cold_start_timeout_seconds must be positive")

        self.app_name = app_name.strip() if app_name else None
        self.function_name = function_name.strip() if function_name else None
        self.function_ref = function_ref
        self.environment_name = environment_name
        self.default_model = default_model or ""
        self.timeout_seconds = timeout_seconds
        self.cold_start_timeout_seconds = cold_start_timeout_seconds
        self.readiness_payload = dict(readiness_payload) if readiness_payload is not None else None
        self.volumes = dict(volumes) if volumes is not None else None
        self.volume_mounts = volume_mounts
        self._modal_module = modal_module
        self._function: Any | None = None
        self._active_call: Any | None = None
        self._ready = False
        self._startup_lock = asyncio.Lock()

    async def complete(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        config: ProviderConfig,
    ) -> ProviderResponse:
        model = self._resolve_model(config)
        payload = self._build_payload(prompt, history, config, model)
        function = await self._ensure_function()

        cold_started = await self._ensure_ready(function)

        timeout = config.timeout_seconds or self.timeout_seconds
        if cold_started and self.readiness_payload is None:
            timeout = self.cold_start_timeout_seconds
        raw = await self._spawn_and_get(function, payload, timeout, "Modal provider request")
        return self._to_provider_response(raw)

    async def close(self) -> None:
        """Stop the active ephemeral Modal call/container when the SDK handle supports it."""

        call = self._active_call
        function = self._function
        self._active_call = None
        self._function = None
        self._ready = False

        for target in (call, function):
            if target is None:
                continue
            stopper = self._find_stop_method(target)
            if stopper is None:
                continue
            try:
                await asyncio.to_thread(stopper)
            except Exception as exc:
                raise self._map_modal_exception("Modal provider teardown failed", exc) from exc
            break

    async def aclose(self) -> None:
        await self.close()

    async def __aenter__(self) -> ModalEphemeralProvider:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    async def _ensure_function(self) -> Any:
        if self._function is not None:
            return self._function

        async with self._startup_lock:
            if self._function is not None:
                return self._function

            try:
                function = await asyncio.wait_for(
                    asyncio.to_thread(self._resolve_function),
                    timeout=self.cold_start_timeout_seconds,
                )
            except TimeoutError as exc:
                raise ProviderTimeoutError("Modal provider cold-start timed out") from exc
            except Exception as exc:
                raise self._map_modal_exception("Modal provider lookup failed", exc) from exc

            self._function = function
            return function

    async def _ensure_ready(self, function: Any) -> bool:
        if self._ready:
            return False

        async with self._startup_lock:
            if self._ready:
                return False
            if self.readiness_payload is not None:
                await self._spawn_and_get(
                    function,
                    dict(self.readiness_payload),
                    self.cold_start_timeout_seconds,
                    "Modal provider cold-start",
                )
            self._ready = True
            return True

    def _resolve_function(self) -> Any:
        function = self._coerce_function_ref()
        volumes = self._resolve_volumes()
        if volumes:
            function = function.with_options(volumes=volumes)

        hydrate = getattr(function, "hydrate", None)
        if callable(hydrate):
            hydrated = hydrate()
            if hydrated is not None:
                function = hydrated
        return function

    def _coerce_function_ref(self) -> Any:
        if self.function_ref is not None and not isinstance(self.function_ref, str):
            return self.function_ref

        modal_module = self._get_modal_module()
        if self.function_ref is not None:
            app_name, function_name = self._parse_function_ref(self.function_ref)
        else:
            app_name = self.app_name
            function_name = self.function_name

        if app_name is None or function_name is None:
            raise ValueError("Modal function reference must include app and function names")

        kwargs: dict[str, Any] = {}
        if self.environment_name is not None:
            kwargs["environment_name"] = self.environment_name
        return modal_module.Function.from_name(app_name, function_name, **kwargs)

    def _parse_function_ref(self, function_ref: str) -> tuple[str, str]:
        value = function_ref.strip()
        if not value:
            raise ValueError("function_ref must not be empty")
        for separator in ("::", ":", "."):
            if separator in value:
                app_name, function_name = value.split(separator, 1)
                app_name = app_name.strip()
                function_name = function_name.strip()
                if app_name and function_name:
                    return app_name, function_name
        raise ValueError("function_ref must be formatted as 'app_name:function_name'")

    def _resolve_volumes(self) -> dict[str, Any]:
        if self.volumes is not None:
            return dict(self.volumes)
        if self.volume_mounts is None:
            return {}

        modal_module = self._get_modal_module()
        resolved: dict[str, Any] = {}
        if isinstance(self.volume_mounts, Mapping):
            mounts = [
                ModalVolumeMount(mount_path=mount_path, volume_name=volume_name)
                for mount_path, volume_name in self.volume_mounts.items()
            ]
        else:
            mounts = list(self.volume_mounts)

        for mount in mounts:
            if not mount.mount_path or not mount.mount_path.strip():
                raise ValueError("Modal volume mount path must not be empty")
            if not mount.volume_name or not mount.volume_name.strip():
                raise ValueError("Modal volume name must not be empty")
            resolved[mount.mount_path] = modal_module.Volume.from_name(
                mount.volume_name,
                create_if_missing=mount.create_if_missing,
            )
        return resolved

    async def _spawn_and_get(
        self,
        function: Any,
        payload: Mapping[str, Any],
        timeout: float,
        operation: str,
    ) -> Any:
        try:
            call = await asyncio.wait_for(
                asyncio.to_thread(function.spawn, dict(payload)),
                timeout=timeout,
            )
            self._active_call = call
            return await asyncio.wait_for(
                asyncio.to_thread(call.get, timeout=timeout),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise ProviderTimeoutError(f"{operation} timed out") from exc
        except Exception as exc:
            raise self._map_modal_exception(f"{operation} failed", exc) from exc
        finally:
            self._active_call = None

    def _resolve_model(self, config: ProviderConfig) -> str:
        model = config.model.strip() if config.model else self.default_model.strip()
        if not model:
            raise ValueError("model must not be empty")
        return model

    def _build_payload(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        config: ProviderConfig,
        model: str,
    ) -> dict[str, Any]:
        messages = [dict(message) for message in history]
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if config.max_tokens is not None:
            payload["max_tokens"] = config.max_tokens
        if config.top_p is not None:
            payload["top_p"] = config.top_p
        if config.stop is not None:
            payload["stop"] = list(config.stop)

        for key, value in config.extra.items():
            if key not in payload:
                payload[key] = value
        return payload

    def _to_provider_response(self, raw: Any) -> ProviderResponse:
        if isinstance(raw, ProviderResponse):
            return raw
        if isinstance(raw, str):
            return ProviderResponse(
                text=raw,
                finish_reason="",
                model=None,
                raw={"text": raw},
            )
        if not isinstance(raw, Mapping):
            raise ProviderError("Modal provider returned an unexpected response shape")

        direct_text = raw.get("text")
        if isinstance(direct_text, str):
            finish_reason = raw.get("finish_reason")
            model = raw.get("model")
            return ProviderResponse(
                text=direct_text,
                finish_reason=finish_reason if isinstance(finish_reason, str) else "",
                model=model if isinstance(model, str) else None,
                raw=raw,
            )

        choices = raw.get("choices")
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
            raise ProviderError("Modal provider response missing choices")

        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise ProviderError("Modal provider response choice has an unexpected shape")

        message = choice.get("message")
        if isinstance(message, Mapping):
            content = message.get("content")
        else:
            content = choice.get("text")

        text = content if isinstance(content, str) else "" if content is None else json.dumps(content)
        finish_reason = choice.get("finish_reason")
        model = raw.get("model")

        return ProviderResponse(
            text=text,
            finish_reason=finish_reason if isinstance(finish_reason, str) else "",
            model=model if isinstance(model, str) else None,
            raw=raw,
        )

    def _find_stop_method(self, target: Any) -> Any | None:
        cancel = getattr(target, "cancel", None)
        if callable(cancel):
            return lambda: cancel(terminate_containers=True)
        for method_name in ("stop", "terminate", "detach"):
            method = getattr(target, method_name, None)
            if callable(method):
                return method
        return None

    def _get_modal_module(self) -> Any:
        if self._modal_module is not None:
            return self._modal_module
        try:
            self._modal_module = import_module("modal")
        except ImportError as exc:
            raise ProviderAuthError("Modal SDK is not installed") from exc
        return self._modal_module

    def _map_modal_exception(self, prefix: str, exc: Exception) -> ProviderError:
        if isinstance(exc, ProviderError):
            return exc
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return ProviderTimeoutError(f"{prefix}: {exc}".strip())
        if isinstance(exc, (ConnectionError, OSError)):
            return ProviderAuthError(f"{prefix}: {exc}".strip())

        modal_module = self._modal_module
        exception_module = getattr(modal_module, "exception", None) if modal_module else None
        auth_error = getattr(exception_module, "AuthError", None)
        function_timeout_error = getattr(exception_module, "FunctionTimeoutError", None)
        modal_timeout_error = getattr(exception_module, "TimeoutError", None)
        modal_error = getattr(modal_module, "Error", None) if modal_module else None

        if auth_error is not None and isinstance(exc, auth_error):
            return ProviderAuthError(f"{prefix}: {exc}".strip())
        timeout_types = tuple(
            error_type
            for error_type in (function_timeout_error, modal_timeout_error)
            if error_type is not None
        )
        if timeout_types and isinstance(exc, timeout_types):
            return ProviderTimeoutError(f"{prefix}: {exc}".strip())

        name = exc.__class__.__name__.lower()
        if any(token in name for token in ("auth", "permission", "credential", "connection")):
            return ProviderAuthError(f"{prefix}: {exc}".strip())
        if "timeout" in name:
            return ProviderTimeoutError(f"{prefix}: {exc}".strip())
        if modal_error is not None and isinstance(exc, modal_error):
            return ProviderError(f"{prefix}: {exc}".strip())
        return ProviderError(f"{prefix}: {exc}".strip())


__all__ = ["ModalEphemeralProvider", "ModalVolumeMount"]
