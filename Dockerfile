# ==============================================================================
# Multi-Stage Dockerfile for Crypto Signal Bot Quantitative Trading Platform
# Stage 1: Build React/TypeScript Modern UI bundle
# Stage 2: Minimal, hardened Python runtime with non-root security context
# ==============================================================================

# Stage 1: Frontend Build
FROM node:22-alpine AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Stage 2: Hardened Python Application Runtime
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SIGNAL_BOT_USE_MODERN_UI=1 \
    SIGNAL_BOT_ENV=production \
    SIGNAL_BOT_HOST=0.0.0.0 \
    WEB_PORT=8050

# Install runtime OS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    default-mysql-client \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application source
COPY . .

# Copy modern frontend production distribution from Stage 1
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Security: Create non-root signalbot system user and group
RUN groupadd -g 1001 signalbot && \
    useradd -u 1001 -g signalbot -m -s /bin/bash signalbot && \
    chown -R signalbot:signalbot /app

USER signalbot

EXPOSE 8050

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8050/healthz || exit 1

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8050"]
