# Chatbot Architecture

## 1. Purpose and Current Status
The application is a companion chatbot API service built for modular interactions. It appears to be in an active development phase, with many advanced features (like extensive memory, mood, persona, voice, visual generation) modeled in the database and structured in the codebase, but varying in their level of runtime wiring. Basic chat, user sessions, and conversation persistence are implemented and wired.

## 2. Repository Map
- `chatbot/`: Main Python backend package.
  - `api/`: FastAPI route definitions (`chat.py`, `sessions.py`, `memory.py`, etc.).
  - `core/`: Core runtime, mainly the chat turn pipeline (`turn.py`, `steps.py`).
  - `db/`: SQLAlchemy models (`models.py`).
  - `memory/`: Memory processing, extraction, retrieval, and vector stores.
  - `soul/`: Personality, mood, relationship, and user state engines.
  - `visual/`: Visual identity and appearance systems.
  - `voice/`: STT/TTS and cadence engines.
  - `providers/`: LLM integrations (`bootstrap.py`, `openrouter.py`).
  - `auth/`: Authentication dependencies and security.
- `react_ui/`: React/Vite frontend.
  - `src/App.tsx`: Main chat interface and state management.
- `migrations/`: Alembic database migrations.
- `docker-compose.yml`: Local infrastructure setup.

## 3. Runtime Stack
- **Frontend**: React, Vite, Tailwind CSS (implied by typical Vite setup, package.json).
- **Backend**: Python 3.12, FastAPI, Uvicorn.
- **Database**: PostgreSQL with `pgvector` for vector embeddings (via SQLAlchemy/asyncpg).
- **Cache/Queue**: Redis.
- **Migrations**: Alembic.
- **Containers**: Docker Compose for API, UI, DB, and Redis.
- **Provider Integrations**: OpenRouter (configured).

## 4. How to Run the System
Based on `docker-compose.yml` and `pyproject.toml`:
- **Docker Compose**: `docker compose up` starts `api` (port 8000), `ui` (port 3000), `db` (port 5432), and `redis` (port 6379).
- **Backend Command**: `uvicorn chatbot.api.app:app --host 0.0.0.0 --port 8000` (or similar, discoverable via `API_PORT`).
- **Frontend Command**: `npm run dev` in `react_ui/` (based on Vite).
- **Migration Command**: `alembic upgrade head` (via `alembic.ini`).

## 5. Configuration and Environment Variables
Important settings are managed in `chatbot/config.py` using `pydantic-settings`:
- `DATABASE_URL`: Defaults to `postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot`
- `REDIS_URL`: Defaults to `redis://localhost:6379/0`
- `API_HOST` / `API_PORT`: Defaults to `0.0.0.0:8000`
- `LOG_LEVEL`: Defaults to `INFO`
- `ENVIRONMENT`: Defaults to `development`
- `OPENROUTER_API_KEY`: Required for LLM calls.
- `OPENROUTER_BASE_URL`: Defaults to `https://openrouter.ai/api/v1`.
- `DEFAULT_MODEL`: Defaults to `google/gemma-4-31b-it`.
- `DEFAULT_PROVIDER`: Defaults to `openrouter`.

## 6. Data Model Overview
Key models in `chatbot/db/models.py`:
- **Core**: `User`, `UserSession` (auth), `Session` (chat conversations), `Message` (chat turns).
- **Companions**: `Companion` (bot profiles).
- **Memory**: `ImageMemory`, `SemanticFact`, `TimelineEntry`, `Episode`, `Entity`, `NarrativeThread`.
- **Identity/Soul**: `CharacterProfile`, `IdentityState`, `BotMoodLog`, `UserStateLog`, `RelationshipConfig`.
- **Visual/Voice**: `Appearance`, `OutfitRecord`, `VoicePreference`.
- **Agency/Rituals**: `Ritual`, `AgencyRitual`, `AgencyTimeline`, `InitiativeSetting`, `AgencyNotification`.
- **Rooms/Scenes**: `Room`, `Scene`.
- **Observability**: `ObservabilityTrace`, `ObservabilityUsage`.

## 7. API Overview
Routes are registered in `chatbot/api/app.py`:
- `chat`: `/chat` (Core interaction).
- `sessions`: `/sessions` (Manage chat threads).
- `memory`: `/memory`
- `config`: `/config`
- `agency`, `rooms`, `admin`, `auth`, `streaming`, `voice`: Grouped by domain.

## 8. Frontend Overview
`react_ui/src/App.tsx` contains the main chat UI. It manages API client calls to `createSession`, `deleteSession`, `getSessionMessages`, `listSessions`, and `sendMessage`. It maintains chat UI state, message history per session, handles conversation switching via `activeSessionId`, and includes a sidebar for session selection.

## 9. Chat Turn Pipeline
Path from user message to assistant response (`chatbot/core/turn.py` and `chatbot/api/chat.py`):
1. **Frontend Call**: POST to `/chat/`.
2. **Backend Route**: Resolves session and companion logic, ensures ownership, constructs `TurnContext`.
3. **Pipeline Orchestration**: Executes a pipeline constructed by `build_default_pipeline()`.
4. **Fast Path Steps**:
   - `receive`, `retrieve`, `compile_context`, `call_provider`, `respond`.
5. **Deferred Steps**:
   - `extract_memory`, `consolidate`.
6. **Response Persistence**: Responses are saved to `messages` table in Postgres (`api/chat.py`).

*Note on wiring*: Basic fast-path steps appear wired and handle fallback gracefully. Advanced conditional imports in `turn.py` (like `trigger_ritual`, `PersonaPromptAssembler`, `MoodEngine`) suggest these components are built but might gracefully degrade if dependencies fail or aren't fully integrated.

## 10. Model and Provider Usage
- **Configured Providers**: OpenRouter is the only explicitly registered provider at runtime (via `chatbot/providers/bootstrap.py`). Modal and OpenAI compatibility modules exist but appear unused.
- **Configured Models**: `google/gemma-4-31b-it` (default chat model).
- **Purpose**: Used for core chat generation.
- **Other Models (Memory, Voice, Vision)**: The codebase implies capabilities for extraction and visual generation, but specific models for these domains aren't explicitly configured in `config.py` beyond the default LLM.
- **Wired at Runtime**: The `openrouter` provider calling `google/gemma-4-31b-it` is wired for the chat pipeline.

## 11. Memory Architecture
- **Conversation History**: Implemented (via `messages` table).
- **Working Memory**: Built (`working_memory.py`), partial wiring.
- **Long-term Memory**: Built (Semantic Facts, Episodes).
- **User Facts**: Built.
- **Pinned Memories**: Not found.
- **Provenance**: Built (`provenance.py`).
- **Embeddings/Vector Search**: Built (`pgvector_store.py`, uses Postgres `pgvector`).
- **Summaries**: Built (`consolidation/summarizer.py`).
- **Retrieval/Ranking**: Built (`retrieval.py`).
- **Deletion/Correction**: Partial (supported via `controls.py`).
- **Per-User/Conversation Scoping**: Implemented (enforced via `user_id` and `session_id` in models).

## 12. Personality, Persona, Mood, and Relationship Systems
Located in `chatbot/soul/`:
- **Storage**: Backed by DB models (`CharacterProfile`, `BotMoodLog`, `RelationshipConfig`).
- **Configuration**: `Companion` table links to persona text.
- **Engines**: `MoodEngine`, `UserStateEngine`, `ModeSelector`, `PersonaPromptAssembler` exist.
- **Runtime Wiring**: `turn.py` imports these modules. They appear configured to inject into the prompt context during the `compile_context` step, but are wrapped in `try/except ImportError`, indicating they are modular and might be partially wired or optional.

## 13. Conversation and Chat Persistence
- **Creation/Selection**: Sessions are created and listed via `/sessions` API. Frontend maintains `activeSessionId`.
- **Persistence**: Durable storage in Postgres (`sessions` and `messages` tables).
- **Isolation**: Strictly scoped by `user_id` checking in API routes (`api/chat.py` enforces ownership).
- **Switching**: Frontend triggers loading messages based on session selection.

## 14. Voice, Visual Identity, and Companion Features
- **Voice (STT/TTS)**: `voice/` package exists with STT/TTS and cadence logic. API routes defined. Appears partial/stubbed in core pipeline.
- **Visual Identity**: `visual/` package handles appearance, outfits, image understanding, and generation (`image_memories`). Model relationships exist.
- **Rituals/Proactive**: `agency` and `continuity` packages define rituals and autonomous timeline actions.
- **Runtime Wiring**: These features exist robustly as independent services/APIs but their synchronous integration into every standard chat turn is partial or deferred.

## 15. Auth and Multi-User Isolation
- **Auth Mechanism**: JWT-based (defined in `chatbot/config.py` and `chatbot/auth/`).
- **User Model**: `User` and `UserSession` in DB.
- **Handling**: Dependencies like `get_current_user` inject `CurrentUser`.
- **Isolation**: Route handlers explicitly check `user_id` (e.g., `Session.user_id == user_uuid` in `chat.py`) to prevent unauthorized access.

## 16. Observability, Routing, Streaming, and Performance
- **Logging/Metrics/Tracing**: `observability/` package defines structured logging, metrics collection, and tracing (`observability_traces` table).
- **Routing**: `ProviderRouter` exists for LLM routing.
- **Streaming**: Defined in `streaming.py` with its own API router.
- **Performance/Background**: Redis is configured for caching and background queues (`BackgroundQueue`, `PersonaCache`). `turn.py` supports deferred steps (`extract_memory`, `consolidate`).

## 17. Known Gaps / Incomplete Areas
- Provider ecosystem relies heavily on OpenRouter; other providers are stubbed.
- Many advanced soul/memory/voice features rely on `try/except ImportError` fallbacks in the main pipeline, suggesting incomplete installation or optional dependencies.
- Embeddings models are not explicitly configured in environment variables, despite `pgvector` usage.
- UI implementation focuses primarily on basic chat and sessions; advanced features (voice, visual) may lack frontend components.

## 18. Change Guide for Future AI Agents

| To change this | Start here | Also inspect |
|---|---|---|
| chat pipeline | `chatbot/core/turn.py` | `chatbot/api/chat.py`, `chatbot/core/steps.py` |
| frontend chat UI | `react_ui/src/App.tsx` | `react_ui/src/components/`, `react_ui/src/api/` |
| conversation persistence | `chatbot/api/chat.py` | `chatbot/db/models.py`, `chatbot/api/sessions.py` |
| memory retrieval | `chatbot/memory/retrieval.py` | `chatbot/memory/pgvector_store.py` |
| memory extraction | `chatbot/memory/extraction.py` | `chatbot/core/turn.py` (DEFERRED_STEPS) |
| personality/persona | `chatbot/soul/persona_prompt.py` | `chatbot/db/models.py`, `chatbot/companions/` |
| mood/emotional state | `chatbot/soul/mood.py` | `chatbot/db/models.py` |
| relationship settings | `chatbot/soul/relationship.py` | `chatbot/db/models.py` |
| LLM provider/model config | `chatbot/config.py` | `chatbot/providers/bootstrap.py` |
| embeddings/vector search | `chatbot/memory/pgvector_store.py` | `chatbot/db/models.py` |
| auth | `chatbot/auth/dependencies.py` | `chatbot/auth/security.py`, `chatbot/api/auth.py` |
| voice | `chatbot/voice/` | `chatbot/api/voice.py` |
| visual identity | `chatbot/visual/` | `chatbot/db/models.py` |
| observability | `chatbot/observability/` | `chatbot/db/models.py` |
| streaming | `chatbot/api/streaming.py` | `chatbot/core/turn.py` |
| migrations | `migrations/versions/` | `alembic.ini`, `chatbot/db/models.py` |

## 19. Architecture Notes and Cautions
- do not assume a service is wired just because files exist
- preserve per-user/per-conversation scoping
- durable history belongs in Postgres
- run Alembic for schema changes
- avoid broad refactors
