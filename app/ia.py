"""
Clasificacion con Claude: prioridad, marca y motivo de cada lead nuevo.

- Solo clasifica: NO redacta mensajes (la app no envia nada).
- Lotes de 8 leads por llamada, salida con esquema JSON estricto.
- La marca solo se acepta si cada palabra esta en el titulo (marca_valida):
  si la IA inventa o traduce, se descarta y manda la heuristica.
- Un lote que falla no corta el resto; sin API key la app sigue andando.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from . import claves
from .plantillas import marca_valida

log = logging.getLogger(__name__)

MODELO = os.environ.get("IA_MODEL", "claude-opus-5")
LOTE = 8
PRIORIDADES = ("alta", "media", "baja")


class IAError(RuntimeError):
    pass


PROMPT = """\
Eres asistente de un despacho de abogados en España que registra marcas ante la
OEPM (Oficina Española de Patentes y Marcas) y la EUIPO.
Te paso datos PÚBLICOS de negocios sacados de Google Maps. Para cada uno:

1. prioridad: qué tan buen candidato es para ofrecerle registrar su marca. La
   abogada busca PYMES Y PROYECTOS PEQUEÑOS PERO ESTABLES Y REGULARIZADOS, que ya
   funcionan y todavía no registraron su marca. Le interesan especialmente la
   música y la noche (discotecas, salas, promotoras, estudios, sellos) y la
   gastronomía (restaurantes nuevos, puestos de mercado, cocinas de delivery).
   - "alta": nombre comercial propio y distintivo Y señales de negocio estable:
     bastantes reseñas (más de 30 aprox.), web propia o dominio propio, horario
     fijo, local físico, más de un local, o forma jurídica en el nombre (S.L.,
     S.A.). Si es de música/noche o gastronomía, súbelo un poco.
   - "media": nombre propio pero con poca señal de estabilidad (pocas reseñas,
     sin web), o algo genérico pero con actividad clara.
   - "baja": nombre puramente descriptivo y no registrable como tal ("Panadería
     del Centro", "Bar Plaza"); sucursal o franquicia de una cadena conocida
     que ya tiene su marca registrada; profesional que trabaja con su nombre y
     apellidos; actividad que parece un hobby o algo ocasional (venta casera,
     sin local, sin web y casi sin reseñas); organismo público, colegio,
     parroquia, asociación o similar.

2. marca: LA MARCA del negocio, tal como se la mostraría la abogada ("el registro
   de la marca X"). El título de Maps suele traer actividad, ciudad, forma
   jurídica o eslogan pegados: "Panadería La Espiga S.L." -> "La Espiga".
   - Quita actividad, ciudad, número de local y forma jurídica (S.L., S.A.,
     S.L.U., C.B.) SOLO cuando sobran.
   - Si la actividad es parte del nombre, déjala: "Verde Vivero" es "Verde
     Vivero"; "La Tasca de Pepe" es "La Tasca de Pepe". Pero "Peluquería
     Marta Ruiz" es "Marta Ruiz" y "Kali Moda" es "Kali".
   - Conserva los artículos: "La Rosa", no "Rosa".
   - Cópiala TAL CUAL aparece en el título. No inventes, no traduzcas (tampoco
     del catalán, euskera o gallego), no añadas palabras. Si no puedes aislarla
     con confianza, devuelve el título.

3. motivo: una línea, en español de España, explicando la prioridad.

Devuelve un resultado por cada negocio, con el mismo "id" que te pasé.

NEGOCIOS:
{negocios}
"""

ESQUEMA = {
    "type": "object",
    "properties": {
        "resultados": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "prioridad": {"type": "string", "enum": list(PRIORIDADES)},
                    "marca": {"type": "string"},
                    "motivo": {"type": "string"},
                },
                "required": ["id", "prioridad", "marca", "motivo"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["resultados"],
    "additionalProperties": False,
}


def configurada() -> bool:
    return bool(claves.valor("anthropic"))


def _cliente(api_key: str | None = None):
    clave = api_key or claves.valor("anthropic")
    if not clave:
        raise IAError("Falta la clave de Anthropic: cárgala en Ajustes → Conexiones.")
    import anthropic
    return anthropic.Anthropic(api_key=clave, max_retries=3, timeout=120)


def _resumen(lead: dict[str, Any]) -> dict[str, Any]:
    d = {
        "id": lead["id"],
        "titulo": (lead.get("negocio") or "").strip(),   # CRUDO, sin podar
        "rubro": lead.get("rubro") or "",
        "ciudad": lead.get("ciudad") or "",
        "web": lead.get("web") or "",
        "instagram": lead.get("instagram") or "",
        "resenas": lead.get("resenas") or 0,
        "rating": lead.get("rating") or 0,
    }
    return {k: v for k, v in d.items() if v not in ("", 0, None)}


def _llamar(client, prompt: str) -> dict[str, Any] | None:
    """Una llamada. Devuelve el JSON o None si el modelo no contesto util."""
    import anthropic
    params: dict[str, Any] = {
        "model": MODELO,
        "max_tokens": 4000,
        "output_config": {"effort": "low",
                          "format": {"type": "json_schema", "schema": ESQUEMA}},
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        # Fallback del lado del servidor si el modelo declina (no deberia pasar
        # con nombres de negocios, pero no cuesta nada tenerlo).
        resp = client.beta.messages.create(
            **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    except anthropic.BadRequestError as exc:
        if "fallback" not in str(exc).lower():
            raise
        resp = client.messages.create(**params)
    if resp.stop_reason in ("refusal", "max_tokens"):
        log.warning("IA: lote sin respuesta util (stop_reason=%s)", resp.stop_reason)
        return None
    texto = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        return json.loads(texto)
    except ValueError:
        log.warning("IA: respuesta que no es JSON: %s", texto[:200])
        return None


def clasificar(leads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    {lead_id: {prioridad, motivo_ia, marca_ia?}} para los leads que salieron bien.

    Los que no aparecen en el resultado quedan para reintentar.
    """
    import anthropic
    leads = [l for l in leads if l.get("id")]
    if not leads:
        return {}
    client = _cliente()
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(leads), LOTE):
        tanda = leads[i:i + LOTE]
        prompt = PROMPT.format(negocios=json.dumps([_resumen(l) for l in tanda],
                                                   ensure_ascii=False, indent=1))
        try:
            datos = _llamar(client, prompt)
        except anthropic.AuthenticationError as exc:
            raise IAError("Anthropic rechazó la clave (401). Cárgala de nuevo en Ajustes → Conexiones.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise IAError("La API key de Anthropic no tiene permiso (403).") from exc
        except anthropic.APIError as exc:
            log.warning("IA: lote %d falló: %s", i // LOTE + 1, exc)
            continue
        if not datos:
            continue
        por_id = {l["id"]: l for l in tanda}
        for r in datos.get("resultados") or []:
            lead = por_id.get(r.get("id"))
            if not lead:                      # id inventado: se descarta
                continue
            prioridad = (r.get("prioridad") or "").strip().lower()
            campos: dict[str, Any] = {
                "prioridad": prioridad if prioridad in PRIORIDADES else "media",
                "motivo_ia": (r.get("motivo") or "").strip()[:500],
                "marca_ia": None,
            }
            marca = (r.get("marca") or "").strip()
            if marca and marca_valida(marca, lead.get("negocio") or ""):
                campos["marca_ia"] = marca[:200]
            salida[lead["id"]] = campos
    return salida


def probar(api_key: str | None = None) -> str:
    import anthropic
    client = _cliente(api_key)
    try:
        resp = client.messages.create(model=MODELO, max_tokens=50,
                                      output_config={"effort": "low"},
                                      messages=[{"role": "user", "content": "Responde solo: ok"}])
    except anthropic.AuthenticationError as exc:
        raise IAError("Anthropic rechazó la clave. Revisa que esté bien copiada.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise IAError("La clave de Anthropic no tiene permiso.") from exc
    except anthropic.BadRequestError as exc:
        if "credit" in str(exc).lower():
            raise IAError("La cuenta de Anthropic no tiene saldo: compra créditos en Billing.") from exc
        log.warning("IA: error al probar la clave: %s", exc)
        raise IAError("Anthropic no aceptó la prueba. Revisa la clave y el saldo de la cuenta.") from exc
    except anthropic.APIError as exc:
        log.warning("IA: error al probar la clave: %s", exc)
        raise IAError("No se pudo conectar con Anthropic. Vuelve a intentarlo en un rato.") from exc
    return f"ok ({resp.model})"
