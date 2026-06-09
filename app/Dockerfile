# NextAccount v2 — Dockerfile
# マルチステージビルド / 本番用

# ===== Stage 1: 依存ライブラリのビルド =====
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ===== Stage 2: 実行イメージ =====
FROM python:3.11-slim

WORKDIR /app

# タイムゾーン + 必要ランタイムのみ
ENV TZ=Asia/Tokyo \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# ビルドステージからライブラリをコピー
COPY --from=builder /install /usr/local

# アプリケーションコードをコピー
# .dockerignore で不要ファイルを除外すること
COPY . .

# 非rootユーザーで実行（セキュリティ）
RUN useradd -m -u 1000 appuser \
    && mkdir -p /secrets \
    && chown -R appuser:appuser /app /secrets
USER appuser

# ヘルスチェック（30秒ごと）
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:${PORT}/health || exit 1

EXPOSE ${PORT}

CMD ["python", "main.py"]
