"""
Fixtures. Por defecto cada test usa un SQLite nuevo.

Para correr la MISMA suite contra Postgres (ej. una base de prueba en Railway):
    TEST_DATABASE_URL=postgresql://... pytest
OJO: borra todas las tablas de esa base antes de cada test. Nunca apuntarla a
la base de produccion.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Nunca tocar credenciales reales durante los tests
for var in ("APIFY_TOKEN", "ANTHROPIC_API_KEY", "DATABASE_URL", "RAILWAY_ENVIRONMENT", "PRODUCCION",
            "USUARIO_INICIAL_EMAIL", "USUARIO_INICIAL_CLAVE"):
    os.environ.pop(var, None)
os.environ["TRABAJOS"] = "0"

from app import create_app, db  # noqa: E402
from app import auth  # noqa: E402

PG = os.environ.get("TEST_DATABASE_URL", "").strip()


def _url(tmp_path, nombre):
    if not PG:
        return f"sqlite:///{(tmp_path / nombre).as_posix()}"
    url = PG
    for viejo in ("postgres://", "postgresql://"):
        if url.startswith(viejo):
            url = "postgresql+psycopg://" + url[len(viejo):]
    m = db.crear_motor(url)
    db.meta.drop_all(m)
    m.dispose()
    return url


@pytest.fixture
def motor(tmp_path):
    m = db.crear_motor(_url(tmp_path, "test.db"))
    db.inicializar(m)
    yield m
    m.dispose()


@pytest.fixture
def cfg(motor):
    with motor.connect() as conn:
        return db.leer_config(conn)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("app._cargar_env_local", lambda: None)
    a = create_app({"DATABASE_URL": _url(tmp_path, "app.db"), "TRABAJOS": False, "TESTING": True})
    with a.extensions["motor"].begin() as conn:
        auth.crear_usuario(conn, "miqueas@test.com", "Miqueas", "clave-segura-123")
    yield a
    a.extensions["motor"].dispose()


@pytest.fixture
def cliente(app):
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        csrf = s["csrf"]
    r = c.post("/login", data={"email": "miqueas@test.com", "clave": "clave-segura-123", "csrf": csrf})
    assert r.status_code == 302
    return c
