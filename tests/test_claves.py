"""Claves propias (Apify / Anthropic) cargadas desde Ajustes."""
import pytest
from sqlalchemy import select

from app import apify, claves, db, ia

APIFY = "apify_api_" + "x" * 30
ANTH = "sk-ant-" + "y" * 40


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claves._cache.clear()
    claves._ilegibles.clear()
    yield
    claves._cache.clear()


@pytest.fixture
def pruebas_ok(monkeypatch):
    monkeypatch.setattr(apify, "probar_token", lambda token=None: "renata")
    monkeypatch.setattr(ia, "probar", lambda api_key=None: "ok (claude)")


def test_guardar_cifra_y_nunca_devuelve_la_clave(cliente, app, pruebas_ok):
    r = cliente.post("/api/claves/apify", json={"clave": f'  "{APIFY}" '})
    assert r.status_code == 200 and "renata" in r.get_json()["ok"]
    estado = cliente.get("/api/claves").get_json()["claves"]["apify"]
    assert estado == {"nombre": "Apify", "configurada": True, "origen": "app",
                      "termina_en": APIFY[-4:], "ilegible": False}
    assert APIFY not in cliente.get("/api/claves").get_data(as_text=True)
    assert apify.configurado() and claves.valor("apify") == APIFY
    with app.extensions["motor"].connect() as conn:
        guardado = conn.execute(select(db.config.c.valor).where(db.config.c.clave == "clave:apify")).scalar_one()
        eventos = [dict(e._mapping) for e in conn.execute(select(db.eventos))]
    assert APIFY not in guardado                      # en la base va cifrada
    assert not any(APIFY in (e["datos"] or "") for e in eventos)   # y nunca en el historial


def test_clave_que_falla_no_se_guarda(cliente, monkeypatch):
    def falla(token=None):
        raise apify.ApifyError("Apify rechazó la clave (401).")
    monkeypatch.setattr(apify, "probar_token", falla)
    r = cliente.post("/api/claves/apify", json={"clave": APIFY})
    assert r.status_code >= 400 and "rechazó" in r.get_json()["error"]
    assert not cliente.get("/api/claves").get_json()["claves"]["apify"]["configurada"]


@pytest.mark.parametrize("mala", ["", "corta", "con espacios " + "x" * 30])
def test_validacion_basica(cliente, pruebas_ok, mala):
    assert cliente.post("/api/claves/anthropic", json={"clave": mala}).status_code == 400


def test_servicio_desconocido(cliente):
    assert cliente.post("/api/claves/openai", json={"clave": ANTH}).status_code == 404


def test_sobrevive_a_un_reinicio_y_se_puede_quitar(cliente, app, pruebas_ok):
    cliente.post("/api/claves/anthropic", json={"clave": ANTH})
    claves._cache.clear()
    claves.cargar(app.extensions["motor"])            # como al arrancar
    assert ia.configurada() and claves.valor("anthropic") == ANTH
    r = cliente.delete("/api/claves/anthropic", json={}).get_json()
    assert r["claves"]["anthropic"]["configurada"] is False
    assert not ia.configurada()


def test_la_de_la_app_gana_y_la_del_servidor_es_respaldo(cliente, pruebas_ok, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_del_servidor_" + "z" * 10)
    e = cliente.get("/api/claves").get_json()["claves"]["apify"]
    assert e["configurada"] and e["origen"] == "servidor"
    cliente.post("/api/claves/apify", json={"clave": APIFY})
    assert claves.valor("apify") == APIFY
    cliente.delete("/api/claves/apify", json={})
    assert claves.valor("apify").startswith("apify_api_del_servidor_")


def test_si_cambia_secret_key_avisa_que_hay_que_recargarla(cliente, app, pruebas_ok, monkeypatch):
    cliente.post("/api/claves/apify", json={"clave": APIFY})
    monkeypatch.setenv("SECRET_KEY", "otra-clave-distinta")
    claves.cargar(app.extensions["motor"])
    e = cliente.get("/api/claves").get_json()["claves"]["apify"]
    assert e["ilegible"] and not e["configurada"]


def test_busqueda_sin_clave_explica_donde_cargarla(cliente):
    r = cliente.post("/api/busquedas", json={"rubro": "bares", "ciudades": ["Madrid"]})
    assert r.status_code == 400 and "Ajustes" in r.get_json()["error"]
