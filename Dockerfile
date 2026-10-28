FROM python:3.12-slim

# ---- 基础环境 ----
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PROJECT_ROOT=/app

WORKDIR /app

# curl 用于 healthcheck；build-essential 仅部分包需要编译，装完即删
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl build-essential \
 && rm -rf /var/lib/apt/lists/*

# ---- 依赖层（先拷贝依赖清单，利用构建缓存）----
COPY requirements.txt requirements-local.txt ./
# 先装 CPU 版 torch，避免默认拉入 CUDA 运行时（镜像体积 -2GB 左右）
RUN pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt -r requirements-local.txt

# ---- 代码层 ----
COPY . .

# 模型权重、向量库、日志都不进镜像，运行时通过 volume 挂载
RUN mkdir -p /app/logs /app/data/chroma_db /app/models /app/Agent/mcp_tools/workspace \
 && useradd --create-home --shell /bin/bash appuser \
 && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["python", "-m", "Port.server"]
