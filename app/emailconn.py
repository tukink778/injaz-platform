"""طبقة خدمة «ربط حساب البريد» (OAuth 2.0): تشفير الرموز، حالة التدفق، تجديد الرموز، الإرسال بحساب المستخدم، وإحصاءات المشرف.

مبادئ الأمان:
- لا كلمات مرور بريد: المستخدم يسجّل الدخول عند المزوّد نفسه ويوافق على صلاحية الإرسال فقط.
- رمز التجديد يُخزَّن مشفّراً AES-256-GCM (المفتاح مشتق من مفتاح النظام الرئيسي)، مع AAD يربطه بالمستخدم والمزوّد والحساب؛
  فنسخه إلى صف/مستخدم آخر يفشل فك تشفيره. رمز الوصول لا يُخزَّن أبداً (ذاكرة العملية فقط، لدقائق).
- كل استعلام يمرّ عبر user_id القادم من الجلسة/المهمة (لا من نموذج المستخدم) فلا يمكن استخدام اتصال مستخدم آخر.
- لا تُسجَّل الرموز في السجلات (مرشّح تنقيح) ولا تُعرض للمشرف (أعمدة صريحة بلا الرمز).
"""
import base64, hashlib, logging, os, re, secrets, threading, time
from email.message import EmailMessage
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from . import config, mailproviders
from .config import Cfg
from .mailproviders.base import b64url, pkce_challenge
from .mailproviders.errors import AuthExpired, ProviderError, TokenRejected
from .security import audit, token_hash

log = logging.getLogger("injaz.email")
STATE_TTL = 600
_aes = AESGCM(config.subkey("oauth-tokens"))   # 32 بايت = AES-256
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_sleep = time.sleep


class ConnectionExpired(Exception):
    """الاتصال غير صالح (منتهٍ/ملغى/غير موجود): يلزم الربط أو إعادة الربط."""


class LinkError(Exception):
    """فشل في إكمال الربط؛ الرسالة آمنة للعرض للمستخدم."""


# ---------------- التشفير ----------------
def seal(plain: str, aad: str) -> str:
    nonce = os.urandom(12)
    return "v1." + b64url(nonce + _aes.encrypt(nonce, plain.encode(), aad.encode()))


def unseal(blob: str, aad: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(blob[3:] + "=" * (-len(blob[3:]) % 4)) if blob.startswith("v1.") else b""
        return _aes.decrypt(raw[:12], raw[12:], aad.encode()).decode()
    except (InvalidTag, ValueError):
        raise ConnectionExpired("token_corrupt")


def _aad(user_id, provider, subject): return f"conn|{user_id}|{provider}|{subject}"


# ---------------- إعدادات عامة ----------------
def https_ok() -> bool:
    b = Cfg.BASE_URL
    return b.startswith("https://") or re.match(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$", b) is not None


def redirect_uri(provider: str) -> str:
    return f"{Cfg.BASE_URL}/account/email/callback/{provider}"


def provider_ready(p) -> bool:
    return bool(p and p.configured() and https_ok())


def connect_nonce(provider: str) -> str:
    """رمز لمرة-الغرض لبدء الربط عبر رابط GET (لا يكشف رمز CSRF الأصلي)."""
    from .security import csrf_token
    return hmac_hex(csrf_token(), "emailconn-connect|" + provider)


def hmac_hex(key: str, msg: str) -> str:
    import hmac
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]


# ---------------- بدء الربط وإكماله ----------------
def begin(c, user, provider_key: str) -> str:
    p = mailproviders.get(provider_key)
    if not provider_ready(p): raise LinkError("المزوّد غير مفعّل على الخادم (راجع المشرف)")
    now = int(time.time())
    c.execute("DELETE FROM oauth_states WHERE created_at<?", (now - STATE_TTL,))
    c.execute("DELETE FROM oauth_states WHERE user_id=? AND provider=?", (user["id"], provider_key))
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)[:96]
    c.execute("INSERT INTO oauth_states(state_hash,user_id,provider,verifier_enc,created_at) VALUES(?,?,?,?,?)",
              (token_hash(state), user["id"], provider_key, seal(verifier, f"state|{user['id']}|{provider_key}"), now))
    return p.authorize_url(redirect_uri(provider_key), state, pkce_challenge(verifier))


def _msg_for(code: str) -> str:
    return {"access_denied": "لم تُمنح الصلاحيات، فلم يُربط الحساب.", "network": "تعذّر الاتصال بالمزوّد. حاول لاحقاً.",
            "email_not_verified": "بريد حساب المزوّد غير موثَّق لديه."}.get(code, f"رفض المزوّد الطلب ({code}).")


def finish(c, user, provider_key: str, args) -> str:
    """يُكمل الربط من معاملات العودة. يرجع رسالة نجاح، أو يرمي LinkError برسالة آمنة."""
    p = mailproviders.get(provider_key)
    if not p: raise LinkError("مزوّد غير معروف")
    state = (args.get("state") or "")
    row = None
    if state:
        row = c.execute("SELECT * FROM oauth_states WHERE state_hash=?", (token_hash(state),)).fetchone()
        c.execute("DELETE FROM oauth_states WHERE state_hash=?", (token_hash(state),))       # استخدام واحد حتى عند الفشل
    c.execute("DELETE FROM oauth_states WHERE created_at<?", (int(time.time()) - STATE_TTL,))
    if not row or row["user_id"] != user["id"] or row["provider"] != provider_key or time.time() - row["created_at"] > STATE_TTL:
        audit("emailconn.fail", provider_key, "state_invalid", conn=c, user=user)
        raise LinkError("انتهت صلاحية طلب الربط أو لم يبدأ من حسابك. ابدأ من جديد.")
    if args.get("error"):
        code = re.sub(r"[^a-z_]", "", str(args.get("error"))[:40]) or "error"
        audit("emailconn.denied", provider_key, code, conn=c, user=user)
        raise LinkError(_msg_for(code))
    code_ = args.get("code") or ""
    if not code_: raise LinkError("استجابة المزوّد ناقصة")
    verifier = unseal(row["verifier_enc"], f"state|{user['id']}|{provider_key}")
    try:
        t = p.exchange_code(code_, redirect_uri(provider_key), verifier)
        granted = t.scope or " ".join(p.scopes)
        if not p.has_send_scope(granted):
            p.revoke(t.refresh_token or t.access_token)
            audit("emailconn.fail", provider_key, "scope_missing", conn=c, user=user)
            raise LinkError("لم تمنح صلاحية «إرسال البريد». أعد المحاولة ووافق على الصلاحية المطلوبة.")
        ident = p.identity(t.access_token)
    except AuthExpired as e:
        audit("emailconn.fail", provider_key, e.code, conn=c, user=user); raise LinkError("انتهى رمز التفويض. أعد المحاولة.")
    except ProviderError as e:
        audit("emailconn.fail", provider_key, e.code, conn=c, user=user); raise LinkError(_msg_for(e.code))
    if not _EMAIL_RE.match(ident.email): raise LinkError("تعذّر التحقق من عنوان البريد")
    mine = c.execute("SELECT * FROM user_email_connections WHERE user_id=? AND provider=?", (user["id"], provider_key)).fetchone()
    other = c.execute("SELECT user_id FROM user_email_connections WHERE provider=? AND provider_user_id=?", (provider_key, ident.subject)).fetchone()
    if other and other["user_id"] != user["id"]:
        p.revoke(t.refresh_token or t.access_token)
        audit("emailconn.fail", provider_key, "linked_to_other_user", conn=c, user=user)
        raise LinkError("هذا الحساب مرتبط بمستخدم آخر في المنصة. فكّ ربطه هناك أولاً.")
    rt = t.refresh_token
    if not rt and mine and mine["provider_user_id"] == ident.subject:
        rt = unseal(mine["encrypted_refresh_token"], _aad(user["id"], provider_key, mine["provider_user_id"]))   # المزوّد لم يُصدر رمزاً جديداً
    if not rt: raise LinkError("لم يُصدر المزوّد رمز تجديد. أزل تفويض التطبيق من حسابك ثم أعد الربط.")
    now = int(time.time()); blob = seal(rt, _aad(user["id"], provider_key, ident.subject))
    if mine:
        if mine["provider_user_id"] != ident.subject:   # تغيير الصندوق: يُلغى القديم
            try: p.revoke(unseal(mine["encrypted_refresh_token"], _aad(user["id"], provider_key, mine["provider_user_id"])))
            except ConnectionExpired: pass
        c.execute("""UPDATE user_email_connections SET provider_user_id=?,email_address=?,encrypted_refresh_token=?,token_expiry=?,granted_scopes=?,
                     connection_status='connected',last_error='',last_error_at=NULL,updated_at=?,last_success_at=? WHERE id=?""",
                  (ident.subject, ident.email, blob, now + t.expires_in, granted, now, now, mine["id"]))
        cid = mine["id"]
    else:
        cid = c.insert("""INSERT INTO user_email_connections(user_id,provider,provider_user_id,email_address,encrypted_refresh_token,token_expiry,granted_scopes,
                     created_at,updated_at,last_success_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                       (user["id"], provider_key, ident.subject, ident.email, blob, now + t.expires_in, granted, now, now, now))
    _cache[cid] = (t.access_token, time.time() + max(60, t.expires_in - 120))
    audit("emailconn.connect", provider_key, ident.email, conn=c, user=user)
    return f"تم ربط {ident.email} بنجاح"


# ---------------- الرموز ----------------
_cache: dict = {}             # conn_id -> (access_token, أقصى وقت استخدام) — ذاكرة فقط
_locks: dict = {}
_locks_guard = threading.Lock()


def _lock(cid):
    with _locks_guard: return _locks.setdefault(cid, threading.Lock())


def _mark(c, cid, status=None, error=""):
    now = int(time.time())
    c.execute("UPDATE user_email_connections SET connection_status=COALESCE(?,connection_status), last_error=?, last_error_at=?, updated_at=? WHERE id=?",
              (status, error, now if error else None, now, cid))


def _get_conn(c, user_id, provider_key):
    return c.execute("SELECT * FROM user_email_connections WHERE user_id=? AND provider=?", (user_id, provider_key)).fetchone()


def access_token(c, conn, force=False) -> str:
    p = mailproviders.get(conn["provider"]); cid = conn["id"]
    with _lock(cid):
        hit = _cache.get(cid)
        if hit and not force and hit[1] > time.time(): return hit[0]
        rt = unseal(conn["encrypted_refresh_token"], _aad(conn["user_id"], conn["provider"], conn["provider_user_id"]))
        try:
            t = p.refresh(rt)
        except AuthExpired as e:
            _cache.pop(cid, None); _mark(c, cid, "needs_reconnect", e.code)
            audit("emailconn.expired", conn["provider"], e.code, conn=c, user=dict(id=conn["user_id"], username="-"))
            raise ConnectionExpired(e.code)
        except ProviderError as e:
            _mark(c, cid, None, e.code); raise
        now = int(time.time())
        if t.refresh_token and t.refresh_token != rt:     # مزوّدون يدوّرون رمز التجديد
            c.execute("UPDATE user_email_connections SET encrypted_refresh_token=? WHERE id=?", (seal(t.refresh_token, _aad(conn["user_id"], conn["provider"], conn["provider_user_id"])), cid))
        c.execute("UPDATE user_email_connections SET token_expiry=?, last_success_at=?, updated_at=? WHERE id=?", (now + t.expires_in, now, now, cid))
        _cache[cid] = (t.access_token, time.time() + max(60, t.expires_in - 120))
        return t.access_token


def send_as_user(c, sender: dict, msg: EmailMessage):
    """sender = dict(user_id, provider). يرسل بحساب المستخدم نفسه فقط."""
    conn = _get_conn(c, sender["user_id"], sender["provider"])
    if not conn or conn["connection_status"] != "connected": raise ConnectionExpired("not_connected")
    p = mailproviders.get(conn["provider"])
    if msg["From"]: msg.replace_header("From", conn["email_address"])
    else: msg["From"] = conn["email_address"]
    tok = access_token(c, conn)
    for attempt in range(3):
        try:
            try:
                p.send(tok, msg)
            except TokenRejected:
                tok = access_token(c, conn, force=True); p.send(tok, msg)      # انتهت صلاحية رمز الوصول أثناء العمل
            break
        except TokenRejected:
            _mark(c, conn["id"], "needs_reconnect", "token_rejected"); raise ConnectionExpired("token_rejected")
        except ProviderError as e:
            if e.retryable and attempt < 2:
                _sleep(min(max(e.retry_after, 2 ** (attempt + 1)), 30)); continue
            _mark(c, conn["id"], None, e.code); raise
    now = int(time.time())
    c.execute("UPDATE user_email_connections SET last_used_at=?, last_success_at=?, last_error='', last_error_at=NULL WHERE id=?", (now, now, conn["id"]))
    return p


def disconnect(c, user, provider_key: str) -> bool:
    conn = _get_conn(c, user["id"], provider_key)
    if not conn: return False
    p = mailproviders.get(provider_key)
    try:
        if p: p.revoke(unseal(conn["encrypted_refresh_token"], _aad(conn["user_id"], provider_key, conn["provider_user_id"])))
    except ConnectionExpired: pass
    c.execute("DELETE FROM user_email_connections WHERE id=?", (conn["id"],))       # حذف الرموز المخزَّنة نهائياً
    _cache.pop(conn["id"], None)
    audit("emailconn.disconnect", provider_key, conn["email_address"], conn=c, user=user)
    return True


def remove_by_admin(c, admin, conn_id: int) -> bool:
    conn = c.execute("SELECT id,user_id,provider,provider_user_id,email_address,encrypted_refresh_token FROM user_email_connections WHERE id=?", (conn_id,)).fetchone()
    if not conn: return False
    p = mailproviders.get(conn["provider"])
    try:
        if p: p.revoke(unseal(conn["encrypted_refresh_token"], _aad(conn["user_id"], conn["provider"], conn["provider_user_id"])))
    except ConnectionExpired: pass
    c.execute("DELETE FROM user_email_connections WHERE id=?", (conn_id,)); _cache.pop(conn_id, None)
    audit("emailconn.admin_remove", conn["provider"], f"user:{conn['user_id']} {conn['email_address']}", conn=c, user=admin)
    return True


# ---------------- اختيار المرسِل (للشهادات) ----------------
def user_connections(c, user_id):
    return c.execute("SELECT provider,email_address,connection_status FROM user_email_connections WHERE user_id=? ORDER BY provider", (user_id,)).fetchall()


def sender_options(c, user, smtp_ok: bool):
    """خيارات الإرسال المتاحة للمستخدم: حساباته المرتبطة (الصالحة) ثم SMTP المركزي إن وُجد."""
    out = []
    for r in user_connections(c, user["id"]):
        p = mailproviders.get(r["provider"])
        if p and r["connection_status"] == "connected":
            out.append(dict(key=r["provider"], label=f"{p.label_en.split(' (')[0]} · {r['email_address']}", email=r["email_address"]))
    if smtp_ok: out.append(dict(key="smtp", label="البريد المركزي للنظام (SMTP)", email=""))
    return out


def resolve_sender(c, user, choice: str, smtp_ok: bool):
    """يرجع (provider_key|None للمركزي). يرمي ConnectionExpired/LookupError إن لم يتوفر ما طُلب. لا يقبل إلا اتصالات المستخدم نفسه."""
    opts = sender_options(c, user, smtp_ok)
    keys = [o["key"] for o in opts]
    if not keys: raise LookupError("no_sender")
    choice = (choice or "").strip().lower() or keys[0]
    if choice not in keys: raise LookupError("sender_unavailable")
    return None if choice == "smtp" else choice


def friendly_error(e) -> str:
    if isinstance(e, ConnectionExpired): return "انتهى الاتصال بحساب البريد — أعد الربط من «حسابي»"
    if isinstance(e, ProviderError): return f"رفض مزوّد البريد الإرسال ({e.code})"
    return type(e).__name__


# ---------------- عرض الحساب والمشرف ----------------
def account_view(c, user_id):
    rows = {r["provider"]: r for r in c.execute("""SELECT id,provider,email_address,connection_status,last_error,last_success_at,last_used_at,created_at
                                                   FROM user_email_connections WHERE user_id=?""", (user_id,))}
    return [dict(key=p.key, label_ar=p.label_ar, label_en=p.label_en, ready=provider_ready(p), conn=rows.get(p.key), nonce=connect_nonce(p.key)) for p in mailproviders.all_providers()]


def admin_stats(c):
    """إحصاءات تجميعية وقائمة اتصالات بلا أي رموز."""
    by = [dict(r) for r in c.execute("SELECT provider, connection_status st, COUNT(*) n FROM user_email_connections GROUP BY provider, connection_status")]
    conns = c.execute("""SELECT x.id, x.provider, x.email_address, x.connection_status, x.last_error, x.last_error_at, x.last_used_at, x.last_success_at, x.created_at,
                                u.username, u.full_name FROM user_email_connections x JOIN users u ON u.id=x.user_id ORDER BY x.provider, u.username""").fetchall()
    errs = c.execute("SELECT ts, username, action, target, detail FROM audit WHERE action IN ('emailconn.fail','emailconn.expired','emailconn.denied') ORDER BY id DESC LIMIT 15").fetchall()
    prov = [dict(key=p.key, label=p.label_en, ready=provider_ready(p), configured=p.configured(), env=(p.env_id, p.env_secret)) for p in mailproviders.all_providers()]
    return dict(by=by, total=len(conns), ok=sum(1 for r in conns if r["connection_status"] == "connected"),
                bad=sum(1 for r in conns if r["connection_status"] != "connected"), conns=conns, errs=errs, providers=prov, https_ok=https_ok())


# ---------------- تنقيح السجلات ----------------
_SECRET = re.compile(r"(?i)((?:access_token|refresh_token|id_token|client_secret|code_verifier|authorization|code|state)[\"']?\s*[:=]\s*[\"']?(?:Bearer\s+)?)[^\s\"'&,}]+|(Bearer\s+)[A-Za-z0-9._~+/=\-]+|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*")


class RedactFilter(logging.Filter):
    def filter(self, record):
        try:
            msg = record.getMessage()
            red = _SECRET.sub(lambda m: (m.group(1) or m.group(2) or "") + "[REDACTED]", msg)
            if red != msg: record.msg, record.args = red, ()
        except Exception: pass
        return True


def install_log_redaction(app):
    f = RedactFilter()
    for lg in (logging.getLogger(), app.logger, log):
        for h in lg.handlers: h.addFilter(f)
        lg.addFilter(f)
