#!/bin/bash
# ============================================================
# NextAccount v2 — Google Cloud Run デプロイスクリプト
#
# 実行前に以下を確認:
#   1. gcloud CLI インストール済み
#   2. gcloud auth login 済み
#   3. 下の「設定」セクションを自分の値に変更
#
# 初回実行:
#   chmod +x deploy/cloudrun/deploy.sh
#   ./deploy/cloudrun/deploy.sh --setup   ← Secret Manager登録まで全自動
#
# 2回目以降（コード更新のみ）:
#   ./deploy/cloudrun/deploy.sh
# ============================================================

set -euo pipefail

# ============================================================
# ★ここを自分の値に変更する★
# ============================================================
PROJECT_ID="your-gcp-project-id"
REGION="asia-northeast1"
SERVICE_NAME="nextaccount-v2"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# ============================================================
# カラー出力
# ============================================================
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ============================================================
# Secret Manager セットアップ（--setup フラグ時のみ実行）
# ============================================================

setup_secrets() {
    info "Secret Manager にシークレットを登録します"
    info "各値を入力してください（入力内容は表示されません）"

    read -rsp "SLACK_BOT_TOKEN (xoxb-...): "    SLACK_BOT_TOKEN;    echo
    read -rsp "SLACK_APP_TOKEN (xapp-...): "    SLACK_APP_TOKEN;    echo
    read -rsp "DATABASE_URL (postgresql://...): " DATABASE_URL;      echo
    read -rsp "GOOGLE_SHEET_ID: "               GOOGLE_SHEET_ID;    echo
    read -rsp "google_key.json のパス: "        KEY_PATH;           echo

    # Secret Manager に登録（存在する場合は新バージョンを追加）
    _upsert_secret "SLACK_BOT_TOKEN"   "$SLACK_BOT_TOKEN"
    _upsert_secret "SLACK_APP_TOKEN"   "$SLACK_APP_TOKEN"
    _upsert_secret "DATABASE_URL"      "$DATABASE_URL"
    _upsert_secret "GOOGLE_SHEET_ID"   "$GOOGLE_SHEET_ID"
    _upsert_secret_file "GOOGLE_KEY_JSON" "$KEY_PATH"

    info "✅ シークレット登録完了"
}

_upsert_secret() {
    local name="$1"; local value="$2"
    if gcloud secrets describe "$name" --project="$PROJECT_ID" &>/dev/null; then
        echo -n "$value" | gcloud secrets versions add "$name" \
            --data-file=- --project="$PROJECT_ID"
        info "  更新: $name"
    else
        echo -n "$value" | gcloud secrets create "$name" \
            --data-file=- --replication-policy=automatic --project="$PROJECT_ID"
        info "  作成: $name"
    fi
}

_upsert_secret_file() {
    local name="$1"; local path="$2"
    [[ -f "$path" ]] || error "ファイルが見つかりません: $path"
    if gcloud secrets describe "$name" --project="$PROJECT_ID" &>/dev/null; then
        gcloud secrets versions add "$name" \
            --data-file="$path" --project="$PROJECT_ID"
    else
        gcloud secrets create "$name" \
            --data-file="$path" --replication-policy=automatic --project="$PROJECT_ID"
    fi
    info "  登録: $name (from $path)"
}

# ============================================================
# API 有効化
# ============================================================

enable_apis() {
    info "必要な API を有効化中..."
    gcloud services enable \
        run.googleapis.com \
        cloudbuild.googleapis.com \
        secretmanager.googleapis.com \
        vision.googleapis.com \
        sheets.googleapis.com \
        --project="$PROJECT_ID"
    info "✅ API 有効化完了"
}

# ============================================================
# ビルド & デプロイ
# ============================================================

build_and_deploy() {
    info "Docker イメージをビルド中..."
    gcloud builds submit \
        --tag "$IMAGE" \
        --project "$PROJECT_ID" \
        .
    info "✅ ビルド完了: $IMAGE"

    info "Cloud Run にデプロイ中..."
    gcloud run deploy "$SERVICE_NAME" \
        --image "$IMAGE" \
        --platform managed \
        --region "$REGION" \
        --project "$PROJECT_ID" \
        --allow-unauthenticated \
        --port 8080 \
        --memory 512Mi \
        --cpu 1 \
        --min-instances 1 \
        --max-instances 5 \
        --concurrency 80 \
        --timeout 300 \
        --set-env-vars "TZ=Asia/Tokyo,LOG_LEVEL=INFO,ENVIRONMENT=production" \
        --set-secrets "SLACK_BOT_TOKEN=SLACK_BOT_TOKEN:latest" \
        --set-secrets "SLACK_APP_TOKEN=SLACK_APP_TOKEN:latest" \
        --set-secrets "DATABASE_URL=DATABASE_URL:latest" \
        --set-secrets "GOOGLE_SHEET_ID=GOOGLE_SHEET_ID:latest" \
        --set-secrets "/secrets/google_key.json=GOOGLE_KEY_JSON:latest" \
        --set-env-vars "GOOGLE_APPLICATION_CREDENTIALS=/secrets/google_key.json"

    SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
        --region "$REGION" \
        --project "$PROJECT_ID" \
        --format="value(status.url)")

    info "✅ デプロイ完了！"
    info "   URL: ${SERVICE_URL}"
    info "   ヘルスチェック: ${SERVICE_URL}/health"
}

# ============================================================
# メイン処理
# ============================================================

# カレントディレクトリをプロジェクトルートに移動
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../.."

if [[ "${1:-}" == "--setup" ]]; then
    enable_apis
    setup_secrets
fi

build_and_deploy
