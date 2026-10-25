# =============================================================================
# 产品级领域知识库系统 —— 后端镜像
# 构建：docker build -t rag-backend .
# 运行：见 docker-compose.yml
# =============================================================================

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 编译 pdfplumber / torch 等所需的最小系统依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖，利用层缓存
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 再拷贝项目代码（含 alembic.ini / alembic / scripts）
COPY . .

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
