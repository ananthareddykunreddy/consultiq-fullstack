from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_ok():
    response = client.get("/")
    assert response.status_code == 200


def test_services_ok():
    response = client.get("/services")
    assert response.status_code == 200


def test_login_page_ok():
    response = client.get("/login")
    assert response.status_code == 200


def test_health_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_password_reset_request_page_ok():
    response = client.get("/password-reset-request")
    assert response.status_code == 200


def test_contact_booking_form_has_document_upload():
    response = client.get("/contact")
    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="document"' in response.text
