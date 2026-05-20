"""
一回限りの修正スクリプト:
- M1ヘッダーを「貸方補助科目」に変更
- L列の括弧内容（社員名）を削除
対象: Expense_report内のsamssai_202602以外のすべてのシート
"""
import os
import re
import sys

# .env を読み込む
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

SPREADSHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
CREDS_PATH     = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/google_key.json")
SKIP_SHEET     = "samssai_202602"

if not SPREADSHEET_ID:
    print("ERROR: GOOGLE_SHEET_ID が設定されていません")
    sys.exit(1)

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds  = service_account.Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
svc    = build("sheets", "v4", credentials=creds, cache_discovery=False)
sheets = svc.spreadsheets()

# シート一覧取得
meta       = sheets.get(spreadsheetId=SPREADSHEET_ID).execute()
all_sheets = [(s["properties"]["title"], s["properties"]["sheetId"])
              for s in meta.get("sheets", [])]

print(f"スプレッドシート内シート数: {len(all_sheets)}")

for sheet_name, sheet_id in all_sheets:
    if sheet_name == SKIP_SHEET:
        print(f"  SKIP: {sheet_name}")
        continue

    print(f"  処理中: {sheet_name}")

    # ① M1 を「貸方補助科目」に更新
    sheets.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_name}'!M1",
        valueInputOption="USER_ENTERED",
        body={"values": [["貸方補助科目"]]},
    ).execute()

    # ② L列を取得
    result = sheets.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_name}'!L1:L",
    ).execute()
    rows = result.get("values", [])

    if not rows:
        print(f"    L列データなし → スキップ")
        continue

    # ③ 括弧内容を削除（例: 「未払費用（田中太郎）」→「未払費用」）
    updated = []
    changed = 0
    for row in rows:
        if row:
            original = row[0]
            cleaned  = re.sub(r"（[^）]*）|\([^\)]*\)", "", original).strip()
            updated.append([cleaned])
            if cleaned != original:
                changed += 1
        else:
            updated.append([""])

    if changed > 0:
        sheets.values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_name}'!L1:L{len(updated)}",
            valueInputOption="USER_ENTERED",
            body={"values": updated},
        ).execute()
        print(f"    M1更新 + L列 {changed}セル修正")
    else:
        print(f"    M1更新のみ（L列に括弧なし）")

print("\n完了")
