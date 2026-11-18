import re, secrets, time, datetime
from flask import abort, g
from .db import db
from .security import dec, check_dept, dept_scope

CATEGORIES = {"training": "تدريب (ورش ودورات)", "career": "توجيه وظيفي", "academic": "أنشطة أكاديمية (محاضرات وندوات)",
              "initiative": "مبادرات وبرامج طلابية", "event": "فعاليات (مؤتمرات وملتقيات)", "partnership": "شراكات وبرامج مشتركة"}
STATUS_AR = {"draft": "مسودة", "open": "التسجيل مفتوح", "closed": "مغلقة", "issued": "صدرت الشهادات"}
ROLE_AR = {"admin": "مشرف عام", "manager": "رئيس قسم", "organizer": "منظم فعاليات", "viewer": "مطّلع (قراءة فقط)"}
P_STATUS = {"pending": "قيد المراجعة", "approved": "معتمد", "rejected": "مرفوض"}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
UID_RE = re.compile(r"^[A-Za-z0-9\-_/]{3,24}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]")


def clean_text(s, maxlen=200):
    s = _CTRL.sub("", str(s or "")).strip()
    return re.sub(r"\s+", " ", s)[:maxlen]


def valid_name(n):
    return 3 <= len(n) <= 120 and bool(re.search(r"[A-Za-z؀-ۿ]", n)) and not re.search(r"[<>{}=@]", n)


def event_or_404(eid, trashed=False):
    """الفعالية بحسب نطاق قسم المستخدم. المحذوفة (في السلة) لا تُرى إلا بـ trashed=True (صفحات السلة فقط) والعكس صحيح."""
    e = db().execute("SELECT e.*, d.name_ar dept_ar, d.name_en dept_en, d.code dept_code FROM events e JOIN departments d ON d.id=e.dept_id WHERE e.id=?", (eid,)).fetchone()
    if not e or (e["deleted_at"] is not None) != trashed: abort(404)
    check_dept(e["dept_id"])
    return e


def can_write():
    return g.user["role"] in ("admin", "manager", "organizer")


def need_write():
    if not can_write(): abort(403)


def part_view(r):
    return dict(id=r["id"], name=dec(r["name_enc"]), name_en=dec(r["name_en_enc"]), uid=dec(r["uid_enc"]), email=dec(r["email_enc"]),
                spec=r["specialization"], acad=r["academic_status"], gender=r["gender"], nat=r["nationality"], source=r["source"], status=r["status"], checked_in_at=r["checked_in_at"], created_at=r["created_at"])


def form_row(f, row=0):
    """يقرأ حقول المشارك من نموذج HTML (يدوي/تسجيل QR/تعديل) في صف موحّد للتحقق."""
    return dict(row=row, name=clean_text(f.get("name"), 120), name_en=clean_text(f.get("name_en"), 120), uid=clean_text(f.get("uid"), 24),
                email=clean_text(f.get("email"), 120).lower(), spec=clean_text(f.get("spec"), 80),
                acad=clean_text(f.get("acad"), 20), gender=clean_text(f.get("gender"), 20), nat=clean_text(f.get("nat"), 20))


def cert_part(p):
    """بيانات المشارك كما تدخل في الشهادة (مفكوكة التشفير) من صف participants."""
    return dict(name=dec(p["name_enc"]), name_en=dec(p["name_en_enc"]), uid=dec(p["uid_enc"]), spec=p["specialization"],
                acad=p["academic_status"], gender=p["gender"], nat=p["nationality"])


def ts_fmt(ts, fmt="%Y-%m-%d %H:%M"):
    if not ts: return "—"
    return datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=4))).strftime(fmt)  # توقيت عُمان


def mask_name(n):
    parts = n.split()
    if len(parts) <= 1: return (n[:1] + "•••") if n else ""
    return parts[0] + " " + " ".join(p[0] + "." for p in parts[1:])


def mask_email(e):
    if "@" not in e: return ""
    a, b = e.split("@", 1)
    return a[:2] + "•••@" + b


def new_verify_code():
    al = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    c = "".join(secrets.choice(al) for _ in range(12))
    return f"{c[:4]}-{c[4:8]}-{c[8:]}"


def csv_safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in "=+-@\t\r" else v
