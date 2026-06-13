"""
Multi-workspace installation store — reads bot tokens from slack_workspaces table
"""
from __future__ import annotations
import logging
from slack_sdk.oauth.installation_store import InstallationStore
from slack_sdk.oauth.installation_store.models.bot import Bot
from slack_sdk.oauth.installation_store.models.installation import Installation

logger = logging.getLogger(__name__)


class DBInstallationStore(InstallationStore):
    """Reads per-workspace bot tokens from the slack_workspaces table."""

    def save(self, installation: Installation):
        # OAuth callback in api/slack_oauth.py handles DB writes — no-op here
        pass

    def find_installation(
        self,
        *,
        enterprise_id: str | None,
        team_id: str | None,
        user_id: str | None = None,
        is_enterprise_install: bool = False,
    ) -> Installation | None:
        if not team_id:
            return None
        try:
            from api.helpers import get_db_connection, get_db_cursor
            conn = get_db_connection()
            cur = get_db_cursor(conn)
            cur.execute(
                """SELECT bot_token, bot_user_id, workspace_name
                   FROM slack_workspaces
                   WHERE workspace_id = %s AND is_connected = TRUE
                   ORDER BY connected_at DESC LIMIT 1""",
                (team_id,)
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                logger.warning(f"Installation not found: team_id={team_id}")
                return None
            return Installation(
                app_id="",
                enterprise_id=enterprise_id or "",
                team_id=team_id,
                team_name=row["workspace_name"] or "",
                bot_token=row["bot_token"],
                bot_id=row["bot_user_id"] or "",
                bot_user_id=row["bot_user_id"] or "",
            )
        except Exception as e:
            logger.error(f"find_installation error: {e}")
            return None

    def find_bot(
        self,
        *,
        enterprise_id: str | None,
        team_id: str | None,
        is_enterprise_install: bool = False,
    ) -> Bot | None:
        if not team_id:
            return None
        try:
            from api.helpers import get_db_connection, get_db_cursor
            conn = get_db_connection()
            cur = get_db_cursor(conn)
            cur.execute(
                """SELECT bot_token, bot_user_id
                   FROM slack_workspaces
                   WHERE workspace_id = %s AND is_connected = TRUE
                   ORDER BY connected_at DESC LIMIT 1""",
                (team_id,)
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                logger.warning(f"Bot not found: team_id={team_id}")
                return None
            return Bot(
                app_id="",
                enterprise_id=enterprise_id or "",
                team_id=team_id,
                bot_token=row["bot_token"],
                bot_id=row["bot_user_id"] or "",
                bot_user_id=row["bot_user_id"] or "",
            )
        except Exception as e:
            logger.error(f"find_bot error: {e}")
            return None
