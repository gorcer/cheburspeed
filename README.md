# XSpeed

Сервис мониторинга скорости работы vless соединений.

## Структура монорепо

- `server/` — FastAPI backend (REST API + WebSocket), PostgreSQL, JWT auth.
- `probe/` — клиентская проба (xray + speedtest через WebSocket).
- `frontend/` — SvelteKit UI (карта нод, live-результаты тестов).
- `nginx/` — реверс-прокси.
- `Makefile` — основные таргеты (`make install`, `make migrate`, `make server`, …).
- `docker-compose.yml` — стек `postgres` + `server` + `frontend` + `nginx`.

## Быстрый старт

```sh
cp .env.example .env
make install
make migrate
make server      # запуск backend
make probe       # запуск локальной пробы
make frontend    # запуск SvelteKit dev-сервера
make test
```

Полный запуск в Docker:

```sh
docker compose up --build
```

## Переменные окружения

См. `.env.example`. Обязательные: `DATABASE_URL`, `JWT_SECRET`, `WS_HOST`, `FRONTEND_ORIGIN`.

## Документация API

После запуска `server` документация доступна по `/docs` (Swagger UI).