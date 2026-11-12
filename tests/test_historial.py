"""Historial de busquedas y analisis previo (para no pagar dos veces lo mismo)."""
from collections import Counter

from sqlalchemy import update

from app import apify, db, historial, trabajos
from app.historial import analizar, normalizar_termino, variantes_de, zonas_de


def busqueda(motor, rubro, ciudad, limite=40, zona="", encontrados=40, nuevos=40, estado="importada", costo=0.16):
    with motor.begin() as conn:
        [bid] = trabajos.crear_busquedas(conn, rubro, [(ciudad, "")], limite, False, "Mauro", zona)
        conn.execute(update(db.busquedas).where(db.busquedas.c.id == bid).values(
            estado=estado, encontrados=encontrados, nuevos=nuevos, costo_usd=costo))
    return bid


def test_normalizar_termino():
    assert normalizar_termino("Panaderías") == normalizar_termino("panaderia") == normalizar_termino(" PANADERIAS ")
    assert normalizar_termino("Gimnasios") == "gimnasio"
    assert normalizar_termino("Valencia") == normalizar_termino("valencia")


def test_variantes_y_zonas():
    assert "boutique" in variantes_de("Tiendas de ropa")
    assert "crossfit" in variantes_de("gimnasios")
    assert variantes_de("barberías") and "cervecería" not in variantes_de("barberías")   # "bar" != "barberia"
    assert variantes_de("algo rarísimo") == []
    assert "Ruzafa" in zonas_de("Valencia")
    assert "Malasaña" in zonas_de("Madrid")
    assert zonas_de("Teruel") == []


def test_claves_de_variantes_estan_normalizadas():
    """Una clave sin normalizar (ej "hoteles") nunca matchea: seria letra muerta."""
    for clave in historial.VARIANTES:
        assert normalizar_termino(clave) == clave, clave
    # y los plurales que escribe el usuario tienen que caer en esas claves
    assert "hostal" in variantes_de("hoteles")
    assert "decoración" in variantes_de("tiendas de muebles")
    assert "parrilla" in variantes_de("Asadores")


def test_catalogo_de_rubros():
    rubros = [r for lista in historial.RUBROS.values() for r in lista]
    assert len(historial.RUBROS) >= 10 and len(rubros) >= 120
    assert list(historial.RUBROS)[0] == "Frecuentes"          # la familia que se ve al abrir
    for familia, lista in historial.RUBROS.items():
        assert lista, familia
        assert len(set(lista)) == len(lista), familia         # sin repetidos dentro de una familia
    # la mayoria tiene que ofrecer variantes: es lo que evita repetir la busqueda
    assert sum(1 for r in rubros if variantes_de(r)) >= len(rubros) * 0.7


def test_ciudades_sin_nombres_repetidos():
    """provincia_de resuelve por nombre y gana el primero: dos "Mercedes" mienten."""
    repetidas = [c for c, n in Counter(c for c, _ in apify.CIUDADES).items() if n > 1]
    assert repetidas == []
    assert len(apify.CIUDADES) >= 200
    assert len({p for _, p in apify.CIUDADES}) == 52       # 50 provincias + Ceuta y Melilla
    assert apify.provincia_de("getafe") == "Madrid"


def test_principales_son_ciudades_reales_y_entran_en_un_barrido():
    from app import MAX_CIUDADES_BARRIDO
    nombres = {c for c, _ in apify.CIUDADES}
    assert apify.PRINCIPALES <= nombres
    assert 0 < len(apify.PRINCIPALES) <= MAX_CIUDADES_BARRIDO
    assert len({apify.provincia_de(c) for c in apify.PRINCIPALES}) == 52   # llega a todo el pais


def test_una_zona_no_es_otra_ciudad_de_la_misma_provincia():
    """Zona que ademas es ciudad de la lista = la misma corrida pagada dos veces."""
    provincia = dict(apify.CIUDADES)
    for ciudad, zonas in historial.ZONAS.items():
        prov = provincia.get(ciudad, "")
        for z in zonas:
            assert not (z in provincia and provincia[z] == prov), (ciudad, z)


def test_historial_agrupa_misma_busqueda_escrita_distinto(motor):
    busqueda(motor, "Ropa", "Valencia", 40, encontrados=40, nuevos=40)
    busqueda(motor, "ropas", "valencia", 100, encontrados=100, nuevos=55)
    busqueda(motor, "boutique", "Valencia", 40, encontrados=40, nuevos=30)
    with motor.connect() as conn:
        h = historial.historial(conn)
    assert len(h) == 2
    ind = next(g for g in h if normalizar_termino(g["rubro"]) == "ropa")
    assert ind["veces"] == 2 and ind["max_limite"] == 100
    assert ind["encontrados"] == 140 and ind["nuevos"] == 95
    assert ind["rendimiento_ultima"] == 55.0 and ind["estado"] == "rinde"
    assert round(ind["costo_usd"], 2) == 0.32


def test_estados_de_rendimiento(motor):
    busqueda(motor, "a", "X", encontrados=40, nuevos=4)          # 10% -> agotada
    busqueda(motor, "b", "X", encontrados=40, nuevos=10)         # 25% -> bajando
    busqueda(motor, "c", "X", estado="corriendo", encontrados=0, nuevos=0)
    with motor.connect() as conn:
        estados = {g["rubro"]: g["estado"] for g in historial.historial(conn)}
    assert estados == {"a": "agotada", "b": "bajando", "c": "en_curso"}


def test_analizar_repetida_misma_cantidad_no_trae_nada(motor):
    busqueda(motor, "Ropa", "Valencia", 40)
    with motor.connect() as conn:
        a = analizar(conn, "ropa", "valencia", "", 40)
        b = analizar(conn, "ropa", "valencia", "", 100)
        c = analizar(conn, "lencería", "valencia", "", 40)
        d = analizar(conn, "ropa", "valencia", "Ruzafa", 40)
    assert a["aviso"] == "sin_novedad" and a["nuevos_esperados"] == 0
    assert b["aviso"] == "ampliar" and b["nuevos_esperados"] == 60
    assert c["misma"] is None and c["aviso"] == ""                 # variante: no es repetida
    assert d["misma"] is None                                      # otra zona: no es repetida
    assert any(v["texto"] == "boutique" and not v["ya_buscado"] for v in a["variantes"])
    assert a["zonas"] and not a["zonas"][0]["ya_buscado"]


def test_analizar_marca_variantes_y_zonas_ya_buscadas(motor):
    busqueda(motor, "boutique", "Valencia")
    busqueda(motor, "ropa", "Valencia", zona="Ruzafa")
    with motor.connect() as conn:
        a = analizar(conn, "ropa", "Valencia", "", 40)
    boutique = next(v for v in a["variantes"] if v["texto"] == "boutique")
    ruzafa = next(z for z in a["zonas"] if z["texto"] == "Ruzafa")
    assert boutique["ya_buscado"] and ruzafa["ya_buscado"]
    assert a["variantes"][-1]["ya_buscado"]                         # las ya buscadas van al final


def test_api_confirmacion_informa_repetida_y_bloquea_en_curso(cliente, app, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "x")
    datos = {"rubro": "gimnasios", "ciudades": [{"ciudad": "Getafe", "provincia": "Madrid"}], "limite": 20}
    assert cliente.post("/api/busquedas", json={**datos, "confirmar": True}).status_code == 200
    r = cliente.post("/api/busquedas", json=datos)
    assert r.status_code == 409 and "en marcha" in r.get_json()["error"]
    with app.extensions["motor"].begin() as conn:
        conn.execute(update(db.busquedas).values(estado="importada", encontrados=20, nuevos=20))
    pre = cliente.post("/api/busquedas", json=datos).get_json()
    assert pre["repetidas"][0]["aviso"] == "sin_novedad"
    pre2 = cliente.post("/api/busquedas", json={**datos, "limite": 60}).get_json()
    assert pre2["repetidas"][0]["aviso"] == "ampliar" and pre2["repetidas"][0]["nuevos_esperados"] == 40
    an = cliente.get("/api/busquedas/analizar?rubro=Gimnasio&ciudad=getafe&limite=20").get_json()
    assert an["misma"]["veces"] == 1 and "crossfit" in [v["texto"] for v in an["variantes"]]
    h = cliente.get("/api/busquedas/historial").get_json()["historial"]
    assert h[0]["rubro"] == "gimnasios"


def test_migracion_agrega_zona(tmp_path):
    from sqlalchemy import inspect, text
    m = db.crear_motor(f"sqlite:///{(tmp_path / 'v.db').as_posix()}")
    db.inicializar(m)
    with m.begin() as conn:
        conn.execute(text("ALTER TABLE busquedas DROP COLUMN zona"))
        conn.execute(text("DELETE FROM schema_version WHERE version = 3"))
    db.inicializar(m)
    assert "zona" in {c["name"] for c in inspect(m).get_columns("busquedas")}
