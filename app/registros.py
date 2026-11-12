"""
Cruce con registros de marcas: ¿hay en la Unión Europea una marca igual o
parecida que pueda ser un riesgo para este negocio?

Fuente: API oficial de la EUIPO (Trademark Search). Las marcas de la UE tienen
efecto en España, así que una marca de la UE igual o parecida en clases
relacionadas es justo el riesgo que interesa detectar.

    OAuth2 client_credentials  ->  token (dura ~1 h, se cachea)
    GET /trademarks?query=<RSQL>&size=50   con X-IBM-Client-Id + Bearer

Las credenciales (Client ID y Client Secret) las saca el despacho en
dev.euipo.europa.eu y las carga en Ajustes -> Conexiones (claves.py, servicio
"euipo", guardado como JSON cifrado).

El resultado es un SEMÁFORO orientativo, no un dictamen:
  - rojo:     marca activa idéntica, que suena igual o muy parecida, en una
              clase de Niza que coincide con la actividad del negocio.
  - amarillo: lo mismo pero en otras clases, o algo parecido en clases afines.
  - verde:    nada relevante en la UE.
Decidir si hay riesgo de confusión es trabajo de la abogada.

OEPM (marcas nacionales): su servicio web pide usuario y contraseña que da la
propia OEPM tras un formulario; cuando los tengan, se suma aquí como otra
fuente. Mientras tanto la ficha ofrece el enlace al Localizador.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from typing import Any
from urllib.parse import quote

import requests

from . import claves

log = logging.getLogger(__name__)

ENTORNOS = {
    "produccion": {"api": "https://api.euipo.europa.eu/trademark-search",
                   "token": "https://euipo.europa.eu/cas-server-webapp/oidc/accessToken"},
    "sandbox": {"api": "https://api-sandbox.euipo.europa.eu/trademark-search",
                "token": "https://auth-sandbox.euipo.europa.eu/oidc/accessToken"},
}
# Estados que ya no protegen nada: se ignoran.
INACTIVAS = {"REFUSED", "WITHDRAWN", "CANCELLED", "SURRENDERED", "EXPIRED", "REMOVED_FROM_REGISTER"}
ESTADO_ES = {
    "RECEIVED": "recibida", "UNDER_EXAMINATION": "en examen", "APPLICATION_PUBLISHED": "solicitud publicada",
    "REGISTRATION_PENDING": "registro pendiente", "REGISTERED": "registrada",
    "OPPOSITION_PENDING": "oposición pendiente", "APPEALED": "recurrida",
    "CANCELLATION_PENDING": "nulidad pendiente", "APPEALABLE": "recurrible",
    "START_OF_OPPOSITION_PERIOD": "en plazo de oposición", "ACCEPTANCE_PENDING": "aceptación pendiente",
    "ACCEPTED": "aceptada",
}


class RegistroError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Credenciales y token
# --------------------------------------------------------------------------
def _entorno() -> dict[str, str]:
    return ENTORNOS.get(os.environ.get("EUIPO_ENTORNO", "produccion"), ENTORNOS["produccion"])


def credenciales(valor: str | None = None) -> tuple[str, str]:
    """(client_id, client_secret) guardados en claves (JSON), o ("", "")."""
    crudo = valor if valor is not None else claves.valor("euipo")
    try:
        d = json.loads(crudo) if crudo else {}
        return str(d.get("client_id") or ""), str(d.get("client_secret") or "")
    except ValueError:
        return "", ""


def configurado() -> bool:
    cid, sec = credenciales()
    return bool(cid and sec)


_token: dict[str, Any] = {}
_lock = threading.Lock()


def _pedir_token(cid: str, sec: str) -> tuple[str, int]:
    try:
        r = requests.post(_entorno()["token"], timeout=30, data={
            "grant_type": "client_credentials", "client_id": cid,
            "client_secret": sec, "scope": "uid"})
    except requests.RequestException as exc:
        log.warning("EUIPO: error de conexion al pedir token: %s", exc)
        raise RegistroError("No se pudo conectar con la EUIPO. Vuelve a intentarlo en un rato.") from exc
    if r.status_code in (400, 401, 403):
        raise RegistroError("La EUIPO rechazó el Client ID o el Client Secret. Revisa que estén bien copiados.")
    if not r.ok:
        raise RegistroError(f"La EUIPO respondió {r.status_code} al pedir acceso.")
    try:
        datos = r.json()
        return datos["access_token"], int(datos.get("expires_in") or 3600)
    except (ValueError, KeyError) as exc:
        raise RegistroError("La EUIPO devolvió una respuesta inesperada al pedir acceso.") from exc


def _token_para(cid: str, sec: str, forzar: bool = False) -> str:
    with _lock:
        if (not forzar and _token.get("cid") == cid and _token.get("sec") == sec
                and _token.get("vence", 0) > time.time() + 60):
            return _token["valor"]
    valor, dura = _pedir_token(cid, sec)
    with _lock:
        _token.update(cid=cid, sec=sec, valor=valor, vence=time.time() + dura)
    return valor


def _buscar(query: str, cid: str, sec: str, size: int = 50) -> list[dict[str, Any]]:
    url = _entorno()["api"] + "/trademarks"
    for intento in range(2):
        headers = {"Accept": "application/json", "X-IBM-Client-Id": cid,
                   "Authorization": f"Bearer {_token_para(cid, sec, forzar=intento > 0)}"}
        try:
            r = requests.get(url, headers=headers, timeout=45,
                             params={"query": query, "size": size, "page": 0})
        except requests.RequestException as exc:
            log.warning("EUIPO: error de conexion: %s", exc)
            raise RegistroError("No se pudo conectar con la EUIPO. Vuelve a intentarlo en un rato.") from exc
        if r.status_code == 401 and intento == 0:
            continue                                  # token vencido: se pide otro
        if r.status_code in (401, 403):
            raise RegistroError("La EUIPO no autoriza la búsqueda. Revisa que la app tenga la suscripción a "
                                "'Trademark search' aprobada en dev.euipo.europa.eu.")
        if r.status_code == 429:
            raise RegistroError("La EUIPO limitó las consultas por un rato (429).")
        if r.status_code == 400:
            log.warning("EUIPO 400 para %r: %s", query, r.text[:300])
            return []
        if not r.ok:
            raise RegistroError(f"La EUIPO respondió {r.status_code}.")
        try:
            return list(r.json().get("trademarks") or [])
        except ValueError as exc:
            raise RegistroError("La EUIPO devolvió una respuesta que no es JSON.") from exc
    return []


def probar(valor: str | None = None) -> str:
    """Pide un token y hace una búsqueda chica. Devuelve un texto para la UI."""
    cid, sec = credenciales(valor)
    if not (cid and sec):
        raise RegistroError("Faltan el Client ID o el Client Secret.")
    _token_para(cid, sec, forzar=True)
    res = _buscar('wordMarkSpecification.verbalElement==*ZARA*', cid, sec, size=10)
    return f"EUIPO conectada ({len(res)} resultados de prueba)"


# --------------------------------------------------------------------------
# Parecido entre nombres
# --------------------------------------------------------------------------
_FORMAS = re.compile(r"\b(s\s?l\s?u?|s\s?a\s?u?|s\s?l\s?l|c\s?b|s\s?coop|ltd|gmbh|srl|sas|sarl|bv|nv|ag)\b")
_VACIAS = {"el", "la", "los", "las", "de", "del", "y", "e", "the", "and", "of", "le", "les", "il", "lo"}


def normal(texto: str | None) -> str:
    s = unicodedata.normalize("NFD", (texto or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = _FORMAS.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def compacto(texto: str | None) -> str:
    return "".join(p for p in normal(texto).split() if p not in _VACIAS)


def fonetico(texto: str | None) -> str:
    """Cómo suena, en castellano y simplificado: "Cali" == "Kaly", "Zeta" == "Seta"."""
    s = compacto(texto)
    for a, b in (("ph", "f"), ("ch", "X"), ("qu", "k"), ("ll", "y"), ("sh", "X"), ("w", "u"),
                 ("ce", "se"), ("ci", "si"), ("ge", "je"), ("gi", "ji"), ("c", "k"), ("q", "k"),
                 ("z", "s"), ("v", "b"), ("h", ""), ("x", "ks")):
        s = s.replace(a, b)
    s = s.replace("y", "i")
    return re.sub(r"(.)\1+", r"\1", s)


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previo = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(previo[j] + 1, actual[j - 1] + 1, previo[j - 1] + (ca != cb)))
        previo = actual
    return previo[-1]


def parecido(a: str, b: str) -> tuple[float, str]:
    """(0..1, tipo) entre dos denominaciones."""
    ca, cb = compacto(a), compacto(b)
    if not ca or not cb:
        return 0.0, ""
    if ca == cb:
        return 1.0, "idéntica"
    if fonetico(a) == fonetico(b):
        return 0.95, "suena igual"
    ratio = 1 - _levenshtein(ca, cb) / max(len(ca), len(cb))
    if ratio >= 0.8:
        return ratio, "muy parecida"
    corto, largo = sorted((ca, cb), key=len)
    if len(corto) >= 4 and corto in largo:
        return max(ratio, 0.7), "la contiene"
    if ratio >= 0.65:
        return ratio, "parecida"
    return ratio, ""


# --------------------------------------------------------------------------
# Clases de Niza probables según el rubro del negocio
# --------------------------------------------------------------------------
_CLASES = [
    (("discoteca", "club", "copas", "pub", "bar", "cerveceria", "coctel", "chiringuito"), {41, 43}),
    (("concierto", "promotora", "festival", "evento", "artista", "booking", "dj", "sala"), {41}),
    (("discograf", "sello", "grabacion", "productora musical", "estudio de mezcla", "musica"), {9, 41}),
    (("instrumento", "luthier"), {15, 35}),
    (("restaurante", "asador", "pizzeria", "hamburgues", "sushi", "tapas", "comida", "cocina",
      "delivery", "domicilio", "catering", "cafeteria", "cafe", "mercado", "food", "hotel", "hostal"), {43}),
    (("panaderia", "pasteleria", "obrador", "horno", "chocolat", "heladeria", "churreria"), {30, 43}),
    (("bodega", "vino", "vinoteca"), {33}),
    (("cerveza",), {32}),
    (("carniceria", "charcuteria", "embutido", "pescaderia", "aceite", "conserva", "miel"), {29, 30, 35}),
    (("ropa", "moda", "boutique", "zapateria", "lenceria", "uniforme"), {25, 35}),
    (("joyeria", "bisuteria", "relojeria"), {14}),
    (("estetica", "peluqueria", "barberia", "spa", "unas", "depilacion"), {44, 3}),
    (("cosmetica", "perfumeria"), {3}),
    (("gimnasio", "crossfit", "pilates", "yoga", "padel", "fitness"), {41}),
    (("veterinaria", "mascota", "canina"), {44, 31}),
    (("dental", "fisioterapia", "clinica", "medico", "psicolog", "nutricion"), {44}),
    (("inmobiliaria",), {36}),
    (("asesoria", "gestoria", "consultora"), {35}),
    (("software", "informatica", "web", "marketing", "publicidad", "diseno"), {42, 35}),
    (("construccion", "reforma", "constructora", "aluminio", "cerrajeria"), {37}),
    (("taller", "neumatico", "recambio", "concesionario", "moto", "lavadero"), {37, 12}),
    (("mueble", "colchon", "decoracion", "interiorismo"), {20, 35}),
    (("floristeria", "vivero"), {31, 44}),
    (("academia", "escuela", "formacion", "idiomas"), {41}),
    (("optica",), {9, 44}),
]


def clases_para(rubro: str | None) -> set[int]:
    r = normal(rubro)
    salida: set[int] = set()
    for claves_rubro, clases in _CLASES:
        if any(k in r for k in claves_rubro):
            salida |= clases
    return salida


# --------------------------------------------------------------------------
# Revisión de un negocio
# --------------------------------------------------------------------------
def _termino(texto: str) -> str:
    """Un término seguro para meter en RSQL (sin comillas ni operadores)."""
    return re.sub(r'[*"\'(),;=!<>~]', " ", texto).strip()


def consultas_para(marca: str) -> list[str]:
    palabras = [p for p in re.split(r"\s+", _termino(marca)) if p]
    utiles = [p for p in palabras if normal(p) not in _VACIAS and len(normal(p)) >= 3]
    salida = []
    if len(palabras) > 1:
        salida.append(f'wordMarkSpecification.verbalElement=="*{" ".join(palabras)}*"')
    if utiles:
        larga = max(utiles, key=len)
        salida.append(f"wordMarkSpecification.verbalElement==*{larga}*")
    return salida[:2]


def _resumen_marca(tm: dict[str, Any]) -> dict[str, Any]:
    numero = str(tm.get("applicationNumber") or "")
    titulares = [a.get("name") for a in (tm.get("applicants") or []) if isinstance(a, dict) and a.get("name")]
    return {
        "numero": numero,
        "denominacion": ((tm.get("wordMarkSpecification") or {}).get("verbalElement") or "").strip(),
        "estado": tm.get("status") or "",
        "estado_es": ESTADO_ES.get(tm.get("status") or "", (tm.get("status") or "").lower()),
        "clases": sorted(int(c) for c in (tm.get("niceClasses") or []) if str(c).isdigit()),
        "titular": ", ".join(titulares)[:200],
        "fecha": tm.get("applicationDate") or "",
        "tipo": tm.get("markFeature") or "",
        "url": f"https://euipo.europa.eu/eSearch/#details/trademarks/{quote(numero)}" if numero else "",
    }


def evaluar(marca: str, rubro: str, encontradas: list[dict[str, Any]]) -> dict[str, Any]:
    """Semáforo + coincidencias ordenadas (lo mas riesgoso primero)."""
    clases_lead = clases_para(rubro)
    vistas, coincidencias = set(), []
    for tm in encontradas:
        m = _resumen_marca(tm)
        if not m["denominacion"] or m["numero"] in vistas or m["estado"] in INACTIVAS:
            continue
        vistas.add(m["numero"])
        score, tipo = parecido(marca, m["denominacion"])
        if not tipo:
            continue
        cruce = sorted(clases_lead & set(m["clases"])) if clases_lead else []
        fuerte = tipo in ("idéntica", "suena igual", "muy parecida")
        if fuerte and (cruce or not clases_lead):
            nivel = "rojo"
        elif fuerte or cruce:
            nivel = "amarillo"
        else:
            nivel = "info"
        coincidencias.append({**m, "parecido": round(score, 2), "como": tipo,
                              "clases_en_comun": cruce, "nivel": nivel})
    orden = {"rojo": 0, "amarillo": 1, "info": 2}
    coincidencias.sort(key=lambda c: (orden[c["nivel"]], -c["parecido"]))
    niveles = {c["nivel"] for c in coincidencias}
    nivel = "rojo" if "rojo" in niveles else "amarillo" if "amarillo" in niveles else "verde"
    return {"nivel": nivel, "clases_negocio": sorted(clases_lead),
            "coincidencias": coincidencias[:12], "total": len(coincidencias)}


def revisar(marca: str, rubro: str) -> dict[str, Any]:
    cid, sec = credenciales()
    if not (cid and sec):
        raise RegistroError("Faltan las credenciales de la EUIPO: cárgalas en Ajustes → Conexiones.")
    encontradas: list[dict[str, Any]] = []
    for q in consultas_para(marca):
        encontradas.extend(_buscar(q, cid, sec))
    return evaluar(marca, rubro, encontradas)


def enlaces_manuales(marca: str) -> dict[str, str]:
    """Para revisar a mano lo que la API todavia no cubre (OEPM, otras oficinas)."""
    return {
        "oepm": "https://consultas2.oepm.es/LocalizadorWeb/",
        "tmview": "https://www.tmdn.org/tmview/#/tmview/results?page=1&pageSize=30&criteria=C&basicSearch="
                  + quote(marca or ""),
    }


# --------------------------------------------------------------------------
# Guardado y trabajo de fondo
# --------------------------------------------------------------------------
MAX_INTENTOS = 3


def guardar(conn, lead_id: str, marca: str, resultado: dict[str, Any] | None,
            error: str | None = None) -> dict[str, Any]:
    from sqlalchemy import select, update
    from . import db
    previo = conn.execute(select(db.registros).where(db.registros.c.lead_id == lead_id)).first()
    intentos = ((previo.intentos or 0) + 1) if (previo and error and previo.marca == marca) else (1 if error else 0)
    fila = {"marca": marca[:200], "nivel": resultado["nivel"] if resultado else None,
            "resultado": db.a_json(resultado) if resultado else None, "error": error,
            "intentos": intentos, "revisado": db.ahora_iso()}
    if not db.insertar_si_no_existe(conn, db.registros, {"lead_id": lead_id, **fila}):
        conn.execute(update(db.registros).where(db.registros.c.lead_id == lead_id).values(**fila))
    return {"lead_id": lead_id, **fila, "resultado": resultado}


def leer(conn, lead_ids: list[str]) -> dict[str, dict[str, Any]]:
    from sqlalchemy import select
    from . import db
    if not lead_ids:
        return {}
    salida = {}
    for f in conn.execute(select(db.registros).where(db.registros.c.lead_id.in_(lead_ids))):
        d = dict(f._mapping)
        try:
            d["resultado"] = json.loads(d["resultado"]) if d["resultado"] else None
        except ValueError:
            d["resultado"] = None
        salida[d["lead_id"]] = d
    return salida
