"""Портреты: /api/portrait (Spotify-режим), /api/demo/portrait (+save),
/api/p/{id}, /api/portraits (список «Мои портреты», SPEC v0.9 §C)."""

from __future__ import annotations

import copy
import logging

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from app import pipeline
from app.config import settings
from app.engine import spotify, store
from app.engine.observatory import OBSERVATORY_VERSION, build_observatory
from app.engine.portrait import MOOD_FEATURES, build_portrait
from app.engine.story import STORY_VERSION, build_story

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["portrait"])

# demo-портрет детерминирован по (path, mtime) — кэшируем в памяти процесса
_demo_cache: dict[tuple[str, float], dict] = {}


def _attach_percentile(portrait: dict) -> dict:
    """bittersweet.percentile «на лету»; сбой базы не роняет выдачу портрета."""
    try:
        return store.attach_bittersweet_percentile(portrait)
    except Exception:
        log.exception("перцентиль биттерсвита не посчитался")
        bs = portrait.get("bittersweet")
        if isinstance(bs, dict):
            bs["percentile"] = None
        return portrait


def _demo_portrait_data() -> dict:
    """Демо-портрет из CSV эксперимента (кэш в памяти процесса)."""
    csv_path = settings.demo_features_csv
    if not csv_path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"demo CSV не найден: {csv_path}. Задайте DEMO_FEATURES_CSV в .env.",
        )
    cache_key = (str(csv_path), csv_path.stat().st_mtime)
    if cache_key not in _demo_cache:
        df = pd.read_csv(csv_path)
        try:
            _demo_cache.clear()
            _demo_cache[cache_key] = build_portrait(df)
        except ValueError as e:
            raise HTTPException(status_code=503, detail=f"demo CSV непригоден: {e}") from e
    return _demo_cache[cache_key]


@router.get("/demo/portrait")
def demo_portrait() -> dict:
    # копия: перцентиль живой (зависит от таблицы portraits), кэш не мутируем
    return _attach_percentile(copy.deepcopy(_demo_portrait_data()))


@router.post("/demo/portrait/save")
def save_demo_portrait() -> dict:
    """Сохраняет демо-портрет для шаринга, возвращает {portrait_id}."""
    portrait_id = store.save_portrait(_demo_portrait_data(), source_label="демо")
    return {"portrait_id": portrait_id}


@router.get("/portraits")
def portraits_list(limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    """Список новейших сохранённых портретов — «Мои портреты» (SPEC v0.9 §C).

    Лёгкая сводка: id, source_label, created_at, n_tracks, top_archetype,
    fingerprint, bittersweet_share. payload НЕ разворачивается целиком
    (points тяжёлые) — см. store.list_portraits (json_extract в SQLite).

    ВАЖНО (privacy): MVP-локально возвращает ВСЕ портреты — продукт
    однопользовательский до деплоя. Перед публичным деплоем обязательно
    скоупить выдачу по owner (чужие портреты не должны быть видны).
    """
    return store.list_portraits(limit=limit)


@router.get("/p/{portrait_id}")
def saved_portrait(portrait_id: str) -> dict:
    """Сохранённый портрет по постоянной ссылке (+ id, source_label, created_at)."""
    row = store.get_portrait(portrait_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"портрет {portrait_id} не найден")
    portrait = _attach_percentile(row["portrait"])
    return {
        **portrait,
        "id": row["id"],
        "source_label": row["source_label"],
        "created_at": row["created_at"],
    }


def _radar_reference() -> dict[str, list[float]] | None:
    """Референс перцентилей радара (SPEC v0.11 §A3).

    Основной путь — медианы MOOD_FEATURES всех сохранённых портретов;
    если пригодных портретов < PERCENTILE_MIN_PORTRAITS (5) — значения
    треков демо-набора. Всё best effort: сбой референса не роняет
    обсерваторию (radar отдаст self_pct = 50).
    """
    try:
        medians = store.list_feature_medians(MOOD_FEATURES)
    except Exception:
        log.exception("радар: медианы сохранённых портретов не прочитались")
        medians = []
    if len(medians) >= store.PERCENTILE_MIN_PORTRAITS:
        return {f: [m[f] for m in medians] for f in MOOD_FEATURES}
    try:
        df = pd.read_csv(settings.demo_features_csv)
        return {
            f: pd.to_numeric(df[f], errors="coerce").dropna().tolist()
            for f in MOOD_FEATURES
        }
    except Exception:
        log.exception("радар: демо-набор не прочитался")
    if medians:  # меньше 5 портретов и нет демо — лучше такой референс, чем никакой
        return {f: [m[f] for m in medians] for f in MOOD_FEATURES}
    return None


def _observatory_for(portrait_id: str, payload: dict) -> dict:
    """Обсерватория портрета: из payload или ленивая одноразовая сборка.

    Первый запрос считает модели из points[].features и кладёт результат
    в payload портрета (observatory + observatory_version), повторные
    отдают из payload. Портрет без features в points (до v0.4) -> 409:
    аудио не пересчитываем, пользователю нужно перестроить портрет.
    """
    if (
        payload.get("observatory_version") == OBSERVATORY_VERSION
        and isinstance(payload.get("observatory"), dict)
    ):
        return payload["observatory"]

    points = payload.get("points") or []
    if not points or not all(isinstance(p.get("features"), dict) for p in points):
        raise HTTPException(
            status_code=409,
            detail="портрет старой версии — в точках нет признаков; перестройте портрет",
        )
    try:
        observatory = build_observatory(payload, reference=_radar_reference())
    except ValueError as e:  # неполные features в точках и прочая старина
        raise HTTPException(status_code=409, detail=f"портрет старой версии: {e}") from e
    store.save_observatory(portrait_id, observatory, version=OBSERVATORY_VERSION)
    return observatory


@router.get("/p/{portrait_id}/observatory")
def portrait_observatory(portrait_id: str) -> dict:
    """«Обсерватория» портрета — аналитический этаж (SPEC v0.11 §A)."""
    row = store.get_portrait(portrait_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"портрет {portrait_id} не найден")
    return _observatory_for(portrait_id, row["portrait"])


@router.get("/p/{portrait_id}/story")
def portrait_story(portrait_id: str) -> dict:
    """«История» портрета — рассказ по главам (SPEC v0.12 §A).

    Ленивая одноразовая сборка (как обсерватория): первый запрос собирает
    главы из payload (portrait + observatory + timeline; обсерватория при
    необходимости достраивается тут же) и кладёт результат в payload
    (story + story_version), повторные отдают из payload. Старый портрет
    без features в points -> 409 (см. _observatory_for).
    """
    row = store.get_portrait(portrait_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"портрет {portrait_id} не найден")
    payload = row["portrait"]
    if (
        payload.get("story_version") == STORY_VERSION
        and isinstance(payload.get("story"), dict)
    ):
        return payload["story"]

    payload["observatory"] = _observatory_for(portrait_id, payload)
    story = build_story(payload, portrait_id)
    store.save_story(portrait_id, story, version=STORY_VERSION)
    return story


@router.get("/portrait")
async def user_portrait(request: Request) -> dict:
    token = request.session.get("spotify_access_token")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="нет сессии Spotify — начните с /auth/spotify/login "
                   "(или используйте /api/demo/portrait)",
        )
    try:
        tracks = await spotify.fetch_user_tracks(token)
    except spotify.SpotifyError as e:
        raise HTTPException(status_code=502, detail=f"Spotify: {e}") from e

    try:
        return _attach_percentile(pipeline.build_portrait_for_tracks(tracks))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
