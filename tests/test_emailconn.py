"""ربط حسابات البريد (OAuth 2.0) — خادم مزوّد وهمي يتحقق فعلياً من state وPKCE وredirect_uri والتجديد والإرسال.
يغطي: ربط Outlook/Gmail، رفض الصلاحيات، الفصل، إعادة الربط، انتهاء رمز الوصول، فشل التجديد، الإرسال عبر Outlook/Gmail،
عزل المستخدمين، عدم ظهور الرموز في السجلات/القاعدة/الصفحات، واستمرار SMTP."""
import base64, email, email.policy, io, json, logging, os, re, sys, time, urllib.parse, pathlib
os.environ.update(INJAZ_MS_CLIENT_ID="ms-client-id", INJAZ_MS_CLIENT_SECRET="ms-client-secret-VALUE",
                  INJAZ_GOOGLE_CLIENT_ID="g-client-id", INJAZ_GOOGLE_CLIENT_SECRET="g-client-secret-VALUE")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_e2e as T
from test_e2e import app, tok, login, connect
from app import emailconn, mailproviders
from app.security import dec
from app.mailproviders import http as mhttp
from app.mailproviders.base import pkce_challenge

GS = "https://www.googleapis.com/auth/gmail.send"


class World:
    """مزوّدان وهميان (Microsoft/Google) خلف طبقة HTTP الوحيدة في المشروع."""
    def __init__(s):
        s.codes, s.access, s.refresh, s.expired, s.sent, s.revoked, s.calls = {}, {}, {}, set(), [], [], []
        s.n = 0; s.refresh_fail = False; s.fail_send_status = 0; s.throttle = 0

    def consent(s, location, account, deny=False, scope=None):
        u = urllib.parse.urlparse(location); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        prov = "microsoft" if "microsoftonline" in u.netloc else "google"
        assert q["response_type"] == "code" and q["code_challenge_method"] == "S256" and q["state"] and q["code_challenge"]
        assert q["client_id"] == {"microsoft": "ms-client-id", "google": "g-client-id"}[prov]
        assert q["redirect_uri"].endswith(f"/account/email/callback/{prov}")
        if deny: return dict(error="access_denied", state=q["state"])
        s.n += 1; code = f"CODE-{s.n}"
        s.codes[code] = dict(prov=prov, challenge=q["code_challenge"], redirect=q["redirect_uri"], account=account, scope=scope if scope is not None else q["scope"])
        return dict(code=code, state=q["state"])

    def _tokens(s, prov, account, scope, rotate=True):
        s.n += 1; a, r = f"ACCESS-SECRET-{s.n}", f"REFRESH-SECRET-{s.n}"
        s.access[a] = (prov, account); s.refresh[r] = (prov, account, scope)
        return dict(access_token=a, refresh_token=r, expires_in=3600, scope=scope, token_type="Bearer")

    def request(s, method, url, *, headers=None, form=None, json_body=None, timeout=20):
        s.calls.append((method, url.split("?")[0]))
        R = mhttp.Resp
        bearer = (headers or {}).get("Authorization", "").replace("Bearer ", "")
        if url.endswith("/token") and ("microsoftonline" in url or "googleapis.com" in url):
            prov = "microsoft" if "microsoftonline" in url else "google"
            assert form["client_id"] == {"microsoft": "ms-client-id", "google": "g-client-id"}[prov] and form["client_secret"].endswith("-VALUE")
            if form["grant_type"] == "authorization_code":
                c = s.codes.pop(form["code"], None)          # الرمز يُستعمل مرة واحدة
                if not c or c["prov"] != prov or c["redirect"] != form["redirect_uri"] or pkce_challenge(form["code_verifier"]) != c["challenge"]:
                    return R(400, dict(error="invalid_grant"), 0)
                t = s._tokens(prov, c["account"], c["scope"])
                if getattr(s, "no_refresh", False): t.pop("refresh_token")
                return R(200, t, 0)
            rec = s.refresh.get(form["refresh_token"])
            if s.refresh_fail or not rec: return R(400, dict(error="invalid_grant", error_description="AADSTS70008 token expired"), 0)
            t = s._tokens(prov, rec[1], rec[2])
            if prov == "google": t.pop("refresh_token");
            else: s.refresh.pop(form["refresh_token"], None)       # Microsoft يدوّر رمز التجديد
            return R(200, t, 0)
        if url.startswith("https://oauth2.googleapis.com/revoke"): s.revoked.append(form["token"]); return R(200, {}, 0)
        if bearer not in s.access or bearer in s.expired: return R(401, dict(error=dict(code="InvalidAuthenticationToken")), 0)
        prov, acc = s.access[bearer]
        if url.startswith("https://graph.microsoft.com/v1.0/me?"): return R(200, dict(id=acc["sub"], mail=acc["email"], userPrincipalName=acc["email"]), 0)
        if url.startswith("https://openidconnect.googleapis.com/v1/userinfo"): return R(200, dict(sub=acc["sub"], email=acc["email"], email_verified=acc.get("verified", True)), 0)
        if s.fail_send_status: return R(s.fail_send_status, {}, 0)
        if s.throttle > 0: s.throttle -= 1; return R(429, {}, 1)
        if url == "https://graph.microsoft.com/v1.0/me/sendMail":
            m = json_body["message"]
            s.sent.append(dict(prov="microsoft", bearer=bearer, acct=acc["email"], to=[r["emailAddress"]["address"] for r in m["toRecipients"]], subject=m["subject"],
                               body=m["body"]["content"], atts=[a["name"] for a in m.get("attachments", [])], frm=None)); return R(202, {}, 0)
        if url == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send":
            raw = base64.urlsafe_b64decode(json_body["raw"] + "=="); m = email.message_from_bytes(raw, policy=email.policy.default)
            s.sent.append(dict(prov="google", bearer=bearer, acct=acc["email"], to=[m["To"]], subject=email.header.decode_header(m["Subject"])[0][0] if m["Subject"] else "",
                               body=(m.get_body(("plain",)).get_content() if m.get_body(("plain",)) else ""), atts=[p.get_filename() for p in m.iter_attachments()], frm=m["From"])); return R(200, dict(id="m1"), 0)
        raise AssertionError("URL غير متوقع: " + url)


W = World(); mhttp.request = W.request
emailconn._sleep = lambda s: None
for p in mailproviders.all_providers(): p.min_interval = 0

# التقاط كل السجلات للتحقق من عدم تسريب الرموز
LOGS = io.StringIO(); _h = logging.StreamHandler(LOGS); _h.setLevel(logging.DEBUG)
logging.getLogger().addHandler(_h); logging.getLogger().setLevel(logging.DEBUG); app.logger.addHandler(_h)
emailconn.install_log_redaction(app)

AL = dict(sub="ms-sub-A", email="alice@contoso.com"); GA = dict(sub="g-sub-A", email="alice@gmail.com"); BOB = dict(sub="ms-sub-B", email="bob@contoso.com")


def start(cl, prov, expect=302):
    page = cl.get("/account").get_data(True)
    href = re.search(rf'href="(/account/email/connect/{prov}\?n=[0-9a-f]+)"', page)
    assert href, f"زر الربط غير ظاهر ({prov})"
    r = cl.get(href.group(1)); assert r.status_code == expect, r.status_code
    return r.headers.get("Location")


def link(cl, prov, account, **kw):
    loc = start(cl, prov)
    q = W.consent(loc, account, **kw)
    r = cl.get(f"/account/email/callback/{prov}?" + urllib.parse.urlencode(q)); assert r.status_code == 302
    return cl.get("/account").get_data(True), loc


def row(uid, prov):
    return connect().execute("SELECT * FROM user_email_connections WHERE user_id=? AND provider=?", (uid, prov)).fetchone()


def wait_job():
    c = connect()
    for _ in range(300):
        j = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
        if j["status"] != "running": return j
        time.sleep(0.1)
    raise AssertionError("المهمة لم تنتهِ")


def send(cl, eid, **f):
    r = cl.post(f"/events/{eid}/send", data=dict(_csrf=tok(cl, f"/events/{eid}"), scope="all", **f))
    assert r.status_code == 302, r.status_code
    return r


def run():
    T.test_all()
    c = connect(); eid = c.execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()[0]
    from app.db import set_setting; set_setting(c, 'attach_pdf', '1')
    ncert = c.execute("SELECT COUNT(*) FROM certificates WHERE event_id=? AND status='valid'", (eid,)).fetchone()[0]; assert ncert >= 2
    uid_admin = c.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
    ad = app.test_client(); assert login(ad, "admin", "Admin-Pass-12345").status_code == 302

    # ---------- 0) الضوابط الأساسية ----------
    assert "Email Accounts" in ad.get("/account").get_data(True)
    assert ad.get("/account/email/connect/microsoft").status_code == 400                      # بلا رمز
    assert ad.get("/account/email/connect/microsoft?n=deadbeef").status_code == 400            # رمز خاطئ
    assert ad.get("/account/email/connect/yahoo?n=x").status_code == 404                       # مزوّد غير معروف
    assert app.test_client().get("/account/email/connect/google?n=x").status_code == 302       # مجهول → تسجيل الدخول
    assert ad.post("/account/email/disconnect/google", data=dict(_csrf="bad")).status_code == 400   # CSRF
    from app.config import Cfg
    old = Cfg.BASE_URL; Cfg.BASE_URL = "http://injaz.example.edu"                                 # ليس HTTPS ولا localhost
    assert not emailconn.https_ok() and "/account/email/connect/" not in ad.get("/account").get_data(True); Cfg.BASE_URL = old

    # ---------- 1) ربط Outlook بنجاح ----------
    html, loc = link(ad, "microsoft", AL)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).items()}
    assert set(q["scope"].split()) == {"offline_access", "User.Read", "Mail.Send"}, q["scope"]          # أقل الصلاحيات، لا Mail.Read
    assert "Mail.Read" not in loc and "client_secret" not in loc and "login.microsoftonline.com" in loc
    assert "تم ربط alice@contoso.com بنجاح" in html and "Connected" in html
    r1 = row(uid_admin, "microsoft"); assert r1["email_address"] == "alice@contoso.com" and r1["connection_status"] == "connected" and r1["provider_user_id"] == "ms-sub-A"
    assert r1["encrypted_refresh_token"].startswith("v1.") and "REFRESH-SECRET" not in r1["encrypted_refresh_token"]
    assert "Mail.Send" in r1["granted_scopes"]

    # ---------- 2) ربط Gmail بنجاح ----------
    html, loc = link(ad, "google", GA)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).items()}
    assert set(q["scope"].split()) == {"openid", "email", GS} and q["access_type"] == "offline" and "mail.readonly" not in loc and "gmail.modify" not in loc
    assert "تم ربط alice@gmail.com بنجاح" in html and row(uid_admin, "google")["email_address"] == "alice@gmail.com"

    # ---------- 3) رفض الصلاحيات + حالات state ----------
    uid_b = None
    b_cl = app.test_client(); cdb = connect()
    cdb.execute("INSERT INTO users(username,full_name,pw_hash,role,must_change_pw,created_at) VALUES('mgr2','مدير ثانٍ',?,'admin',0,?)", (T.hash_pw("Mgr2-Pass-12345!"), int(time.time())))
    uid_b = cdb.execute("SELECT id FROM users WHERE username='mgr2'").fetchone()[0]
    assert login(b_cl, "mgr2", "Mgr2-Pass-12345!").status_code == 302
    html, _ = link(b_cl, "microsoft", BOB, deny=True)
    assert "لم تُمنح الصلاحيات" in html and row(uid_b, "microsoft") is None
    # استخدام state مرتين (إعادة تشغيل الاستجابة) يُرفض
    loc = start(b_cl, "microsoft"); q = W.consent(loc, BOB); cb = "/account/email/callback/microsoft?" + urllib.parse.urlencode(q)
    b_cl.get(cb); assert row(uid_b, "microsoft") is not None
    html = (b_cl.get(cb, follow_redirects=True)).get_data(True); assert "انتهت صلاحية طلب الربط" in html
    emailconn.disconnect(connect(), dict(id=uid_b, username="mgr2"), "microsoft")
    # state مزوَّر / مستخدم آخر / مزوّد آخر
    assert "انتهت صلاحية" in b_cl.get("/account/email/callback/microsoft?code=x&state=forged", follow_redirects=True).get_data(True)
    loc = start(ad, "microsoft"); q = W.consent(loc, AL)
    html = b_cl.get("/account/email/callback/microsoft?" + urllib.parse.urlencode(q), follow_redirects=True).get_data(True)    # مستخدم آخر يحاول إكمال ربط غيره
    assert "انتهت صلاحية" in html and row(uid_b, "microsoft") is None
    loc = start(ad, "microsoft"); q = W.consent(loc, AL)
    html = ad.get("/account/email/callback/google?" + urllib.parse.urlencode(q), follow_redirects=True).get_data(True)         # مزوّد مختلف عن المسجَّل في state
    assert "انتهت صلاحية" in html
    # state منتهي (> 10 دقائق)
    loc = start(ad, "google"); q = W.consent(loc, GA)
    connect().execute("UPDATE oauth_states SET created_at=created_at-700"); assert "انتهت صلاحية" in ad.get("/account/email/callback/google?" + urllib.parse.urlencode(q), follow_redirects=True).get_data(True)
    # المستخدم ألغى تحديد «إرسال البريد» (موافقة جزئية في Google) → يُرفض ويُلغى التفويض
    before = len(W.revoked)
    html, _ = link(b_cl, "google", dict(sub="g-sub-B", email="bob@gmail.com"), scope="openid email")
    assert "صلاحية «إرسال البريد»" in html and row(uid_b, "google") is None and len(W.revoked) == before + 1
    # لا رمز تجديد من المزوّد
    W.no_refresh = True; html, _ = link(b_cl, "microsoft", BOB); W.no_refresh = False
    assert "رمز تجديد" in html and row(uid_b, "microsoft") is None
    # حساب البريد نفسه لا يُربط بمستخدمين اثنين
    html, _ = link(b_cl, "microsoft", AL); assert "مرتبط بمستخدم آخر" in html and row(uid_b, "microsoft") is None
    # بريد Google غير موثَّق
    html, _ = link(b_cl, "google", dict(sub="g-sub-X", email="x@gmail.com", verified=False)); assert row(uid_b, "google") is None and "غير موثَّق" in html

    # ---------- 6) انتهاء رمز الوصول: تجديد تلقائي ----------
    conn = connect().execute("SELECT * FROM user_email_connections WHERE id=?", (r1["id"],)).fetchone()
    msg = T.EmailMessage() if hasattr(T, "EmailMessage") else None
    from email.message import EmailMessage
    def mk(to="a@b.com"):
        m = EmailMessage(); m["Subject"] = "اختبار"; m["From"] = "no-reply@x.edu"; m["To"] = to; m.set_content("نص"); return m
    emailconn._cache.clear(); n0 = len([x for x in W.calls if x[1].endswith("/token")])
    emailconn.send_as_user(connect(), dict(user_id=uid_admin, provider="microsoft"), mk())           # لا رمز وصول مخزّن → يجدّد
    assert len([x for x in W.calls if x[1].endswith("/token")]) == n0 + 1 and W.sent[-1]["to"] == ["a@b.com"]
    W.expired.update(W.access)                                                                        # الرمز المخزَّن مؤقتاً صار مرفوضاً (401)
    n1 = len([x for x in W.calls if x[1].endswith("/token")])
    emailconn.send_as_user(connect(), dict(user_id=uid_admin, provider="microsoft"), mk("b@c.com"))   # 401 → تجديد → إعادة المحاولة
    assert len([x for x in W.calls if x[1].endswith("/token")]) == n1 + 1 and W.sent[-1]["to"] == ["b@c.com"]
    rot = row(uid_admin, "microsoft")["encrypted_refresh_token"]; assert rot != r1["encrypted_refresh_token"], "رمز التجديد المُدوَّر لم يُحفظ"
    W.throttle = 2; emailconn.send_as_user(connect(), dict(user_id=uid_admin, provider="microsoft"), mk("t@c.com")); assert W.sent[-1]["to"] == ["t@c.com"]   # 429 مع إعادة محاولة

    # ---------- 8) إرسال الشهادات عبر Outlook ----------
    sent0 = len(W.sent); c = connect(); c.execute("UPDATE certificates SET email_status='pending' WHERE event_id=?", (eid,))
    send(ad, eid, sender="microsoft"); j = wait_job(); assert j["status"] == "done" and j["failed"] == 0, dict(j)
    got = W.sent[sent0:]; assert len(got) == ncert and all(g["prov"] == "microsoft" and g["acct"] == "alice@contoso.com" for g in got)
    assert all(g["atts"] and g["atts"][0].endswith(".pdf") and "/get/" in g["body"] and "رقم الشهادة" in g["body"] for g in got)       # PDF مرفق + القالب الحالي
    assert {g["to"][0] for g in got} == {dec(r["email_enc"]) for r in c.execute("SELECT p.email_enc FROM certificates x JOIN participants p ON p.id=x.participant_id WHERE x.event_id=? AND x.status='valid'", (eid,))}
    assert c.execute("SELECT COUNT(*) FROM certificates WHERE event_id=? AND email_status='sent'", (eid,)).fetchone()[0] == ncert
    assert "via=microsoft" in c.execute("SELECT detail FROM audit WHERE action='certs.email' ORDER BY id DESC LIMIT 1").fetchone()[0]

    # ---------- 9) إرسال الشهادات عبر Gmail ----------
    sent0 = len(W.sent); send(ad, eid, sender="google"); j = wait_job(); assert j["failed"] == 0, (dict(j), [dict(r) for r in connect().execute("SELECT email_status,email_error FROM certificates WHERE event_id=?", (eid,))])
    got = W.sent[sent0:]; assert len(got) == ncert and all(g["prov"] == "google" and g["frm"] == "alice@gmail.com" and g["atts"][0].endswith(".pdf") for g in got)
    # الافتراضي بلا اختيار = أول حساب مرتبط للمنفّذ
    sent0 = len(W.sent); send(ad, eid); wait_job(); assert len(W.sent) == sent0 + ncert and W.sent[-1]["prov"] == "google"
    # صفحة الفعالية تعرض خيار المرسِل
    ev_html = ad.get(f"/events/{eid}").get_data(True); assert 'name="sender"' in ev_html and "alice@gmail.com" in ev_html and "alice@contoso.com" in ev_html

    # ---------- 10) عزل المستخدمين ----------
    sent0 = len(W.sent); jobs0 = connect().execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    for ch in ("microsoft", "google"):                      # مدير ثانٍ بلا اتصالات يطلب الإرسال من اتصال المستخدم الأول
        r = b_cl.post(f"/events/{eid}/send", data=dict(_csrf=tok(b_cl, f"/events/{eid}"), scope="all", sender=ch), follow_redirects=True)
        assert "لا توجد وسيلة إرسال" in r.get_data(True) or "ENV" in r.get_data(True) or r.status_code == 200
    assert len(W.sent) == sent0 and connect().execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == jobs0, "تم إرسال/إنشاء مهمة باتصال مستخدم آخر!"
    r = b_cl.post("/account/email/disconnect/microsoft", data=dict(_csrf=tok(b_cl, "/account"))); assert row(uid_admin, "microsoft") is not None   # فصل المستخدم الثاني لا يمسّ اتصال الأول
    # نقل رمز مشفَّر من صف مستخدم إلى آخر لا يعمل (AAD)
    c = connect(); blob = row(uid_admin, "google")["encrypted_refresh_token"]
    try: emailconn.unseal(blob, emailconn._aad(uid_b, "google", "g-sub-A")); raise SystemExit("فُكّ رمز مستخدم آخر!")
    except emailconn.ConnectionExpired: pass
    assert emailconn.unseal(blob, emailconn._aad(uid_admin, "google", "g-sub-A")).startswith("REFRESH-SECRET")
    # sender_options/send_as_user لا يقبلان إلا user_id المنفّذ
    try: emailconn.send_as_user(connect(), dict(user_id=uid_b, provider="google"), mk()); raise SystemExit("أُرسل من حساب غير مربوط!")
    except emailconn.ConnectionExpired: pass
    # عدّل المرسِل في المهمة: المهمة تستخدم user["id"] فقط (لا معرّف اتصال من النموذج)
    assert "conn" not in " ".join(re.findall(r'name="(\w+)"', ev_html))

    # ---------- 7) فشل تجديد Refresh Token + احتياطي SMTP ----------
    for p_ in ("microsoft",):
        c = connect(); c.execute("UPDATE certificates SET email_status='pending' WHERE event_id=?", (eid,))
        W.refresh_fail = True; emailconn._cache.clear(); sent0 = len(W.sent)
        send(ad, eid, sender="microsoft"); j = wait_job()
        assert j["failed"] == ncert and len(W.sent) == sent0, dict(j)
        errs = {r[0] for r in connect().execute("SELECT email_error FROM certificates WHERE status='valid' AND event_id=?", (eid,))}; assert errs == {"انتهى الاتصال بحساب البريد — أعد الربط من «حسابي»"}, errs
        r_ = row(uid_admin, "microsoft"); assert r_["connection_status"] == "needs_reconnect" and r_["last_error"] == "invalid_grant"
        acc_html = ad.get("/account").get_data(True); assert "Reconnect needed" in acc_html and "إعادة الربط" in acc_html
        assert "microsoft" not in " ".join(o["key"] for o in emailconn.sender_options(connect(), dict(id=uid_admin), True))
        # احتياطي SMTP بموافقة صريحة (يعمل بوضع الملف في الاختبار): نعيد الحالة لمتصل ونكسر التجديد ثم نطلب fallback
        c.execute("UPDATE user_email_connections SET connection_status='connected' WHERE id=?", (r_["id"],)); c.execute("UPDATE certificates SET email_status='pending' WHERE event_id=?", (eid,))
        files0 = len(list((T.pathlib.Path(os.environ["INJAZ_DATA_DIR"]) / "outbox").glob("*.eml")))
        send(ad, eid, sender="microsoft", fallback="1"); j = wait_job(); W.refresh_fail = False
        assert j["failed"] == 0 and len(W.sent) == sent0, dict(j)
        assert len(list((T.pathlib.Path(os.environ["INJAZ_DATA_DIR"]) / "outbox").glob("*.eml"))) >= ncert  # الملفات تُسمّى بالرقم التسلسلي فتُستبدل
        assert connect().execute("SELECT COUNT(*) FROM audit WHERE action='certs.email_fallback'").fetchone()[0] >= 1

    # ---------- 5) إعادة الربط ----------
    assert row(uid_admin, "microsoft")["connection_status"] == "needs_reconnect"  # فشل التجديد أثناء الاحتياطي أعاده لحالة إعادة الربط
    connect().execute("UPDATE user_email_connections SET connection_status='needs_reconnect', last_error='invalid_grant' WHERE provider='microsoft'")
    html, _ = link(ad, "microsoft", AL); r = row(uid_admin, "microsoft")
    assert r["connection_status"] == "connected" and r["last_error"] == "" and "Disconnected" not in html.split("Microsoft")[1][:900]
    sent0 = len(W.sent); emailconn.send_as_user(connect(), dict(user_id=uid_admin, provider="microsoft"), mk()); assert len(W.sent) == sent0 + 1

    # ---------- 4) فصل الحساب ----------
    W.revoked.clear(); rt_google = emailconn.unseal(row(uid_admin, "google")["encrypted_refresh_token"], emailconn._aad(uid_admin, "google", "g-sub-A"))
    r = ad.post("/account/email/disconnect/google", data=dict(_csrf=tok(ad, "/account")), follow_redirects=True); assert "تم فصل الحساب" in r.get_data(True)
    assert row(uid_admin, "google") is None and rt_google in W.revoked                    # حُذفت الرموز محلياً وأُلغيت لدى Google
    assert "Disconnected" in ad.get("/account").get_data(True)
    try: emailconn.send_as_user(connect(), dict(user_id=uid_admin, provider="google"), mk()); raise SystemExit("أرسل بعد الفصل")
    except emailconn.ConnectionExpired: pass
    r = ad.post("/account/email/disconnect/microsoft", data=dict(_csrf=tok(ad, "/account"))); assert row(uid_admin, "microsoft") is None
    # بعد الفصل التام: الإرسال بلا وسيلة OAuth يستخدم SMTP المركزي تلقائياً (الوضع الافتراضي)
    # ---------- 12) SMTP الحالي يعمل ----------
    out = T.pathlib.Path(os.environ["INJAZ_DATA_DIR"]) / "outbox"; n_eml = len(list(out.glob("*.eml")))
    connect().execute("UPDATE certificates SET email_status='pending' WHERE event_id=?", (eid,)); sent0 = len(W.sent)
    send(ad, eid); j = wait_job(); assert j["failed"] == 0 and len(W.sent) == sent0
    assert len(list(out.glob("*.eml"))) >= n_eml  # الملفات تُستبدل بالرقم التسلسلي: يكفي ألا يفشل ولا يمرّ عبر OAuth
    assert connect().execute("SELECT COUNT(*) FROM certificates WHERE event_id=? AND email_status='sent'", (eid,)).fetchone()[0] == ncert
    ad.post(f"/certs/{connect().execute('SELECT id FROM certificates WHERE event_id=? LIMIT 1', (eid,)).fetchone()[0]}/resend", data=dict(_csrf=tok(ad, f"/events/{eid}"), sender="smtp"))
    assert len(W.sent) == sent0

    # ---------- المشرف: إحصاءات بلا أسرار + إدارة ----------
    link(ad, "google", GA); link(b_cl, "microsoft", BOB)
    page = ad.get("/admin/email-accounts"); assert page.status_code == 200; h = page.get_data(True)
    assert "alice@gmail.com" in h and "bob@contoso.com" in h and "Microsoft" in h and "Google" in h
    for secret in ("REFRESH-SECRET", "ACCESS-SECRET", "v1.", "encrypted_refresh_token", "ms-client-secret-VALUE", "g-client-secret-VALUE"): assert secret not in h, secret
    S = emailconn.admin_stats(connect()); assert S["total"] == 2 and S["ok"] == 2 and S["errs"]
    cid_b = row(uid_b, "microsoft")["id"]
    assert b_cl.get("/admin/email-accounts").status_code == 200   # مدير ثانٍ هو admin أيضاً
    assert ad.post(f"/admin/email-accounts/{cid_b}/remove", data=dict(_csrf=tok(ad, "/admin/email-accounts"))).status_code == 302 and row(uid_b, "microsoft") is None
    # دور منظّم لا يصل لصفحة المشرف ولا لروابط الإدارة
    cdb = connect(); cdb.execute("INSERT INTO users(username,full_name,pw_hash,role,dept_id,must_change_pw,created_at) VALUES('org9','منظم',?,'organizer',1,0,?)", (T.hash_pw("Org9-Pass-12345!"), int(time.time())))
    o = app.test_client(); assert login(o, "org9", "Org9-Pass-12345!").status_code == 302
    assert o.get("/admin/email-accounts").status_code == 403 and o.post(f"/admin/email-accounts/1/remove", data=dict(_csrf=tok(o, "/"))).status_code == 403
    assert "Email Accounts" in o.get("/account").get_data(True)               # لكنه يربط حسابه هو
    cdb.execute("INSERT INTO users(username,full_name,pw_hash,role,dept_id,must_change_pw,created_at) VALUES('view9','مطّلع',?,'viewer',1,0,?)", (T.hash_pw("View9-Pass-12345!"), int(time.time())))
    v = app.test_client(); assert login(v, "view9", "View9-Pass-12345!").status_code == 302
    assert "Email Accounts" not in v.get("/account").get_data(True) and v.get("/account/email/connect/google?n=x").status_code == 403

    # ---------- 11) لا أسرار في السجلات/القاعدة/الصفحات ----------
    app.logger.info("debug access_token=ACCESS-SECRET-LEAK refresh_token: REFRESH-SECRET-LEAK Authorization: Bearer ACCESS-SECRET-LEAK2 code_verifier=VERIFIER-LEAK")
    logs = LOGS.getvalue()
    for secret in ("ACCESS-SECRET", "REFRESH-SECRET", "CODE-", "VERIFIER-LEAK", "ms-client-secret-VALUE", "g-client-secret-VALUE", "eyJ"): assert secret not in logs, f"تسرّب في السجلات: {secret}"
    assert "[REDACTED]" in logs                                                                       # التنقيح اشتغل على سطر التجربة
    cdb = connect()
    for (txt,) in cdb.execute("SELECT detail||target||username||action FROM audit"): assert "SECRET" not in txt and "CODE-" not in txt
    for (m_,) in cdb.execute("SELECT message FROM jobs"): assert "SECRET" not in m_
    for (m_,) in cdb.execute("SELECT email_error FROM certificates"): assert "SECRET" not in m_
    import dbraw; from app.db import IS_PG, connect as _c
    raw = dbraw.raw_db_bytes(os.environ["INJAZ_DATA_DIR"], _c, IS_PG)
    assert b"REFRESH-SECRET" not in raw and b"ACCESS-SECRET" not in raw, "رمز مخزَّن بنص صريح في قاعدة البيانات!"
    assert b"ms-client-secret" not in raw
    print("EMAILCONN OK", dict(sent=len(W.sent), providers=[p.key for p in mailproviders.all_providers()]))


if __name__ == "__main__":
    run()
