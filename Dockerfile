FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml pyproject.toml
RUN pip install --no-cache-dir -e .

COPY src/ src/
COPY docs/ docs/

ENV ENVIRONMENT=production
ENV DEBUG=false

CMD ["python", "scripts/run_agent_pool.py", "--target", "remote"]
