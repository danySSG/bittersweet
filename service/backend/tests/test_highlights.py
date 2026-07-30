"""Хайлайты «самые-самые» (SPEC v0.3 §A): непересекающиеся, приоритет, порог биттерсвита."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.portrait import build_portrait

PRIORITY = ["fastest", "darkest", "most_bittersweet", "most_energetic", "most_yours"]


def _synthetic_df(n: int = 40) -> pd.DataFrame:
    """Две выраженные группы по n/2 (как в test_engine); bittersweet всюду < 0.8."""
    rng = np.random.default_rng(42)
    half = n // 2
    rows = []
    for i in range(n):
        calm = i < half
        minor_score = rng.uniform(0.68, 0.85) if calm else rng.uniform(0.15, 0.32)
        rows.append({
            "videoId": f"vid{i:03d}",
            "artists": f"Artist {i}",
            "title": f"Track {i}",
            "tempo": rng.uniform(65, 85) if calm else rng.uniform(150, 175),
            "key": "A",
            "mode": "minor" if calm else "major",
            "minor_score": round(minor_score, 3),
            "bittersweet": round(1 - 2 * abs(minor_score - 0.5), 3),
            "percussive": rng.uniform(0.05, 0.15) if calm else rng.uniform(0.45, 0.6),
            "energy_rms": rng.uniform(0.05, 0.1) if calm else rng.uniform(0.25, 0.35),
            "brightness": rng.uniform(900, 1300) if calm else rng.uniform(2600, 3400),
            "onset_rate": rng.uniform(0.8, 1.6) if calm else rng.uniform(4.0, 6.0),
            "status": "ok",
        })
    return pd.DataFrame(rows)


def test_highlights_disjoint_and_shaped():
    hl = build_portrait(_synthetic_df(40))["highlights"]
    assert 1 <= len(hl) <= 5
    kinds = [h["kind"] for h in hl]
    tracks = [h["track"] for h in hl]
    assert len(set(kinds)) == len(kinds)
    assert len(set(tracks)) == len(tracks)  # трек попадает только в ОДИН хайлайт
    for h in hl:
        # SPEC v0.10 §D: + videoId — полный трек в официальном YouTube-плеере
        assert set(h) == {"kind", "title", "track", "value", "preview_url", "videoId"}
        assert h["title"] and h["value"] and " — " in h["track"]
        assert h["videoId"].startswith("vid")  # из колонки df, не кэш-ключ
    # порядок соответствует приоритету спеки (сверху вниз)
    assert kinds == [k for k in PRIORITY if k in kinds]


def test_highlight_values_have_units():
    hl = {h["kind"]: h for h in build_portrait(_synthetic_df(40))["highlights"]}
    assert hl["fastest"]["value"].endswith("bpm")
    assert hl["darkest"]["value"].endswith("Гц")
    assert hl["fastest"]["title"] == "самый быстрый"


def test_no_bittersweet_highlight_below_threshold():
    """bittersweet везде < 0.8 -> хайлайта most_bittersweet нет, остальные 4 есть."""
    df = _synthetic_df(40)
    assert float(df["bittersweet"].max()) < 0.8
    kinds = [h["kind"] for h in build_portrait(df)["highlights"]]
    assert "most_bittersweet" not in kinds
    assert kinds == ["fastest", "darkest", "most_energetic", "most_yours"]


def test_bittersweet_highlight_present_when_high():
    df = _synthetic_df(40)
    df.loc[10, "bittersweet"] = 0.95
    df.loc[30, "bittersweet"] = 0.9
    kinds = {h["kind"] for h in build_portrait(df)["highlights"]}
    assert "most_bittersweet" in kinds
