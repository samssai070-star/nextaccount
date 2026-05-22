"""
NextAccount v2 — core/csv_export.py
freee・マネーフォワード・汎用CSV形式のエクスポート

8%軽減税率・10%標準税率の混在レシートは同一行番号で2行に分割出力。
"""
from __future__ import annotations
import io, csv, logging
from .accounting import calc_deductible_tax

logger = logging.getLogger(__name__)


def _has_10(evt: dict) -> bool:
    return int(evt.get("taxable_10_amount", 0) or 0) > 0 or int(evt.get("tax_10_amount", 0) or 0) > 0

def _has_8(evt: dict) -> bool:
    return int(evt.get("taxable_8_amount", 0) or 0) > 0 or int(evt.get("tax_8_amount", 0) or 0) > 0


# ============================================================
# freee形式
# ============================================================

def build_freee_csv(events: list[dict]) -> bytes:
    """
    freee会計インポート形式CSV
    列: 管理番号,発生日,借方勘定科目,借方補助科目,借方税区分,借方金額(税込),
        貸方勘定科目,貸方補助科目,貸方税区分,貸方金額(税込),摘要,メモ
    エンコード: UTF-8 BOM付き
    8%+10%混在は同一管理番号で2行出力
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    writer.writerow([
        "管理番号", "発生日", "借方勘定科目", "借方補助科目", "借方税区分",
        "借方金額(税込)", "貸方勘定科目", "貸方補助科目", "貸方税区分",
        "貸方金額(税込)", "摘要", "メモ"
    ])

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

            memo_base = "適格請求書（全額控除）" if has_invoice else ""

            def freee_kubun(is_10: bool) -> str:
                if is_10:
                    return "課税仕入10%" if has_invoice else "課税仕入(経過措置)10%"
                return "課税仕入8%(軽減)" if has_invoice else "課税仕入(経過措置)8%"

            both = _has_10(evt) and _has_8(evt)

            if both:
                amt_10 = taxable_10 + tax_10
                amt_8  = taxable_8  + tax_8
                ded_10 = calc_deductible_tax(tax_10, has_invoice, expense_date)
                ded_8  = calc_deductible_tax(tax_8,  has_invoice, expense_date)

                writer.writerow([
                    event_id, event_date,
                    debit_account, "", freee_kubun(True), amt_10,
                    credit_base, employee, "対象外", amt_10,
                    summary, memo_base or ded_10["deduction_label"],
                ])
                if ded_10["non_deductible_tax"] > 0:
                    nd = ded_10["non_deductible_tax"]
                    writer.writerow([event_id, event_date, "雑損失", "", "対象外", nd,
                                     credit_base, employee, "対象外", nd,
                                     summary + "（控除不可分）", f"経過措置控除不可分: {nd}円"])

                writer.writerow([
                    event_id, event_date,
                    debit_account, "", freee_kubun(False), amt_8,
                    credit_base, employee, "対象外", amt_8,
                    summary, memo_base or ded_8["deduction_label"],
                ])
                if ded_8["non_deductible_tax"] > 0:
                    nd = ded_8["non_deductible_tax"]
                    writer.writerow([event_id, event_date, "雑損失", "", "対象外", nd,
                                     credit_base, employee, "対象外", nd,
                                     summary + "（控除不可分）", f"経過措置控除不可分: {nd}円"])

            elif _has_8(evt):
                ded = calc_deductible_tax(tax_8, has_invoice, expense_date)
                writer.writerow([
                    event_id, event_date,
                    debit_account, "", freee_kubun(False), total_amount,
                    credit_base, employee, "対象外", total_amount,
                    summary, memo_base or ded["deduction_label"],
                ])
                if ded["non_deductible_tax"] > 0:
                    nd = ded["non_deductible_tax"]
                    writer.writerow([event_id, event_date, "雑損失", "", "対象外", nd,
                                     credit_base, employee, "対象外", nd,
                                     summary + "（控除不可分）", f"経過措置控除不可分: {nd}円"])

            else:
                ded    = calc_deductible_tax(tax_10, has_invoice, expense_date)
                kubun  = freee_kubun(True) if tax_10 > 0 else "対象外"
                writer.writerow([
                    event_id, event_date,
                    debit_account, "", kubun, total_amount,
                    credit_base, employee, "対象外", total_amount,
                    summary, memo_base or ded["deduction_label"],
                ])
                if ded["non_deductible_tax"] > 0:
                    nd = ded["non_deductible_tax"]
                    writer.writerow([event_id, event_date, "雑損失", "", "対象外", nd,
                                     credit_base, employee, "対象外", nd,
                                     summary + "（控除不可分）", f"経過措置控除不可分: {nd}円"])

        except Exception as e:
            logger.error(f"freee CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    return "﻿".encode("utf-8") + csv_str.encode("utf-8")


# ============================================================
# マネーフォワード形式
# ============================================================

def build_mf_csv(events: list[dict]) -> bytes:
    """
    マネーフォワードクラウド会計インポート形式CSV
    列: 取引日,借方勘定科目,借方補助科目,借方税区分,借方金額,借方消費税,
        貸方勘定科目,貸方補助科目,貸方税区分,貸方金額,貸方消費税,摘要,仕訳メモ
    エンコード: UTF-8 BOM付き
    8%+10%混在は同一行番号で2行出力
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    writer.writerow([
        "取引日", "借方勘定科目", "借方補助科目", "借方税区分",
        "借方金額", "借方消費税額",
        "貸方勘定科目", "貸方補助科目", "貸方税区分",
        "貸方金額", "貸方消費税額", "摘要", "仕訳メモ"
    ])

    for evt in events:
        try:
            event_date    = str(evt.get("event_date", ""))
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
            invoice_no    = evt.get("invoice_number", "") or ""
            has_invoice   = bool(evt.get("has_invoice", False))
            expense_date  = str(evt.get("event_date", ""))

            summary = counterparty
            if employee:   summary += f" / {employee}"
            if invoice_no: summary += f" / {invoice_no}"

            memo_base = "適格請求書（全額控除）" if has_invoice else ""

            def mf_kubun(is_10: bool) -> str:
                if is_10:
                    return "課税仕入れ10%" if has_invoice else "課税仕入れ10%(経過措置)"
                return "課税仕入れ8%(軽減税率)" if has_invoice else "課税仕入れ8%(軽減税率・経過措置)"

            both = _has_10(evt) and _has_8(evt)

            if both:
                ded_10 = calc_deductible_tax(tax_10, has_invoice, expense_date)
                ded_8  = calc_deductible_tax(tax_8,  has_invoice, expense_date)

                writer.writerow([
                    event_date, debit_account, "", mf_kubun(True),
                    taxable_10, ded_10["deductible_tax"],
                    credit_base, employee, "対象外", taxable_10 + tax_10, 0,
                    summary, memo_base or ded_10["deduction_label"],
                ])
                if ded_10["non_deductible_tax"] > 0:
                    nd = ded_10["non_deductible_tax"]
                    writer.writerow([event_date, "雑損失", "", "対象外", nd, 0,
                                     credit_base, employee, "対象外", nd, 0,
                                     summary + "（控除不可分）", f"経過措置控除不可分: {nd}円"])

                writer.writerow([
                    event_date, debit_account, "", mf_kubun(False),
                    taxable_8, ded_8["deductible_tax"],
                    credit_base, employee, "対象外", taxable_8 + tax_8, 0,
                    summary, memo_base or ded_8["deduction_label"],
                ])
                if ded_8["non_deductible_tax"] > 0:
                    nd = ded_8["non_deductible_tax"]
                    writer.writerow([event_date, "雑損失", "", "対象外", nd, 0,
                                     credit_base, employee, "対象外", nd, 0,
                                     summary + "（控除不可分）", f"経過措置控除不可分: {nd}円"])

            elif _has_8(evt):
                ded = calc_deductible_tax(tax_8, has_invoice, expense_date)
                writer.writerow([
                    event_date, debit_account, "", mf_kubun(False),
                    taxable_8, ded["deductible_tax"],
                    credit_base, employee, "対象外", total_amount, 0,
                    summary, memo_base or ded["deduction_label"],
                ])
                if ded["non_deductible_tax"] > 0:
                    nd = ded["non_deductible_tax"]
                    writer.writerow([event_date, "雑損失", "", "対象外", nd, 0,
                                     credit_base, employee, "対象外", nd, 0,
                                     summary + "（控除不可分）", f"経過措置控除不可分: {nd}円"])

            else:
                ded   = calc_deductible_tax(tax_10, has_invoice, expense_date)
                kubun = mf_kubun(True) if tax_10 > 0 else "対象外"
                writer.writerow([
                    event_date, debit_account, "", kubun,
                    taxable_10, ded["deductible_tax"],
                    credit_base, employee, "対象外", total_amount, 0,
                    summary, memo_base or ded["deduction_label"],
                ])
                if ded["non_deductible_tax"] > 0:
                    nd = ded["non_deductible_tax"]
                    writer.writerow([event_date, "雑損失", "", "対象外", nd, 0,
                                     credit_base, employee, "対象外", nd, 0,
                                     summary + "（控除不可分）", f"経過措置控除不可分: {nd}円"])

        except Exception as e:
            logger.error(f"MF CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    return "﻿".encode("utf-8") + csv_str.encode("utf-8")


# ============================================================
# 汎用CSV形式（税理士向け）
# ============================================================

def build_generic_csv(events: list[dict]) -> bytes:
    """
    汎用仕訳CSV形式
    列: 日付,借方科目,借方補助科目,借方金額(税抜),借方消費税額,税区分,
        貸方科目,貸方補助科目,貸方金額(税込),摘要,T番号,控除区分,管理番号,仕訳メモ
    エンコード: UTF-8 BOM付き
    8%+10%混在は同一管理番号で2行出力
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    writer.writerow([
        "日付", "借方科目", "借方補助科目", "借方金額(税抜)", "借方消費税額", "税区分",
        "貸方科目", "貸方補助科目", "貸方金額(税込)",
        "摘要", "T番号", "控除区分", "管理番号", "仕訳メモ"
    ])

    for evt in events:
        try:
            event_date    = str(evt.get("event_date", ""))
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
            if employee: summary += f" / {employee}"

            memo_base = "適格請求書（全額控除）" if has_invoice else ""

            def gen_kubun(is_10: bool) -> str:
                if is_10:
                    return "課税仕入10%" if has_invoice else "課税仕入10%(経過措置)"
                return "課税仕入8%(軽減)" if has_invoice else "課税仕入8%(軽減・経過)"

            both = _has_10(evt) and _has_8(evt)

            if both:
                ded_10 = calc_deductible_tax(tax_10, has_invoice, expense_date)
                ded_8  = calc_deductible_tax(tax_8,  has_invoice, expense_date)

                writer.writerow([
                    event_date, debit_account, "", taxable_10, ded_10["deductible_tax"], gen_kubun(True),
                    credit_base, employee, taxable_10 + tax_10,
                    summary, invoice_no, ded_10["deduction_label"], event_id,
                    memo_base or ded_10["deduction_label"],
                ])
                if ded_10["non_deductible_tax"] > 0:
                    nd = ded_10["non_deductible_tax"]
                    writer.writerow([event_date, "雑損失", "", nd, 0, "対象外",
                                     credit_base, employee, nd,
                                     summary + "（控除不可分）", invoice_no,
                                     ded_10["deduction_label"], event_id, f"経過措置控除不可分: {nd}円"])

                writer.writerow([
                    event_date, debit_account, "", taxable_8, ded_8["deductible_tax"], gen_kubun(False),
                    credit_base, employee, taxable_8 + tax_8,
                    summary, invoice_no, ded_8["deduction_label"], event_id,
                    memo_base or ded_8["deduction_label"],
                ])
                if ded_8["non_deductible_tax"] > 0:
                    nd = ded_8["non_deductible_tax"]
                    writer.writerow([event_date, "雑損失", "", nd, 0, "対象外",
                                     credit_base, employee, nd,
                                     summary + "（控除不可分）", invoice_no,
                                     ded_8["deduction_label"], event_id, f"経過措置控除不可分: {nd}円"])

            elif _has_8(evt):
                ded = calc_deductible_tax(tax_8, has_invoice, expense_date)
                writer.writerow([
                    event_date, debit_account, "", taxable_8, ded["deductible_tax"], gen_kubun(False),
                    credit_base, employee, total_amount,
                    summary, invoice_no, ded["deduction_label"], event_id,
                    memo_base or ded["deduction_label"],
                ])
                if ded["non_deductible_tax"] > 0:
                    nd = ded["non_deductible_tax"]
                    writer.writerow([event_date, "雑損失", "", nd, 0, "対象外",
                                     credit_base, employee, nd,
                                     summary + "（控除不可分）", invoice_no,
                                     ded["deduction_label"], event_id, f"経過措置控除不可分: {nd}円"])

            else:
                ded   = calc_deductible_tax(tax_10, has_invoice, expense_date)
                kubun = gen_kubun(True) if tax_10 > 0 else "対象外"
                writer.writerow([
                    event_date, debit_account, "", taxable_10, ded["deductible_tax"], kubun,
                    credit_base, employee, total_amount,
                    summary, invoice_no, ded["deduction_label"], event_id,
                    memo_base or ded["deduction_label"],
                ])
                if ded["non_deductible_tax"] > 0:
                    nd = ded["non_deductible_tax"]
                    writer.writerow([event_date, "雑損失", "", nd, 0, "対象外",
                                     credit_base, employee, nd,
                                     summary + "（控除不可分）", invoice_no,
                                     ded["deduction_label"], event_id, f"経過措置控除不可分: {nd}円"])

        except Exception as e:
            logger.error(f"汎用CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    return "﻿".encode("utf-8") + csv_str.encode("utf-8")
