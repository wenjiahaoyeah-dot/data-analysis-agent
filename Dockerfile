FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先拷依赖清单，利用 Docker 层缓存
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY app ./app
COPY frontend ./frontend
COPY scripts ./scripts
COPY data ./data
COPY pytest.ini ./

RUN mkdir -p storage/datasets storage/tasks storage/eval

EXPOSE 8000

# 默认启动后端 API；前端由 docker-compose 用另一个 service 覆盖 command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
