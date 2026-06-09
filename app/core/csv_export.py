"""
NextAccount v2 — core/csv_export.py
freee・マネーフォワード形式のCSVエクスポート
"""
from __future__ import annotations
import io, csv, logging
from .accounting import calc_deductible_tax

logger = logging.getLogger(__name__)

def _tax_kubun_freee(has_invoice: bool, tax_10: int, tax_8: int) -> str:
    """freee向け税区分を返す。8%軽減税率を正しく識別する。"""
    if tax_8 > 0 and tax_10 == 0:
        return "課税仕入8%(軽減)" if has_invoice else "課税仕入(経過措置)8%"
    if has_invoice:
        return "課税仕入10%"
    if tax_10 > 0:
        return "課税仕入(経過措置)10%"
    return "対象外"

def _tax_kubun_mf(has_invoice: bool, tax_10: int, tax_8: int) -> str:
    """MF向け税区分を返す。8%軽減税率を正しく識別する。"""
    if tax_8 > 0 and tax_10 == 0:
        return "課税仕入れ8%(軽減税率)" if has_invoice else "課税仕入れ8%(軽減税率・経過措置)"
    if has_invoice:
        return "課税仕入れ10%"
    if tax_10 > 0:
        return "課税仕入れ10%(経過措置)"
    return "対象外"


# ============================================================
# freee形式
# ============================================================

def build_freee_csv(events: list[dict]) -> bytes:
    """
    freee会計インポート形式CSV
    列: 管理番号,発生日,借方勘定科目,借方補助科目,借方税区分,借方金額,
        貸方勘定科目,貸方補助科目,貸方税区分,貸方金額,摘要,メモ
    エンコード: UTF-8 BOM付き
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    # ヘッダー
    writer.writerow([
        "管理番号", "発生日", "借方勘定科目", "借方補助科目", "借方税区分",
        "借方金額(税込)", "貸方勘定科目", "貸方補助科目", "貸方税区分",
        "貸方金額(税込)", "摘要", "メモ"
    ])

    for evt in events:
        try:
            event_date     = str(evt.get("event_date", "")).replace("-", "/")
            debit_account  = evt.get("debit_account", "消耗品費")
            credit_account = evt.get("credit_account", "未払費用")
            credit_base    = credit_account.split("（")[0].strip()
            total_amount   = int(evt.get("amount", 0))
            tax_10         = int(evt.get("tax_10_amount", 0) or 0)
            tax_8          = int(evt.get("tax_8_amount", 0) or 0)
            counterparty   = evt.get("counterparty", "")
            employee       = evt.get("employee_name", "")
            event_id       = evt.get("event_id", "")
            invoice_no     = evt.get("invoice_number", "") or ""
            has_invoice    = bool(evt.get("has_invoice", False))
            expense_date   = str(evt.get("event_date", ""))

            tax_total  = tax_10 + tax_8
            deduction  = calc_deductible_tax(tax_total, has_invoice, expense_date)
            non_ded    = deduction["non_deductible_tax"]
            label      = deduction["deduction_label"]

            summary = counterparty
            if employee:   summary += f" / {employee}"
            if invoice_no: summary += f" / {invoice_no}"

            tax_kubun = _tax_kubun_freee(has_invoice, tax_10, tax_8)
            memo = label if not has_invoice else "適格請求書（全額控除）"

            writer.writerow([
                event_id, event_date,
                debit_account, "", tax_kubun, total_amount,
                credit_base, employee, "対象外", total_amount,
                summary, memo
            ])

            # 控除不可分は別行で雑損失
            if non_ded > 0:
                writer.writerow([
                    event_id, event_date,
                    "雑損失", "", "対象外", non_ded,
                    credit_base, employee, "対象外", non_ded,
                    summary + "（控除不可分）",
                    f"経過措置控除不可分: {non_ded}円"
                ])

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
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    # ヘッダー
    writer.writerow([
        "取引日", "借方勘定科目", "借方補助科目", "借方税区分",
        "借方金額", "借方消費税額",
        "貸方勘定科目", "貸方補助科目", "貸方税区分",
        "貸方金額", "貸方消費税額", "摘要", "仕訳メモ"
    ])

    for evt in events:
        try:
            event_date     = str(evt.get("event_date", ""))
            debit_account  = evt.get("debit_account", "消耗品費")
            credit_account = evt.get("credit_account", "未払費用")
            credit_base    = credit_account.split("（")[0].strip()
            total_amount   = int(evt.get("amount", 0))
            tax_10         = int(evt.get("tax_10_amount", 0) or 0)
            tax_8          = int(evt.get("tax_8_amount", 0) or 0)
            taxable_10     = int(evt.get("taxable_10_amount", 0) or 0)
            taxable_8      = int(evt.get("taxable_8_amount", 0) or 0)
            counterparty   = evt.get("counterparty", "")
            employee       = evt.get("employee_name", "")
            event_id       = evt.get("event_id", "")
            invoice_no     = evt.get("invoice_number", "") or ""
            has_invoice    = bool(evt.get("has_invoice", False))
            expense_date   = str(evt.get("event_date", ""))

            tax_total  = tax_10 + tax_8
            deduction  = calc_deductible_tax(tax_total, has_invoice, expense_date)
            deductible = deduction["deductible_tax"]
            non_ded    = deduction["non_deductible_tax"]
            label      = deduction["deduction_label"]

            summary = counterparty
            if employee:   summary += f" / {employee}"
            if invoice_no: summary += f" / {invoice_no}"

            # 8%軽減税率専用 or 10% or 対象外
            if tax_8 > 0 and tax_10 == 0:
                taxable_amount = taxable_8
                tax_amount     = tax_8
            else:
                taxable_amount = taxable_10
                tax_amount     = tax_10

            tax_kubun = _tax_kubun_mf(has_invoice, tax_10, tax_8)
            memo = label if not has_invoice else "適格請求書（全額控除）"

            writer.writerow([
                event_date,
                debit_account, "", tax_kubun, taxable_amount, deductible,
                credit_base, employee, "対象外", total_amount, 0,
                summary, memo
            ])

            # 控除不可分は雑損失で別行
            if non_ded > 0:
                writer.writerow([
                    event_date,
                    "雑損失", "", "対象外", non_ded, 0,
                    credit_base, employee, "対象外", non_ded, 0,
                    summary + "（控除不可分）",
                    f"経過措置控除不可分: {non_ded}円"
                ])

        except Exception as e:
            logger.error(f"MF CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    return "﻿".encode("utf-8") + csv_str.encode("utf-8")


# ============================================================
# 汎用CSV形式（税理士向け・どのソフトでも読み込み可能）
# ============================================================

def build_generic_csv(events: list[dict]) -> bytes:
    """
    汎用仕訳CSV形式
    税理士が使うどのソフトでもインポート可能な標準形式。
    列: 日付,借方科目,借方補助,借方金額,借方消費税,貸方科目,貸方補助,
        貸方金額,貸方消費税,摘要,T番号,控除区分,管理番号
    エンコード: UTF-8 BOM付き
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    writer.writerow([
        "日付", "借方科目", "借方補助科目", "借方金額(税抜)",
        "借方消費税額", "貸方科目", "貸方補助科目",
        "貸方金額(税込)", "貸方消費税額", "摘要",
        "T番号", "控除区分", "管理番号", "仕訳メモ"
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

            tax_total  = tax_10 + tax_8
            deduction  = calc_deductible_tax(tax_total, has_invoice, expense_date)
            deductible = deduction["deductible_tax"]
            non_ded    = deduction["non_deductible_tax"]
            label      = deduction["deduction_label"]

            summary = counterparty
            if employee: summary += f" / {employee}"

            # 8%軽減税率専用 or 10% or 対象外
            if tax_8 > 0 and tax_10 == 0:
                taxable_amount = taxable_8
                tax_amount     = tax_8
            else:
                taxable_amount = taxable_10
                tax_amount     = tax_10

            # メイン行
            writer.writerow([
                event_date, debit_account, "", taxable_amount, deductible,
                credit_base, employee, total_amount, 0,
                summary, invoice_no, label, event_id,
                "適格請求書（全額控除）" if has_invoice else label
            ])

            # 控除不可分（雑損失）
            if non_ded > 0:
                writer.writerow([
                    event_date, "雑損失", "", non_ded, 0,
                    credit_base, employee, non_ded, 0,
                    summary + "（控除不可分）", invoice_no,
                    label, event_id,
                    f"経過措置控除不可分: {non_ded}円"
                ])

        except Exception as e:
            logger.error(f"汎用CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    return "﻿".encode("utf-8") + csv_str.encode("utf-8")
