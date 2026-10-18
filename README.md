# CheburSpeed

Сервис мониторинга скорости работы vless соединений.

## Структура монорепо

- `server/` — FastAPI backend (REST API + WebSocket), PostgreSQL, JWT auth.
- `probe/` — клиентская проба (xray + speedtest через WebSocket),
  multi-arch Docker-образ (`linux/amd64`, `linux/arm64`).
- `frontend/` — SvelteKit UI (карта нод, live-результаты тестов).
- `nginx/` — реверс-прокси. `nginx.conf` для dev,
  `nginx-prod.conf.template` для production с TLS + ACME.
- `docker-compose.yml` — локальный стек `postgres` + `server`
  + `frontend` + `nginx` без TLS.
- `docker-compose.prod.yml` — production-стек: `restart: always`,
  healthchecks, certbot, persistent volumes.
- `.github/workflows/` — CI/CD: `server-ci.yml`, `frontend-ci.yml`,
  `probe-ci.yml`, `release.yml`.
- `Makefile` — основные таргеты (`make install`, `make migrate`, …).

## Быстрый старт (dev)

```sh
cp .env.example .env
make install
make migrate
make server      # запуск backend
make probe       # сборка и push локальной multi-arch пробы
make frontend    # запуск SvelteKit dev-сервера
make test
```

Запуск всего стека в Docker (без TLS):

```sh
docker compose up --build
```

## Production deployment

Production-стек работает только на образах из GHCR, выкатывает HTTPS
через certbot и применяет `restart: always`. Один раз подготовить:

```sh
cp .env.prod.example .env.prod
$EDITOR .env.prod   # заполнить JWT_SECRET, NGINX_SERVER_NAME, CERTBOT_EMAIL, …
docker compose -f docker-compose.prod.yml --env-file .env.prod pull
```

Получить первый сертификат Let's Encrypt:

```sh
make certbot-init
# либо:
# docker compose -f docker-compose.prod.yml --env-file .env.prod \
#   --profile init run --rm certbot-init
```

Поднять стек:

```sh
make prod-up
make prod-logs       # логи в реальном времени
make prod-ps         # статус сервисов и healthcheck'и
```

Поддерживаемые `make`-таргеты production-стека:

| Команда             | Что делает                                                            |
|---------------------|-----------------------------------------------------------------------|
| `make prod-up`      | `compose … up -d`                                                     |
| `make prod-down`    | `compose … down`                                                      |
| `make prod-logs`    | `compose … logs -f`                                                   |
| `make prod-ps`      | `compose … ps`                                                        |
| `make prod-pull`    | подтянуть свежие образы по `XSPEED_VERSION`                           |
| `make prod-config`  | провалидировать compose-конфиг (`docker compose config`)              |
| `make certbot-init` | одноразовый выпуск сертификата через webroot                           |

После первого `prod-up` certbot-контейнер периодически продлевает
сертификаты (каждые 12 часов) — отдельный cron не нужен.

## Переменные окружения

`.env` (dev) — см. `.env.example`. Обязательные: `DATABASE_URL`,
`JWT_SECRET`, `WS_HOST`, `FRONTEND_ORIGIN`.

`.env.prod` (production) — см. `.env.prod.example`. Кроме dev-полей нужны:

| Переменная            | Назначение                                                |
|-----------------------|------------------------------------------------------------|
| `GITHUB_REPOSITORY_OWNER` | владелец образа в GHCR (по умолчанию `gorcer`)         |
| `XSPEED_VERSION`      | тег образа (`:latest`, `0.1.0`, …)                         |
| `NGINX_SERVER_NAME`   | доменное имя (используется nginx и certbot)               |
| `CERTBOT_EMAIL`       | контакт для Let's Encrypt                                 |
| `NGINX_HTTP_PORT`     | порт HTTP на хосте (по умолчанию `80`)                     |
| `NGINX_HTTPS_PORT`    | порт HTTPS на хосте (по умолчанию `443`)                   |

`JWT_SECRET` генерируется так:

```sh
openssl rand -hex 32
```

## CI/CD

CI описан в `.github/workflows/`:

- **`server-ci.yml`** — push/PR в `master`/`dev`, ветвящиеся в
  изменения под `server/`. Прогоняет `ruff`, `ruff format --check`,
  `mypy`, `pytest --cov` и собирает Docker-образ
  `ghcr.io/<owner>/cheburspeed-server` (push в GHCR только на push,
  не на PR).
- **`frontend-ci.yml`** — push/PR, ветвящиеся в `frontend/`. Прогоняет
  `prettier --check`, ESLint, `svelte-check`, `pnpm build` и собирает
  Docker-образ `ghcr.io/<owner>/cheburspeed-frontend`.
- **`probe-ci.yml`** — push/PR, ветвящиеся в `probe/`. Прогоняет
  `ruff`, `pytest` и multi-arch buildx
  (`linux/amd64,linux/arm64`) для образа
  `ghcr.io/<owner>/cheburspeed-probe`. После push на `master`/`dev`
  шаг `verify-manifest` делает `docker buildx imagetools inspect`
  и проверяет, что манифест содержит обе архитектуры.
- **`release.yml`** — срабатывает на пуш тега `v*`. Собирает все
  три образа с тегами `:X.Y.Z` и `:latest`, прикладывает к Release
  архив `frontend/build/`, генерирует release notes (`git log` от
  предыдущего тега до HEAD + автоматические GitHub release notes).

Все workflow используют `GITHUB_TOKEN` для входа в GHCR — отдельных
secrets не нужно. Для авторизации при локальном пуше тега достаточно
прав `contents: write` и `packages: write`, которые есть у
мэйнтейнеров репозитория.

### Локальная сборка multi-arch пробы

```sh
make probe-buildx                       # build + push всех архитектур
make probe-build                        # одиночная сборка под хост
make probe-buildx IMAGE_TAG=0.1.0 PROBE_PLATFORMS=linux/arm64
```

Аналогично для сервера и фронтенда: `make server-buildx`,
`make frontend-buildx`.

### Релиз новой версии

```sh
# дерево должно быть чистым
make release-tag  VERSION=0.1.0
make release-push VERSION=0.1.0
# → push v0.1.0 в origin → запускается .github/workflows/release.yml
# → три Docker-образа с тегами :0.1.0 и :latest в GHCR
# → GitHub Release с архивом frontend/build
```

Если удобнее одной командой:

```sh
git tag -a v0.1.0 -m "Release v0.1.0" && git push origin v0.1.0
```

## Добавление пробы

Проба — это контейнер, который запускается на узле рядом с
xray-инстансом, получает задания по WebSocket и возвращает
измеренную скорость на сервер. Существует два сценария установки:
через готовый образ из GHCR (production) или из исходников (dev).

### Быстрая установка с GHCR

После релиза образ `ghcr.io/gorcer/cheburspeed-probe` уже
содержит и xray, и клиент. Для production-узлов используйте этот
путь — multi-arch образ (linux/amd64, linux/arm64) собирается
через buildx в `.github/workflows/release.yml`.

```sh
docker run -d \
  --name xspeed-probe \
  --network=host \
  --restart unless-stopped \
  -e SERVER_URL=wss://xspeed.example.com/ws \
  -e PROBE_TOKEN=<токен из таблицы probes> \
  -e PROBE_ALIAS=msk-router-1 \
  -e REGION_CODE=msk \
  ghcr.io/gorcer/cheburspeed-probe:latest
```

`--network=host` нужен, чтобы проба могла слушать xray на
`127.0.0.1:1080` без проброса портов. Если это недопустимо
по соображениям безопасности, прокиньте порт явно:

```sh
docker run -d --name xspeed-probe \
  --restart unless-stopped \
  -p 127.0.0.1:1080:1080 \
  -e SERVER_URL=wss://xspeed.example.com/ws \
  -e PROBE_TOKEN=<токен из таблицы probes> \
  -e PROBE_ALIAS=msk-router-1 \
  -e REGION_CODE=msk \
  ghcr.io/gorcer/cheburspeed-probe:latest
```

Теги:

- `:latest` — последний push в `master`/`dev`, обновляется при
  каждом коммите. Не использовать в production.
- `:X.Y.Z` — релизные теги, ставятся при пуше тега `vX.Y.Z`
  (см. `.github/workflows/release.yml`). Рекомендуется
  фиксировать релизный тег в `.env.prod` через `XSPEED_VERSION`.

Архитектуры: образ собирается сразу под `linux/amd64` и
`linux/arm64`. Проверить наличие обеих платформ можно так:

```sh
docker buildx imagetools inspect ghcr.io/gorcer/cheburspeed-probe:latest
```

### Получение токена пробы

Проба авторизуется на сервере по токену, который заранее
регистрируется в админке. Без токена контейнер запустится, но
WebSocket-хэндшейк будет отклонён с HTTP 403.

1. Зайти в `/admin/login`, ввести email/пароль администратора
   (создаётся через `make admin-create email=… password=…`).
2. Перейти в `/admin/probes` и нажать «Создать пробу».
3. Заполнить поля `alias` (человекочитаемое имя, например
   `msk-router-1`) и `region` (код региона из справочника,
   например `msk`). Поле `token` можно оставить пустым — сервер
   сгенерирует его автоматически.
4. Скопировать полученный `token` и подставить его в `.env`
   (`PROBE_TOKEN=…`) или в `-e PROBE_TOKEN=…` при `docker run`.

Тот же `alias`/`region_code` сервер использует для отображения
пробы на карте (`/admin/dashboard`, главная страница). Если
планируется больше одной пробы в одном регионе — у каждой должен
быть уникальный `alias`.

### Локальное управление пробой (`probe.sh`)

В корне репозитория лежит `probe.sh` — обёртка над
`docker run` / `docker start|stop|rm` для текущего окружения.
Конфигурация берётся из `.env` (`PROBE_TOKEN`, `PROBE_REGION`,
`PROBE_ALIAS`, `PROBE_SERVER_URL`, `PROBE_SPEEDTEST_URL`).

```sh
./probe.sh create    # build образа (если нужно) + новый контейнер
./probe.sh start     # поднять уже созданный, но остановленный контейнер
./probe.sh status    # docker ps + последние логи + серверная регистрация
./probe.sh stop      # остановить контейнер (НЕ удалять; revives через start)
./probe.sh restart   # docker restart (stop + start, без пересборки образа)
./probe.sh remove    # удалить контейнер (заменяет старое «деструктивное stop»)
./probe.sh logs [N]  # `docker logs --tail N -f`
```

Семантика `stop` / `start` / `remove` совпадает с plain docker:
`stop` гасит процесс (контейнер остаётся на диске), `start` —
`docker start` уже существующего контейнера, `remove` —
`docker rm -f` для окончательного удаления. `create` идемпотентен:
повторный запуск снимает существующий контейнер с тем же именем
и поднимает новый.

`create` поддерживает два флага:

- `--no-build` — пропустить `docker build`, даже если образ
  отсутствует локально (полезно, если образ уже подтянут из
  GHCR через `docker pull`).
- `--pull` — выполнить `docker pull $PROBE_IMAGE` вместо
  локальной сборки.

`status` дополнительно заходит в `/api/v1/admin/probes` и находит
запись, чей `token` совпадает с `PROBE_TOKEN` из `.env` (если
токен не выставлен — ищет по `alias`/`region_code`). Без
админ-учётных данных или при недоступном сервере этот шаг тихо
пропускается.

Имя контейнера по умолчанию `xspeed-probe-${PROBE_REGION,,}`;
переопределяется через `PROBE_CONTAINER` в `.env`.

### Запуск из исходников (dev)

Для отладки без контейнера можно запустить пробу прямо из
исходников Python. Внутри `probe/` используется тот же asyncio
event-loop, что и в Docker-образе, только вместо xray-конфига
можно указать собственный SOCKS5 через переменные окружения.

```sh
cd probe
cp .env.example .env
# отредактировать .env: SERVER_URL, PROBE_TOKEN, PROBE_ALIAS, REGION_CODE
uv sync
uv run xspeed-probe
```

Альтернативно из корня репозитория — через Makefile:

```sh
make install-probe    # uv sync в probe/
make probe-run        # uv run xspeed-probe
```

Проба автоматически переподключается при обрыве связи
(exponential backoff, от 1 до 30 секунд). Без заданного
`NETWORK_INTERFACE` проба использует любой доступный интерфейс;
если переменная задана, но интерфейс отсутствует, проба логирует
warning и пропускает задание со статусом
`error="netif_unavailable"`.

## Документация API

После запуска `server` Swagger UI доступен по `/docs`.

## Troubleshooting

| Симптом                                                    | Проверка                                                                                  |
|------------------------------------------------------------|-------------------------------------------------------------------------------------------|
| `prod-up` падает на certbot                                | `make prod-config` — внутри `NGINX_SERVER_NAME` и `CERTBOT_EMAIL` обязательны.            |
| WebSocket-соединения рвутся через nginx                   | Проверить `proxy_read_timeout` в `nginx-prod.conf.template` (по умолчанию 3600s).         |
| Healthcheck `server` ругается на `localhost:8000/healthz`  | `docker compose -f docker-compose.prod.yml logs server`; убедиться, что порт 8000 открыт внутри `xspeed_net`. |
| Проба не подключается                                      | `docker logs xspeed-probe`; `curl -v $SERVER_URL`; проверить, что nginx проксирует `/ws/`. |
| `docker buildx imagetools inspect …` показывает одну платформу | В workflow включён `setup-qemu-action`; иначе buildx не умеет кросс-архитектуру.        |
| `compose config` жалуется на `${VAR:?...}`                | В `.env.prod` отсутствует обязательная переменная.                                        |

## Лицензия

MIT.
