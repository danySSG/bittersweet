"""Discovery «найти ещё такого» (SPEC v0.4 §A) — БЕЗ сети (всё monkeypatch).

Юниты движка (сиды/кандидаты/match) + полный цикл джобы через TestClient:
радио (get_watch_playlist), превью-каскад и librosa-анализ замоканы.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import discovery, features, previews, store
from app.engine.portrait import MOOD_FEATURES
from app.main import app

client = TestClient(app)

BASE = {
    "tempo": 120.0, "minor_score": 0.7, "bittersweet": 0.6, "percussive": 0.3,
    "energy_rms": 0.2, "brightness": 2000.0, "onset_rate": 3.0,
}


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


def _feats(**over) -> dict:
    f = dict(BASE)
    f.update(over)
    return f


def _point(
    video_id: str | None, cluster: int, x: float = 50.0, y: float = 50.0, **over
) -> dict:
    return {
        "x": x, "y": y, "cluster": cluster,
        "label": f"Artist — {video_id or 'без id'}", "meta": "120bpm",
        "videoId": video_id, "features": _feats(**over),
    }


def _cluster(label: str) -> dict:
    return {
        "label": label, "archetype": {"name": "грустный бэнгер", "emoji": "🌒"},
        "size": 2, "share": 50,
        "medians": {"tempo": 160, "minor_share": 80, "brightness": 2000},
        "examples": ["A — B"], "color": "#7F77DD",
    }


def _portrait(points: list[dict]) -> dict:
    n_clusters = max(p["cluster"] for p in points) + 1
    return {
        "n_tracks": len(points),
        "clusters": [_cluster(f"кластер {i}") for i in range(n_clusters)],
        "bittersweet": {"count": 0, "share": 0, "top": [], "percentile": None},
        "highlights": [],
        "points": points,
        "fingerprint": {"tempo_median": 120, "minor_share": 80, "brightness_mean": 2000},
    }


def _two_cluster_portrait() -> dict:
    """Кластер 0: темп 150/170 (центроид 160); кластер 1: 60/80, ярче."""
    return _portrait([
        _point("vidAAA00001", 0, tempo=150.0),
        _point("vidAAA00002", 0, tempo=170.0),
        _point("vidBBB00001", 1, tempo=60.0, brightness=3000.0),
        _point("vidBBB00002", 1, tempo=80.0, brightness=3000.0),
    ])


# ---------- сиды ----------

def test_seeds_nearest_to_centroid_first():
    """Сиды сортируются по близости к центроиду кластера (центроид: темп 125)."""
    portrait = _portrait([
        _point("vidfar00001", 0, tempo=100.0),
        _point("vidnear0001", 0, tempo=120.0),
        _point("vidbest0001", 0, tempo=125.0),
        _point(None, 0, tempo=155.0),          # без videoId — в центроиде, но не сид
        _point("vidother001", 1, tempo=200.0),  # чужой кластер
    ])
    seeds = discovery.select_seeds(portrait, 0)
    assert seeds == ["vidbest0001", "vidnear0001", "vidfar00001"]


def test_seeds_skip_points_without_video_id_and_cap_at_limit():
    tempos = [125, 126, 124, 128, 122, 150, 90]  # центроид ~123.6
    points = [_point(f"vid{i:08d}", 0, tempo=float(t)) for i, t in enumerate(tempos)]
    points.append(_point(None, 0, tempo=123.0))  # самый близкий, но без videoId
    portrait = _portrait(points)
    seeds = discovery.select_seeds(portrait, 0)
    assert len(seeds) == discovery.SEED_LIMIT
    assert "vid00000005" not in seeds and "vid00000006" not in seeds  # дальние за лимитом
    assert None not in seeds


def test_supports_discovery_old_portrait_false():
    old = _portrait([_point("vidAAA00001", 0)])
    for p in old["points"]:
        del p["videoId"]  # портрет, сохранённый до v0.4
    assert discovery.supports_discovery(old) is False
    assert discovery.supports_discovery({"points": []}) is False
    assert discovery.supports_discovery(_two_cluster_portrait()) is True


def test_supports_discovery_requires_features():
    portrait = _portrait([_point("vidAAA00001", 0)])
    del portrait["points"][0]["features"]
    assert discovery.supports_discovery(portrait) is False


# ---------- point-режим: сиды и target по точке карты (SPEC v0.9 §B) ----------

def test_nearest_points_sorted_and_skip_without_video_id():
    """Ближайшие к (x, y) — первыми; точки без videoId не участвуют."""
    portrait = _portrait([
        _point("vidnear0001", 0, x=10.0, y=10.0),
        _point("vidmid00001", 0, x=30.0, y=10.0),
        _point(None, 0, x=12.0, y=10.0),        # самая близкая, но без videoId
        _point("vidfar00001", 0, x=90.0, y=90.0),
    ])
    near = discovery.nearest_points(portrait, 12.0, 10.0)
    assert [p["videoId"] for p in near] == ["vidnear0001", "vidmid00001", "vidfar00001"]


def test_nearest_points_caps_at_k():
    points = [_point(f"vid{i:08d}", 0, x=float(i)) for i in range(12)]
    portrait = _portrait(points)
    near = discovery.nearest_points(portrait, 0.0, 50.0)
    assert len(near) == discovery.POINT_NEIGHBORS
    assert [p["videoId"] for p in near] == [
        f"vid{i:08d}" for i in range(discovery.POINT_NEIGHBORS)
    ]


def test_point_target_seeds_vector_and_key():
    """Сиды — до 5 из 7 ближайших; ключ и label — по округлённым координатам."""
    points = [_point(f"vid{i:08d}", 0, x=float(i), y=0.0, tempo=100.0 + i)
              for i in range(9)]
    portrait = _portrait(points)
    target = discovery.point_target(portrait, 0.4, 0.3)
    assert target["seeds"] == [f"vid{i:08d}" for i in range(discovery.SEED_LIMIT)]
    assert target["store_key"] == "point:0:0"

    info = target["result_extra"]["target"]
    assert info["kind"] == "point"
    assert info["x"] == 0.4 and info["y"] == 0.3
    assert info["label"] == "точка карты: настроение 0 · энергия 0"
    assert info["near"] == [points[i]["label"] for i in range(discovery.POINT_NEAR_LABELS)]

    # target-вектор = среднее MOOD_FEATURES 7 ближайших в z-шкале библиотеки:
    # темпы ближайших 100..106 (среднее 103), библиотека 100..108 (среднее 104)
    mean, std = discovery.library_space(portrait)
    tempo_idx = MOOD_FEATURES.index("tempo")
    assert target["centroid"][tempo_idx] == pytest.approx((103 - mean[tempo_idx]) / std[tempo_idx])


def test_point_target_too_few_neighbors_raises():
    """< 3 пригодных точек рядом — человеческий ValueError (HTTP-слой даст 409)."""
    portrait = _portrait([
        _point("vidAAA00001", 0),
        _point("vidBBB00001", 0),
        _point(None, 0),
        _point(None, 0),
    ])
    with pytest.raises(ValueError, match="слишком мало"):
        discovery.point_target(portrait, 50.0, 50.0)


def test_cluster_target_matches_legacy_pipeline_inputs():
    """cluster_target отдаёт те же сиды и вектор, что старые select_seeds/cluster_space."""
    portrait = _two_cluster_portrait()
    target = discovery.cluster_target(portrait, 0)
    centroid, mean, std = discovery.cluster_space(portrait, 0)
    assert target["seeds"] == discovery.select_seeds(portrait, 0)
    assert target["centroid"] == pytest.approx(centroid)
    assert target["mean"] == pytest.approx(mean) and target["std"] == pytest.approx(std)
    assert target["store_key"] == "0"
    assert target["result_extra"]["cluster_label"] == "кластер 0"
    assert target["result_extra"]["target"] == {"kind": "cluster", "label": "кластер 0"}


# ---------- кандидаты (радио) ----------

def _radio_track(vid: str, artist: str, title: str | None = None) -> dict:
    return {"videoId": vid, "title": title or f"Track {vid}",
            "artists": [{"name": artist}]}


def test_collect_candidates_dedup_exclude_and_artist_cap(monkeypatch):
    radio_by_seed = {
        "seed0000001": {"tracks": [
            _radio_track("cand0000001", "Alpha"),
            _radio_track("cand0000001", "Alpha"),          # дубль videoId
            _radio_track("vidAAA00001", "Own"),            # трек портрета — исключить
            _radio_track("cand0000002", "Spammer"),
            _radio_track("cand0000003", "Spammer"),
            _radio_track("cand0000004", "Spammer"),        # 3-й на артиста — мимо
            {"videoId": "candNoTitle", "artists": [{"name": "X"}]},  # без title — мимо
        ]},
        "seed0000002": {"tracks": [
            _radio_track("cand0000001", "Alpha"),          # дубль из другого сида
            _radio_track("cand0000005", "Beta"),
        ]},
    }
    monkeypatch.setattr(
        discovery, "fetch_radio", lambda vid, limit=15: radio_by_seed[vid]
    )
    cands = discovery.collect_candidates(
        ["seed0000001", "seed0000002"], exclude={"vidAAA00001"}, cap=50
    )
    assert [c["videoId"] for c in cands] == [
        "cand0000001", "cand0000002", "cand0000003", "cand0000005"
    ]
    assert cands[0]["artist"] == "Alpha"


def test_collect_candidates_broken_seed_skipped(monkeypatch):
    """get_watch_playlist падает на сиде — сид пропускается, джоба живёт."""
    def radio(vid, limit=15):
        if vid == "seedbroken1":
            raise RuntimeError("watch playlist недоступен")
        return {"tracks": [_radio_track("cand0000001", "Alpha")]}

    monkeypatch.setattr(discovery, "fetch_radio", radio)
    cands = discovery.collect_candidates(
        ["seedbroken1", "seed0000002"], exclude=set(), cap=50
    )
    assert [c["videoId"] for c in cands] == ["cand0000001"]


def test_collect_candidates_cap_drops_tail(monkeypatch):
    tracks = [_radio_track(f"cand{i:07d}", f"Artist {i}") for i in range(10)]
    monkeypatch.setattr(discovery, "fetch_radio", lambda vid, limit=15: {"tracks": tracks})
    cands = discovery.collect_candidates(["seed0000001"], exclude=set(), cap=4)
    assert [c["videoId"] for c in cands] == [f"cand{i:07d}" for i in range(4)]


# ---------- match ----------

def test_match_tempo_octave_tolerant():
    """Кандидат с темпом t/2 (и 2t) от центроида НЕ штрафуется — урок «kah»."""
    portrait = _two_cluster_portrait()
    centroid, mean, std = discovery.cluster_space(portrait, 0)
    exact = discovery.match_score(_feats(tempo=160.0), centroid, mean, std)
    half = discovery.match_score(_feats(tempo=80.0), centroid, mean, std)
    double = discovery.match_score(_feats(tempo=320.0), centroid, mean, std)
    off = discovery.match_score(_feats(tempo=100.0), centroid, mean, std)
    assert exact == 100
    assert half == exact and double == exact  # октава — не ошибка
    assert off < exact                        # неоктавное отклонение — штраф


def test_match_monotonic_and_bounded():
    portrait = _two_cluster_portrait()
    centroid, mean, std = discovery.cluster_space(portrait, 0)
    offsets = [0, 200, 500, 1000, 5000, 50000]
    scores = [
        discovery.match_score(_feats(tempo=160.0, brightness=2000.0 + o), centroid, mean, std)
        for o in offsets
    ]
    assert scores[0] == 100
    assert all(a >= b for a, b in zip(scores, scores[1:]))  # монотонность по расстоянию
    assert all(0 <= s <= 100 for s in scores)
    assert scores[-1] == 0  # очень далеко -> 0, не отрицательное


# ---------- store.update_discoveries ----------

def test_update_discoveries_appends_and_overwrites():
    pid = store.save_portrait(_two_cluster_portrait(), source_label="демо")
    assert store.update_discoveries(pid, 0, {"discoveries": [{"match": 90}]}) is True
    assert store.update_discoveries(pid, 1, {"discoveries": []}) is True
    assert store.update_discoveries(pid, 0, {"discoveries": [{"match": 55}]}) is True

    payload = store.get_portrait(pid)["portrait"]
    assert payload["discoveries"]["0"] == {"discoveries": [{"match": 55}]}  # перезапись
    assert payload["discoveries"]["1"] == {"discoveries": []}
    assert payload["points"]  # остальной payload не тронут

    assert store.update_discoveries("nope1234", 0, {}) is False


def test_update_discoveries_point_key_appends_and_overwrites():
    """Обобщённый ключ (SPEC v0.9 §B): point:x:y живёт рядом с кластерными."""
    pid = store.save_portrait(_two_cluster_portrait(), source_label="демо")
    assert store.update_discoveries(pid, 0, {"discoveries": [{"match": 90}]}) is True
    assert store.update_discoveries(pid, "point:50:50", {"discoveries": [{"match": 70}]}) is True
    assert store.update_discoveries(pid, "point:50:50", {"discoveries": [{"match": 44}]}) is True

    payload = store.get_portrait(pid)["portrait"]
    assert payload["discoveries"]["0"] == {"discoveries": [{"match": 90}]}
    assert payload["discoveries"]["point:50:50"] == {"discoveries": [{"match": 44}]}  # перезапись


# ---------- джоба: полный цикл с моками ----------

def _poll_until_final(job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    state: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        state = r.json()
        if state["status"] in ("done", "error"):
            return state
        time.sleep(0.05)
    raise AssertionError(f"джоба {job_id} не завершилась за {timeout} c: {state}")


@pytest.fixture()
def _fake_analysis(monkeypatch):
    """Превью-каскад и librosa без сети: признаки кандидата — по его title.

    Библиотека _two_cluster_portrait: центроид кластера 0 — темп 160,
    brightness 2000 (z-шкала brightness: mean 2500, std 500).
    """
    by_title = {
        "exact": _feats(tempo=160.0),                       # match 100
        "half-tempo": _feats(tempo=80.0),                   # октава -> тоже 100
        "near": _feats(tempo=160.0, brightness=2300.0),     # match ~74
        "far": _feats(tempo=160.0, brightness=2800.0),      # match ~45
        "alien": _feats(tempo=160.0, brightness=20000.0),   # match ~0 -> отсев
    }
    current = {"feats": None}

    def fake_preview(isrc, artist, title, client=None):
        if title == "no-preview":
            return None
        current["feats"] = {**by_title[title], "key": "A", "mode": "minor"}
        return f"https://cdn.example/{title}.mp3"

    monkeypatch.setattr(previews, "match_track_to_preview", fake_preview)
    monkeypatch.setattr(previews, "download_preview", lambda url, dest, client=None: True)
    monkeypatch.setattr(features, "analyze", lambda path: current["feats"])


@pytest.fixture()
def _fake_radio(monkeypatch):
    tracks = [
        _radio_track("candalien01", "Alien", "alien"),
        _radio_track("candfar0001", "Far", "far"),
        _radio_track("candexact01", "Exact", "exact"),
        _radio_track("candnear001", "Near", "near"),
        _radio_track("candhalf001", "Half", "half-tempo"),
        _radio_track("candnoprev1", "Silent", "no-preview"),
    ]
    calls: list[str] = []

    def radio(video_id, limit=15):
        calls.append(video_id)
        return {"tracks": tracks}

    monkeypatch.setattr(discovery, "fetch_radio", radio)
    return calls


def test_discovery_job_full_cycle(_fake_radio, _fake_analysis):
    pid = store.save_portrait(_two_cluster_portrait(), source_label="демо")
    r = client.post("/api/discover", json={"portrait_id": pid, "cluster": 0})
    assert r.status_code == 202
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "done", state["error"]

    # радио строилось по обоим сидам кластера 0
    assert sorted(_fake_radio) == ["vidAAA00001", "vidAAA00002"]

    assert state["progress"]["stage"] == "analyzing"  # финальный этап
    assert state["progress"]["done"] == state["progress"]["total"] == 6

    result = state["result"]
    assert result["cluster_label"] == "кластер 0"
    assert result["archetype"] == {"name": "грустный бэнгер", "emoji": "🌒"}

    found = result["discoveries"]
    # оба стопроцентных — сверху (точный темп и его октава)
    assert {f["videoId"] for f in found[:2]} == {"candexact01", "candhalf001"}
    assert {f["videoId"] for f in found} == {
        "candexact01", "candhalf001", "candnear001", "candfar0001"
    }  # alien — ниже порога, no-preview — без превью
    matches = [f["match"] for f in found]
    assert matches == sorted(matches, reverse=True)  # match убывает
    assert all(m >= discovery.MATCH_THRESHOLD for m in matches)
    for f in found:
        assert set(f) == {"artist", "title", "videoId", "match",
                          "tempo", "key", "mode", "preview_url"}
        assert f["preview_url"].startswith("https://cdn.example/")
        assert isinstance(f["tempo"], int) and f["mode"] == "minor"

    ids = ",".join(f["videoId"] for f in found)
    assert result["listen_all_url"] == f"https://www.youtube.com/watch_videos?video_ids={ids}"

    # находки дописаны в payload портрета — /p/{id} отдаёт их без пересчёта
    saved = client.get(f"/api/p/{pid}")
    assert saved.status_code == 200
    assert saved.json()["discoveries"]["0"] == result


def test_discovery_job_limit_truncates(_fake_radio, _fake_analysis):
    pid = store.save_portrait(_two_cluster_portrait(), source_label="демо")
    r = client.post("/api/discover", json={"portrait_id": pid, "cluster": 0, "limit": 2})
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "done", state["error"]
    found = state["result"]["discoveries"]
    assert len(found) == 2
    assert all(f["match"] == 100 for f in found)  # остались лучшие


def test_discovery_job_all_seeds_broken_is_human_error(monkeypatch, _fake_analysis):
    def boom(video_id, limit=15):
        raise RuntimeError("get_watch_playlist упал")

    monkeypatch.setattr(discovery, "fetch_radio", boom)
    pid = store.save_portrait(_two_cluster_portrait(), source_label="демо")
    r = client.post("/api/discover", json={"portrait_id": pid, "cluster": 0})
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "error"
    assert "радио не дало кандидатов" in state["error"]


def test_discovery_job_cluster_without_video_ids_errors(_fake_radio, _fake_analysis):
    portrait = _portrait([
        _point("vidAAA00001", 0),
        _point(None, 1, tempo=60.0),
        _point(None, 1, tempo=80.0),
    ])
    pid = store.save_portrait(portrait, source_label="демо")
    r = client.post("/api/discover", json={"portrait_id": pid, "cluster": 1})
    assert r.status_code == 202
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "error"
    assert "нет треков с videoId" in state["error"]


def test_point_discovery_job_full_cycle(_fake_radio, _fake_analysis):
    """Point-режим (SPEC v0.9 §B): тот же конвейер, target в результате,
    находки в payload под ключом point:x:y."""
    pid = store.save_portrait(_two_cluster_portrait(), source_label="демо")
    r = client.post(
        "/api/discover", json={"portrait_id": pid, "point": {"x": 50.0, "y": 50.0}}
    )
    assert r.status_code == 202
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "done", state["error"]

    # сиды — до 5 ближайших к точке (все 4 точки портрета на 50/50)
    assert sorted(_fake_radio) == [
        "vidAAA00001", "vidAAA00002", "vidBBB00001", "vidBBB00002"
    ]

    result = state["result"]
    assert "cluster_label" not in result and "archetype" not in result
    target = result["target"]
    assert target["kind"] == "point"
    assert target["x"] == 50.0 and target["y"] == 50.0
    assert target["label"] == "точка карты: настроение 50 · энергия 50"
    assert len(target["near"]) == 3
    assert all(near.startswith("Artist — ") for near in target["near"])

    found = result["discoveries"]
    assert found  # конвейер тот же: превью-каскад, порог, сортировка
    matches = [f["match"] for f in found]
    assert matches == sorted(matches, reverse=True)
    assert all(m >= discovery.MATCH_THRESHOLD for m in matches)
    ids = ",".join(f["videoId"] for f in found)
    assert result["listen_all_url"] == f"https://www.youtube.com/watch_videos?video_ids={ids}"

    # payload: находки под ключом point:{round(x)}:{round(y)}
    saved = client.get(f"/api/p/{pid}")
    assert saved.status_code == 200
    assert saved.json()["discoveries"]["point:50:50"] == result


def test_cluster_discovery_result_gets_additive_target(_fake_radio, _fake_analysis):
    """Cluster-режим: прежние поля не тронуты, target добавлен аддитивно."""
    pid = store.save_portrait(_two_cluster_portrait(), source_label="демо")
    r = client.post("/api/discover", json={"portrait_id": pid, "cluster": 0})
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "done", state["error"]
    result = state["result"]
    assert result["cluster_label"] == "кластер 0"
    assert result["archetype"] == {"name": "грустный бэнгер", "emoji": "🌒"}
    assert result["target"] == {"kind": "cluster", "label": "кластер 0"}


# ---------- HTTP-валидация: 404 / 422 / 409 ----------

def test_discover_404_unknown_portrait():
    r = client.post("/api/discover", json={"portrait_id": "zzzzzzzz", "cluster": 0})
    assert r.status_code == 404


def test_discover_422_bad_cluster_and_limit():
    pid = store.save_portrait(_two_cluster_portrait(), source_label="демо")
    assert client.post(
        "/api/discover", json={"portrait_id": pid, "cluster": 99}
    ).status_code == 422
    assert client.post(
        "/api/discover", json={"portrait_id": pid, "cluster": -1}
    ).status_code == 422
    assert client.post(
        "/api/discover", json={"portrait_id": pid, "cluster": 0, "limit": 26}
    ).status_code == 422


def test_discover_422_exactly_one_of_cluster_point():
    """Ровно один из cluster/point: оба или ни одного -> 422 (SPEC v0.9 §B)."""
    pid = store.save_portrait(_two_cluster_portrait(), source_label="демо")
    assert client.post(
        "/api/discover", json={"portrait_id": pid}
    ).status_code == 422  # ни одного
    assert client.post(
        "/api/discover",
        json={"portrait_id": pid, "cluster": 0, "point": {"x": 50, "y": 50}},
    ).status_code == 422  # оба


def test_discover_422_point_out_of_bounds():
    pid = store.save_portrait(_two_cluster_portrait(), source_label="демо")
    for bad in ({"x": -1, "y": 50}, {"x": 50, "y": 101}, {"x": 50}):
        assert client.post(
            "/api/discover", json={"portrait_id": pid, "point": bad}
        ).status_code == 422, bad


def test_discover_point_409_too_few_neighbors():
    """Рядом с точкой < 3 пригодных треков — честный 409 с человеческим текстом."""
    portrait = _portrait([
        _point("vidAAA00001", 0),
        _point("vidBBB00001", 0),
        _point(None, 0),
        _point(None, 0),
    ])
    pid = store.save_portrait(portrait, source_label="демо")
    r = client.post(
        "/api/discover", json={"portrait_id": pid, "point": {"x": 50, "y": 50}}
    )
    assert r.status_code == 409
    assert "слишком мало" in r.json()["detail"]


def test_discover_point_409_old_portrait():
    """Старый портрет (points без videoId/features) — 409 и в point-режиме."""
    old = _two_cluster_portrait()
    for p in old["points"]:
        del p["videoId"]
        del p["features"]
    pid = store.save_portrait(old, source_label="демо")
    r = client.post(
        "/api/discover", json={"portrait_id": pid, "point": {"x": 50, "y": 50}}
    )
    assert r.status_code == 409
    assert "старой версией" in r.json()["detail"]


def test_discover_409_old_portrait():
    """Портрет, сохранённый до v0.4 (points без videoId), — честный 409."""
    old = _two_cluster_portrait()
    for p in old["points"]:
        del p["videoId"]
        del p["features"]
    pid = store.save_portrait(old, source_label="демо")
    r = client.post("/api/discover", json={"portrait_id": pid, "cluster": 0})
    assert r.status_code == 409
    assert "старой версией" in r.json()["detail"]
