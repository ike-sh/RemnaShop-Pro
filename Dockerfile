FROM python:3.11-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 注意：.dockerignore 仅影响 Docker build 上下文，不影响服务器上的 git clone 内容。
# 这里显式只复制运行时所需文件，确保镜像内不包含 tests/docs/README 等非运行时文件。
COPY bot.py ./
COPY handlers ./handlers
COPY jobs ./jobs
COPY services ./services
COPY storage ./storage
COPY utils ./utils
COPY docker ./docker
RUN chmod +x docker/entrypoint.sh

ENTRYPOINT ["./docker/entrypoint.sh"]
