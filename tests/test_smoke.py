import uuid
import sqlite3
import re

from fastapi.testclient import TestClient

from app.main import (
    DB_PATH,
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_ADMIN_PASSWORD,
    RATE_LIMIT_STORE,
    SEO_LANDING_PAGES,
    app,
    build_whatsapp_send_link,
    create_checkout_session,
    generate_whatsapp_reminder,
    sanitize_phone_number,
)


def extract_csrf(response_text: str) -> str:
    marker = 'meta name="csrf-token" content="'
    start = response_text.index(marker) + len(marker)
    end = response_text.index('"', start)
    return response_text[start:end]


def register_and_login_client(client: TestClient) -> str:
    RATE_LIMIT_STORE.clear()
    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    register_page = client.get("/register")
    csrf = extract_csrf(register_page.text)
    register = client.post(
        "/register",
        data={
            "csrf_token": csrf,
            "full_name": "Mario Rossi",
            "email": email,
            "phone": "123456789",
            "password": "StrongPass123!",
        },
        follow_redirects=False,
    )
    assert register.status_code == 303
    login_page = client.get("/login")
    csrf = extract_csrf(login_page.text)
    login = client.post(
        "/login",
        data={"csrf_token": csrf, "email": email, "password": "StrongPass123!"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    return email


def login_admin(client: TestClient) -> None:
    page = client.get("/admin-login")
    csrf = extract_csrf(page.text)
    response = client.post(
        "/admin-login",
        data={"csrf_token": csrf, "email": DEFAULT_ADMIN_EMAIL, "password": DEFAULT_ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303


def create_test_request(client: TestClient, service_type: str = "CAF - 730 Tax Filing") -> int:
    email = register_and_login_client(client)
    page = client.get("/contact")
    csrf = extract_csrf(page.text)
    response = client.post(
        "/appointments",
        data={
            "csrf_token": csrf,
            "full_name": "Mario Rossi",
            "email": email,
            "phone": "123456789",
            "service_type": service_type,
            "preferred_date": "2026-06-10",
            "message": "Test request",
            "privacy_accept": "1",
            "terms_accept": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/client-area")
    assert page.status_code == 200
    text = page.text
    marker = "/appointments/"
    start = text.index(marker) + len(marker)
    end = text.index("/invite", start)
    return int(text[start:end])


def create_employee(client: TestClient, role: str = "employee") -> tuple[str, str]:
    login_admin(client)
    employee_email = f"{role}-{uuid.uuid4().hex[:8]}@consultiq.it"
    password = "StaffPass123!"
    page = client.get("/admin/employees")
    csrf = extract_csrf(page.text)
    response = client.post(
        "/admin/employees",
        data={
            "csrf_token": csrf,
            "full_name": "Operatore Demo",
            "email": employee_email,
            "phone": "3333333333",
            "password": password,
            "role": role,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return employee_email, password


def login_staff(client: TestClient, email: str, password: str) -> None:
    page = client.get("/admin-login")
    csrf = extract_csrf(page.text)
    response = client.post(
        "/admin-login",
        data={"csrf_token": csrf, "email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def get_user_id_by_email(email: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    assert row is not None
    return int(row[0])


def visible_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_home_ok():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Soluzioni semplici per le tue pratiche" in response.text


def test_services_ok_and_public_pricing_hidden():
    with TestClient(app) as client:
        response = client.get("/services")
        assert response.status_code == 200
        assert "Richiedi assistenza" in response.text
        assert "Gratuito" not in response.text
        assert "Preventivo su richiesta" not in response.text
        assert "Da EUR" not in response.text


def test_login_page_ok():
    with TestClient(app) as client:
        response = client.get("/login")
        assert response.status_code == 200


def test_health_ok():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_password_reset_request_page_ok():
    with TestClient(app) as client:
        response = client.get("/password-reset-request")
        assert response.status_code == 200


def test_contact_request_form_has_document_upload():
    with TestClient(app) as client:
        response = client.get("/contact")
        assert response.status_code == 200
        assert 'enctype="multipart/form-data"' in response.text
        assert 'name="document"' in response.text
        assert 'name="preferred_date"' not in response.text
        assert "Invia richiesta pratica" in response.text


def test_service_booking_form_has_no_date_selection():
    with TestClient(app) as client:
        response = client.get("/services/caf-730")
        assert response.status_code == 200
        assert "Invia richiesta per questo servizio" in response.text
        assert 'name="preferred_date"' not in response.text


def test_terms_and_cookie_pages_ok():
    with TestClient(app) as client:
        assert client.get("/terms").status_code == 200
        assert client.get("/cookie-policy").status_code == 200


def test_contact_requires_privacy_consent():
    with TestClient(app) as client:
        page = client.get("/contact")
        csrf = extract_csrf(page.text)
        response = client.post(
            "/contact",
            data={
                "csrf_token": csrf,
                "full_name": "Mario Rossi",
                "email": "mario@example.com",
                "phone": "123456789",
                "subject": "Informazioni",
                "message": "Vorrei supporto",
            },
        )
        assert response.status_code == 422


def test_admin_routes_require_auth():
    with TestClient(app) as fresh_client:
        response = fresh_client.get("/admin", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/admin-login"


def test_admin_calendar_requires_auth():
    with TestClient(app) as fresh_client:
        response = fresh_client.get("/admin/calendar", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/admin-login"


def test_robots_and_sitemap_exist():
    with TestClient(app) as client:
        robots = client.get("/robots.txt")
        sitemap = client.get("/sitemap.xml")
        assert robots.status_code == 200
        assert "Sitemap: /sitemap.xml" in robots.text
        assert sitemap.status_code == 200
        assert "<urlset" in sitemap.text


def test_all_seo_pages_return_200_and_sitemap_lists_them():
    with TestClient(app) as client:
        sitemap = client.get("/sitemap.xml")
        for slug in SEO_LANDING_PAGES:
            response = client.get(f"/servizi/{slug}")
            assert response.status_code == 200
            assert f"/servizi/{slug}" in sitemap.text


def test_client_can_download_own_ics_invite():
    with TestClient(app) as client:
        request_id = create_test_request(client)
        response = client.get(f"/appointments/{request_id}/invite")
        assert response.status_code == 200
        assert "BEGIN:VCALENDAR" in response.text


def test_admin_can_assign_request_and_update_payment_status():
    with TestClient(app) as client:
        request_id = create_test_request(client, "Business - Partita IVA")
        employee_email, employee_password = create_employee(client)
        dashboard = client.get("/admin")
        csrf = extract_csrf(dashboard.text)
        employee_id = get_user_id_by_email(employee_email)
        response = client.post(
            f"/admin/appointments/{request_id}/status",
            data={
                "csrf_token": csrf,
                "status": "completed",
                "priority": "alta",
                "payment_status": "paid",
                "assigned_employee_id": str(employee_id),
                "requested_amount": "99",
                "internal_note": "Assegnata al team",
                "next_url": "/admin",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        refreshed = client.get("/admin")
        assert "Pagato" in refreshed.text
        assert "Operatore Demo" in refreshed.text


def test_employee_login_and_visibility_are_scoped():
    with TestClient(app) as client:
        request_id = create_test_request(client, "Business - Partita IVA")
        request_id_2 = create_test_request(client, "CAF - 730 Tax Filing")
        employee_email, employee_password = create_employee(client)
        dashboard = client.get("/admin")
        csrf = extract_csrf(dashboard.text)
        employee_id = get_user_id_by_email(employee_email)
        client.post(
            f"/admin/appointments/{request_id}/status",
            data={
                "csrf_token": csrf,
                "status": "in_progress",
                "priority": "media",
                "assigned_employee_id": str(employee_id),
                "payment_status": "not_requested",
                "next_url": "/admin",
            },
            follow_redirects=False,
        )
        client.get("/logout")
        login_staff(client, employee_email, employee_password)
        employee_dashboard = client.get("/employee/dashboard")
        assert employee_dashboard.status_code == 200
        assert f">{request_id}<" in employee_dashboard.text
        assert f">{request_id_2}<" not in employee_dashboard.text
        forbidden = client.get(f"/appointments/{request_id_2}/invite")
        assert forbidden.status_code == 403


def test_admin_dashboard_and_calendar_and_employee_pages_load():
    with TestClient(app) as client:
        create_test_request(client)
        login_admin(client)
        dashboard = client.get("/admin")
        calendar = client.get("/admin/calendar")
        analytics = client.get("/admin/analytics")
        employees = client.get("/admin/employees")
        assert dashboard.status_code == 200
        assert calendar.status_code == 200
        assert analytics.status_code == 200
        assert employees.status_code == 200
        assert "Copia promemoria WhatsApp" in calendar.text
        assert "Panoramica" in dashboard.text
        assert "Pipeline richieste" in dashboard.text
        assert "Registro attivita" in dashboard.text


def test_admin_dashboard_filters_and_backoffice_layout():
    with TestClient(app) as client:
        create_test_request(client, "CAF - 730 Tax Filing")
        login_admin(client)
        response = client.get("/admin?quick_filter=unassigned")
        assert response.status_code == 200
        assert "Da assegnare" in response.text
        assert "ConsultIQ Admin / Area Staff" in response.text
        assert 'href="/logout"' in response.text
        assert "backoffice-sidebar" in response.text
        assert 'class="btn ghost small" href="/logout"' not in response.text
        assert 'backoffice-logout" href="/logout"' in response.text
        assert "mobile-cta-bar" not in response.text
        assert "Richiedi aiuto" not in response.text
        assert 'href="/services"' not in response.text
        assert 'href="/contact"' not in response.text
        text = visible_text(response.text)
        assert "in_progress" not in text
        assert "completed" not in text
        assert "pending" not in text


def test_request_detail_pages_and_checklist_workflow():
    with TestClient(app) as client:
        request_id = create_test_request(client, "Business - Partita IVA")
        employee_email, employee_password = create_employee(client)
        dashboard = client.get("/admin")
        csrf = extract_csrf(dashboard.text)
        employee_id = get_user_id_by_email(employee_email)
        client.post(
            f"/admin/appointments/{request_id}/status",
            data={
                "csrf_token": csrf,
                "status": "in_progress",
                "priority": "alta",
                "assigned_employee_id": str(employee_id),
                "payment_status": "not_requested",
                "next_url": "/admin",
            },
            follow_redirects=False,
        )
        detail = client.get(f"/admin/requests/{request_id}")
        assert detail.status_code == 200
        assert "Checklist interna" in detail.text
        assert "Gestione imprese - Apertura Partita IVA" in detail.text
        csrf = extract_csrf(detail.text)
        created = client.post(
            f"/requests/{request_id}/checklist",
            data={"csrf_token": csrf, "title": "Preparare pratica", "next_url": f"/admin/requests/{request_id}"},
            follow_redirects=False,
        )
        assert created.status_code == 303
        refreshed = client.get(f"/admin/requests/{request_id}")
        assert "Preparare pratica" in refreshed.text
        csrf = extract_csrf(refreshed.text)
        with sqlite3.connect(DB_PATH) as conn:
            item_id = conn.execute("SELECT id FROM request_checklist_items WHERE request_id=? ORDER BY id DESC", (request_id,)).fetchone()[0]
        toggled = client.post(
            f"/requests/{request_id}/checklist/{item_id}/toggle",
            data={"csrf_token": csrf, "completed": "1", "next_url": f"/admin/requests/{request_id}"},
            follow_redirects=False,
        )
        assert toggled.status_code == 303
        client.get("/logout")
        login_staff(client, employee_email, employee_password)
        employee_detail = client.get(f"/employee/requests/{request_id}")
        assert employee_detail.status_code == 200
        assert "Preparare pratica" in employee_detail.text


def test_employee_cannot_open_unassigned_or_other_request_detail():
    with TestClient(app) as client:
        request_id = create_test_request(client, "Business - Partita IVA")
        other_request_id = create_test_request(client, "CAF - 730 Tax Filing")
        employee_email, employee_password = create_employee(client)
        other_employee_email, _ = create_employee(client)
        dashboard = client.get("/admin")
        csrf = extract_csrf(dashboard.text)
        employee_id = get_user_id_by_email(employee_email)
        other_employee_id = get_user_id_by_email(other_employee_email)
        client.post(
            f"/admin/appointments/{request_id}/status",
            data={
                "csrf_token": csrf,
                "status": "in_progress",
                "priority": "media",
                "assigned_employee_id": str(employee_id),
                "payment_status": "not_requested",
                "next_url": "/admin",
            },
            follow_redirects=False,
        )
        client.post(
            f"/admin/appointments/{other_request_id}/status",
            data={
                "csrf_token": csrf,
                "status": "in_progress",
                "priority": "media",
                "assigned_employee_id": str(other_employee_id),
                "payment_status": "not_requested",
                "next_url": "/admin",
            },
            follow_redirects=False,
        )
        assert client.get(f"/admin/requests/{request_id}").status_code == 200
        assert client.get(f"/admin/requests/{other_request_id}").status_code == 200
        client.get("/logout")
        login_staff(client, employee_email, employee_password)
        admin_area = client.get("/admin", follow_redirects=False)
        assert admin_area.status_code == 303
        assert admin_area.headers["location"] == "/admin-login"
        assert client.get(f"/employee/requests/{request_id}").status_code == 200
        assert client.get(f"/employee/requests/{other_request_id}").status_code == 403


def test_staff_pages_hide_public_footer_and_show_dashboard_limits():
    with TestClient(app) as client:
        create_test_request(client, "CAF - 730 Tax Filing")
        login_admin(client)
        for path in ["/admin", "/admin/employees", "/admin/analytics"]:
            response = client.get(path)
            assert response.status_code == 200
            assert "site-footer" not in response.text
            assert "site-header" not in response.text
            assert "backoffice-sidebar" in response.text
            assert "ConsultIQ Admin / Area Staff" in response.text
            assert "mobile-cta-bar" not in response.text
            assert "Richiedi aiuto" not in response.text
        dashboard = client.get("/admin")
        assert "Mostrate le ultime 10 richieste" in dashboard.text
        assert "Anteprima degli ultimi 20 documenti" in dashboard.text
        assert "Mostrate le ultime 50 attivita" in dashboard.text


def test_employee_backoffice_layout_is_scoped():
    with TestClient(app) as client:
        request_id = create_test_request(client, "Business - Partita IVA")
        employee_email, employee_password = create_employee(client)
        dashboard = client.get("/admin")
        csrf = extract_csrf(dashboard.text)
        employee_id = get_user_id_by_email(employee_email)
        client.post(
            f"/admin/appointments/{request_id}/status",
            data={
                "csrf_token": csrf,
                "status": "in_progress",
                "priority": "media",
                "assigned_employee_id": str(employee_id),
                "payment_status": "not_requested",
                "next_url": "/admin",
            },
            follow_redirects=False,
        )
        client.get("/logout")
        login_staff(client, employee_email, employee_password)
        response = client.get("/employee/dashboard")
        assert response.status_code == 200
        assert "backoffice-sidebar" in response.text
        assert "Le mie richieste" in response.text
        assert "Notifiche" in response.text
        assert "Operatori" not in response.text
        assert "Performance Team" not in response.text
        assert "Analytics" not in response.text
        assert 'href="/admin/calendar"' not in response.text
        assert "site-header" not in response.text
        assert "mobile-cta-bar" not in response.text
        assert "Richiedi aiuto" not in response.text


def test_client_request_detail_security_and_safe_visibility():
    with TestClient(app) as client:
        request_id = create_test_request(client, "CAF - 730 Tax Filing")
        detail = client.get(f"/client/requests/{request_id}")
        assert detail.status_code == 200
        assert "Documenti richiesti" in detail.text
        assert "CU / Certificazione Unica" in detail.text
        assert "Note interne" not in detail.text
        assert "Registro audit" not in detail.text
        client.get("/logout")
        other_email = register_and_login_client(client)
        assert other_email
        forbidden = client.get(f"/client/requests/{request_id}")
        assert forbidden.status_code == 403


def test_client_logout_is_inside_client_area_not_header():
    with TestClient(app) as client:
        register_and_login_client(client)
        response = client.get("/client-area")
        assert response.status_code == 200
        assert "Esci dall'area cliente" in response.text
        header = response.text.split("</header>", 1)[0]
        assert 'href="/logout"' not in header


def test_internal_and_public_comments_visibility():
    with TestClient(app) as client:
        request_id = create_test_request(client, "Business - Partita IVA")
        login_admin(client)
        detail = client.get(f"/admin/requests/{request_id}")
        csrf = extract_csrf(detail.text)
        internal = client.post(
            f"/requests/{request_id}/comments",
            data={"csrf_token": csrf, "body": "Nota interna riservata", "visibility": "internal", "next_url": f"/admin/requests/{request_id}"},
            follow_redirects=False,
        )
        public = client.post(
            f"/requests/{request_id}/comments",
            data={"csrf_token": csrf, "body": "Aggiornamento visibile al cliente", "visibility": "public", "next_url": f"/admin/requests/{request_id}"},
            follow_redirects=False,
        )
        assert internal.status_code == 303
        assert public.status_code == 303
        admin_detail = client.get(f"/admin/requests/{request_id}")
        assert "Nota interna riservata" in admin_detail.text
        assert "Aggiornamento visibile al cliente" in admin_detail.text
        client.get("/logout")
        page = client.get("/login")
        csrf = extract_csrf(page.text)
        with sqlite3.connect(DB_PATH) as conn:
            email = conn.execute("SELECT email FROM appointments WHERE id=?", (request_id,)).fetchone()[0]
        login = client.post("/login", data={"csrf_token": csrf, "email": email, "password": "StrongPass123!"}, follow_redirects=False)
        assert login.status_code == 303
        client_detail = client.get(f"/client/requests/{request_id}")
        assert "Aggiornamento visibile al cliente" in client_detail.text
        assert "Nota interna riservata" not in client_detail.text


def test_auto_document_requirements_workflow_and_notifications():
    with TestClient(app) as client:
        request_id = create_test_request(client, "Business - Partita IVA")
        with sqlite3.connect(DB_PATH) as conn:
            doc_count = conn.execute("SELECT COUNT(*) FROM request_document_requirements WHERE request_id=?", (request_id,)).fetchone()[0]
            task_count = conn.execute("SELECT COUNT(*) FROM request_checklist_items WHERE request_id=?", (request_id,)).fetchone()[0]
            staff_notice_count = conn.execute("SELECT COUNT(*) FROM notifications WHERE channel='staff' AND target_id=?", (str(request_id),)).fetchone()[0]
        assert doc_count >= 4
        assert task_count >= 4
        assert staff_notice_count >= 1
        login_admin(client)
        detail = client.get(f"/admin/requests/{request_id}")
        csrf = extract_csrf(detail.text)
        with sqlite3.connect(DB_PATH) as conn:
            req_id = conn.execute("SELECT id FROM request_document_requirements WHERE request_id=? LIMIT 1", (request_id,)).fetchone()[0]
        response = client.post(
            f"/requests/{request_id}/document-requirements/{req_id}",
            data={"csrf_token": csrf, "status": "received", "note": "Ricevuto in sede", "next_url": f"/admin/requests/{request_id}"},
            follow_redirects=False,
        )
        assert response.status_code == 303


def test_staff_notifications_mark_read_and_team_performance_load():
    with TestClient(app) as client:
        request_id = create_test_request(client, "CAF - 730 Tax Filing")
        login_admin(client)
        performance = client.get("/admin/team-performance")
        assert performance.status_code == 200
        assert "Performance team" in performance.text
        notifications = client.get("/staff/notifications")
        assert notifications.status_code == 200
        assert "Notifiche staff" in notifications.text
        csrf = extract_csrf(notifications.text)
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT id FROM notifications WHERE channel='staff' AND target_id=? ORDER BY id DESC", (str(request_id),)).fetchone()
        assert row is not None
        marked = client.post(f"/staff/notifications/{row[0]}/read", data={"csrf_token": csrf}, follow_redirects=False)
        assert marked.status_code == 303


def test_launch_readiness_permissions_and_audit_export_load():
    with TestClient(app) as client:
        create_test_request(client, "CAF - 730 Tax Filing")
        login_admin(client)
        launch = client.get("/admin/launch-readiness")
        permissions = client.get("/admin/permissions")
        reminders = client.get("/admin/document-reminders")
        audit = client.get("/admin/audit/export")
        assert launch.status_code == 200
        assert "Checklist produzione ConsultIQ" in launch.text
        assert "Stato migrazioni SQLite" in launch.text
        assert permissions.status_code == 200
        assert "Controllo accessi SmartCAF" in permissions.text
        assert "Operatore" in permissions.text
        assert reminders.status_code == 200
        assert "Promemoria documenti in scadenza" in reminders.text
        assert audit.status_code == 200
        assert "text/csv" in audit.headers["content-type"]
        assert "Azione" in audit.text


def test_auto_assignment_assigns_unassigned_requests():
    with TestClient(app) as client:
        request_id = create_test_request(client, "CAF - 730 Tax Filing")
        create_employee(client)
        dashboard = client.get("/admin")
        csrf = extract_csrf(dashboard.text)
        response = client.post("/admin/requests/auto-assign", data={"csrf_token": csrf}, follow_redirects=False)
        assert response.status_code == 303
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT assigned_employee_id FROM appointments WHERE id=?", (request_id,)).fetchone()
        assert row[0] is not None


def test_document_expiry_reminder_creates_notification():
    with TestClient(app) as client:
        request_id = create_test_request(client, "CAF - 730 Tax Filing")
        login_admin(client)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO appointment_documents (appointment_id, original_filename, stored_filename, file_path, category, status, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (request_id, "carta-identita.pdf", "dummy.pdf", "app/data/uploads/dummy.pdf", "Documento identita", "received", "2026-06-20"),
            )
            document_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        page = client.get("/admin/document-reminders?days=30")
        assert "carta-identita.pdf" in page.text
        csrf = extract_csrf(page.text)
        response = client.post(f"/admin/document-reminders/{document_id}/notify", data={"csrf_token": csrf}, follow_redirects=False)
        assert response.status_code == 303
        with sqlite3.connect(DB_PATH) as conn:
            count = conn.execute("SELECT COUNT(*) FROM notifications WHERE title='Documento in scadenza ConsultIQ'").fetchone()[0]
        assert count >= 1


def test_staff_password_reset_redirects_to_staff_login():
    with TestClient(app) as client:
        employee_email, _ = create_employee(client)
        client.get("/logout")
        page = client.get("/password-reset-request")
        csrf = extract_csrf(page.text)
        requested = client.post("/password-reset-request", data={"csrf_token": csrf, "email": employee_email}, follow_redirects=False)
        assert requested.status_code == 303
        with sqlite3.connect(DB_PATH) as conn:
            token = conn.execute(
                "SELECT pr.token FROM password_resets pr JOIN users u ON u.id=pr.user_id WHERE u.email=? ORDER BY pr.id DESC LIMIT 1",
                (employee_email,),
            ).fetchone()[0]
        reset_page = client.get(f"/password-reset/{token}")
        csrf = extract_csrf(reset_page.text)
        reset = client.post(f"/password-reset/{token}", data={"csrf_token": csrf, "password": "NewStaffPass123!"}, follow_redirects=False)
        assert reset.status_code == 303
        assert reset.headers["location"].startswith("/admin-login")


def test_admin_is_redirected_away_from_client_area():
    with TestClient(app) as client:
        login_admin(client)
        response = client.get("/client-area", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/admin"


def test_analytics_csv_export_works():
    with TestClient(app) as client:
        login_admin(client)
        response = client.get("/admin/analytics/export?mode=summary")
        assert response.status_code == 200
        assert "metrica,valore" in response.text


def test_whatsapp_helper_generation():
    appointment = {
        "id": 99,
        "full_name": "Mario Rossi",
        "phone": "+39 344 488 1606",
        "service_type": "Business - Partita IVA",
        "preferred_date": "2026-06-10",
        "message": "Test",
    }
    assert sanitize_phone_number("0039 344 488 1606") == "393444881606"
    text = generate_whatsapp_reminder(appointment, "missing_documents", "Carta identita'")
    assert "Carta identita'" in text
    link = build_whatsapp_send_link(appointment["phone"], text)
    assert link.startswith("https://wa.me/393444881606?text=")


def test_stripe_disabled_does_not_crash():
    payload = create_checkout_session(1)
    assert payload["status"] == "stub"
