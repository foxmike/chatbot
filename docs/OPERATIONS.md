# Chatbot Engine Operations

## 1. Purpose of This Document

This document is for operating, administering, and using the chatbot system. It covers workflows for local setup, environment configuration, database administration, and basic user/admin interactions.

**Note**: For design and codebase structure details, refer to [ARCHITECTURE.md](file:///home/mike/dev/mike/chatbot/python/chatbot/docs/ARCHITECTURE.md). Do not add architectural explanations to this file.

## 2. Local Development Quick Start

To launch the entire stack using Docker Compose:

```bash
# 1. Start the services (API, UI, Database, Redis, Worker)
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

- `api`: FastAPI backend service exposing host port `8000`. Exposes all chat and management endpoints.
- `ui`: React/Vite frontend built via `react_ui/Dockerfile`, mapped to host port `3000`.
- `db`: PostgreSQL 16 with `pgvector`, exposing host port `5432`. Data volume persists at `postgres-data`.
- `redis`: Redis 7 Alpine, exposing host port `6379`.
- `worker`: Celery worker service running `celery -A chatbot worker --loglevel=info`.
  > [!NOTE]
  > While the `worker` Celery service is defined in `docker-compose.yml` for future scalability, the current active task processor runs **in-process** inside the FastAPI `api` container. The `api` container initializes an asyncio-based `BackgroundWorker` during its startup lifecycle to consume memory extraction and consolidation tasks.

All services communicate over the `chatbot-net` bridge network.

## 4. Environment Variables

Operational settings are loaded via `.env` (overriding defaults in [chatbot/config.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/config.py)):

### Core Configuration
- `DATABASE_URL`: Database connection string. Default: `postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot`
- `REDIS_URL`: Redis cache/queue URL. Default: `redis://localhost:6379/0`
- `JWT_SECRET_KEY`: Secret key for signing authorization tokens. Default: `dev-jwt-secret-key-change-me`
- `API_HOST`: Bind address for API service. Default: `0.0.0.0`
- `API_PORT`: Port to run the API service on. Default: `8000`
- `LOG_LEVEL`: Logger verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Default: `INFO`
- `ENVIRONMENT`: Running environment name. Default: `development`

### LLM Providers and Routing
- `OPENROUTER_API_KEY`: API key for OpenRouter calls. Required for default LLM capabilities.
- `OPENROUTER_BASE_URL`: Endpoint URL for OpenRouter. Default: `https://openrouter.ai/api/v1`
- `DEFAULT_MODEL`: Model used for default chat tasks. Default: `google/gemma-4-31b-it`
- `DEFAULT_PROVIDER`: Default LLM provider name. Default: `openrouter`
- `LLM_PROVIDER`: Selected LLM provider backend (`openrouter` | `sglang` | `vllm` | `ollama`). Default: `openrouter`
- `SGLANG_BASE_URL`, `SGLANG_MODEL`, `SGLANG_API_KEY`: SGLang endpoint settings.
- `VLLM_BASE_URL`, `VLLM_MODEL`, `VLLM_API_KEY`: vLLM endpoint settings.
- `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_API_KEY`: Ollama endpoint settings.
- `OLLAMA_NUM_CTX`: Context window size override for Ollama. Default: `8192`

### Task-Class Model Routing
- `TASK_CLASS_ROUTING_ENABLED`: Enable routing different intents to specific models. Default: `True`
- `TASK_CLASS_DEFAULT_PROVIDER` / `TASK_CLASS_DEFAULT_MODEL`: Default fallback. Default: `openrouter` / `google/gemma-4-31b-it`
- `TASK_CLASS_CASUAL_PROVIDER` / `TASK_CLASS_CASUAL_MODEL`: Model for casual/greetings. Default: `openrouter` / `google/gemma-4-31b-it`
- `TASK_CLASS_EMOTIONAL_PROVIDER` / `TASK_CLASS_EMOTIONAL_MODEL`: Model for emotional/venting chat. Default: `openrouter` / `openrouter/deepseek/deepseek-v4-pro`
- `TASK_CLASS_SUMMARIZE_PROVIDER` / `TASK_CLASS_SUMMARIZE_MODEL`: Model for summaries. Default: `openrouter` / `openrouter/deepseek/deepseek-v3.2`
- `TASK_CLASS_EXTRACT_PROVIDER` / `TASK_CLASS_EXTRACT_MODEL`: Model for fact/memory extraction. Default: `openrouter` / `openrouter/qwen/qwen3-coder-next`
- `TASK_CLASS_EMBED_PROVIDER` / `TASK_CLASS_EMBED_MODEL`: Model for generating embeddings. Default: `openrouter` / `openrouter/z-ai/glm-4.7-flash`

### Memory Embeddings
- `EMBEDDING_PROVIDER`: Provider name for vector embeddings. Default: `openrouter`
- `EMBEDDING_MODEL`: Embedding model identifier. Default: `openai/text-embedding-3-small`

### Speech Services (TTS / STT)
- `TTS_PROVIDER`: TTS provider (`elevenlabs` | custom). Default: `elevenlabs`
- `TTS_MODEL`: TTS synthesis model. Default: `monolingual-v1`
- `TTS_VOICE`: Voice ID used for text-to-speech. Default: `""`
- `TTS_SPEED`: Cadence playback speed multiplier. Default: `1.0`
- `TTS_API_KEY` / `TTS_BASE_URL`: TTS provider auth keys and URLs.
- `STT_PROVIDER`: STT provider (`deepgram` | custom). Default: `deepgram`
- `STT_MODEL`: Speech-to-text model. Default: `nova-2`
- `STT_LANGUAGE`: Speech recognition language. Default: `en`
- `STT_API_KEY` / `STT_BASE_URL`: STT provider auth keys and URLs.

### Image Generation and Understanding
- `IMAGE_GENERATION_PROVIDER`: Selected image generation provider. Default: `openrouter`
- `IMAGE_GENERATION_MODEL`: Diffusion model. Default: `black-forest-labs/flux.2-pro`
- `IMAGE_GENERATION_API_KEY` / `IMAGE_GENERATION_BASE_URL`: Image generation auth and endpoint settings.
- `IMAGE_UNDERSTANDING_PROVIDER`: Image analysis/understanding provider. Default: `openrouter`
- `IMAGE_UNDERSTANDING_MODEL`: Multimodal analysis model. Default: `openrouter/moonshotai/kimi-k2-0905`
- `IMAGE_UNDERSTANDING_API_KEY` / `IMAGE_UNDERSTANDING_BASE_URL`: Image understanding auth and endpoint settings.

## 5. Database and Migrations

The application uses Alembic for database schema management.

- **Apply all migrations**: `alembic upgrade head`
- **Check current version**: `alembic current`

Sessions, messages, companion profiles, and memory items are fully durable and are stored in PostgreSQL.

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
- **Edit Companion**: normal users can create, select, edit, and delete their own custom companions using the Companion Editor interface, which includes fields for name, backstory, free-form persona, sliders, and JSON-based linguistic fingerprint fields.

## 9. Admin / Developer Operations

- **Start Stack**: `docker compose up -d`
- **Stop Stack**: `docker compose down`
- **View Logs**: `docker compose logs -f api` (or `db`, `redis`, `ui`, `worker`)
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
- `/auth`, `/streaming`, `/config`, `/memory`, `/voice`, `/rooms`, `/agency`, `/admin`, `/companions`, `/visual`, `/timeline`, `/narrative`, `/rituals`

## 11. Data Durability and Isolation

- **Users/Auth Sessions**: Persisted in Postgres.
- **Chat Sessions/Conversations**: Persisted durably in Postgres.
- **Messages**: Persisted durably in Postgres.
- **Deleted chats**: Triggering a delete permanently removes the session and cascades to all its messages in Postgres.
- **Per-user isolation**: The backend enforces strict ownership boundaries by checking the authenticated `user_id` on core endpoints (chat, sessions, companions).
- **Restarts**: All chat histories and configurations survive backend and database restarts.

## 12. Provider / Model Operations

- **Default Provider**: OpenRouter.
- **Default Model**: `google/gemma-4-31b-it`.
- **Missing Provider Key**: If `OPENROUTER_API_KEY` is not set, the provider system logs a warning on startup (`openrouter provider registered but calls will fail`). When a chat is initiated, it will fail with an error string indicating the pipeline failed.
- **Embedding Provider Configuration**: The embedding provider must be configured with a valid key for vector memory retrieval to function. To verify embeddings are working, check the backend logs for `message_embeddings_generated` and check the `Message.embedding` column in the database.

## 13. Common Failure Modes and Fixes

- **Frontend cannot reach backend**: Verify `api` container is running and healthy on port 8000. Ensure `.env` is loaded properly.
- **Chat returns provider/API errors**: Check that `OPENROUTER_API_KEY` is exported and valid. Check `docker compose logs api` for detailed traceback.
- **Sessions/messages not persisting**: The Postgres connection is likely failing. Verify `DATABASE_URL` matches the exposed credentials.
- **Delete does not remove chat**: Verify user ownership; the backend refuses deletes if the `user_id` does not match the session owner. Check API logs for 403/404 errors.
- **Database connection refused**: Ensure `docker compose up db` ran successfully.
- **Redis connection refused**: Ensure `docker compose up redis` ran successfully.
- **Migrations not applied**: An outdated schema will cause HTTP 500s. Run `alembic upgrade head`.
- **Worker service fails**: If the Celery container `worker` crashes due to missing dependencies, note that task processing will degrade only if the in-process FastAPI `BackgroundWorker` is not running. Check FastAPI logs (`docker compose logs api`) to verify tasks like `memory_extract` and `consolidate` are being executed successfully.
- **Linguistic Fingerprint JSON syntax errors**: When saving a Companion, if punctuation habits or slang profiles contain invalid JSON strings, the backend will return a 400 Bad Request. Ensure these fields are formatted as valid JSON objects in the Companion Editor.

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
5. Verify background tasks: Check backend logs after sending a message to ensure `memory_extract` tasks complete successfully:
   ```bash
   docker compose logs api | grep -i "background"
   ```

## 15. Prompt Configuration and Telemetry

### Prompt Telemetry Inspection
To analyze the effectiveness of prompt configurations, the `PromptTelemetryCollector` records prompt events. Developers can query these logged metrics directly:
- Each generation event records `prompt_template`, `model_id`, `mode`, and `formatter`, associated with a `trace_id`.
- Check these logs by calling the admin endpoints or inspecting debug logs:
  ```bash
  docker compose logs api | grep -i "Recorded prompt telemetry"
  ```

### Prompt Compression
The system automatically compresses prompts to fit within model context windows.
- **Compression Logic**: The `PromptCompressor` manages token budgets for persona, memory, history, and scene sections.
- **Thresholds**: If the total estimated tokens exceed the model's context window (resolved via `chatbot.core.token_budget`), compression is triggered.
- **Heuristics & Fallbacks**: Persona uses LLM-based summarization (if available) with a fallback to safety-preserving heuristic pruning. History uses dialogue summarization with a recency-pruning fallback. Memories are filtered by similarity relevance scores.

## 16. Operational Boundaries

- `docs/OPERATIONS.md` is strictly for running, administering, using, and troubleshooting the system.
- `docs/ARCHITECTURE.md` is strictly for outlining system design, codebase map, and runtime wiring.
- Future design decisions should be mapped in `ARCHITECTURE.md`, not here.
