@echo off
REM Jalankan aplikasi di http://localhost:8000
cd /d %~dp0
if not exist .env copy .env.example .env
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q -r requirements.txt
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
