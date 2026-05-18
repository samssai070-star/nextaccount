"""
NextAccount v2 — core/slack_oauth.py
Slack OAuth 2.0 Install Flow
"""
from __future__ import annotations
import os
import logging
import secrets
import time
import requests

logger = logging.getLogger(__name__)

_state_store: dict[str, dict] = {}
_STATE_TTL = 600


def _purge_expired():
    now = time.time()
    expired = [k for k, v in _state_store.items() if now - v["created_at"] > _STATE_TTL]
    for k in expired:
        del _state_store[k]


def generate_oauth_url(plan: str = "", redirect_uri: str = "") -> str:
    _purge_expired()

    client_id = os.environ.get("SLACK_CLIENT_ID", "")
    if not client_id:
        raise ValueError("SLACK_CLIENT_ID が設定されていません")

    state = secrets.token_urlsafe(32)
    _state_store[state] = {"plan": plan, "created_at": time.time()}

    scopes = ",".join([
        "channels:history", "channels:read", "chat:write", "commands",
        "files:read", "files:write", "groups:history", "im:history",
        "im:write", "mpim:history", "team:read", "users:read", "usergroups:read",
    ])

    params = {"client_id": client_id, "scope": scopes, "state": state}
    if redirect_uri:
        params["redirect_uri"] = redirect_uri

    query = "&".join(k + "=" + v for k, v in params.items())
    url = "https://slack.com/oauth/v2/authorize?" + query
    logger.info("OAuth URL生成: plan=%s state=%s...", plan, state[:8])
    return url


def exchange_code(code: str, state: str, redirect_uri: str = "") -> dict:
    _purge_expired()

    state_data = _state_store.pop(state, None)
    if state_data is None:
        logger.warning("不正なstate: %s...", state[:8])
        return {"ok": False, "error": "invalid_state"}

    if time.time() - state_data["created_at"] > _STATE_TTL:
        return {"ok": False, "error": "state_expired"}

    plan = state_data.get("plan", "micro")

    client_id     = os.environ.get("SLACK_CLIENT_ID", "")
    client_secret = os.environ.get("SLACK_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return {"ok": False, "error": "server_config_error"}

    payload = {"client_id": client_id, "client_secret": client_secret, "code": code}
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri

    resp = requests.post("https://slack.com/api/oauth.v2.access", data=payload, timeout=10)
    data = resp.json()

    if not data.get("ok"):
        err = data.get("error", "slack_error")
        logger.error("Slack OAuth失敗: %s", err)
        return {"ok": False, "error": err}

    team_id     = data.get("team", {}).get("id", "")
    team_name   = data.get("team", {}).get("name", "")
    bot_token   = data.get("access_token", "")
    bot_user_id = data.get("bot_user_id", "")

    logger.info("OAuth成功: team=%s(%s) plan=%s", team_name, team_id, plan)

    return {
        "ok": True,
        "team_id": team_id,
        "team_name": team_name,
        "bot_token": bot_token,
        "bot_user_id": bot_user_id,
        "plan": plan,
    }
