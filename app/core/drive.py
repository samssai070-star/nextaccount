"""
NextAccount v2 — core/drive.py
Google Drive API との連携（ファイル作成・画像アップロード・共有権限設定）
"""
from __future__ import annotations

import io
import logging
import time
from typing import Optional

import requests as http_requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from .config import GOOGLE_APPLICATION_CREDENTIALS

logger = logging.getLogger(__name__)

# Sheets + Drive 両方のスコープが必要
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _build_service(api: str, version: str):
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_APPLICATION_CREDENTIALS, scopes=SCOPES
    )
    return build(api, version, credentials=creds, cache_discovery=False)


class DriveManager:
    """Google Drive との連携を管理するクラス"""

    def __init__(self, folder_id: str):
        self.folder_id = folder_id
        self._drive   = None
        self._sheets  = None

    @property
    def drive(self):
        if self._drive is None:
            self._drive = _build_service("drive", "v3")
        return self._drive

    @property
    def sheets_svc(self):
        if self._sheets is None:
            self._sheets = _build_service("sheets", "v4")
        return self._sheets

    # ----------------------------------------------------------
    # リトライ付きAPIコール
    # ----------------------------------------------------------

    def _execute(self, request, max_retries: int = 3):
        for attempt in range(max_retries + 1):
            try:
                return request.execute()
            except HttpError as e:
                status = int(e.resp.status)
                if status in (429, 500, 502, 503, 504) and attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(f"Drive API {status} → {wait}s retry ({attempt+1}/{max_retries})")
                    time.sleep(wait)
                else:
                    raise

    # ----------------------------------------------------------
    # フォルダ管理
    # ----------------------------------------------------------

    def _get_or_create_subfolder(self, folder_name: str) -> str:
        """親フォルダ内にサブフォルダを取得または作成してIDを返す。"""
        query = (
            f"name='{folder_name}' and "
            f"'{self.folder_id}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        result = self._execute(
            self.drive.files().list(q=query, fields="files(id)", pageSize=1)
        )
        files = result.get("files", [])
        if files:
            return files[0]["id"]
        folder = self._execute(
            self.drive.files().create(
                body={
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [self.folder_id],
                },
                fields="id",
            )
        )
        logger.info(f"サブフォルダ作成: {folder_name}")
        return folder["id"]

    # ----------------------------------------------------------
    # スプレッドシート作成
    # ----------------------------------------------------------

    def create_spreadsheet(self, title: str, share_emails: list[str]) -> str:
        """
        指定フォルダにスプレッドシートを作成し、share_emails に writer 権限を付与する。
        作成したスプレッドシートIDを返す。
        """
        file_obj = self._execute(
            self.drive.files().create(
                body={
                    "name": title,
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                    "parents": [self.folder_id],
                },
                fields="id",
            )
        )
        spreadsheet_id = file_obj["id"]
        logger.info(f"スプレッドシート作成: {title} → {spreadsheet_id}")

        for email in share_emails:
            self.share_file(spreadsheet_id, email, role="writer")

        return spreadsheet_id

    # ----------------------------------------------------------
    # 共有権限
    # ----------------------------------------------------------

    def share_file(self, file_id: str, email: str, role: str = "reader") -> None:
        """ファイルをメールアドレスに共有する。role: reader / writer / commenter"""
        try:
            self._execute(
                self.drive.permissions().create(
                    fileId=file_id,
                    body={"type": "user", "role": role, "emailAddress": email},
                    sendNotificationEmail=False,
                )
            )
            logger.info(f"ファイル共有: {file_id} → {email} ({role})")
        except Exception as e:
            logger.warning(f"共有設定失敗 ({email}): {e}")

    # ----------------------------------------------------------
    # 領収書画像アップロード
    # ----------------------------------------------------------

    def upload_receipt_image(
        self,
        slack_file_url: str,
        filename: str,
        slack_bot_token: str,
        subfolder_name: Optional[str] = None,
    ) -> Optional[str]:
        """
        Slack の画像URLをDriveにアップロードし、webViewLink を返す。
        anyoneWithLink で閲覧可能に設定するため、税理士がSheetsから直接開ける。
        subfolder_name: 社員名などのサブフォルダ（省略可）
        """
        # 1. Slack URLから画像ダウンロード
        try:
            resp = http_requests.get(
                slack_file_url,
                headers={"Authorization": f"Bearer {slack_bot_token}"},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Slack画像ダウンロード失敗 ({slack_file_url}): {e}")
            return None

        content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
        image_data   = resp.content

        # 2. アップロード先フォルダを決定
        parent_id = self._get_or_create_subfolder(subfolder_name) if subfolder_name else self.folder_id

        # 3. Drive にアップロード
        media = MediaIoBaseUpload(io.BytesIO(image_data), mimetype=content_type, resumable=False)
        try:
            uploaded = self._execute(
                self.drive.files().create(
                    body={"name": filename, "parents": [parent_id]},
                    media_body=media,
                    fields="id,webViewLink",
                )
            )
            file_id  = uploaded["id"]
            web_link = uploaded.get("webViewLink", "")
        except Exception as e:
            logger.error(f"Driveアップロード失敗: {e}")
            return None

        # 4. anyoneWithLink で閲覧可能に（税理士・財務担当が Sheets から開けるよう）
        try:
            self._execute(
                self.drive.permissions().create(
                    fileId=file_id,
                    body={"type": "anyone", "role": "reader"},
                )
            )
        except Exception as e:
            logger.warning(f"画像公開設定失敗: {e}")

        logger.info(f"領収書アップロード完了: {filename} → {web_link}")
        return web_link
