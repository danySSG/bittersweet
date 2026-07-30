# DEPLOY.md — runbook деплоя Bittersweet (скелет, SPEC v0.14 §D)

Статус: скелет до итогов hosting-исследования. Места, где ждём выбора
хостинга/тарифа, помечены **TODO(hosting)** — не выполнять вслепую.

Состав:

- **Backend** — FastAPI (`service/backend`), контейнер из `service/backend/Dockerfile`,
  состояние в одном файле SQLite (`cache.db`) → нужен персистентный volume.
- **Frontend** — Next.js (`service/frontend`) → Vercel.

---

## 1. Переменные окружения бэка

| Переменная | Обязательна на проде | Значение на проде | Комментарий |
|---|---|---|---|
| `SESSION_SECRET` | да | случайная строка (`openssl rand -hex 32`) | Подпись session-cookie **и** ключ шифрования refresh-токенов Google (HKDF). Смена = разлогин всех. Хранить как секрет, не в `fly.toml`. |
| `COOKIE_SECURE` | да | `1` | Secure-флаг session-cookie за HTTPS-прокси. Локалка — `0` (дефолт). |
| `FRONTEND_URL` | да | `https://<фронт-домен>` | Точный origin: CORS с credentials + redirect после OAuth. Без слэша в конце. |
| `GOOGLE_CLIENT_ID` | да | из Google Cloud Console | Пусто = `/auth/google/*` отвечают 503 (демо-режим работает). |
| `GOOGLE_CLIENT_SECRET` | да | из Google Cloud Console | Секрет. **См. §6 — ротация обязательна до деплоя.** |
| `GOOGLE_REDIRECT_URI` | да | `https://<бэк-домен>/auth/google/callback` | Должен буква в букву совпадать с Google Console (§4). |
| `CACHE_DB` | да | `/data/cache.db` | Путь SQLite на примонтированном volume. Дефолт `/app/cache.db` в контейнере эфемерен! |
| `PORT` | нет | `8000` | Порт uvicorn; читается CMD контейнера (`${PORT:-8000}`). |
| `DEMO_FEATURES_CSV` | нет | путь к CSV на volume | Демо-портрет. CSV в образ не попадает (лежит в `data/` вне build-контекста) — залить на volume или оставить демо выключенным. |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` / `SPOTIFY_REDIRECT_URI` | нет | — | Spotify-ветка; пусто = spotify-логин выключен, остальное работает. |

Всё внешнее конфигурируется только через env (см. `app/config.py`) —
захардкоженных `localhost` вне дефолтов конфига в коде нет (проверено grep'ом).

## 2. Локальная проверка Docker-сборки

Из `service/backend`:

```sh
docker build -t bittersweet-backend .
docker run --rm -p 8000:8000 \
  -e SESSION_SECRET=local-test -e COOKIE_SECURE=0 \
  bittersweet-backend

curl -s http://localhost:8000/health           # ожидаем {"status":"ok"}
curl -si http://localhost:8000/auth/google/login | head -1   # 503 без GOOGLE_* — норм
```

Проверить, что ffmpeg в образе жив (нужен для m4a-превью):

```sh
docker run --rm --entrypoint ffmpeg bittersweet-backend -version | head -1
```

> Примечание: в окружении, где готовилась эта волна, docker-демона не было —
> сборка образа локально не прогонялась, Dockerfile прошёл ручное ревью.
> Перед первым деплоем выполнить этот раздел на машине с Docker.

## 3. Деплой бэка — Fly.io (кандидат)

**TODO(hosting):** выбор Fly.io предварительный; тарифы/регион/размер машины —
после hosting-исследования. Ниже — форма команд, не финальные значения.

```sh
cd service/backend
fly launch --no-deploy            # сгенерирует fly.toml из Dockerfile; TODO(hosting): регион, размер VM
fly volumes create bittersweet_data --size 1   # TODO(hosting): размер и цена volume
fly secrets set \
  SESSION_SECRET=... \
  GOOGLE_CLIENT_ID=... \
  GOOGLE_CLIENT_SECRET=... \
  GOOGLE_REDIRECT_URI=https://<app>.fly.dev/auth/google/callback \
  FRONTEND_URL=https://<фронт-домен> \
  COOKIE_SECURE=1 \
  CACHE_DB=/data/cache.db
fly deploy
```

В `fly.toml` понадобится секция mounts (volume → `/data`) и http-check на
`GET /health`. **TODO(hosting):** конкретный `fly.toml` — после `fly launch`.

Важно про SQLite: один writer — значит **одна машина** (`fly scale count 1`),
без авто-клонов. Масштабирование за пределы одной машины = отдельное решение
(Litestream/LiteFS или Postgres), сейчас вне скоупа.

## 4. Деплой фронта — Vercel

1. Импортировать репозиторий, root directory: `service/frontend`.
2. Env: `NEXT_PUBLIC_API_URL=https://<бэк-домен>` (инлайнится в сборку —
   после смены значения нужен redeploy).
3. Прод-домен фронта должен совпасть с `FRONTEND_URL` бэка (CORS + OAuth-redirect).

## 5. Google Cloud Console (OAuth-клиент)

В APIs & Services → Credentials → OAuth 2.0 Client:

- **Authorized redirect URIs**: добавить `https://<бэк-домен>/auth/google/callback`
  (буква в букву = `GOOGLE_REDIRECT_URI`); локальный
  `http://localhost:8000/auth/google/callback` можно оставить для разработки.
- **Authorized JavaScript origins**: `https://<фронт-домен>` (и `https://<бэк-домен>`,
  если Google потребует origin инициатора).
- OAuth consent screen: ссылка на privacy policy — `https://<фронт-домен>/privacy`
  (страница из SPEC v0.14 §C, фундамент верификации приложения).

## 6. Ротация GOOGLE_CLIENT_SECRET — до деплоя, обязательно

Текущий client_secret **светился открытым текстом в чате** при первичной
настройке — считать скомпрометированным.

1. Google Console → Credentials → OAuth client → **Reset secret**
   (или создать новый client и удалить старый).
2. Новый секрет — только в секреты хостинга (`fly secrets set ...`) и локальный
   `.env` (он в `.gitignore` и `.dockerignore`).
3. Старый секрет после ротации недействителен; пользователи заново не логинятся
   (refresh-токены живут, секрет участвует только в обмене токенов сервером —
   но проверить логин по смоку §7).

## 7. Смок-чеклист после деплоя

- [ ] `GET https://<бэк>/health` → 200 `{"status":"ok"}`.
- [ ] Фронт открывается по `https://<фронт-домен>`, лендинг рисует галактику.
- [ ] `GET https://<бэк>/api/me` из браузера фронта → `{"connected": false}`,
      без CORS-ошибок в консоли.
- [ ] Логин через YouTube: consent → redirect на `/portrait?source=youtube`;
      в DevTools у cookie `session` стоят `Secure`, `HttpOnly`, `SameSite=Lax`.
- [ ] Анализ лайков доходит до готового портрета; превью играют (m4a → ffmpeg жив).
- [ ] Демо-портрет: если `DEMO_FEATURES_CSV` задан — `GET /api/demo/portrait` → 200;
      если нет — осознанно принять, что демо выключено.
- [ ] «Отключить» — удаляет аккаунт (`/api/me` снова `connected: false`).
- [ ] Рестарт машины бэка → портреты на месте (volume примонтирован, `CACHE_DB` верный).
- [ ] `/privacy` → 200, футер-ссылка на месте.

## 8. Что осталось до «в мире» (сводка TODO)

- **TODO(hosting):** итог hosting-исследования → финальные команды/цены в §3.
- **TODO:** залить `features_full.csv` на volume + `DEMO_FEATURES_CSV` (или решить жить без демо).
- **TODO:** ротация client_secret (§6) — до первого публичного анонса.
- **TODO:** верификация OAuth-приложения Google (unverified-экран до неё).
