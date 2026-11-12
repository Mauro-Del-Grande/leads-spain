"""
Trabajo de fondo: busquedas de Apify y clasificacion con IA.

Un hilo que da vueltas cada pocos segundos:
  1. Lanza las busquedas 'pendiente' (maximo 2 corriendo a la vez).
  2. Consulta las 'corriendo'; cuando terminan, descarga e importa.
  3. Clasifica con IA los leads 'pendiente'.

Todo el estado vive en la base, no en memoria: si el servidor se reinicia a
mitad de camino, al volver retoma exactamente donde quedo. Una corrida de Apify
ya pagada nunca se pierde.

Las transiciones de estado son UPDATE ... WHERE estado = <el esperado>: si por
algun motivo corrieran dos hilos, solo uno "gana" cada busqueda.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.engine import Engine

from . import apify, crm, db, ia, registros
from .db import ahora_iso, busquedas, leads

log = logging.getLogger(__name__)

MAX_CORRIENDO = 2
MAX_INTENTOS_IA = 3
INTERVALO = 5


# --------------------------------------------------------------------------
# Alta de busquedas
# --------------------------------------------------------------------------
def crear_busquedas(conn, rubro: str, ciudades: list[tuple[str, str]], limite: int,
                    con_contactos: bool, usuario: str | None, zona: str = "") -> list[int]:
    lote = uuid.uuid4().hex[:12] if len(ciudades) > 1 else None
    zona = (zona or "").strip()[:120] if len(ciudades) == 1 else ""   # zona solo con una ciudad
    ids = []
    ahora = ahora_iso()
    for ciudad, provincia in ciudades:
        r = conn.execute(insert(busquedas).values(
            rubro=rubro.strip(), ciudad=ciudad.strip(), provincia=provincia or None, zona=zona or None,
            limite=limite, con_contactos=con_contactos, lote=lote, estado="pendiente",
            usuario=usuario, creado=ahora, actualizado=ahora))
        ids.append(r.inserted_primary_key[0])
    db.evento(conn, "busqueda", None, usuario, rubro=rubro, ciudades=[c for c, _ in ciudades],
              zona=zona or None, limite=limite, con_contactos=con_contactos)
    return ids


def _pasar(motor: Engine, busqueda_id: int, desde: str, hacia: str, **valores: Any) -> bool:
    """Transicion atomica. True si esta llamada fue la que la hizo."""
    with motor.begin() as conn:
        r = conn.execute(update(busquedas)
                         .where(and_(busquedas.c.id == busqueda_id, busquedas.c.estado == desde))
                         .values(estado=hacia, actualizado=ahora_iso(), **valores))
        return r.rowcount == 1


# --------------------------------------------------------------------------
# Importacion
# --------------------------------------------------------------------------
def importar_items(motor: Engine, busqueda: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, int]:
    """
    Pasa los items de Maps por la deduplicacion. Cada candidato va en su propio
    savepoint: uno roto no tumba al resto (queda contado como descartado).
    """
    cont = {"encontrados": len(items), "nuevos": 0, "repetidos": 0, "fusionados": 0, "descartados": 0}
    with motor.begin() as conn:
        db.bloquear(conn, "padron")
        cfg = db.leer_config(conn)
        for item in items:
            cand = apify.normalizar_item(item, busqueda["ciudad"], busqueda.get("provincia") or "")
            if not cand:
                cont["descartados"] += 1
                continue
            try:
                with conn.begin_nested():
                    r = crm.ingresar(conn, cand, cfg, fuente="maps",
                                     usuario=busqueda.get("usuario"), busqueda_id=busqueda["id"])
            except Exception:                                   # noqa: BLE001
                log.exception("No se pudo ingresar %r", cand.get("negocio"))
                cont["descartados"] += 1
                continue
            cont["nuevos" if r["resultado"] == "nuevo" else "repetidos"] += 1
            cont["fusionados"] += len(r["fusionados"])
    return cont


def _procesar_terminada(motor: Engine, b: dict[str, Any], estado_apify: str, costo: float | None,
                        mensaje: str) -> None:
    if not _pasar(motor, b["id"], "corriendo", "importando"):
        return                                   # otro hilo la tomo
    try:
        if b.get("crudo"):
            items = json.loads(b["crudo"])
        else:
            items = apify.items_dataset(b["dataset_id"]) if b.get("dataset_id") else []
            with motor.begin() as conn:          # el crudo se guarda ANTES de importar
                conn.execute(update(busquedas).where(busquedas.c.id == b["id"])
                             .values(crudo=json.dumps(items, ensure_ascii=False)))
        cont = importar_items(motor, b, items)
    except Exception as exc:                     # noqa: BLE001
        log.exception("Fallo importando la busqueda %s", b["id"])
        _pasar(motor, b["id"], "importando", "corriendo", error=f"Reintentando importación: {exc}")
        return
    error = None
    if estado_apify != "SUCCEEDED":
        error = (f"Apify terminó con estado {estado_apify}"
                 + (f" ({mensaje})" if mensaje else "")
                 + f". Se importó lo que alcanzó a traer ({cont['encontrados']}).")
    _pasar(motor, b["id"], "importando", "importada", error=error,
           costo_usd=costo, **cont)


def reimportar(motor: Engine, busqueda_id: int) -> dict[str, int]:
    """Vuelve a pasar el crudo guardado por la deduplicacion (sin pagar de nuevo)."""
    with motor.connect() as conn:
        b = conn.execute(select(busquedas).where(busquedas.c.id == busqueda_id)).first()
    if not b or not b.crudo:
        raise crm.ErrorCRM("Esa búsqueda no tiene datos guardados para reimportar.")
    b = dict(b._mapping)
    cont = importar_items(motor, b, json.loads(b["crudo"]))
    with motor.begin() as conn:
        db.evento(conn, "reimportada", None, None, busqueda_id=busqueda_id, **cont)
    return cont


# --------------------------------------------------------------------------
# Un paso del ciclo
# --------------------------------------------------------------------------
def paso_busquedas(motor: Engine) -> None:
    with motor.connect() as conn:
        corriendo = [dict(r._mapping) for r in conn.execute(
            select(busquedas).where(busquedas.c.estado == "corriendo"))]
        pendientes = [dict(r._mapping) for r in conn.execute(
            select(busquedas).where(busquedas.c.estado == "pendiente")
            .order_by(busquedas.c.id).limit(max(0, MAX_CORRIENDO - len(corriendo))))]

    for b in corriendo:
        if not b.get("run_id"):                  # quedo a medio lanzar: se relanza
            _pasar(motor, b["id"], "corriendo", "pendiente")
            continue
        try:
            st = apify.estado_corrida(b["run_id"])
        except apify.ApifyError as exc:
            log.warning("Busqueda %s: no se pudo consultar Apify: %s", b["id"], exc)
            continue
        if st["estado"] in apify.ESTADOS_FINALES:
            if st["dataset_id"] and not b.get("dataset_id"):
                b["dataset_id"] = st["dataset_id"]
            _procesar_terminada(motor, b, st["estado"], st["costo_usd"], st["mensaje"])

    for b in pendientes:
        if not _pasar(motor, b["id"], "pendiente", "corriendo"):
            continue
        try:
            r = apify.lanzar(apify.entrada_maps(b["rubro"], b["ciudad"], b.get("provincia") or "",
                                                b["limite"], b["con_contactos"], b.get("zona") or ""))
        except apify.ApifyError as exc:
            _pasar(motor, b["id"], "corriendo", "fallida", error=str(exc))
            continue
        with motor.begin() as conn:
            conn.execute(update(busquedas).where(busquedas.c.id == b["id"])
                         .values(run_id=r["run_id"], dataset_id=r["dataset_id"],
                                 actualizado=ahora_iso()))


def paso_ia(motor: Engine, lote: int = 24) -> int:
    """Clasifica hasta `lote` leads pendientes. Devuelve cuantos proceso."""
    if not ia.configurada():
        return 0
    with motor.connect() as conn:
        pend = [dict(r._mapping) for r in conn.execute(
            select(leads).where(leads.c.ia_estado == "pendiente")
            .order_by(leads.c.creado).limit(lote))]
    if not pend:
        return 0
    try:
        res = ia.clasificar(pend)
    except ia.IAError as exc:
        log.warning("IA no disponible: %s", exc)
        return 0
    with motor.begin() as conn:
        for lead in pend:
            campos = res.get(lead["id"])
            if campos:
                # Solo se completa: si alguien ya puso prioridad a mano, se respeta
                actual = crm.obtener(conn, lead["id"])
                if not actual:
                    continue
                valores = {"ia_estado": "ok", "motivo_ia": campos["motivo_ia"],
                           "marca_ia": campos["marca_ia"]}
                if not actual.get("prioridad"):
                    valores["prioridad"] = campos["prioridad"]
                conn.execute(update(leads).where(leads.c.id == lead["id"]).values(**valores))
            else:
                intentos = (lead.get("ia_intentos") or 0) + 1
                conn.execute(update(leads).where(leads.c.id == lead["id"]).values(
                    ia_intentos=intentos,
                    ia_estado="error" if intentos >= MAX_INTENTOS_IA else "pendiente"))
    return len(res)


def paso_registros(motor: Engine, lote: int = 4) -> int:
    """
    Cruce con la EUIPO de los prospectos prometedores (alta/media, sin contactar)
    que todavia no se revisaron, o cuya marca cambio. Pocos por vuelta: es una
    API publica y no hay apuro.
    """
    if not registros.configurado():
        return 0
    with motor.connect() as conn:
        candidatos = [dict(r._mapping) for r in conn.execute(
            select(leads).where(and_(leads.c.estado == "nuevo", leads.c.ia_estado == "ok",
                                     leads.c.prioridad.in_(("alta", "media"))))
            .order_by(leads.c.prioridad, leads.c.creado).limit(200))]
        hechos = registros.leer(conn, [l["id"] for l in candidatos])
    pendientes = []
    for l in candidatos:
        marca = crm.vista(l)["marca"]
        r = hechos.get(l["id"])
        if r and r["marca"] == marca and (r["nivel"] or (r["intentos"] or 0) >= registros.MAX_INTENTOS):
            continue
        pendientes.append((l, marca))
        if len(pendientes) >= lote:
            break
    for l, marca in pendientes:
        try:
            res = registros.revisar(marca, l.get("rubro") or "")
            with motor.begin() as conn:
                registros.guardar(conn, l["id"], marca, res)
        except registros.RegistroError as exc:
            log.warning("Registros: %s", exc)
            with motor.begin() as conn:
                registros.guardar(conn, l["id"], marca, None, str(exc))
            break                                   # si la EUIPO falla, no insistir esta vuelta
    return len(pendientes)


def recuperar_al_iniciar(motor: Engine) -> None:
    """Una importacion cortada por un reinicio vuelve a 'corriendo' (es idempotente)."""
    with motor.begin() as conn:
        conn.execute(update(busquedas).where(busquedas.c.estado == "importando")
                     .values(estado="corriendo", actualizado=ahora_iso()))


# --------------------------------------------------------------------------
# Hilo
# --------------------------------------------------------------------------
_hilo: threading.Thread | None = None
_parar = threading.Event()


def iniciar(motor: Engine) -> None:
    global _hilo
    if _hilo and _hilo.is_alive():
        return
    recuperar_al_iniciar(motor)
    _parar.clear()

    def _ciclo() -> None:
        while not _parar.is_set():
            for paso in (paso_busquedas, paso_ia, paso_registros):
                try:
                    paso(motor)
                except Exception:                  # noqa: BLE001 - el hilo no se muere nunca
                    log.exception("Error en %s", paso.__name__)
            _parar.wait(INTERVALO)

    _hilo = threading.Thread(target=_ciclo, name="trabajos", daemon=True)
    _hilo.start()


def detener() -> None:
    _parar.set()
