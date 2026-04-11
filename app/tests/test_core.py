"""
NextAccount v2 — tests/test_core.py
OCR 解析・会計処理・仕訳生成の単体テスト
（Google API / DB への実際の接続は不要）
"""

import pytest
from unittest.mock import patch, MagicMock


# ============================================================
# OCR テキスト解析テスト
# ============================================================

class TestOcrParsing:

    def setup_method(self):
        # google.cloud を使わずに解析関数だけテスト
        import sys
        sys.modules.setdefault("google", MagicMock())
        sys.modules.setdefault("google.cloud", MagicMock())
        sys.modules.setdefault("google.cloud.vision", MagicMock())

    def test_extract_total_amount_yen_sign(self):
        from core.ocr import extract_total_amount
        text = "合計 ¥3,850\n税込"
        assert extract_total_amount(text) == 3850

    def test_extract_total_amount_kanji(self):
        from core.ocr import extract_total_amount
        text = "税込合計\n6,000円"
        assert extract_total_amount(text) == 6000

    def test_extract_invoice_number(self):
        from core.ocr import extract_invoice_number
        text = "登録番号 T4010001137274\n発行日"
        assert extract_invoice_number(text) == "T4010001137274"

    def test_extract_invoice_number_none(self):
        from core.ocr import extract_invoice_number
        text = "レシート\n合計 500円"
        assert extract_invoice_number(text) is None

    def test_extract_date_slash(self):
        from core.ocr import extract_date
        text = "発行日 2026/04/04"
        assert extract_date(text) == "2026-04-04"

    def test_extract_date_reiwa(self):
        from core.ocr import extract_date
        text = "令和8年4月4日"
        assert extract_date(text) == "2026-04-04"

    def test_extract_date_none(self):
        from core.ocr import extract_date
        text = "合計 1000円"
        assert extract_date(text) is None

    def test_tax_breakdown_10pct(self):
        from core.ocr import extract_tax_breakdown
        text = "10%対象 5,000\n消費税10% 500\n合計 5,500"
        result = extract_tax_breakdown(text)
        assert result["taxable_10"] == 5000
        assert result["tax_10"] == 500

    def test_extract_counterparty(self):
        from core.ocr import extract_counterparty
        text = "タイムズ24株式会社\n〒100-0001\n東京都..."
        assert "タイムズ" in extract_counterparty(text)


# ============================================================
# 勘定科目分類テスト
# ============================================================

class TestAccountingClassification:

    def test_classify_parking(self):
        from core.accounting import classify_account
        assert classify_account("タイムズ24", "タイムズ24株式会社") == "旅費交通費"

    def test_classify_postoffice(self):
        from core.accounting import classify_account
        assert classify_account("郵便局", "日本郵便株式会社") == "通信費"

    def test_classify_electricity(self):
        from core.accounting import classify_account
        assert classify_account("東京電力 電気料金", "東京電力") == "水道光熱費"

    def test_classify_convenience_store(self):
        from core.accounting import classify_account
        assert classify_account("セブンイレブン", "セブン-イレブン") == "消耗品費"

    def test_classify_restaurant(self):
        from core.accounting import classify_account
        assert classify_account("デニーズ", "デニーズ") == "接待交際費"

    def test_classify_unknown_defaults_to_shoumouhin(self):
        from core.accounting import classify_account
        assert classify_account("謎の商店", "謎の商店") == "消耗品費"

    def test_normalize_merchant_times(self):
        from core.accounting import normalize_merchant_name
        result = normalize_merchant_name("タイムズ 渋谷店")
        assert result == "タイムズ24株式会社"

    def test_normalize_merchant_unknown(self):
        from core.accounting import normalize_merchant_name
        result = normalize_merchant_name("個人タクシー")
        assert result == "個人タクシー"  # そのまま返す

    def test_build_credit_account(self):
        from core.accounting import build_credit_account
        assert build_credit_account("田中太郎") == "未払費用（田中太郎）"

    def test_generate_event_id(self):
        from core.accounting import generate_event_id
        eid = generate_event_id("2026-04-04", 3)
        assert eid == "T20260404-00003"

    def test_generate_event_id_no_date(self):
        from core.accounting import generate_event_id
        import re
        eid = generate_event_id(None, 1)
        assert re.match(r"T\d{8}-00001", eid)
