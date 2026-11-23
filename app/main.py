from __future__ import annotations

import logging
import os
import csv
import io
import json
import shutil
import smtplib
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pyotp
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
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
SMTP_USER = os.getenv("SMTP_USER", os.getenv("CONSULTIQ_SMTP_USER", ""))
SMTP_PASS = os.getenv("SMTP_PASSWORD", os.getenv("CONSULTIQ_SMTP_PASS", ""))
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", os.getenv("CONSULTIQ_SMTP_FROM", "noreply@consultiq.local"))
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Consultiq Italy")
ADMIN_NOTIFICATION_EMAIL = os.getenv("ADMIN_NOTIFICATION_EMAIL", DEFAULT_ADMIN_EMAIL)
CLAMAV_ENABLED = os.getenv("CLAMAV_ENABLED", "false").lower() == "true"
CLAMAV_FAIL_MODE = os.getenv("CLAMAV_FAIL_MODE", "mark_scan_pending")
STRIPE_ENABLED = os.getenv("STRIPE_ENABLED", "false").lower() == "true"
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLIC_KEY = os.getenv("STRIPE_PUBLIC_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
WHATSAPP_BUSINESS_NUMBER = os.getenv("WHATSAPP_BUSINESS_NUMBER", "393444881606")
COMMON_PASSWORDS = {"password", "password123", "12345678", "qwerty123", "admin123", "consultiq123"}
DOCUMENT_CATEGORIES = [
    "Documento identita",
    "Codice fiscale",
    "Permesso di soggiorno",
    "ISEE",
    "730",
    "DSU",
    "Contratto",
    "Busta paga",
    "Altro",
]
DOCUMENT_STATUSES = ["received", "under_review", "approved", "missing_info", "rejected", "scan_pending"]
REQUEST_STATUSES = ["new", "contacted", "documents_requested", "documents_received", "in_progress", "waiting_client", "completed", "cancelled"]
REQUEST_PRIORITIES = ["bassa", "media", "alta", "urgente"]
PAYMENT_STATUSES = ["not_requested", "requested", "pending_confirmation", "paid", "not_required", "bank_transfer_requested"]
LEGAL_REVIEW_WARNING = "Da verificare da un consulente legale prima della pubblicazione definitiva."
EMAIL_TEMPLATES = {
    "client_booking_confirmation": {
        "subject": "Richiesta ricevuta da ConsultIQ",
        "body": "Abbiamo ricevuto la tua richiesta per {service}. Ti contatteremo per confermare documenti, tempi e modalita' di pagamento, se necessarie.",
    },
    "admin_new_booking": {
        "subject": "Nuova richiesta servizio dal sito",
        "body": "E' stata registrata una nuova richiesta da {client_name} per {service}. Verifica la pratica nell'area amministrativa e assegna un operatore.",
    },
    "appointment_status_changed": {
        "subject": "Aggiornamento stato richiesta ConsultIQ",
        "body": "La tua richiesta relativa a {service} e' stata aggiornata. Nuovo stato: {status}. Se necessario, ti invieremo ulteriori istruzioni o documenti da integrare.",
    },
    "missing_documents_requested": {
        "subject": "Documentazione integrativa richiesta",
        "body": "Per proseguire con la pratica {service} abbiamo bisogno della seguente documentazione: {missing_documents}. Puoi caricarla dall'area cliente o consegnarla in sede secondo le istruzioni ricevute.",
    },
    "document_received_confirmation": {
        "subject": "Documento ricevuto correttamente",
        "body": "Abbiamo ricevuto il documento {document_name} collegato alla pratica #{appointment_id}. Il team lo verifichera' prima di eventuali ulteriori richieste.",
    },
    "contact_message_received": {
        "subject": "Messaggio ricevuto da ConsultIQ",
        "body": "Grazie, abbiamo ricevuto il tuo messaggio. Il team ConsultIQ ti rispondera' appena possibile con indicazioni operative o proposta di appuntamento.",
    },
    "appointment_confirmed": {
        "subject": "Richiesta presa in carico",
        "body": "La tua richiesta per {service} e' stata presa in carico. Ti contatteremo per confermare la documentazione necessaria e gli eventuali prossimi passaggi.",
    },
    "appointment_cancelled": {
        "subject": "Richiesta annullata",
        "body": "La richiesta per {service} e' stata annullata o sospesa. Contattaci se desideri inviare una nuova richiesta o ricevere chiarimenti.",
    },
    "payment_status_updated": {
        "subject": "Aggiornamento stato pagamento richiesta",
        "body": "Lo stato amministrativo del pagamento per {service} e' stato aggiornato a: {payment_status}. Se necessario, ti contatteremo con istruzioni offline.",
    },
    "admin_notification_email": {
        "subject": "Notifica operativa ConsultIQ",
        "body": "E' stato registrato un evento operativo: {event_label}. Controlla dashboard, calendario o documenti per i dettagli.",
    },
    "request_assignment_notification": {
        "subject": "Nuova richiesta assegnata",
        "body": "Ti e' stata assegnata la richiesta #{request_id} per {service}. Accedi alla dashboard operativa per gestirla.",
    },
}

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
RATE_LIMIT_STORE: dict[str, list[float]] = {}
DASHBOARD_SECTION_LIMIT = 10
DASHBOARD_DOCUMENT_LIMIT = 20
DASHBOARD_AUDIT_LIMIT = 50

COMMON_DOCUMENTS = ["Documento di identita' o passaporto", "Codice fiscale", "Recapiti aggiornati", "Documentazione gia' disponibile sulla pratica"]
CONSULTIQ_ADDRESS = "Via Licinio Murena 32-34, Roma - Metro A Numidio Quadrato"
CONSULTIQ_PHONE = "0697616812"
CONSULTIQ_EMAIL = "info@consultiq.it"
DOCUMENT_REQUIREMENT_STATUS_LABELS = {"missing": "Mancante", "requested": "Richiesto", "received": "Ricevuto", "not_required": "Non necessario"}
SLA_STATUS_LABELS = {"ok": "Nei tempi", "warning": "Da seguire", "overdue": "In ritardo"}
SERVICE_DOCUMENT_REQUIREMENTS = {
    "caf-730": ["Documento di identita'", "Codice fiscale", "CU / Certificazione Unica", "Spese mediche se disponibili", "Contratti di affitto / interessi mutuo se presenti"],
    "caf-isee": ["Documento di identita'", "Codice fiscale", "Stato di famiglia / nucleo familiare", "Giacenze medie e saldi", "Redditi", "Patrimonio immobiliare"],
    "immigration-residence-permit": ["Documento di identita'", "Passaporto", "Permesso precedente se presente", "Codice fiscale", "Documentazione reddito/alloggio se richiesta"],
    "business-partita-iva": ["Documento di identita'", "Codice fiscale", "Descrizione attivita'", "Indirizzo sede", "Recapiti"],
}
SERVICE_WORKFLOW_TEMPLATES = {
    "caf-730": ["Verificare documento identita'", "Verificare codice fiscale", "Controllare CU", "Controllare detrazioni/deduzioni", "Preparare pratica", "Confermare al cliente"],
    "business-partita-iva": ["Verificare dati anagrafici", "Verificare attivita' prevista", "Controllare codice ATECO", "Preparare riepilogo per cliente", "Richiedere conferma pagamento/manuale", "Segnare pratica completata"],
    "immigration-residence-permit": ["Verificare documento identita'", "Verificare passaporto", "Controllare permesso/documenti", "Richiedere integrazioni", "Preparare pratica", "Aggiornare cliente"],
}
SERVICE_CATEGORY_LABELS = {
    "CAF": "CAF",
    "Patronato": "Patronato",
    "Immigration": "Immigrazione e visti",
    "Admission": "Studio e ammissioni",
    "Embassy": "Pratiche consolari",
    "Business": "Gestione imprese",
    "Support": "Servizi al cittadino",
}
SERVICE_TITLE_LABELS = {
    "caf-730": "Modello 730 e redditi",
    "caf-bonus": "Bonus e agevolazioni",
    "caf-cambio-residenza": "Cambio residenza",
    "caf-f24": "Modelli F24",
    "caf-imu": "IMU",
    "caf-isee": "Dichiarazione ISEE",
    "caf-modello-redditi": "Modello Redditi",
    "caf-pec": "Attivazione PEC",
    "caf-red-icric": "RED e ICRIC",
    "caf-spid": "SPID",
    "caf-successioni": "Successioni e volture",
    "patronato-adi": "Assegno di inclusione",
    "patronato-assegno-unico": "Assegno unico",
    "patronato-dimissioni": "Dimissioni online",
    "patronato-disoccupazione": "Disoccupazione e NASPI",
    "patronato-invalidita-civile": "Invalidita' civile",
    "patronato-legge-104": "Legge 104",
    "patronato-maternita": "Maternita'",
    "patronato-pensione": "Pensioni e previdenza",
    "immigration-asilo-politico": "Asilo politico",
    "immigration-cittadinanza": "Cittadinanza italiana",
    "immigration-conversione-permesso": "Conversione permesso",
    "immigration-document-translation": "Traduzione documenti",
    "immigration-flussi": "Decreto flussi",
    "immigration-international-documents": "Documenti internazionali",
    "immigration-kit-soggiorno": "Kit soggiorno",
    "immigration-residence-permit": "Permesso di soggiorno",
    "immigration-ricongiungimento-familiare": "Ricongiungimento familiare",
    "admission-borsa-di-studio": "Borsa di studio",
    "admission-student-document-support": "Supporto documenti studente",
    "admission-university-applications": "Domande universitarie",
    "embassy-citizenship": "Cittadinanza in ambito consolare",
    "embassy-oci-card": "OCI Card",
    "embassy-passport-application": "Richiesta passaporto",
    "embassy-passport-miscellaneous-services": "Servizi accessori passaporto",
    "embassy-passport-surrender": "Passport surrender",
    "embassy-visa-services": "Servizi visti",
    "business-camera-di-commercio": "Pratiche Camera di Commercio",
    "business-certificazione-unica": "Certificazione unica",
    "business-corso-sab-ex-rec": "Corso SAB ex REC",
    "business-fattura-elettronica": "Fattura elettronica",
    "business-partita-iva": "Apertura Partita IVA",
    "business-riduzione-contributi-inps": "Riduzione contributi INPS",
    "business-scia": "SCIA e avvio attivita'",
    "support-abbonamento-agevolazione": "Abbonamento agevolato",
    "support-account-registration-apps": "Registrazione account e app",
    "support-appartamenti-affitto-garanzie": "Affitto e garanzie abitative",
    "support-asilo-nido-iscrizione": "Iscrizione asilo nido",
    "support-assicurazione-auto-moto": "Assicurazioni auto e moto",
    "support-domanda-mensa": "Domanda mensa",
    "support-esenzione-tari": "Esenzione TARI",
    "support-rider-compensation": "Supporto rider e indennizzi",
    "support-tessera-sanitaria-codice-fiscale": "Tessera sanitaria e codice fiscale",
    "support-test-lingua-a2": "Test lingua A2",
    "support-traduzioni-legalizzazione": "Traduzioni e legalizzazione",
}
REQUEST_STATUS_LABELS = {
    "new": "Nuova",
    "contacted": "Cliente contattato",
    "documents_requested": "Documenti richiesti",
    "documents_received": "Documenti ricevuti",
    "in_progress": "In lavorazione",
    "waiting_client": "In attesa cliente",
    "completed": "Completata",
    "cancelled": "Annullata",
}
PAYMENT_STATUS_LABELS = {
    "not_requested": "Non richiesto",
    "requested": "Richiesto",
    "bank_transfer_requested": "Bonifico richiesto",
    "pending_confirmation": "In verifica",
    "pending": "In verifica",
    "paid": "Pagato",
    "not_required": "Non richiesto",
}
DOCUMENT_STATUS_LABELS = {"received": "Ricevuto", "under_review": "In verifica", "approved": "Approvato", "missing_info": "Documenti mancanti", "rejected": "Da correggere", "scan_pending": "Scansione in attesa"}
NOTIFICATION_STATUS_LABELS = {"queued": "In coda", "sent": "Inviata", "failed": "Non inviata", "pending": "Da riprovare"}
SCAN_STATUS_LABELS = {"not_scanned": "Controllo non attivo", "scan_pending": "Scansione in attesa", "not_applicable": "Non applicabile"}
WHATSAPP_REMINDER_TYPES = {"confirmation", "reminder_24h", "missing_documents", "payment_pending"}
AUDIT_ACTION_LABELS = {
    "login": "Accesso effettuato",
    "service_booked": "Richiesta servizio inviata",
    "appointment_created": "Richiesta creata",
    "appointment_status_updated": "Stato richiesta aggiornato",
    "appointment_deleted": "Richiesta eliminata",
    "appointment_email_sent": "Email operativa inviata",
    "document_uploaded": "Documento caricato",
    "document_viewed": "Documento scaricato",
    "document_status_updated": "Stato documento aggiornato",
    "document_deleted": "Documento eliminato",
    "client_data_exported": "Esportazione dati cliente",
    "user_anonymized": "Utente anonimizzato",
    "employee_created": "Operatore creato",
    "request_assigned": "Richiesta assegnata",
    "request_reassigned": "Richiesta riassegnata",
    "checklist_item_created": "Attivita' checklist creata",
    "checklist_item_updated": "Attivita' checklist aggiornata",
    "internal_note_added": "Nota interna aggiunta",
    "client_update_added": "Aggiornamento cliente aggiunto",
    "document_requirement_updated": "Requisito documento aggiornato",
    "admin_2fa_enabled": "2FA admin attivata",
    "admin_2fa_verified": "Verifica 2FA admin",
    "password_reset_requested": "Richiesta reset password",
    "password_reset_completed": "Password aggiornata",
    "notification_sent": "Notifica creata",
}
AUDIT_TARGET_LABELS = {"appointment": "Richiesta", "document": "Documento", "user": "Utente"}
ROLE_LABELS = {"admin": "Amministratore", "manager": "Responsabile", "employee": "Operatore", "client": "Cliente"}
ROLE_PERMISSION_MATRIX = {
    "admin": [
        "Gestione completa richieste",
        "Creazione e disattivazione operatori",
        "Analytics, audit, export e strumenti lancio",
        "Configurazione 2FA e controlli sicurezza",
    ],
    "manager": [
        "Gestione richieste e assegnazioni",
        "Vista performance team",
        "Analytics e notifiche staff",
        "Creazione operatori non amministratori",
    ],
    "employee": [
        "Accesso solo alle richieste assegnate",
        "Checklist, commenti e documenti delle proprie pratiche",
        "Notifiche operative personali",
        "Nessun accesso a gestione operatori o analytics admin",
    ],
    "client": [
        "Accesso solo alle proprie pratiche",
        "Upload documenti e download invito calendario",
        "Lettura aggiornamenti pubblici del team",
        "Nessuna informazione interna staff",
    ],
}


def service(slug: str, category: str, title: str, summary: str, documents: list[str] | None = None, price_type: str = "quote_required", price_amount: float | None = None, icon: str = "file") -> dict[str, Any]:
    return {
        "slug": slug,
        "category": category,
        "title": title,
        "summary": summary,
        "documents": documents or COMMON_DOCUMENTS,
        "price_type": price_type,
        "price_amount": price_amount,
        "icon": icon,
    }


SERVICES = [
    service("caf-730", "CAF", "730 Tax Filing", "Annual personal tax declaration with full checklist and validation.", ["CU or income statement", "Deductible expenses", "ID and codice fiscale"], "fixed", 45, "receipt"),
    service("caf-bonus", "CAF", "Bonus Applications", "Support for household, family and public benefit bonus requests.", price_type="quote_required", icon="spark"),
    service("caf-cambio-residenza", "CAF", "Cambio Residenza", "Residence change request preparation and municipal filing support.", price_type="fixed", price_amount=35, icon="map"),
    service("caf-f24", "CAF", "F24 Payment Forms", "Tax and payment form preparation with guided submission support.", price_type="fixed", price_amount=20, icon="bank"),
    service("caf-imu", "CAF", "IMU", "Municipal property tax calculation and filing support.", price_type="quote_required", icon="home"),
    service("caf-isee", "CAF", "ISEE Declaration", "Family economic profile support for bonus and welfare access.", ["ID or passport", "Codice fiscale", "Income records", "Residence certificate"], "fixed", 30, "folder"),
    service("caf-modello-redditi", "CAF", "Modello Redditi", "Income declaration support for individuals and special filing cases.", price_type="fixed", price_amount=60, icon="receipt"),
    service("caf-pec", "CAF", "PEC", "Certified email setup and administrative support.", price_type="fixed", price_amount=25, icon="mail"),
    service("caf-red-icric", "CAF", "RED / ICRIC", "Pension and disability declaration support.", price_type="fixed", price_amount=30, icon="shield"),
    service("caf-spid", "CAF", "SPID", "Digital identity activation and assistance.", price_type="free", price_amount=0, icon="key"),
    service("caf-successioni", "CAF", "Successioni", "Inheritance declaration and document preparation support.", price_type="quote_required", icon="archive"),
    service("patronato-adi", "Patronato", "ADI", "Support for inclusion allowance and related welfare practices.", price_type="free", price_amount=0, icon="heart"),
    service("patronato-assegno-unico", "Patronato", "Assegno Unico", "Family allowance application and renewal assistance.", price_type="free", price_amount=0, icon="family"),
    service("patronato-dimissioni", "Patronato", "Dimissioni", "Online resignation procedure support.", price_type="fixed", price_amount=25, icon="briefcase"),
    service("patronato-disoccupazione", "Patronato", "Disoccupazione / NASPI", "Unemployment benefits request and status handling.", ["Termination letter", "Employment contract", "IBAN", "ID"], "free", 0, "briefcase"),
    service("patronato-invalidita-civile", "Patronato", "Invalidita Civile", "Civil disability application and documentation support.", price_type="free", price_amount=0, icon="medical"),
    service("patronato-legge-104", "Patronato", "Legge 104", "Assistance with Legge 104 requests and supporting documents.", price_type="free", price_amount=0, icon="medical"),
    service("patronato-maternita", "Patronato", "Maternita", "Maternity benefit application support.", price_type="free", price_amount=0, icon="family"),
    service("patronato-pensione", "Patronato", "Pensione", "Pension practice support, planning and filing assistance.", ["ID", "Contribution history", "Employment records"], "quote_required", None, "clock"),
    service("immigration-asilo-politico", "Immigration", "Asilo Politico", "Political asylum procedure guidance and document review.", price_type="quote_required", icon="globe"),
    service("immigration-cittadinanza", "Immigration", "Cittadinanza", "Citizenship eligibility review and application support.", ["Birth certificate", "Residence records", "Language certificate", "Criminal clearance"], "quote_required", None, "globe"),
    service("immigration-conversione-permesso", "Immigration", "Conversione Permesso", "Permit conversion support for eligible immigration cases.", price_type="quote_required", icon="passport"),
    service("immigration-document-translation", "Immigration", "Document Translation", "Translation support for immigration and administrative dossiers.", price_type="quote_required", icon="translate"),
    service("immigration-flussi", "Immigration", "Flussi", "Decreto flussi application and document preparation support.", price_type="quote_required", icon="air"),
    service("immigration-international-documents", "Immigration", "International Documents", "Support for international documents, legalization and administrative use.", price_type="quote_required", icon="file"),
    service("immigration-kit-soggiorno", "Immigration", "Kit Soggiorno", "Postal kit preparation for stay permit requests and renewals.", price_type="fixed", price_amount=50, icon="mail"),
    service("immigration-residence-permit", "Immigration", "Residence Permit", "Permit issuance and renewal with full procedural support.", ["Passport", "Current permit", "Accommodation proof", "Income proof"], "quote_required", None, "passport"),
    service("immigration-ricongiungimento-familiare", "Immigration", "Ricongiungimento Familiare", "Family reunification application and document support.", price_type="quote_required", icon="family"),
    service("admission-borsa-di-studio", "Admission", "Borsa di Studio", "Scholarship application and document preparation support.", price_type="quote_required", icon="cap"),
    service("admission-student-document-support", "Admission", "Student Document Support", "Student paperwork, enrollment and administrative assistance.", price_type="quote_required", icon="cap"),
    service("admission-university-applications", "Admission", "University Applications", "University admission guidance and application filing.", price_type="quote_required", icon="cap"),
    service("embassy-citizenship", "Embassy", "Citizenship", "Embassy-related citizenship paperwork and appointment support.", price_type="quote_required", icon="stamp"),
    service("embassy-oci-card", "Embassy", "OCI Card", "OCI card application and document assistance.", price_type="quote_required", icon="stamp"),
    service("embassy-passport-application", "Embassy", "Passport Application", "Passport application preparation and appointment support.", price_type="quote_required", icon="passport"),
    service("embassy-passport-miscellaneous-services", "Embassy", "Passport Miscellaneous Services", "Support for passport corrections, renewals and related services.", price_type="quote_required", icon="passport"),
    service("embassy-passport-surrender", "Embassy", "Passport Surrender", "Passport surrender application and document support.", price_type="quote_required", icon="passport"),
    service("embassy-visa-services", "Embassy", "Visa Services", "Visa document preparation and application assistance.", price_type="quote_required", icon="air"),
    service("business-camera-di-commercio", "Business", "Camera di Commercio", "Chamber of commerce filings and company records support.", price_type="quote_required", icon="building"),
    service("business-certificazione-unica", "Business", "Certificazione Unica", "CU preparation and administrative support for businesses.", price_type="quote_required", icon="bank"),
    service("business-corso-sab-ex-rec", "Business", "Corso SAB Ex REC", "Guidance for SAB Ex REC course enrollment and documentation.", price_type="quote_required", icon="cap"),
    service("business-fattura-elettronica", "Business", "Fattura Elettronica", "E-invoicing setup and compliance operations for businesses.", ["VAT profile", "SDI or PEC details", "Company registration info"], "fixed", 80, "bank"),
    service("business-partita-iva", "Business", "Partita IVA", "VAT setup, tax regime guidance and registration workflow.", ["ID", "Business activity details", "Residence proof"], "fixed", 120, "building"),
    service("business-riduzione-contributi-inps", "Business", "Riduzione Contributi INPS", "INPS contribution reduction request support.", price_type="quote_required", icon="building"),
    service("business-scia", "Business", "SCIA", "SCIA filing support for business activities and authorizations.", price_type="quote_required", icon="building"),
    service("support-abbonamento-agevolazione", "Support", "Abbonamento Agevolazione", "Discounted subscription and local benefit application support.", price_type="free", price_amount=0, icon="train"),
    service("support-account-registration-apps", "Support", "Account Registration Apps", "Account setup for public and service apps.", price_type="fixed", price_amount=20, icon="phone"),
    service("support-appartamenti-affitto-garanzie", "Support", "Appartamenti Affitto Garanzie", "Rental, guarantee and housing document support.", price_type="quote_required", icon="home"),
    service("support-asilo-nido-iscrizione", "Support", "Asilo Nido Iscrizione", "Nursery enrollment application support.", price_type="fixed", price_amount=30, icon="family"),
    service("support-assicurazione-auto-moto", "Support", "Assicurazione Auto Moto", "Car and motorbike insurance support.", price_type="quote_required", icon="car"),
    service("support-domanda-mensa", "Support", "Domanda Mensa", "School meal application assistance.", price_type="fixed", price_amount=20, icon="food"),
    service("support-esenzione-tari", "Support", "Esenzione TARI", "TARI exemption request support.", price_type="quote_required", icon="home"),
    service("support-rider-compensation", "Support", "Rider Compensation", "Support for rider compensation and related claims.", price_type="quote_required", icon="bike"),
    service("support-tessera-sanitaria-codice-fiscale", "Support", "Tessera Sanitaria / Codice Fiscale", "Health card and tax code assistance.", price_type="free", price_amount=0, icon="medical"),
    service("support-test-lingua-a2", "Support", "Test Lingua A2", "A2 Italian language test booking and preparation support.", price_type="fixed", price_amount=35, icon="cap"),
    service("support-traduzioni-legalizzazione", "Support", "Traduzioni e Legalizzazione", "Certified translation and legalization management for official use.", ["Original documents", "Destination authority info", "ID"], "quote_required", None, "translate"),
]
SERVICE_BY_SLUG = {s["slug"]: s for s in SERVICES}
SERVICE_TITLES = [f"{s['category']} - {s['title']}" for s in SERVICES]
SERVICE_CATEGORIES = ["CAF", "Patronato", "Immigration", "Admission", "Embassy", "Business", "Support"]
SEO_LANDING_PAGES = {
    "modello-730-roma": {
        "service_slug": "caf-730",
        "title": "Modello 730 Roma",
        "description": "Assistenza Modello 730 a Roma con supporto documentale, controllo preliminare e appuntamenti presso la sede ConsultIQ in zona Tuscolana - Numidio Quadrato.",
        "faqs": [("Quali documenti servono per il 730?", "Documento di identita', codice fiscale, CU, certificazioni e spese detraibili o deducibili."), ("Seguite anche nuclei familiari e lavoratori dipendenti?", "Si', valutiamo la documentazione disponibile e indichiamo i passaggi amministrativi corretti."), ("Dove si trova l'ufficio?", CONSULTIQ_ADDRESS)],
    },
    "isee-roma": {
        "service_slug": "caf-isee",
        "title": "ISEE Roma",
        "description": "Calcolo ISEE a Roma per bonus, agevolazioni e pratiche familiari con checklist chiara e assistenza in zona Appio Tuscolano.",
        "faqs": [("Quanto tempo serve per preparare l'ISEE?", "Dipende dalla completezza dei documenti, ma la verifica preliminare viene impostata rapidamente."), ("Posso inviare i documenti online?", "Si', puoi caricare i documenti dall'area cliente o allegarli alla prenotazione."), ("Dove siete a Roma?", CONSULTIQ_ADDRESS)],
    },
    "spid-roma": {
        "service_slug": "caf-spid",
        "title": "SPID Roma",
        "description": "Attivazione e supporto SPID a Roma vicino Metro A Numidio Quadrato per cittadini, famiglie e utenti che hanno bisogno di accesso ai servizi online.",
        "faqs": [("Serve appuntamento per SPID?", "Consigliamo la prenotazione per gestire la pratica con piu' tranquillita'."), ("Quali documenti devo portare?", "Documento di identita', codice fiscale e recapiti aggiornati."), ("Il servizio e' vicino alla Metro A?", "Si', la sede e' a pochi passi da Numidio Quadrato.")],
    },
    "permesso-di-soggiorno-roma": {
        "service_slug": "immigration-residence-permit",
        "title": "Permesso di soggiorno Roma",
        "description": "Supporto per rinnovo e rilascio permesso di soggiorno a Roma con verifica documenti, prenotazione e assistenza operativa in zona Cinecitta' e Tuscolana.",
        "faqs": [("Posso caricare i documenti prima dell'appuntamento?", "Si', il portale consente l'upload dei documenti collegati alla pratica."), ("Fornite check-list documentale?", "Si', trovi documenti richiesti e note operative nella pagina del servizio."), ("L'ufficio e' a Roma citta'?", CONSULTIQ_ADDRESS)],
    },
    "cittadinanza-italiana-roma": {
        "service_slug": "immigration-cittadinanza",
        "title": "Cittadinanza italiana Roma",
        "description": "Supporto documentale per cittadinanza italiana a Roma con verifica preliminare dei requisiti e coordinamento dei passaggi amministrativi.",
        "faqs": [("Analizzate i requisiti prima della pratica?", "Si', facciamo una verifica preliminare dei documenti disponibili e dei passaggi da completare."), ("Posso richiedere supporto anche per integrazioni?", "Si', il team puo' aiutarti a organizzare integrazioni e caricamenti documentali."), ("Dove si trova la sede?", CONSULTIQ_ADDRESS)],
    },
    "ricongiungimento-familiare-roma": {
        "service_slug": "immigration-ricongiungimento-familiare",
        "title": "Ricongiungimento familiare Roma",
        "description": "Assistenza per ricongiungimento familiare a Roma con supporto su alloggio, reddito e dossier documentale in zona Quadraro, Don Bosco e Appio.",
        "faqs": [("Seguite anche i documenti dell'alloggio?", "Si', rientrano nella verifica preliminare della pratica e nella checklist."), ("Posso ricevere una lista dei documenti mancanti?", "Si', l'area amministrativa puo' inviarti richieste documentali puntuali."), ("L'ufficio e' raggiungibile con Metro A?", "Si', siamo a Numidio Quadrato.")],
    },
    "apertura-partita-iva-roma": {
        "service_slug": "business-partita-iva",
        "title": "Apertura Partita IVA Roma",
        "description": "Apertura Partita IVA a Roma con orientamento sul regime fiscale, checklist documentale e avvio attivita' nella sede ConsultIQ di Numidio Quadrato.",
        "faqs": [("Aiutate anche nella scelta del regime?", "Si', l'analisi preliminare serve a orientare la pratica in modo coerente."), ("Serve gia' un business plan?", "Non sempre, dipende dal tipo di attivita' e dagli adempimenti richiesti."), ("Dove siete a Roma?", CONSULTIQ_ADDRESS)],
    },
    "apertura-srl-roma": {
        "service_slug": "business-camera-di-commercio",
        "title": "Apertura SRL Roma",
        "description": "Supporto operativo per apertura SRL a Roma, pratiche Camera di Commercio e coordinamento documentale per nuove societa'.",
        "faqs": [("Seguite anche gli adempimenti iniziali?", "Si', coordiniamo i passaggi documentali e amministrativi di avvio."), ("Serve un appuntamento in sede?", "Spesso e' utile, ma possiamo iniziare con una raccolta preliminare online."), ("La sede e' a Numidio Quadrato?", "Si', in Via Licinio Murena 32-34, Roma.")],
    },
    "startup-innovativa-roma": {
        "service_slug": "business-scia",
        "title": "Startup innovativa Roma",
        "description": "Assistenza iniziale per startup innovativa a Roma con orientamento pratico su apertura, documenti e primi adempimenti per team e professionisti.",
        "faqs": [("Seguite anche la parte documentale iniziale?", "Si', la piattaforma aiuta a centralizzare i passaggi documentali e operativi."), ("Posso prenotare una consulenza introduttiva?", "Si', dalla pagina puoi inviare una richiesta rapida e ricevere un primo riscontro."), ("Dove trovo l'ufficio?", CONSULTIQ_ADDRESS)],
    },
}
NEWS_ITEMS = [
    {"date": "2026-05-10", "title": "Campagna 730 aperta", "summary": "Sono disponibili appuntamenti dedicati per raccolta documenti e verifica preliminare delle dichiarazioni."},
    {"date": "2026-05-22", "title": "Rinnovo permesso: flusso piu' ordinato", "summary": "La nuova gestione documentale riduce le integrazioni e migliora il controllo delle pratiche."},
    {"date": "2026-05-28", "title": "Nuovo supporto per avvio impresa", "summary": "ConsultIQ amplia i servizi per Partita IVA, SCIA e organizzazione documentale iniziale."},
]


def format_price(service_item: dict[str, Any]) -> str:
    if service_item["price_type"] == "free":
        return "Gratuito"
    if service_item["price_type"] == "fixed" and service_item["price_amount"] is not None:
        return f"Da EUR {int(service_item['price_amount'])}"
    return "Preventivo su richiesta"


def category_icon(category: str) -> str:
    icons = {
        "CAF": "grid",
        "Patronato": "care",
        "Immigration": "globe",
        "Admission": "cap",
        "Embassy": "stamp",
        "Business": "building",
        "Support": "spark",
    }
    return icons.get(category, "file")


def italian_service_category(category: str) -> str:
    return SERVICE_CATEGORY_LABELS.get(category, category)


def italian_service_title(service_item: dict[str, Any]) -> str:
    return SERVICE_TITLE_LABELS.get(service_item["slug"], service_item["title"])


def italian_service_summary(service_item: dict[str, Any]) -> str:
    default_summary = (
        f"Supporto operativo per {italian_service_title(service_item).lower()} con verifica documentale, "
        f"prenotazione e gestione pratica presso la sede ConsultIQ di Roma."
    )
    return service_item.get("display_summary") or default_summary


def normalize_service_documents(service_item: dict[str, Any]) -> list[str]:
    docs = []
    for item in service_item.get("documents", []):
        docs.append(
            {
                "ID or passport": "Documento di identita' o passaporto",
                "Contact details": "Recapiti aggiornati",
                "Existing case documents": "Documenti gia' disponibili sulla pratica",
                "Residence certificate": "Certificato o attestazione di residenza",
                "Income records": "Documentazione reddituale",
                "CU or income statement": "CU o certificazioni redditi",
                "Deductible expenses": "Spese detraibili o deducibili",
                "Termination letter": "Lettera di cessazione rapporto",
                "Employment contract": "Contratto di lavoro",
                "Contribution history": "Estratto contributivo",
                "Employment records": "Storico lavorativo",
                "Birth certificate": "Certificato di nascita",
                "Residence records": "Documenti di residenza",
                "Language certificate": "Certificazione linguistica",
                "Criminal clearance": "Certificati penali richiesti",
                "Passport": "Passaporto",
                "Current permit": "Permesso attuale",
                "Accommodation proof": "Prova di alloggio",
                "Income proof": "Prova di reddito",
                "Original documents": "Documenti originali",
                "Destination authority info": "Dati dell'ente destinatario",
                "VAT profile": "Dati fiscali e partita IVA",
                "SDI or PEC details": "Dati SDI o PEC",
                "Company registration info": "Dati aziendali e iscrizione",
                "Business activity details": "Dettagli dell'attivita'",
                "Residence proof": "Prova di residenza",
            }.get(item, item)
        )
    return docs


def status_label(value: str) -> str:
    return REQUEST_STATUS_LABELS.get(value, value)


def payment_status_label(value: str) -> str:
    return PAYMENT_STATUS_LABELS.get(value, value)


def document_status_label(value: str) -> str:
    return DOCUMENT_STATUS_LABELS.get(value, value)


def notification_status_label(value: str) -> str:
    return NOTIFICATION_STATUS_LABELS.get(value, value)


def scan_status_label(value: str) -> str:
    return SCAN_STATUS_LABELS.get(value, value)


def audit_action_label(value: str) -> str:
    return AUDIT_ACTION_LABELS.get(value, value.replace("_", " ").capitalize())


def audit_target_label(value: str) -> str:
    return AUDIT_TARGET_LABELS.get(value, value.capitalize())


def audit_detail_label(value: str | None) -> str:
    if not value:
        return "-"
    replacements = {
        "in_progress": status_label("in_progress"),
        "completed": status_label("completed"),
        "pending": "In attesa",
        "new": status_label("new"),
        "waiting_client": status_label("waiting_client"),
        "documents_requested": status_label("documents_requested"),
        "documents_received": status_label("documents_received"),
        "cancelled": status_label("cancelled"),
        "employee": role_label("employee"),
        "client": role_label("client"),
        "admin": role_label("admin"),
        "manager": role_label("manager"),
        "deleted_by_admin": "Eliminata da amministratore",
        "json_export": "Esportazione JSON",
        "email_sent": "Email inviata",
        "success": "Operazione completata",
        "enabled": "Attivata",
        "document_expiry": "Scadenza documento",
        "auto_assign": "Assegnazione automatica",
    }
    readable = value
    for raw, label in replacements.items():
        readable = readable.replace(raw, label)
    for service_item in SERVICES:
        raw_service = f"{service_item['category']} - {service_item['title']}"
        readable = readable.replace(raw_service, display_service_type(raw_service))
    return readable


def role_label(value: str) -> str:
    return ROLE_LABELS.get(value, value.capitalize())


def request_priority_label(value: str) -> str:
    labels = {"bassa": "Bassa", "media": "Media", "alta": "Alta", "urgente": "Urgente"}
    return labels.get(value, value.capitalize())


def request_area_label(service_type: str) -> str:
    service_item = find_service_by_service_type(service_type)
    if service_item:
        return service_item["display_category"]
    raw = (service_type or "").split(" - ")[0]
    return italian_service_category(raw)


def request_home_path(user: dict[str, Any]) -> str:
    if user["role"] == "admin":
        return "/admin"
    if user["role"] in {"manager", "employee"}:
        return "/employee/dashboard"
    return "/client-area"


def can_manage_request(user: dict[str, Any], request_row: sqlite3.Row | dict[str, Any]) -> bool:
    if user["role"] == "admin":
        return True
    if user["role"] == "manager":
        return True
    if user["role"] == "employee":
        assigned_id = request_row["assigned_employee_id"] if isinstance(request_row, sqlite3.Row) else request_row.get("assigned_employee_id")
        return assigned_id == user["id"]
    request_user_id = request_row["user_id"] if isinstance(request_row, sqlite3.Row) else request_row.get("user_id")
    request_email = request_row["email"] if isinstance(request_row, sqlite3.Row) else request_row.get("email")
    return request_user_id == user["id"] or request_email == user["email"]


def append_internal_note(existing: str | None, author: str, note: str) -> str:
    clean = note.strip()
    if not clean:
        return existing or ""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"[{stamp}] {author}: {clean}"
    return f"{existing}\n{line}".strip() if existing else line


def parse_request_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None


def request_age_days(row: sqlite3.Row | dict[str, Any]) -> int:
    raw = row["created_at"] if isinstance(row, sqlite3.Row) else row.get("created_at")
    created = parse_request_date(raw)
    return max((date.today() - created).days, 0) if created else 0


def request_age_label(row: sqlite3.Row | dict[str, Any]) -> str:
    days = request_age_days(row)
    if days == 0:
        return "Oggi"
    if days == 1:
        return "1 giorno"
    if 2 <= days <= 3:
        return "2-3 giorni"
    if 4 <= days <= 7:
        return "Oltre 3 giorni"
    return "Oltre 7 giorni"


def request_sla_class(row: sqlite3.Row | dict[str, Any]) -> str:
    days = request_age_days(row)
    status = row["status"] if isinstance(row, sqlite3.Row) else row.get("status")
    priority = row["priority"] if isinstance(row, sqlite3.Row) else row.get("priority")
    assigned = row["assigned_employee_id"] if isinstance(row, sqlite3.Row) else row.get("assigned_employee_id")
    if priority == "urgente":
        return "sla-urgent"
    if not assigned and days >= 1:
        return "sla-warning"
    if status == "waiting_client" and days > 3:
        return "sla-warning"
    if status == "in_progress" and days > 7:
        return "sla-danger"
    return "sla-ok"


def request_sla_status(row: sqlite3.Row | dict[str, Any]) -> str:
    css_class = request_sla_class(row)
    if css_class in {"sla-danger", "sla-urgent"}:
        return "overdue"
    if css_class == "sla-warning":
        return "warning"
    return "ok"


def request_sla_label(row: sqlite3.Row | dict[str, Any]) -> str:
    return SLA_STATUS_LABELS[request_sla_status(row)]


def checklist_progress(items: list[sqlite3.Row]) -> dict[str, int]:
    total = len(items)
    done = sum(1 for item in items if item["completed"])
    return {"total": total, "done": done, "percent": int((done / total) * 100) if total else 0}


def display_service_type(service_type: str) -> str:
    service_item = find_service_by_service_type(service_type)
    if not service_item:
        return service_type
    return f"{service_item['display_category']} - {service_item['display_title']}"


def sanitize_phone_number(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):
        return f"39{digits}"
    return digits


def build_whatsapp_send_link(phone: str, text: str) -> str:
    normalized = sanitize_phone_number(phone)
    if not normalized:
        return ""
    return f"https://wa.me/{normalized}?text={quote(text)}"


def generate_whatsapp_reminder(appointment: sqlite3.Row | dict[str, Any], reminder_type: str = "confirmation", missing_documents: str = "") -> str:
    reminder = reminder_type if reminder_type in WHATSAPP_REMINDER_TYPES else "confirmation"
    service_name = display_service_type(appointment["service_type"])
    client_name = appointment["full_name"]
    preferred_date = appointment["preferred_date"]
    intro = f"Gentile {client_name}, ti scriviamo da ConsultIQ in merito alla pratica {service_name}."
    if reminder == "reminder_24h":
        return (
            f"{intro} Ti ricordiamo l'appuntamento previsto per il {preferred_date} presso la sede di {CONSULTIQ_ADDRESS}. "
            "Se hai nuovi documenti, puoi caricarli prima dell'incontro oppure portarli con te."
        )
    if reminder == "missing_documents":
        docs_text = missing_documents.strip() or "documentazione integrativa"
        return (
            f"{intro} Per procedere abbiamo bisogno dei seguenti documenti: {docs_text}. "
            "Puoi inviarli dall'area cliente oppure consegnarli in sede dopo conferma del team."
        )
    if reminder == "payment_pending":
        return (
            f"{intro} La pratica risulta con pagamento amministrativo in attesa. "
            "Per la verifica manuale o per ricevere istruzioni aggiornate, contattaci oppure rispondi a questo messaggio."
        )
    return (
        f"{intro} Abbiamo preso in carico la richiesta e ti contatteremo per conferma appuntamento, "
        "verifica documenti e prossimi passaggi amministrativi."
    )


def safe_next_url(next_url: str | None, fallback: str) -> str:
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return fallback


for service_item in SERVICES:
    service_item["display_category"] = italian_service_category(service_item["category"])
    service_item["display_title"] = italian_service_title(service_item)
    service_item["documents"] = normalize_service_documents(service_item)
    service_item["display_summary"] = italian_service_summary(service_item)
    service_item["price_label"] = format_price(service_item)
    service_item["category_icon"] = category_icon(service_item["category"])


def find_service_by_service_type(service_type: str) -> dict[str, Any] | None:
    for service_item in SERVICES:
        if f"{service_item['category']} - {service_item['title']}" == service_type:
            return service_item
    return None

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


def scan_upload(document: UploadFile | None) -> str:
    if not document or not document.filename:
        return "not_applicable"
    if not CLAMAV_ENABLED:
        return "not_scanned"
    if CLAMAV_FAIL_MODE == "fail_safe":
        raise HTTPException(status_code=503, detail="Antivirus scan service unavailable")
    return "scan_pending"


def save_appointment_document(conn: sqlite3.Connection, appointment_id: int, uploaded_by_user_id: int | None, document: UploadFile) -> None:
    scan_status = scan_upload(document)
    safe_name = f"{uuid.uuid4().hex}_{Path(document.filename or 'upload.bin').name}"
    save_path = UPLOAD_DIR / safe_name
    with save_path.open("wb") as buffer:
        shutil.copyfileobj(document.file, buffer)
    conn.execute(
        "INSERT INTO appointment_documents (appointment_id, uploaded_by_user_id, original_filename, stored_filename, file_path, category, status, scan_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (appointment_id, uploaded_by_user_id, document.filename or "document", safe_name, f"/uploads/{safe_name}", "Altro", "received", scan_status),
    )


def get_current_user(request: Request) -> dict[str, Any] | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, full_name, email, phone, role, admin_totp_secret, active FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def require_login(request: Request) -> dict[str, Any] | None:
    return get_current_user(request)


def require_client(request: Request) -> dict[str, Any] | None:
    user = get_current_user(request)
    if user and user.get("role") == "client":
        return user
    return None


def require_staff(request: Request) -> dict[str, Any] | None:
    user = get_current_user(request)
    if user and user.get("role") in {"admin", "manager", "employee"} and user.get("active", 1):
        return user
    return None


def require_admin(request: Request) -> dict[str, Any] | None:
    user = get_current_user(request)
    if user and user.get("role") == "admin" and user.get("active", 1):
        return user
    return None


def require_management(request: Request) -> dict[str, Any] | None:
    user = get_current_user(request)
    if user and user.get("role") in {"admin", "manager"} and user.get("active", 1):
        return user
    return None


def is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def enforce_required_consents(privacy_accept: str | None, terms_accept: str | None = None) -> None:
    if not is_truthy(privacy_accept):
        raise HTTPException(status_code=400, detail="Privacy acceptance required")
    if terms_accept is not None and not is_truthy(terms_accept):
        raise HTTPException(status_code=400, detail="Terms acceptance required")


def build_whatsapp_link(service_name: str, client_name: str = "", preferred_date: str = "", missing_documents: str = "") -> str:
    parts = [
        "Ciao Consultiq Italy,",
        f"sono {client_name}." if client_name else "",
        f"Ho bisogno di assistenza per: {service_name}.",
        f"Data richiesta: {preferred_date}." if preferred_date else "",
        f"Documenti mancanti: {missing_documents}." if missing_documents else "",
        "Vorrei un supporto sul prossimo passo.",
    ]
    text = " ".join(part for part in parts if part).strip()
    return f"https://wa.me/{WHATSAPP_BUSINESS_NUMBER}?text={quote(text)}"


def create_checkout_session(_appointment_id: int) -> dict[str, Any]:
    return {
        "enabled": bool(STRIPE_ENABLED and STRIPE_SECRET_KEY),
        "provider": "stripe",
        "status": "stub",
        "message": "Integrare Stripe Checkout in una fase successiva con chiavi ambiente valide.",
    }


def appointment_start_dt(appointment: sqlite3.Row | dict[str, Any]) -> datetime:
    preferred_date = (appointment["preferred_date"] or "").strip()
    day = datetime.fromisoformat(preferred_date).date() if preferred_date else date.today()
    return datetime.combine(day, datetime.min.time()).replace(hour=10, minute=0)


def generate_ics_for_appointment(appointment: sqlite3.Row | dict[str, Any]) -> str:
    start_dt = appointment_start_dt(appointment)
    end_dt = start_dt + timedelta(hours=1)
    uid = f"consultiq-{appointment['id']}@consultiq.it"
    summary = f"ConsultIQ - {display_service_type(appointment['service_type'])}"
    description = (
        f"Cliente: {appointment['full_name']}\\n"
        f"Servizio: {display_service_type(appointment['service_type'])}\\n"
        f"Dettagli: {(appointment['message'] or '').strip() or 'Da completare in sede'}\\n"
        f"Contatti: {CONSULTIQ_PHONE} - {CONSULTIQ_EMAIL}"
    )
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Consultiq Italy//Calendar Invite//IT",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{summary}",
            f"LOCATION:{CONSULTIQ_ADDRESS}",
            f"DESCRIPTION:{description}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )


def calendar_period(view: str, reference: date) -> tuple[date, date]:
    if view == "day":
        return reference, reference
    if view == "week":
        start = reference - timedelta(days=reference.weekday())
        return start, start + timedelta(days=6)
    start = reference.replace(day=1)
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    end = next_month - timedelta(days=1)
    return start, end


def shift_calendar_reference(view: str, reference: date, direction: int) -> date:
    if view == "day":
        return reference + timedelta(days=direction)
    if view == "week":
        return reference + timedelta(days=7 * direction)
    month = reference.month + direction
    year = reference.year
    if month < 1:
        month = 12
        year -= 1
    if month > 12:
        month = 1
        year += 1
    return date(year, month, 1)


def calendar_cells(start: date, end: date, appointments: list[sqlite3.Row]) -> list[dict[str, Any]]:
    items = []
    current = start
    while current <= end:
        items.append(
            {
                "date": current.isoformat(),
                "label": current.strftime("%d %b"),
                "appointments": [a for a in appointments if a["preferred_date"] == current.isoformat()],
            }
        )
        current += timedelta(days=1)
    return items


def compute_admin_analytics(appointments: list[sqlite3.Row], contacts: list[sqlite3.Row], documents: list[sqlite3.Row]) -> dict[str, Any]:
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    _, month_end = calendar_period("month", today)
    appointments_today = sum(1 for a in appointments if a["preferred_date"] == today.isoformat())
    appointments_week = sum(1 for a in appointments if a["preferred_date"] and week_start <= datetime.fromisoformat(a["preferred_date"]).date() <= today + timedelta(days=6))
    appointments_month = sum(1 for a in appointments if a["preferred_date"] and month_start <= datetime.fromisoformat(a["preferred_date"]).date() <= month_end)
    service_counter: dict[str, int] = {}
    category_counter: dict[str, int] = {}
    monthly_counter: dict[str, int] = {}
    monthly_revenue: dict[str, float] = {}
    returning_emails: dict[str, int] = {}
    for a in appointments:
        service_counter[a["service_type"]] = service_counter.get(a["service_type"], 0) + 1
        category = (a["service_type"] or "").split(" - ")[0]
        category_counter[category] = category_counter.get(category, 0) + 1
        if a["preferred_date"]:
            month_key = a["preferred_date"][:7]
            monthly_counter[month_key] = monthly_counter.get(month_key, 0) + 1
            if a["payment_status"] == "paid":
                amount = float(a["requested_amount"] or a["price_amount"] or 0)
                if amount:
                    monthly_revenue[month_key] = monthly_revenue.get(month_key, 0) + amount
        returning_emails[a["email"]] = returning_emails.get(a["email"], 0) + 1
    pending_documents = sum(1 for d in documents if d["status"] in {"received", "under_review"})
    missing_documents = sum(1 for d in documents if d["status"] == "missing_info")
    completed_appointments = sum(1 for a in appointments if a["status"] == "completed")
    document_completion_rate = round((sum(1 for d in documents if d["status"] == "approved") / max(len(documents), 1)) * 100, 1)
    revenue_this_month = monthly_revenue.get(today.strftime("%Y-%m"), 0.0)
    most_requested = sorted(service_counter.items(), key=lambda item: item[1], reverse=True)[:5]
    conversion_rate = round((len(appointments) / max(len(contacts), 1)) * 100, 1) if contacts else 0.0
    chart_status = [sum(1 for a in appointments if a["status"] == key) for key in REQUEST_STATUSES]
    category_keys = list(category_counter.keys())
    chart_category_labels = [italian_service_category(key) for key in category_keys] or ["Nessuno"]
    chart_category_values = [category_counter[key] for key in category_keys] or [0]
    trend_labels = sorted(monthly_counter.keys()) or [today.strftime("%Y-%m")]
    trend_values = [monthly_counter.get(label, 0) for label in trend_labels]
    revenue_values = [round(monthly_revenue.get(label, 0.0), 2) for label in trend_labels]
    return {
        "appointments_today": appointments_today,
        "appointments_week": appointments_week,
        "appointments_month": appointments_month,
        "most_requested_services": most_requested,
        "revenue_this_month": round(revenue_this_month, 2),
        "pending_documents": pending_documents,
        "missing_documents": missing_documents,
        "completed_appointments": completed_appointments,
        "returning_clients": sum(1 for count in returning_emails.values() if count > 1),
        "document_completion_rate": document_completion_rate,
        "conversion_rate": conversion_rate,
        "chart_status": chart_status,
        "chart_category_labels": chart_category_labels,
        "chart_category_values": chart_category_values,
        "trend_labels": trend_labels,
        "trend_values": trend_values,
        "revenue_trend_values": revenue_values,
    }


def filter_appointments(
    appointments: list[sqlite3.Row],
    date_from: str = "",
    date_to: str = "",
    category: str = "",
    status: str = "",
    payment_status: str = "",
) -> list[sqlite3.Row]:
    start = datetime.fromisoformat(date_from).date() if date_from else None
    end = datetime.fromisoformat(date_to).date() if date_to else None
    filtered = []
    for appointment in appointments:
        preferred = datetime.fromisoformat(appointment["preferred_date"]).date() if appointment["preferred_date"] else None
        if start and preferred and preferred < start:
            continue
        if end and preferred and preferred > end:
            continue
        if category and not (appointment["service_type"] or "").startswith(category):
            continue
        if status and appointment["status"] != status:
            continue
        if payment_status and appointment["payment_status"] != payment_status:
            continue
        filtered.append(appointment)
    return filtered


def analytics_totals(appointments: list[sqlite3.Row]) -> dict[str, Any]:
    return {
        "appointments": len(appointments),
        "completed": sum(1 for a in appointments if a["status"] == "completed"),
        "pending": sum(1 for a in appointments if a["status"] in {"new", "contacted", "documents_requested", "documents_received", "in_progress", "waiting_client"}),
        "cancelled": sum(1 for a in appointments if a["status"] == "cancelled"),
        "paid": sum(1 for a in appointments if a["payment_status"] == "paid"),
        "unpaid_pending": sum(1 for a in appointments if a["payment_status"] in {"bank_transfer_requested", "pending_confirmation"}),
        "estimated_revenue": round(sum(float(a["requested_amount"] or a["price_amount"] or 0) for a in appointments if a["payment_status"] == "paid"), 2),
    }


def analytics_csv(appointments: list[sqlite3.Row], totals: dict[str, Any], mode: str = "appointments") -> str:
    lines: list[str] = []
    if mode == "summary":
        lines.append("metrica,valore")
        for key, value in totals.items():
            lines.append(f"{key},{value}")
        return "\n".join(lines)
    lines.append("id,cliente,email,telefono,area,servizio,data,stato,priorita,assegnato_a,pagamento,importo")
    for appointment in appointments:
        lines.append(
            ",".join(
                [
                    str(appointment["id"]),
                    f"\"{appointment['full_name']}\"",
                    appointment["email"],
                    f"\"{appointment['phone']}\"",
                    f"\"{request_area_label(appointment['service_type'])}\"",
                    f"\"{display_service_type(appointment['service_type'])}\"",
                    appointment["preferred_date"],
                    status_label(appointment["status"]),
                    request_priority_label(appointment["priority"]),
                    f"\"{appointment['assigned_employee_name'] if 'assigned_employee_name' in appointment.keys() and appointment['assigned_employee_name'] else ''}\"" if isinstance(appointment, sqlite3.Row) else f"\"{appointment.get('assigned_employee_name', '')}\"",
                    payment_status_label(appointment["payment_status"]),
                    str(appointment["requested_amount"] or appointment["price_amount"] or 0),
                ]
            )
        )
    return "\n".join(lines)


def request_query_for_role(user: dict[str, Any]) -> str:
    base = (
        "SELECT a.*, "
        "u.full_name AS assigned_employee_name, "
        "u.email AS assigned_employee_email "
        "FROM appointments a "
        "LEFT JOIN users u ON u.id=a.assigned_employee_id"
    )
    if user["role"] == "employee":
        return base + " WHERE a.assigned_employee_id=? ORDER BY COALESCE(a.updated_at, a.created_at) DESC, a.id DESC"
    if user["role"] == "client":
        return base + " WHERE a.user_id=? OR a.email=? ORDER BY a.id DESC"
    return base + " ORDER BY COALESCE(a.updated_at, a.created_at) DESC, a.id DESC"


def fetch_requests_for_user(conn: sqlite3.Connection, user: dict[str, Any]) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    query = request_query_for_role(user)
    if user["role"] == "employee":
        return conn.execute(query, (user["id"],)).fetchall()
    if user["role"] == "client":
        return conn.execute(query, (user["id"], user["email"])).fetchall()
    return conn.execute(query).fetchall()


def fetch_staff_members(conn: sqlite3.Connection, include_inactive: bool = False) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, full_name, email, phone, role, active, created_at FROM users "
        f"WHERE role IN ('admin', 'manager', 'employee'){ ' AND active=1' if not include_inactive else '' } "
        "ORDER BY role, full_name"
    ).fetchall()


def filter_request_rows(
    rows: list[sqlite3.Row],
    status: str = "",
    category: str = "",
    service: str = "",
    assigned_employee_id: str = "",
    priority: str = "",
    payment_status: str = "",
    date_from: str = "",
    date_to: str = "",
    search: str = "",
    quick_filter: str = "",
) -> list[sqlite3.Row]:
    start = parse_request_date(date_from)
    end = parse_request_date(date_to)
    query = search.strip().lower()
    today = date.today()
    filtered: list[sqlite3.Row] = []
    for row in rows:
        preferred = parse_request_date(row["preferred_date"])
        created = parse_request_date(row["created_at"]) or today
        haystack = f"{row['id']} {row['full_name']} {row['email']} {row['phone']} {display_service_type(row['service_type'])}".lower()
        if status and row["status"] != status:
            continue
        if category and not (row["service_type"] or "").startswith(category):
            continue
        if service and row["service_type"] != service:
            continue
        if assigned_employee_id:
            if assigned_employee_id == "none" and row["assigned_employee_id"]:
                continue
            if assigned_employee_id != "none" and str(row["assigned_employee_id"] or "") != assigned_employee_id:
                continue
        if priority and row["priority"] != priority:
            continue
        if payment_status and row["payment_status"] != payment_status:
            continue
        if start and preferred and preferred < start:
            continue
        if end and preferred and preferred > end:
            continue
        if query and query not in haystack:
            continue
        if quick_filter == "unassigned" and row["assigned_employee_id"]:
            continue
        if quick_filter == "urgent" and row["priority"] != "urgente":
            continue
        if quick_filter == "late" and request_sla_class(row) not in {"sla-warning", "sla-danger"}:
            continue
        if quick_filter == "waiting_client" and row["status"] != "waiting_client":
            continue
        if quick_filter == "call_today" and not (preferred == today and row["status"] in {"new", "contacted", "waiting_client"}):
            continue
        if quick_filter == "missing_documents" and row["status"] not in {"documents_requested", "waiting_client"}:
            continue
        if quick_filter == "payment_check" and row["payment_status"] not in {"requested", "bank_transfer_requested", "pending_confirmation"}:
            continue
        filtered.append(row)
    return filtered


def request_board(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{"status": status, "label": status_label(status), "items": [row for row in rows if row["status"] == status]} for status in REQUEST_STATUSES]


def employee_workload(rows: list[sqlite3.Row], employees: list[sqlite3.Row]) -> list[dict[str, Any]]:
    week_start = date.today() - timedelta(days=date.today().weekday())
    workload: list[dict[str, Any]] = []
    for employee in employees:
        assigned = [row for row in rows if row["assigned_employee_id"] == employee["id"]]
        open_items = [row for row in assigned if row["status"] not in {"completed", "cancelled"}]
        completed_week = [row for row in assigned if row["status"] == "completed" and (parse_request_date(row["updated_at"]) or date.today()) >= week_start]
        workload.append(
            {
                "employee": employee,
                "assigned": len(assigned),
                "open": len(open_items),
                "completed_week": len(completed_week),
                "overloaded": len(open_items) >= 12,
            }
        )
    return workload


def fetch_request_detail_context(conn: sqlite3.Connection, request_id: int, user: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT a.*, u.full_name AS assigned_employee_name, u.email AS assigned_employee_email "
        "FROM appointments a LEFT JOIN users u ON u.id=a.assigned_employee_id WHERE a.id=?",
        (request_id,),
    ).fetchone()
    if not row or not can_manage_request(user, row):
        return None
    documents = conn.execute(
        "SELECT d.*, uploader.full_name AS uploader_name FROM appointment_documents d "
        "LEFT JOIN users uploader ON uploader.id=d.uploaded_by_user_id "
        "WHERE d.appointment_id=? ORDER BY d.id DESC",
        (request_id,),
    ).fetchall()
    checklist = conn.execute(
        "SELECT c.*, u.full_name AS completed_by_name FROM request_checklist_items c "
        "LEFT JOIN users u ON u.id=c.completed_by WHERE c.request_id=? ORDER BY c.completed, c.id",
        (request_id,),
    ).fetchall()
    requirements = conn.execute(
        "SELECT * FROM request_document_requirements WHERE request_id=? ORDER BY id",
        (request_id,),
    ).fetchall()
    comments = conn.execute(
        "SELECT c.*, u.full_name AS author_name FROM request_comments c LEFT JOIN users u ON u.id=c.author_id WHERE c.request_id=? ORDER BY c.id DESC",
        (request_id,),
    ).fetchall()
    audit_logs = conn.execute(
        "SELECT a.id, a.action, a.target_type, a.target_id, a.details, a.created_at, u.full_name AS actor "
        "FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_user_id "
        "WHERE a.target_type='appointment' AND a.target_id=? ORDER BY a.id DESC",
        (str(request_id),),
    ).fetchall()
    staff_members = fetch_staff_members(conn, include_inactive=False)
    reminder_text = generate_whatsapp_reminder(row, "confirmation")
    email_text = (
        f"Gentile {row['full_name']},\n\n"
        f"la contattiamo da ConsultIQ per la richiesta #{row['id']} relativa a "
        f"{display_service_type(row['service_type'])}.\n\n"
        "Restiamo a disposizione per completare la pratica.\n\n"
        "Cordiali saluti,\nConsultIQ Italy"
    )
    return {
        "appointment": row,
        "documents": documents,
        "checklist_items": checklist,
        "checklist_progress": checklist_progress(checklist),
        "document_requirements": requirements,
        "comments": comments,
        "timeline_events": fetch_request_timeline(conn, request_id, public_only=False),
        "audit_logs": audit_logs,
        "staff_members": staff_members,
        "whatsapp_text": reminder_text,
        "whatsapp_link": build_whatsapp_send_link(row["phone"], reminder_text) if row["phone"] else "",
        "email_text": email_text,
    }


def fetch_client_request_detail_context(conn: sqlite3.Connection, request_id: int, user: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT a.*, u.full_name AS assigned_employee_name FROM appointments a LEFT JOIN users u ON u.id=a.assigned_employee_id WHERE a.id=?",
        (request_id,),
    ).fetchone()
    if not row or not can_manage_request(user, row):
        return None
    documents = conn.execute(
        "SELECT id, original_filename, category, status, client_note, expires_at, scan_status, created_at FROM appointment_documents WHERE appointment_id=? ORDER BY id DESC",
        (request_id,),
    ).fetchall()
    requirements = conn.execute("SELECT * FROM request_document_requirements WHERE request_id=? ORDER BY id", (request_id,)).fetchall()
    public_comments = conn.execute(
        "SELECT c.*, u.full_name AS author_name FROM request_comments c LEFT JOIN users u ON u.id=c.author_id WHERE c.request_id=? AND c.is_internal=0 ORDER BY c.id DESC",
        (request_id,),
    ).fetchall()
    client_progress = {"total": len(requirements), "done": sum(1 for item in requirements if item["status"] == "received")}
    client_progress["percent"] = int((client_progress["done"] / client_progress["total"]) * 100) if client_progress["total"] else 0
    return {
        "appointment": row,
        "documents": documents,
        "document_requirements": requirements,
        "public_comments": public_comments,
        "timeline_events": fetch_request_timeline(conn, request_id, public_only=True),
        "client_progress": client_progress,
        "payment_instruction": public_payment_instruction(row),
    }


def checklist_counts_for_requests(conn: sqlite3.Connection, request_ids: list[int]) -> dict[int, dict[str, int]]:
    if not request_ids:
        return {}
    placeholders = ",".join("?" for _ in request_ids)
    rows = conn.execute(
        f"SELECT request_id, COUNT(*) AS total, SUM(CASE WHEN completed=1 THEN 1 ELSE 0 END) AS done "
        f"FROM request_checklist_items WHERE request_id IN ({placeholders}) GROUP BY request_id",
        request_ids,
    ).fetchall()
    counts = {int(row["request_id"]): {"total": int(row["total"]), "done": int(row["done"] or 0)} for row in rows}
    for request_id in request_ids:
        counts.setdefault(request_id, {"total": 0, "done": 0})
    for item in counts.values():
        item["percent"] = int((item["done"] / item["total"]) * 100) if item["total"] else 0
    return counts


def ensure_request_checklist_schema(conn: sqlite3.Connection) -> None:
    """Lightweight SQLite migration helper for production upgrades."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS request_checklist_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "request_id INTEGER NOT NULL, "
        "title TEXT NOT NULL, "
        "completed INTEGER NOT NULL DEFAULT 0, "
        "completed_by INTEGER, "
        "completed_at TEXT, "
        "created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(request_id) REFERENCES appointments(id), "
        "FOREIGN KEY(completed_by) REFERENCES users(id)"
        ")"
    )


def ensure_request_comments_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS request_comments ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "request_id INTEGER NOT NULL, "
        "author_id INTEGER, "
        "body TEXT NOT NULL, "
        "is_internal INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(request_id) REFERENCES appointments(id), "
        "FOREIGN KEY(author_id) REFERENCES users(id)"
        ")"
    )


def ensure_document_requirements_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS request_document_requirements ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "request_id INTEGER NOT NULL, "
        "title TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'missing', "
        "note TEXT NOT NULL DEFAULT '', "
        "created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TEXT DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(request_id) REFERENCES appointments(id)"
        ")"
    )


def ensure_notifications_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(notifications)").fetchall()}
    for stmt, col in [
        ("ALTER TABLE notifications ADD COLUMN target_type TEXT", "target_type"),
        ("ALTER TABLE notifications ADD COLUMN target_id TEXT", "target_id"),
        ("ALTER TABLE notifications ADD COLUMN read_at TEXT", "read_at"),
    ]:
        if col not in cols:
            conn.execute(stmt)


def ensure_manual_payment_fields(conn: sqlite3.Connection, existing_appt_cols: set[str]) -> None:
    for stmt, col in [
        ("ALTER TABLE appointments ADD COLUMN payment_method TEXT NOT NULL DEFAULT ''", "payment_method"),
        ("ALTER TABLE appointments ADD COLUMN payment_instruction TEXT NOT NULL DEFAULT ''", "payment_instruction"),
        ("ALTER TABLE appointments ADD COLUMN payment_internal_note TEXT NOT NULL DEFAULT ''", "payment_internal_note"),
    ]:
        if col not in existing_appt_cols:
            conn.execute(stmt)


def service_slug_for_request(service_type: str) -> str:
    service_item = find_service_by_service_type(service_type)
    return service_item["slug"] if service_item else ""


def document_requirements_for_service(service_type: str) -> list[str]:
    slug = service_slug_for_request(service_type)
    if slug in SERVICE_DOCUMENT_REQUIREMENTS:
        return SERVICE_DOCUMENT_REQUIREMENTS[slug]
    category = (service_type or "").split(" - ")[0].lower()
    if "immigration" in category:
        return SERVICE_DOCUMENT_REQUIREMENTS["immigration-residence-permit"]
    if "business" in category:
        return SERVICE_DOCUMENT_REQUIREMENTS["business-partita-iva"]
    if "caf" in category:
        return SERVICE_DOCUMENT_REQUIREMENTS["caf-730"]
    return COMMON_DOCUMENTS


def workflow_template_for_service(service_type: str) -> list[str]:
    slug = service_slug_for_request(service_type)
    if slug in SERVICE_WORKFLOW_TEMPLATES:
        return SERVICE_WORKFLOW_TEMPLATES[slug]
    category = (service_type or "").split(" - ")[0].lower()
    if "immigration" in category:
        return SERVICE_WORKFLOW_TEMPLATES["immigration-residence-permit"]
    if "business" in category:
        return SERVICE_WORKFLOW_TEMPLATES["business-partita-iva"]
    if "caf" in category:
        return SERVICE_WORKFLOW_TEMPLATES["caf-730"]
    return ["Verificare dati cliente", "Controllare documentazione", "Preparare pratica", "Aggiornare cliente"]


def seed_request_workflow(conn: sqlite3.Connection, request_id: int, service_type: str) -> None:
    existing_docs = conn.execute("SELECT COUNT(*) FROM request_document_requirements WHERE request_id=?", (request_id,)).fetchone()[0]
    if not existing_docs:
        conn.executemany(
            "INSERT INTO request_document_requirements (request_id, title, status) VALUES (?, ?, 'missing')",
            [(request_id, title) for title in document_requirements_for_service(service_type)],
        )
    existing_tasks = conn.execute("SELECT COUNT(*) FROM request_checklist_items WHERE request_id=?", (request_id,)).fetchone()[0]
    if not existing_tasks:
        conn.executemany(
            "INSERT INTO request_checklist_items (request_id, title) VALUES (?, ?)",
            [(request_id, title) for title in workflow_template_for_service(service_type)],
        )


def add_staff_notification(user_id: int | None, title: str, message: str, target_type: str = "appointment", target_id: str = "") -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO notifications (user_id, email, channel, title, message, status, target_type, target_id) VALUES (?, ?, 'staff', ?, ?, 'queued', ?, ?)",
            (user_id, ADMIN_NOTIFICATION_EMAIL if user_id is None else "", title, message, target_type, target_id),
        )


def fetch_staff_notifications(conn: sqlite3.Connection, user: dict[str, Any], limit: int = 20) -> list[sqlite3.Row]:
    if user["role"] in {"admin", "manager"}:
        return conn.execute(
            "SELECT * FROM notifications WHERE channel='staff' ORDER BY read_at IS NOT NULL, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM notifications WHERE channel='staff' AND user_id=? ORDER BY read_at IS NOT NULL, id DESC LIMIT ?",
        (user["id"], limit),
    ).fetchall()


def fetch_request_timeline(conn: sqlite3.Connection, request_id: int, public_only: bool = False) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    row = conn.execute("SELECT created_at, full_name FROM appointments WHERE id=?", (request_id,)).fetchone()
    if row:
        events.append({"label": "Richiesta inviata", "detail": f"Richiesta ricevuta da {row['full_name']}", "created_at": row["created_at"]})
    comments = conn.execute(
        "SELECT c.body, c.is_internal, c.created_at, u.full_name AS author FROM request_comments c LEFT JOIN users u ON u.id=c.author_id WHERE c.request_id=? ORDER BY c.id",
        (request_id,),
    ).fetchall()
    for comment in comments:
        if public_only and comment["is_internal"]:
            continue
        label = "Aggiornamento operatore" if not comment["is_internal"] else "Nota interna"
        events.append({"label": label, "detail": comment["body"], "created_at": comment["created_at"]})
    docs = conn.execute("SELECT original_filename, created_at FROM appointment_documents WHERE appointment_id=? ORDER BY id", (request_id,)).fetchall()
    for doc in docs:
        events.append({"label": "Documento caricato", "detail": doc["original_filename"], "created_at": doc["created_at"]})
    if not public_only:
        audits = conn.execute(
            "SELECT action, details, created_at FROM audit_logs WHERE target_type='appointment' AND target_id=? ORDER BY id",
            (str(request_id),),
        ).fetchall()
        for audit in audits:
            events.append({"label": audit_action_label(audit["action"]), "detail": audit_detail_label(audit["details"]), "created_at": audit["created_at"]})
    return sorted(events, key=lambda item: item["created_at"] or "")


def public_payment_instruction(row: sqlite3.Row) -> str:
    if row["payment_status"] in {"not_requested", "not_required"}:
        return "Nessuna istruzione di pagamento richiesta al momento."
    if "payment_instruction" in row.keys() and row["payment_instruction"]:
        return row["payment_instruction"]
    method = row["payment_method"] if "payment_method" in row.keys() and row["payment_method"] else "modalita' concordata con l'ufficio"
    return f"Pagamento {payment_status_label(row['payment_status']).lower()} tramite {method}. Il team ti confermera' i dettagli."


def send_email_notification(to_email: str, subject: str, body: str) -> None:
    if not SMTP_HOST:
        logger.info("EMAIL_NOT_SENT host_missing to=%s subject=%s", to_email, subject)
        raise RuntimeError("SMTP not configured")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
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


def render_email_template(template_key: str, **values: Any) -> tuple[str, str]:
    template = EMAIL_TEMPLATES[template_key]
    return template["subject"].format(**values), template["body"].format(**values)


def add_notification(user_id: int | None, email: str, channel: str, title: str, message: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO notifications (user_id, email, channel, title, message, status) VALUES (?, ?, ?, ?, ?, 'queued')", (user_id, email, channel, title, message))
        notification_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    try:
        if channel == "email":
            send_email_notification(email, title, message)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE notifications SET status='sent' WHERE id=?", (notification_id,))
    except Exception:
        logger.exception("notification_send_failed")
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE notifications SET status=? WHERE id=?", ("failed" if SMTP_HOST else "pending", notification_id))


def admin_launch_checklist() -> list[dict[str, str]]:
    return [
        {"title": "Password reset staff/operatori", "status": "Attivo", "detail": "Il recupero password funziona per clienti, operatori, responsabili e amministratori."},
        {"title": "Email SMTP", "status": "Da configurare" if not SMTP_HOST else "Configurato", "detail": "Inserire host, porta, utente e mittente nelle variabili ambiente del hosting."},
        {"title": "Migrazioni database", "status": "Protetto", "detail": "All'avvio il sistema applica CREATE TABLE IF NOT EXISTS e ALTER TABLE sicuri per SQLite."},
        {"title": "Permessi ruoli", "status": "Documentato", "detail": "La pagina permessi mostra cosa possono fare admin, responsabili, operatori e clienti."},
        {"title": "Scadenze documenti", "status": "Attivo", "detail": "Il pannello documenti in scadenza permette di creare promemoria staff e cliente."},
        {"title": "Auto-assegnazione", "status": "Disponibile", "detail": "Le richieste non assegnate possono essere distribuite al team con carico minore."},
        {"title": "Audit export", "status": "Disponibile", "detail": "Il registro audit puo' essere esportato in CSV dall'area admin."},
        {"title": "Backup e ripristino", "status": "Guidato", "detail": "La pagina backup indica percorsi e passaggi per database, upload e restore."},
        {"title": "Privacy/cookie/legale", "status": "Da validare", "detail": "I testi sono presenti ma devono essere approvati da consulente legale prima del go-live."},
        {"title": "Checklist deploy", "status": "Disponibile", "detail": "La pagina prontezza lancio riepiloga cPanel, variabili ambiente e verifiche finali."},
    ]


def database_migration_status() -> list[dict[str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        required_tables = [
            "users",
            "appointments",
            "appointment_documents",
            "audit_logs",
            "notifications",
            "password_resets",
            "request_checklist_items",
            "request_comments",
            "request_document_requirements",
        ]
        rows = [{"name": table, "status": "Presente" if table in tables else "Mancante"} for table in required_tables]
        appt_cols = {row[1] for row in conn.execute("PRAGMA table_info(appointments)").fetchall()}
        for col in ["priority", "assigned_employee_id", "payment_status", "payment_method", "payment_instruction", "updated_at"]:
            rows.append({"name": f"appointments.{col}", "status": "Presente" if col in appt_cols else "Mancante"})
    return rows


def expiring_documents(days: int = 30) -> list[sqlite3.Row]:
    today = date.today()
    limit = today + timedelta(days=days)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT d.*, a.full_name, a.email, a.service_type FROM appointment_documents d "
            "JOIN appointments a ON a.id=d.appointment_id "
            "WHERE d.expires_at IS NOT NULL AND d.expires_at!='' AND date(d.expires_at) BETWEEN date(?) AND date(?) "
            "ORDER BY date(d.expires_at), d.id DESC",
            (today.isoformat(), limit.isoformat()),
        ).fetchall()


def audit_logs_csv(rows: list[sqlite3.Row]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Operatore", "Azione", "Target", "Dettagli", "Creato"])
    for row in rows:
        writer.writerow([
            row["id"],
            row["actor"] or "Sistema",
            audit_action_label(row["action"]),
            f"{audit_target_label(row['target_type'])} #{row['target_id']}",
            audit_detail_label(row["details"]),
            row["created_at"],
        ])
    return output.getvalue()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'client', admin_totp_secret TEXT, active INTEGER NOT NULL DEFAULT 1, is_anonymized INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS appointments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL, service_type TEXT NOT NULL, preferred_date TEXT NOT NULL, message TEXT, status TEXT NOT NULL DEFAULT 'new', priority TEXT NOT NULL DEFAULT 'media', assigned_employee_id INTEGER, internal_notes TEXT NOT NULL DEFAULT '', payment_status TEXT NOT NULL DEFAULT 'not_requested', payment_note TEXT NOT NULL DEFAULT '', payment_reference TEXT, price_label TEXT NOT NULL DEFAULT 'quote_required', price_amount REAL, requested_amount REAL, privacy_accepted INTEGER NOT NULL DEFAULT 0, terms_accepted INTEGER NOT NULL DEFAULT 0, marketing_accepted INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(assigned_employee_id) REFERENCES users(id))")
        conn.execute("CREATE TABLE IF NOT EXISTS contact_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT, subject TEXT NOT NULL, message TEXT NOT NULL, privacy_accepted INTEGER NOT NULL DEFAULT 0, marketing_accepted INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS appointment_documents (id INTEGER PRIMARY KEY AUTOINCREMENT, appointment_id INTEGER NOT NULL, uploaded_by_user_id INTEGER, original_filename TEXT NOT NULL, stored_filename TEXT NOT NULL, file_path TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Altro', status TEXT NOT NULL DEFAULT 'received', client_note TEXT NOT NULL DEFAULT '', expires_at TEXT, scan_status TEXT NOT NULL DEFAULT 'not_scanned', created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(appointment_id) REFERENCES appointments(id), FOREIGN KEY(uploaded_by_user_id) REFERENCES users(id))")
        conn.execute("CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER, action TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, email TEXT NOT NULL, channel TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS password_resets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, token TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        ensure_request_checklist_schema(conn)
        ensure_request_comments_schema(conn)
        ensure_document_requirements_schema(conn)
        ensure_notifications_schema(conn)
        existing_user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_anonymized" not in existing_user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_anonymized INTEGER NOT NULL DEFAULT 0")
        if "active" not in existing_user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        existing_appt_cols = {row[1] for row in conn.execute("PRAGMA table_info(appointments)").fetchall()}
        for stmt, col in [
            ("ALTER TABLE appointments ADD COLUMN priority TEXT NOT NULL DEFAULT 'media'", "priority"),
            ("ALTER TABLE appointments ADD COLUMN assigned_employee_id INTEGER", "assigned_employee_id"),
            ("ALTER TABLE appointments ADD COLUMN internal_notes TEXT NOT NULL DEFAULT ''", "internal_notes"),
            ("ALTER TABLE appointments ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'not_requested'", "payment_status"),
            ("ALTER TABLE appointments ADD COLUMN payment_note TEXT NOT NULL DEFAULT ''", "payment_note"),
            ("ALTER TABLE appointments ADD COLUMN payment_reference TEXT", "payment_reference"),
            ("ALTER TABLE appointments ADD COLUMN price_label TEXT NOT NULL DEFAULT 'quote_required'", "price_label"),
            ("ALTER TABLE appointments ADD COLUMN price_amount REAL", "price_amount"),
            ("ALTER TABLE appointments ADD COLUMN requested_amount REAL", "requested_amount"),
            ("ALTER TABLE appointments ADD COLUMN privacy_accepted INTEGER NOT NULL DEFAULT 0", "privacy_accepted"),
            ("ALTER TABLE appointments ADD COLUMN terms_accepted INTEGER NOT NULL DEFAULT 0", "terms_accepted"),
            ("ALTER TABLE appointments ADD COLUMN marketing_accepted INTEGER NOT NULL DEFAULT 0", "marketing_accepted"),
            ("ALTER TABLE appointments ADD COLUMN updated_at TEXT", "updated_at"),
        ]:
            if col not in existing_appt_cols:
                conn.execute(stmt)
        ensure_manual_payment_fields(conn, existing_appt_cols)
        if "updated_at" in existing_appt_cols:
            conn.execute("UPDATE appointments SET updated_at=COALESCE(updated_at, created_at)")
        else:
            conn.execute("UPDATE appointments SET updated_at=created_at WHERE updated_at IS NULL")
        existing_contact_cols = {row[1] for row in conn.execute("PRAGMA table_info(contact_messages)").fetchall()}
        for stmt, col in [
            ("ALTER TABLE contact_messages ADD COLUMN privacy_accepted INTEGER NOT NULL DEFAULT 0", "privacy_accepted"),
            ("ALTER TABLE contact_messages ADD COLUMN marketing_accepted INTEGER NOT NULL DEFAULT 0", "marketing_accepted"),
        ]:
            if col not in existing_contact_cols:
                conn.execute(stmt)
        existing_doc_cols = {row[1] for row in conn.execute("PRAGMA table_info(appointment_documents)").fetchall()}
        for stmt, col in [
            ("ALTER TABLE appointment_documents ADD COLUMN category TEXT NOT NULL DEFAULT 'Altro'", "category"),
            ("ALTER TABLE appointment_documents ADD COLUMN status TEXT NOT NULL DEFAULT 'received'", "status"),
            ("ALTER TABLE appointment_documents ADD COLUMN client_note TEXT NOT NULL DEFAULT ''", "client_note"),
            ("ALTER TABLE appointment_documents ADD COLUMN expires_at TEXT", "expires_at"),
            ("ALTER TABLE appointment_documents ADD COLUMN scan_status TEXT NOT NULL DEFAULT 'not_scanned'", "scan_status"),
        ]:
            if col not in existing_doc_cols:
                conn.execute(stmt)
        admin_exists = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        if not admin_exists:
            conn.execute("INSERT INTO users (full_name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, 'admin')", ("ConsultIQ Admin", DEFAULT_ADMIN_EMAIL, "+39 02 0000 0000", hash_password(DEFAULT_ADMIN_PASSWORD)))


@app.on_event("startup")
def startup_event() -> None:
    init_db()


def render(request: Request, template: str, **context: Any):
    grouped_services = {
        category: [svc for svc in SERVICES if svc["category"] == category]
        for category in SERVICE_CATEGORIES
    }
    cookie_banner_copy = {
        "title": "Preferenze cookie",
        "warning": "I testi legali e le impostazioni finali vanno verificati da un consulente legale prima del go-live.",
    }
    return templates.TemplateResponse(
        template,
        {
            "request": request,
            "user": get_current_user(request),
            "services": SERVICES,
            "service_titles": SERVICE_TITLES,
            "service_categories": SERVICE_CATEGORIES,
            "grouped_services": grouped_services,
            "request_statuses": REQUEST_STATUSES,
            "request_priorities": REQUEST_PRIORITIES,
            "csrf_token": get_or_create_csrf_token(request),
            "cookie_banner_copy": cookie_banner_copy,
            "admin_notification_email": ADMIN_NOTIFICATION_EMAIL,
            "smtp_configured": bool(SMTP_HOST),
            "status_label": status_label,
            "payment_status_label": payment_status_label,
            "document_status_label": document_status_label,
            "notification_status_label": notification_status_label,
            "scan_status_label": scan_status_label,
            "document_requirement_status_label": lambda value: DOCUMENT_REQUIREMENT_STATUS_LABELS.get(value, value),
            "audit_action_label": audit_action_label,
            "audit_target_label": audit_target_label,
            "audit_detail_label": audit_detail_label,
            "role_label": role_label,
            "request_priority_label": request_priority_label,
            "request_area_label": request_area_label,
            "request_age_label": request_age_label,
            "request_sla_class": request_sla_class,
            "request_sla_label": request_sla_label,
            "checklist_progress": checklist_progress,
            "display_service_type": display_service_type,
            "italian_service_category": italian_service_category,
            "generate_whatsapp_reminder": generate_whatsapp_reminder,
            "build_whatsapp_send_link": build_whatsapp_send_link,
            **context,
        },
    )


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    logger.info("%s %s %s %sms", request.method, request.url.path, response.status_code, int((time.time() - start) * 1000))
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https://www.google.com https://www.gstatic.com; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-src https://www.google.com; connect-src 'self'; font-src 'self' data:;"
    return response


@app.exception_handler(404)
async def not_found_handler(request: Request, _exc: Exception):
    return templates.TemplateResponse("404.html", {"request": request, "title": "Pagina non trovata | ConsultIQ", "csrf_token": get_or_create_csrf_token(request), "user": get_current_user(request), "cookie_banner_copy": {"title": "Preferenze cookie", "warning": "I testi legali e le impostazioni finali vanno verificati da un consulente legale prima del go-live."}}, status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, _exc: Exception):
    return templates.TemplateResponse("500.html", {"request": request, "title": "Errore server | ConsultIQ", "csrf_token": get_or_create_csrf_token(request), "user": get_current_user(request), "cookie_banner_copy": {"title": "Preferenze cookie", "warning": "I testi legali e le impostazioni finali vanno verificati da un consulente legale prima del go-live."}}, status_code=500)


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "utc": datetime.now(timezone.utc).isoformat()})


@app.get("/robots.txt")
def robots_txt():
    content = "User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /client-area\nSitemap: /sitemap.xml\n"
    return PlainTextResponse(content)


@app.get("/sitemap.xml")
def sitemap_xml(request: Request):
    static_paths = ["/", "/services", "/news", "/contact", "/privacy", "/terms", "/cookie-policy", "/required-documents"]
    service_paths = [f"/services/{service['slug']}" for service in SERVICES]
    seo_paths = [f"/servizi/{slug}" for slug in SEO_LANDING_PAGES]
    urls = static_paths + service_paths + seo_paths
    base = str(request.base_url).rstrip("/")
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path in urls:
        body.append(f"<url><loc>{base}{path}</loc></url>")
    body.append("</urlset>")
    return Response("\n".join(body), media_type="application/xml")


@app.get("/")
def home(request: Request):
    featured_services = [SERVICE_BY_SLUG[slug] for slug in ["caf-730", "caf-isee", "patronato-pensione", "immigration-residence-permit", "patronato-assegno-unico", "business-partita-iva"] if slug in SERVICE_BY_SLUG]
    return render(request, "index.html", page="home", title="ConsultIQ Roma | CAF, Patronato e Servizi al Cittadino", news=NEWS_ITEMS[:2], featured_services=featured_services)


@app.get("/services")
def services_page(request: Request):
    seo_links = [{"slug": slug, "title": page["title"]} for slug, page in SEO_LANDING_PAGES.items()]
    return render(request, "services.html", page="services", title="Servizi ConsultIQ | Roma", services=SERVICES, seo_links=seo_links)


@app.get("/services/{slug}")
def service_detail(request: Request, slug: str, booked: int = 0):
    service = SERVICE_BY_SLUG.get(slug)
    if not service:
        return RedirectResponse(url="/services", status_code=303)
    related = [s for s in SERVICES if s["category"] == service["category"] and s["slug"] != slug][:3]
    whatsapp_link = build_whatsapp_link(f"{service['category']} - {service['title']}")
    meta_description = f"{service['display_title']} a Roma con sede in {CONSULTIQ_ADDRESS}. {service['display_summary']}"
    breadcrumbs = [("Home", "/"), ("Servizi", "/services"), (service["display_title"], f"/services/{service['slug']}")]
    return render(request, "service_detail.html", page="services", title=f"{service['display_title']} | ConsultIQ Roma", service=service, related=related, booked=booked, whatsapp_link=whatsapp_link, meta_description=meta_description, breadcrumbs=breadcrumbs)


@app.get("/servizi/{landing_slug}")
def seo_service_page(request: Request, landing_slug: str):
    page = SEO_LANDING_PAGES.get(landing_slug)
    if not page:
        return RedirectResponse(url="/services", status_code=303)
    service_item = SERVICE_BY_SLUG[page["service_slug"]]
    related = [s for s in SERVICES if s["category"] == service_item["category"] and s["slug"] != service_item["slug"]][:4]
    whatsapp_link = build_whatsapp_link(f"{service_item['category']} - {service_item['title']}")
    structured_data = json.dumps(
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "LocalBusiness",
                    "name": "Consultiq Italy",
                    "address": {
                        "@type": "PostalAddress",
                        "streetAddress": "Via Licinio Murena 32-34",
                        "addressLocality": "Roma",
                        "addressCountry": "IT",
                    },
                    "telephone": CONSULTIQ_PHONE,
                    "email": CONSULTIQ_EMAIL,
                },
                {
                    "@type": "Service",
                    "name": page["title"],
                    "provider": {"@type": "LocalBusiness", "name": "Consultiq Italy"},
                    "description": page["description"],
                    "areaServed": "Roma",
                },
            ],
        }
    )
    breadcrumbs = [("Home", "/"), ("Servizi", "/services"), (page["title"], f"/servizi/{landing_slug}")]
    return render(
        request,
        "seo_service.html",
        page="services",
        title=f"{page['title']} | ConsultIQ Roma",
        meta_description=page["description"],
        og_description=page["description"],
        service=service_item,
        landing=page,
        related=related,
        whatsapp_link=whatsapp_link,
        structured_data=structured_data,
        breadcrumbs=breadcrumbs,
    )


@app.post("/services/{slug}/book")
def book_service_detail(request: Request, slug: str, csrf_token: str = Form(...), full_name: str = Form(...), email: str = Form(...), phone: str = Form(...), preferred_date: str = Form(""), message: str = Form(""), privacy_accept: str = Form(...), terms_accept: str = Form(...), marketing_accept: str = Form("0"), document: UploadFile | None = File(default=None)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "book_service_detail", 20, 300)
    enforce_required_consents(privacy_accept, terms_accept)
    service = SERVICE_BY_SLUG.get(slug)
    if not service:
        return RedirectResponse(url="/services", status_code=303)
    validate_upload(document)
    user = get_current_user(request)
    user_id = user["id"] if user else None
    service_type = f"{service['category']} - {service['title']}"
    clean_date = preferred_date.strip() or date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO appointments (user_id, full_name, email, phone, service_type, preferred_date, message, status, priority, payment_status, price_label, price_amount, privacy_accepted, terms_accepted, marketing_accepted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                full_name.strip(),
                email.strip(),
                phone.strip(),
                service_type,
                clean_date,
                message.strip(),
                "new",
                "media",
                "not_requested",
                service["price_type"],
                service["price_amount"],
                int(is_truthy(privacy_accept)),
                int(is_truthy(terms_accept)),
                int(is_truthy(marketing_accept)),
            ),
        )
        appointment_id = cur.lastrowid
        seed_request_workflow(conn, appointment_id, service_type)
        if document and document.filename:
            save_appointment_document(conn, appointment_id, user_id, document)
    add_audit_log(user_id, "service_booked", "appointment", str(appointment_id), service_type)
    subject, body = render_email_template("client_booking_confirmation", service=service["display_category"] + " - " + service["display_title"])
    add_notification(user_id, email.strip(), "email", subject, body)
    admin_subject, admin_body = render_email_template("admin_new_booking", client_name=full_name.strip(), service=service["display_category"] + " - " + service["display_title"], preferred_date=clean_date)
    add_notification(None, ADMIN_NOTIFICATION_EMAIL, "email", admin_subject, admin_body)
    add_staff_notification(None, "Nuova richiesta ricevuta", f"{full_name.strip()} - {service['display_category']} - {service['display_title']}", "appointment", str(appointment_id))
    return RedirectResponse(url=f"/services/{slug}?booked=1", status_code=303)


@app.get("/news")
def news_page(request: Request):
    return render(request, "news.html", page="news", title="Novita' | ConsultIQ", news=NEWS_ITEMS)


@app.get("/required-documents")
def required_documents(request: Request):
    return render(request, "required_documents.html", page="documents", title="Documenti richiesti | ConsultIQ", services=SERVICES)


@app.get("/privacy")
def privacy_page(request: Request):
    return render(request, "policy.html", page="legal", title="Informativa Privacy | ConsultIQ", heading="Informativa Privacy", policy_type="privacy")


@app.get("/cookie-policy")
def cookie_page(request: Request):
    return render(request, "policy.html", page="legal", title="Cookie Policy | ConsultIQ", heading="Cookie Policy", policy_type="cookies")


@app.get("/gdpr")
def gdpr_page(request: Request):
    return render(request, "policy.html", page="legal", title="Nota GDPR | ConsultIQ", heading="Nota GDPR", policy_type="gdpr")


@app.get("/legal")
def legal_page(request: Request):
    return render(request, "policy.html", page="legal", title="Termini e Condizioni | ConsultIQ", heading="Termini e Condizioni", policy_type="terms")


@app.get("/terms")
def terms_page(request: Request):
    return render(request, "policy.html", page="legal", title="Termini e Condizioni | ConsultIQ", heading="Termini e Condizioni", policy_type="terms")


@app.get("/client-area")
def client_area(request: Request):
    current_user = get_current_user(request)
    if not current_user:
        return RedirectResponse(url="/login", status_code=303)
    if current_user.get("role") == "admin":
        return RedirectResponse(url="/admin", status_code=303)
    user = require_client(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        appointments = fetch_requests_for_user(conn, user)
        docs = conn.execute("SELECT d.id, d.appointment_id, d.original_filename, d.status, d.category, d.client_note, d.expires_at, d.scan_status, d.created_at FROM appointment_documents d JOIN appointments a ON a.id=d.appointment_id WHERE a.user_id=? OR a.email=? ORDER BY d.id DESC", (user["id"], user["email"])).fetchall()
        notifications = conn.execute("SELECT id, title, message, status, created_at FROM notifications WHERE user_id=? OR email=? ORDER BY id DESC LIMIT 30", (user["id"], user["email"])).fetchall()
    expiring_docs = {
        "30": sum(1 for d in docs if d["expires_at"] and 0 <= (datetime.fromisoformat(d["expires_at"]).date() - datetime.now().date()).days <= 30),
        "60": sum(1 for d in docs if d["expires_at"] and 0 <= (datetime.fromisoformat(d["expires_at"]).date() - datetime.now().date()).days <= 60),
        "90": sum(1 for d in docs if d["expires_at"] and 0 <= (datetime.fromisoformat(d["expires_at"]).date() - datetime.now().date()).days <= 90),
    }
    kpis = {
        "total_appointments": len(appointments),
        "completed_count": sum(1 for a in appointments if a["status"] == "completed"),
        "pending_count": sum(1 for a in appointments if a["status"] in {"new", "contacted", "documents_requested", "documents_received", "in_progress", "waiting_client"}),
        "upload_count": len(docs),
    }
    return render(request, "client_area.html", page="client", title="Area cliente | ConsultIQ", appointments=appointments, documents=docs, notifications=notifications, client_kpis=kpis, expiring_docs=expiring_docs)


@app.get("/client/requests/{request_id}")
def client_request_detail(request_id: int, request: Request):
    user = require_client(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        context = fetch_client_request_detail_context(conn, request_id, user)
    if not context:
        raise HTTPException(status_code=403, detail="Accesso non autorizzato")
    return render(request, "client_request_detail.html", page="client", title=f"Richiesta #{request_id} | ConsultIQ", **context)


@app.post("/client-area/appointments/{appointment_id}/upload")
def upload_appointment_document(request: Request, appointment_id: int, csrf_token: str = Form(...), privacy_accept: str = Form(...), marketing_accept: str = Form("0"), category: str = Form("Altro"), expires_at: str = Form(""), document: UploadFile = File(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "upload_appointment_document", 25, 300)
    enforce_required_consents(privacy_accept)
    user = require_client(request)
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
        save_appointment_document(conn, appointment_id, user["id"], document)
        conn.execute("UPDATE appointment_documents SET category=?, expires_at=? WHERE id=(SELECT MAX(id) FROM appointment_documents WHERE appointment_id=?)", (category if category in DOCUMENT_CATEGORIES else "Altro", expires_at.strip() or None, appointment_id))
    add_audit_log(user["id"], "document_uploaded", "appointment", str(appointment_id), document.filename or "document")
    with sqlite3.connect(DB_PATH) as notify_conn:
        notify_conn.row_factory = sqlite3.Row
        appt = notify_conn.execute("SELECT assigned_employee_id FROM appointments WHERE id=?", (appointment_id,)).fetchone()
    add_staff_notification(appt["assigned_employee_id"] if appt and appt["assigned_employee_id"] else None, "Documento caricato", f"Nuovo documento su richiesta #{appointment_id}: {document.filename}", "appointment", str(appointment_id))
    subject, body = render_email_template("document_received_confirmation", document_name=document.filename or "document", appointment_id=appointment_id)
    add_notification(user["id"], user["email"], "email", subject, body)
    return RedirectResponse(url="/client-area", status_code=303)


@app.get("/documents/{document_id}/download")
def download_document(document_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT d.id, d.original_filename, d.stored_filename, a.user_id, a.email, a.assigned_employee_id FROM appointment_documents d JOIN appointments a ON a.id=d.appointment_id WHERE d.id=?",
            (document_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    owns = user["role"] in {"admin", "manager"} or row["assigned_employee_id"] == user["id"] or row["user_id"] == user["id"] or row["email"] == user["email"]
    if not owns:
        raise HTTPException(status_code=403, detail="Forbidden")
    file_path = UPLOAD_DIR / row["stored_filename"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing")
    add_audit_log(user["id"], "document_viewed", "document", str(document_id), row["original_filename"])
    return Response(content=file_path.read_bytes(), media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename=\"{row['original_filename']}\""})


@app.get("/appointments/{appointment_id}/invite")
def appointment_invite(appointment_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if not can_manage_request(user, row):
        raise HTTPException(status_code=403, detail="Forbidden")
    ics = generate_ics_for_appointment(row)
    return Response(content=ics, media_type="text/calendar", headers={"Content-Disposition": f"attachment; filename=\"appointment-{appointment_id}.ics\""})


@app.get("/admin")
def admin(request: Request, status: str = "", category: str = "", service: str = "", assigned_employee_id: str = "", priority: str = "", payment_status: str = "", date_from: str = "", date_to: str = "", search: str = "", quick_filter: str = ""):
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        all_appointments = fetch_requests_for_user(conn, user)
        appointments = filter_request_rows(all_appointments, status=status, category=category, service=service, assigned_employee_id=assigned_employee_id, priority=priority, payment_status=payment_status, date_from=date_from, date_to=date_to, search=search, quick_filter=quick_filter)[:250]
        contacts = conn.execute("SELECT * FROM contact_messages ORDER BY id DESC LIMIT 200").fetchall()
        users = conn.execute("SELECT id, full_name, email, role, active, created_at FROM users ORDER BY id DESC").fetchall()
        employees = fetch_staff_members(conn, include_inactive=True)
        active_employees = [employee for employee in employees if employee["active"]]
        documents = conn.execute("SELECT d.id, d.appointment_id, d.original_filename, d.file_path, d.category, d.status, d.client_note, d.expires_at, d.scan_status, d.created_at, u.full_name AS uploader FROM appointment_documents d LEFT JOIN users u ON u.id=d.uploaded_by_user_id ORDER BY d.id DESC LIMIT ?", (DASHBOARD_DOCUMENT_LIMIT,)).fetchall()
        notifications = conn.execute("SELECT id, email, channel, title, status, created_at FROM notifications ORDER BY id DESC LIMIT 100").fetchall()
        audit_logs = conn.execute("SELECT a.id, a.action, a.target_type, a.target_id, a.details, a.created_at, u.full_name AS actor FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_user_id ORDER BY a.id DESC LIMIT ?", (DASHBOARD_AUDIT_LIMIT,)).fetchall()
    status_counts = {key: 0 for key in REQUEST_STATUSES}
    for a in appointments:
        if a["status"] in status_counts:
            status_counts[a["status"]] += 1
    payment_pending_total = sum(1 for a in appointments if a["payment_status"] in {"bank_transfer_requested", "pending_confirmation"})
    revenue_total = round(sum(float(a["requested_amount"] or a["price_amount"] or 0) for a in appointments if a["payment_status"] == "paid"), 2)
    analytics = compute_admin_analytics(appointments, contacts, documents)
    unassigned = [a for a in all_appointments if not a["assigned_employee_id"] and a["status"] not in {"completed", "cancelled"}]
    urgent = [a for a in all_appointments if a["priority"] == "urgente" and a["status"] not in {"completed", "cancelled"}]
    today_priority = [a for a in all_appointments if a["priority"] == "urgente" or request_sla_class(a) in {"sla-warning", "sla-danger"}][:8]
    recent_activity = audit_logs[:8]
    workload = employee_workload(all_appointments, active_employees)
    overdue_requests = [a for a in all_appointments if request_sla_status(a) == "overdue" and a["status"] not in {"completed", "cancelled"}]
    sla_open = [a for a in all_appointments if a["status"] not in {"completed", "cancelled"}]
    sla_ok = [a for a in sla_open if request_sla_status(a) == "ok"]
    board = request_board(appointments)
    admin_sections = {
        "nuove": [a for a in appointments if a["status"] == "new"][:DASHBOARD_SECTION_LIMIT],
        "non-assegnate": [a for a in appointments if not a["assigned_employee_id"]][:DASHBOARD_SECTION_LIMIT],
        "lavorazione": [a for a in appointments if a["status"] == "in_progress"][:DASHBOARD_SECTION_LIMIT],
        "completate": [a for a in appointments if a["status"] == "completed"][:DASHBOARD_SECTION_LIMIT],
    }
    grouped_requests = {
        "caf": [a for a in appointments if (a["service_type"] or "").startswith("CAF")][:12],
        "patronato": [a for a in appointments if (a["service_type"] or "").startswith("Patronato")][:12],
        "immigrazione": [a for a in appointments if (a["service_type"] or "").startswith("Immigration")][:12],
        "business": [a for a in appointments if (a["service_type"] or "").startswith("Business")][:12],
    }
    kpis = {"appointments_total": len(appointments), "contacts_total": len(contacts), "users_total": len(users), "documents_total": len(documents), "new_total": status_counts["new"], "in_progress_total": status_counts["in_progress"], "completed_total": status_counts["completed"], "cancelled_total": status_counts["cancelled"], "pending_payments_total": payment_pending_total, "revenue_total": revenue_total, "unassigned_total": len(unassigned), "urgent_total": len(urgent), "overdue_total": len(overdue_requests), "unassigned_over_24h": sum(1 for a in unassigned if request_age_days(a) >= 1), "waiting_client_over_3d": sum(1 for a in all_appointments if a["status"] == "waiting_client" and request_age_days(a) > 3), "sla_compliance": round((len(sla_ok) / max(len(sla_open), 1)) * 100, 1), **analytics}
    filters = {"status": status, "category": category, "service": service, "assigned_employee_id": assigned_employee_id, "priority": priority, "payment_status": payment_status, "date_from": date_from, "date_to": date_to, "search": search, "quick_filter": quick_filter}
    return render(request, "admin_dashboard.html", page="admin", title="Dashboard amministrativa | ConsultIQ", appointments=appointments, contacts=contacts, users=users, employees=employees, active_employees=active_employees, employee_workload=workload, request_board=board, admin_sections=admin_sections, today_priority=today_priority, recent_activity=recent_activity, grouped_requests=grouped_requests, unassigned_requests=unassigned[:DASHBOARD_SECTION_LIMIT], urgent_requests=urgent[:DASHBOARD_SECTION_LIMIT], overdue_requests=overdue_requests[:DASHBOARD_SECTION_LIMIT], documents=documents, notifications=notifications, audit_logs=audit_logs, admin_kpis=kpis, admin_filters=filters, dashboard_section_limit=DASHBOARD_SECTION_LIMIT, dashboard_document_limit=DASHBOARD_DOCUMENT_LIMIT, dashboard_audit_limit=DASHBOARD_AUDIT_LIMIT, smtp_warning=not SMTP_HOST, document_categories=DOCUMENT_CATEGORIES, document_statuses=DOCUMENT_STATUSES, payment_statuses=PAYMENT_STATUSES)


@app.get("/admin/requests/{request_id}")
def admin_request_detail(request_id: int, request: Request):
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        context = fetch_request_detail_context(conn, request_id, user)
    if not context:
        raise HTTPException(status_code=404, detail="Richiesta non trovata")
    return render(request, "request_detail.html", page="admin", title=f"Richiesta #{request_id} | ConsultIQ", staff_mode="admin", **context)


@app.get("/employee/requests/{request_id}")
def employee_request_detail(request_id: int, request: Request):
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    if user["role"] in {"admin", "manager"}:
        return RedirectResponse(url=f"/admin/requests/{request_id}", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        context = fetch_request_detail_context(conn, request_id, user)
    if not context:
        raise HTTPException(status_code=403, detail="Accesso non autorizzato")
    return render(request, "request_detail.html", page="admin", title=f"Richiesta assegnata #{request_id} | ConsultIQ", staff_mode="employee", **context)


@app.post("/requests/{request_id}/checklist")
def create_checklist_item(request_id: int, request: Request, csrf_token: str = Form(...), title: str = Form(...), next_url: str = Form("")):
    enforce_csrf(request, csrf_token)
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM appointments WHERE id=?", (request_id,)).fetchone()
        if not row or not can_manage_request(user, row):
            raise HTTPException(status_code=403, detail="Accesso non autorizzato")
        clean_title = title.strip()
        if clean_title:
            conn.execute("INSERT INTO request_checklist_items (request_id, title) VALUES (?, ?)", (request_id, clean_title))
    if clean_title:
        add_audit_log(user["id"], "checklist_item_created", "appointment", str(request_id), clean_title)
    fallback = f"/admin/requests/{request_id}" if user["role"] in {"admin", "manager"} else f"/employee/requests/{request_id}"
    return RedirectResponse(url=safe_next_url(next_url or fallback, fallback), status_code=303)


@app.post("/requests/{request_id}/checklist/{item_id}/toggle")
def toggle_checklist_item(request_id: int, item_id: int, request: Request, csrf_token: str = Form(...), completed: str = Form("0"), next_url: str = Form("")):
    enforce_csrf(request, csrf_token)
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    done = 1 if completed == "1" else 0
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM appointments WHERE id=?", (request_id,)).fetchone()
        item = conn.execute("SELECT * FROM request_checklist_items WHERE id=? AND request_id=?", (item_id, request_id)).fetchone()
        if not row or not item or not can_manage_request(user, row):
            raise HTTPException(status_code=403, detail="Accesso non autorizzato")
        conn.execute(
            "UPDATE request_checklist_items SET completed=?, completed_by=?, completed_at=? WHERE id=?",
            (done, user["id"] if done else None, datetime.now(timezone.utc).isoformat() if done else None, item_id),
        )
    add_audit_log(user["id"], "checklist_item_updated", "appointment", str(request_id), "completed" if done else "pending")
    fallback = f"/admin/requests/{request_id}" if user["role"] in {"admin", "manager"} else f"/employee/requests/{request_id}"
    return RedirectResponse(url=safe_next_url(next_url or fallback, fallback), status_code=303)


@app.post("/requests/{request_id}/comments")
def create_request_comment(request_id: int, request: Request, csrf_token: str = Form(...), body: str = Form(...), visibility: str = Form("internal"), next_url: str = Form("")):
    enforce_csrf(request, csrf_token)
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM appointments WHERE id=?", (request_id,)).fetchone()
        if not row or not can_manage_request(user, row):
            raise HTTPException(status_code=403, detail="Accesso non autorizzato")
        clean_body = body.strip()
        is_internal = 0 if visibility == "public" else 1
        if clean_body:
            conn.execute("INSERT INTO request_comments (request_id, author_id, body, is_internal) VALUES (?, ?, ?, ?)", (request_id, user["id"], clean_body, is_internal))
    if clean_body:
        add_audit_log(user["id"], "internal_note_added" if is_internal else "client_update_added", "appointment", str(request_id), "internal_note" if is_internal else "client_update")
        add_staff_notification(row["assigned_employee_id"] if row["assigned_employee_id"] else None, "Nota aggiunta", f"Aggiornamento sulla richiesta #{request_id}", "appointment", str(request_id))
    fallback = f"/admin/requests/{request_id}" if user["role"] in {"admin", "manager"} else f"/employee/requests/{request_id}"
    return RedirectResponse(url=safe_next_url(next_url or fallback, fallback), status_code=303)


@app.post("/requests/{request_id}/document-requirements/{item_id}")
def update_document_requirement(request_id: int, item_id: int, request: Request, csrf_token: str = Form(...), status: str = Form("missing"), note: str = Form(""), next_url: str = Form("")):
    enforce_csrf(request, csrf_token)
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    clean_status = status if status in DOCUMENT_REQUIREMENT_STATUS_LABELS else "missing"
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM appointments WHERE id=?", (request_id,)).fetchone()
        if not row or not can_manage_request(user, row):
            raise HTTPException(status_code=403, detail="Accesso non autorizzato")
        conn.execute("UPDATE request_document_requirements SET status=?, note=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND request_id=?", (clean_status, note.strip(), item_id, request_id))
    add_audit_log(user["id"], "document_requirement_updated", "appointment", str(request_id), clean_status)
    fallback = f"/admin/requests/{request_id}" if user["role"] in {"admin", "manager"} else f"/employee/requests/{request_id}"
    return RedirectResponse(url=safe_next_url(next_url or fallback, fallback), status_code=303)


@app.get("/staff/notifications")
def staff_notifications(request: Request):
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        notifications = fetch_staff_notifications(conn, user, limit=100)
    return render(request, "staff_notifications.html", page="admin", title="Notifiche staff | ConsultIQ", staff_notifications=notifications)


@app.post("/staff/notifications/{notification_id}/read")
def mark_staff_notification_read(notification_id: int, request: Request, csrf_token: str = Form(...)):
    enforce_csrf(request, csrf_token)
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        if user["role"] in {"admin", "manager"}:
            conn.execute("UPDATE notifications SET read_at=CURRENT_TIMESTAMP, status='sent' WHERE id=? AND channel='staff'", (notification_id,))
        else:
            conn.execute("UPDATE notifications SET read_at=CURRENT_TIMESTAMP, status='sent' WHERE id=? AND channel='staff' AND user_id=?", (notification_id, user["id"]))
    return RedirectResponse(url="/staff/notifications", status_code=303)


@app.get("/employee/dashboard")
def employee_dashboard(request: Request, status: str = "", category: str = "", service: str = "", priority: str = "", payment_status: str = "", date_from: str = "", date_to: str = "", search: str = "", quick_filter: str = ""):
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    if user["role"] in {"admin", "manager"}:
        return RedirectResponse(url="/admin", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        all_appointments = fetch_requests_for_user(conn, user)
        appointments = filter_request_rows(all_appointments, status=status, category=category, service=service, priority=priority, payment_status=payment_status, date_from=date_from, date_to=date_to, search=search, quick_filter=quick_filter)
        documents = conn.execute(
            "SELECT d.id, d.appointment_id, d.original_filename, d.status, d.category, d.expires_at, d.created_at "
            "FROM appointment_documents d JOIN appointments a ON a.id=d.appointment_id "
            "WHERE a.assigned_employee_id=? ORDER BY d.id DESC LIMIT 120",
            (user["id"],),
        ).fetchall()
        checklist_counts = checklist_counts_for_requests(conn, [row["id"] for row in appointments])
    kpis = {
        "assigned_total": len(all_appointments),
        "new_total": sum(1 for a in all_appointments if a["status"] == "new"),
        "working_total": sum(1 for a in all_appointments if a["status"] in {"contacted", "documents_requested", "documents_received", "in_progress", "waiting_client"}),
        "completed_total": sum(1 for a in all_appointments if a["status"] == "completed"),
        "urgent_total": sum(1 for a in all_appointments if a["priority"] == "urgente"),
        "today_followups": sum(1 for a in all_appointments if parse_request_date(a["preferred_date"]) == date.today()),
        "completed_week": employee_workload(all_appointments, [user])[0]["completed_week"] if all_appointments else 0,
    }
    filters = {"status": status, "category": category, "service": service, "priority": priority, "payment_status": payment_status, "date_from": date_from, "date_to": date_to, "search": search, "quick_filter": quick_filter}
    priority_requests = [a for a in all_appointments if a["priority"] == "urgente" and a["status"] not in {"completed", "cancelled"}][:6]
    return render(request, "employee_dashboard.html", page="admin", title="Dashboard operatore | ConsultIQ", appointments=appointments, documents=documents, employee_kpis=kpis, employee_filters=filters, priority_requests=priority_requests, checklist_counts=checklist_counts, payment_statuses=PAYMENT_STATUSES)


@app.get("/admin/calendar")
def admin_calendar(request: Request, view: str = "month", status: str = "", category: str = "", day: str = "", appointment_id: int | None = None):
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    reference = datetime.fromisoformat(day).date() if day else date.today()
    if view not in {"day", "week", "month"}:
        view = "month"
    start, end = calendar_period(view, reference)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        appointments = list(reversed(fetch_requests_for_user(conn, user)))
        staff_members = fetch_staff_members(conn, include_inactive=False)
    filtered = [
        a for a in appointments
        if (not status or a["status"] == status)
        and (not category or (a["service_type"] or "").startswith(category))
        and a["preferred_date"]
        and start <= datetime.fromisoformat(a["preferred_date"]).date() <= end
    ]
    calendar_items = calendar_cells(start, end, filtered)
    selected = next((a for a in filtered if appointment_id and a["id"] == appointment_id), None) or (filtered[0] if filtered else None)
    selected_whatsapp = {}
    if selected:
        for reminder_type in sorted(WHATSAPP_REMINDER_TYPES):
            reminder_text = generate_whatsapp_reminder(selected, reminder_type)
            selected_whatsapp[reminder_type] = {
                "text": reminder_text,
                "copy": reminder_text,
                "link": build_whatsapp_send_link(selected["phone"], reminder_text),
            }
    prev_day = shift_calendar_reference(view, reference, -1).isoformat()
    next_day = shift_calendar_reference(view, reference, 1).isoformat()
    return render(
        request,
        "admin_calendar.html",
        page="admin",
        title="Calendario Appuntamenti | ConsultIQ",
        calendar_view=view,
        selected_status=status,
        selected_category=category,
        selected_day=reference.isoformat(),
        calendar_items=calendar_items,
        appointments=filtered,
        selected_appointment=selected,
        selected_whatsapp=selected_whatsapp,
        payment_statuses=PAYMENT_STATUSES,
        staff_members=staff_members,
        prev_day=prev_day,
        next_day=next_day,
    )


@app.get("/admin/analytics")
def admin_analytics(request: Request, date_from: str = "", date_to: str = "", category: str = "", status: str = "", payment_status: str = ""):
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        appointments = fetch_requests_for_user(conn, user)
        contacts = conn.execute("SELECT * FROM contact_messages ORDER BY id DESC").fetchall()
        documents = conn.execute("SELECT * FROM appointment_documents ORDER BY id DESC").fetchall()
    filtered = filter_appointments(appointments, date_from=date_from, date_to=date_to, category=category, status=status, payment_status=payment_status)
    analytics = compute_admin_analytics(filtered, contacts, documents)
    totals = analytics_totals(filtered)
    filters = {"date_from": date_from, "date_to": date_to, "category": category, "status": status, "payment_status": payment_status}
    return render(request, "admin_analytics.html", page="admin", title="Analisi e KPI | ConsultIQ", analytics=analytics, filtered_appointments=filtered[:100], analytics_totals=totals, analytics_filters=filters, payment_statuses=PAYMENT_STATUSES)


@app.get("/admin/analytics/export")
def admin_analytics_export(request: Request, mode: str = "appointments", date_from: str = "", date_to: str = "", category: str = "", status: str = "", payment_status: str = ""):
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        appointments = fetch_requests_for_user(conn, user)
    filtered = filter_appointments(appointments, date_from=date_from, date_to=date_to, category=category, status=status, payment_status=payment_status)
    content = analytics_csv(filtered, analytics_totals(filtered), mode=mode if mode in {"appointments", "summary"} else "appointments")
    filename = "analytics-summary.csv" if mode == "summary" else "analytics-appointments.csv"
    return Response(content=content, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=\"{filename}\""})


@app.get("/admin/analytics/report")
def admin_analytics_report(request: Request, date_from: str = "", date_to: str = "", category: str = "", status: str = "", payment_status: str = ""):
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        appointments = fetch_requests_for_user(conn, user)
        contacts = conn.execute("SELECT * FROM contact_messages ORDER BY id DESC").fetchall()
        documents = conn.execute("SELECT * FROM appointment_documents ORDER BY id DESC").fetchall()
    filtered = filter_appointments(appointments, date_from=date_from, date_to=date_to, category=category, status=status, payment_status=payment_status)
    analytics = compute_admin_analytics(filtered, contacts, documents)
    totals = analytics_totals(filtered)
    html = ["<html lang='it'><head><meta charset='utf-8'><title>Report ConsultIQ</title><style>body{font-family:Arial,sans-serif;margin:32px;color:#123}table{border-collapse:collapse;width:100%;margin-top:16px}th,td{border:1px solid #ccd;padding:8px;text-align:left}h1,h2{margin-bottom:8px}</style></head><body>"]
    html.append("<h1>Report operativo ConsultIQ</h1>")
    html.append(f"<p>Sede: {CONSULTIQ_ADDRESS}</p>")
    report_labels = {
        "appointments": "Totale richieste",
        "completed": "Pratiche completate",
        "pending": "Pratiche in gestione",
        "cancelled": "Pratiche annullate",
        "paid": "Pagamenti registrati",
        "unpaid_pending": "Pagamenti in attesa",
        "estimated_revenue": "Ricavo stimato",
    }
    html.append("<h2>Totali</h2><table><tbody>")
    for key, value in totals.items():
        html.append(f"<tr><th>{report_labels.get(key, key)}</th><td>{value}</td></tr>")
    html.append("</tbody></table><h2>Richieste filtrate</h2><table><thead><tr><th>ID</th><th>Cliente</th><th>Servizio</th><th>Data</th><th>Stato</th><th>Pagamento</th></tr></thead><tbody>")
    for appointment in filtered[:150]:
        html.append(f"<tr><td>{appointment['id']}</td><td>{appointment['full_name']}</td><td>{display_service_type(appointment['service_type'])}</td><td>{appointment['preferred_date']}</td><td>{status_label(appointment['status'])}</td><td>{payment_status_label(appointment['payment_status'])}</td></tr>")
    if not filtered:
        html.append("<tr><td colspan='6'>Nessuna richiesta per i filtri selezionati.</td></tr>")
    html.append("</tbody></table>")
    html.append(f"<p>Richieste del mese: {analytics['appointments_month']} | Ricavi del mese: {analytics['revenue_this_month']}</p>")
    html.append("</body></html>")
    return HTMLResponse("".join(html))


@app.get("/admin/team-performance")
def admin_team_performance(request: Request):
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        employees = fetch_staff_members(conn, include_inactive=False)
        requests = fetch_requests_for_user(conn, user)
    rows = []
    for employee in employees:
        assigned = [r for r in requests if r["assigned_employee_id"] == employee["id"]]
        open_items = [r for r in assigned if r["status"] not in {"completed", "cancelled"}]
        completed_week = [r for r in assigned if r["status"] == "completed" and (parse_request_date(r["updated_at"]) or today) >= week_start]
        completed_month = [r for r in assigned if r["status"] == "completed" and (parse_request_date(r["updated_at"]) or today) >= month_start]
        overdue = [r for r in open_items if request_sla_status(r) == "overdue"]
        completed_durations = [max(((parse_request_date(r["updated_at"]) or today) - (parse_request_date(r["created_at"]) or today)).days, 0) for r in completed_month]
        rows.append({
            "employee": employee,
            "open": len(open_items),
            "completed_week": len(completed_week),
            "completed_month": len(completed_month),
            "avg_completion_days": round(sum(completed_durations) / max(len(completed_durations), 1), 1) if completed_durations else 0,
            "overdue": len(overdue),
            "overloaded": len(open_items) >= 12,
        })
    return render(request, "team_performance.html", page="admin", title="Performance team | ConsultIQ", performance_rows=rows)


@app.get("/admin/launch-readiness")
def admin_launch_readiness(request: Request):
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    backup_paths = [
        {"label": "Database SQLite", "path": str(DB_PATH)},
        {"label": "Upload documenti", "path": str(UPLOAD_DIR)},
        {"label": "Log applicativi", "path": str(DATA_DIR / "logs")},
    ]
    deploy_steps = [
        "Pull dal repository GitHub nel cPanel.",
        "Impostare variabili ambiente da .env.example senza salvare segreti nel repository.",
        "Installare requirements.txt nell'app Python del hosting.",
        "Verificare permessi scrittura su app/data e app/data/uploads.",
        "Aprire /health e poi verificare login admin, client e operatore.",
        "Programmare backup giornaliero di database e uploads.",
    ]
    legal_steps = [
        "Validare Privacy Policy, Cookie Policy e Termini con consulente legale.",
        "Confermare titolare trattamento, PEC, retention e procedura data breach.",
        "Verificare cookie reali e strumenti analytics prima del consenso.",
    ]
    return render(
        request,
        "admin_launch_readiness.html",
        page="admin",
        title="Prontezza lancio | ConsultIQ",
        checklist=admin_launch_checklist(),
        migration_rows=database_migration_status(),
        backup_paths=backup_paths,
        deploy_steps=deploy_steps,
        legal_steps=legal_steps,
        smtp_warning=not SMTP_HOST,
    )


@app.get("/admin/permissions")
def admin_permissions(request: Request):
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    return render(request, "admin_permissions.html", page="admin", title="Permessi ruoli | ConsultIQ", permission_matrix=ROLE_PERMISSION_MATRIX)


@app.get("/admin/document-reminders")
def admin_document_reminders(request: Request, days: int = 30):
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    safe_days = days if days in {30, 60, 90} else 30
    return render(request, "admin_document_reminders.html", page="admin", title="Promemoria documenti | ConsultIQ", documents=expiring_documents(safe_days), selected_days=safe_days)


@app.post("/admin/document-reminders/{document_id}/notify")
def admin_document_reminder_notify(document_id: int, request: Request, csrf_token: str = Form(...), next_url: str = Form("/admin/document-reminders")):
    enforce_csrf(request, csrf_token)
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT d.*, a.user_id, a.full_name, a.email, a.service_type FROM appointment_documents d "
            "JOIN appointments a ON a.id=d.appointment_id WHERE d.id=?",
            (document_id,),
        ).fetchone()
    if row:
        title = "Documento in scadenza ConsultIQ"
        message = f"Gentile {row['full_name']}, il documento {row['original_filename']} collegato alla pratica {display_service_type(row['service_type'])} risulta in scadenza il {row['expires_at']}. Contatta ConsultIQ o carica un aggiornamento nell'area cliente."
        add_notification(row["user_id"], row["email"], "email", title, message)
        add_staff_notification(None, "Promemoria scadenza documento", f"Creato promemoria per documento #{document_id}", "document", str(document_id))
        add_audit_log(user["id"], "notification_sent", "document", str(document_id), "document_expiry")
    return RedirectResponse(url=safe_next_url(next_url, "/admin/document-reminders"), status_code=303)


@app.get("/admin/audit/export")
def admin_audit_export(request: Request):
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT a.id, a.action, a.target_type, a.target_id, a.details, a.created_at, u.full_name AS actor "
            "FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_user_id ORDER BY a.id DESC LIMIT 1000"
        ).fetchall()
    return Response(audit_logs_csv(rows), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=\"audit-consultiq.csv\""})


@app.post("/admin/requests/auto-assign")
def admin_auto_assign_requests(request: Request, csrf_token: str = Form(...)):
    enforce_csrf(request, csrf_token)
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    assigned_count = 0
    assigned_notifications: list[tuple[int, int]] = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        employees = conn.execute("SELECT id, full_name FROM users WHERE role='employee' AND active=1 ORDER BY full_name").fetchall()
        unassigned = conn.execute("SELECT id, service_type FROM appointments WHERE assigned_employee_id IS NULL AND status NOT IN ('completed','cancelled') ORDER BY id ASC").fetchall()
        if employees:
            load = {employee["id"]: conn.execute("SELECT COUNT(*) FROM appointments WHERE assigned_employee_id=? AND status NOT IN ('completed','cancelled')", (employee["id"],)).fetchone()[0] for employee in employees}
            for row in unassigned:
                employee_id = min(load, key=load.get)
                conn.execute("UPDATE appointments SET assigned_employee_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (employee_id, row["id"]))
                load[employee_id] += 1
                assigned_count += 1
                assigned_notifications.append((employee_id, row["id"]))
    for employee_id, request_id in assigned_notifications:
        add_staff_notification(employee_id, "Richiesta assegnata automaticamente", f"Ti e' stata assegnata la richiesta #{request_id}", "appointment", str(request_id))
    add_audit_log(user["id"], "request_assigned", "appointment", "bulk", f"auto_assign:{assigned_count}")
    return RedirectResponse(url="/admin?quick_filter=unassigned", status_code=303)


@app.post("/admin/appointments/{appointment_id}/status")
def update_appointment_status(appointment_id: int, request: Request, csrf_token: str = Form(...), status: str = Form(...), payment_status: str = Form("not_requested"), priority: str = Form("media"), assigned_employee_id: str = Form(""), internal_note: str = Form(""), requested_amount: str = Form(""), payment_method: str = Form(""), payment_reference: str = Form(""), payment_instruction: str = Form(""), payment_internal_note: str = Form(""), next_url: str = Form("/admin")):
    enforce_csrf(request, csrf_token)
    user = require_staff(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if not row or not can_manage_request(user, row):
            return RedirectResponse(url=safe_next_url(next_url, request_home_path(user)), status_code=303)
        clean_status = status if status in REQUEST_STATUSES else "new"
        clean_priority = priority if priority in REQUEST_PRIORITIES else (row["priority"] or "media")
        clean_payment = row["payment_status"]
        clean_assigned = row["assigned_employee_id"]
        clean_amount = row["requested_amount"]
        clean_method = row["payment_method"] if "payment_method" in row.keys() else ""
        clean_reference = row["payment_reference"] if "payment_reference" in row.keys() else ""
        clean_instruction = row["payment_instruction"] if "payment_instruction" in row.keys() else ""
        clean_payment_note = row["payment_internal_note"] if "payment_internal_note" in row.keys() else ""
        if user["role"] in {"admin", "manager"}:
            clean_payment = payment_status if payment_status in PAYMENT_STATUSES else row["payment_status"]
            clean_assigned = int(assigned_employee_id) if assigned_employee_id.strip().isdigit() else None
            clean_method = payment_method.strip()
            clean_reference = payment_reference.strip()
            clean_instruction = payment_instruction.strip()
            clean_payment_note = payment_internal_note.strip()
            try:
                clean_amount = float(requested_amount) if requested_amount.strip() else row["requested_amount"]
            except ValueError:
                clean_amount = row["requested_amount"]
        note_author = user["full_name"] if user.get("full_name") else role_label(user["role"])
        notes = append_internal_note(row["internal_notes"], note_author, internal_note)
        conn.execute(
            "UPDATE appointments SET status=?, priority=?, payment_status=?, assigned_employee_id=?, requested_amount=?, payment_method=?, payment_reference=?, payment_instruction=?, payment_internal_note=?, internal_notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (clean_status, clean_priority, clean_payment, clean_assigned, clean_amount, clean_method, clean_reference, clean_instruction, clean_payment_note, notes, appointment_id),
        )
        updated_row = conn.execute("SELECT user_id, email, service_type, preferred_date, assigned_employee_id FROM appointments WHERE id=?", (appointment_id,)).fetchone()
    add_audit_log(user["id"], "appointment_status_updated", "appointment", str(appointment_id), status)
    if internal_note.strip():
        add_audit_log(user["id"], "internal_note_added", "appointment", str(appointment_id), "internal_note")
        with sqlite3.connect(DB_PATH) as comment_conn:
            comment_conn.execute("INSERT INTO request_comments (request_id, author_id, body, is_internal) VALUES (?, ?, ?, 1)", (appointment_id, user["id"], internal_note.strip()))
    if updated_row:
        subject, body = render_email_template("appointment_status_changed", service=display_service_type(updated_row["service_type"]), status=status_label(clean_status))
        add_notification(updated_row["user_id"], updated_row["email"], "email", subject, body)
        if user["role"] in {"admin", "manager"}:
            payment_subject, payment_body = render_email_template("payment_status_updated", service=display_service_type(updated_row["service_type"]), payment_status=payment_status_label(clean_payment))
            add_notification(updated_row["user_id"], updated_row["email"], "email", payment_subject, payment_body)
            if clean_assigned and clean_assigned != row["assigned_employee_id"]:
                with sqlite3.connect(DB_PATH) as notify_conn:
                    notify_conn.row_factory = sqlite3.Row
                    employee = notify_conn.execute("SELECT id, email FROM users WHERE id=?", (clean_assigned,)).fetchone()
                if employee:
                    emp_subject, emp_body = render_email_template("request_assignment_notification", request_id=appointment_id, service=display_service_type(updated_row["service_type"]))
                    add_notification(employee["id"], employee["email"], "email", emp_subject, emp_body)
                    add_staff_notification(employee["id"], "Richiesta assegnata", f"Ti e' stata assegnata la richiesta #{appointment_id}", "appointment", str(appointment_id))
                    add_audit_log(user["id"], "request_assigned" if not row["assigned_employee_id"] else "request_reassigned", "appointment", str(appointment_id), str(clean_assigned))
        add_staff_notification(updated_row["assigned_employee_id"] if updated_row["assigned_employee_id"] else None, "Stato richiesta aggiornato", f"Richiesta #{appointment_id}: {status_label(clean_status)}", "appointment", str(appointment_id))
    return RedirectResponse(url=safe_next_url(next_url, request_home_path(user)), status_code=303)


@app.post("/admin/appointments/{appointment_id}/delete")
def delete_appointment(appointment_id: int, request: Request, csrf_token: str = Form(...)):
    enforce_csrf(request, csrf_token)
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        docs = conn.execute("SELECT id, stored_filename FROM appointment_documents WHERE appointment_id=?", (appointment_id,)).fetchall()
        conn.execute("DELETE FROM appointment_documents WHERE appointment_id=?", (appointment_id,))
        conn.execute("DELETE FROM appointments WHERE id=?", (appointment_id,))
    for doc in docs:
        file_path = UPLOAD_DIR / doc["stored_filename"]
        if file_path.exists():
            file_path.unlink()
    add_audit_log(user["id"], "appointment_deleted", "appointment", str(appointment_id), "deleted_by_admin")
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/employees")
def admin_employees(request: Request):
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        employees = fetch_staff_members(conn, include_inactive=True)
        requests = fetch_requests_for_user(conn, user)
    employee_rows = []
    for employee in employees:
        assigned = [r for r in requests if r["assigned_employee_id"] == employee["id"]]
        employee_rows.append({"employee": employee, "assigned_total": len(assigned), "open_total": sum(1 for r in assigned if r["status"] != "completed")})
    return render(request, "admin_employees.html", page="admin", title="Gestione operatori | ConsultIQ", employee_rows=employee_rows)


@app.post("/admin/employees")
def create_employee(request: Request, csrf_token: str = Form(...), full_name: str = Form(...), email: str = Form(...), phone: str = Form(""), password: str = Form(...), role: str = Form("employee")):
    enforce_csrf(request, csrf_token)
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    clean_role = role if role in {"manager", "employee"} else "employee"
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO users (full_name, email, phone, password_hash, role, active) VALUES (?, ?, ?, ?, ?, 1)",
                (full_name.strip(), email.strip().lower(), phone.strip(), hash_password(password.strip()), clean_role),
            )
            employee_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except sqlite3.IntegrityError:
        return RedirectResponse(url="/admin/employees", status_code=303)
    add_audit_log(user["id"], "employee_created", "user", str(employee_id), clean_role)
    return RedirectResponse(url="/admin/employees", status_code=303)


@app.post("/admin/employees/{employee_id}/toggle")
def toggle_employee(request: Request, employee_id: int, csrf_token: str = Form(...)):
    enforce_csrf(request, csrf_token)
    user = require_management(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, active FROM users WHERE id=? AND role IN ('manager', 'employee')", (employee_id,)).fetchone()
        if row:
            conn.execute("UPDATE users SET active=? WHERE id=?", (0 if row["active"] else 1, employee_id))
    return RedirectResponse(url="/admin/employees", status_code=303)


@app.post("/admin/documents/{document_id}/update")
def update_document(document_id: int, request: Request, csrf_token: str = Form(...), status: str = Form(...), category: str = Form("Altro"), client_note: str = Form(""), expires_at: str = Form("")):
    enforce_csrf(request, csrf_token)
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT d.id, d.appointment_id, a.user_id, a.email, a.service_type FROM appointment_documents d JOIN appointments a ON a.id=d.appointment_id WHERE d.id=?", (document_id,)).fetchone()
        if not row:
            return RedirectResponse(url="/admin", status_code=303)
        conn.execute("UPDATE appointment_documents SET status=?, category=?, client_note=?, expires_at=? WHERE id=?", (status if status in DOCUMENT_STATUSES else "received", category if category in DOCUMENT_CATEGORIES else "Altro", client_note.strip(), expires_at.strip() or None, document_id))
    add_audit_log(user["id"], "document_status_updated", "document", str(document_id), status)
    if status == "missing_info":
        subject, body = render_email_template("missing_documents_requested", service=display_service_type(row["service_type"]), missing_documents=client_note.strip() or "Documentazione integrativa")
        add_notification(row["user_id"], row["email"], "email", subject, body)
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/documents/{document_id}/delete")
def delete_document(document_id: int, request: Request, csrf_token: str = Form(...)):
    enforce_csrf(request, csrf_token)
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT stored_filename, original_filename FROM appointment_documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            return RedirectResponse(url="/admin", status_code=303)
        conn.execute("DELETE FROM appointment_documents WHERE id=?", (document_id,))
    file_path = UPLOAD_DIR / row["stored_filename"]
    if file_path.exists():
        file_path.unlink()
    add_audit_log(user["id"], "document_deleted", "document", str(document_id), row["original_filename"])
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/users/{user_id}/export")
def export_user_data(user_id: int, request: Request):
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        user_row = conn.execute("SELECT id, full_name, email, phone, role, is_anonymized, created_at FROM users WHERE id=?", (user_id,)).fetchone()
        appointments = conn.execute("SELECT * FROM appointments WHERE user_id=?", (user_id,)).fetchall()
        documents = conn.execute("SELECT d.* FROM appointment_documents d JOIN appointments a ON a.id=d.appointment_id WHERE a.user_id=?", (user_id,)).fetchall()
        notifications = conn.execute("SELECT * FROM notifications WHERE user_id=?", (user_id,)).fetchall()
    if not user_row:
        raise HTTPException(status_code=404, detail="User not found")
    payload = {
        "user": dict(user_row),
        "appointments": [dict(row) for row in appointments],
        "documents": [dict(row) for row in documents],
        "notifications": [dict(row) for row in notifications],
    }
    add_audit_log(user["id"], "client_data_exported", "user", str(user_id), "json_export")
    return Response(content=json.dumps(payload, ensure_ascii=True, indent=2), media_type="application/json", headers={"Content-Disposition": f"attachment; filename=\"client-{user_id}-export.json\""})


@app.post("/admin/users/{user_id}/anonymize")
def anonymize_user(user_id: int, request: Request, csrf_token: str = Form(...)):
    enforce_csrf(request, csrf_token)
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, email FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return RedirectResponse(url="/admin", status_code=303)
        anon_email = f"anon-{user_id}@anonymized.local"
        conn.execute("UPDATE users SET full_name=?, email=?, phone=?, password_hash=?, is_anonymized=1 WHERE id=?", (f"Utente anonimizzato #{user_id}", anon_email, "", hash_password(uuid.uuid4().hex), user_id))
        conn.execute("UPDATE appointments SET full_name=?, email=?, phone=?, message=? WHERE user_id=?", (f"Utente anonimizzato #{user_id}", anon_email, "", "Record anonimizzato su richiesta", user_id))
    add_audit_log(user["id"], "user_anonymized", "user", str(user_id), row["email"])
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/appointments/{appointment_id}/send-email")
def admin_send_appointment_email(appointment_id: int, request: Request, csrf_token: str = Form(...), template_key: str = Form(...), missing_documents: str = Form("")):
    enforce_csrf(request, csrf_token)
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT user_id, full_name, email, service_type, preferred_date FROM appointments WHERE id=?", (appointment_id,)).fetchone()
    if not row or template_key not in {"missing_documents_requested", "appointment_confirmed", "appointment_cancelled"}:
        return RedirectResponse(url="/admin", status_code=303)
    subject, body = render_email_template(template_key, client_name=row["full_name"], service=display_service_type(row["service_type"]), preferred_date=row["preferred_date"], missing_documents=missing_documents or "Da definire")
    add_notification(row["user_id"], row["email"], "email", subject, body)
    add_audit_log(user["id"], "appointment_email_sent", "appointment", str(appointment_id), template_key)
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/2fa/setup")
def admin_2fa_setup(request: Request):
    user = require_admin(request)
    if not user:
        return RedirectResponse(url="/admin-login", status_code=303)
    secret = pyotp.random_base32()
    request.session["pending_2fa_secret"] = secret
    provisioning_uri = pyotp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name="ConsultIQ")
    return render(request, "admin_2fa_setup.html", page="admin", title="Configurazione 2FA admin | ConsultIQ", secret=secret, provisioning_uri=provisioning_uri)


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
    return render(request, "admin_2fa_verify.html", page="auth", title="Verifica 2FA admin | ConsultIQ", error=error)


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
    return render(request, "contact.html", page="contact", title="Contatti e richieste assistenza | ConsultIQ", submitted=submitted, selected_service=service)


@app.post("/contact")
def contact_submit(request: Request, csrf_token: str = Form(...), full_name: str = Form(...), email: str = Form(...), phone: str = Form(""), subject: str = Form(...), message: str = Form(...), privacy_accept: str = Form(...), marketing_accept: str = Form("0")):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "contact_submit", 10, 300)
    enforce_required_consents(privacy_accept)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO contact_messages (full_name, email, phone, subject, message, privacy_accepted, marketing_accepted) VALUES (?, ?, ?, ?, ?, ?, ?)", (full_name.strip(), email.strip(), phone.strip(), subject.strip(), message.strip(), int(is_truthy(privacy_accept)), int(is_truthy(marketing_accept))))
    subject_line, body = render_email_template("contact_message_received")
    add_notification(None, email.strip(), "email", subject_line, body)
    return RedirectResponse(url="/contact?submitted=1", status_code=303)


@app.post("/appointments")
def create_appointment(request: Request, csrf_token: str = Form(...), full_name: str = Form(...), email: str = Form(...), phone: str = Form(...), service_type: str = Form(...), preferred_date: str = Form(""), message: str = Form(""), privacy_accept: str = Form(...), terms_accept: str = Form(...), marketing_accept: str = Form("0"), document: UploadFile | None = File(default=None)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "create_appointment", 20, 300)
    enforce_required_consents(privacy_accept, terms_accept)
    validate_upload(document)
    user = get_current_user(request)
    user_id = user["id"] if user else None
    service_item = find_service_by_service_type(service_type.strip())
    price_type = service_item["price_type"] if service_item else "quote_required"
    price_amount = service_item["price_amount"] if service_item else None
    clean_date = preferred_date.strip() or date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO appointments (user_id, full_name, email, phone, service_type, preferred_date, message, status, priority, payment_status, price_label, price_amount, requested_amount, privacy_accepted, terms_accepted, marketing_accepted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                full_name.strip(),
                email.strip(),
                phone.strip(),
                service_type.strip(),
                clean_date,
                message.strip(),
                "new",
                "media",
                "not_requested",
                price_type,
                price_amount,
                price_amount,
                int(is_truthy(privacy_accept)),
                int(is_truthy(terms_accept)),
                int(is_truthy(marketing_accept)),
            ),
        )
        appointment_id = cur.lastrowid
        seed_request_workflow(conn, appointment_id, service_type.strip())
        if document and document.filename:
            save_appointment_document(conn, appointment_id, user_id, document)
    add_audit_log(user_id, "appointment_created", "appointment", str(appointment_id), service_type)
    display_name = display_service_type(service_type.strip())
    subject_line, body = render_email_template("client_booking_confirmation", service=display_name)
    add_notification(user_id, email.strip(), "email", subject_line, body)
    admin_subject, admin_body = render_email_template("admin_new_booking", client_name=full_name.strip(), service=display_name, preferred_date=clean_date)
    add_notification(None, ADMIN_NOTIFICATION_EMAIL, "email", admin_subject, admin_body)
    add_staff_notification(None, "Nuova richiesta ricevuta", f"{full_name.strip()} - {display_name}", "appointment", str(appointment_id))
    return RedirectResponse(url="/client-area" if user else "/contact?submitted=1", status_code=303)


@app.get("/register")
def register_page(request: Request, error: str = ""):
    return render(request, "register.html", page="auth", title="Registrazione cliente | ConsultIQ", error=error)


@app.post("/register")
def register_submit(request: Request, csrf_token: str = Form(...), full_name: str = Form(...), email: str = Form(...), phone: str = Form(""), password: str = Form(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "register_submit", 6, 300)
    if len(password.strip()) < 10:
        return RedirectResponse(url="/register?error=La+password+deve+contenere+almeno+10+caratteri", status_code=303)
    if password.strip().lower() in COMMON_PASSWORDS:
        return RedirectResponse(url="/register?error=Scegli+una+password+piu'+sicura", status_code=303)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO users (full_name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, 'client')", (full_name.strip(), email.strip().lower(), phone.strip(), hash_password(password.strip())))
    except sqlite3.IntegrityError:
        return RedirectResponse(url="/register?error=Email+gia'+registrata", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/login")
def login_page(request: Request, error: str = ""):
    return render(request, "login.html", page="auth", title="Accesso area cliente | ConsultIQ", error=error)


@app.post("/login")
def login_submit(request: Request, csrf_token: str = Form(...), email: str = Form(...), password: str = Form(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "login_submit", 8, 300)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, role, password_hash, active FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not row or row["role"] != "client" or not row["active"] or not verify_password(password.strip(), row["password_hash"]):
        return RedirectResponse(url="/login?error=Credenziali+non+valide", status_code=303)
    request.session["user_id"] = row["id"]
    add_audit_log(row["id"], "login", "user", str(row["id"]), row["role"])
    return RedirectResponse(url="/client-area", status_code=303)


@app.get("/admin-login")
def admin_login_page(request: Request, error: str = ""):
    return render(request, "admin_login.html", page="auth", title="Accesso staff | ConsultIQ", error=error)


@app.post("/admin-login")
def admin_login_submit(request: Request, csrf_token: str = Form(...), email: str = Form(...), password: str = Form(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "admin_login_submit", 8, 300)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, role, password_hash, admin_totp_secret, active FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not row or row["role"] not in {"admin", "manager", "employee"} or not row["active"] or not verify_password(password.strip(), row["password_hash"]):
        return RedirectResponse(url="/admin-login?error=Credenziali+staff+non+valide", status_code=303)
    if row["role"] == "admin" and row["admin_totp_secret"]:
        request.session["pending_admin_user_id"] = row["id"]
        return RedirectResponse(url="/admin-2fa-verify", status_code=303)
    request.session["user_id"] = row["id"]
    return RedirectResponse(url="/admin" if row["role"] in {"admin", "manager"} else "/employee/dashboard", status_code=303)


@app.get("/password-reset-request")
def password_reset_request_page(request: Request, status: str = ""):
    return render(request, "password_reset_request.html", page="auth", title="Recupero password | ConsultIQ", status=status)


@app.post("/password-reset-request")
def password_reset_request_submit(request: Request, csrf_token: str = Form(...), email: str = Form(...)):
    enforce_csrf(request, csrf_token)
    check_rate_limit(request, "password_reset_request_submit", 5, 300)
    reset_payload: dict[str, Any] | None = None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT id FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        if user:
            token = uuid.uuid4().hex
            expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            conn.execute("INSERT INTO password_resets (user_id, token, expires_at, used) VALUES (?, ?, ?, 0)", (user["id"], token, expires))
            link = f"{request.url.scheme}://{request.url.netloc}/password-reset/{token}"
            reset_payload = {"user_id": user["id"], "email": email.strip(), "link": link}
    if reset_payload:
        add_notification(reset_payload["user_id"], reset_payload["email"], "email", "Reimpostazione password ConsultIQ", f"Usa questo link per impostare una nuova password: {reset_payload['link']}")
        add_audit_log(reset_payload["user_id"], "password_reset_requested", "user", str(reset_payload["user_id"]), "email_sent")
    return RedirectResponse(url="/password-reset-request?status=Se+l'email+esiste,+abbiamo+inviato+un+link+per+reimpostare+la+password", status_code=303)


@app.get("/password-reset/{token}")
def password_reset_page(request: Request, token: str, error: str = ""):
    return render(request, "password_reset.html", page="auth", title="Nuova password | ConsultIQ", token=token, error=error)


@app.post("/password-reset/{token}")
def password_reset_submit(request: Request, token: str, csrf_token: str = Form(...), password: str = Form(...)):
    enforce_csrf(request, csrf_token)
    if len(password.strip()) < 10:
        return RedirectResponse(url=f"/password-reset/{token}?error=La+password+deve+contenere+almeno+10+caratteri", status_code=303)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT pr.id, pr.user_id, pr.expires_at, pr.used, u.role FROM password_resets pr JOIN users u ON u.id=pr.user_id WHERE pr.token=?", (token,)).fetchone()
        if not row or row["used"]:
            return RedirectResponse(url=f"/password-reset/{token}?error=Token+non+valido+o+gia'+utilizzato", status_code=303)
        if datetime.fromisoformat(row["expires_at"]) < now:
            return RedirectResponse(url=f"/password-reset/{token}?error=Token+scaduto", status_code=303)
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password.strip()), row["user_id"]))
        conn.execute("UPDATE password_resets SET used=1 WHERE id=?", (row["id"],))
    add_audit_log(row["user_id"], "password_reset_completed", "user", str(row["user_id"]), "success")
    if row["role"] in {"admin", "manager", "employee"}:
        return RedirectResponse(url="/admin-login?error=Password+aggiornata+correttamente.+Accedi+di+nuovo", status_code=303)
    return RedirectResponse(url="/login?error=Password+aggiornata+correttamente.+Accedi+di+nuovo", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)
