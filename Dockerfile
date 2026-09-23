FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY tools/ /app/tools/

RUN groupadd --gid 10001 account && useradd --uid 10001 --gid account --no-create-home --shell /usr/sbin/nologin account
USER 10001:10001

ENTRYPOINT ["python", "-B", "/app/tools/cc_telegram.py", "--home", "/data", "--secrets", "/run/secrets/service.json"]
