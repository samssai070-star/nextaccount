"""
NextAccount v2 — core/accounting.py
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from .config import BRAND_MASTER, CREDIT_ACCOUNT_BASE, DEBIT_KEYWORDS

logger = logging.getLogger(__name__)

def get_invoice_deduction_rate(expense_date: str) -> tuple[float, str]:
    try:
        d = date.fromisoformat(expense_date)
    except (ValueError, TypeError):
        d = date.today()
    if d < date(2026, 10, 1):
        return 0.80, "経過措置80%（〜2026/9）"
    elif d < date(2028, 10, 1):
        return 0.70, "経過措置70%（〜2028/9）"
    elif d < date(2030, 10, 1):
        return 0.50, "経過措置50%（〜2030/9）"
    elif d < date(2031, 10, 1):
        return 0.30, "経過措置30%（〜2031/9）"
    else:
        return 0.00, "控除不可（経過措置終了）"

def calc_deductible_tax(tax_amount: int, has_invoice: bool, expense_date: str) -> dict:
    if has_invoice:
        return {"deductible_tax": tax_amount, "non_deductible_tax": 0, "deduction_rate": 1.0, "deduction_label": "適格請求書（全額控除）"}
    else:
        rate, label = get_invoice_deduction_rate(expense_date)
        deductible = int(tax_amount * rate)
        return {"deductible_tax": deductible, "non_deductible_tax": tax_amount - deductible, "deduction_rate": rate, "deduction_label": label}

@dataclass
class JournalEntry:
    event_id: str
    event_date: str
    counterparty: str
    total_amount: int
    taxable_10_amount: int
    tax_10_amount: int
    taxable_8_amount: int
    tax_8_amount: int
    debit_account: str
    credit_account: str
    invoice_number: Optional[str]
    has_invoice: bool
    employee_name: str
    status: str = "申請中"
    evidence_url: str = ""
    memo: str = ""
    debit_subsidiary: str = ""
    purpose: str = ""

    def get_deduction_info(self) -> dict:
        tax_total = self.tax_10_amount + self.tax_8_amount
        return calc_deductible_tax(tax_total, self.has_invoice, self.event_date)

    def to_sheet_row(self) -> list:
        deduction = self.get_deduction_info()
        return [
            self.event_id,                          # A 管理ID
            self.event_date,                        # B 発生日
            self.counterparty,                      # C 取引先
            self.total_amount,                      # D 税込金額
            self.taxable_10_amount,                 # E 税率10%対象額
            self.tax_10_amount,                     # F 消費税(10%)
            self.taxable_8_amount,                  # G 税率8%対象額
            self.tax_8_amount,                      # H 消費税(8%)
            self.invoice_number or "",              # I T番号
            self.debit_account,                     # J 借方科目
            self.debit_subsidiary or "",            # K 借方補助科目
            self.credit_account,                    # L 貳方科目
            self.employee_name,                     # M 申請者
            self.status,                            # N ステータス
            ('=HYPERLINK("' + self.evidence_url + '","証憑")') if self.evidence_url else (self.memo or ""),  # O 証憑
            self.purpose or "",                     # P 用途
        ]

    def to_db_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_date": self.event_date,
            "counterparty": self.counterparty,
            "amount": self.total_amount,
            "taxable_10_amount": self.taxable_10_amount,
            "tax_10_amount": self.tax_10_amount,
            "taxable_8_amount": self.taxable_8_amount,
            "tax_8_amount": self.tax_8_amount,
            "debit_account": self.debit_account,
            "debit_subsidiary": self.debit_subsidiary,
            "credit_account": self.credit_account,
            "invoice_number": self.invoice_number,
            "has_invoice": self.has_invoice,
            "employee_name": self.employee_name,
            "status": self.status,
            "evidence_url": self.evidence_url,
            "memo": self.memo,
            "purpose": self.purpose,
        }

def normalize_merchant_name(raw_name: str) -> str:
    lower = raw_name.lower()
    for keyword, (normalized, _) in BRAND_MASTER.items():
        if keyword.lower() in lower:
            return normalized
    return raw_name.strip() or "不明"

def classify_account(raw_text: str, merchant: str) -> str:
    """
    raw_text と merchant から勘定科目を判定する。
    1. BRAND_MASTER でのマッチ（正規化名→科目）
    2. DEBIT_KEYWORDS でのキーワードマッチ（raw_text + merchant 全体）
    3. デフォルト: 消耗品費
    """
    combined = (raw_text or "") + " " + (merchant or "")
    combined_lower = combined.lower()

    # 1. BRAND_MASTER でのマッチ
    for keyword, (_, account) in BRAND_MASTER.items():
        if keyword.lower() in combined_lower:
            return account

    # 2. DEBIT_KEYWORDS でのキーワードマッチ
    for account, keywords in DEBIT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in combined_lower:
                return account

    # 3. デフォルト
    return "消耗品費"

def generate_event_id(event_date: Optional[str], sequence: int) -> str:
    if event_date:
        try:
            d = datetime.strptime(event_date, "%Y-%m-%d")
            date_str = d.strftime("%Y%m%d")
        except ValueError:
            date_str = datetime.now().strftime("%Y%m%d")
    else:
        date_str = datetime.now().strftime("%Y%m%d")
    return f"T{date_str}-{sequence:05d}"

def build_credit_account(employee_name: str) -> str:
    """貸方科目を構築する。社員名が指定されていれば「未払費用（{employee_name}）」を返す。"""
    if employee_name:
        return f"{CREDIT_ACCOUNT_BASE}（{employee_name}）"
    return CREDIT_ACCOUNT_BASE

def build_journal_entry(*, ocr_result, employee_name: str, employee_slack_id: str, event_id: str, raw_text: str) -> JournalEntry:
    merchant = normalize_merchant_name(ocr_result.counterparty)
    debit = classify_account(raw_text, merchant)
    credit = build_credit_account(employee_name)
    event_date = ocr_result.event_date or datetime.now().strftime("%Y-%m-%d")
    return JournalEntry(
        event_id=event_id, event_date=event_date, counterparty=merchant,
        total_amount=ocr_result.total_amount, taxable_10_amount=ocr_result.taxable_10_amount,
        tax_10_amount=ocr_result.tax_10_amount, taxable_8_amount=ocr_result.taxable_8_amount,
        tax_8_amount=ocr_result.tax_8_amount, debit_account=debit, credit_account=credit,
        invoice_number=ocr_result.invoice_number, has_invoice=ocr_result.has_invoice,
        employee_name=employee_name,
        debit_subsidiary=getattr(ocr_result, "debit_subsidiary", ""),
        purpose=getattr(ocr_result, "purpose", ""),
    )

