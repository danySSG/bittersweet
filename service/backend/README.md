# tastemap backend

FastAPI-бэкенд сервиса «акустический портрет музыкального вкуса»:
Spotify OAuth (PKCE) → ISRC → 30-сек превью (iTunes/Deezer) → признаки (librosa)
→ кластеры настроений → JSON-портрет. Спецификация: `../../docs/SPEC.md`.

## Требования

- Python 3.12 (закреплён в `.python-version`)
- [uv](https://docs.astral.sh/uv/)

## Запуск

```bash
cd service/backend
uv sync                              # поставить зависимости (создаст .venv)
uv run uvicorn app.main:app --reload # http://localhost:8000
```

Проверка:

```bash
curl localhost:8000/health              # {"status":"ok"}
curl localhost:8000/api/demo/portrait   # JSON-портрет по демо-данным
```

Тесты:

```bash
uv run pytest
```

## Режимы

### Demo-режим (работает сразу, без Spotify)

`GET /api/demo/portrait` читает готовый CSV признаков
(`DEMO_FEATURES_CSV`, по умолчанию `<repo>/data/features_full.csv`)
и строит портрет: кластеры настроений, биттерсвит, карта точек, fingerprint.
Если `SPOTIFY_CLIENT_ID` не задан, `/auth/spotify/login` отвечает `503`
с пояснением — это нормально для demo-режима.

### Spotify-режим

1. `GET /auth/spotify/login` — редирект на авторизацию Spotify (PKCE).
2. `GET /auth/spotify/callback` — обмен кода на токен, токен кладётся
   в подписанную session-cookie, редирект на фронт `/portrait`.
3. `GET /api/portrait` — топ-50 + 50 сохранённых треков → ISRC → кэш-хит
   или превью → анализ → портрет.

Признаки кэшируются по ISRC в SQLite (`cache.db`): популярный трек
анализируется один раз на всех пользователей.

Примечание: превью iTunes приходят в `.m4a` — для их декодирования librosa
может понадобиться ffmpeg (`brew install ffmpeg`).

### Режим «Войти через YouTube» (SPEC v0.5)

1. `GET /auth/google/login` — редирект на consent-экран Google
   (scope `youtube.readonly`, `access_type=offline`, `prompt=consent`).
2. `GET /auth/google/callback` — обмен кода на токены, identity по
   `channels.list(mine=true)`, аккаунт в SQLite (`google_accounts`,
   refresh_token шифруется Fernet-ключом из `SESSION_SECRET`),
   httpOnly session-cookie `{channel_id}`, редирект на
   `{FRONTEND_URL}/portrait?source=youtube`.
3. `GET /api/me` — `{connected, channel_title?}` по cookie.
4. `POST /api/analyze/youtube` (`{limit: <=40}`) — джоба: лайки из
   likes-плейлиста (до 200 свежих) → `videos.list` (фильтр >12 мин и
   приватных/удалённых) → превью-каскад → портрет с источником
   «лайки YouTube». `401` без сессии.
5. `POST /auth/google/disconnect` — revoke токена + удаление аккаунта и
   всех youtube-портретов + снятие cookie (немедленно).

Правило 30 дней (Google Dev Policies III.E.4.c): youtube-портреты старше
30 дней и осиротевшие аккаунты удаляются на старте приложения и в начале
каждой youtube-джобы (`purge_stale_youtube_data`).

Без `GOOGLE_CLIENT_ID` все `/auth/google/*` отвечают `503` с пояснением.

## Настройка Google OAuth / YouTube (по шагам, GCP)

1. Откройте <https://console.cloud.google.com/> и создайте проект
   (например, `tastemap-dev`).
2. Включите API: **APIs & Services → Library → YouTube Data API v3 →
   Enable**.
3. Настройте consent-экран: **APIs & Services → OAuth consent screen**:
   - **User Type** — `External`; заполните имя приложения и почту;
   - **Scopes** — можно не добавлять вручную (мы запрашиваем
     `.../auth/youtube.readonly` в рантайме);
   - **Publishing status** оставьте **Testing** и добавьте себя (и до
     100 человек) в **Test users** — без верификации входить смогут
     только они.
4. Создайте OAuth-клиент: **APIs & Services → Credentials →
   Create Credentials → OAuth client ID**:
   - **Application type** — `Web application`;
   - **Authorized redirect URIs** — ровно
     `http://localhost:8000/auth/google/callback`
     (байт в байт совпадает с `GOOGLE_REDIRECT_URI`).
5. Скопируйте **Client ID** и **Client Secret** в `.env`:

   ```bash
   cp .env.example .env
   # GOOGLE_CLIENT_ID=<ваш Client ID>
   # GOOGLE_CLIENT_SECRET=<ваш Client Secret>
   # SESSION_SECRET=<случайная строка: python -c "import secrets; print(secrets.token_hex(32))">
   ```

6. Перезапустите бэкенд и откройте
   `http://localhost:8000/auth/google/login` — должен произойти редирект
   на consent-экран Google с предупреждением «unverified app» (норма для
   Testing).

Важно про режим **Testing**: refresh-токены живут **7 дней**, потом
Google отвечает `invalid_grant` — сервис удалит аккаунт и попросит войти
заново. Это норма до прохождения бесплатной верификации sensitive-scope
(~2–4 недели, нужен домен и privacy policy — после деплоя).

`youtube.readonly` — sensitive scope: читаем только список лайков
(`playlistItems.list`, 1 unit на 50 треков) и длительности
(`videos.list`); данные из API храним не дольше 30 дней.

## Регистрация Spotify-приложения (по шагам)

1. Откройте <https://developer.spotify.com/dashboard> и войдите со своим
   аккаунтом Spotify (подойдёт обычный бесплатный).
2. Нажмите **Create app**.
3. Заполните форму:
   - **App name** — например, `tastemap-dev`;
   - **App description** — любая строка, например `acoustic taste portrait (dev)`;
   - **Redirect URIs** — добавьте ровно:
     `http://localhost:8000/auth/spotify/callback`
     (нажмите **Add**; URI должен совпадать с `SPOTIFY_REDIRECT_URI` байт в байт);
   - **Which API/SDKs are you planning to use?** — отметьте **Web API**.
4. Примите условия и нажмите **Save**.
5. На странице приложения откройте **Settings** и скопируйте **Client ID**
   (Client Secret для PKCE не обязателен, но можно скопировать про запас).
6. В `service/backend` создайте `.env` из шаблона и заполните:

   ```bash
   cp .env.example .env
   # SPOTIFY_CLIENT_ID=<ваш Client ID>
   # SESSION_SECRET=<случайная строка, например: python -c "import secrets; print(secrets.token_hex(32))">
   ```

7. В режиме разработки Spotify-приложение работает в **Development mode**:
   авторизоваться могут только явно добавленные пользователи. Добавьте себя:
   **Settings → User Management → Add new user** (имя + email аккаунта Spotify).
8. Перезапустите бэкенд и откройте `http://localhost:8000/auth/spotify/login` —
   должен произойти редирект на страницу согласия Spotify
   (scope: `user-top-read user-library-read`).

## Переменные окружения (`.env`)

| Переменная | Назначение | По умолчанию |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` | Client ID OAuth-клиента GCP; пусто = /auth/google/* отвечают 503 | *(пусто)* |
| `GOOGLE_CLIENT_SECRET` | Client Secret OAuth-клиента GCP | *(пусто)* |
| `GOOGLE_REDIRECT_URI` | callback-URI, как в GCP Credentials | `http://localhost:8000/auth/google/callback` |
| `FRONTEND_URL` | точный origin фронта (CORS + redirect после OAuth) | `http://localhost:3000` |
| `SPOTIFY_CLIENT_ID` | Client ID приложения Spotify; пусто = demo-режим | *(пусто)* |
| `SPOTIFY_CLIENT_SECRET` | не требуется для PKCE, зарезервирован | *(пусто)* |
| `SPOTIFY_REDIRECT_URI` | callback-URI, как в дашборде Spotify | `http://localhost:8000/auth/spotify/callback` |
| `SESSION_SECRET` | секрет подписи session-cookie + ключ шифрования refresh_token (HKDF) | `dev-secret-change-me` |
| `DEMO_FEATURES_CSV` | CSV признаков для demo-портрета | `<repo>/data/features_full.csv` |

## Структура

```
app/
  main.py            # FastAPI, CORS (localhost:3000), session-cookie
  config.py          # настройки (pydantic-settings, .env)
  pipeline.py        # tracks -> (кэш | превью -> analyze) -> портрет
  routes/            # /health, /auth/spotify/*, /api/{demo/}portrait
  engine/
    features.py      # librosa-признаки (Крумхансл-Шмуклер и др.)
    portrait.py      # перцентили, KMeans, метки кластеров, биттерсвит
    previews.py      # ISRC -> превью (iTunes / Deezer)
    spotify.py       # OAuth PKCE + выгрузка библиотеки
    cache.py         # SQLite-кэш признаков по ISRC
tests/               # pytest (uv run pytest)
```
