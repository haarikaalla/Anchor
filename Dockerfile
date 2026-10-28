FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANCHOR_DB=/data/anchor.db

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY anchor ./anchor
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 anchor \
    && mkdir -p /data \
    && chown -R anchor:anchor /data

USER anchor
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["anchor", "--db", "/data/anchor.db", "serve", "--host", "0.0.0.0", "--port", "8080"]
