"""Долговечные джобы анализа (MVP: без Celery/RQ, но с SQLite под капотом).

Горячее состояние — in-memory dict {job_id: {status: queued|running|done|error,
progress: {done, total, matched, ...}, portrait, portrait_id, error,
started_at, rate_per_min}}, uuid4-ключи, threading.Lock. Исполнение —
fastapi.BackgroundTasks.

Каждое обновление пишется СКВОЗЬ память в таблицу jobs той же SQLite
(app.jobs_store): переходы статусов — немедленно, прогресс — не чаще раза
в PROGRESS_WRITE_INTERVAL сек на джобу (диск не молотим). Инцидент-мотивация:
сервер умер посреди безлимитного анализа 437 треков — in-memory джоба
погибла, хотя кэш дескрипторов уцелел.

Анализ занимает десятки секунд (а безлимитная youtube-джоба — возможно, часы)
— HTTP-запрос держать нельзя: POST сразу отвечает 202 {job_id}, клиент поллит
GET /api/jobs/{job_id}. После рестарта GET при промахе памяти читает БД
(тот же job_id продолжает жить), а resume_pending_jobs (lifespan)
перезапускает незавершённые джобы С НАЧАЛА: конвейер идемпотентен благодаря
кэшу дескрипторов — готовые треки промотаются кэш-хитами. Перезапущенные
джобы несут progress.resumed = true; джобы старше STALE_AFTER — error.

Виды джоб: playlist (v0.2, progress без stage), takeout (v0.3, stage:
parsing/resolving/analyzing), discovery (v0.4, stage: seeding/radio/analyzing;
результат — в поле result done-джобы), youtube (v0.5-v0.7, stage:
fetching/analyzing + progress.music_found).
"""

from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import partial

from app import jobs_store, pipeline
from app.engine import discovery, google_accounts, google_oauth, resolver, store
from app.engine.portrait import build_portrait
from app.sources import takeout, youtube_api, ytmusic

log = logging.getLogger(__name__)

# минимум проанализированных треков, чтобы вообще пытаться строить портрет
MIN_MATCHED = 12  # столько требует build_portrait (MIN_TRACKS) — порог честный, не скрытый

_lock = threading.Lock()
_jobs: dict[str, dict] = {}

# запись прогресса в SQLite — не чаще раза в PROGRESS_WRITE_INTERVAL сек на
# джобу; переходы статусов пишутся немедленно (см. _update)
PROGRESS_WRITE_INTERVAL = 2.0
_last_write: dict[str, float] = {}
_clock: Callable[[], float] = time.monotonic  # монотонные часы (подменяются в тестах)


class RateMeter:
    """Скользящая скорость анализа: треков/мин по последним <= window тикам.

    SPEC v0.7 §A2: фронт считает ETA длинной джобы по rate_per_min. Окно 20
    сглаживает разброс (кэш-хиты мгновенны, iTunes-троттлинг ~3 с на трек).
    clock инъецируется для детерминированных тестов.
    """

    def __init__(
        self, window: int = 20, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._times: deque[float] = deque(maxlen=window)
        self._clock = clock

    def tick(self) -> float | None:
        """Отметить обработанный трек; вернуть текущую скорость (или None)."""
        self._times.append(self._clock())
        return self.rate_per_min()

    def rate_per_min(self) -> float | None:
        """Треков/мин по окну; None, пока тиков < 2 (нечего считать)."""
        if len(self._times) < 2:
            return None
        elapsed = self._times[-1] - self._times[0]
        if elapsed <= 0:
            return None
        return round((len(self._times) - 1) / elapsed * 60, 1)


def _new_state(stage: str | None = None) -> dict:
    progress = {"done": 0, "total": 0, "matched": 0}
    if stage:
        progress["stage"] = stage
    return {
        "status": "queued",
        "progress": progress,
        "portrait": None,
        "portrait_id": None,
        "error": None,
        # SPEC v0.7 §A2: фронт считает ETA по started_at + rate_per_min
        "started_at": None,
        "rate_per_min": None,
    }


def create_job(
    stage: str | None = None,
    *,
    kind: str,
    params: dict | None = None,
    owner: str | None = None,
) -> str:
    """Новая джоба вида kind; stage — начальный этап в progress (takeout: "parsing").

    params (JSON-сериализуемые параметры запуска) и owner уходят в таблицу
    jobs: по params джоба перезапускается на старте (resume_pending_jobs),
    по owner/params.url работает single-flight (find_active_job).

    Playlist-джобы stage не передают — их progress остаётся трёхпольным
    (обратная совместимость с фронтом v0.2 и тестами).
    """
    job_id = uuid.uuid4().hex
    state = _new_state(stage)
    with _lock:
        _jobs[job_id] = state
    try:
        jobs_store.insert_job(job_id, kind, state, params=params, owner=owner)
    except Exception:
        # durability — best effort: джоба всё равно идёт в памяти
        log.exception("джоба %s: не записалась в SQLite (идёт только в памяти)", job_id)
    return job_id


def get_job(job_id: str) -> dict | None:
    """Копия состояния джобы (мутировать снаружи нельзя) или None.

    Промах горячего слоя -> чтение из SQLite: после рестарта бэка фронт
    продолжает поллить тот же job_id и получает живое состояние из БД.
    """
    with _lock:
        state = _jobs.get(job_id)
        if state is not None:
            return copy.deepcopy(state)
    try:
        return jobs_store.get_state(job_id)
    except Exception:
        log.exception("джоба %s: чтение из SQLite упало", job_id)
        return None


def find_active_job(
    kind: str, owner: str | None = None, url: str | None = None
) -> str | None:
    """job_id живой (queued/running) джобы того же kind — single-flight.

    Роуты отвечают 409 {detail, job_id} на дубликат: вторая джоба того же
    kind от того же owner (или тот же playlist url) не ставится, фронт
    поллит возвращённый job_id. Сбой чтения БД дубликат не ловит (best effort).
    """
    try:
        return jobs_store.find_active(kind, owner=owner, url=url)
    except Exception:
        log.exception("single-flight: поиск живой джобы (%s) упал", kind)
        return None


def _persist(job_id: str, state: dict) -> None:
    """Состояние -> SQLite; сбой записи джобу не роняет (память — главнее)."""
    try:
        jobs_store.save_state(job_id, state)
    except Exception:
        log.exception("джоба %s: состояние не записалось в SQLite", job_id)


def _update(job_id: str, **fields) -> None:
    """Обновить состояние в памяти + записать сквозь неё в SQLite.

    Переходы статусов (fields содержит status) пишутся немедленно; прочие
    обновления (прогресс, rate_per_min) — не чаще раза в
    PROGRESS_WRITE_INTERVAL сек на джобу: длинная джоба тикает каждый трек,
    молотить диск незачем — GET читает горячую память.
    """
    is_transition = "status" in fields
    with _lock:
        state = _jobs.get(job_id)
        if state is None:
            return
        if "progress" in fields and (state.get("progress") or {}).get("resumed"):
            # resumed переживает перезапись progress телом джобы (resume-on-startup)
            fields["progress"] = {**fields["progress"], "resumed": True}
        state.update(fields)
        if is_transition:
            _last_write.pop(job_id, None)  # следующий прогресс запишется сразу
        else:
            now = _clock()
            last = _last_write.get(job_id)
            if last is not None and now - last < PROGRESS_WRITE_INTERVAL:
                return  # горячее состояние уже в памяти, диск не трогаем
            _last_write[job_id] = now
        snapshot = copy.deepcopy(state)
    _persist(job_id, snapshot)


def _set_progress(
    job_id: str,
    done: int,
    total: int,
    matched: int,
    stage: str | None = None,
    music_found: int | None = None,
) -> None:
    progress = {"done": done, "total": total, "matched": matched}
    if stage:
        progress["stage"] = stage  # takeout: "parsing" | "resolving" | "analyzing"
    if music_found is not None:
        # youtube (SPEC v0.7 §A1): сколько музыкальных лайков найдено сканом
        progress["music_found"] = music_found
    _update(job_id, progress=progress)


def _mark_running(job_id: str) -> None:
    """status=running + started_at (ISO) — фронт считает ETA (SPEC v0.7 §A2)."""
    _update(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
    )


def run_playlist_job(job_id: str, url: str, limit: int) -> None:
    """Тело джобы: плейлист -> треки -> признаки -> портрет. Ошибки -> status=error."""
    _mark_running(job_id)
    try:
        tracks = ytmusic.fetch_playlist(url, limit=limit)
    except ytmusic.PlaylistPrivate as e:
        _update(job_id, status="error", error=str(e))
        return
    except ytmusic.PlaylistNotFound as e:
        _update(job_id, status="error", error=str(e))
        return
    except (ytmusic.PlaylistError, ValueError) as e:
        _update(job_id, status="error", error=str(e))
        return
    except Exception:
        log.exception("джоба %s: неожиданная ошибка чтения плейлиста", job_id)
        _update(job_id, status="error", error="не удалось прочитать плейлист (бэк YT Music недоступен?)")
        return

    if not tracks:
        _update(job_id, status="error", error="в плейлисте не нашлось подходящих треков")
        return

    _set_progress(job_id, 0, len(tracks), 0)
    try:
        df = pipeline.analyze_tracks(
            tracks, progress_cb=lambda d, t, m: _set_progress(job_id, d, t, m)
        )
    except Exception:
        log.exception("джоба %s: анализ упал", job_id)
        _update(job_id, status="error", error="анализ треков не удался")
        return

    _finalize_job(job_id, df, n_tracks=len(tracks), source_label="плейлист YouTube Music")


def _finalize_job(
    job_id: str,
    df,
    n_tracks: int,
    source_label: str,
    owner: str | None = None,
    source: str | None = None,
    api_fetched_at: str | None = None,
) -> None:
    """Общий хвост джобы: проверка matched -> портрет -> сохранение -> done.

    owner/source/api_fetched_at — для OAuth-источников (v0.5, youtube):
    попадают и в payload портрета (source, api_fetched_at), и в колонки
    portraits (привязка к channel_id для disconnect/purge).

    Честное покрытие (SPEC v0.13 §B): coverage = {candidates, analyzed}
    кладётся и в done-состояние джобы, и в payload портрета. candidates —
    треки-кандидаты ДО матчинга превью (n_tracks: найденные музыкальные /
    распознанные строки файла / треки плейлиста), analyzed — сколько реально
    проанализировано. Треки без 30-сек превью выпадают молча — пользователь
    должен видеть «306 из 437», а не гадать, куда делись треки.
    """
    matched = len(df)
    coverage = {"candidates": int(n_tracks), "analyzed": int(matched)}
    if matched < MIN_MATCHED:
        _update(
            job_id, status="error",
            error=f"слишком мало треков сматчилось: {matched} из {n_tracks} "
                  f"(нужно >= {MIN_MATCHED})",
        )
        return

    try:
        portrait = build_portrait(df)
    except ValueError as e:
        _update(job_id, status="error", error=str(e))
        return

    # SPEC v0.13 §B: покрытие — в payload ДО сохранения (переживает рестарт,
    # питает приписку в шапке и главу identity истории)
    portrait["coverage"] = coverage
    if source:
        portrait["source"] = source
    if api_fetched_at:
        portrait["api_fetched_at"] = api_fetched_at

    # постоянная ссылка — best effort: сбой записи НЕ роняет джобу,
    # портрет всё равно отдаётся, просто без portrait_id
    portrait_id = None
    try:
        portrait_id = store.save_portrait(
            portrait, source_label=source_label,
            owner=owner, source=source, api_fetched_at=api_fetched_at,
        )
    except Exception:
        log.exception("джоба %s: портрет не сохранился (отдаём без portrait_id)", job_id)
    try:
        store.attach_bittersweet_percentile(portrait)
    except Exception:
        log.exception("джоба %s: перцентиль биттерсвита не посчитался", job_id)

    _update(
        job_id, status="done",
        portrait=portrait, portrait_id=portrait_id, coverage=coverage,
    )


def run_takeout_job(job_id: str, entries: list[takeout.RawEntry], limit: int = 40) -> None:
    """Тело takeout-джобы: RawEntry -> (oEmbed-резолвинг) -> конвейер -> портрет.

    entries уже распарсены в HTTP-хендлере (быстро и без сети — кривой файл
    даёт 422 сразу). Берём ПОСЛЕДНИЕ по времени добавления limit треков
    (свежие лайки интереснее старых). Этапы в progress.stage:
    "resolving" (video_id -> artist/title) -> "analyzing" (превью + признаки).
    """
    _mark_running(job_id)
    picked = takeout.select_latest(entries, limit)

    # --- этап 1: резолвинг video_id -> artist/title (oEmbed, кэш video_meta) ---
    total = len(picked)
    _set_progress(job_id, 0, total, 0, stage="resolving")
    tracks: list[dict] = []
    for done, entry in enumerate(picked, start=1):
        # liked_at (SPEC v0.10 §A) — timestamp плейлистного CSV (колонка уже
        # распарсена в takeout.parse_csv); у library-songs его нет -> None
        liked_at = entry.added_at.isoformat() if entry.added_at else None
        if entry.artist and entry.title:
            # library-songs: метаданные уже в CSV, сеть не нужна
            tracks.append({
                "artist": entry.artist, "title": entry.title,
                "videoId": entry.video_id, "liked_at": liked_at,
            })
        else:
            try:
                meta = resolver.resolve_video(entry.video_id)
            except Exception:
                log.exception("джоба %s: резолвер упал на %s", job_id, entry.video_id)
                meta = None
            if meta is not None:
                tracks.append({
                    "artist": meta["artist"], "title": meta["title"],
                    "videoId": entry.video_id, "liked_at": liked_at,
                })
            # meta is None -> видео удалено/недоступно: пропускаем, идёт в unmatched
        _set_progress(job_id, done, total, len(tracks), stage="resolving")

    if not tracks:
        _update(
            job_id, status="error",
            error="не удалось определить ни одного трека из выгрузки "
                  "(видео удалены или сервисы недоступны)",
        )
        return

    # --- этап 2: существующий конвейер (превью -> librosa -> кэш) ---
    _set_progress(job_id, 0, len(tracks), 0, stage="analyzing")
    try:
        df = pipeline.analyze_tracks(
            tracks,
            progress_cb=lambda d, t, m: _set_progress(job_id, d, t, m, stage="analyzing"),
        )
    except Exception:
        log.exception("джоба %s: анализ упал", job_id)
        _update(job_id, status="error", error="анализ треков не удался")
        return

    _finalize_job(job_id, df, n_tracks=total, source_label="выгрузка Google Takeout")


def run_youtube_job(
    job_id: str, channel_id: str, access_token: str, limit: int | None = 40
) -> None:
    """Тело youtube-джобы (SPEC v0.5 §A3, v0.7 §A1): скан музыкальных лайков
    -> конвейер -> портрет.

    limit=None/0 — «все музыкальные лайки» (SPEC v0.7 §A2, скан до 5000).
    Этапы в progress.stage: "fetching" (скан playlistItems + музыкальный
    фильтр videos.list; done/total = просканировано/оценка, music_found —
    найденные музыкальные) -> "analyzing" (превью-каскад + признаки, как
    раньше). access_token уже живой — его добыл роут (рефреш с
    401-семантикой там же).
    """
    _mark_running(job_id)

    # правило 30 дней — в начале каждой youtube-джобы (спека §A3)
    try:
        google_accounts.purge_stale_youtube_data()
    except Exception:
        log.exception("джоба %s: purge_stale_youtube_data упал (не критично)", job_id)

    account = google_accounts.get_account(channel_id)
    if account is None:
        _update(job_id, status="error", error="аккаунт YouTube отключён — войдите заново")
        return
    playlist_id = account.get("likes_playlist_id") or "LL"  # LL = алиас likes-плейлиста

    # --- этап 1: скан лайков + музыкальный фильтр (SPEC v0.7 §A1) ---
    # categoryId==10 ИЛИ « - Topic», длительность <= 12 минут, доступные;
    # limit применяется уже к отфильтрованному списку (внутри скана)
    _set_progress(job_id, 0, 0, 0, stage="fetching", music_found=0)
    try:
        tracks = youtube_api.scan_music_likes(
            access_token, playlist_id, limit=limit,
            progress_cb=lambda scanned, total, found: _set_progress(
                job_id, scanned, total, 0, stage="fetching", music_found=found
            ),
        )
    except youtube_api.YouTubeQuotaError as e:
        log.warning("джоба %s: квота YouTube: %s", job_id, e)
        _update(
            job_id, status="error",
            error="квота YouTube исчерпана — попробуйте завтра",
        )
        return
    except youtube_api.YouTubeAPIError as e:
        log.warning("джоба %s: YouTube API: %s", job_id, e)
        _update(job_id, status="error", error=f"не удалось прочитать лайки YouTube: {e}")
        return

    if not tracks:
        _update(
            job_id, status="error",
            error="в лайках не нашлось музыкальных треков "
                  "(категория Music / YT Music, короче 12 минут, доступных)",
        )
        return
    music_found = len(tracks)

    # момент выгрузки из API — точка отсчёта правила 30 дней
    api_fetched_at = datetime.now(timezone.utc).isoformat()

    # --- этап 2: существующий конвейер (превью -> librosa -> кэш) ---
    # rate_per_min — скользящее окно по последним <= 20 трекам (SPEC v0.7 §A2)
    rate = RateMeter()

    def _analyzing_progress(done: int, total: int, matched: int) -> None:
        _set_progress(
            job_id, done, total, matched,
            stage="analyzing", music_found=music_found,
        )
        _update(job_id, rate_per_min=rate.tick())

    _set_progress(
        job_id, 0, len(tracks), 0, stage="analyzing", music_found=music_found
    )
    try:
        df = pipeline.analyze_tracks(tracks, progress_cb=_analyzing_progress)
    except Exception:
        log.exception("джоба %s: анализ упал", job_id)
        _update(job_id, status="error", error="анализ треков не удался")
        return

    _finalize_job(
        job_id, df, n_tracks=len(tracks), source_label="лайки YouTube",
        owner=channel_id, source="youtube", api_fetched_at=api_fetched_at,
    )


def run_discovery_job(
    job_id: str,
    portrait_id: str,
    cluster: int | None,
    limit: int,
    point: dict | None = None,
) -> None:
    """Тело discovery-джобы (SPEC v0.4 §A2, v0.9 §B): сиды -> радио -> анализ -> match.

    Два режима с ОБЩИМ конвейером: cluster (архетип портрета) и point
    (клик по точке карты, point={"x", "y"}) — различаются только получением
    target (сиды + вектор, см. discovery.cluster_target / point_target).
    Этапы в progress.stage: "seeding" -> "radio" (done/total = сиды) ->
    "analyzing" (done/total = кандидаты, matched = находки выше порога).
    Результат — в поле result done-джобы + дописывается в payload портрета.
    """
    _mark_running(job_id)
    try:
        _run_discovery(job_id, portrait_id, cluster, limit, point)
    except Exception:
        log.exception("джоба %s: discovery упал", job_id)
        _update(job_id, status="error", error="поиск похожего не удался")


def _run_discovery(
    job_id: str, portrait_id: str, cluster: int | None, limit: int,
    point: dict | None = None,
) -> None:
    row = store.get_portrait(portrait_id)
    if row is None:
        _update(job_id, status="error", error=f"портрет {portrait_id} не найден")
        return
    portrait = row["portrait"]
    if not discovery.supports_discovery(portrait):
        _update(
            job_id, status="error",
            error="портрет сохранён старой версией — постройте его заново",
        )
        return

    # --- этап 1: target = сиды + вектор (роут валидирует; страховка от гонок) ---
    _set_progress(job_id, 0, 0, 0, stage="seeding")
    if point is not None:
        try:
            target = discovery.point_target(
                portrait, float(point["x"]), float(point["y"])
            )
        except ValueError as e:  # рядом с точкой < POINT_MIN_NEIGHBORS пригодных
            _update(job_id, status="error", error=str(e))
            return
    else:
        clusters = portrait.get("clusters") or []
        if cluster is None or not (0 <= cluster < len(clusters)):
            _update(job_id, status="error", error=f"кластера {cluster} нет в портрете")
            return
        target = discovery.cluster_target(portrait, cluster)
        if not target["seeds"]:
            _update(
                job_id, status="error",
                error="в этом кластере нет треков с videoId — не из чего строить радио",
            )
            return

    _run_discovery_pipeline(job_id, portrait_id, portrait, target, limit)


def _run_discovery_pipeline(
    job_id: str, portrait_id: str, portrait: dict, target: dict, limit: int
) -> None:
    """Общий конвейер discovery: радио по сидам -> анализ -> match к вектору.

    target — из discovery.cluster_target / point_target (сиды, вектор,
    store_key, result_extra); конвейер от режима не зависит (SPEC v0.9 §B).
    """
    # --- этап 2: радио YT Music по каждому сиду (сбой сида — пропуск сида) ---
    seeds = target["seeds"]
    _set_progress(job_id, 0, len(seeds), 0, stage="radio")
    own_ids = {p["videoId"] for p in portrait["points"] if p.get("videoId")}
    candidates = discovery.collect_candidates(
        seeds, exclude=own_ids, cap=limit * discovery.CANDIDATE_CAP_FACTOR,
        progress_cb=lambda d, t: _set_progress(job_id, d, t, 0, stage="radio"),
    )
    if not candidates:
        _update(
            job_id, status="error",
            error="радио не дало кандидатов — попробуйте другой архетип или точку",
        )
        return

    # --- этап 3: превью-каскад (кэш) + анализ + match к target-вектору ---
    total = len(candidates)
    _set_progress(job_id, 0, total, 0, stage="analyzing")
    centroid, mean, std = target["centroid"], target["mean"], target["std"]
    found: list[dict] = []
    for done, cand in enumerate(candidates, start=1):
        res = discovery.analyze_candidate(cand["artist"], cand["title"])
        if res is not None:
            feats, preview_url = res
            match = discovery.match_score(feats, centroid, mean, std)
            if match >= discovery.MATCH_THRESHOLD:
                found.append({
                    "artist": cand["artist"],
                    "title": cand["title"],
                    "videoId": cand["videoId"],
                    "match": match,
                    "tempo": round(float(feats["tempo"])),
                    "key": feats.get("key"),
                    "mode": feats.get("mode"),
                    "preview_url": preview_url,
                })
        _set_progress(job_id, done, total, len(found), stage="analyzing")

    found.sort(key=lambda r: -r["match"])
    found = found[:limit]
    result = {
        "discoveries": found,
        **target["result_extra"],
        "listen_all_url": discovery.listen_all_url([f["videoId"] for f in found]),
    }

    # находки — в payload портрета (best effort: сбой записи не роняет джобу)
    try:
        store.update_discoveries(portrait_id, target["store_key"], result)
    except Exception:
        log.exception("джоба %s: discoveries не дописались в портрет %s", job_id, portrait_id)

    _update(job_id, status="done", result=result)


# --- resume-on-startup: перезапуск незавершённых джоб после рестарта бэка ---

# джобы старше суток не перезапускаем: контекст протух (токены, ожидание
# пользователя) — честнее пометить ошибкой и дать запустить анализ заново
STALE_AFTER = timedelta(hours=24)

STALE_ERROR = "джоба прервана рестартом сервера и устарела — запустите анализ заново"


def _thread_runner(fn: Callable[[], None]) -> None:
    """Исполнение тела перезапущенной джобы — daemon-поток (как BackgroundTasks)."""
    threading.Thread(target=fn, daemon=True, name="job-resume").start()


def _resume_playlist(job_id: str, params: dict) -> None:
    run_playlist_job(job_id, params["url"], int(params.get("limit") or 25))


def _resume_takeout(job_id: str, params: dict) -> None:
    entries = [takeout.entry_from_dict(d) for d in params.get("entries") or []]
    if not entries:
        _update(
            job_id, status="error",
            error="не удалось восстановить треки джобы — загрузите выгрузку заново",
        )
        return
    run_takeout_job(job_id, entries, int(params.get("limit") or 40))


def _resume_youtube(job_id: str, params: dict) -> None:
    """access_token к моменту рестарта протух — добываем свежий по refresh_token."""
    account = google_accounts.get_account(params.get("channel_id") or "")
    if account is None:
        _update(job_id, status="error", error="аккаунт YouTube отключён — войдите заново")
        return
    try:
        access_token = google_oauth.ensure_access_token(account)
    except google_oauth.GoogleOAuthError:  # включая InvalidGrant
        log.exception("джоба %s: resume не добыл access_token", job_id)
        _update(
            job_id, status="error",
            error="доступ к YouTube отозван или истёк — войдите заново",
        )
        return
    run_youtube_job(job_id, account["channel_id"], access_token, params.get("limit") or None)


def _resume_discovery(job_id: str, params: dict) -> None:
    """cluster- и point-режим (SPEC v0.9 §B): в params ровно один из cluster/point."""
    cluster = params.get("cluster")
    run_discovery_job(
        job_id, params["portrait_id"],
        int(cluster) if cluster is not None else None,
        int(params.get("limit") or 15),
        point=params.get("point"),
    )


# kind -> (начальный stage свежего состояния — как в роутах, тело перезапуска)
_RESUME: dict[str, tuple[str | None, Callable[[str, dict], None]]] = {
    "playlist": (None, _resume_playlist),
    "takeout": ("parsing", _resume_takeout),
    "youtube": ("fetching", _resume_youtube),
    "discovery": ("seeding", _resume_discovery),
}


def _is_stale(created_at: str) -> bool:
    try:
        created = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created > STALE_AFTER


def _mark_interrupted(job_id: str, state: dict, error: str) -> None:
    """Незавершённая джоба, которую не перезапускаем: error в память и БД."""
    state = {**state, "status": "error", "error": error}
    with _lock:
        _jobs[job_id] = state
        snapshot = copy.deepcopy(state)
    _persist(job_id, snapshot)


def resume_pending_jobs(
    runner: Callable[[Callable[[], None]], None] = _thread_runner,
) -> list[str]:
    """Перезапуск queued/running джоб из SQLite (вызывается из lifespan).

    Джоба перезапускается С НАЧАЛА: конвейер идемпотентен благодаря кэшу
    дескрипторов — уже проанализированные треки промотаются кэш-хитами.
    Перезапущенная джоба получает свежее состояние под ТЕМ ЖЕ job_id
    (фронт продолжает поллинг) с progress.resumed = true. Джобы старше
    STALE_AFTER (и неизвестных kind) помечаются error без перезапуска.

    runner исполняет тело джобы (по умолчанию — daemon-поток); тесты
    передают синхронный runner. Возвращает job_id перезапущенных джоб.
    """
    try:
        pending = jobs_store.pending_jobs()
    except Exception:
        log.exception("resume-on-startup: чтение незавершённых джоб упало")
        return []

    resumed: list[str] = []
    for row in pending:
        job_id, kind = row["job_id"], row["kind"]
        if kind not in _RESUME:
            _mark_interrupted(
                job_id, row["state"],
                f"джоба неизвестного вида {kind!r} прервана рестартом сервера",
            )
            continue
        if _is_stale(row["created_at"]):
            _mark_interrupted(job_id, row["state"], STALE_ERROR)
            continue
        stage, body = _RESUME[kind]
        state = _new_state(stage)
        state["progress"]["resumed"] = True  # переживает _set_progress (см. _update)
        with _lock:
            _jobs[job_id] = state
            snapshot = copy.deepcopy(state)
        _persist(job_id, snapshot)  # queued + resumed — в БД немедленно
        resumed.append(job_id)
        log.info("resume-on-startup: перезапускаю %s-джобу %s", kind, job_id)
        runner(partial(_run_resumed, job_id, body, row["params"]))
    return resumed


def _run_resumed(job_id: str, body: Callable[[str, dict], None], params: dict) -> None:
    """Обёртка тела перезапуска: любое исключение -> честная error-джоба."""
    try:
        body(job_id, params)
    except Exception:
        log.exception("джоба %s: перезапуск после рестарта упал", job_id)
        _update(job_id, status="error", error="перезапуск джобы после рестарта не удался")
