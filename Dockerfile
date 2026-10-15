# syntax=docker/dockerfile:1
# Two stages: the builder has uv and resolves the locked dependencies; the
# runtime image only receives the finished virtualenv, so uv, caches and build
# tooling never ship to Cloud Run.

FROM ghcr.io/astral-sh/uv:0.12.24 AS uv

FROM python:3.13-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
# Use the image's interpreter, compile bytecode once at build time, and copy
# files instead of hard-linking from a cache mount that will not exist later.
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# Dependencies first: this layer is reused until uv.lock changes, so a code
# edit does not reinstall FastAPI. --locked fails if uv.lock is stale.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# --no-editable installs the package into the venv, so the runtime stage does
# not need the source tree.
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim AS runtime
# A fixed non-root UID: a compromised process cannot write to the image.
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=builder --chown=root:root /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
# Shell form so ${PORT} expands: Cloud Run injects PORT and the container must
# listen on it. exec keeps uvicorn as PID 1 so SIGTERM reaches it directly.
CMD exec uvicorn test_api.api:app --host 0.0.0.0 --port "${PORT:-8080}"
