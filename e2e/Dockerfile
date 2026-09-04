# Isolated Hermes Agent for exercising `hermes plugins install` end to end.
# Build context: a hermes-agent checkout (tracked files only, see run.sh).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=hermes / /opt/hermes-agent
WORKDIR /opt/hermes-agent
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev \
    && uv pip install --python /opt/venv/bin/python pexpect pyte
ENV PATH=/opt/venv/bin:$PATH HERMES_AGENT_SRC=/opt/hermes-agent HERMES_HOME=/e2e/hermes-home
RUN mkdir -p /e2e/hermes-home && hermes --version
