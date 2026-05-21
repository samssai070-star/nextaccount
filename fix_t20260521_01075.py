#!/usr/bin/env python3
"""
一回限りの修正スクリプト: T20260521-01075 の E列(taxable_10_amount)・F列(tax_10_amount) を0にする
"""
import os, sys
sys.path.insert(0, "/app")

EVENT_ID = "T20260521-01075"

def main():
    from core.database import _get_conn, get_event_by_id
    from core.sheets import SheetsManager
    from core.accounting import JournalEntry
    from core.config import FINANCE_SUMMARY_SHEET_NAME, EMPLOYEE_SHEET_NAME_FORMAT

    # テナントIDを特定
    import psycopg2
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT tenant_id FROM accounting_events WHERE event_id=%s LIMIT 1", (EVENT_ID,))
        row = cur.fetchone()
        if not row:
            print(f"❌ {EVENT_ID} が見つかりません")
            return
        tenant_id = row[0]
    conn.close()
    print(f"tenant_id: {tenant_id}")

    # DB更新
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE accounting_events SET taxable_10_amount=0, tax_10_amount=0, updated_at=NOW() "
                "WHERE event_id=%s AND tenant_id=%s",
                (EVENT_ID, tenant_id)
            )
            print(f"DB更新: rowcount={cur.rowcount}")

    # Sheets更新
    SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
    if not SHEET_ID:
        print("❌ GOOGLE_SHEET_ID 未設定")
        return

    sheets = SheetsManager(SHEET_ID)

    import psycopg2.extras
    conn2 = psycopg2.connect(DATABASE_URL)
    with conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET app.tenant_id=%s", (str(tenant_id),))
        cur.execute("SELECT * FROM accounting_events WHERE event_id=%s", (EVENT_ID,))
        evt = cur.fetchone()
    conn2.close()

    if not evt:
        print("❌ DB再取得失敗")
        return

    entry = JournalEntry(
        event_id          = evt["event_id"],
        event_date        = str(evt["event_date"]),
        counterparty      = evt["counterparty"],
        total_amount      = evt["amount"],
        taxable_10_amount = 0,
        tax_10_amount     = 0,
        taxable_8_amount  = evt.get("taxable_8_amount", 0) or 0,
        tax_8_amount      = evt.get("tax_8_amount", 0) or 0,
        invoice_number    = evt.get("invoice_number"),
        has_invoice       = bool(evt.get("has_invoice")),
        debit_account     = evt["debit_account"],
        debit_subsidiary  = evt.get("debit_subsidiary", ""),
        credit_account    = evt["credit_account"],
        employee_name     = evt.get("employee_name", ""),
        status            = evt.get("status", ""),
        evidence_url      = evt.get("evidence_url", ""),
        purpose           = evt.get("purpose", ""),
    )

    ok = sheets.update_journal_entry(entry)
    print(f"Sheets更新: {'✅ 成功' if ok else '❌ 失敗'}")

if __name__ == "__main__":
    main()
