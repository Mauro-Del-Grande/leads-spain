"""
Leads España — servidor Flask.

    flask --app app run            (desarrollo)
    gunicorn "app:create_app()"    (produccion, ver railway.json)
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from datetime import timedelta
from typing import Any

from flask import (Flask, Response, g, jsonify, redirect, render_template,
                   request, session, url_for)
from sqlalchemy import select, update
from werkzeug.middleware.proxy_fix import ProxyFix

from . import apify, auth, claves, crm, db, historial, ia, trabajos
from .crm import ErrorCRM


# Tope de ciudades por barrido. Existe para que un "marcar todas" sin querer no
# dispare 250 corridas de Apify de una; apify.PRINCIPALES entra holgado debajo.
MAX_CIUDADES_BARRIDO = 60


def _cargar_env_local() -> None:
    """Lee un .env en la raiz (solo desarrollo). En Railway van como variables."""
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, _, v = linea.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def en_produccion() -> bool:
    return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PRODUCCION"))


def create_app(config: dict[str, Any] | None = None) -> Flask:
    _cargar_env_local()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)   # detras del proxy de Railway
    secreto = os.environ.get("SECRET_KEY", "")
    if not secreto:
        if en_produccion():
            raise RuntimeError("Falta SECRET_KEY en las variables de entorno.")
        secreto = "solo-desarrollo-no-usar-en-produccion"
    app.config.update(
        SECRET_KEY=secreto,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=en_produccion(),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        JSON_AS_ASCII=False,
    )
    app.json.ensure_ascii = False
    if config:
        app.config.update(config)

    motor = db.crear_motor(app.config.get("DATABASE_URL"))
    db.inicializar(motor)
    claves.cargar(motor)
    app.extensions["motor"] = motor
    _usuario_inicial(motor)
    if app.config.get("TRABAJOS", os.environ.get("TRABAJOS", "1") != "0"):
        trabajos.iniciar(motor)

    # Version de los estaticos: cambia con cada deploy, asi el móvil no se
    # queda con un app.js viejo en cache.
    import hashlib
    h = hashlib.sha256()
    for nombre in ("app.js", "app.css"):
        with open(os.path.join(app.static_folder, nombre), "rb") as fh:
            h.update(fh.read())
    version = h.hexdigest()[:10]

    @app.context_processor
    def _version():
        return {"v": version}

    _rutas(app, motor)
    _cli(app, motor)
    return app


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
def _rutas(app: Flask, motor) -> None:

    @app.before_request
    def _cargar_usuario():
        g.usuario = None
        uid = session.get("uid")
        if uid:
            with motor.connect() as conn:
                fila = conn.execute(select(db.usuarios).where(db.usuarios.c.id == uid)).first()
            if fila and fila.activo:
                g.usuario = {"id": fila.id, "email": fila.email, "nombre": fila.nombre}
            else:
                session.clear()

    @app.after_request
    def _cabeceras(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        # El HTML tampoco se cachea: una pagina vieja en el móvil se queda sin
        # los elementos que espera el app.js nuevo. Los estaticos si, van con ?v=hash.
        if request.path.startswith("/api/") or resp.mimetype == "text/html":
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.errorhandler(ErrorCRM)
    def _error_crm(exc):
        return jsonify({"error": str(exc)}), 409

    @app.errorhandler(auth.ErrorUsuario)
    def _error_usuario(exc):
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(apify.ApifyError)
    def _error_apify(exc):
        return jsonify({"error": str(exc)}), 502

    @app.errorhandler(ia.IAError)
    def _error_ia(exc):
        return jsonify({"error": str(exc)}), 502

    @app.errorhandler(415)
    def _no_json(_):
        return jsonify({"error": "Pedido inválido."}), 415

    @app.errorhandler(500)
    def _error_500(_):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Error interno. No se guardó nada; prueba de nuevo."}), 500
        return "Error interno", 500

    def usuario_nombre() -> str | None:
        u = auth.usuario_actual()
        return u["nombre"] if u else None

    def cuerpo() -> dict[str, Any]:
        d = request.get_json(silent=True)
        return d if isinstance(d, dict) else {}

    # ---------------------------------------------------------------- paginas
    @app.get("/salud")
    def salud():
        with motor.connect() as conn:
            conn.execute(select(1))
        return {"ok": True}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        with motor.connect() as conn:
            if not auth.hay_usuarios(conn):
                return render_template("login.html", sin_usuarios=True, csrf=auth.token_csrf())
        error = ""
        if request.method == "POST":
            ip = request.remote_addr or "?"
            if not auth.csrf_valido(request.form.get("csrf")):
                error = "La página venció. Vuelve a intentarlo."
            elif auth.login_bloqueado(ip):
                error = "Demasiados intentos. Espera 15 minutos."
            else:
                with motor.connect() as conn:
                    u = auth.verificar(conn, request.form.get("email", ""), request.form.get("clave", ""))
                if u:
                    auth.limpiar_fallos(ip)
                    session.clear()
                    session.permanent = True
                    session["uid"] = u["id"]
                    return redirect(url_for("inicio"))
                auth.registrar_fallo(ip)
                error = "Email o contraseña incorrectos."
        return render_template("login.html", error=error, csrf=auth.token_csrf(),
                               email=request.form.get("email", ""))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @auth.requiere_login(api=False)
    def inicio():
        return render_template("index.html", usuario=auth.usuario_actual())

    # ---------------------------------------------------------------- estado
    @app.get("/api/estado")
    @auth.requiere_login()
    def api_estado():
        with motor.connect() as conn:
            cfg = db.leer_config(conn)
            activas = conn.execute(select(db.busquedas.c.id).where(
                db.busquedas.c.estado.in_(("pendiente", "corriendo", "importando")))).all()
            ia_pend = conn.execute(select(db.leads.c.id).where(
                db.leads.c.ia_estado == "pendiente")).all()
            return jsonify({
                "usuario": auth.usuario_actual(), "contactados_hoy": crm.contactados_hoy(conn),
                "busquedas_activas": len(activas), "ia_pendientes": len(ia_pend),
                "apify": apify.configurado(), "ia": ia.configurada(),
                "hoy": db.hoy_local()})

    # ------------------------------------------------------------------- hoy
    @app.get("/api/hoy")
    @auth.requiere_login()
    def api_hoy():
        with motor.connect() as conn:
            c = crm.cola(conn)
            return jsonify({"pendientes": c["pendientes"], "contactados_hoy": c["contactados_hoy"],
                            "cola": [crm.vista(l) for l in c["leads"]],
                            "canales_contacto": list(crm.CANALES_CONTACTO.items()),
                            "seguimientos": [crm.vista(l) for l in crm.seguimientos(conn)]})

    @app.post("/api/contacto/registrar")
    @auth.requiere_login()
    def api_registrar_contacto():
        """Constancia de un contacto hecho POR FUERA de la app. No envia nada."""
        d = cuerpo()
        with motor.begin() as conn:
            r = crm.registrar_contacto(conn, d.get("lead_id", ""), d.get("canal", ""),
                                       d.get("nota") or "", usuario_nombre())
            r["contactados_hoy"] = crm.contactados_hoy(conn)
            return jsonify(r)

    # ----------------------------------------------------------------- leads
    @app.get("/api/leads")
    @auth.requiere_login()
    def api_leads():
        a = request.args
        with motor.connect() as conn:
            r = crm.buscar(conn, q=a.get("q", ""), estado=a.get("estado", ""),
                           prioridad=a.get("prioridad", ""), ciudad=a.get("ciudad", ""),
                           rubro=a.get("rubro", ""), canal=a.get("canal", ""),
                           pagina=_entero(a.get("pagina"), 1))
            r["leads"] = [crm.vista(l) for l in r["leads"]]
            r.update(crm.ciudades_y_rubros(conn))
            return jsonify(r)

    @app.get("/api/leads/<lead_id>")
    @auth.requiere_login()
    def api_lead(lead_id):
        with motor.connect() as conn:
            lead = crm.obtener(conn, lead_id)
            if not lead:
                return jsonify({"error": "Ese lead ya no existe (puede haberse fusionado)."}), 404
            return jsonify({"lead": crm.vista(lead), "canales_contacto": list(crm.CANALES_CONTACTO.items()), "historial": crm.historial(conn, lead_id),
                            "contacto": crm.contacto_de(conn, lead_id)})

    @app.patch("/api/leads/<lead_id>")
    @auth.requiere_login()
    def api_editar(lead_id):
        with motor.begin() as conn:
            lead = crm.editar(conn, lead_id, cuerpo(), usuario_nombre(), db.leer_config(conn))
            return jsonify({"lead": crm.vista(lead)})

    @app.post("/api/leads/<lead_id>/estado")
    @auth.requiere_login()
    def api_estado_lead(lead_id):
        d = cuerpo()
        with motor.begin() as conn:
            lead = crm.cambiar_estado(conn, lead_id, d.get("estado", ""), usuario_nombre(),
                                      reabrir=bool(d.get("reabrir")))
            return jsonify({"lead": crm.vista(lead)})

    @app.post("/api/leads/<lead_id>/nota")
    @auth.requiere_login()
    def api_nota(lead_id):
        with motor.begin() as conn:
            crm.agregar_nota(conn, lead_id, cuerpo().get("texto", ""), usuario_nombre())
            return jsonify({"ok": True})

    @app.post("/api/leads/fusionar")
    @auth.requiere_login()
    def api_fusionar():
        d = cuerpo()
        with motor.begin() as conn:
            base = crm.fusionar_manual(conn, d.get("a", ""), d.get("b", ""), usuario_nombre())
            return jsonify({"lead_id": base})

    # ------------------------------------------------------------- busquedas
    @app.get("/api/ciudades")
    @auth.requiere_login()
    def api_ciudades():
        return jsonify({"ciudades": [{"ciudad": c, "provincia": p,
                                      "principal": c in apify.PRINCIPALES}
                                     for c, p in apify.CIUDADES],
                        "max_barrido": MAX_CIUDADES_BARRIDO})

    @app.get("/api/rubros")
    @auth.requiere_login()
    def api_rubros():
        return jsonify({"familias": [{"nombre": f, "rubros": rs}
                                     for f, rs in historial.RUBROS.items()]})

    @app.get("/api/busquedas")
    @auth.requiere_login()
    def api_busquedas():
        cols = [c for c in db.busquedas.c if c.name != "crudo"]
        with motor.connect() as conn:
            filas = conn.execute(select(*cols).order_by(db.busquedas.c.id.desc()).limit(60)).all()
            return jsonify({"busquedas": [dict(f._mapping) for f in filas]})

    @app.post("/api/busquedas")
    @auth.requiere_login()
    def api_nueva_busqueda():
        d = cuerpo()
        rubro = (d.get("rubro") or "").strip()
        if not rubro or len(rubro) > 120:
            return jsonify({"error": "Escribe qué rubro buscar (ej: panaderías)."}), 400
        limite = _entero(d.get("limite"), 40)
        if not 1 <= limite <= 300:
            return jsonify({"error": "La cantidad por ciudad tiene que estar entre 1 y 300."}), 400
        ciudades: list[tuple[str, str]] = []
        for c in d.get("ciudades") or []:
            nombre = str((c.get("ciudad") if isinstance(c, dict) else c) or "").strip()
            if nombre and nombre.lower() not in [x.lower() for x, _ in ciudades]:
                prov = (c.get("provincia") if isinstance(c, dict) else "") or apify.provincia_de(nombre)
                ciudades.append((nombre[:120], (prov or "")[:120]))
        if not ciudades:
            return jsonify({"error": "Elige al menos una ciudad."}), 400
        if len(ciudades) > MAX_CIUDADES_BARRIDO:
            return jsonify({"error": f"Máximo {MAX_CIUDADES_BARRIDO} ciudades por barrido."}), 400
        con_contactos = bool(d.get("con_contactos"))
        if not apify.configurado():
            return jsonify({"error": "Falta la clave de Apify: cárgala en Ajustes → Conexiones."}), 400
        zona = (d.get("zona") or "").strip()[:120] if len(ciudades) == 1 else ""
        with motor.begin() as conn:
            db.bloquear(conn, "busquedas")
            analisis = [historial.analizar(conn, rubro, ciudad, zona, limite) for ciudad, _ in ciudades]
            en_curso = [a["clave"]["ciudad"] for a in analisis if a["aviso"] == "en_curso"]
            if en_curso:
                return jsonify({"error": "Esa búsqueda ya está en marcha en: " + ", ".join(en_curso)
                                + ". Espera a que termine."}), 409
            if not d.get("confirmar"):
                repetidas = [{
                    "ciudad": a["clave"]["ciudad"], "aviso": a["aviso"],
                    "nuevos_esperados": a["nuevos_esperados"],
                    "veces": a["misma"]["veces"], "ultima_fecha": a["misma"]["ultima_fecha"],
                    "max_limite": a["misma"]["max_limite"], "nuevos": a["misma"]["nuevos"],
                    "rendimiento_ultima": a["misma"]["rendimiento_ultima"],
                } for a in analisis if a["misma"]]
                return jsonify({"confirmar": True, "repetidas": repetidas,
                                "costo_estimado_usd": apify.estimar_costo(limite * len(ciudades), con_contactos),
                                "ciudades": len(ciudades)})
            ids = trabajos.crear_busquedas(conn, rubro, ciudades, limite, con_contactos, usuario_nombre(), zona)
        return jsonify({"ids": ids})

    @app.get("/api/busquedas/analizar")
    @auth.requiere_login()
    def api_analizar_busqueda():
        a = request.args
        rubro, ciudad = (a.get("rubro") or "").strip(), (a.get("ciudad") or "").strip()
        if not rubro or not ciudad:
            return jsonify({"vacio": True})
        with motor.connect() as conn:
            return jsonify(historial.analizar(conn, rubro[:120], ciudad[:120], (a.get("zona") or "").strip()[:120],
                                              max(1, min(300, _entero(a.get("limite"), 40)))))

    @app.get("/api/busquedas/historial")
    @auth.requiere_login()
    def api_historial_busquedas():
        with motor.connect() as conn:
            return jsonify({"historial": historial.historial(conn)})

    @app.post("/api/busquedas/<int:bid>/reimportar")
    @auth.requiere_login()
    def api_reimportar(bid):
        return jsonify(trabajos.reimportar(motor, bid))

    # --------------------------------------------------------------- metricas
    @app.get("/api/metricas")
    @auth.requiere_login()
    def api_metricas():
        with motor.connect() as conn:
            return jsonify(crm.metricas(conn))

    # ------------------------------------------------------------------ config
    @app.get("/api/config")
    @auth.requiere_login()
    def api_config():
        with motor.connect() as conn:
            usuarios = [dict(f._mapping) for f in conn.execute(
                select(db.usuarios.c.id, db.usuarios.c.email, db.usuarios.c.nombre, db.usuarios.c.activo))]
            return jsonify({"config": db.leer_config(conn), "usuarios": usuarios,
                            "modelo_ia": ia.MODELO})

    @app.post("/api/config")
    @auth.requiere_login()
    def api_guardar_config():
        d = cuerpo()
        valores = {}
        if "ciudad_default" in d:
            valores["ciudad_default"] = str(d["ciudad_default"]).strip()[:120]
        if "limite_busqueda" in d:
            valores["limite_busqueda"] = max(1, min(300, _entero(d["limite_busqueda"], 40)))
        with motor.begin() as conn:
            db.guardar_config(conn, valores)
            if valores:
                db.evento(conn, "config", None, usuario_nombre(), **valores)
            return jsonify({"config": db.leer_config(conn)})

    # ----------------------------------------------------------------- claves
    @app.get("/api/claves")
    @auth.requiere_login()
    def api_claves():
        return jsonify({"claves": claves.resumen()})

    @app.post("/api/claves/<servicio>")
    @auth.requiere_login()
    def api_guardar_clave(servicio):
        """Prueba la clave contra el servicio y, si anda, la guarda cifrada."""
        if servicio not in claves.SERVICIOS:
            return jsonify({"error": "Servicio desconocido."}), 404
        clave = claves.limpiar(cuerpo().get("clave"))
        if len(clave) < 20 or len(clave) > 400 or any(c.isspace() for c in clave):
            return jsonify({"error": "Eso no parece una clave: cópiala completa, sin espacios."}), 400
        if servicio == "apify":
            cuenta = apify.probar_token(clave)
            ok = f"Apify conectado (cuenta {cuenta})."
        else:
            ok = f"IA conectada: {ia.probar(clave)}."
        with motor.begin() as conn:
            claves.guardar(conn, servicio, clave, usuario_nombre())
        return jsonify({"ok": ok, "claves": claves.resumen()})

    @app.delete("/api/claves/<servicio>")
    @auth.requiere_login()
    def api_quitar_clave(servicio):
        if servicio not in claves.SERVICIOS:
            return jsonify({"error": "Servicio desconocido."}), 404
        with motor.begin() as conn:
            claves.quitar(conn, servicio, usuario_nombre())
        return jsonify({"claves": claves.resumen()})

    @app.post("/api/probar/<que>")
    @auth.requiere_login()
    def api_probar(que):
        if que == "apify":
            return jsonify({"ok": f"Apify conectado: {apify.probar_token()}"})
        if que == "ia":
            return jsonify({"ok": f"IA conectada: {ia.probar()}"})
        return jsonify({"error": "?"}), 404

    @app.post("/api/usuarios")
    @auth.requiere_login()
    def api_crear_usuario():
        d = cuerpo()
        with motor.begin() as conn:
            auth.crear_usuario(conn, d.get("email", ""), d.get("nombre", ""), d.get("clave", ""))
            db.evento(conn, "usuario", None, usuario_nombre(), email=d.get("email", ""))
        return jsonify({"ok": True})

    @app.post("/api/usuarios/clave")
    @auth.requiere_login()
    def api_cambiar_clave():
        d = cuerpo()
        with motor.begin() as conn:
            auth.cambiar_clave(conn, auth.usuario_actual()["id"], d.get("actual", ""), d.get("nueva", ""))
        return jsonify({"ok": True})

    # ---------------------------------------------------------------- export
    @app.get("/api/exportar.csv")
    @auth.requiere_login()
    def api_exportar_csv():
        with motor.connect() as conn:
            filas = crm.exportar_filas(conn)
        cols = [c.name for c in db.leads.c]
        buf = io.StringIO()
        buf.write("﻿")                          # BOM: Excel respeta los acentos
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", delimiter=";")
        w.writeheader()
        for f in filas:
            w.writerow({k: ("" if v is None else v) for k, v in f.items()})
        return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=leads-{db.hoy_local()}.csv"})

    @app.get("/api/exportar.json")
    @auth.requiere_login()
    def api_exportar_json():
        """Backup completo (leads, claves, contactos, eventos, busquedas sin crudo)."""
        with motor.connect() as conn:
            datos = {
                "generado": db.ahora_iso(),
                "leads": crm.exportar_filas(conn),
                "claves": [dict(f._mapping) for f in conn.execute(select(db.claves))],
                "contactos": [dict(f._mapping) for f in conn.execute(select(db.contactos))],
                "eventos": [dict(f._mapping) for f in conn.execute(select(db.eventos))],
                "busquedas": [dict(f._mapping) for f in conn.execute(
                    select(*[c for c in db.busquedas.c if c.name != "crudo"]))],
                "config": db.leer_config(conn),
            }
        return Response(json.dumps(datos, ensure_ascii=False, default=str),
                        mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=backup-{db.hoy_local()}.json"})


def _usuario_inicial(motor) -> None:
    """
    Crea el primer usuario desde variables de entorno, SOLO si no hay ninguno.
    Evita tener que abrir una consola en Railway. Despues del primer login se
    pueden borrar esas variables.
    """
    email = os.environ.get("USUARIO_INICIAL_EMAIL", "").strip()
    clave = os.environ.get("USUARIO_INICIAL_CLAVE", "")
    if not (email and clave):
        return
    with motor.begin() as conn:
        db.bloquear(conn, "usuarios")
        if auth.hay_usuarios(conn):
            return
        auth.crear_usuario(conn, email, os.environ.get("USUARIO_INICIAL_NOMBRE", "Admin"), clave)
        logging.getLogger(__name__).info("Usuario inicial %s creado", email)


def _entero(v: Any, defecto: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return defecto


# --------------------------------------------------------------------------
# Comandos de consola
# --------------------------------------------------------------------------
def _cli(app: Flask, motor) -> None:
    import click

    @app.cli.command("crear-usuario")
    @click.argument("email")
    @click.argument("nombre")
    @click.password_option("--clave", prompt="Contraseña")
    def crear_usuario_cmd(email, nombre, clave):
        """Crea un usuario: flask --app app crear-usuario mail@x.com "Nombre" """
        with motor.begin() as conn:
            auth.crear_usuario(conn, email, nombre, clave)
        click.echo(f"Usuario {email} creado.")
