"""
一回限り: T20251124-00011 の古い重複行を削除し、最新行を残す
"""
import os, sys

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
TARGET_ID      = "T20251124-00011"

if not SPREADSHEET_ID:
    print("ERROR: GOOGLE_SHEET_ID 未設定"); sys.exit(1)

from google.oauth2 import service_account
from googleapiclient.discovery import build

creds  = service_account.Credentials.from_service_account_file(
    CREDS_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
svc    = build("sheets", "v4", credentials=creds, cache_discovery=False)
sheets = svc.spreadsheets()

meta       = sheets.get(spreadsheetId=SPREADSHEET_ID).execute()
all_sheets = [(s["properties"]["title"], s["properties"]["sheetId"])
              for s in meta.get("sheets", [])]

for sheet_name, sheet_id in all_sheets:
    col_a = (
        sheets.values()
        .get(spreadsheetId=SPREADSHEET_ID, range=f"'{sheet_name}'!A:A")
        .execute()
        .get("values", [])
    )

    # 対象IDが存在する行インデックス（0-based）を全て収集
    matches = [i for i, r in enumerate(col_a) if r and r[0] == TARGET_ID]

    if len(matches) <= 1:
        continue  # 重複なし

    print(f"{sheet_name}: {len(matches)}行 見つかりました → 最新1行を残して削除")

    # 最後の行（最新）を残し、それ以外を後ろから削除
    to_delete = matches[:-1]
    for row_idx in reversed(to_delete):
        sheets.batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_idx,
                        "endIndex": row_idx + 1,
                    }
                }
            }]}
        ).execute()
        print(f"  削除: row {row_idx + 1} (0-based: {row_idx})")

print("完了")
