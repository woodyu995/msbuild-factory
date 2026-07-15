# Multi-stage: UI build + Portal API image for Kubernetes
FROM node:22-alpine AS ui
WORKDIR /ui
COPY portal/frontend/package.json portal/frontend/package-lock.json* ./
RUN npm install
COPY portal/frontend/ ./
RUN npm run build

FROM python:3.12-slim AS api
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORTAL_ALLOW_INSECURE_DEFAULTS=false

COPY portal/backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY portal/backend/ ./
COPY --from=ui /ui/dist ./static

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
