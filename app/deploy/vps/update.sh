#!/bin/bash
# ============================================================
# NextAccount v2 — VPS コード更新スクリプト
#
# 使い方（ローカルPCから実行）:
#   ./deploy/vps/update.sh user@your-server-ip
# ============================================================

set -euo pipefail

SERVER="${1:?使い方: $0 user@server-ip}"
APP_DIR="/opt/nextaccount"

GREEN='\033[0;32m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }

# ローカルでアーカイブ作成
info "コードをパッケージ化中..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../.."
tar -czf /tmp/nextaccount_update.tar.gz \
    --exclude=".git" --exclude="__pycache__" --exclude="*.pyc" \
    --exclude=".env" --exclude="*.json" --exclude="venv/" \
    --exclude="deploy/" \
    .

# サーバーに転送
info "サーバーに転送中: $SERVER"
scp /tmp/nextaccount_update.tar.gz "${SERVER}:/tmp/"

# サーバー上で展開・再起動
info "サーバー上でデプロイ中..."
ssh "$SERVER" "
    set -e
    cd $APP_DIR/app
    tar -xzf /tmp/nextaccount_update.tar.gz --overwrite
    docker compose build --no-cache
    systemctl restart nextaccount
    sleep 5
    curl -sf http://localhost:8080/health && echo ' ✅ 起動確認OK' || echo ' ❌ ヘルスチェック失敗'
    rm /tmp/nextaccount_update.tar.gz
"

rm /tmp/nextaccount_update.tar.gz
info "✅ 更新完了！"
