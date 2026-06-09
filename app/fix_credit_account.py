"""一回限り: 全シートのL列から括弧内容（例: 未払費用（samssai））を削除"""
import os, re, sys

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

if not SPREADSHEET_ID:
    print("ERROR: GOOGLE_SHEET_ID 未設定"); sys.exit(1)

from google.oauth2 import service_account
from googleapiclient.discovery import build

creds  = service_account.Credentials.from_service_account_file(
    CREDS_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
svc    = build("sheets", "v4", credentials=creds, cache_discovery=False)
sheets = svc.spreadsheets()

meta       = sheets.get(spreadsheetId=SPREADSHEET_ID).execute()
all_sheets = [s["properties"]["title"] for s in meta.get("sheets", [])]

total_fixed = 0
for sheet_name in all_sheets:
    rows = (
        sheets.values()
        .get(spreadsheetId=SPREADSHEET_ID, range=f"'{sheet_name}'!L:L")
        .execute()
        .get("values", [])
    )

    updated = []
    changed = 0
    for row in rows:
        if row:
            original = row[0]
            cleaned  = re.sub(r"[（(][^）)]*[）)]", "", original).strip()
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
        print(f"  {sheet_name}: {changed}セル修正")
        total_fixed += changed

# DBも一括修正
print("\nDB修正中...")
import psycopg2
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL:
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE accounting_events SET credit_account = '未払費用' "
            "WHERE credit_account LIKE '未払費用%' AND credit_account != '未払費用'"
        )
        print(f"  DB更新: {cur.rowcount}件")
    conn.commit()
    conn.close()
else:
    print("  DATABASE_URL未設定 → DBスキップ")

print(f"\n完了 (Sheets: {total_fixed}セル修正)")
