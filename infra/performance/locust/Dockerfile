FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir locust==2.32.6

WORKDIR /perf

ENTRYPOINT ["locust"]