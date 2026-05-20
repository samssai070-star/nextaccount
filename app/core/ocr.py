"""
NextAccount v2 — core/ocr.py
Google Cloud Vision を使用して領収書・請求書から全項目を抽出する。

抽出項目:
  - 税込合計金額
  - 税率10% 対象額 / 消費税額
  - 税率8%  対象額 / 消費税額（軽減税率）
  - T番号（適格請求書発行事業者登録番号）
  - 発生日
  - 取引先名
  - raw_text（デバッグ用）
"""

from __future__ import annotations

import re
import os
import logging
from datetime import date, datetime
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# データクラス
# ============================================================

@dataclass
class OcrResult:
    """OCR 抽出結果を格納するデータクラス"""
    raw_text: str = ""

    # 金額
    total_amount: int = 0            # 税込合計
    taxable_10_amount: int = 0       # 税率10%対象（本体）
    tax_10_amount: int = 0           # 消費税額（10%）
    taxable_8_amount: int = 0        # 税率8%対象（本体）
    tax_8_amount: int = 0            # 消費税額（8%）

    # インボイス情報
    invoice_number: Optional[str] = None   # T + 13桁
    has_invoice: bool = False

    # 日付・取引先
    event_date: Optional[str] = None       # YYYY-MM-DD
    counterparty: str = "不明"

    # OCR品質
    used_real_ocr: bool = False
    confidence: float = 0.0

    # AI分類結果
    debit_subsidiary: str = ""     # 借方補助科目
    purpose: str = ""              # 用途（DM入力）

    def to_dict(self) -> dict:
        return {
            "total_amount": self.total_amount,
            "taxable_10_amount": self.taxable_10_amount,
            "tax_10_amount": self.tax_10_amount,
            "taxable_8_amount": self.taxable_8_amount,
            "tax_8_amount": self.tax_8_amount,
            "invoice_number": self.invoice_number,
            "has_invoice": self.has_invoice,
            "event_date": self.event_date,
            "counterparty": self.counterparty,
        }


# ============================================================
# Google Cloud Vision OCR
# ============================================================

def _call_vision_api(image_bytes: bytes) -> str:
    """Google Cloud Vision API を呼び出してテキストを返す"""
    from google.cloud import vision  # type: ignore
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    response = client.text_detection(image=image)

    if response.error.message:
        raise RuntimeError(f"Vision API エラー: {response.error.message}")

    texts = response.text_annotations
    return texts[0].description if texts else ""


def perform_ocr(image_path: str) -> str:
    """
    画像ファイルパスを受け取り、OCR テキスト全文を返す。
    Vision API が使えない場合は RuntimeError を raise する。
    """
    with open(image_path, "rb") as f:
        content = f.read()
    return _call_vision_api(content)


def perform_ocr_from_bytes(image_bytes: bytes) -> str:
    """バイト列から直接 OCR を実行する"""
    return _call_vision_api(image_bytes)


# ============================================================
# テキスト解析
# ============================================================

def _clean_number(s: str) -> int:
    """'\''1,234'\'' や '\''¥1,234'\'' を int に変換する"""
    s = re.sub(r"[^\d]", "", s)
    return int(s) if s else 0


def extract_total_amount(text: str) -> int:
    """
    税込合計金額を抽出する。
    優先順位:
      1. 税込合計・合計金額など明示的なキーワードに続く金額
      2. お預り/おつり行を除外したうえで最大の ¥ 金額
    """
    # お預り・おつり行を含む行を事前除去
    cleaned_lines = []
    exclude_re = re.compile(
        r"(?:お預り|お釣り|おつり|釣り銭|お釣|預り|釣銭|チェンジ|CHANGE|CASH\s*TENDERED|CHANGE\s*DUE)",
        re.IGNORECASE,
    )
    for line in text.split("\n"):
        if not exclude_re.search(line):
            cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)

    # Priority 1: 合計・支払い金額を明示するキーワード（先頭マッチで即返す）
    priority_patterns = [
        r"(?:税込合計|合計金額|領収金額|ご請求金額|現金領収額|お支払い金額|お支払金額|請求金額|ご請求額)"
        r"\s*[：:￥¥]?\s*([\d,]+)",
        r"(?:合計|小計)\s*[：:￥¥]\s*([\d,]+)",   # ：または¥が必須（単なる「合計」後の改行対策）
        r"(?:合計|小計)\s+([\d,]+)",
    ]
    for pat in priority_patterns:
        m = re.search(pat, cleaned, re.IGNORECASE)
        if m:
            v = _clean_number(m.group(1))
            if v > 0:
                return v

    # Priority 2: お預り除去済みテキストから ¥ 金額を収集して最大値
    candidates: list[int] = []
    for pat in [r"[￥¥]\s*([\d,]+)", r"([\d,]+)\s*円"]:
        for m in re.finditer(pat, cleaned):
            v = _clean_number(m.group(1))
            if v > 0:
                candidates.append(v)

    return max(candidates) if candidates else 0


def extract_tax_breakdown(text: str) -> dict:
    """
    税率別の内訳を抽出する。
    返値: {taxable_10, tax_10, taxable_8, tax_8}
    """
    result = {"taxable_10": 0, "tax_10": 0, "taxable_8": 0, "tax_8": 0}

    # 10% 対象額
    for pat in [
        r"(?:10%対象|税率10%|10%課税)[^\d]*([\d,]+)",
        r"(?:標準税率対象)[^\d]*([\d,]+)",
    ]:
        m = re.search(pat, text)
        if m:
            result["taxable_10"] = _clean_number(m.group(1))
            break

    # 消費税 10%
    for pat in [
        r"(?:消費税10%|内税10%|税額10%|消費税\s*\(10%\))[^\d]*([\d,]+)",
        r"(?:内消費税等)[^\d]*([\d,]+)",
    ]:
        m = re.search(pat, text)
        if m:
            result["tax_10"] = _clean_number(m.group(1))
            break

    # 8% 対象額（軽減税率）
    for pat in [
        r"(?:8%対象|税率8%|8%課税|軽減税率対象)[^\d]*([\d,]+)",
    ]:
        m = re.search(pat, text)
        if m:
            result["taxable_8"] = _clean_number(m.group(1))
            break

    # 消費税 8%
    for pat in [
        r"(?:消費税8%|内税8%|税額8%|消費税\s*\(8%\))[^\d]*([\d,]+)",
    ]:
        m = re.search(pat, text)
        if m:
            result["tax_8"] = _clean_number(m.group(1))
            break

    # 内訳が取れなかった場合: 合計から内税10%を推算
    if result["taxable_10"] == 0 and result["tax_10"] == 0:
        total = extract_total_amount(text)
        if total > 0:
            taxable = round(total / 1.10)
            result["taxable_10"] = taxable
            result["tax_10"] = total - taxable
    
    # OCRで税額が直接読み取れた場合（「税率10 1,306」など）は上書き
    tax_direct = re.search(r"(?:税率10|消費税10%?|内消費税)[^\d]*([\d,]+)", text)
    if tax_direct:
        tax_val = int(tax_direct.group(1).replace(",", ""))
        if tax_val > 0:
            result["tax_10"] = tax_val
            result["taxable_10"] = extract_total_amount(text) - tax_val

    return result


def extract_invoice_number(text: str) -> Optional[str]:
    """T番号（T + 13桁の数字）を抽出する
    対応パターン:
      - T1234567890123
      - 登録番号 T1234567890123
      - T番号: T1234567890123
    """
    # Tで始まる13桁数字（前後に文字があってもOK）
    patterns = [
        r"T(\d{13})",
        r"(?:登録番号|T番号|インボイス番号)[^\d]*T(\d{13})",
        r"(?:登録番号|T番号)[^\d]*(\d{13})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return f"T{m.group(1)}"
    return None


def extract_date(text: str) -> Optional[str]:
    """
    発行日・発生日を YYYY-MM-DD 形式で返す。
    複数フォーマットに対応:
      2026/01/20, 2026-01-20, 令和8年1月20日, R8.1.20, 20260120
    """
    # 西暦スラッシュ / ハイフン
    m = re.search(r"(\d{4})[/\-年](\d{1,2})[/\-月](\d{1,2})日?", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 令和
    REIWA_BASE = 2018
    m = re.search(r"令和\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", text)
    if m:
        try:
            year = REIWA_BASE + int(m.group(1))
            return datetime(year, int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # R8.1.20
    m = re.search(r"R(\d{1,2})[\.\-/](\d{1,2})[\.\-/](\d{1,2})", text)
    if m:
        try:
            year = REIWA_BASE + int(m.group(1))
            return datetime(year, int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 8桁数字 YYYYMMDD
    m = re.search(r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def extract_counterparty(text: str) -> str:
    """
    取引先名を抽出する。
    ノイズ行を除外して店名・会社名を特定する。
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    noise = re.compile(
        r"^(?:領収書|領収証|請求書|明細書|レシート|receipt|invoice"
        r"|\d+|〒\d|TEL|FAX|tel|fax|様|御中"
        r"|\*+|={3,}|-{3,}"
        r"|ご利用|ありがとう|合計|小計|税込|税抜|お買|お支払"
        r"|年|月|日|時|分|秒"
        r"|No\.|番号|伝票|レシート番号)",
        re.IGNORECASE,
    )
    # アスタリスクや記号だけの行を除外
    symbol_only = re.compile(r"^[\*\-=\s　]+$")
    
    candidates = []
    for line in lines:
        if noise.match(line):
            continue
        if symbol_only.match(line):
            continue
        if len(line) < 2:
            continue
        # 日本語文字を含む行を優先
        if re.search(r"[\u3040-\u9fff]", line):
            candidates.append(line[:50])
    
    # 候補の中から店名らしい行を選ぶ
    # （「店」「屋」「社」「局」「院」などを含む行を優先）
    shop_pattern = re.compile(r"[店屋社局院館堂市場]")
    for c in candidates:
        if shop_pattern.search(c):
            return c
    
    return candidates[0] if candidates else "不明"


# ============================================================
# メインエントリポイント
# ============================================================

def parse_receipt(image_path: str) -> OcrResult:
    """
    画像ファイルを受け取り、OcrResult を返す。
    Vision API が使えない場合は used_real_ocr=False で空の結果を返す。
    """
    result = OcrResult()

    try:
        raw_text = perform_ocr(image_path)
        result.raw_text = raw_text
        result.used_real_ocr = True
        logger.info(f"OCR 完了: {len(raw_text)} 文字")
    except Exception as e:
        logger.error(f"OCR 失敗: {e}")
        result.used_real_ocr = False
        return result

    # 金額
    result.total_amount = extract_total_amount(raw_text)

    # 税率別内訳
    tax = extract_tax_breakdown(raw_text)
    result.taxable_10_amount = tax["taxable_10"]
    result.tax_10_amount     = tax["tax_10"]
    result.taxable_8_amount  = tax["taxable_8"]
    result.tax_8_amount      = tax["tax_8"]

    # T番号
    t_num = extract_invoice_number(raw_text)
    result.invoice_number = t_num
    result.has_invoice = t_num is not None

    # 日付
    result.event_date = extract_date(raw_text)

    # 取引先
    result.counterparty = extract_counterparty(raw_text)

    logger.info(
        f"解析結果 — 金額: ¥{result.total_amount:,} / "
        f"T番号: {result.invoice_number} / "
        f"日付: {result.event_date} / "
        f"取引先: {result.counterparty}"
    )
    return result

