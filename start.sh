#!/bin/sh
# Titik awal container Cloud Run.
set -e
APP="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --proxy-headers --forwarded-allow-ips=*"
cd /srv/backend

if [ -n "$LITESTREAM_REPLICA_URL" ]; then
  # Mode SQLite gratis: pulihkan database dari Cloud Storage, lalu jalankan aplikasi
  # sambil mereplikasi setiap perubahan kembali ke Cloud Storage.
  export SQLITE_PATH="${SQLITE_PATH:-/srv/data/ocr.db}"
  export DATABASE_URL="sqlite:///${SQLITE_PATH}"
  mkdir -p "$(dirname "$SQLITE_PATH")"
  litestream restore -config /srv/litestream.yml -if-db-not-exists -if-replica-exists "$SQLITE_PATH"
  exec litestream replicate -config /srv/litestream.yml -exec "$APP"
fi

exec $APP
