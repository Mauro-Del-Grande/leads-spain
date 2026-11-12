"""Rutas HTTP: login, proteccion, flujo de contacto completo."""
from sqlalchemy import update

from app import MAX_CIUDADES_BARRIDO, crm, db


def sembrar(app, **cand):
    motor = app.extensions["motor"]
    with motor.begin() as conn:
        cfg = db.leer_config(conn)
        db.bloquear(conn, "padron")
        base = {"negocio": "Kali Moda", "telefono_raw": "612 40 12 34",
                "instagram": "kali.mad", "ciudad": "Madrid", "rubro": "Tienda de ropa"}
        return crm.ingresar(conn, {**base, **cand}, cfg, fuente="maps")["lead_id"]


def test_sin_login_redirige_y_api_401(app):
    c = app.test_client()
    assert c.get("/").status_code == 302
    assert c.get("/api/hoy").status_code == 401


def test_login_incorrecto(app):
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        csrf = s["csrf"]
    r = c.post("/login", data={"email": "miqueas@test.com", "clave": "mala", "csrf": csrf})
    assert r.status_code == 200 and "incorrectos" in r.get_data(as_text=True)


def test_login_sin_csrf_falla(app):
    c = app.test_client()
    r = c.post("/login", data={"email": "miqueas@test.com", "clave": "clave-segura-123"})
    assert r.status_code == 200 and "venció" in r.get_data(as_text=True)


def test_escritura_sin_json_rechazada(cliente, app):
    lid = sembrar(app)
    r = cliente.post(f"/api/leads/{lid}/estado", data={"estado": "cliente"})
    assert r.status_code == 415


def test_flujo_completo_de_contacto(cliente, app):
    lid = sembrar(app)
    hoy = cliente.get("/api/hoy").get_json()
    assert [l["id"] for l in hoy["cola"]] == [lid]
    t = hoy["cola"][0]
    assert t["marca"] == "Kali" and t["medios"] == ["telefono", "instagram"]
    assert "mensajes" not in t and hoy["contactados_hoy"] == 0
    assert [k for k, _ in hoy["canales_contacto"]] == ["llamada", "visita", "carta", "respuesta", "otro"]

    # La app ya no prepara ni envia mensajes
    assert cliente.post("/api/contacto/preparar", json={"lead_id": lid, "canal": "whatsapp"}).status_code == 404
    assert cliente.post("/api/contacto/confirmar", json={"lead_id": lid, "canal": "whatsapp"}).status_code == 404

    assert cliente.post("/api/contacto/registrar", json={"lead_id": lid, "canal": "whatsapp"}).status_code == 409
    r = cliente.post("/api/contacto/registrar",
                     json={"lead_id": lid, "canal": "llamada", "nota": "Llamar en enero"}).get_json()
    assert r["ya_registrado"] is False and r["contactados_hoy"] == 1
    r2 = cliente.post("/api/contacto/registrar", json={"lead_id": lid, "canal": "visita"}).get_json()
    assert r2["ya_registrado"] is True
    assert cliente.get("/api/hoy").get_json()["cola"] == []

    ficha = cliente.get(f"/api/leads/{lid}").get_json()
    assert ficha["lead"]["estado"] == "contactado"
    assert ficha["contacto"]["canal"] == "llamada" and ficha["contacto"]["texto"] == "Llamar en enero"
    assert ficha["contacto"]["usuario"] == "Miqueas"


def test_estado_y_notas(cliente, app):
    lid = sembrar(app)
    assert cliente.post(f"/api/leads/{lid}/estado", json={"estado": "respondio"}).status_code == 200
    r = cliente.patch(f"/api/leads/{lid}", json={"proxima_accion": "mandar presupuesto", "proxima_fecha": "2020-01-01"})
    assert r.status_code == 200
    seg = cliente.get("/api/hoy").get_json()["seguimientos"]
    assert [s["id"] for s in seg] == [lid]
    assert cliente.post(f"/api/leads/{lid}/nota", json={"texto": "llamar el lunes"}).status_code == 200
    h = cliente.get(f"/api/leads/{lid}").get_json()["historial"]
    assert h[0]["tipo"] == "nota"


def test_fecha_invalida(cliente, app):
    lid = sembrar(app)
    r = cliente.patch(f"/api/leads/{lid}", json={"proxima_fecha": "mañana"})
    assert r.status_code == 409


def test_busqueda_pide_confirmacion_y_avisa_repetida(cliente, app, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "x")
    datos = {"rubro": "panaderías", "ciudades": [{"ciudad": "Getafe", "provincia": "Madrid"}], "limite": 20}
    pre = cliente.post("/api/busquedas", json=datos).get_json()
    assert pre["confirmar"] and pre["costo_estimado_usd"] == 0.08 and pre["repetidas"] == []
    ok = cliente.post("/api/busquedas", json={**datos, "confirmar": True}).get_json()
    assert len(ok["ids"]) == 1
    with app.extensions["motor"].begin() as conn:            # termino la corrida
        conn.execute(update(db.busquedas).values(estado="importada", encontrados=20, nuevos=20))
    pre2 = cliente.post("/api/busquedas", json=datos).get_json()
    assert pre2["repetidas"][0]["ciudad"] == "Getafe"
    lista = cliente.get("/api/busquedas").get_json()["busquedas"]
    assert "crudo" not in lista[0]


def test_busqueda_validaciones(cliente, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "x")
    assert cliente.post("/api/busquedas", json={"rubro": "", "ciudades": ["X"]}).status_code == 400
    assert cliente.post("/api/busquedas", json={"rubro": "bares", "ciudades": []}).status_code == 400
    assert cliente.post("/api/busquedas", json={"rubro": "bares", "ciudades": ["X"], "limite": 999}).status_code == 400
    muchas = [{"ciudad": f"Pueblo {i}"} for i in range(MAX_CIUDADES_BARRIDO + 1)]
    assert cliente.post("/api/busquedas", json={"rubro": "bares", "ciudades": muchas}).status_code == 400


def test_catalogos_de_busqueda(cliente):
    c = cliente.get("/api/ciudades").get_json()
    assert len(c["ciudades"]) >= 200 and c["max_barrido"] == MAX_CIUDADES_BARRIDO
    una = next(x for x in c["ciudades"] if x["ciudad"] == "Valencia")
    assert una == {"ciudad": "Valencia", "provincia": "Valencia", "principal": True}
    assert any(not x["principal"] for x in c["ciudades"])

    f = cliente.get("/api/rubros").get_json()["familias"]
    assert f[0]["nombre"] == "Frecuentes" and "tiendas de ropa" in f[0]["rubros"]
    assert all(x["rubros"] for x in f)


def test_config(cliente):
    r = cliente.post("/api/config", json={"ciudad_default": "Sevilla", "tope_whatsapp": 6}).get_json()
    assert r["config"]["ciudad_default"] == "Sevilla" and "tope_whatsapp" not in r["config"]
    assert "plantillas" not in cliente.get("/api/config").get_json()
    assert cliente.post("/api/plantillas/whatsapp", json={"cuerpo": "x"}).status_code == 404
    assert cliente.post("/api/canales/whatsapp/restriccion", json={}).status_code == 404


def test_exportes(cliente, app):
    sembrar(app, negocio="Café Ñandú")
    csv = cliente.get("/api/exportar.csv")
    assert csv.status_code == 200 and csv.data.startswith("﻿".encode())
    assert "Café Ñandú" in csv.data.decode("utf-8")
    js = cliente.get("/api/exportar.json").get_json()
    assert len(js["leads"]) == 1 and js["claves"]


def test_metricas(cliente, app):
    lid = sembrar(app)
    cliente.post("/api/contacto/registrar", json={"lead_id": lid, "canal": "llamada"})
    cliente.post(f"/api/leads/{lid}/estado", json={"estado": "cliente"})
    m = cliente.get("/api/metricas").get_json()
    assert m["clientes"] == 1 and m["contactados"] == 1 and m["tasa_respuesta"] == 100.0


def test_xss_en_nombre_se_guarda_crudo(cliente, app):
    """El escape lo hace la UI (esc()); la API devuelve el dato tal cual, como JSON."""
    lid = sembrar(app, negocio="<img src=x onerror=alert(1)>")
    r = cliente.get(f"/api/leads/{lid}")
    assert r.content_type.startswith("application/json")


def test_usuarios(cliente):
    assert cliente.post("/api/usuarios", json={"email": "mauro@test.com", "nombre": "Mauro", "clave": "corta"}).status_code == 400
    assert cliente.post("/api/usuarios", json={"email": "mauro@test.com", "nombre": "Mauro", "clave": "otra-clave-larga"}).status_code == 200
    assert cliente.post("/api/usuarios", json={"email": "mauro@test.com", "nombre": "Mauro", "clave": "otra-clave-larga"}).status_code == 400


def test_usuario_inicial_desde_entorno(tmp_path, monkeypatch):
    from app import create_app
    monkeypatch.setattr("app._cargar_env_local", lambda: None)
    monkeypatch.setenv("USUARIO_INICIAL_EMAIL", "mauro@test.com")
    monkeypatch.setenv("USUARIO_INICIAL_CLAVE", "clave-larguisima-1")
    url = f"sqlite:///{(tmp_path / 'u.db').as_posix()}"
    a = create_app({"DATABASE_URL": url, "TRABAJOS": False})
    create_app({"DATABASE_URL": url, "TRABAJOS": False})          # segundo arranque: no duplica
    from app import auth
    with a.extensions["motor"].connect() as conn:
        assert auth.verificar(conn, "mauro@test.com", "clave-larguisima-1")
        assert conn.execute(__import__("sqlalchemy").text("select count(*) from usuarios")).scalar() == 1


def test_salud(app):
    assert app.test_client().get("/salud").get_json() == {"ok": True}
