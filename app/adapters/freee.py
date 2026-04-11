"""
NextAccount v2 — adapters/freee.py
freee 会計 API v1 への仕訳自動計上アダプター。

事前準備:
  1. freee アプリ登録 → CLIENT_ID / CLIENT_SECRET 取得
  2. .env に FREEE_CLIENT_ID / FREEE_CLIENT_SECRET / FREEE_COMPANY_ID を設定
  3. 初回のみ OAuth フローを実行して access_token を取得

API ドキュメント:
  https://developer.freee.co.jp/docs/accounting
"""

from __future__ import annotations

import os
import json
import logging
import requests
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

FREEE_API_BASE = "https://api.freee.co.jp/api/1"
TOKEN_URL      = "https://accounts.freee.co.jp/public_api/token"


# ============================================================
# 認証トークン管理
# ============================================================

class FreeeTokenManager:
    """
    アクセストークンの取得・リフレッシュを管理する。
    トークンはローカルファイル（/tmp/freee_token.json）にキャッシュする。
    本番環境では Secret Manager / DB への保存を推奨。
    """

    TOKEN_CACHE = "/tmp/freee_token.json"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id     = client_id
        self.client_secret = client_secret

    def _load_cache(self) -> Optional[dict]:
        try:
            with open(self.TOKEN_CACHE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_cache(self, token: dict) -> None:
        with open(self.TOKEN_CACHE, "w") as f:
            json.dump(token, f)

    def refresh(self, refresh_token: str) -> dict:
        resp = requests.post(TOKEN_URL, data={
            "grant_type":    "refresh_token",
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
        })
        resp.raise_for_status()
        token = resp.json()
        self._save_cache(token)
        return token

    def get_access_token(self) -> str:
        cached = self._load_cache()
        if not cached:
            raise RuntimeError(
                "freee トークンが見つかりません。"
                "scripts/freee_oauth.py を実行して初回認証を完了してください。"
            )
        # 有効期限チェック（expires_in が残り60秒以下ならリフレッシュ）
        expires_at = cached.get("created_at", 0) + cached.get("expires_in", 0)
        if datetime.now().timestamp() >= expires_at - 60:
            logger.info("freee: トークンをリフレッシュ中...")
            cached = self.refresh(cached["refresh_token"])
        return cached["access_token"]


# ============================================================
# freee 勘定科目コードマッピング
# NextAccount の科目名 → freee の account_item_name
# ============================================================

FREEE_ACCOUNT_MAP: dict[str, str] = {
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

# 貸方: 未払費用
FREEE_CREDIT_ITEM = "未払費用"


# ============================================================
# freee API クライアント
# ============================================================

class FreeeClient:
    """freee 会計 API クライアント"""

    def __init__(self):
        self.client_id     = os.environ.get("FREEE_CLIENT_ID", "")
        self.client_secret = os.environ.get("FREEE_CLIENT_SECRET", "")
        self.company_id    = int(os.environ.get("FREEE_COMPANY_ID", "0"))
        self.token_mgr     = FreeeTokenManager(self.client_id, self.client_secret)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token_mgr.get_access_token()}",
            "Content-Type":  "application/json",
        }

    def _get_account_item_id(self, account_name: str) -> Optional[int]:
        """勘定科目名からIDを取得する"""
        resp = requests.get(
            f"{FREEE_API_BASE}/account_items",
            headers=self._headers(),
            params={"company_id": self.company_id},
        )
        resp.raise_for_status()
        items = resp.json().get("account_items", [])
        for item in items:
            if item["name"] == account_name:
                return item["id"]
        logger.warning(f"freee: 勘定科目が見つかりません: {account_name}")
        return None

    def post_journal_entry(self, entry) -> Optional[dict]:
        """
        JournalEntry を受け取り、freee に仕訳を計上する。

        Args:
            entry: core.accounting.JournalEntry

        Returns:
            freee API のレスポンス dict / 失敗時 None
        """
        from core.accounting import JournalEntry
        e: JournalEntry = entry

        # 借方科目名を freee の名称にマッピング
        freee_debit_name  = FREEE_ACCOUNT_MAP.get(e.debit_account, e.debit_account)
        freee_credit_name = FREEE_CREDIT_ITEM

        # 勘定科目IDを取得
        debit_id  = self._get_account_item_id(freee_debit_name)
        credit_id = self._get_account_item_id(freee_credit_name)

        if not debit_id or not credit_id:
            logger.error("freee: 勘定科目IDが取得できません")
            return None

        # 仕訳ボディ
        body = {
            "company_id": self.company_id,
            "issue_date": e.event_date,
            "type":       "expense",
            "details": [
                {
                    "account_item_id": debit_id,
                    "tax_code":        1 if e.taxable_10_amount > 0 else 0,
                    "amount":          e.total_amount,
                    "vat":             e.tax_10_amount + e.tax_8_amount,
                    "description":     e.counterparty,
                    "entry_side":      "debit",
                },
                {
                    "account_item_id": credit_id,
                    "tax_code":        0,
                    "amount":          e.total_amount,
                    "vat":             0,
                    "description":     f"未払費用({e.employee_name})",
                    "entry_side":      "credit",
                },
            ],
        }

        # インボイス番号がある場合は添付
        if e.invoice_number:
            body["qualified_invoice_status"] = "qualified"

        resp = requests.post(
            f"{FREEE_API_BASE}/deals",
            headers=self._headers(),
            json=body,
        )

        if resp.status_code in (200, 201):
            result = resp.json()
            logger.info(f"freee 計上成功: deal_id={result.get('deal', {}).get('id')}")
            return result
        else:
            logger.error(f"freee 計上失敗: {resp.status_code} — {resp.text}")
            return None
