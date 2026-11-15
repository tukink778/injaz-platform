"""طبقة الأمان: تشفير البيانات الشخصية، كلمات المرور، CSRF، 2FA، تحديد المعدل، سجل التدقيق المتسلسل."""
import base64, functools, hashlib, hmac, io, json, re, secrets, threading, time
import pyotp, qrcode
from cryptography.fernet import Fernet, InvalidToken
from flask import abort, g, redirect, request, session, url_for, flash
from werkzeug.security import check_password_hash, generate_password_hash
from . import config
from .db import db

_fernet = Fernet(base64.urlsafe_b64encode(config.subkey("data")))
_blind = config.subkey("blind")


def enc(s: str) -> str:
    return _fernet.encrypt((s or "").encode()).decode()


def dec(t: str) -> str:
    if not t:
        return ""
    try:
        return _fernet.decrypt(t.encode()).decode()
    except InvalidToken:
        return "⚠"


def enc_bytes(b: bytes) -> bytes:
    return _fernet.encrypt(b)


def dec_bytes(b: bytes) -> bytes:
    return _fernet.decrypt(b)


def norm_id(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def blind(kind: str, value: str) -> str:
    """فهرس أعمى: يسمح بالبحث والكشف عن التكرار دون تخزين القيمة بصيغة مقروءة."""
    v = norm_id(value) if value else ""
    if not v:
        return ""
    return hmac.new(_blind, f"{kind}:{v}".encode(), hashlib.sha256).hexdigest()[:32]


def token_hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


# ---------- كلمات المرور ----------
def hash_pw(pw): return generate_password_hash(pw, method="scrypt")
_DUMMY = generate_password_hash("dummy-password", method="scrypt")


def check_pw(h, pw):
    return check_password_hash(h or _DUMMY, pw)


def pw_problem(pw: str):
    if len(pw) < 12: return "كلمة المرور يجب ألا تقل عن 12 حرفاً"
    if not (re.search(r"[a-z]", pw) and re.search(r"[A-Z]", pw) and re.search(r"\d", pw)):
        return "يجب أن تحتوي على حرف كبير وصغير ورقم (بالإنجليزية)"
    if pw.lower() in {"password1234", "administrator1"}: return "كلمة مرور شائعة"
    return None


# ---------- CSRF ----------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400, "انتهت صلاحية النموذج، أعد تحميل الصفحة")


# ---------- تحديد المعدل ----------
_rl_lock = threading.Lock()


def client_ip():
    return request.remote_addr or "?"


def rate_hit(key, limit, window):
    """يسجل محاولة ويرجع True إذا تم تجاوز الحد."""
    now = int(time.time())
    c = db()
    with _rl_lock:
        c.execute("DELETE FROM rate WHERE ts<?", (now - 86400,))
        n = c.execute("SELECT COUNT(*) FROM rate WHERE key=? AND ts>?", (key, now - window)).fetchone()[0]
        if n >= limit:
            return True
        c.execute("INSERT INTO rate(key,ts) VALUES(?,?)", (key, now))
    return False


# ---------- سجل التدقيق (سلسلة تجزئة غير قابلة للتعديل بصمت) ----------
_audit_lock = threading.Lock()


def audit(action, target="", detail="", conn=None, user=None):
    c = conn or db()
    u = user or getattr(g, "user", None)
    try:
        ip = client_ip()
    except RuntimeError:
        ip = "-"
    with _audit_lock, c.audit_guard():   # PostgreSQL: قفل استشاري على مستوى القاعدة يحمي السلسلة عبر عدة عمليات/نسخ
        row = c.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else "GENESIS"
        ts = int(time.time())
        uid = u["id"] if u else None
        un = u["username"] if u else ""
        payload = json.dumps([ts, uid, un, ip, action, target, detail, prev], ensure_ascii=False)
        h = hashlib.sha256(payload.encode()).hexdigest()
        c.execute("INSERT INTO audit(ts,user_id,username,ip,action,target,detail,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?)",
                  (ts, uid, un, ip, action, target, detail, prev, h))


def audit_verify(c):
    prev = "GENESIS"
    for r in c.execute("SELECT * FROM audit ORDER BY id"):
        payload = json.dumps([r["ts"], r["user_id"], r["username"], r["ip"], r["action"], r["target"], r["detail"], prev], ensure_ascii=False)
        if r["prev_hash"] != prev or hashlib.sha256(payload.encode()).hexdigest() != r["hash"]:
            return False, r["id"]
        prev = r["hash"]
    return True, None


# ---------- 2FA ----------
def new_totp():
    return pyotp.random_base32()


def totp_ok(secret_enc, code):
    return bool(code) and pyotp.TOTP(dec(secret_enc)).verify(re.sub(r"\D", "", code), valid_window=1)


def totp_qr_uri(secret_enc, username):
    uri = pyotp.TOTP(dec(secret_enc)).provisioning_uri(name=username, issuer_name="INJAZ")
    img = qrcode.make(uri, box_size=6, border=2)
    b = io.BytesIO(); img.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


# ---------- الجلسات والصلاحيات ----------
def load_user():
    g.user = None
    uid = session.get("uid")
    if not uid:
        return
    now = time.time()
    if now - session.get("last", 0) > config.Cfg.SESSION_IDLE or now - session.get("t0", 0) > config.Cfg.SESSION_ABS:
        session.clear(); return
    u = db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
    if not u or u["session_ver"] != session.get("sv"):
        session.clear(); return
    session["last"] = now
    g.user = u


def login_user(u):
    keep = {}
    session.clear()
    session.update(uid=u["id"], sv=u["session_ver"], t0=time.time(), last=time.time(), csrf=secrets.token_urlsafe(32))
    session.permanent = False


def login_required(f):
    @functools.wraps(f)
    def w(*a, **k):
        if not g.user:
            return redirect(url_for("auth.login", next=request.path))
        if g.user["must_change_pw"] and request.endpoint not in ("auth.account", "auth.logout", "static"):
            flash("يجب تغيير كلمة المرور قبل المتابعة", "warn")
            return redirect(url_for("auth.account"))
        return f(*a, **k)
    return w


def roles(*allowed):
    def deco(f):
        @functools.wraps(f)
        @login_required
        def w(*a, **k):
            if g.user["role"] not in allowed:
                abort(403)
            return f(*a, **k)
        return w
    return deco


WRITE_ROLES = ("admin", "manager", "organizer")


def dept_scope():
    """None = كل الأقسام (مشرف عام) وإلا رقم القسم."""
    return None if g.user["role"] == "admin" else g.user["dept_id"]


def check_dept(dept_id):
    s = dept_scope()
    if s is not None and s != dept_id:
        abort(404)  # لا نكشف وجود سجلات أقسام أخرى


def secure_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                                              "script-src 'self'; font-src 'self'; frame-ancestors 'self'; form-action 'self'; base-uri 'none'")
    if config.Cfg.HTTPS:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.endpoint not in ("static",):
        resp.headers["Cache-Control"] = "no-store"
    return resp
