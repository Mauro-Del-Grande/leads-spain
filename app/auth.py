"""Usuarios, login y proteccion de rutas."""
from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps
from typing import Any, Callable

from flask import abort, g, jsonify, redirect, request, session, url_for
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection
from werkzeug.security import check_password_hash, generate_password_hash

from .db import ahora_iso, usuarios

MIN_LARGO_CLAVE = 10


class ErrorUsuario(Exception):
    pass


def crear_usuario(conn: Connection, email: str, nombre: str, clave: str) -> int:
    email = (email or "").strip().lower()
    nombre = (nombre or "").strip()
    if "@" not in email or not nombre:
        raise ErrorUsuario("Email y nombre son obligatorios.")
    if len(email) > 200 or len(nombre) > 100:
        raise ErrorUsuario("Email o nombre demasiado largos.")
    if len(clave or "") < MIN_LARGO_CLAVE:
        raise ErrorUsuario(f"La contraseña tiene que tener al menos {MIN_LARGO_CLAVE} caracteres.")
    if conn.execute(select(usuarios.c.id).where(usuarios.c.email == email)).first():
        raise ErrorUsuario("Ya existe un usuario con ese email.")
    r = conn.execute(insert(usuarios).values(email=email, nombre=nombre, activo=True,
                                             hash=generate_password_hash(clave),
                                             creado=ahora_iso()))
    return r.inserted_primary_key[0]


def cambiar_clave(conn: Connection, usuario_id: int, actual: str, nueva: str) -> None:
    fila = conn.execute(select(usuarios).where(usuarios.c.id == usuario_id)).first()
    if not fila or not check_password_hash(fila.hash, actual or ""):
        raise ErrorUsuario("La contraseña actual no es correcta.")
    if len(nueva or "") < MIN_LARGO_CLAVE:
        raise ErrorUsuario(f"La contraseña nueva tiene que tener al menos {MIN_LARGO_CLAVE} caracteres.")
    conn.execute(update(usuarios).where(usuarios.c.id == usuario_id)
                 .values(hash=generate_password_hash(nueva)))


def verificar(conn: Connection, email: str, clave: str) -> dict[str, Any] | None:
    fila = conn.execute(select(usuarios).where(
        func.lower(usuarios.c.email) == (email or "").strip().lower())).first()
    if not fila:
        check_password_hash(generate_password_hash("x"), clave or "")   # mismo tiempo
        return None
    if not fila.activo or not check_password_hash(fila.hash, clave or ""):
        return None
    return {"id": fila.id, "email": fila.email, "nombre": fila.nombre}


def hay_usuarios(conn: Connection) -> bool:
    return conn.execute(select(func.count()).select_from(usuarios)).scalar_one() > 0


# --------------------------------------------------------------------------
# Limite de intentos de login (por IP, en memoria: un solo proceso)
# --------------------------------------------------------------------------
_intentos: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()
VENTANA = 15 * 60
MAX_FALLOS = 8


def login_bloqueado(ip: str) -> bool:
    ahora = time.time()
    with _lock:
        q = _intentos[ip]
        while q and ahora - q[0] > VENTANA:
            q.popleft()
        return len(q) >= MAX_FALLOS


def registrar_fallo(ip: str) -> None:
    with _lock:
        _intentos[ip].append(time.time())


def limpiar_fallos(ip: str) -> None:
    with _lock:
        _intentos.pop(ip, None)


# --------------------------------------------------------------------------
# Decoradores y CSRF
# --------------------------------------------------------------------------
def usuario_actual() -> dict[str, Any] | None:
    return g.get("usuario")


def requiere_login(api: bool = True) -> Callable:
    def deco(fn):
        @wraps(fn)
        def envoltura(*a, **kw):
            if not g.get("usuario"):
                if api:
                    return jsonify({"error": "Tu sesión se cerró. Vuelve a entrar."}), 401
                return redirect(url_for("login"))
            # Toda escritura de la API va en JSON: un formulario de otro sitio
            # no puede mandar application/json sin preflight (anti-CSRF).
            if api and request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.is_json:
                abort(415)
            return fn(*a, **kw)
        return envoltura
    return deco


def token_csrf() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def csrf_valido(valor: str | None) -> bool:
    esperado = session.get("csrf")
    return bool(esperado and valor and secrets.compare_digest(esperado, valor))
