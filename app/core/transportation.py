"""
NextAccount v2 — core/transportation.py
交通費管理モジュール（定期券・申請・合規性チェック）
"""

from __future__ import annotations
import logging
from typing import Optional, Tuple, Dict

logger = logging.getLogger(__name__)


# ============================================================
# 駅情報マスタ（拡張予定：API連携）
# ============================================================

STATION_ALIASES = {
    "新宿": ["しんじゅく", "shinjuku"],
    "渋谷": ["しぶや", "shibuya"],
    "品川": ["しながわ", "shinagawa"],
    "東京": ["とうきょう", "tokyo"],
    "恵比寿": ["えびす", "ebisu"],
    "涩谷": ["しぶや", "shibuya"],  # 別字
}

def normalize_station_name(station_raw: str) -> str:
    """駅名を正規化（ひらがな・ローマ字 → 漢字）"""
    station_lower = station_raw.lower().strip()
    for canonical, aliases in STATION_ALIASES.items():
        if station_lower in aliases or station_lower == canonical.lower():
            return canonical
    return station_raw.strip()


# ============================================================
# 定期券管理関数
# ============================================================

def register_commute_ticket(
    slack_user_id: str,
    employee_name: str,
    tenant_id: str,
    from_station: str,
    to_station: str,
    monthly_price: int
) -> dict:
    """定期券を登録（DB保存）"""
    from core.database import upsert_commute_ticket, get_commute_ticket
    
    # 駅名を正規化
    from_station_norm = normalize_station_name(from_station)
    to_station_norm = normalize_station_name(to_station)
    
    # DB に保存
    ticket = upsert_commute_ticket(
        slack_user_id=slack_user_id,
        employee_name=employee_name,
        tenant_id=tenant_id,
        from_station=from_station_norm,
        to_station=to_station_norm,
        monthly_price=monthly_price
    )
    
    logger.info(f"定期券登録: {employee_name} ({from_station_norm}→{to_station_norm}) ¥{monthly_price}")
    return ticket or {}


def get_user_commute_ticket(slack_user_id: str, tenant_id: str) -> Optional[dict]:
    """ユーザーの定期券情報を取得"""
    from core.database import get_commute_ticket
    return get_commute_ticket(slack_user_id, tenant_id)


# ============================================================
# 経済合理性検証（参考最安値）
# ============================================================

REFERENCE_PRICES = {
    ("新宿", "渋谷"): 200,
    ("新宿", "品川"): 300,
    ("渋谷", "恵比寿"): 150,
    ("品川", "東京"): 250,
}

def get_reference_price(from_station: str, to_station: str) -> Optional[int]:
    """参考最安値を取得（maps_integration を使用）"""
    from core.maps_integration import get_reference_price as get_ref_from_maps
    result = get_ref_from_maps(from_station, to_station)
    return result["fare"] if result else None

def validate_transportation_price(
    from_station: str,
    to_station: str,
    declared_price: int,
    tolerance_pct: int = 10
) -> tuple[bool, str]:
    """
    申告金額が経済合理性の範囲内か検証
    
    Args:
        declared_price: ユーザーが申告した金額
        tolerance_pct: 許容範囲（%）
    
    Returns:
        (is_valid, message)
    """
    ref_price = get_reference_price(from_station, to_station)
    
    if ref_price is None:
        return True, "参考最安値なし（スキップ）"
    
    lower_bound = int(ref_price * (1 - tolerance_pct / 100))
    upper_bound = int(ref_price * (1 + tolerance_pct / 100))
    
    if lower_bound <= declared_price <= upper_bound:
        return True, f"OK: ¥{declared_price} (参考¥{ref_price}±{tolerance_pct}%)"
    else:
        return False, f"NG: ¥{declared_price} (参考¥{ref_price}、許容¥{lower_bound}-{upper_bound})"


# ============================================================
# 定期区間重複計算（最も重要）
# ============================================================

def calculate_overlap_deduction(
    commute_from: str,
    commute_to: str,
    claimed_from: str,
    claimed_to: str
) -> dict:
    """
    定期区間との重複を計算し、扣除額を算出
    
    例：
    - 定期: 新宿 → 渋谷（¥200）
    - 申請: 新宿 → 恵比寿（¥350）
    - 結果: 新宿 → 渋谷 の¥200を扣除、¥150のみ報告可能
    
    Returns:
        {
            "overlap_station_from": "新宿",
            "overlap_station_to": "渋谷",
            "overlap_amount": 200,
            "final_reimbursement": 150,
            "is_overlapped": True,
            "message": "定期券区間が重複します"
        }
    """
    commute_from = normalize_station_name(commute_from)
    commute_to = normalize_station_name(commute_to)
    claimed_from = normalize_station_name(claimed_from)
    claimed_to = normalize_station_name(claimed_to)
    
    # 簡易ロジック（本来は線路検証が必要）
    # 共通の出発駅と異なる到着駅の場合
    if commute_from == claimed_from and commute_to != claimed_to:
        # 定期区間との重複あり
        overlap_price = get_reference_price(commute_from, commute_to) or 0
        claimed_price = get_reference_price(claimed_from, claimed_to)
        
        if claimed_price is None:
            return {
                "overlap_station_from": commute_from,
                "overlap_station_to": commute_to,
                "overlap_amount": overlap_price,
                "final_reimbursement": None,  # 計算不可
                "is_overlapped": True,
                "message": "定期区間が重複しますが、先着の金額が計算できません"
            }
        
        final = max(0, claimed_price - overlap_price)
        return {
            "overlap_station_from": commute_from,
            "overlap_station_to": commute_to,
            "overlap_amount": overlap_price,
            "final_reimbursement": final,
            "is_overlapped": True,
            "message": f"定期区間({commute_from}→{commute_to})¥{overlap_price}を扣除 → 報告可¥{final}"
        }
    
    # 重複なし
    return {
        "overlap_amount": 0,
        "final_reimbursement": get_reference_price(claimed_from, claimed_to),
        "is_overlapped": False,
        "message": "定期区間との重複なし"
    }


# ============================================================
# 非課税限額管理
# ============================================================

NONTAXABLE_LIMIT = 150000  # 月額150,000円

def calculate_nontaxable_transportation(
    monthly_commute_price: int,
    additional_transportation_claims: list[int]
) -> dict:
    """
    月次の交通費（定期+実報実销）の非課税額を計算
    
    Returns:
        {
            "monthly_commute": 月次定期料金,
            "additional_total": 実報実销合計,
            "total": 合計,
            "nontaxable_amount": 非課税額,
            "taxable_amount": 課税対象額,
            "status": "OK" | "OVER_LIMIT"
        }
    """
    additional_total = sum(additional_transportation_claims)
    total = monthly_commute_price + additional_total
    
    if total <= NONTAXABLE_LIMIT:
        return {
            "monthly_commute": monthly_commute_price,
            "additional_total": additional_total,
            "total": total,
            "nontaxable_amount": total,
            "taxable_amount": 0,
            "status": "OK"
        }
    else:
        return {
            "monthly_commute": monthly_commute_price,
            "additional_total": additional_total,
            "total": total,
            "nontaxable_amount": NONTAXABLE_LIMIT,
            "taxable_amount": total - NONTAXABLE_LIMIT,
            "status": "OVER_LIMIT"
        }

