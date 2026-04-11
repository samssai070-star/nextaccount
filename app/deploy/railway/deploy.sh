#!/bin/bash
# ============================================================
# NextAccount v2 — Railway CLI デプロイスクリプト
#
# 前提:
#   npm install -g @railway/cli
#   railway login
#
# 実行:
#   chmod +x deploy/railway/deploy.sh
#   ./deploy/railway/deploy.sh
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../.."

# ============================================================
# Railway CLI チェック
# ============================================================
if ! command -v railway &>/dev/null; then
    warn "Railway CLI が見つかりません。インストールします..."
    npm install -g @railway/cli
fi

# ============================================================
# プロジェクト作成 or 既存プロジェクトにリンク
# ============================================================
if [[ ! -f ".railway/config.json" ]]; then
    info "Railway プロジェクトを作成します..."
    railway init
else
    info "既存の Railway プロジェクトを使用します"
fi

# ============================================================
# 環境変数を設定（対話式）
# ============================================================
info "環境変数を Railway に設定します"
info "各値を入力してください（入力内容は表示されません）"

read -rsp "SLACK_BOT_TOKEN (xoxb-...): "     BOT_TOKEN;   echo
read -rsp "SLACK_APP_TOKEN (xapp-...): "     APP_TOKEN;   echo
read -rsp "DATABASE_URL (postgresql://...): " DB_URL;      echo
read -rsp "GOOGLE_SHEET_ID: "               SHEET_ID;    echo
read -rp  "google_key.json のパス: "        KEY_PATH

if [[ ! -f "$KEY_PATH" ]]; then
    echo "ファイルが見つかりません: $KEY_PATH"
    exit 1
fi
GOOGLE_KEY_JSON=$(cat "$KEY_PATH")

railway variables set \
    SLACK_BOT_TOKEN="$BOT_TOKEN" \
    SLACK_APP_TOKEN="$APP_TOKEN" \
    DATABASE_URL="$DB_URL" \
    GOOGLE_SHEET_ID="$SHEET_ID" \
    GOOGLE_KEY_JSON="$GOOGLE_KEY_JSON" \
    GOOGLE_APPLICATION_CREDENTIALS="/secrets/google_key.json" \
    TZ="Asia/Tokyo" \
    LOG_LEVEL="INFO" \
    ENVIRONMENT="production"

info "✅ 環境変数設定完了"

# ============================================================
# デプロイ
# ============================================================
info "Railway にデプロイ中..."
railway up --detach

info "✅ デプロイ完了！"
info "   ダッシュボード: https://railway.app/dashboard"
railway status
