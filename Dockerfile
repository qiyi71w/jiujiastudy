FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2" \
    && python -m pip install --no-cache-dir -r /app/requirements.txt
COPY tools/ /app/tools/

RUN groupadd --gid 10001 account && useradd --uid 10001 --gid account --no-create-home --shell /usr/sbin/nologin account
USER 10001:10001

ENTRYPOINT ["python", "-B", "/app/tools/cc_server.py", "--home", "/data", "--secrets", "/run/secrets/service.json"]
