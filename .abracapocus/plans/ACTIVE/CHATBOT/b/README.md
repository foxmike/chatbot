# b

## Goal

Backend provider abstraction. A single Provider interface, an OpenAI-compatible adapter that serves OpenRouter, local vLLM, and Vast.ai-stood-up instances (base_url + key differ, contract identical), a Modal ephemeral wrapper handling cold-start/teardown, and a policy router that picks a provider per request. No first-party orchestration framework.

## Notes

- Async: base.py (the Provider interface) is wave 1. openai_compat.py, modal_ephemeral.py, and router.py run in parallel in wave 2 on disjoint files, each depending only on base.py. tests/test_providers.py is the wave-3 fan-in.
- Risk: Provider interface is a public contract consumed by phase g. Lock its shape early; changing it later ripples. Validate any concrete OpenRouter model strings before execution per the backend/model rules.
