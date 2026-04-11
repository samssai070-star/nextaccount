
from __future__ import annotations
import hashlib, logging, os, secrets
from datetime import datetime, timezone
from typing import Optional
import requests

logger = logging.getLogger(__name__)

TSA_CONFIGS = {
    "freetsa": {"url": "https://freetsa.org/tsr", "name": "FreeTSA（開発用・非認定）", "certified": False},
    "seiko":   {"url": os.environ.get("SEIKO_TSA_URL", ""), "name": "セイコートラスト", "certified": True},
    "amano":   {"url": os.environ.get("AMANO_TSA_URL", ""), "name": "アマノ", "certified": True},
}
TSA_MODE = os.environ.get("TSA_MODE", "freetsa")

def _encode_length(n):
    if n < 0x80: return bytes([n])
    elif n < 0x100: return bytes([0x81, n])
    else: return bytes([0x82, (n>>8)&0xff, n&0xff])

def _build_tsq(digest):
    oid = bytes([0x30,0x0d,0x06,0x09,0x60,0x86,0x48,0x01,0x65,0x03,0x04,0x02,0x01,0x05,0x00])
    mi = bytes([0x30]) + _encode_length(len(oid)+2+len(digest)) + oid + bytes([0x04]) + _encode_length(len(digest)) + digest
    nonce = bytes([0x02,0x08]) + secrets.token_bytes(8)
    body = bytes([0x02,0x01,0x01]) + mi + nonce + bytes([0x01,0x01,0xff])
    return bytes([0x30]) + _encode_length(len(body)) + body

def apply_timestamp(file_bytes):
    cfg = TSA_CONFIGS.get(TSA_MODE, TSA_CONFIGS["freetsa"])
    try:
        digest = hashlib.sha256(file_bytes).digest()
        resp = requests.post(cfg["url"], data=_build_tsq(digest),
            headers={"Content-Type": "application/timestamp-query"}, timeout=10)
        resp.raise_for_status()
        logger.info(f"タイムスタンプ取得完了: {cfg['name']} {len(resp.content)}bytes")
        return {"token": resp.content, "timestamp_at": datetime.now(timezone.utc),
                "tsa_name": cfg["name"], "certified": cfg["certified"],
                "hash": hashlib.sha256(file_bytes).hexdigest()}
    except Exception as e:
        logger.warning(f"タイムスタンプ取得失敗: {e}")
        return None

def save_timestamp_to_db(event_id, tenant_id, ts_result):
    try:
        from core.database import _get_conn
        with _get_conn(tenant_id) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE accounting_events SET timestamp_token=%s, timestamp_at=%s, timestamp_verified=TRUE, updated_at=NOW() WHERE event_id=%s AND tenant_id=%s",
                    (ts_result["token"], ts_result["timestamp_at"], event_id, tenant_id))
        return True
    except Exception as e:
        logger.error(f"タイムスタンプDB保存失敗: {e}")
        return False

def verify_timestamp(file_bytes, token):
    try:
        match = hashlib.sha256(file_bytes).digest() in token
        return {"valid": match, "hash_match": match, "message": "検証成功" if match else "ハッシュ不一致"}
    except Exception as e:
        return {"valid": False, "hash_match": False, "message": str(e)}

def get_timestamp_badge(event):
    if event.get("timestamp_verified") and event.get("timestamp_at"):
        ts = event["timestamp_at"]
        ts_str = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)[:16]
        certified = TSA_CONFIGS.get(TSA_MODE, {}).get("certified", False)
        return ("🔒 タイムスタンプ付与済（総務大臣認定）\n" if certified else "🔑 タイムスタンプ付与済（開発用）\n") + ts_str + " UTC"
    return "⏳ タイムスタンプ未付与"
