"""診断: samssai_202510 シートの内容を確認する"""
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

from google.oauth2 import service_account
from googleapiclient.discovery import build

creds  = service_account.Credentials.from_service_account_file(
    CREDS_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
svc    = build("sheets", "v4", credentials=creds, cache_discovery=False)
sheets = svc.spreadsheets()

rows = (
    sheets.values()
    .get(spreadsheetId=SPREADSHEET_ID, range=f"'{TARGET_SHEET}'!A:M")
    .execute()
    .get("values", [])
)

print(f"総行数: {len(rows)}")
print()
for i, row in enumerate(rows[:20]):  # 最初の20行
    j_val = row[9].strip()  if len(row) > 9  else "(空)"
    k_val = row[10].strip() if len(row) > 10 else "(空)"
    c_val = row[2].strip()  if len(row) > 2  else "(空)"
    print(f"行{i+1:2d} | C取引先={c_val[:12]:12s} | J借方科目={j_val:10s} | K補助科目={k_val}")
