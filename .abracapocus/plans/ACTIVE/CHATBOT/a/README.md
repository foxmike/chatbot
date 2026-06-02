# a

## Goal

Repository foundation and runnable infrastructure. uv-managed Python 3.12 project, Docker Compose bringing up Postgres+pgvector and Redis, typed config loader, Alembic initialized, and a FastAPI app skeleton exposing a health endpoint that everything else builds on.

## Notes

- Async: Bootstrap is mostly sequential: pyproject + project package first (wave 1). docker-compose.yml, .env.example, and config.py can run in parallel in wave 2 on disjoint files. The FastAPI app skeleton + health endpoint and the Alembic init form wave 3 and depend on config.
- Risk: Foundation phase; every later phase depends on it. Keep dependencies minimal and approved-only. No business logic here.
