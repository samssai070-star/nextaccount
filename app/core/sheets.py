"""
NextAccount v2 — core/sheets.py
Google Sheets API v4 との連携を担当する。

シート構成:
  - 社員別月次シート: "{社員名}_{YYYYMM}"
      → 毎月自動生成、承認済み仕訳を追記
  - 財務部門集計シート: "財務部門_集計"
      → 全社員の承認済み仕訳を月次ロールアップ

列定義（SHEET_COLUMNS に準拠・16列）:
  A: 管理ID     B: 発生日     C: 取引先     D: 税込金額
  E: 税率10%対象額  F: 消費税(10%)  G: 税率8%対象額  H: 消費税(8%)
  I: T番号      J: 借方科目   K: 借方補助科目  L: 貸方科目
  M: 貸方補助科目  N: ステータス  O: 証憑     P: 用途
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import (
    GOOGLE_APPLICATION_CREDENTIALS,
    SHEET_COLUMNS,
    EMPLOYEE_SHEET_NAME_FORMAT,
    FINANCE_SUMMARY_SHEET_NAME,
)

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# ============================================================
# クライアント生成
# ============================================================

def _build_service():
    """Google Sheets API サービスを生成する"""
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_APPLICATION_CREDENTIALS,
        scopes=SCOPES,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


# ============================================================
# シート管理
# ============================================================

class SheetsManager:
    """Google Sheets との同期を管理するクラス"""

    def __init__(self, spreadsheet_id: str):
        self.spreadsheet_id = spreadsheet_id
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = _build_service()
        return self._service

    # ----------------------------------------------------------
    # シート存在確認・作成
    # ----------------------------------------------------------

    def _get_sheet_names(self) -> list[str]:
        """スプレッドシート内のシート名一覧を返す"""
        meta = (
            self.service.spreadsheets()
            .get(spreadsheetId=self.spreadsheet_id)
            .execute()
        )
        return [s["properties"]["title"] for s in meta.get("sheets", [])]

    def _ensure_sheet(self, sheet_name: str) -> None:
        """
        指定名のシートが存在しない場合は作成し、
        ヘッダー行（SHEET_COLUMNS）を書き込む。
        """
        existing = self._get_sheet_names()
        if sheet_name in existing:
            return

        # シートを追加
        body = {
            "requests": [
                {"addSheet": {"properties": {"title": sheet_name}}}
            ]
        }
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body=body,
        ).execute()
        logger.info(f"シート作成: {sheet_name}")

        # ヘッダー行を書き込む
        self._write_header(sheet_name)

    def _write_header(self, sheet_name: str) -> None:
        """ヘッダー行を書き込む（太字・背景色を適用）"""
        # 値の書き込み
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [SHEET_COLUMNS]},
        ).execute()

        # ヘッダー行を太字・背景色に設定
        sheet_id = self._get_sheet_id(sheet_name)
        if sheet_id is None:
            return

        format_body = {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": {
                                    "red": 0.26,
                                    "green": 0.52,
                                    "blue": 0.96,
                                },
                                "textFormat": {
                                    "bold": True,
                                    "foregroundColor": {
                                        "red": 1.0,
                                        "green": 1.0,
                                        "blue": 1.0,
                                    },
                                },
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }
                }
            ]
        }
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body=format_body,
        ).execute()

    def _get_sheet_id(self, sheet_name: str) -> Optional[int]:
        """シート名からシートIDを取得する"""
        meta = (
            self.service.spreadsheets()
            .get(spreadsheetId=self.spreadsheet_id)
            .execute()
        )
        for s in meta.get("sheets", []):
            if s["properties"]["title"] == sheet_name:
                return s["properties"]["sheetId"]
        return None

    # ----------------------------------------------------------
    # データ書き込み
    # ----------------------------------------------------------

    def _append_row(self, sheet_name: str, row: list) -> None:
        """シートの末尾に1行追記する"""
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

    def _get_all_values(self, sheet_name: str) -> list[list]:
        """シートの全データを取得する"""
        result = (
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A:P",
            )
            .execute()
        )
        return result.get("values", [])

    def _sort_by_date(self, sheet_name: str) -> None:
        """
        B列（発生日）で降順ソートする。
        ヘッダー行（1行目）は除外。
        """
        sheet_id = self._get_sheet_id(sheet_name)
        if sheet_id is None:
            return

        body = {
            "requests": [
                {
                    "sortRange": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,  # ヘッダーを除く
                        },
                        "sortSpecs": [
                            {
                                "dimensionIndex": 1,  # B列
                                "sortOrder": "DESCENDING",
                            }
                        ],
                    }
                }
            ]
        }
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body=body,
        ).execute()

    # ----------------------------------------------------------
    # 月次合計行
    # ----------------------------------------------------------

    def _update_monthly_total(self, sheet_name: str, year: int, month: int) -> None:
        """
        月次合計行を更新（または追記）する。
        合計行のフォーマット: "{YYYY}/{MM}合計"
        """
        rows = self._get_all_values(sheet_name)
        total_label = f"{year:04d}/{month:02d}合計"

        # D列（index 3）の金額を合計
        total_amount = 0
        total_10 = 0
        total_tax10 = 0
        total_8 = 0
        total_tax8 = 0

        existing_total_row = None
        for i, row in enumerate(rows):
            if len(row) > 0 and row[0] == total_label:
                existing_total_row = i + 1  # 1-indexed
                continue
            if len(row) >= 4:
                try:
                    total_amount += int(str(row[3]).replace(",", "").replace("¥", "") or 0)
                    total_10     += int(str(row[4]).replace(",", "") if len(row) > 4 else 0)
                    total_tax10  += int(str(row[5]).replace(",", "") if len(row) > 5 else 0)
                    total_8      += int(str(row[6]).replace(",", "") if len(row) > 6 else 0)
                    total_tax8   += int(str(row[7]).replace(",", "") if len(row) > 7 else 0)
                except (ValueError, TypeError):
                    pass

        total_row = [
            total_label, "", "", total_amount,
            total_10, total_tax10, total_8, total_tax8,
            "", "", "", "", "", "", "", "",
        ]

        if existing_total_row:
            # 既存の合計行を上書き
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A{existing_total_row}",
                valueInputOption="USER_ENTERED",
                body={"values": [total_row]},
            ).execute()
        else:
            self._append_row(sheet_name, total_row)

    # ----------------------------------------------------------
    # 公開 API
    # ----------------------------------------------------------

    def write_journal_entry(self, entry) -> bool:
        """
        JournalEntry を受け取り、社員別月次シートと財務集計シートに書き込む。

        Args:
            entry: core.accounting.JournalEntry

        Returns:
            bool: 成功した場合 True
        """
        try:
            event_date = entry.event_date  # YYYY-MM-DD
            year, month = int(event_date[:4]), int(event_date[5:7])
            ym = f"{year:04d}{month:02d}"

            # ===== 1. 社員別月次シート =====
            employee_sheet = EMPLOYEE_SHEET_NAME_FORMAT.format(
                employee=entry.employee_name, ym=ym
            )
            self._ensure_sheet(employee_sheet)
            self._append_row(employee_sheet, entry.to_sheet_row())
            self._sort_by_date(employee_sheet)
            self._update_monthly_total(employee_sheet, year, month)
            logger.info(f"社員シート書き込み: {employee_sheet}")

            # ===== 2. 財務集計シート =====
            self._ensure_sheet(FINANCE_SUMMARY_SHEET_NAME)
            self._append_row(FINANCE_SUMMARY_SHEET_NAME, entry.to_sheet_row())
            self._sort_by_date(FINANCE_SUMMARY_SHEET_NAME)
            self._update_monthly_total(FINANCE_SUMMARY_SHEET_NAME, year, month)
            logger.info(f"財務集計シート書き込み完了")

            return True

        except HttpError as e:
            logger.error(f"Sheets API エラー: {e}")
            return False
        except Exception as e:
            logger.error(f"シート書き込みエラー: {e}", exc_info=True)
            return False

    def rebuild_employee_sheet(
        self, employee_name: str, year: int, month: int, events: list[dict]
    ) -> bool:
        """
        社員・月を指定して、DBから取得したイベント一覧でシートを再構築する。
        月次バッチ処理や修正後の再同期に使用する。
        """
        ym = f"{year:04d}{month:02d}"
        sheet_name = EMPLOYEE_SHEET_NAME_FORMAT.format(employee=employee_name, ym=ym)

        try:
            self._ensure_sheet(sheet_name)

            # ヘッダー以降をクリア（16列）
            sheet_id = self._get_sheet_id(sheet_name)
            if sheet_id:
                self.service.spreadsheets().values().clear(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{sheet_name}'!A2:P",
                ).execute()

            from .accounting import JournalEntry
            for evt in events:
                entry = JournalEntry(
                    event_id          = evt.get("event_id", ""),
                    event_date        = str(evt.get("event_date", "")),
                    counterparty      = evt.get("counterparty", ""),
                    total_amount      = evt.get("amount", 0),
                    taxable_10_amount = evt.get("taxable_10_amount", 0),
                    tax_10_amount     = evt.get("tax_10_amount", 0),
                    taxable_8_amount  = evt.get("taxable_8_amount", 0),
                    tax_8_amount      = evt.get("tax_8_amount", 0),
                    invoice_number    = evt.get("invoice_number"),
                    has_invoice       = bool(evt.get("has_invoice")),
                    debit_account     = evt.get("debit_account", ""),
                    debit_subsidiary  = evt.get("debit_subsidiary", ""),
                    credit_account    = evt.get("credit_account", ""),
                    employee_name     = evt.get("employee_name", ""),
                    status            = evt.get("status", ""),
                    evidence_url      = evt.get("evidence_url", ""),
                    purpose           = evt.get("purpose", "") or evt.get("memo", ""),
                )
                self._append_row(sheet_name, entry.to_sheet_row())

            self._sort_by_date(sheet_name)
            self._update_monthly_total(sheet_name, year, month)
            logger.info(f"シート再構築完了: {sheet_name} ({len(events)} 件)")
            return True

        except Exception as e:
            logger.error(f"シート再構築エラー: {e}", exc_info=True)
            return False
