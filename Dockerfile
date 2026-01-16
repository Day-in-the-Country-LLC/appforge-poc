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
ENV SERVICE_HOST=0.0.0.0
ENV SERVICE_PORT=8080

EXPOSE 8080

CMD ["uvicorn", "ace.runners.service:app", "--host", "0.0.0.0", "--port", "8080"]
