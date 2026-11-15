# Deployment

Use Python 3.13+, PostgreSQL and an HTTPS reverse proxy. Install `requirements.txt` in a virtual environment. Use a dedicated SupportFlow database, never another service's database.

Configure the process manager or hosting platform:

```text
DEBUG=false
DEMO_MODE=false
SECRET_KEY=<at least 50 random characters; keep stable across restarts>
ALLOWED_HOSTS=support.example.com
CSRF_TRUSTED_ORIGINS=https://support.example.com
SUPPORTFLOW_DATABASE_URL=postgresql://supportflow:<password>@db:5432/supportflow
SUPPORTFLOW_REDIS_URL=redis://cache:6379/1
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DEFAULT_FROM_EMAIL=SupportFlow <support@example.com>
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
EMAIL_HOST_USER=<SMTP user>
EMAIL_HOST_PASSWORD=<SMTP password>
AI_PROVIDER=demo
```

Use your database provider's TLS options, such as `?sslmode=require`. Use private authenticated Redis or `rediss://` when needed. Generic `DATABASE_URL` and `SUPPORTFLOW_ALLOWED_HOSTS` are not settings; use the names above.

Set `AI_PROVIDER=openai-compatible`, `AI_BASE_URL`, `AI_API_KEY` and `AI_MODEL` for remote AI. Demo AI stays labeled if retained.

## Release

```bash
python -m pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py check --deploy --fail-level WARNING
gunicorn supportflow.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60 --access-logfile - --error-logfile -
```

Do not seed production. Register the initial workspace through the app. Django administration is optional; create a superuser separately if needed.

WhiteNoise serves collected assets. Set `TRUST_PROXY_SSL_HEADER=true` only if the trusted HTTPS proxy strips client forwarding headers and sets `X-Forwarded-Proto` itself. Keep `SECURE_SSL_REDIRECT=true`. Restrict Gunicorn's port to the proxy.

Secure cookies, HSTS and HTTPS redirects turn on with `DEBUG=false`. HSTS includes subdomains and preload, so the hostname and its subdomains must support HTTPS. A shared Redis cache keeps rate limits consistent across workers. The file cache fallback suits one host sharing its cache directory.

## Operations

- Poll GET `/health/` and alert on failures. Restrict logs and set retention limits.
- Back up PostgreSQL regularly; test restoration into a separate database. Back up before migrations. Roll back code only when compatible with the installed schema.
- Keep secrets outside version control. Rotate SMTP/provider credentials through the deployment platform.
- Verify registration, sign-in, invitations, password reset, replies, assignment, resolution and feedback on staging before rollout.
- Conversations stay in the workspace. Customer email delivery, inbound email ingestion, attachments, billing and background jobs are not implemented.
