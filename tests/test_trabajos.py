"""Busquedas de Apify (simulado) e IA (simulada): recuperacion y parseo."""
import json

import pytest
from sqlalchemy import func, select, update

from app import apify, crm, db, ia, trabajos


def item_maps(n, **extra):
    """Item con la forma real que devuelve compass/crawler-google-places."""
    base = {
        "title": f"Panadería La Espiga {n}", "categoryName": "Panadería",
        "address": f"Colón {n}, B7500 Tres Arroyos, Provincia de Buenos Aires",
        "city": "Madrid", "state": "Provincia de Buenos Aires",
        "phone": f"02983 42-{n:04d}", "phoneUnformatted": f"+5429834{n:05d}",
        "website": None, "url": f"https://www.google.com/maps/search/?api=1&query=x&query_place_id=ChIJ{n}",
        "placeId": f"ChIJ{n}", "totalScore": 4.6, "reviewsCount": 120 + n,
        "permanentlyClosed": False, "temporarilyClosed": False,
    }
    base.update(extra)
    return base


# ------------------------------------------------------------------ parseo
def test_normalizar_item_basico():
    c = apify.normalizar_item(item_maps(1), "Madrid", "Buenos Aires")
    assert c["negocio"] == "Panadería La Espiga 1"
    assert c["web"] == ""                     # el "url" de Maps NUNCA es la web
    assert c["place_id"] == "ChIJ1"
    assert c["ciudad"] == "Madrid"


def test_normalizar_item_cerrado_se_descarta():
    assert apify.normalizar_item(item_maps(1, permanentlyClosed=True), "X", "") is None
    assert apify.normalizar_item({"title": None}, "X", "") is None


def test_normalizar_item_contactos():
    c = apify.normalizar_item(item_maps(1, emails=["ventas@laespiga.com.ar", "x@example.com"],
                                        instagrams=["https://www.instagram.com/laespiga.ta/"],
                                        website="https://laespiga.com.ar"), "Madrid", "")
    assert c["email"] == "ventas@laespiga.com.ar"
    assert c["instagram"] == "laespiga.ta"
    assert c["web"] == "https://laespiga.com.ar"


def test_web_que_es_instagram_pasa_a_instagram():
    c = apify.normalizar_item(item_maps(1, website="https://instagram.com/kali.ta"), "Madrid", "")
    assert c["instagram"] == "kali.ta" and c["web"] == ""


def test_entrada_maps_ubicacion_separada():
    e = apify.entrada_maps("panaderías", "Córdoba", "Córdoba", 40, False)
    assert e["searchStringsArray"] == ["panaderías"]
    assert e["locationQuery"] == "Córdoba, Córdoba, España"
    assert e["countryCode"] == "es"
    assert e["skipClosedPlaces"] is False     # cobra extra; se filtra aca
    assert e["scrapeReviewsPersonalData"] is False


def test_provincia_de():
    assert apify.provincia_de("malaga") == "Málaga"
    assert apify.provincia_de("Ciudad Inventada") == ""


# ------------------------------------------------------ ciclo de busquedas
class ApifyFalso:
    def __init__(self, items, estado="SUCCEEDED"):
        self.items, self.estado, self.lanzadas, self.descargas = items, estado, 0, 0

    def lanzar(self, entrada):
        self.lanzadas += 1
        return {"run_id": f"run{self.lanzadas}", "dataset_id": f"ds{self.lanzadas}"}

    def estado_corrida(self, run_id):
        return {"estado": self.estado, "dataset_id": "ds1", "costo_usd": 0.16, "mensaje": ""}

    def items_dataset(self, dataset_id):
        self.descargas += 1
        return self.items


@pytest.fixture
def falso(monkeypatch):
    f = ApifyFalso([item_maps(i) for i in range(1, 6)] + [item_maps(99, permanentlyClosed=True)])
    monkeypatch.setattr(apify, "lanzar", f.lanzar)
    monkeypatch.setattr(apify, "estado_corrida", f.estado_corrida)
    monkeypatch.setattr(apify, "items_dataset", f.items_dataset)
    return f


def busqueda(motor, bid):
    with motor.connect() as conn:
        return dict(conn.execute(select(db.busquedas).where(db.busquedas.c.id == bid)).first()._mapping)


def test_busqueda_completa(motor, falso):
    with motor.begin() as conn:
        [bid] = trabajos.crear_busquedas(conn, "panaderías", [("Madrid", "Buenos Aires")], 10, False, "Mauro")
    trabajos.paso_busquedas(motor)            # lanza
    assert busqueda(motor, bid)["estado"] == "corriendo" and falso.lanzadas == 1
    trabajos.paso_busquedas(motor)            # termina e importa
    b = busqueda(motor, bid)
    assert b["estado"] == "importada"
    assert (b["nuevos"], b["repetidos"], b["descartados"]) == (5, 0, 1)
    assert b["costo_usd"] == 0.16
    assert json.loads(b["crudo"])             # el crudo quedo guardado


def test_reimportar_no_duplica_ni_descarga(motor, falso):
    with motor.begin() as conn:
        [bid] = trabajos.crear_busquedas(conn, "panaderías", [("Madrid", "Buenos Aires")], 10, False, None)
    trabajos.paso_busquedas(motor)
    trabajos.paso_busquedas(motor)
    c = trabajos.reimportar(motor, bid)
    assert c["nuevos"] == 0 and c["repetidos"] == 5
    assert falso.descargas == 1
    with motor.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.leads)).scalar_one() == 5


def test_reinicio_a_mitad_de_importacion_se_recupera(motor, falso):
    with motor.begin() as conn:
        [bid] = trabajos.crear_busquedas(conn, "panaderías", [("Madrid", "Buenos Aires")], 10, False, None)
    trabajos.paso_busquedas(motor)
    # Simula un corte: quedo 'importando'
    with motor.begin() as conn:
        conn.execute(update(db.busquedas).where(db.busquedas.c.id == bid).values(estado="importando"))
    trabajos.recuperar_al_iniciar(motor)
    trabajos.paso_busquedas(motor)
    assert busqueda(motor, bid)["estado"] == "importada"


def test_corrida_cortada_importa_lo_que_trajo(motor, falso):
    falso.estado = "TIMED-OUT"
    with motor.begin() as conn:
        [bid] = trabajos.crear_busquedas(conn, "gimnasios", [("Madrid", "Buenos Aires")], 10, False, None)
    trabajos.paso_busquedas(motor)
    trabajos.paso_busquedas(motor)
    b = busqueda(motor, bid)
    assert b["estado"] == "importada" and b["nuevos"] == 5
    assert "TIMED-OUT" in b["error"]


def test_falla_al_lanzar_queda_fallida(motor, monkeypatch):
    def romper(_):
        raise apify.ApifyError("Sin créditos en Apify (402).")
    monkeypatch.setattr(apify, "lanzar", romper)
    with motor.begin() as conn:
        [bid] = trabajos.crear_busquedas(conn, "bares", [("Madrid", "")], 10, False, None)
    trabajos.paso_busquedas(motor)
    b = busqueda(motor, bid)
    assert b["estado"] == "fallida" and "402" in b["error"]


def test_maximo_dos_corriendo(motor, falso):
    falso.estado = "RUNNING"
    with motor.begin() as conn:
        trabajos.crear_busquedas(conn, "bares", [("A", ""), ("B", ""), ("C", ""), ("D", "")], 10, False, None)
    trabajos.paso_busquedas(motor)
    trabajos.paso_busquedas(motor)
    assert falso.lanzadas == 2


def test_zona_va_en_la_ubicacion_pero_el_lead_queda_en_la_ciudad(motor, falso, monkeypatch):
    capturada = {}
    monkeypatch.setattr(apify, "lanzar", lambda entrada: capturada.update(entrada) or falso.lanzar(entrada))
    with motor.begin() as conn:
        [bid] = trabajos.crear_busquedas(conn, "boutique", [("Valencia", "Valencia")], 10, False,
                                         None, zona="Ruzafa")
    trabajos.paso_busquedas(motor)
    trabajos.paso_busquedas(motor)
    assert capturada["locationQuery"] == "Ruzafa, Valencia, Valencia, España"
    with motor.connect() as conn:
        ciudades = {r[0] for r in conn.execute(select(db.leads.c.ciudad))}
    assert ciudades == {"Valencia"}


# --------------------------------------------------------------------- IA
def test_ia_sin_key_no_hace_nada(motor, cfg):
    with motor.begin() as conn:
        db.bloquear(conn, "padron")
        crm.ingresar(conn, {"negocio": "X", "ciudad": "Y"}, cfg, fuente="maps")
    assert trabajos.paso_ia(motor) == 0


def test_ia_guardarrailes(motor, cfg, monkeypatch):
    with motor.begin() as conn:
        a = crm.ingresar(conn, {"negocio": "Panadería y confitería. Dulce Sabor 2", "ciudad": "Madrid"},
                         cfg, fuente="maps")["lead_id"]
        b = crm.ingresar(conn, {"negocio": "Sport Gym", "ciudad": "Madrid"}, cfg, fuente="maps")["lead_id"]
        c = crm.ingresar(conn, {"negocio": "Otro", "ciudad": "Madrid"}, cfg, fuente="maps")["lead_id"]

    def clasificar_falso(leads):
        # a: marca valida; b: marca INVENTADA; c: la IA no lo devolvio
        return {a: {"prioridad": "alta", "motivo_ia": "ok", "marca_ia": "Dulce Sabor"},
                b: {"prioridad": "media", "motivo_ia": "ok", "marca_ia": None}}
    monkeypatch.setattr(ia, "configurada", lambda: True)
    monkeypatch.setattr(ia, "clasificar", clasificar_falso)
    trabajos.paso_ia(motor)
    with motor.connect() as conn:
        la, lb, lc = crm.obtener(conn, a), crm.obtener(conn, b), crm.obtener(conn, c)
    assert la["marca_ia"] == "Dulce Sabor" and la["prioridad"] == "alta" and la["ia_estado"] == "ok"
    assert lb["marca_ia"] is None and lb["ia_estado"] == "ok"
    assert lc["ia_estado"] == "pendiente" and lc["ia_intentos"] == 1
    for _ in range(2):
        trabajos.paso_ia(motor)
    with motor.connect() as conn:
        assert crm.obtener(conn, c)["ia_estado"] == "error"     # no reintenta para siempre


def test_ia_clasificar_filtra_ids_y_marcas(monkeypatch):
    class Bloque:
        type = "text"
        text = json.dumps({"resultados": [
            {"id": "L1", "prioridad": "alta", "marca": "Iron Temple Fitness", "motivo": "x"},
            {"id": "INVENTADO", "prioridad": "alta", "marca": "Y", "motivo": "x"},
            {"id": "L2", "prioridad": "rarisima", "marca": "La Espiga", "motivo": "y"},
        ]})

    class Resp:
        stop_reason = "end_turn"
        content = [Bloque()]

    class Mensajes:
        def create(self, **kw):
            assert kw["output_config"]["format"]["type"] == "json_schema"
            return Resp()

    class Cliente:
        messages = Mensajes()
        beta = type("B", (), {"messages": Mensajes()})()

    monkeypatch.setattr(ia, "_cliente", lambda: Cliente())
    r = ia.clasificar([{"id": "L1", "negocio": "Sport Gym"}, {"id": "L2", "negocio": "Panadería La Espiga"}])
    assert set(r) == {"L1", "L2"}
    assert r["L1"]["marca_ia"] is None                       # inventada: descartada
    assert r["L2"]["marca_ia"] == "La Espiga"
    assert r["L2"]["prioridad"] == "media"                   # valor invalido -> media
