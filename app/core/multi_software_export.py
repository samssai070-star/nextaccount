"""
NextAccount v2 — core/multi_software_export.py
会計ソフト別CSVエクスポート（5形式）
- 勘定奉行（OBC）
- PCA会計
- TKC
- JDL
- MJS かんたん決算

8%軽減税率・10%標準税率の混在レシートは同一伝票番号で2行に分割出力。
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
# 勘定奉行（OBC）形式
# ============================================================

def build_kanjo_ahra_csv(events: list[dict], dept_code: str = "") -> bytes:
    """
    勘定奉行インポート形式（仕訳日記帳）CSV
    列: 仕訳日,借方科目,借方補助,借方部門,借方金額(税込),借方消費税,借方税区分,
        貸方科目,貸方補助,貸方部門,貸方金額(税込),貸方消費税,貸方税区分,摘要,伝票番号
    エンコード: UTF-8 BOM付き
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    writer.writerow([
        "仕訳日", "借方科目", "借方補助科目", "借方部門", "借方金額(税込)", "借方消費税", "借方税区分",
        "貸方科目", "貸方補助科目", "貸方部門", "貸方金額(税込)", "貸方消費税", "貸方税区分",
        "摘要", "伝票番号",
    ])

    dept = dept_code or ""

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
            has_invoice   = bool(evt.get("has_invoice", False))

            summary = counterparty
            if employee:
                summary += f"/{employee}"

            def obc_kubun(is_10: bool) -> str:
                if is_10:
                    return "課税仕入10%" if has_invoice else "課税仕入(経過)10%"
                return "課税仕入8%(軽)" if has_invoice else "課税仕入(経過)8%"

            both = _has_10(evt) and _has_8(evt)

            if both:
                writer.writerow([
                    event_date, debit_account, "", dept, taxable_10, tax_10, obc_kubun(True),
                    credit_base, employee, dept, taxable_10 + tax_10, 0, "対象外",
                    summary, event_id,
                ])
                writer.writerow([
                    event_date, debit_account, "", dept, taxable_8, tax_8, obc_kubun(False),
                    credit_base, employee, dept, taxable_8 + tax_8, 0, "対象外",
                    summary, event_id,
                ])
            elif _has_8(evt):
                writer.writerow([
                    event_date, debit_account, "", dept, taxable_8, tax_8, obc_kubun(False),
                    credit_base, employee, dept, total_amount, 0, "対象外",
                    summary, event_id,
                ])
            else:
                kubun = obc_kubun(True) if tax_10 > 0 else "対象外"
                writer.writerow([
                    event_date, debit_account, "", dept, taxable_10, tax_10, kubun,
                    credit_base, employee, dept, total_amount, 0, "対象外",
                    summary, event_id,
                ])

        except Exception as e:
            logger.error(f"勘定奉行CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    return "﻿".encode("utf-8") + csv_str.encode("utf-8")


# ============================================================
# PCA会計形式
# ============================================================

def build_pca_csv(events: list[dict], dept_code: str = "") -> bytes:
    """
    PCA会計インポート形式CSV
    列: 年月日,借方勘定科目,借方補助科目,借方金額(税抜),借方消費税額,
        貸方勘定科目,貸方補助科目,貸方金額(税込),貸方消費税額,摘要,参考,税区分
    エンコード: Shift_JIS
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    writer.writerow([
        "年月日", "借方勘定科目", "借方補助科目", "借方金額", "借方消費税額",
        "貸方勘定科目", "貸方補助科目", "貸方金額", "貸方消費税額",
        "摘要", "参考", "税区分",
    ])

    for evt in events:
        try:
            event_date    = str(evt.get("event_date", "")).replace("-", "")
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
            event_id      = evt.get("event_id", "")

            summary = counterparty
            if employee:
                summary += f"/{employee}"

            def pca_kubun(is_10: bool) -> str:
                if is_10:
                    return "課税仕入10%" if has_invoice else "課税仕入10%(経過措置)"
                return "課税仕入8%(軽減)" if has_invoice else "課税仕入8%(軽減・経過)"

            both = _has_10(evt) and _has_8(evt)

            if both:
                writer.writerow([
                    event_date, debit_account, "", taxable_10, tax_10,
                    credit_base, employee, taxable_10 + tax_10, 0,
                    summary, invoice_no, pca_kubun(True),
                ])
                writer.writerow([
                    event_date, debit_account, "", taxable_8, tax_8,
                    credit_base, employee, taxable_8 + tax_8, 0,
                    summary, invoice_no, pca_kubun(False),
                ])
            elif _has_8(evt):
                writer.writerow([
                    event_date, debit_account, "", taxable_8, tax_8,
                    credit_base, employee, total_amount, 0,
                    summary, invoice_no, pca_kubun(False),
                ])
            else:
                kubun = pca_kubun(True) if tax_10 > 0 else "対象外"
                writer.writerow([
                    event_date, debit_account, "", taxable_10, tax_10,
                    credit_base, employee, total_amount, 0,
                    summary, invoice_no, kubun,
                ])

        except Exception as e:
            logger.error(f"PCA CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    return csv_str.encode("shift_jis", errors="replace")


# ============================================================
# TKC形式（税理士事務所向け）
# ============================================================

def build_tkc_csv(events: list[dict], dept_code: str = "") -> bytes:
    """
    TKC会計インポート形式CSV（借方・貸方の2行1セット）
    列: 仕訳日,借貸区分,勘定科目,補助科目,金額(税抜),消費税額,税区分,摘要,証拠番号
    エンコード: UTF-8 BOM付き
    8%+10%混在は各税率ごとに借方行を追加（貸方は合計1行）
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    writer.writerow([
        "仕訳日", "借貸区分", "勘定科目", "補助科目",
        "金額(税抜)", "消費税額", "税区分", "摘要", "証拠番号",
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

            summary = counterparty
            if invoice_no:
                summary += f"({invoice_no})"

            def tkc_kubun(is_10: bool) -> str:
                if is_10:
                    return "仕入10%" if has_invoice else "仕入10%(経過)"
                return "仕入8%(軽)" if has_invoice else "仕入8%(軽・経過)"

            both = _has_10(evt) and _has_8(evt)

            if both:
                writer.writerow([event_date, "借", debit_account, "", taxable_10, tax_10, tkc_kubun(True),  summary, event_id])
                writer.writerow([event_date, "借", debit_account, "", taxable_8,  tax_8,  tkc_kubun(False), summary, event_id])
            elif _has_8(evt):
                writer.writerow([event_date, "借", debit_account, "", taxable_8, tax_8, tkc_kubun(False), summary, event_id])
            else:
                kubun = tkc_kubun(True) if tax_10 > 0 else "対象外"
                writer.writerow([event_date, "借", debit_account, "", taxable_10, tax_10, kubun, summary, event_id])

            # 貸方は税込合計1行
            writer.writerow([event_date, "貸", credit_base, employee, total_amount, 0, "対象外", summary, event_id])

        except Exception as e:
            logger.error(f"TKC CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    return "﻿".encode("utf-8") + csv_str.encode("utf-8")


# ============================================================
# JDL形式（会計事務所向け）
# ============================================================

def build_jdl_csv(events: list[dict], dept_code: str = "") -> bytes:
    """
    JDL会計インポート形式CSV
    列: 処理月日,摘要,借方科目,借方補助,借方金額,借方消費税,
        貸方科目,貸方補助,貸方金額,貸方消費税,伝票番号,税区分
    エンコード: Shift_JIS
    8%+10%混在は同一伝票番号で2行出力
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    writer.writerow([
        "処理月日", "摘要", "借方科目", "借方補助", "借方金額", "借方消費税",
        "貸方科目", "貸方補助", "貸方金額", "貸方消費税", "伝票番号", "税区分",
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

            summary = counterparty
            if invoice_no:
                summary += f" {invoice_no}"

            def jdl_kubun(is_10: bool) -> str:
                if is_10:
                    return "課税10%" if has_invoice else "課税10%(経過)"
                return "課税8%軽" if has_invoice else "課税8%軽(経過)"

            both = _has_10(evt) and _has_8(evt)

            if both:
                writer.writerow([
                    event_date, summary, debit_account, "", taxable_10, tax_10,
                    credit_base, employee, taxable_10 + tax_10, 0, event_id, jdl_kubun(True),
                ])
                writer.writerow([
                    event_date, summary, debit_account, "", taxable_8, tax_8,
                    credit_base, employee, taxable_8 + tax_8, 0, event_id, jdl_kubun(False),
                ])
            elif _has_8(evt):
                writer.writerow([
                    event_date, summary, debit_account, "", taxable_8, tax_8,
                    credit_base, employee, total_amount, 0, event_id, jdl_kubun(False),
                ])
            else:
                kubun = jdl_kubun(True) if tax_10 > 0 else "対象外"
                writer.writerow([
                    event_date, summary, debit_account, "", taxable_10, tax_10,
                    credit_base, employee, total_amount, 0, event_id, kubun,
                ])

        except Exception as e:
            logger.error(f"JDL CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    return csv_str.encode("shift_jis", errors="replace")


# ============================================================
# MJSかんたん決算形式
# ============================================================

def build_mjs_csv(events: list[dict], dept_code: str = "") -> bytes:
    """
    MJSかんたん決算インポート形式CSV
    列: 日付,借方勘定,借方補助,借方金額,借方消費税,税区分,
        貸方勘定,貸方補助,貸方金額,摘要
    エンコード: UTF-8 BOM付き
    8%+10%混在は同一行番号で2行出力
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    writer.writerow([
        "日付", "借方勘定", "借方補助", "借方金額", "借方消費税", "税区分",
        "貸方勘定", "貸方補助", "貸方金額", "摘要",
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
            has_invoice   = bool(evt.get("has_invoice", False))

            summary = counterparty
            if employee:
                summary += f" {employee}"

            def mjs_kubun(is_10: bool) -> str:
                if is_10:
                    return "仕入（10%）" if has_invoice else "仕入（10%・経過）"
                return "仕入（8%軽減）" if has_invoice else "仕入（8%軽減・経過）"

            both = _has_10(evt) and _has_8(evt)

            if both:
                writer.writerow([
                    event_date, debit_account, "", taxable_10, tax_10, mjs_kubun(True),
                    credit_base, employee, taxable_10 + tax_10, summary,
                ])
                writer.writerow([
                    event_date, debit_account, "", taxable_8, tax_8, mjs_kubun(False),
                    credit_base, employee, taxable_8 + tax_8, summary,
                ])
            elif _has_8(evt):
                writer.writerow([
                    event_date, debit_account, "", taxable_8, tax_8, mjs_kubun(False),
                    credit_base, employee, total_amount, summary,
                ])
            else:
                kubun = mjs_kubun(True) if tax_10 > 0 else "対象外"
                writer.writerow([
                    event_date, debit_account, "", taxable_10, tax_10, kubun,
                    credit_base, employee, total_amount, summary,
                ])

        except Exception as e:
            logger.error(f"MJS CSV変換エラー ({evt.get('event_id')}): {e}")
            continue

    csv_str = output.getvalue()
    return "﻿".encode("utf-8") + csv_str.encode("utf-8")
