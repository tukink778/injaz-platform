"""ربط حسابات البريد (OAuth 2.0): اتصال واحد لكل (مستخدم، مزوّد)، ولا يُربط صندوق بريد بأكثر من مستخدم.
الرموز لا تُخزَّن إلا مشفّرة (AES-256-GCM) في encrypted_refresh_token. oauth_states: حالة تدفق الربط (استخدام واحد، 10 دقائق)."""
DESCRIPTION = "user_email_connections, oauth_states"


def up(c):
    # فحص أحرف المزوّد: GLOB في SQLite، تعبير منتظم في PostgreSQL
    prov_chk = "provider ~ '^[a-z0-9_]+$'" if c.dialect == "postgres" else "provider NOT GLOB '*[^a-z0-9_]*'"
    c.ddl(f"""CREATE TABLE IF NOT EXISTS user_email_connections(
      id INTEGER PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      provider TEXT NOT NULL CHECK(length(provider) BETWEEN 2 AND 32 AND {prov_chk}),
      provider_user_id TEXT NOT NULL,
      email_address TEXT NOT NULL,
      encrypted_refresh_token TEXT NOT NULL,
      token_expiry INTEGER NOT NULL DEFAULT 0,
      granted_scopes TEXT NOT NULL DEFAULT '',
      connection_status TEXT NOT NULL DEFAULT 'connected' CHECK(connection_status IN ('connected','needs_reconnect')),
      last_error TEXT NOT NULL DEFAULT '', last_error_at INTEGER,
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, last_used_at INTEGER, last_success_at INTEGER,
      UNIQUE(user_id, provider),
      UNIQUE(provider, provider_user_id))""")
    c.ddl("CREATE INDEX IF NOT EXISTS ix_uec_status ON user_email_connections(provider, connection_status)")
    c.ddl("""CREATE TABLE IF NOT EXISTS oauth_states(
      state_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      provider TEXT NOT NULL, verifier_enc TEXT NOT NULL, created_at INTEGER NOT NULL)""")
