"""
NextAccount v2 — core/ai_classifier.py
Anthropic SDK で領収書OCRテキストから全項目を一括判定する。
プロンプトキャッシングにより、静的な分類ルールをキャッシュしてコスト・レイテンシを削減する。
"""

from __future__ import annotations
import os
import json
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """あなたは日本の経理・会計の専門家です。領収書・レシートのOCRテキストから全項目を正確に抽出・判定してください。

以下のJSON形式のみで回答してください（前後の説明・コードブロック不要）:
{
  "counterparty": "取引先名（店名・会社名。「領収書」「様」などは除く）",
  "event_date": "発生日（YYYY-MM-DD形式。不明な場合は今日の日付）",
  "total_amount": 実際に支払った税込合計金額（整数。「合計」「税込合計」「合計金額」「ご請求金額」「現金領収額」「お支払金額」に続く金額を使う。「お預り」「おつり」「お釣り」は絶対に含めない）,
  "taxable_10_amount": 税率10%の税抜本体金額（整数。「内消費税」「消費税」が明記されていれば total_amount - tax_10_amount で計算。明記なければ total_amount ÷ 1.1 を四捨五入）,
  "tax_10_amount": 消費税10%の金額（整数。「内消費税」「消費税10%」に続く金額。なければ total_amount - taxable_10_amount）,
  "taxable_8_amount": 税率8%の税抜本体金額（整数。なければ0）,
  "tax_8_amount": 消費税8%の金額（整数。なければ0）,
  "invoice_number": "登録番号・T番号（T+13桁。なければnull）",
  "debit_account": "勘定科目（科目名のみ）",
  "debit_subsidiary": "借方補助科目（具体的な費目。なければ空文字列）",
  "reason": "判定理由（一言）"
}

【最重要】内税（税込）方式の読み方:
- 「内消費税 ¥609」「(内消費税 ¥609)」は、合計金額に既に含まれている税額を示す。合計に加算してはいけない。
- 「合計 ¥6,700 / 内消費税 ¥609」の場合: total_amount=6700, tax_10_amount=609, taxable_10_amount=6091
- 各商品に「内」「税込」がついている場合（例: ¥350内）は税込表示であり、合計も税込。
- 「内消費税」は合計の内訳であり、合計 + 内消費税 = 二重計算になるので絶対にしない。

勘定科目の選択肢:
- 旅費交通費（電車・バス・タクシー・駐車場・宿泊・航空券）
- 通信費（電話・郵便・宅配・インターネット）
- 水道光熱費（電気・ガス・水道）
- 接待交際費（飲食店・レストラン・居酒屋・カフェでの接待）
- 会議費（会議室・打合せ時の飲食）
- 消耗品費（コンビニ・文具・日用品・EC）
- 広告宣伝費（広告・印刷・デザイン）
- 地代家賃（家賃・駐車場月極）
- 租税公課（税務署・国税・都税・市税・固定資産税・印紙税・源泉所得税・法人税・消費税納付）
- 社会保険料（健康保険・厚生年金・労働保険・雇用保険）
- 外注費（業務委託・フリーランス・委託料・外注）
- 修繕費（設備・機器の修理）
- 諸雑費（上記に該当しない）

その他のルール:
- T番号はT+13桁の数字（「登録番号」の後も対象）
- 取引先は店名・会社名のみ（住所・電話番号は含めない）
- 「お預り」「おつり」「釣銭」の金額は total_amount に絶対に使わない"""


def extract_all_by_claude(ocr_text: str) -> dict:
    """
    Anthropic SDK でOCRテキストから全項目を抽出・判定する。
    静的なシステムプロンプトをプロンプトキャッシングでキャッシュする。
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 未設定")
        return {}

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        today = datetime.now().strftime("%Y-%m-%d")
        user_content = f"今日の日付: {today}\n\nOCRテキスト:\n\"\"\"\n{ocr_text[:2000]}\n\"\"\""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )

        text = response.content[0].text.strip()
        logger.info(f"Claude応答: {text[:200]}")

        # キャッシュ使用状況をログ
        usage = response.usage
        if hasattr(usage, "cache_read_input_tokens") and usage.cache_read_input_tokens:
            logger.info(f"プロンプトキャッシュ: hit={usage.cache_read_input_tokens}tok")
        elif hasattr(usage, "cache_creation_input_tokens") and usage.cache_creation_input_tokens:
            logger.info(f"プロンプトキャッシュ: created={usage.cache_creation_input_tokens}tok")

        text = re.sub(r"```json|```", "", text).strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            logger.info(
                f"Claude判定完了: {data.get('counterparty')} → "
                f"{data.get('debit_account')} "
                f"¥{data.get('total_amount')} "
                f"({data.get('reason')})"
            )
            return data

    except json.JSONDecodeError as e:
        logger.error(f"JSON解析エラー: {e}")
    except Exception as e:
        logger.error(f"Claude API エラー: {e}")

    return {}


def classify(ocr_text: str, current_counterparty: str) -> dict:
    """
    Claude API で全項目を判定して返す。
    失敗時は空dictを返す（呼び出し元でOCR結果を使用）。
    """
    return extract_all_by_claude(ocr_text)
