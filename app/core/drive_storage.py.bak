"""
NextAccount v2 — core/drive_storage.py
領収書画像を顧客の Google Drive に自動保存する。

フォルダ構成:
  NextAccount_証憑/
    └── 2026/
        └── 03/
            └── T20260308-00001_900円_東京都交通局.jpg

電子帳簿保存法対応:
  - 管理ID・日付・金額・取引先をファイル名に含める
  - アップロード日時をファイルプロパティに記録
  - DriveファイルURLをDBに保存
"""

from __future__ import annotations

import io
import os
import logging
from datetime import datetime
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from .config import GOOGLE_APPLICATION_CREDENTIALS

logger = logging.getLogger(__name__)

# drive.file だと自分が作ったファイルのみ → drive スコープに変更
SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

ROOT_FOLDER_NAME = "NextAccount_証憑"


def _build_service():
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_APPLICATION_CREDENTIALS, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _get_or_create_folder(service, name: str, parent_id: Optional[str] = None) -> str:
    """フォルダを取得または作成してIDを返す"""
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])

    if files:
        return files[0]["id"]

    # 作成
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]

    folder = service.files().create(body=meta, fields="id").execute()
    logger.info(f"フォルダ作成: {name}")
    return folder["id"]


def _build_filename(entry: dict, original_filename: str) -> str:
    """
    電帳法対応のファイル名を生成する。
    形式: {管理ID}_{金額}円_{取引先}.{拡張子}
    """
    event_id     = entry.get("event_id", "unknown")
    amount       = entry.get("total_amount", 0)
    counterparty = entry.get("counterparty", "不明")

    safe_name = "".join(c for c in counterparty if c not in r'\/:*?"<>|')[:20]
    ext = original_filename.rsplit(".", 1)[-1] if "." in original_filename else "jpg"
    return f"{event_id}_{amount}円_{safe_name}.{ext}"


def upload_receipt(
    image_bytes: bytes,
    original_filename: str,
    entry: dict,
    mime_type: str = "image/jpeg",
) -> Optional[str]:
    """
    領収書画像を Google Drive にアップロードする。

    Returns:
        Google Drive の webViewLink URL / 失敗時 None
    """
    root_folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")

    try:
        service = _build_service()

        # 日付からフォルダパスを決定
        event_date = str(entry.get("event_date", datetime.now().strftime("%Y-%m-%d")))
        year  = event_date[:4]
        month = event_date[5:7]

        # フォルダ階層を作成
        if root_folder_id:
            root_id = root_folder_id
        else:
            root_id = _get_or_create_folder(service, ROOT_FOLDER_NAME)

        year_id  = _get_or_create_folder(service, year,  parent_id=root_id)
        month_id = _get_or_create_folder(service, month, parent_id=year_id)

        # ファイル名生成
        filename = _build_filename(entry, original_filename)

        # アップロード
        file_meta = {
            "name":    filename,
            "parents": [month_id],
            "properties": {
                "event_id":     entry.get("event_id", ""),
                "amount":       str(entry.get("total_amount", 0)),
                "counterparty": entry.get("counterparty", ""),
                "uploaded_at":  datetime.now().isoformat(),
            },
        }
        media = MediaIoBaseUpload(
            io.BytesIO(image_bytes),
            mimetype=mime_type,
            resumable=False,
        )
        file = service.files().create(
            body=file_meta,
            media_body=media,
            fields="id, webViewLink",
        ).execute()

        file_url = file.get("webViewLink", "")
        logger.info(f"Drive アップロード完了: {filename} → {file_url}")
        return file_url

    except Exception as e:
        logger.error(f"Drive アップロードエラー: {e}", exc_info=True)
        return None
