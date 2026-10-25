# 多阶段构建 Dockerfile（T13-1）
# 阶段1：构建依赖
FROM python:3.12-slim AS builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装依赖到临时目录
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# 阶段2：运行时
FROM python:3.12-slim AS runtime

WORKDIR /app

# 安装运行时系统依赖（健康检查用 python urllib，P2-2）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 从构建阶段复制依赖
COPY --from=builder /install /usr/local

# 复制应用代码
COPY . .

# 创建数据目录
RUN mkdir -p data/db data/checkpoints data/reports data/workspace data/eval_cases

# 环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

# 暴露端口
EXPOSE 8000

# 健康检查（P2-2：使用 python urllib，不依赖 curl）
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
