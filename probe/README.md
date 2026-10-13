# Probe

Клиент-проба — регистрируется на сервере по токену и через WebSocket
получает задания на замер скорости vless-соединений. Для каждого
задания запускает локальный [xray-core](https://github.com/XTLS/Xray-core),
подключается к указанному vless://-эндпоинту и замеряет download
throughput через SOCKS5-прокси.

## Архитектура

```
                   +--------------------+
   WS (online,     |                    |   subprocess.Popen
   task.assigned,  |     probe          |   xray run -c …
   result)         |                    |   +--> SOCKS5 127.0.0.1:1080
   <-------------> |  wsclient  | xray  |                 |
                   |  + speedtest + netif                 v
                   +--------------------+        httpx GET (proxy)
                                                        |
                                                        v
                                           SPEEDTEST_URL (Cloudflare)
```

* `config.py` — pydantic-settings (`SERVER_URL`, `PROBE_TOKEN`,
  `NETWORK_INTERFACE?`, `SPEEDTEST_URL?`, `PROBE_ALIAS?`, `REGION_CODE?`).
* `netif.py` — проверяет, что `NETWORK_INTERFACE` присутствует в
  `socket.if_nameindex()`, иначе шлёт `netif_unavailable`.
* `xray.py` — парсит `vless://` URI, генерирует JSON-конфиг и
  запускает `xray-core`; ждёт открытия SOCKS-порта перед замером.
* `speedtest.py` — `httpx.AsyncClient` через SOCKS-прокси, стримит
  тело ответа, вычисляет Mbps по фактически скачанным байтам.
* `wsclient.py` — WebSocket-клиент с `ping_interval=30` и
  reconnect+backoff (1s → 30s).

## Локальная разработка

```sh
cd probe
cp .env.example .env
# заполнить PROBE_TOKEN и SERVER_URL

uv sync
uv run xspeed-probe        # или make probe-run
```

## Docker (multi-arch)

`make probe` собирает и пушит multi-arch образ (linux/amd64 +
linux/arm64) в `ghcr.io/gorcer/xspeed-probe:latest`:

```sh
make probe                 # → probe-buildx
make probe-build PLATFORMS=linux/amd64   # одно-архитектурный build
```

Override переменных сборки:

```sh
make probe-buildx PROBE_IMAGE=ghcr.io/me/probe PROBE_IMAGE_TAG=v0.1.0
```

## Запуск контейнера

```sh
docker run --rm \
    -e SERVER_URL=ws://host:8080/ws \
    -e PROBE_TOKEN=… \
    -e PROBE_ALIAS=lab \
    -e REGION_CODE=msk \
    --network=host \
    ghcr.io/gorcer/xspeed-probe:latest
```

> Используется `--network=host`, чтобы xray мог слушать на
> `127.0.0.1:1080` (внутри контейнера) и при этом выполнять исходящие
> соединения через интерфейс, указанный в `NETWORK_INTERFACE` (на хосте).

## Graceful shutdown

`SIGTERM` / `SIGINT` отменяет текущий замер (если идёт), закрывает
WebSocket и завершает все xray-процессы, порождённые этой пробой.

## Переменные окружения

| Имя                 | Обязательное                                                  | Описание                                              |
|---------------------|------------------------------------------------------------|-------------------------------------------------------|
| `SERVER_URL`        | нет (`ws://server:8080/ws`)                                | WebSocket endpoint                                    |
| `PROBE_TOKEN`      | да                                                          | Bearer-токен для авторизации на сервере               |
| `PROBE_ALIAS`      | нет                                                         | Человекочитаемое имя пробы                            |
| `REGION_CODE`      | нет                                                         | Код региона (см. `regions.code` в БД)                 |
| `NETWORK_INTERFACE`| нет                                                         | Исходящий NIC; отсутствие → задача пропускается       |
| `SPEEDTEST_URL`    | нет                                                         | URL для замера скорости                                |
| `LOG_LEVEL`        | нет (`INFO`)                                               | Уровень логирования                                   |