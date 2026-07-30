# Bittersweet backend (SPEC v0.14 §B).
# Сборка из service/backend:  docker build -t bittersweet-backend .
# Запуск:  docker run --rm -p 8000:8000 --env-file .env bittersweet-backend

FROM python:3.12-slim

# ffmpeg обязателен: librosa/audioread декодируют m4a/mp3-превью через него.
# Слой стоит первым — он тяжёлый и меняется реже всего.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv — из официального образа, без curl-пайпов. Пин = локальной версии
# (uv 0.10.12 на машине разработки), чтобы uv.lock читался одинаково.
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Слой зависимостей: кэшируется, пока pyproject.toml/uv.lock не меняются.
# --no-install-project: сам проект не пакетируется, код приложения копируем ниже.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Код приложения — отдельным (дешёвым) слоем.
COPY app ./app

# Непривилегированный пользователь; /app должен быть записываемым:
# дефолтный cache_db = /app/cache.db (на проде переопределяется CACHE_DB на volume).
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# healthcheck остаётся GET /health (exec-форма: без shell-квотинга, stdlib-only).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4)"]

# sh -c ради ${PORT:-8000}; exec — чтобы uvicorn получал сигналы (graceful stop).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
