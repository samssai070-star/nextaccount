-- ============================================================
-- Migration: clients テーブル追加 & 既存テーブル拡張
-- 会計事務所の顧問先管理対応
-- Supabase SQL エディタで実行する
-- ============================================================

-- organizations: org_type カラム追加（既存は全て 'company'）
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS org_type VARCHAR(20) NOT NULL DEFAULT 'company';

-- clients テーブル作成
CREATE TABLE IF NOT EXISTS clients (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      INTEGER      NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name        VARCHAR(200) NOT NULL,
    description TEXT         DEFAULT '',
    is_active   BOOLEAN      DEFAULT TRUE,
    created_at  TIMESTAMP    DEFAULT NOW(),
    updated_at  TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_clients_org_id ON clients(org_id);

-- updated_at 自動更新トリガー
CREATE OR REPLACE TRIGGER trg_clients_updated_at
BEFORE UPDATE ON clients
FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- tenants: org_id / client_id カラム追加
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS org_id    INTEGER REFERENCES organizations(id);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS client_id UUID    REFERENCES clients(id);

-- accounting_events: client_id カラム追加
ALTER TABLE accounting_events ADD COLUMN IF NOT EXISTS client_id UUID REFERENCES clients(id);
CREATE INDEX IF NOT EXISTS idx_ae_client_id ON accounting_events(client_id);

-- 確認クエリ
SELECT 'clients migration 完了' AS result;
