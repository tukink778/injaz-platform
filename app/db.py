"""طبقة قاعدة البيانات الموحّدة: SQLite (افتراضي محلياً) أو PostgreSQL (عند ضبط DATABASE_URL).

الواجهة العامة ثابتة لبقية المشروع: connect() · db() · close_db() · setting() · set_setting() · next_counter() · init_db().
كل اتصال (من النوعين) يوفّر الطرق نفسها:
    execute / executemany          ← تُكتب الاستعلامات بعلامة ? في الحالتين (تُترجم تلقائياً لـ PostgreSQL)
    insert(sql, params) -> id      ← إدراج مع إرجاع رقم السجل الجديد (بديل lastrowid / last_insert_rowid)
    txn(immediate=False)           ← معاملة (تتحول إلى SAVEPOINT إن كانت داخل معاملة قائمة)
    columns(table) / dialect / ddl(sql)   ← لأعمال الهجرات
الصفوف تدعم row["col"] وrow[0] وdict(row) وrow.keys() كما في sqlite3.Row.
"""
import contextlib
import functools
import hashlib
import itertools
import logging
import re
import sqlite3
import threading
import time

from flask import g

from . import config
from .config import DB_PATH

log = logging.getLogger("injaz.db")
IS_PG = config.IS_PG

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

AUDIT_LOCK_ID = 7311001          # مفتاح قفل استشاري (PostgreSQL) لتسلسل سجل التدقيق
MIGRATION_LOCK_ID = 7311002      # قفل الهجرات حتى لا تعمل نسختان من التطبيق عليها معاً


# ---------------------------------------------------------------------------
# أدوات مشتركة
# ---------------------------------------------------------------------------
def _split_statements(script: str):
    return [s.strip() for s in script.split(";") if s.strip()]


def pg_ddl(sql: str) -> str:
    """يحوّل DDL مكتوباً بصيغة SQLite إلى PostgreSQL (أنواع الأعمدة والمفاتيح التلقائية)."""
    sql = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\b", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", sql)
    sql = re.sub(r"\bINTEGER\b", "BIGINT", sql)
    return re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)


class _TxnMixin:
    """معاملات متداخلة آمنة: الخارجية BEGIN/COMMIT والداخلية SAVEPOINT (تعمل بالمثل في SQLite وPostgreSQL)."""
    _sp = itertools.count(1)

    @contextlib.contextmanager
    def txn(self, immediate=False):
        if self.in_txn:
            name = f"sp_{next(self._sp)}"
            self.execute(f"SAVEPOINT {name}")
            try:
                yield self
            except BaseException:
                self.execute(f"ROLLBACK TO SAVEPOINT {name}"); self.execute(f"RELEASE SAVEPOINT {name}"); raise
            else:
                self.execute(f"RELEASE SAVEPOINT {name}")
            return
        self.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield self
        except BaseException:
            self.execute("ROLLBACK"); raise
        else:
            self.execute("COMMIT")


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------
class SQLiteConn(_TxnMixin, sqlite3.Connection):
    dialect = "sqlite"

    @property
    def in_txn(self):
        return self.in_transaction

    def insert(self, sql, params=()):
        return self.execute(sql, params).lastrowid

    def ddl(self, sql):
        self.execute(sql)

    def columns(self, table):
        return {r[1] for r in self.execute(f"PRAGMA table_info({table})")}

    def tables(self):
        return [r[0] for r in self.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    def audit_guard(self):
        return contextlib.nullcontext()

    def month_of_epoch(self, col):
        return f"strftime('%Y-%m', {col}, 'unixepoch', '+4 hours')"      # توقيت عُمان UTC+4

    def ping(self):
        return self.execute("SELECT 1").fetchone()[0] == 1


def _connect_sqlite():
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None, factory=SQLiteConn)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
class Row:
    """صف بنتائج الاستعلام: row["col"] · row[0] · dict(row) · row.keys() · التكرار على القيم."""
    __slots__ = ("_k", "_i", "_v")

    def __init__(self, keys, index, values):
        self._k, self._i, self._v = keys, index, values

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._v[key]
        try:
            return self._v[self._i[key]]
        except KeyError:
            raise IndexError(f"No item with that key: {key}") from None

    def keys(self):
        return list(self._k)

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)

    def __eq__(self, other):
        return isinstance(other, Row) and self._k == other._k and self._v == other._v

    def __hash__(self):
        return hash(self._v)

    def __repr__(self):
        return "Row(" + ", ".join(f"{k}={v!r}" for k, v in zip(self._k, self._v)) + ")"


def _row_factory(cursor):
    desc = cursor.description
    if desc is None:
        return lambda values: values
    keys = tuple(d.name for d in desc)
    index = {}
    for i, k in enumerate(keys):
        index.setdefault(k, i)               # عند تكرار الاسم يُعتمد الأول (كما في sqlite3.Row)
    return lambda values: Row(keys, index, tuple(values))


@functools.lru_cache(maxsize=2048)
def _translate(sql: str) -> str:
    """SQLite → PostgreSQL للاستعلامات العادية: علامات ? و%، LIKE غير الحساس لحالة الأحرف، وصيغ BEGIN."""
    s = sql.strip()
    if re.match(r"(?is)^PRAGMA\b", s):
        raise RuntimeError("PRAGMA خاص بـ SQLite؛ استخدم c.columns() أو طرق الاتصال الموحّدة")
    if "last_insert_rowid" in s:
        raise RuntimeError("استخدم c.insert(...) لإرجاع رقم السجل الجديد")
    s = re.sub(r"(?is)^BEGIN\s+(IMMEDIATE|EXCLUSIVE|DEFERRED)\b", "BEGIN", s)
    out, i, n = [], 0, len(s)
    while i < n:                                # لا نمسّ النصوص بين علامتي اقتباس مفردة (عدا مضاعفة %)
        ch = s[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if s[j] == "'":
                    if j + 1 < n and s[j + 1] == "'":
                        j += 2; continue
                    break
                j += 1
            out.append(s[i:j + 1].replace("%", "%%")); i = j + 1; continue
        if ch == "%":
            out.append("%%")
        elif ch == "?":
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    t = "".join(out)
    t = re.sub(r"(?i)\bLIKE\s+%s", "ILIKE %s ESCAPE ''", t)      # SQLite: LIKE غير حساس للحالة ولا يوجد محرف هروب افتراضي
    return re.sub(r"(?i)\bLIKE\b(?!\s+%s)", "ILIKE", t)


_pool = None
_pool_lock = threading.Lock()


def _redact(msg: str) -> str:
    """لا تظهر كلمة مرور القاعدة أو عنوانها الكامل في أي رسالة خطأ أو سجل."""
    msg = str(msg)
    try:
        from psycopg.conninfo import conninfo_to_dict
        pw = conninfo_to_dict(config.DATABASE_URL).get("password")
        if pw:
            msg = msg.replace(pw, "***")
    except Exception:
        pass
    return re.sub(r"(postgres(?:ql)?://[^:@/\s]+:)[^@\s]+@", r"\1***@", msg)


def _conninfo():
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    d = conninfo_to_dict(config.DATABASE_URL)
    host = d.get("host", "") or ""
    local = host in ("", "localhost", "127.0.0.1", "::1") or host.startswith("/")
    if "sslmode" not in d:                      # Neon يشترط TLS؛ لا نُلزمه على الاتصال المحلي (للتطوير والاختبار)
        d["sslmode"] = config.Cfg.DB_SSLMODE or ("prefer" if local else "require")
    d.setdefault("connect_timeout", "10")
    d.setdefault("application_name", "injaz")
    return make_conninfo(**d)


def _configure(conn):
    import psycopg
    from psycopg.adapt import Loader

    class _Num(Loader):                          # SUM/AVG تعيد numeric في PostgreSQL: نرجعها int/float كما في SQLite
        def load(self, data):
            s = bytes(data).decode()
            return float(s) if "." in s else int(s)

    conn.adapters.register_loader("numeric", _Num)
    conn.row_factory = _row_factory
    if config.Cfg.DB_SCHEMA:                    # اختياري (للاختبارات): عزل الجداول في schema مستقل
        # الـ schema يكون قد أُنشئ قبل فتح المجمّع (_bootstrap_schema)؛ والاسم يُهرَّب كمعرّف عبر psycopg.sql لا كنص خام
        from psycopg import sql
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(_ident_name(config.Cfg.DB_SCHEMA))))


def _ident_name(name: str) -> str:
    """قائمة سماح صارمة لاسم الـ schema (حروف/أرقام/شرطة سفلية فقط، ≤63) — تُطبَّق قبل أي استخدام في SQL."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name or "") or name.lower().startswith("pg_"):
        raise RuntimeError("اسم schema غير صالح (INJAZ_DB_SCHEMA): أحرف لاتينية وأرقام وشرطة سفلية فقط، ولا يبدأ بـ pg_")
    return name


def _ident(name: str) -> str:
    return '"' + _ident_name(name) + '"'


def _bootstrap_schema(ci):
    """قبل فتح المجمّع: يتأكد أن الـ schema المخصص موجود (وينشئه عند الحاجة) ثم يتحقق من الصلاحيات، بخطأ واضح بدل فشل لاحق غامض.
    يعمل باتصال مباشر مستقل لا يعتمد على search_path، وتحت القفل الاستشاري نفسه كي لا تتسابق نسختان على CREATE SCHEMA."""
    import psycopg
    from psycopg import sql
    name = _ident_name(config.Cfg.DB_SCHEMA)
    with psycopg.connect(ci, autocommit=True, prepare_threshold=None) as cx:
        cx.execute("BEGIN")
        try:
            cx.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
            if not cx.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (name,)).fetchone():
                if not cx.execute("SELECT has_database_privilege(current_database(), 'CREATE')").fetchone()[0]:
                    raise RuntimeError(f"الـ schema «{name}» غير موجود ودور القاعدة لا يملك صلاحية CREATE لإنشائه. أنشئه يدوياً أو امنح الصلاحية.")
                cx.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
            u, cr = cx.execute("SELECT has_schema_privilege(%s,'USAGE'), has_schema_privilege(%s,'CREATE')", (name, name)).fetchone()
            if not (u and cr):
                raise RuntimeError(f"دور القاعدة لا يملك صلاحيتي USAGE وCREATE على schema «{name}».")
            cx.execute("COMMIT")
        except BaseException:
            cx.execute("ROLLBACK"); raise


def get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            from psycopg_pool import ConnectionPool
            try:
                if config.Cfg.DB_SCHEMA:
                    _bootstrap_schema(_conninfo())
                _pool = ConnectionPool(
                    conninfo=_conninfo(), min_size=1, max_size=config.Cfg.DB_POOL_MAX, name="injaz", open=False,
                    kwargs=dict(autocommit=True, prepare_threshold=None),      # بلا prepared statements: متوافق مع PgBouncer (Neon pooler)
                    configure=_configure, check=ConnectionPool.check_connection,
                    timeout=config.Cfg.DB_CONNECT_WAIT, max_lifetime=1800, max_idle=240, reconnect_timeout=60)
                _pool.open(wait=True, timeout=config.Cfg.DB_CONNECT_WAIT)
            except Exception as e:
                p, _pool = _pool, None
                try:
                    p and p.close()
                except Exception:
                    pass
                if isinstance(e, RuntimeError) and "schema" in str(e):
                    raise RuntimeError(_redact(e)) from None
                raise RuntimeError(f"تعذّر الاتصال بقاعدة PostgreSQL ({type(e).__name__}): {_redact(e)}") from None
        return _pool


def close_pool():
    global _pool
    with _pool_lock:
        p, _pool = _pool, None
    if p:
        p.close()


class PGConn(_TxnMixin):
    dialect = "postgres"

    def __init__(self):
        self._pool = get_pool()
        try:
            self._c = self._pool.getconn()
        except Exception as e:
            raise RuntimeError(f"تعذّر الحصول على اتصال بقاعدة البيانات ({type(e).__name__}): {_redact(e)}") from None

    # ---- الاتصال ----
    @property
    def in_txn(self):
        from psycopg.pq import TransactionStatus
        return self._c.info.transaction_status != TransactionStatus.IDLE

    def close(self):
        c, self._c = getattr(self, "_c", None), None
        if c is not None:
            try:
                self._pool.putconn(c)            # يُرجع الاتصال للمجمّع (ويتراجع عن أي معاملة مفتوحة)
            except Exception:
                log.warning("failed to return connection to pool")

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ---- التنفيذ ----
    def execute(self, sql, params=()):
        return self._c.execute(_translate(sql), tuple(params))

    def executemany(self, sql, seq):
        cur = self._c.cursor()
        cur.executemany(_translate(sql), [tuple(p) for p in seq])
        return cur

    def insert(self, sql, params=()):
        return self.execute(sql.rstrip().rstrip(";") + " RETURNING id", params).fetchone()[0]

    def ddl(self, sql):
        self.execute(pg_ddl(sql))

    def columns(self, table):
        return {r[0] for r in self.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?", (table,))}

    def tables(self):
        return [r[0] for r in self.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE' ORDER BY 1")]

    @contextlib.contextmanager
    def audit_guard(self):
        """يُسلسل كتابات سجل التدقيق عبر كل العمليات/النسخ (قفل استشاري على مستوى المعاملة)."""
        with self.txn():
            self.execute("SELECT pg_advisory_xact_lock(?)", (AUDIT_LOCK_ID,))
            yield

    def month_of_epoch(self, col):
        return f"to_char(to_timestamp({col} + 14400) AT TIME ZONE 'UTC', 'YYYY-MM')"     # +4 ساعات = توقيت عُمان

    def ping(self):
        return self.execute("SELECT 1").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# الواجهة العامة
# ---------------------------------------------------------------------------
def connect():
    return PGConn() if IS_PG else _connect_sqlite()


def _lock_migrations(c):
    """PostgreSQL: قفل استشاري على مستوى المعاملة (يُحرَّر تلقائياً عند COMMIT/ROLLBACK). SQLite: BEGIN IMMEDIATE يؤدي الغرض."""
    if c.dialect == "postgres":
        c.execute("SELECT pg_advisory_xact_lock(?)", (MIGRATION_LOCK_ID,))


@contextlib.contextmanager
def startup_guard(c):
    """معاملة + قفل البدء نفسه: لخطوات الإقلاع «افحص ثم أدخل» (بذر الأقسام/القوالب، بصمة المفتاح) كي لا تتكرر عند تشغيل نسختين معاً."""
    with c.txn(immediate=True):
        _lock_migrations(c)
        yield


def run_migrations(c, pkg=None):
    """يطبّق الهجرات غير المنفّذة بالترتيب (app/migrations) في حلقة واحدة.
    لكل هجرة معاملة ذرية واحدة: [قفل ← فحص schema_migrations ← up() ← تسجيل]. فشلها يُلغي كل أثرها (DDL معاملاتي في القاعدتين)،
    والقفل قبل الفحص يضمن أن نسخة ثانية تنتظر ثم ترى الهجرة مسجّلة فتتخطاها. pkg: لتمرير حزمة هجرات بديلة في الاختبارات فقط."""
    import importlib, pkgutil
    if pkg is None:
        from . import migrations as pkg
    with c.txn(immediate=True):                     # الجدول نفسه يُنشأ تحت القفل أيضاً (CREATE IF NOT EXISTS المتزامن قد يتصادم)
        _lock_migrations(c)
        c.ddl("CREATE TABLE IF NOT EXISTS schema_migrations(name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)")
    names = sorted(x.name for x in pkgutil.iter_modules(pkg.__path__) if re.fullmatch(r"m\d{4}_\w+", x.name))
    for m in names:
        with c.txn(immediate=True):
            _lock_migrations(c)                     # 1) القفل أولاً
            if c.execute("SELECT 1 FROM schema_migrations WHERE name=?", (m,)).fetchone():   # 2) ثم الفحص
                continue
            importlib.import_module(f"{pkg.__name__}.{m}").up(c)                              # 3) التنفيذ
            c.execute("INSERT INTO schema_migrations(name,applied_at) VALUES(?,?)", (m, int(time.time())))  # 4) التسجيل (ذرّياً مع 3)


def init_db():
    c = connect()
    try:
        if c.dialect == "postgres":
            # الـ schema المخصص أُنشئ وفُحصت صلاحياته مسبقاً عند فتح المجمّع (get_pool). هنا نتحقق من الـ schema الفعلي (الافتراضي أيضاً)
            cur, ok = c.execute("SELECT current_schema(), COALESCE(has_schema_privilege(current_schema(),'CREATE'),false)").fetchone()
            if not cur or not ok:
                raise RuntimeError(f"لا يوجد schema صالح للكتابة (الحالي: {cur!r}). امنح دور القاعدة صلاحية CREATE على schema «public» أو اضبط INJAZ_DB_SCHEMA.")
            with c.txn():
                c.execute("SELECT pg_advisory_xact_lock(?)", (MIGRATION_LOCK_ID,))
                for stmt in _split_statements(pg_ddl(SCHEMA)):
                    c.execute(stmt)
        else:
            c.executescript(SCHEMA)
        run_migrations(c)
    finally:
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
    return c.execute("INSERT INTO counters(name,val) VALUES(?,1) ON CONFLICT(name) DO UPDATE SET val=counters.val+1 RETURNING val", (name,)).fetchone()[0]


def health():
    """فحص الجاهزية: اتصال القاعدة يعمل. يرجع (True, dialect) أو (False, نوع الخطأ فقط دون أسرار)."""
    c = None
    try:
        c = connect()
        return c.ping(), c.dialect
    except Exception as e:
        return False, type(e).__name__
    finally:
        if c:
            c.close()
