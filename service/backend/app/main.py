"""tastemap backend — FastAPI-приложение.

Запуск (из service/backend): uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app import jobs
from app.config import settings
from app.engine import google_accounts
from app.routes import (
    analyze, auth, compare, discover, google, health, portrait, preview,
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # правило 30 дней (SPEC v0.5 §A3) — чистка на старте приложения;
    # сбой чистки не должен мешать запуску (повторится в youtube-джобах)
    try:
        google_accounts.purge_stale_youtube_data()
    except Exception:
        log.exception("purge_stale_youtube_data на старте упал")
    # resume-on-startup: незавершённые джобы из SQLite перезапускаются с начала
    # (конвейер идемпотентен благодаря кэшу дескрипторов); джобы старше суток
    # помечаются error. Сбой resume не должен мешать запуску приложения.
    try:
        resumed = jobs.resume_pending_jobs()
        if resumed:
            log.info("resume-on-startup: перезапущено джоб — %d", len(resumed))
    except Exception:
        log.exception("resume-on-startup на старте упал")
    yield


app = FastAPI(title="bittersweet backend", version="0.2.0", lifespan=lifespan)

# session-cookie (httpOnly, подписана itsdangerous — этим занимается SessionMiddleware).
# https_only=Secure-флаг: False на локалке, COOKIE_SECURE=1 на проде за HTTPS-прокси.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.cookie_secure,
)

# CORS для фронта: точный origin + credentials (cookie ходит через fetch)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(google.router)
app.include_router(portrait.router)
app.include_router(analyze.router)
app.include_router(compare.router)
app.include_router(discover.router)
app.include_router(preview.router)
