"""
Identidad canonica de un negocio: normalizadores y claves anti-duplicados.

El mismo negocio aparece escrito distinto en cada fuente ("Panaderia La Espiga
S.R.L." en Maps es "@laespiga.ta" en Instagram). Cada lead tiene VARIAS claves y
basta que UNA coincida para saber que es el mismo:

    pid:<place_id>      id de la ficha de Google Maps inequivoca
    tel:<E164>          telefono normalizado          la mas fuerte
    ig:<usuario>        usuario de Instagram
    web:<dominio>       dominio raiz del sitio propio
    nom:<nombre>|<ciu>  nombre normalizado + ciudad   ultimo recurso
    nuc:<nucleo>|<ciu>  nucleo del nombre + ciudad    ultimo recurso

Regla de oro: es preferible NO generar una clave que generar una equivocada.
Una clave equivocada fusiona dos negocios distintos (se pierde un lead); una
clave faltante se compensa con las otras.

El formato de las claves es IDENTICO al del sistema anterior (leadgen-marcas):
asi las claves migradas desde Notion siguen matcheando.

Este modulo es puro (sin base de datos) para poder testearlo aislado.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# --------------------------------------------------------------------------
# Estados. El orden importa: al fusionar gana el de MAYOR rango, asi un lead ya
# contactado nunca "retrocede" a nuevo (eso causaria un doble contacto).
# --------------------------------------------------------------------------
ESTADOS = ("nuevo", "contactado", "respondio", "propuesta", "cliente",
           "no interesa", "no molestar")
_RANGO = {e: i for i, e in enumerate(ESTADOS)}

ETIQUETAS_ESTADO = {
    "nuevo": "Nuevo", "contactado": "Contactado", "respondio": "Respondió",
    "propuesta": "Propuesta", "cliente": "Cliente",
    "no interesa": "No interesa", "no molestar": "No molestar",
}

# Estados desde los que NO se puede volver a mandar un primer mensaje.
ESTADOS_BLOQUEADOS = frozenset(ESTADOS) - {"nuevo"}


def rango_estado(estado: str | None) -> int:
    return _RANGO.get((estado or "nuevo").strip().lower(), 0)


def estado_mayor(a: str | None, b: str | None) -> str:
    """El mas avanzado de dos estados (el que gana al fusionar)."""
    a = (a or "nuevo").strip().lower()
    b = (b or "nuevo").strip().lower()
    a = a if a in _RANGO else "nuevo"
    b = b if b in _RANGO else "nuevo"
    return a if _RANGO[a] >= _RANGO[b] else b


def bloquea_contacto(estado: str | None) -> bool:
    return (estado or "").strip().lower() in ESTADOS_BLOQUEADOS


# --------------------------------------------------------------------------
# Normalizadores
# --------------------------------------------------------------------------
def sin_acentos(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn")


# Sufijos societarios que no distinguen al negocio.
_SUFIJOS = (" sl", " s l", " slu", " s l u", " sll", " s l l", " sa", " s a", " sau",
            " s a u", " cb", " c b", " scoop", " s coop", " sociedad limitada",
            " sociedad anonima", " y cia", " e hijos", " hermanos", " hnos")


def normalizar_nombre(nombre: str | None) -> str:
    """Nombre comparable: sin acentos, minusculas, sin puntuacion ni sufijos."""
    s = sin_acentos((nombre or "").lower())
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    cambio = True
    while cambio:                            # puede haber mas de un sufijo
        cambio = False
        for suf in _SUFIJOS:
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                cambio = True
    return re.sub(r"\s+", " ", s).strip()


# Palabras que dicen el RUBRO, no cual negocio es. "Panaderia La Espiga" y
# "La Espiga" son el mismo; por eso se comparan tambien sin estas palabras.
# OJO: cambiar esta lista cambia las claves `nuc:` ya guardadas.
GENERICOS = frozenset({
    "panaderia", "panificadora", "confiteria", "pasteleria", "sandwicheria",
    "heladeria", "rotiseria", "forrajeria", "indumentaria", "propiedades",
    "pizzeria", "parrilla", "restaurante", "restaurant", "bar", "cafe",
    "cafeteria", "cerveceria", "carniceria", "verduleria", "fiambreria",
    "despensa", "almacen", "autoservicio", "supermercado", "super",
    "minimercado", "mercado", "mayorista", "distribuidora", "deposito",
    "ferreteria", "corralon", "bulonera", "pinturera", "vivero", "floreria",
    "farmacia", "perfumeria", "libreria", "papeleria", "bazar", "juguetria",
    "jugueteria", "zapateria", "muebleria", "boutique", "tienda", "local",
    "kiosco", "quiosco", "maxikiosco", "optica", "veterinaria", "peluqueria",
    "barberia", "gimnasio", "gym", "spa", "taller", "lubricentro", "gomeria",
    "cerrajeria", "tapiceria", "herreria", "carpinteria", "imprenta",
    "lavadero", "repuestos", "automotores", "concesionaria", "inmobiliaria",
    "estudio", "consultorio", "clinica", "sanatorio", "laboratorio",
    "hotel", "hostel", "cabanas", "casa", "grupo", "centro",
    # Espana
    "horno", "obrador", "charcuteria", "polleria", "fruteria", "herbolario",
    "drogueria", "floristeria",
    "churreria", "marisqueria", "arroceria",
    "gastrobar", "hostal", "pension", "asesoria",
    "gestoria", "autoescuela", "clinica", "dental", "fisioterapia", "estanco",
    "talleres", "neumaticos", "recambios", "concesionario", "estetica",
})
_ARTICULOS = {"la", "el", "los", "las", "de", "del", "y", "e", "a", "al",
              "lo", "un", "una", "unos", "unas"}


def nucleo_nombre(nombre: str | None) -> str:
    """
    Parte distintiva del nombre, sin rubro ni articulos iniciales.

        "Panaderia La Espiga S.R.L." -> "espiga"

    Devuelve "" si lo que queda es demasiado corto para identificar algo.
    """
    toks = [t for t in normalizar_nombre(nombre).split() if t not in GENERICOS]
    while toks and toks[0] in _ARTICULOS:
        toks.pop(0)
    nuc = " ".join(toks).strip()
    return nuc if len(nuc.replace(" ", "")) >= 4 else ""


# Rutas de instagram.com que no son un usuario.
_IG_RESERVADAS = {"p", "reel", "reels", "explore", "stories", "tv", "accounts",
                  "direct", "about", "developer", "legal", "web"}


def normalizar_usuario_ig(usuario: str | None) -> str:
    """Usuario de IG comparable: sin @, sin URL, en minusculas."""
    s = (usuario or "").strip().lower()
    if not s:
        return ""
    m = re.search(r"instagram\.com/([a-z0-9._]+)", s)
    if m:
        s = m.group(1)
    elif "/" in s.strip("/") or s.startswith("http"):
        return ""                            # una URL que no es de Instagram
    s = s.lstrip("@").strip("/")
    s = re.sub(r"[?#].*$", "", s)
    if s in _IG_RESERVADAS:
        return ""
    return s if re.fullmatch(r"[a-z0-9._]{1,30}", s) else ""


# Dominios que NO identifican a un negocio: redes, acortadores y -sobre todo-
# google.com, que es lo que Maps devuelve como "url" cuando no hay web propia.
# Sin esta lista TODOS los negocios sin sitio comparten "web:google.com".
_DOMINIOS_GENERICOS = (
    "instagram.com", "facebook.com", "fb.com", "fb.me", "m.me", "wa.me",
    "whatsapp.com", "linktr.ee", "linktree.com",
    "tiktok.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
    "linkedin.com", "google.com", "google.com.ar", "goo.gl", "g.page",
    "mercadolibre.com.ar", "pedidosya.com.ar", "rappi.com.ar", "bit.ly",
    "beacons.ai", "linkin.bio", "taplink.cc",
)
# Plataformas donde el dominio pelado es generico pero el SUBDOMINIO es de una
# tienda puntual (kaimasu.mitiendanube.com identifica a "kaimasu").
_PLATAFORMAS = (
    "tiendanube.com", "mitiendanube.com", "empretienda.com.ar", "mercadoshops.com.ar",
    "business.site", "wixsite.com", "square.site", "blogspot.com", "wordpress.com",
    "carrd.co", "negocio.site",
)


def normalizar_dominio(web: str | None) -> str:
    """Dominio raiz comparable: sin protocolo, sin www, sin path ni query."""
    s = (web or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^[a-z]+://", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0]
    s = re.sub(r"^www\d?\.", "", s)
    s = s.split(":")[0]
    if any(s == d or s.endswith("." + d) for d in _DOMINIOS_GENERICOS):
        return ""
    if s in _PLATAFORMAS:                    # el "www." ya se saco arriba
        return ""
    return s if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", s) else ""


def normalizar_telefono(raw: str | None, area_default: str = "") -> str:
    """
    Telefono espanol -> E.164 (+34NNNNNNNNN), o "" si no hay certeza.

    Maneja las formas reales: +34 612 34 56 78 / 0034 612345678 /
    91 123 45 67 / 612-34-56-78. En Espana el numero siempre tiene 9 digitos
    (el prefijo provincial va incluido), asi que `area_default` no se usa: se
    deja el parametro para no cambiar la firma.
    """
    if not raw:
        return ""
    d = re.sub(r"\D", "", str(raw))
    if not d:
        return ""
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("34") and len(d) == 11:
        d = d[2:]
    if len(d) != 9 or d[0] not in "6789":
        return ""
    # 900/800 gratuitos, 901/902 tarifa compartida, 80x/905 tarificacion
    # adicional: no son la linea de un negocio chico.
    if d[:3] in ("800", "803", "806", "807", "900", "901", "902", "905"):
        return ""
    return f"+34{d}"


def es_movil_probable(raw: str | None) -> bool | None:
    """
    ¿Es un movil? True / False / None (no se sabe).

    En Espana se sabe por el primer digito: 6 y 7 son moviles, 8 y 9 fijos.
    """
    e164 = normalizar_telefono(raw)
    if not e164:
        return None
    return e164[3] in "67"


def whatsapp_numero(e164: str | None) -> str:
    """Numero en formato internacional sin '+' (solo moviles), o ""."""
    d = re.sub(r"\D", "", e164 or "")
    if len(d) == 11 and d.startswith("34") and d[2] in "67":
        return d
    return ""


def normalizar_email(mail: str | None) -> str:
    s = (mail or "").strip().lower().rstrip(".")
    s = re.sub(r"^mailto:", "", s)
    if not re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", s):
        return ""
    # Direcciones de relleno que traen las plantillas web
    if re.search(r"@(example|sentry|wixpress|domain)\.", s) or \
            re.search(r"\.(png|jpe?g|gif|webp|svg)$", s):
        return ""
    return s


# --------------------------------------------------------------------------
# Claves canonicas
# --------------------------------------------------------------------------
def claves_de(cand: dict[str, Any], area_default: str = "") -> list[str]:
    """Todas las claves canonicas de un candidato, de fuerte a debil."""
    claves: list[str] = []

    # El id de lugar de Google: identifica sin ambiguedad una ficha de Maps.
    pid = re.sub(r"\s", "", str(cand.get("place_id") or ""))
    if pid:
        claves.append(f"pid:{pid}")

    tel = cand.get("telefono_e164") or normalizar_telefono(
        cand.get("telefono_raw") or cand.get("telefono") or "", area_default)
    if tel:
        claves.append(f"tel:{tel}")

    ig = normalizar_usuario_ig(cand.get("instagram") or "")
    if ig:
        claves.append(f"ig:{ig}")

    dom = normalizar_dominio(cand.get("web") or "")
    if dom:
        claves.append(f"web:{dom}")

    # El nombre solo sirve junto a la ciudad: "la esquina" hay en todos lados.
    ciu = normalizar_nombre(cand.get("ciudad") or "")
    if ciu:
        nom = normalizar_nombre(cand.get("negocio") or "")
        if nom and len(nom) >= 4:
            claves.append(f"nom:{nom}|{ciu}")
        # El nucleo, salvo que sea el nombre de la ciudad: "Panaderia Tres
        # Arroyos" arrastraria a cualquier negocio bautizado como el pueblo.
        nuc = nucleo_nombre(cand.get("negocio") or "")
        if nuc and nuc != nom and nuc != ciu and nuc not in ciu:
            claves.append(f"nuc:{nuc}|{ciu}")

    vistas: set[str] = set()
    return [c for c in claves if not (c in vistas or vistas.add(c))]


def clave_valida(clave: str) -> bool:
    """Filtra claves importadas: descarta las que hoy no se generarian."""
    if clave.startswith("pid:"):
        return len(clave) > 4 and " " not in clave
    if clave.startswith("web:"):
        return bool(normalizar_dominio(clave[4:]))
    if clave.startswith("tel:"):
        return bool(re.fullmatch(r"tel:\+34[6789]\d{8}", clave))
    if clave.startswith("ig:"):
        return bool(normalizar_usuario_ig(clave[3:]))
    if clave.startswith(("nom:", "nuc:")):
        nombre, _, ciu = clave[4:].partition("|")
        if not (nombre and ciu):
            return False
        if clave.startswith("nuc:") and (nombre == ciu or nombre in ciu):
            return False
        return True
    return False
