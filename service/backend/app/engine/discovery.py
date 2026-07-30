"""Discovery «найти ещё такого» (SPEC v0.4 §A2) — движок без HTTP-слоя.

Продуктизация экспериментального discover.py из корня (логика сидов/радио/
дедупа адаптирована, НЕ импорт): сиды из выбранного кластера -> радио YT Music
по каждому сиду -> превью-каскад + анализ кандидатов -> match к центроиду.

SPEC v0.9 §B: рядом с cluster-режимом живёт point-режим (клик по точке карты).
Конвейер ОБЩИЙ — режимы различаются только получением target (сиды + вектор):
cluster_target / point_target, дальше jobs._run_discovery_pipeline один на всех.

Всё пространство восстанавливается из точек портрета: каждая точка несёт
videoId и сырые MOOD_FEATURES (SPEC v0.4 §A1), поэтому стандартизация
(mean/std библиотеки) и центроид кластера пересчитываются без исходного
DataFrame. Портреты, сохранённые до v0.4 (без этих полей), discovery
не поддерживает — supports_discovery() -> False, HTTP-слой отвечает 409.

Темп в match октаво-толерантен: beat_track любит удваивать/половинить BPM
(урок «kah»), поэтому расстояние по темпу — min по кандидатским {t, 2t, t/2}.
"""

from __future__ import annotations

import logging
import math
from collections import Counter

import numpy as np

from app import pipeline
from app.engine import cache, previews
from app.engine.portrait import MOOD_FEATURES

log = logging.getLogger(__name__)

SEED_LIMIT = 5          # сидов на кластер — до 5 ближайших к центроиду
RADIO_PER_SEED = 15     # треков радио на сид
MATCH_THRESHOLD = 35    # ниже — не показываем (слишком далеко от вкуса)
MAX_PER_ARTIST = 2      # разнообразие: не больше 2 кандидатов на артиста
CANDIDATE_CAP_FACTOR = 3  # потолок кандидатов = limit * 3

# point-режим (SPEC v0.9 §B): сиды и target-вектор — из k ближайших точек карты
POINT_NEIGHBORS = 7      # k ближайших к (x, y) точек с videoId и features
POINT_MIN_NEIGHBORS = 3  # меньше — точка «в пустоте», честный 409
POINT_NEAR_LABELS = 3    # сколько label ближайших треков уходит в target.near

NOT_ENOUGH_NEAR_MSG = (
    "рядом с этой точкой слишком мало ваших треков — "
    "кликните ближе к скоплению точек на карте"
)

LISTEN_ALL_URL = "https://www.youtube.com/watch_videos?video_ids="

_TEMPO_IDX = MOOD_FEATURES.index("tempo")


def supports_discovery(portrait: dict) -> bool:
    """Портрет достаточно свежий для discovery (точки несут videoId+features)?

    Портреты, сохранённые до v0.4, этих полей не имеют — для них HTTP-слой
    отвечает 409 «портрет старой версии — построй заново».
    """
    points = portrait.get("points") or []
    return bool(points) and all(
        "videoId" in p
        and isinstance(p.get("features"), dict)
        and all(f in p["features"] for f in MOOD_FEATURES)
        for p in points
    )


def _features_matrix(points: list[dict]) -> np.ndarray:
    return np.array(
        [[float(p["features"][f]) for f in MOOD_FEATURES] for p in points], dtype=float
    )


def library_space(portrait: dict) -> tuple[np.ndarray, np.ndarray]:
    """(mean, std) MOOD_FEATURES по ВСЕЙ библиотеке портрета.

    Та же стандартизация, что StandardScaler в build_portrait: и cluster-,
    и point-режим меряют расстояния в одной z-шкале библиотеки.
    """
    X = _features_matrix(portrait["points"])
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0  # константный признак: не делим на ноль
    return mean, std


def cluster_space(portrait: dict, cluster: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(центроид кластера, mean, std) в стандартизованном MOOD_FEATURES-пространстве.

    mean/std — по ВСЕЙ библиотеке портрета (library_space),
    центроид — среднее стандартизованных точек выбранного кластера.
    """
    mean, std = library_space(portrait)
    pts = [p for p in portrait["points"] if p["cluster"] == cluster]
    if not pts:
        raise ValueError(f"в кластере {cluster} нет точек")
    Z = (_features_matrix(pts) - mean) / std
    return Z.mean(axis=0), mean, std


def select_seeds(portrait: dict, cluster: int, limit: int = SEED_LIMIT) -> list[str]:
    """videoId сидов: точки кластера, ближайшие к центроиду, у которых есть videoId."""
    centroid, mean, std = cluster_space(portrait, cluster)
    scored: list[tuple[float, str]] = []
    for p in portrait["points"]:
        if p["cluster"] != cluster or not p.get("videoId"):
            continue
        z = (np.array([float(p["features"][f]) for f in MOOD_FEATURES]) - mean) / std
        scored.append((float(np.linalg.norm(z - centroid)), p["videoId"]))
    scored.sort(key=lambda pair: pair[0])
    return [vid for _, vid in scored[:limit]]


# --- target-абстракция (SPEC v0.9 §B): конвейер один, различаются сиды+вектор ---
# target = {seeds, centroid, mean, std, store_key, result_extra}:
#   seeds       — videoId для радио;
#   centroid/mean/std — куда матчим кандидатов (октаво-толерантный match_score);
#   store_key   — ключ записи в payload["discoveries"] ("0" | "point:50:50");
#   result_extra — поля done-результата поверх discoveries/listen_all_url
#                  (cluster_label/archetype — как раньше, target — аддитивно).


def cluster_target(portrait: dict, cluster: int) -> dict:
    """target cluster-режима: сиды у центроида кластера, вектор — сам центроид."""
    centroid, mean, std = cluster_space(portrait, cluster)
    c = portrait["clusters"][cluster]
    return {
        "seeds": select_seeds(portrait, cluster),
        "centroid": centroid,
        "mean": mean,
        "std": std,
        "store_key": str(cluster),
        "result_extra": {
            "cluster_label": c["label"],
            "archetype": c["archetype"],
            "target": {"kind": "cluster", "label": c["label"]},
        },
    }


def nearest_points(portrait: dict, x: float, y: float, k: int = POINT_NEIGHBORS) -> list[dict]:
    """До k точек карты, ближайших к (x, y) евклидом по осям x/y.

    Берутся только точки с videoId и полными features — топливо радио
    и target-вектора; прочие (Spotify-ключи, старые записи) пропускаются.
    """
    eligible = [
        p for p in portrait.get("points") or []
        if p.get("videoId")
        and isinstance(p.get("features"), dict)
        and all(f in p["features"] for f in MOOD_FEATURES)
    ]
    eligible.sort(key=lambda p: (float(p["x"]) - x) ** 2 + (float(p["y"]) - y) ** 2)
    return eligible[:k]


def point_label(x: float, y: float) -> str:
    """Человеческая подпись точки в осях карты (x = настроение, y = энергия)."""
    return f"точка карты: настроение {round(x)} · энергия {round(y)}"


def point_store_key(x: float, y: float) -> str:
    """Ключ discoveries для точки: округление до целых — повтор той же
    округлённой точки перезаписывает прежнюю запись (SPEC v0.9 §B)."""
    return f"point:{round(x)}:{round(y)}"


def point_target(portrait: dict, x: float, y: float) -> dict:
    """target point-режима: сиды — ближайшие к (x, y) точки карты,
    вектор — среднее их MOOD_FEATURES в z-шкале библиотеки.

    ValueError с человеческим сообщением, если пригодных точек рядом
    < POINT_MIN_NEIGHBORS (HTTP-слой отвечает тем же текстом в 409).
    """
    near = nearest_points(portrait, x, y)
    if len(near) < POINT_MIN_NEIGHBORS:
        raise ValueError(NOT_ENOUGH_NEAR_MSG)
    mean, std = library_space(portrait)
    Z = (_features_matrix(near) - mean) / std
    return {
        "seeds": [p["videoId"] for p in near[:SEED_LIMIT]],
        "centroid": Z.mean(axis=0),
        "mean": mean,
        "std": std,
        "store_key": point_store_key(x, y),
        "result_extra": {
            "target": {
                "kind": "point",
                "x": x,
                "y": y,
                "label": point_label(x, y),
                "near": [p["label"] for p in near[:POINT_NEAR_LABELS]],
            },
        },
    }


def fetch_radio(video_id: str, limit: int = RADIO_PER_SEED) -> dict:
    """Радио YT Music по треку (анонимно). Сырой ответ get_watch_playlist.

    Сеть; в тестах — monkeypatch. Может падать на отдельных videoId —
    collect_candidates пропускает такой сид, не роняя джобу.
    """
    # ленивый импорт — как в sources.ytmusic: ytmusicapi не нужен тестам
    from ytmusicapi import YTMusic

    return YTMusic().get_watch_playlist(videoId=video_id, limit=limit)


def collect_candidates(
    seeds: list[str],
    exclude: set[str],
    cap: int,
    per_seed: int = RADIO_PER_SEED,
    progress_cb=None,
) -> list[dict]:
    """Радио по сидам -> кандидаты [{videoId, artist, title}].

    Дедуп по videoId, исключение exclude (треки портрета), <= MAX_PER_ARTIST
    на артиста, потолок cap (лишние отбрасываются с конца). Упавшее радио
    по сиду — пропуск сида. progress_cb(done_seeds, total_seeds) — опционально.
    """
    candidates: dict[str, dict] = {}
    per_artist: Counter[str] = Counter()
    for done, seed in enumerate(seeds, start=1):
        if len(candidates) < cap:
            try:
                radio = fetch_radio(seed, limit=per_seed)
            except Exception:
                log.warning("радио по сиду %s не получилось — пропускаю сид", seed)
                radio = {}
            for t in radio.get("tracks") or []:
                if len(candidates) >= cap:
                    break
                vid, title = t.get("videoId"), t.get("title")
                if not vid or not title or vid in exclude or vid in candidates:
                    continue
                artist = ", ".join(
                    a["name"] for a in (t.get("artists") or []) if a.get("name")
                )
                artist_key = artist.casefold()
                if per_artist[artist_key] >= MAX_PER_ARTIST:
                    continue
                per_artist[artist_key] += 1
                candidates[vid] = {"videoId": vid, "artist": artist, "title": title}
        if progress_cb:
            progress_cb(done, len(seeds))
    return list(candidates.values())


def analyze_candidate(artist: str, title: str) -> tuple[dict, str] | None:
    """(признаки, preview_url) кандидата или None (нет превью / анализ упал).

    Существующий превью-каскад с кэшем признаков: кэш-хит экономит скачивание
    и librosa. URL превью с v0.6 тоже кэшируется (SPEC v0.6 §A2) — живой
    каскад нужен только старым записям, найденное дозаписывается в кэш.
    """
    key = cache.key_for(None, artist, title)
    feats = cache.get_features(key)
    if feats is not None:
        url = cache.get_preview_url(key)
        if not url:
            url = previews.match_track_to_preview(None, artist, title)
            if url:
                cache.set_preview_url(key, url)
        return (feats, url) if url else None
    res = pipeline.analyze_with_preview(None, artist, title)
    if res is None:
        return None
    feats, url = res
    cache.set_features(key, feats, preview_url=url)
    return feats, url


def match_score(
    feats: dict, centroid: np.ndarray, mean: np.ndarray, std: np.ndarray
) -> int:
    """match 0..100 = round(100 * exp(-d/2)), d — стандартизованное расстояние
    до центроида по MOOD_FEATURES с октаво-толерантным темпом (min по {t, 2t, t/2})."""
    diff = (
        np.array([float(feats[f]) for f in MOOD_FEATURES]) - mean
    ) / std - centroid
    tempo = float(feats["tempo"])
    diff[_TEMPO_IDX] = min(
        abs((t - mean[_TEMPO_IDX]) / std[_TEMPO_IDX] - centroid[_TEMPO_IDX])
        for t in (tempo, 2 * tempo, tempo / 2)
    )
    d = float(np.linalg.norm(diff))
    return round(100 * math.exp(-d / 2))


def listen_all_url(video_ids: list[str]) -> str:
    """Ссылка «слушать всё» — анонимный плейлист YouTube из топ-находок."""
    return LISTEN_ALL_URL + ",".join(video_ids)
