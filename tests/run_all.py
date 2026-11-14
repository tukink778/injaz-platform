"""يشغّل كل الاختبارات في عمليات منفصلة ويطبع الحصيلة الفعلية.  python tests/run_all.py"""
import pathlib, subprocess, sys
here = pathlib.Path(__file__).parent; fails = 0
for t in ("test_migrations.py", "test_templates.py", "test_e2e.py", "test_authz.py"):
    r = subprocess.run([sys.executable, str(here / t)], capture_output=True, text=True)
    ok = r.returncode == 0
    print(("PASS " if ok else "FAIL ") + t, "|", (r.stdout.strip().splitlines() or [""])[-1][:80])
    if not ok: fails += 1; print(r.stderr[-1500:])
sys.exit(1 if fails else 0)
