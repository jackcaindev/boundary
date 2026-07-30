FROM ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm-slim

WORKDIR /app/sample-agent

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY sample-agent/pyproject.toml sample-agent/uv.lock ./
RUN uv sync --locked --no-install-project

COPY sample-agent/src ./src
COPY sample-agent/tests ./tests
COPY contract-fixtures /app/contract-fixtures
RUN uv sync --locked

CMD ["/app/sample-agent/.venv/bin/uvicorn", "sample_agent.main:app", "--host", "0.0.0.0", "--port", "8001"]
