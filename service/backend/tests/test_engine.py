"""build_portrait на синтетическом DataFrame из 40 строк."""

import numpy as np
import pandas as pd
import pytest

from app.engine.portrait import build_portrait


def _synthetic_df(n: int = 40) -> pd.DataFrame:
    """Две выраженные группы по 20: спокойное-меланхоличное vs энергично-светлое."""
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


def test_build_portrait_contract():
    df = _synthetic_df(40)
    portrait = build_portrait(df)

    assert portrait["n_tracks"] == 40
    assert len(portrait["clusters"]) >= 2
    assert sum(c["size"] for c in portrait["clusters"]) == 40
    for c in portrait["clusters"]:
        assert c["label"] and c["color"].startswith("#")
        assert {"tempo", "minor_share", "brightness"} <= set(c["medians"])
        assert 1 <= len(c["examples"]) <= 4

    assert len(portrait["points"]) == 40
    for p in portrait["points"]:
        assert 0 <= p["x"] <= 100 and 0 <= p["y"] <= 100
        assert 0 <= p["cluster"] < len(portrait["clusters"])

    assert {"count", "share", "top"} <= set(portrait["bittersweet"])
    assert {"tempo_median", "minor_share", "brightness_mean"} <= set(portrait["fingerprint"])


def test_build_portrait_separates_obvious_groups():
    """Медианы темпа крупнейших кластеров должны отражать две группы."""
    portrait = build_portrait(_synthetic_df(40))
    tempos = sorted(c["medians"]["tempo"] for c in portrait["clusters"])
    assert tempos[0] < 100 < tempos[-1]


def test_build_portrait_filters_bad_status():
    df = _synthetic_df(40)
    df.loc[:4, "status"] = "download_failed"
    portrait = build_portrait(df)
    assert portrait["n_tracks"] == 35


def test_build_portrait_rejects_tiny_input():
    with pytest.raises(ValueError):
        build_portrait(_synthetic_df(40).head(5))


def test_build_portrait_respects_explicit_k():
    portrait = build_portrait(_synthetic_df(40), k=3)
    assert len(portrait["clusters"]) == 3


def test_examples_rich_and_highlight_previews_from_cache(tmp_path, monkeypatch):
    """SPEC v0.6 §A2: preview_url подтягивается из кэша при построении портрета."""
    from app.config import settings
    from app.engine import cache

    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")
    for i in range(40):
        cache.set_preview_url(
            cache.key_for(None, f"Artist {i}", f"Track {i}"), f"https://cdn/{i}.mp3"
        )

    portrait = build_portrait(_synthetic_df(40))
    for c in portrait["clusters"]:
        assert [e["label"] for e in c["examples_rich"]] == c["examples"]
        for e in c["examples_rich"]:
            i = int(e["label"].split(" — ")[0].removeprefix("Artist "))
            assert e["preview_url"] == f"https://cdn/{i}.mp3"
    for h in portrait["highlights"]:
        i = int(h["track"].split(" — ")[0].removeprefix("Artist "))
        assert h["preview_url"] == f"https://cdn/{i}.mp3"


def test_examples_rich_null_when_cache_empty(tmp_path, monkeypatch):
    """Старые кэш-записи без preview_url -> null (их добирает /api/preview/resolve)."""
    from app.config import settings

    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")
    portrait = build_portrait(_synthetic_df(40))
    assert all(
        e["preview_url"] is None
        for c in portrait["clusters"] for e in c["examples_rich"]
    )
    assert all(h["preview_url"] is None for h in portrait["highlights"])
