# ربط حسابات البريد (OAuth 2.0) — Email Account Integration

كل موظف (admin / manager / organizer) يربط بريده الشخصي من **حسابي ← حسابات البريد** ثم يرسل الشهادات منه.
لا تُطلب كلمة مرور البريد إطلاقاً: يسجّل الموظف دخوله عند Microsoft أو Google نفسه ويوافق على صلاحية **الإرسال فقط**.
دور `viewer` لا يملك هذا القسم. SMTP المركزي يبقى كخيار احتياطي.

## 1) المتطلبات
- `INJAZ_BASE_URL` بصيغة **https://** وبدومين فعلي (يُقبل `http://localhost` للتجربة فقط). يجب أن يطابق تماماً ما تسجّله لدى المزوّد.
- حساب مسؤول على Microsoft Entra و/أو Google Cloud.
- لا مكتبات جديدة (الكود يستخدم `cryptography` الموجودة أصلاً).

## 2) Redirect URIs (يجب تسجيلها حرفياً عند المزوّد)
| المزوّد | Redirect URI |
|---|---|
| Microsoft | `https://DOMAIN/account/email/callback/microsoft` |
| Google | `https://DOMAIN/account/email/callback/google` |

للتجربة المحلية: `http://localhost:8080/account/email/callback/microsoft` (و`/google`) مع `INJAZ_BASE_URL=http://localhost:8080`.
استخدم `localhost` وليس `127.0.0.1` مع Google.

## 3) إعداد Microsoft Entra (Outlook / Microsoft 365 / Hotmail)
1. https://entra.microsoft.com ← **Identity ← Applications ← App registrations ← New registration**.
2. الاسم: `INJAZ Mail`. **Supported account types:**
   - لقبول Outlook.com/Hotmail وأي مؤسسة: *Accounts in any organizational directory and personal Microsoft accounts* ← `INJAZ_MS_TENANT=common`.
   - لحصر الربط بمؤسستك فقط: *Single tenant* ← ضع **Directory (tenant) ID** في `INJAZ_MS_TENANT`.
3. **Redirect URI:** المنصة **Web** والقيمة من الجدول أعلاه.
4. **API permissions ← Add a permission ← Microsoft Graph ← Delegated permissions**: أضف `User.Read` و`Mail.Send` و`offline_access` فقط. (إن منعت مؤسستك موافقة المستخدمين فاضغط *Grant admin consent*.)
5. **Certificates & secrets ← New client secret**: انسخ **Value** (وليس Secret ID) فوراً. له تاريخ انتهاء (حتى 24 شهراً): سجّل موعد تجديده.
6. **Overview**: انسخ **Application (client) ID**.
```
INJAZ_MS_CLIENT_ID=<Application (client) ID>
INJAZ_MS_CLIENT_SECRET=<Secret Value>
INJAZ_MS_TENANT=common            # أو Tenant ID
```
حد Exchange Online تقريباً 30 رسالة/دقيقة لكل صندوق؛ النظام يلتزم به تلقائياً (فاصل ~2.1 ث بين الرسائل).

## 4) إعداد Google Cloud (Gmail / Google Workspace)
1. https://console.cloud.google.com ← أنشئ مشروعاً ← **APIs & Services ← Library ← Gmail API ← Enable**.
2. **OAuth consent screen (Google Auth Platform)**:
   - *Internal*: إن كانت مؤسستك Google Workspace وكل المستخدمين فيها (الأسهل، بلا مراجعة من Google).
   - *External*: لحسابات gmail.com. الصلاحية `gmail.send` **حساسة**: أثناء حالة *Testing* تنتهي رموز التجديد خلال 7 أيام ولا يعمل إلا للمستخدمين التجريبيين المضافين؛ للإنتاج يلزم *Publish* مع **تحقق Google (OAuth verification)**.
   - Scopes: `openid` و`email` و`https://www.googleapis.com/auth/gmail.send` فقط.
3. **Credentials ← Create credentials ← OAuth client ID ← Web application**، وأضف **Authorized redirect URI** من الجدول أعلاه.
4. انسخ Client ID وClient secret:
```
INJAZ_GOOGLE_CLIENT_ID=<...apps.googleusercontent.com>
INJAZ_GOOGLE_CLIENT_SECRET=<...>
```

## 4.5) تشغيل الميزة
1. ضع المتغيرات في `.env` (انظر `.env.example`) ثم أعد تشغيل التطبيق (`docker compose up -d` أو `systemctl restart injaz`).
2. المشرف: **حسابات البريد المرتبطة** (من الإعدادات) تعرض حالة «مفعّل» لكل مزوّد.
3. الموظف: **حسابي ← حسابات البريد ← ربط Microsoft / Google** ← يسجّل الدخول عند المزوّد ← يوافق ← يعود للمنصة بحالة «متصل» مع بريده وآخر اتصال.
4. صفحة الفعالية ← «إرسال للبريد»: اختر **المرسِل** (حسابك المرتبط، أو SMTP المركزي). الإرسال دائماً من حساب منفّذ العملية نفسه. خيار «استخدم SMTP عند انتهاء الاتصال» اختياري وبموافقة صريحة منك.
5. عند انتهاء الاتصال/إلغاء التفويض تظهر «إعادة الربط»، وتُحفظ الرسائل الفاشلة بحالة واضحة لإعادة الإرسال بعد الربط.

## 5) التقرير الأمني — كيف تُحمى بيانات المستخدمين
| الخطر | الحماية |
|---|---|
| طلب كلمة مرور البريد | لا يحدث؛ الدخول عند المزوّد الرسمي (Authorization Code Flow من الخادم) |
| CSRF على مسار OAuth | `state` عشوائي (256 بت)، يُخزَّن مجزّأً، **استخدام واحد**، صلاحية 10 دقائق، مربوط بالمستخدم والمزوّد؛ وزر الربط محمي برمز مشتق من جلسة المستخدم |
| اعتراض code | **PKCE (S256)**؛ الـverifier مخزّن مشفّراً مؤقتاً ويُحذف فور الاستخدام |
| redirect مزيّف | `redirect_uri` يُبنى من `INJAZ_BASE_URL` الثابت ويُتحقق منه لدى المزوّد؛ HTTPS مفروض |
| سرقة رمز التجديد من القاعدة | **AES-256-GCM** بمفتاح مشتق من المفتاح الرئيسي (`INJAZ_MASTER_KEY`/`data/.keys`)، مع **AAD** يربط الرمز بـ(المستخدم، المزوّد، هوية الحساب)؛ نسخه إلى صف/مستخدم آخر **يفشل فك تشفيره** |
| رمز الوصول | لا يُخزَّن في القاعدة أبداً؛ في ذاكرة العملية لدقائق فقط ويُجدَّد تلقائياً (وعند 401) |
| خلط الحسابات | قيدان في القاعدة: `UNIQUE(user_id, provider)` و`UNIQUE(provider, provider_user_id)`؛ وكل استعلام يستخدم `user_id` من الجلسة/المهمة وليس من النموذج |
| إرسال من حساب موظف آخر | غير ممكن: لا يوجد معرّف اتصال في أي نموذج، والمهمة تحمل مستخدمها فقط |
| تسريب في السجلات | مرشّح تنقيح لـ `access_token/refresh_token/Authorization: Bearer/code/state/code_verifier/JWT`؛ مُختبَر |
| اطلاع المشرف | يرى المزوّد والبريد والحالة والأخطاء وعدد الحسابات فقط؛ لا رموز ولا محتوى رسائل ولا كلمات مرور، ويمكنه فصل اتصال |
| أقل الصلاحيات | Microsoft: `Mail.Send` + `User.Read` + `offline_access`. Google: `openid` + `email` + `gmail.send`. لا قراءة ولا حذف |
| الفصل | يحذف الرمز محلياً **ويلغيه لدى المزوّد** (Google revoke)؛ مع Microsoft تُلغى الجلسة محلياً وللإلغاء الكامل يزيل المستخدم التطبيق من https://myapps.microsoft.com |
| حدود المزوّد/429 | إعادة محاولة بتأخير متدرّج مع احترام `Retry-After` |

**ما لا تغطيه الحماية البرمجية:** فقدان المفتاح الرئيسي يعني فقدان الرموز (يعيد الموظفون الربط)؛ وتسرّب المفتاح مع القاعدة معاً يكشف الرموز، فاحفظهما في أماكن منفصلة. حماية الخادم نفسه (تحديثات، جدار ناري) مسؤولية الاستضافة. دوّر `Client Secret` قبل انتهائه.

## 6) إضافة مزوّد بريد جديد مستقبلاً (مثلاً Yahoo / Zoho)
1. أنشئ `app/mailproviders/zoho.py`:
```python
from . import http, register
from .base import Identity, MailProvider
from .errors import ProviderError

@register
class Zoho(MailProvider):
    key = "zoho"                                  # أحرف صغيرة وأرقام و_ فقط
    label_ar = "حساب Zoho"; label_en = "Zoho Mail"
    scopes = ["..."]; send_scopes = ["..."]       # أقل صلاحيات تكفي للإرسال
    env_id = "INJAZ_ZOHO_CLIENT_ID"; env_secret = "INJAZ_ZOHO_CLIENT_SECRET"
    token_url = "https://.../oauth/v2/token"
    min_interval = 1.0                            # فاصل بين الرسائل احتراماً لحدود المزوّد

    def authorize_url(self, redirect_uri, state, code_challenge): ...   # رابط الموافقة (مع PKCE S256)
    def identity(self, access_token): return Identity(subject, email)    # هوية ثابتة + بريد
    def send(self, access_token, msg): ...                               # msg: EmailMessage؛ استخدم self._check_send(http.request(...))
    def revoke(self, token): ...                                         # اختياري
```
2. أضف `zoho` إلى سطر الاستيراد في آخر `app/mailproviders/__init__.py`.
3. أضف متغيري البيئة إلى `.env`/`.env.example`، وسجّل Redirect URI: `https://DOMAIN/account/email/callback/zoho`.
هذا كل شيء: الواجهة والإحصاءات والإرسال والتشفير والتنقيح تعمل تلقائياً لأي مزوّد مسجّل، ولا يُعدَّل نظام الإرسال.

## 7) الملفات
- جديدة: `app/emailconn.py` · `app/views_email.py` · `app/mailproviders/{__init__,base,errors,http,microsoft,google}.py` · `app/migrations/m0003_email_connections.py` · `app/templates/{_email_accounts,admin_email_accounts}.html` · `tests/test_emailconn.py`
- معدّلة: `app/services.py` (إرسال الشهادات عبر الحساب المرتبط مع SMTP احتياطي) · `app/config.py` · `app/__init__.py` · قالب الحساب والفعالية · `.env.example`
- الجداول: `user_email_connections` · `oauth_states` (الهجرة تُطبَّق تلقائياً عند التشغيل أو `python manage.py migrate`).

## 8) الاختبارات
`python tests/test_emailconn.py` (ضمن `tests/run_all.py`) بخادم مزوّد وهمي يتحقق فعلياً من state وPKCE وredirect_uri:
ربط Outlook · ربط Gmail · رفض الصلاحيات · الفصل (وإلغاء الرمز لدى Google) · إعادة الربط · انتهاء رمز الوصول (تجديد تلقائي + إعادة محاولة بعد 401) · فشل تجديد Refresh Token · إرسال شهادات عبر Outlook وGmail (PDF مرفق) · منع استخدام اتصال مستخدم آخر · خلو السجلات والقاعدة والصفحات من الرموز · استمرار SMTP.
> الاختبارات تحاكي المزوّدين؛ **الربط الحقيقي لم يُجرَّب** لأنه يتطلب حسابات وتطبيقات مسجّلة لديك.
