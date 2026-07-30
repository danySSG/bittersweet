"""Timeline динамики вкуса по датам лайков (SPEC v0.10 §A) — БЕЗ сети.

Юниты _build_timeline (адаптивное детерминированное слияние бакетов,
пороги 60% liked_at / 3 месяца / 8 треков на бакет, форма бакета) +
liked_at сквозь pipeline.analyze_tracks и в points[] портрета.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.portrait import (
    BITTERSWEET_THRESHOLD,
    TIMELINE_MIN_BUCKET_TRACKS,
    TIMELINE_MIN_LIKED_SHARE,
    TIMELINE_MIN_MONTHS,
    _build_timeline,
    build_portrait,
)

BUCKET_KEYS = {
    "period_start", "period_end", "n_tracks", "tempo_median",
    "minor_share", "bittersweet_share", "top_archetype",
}


def _row(i: int, liked_at: str | None) -> dict:
    """Строка признаков: две выраженные группы (как в test_engine)."""
    rng = np.random.default_rng(42 + i)
    calm = i % 2 == 0
    minor_score = float(rng.uniform(0.68, 0.85) if calm else rng.uniform(0.15, 0.32))
    return {
        "videoId": f"vid{i:03d}",
        "artists": f"Artist {i}",
        "title": f"Track {i}",
        "tempo": float(rng.uniform(65, 85) if calm else rng.uniform(150, 175)),
        "key": "A",
        "mode": "minor" if calm else "major",
        "minor_score": round(minor_score, 3),
        "bittersweet": round(1 - 2 * abs(minor_score - 0.5), 3),
        "percussive": float(rng.uniform(0.05, 0.15) if calm else rng.uniform(0.45, 0.6)),
        "energy_rms": float(rng.uniform(0.05, 0.1) if calm else rng.uniform(0.25, 0.35)),
        "brightness": float(rng.uniform(900, 1300) if calm else rng.uniform(2600, 3400)),
        "onset_rate": float(rng.uniform(0.8, 1.6) if calm else rng.uniform(4.0, 6.0)),
        "liked_at": liked_at,
        "status": "ok",
    }


def _df_by_months(counts: dict[str, int]) -> pd.DataFrame:
    """{"2024-01": 5, ...} -> DataFrame, где на месяц приходится counts треков."""
    liked: list[str | None] = []
    for month, n in counts.items():
        liked += [f"{month}-{(j % 27) + 1:02d}T12:00:00+00:00" for j in range(n)]
    return pd.DataFrame([_row(i, la) for i, la in enumerate(liked)])


def _timeline(df: pd.DataFrame) -> list[dict] | None:
    from app.engine.portrait import MOOD_FEATURES

    return _build_timeline(df, df[MOOD_FEATURES].median())


# ------------------------------------------------- слияние бакетов (юниты)

def test_merge_uneven_months_deterministic():
    """Неровные месяцы [10,3,9,12,2,8] (через границу года) -> 4 бакета >= 8.

    Трассировка алгоритма (наименьший бакет + меньший сосед):
    [10,3,9,12,2,8] -> min=2 сливается с правым (8 < 12) -> [10,3,9,12,10]
    -> min=3 сливается с правым (9 < 10) -> [10,12,12,10] -> все >= 8, стоп.
    """
    tl = _timeline(_df_by_months({
        "2023-11": 10, "2023-12": 3, "2024-01": 9,
        "2024-02": 12, "2024-03": 2, "2024-04": 8,
    }))
    assert tl is not None
    assert [b["n_tracks"] for b in tl] == [10, 12, 12, 10]
    assert [(b["period_start"], b["period_end"]) for b in tl] == [
        ("2023-11-01", "2023-11-30"),
        ("2023-12-01", "2024-01-31"),   # декабрь слился с январём
        ("2024-02-01", "2024-02-29"),   # февраль високосного — сам по себе
        ("2024-03-01", "2024-04-30"),   # март слился с апрелем
    ]
    # сумма треков по бакетам = все датированные треки
    assert sum(b["n_tracks"] for b in tl) == 44


def test_merge_ties_resolved_left_to_right():
    """Равные месяцы [4,4,4,4]: наименьший — самый ранний, сосед — меньший.

    [4,4,4,4] -> первый (i=0) сливается с единственным соседом -> [8,4,4]
    -> min=4 (i=1) сливается с правым (4 < 8) -> [8,8]. Детерминированно.
    """
    tl = _timeline(_df_by_months({
        "2024-01": 4, "2024-02": 4, "2024-03": 4, "2024-04": 4,
    }))
    assert tl is not None
    assert [b["n_tracks"] for b in tl] == [8, 8]
    assert [(b["period_start"], b["period_end"]) for b in tl] == [
        ("2024-01-01", "2024-02-29"), ("2024-03-01", "2024-04-30"),
    ]
    assert all(b["n_tracks"] >= TIMELINE_MIN_BUCKET_TRACKS for b in tl)


def test_no_merge_when_all_months_big_enough():
    """Все месяцы >= 8 — остаются месячными, порядок хронологический."""
    tl = _timeline(_df_by_months({"2024-01": 9, "2024-02": 8, "2024-03": 10}))
    assert tl is not None
    assert [b["n_tracks"] for b in tl] == [9, 8, 10]
    starts = [b["period_start"] for b in tl]
    assert starts == sorted(starts)


def test_bucket_contract_fields():
    tl = _timeline(_df_by_months({"2024-01": 9, "2024-02": 8, "2024-03": 10}))
    assert tl is not None
    for b in tl:
        assert set(b) == BUCKET_KEYS
        assert b["period_start"] <= b["period_end"]
        assert b["tempo_median"] > 0
        assert 0 <= b["minor_share"] <= 100
        assert 0 <= b["bittersweet_share"] <= 100
        assert b["top_archetype"]["name"] and b["top_archetype"]["emoji"]


# ---------------------------------------------- честное отсутствие timeline

def test_no_timeline_when_coverage_under_three_months():
    """2 месяца покрытия (< TIMELINE_MIN_MONTHS) — timeline честно None."""
    assert TIMELINE_MIN_MONTHS == 3
    assert _timeline(_df_by_months({"2024-01": 20, "2024-02": 20})) is None


def test_no_timeline_when_liked_at_share_below_60pct():
    """liked_at меньше чем у 60% точек — динамика недостоверна, None."""
    df = _df_by_months({"2024-01": 8, "2024-02": 8, "2024-03": 8})  # 24 датированных
    bare = pd.DataFrame([_row(100 + i, None) for i in range(20)])   # 20 без даты
    df = pd.concat([df, bare], ignore_index=True)
    assert 24 / 44 < TIMELINE_MIN_LIKED_SHARE
    assert _timeline(df) is None


def test_no_timeline_when_merge_collapses_to_single_bucket():
    """3 месяца по 4 трека сливаются в один бакет — динамики нет, None."""
    assert _timeline(_df_by_months({
        "2024-01": 4, "2024-02": 4, "2024-03": 4,
    })) is None


def test_no_timeline_without_liked_at_column():
    df = pd.DataFrame([_row(i, None) for i in range(16)]).drop(columns=["liked_at"])
    assert _timeline(df) is None


def test_garbage_liked_at_counts_as_missing():
    """Мусорные liked_at (не даты) не должны ни падать, ни попадать в бакеты."""
    df = _df_by_months({"2024-01": 8, "2024-02": 8, "2024-03": 8})
    df.loc[0, "liked_at"] = "не дата"
    tl = _timeline(df)
    assert tl is not None
    assert sum(b["n_tracks"] for b in tl) == 23  # мусорная строка выпала


# --------------------------------------- liked_at сквозь конвейер и портрет

def test_liked_at_through_pipeline_to_points(monkeypatch, tmp_path):
    """liked_at из источника -> analyze_tracks -> DataFrame -> points[].liked_at."""
    from app import pipeline
    from app.config import settings
    from app.engine import features, previews

    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")
    counter = {"n": 0}

    def fake_analyze(path):
        i = counter["n"]
        counter["n"] += 1
        return {k: v for k, v in _row(i, None).items()
                if k not in {"videoId", "artists", "title", "liked_at", "status"}}

    monkeypatch.setattr(previews, "match_track_to_preview",
                        lambda isrc, artist, title, client=None: "http://fake/p.mp3")
    monkeypatch.setattr(previews, "download_preview", lambda url, dest, client=None: True)
    monkeypatch.setattr(features, "analyze", fake_analyze)

    tracks = [
        {"artist": f"Artist {i}", "title": f"Track {i}", "videoId": f"vid{i:03d}",
         "liked_at": f"2024-{(i % 4) + 1:02d}-10T09:00:00+00:00" if i % 2 == 0 else None}
        for i in range(16)
    ]
    df = pipeline.analyze_tracks(tracks)
    got = [None if pd.isna(v) else v for v in df["liked_at"]]  # DataFrame: None -> NaN
    assert got == [t["liked_at"] for t in tracks]

    portrait = build_portrait(df)
    liked = [p["liked_at"] for p in portrait["points"]]
    assert liked == [t["liked_at"] for t in tracks]  # ISO как пришёл, nullable
    # датированных 8 из 16 (50% < 60%) — timeline честно null, и это не ошибка
    assert portrait["timeline"] is None


def test_portrait_payload_has_timeline_when_data_allows():
    """build_portrait кладёт timeline в payload аддитивно (SPEC v0.10 §A)."""
    df = _df_by_months({"2024-01": 9, "2024-02": 8, "2024-03": 10})
    portrait = build_portrait(df)
    assert [b["n_tracks"] for b in portrait["timeline"]] == [9, 8, 10]
    # bittersweet_share бакета — по портретному определению (порог + тембр)
    assert all(
        0 <= b["bittersweet_share"] <= 100 for b in portrait["timeline"]
    )
    assert BITTERSWEET_THRESHOLD == 0.86  # определение зафиксировано спекой
