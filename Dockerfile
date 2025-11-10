# syntax=docker/dockerfile:1

FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python deps
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# App code
COPY backend /app/backend

# Create log directory
RUN mkdir -p /var/log/app

ENV FLASK_APP=backend.app:create_app()
ENV GUNICORN_WORKERS=3
ENV GUNICORN_BIND=0.0.0.0:8000

EXPOSE 8000

# Healthcheck endpoint
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8000/health || exit 1

# Run gunicorn
COPY backend/docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "-w", "${GUNICORN_WORKERS}", "-b", "${GUNICORN_BIND}", "--access-logfile", "-", "--error-logfile", "-", "backend.wsgi:app"]
