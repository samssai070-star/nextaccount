#!/usr/bin/env python3
"""
毎日深夜にDBから全データを読み取り、社員別シートと集計シートを再構築するスクリプト
cron: 0 0 * * * docker exec nextaccount python3 /app/scripts/sort_sheets.py
"""
import os, logging, psycopg2
from collections import defaultdict
from googleapiclient.discovery import build
from google.oauth2 import service_account

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sort_sheets")

SKIP_SHEETS   = ["sheet1", "財務部門_集計"]
SUMMARY_SHEET = "財務部門_集計"
APPROVED      = "\u696d\u52d9\u627f\u8a8d\u6e08"  # 業務承認済

HEADER = [
    "管理ID", "発生日", "取引先", "税込金額",
    "税率10%対象額", "消費税(10%)", "税率8%対象額", "消費税(8%)",
    "T番号", "借方科目", "借方補助科目", "貸方科目", "申請者", "ステータス", "証憑", "用途"
]


def get_sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)


def get_db_records():
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            event_id, event_date, counterparty, amount,
            taxable_10_amount, tax_10_amount, taxable_8_amount, tax_8_amount,
            invoice_number, debit_account, COALESCE(debit_subsidiary, '\'''\'') AS debit_sub, credit_account, employee_name, status, evidence_url, COALESCE(purpose, '\'''\'')
        FROM accounting_events
        WHERE status = %s
        ORDER BY employee_name, event_date
        """,
        (APPROVED,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def to_int(val):
    try:
        return int(val or 0)
    except (ValueError, TypeError):
        return 0


def ensure_sheet(service, sheet_id, title, existing_titles):
    if title not in existing_titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]}
        ).execute()
        logger.info("シート作成: %s", title)
    # 既存・新規問わずヘッダーを常に最新に更新
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=title + "!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [HEADER]}
    ).execute()


def write_sheet_with_totals(service, sheet_id, title, rows):
    monthly = defaultdict(lambda: {"rows": [], "amount": 0, "t10": 0, "tax10": 0, "t8": 0, "tax8": 0})
    for r in rows:
        date_str = str(r[1]) if r[1] else ""
        ym = date_str[:7] if len(date_str) >= 7 else "不明"
        monthly[ym]["rows"].append(r)
        monthly[ym]["amount"] += to_int(r[3])
        monthly[ym]["t10"]    += to_int(r[4])
        monthly[ym]["tax10"]  += to_int(r[5])
        monthly[ym]["t8"]     += to_int(r[6])
        monthly[ym]["tax8"]   += to_int(r[7])

    output_rows = []
    for ym in sorted(monthly.keys()):
        m = monthly[ym]
        for r in m["rows"]:
            # 証憑ハイパーリンク生成
            ev_url = r[14] or ""
            hyperlink = f'=HYPERLINK("{ev_url}","証憑")' if ev_url else ""
            output_rows.append([
                r[0], str(r[1]), r[2] or "", to_int(r[3]),
                to_int(r[4]), to_int(r[5]), to_int(r[6]), to_int(r[7]),
                r[8] or "", r[9] or "", r[10] or "", r[11] or "", r[12] or "", r[13] or "",
                hyperlink, r[15] or ""
            ])
        ym_label = ym.replace("-", "/") + "\u5408\u8a08"  # 合計
        output_rows.append([ym_label, "", "", m["amount"], m["t10"], m["tax10"], m["t8"], m["tax8"], "", "", "", "", "", ""])

    service.spreadsheets().values().clear(
        spreadsheetId=sheet_id,
        range=title + "!A2:Z10000"
    ).execute()

    if output_rows:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=title + "!A2",
            valueInputOption="USER_ENTERED",
            body={"values": output_rows}
        ).execute()

    logger.info("%s: %d件書き込み完了", title, len(rows))


def main():
    service  = get_sheets_service()
    sheet_id = os.environ["GOOGLE_SHEET_ID"]

    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing_titles = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    records = get_db_records()
    logger.info("DBレコード取得: %d件", len(records))

    if not records:
        logger.info("データなし")
        return

    # 社員×月でグループ化
    employee_months = defaultdict(lambda: defaultdict(list))
    all_data = []

    for r in records:
        employee = r[12] or "unknown"
        date_str = str(r[1]) if r[1] else ""
        ym = date_str[:7].replace("-", "") if len(date_str) >= 7 else "000000"
        employee_months[employee][ym].append(r)
        all_data.append(r)

    # 社員別月別シートを更新
    for employee in sorted(employee_months.keys()):
        for ym in sorted(employee_months[employee].keys()):
            sname = f"{employee}_{ym}"
            ensure_sheet(service, sheet_id, sname, existing_titles)
            write_sheet_with_totals(service, sheet_id, sname, employee_months[employee][ym])

    # 集計シートを全データで再構築
    ensure_sheet(service, sheet_id, SUMMARY_SHEET, existing_titles)
    write_sheet_with_totals(service, sheet_id, SUMMARY_SHEET, all_data)
    logger.info("集計シート再構築完了: %d件", len(all_data))


if __name__ == "__main__":
    main()

