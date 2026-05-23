#!/usr/bin/env python3
"""
全シート（年間集計 + 社員別月次シート）の月合計行を再計算して上書きする（一回限り実行）。
実行: docker exec nextaccount python3 /app/scripts/fix_annual_totals.py
"""
import sys
sys.path.insert(0, "/app")

from core.config import GOOGLE_SHEET_ID, FINANCE_SUMMARY_SHEET_NAME
from core.sheets import SheetsManager


def fix_sheet(sm: SheetsManager, sheet_name: str) -> None:
    rows = sm._get_all_values(sheet_name)
    if not rows:
        print(f"  [{sheet_name}] データなし → スキップ")
        return

    # 発生日（B列=index1）から月一覧を収集（合計行・ヘッダーを除く）
    months = set()
    for row in rows[1:]:
        date_val = str(row[1]) if len(row) > 1 else ""
        if len(date_val) >= 7 and date_val[4] == "-":
            try:
                y = int(date_val[:4])
                m = int(date_val[5:7])
                months.add((y, m))
            except ValueError:
                pass

    if not months:
        print(f"  [{sheet_name}] 月データなし → スキップ")
        return

    for year, month in sorted(months):
        sm._update_monthly_total(sheet_name, year, month)
        print(f"  [{sheet_name}] {year:04d}/{month:02d}合計 → 再計算完了")

    # 年間集計シートのみ年間合計額を更新
    if sheet_name == FINANCE_SUMMARY_SHEET_NAME:
        sm._update_annual_total(sheet_name)
        print(f"  [{sheet_name}] 年間合計額 → 再計算完了")


def main():
    if not GOOGLE_SHEET_ID:
        print("GOOGLE_SHEET_ID が未設定です")
        sys.exit(1)

    sm = SheetsManager(GOOGLE_SHEET_ID)
    all_sheets = sm._get_sheet_names()
    print(f"シート一覧: {all_sheets}\n")

    skip = {"Sheet1", "sheet1"}
    for sheet_name in all_sheets:
        if sheet_name in skip:
            continue
        print(f"処理中: {sheet_name}")
        try:
            fix_sheet(sm, sheet_name)
        except Exception as e:
            print(f"  !! エラー: {e}")

    print("\n全シートの合計行を修正しました。")


if __name__ == "__main__":
    main()
