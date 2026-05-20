-- ============================================================
-- NextAccount v2 — Supabase スキーマ
-- Supabase SQL エディタで実行する
-- ============================================================

-- メインテーブル
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
    credit_account      VARCHAR(100) NOT NULL,          -- 未払費用（社員名）
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
    updated_at          TIMESTAMP    DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_ae_invoice   ON accounting_events(invoice_number);
CREATE INDEX IF NOT EXISTS idx_ae_date      ON accounting_events(event_date);
CREATE INDEX IF NOT EXISTS idx_ae_status    ON accounting_events(status);
CREATE INDEX IF NOT EXISTS idx_ae_employee  ON accounting_events(employee_name);
CREATE INDEX IF NOT EXISTS idx_ae_created   ON accounting_events(created_at);

-- updated_at 自動更新トリガー
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_ae_updated_at
BEFORE UPDATE ON accounting_events
FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Row Level Security（RLS）を有効化（オプション）
-- ALTER TABLE accounting_events ENABLE ROW LEVEL SECURITY;

-- 確認クエリ
SELECT 'スキーマ作成完了' AS result;

-- ============================================================
-- Tenants テーブル（マルチテナント対応）
-- ============================================================
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slack_team_id VARCHAR(50) UNIQUE NOT NULL,
    slack_bot_token VARCHAR(255),
    google_sheet_id VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tenants_slack_team_id ON tenants(slack_team_id);

-- ============================================================
-- Users テーブル（定期券区間管理）
-- ============================================================
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

-- accounting_events に tenant_id を追加（存在しない場合）
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id);
CREATE INDEX IF NOT EXISTS idx_ae_tenant_id ON accounting_events(tenant_id);
