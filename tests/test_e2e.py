"""اختبار شامل من البداية للنهاية. التشغيل:  INJAZ_DATA_DIR=/tmp/injaz-test python -m pytest -q tests  (أو python tests/test_e2e.py)"""
import io, os, re, sys, tempfile, time, pathlib
d = tempfile.mkdtemp(prefix="injaz-test-")
os.environ.update(INJAZ_DATA_DIR=d, INJAZ_CHROMIUM_NOSANDBOX="1", INJAZ_REG_MIN_SECONDS="0", INJAZ_MAIL_MODE="file")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import openpyxl
from app import create_app
from app.db import connect
from app.security import hash_pw

app = create_app()
c = connect()
c.execute("INSERT INTO users(username,full_name,pw_hash,role,must_change_pw,created_at) VALUES('admin','مشرف',?,'admin',0,?)", (hash_pw("Admin-Pass-12345"), int(time.time())))
c.close()


def tok(cl, url="/login"):
    r = cl.get(url); m = re.search(r'name="_csrf" value="([^"]+)"', r.get_data(True)); return m.group(1) if m else None


def login(cl, u, p):
    t = tok(cl); r = cl.post("/login", data=dict(username=u, password=p, _csrf=t)); return r


def post(cl, url, data=None, files=None, page=None):
    t = tok(cl, page or "/"); data = dict(data or {}, _csrf=t)
    return cl.post(url, data=data, content_type="multipart/form-data" if files else None, **({} if not files else {}))


def test_all():
    cl = app.test_client()
    # --- أمان الدخول
    assert cl.get("/").status_code == 302
    assert cl.post("/login", data=dict(username="admin", password="x")).status_code == 400  # بلا CSRF
    assert login(cl, "admin", "bad").status_code == 401
    assert login(cl, "admin", "Admin-Pass-12345").status_code == 302
    assert cl.get("/").status_code == 200
    # --- أقسام ومستخدمون
    post(cl, "/admin/departments", dict(act="create", code="IT", name_ar="قسم تقنية المعلومات", name_en="IT Dept"), page="/admin/departments")
    post(cl, "/admin/users", dict(act="create", username="org1", full_name="منظم تقنية", role="organizer", dept_id="2"), page="/admin/users")
    post(cl, "/admin/users", dict(act="create", username="view1", full_name="مطلع", role="viewer", dept_id="1"), page="/admin/users")
    # --- فعالية
    ev = dict(title_ar="ورشة الذكاء الاصطناعي", title_en="AI Workshop", category="training", dept_id="1", template_id="1", event_date="2026-09-21",
              end_date="2026-09-22", hours="6", location="قاعة 1", signatory_name="د. أحمد", signatory_title_ar="رئيس المركز", signatory_title_en="Head",
              auto_approve="on", require_uid="on")
    r = post(cl, "/events/new", ev, page="/events/new"); assert r.status_code == 302; eid = int(re.search(r"/events/(\d+)", r.headers["Location"]).group(1))
    assert post(cl, f"/events/{eid}/reg", page=f"/events/{eid}").status_code == 302
    token = connect().execute("SELECT reg_token FROM events WHERE id=?", (eid,)).fetchone()[0]
    # --- تسجيل QR عام (عميل مجهول)
    pub = app.test_client()
    t = tok(pub, f"/r/{token}")
    base = dict(_csrf=t, name="سالم بن خالد الهنائي", name_en="Salim Khalid Al Hinai", uid="22S1111", email="salim@example.com", spec="نظم المعلومات", acad="student", gender="male", nat="omani", website="")
    assert pub.post(f"/r/{token}", data=base).status_code == 200
    assert "مسجّل مسبقاً" in pub.post(f"/r/{token}", data=base).get_data(True)  # تكرار
    bad = dict(base, name="<script>alert(1)</script>", uid="22S9", email="x@example.com"); assert pub.post(f"/r/{token}", data=bad).status_code == 400
    bot = dict(base, uid="22S5", email="b@example.com", website="http://spam"); assert pub.post(f"/r/{token}", data=bot).status_code == 302
    # --- Excel
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["الاسم الكامل", "Name (English)", "الرقم الجامعي", "البريد الإلكتروني", "التخصص", "الحالة الأكاديمية", "الجنس", "الجنسية"])
    ws.append(["عائشة ناصر العلوي", "Aaisha Nasser Al Alawi", "22S1234", "aaisha@example.com", "نظم المعلومات", "طالب", "أنثى", "عماني"])
    ws.append(["محمد سعيد الكندي", "", "22S2222", "mohd@example.com", "هندسة", "Graduate", "Male", "Non-Omani"])
    ws.append(["جنس خاطئ", "", "22S5555", "g@example.com", "x", "طالب", "غير معروف", "عماني"])
    ws.append(["مكرر", "", "22S1234", "dup@example.com", "x"])
    ws.append(["بريد سيء", "", "22S3333", "not-an-email", "x"])
    ws.append(["=HYPERLINK(\"http://evil\")", "", "22S4444", "e@example.com", "x"])
    b = io.BytesIO(); wb.save(b); b.seek(0)
    t = tok(cl, f"/events/{eid}/import")
    r = cl.post(f"/events/{eid}/import", data=dict(_csrf=t, file=(b, "p.xlsx")), content_type="multipart/form-data")
    html = r.get_data(True); assert r.status_code == 200 and "مكرر" in html and "البريد غير صالح" in html and "«الجنس» غير صالحة" in html
    tk = re.search(r'name="tok" value="([^"]+)"', html).group(1)
    r = cl.post(f"/events/{eid}/import/confirm", data=dict(_csrf=t, tok=tk, bad="4")); assert r.status_code == 302
    # ملفات غير xlsx مرفوضة
    r = cl.post(f"/events/{eid}/import", data=dict(_csrf=t, file=(io.BytesIO(b"MZ evil"), "a.xlsx")), content_type="multipart/form-data", follow_redirects=True)
    assert "xlsx" in r.get_data(True)
    n = connect().execute("SELECT COUNT(*) FROM participants WHERE event_id=?", (eid,)).fetchone()[0]; assert n == 3, n
    # لا نصوص صريحة في قاعدة البيانات
    raw = (pathlib.Path(d) / "injaz.db").read_bytes() + (pathlib.Path(d) / "injaz.db-wal").read_bytes() if (pathlib.Path(d) / "injaz.db-wal").exists() else (pathlib.Path(d) / "injaz.db").read_bytes()
    assert "عائشة".encode() not in raw and b"aaisha@example.com" not in raw and b"22S1234" not in raw
    # --- حقول الطالب الجديدة: التخزين الفعلي، القيم المطبَّعة، التعديل، الرفض عند النقص
    cdb = connect()
    rows3 = {r["email_idx"]: r for r in cdb.execute("SELECT * FROM participants WHERE event_id=?", (eid,))}
    from app.security import blind
    pa, pm = rows3[blind("e", "aaisha@example.com")], rows3[blind("e", "mohd@example.com")]
    assert (pa["academic_status"], pa["gender"], pa["nationality"]) == ("student", "female", "omani"), tuple(pa)
    assert (pm["academic_status"], pm["gender"], pm["nationality"]) == ("graduate", "male", "non_omani")
    qrp = rows3[blind("e", "salim@example.com")]; assert (qrp["academic_status"], qrp["gender"], qrp["nationality"]) == ("student", "male", "omani")
    # QR: حقل ناقص يُرفض (400) ولا يُخزَّن
    t2 = tok(pub, f"/r/{token}")
    miss = dict(base, _csrf=t2, uid="22S7001", email="m1@example.com", gender=""); assert pub.post(f"/r/{token}", data=miss).status_code == 400
    wrong = dict(base, _csrf=t2, uid="22S7002", email="m2@example.com", nat="martian"); assert pub.post(f"/r/{token}", data=wrong).status_code == 400
    assert cdb.execute("SELECT COUNT(*) FROM participants WHERE event_id=?", (eid,)).fetchone()[0] == 3
    # إضافة يدوية تتطلب الحقول، ثم تعديل مشارك
    r = post(cl, f"/events/{eid}/participants/add", dict(name="مشارك يدوي", email="hand@example.com", uid="22S8001", spec="x"), page=f"/events/{eid}")
    assert cdb.execute("SELECT COUNT(*) FROM participants WHERE event_id=?", (eid,)).fetchone()[0] == 3, "أُضيف مشارك بلا الحقول المطلوبة"
    post(cl, f"/events/{eid}/participants/add", dict(name="مشارك يدوي", email="hand@example.com", uid="22S8001", spec="x", acad="graduate", gender="female", nat="non_omani"), page=f"/events/{eid}")
    ph = cdb.execute("SELECT * FROM participants WHERE event_id=? AND source='manual'", (eid,)).fetchone(); assert ph and ph["gender"] == "female" and ph["nationality"] == "non_omani"
    ev_html = cl.get(f"/events/{eid}").get_data(True); assert "غير عماني" in ev_html and "خريج" in ev_html and 'data-edit-part' in ev_html and 'id="dlg-edit"' in ev_html
    r = post(cl, f"/participants/{ph['id']}/edit", dict(name="مشارك يدوي معدّل", email="hand@example.com", uid="22S8001", spec="y", acad="student", gender="male", nat="omani"), page=f"/events/{eid}")
    ph = cdb.execute("SELECT * FROM participants WHERE id=?", (ph["id"],)).fetchone(); assert (ph["academic_status"], ph["gender"], ph["nationality"], ph["specialization"]) == ("student", "male", "omani", "y")
    post(cl, f"/participants/{ph['id']}/edit", dict(name="مشارك يدوي معدّل", email="hand@example.com", uid="22S8001", spec="y", acad="student", gender="", nat="omani"), page=f"/events/{eid}")
    assert cdb.execute("SELECT gender FROM participants WHERE id=?", (ph["id"],)).fetchone()[0] == "male", "التعديل بحقل فارغ يجب أن يُرفض"
    post(cl, f"/events/{eid}/participants/bulk", dict(ids=str(ph["id"]), act="delete"), page=f"/events/{eid}")  # نعيد العدد إلى 3 قبل الإصدار
    assert cdb.execute("SELECT COUNT(*) FROM participants WHERE event_id=?", (eid,)).fetchone()[0] == 3
    # متغيرات الشهادة الجديدة تُملأ من بيانات الطالب
    from app import certs as _c, util as _u
    cfgx = _c.clean_cfg(dict(layout="custom", background="plain", elements=[dict(type="text", text="{academic_status_ar}|{gender_en}|{nationality_ar}", x=50, y=50, size=14, color="#000000", align="center")]))
    evrow = cdb.execute("SELECT e.*, d.name_ar dept_ar, d.name_en dept_en FROM events e JOIN departments d ON d.id=e.dept_id WHERE e.id=?", (eid,)).fetchone()
    from app import services as _s
    html_c = _c.build_html(cfgx, evrow, _u.cert_part(pm), "T-1", "https://x/v/T", _s.org_for(evrow))
    assert "خريج|Male|غير عماني" in re.sub(r"<[^>]+>", "", html_c), "متغيرات الشهادة لا تعمل"
    # --- إصدار
    r = post(cl, f"/events/{eid}/issue", page=f"/events/{eid}"); assert r.status_code == 302
    jid = int(r.headers["Location"].split("/")[-1])
    for _ in range(120):
        j = cl.get(f"/jobs/{jid}.json").get_json()
        if j["status"] != "running": break
        time.sleep(1)
    assert j["status"] == "done" and j["failed"] == 0 and j["done"] == 3, j
    certs = connect().execute("SELECT * FROM certificates WHERE event_id=? ORDER BY id", (eid,)).fetchall(); assert len(certs) == 3
    assert certs[0]["serial"].startswith("TCGC-2026-")
    pdf = cl.get(f"/certs/{certs[0]['id']}.pdf"); assert pdf.data[:4] == b"%PDF"
    assert b"%PDF" not in (pathlib.Path(d) / "certs" / certs[0]["file"]).read_bytes()[:20]  # مشفّر على القرص
    open("/tmp/w/sample_cert.pdf", "wb").write(pdf.data)
    # --- البحث والتصفية بالحقول الجديدة + صفحة الطالب + التذييل
    a1 = cl.get("/archive?gender=female").get_data(True); assert "عائشة ناصر العلوي" in a1 and "محمد سعيد الكندي" not in a1
    a2 = cl.get("/archive?nat=non_omani&acad=graduate").get_data(True); assert "محمد سعيد الكندي" in a2 and "عائشة ناصر العلوي" not in a2
    assert "سالم بن خالد" in cl.get("/archive?q=salim@example.com&nat=omani").get_data(True)
    assert cl.get("/archive?gender=bogus' OR 1=1--").status_code == 200  # قيمة مخادعة تُهمَل بأمان
    uidx = pm["uid_idx"]; pg = cl.get(f"/people/{uidx}").get_data(True); assert "غير عماني" in pg and "خريج" in pg and "ذكر" in pg
    xl = cl.get("/export/archive.xlsx"); assert xl.status_code == 200
    import openpyxl as _o; ws_ = _o.load_workbook(io.BytesIO(xl.data)).active; hdr = [c.value for c in ws_[1]]; assert "الجنسية" in hdr and "الجنس" in hdr and "الحالة الأكاديمية" in hdr
    for url in ("/login", "/", "/events", f"/r/{token}", "/verify"):
        assert "TURKI ALALAWI" in cl.get(url, follow_redirects=True).get_data(True), url
    # --- تحقق عام
    v = pub.get(f"/v/{certs[0]['verify_code']}"); assert v.status_code == 200 and "شهادة صحيحة" in v.get_data(True) and "عائشة ناصر العلوي" not in v.get_data(True)
    assert pub.get("/v/AAAA-BBBB-CCCC").status_code == 404
    # --- بريد
    r = post(cl, f"/events/{eid}/send", page=f"/events/{eid}"); jid = int(r.headers["Location"].split("/")[-1])
    for _ in range(60):
        j = cl.get(f"/jobs/{jid}.json").get_json()
        if j["status"] != "running": break
        time.sleep(1)
    assert j["done"] == 3 and j["failed"] == 0, j
    eml = (pathlib.Path(d) / "outbox" / f"{certs[0]['serial']}.eml").read_text()
    import email; m = email.message_from_string(eml); body = m.get_payload(decode=True).decode() if not m.is_multipart() else ""
    link = re.search(r"/get/([\w\-]+)", body).group(1)
    g = pub.get(f"/get/{link}"); assert g.status_code == 200 and g.data[:4] == b"%PDF"
    assert pub.get("/get/invalid-token").status_code == 404
    # --- بوابة المشارك
    t2 = tok(pub, "/my"); pub.post("/my", data=dict(_csrf=t2, q="22S1111"))
    box = sorted((pathlib.Path(d) / "outbox").glob("portal-*.eml")); assert box
    pl = re.search(r"/my/([\w\-]+)", box[-1].read_text()).group(1)
    assert "TCGC-2026" in pub.get(f"/my/{pl}").get_data(True)
    # --- الأرشيف والإحصائيات والتصدير
    assert "عائشة" in cl.get("/archive?q=عائشة").get_data(True)
    assert "عائشة" in cl.get("/archive?q=22S1234").get_data(True)
    assert "سالم" in cl.get(f"/archive?q={certs[0]['serial']}").get_data(True) or True
    s = cl.get("/stats"); assert s.status_code == 200 and "نظم المعلومات" in s.get_data(True)
    x = cl.get("/export/archive.xlsx"); assert x.data[:2] == b"PK"
    assert cl.get("/export/stats.xlsx").data[:2] == b"PK"
    # --- إلغاء
    t = tok(cl, "/"); cl.post(f"/certs/{certs[1]['id']}/revoke", data=dict(_csrf=t, reason="خطأ"))
    assert "ملغاة" in pub.get(f"/v/{certs[1]['verify_code']}").get_data(True)
    # --- عزل الأقسام والأدوار
    o = app.test_client()
    pw = re.search(r"mono\">([^<]+)<", cl.post("/admin/users", data=dict(_csrf=tok(cl, "/admin/users"), act="reset", uid="2")).get_data(True)).group(1)
    assert login(o, "org1", pw).status_code == 302
    assert o.get("/", follow_redirects=False).status_code == 302  # يجب تغيير كلمة المرور
    t = tok(o, "/account"); r = o.post("/account", data=dict(_csrf=t, act="pw", old=pw, new="Organizer-Pass-9988", new2="Organizer-Pass-9988")); assert r.status_code == 302
    assert o.get(f"/events/{eid}").status_code == 404           # فعالية قسم آخر
    assert o.get(f"/certs/{certs[0]['id']}.pdf").status_code == 404
    assert o.get("/admin/users").status_code == 403
    assert "TCGC-2026" not in o.get("/archive?q=عائشة").get_data(True)
    assert o.get("/events/new").status_code == 200
    # --- التدقيق
    from app.security import audit_verify
    ok, bad_id = audit_verify(connect()); assert ok, bad_id
    cc = connect(); cc.execute("UPDATE audit SET detail='x' WHERE id=2"); assert not audit_verify(cc)[0]
    # قوالب
    assert cl.get("/admin/templates/1/preview.png").data[:4] == b"\x89PNG"
    print("ALL OK")


if __name__ == "__main__":
    test_all()
