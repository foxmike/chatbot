# Chatbot Engine Operations

## 1. Purpose of This Document

This document is for operating, administering, and using the chatbot system. It covers workflows for local setup, environment configuration, database administration, and basic user interactions.

**Note**: For design and codebase structure details, refer to `docs/ARCHITECTURE.md`. Do not add architectural explanations to this file.

## 2. Local Development Quick Start

To launch the entire stack using Docker Compose:

```bash
# 1. Start the services (API, UI, Database, Redis)
docker compose up -d

# 2. Apply database migrations
alembic upgrade head

# 3. (Optional) Run the seed script to create an initial chat session
python scripts/seed.py
```

Alternatively, to run the backend and frontend locally outside of Docker:
```bash
# Start backend
uvicorn chatbot.api.app:app --host 0.0.0.0 --port 8000 --reload

# Start frontend (in a new terminal)
cd react_ui
npm install
npm run dev
```

## 3. Docker Compose Services

- `api`: FastAPI backend service exposing host port `8000`.
- `ui`: React/Vite frontend built via `react_ui/Dockerfile`, mapped to host port `3000`.
- `db`: PostgreSQL 16 with `pgvector`, exposing host port `5432`. Data volume persists at `postgres-data`.
- `redis`: Redis 7 Alpine, exposing host port `6379`.

All services communicate over the `chatbot-net` bridge network.

## 4. Environment Variables

Operational settings are loaded via `.env` (overriding `chatbot/config.py` defaults):

- `DATABASE_URL`: `postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot`
- `REDIS_URL`: `redis://localhost:6379/0`
- `API_HOST`: `0.0.0.0`
- `API_PORT`: `8000`
- `LOG_LEVEL`: `INFO`
- `OPENROUTER_API_KEY`: **Required** for LLM capabilities.
- `OPENROUTER_BASE_URL`: `https://openrouter.ai/api/v1`
- `DEFAULT_MODEL`: `google/gemma-4-31b-it`
- `DEFAULT_PROVIDER`: `openrouter`

## 5. Database and Migrations

The application uses Alembic for database schema management.

- **Apply all migrations**: `alembic upgrade head`
- **Check current version**: `alembic current`

Sessions and messages are fully durable and are stored in PostgreSQL.

## 6. Running the Backend

The exact command used by the Dockerfile is:
```bash
uvicorn chatbot.api.app:app --host 0.0.0.0 --port 8000
```
When running locally for development, append `--reload` for auto-reloading.

## 7. Running the Frontend

The exact command inside the `react_ui` directory is:
```bash
npm run dev
```
The Vite development server uses a proxy rule in `vite.config.ts` to rewrite requests starting with `/api` and forward them to `http://localhost:8000`.

## 8. User Operations

- **Sign up / Log in**: The frontend includes an Auth screen to negotiate JWT tokens against backend auth routes.
- **Create Chat**: Click "New Chat" in the sidebar. This calls `POST /sessions` and navigates to the newly created session.
- **Switch Chat**: Click an existing session in the sidebar. The frontend requests `GET /sessions/{id}/messages` and reloads the durable chat history.
- **Delete Chat**: Click the `×` on a session in the sidebar. This triggers `DELETE /sessions/{id}`, removes related messages in Postgres, clears local UI cache, and auto-selects a different session.

## 9. Admin / Developer Operations

- **Start Stack**: `docker compose up -d`
- **Stop Stack**: `docker compose down`
- **View Logs**: `docker compose logs -f api` (or `db`, `redis`, `ui`)
- **Run Migrations**: `alembic upgrade head`
- **Seed Data**: Run `python scripts/seed.py`. This script targets the running API, creates a session, and sends a test message. Pass `--no-warm` to skip the test message.

## 10. API Operational Reference

Backend routes are mounted directly at the root, e.g., `http://localhost:8000/chat`. 

Verified active endpoints:
- `GET /health` (returns `{"status": "ok"}`)
- `POST /sessions`
- `DELETE /sessions/{session_id}`
- `GET /sessions/{session_id}/messages`
- `POST /chat`
- `/auth`, `/streaming`, `/config`, `/memory`, `/voice`, `/rooms`, `/agency`, `/admin`

## 11. Data Durability and Isolation

- **Users/Auth Sessions**: Persisted in Postgres.
- **Chat Sessions/Conversations**: Persisted durably in Postgres.
- **Messages**: Persisted durably in Postgres.
- **Deleted chats**: Triggering a delete permanently removes the session and cascades to all its messages in Postgres.
- **Per-user isolation**: All chat and session endpoints enforce strict ownership boundaries by checking the authenticated `user_id`.
- **Restarts**: All chat histories and configurations survive backend and database restarts.

## 12. Provider / Model Operations

- **Provider**: OpenRouter is the default.
- **Model**: `google/gemma-4-31b-it`.
- **Missing Provider Key**: If `OPENROUTER_API_KEY` is not set, the provider system logs a warning on startup (`openrouter provider registered but calls will fail`). When a chat is initiated, it will fail with an error string indicating the pipeline failed. 

## 13. Common Failure Modes and Fixes

- **Frontend cannot reach backend**: Verify `api` container is running and healthy on port 8000. Ensure `.env` is loaded properly.
- **Chat returns provider/API errors**: Check that `OPENROUTER_API_KEY` is exported and valid. Check `docker compose logs api` for detailed traceback.
- **Sessions/messages not persisting**: The Postgres connection is likely failing. Verify `DATABASE_URL` matches the exposed credentials. 
- **Delete does not remove chat**: Verify user ownership; the backend refuses deletes if the `user_id` does not match the session owner. Check API logs for 403/404 errors.
- **Database connection refused**: Ensure `docker compose up db` ran successfully.
- **Redis connection refused**: Ensure `docker compose up redis` ran successfully.
- **Migrations not applied**: An outdated schema will cause HTTP 500s. Run `alembic upgrade head`.

## 14. Verification Checklist

To verify a locally running stack:

1. Check running containers:
   ```bash
   docker compose ps
   ```
2. Verify migrations are up to date:
   ```bash
   alembic current
   ```
3. Check backend health:
   ```bash
   curl http://localhost:8000/health
   ```
4. Verify chat persistence: Create a chat in the UI at `http://localhost:3000`, send a message, and reload the page. The chat and message should persist.

## 15. Prompt Configuration and Telemetry

### Prompt Compression
The system automatically compresses prompts to fit within model context windows.

- **Compression Logic**: The `PromptCompressor` manages token budgets for persona, memory, history, and scene sections.
- **Thresholds**: If the total estimated tokens exceed the model's context window (resolved via `chatbot.core.token_budget`), compression is triggered.
- **Methods**:
    - **Persona**: LLM-based summarization (if available) or heuristic pruning of blocks, prioritizing safety and core identity.
    - **History**: Recency pruning (preserves last 3 turns) and LLM-based summarization of older turns.
    - **Memories**: Relevance-score based filtering.
    - **Scene**: Heuristic truncation.

### Prompt Telemetry
Telemetry is used to track the effectiveness of prompt configurations.

- **Collector**: `PromptTelemetryCollector` in `chatbot/observability/prompt_telemetry.py` records events.
- **Observed Data**: Each generation records the `prompt_template`, `model_id`, `mode`, and `formatter`.
- **Monitoring**: Telemetry events can be retrieved via `get_events()` to analyze which configurations produce the best responses.

## 16. Operational Boundaries

- `docs/OPERATIONS.md` is strictly for running, administering, using, and troubleshooting the system.
- `docs/ARCHITECTURE.md` is strictly for outlining system design, codebase map, and runtime wiring. 
- Future design decisions should be mapped in `ARCHITECTURE.md`, not here.
