# Chatbot Architecture

## 1. Purpose and Current Status
The application is a companion chatbot API service built for modular interactions. It is in active development. Basic chat, user sessions, conversation persistence, and session deletion are implemented and active. Advanced features (mood, persona, user state) are actively wired into the chat pipeline, while others (voice, visual generation, memory extraction/consolidation) exist as API endpoints or database scaffolding but are either unconfigured or lack running workers to execute them.

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
- **Frontend**: React, Vite, Tailwind CSS (configured in `react_ui/package.json` and `tailwind.config.js`).
- **Backend**: Python 3.12, FastAPI, Uvicorn.
- **Database**: PostgreSQL with `pgvector` for vector embeddings (via SQLAlchemy/asyncpg).
- **Cache/Queue**: Redis.
- **Migrations**: Alembic.
- **Containers**: Docker Compose for API, UI, DB, and Redis.
- **Provider Integrations**: OpenRouter.

## 4. How to Run the System
Based on `docker-compose.yml` and repository files:
- **Docker Compose**: `docker compose up` starts `api` (port 8000), `ui` (port 3000), `db` (port 5432), and `redis` (port 6379).
- **Backend Command**: `uvicorn chatbot.api.app:app --host 0.0.0.0 --port 8000` (executed by `Dockerfile`).
- **Frontend Command**: `npm run dev` (executed as `vite` in `react_ui/package.json`).
- **Migration Command**: `alembic upgrade head` (via `alembic.ini`).

## 5. Configuration and Environment Variables
Settings are managed in `chatbot/config.py`:
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
Routes are registered in `chatbot/api/app.py` directly at the root (e.g., `/chat`, `/sessions`). The frontend uses Vite proxy rules (`vite.config.ts`) to rewrite `/api/` requests to the root of the backend on port 8000.
- `chat`: `/chat` (Core interaction).
- `sessions`: `/sessions` (Manage chat threads).
- `memory`: `/memory`
- `config`: `/config`
- `agency`, `rooms`, `admin`, `auth`, `streaming`, `voice`: Grouped by domain.

## 8. Frontend Overview
`react_ui/src/App.tsx` directly owns the main chat UI state via React `useState` hooks. It manages API client calls to `createSession`, `deleteSession`, `getSessionMessages`, `listSessions`, and `sendMessage`. It maintains message history per session, handles conversation switching via `activeSessionId`, and includes a sidebar (`SessionSidebar.tsx`) for selecting or deleting sessions.

## 9. Chat Turn Pipeline
Path from user message to assistant response (`chatbot/core/turn.py` and `chatbot/api/chat.py`):
1. **Frontend Call**: POST to `/chat/`.
2. **Backend Route**: Resolves session and companion logic, ensures ownership, and constructs `TurnContext`.
3. **Pipeline Orchestration**: Executes a pipeline via `build_default_pipeline()`.
4. **Fast Path Steps**:
   - `receive`: Cleans input.
   - `retrieve`: Loads working memory (semantic retrieval is inactive because `retrieval_engine` is not passed to context in `chat.py`).
   - `compile_context`: Active. Injects Persona, Mood, and UserState into prompts using imported engines.
   - `call_provider`: Calls OpenRouter.
   - `respond`: Returns output.
5. **Deferred Steps**:
   - `extract_memory` and `consolidate` are queued to `BackgroundQueue`. However, since no background worker service is defined in `docker-compose.yml`, they are never executed.
6. **Response Persistence**: Responses are saved synchronously to the `messages` table in Postgres (`api/chat.py`).

## 10. Model and Provider Usage
- **Configured Providers**: OpenRouter is the only explicitly registered provider at runtime (via `chatbot/providers/bootstrap.py`).
- **Configured Models**: `google/gemma-4-31b-it` (default chat model).
- **Purpose**: Used for core chat generation.
- **Embeddings/Vector Search**: The codebase contains pgvector logic (`chatbot/memory/pgvector_store.py`), but no embedding model is configured in `config.py` or `bootstrap.py`. Embeddings are effectively disabled in the standard flow.
- **Voice/Vision**: No external models or providers are configured for voice (STT/TTS) or image generation. These subsystems exist only as API scaffolding.

## 11. Memory Architecture
- **Conversation History**: Wired in standard chat path (via `messages` table).
- **Working Memory**: Wired in standard chat path (loaded in `RetrieveStep`).
- **Embeddings/Vector Search**: Database model exists only (no configured embedding provider, and retrieval is skipped in `chat.py`).
- **Memory Extraction/Consolidation**: Service exists but not wired (deferred tasks are queued but no worker runs them).
- **Long-term Memory/Semantic Facts**: Available through API only.
- **Per-User/Conversation Scoping**: Wired in standard chat path (enforced via `user_id` and `session_id` checks).

## 12. Personality, Persona, Mood, and Relationship Systems
Located in `chatbot/soul/`:
- **Storage**: Backed by DB models (`CharacterProfile`, `BotMoodLog`, `RelationshipConfig`).
- **Runtime Wiring**: `MoodEngine`, `UserStateEngine`, and `PersonaPromptAssembler` are actively imported and used by `build_default_pipeline` in `turn.py`. They actively affect the prompts via the `compile_context` step in the standard chat path.

## 13. Conversation and Chat Persistence
- **Creation/Selection**: Sessions are created and listed via `/sessions` API. `App.tsx` maintains `activeSessionId`.
- **Persistence**: Durable storage in Postgres (`sessions` and `messages` tables).
- **Deletion**: Fully wired. The frontend provides a delete button in `SessionSidebar`. Clicking it calls `DELETE /sessions/{session_id}`. `App.tsx` removes cached messages from state and auto-selects or creates another chat.
- **Isolation**: Strictly scoped by `user_id` checking in API routes (`api/chat.py` and `api/sessions.py` enforce ownership).

## 14. Voice, Visual Identity, and Companion Features
- **Voice (STT/TTS)**: `voice/` package exists with STT/TTS and cadence logic. It relies on API scaffolding without external models configured at runtime.
- **Visual Identity**: `visual/` package handles appearance, outfits, image understanding, and generation (`image_memories`). Contains scaffolding but no explicit provider wiring.
- **Rituals/Proactive**: `agency` and `continuity` packages define rituals and autonomous timeline actions, but are conditionally skipped in standard chat if missing or unconfigured.

## 15. Auth and Multi-User Isolation
- **Auth Mechanism**: JWT-based (defined in `chatbot/config.py` and `chatbot/auth/`).
- **User Model**: `User` and `UserSession` in DB.
- **Handling**: Dependencies like `get_current_user` inject `CurrentUser`.
- **Isolation**: Route handlers explicitly check `user_id` to prevent unauthorized access.

## 16. Observability, Routing, Streaming, and Performance
- **Logging/Metrics/Tracing**: `observability/` package defines structured logging, metrics collection, and tracing (`observability_traces` table).
- **Routing**: `ProviderRouter` exists for LLM routing.
- **Streaming**: Defined in `streaming.py` with its own API router.

## 17. Known Gaps / Incomplete Areas
- The background worker service is entirely absent from `docker-compose.yml`, breaking all deferred memory extraction and consolidation.
- The `retrieval_engine` is not passed to the `TurnContext` in `api/chat.py`, silently bypassing vector memory retrieval in the chat pipeline.
- No embedding provider (e.g. OpenAI/Cohere) is configured in `bootstrap.py` or `config.py` to power `pgvector_store.py`.
- Voice and Vision systems exist purely as unconfigured scaffolding.

## 18. Change Guide for Future AI Agents

| To change this | Start here | Also inspect |
|---|---|---|
| chat pipeline | `chatbot/core/turn.py` | `chatbot/api/chat.py`, `chatbot/core/steps.py` |
| frontend chat UI | `react_ui/src/App.tsx` | `react_ui/src/components/`, `react_ui/src/api/` |
| conversation persistence / delete | `chatbot/api/sessions.py` | `react_ui/src/App.tsx`, `chatbot/db/models.py` |
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
- Do not assume a service is wired just because files exist.
- Preserve per-user/per-conversation scoping in all API operations.
- Durable history belongs in Postgres.
- Run Alembic for schema changes.
- Avoid broad refactors.
