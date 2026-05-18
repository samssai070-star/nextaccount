"""
NextAccount v2 — core/maps_integration.py
Google Maps / Navitime API ハイブリッド統合
Phase 1: Google Maps API + Local Master
Phase 2: Navitime API（6ヶ月後）
"""

from __future__ import annotations
import logging
import os
import requests
from typing import Optional, Dict

logger = logging.getLogger(__name__)

GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
GOOGLE_MAPS_API_URL = "https://maps.googleapis.com/maps/api/directions/json"
NAVITIME_API_KEY = os.environ.get("NAVITIME_API_KEY", "")


# ============================================================
# ローカル参考最安値マスタ（Phase 1）
# ============================================================

REFERENCE_PRICES_MASTER = {
    ("新宿", "渋谷"): {"fare": 200, "duration": 15, "source": "local_master"},
    ("新宿", "品川"): {"fare": 300, "duration": 20, "source": "local_master"},
    ("新宿", "東京"): {"fare": 250, "duration": 10, "source": "local_master"},
    ("新宿", "恵比寿"): {"fare": 250, "duration": 20, "source": "local_master"},
    ("渋谷", "恵比寿"): {"fare": 150, "duration": 10, "source": "local_master"},
    ("渋谷", "品川"): {"fare": 250, "duration": 25, "source": "local_master"},
    ("品川", "東京"): {"fare": 250, "duration": 15, "source": "local_master"},
    ("品川", "新横浜"): {"fare": 400, "duration": 30, "source": "local_master"},
}


# ============================================================
# Google Maps API（フォールバック用）
# ============================================================

def get_route_via_google_maps(from_station: str, to_station: str) -> Optional[Dict]:
    """Google Maps API で公共交通ルートを検索"""
    if not GOOGLE_MAPS_API_KEY:
        logger.warning("GOOGLE_MAPS_API_KEY not configured")
        return None
    
    try:
        params = {
            "origin": from_station,
            "destination": to_station,
            "mode": "transit",
            "key": GOOGLE_MAPS_API_KEY,
            "language": "ja"
        }
        
        response = requests.get(GOOGLE_MAPS_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        status = data.get("status")
        if status != "OK":
            logger.error(f"Google Maps API returned: {status}")
            return None
        
        route = data["routes"][0]
        leg = route["legs"][0]
        
        distance_km = leg["distance"]["value"] / 1000
        duration_min = leg["duration"]["value"] / 60
        
        result = {
            "distance_km": distance_km,
            "duration_minutes": duration_min,
            "fare_yen": None,
            "source": "Google Maps",
            "steps": len(leg.get("steps", []))
        }
        
        logger.info(f"Google Maps route found: {from_station} to {to_station}")
        return result
        
    except Exception as e:
        logger.error(f"Google Maps API error: {e}")
        return None


# ============================================================
# 参考最安値取得（ハイブリッド）
# ============================================================

def get_reference_price(from_station: str, to_station: str, use_google_maps: bool = True) -> Optional[Dict]:
    """
    参考最安値を取得
    
    優先順位：
    1. ローカルマスタ
    2. Google Maps API（フォールバック）
    3. Navitime API（Phase 2）
    """
    from core.transportation import normalize_station_name
    
    from_norm = normalize_station_name(from_station)
    to_norm = normalize_station_name(to_station)
    key = (from_norm, to_norm)
    
    # ローカルマスタを優先
    if key in REFERENCE_PRICES_MASTER:
        data = REFERENCE_PRICES_MASTER[key]
        logger.info(f"Reference price from local master: {from_norm} to {to_norm}")
        return data
    
    # Google Maps フォールバック
    if use_google_maps:
        gmaps_result = get_route_via_google_maps(from_norm, to_norm)
        if gmaps_result:
            estimated_fare = int(gmaps_result["distance_km"] * 50 + 100)
            result = {
                "fare": estimated_fare,
                "duration": gmaps_result["duration_minutes"],
                "source": "Google Maps (estimated)",
                "distance_km": gmaps_result["distance_km"],
            }
            logger.info(f"Reference price from Google Maps: {from_norm} to {to_norm}")
            return result
    
    logger.warning(f"No reference price found: {from_norm} to {to_norm}")
    return None


def update_reference_price_master(from_station: str, to_station: str, fare: int, duration: int = None):
    """参考最安値マスタを更新"""
    from core.transportation import normalize_station_name
    
    from_norm = normalize_station_name(from_station)
    to_norm = normalize_station_name(to_station)
    key = (from_norm, to_norm)
    
    REFERENCE_PRICES_MASTER[key] = {
        "fare": fare,
        "duration": duration,
        "source": "manual_update",
    }
    
    log_msg = f"Reference price updated: {from_norm} to {to_norm} = {fare} yen"
    logger.info(log_msg)


# ============================================================
# Phase 2 プレースホルダー（6ヶ月後）
# ============================================================

def get_route_via_navitime(from_station: str, to_station: str) -> Optional[Dict]:
    """Navitime API（Phase 2 実装予定）"""
    if not NAVITIME_API_KEY:
        logger.debug("NAVITIME_API_KEY not set")
        return None
    
    logger.info("Navitime integration scheduled for Phase 2")
    return None


# ============================================================
# API ヘルスチェック
# ============================================================

def check_api_availability() -> Dict[str, bool]:
    """利用可能な API をチェック"""
    return {
        "local_master": True,
        "google_maps": bool(GOOGLE_MAPS_API_KEY),
        "navitime": bool(NAVITIME_API_KEY),
    }

