"""一回限り: samssai_202510 のガソリンスタンド行のK列を「ガソリン代」に上書きする"""
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
TARGET_SHEET   = "samssai_202510"

if not SPREADSHEET_ID:
    print("ERROR: GOOGLE_SHEET_ID 未設定"); sys.exit(1)

from google.oauth2 import service_account
from googleapiclient.discovery import build

creds  = service_account.Credentials.from_service_account_file(
    CREDS_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
svc    = build("sheets", "v4", credentials=creds, cache_discovery=False)
sheets = svc.spreadsheets()

GASOLINE_KEYWORDS = ["eneos", "ガソリン", "ss", "apollostation", "apollo station",
                     "エネオス", "出光", "コスモ", "昭和シェル", "ジェネオス",
                     "ホクレン", "キグナス", "三愛"]

rows = (
    sheets.values()
    .get(spreadsheetId=SPREADSHEET_ID, range=f"'{TARGET_SHEET}'!A:K")
    .execute()
    .get("values", [])
)

updates = []
for i, row in enumerate(rows):
    if i == 0:
        continue
    counterparty = row[2].strip() if len(row) > 2 else ""
    k_val        = row[10].strip() if len(row) > 10 else ""
    cp_lower     = counterparty.lower()

    if any(kw in cp_lower for kw in GASOLINE_KEYWORDS):
        if k_val != "ガソリン代":
            updates.append((i + 1, k_val, counterparty))
            print(f"  行{i+1}: {counterparty} → {k_val or '(空)'} → ガソリン代")

if not updates:
    print("対象行なし（既にガソリン代、またはガソリンスタンドなし）")
    sys.exit(0)

print(f"\n{len(updates)} 行を更新します...")
data = [
    {"range": f"'{TARGET_SHEET}'!K{row_idx}", "values": [["ガソリン代"]]}
    for row_idx, _, _ in updates
]
sheets.values().batchUpdate(
    spreadsheetId=SPREADSHEET_ID,
    body={"valueInputOption": "USER_ENTERED", "data": data},
).execute()
print("完了")
