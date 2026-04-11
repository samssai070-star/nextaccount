"""
NextAccount v2 — adapters/mfcloud.py
マネーフォワードクラウド会計 API への仕訳自動計上アダプター。

事前準備:
  1. MFクラウド → アプリ連携 → APIキー取得
  2. .env に MF_ACCESS_TOKEN / MF_OFFICE_ID を設定

API ドキュメント:
  https://invoice.moneyforward.com/docs/api/v3
"""

from __future__ import annotations

import os
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

MF_API_BASE = "https://accounting.moneyforward.com/api/v2"


# ============================================================
# 科目マッピング (NextAccount → MFクラウド)
# ============================================================

MF_ACCOUNT_MAP: dict[str, str] = {
    "旅費交通費": "旅費交通費",
    "通信費":     "通信費",
    "水道光熱費": "水道光熱費",
    "接待交際費": "交際費",
    "消耗品費":   "消耗品費",
    "会議費":     "会議費",
    "広告宣伝費": "広告宣伝費",
    "地代家賃":   "地代家賃",
    "修繕費":     "修繕費",
    "諸雑費":     "雑費",
}

MF_CREDIT_ITEM = "未払費用"


# ============================================================
# MFクラウド API クライアント
# ============================================================

class MFCloudClient:
    """マネーフォワードクラウド会計 API クライアント"""

    def __init__(self):
        self.access_token = os.environ.get("MF_ACCESS_TOKEN", "")
        self.office_id    = os.environ.get("MF_OFFICE_ID", "")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    def _get_account_item_id(self, name: str) -> Optional[str]:
        """勘定科目名からIDを取得する"""
        resp = requests.get(
            f"{MF_API_BASE}/offices/{self.office_id}/account_items",
            headers=self._headers(),
        )
        resp.raise_for_status()
        for item in resp.json().get("account_items", []):
            if item.get("name") == name:
                return item["id"]
        logger.warning(f"MFクラウド: 勘定科目が見つかりません: {name}")
        return None

    def post_journal_entry(self, entry) -> Optional[dict]:
        """
        JournalEntry を受け取り、MFクラウド会計に仕訳を計上する。

        Returns:
            MF API のレスポンス dict / 失敗時 None
        """
        from core.accounting import JournalEntry
        e: JournalEntry = entry

        mf_debit_name  = MF_ACCOUNT_MAP.get(e.debit_account, e.debit_account)
        debit_id  = self._get_account_item_id(mf_debit_name)
        credit_id = self._get_account_item_id(MF_CREDIT_ITEM)

        if not debit_id or not credit_id:
            logger.error("MFクラウド: 勘定科目IDが取得できません")
            return None

        body = {
            "journal_entry": {
                "recognized_at": e.event_date,
                "memo":          f"{e.counterparty} / {e.employee_name}",
                "entry_details_attributes": [
                    {
                        "account_item_id": debit_id,
                        "debit_or_credit": "debit",
                        "amount":          e.total_amount,
                        "tax_amount":      e.tax_10_amount + e.tax_8_amount,
                        "description":     e.counterparty,
                    },
                    {
                        "account_item_id": credit_id,
                        "debit_or_credit": "credit",
                        "amount":          e.total_amount,
                        "tax_amount":      0,
                        "description":     f"未払費用({e.employee_name})",
                    },
                ],
            }
        }

        resp = requests.post(
            f"{MF_API_BASE}/offices/{self.office_id}/journal_entries",
            headers=self._headers(),
            json=body,
        )

        if resp.status_code in (200, 201):
            result = resp.json()
            logger.info(f"MFクラウド 計上成功: id={result.get('journal_entry', {}).get('id')}")
            return result
        else:
            logger.error(f"MFクラウド 計上失敗: {resp.status_code} — {resp.text}")
            return None
