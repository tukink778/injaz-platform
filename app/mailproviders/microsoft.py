"""Microsoft Outlook / Microsoft 365 / Hotmail عبر Microsoft identity platform + Microsoft Graph (/me/sendMail)."""
import base64, os, urllib.parse
from . import http
from .base import Identity, MailProvider
from .errors import ProviderError
from . import register


@register
class Microsoft(MailProvider):
    key = "microsoft"; label_ar = "حساب Microsoft (Outlook / Microsoft 365)"; label_en = "Microsoft (Outlook / 365)"
    scopes = ["offline_access", "User.Read", "Mail.Send"]
    send_scopes = ["Mail.Send", "https://graph.microsoft.com/Mail.Send"]
    env_id = "INJAZ_MS_CLIENT_ID"; env_secret = "INJAZ_MS_CLIENT_SECRET"
    min_interval = 2.1      # Exchange Online: ~30 رسالة/دقيقة لكل صندوق

    def _base(self):
        t = os.environ.get("INJAZ_MS_TENANT", "common") or "common"
        if not all(ch.isalnum() or ch in "-." for ch in t): t = "common"
        return f"https://login.microsoftonline.com/{t}/oauth2/v2.0"

    @property
    def token_url(self): return self._base() + "/token"

    def authorize_url(self, redirect_uri, state, code_challenge):
        q = dict(client_id=self.client_id(), response_type="code", redirect_uri=redirect_uri, response_mode="query",
                 scope=" ".join(self.scopes), state=state, code_challenge=code_challenge, code_challenge_method="S256", prompt="select_account")
        return self._base() + "/authorize?" + urllib.parse.urlencode(q)

    def identity(self, access_token):
        r = http.request("GET", "https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName", headers={"Authorization": f"Bearer {access_token}"})
        if r.status != 200: raise ProviderError(f"identity_http_{r.status}")
        email = (r.body.get("mail") or r.body.get("userPrincipalName") or "").strip().lower()
        if not r.body.get("id") or "@" not in email: raise ProviderError("identity_incomplete")
        return Identity(str(r.body["id"]), email)

    def send(self, access_token, msg):
        body = msg.get_body(("plain",)); text = body.get_content() if body else ""
        atts = [dict({"@odata.type": "#microsoft.graph.fileAttachment"}, name=a.get_filename() or "attachment", contentType=a.get_content_type(),
                     contentBytes=base64.b64encode(a.get_payload(decode=True)).decode()) for a in msg.iter_attachments()]
        payload = dict(message=dict(subject=str(msg["Subject"] or ""), body=dict(contentType="Text", content=text),
                                    toRecipients=[dict(emailAddress=dict(address=a.strip())) for a in str(msg["To"]).split(",") if a.strip()],
                                    **({"attachments": atts} if atts else {})), saveToSentItems=True)
        self._check_send(http.request("POST", "https://graph.microsoft.com/v1.0/me/sendMail", headers={"Authorization": f"Bearer {access_token}"}, json_body=payload, timeout=60))
