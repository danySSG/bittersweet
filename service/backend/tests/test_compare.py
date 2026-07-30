"""Сравнение вкусов (SPEC v0.3 §C): инварианты score, facets, verdict, ветки ошибок."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import store
from app.engine.compare import fingerprint_vector, similarity_score
from app.engine.portrait import ARCHETYPE_NAMES, build_portrait
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


def _df(n: int, calm_ratio: float, seed: int) -> pd.DataFrame:
    """Синтетика с настраиваемой долей «спокойной» группы — разные портреты."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        calm = i < int(n * calm_ratio)
        minor_score = rng.uniform(0.68, 0.85) if calm else rng.uniform(0.15, 0.32)
        rows.append({
            "videoId": f"vid{i:03d}", "artists": f"Artist {i}", "title": f"Track {i}",
            "tempo": rng.uniform(65, 85) if calm else rng.uniform(150, 175),
            "key": "A", "mode": "minor" if calm else "major",
            "minor_score": round(minor_score, 3),
            "bittersweet": round(1 - 2 * abs(minor_score - 0.5), 3),
            "percussive": rng.uniform(0.05, 0.15) if calm else rng.uniform(0.45, 0.6),
            "energy_rms": rng.uniform(0.05, 0.1) if calm else rng.uniform(0.25, 0.35),
            "brightness": rng.uniform(900, 1300) if calm else rng.uniform(2600, 3400),
            "onset_rate": rng.uniform(0.8, 1.6) if calm else rng.uniform(4.0, 6.0),
            "status": "ok",
        })
    return pd.DataFrame(rows)


def _saved_pair() -> tuple[str, str]:
    a = store.save_portrait(build_portrait(_df(40, 0.5, seed=42)), "демо")
    b = store.save_portrait(build_portrait(_df(40, 0.8, seed=7)), "плейлист YouTube Music")
    return a, b


# --- юнит: вектор-отпечаток и score ---

def test_fingerprint_vector_shape_and_scaling():
    portrait = build_portrait(_df(40, 0.5, seed=42))
    v = fingerprint_vector(portrait)
    assert len(v) == 4 + len(ARCHETYPE_NAMES)
    # хвост — доли архетипов 0..1, в сумме ~1 (все кластеры чем-то названы)
    tail = v[4:]
    assert (tail >= 0).all() and (tail <= 1).all()
    assert tail.sum() == pytest.approx(1.0, abs=0.05)  # округление share до целых %


def test_identical_portraits_score_100():
    portrait = build_portrait(_df(40, 0.5, seed=42))
    assert similarity_score(portrait, portrait) == 100


def test_score_symmetric_and_bounded():
    pa = build_portrait(_df(40, 0.5, seed=42))
    pb = build_portrait(_df(40, 0.8, seed=7))
    s = similarity_score(pa, pb)
    assert s == similarity_score(pb, pa)
    assert 0 <= s <= 100


# --- API /api/compare ---

def test_compare_api_contract():
    a, b = _saved_pair()
    r = client.get("/api/compare", params={"a": a, "b": b})
    assert r.status_code == 200
    data = r.json()
    assert 0 <= data["score"] <= 100
    assert isinstance(data["common_archetypes"], list)
    assert all(n in ARCHETYPE_NAMES for n in data["common_archetypes"])
    assert [f["name"] for f in data["facets"]] == ["темп", "минор", "яркость", "биттерсвит"]
    assert [f["unit"] for f in data["facets"]] == ["bpm", "%", "Гц", "%"]
    for f in data["facets"]:
        assert isinstance(f["a"], (int, float)) and isinstance(f["b"], (int, float))
    assert any(w in data["verdict"] for w in
               ("близнецы", "унисон", "общие волны", "противоположности"))
    assert data["a"]["id"] == a and data["b"]["id"] == b


def test_compare_api_symmetric_score():
    a, b = _saved_pair()
    s_ab = client.get("/api/compare", params={"a": a, "b": b}).json()["score"]
    s_ba = client.get("/api/compare", params={"a": b, "b": a}).json()["score"]
    assert s_ab == s_ba


def test_compare_identical_payload_two_ids_is_100():
    """Один и тот же портрет под двумя id — «музыкальные близнецы», score 100."""
    portrait = build_portrait(_df(40, 0.5, seed=42))
    a = store.save_portrait(portrait, "демо")
    b = store.save_portrait(portrait, "демо (копия)")
    data = client.get("/api/compare", params={"a": a, "b": b}).json()
    assert data["score"] == 100
    assert data["verdict"].startswith("музыкальные близнецы")
    assert data["common_archetypes"]  # архетипы совпадают полностью


def test_compare_same_id_422():
    a, _ = _saved_pair()
    assert client.get("/api/compare", params={"a": a, "b": a}).status_code == 422


def test_compare_unknown_id_404():
    a, _ = _saved_pair()
    assert client.get("/api/compare", params={"a": a, "b": "zzzzzzzz"}).status_code == 404
    assert client.get("/api/compare", params={"a": "zzzzzzzz", "b": a}).status_code == 404
