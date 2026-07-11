FROM python:3.13-slim

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml README.md ./
RUN mkdir -p src/pinchana_pinterest && touch src/pinchana_pinterest/__init__.py \
    && uv sync --no-dev --no-install-project

COPY src ./src
RUN uv sync --no-dev

RUN mkdir -p /app/cache
ENV CACHE_PATH=/app/cache \
    CACHE_MAX_SIZE_GB=2 \
    MAX_MEDIA_SIZE_MB=100

EXPOSE 8090
CMD ["uv", "run", "uvicorn", "pinchana_pinterest.main:app", "--host", "0.0.0.0", "--port", "8090"]

