# API + action engine image for Cloud Run (same image, two services).
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080 \
    YUKDHIS_RUNTIME_DIR=/tmp/yukdhis
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY --chown=app:app agents ./agents
COPY --chown=app:app api ./api
COPY --chown=app:app bank_mcp_server ./bank_mcp_server
COPY --chown=app:app core ./core
COPY --chown=app:app ingestion ./ingestion
COPY --chown=app:app knowledge_graph ./knowledge_graph
COPY --chown=app:app pipeline ./pipeline
COPY --chown=app:app privacy ./privacy
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app infrastructure/cloudsql ./infrastructure/cloudsql
COPY --chown=app:app data ./data
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
