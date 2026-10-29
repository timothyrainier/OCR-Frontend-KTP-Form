FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
RUN useradd --create-home app && mkdir -p /srv/data && chown -R app /srv/data
USER app

WORKDIR /srv/backend
# 1 worker + threadpool FastAPI sudah cukup; Cloud Run menambah instance saat ramai.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips="*"
