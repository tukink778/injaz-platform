import io, re, datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from flask import Blueprint, Response, abort, g, render_template, request
from .db import db
from .security import *
from .util import *
from . import choices

bp = Blueprint("reports", __name__)


def _filters():
    a = request.args
    return dict(q=clean_text(a.get("q"), 80), dept=a.get("dept", ""), year=a.get("year", ""), cat=a.get("cat", ""),
                status=a.get("status", ""), spec=clean_text(a.get("spec"), 80), dfrom=a.get("dfrom", ""), dto=a.get("dto", ""),
                **{k: (a.get(k, "") if a.get(k, "") in [c for c, *_ in choices.options(k)] else "") for k in ("acad", "gender", "nat")})


def _where(f):
    w, a = " AND e.deleted_at IS NULL", []      # الفعاليات المحذوفة (السلة) خارج الأرشيف والبحث والإحصاءات دائماً
    s = dept_scope()
    if s is not None: w += " AND e.dept_id=?"; a.append(s)
    elif f["dept"].isdigit(): w += " AND e.dept_id=?"; a.append(int(f["dept"]))
    if f["year"].isdigit(): w += " AND substr(e.event_date,1,4)=?"; a.append(f["year"])
    if f["cat"] in CATEGORIES: w += " AND e.category=?"; a.append(f["cat"])
    if f["status"] in ("valid", "revoked"): w += " AND x.status=?"; a.append(f["status"])
    if f["spec"]: w += " AND p.specialization=?"; a.append(f["spec"])
    for k, col in (("acad", "academic_status"), ("gender", "gender"), ("nat", "nationality")):
        if f.get(k): w += f" AND p.{col}=?"; a.append(f[k])
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f["dfrom"]): w += " AND e.event_date>=?"; a.append(f["dfrom"])
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f["dto"]): w += " AND e.event_date<=?"; a.append(f["dto"])
    return w, a


BASE_Q = """SELECT x.*, e.title_ar, e.event_date, e.hours, e.category, e.dept_id, d.name_ar dept_ar, p.specialization, p.academic_status acad, p.gender, p.nationality nat, p.uid_enc, p.uid_idx, p.email_idx, p.email_enc, p.source
  FROM certificates x JOIN events e ON e.id=x.event_id JOIN departments d ON d.id=e.dept_id JOIN participants p ON p.id=x.participant_id WHERE 1=1"""


def _search(f, limit=500):
    w, a = _where(f)
    q = f["q"]
    if q:
        ui, ei = blind("u", q), blind("e", q.lower())
        qq = q.upper()
        w += " AND (x.serial=? OR x.verify_code=? OR (p.uid_idx=? AND p.uid_idx!='') OR (p.email_idx=? AND p.email_idx!='') OR e.title_ar LIKE ? OR e.title_en LIKE ?)"
        a += [qq, qq, ui, ei, f"%{q}%", f"%{q}%"]
        rows = db().execute(BASE_Q + w + " ORDER BY x.id DESC LIMIT ?", (*a, limit)).fetchall()
        # البحث بالاسم: الأسماء مشفّرة فنفك تشفيرها ضمن نفس النطاق
        w0, a0 = _where(f)
        have = {r["id"] for r in rows}
        ql = q.lower()
        for r in db().execute(BASE_Q + w0 + " ORDER BY x.id DESC LIMIT 20000", a0):
            if r["id"] not in have and ql in dec(r["name_enc"]).lower():
                rows.append(r)
                if len(rows) >= limit: break
        return rows
    return db().execute(BASE_Q + w + " ORDER BY x.id DESC LIMIT ?", (*a, limit)).fetchall()


def _filter_opts():
    c = db(); s = dept_scope()
    depts = c.execute("SELECT * FROM departments ORDER BY name_ar").fetchall() if s is None else c.execute("SELECT * FROM departments WHERE id=?", (s,)).fetchall()
    years = [r[0] for r in c.execute("SELECT DISTINCT substr(event_date,1,4) FROM events WHERE deleted_at IS NULL ORDER BY 1 DESC")]
    specs = [r[0] for r in c.execute("SELECT DISTINCT p.specialization FROM participants p JOIN events e ON e.id=p.event_id WHERE p.specialization!='' AND e.deleted_at IS NULL ORDER BY 1")]
    return depts, years, specs


@bp.route("/archive")
@login_required
def archive():
    f = _filters(); rows = _search(f)
    out = [dict(r, name=dec(r["name_enc"]), uid=dec(r["uid_enc"])) for r in rows]
    depts, years, specs = _filter_opts()
    audit("archive.search", "", f["q"][:40]) if f["q"] else None
    return render_template("archive.html", rows=out, f=f, depts=depts, years=years, specs=specs, cats=CATEGORIES)


@bp.route("/people/<uid_idx>")
@login_required
def person(uid_idx):
    if not re.fullmatch(r"[0-9a-f]{32}", uid_idx): abort(404)
    s = dept_scope()
    w = (" AND e.dept_id=?" if s is not None else "") + " AND e.deleted_at IS NULL"
    rows = db().execute(BASE_Q + " AND (p.uid_idx=? OR p.email_idx=?)" + w + " ORDER BY e.event_date DESC", (uid_idx, uid_idx, *([s] if s is not None else []))).fetchall()
    if not rows: abort(404)
    out = [dict(r, name=dec(r["name_enc"]), uid=dec(r["uid_enc"])) for r in rows]
    audit("person.view", uid_idx[:8])
    return render_template("person.html", rows=out, hours=sum(r["hours"] for r in rows if r["status"] == "valid"), pid=uid_idx)


# ---------- الإحصائيات ----------
def compute_stats(f):
    c = db(); w, a = _where(f)
    base = "FROM certificates x JOIN events e ON e.id=x.event_id JOIN departments d ON d.id=e.dept_id JOIN participants p ON p.id=x.participant_id WHERE x.status='valid'" + w
    one = lambda sql, args=a: c.execute(sql, args).fetchall()
    S = {}
    S["total"] = c.execute("SELECT COUNT(*) " + base, a).fetchone()[0]
    S["people"] = c.execute("SELECT COUNT(DISTINCT CASE WHEN p.uid_idx!='' THEN p.uid_idx ELSE p.email_idx END) " + base, a).fetchone()[0]
    S["hours"] = c.execute("SELECT COALESCE(SUM(e.hours),0) " + base, a).fetchone()[0]
    S["events"] = c.execute("SELECT COUNT(DISTINCT e.id) " + base, a).fetchone()[0]
    S["depts"] = c.execute("SELECT COUNT(DISTINCT e.dept_id) " + base, a).fetchone()[0]
    S["revoked"] = c.execute("SELECT COUNT(*) " + base.replace("x.status='valid'", "x.status='revoked'"), a).fetchone()[0]
    auto = c.execute("SELECT COUNT(*) " + base + " AND p.source IN ('qr','excel')", a).fetchone()[0]
    S["auto_pct"] = round(100 * auto / S["total"], 1) if S["total"] else 0
    avg = c.execute("SELECT AVG(x.issued_at - p.created_at) " + base, a).fetchone()[0]
    S["avg_hours"] = round((avg or 0) / 3600, 1)
    ev_w, ev_a = _where(dict(f, status="", spec=""))
    ev_w = ev_w.replace(" AND p.specialization=?", "")
    r = c.execute("SELECT COALESCE(SUM(e.rows_total),0), COALESCE(SUM(e.rows_invalid),0) FROM events e WHERE 1=1" + ev_w, ev_a).fetchone()
    S["accuracy"] = round(100 * (1 - r[1] / r[0]), 1) if r[0] else None
    S["mail_pct"] = None
    m = c.execute("SELECT SUM(CASE WHEN x.email_status='sent' THEN 1 ELSE 0 END), COUNT(*) " + base, a).fetchone()
    S["mail_pct"] = round(100 * (m[0] or 0) / m[1], 1) if m[1] else 0
    S["by_dept"] = [(r[0], r[1]) for r in one("SELECT d.name_ar, COUNT(*) n " + base + " GROUP BY d.id ORDER BY n DESC")]
    S["by_spec"] = [(r[0] or "غير محدد", r[1]) for r in one("SELECT p.specialization, COUNT(*) n " + base + " GROUP BY p.specialization ORDER BY n DESC LIMIT 15")]
    S["by_cat"] = [(CATEGORIES.get(r[0], r[0]), r[1]) for r in one("SELECT e.category, COUNT(*) n " + base + " GROUP BY e.category ORDER BY n DESC")]
    S["by_tpl"] = [(r[0], r[1]) for r in one("SELECT t.name, COUNT(*) n " + base.replace("JOIN departments d", "JOIN templates t ON t.id=e.template_id JOIN departments d") + " GROUP BY t.id ORDER BY n DESC")]
    S["by_month"] = [(r[0], r[1]) for r in one("SELECT substr(e.event_date,1,7) m, COUNT(*) n " + base + " GROUP BY m ORDER BY m")]
    S["by_source"] = {"qr": 0, "excel": 0, "manual": 0}
    for r in one("SELECT p.source, COUNT(*) " + base + " GROUP BY p.source"): S["by_source"][r[0]] = r[1]
    S["top_events"] = [(r[0], r[1], r[2]) for r in one("SELECT e.title_ar, e.event_date, COUNT(*) n " + base + " GROUP BY e.id ORDER BY n DESC LIMIT 8")]
    return S


@bp.route("/stats")
@login_required
def stats():
    f = _filters(); S = compute_stats(f)
    depts, years, specs = _filter_opts()
    top_people = []
    if g.user["role"] in ("admin", "manager"):
        w, a = _where(f)
        for r in db().execute("""SELECT CASE WHEN p.uid_idx!='' THEN p.uid_idx ELSE p.email_idx END k, COUNT(*) n, MAX(x.id) mx, SUM(e.hours) h
            FROM certificates x JOIN events e ON e.id=x.event_id JOIN participants p ON p.id=x.participant_id WHERE x.status='valid' AND (CASE WHEN p.uid_idx!='' THEN p.uid_idx ELSE p.email_idx END)!=''""" + w + " GROUP BY k ORDER BY n DESC LIMIT 8", a):
            nm = db().execute("SELECT name_enc FROM certificates WHERE id=?", (r["mx"],)).fetchone()
            top_people.append((dec(nm[0]), r["n"], r["h"], r["k"]))
    return render_template("stats.html", S=S, f=f, depts=depts, years=years, specs=specs, cats=CATEGORIES, top_people=top_people)


# ---------- التصدير ----------
def _xlsx(headers, rows, title):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = title[:30]; ws.sheet_view.rightToLeft = True
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="29328D"); c.alignment = Alignment(horizontal="center")
    for r in rows: ws.append([csv_safe(v) if isinstance(v, str) else v for v in r])
    for col in ws.columns: ws.column_dimensions[col[0].column_letter].width = 22
    b = io.BytesIO(); wb.save(b)
    return Response(b.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={title}-{datetime.date.today()}.xlsx"})


@bp.route("/export/archive.xlsx")
@roles("admin", "manager")
def export_archive():
    f = _filters(); rows = _search(f, limit=20000)
    audit("export.archive", "", f"n={len(rows)}")
    return _xlsx(["رقم الشهادة", "رمز التحقق", "الاسم", "الرقم الجامعي", "البريد", "التخصص", "الحالة الأكاديمية", "الجنس", "الجنسية", "الفعالية", "التاريخ", "الساعات", "القسم", "الحالة", "تاريخ الإصدار"],
                 [(r["serial"], r["verify_code"], dec(r["name_enc"]), dec(r["uid_enc"]), dec(r["email_enc"]), r["specialization"],
                   choices.label("acad", r["acad"]) if r["acad"] else "", choices.label("gender", r["gender"]) if r["gender"] else "", choices.label("nat", r["nat"]) if r["nat"] else "", r["title_ar"], r["event_date"], r["hours"],
                   r["dept_ar"], "سارية" if r["status"] == "valid" else "ملغاة", ts_fmt(r["issued_at"])) for r in rows], "archive")


@bp.route("/export/stats.xlsx")
@roles("admin", "manager", "viewer")
def export_stats():
    S = compute_stats(_filters()); rows = []
    for k, lab in (("by_dept", "القسم"), ("by_spec", "التخصص"), ("by_cat", "نوع النشاط"), ("by_tpl", "القالب"), ("by_month", "الشهر")):
        for n, v in S[k]: rows.append((lab, n, v))
    for k, lab in (("total", "إجمالي الشهادات"), ("people", "عدد المستفيدين"), ("hours", "إجمالي الساعات"), ("events", "عدد الفعاليات"), ("auto_pct", "نسبة الإصدار آلياً %"), ("avg_hours", "متوسط زمن الإصدار (ساعة)"), ("accuracy", "دقة البيانات %")):
        rows.insert(0, ("مؤشر", lab, S[k]))
    audit("export.stats")
    return _xlsx(["النوع", "البند", "القيمة"], rows, "stats")
