"""
NextAccount v2 — core/yayoi_export.py
T番号あり → 1行仕訳（全額控除）
T番号なし → 2行仕訳（控除可能分 + 雑損失）
8%/10%混在レシートは同一伝票番号で税率ごとに分割出力
"""
from __future__ import annotations
import io, csv, logging
from .accounting import calc_deductible_tax

logger = logging.getLogger(__name__)

_TAX_KUBUN_10 = "課税仕入10%"
_TAX_KUBUN_8  = "課税仕入8%"
_TAX_KUBUN_NA = "対象外"

def _make_row(voucher_no, event_date, debit_account, debit_sub, tax_kubun,
              debit_amount, debit_tax, credit_account, credit_sub,
              credit_amount, summary, event_id, memo="") -> list:
    return [
        voucher_no, event_date, debit_account, debit_sub, "",
        tax_kubun, debit_amount, debit_tax,
        credit_account, credit_sub, "", _TAX_KUBUN_NA, credit_amount, 0,
        summary, event_id, "", "0", "", memo, "0", "", "", "", ""
    ]


def _emit_portion(
    writer,
    voucher_no: int,
    event_date: str,
    debit_account: str,
    credit_base: str,
    employee: str,
    summary: str,
    event_id: str,
    taxable_amount: int,
    tax_amount: int,
    tax_kubun: str,
    credit_amount: int,
    has_invoice: bool,
    expense_date: str,
) -> None:
    """1つの税率区分について弥生行を出力する（T番号なし時は2行）。"""
    if tax_amount == 0 and taxable_amount == 0:
        return

    deduction          = calc_deductible_tax(tax_amount, has_invoice, expense_date)
    deductible_tax     = deduction["deductible_tax"]
    non_deductible_tax = deduction["non_deductible_tax"]
    deduction_label    = deduction["deduction_label"]

    if has_invoice or non_deductible_tax == 0:
        writer.writerow(_make_row(
            voucher_no, event_date, debit_account, "",
            tax_kubun,
            taxable_amount, tax_amount,
            credit_base, employee, credit_amount,
            summary, event_id,
            "適格請求書（全額控除）" if has_invoice else "",
        ))
    else:
        writer.writerow(_make_row(
            voucher_no, event_date, debit_account, "",
            tax_kubun,
            taxable_amount, deductible_tax,
            credit_base, employee, credit_amount,
            summary, event_id,
            f"経過措置（{deduction_label}）控除可能分",
        ))
        writer.writerow(_make_row(
            voucher_no, event_date, "雑損失", "", _TAX_KUBUN_NA,
            non_deductible_tax, 0,
            credit_base, employee, non_deductible_tax,
            summary + "（控除不可分）", event_id,
            f"経過措置（{deduction_label}）控除不可分→雑損失",
        ))


def build_yayoi_csv(events: list[dict]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    voucher_no = 1

    for evt in events:
        try:
            event_date    = str(evt.get("event_date", "")).replace("-", "/")
            debit_account = evt.get("debit_account", "消耗品費")
            credit_account= evt.get("credit_account", "未払費用")
            credit_base   = credit_account.split("（")[0].strip()
            total_amount  = int(evt.get("amount", 0))
            tax_10        = int(evt.get("tax_10_amount", 0) or 0)
            tax_8         = int(evt.get("tax_8_amount", 0) or 0)
            taxable_10    = int(evt.get("taxable_10_amount", 0) or 0)
            taxable_8     = int(evt.get("taxable_8_amount", 0) or 0)
            counterparty  = evt.get("counterparty", "")
            employee      = evt.get("employee_name", "")
            event_id      = evt.get("event_id", "")
            invoice_no    = evt.get("invoice_number", "") or ""
            has_invoice   = bool(evt.get("has_invoice", False))
            expense_date  = str(evt.get("event_date", ""))

            summary = counterparty
            if employee:   summary += f" / {employee}"
            if invoice_no: summary += f" / {invoice_no}"

            has_10 = taxable_10 > 0 or tax_10 > 0
            has_8  = taxable_8  > 0 or tax_8  > 0

            if has_10 and has_8:
                # 混在: 10%分と8%分を同一伝票番号で2ブロック出力
                _emit_portion(
                    writer, voucher_no, event_date,
                    debit_account, credit_base, employee, summary, event_id,
                    taxable_10, tax_10, _TAX_KUBUN_10,
                    taxable_10 + tax_10, has_invoice, expense_date,
                )
                _emit_portion(
                    writer, voucher_no, event_date,
                    debit_account, credit_base, employee, summary, event_id,
                    taxable_8, tax_8, _TAX_KUBUN_8,
                    taxable_8 + tax_8, has_invoice, expense_date,
                )
            elif has_8:
                _emit_portion(
                    writer, voucher_no, event_date,
                    debit_account, credit_base, employee, summary, event_id,
                    taxable_8, tax_8, _TAX_KUBUN_8,
                    total_amount, has_invoice, expense_date,
                )
            else:
                # 10%のみ、または税額0（対象外）
                tax_kubun = _TAX_KUBUN_10 if tax_10 > 0 else _TAX_KUBUN_NA
                _emit_portion(
                    writer, voucher_no, event_date,
                    debit_account, credit_base, employee, summary, event_id,
                    taxable_10, tax_10, tax_kubun,
                    total_amount, has_invoice, expense_date,
                )

            voucher_no += 1

        except Exception as e:
            logger.error(f"CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    try:
        return csv_str.encode("shift_jis", errors="replace")
    except Exception:
        return csv_str.encode("utf-8")
