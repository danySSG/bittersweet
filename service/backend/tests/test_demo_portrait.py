from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_demo_portrait_ok():
    r = client.get("/api/demo/portrait")
    assert r.status_code == 200


def test_demo_portrait_contract():
    data = client.get("/api/demo/portrait").json()

    assert data["n_tracks"] > 0

    clusters = data["clusters"]
    assert len(clusters) >= 2
    for c in clusters:
        assert set(c) == {"label", "archetype", "size", "share", "medians",
                          "examples", "examples_rich", "color"}
        assert c["archetype"]["name"] and c["archetype"]["emoji"]
        assert c["size"] > 0 and 0 <= c["share"] <= 100
        assert {"tempo", "minor_share", "brightness"} <= set(c["medians"])
        assert c["examples"] and all(" — " in e for e in c["examples"])
        # SPEC v0.6 §A2: examples_rich — те же треки + preview_url (может null);
        # SPEC v0.10 §D: + videoId (может null) — полный YouTube-плеер
        assert [e["label"] for e in c["examples_rich"]] == c["examples"]
        for e in c["examples_rich"]:
            assert set(e) == {"label", "preview_url", "videoId"}
            assert e["preview_url"] is None or e["preview_url"].startswith("http")
        assert c["color"].startswith("#")
    assert sum(c["size"] for c in clusters) == data["n_tracks"]

    # SPEC v0.6 §A2: хайлайты проигрываются — preview_url в контракте (может null);
    # SPEC v0.10 §D: videoId — в demo-CSV настоящие id, здесь non-null
    for h in data["highlights"]:
        assert "preview_url" in h
        assert h["videoId"]

    # SPEC v0.10 §A: timeline в контракте; в demo-CSV нет liked_at -> null
    assert "timeline" in data
    assert data["timeline"] is None

    bs = data["bittersweet"]
    assert {"count", "share", "top"} <= set(bs)

    points = data["points"]
    assert len(points) == data["n_tracks"]
    for p in points[:20]:
        assert 0 <= p["x"] <= 100 and 0 <= p["y"] <= 100
        assert 0 <= p["cluster"] < len(clusters)
        assert p["label"] and p["meta"]

    fp = data["fingerprint"]
    assert {"tempo_median", "minor_share", "brightness_mean"} <= set(fp)


def test_demo_unauthorized_spotify_portrait():
    """Spotify-режим без сессии — 401, а не падение."""
    r = client.get("/api/portrait")
    assert r.status_code == 401
