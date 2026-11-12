"""
Base de datos: esquema, conexion y bloqueos.

Postgres en produccion (Railway), SQLite en desarrollo y tests. El esquema es
neutral entre ambos; lo que difiere (bloqueos, "insert or ignore") esta
encapsulado aca.

Las garantias anti-duplicados las hace cumplir LA BASE, no el codigo:
  - claves.clave es PRIMARY KEY: una clave canonica pertenece a un solo lead.
  - contactos.lead_id es UNIQUE: un negocio recibe un solo primer mensaje.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (Boolean, Column, Float, ForeignKey, Index, Integer,
                        MetaData, String, Table, Text, create_engine, event,
                        insert, select, text, update)
from sqlalchemy.engine import Connection, Engine

TZ_LOCAL = ZoneInfo("Europe/Madrid")
SCHEMA_VERSION = 1

meta = MetaData()

usuarios = Table(
    "usuarios", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String(200), nullable=False, unique=True),
    Column("nombre", String(100), nullable=False),
    Column("hash", String(300), nullable=False),
    Column("activo", Boolean, nullable=False, default=True),
    Column("creado", String(40), nullable=False),
)

leads = Table(
    "leads", meta,
    Column("id", String(36), primary_key=True),
    Column("negocio", String(300), nullable=False),        # titulo crudo
    Column("marca_ia", String(200)),
    Column("marca_manual", String(200)),
    Column("rubro", String(200)),
    Column("ciudad", String(120)),
    Column("provincia", String(120)),
    Column("direccion", String(300)),
    Column("telefono_raw", String(60)),
    Column("telefono_e164", String(20)),
    Column("movil_probable", Boolean),
    Column("email", String(200)),
    Column("instagram", String(60)),
    Column("web", String(300)),
    Column("maps_url", Text),
    Column("place_id", String(120)),
    Column("rating", Float),
    Column("resenas", Integer),
    Column("fuentes", String(100)),                         # "maps,notion"
    Column("prioridad", String(10)),                        # alta|media|baja
    Column("motivo_ia", Text),
    Column("ia_estado", String(12), nullable=False, default="pendiente"),  # pendiente|ok|error
    Column("ia_intentos", Integer, nullable=False, default=0),
    Column("estado", String(20), nullable=False, default="nuevo"),
    Column("canal_contactado", String(20)),
    Column("fecha_contacto", String(40)),
    Column("proxima_accion", String(300)),
    Column("proxima_fecha", String(10)),                    # YYYY-MM-DD (AR)
    Column("notas", Text),
    Column("monto_presupuesto", Float),                     # euros
    Column("monto_cobrado", Float),                         # euros
    Column("fecha_cobro", String(10)),                      # YYYY-MM-DD
    Column("busqueda_id", Integer),
    Column("creado", String(40), nullable=False),
    Column("actualizado", String(40), nullable=False),
)
Index("ix_leads_estado", leads.c.estado)
Index("ix_leads_proxima", leads.c.proxima_fecha)
Index("ix_leads_ia", leads.c.ia_estado)

claves = Table(
    "claves", meta,
    Column("clave", String(700), primary_key=True),
    Column("lead_id", String(36), ForeignKey("leads.id", ondelete="CASCADE"),
           nullable=False, index=True),
)

contactos = Table(
    "contactos", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lead_id", String(36), ForeignKey("leads.id", ondelete="CASCADE"),
           nullable=False, unique=True),
    Column("canal", String(20), nullable=False),     # whatsapp|instagram|email|manual|migrado
    Column("texto", Text),
    Column("usuario", String(100)),
    Column("fecha", String(40), nullable=False),     # ISO UTC
    Column("fecha_ar", String(10), nullable=False),  # dia en hora de Madrid
)
Index("ix_contactos_dia", contactos.c.fecha_ar)

eventos = Table(
    "eventos", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", String(40), nullable=False),
    Column("usuario", String(100)),
    Column("tipo", String(30), nullable=False),
    Column("lead_id", String(36), index=True),     # sin FK: sobrevive a fusiones
    Column("datos", Text),
)

busquedas = Table(
    "busquedas", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("rubro", String(200), nullable=False),
    Column("ciudad", String(120), nullable=False),
    Column("provincia", String(120)),
    Column("zona", String(120)),                   # barrio dentro de la ciudad (opcional)
    Column("limite", Integer, nullable=False),
    Column("con_contactos", Boolean, nullable=False, default=False),
    Column("lote", String(40)),                    # agrupa las ciudades de un barrido
    Column("run_id", String(40)),
    Column("dataset_id", String(40)),
    Column("estado", String(20), nullable=False),  # pendiente|corriendo|importando|importada|fallida
    Column("error", Text),
    Column("encontrados", Integer),
    Column("nuevos", Integer),
    Column("repetidos", Integer),
    Column("fusionados", Integer),
    Column("descartados", Integer),
    Column("costo_usd", Float),
    Column("crudo", Text),                         # dataset tal cual vino de Apify
    Column("usuario", String(100)),
    Column("creado", String(40), nullable=False),
    Column("actualizado", String(40), nullable=False),
)
Index("ix_busquedas_estado", busquedas.c.estado)

config = Table(
    "config", meta,
    Column("clave", String(60), primary_key=True),
    Column("valor", Text, nullable=False),         # JSON
)

plantillas = Table(
    "plantillas", meta,
    Column("canal", String(20), primary_key=True),  # whatsapp|instagram|email
    Column("asunto", String(300)),
    Column("cuerpo", Text, nullable=False),
    Column("usuario", String(100)),
    Column("actualizado", String(40), nullable=False),
)

# Cruce con registros de marcas (EUIPO, y OEPM cuando haya acceso). Una fila
# por lead: el ultimo chequeo. Si cambia la marca, se vuelve a revisar.
registros = Table(
    "registros", meta,
    Column("lead_id", String(36), primary_key=True),   # sin FK: sobrevive a fusiones
    Column("marca", String(200), nullable=False),      # la marca que se busco
    Column("nivel", String(10)),                       # rojo|amarillo|verde (None si fallo)
    Column("resultado", Text),                         # JSON con las coincidencias
    Column("error", Text),
    Column("intentos", Integer, nullable=False, default=0),
    Column("revisado", String(40), nullable=False),    # ISO UTC
)

esquema = Table(
    "schema_version", meta,
    Column("version", Integer, primary_key=True),
    Column("aplicada", String(40), nullable=False),
)


# --------------------------------------------------------------------------
# Tiempo
# --------------------------------------------------------------------------
def ahora_iso() -> str:
    # Microsegundos: "el mas viejo gana" al fusionar necesita un orden sin empates.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def hoy_local() -> str:
    """El dia de HOY en Espana (hora de Madrid). El servidor corre en UTC: sin
    esto los seguimientos "para hoy" cambiarian de dia a otra hora."""
    return datetime.now(TZ_LOCAL).date().isoformat()


def dia_local(iso_utc: str) -> str:
    try:
        return datetime.fromisoformat(iso_utc).astimezone(TZ_LOCAL).date().isoformat()
    except (TypeError, ValueError):
        return hoy_local()


def a_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------
# Motor
# --------------------------------------------------------------------------
def url_base_datos() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        carpeta = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
        carpeta.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(carpeta / 'crm.db').as_posix()}"
    # Railway entrega postgres:// o postgresql:// ; SQLAlchemy necesita el driver
    for viejo in ("postgres://", "postgresql://"):
        if url.startswith(viejo):
            return "postgresql+psycopg://" + url[len(viejo):]
    return url


def crear_motor(url: str | None = None) -> Engine:
    url = url or url_base_datos()
    if url.startswith("sqlite"):
        motor = create_engine(url, connect_args={"check_same_thread": False,
                                                 "timeout": 30})

        @event.listens_for(motor, "connect")
        def _pragmas(dbapi_conn, _):                 # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
        return motor
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def es_postgres(conn: Connection) -> bool:
    return conn.dialect.name == "postgresql"


def bloquear(conn: Connection, nombre: str) -> None:
    """
    Candado exclusivo hasta el fin de la transaccion.

    Serializa las operaciones que leen-y-escriben el padron (altas, fusiones,
    confirmar contacto): dos importaciones a la vez nunca crean el mismo lead
    dos veces, y dos confirmaciones simultaneas no pasan el tope diario.
    En SQLite no hace falta: las escrituras ya son de a una.
    """
    if es_postgres(conn):
        n = int.from_bytes(hashlib.sha256(nombre.encode()).digest()[:8], "big", signed=True)
        conn.execute(text("SELECT pg_advisory_xact_lock(:n)"), {"n": n})


def insertar_si_no_existe(conn: Connection, tabla: Table, valores: dict[str, Any]) -> bool:
    """
    INSERT que ignora el conflicto de clave. Devuelve True si inserto.

    Se decide por RETURNING (si vuelve una fila, inserto) y NO por rowcount:
    en Postgres, con el RETURNING implicito que agrega SQLAlchemy, rowcount
    puede dar 0 aunque la fila se haya insertado.
    """
    if es_postgres(conn):
        from sqlalchemy.dialects.postgresql import insert as ins
    else:
        from sqlalchemy.dialects.sqlite import insert as ins
    stmt = (ins(tabla).values(**recortar(tabla, valores)).on_conflict_do_nothing()
            .returning(*tabla.primary_key.columns))
    return conn.execute(stmt).first() is not None


def largo_maximo(tabla: Table, columna: str) -> int | None:
    col = tabla.c.get(columna)
    return getattr(col.type, "length", None) if col is not None else None


def recortar(tabla: Table, valores: dict[str, Any]) -> dict[str, Any]:
    """
    Ajusta cada texto al largo de su columna. SQLite no controla el largo pero
    Postgres SI (y fallaria el INSERT entero): datos scrapeados raros no pueden
    tumbar una importacion.
    """
    salida = {}
    for k, v in valores.items():
        n = largo_maximo(tabla, k)
        salida[k] = v[:n] if (n and isinstance(v, str) and len(v) > n) else v
    return salida


def evento(conn: Connection, tipo: str, lead_id: str | None = None,
           usuario: str | None = None, **datos: Any) -> None:
    """Registro append-only. Nunca se modifica ni se borra."""
    conn.execute(insert(eventos).values(**recortar(eventos, dict(
        ts=ahora_iso(), usuario=usuario, tipo=tipo, lead_id=lead_id,
        datos=a_json(datos) if datos else None))))


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
CONFIG_DEFAULT: dict[str, Any] = {
    # En Espana no hay telefonos sin prefijo: area_default queda vacio.
    "area_default": "",
    "ciudad_default": "Madrid",
    "limite_busqueda": 40,
}


def leer_config(conn: Connection) -> dict[str, Any]:
    cfg = dict(CONFIG_DEFAULT)
    for fila in conn.execute(select(config.c.clave, config.c.valor)):
        if fila.clave not in CONFIG_DEFAULT:        # ej. "canal:whatsapp" (versiones viejas)
            continue
        try:
            cfg[fila.clave] = json.loads(fila.valor)
        except ValueError:
            pass
    return cfg


def guardar_config(conn: Connection, valores: dict[str, Any]) -> None:
    for k, v in valores.items():
        if k not in CONFIG_DEFAULT:
            continue
        if not insertar_si_no_existe(conn, config, {"clave": k, "valor": a_json(v)}):
            conn.execute(update(config).where(config.c.clave == k).values(valor=a_json(v)))


# --------------------------------------------------------------------------
# Inicializacion y migraciones
# --------------------------------------------------------------------------
# Migraciones futuras: {version: [sentencias SQL]}. Nunca editar una ya aplicada.
MIGRACIONES: dict[int, list[str]] = {
    2: [  # montos de presupuesto y cobro
        "ALTER TABLE leads ADD COLUMN monto_presupuesto DOUBLE PRECISION",
        "ALTER TABLE leads ADD COLUMN monto_cobrado DOUBLE PRECISION",
        "ALTER TABLE leads ADD COLUMN fecha_cobro VARCHAR(10)",
    ],
    3: [  # zona/barrio de una busqueda
        "ALTER TABLE busquedas ADD COLUMN zona VARCHAR(120)",
    ],
}


def inicializar(motor: Engine) -> None:
    """Crea las tablas que falten y aplica migraciones pendientes. Idempotente."""
    meta.create_all(motor)
    with motor.begin() as conn:
        bloquear(conn, "migraciones")
        aplicadas = {r.version for r in conn.execute(select(esquema.c.version))}
        if not aplicadas:
            # Base nueva: create_all ya la dejo en el esquema actual, asi que
            # todas las migraciones cuentan como aplicadas.
            aplicadas = {SCHEMA_VERSION, *MIGRACIONES}
            for version in sorted(aplicadas):
                conn.execute(insert(esquema).values(version=version, aplicada=ahora_iso()))
        for version in sorted(MIGRACIONES):
            if version in aplicadas:
                continue
            for sql in MIGRACIONES[version]:
                conn.execute(text(sql))
            conn.execute(insert(esquema).values(version=version, aplicada=ahora_iso()))
