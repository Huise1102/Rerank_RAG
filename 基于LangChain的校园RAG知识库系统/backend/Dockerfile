# 校园 RAG 后端镜像
# 单 worker 运行：Chroma 使用本地文件索引，同一时间只能被一个进程打开
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.hf-cache \
    SENTENCE_TRANSFORMERS_HOME=/app/.hf-cache \
    HF_ENDPOINT=https://hf-mirror.com

WORKDIR /app

# libgomp1 供 onnxruntime、chromadb 使用；curl 用于健康检查
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y

COPY . .

RUN useradd -m -u 1000 campus \
    && mkdir -p /app/storage /app/indexes /app/logs /app/data /app/.hf-cache \
    && chown -R campus:campus /app
USER campus

EXPOSE 8004

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8004/health || exit 1

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8004"]
