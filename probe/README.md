# Probe

Клиент-проба — регистрируется на сервере по токену и через WebSocket
получает задания на замер скорости туннелей. Поддерживаются два
протокола, оба публикуют туннель локально на SOCKS5 `127.0.0.1:1080`:

* **VLESS** — локальный [xray-core](https://github.com/XTLS/Xray-core)
  запускается с конфигом из `vless://`-ссылки;
* **AmneziaWG** — userspace-хелпер `awgsocks` (см. ниже) поднимает
  туннель из INI-конфига AmneziaVPN.

Замер скорости в обоих случаях идёт через этот SOCKS-прокси.

## Архитектура

```
                    +--------------------+
   WS (online,     |                    |   subprocess.Popen
   task.assigned,  |     probe          |   xray run -c …            (vless)
   result)         |                    |   +--> SOCKS5 127.0.0.1:1080
   <-------------> |  wsclient  | xray  |                 |
                   |  + awg     | awg   |   subprocess.Popen
                   |  + speedtest + netif  awgsocks -config …
                   +--------------------+   +--> SOCKS5 127.0.0.1:1080
                                                         |
                                                         v
                                            httpx GET (proxy)
                                                         |
                                                         v
                                            SPEEDTEST_URL (Cloudflare)
```

* `config.py` — pydantic-settings (`SERVER_URL`, `PROBE_TOKEN`,
  `NETWORK_INTERFACE?`, `SPEEDTEST_URL?`, `PROBE_ALIAS?`, `REGION_CODE?`).
* `netif.py` — проверяет, что `NETWORK_INTERFACE` присутствует в
  `socket.if_nameindex()`, иначе молча пропускает задачу. Дополнительно
  резолвит IPv4 интерфейса через SIOCGIFADDR; при отсутствии маршрутизируемого
  IPv4 задача тоже пропускается без ошибки (xray принимает только IP-литерал
  в `sendThrough`, лишних проверок в нём нет).
* `xray.py` — парсит `vless://` URI, генерирует JSON-конфиг и
  запускает `xray-core`; ждёт открытия SOCKS-порта перед замером.
  Если проба передала IPv4 интерфейса, кладёт его в `sendThrough` —
  так xray действительно пинит исходящий NIC, а не идёт через
  default route.
* `awg.py` — парсит INI-конфиг AmneziaWG (`[Interface]` / `[Peer]`),
  декодирует base64-ключи в hex (UAPI `amneziawg-go` понимает только
  hex), пишет JSON для хелпера и управляет его процессом.
* `speedtest.py` — `httpx.AsyncClient` через SOCKS-прокси, стримит
  тело ответа, вычисляет Mbps по фактически скачанным байтам.
* `wsclient.py` — WebSocket-клиент с `ping_interval=30` и
  reconnect+backoff (1s → 30s).

## AmneziaWG: почему userspace

AWG-обфускации нет ни в xray-core, ни в sing-box, поэтому туннель
строит отдельный хелпер `probe/awgproxy/` на
[`amneziawg-go/v3`](https://github.com/amnezia-vpn/amneziawg-go)
(gVisor netstack + параметры `jc/jmin/jmax/s1..s4/h1..h4/i1..i5`).
Kernel-модуль `awg` не подходит: пробы работают непривилегированно
(`--network=host`, uid 1000, без `/dev/net/tun` и без `NET_ADMIN`), а
контейнер делит netns с хостом. gVisor даёт TCP/UDP/ICMP внутри
процесса без единой capability.

* `probe/awgproxy/main.go` — флаги `-config`, `-socks`, `-bind-device`;
  резолвит `Endpoint` системным резолвером **до** `IpcSet`
  (`amneziawg-go` принимает только IP-литерал, а AmneziaVPN отдаёт
  домен), поднимает девайс и SOCKS5-сервер (только CONNECT).
* `probe/awgproxy/bind_linux.go` — собственная реализация
  `conn.Bind` с `SO_BINDTODEVICE`, потому что `StdNetBind` из
  `amneziawg-go` создаёт сокеты внутри себя и не даёт задать
  socket-опции. Используется только когда задан `-bind-device`.
* `probe/awgproxy/uapi.go` — рендер конфигурации в UAPI; отсутствующие
  необфусцирующие параметры не выводятся (пустое значение UAPI
  отвергает как ошибку разбора).

Ключи в `[Interface]` и `[Peer]` (`PrivateKey`, `PresharedKey`) — это
секрет: конфиг на диске создаётся с `umask 0o077` и правами `0600`,
в логи попадает только argv без секретов, `?link=` в URL не пишется.

Проверка живым туннелем (нужен рабочий AWG-конфиг):

```sh
cd probe && uv sync
uv run python - <<'PY'
import json
from xspeed_probe.awg import build_awg_helper_config, parse_awg_config
cfg = parse_awg_config(open("/path/to/amnezia.conf").read())
json.dump(build_awg_helper_config(cfg), open("/tmp/awg.json", "w"), indent=2)
PY
docker build -t xspeed-probe:local -f Dockerfile .
docker run --rm --network=host --entrypoint /usr/local/bin/awgsocks \
    -v /tmp/awg.json:/cfg/awg.json:ro \
    xspeed-probe:local -config /cfg/awg.json -socks 127.0.0.1:1080
curl -sS --socks5-hostname 127.0.0.1:1080 -o /dev/null -w '%{size_download}\n' \
    'https://speed.cloudflare.com/__down?bytes=5000000'
```

Тесты хелпера: `cd probe/awgproxy && go test ./...`.

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
> Имя интерфейса стабилизируется через udev-правило
> `infra/udev/90-usb-phone-tether.rules` (см. ниже): USB-модемы телефонов
> по умолчанию получают имена на основе MAC, который меняется при
> переподключении — без правила проба теряет биндинг после каждого
> перетыка кабеля.

## Стабильное имя USB-модема

`NETWORK_INTERFACE` ожидает имя интерфейса, которое не меняется между
переподключениями телефона. USB-tether (RNDIS / CDC-Ethernet) по
умолчанию получает имя вида `enx<MAC>`, а MAC телефоны перегенерируют
на каждый реплаг. Подключите правило из `infra/udev/`:

```sh
sudo install -m 0644 infra/udev/90-usb-phone-tether.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --action=add
ip -o link show usbphone0      # должен появиться USB-Ethernet
```

После этого `NETWORK_INTERFACE=usbphone0` остаётся валидным независимо
от того, сколько раз телефон перетыкался. Правило матчит
`idVendor=04e8 idProduct=6863` (Samsung Galaxy tethering); добавьте
ещё строки для других телефонов по образцу.

## Graceful shutdown

`SIGTERM` / `SIGINT` отменяет текущий замер (если идёт), закрывает
WebSocket и завершает все процессы туннелей (xray и `awgsocks`),
порождённые этой пробой.

## Переменные окружения

| Имя                 | Обязательное                                                  | Описание                                              |
|---------------------|------------------------------------------------------------|-------------------------------------------------------|
| `SERVER_URL`        | нет (`ws://server:8080/ws`)                                | WebSocket endpoint                                    |
| `PROBE_TOKEN`      | да                                                          | Bearer-токен для авторизации на сервере               |
| `PROBE_ALIAS`      | нет                                                         | Человекочитаемое имя пробы                            |
| `REGION_CODE`      | нет                                                         | Код региона (см. `regions.code` в БД)                 |
| `NETWORK_INTERFACE`| нет                                                         | Исходящий NIC. Для vless проба резолвит его IPv4 (через SIOCGIFADDR) и кладёт в `sendThrough`; для AmneziaWG имя NIC уходит в `awgsocks -bind-device` (SO_BINDTODEVICE). Пусто или интерфейс отсутствует — задача пропускается без ошибки; есть NIC, но без маршрутизируемого IPv4 — задача пропускается без ошибки. В обоих случаях серверный sweeper через 5 минут вернёт задачу в очередь. |
| `SPEEDTEST_URL`    | нет                                                         | URL для замера скорости                                |
| `XRAY_CONFIG_DIR`  | нет (`/tmp`)                                               | Каталог для временных конфигов туннелей               |
| `XRAY_READY_TIMEOUT` | нет (`10`)                                                 | Сколько ждать SOCKS-порт xray (секунды)                |
| `AWGPROXY_BINARY`  | нет (`/usr/local/bin/awgsocks`)                            | Путь к userspace-хелперу AmneziaWG в образе пробы     |
| `AWG_READY_TIMEOUT`| нет (`15`)                                                 | Сколько ждать SOCKS-порт awgsocks (секунды); handshake с сервером AWG добавляет один RTT к старту |
| `LOG_LEVEL`        | нет (`INFO`)                                               | Уровень логирования                                   |