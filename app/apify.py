"""
Google Maps via Apify (actor compass/crawler-google-places).

Corrida ASINCRONA: se lanza, se guarda el run_id y se consulta hasta que
termina. Asi un reinicio del servidor no pierde una corrida ya pagada: al
arrancar se retoman las busquedas que quedaron 'corriendo'.

Lecciones del sistema anterior (medidas con datos reales):
  - La ubicacion va en `locationQuery` + `countryCode`. Metida en el texto
    buscado, el actor geolocaliza en Estados Unidos.
  - El campo `url` es la ficha de Maps, NO la web del negocio.
Y de la tarifa del actor: `skipClosedPlaces` cobra un extra por lugar, asi que
los cerrados se descartan aca con `permanentlyClosed`, gratis.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

import requests

from .identidad import normalizar_email, normalizar_usuario_ig, sin_acentos

API = "https://api.apify.com/v2"
ACTOR_MAPS = os.environ.get("APIFY_ACTOR_MAPS", "compass~crawler-google-places")

# Tarifa publicada del actor (plan free, USD por lugar). Solo para el estimado
# previo; el costo REAL se lee de la corrida (usageTotalUsd).
PRECIO_LUGAR = 0.004
PRECIO_CONTACTOS = 0.002

ESTADOS_FINALES = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}


class ApifyError(RuntimeError):
    pass


# Plazas para barridos. Maps necesita una ubicacion concreta, por eso
# "todo el pais" = una corrida por ciudad. (ciudad, provincia)
#
# Criterio de la lista: localidades con centro comercial propio. Se evitan a
# proposito dos cosas que hacen pagar dos veces lo mismo:
#   - nombres repetidos entre provincias (Mercedes, Ituzaingo, Rawson...):
#     queda solo la plaza mas grande, porque `provincia_de` resuelve por nombre
#     y el primero gana;
#   - localidades que ya figuran como zona de otra ciudad en historial.ZONAS
#     (Yerba Buena, Godoy Cruz, Chacras de Coria...): esas se buscan como zona.
CIUDADES: list[tuple[str, str]] = [
    # Comunidad de Madrid
    ("Madrid", "Madrid"), ("Móstoles", "Madrid"), ("Alcalá de Henares", "Madrid"),
    ("Fuenlabrada", "Madrid"), ("Leganés", "Madrid"), ("Getafe", "Madrid"),
    ("Alcorcón", "Madrid"), ("Torrejón de Ardoz", "Madrid"), ("Parla", "Madrid"),
    ("Alcobendas", "Madrid"), ("Las Rozas de Madrid", "Madrid"),
    ("San Sebastián de los Reyes", "Madrid"), ("Pozuelo de Alarcón", "Madrid"),
    ("Rivas-Vaciamadrid", "Madrid"), ("Coslada", "Madrid"), ("Valdemoro", "Madrid"),
    ("Majadahonda", "Madrid"), ("Collado Villalba", "Madrid"), ("Aranjuez", "Madrid"),
    ("Arganda del Rey", "Madrid"), ("Boadilla del Monte", "Madrid"), ("Pinto", "Madrid"),
    ("Colmenar Viejo", "Madrid"), ("Tres Cantos", "Madrid"),

    # Cataluña
    ("Barcelona", "Barcelona"), ("L'Hospitalet de Llobregat", "Barcelona"),
    ("Badalona", "Barcelona"), ("Terrassa", "Barcelona"), ("Sabadell", "Barcelona"),
    ("Mataró", "Barcelona"), ("Santa Coloma de Gramenet", "Barcelona"),
    ("Cornellà de Llobregat", "Barcelona"), ("Sant Boi de Llobregat", "Barcelona"),
    ("Sant Cugat del Vallès", "Barcelona"), ("Rubí", "Barcelona"), ("Manresa", "Barcelona"),
    ("Vilanova i la Geltrú", "Barcelona"), ("Viladecans", "Barcelona"),
    ("Castelldefels", "Barcelona"), ("El Prat de Llobregat", "Barcelona"),
    ("Granollers", "Barcelona"), ("Cerdanyola del Vallès", "Barcelona"),
    ("Mollet del Vallès", "Barcelona"), ("Vic", "Barcelona"), ("Igualada", "Barcelona"),
    ("Sitges", "Barcelona"),
    ("Girona", "Girona"), ("Figueres", "Girona"), ("Blanes", "Girona"),
    ("Lloret de Mar", "Girona"), ("Olot", "Girona"),
    ("Lleida", "Lleida"), ("Balaguer", "Lleida"), ("Tàrrega", "Lleida"),
    ("Tarragona", "Tarragona"), ("Reus", "Tarragona"), ("Tortosa", "Tarragona"),
    ("Cambrils", "Tarragona"), ("Salou", "Tarragona"), ("El Vendrell", "Tarragona"),
    ("Valls", "Tarragona"),

    # Comunidad Valenciana
    ("Valencia", "Valencia"), ("Torrent", "Valencia"), ("Gandia", "Valencia"),
    ("Paterna", "Valencia"), ("Sagunto", "Valencia"), ("Burjassot", "Valencia"),
    ("Mislata", "Valencia"), ("Alzira", "Valencia"), ("Ontinyent", "Valencia"),
    ("Xàtiva", "Valencia"),
    ("Alicante", "Alicante"), ("Elche", "Alicante"), ("Torrevieja", "Alicante"),
    ("Orihuela", "Alicante"), ("Benidorm", "Alicante"), ("Alcoy", "Alicante"),
    ("San Vicente del Raspeig", "Alicante"), ("Elda", "Alicante"), ("Dénia", "Alicante"),
    ("Villena", "Alicante"), ("Jávea", "Alicante"), ("Santa Pola", "Alicante"),
    ("Calpe", "Alicante"),
    ("Castellón de la Plana", "Castellón"), ("Vila-real", "Castellón"),
    ("Burriana", "Castellón"), ("Vinaròs", "Castellón"), ("Benicarló", "Castellón"),

    # Andalucía
    ("Sevilla", "Sevilla"), ("Dos Hermanas", "Sevilla"), ("Alcalá de Guadaíra", "Sevilla"),
    ("Utrera", "Sevilla"), ("Mairena del Aljarafe", "Sevilla"), ("Écija", "Sevilla"),
    ("Carmona", "Sevilla"),
    ("Málaga", "Málaga"), ("Marbella", "Málaga"), ("Mijas", "Málaga"),
    ("Vélez-Málaga", "Málaga"), ("Fuengirola", "Málaga"), ("Torremolinos", "Málaga"),
    ("Benalmádena", "Málaga"), ("Estepona", "Málaga"), ("Rincón de la Victoria", "Málaga"),
    ("Antequera", "Málaga"), ("Ronda", "Málaga"), ("Nerja", "Málaga"),
    ("Cádiz", "Cádiz"), ("Jerez de la Frontera", "Cádiz"), ("Algeciras", "Cádiz"),
    ("San Fernando", "Cádiz"), ("El Puerto de Santa María", "Cádiz"),
    ("Chiclana de la Frontera", "Cádiz"), ("Sanlúcar de Barrameda", "Cádiz"),
    ("La Línea de la Concepción", "Cádiz"), ("Puerto Real", "Cádiz"), ("Rota", "Cádiz"),
    ("Arcos de la Frontera", "Cádiz"),
    ("Córdoba", "Córdoba"), ("Lucena", "Córdoba"), ("Puente Genil", "Córdoba"),
    ("Montilla", "Córdoba"), ("Priego de Córdoba", "Córdoba"),
    ("Granada", "Granada"), ("Motril", "Granada"), ("Almuñécar", "Granada"),
    ("Baza", "Granada"), ("Loja", "Granada"), ("Armilla", "Granada"),
    ("Jaén", "Jaén"), ("Linares", "Jaén"), ("Andújar", "Jaén"), ("Úbeda", "Jaén"),
    ("Martos", "Jaén"), ("Baeza", "Jaén"),
    ("Almería", "Almería"), ("Roquetas de Mar", "Almería"), ("El Ejido", "Almería"),
    ("Níjar", "Almería"), ("Vícar", "Almería"), ("Huércal-Overa", "Almería"),
    ("Huelva", "Huelva"), ("Lepe", "Huelva"), ("Almonte", "Huelva"),
    ("Isla Cristina", "Huelva"), ("Ayamonte", "Huelva"), ("Moguer", "Huelva"),

    # Región de Murcia
    ("Murcia", "Murcia"), ("Cartagena", "Murcia"), ("Lorca", "Murcia"),
    ("Molina de Segura", "Murcia"), ("Alcantarilla", "Murcia"), ("Mazarrón", "Murcia"),
    ("Águilas", "Murcia"), ("Yecla", "Murcia"), ("Caravaca de la Cruz", "Murcia"),
    ("San Javier", "Murcia"),

    # Aragón
    ("Zaragoza", "Zaragoza"), ("Calatayud", "Zaragoza"), ("Utebo", "Zaragoza"),
    ("Ejea de los Caballeros", "Zaragoza"),
    ("Huesca", "Huesca"), ("Monzón", "Huesca"), ("Barbastro", "Huesca"), ("Jaca", "Huesca"),
    ("Teruel", "Teruel"), ("Alcañiz", "Teruel"),

    # País Vasco y Navarra
    ("Bilbao", "Bizkaia"), ("Barakaldo", "Bizkaia"), ("Getxo", "Bizkaia"),
    ("Portugalete", "Bizkaia"), ("Santurtzi", "Bizkaia"), ("Basauri", "Bizkaia"),
    ("Durango", "Bizkaia"),
    ("San Sebastián", "Gipuzkoa"), ("Irun", "Gipuzkoa"), ("Errenteria", "Gipuzkoa"),
    ("Eibar", "Gipuzkoa"), ("Zarautz", "Gipuzkoa"),
    ("Vitoria-Gasteiz", "Álava"), ("Llodio", "Álava"),
    ("Pamplona", "Navarra"), ("Tudela", "Navarra"), ("Barañáin", "Navarra"),
    ("Estella", "Navarra"),

    # La Rioja, Cantabria y Asturias
    ("Logroño", "La Rioja"), ("Calahorra", "La Rioja"), ("Arnedo", "La Rioja"),
    ("Haro", "La Rioja"),
    ("Santander", "Cantabria"), ("Torrelavega", "Cantabria"),
    ("Castro Urdiales", "Cantabria"), ("Laredo", "Cantabria"),
    ("Oviedo", "Asturias"), ("Gijón", "Asturias"), ("Avilés", "Asturias"),
    ("Siero", "Asturias"), ("Langreo", "Asturias"), ("Mieres", "Asturias"),

    # Galicia
    ("A Coruña", "A Coruña"), ("Santiago de Compostela", "A Coruña"), ("Ferrol", "A Coruña"),
    ("Narón", "A Coruña"), ("Oleiros", "A Coruña"), ("Arteixo", "A Coruña"),
    ("Carballo", "A Coruña"),
    ("Vigo", "Pontevedra"), ("Pontevedra", "Pontevedra"),
    ("Vilagarcía de Arousa", "Pontevedra"), ("Redondela", "Pontevedra"),
    ("Cangas", "Pontevedra"),
    ("Lugo", "Lugo"), ("Monforte de Lemos", "Lugo"), ("Viveiro", "Lugo"),
    ("Ourense", "Ourense"), ("Verín", "Ourense"),

    # Castilla y León
    ("León", "León"), ("Ponferrada", "León"), ("San Andrés del Rabanedo", "León"),
    ("Astorga", "León"),
    ("Valladolid", "Valladolid"), ("Medina del Campo", "Valladolid"),
    ("Laguna de Duero", "Valladolid"),
    ("Burgos", "Burgos"), ("Miranda de Ebro", "Burgos"), ("Aranda de Duero", "Burgos"),
    ("Salamanca", "Salamanca"), ("Béjar", "Salamanca"), ("Ciudad Rodrigo", "Salamanca"),
    ("Palencia", "Palencia"), ("Aguilar de Campoo", "Palencia"),
    ("Zamora", "Zamora"), ("Benavente", "Zamora"),
    ("Segovia", "Segovia"), ("Cuéllar", "Segovia"),
    ("Ávila", "Ávila"), ("Arévalo", "Ávila"),
    ("Soria", "Soria"), ("Almazán", "Soria"),

    # Castilla-La Mancha
    ("Toledo", "Toledo"), ("Talavera de la Reina", "Toledo"), ("Illescas", "Toledo"),
    ("Seseña", "Toledo"),
    ("Ciudad Real", "Ciudad Real"), ("Puertollano", "Ciudad Real"),
    ("Tomelloso", "Ciudad Real"), ("Alcázar de San Juan", "Ciudad Real"),
    ("Valdepeñas", "Ciudad Real"),
    ("Albacete", "Albacete"), ("Hellín", "Albacete"), ("Almansa", "Albacete"),
    ("Villarrobledo", "Albacete"),
    ("Cuenca", "Cuenca"), ("Tarancón", "Cuenca"),
    ("Guadalajara", "Guadalajara"), ("Azuqueca de Henares", "Guadalajara"),

    # Extremadura
    ("Badajoz", "Badajoz"), ("Mérida", "Badajoz"), ("Don Benito", "Badajoz"),
    ("Almendralejo", "Badajoz"), ("Villanueva de la Serena", "Badajoz"), ("Zafra", "Badajoz"),
    ("Cáceres", "Cáceres"), ("Plasencia", "Cáceres"), ("Navalmoral de la Mata", "Cáceres"),

    # Islas Baleares
    ("Palma", "Baleares"), ("Ibiza", "Baleares"), ("Manacor", "Baleares"),
    ("Llucmajor", "Baleares"), ("Mahón", "Baleares"), ("Ciutadella", "Baleares"),
    ("Inca", "Baleares"), ("Santa Eulària des Riu", "Baleares"),

    # Canarias
    ("Las Palmas de Gran Canaria", "Las Palmas"), ("Telde", "Las Palmas"),
    ("Santa Lucía de Tirajana", "Las Palmas"), ("Arrecife", "Las Palmas"),
    ("San Bartolomé de Tirajana", "Las Palmas"), ("Puerto del Rosario", "Las Palmas"),
    ("Arucas", "Las Palmas"),
    ("Santa Cruz de Tenerife", "Santa Cruz de Tenerife"),
    ("San Cristóbal de La Laguna", "Santa Cruz de Tenerife"),
    ("Arona", "Santa Cruz de Tenerife"), ("Adeje", "Santa Cruz de Tenerife"),
    ("Granadilla de Abona", "Santa Cruz de Tenerife"),
    ("La Orotava", "Santa Cruz de Tenerife"), ("Puerto de la Cruz", "Santa Cruz de Tenerife"),
    ("Los Llanos de Aridane", "Santa Cruz de Tenerife"),
    ("Santa Cruz de La Palma", "Santa Cruz de Tenerife"),

    # Ciudades autónomas
    ("Ceuta", "Ceuta"), ("Melilla", "Melilla"),
]

# Las plazas grandes: el boton "Principales" del barrido marca estas y nada mas
# (todas las capitales de provincia y algunas ciudades grandes que no lo son).
# Tiene que quedar por debajo del tope de ciudades por barrido (ver __init__.py).
PRINCIPALES: frozenset[str] = frozenset({
    "Madrid", "Barcelona", "Girona", "Lleida", "Tarragona", "Valencia", "Alicante",
    "Castellón de la Plana", "Sevilla", "Málaga", "Cádiz", "Córdoba", "Granada", "Jaén",
    "Almería", "Huelva", "Murcia", "Zaragoza", "Huesca", "Teruel", "Bilbao",
    "San Sebastián", "Vitoria-Gasteiz", "Pamplona", "Logroño", "Santander", "Oviedo",
    "A Coruña", "Pontevedra", "Lugo", "Ourense", "León", "Valladolid", "Burgos",
    "Salamanca", "Palencia", "Zamora", "Segovia", "Ávila", "Soria", "Toledo",
    "Ciudad Real", "Albacete", "Cuenca", "Guadalajara", "Badajoz", "Cáceres", "Palma",
    "Las Palmas de Gran Canaria", "Santa Cruz de Tenerife", "Ceuta", "Melilla",
    # grandes que no son capital
    "Vigo", "Gijón", "Elche", "Jerez de la Frontera", "Marbella", "Cartagena",
})


def provincia_de(ciudad: str) -> str:
    objetivo = sin_acentos((ciudad or "").strip().lower())
    for nombre, provincia in CIUDADES:
        if sin_acentos(nombre.lower()) == objetivo:
            return provincia
    return ""


def estimar_costo(cantidad: int, con_contactos: bool) -> float:
    return round(cantidad * (PRECIO_LUGAR + (PRECIO_CONTACTOS if con_contactos else 0)), 2)


# --------------------------------------------------------------------------
# Cliente HTTP
# --------------------------------------------------------------------------
def _token() -> str:
    tok = os.environ.get("APIFY_TOKEN", "").strip()
    if not tok:
        raise ApifyError("Falta configurar APIFY_TOKEN.")
    return tok


def _pedir(metodo: str, ruta: str, **kw: Any) -> Any:
    # El token va en el header, no en la URL: asi no queda en ningun log.
    headers = {"Authorization": f"Bearer {_token()}"}
    try:
        r = requests.request(metodo, f"{API}{ruta}", headers=headers,
                             timeout=kw.pop("timeout", 60), **kw)
    except requests.RequestException as exc:
        raise ApifyError(f"No se pudo conectar con Apify: {exc}") from exc
    if r.status_code == 401:
        raise ApifyError("Apify rechazó el token (401). Revisa APIFY_TOKEN.")
    if r.status_code == 402:
        raise ApifyError("Sin créditos en Apify (402). Carga saldo o espera al próximo ciclo.")
    if r.status_code == 403:
        raise ApifyError("Apify denegó el acceso (403). Revisa los permisos del token.")
    if r.status_code == 404:
        raise ApifyError(f"Apify: no existe el recurso pedido ({ruta.split('?')[0]}).")
    if r.status_code == 429:
        raise ApifyError("Apify: demasiadas solicitudes (429). Prueba en un minuto.")
    if not r.ok:
        raise ApifyError(f"Apify devolvió {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except ValueError as exc:
        raise ApifyError("Apify devolvió una respuesta que no es JSON.") from exc


def entrada_maps(rubro: str, ciudad: str, provincia: str, limite: int,
                 con_contactos: bool, zona: str = "") -> dict[str, Any]:
    # La zona (barrio) solo afina la ubicacion; los leads quedan con la CIUDAD.
    ubicacion = ", ".join(p for p in (zona, ciudad, provincia, "España") if p)
    return {
        "searchStringsArray": [rubro.strip()],
        "locationQuery": ubicacion,
        "countryCode": "es",
        "language": "es",
        "maxCrawledPlacesPerSearch": max(1, min(int(limite), 300)),
        "scrapeContacts": bool(con_contactos),
        # Todo lo que no se usa, apagado (varios cobran extra)
        "skipClosedPlaces": False,
        "scrapePlaceDetailPage": False,
        "maxReviews": 0,
        "maxImages": 0,
        "maxQuestions": 0,
        "scrapeReviewsPersonalData": False,
    }


def lanzar(entrada: dict[str, Any]) -> dict[str, str]:
    """Lanza la corrida y vuelve enseguida con run_id y dataset_id."""
    datos = _pedir("POST", f"/acts/{ACTOR_MAPS}/runs", json=entrada)["data"]
    return {"run_id": datos["id"], "dataset_id": datos.get("defaultDatasetId") or ""}


def estado_corrida(run_id: str) -> dict[str, Any]:
    d = _pedir("GET", f"/actor-runs/{run_id}")["data"]
    return {"estado": d.get("status") or "", "dataset_id": d.get("defaultDatasetId") or "",
            "costo_usd": d.get("usageTotalUsd"),
            "mensaje": d.get("statusMessage") or ""}


def items_dataset(dataset_id: str, lote: int = 500) -> list[dict[str, Any]]:
    """Descarga el dataset completo, paginando."""
    salida: list[dict[str, Any]] = []
    offset = 0
    while True:
        pagina = _pedir("GET", f"/datasets/{dataset_id}/items",
                        params={"clean": "true", "format": "json",
                                "offset": offset, "limit": lote}, timeout=120)
        if not isinstance(pagina, list) or not pagina:
            break
        salida.extend(pagina)
        if len(pagina) < lote:
            break
        offset += lote
    return salida


def probar_token() -> str:
    """Nombre de usuario de la cuenta (para el boton 'probar' de Configuracion)."""
    d = _pedir("GET", "/users/me")["data"]
    return d.get("username") or d.get("id") or "ok"


# --------------------------------------------------------------------------
# Parseo tolerante de items
# --------------------------------------------------------------------------
def _primero(item: dict[str, Any], *claves: str) -> Any:
    for k in claves:
        v = item.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _lista(valor: Any) -> list[str]:
    if not valor:
        return []
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, Iterable):
        return [str(v) for v in valor if v]
    return []


def _instagram_de(item: dict[str, Any]) -> str:
    candidatos = _lista(item.get("instagrams")) + _lista(item.get("instagram"))
    for k, v in item.items():                  # por si el actor renombra el campo
        if "instagram" in k.lower() and k not in ("instagrams", "instagram"):
            candidatos += _lista(v) if not isinstance(v, dict) else []
    for web in _lista(item.get("website")):
        if "instagram.com" in web.lower():
            candidatos.append(web)
    for c in candidatos:
        u = normalizar_usuario_ig(c)
        if u:
            return u
    return ""


def _email_de(item: dict[str, Any]) -> str:
    for c in _lista(item.get("emails")) + _lista(item.get("email")):
        e = normalizar_email(c)
        if e:
            return e
    return ""


def _ciudad_de(item: dict[str, Any], ciudad_busqueda: str) -> str:
    """
    La ciudad del negocio: la de la BUSQUEDA. La ciudad forma parte de las
    claves por nombre, y el actor a veces devuelve el barrio o una localidad
    vecina: eso partiria un mismo negocio en dos leads.
    """
    return ciudad_busqueda or str(_primero(item, "city") or "")


def normalizar_item(item: dict[str, Any], ciudad: str, provincia: str) -> dict[str, Any] | None:
    """Item crudo de Maps -> candidato. None si no sirve (sin nombre o cerrado)."""
    nombre = _primero(item, "title", "name")
    if not nombre or not isinstance(nombre, str):
        return None
    if item.get("permanentlyClosed") or item.get("temporarilyClosed"):
        return None
    web = _primero(item, "website")
    web = web if isinstance(web, str) else ""
    rubro = _primero(item, "categoryName")
    if not rubro:
        cats = _lista(item.get("categories"))
        rubro = cats[0] if cats else ""
    telefono = _primero(item, "phone", "phoneUnformatted")
    return {
        "negocio": re.sub(r"\s+", " ", nombre).strip()[:300],
        "rubro": str(rubro or "")[:200],
        "ciudad": _ciudad_de(item, ciudad),
        "provincia": provincia or _primero(item, "state") or "",
        "direccion": str(_primero(item, "address") or "")[:300],
        "telefono_raw": str(telefono or "")[:60],
        "email": _email_de(item),
        "instagram": _instagram_de(item),
        # "url" es la ficha de Maps, NUNCA la web del negocio
        "web": "" if "instagram.com" in web.lower() else web[:300],
        "maps_url": _primero(item, "url") or "",
        "place_id": str(_primero(item, "placeId") or "")[:120],
        "rating": item.get("totalScore") if isinstance(item.get("totalScore"), (int, float)) else None,
        "resenas": item.get("reviewsCount") if isinstance(item.get("reviewsCount"), int) else None,
    }
