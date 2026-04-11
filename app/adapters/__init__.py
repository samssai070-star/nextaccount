"""
NextAccount v2 — adapters/__init__.py
会計ソフトAPIアダプターの統合ディスパッチャー。

使用する会計ソフトを環境変数 ACCOUNTING_SOFTWARE で切り替える:
  ACCOUNTING_SOFTWARE=freee     → freee 会計
  ACCOUNTING_SOFTWARE=mfcloud   → マネーフォワードクラウド会計
  ACCOUNTING_SOFTWARE=none      → 計上スキップ（デフォルト）

Phase 2: bot/slack_handler.py の handle_approve から呼び出す。
"""

from __future__ import annotations

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SOFTWARE = os.environ.get("ACCOUNTING_SOFTWARE", "none").lower()


def post_to_accounting_software(entry) -> dict:
    """
    設定された会計ソフトに仕訳を計上する。

    Args:
        entry: core.accounting.JournalEntry

    Returns:
        {"success": bool, "software": str, "message": str}
    """
    if SOFTWARE == "freee":
        return _post_freee(entry)
    elif SOFTWARE == "mfcloud":
        return _post_mfcloud(entry)
    else:
        logger.info("会計ソフト連携: 無効 (ACCOUNTING_SOFTWARE=none)")
        return {"success": False, "software": "none", "message": "会計ソフト連携は無効です"}


def _post_freee(entry) -> dict:
    try:
        from .freee import FreeeClient
        client = FreeeClient()
        result = client.post_journal_entry(entry)
        if result:
            deal_id = result.get("deal", {}).get("id", "?")
            return {
                "success":  True,
                "software": "freee",
                "message":  f"freee 計上完了 (deal_id: {deal_id})",
            }
        return {"success": False, "software": "freee", "message": "freee 計上失敗"}
    except Exception as e:
        logger.error(f"freee エラー: {e}", exc_info=True)
        return {"success": False, "software": "freee", "message": str(e)}


def _post_mfcloud(entry) -> dict:
    try:
        from .mfcloud import MFCloudClient
        client = MFCloudClient()
        result = client.post_journal_entry(entry)
        if result:
            je_id = result.get("journal_entry", {}).get("id", "?")
            return {
                "success":  True,
                "software": "mfcloud",
                "message":  f"MFクラウド 計上完了 (id: {je_id})",
            }
        return {"success": False, "software": "mfcloud", "message": "MFクラウド 計上失敗"}
    except Exception as e:
        logger.error(f"MFクラウド エラー: {e}", exc_info=True)
        return {"success": False, "software": "mfcloud", "message": str(e)}
