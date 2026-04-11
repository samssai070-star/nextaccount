#!/bin/bash
# ============================================================
# NextAccount v2 — VPS (Ubuntu 22.04) セットアップスクリプト
#
# 【使い方】
#   新しいVPSサーバーにSSHで接続後、このスクリプトを実行するだけ。
#   Docker のインストール・アプリの配置・systemd登録まで全自動。
#
#   curl -fsSL https://raw.githubusercontent.com/yourorg/nextaccount/main/deploy/vps/setup.sh | bash
#   または
#   chmod +x deploy/vps/setup.sh && sudo ./deploy/vps/setup.sh
#
# 【実行環境】
#   Ubuntu 22.04 LTS (推奨)
#   最低スペック: 1 vCPU / 1GB RAM / 20GB SSD
# ============================================================

set -euo pipefail

APP_DIR="/opt/nextaccount"
APP_USER="nextaccount"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
section() { echo -e "\n${BLUE}━━━ $* ━━━${NC}"; }

# root チェック
if [[ $EUID -ne 0 ]]; then
    echo "このスクリプトは sudo で実行してください"
    exit 1
fi

# ============================================================
section "Step 1: システムアップデート"
# ============================================================
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq curl git unzip ufw fail2ban
info "✅ システムアップデート完了"

# ============================================================
section "Step 2: Docker インストール"
# ============================================================
if command -v docker &>/dev/null; then
    info "Docker は既にインストール済みです: $(docker --version)"
else
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    info "✅ Docker インストール完了: $(docker --version)"
fi

# Docker Compose v2 チェック
if ! docker compose version &>/dev/null; then
    apt-get install -y -qq docker-compose-plugin
fi
info "✅ Docker Compose: $(docker compose version)"

# ============================================================
section "Step 3: アプリユーザー作成"
# ============================================================
if ! id -u "$APP_USER" &>/dev/null; then
    useradd -r -m -d "$APP_DIR" -s /bin/bash "$APP_USER"
    usermod -aG docker "$APP_USER"
    info "✅ ユーザー作成: $APP_USER"
else
    info "ユーザー $APP_USER は既に存在します"
fi

# ============================================================
section "Step 4: アプリディレクトリ配置"
# ============================================================
mkdir -p "$APP_DIR"/{app,secrets,logs}
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# アプリコードをコピー（このスクリプトと同じディレクトリのルートから）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rsync -a --exclude=".git" --exclude="__pycache__" --exclude="*.pyc" \
    --exclude=".env" --exclude="*.json" --exclude="venv/" \
    "${SCRIPT_DIR}/../../" "$APP_DIR/app/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/app/"
info "✅ アプリコードを $APP_DIR/app/ にコピーしました"

# ============================================================
section "Step 5: 環境変数ファイルの作成"
# ============================================================
ENV_FILE="$APP_DIR/app/.env"
if [[ -f "$ENV_FILE" ]]; then
    warn ".env が既に存在します。上書きしません。"
    warn "手動で $ENV_FILE を編集してください。"
else
    info "環境変数を設定します（入力内容は表示されません）"

    read -rsp "SLACK_BOT_TOKEN (xoxb-...): "     BOT_TOKEN;   echo
    read -rsp "SLACK_APP_TOKEN (xapp-...): "     APP_TOKEN;   echo
    read -rsp "DATABASE_URL (postgresql://...): " DB_URL;      echo
    read -rsp "GOOGLE_SHEET_ID: "               SHEET_ID;    echo
    read -rp  "google_key.json のパス (ローカル): " KEY_PATH

    if [[ -f "$KEY_PATH" ]]; then
        cp "$KEY_PATH" "$APP_DIR/secrets/google_key.json"
        chmod 600 "$APP_DIR/secrets/google_key.json"
        chown "$APP_USER:$APP_USER" "$APP_DIR/secrets/google_key.json"
        info "✅ google_key.json をコピーしました"
    else
        warn "google_key.json が見つかりません。後で $APP_DIR/secrets/ に手動で配置してください。"
    fi

    cat > "$ENV_FILE" <<EOF
SLACK_BOT_TOKEN=${BOT_TOKEN}
SLACK_APP_TOKEN=${APP_TOKEN}
DATABASE_URL=${DB_URL}
GOOGLE_SHEET_ID=${SHEET_ID}
GOOGLE_APPLICATION_CREDENTIALS=/secrets/google_key.json
TZ=Asia/Tokyo
LOG_LEVEL=INFO
ENVIRONMENT=production
ACCOUNTING_SOFTWARE=none
EOF
    chmod 600 "$ENV_FILE"
    chown "$APP_USER:$APP_USER" "$ENV_FILE"
    info "✅ .env ファイルを作成しました"
fi

# ============================================================
section "Step 6: Docker Compose ファイルの生成"
# ============================================================
cat > "$APP_DIR/docker-compose.yml" <<'EOF'
version: "3.9"

services:
  nextaccount:
    build:
      context: ./app
      dockerfile: Dockerfile
    image: nextaccount-v2:latest
    container_name: nextaccount
    restart: always
    env_file:
      - ./app/.env
    volumes:
      - ./secrets:/secrets:ro
      - ./logs:/tmp/logs
    ports:
      - "127.0.0.1:8080:8080"
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"
EOF
chown "$APP_USER:$APP_USER" "$APP_DIR/docker-compose.yml"
info "✅ docker-compose.yml を作成しました"

# ============================================================
section "Step 7: systemd サービス登録"
# ============================================================
cat > /etc/systemd/system/nextaccount.service <<EOF
[Unit]
Description=NextAccount v2 Bot
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStartPre=/usr/bin/docker compose pull --quiet
ExecStart=/usr/bin/docker compose up --build
ExecStop=/usr/bin/docker compose down
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=nextaccount

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nextaccount
info "✅ systemd サービス登録完了"

# ============================================================
section "Step 8: ファイアウォール設定"
# ============================================================
ufw --force enable
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 443/tcp   # 将来の HTTPS 用
# 8080 は外部に開けない（nginx 経由で必要な場合は別途設定）
ufw status
info "✅ UFW ファイアウォール設定完了"

# ============================================================
section "Step 9: アプリ起動"
# ============================================================
info "Docker イメージをビルドして起動します..."
cd "$APP_DIR"
sudo -u "$APP_USER" docker compose build --no-cache
systemctl start nextaccount

sleep 5

# ヘルスチェック
if curl -sf http://localhost:8080/health &>/dev/null; then
    info "✅ アプリが正常に起動しました！"
    curl -s http://localhost:8080/health | python3 -m json.tool
else
    warn "⚠️  ヘルスチェック失敗。ログを確認してください:"
    journalctl -u nextaccount -n 30 --no-pager
fi

# ============================================================
section "セットアップ完了！"
# ============================================================
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  NextAccount v2 のセットアップが完了しました！   ${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  アプリ場所 : $APP_DIR"
echo "  ログ確認   : journalctl -u nextaccount -f"
echo "  状態確認   : systemctl status nextaccount"
echo "  再起動     : systemctl restart nextaccount"
echo "  停止       : systemctl stop nextaccount"
echo "  コンテナ   : docker ps"
echo "  環境変数   : vi $APP_DIR/app/.env  → systemctl restart nextaccount"
echo ""
