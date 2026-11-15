"""اختبار إدارة قوالب الشهادات: إنشاء/نسخ/تفعيل/حذف مع فحص الاعتماديات، والمحرر المرئي، والصلاحيات، وخلو العينات من بيانات شخصية."""
import json, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_e2e as T   # يجهّز البيئة ومستخدم admin
from test_e2e import app, c, post, login, tok

def tpl(i): return T.connect().execute("SELECT * FROM templates WHERE id=?", (i,)).fetchone()

def run():
    cl = app.test_client(); assert login(cl, "admin", "Admin-Pass-12345").status_code == 302
    db = T.connect()
    n0 = db.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
    # 1) قالب فارغ أفقي وعمودي
    r = post(cl, "/admin/templates/new", dict(mode="blank", orientation="portrait"), page="/admin/templates"); assert r.status_code == 302
    tid = int(re.search(r"/templates/(\d+)", r.headers["Location"]).group(1))
    assert json.loads(tpl(tid)["config"])["orientation"] == "portrait"
    page = cl.get(f"/admin/templates/{tid}"); assert page.status_code == 200 and "editor" in page.get_data(True).lower()
    # 2) حفظ تصميم مخصص بعناصر ومعاينة حيّة PNG
    cfg = dict(layout="custom", orientation="portrait", background="plain", elements=[
        dict(type="text", text="{name}", x=50, y=40, size=28, color="#1d2a7a", align="center"),
        dict(type="text", text="{event_ar} — {hours}", x=50, y=55, size=16, color="#333333", align="center"),
        dict(type="qr", x=80, y=88, size=18)])
    t = tok(cl, f"/admin/templates/{tid}")
    r = cl.post(f"/admin/templates/{tid}", data=dict(_csrf=t, name="قالبي", cfg=json.dumps(cfg)), headers={"X-Requested-With": "fetch"}); assert r.status_code == 200 and r.get_json()["ok"]
    saved = json.loads(tpl(tid)["config"]); assert tpl(tid)["name"] == "قالبي" and len(saved["elements"]) == 3, saved
    r = cl.post("/admin/templates/live", data=dict(_csrf=t, cfg=json.dumps(cfg))); assert r.status_code == 200 and r.data[:4] == b"\x89PNG"
    # عناصر خبيثة/زائدة تُنقّى ولا تكسر
    bad = dict(cfg, elements=[dict(type="text", text="<script>x</script>{name}", x=9999, y=-5, size=9999, color="red")] * 100)
    r = cl.post("/admin/templates/live", data=dict(_csrf=t, cfg=json.dumps(bad))); assert r.status_code == 200
    # 3) نسخ
    r = post(cl, f"/admin/templates/{tid}/duplicate", page="/admin/templates"); assert r.status_code == 302
    did = int(re.search(r"/templates/(\d+)", r.headers["Location"]).group(1)); assert did != tid and tpl(did)["active"] == 1
    # 4) تعطيل وتفعيل
    post(cl, f"/admin/templates/{did}/toggle", page="/admin/templates"); assert tpl(did)["active"] == 0
    post(cl, f"/admin/templates/{did}/toggle", page="/admin/templates"); assert tpl(did)["active"] == 1
    # 5) لا يمكن تعطيل آخر قالب مفعّل
    ids = [r["id"] for r in db.execute("SELECT id FROM templates WHERE active=1")]
    for i in ids[:-1]: db.execute("UPDATE templates SET active=0 WHERE id=?", (i,))
    last = ids[-1]
    post(cl, f"/admin/templates/{last}/toggle", page="/admin/templates"); assert tpl(last)["active"] == 1
    for i in ids[:-1]: db.execute("UPDATE templates SET active=1 WHERE id=?", (i,))
    pass
    # 6) الحذف: قالب مرتبط بفعالية يتطلب نقلاً صريحاً
    dept = db.execute("SELECT id FROM departments LIMIT 1").fetchone()
    if not dept: db.execute("INSERT INTO departments(name_ar,name_en) VALUES('قسم','Dept')"); dept = db.execute("SELECT id FROM departments LIMIT 1").fetchone()
    db.execute("INSERT INTO events(dept_id,title_ar,event_date,template_id,reg_token,created_at) VALUES(?,'فعالية اختبار','2026-01-01',?,'tok-test-1',0)", (dept["id"], did))
    ev = db.execute("SELECT id FROM events WHERE template_id=?", (did,)).fetchone()["id"]
    assert cl.get(f"/admin/templates/{did}/delete").status_code == 200
    post(cl, f"/admin/templates/{did}/delete", dict(confirm="yes"), page=f"/admin/templates/{did}/delete"); assert tpl(did) is not None, "حُذف بلا نقل!"
    post(cl, f"/admin/templates/{did}/delete", dict(confirm="yes", reassign=str(tid)), page=f"/admin/templates/{did}/delete")
    assert tpl(did) is None and db.execute("SELECT template_id FROM events WHERE id=?", (ev,)).fetchone()[0] == tid
    # 7) حذف قالب مدمج غير مستخدم مسموح
    b = db.execute("SELECT id FROM templates WHERE builtin=1 AND id NOT IN (SELECT template_id FROM events WHERE template_id IS NOT NULL) LIMIT 1").fetchone()["id"]
    post(cl, f"/admin/templates/{b}/delete", dict(confirm="yes"), page=f"/admin/templates/{b}/delete"); assert tpl(b) is None
    # 8) الصلاحيات: منظّم عادي ممنوع
    db.execute("INSERT INTO users(username,full_name,pw_hash,role,must_change_pw,created_at) VALUES('org','منظم',?,'organizer',0,0)", (T.hash_pw("Org-Pass-12345!"),))
    oc = app.test_client(); assert login(oc, "org", "Org-Pass-12345!").status_code == 302
    for u in ("/admin/templates", f"/admin/templates/{tid}", "/admin/templates/live"): assert oc.get(u).status_code in (403, 405), u
    rr = oc.post(f"/admin/templates/{tid}/toggle", data=dict(_csrf=tok(oc, "/"))); assert rr.status_code == 403, rr.status_code
    # 9) لا بيانات شخصية في عينات القوالب وقالب Excel
    import openpyxl, io
    wb = openpyxl.load_workbook(io.BytesIO(cl.get("/template.xlsx").data)); txt = " ".join(str(x) for row in wb.active.iter_rows(values_only=True) for x in row if x)
    assert not re.search(r"@(?!example)|عائشة|سالم", txt) and "participant@example.com" in txt, txt
    # 10) خيار الأحرف الكبيرة لا يتلف وسوم HTML ولا يسمح بحقن
    from app import certs as C
    h = str(C.fill("name: {name} <b>{x}</b>", dict(name="Ali & <i>Co</i>", x="v"), upper=True))
    assert 'class="hl"' in h or 'class="bd"' in h, h
    assert "ALI &amp; &lt;I&gt;CO&lt;/I&gt;" in h and "&lt;B&gt;" in h and "<i>" not in h and "<b>" not in h, h
    assert "class=\"BD\"" not in h and "SPAN" not in h
    print("TEMPLATES OK")

if __name__ == "__main__":
    run()
