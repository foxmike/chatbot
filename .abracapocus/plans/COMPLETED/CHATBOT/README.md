# COMPANION_ENGINE

## Purpose

Build a modular, production-intended companion chatbot engine as a standalone API service. The engine relies on internally-coded memory, personality/soul, and state so that the typical weaknesses and inter-model differences of the underlying LLM are minimized. No orchestration framework owns the core: there is no LangGraph, Letta, or Zep acting as the brain. The turn loop, memory consolidation, context compilation, and identity continuity are all first-party code. Backends are reached through a single OpenAI-compatible Provider interface (covering OpenRouter, local vLLM, and Vast.ai-stood-up instances) plus a Modal ephemeral wrapper for episodic compute. Vectors live in Postgres via pgvector behind a VectorStore interface so a Qdrant swap later touches one adapter. Stack: Python 3.12, FastAPI, Postgres, Redis, Alembic, Docker Compose, uv. Everything is structured so voice, frontend, and visual modules attach later without touching the core.
