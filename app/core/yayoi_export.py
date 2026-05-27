"""
NextAccount v2 — core/yayoi_export.py
T番号あり → 1行仕訳（全額控除）
T番号なし → 2行仕訳（控除可能分 + 雑損失）
"""
from __future__ import annotations
import io, csv, logging
from .accounting import calc_deductible_tax

logger = logging.getLogger(__name__)

def _tax_kubun(tax_10: int, tax_8: int = 0) -> str:
    if tax_8 > 0 and tax_10 == 0:
        return "課税仕入8%"
    return "課税仕入10%" if tax_10 > 0 else "対象外"

def _make_row(voucher_no, event_date, debit_account, debit_sub, tax_kubun,
              debit_amount, debit_tax, credit_account, credit_sub,
              credit_amount, summary, event_id, memo="") -> list:
    return [
        2000, voucher_no, "", event_date, debit_account, debit_sub, "",
        tax_kubun, debit_amount, debit_tax,
        credit_account, credit_sub, "", "対象外", credit_amount, 0,
        summary, event_id, "", "0", "", memo, "0", "", "", "", ""
    ]

def build_yayoi_csv(events: list[dict]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    voucher_no = 1

    def _has_10(evt): return int(evt.get("tax_10_amount", 0) or 0) > 0
    def _has_8(evt): return int(evt.get("tax_8_amount", 0) or 0) > 0

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
            if employee:  summary += f" / {employee}"
            if invoice_no: summary += f" / {invoice_no}"

            both = _has_10(evt) and _has_8(evt)

            if both:
                # 10% 行
                ded_10 = calc_deductible_tax(tax_10, has_invoice, expense_date)
                writer.writerow(_make_row(
                    voucher_no, event_date, debit_account, "",
                    _tax_kubun(tax_10, 0),
                    taxable_10 + tax_10, ded_10["deductible_tax"], credit_base, employee, taxable_10 + tax_10,
                    summary, event_id, ded_10["deduction_label"] if not has_invoice else "適格請求書（10%）"
                ))
                # 8% 行
                ded_8 = calc_deductible_tax(tax_8, has_invoice, expense_date)
                writer.writerow(_make_row(
                    voucher_no, event_date, debit_account, "",
                    _tax_kubun(0, tax_8),
                    taxable_8 + tax_8, ded_8["deductible_tax"], credit_base, employee, taxable_8 + tax_8,
                    summary, event_id, ded_8["deduction_label"] if not has_invoice else "適格請求書（8%）"
                ))
            else:
                # 単一税率
                tax_total  = tax_10 + tax_8
                deduction  = calc_deductible_tax(tax_total, has_invoice, expense_date)
                deductible_tax     = deduction["deductible_tax"]
                non_deductible_tax = deduction["non_deductible_tax"]
                deduction_label    = deduction["deduction_label"]

                # 8%専用 or 10% or 対象外の場合
                if tax_8 > 0 and tax_10 == 0:
                    taxable_amount = taxable_8
                    tax_amount     = tax_8
                elif tax_10 > 0:
                    taxable_amount = taxable_10
                    tax_amount     = tax_10
                else:
                    # 対象外: 借方金額 = 含税合計, 消費税 = 0
                    taxable_amount = total_amount
                    tax_amount     = 0

                if has_invoice or non_deductible_tax == 0:
                    writer.writerow(_make_row(
                        voucher_no, event_date, debit_account, "",
                        _tax_kubun(tax_10, tax_8),
                        taxable_amount + tax_amount, tax_amount, credit_base, employee, total_amount,
                        summary, event_id, "適格請求書（全額控除）"
                    ))
                else:
                    writer.writerow(_make_row(
                        voucher_no, event_date, debit_account, "",
                        _tax_kubun(deductible_tax, 0),
                        taxable_amount + deductible_tax, deductible_tax, credit_base, employee, total_amount,
                        summary, event_id, f"経過措置（{deduction_label}）控除可能分"
                    ))
                    writer.writerow(_make_row(
                        voucher_no, event_date, "雑損失", "", "対象外",
                        non_deductible_tax, 0, credit_base, employee, non_deductible_tax,
                        summary + "（控除不可分）", event_id,
                        f"経過措置（{deduction_label}）控除不可分→雑損失"
                    ))
            voucher_no += 1

        except Exception as e:
            logger.error(f"CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    try:
        return csv_str.encode("shift_jis", errors="replace")
    except Exception:
        return csv_str.encode("utf-8")
