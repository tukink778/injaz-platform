"""يشغّل كل الاختبارات في عمليات منفصلة ويطبع الحصيلة الفعلية.
  SQLite (افتراضي):   python tests/run_all.py
  PostgreSQL:         DATABASE_URL=postgresql://... python tests/run_all.py      (يستعمل قاعدة اختبار فقط، لا إنتاج!)
على PostgreSQL يُعزل كل ملف اختبار في schema مؤقت يُحذف بعده، ويُولَّد له مفتاح تشفير عشوائي مؤقت."""
import base64, os, pathlib, secrets, subprocess, sys
here = pathlib.Path(__file__).parent; fails = 0
url = os.environ.get("DATABASE_URL") or os.environ.get("INJAZ_DATABASE_URL", "")
if url:
    import psycopg
    if "--i-know-this-is-a-test-db" not in sys.argv and not any(h in url for h in ("127.0.0.1", "localhost", "test")):
        sys.exit("رفض: الرابط لا يبدو قاعدة اختبار. أضف --i-know-this-is-a-test-db إن كنت متأكداً (تُنشأ schemas مؤقتة وتُحذف).")
print("قاعدة البيانات:", "PostgreSQL" if url else "SQLite")
for t in ("test_migrations.py", "test_templates.py", "test_e2e.py", "test_authz.py", "test_emailconn.py", "test_db_init.py", "test_postgres.py"):
    if t == "test_postgres.py" and not url: continue
    env = dict(os.environ); schema = ""
    if url:
        schema = "t_" + t[5:-3] + "_" + secrets.token_hex(3)
        env.update(INJAZ_DB_SCHEMA=schema, INJAZ_MASTER_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    r = subprocess.run([sys.executable, str(here / t)], capture_output=True, text=True, env=env)
    ok = r.returncode == 0
    print(("PASS " if ok else "FAIL ") + t, "|", (r.stdout.strip().splitlines() or [""])[-1][:80])
    if not ok: fails += 1; print(r.stdout[-800:]); print(r.stderr[-2000:])
    if url:
        with psycopg.connect(url, autocommit=True) as cx: cx.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
sys.exit(1 if fails else 0)
