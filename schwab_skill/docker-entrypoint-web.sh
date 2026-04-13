#!/bin/sh
set -e
cd /app

# Bootstrap mode stamps schema inside the app import; skip CLI migrate to avoid fighting that path.
case "${SAAS_BOOTSTRAP_SCHEMA:-}" in
  1|true|TRUE|yes|Yes) ;;
  *)
    case "${SAAS_RUN_ALEMBIC:-}" in
      1|true|TRUE|yes|Yes)
        if [ -f alembic.ini ]; then
          alembic upgrade head
        fi
        ;;
    esac
    ;;
esac

exec uvicorn webapp.main_saas:app --host 0.0.0.0 --port "${PORT:-8000}"
