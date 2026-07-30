from fastapi.testclient import TestClient

from app.main import app


def test_health():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_spotify_login_503_in_demo_mode(monkeypatch):
    """Без SPOTIFY_CLIENT_ID логин отвечает 503 с понятным JSON."""
    from app.config import settings

    monkeypatch.setattr(settings, "spotify_client_id", "")
    client = TestClient(app)
    r = client.get("/auth/spotify/login", follow_redirects=False)
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "spotify_not_configured"
    assert body["demo_endpoint"] == "/api/demo/portrait"
