FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DAILY_NEWS_CONFIG=/app/config \
    DAILY_NEWS_DATABASE=/data/daily-news.sqlite3

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY config ./config
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 daily-news \
    && mkdir /data \
    && chown daily-news:daily-news /data

USER daily-news
VOLUME ["/data"]
ENTRYPOINT ["daily-news"]
CMD ["topics"]

