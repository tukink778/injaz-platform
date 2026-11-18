#!/usr/bin/env python3
"""أوامر الإدارة:
  python manage.py init                         تهيئة القاعدة وإنشاء حساب المشرف العام (مرة واحدة)
  python manage.py adduser USERNAME ROLE [DEPT_CODE]   إضافة مستخدم (ROLE: admin|manager|organizer|viewer) بكلمة مرور عشوائية
  python manage.py demo                         حسابات تجريبية لكل صلاحية بكلمات مرور عشوائية (للتجربة فقط، لا تُستخدم في الإنتاج)
  python manage.py backup                       نسخة احتياطية (قاعدة + شهادات + أصول) مع حذف الأقدم من INJAZ_BACKUP_KEEP
  python manage.py restore FILE.zip             استعادة نسخة احتياطية (يوقف الكتابة فوق قاعدة موجودة إلا مع --force)
  python manage.py verify-audit                 التحقق من سلامة سجل التدقيق
  python manage.py migrate                      تطبيق هجرات القاعدة (تُطبَّق تلقائياً عند التشغيل أيضاً)"""
import sys, secrets, time, zipfile, datetime
from app import create_app, config
from app.db import connect
from app.security import hash_pw, audit_verify

ROLES = ("admin", "manager", "organizer", "viewer")


def _newpw():
    return secrets.token_urlsafe(12) + "aA1"


def init():
    create_app()
    c = connect()
    if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        pw = _newpw()
        c.execute("INSERT INTO users(username,full_name,pw_hash,role,must_change_pw,created_at) VALUES('admin','المشرف العام',?,'admin',1,?)", (hash_pw(pw), int(time.time())))
        print("=" * 56, "\n  تم إنشاء حساب المشرف العام\n  اسم المستخدم : admin\n  كلمة المرور  :", pw, "\n  (مؤقتة: سيُطلب تغييرها عند أول دخول، ولن تظهر مرة أخرى)\n" + "=" * 56)
    else:
        print("النظام مهيأ مسبقاً.")


def adduser(username, role, dept_code=None):
    create_app(); c = connect()
    if role not in ROLES: sys.exit("الدور غير صالح. المتاح: " + " | ".join(ROLES))
    if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone(): sys.exit("اسم المستخدم موجود")
    dept = None
    if role != "admin":
        r = c.execute("SELECT id FROM departments WHERE code=?" if dept_code else "SELECT id FROM departments ORDER BY id LIMIT 1", (dept_code,) if dept_code else ()).fetchone()
        if not r: sys.exit("القسم غير موجود")
        dept = r[0]
    pw = _newpw()
    c.execute("INSERT INTO users(username,full_name,pw_hash,role,dept_id,must_change_pw,created_at) VALUES(?,?,?,?,?,1,?)", (username, username, hash_pw(pw), role, dept, int(time.time())))
    print(f"{username} ({role}) — كلمة المرور المؤقتة: {pw}")


def demo():
    if config.Cfg.HTTPS and "--force" not in sys.argv: sys.exit("وضع الإنتاج مفعّل (INJAZ_HTTPS=1): الحسابات التجريبية ممنوعة. استخدم --force إن كنت متأكداً.")
    create_app(); c = connect(); out = []
    for u, role in (("demo_manager", "manager"), ("demo_organizer", "organizer"), ("demo_viewer", "viewer")):
        if c.execute("SELECT 1 FROM users WHERE username=?", (u,)).fetchone(): out.append((u, role, "(موجود مسبقاً)")); continue
        d = c.execute("SELECT id FROM departments ORDER BY id LIMIT 1").fetchone()[0]; pw = _newpw()
        c.execute("INSERT INTO users(username,full_name,pw_hash,role,dept_id,must_change_pw,created_at) VALUES(?,?,?,?,?,1,?)", (u, "حساب تجريبي — " + role, hash_pw(pw), role, d, int(time.time())))
        out.append((u, role, pw))
    print("حسابات تجريبية (كلمات مرور عشوائية مؤقتة تُغيَّر عند أول دخول؛ احذف الحسابات قبل الإنتاج):")
    for u, role, pw in out: print(f"  {u:16} {role:10} {pw}")


def _pg_dump_json(path):
    """نسخة منطقية من PostgreSQL (JSON لكل جدول) — لا تحتاج pg_dump ولا صلاحيات خاصة، وتعمل على Neon."""
    import base64, json
    c = connect(); out = {"format": "injaz-pg-1", "tables": {}}
    with c.txn():                                   # لقطة متسقة واحدة
        for t in c.tables():
            cur = c.execute(f"SELECT * FROM {t}")
            cols = [d[0] for d in cur.description]
            rows = []
            for r in cur.fetchall():
                rows.append([{"__b64": base64.b64encode(bytes(v)).decode()} if isinstance(v, (bytes, bytearray, memoryview)) else v for v in r])
            out["tables"][t] = {"cols": cols, "rows": rows}
    c.close()
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


def _fk_order(c):
    """ترتيب الجداول بحيث يأتي الأب قبل الابن (للاستعادة)."""
    tabs = c.tables(); deps = {t: set() for t in tabs}
    for r in c.execute("""SELECT cl.relname, rf.relname FROM pg_constraint k JOIN pg_class cl ON cl.oid=k.conrelid JOIN pg_class rf ON rf.oid=k.confrelid
                          JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE k.contype='f' AND n.nspname=current_schema()"""):
        if r[0] != r[1]: deps.setdefault(r[0], set()).add(r[1])
    order = []
    while deps:
        ready = sorted(t for t, d in deps.items() if not (d - set(order)))
        if not ready: ready = sorted(deps)         # حلقة غير متوقعة: نكمل بالترتيب الأبجدي
        for t in ready: order.append(t); deps.pop(t)
    return order


def backup():
    out = config.DATA / "backups" / f"injaz-{datetime.datetime.now():%Y%m%d-%H%M%S}.zip"
    tmp = config.DATA / "tmp" / ("snap.json" if config.IS_PG else "snap.db")
    if config.IS_PG:
        _pg_dump_json(tmp)
    else:
        import sqlite3
        s = sqlite3.connect(config.DB_PATH); d = sqlite3.connect(tmp); s.backup(d); d.close(); s.close()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp, "pgdump.json" if config.IS_PG else "injaz.db")
        for f in (config.DATA / "certs").glob("*.enc"): z.write(f, "certs/" + f.name)
        for f in (config.DATA / "assets").glob("*.jpg"): z.write(f, "assets/" + f.name)
        for f in (config.DATA / "assets").glob("*.png"): z.write(f, "assets/" + f.name)
    tmp.unlink()
    old = sorted((config.DATA / "backups").glob("injaz-*.zip"))[:-max(1, config.Cfg.BACKUP_KEEP)]
    for f in old: f.unlink()
    print("تم:", out, f"(احتُفظ بآخر {config.Cfg.BACKUP_KEEP} نسخة)", "\nتنبيه: النسخة لا تتضمن مفتاح التشفير (INJAZ_MASTER_KEY أو data/.keys). احفظه منفصلاً في مكان آمن.")
    if config.IS_PG: print("تنبيه: ملفات PDF تُحفظ ضمن data/certs؛ على Render بلا قرص دائم لا تبقى بين عمليات النشر (انظر docs/POSTGRES_NEON_RENDER.md).")


def _restore_pg(z, force):
    import base64, json
    create_app(); c = connect()
    if c.execute("SELECT 1 FROM users LIMIT 1").fetchone() and not force:
        sys.exit("القاعدة الحالية تحتوي بيانات. أوقف الخدمة وخذ نسخة، ثم كرر الأمر مع --force لاستبدالها.")
    d = json.loads(z.read("pgdump.json").decode("utf-8"))
    if d.get("format") != "injaz-pg-1": sys.exit("صيغة نسخة غير معروفة")
    order = [t for t in _fk_order(c) if t in d["tables"]]
    known = set(c.tables()); extra = [t for t in d["tables"] if t not in known]
    if extra: sys.exit("جداول غير موجودة في القاعدة (شغّل migrate أولاً): " + ", ".join(extra))
    with c.txn():
        for t in reversed(order): c.execute(f"DELETE FROM {t}")
        for t in order:
            cols, rows = d["tables"][t]["cols"], d["tables"][t]["rows"]
            if not rows: continue
            sql = f"INSERT INTO {t}({','.join(cols)}) OVERRIDING SYSTEM VALUE VALUES({','.join('?' * len(cols))})" if "id" in cols else \
                  f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
            c.executemany(sql, [tuple(base64.b64decode(v["__b64"]) if isinstance(v, dict) and "__b64" in v else v for v in r) for r in rows])
            if "id" in cols:                          # مزامنة المتسلسل كي لا يتصادم الإدخال التالي مع المعرفات المستعادة
                c.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), COALESCE((SELECT MAX(id) FROM {t}),0)+1, false)")


def restore(path):
    force = "--force" in sys.argv
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith("/") or ".." in n: sys.exit("ملف نسخة غير آمن")
        if config.IS_PG:
            if "pgdump.json" not in z.namelist(): sys.exit("هذه نسخة SQLite ولا تُستعاد إلى PostgreSQL مباشرة (الترحيل يتطلب موافقة وخطوات منفصلة).")
            _restore_pg(z, force)
        else:
            if "injaz.db" not in z.namelist(): sys.exit("هذه نسخة PostgreSQL؛ شغّل الاستعادة مع DATABASE_URL.")
            if config.DB_PATH.exists() and not force: sys.exit("توجد قاعدة بيانات حالية. أوقف الخدمة وكرر الأمر مع --force لاستبدالها (خذ نسخة أولاً).")
            z.extract("injaz.db", config.DATA / "tmp")
            (config.DATA / "tmp" / "injaz.db").replace(config.DB_PATH)
        for n in z.namelist():
            if n.startswith(("certs/", "assets/")) and not n.endswith("/"):
                tgt = config.DATA / n; tgt.parent.mkdir(exist_ok=True); tgt.write_bytes(z.read(n))
    print("تمت الاستعادة. تأكد أن INJAZ_MASTER_KEY (أو data/.keys) هو نفس مفتاح النسخة الأصلية.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    args = [a for a in sys.argv[2:] if not a.startswith("--")]
    if cmd == "init": init()
    elif cmd == "adduser" and len(args) >= 2: adduser(*args[:3])
    elif cmd == "demo": demo()
    elif cmd == "backup": create_app(); backup()
    elif cmd == "restore" and args: restore(args[0])
    elif cmd == "purge-trash":
        # حذف نهائي للفعاليات التي تجاوزت مدة السلة الآن (يعمل تلقائياً أيضاً أثناء تشغيل الخدمة).
        app = create_app()
        with app.app_context():
            from app import services
            print("حُذفت نهائياً:", services.purge_expired_trash(connect()), "فعالية")
    elif cmd == "migrate": create_app(); print("الهجرات مطبّقة:", [r[0] for r in connect().execute("SELECT name FROM schema_migrations ORDER BY name")])
    elif cmd == "verify-audit":
        create_app(); ok, bad = audit_verify(connect()); print("سجل التدقيق سليم ✔" if ok else f"⚠ تم العبث بالسجل عند السطر {bad}")
    else: print(__doc__)
