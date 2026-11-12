"""
Extraccion de la marca de un negocio a partir de su titulo en Google Maps.

(El archivo se sigue llamando plantillas.py por historia: en la version
argentina tambien armaba el texto del primer mensaje. En la version espanola
la app NO redacta ni envia mensajes, ver crm.py.)
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from .identidad import GENERICOS

# --------------------------------------------------------------------------
# Marca
# --------------------------------------------------------------------------
_FORMA_JURIDICA = re.compile(
    r"[,\s]+(s\.?\s?l\.?\s?[lu]?\.?|s\.?\s?a\.?\s?u?\.?|s\.?\s?coop\.?|c\.?\s?b\.?)\s*$",
    re.IGNORECASE)
_COLGANTES = {"y", "e", "de", "del", "en", "para", "by", "&", "-", "|"}
_SEP_FUERTE = r"[|/·—–]"
_RUBRO_EXTRA = {"ropa", "calzado", "accesorios", "deportiva", "deportivo",
                "urbana", "urbano", "streetwear", "oversize", "shop", "store",
                "moda", "outlet", "mayorista", "minorista",
                "entrenamiento", "musculacion",
                "femenina", "femenino", "masculina", "masculino", "infantil",
                "infantiles", "ninos", "ninas", "bebes", "resto", "brunch"}
# Palabras que SOLAS no son una marca ("Verde Vivero" -> "Verde" estaria mal).
_NO_AUTONOMOS = {
    "verde", "verdes", "rojo", "roja", "rojos", "rojas", "azul", "azules",
    "amarillo", "amarilla", "negro", "negra", "blanco", "blanca", "gris",
    "violeta", "naranja", "celeste", "dorado", "dorada", "plateado",
    "plateada", "colorado", "colorada",
    "tostado", "tostada", "fresco", "fresca", "rico", "rica", "sabroso",
    "sabrosa", "natural", "naturales", "integral", "criollo", "criolla",
    "nuevo", "nueva", "viejo", "vieja", "grande", "chico", "chica",
    "moderno", "moderna", "clasico", "clasica", "lindo", "linda",
    "bueno", "buena", "sano", "sana", "puro", "pura", "feliz",
    "municipal", "vital", "total",
    "sport", "sports", "power", "full", "mega", "super", "center",
    "centre", "extreme", "max", "plus", "fit",
    "coffee", "espresso", "central", "co", "look",
}


def _normal(token: str) -> str:
    t = unicodedata.normalize("NFD", (token or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", t)


def marca_heuristica(negocio: str, ciudad: str = "") -> str:
    """
    La marca a partir del titulo de Maps, sin IA.

    "Panaderia y confiteria. Dulce Sabor 2" -> "Dulce Sabor". Saca rubro,
    conectores colgantes, ciudad y numero de sucursal. Conserva articulos.
    """
    nombre = (negocio or "").strip()
    if not nombre:
        return ""
    # Forma juridica al final: "La Espiga, S.L." -> "La Espiga"
    nombre = _FORMA_JURIDICA.sub("", nombre).strip() or nombre
    primera = re.split(_SEP_FUERTE, nombre)[0].strip() or nombre
    plano = re.sub(r"[-.,;:]+", " ", primera)

    descartables = GENERICOS | _RUBRO_EXTRA
    toks = [t for t in re.split(r"\s+", plano) if t]
    fuera = {i for i, t in enumerate(toks) if not _normal(t) or _normal(t) in descartables}
    fuera |= {i for i, t in enumerate(toks)
              if _normal(t) in _COLGANTES and (i - 1 in fuera or i + 1 in fuera)}
    limpios = [t for i, t in enumerate(toks) if i not in fuera]

    ciu_toks = [_normal(t) for t in re.split(r"\s+", ciudad or "") if _normal(t)]

    def _sin_ciudad(lista: list[str]) -> list[str]:
        if ciu_toks and len(lista) > len(ciu_toks) and \
                [_normal(t) for t in lista[-len(ciu_toks):]] == ciu_toks:
            lista = lista[:-len(ciu_toks)]
        # "... en Tres Arroyos" deja el "en" colgando
        while lista and (_normal(lista[-1]) in _COLGANTES or _normal(lista[-1]).isdigit()):
            lista = lista[:-1]
        return lista

    limpios = _sin_ciudad(limpios)
    while limpios and _normal(limpios[0]) in _COLGANTES:
        limpios.pop(0)

    # Si al podar queda un adjetivo suelto, el rubro era parte del nombre.
    if len(limpios) == 1 and _normal(limpios[0]) in _NO_AUTONOMOS:
        entero = _sin_ciudad(list(toks))
        if len(entero) > 1:
            limpios = entero

    marca = " ".join(limpios).strip(" .,-|&")
    if not marca or (ciu_toks and _normal(marca) == "".join(ciu_toks)):
        return nombre
    return marca


def marca_valida(marca: str, negocio: str) -> bool:
    """
    ¿La marca que devolvio la IA sale REALMENTE del titulo?

    En la ficha la marca tiene que ser real: una inventada es peor que una
    mal podada. Cada palabra tiene que estar en el titulo.
    """
    marca = (marca or "").strip()
    if not marca or len(marca) > 80:
        return False
    titulo = {_normal(t) for t in re.split(r"[\s\-.,;:|/·—–]+", negocio or "")}
    palabras = [_normal(t) for t in re.split(r"\s+", marca) if _normal(t)]
    return bool(palabras) and all(t in titulo for t in palabras)


def marca_final(lead: dict[str, Any]) -> str:
    """La que se muestra: la corregida a mano, si no la de la IA
    (validada), si no la heuristica."""
    manual = (lead.get("marca_manual") or "").strip()
    if manual:
        return manual
    ia = (lead.get("marca_ia") or "").strip()
    if ia and marca_valida(ia, lead.get("negocio") or ""):
        return ia
    return marca_heuristica(lead.get("negocio") or "", lead.get("ciudad") or "") \
        or (lead.get("negocio") or "")
