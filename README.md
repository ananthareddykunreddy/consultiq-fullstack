# ConsultIQ Full-Stack (cPanel + GitHub Ready)

## cPanel BASIC deploy (recommended for your hosting)

### 1) Clone repository in cPanel
- cPanel -> `Git Version Control`
- Clone: `https://github.com/ananthareddykunreddy/consultiq-fullstack.git`
- Target dir: `~/consultiq-fullstack`

### 2) Create Python App in cPanel
- cPanel -> `Applications` / `Setup Python App`
- Python version: highest available (3.10+)
- Application root: `consultiq-fullstack`
- Application URL: `consultiq.it` (or test path)
- Startup file: `passenger_wsgi.py`
- Entry point: `application`

### 3) Install dependencies
Use cPanel terminal (inside Python app virtualenv):
```bash
cd ~/consultiq-fullstack
pip install -r requirements.txt
```

### 4) Set environment variables in Python App UI
Set at minimum:
- `CONSULTIQ_SESSION_SECRET`
- `CONSULTIQ_ADMIN_EMAIL`
- `CONSULTIQ_ADMIN_PASSWORD`
- `CONSULTIQ_MAX_UPLOAD_BYTES`
- Optional SMTP vars for email notifications

### 5) Ensure writable data directories
```bash
mkdir -p ~/consultiq-fullstack/app/data/uploads
chmod -R 755 ~/consultiq-fullstack/app/data
```

### 6) Restart app
- In cPanel Python App panel click `Restart`

### 7) Verify
- `https://consultiq.it/health`

## Files added for cPanel
- `passenger_wsgi.py` (WSGI entrypoint)
- `.cpanel.yml` (optional cPanel deploy tasks)

## Existing GitHub Action
- `.github/workflows/deploy.yml` is currently VPS/SSH style.
- For cPanel shared hosting, use cPanel Git Pull + Python App Restart.

## Security/features included
- Bcrypt auth
- CSRF + rate limiting
- Admin 2FA (TOTP)
- Password reset flow
- Audit logs + notifications
- KPI dashboards + sheets-style tables
- Upload validation
- Health endpoint
