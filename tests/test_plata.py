"""Montos de presupuesto y cobro: carga, validacion, metricas y migracion."""
import pytest
from sqlalchemy import inspect, text

from app import crm, db
from app.crm import ErrorCRM, parsear_monto


@pytest.mark.parametrize("crudo,esperado", [
    ("1500", 1500), ("1.500", 1500), ("1.500 €", 1500), ("1.500,50", 1500.5),
    ("1.250.000", 1250000), ("1500.5", 1500.5), ("  ", None), (None, None), (980, 980),
    ("EUR 1.250", 1250), ("€950", 950),
])
def test_parsear_monto(crudo, esperado):
    assert parsear_monto(crudo) == esperado


@pytest.mark.parametrize("malo", ["mucho", "-500", "1e12", "nan"])
def test_monto_invalido(malo):
    with pytest.raises(ErrorCRM):
        parsear_monto(malo)


def nuevo(motor, cfg, **kw):
    with motor.begin() as conn:
        db.bloquear(conn, "padron")
        return crm.ingresar(conn, {"negocio": kw.pop("negocio", "Kali"), "ciudad": kw.pop("ciudad", "Madrid"),
                                   "rubro": kw.pop("rubro", "Ropa"), **kw}, cfg, fuente="maps")["lead_id"]


def test_cargar_cobro_pone_fecha_de_hoy_y_borrar_la_quita(motor, cfg):
    lid = nuevo(motor, cfg)
    with motor.begin() as conn:
        l = crm.editar(conn, lid, {"monto_presupuesto": "180.000", "monto_cobrado": "150.000"}, "M", cfg)
    assert l["monto_presupuesto"] == 180000 and l["monto_cobrado"] == 150000
    assert l["fecha_cobro"] == db.hoy_local()
    with motor.begin() as conn:
        l = crm.editar(conn, lid, {"monto_cobrado": ""}, "M", cfg)
    assert l["monto_cobrado"] is None and l["fecha_cobro"] is None


def test_fecha_de_cobro_explicita_y_no_futura(motor, cfg):
    lid = nuevo(motor, cfg)
    with motor.begin() as conn:
        l = crm.editar(conn, lid, {"monto_cobrado": "90000", "fecha_cobro": "2026-08-15"}, "M", cfg)
    assert l["fecha_cobro"] == "2026-08-15"
    with motor.begin() as conn, pytest.raises(ErrorCRM, match="futura"):
        crm.editar(conn, lid, {"fecha_cobro": "2999-01-01"}, "M", cfg)


def test_metricas_de_plata(motor, cfg):
    a = nuevo(motor, cfg, negocio="A1", rubro="Ropa", telefono_raw="2983401111")
    b = nuevo(motor, cfg, negocio="B1", rubro="Ropa", telefono_raw="2983402222")
    c = nuevo(motor, cfg, negocio="C1", rubro="Gimnasio", telefono_raw="2983403333")
    with motor.begin() as conn:
        crm.editar(conn, a, {"monto_presupuesto": "200000", "monto_cobrado": "200000", "fecha_cobro": "2026-08-10"}, "M", cfg)
        crm.editar(conn, b, {"monto_presupuesto": "150000"}, "M", cfg)
        crm.editar(conn, c, {"monto_cobrado": "100000", "fecha_cobro": "2026-09-01"}, "M", cfg)
    with motor.connect() as conn:
        m = crm.metricas(conn)
    assert m["cobrado"] == 300000 and m["cobros"] == 2 and m["ticket_promedio"] == 150000
    assert m["presupuestado"] == 350000 and m["presupuestos"] == 2
    assert m["cierre_presupuestos"] == 50.0                      # de 2 presupuestos, 1 cobrado
    assert [x["mes"] for x in m["cobrado_por_mes"]] == ["2026-09", "2026-08"]
    ropa = next(r for r in m["por_rubro"] if r["clave"] == "Ropa")
    assert ropa["cobrado"] == 200000
    assert m["por_rubro"][0]["clave"] == "Ropa"                 # ordena por lo cobrado


def test_metricas_sin_plata(motor):
    with motor.connect() as conn:
        m = crm.metricas(conn)
    assert m["cobrado"] == 0 and m["ticket_promedio"] is None and m["cierre_presupuestos"] is None


def test_fusion_conserva_montos(motor, cfg):
    a = nuevo(motor, cfg, negocio="Uno", telefono_raw="2983401111")
    b = nuevo(motor, cfg, negocio="Dos", instagram="dos")
    with motor.begin() as conn:
        crm.editar(conn, b, {"monto_cobrado": "120000"}, "M", cfg)
        base = crm.fusionar_manual(conn, a, b, "M")
        assert crm.obtener(conn, base)["monto_cobrado"] == 120000


def test_migracion_agrega_columnas_a_una_base_vieja(tmp_path):
    """Una base creada antes de los montos (version 1) recibe las columnas sin perder datos."""
    m = db.crear_motor(f"sqlite:///{(tmp_path / 'vieja.db').as_posix()}")
    db.inicializar(m)
    with m.begin() as conn:
        for col in ("monto_presupuesto", "monto_cobrado", "fecha_cobro"):
            conn.execute(text(f"ALTER TABLE leads DROP COLUMN {col}"))
        conn.execute(text("DELETE FROM schema_version WHERE version = 2"))
        conn.execute(text("INSERT INTO leads (id, negocio, estado, ia_estado, ia_intentos, creado, actualizado) "
                          "VALUES ('x', 'Viejo', 'nuevo', 'ok', 0, '2026-01-01', '2026-01-01')"))
    db.inicializar(m)
    db.inicializar(m)                                              # idempotente
    columnas = {c["name"] for c in inspect(m).get_columns("leads")}
    assert {"monto_presupuesto", "monto_cobrado", "fecha_cobro"} <= columnas
    with m.connect() as conn:
        assert conn.execute(text("SELECT negocio FROM leads WHERE id='x'")).scalar() == "Viejo"
        assert conn.execute(text("SELECT count(*) FROM schema_version WHERE version=2")).scalar() == 1


def test_api_montos(cliente, app):
    with app.extensions["motor"].begin() as conn:
        cfg = db.leer_config(conn)
        lid = crm.ingresar(conn, {"negocio": "Kali", "ciudad": "Madrid"}, cfg, fuente="maps")["lead_id"]
    r = cliente.patch(f"/api/leads/{lid}", json={"monto_cobrado": "150.000"})
    assert r.status_code == 200 and r.get_json()["lead"]["monto_cobrado"] == 150000
    assert cliente.patch(f"/api/leads/{lid}", json={"monto_cobrado": "muchos"}).status_code == 409
    assert cliente.get("/api/metricas").get_json()["cobrado"] == 150000
    assert "monto_cobrado" in cliente.get("/api/exportar.csv").data.decode("utf-8")
