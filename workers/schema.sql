-- D1 schema for the Workers edition. Idempotent; applied by deploy workflow.
CREATE TABLE IF NOT EXISTS tenants (
  tenant_id TEXT PRIMARY KEY,
  brand_voice TEXT,
  intensity TEXT DEFAULT 'balanced',
  analytics_consent INTEGER DEFAULT 0,
  training_consent INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS scripts (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  topic TEXT, platform TEXT, content TEXT, source TEXT,
  labeled INTEGER DEFAULT 1, note TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_scripts_tenant ON scripts(tenant_id);
CREATE TABLE IF NOT EXISTS audits (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  url TEXT, status_code INTEGER, result TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_audits_tenant ON audits(tenant_id);
CREATE TABLE IF NOT EXISTS pipeline_runs (
  period_key TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  period TEXT, result TEXT, run_at TEXT
);
