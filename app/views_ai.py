"""أدوات الذكاء في إنجاز.

مبدأ الخصوصية: كل التحليل يجري محلياً داخل الخادم (قواعد + إحصاء + مطابقة نصية).
لا يُرسل أي اسم أو بريد أو رقم جامعي إلى أي خدمة خارجية. المساعد لا يعرض بيانات شخصية أبداً.
الأدوات اختيارية: يعطّلها المشرف من الإعدادات، وتُحجب عن دور «مطّلع».
"""
import colorsys, difflib, io, json, re, time, unicodedata
from collections import Counter, defaultdict
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for
from .db import db, setting
from .security import *
from .util import *
from . import certs, choices, services, views_reports as vr

bp = Blueprint("ai", __name__, url_prefix="/ai")


def ai_required(f):
    import functools

    @functools.wraps(f)
    @login_required
    def w(*a, **k):
        if g.user["role"] == "viewer" or setting(db(), "ai_enabled", "1") != "1":
            abort(403)
        return f(*a, **k)
    return w


# ---------------------------------------------------------------- مساعد إنجاز
def _scope():
    s = dept_scope()
    return ("", []) if s is None else (" AND e.dept_id=?", [s])


def _norm_q(q):
    q = unicodedata.normalize("NFKC", q or "").lower()
    q = re.sub(r"[ً-ْـ]", "", q)
    return q.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي").replace("ة", "ه")


def _has(q, *words):
    return any(_norm_q(w) in q for w in words)


def assistant_answer(raw):
    """يفهم أمراً بالعربية/الإنجليزية ويعيد dict(text, links, bullets). لا يعرض بيانات شخصية."""
    q = _norm_q(raw)
    c = db(); w, a = _scope()
    admin = g.user["role"] == "admin"; rw = g.user["role"] != "viewer"
    L = lambda t, ep, **kw: dict(t=t, u=url_for(ep, **kw))
    out = dict(text="", links=[], bullets=[])
    if _has(q, "انشئ فعاليه", "فعاليه جديده", "اضافه فعاليه", "new event", "create event", "ابدا فعاليه"):
        out.update(text="ابدأ من نموذج الفعالية؛ بعده يرشدك مسار الإصدار خطوة بخطوة (مشاركون ← مراجعة ← قالب ← معاينة ← إصدار ← إرسال).", links=[L("فعالية جديدة", "main.event_new")])
    elif _has(q, "excel", "اكسل", "استيراد", "رفع ملف", "ملف مشاركين", "قالب اكسل"):
        out.update(text="حمّل قالب Excel الرسمي من خطوة «المشاركون» في أي فعالية، واملأه، ثم افحصه مسبقاً بمحلل البيانات قبل الرفع لتكتشف التكرار والأخطاء.",
                   links=[L("محلل البيانات", "ai.data"), L("الفعاليات", "main.events")])
    elif _has(q, "كم شهاد", "عدد الشهادات", "اجمالي الشهادات", "how many cert"):
        n = c.execute("SELECT COUNT(*) FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.status='valid'" + w, a).fetchone()[0]
        rv = c.execute("SELECT COUNT(*) FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.status='revoked'" + w, a).fetchone()[0]
        out.update(text=f"الشهادات السارية الصادرة: {n}" + (f" (ملغاة: {rv})" if rv else "") + ".", links=[L("الإحصائيات", "reports.stats"), L("الأرشيف", "reports.archive")])
    elif _has(q, "كم مشارك", "عدد المشاركين", "كم مستفيد", "المستفيدين"):
        n = c.execute("SELECT COUNT(*) FROM participants p JOIN events e ON e.id=p.event_id WHERE p.status='approved'" + w, a).fetchone()[0]
        out.update(text=f"المشاركون المعتمدون في كل الفعاليات: {n}.", links=[L("الإحصائيات", "reports.stats")])
    elif _has(q, "ساعات", "ساعه"):
        h = c.execute("SELECT COALESCE(SUM(e.hours),0) FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.status='valid'" + w, a).fetchone()[0]
        out.update(text=f"إجمالي الساعات التدريبية المسجلة في شهادات سارية: {h:g} ساعة.", links=[L("الإحصائيات", "reports.stats")])
    elif _has(q, "معلق", "مراجعه", "بانتظار", "pending", "ماذا يجب", "مهام", "ماذا افعل"):
        items = []
        for r in c.execute(f"SELECT e.id, e.title_ar, COUNT(*) n FROM participants p JOIN events e ON e.id=p.event_id WHERE p.status='pending'{w} GROUP BY e.id", a):
            items.append((f"{r['n']} تسجيل بانتظار المراجعة — {r['title_ar']}", url_for("main.flow", eid=r["id"], step=3)))
        for r in c.execute(f"""SELECT e.id, e.title_ar, COUNT(*) n FROM participants p JOIN events e ON e.id=p.event_id WHERE p.status='approved'{w}
              AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=p.id AND x.status='valid') GROUP BY e.id""", a):
            items.append((f"{r['n']} معتمد بانتظار الإصدار — {r['title_ar']}", url_for("main.flow", eid=r["id"], step=6)))
        for r in c.execute(f"SELECT e.id, e.title_ar, COUNT(*) n FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.status='valid' AND x.email_status='failed'{w} GROUP BY e.id", a):
            items.append((f"فشل إرسال {r['n']} بريد — {r['title_ar']}", url_for("main.flow", eid=r["id"], step=7)))
        out["text"] = "لا توجد مهام معلّقة. كل شيء محدّث ✓" if not items else f"لديك {len(items)} مهمة تحتاج إجراءً:"
        out["links"] = [dict(t=t, u=u) for t, u in items[:8]]
    elif _has(q, "فشل", "بريد", "ايميل", "email", "smtp"):
        n = c.execute("SELECT COUNT(*) FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.status='valid' AND x.email_status='failed'" + w, a).fetchone()[0]
        out["text"] = f"رسائل فاشلة حالياً: {n}. افتح الفعالية ← خطوة «الإرسال» لإعادة المحاولة لكل حالة." if n else "لا توجد رسائل فاشلة."
        if admin: out["links"] = [L("إعدادات البريد", "admin.settings")]
    elif _has(q, "قالب", "تصميم", "template"):
        out.update(text="القوالب تُدار من قسم الإدارة: إنشاء، تعديل بالمحرر المرئي، نسخ، تفعيل/إيقاف، حذف." if admin else "اختيار القالب يتم في خطوة 4 داخل مسار الفعالية. إنشاء القوالب للمشرف العام.",
                   links=([L("قوالب الشهادات", "admin.templates")] if admin else []) + [L("مصمم الشهادات الذكي", "ai.designer")])
    elif _has(q, "تحقق", "verify", "صحه شهاد"):
        out.update(text="صفحة التحقق العامة تتيح لأي شخص التأكد من صحة الشهادة برقمها أو بمسح رمز QR.", links=[L("صفحة التحقق", "public.verify_form")])
    elif _has(q, "ارشيف", "سجل", "archive"):
        out.update(text="الأرشيف يحفظ كل الشهادات الصادرة ويمكن البحث فيه بالاسم أو الرقم الجامعي أو رقم الشهادة.", links=[L("الأرشيف", "reports.archive")])
    elif _has(q, "احصاء", "تقرير", "تحليل", "اتجاه", "ملخص", "stats", "report"):
        out.update(text="الإحصائيات التفصيلية ومؤشرات الأداء، أو التحليلات الذكية للملخص الإداري.", links=[L("التحليلات الذكية", "ai.insights"), L("الإحصائيات", "reports.stats")])
    elif _has(q, "امان", "كلمه المرور", "كلمه السر", "خطوتين", "2fa", "password"):
        out.update(text="من «حسابي» تغيّر كلمة المرور وتفعّل التحقق بخطوتين.", links=[L("حسابي", "auth.account")])
    elif _has(q, "مستخدم", "صلاحي", "users") and admin:
        out.update(text="إدارة المستخدمين والأدوار والأقسام.", links=[L("المستخدمون", "admin.users"), L("الأقسام", "admin.departments")])
    else:
        words = [x for x in re.split(r"\s+", raw.strip()) if len(x) >= 3][:4]
        evs = []
        for wd in words:
            evs += c.execute(f"SELECT e.id, e.title_ar FROM events e WHERE (e.title_ar LIKE ? OR e.title_en LIKE ?){w} ORDER BY e.event_date DESC LIMIT 4", (f"%{wd}%", f"%{wd}%", *a)).fetchall()
        seen, links = set(), []
        for e in evs:
            if e["id"] not in seen: seen.add(e["id"]); links.append(L(e["title_ar"], "main.event_view", eid=e["id"]))
        if links:
            out.update(text="وجدت فعاليات قد تقصدها:", links=links[:6])
        else:
            out.update(text="لم أفهم طلبك تماماً. جرّب إحدى هذه الصيغ:", bullets=[
                "كم شهادة صدرت؟", "ما المهام المعلّقة؟", "كيف أنشئ فعالية جديدة؟", "كيف أرفع ملف Excel؟", "ما عدد الساعات التدريبية؟", "لماذا فشل إرسال البريد؟"])
    return out


@bp.route("/assistant", methods=["GET", "POST"])
@ai_required
def assistant():
    q = clean_text(request.values.get("q"), 200)
    ans = None
    if q:
        if rate_hit(f"ai:{g.user['id']}", 60, 600): abort(429)
        ans = assistant_answer(q)
        audit("ai.assistant", "", q[:60])
    return render_template("ai_assistant.html", q=q, ans=ans)


# ---------------------------------------------------------------- محلل البيانات
_DOMAIN_FIX = {"gmial.com": "gmail.com", "gmai.com": "gmail.com", "gamil.com": "gmail.com", "gmail.con": "gmail.com", "gmail.co": "gmail.com", "gmail.comm": "gmail.com",
               "gnail.com": "gmail.com", "gmal.com": "gmail.com", "hotmial.com": "hotmail.com", "hotmal.com": "hotmail.com", "hotmail.con": "hotmail.com",
               "outlok.com": "outlook.com", "outlook.con": "outlook.com", "yaho.com": "yahoo.com", "yahoo.con": "yahoo.com", "icloud.con": "icloud.com"}
ERR_HELP = {
    "الاسم غير صالح": "الاسم قصير جداً أو يحتوي رموزاً غير مسموحة. اكتب الاسم الكامل بحروف عربية أو إنجليزية.",
    "الاسم الإنجليزي غير صالح": "عمود Name (English) يحتوي رموزاً غير مسموحة؛ اتركه فارغاً أو صحّحه.",
    "البريد غير صالح": "صيغة البريد خاطئة. الصيغة الصحيحة: name@domain.com دون مسافات.",
    "البريد مفقود": "البريد ضروري لإرسال الشهادة. أضفه أو احذف الصف.",
    "الرقم الجامعي مفقود": "هذه الفعالية تشترط الرقم الجامعي. أكمله أو اجعله اختيارياً في إعدادات الفعالية.",
    "الرقم الجامعي غير صالح": "الرقم الجامعي يقبل أحرفاً وأرقاماً وشرطة فقط (3–24 خانة).",
    "التخصص مفقود": "الفعالية تشترط التخصص. أكمله لكل صف.",
    "«الحالة الأكاديمية» غير صالحة": "القيم المقبولة: طالب أو خريج (Student / Graduate). أو اتركها فارغة.",
    "«الجنس» غير صالحة": "القيم المقبولة: ذكر أو أنثى (Male / Female). أو اتركها فارغة.",
    "«الجنسية» غير صالحة": "القيم المقبولة: عماني أو غير عماني (Omani / Non-Omani). أو اتركها فارغة.",
    "مكرر": "هذا الشخص مكرر في الملف (نفس الرقم الجامعي أو البريد). يُبقى الصف الأول.",
}


def _arnorm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[ً-ْـ]", "", s)
    for x, y in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")): s = s.replace(x, y)
    return re.sub(r"\s+", " ", s).strip()


def suggest_email(e):
    """يعيد بريداً مصحّحاً مقترحاً أو None."""
    if not e: return None
    f = e.strip().replace(" ", "").replace(",", ".").replace("،", ".").rstrip(".").lower()
    f = re.sub(r"@+", "@", f)
    if "@" in f:
        local, _, dom = f.partition("@")
        dom = _DOMAIN_FIX.get(dom, dom)
        f = local + "@" + dom
    return f if f != e and EMAIL_RE.match(f) else None


def analyze_rows(rows, require_uid=True, require_spec=False):
    """تحليل ملف المشاركين. دالة نقية قابلة للاختبار. تعيد dict بالملخص والمشاكل والصفوف المنظّفة."""
    ev = dict(require_uid=1 if require_uid else 0, require_spec=1 if require_spec else 0)
    issues, cleaned, review = [], [], []
    fixed_rows = []
    for r in rows:
        r = dict(r); fx = []
        s = suggest_email(r["email"])
        if s: fx.append(f"البريد «{r['email']}» ← «{s}»"); r["email"] = s
        fixed_rows.append((r, fx))
    just = [r for r, _ in fixed_rows]
    good, bad = services.validate_rows(just, ev, set())
    bad_by_row = {b["row"]: b for b in bad}
    fix_by_row = {r["row"]: fx for r, fx in fixed_rows if fx}
    for rowno, fx in fix_by_row.items():
        for t in fx: issues.append(dict(row=rowno, sev="fix", msg="تصحيح تلقائي: " + t, help="طُبّق التصحيح في الملف المنظّف. راجعه قبل الاعتماد."))
    for b in bad:
        for e in b["errors"]:
            issues.append(dict(row=b["row"], sev="err", msg=e, help=ERR_HELP.get(e, "")))
        review.append(b)
    # فحوص إضافية (تحذيرات لا تمنع الاستيراد)
    names = [(r["row"], r["name"], _arnorm(r["name"]), r["uid"]) for r in just if r["name"]]
    for rowno, name, nn, _ in names:
        if len(nn.split()) < 2: issues.append(dict(row=rowno, sev="warn", msg="الاسم كلمة واحدة", help="للشهادة يُفضَّل الاسم الثنائي أو الكامل."))
        if re.search(r"\d", name): issues.append(dict(row=rowno, sev="warn", msg="الاسم يحتوي أرقاماً", help="قد يكون الرقم الجامعي مكتوباً في عمود الاسم."))
    buckets = defaultdict(list)
    for t in names: buckets[t[2][:3]].append(t)
    pairs = 0
    for grp in buckets.values():
        grp = grp[:150]
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                (r1, n1, a1, u1), (r2, n2, a2, u2) = grp[i], grp[j]
                if a1 == a2 and u1 and u1 == u2: continue  # تكرار مؤكد يلتقطه المدقق
                if difflib.SequenceMatcher(None, a1, a2).ratio() >= 0.92 and pairs < 50:
                    pairs += 1
                    issues.append(dict(row=r2, sev="warn", msg=f"اسم مشابه جداً لصف {r1}", help="قد يكون الشخص نفسه مكتوباً بصيغتين. تأكد قبل الإصدار حتى لا تصدر شهادتان."))
    by_mail = defaultdict(set)
    for r in just:
        if r["email"] and r["name"]: by_mail[r["email"]].add((r["row"], _arnorm(r["name"])))
    for mail, items in by_mail.items():
        if len({n for _, n in items}) > 1:
            issues.append(dict(row=min(x for x, _ in items), sev="warn", msg="نفس البريد لأسماء مختلفة", help="ستصل عدة شهادات إلى بريد واحد. تأكد أنه مقصود."))
    lens = Counter(len(r["uid"]) for r in just if r["uid"])
    if lens:
        common = lens.most_common(1)[0][0]
        if lens[common] >= max(3, len(just) // 2):
            for r in just:
                if r["uid"] and len(r["uid"]) != common: issues.append(dict(row=r["row"], sev="warn", msg=f"طول الرقم الجامعي {len(r['uid'])} بينما الغالب {common}", help="غالباً خطأ كتابة. راجع الرقم."))
    for g_ in good: cleaned.append(g_)
    sev_n = Counter(i["sev"] for i in issues)
    total = len(just)
    return dict(total=total, good=len(good), bad=len(bad), fixes=sev_n["fix"], warns=sev_n["warn"], errors=sev_n["err"],
                dups=sum(1 for b in bad if "مكرر" in b["errors"]), issues=sorted(issues, key=lambda i: (i["row"], i["sev"])), cleaned=cleaned, review=review,
                ready_pct=round(100 * len(good) / total, 1) if total else 0)


def summary_text(A):
    if not A["total"]: return "الملف لا يحتوي صفوفاً."
    parts = [f"حُلّل {A['total']} صفاً: {A['good']} جاهز للاستيراد ({A['ready_pct']}%)"]
    if A["bad"]: parts.append(f"{A['bad']} يحتاج تصحيحاً" + (f" منها {A['dups']} مكرر" if A["dups"] else ""))
    if A["fixes"]: parts.append(f"طُبّق {A['fixes']} تصحيح تلقائي للبريد")
    if A["warns"]: parts.append(f"{A['warns']} تنبيه يستحق المراجعة")
    verdict = "الملف جاهز للرفع." if not A["bad"] else "صحّح الصفوف المعلّمة ثم ارفع الملف المنظّف."
    return "، ".join(parts) + ". " + verdict


def build_clean_xlsx(good, review):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "المشاركون"; ws.sheet_view.rightToLeft = True
    head = ["الاسم الكامل", "Name (English)", "الرقم الجامعي", "البريد الإلكتروني", "التخصص", "الحالة الأكاديمية", "الجنس", "الجنسية"]
    ws.append(head)
    fill = PatternFill("solid", fgColor="29328D")
    for i, c in enumerate(ws[1], 1):
        c.font = Font(bold=True, color="FFFFFF", name="Arial"); c.fill = fill; c.alignment = Alignment(horizontal="center")
        ws.column_dimensions[c.column_letter].width = [28, 26, 18, 30, 24, 18, 12, 16][i - 1]
    for r in good: ws.append([csv_safe(r["name"]), csv_safe(r["name_en"]), csv_safe(r["uid"]), csv_safe(r["email"]), csv_safe(r["spec"]),
                                  choices.label("acad", r.get("acad", "")) if r.get("acad") else "", choices.label("gender", r.get("gender", "")) if r.get("gender") else "",
                                  choices.label("nat", r.get("nat", "")) if r.get("nat") else ""])
    for r in range(2, ws.max_row + 1): ws.cell(r, 3).number_format = "@"
    if review:
        w2 = wb.create_sheet("تحتاج مراجعة"); w2.sheet_view.rightToLeft = True
        w2.append(["الصف الأصلي", "الاسم الكامل", "الرقم الجامعي", "البريد", "المشكلة"])
        for r in review: w2.append([r["row"], csv_safe(r["name"]), csv_safe(r["uid"]), csv_safe(r["email"]), csv_safe(" · ".join(r["errors"]))])
        w2.column_dimensions["E"].width = 50
    b = io.BytesIO(); wb.save(b); return b.getvalue()


@bp.route("/data", methods=["GET", "POST"])
@ai_required
def data():
    A = summ = token = None
    if request.method == "POST":
        if rate_hit(f"aidata:{g.user['id']}", 20, 600): abort(429)
        f = request.files.get("file")
        if not f or not (f.filename or "").lower().endswith(".xlsx"):
            flash("اختر ملف Excel بصيغة xlsx", "err")
        else:
            try:
                rows = services.parse_xlsx(f.read())
                A = analyze_rows(rows, request.form.get("require_uid") == "1", request.form.get("require_spec") == "1")
                summ = summary_text(A)
                token = services.stage_save(g.user["id"], dict(good=[{k: r[k] for k in ("name", "name_en", "uid", "email", "spec", "acad", "gender", "nat")} for r in A["cleaned"]],
                                                                review=[{k: r[k] for k in ("row", "name", "uid", "email", "errors")} for r in A["review"]]))
                audit("ai.data.analyze", "", f"rows={A['total']} bad={A['bad']} fixes={A['fixes']}")
            except services.ImportError_ as e:
                flash(str(e), "err")
    return render_template("ai_data.html", A=A, summ=summ, token=token, ERR=ERR_HELP, req_uid=request.form.get("require_uid", "1"), req_spec=request.form.get("require_spec", "0"))


@bp.route("/data/cleaned/<token>.xlsx")
@ai_required
def data_cleaned(token):
    p = services.stage_load(token, g.user["id"])
    if not p: abort(404)
    audit("ai.data.download", "", f"rows={len(p['good'])}")
    return Response(build_clean_xlsx(p["good"], p["review"]), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=injaz-cleaned.xlsx", "Cache-Control": "no-store"})


# ---------------------------------------------------------------- مصمم الشهادات الذكي
def _hex2hsl(h):
    r, g_, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)); hh, l, s = colorsys.rgb_to_hls(r, g_, b); return hh, s, l


def _hsl2hex(h, s, l):
    r, g_, b = colorsys.hls_to_rgb(h % 1, max(0, min(1, l)), max(0, min(1, s))); return "#%02X%02X%02X" % (round(r * 255), round(g_ * 255), round(b * 255))


def lum(h):
    f = lambda v: v / 12.92 if v <= .03928 else ((v + .055) / 1.055) ** 2.4
    r, g_, b = (f(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5)); return .2126 * r + .7152 * g_ + .0722 * b


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True); return round((la + .05) / (lb + .05), 2)


MOODS = {
    "formal": ("رسمي وأكاديمي", "#1F2A68", 0.11, "جدول/فقرة رسمية بخط واضح وهوامش واسعة", "table"),
    "modern": ("عصري وتقني", "#4F46E5", 0.50, "عنوان كبير مع مساحات بيضاء وتوزيع متوازن", "elegant"),
    "warm": ("دافئ ومحفّز", "#8E2B46", 0.08, "عنوان بارز بلون مميز وفقرة قصيرة مركزة", "paragraph"),
    "nature": ("هادئ وطبيعي", "#0F6B5C", 0.12, "ألوان هادئة وتباعد مريح بين الأسطر", "paragraph"),
}


def design_suggestions(mood, base=None, orientation="landscape"):
    name, p0, accent_shift, tip, layout = MOODS.get(mood, MOODS["formal"])
    base = base if base and re.fullmatch(r"#[0-9a-fA-F]{6}", base) else p0
    h, s, l = _hex2hsl(base)
    primary = _hsl2hex(h, min(max(s, .45), .85), min(max(l, .18), .36))   # داكن كفاية ليكون النص الأبيض مقروءاً
    sug = []
    for label, dh, why in (("تكميلي متباين", .5, "لون مقابل في الدائرة اللونية يعطي أقوى تمييز للعنوان."), ("مجاور هادئ", .08, "ألوان متقاربة تعطي مظهراً متناسقاً وهادئاً."), ("ثلاثي حيوي", .33, "ثلاثة ألوان متباعدة لإحساس شبابي ونشيط.")):
        acc = _hsl2hex(h + dh, .75, .5)
        sug.append(dict(label=label, why=why, primary=primary, accent=acc, contrast=contrast(primary, "#FFFFFF"), accent_contrast=contrast(acc, "#FFFFFF"),
                        layout=layout, orientation=orientation))
    tips = [tip, "اجعل العنوان أكبر عنصر، والاسم ثاني أبرز عنصر، والتفاصيل أصغر.",
            "استخدم لونين فقط: الأساسي للعناوين والرأس، والمميز للخط الفاصل والعنوان الرئيسي.",
            "اترك ما لا يقل عن 15 مم هوامش حول الشهادة، ولا تضع QR فوق نص.",
            "ضع الختم والتوقيع في أسفل الشهادة وQR في الزاوية المقابلة لتوازن التركيب."]
    if orientation == "portrait": tips.append("في الوضع العمودي قلّل طول العنوان واجعل الاسم في منتصف الارتفاع.")
    return dict(mood_name=name, items=sug, tips=tips)


@bp.route("/designer", methods=["GET", "POST"])
@ai_required
def designer():
    res = None; f = request.form
    if request.method == "POST":
        res = design_suggestions(f.get("mood", "formal"), f.get("base", "").strip(), "portrait" if f.get("orientation") == "portrait" else "landscape")
    return render_template("ai_designer.html", res=res, MOODS=MOODS, f=f)


@bp.route("/designer/create", methods=["POST"])
@roles("admin")
def designer_create():
    if setting(db(), "ai_enabled", "1") != "1": abort(403)
    f = request.form
    layout = f.get("layout") if f.get("layout") in ("table", "paragraph", "elegant") else "table"
    src = next((c for n, c in certs.PRESETS if c.get("layout") == layout), certs.PRESETS[0][1])
    cfg = dict(src, primary=f.get("primary", ""), accent=f.get("accent", ""), orientation="portrait" if f.get("orientation") == "portrait" else "landscape")
    cfg = certs.clean_cfg(cfg)
    name = "تصميم مقترح: " + (clean_text(f.get("mood_name"), 40) or "جديد")
    tid = db().insert("INSERT INTO templates(name,config,created_at) VALUES(?,?,?)", (name, json.dumps(cfg, ensure_ascii=False), int(time.time()))); audit("template.create", str(tid), "ai.designer")
    flash("أُنشئ قالب من التصميم المقترح. عدّله بالمحرر المرئي ثم فعّله.", "ok")
    return redirect(url_for("admin.template_edit", tid=tid))


# ---------------------------------------------------------------- التحليلات الذكية
def build_insights(S, pending, stale, failed):
    """ملخص نصي مُولَّد من الأرقام. دالة نقية."""
    pts = []   # (مستوى, نص)
    if not S["total"]:
        return [("info", "لا توجد شهادات صادرة ضمن النطاق المحدد بعد.")]
    pts.append(("ok", f"صدرت {S['total']} شهادة لـ {S['people']} مستفيداً عبر {S['events']} فعالية بإجمالي {S['hours']:g} ساعة تدريبية."))
    m = S["by_month"]
    if len(m) >= 2:
        (m1, a), (m2, b) = m[-2], m[-1]
        if a:
            ch = round(100 * (b - a) / a)
            pts.append(("ok" if ch >= 0 else "warn", f"الشهادات في {m2}: {b} مقابل {a} في {m1} ({'+' if ch >= 0 else ''}{ch}%)."))
    if S["by_dept"] and len(S["by_dept"]) > 1:
        t = S["by_dept"][0]; pts.append(("info", f"أكثر الجهات إصداراً: {t[0]} ({round(100 * t[1] / S['total'])}% من الإجمالي)."))
    if S["by_spec"]:
        sp = [x for x in S["by_spec"] if x[0] != "غير محدد"]
        if sp: pts.append(("info", f"أكثر التخصصات استفادة: {sp[0][0]} ({sp[0][1]} شهادة)."))
        nd = next((n for k, n in S["by_spec"] if k == "غير محدد"), 0)
        if nd and nd / S["total"] > .2: pts.append(("warn", f"{round(100 * nd / S['total'])}% من الشهادات بلا تخصص؛ إكمال التخصص يحسّن دقة الإحصائيات."))
    if S["by_cat"]: pts.append(("info", f"أكثر الأنشطة: {S['by_cat'][0][0]} ({S['by_cat'][0][1]} شهادة)."))
    if S["mail_pct"] < 90: pts.append(("warn", f"نسبة التسليم بالبريد {S['mail_pct']}%؛ تابع الرسائل الفاشلة وأعد إرسالها."))
    else: pts.append(("ok", f"نسبة التسليم بالبريد ممتازة ({S['mail_pct']}%)."))
    if S["accuracy"] is not None and S["accuracy"] < 90: pts.append(("warn", f"دقة ملفات Excel {S['accuracy']}%؛ استخدم محلل البيانات قبل الرفع لتقليل الأخطاء."))
    if S["avg_hours"] > 72: pts.append(("warn", f"متوسط الزمن من التسجيل حتى الإصدار {S['avg_hours']} ساعة؛ جرّب الاعتماد التلقائي أو جدولة المراجعة."))
    if pending: pts.append(("warn", f"{pending} تسجيل بانتظار المراجعة."))
    if stale: pts.append(("warn", f"{stale} معتمد لم تصدر شهاداتهم بعد."))
    if failed: pts.append(("err", f"{failed} رسالة بريد فاشلة تحتاج متابعة."))
    if S["revoked"]: pts.append(("info", f"{S['revoked']} شهادة ملغاة ضمن النطاق."))
    return pts


KPI_HELP = [
    ("نسبة الإصدار آلياً", "حصة الشهادات التي جاءت من QR أو Excel لا من الإدخال اليدوي. كلما ارتفعت قلّت الأخطاء والجهد."),
    ("متوسط الزمن للإصدار", "الساعات بين تسجيل المشارك وإصدار شهادته. الأقل أفضل."),
    ("دقة بيانات Excel", "نسبة الصفوف الصحيحة من إجمالي الصفوف المرفوعة. تحسّنها بمحلل البيانات."),
    ("نسبة التسليم بالبريد", "الشهادات التي وصلت بريدياً من إجمالي الصادر. أقل من 90% تستدعي المتابعة."),
]


@bp.route("/insights")
@ai_required
def insights():
    f = vr._filters(); S = vr.compute_stats(f)
    depts, years, specs = vr._filter_opts()
    c = db(); w, a = _scope()
    pending = c.execute("SELECT COUNT(*) FROM participants p JOIN events e ON e.id=p.event_id WHERE p.status='pending'" + w, a).fetchone()[0]
    stale = c.execute("SELECT COUNT(*) FROM participants p JOIN events e ON e.id=p.event_id WHERE p.status='approved' AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=p.id AND x.status='valid')" + w, a).fetchone()[0]
    failed = c.execute("SELECT COUNT(*) FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.status='valid' AND x.email_status='failed'" + w, a).fetchone()[0]
    pts = build_insights(S, pending, stale, failed)
    audit("ai.insights", "", "")
    return render_template("ai_insights.html", S=S, f=f, depts=depts, years=years, pts=pts, kpi_help=KPI_HELP, printing=request.args.get("print") == "1",
                           now=ts_fmt(int(time.time())), scope_name=("كل الأقسام" if dept_scope() is None else (depts[0]["name_ar"] if depts else "")))
