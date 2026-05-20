"""一回限り: samssai_202510 シートのK列（借方補助科目）を補完する"""
import os, re, sys

env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

SPREADSHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
CREDS_PATH     = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/google_key.json")
TARGET_SHEET   = "samssai_202510"

if not SPREADSHEET_ID:
    print("ERROR: GOOGLE_SHEET_ID 未設定"); sys.exit(1)

from google.oauth2 import service_account
from googleapiclient.discovery import build

creds  = service_account.Credentials.from_service_account_file(
    CREDS_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
svc    = build("sheets", "v4", credentials=creds, cache_discovery=False)
sheets = svc.spreadsheets()


def infer_subsidiary(debit_account: str, counterparty: str) -> str:
    cp = counterparty.lower()

    if debit_account == "旅費交通費":
        if any(k in cp for k in ["タクシー", "taxi", "go株式会社", "km", "国際自動車", "日本交通", "didi", "uber"]):
            return "タクシー代"
        if any(k in cp for k in ["タイムズ", "パーク24", "times", "コインパーク", "三井リパーク", "駐車", "パーキング", "パーク", "parking"]):
            return "駐車場代"
        if any(k in cp for k in ["ホテル", "イン", "アパ", "コンフォート", "宿泊"]):
            return "宿泊費"
        if any(k in cp for k in ["航空", "ana", "jal", "peach", "skymark", "エアライン"]):
            return "航空券"
        if any(k in cp for k in ["eneos", "ガソリン", "ss", "スタンド", "apollo", "エネオス", "出光", "コスモ", "昭和シェル", "ジェネオス", "ホクレン"]):
            return "ガソリン代"
        if any(k in cp for k in ["ビーチライン", "道路", "有料", "高速"]):
            return "駐車場代"  # 有料道路は駐車場代に準じる
        if any(k in cp for k in ["バス"]):
            return "バス代"
        return "電車賃"

    if debit_account == "通信費":
        if any(k in cp for k in ["ヤマト", "佐川", "日本郵便", "ゆうパック", "クロネコ"]):
            return "郵便・宅配"
        if any(k in cp for k in ["ドコモ", "docomo", "au", "kddi", "softbank", "ソフトバンク", "楽天モバイル"]):
            return "電話代"
        return "インターネット"

    if debit_account == "水道光熱費":
        if any(k in cp for k in ["電力", "東京電力", "関西電力", "中部電力"]):
            return "電気代"
        if any(k in cp for k in ["ガス", "東京ガス", "大阪ガス"]):
            return "ガス代"
        if any(k in cp for k in ["水道"]):
            return "水道代"
        return "電気代"

    if debit_account == "接待交際費":
        return "接待飲食費"

    if debit_account == "会議費":
        return "会議飲食費"

    if debit_account == "消耗品費":
        if any(k in cp for k in ["アマゾン", "amazon", "ヨドバシ", "ビックカメラ", "pc", "パソコン"]):
            return "PC周辺機器"
        if any(k in cp for k in ["コクヨ", "アスクル", "文具"]):
            return "文具・事務用品"
        if any(k in cp for k in ["セブン", "ローソン", "ファミ", "ミニストップ", "コンビニ", "無印"]):
            return "日用品"
        return "その他消耗品"

    if debit_account == "広告宣伝費":
        if any(k in cp for k in ["印刷", "キンコーズ"]):
            return "印刷費"
        if any(k in cp for k in ["デザイン"]):
            return "デザイン費"
        return "広告費"

    if debit_account == "地代家賃":
        if "駐車" in cp:
            return "駐車場月極"
        return "事務所家賃"

    if debit_account == "租税公課":
        if "固定資産" in cp:
            return "固定資産税"
        if "印紙" in cp:
            return "印紙税"
        if "源泉" in cp:
            return "源泉所得税"
        return "消費税"

    if debit_account == "社会保険料":
        if "年金" in cp:
            return "厚生年金"
        if "雇用" in cp:
            return "雇用保険"
        return "健康保険料"

    if debit_account == "外注費":
        return "業務委託費"

    if debit_account == "福利厚生費":
        if "健康診断" in cp or "検診" in cp:
            return "健康診断費"
        return "社員食事補助"

    if debit_account == "修繕費":
        return "設備修繕費"

    if debit_account == "諸雑費":
        return "諸雑費"

    return ""


# シート読み込み（A〜K列）
rows = (
    sheets.values()
    .get(spreadsheetId=SPREADSHEET_ID, range=f"'{TARGET_SHEET}'!A:K")
    .execute()
    .get("values", [])
)

if not rows:
    print("データなし"); sys.exit(0)

updates = []  # (row_index_1based, new_k_value)

for i, row in enumerate(rows):
    if i == 0:
        continue  # ヘッダー行スキップ

    # J列 = index 9、K列 = index 10
    debit_account = row[9].strip() if len(row) > 9 else ""
    k_value       = row[10].strip() if len(row) > 10 else ""
    counterparty  = row[2].strip() if len(row) > 2 else ""

    if not debit_account:
        continue  # 借方科目が空なら対象外

    if k_value and k_value != "'":
        continue  # 既に値あり → スキップ（シングルクォートのみは空扱い）

    new_k = infer_subsidiary(debit_account, counterparty)
    if new_k:
        updates.append((i + 1, new_k))
        print(f"  行{i+1}: {counterparty} / {debit_account} → 補助: {new_k}")

if not updates:
    print("補完が必要な行はありません")
    sys.exit(0)

print(f"\n合計 {len(updates)} 行を更新します...")

# バッチ更新
data = []
for row_idx, k_val in updates:
    data.append({
        "range": f"'{TARGET_SHEET}'!K{row_idx}",
        "values": [[k_val]],
    })

sheets.values().batchUpdate(
    spreadsheetId=SPREADSHEET_ID,
    body={"valueInputOption": "USER_ENTERED", "data": data},
).execute()

print(f"完了: {len(updates)} 件のK列を補完しました")
