# Frontend

SvelteKit 2 + Svelte 5 (runes) + Tailwind 4 UI для XSpeed.

Public URL: http://localhost:5173 (dev) / http://localhost:3000 (prod via nginx).

## Запуск

```sh
pnpm install
pnpm dev
```

Поддерживает проксирование на backend:

- `localhost:5173/api/*` → `localhost:8000/api/*`
- `localhost:5173/ws` → `localhost:8000/ws`

## Build

```sh
pnpm build
pnpm preview
```

## Структура

- `src/routes/` — публичные маршруты (`/`, `/docs`, `/admin`, `/admin/login`)
- `src/lib/components/` — `AppShell`, `Map`, `ProbeForm`, `ResultsTable`, `StatusDot`
- `src/lib/api/` — `tasks.ts`, `probes.ts`, `ws.svelte.ts` (singleton WS с reconnect + backoff)
- `src/lib/stores/` — `locale.svelte.ts`, `auth.svelte.ts` (Svelte 5 runes)
- `src/lib/locale/` — `ru.json`, `en.json`
- `static/regions-layout.json` — позиции субъектов РФ на схематической SVG-карте

## Переменные окружения

См. `.env.example`. Публичные (доступны в браузере) значения должны
начинаться с `PUBLIC_`:

- `PUBLIC_API_BASE` — префикс REST API (по умолчанию `/api`)
- `PUBLIC_WS_BASE` — префикс WebSocket (по умолчанию `/ws`)

## Переключение языка

В шапке доступна кнопка `RU ⇄ EN`. Состояние сохраняется в `localStorage`
под ключом `xspeed:locale` и зеркалится в `<html lang>`.