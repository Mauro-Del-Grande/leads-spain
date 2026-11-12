# Radar de Marcas · España

Captación de prospectos y CRM para un despacho que registra marcas en España
(OEPM / EUIPO). La app busca negocios en Google Maps con Apify, los prioriza con
IA (marca propia y distintiva, actividad real) y lleva el seguimiento hasta que
el negocio pasa a cliente.

Es una copia adaptada de la app argentina (`leads-marca`), con una diferencia de
fondo: **no envía ni prepara mensajes.** En España la LSSI (art. 21) prohíbe las
comunicaciones comerciales por email, WhatsApp u otro medio electrónico que el
destinatario no haya pedido o autorizado, también entre empresas. La app
encuentra y ordena; el contacto se hace por fuera (llamada, visita, carta…) y
en la app solo se **registra** cómo fue.

---

## Qué cambió respecto de la versión argentina

| | Argentina | España |
|---|---|---|
| Contacto | Mensaje armado por WhatsApp / IG / email | Sin mensajes: se registra llamada, visita, carta, "me contactó" u otro |
| Topes y esperas por canal | Sí | No hacen falta (se quitaron) |
| Ciudades | 252 de Argentina | 292 de España, las 50 provincias + Ceuta y Melilla |
| Rubros | Vocabulario argentino | Vocabulario de España (hostelería, asesorías, herbolarios…) |
| Teléfonos | +54 | +34 (móvil 6/7, fijo 8/9; descarta 900/902/80x) |
| Hora | Buenos Aires | Madrid |
| Importes | Pesos | Euros |
| IA | INPI | OEPM / EUIPO, quita S.L., S.A., C.B. del nombre |

## Claves del cliente

Cada cliente usa **sus propias cuentas** de Apify y Anthropic. Las pega en
**Ajustes → Conexiones**: la app las prueba contra el servicio y solo si
funcionan las guarda **cifradas** en la base (con una llave derivada de
`SECRET_KEY`). Nunca se vuelven a mostrar ni quedan en el historial: solo los
últimos 4 caracteres. La clave cargada en la app tiene prioridad sobre las
variables `APIFY_TOKEN` / `ANTHROPIC_API_KEY`, que quedan como respaldo.

Guía paso a paso para el cliente: [GUIA-CLAVES.md](GUIA-CLAVES.md).

> Si se cambia `SECRET_KEY`, las claves guardadas no se pueden leer: la app lo
> avisa en Ajustes y hay que volver a pegarlas.

## Cruce con registros de marcas (EUIPO)

Para cada prospecto de prioridad alta o media, el trabajo de fondo busca en la
API oficial de la EUIPO (Trademark Search) marcas de la UE iguales, que suenan
igual o muy parecidas, y deja un semáforo (rojo / amarillo / verde) según el
parecido y si comparten clase de Niza con la actividad del negocio
(`registros.py`). Se ve en la tarjeta de Hoy y en la ficha, con enlace a cada
marca. Es orientativo: el juicio sobre el riesgo es de la abogada.

Las credenciales (Client ID + Client Secret de dev.euipo.europa.eu, con la
suscripción a "Trademark search" aprobada) se cargan en Ajustes → Conexiones.
Guía: [GUIA-EUIPO.md](GUIA-EUIPO.md). `EUIPO_ENTORNO=sandbox` apunta al entorno
de pruebas de la EUIPO.

**Pendiente:** marcas solo nacionales (OEPM). Su servicio web pide usuario y
contraseña que da la OEPM por formulario; mientras tanto la ficha enlaza al
Localizador y a TMview para revisar a mano.

## Garantías

| Qué | Cómo se garantiza |
|---|---|
| **Nunca dos leads para el mismo negocio** | Cada negocio tiene varias claves (id de Google, teléfono, Instagram, web, nombre+ciudad). Si un negocio nuevo coincide con dos leads, se fusionan. |
| **Nunca dos "primeros contactos"** | `contactos.lead_id` es `UNIQUE` y el estado nunca vuelve a "Nuevo". Registrar dos veces (o desde dos dispositivos) no duplica. |
| **No se pierde nada** | Todo cambio queda en `eventos` (quién, cuándo, antes/después). La respuesta cruda de Apify se guarda y se puede reimportar sin volver a pagar. |
| **"No molestar" es permanente** | Aunque el negocio vuelva a aparecer en otra búsqueda. |

## Uso diario

1. **Hoy**: arriba, los seguimientos que vencen hoy; abajo, los prospectos ordenados por prioridad. Se pueden filtrar por dato de contacto (teléfono, móvil, web…).
2. Cada tarjeta muestra la **marca** (se puede corregir), por qué la IA le dio esa prioridad, y los datos: teléfono (fijo o móvil), web, Instagram, Google Maps.
3. Contacta al negocio por fuera de la app y toca **Registrar contacto**: cómo fue, una nota y, si quieres, la próxima acción con fecha. Ese negocio sale de la cola.
4. Si no sirve, **Descartar** (pasa a "No interesa").
5. Cuando responda, en **Leads** se le cambia el estado (Respondió → Propuesta → Cliente) y se cargan presupuesto y cobro en euros.

## Buscar negocios

**Buscar** → rubro + ciudad (o **barrido** por varias ciudades; "Principales" marca las capitales de provincia y grandes ciudades) + cantidad.
Antes de lanzar la búsqueda, la app muestra el costo estimado y avisa si esa
búsqueda ya se hizo. Los resultados entran solos en unos minutos y la IA los clasifica.

---

## Despliegue en Railway (una sola vez)

1. En [railway.com](https://railway.com): **New Project → Deploy from GitHub repo** → elegí este repo.
2. En el mismo proyecto: **+ New → Database → PostgreSQL**.
3. En el servicio de la app, pestaña **Variables**:

   | Variable | Valor |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (referencia al servicio de Postgres) |
   | `SECRET_KEY` | una clave larga al azar: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `APIFY_TOKEN` | *(opcional)* respaldo si el cliente no cargó su clave |
   | `ANTHROPIC_API_KEY` | *(opcional)* respaldo si el cliente no cargó su clave |
   | `USUARIO_INICIAL_EMAIL` | el email del primer usuario |
   | `USUARIO_INICIAL_NOMBRE` | tu nombre |
   | `USUARIO_INICIAL_CLAVE` | contraseña de 10 caracteres o más |

4. **Settings → Networking → Generate Domain** para tener la URL pública (https).
5. Entrá con ese usuario → **Ajustes**:
   - **Añadir usuario** para cada persona que la use.
   - **Probar Apify** y **Probar IA**.
6. Borrá las tres variables `USUARIO_INICIAL_*`: ya no hacen falta.
7. Configurá los backups (ver abajo).

## Backups

Los backups dentro de Railway requieren el plan Pro. En su lugar, **GitHub hace
una copia completa de la base todas las noches** (03:00 hora argentina) y la
guarda 90 días en este repo (privado). Si una noche falla, GitHub te avisa por mail.

Configuración (una vez):
1. Railway → servicio **Postgres** → **Settings → Networking → TCP Proxy** (activarlo).
2. Copiar **`DATABASE_PUBLIC_URL`** de **Postgres → Variables**.
3. GitHub → este repo → **Settings → Secrets and variables → Actions → New repository secret**:
   nombre **`DATABASE_BACKUP_URL`**, valor = esa URL.
4. **Actions → Backup de la base → Run workflow** para probarlo. Al terminar, el
   backup está en la corrida, sección *Artifacts*, junto con `resumen.txt`, que tiene
   la cantidad de leads, contactos y eventos.

**Restaurar** (Actions → **Restaurar backup** → Run workflow):
- Pide el número de la corrida del backup y que escribas `RESTAURAR`.
- Restaura en la base del secreto **`DATABASE_RESTORE_URL`** y **se niega a correr
  si ese destino es la base de producción**. Lo seguro es restaurar en una base
  nueva, revisar y recién después apuntar la app a ella.

Además, desde la app, **Ajustes → Exportar → Backup completo (JSON)**
baja todo en cualquier momento.

El `railway.json` ya define el comando de arranque (gunicorn), el healthcheck
(`/salud`) y el reinicio automático ante fallas.

## Desarrollo local

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env        # y completar
.venv\Scripts\flask --app app crear-usuario tu@mail.com "Tu nombre"
.venv\Scripts\flask --app app run
```

Sin `DATABASE_URL`, la app usa SQLite en `data/crm.db`.

## Tests

```bash
.venv\Scripts\pytest
```

Los tests corren sin red y sin credenciales. Cubren duplicados, fusiones,
doble registro de contacto, teléfonos españoles, hora de Madrid, recuperación
tras un reinicio, guardarraíles de la IA, que la app ya no prepare mensajes,
las rutas HTTP y el login.

Para correr la misma suite contra Postgres, usá una base de **prueba**
(la suite borra las tablas):

```bash
set TEST_DATABASE_URL=postgresql://...   &&   .venv\Scripts\pytest
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `app/identidad.py` | normalizadores (teléfono ES, IG, web, email) y claves anti-duplicados |
| `app/crm.py` | altas con fusión, registro de contacto, estados, edición, métricas |
| `app/db.py` | esquema, bloqueos (advisory locks en Postgres), hora de Madrid |
| `app/apify.py` | Google Maps vía Apify + lista de ciudades de España |
| `app/historial.py` | rubros, variantes, barrios y "no pagar dos veces la misma búsqueda" |
| `app/trabajos.py` | trabajo de fondo: búsquedas e IA, recuperación al reiniciar |
| `app/ia.py` | Claude: prioridad y marca, con salida JSON validada |
| `app/claves.py` | claves de Apify/Anthropic/EUIPO del cliente, cifradas en la base |
| `app/registros.py` | cruce con la EUIPO: parecido de nombres, clases de Niza y semáforo |
| `app/plantillas.py` | extracción de la marca a partir del título de Maps |
