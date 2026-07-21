# Multi-stage: UI build + Portal API image for Kubernetes
# Linux-only base images — build with Docker in Linux container mode
# (not Windows containers). Example: docker build --platform linux/amd64 ...
FROM --platform=linux/amd64 node:22-alpine AS ui
WORKDIR /ui
COPY portal/frontend/package.json portal/frontend/package-lock.json ./
RUN npm ci
COPY portal/frontend/ ./
RUN npm run build

FROM --platform=linux/amd64 python:3.12-slim AS api
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORTAL_ALLOW_INSECURE_DEFAULTS=false

# Docker CLI for PORTAL_LOCAL_FACTORY (socket mounted at runtime). No daemon.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz \
       | tar -xz -C /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && chmod +x /usr/local/bin/docker \
    && rm -rf /tmp/docker /var/lib/apt/lists/*

COPY portal/backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY portal/backend/ ./
COPY --from=ui /ui/dist ./static

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
