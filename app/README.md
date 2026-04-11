# NextAccount v2 — セットアップ & 運用ガイド

## プロジェクト構成

```
nextaccount/
├── main.py                    # エントリポイント（Flask + Slack Bot）
├── Dockerfile                 # クラウドデプロイ用
├── railway.toml               # Railway デプロイ設定
├── requirements.txt
├── .gitignore
│
├── core/                      # ビジネスロジック
│   ├── config.py              # 設定・商家マスター（150+）
│   ├── ocr.py                 # Google Cloud Vision OCR
│   ├── accounting.py          # 科目分類・仕訳生成
│   ├── database.py            # PostgreSQL (Supabase) CRUD
│   └── sheets.py              # Google Sheets 同期
│
├── bot/
│   └── slack_handler.py       # Slack イベントハンドラ
│
├── adapters/                  # Phase 2: 会計ソフト連携
│   ├── freee.py               # freee 会計 API
│   ├── mfcloud.py             # MFクラウド会計 API
│   └── __init__.py            # ディスパッチャー
│
├── scripts/
│   ├── schema.sql             # Supabase テーブル作成SQL
│   ├── monthly_batch.py       # 月次バッチ（シート再構築）
│   ├── freee_oauth.py         # freee 初回認証
│   └── deploy_cloud_run.sh    # Cloud Run デプロイ
│
├── config/
│   └── .env.example           # 環境変数テンプレート
│
└── tests/
    └── test_core.py           # 単体テスト（14項目）
```

---

## Phase 1 セットアップ手順

### Step 1: Supabase — データベース準備

1. [Supabase](https://supabase.com) にアクセスしてプロジェクトを作成
2. 左メニュー「SQL Editor」を開く
3. `scripts/schema.sql` の内容を貼り付けて実行
4. 左メニュー「Settings > Database > Connection String > URI」をコピー

### Step 2: Google Cloud — Vision API + サービスアカウント

```bash
# 1. プロジェクト作成（または既存のものを使用）
gcloud projects create nextaccount-v2

# 2. Vision API を有効化
gcloud services enable vision.googleapis.com --project=nextaccount-v2

# 3. Sheets API を有効化
gcloud services enable sheets.googleapis.com --project=nextaccount-v2

# 4. サービスアカウント作成
gcloud iam service-accounts create nextaccount-bot \
  --display-name="NextAccount Bot"

# 5. キー発行（このファイルは .gitignore に追加すること）
gcloud iam service-accounts keys create /secrets/google_key.json \
  --iam-account=nextaccount-bot@nextaccount-v2.iam.gserviceaccount.com
```

### Step 3: Google Sheets — スプレッドシート準備

1. [Google Sheets](https://sheets.google.com) で新しいスプレッドシートを作成
2. スプレッドシートのURLから ID をコピー
   - 例: `https://docs.google.com/spreadsheets/d/`**`1aBcD...`**`/edit`
3. 「共有」→ サービスアカウントのメールアドレス（`nextaccount-bot@...`）を追加 → 編集者に設定

### Step 4: Slack App 設定

1. [api.slack.com/apps](https://api.slack.com/apps) → 「Create New App」→「From scratch」
2. **Socket Mode を有効化**
   - 「Socket Mode」→「Enable Socket Mode」→ App-Level Token 生成（名前: `nextaccount`）→ `xapp-...` をコピー
3. **Bot Token スコープを追加**
   - 「OAuth & Permissions」→「Scopes」→「Bot Token Scopes」に追加:
     - `files:read`
     - `chat:write`
     - `channels:history`
     - `users:read`
4. **Event Subscriptions を設定**
   - 「Event Subscriptions」→ Enable → Subscribe to bot events:
     - `file_shared`
     - `app_mention`
5. **Interactivity を有効化**
   - 「Interactivity & Shortcuts」→ Enable
6. **アプリをワークスペースにインストール**
   - 「Install App」→「Install to Workspace」→ `xoxb-...` をコピー

### Step 5: 環境変数を設定

```bash
cp config/.env.example .env
# .env を編集して各値を入力
```

```env
SLACK_BOT_TOKEN=xoxb-xxxx
SLACK_APP_TOKEN=xapp-xxxx
GOOGLE_APPLICATION_CREDENTIALS=/secrets/google_key.json
GOOGLE_SHEET_ID=1aBcD...
DATABASE_URL=postgresql://postgres:PASSWORD@db.xxxx.supabase.co:5432/postgres
TZ=Asia/Tokyo
```

### Step 6: ローカルで動作確認

```bash
# 仮想環境作成
python3.11 -m venv venv
source venv/bin/activate

# 依存ライブラリインストール
pip install -r requirements.txt

# 起動
python main.py
```

Slack のチャンネルで Bot を @メンションしてヘルプが返ってくれば成功。

---

## クラウドデプロイ

### Cloud Run（推奨）

```bash
# PROJECT_ID を設定
export PROJECT_ID=your-gcp-project-id

# Secret Manager に認証情報を登録
echo -n "xoxb-..." | gcloud secrets create SLACK_BOT_TOKEN --data-file=- --replication-policy=automatic
echo -n "xapp-..." | gcloud secrets create SLACK_APP_TOKEN --data-file=- --replication-policy=automatic
echo -n "postgresql://..." | gcloud secrets create DATABASE_URL --data-file=- --replication-policy=automatic
echo -n "1aBcD..." | gcloud secrets create GOOGLE_SHEET_ID --data-file=- --replication-policy=automatic
gcloud secrets create GOOGLE_KEY_JSON --data-file=/secrets/google_key.json --replication-policy=automatic

# デプロイ（scripts/deploy_cloud_run.sh を編集して PROJECT_ID を設定）
chmod +x scripts/deploy_cloud_run.sh
./scripts/deploy_cloud_run.sh
```

### Railway（簡単デプロイ）

1. [Railway.app](https://railway.app) でアカウント作成
2. 「New Project」→「Deploy from GitHub repo」
3. リポジトリを選択
4. 「Variables」に以下を設定:
   - `SLACK_BOT_TOKEN`
   - `SLACK_APP_TOKEN`
   - `DATABASE_URL`
   - `GOOGLE_SHEET_ID`
   - `GOOGLE_APPLICATION_CREDENTIALS` の代わりに `GOOGLE_KEY_JSON`（JSONの中身をそのまま貼る）
5. 自動デプロイが走る

---

## 従業員の使い方

1. Slack の専用チャンネルに領収書の**写真または PDF** をアップロード
2. 数秒で解析完了 → 仕訳カードが表示される
3. 内容を確認して「✅ 承認」をクリック
4. 自動的に以下が実行される:
   - **個人の月次シート** `{名前}_{YYYYMM}` に記録
   - **財務部門集計シート** `財務部門_集計` にも追記
   - *(Phase 2)* freee / MFクラウドに仕訳が自動計上

---

## 仕訳ルール

| 項目    | 内容                        |
|---------|-----------------------------|
| 借方    | 経費科目（OCR自動分類）      |
| 貸方    | `未払費用（社員名）`         |
| 管理ID  | `T{発生日YYYYMMDD}-{連番}`  |
| 税率    | 10%（軽減8%も自動判別）     |

---

## Phase 2: 会計ソフト連携の有効化

### freee

```bash
# 1. .env に追加
ACCOUNTING_SOFTWARE=freee
FREEE_CLIENT_ID=xxxx
FREEE_CLIENT_SECRET=xxxx
FREEE_COMPANY_ID=xxxx

# 2. 初回認証（一度だけ実行）
python scripts/freee_oauth.py
```

### MFクラウド会計

```bash
# .env に追加
ACCOUNTING_SOFTWARE=mfcloud
MF_ACCESS_TOKEN=xxxx
MF_OFFICE_ID=xxxx
```

承認ボタンをクリックすると、Sheets 同期後に自動で会計ソフトへ計上される。

---

## 月次バッチ処理

月末や必要な時に実行することで、Google Sheets を DB から再構築できる。

```bash
# 今月を処理
python scripts/monthly_batch.py

# 指定月を処理（例: 2026年3月）
python scripts/monthly_batch.py --year 2026 --month 3
```

---

## テスト実行

```bash
pytest tests/ -v
```

---

## トラブルシューティング

| 症状 | 確認事項 |
|------|----------|
| OCR が動かない | `GOOGLE_APPLICATION_CREDENTIALS` のパスと Vision API の有効化を確認 |
| Sheets に書き込めない | サービスアカウントを「編集者」として共有しているか確認 |
| 重複と言われる | 同じ T番号 + 金額のレコードが既にDBに存在する |
| freee 計上失敗 | `scripts/freee_oauth.py` を再実行してトークンを更新 |
| Bot が反応しない | Socket Mode が有効か、`SLACK_APP_TOKEN` が正しいか確認 |

---

## セキュリティチェックリスト

- [ ] `.env` を `.gitignore` に追加済み
- [ ] `google_key.json` を `.gitignore` に追加済み
- [ ] GitHub に秘密鍵をプッシュしていない
- [ ] Cloud Run は Secret Manager を使用している
- [ ] Supabase のパスワードを定期的に変更している
- [ ] freee / MF のアクセストークンを定期的にリフレッシュしている
