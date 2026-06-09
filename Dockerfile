# ECHO-SRE — agentic SRE copilot streaming service.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ECHO_SRE_PROVIDERS_FILE=config/providers.yaml \
    ECHO_SRE_BACKEND=synthetic \
    ECHO_SRE_METRICS_PORT=9090

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# Runtime assets (provider registry + demo scenarios + runbooks live in src).
COPY config ./config
COPY scenarios ./scenarios

# The container ships the example registry as providers.yaml; real keys are injected as
# environment variables (GEMINI_API_KEY, ...) by the platform at deploy time.
RUN cp -n config/providers.example.yaml config/providers.yaml || true

EXPOSE 8000 9090

# Azure Container Apps sets $PORT; default to 8000 locally.
CMD ["sh", "-c", "echo-sre serve-api --host 0.0.0.0 --port ${PORT:-8000}"]
