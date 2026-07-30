"""Оркестрация анализа: tracks -> (кэш | превью -> analyze) -> DataFrame/портрет.

analyze_tracks — общий путь для Spotify-режима и playlist-джобы (v0.2):
кэш-хит по обобщённому ключу (isrc ИЛИ artist+title) или превью + librosa-анализ.
Превью живёт только во временном файле и удаляется после анализа —
аудио не хранится (принцип из ARCHITECTURE.md).

MVP: синхронный последовательный анализ (≈25-50 треков × ~2 сек — приемлемо);
для HTTP это заворачивается в BackgroundTasks-джобу (app/jobs.py).
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from app.engine import cache, features, previews
from app.engine.portrait import build_portrait

log = logging.getLogger(__name__)

# progress_cb(done, total, matched) — done: обработано, matched: с признаками
ProgressCb = Callable[[int, int, int], None]


def analyze_tracks(tracks: list[dict], progress_cb: ProgressCb | None = None) -> pd.DataFrame:
    """[{artist, title, isrc?, videoId?, liked_at?}] -> DataFrame признаков.

    Для каждого трека: кэш-хит ИЛИ (превью -> librosa-анализ -> в кэш).
    Треки без превью пропускаются (не попадают в matched).
    liked_at (SPEC v0.10 §A, ISO-строка или None) протаскивается как есть —
    из него build_portrait собирает timeline динамики вкуса.
    """
    rows: list[dict] = []
    total = len(tracks)
    done = matched = 0
    for t in tracks:
        isrc = t.get("isrc")
        key = cache.key_for(isrc, t["artist"], t["title"])
        feats = cache.get_features(key)
        if feats is None:
            res = analyze_with_preview(isrc, t["artist"], t["title"])
            if res is not None:
                # preview_url — в кэш вместе с признаками (SPEC v0.6 §A2):
                # портрет и /api/preview/resolve отдают его без повторного каскада
                feats, preview_url = res
                cache.set_features(key, feats, preview_url=preview_url)
        done += 1
        if feats is not None:
            matched += 1
            rows.append({
                **feats,
                "videoId": t.get("videoId") or key,
                "artists": t["artist"],
                "title": t["title"],
                "liked_at": t.get("liked_at"),  # ISO или None (SPEC v0.10 §A)
                "status": "ok",
            })
        if progress_cb:
            progress_cb(done, total, matched)
    return pd.DataFrame(rows)


def collect_features(tracks: list[dict]) -> pd.DataFrame:
    """[{isrc, artist, title}] -> DataFrame признаков (имя из v0.1, общий путь)."""
    return analyze_tracks(tracks)


def analyze_with_preview(
    isrc: str | None, artist: str, title: str
) -> tuple[dict, str] | None:
    """(признаки, preview_url) или None: превью-каскад -> скачивание -> librosa.

    URL нужен discovery (SPEC v0.4): превью отдаётся фронту для прослушивания
    как есть, без проксирования через бэк.
    """
    url = previews.match_track_to_preview(isrc, artist, title)
    if not url:
        log.info("превью не найдено: %s — %s (isrc=%s)", artist, title, isrc)
        return None
    suffix = ".m4a" if ".m4a" in url else ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        if not previews.download_preview(url, str(tmp_path)):
            log.info("превью не скачалось: %s — %s", artist, title)
            return None
        return features.analyze(tmp_path), url
    except Exception:
        log.exception("анализ не удался: %s — %s", artist, title)
        return None
    finally:
        # аудио не хранится: превью удаляется сразу после анализа
        tmp_path.unlink(missing_ok=True)


def build_portrait_for_tracks(tracks: list[dict]) -> dict:
    """Полный конвейер: список треков -> JSON-портрет."""
    df = collect_features(tracks)
    if df.empty:
        raise ValueError("не удалось получить признаки ни для одного трека")
    return build_portrait(df)
