"""Google Gmail / Google Workspace عبر OpenID Connect + Gmail API (users.messages.send). صلاحية gmail.send فقط (لا قراءة ولا حذف)."""
import urllib.parse
from . import http
from .base import Identity, MailProvider, b64url
from .errors import ProviderError
from . import register

SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


@register
class Google(MailProvider):
    key = "google"; label_ar = "حساب Google (Gmail / Workspace)"; label_en = "Google (Gmail / Workspace)"
    scopes = ["openid", "email", SEND_SCOPE]
    send_scopes = [SEND_SCOPE]
    env_id = "INJAZ_GOOGLE_CLIENT_ID"; env_secret = "INJAZ_GOOGLE_CLIENT_SECRET"
    token_url = "https://oauth2.googleapis.com/token"
    min_interval = 0.5

    def authorize_url(self, redirect_uri, state, code_challenge):
        q = dict(client_id=self.client_id(), redirect_uri=redirect_uri, response_type="code", scope=" ".join(self.scopes), state=state,
                 code_challenge=code_challenge, code_challenge_method="S256", access_type="offline", prompt="consent select_account", include_granted_scopes="false")
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q)

    def identity(self, access_token):
        r = http.request("GET", "https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {access_token}"})
        if r.status != 200: raise ProviderError(f"identity_http_{r.status}")
        email = (r.body.get("email") or "").strip().lower()
        if not r.body.get("sub") or "@" not in email: raise ProviderError("identity_incomplete")
        if r.body.get("email_verified") not in (True, "true"): raise ProviderError("email_not_verified")
        return Identity(str(r.body["sub"]), email)

    def send(self, access_token, msg):
        self._check_send(http.request("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", headers={"Authorization": f"Bearer {access_token}"},
                                      json_body=dict(raw=b64url(bytes(msg))), timeout=60))

    def revoke(self, token):
        try: http.request("POST", "https://oauth2.googleapis.com/revoke", form=dict(token=token))
        except Exception: pass     # أفضل جهد: الحذف المحلي يتم دائماً
