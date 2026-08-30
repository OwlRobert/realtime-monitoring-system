#!/usr/bin/env sh
set -e

echo "Running database migrations..."
alembic upgrade head
echo "Migrations complete."

echo "Seeding demo accounts..."
python -m app.services.bootstrap
echo "Demo account seeding complete."

exec uvicorn app.main:app \
    --host "${UVICORN_HOST:-0.0.0.0}" \
    --port "${UVICORN_PORT:-8000}" \
    --workers 1 \
    --log-level "$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')"
