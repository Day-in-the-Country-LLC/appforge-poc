FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:${PATH}"
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ src/
COPY docs/ docs/
COPY scripts/ scripts/

ENV ENVIRONMENT=production
ENV DEBUG=false

ENV PATH="/opt/venv/bin:${PATH}"
CMD ["python", "scripts/run_agent_pool.py", "--target", "remote"]
