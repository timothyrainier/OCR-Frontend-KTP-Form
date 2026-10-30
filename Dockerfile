FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Litestream: replikasi SQLite ke Cloud Storage (untuk mode database gratis)
ARG LITESTREAM_VERSION=0.3.13
ADD https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-amd64.tar.gz /tmp/litestream.tgz
RUN echo "eb75a3de5cab03875cdae9f5f539e6aedadd66607003d9b1e7a9077948818ba0  /tmp/litestream.tgz" | sha256sum -c - \
    && tar -xzf /tmp/litestream.tgz -C /usr/local/bin && rm /tmp/litestream.tgz

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY litestream.yml start.sh ./
RUN chmod +x start.sh && useradd --create-home app && mkdir -p /srv/data && chown -R app /srv/data
USER app

CMD ["/srv/start.sh"]
