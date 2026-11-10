# Isolated Hermes Agent for exercising `hermes plugins install` end to end.
# Build context: a hermes-agent checkout (tracked files only, see run.sh).
FROM ghcr.io/astral-sh/uv:0.13.7-python3.14-trixie
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=hermes / /opt/hermes-agent
WORKDIR /opt/hermes-agent
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
COPY dev/pyproject.toml dev/uv.lock /opt/plugin-tools/
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_PROJECT_ENVIRONMENT=/opt/e2e-venv uv sync --project /opt/plugin-tools --locked --only-group e2e
ENV PATH=/opt/venv/bin:$PATH HERMES_AGENT_SRC=/opt/hermes-agent HERMES_HOME=/e2e/hermes-home
RUN mkdir -p /e2e/hermes-home && hermes --version
