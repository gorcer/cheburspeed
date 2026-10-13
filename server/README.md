# Backend / Server

FastAPI + asyncpg + alembic + WebSocket бекенд.

```sh
cd server
uv sync
uv run uvicorn xspeed_server.app:create_app --host 0.0.0.0 --port 8000 --factory
```

## Структура

- `src/xspeed_server/` — исходники (app, config, lifespan).
- `alembic/` — миграции БД (`alembic upgrade head`).
- `tests/` — pytest (asyncio).
- `Dockerfile` — production-образ (uv + python:3.12-slim).