-- NextAccount v3 SaaS Schema
-- 独立SaaS版本 (移除Google Sheets依赖)
-- 执行日期: 2026-05-23

-- ============================================================
-- 1. Organizations (公司/组织)
-- ============================================================
CREATE TABLE IF NOT EXISTS organizations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    name_kana VARCHAR(255),
    created_by_user_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    UNIQUE(name)
);

CREATE INDEX idx_organizations_created_at ON organizations(created_at);

-- ============================================================
-- 2. Users (登录账号)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    last_login_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(email)
);

CREATE INDEX idx_users_organization_id ON users(organization_id);
CREATE INDEX idx_users_email ON users(email);

-- ============================================================
-- 3. Accounting Periods (会计年度)
-- ============================================================
CREATE TABLE IF NOT EXISTS accounting_periods (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    fiscal_year_start DATE NOT NULL,
    fiscal_year_end DATE NOT NULL,
    start_month INTEGER NOT NULL,  -- 1-12
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(organization_id, fiscal_year_start)
);

CREATE INDEX idx_accounting_periods_org ON accounting_periods(organization_id);

-- ============================================================
-- 4. Departments (部门)
-- ============================================================
CREATE TABLE IF NOT EXISTS departments (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    code VARCHAR(50),
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(organization_id, name)
);

CREATE INDEX idx_departments_org ON departments(organization_id);

-- ============================================================
-- 5. Employees (员工)
-- ============================================================
CREATE TABLE IF NOT EXISTS employees (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    user_id INTEGER,
    full_name VARCHAR(255) NOT NULL,
    full_name_kana VARCHAR(255),
    email VARCHAR(255) NOT NULL,
    slack_user_id VARCHAR(255),
    department_id INTEGER,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL,
    UNIQUE(organization_id, email),
    UNIQUE(slack_user_id)
);

CREATE INDEX idx_employees_org ON employees(organization_id);
CREATE INDEX idx_employees_slack_user_id ON employees(slack_user_id);

-- ============================================================
-- 6. Slack Workspaces (Slack集成)
-- ============================================================
CREATE TABLE IF NOT EXISTS slack_workspaces (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    workspace_id VARCHAR(255) NOT NULL,
    workspace_name VARCHAR(255) NOT NULL,
    bot_token VARCHAR(255) NOT NULL,
    bot_user_id VARCHAR(255),
    channel_id VARCHAR(255),
    channel_name VARCHAR(255) DEFAULT '#経費申請',
    is_connected BOOLEAN DEFAULT FALSE,
    connected_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(organization_id, workspace_id)
);

CREATE INDEX idx_slack_workspaces_org ON slack_workspaces(organization_id);

-- ============================================================
-- 7. Permissions (权限)
-- ============================================================
CREATE TABLE IF NOT EXISTS user_permissions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    organization_id INTEGER NOT NULL,
    permission_type VARCHAR(50) NOT NULL,
    -- 可选值: upload_receipt, approve_journal, export_csv, manage_employees, manage_departments, admin
    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(user_id, permission_type)
);

CREATE INDEX idx_permissions_user ON user_permissions(user_id);

-- ============================================================
-- 8. Setup Progress (初始化进度追踪)
-- ============================================================
CREATE TABLE IF NOT EXISTS setup_progress (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    step_completed INTEGER DEFAULT 0,  -- 0: 未开始, 1-4: 完成的步骤
    is_completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(organization_id)
);

-- ============================================================
-- 9. Session Tokens (会话管理)
-- ============================================================
CREATE TABLE IF NOT EXISTS session_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token VARCHAR(255) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(token)
);

CREATE INDEX idx_session_tokens_user ON session_tokens(user_id);
CREATE INDEX idx_session_tokens_expires ON session_tokens(expires_at);

-- ============================================================
-- 10. Audit Log (审计日志)
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    user_id INTEGER,
    action VARCHAR(255) NOT NULL,
    resource_type VARCHAR(100),
    resource_id INTEGER,
    details JSONB,
    ip_address VARCHAR(45),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX idx_audit_logs_org ON audit_logs(organization_id);
CREATE INDEX idx_audit_logs_created ON audit_logs(created_at);

-- ============================================================
-- Views (视图)
-- ============================================================

-- 组织的完整信息（带Slack状态）
CREATE OR REPLACE VIEW org_with_slack AS
SELECT
    o.id,
    o.name,
    COUNT(DISTINCT e.id) as employee_count,
    COUNT(DISTINCT d.id) as department_count,
    sw.is_connected,
    sw.workspace_name
FROM organizations o
LEFT JOIN employees e ON o.id = e.organization_id AND e.deleted_at IS NULL
LEFT JOIN departments d ON o.id = d.organization_id AND d.deleted_at IS NULL
LEFT JOIN slack_workspaces sw ON o.id = sw.organization_id
GROUP BY o.id, o.name, sw.is_connected, sw.workspace_name;

-- ============================================================
-- Sample Data (测试数据 - 可选)
-- ============================================================
-- 取消注释以插入测试数据
-- INSERT INTO organizations (name) VALUES ('テスト会社');
-- INSERT INTO departments (organization_id, name) VALUES (1, '営業部'), (1, '企画部');
