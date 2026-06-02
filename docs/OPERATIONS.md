## Chatbot Engine Operations

### Local Development Setup

Use this sequence from the repository root for a local chatbot engine environment:

1. Install prerequisites:
   - Python 3.12.
   - `uv`.
   - Docker with Docker Compose for local PostgreSQL and Redis.
2. Start the local backing services:

   ```bash
   docker compose up -d
   ```

3. Install Python dependencies:

   ```bash
   uv pip install -r requirements.txt
   ```

4. Run database migrations:

   ```bash
   alembic upgrade head
   ```

5. Populate local seed data:

   ```bash
   python scripts/seed.py
   ```

6. Start the API:

   ```bash
   uvicorn chatbot.api.app:app --reload
   ```

The repository entrypoint is `chatbot.api.app:app`. The current app factory mounts the
health router; chat, streaming, and session routers exist under `chatbot/api/` and should
be mounted before relying on `/chat`, `/chat/stream`, or `/sessions` through the running
FastAPI app.

### Docker Compose Service Layout

- `api` (`chatbot-api` role): FastAPI service exposed on host port `8000`. The Dockerfile
  starts `uvicorn chatbot.api.app:app --host 0.0.0.0 --port 8000`.
- `db` (`postgres` role): PostgreSQL 16 with the pgvector extension image
  (`pgvector/pgvector:pg16`) exposed on host port `5432`. The default database, user, and
  password are all `chatbot`.
- `redis`: Redis 7 Alpine exposed on host port `6379`.

Compose attaches the services to the `chatbot-net` bridge network and stores PostgreSQL
data in the `postgres-data` named volume.

### Environment Variables

- `DATABASE_URL`: PostgreSQL connection string. The application settings default to
  `postgresql://chatbot:chatbot@localhost:5432/chatbot`; async SQLAlchemy database code
  defaults to `postgresql+asyncpg://postgres:postgres@localhost:5432/chatbot` if this
  variable is not set, so set it explicitly for consistent local behavior.
- `REDIS_URL`: Redis connection string. Default: `redis://localhost:6379/0`.
- `CHATBOT_PROVIDER`: LLM provider identifier. Default operational value: `openrouter`.
- `CHATBOT_MODEL`: Model identifier. Default operational value:
  `openrouter/deepseek/deepseek-v4-pro`.
- `CHATBOT_API_KEY`: API key for the LLM provider. Required when provider-backed turn
  steps are enabled.
- `CHATBOT_LOG_LEVEL`: Chatbot logging level. Default operational value: `INFO`.
- `LOG_LEVEL`: Logging level currently loaded by `chatbot.config.Settings`. Default:
  `INFO`.
- `API_HOST`: API bind host loaded by settings. Default: `0.0.0.0`.
- `API_PORT`: API port loaded by settings. Default: `8000`.
- `ENVIRONMENT`: Runtime environment label loaded by settings. Default: `development`.

The `.env.example` file contains the currently wired local settings. Keep `.env` out of
source control and use it for local overrides.

### Health Checks

- `GET /health` returns HTTP 200 with `{"status": "ok"}` when the API process is
  reachable. In the current implementation this endpoint does not check the database.
- `GET /health/ready` is the readiness target for all dependencies: database, Redis, and
  provider. It should return HTTP 200 only when every dependency passes its readiness
  check. If this route returns 404 in the current checkout, the readiness endpoint has not
  been mounted yet.

### Seed Script Usage

The seed script targets the running HTTP API and creates a development session. By
default it sends one warm-up chat message after session creation.

- `python scripts/seed.py`: basic seed; creates a session and sends the warm-up message.
- `python scripts/seed.py --no-warm`: creates a session and skips the warm-up message.
- `python scripts/seed.py --base-url http://other-host:8000`: targets an alternative API
  host.
- `SEED_BASE_URL=http://other-host:8000 python scripts/seed.py`: uses the environment
  variable instead of `--base-url`.

The script calls `POST /sessions` and `POST /chat` using `httpx` with redirects enabled.
If the application has not mounted the session and chat routers, the seed script will fail
with an HTTP status error.

### Common Failure Modes

- Provider unreachable: check `CHATBOT_API_KEY`, the configured provider/model values,
  and network access to the provider endpoint.
- Database connection refused: verify `docker compose up -d` has started the `db`
  service and that `DATABASE_URL` points at the expected host, port, user, password, and
  database.
- Redis connection refused: verify `docker compose up -d` has started the `redis` service
  and that `REDIS_URL` is correct.
- Memory retrieval returns empty: verify the pgvector extension is installed. The first
  migration runs `CREATE EXTENSION IF NOT EXISTS vector`; rerun `alembic upgrade head` or
  check the database manually if the extension is missing.
- Session lost between restarts: sessions are in-memory by default in
  `chatbot.api.sessions.sessions_store`; persistent session storage is a future
  enhancement.
- Chat or seed requests return 404: verify the FastAPI app has mounted the chat,
  streaming, and session routers in addition to the health router.
- Chat responses say `No response`: the current default turn pipeline uses identity
  steps and only reports `ctx.final_response` or a pipeline error; provider-backed
  response generation must be wired before conversational replies are produced.

### Troubleshooting

- Verify service status:

  ```bash
  docker compose ps
  ```

- Tail API logs:

  ```bash
  docker compose logs -f api
  ```

- Tail database logs:

  ```bash
  docker compose logs -f db
  ```

- Tail Redis logs:

  ```bash
  docker compose logs -f redis
  ```

- Check the health endpoint:

  ```bash
  curl http://localhost:8000/health
  ```

- Smoke-test the chat endpoint after the chat router is mounted:

  ```bash
  curl -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d '{"session_id":"test","message":"Hello"}'
  ```

- If the smoke test redirects or fails, retry the route form implemented by
  `chatbot.api.chat`:

  ```bash
  curl -X POST http://localhost:8000/chat/ -H 'Content-Type: application/json' -d '{"session_id":"test","message":"Hello"}'
  ```
