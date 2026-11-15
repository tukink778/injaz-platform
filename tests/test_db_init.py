"""تهيئة القاعدة والهجرات: schema افتراضي/مخصص، أول إنشاء، إعادة التشغيل، نسختان متزامنتان، فشل الهجرة والتراجع، SQLite.
يعمل على SQLite دائماً، وعلى PostgreSQL عند ضبط DATABASE_URL (ينشئ قواعد بيانات مؤقتة ويحذفها؛ يلزم صلاحية CREATEDB وإلا يُتخطى الجزء الافتراضي)."""
import base64, importlib, os, pathlib, secrets, subprocess, sys, tempfile, textwrap, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
URL = os.environ.get("DATABASE_URL", "")
KEY = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
BOOT = "from app import create_app; create_app(); print('BOOT-OK')"


def sub(code, env_extra, timeout=120):
    env = {k: v for k, v in os.environ.items() if k not in ("INJAZ_DB_SCHEMA",)}
    env.update(env_extra); env.setdefault("INJAZ_MASTER_KEY", KEY)
    env.setdefault("INJAZ_DATA_DIR", tempfile.mkdtemp(prefix="injaz-di-"))
    return subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def done(p, ok=True):
    out, err = p.communicate(timeout=180)
    assert (p.returncode == 0) == ok, (p.returncode, out[-400:], err[-1500:])
    return out, err


def fake_pkg(tag, slow=1.5):
    d = pathlib.Path(tempfile.mkdtemp(prefix="fakemig-")) / f"fm_{tag}"; d.mkdir(); (d / "__init__.py").write_text("")
    (d / "m0001_probe.py").write_text(textwrap.dedent(f"""
        import time
        def up(c):
            c.ddl("CREATE TABLE mig_probe(x INTEGER)")        # تطبيقها مرتين = خطأ (الجدول موجود)
            time.sleep({slow})                                  # نافذة سباق كبيرة
            c.execute("INSERT INTO mig_probe(x) VALUES(1)")
        """))
    (d / "m0002_boom.py").write_text(textwrap.dedent("""
        def up(c):
            c.ddl("CREATE TABLE half_applied(x INTEGER)")
            c.execute("INSERT INTO half_applied(x) VALUES(1)")
            raise RuntimeError("boom")
        """))
    sys.path.insert(0, str(d.parent)); return importlib.import_module(f"fm_{tag}")


def engine_checks(D, label):
    """يُشغَّل داخل العملية الحالية على الاتصال الحالي (SQLite أو PG/schema مخصص)."""
    D.init_db(); c = D.connect()
    n = c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]; assert n == 2, n
    D.init_db(); D.run_migrations(c)                                   # إعادة التشغيل: لا إعادة تطبيق
    assert c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
    pkg = fake_pkg(label)
    errs = []
    def w():
        cc = D.connect()
        try: D.run_migrations(cc, pkg)
        except RuntimeError as e:
            if str(e) != "boom": errs.append(repr(e))
        except Exception as e: errs.append(repr(e))
        finally: cc.close()
    th = [threading.Thread(target=w) for _ in range(4)]; [t.start() for t in th]; [t.join() for t in th]
    assert not errs, errs
    assert c.execute("SELECT COUNT(*) FROM mig_probe").fetchone()[0] == 1, "الهجرة طُبّقت أكثر من مرة"
    assert c.execute("SELECT COUNT(*) FROM schema_migrations WHERE name='m0001_probe'").fetchone()[0] == 1
    # فشل الهجرة: تراجع كامل (لا جدول نصف منشأ ولا تسجيل)، وما قبلها يبقى مطبَّقاً
    assert "half_applied" not in c.tables(), "بقي جدول من هجرة فاشلة"
    assert c.execute("SELECT 1 FROM schema_migrations WHERE name='m0002_boom'").fetchone() is None
    c.close()


def run():
    # ---- SQLite محلياً (في عملية مستقلة بقاعدة مؤقتة)
    p = sub(textwrap.dedent("""
        import sys; sys.path.insert(0, 'tests'); import importlib, test_db_init as T
        D = importlib.import_module('app.db'); assert not D.IS_PG
        T.engine_checks(D, 'sqlite'); print('SQLITE-ENGINE-OK')"""), {"DATABASE_URL": "", "INJAZ_DATABASE_URL": ""})
    out, _ = done(p); assert "SQLITE-ENGINE-OK" in out
    if not URL:
        print("DB-INIT OK (SQLite فقط)"); return
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from urllib.parse import urlsplit, urlunsplit
    base = conninfo_to_dict(URL); admin = dict(base, dbname="postgres")
    def url_db(name, user=None, pw=None):          # رابط postgresql:// لقاعدة أخرى على الخادم نفسه (config لا يقبل صيغة key=value)
        u = urlsplit(URL); host = u.hostname + (f":{u.port}" if u.port else "")
        cred = f"{user or u.username}:{pw or u.password}@"
        return urlunsplit((u.scheme, cred + host, "/" + name, u.query, ""))
    created = []
    def mkdb():
        name = "injaz_t_" + secrets.token_hex(4)
        with psycopg.connect(make_conninfo(**admin), autocommit=True) as a: a.execute(f'CREATE DATABASE "{name}"')
        created.append(name); return url_db(name)
    try:
        # ---- 1) schema مخصص غير موجود: الاتصال الأول لا يفشل، ويُنشأ تلقائياً، والجداول فيه لا في public
        ci = mkdb(); sch = "cust_" + secrets.token_hex(3)
        env = {"DATABASE_URL": ci, "INJAZ_DB_SCHEMA": sch}
        out, _ = done(sub(BOOT, env)); assert "BOOT-OK" in out
        with psycopg.connect(ci, autocommit=True) as c:
            assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=%s AND table_name='users'", (sch,)).fetchone()[0] == 1
            assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0] == 0, "جداول تسرّبت إلى public"
            assert c.execute(f'SELECT count(*) FROM "{sch}".schema_migrations').fetchone()[0] == 2
            t0 = c.execute(f'SELECT count(*) FROM "{sch}".templates').fetchone()[0]
        # ---- 2) إعادة التشغيل: لا إعادة تطبيق ولا إعادة بذر
        out, _ = done(sub(BOOT, env)); assert "BOOT-OK" in out
        with psycopg.connect(ci, autocommit=True) as c:
            assert c.execute(f'SELECT count(*) FROM "{sch}".schema_migrations').fetchone()[0] == 2
            assert c.execute(f'SELECT count(*) FROM "{sch}".templates').fetchone()[0] == t0
        # ---- 3) اسم schema خبيث يُرفض قبل أي SQL
        for bad in ('x"; DROP TABLE users;--', "a b", "pg_catalog", "1abc", "x" * 64):
            out, err = done(sub(BOOT, dict(env, INJAZ_DB_SCHEMA=bad)), ok=False); assert "schema" in (err + out)
        # ---- 4) schema افتراضي (public) في قاعدة جديدة + نسختان متزامنتان تبدآن معاً من الصفر
        ci2 = mkdb(); env2 = {"DATABASE_URL": ci2}; key = {"INJAZ_MASTER_KEY": KEY}
        ps = [sub(BOOT, dict(env2, **key)) for _ in range(3)]
        for p in ps: out, err = done(p); assert "BOOT-OK" in out, err[-800:]
        with psycopg.connect(ci2, autocommit=True) as c:
            names = [r[0] for r in c.execute("SELECT name FROM schema_migrations ORDER BY name")]
            assert names == ["m0002_student_fields", "m0003_email_connections"], names
            assert c.execute("SELECT count(*) FROM departments").fetchone()[0] == 1, "البذر تكرر عند التزامن"
            assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='users'").fetchone()[0] == 1
        # ---- 5) محرك الهجرات تحت التزامن/الفشل على: public (قاعدة جديدة) ثم schema مخصص
        for tag, e in (("pub", env2), ("cus", {"DATABASE_URL": mkdb(), "INJAZ_DB_SCHEMA": "eng_" + secrets.token_hex(3)})):
            out, err = done(sub(textwrap.dedent(f"""
                import sys; sys.path.insert(0, 'tests'); import importlib, test_db_init as T
                D = importlib.import_module('app.db'); assert D.IS_PG
                T.engine_checks(D, '{tag}'); print('PG-ENGINE-OK')"""), dict(e, **key)))
            assert "PG-ENGINE-OK" in out, err[-800:]
        # ---- 6) دور بلا صلاحية CREATE على schema موجود: رسالة واضحة دون كشف الرابط
        ci3 = mkdb()
        with psycopg.connect(ci3, autocommit=True) as c:
            c.execute("CREATE ROLE injaz_ro LOGIN PASSWORD 'x1y2z3ro' NOSUPERUSER" ) if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='injaz_ro'").fetchone() else None
            c.execute("CREATE SCHEMA locked"); c.execute("REVOKE ALL ON SCHEMA locked FROM PUBLIC"); c.execute("GRANT CONNECT ON DATABASE " + f'"{conninfo_to_dict(ci3)["dbname"]}"' + " TO injaz_ro")
        ro = url_db(conninfo_to_dict(ci3)["dbname"], "injaz_ro", "x1y2z3ro")
        out, err = done(sub(BOOT, {"DATABASE_URL": ro, "INJAZ_DB_SCHEMA": "locked"}), ok=False)
        assert "USAGE" in err and "x1y2z3ro" not in err + out, err[-600:]
    finally:
        with psycopg.connect(make_conninfo(**admin), autocommit=True) as a:
            for n in created: a.execute(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)')
            a.execute("DROP ROLE IF EXISTS injaz_ro")
    print("DB-INIT OK (SQLite + PostgreSQL)")


if __name__ == "__main__":
    run()
