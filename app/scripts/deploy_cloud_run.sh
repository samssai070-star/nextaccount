#!/bin/bash
# ============================================================
# NextAccount v2 — Cloud Run デプロイスクリプト
# 使用前に .env の値を確認し、PROJECT_ID / REGION を設定すること
# ============================================================

set -euo pipefail

# ============================================================
# 設定（ここを変更する）
# ============================================================
PROJECT_ID="your-gcp-project-id"
REGION="asia-northeast1"          # 東京
SERVICE_NAME="nextaccount-v2"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# ============================================================
# ビルド & プッシュ
# ============================================================
echo "🔨 Docker イメージをビルド中..."
gcloud builds submit --tag "${IMAGE}" .

echo "✅ ビルド完了: ${IMAGE}"

# ============================================================
# Secret Manager にシークレットを登録（初回のみ）
# ============================================================
# gcloud secrets create SLACK_BOT_TOKEN --replication-policy="automatic"
# echo -n "xoxb-..." | gcloud secrets versions add SLACK_BOT_TOKEN --data-file=-
#
# gcloud secrets create SLACK_APP_TOKEN --replication-policy="automatic"
# echo -n "xapp-..." | gcloud secrets versions add SLACK_APP_TOKEN --data-file=-
#
# gcloud secrets create DATABASE_URL --replication-policy="automatic"
# echo -n "postgresql://..." | gcloud secrets versions add DATABASE_URL --data-file=-
#
# gcloud secrets create GOOGLE_SHEET_ID --replication-policy="automatic"
# echo -n "1aBcD..." | gcloud secrets versions add GOOGLE_SHEET_ID --data-file=-

# ============================================================
# Cloud Run デプロイ
# ============================================================
echo "🚀 Cloud Run にデプロイ中..."

gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 1 \
  --max-instances 3 \
  --set-env-vars "TZ=Asia/Tokyo,LOG_LEVEL=INFO,ENVIRONMENT=production" \
  --set-secrets "SLACK_BOT_TOKEN=SLACK_BOT_TOKEN:latest" \
  --set-secrets "SLACK_APP_TOKEN=SLACK_APP_TOKEN:latest" \
  --set-secrets "DATABASE_URL=DATABASE_URL:latest" \
  --set-secrets "GOOGLE_SHEET_ID=GOOGLE_SHEET_ID:latest" \
  --set-secrets "GOOGLE_APPLICATION_CREDENTIALS=/secrets/google_key.json:GOOGLE_KEY_JSON:latest"

echo ""
echo "✅ デプロイ完了！"
gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" \
  --format="value(status.url)"
