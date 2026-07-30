# SPEC v0.2 — источник «YouTube Music: ссылка на плейлист»

Дополнение к docs/SPEC.md (v0.1 построен и зелёный). Цель: пользователь вставляет ссылку
на публичный/unlisted плейлист YouTube Music и получает СВОЙ портрет — без логина.

Три ступени YT Music как источника (в этом релизе — только первая):
1. **v0.2 (этот спек): плейлист-ссылка** — анонимное чтение метаданных публичного плейлиста.
2. v0.2+: импорт Google Takeout (пользователь сам выгружает свои данные).
3. v0.3: официальный YouTube Data API v3 OAuth (scope youtube.readonly) — кнопка «Войти через YouTube».

## Конвейер

```
ссылка на плейлист → ytmusicapi (анонимно) → [{artist, title, videoId, duration}]
  → фильтр длинных (>12 мин — это mix/ambient-простыни, не песни)
  → на каждый трек: кэш-хит ИЛИ (превью → librosa-анализ в памяти → в кэш)
  → build_portrait → тот же JSON-контракт, что /api/demo/portrait
```

ВАЖНО: у треков из YT Music НЕТ ISRC ⇒ матч превью по artist+title, каскад:
1. **Deezer search** `https://api.deezer.com/search?q=artist:"{artist}" track:"{title}"`
   (фолбэк — простой `q="{artist} {title}"`), поле `preview`. Лимит 50 req/5s — держать ≤8 req/s.
2. **iTunes search** по term (как сейчас). Лимит ~20 req/min — держать ≥3.1 сек между вызовами
   (module-level троттлинг в previews.py).

## Backend (service/backend) — изменения

- `pyproject.toml`: + `ytmusicapi`.
- `app/engine/previews.py`:
  - добавить `_deezer_search_preview(client, artist, title) -> url|None`;
  - `match_track_to_preview(isrc, artist, title)` — новый порядок: (isrc→Deezer-ISRC) →
    Deezer-search → iTunes-search; старое имя `match_isrc_to_preview` оставить алиасом;
  - троттлинг: iTunes ≥3.1s между вызовами, Deezer ≤8/s (time.monotonic, module-level lock).
- `app/engine/cache.py`: обобщить ключ — колонка `key TEXT PRIMARY KEY`;
  `key_for(isrc, artist, title)` → `isrc:{ISRC}` если есть, иначе `at:{norm(artist)}|{norm(title)}`,
  norm = lower + схлопнуть пробелы + убрать скобочные хвосты вида "(slowed)", "(feat. …)".
  Старую базу не мигрировать (MVP): при несовпадении схемы пересоздать файл.
- `app/sources/__init__.py`, `app/sources/ytmusic.py`:
  `fetch_playlist(url_or_id, limit) -> list[Track]` анонимным `YTMusic()`;
  разбор ссылки (`list=` параметр или голый id); типизированные ошибки:
  `PlaylistNotFound`, `PlaylistPrivate` (ytmusicapi кидает исключение — маппить).
- Джобы (анализ занимает десятки секунд — нельзя держать HTTP-запрос):
  - `app/jobs.py`: in-memory store `{job_id: {status: queued|running|done|error,
    progress: {done, total, matched}, portrait: dict|None, error: str|None}}`,
    uuid4-ключи, threading.Lock; исполнение через `fastapi.BackgroundTasks` (без Celery в MVP).
  - `POST /api/analyze/playlist` body `{url: str, limit: int<=40 (default 25)}` →
    202 `{job_id}`. Валидация url → 422 с человеческим сообщением.
  - `GET /api/jobs/{job_id}` → полное состояние джобы; 404 если нет.
  - В джобе: скачивать превью во временный файл/память, после анализа УДАЛЯТЬ
    (аудио не хранится — принцип из ARCHITECTURE.md); треки без превью считать
    в `progress.matched`; если проанализировано <8 треков → status=error
    («слишком мало треков сматчилось»).
- `app/pipeline.py`: `analyze_tracks(tracks, progress_cb) -> DataFrame` — общий путь
  для playlist-джобы; переиспользует features.analyze + cache.

## Frontend (service/frontend) — изменения

- Лендинг: под кнопками — поле «Ссылка на плейлист YouTube Music» + кнопка
  «Построить портрет» → `/portrait?playlist={encodeURIComponent(url)}`.
- `/portrait`: если есть `?playlist=` — POST `/api/analyze/playlist`, дальше поллинг
  `/api/jobs/{id}` каждые 2s; прогресс-бар «проанализировано N из M» в стиле страницы;
  при done — тот же рендер портрета; при error — человеческое сообщение
  (приватный плейлист / мало треков / бэк недоступен). Demo-режим не трогать.
- `lib/types.ts`: типы Job/JobProgress.

## Тесты (без сети! monkeypatch)

- `tests/test_previews_cascade.py`: с фейковым httpx-клиентом проверить порядок:
  (a) при isrc → Deezer-ISRC первым; (b) без isrc → Deezer-search первым, iTunes вторым;
  (c) all-fail → None.
- `tests/test_cache_key.py`: key_for — isrc-приоритет, нормализация артиста/названия,
  "(slowed + reverb)"-хвосты.
- `tests/test_playlist_job.py`: monkeypatch fetch_playlist (4 синтетических трека) +
  match→фейковый url + analyze→синтетические признаки; POST → 202; поллить TestClient'ом
  до done; в portrait валидный контракт; кейс приватного плейлиста → job error.

## Definition of Done

1. `uv run pytest` — все старые + новые тесты зелёные, БЕЗ сетевых вызовов.
2. `npm run build` — зелёный.
3. Живой smoke (с сетью, вне pytest): POST реального плейлиста
   `https://music.youtube.com/playlist?list=PLfoNjwA6k-mY` (4 трека) → job доходит
   до done, портрет строится (или до error с внятной причиной, если <8 треков —
   тогда проверить на любом публичном плейлисте YT Music ≥15 треков и приложить job_id/итог).
4. Треки >12 мин отфильтровываются; превью-файлы после анализа удалены (проверить tmp).
5. v0.1-поведение не сломано: /api/demo/portrait и demo-путь фронта работают как раньше.
