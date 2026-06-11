"""Slack OAuth Integration"""
from __future__ import annotations
import os, requests, logging
from flask import Blueprint, request, redirect
from .helpers import (
    get_db_connection, get_db_cursor, require_auth,
    success_response, error_response
)

logger = logging.getLogger(__name__)
slack_bp = Blueprint("slack", __name__, url_prefix="/api/slack")

SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET", "")
SLACK_REDIRECT_URI = os.environ.get("SLACK_REDIRECT_URI", "https://nextaccount.jp/api/slack/oauth/callback")

@slack_bp.route("/oauth/start", methods=["GET"])
@require_auth
def slack_oauth_start():
    """Slack OAuth フローを開始"""
    try:
        org_id = request.organization_id

        if not SLACK_CLIENT_ID:
            return error_response("Slack OAuth not configured"), 503

        # OAuth URLを生成 - core/slack_oauth.py と同じscopes配置を使用
        scopes = ",".join([
            "channels:history",
            "channels:read",
            "chat:write",
            "commands",
            "files:read",
            "files:write",
            "groups:history",
            "im:history",
            "im:write",
            "mpim:history",
            "team:read",
            "users:read",
            "usergroups:read"
        ])

        oauth_url = (
            f"https://slack.com/oauth/v2/authorize?"
            f"client_id={SLACK_CLIENT_ID}&"
            f"scope={scopes}&"
            f"redirect_uri={SLACK_REDIRECT_URI}&"
            f"state={org_id}"
        )

        return success_response({"oauth_url": oauth_url})

    except Exception as e:
        logger.error(f"OAuth start error: {e}")
        return error_response(str(e)), 500

@slack_bp.route("/oauth/callback", methods=["GET"])
def slack_oauth_callback():
    """Slack OAuth コールバック"""
    try:
        code = request.args.get("code")
        state = request.args.get("state")
        error = request.args.get("error")

        if error:
            logger.error(f"Slack OAuth error: {error}")
            return redirect(f"https://nextaccount.jp/setup.html?error=slack_{error}")

        if not code or not state:
            return redirect("https://nextaccount.jp/setup.html?error=missing_params")

        org_id = int(state)

        # トークンを交換
        response = requests.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": SLACK_CLIENT_ID,
                "client_secret": SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": SLACK_REDIRECT_URI
            }
        )

        data = response.json()

        if not data.get("ok"):
            error_msg = data.get("error", "unknown_error")
            logger.error(f"Slack token exchange failed: {error_msg}")
            return redirect(f"https://nextaccount.jp/setup.html?error=slack_{error_msg}")

        # Slack情報を抽出
        bot_token = data.get("access_token")
        bot_user_id = data.get("bot_user_id")
        team_id = data.get("team", {}).get("id")
        team_name = data.get("team", {}).get("name")

        if not all([bot_token, bot_user_id, team_id]):
            logger.error("Missing required Slack data")
            return redirect("https://nextaccount.jp/setup.html?error=missing_slack_data")

        # #経費申請 チャンネルを作成または取得
        logger.info(f"Ensuring expense channel for org {org_id}...")
        channel_id, channel_name = _ensure_expense_channel(bot_token)
        logger.info(f"Channel result: channel_id={channel_id}, channel_name={channel_name}")

        logger.info(f"Connecting to database...")
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # Slack Workspaceを保存
        logger.info(f"Inserting Slack workspace: org_id={org_id}, team_id={team_id}, bot_user_id={bot_user_id}")
        cur.execute(
            """INSERT INTO slack_workspaces
               (organization_id, workspace_id, workspace_name, bot_token, bot_user_id, channel_id, channel_name, is_connected)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (organization_id, workspace_id) DO UPDATE SET
               bot_token = EXCLUDED.bot_token,
               bot_user_id = EXCLUDED.bot_user_id,
               channel_id = EXCLUDED.channel_id,
               is_connected = true,
               connected_at = CURRENT_TIMESTAMP""",
            (org_id, team_id, team_name, bot_token, bot_user_id, channel_id, channel_name, True)
        )

        logger.info(f"Committing transaction...")
        conn.commit()
        conn.close()

        logger.info(f"Slack workspace {team_id} connected for org {org_id}")

        return redirect("https://nextaccount.jp/setup.html?step=4&success=true")

    except Exception as e:
        import traceback
        logger.error(f"OAuth callback error: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return redirect(f"https://nextaccount.jp/setup.html?error=callback_error")

@slack_bp.route("/workspace/info", methods=["GET"])
@require_auth
def get_workspace_info():
    """接続されたSlack Workspaceの情報を取得"""
    try:
        org_id = request.organization_id

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """SELECT workspace_id, workspace_name, bot_user_id, channel_name, is_connected
               FROM slack_workspaces WHERE organization_id=%s""",
            (org_id,)
        )
        workspace = cur.fetchone()
        conn.close()

        if not workspace:
            return error_response("No Slack workspace connected"), 404

        return success_response({
            "workspace_id": workspace["workspace_id"],
            "workspace_name": workspace["workspace_name"],
            "bot_user_id": workspace["bot_user_id"],
            "channel_name": workspace["channel_name"],
            "is_connected": workspace["is_connected"]
        })

    except Exception as e:
        logger.error(f"Get workspace info error: {e}")
        return error_response(str(e)), 500

def _ensure_expense_channel(bot_token: str) -> tuple[str, str]:
    """#経費申請 チャンネルを確認または作成"""
    try:
        # チャンネルリストを取得
        headers = {"Authorization": f"Bearer {bot_token}"}

        response = requests.get(
            "https://slack.com/api/conversations.list",
            headers=headers,
            params={"types": "public_channel"}
        )

        list_data = response.json()
        if not list_data.get("ok"):
            error_msg = list_data.get("error", "unknown_error")
            logger.warning(f"Failed to list channels: {error_msg}")
            return None, "#経費申請"

        channels = list_data.get("channels", [])
        for channel in channels:
            if channel.get("name") == "経費申請":
                channel_id = channel.get("id")
                logger.info(f"Found existing #経費申請 channel: {channel_id}")
                return channel_id, channel.get("name")

        # チャンネルが存在しない場合は作成
        logger.info("Creating #経費申請 channel...")
        response = requests.post(
            "https://slack.com/api/conversations.create",
            headers=headers,
            json={"name": "経費申請"}
        )

        create_data = response.json()
        if create_data.get("ok"):
            channel_id = create_data["channel"]["id"]
            logger.info(f"Created #経費申請 channel: {channel_id}")
            return channel_id, "経費申請"

        error_msg = create_data.get("error", "unknown_error")
        logger.warning(f"Failed to create #経費申請 channel: {error_msg}")
        return None, "#経費申請"

    except Exception as e:
        import traceback
        logger.error(f"Error ensuring expense channel: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None, "#経費申請"
