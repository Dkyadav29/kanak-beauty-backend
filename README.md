# Kanak Beauty Service — Backend (Django REST Framework + MariaDB)

Production-oriented REST API for the Kanak Beauty Service platform. It implements the contract the React frontend (repo root) already uses: `../docs/API_CONTRACTS.md`. Every protected endpoint enforces role and ownership rules on the server; the frontend's guards are only UX.

## Stack

Python 3.12+ · Django 6.0 · Django REST Framework 3.18 · djangorestframework-simplejwt (JWT access/refresh with rotation and blacklist) · drf-spectacular (OpenAPI 3) · django-filter · django-cors-headers · python-decouple · MariaDB 10.11 via Django's MySQL backend (PyMySQL driver; `mysqlclient` optional) · pytest + pytest-django · Black + Ruff · Gunicorn + WhiteNoise.

## Project layout

```
backend/
├── config/            settings (base / development / production / test), urls, wsgi, asgi
├── common/            ids (prefixed ULID-like PKs), base models, permissions (role → permission
│                      vocabulary + DRF classes), pagination, error envelope, validators, throttles, utils
├── apps/
│   ├── accounts/      custom User (role, profile fields, referral code), OTP, reset tokens, login audit
│   ├── authentication/ register / OTP / login / logout / refresh / me / forgot & reset password
│   ├── core/          AuditLog + log_audit(), admin audit-log API, seed_demo command
│   ├── courses/       trainers, courses, modules, lessons, materials; public catalogue; admin management
│   ├── batches/       batches, memberships, history timeline, live sessions, attendance, recordings, join/stream signing
│   ├── enrollments/   course access, lesson progress, completion evaluation (the completion rules)
│   ├── payments/      orders, signed webhook (idempotent), sandbox gateway (dev only), admin refunds/reconcile
│   ├── certificates/  issuance (server-side eligibility), revoke/reissue, public verification
│   ├── referrals/     Refer & Earn programme config, referrals, wallet ledger, pricing hooks
│   └── reports/       admin overview, analytics, student directory/detail
├── docs/CONVENTIONS.md
├── scripts/mariadb_setup.sql
├── conftest.py        shared pytest fixtures
├── requirements.txt   pinned dependencies
├── gunicorn.conf.py
└── .env.example
```

## Quick start (local development)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            # then edit: DB_PASSWORD, secrets
```

### 1. Create the MariaDB database and least-privilege user

Edit the password placeholder in `scripts/mariadb_setup.sql`, then run it as an admin user:

```bash
mysql -u root -p < scripts/mariadb_setup.sql
```

It creates `kanak_academy` (utf8mb4) and the user `kanak` with data + DDL rights on that database only, plus rights on `test_kanak_academy` for the test runner. Put the same password in `.env` (`DB_PASSWORD`).

### 2. Migrate, seed, run

```bash
python manage.py migrate
python manage.py seed_demo          # demo academy data (dev only); prints demo logins
python manage.py runserver 0.0.0.0:8000
```

API base URL: `http://localhost:8000/api/v1` · Swagger UI: `http://localhost:8000/api/v1/docs` · ReDoc: `/api/v1/redoc` · raw schema: `/api/v1/schema` · health: `/api/v1/health`.

Point the frontend at it: in the repo root `.env` set `VITE_API_BASE_URL=http://localhost:8000/api/v1` (the default in `.env.example`).

To create the first administrator without demo data:

```bash
python manage.py create_admin --email admin@example.com --name "Academy Admin" --password "ChangeMe123"
```

## Configuration

All settings come from environment variables (see `.env.example`). Key ones:

| Variable | Purpose |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | `config.settings.development` / `config.settings.production` / `config.settings.test` |
| `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY` | Long random strings; production refuses to start with the defaults |
| `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, `PUBLIC_SITE_URL` | Host and frontend origin configuration |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_TEST_NAME` | MariaDB connection |
| `JWT_ACCESS_MINUTES`, `JWT_REFRESH_DAYS` | Token lifetimes (refresh tokens rotate and are blacklisted) |
| `PAYMENT_GATEWAY`, `PAYMENT_WEBHOOK_SECRET` | `sandbox` enables the local gateway simulator; production requires a real provider name and secret |
| `LIVE_PROVIDER`, `SIGNED_URL_TTL_MINUTES` | Conferencing adapter and TTL of signed join/stream links |
| `OTP_TTL_MINUTES`, `PASSWORD_RESET_TTL_MINUTES`, `EMAIL_BACKEND` | Verification and reset flows |

## API conventions

- Prefix `/api/v1/`, **no trailing slashes** (matches the existing frontend). JSON is camelCase on the wire; models use snake_case internally.
- Auth: `Authorization: Bearer <access token>`. Login/verify return `{accessToken, refreshToken, expiresAt, user}`; `user.permissions` lists the effective permission strings.
- Errors: `{ "code": "ALREADY_ENROLLED", "message": "...", "details": { "field": ["..."] } }`. 401 = no/expired session, 403 = not permitted, 404 = not found (also used for other users' records to prevent id enumeration), 409 = business conflict, 429 = rate limited.
- Pagination: `?page=&pageSize=` → `{ items, total, page, pageSize }`.
- Full endpoint inventory with request/response types: `../docs/API_CONTRACTS.md` (contract) and the generated Swagger UI (implementation).

### Sample requests

Login:

```http
POST /api/v1/auth/login
Content-Type: application/json

{ "email": "priya@student.demo", "password": "Student@123" }
```

```json
{
  "accessToken": "eyJ...", "refreshToken": "eyJ...", "expiresAt": "2026-10-12T10:30:00+05:30",
  "user": { "id": "usr_...", "name": "Priya Sharma", "email": "priya@student.demo", "role": "student",
            "permissions": ["profile:read:self", "courses:read:enrolled", "..."], "avatarUrl": null,
            "emailVerified": true, "createdAt": "2026-07-09T09:00:00+05:30" }
}
```

Create an order and complete it in the sandbox gateway (development only):

```http
POST /api/v1/payments/orders
Authorization: Bearer <student token>
{ "courseId": "crs_...", "applyWalletCredit": true }
→ 201 { "orderId": "KBA-ORD-1104", "paymentId": "pay_...", "amount": 9499, "currency": "INR",
        "listPrice": 9999, "referralDiscount": 0, "walletCreditApplied": 500,
        "gateway": { "provider": "sandbox", "checkoutToken": "..." } }

POST /api/v1/payments/sandbox/complete
{ "checkoutToken": "...", "outcome": "success", "method": "UPI" }
→ 200 { "status": "paid" }
```

A real gateway instead calls `POST /api/v1/payments/webhook` with header `X-Kanak-Signature: <hex HMAC-SHA256 of the raw body using PAYMENT_WEBHOOK_SECRET>` and body `{ "eventId", "paymentId", "status": "paid"|"failed", "gatewayReference", "method", "failureReason"? }`. Deliveries are idempotent per `eventId`; access is granted inside the same transaction that marks the payment paid.

Forbidden example (student calling an admin endpoint):

```http
GET /api/v1/admin/payments  →  403 { "code": "FORBIDDEN", "message": "You do not have permission to perform this action." }
```

## Security notes

- Passwords: Django PBKDF2 (switch to Argon2 by installing `argon2-cffi`); Django password validators + letter/digit rule.
- Roles are assigned server-side; registration always creates a student. Permissions derive from the role (`common/permissions.py`) and are checked in every view (`HasPermission`, `IsStudent`, `IsAdmin`) and in querysets (ownership).
- Throttling on login, registration, OTP, password reset and public lookups (`common/throttles.py`); failed logins are recorded in `LoginAttempt`.
- Refresh tokens rotate and are blacklisted on logout/password change; access tokens are short-lived.
- Secrets never leave the server: provider meeting references, media storage keys, OTP/reset hashes, password hashes. Join and stream links are signed and expire.
- Webhook signatures are verified with constant-time comparison; events are stored once and replays acknowledged without side effects.
- Production settings enforce `DEBUG=False`, explicit hosts, HTTPS redirect, secure cookies, HSTS, `X-Frame-Options: DENY`, and refuse default secrets or the sandbox gateway.
- Every admin mutation writes an `AuditLog` row; nothing is hard-deleted through the API.

## Testing

```bash
# MariaDB (default): needs DB_* in .env and CREATE rights on DB_TEST_NAME
python -m pytest -q

# Quick local iteration without MariaDB (in-memory SQLite; not the supported runtime DB)
TEST_DB_SQLITE=1 python -m pytest -q          # PowerShell: $env:TEST_DB_SQLITE="1"; python -m pytest -q
```

Code quality: `python -m black apps common config tests` and `python -m ruff check apps common config tests`.

## Production deployment

1. Provision MariaDB; run `scripts/mariadb_setup.sql` with a strong password.
2. Set environment variables (see table) with `DJANGO_SETTINGS_MODULE=config.settings.production`, a real `PAYMENT_GATEWAY`, `PAYMENT_WEBHOOK_SECRET`, `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `PUBLIC_SITE_URL`.
3. `pip install -r requirements.txt` (optionally `mysqlclient`, `argon2-cffi`), `python manage.py migrate`, `python manage.py collectstatic --noinput`, `python manage.py create_admin ...`.
4. Run `gunicorn config.wsgi:application -c gunicorn.conf.py` behind a TLS-terminating reverse proxy (nginx/Caddy) that sets `X-Forwarded-Proto`.
5. Configure the payment provider's webhook to `https://<host>/api/v1/payments/webhook`, media/recording storage (CDN with signed URLs) and the conferencing provider adapter (`apps/batches/providers.py`).

## Status (2026-10-12)

### Implemented
- All ten apps with models, migrations (`0001_initial` each), serializers, services, permissions, views, URLs and OpenAPI annotations. `manage.py check` clean, `makemigrations --check` clean, `spectacular --validate` clean.
- Authentication: register (student role forced, optional referral code), OTP verification, login with throttling and `LoginAttempt` audit, logout (refresh blacklist), token refresh, me, forgot/reset password, change password, profile read/update, `create_admin` command.
- Courses: public catalogue (published only, materials stripped), course detail by id or slug, course sessions, trainers, academy stats; admin course CRUD, publish/unpublish (blocked on empty curriculum), curriculum replace preserving lesson ids, thumbnail upload, enrolled students, trainer management.
- Batches: admin batches list/detail/create/update, status transitions with cascading session cancellation, add/withdraw students (eligibility, capacity, duplicates), live session scheduling/cancel/attendance (completes live lessons and triggers completion evaluation), recordings; student live schedule with join windows and signed join links (provider adapter), recordings list and signed playback streams; public upcoming sessions.
- Enrollments and progress: student dashboard, enrollments, enrolled course detail, progress summary, lesson progress updates (live lessons excluded), server-side completion evaluation with automatic certificate issuance.
- Payments: order creation with referral discount and wallet credit, HMAC-signed idempotent webhook granting access in one transaction, sandbox gateway (dev only, `POST /payments/sandbox/complete`), student payment history, admin enrollments (grant/suspend/reactivate), admin payments (reconcile, refund with access revocation and reward reversal). No endpoint can mark a payment paid.
- Certificates: issuance with eligibility re-verification and row locks, revoke, reissue, eligible-student list, public verification with lookup log; student certificate endpoints.
- Refer & Earn: programme config, referral validation, student summary with wallet ledger, admin list/stats/config; pricing and reward hooks in the payment flow.
- Reports: admin overview with date range, analytics, student directory with filters, student detail.
- `seed_demo` command (idempotent, dev only) reproducing the frontend demo data and logins.
- Tests: 408 passed, 1 skipped (a row-lock test that needs MariaDB) with `TEST_DB_SQLITE=1`; Black and Ruff clean.

### Outstanding / to verify
- **Run the suite and migrations against MariaDB.** The local MariaDB root password was not available, so `scripts/mariadb_setup.sql` has not been applied and the MariaDB-backed test run (`python -m pytest -q` without `TEST_DB_SQLITE`) is pending. Expected to pass; the skipped row-lock test will then run.
- Real payment gateway and conferencing provider adapters are stubs (`apps/payments/gateway.py`, `apps/batches/providers.py`); wire the chosen vendors.
- Media: recordings and materials are served from `MEDIA_ROOT` through signed URLs; production should redirect to a CDN with signed URLs.
- The frontend stores only the access token and does not yet call `auth/token/refresh`; with the default 30-minute access lifetime users are asked to sign in again after expiry. Adding silent refresh to the frontend Axios client is a small follow-up.
- Optional endpoints from the contract not yet built: contact form, invoice PDF, official certificate PDF generation.

### Assumptions
- URL paths have no trailing slash and JSON is camelCase, to stay compatible with the existing frontend contract rather than the slash-terminated examples in the brief.
- Prefixed string primary keys (`usr_…`, `crs_…`) are used for every business table to match frontend id types and prevent id enumeration.
- Certificate numbers follow `KBA-<year>-<sequence>` with the sequence starting after a reserved block of 200, matching the demo data.
- PyMySQL is used as the MariaDB driver because no compiled client was available for Python 3.14 on this machine; `mysqlclient` can replace it without code changes.
