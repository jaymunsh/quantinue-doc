FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /workspace

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "uvicorn", "quantinue.main:app", "--host", "0.0.0.0", "--port", "8000"]
