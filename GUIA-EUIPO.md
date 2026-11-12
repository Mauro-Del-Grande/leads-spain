# Cruce con registros de marcas: cómo activarlo

Con esto la app revisa sola, para cada prospecto prometedor, si en la **EUIPO** hay marcas de la Unión Europea iguales, que suenan igual o muy parecidas, y te lo marca con un semáforo:

- 🔴 **Rojo**: hay una marca activa igual o muy parecida en una clase de Niza que coincide con la actividad del negocio.
- 🟡 **Amarillo**: hay algo parecido, pero en otras clases o con menos parecido.
- 🟢 **Verde**: no apareció nada relevante en la UE.

En la ficha de cada negocio ves las marcas encontradas: número, titular, estado y clases, con enlace directo a la EUIPO. **Es una orientación automática**: decidir si hay riesgo de confusión sigue siendo tu criterio profesional.

Es gratis. Solo hace falta pedir acceso a la API de la EUIPO, una vez.

---

## Paso 1. Cuenta en la EUIPO

Si ya tienes usuario en la web de la EUIPO (el de presentar marcas), sirve ese. Si no, crea uno en **euipo.europa.eu** → *Registrarse*.

## Paso 2. Registrar la app

1. Entra en **dev.euipo.europa.eu** y toca **Sign in** con tu usuario de la EUIPO.
2. Arriba aparece **Apps**: entra y registra una app nueva. Como nombre pon "Radar de Marcas"; la descripción puede ser "Búsqueda de antecedentes para mi despacho".
3. Al crearla te muestra un **Client ID** y un **Client Secret**. Guárdalos: son los dos datos que vas a pegar en la app.

## Paso 3. Suscribirte a "Trademark search"

1. En el catálogo de APIs (**API Products**) abre **Trademark search**.
2. Toca **Get access** (suscribirse), elige la app que creaste y el plan disponible, y acepta las condiciones de uso.
3. **Espera la aprobación**: la EUIPO revisa las solicitudes a mano y puede tardar unos días. Te avisan por correo.

## Paso 4. Pegarlo en la app

Cuando llegue la aprobación: **Ajustes → Conexiones → EUIPO**. Pega el **Client ID** y el **Client Secret** y toca **Guardar y probar**. Si sale en verde "EUIPO conectada", ya está: en unos minutos los prospectos de prioridad alta y media empiezan a mostrar su semáforo. En cualquier ficha también puedes tocar **Revisar ahora**.

---

## Si algo falla

| Mensaje | Qué pasa |
|---|---|
| "rechazó el Client ID o el Client Secret" | Están mal copiados. Cópialos de nuevo desde *Apps*. |
| "no autoriza la búsqueda… suscripción" | La suscripción a *Trademark search* todavía no está aprobada. |
| "No se pudo conectar" | Problema momentáneo de la EUIPO: prueba más tarde. |

## ¿Y las marcas solo españolas (OEPM)?

Las marcas de la UE tienen efecto en España, pero las nacionales están en la OEPM. La OEPM también tiene un servicio gratuito para consultas automáticas, aunque pide **usuario y contraseña** que da la propia OEPM:

1. En **oepm.es** → *Servicios web de la OEPM* → **Localizador de marcas**, rellena el formulario de solicitud de acceso.
2. Cuando la OEPM te mande el usuario y la contraseña, avísanos y lo sumamos al cruce automático.

Mientras tanto, en cada ficha tienes enlaces para revisar a mano en el **Localizador de la OEPM** y en **TMview**, que reúne las oficinas de toda la UE.
