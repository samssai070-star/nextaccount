"""
NextAccount v2 — scripts/monthly_batch.py
月次バッチ処理スクリプト。

機能:
  1. 指定月の全承認済みイベントを DB から取得
  2. 社員ごとの Google Sheets を再構築（ヘッダー + データ + 合計行）
  3. 財務部門集計シートを再構築

実行方法:
  # 今月を処理
  python scripts/monthly_batch.py

  # 指定月を処理
  python scripts/monthly_batch.py --year 2026 --month 4
"""

from __future__ import annotations

import sys
import os
import argparse
import logging
from datetime import datetime

# プロジェクトルートを PATH に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from core.database import list_all_events_by_month, init_database
from core.sheets import SheetsManager
from core.config import GOOGLE_SHEET_ID, FINANCE_SUMMARY_SHEET_NAME

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def run_batch(year: int, month: int) -> None:
    logger.info(f"月次バッチ開始: {year:04d}/{month:02d}")

    # DB から全イベントを取得
    events = list_all_events_by_month(year, month)
    logger.info(f"対象件数: {len(events)} 件")

    if not events:
        logger.info("対象データなし — 終了")
        return

    if not GOOGLE_SHEET_ID:
        logger.error("GOOGLE_SHEET_ID が設定されていません")
        return

    mgr = SheetsManager(GOOGLE_SHEET_ID)

    # 社員ごとにグループ化
    from collections import defaultdict
    by_employee: dict[str, list] = defaultdict(list)
    for evt in events:
        emp = evt.get("employee_name", "不明")
        by_employee[emp].append(evt)

    # 社員別シートを再構築
    for employee_name, emp_events in by_employee.items():
        logger.info(f"  社員シート再構築: {employee_name} ({len(emp_events)} 件)")
        ok = mgr.rebuild_employee_sheet(employee_name, year, month, emp_events)
        if ok:
            logger.info(f"  ✅ {employee_name} 完了")
        else:
            logger.error(f"  ❌ {employee_name} 失敗")

    # 財務集計シートを再構築
    logger.info(f"財務集計シート再構築: {len(events)} 件")
    ok = mgr.rebuild_employee_sheet(
        FINANCE_SUMMARY_SHEET_NAME, year, month, events
    )
    if ok:
        logger.info("✅ 財務集計シート完了")
    else:
        logger.error("❌ 財務集計シート失敗")

    logger.info("月次バッチ完了")


def main():
    parser = argparse.ArgumentParser(description="NextAccount v2 月次バッチ")
    parser.add_argument("--year",  type=int, default=datetime.now().year)
    parser.add_argument("--month", type=int, default=datetime.now().month)
    args = parser.parse_args()

    init_database()
    run_batch(args.year, args.month)


if __name__ == "__main__":
    main()
