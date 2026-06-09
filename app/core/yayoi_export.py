"""
NextAccount v2 — core/yayoi_export.py
T番号あり → 1行仕訳（全額控除）
T番号なし → 2行仕訳（控除可能分 + 雑損失）

build_yayoi_csv  : 弥生会計デスクトップ向け（26列 A-Z、Shift-JIS）
build_yenbo_csv  : クラウド円簿(yenbo.jp)向け（25列 A-Y、Shift-JIS）
"""
from __future__ import annotations
import io, csv, logging
from .accounting import calc_deductible_tax

logger = logging.getLogger(__name__)

def _tax_kubun(tax_10: int, tax_8: int = 0) -> str:
    if tax_8 > 0 and tax_10 == 0:
        return "課税仕入8%（軽）"
    return "課税仕入10%" if tax_10 > 0 else "対象外"

def _make_row(voucher_no, event_date, debit_account, debit_sub, tax_kubun,
              debit_amount, debit_tax, credit_account, credit_sub,
              credit_amount, summary, event_id, memo="") -> list:
    # 26列（弥生会計デスクトップ A-Z）
    return [
        2000, voucher_no, "", event_date, debit_account, debit_sub, "",
        tax_kubun, debit_amount, debit_tax,
        credit_account, credit_sub, "", "対象外", credit_amount, 0,
        summary, event_id, "", "0", "", memo, "", "", "", "no"
    ]

def _make_row_yenbo(voucher_no, event_date, debit_account, debit_sub, tax_kubun,
                    debit_amount, debit_tax, credit_account, credit_sub,
                    credit_amount, summary, event_id, memo="") -> list:
    # 25列（クラウド円簿 A-Y）
    return [
        2000, voucher_no, "", event_date, debit_account, debit_sub, "",
        tax_kubun, debit_amount, debit_tax,
        credit_account, credit_sub, "", "対象外", credit_amount, 0,
        summary, event_id, "", "0", "", memo, "", "", "no"
    ]


def _build_csv(events: list[dict], row_fn) -> str:
    """共通の仕訳CSV生成ロジック。row_fn に _make_row または _make_row_yenbo を渡す。"""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    voucher_no = 1

    def _has_10(evt): return int(evt.get("tax_10_amount", 0) or 0) > 0
    def _has_8(evt):  return int(evt.get("tax_8_amount",  0) or 0) > 0

    for evt in events:
        try:
            event_date    = str(evt.get("event_date", "")).replace("-", "/")
            debit_account = evt.get("debit_account", "消耗品費")
            credit_account= evt.get("credit_account", "未払費用")
            credit_base   = credit_account.split("（")[0].strip()
            total_amount  = int(evt.get("amount", 0))
            tax_10        = int(evt.get("tax_10_amount", 0) or 0)
            tax_8         = int(evt.get("tax_8_amount",  0) or 0)
            taxable_10    = int(evt.get("taxable_10_amount", 0) or 0)
            taxable_8     = int(evt.get("taxable_8_amount",  0) or 0)
            counterparty  = evt.get("counterparty", "")
            employee      = evt.get("employee_name", "")
            event_id      = evt.get("event_id", "")
            invoice_no    = evt.get("invoice_number", "") or ""
            has_invoice   = bool(evt.get("has_invoice", False))
            expense_date  = str(evt.get("event_date", ""))

            summary = counterparty
            if employee:   summary += f" / {employee}"
            if invoice_no: summary += f" / {invoice_no}"

            both = _has_10(evt) and _has_8(evt)

            if both:
                # 10%+8%混在: 2行に分割
                ded_10 = calc_deductible_tax(tax_10, has_invoice, expense_date)
                writer.writerow(row_fn(
                    voucher_no, event_date, debit_account, "",
                    _tax_kubun(tax_10, 0),
                    taxable_10 + tax_10, ded_10["deductible_tax"],
                    credit_base, employee, taxable_10 + tax_10,
                    summary, event_id,
                    "適格請求書（10%）" if has_invoice else ded_10["deduction_label"]
                ))
                ded_8 = calc_deductible_tax(tax_8, has_invoice, expense_date)
                writer.writerow(row_fn(
                    voucher_no, event_date, debit_account, "",
                    _tax_kubun(0, tax_8),
                    taxable_8 + tax_8, ded_8["deductible_tax"],
                    credit_base, employee, taxable_8 + tax_8,
                    summary, event_id,
                    "適格請求書（8%）" if has_invoice else ded_8["deduction_label"]
                ))
            else:
                # 単一税率 or 対象外
                tax_total = tax_10 + tax_8

                if tax_8 > 0 and tax_10 == 0:
                    debit_amount = taxable_8 + tax_8
                    debit_tax    = tax_8
                elif tax_10 > 0:
                    debit_amount = taxable_10 + tax_10
                    debit_tax    = tax_10
                else:
                    # 対象外: 借方金額 = 含税合計, 消費税 = 0
                    debit_amount = total_amount
                    debit_tax    = 0

                deduction          = calc_deductible_tax(tax_total, has_invoice, expense_date)
                non_deductible_tax = deduction["non_deductible_tax"]
                deduction_label    = deduction["deduction_label"]

                # 貸借一致: debit_amount が total_amount を超える場合は total_amount を使用
                effective_debit = min(debit_amount, total_amount)
                remainder = total_amount - effective_debit  # 非課税部分（>0 なら別行追加）

                if has_invoice or non_deductible_tax == 0:
                    writer.writerow(row_fn(
                        voucher_no, event_date, debit_account, "",
                        _tax_kubun(tax_10, tax_8),
                        effective_debit, debit_tax,
                        credit_base, employee, effective_debit,
                        summary, event_id, "適格請求書（全額控除）"
                    ))
                else:
                    deductible_tax = deduction["deductible_tax"]
                    writer.writerow(row_fn(
                        voucher_no, event_date, debit_account, "",
                        _tax_kubun(deductible_tax, 0),
                        effective_debit, deductible_tax,
                        credit_base, employee, effective_debit,
                        summary, event_id, f"経過措置（{deduction_label}）控除可能分"
                    ))
                    writer.writerow(row_fn(
                        voucher_no, event_date, "雑損失", "", "対象外",
                        non_deductible_tax, 0,
                        credit_base, employee, non_deductible_tax,
                        summary + "（控除不可分）", event_id,
                        f"経過措置（{deduction_label}）控除不可分→雑損失"
                    ))

                # 非課税部分が残る場合（対象外の追加行）
                if remainder > 0:
                    writer.writerow(row_fn(
                        voucher_no, event_date, debit_account, "", "対象外",
                        remainder, 0,
                        credit_base, employee, remainder,
                        summary + "（対象外）", event_id, "対象外（非課税部分）"
                    ))

            voucher_no += 1

        except Exception as e:
            logger.error(f"CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    return output.getvalue()


def build_yayoi_csv(events: list[dict]) -> bytes:
    """弥生会計デスクトップ向け（26列 A-Z、Shift-JIS）"""
    csv_str = _build_csv(events, _make_row)
    try:
        return csv_str.encode("shift_jis", errors="replace")
    except Exception:
        return csv_str.encode("utf-8")


def build_yenbo_csv(events: list[dict]) -> bytes:
    """クラウド円簿(yenbo.jp)向け（25列 A-Y、Shift-JIS）"""
    csv_str = _build_csv(events, _make_row_yenbo)
    try:
        return csv_str.encode("shift_jis", errors="replace")
    except Exception:
        return csv_str.encode("utf-8")
