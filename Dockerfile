FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    tmux \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code @openai/codex

# Git identity required for CLI agents to create commits.
RUN git config --global user.name "ACE Agent" \
    && git config --global user.email "ace-agent@noreply.github.com"

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY src/ src/
# The real repo-gcp-mapping.json is gitignored (contains GCP project IDs).
# Copy the example as a fallback; override at runtime via volume mount, GCS
# fetch, or by supplying REPO_GCP_MAPPING_PATH pointing to a mounted secret.
COPY docs/repo-gcp-mapping.example.json docs/repo-gcp-mapping.json
COPY prompts/ prompts/
COPY skills/ /root/.codex/skills/
COPY skills/ /root/.claude/skills/

ENV HOME=/root
ENV ENVIRONMENT=production
ENV DEBUG=false
ENV PORT=8080
ENV PYTHONPATH=/app/src

ENV PATH="/opt/venv/bin:${PATH}"
CMD ["uvicorn", "ace.webhooks.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
