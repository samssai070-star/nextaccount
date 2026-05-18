"""
NextAccount v2 — core/ai_classifier_v2.py
Claude API で借方補助科目まで自動識別する拡張版
"""

from __future__ import annotations
import os
import json
import logging
import re
import requests

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def extract_all_by_claude(ocr_text: str, employee_name: str = "") -> dict:
    """
    Claude API に領収書の全項目抽出・判定を依頼する（借方補助科目対応）。

    Returns:
        {
            "counterparty": str,
            "event_date": str (YYYY-MM-DD),
            "total_amount": int,
            "taxable_10_amount": int,
            "tax_10_amount": int,
            "taxable_8_amount": int,
            "tax_8_amount": int,
            "invoice_number": str or None,
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 未設定")
        return {}

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""以下は領収書・レシートのOCRテキストです。
全項目を正確に抽出・判定してください。

OCRテキスト:
\"\"\"
{ocr_text[:2000]}
\"\"\"

従業員情報（参考用）:
申請者: {employee_name if employee_name else "未設定"}

以下のJSON形式のみで回答してください（前後の説明・コードブロック不要）:
{{
  "counterparty": "取引先名（店名・会社名。「領収書」「様」などは除く）",
  "event_date": "発生日（YYYY-MM-DD形式。不明な場合は{today}）。令和→西暦変換: R1=2019,R2=2020,R3=2021,R4=2022,R5=2023,R6=2024,R7=2025,R8=2026。例:R7年8月13日→2025-08-13",
  "total_amount": 実際に支払った税込合計金額（整数。円記号・カンマ不要）,
  "taxable_10_amount": 税率10%対象の本体金額（整数）,
  "tax_10_amount": 消費税10%の金額（整数）,
  "taxable_8_amount": 税率8%対象の本体金額（整数。なければ0）,
  "tax_8_amount": 消費税8%の金額（整数。なければ0）,
  "invoice_number": "T番号（T+13桁。なければnull）",
  "debit_account": "勘定科目",
  "debit_subsidiary": "借方補助科目。旅費交通費の場合は宿泊費/駐車場代/高速料金/電車賃/タクシー代/ガソリン代など具体的に。消耗品費なら文具/オフィス用品など。その他の科目も可能な限り具体的に。推測不可なら空文字列を返す",
  "department": "推測部門（営業部・企画部・管理部など。領収書から推測）",
  "reason": "判定理由（一言）"
}}

勘定科目の選択肢:
- 旅費交通費
- 通信費
- 水道光熱費
- 接待交際費
- 会議費
- 消耗品費
- 広告宣伝費
- 地代家賃
- 租税公課
- 社会保険料
- 外注費（業務委託・フリーランス）
- 修繕費
- 諸雑費（その他）
- 福利厚生費

【国税庁タックスアンサー準拠の判定ルール】

■ 接待交際費（No.5265）
- 得意先・仕入先への飲食・贈答・娯楽
- 飲食店・レストラン・居酒屋・バー（取引先同席）
- 1人5,000円超の飲食は原則、接待交際費

■ 会議費（No.5265）
- 社内会議・打合せ時の飲食（1人5,000円以下が目安）
- 会議室レンタル料

■ 福利厚生費（No.5264）
- スーパーマーケット（イオン・西友・業務スーパー等）で購入した酒類・食品
- 社員向けお菓子・飲料・食事（接待でないもの）
- 慶弔見舞金、社員旅行、健康診断

■ 旅費交通費（No.5702）
- 電車・バス・タクシー・新幹線・航空券
- 駐車場（時間貸し）・高速道路料金
- 出張宿泊費・日当

■ 消耗品費（No.5461）
- 文具・事務用品・コピー用紙
- 10万円未満の少額備品
- コンビニの日用品（食品以外）

■ 地代家賃
- 月極駐車場（継続契約）
- 事務所・倉庫の家賃

■ 租税公課（No.5300）
- 印紙税・固定資産税・自動車税
- 税務署への納税額

■ 外注費
- フリーランス・業務委託への支払い
- 請負契約による作業費

■ インボイス制度（2023年10月〜）
- T+13桁の登録番号がある → 適格請求書（全額控除可）
- 登録番号なし → 経過措置で一部控除可（2026年9月まで80%）
- 数字のみ14桁（例:14010001137274）はT番号ではない → null

飲食・食品の判定ルール（重要）:
- 飲食店・レストラン・居酒屋・バー → 取引先との接待なら「接待交際費」、社内会議なら「会議費」
- コンビニの食品・飲料 → 少額で会議用なら「会議費」、それ以外は「消耗品費」
- 酒類を含む飲食店 → 「接待交際費」
- スーパーマーケット・業務スーパー・イオン・西友等で購入した酒類・食品 → 「福利厚生費」
- オフィス用の食品・お菓子・飲料（スーパー購入）→ 「福利厚生費」

借方補助科目の例（領収書のテキストから推測）:
【部門別】営業部、企画部、管理部、営業支援課、開発チーム
【従業員別】〇〇さん、△△部長、□□課長
【プロジェクト別】プロジェクトA、新規事業、拡張工事
【顧客別】〇〇会社向け、□□社向け
【地域別】東京営業所、大阪支店、名古屋営業所
【用途別】会議用、研修用、セミナー用

補助科目の判定ルール:
1. 領収書に「営業部」「企画課」などの部門名が含まれる → その部門を補助科目に
2. 領収書に「〇〇様」「△△さん」などの人名が含まれる → その人名を補助科目に
3. 領収書に「プロジェクトA」などのプロジェクト名が含まれる → そのプロジェクト名を補助科目に
4. 宛名が「〇〇会社」などの顧客名 → 「〇〇会社向け」を補助科目に
5. 勘定科目が「旅費交通費」の場合 → 費用種別を補助科目に:
   - ホテル・旅館・宿泊施設 → 「宿泊費」
   - 電車・バス・新幹線・航空券 → 「電車賃」
   - タクシー → 「タクシー代」
   - 駐車場（時間貸し）→ 「駐車場代」
   - 高速道路 → 「高速代」
6. 勘定科目が「水道光熱費」の場合 → 「電気代」「ガス代」「水道代」など
7. 領収書に明記がない場合 → 勘定科目から費用種別を推測

重要:
- 補助科目は日本語で30文字以内
- 推測できない場合は「（一般）」または「（未分類）」
- 勘定科目の判定ルール（租税公課・社会保険料・外注費など）は優先

重要ルール（共通）:
- T番号はT+13桁の数字
- 税額が明記されている場合はその値を使う
- 取引先は店名・会社名のみ"""

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        resp.raise_for_status()
        full_response = resp.json()
        # 保存完整响应到文件
        with open('/tmp/claude_response.json', 'w', encoding='utf-8') as f:
            json.dump(full_response, f, ensure_ascii=False, indent=2)
        logger.info("Claude完整響応已保存")
        text = full_response["content"][0]["text"].strip()

        #         text = re.sub(r"```json|```", "", text).strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            logger.info(
                f"Claude判定完了: {data.get('\''counterparty'\'')} → "
                f"{data.get('\''debit_account'\'')} / {data.get('\''debit_subsidiary'\'')} "
                f"¥{data.get('\''total_amount'\'')} "
                f"({data.get('\''reason'\'')})"
            )
            return data

    except json.JSONDecodeError as e:
        logger.error(f"JSON解析エラー: {e} / レスポンス: {text[:300]}")
    except Exception as e:
        logger.error(f"Claude API エラー: {e}")

    return {}


def classify(ocr_text: str, current_counterparty: str, employee_name: str = "") -> dict:
    """
    Claude API で全項目を判定して返す（借方補助科目対応）。
    失敗時は空dictを返す（呼び出し元でOCR結果を使用）。
    """
    return extract_all_by_claude(ocr_text, employee_name)

