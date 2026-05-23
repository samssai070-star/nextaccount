"""
NextAccount v2 — core/multi_software_export.py
複数の会計ソフトウェア向けCSVエクスポート形式
- 勘定奉行（Oracle）
- PCA 会計
- TKC
- JDL
- MJS かんたん決算
"""
from __future__ import annotations
import io, csv, logging
from .accounting import calc_deductible_tax

logger = logging.getLogger(__name__)


# ============================================================
# 勘定奉行（Oracle Financials）形式
# ============================================================

def build_kanjo_ahra_csv(events: list[dict]) -> bytes:
    """
    勘定奉行インポート形式CSV
    大企業・上場企業向けの標準フォーマット
    列: 仕訳日,借方勘定コード,借方部門コード,借方金額,
        貸方勘定コード,貸方部門コード,貸方金額,摘要,附箋,伝票番号
    エンコード: UTF-8 BOM付き
    8%+10%混在は同一管理番号で2行出力
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    # ヘッダー
    writer.writerow([
        "仕訳日", "借方勘定コード", "借方部門コード", "借方金額",
        "貸方勘定コード", "貸方部門コード", "貸方金額",
        "摘要", "附箋", "伝票番号"
    ])

    def _has_10(evt): return int(evt.get("tax_10_amount", 0) or 0) > 0
    def _has_8(evt): return int(evt.get("tax_8_amount", 0) or 0) > 0

    for evt in events:
        try:
            event_date     = str(evt.get("event_date", ""))
            debit_account  = evt.get("debit_account", "消耗品費")
            credit_account = evt.get("credit_account", "未払費用")
            total_amount   = int(evt.get("amount", 0))
            tax_10         = int(evt.get("tax_10_amount", 0))
            tax_8          = int(evt.get("tax_8_amount", 0))
            taxable_10     = int(evt.get("taxable_10_amount", 0))
            taxable_8      = int(evt.get("taxable_8_amount", 0))
            counterparty   = evt.get("counterparty", "")
            employee       = evt.get("employee_name", "")
            event_id       = evt.get("event_id", "")

            # 勘定奉行では部門コードを使う（部門：100=経理部など）
            dept_code = "100"  # デフォルト
            summary = f"{counterparty}"
            if employee:
                summary += f"/{employee}"

            both = _has_10(evt) and _has_8(evt)

            if both:
                # 10% 行
                writer.writerow([
                    event_date, debit_account, dept_code, taxable_10 + tax_10,
                    credit_account, dept_code, taxable_10 + tax_10,
                    summary, "", event_id
                ])
                # 8% 行
                writer.writerow([
                    event_date, debit_account, dept_code, taxable_8 + tax_8,
                    credit_account, dept_code, taxable_8 + tax_8,
                    summary, "", event_id
                ])
            else:
                # 単一税率
                writer.writerow([
                    event_date, debit_account, dept_code, total_amount,
                    credit_account, dept_code, total_amount,
                    summary, "", event_id
                ])

        except Exception as e:
            logger.error(f"CSV conversion error for event: {e}")
            continue

    csv_str = output.getvalue()
    return "\ufeff".encode("utf-8") + csv_str.encode("utf-8")


# ============================================================
# PCA 会計形式
# ============================================================

def build_pca_csv(events: list[dict]) -> bytes:
    """
    PCA会計インポート形式CSV
    中堅企業向けの汎用フォーマット
    列: 年月日,借方勘定科目,借方補助,借方金額,借方消費税,
        貸方勘定科目,貸方補助,貸方金額,貸方消費税,摘要,参考
    エンコード: Shift_JIS (PCAの標準エンコーディング)
    8%+10%混在は同一管理番号で2行出力
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    # ヘッダー
    writer.writerow([
        "年月日", "借方勘定科目", "借方補助科目", "借方金額", "借方消費税額",
        "貸方勘定科目", "貸方補助科目", "貸方金額", "貸方消費税額",
        "摘要", "参考"
    ])

    def _has_10(evt): return int(evt.get("tax_10_amount", 0) or 0) > 0
    def _has_8(evt): return int(evt.get("tax_8_amount", 0) or 0) > 0

    for evt in events:
        try:
            event_date     = str(evt.get("event_date", "")).replace("-", "")  # YYYYMMDD
            debit_account  = evt.get("debit_account", "消耗品費")
            credit_account = evt.get("credit_account", "未払費用")
            credit_base    = credit_account.split("（")[0].strip()
            total_amount   = int(evt.get("amount", 0))
            tax_10         = int(evt.get("tax_10_amount", 0))
            tax_8          = int(evt.get("tax_8_amount", 0))
            taxable_10     = int(evt.get("taxable_10_amount", 0))
            taxable_8      = int(evt.get("taxable_8_amount", 0))
            counterparty   = evt.get("counterparty", "")
            employee       = evt.get("employee_name", "")
            event_id       = evt.get("event_id", "")
            invoice_no     = evt.get("invoice_number", "") or ""

            summary = counterparty
            if employee:
                summary += f"/{employee}"

            both = _has_10(evt) and _has_8(evt)

            if both:
                # 10% 行
                writer.writerow([
                    event_date, debit_account, "", taxable_10, tax_10,
                    credit_base, employee, taxable_10 + tax_10, 0,
                    summary, invoice_no
                ])
                # 8% 行
                writer.writerow([
                    event_date, debit_account, "", taxable_8, tax_8,
                    credit_base, employee, taxable_8 + tax_8, 0,
                    summary, invoice_no
                ])
            else:
                # 単一税率
                writer.writerow([
                    event_date, debit_account, "", taxable_10 or taxable_8, tax_10 + tax_8,
                    credit_base, employee, total_amount, 0,
                    summary, invoice_no
                ])

        except Exception as e:
            logger.error(f"CSV conversion error for event: {e}")
            continue

    csv_str = output.getvalue()
    return csv_str.encode("shift_jis")


# ============================================================
# TKC 形式（税理士事務所向け）
# ============================================================

def build_tkc_csv(events: list[dict]) -> bytes:
    """
    TKC会計インポート形式CSV
    税理士事務所・会計事務所向けフォーマット
    列: 仕訳日,勘定科目コード,補助コード,金額,消費税,税区分,摘要,証拠
    エンコード: UTF-8 BOM付き
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    # ヘッダー
    writer.writerow([
        "仕訳日", "勘定科目コード", "補助コード", "金額",
        "消費税", "税区分", "摘要", "証拠番号"
    ])

    def _has_10(evt): return int(evt.get("tax_10_amount", 0) or 0) > 0
    def _has_8(evt): return int(evt.get("tax_8_amount", 0) or 0) > 0

    for evt in events:
        try:
            event_date     = str(evt.get("event_date", ""))
            debit_account  = evt.get("debit_account", "消耗品費")
            credit_account = evt.get("credit_account", "未払費用")
            total_amount   = int(evt.get("amount", 0))
            tax_10         = int(evt.get("tax_10_amount", 0))
            tax_8          = int(evt.get("tax_8_amount", 0))
            taxable_10     = int(evt.get("taxable_10_amount", 0))
            taxable_8      = int(evt.get("taxable_8_amount", 0))
            counterparty   = evt.get("counterparty", "")
            employee       = evt.get("employee_name", "")
            event_id       = evt.get("event_id", "")
            invoice_no     = evt.get("invoice_number", "") or ""
            has_invoice    = bool(evt.get("has_invoice", False))
            expense_date   = str(evt.get("event_date", ""))

            summary = counterparty
            if invoice_no:
                summary += f"({invoice_no})"

            both = _has_10(evt) and _has_8(evt)

            if both:
                # 10% 借方
                tax_kubun = "仕入10" if has_invoice else "仕入"
                writer.writerow([
                    event_date, debit_account, "", taxable_10 + tax_10,
                    tax_10, tax_kubun, summary, event_id
                ])
                # 8% 借方
                tax_kubun_8 = "仕入8" if has_invoice else "仕入"
                writer.writerow([
                    event_date, debit_account, "", taxable_8 + tax_8,
                    tax_8, tax_kubun_8, summary, event_id
                ])
            else:
                # 単一税率 借方
                tax_total = tax_10 + tax_8
                tax_kubun = "仕入10" if (has_invoice and tax_10 > 0) else "仕入"
                writer.writerow([
                    event_date, debit_account, "", total_amount,
                    tax_total, tax_kubun, summary, event_id
                ])

            # 貸方（常に1行）
            writer.writerow([
                event_date, credit_account, employee, total_amount,
                0, "対象外", summary, event_id
            ])

        except Exception as e:
            logger.error(f"CSV conversion error for event: {e}")
            continue

    csv_str = output.getvalue()
    return "\ufeff".encode("utf-8") + csv_str.encode("utf-8")


# ============================================================
# JDL 形式（会計事務所向け）
# ============================================================

def build_jdl_csv(events: list[dict]) -> bytes:
    """
    JDL会計インポート形式CSV
    会計事務所専用フォーマット
    列: 処理月日,摘要,借方科目,借方補助,借方金額,借方消費税,
        貸方科目,貸方補助,貸方金額,貸方消費税,伝票番号
    エンコード: Shift_JIS（JDLの標準）
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    # ヘッダー
    writer.writerow([
        "処理月日", "摘要", "借方科目", "借方補助", "借方金額", "借方消費税",
        "貸方科目", "貸方補助", "貸方金額", "貸方消費税", "伝票番号"
    ])

    def _has_10(evt): return int(evt.get("tax_10_amount", 0) or 0) > 0
    def _has_8(evt): return int(evt.get("tax_8_amount", 0) or 0) > 0

    for evt in events:
        try:
            event_date     = str(evt.get("event_date", "")).replace("-", "/")
            debit_account  = evt.get("debit_account", "消耗品費")
            credit_account = evt.get("credit_account", "未払費用")
            credit_base    = credit_account.split("（")[0].strip()
            total_amount   = int(evt.get("amount", 0))
            tax_10         = int(evt.get("tax_10_amount", 0))
            tax_8          = int(evt.get("tax_8_amount", 0))
            taxable_10     = int(evt.get("taxable_10_amount", 0))
            taxable_8      = int(evt.get("taxable_8_amount", 0))
            counterparty   = evt.get("counterparty", "")
            employee       = evt.get("employee_name", "")
            event_id       = evt.get("event_id", "")
            invoice_no     = evt.get("invoice_number", "") or ""

            summary = counterparty
            if invoice_no:
                summary += f" {invoice_no}"

            both = _has_10(evt) and _has_8(evt)

            if both:
                # 10% 行
                writer.writerow([
                    event_date, summary, debit_account, "", taxable_10 + tax_10, tax_10,
                    credit_base, employee, taxable_10 + tax_10, 0, event_id
                ])
                # 8% 行
                writer.writerow([
                    event_date, summary, debit_account, "", taxable_8 + tax_8, tax_8,
                    credit_base, employee, taxable_8 + tax_8, 0, event_id
                ])
            else:
                # 単一税率
                writer.writerow([
                    event_date, summary, debit_account, "", total_amount, tax_10 + tax_8,
                    credit_base, employee, total_amount, 0, event_id
                ])

        except Exception as e:
            logger.error(f"CSV conversion error for event: {e}")
            continue

    csv_str = output.getvalue()
    return csv_str.encode("shift_jis")


# ============================================================
# MJS かんたん決算形式
# ============================================================

def build_mjs_csv(events: list[dict]) -> bytes:
    """
    MJSかんたん決算インポート形式CSV
    中小企業向けシンプルフォーマット
    列: 日付,借方勘定,借方補助,借方金額,貸方勘定,貸方補助,
        貸方金額,摘要,税区分,消費税額
    エンコード: UTF-8 BOM付き
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    # ヘッダー
    writer.writerow([
        "日付", "借方勘定", "借方補助", "借方金額",
        "貸方勘定", "貸方補助", "貸方金額",
        "摘要", "税区分", "消費税額"
    ])

    def _has_10(evt): return int(evt.get("tax_10_amount", 0) or 0) > 0
    def _has_8(evt): return int(evt.get("tax_8_amount", 0) or 0) > 0

    for evt in events:
        try:
            event_date     = str(evt.get("event_date", ""))
            debit_account  = evt.get("debit_account", "消耗品費")
            credit_account = evt.get("credit_account", "未払費用")
            credit_base    = credit_account.split("（")[0].strip()
            total_amount   = int(evt.get("amount", 0))
            tax_10         = int(evt.get("tax_10_amount", 0))
            tax_8          = int(evt.get("tax_8_amount", 0))
            taxable_10     = int(evt.get("taxable_10_amount", 0))
            taxable_8      = int(evt.get("taxable_8_amount", 0))
            counterparty   = evt.get("counterparty", "")
            employee       = evt.get("employee_name", "")
            event_id       = evt.get("event_id", "")
            has_invoice    = bool(evt.get("has_invoice", False))

            summary = counterparty
            if employee:
                summary += f" {employee}"

            both = _has_10(evt) and _has_8(evt)

            if both:
                # 10% 行
                tax_kubun = "仕入（10%）" if has_invoice else "仕入"
                writer.writerow([
                    event_date, debit_account, "", taxable_10 + tax_10,
                    credit_base, employee, taxable_10 + tax_10,
                    summary, tax_kubun, tax_10
                ])
                # 8% 行
                tax_kubun_8 = "仕入（8%）" if has_invoice else "仕入"
                writer.writerow([
                    event_date, debit_account, "", taxable_8 + tax_8,
                    credit_base, employee, taxable_8 + tax_8,
                    summary, tax_kubun_8, tax_8
                ])
            else:
                # 単一税率
                tax_kubun = "仕入（10%）" if (has_invoice and tax_10 > 0) else "仕入"
                writer.writerow([
                    event_date, debit_account, "", total_amount,
                    credit_base, employee, total_amount,
                    summary, tax_kubun, tax_10 + tax_8
                ])

        except Exception as e:
            logger.error(f"CSV conversion error for event: {e}")
            continue

    csv_str = output.getvalue()
    return "\ufeff".encode("utf-8") + csv_str.encode("utf-8")

