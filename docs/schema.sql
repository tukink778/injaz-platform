-- مخطط قاعدة بيانات INJAZ (SQLite) كما يُنشأ عند أول تشغيل + الهجرات المطبّقة.
-- للمرجعية فقط: التطبيق ينشئ القاعدة ويطبّق الهجرات (app/migrations) تلقائياً.

CREATE TABLE audit(
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, user_id INTEGER, username TEXT, ip TEXT, action TEXT NOT NULL,
  target TEXT, detail TEXT, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);

CREATE TABLE certificates(
  id INTEGER PRIMARY KEY, participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  serial TEXT UNIQUE NOT NULL, verify_code TEXT UNIQUE NOT NULL, name_enc TEXT NOT NULL,
  issued_at INTEGER NOT NULL, issued_by INTEGER REFERENCES users(id), file TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'valid', revoked_at INTEGER, revoked_reason TEXT NOT NULL DEFAULT '',
  email_status TEXT NOT NULL DEFAULT 'none', email_sent_at INTEGER, email_error TEXT NOT NULL DEFAULT '',
  dl_hash TEXT NOT NULL DEFAULT '', dl_expires INTEGER NOT NULL DEFAULT 0, downloads INTEGER NOT NULL DEFAULT 0);

CREATE TABLE counters(name TEXT PRIMARY KEY, val INTEGER NOT NULL);

CREATE TABLE departments(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name_ar TEXT NOT NULL, name_en TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE events(
  id INTEGER PRIMARY KEY, dept_id INTEGER NOT NULL REFERENCES departments(id),
  title_ar TEXT NOT NULL, title_en TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'training',
  template_id INTEGER NOT NULL REFERENCES templates(id),
  event_date TEXT NOT NULL, end_date TEXT, hours REAL NOT NULL DEFAULT 0, location TEXT NOT NULL DEFAULT '',
  signatory_name TEXT NOT NULL DEFAULT '', signatory_title_ar TEXT NOT NULL DEFAULT '', signatory_title_en TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft', reg_token TEXT UNIQUE NOT NULL, reg_open INTEGER NOT NULL DEFAULT 0,
  reg_opens_at TEXT, reg_closes_at TEXT, reg_pin TEXT NOT NULL DEFAULT '', max_participants INTEGER NOT NULL DEFAULT 0,
  auto_approve INTEGER NOT NULL DEFAULT 0, require_uid INTEGER NOT NULL DEFAULT 1, require_spec INTEGER NOT NULL DEFAULT 0,
  rows_total INTEGER NOT NULL DEFAULT 0, rows_invalid INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER REFERENCES users(id), created_at INTEGER NOT NULL);

CREATE TABLE jobs(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, event_id INTEGER, user_id INTEGER, status TEXT NOT NULL DEFAULT 'running',
  total INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL);

CREATE TABLE participants(
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  name_enc TEXT NOT NULL, name_en_enc TEXT NOT NULL DEFAULT '', uid_enc TEXT NOT NULL DEFAULT '', uid_idx TEXT NOT NULL DEFAULT '',
  email_enc TEXT NOT NULL DEFAULT '', email_idx TEXT NOT NULL DEFAULT '', specialization TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual', status TEXT NOT NULL DEFAULT 'pending',
  checked_in_at INTEGER, created_at INTEGER NOT NULL, dedupe TEXT NOT NULL, academic_status TEXT NOT NULL DEFAULT '', gender TEXT NOT NULL DEFAULT '', nationality TEXT NOT NULL DEFAULT '',
  UNIQUE(event_id, dedupe));

CREATE TABLE portal_tokens(token_hash TEXT PRIMARY KEY, uid_idx TEXT NOT NULL DEFAULT '', email_idx TEXT NOT NULL DEFAULT '', expires INTEGER NOT NULL);

CREATE TABLE rate(key TEXT NOT NULL, ts INTEGER NOT NULL);

CREATE TABLE schema_migrations(name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL);

CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE templates(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL, builtin INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);

CREATE TABLE users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',
  pw_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN('admin','manager','organizer','viewer')),
  dept_id INTEGER REFERENCES departments(id), active INTEGER NOT NULL DEFAULT 1,
  must_change_pw INTEGER NOT NULL DEFAULT 1, totp_secret TEXT, failed INTEGER NOT NULL DEFAULT 0,
  locked_until INTEGER NOT NULL DEFAULT 0, session_ver INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL, last_login INTEGER);

CREATE INDEX ix_part_demo ON participants(gender, nationality, academic_status);

CREATE INDEX ix_part_email ON participants(email_idx);

CREATE INDEX ix_part_uid ON participants(uid_idx);

CREATE INDEX ix_rate ON rate(key, ts);
