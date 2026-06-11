"""Prompt compression module for small-context models.

This module provides utilities to prioritize, prune, and summarize prompt context
sections (persona, history, memory, scene) to fit within constrained token windows.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from chatbot.core.steps import TurnContext, TurnStep
from chatbot.core.token_budget import TokenBudgetCalculator, estimate_tokens
from chatbot.providers.base import Provider, ProviderConfig

logger = logging.getLogger(__name__)


class PromptCompressor:
    """Compresses prompt context sections to fit within constrained token windows.

    Supports heuristic/extractive pruning and optional LLM-based summarization
    for persona prompts, conversation history, and retrieved memories.
    """

    def __init__(
        self,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
        provider_name: str | None = None,
        llm_provider: Provider | None = None,
        token_counter: Callable[[Any], int] | None = None,
        reserved_response_tokens: int | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.model = model
        self.provider_name = provider_name
        self.llm_provider = llm_provider
        self.token_counter = token_counter or estimate_tokens
        self.reserved_response_tokens = reserved_response_tokens

    async def compress_context(
        self,
        ctx: TurnContext,
        *,
        max_tokens: int | None = None,
    ) -> TurnContext:
        """Compress a TurnContext's prompt sections to fit the target window."""
        # 1. Resolve max tokens (context window)
        target_max = max_tokens or self.max_tokens
        if target_max is None:
            try:
                from chatbot.core.token_budget import resolve_context_window

                target_max = resolve_context_window(
                    model=self.model
                    or ctx.metadata.get("model")
                    or ctx.metadata.get("default_model"),
                    provider=self.provider_name
                    or ctx.metadata.get("provider_name")
                    or ctx.metadata.get("default_provider"),
                )
            except Exception:
                target_max = 2048  # Default conservative fallback

        # 2. Extract components
        persona_prompt = ctx.metadata.get("persona_prompt") or ctx.compiled_context or ""
        if not ctx.metadata.get("persona_prompt") and ctx.compiled_context:
            parts = ctx.compiled_context.split("\n\nRetrieved memories:")
            if len(parts) > 1:
                persona_prompt = parts[0]
            else:
                parts = ctx.compiled_context.split("\n\nConversation history:")
                persona_prompt = parts[0]

        history = ctx.history or []
        retrieved_memories = ctx.retrieved_memories or []
        scene_prompt = ctx.metadata.get("roleplay_scene_prompt") or ""

        # 3. Calculate budgets for each section
        calculator = TokenBudgetCalculator(
            context_window=target_max,
            model=self.model or ctx.metadata.get("model"),
            provider=self.provider_name or ctx.metadata.get("provider_name"),
            reserved_response_tokens=self.reserved_response_tokens,
            token_counter=self.token_counter,
        )
        budget = calculator.calculate(
            persona=persona_prompt,
            memory=retrieved_memories,
            history=history,
            scene=scene_prompt,
        )

        logger.info(
            "Prompt compression initiated. Target: %d tokens. Estimates: "
            "persona=%d/%d, memory=%d/%d, history=%d/%d, scene=%d/%d",
            target_max,
            budget.persona.estimated_tokens,
            budget.persona.budget_tokens,
            budget.memory.estimated_tokens,
            budget.memory.budget_tokens,
            budget.history.estimated_tokens,
            budget.history.budget_tokens,
            budget.scene.estimated_tokens,
            budget.scene.budget_tokens,
        )

        # 4. Compress each section using calculated budgets
        compressed_persona = persona_prompt
        if not budget.persona.fits:
            compressed_persona = await self.compress_persona(
                persona_prompt,
                budget.persona.budget_tokens,
                ctx=ctx,
            )

        compressed_memories = retrieved_memories
        if not budget.memory.fits:
            compressed_memories = await self.compress_memories(
                retrieved_memories,
                budget.memory.budget_tokens,
            )

        compressed_history = history
        if not budget.history.fits:
            compressed_history = await self.compress_history(
                history,
                budget.history.budget_tokens,
                ctx=ctx,
            )

        compressed_scene = scene_prompt
        if not budget.scene.fits:
            compressed_scene = await self.compress_scene(
                scene_prompt,
                budget.scene.budget_tokens,
            )

        # 5. Apply compressed sections back to context
        ctx.history = compressed_history
        ctx.retrieved_memories = compressed_memories
        ctx.metadata["persona_prompt"] = compressed_persona
        if compressed_scene:
            ctx.metadata["roleplay_scene_prompt"] = compressed_scene

        # Re-compile context text
        ctx.compiled_context = self._compile_context_text(
            persona_prompt=compressed_persona,
            history=compressed_history,
            retrieved_memories=compressed_memories,
            scene_prompt=compressed_scene,
        )
        ctx.metadata["compiled_context"] = ctx.compiled_context

        return ctx

    async def compress_persona(
        self,
        persona_prompt: str,
        target_tokens: int,
        *,
        ctx: TurnContext | None = None,
    ) -> str:
        """Compress the persona prompt using LLM if available, otherwise heuristic pruner."""
        if target_tokens <= 0:
            return ""

        # Check if LLM summarization is possible
        provider = self.llm_provider
        if provider is None and ctx is not None:
            try:
                from chatbot.core.turn import _select_provider

                provider = _select_provider(ctx)
            except Exception:
                pass

        if provider is not None:
            try:
                model = self.model or (ctx.metadata.get("model") if ctx else None)
                config = ProviderConfig(
                    model=model or "default",
                    temperature=0.2,
                    max_tokens=max(64, target_tokens),
                    timeout_seconds=5.0,
                )
                system_instruction = (
                    "You are a prompt optimization sub-agent. Your task is to compress a "
                    "companion persona prompt to fit a smaller context window. "
                    "Do NOT lose any safety guidelines, core character traits, or critical voice "
                    "instructions. Make it extremely concise, remove filler words, and "
                    "consolidate rules. Respond ONLY with the compressed persona prompt text."
                )
                prompt = (
                    f"Compress the following persona prompt to fit within {target_tokens} tokens:\n\n"
                    f"{persona_prompt}"
                )
                try:
                    from chatbot.core.prompt_formatter import format_prompt

                    formatted = format_prompt(
                        model=model,
                        prompt=prompt,
                        system_prompt=system_instruction,
                    )
                    payload = formatted.payload
                    result = await provider.complete(
                        prompt=payload.get("prompt", prompt),
                        history=payload.get("history", []),
                        config=config,
                    )
                    compressed_text = result.text.strip()
                    if compressed_text and self.token_counter(compressed_text) <= target_tokens:
                        logger.info("Persona prompt compressed using LLM.")
                        return compressed_text
                except Exception as e:
                    logger.warning(
                        "LLM persona prompt compression failed: %s. Falling back to heuristic.",
                        e,
                    )
            except Exception as e:
                logger.warning(
                    "Failed to prepare LLM persona compression: %s. Falling back to heuristic.",
                    e,
                )

        # Heuristic / Extractive Fallback
        return self._heuristic_compress_persona(persona_prompt, target_tokens)

    def _heuristic_compress_persona(self, persona_prompt: str, target_tokens: int) -> str:
        """Prune paragraphs heuristically, keeping safety and core identity rules."""
        if not persona_prompt.strip() or target_tokens <= 0:
            return ""

        blocks = [b.strip() for b in persona_prompt.split("\n\n") if b.strip()]
        scored_blocks = []

        # Keywords for prioritization
        safety_keywords = {
            "safety",
            "must",
            "never",
            "always",
            "do not",
            "restrict",
            "violate",
            "prohibit",
        }
        core_keywords = {
            "identity",
            "companion",
            "character",
            "voice",
            "tone",
            "personality",
            "traits",
        }

        for idx, block in enumerate(blocks):
            # Base score favors earlier content (original layout priority)
            score = idx * 1.5

            block_lower = block.lower()
            # Boost priority (reduce score) for safety and core rules
            if any(k in block_lower for k in safety_keywords):
                score -= 10.0
            if any(k in block_lower for k in core_keywords):
                score -= 5.0

            scored_blocks.append((score, block))

        scored_blocks.sort(key=lambda x: x[0])

        selected_blocks = set()
        current_tokens = 0

        for _, block in scored_blocks:
            block_tokens = self.token_counter(block)
            if selected_blocks:
                block_tokens += 1  # Add overhead for double newlines
            if current_tokens + block_tokens <= target_tokens or not selected_blocks:
                selected_blocks.add(block)
                current_tokens += block_tokens

        # Maintain original order of selected blocks
        ordered_blocks = [b for b in blocks if b in selected_blocks]
        result = "\n\n".join(ordered_blocks)

        # Truncate if still over budget
        if self.token_counter(result) > target_tokens:
            try:
                from chatbot.core.prompt_layers import _truncate_to_budget

                result = _truncate_to_budget(result, target_tokens)
            except Exception:
                char_limit = target_tokens * 4
                result = result[:char_limit].rstrip()

        return result

    async def compress_history(
        self,
        history: Sequence[Mapping[str, Any]],
        target_tokens: int,
        *,
        ctx: TurnContext | None = None,
    ) -> list[dict[str, Any]]:
        """Compress the conversation history to fit target tokens."""
        if not history or target_tokens <= 0:
            return []

        if self.token_counter(history) <= target_tokens:
            return [dict(msg) for msg in history]

        # Preserve the most recent turns verbatim
        min_recent_turns = 3
        recent_turns = list(history[-min_recent_turns:])
        older_turns = list(history[:-min_recent_turns])

        if self.token_counter(recent_turns) > target_tokens:
            pruned_recent = []
            current_tokens = 0
            for msg in reversed(recent_turns):
                msg_tokens = self.token_counter(msg)
                if current_tokens + msg_tokens <= target_tokens or not pruned_recent:
                    pruned_recent.append(dict(msg))
                    current_tokens += msg_tokens
                else:
                    break
            pruned_recent.reverse()
            return pruned_recent

        # Attempt to summarize older turns
        provider = self.llm_provider
        if provider is None and ctx is not None:
            try:
                from chatbot.core.turn import _select_provider

                provider = _select_provider(ctx)
            except Exception:
                pass

        if provider is not None and older_turns:
            try:
                dialogue_lines = []
                for msg in older_turns:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    dialogue_lines.append(f"{role}: {content}")
                dialogue_text = "\n".join(dialogue_lines)

                model = self.model or (ctx.metadata.get("model") if ctx else None)
                config = ProviderConfig(
                    model=model or "default",
                    temperature=0.2,
                    max_tokens=max(64, target_tokens - self.token_counter(recent_turns)),
                    timeout_seconds=5.0,
                )
                system_instruction = (
                    "You are a conversation summarizer. Summarize the following dialogue briefly. "
                    "Capture key facts, user preferences, current topics, and unresolved questions. "
                    "Keep it highly concise and in third person. Respond ONLY with the summary text."
                )
                prompt = f"Summarize the conversation:\n\n{dialogue_text}"

                try:
                    from chatbot.core.prompt_formatter import format_prompt

                    formatted = format_prompt(
                        model=model,
                        prompt=prompt,
                        system_prompt=system_instruction,
                    )
                    payload = formatted.payload
                    result = await provider.complete(
                        prompt=payload.get("prompt", prompt),
                        history=payload.get("history", []),
                        config=config,
                    )
                    summary = result.text.strip()
                    if summary:
                        summary_msg = {
                            "role": "system",
                            "content": f"Summary of earlier conversation: {summary}",
                        }
                        combined = [summary_msg] + [dict(msg) for msg in recent_turns]
                        if self.token_counter(combined) <= target_tokens:
                            logger.info("Conversation history compressed using LLM summarization.")
                            return combined
                except Exception as e:
                    logger.warning(
                        "LLM history summarization failed: %s. Falling back to recency pruning.",
                        e,
                    )
            except Exception as e:
                logger.warning("Failed to prepare LLM history summarization: %s.", e)

        # Recency pruning fallback
        pruned_history = []
        current_tokens = 0
        for msg in reversed(history):
            msg_tokens = self.token_counter(msg)
            if current_tokens + msg_tokens <= target_tokens or not pruned_history:
                pruned_history.append(dict(msg))
                current_tokens += msg_tokens
            else:
                break
        pruned_history.reverse()
        return pruned_history

    async def compress_memories(
        self,
        memories: Sequence[Mapping[str, Any]],
        target_tokens: int,
    ) -> list[dict[str, Any]]:
        """Filter retrieved memories by relevance score and budget."""
        if not memories or target_tokens <= 0:
            return []

        def get_relevance(m: Mapping[str, Any]) -> float:
            for key in ("relevance", "relevance_score", "score", "confidence"):
                if key in m:
                    try:
                        return float(m[key])
                    except (ValueError, TypeError):
                        pass
            return 0.5

        sorted_memories = sorted(memories, key=get_relevance, reverse=True)
        kept_memories: list[dict[str, Any]] = []
        current_tokens = 0

        for memory in sorted_memories:
            content = (
                memory.get("content") or memory.get("summary") or memory.get("fact_text") or ""
            )
            text = str(content).strip()
            memory_type = str(memory.get("type") or memory.get("kind") or "").strip()
            formatted_line = f"- {memory_type}: {text}" if memory_type else f"- {text}"
            line_tokens = self.token_counter(formatted_line)

            if current_tokens + line_tokens <= target_tokens or not kept_memories:
                kept_memories.append(dict(memory))
                current_tokens += line_tokens
            else:
                break

        orig_indices = {id(m): i for i, m in enumerate(memories)}
        kept_memories.sort(key=lambda m: orig_indices.get(id(m), 0))
        return kept_memories

    async def compress_scene(self, scene_prompt: str, target_tokens: int) -> str:
        """Compress scene prompt/attention anchors using heuristic truncation."""
        if not scene_prompt or target_tokens <= 0:
            return ""
        if self.token_counter(scene_prompt) <= target_tokens:
            return scene_prompt

        char_limit = target_tokens * 4
        truncated = scene_prompt[:char_limit]
        for sep in ("\n\n", "\n", ". ", " "):
            idx = truncated.rfind(sep)
            if idx > char_limit // 2:
                return truncated[: idx + len(sep)].rstrip()
        return truncated.rstrip()

    def _compile_context_text(
        self,
        *,
        persona_prompt: str,
        history: Sequence[Mapping[str, Any]],
        retrieved_memories: Sequence[Mapping[str, Any]],
        scene_prompt: str = "",
    ) -> str:
        """Local implementation of context compilation matching _compile_context_text."""
        sections: list[str] = []
        if persona_prompt.strip():
            sections.append(persona_prompt.strip())

        if scene_prompt.strip():
            sections.append(scene_prompt.strip())

        memory_lines = []
        for memory in retrieved_memories:
            content = (
                memory.get("content") or memory.get("summary") or memory.get("fact_text") or ""
            )
            text = str(content).strip()
            if text:
                m_type = str(memory.get("type") or memory.get("kind") or "").strip()
                line = f"- {m_type}: {text}" if m_type else f"- {text}"
                memory_lines.append(line)

        if memory_lines:
            sections.append("Retrieved memories:\n" + "\n".join(memory_lines))

        history_lines = []
        for msg in history:
            role = str(msg.get("role") or "message").strip()
            content = str(msg.get("content") or "").strip()
            if content:
                history_lines.append(f"{role}: {content}")

        if history_lines:
            sections.append("Conversation history:\n" + "\n".join(history_lines))

        return "\n\n".join(sections)


class PromptCompressionStep(TurnStep):
    """A TurnStep that executes prompt compression against the TurnContext.

    This step checks if the total estimated tokens of the compiled context exceeds
    the target window limits and runs the compressor to prune/summarize.
    """

    name = "compress_prompt"

    def __init__(self, compressor: PromptCompressor | None = None) -> None:
        self.compressor = compressor or PromptCompressor()

    async def run(self, ctx: TurnContext) -> TurnContext:
        try:
            return await self.compressor.compress_context(ctx)
        except Exception:
            logger.error(
                "Prompt compression step failed, proceeding with original context.",
                exc_info=True,
            )
            return ctx
