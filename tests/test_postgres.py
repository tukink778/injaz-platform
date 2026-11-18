"""اختبارات خاصة بـ PostgreSQL (تُشغَّل فقط عند ضبط DATABASE_URL): اتصال، مخطط، هجرات، تزامن، معاملات، نسخ/استعادة، تشفير، مفتاح، health."""
import base64, json, os, pathlib, subprocess, sys, tempfile, threading, zipfile
d = tempfile.mkdtemp(prefix="injaz-pg-"); os.environ.update(INJAZ_DATA_DIR=d, INJAZ_REG_MIN_SECONDS="0")
ROOT = pathlib.Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
from app import create_app, config
import importlib; D = importlib.import_module("app.db")
from app.security import enc, dec, blind, audit, audit_verify


def run():
    assert config.IS_PG and D.IS_PG, "DATABASE_URL غير مضبوط"
    app = create_app()
    c = D.connect()
    # 1) اتصال + نوع القاعدة + ssl/إخفاء الأسرار
    assert c.ping() and c.dialect == "postgres"; ok, info = D.health(); assert ok and info == "postgres", (ok, info)
    # 2) كل الجداول
    want = {"departments", "users", "templates", "events", "participants", "certificates", "counters", "audit", "settings", "rate", "portal_tokens", "jobs",
            "schema_migrations", "user_email_connections", "oauth_states"}
    have = set(c.tables()); assert want <= have, want - have
    # 3) الهجرات مرة واحدة + تكرار آمن
    n1 = c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]; D.run_migrations(c); D.run_migrations(c); D.init_db()
    assert c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == n1 == 3
    assert {"academic_status", "gender", "nationality"} <= c.columns("participants")
    # هوية تلقائية + RETURNING
    a = c.insert("INSERT INTO templates(name,config,created_at) VALUES('a','{}',0)", ()); b = c.insert("INSERT INTO templates(name,config,created_at) VALUES('b','{}',0)", ())
    assert isinstance(a, int) and b == a + 1
    # 4) قيود: UNIQUE + FK + CASCADE + أن الخطأ لا يُفسد المعاملة الخارجية (SAVEPOINT)
    with c.txn():
        c.execute("INSERT INTO counters(name,val) VALUES('x',1)")
        try:
            with c.txn(): c.execute("INSERT INTO counters(name,val) VALUES('x',2)")
            raise SystemExit("كان يجب رفض المكرر")
        except SystemExit: raise
        except Exception: pass
        assert c.execute("SELECT val FROM counters WHERE name='x'").fetchone()[0] == 1       # ما زالت المعاملة صالحة
    try:
        c.execute("INSERT INTO events(dept_id,title_ar,event_date,template_id,reg_token,created_at) VALUES(99999,'x','2026-01-01',1,'tk',0)"); raise SystemExit("FK مفقود")
    except SystemExit: raise
    except Exception: pass
    # 5) rollback كامل
    try:
        with c.txn():
            c.execute("INSERT INTO counters(name,val) VALUES('rb',1)"); raise RuntimeError("boom")
    except RuntimeError: pass
    assert c.execute("SELECT 1 FROM counters WHERE name='rb'").fetchone() is None
    # 6) تزامن: العداد التسلسلي بلا تكرار ولا فجوات تحت 16 خيطاً × 25
    seen, errs = [], []
    def w():
        try:
            cc = D.connect()
            for _ in range(25):
                with cc.txn(immediate=True): seen.append(D.next_counter(cc, "serial:T:2026"))
            cc.close()
        except Exception as e: errs.append(repr(e))
    th = [threading.Thread(target=w) for _ in range(16)]; [t.start() for t in th]; [t.join() for t in th]
    assert not errs, errs[:2]; assert sorted(seen) == list(range(1, 401)), "تكرار/فجوة في الأرقام التسلسلية"
    # 7) سلسلة التدقيق تحت التزامن
    from flask import g
    def wa():
        with app.test_request_context("/"):
            cx = D.connect()
            for i in range(10): audit("t.concurrent", str(i), "x", conn=cx, user=None)
            cx.close()
    th = [threading.Thread(target=wa) for _ in range(8)]; [t.start() for t in th]; [t.join() for t in th]
    ok, bad = audit_verify(c); assert ok, f"السلسلة انكسرت عند {bad}"
    assert c.execute("SELECT COUNT(*) FROM audit WHERE action='t.concurrent'").fetchone()[0] == 80
    # 8) التشفير: ذهاب وإياب + لا نص صريح
    e = enc("عائشة"); assert dec(e) == "عائشة" and "عائشة" not in e; assert blind("e", "A@x.com") == blind("e", "a@x.com")
    # 9) نسخ احتياطي ثم استعادة إلى قاعدة فارغة (يشمل مزامنة المتسلسل)
    env = dict(os.environ)
    run_ = lambda *a: subprocess.run([sys.executable, str(ROOT / "manage.py"), *a], capture_output=True, text=True, env=env, cwd=str(ROOT))
    c.execute("INSERT INTO users(username,full_name,pw_hash,role,created_at) VALUES('bk','x','h','admin',0)")
    r = run_("backup"); assert r.returncode == 0, r.stderr[-800:]
    z = sorted((pathlib.Path(d) / "backups").glob("injaz-*.zip"))[-1]
    with zipfile.ZipFile(z) as zz: assert "pgdump.json" in zz.namelist(); assert config.DATABASE_URL.split(":", 2)[2].split("@")[0].encode() not in zz.read("pgdump.json")
    r = run_("restore", str(z)); assert r.returncode != 0, "الاستعادة فوق بيانات موجودة يجب أن تُرفض دون --force"
    nusers = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    r = run_("restore", str(z), "--force"); assert r.returncode == 0, r.stdout[-500:] + r.stderr[-800:]
    assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == nusers
    nid = c.insert("INSERT INTO templates(name,config,created_at) VALUES('after-restore','{}',0)", ()); assert nid > b
    ok, bad = audit_verify(c); assert ok, "سلسلة التدقيق بعد الاستعادة"
    # 10) فحص مفتاح التشفير: مفتاح خاطئ يمنع التشغيل ولا يغيّر البيانات
    bad_env = dict(env, INJAZ_MASTER_KEY=base64.urlsafe_b64encode(os.urandom(32)).decode())
    r = subprocess.run([sys.executable, "-c", "from app import create_app; create_app()"], capture_output=True, text=True, env=bad_env, cwd=str(ROOT))
    assert r.returncode != 0 and "INJAZ_MASTER_KEY" in (r.stderr + r.stdout), "مفتاح خاطئ لم يُرفض"
    # 11) غياب المفتاح في وضع PG يُرفض (لا توليد تلقائي)
    nk = {k: v for k, v in env.items() if k != "INJAZ_MASTER_KEY"}
    r = subprocess.run([sys.executable, "-c", "import app.config"], capture_output=True, text=True, env=nk, cwd=str(ROOT))
    assert r.returncode != 0 and not (pathlib.Path(d) / ".keys").exists(), "تم توليد مفتاح تلقائياً!"
    # 12) لا تسرّب لكلمة المرور في رسالة خطأ اتصال
    url = config.DATABASE_URL; pw = url.split(":", 2)[2].split("@")[0]
    r = subprocess.run([sys.executable, "-c", "from app import db; db.connect()"], capture_output=True, text=True,
                       env=dict(env, DATABASE_URL=url.replace("54329", "54399"), INJAZ_DB_CONNECT_WAIT="2"), cwd=str(ROOT))
    assert r.returncode != 0 and pw not in r.stdout + r.stderr, "سرّ في رسالة الخطأ أو لم يفشل الاتصال"
    # 13) /healthz
    cl = app.test_client(); rr = cl.get("/healthz"); assert rr.status_code == 200 and rr.get_json() == {"status": "ok", "db": "postgres"}
    c.close(); print("POSTGRES OK")


if __name__ == "__main__":
    run()
