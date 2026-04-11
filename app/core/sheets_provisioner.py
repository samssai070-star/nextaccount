"""
NextAccount v2 — core/sheets_provisioner.py
新規テナント用 Google Spreadsheet を自動作成・共有するモジュール。
"""
from __future__ import annotations
import logging
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from .config import GOOGLE_APPLICATION_CREDENTIALS, SHEET_COLUMNS, FINANCE_SUMMARY_SHEET_NAME

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def _build_services():
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_APPLICATION_CREDENTIALS, scopes=SCOPES)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    drive  = build("drive",  "v3", credentials=creds, cache_discovery=False)
    return sheets, drive

def provision_tenant_spreadsheet(company_name: str, share_email: Optional[str] = None) -> str:
    sheets_svc, drive_svc = _build_services()

    title = f"NextAccount — {company_name}"
    spreadsheet = sheets_svc.spreadsheets().create(
        body={
            "properties": {"title": title, "locale": "ja_JP"},
            "sheets": [{"properties": {"title": FINANCE_SUMMARY_SHEET_NAME}}],
        },
        fields="spreadsheetId,spreadsheetUrl",
    ).execute()

    spreadsheet_id = spreadsheet["spreadsheetId"]
    logger.info(f"Spreadsheet 作成: {title} → {spreadsheet_id}")

    try:
        sheets_svc.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{FINANCE_SUMMARY_SHEET_NAME}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [SHEET_COLUMNS]},
        ).execute()
        meta = sheets_svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for s in meta.get("sheets", []):
            if s["properties"]["title"] == FINANCE_SUMMARY_SHEET_NAME:
                sheet_id = s["properties"]["sheetId"]
                sheets_svc.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"requests": [{"repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": {"red": 0.26, "green": 0.52, "blue": 0.96},
                            "textFormat": {"bold": True, "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}},
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }}]},
                ).execute()
        logger.info(f"ヘッダー初期化完了")
    except HttpError as e:
        logger.warning(f"ヘッダー初期化失敗（続行）: {e}")

    if share_email:
        try:
            drive_svc.permissions().create(
                fileId=spreadsheet_id,
                body={"type": "user", "role": "writer", "emailAddress": share_email},
                sendNotificationEmail=True,
                emailMessage=f"NextAccount から {company_name} 様の経費管理シートを共有しました。",
            ).execute()
            logger.info(f"共有完了: {share_email}")
        except HttpError as e:
            logger.warning(f"共有失敗（続行）: {e}")

    return spreadsheet_id
