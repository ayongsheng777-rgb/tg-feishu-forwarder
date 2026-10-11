FROM python:3.12-slim

# 非交互安装（避免 tzdata 弹出地区选择卡住构建）
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai

# Proxy support for build (apt/pip behind proxy)
ARG http_proxy=
ARG https_proxy=
ARG no_proxy=

# System dependencies for Pillow, Telethon, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure data and media_cache directories exist
RUN mkdir -p /data /app/media_cache

EXPOSE 8000

# Default: run the FastAPI backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
