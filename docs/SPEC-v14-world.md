# SPEC v0.14 — «В мир»: ребрендинг Bittersweet + продакшен-готовность

Решение основателя: выводим продукт в паблик. Имя утверждено: **Bittersweet** 🍬.
Эта волна готовит код; сам деплой — следующий шаг (нужны аккаунты основателя).

## A. Ребрендинг tastemap → Bittersweet (frontend)

- Все ПОЛЬЗОВАТЕЛЬСКИЕ строки: wordmark «TASTEMAP» на лендинге → «BITTERSWEET»;
  «← tastemap» назад-ссылки → «← bittersweet»; layout.tsx: title «bittersweet —
  акустический портрет твоего вкуса», description без изменения смысла;
  share-card водяной знак «tastemap · /p/{id}» → «bittersweet · /p/{id}»;
  квиз-шаринг «— bittersweet»; финал истории и любые упоминания.
- Идентификаторы кода/пути НЕ трогать (никаких переименований директорий/пакетов).
- package.json name → "bittersweet-frontend"; backend pyproject name → "bittersweet-backend"
  (безопасно: на импорты не влияет).
- Грep-тест ребрендинга: в собранных чанках и SSR-html НЕ должно остаться «tastemap»
  (кроме как в коде-комментариях/спеках docs/, их не трогаем).

## B. Продакшен-готовность backend

- `service/backend/Dockerfile`: python:3.12-slim + ffmpeg (декодер m4a/mp3!) +
  uv (copy from ghcr.io/astral-sh/uv) + uv sync --frozen --no-dev; uvicorn app.main:app
  --host 0.0.0.0 --port ${PORT:-8000}. Слои кэшируемо: сначала pyproject+uv.lock.
- `service/backend/.dockerignore`: .venv, cache.db, .env, tests, __pycache__.
- Конфиг: убедиться, что ВСЁ внешнее — из env (cache_db путь, FRONTEND_URL,
  GOOGLE_*, SESSION_SECRET); grep на захардкоженный localhost вне дефолтов config.py.
- Cookies за HTTPS-прокси: настройка `cookie_secure: bool = False` в Settings
  (env COOKIE_SECURE=1 на проде) → session-cookie ставится с Secure; samesite
  остаётся lax. Тест: с флагом кука содержит Secure.
- healthcheck остаётся GET /health.

## C. Privacy + футер (frontend) — фундамент для верификации Google

- Страница `/privacy` (статическая, на русском): что собираем (метаданные лайков
  через официальный YouTube API — названия/каналы/даты; НЕ собираем: аудио, историю
  просмотров, персональные данные сверх канала), как храним (дескрипторы звука из
  30-сек превью открытых каталогов; данные API — не дольше 30 дней без обновления),
  отключение (кнопка «Отключить» — немедленное удаление токенов и youtube-портретов),
  превью iTunes/Deezer не сохраняются, контакт: danyfomin003@gmail.com. Честно,
  коротко, без юридического тумана; дата версии.
- Футер на лендинге и странице портрета: «bittersweet · политика данных · GitHub?»
  (ссылка /privacy; GitHub-ссылку не ставить — репо приватный пока).
- В consent-заглушке логина упоминание «читаем только лайки» уже есть — согласовать
  формулировку с /privacy.

## D. DEPLOY.md (runbook-скелет)

- docs/DEPLOY.md: пошагово — переменные окружения бэка (таблица), сборка Docker
  локально для проверки, деплой бэка (fly launch/deploy с volume для cache.db —
  конкретные цены/команды пометить TODO до итогов hosting-исследования), деплой
  фронта на Vercel (env NEXT_PUBLIC_API_URL), обновление GOOGLE_REDIRECT_URI и
  origins в Google Console, смок-чеклист после деплоя, замечание про ротацию
  client_secret (светился в чате при настройке).

## E. Definition of Done

1. pytest зелёный (+ тест Secure-куки); tsc + NEXT_BUILD_DIR-сборка зелёные.
2. Грep: «tastemap» отсутствует в SSR-html лендинга/портрета и собранных чанках.
3. /privacy → 200, содержательна, слово «лайки» есть, жаргона нет; футер-ссылка видна.
4. Dockerfile собирается ЕСЛИ docker доступен в окружении (проверить `docker version`;
   если демона нет — синтаксис-ревью + hadolint-подобный чек-лист вручную, пометить
   в отчёте «сборка не проверена: нет докера»).
5. Живые серверы (:8000/:3000) не трогать; прежние потоки не сломаны (вся сюита).
