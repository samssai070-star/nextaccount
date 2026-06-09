"""K列の実際のバイト値を確認する"""
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

from google.oauth2 import service_account
from googleapiclient.discovery import build

creds  = service_account.Credentials.from_service_account_file(
    CREDS_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
svc    = build("sheets", "v4", credentials=creds, cache_discovery=False)
sheets = svc.spreadsheets()

rows = (
    sheets.values()
    .get(spreadsheetId=SPREADSHEET_ID, range="'samssai_202510'!A:K")
    .execute()
    .get("values", [])
)

for i, row in enumerate(rows[1:6], start=2):  # 行2〜6のみ
    k_val = row[10] if len(row) > 10 else None
    j_val = row[9] if len(row) > 9 else None
    print(f"行{i}: len(row)={len(row)}, J={repr(j_val)}, K={repr(k_val)}")
