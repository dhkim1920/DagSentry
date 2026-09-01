# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG UV_VERSION=0.11.8

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/dagsentry-venv

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime

ENV PATH=/opt/dagsentry-venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 dagsentry \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin dagsentry

WORKDIR /opt/dagsentry
COPY --from=builder /opt/dagsentry-venv /opt/dagsentry-venv
COPY --chown=dagsentry:dagsentry alembic.ini ./
COPY --chown=dagsentry:dagsentry migrations ./migrations
COPY --chmod=0555 deployment/container-entrypoint.sh /usr/local/bin/dagsentry-entrypoint

USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/dagsentry-entrypoint"]
CMD ["dagsentry-api"]
