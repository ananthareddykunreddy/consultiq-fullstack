# ConsultIQ Full-Stack (Launch-Ready)

## Completed hardening and feature bundle
- Bcrypt password hashing (`passlib`)
- CSRF protection across POST forms (auto-injected hidden token)
- Per-IP rate limiting on auth/contact/booking/upload
- Upload validation (type, size, executable signature block)
- 2FA for admin using TOTP (`pyotp`)
- Password reset request + reset flow (token + expiry)
- Audit logs for critical actions
- Notification queue + email dispatch hook
- KPI dashboards + chart widgets + Sheets-style tables
- 404/500 pages + request logging middleware
- Health endpoint (`/health`) for monitoring

## Operations & deployment assets
- Nginx reverse-proxy template: `deploy/nginx-consultiq.conf`
- systemd service template: `deploy/consultiq.service`
- DB backup script: `scripts/backup-db.ps1`
- Log monitor script: `ops/monitor-log.ps1`
- PostgreSQL migration bootstrap schema: `migrations/postgres_schema.sql`

## Environment variables
Copy `.env.example` and set strong values:
- `CONSULTIQ_SESSION_SECRET`
- `CONSULTIQ_ADMIN_PASSWORD`
- `CONSULTIQ_DB_PATH`
- SMTP variables for real email notifications

## Test and validation
- Run: `python -m py_compile app/main.py`
- Run: `python -m pytest -q`

## Main routes
- `/`, `/services`, `/services/{slug}`
- `/news`, `/required-documents`
- `/contact`, `/client-area`, `/admin`
- `/admin/2fa/setup`, `/admin-2fa-verify`
- `/password-reset-request`, `/password-reset/{token}`
- `/privacy`, `/cookie-policy`, `/gdpr`, `/legal`
- `/health`
