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


def backup():
    out = config.DATA / "backups" / f"injaz-{datetime.datetime.now():%Y%m%d-%H%M%S}.zip"
    tmp = config.DATA / "tmp" / "snap.db"
    import sqlite3
    s = sqlite3.connect(config.DB_PATH); d = sqlite3.connect(tmp); s.backup(d); d.close(); s.close()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp, "injaz.db")
        for f in (config.DATA / "certs").glob("*.enc"): z.write(f, "certs/" + f.name)
        for f in (config.DATA / "assets").glob("*.jpg"): z.write(f, "assets/" + f.name)
        for f in (config.DATA / "assets").glob("*.png"): z.write(f, "assets/" + f.name)
    tmp.unlink()
    old = sorted((config.DATA / "backups").glob("injaz-*.zip"))[:-max(1, config.Cfg.BACKUP_KEEP)]
    for f in old: f.unlink()
    print("تم:", out, f"(احتُفظ بآخر {config.Cfg.BACKUP_KEEP} نسخة)", "\nتنبيه: النسخة لا تتضمن مفتاح التشفير (data/.keys). احفظه منفصلاً في مكان آمن وإلا لن تُقرأ البيانات.")


def restore(path):
    if config.DB_PATH.exists() and "--force" not in sys.argv: sys.exit("توجد قاعدة بيانات حالية. أوقف الخدمة وكرر الأمر مع --force لاستبدالها (خذ نسخة قبل ذلك).")
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith("/") or ".." in n: sys.exit("ملف نسخة غير آمن")
        z.extract("injaz.db", config.DATA / "tmp")
        (config.DATA / "tmp" / "injaz.db").replace(config.DB_PATH)
        for n in z.namelist():
            if n.startswith(("certs/", "assets/")) and not n.endswith("/"):
                tgt = config.DATA / n; tgt.parent.mkdir(exist_ok=True); tgt.write_bytes(z.read(n))
    print("تمت الاستعادة. تأكد أن data/.keys (أو INJAZ_MASTER_KEY) هو نفس مفتاح النسخة الأصلية.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    args = [a for a in sys.argv[2:] if not a.startswith("--")]
    if cmd == "init": init()
    elif cmd == "adduser" and len(args) >= 2: adduser(*args[:3])
    elif cmd == "demo": demo()
    elif cmd == "backup": create_app(); backup()
    elif cmd == "restore" and args: restore(args[0])
    elif cmd == "migrate": create_app(); print("الهجرات مطبّقة:", [r[0] for r in connect().execute("SELECT name FROM schema_migrations ORDER BY name")])
    elif cmd == "verify-audit":
        create_app(); ok, bad = audit_verify(connect()); print("سجل التدقيق سليم ✔" if ok else f"⚠ تم العبث بالسجل عند السطر {bad}")
    else: print(__doc__)
