"""El nucleo del negocio: sin duplicados, sin doble contacto, sin perder datos."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, update

from app import crm, db
from app.crm import ErrorCRM


def contar(conn, tabla):
    return conn.execute(select(func.count()).select_from(tabla)).scalar_one()


def ingresar(motor, cfg, cand, fuente="maps", **kw):
    with motor.begin() as conn:
        db.bloquear(conn, "padron")
        return crm.ingresar(conn, cand, cfg, fuente=fuente, **kw)


def lead(motor, lead_id):
    with motor.connect() as conn:
        return crm.obtener(conn, lead_id)


def registrar(motor, lead_id, canal="llamada", nota="", usuario="Renata"):
    with motor.begin() as conn:
        return crm.registrar_contacto(conn, lead_id, canal, nota, usuario)


# ------------------------------------------------------------------ altas
def test_mismo_negocio_dos_fuentes_un_solo_lead(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "La Espiga", "instagram": "@laespiga.mad", "ciudad": "Madrid"})
    b = ingresar(motor, cfg, {"negocio": "Panadería La Espiga S.L.", "telefono_raw": "91 123 45 67",
                              "ciudad": "Madrid", "web": "www.laespiga.es", "instagram": "laespiga.mad"})
    assert a["resultado"] == "nuevo" and b["resultado"] == "repetido"
    assert a["lead_id"] == b["lead_id"]
    l = lead(motor, a["lead_id"])
    assert l["telefono_e164"] == "+34911234567"
    with motor.connect() as conn:
        assert contar(conn, db.leads) == 1


def test_mismo_nombre_y_ciudad_es_el_mismo(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Panadería La Espiga", "ciudad": "Madrid"})
    b = ingresar(motor, cfg, {"negocio": "La Espiga S.L.", "ciudad": "Madrid",
                              "telefono_raw": "+34 612 34 56 78"})
    assert a["lead_id"] == b["lead_id"]


def test_rescrape_no_duplica(motor, cfg):
    lote = [{"negocio": f"Negocio {i}", "telefono_raw": f"6123400{i:02d}", "ciudad": "Valencia",
             "place_id": f"pid{i}"} for i in range(10)]
    r1 = [ingresar(motor, cfg, c)["resultado"] for c in lote]
    r2 = [ingresar(motor, cfg, c)["resultado"] for c in lote]
    assert r1.count("nuevo") == 10 and r2.count("nuevo") == 0
    with motor.connect() as conn:
        assert contar(conn, db.leads) == 10


def test_sin_web_no_se_fusionan(motor, cfg):
    """Regresion: Maps devuelve google.com/maps como url cuando no hay web."""
    for i, n in enumerate(["La Industrial", "La Lonja", "La Rosa"]):
        ingresar(motor, cfg, {"negocio": n, "ciudad": "Sevilla", "telefono_raw": f"95411111{i}",
                              "web": f"https://www.google.com/maps/search/?api=1&query={n}"})
    with motor.connect() as conn:
        assert contar(conn, db.leads) == 3


def test_mismo_nombre_otra_ciudad_son_distintos(motor, cfg):
    for ciudad in ("Madrid", "Barcelona", "Valencia", "Sevilla", "Bilbao"):
        ingresar(motor, cfg, {"negocio": "Panadería La Rosa", "ciudad": ciudad})
    with motor.connect() as conn:
        assert contar(conn, db.leads) == 5


def test_mismo_telefono_con_y_sin_prefijo(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Bar Uno", "telefono_raw": "612 34 56 78", "ciudad": "Madrid"})
    b = ingresar(motor, cfg, {"negocio": "Otro nombre", "telefono_raw": "0034612345678", "ciudad": "Getafe"})
    assert a["lead_id"] == b["lead_id"]


def test_candidato_que_une_dos_leads_los_fusiona(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Ferretería Sur", "telefono_raw": "954111222", "ciudad": "Sevilla"})
    b = ingresar(motor, cfg, {"negocio": "Fesur", "instagram": "fesur.sev", "ciudad": "Cádiz"})
    c = ingresar(motor, cfg, {"negocio": "Ferretería Sur", "telefono_raw": "+34 954 11 12 22",
                              "instagram": "@fesur.sev", "ciudad": "Sevilla"})
    assert c["resultado"] == "repetido"
    assert c["fusionados"] == [b["lead_id"]]
    with motor.connect() as conn:
        assert contar(conn, db.leads) == 1
        assert {r.lead_id for r in conn.execute(select(db.claves.c.lead_id))} == {a["lead_id"]}


def test_fusion_conserva_estado_mas_avanzado_y_primer_contacto(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Bar Central", "telefono_raw": "912999888", "ciudad": "Madrid"})
    b = ingresar(motor, cfg, {"negocio": "Central Bar", "instagram": "centralbar", "ciudad": "Madrid"})
    registrar(motor, b["lead_id"], "visita")
    ingresar(motor, cfg, {"negocio": "Bar Central", "telefono_raw": "912999888", "instagram": "centralbar",
                          "ciudad": "Madrid"})
    l = lead(motor, a["lead_id"])
    assert l["estado"] == "contactado"
    assert l["canal_contactado"] == "visita"
    with motor.connect() as conn:
        assert contar(conn, db.leads) == 1
        assert contar(conn, db.contactos) == 1


def test_contactado_que_reaparece_no_vuelve_a_nuevo(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Óptica Ver", "telefono_raw": "913421111", "ciudad": "Madrid"})
    registrar(motor, a["lead_id"])
    r = ingresar(motor, cfg, {"negocio": "Optica Ver", "telefono_raw": "+34 913 42 11 11", "ciudad": "Madrid"})
    assert r["resultado"] == "repetido"
    assert lead(motor, a["lead_id"])["estado"] == "contactado"
    with motor.connect() as conn:
        assert crm.cola(conn, cfg)["leads"] == []


# ----------------------------------------------------------------- contacto
def test_registrar_es_idempotente(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Kali Moda", "telefono_raw": "612401234", "ciudad": "Madrid"})
    r1 = registrar(motor, a["lead_id"], "llamada", "Hablé con la dueña")
    r2 = registrar(motor, a["lead_id"], "visita")
    assert not r1["ya_registrado"] and r2["ya_registrado"]
    with motor.connect() as conn:
        assert contar(conn, db.contactos) == 1
        assert crm.contactados_hoy(conn) == 1
        c = crm.contacto_de(conn, a["lead_id"])
    assert c["canal"] == "llamada" and c["texto"] == "Hablé con la dueña"
    assert lead(motor, a["lead_id"])["estado"] == "contactado"


def test_registrar_canal_desconocido_se_rechaza(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Kali", "telefono_raw": "612401234", "ciudad": "Madrid"})
    for canal in ("whatsapp", "email", ""):
        with motor.begin() as conn, pytest.raises(ErrorCRM):
            crm.registrar_contacto(conn, a["lead_id"], canal, "", "R")
    assert lead(motor, a["lead_id"])["estado"] == "nuevo"


def test_no_molestar_no_se_registra_contacto(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Kali", "telefono_raw": "612401234", "ciudad": "Madrid"})
    with motor.begin() as conn:
        crm.cambiar_estado(conn, a["lead_id"], "no molestar", "R")
    with motor.begin() as conn, pytest.raises(ErrorCRM, match="no lo contacten"):
        crm.registrar_contacto(conn, a["lead_id"], "llamada", "", "R")


def test_registrar_en_lead_fusionado_registra_en_el_que_quedo(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Uno", "telefono_raw": "612401111", "ciudad": "Madrid"})
    b = ingresar(motor, cfg, {"negocio": "Dos", "instagram": "dos", "ciudad": "Madrid"})
    with motor.begin() as conn:
        crm.fusionar_manual(conn, a["lead_id"], b["lead_id"], "M")
    r = registrar(motor, b["lead_id"])                     # id viejo
    assert r["lead_id"] == a["lead_id"]
    assert lead(motor, a["lead_id"])["estado"] == "contactado"


def test_contactados_hoy_en_hora_de_madrid(motor, cfg, monkeypatch):
    a = ingresar(motor, cfg, {"negocio": "A1", "telefono_raw": "612401111", "ciudad": "Madrid"})
    monkeypatch.setattr(crm, "hoy_local", lambda: "2026-03-10")
    registrar(motor, a["lead_id"])
    with motor.connect() as conn:
        assert crm.contactados_hoy(conn) == 1
    # 23:30 UTC del 10/03 ya es 11/03 en Madrid (UTC+1)
    assert db.dia_local(datetime(2026, 3, 10, 23, 30, tzinfo=timezone.utc).isoformat()) == "2026-03-11"


def test_cola_todos_los_nuevos_por_prioridad(motor, cfg):
    sin_datos = ingresar(motor, cfg, {"negocio": "Sin Datos", "ciudad": "Madrid"})
    baja = ingresar(motor, cfg, {"negocio": "Baja", "telefono_raw": "612401111", "ciudad": "Madrid"})
    alta = ingresar(motor, cfg, {"negocio": "Alta", "instagram": "alta.ok", "ciudad": "Madrid"})
    with motor.begin() as conn:
        conn.execute(update(db.leads).where(db.leads.c.id == baja["lead_id"]).values(prioridad="baja"))
        conn.execute(update(db.leads).where(db.leads.c.id == alta["lead_id"]).values(prioridad="alta"))
    with motor.connect() as conn:
        c = crm.cola(conn, cfg)
    ids = [l["id"] for l in c["leads"]]
    assert ids == [alta["lead_id"], baja["lead_id"], sin_datos["lead_id"]]
    assert c["pendientes"] == 3


def test_vista_no_trae_mensajes(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Kali Moda", "telefono_raw": "612401234", "email": "hola@kali.es",
                              "ciudad": "Madrid", "direccion": "Calle Mayor 1"})
    v = crm.vista(lead(motor, a["lead_id"]))
    assert v["medios"] == ["telefono", "email", "direccion"]
    assert v["es_movil"] is True
    assert "mensajes" not in v and "canales" not in v


# ------------------------------------------------------------------ estados
def test_no_retrocede_sin_reabrir(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "A1", "telefono_raw": "612401111", "ciudad": "Madrid"})
    registrar(motor, a["lead_id"])
    with motor.begin() as conn:
        crm.cambiar_estado(conn, a["lead_id"], "cliente", "M")
    with motor.begin() as conn, pytest.raises(ErrorCRM):
        crm.cambiar_estado(conn, a["lead_id"], "respondio", "M")
    with motor.begin() as conn:
        crm.cambiar_estado(conn, a["lead_id"], "respondio", "M", reabrir=True)
    assert lead(motor, a["lead_id"])["estado"] == "respondio"


def test_contactado_a_mano_registra_contacto(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "A1", "telefono_raw": "612401111", "ciudad": "Madrid"})
    with motor.begin() as conn:
        crm.cambiar_estado(conn, a["lead_id"], "contactado", "M")
    with motor.connect() as conn:
        assert crm.contacto_de(conn, a["lead_id"])["canal"] == "manual"
    with motor.begin() as conn, pytest.raises(ErrorCRM):
        crm.cambiar_estado(conn, a["lead_id"], "nuevo", "M", reabrir=True)


def test_no_molestar_es_permanente_ante_rescrape(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "A1", "telefono_raw": "612401111", "ciudad": "Madrid"})
    with motor.begin() as conn:
        crm.cambiar_estado(conn, a["lead_id"], "no molestar", "M")
    ingresar(motor, cfg, {"negocio": "A1", "telefono_raw": "612401111", "ciudad": "Madrid"})
    assert lead(motor, a["lead_id"])["estado"] == "no molestar"
    with motor.connect() as conn:
        assert crm.cola(conn, cfg)["leads"] == []


# ------------------------------------------------------------------- edicion
def test_editar_telefono_de_otro_negocio_se_rechaza(motor, cfg):
    ingresar(motor, cfg, {"negocio": "A1", "telefono_raw": "612401111", "ciudad": "Madrid"})
    b = ingresar(motor, cfg, {"negocio": "B1", "ciudad": "Madrid"})
    with motor.begin() as conn, pytest.raises(ErrorCRM, match="A1"):
        crm.editar(conn, b["lead_id"], {"telefono_raw": "+34 612 40 11 11"}, "M", cfg)


def test_editar_telefono_nuevo_crea_clave(motor, cfg):
    b = ingresar(motor, cfg, {"negocio": "B1", "ciudad": "Madrid"})
    with motor.begin() as conn:
        crm.editar(conn, b["lead_id"], {"telefono_raw": "612 40 22 22"}, "M", cfg)
    r = ingresar(motor, cfg, {"negocio": "Otro nombre", "telefono_raw": "+34612402222", "ciudad": "Madrid"})
    assert r["lead_id"] == b["lead_id"]


def test_fusion_manual(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "Uno", "telefono_raw": "612401111", "ciudad": "Madrid"})
    b = ingresar(motor, cfg, {"negocio": "Dos", "instagram": "dos", "ciudad": "Madrid"})
    with motor.begin() as conn:
        base = crm.fusionar_manual(conn, b["lead_id"], a["lead_id"], "M")
    assert base == a["lead_id"]
    l = lead(motor, base)
    assert l["instagram"] == "dos" and l["telefono_e164"] == "+34612401111"
    with motor.connect() as conn:
        tipos = [e["tipo"] for e in crm.historial(conn, base)]
    assert "fusionado" in tipos


def test_textos_largos_no_rompen(motor, cfg):
    """Postgres rechaza textos mas largos que la columna: se recortan al importar."""
    r = ingresar(motor, cfg, {"negocio": "N" * 900, "rubro": "R" * 900, "ciudad": "Madrid",
                              "direccion": "D" * 900, "web": "https://" + "a" * 400 + ".com"})
    l = lead(motor, r["lead_id"])
    assert len(l["negocio"]) <= 300 and len(l["rubro"]) <= 200 and len(l["web"]) <= 300
    with motor.begin() as conn, pytest.raises(ErrorCRM, match="demasiado largo"):
        crm.editar(conn, r["lead_id"], {"proxima_accion": "x" * 301}, "M", cfg)


def test_columnas_de_texto_tienen_limite_y_se_recortan(motor):
    largo = {c.name: c.type.length for c in db.leads.c if getattr(c.type, "length", None)}
    assert largo["negocio"] == 300
    recortado = db.recortar(db.leads, {k: "x" * (n + 50) for k, n in largo.items()})
    assert all(len(recortado[k]) == n for k, n in largo.items())


def test_insertar_si_no_existe_informa_bien(motor):
    with motor.begin() as conn:
        assert db.insertar_si_no_existe(conn, db.config, {"clave": "limite_busqueda", "valor": "5"}) is True
        assert db.insertar_si_no_existe(conn, db.config, {"clave": "limite_busqueda", "valor": "9"}) is False
        assert db.leer_config(conn)["limite_busqueda"] == 5


def test_eventos_registran_todo(motor, cfg):
    a = ingresar(motor, cfg, {"negocio": "A1", "telefono_raw": "612401111", "ciudad": "Madrid"})
    registrar(motor, a["lead_id"], "llamada", "", "Renata")
    with motor.begin() as conn:
        crm.cambiar_estado(conn, a["lead_id"], "respondio", "Mauro")
        crm.agregar_nota(conn, a["lead_id"], "quiere presupuesto", "Mauro")
    with motor.connect() as conn:
        h = crm.historial(conn, a["lead_id"])
    assert [e["tipo"] for e in h] == ["nota", "estado", "contacto", "creado"]
    assert h[0]["usuario"] == "Mauro"
