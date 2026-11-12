"""
Claves de servicios externos (Apify y Anthropic) cargadas desde la app.

Para que cada despacho use SUS cuentas sin pasarle las claves a nadie: las
pegan en Ajustes -> Conexiones, la app las prueba y las guarda CIFRADAS en la
base (tabla `config`, clave "clave:<servicio>"). Nunca se vuelven a mostrar:
la UI solo ve los ultimos 4 caracteres.

- Cifrado: Fernet (AES + HMAC) con una llave derivada de SECRET_KEY. Si un dia
  cambia SECRET_KEY, las claves guardadas no se pueden leer: la app lo avisa y
  hay que volver a pegarlas (no se rompe nada mas).
- Prioridad: la clave cargada en la app gana; si no hay, se usa la variable de
  entorno (APIFY_TOKEN / ANTHROPIC_API_KEY), como antes.
- Las claves viven en memoria (`_cache`) para no ir a la base en cada llamada;
  se cargan al arrancar y se actualizan al guardar o quitar.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, update
from sqlalchemy.engine import Connection, Engine

from . import db

SERVICIOS: dict[str, dict[str, str]] = {
    "apify": {"nombre": "Apify", "entorno": "APIFY_TOKEN"},
    "anthropic": {"nombre": "Anthropic (IA)", "entorno": "ANTHROPIC_API_KEY"},
    # JSON {"client_id": ..., "client_secret": ...} (ver registros.py)
    "euipo": {"nombre": "EUIPO (registro de marcas de la UE)", "entorno": "EUIPO_CREDENCIALES"},
}

_cache: dict[str, str] = {}
_ilegibles: set[str] = set()
_lock = threading.Lock()


def _fernet() -> Fernet:
    secreto = os.environ.get("SECRET_KEY") or "solo-desarrollo-no-usar-en-produccion"
    llave = hashlib.sha256(("claves:" + secreto).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(llave))


def _clave_config(servicio: str) -> str:
    return f"clave:{servicio}"


def cargar(motor: Engine) -> None:
    """Lee las claves guardadas (al arrancar)."""
    with motor.connect() as conn:
        filas = conn.execute(select(db.config.c.clave, db.config.c.valor)
                             .where(db.config.c.clave.like("clave:%"))).all()
    nuevas, ilegibles = {}, set()
    for f in filas:
        servicio = f.clave.split(":", 1)[1]
        if servicio not in SERVICIOS:
            continue
        try:
            cifrado = json.loads(f.valor)
            nuevas[servicio] = _fernet().decrypt(cifrado.encode()).decode()
        except (ValueError, InvalidToken, AttributeError):
            ilegibles.add(servicio)
    with _lock:
        _cache.clear()
        _cache.update(nuevas)
        _ilegibles.clear()
        _ilegibles.update(ilegibles)


def valor(servicio: str) -> str:
    """La clave a usar: la de la app y, si no hay, la variable de entorno."""
    with _lock:
        propia = _cache.get(servicio, "")
    return propia or os.environ.get(SERVICIOS[servicio]["entorno"], "").strip()


def guardar(conn: Connection, servicio: str, clave: str, usuario: str | None) -> None:
    cifrado = db.a_json(_fernet().encrypt(clave.encode()).decode())
    k = _clave_config(servicio)
    if not db.insertar_si_no_existe(conn, db.config, {"clave": k, "valor": cifrado}):
        conn.execute(update(db.config).where(db.config.c.clave == k).values(valor=cifrado))
    # Nunca la clave en el historial: solo que cambio y como termina.
    db.evento(conn, "clave", None, usuario, servicio=servicio, termina_en=_visible(servicio, clave))
    with _lock:
        _cache[servicio] = clave
        _ilegibles.discard(servicio)


def quitar(conn: Connection, servicio: str, usuario: str | None) -> None:
    conn.execute(delete(db.config).where(db.config.c.clave == _clave_config(servicio)))
    db.evento(conn, "clave_quitada", None, usuario, servicio=servicio)
    with _lock:
        _cache.pop(servicio, None)
        _ilegibles.discard(servicio)


def resumen() -> dict[str, Any]:
    """Estado de cada servicio para la UI. NUNCA incluye la clave."""
    salida = {}
    with _lock:
        cache, ilegibles = dict(_cache), set(_ilegibles)
    for servicio, info in SERVICIOS.items():
        propia = cache.get(servicio, "")
        entorno = os.environ.get(info["entorno"], "").strip()
        usada = propia or entorno
        salida[servicio] = {
            "nombre": info["nombre"],
            "configurada": bool(usada),
            "origen": "app" if propia else ("servidor" if entorno else ""),
            "termina_en": _visible(servicio, usada),
            "ilegible": servicio in ilegibles,
        }
    return salida


def _visible(servicio: str, valor: str) -> str:
    """Lo unico que se muestra: los ultimos 4 caracteres (del Client ID en la EUIPO)."""
    if not valor:
        return ""
    if servicio == "euipo":
        try:
            valor = str(json.loads(valor).get("client_id") or "")
        except (ValueError, AttributeError):
            return ""
    return valor[-4:]


def limpiar(clave: Any) -> str:
    """Lo que pego el usuario, sin espacios ni comillas alrededor."""
    return str(clave or "").strip().strip('"').strip("'").strip()
