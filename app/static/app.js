"use strict";

/* ====================================================================
   Utilidades
   ==================================================================== */
const $ = (sel, raiz = document) => raiz.querySelector(sel);
const $$ = (sel, raiz = document) => [...raiz.querySelectorAll(sel)];

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

const ESTADOS = ["nuevo", "contactado", "respondio", "propuesta", "cliente", "no interesa", "no molestar"];
const ETIQUETA = {
  "nuevo": "Nuevo", "contactado": "Contactado", "respondio": "Respondió", "propuesta": "Propuesta",
  "cliente": "Cliente", "no interesa": "No interesa", "no molestar": "No molestar"
};
const TZ = "Europe/Madrid";
const CANAL = { llamada: "Llamada", visita: "Visita en persona", carta: "Carta / correo postal",
  respuesta: "Me contactó el negocio", otro: "Otro", manual: "Sin detalle", migrado: "Sistema anterior",
  whatsapp: "WhatsApp", instagram: "Instagram", email: "Email" };

const chipEstado = e => `<span class="chip e-${esc((e || "").replace(" ", "-"))}">${esc(ETIQUETA[e] || e)}</span>`;
const chipPrioridad = p => p ? `<span class="chip ${esc(p)}">${esc(p)}</span>` : `<span class="chip">sin clasificar</span>`;

const _euros = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const euros = n => (n == null ? "–" : _euros.format(n));
const montoInput = n => (n == null ? "" : new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2, useGrouping: "always" }).format(n));

function fecha(iso, conHora = true) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  if (isNaN(d)) return iso;
  const op = { day: "2-digit", month: "2-digit", year: "numeric", timeZone: TZ };
  if (conHora && iso.length > 10) Object.assign(op, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleString("es-ES", op);
}

function avisar(texto, tipo = "") {
  const el = document.createElement("div");
  el.className = "aviso-flotante " + tipo;
  el.textContent = texto;
  $("#avisos").appendChild(el);
  setTimeout(() => { el.classList.add("saliendo"); setTimeout(() => el.remove(), 260); }, tipo === "error" ? 7000 : 3500);
}

// Onda al tocar cualquier boton (feedback tactil en el móvil)
document.addEventListener("pointerdown", e => {
  const b = e.target.closest(".btn");
  if (!b || b.disabled) return;
  const r = b.getBoundingClientRect(), d = Math.max(r.width, r.height);
  const o = document.createElement("span");
  o.className = "onda";
  Object.assign(o.style, { width: d + "px", height: d + "px", left: (e.clientX - r.left - d / 2) + "px", top: (e.clientY - r.top - d / 2) + "px" });
  b.appendChild(o);
  setTimeout(() => o.remove(), 600);
});

async function api(ruta, { metodo = "GET", datos } = {}) {
  const op = { method: metodo, headers: { "Accept": "application/json" }, credentials: "same-origin" };
  if (datos !== undefined) {
    op.headers["Content-Type"] = "application/json";
    op.body = JSON.stringify(datos);
  }
  let r;
  try {
    r = await fetch(ruta, op);
  } catch (e) {
    throw new Error("Sin conexión. Revisa internet y vuelve a intentarlo.");
  }
  if (r.status === 401) { location.href = "/login"; throw new Error("Sesión vencida"); }
  let cuerpo = {};
  try { cuerpo = await r.json(); } catch (e) { /* sin cuerpo */ }
  if (!r.ok) throw new Error(cuerpo.error || `Error ${r.status}`);
  return cuerpo;
}

async function conBoton(boton, fn) {
  if (boton) boton.disabled = true;
  try { return await fn(); }
  catch (e) { avisar(e.message, "error"); }
  finally { if (boton) boton.disabled = false; }
}

/* ====================================================================
   Navegacion
   ==================================================================== */
const vistas = { hoy: cargarHoy, leads: cargarLeads, buscar: cargarBuscar, metricas: cargarMetricas, ajustes: cargarAjustes };
let vistaActual = "hoy";

function mostrarVista(nombre) {
  vistaActual = nombre;
  $$("#tabs button").forEach(b => b.classList.toggle("activo", b.dataset.vista === nombre));
  $$(".vista").forEach(v => v.hidden = v.id !== "vista-" + nombre);
  try { localStorage.setItem("vista", nombre); } catch (e) { /* modo privado */ }
  vistas[nombre]();
}
$$("#tabs button").forEach(b => b.addEventListener("click", () => mostrarVista(b.dataset.vista)));

/* ====================================================================
   HOY
   La app NO envia ni prepara mensajes (LSSI art. 21): muestra los
   prospectos con sus datos y deja REGISTRAR como se los contacto.
   ==================================================================== */
let colaHoy = [];
let pendientesHoy = 0;
let canalesContacto = [];   // [[clave, etiqueta], ...] en orden
let filtroMedio = "";
try { filtroMedio = localStorage.getItem("filtro-medio") || ""; } catch (e) { /* modo privado */ }

const MEDIO = { telefono: "Con teléfono", movil: "Con móvil", email: "Con email", instagram: "Con Instagram", web: "Con web" };

/** Sello circular: cuantos negocios se contactaron hoy (la "firma" de la app). */
function pintarSelloDia(hoy) {
  const el = $("#sello-dia");
  if (!el) return;
  const meta = 10;                        // referencia visual, no un tope
  const r = 46, largo = 2 * Math.PI * r;
  const frac = Math.min(1, hoy / meta);
  el.innerHTML = `
    <svg viewBox="0 0 112 112" class="${frac >= 1 ? "lleno" : ""}">
      <defs><linearGradient id="grad-anillo" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#fcd34d"/></linearGradient></defs>
      <circle cx="56" cy="56" r="${r}" fill="none" class="pista" stroke-width="8"/>
      <circle cx="56" cy="56" r="${r}" fill="none" class="progreso" stroke-width="8" stroke-linecap="round"
        stroke-dasharray="${largo}" stroke-dashoffset="${largo}" transform="rotate(-90 56 56)"/>
    </svg>
    <div class="centro"><div><b>${hoy}</b><br><span>contactos<br>hoy</span></div></div>`;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const p = el.querySelector(".progreso");
    if (p) p.style.strokeDashoffset = String(largo * (1 - frac));
  }));
  el.setAttribute("aria-label", `${hoy} negocios contactados hoy`);
}

function pintarCupo(hoy) {
  const el = $("#cupo");
  el.textContent = `${hoy} ${hoy === 1 ? "contacto" : "contactos"} hoy`;
  el.title = "Negocios contactados hoy (registrados en la app)";
  pintarSelloDia(hoy);
}

async function refrescarEstado() {
  try {
    const e = await api("/api/estado");
    pintarCupo(e.contactados_hoy);
    return e;
  } catch (err) { return null; }
}

/** Saludo segun la hora y la fecha de hoy (hora de Madrid). */
function pintarSaludo() {
  const el = $("#hoy-saludo");
  if (!el) return;
  const ahora = new Date();
  const hora = +ahora.toLocaleString("en-US", { hour: "numeric", hour12: false, timeZone: TZ });
  const saludo = hora < 6 ? "Buenas noches" : hora < 14 ? "Buenos días" : hora < 21 ? "Buenas tardes" : "Buenas noches";
  el.textContent = `${saludo}, ${el.dataset.nombre}`;
  $("#hoy-fecha").textContent = ahora.toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long", timeZone: TZ });
}

/** Lluvia de confeti (celebracion al cerrar un cliente). */
function confeti(cantidad = 90) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const colores = ["#1e3a5f", "#c9a227", "#22b8f0", "#4ade80", "#fbbf24", "#f472b6"];
  const cont = document.createElement("div");
  cont.className = "confeti";
  for (let i = 0; i < cantidad; i++) {
    const p = document.createElement("i");
    p.style.left = Math.random() * 100 + "vw";
    p.style.background = colores[i % colores.length];
    p.style.setProperty("--x", (Math.random() * 30 - 15) + "vw");
    p.style.setProperty("--r", (Math.random() * 900 - 450) + "deg");
    p.style.setProperty("--d", (1.4 + Math.random() * 1.4) + "s");
    p.style.animationDelay = Math.random() * .35 + "s";
    cont.appendChild(p);
  }
  document.body.appendChild(cont);
  setTimeout(() => cont.remove(), 3400);
}

/** Numeros que suben al entrar a Metricas. */
function contarHasta(el) {
  const fin = parseFloat(el.dataset.valor);
  const formato = el.dataset.formato === "euros" ? euros : (v => Math.round(v).toLocaleString("es-ES") + (el.dataset.sufijo || ""));
  if (!isFinite(fin) || matchMedia("(prefers-reduced-motion: reduce)").matches) { el.textContent = formato(fin); return; }
  const t0 = performance.now(), dur = 900;
  const paso = t => {
    const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    el.textContent = formato(fin * e);
    if (k < 1) requestAnimationFrame(paso);
  };
  requestAnimationFrame(paso);
}

async function cargarHoy() {
  const cont = $("#hoy-cola");
  try {
    const d = await api("/api/hoy");
    colaHoy = d.cola;
    pendientesHoy = d.pendientes;
    canalesContacto = d.canales_contacto;
    pintarCupo(d.contactados_hoy);
    pintarSaludo();
    pintarSeguimientos(d.seguimientos);
    $("#hoy-resumen").textContent = `${d.pendientes} ${d.pendientes === 1 ? "prospecto esperando" : "prospectos esperando"} primer contacto · ${d.contactados_hoy} ${d.contactados_hoy === 1 ? "contactado" : "contactados"} hoy`;
    pintarFiltroMedio();
    pintarCola();
  } catch (e) {
    cont.innerHTML = `<div class="vacio error">${esc(e.message)}</div>`;
  }
}

const tieneMedio = (l, m) => m === "movil" ? l.es_movil : l.medios.includes(m);
const colaFiltrada = () => filtroMedio ? colaHoy.filter(l => tieneMedio(l, filtroMedio)) : colaHoy;

/** Chips para filtrar la cola por el dato de contacto que tienen. */
function pintarFiltroMedio() {
  const el = $("#hoy-canales");
  const opciones = [["", "Todos", colaHoy.length],
                    ...Object.keys(MEDIO).map(m => [m, MEDIO[m], colaHoy.filter(l => tieneMedio(l, m)).length])];
  el.innerHTML = opciones
    .filter(([m, , n]) => !m || n || m === filtroMedio)
    .map(([m, nombre, n]) => `<button data-medio="${m}" class="${m === filtroMedio ? "activo" : ""}"
        aria-pressed="${m === filtroMedio}">${esc(nombre)} · ${n}</button>`).join("");
  $("#hoy-orden").textContent = filtroMedio ? MEDIO[filtroMedio] : "Ordenado por prioridad";
  $$("button", el).forEach(b => b.onclick = () => {
    filtroMedio = b.dataset.medio;
    try { localStorage.setItem("filtro-medio", filtroMedio); } catch (e) { /* modo privado */ }
    pintarFiltroMedio();
    pintarCola();
  });
}

function pintarCola() {
  const cont = $("#hoy-cola");
  const lista = colaFiltrada();
  const icono = `<svg class="icono-vacio" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/></svg>`;
  if (!lista.length) {
    cont.innerHTML = pendientesHoy
      ? `<div class="vacio">${icono}<b>Ninguno con ese filtro.</b><br>Prueba con "Todos".</div>`
      : `<div class="vacio">${icono}<b>No hay prospectos pendientes.</b><br>Haz una búsqueda nueva en <b>Buscar</b>.</div>`;
    return;
  }
  cont.innerHTML = lista.map(tarjetaCola).join("");
  $$(".tarjeta", cont).forEach((t, i) => { t.classList.add("entra"); t.style.setProperty("--i", Math.min(i, 8)); });
}

function pintarSeguimientos(lista) {
  const el = $("#hoy-seguimientos");
  if (!lista.length) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <div class="encabezado"><h2>Seguimientos para hoy</h2><span class="sutil">${lista.length}</span></div>
    <div class="lista-filas">${lista.map(l => `
      <div class="fila" data-abrir="${esc(l.id)}">
        <div class="izq">
          <div class="n">${esc(l.marca)} </div>
          <div class="m">${esc(l.proxima_accion || "Sin detalle")} · ${fecha(l.proxima_fecha, false)}</div>
        </div>
        <div class="der">${chipEstado(l.estado)}</div>
      </div>`).join("")}
    </div>`;
}

const urlWeb = w => /^https?:/i.test(w) ? w : "https://" + w;
const telVisible = l => l.telefono_e164
  ? l.telefono_e164.replace(/^\+34(\d{3})(\d{3})(\d{3})$/, "$1 $2 $3")
  : (l.telefono_raw || "");

/** Datos de contacto como enlaces para INVESTIGAR o llamar (nunca para enviar texto). */
function datosContacto(l) {
  const d = [];
  if (l.telefono_e164 || l.telefono_raw) {
    const tipo = l.telefono_e164 ? (l.es_movil ? "móvil" : "fijo") : "sin validar";
    d.push(`<a class="dato" href="tel:${esc(l.telefono_e164 || l.telefono_raw)}">📞 ${esc(telVisible(l))} <small>${tipo}</small></a>`);
  }
  if (l.web) d.push(`<a class="dato" href="${esc(urlWeb(l.web))}" target="_blank" rel="noopener">🌐 Web</a>`);
  if (l.instagram) d.push(`<a class="dato" href="https://instagram.com/${esc(l.instagram)}" target="_blank" rel="noopener">📷 @${esc(l.instagram)}</a>`);
  if (l.maps_url) d.push(`<a class="dato" href="${esc(l.maps_url)}" target="_blank" rel="noopener">📍 Maps</a>`);
  if (l.email) d.push(`<span class="dato" title="Solo para referencia: no enviar publicidad sin consentimiento">✉️ ${esc(l.email)}</span>`);
  return d.join("");
}

function tarjetaCola(l) {
  const meta = esc([l.rubro, l.ciudad].filter(Boolean).join(" · ")) +
    (l.resenas ? ` · <span class="estrellas">★ ${esc(l.rating ?? "–")}</span> (${esc(l.resenas)} reseñas)` : "");
  return `
  <article class="tarjeta p-${esc(l.prioridad || "sin")}" id="t-${esc(l.id)}">
    <div class="titulo">
      <div class="nombre" data-abrir="${esc(l.id)}">${esc(l.negocio)}</div>
      ${chipPrioridad(l.prioridad)}
    </div>
    <div class="meta">${meta}</div>
    ${l.direccion ? `<div class="meta">${esc(l.direccion)}</div>` : ""}
    <div class="marca-edit">
      <span title="La marca que tendría que registrar">Marca</span>
      <input value="${esc(l.marca)}" data-marca-original="${esc(l.marca)}" id="m-${esc(l.id)}" maxlength="200" aria-label="Marca">
    </div>
    ${l.motivo_ia ? `<div class="motivo">${esc(l.motivo_ia)}</div>` : ""}
    <div class="datos-contacto">${datosContacto(l) || '<span class="sutil">Sin datos de contacto: búscalo en Maps.</span>'}</div>
    <div class="canales">
      <button class="btn primario" data-registrar="${esc(l.id)}">Registrar contacto</button>
      <button class="btn" data-abrir="${esc(l.id)}">Ver ficha</button>
      <button class="btn fantasma" data-descartar="${esc(l.id)}" title="Marcar como 'No interesa' y sacarlo de la cola">Descartar</button>
    </div>
  </article>`;
}

/** Guarda la marca si se corrigio en la tarjeta. */
async function guardarMarcaTarjeta(leadId) {
  const input = $("#m-" + CSS.escape(leadId));
  if (!input) return;
  const marca = input.value.trim();
  if (marca && marca !== input.dataset.marcaOriginal) {
    await api(`/api/leads/${encodeURIComponent(leadId)}`, { metodo: "PATCH", datos: { marca_manual: marca } });
    input.dataset.marcaOriginal = marca;
  }
}

function abrirRegistro(leadId) {
  const l = colaHoy.find(x => x.id === leadId) || { id: leadId, negocio: "" };
  abrirPanel(`
    <h2>Registrar contacto${l.negocio ? " · " + esc(l.negocio) : ""}</h2>
    <p class="sutil">Deja constancia de cómo contactaste a este negocio. La app no envía nada: solo lo anota
      para que no vuelva a aparecer en la cola y quede en el historial.</p>
    <div class="opciones-canal" role="radiogroup" aria-label="Cómo fue el contacto">
      ${canalesContacto.map(([k, v], i) => `
        <label class="opcion"><input type="radio" name="r-canal" value="${esc(k)}" ${i === 0 ? "checked" : ""}> ${esc(v)}</label>`).join("")}
    </div>
    <label>Nota (opcional) <textarea id="r-nota" maxlength="2000" placeholder="ej: hablé con la dueña, le interesa, volver a llamar en enero"></textarea></label>
    <label>Próxima acción (opcional) <input id="r-accion" maxlength="300" placeholder="ej: enviarle presupuesto"></label>
    <label>Fecha <input id="r-fecha" type="date"></label>
    <button class="btn primario grande" id="r-guardar">Guardar</button>`);
  $("#r-guardar").onclick = ev => conBoton(ev.target, async () => {
    const canal = $("input[name=r-canal]:checked").value;
    await guardarMarcaTarjeta(leadId);
    const r = await api("/api/contacto/registrar", { metodo: "POST", datos: { lead_id: leadId, canal, nota: $("#r-nota").value } });
    const accion = $("#r-accion").value.trim(), cuando = $("#r-fecha").value;
    if (accion || cuando) {
      await api(`/api/leads/${encodeURIComponent(r.lead_id)}`, { metodo: "PATCH", datos: { proxima_accion: accion, proxima_fecha: cuando } });
    }
    avisar(r.ya_registrado ? "Ya estaba registrado." : "Listo: contacto registrado.", "ok");
    pintarCupo(r.contactados_hoy);
    cerrarPanel();
    await sacarTarjeta(leadId, "Contactado");
    refrescarVistaActual();
  });
}

async function sacarTarjeta(leadId, texto) {
  const t = $("#t-" + CSS.escape(leadId));
  if (t && vistaActual === "hoy") {
    t.insertAdjacentHTML("beforeend", `<div class="sello-enviado">${esc(texto)}</div>`);
    await new Promise(res => setTimeout(res, 650));
    t.classList.add("saliendo");
    await new Promise(res => setTimeout(res, 280));
  }
}

document.addEventListener("click", e => {
  const reg = e.target.closest("[data-registrar]");
  if (reg) { abrirRegistro(reg.dataset.registrar); return; }
  const desc = e.target.closest("[data-descartar]");
  if (desc) {
    if (!confirm("¿Descartar este negocio? Pasa a 'No interesa' y sale de la cola.")) return;
    conBoton(desc, async () => {
      await api(`/api/leads/${encodeURIComponent(desc.dataset.descartar)}/estado`, { metodo: "POST", datos: { estado: "no interesa" } });
      await sacarTarjeta(desc.dataset.descartar, "Descartado");
      refrescarVistaActual();
    });
    return;
  }
  const abrir = e.target.closest("[data-abrir]");
  if (abrir) abrirLead(abrir.dataset.abrir);
});

/* ====================================================================
   Panel lateral
   ==================================================================== */
function abrirPanel(html) {
  $("#panel-contenido").innerHTML = html;
  $("#panel").hidden = false;
  $("#velo").hidden = false;
  $("#panel").scrollTop = 0;
  document.body.style.overflow = "hidden";
}
function cerrarPanel() {
  $("#panel").hidden = true;
  $("#velo").hidden = true;
  document.body.style.overflow = "";
}
$("#panel-cerrar").onclick = cerrarPanel;
$("#velo").onclick = cerrarPanel;
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#panel").hidden) cerrarPanel(); });

/* ====================================================================
   Ficha de un lead
   ==================================================================== */
async function abrirLead(id) {
  try {
    const d = await api("/api/leads/" + encodeURIComponent(id));
    pintarFicha(d);
  } catch (e) { avisar(e.message, "error"); }
}

function pintarFicha({ lead: l, historial, contacto }) {
  const enlaces = [];
  if (l.telefono_e164) enlaces.push(`<a href="tel:${esc(l.telefono_e164)}">Llamar</a>`);
  if (l.instagram) enlaces.push(`<a href="https://instagram.com/${esc(l.instagram)}" target="_blank" rel="noopener">Ver Instagram</a>`);
  if (l.maps_url) enlaces.push(`<a href="${esc(l.maps_url)}" target="_blank" rel="noopener">Google Maps</a>`);
  const siguientes = ESTADOS.filter(e => e !== "nuevo" && e !== l.estado && e !== "contactado");

  abrirPanel(`
    <h2>${esc(l.negocio)}</h2>
    <div class="fila-acciones">${chipEstado(l.estado)} ${chipPrioridad(l.prioridad)}</div>
    <dl class="datos">
      <dt>Marca</dt><dd>${esc(l.marca)}</dd>
      <dt>Rubro</dt><dd>${esc(l.rubro || "–")}</dd>
      <dt>Ciudad</dt><dd>${esc([l.ciudad, l.provincia].filter(Boolean).join(", ") || "–")}</dd>
      <dt>Dirección</dt><dd>${esc(l.direccion || "–")}</dd>
      <dt>Teléfono</dt><dd>${esc(telVisible(l) || "–")}${l.telefono_e164 ? ` <span class="chip">${l.es_movil ? "móvil" : "fijo"}</span>` : l.telefono_raw ? ' <span class="chip">no válido</span>' : ""}</dd>
      <dt>Instagram</dt><dd>${l.instagram ? "@" + esc(l.instagram) : "–"}</dd>
      <dt>Email</dt><dd>${esc(l.email || "–")}</dd>
      <dt>Web</dt><dd>${l.web ? `<a href="${esc(/^https?:/i.test(l.web) ? l.web : "https://" + l.web)}" target="_blank" rel="noopener">${esc(l.web)}</a>` : "–"}</dd>
      <dt>Reseñas</dt><dd>${l.resenas ? `★ ${esc(l.rating)} (${esc(l.resenas)})` : "–"}</dd>
      <dt>Contacto</dt><dd>${contacto && contacto.fecha ? `${esc(CANAL[contacto.canal] || contacto.canal)} · ${fecha(contacto.fecha)}${contacto.usuario ? " · " + esc(contacto.usuario) : ""}${contacto.texto ? "<br>" + esc(contacto.texto) : ""}` : "Todavía no"}</dd>
      ${l.motivo_ia ? `<dt>IA</dt><dd>${esc(l.motivo_ia)}</dd>` : ""}
    </dl>
    <div class="fila-acciones">${enlaces.join(" · ")}</div>

    <div class="panel">
      <h3>Estado</h3>
      ${l.estado === "nuevo" ? `<div class="fila-acciones"><button class="btn primario chico" data-registrar="${esc(l.id)}">Registrar contacto</button></div>` : ""}
      <div class="estados">
        ${siguientes.map(e => `<button class="btn chico ${e.startsWith("no ") ? "peligro" : ""}" data-estado="${esc(e)}">${esc(ETIQUETA[e])}</button>`).join("")}
      </div>
      <details><summary class="sutil">Reabrir (volver a un estado anterior)</summary>
        <div class="fila-acciones">
          ${ESTADOS.filter(e => ESTADOS.indexOf(e) < ESTADOS.indexOf(l.estado) && (e !== "nuevo" || !contacto.fecha)).map(e =>
            `<button class="btn chico" data-reabrir="${esc(e)}">${esc(ETIQUETA[e])}</button>`).join("") || '<span class="sutil">No hay estados anteriores disponibles.</span>'}
        </div>
      </details>
    </div>

    <div class="panel">
      <h3>Seguimiento</h3>
      <label>Próxima acción <input id="e-proxima_accion" value="${esc(l.proxima_accion || "")}" maxlength="300" placeholder="ej: enviarle presupuesto"></label>
      <label>Fecha <input id="e-proxima_fecha" type="date" value="${esc(l.proxima_fecha || "")}"></label>
      <label>Notas <textarea id="e-notas">${esc(l.notas || "")}</textarea></label>
      <button class="btn primario" id="guardar-seguimiento">Guardar</button>
    </div>

    <div class="panel">
      <h3>Importes</h3>
      <div class="fila-acciones">
        <label>Presupuesto (€) <input id="e-monto_presupuesto" inputmode="decimal" value="${esc(montoInput(l.monto_presupuesto))}" placeholder="ej: 1.500"></label>
        <label>Cobrado (€) <input id="e-monto_cobrado" inputmode="decimal" value="${esc(montoInput(l.monto_cobrado))}" placeholder="ej: 1.500"></label>
        <label>Fecha de cobro <input id="e-fecha_cobro" type="date" value="${esc(l.fecha_cobro || "")}"></label>
      </div>
      <p class="sutil">En euros. Si pones lo cobrado sin fecha, se toma la de hoy. Para borrar un importe, déjalo vacío.</p>
      <button class="btn primario" id="guardar-plata">Guardar importes</button>
    </div>

    <details class="panel">
      <summary><b>Editar datos</b></summary>
      <label>Nombre del negocio <input id="e-negocio" value="${esc(l.negocio)}"></label>
      <label>Marca <input id="e-marca_manual" value="${esc(l.marca_manual || "")}" placeholder="${esc(l.marca)}"></label>
      <label>Rubro <input id="e-rubro" value="${esc(l.rubro || "")}"></label>
      <label>Ciudad <input id="e-ciudad" value="${esc(l.ciudad || "")}"></label>
      <label>Teléfono <input id="e-telefono_raw" value="${esc(l.telefono_raw || "")}" inputmode="tel"></label>
      <label>Instagram <input id="e-instagram" value="${esc(l.instagram || "")}" placeholder="usuario"></label>
      <label>Email <input id="e-email" value="${esc(l.email || "")}" type="email"></label>
      <label>Web <input id="e-web" value="${esc(l.web || "")}"></label>
      <label>Prioridad
        <select id="e-prioridad">
          ${["", "alta", "media", "baja"].map(p => `<option value="${p}" ${p === (l.prioridad || "") ? "selected" : ""}>${p || "sin clasificar"}</option>`).join("")}
        </select>
      </label>
      <button class="btn primario" id="guardar-datos">Guardar datos</button>
    </details>

    <details class="panel">
      <summary><b>¿Está repetido?</b> Fusionar con otro lead</summary>
      <p class="sutil">Si este negocio aparece dos veces, busca el otro y fusiónalos. Se conserva lo de los dos y el primer contacto.</p>
      <input id="fusion-q" type="search" placeholder="Buscar el otro lead…">
      <div id="fusion-res" class="lista-filas"></div>
    </details>

    <div class="panel">
      <h3>Historial</h3>
      <label>Añadir nota <textarea id="nota-nueva" placeholder="ej: me dijo que lo piensa"></textarea></label>
      <button class="btn" id="agregar-nota">Añadir al historial</button>
      <ul class="historial">${historial.map(itemHistorial).join("")}</ul>
    </div>
  `);

  const id = l.id;
  $$("[data-estado]", $("#panel")).forEach(b => b.onclick = () => cambiarEstado(id, b.dataset.estado, false, b));
  $$("[data-reabrir]", $("#panel")).forEach(b => b.onclick = () => {
    if (confirm(`¿Volver a "${ETIQUETA[b.dataset.reabrir]}"?`)) cambiarEstado(id, b.dataset.reabrir, true, b);
  });
  $("#guardar-seguimiento").onclick = ev => guardarCampos(id, ["proxima_accion", "proxima_fecha", "notas"], ev.target);
  $("#guardar-plata").onclick = ev => {
    const cobrado = $("#e-monto_cobrado").value.trim();
    const campos = ["monto_presupuesto", "monto_cobrado"];
    if ($("#e-fecha_cobro").value || !cobrado) campos.push("fecha_cobro");
    guardarCampos(id, campos, ev.target).then(ok => {
      if (ok && cobrado && l.estado !== "cliente" && confirm("¿Lo marco también como Cliente?")) {
        cambiarEstado(id, "cliente", ESTADOS.indexOf("cliente") < ESTADOS.indexOf(l.estado));
      }
    });
  };
  $("#guardar-datos").onclick = ev => guardarCampos(id, ["negocio", "marca_manual", "rubro", "ciudad", "telefono_raw", "instagram", "email", "web", "prioridad"], ev.target);
  $("#agregar-nota").onclick = ev => conBoton(ev.target, async () => {
    const texto = $("#nota-nueva").value.trim();
    if (!texto) return;
    await api(`/api/leads/${encodeURIComponent(id)}/nota`, { metodo: "POST", datos: { texto } });
    abrirLead(id);
  });
  let temporizador;
  $("#fusion-q").oninput = ev => {
    clearTimeout(temporizador);
    temporizador = setTimeout(() => buscarParaFusion(id, ev.target.value), 300);
  };
}

function itemHistorial(ev) {
  const d = ev.datos || {};
  let txt = ev.tipo;
  if (ev.tipo === "creado") txt = `Creado (${d.fuente || ""})`;
  else if (ev.tipo === "estado") txt = `Estado: ${ETIQUETA[d.desde] || d.desde} → ${ETIQUETA[d.hasta] || d.hasta}`;
  else if (ev.tipo === "reabierto") txt = `Reabierto: ${ETIQUETA[d.desde] || d.desde} → ${ETIQUETA[d.hasta] || d.hasta}`;
  else if (ev.tipo === "contacto") txt = `Primer contacto: ${CANAL[d.canal] || d.canal}${d.nota ? " — " + d.nota : ""}`;
  else if (ev.tipo === "nota") txt = `📝 ${d.texto}`;
  else if (ev.tipo === "editado") txt = `Datos editados: ${Object.keys(d.despues || {}).join(", ")}`;
  else if (ev.tipo === "fusionado") txt = `Se fusionó un duplicado (${d.motivo || ""})`;
  else if (ev.tipo === "completado") txt = `Datos completados desde ${d.fuente}: ${(d.campos || []).join(", ")}`;
  return `<li><div>${esc(txt)}</div><div class="cuando">${fecha(ev.ts)}${ev.usuario ? " · " + esc(ev.usuario) : ""}</div></li>`;
}

async function cambiarEstado(id, estado, reabrir, boton) {
  if (estado === "no molestar" && !confirm("“No molestar” es definitivo: no volverá a aparecer para contactar. ¿Seguir?")) return;
  await conBoton(boton, async () => {
    await api(`/api/leads/${encodeURIComponent(id)}/estado`, { metodo: "POST", datos: { estado, reabrir } });
    avisar("Estado actualizado", "ok");
    abrirLead(id);
    refrescarVistaActual();
  });
}

async function guardarCampos(id, campos, boton) {
  const datos = {};
  campos.forEach(c => { datos[c] = $("#e-" + c).value; });
  return await conBoton(boton, async () => {
    await api(`/api/leads/${encodeURIComponent(id)}`, { metodo: "PATCH", datos });
    avisar("Guardado", "ok");
    abrirLead(id);
    refrescarVistaActual();
    return true;
  }) === true;
}

async function buscarParaFusion(id, q) {
  const cont = $("#fusion-res");
  if (!q.trim()) { cont.innerHTML = ""; return; }
  try {
    const r = await api("/api/leads?q=" + encodeURIComponent(q));
    const otros = r.leads.filter(l => l.id !== id).slice(0, 8);
    cont.innerHTML = otros.map(l => `
      <div class="fila estatica">
        <div class="izq"><div class="n">${esc(l.negocio)}</div><div class="m">${esc([l.ciudad, l.telefono_raw, l.instagram && "@" + l.instagram].filter(Boolean).join(" · "))}</div></div>
        <div class="der">${chipEstado(l.estado)} <button class="btn chico" data-fusionar="${esc(l.id)}">Fusionar</button></div>
      </div>`).join("") || '<div class="sutil">Sin resultados.</div>';
    $$("[data-fusionar]", cont).forEach(b => b.onclick = () => {
      if (!confirm("¿Seguro que son el mismo negocio? Esto no se puede deshacer.")) return;
      conBoton(b, async () => {
        const r2 = await api("/api/leads/fusionar", { metodo: "POST", datos: { a: id, b: b.dataset.fusionar } });
        avisar("Fusionados", "ok");
        abrirLead(r2.lead_id);
        refrescarVistaActual();
      });
    });
  } catch (e) { cont.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}

function refrescarVistaActual() {
  if (vistaActual === "leads") cargarLeads(false);
  else if (vistaActual === "hoy") cargarHoy();
}

/* ====================================================================
   LEADS
   ==================================================================== */
let paginaLeads = 1;
(() => {
  const sel = $("#f-estado");
  ESTADOS.forEach(e => sel.insertAdjacentHTML("beforeend", `<option value="${esc(e)}">${esc(ETIQUETA[e])}</option>`));
  let t;
  $("#f-q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => cargarLeads(true), 300); });
  ["#f-estado", "#f-prioridad", "#f-canal", "#f-ciudad"].forEach(s => $(s).addEventListener("change", () => cargarLeads(true)));
})();

async function cargarLeads(reiniciar = true) {
  if (reiniciar) paginaLeads = 1;
  const p = new URLSearchParams({
    q: $("#f-q").value, estado: $("#f-estado").value, prioridad: $("#f-prioridad").value,
    canal: $("#f-canal").value, ciudad: $("#f-ciudad").value, pagina: paginaLeads
  });
  const lista = $("#leads-lista");
  try {
    const r = await api("/api/leads?" + p);
    const selCiudad = $("#f-ciudad");
    const actual = selCiudad.value;
    selCiudad.innerHTML = `<option value="">Todas las ciudades</option>` +
      r.ciudades.map(c => `<option ${c === actual ? "selected" : ""}>${esc(c)}</option>`).join("");
    $("#leads-total").textContent = `${r.total} leads`;
    lista.innerHTML = r.leads.map(l => `
      <div class="fila" data-abrir="${esc(l.id)}">
        <div class="izq">
          <div class="n">${esc(l.negocio)}</div>
          <div class="m">${esc([l.rubro, l.ciudad].filter(Boolean).join(" · "))} ${l.medios.map(c => ({ telefono: "📞", instagram: "📷", email: "✉️", web: "🌐" })[c] || "").join(" ")}</div>
        </div>
        <div class="der">${l.monto_cobrado ? `<span class="chip e-cliente">${esc(euros(l.monto_cobrado))}</span>` : l.monto_presupuesto ? `<span class="chip">presup. ${esc(euros(l.monto_presupuesto))}</span>` : ""} ${chipPrioridad(l.prioridad)} ${chipEstado(l.estado)}</div>
      </div>`).join("") || `<div class="vacio">No hay leads con esos filtros.</div>`;
    const paginas = Math.max(1, Math.ceil(r.total / r.por_pagina));
    $("#leads-paginado").innerHTML = paginas > 1 ? `
      <button class="btn chico" ${paginaLeads <= 1 ? "disabled" : ""} id="pag-ant">‹ Anterior</button>
      <span class="sutil">Página ${paginaLeads} de ${paginas}</span>
      <button class="btn chico" ${paginaLeads >= paginas ? "disabled" : ""} id="pag-sig">Siguiente ›</button>` : "";
    const ant = $("#pag-ant"), sig = $("#pag-sig");
    if (ant) ant.onclick = () => { paginaLeads--; cargarLeads(false); };
    if (sig) sig.onclick = () => { paginaLeads++; cargarLeads(false); };
  } catch (e) { lista.innerHTML = `<div class="vacio error">${esc(e.message)}</div>`; }
}

/* ====================================================================
   BUSCAR
   ==================================================================== */
let ciudadesLista = [], maxBarrido = 60, familiasRubro = [];
const ciudadesElegidas = new Set();      // indices dentro de ciudadesLista
let refrescoBusquedas = null;

const sinTildes = s => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

async function cargarBuscar() {
  if (!ciudadesLista.length) {
    try {
      const r = await api("/api/ciudades");
      ciudadesLista = r.ciudades;
      maxBarrido = r.max_barrido || maxBarrido;
      $("#lista-ciudades").innerHTML = ciudadesLista.map(c => `<option value="${esc(c.ciudad)}">${esc(c.provincia)}</option>`).join("");
      pintarCiudades();
    } catch (e) { avisar(e.message, "error"); }
  }
  if (!familiasRubro.length) cargarRubros();
  cargarBusquedas();
  cargarHistorial();
  analizarBusqueda();
}

/* ---------- Rubros: familias arriba, chips de la familia elegida abajo ---------- */
async function cargarRubros() {
  try { familiasRubro = (await api("/api/rubros")).familias; } catch (e) { return; }
  $("#familias-rubro").innerHTML = familiasRubro.map((f, i) =>
    `<button type="button" data-familia="${i}">${esc(f.nombre)}</button>`).join("");
  pintarRubros(0);
}

function pintarRubros(i) {
  $$("#familias-rubro button").forEach(b => b.classList.toggle("activo", +b.dataset.familia === i));
  $("#chips-rubro").innerHTML = (familiasRubro[i] ? familiasRubro[i].rubros : [])
    .map(r => `<button type="button" data-rubro="${esc(r)}">${esc(r)}</button>`).join("");
  marcarRubroActivo();
}

function marcarRubroActivo() {
  const v = $("#b-rubro").value.trim().toLowerCase();
  $$("#chips-rubro button").forEach(b => b.classList.toggle("activo", b.dataset.rubro.toLowerCase() === v));
}

$("#familias-rubro").addEventListener("click", ev => {
  const b = ev.target.closest("button[data-familia]");
  if (b) pintarRubros(+b.dataset.familia);
});
$("#chips-rubro").addEventListener("click", ev => {
  const b = ev.target.closest("button[data-rubro]");
  if (!b) return;
  $("#b-rubro").value = b.dataset.rubro;
  marcarRubroActivo();
  analizarBusqueda();
});

/* ---------- Barrido: ciudades agrupadas por provincia, con filtro ---------- */
function pintarCiudades() {
  const texto = $("#b-filtro-ciudad").value.trim(), f = sinTildes(texto);
  const grupos = new Map();
  ciudadesLista.forEach((c, i) => {
    if (f && !sinTildes(c.ciudad).includes(f) && !sinTildes(c.provincia).includes(f)) return;
    if (!grupos.has(c.provincia)) grupos.set(c.provincia, []);
    grupos.get(c.provincia).push(i);
  });
  $("#b-ciudades").innerHTML = grupos.size
    ? [...grupos].map(([prov, idx]) => `
        <div class="grupo-prov">
          <div class="cabeza-prov"><b>${esc(prov)}</b>
            <button type="button" data-prov="1">${idx.every(i => ciudadesElegidas.has(i)) ? "Ninguna" : `Todas (${idx.length})`}</button>
          </div>
          ${idx.map(i => `<label><input type="checkbox" value="${i}"${ciudadesElegidas.has(i) ? " checked" : ""}>
            <span>${esc(ciudadesLista[i].ciudad)}</span></label>`).join("")}
        </div>`).join("")
    : `<div class="vacio">Ninguna ciudad coincide con «${esc(texto)}».</div>`;
  actualizarCuenta();
}

function actualizarCuenta() {
  const n = ciudadesElegidas.size, el = $("#b-cuenta");
  el.textContent = n
    ? `${n} de ${ciudadesLista.length}` + (n > maxBarrido ? ` · el máximo por barrido es ${maxBarrido}` : "")
    : `Ninguna elegida · ${ciudadesLista.length} disponibles`;
  el.classList.toggle("error", n > maxBarrido);
}

$("#b-ciudades").addEventListener("change", ev => {
  const cb = ev.target.closest("input[type=checkbox]");
  if (!cb) return;
  if (cb.checked) ciudadesElegidas.add(+cb.value); else ciudadesElegidas.delete(+cb.value);
  actualizarCuenta();
});
$("#b-ciudades").addEventListener("click", ev => {
  const b = ev.target.closest("button[data-prov]");
  if (!b) return;
  const idx = $$("input[type=checkbox]", b.closest(".grupo-prov")).map(c => +c.value);
  const faltan = idx.some(i => !ciudadesElegidas.has(i));
  idx.forEach(i => faltan ? ciudadesElegidas.add(i) : ciudadesElegidas.delete(i));
  pintarCiudades();
});
$("#b-filtro-ciudad").addEventListener("input", pintarCiudades);
$("#b-principales").onclick = () => {
  ciudadesElegidas.clear();
  ciudadesLista.forEach((c, i) => { if (c.principal) ciudadesElegidas.add(i); });
  pintarCiudades();
};
$("#b-visibles").onclick = () => {
  $$("#b-ciudades input[type=checkbox]").forEach(c => ciudadesElegidas.add(+c.value));
  pintarCiudades();
};
$("#b-ninguna").onclick = () => { ciudadesElegidas.clear(); pintarCiudades(); };

const modoVarias = () => $("input[name=b-modo]:checked").value === "varias";

$$("input[name=b-modo]").forEach(r => r.addEventListener("change", () => {
  $("#b-una").hidden = modoVarias();
  $("#b-varias").hidden = !modoVarias();
  analizarBusqueda();
}));
$("#b-rubro").addEventListener("input", marcarRubroActivo);

/* ---------- Analisis en vivo: ¿ya lo buscaste? ¿que conviene probar? ---------- */
let temporizadorAnalisis, pedidoAnalisis = 0;
["#b-rubro", "#b-ciudad", "#b-zona", "#b-limite"].forEach(s =>
  $(s).addEventListener("input", () => { clearTimeout(temporizadorAnalisis); temporizadorAnalisis = setTimeout(analizarBusqueda, 350); }));

async function analizarBusqueda() {
  const cont = $("#b-analisis");
  const rubro = $("#b-rubro").value.trim(), ciudad = $("#b-ciudad").value.trim();
  if (modoVarias() || !rubro || !ciudad) { cont.innerHTML = ""; return; }
  const n = ++pedidoAnalisis;
  const p = new URLSearchParams({ rubro, ciudad, zona: $("#b-zona").value.trim(), limite: $("#b-limite").value });
  try {
    const a = await api("/api/busquedas/analizar?" + p);
    if (n !== pedidoAnalisis) return;               // llego tarde: ya se tipeo otra cosa
    pintarAnalisis(a);
  } catch (e) { cont.innerHTML = ""; }
}

function pintarAnalisis(a) {
  const cont = $("#b-analisis");
  $("#lista-zonas").innerHTML = a.zonas.map(z => `<option value="${esc(z.texto)}">`).join("");
  const donde = esc(a.clave.ciudad) + (a.clave.zona ? ` (${esc(a.clave.zona)})` : "");
  const m = a.misma;
  let caja;
  if (m && a.aviso === "sin_novedad") {
    caja = `<div class="analisis malo"><b>Ya buscaste «${esc(m.rubro)}» en ${donde}</b>
      ${m.veces} ${m.veces === 1 ? "vez" : "veces"}, la última el ${fecha(m.ultima_fecha, false)}, hasta ${m.max_limite} lugares
      (${m.nuevos} negocios nuevos en total).
      <br>Repetirla con ${esc($("#b-limite").value)} trae <b>los mismos negocios</b>: 0 nuevos y Apify la cobra igual.
      <br><span class="sutil">Prueba una variante o una zona de abajo, o sube la cantidad a más de ${m.max_limite}.</span></div>`;
  } else if (m && a.aviso === "ampliar") {
    caja = `<div class="analisis medio"><b>Ya buscaste «${esc(m.rubro)}» en ${donde} hasta ${m.max_limite} lugares.</b>
      Con ${esc($("#b-limite").value)} pueden salir <b>hasta ${a.nuevos_esperados} nuevos</b>: los primeros ${m.max_limite} ya los tienes y se pagan igual.
      <br><span class="sutil">Una variante del rubro o una zona suele rendir más por el mismo costo.</span></div>`;
  } else if (m && a.aviso === "en_curso") {
    caja = `<div class="analisis medio"><b>Esta búsqueda está en marcha.</b> Espera a que termine para ver cuántos nuevos trajo.</div>`;
  } else {
    caja = `<div class="analisis bueno"><b>Búsqueda nueva ✓</b> Nunca buscaste «${esc(a.clave.rubro)}» en ${donde}.
      ${a.busquedas_ciudad ? `<span class="sutil">En ${esc(a.clave.ciudad)} ya tienes ${a.leads_ciudad} negocios de ${a.busquedas_ciudad} ${a.busquedas_ciudad === 1 ? "búsqueda" : "búsquedas"}; los repetidos se descartan solos.</span>` : ""}</div>`;
  }
  const chips = (lista, destino) => lista.map(x =>
    `<button type="button" class="${x.ya_buscado ? "ya" : ""}" data-sugerencia="${destino}" data-valor="${esc(x.texto)}"
      title="${x.ya_buscado ? "Ya la buscaste en esta ciudad" : "Todavía no la buscaste"}">${x.ya_buscado ? "✓ " : ""}${esc(x.texto)}</button>`).join("");
  cont.innerHTML = caja +
    (a.variantes.length ? `<div class="sugerencias"><span>Variantes del rubro</span><div class="chips-rubro">${chips(a.variantes, "rubro")}</div></div>` : "") +
    (a.zonas.length ? `<div class="sugerencias"><span>Zonas de ${esc(a.clave.ciudad)}</span><div class="chips-rubro">${chips(a.zonas, "zona")}</div></div>` : "");
  $$("[data-sugerencia]", cont).forEach(b => b.onclick = () => {
    $(b.dataset.sugerencia === "rubro" ? "#b-rubro" : "#b-zona").value = b.dataset.valor;
    marcarRubroActivo();
    analizarBusqueda();
  });
}

$("#form-buscar").addEventListener("submit", async ev => {
  ev.preventDefault();
  const boton = ev.submitter;
  let ciudades;
  if (modoVarias()) {
    ciudades = [...ciudadesElegidas].sort((a, b) => a - b).map(i => ciudadesLista[i]);
    if (ciudades.length > maxBarrido) {
      avisar(`Elegiste ${ciudades.length} ciudades y el máximo por barrido es ${maxBarrido}.`, "error");
      return;
    }
  } else {
    const nombre = $("#b-ciudad").value.trim();
    const conocida = ciudadesLista.find(c => c.ciudad.toLowerCase() === nombre.toLowerCase());
    ciudades = nombre ? [conocida || { ciudad: nombre, provincia: "" }] : [];
  }
  const datos = {
    rubro: $("#b-rubro").value.trim(), ciudades, zona: modoVarias() ? "" : $("#b-zona").value.trim(),
    limite: +$("#b-limite").value, con_contactos: $("#b-contactos").checked
  };
  await conBoton(boton, async () => {
    const pre = await api("/api/busquedas", { metodo: "POST", datos });
    let msg = `Se van a buscar hasta ${datos.limite} "${datos.rubro}" en ${pre.ciudades} ciudad(es)` +
      (datos.zona ? ` (zona ${datos.zona})` : "") + `.\nCosto estimado en Apify: hasta US$ ${pre.costo_estimado_usd.toFixed(2)}.`;
    const sinNovedad = pre.repetidas.filter(r => r.aviso === "sin_novedad");
    const ampliar = pre.repetidas.filter(r => r.aviso === "ampliar");
    if (sinNovedad.length) {
      msg += `\n\n⛔ YA LA BUSCASTE en: ${sinNovedad.map(r => `${r.ciudad} (${fecha(r.ultima_fecha, false)}, hasta ${r.max_limite})`).join(", ")}.` +
        `\nVa a traer los MISMOS negocios: 0 nuevos, y se paga igual.`;
    }
    if (ampliar.length) {
      msg += `\n\n⚠ Ya buscada en: ${ampliar.map(r => `${r.ciudad} (hasta ${r.max_limite}; ahora hasta ${r.nuevos_esperados} nuevos)`).join(", ")}.`;
    }
    if (!confirm(msg + "\n\n¿Buscar igual?".replace(" igual", pre.repetidas.length ? " igual" : ""))) return;
    await api("/api/busquedas", { metodo: "POST", datos: { ...datos, confirmar: true } });
    avisar("Búsqueda en marcha. Los resultados aparecen solos en unos minutos.", "ok");
    cargarBusquedas();
    cargarHistorial();
    analizarBusqueda();
  });
});

const ESTADO_BUSQUEDA = { pendiente: "En cola", corriendo: "Buscando…", importando: "Importando…", importada: "Lista", fallida: "Falló" };

/* ---------- En curso y recientes (con reimportar) ---------- */
async function cargarBusquedas() {
  clearTimeout(refrescoBusquedas);
  const cont = $("#busquedas");
  try {
    const r = await api("/api/busquedas");
    const recientes = r.busquedas.slice(0, 8);
    cont.innerHTML = recientes.map(b => `
      <div class="fila estatica">
        <div class="izq">
          <div class="n">${esc(b.rubro)} · ${esc(b.ciudad)}${b.zona ? ` · ${esc(b.zona)}` : ""}</div>
          <div class="m">${fecha(b.creado)} · ${esc(b.usuario || "")}
            ${b.estado === "importada" ? ` · ${b.nuevos} nuevos, ${b.repetidos} ya estaban${b.descartados ? `, ${b.descartados} descartados` : ""}${b.costo_usd != null ? ` · US$ ${Number(b.costo_usd).toFixed(2)}` : ""}` : ""}
          </div>
          ${b.error ? `<div class="m error" title="${esc(b.error)}">${esc(b.error)}</div>` : ""}
        </div>
        <div class="der">
          <span class="chip ${b.estado === "fallida" ? "e-no-interesa" : b.estado === "importada" ? "e-cliente" : "e-contactado"}">${esc(ESTADO_BUSQUEDA[b.estado] || b.estado)}</span>
          ${b.estado === "importada" ? `<button class="btn chico fantasma" data-reimportar="${b.id}" title="Volver a procesar sin gastar créditos">↻</button>` : ""}
        </div>
      </div>`).join("") || `<div class="vacio">Todavía no has hecho búsquedas.</div>`;
    $$("[data-reimportar]", cont).forEach(b => b.onclick = () => conBoton(b, async () => {
      const c = await api(`/api/busquedas/${b.dataset.reimportar}/reimportar`, { metodo: "POST", datos: {} });
      avisar(`Reimportada: ${c.nuevos} nuevos, ${c.repetidos} ya estaban.`, "ok");
      cargarBusquedas();
    }));
    const activas = r.busquedas.some(b => ["pendiente", "corriendo", "importando"].includes(b.estado));
    if (activas && vistaActual === "buscar") refrescoBusquedas = setTimeout(() => { cargarBusquedas(); cargarHistorial(); }, 8000);
  } catch (e) { cont.innerHTML = `<div class="vacio error">${esc(e.message)}</div>`; }
}

/* ---------- Historial agrupado: que ya se busco y cuanto rinde ---------- */
const ESTADO_HISTORIAL = {
  rinde: ["Rinde", "e-cliente", "La última búsqueda trajo 40% o más de negocios nuevos"],
  bajando: ["Bajando", "e-contactado", "La última trajo entre 15% y 40% de nuevos"],
  agotada: ["Agotada", "e-no-interesa", "La última trajo menos de 15% de nuevos: prueba variantes o zonas"],
  en_curso: ["En curso", "e-nuevo", "Todavía está buscando"],
  fallida: ["Falló", "e-no-interesa", "La última corrida falló"],
  sin_resultados: ["Sin resultados", "", "Google Maps no devolvió lugares"],
};
let historialDatos = [];

async function cargarHistorial() {
  try {
    historialDatos = (await api("/api/busquedas/historial")).historial;
    pintarHistorial();
  } catch (e) { $("#historial").innerHTML = `<div class="vacio error">${esc(e.message)}</div>`; }
}

function pintarHistorial() {
  const q = ($("#h-filtro").value || "").trim().toLowerCase();
  const filas = historialDatos.filter(g => !q || `${g.rubro} ${g.ciudad} ${g.zona}`.toLowerCase().includes(q));
  $("#h-total").textContent = historialDatos.length
    ? `${historialDatos.length} ${historialDatos.length === 1 ? "combinación" : "combinaciones"} · US$ ${historialDatos.reduce((s, g) => s + g.costo_usd, 0).toFixed(2)} en total` : "";
  $("#historial").innerHTML = filas.map((g, i) => {
    const [txt, clase, ayuda] = ESTADO_HISTORIAL[g.estado] || [g.estado, "", ""];
    return `<div class="fila estatica">
      <div class="izq">
        <div class="n">${esc(g.rubro)} · ${esc(g.ciudad)}${g.zona ? ` · ${esc(g.zona)}` : ""}</div>
        <div class="m">${g.veces} ${g.veces === 1 ? "vez" : "veces"} · última ${fecha(g.ultima_fecha, false)} · hasta ${g.max_limite}
          · ${g.nuevos} nuevos de ${g.encontrados}${g.rendimiento_ultima != null ? ` · última rindió ${g.rendimiento_ultima}%` : ""} · US$ ${g.costo_usd.toFixed(2)}</div>
      </div>
      <div class="der">
        <span class="chip ${clase}" title="${esc(ayuda)}">${esc(txt)}</span>
        <button class="btn chico" data-hist="${i}" title="Cargar esta búsqueda arriba para ver variantes y zonas">Variantes</button>
      </div>
    </div>`;
  }).join("") || `<div class="vacio">${historialDatos.length ? "Nada coincide con el filtro." : "Todavía no has hecho búsquedas."}</div>`;
  $$("[data-hist]", $("#historial")).forEach(b => b.onclick = () => {
    const g = filas[+b.dataset.hist];
    $$("input[name=b-modo]")[0].checked = true;
    $("#b-una").hidden = false; $("#b-varias").hidden = true;
    $("#b-rubro").value = g.rubro; $("#b-ciudad").value = g.ciudad; $("#b-zona").value = g.zona || "";
    $("#b-limite").value = g.ultima_limite || 40;
    $("#form-buscar").scrollIntoView({ behavior: "smooth", block: "start" });
    analizarBusqueda();
  });
}
$("#h-filtro").addEventListener("input", () => pintarHistorial());

/* ====================================================================
   METRICAS
   ==================================================================== */
const compacto = n => new Intl.NumberFormat("es-ES", { notation: "compact", maximumFractionDigits: 1 }).format(n);
const eurosCompacto = n => compacto(n) + " €";

/** Tope "redondo" para el eje: 7 -> 8, 143 -> 150, 12800 -> 15000. */
function topeRedondo(max) {
  if (max <= 0) return 4;
  const pot = 10 ** Math.floor(Math.log10(max));
  const paso = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find(p => p * pot >= max);
  return paso * pot;
}

/**
 * Grafico de columnas (una sola serie) en SVG, con tooltip por columna,
 * foco con teclado y el valor maximo rotulado. `datos`: [{etiqueta, valor, detalle}].
 */
function graficoColumnas(cont, datos, { formato = v => String(v), etiquetaCada = 1 } = {}) {
  const W = 560, H = 210, izq = 44, der = 8, arr = 18, abj = 26;
  const anchoUtil = W - izq - der, altoUtil = H - arr - abj;
  const tope = topeRedondo(Math.max(0, ...datos.map(d => d.valor)));
  const banda = anchoUtil / Math.max(1, datos.length);
  const ancho = Math.min(24, banda * .62);
  const y = v => arr + altoUtil * (1 - v / tope);
  const ticks = [0, .5, 1].map(f => tope * f);
  const iMax = datos.reduce((mi, d, i) => (d.valor > datos[mi].valor ? i : mi), 0);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(cont.dataset.titulo || "")}">`;
  ticks.forEach(t => {
    svg += `<line class="grilla" x1="${izq}" x2="${W - der}" y1="${y(t)}" y2="${y(t)}"/>`;
    svg += `<text class="eje" x="${izq - 8}" y="${y(t) + 3.5}" text-anchor="end">${esc(formato(t, true))}</text>`;
  });
  datos.forEach((d, i) => {
    const cx = izq + banda * i + banda / 2, x0 = cx - ancho / 2;
    const alto = Math.max(0, y(0) - y(d.valor)), r = Math.min(4, alto, ancho / 2);
    if (alto > 0) {
      // extremo de datos redondeado (4px), base recta
      svg += `<path class="marca-dato" data-i="${i}" d="M${x0},${y(0)} V${y(d.valor) + r} Q${x0},${y(d.valor)} ${x0 + r},${y(d.valor)} H${x0 + ancho - r} Q${x0 + ancho},${y(d.valor)} ${x0 + ancho},${y(d.valor) + r} V${y(0)} Z"/>`;
    }
    if (i === iMax && d.valor > 0) {
      svg += `<text class="valor" x="${cx}" y="${y(d.valor) - 6}" text-anchor="middle">${esc(formato(d.valor))}</text>`;
    }
    if (i % etiquetaCada === 0 || i === datos.length - 1) {
      svg += `<text class="eje" x="${cx}" y="${H - 8}" text-anchor="middle">${esc(d.etiqueta)}</text>`;
    }
    svg += `<rect class="hit" data-i="${i}" tabindex="0" x="${izq + banda * i}" y="${arr}" width="${banda}" height="${altoUtil}"/>`;
  });
  svg += `<line class="grilla" x1="${izq}" x2="${W - der}" y1="${y(0)}" y2="${y(0)}" style="stroke:var(--borde-fuerte)"/></svg>`;
  cont.innerHTML = svg;
  const tip = document.createElement("div");
  tip.className = "grafico-tip";
  tip.style.opacity = "0";
  cont.appendChild(tip);
  const svgEl = cont.querySelector("svg");
  const mostrar = hit => {
    const i = +hit.dataset.i, d = datos[i];
    cont.querySelectorAll(".marca-dato").forEach(m => m.classList.toggle("en-foco", +m.dataset.i === i));
    tip.replaceChildren();
    const b = document.createElement("b"); b.textContent = formato(d.valor);
    const s = document.createElement("span"); s.textContent = d.detalle || d.etiqueta;
    tip.append(b, s);
    const escala = svgEl.getBoundingClientRect().width / W;
    const cx = (izq + banda * i + banda / 2) * escala;
    tip.style.left = Math.min(Math.max(cx, 60), svgEl.getBoundingClientRect().width - 60) + "px";
    tip.style.top = (y(d.valor) * escala) + "px";
    tip.style.opacity = "1";
  };
  const ocultar = () => { tip.style.opacity = "0"; cont.querySelectorAll(".marca-dato").forEach(m => m.classList.remove("en-foco")); };
  cont.querySelectorAll(".hit").forEach(h => {
    h.addEventListener("pointerenter", () => mostrar(h));
    h.addEventListener("focus", () => mostrar(h));
    h.addEventListener("pointerleave", ocultar);
    h.addEventListener("blur", ocultar);
  });
}

/** Los ultimos n dias (hora argentina), del mas viejo al de hoy, en AAAA-MM-DD. */
function ultimosDias(n) {
  const hoy = new Date();
  return Array.from({ length: n }, (_, k) => {
    const d = new Date(hoy.getTime() - (n - 1 - k) * 86400000);
    return d.toLocaleDateString("en-CA", { timeZone: TZ });
  });
}
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

async function cargarMetricas() {
  const cont = $("#metricas");
  try {
    const m = await api("/api/metricas");
    const max = Math.max(1, ...Object.values(m.por_estado));
    const tabla = (titulo, filas) => `
      <h3>${titulo}</h3>
      <div class="tabla-scroll"><table>
        <thead><tr><th></th><th class="num">Leads</th><th class="num">Contactados</th><th class="num">Respuestas</th><th class="num">Clientes</th><th class="num">% resp.</th><th class="num">Cobrado</th></tr></thead>
        <tbody>${filas.map(f => `<tr><td>${esc(f.clave)}</td><td class="num">${f.leads}</td><td class="num">${f.contactados}</td><td class="num">${f.respuestas}</td><td class="num">${f.clientes}</td><td class="num">${f.contactados ? Math.round(100 * f.respuestas / f.contactados) + "%" : "–"}</td><td class="num">${f.cobrado ? euros(f.cobrado) : "–"}</td></tr>`).join("") || `<tr><td colspan="7" class="sutil">Sin datos todavía</td></tr>`}</tbody>
      </table></div>`;

    // Envios: los 14 dias completos (los que no tuvieron envios van en 0)
    const porDia = Object.fromEntries(m.por_dia.map(d => [d.dia, d.enviados]));
    const dias = ultimosDias(14).map(dia => ({
      etiqueta: dia.slice(8, 10) + "/" + dia.slice(5, 7), valor: porDia[dia] || 0,
      detalle: fecha(dia, false),
    }));
    const meses = [...m.cobrado_por_mes].reverse().map(x => ({
      etiqueta: MESES[+x.mes.slice(5, 7) - 1] + " " + x.mes.slice(2, 4), valor: x.cobrado,
      detalle: `${x.cobros} ${x.cobros === 1 ? "cobro" : "cobros"} · ${MESES[+x.mes.slice(5, 7) - 1]} ${x.mes.slice(0, 4)}`,
    }));

    cont.innerHTML = `
      <div class="encabezado" style="margin-top:0"><h2>Cómo va el negocio</h2><span class="sutil">Actualizado ahora</span></div>
      <div class="kpis">
        <div class="kpi destacado entra" style="--i:0"><div class="l">Cobrado en total</div><div class="v" data-valor="${m.cobrado}" data-formato="euros">${euros(m.cobrado)}</div>
          <div class="sutil">${m.cobros} ${m.cobros === 1 ? "cobro" : "cobros"} · ticket medio ${euros(m.ticket_promedio)}</div></div>
        <div class="kpi entra" style="--i:1"><div class="l">Clientes</div><div class="v" data-valor="${m.clientes}">${m.clientes}</div></div>
        <div class="kpi entra" style="--i:2"><div class="l">Tasa de respuesta</div><div class="v">${m.tasa_respuesta}%</div><div class="sutil">${m.respuestas} de ${m.contactados}</div></div>
      </div>
      <div class="kpis">
        <div class="kpi entra" style="--i:3"><div class="l">Leads</div><div class="v" data-valor="${m.total}">${m.total}</div></div>
        <div class="kpi entra" style="--i:4"><div class="l">Presupuestado</div><div class="v" data-valor="${m.presupuestado}" data-formato="euros">${euros(m.presupuestado)}</div><div class="sutil">${m.presupuestos} ${m.presupuestos === 1 ? "presupuesto" : "presupuestos"}</div></div>
        <div class="kpi entra" style="--i:5"><div class="l">Presupuestos cobrados</div><div class="v">${m.cierre_presupuestos != null ? m.cierre_presupuestos + "%" : "–"}</div></div>
        <div class="kpi entra" style="--i:6"><div class="l">Gasto en Apify</div><div class="v">US$ ${m.costo_apify_usd.toFixed(2)}</div><div class="sutil">${m.costo_por_cliente_usd != null ? "US$ " + m.costo_por_cliente_usd.toFixed(2) + " por cliente" : "aún sin clientes"}</div></div>
      </div>

      <div class="graficos">
        <div class="grafico-caja">
          <h3>Contactos registrados</h3><div class="sub">Primeros contactos · últimos 14 días</div>
          <div class="grafico" id="g-dias" data-titulo="Contactos registrados en los últimos 14 días"></div>
          <details><summary class="sutil">Ver como tabla</summary><table><tbody>${dias.map(d => `<tr><td>${esc(d.detalle)}</td><td class="num">${d.valor}</td></tr>`).join("")}</tbody></table></details>
        </div>
        <div class="grafico-caja">
          <h3>Cobrado por mes</h3><div class="sub">En euros</div>
          ${meses.length ? `<div class="grafico" id="g-meses" data-titulo="Cobrado por mes"></div>
          <details><summary class="sutil">Ver como tabla</summary><table><tbody>${meses.map(x => `<tr><td>${esc(x.detalle)}</td><td class="num">${euros(x.valor)}</td></tr>`).join("")}</tbody></table></details>`
          : `<div class="vacio" style="padding:1.6rem .8rem">Cuando registres cobros en la ficha de un cliente, aquí verás la evolución mes a mes.</div>`}
        </div>
      </div>

      <div class="grafico-caja embudo">
        <h3>Embudo</h3><div class="sub">Dónde está cada negocio hoy</div>
        ${ESTADOS.map((e, i) => `<div class="paso"><span>${esc(ETIQUETA[e])}</span>
          <span class="pista"><span class="relleno" style="width:${(100 * m.por_estado[e] / max).toFixed(1)}%;animation-delay:${i * 60}ms"></span></span>
          <span class="val">${m.por_estado[e]}</span></div>`).join("")}
      </div>
      ${tabla("Por rubro", m.por_rubro)}
      ${tabla("Por ciudad", m.por_ciudad)}`;

    $$(".v[data-valor]", cont).forEach(contarHasta);
    graficoColumnas($("#g-dias"), dias, { etiquetaCada: 2, formato: (v, eje) => eje ? compacto(v) : String(v) });
    const gMeses = $("#g-meses");
    if (gMeses) graficoColumnas(gMeses, meses, { formato: (v, eje) => eje ? eurosCompacto(v) : euros(v) });
  } catch (e) { cont.innerHTML = `<div class="vacio error">${esc(e.message)}</div>`; }
}

/* ====================================================================
   AJUSTES
   ==================================================================== */
const AYUDA_CLAVE = {
  apify: `<ol>
      <li>Entra en <a href="https://console.apify.com/sign-up" target="_blank" rel="noopener">console.apify.com</a> y crea una cuenta (puede ser con Google).</li>
      <li>Ve a <b>Settings → API &amp; Integrations</b>
        (<a href="https://console.apify.com/settings/integrations" target="_blank" rel="noopener">enlace directo</a>).</li>
      <li>En <b>Personal API tokens</b>, copia el token (empieza por <code>apify_api_</code>).</li>
      <li>Pégalo aquí abajo y toca <b>Guardar y probar</b>.</li>
    </ol>
    <p class="sutil">El plan gratis trae un poco de crédito al mes para probar. Para buscar en serio conviene un plan de pago
      (cada búsqueda de 40 negocios cuesta unos 0,16 US$).</p>`,
  anthropic: `<ol>
      <li>Entra en <a href="https://platform.claude.com" target="_blank" rel="noopener">platform.claude.com</a> (la consola de Anthropic) y crea una cuenta.</li>
      <li>Ve a <b>Settings → Billing</b> y toca <b>Buy credits</b>. Con 5 US$ hay para muchísimas búsquedas.</li>
      <li>Ve a <b>Settings → API keys</b>, toca <b>Create key</b>, ponle un nombre (por ejemplo "Radar de Marcas") y copia la clave
        (empieza por <code>sk-ant-</code>). Solo se muestra una vez.</li>
      <li>Pégala aquí abajo y toca <b>Guardar y probar</b>.</li>
    </ol>`,
};

function tarjetaClave(sv, k) {
  const estado = k.ilegible
    ? `<span class="chip e-no-interesa">hay que volver a cargarla</span>`
    : k.configurada
      ? `<span class="chip e-cliente">conectada</span> <span class="sutil">termina en …${esc(k.termina_en)}${k.origen === "servidor" ? " · cargada en el servidor" : ""}</span>`
      : `<span class="chip e-no-interesa">falta</span>`;
  return `
    <div class="clave-servicio">
      <h3>${esc(k.nombre)} ${estado}</h3>
      <details ${k.configurada ? "" : "open"}><summary class="sutil">Cómo consigo esta clave</summary>${AYUDA_CLAVE[sv]}</details>
      <div class="fila-clave">
        <input id="k-${sv}" type="password" autocomplete="off" spellcheck="false"
          placeholder="${k.configurada ? "Pega una clave nueva para reemplazarla" : "Pega aquí tu clave"}" aria-label="Clave de ${esc(k.nombre)}">
        <button class="btn primario chico" data-guardar-clave="${sv}">Guardar y probar</button>
        ${k.origen === "app" ? `<button class="btn chico fantasma" data-quitar-clave="${sv}">Quitar</button>` : ""}
      </div>
    </div>`;
}

async function cargarAjustes() {
  const cont = $("#ajustes");
  try {
    const [c, est, cl] = await Promise.all([api("/api/config"), api("/api/estado"), api("/api/claves")]);
    const cfg = c.config;
    cont.innerHTML = `
      <div class="panel aviso-legal">
        <h2>Cómo se usa esta app</h2>
        <p>La app busca negocios en Google Maps, los ordena por prioridad y guarda el seguimiento.
          <b>No envía mensajes</b>: en España la LSSI (art. 21) prohíbe enviar publicidad por email,
          WhatsApp u otros medios electrónicos sin consentimiento previo, también entre empresas.
          El contacto se hace por fuera (llamada, visita, carta…) y aquí solo se registra.</p>
      </div>

      <div class="panel">
        <h2>General</h2>
        <label>Ciudad propia <input id="c-ciudad_default" value="${esc(cfg.ciudad_default)}"></label>
        <p class="sutil">La ciudad que aparece por defecto en el buscador.</p>
        <button class="btn primario" id="guardar-config">Guardar</button>
      </div>

      <div class="panel" id="conexiones">
        <h2>Conexiones</h2>
        <p class="sutil">La app usa dos servicios con <b>tus propias cuentas</b>: Apify (busca los negocios en Google Maps)
          y Anthropic (la IA que los ordena). Pega aquí tus claves: se prueban al guardarlas, se guardan cifradas
          y nadie puede volver a verlas, ni siquiera desde esta pantalla.</p>
        ${["apify", "anthropic"].map(sv => tarjetaClave(sv, cl.claves[sv])).join("")}
        ${est.ia_pendientes ? `<p class="sutil">${est.ia_pendientes} ${est.ia_pendientes === 1 ? "negocio esperando" : "negocios esperando"} que la IA los clasifique.</p>` : ""}
      </div>

      <div class="panel">
        <h2>Usuarios</h2>
        <ul>${c.usuarios.map(u => `<li>${esc(u.nombre)} — ${esc(u.email)}${u.activo ? "" : " (inactivo)"}</li>`).join("")}</ul>
        <details><summary>Añadir usuario</summary>
          <label>Nombre <input id="u-nombre"></label>
          <label>Email <input id="u-email" type="email"></label>
          <label>Contraseña (mínimo 10 caracteres) <input id="u-clave" type="password" autocomplete="new-password"></label>
          <button class="btn primario chico" id="crear-usuario">Crear</button>
        </details>
        <details><summary>Cambiar mi contraseña</summary>
          <label>Actual <input id="u-actual" type="password" autocomplete="current-password"></label>
          <label>Nueva (mínimo 10) <input id="u-nueva" type="password" autocomplete="new-password"></label>
          <button class="btn primario chico" id="cambiar-clave">Cambiar</button>
        </details>
      </div>

      <div class="panel">
        <h2>Exportar</h2>
        <div class="fila-acciones">
          <a class="btn chico" href="/api/exportar.csv">Planilla (CSV para Excel)</a>
          <a class="btn chico" href="/api/exportar.json">Backup completo (JSON)</a>
        </div>
      </div>`;

    $("#guardar-config").onclick = ev => conBoton(ev.target, async () => {
      const datos = {};
      ["ciudad_default"].forEach(k => datos[k] = $("#c-" + k).value);
      await api("/api/config", { metodo: "POST", datos });
      avisar("Guardado", "ok");
    });
    $$("[data-guardar-clave]").forEach(b => b.onclick = () => conBoton(b, async () => {
      const sv = b.dataset.guardarClave, input = $("#k-" + sv);
      if (!input.value.trim()) { avisar("Pega la clave primero.", "error"); return; }
      b.textContent = "Probando…";
      try {
        const r = await api("/api/claves/" + sv, { metodo: "POST", datos: { clave: input.value } });
        input.value = "";
        avisar(r.ok + " Clave guardada.", "ok");
        cargarAjustes();
      } finally { b.textContent = "Guardar y probar"; }
    }));
    $$("[data-quitar-clave]").forEach(b => b.onclick = () => {
      if (!confirm("¿Quitar esta clave? Las búsquedas o la IA dejarán de funcionar hasta que cargues otra.")) return;
      conBoton(b, async () => {
        await api("/api/claves/" + b.dataset.quitarClave, { metodo: "DELETE", datos: {} });
        avisar("Clave quitada", "ok");
        cargarAjustes();
      });
    });
    $("#crear-usuario").onclick = ev => conBoton(ev.target, async () => {
      await api("/api/usuarios", { metodo: "POST", datos: { nombre: $("#u-nombre").value, email: $("#u-email").value, clave: $("#u-clave").value } });
      avisar("Usuario creado", "ok");
      cargarAjustes();
    });
    $("#cambiar-clave").onclick = ev => conBoton(ev.target, async () => {
      await api("/api/usuarios/clave", { metodo: "POST", datos: { actual: $("#u-actual").value, nueva: $("#u-nueva").value } });
      avisar("Contraseña cambiada", "ok");
      $("#u-actual").value = $("#u-nueva").value = "";
    });
  } catch (e) { cont.innerHTML = `<div class="vacio error">${esc(e.message)}</div>`; }
}

/* ====================================================================
   Inicio
   ==================================================================== */
(() => {
  let inicial = "hoy";
  try { inicial = localStorage.getItem("vista") || "hoy"; } catch (e) { /* modo privado */ }
  mostrarVista(vistas[inicial] ? inicial : "hoy");
  refrescarEstado();
  // Al volver a la pestaña (ej. después de llamar) se refresca
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { refrescarEstado(); if (vistaActual === "hoy" && $("#panel").hidden) cargarHoy(); }
  });
})();
