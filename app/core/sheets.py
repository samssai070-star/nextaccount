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
import time
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
        self._meta_cache: Optional[dict] = None  # spreadsheets().get() のキャッシュ

    @property
    def service(self):
        if self._service is None:
            self._service = _build_service()
        return self._service

    # ----------------------------------------------------------
    # リトライ付きAPIコール
    # ----------------------------------------------------------

    def _execute(self, request, max_retries: int = 4):
        """APIリクエストを実行する。レート制限(429)・サーバーエラー(5xx)時は指数バックオフでリトライ。"""
        for attempt in range(max_retries + 1):
            try:
                return request.execute()
            except HttpError as e:
                status = int(e.resp.status)
                if status in (429, 500, 502, 503, 504) and attempt < max_retries:
                    wait = 2 ** attempt  # 1s, 2s, 4s, 8s
                    logger.warning(
                        f"Sheets API {status} エラー → {wait}秒後リトライ ({attempt + 1}/{max_retries}): {e}"
                    )
                    time.sleep(wait)
                else:
                    raise

    # ----------------------------------------------------------
    # メタデータキャッシュ
    # ----------------------------------------------------------

    def _get_meta(self, force_refresh: bool = False) -> dict:
        """スプレッドシートのメタデータを返す（キャッシュ付き）。"""
        if self._meta_cache is None or force_refresh:
            self._meta_cache = self._execute(
                self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id)
            )
        return self._meta_cache

    def _invalidate_meta_cache(self) -> None:
        self._meta_cache = None

    # ----------------------------------------------------------
    # シート存在確認・作成
    # ----------------------------------------------------------

    def _get_sheet_names(self) -> list[str]:
        """スプレッドシート内のシート名一覧を返す（キャッシュ利用）"""
        meta = self._get_meta()
        return [s["properties"]["title"] for s in meta.get("sheets", [])]

    def _get_sheet_id(self, sheet_name: str) -> Optional[int]:
        """シート名からシートIDを取得する（キャッシュ利用）。見つからない場合は再取得して再試行。"""
        for force in (False, True):
            meta = self._get_meta(force_refresh=force)
            for s in meta.get("sheets", []):
                if s["properties"]["title"] == sheet_name:
                    return s["properties"]["sheetId"]
        return None

    def _ensure_sheet(self, sheet_name: str) -> None:
        """
        指定名のシートが存在しない場合は作成し、
        ヘッダー行（SHEET_COLUMNS）を書き込む。
        """
        if sheet_name in self._get_sheet_names():
            return

        self._execute(
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
            )
        )
        self._invalidate_meta_cache()  # 新シート追加後にキャッシュをクリア
        logger.info(f"シート作成: {sheet_name}")

        self._write_header(sheet_name)

    def _write_header(self, sheet_name: str) -> None:
        """ヘッダー行を書き込む（太字・背景色を適用）"""
        self._execute(
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A1",
                valueInputOption="USER_ENTERED",
                body={"values": [SHEET_COLUMNS]},
            )
        )

        sheet_id = self._get_sheet_id(sheet_name)
        if sheet_id is None:
            return

        self._execute(
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
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
                },
            )
        )

    # ----------------------------------------------------------
    # データ書き込み
    # ----------------------------------------------------------

    def _append_row(self, sheet_name: str, row: list) -> None:
        """シートの末尾に1行追記する"""
        self._execute(
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            )
        )

    def _write_rows_batch(self, sheet_name: str, rows: list[list]) -> None:
        """複数行を1回のAPIコールで書き込む（レート制限対策）"""
        if not rows:
            return
        self._execute(
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A2",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
        )

    def _get_all_values(self, sheet_name: str) -> list[list]:
        """シートの全データを取得する"""
        result = self._execute(
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A:P",
            )
        )
        return result.get("values", [])

    def _sort_by_date(self, sheet_name: str) -> None:
        """B列（発生日）で降順ソートする。ヘッダー行（1行目）は除外。"""
        sheet_id = self._get_sheet_id(sheet_name)
        if sheet_id is None:
            return

        self._execute(
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
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
                },
            )
        )

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

        total_amount = 0
        total_10 = 0
        total_tax10 = 0
        total_8 = 0
        total_tax8 = 0

        month_prefix = f"{year:04d}-{month:02d}"  # 発生日列でフィルタ用

        existing_total_row = None
        for i, row in enumerate(rows):
            if len(row) > 0 and row[0] == total_label:
                existing_total_row = i + 1  # 1-indexed
                continue
            # 発生日（B列=index1）が当月でない行はスキップ
            date_val = str(row[1]) if len(row) > 1 else ""
            if not date_val.startswith(month_prefix):
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
            self._execute(
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{sheet_name}'!A{existing_total_row}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [total_row]},
                )
            )
        else:
            self._append_row(sheet_name, total_row)

    def _update_annual_total(self, sheet_name: str) -> None:
        """
        年間合計額行を最下行に更新する（常に最終行に配置）。
        全月の月合計行を合算して年間集計を算出する。
        """
        LABEL = "年間合計額"
        rows  = self._get_all_values(sheet_name)

        total_amount = total_10 = total_tax10 = total_8 = total_tax8 = 0
        existing_row_index = None

        for i, row in enumerate(rows):
            id_val = str(row[0]).strip() if len(row) > 0 else ""
            if id_val == LABEL:
                existing_row_index = i + 1  # 1-indexed
                continue
            # 月合計行を検出: "YYYY/MM合計" (9文字)
            if len(id_val) == 9 and id_val[4:5] == "/" and id_val[7:] == "合計":
                try:
                    total_amount += int(str(row[3]).replace(",", "") or 0) if len(row) > 3 else 0
                    total_10     += int(str(row[4]).replace(",", "") or 0) if len(row) > 4 else 0
                    total_tax10  += int(str(row[5]).replace(",", "") or 0) if len(row) > 5 else 0
                    total_8      += int(str(row[6]).replace(",", "") or 0) if len(row) > 6 else 0
                    total_tax8   += int(str(row[7]).replace(",", "") or 0) if len(row) > 7 else 0
                except (ValueError, TypeError):
                    pass

        annual_row = [
            LABEL, "", "", total_amount,
            total_10, total_tax10, total_8, total_tax8,
            "", "", "", "", "", "", "", "",
        ]

        # 既存行を削除してから末尾に再追記（常に最下行に固定）
        if existing_row_index is not None:
            sheet_id = self._get_sheet_id(sheet_name)
            if sheet_id is not None:
                self._execute(
                    self.service.spreadsheets().batchUpdate(
                        spreadsheetId=self.spreadsheet_id,
                        body={"requests": [{
                            "deleteDimension": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "dimension": "ROWS",
                                    "startIndex": existing_row_index - 1,
                                    "endIndex": existing_row_index,
                                }
                            }
                        }]},
                    )
                )
        self._append_row(sheet_name, annual_row)
        logger.info(f"年間合計額更新: {sheet_name} → ¥{total_amount:,}")

    # ----------------------------------------------------------
    # 公開 API
    # ----------------------------------------------------------

    def _find_row_by_event_id(self, sheet_name: str, event_id: str) -> Optional[int]:
        """シート内で event_id が一致する行番号（1始まり）を返す。なければ None。"""
        col_a = self._execute(
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!A:A")
        ).get("values", [])
        return next(
            (i + 1 for i, r in enumerate(col_a) if r and r[0].strip() == event_id.strip()),
            None,
        )

    def delete_row_by_event_id(self, sheet_name: str, event_id: str) -> bool:
        """指定 event_id の行をシートから物理削除する。見つからない場合は False を返す。"""
        sheet_id = self._get_sheet_id(sheet_name)
        if sheet_id is None:
            return False
        row_index = self._find_row_by_event_id(sheet_name, event_id)
        if row_index is None:
            return False
        self._execute(
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "requests": [{
                        "deleteDimension": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": row_index - 1,  # 0-indexed
                                "endIndex": row_index,
                            }
                        }
                    }]
                },
            )
        )
        logger.info(f"行削除: {sheet_name} row={row_index} ({event_id})")
        return True

    def remove_event(self, event_id: str, employee_name: str, year: int, month: int) -> bool:
        """
        DB削除・却下後に社員シートと財務集計シート両方から行を削除し、月次合計を更新する。
        """
        ym = f"{year:04d}{month:02d}"
        employee_sheet = EMPLOYEE_SHEET_NAME_FORMAT.format(employee=employee_name, ym=ym)
        try:
            for sheet_name in [employee_sheet, FINANCE_SUMMARY_SHEET_NAME]:
                if sheet_name not in self._get_sheet_names():
                    continue
                deleted = self.delete_row_by_event_id(sheet_name, event_id)
                if deleted:
                    self._update_monthly_total(sheet_name, year, month)
                    if sheet_name == FINANCE_SUMMARY_SHEET_NAME:
                        self._update_annual_total(sheet_name)
        except Exception as e:
            logger.error(f"Sheets行削除エラー: {e}", exc_info=True)
            return False
        return True

    def rebuild_finance_summary(self, year: int, month: int, all_events: list[dict]) -> bool:
        """
        財務部門_集計シートの当月データを全再構築する。
        all_events は全社員の当月承認済み仕訳リスト。
        """
        from .accounting import JournalEntry
        sheet_name = FINANCE_SUMMARY_SHEET_NAME
        ym_prefix  = f"{year:04d}/{month:02d}"

        try:
            self._ensure_sheet(sheet_name)
            sheet_id = self._get_sheet_id(sheet_name)

            # 当月行（発生日が当月 または 合計ラベルが当月）を特定して削除
            rows = self._get_all_values(sheet_name)
            delete_indices = []
            for i, row in enumerate(rows):
                if i == 0:
                    continue  # ヘッダー
                id_val   = str(row[0]) if len(row) > 0 else ""
                date_val = str(row[1]) if len(row) > 1 else ""
                if (date_val.startswith(f"{year:04d}-{month:02d}")
                        or id_val == f"{ym_prefix}合計"):
                    delete_indices.append(i + 1)  # 1-indexed

            if delete_indices and sheet_id is not None:
                requests = [
                    {"deleteDimension": {"range": {
                        "sheetId": sheet_id, "dimension": "ROWS",
                        "startIndex": idx - 1, "endIndex": idx,
                    }}}
                    for idx in sorted(delete_indices, reverse=True)
                ]
                self._execute(
                    self.service.spreadsheets().batchUpdate(
                        spreadsheetId=self.spreadsheet_id,
                        body={"requests": requests},
                    )
                )

            # 当月仕訳を再追記
            for evt in all_events:
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
            self._update_annual_total(sheet_name)
            logger.info(f"財務集計シート再構築完了: {ym_prefix} ({len(all_events)}件)")
            return True

        except Exception as e:
            logger.error(f"財務集計シート再構築エラー: {e}", exc_info=True)
            return False

    def write_journal_entry(self, entry) -> bool:
        """
        JournalEntry を受け取り、社員別月次シートと財務集計シートに書き込む。
        既に同じ event_id の行が存在する場合は追記せず上書きする（二重登録防止）。
        """
        try:
            event_date = entry.event_date  # YYYY-MM-DD
            year, month = int(event_date[:4]), int(event_date[5:7])
            ym = f"{year:04d}{month:02d}"

            sheet_targets = [
                EMPLOYEE_SHEET_NAME_FORMAT.format(employee=entry.employee_name, ym=ym),
                FINANCE_SUMMARY_SHEET_NAME,
            ]

            for sheet_name in sheet_targets:
                self._ensure_sheet(sheet_name)
                row_index = self._find_row_by_event_id(sheet_name, entry.event_id)
                if row_index:
                    col_end = chr(ord("A") + len(entry.to_sheet_row()) - 1)
                    self._execute(
                        self.service.spreadsheets().values().update(
                            spreadsheetId=self.spreadsheet_id,
                            range=f"'{sheet_name}'!A{row_index}:{col_end}{row_index}",
                            valueInputOption="USER_ENTERED",
                            body={"values": [entry.to_sheet_row()]},
                        )
                    )
                    logger.info(f"既存行を上書き（重複防止）: {sheet_name} row={row_index} ({entry.event_id})")
                else:
                    self._append_row(sheet_name, entry.to_sheet_row())
                    logger.info(f"新規行を追加: {sheet_name} ({entry.event_id})")
                self._sort_by_date(sheet_name)
                self._update_monthly_total(sheet_name, year, month)
                if sheet_name == FINANCE_SUMMARY_SHEET_NAME:
                    self._update_annual_total(sheet_name)

            return True

        except HttpError as e:
            logger.error(f"Sheets API エラー: {e}")
            return False
        except Exception as e:
            logger.error(f"シート書き込みエラー: {e}", exc_info=True)
            return False

    def update_journal_entry(self, entry) -> bool:
        """
        event_id で既存行を検索して上書きする。
        見つからない場合は write_journal_entry にフォールバック。
        /edit や用途更新後の再同期に使用する。
        """
        try:
            event_date = entry.event_date
            year, month = int(event_date[:4]), int(event_date[5:7])
            ym = f"{year:04d}{month:02d}"

            sheet_names = [
                EMPLOYEE_SHEET_NAME_FORMAT.format(employee=entry.employee_name, ym=ym),
                FINANCE_SUMMARY_SHEET_NAME,
            ]

            for sheet_name in sheet_names:
                self._ensure_sheet(sheet_name)

                col_a = self._execute(
                    self.service.spreadsheets()
                    .values()
                    .get(spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!A:A")
                ).get("values", [])

                row_index = next(
                    (i + 1 for i, r in enumerate(col_a) if r and r[0].strip() == entry.event_id.strip()),
                    None,
                )

                logger.info(f"update_journal_entry: sheet={sheet_name} event_id={entry.event_id} row_index={row_index} col_a_count={len(col_a)}")

                if row_index:
                    self._execute(
                        self.service.spreadsheets().values().update(
                            spreadsheetId=self.spreadsheet_id,
                            range=f"'{sheet_name}'!A{row_index}",
                            valueInputOption="USER_ENTERED",
                            body={"values": [entry.to_sheet_row()]},
                        )
                    )
                    logger.info(f"行更新: {sheet_name} row={row_index} ({entry.event_id})")
                else:
                    self._append_row(sheet_name, entry.to_sheet_row())
                    logger.info(f"行追加: {sheet_name} ({entry.event_id})")

                self._sort_by_date(sheet_name)
                self._update_monthly_total(sheet_name, year, month)
                if sheet_name == FINANCE_SUMMARY_SHEET_NAME:
                    self._update_annual_total(sheet_name)

            return True

        except HttpError as e:
            logger.error(f"Sheets 更新エラー: {e}")
            return False
        except Exception as e:
            logger.error(f"Sheets 更新エラー: {e}", exc_info=True)
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

            sheet_id = self._get_sheet_id(sheet_name)
            if sheet_id:
                self._execute(
                    self.service.spreadsheets().values().clear(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"'{sheet_name}'!A2:P",
                    )
                )

            from .accounting import JournalEntry
            all_rows = []
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
                all_rows.append(entry.to_sheet_row())
            self._write_rows_batch(sheet_name, all_rows)

            self._sort_by_date(sheet_name)
            self._update_monthly_total(sheet_name, year, month)
            logger.info(f"シート再構築完了: {sheet_name} ({len(events)} 件)")
            return True

        except Exception as e:
            logger.error(f"シート再構築エラー: {e}", exc_info=True)
            return False
