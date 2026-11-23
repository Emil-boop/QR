import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    database = tmp_path / "test.db"
    qr_dir = tmp_path / "qr"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("QR_CODE_DIR", str(qr_dir))

    import app as app_module

    app_module.app.config.update(TESTING=True)
    return app_module


@pytest.fixture()
def client(app_module):
    return app_module.app.test_client()


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_create_lend_and_return_book(client):
    response = client.post(
        "/qr-erstellen",
        data={
            "titel": "Testbuch",
            "isbn": "978-1234567890",
            "regal": "A1",
            "hintergrund": "Weiß",
        },
    )
    assert response.status_code == 200

    depot = client.get("/depot")
    assert b"Testbuch" in depot.data

    database = client.application.extensions
    from app import get_db

    with client.application.app_context():
        book = get_db().execute(
            "SELECT id FROM buecher WHERE titel = ?", ("Testbuch",)
        ).fetchone()
        book_id = book["id"]

    response = client.post(
        f"/buecher/{book_id}/ausleihen",
        data={"ausleiher": "Emil", "rueckgabedatum": "2026-12-31"},
    )
    assert response.status_code == 302

    response = client.post(f"/buecher/{book_id}/zurueckgeben")
    assert response.status_code == 302
