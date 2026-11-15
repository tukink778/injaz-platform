from app import create_app
app = create_app()

if __name__ == "__main__":
    import os
    from waitress import serve
    kw = {}
    if os.environ.get("INJAZ_BEHIND_PROXY") == "1":
        # خلف وكيل عكسي (Nginx/Caddy محلياً، أو موزّع Render): نثق بترويسات العنوان والبروتوكول منه وحده.
        # INJAZ_TRUSTED_PROXY: عنوان الوكيل (الافتراضي 127.0.0.1؛ على Render ضعه "*" لأن التطبيق لا يُستقبل إلا عبر موزّعهم)
        # INJAZ_PROXY_COUNT: عدد الوكلاء الموثوقين في السلسلة (يحدد أي عنوان في X-Forwarded-For يُعتمد كعنوان العميل)
        kw = dict(trusted_proxy=os.environ.get("INJAZ_TRUSTED_PROXY") or "127.0.0.1",
                  trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"},
                  clear_untrusted_proxy_headers=True)
        cnt = int(os.environ.get("INJAZ_PROXY_COUNT", "0") or 0)
        if cnt > 0:
            kw["trusted_proxy_count"] = cnt
    # Render يمرّر المنفذ في PORT ويتطلب الاستماع على 0.0.0.0؛ محلياً الافتراضي 127.0.0.1:8080
    port = int(os.environ.get("PORT") or os.environ.get("INJAZ_PORT", "8080"))
    host = os.environ.get("INJAZ_HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    serve(app, host=host, port=port, threads=8, **kw)
