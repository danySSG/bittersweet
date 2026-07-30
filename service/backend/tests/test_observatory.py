"""«Обсерватория» (SPEC v0.11 §A) — БЕЗ сети, синтетические DataFrame/payload.

Каждая модель: форма и инварианты (KDE нормирован, суммы колеса = n,
PCA explained убывает, t-SNE длина/диапазон, outliers <= 8 и why непусто,
clock null при < 50 датах, энтропия = log2(k) на равных долях) +
роут: 404/409 «старый портрет», ленивая сборка в payload, идемпотентность.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import store
from app.engine.observatory import (
    KDE_GRID_POINTS,
    OBSERVATORY_VERSION,
    build_observatory,
    clock,
    entropy,
    kde_profile,
    key_wheel,
    outliers,
    pca_axes,
    points_frame,
    radar,
    ridgelines,
    tsne_coords,
)
from app.engine.portrait import MOOD_FEATURES, build_portrait
from app.main import app
from app.routes import portrait as portrait_routes

client = TestClient(app)

PANEL_KEYS = {
    "n_tracks", "key_wheel", "ridgelines", "radar", "pca",
    "tsne", "outliers", "clock", "entropy",
}


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


# ------------------------------------------------------- синтетические данные

def _feature_row(i: int) -> dict:
    """Строка признаков: две выраженные группы (как в test_engine/test_timeline)."""
    rng = np.random.default_rng(42 + i)
    calm = i % 2 == 0
    minor_score = float(rng.uniform(0.68, 0.85) if calm else rng.uniform(0.15, 0.32))
    return {
        "videoId": f"vid{i:03d}",
        "artists": f"Artist {i}",
        "title": f"Track {i}",
        "tempo": float(rng.uniform(65, 85) if calm else rng.uniform(150, 175)),
        "key": "A" if calm else "C",
        "mode": "minor" if calm else "major",
        "minor_score": round(minor_score, 3),
        "bittersweet": round(1 - 2 * abs(minor_score - 0.5), 3),
        "percussive": float(rng.uniform(0.05, 0.15) if calm else rng.uniform(0.45, 0.6)),
        "energy_rms": float(rng.uniform(0.05, 0.1) if calm else rng.uniform(0.25, 0.35)),
        "brightness": float(rng.uniform(900, 1300) if calm else rng.uniform(2600, 3400)),
        "onset_rate": float(rng.uniform(0.8, 1.6) if calm else rng.uniform(4.0, 6.0)),
        "status": "ok",
    }


def _features_df(n: int = 40) -> pd.DataFrame:
    """DataFrame под build_portrait (вход конвейера)."""
    return pd.DataFrame([_feature_row(i) for i in range(n)])


def _df(n: int = 60) -> pd.DataFrame:
    """DataFrame в формате points_frame (вход моделей обсерватории)."""
    rows = []
    for i in range(n):
        r = _feature_row(i)
        rows.append({
            **{f: r[f] for f in MOOD_FEATURES},
            "label": f"{r['artists']} — {r['title']}",
            "videoId": r["videoId"],
            "cluster": i % 3,
            "meta": f"{r['tempo']:.0f}bpm · {r['key']} {r['mode']}",
            "liked_at": None,
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------- колесо квинт

def test_key_wheel_counts_sum_to_n_and_positions():
    metas = ["120bpm · C major"] * 3 + ["90bpm · A minor"] * 5 + ["100bpm · F# major"] * 2
    w = key_wheel(metas)
    assert w["order"] == ["C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F"]
    assert sum(w["major_counts"]) + sum(w["minor_counts"]) == 10 == w["n_keyed"]
    assert w["major_counts"][0] == 3   # C — позиция 0
    assert w["minor_counts"][3] == 5   # A — позиция 3 круга квинт
    assert w["major_counts"][6] == 2   # F# — позиция 6
    assert w["top"] == {"key": "A", "mode": "minor", "count": 5, "share": 0.5}
    total_share = sum(w["major_share"]) + sum(w["minor_share"])
    assert total_share == pytest.approx(1.0, abs=0.01)


def test_key_wheel_skips_points_without_key():
    w = key_wheel(["120bpm", "", "90bpm · A minor"])
    assert w["n_keyed"] == 1
    assert w["top"]["key"] == "A"


# ---------------------------------------------------------------- риджлайны

def test_kde_profile_shape_grid_range_and_normalization():
    rng = np.random.default_rng(0)
    v = rng.normal(120, 20, 300)
    prof = kde_profile(v)
    assert len(prof["grid"]) == len(prof["density"]) == KDE_GRID_POINTS
    assert max(prof["density"]) == 1.0  # нормирован к максимуму 1
    assert all(0 <= d <= 1 for d in prof["density"])
    p1, p99 = np.percentile(v, [1, 99])
    assert prof["grid"][0] == pytest.approx(p1, abs=0.01)
    assert prof["grid"][-1] == pytest.approx(p99, abs=0.01)
    assert prof["p25"] <= prof["median"] <= prof["p75"]


def test_kde_profile_constant_values_do_not_crash():
    prof = kde_profile(np.full(50, 3.14))
    assert len(prof["grid"]) == len(prof["density"]) == KDE_GRID_POINTS
    assert all(np.isfinite(prof["density"]))
    assert prof["median"] == pytest.approx(3.14)


def test_ridgelines_features_order_and_shape():
    profiles = ridgelines(_df())
    assert [p["feature"] for p in profiles] == [
        "tempo", "brightness", "minor_score", "bittersweet",
    ]
    for p in profiles:
        assert p["name_ru"]
        assert len(p["grid"]) == KDE_GRID_POINTS
        assert max(p["density"]) == 1.0


# --------------------------------------------------------------------- радар

def test_radar_percentile_against_reference():
    df = _df()
    # 3 значения ниже медианы портрета, 1 выше -> перцентиль 75
    ref = {
        f: [float(df[f].median()) - 1] * 3 + [float(df[f].median()) + 1]
        for f in MOOD_FEATURES
    }
    axes = radar(df, ref)["axes"]
    assert len(axes) == 7
    assert [a["feature"] for a in axes] == MOOD_FEATURES
    assert all(a["self_pct"] == 75.0 for a in axes)
    assert all(a["avg_pct"] == 50 for a in axes)
    assert all(a["name_ru"] for a in axes)


def test_radar_without_reference_is_neutral_50():
    axes = radar(_df(), None)["axes"]
    assert all(a["self_pct"] == 50.0 for a in axes)


# ----------------------------------------------------------------------- PCA

def test_pca_explained_decreasing_and_labels():
    p = pca_axes(_df())
    assert len(p["explained"]) == 2
    assert p["explained"][0] >= p["explained"][1] > 0  # explained убывает
    assert sum(p["explained"]) <= 100.01
    assert len(p["axes"]) == 2
    for ax in p["axes"]:
        assert set(ax["loadings"]) == set(MOOD_FEATURES)
        assert all(abs(w) <= 1.001 for w in ax["loadings"].values())
        # интерпретация — топ-3 |loadings| по-русски, полюса разные
        assert len(ax["label_pos"].split(" · ")) == 3
        assert len(ax["label_neg"].split(" · ")) == 3
        assert ax["label_pos"] != ax["label_neg"]


# --------------------------------------------------------------------- t-SNE

def test_tsne_length_order_and_range():
    n = 40
    t = tsne_coords(_df(n))
    assert len(t["x"]) == len(t["y"]) == n  # по индексам points
    for axis in (t["x"], t["y"]):
        assert min(axis) == 0.0 and max(axis) == 100.0  # нормировка в 0..100
        assert all(0 <= v <= 100 for v in axis)


# -------------------------------------------------------------- белые вороны

def test_outliers_at_most_8_scores_ranked_why_readable():
    df = _df(60)
    df.loc[0, ["tempo", "brightness"]] = [250.0, 9000.0]  # заведомая ворона
    out = outliers(df)
    assert 1 <= len(out) <= 8
    labels = [o["label"] for o in out]
    assert df["label"].iloc[0] in labels
    scores = [o["score"] for o in out]
    assert scores == sorted(scores, reverse=True)  # аномальнее — выше
    assert scores[0] == 100 and all(0 <= s <= 100 for s in scores)
    for o in out:
        assert len(o["why"]) == 2
        assert all("экстремально" in w and "(" in w for w in o["why"])
    crow = out[labels.index(df["label"].iloc[0])]
    assert any("быстрый (250 bpm)" in w for w in crow["why"])


# ---------------------------------------------------------------- часы вкуса

def _clock_df() -> pd.DataFrame:
    """52 датированных точки: 40 обычных в 12:00, по 6 биттерсвит в 23 и 00.

    2024-01-01 — понедельник; медиана brightness = 1000, биттерсвит-треки
    (bittersweet 0.95, brightness 3000) проходят портретное определение.
    """
    rows = [
        {"bittersweet": 0.1, "brightness": 1000.0,
         "liked_at": "2024-01-01T12:00:00+00:00"}
        for _ in range(40)
    ]
    for h in (23, 0):
        rows += [
            {"bittersweet": 0.95, "brightness": 3000.0,
             "liked_at": f"2024-01-01T{h:02d}:00:00+00:00"}
            for _ in range(6)
        ]
    return pd.DataFrame(rows)


def test_clock_null_when_dated_below_50():
    df = pd.DataFrame(
        [{"bittersweet": 0.1, "brightness": 1000.0,
          "liked_at": "2024-01-01T12:00:00+00:00"}] * 49
        + [{"bittersweet": 0.1, "brightness": 1000.0, "liked_at": None}] * 20
    )
    assert clock(df) is None


def test_clock_buckets_peak_and_cyclic_insight():
    c = clock(_clock_df())
    assert c is not None
    assert c["n_dated"] == 52
    assert len(c["hour_counts"]) == 24 and len(c["weekday_counts"]) == 7
    assert sum(c["hour_counts"]) == 52
    assert c["hour_counts"][12] == 40
    assert c["hour_counts"][23] == 6 and c["hour_counts"][0] == 6
    assert c["peak_hour"] == 12
    assert c["hour_bittersweet"][12] == 0.0
    assert c["hour_bittersweet"][23] == 1.0 and c["hour_bittersweet"][0] == 1.0
    assert c["hour_bittersweet"][5] is None  # < 3 треков в часе
    assert c["weekday_counts"][0] == 52      # понедельник
    assert c["weekday_bittersweet"][0] == round(12 / 52, 3)
    # циклическое окно 22-23-00 (12 треков >= 10, доля 1.0) побеждает
    assert c["insight"] == "биттерсвит чаще всего ты лайкаешь в 22—00"


def test_clock_insight_null_when_window_below_10_tracks():
    rows = [
        {"bittersweet": 0.1, "brightness": 1000.0,
         "liked_at": "2024-01-01T12:00:00+00:00"}
        for _ in range(44)
    ] + [
        {"bittersweet": 0.95, "brightness": 3000.0,
         "liked_at": "2024-01-01T03:00:00+00:00"}
        for _ in range(8)  # окно вокруг 03 — только 8 треков (< 10)
    ]
    c = clock(pd.DataFrame(rows))
    assert c is not None
    assert c["insight"] is None


# ------------------------------------------------------------------ энтропия

def test_entropy_equal_shares_equals_log2_k():
    e = entropy([0] * 10 + [1] * 10 + [2] * 10 + [3] * 10)
    assert e["h_bits"] == 2.0  # log2(4) — ручной расчёт
    assert e["effective_moods"] == 4.0
    assert e["verdict"] == "сфокусированный"


def test_entropy_verdict_thresholds():
    mono = entropy([0] * 30)
    assert mono["h_bits"] == 0.0 and mono["verdict"] == "монолитный вкус"
    assert entropy([0] * 10 + [1] * 10)["verdict"] == "монолитный вкус"  # H=1
    many = entropy(list(range(8)) * 5)
    assert many["h_bits"] == 3.0 and many["verdict"] == "многоликий"


# ------------------------------------------------------- сборка build_observatory

def test_points_frame_rejects_old_or_broken_points():
    with pytest.raises(ValueError):
        points_frame([])
    with pytest.raises(ValueError):
        points_frame([{"x": 1.0, "y": 2.0, "label": "без признаков"}])
    with pytest.raises(ValueError):
        points_frame([{"features": {"tempo": 120.0}, "label": "неполные"}])


def test_build_observatory_all_panels_from_real_payload():
    payload = build_portrait(_features_df(40))
    obs = build_observatory(payload)
    assert set(obs) == PANEL_KEYS
    n = payload["n_tracks"]
    assert obs["n_tracks"] == n
    assert len(obs["tsne"]["x"]) == len(payload["points"]) == n
    wheel = obs["key_wheel"]
    assert sum(wheel["major_counts"]) + sum(wheel["minor_counts"]) == n
    assert [p["feature"] for p in obs["ridgelines"]] == [
        "tempo", "brightness", "minor_score", "bittersweet",
    ]
    assert len(obs["radar"]["axes"]) == 7
    assert obs["pca"]["explained"][0] >= obs["pca"]["explained"][1]
    assert len(obs["outliers"]) <= 8
    assert obs["clock"] is None  # liked_at в точках нет
    assert obs["entropy"]["effective_moods"] >= 1.0


# ------------------------------------------------------------------ роут API

def _saved_portrait_id(n: int = 40) -> str:
    return store.save_portrait(build_portrait(_features_df(n)), source_label="демо")


def test_observatory_endpoint_computes_and_persists_in_payload():
    pid = _saved_portrait_id()
    r = client.get(f"/api/p/{pid}/observatory")
    assert r.status_code == 200
    obs = r.json()
    assert set(obs) == PANEL_KEYS
    # ленивая одноразовая сборка легла в payload портрета
    row = store.get_portrait(pid)
    assert row["portrait"]["observatory_version"] == OBSERVATORY_VERSION
    assert row["portrait"]["observatory"] == obs


def test_observatory_endpoint_idempotent_second_get_from_payload(monkeypatch):
    pid = _saved_portrait_id()
    assert client.get(f"/api/p/{pid}/observatory").status_code == 200
    # маркер прямо в payload: второй GET обязан отдать его, а не пересчитать
    marker = {"marker": "из payload, без пересчёта"}
    store.save_observatory(pid, marker, version=OBSERVATORY_VERSION)

    def _boom(*args, **kwargs):
        raise AssertionError("второй GET не должен пересчитывать обсерваторию")

    monkeypatch.setattr(portrait_routes, "build_observatory", _boom)
    r2 = client.get(f"/api/p/{pid}/observatory")
    assert r2.status_code == 200
    assert r2.json() == marker


def test_observatory_409_on_portrait_without_features():
    payload = build_portrait(_features_df(40))
    for p in payload["points"]:
        p.pop("features")
    pid = store.save_portrait(payload, source_label="демо")
    r = client.get(f"/api/p/{pid}/observatory")
    assert r.status_code == 409
    assert "старой версии" in r.json()["detail"]


def test_observatory_404_on_unknown_portrait():
    assert client.get("/api/p/zzzzzzzz/observatory").status_code == 404


def test_radar_reference_prefers_saved_portraits_when_enough():
    payload = build_portrait(_features_df(40))
    for _ in range(store.PERCENTILE_MIN_PORTRAITS):
        store.save_portrait(payload, source_label="демо")
    ref = portrait_routes._radar_reference()
    assert set(ref) == set(MOOD_FEATURES)
    assert all(len(v) == store.PERCENTILE_MIN_PORTRAITS for v in ref.values())


def test_store_list_feature_medians_skips_old_portraits():
    payload = build_portrait(_features_df(40))
    old = build_portrait(_features_df(40))
    for p in old["points"]:
        p.pop("features")
    store.save_portrait(payload, source_label="демо")
    store.save_portrait(old, source_label="демо")
    medians = store.list_feature_medians(MOOD_FEATURES)
    assert len(medians) == 1
    assert set(medians[0]) == set(MOOD_FEATURES)
