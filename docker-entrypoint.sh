#!/usr/bin/env sh
set -e

# Optional database migration
if [ "$AUTO_MIGRATE" = "1" ]; then
  echo "Running database migrations..."
  flask db upgrade || echo "Migration failed or not configured; continuing"
fi

exec "$@"
