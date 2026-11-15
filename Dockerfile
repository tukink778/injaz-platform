FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/opt/pw
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY . .
RUN useradd -r -u 10001 injaz && mkdir -p /app/data && chown -R injaz /app/data && chmod -R a+rX /opt/pw
USER injaz
ENV INJAZ_HOST=0.0.0.0 INJAZ_PORT=8080 INJAZ_DATA_DIR=/app/data INJAZ_CHROMIUM_NOSANDBOX=1
EXPOSE 8080
# Render يمرّر PORT (الافتراضي 10000) ويفحص المسار /healthz؛ محلياً يبقى 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import os,urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz'%(os.environ.get('PORT') or os.environ.get('INJAZ_PORT','8080')),timeout=4).status==200 else 1)"
CMD ["python", "wsgi.py"]
