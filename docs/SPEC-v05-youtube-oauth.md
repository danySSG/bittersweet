# SPEC v0.5 — «Войти через YouTube»: официальный OAuth без костылей

Основа: исследование 2026-07-10 (Google OAuth sensitive-верификация, Data API, политики).
Ключевые факты, на которых строимся:
- `youtube.readonly` — sensitive scope: бесплатная верификация (~2-4 нед), до неё — Testing:
  ≤100 test users, refresh-токены живут 7 дней (для MVP-теста достаточно).
- Лайки читаются: `channels.list(mine=true)` → `contentDetails.relatedPlaylists.likes` →
  `playlistItems.list` (1 unit/50 треков). Лайки из YT Music попадают в этот список.
- ПОЛИТИКА 30 ДНЕЙ (Dev Policies III.E.4.c): данные из API храним ≤30 дней, потом удалить
  или обновить. По запросу пользователя — удалить за 7 дней (делаем немедленно).
- Названия приходят как названия видео; артист = videoOwnerChannelTitle с суффиксом
  « - Topic» у авто-треков (наш парсер уже есть в resolver.py — переиспользовать).

## A. Backend

### A1. Конфиг и жизненный цикл

- env: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`
  (default http://localhost:8000/auth/google/callback), `FRONTEND_URL`
  (default http://localhost:3000). Без client_id все /auth/google/* отвечают 503 с
  человеческим JSON (как у Spotify-заглушки).
- README: пошаговая настройка GCP (создать проект → включить YouTube Data API v3 →
  OAuth consent screen External, Testing, добавить test users → создать OAuth client
  Web application с redirect URI → вписать ключи в .env). Отметить: в Testing
  refresh-токены умирают через 7 дней — это норма до верификации.

### A2. OAuth-флоу (Authorization Code + state)

- `GET /auth/google/login` → redirect на accounts.google.com: scope=`https://www.googleapis.com/auth/youtube.readonly`,
  access_type=offline, prompt=consent, `state` = подписанный itsdangerous токен (CSRF).
- `GET /auth/google/callback` → проверка state → обмен code на токены (httpx, POST
  oauth2.googleapis.com/token) → identity: `channels.list(part=snippet,contentDetails&mine=true)`
  → channel_id = ключ пользователя → сохранить в SQLite таблицу
  `google_accounts(channel_id PK, channel_title, refresh_token_enc, access_token,
  token_expiry, created_at, likes_playlist_id)`; refresh_token шифровать (Fernet, ключ
  выводится из SESSION_SECRET). Выставить httpOnly signed session-cookie {channel_id}
  → redirect на `{FRONTEND_URL}/portrait?source=youtube`.
- Обновление access_token по refresh_token при истечении (helper с обработкой
  invalid_grant → аккаунт считать отключённым, чистить).
- `GET /api/me` → {connected: bool, channel_title?} по session-cookie.
- `POST /auth/google/disconnect` → revoke (oauth2.googleapis.com/revoke) + удалить
  строку аккаунта + удалить все портреты этого channel_id с source='youtube' + снять
  cookie. Немедленно (правило 7 дней перекрываем с запасом).

### A3. Источник «лайки YouTube»

- `POST /api/analyze/youtube` body `{limit: int<=40 default 40}`; 401 без сессии.
  Джоба (существующая инфраструктура, stage'ы: fetching → analyzing):
  1. playlistItems.list по likes-плейлисту, паджинация до 200 свежих;
  2. videos.list батчами по 50 (part=contentDetails) → duration; фильтр >12 мин
     и приватных/удалённых;
  3. нормализация: title + videoOwnerChannelTitle, срез « - Topic» (переиспользовать
     функцию из resolver.py — вынести в общий модуль, не копировать);
  4.限 limit свежих → существующий конвейер (превью-каскад → analyze → портрет);
  5. портрет сохраняется с source_label «лайки YouTube», в payload — `api_fetched_at`
     (ISO) и `source: "youtube"`, привязка к channel_id (колонка owner в portraits).
- ПРАВИЛО 30 ДНЕЙ: функция `purge_stale_youtube_data()` — удаляет youtube-портреты
  старше 30 дней и осиротевшие аккаунты; вызывается на старте приложения и в начале
  каждой youtube-джобы. Тест на неё обязателен.

### A4. Тесты (все без сети: respx или monkeypatch httpx)

- oauth: login формирует корректный URL (scope/state/offline); callback с плохим state
  → 400; happy-path с замоканными token+channels → cookie выставлена, аккаунт в БД,
  refresh_token зашифрован (в БД не встречается плоско);
- refresh: истёкший access → рефреш; invalid_grant → аккаунт удалён, 401;
- джоба: замоканные playlistItems (2 страницы) + videos (длинные фильтруются) +
  превью/analyze → done, портрет с source_label и api_fetched_at; « - Topic» срезан;
- disconnect: revoke вызван, аккаунт и youtube-портреты удалены, cookie снята;
- purge: портрет старше 30 дней удаляется, свежий и не-youtube — нет;
- 503-ветки без GOOGLE_CLIENT_ID.

## B. Frontend

- Лендинг: кнопка «Войти со Spotify» ЗАМЕНЯЕТСЯ на «Войти через YouTube» (primary
  рядом с «Попробовать демо») → {API}/auth/google/login. Мелкая подпись под кнопкой:
  «официальный вход Google · читаем только лайки · отключение в один клик».
- `/portrait?source=youtube`: вызывает GET /api/me; если connected → POST
  /api/analyze/youtube → существующий поллинг/прогресс → портрет; если нет —
  человеческое состояние с кнопкой входа. 503 от бэка → подсказка «бэкенд не настроен
  (см. README: настройка Google OAuth)».
- Шапка страницы портрета: если /api/me connected — чип «YouTube: {channel_title}» +
  кнопка «Отключить» (confirm → POST disconnect → редирект на лендинг). fetch с
  credentials: 'include'; CORS на бэке — allow_credentials + точный origin (не *).
- Types: опционально, без падений на старом бэке.

## C. Definition of Done

1. pytest зелёный (старые + новые), без сети.
2. Мокированный e2e через TestClient: login-URL → callback (мок Google) → cookie →
   POST /api/analyze/youtube (мок API + мок превью/analyze) → done → портрет с
   source_label «лайки YouTube» → GET /api/p/{id} → disconnect → портрет удалён.
3. Фронт: сборка по правилу (dev-сервер!), SSR-проверки: лендинг с кнопкой YouTube
   (и БЕЗ кнопки Spotify), /portrait?source=youtube отдаёт 200.
4. Без реальных GOOGLE_* креденшалов все ветки деградируют в понятные 503/подсказки
   (проверить curl'ом живого бэка).
5. README-чеклист настройки GCP полный (по нему пойдёт основатель).
6. Прежние потоки (демо/плейлист/Takeout/discovery/пермалинки/compare) не сломаны.

## Вне скоупа (зафиксировать в ARCHITECTURE как next)

Автообновление youtube-портретов фоном (сейчас: purge + повторный анализ вручную),
верификация приложения Google (нужен домен + privacy policy — после деплоя v0.3
роадмапа), multi-account.
