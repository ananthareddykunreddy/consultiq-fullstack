from __future__ import annotations

import logging
import os
import shutil
import smtplib
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pyotp
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.getenv("CONSULTIQ_DB_PATH", str(DATA_DIR / "consultiq.db")))
MAX_UPLOAD_BYTES = int(os.getenv("CONSULTIQ_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
SESSION_SECRET = os.getenv("CONSULTIQ_SESSION_SECRET", "change_me_in_production")
DEFAULT_ADMIN_EMAIL = os.getenv("CONSULTIQ_ADMIN_EMAIL", "admin@consultiq.it")
DEFAULT_ADMIN_PASSWORD = os.getenv("CONSULTIQ_ADMIN_PASSWORD", "ChangeThisAdminPassword123!")
SMTP_HOST = os.getenv("CONSULTIQ_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("CONSULTIQ_SMTP_PORT", "587"))
SMTP_USER = os.getenv("CONSULTIQ_SMTP_USER", "")
SMTP_PASS = os.getenv("CONSULTIQ_SMTP_PASS", "")
SMTP_FROM = os.getenv("CONSULTIQ_SMTP_FROM", "noreply@consultiq.local")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
RATE_LIMIT_STORE: dict[str, list[float]] = {}

SERVICES = [
    {"slug": "caf-isee", "category": "CAF", "title": "ISEE Declaration", "summary": "Family economic profile support for bonus and welfare access.", "documents": ["ID/Passport", "Codice Fiscale", "Income Records", "Residence Certificate"]},
    {"slug": "caf-730", "category": "CAF", "title": "730 Tax Filing", "summary": "Annual personal tax declaration with full checklist and validation.", "documents": ["CU/Income Statement", "Medical expenses", "Rent/Mortgage docs", "ID + Codice Fiscale"]},
    {"slug": "caf-f24", "category": "CAF", "title": "F24 Payment Forms", "summary": "Tax/payment form preparation and submission guidance.", "documents": ["Payment reference", "Tax code details", "ID document"]},
    {"slug": "patronato-pensione", "category": "Patronato", "title": "Pensione Support", "summary": "Pension practice support, planning and filing assistance.", "documents": ["ID", "Contribution history", "Employment records"]},
    {"slug": "patronato-disoccupazione", "category": "Patronato", "title": "Disoccupazione / NASPI", "summary": "Unemployment benefits request and status handling.", "documents": ["Termination letter", "Employment contract", "IBAN", "ID"]},
    {"slug": "immigration-permesso", "category": "Immigration", "title": "Permesso di Soggiorno", "summary": "Permit issuance/renewal with full procedural support.", "documents": ["Passport", "Current permit", "Accommodation proof", "Income proof"]},
    {"slug": "immigration-cittadinanza", "category": "Immigration", "title": "Citizenship Application", "summary": "Eligibility review and complete citizenship dossier preparation.", "documents": ["Birth certificate", "Residence records", "Language cert", "Criminal clearance"]},
    {"slug": "business-partita-iva", "category": "Business", "title": "Partita IVA Opening", "summary": "Entity setup, tax regime guidance and registration workflow.", "documents": ["ID", "Business activity details", "Residence proof"]},
    {"slug": "business-fattura-elettronica", "category": "Business", "title": "Fattura Elettronica", "summary": "E-invoicing setup and compliance operations for businesses.", "documents": ["VAT profile", "SDI/PEC details", "Company registration info"]},
    {"slug": "support-traduzioni-legalizzazione", "category": "Support", "title": "Translations & Legalization", "summary": "Certified translation and legalization management for official use.", "documents": ["Original documents", "Destination authority info", "ID"]},
]
SERVICE_BY_SLUG = {s["slug"]: s for s in SERVICES}
SERVICE_TITLES = [f"{s['category']} - {s['title']}" for s in SERVICES]
NEWS_ITEMS = [
    {"date": "2026-05-10", "title": "730 Campaign Open", "summary": "Priority slots opened for early filing and document validation."},
    {"date": "2026-05-22", "title": "Permit Renewal Fast Track", "summary": "New workflow reduces missing-document rework for renewals."},
    {"date": "2026-05-28", "title": "Business Startup Advisory", "summary": "Expanded support for Partita IVA and compliance onboarding."},
]

app = FastAPI(title="ConsultIQ")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG_DIR / "app.log"), level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("consultiq")


def hash_password(raw: str) -> str:
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return pwd_context.verify(raw, hashed)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request, key: str, limit: int, window_seconds: int) -> None:
    now = time.time()
    bucket_key = f"{key}:{get_client_ip(request)}"
    entries = [t for t in RATE_LIMIT_STORE.get(bucket_key, []) if now - t <= window_seconds]
    if len(entries) >= limit:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
    entries.append(now)
    RATE_LIMIT_STORE[bucket_key] = entries


def get_or_create_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = uuid.uuid4().hex
        request.session["csrf_token"] = token
    return token


def enforce_csrf(request: Request, csrf_token: str) -> None:
    expected = request.session.get("csrf_token")
    if not expected or csrf_token != expected:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def validate_upload(document: UploadFile | None) -> None:
    if not document or not document.filename:
        return
    ext = Path(document.filename).suffix.lower()
    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx"}
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    pos = document.file.tell()
    document.file.seek(0, 2)
    size = document.file.tell()
    document.file.seek(pos)
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large")
    head = document.file.read(16)
    document.file.seek(0)
    if head.startswith(b"MZ") or head.startswith(b"\x7fELF"):
        raise HTTPException(status_code=400, detail="Executable files are not allowed")


def get_current_user(request: Request) -> dict[str, Any] | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, full_name, email, phone, role, admin_totp_secret FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def require_login(request: Request) -> dict[str, Any] | None:
    return get_current_user(request)


def require_admin(request: Request) -> dict[str, Any] | None:
    user = get_current_user(request)
    if user and user.get("role") == "admin":
        return user
    return None


def send_email_notification(to_email: str, subject: str, body: str) -> None:
    if not SMTP_HOST:
        logger.info("EMAIL_NOT_SENT host_missing to=%s subject=%s", to_email, subject)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        smtp.starttls()
        if SMTP_USER:
            smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)


def add_audit_log(actor_user_id: int | None, action: str, target_type: str, target_id: str, details: str = "") -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO audit_logs (actor_user_id, action, target_type, target_id, details) VALUES (?, ?, ?, ?, ?)", (actor_user_id, action, target_type, target_id, details))


def add_notification(user_id: int | None, email: str, channel: str, title: str, message: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO notifications (user_id, email, channel, title, message, status) VALUES (?, ?, ?, ?, ?, 'queued')", (user_id, email, channel, title, message))
    try:
        if channel == "email":
            send_email_notification(email, title, message)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE notifications SET status='sent' WHERE email=? AND title=? AND message=?", (email, title, message))
    except Exception:
        logger.exception("notification_send_failed")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'client', admin_totp_secret TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS appointments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL, service_type TEXT NOT NULL, preferred_date TEXT NOT NULL, message TEXT, status TEXT NOT NULL DEFAULT 'new', created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id))")
        conn.execute("CREATE TABLE IF NOT EXISTS contact_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT, subject TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS appointment_documents (id INTEGER PRIMARY KEY AUTOINCREMENT, appointment_id INTEGER NOT NULL, uploaded_by_user_id INTEGER, original_filename TEXT NOT NULL, stored_filename TEXT NOT NULL, file_path TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(appointment_id) REFERENCES appointments(id), FOREIGN KEY(uploaded_by_user_id) REFERENCES users(id))")
        conn.execute("CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER, action TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, email TEXT NOT NULL, channel TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS password_resets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, token TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        admin_exists = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        if not admin_exists:
            conn.execute("INSERT INTO users (full_name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, 'admin')", ("ConsultIQ Admin", DEFAULT_ADMIN_EMAIL, "+39 02 0000 0000", hash_password(DEFAULT_ADMIN_PASSWORD)))


@app.on_event("startup")
def startup_event() -> None:
    init_db()


def render(request: Request, template: str, **context: Any):
    return templates.TemplateResponse(template, {"request": request, "user": get_current_user(request), "services": SERVICES, "service_titles": SERVICE_TITLES, "csrf_token": get_or_create_csrf_token(request), **context})


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    logger.info("%s %s %s %sms", request.method, request.url.path, response.status_code, int((time.time() - start) * 1000))
    return response


@app.exception_handler(404)
async def not_found_handler(request: Request, _exc: Exception):
    return templates.TemplateResponse("404.html", {"request": request, "title": "Not Found", "csrf_token": get_or_create_csrf_token(request), "user": get_current_user(request)}, status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, _exc: Exception):
    return templates.TemplateResponse("500.html", {"request": request, "title": "Server Error", "csrf_token": get_or_create_csrf_token(request), "user": get_current_user(request)}, status_code=500)


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "utc": datetime.now(timezone.utc).isoformat()})


@app.get("/")
def home(request: Request):
    return render(request, "index.html", page="home", title="ConsultIQ | Strategic Advisory", news=NEWS_ITEMS[:2])


@app.get("/services")
def services_page(request: Request):
    return render(request, "services.html", page="services", title="ConsultIQ Services", services=SERVICES)


@app.get("/services/{slug}")
def service_detail(request: Request, slug: str, booked: int = 0):
    service = SERVICE_BY_SLUG.get(slug)
    if not service:
        return RedirectResponse(url="/services", status_code=303)
    related = [s for s in SERVICES if s["category"] == service["category"] and s["slug"] != slug][:3]
    return render(request, "service_detail.html", page="services", title=f"{service['title']} | ConsultIQ", service=service, related=related, booked=booked)


@app.post("/services/{slug}/book")
def book_service_detail(request: Request, slug: str, csrf_token: str = Form(...), full_name: str = Form(...), email: str = Form(...), phone: str = Form(...), preferred_date: str = Form(...), message: str = Form(""), document: UploadFile | None = File(default=None)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "book_service_detail", 20, 300)
    service = SERVICE_BY_SLUG.get(slug)
    if not service:
        return RedirectResponse(url="/services", status_code=303)
    validate_upload(document)
    user = get_current_user(request)
    user_id = user["id"] if user else None
    service_type = f"{service['category']} - {service['title']}"
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("INSERT INTO appointments (user_id, full_name, email, phone, service_type, preferred_date, message) VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, full_name.strip(), email.strip(), phone.strip(), service_type, preferred_date.strip(), message.strip()))
        appointment_id = cur.lastrowid
        if document and document.filename:
            safe_name = f"{uuid.uuid4().hex}_{Path(document.filename).name}"
            save_path = UPLOAD_DIR / safe_name
            with save_path.open("wb") as buffer:
                shutil.copyfileobj(document.file, buffer)
            conn.execute("INSERT INTO appointment_documents (appointment_id, uploaded_by_user_id, original_filename, stored_filename, file_path) VALUES (?, ?, ?, ?, ?)", (appointment_id, user_id, document.filename, safe_name, f"/uploads/{safe_name}"))
    add_audit_log(user_id, "service_booked", "appointment", str(appointment_id), service_type)
    add_notification(user_id, email.strip(), "email", "Booking Received", f"Your booking for {service_type} was received.")
    return RedirectResponse(url=f"/services/{slug}?booked=1", status_code=303)


@app.get("/news")
def news_page(request: Request):
    return render(request, "news.html", page="news", title="News | ConsultIQ", news=NEWS_ITEMS)


@app.get("/required-documents")
def required_documents(request: Request):
    return render(request, "required_documents.html", page="documents", title="Required Documents | ConsultIQ", services=SERVICES)


@app.get("/privacy")
def privacy_page(request: Request):
    return render(request, "policy.html", page="legal", title="Privacy Policy | ConsultIQ", heading="Privacy Policy")


@app.get("/cookie-policy")
def cookie_page(request: Request):
    return render(request, "policy.html", page="legal", title="Cookie Policy | ConsultIQ", heading="Cookie Policy")


@app.get("/gdpr")
def gdpr_page(request: Request):
    return render(request, "policy.html", page="legal", title="GDPR Notice | ConsultIQ", heading="GDPR Notice")


@app.get("/legal")
def legal_page(request: Request):
    return render(request, "policy.html", page="legal", title="Legal Notice | ConsultIQ", heading="Legal Notice")


@app.get("/client-area")
def client_area(request: Request):
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        appointments = conn.execute("SELECT id, service_type, preferred_date, status, created_at FROM appointments WHERE user_id=? OR email=? ORDER BY id DESC", (user["id"], user["email"])).fetchall()
        docs = conn.execute("SELECT d.id, d.appointment_id, d.original_filename, d.file_path, d.created_at FROM appointment_documents d JOIN appointments a ON a.id=d.appointment_id WHERE a.user_id=? OR a.email=? ORDER BY d.id DESC", (user["id"], user["email"])).fetchall()
        notifications = conn.execute("SELECT id, title, message, status, created_at FROM notifications WHERE user_id=? OR email=? ORDER BY id DESC LIMIT 30", (user["id"], user["email"])).fetchall()
    kpis = {"total_appointments": len(appointments), "completed_count": sum(1 for a in appointments if a["status"] == "completed"), "pending_count": sum(1 for a in appointments if a["status"] in ("new", "in_progress")), "upload_count": len(docs)}
    return render(request, "client_area.html", page="client", title="Client Area | ConsultIQ", appointments=appointments, documents=docs, notifications=notifications, client_kpis=kpis)


@app.post("/client-area/appointments/{appointment_id}/upload")
def upload_appointment_document(request: Request, appointment_id: int, csrf_token: str = Form(...), document: UploadFile = File(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "upload_appointment_document", 25, 300)
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    validate_upload(document)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        appt = conn.execute("SELECT id, user_id, email FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if not appt:
            return RedirectResponse(url="/client-area", status_code=303)
        owns = appt["user_id"] == user["id"] or appt["email"] == user["email"] or user["role"] == "admin"
        if not owns:
            return RedirectResponse(url="/client-area", status_code=303)
        safe_name = f"{uuid.uuid4().hex}_{Path(document.filename or 'upload.bin').name}"
        save_path = UPLOAD_DIR / safe_name
        with save_path.open("wb") as buffer:
            shutil.copyfileobj(document.file, buffer)
        conn.execute("INSERT INTO appointment_documents (appointment_id, uploaded_by_user_id, original_filename, stored_filename, file_path) VALUES (?, ?, ?, ?, ?)", (appointment_id, user["id"], document.filename or "document", safe_name, f"/uploads/{safe_name}"))
    add_audit_log(user["id"], "document_uploaded", "appointment", str(appointment_id), document.filename or "document")
    return RedirectResponse(url="/client-area", status_code=303)


@app.get("/admin")
def admin(request: Request):
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        appointments = conn.execute("SELECT * FROM appointments ORDER BY id DESC LIMIT 200").fetchall()
        contacts = conn.execute("SELECT * FROM contact_messages ORDER BY id DESC LIMIT 200").fetchall()
        users = conn.execute("SELECT id, full_name, email, role, created_at FROM users ORDER BY id DESC").fetchall()
        documents = conn.execute("SELECT d.id, d.appointment_id, d.original_filename, d.file_path, d.created_at, u.full_name AS uploader FROM appointment_documents d LEFT JOIN users u ON u.id=d.uploaded_by_user_id ORDER BY d.id DESC LIMIT 200").fetchall()
        notifications = conn.execute("SELECT id, email, channel, title, status, created_at FROM notifications ORDER BY id DESC LIMIT 100").fetchall()
        audit_logs = conn.execute("SELECT a.id, a.action, a.target_type, a.target_id, a.details, a.created_at, u.full_name AS actor FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_user_id ORDER BY a.id DESC LIMIT 200").fetchall()
    status_counts = {"new": 0, "in_progress": 0, "completed": 0, "cancelled": 0}
    for a in appointments:
        if a["status"] in status_counts:
            status_counts[a["status"]] += 1
    kpis = {"appointments_total": len(appointments), "contacts_total": len(contacts), "users_total": len(users), "documents_total": len(documents), "new_total": status_counts["new"], "in_progress_total": status_counts["in_progress"], "completed_total": status_counts["completed"], "cancelled_total": status_counts["cancelled"]}
    return render(request, "admin_dashboard.html", page="admin", title="Admin Dashboard | ConsultIQ", appointments=appointments, contacts=contacts, users=users, documents=documents, notifications=notifications, audit_logs=audit_logs, admin_kpis=kpis)


@app.post("/admin/appointments/{appointment_id}/status")
def update_appointment_status(appointment_id: int, request: Request, csrf_token: str = Form(...), status: str = Form(...)):
    enforce_csrf(request, csrf_token)
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE appointments SET status=? WHERE id=?", (status, appointment_id))
        row = conn.execute("SELECT user_id, email, service_type FROM appointments WHERE id=?", (appointment_id,)).fetchone()
    add_audit_log(user["id"], "appointment_status_updated", "appointment", str(appointment_id), status)
    if row:
        add_notification(row["user_id"], row["email"], "email", "Appointment Status Updated", f"Your {row['service_type']} request is now '{status}'.")
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/2fa/setup")
def admin_2fa_setup(request: Request):
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    secret = pyotp.random_base32()
    request.session["pending_2fa_secret"] = secret
    provisioning_uri = pyotp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name="ConsultIQ")
    return render(request, "admin_2fa_setup.html", page="admin", title="Admin 2FA Setup", secret=secret, provisioning_uri=provisioning_uri)


@app.post("/admin/2fa/setup")
def admin_2fa_setup_confirm(request: Request, csrf_token: str = Form(...), code: str = Form(...)):
    enforce_csrf(request, csrf_token)
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    secret = request.session.get("pending_2fa_secret")
    if not secret or not pyotp.TOTP(secret).verify(code.strip(), valid_window=1):
        return RedirectResponse(url="/admin/2fa/setup", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET admin_totp_secret=? WHERE id=?", (secret, user["id"]))
    request.session.pop("pending_2fa_secret", None)
    add_audit_log(user["id"], "admin_2fa_enabled", "user", str(user["id"]), "enabled")
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin-2fa-verify")
def admin_2fa_verify_page(request: Request, error: str = ""):
    if not request.session.get("pending_admin_user_id"):
        return RedirectResponse(url="/admin-login", status_code=303)
    return render(request, "admin_2fa_verify.html", page="auth", title="Admin 2FA Verify", error=error)


@app.post("/admin-2fa-verify")
def admin_2fa_verify_submit(request: Request, csrf_token: str = Form(...), code: str = Form(...)):
    enforce_csrf(request, csrf_token)
    pending_id = request.session.get("pending_admin_user_id")
    if not pending_id:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, admin_totp_secret FROM users WHERE id=?", (pending_id,)).fetchone()
    if not row or not row["admin_totp_secret"] or not pyotp.TOTP(row["admin_totp_secret"]).verify(code.strip(), valid_window=1):
        return RedirectResponse(url="/admin-2fa-verify?error=Invalid+2FA+code", status_code=303)
    request.session["user_id"] = row["id"]
    request.session.pop("pending_admin_user_id", None)
    add_audit_log(row["id"], "admin_2fa_verified", "user", str(row["id"]), "success")
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/contact")
def contact(request: Request, submitted: int = 0, service: str = ""):
    return render(request, "contact.html", page="contact", title="Contact ConsultIQ", submitted=submitted, selected_service=service)


@app.post("/contact")
def contact_submit(request: Request, csrf_token: str = Form(...), full_name: str = Form(...), email: str = Form(...), phone: str = Form(""), subject: str = Form(...), message: str = Form(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "contact_submit", 10, 300)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO contact_messages (full_name, email, phone, subject, message) VALUES (?, ?, ?, ?, ?)", (full_name.strip(), email.strip(), phone.strip(), subject.strip(), message.strip()))
    add_notification(None, email.strip(), "email", "Contact Request Received", "Thanks, we received your contact request.")
    return RedirectResponse(url="/contact?submitted=1", status_code=303)


@app.post("/appointments")
def create_appointment(request: Request, csrf_token: str = Form(...), full_name: str = Form(...), email: str = Form(...), phone: str = Form(...), service_type: str = Form(...), preferred_date: str = Form(...), message: str = Form("")):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "create_appointment", 20, 300)
    user = get_current_user(request)
    user_id = user["id"] if user else None
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("INSERT INTO appointments (user_id, full_name, email, phone, service_type, preferred_date, message) VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, full_name.strip(), email.strip(), phone.strip(), service_type.strip(), preferred_date.strip(), message.strip()))
        appointment_id = cur.lastrowid
    add_audit_log(user_id, "appointment_created", "appointment", str(appointment_id), service_type)
    add_notification(user_id, email.strip(), "email", "Appointment Request Received", f"Your request for {service_type} was submitted.")
    return RedirectResponse(url="/client-area" if user else "/contact?submitted=1", status_code=303)


@app.get("/register")
def register_page(request: Request, error: str = ""):
    return render(request, "register.html", page="auth", title="Register | ConsultIQ", error=error)


@app.post("/register")
def register_submit(request: Request, csrf_token: str = Form(...), full_name: str = Form(...), email: str = Form(...), phone: str = Form(""), password: str = Form(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "register_submit", 6, 300)
    if len(password.strip()) < 10:
        return RedirectResponse(url="/register?error=Password+must+be+at+least+10+characters", status_code=303)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO users (full_name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, 'client')", (full_name.strip(), email.strip().lower(), phone.strip(), hash_password(password.strip())))
    except sqlite3.IntegrityError:
        return RedirectResponse(url="/register?error=Email+already+registered", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/login")
def login_page(request: Request, error: str = ""):
    return render(request, "login.html", page="auth", title="Login | ConsultIQ", error=error)


@app.post("/login")
def login_submit(request: Request, csrf_token: str = Form(...), email: str = Form(...), password: str = Form(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "login_submit", 8, 300)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, role, password_hash FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not row or not verify_password(password.strip(), row["password_hash"]):
        return RedirectResponse(url="/login?error=Invalid+credentials", status_code=303)
    request.session["user_id"] = row["id"]
    add_audit_log(row["id"], "login", "user", str(row["id"]), row["role"])
    return RedirectResponse(url="/admin" if row["role"] == "admin" else "/client-area", status_code=303)


@app.get("/admin-login")
def admin_login_page(request: Request, error: str = ""):
    return render(request, "admin_login.html", page="auth", title="Admin Login | ConsultIQ", error=error)


@app.post("/admin-login")
def admin_login_submit(request: Request, csrf_token: str = Form(...), email: str = Form(...), password: str = Form(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "admin_login_submit", 8, 300)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, role, password_hash, admin_totp_secret FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not row or row["role"] != "admin" or not verify_password(password.strip(), row["password_hash"]):
        return RedirectResponse(url="/admin-login?error=Invalid+admin+credentials", status_code=303)
    if row["admin_totp_secret"]:
        request.session["pending_admin_user_id"] = row["id"]
        return RedirectResponse(url="/admin-2fa-verify", status_code=303)
    request.session["user_id"] = row["id"]
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/password-reset-request")
def password_reset_request_page(request: Request, status: str = ""):
    return render(request, "password_reset_request.html", page="auth", title="Password Reset Request", status=status)


@app.post("/password-reset-request")
def password_reset_request_submit(request: Request, csrf_token: str = Form(...), email: str = Form(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "password_reset_request_submit", 5, 300)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT id FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        if user:
            token = uuid.uuid4().hex
            expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            conn.execute("INSERT INTO password_resets (user_id, token, expires_at, used) VALUES (?, ?, ?, 0)", (user["id"], token, expires))
            link = f"{request.url.scheme}://{request.url.netloc}/password-reset/{token}"
            send_email_notification(email.strip(), "ConsultIQ Password Reset", f"Use this link to reset your password: {link}")
            add_audit_log(user["id"], "password_reset_requested", "user", str(user["id"]), "email_sent")
    return RedirectResponse(url="/password-reset-request?status=If+the+email+exists,+a+reset+link+was+sent", status_code=303)


@app.get("/password-reset/{token}")
def password_reset_page(request: Request, token: str, error: str = ""):
    return render(request, "password_reset.html", page="auth", title="Reset Password", token=token, error=error)


@app.post("/password-reset/{token}")
def password_reset_submit(request: Request, token: str, csrf_token: str = Form(...), password: str = Form(...)):
    enforce_csrf(request, csrf_token)
    if len(password.strip()) < 10:
        return RedirectResponse(url=f"/password-reset/{token}?error=Password+must+be+at+least+10+characters", status_code=303)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, user_id, expires_at, used FROM password_resets WHERE token=?", (token,)).fetchone()
        if not row or row["used"]:
            return RedirectResponse(url=f"/password-reset/{token}?error=Invalid+or+used+token", status_code=303)
        if datetime.fromisoformat(row["expires_at"]) < now:
            return RedirectResponse(url=f"/password-reset/{token}?error=Expired+token", status_code=303)
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password.strip()), row["user_id"]))
        conn.execute("UPDATE password_resets SET used=1 WHERE id=?", (row["id"],))
    add_audit_log(row["user_id"], "password_reset_completed", "user", str(row["user_id"]), "success")
    return RedirectResponse(url="/login?error=Password+reset+successful.+Please+login", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)
