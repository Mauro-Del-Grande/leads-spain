# Cómo conectar tus cuentas (5 minutos)

La app usa dos servicios con **tus propias cuentas**:

- **Apify**: busca los negocios en Google Maps.
- **Anthropic**: la inteligencia artificial que los ordena por prioridad.

Cada servicio te da una **clave** (un texto largo). La pegas una sola vez en la app y listo. Nadie más la ve: la app la guarda cifrada y después solo muestra los últimos 4 caracteres.

---

## 1. Clave de Apify

1. Entra en **console.apify.com** y crea una cuenta (puedes usar tu cuenta de Google).
2. En el menú de la izquierda ve a **Settings → API & Integrations**.
3. En **Personal API tokens** copia el token. Empieza por `apify_api_`.
4. En la app: **Ajustes → Conexiones → Apify**, pégalo y toca **Guardar y probar**.

Debería salir en verde "Apify conectado". El plan gratis trae algo de crédito cada mes para probar. Cada búsqueda de 40 negocios cuesta unos 0,16 US$.

## 2. Clave de Anthropic

1. Entra en **platform.claude.com** y crea una cuenta. Es distinta de la cuenta de chat de Claude, aunque uses el mismo email.
2. Ve a **Settings → Billing** y toca **Buy credits**. Con 5 US$ tienes para muchísimas búsquedas.
3. Ve a **Settings → API keys** y toca **Create key**. Ponle un nombre, por ejemplo "Radar de Marcas", y copia la clave. Empieza por `sk-ant-` y **solo se muestra una vez**.
4. En la app: **Ajustes → Conexiones → Anthropic**, pégala y toca **Guardar y probar**.

Debería salir en verde "IA conectada".

---

## Si algo falla

| Mensaje | Qué hacer |
|---|---|
| "rechazó la clave" | La copiaste incompleta. Vuelve a copiarla entera. En Anthropic, si ya no la ves, crea una nueva. |
| "no tiene saldo" / "Sin créditos" | Carga saldo en la cuenta de ese servicio. |
| "No se pudo conectar" | Problema momentáneo: prueba de nuevo en unos minutos. |
| "hay que volver a cargarla" | Pega la clave otra vez. |

## Buenas prácticas

- No mandes las claves por WhatsApp ni por email: pégalas directamente en la app.
- Si crees que alguien vio una clave, bórrala en la web del servicio, crea una nueva y pégala en la app. La anterior deja de funcionar al instante.
- Para cambiar una clave basta con pegar la nueva encima: reemplaza a la anterior.
