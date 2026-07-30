"""Настройки сервиса (pydantic-settings, .env)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app -> backend -> service -> корень репозитория.
# Абсолютные пути от расположения файла, НЕ от cwd — иначе тесты/uvicorn
# сломаются при запуске из другой директории.
BACKEND_DIR = Path(__file__).resolve().parents[1]
try:
    REPO_ROOT = Path(__file__).resolve().parents[3]
except IndexError:
    # В Docker дерево короче (/app/app/config.py — выше только /app и /),
    # parents[3] не существует. Демо-CSV там задаётся через DEMO_FEATURES_CSV.
    REPO_ROOT = BACKEND_DIR


class Settings(BaseSettings):
    # Spotify OAuth (PKCE). Пустой client_id = сервис работает в demo-режиме.
    spotify_client_id: str = ""
    spotify_client_secret: str = ""  # для PKCE не обязателен, оставлен на будущее
    spotify_redirect_uri: str = "http://localhost:8000/auth/spotify/callback"
    spotify_scope: str = "user-top-read user-library-read"

    # Google OAuth (Authorization Code, scope youtube.readonly).
    # Пустой client_id = все /auth/google/* отвечают 503 (см. README, «Настройка GCP»).
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    session_secret: str = "dev-secret-change-me"

    # Secure-флаг session-cookie. False для локалки (http://localhost),
    # на проде за HTTPS-прокси — env COOKIE_SECURE=1. samesite остаётся lax.
    cookie_secure: bool = False

    # Готовый CSV признаков из эксперимента — источник demo-портрета.
    demo_features_csv: Path = REPO_ROOT / "data" / "features_full.csv"

    # SQLite-кэш признаков по ISRC.
    cache_db: Path = BACKEND_DIR / "cache.db"

    frontend_url: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
