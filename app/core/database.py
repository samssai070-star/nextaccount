"""
NextAccount v2 — core/database.py  (マルチテナント対応版)
"""
from __future__ import annotations
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Optional
import psycopg2
import psycopg2.extras
from .config import DATABASE_URL

logger = logging.getLogger(__name__)

@contextmanager
def _get_conn(tenant_id=None):
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL が設定されていません")
    conn = psycopg2.connect(DATABASE_URL)
    try:
        if tenant_id:
            with conn.cursor() as cur:
                cur.execute("SET app.tenant_id = %s", (str(tenant_id),))
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

DDL = """
CREATE TABLE IF NOT EXISTS accounting_events (
    event_id            VARCHAR(30)  PRIMARY KEY,
    event_date          DATE         NOT NULL,
    counterparty        VARCHAR(200) NOT NULL,
    amount              INTEGER      NOT NULL,
    taxable_10_amount   INTEGER      DEFAULT 0,
    tax_10_amount       INTEGER      DEFAULT 0,
    taxable_8_amount    INTEGER      DEFAULT 0,
    tax_8_amount        INTEGER      DEFAULT 0,
    debit_account       VARCHAR(100) NOT NULL,
    debit_subsidiary    VARCHAR(100) DEFAULT '',
    credit_account      VARCHAR(100) NOT NULL,
    invoice_number      VARCHAR(20),
    has_invoice         BOOLEAN      DEFAULT FALSE,
    employee_name       VARCHAR(100) DEFAULT '',
    employee_slack_id   VARCHAR(50)  DEFAULT '',
    status              VARCHAR(50)  DEFAULT '申請中',
    evidence_url        TEXT         DEFAULT '',
    memo                TEXT         DEFAULT '',
    purpose             TEXT         DEFAULT '',
    source_type         VARCHAR(50)  DEFAULT 'expense',
    approved_by         VARCHAR(100),
    approved_at         TIMESTAMP,
    created_at          TIMESTAMP    DEFAULT NOW(),
    updated_at          TIMESTAMP    DEFAULT NOW(),
    tenant_id           UUID         REFERENCES tenants(id)
);
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS debit_subsidiary VARCHAR(100) DEFAULT '';
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS purpose TEXT DEFAULT '';
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS timestamp_token TEXT DEFAULT NULL;
ALTER TABLE accounting_events ALTER COLUMN timestamp_token TYPE TEXT;
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS timestamp_at TIMESTAMP;
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS timestamp_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS approval_card_channel VARCHAR(50) DEFAULT '';
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS approval_card_ts VARCHAR(50) DEFAULT '';
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS uploader_dm_channel VARCHAR(50) DEFAULT '';
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS uploader_dm_ts VARCHAR(50) DEFAULT '';
CREATE TABLE IF NOT EXISTS employee_monthly_codes (
    id          SERIAL PRIMARY KEY,
    tenant_id   UUID         REFERENCES tenants(id),
    slack_user_id VARCHAR(50) NOT NULL,
    year_month  VARCHAR(7)   NOT NULL,
    employee_code INTEGER     NOT NULL CHECK (employee_code BETWEEN 1 AND 99),
    UNIQUE(tenant_id, year_month, slack_user_id),
    UNIQUE(tenant_id, year_month, employee_code)
);
CREATE INDEX IF NOT EXISTS idx_emp_codes_tenant ON employee_monthly_codes(tenant_id, year_month);
CREATE TABLE IF NOT EXISTS tenant_sequences (
    tenant_id   UUID        NOT NULL,
    seq_key     VARCHAR(60) NOT NULL,
    current_val INTEGER     NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, seq_key)
);
CREATE INDEX IF NOT EXISTS idx_ae_invoice   ON accounting_events(invoice_number);
CREATE INDEX IF NOT EXISTS idx_ae_date      ON accounting_events(event_date);
CREATE INDEX IF NOT EXISTS idx_ae_status    ON accounting_events(status);
CREATE INDEX IF NOT EXISTS idx_ae_employee  ON accounting_events(employee_name);
CREATE INDEX IF NOT EXISTS idx_ae_created   ON accounting_events(created_at);
CREATE INDEX IF NOT EXISTS idx_ae_tenant_id ON accounting_events(tenant_id);
"""

def init_database():
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                cur.execute(USERS_DDL)
#         # init_users_table()
        logger.info("データベース初期化完了")
    except Exception as e:
        logger.error(f"データベース初期化失敗: {e}")
        raise

def get_tenant_by_slack_team(slack_team_id):
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE slack_team_id = %s AND is_active = TRUE", (slack_team_id,))
            row = cur.fetchone()
    return dict(row) if row else None

def create_tenant(slack_team_id, slack_bot_token, google_sheet_id=None):
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO tenants (slack_team_id, slack_bot_token, google_sheet_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (slack_team_id) DO UPDATE
                  SET slack_bot_token = EXCLUDED.slack_bot_token, is_active = TRUE
                RETURNING *
            """, (slack_team_id, slack_bot_token, google_sheet_id))
            row = cur.fetchone()
    logger.info(f"テナント作成/更新: {slack_team_id}")
    return dict(row)

def update_tenant_sheet(tenant_id, google_sheet_id):
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE tenants SET google_sheet_id = %s WHERE id = %s", (google_sheet_id, tenant_id))

def update_tenant_billing(tenant_id: str, **kwargs) -> None:
    """テナントの課金情報を更新する"""
    allowed = {"stripe_customer_id", "stripe_subscription_id", "stripe_price_id",
               "billing_status", "trial_ends_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %({k})s" for k in fields)
    fields["tenant_id"] = tenant_id
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE tenants SET {set_clause}, updated_at=NOW() WHERE id=%(tenant_id)s",
                fields
            )
    logger.info(f"テナント課金情報更新: {tenant_id} fields={list(fields.keys())}")

def _atomic_next_seq(conn, tenant_id: str, seq_key: str) -> int:
    """tenant_sequencesを使ったアトミックな採番（同時アクセス安全）"""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO tenant_sequences (tenant_id, seq_key, current_val)
            VALUES (%s, %s, 1)
            ON CONFLICT (tenant_id, seq_key) DO UPDATE
                SET current_val = tenant_sequences.current_val + 1
            RETURNING current_val
        """, (tenant_id, seq_key))
        return cur.fetchone()[0]

def get_or_assign_employee_code(slack_user_id: str, tenant_id: str, year_month: str) -> int:
    """当月の社員コード（01-99）を取得または新規割当する（同時アクセス安全）"""
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            # 既存コードを確認
            cur.execute(
                "SELECT employee_code FROM employee_monthly_codes WHERE tenant_id=%s AND year_month=%s AND slack_user_id=%s",
                (tenant_id, year_month, slack_user_id)
            )
            row = cur.fetchone()
            if row:
                return row[0]
            # アトミックに次のコードを採番
            new_code = _atomic_next_seq(conn, tenant_id, f"emp_code:{year_month}")
            if new_code > 99:
                new_code = 99
            cur.execute("""
                INSERT INTO employee_monthly_codes (tenant_id, slack_user_id, year_month, employee_code)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tenant_id, year_month, slack_user_id) DO NOTHING
                RETURNING employee_code
            """, (tenant_id, slack_user_id, year_month, new_code))
            row = cur.fetchone()
            if row:
                return row[0]
            # 極めて稀な競合ケース: 既存を再取得
            cur.execute(
                "SELECT employee_code FROM employee_monthly_codes WHERE tenant_id=%s AND year_month=%s AND slack_user_id=%s",
                (tenant_id, year_month, slack_user_id)
            )
            return cur.fetchone()[0]

def get_next_employee_sequence(upload_date: str, employee_code: int, tenant_id: str) -> int:
    """指定社員コードの当日連番（001〜999）を取得する（同時アクセス安全）"""
    with _get_conn(tenant_id) as conn:
        return _atomic_next_seq(conn, tenant_id, f"daily:{upload_date}:{employee_code:02d}")

def get_next_sequence(event_date, tenant_id):
    date_prefix = event_date.replace("-", "")
    like_pattern = f"T{date_prefix}-%"
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT event_id FROM accounting_events WHERE event_id LIKE %s AND tenant_id = %s ORDER BY event_id DESC LIMIT 1", (like_pattern, tenant_id))
            row = cur.fetchone()
    if row:
        return int(row[0].split("-")[1]) + 1
    return 1

def check_duplicate(invoice_number, amount, event_date, tenant_id):
    if not invoice_number:
        return None
    with _get_conn(tenant_id) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT event_id, counterparty, status, created_at FROM accounting_events WHERE invoice_number = %s AND amount = %s AND event_date = %s AND status IN ('業務承認済', '申請中') AND tenant_id = %s LIMIT 1", (invoice_number, amount, event_date, tenant_id))
            row = cur.fetchone()
    return dict(row) if row else None

def insert_event(entry_dict, tenant_id):
    entry_dict.setdefault("taxable_10_amount", 0)
    entry_dict.setdefault("tax_10_amount", 0)
    entry_dict.setdefault("taxable_8_amount", 0)
    entry_dict.setdefault("tax_8_amount", 0)
    entry_dict.setdefault("has_invoice", False)
    entry_dict.setdefault("employee_slack_id", "")
    entry_dict.setdefault("evidence_url", "")
    entry_dict.setdefault("memo", "")
    entry_dict.setdefault("purpose", "")
    entry_dict.setdefault("debit_subsidiary", "")
    entry_dict.setdefault("source_type", "expense")
    entry_dict["tenant_id"] = tenant_id
    sql = """INSERT INTO accounting_events (
        event_id, event_date, counterparty, amount,
        taxable_10_amount, tax_10_amount, taxable_8_amount, tax_8_amount,
        debit_account, debit_subsidiary, credit_account, invoice_number, has_invoice,
        employee_name, employee_slack_id, status, evidence_url, memo,
        purpose, source_type, tenant_id
    ) VALUES (
        %(event_id)s, %(event_date)s, %(counterparty)s, %(amount)s,
        %(taxable_10_amount)s, %(tax_10_amount)s, %(taxable_8_amount)s, %(tax_8_amount)s,
        %(debit_account)s, %(debit_subsidiary)s, %(credit_account)s, %(invoice_number)s, %(has_invoice)s,
        %(employee_name)s, %(employee_slack_id)s, %(status)s, %(evidence_url)s, %(memo)s,
        %(purpose)s, %(source_type)s, %(tenant_id)s
    )"""
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, entry_dict)
    logger.info(f"INSERT 完了: {entry_dict['event_id']} (tenant: {tenant_id})")

def get_event_by_id(event_id, tenant_id):
    with _get_conn(tenant_id) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM accounting_events WHERE event_id = %s AND tenant_id = %s", (event_id, tenant_id))
            row = cur.fetchone()
    return dict(row) if row else None


def get_linked_nyutou_entry(main_event_id: str, tenant_id: str):
    """主エントリのevent_idにリンクされた入湯税エントリを取得する"""
    with _get_conn(tenant_id) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM accounting_events WHERE purpose = %s AND debit_subsidiary = '入湯税' AND tenant_id = %s LIMIT 1",
                (f"入湯税（{main_event_id}から分割）", tenant_id)
            )
            row = cur.fetchone()
    return dict(row) if row else None

def save_uploader_dm_info(event_id: str, tenant_id: str, channel: str, ts: str) -> None:
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE accounting_events SET uploader_dm_channel=%s, uploader_dm_ts=%s WHERE event_id=%s AND tenant_id=%s",
                (channel, ts, event_id, tenant_id)
            )

def get_uploader_dm_info(event_id: str, tenant_id: str):
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT uploader_dm_channel, uploader_dm_ts FROM accounting_events WHERE event_id=%s AND tenant_id=%s",
                (event_id, tenant_id)
            )
            row = cur.fetchone()
    if row and row[0] and row[1]:
        return row[0], row[1]
    return None

def save_approval_card_info(event_id: str, tenant_id: str, channel: str, ts: str) -> None:
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE accounting_events SET approval_card_channel=%s, approval_card_ts=%s WHERE event_id=%s AND tenant_id=%s",
                (channel, ts, event_id, tenant_id)
            )

def get_approval_card_info(event_id: str, tenant_id: str):
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT approval_card_channel, approval_card_ts FROM accounting_events WHERE event_id=%s AND tenant_id=%s",
                (event_id, tenant_id)
            )
            row = cur.fetchone()
    if row and row[0] and row[1]:
        return row[0], row[1]
    return None

def update_status(event_id, status, tenant_id, approved_by=None):
    now = datetime.now()
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            if approved_by:
                cur.execute("UPDATE accounting_events SET status=%s, approved_by=%s, approved_at=%s, updated_at=%s WHERE event_id=%s AND tenant_id=%s", (status, approved_by, now, now, event_id, tenant_id))
            else:
                cur.execute("UPDATE accounting_events SET status=%s, updated_at=%s WHERE event_id=%s AND tenant_id=%s", (status, now, event_id, tenant_id))
    logger.info(f"ステータス更新: {event_id} → {status}")

def update_event(event_id: str, tenant_id: str, fields: dict) -> bool:
    """仕訳の任意フィールドを更新する（承認後の修正にも使用）"""
    ALLOWED = {
        "counterparty", "amount", "event_date", "debit_account", "debit_subsidiary",
        "invoice_number", "has_invoice", "memo", "purpose",
        "taxable_10_amount", "tax_10_amount", "taxable_8_amount", "tax_8_amount",
    }
    updates = {k: v for k, v in fields.items() if k in ALLOWED}
    if not updates:
        return False
    set_clause = ", ".join(f"{k} = %({k})s" for k in updates)
    updates["event_id"] = event_id
    updates["tenant_id"] = tenant_id
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE accounting_events SET {set_clause}, updated_at=NOW() "
                f"WHERE event_id=%(event_id)s AND tenant_id=%(tenant_id)s",
                updates
            )
    logger.info(f"仕訳更新: {event_id} fields={list(updates.keys())}")
    return True

def list_events_by_employee_month(employee_name, year, month, tenant_id):
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    from_date = f"{year:04d}-{month:02d}-01"
    to_date   = f"{year:04d}-{month:02d}-{last_day:02d}"
    with _get_conn(tenant_id) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM accounting_events WHERE employee_name = %s AND event_date BETWEEN %s AND %s AND tenant_id = %s ORDER BY event_date DESC", (employee_name, from_date, to_date, tenant_id))
            rows = cur.fetchall()
    return [dict(r) for r in rows]

def list_all_events_by_month(year, month, tenant_id):
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    from_date = f"{year:04d}-{month:02d}-01"
    to_date   = f"{year:04d}-{month:02d}-{last_day:02d}"
    with _get_conn(tenant_id) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM accounting_events WHERE event_date BETWEEN %s AND %s AND tenant_id = %s ORDER BY employee_name, event_date DESC", (from_date, to_date, tenant_id))
            rows = cur.fetchall()
    return [dict(r) for r in rows]

# ============================================================
# Users テーブル（定期券区間管理）
# ============================================================

USERS_DDL = """
CREATE TABLE IF NOT EXISTS nextaccount_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slack_user_id VARCHAR(50) NOT NULL,
    employee_name VARCHAR(100) NOT NULL,
    commute_from VARCHAR(100),
    commute_to VARCHAR(100),
    tenant_id UUID REFERENCES tenants(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(slack_user_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_users_slack_id ON nextaccount_users(slack_user_id);
CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON nextaccount_users(tenant_id);
"""

def init_users_table():
    """users テーブルを作成"""
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(USERS_DDL)
        logger.info("users テーブル初期化完了")
    except Exception as e:
        logger.warning(f"users テーブル初期化: {e}")

def get_user_by_slack_id(slack_user_id, tenant_id):
    """Slack user_id からユーザーを取得"""
    with _get_conn(tenant_id) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM nextaccount_users WHERE slack_user_id = %s AND tenant_id = %s",
                (slack_user_id, tenant_id)
            )
            row = cur.fetchone()
    return dict(row) if row else None

def upsert_user(slack_user_id, employee_name, tenant_id, commute_from=None, commute_to=None):
    """ユーザーを作成または更新"""
    with _get_conn(tenant_id) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO nextaccount_users (slack_user_id, employee_name, commute_from, commute_to, tenant_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (slack_user_id, tenant_id) DO UPDATE
                  SET employee_name = EXCLUDED.employee_name,
                      commute_from = COALESCE(EXCLUDED.commute_from, nextaccount_users.commute_from),
                      commute_to = COALESCE(EXCLUDED.commute_to, nextaccount_users.commute_to),
                      updated_at = NOW()
                RETURNING *
            """, (slack_user_id, employee_name, commute_from, commute_to, tenant_id))
            row = cur.fetchone()
    return dict(row) if row else None

def update_commute_section(slack_user_id, tenant_id, commute_from, commute_to):
    """定期券区間を更新"""
    with _get_conn(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE nextaccount_users SET commute_from=%s, commute_to=%s, updated_at=NOW() WHERE slack_user_id=%s AND tenant_id=%s",
                (commute_from, commute_to, slack_user_id, tenant_id)
            )
    logger.info(f"定期券区間更新: {slack_user_id} ({commute_from}→{commute_to})")
