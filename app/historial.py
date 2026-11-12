"""
Historial de busquedas y analisis previo: para no pagar dos veces lo mismo.

Google Maps devuelve casi siempre los MISMOS lugares, en el mismo orden, para
la misma busqueda (rubro + ubicacion). Repetirla con la misma cantidad trae
negocios que ya estan: no se duplican en el padron, pero Apify los cobra igual.

Para sacar negocios NUEVOS de una ciudad ya buscada:
  - variantes del rubro ("boutique" en vez de "indumentaria"),
  - una zona/barrio dentro de la ciudad,
  - o una cantidad mayor que la de la busqueda anterior.

Este modulo agrupa el historial por (rubro, ciudad, zona) normalizados,
calcula cuanto rindio cada combinacion y sugiere alternativas.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from .db import busquedas, leads
from .identidad import sin_acentos

ESTADOS_OK = ("importada",)
ESTADOS_EN_CURSO = ("pendiente", "corriendo", "importando")


def normalizar_termino(texto: str | None) -> str:
    """'Panaderías' == 'panaderia' == 'PANADERIAS '. Singular simple, sin acentos."""
    s = sin_acentos((texto or "").lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    palabras = []
    for p in s.split():
        if len(p) > 4 and p.endswith("es") and p[-3] in "rlndzj":   # "mueblerias" no, "locales" -> "local"
            p = p[:-2]
        elif len(p) > 3 and p.endswith("s"):
            p = p[:-1]
        palabras.append(p)
    return " ".join(palabras)


def clave(rubro: str | None, ciudad: str | None, zona: str | None = None) -> tuple[str, str, str]:
    return (normalizar_termino(rubro), normalizar_termino(ciudad), normalizar_termino(zona))


# --------------------------------------------------------------------------
# Sugerencias
# --------------------------------------------------------------------------
# Catalogo de rubros para los chips del buscador, agrupado por familia.
# "Frecuentes" es lo que se ve al abrir; las demas familias se despliegan.
# Criterio: rubros que registran marca (nombre propio, logo, local o producto).
RUBROS: dict[str, list[str]] = {
    "Frecuentes": [
        "discotecas", "salas de conciertos", "promotoras de eventos", "estudios de grabación",
        "restaurantes", "puestos de mercado gastronómico", "cocinas solo delivery",
        "cervecerías", "cafeterías", "tiendas de ropa", "centros de estética", "pastelerías",
    ],
    "Música y noche": [
        "discotecas", "clubs nocturnos", "salas de conciertos", "bares de copas",
        "promotoras de eventos", "promotoras musicales", "agencias de artistas",
        "sellos discográficos", "estudios de grabación", "productoras musicales",
        "escuelas de música", "tiendas de instrumentos", "festivales", "salas de ensayo",
        "academias de DJ",
    ],
    "Hostelería": [
        "restaurantes", "restaurantes nuevos", "puestos de mercado gastronómico",
        "chiringuitos", "cocinas solo delivery", "asadores", "pizzerías", "hamburgueserías", "sushi",
        "comida para llevar", "bocadillerías", "bares de tapas", "comida vegana",
        "cafeterías", "café de especialidad", "teterías", "panaderías", "pastelerías",
        "heladerías", "chocolaterías", "cervecerías artesanales", "bares",
        "vinotecas", "food trucks", "catering",
    ],
    "Moda": [
        "tiendas de ropa", "ropa de mujer", "ropa de hombre", "ropa infantil",
        "ropa deportiva", "moda urbana", "boutiques", "lencería",
        "zapaterías", "marroquinería", "joyerías", "bisutería", "relojerías",
        "ópticas", "ropa laboral", "uniformes",
    ],
    "Belleza y salud": [
        "peluquerías", "barberías", "centros de estética", "spa", "depilación",
        "centros de uñas", "cosmética", "perfumerías", "farmacias",
        "clínicas dentales", "fisioterapia", "centros médicos",
        "nutricionistas", "psicólogos", "laboratorios de análisis", "ortopedias",
    ],
    "Deporte": [
        "gimnasios", "crossfit", "pilates", "estudios de yoga", "piscinas",
        "escuelas de fútbol", "artes marciales", "clubes de pádel",
        "suplementos deportivos", "tiendas de bicicletas",
    ],
    "Alimentación": [
        "herbolarios", "tiendas ecológicas", "charcuterías", "carnicerías",
        "pescaderías", "pasta fresca", "embutidos", "bodegas",
        "tostaderos de café", "aceite de oliva", "miel artesana", "conservas artesanas",
        "productos sin gluten", "distribuidores de alimentación",
    ],
    "Hogar y deco": [
        "tiendas de muebles", "decoración", "interiorismo", "colchonerías",
        "menaje del hogar", "iluminación", "textil hogar", "cortinas", "tiendas de regalos",
        "artículos de fiesta", "droguerías", "viveros", "floristerías",
    ],
    "Construcción": [
        "almacenes de construcción", "ferreterías", "tiendas de pintura", "baños y cocinas",
        "carpintería de aluminio", "constructoras", "piscinas y jardines", "energía solar",
        "climatización", "cerrajerías", "cristalerías", "herrerías",
    ],
    "Agro e industria": [
        "cooperativas agrícolas", "viveros profesionales", "maquinaria agrícola",
        "almazaras", "piensos", "talleres metalúrgicos", "carpinterías", "empresas textiles",
        "imprentas", "artes gráficas", "packaging", "plásticos",
    ],
    "Motor": [
        "talleres mecánicos", "cambio de aceite", "neumáticos", "chapa y pintura",
        "concesionarios", "recambios", "lavaderos de coches", "motos", "car audio",
    ],
    "Mascotas": [
        "clínicas veterinarias", "tiendas de mascotas", "peluquerías caninas",
        "residencias caninas", "pienso para mascotas", "adiestramiento canino",
    ],
    "Servicios": [
        "inmobiliarias", "asesorías", "gestorías", "estudios de arquitectura",
        "correduría de seguros", "agencias de viajes", "logística", "transporte",
        "agentes de aduanas", "consultoras", "empresas de limpieza",
    ],
    "Tecnología": [
        "desarrollo de software", "agencias de marketing digital",
        "agencias de publicidad", "productoras audiovisuales", "diseño gráfico",
        "fotografía", "estudios de grabación", "servicio técnico",
        "informática", "tiendas de móviles",
    ],
    "Educación y niños": [
        "escuelas infantiles", "academias de idiomas", "escuelas de música",
        "academias de baile", "clases particulares", "centros de formación",
        "jugueterías", "librerías", "papelerías", "parques infantiles",
    ],
    "Turismo y eventos": [
        "hoteles", "casas rurales", "hostales", "apartamentos turísticos",
        "salones de eventos", "organización de eventos", "fotografía de bodas",
        "alquiler de mobiliario", "wedding planner",
    ],
}


# Variantes por rubro: cada termino trae lugares distintos en Google Maps.
# Clave = raiz normalizada (una sola palabra) que aparece en lo que escribio el
# usuario. Ojo al agregar: la clave tiene que ser lo que devuelve
# normalizar_termino, no el plural ("hoteles" -> "hotel"). El test
# test_historial lo verifica.
VARIANTES: dict[str, list[str]] = {
    # --- comercio y hosteleria ---
    "ropa": ["boutique", "ropa de mujer", "ropa de hombre", "ropa infantil", "moda urbana",
             "lencería", "zapatería", "complementos de moda", "tienda de ropa"],
    "moda": ["tienda de ropa", "boutique", "moda urbana", "ropa de mujer", "complementos de moda"],
    "boutique": ["tienda de ropa", "ropa de mujer", "moda", "complementos de moda"],
    "panaderia": ["pastelería", "horno de pan", "panadería artesana", "bollería", "obrador"],
    "pasteleria": ["tartas", "repostería", "confitería", "obrador", "panadería artesana"],
    "gimnasio": ["crossfit", "pilates", "centro de entrenamiento", "entrenamiento funcional",
                 "estudio de yoga", "entrenador personal"],
    "cafeteria": ["café de especialidad", "brunch", "coffee shop", "tetería", "desayunos"],
    "cafe": ["cafetería", "café de especialidad", "brunch", "coffee shop", "tetería"],
    "cerveceria": ["cervecería artesanal", "bar", "pub", "brewpub", "vermutería"],
    "bar": ["cervecería", "pub", "coctelería", "vermutería", "bar de tapas"],
    "peluqueria": ["barbería", "salón de belleza", "centro de estética", "centro de uñas", "coloración"],
    "barberia": ["peluquería masculina", "peluquería", "barber shop"],
    "estetica": ["spa", "depilación láser", "cosmetología", "uñas", "cejas y pestañas", "masajes"],
    "veterinaria": ["tienda de mascotas", "peluquería canina", "residencia canina", "pienso"],
    "muebl": [    # "muebles" se normaliza a "muebl"
       "decoración", "interiorismo", "colchonería", "muebles a medida", "menaje del hogar"],
    "restaurante": ["asador", "pizzería", "hamburguesería", "sushi", "comida vegana",
                    "bar de tapas", "arrocería", "marisquería"],
    "pizzeria": ["pizza napolitana", "comida italiana", "comida para llevar", "hamburguesería"],
    "heladeria": ["helados artesanos", "chocolatería", "horchatería"],
    "hamburgueseria": ["burger", "comida rápida", "bocadillería", "smash burger"],
    "cosmetica": ["perfumería", "maquillaje", "cosmética natural", "skincare"],
    "joyeria": ["bisutería", "relojería", "platería", "complementos"],
    "optica": ["gafas", "lentes de contacto", "óptica y audiología"],
    "libreria": ["papelería", "librería infantil", "bellas artes"],
    "jugueteria": ["juguetes educativos", "tienda de regalos", "artículos de fiesta"],
    "vivero": ["plantas", "floristería", "jardinería", "paisajismo"],
    "floristeria": ["vivero", "plantas", "regalos", "arreglos florales"],
    "inmobiliaria": ["agencia inmobiliaria", "promotora", "administración de fincas"],
    "taller": ["mecánica", "cambio de aceite", "neumáticos", "chapa y pintura"],
    "herbolario": ["tienda ecológica", "productos naturales", "dietética", "parafarmacia"],
    "ecologica": ["herbolario", "productos ecológicos", "tienda a granel", "sin gluten"],
    "vinoteca": ["bodega", "vinos", "tienda gourmet", "licorería"],
    "asador": ["parrilla", "restaurante de carnes", "brasería", "restaurante"],
    "sushi": ["comida japonesa", "sushi a domicilio", "wok", "comida asiática"],
    "chocolateria": ["bombonería", "heladería", "repostería", "churrería"],
    "lenceria": ["ropa interior", "corsetería", "pijamas", "ropa de mujer"],
    "marroquineria": ["bolsos", "piel", "maletas", "complementos de piel"],
    "relojeria": ["joyería", "relojes", "reparación de relojes"],
    "uniforme": ["ropa laboral", "vestuario laboral", "uniformes sanitarios", "bordados"],
    "spa": ["centro de estética", "masajes", "balneario", "sauna"],
    "decoracion": ["interiorismo", "tienda de muebles", "menaje del hogar", "iluminación"],
    "colchoneria": ["colchones", "descanso", "tienda de muebles", "textil hogar"],
    "papeleria": ["librería", "bellas artes", "material de oficina", "imprenta"],
    "tapa": ["bar de tapas", "taberna", "restaurante", "gastrobar"],
    "bocadilleria": ["bocadillos", "sandwichería", "comida para llevar"],

    # --- alimentacion (productores) ---
    "carniceria": ["charcutería", "embutidos", "pollería", "carne de calidad"],
    "charcuteria": ["embutidos", "quesería", "jamonería", "tienda gourmet"],
    "embutido": ["charcutería", "jamonería", "quesos artesanos"],
    "pasta": ["pasta fresca", "comida italiana", "tienda gourmet"],
    "bodega": ["vinos", "viñedo", "vinoteca", "cava"],
    "miel": ["apicultura", "productos ecológicos", "herbolario"],
    "pescaderia": ["marisco", "pescado fresco", "sushi"],
    "aceite": ["almazara", "aceite de oliva virgen extra", "tienda gourmet"],

    # --- salud y bienestar ---
    "farmacia": ["parafarmacia", "perfumería", "dermocosmética"],
    "perfumeria": ["cosmética", "maquillaje", "skincare", "parafarmacia"],
    "dental": ["clínica dental", "implantes dentales", "ortodoncia", "odontólogo"],
    "fisioterapia": ["osteopatía", "rehabilitación", "masajes", "quiropráctico"],
    "nutricionista": ["dietista", "nutrición", "consulta de nutrición"],
    "psicologo": ["psicología", "terapia", "psicopedagogía"],
    "laboratorio": ["análisis clínicos", "extracciones", "laboratorio clínico"],
    "ortopedia": ["material sanitario", "sillas de ruedas", "calzado ortopédico"],
    "piscina": ["escuela de natación", "club deportivo", "mantenimiento de piscinas"],
    "yoga": ["estudio de yoga", "pilates", "meditación", "entrenamiento funcional"],
    "crossfit": ["box de crossfit", "entrenamiento funcional", "gimnasio", "halterofilia"],
    "padel": ["club de pádel", "pistas de pádel", "tienda de pádel"],
    "suplemento": ["nutrición deportiva", "proteínas", "herbolario"],
    "bicicleta": ["taller de bicicletas", "ciclismo", "bicicletas eléctricas"],

    # --- hogar y construccion ---
    "ferreteria": ["almacén de construcción", "tienda de pintura", "herramientas", "fontanería",
                   "material eléctrico"],
    "construccion": ["materiales de construcción", "ferretería", "reformas", "cerámica"],
    "pintura": ["pinturas", "ferretería", "revestimientos"],
    "aluminio": ["carpintería de aluminio", "ventanas", "cerramientos", "PVC"],
    "cerrajeria": ["llaves", "cerraduras", "cerrajero urgente"],
    "cristaleria": ["cristales", "espejos", "mamparas"],
    "herreria": ["forja", "puertas metálicas", "estructuras metálicas"],
    "climatizacion": ["aire acondicionado", "calefacción", "refrigeración"],
    "solar": ["energía solar", "placas solares", "energías renovables"],
    "menaje": ["tienda de regalos", "artículos de cocina", "textil hogar", "decoración"],
    "regalo": ["menaje del hogar", "artículos de fiesta", "souvenirs", "juguetes"],
    "iluminacion": ["lámparas", "material eléctrico", "decoración"],
    "textil": ["textil hogar", "ropa de cama", "cortinas", "confección"],
    "drogueria": ["productos de limpieza", "perfumería", "empresa de limpieza"],
    "limpieza": ["productos de limpieza", "empresa de limpieza", "limpieza de oficinas"],
    "reforma": ["empresa de reformas", "constructora", "interiorismo"],
    "fiesta": ["artículos de fiesta", "globos", "cumpleaños infantiles", "disfraces"],

    # --- agro e industria ---
    "agricola": ["maquinaria agrícola", "suministros agrícolas", "cooperativa agrícola"],
    "almazara": ["aceite de oliva", "cooperativa agrícola"],
    "pienso": ["comida para mascotas", "tienda de mascotas", "suministros agrícolas"],
    "maquinaria": ["maquinaria agrícola", "recambios agrícolas", "maquinaria industrial"],
    "metalurgico": ["tornería", "estructuras metálicas", "calderería"],
    "carpinteria": ["muebles a medida", "carpintería de madera", "puertas de madera"],
    "imprenta": ["artes gráficas", "rotulación", "packaging", "serigrafía"],
    "grafica": ["imprenta", "rotulación", "diseño gráfico", "serigrafía"],
    "packaging": ["envases", "embalajes", "bolsas personalizadas"],
    "plastico": ["inyección de plástico", "envases de plástico", "plásticos industriales"],

    # --- motor ---
    "neumatico": ["taller de neumáticos", "alineación", "taller mecánico"],
    "concesionario": ["coches de ocasión", "compraventa de coches", "coches nuevos"],
    "recambio": ["recambios de coche", "accesorios para coches", "desguace"],
    "lavadero": ["lavado de coches", "detailing", "limpieza de vehículos"],
    "moto": ["taller de motos", "recambios de moto", "cascos", "concesionario de motos"],

    # --- mascotas ---
    "mascota": ["tienda de mascotas", "clínica veterinaria", "peluquería canina", "pienso"],
    "canina": ["peluquería canina", "residencia canina", "adiestrador canino"],

    # --- servicios profesionales ---
    "asesoria": ["gestoría", "asesoría fiscal", "asesoría laboral", "contable"],
    "gestoria": ["asesoría", "gestoría administrativa", "trámites"],
    "arquitectura": ["arquitecto", "interiorismo", "aparejador", "constructora"],
    "constructora": ["empresa constructora", "reformas", "promotora"],
    "seguro": ["corredor de seguros", "agencia de seguros", "aseguradora"],
    "viaj": ["agencia de viajes", "turismo", "excursiones"],
    "logistica": ["transporte de mercancías", "almacén", "mensajería", "distribución"],
    "transporte": ["logística", "mudanzas", "transporte de mercancías", "furgonetas"],
    "consultora": ["consultoría", "recursos humanos", "formación empresarial"],

    # --- tecnologia y digital ---
    "software": ["desarrollo de software", "aplicaciones móviles", "desarrollo web"],
    "marketing": ["agencia de marketing digital", "agencia de publicidad", "community manager"],
    "publicidad": ["agencia de publicidad", "marketing digital", "rotulación"],
    "fotografia": ["estudio fotográfico", "fotógrafo de bodas", "productora audiovisual"],
    "audiovisual": ["productora audiovisual", "vídeo corporativo", "estudio de grabación"],
    "informatica": ["servicio técnico", "reparación de ordenadores", "tienda de informática"],
    "movil": ["reparación de móviles", "accesorios para móviles", "tienda de telefonía"],

    # --- educacion y niños ---
    "infantil": ["escuela infantil", "guardería", "ludoteca"],
    "idioma": ["academia de inglés", "academia de idiomas", "clases de inglés"],
    "baile": ["academia de baile", "escuela de danza", "estudio de danza"],
    "musica": ["escuela de música", "tienda de instrumentos", "estudio de grabación"],
    "formacion": ["academia", "centro de estudios", "oposiciones"],

    # --- turismo y eventos ---
    "hotel": ["hostal", "apartamentos turísticos", "casa rural", "pensión"],
    "rural": ["casa rural", "turismo rural", "alojamiento rural"],
    "hostal": ["hotel", "pensión", "apartamentos turísticos"],
    "evento": ["salón de eventos", "organización de eventos", "catering", "wedding planner"],
    "catering": ["servicio de catering", "salón de eventos", "organización de eventos"],

    # --- musica y noche ---
    "discoteca": ["club nocturno", "sala de fiestas", "bar de copas", "sala de baile"],
    "club": ["discoteca", "club nocturno", "sala de fiestas", "bar de copas"],
    "concierto": ["sala de conciertos", "sala de música en directo", "auditorio", "música en vivo"],
    "promotora": ["promotora de eventos", "promotora musical", "organización de conciertos", "agencia de booking"],
    "artista": ["agencia de artistas", "management musical", "agencia de booking", "representación artística"],
    "discografico": ["sello discográfico", "discográfica", "editorial musical", "productora musical"],
    "sello": ["sello discográfico", "discográfica", "editorial musical"],
    "grabacion": ["estudio de grabación", "productora musical", "estudio de mezcla", "sala de ensayo"],
    "productora": ["productora musical", "productora audiovisual", "estudio de grabación"],
    "festival": ["festival de música", "promotora de eventos", "organización de eventos"],
    "ensayo": ["sala de ensayo", "local de ensayo", "estudio de grabación"],
    "dj": ["academia de DJ", "escuela de DJ", "productora musical"],
    "instrumento": ["tienda de música", "luthier", "tienda de instrumentos musicales"],
    "copa": ["bar de copas", "coctelería", "pub", "discoteca"],

    # --- gastronomia nueva ---
    "mercado": ["puesto de mercado", "mercado gastronómico", "food hall", "street food"],
    "chiringuito": ["chiringuito de playa", "beach bar", "bar de playa", "terraza"],
    "delivery": ["comida a domicilio", "dark kitchen", "cocina fantasma", "comida para llevar"],
    "cocina": ["dark kitchen", "comida a domicilio", "obrador", "catering"],
}

# Zonas/barrios de las ciudades grandes, para el campo "Zona". Solo tiene
# sentido donde una sola corrida de Maps no alcanza a cubrir la ciudad.
# No poner aca una localidad que ya sea una ciudad de apify.CIUDADES de la
# misma provincia (seria la misma corrida pagada dos veces).
ZONAS: dict[str, list[str]] = {
    "Madrid": [
        "Centro", "Malasaña", "Chueca", "Lavapiés", "La Latina", "Chamberí", "Salamanca",
        "Retiro", "Chamartín", "Tetuán", "Arganzuela", "Moncloa", "Argüelles", "Carabanchel",
        "Usera", "Puente de Vallecas", "Ciudad Lineal", "Hortaleza", "Fuencarral",
        "Moratalaz", "San Blas", "Barajas", "Villaverde"],
    "Barcelona": [
        "Eixample", "Gràcia", "Sants", "Les Corts", "Sarrià-Sant Gervasi", "Horta-Guinardó",
        "Nou Barris", "Sant Andreu", "Sant Martí", "Poblenou", "Ciutat Vella", "El Born",
        "El Raval", "Poble-sec", "Sant Antoni"],
    "Valencia": ["Ciutat Vella", "Ruzafa", "L'Eixample", "Benimaclet", "El Cabanyal", "Campanar",
                 "Patraix", "Algirós", "Extramurs", "Benicalap", "Quatre Carreres"],
    "Sevilla": ["Centro", "Triana", "Nervión", "Los Remedios", "Macarena", "Sevilla Este",
                "Bellavista", "San Bernardo", "Alameda"],
    "Málaga": ["Centro", "El Palo", "Teatinos", "Huelin", "Pedregalejo", "Ciudad Jardín",
               "Soho", "Carretera de Cádiz"],
    "Zaragoza": ["Centro", "Delicias", "Actur", "Universidad", "Las Fuentes", "San José",
                 "Casco Histórico", "Valdespartera"],
    "Bilbao": ["Abando", "Indautxu", "Deusto", "Casco Viejo", "Santutxu", "San Ignacio"],
    "Palma": ["Centro", "Santa Catalina", "Son Espanyolet", "El Molinar", "Playa de Palma",
              "Pere Garau"],
    "Las Palmas de Gran Canaria": ["Triana", "Vegueta", "Guanarteme", "Mesa y López",
                                   "Schamann", "Ciudad Jardín"],
    "Murcia": ["Centro", "El Carmen", "Santa María de Gracia", "Espinardo", "Juan Carlos I"],
    "Alicante": ["Centro", "Playa de San Juan", "Benalúa", "Carolinas", "San Blas"],
    "Valladolid": ["Centro", "Delicias", "Parquesol", "La Rubia", "Huerta del Rey"],
    "Córdoba": ["Centro", "Ciudad Jardín", "Centro Comercial", "Santa Rosa", "Fuensanta"],
    "Granada": ["Centro", "Albaicín", "Realejo", "Zaidín", "Chana", "Ronda"],
    "Vigo": ["Centro", "Bouzas", "Coia", "Teis", "Casco Vello"],
    "A Coruña": ["Centro", "Monte Alto", "Os Mallos", "Cuatro Caminos", "Los Rosales"],
}


def variantes_de(rubro: str) -> list[str]:
    n = normalizar_termino(rubro)
    salida: list[str] = []
    for raiz, lista in VARIANTES.items():
        if raiz in n.split():                 # palabra completa: "bar" no es "barberia"
            for v in lista:
                if normalizar_termino(v) != n and v not in salida:
                    salida.append(v)
    return salida


def zonas_de(ciudad: str) -> list[str]:
    n = normalizar_termino(ciudad)
    for nombre, lista in ZONAS.items():
        c = normalizar_termino(nombre)
        if n == c or n.startswith(c + " ") or n.replace("ciudad de ", "") == c:
            return list(lista)
    return []


# --------------------------------------------------------------------------
# Historial agrupado
# --------------------------------------------------------------------------
def _filas(conn: Connection) -> list[dict[str, Any]]:
    cols = [c for c in busquedas.c if c.name != "crudo"]
    return [dict(f._mapping) for f in conn.execute(select(*cols).order_by(busquedas.c.id))]


def _rendimiento(nuevos: int | None, encontrados: int | None) -> float | None:
    if not encontrados:
        return None
    return round(100 * (nuevos or 0) / encontrados, 1)


def estado_de(ultima: dict[str, Any]) -> str:
    """rinde | bajando | agotada | en_curso | fallida | sin_resultados"""
    if ultima["estado"] in ESTADOS_EN_CURSO:
        return "en_curso"
    if ultima["estado"] == "fallida":
        return "fallida"
    r = _rendimiento(ultima.get("nuevos"), ultima.get("encontrados"))
    if r is None:
        return "sin_resultados"
    return "rinde" if r >= 40 else "bajando" if r >= 15 else "agotada"


def historial(conn: Connection) -> list[dict[str, Any]]:
    """Una fila por (rubro, ciudad, zona): cuantas veces, cuanto trajo y cuanto rinde."""
    grupos: dict[tuple, dict[str, Any]] = {}
    for b in _filas(conn):
        k = clave(b["rubro"], b["ciudad"], b.get("zona"))
        g = grupos.setdefault(k, {
            "rubro": b["rubro"], "ciudad": b["ciudad"], "zona": b.get("zona") or "",
            "veces": 0, "encontrados": 0, "nuevos": 0, "costo_usd": 0.0, "max_limite": 0,
            "primera": b["creado"], "ultima": None, "busqueda_ids": []})
        g["veces"] += 1
        g["busqueda_ids"].append(b["id"])
        if b["estado"] in ESTADOS_OK or b["estado"] in ESTADOS_EN_CURSO:
            g["max_limite"] = max(g["max_limite"], b["limite"] or 0)
        g["encontrados"] += b.get("encontrados") or 0
        g["nuevos"] += b.get("nuevos") or 0
        g["costo_usd"] += float(b.get("costo_usd") or 0)
        g["ultima"] = b                      # _filas viene ordenado por id
        # se muestra el texto tal como se escribio la ultima vez
        g["rubro"], g["ciudad"], g["zona"] = b["rubro"], b["ciudad"], b.get("zona") or ""
    salida = []
    for g in grupos.values():
        u = g.pop("ultima")
        g["ultima_fecha"] = u["creado"]
        g["ultima_limite"] = u["limite"]
        g["ultima_nuevos"] = u.get("nuevos")
        g["ultima_encontrados"] = u.get("encontrados")
        g["rendimiento_ultima"] = _rendimiento(u.get("nuevos"), u.get("encontrados"))
        g["rendimiento_total"] = _rendimiento(g["nuevos"], g["encontrados"])
        g["estado"] = estado_de(u)
        g["costo_usd"] = round(g["costo_usd"], 3)
        salida.append(g)
    salida.sort(key=lambda g: g["ultima_fecha"], reverse=True)
    return salida


def _leads_de_busquedas(conn: Connection, ids: list[int]) -> int:
    if not ids:
        return 0
    return conn.execute(select(func.count()).select_from(leads)
                        .where(leads.c.busqueda_id.in_(ids))).scalar_one()


def analizar(conn: Connection, rubro: str, ciudad: str, zona: str = "", limite: int = 40) -> dict[str, Any]:
    """
    Todo lo que conviene saber ANTES de lanzar una busqueda:
    si ya se hizo, cuanto rindio, que se puede esperar y que alternativas hay.
    """
    k = clave(rubro, ciudad, zona)
    hist = historial(conn)
    misma = next((g for g in hist if clave(g["rubro"], g["ciudad"], g["zona"]) == k), None)
    n_ciudad = normalizar_termino(ciudad)
    en_ciudad = [g for g in hist if normalizar_termino(g["ciudad"]) == n_ciudad]
    leads_ciudad = conn.execute(select(func.count()).select_from(leads).where(
        func.lower(func.coalesce(leads.c.ciudad, "")) == (ciudad or "").strip().lower())).scalar_one() if ciudad else 0

    ya_buscados_rubro = {normalizar_termino(g["rubro"]) for g in en_ciudad if not g["zona"]}
    ya_buscadas_zonas = {normalizar_termino(g["zona"]) for g in en_ciudad
                         if g["zona"] and normalizar_termino(g["rubro"]) == k[0]}
    variantes = [{"texto": v, "ya_buscado": normalizar_termino(v) in ya_buscados_rubro} for v in variantes_de(rubro)]
    zonas = [{"texto": z, "ya_buscado": normalizar_termino(z) in ya_buscadas_zonas} for z in zonas_de(ciudad)]
    # Primero las que todavia no se buscaron
    variantes.sort(key=lambda v: v["ya_buscado"])
    zonas.sort(key=lambda z: z["ya_buscado"])

    aviso, nuevos_esperados = "", None
    if misma:
        prev = misma["max_limite"]
        if misma["estado"] == "en_curso":
            aviso = "en_curso"
        elif limite <= prev:
            aviso = "sin_novedad"            # Maps va a devolver los mismos lugares
            nuevos_esperados = 0
        else:
            aviso = "ampliar"
            nuevos_esperados = limite - prev  # como maximo: los que estan despues de los ya traidos
    return {
        "clave": {"rubro": rubro, "ciudad": ciudad, "zona": zona},
        "misma": misma,
        "leads_de_esta_busqueda": _leads_de_busquedas(conn, misma["busqueda_ids"]) if misma else 0,
        "leads_ciudad": leads_ciudad,
        "busquedas_ciudad": len(en_ciudad),
        "aviso": aviso,
        "nuevos_esperados": nuevos_esperados,
        "variantes": variantes[:10],
        "zonas": zonas,
    }
