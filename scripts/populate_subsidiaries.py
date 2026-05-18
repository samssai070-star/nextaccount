#!/usr/bin/env python3
import sys, logging
sys.path.insert(0, '/app')
from core.database import _get_conn
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def infer_subsidiary(debit_account, counterparty, memo=""):
    """勘定科目と取引先から借方補助科目を推定する"""
    text = (debit_account + counterparty + memo).lower()
    
    # 旅費交通費の補助科目
    if debit_account == "旅費交通費":
        if any(w in text for w in ["電車","鉄道","train","jr","metro","駅","line","運輸","交通局"]):
            return "電車賃"
        if any(w in text for w in ["タクシー","taxi","uber","タクシープラス"]):
            return "タクシー代"
        if any(w in text for w in ["駐車","parking","times","パーキング","時間","24"]):
            return "駐車場代"
        if any(w in text for w in ["宿泊","hotel","inn","ryokan","宿","ホテル","旅館"]):
            return "宿泊費"
        if any(w in text for w in ["飛行","flight","air","航空","airport","ana","jal"]):
            return "航空券"
        if any(w in text for w in ["ガソリン","gas","fuel","給油","eneos","昭和シェル"]):
            return "ガソリン代"
        if any(w in text for w in ["高速","expressway","toll","highway"]):
            return "高速道路料金"
        # デフォルト：旅費交通費は電車賃（最も一般的）
        return "電車賃"

    # 接待交際費の補助科目
    if debit_account == "接待交際費":
        if any(w in text for w in ["飲食","食事","dining","meal","restaurant","bar","居酒屋","焼肉","寿司","和食","洋食","カフェ","カレー"]):
            return "接待飲食費"
        if any(w in text for w in ["手土産","gift","みやげ"]):
            return "手土産"
        if any(w in text for w in ["ゴルフ","golf"]):
            return "ゴルフ接待費"
        return "接待飲食費"

    # 会議費の補助科目
    if debit_account == "会議費":
        if any(w in text for w in ["飲食","食事","dining","meal","restaurant"]):
            return "会議飲食費"
        return ""

    # 消耗品費の補助科目
    if debit_account == "消耗品費":
        if any(w in text for w in ["文具","paper","pen","stationery","office"]):
            return "文具・事務用品"
        if any(w in text for w in ["日用品"]):
            return "日用品"
        if any(w in text for w in ["食料","food","groceries","食品"]):
            return "食料品"
        return ""

    return ""

def main():
    logger.info("=== 借方補助科目 一括登録開始 ===")
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT event_id, debit_account, counterparty, memo, tenant_id FROM accounting_events WHERE debit_subsidiary IS NULL OR debit_subsidiary = '' ORDER BY event_date DESC")
            rows = cur.fetchall()
    
    logger.info(f"処理対象: {len(rows)}件")
    updated = 0
    for i, row in enumerate(rows, 1):
        event_id = row["event_id"]
        debit_account = row["debit_account"] or ""
        counterparty = row["counterparty"] or ""
        memo = row["memo"] or ""
        tenant_id = row["tenant_id"]
        
        subsidiary = infer_subsidiary(debit_account, counterparty, memo)
        
        if subsidiary:
            with _get_conn(tenant_id) as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE accounting_events SET debit_subsidiary = %s WHERE event_id = %s AND tenant_id = %s", (subsidiary, event_id, tenant_id))
            updated += 1
            logger.info(f"[{i:3d}] {event_id} → {debit_account} / {subsidiary}")
        else:
            logger.info(f"[{i:3d}] {event_id} → {debit_account} （補助科目なし）")
    
    logger.info("")
    logger.info("=== 完了 ===")
    logger.info(f"総件数: {len(rows)}件")
    logger.info(f"更新件数: {updated}件")

if __name__ == "__main__":
    main()
