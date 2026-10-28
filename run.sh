#!/usr/bin/env bash
# Jalankan aplikasi di http://localhost:8000
set -e
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
cd backend
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
