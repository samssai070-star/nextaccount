# NextAccount v2 — クラウドデプロイガイド

## 📋 事前に用意するもの（全プラットフォーム共通）

| 項目 | 取得場所 | 変数名 |
|------|----------|--------|
| Slack Bot Token | api.slack.com → アプリ → OAuth & Permissions | `SLACK_BOT_TOKEN` |
| Slack App Token | api.slack.com → アプリ → Socket Mode | `SLACK_APP_TOKEN` |
| Google サービスアカウントキー | Google Cloud Console → IAM → サービスアカウント | `google_key.json` |
| Google Sheet ID | スプレッドシートURL中の長い文字列 | `GOOGLE_SHEET_ID` |
| Supabase 接続URL | Supabase → Settings → Database → URI | `DATABASE_URL` |

---

## 🚀 Option A: Google Cloud Run（推奨）

**月額費用**: 小規模なら無料枠内（リクエスト数による）  
**特徴**: スケールアウト自動、Secret Manager でキー管理が安全

```bash
# 1. gcloud CLI をインストール
#    https://cloud.google.com/sdk/docs/install

# 2. ログイン
gcloud auth login

# 3. deploy/cloudrun/deploy.sh を編集して PROJECT_ID を設定
vi deploy/cloudrun/deploy.sh   # PROJECT_ID を変更

# 4. 初回デプロイ（シークレット登録 + ビルド + デプロイを全自動）
chmod +x deploy/cloudrun/deploy.sh
./deploy/cloudrun/deploy.sh --setup

# 2回目以降（コード更新のみ）
./deploy/cloudrun/deploy.sh
```

---

## 🚂 Option B: Railway（最も簡単）

**月額費用**: $5〜（従量課金）  
**特徴**: GitHub を push するだけで自動デプロイ

```bash
# 1. Railway CLI インストール
npm install -g @railway/cli

# 2. ログイン
railway login

# 3. デプロイ（環境変数の入力を求められる）
chmod +x deploy/railway/deploy.sh
./deploy/railway/deploy.sh
```

**または GUI で設定する場合:**
1. [railway.app](https://railway.app) → New Project → Deploy from GitHub
2. このリポジトリを選択
3. Variables タブで以下を設定:

```
SLACK_BOT_TOKEN     = xoxb-...
SLACK_APP_TOKEN     = xapp-...
DATABASE_URL        = postgresql://...
GOOGLE_SHEET_ID     = 1aBcD...
GOOGLE_KEY_JSON     = { "type": "service_account", ... }  ← JSONの中身を丸ごと貼る
GOOGLE_APPLICATION_CREDENTIALS = /secrets/google_key.json
TZ                  = Asia/Tokyo
LOG_LEVEL           = INFO
ENVIRONMENT         = production
```

> Railway では `google_key.json` の内容を `GOOGLE_KEY_JSON` 変数に貼り付けると  
> コンテナ起動時に `/secrets/google_key.json` として書き出す処理が必要です。  
> → `main.py` の起動前処理で自動対応済み（環境変数 `GOOGLE_KEY_JSON` があれば自動書き出し）

---

## 🖥️ Option C: VPS / 自分のサーバー（Ubuntu 22.04）

**月額費用**: $5〜（Vultr / DigitalOcean / Conoha など）  
**特徴**: 完全なコントロール、Docker + systemd で永続稼働

```bash
# 1. VPS に SSH で接続
ssh root@your-server-ip

# 2. このリポジトリをサーバーに転送（ローカルPCから実行）
scp -r nextaccount/ root@your-server-ip:/tmp/nextaccount_src

# 3. サーバー上でセットアップスクリプトを実行
ssh root@your-server-ip
cd /tmp/nextaccount_src
chmod +x deploy/vps/setup.sh
sudo ./deploy/vps/setup.sh
# → 対話的に各値を入力するだけで完了

# コード更新時（ローカルPCから）
./deploy/vps/update.sh root@your-server-ip
```

### VPS の日常運用コマンド

```bash
# ログをリアルタイム確認
journalctl -u nextaccount -f

# 状態確認
systemctl status nextaccount

# 再起動
systemctl restart nextaccount

# コンテナのログ確認
docker logs nextaccount -f --tail=100

# ヘルスチェック
curl http://localhost:8080/health
```

---

## 🔑 google_key.json の安全な扱い方

### Railway / Render の場合
JSON の中身を環境変数 `GOOGLE_KEY_JSON` に丸ごと貼る。  
`main.py` が起動時に自動で `/secrets/google_key.json` に書き出す。

### Cloud Run の場合
Secret Manager に登録してコンテナにマウント（`deploy.sh --setup` で自動設定）。

### VPS の場合
`/opt/nextaccount/secrets/google_key.json` に配置（`setup.sh` が自動コピー）。  
パーミッション: `chmod 600`

---

## ✅ デプロイ後の確認

```bash
# ヘルスチェック（URLは各プラットフォームのものに変更）
curl https://your-app-url/health

# 期待するレスポンス:
# {"status": "ok", "service": "nextaccount-v2", "uptime_seconds": 42, "env": "production"}
```

Slack で Bot を @メンションして「こんにちは！NextAccount v2 Bot です。」と返ってきたら完成。

---

## 🛠️ トラブルシューティング

| 症状 | 原因と対処 |
|------|-----------|
| `/health` が 503 を返す | Bot がまだ起動中。20秒待って再試行 |
| Slack Bot が反応しない | `SLACK_APP_TOKEN` (xapp-) が正しいか確認。Socket Mode が有効か確認 |
| OCR が動かない | `GOOGLE_APPLICATION_CREDENTIALS` のパスと Vision API の有効化を確認 |
| Sheets に書き込めない | サービスアカウントを「編集者」として共有しているか確認 |
| DB 接続エラー | `DATABASE_URL` の値。Supabase のパスワードに記号がある場合は URL エンコード |
| Railway で google_key.json が見つからない | `GOOGLE_KEY_JSON` 変数が設定されているか確認 |
