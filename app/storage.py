"""طبقة تخزين ملفات الشهادات (PDF المشفّرة) منفصلة عن القاعدة.
اليوم: القرص المحلي ضمن INJAZ_DATA_DIR/certs (المسارات والأسماء كما كانت تماماً — لا ترحيل ولا تغيير مسارات).
مستقبلاً: لإضافة Object Storage (S3/R2...) أضف صنفاً بالواجهة نفسها (save/load/exists) واختره بـ INJAZ_STORAGE_BACKEND.
المحتوى الذي يُمرَّر هنا مشفَّر مسبقاً (Fernet)، فلا يحتاج الخلفية الجديدة لأي تشفير إضافي."""
import pathlib, re
from . import config


def filename(serial: str) -> str:
    return re.sub(r"[^A-Za-z0-9\-]", "", serial) + ".pdf.enc"


class LocalStorage:
    def __init__(self, root: pathlib.Path):
        self.root = root

    def path(self, serial):
        return self.root / filename(serial)

    def save(self, serial, data: bytes):
        p = self.path(serial); tmp = p.with_suffix(".part")
        tmp.write_bytes(data); tmp.replace(p)           # كتابة ذرّية: لا ملف نصف مكتوب عند انقطاع التشغيل

    def load(self, serial) -> bytes:
        return self.path(serial).read_bytes()

    def exists(self, serial) -> bool:
        return self.path(serial).is_file()

    def delete(self, serial) -> bool:
        """يحذف ملف الشهادة (إن وُجد). يعيد True عند الحذف أو عدم وجود الملف أصلاً."""
        try:
            self.path(serial).unlink(missing_ok=True); return True
        except OSError:
            return False


def _make():
    if config.Cfg.STORAGE_BACKEND != "local":
        raise SystemExit(f"INJAZ_STORAGE_BACKEND={config.Cfg.STORAGE_BACKEND!r} غير مدعوم بعد (المدعوم: local). انظر docs/POSTGRES_NEON_RENDER.md.")
    return LocalStorage(config.DATA / "certs")


certs = _make()
