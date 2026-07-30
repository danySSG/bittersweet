# tastemap — спецификация MVP (рабочее название)

Онлайн-сервис «акустический портрет музыкального вкуса».
Выросло из ночного эксперимента: анализ 448 лайков → карта настроений → биттерсвит →
сигнатуры. Теперь — продукт.

## Ключевое архитектурное решение

**Развязать «где логинятся» и «откуда аудио»:**
- Логин + библиотека → Spotify OAuth (PKCE): топ-треки, saved tracks, `external_ids.isrc`.
- Аудио для анализа → 30-сек превью из iTunes Search API / Deezer (матч по ISRC → фолбэк по artist+title).
- Признаки считаем сами (librosa, наш движок) и **кэшируем по ISRC** — популярный трек
  анализируется один раз на всех пользователей.

## Структура репозитория

```
music/
  analyze_audio.py … deep.py     # исходный экспериментальный конвейер (не трогаем)
  data/                          # локальные данные эксперимента (gitignored)
  docs/
    SPEC.md                      # этот файл
    ARCHITECTURE.md              # архитектура + роадмап + риски (пишет агент)
  service/
    backend/                     # FastAPI (Python 3.12, uv)
    frontend/                    # Next.js 15 (App Router)
```

## Backend (service/backend)

Python 3.12, uv-проект, FastAPI + uvicorn. НЕ трогает корневой pyproject.toml —
свой собственный проект.

```
service/backend/
  pyproject.toml            # fastapi, uvicorn, httpx, librosa, numpy, pandas,
                            # scikit-learn, soundfile, python-dotenv, itsdangerous, pytest
  .env.example              # SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI,
                            # SESSION_SECRET, DEMO_FEATURES_CSV (default ../../data/features_full.csv)
  README.md                 # запуск, регистрация Spotify-приложения по шагам
  app/
    main.py                 # FastAPI app, CORS для localhost:3000, session-cookie (itsdangerous)
    config.py               # pydantic-settings / env
    routes/
      health.py             # GET /health -> {status:"ok"}
      auth.py               # GET /auth/spotify/login (redirect), GET /auth/spotify/callback
      portrait.py           # GET /api/portrait  (требует сессию — Spotify-режим)
                            # GET /api/demo/portrait (без логина — считает по DEMO_FEATURES_CSV)
    engine/
      features.py           # адаптация analyze_audio.py: analyze(path)->dict признаков
                            #   (tempo, key, mode, minor_score, bittersweet, percussive,
                            #    energy_rms, brightness, onset_rate) — самодостаточная копия,
                            #   НЕ импорт из корня
      portrait.py           # перцентили, energy/valence, KMeans-кластеры с человеческими
                            #   метками (адаптация cluster.py/deep.py), биттерсвит-категория,
                            #   выход = JSON под фронт (см. контракт ниже)
      previews.py           # match_isrc_to_preview(isrc, artist, title) -> preview_url|None
                            #   iTunes Search API (lookup по isrc, фолбэк search term),
                            #   Deezer /track/isrc: как второй источник; httpx, таймауты, ретраи
      spotify.py            # OAuth PKCE + GET /me/top/tracks, /me/tracks -> [{isrc, artist, title}]
      cache.py              # SQLite (backend/cache.db): таблица features(isrc PK, json, created_at)
    pipeline.py             # orchestrate: tracks -> (cache | preview -> analyze) -> portrait
  tests/
    test_health.py          # TestClient: /health == 200
    test_demo_portrait.py   # /api/demo/portrait == 200, в ответе >=2 кластера, есть points
    test_engine.py          # portrait.build_portrait на синтетическом df из 40 строк
```

### Контракт /api/*/portrait (JSON)

```json
{
  "n_tracks": 375,
  "clusters": [
    {"label": "энергично-меланхоличное", "size": 67, "share": 18,
     "medians": {"tempo": 157, "minor_share": 90, "brightness": 1916},
     "examples": ["artist — title", "..."] , "color": "#7F77DD"}
  ],
  "bittersweet": {"count": 44, "share": 12, "top": ["artist — title"]},
  "points": [{"x": 12.3, "y": 45.6, "cluster": 0, "label": "artist — title",
              "meta": "157bpm · A minor"}],
  "fingerprint": {"tempo_median": 118, "minor_share": 65, "brightness_mean": 1800}
}
```

points: x = valence-перцентиль 0..100, y = energy-перцентиль 0..100 (MVP без UMAP —
детерминированно и без тяжёлой зависимости; UMAP в v2).

### Demo-режим (критично для MVP)

`GET /api/demo/portrait` работает БЕЗ Spotify: читает готовый CSV признаков
(DEMO_FEATURES_CSV -> data/features_full.csv из эксперимента) и строит портрет.
Это позволяет разрабатывать/показывать фронт до регистрации Spotify-приложения.

### Spotify-режим

- PKCE flow (без client_secret на фронте), scope: `user-top-read user-library-read`.
- После callback: тянем топ-50 (medium_term) + saved 50, собираем isrc.
- pipeline: для каждого isrc — кэш-хит или (превью -> временный файл -> analyze -> в кэш).
- Анализ синхронный последовательный в MVP (50 треков × ~2 сек = приемлемо);
  очередь (RQ/Celery) — v2.

## Frontend (service/frontend)

Next.js 15 (App Router, TS), без UI-библиотек — свой CSS в духе наших артефактов
(тёмный #0b0b10, фиолетовый #7F77DD, моно-шрифт для цифр).

```
app/
  page.tsx            # лендинг: заголовок, "Попробовать демо" -> /portrait?demo=1,
                      # "Войти со Spotify" -> {API}/auth/spotify/login
  portrait/page.tsx   # клиентский фетч /api/demo/portrait или /api/portrait,
                      # скаттер-карта (SVG, точки по x/y, тултип), карточки кластеров,
                      # блок биттерсвит, кнопка "поделиться" (пока copy-link)
lib/api.ts            # базовый URL из NEXT_PUBLIC_API_URL (default http://localhost:8000)
```

## Definition of Done (MVP-скаффолд)

1. `cd service/backend && uv run pytest` — зелёный.
2. `uv run uvicorn app.main:app` + `curl localhost:8000/api/demo/portrait` — валидный JSON контракта.
3. `cd service/frontend && npm run build` — успешно.
4. Фронт на localhost:3000 показывает демо-портрет с живого бэка.
5. README.md бэка содержит пошаговую регистрацию Spotify-приложения (developer.spotify.com,
   redirect URI http://localhost:8000/auth/spotify/callback) и инструкцию запуска.

## Не в MVP (v2+)

Postgres, очередь задач, UMAP, стемы/Demucs (юридически рискованно — исключено из продукта),
рекомендации, share-cards как картинки, деплой (Vercel + Fly/Railway), мониторинг.
