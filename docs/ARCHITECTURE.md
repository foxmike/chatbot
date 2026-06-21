# Chatbot Architecture

## 1. Purpose and Current Status
The application is a companion chatbot API service built for modular, personalized interactions. Basic chat, user sessions, conversation persistence, and user authentication are fully implemented and active. Personality systems, mood logs, multi-timescale memory retrieval, and structured background consolidation tasks are wired and functional.

## 2. Repository Map
- `chatbot/`: Main Python backend package.
  - `api/`: FastAPI route definitions (`chat.py`, `sessions.py`, `memory.py`, `companions.py`, `visual.py`, `voice.py`, `continuity.py`, `rooms.py`, etc.).
  - `core/`: Core runtime, mainly the chat turn pipeline, intent classifier, prompt layer assembly, token budgeting, and prompt compressor (`turn.py`, `steps.py`, `prompt_layers.py`, `prompt_formatter.py`, `intent_classifier.py`, `token_budget.py`, `prompt_compressor.py`, `attention_anchor.py`, `mode_contracts.py`).
  - `db/`: SQLAlchemy models (`models.py`).
  - `memory/`: Memory processing, extraction, retrieval, timescales, and vector store adapters (`timescale.py`, `confidence.py`, `retrieval.py`, `narrative_gravity.py`, `session_delta.py`, `callback_planner.py`).
  - `soul/`: Personality, mood, relationship, pacing, anti-repetition, and user state engines (`linguistic_fingerprint.py`, `anti_repetition.py`, `pacing.py`, `persona_prompt.py`, `relationship.py`, `mood.py`, `user_state.py`, `modes.py`).
  - `visual/`: Visual identity, outfits, appearance, and diffusion generation managers.
  - `voice/`: STT/TTS adapters and cadences (`prompt_adapter.py`, `tts.py`, `stt.py`).
  - `providers/`: LLM integrations, model families, and sampling profiles (`bootstrap.py`, `router.py`, `openai_compat.py`, `sampling.py`).
  - `auth/`: Authentication dependencies, JWT security, and user-scoping.
- `react_ui/`: React/Vite frontend.
  - `src/App.tsx`: Main chat interface and state management.
  - `src/components/`: Subcomponents (e.g. `CompanionEditor.tsx`, `CompanionSelector.tsx`, `SessionSidebar.tsx`, `MessageList.tsx`).
- `migrations/`: Alembic database migrations.
- `docker-compose.yml`: Local infrastructure setup (database, cache, frontend, backend API, and a worker skeleton).

## 3. Runtime Stack
- **Frontend**: React, Vite, Tailwind CSS (configured in `react_ui/package.json` and `tailwind.config.js`).
- **Backend**: Python 3.12, FastAPI, Uvicorn.
- **Database**: PostgreSQL with `pgvector` for vector embeddings (via SQLAlchemy/asyncpg).
- **Cache/Queue**: Redis.
- **Migrations**: Alembic.
- **Containers**: Docker Compose for API, UI, DB, Redis, and Worker.
- **Provider Integrations**: OpenRouter, SGLang, vLLM, and Ollama support.

## 4. How to Run the System
Based on `docker-compose.yml` and repository files:
- **Docker Compose**: `docker compose up` starts `api` (port 8000), `ui` (port 3000), `db` (port 5432), `redis` (port 6379), and `worker` (Celery background worker).
- **Backend Command**: `uvicorn chatbot.api.app:app --host 0.0.0.0 --port 8000` (executed by `Dockerfile`).
- **Frontend Command**: `npm run dev` (executed as `vite` in `react_ui/package.json`).
- **Migration Command**: `alembic upgrade head` (via `alembic.ini`).

## 5. Configuration and Environment Variables
Settings are managed in `chatbot/config.py`:
- `DATABASE_URL`: Defaults to `postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot`.
- `REDIS_URL`: Defaults to `redis://localhost:6379/0`.
- `API_HOST` / `API_PORT`: Defaults to `0.0.0.0:8000`.
- `OPENROUTER_API_KEY`: Required for default remote LLM operations.
- `LLM_PROVIDER`: Selected LLM provider backend (`openrouter` | `sglang` | `vllm` | `ollama`).
- `EMBEDDING_PROVIDER`: Selected embedding provider (e.g. `openrouter`).
- `EMBEDDING_MODEL`: Embedding model name (e.g. `openai/text-embedding-3-small`).

## 6. Data Model Overview
Key models in `chatbot/db/models.py`:
- **Core**: `User` (includes `adaptive_verbosity`), `UserSession` (auth), `Session` (chat conversations), `Message` (chat turns, includes `embedding` vectors).
- **Companions**: `Companion` (bot profiles including JSON fields for `punctuation_habits`, `sentence_length_profile`, `emoji_style`, `slang_profile`, and float `vocabulary_density`).
- **Memory**: `ImageMemory`, `SemanticFact` (durable memories), `TimelineEntry` (continuity projects, promises, questions), `Episode`, `Entity`, `NarrativeThread`.
- **Identity/Soul**: `CharacterProfileModel`, `IdentityStateModel` (stores slow/fixed identity properties and mood logs), `BotMoodLog`, `UserStateLog`, `RelationshipConfigModel`.
- **Visual/Voice**: `Appearance`, `OutfitRecord`, `Ritual`, `AgencyRitual`, `AgencyTimeline`, `InitiativeSettings`, `Notification`.
- **Observability**: `ObservabilityTrace`, `ObservabilityUsage`.

## 7. API Overview
Routes are registered in `chatbot/api/app.py`:
- `chat`: `/chat` (Core interaction endpoint, including streaming `/chat/stream`).
- `sessions`: `/sessions` (Manage chat threads).
- `companions`: `/companions` (Authenticated companion CRUD).
- `config`: `/config` (Manages relationship configs, character profiles, identity states).
- `memory`: `/memory` (Manage long-term semantic facts).
- `rooms`: `/rooms` (Manage rooms, scenes, and roleplay frames).
- `visual`: `/visual` (Generates or uploads avatar images).
- `voice`: `/voice` (Synthesizes voice speech or transcribes audio; manages voice preferences).
- `continuity`: `/timeline`, `/narrative`, `/rituals` (Tracks continuity events).
- `agency`: `/agency` (Initiative, notifications, proactive scheduling).
- `admin`: `/admin` (Observability logs, traces, usage, metrics).

## 8. Frontend Overview
`react_ui/src/App.tsx` directly owns the main chat UI state. It handles login, lists sessions, renders active chats, and integrates the `CompanionEditor` and `CompanionSelector` to allow users to build and modify companion profiles (such as editing names, sliders, and JSON-based linguistic fingerprints).

## 9. Chat Turn Pipeline
Path from user message to assistant response (`chatbot/core/turn.py` and `chatbot/api/chat.py`):
1. **Frontend Call**: POST to `/chat/`.
2. **Backend Route**: Validates current user, resolves session, companion, and room configurations. Initializes `TurnContext`.
3. **Intent Classification**: Evaluates user message intent (e.g. casual vs deep venting) to route the request:
   - **Fast Path**: Evaluated for simple intents (`greeting`, `casual`, `feedback`). Runs synchronous steps: `receive`, `retrieve`, `compile_context`, `call_provider`, `respond`, and `update_identity`. Post-turn steps are deferred to the background queue.
   - **Deep Path**: Evaluated for complex intents (`venting`, `roleplay`, `planning`, `question`, `command`). Runs all steps in sequence, including emotional-intelligence inference and mood updates.
4. **Context Compilation**:
   - Assembles prompt layers deterministically.
   - Trims text using token budgets.
   - Formats instructions using the target model's message structure.
5. **LLM Provider Call**: Sends the payload to SGLang, vLLM, Ollama, or OpenRouter with mode-specific sampling parameters.
6. **Respond**: Returns the final text response.
7. **Post-Turn Queue**: If fast-path was used, deferred tasks (`extract_memory` and `consolidate`) are pushed onto `BackgroundQueue` for asynchronous processing.

## 10. Model and Provider Usage
- **Chat Generation**: Models like `google/gemma-4-31b-it` are configured via settings. SGLang, vLLM, Ollama, and OpenRouter are supported providers.
- **Message Formatting**: The `prompt_formatter` dynamically structures message payloads based on model family profiles:
   - `OPENAI`: Formats system instruction in a `"system"` role dictionary.
   - `ANTHROPIC`: Lifts the system instruction to a root `"system"` parameter.
   - `GEMINI`: Maps message roles to `"model"` and structures system prompts in `"system_instruction"`.
- **Mode Contracts**: Defined in [mode_contracts.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/mode_contracts.py). Dictates response constraints across modes (`bedtime`, `advisor`, `deep_talk`, `roleplay`, `brainstorming`, `companion`), overriding tone, pacing, maximum token ratios, and active prompt sections. Integrated with room configurations to enforce tone-consistent interactions per room type.
- **Sampling Profiles**: Defined in [sampling.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/providers/sampling.py). Resolves generation parameters (temperature, top_p, top_k, presence/frequency penalties) per model family, adjusting them dynamically using mode-specific temperature modifiers (e.g., roleplay increases temperature, bedtime lowers it).
- **Memory Embeddings**: Handled by the configured `embedding_provider` (e.g., `openai/text-embedding-3-small`). Enables pgvector similarity search on the database.


## 11. Memory Architecture
The memory system is structured to build comprehensive situational awareness:
- **Conversation History**: Loaded synchronously from Postgres.
- **Working Memory**: Dynamically retrieved in `SceneAwareRetrievalStep` using `PgvectorStore` and the configured embedding provider.
- **Retrieval Confidence Scoring**: Evaluates retrieved results and drops items falling below a set threshold.
- **Contradiction-Aware Recency Tagging**: Employs a `ContradictionResolver` to verify incoming facts. If a new memory contradicts a retrieved fact, the old fact is tagged as superseded.
- **Multi-Timescale Structuring**: Categorizes memories into timescales (`recent_conversational_state`, `stable_identity_fact`, `long_term_narrative_arc`, `active_goal`).
- **Narrative Gravity Weighting**: Adjusts vector scores based on emotional importance and salience metadata flags.
- **Background Task Execution**: An asyncio-based `BackgroundWorker` runs in-process inside the FastAPI app. It processes `memory_extract` and `consolidate` tasks asynchronously, extracting semantic facts and organizing narrative summaries without blocking the user response path.

## 12. Personality, Persona, Mood, and Relationship Systems
Located in `chatbot/soul/`:
- **Persona Assembly**: `PersonaPromptAssembler` compiles the companion's core prompts.
- **Linguistic Fingerprint**: Injects specific stylization directives (punctuation ellipsis frequency, sentence complexity, emoji style, slang types, and vocabulary density) into the prompt based on companion profile columns.
- **Anti-Repetition Directives**: Analyzes recent responses to detect repeated words, phrase n-grams, sentence starters, or concluding questions, appending temporary negative directives to enforce vocabulary variation.
- **Assistantism Scrubber**: Automatically appends a directive to strip AI-assistant verbal tics (e.g., "Certainly!", "As an AI...").
- **Pacing Control**: Estimates the user's typing momentum (response delay vs message length) to choose pacing directives matching the current scene type.
- **Relationship Config**: Generates prompt fragments guiding companion behavior based on relationship stage trust metrics (acquaintance, established bond, intimate).

## 13. Conversation and Chat Persistence
- **Durable Persistence**: All sessions, companions, messages, and configurations are stored durably in PostgreSQL tables.
- **Delete Cascade**: Deleting a session via `DELETE /sessions/{id}` cascades to cleanly remove all corresponding messages.

## 14. Voice, Visual Identity, and Companion Features
- **Voice (STT/TTS)**: Exposes endpoints for transcription and synthesis. The `voice/prompt_adapter.py` formats voice-variant prompts, instructing the LLM to write speech-friendly text (no markdown, written-out numbers). Synthesize and transcribe are currently mock adapters that raise `NotImplementedError`.
- **Visual Identity**: Exposes endpoints to generate, upload, list, and delete companion avatar images. Integrates with the React UI gallery. Real image generation relies on remote diffusion APIs.
- **Continuity & Rituals**: Registers routers for timeline, narrative, and rituals. Continuity tables are persisted in Postgres.

## 15. Auth and Multi-User Isolation
- **Auth**: JWT-based bearer tokens inject current user credentials.
- **Isolation**: Sessions, companions, and messages enforce ownership checks (`current_user.user_id == Session.user_id`).
  > [!WARNING]
  > Several secondary routers (e.g., config, rooms, memory, agency, timeline, narrative, rituals, admin, streaming) contain endpoints that either lack authentication or query sessions without verifying current-user ownership.

## 16. Observability and Prompt Telemetry
- **Structured Logs**: The `StructuredLogger` tracks memory-write, contradiction, and failed-retrieval events.
- **Prompt Telemetry**: `PromptTelemetryCollector` records the configuration variables (`prompt_template`, `model_id`, `mode`, `formatter`) and associates them with `trace_id` hashes to enable prompt engineering feedback.
- **Traces & Metrics**: Tracing collects execution durations of all turn steps.

## 17. Prompt System, Budgeting, and Compression
- **Token Budgeting**: A `TokenBudgetCalculator` calculates maximum token allocations dynamically across `persona`, `memory`, `history`, and `scene` slots based on the target model's context window.
- **Prompt Layers**: Built from name-based blocks with deterministic priority ordering:
  `safety_floor` → `platform_behavior` → `companion_identity` → `relationship_stage` → `room_scene` → `mood_state` → `memory` → `session_goals`
- **Short-Response Logic**: Contextual evaluation permissions determine whether the model is allowed to reply with a short response based on user brevity mirroring and intent classification.
- **Prompt Compressor**: Scaffolded system to compress/summarize context slots if token estimates exceed allocations.

## 18. Known Gaps / Incomplete Areas
- **Celery Worker Discrepancy**: `docker-compose.yml` launches a Celery worker command (`celery -A chatbot worker`), but Celery is not installed or configured in the Python codebase. Active background processing is currently executed by the FastAPI asyncio `BackgroundWorker` lifespan task.
- **Mock/Scaffolded Adapters**: TTS/STT and visual diffusion calls raise `NotImplementedError` or generation errors.
- **Scaffolded Prompt Steps**: Attention anchoring, narrative gravity weighting, session-resume delta injection, and prompt compression are scaffolded in code but not integrated into the standard chat pipeline.
- **Auth Gaps**: Endpoints in config, memory, rooms, agency, admin, and streaming routers are partially unauthenticated or lack user-scoped session ownership checks.

## 19. Change Guide for Future AI Agents

| To change this | Start here | Also inspect |
|---|---|---|
| Chat pipeline | `chatbot/core/turn.py` | `chatbot/api/chat.py`, `chatbot/core/steps.py` |
| Prompt assembly layers | `chatbot/core/prompt_layers.py` | `chatbot/core/prompt_formatter.py` |
| Model formatting family | `chatbot/core/prompt_formatter.py` | `chatbot/providers/sampling.py` |
| Intent classification | `chatbot/core/intent_classifier.py` | `chatbot/core/steps.py` |
| Token budgets | `chatbot/core/token_budget.py` | `chatbot/core/prompt_compressor.py` |
| Mode contracts | `chatbot/core/mode_contracts.py` | `chatbot/rooms/room.py` |
| Companion database schema | `chatbot/db/models.py` | `migrations/versions/a01b4ebf6ff0_...` |
| Companion editor UI | `react_ui/src/components/CompanionEditor.tsx` | `react_ui/src/api/client.ts` |
| Linguistic fingerprinting | `chatbot/soul/linguistic_fingerprint.py` | `chatbot/soul/persona_prompt.py` |
| Anti-repetition dynamics | `chatbot/soul/anti_repetition.py` | `chatbot/soul/persona_prompt.py` |
| Pacing rules | `chatbot/soul/pacing.py` | `chatbot/soul/persona_prompt.py` |
| Narrative gravity weighting | `chatbot/memory/narrative_gravity.py` | `chatbot/memory/retrieval.py` |
| Session resume context | `chatbot/memory/session_delta.py` | `chatbot/core/prompt_compressor.py` |
| Callback planning | `chatbot/memory/callback_planner.py` | `chatbot/continuity/timeline.py` |
| Background tasks & workers | `chatbot/perf/worker.py` | `chatbot/perf/handlers.py`, `chatbot/api/app.py` |
| Telemetry & log events | `chatbot/observability/prompt_telemetry.py` | `chatbot/observability/logs.py` |
| Prompt compression | `chatbot/core/prompt_compressor.py` | `chatbot/core/token_budget.py` |

## 20. Architecture Notes and Cautions
- Do not assume a service is wired just because files exist.
- Verify user/session ownership scoping in all API operations.
- Ensure all database schemas have corresponding migrations in Alembic.
