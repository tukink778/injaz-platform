import re
import sqlite3
import time
from flask import g
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS departments(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name_ar TEXT NOT NULL, name_en TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',
  pw_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN('admin','manager','organizer','viewer')),
  dept_id INTEGER REFERENCES departments(id), active INTEGER NOT NULL DEFAULT 1,
  must_change_pw INTEGER NOT NULL DEFAULT 1, totp_secret TEXT, failed INTEGER NOT NULL DEFAULT 0,
  locked_until INTEGER NOT NULL DEFAULT 0, session_ver INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL, last_login INTEGER);
CREATE TABLE IF NOT EXISTS templates(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL, builtin INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events(
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
CREATE TABLE IF NOT EXISTS participants(
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  name_enc TEXT NOT NULL, name_en_enc TEXT NOT NULL DEFAULT '', uid_enc TEXT NOT NULL DEFAULT '', uid_idx TEXT NOT NULL DEFAULT '',
  email_enc TEXT NOT NULL DEFAULT '', email_idx TEXT NOT NULL DEFAULT '', specialization TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual', status TEXT NOT NULL DEFAULT 'pending',
  checked_in_at INTEGER, created_at INTEGER NOT NULL, dedupe TEXT NOT NULL,
  UNIQUE(event_id, dedupe));
CREATE INDEX IF NOT EXISTS ix_part_uid ON participants(uid_idx);
CREATE INDEX IF NOT EXISTS ix_part_email ON participants(email_idx);
CREATE TABLE IF NOT EXISTS certificates(
  id INTEGER PRIMARY KEY, participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  serial TEXT UNIQUE NOT NULL, verify_code TEXT UNIQUE NOT NULL, name_enc TEXT NOT NULL,
  issued_at INTEGER NOT NULL, issued_by INTEGER REFERENCES users(id), file TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'valid', revoked_at INTEGER, revoked_reason TEXT NOT NULL DEFAULT '',
  email_status TEXT NOT NULL DEFAULT 'none', email_sent_at INTEGER, email_error TEXT NOT NULL DEFAULT '',
  dl_hash TEXT NOT NULL DEFAULT '', dl_expires INTEGER NOT NULL DEFAULT 0, downloads INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, val INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, user_id INTEGER, username TEXT, ip TEXT, action TEXT NOT NULL,
  target TEXT, detail TEXT, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rate(key TEXT NOT NULL, ts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_rate ON rate(key, ts);
CREATE TABLE IF NOT EXISTS portal_tokens(token_hash TEXT PRIMARY KEY, uid_idx TEXT NOT NULL DEFAULT '', email_idx TEXT NOT NULL DEFAULT '', expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, event_id INTEGER, user_id INTEGER, status TEXT NOT NULL DEFAULT 'running',
  total INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL);
"""


def connect():
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def run_migrations(c):
    """يطبّق الهجرات غير المنفّذة بالترتيب (مجلد app/migrations)."""
    import importlib, pkgutil
    from . import migrations as pkg
    c.execute("CREATE TABLE IF NOT EXISTS schema_migrations(name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)")
    done = {r[0] for r in c.execute("SELECT name FROM schema_migrations")}
    for m in sorted(x.name for x in pkgutil.iter_modules(pkg.__path__) if re.fullmatch(r"m\d{4}_\w+", x.name)):
        if m in done: continue
        mod = importlib.import_module(f"{pkg.__name__}.{m}")
        c.execute("BEGIN")
        try:
            mod.up(c); c.execute("INSERT INTO schema_migrations(name,applied_at) VALUES(?,?)", (m, int(time.time()))); c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK"); raise


def init_db():
    c = connect()
    c.executescript(SCHEMA)
    run_migrations(c)
    c.close()


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_=None):
    c = g.pop("db", None)
    if c:
        c.close()


def setting(c, key, default=""):
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(c, key, value):
    c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def next_counter(c, name):
    c.execute("INSERT INTO counters(name,val) VALUES(?,1) ON CONFLICT(name) DO UPDATE SET val=val+1", (name,))
    return c.execute("SELECT val FROM counters WHERE name=?", (name,)).fetchone()[0]
