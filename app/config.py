"""إعدادات النظام — كل الأسرار تأتي من متغيرات البيئة أو ملف المفاتيح المحلي (صلاحية 600)."""
import base64, hashlib, hmac, json, os, pathlib, secrets

BASE = pathlib.Path(__file__).resolve().parent.parent


def _load_dotenv():
    """يقرأ ملف .env (KEY=VALUE) من جذر المشروع دون مكتبات إضافية. متغيرات البيئة الفعلية لها الأولوية."""
    f = BASE / ".env"
    if not f.is_file():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k.startswith("INJAZ_") and k not in os.environ:
            os.environ[k] = v


_load_dotenv()
DATA = pathlib.Path(os.environ.get("INJAZ_DATA_DIR", BASE / "data"))
for sub in ("certs", "staging", "tmp", "outbox", "backups"):
    (DATA / sub).mkdir(parents=True, exist_ok=True)
try:
    os.chmod(DATA, 0o700)
except OSError:
    pass

DB_PATH = DATA / "injaz.db"


def _load_master() -> bytes:
    env = os.environ.get("INJAZ_MASTER_KEY")
    if env:
        return base64.urlsafe_b64decode(env + "=" * (-len(env) % 4))
    f = DATA / ".keys"
    if f.exists():
        return base64.urlsafe_b64decode(json.loads(f.read_text())["master"])
    k = secrets.token_bytes(32)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"master": base64.urlsafe_b64encode(k).decode(),
                   "note": "احتفظ بنسخة من هذا المفتاح خارج السيرفر — بدونه لا يمكن فك تشفير البيانات"}, fh)
    return k


MASTER = _load_master()


def subkey(label: str) -> bytes:
    return hmac.new(MASTER, b"injaz/" + label.encode(), hashlib.sha256).digest()


def env(name, default=""):
    return os.environ.get(name, default)


class Cfg:
    BASE_URL = env("INJAZ_BASE_URL", "http://localhost:8080").rstrip("/")
    DEVELOPER_URL = env("INJAZ_DEVELOPER_URL")  # اختياري: رابط يُجعل عليه اسم المطوّر في التذييل (http/https فقط)
    HTTPS = env("INJAZ_HTTPS", "0") == "1"
    SMTP_HOST = env("INJAZ_SMTP_HOST")
    SMTP_PORT = int(env("INJAZ_SMTP_PORT", "587"))
    SMTP_USER = env("INJAZ_SMTP_USER")
    SMTP_PASS = env("INJAZ_SMTP_PASS")
    SMTP_FROM = env("INJAZ_SMTP_FROM", "no-reply@example.edu")
    SMTP_SECURITY = env("INJAZ_SMTP_SECURITY", "starttls")  # starttls | ssl | none
    MAIL_MODE = env("INJAZ_MAIL_MODE", "smtp" if env("INJAZ_SMTP_HOST") else "file")
    NOSANDBOX = env("INJAZ_CHROMIUM_NOSANDBOX", "0") == "1"
    MAX_UPLOAD = 5 * 1024 * 1024
    MAX_ROWS = 5000
    SESSION_IDLE = 30 * 60
    SESSION_ABS = 8 * 3600
    ALLOWED_HOSTS = [h.strip().lower() for h in env("INJAZ_ALLOWED_HOSTS").split(",") if h.strip()]  # اختياري: يرفض أي Host آخر
    BACKUP_KEEP = int(env("INJAZ_BACKUP_KEEP", "14"))
    REG_MIN_SECONDS = float(env("INJAZ_REG_MIN_SECONDS", "3"))
