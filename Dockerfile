FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
# 프로젝트 자체는 제외하고 의존성만 먼저 설치 (레이어 캐시)
RUN uv sync --frozen --no-dev --no-install-project

COPY stocker/ ./stocker/
RUN uv sync --frozen --no-dev
