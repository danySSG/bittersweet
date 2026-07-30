"""Джобы анализа: POST /api/analyze/{playlist,takeout,youtube} + GET /api/jobs/{id}."""

from __future__ import annotations

from fastapi import (
    APIRouter, BackgroundTasks, File, HTTPException, Query, Request, UploadFile,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app import jobs
from app.routes.google import require_live_account
from app.sources import takeout, ytmusic

router = APIRouter(prefix="/api", tags=["analyze"])


def job_conflict_response(job_id: str) -> JSONResponse:
    """409 single-flight: такой анализ уже идёт — поллите существующую джобу.

    Формат тела — {detail, job_id}: фронт берёт job_id из отдельного поля,
    а на всякий случай id продублирован и в человекочитаемом detail.
    """
    return JSONResponse(
        status_code=409,
        content={
            "detail": f"такой анализ уже идёт — джоба {job_id}",
            "job_id": job_id,
        },
    )

# Потолок глубины анализа (SPEC v0.6 §A1): лимит — осознанный выбор
# пользователя, не скрытая отсечка. 200 — разумный максимум MVP: каждый
# несматчившийся в Deezer трек уходит в iTunes Search (~20 req/min,
# троттлинг 3.1 с — см. previews.py), синхронная джоба на 200 треков
# в худшем случае ~15-20 минут; глубже — уже задача для очереди, не MVP.
MAX_ANALYZE_LIMIT = 200


class AnalyzePlaylistRequest(BaseModel):
    url: str
    limit: int = Field(default=25, ge=12, le=MAX_ANALYZE_LIMIT)  # <12 портрет невозможен (MIN_TRACKS)


@router.post("/analyze/playlist", status_code=202)
def analyze_playlist(req: AnalyzePlaylistRequest, background_tasks: BackgroundTasks):
    """Ставит джобу анализа публичного плейлиста YT Music. 202 {job_id}.

    409 {detail, job_id} — этот плейлист уже анализируется (single-flight).
    """
    try:
        ytmusic.extract_playlist_id(req.url)  # валидация ссылки до постановки джобы
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    existing = jobs.find_active_job("playlist", url=req.url)
    if existing:
        return job_conflict_response(existing)
    job_id = jobs.create_job(
        kind="playlist", params={"url": req.url, "limit": req.limit}
    )
    background_tasks.add_task(jobs.run_playlist_job, job_id, req.url, req.limit)
    return {"job_id": job_id}


@router.post("/analyze/takeout", status_code=202)
def analyze_takeout(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    limit: int = Query(default=40, ge=12, le=MAX_ANALYZE_LIMIT),  # <12 портрет невозможен
) -> dict:
    """Джоба анализа выгрузки Google Takeout (zip или одиночный csv). 202 {job_id}.

    Парсинг — прямо в хендлере (быстро, без сети): кривой/пустой/чужой файл
    даёт честный 4xx сразу, а не error-джобу. 413 — файл больше 100 МБ
    или zip-bomb (распакованные CSV больше 50 МБ).
    """
    data = file.file.read(takeout.MAX_UPLOAD_BYTES + 1)
    if len(data) > takeout.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="файл больше 100 МБ — выберите в Takeout только "
                   "«музыкальную библиотеку» и «плейлисты»",
        )
    try:
        entries = takeout.parse_takeout(data, file.filename or "")
    except takeout.TakeoutTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except takeout.TakeoutError as e:  # включая TakeoutEmpty (пустой/чужой zip)
        raise HTTPException(status_code=422, detail=str(e)) from e

    # в params — уже отобранные ПОСЛЕДНИЕ limit треков (bounded, как и сама
    # джоба: select_latest в run_takeout_job на них идемпотентен); по ним
    # resume-on-startup перезапустит джобу без повторной загрузки файла.
    # Single-flight для takeout нет: у анонимной загрузки нет owner/url-ключа.
    picked = takeout.select_latest(entries, limit)
    job_id = jobs.create_job(
        stage="parsing",
        kind="takeout",
        params={
            "entries": [takeout.entry_to_dict(e) for e in picked],
            "limit": limit,
        },
    )
    background_tasks.add_task(jobs.run_takeout_job, job_id, entries, limit)
    return {"job_id": job_id}


class AnalyzeYoutubeRequest(BaseModel):
    # SPEC v0.7 §A2: потолок 200 для youtube СНЯТ — музыкальный фильтр в джобе
    # сам ограничивает объём (скан <= 5000 лайков, анализируется только музыка).
    # null или 0 = БЕЗ ЛИМИТА (все музыкальные); отрицательные -> 422 (ge=0).
    limit: int | None = Field(default=40, ge=0)

    @field_validator("limit")
    @classmethod
    def _limit_enough_for_portrait(cls, v: int | None) -> int | None:
        # 1..11 сжигает полный анализ и гарантированно падает на build_portrait
        # (MIN_TRACKS=12) — отклоняем честно на входе, а не в конце джобы.
        if v is not None and 0 < v < 12:
            raise ValueError("минимум 12 треков для портрета — укажи >= 12 или 0 (все)")
        return v


@router.post("/analyze/youtube", status_code=202)
def analyze_youtube(
    request: Request,
    background_tasks: BackgroundTasks,
    req: AnalyzeYoutubeRequest | None = None,
):
    """Джоба анализа музыкальных лайков YouTube (SPEC v0.5 §A3, v0.7 §A2).
    202 {job_id}; 401 без сессии. limit=null/0 — все музыкальные лайки.
    409 {detail, job_id} — у этого канала уже идёт анализ (single-flight).

    Живой access_token добывает require_live_account (быстрый POST к Google
    при истечении): invalid_grant = доступ отозван -> честный 401.
    """
    account, access_token = require_live_account(request)
    existing = jobs.find_active_job("youtube", owner=account["channel_id"])
    if existing:
        return job_conflict_response(existing)
    raw_limit = req.limit if req is not None else 40
    limit = raw_limit or None  # null/0 -> без лимита (SPEC v0.7 §A2)
    job_id = jobs.create_job(
        stage="fetching",
        kind="youtube",
        params={"channel_id": account["channel_id"], "limit": limit},
        owner=account["channel_id"],
    )
    background_tasks.add_task(
        jobs.run_youtube_job, job_id, account["channel_id"], access_token, limit
    )
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_state(job_id: str) -> dict:
    """Полное состояние джобы; 404 если нет.

    Читает горячую память, при промахе — таблицу jobs в SQLite: после
    рестарта бэка фронт продолжает поллить тот же job_id.
    """
    state = jobs.get_job(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"джоба {job_id} не найдена")
    return state
