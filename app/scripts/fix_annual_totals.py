#!/usr/bin/env python3
"""
年間集計シートの月合計行を全月分再計算して上書きする（一回限り実行）。
実行: docker exec nextaccount python3 /app/scripts/fix_annual_totals.py
"""
import sys, os
sys.path.insert(0, "/app")

from core.config import GOOGLE_SHEET_ID, FINANCE_SUMMARY_SHEET_NAME
from core.sheets import SheetsManager

def main():
    if not GOOGLE_SHEET_ID:
        print("GOOGLE_SHEET_ID が未設定です")
        sys.exit(1)

    sm = SheetsManager(GOOGLE_SHEET_ID)
    sheet_name = FINANCE_SUMMARY_SHEET_NAME
    print(f"対象シート: {sheet_name}")

    rows = sm._get_all_values(sheet_name)
    print(f"総行数: {len(rows)}")

    # 発生日（B列）から月一覧を収集
    months = set()
    for row in rows[1:]:  # ヘッダー除外
        date_val = str(row[1]) if len(row) > 1 else ""
        if len(date_val) >= 7 and date_val[4] == "-":
            try:
                y = int(date_val[:4])
                m = int(date_val[5:7])
                months.add((y, m))
            except ValueError:
                pass

    print(f"検出月: {sorted(months)}")

    for year, month in sorted(months):
        sm._update_monthly_total(sheet_name, year, month)
        print(f"  {year:04d}/{month:02d}合計 → 再計算完了")

    print("全月の合計行を修正しました。")

if __name__ == "__main__":
    main()
