# Debug image for HLSS (High Level Screen Service).
# Build context is the repo root: `docker compose -f deploy/docker-compose.yml build`.
# Fixes vs the legacy root Dockerfile: no trailing-whitespace line-continuation
# breakage, PYTHONPATH so `hlss.main` imports, and an env-driven port.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

# System deps: Pillow + cairosvg (libcairo/pango/gdk-pixbuf), fonts, and the
# Stockfish engine for the local AI opponent.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libjpeg62-turbo-dev \
        zlib1g-dev \
        libpng-dev \
        libfreetype6-dev \
        fonts-dejavu-core \
        libcairo2 \
        libpango-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        stockfish \
        iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
# NOTE: requirements.txt omits python-multipart (it's in pyproject.toml but not
# the pinned list); FastAPI Form endpoints need it, so add it explicitly here.
RUN pip install --upgrade pip && pip install -r requirements.txt python-multipart

# Source (incl. src/hlss/assets and the __file__-relative fonts) + migrations.
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./

EXPOSE 8001

# PORT comes from the env_file (.env: PORT=8001). Shell form so ${PORT} expands.
CMD uvicorn hlss.main:app --host 0.0.0.0 --port ${PORT:-8001}
