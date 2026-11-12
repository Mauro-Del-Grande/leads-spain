"""Cruce con la EUIPO: parecido de nombres, semaforo, credenciales y trabajo de fondo."""
import json

import pytest
from sqlalchemy import update

from app import claves, crm, db, registros, trabajos

CRED = json.dumps({"client_id": "cliente-123456", "client_secret": "secreto-abcdefgh"})


def tm(nombre, clases, estado="REGISTERED", numero=None):
    return {"applicationNumber": numero or f"0{abs(hash(nombre)) % 10**8:08d}",
            "wordMarkSpecification": {"verbalElement": nombre}, "niceClasses": clases,
            "status": estado, "applicants": [{"name": "Titular SA", "office": "EM"}],
            "applicationDate": "2020-01-01", "markFeature": "WORD"}


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    monkeypatch.delenv("EUIPO_CREDENCIALES", raising=False)
    claves._cache.clear()
    registros._token.clear()
    yield
    claves._cache.clear()


# ----------------------------------------------------------------- parecido
@pytest.mark.parametrize("a,b,tipo", [
    ("Kali", "KALI", "idéntica"),
    ("La Espiga S.L.", "Espiga", "idéntica"),
    ("Cali", "Kaly", "suena igual"),
    ("Zeta", "Seta", "suena igual"),
    ("Vacanal", "Bakanal", "suena igual"),
    ("Ironside", "Ironsyde", "suena igual"),
    ("Marbellas", "Marbella", "muy parecida"),
    ("Nandu", "Nandu Music Records", "la contiene"),
])
def test_parecido(a, b, tipo):
    assert registros.parecido(a, b)[1] == tipo


def test_nada_que_ver():
    assert registros.parecido("Kali", "Telefonica")[1] == ""


def test_clases_por_rubro():
    assert registros.clases_para("Discoteca") == {41, 43}
    assert 43 in registros.clases_para("Puestos de mercado gastronómico")
    assert {9, 41} <= registros.clases_para("Sello discográfico")
    assert registros.clases_para("algo rarísimo") == set()


# ----------------------------------------------------------------- semaforo
def test_rojo_si_es_igual_y_comparte_clase():
    r = registros.evaluar("Bakanal", "Discoteca", [tm("VACANAL", [41]), tm("BAKANAL", [9])])
    assert r["nivel"] == "rojo"
    assert r["coincidencias"][0]["denominacion"] == "VACANAL"
    assert r["coincidencias"][0]["clases_en_comun"] == [41]


def test_amarillo_si_es_igual_en_otra_clase():
    r = registros.evaluar("Bakanal", "Discoteca", [tm("BAKANAL", [9, 25])])
    assert r["nivel"] == "amarillo"


def test_las_inactivas_no_cuentan():
    r = registros.evaluar("Bakanal", "Discoteca", [tm("BAKANAL", [41], "EXPIRED"), tm("BAKANAL", [41], "REFUSED")])
    assert r["nivel"] == "verde" and r["coincidencias"] == []


def test_verde_sin_parecidos_y_sin_duplicados():
    r = registros.evaluar("Kali", "Tienda de ropa", [tm("TELEFONICA", [38]), tm("ZARA", [25])])
    assert r["nivel"] == "verde"
    r2 = registros.evaluar("Kali", "Ropa", [tm("KALI", [25], numero="1"), tm("KALI", [25], numero="1")])
    assert r2["total"] == 1


def test_consultas_seguras():
    q = registros.consultas_para('La "Tasca" de Pepe*')
    assert q == ['wordMarkSpecification.verbalElement=="*La Tasca de Pepe*"',
                 "wordMarkSpecification.verbalElement==*Tasca*"]
    assert registros.consultas_para("Kali") == ["wordMarkSpecification.verbalElement==*Kali*"]


# ----------------------------------------------------------------- HTTP falso
class Resp:
    def __init__(self, status, datos):
        self.status_code, self._d, self.text = status, datos, json.dumps(datos)
        self.ok = 200 <= status < 300

    def json(self):
        return self._d


@pytest.fixture
def euipo(monkeypatch):
    llamadas = {"token": 0, "buscar": []}

    def post(url, data=None, timeout=None):
        llamadas["token"] += 1
        if data["client_secret"] != "secreto-abcdefgh":
            return Resp(401, {"error": "invalid_client"})
        assert data["grant_type"] == "client_credentials" and data["scope"] == "uid"
        return Resp(200, {"access_token": "tok", "expires_in": 3600})

    def get(url, headers=None, params=None, timeout=None):
        assert headers["X-IBM-Client-Id"] == "cliente-123456" and headers["Authorization"] == "Bearer tok"
        llamadas["buscar"].append(params["query"])
        return Resp(200, {"trademarks": [tm("VACANAL", [41])], "totalElements": 1})

    monkeypatch.setattr(registros.requests, "post", post)
    monkeypatch.setattr(registros.requests, "get", get)
    return llamadas


def test_guardar_credenciales_euipo(cliente, euipo):
    r = cliente.post("/api/claves/euipo", json={"client_id": " cliente-123456 ", "client_secret": "secreto-abcdefgh"})
    assert r.status_code == 200 and "EUIPO conectada" in r.get_json()["ok"]
    e = r.get_json()["claves"]["euipo"]
    assert e["configurada"] and e["termina_en"] == "3456"
    assert "secreto" not in cliente.get("/api/claves").get_data(as_text=True)
    assert registros.configurado()


def test_credenciales_malas_no_se_guardan(cliente, euipo):
    r = cliente.post("/api/claves/euipo", json={"client_id": "cliente-123456", "client_secret": "otra-cosa-mala"})
    assert r.status_code == 502 and "rechazó" in r.get_json()["error"]
    assert not registros.configurado()
    assert cliente.post("/api/claves/euipo", json={"client_id": "x", "client_secret": ""}).status_code == 400


def _lead(app, **kw):
    motor = app.extensions["motor"]
    with motor.begin() as conn:
        cfg = db.leer_config(conn)
        db.bloquear(conn, "padron")
        lid = crm.ingresar(conn, {"negocio": "Bakanal Club", "rubro": "Discoteca", "ciudad": "Madrid",
                                  "telefono_raw": "612 34 56 78", **kw}, cfg, fuente="maps")["lead_id"]
        conn.execute(update(db.leads).where(db.leads.c.id == lid).values(
            prioridad="alta", ia_estado="ok", marca_ia="Bakanal"))
    return lid


def test_revisar_desde_la_ficha(cliente, app, euipo):
    lid = _lead(app)
    assert cliente.get(f"/api/leads/{lid}").get_json()["euipo"] is False
    claves._cache["euipo"] = CRED
    r = cliente.post(f"/api/leads/{lid}/registros", json={}).get_json()["registro"]
    assert r["nivel"] == "rojo" and r["resultado"]["coincidencias"][0]["denominacion"] == "VACANAL"
    ficha = cliente.get(f"/api/leads/{lid}").get_json()
    assert ficha["registro"]["nivel"] == "rojo" and ficha["enlaces_registro"]["oepm"]
    hoy = cliente.get("/api/hoy").get_json()
    assert hoy["cola"][0]["registro_nivel"] == "rojo"
    assert any(e["tipo"] == "registros" for e in ficha["historial"])


def test_revisar_sin_credenciales_explica(cliente, app):
    lid = _lead(app)
    r = cliente.post(f"/api/leads/{lid}/registros", json={})
    assert r.status_code == 502 and "Ajustes" in r.get_json()["error"]


def test_trabajo_de_fondo_revisa_una_vez_y_reintenta_si_cambia_la_marca(app, euipo):
    motor = app.extensions["motor"]
    lid = _lead(app)
    assert trabajos.paso_registros(motor) == 0          # sin credenciales no hace nada
    claves._cache["euipo"] = CRED
    assert trabajos.paso_registros(motor) == 1
    assert trabajos.paso_registros(motor) == 0          # ya revisado
    token_antes = euipo["token"]
    with motor.begin() as conn:
        crm.editar(conn, lid, {"marca_manual": "Bakanal Madrid"}, "R", db.leer_config(conn))
    assert trabajos.paso_registros(motor) == 1          # cambio la marca: se revisa otra vez
    assert euipo["token"] == token_antes                 # el token se reutiliza


def test_contactados_y_baja_prioridad_no_se_revisan(app, euipo):
    motor = app.extensions["motor"]
    claves._cache["euipo"] = CRED
    lid = _lead(app)
    with motor.begin() as conn:
        conn.execute(update(db.leads).where(db.leads.c.id == lid).values(prioridad="baja"))
    assert trabajos.paso_registros(motor) == 0
