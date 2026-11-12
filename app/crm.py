"""
Logica del CRM: altas sin duplicados, fusiones, contacto y estados.

Todas las funciones reciben una conexion DENTRO de una transaccion (el que
llama hace `with motor.begin() as conn:`): o se aplica todo o no se aplica nada.

Las tres garantias del sistema:
  1. Un negocio = un lead. La clave canonica es PRIMARY KEY en `claves`; si un
     candidato coincide con varios leads, se fusionan en el mas viejo.
  2. Un solo primer contacto por negocio. `contactos.lead_id` es UNIQUE y el
     estado nunca vuelve a 'nuevo' una vez contactado.

La app NO manda ni prepara mensajes: en Espana la LSSI (art. 21) prohibe las
comunicaciones comerciales por email, WhatsApp u otro medio electronico que el
destinatario no haya pedido. La app encuentra y prioriza; el contacto lo hace
la abogada por su cuenta (llamada, visita, carta...) y aca solo se REGISTRA.
  3. Nada se pierde. Cada cambio queda en `eventos` (append-only) con quien lo
     hizo y como estaba antes.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, case, delete, func, insert, or_, select, update
from sqlalchemy.engine import Connection

from . import db
from .db import (a_json, ahora_iso, bloquear, busquedas, claves, contactos,
                 evento, hoy_local, leads)
from .identidad import (ESTADOS, bloquea_contacto, claves_de, clave_valida,
                        es_movil_probable, estado_mayor, normalizar_dominio,
                        normalizar_email, normalizar_nombre,
                        normalizar_telefono, normalizar_usuario_ig,
                        rango_estado, whatsapp_numero)
from .errores import ErrorCRM
from .plantillas import marca_final

# Campos de texto que al fusionar se completan si estan vacios.
_CAMPOS_COMPLETABLES = (
    "negocio", "marca_ia", "marca_manual", "rubro", "ciudad", "provincia",
    "direccion", "telefono_raw", "telefono_e164", "movil_probable", "email",
    "instagram", "web", "maps_url", "place_id", "rating", "resenas",
    "prioridad", "motivo_ia", "canal_contactado", "proxima_accion",
    "busqueda_id", "monto_presupuesto", "monto_cobrado", "fecha_cobro",
)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _dict(fila) -> dict[str, Any]:
    return dict(fila._mapping) if fila is not None else {}


def obtener(conn: Connection, lead_id: str, para_modificar: bool = False) -> dict[str, Any]:
    q = select(leads).where(leads.c.id == lead_id)
    if para_modificar and db.es_postgres(conn):
        q = q.with_for_update()
    return _dict(conn.execute(q).first())


def _area_para(ciudad: str, cfg: dict[str, Any]) -> str:
    """
    Caracteristica para completar un telefono escrito sin area. SOLO para
    negocios de la ciudad por defecto: ponerle 2983 a un telefono de Cordoba
    inventaria una clave equivocada.
    """
    propia = normalizar_nombre(cfg.get("ciudad_default") or "")
    return str(cfg.get("area_default") or "") if propia and propia in normalizar_nombre(ciudad) else ""


def _vacio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _normalizar_candidato(cand: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    ciudad = (cand.get("ciudad") or "").strip()
    area = _area_para(ciudad, cfg)
    tel_raw = (cand.get("telefono_raw") or cand.get("telefono") or "").strip()
    e164 = cand.get("telefono_e164") or normalizar_telefono(tel_raw, area)
    return {
        "negocio": (cand.get("negocio") or "").strip()[:300] or "(sin nombre)",
        "marca_ia": cand.get("marca_ia") or None,
        "marca_manual": cand.get("marca_manual") or None,
        "rubro": (cand.get("rubro") or "").strip()[:200] or None,
        "ciudad": ciudad[:120] or None,
        "provincia": (cand.get("provincia") or "").strip()[:120] or None,
        "direccion": (cand.get("direccion") or "").strip()[:300] or None,
        "telefono_raw": tel_raw[:60] or None,
        "telefono_e164": e164 or None,
        "movil_probable": es_movil_probable(tel_raw) if tel_raw else None,
        "email": normalizar_email(cand.get("email")) or None,
        "instagram": normalizar_usuario_ig(cand.get("instagram")) or None,
        "web": (cand.get("web") or "").strip()[:300] or None,
        "maps_url": cand.get("maps_url") or None,
        "place_id": (cand.get("place_id") or "").strip() or None,
        "rating": cand.get("rating"),
        "resenas": cand.get("resenas"),
        "prioridad": cand.get("prioridad") if cand.get("prioridad") in ("alta", "media", "baja") else None,
        "motivo_ia": cand.get("motivo_ia") or None,
    }, area


def _unir_fuentes(*valores: str | None) -> str:
    todas: list[str] = []
    for v in valores:
        for f in (v or "").split(","):
            f = f.strip()
            if f and f not in todas:
                todas.append(f)
    return ",".join(todas)


# --------------------------------------------------------------------------
# Alta de candidatos (con deduplicacion y fusion)
# --------------------------------------------------------------------------
def ingresar(conn: Connection, cand: dict[str, Any], cfg: dict[str, Any], *,
             fuente: str, usuario: str | None = None, busqueda_id: int | None = None,
             lead_id: str | None = None, estado: str | None = None,
             claves_extra: list[str] | None = None,
             extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Mete un candidato en el padron. Devuelve {resultado, lead_id, fusionados}
    con resultado 'nuevo' | 'repetido'.

    `estado`, `lead_id`, `claves_extra` y `extra` los usa la migracion.
    El llamador debe tener tomado `bloquear(conn, "padron")`.
    """
    datos, area = _normalizar_candidato(cand, cfg)
    todas = claves_de(datos, area)
    for c in claves_extra or []:
        if clave_valida(c) and c not in todas:
            todas.append(c)

    ids: list[str] = []
    if todas:
        ids = [r.lead_id for r in conn.execute(
            select(claves.c.lead_id).where(claves.c.clave.in_(todas)).distinct())]
    ahora = ahora_iso()

    if not ids:
        nuevo_id = lead_id or str(uuid.uuid4())
        fila = {**datos, **(extra or {}), "id": nuevo_id,
                "estado": estado if estado in ESTADOS else "nuevo",
                "fuentes": fuente, "busqueda_id": busqueda_id,
                "ia_estado": "ok" if datos.get("prioridad") else "pendiente",
                "ia_intentos": 0, "creado": (extra or {}).get("creado") or ahora,
                "actualizado": ahora}
        conn.execute(insert(leads).values(**db.recortar(leads, fila)))
        for c in todas:
            db.insertar_si_no_existe(conn, claves, {"clave": c, "lead_id": nuevo_id})
        evento(conn, "creado", nuevo_id, usuario, fuente=fuente, busqueda_id=busqueda_id)
        return {"resultado": "nuevo", "lead_id": nuevo_id, "fusionados": []}

    # Ya existe: gana el mas viejo (conserva historial y contactos)
    existentes = conn.execute(select(leads.c.id).where(leads.c.id.in_(ids))
                              .order_by(leads.c.creado, leads.c.id)).scalars().all()
    base_id, *hermanos = existentes
    for h in hermanos:
        fusionar(conn, base_id, h, usuario, motivo=f"coincidencia al ingresar ({fuente})")

    base = obtener(conn, base_id)
    cambios: dict[str, Any] = {}
    for campo in _CAMPOS_COMPLETABLES:
        nuevo_valor = {**datos, **(extra or {})}.get(campo)
        if _vacio(base.get(campo)) and not _vacio(nuevo_valor):
            cambios[campo] = nuevo_valor
    # La calificacion de Maps se actualiza con la mas reciente
    for campo in ("rating", "resenas"):
        if datos.get(campo) is not None and datos[campo] != base.get(campo):
            cambios[campo] = datos[campo]
    if estado and estado_mayor(base["estado"], estado) != base["estado"]:
        cambios["estado"] = estado_mayor(base["estado"], estado)
    fuentes = _unir_fuentes(base.get("fuentes"), fuente)
    if fuentes != (base.get("fuentes") or ""):
        cambios["fuentes"] = fuentes
    if cambios:
        conn.execute(update(leads).where(leads.c.id == base_id)
                     .values(**db.recortar(leads, cambios), actualizado=ahora))
        evento(conn, "completado", base_id, usuario, fuente=fuente,
               campos=sorted(cambios))

    for c in claves_de({**base, **cambios}, area) + todas:
        db.insertar_si_no_existe(conn, claves, {"clave": c, "lead_id": base_id})
    return {"resultado": "repetido", "lead_id": base_id, "fusionados": hermanos}


def fusionar(conn: Connection, base_id: str, otro_id: str, usuario: str | None,
             motivo: str = "") -> None:
    """
    Absorbe `otro` en `base` sin perder nada:
      - completa campos vacios de base con los de otro;
      - gana el estado mas avanzado (un contactado nunca vuelve a nuevo);
      - se conserva el PRIMER contacto (el mas viejo) y las notas de los dos;
      - las claves de otro pasan a base; otro se borra (queda en eventos).
    """
    if base_id == otro_id:
        return
    base, otro = obtener(conn, base_id), obtener(conn, otro_id)
    if not base or not otro:
        return
    cambios: dict[str, Any] = {}
    for campo in _CAMPOS_COMPLETABLES:
        if _vacio(base.get(campo)) and not _vacio(otro.get(campo)):
            cambios[campo] = otro[campo]
    estado = estado_mayor(base["estado"], otro["estado"])
    if estado != base["estado"]:
        cambios["estado"] = estado
    fechas = [f for f in (base.get("fecha_contacto"), otro.get("fecha_contacto")) if f]
    if fechas and min(fechas) != base.get("fecha_contacto"):
        cambios["fecha_contacto"] = min(fechas)
        cambios["canal_contactado"] = otro.get("canal_contactado") or base.get("canal_contactado")
    notas = [n.strip() for n in (base.get("notas"), otro.get("notas")) if n and n.strip()]
    if len(notas) == 2 and notas[0] != notas[1]:
        cambios["notas"] = notas[0] + "\n\n" + notas[1]
    proximas = [f for f in (base.get("proxima_fecha"), otro.get("proxima_fecha")) if f]
    if proximas and min(proximas) != base.get("proxima_fecha"):
        cambios["proxima_fecha"] = min(proximas)
    cambios["fuentes"] = _unir_fuentes(base.get("fuentes"), otro.get("fuentes"))
    cambios["creado"] = min(base["creado"], otro["creado"])

    # Contactos: se queda el mas viejo (UNIQUE por lead)
    c_base = _dict(conn.execute(select(contactos).where(contactos.c.lead_id == base_id)).first())
    c_otro = _dict(conn.execute(select(contactos).where(contactos.c.lead_id == otro_id)).first())
    if c_otro:
        if not c_base:
            conn.execute(update(contactos).where(contactos.c.id == c_otro["id"])
                         .values(lead_id=base_id))
        elif c_otro["fecha"] < c_base["fecha"]:
            conn.execute(delete(contactos).where(contactos.c.id == c_base["id"]))
            conn.execute(update(contactos).where(contactos.c.id == c_otro["id"])
                         .values(lead_id=base_id))
        else:
            conn.execute(delete(contactos).where(contactos.c.id == c_otro["id"]))

    conn.execute(update(claves).where(claves.c.lead_id == otro_id).values(lead_id=base_id))
    conn.execute(update(leads).where(leads.c.id == base_id)
                 .values(**db.recortar(leads, cambios), actualizado=ahora_iso()))
    conn.execute(delete(leads).where(leads.c.id == otro_id))
    evento(conn, "fusionado", base_id, usuario, absorbido=otro_id, motivo=motivo,
           lead_absorbido=otro, contacto_base=c_base or None, contacto_absorbido=c_otro or None)
    evento(conn, "absorbido", otro_id, usuario, en=base_id)


# --------------------------------------------------------------------------
# Cola de prospectos
# --------------------------------------------------------------------------
# Como se hizo el primer contacto. Ninguno es un envio automatico: la app solo
# deja constancia. "manual" y "migrado" quedan por compatibilidad.
CANALES_CONTACTO: dict[str, str] = {
    "llamada": "Llamada",
    "visita": "Visita en persona",
    "carta": "Carta / correo postal",
    "respuesta": "Me contactó el negocio",
    "otro": "Otro",
}
NOMBRE_CANAL = {**CANALES_CONTACTO, "manual": "Por fuera de la app", "migrado": "Sistema anterior"}


def contactados_hoy(conn: Connection) -> int:
    return conn.execute(select(func.count()).select_from(contactos)
                        .where(contactos.c.fecha_ar == hoy_local())).scalar_one()


_ORDEN_PRIORIDAD = case((leads.c.prioridad == "alta", 0), (leads.c.prioridad == "media", 1),
                        (leads.c.prioridad == "baja", 2), else_=3)


def cola(conn: Connection, cfg: dict[str, Any] | None = None, limite: int = 50) -> dict[str, Any]:
    """Prospectos nuevos (sin contactar), de mejor a peor."""
    nuevo = leads.c.estado == "nuevo"
    total = conn.execute(select(func.count()).select_from(leads).where(nuevo)).scalar_one()
    filas = conn.execute(
        select(leads).where(nuevo)
        .order_by(_ORDEN_PRIORIDAD, func.coalesce(leads.c.resenas, 0).desc(), leads.c.creado)
        .limit(limite)).all()
    return {"pendientes": total, "leads": [_dict(f) for f in filas],
            "contactados_hoy": contactados_hoy(conn)}


def seguimientos(conn: Connection) -> list[dict[str, Any]]:
    filas = conn.execute(
        select(leads).where(and_(leads.c.proxima_fecha.is_not(None),
                                 leads.c.proxima_fecha <= hoy_local(),
                                 leads.c.estado.in_(("contactado", "respondio", "propuesta"))))
        .order_by(leads.c.proxima_fecha, leads.c.actualizado)).all()
    return [_dict(f) for f in filas]


def medios(lead: dict[str, Any]) -> list[str]:
    """Datos de contacto que tiene el negocio (para mostrar, no para enviar)."""
    disp = []
    if lead.get("telefono_e164") or lead.get("telefono_raw"):
        disp.append("telefono")
    if lead.get("email"):
        disp.append("email")
    if lead.get("instagram"):
        disp.append("instagram")
    if lead.get("web"):
        disp.append("web")
    if lead.get("direccion"):
        disp.append("direccion")
    return disp


def vista(lead: dict[str, Any]) -> dict[str, Any]:
    """Lead + campos calculados para la UI."""
    return {**lead,
            "marca": marca_final(lead),
            "medios": medios(lead),
            "bloqueado": bloquea_contacto(lead.get("estado")),
            "es_movil": whatsapp_numero(lead.get("telefono_e164")) != ""}


# --------------------------------------------------------------------------
# Contacto (solo registro)
# --------------------------------------------------------------------------
def _id_vigente(conn: Connection, lead_id: str) -> str:
    """Si el lead se fusiono en otro, el id del que quedo (siguiendo la cadena)."""
    for _ in range(10):
        if conn.execute(select(leads.c.id).where(leads.c.id == lead_id)).first():
            return lead_id
        fila = conn.execute(select(db.eventos.c.datos).where(and_(
            db.eventos.c.tipo == "absorbido", db.eventos.c.lead_id == lead_id))
            .order_by(db.eventos.c.id.desc())).first()
        if not fila or not fila.datos:
            return lead_id
        import json
        lead_id = json.loads(fila.datos).get("en") or lead_id
    return lead_id


def registrar_contacto(conn: Connection, lead_id: str, canal: str, nota: str,
                       usuario: str | None) -> dict[str, Any]:
    """
    Deja constancia del primer contacto (hecho por fuera de la app).
    Idempotente: registrar dos veces (o desde dos dispositivos) no duplica.
    """
    if canal not in CANALES_CONTACTO:
        raise ErrorCRM("Elige cómo fue el contacto.")
    bloquear(conn, "contactos")
    lead_id = _id_vigente(conn, lead_id)
    lead = obtener(conn, lead_id, para_modificar=True)
    if not lead:
        raise ErrorCRM("Ese lead ya no existe. Recarga la página.")
    previo = _dict(conn.execute(select(contactos).where(contactos.c.lead_id == lead_id)).first())
    if previo:
        return {"ya_registrado": True, "contacto": previo, "lead_id": lead_id}
    if lead["estado"] == "no molestar":
        raise ErrorCRM("Este negocio pidió que no lo contacten.")
    ahora = ahora_iso()
    nota = (nota or "").strip()[:2000] or None
    conn.execute(insert(contactos).values(lead_id=lead_id, canal=canal, texto=nota,
                                          usuario=usuario, fecha=ahora, fecha_ar=hoy_local()))
    valores: dict[str, Any] = {"canal_contactado": canal, "fecha_contacto": ahora, "actualizado": ahora}
    if lead["estado"] == "nuevo":
        valores["estado"] = "contactado"
    conn.execute(update(leads).where(leads.c.id == lead_id).values(**valores))
    evento(conn, "contacto", lead_id, usuario, canal=canal, nota=nota,
           **({} if lead["estado"] == "nuevo" else {"estado_previo": lead["estado"]}))
    return {"ya_registrado": False, "lead_id": lead_id}


# --------------------------------------------------------------------------
# Estados y edicion
# --------------------------------------------------------------------------
def cambiar_estado(conn: Connection, lead_id: str, nuevo: str, usuario: str | None,
                   reabrir: bool = False) -> dict[str, Any]:
    nuevo = (nuevo or "").strip().lower()
    if nuevo not in ESTADOS:
        raise ErrorCRM("Estado desconocido.")
    bloquear(conn, "contactos")
    lead = obtener(conn, lead_id)
    if not lead:
        raise ErrorCRM("Ese lead ya no existe. Recarga la página.")
    actual = lead["estado"]
    if actual == nuevo:
        return lead
    tiene_contacto = conn.execute(select(func.count()).select_from(contactos)
                                  .where(contactos.c.lead_id == lead_id)).scalar_one() > 0
    if nuevo == "nuevo" and tiene_contacto:
        raise ErrorCRM("A este negocio ya se lo contactó: no puede volver a 'Nuevo'.")
    if rango_estado(nuevo) < rango_estado(actual) and not reabrir:
        raise ErrorCRM(f"Pasar de '{actual}' a '{nuevo}' es volver atrás. "
                       "Usa 'Reabrir' si es a propósito.")
    ahora = ahora_iso()
    valores: dict[str, Any] = {"estado": nuevo, "actualizado": ahora}
    if nuevo == "contactado" and not tiene_contacto:
        # Se marco como contactado sin decir como: se registra igual para que
        # no vuelva a la cola.
        conn.execute(insert(contactos).values(lead_id=lead_id, canal="manual", texto=None,
                                              usuario=usuario, fecha=ahora, fecha_ar=hoy_local()))
        valores.update(canal_contactado="manual", fecha_contacto=ahora)
    if nuevo in ("cliente", "no interesa", "no molestar"):
        valores.update(proxima_fecha=None, proxima_accion=None)
    conn.execute(update(leads).where(leads.c.id == lead_id).values(**valores))
    evento(conn, "reabierto" if reabrir and rango_estado(nuevo) < rango_estado(actual) else "estado",
           lead_id, usuario, desde=actual, hasta=nuevo)
    return obtener(conn, lead_id)


EDITABLES = ("marca_manual", "notas", "proxima_accion", "proxima_fecha", "prioridad",
             "negocio", "rubro", "ciudad", "telefono_raw", "email", "instagram", "web",
             "monto_presupuesto", "monto_cobrado", "fecha_cobro")
MONTOS = ("monto_presupuesto", "monto_cobrado")
MONTO_MAXIMO = 1_000_000_000


def parsear_monto(valor: Any) -> float | None:
    """
    Monto en euros escrito como lo escribe cualquiera en Espana:
    "1500", "1.500", "1.500,50 €", "1500.5", "EUR 1.250". Vacio -> None.
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        n = float(valor)
    else:
        s = str(valor).strip().replace("€", "").replace(" ", "").replace("\u00a0", "")
        s = s.replace("EUR", "").replace("eur", "").replace("$", "")
        if not s:
            return None
        if "," in s:                           # coma = decimales; puntos = miles
            s = s.replace(".", "").replace(",", ".")
        elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[1]) == 3):
            s = s.replace(".", "")             # "150.000" -> miles
        try:
            n = float(s)
        except ValueError:
            raise ErrorCRM(f"No se entiende el monto \"{valor}\". Escríbelo en euros, ej: 1500 o 1.500") from None
    if n != n or n < 0 or n > MONTO_MAXIMO:      # n != n: NaN
        raise ErrorCRM("El monto tiene que ser un número positivo en euros.")
    return round(n, 2)
_DE_IDENTIDAD = ("negocio", "ciudad", "telefono_raw", "email", "instagram", "web")


def editar(conn: Connection, lead_id: str, campos: dict[str, Any], usuario: str | None,
           cfg: dict[str, Any]) -> dict[str, Any]:
    campos = {k: v for k, v in campos.items() if k in EDITABLES}
    if not campos:
        return obtener(conn, lead_id)
    bloquear(conn, "padron")
    lead = obtener(conn, lead_id)
    if not lead:
        raise ErrorCRM("Ese lead ya no existe. Recarga la página.")

    valores: dict[str, Any] = {}
    for k, v in campos.items():
        if k in MONTOS:
            valores[k] = parsear_monto(v)
            continue
        v = v.strip() if isinstance(v, str) else v
        if k == "prioridad" and v not in ("alta", "media", "baja", None, ""):
            raise ErrorCRM("Prioridad inválida.")
        if k in ("proxima_fecha", "fecha_cobro") and v and not _es_fecha(v):
            raise ErrorCRM("Fecha inválida (usa AAAA-MM-DD).")
        if k == "fecha_cobro" and v and v > hoy_local():
            raise ErrorCRM("La fecha de cobro no puede ser futura.")
        if k == "negocio" and not v:
            raise ErrorCRM("El nombre del negocio no puede quedar vacío.")
        maximo = db.largo_maximo(leads, k)
        if maximo and isinstance(v, str) and len(v) > maximo:
            raise ErrorCRM(f"El campo '{k}' es demasiado largo (máximo {maximo} caracteres).")
        valores[k] = v or None

    # Cobro: si se carga un monto sin fecha, la fecha es hoy; si se borra el
    # monto (y no se manda fecha), se borra tambien la fecha.
    if "monto_cobrado" in valores and "fecha_cobro" not in valores:
        if valores["monto_cobrado"] is None:
            valores["fecha_cobro"] = None
        elif not lead.get("fecha_cobro"):
            valores["fecha_cobro"] = hoy_local()

    if "email" in valores and valores["email"]:
        valores["email"] = normalizar_email(valores["email"]) or None
        if not valores["email"]:
            raise ErrorCRM("Email inválido.")
    if "instagram" in valores and valores["instagram"]:
        valores["instagram"] = normalizar_usuario_ig(valores["instagram"]) or None
        if not valores["instagram"]:
            raise ErrorCRM("Usuario de Instagram inválido.")
    if "web" in valores and valores["web"] and not normalizar_dominio(valores["web"]):
        raise ErrorCRM("Esa web no es un sitio propio (redes y Google Maps no cuentan).")
    ciudad = valores.get("ciudad", lead.get("ciudad")) or ""
    if "telefono_raw" in valores:
        tel = valores["telefono_raw"]
        e164 = normalizar_telefono(tel, _area_para(ciudad, cfg)) if tel else ""
        if tel and not e164:
            raise ErrorCRM("No se entiende el teléfono. Escríbelo con 9 cifras, ej: 612 34 56 78 o 91 123 45 67.")
        valores["telefono_e164"] = e164 or None
        valores["movil_probable"] = es_movil_probable(tel) if tel else None

    # Nuevas claves: no pueden pertenecer a OTRO negocio
    if any(k in valores for k in _DE_IDENTIDAD):
        nuevas = claves_de({**lead, **valores}, _area_para(ciudad, cfg))
        ajenas = conn.execute(select(claves.c.clave, leads.c.negocio)
                              .join(leads, leads.c.id == claves.c.lead_id)
                              .where(and_(claves.c.clave.in_(nuevas), claves.c.lead_id != lead_id))
                              ).first() if nuevas else None
        if ajenas:
            raise ErrorCRM(f"Ese dato ya pertenece a otro negocio: \"{ajenas.negocio}\". "
                           "Si son el mismo, usa 'Fusionar'.")
        for c in nuevas:
            db.insertar_si_no_existe(conn, claves, {"clave": c, "lead_id": lead_id})

    antes = {k: lead.get(k) for k in valores}
    conn.execute(update(leads).where(leads.c.id == lead_id)
                 .values(**valores, actualizado=ahora_iso()))
    evento(conn, "editado", lead_id, usuario, antes=antes, despues=valores)
    return obtener(conn, lead_id)


def fusionar_manual(conn: Connection, a: str, b: str, usuario: str | None) -> str:
    """El usuario indica que dos leads son el mismo negocio. Gana el mas viejo."""
    if a == b:
        raise ErrorCRM("Es el mismo lead.")
    bloquear(conn, "contactos")
    bloquear(conn, "padron")
    la, lb = obtener(conn, a), obtener(conn, b)
    if not la or not lb:
        raise ErrorCRM("Alguno de los dos leads ya no existe.")
    base, otro = (la, lb) if (la["creado"], la["id"]) <= (lb["creado"], lb["id"]) else (lb, la)
    fusionar(conn, base["id"], otro["id"], usuario, motivo="fusión manual")
    return base["id"]


def _es_fecha(v: str) -> bool:
    from datetime import date
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def agregar_nota(conn: Connection, lead_id: str, texto: str, usuario: str | None) -> dict[str, Any]:
    texto = (texto or "").strip()
    if not texto:
        raise ErrorCRM("La nota está vacía.")
    lead = obtener(conn, lead_id)
    if not lead:
        raise ErrorCRM("Ese lead ya no existe. Recarga la página.")
    evento(conn, "nota", lead_id, usuario, texto=texto[:4000])
    conn.execute(update(leads).where(leads.c.id == lead_id).values(actualizado=ahora_iso()))
    return lead


# --------------------------------------------------------------------------
# Consultas para la UI
# --------------------------------------------------------------------------
def buscar(conn: Connection, *, q: str = "", estado: str = "", prioridad: str = "",
           ciudad: str = "", rubro: str = "", canal: str = "", pagina: int = 1,
           por_pagina: int = 50) -> dict[str, Any]:
    cond = []
    if q.strip():
        patron = f"%{q.strip().lower()}%"
        digitos = "".join(ch for ch in q if ch.isdigit())
        opciones = [func.lower(leads.c.negocio).like(patron),
                    func.lower(func.coalesce(leads.c.marca_manual, "")).like(patron),
                    func.lower(func.coalesce(leads.c.marca_ia, "")).like(patron),
                    func.lower(func.coalesce(leads.c.instagram, "")).like(patron),
                    func.lower(func.coalesce(leads.c.email, "")).like(patron)]
        if len(digitos) >= 4:
            opciones.append(func.coalesce(leads.c.telefono_e164, "").like(f"%{digitos[-8:]}%"))
        cond.append(or_(*opciones))
    if estado:
        cond.append(leads.c.estado == estado)
    if prioridad == "sin":
        cond.append(leads.c.prioridad.is_(None))
    elif prioridad:
        cond.append(leads.c.prioridad == prioridad)
    if ciudad:
        cond.append(func.lower(func.coalesce(leads.c.ciudad, "")) == ciudad.lower())
    if rubro:
        cond.append(func.lower(func.coalesce(leads.c.rubro, "")).like(f"%{rubro.lower()}%"))
    if canal in ("telefono", "whatsapp"):
        cond.append(leads.c.telefono_e164.is_not(None))
    elif canal == "movil":
        cond.append(or_(leads.c.telefono_e164.like("+346%"), leads.c.telefono_e164.like("+347%")))
    elif canal == "instagram":
        cond.append(leads.c.instagram.is_not(None))
    elif canal == "email":
        cond.append(leads.c.email.is_not(None))
    donde = and_(*cond) if cond else None

    q_total = select(func.count()).select_from(leads)
    q_filas = select(leads).order_by(leads.c.actualizado.desc())
    if donde is not None:
        q_total, q_filas = q_total.where(donde), q_filas.where(donde)
    pagina = max(1, int(pagina))
    total = conn.execute(q_total).scalar_one()
    filas = conn.execute(q_filas.limit(por_pagina).offset((pagina - 1) * por_pagina)).all()
    return {"total": total, "pagina": pagina, "por_pagina": por_pagina,
            "leads": [_dict(f) for f in filas]}


def historial(conn: Connection, lead_id: str) -> list[dict[str, Any]]:
    import json
    filas = conn.execute(select(db.eventos).where(db.eventos.c.lead_id == lead_id)
                         .order_by(db.eventos.c.id.desc()).limit(200)).all()
    salida = []
    for f in filas:
        d = _dict(f)
        try:
            d["datos"] = json.loads(d["datos"]) if d["datos"] else {}
        except ValueError:
            d["datos"] = {}
        d["datos"].pop("lead_absorbido", None)       # snapshot pesado, no hace falta en la UI
        salida.append(d)
    return salida


def contacto_de(conn: Connection, lead_id: str) -> dict[str, Any]:
    return _dict(conn.execute(select(contactos).where(contactos.c.lead_id == lead_id)).first())


def ciudades_y_rubros(conn: Connection) -> dict[str, list[str]]:
    ciudades = [r[0] for r in conn.execute(select(leads.c.ciudad).where(leads.c.ciudad.is_not(None))
                                           .distinct().order_by(leads.c.ciudad))]
    return {"ciudades": ciudades}


def metricas(conn: Connection) -> dict[str, Any]:
    por_estado = {e: 0 for e in ESTADOS}
    for estado, n in conn.execute(select(leads.c.estado, func.count()).group_by(leads.c.estado)):
        por_estado[estado] = n

    # Contactado = tiene su fila en `contactos` (hubo primer mensaje). Un "no
    # interesa" puesto sin haberle escrito (descartado a ojo) NO es respuesta.
    contactado = select(contactos.c.id).where(contactos.c.lead_id == leads.c.id).exists()
    respondio = and_(contactado, leads.c.estado.in_(
        ("respondio", "propuesta", "cliente", "no interesa", "no molestar")))

    def _por(columna, limite=15):
        filas = conn.execute(
            select(columna.label("clave"),
                   func.count().label("leads"),
                   func.sum(case((contactado, 1), else_=0)).label("contactados"),
                   func.sum(case((respondio, 1), else_=0)).label("respuestas"),
                   func.sum(case((leads.c.estado == "cliente", 1), else_=0)).label("clientes"),
                   func.coalesce(func.sum(leads.c.monto_cobrado), 0).label("cobrado"))
            .where(columna.is_not(None)).group_by(columna)
            .order_by(func.coalesce(func.sum(leads.c.monto_cobrado), 0).desc(),
                      func.sum(case((contactado, 1), else_=0)).desc(), func.count().desc())
            .limit(limite)).all()
        return [dict(f._mapping) for f in filas]

    costo = conn.execute(select(func.coalesce(func.sum(busquedas.c.costo_usd), 0.0))).scalar_one()
    total_contactados = conn.execute(select(func.count()).select_from(leads).where(contactado)).scalar_one()
    respuestas = conn.execute(select(func.count()).select_from(leads).where(respondio)).scalar_one()
    ultimos = conn.execute(select(contactos.c.fecha_ar, func.count()).where(
        contactos.c.canal != "migrado").group_by(contactos.c.fecha_ar)
        .order_by(contactos.c.fecha_ar.desc()).limit(14)).all()

    # Plata: presupuestos y cobros (en euros)
    con_presupuesto = leads.c.monto_presupuesto > 0
    con_cobro = leads.c.monto_cobrado > 0
    presupuestado, n_presupuestos = conn.execute(select(
        func.coalesce(func.sum(leads.c.monto_presupuesto), 0), func.count()).where(con_presupuesto)).one()
    cobrado, n_cobros = conn.execute(select(
        func.coalesce(func.sum(leads.c.monto_cobrado), 0), func.count()).where(con_cobro)).one()
    presupuestos_cobrados = conn.execute(select(func.count()).where(
        and_(con_presupuesto, con_cobro))).scalar_one()
    mes = func.substr(leads.c.fecha_cobro, 1, 7)
    por_mes = conn.execute(select(mes.label("mes"), func.sum(leads.c.monto_cobrado), func.count())
                           .where(and_(con_cobro, leads.c.fecha_cobro.is_not(None)))
                           .group_by(mes).order_by(mes.desc()).limit(12)).all()
    return {
        "presupuestado": round(float(presupuestado), 2),
        "presupuestos": n_presupuestos,
        "cobrado": round(float(cobrado), 2),
        "cobros": n_cobros,
        "ticket_promedio": round(float(cobrado) / n_cobros, 2) if n_cobros else None,
        "cierre_presupuestos": round(100 * presupuestos_cobrados / n_presupuestos, 1) if n_presupuestos else None,
        "cobrado_por_mes": [{"mes": m, "cobrado": round(float(c), 2), "cobros": n} for m, c, n in por_mes],
        "por_estado": por_estado,
        "total": sum(por_estado.values()),
        "contactados": total_contactados,
        "respuestas": respuestas,
        "clientes": por_estado["cliente"],
        "tasa_respuesta": round(100 * respuestas / total_contactados, 1) if total_contactados else 0,
        "costo_apify_usd": round(float(costo or 0), 2),
        "costo_por_cliente_usd": round(float(costo) / por_estado["cliente"], 2) if por_estado["cliente"] else None,
        "por_rubro": _por(leads.c.rubro),
        "por_ciudad": _por(leads.c.ciudad),
        "por_dia": [{"dia": d, "enviados": n} for d, n in ultimos],
    }


def exportar_filas(conn: Connection) -> list[dict[str, Any]]:
    return [_dict(f) for f in conn.execute(select(leads).order_by(leads.c.creado))]


__all__ = ["ErrorCRM", "a_json"]
