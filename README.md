# SupportFlow

A persistent customer support workspace built with Django 5.2, SQL, server-rendered templates and a small JavaScript layer. Includes ticket management, customer profiles, team administration, notifications, activity history, customer satisfaction surveys and reviewed AI suggestions.

## Run locally

Requires Python 3.13 or newer. Run from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export DEBUG=true
export DEMO_MODE=true
export SUPPORTFLOW_DATABASE_URL="sqlite:///$PWD/db.sqlite3"
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000/. Demo accounts share the password `SupportFlow!2026`:

| Account | Role |
| --- | --- |
| admin@supportflow.demo | Admin |
| agent@supportflow.demo | Support agent |
| sarah@supportflow.demo | Support agent |

The seed creates 6 members, 25 customers and 100 tickets with conversations and feedback. Running it again preserves existing data. Seed only a development database. Registration creates a separate, empty workspace. SQLite persists data between restarts.

`.env.example` documents variables. Django does **not** automatically load `.env`; export variables or configure your process manager. `SUPPORTFLOW_DATABASE_URL` selects the database. Generic `DATABASE_URL` is deliberately ignored to prevent access to another application's database.

## Workflows

- Create a customer, then a ticket. Search, filter, sort and assign it from the ticket workspace.
- Add replies or internal notes. Replies are stored in SupportFlow; this version does not send customer email or ingest an email inbox.
- Review AI suggestions before adding a draft or saving suggested metadata. AI never sends a reply or changes ticket fields automatically.
- Resolve a ticket and copy its feedback link for the customer. Ratings feed dashboard satisfaction metrics.
- Admins invite colleagues, change roles and deactivate members. Deactivation unassigns tickets; the final active admin cannot be removed.
- Notifications and the activity feed reflect stored changes. The notification indicator refreshes every 30 seconds.

## Architecture

See [architecture and database design](docs/architecture.md). Page handlers and the JSON API share transactional operations in `desk/services.py`. Both enforce workspace membership. Stale ticket changes return a conflict instead of overwriting a newer edit.

## API

The API at `/api/v1/` uses sessions and Django CSRF protection. Sign in first, then GET `/api/v1/csrf/`; pass its `csrfToken` as `X-CSRFToken` on writes with `Content-Type: application/json`.

| Resource | Methods |
| --- | --- |
| `/api/v1/tickets/` | GET, POST |
| `/api/v1/tickets/<id>/` | GET, PATCH, DELETE |
| `/api/v1/tickets/<id>/messages/` | GET, POST |
| `/api/v1/tickets/<id>/ai/` | POST |
| `/api/v1/customers/` | GET, POST |
| `/api/v1/customers/<id>/` | GET, PATCH |
| `/api/v1/team/`, `/api/v1/activity/`, `/api/v1/metrics/`, `/api/v1/me/` | GET |
| `/api/v1/notifications/` | GET |
| `/api/v1/notifications/<id>/read/`, `/api/v1/notifications/read-all/` | POST |

Lists support `page` and `page_size` (maximum 100). Tickets also accept `q`, `status`, `priority`, `assignee`, `category` and `sort`; metrics accept `days=7`, `30` or `90`. Read a ticket to obtain its `revision`; include it when updating, deleting, adding messages or requesting AI. Conflicts return HTTP 409. Errors contain an `error` object with `code`, `message` and optional `fields`.

## AI

`AI_PROVIDER=demo` uses deterministic local rules and is visibly labeled **Demo AI**. No external calls or credentials are needed.

For a compatible external provider, set `AI_PROVIDER=openai-compatible`, `AI_BASE_URL=https://your-provider.example/v1`, `AI_API_KEY` and `AI_MODEL`. The provider must support `/chat/completions` and JSON object responses. The adapter bounds public conversation input, excludes internal notes, validates output, uses a timeout, and logs execution metadata. Other protocols can be added behind `desk/ai.py`.

## Validation

Use a dedicated local database and cache directory:

```bash
export DEBUG=true
export SUPPORTFLOW_DATABASE_URL="sqlite:///$PWD/test-local.sqlite3"
export SUPPORTFLOW_CACHE_DIR="$PWD/.cache/test"
python manage.py test desk.tests desk.test_ai
python manage.py makemigrations --check --dry-run
ruff check .
```

Django creates a separate test database. GET `/health/` checks database connectivity. See [deployment instructions](docs/deployment.md) for production configuration and operational limits.
