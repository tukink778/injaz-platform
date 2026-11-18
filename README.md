# منصة إنجاز INJAZ — إصدار وإدارة الشهادات الإلكترونية

تطبيق ويب (Flask + SQLite محلياً / PostgreSQL في الإنتاج) لإصدار الشهادات وإرسالها بالبريد وأرشفتها وإحصائها، مع تشفير البيانات الشخصية.
**Developed & Programmed by: TURKI ALALAWI**

## 1) هيكل المشروع
```
app/                الخلفية والواجهة (Flask + Jinja + CSS/JS ثابت — لا خطوة بناء Frontend)
  migrations/       هجرات القاعدة (تُطبَّق تلقائياً عند التشغيل)
  templates/ static/
deploy/             Caddyfile · nginx.conf · injaz.service (systemd) · backup.cron
docs/schema.sql · schema.postgres.sql   مخطط القاعدة (للمرجعية)
docs/POSTGRES_NEON_RENDER.md   دليل PostgreSQL (Neon) والنشر على Render
render.yaml         مخطط نشر Render (بلا أسرار)
tests/              اختبارات آلية (run_all.py يشغّلها كلها)
.env.example        كل متغيرات البيئة موثّقة
Dockerfile · docker-compose.yml
manage.py           أوامر الإدارة   ·   wsgi.py  نقطة التشغيل
```
الواجهة تُخدَّم من نفس التطبيق (نفس الأصل)، لذا **لا حاجة لـ CORS ولا لـ API URLs منفصلة**؛ لا تُضِف ترويسات `Access-Control-*`.

## 2) التثبيت والتشغيل محلياً (ويندوز/ماك/لينكس)
```bash
pip install -r requirements.txt            # ويندوز: py -m pip install -r requirements.txt
playwright install chromium                # لتوليد PDF (مرة واحدة)
python manage.py init                      # ينشئ القاعدة وحساب admin بكلمة مرور مؤقتة (تظهر مرة واحدة)
python wsgi.py                             # http://127.0.0.1:8080
```
على لينكس قد تحتاج `playwright install-deps chromium`.

## 3) الحسابات والصلاحيات
| الدور | الصلاحية |
|---|---|
| `admin` | كل شيء: المستخدمون، الأقسام، القوالب، الإعدادات، سجل التدقيق |
| `manager` (رئيس قسم) | إدارة فعاليات قسمه وإصدار الشهادات والإحصائيات |
| `organizer` | إنشاء فعالياته وإدارة مشاركيها وإصدار شهاداتها |
| `viewer` | اطلاع فقط |

- `python manage.py adduser USERNAME ROLE [DEPT_CODE]` يضيف مستخدماً بكلمة مرور عشوائية مؤقتة.
- **حسابات تجريبية لكل صلاحية:** `python manage.py demo` ينشئ `demo_manager` و`demo_organizer` و`demo_viewer` بكلمات مرور **عشوائية** تُطبع على الشاشة (مؤقتة، تُغيَّر عند أول دخول). لا يعمل في وضع الإنتاج (`INJAZ_HTTPS=1`) إلا بـ `--force`. **احذفها قبل الاستخدام الفعلي.**
- لا توجد كلمات مرور ثابتة في الكود.

## 3-أ) سلة المحذوفات والحذف النهائي
- **حذف فعالية** (مشرف عام: كل الأقسام · رئيس قسم: قسمه فقط) مسموح حتى بعد إصدار الشهادات، ويُنقلها إلى **سلة المحذوفات** بعد تأكيد واضح. لا يُمسح شيء، وتختفي الفعالية من الفعاليات والأرشيف والإحصائيات والبحث وروابط التسجيل والتحقق.
- من السلة: **استعادة** أو **حذف نهائي** (بصفحة تأكيد ثانية إلزامية) يمسح المشاركين والشهادات وملفات PDF ورسائل outbox المرتبطة، ويُبقي أثراً في سجل التدقيق بلا بيانات شخصية.
- **حذف تلقائي** بعد 7 أيام (تُعدَّل من الإعدادات أو `INJAZ_TRASH_DAYS`)؛ يعمل عند الإقلاع ودورياً أثناء التشغيل، أو يدوياً: `python manage.py purge-trash`.

## 4) بيانات الطالب (الحقول)
الاسم الكامل · الرقم الجامعي · البريد · التخصص · **الحالة الأكاديمية** (طالب/خريج) · **الجنس** (ذكر/أنثى) · **الجنسية** (عماني/غير عماني).
تظهر في: نموذج التسجيل بالـQR، الإضافة اليدوية، تعديل المشارك، تفاصيل الشخص، جدول المشاركين، البحث والأرشيف (فلاتر)، قالب Excel (أعمدة جديدة وتقبل العربية والإنجليزية)، ومتغيرات الشهادة:
`{academic_status_ar} {academic_status_en} {gender_ar} {gender_en} {nationality_ar} {nationality_en}`.
السجلات القديمة تبقى «غير محدد» بعد الهجرة ولا تتأثر.

## 5) قاعدة البيانات
- SQLite في `data/injaz.db` (تُنشأ تلقائياً). المسار قابل للتغيير بـ `INJAZ_DATA_DIR` (ضعه على قرص دائم).
- **الهجرات:** `app/migrations/mNNNN_*.py`، تُطبَّق تلقائياً عند التشغيل، ويمكن تشغيلها يدوياً: `python manage.py migrate`. المخطط الكامل في `docs/schema.sql`.
- الأسماء والبريد والأرقام الجامعية وملفات PDF **مشفّرة على القرص**؛ البحث عبر فهارس مجزّأة (HMAC).
- **PostgreSQL (Neon):** اضبط `DATABASE_URL` فتُستخدم PostgreSQL تلقائياً (وإلا SQLite). مفتاح `INJAZ_MASTER_KEY` إلزامي في هذا الوضع. التفاصيل الكاملة، الإعداد على Render وNeon، وحفظ المفتاح والملفات: `docs/POSTGRES_NEON_RENDER.md`. الفحص الصحي: `/healthz`.

## 6) البريد الإلكتروني (SMTP)
بدون `INJAZ_SMTP_HOST` يعمل النظام بوضع الاختبار (الرسائل تُحفظ في `data/outbox`).
Gmail: خادم `smtp.gmail.com` منفذ `587` أمان `starttls` وكلمة مرور تطبيق (App Password) وليس كلمة مرور الحساب. بريد الجامعة: اسأل تقنية المعلومات (غالباً Office 365: `smtp.office365.com:587`) وفضّل حساباً مخصصاً للنظام.
القيم كلها في `.env` (انظر `.env.example`).

## 6.5) ربط بريد كل موظف (OAuth 2.0)
كل موظف يربط Outlook/Microsoft 365 أو Gmail/Google Workspace من **حسابي ← حسابات البريد** ويرسل الشهادات من بريده دون مشاركة كلمة المرور (Microsoft Graph وGmail API، صلاحية الإرسال فقط، رموز مشفّرة AES-256-GCM). SMTP المركزي يبقى احتياطياً.
الإعداد الكامل (Microsoft Entra وGoogle Cloud وRedirect URIs والمتغيرات والتقرير الأمني وإضافة مزوّد جديد): **`docs/EMAIL_INTEGRATION.md`**.

## 7) النشر على الإنترنت
**المتطلبات الخارجية (لا يمكن تجهيزها من داخل المشروع):** سيرفر Linux (VPS) بعنوان عام · دومين وسجل DNS من نوع A · حساب SMTP · مكان آمن لنسخة مفتاح التشفير.

### الطريقة أ — Docker + Caddy (الأسهل، HTTPS تلقائي)
```bash
cp .env.example .env     # عبّئه: INJAZ_BASE_URL, INJAZ_ALLOWED_HOSTS, SMTP ... (INJAZ_HTTPS=1)
# في deploy/Caddyfile استبدل injaz.example.edu بدومينك
docker compose up -d --build
docker compose exec injaz python manage.py init     # مرة واحدة: حساب المشرف
```
### الطريقة ب — سيرفر Linux مباشرة (systemd + Nginx/Caddy)
1. انسخ المشروع إلى `/opt/injaz`، أنشئ مستخدماً `injaz` و`python -m venv .venv`، ثم `pip install -r requirements.txt` و`playwright install --with-deps chromium`.
2. `cp .env.example .env` وعبّئه (`INJAZ_HTTPS=1`, `INJAZ_BEHIND_PROXY=1`, `INJAZ_HOST=127.0.0.1`).
3. `python manage.py init` ثم `sudo cp deploy/injaz.service /etc/systemd/system/ && sudo systemctl enable --now injaz`.
4. ضع Nginx (`deploy/nginx.conf`) أو Caddy أمام التطبيق مع شهادة HTTPS (Let's Encrypt/certbot).

### إعدادات الإنتاج
| الموضوع | الإعداد |
|---|---|
| الدومين/الروابط | `INJAZ_BASE_URL` (يدخل في QR وروابط البريد) و`INJAZ_ALLOWED_HOSTS` |
| HTTPS | `INJAZ_HTTPS=1` ← كوكيز `__Host-` آمنة + HSTS |
| ملفات PDF | `data/certs/*.enc` (مشفّرة) ضمن `INJAZ_DATA_DIR` |
| رفع Excel | حد 5MB؛ يُفحص النوع والمحتوى ويُبطَل أي صيغ خبيثة؛ حد الوكيل 8MB |
| النسخ الاحتياطي | `python manage.py backup` (cron في `deploy/backup.cron`)، والاحتفاظ `INJAZ_BACKUP_KEEP`. **انسخ `data/backups` خارج السيرفر.** الاستعادة: `python manage.py restore FILE.zip` |
| مفتاح التشفير | `data/.keys` أو `INJAZ_MASTER_KEY`. **فقدانه = فقدان القدرة على قراءة البيانات.** احفظ نسخة منفصلة. |

## 8) الأمان (ملخص)
تجزئة كلمات المرور القوية وقفل المحاولات الفاشلة وتحديد المعدل · جلسات بمهلة خمول ومطلقة · 2FA (TOTP) · CSRF · CSP/HSTS/COOP وترويسات أمان · التحقق من Host · أدوار وعزل بين الأقسام (يُختبر آلياً) · رفع ملفات محقَّق · سجل تدقيق مترابط (`python manage.py verify-audit`) · صفحة التحقق العامة لا تكشف بريداً أو رقماً جامعياً · لا أسرار في الكود (`.env` مستثنى في `.gitignore`).
**تحديث المكتبات:** نفّذ دورياً `pip install pip-audit && pip-audit -r requirements.txt`.

## 9) الذكاء الاصطناعي (اختياري، محلي)
مساعد إنجاز، محلل بيانات Excel، مصمم الشهادات، التحليلات: تعمل داخل الخادم **دون إرسال بيانات لأي جهة خارجية**، وتُعطَّل من الإعدادات، ولا تتاح لدور `viewer`.

## 10) الاختبار
```bash
python tests/run_all.py
```
مع `DATABASE_URL` لقاعدة اختبار يعمل على PostgreSQL ويضيف `test_postgres.py` (تزامن، نسخ/استعادة، مفتاح التشفير...). يشغّل: الهجرات · ربط البريد OAuth · القوالب والمحرر المرئي · الرحلة الكاملة (إدخال، Excel، QR، اعتماد، إصدار PDF، تحقق، تحميل، بريد، بحث، أرشيف) · مصفوفة الصلاحيات لكل دور.
