from app import create_app
app = create_app()

if __name__ == "__main__":
    import os
    from waitress import serve
    kw = {}
    if os.environ.get("INJAZ_BEHIND_PROXY") == "1":
        # خلف Nginx/Caddy على نفس الجهاز فقط: نثق بترويسات العنوان والبروتوكول منه وحده
        kw = dict(trusted_proxy="127.0.0.1", trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"},
                  clear_untrusted_proxy_headers=True)
    serve(app, host=os.environ.get("INJAZ_HOST", "127.0.0.1"), port=int(os.environ.get("INJAZ_PORT", "8080")), threads=8, ident="INJAZ", **kw)
