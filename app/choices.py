"""خيارات بيانات الطالب: قيم قياسية تُخزَّن في القاعدة (إنجليزية صغيرة) وتسميات عربية/إنجليزية للعرض.
القيمة الفارغة تعني «غير محدد» (سجلات قديمة أو حقل اختياري في Excel)."""
import re

FIELDS = {
    "acad": dict(col="academic_status", ar="الحالة الأكاديمية", en="Academic status", options=[
        ("student", "طالب", "Student", ["student", "طالب", "طالبة", "طلاب"]),
        ("graduate", "خريج", "Graduate", ["graduate", "خريج", "خريجة", "grad", "alumni", "alumnus"])]),
    "gender": dict(col="gender", ar="الجنس", en="Gender", options=[
        ("male", "ذكر", "Male", ["male", "m", "ذكر", "رجل"]),
        ("female", "أنثى", "Female", ["female", "f", "أنثى", "انثى", "انثي", "أنثي"])]),
    "nat": dict(col="nationality", ar="الجنسية", en="Nationality", options=[
        ("omani", "عماني", "Omani", ["omani", "عماني", "عمانية", "عمان"]),
        ("non_omani", "غير عماني", "Non-Omani", ["non-omani", "non omani", "nonomani", "non_omani", "غير عماني", "غير عمانية", "غير-عماني", "اجنبي", "أجنبي", "وافد"])]),
}


def _k(s): return re.sub(r"[\s_\-]+", " ", str(s or "").strip().lower())


_ALIAS = {f: {_k(a): code for code, _, _, al in d["options"] for a in al + [code]} for f, d in FIELDS.items()}


def norm(field, value):
    """يعيد (الرمز القياسي أو '' إن كانت القيمة فارغة، أو None إن كانت غير معروفة)."""
    v = _k(value)
    if not v: return ""
    return _ALIAS[field].get(v)


def label(field, code, lang="ar"):
    for c, ar, en, _ in FIELDS[field]["options"]:
        if c == code: return ar if lang == "ar" else en
    return "—" if lang == "ar" else "—"


def options(field):
    return [(c, ar, en) for c, ar, en, _ in FIELDS[field]["options"]]


def valid_code(field, code):
    return code == "" or any(c == code for c, *_ in FIELDS[field]["options"])
