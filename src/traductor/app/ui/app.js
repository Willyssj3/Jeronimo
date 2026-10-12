"use strict";

/* Jerónimo (Traductor de Libros) — interfaz. Habla con Python por pywebview (ventana)
   o por HTTP local con token (modo navegador). */

const params = new URLSearchParams(location.search);
const TOKEN = params.get("token");
const state = { info: null, summary: null, view: "resumen", chapter: null, task: null, polling: false };

/* ---------------------------------------------------------------- RPC */

function bridgeReady() {
  if (TOKEN) return Promise.resolve();
  if (window.pywebview && window.pywebview.api) return Promise.resolve();
  return new Promise((resolve) => window.addEventListener("pywebviewready", resolve, { once: true }));
}

async function rpc(name, ...args) {
  await bridgeReady();
  let res;
  if (TOKEN) {
    const r = await fetch("/api/" + name, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-TDL-Token": TOKEN },
      body: JSON.stringify({ args }),
    });
    res = await r.json();
  } else {
    res = await window.pywebview.api.call(name, args);
  }
  if (!res || !res.ok) throw new Error((res && res.error) || "Error desconocido");
  return res.resultado;
}

/* ---------------------------------------------------------------- utilidades de DOM */

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/* Como Element.append, pero ignora null/false (append los convertiría en el texto «null»). */
function put(parent, ...children) {
  for (const c of children.flat()) {
    if (c !== null && c !== undefined && c !== false) parent.append(c);
  }
}

const $ = (sel, root = document) => root.querySelector(sel);
const view = () => $("#view");

/* Iconos de línea propios (24×24, trazo con currentColor). */
const ICONS = {
  libro: '<path d="M12 6.6C10 5.1 7 4.6 3.5 5v13.2c3.5-.4 6.5.1 8.5 1.6 2-1.5 5-2 8.5-1.6V5C17 4.6 14 5.1 12 6.6z"/><path d="M12 6.6v13.2"/>',
  carpeta: '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4.2l2 2.2H19a1.5 1.5 0 0 1 1.5 1.5v8.3A1.5 1.5 0 0 1 19 19.5H5A1.5 1.5 0 0 1 3.5 18z"/>',
  pdf: '<path d="M6.5 3.5h7.5l4.5 4.5v11A1.5 1.5 0 0 1 17 20.5H6.5A1.5 1.5 0 0 1 5 19V5a1.5 1.5 0 0 1 1.5-1.5z"/><path d="M14 3.5V8h4.5"/><path d="M8.5 12.5h7M8.5 16h5"/>',
  pluma: '<path d="M20 3.8C13.2 4.2 8.6 8.9 7.6 16.4c6.8-.9 11.4-5.4 12.4-12.6z"/><path d="M4 20l9-9"/><path d="M10.4 13.6h4.2"/>',
};
const icon = (name) => h("span", { class: "card-icon", html: `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>` });

function pageHead(eyebrow, title, lead) {
  return h("header", { class: "page-head" },
    eyebrow ? h("div", { class: "eyebrow" }, eyebrow) : null,
    h("h1", {}, title),
    lead ? h("p", { class: "lead" }, lead) : null);
}

const cardHead = (iconName, title) => h("div", { class: "card-head" }, icon(iconName), h("h2", {}, title));
const fmt = (n) => Number(n || 0).toLocaleString("es-ES");
const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0);

function toast(msg, isError = false) {
  const el = h("div", { class: isError ? "error" : "" }, msg);
  $("#toast").appendChild(el);
  setTimeout(() => el.remove(), isError ? 9000 : 4500);
}

async function guard(fn) {
  try {
    return await fn();
  } catch (e) {
    toast(e.message, true);
    return undefined;
  }
}

function modal(content, { ok = "Aceptar", cancel = "Cancelar", okClass = "" } = {}) {
  return new Promise((resolve) => {
    const body = $("#modal-body");
    body.textContent = "";
    body.appendChild(typeof content === "string" ? h("p", {}, content) : content);
    const okBtn = $("#modal-ok");
    const cancelBtn = $("#modal-cancel");
    okBtn.textContent = ok;
    okBtn.className = okClass;
    cancelBtn.textContent = cancel || "";
    cancelBtn.hidden = !cancel;
    $("#modal-backdrop").hidden = false;
    const close = (v) => {
      $("#modal-backdrop").hidden = true;
      okBtn.onclick = cancelBtn.onclick = null;
      resolve(v);
    };
    okBtn.onclick = () => close(true);
    cancelBtn.onclick = () => close(false);
    okBtn.focus();
  });
}

function pathField(id, placeholder, kind = "epub") {
  const input = h("input", { id, type: "text", placeholder, spellcheck: "false" });
  const browse = h("button", {
    class: "ghost", type: "button",
    onclick: async () => {
      const p = await guard(() => rpc("choose_path", kind, ""));
      if (p) input.value = p;
    },
  }, "Examinar…");
  if (state.info && state.info.modo !== "escritorio") browse.hidden = true;
  return h("div", { class: "path" }, input, browse);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const ta = h("textarea", { style: "position:fixed;left:-9999px" });
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

/* ---------------------------------------------------------------- texto marcado */

const TOKEN_RE = /<(\/?)g(\d+)\s*>|<x(\d+)\s*\/?\s*>|<\/x\d+\s*>/g;
const CHIP = { img: ["▣", "imagen"], image: ["▣", "imagen"], svg: ["▣", "imagen"], br: ["↵", "salto de línea"],
  a: ["※", "enlace o nota"], sup: ["※", "llamada a nota"], hr: ["—", "línea"] };

function decodeEntities(s) {
  return s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d)).replace(/&amp;/g, "&");
}

function escapeText(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function markedToDom(container, text, marks) {
  container.textContent = "";
  if (!text) return;
  const stack = [container];
  let last = 0;
  let m;
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(text))) {
    if (m.index > last) stack[stack.length - 1].appendChild(document.createTextNode(decodeEntities(text.slice(last, m.index))));
    last = TOKEN_RE.lastIndex;
    if (m[2]) {
      if (m[1]) {
        if (stack.length > 1) stack.pop();
      } else {
        const tag = (marks && marks[m[2]]) || "span";
        const span = h("span", { class: "mk g t-" + tag, "data-g": m[2], title: "formato: " + tag });
        stack[stack.length - 1].appendChild(span);
        stack.push(span);
      }
    } else if (m[3]) {
      const tag = (marks && marks[m[3]]) || "";
      const [sym, label] = CHIP[tag] || ["◆", "elemento fijo"];
      stack[stack.length - 1].appendChild(h("span", { class: "mk x", "data-x": m[3], contenteditable: "false", title: label }, sym));
    }
  }
  if (last < text.length) stack[stack.length - 1].appendChild(document.createTextNode(decodeEntities(text.slice(last))));
}

function domToMarked(node) {
  let out = "";
  for (const child of node.childNodes) {
    if (child.nodeType === Node.TEXT_NODE) out += escapeText(child.nodeValue);
    else if (child.nodeType === Node.ELEMENT_NODE) {
      if (child.dataset.x) out += `<x${child.dataset.x}/>`;
      else if (child.dataset.g) out += `<g${child.dataset.g}>${domToMarked(child)}</g${child.dataset.g}>`;
      else if (child.tagName === "BR") out += " ";
      else out += " " + domToMarked(child) + " ";
    }
  }
  return out;
}

const normalizeMarked = (s) => s.replace(/[ \t\n\r\f]+/g, " ").trim();

/* ---------------------------------------------------------------- tareas en segundo plano */

async function pollTask() {
  if (state.polling) return;
  state.polling = true;
  try {
    for (;;) {
      const t = await rpc("task_status");
      state.task = t;
      renderTaskbar(t);
      if (t.activa) {
        state.taskPending = true;
        if (state.view === "revision" && state.chapter !== null && t.capitulo === state.chapter) {
          refreshChapterQuietly();
        }
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
      if (state.taskPending) {
        // La tarea pudo terminar antes de la primera consulta: igual se refresca.
        state.taskPending = false;
        state.lastTask = t;
        if (t.error) toast(t.error, true);
        else toast(`${t.nombre}: terminada.`);
        await refreshSummary();
        render();
      }
      break;
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    state.polling = false;
  }
}

function renderTaskbar(t) {
  const bar = $("#taskbar");
  if (!t || (!t.activa && !t.nombre)) {
    bar.hidden = true;
    return;
  }
  bar.hidden = !t.activa;
  $("#task-name").textContent = t.nombre + ":";
  $("#task-message").textContent = t.mensaje || "";
  $("#task-bar").style.width = Math.round((t.progreso || 0) * 100) + "%";
}

async function startTask(fn) {
  const t = await guard(fn);
  if (t) {
    state.task = t;
    state.taskPending = true;
    renderTaskbar(t);
    pollTask();
  }
}

$("#btn-task-cancel").onclick = () => guard(() => rpc("cancel_task"));
$("#btn-task-log").onclick = async () => {
  const t = await guard(() => rpc("task_status"));
  if (!t) return;
  await modal(h("div", {}, h("h3", {}, "Registro de la tarea"), h("pre", { class: "log" }, (t.registro || []).join("\n") || "(vacío)")),
    { ok: "Cerrar", cancel: null });
};

/* ---------------------------------------------------------------- navegación */

async function refreshSummary() {
  if (!state.info || !state.info.proyecto_abierto) return;
  state.summary = await rpc("project_summary");
}

$("#btn-quit").onclick = async () => {
  if (!(await modal("¿Cerrar Jerónimo? El progreso ya está guardado.", { ok: "Salir" }))) return;
  if (await guard(() => rpc("quit_app"))) {
    document.body.innerHTML = "<p style='padding:40px;font-size:1.2rem'>Puedes cerrar esta pestaña.</p>";
  }
};

function setChrome() {
  const open = !!state.summary;
  $("#btn-quit").hidden = !(state.info && state.info.modo === "navegador");
  $("#sidebar").hidden = !open;
  $("#btn-close-project").hidden = !open;
  $("#project-name").textContent = open ? state.summary.titulo || state.summary.nombre : "";
  if (open) {
    const prog = state.summary.progreso;
    $("#side-pct").textContent = `${pct(prog.hechos, prog.total)} %`;
    $("#side-bar").style.width = `${pct(prog.hechos, prog.total)}%`;
  }
  for (const a of document.querySelectorAll("#sidebar a")) {
    a.classList.toggle("active", a.dataset.view === state.view || (state.view === "revision" && a.dataset.view === "capitulos"));
  }
}

function go(viewName, chapter = null) {
  state.view = viewName;
  state.chapter = chapter;
  location.hash = viewName === "revision" ? `revision/${chapter}` : viewName;
}

window.addEventListener("hashchange", () => {
  const [v, arg] = location.hash.slice(1).split("/");
  if (v) {
    state.view = v;
    state.chapter = arg !== undefined ? Number(arg) : state.chapter;
  }
  render();
});

$("#btn-close-project").onclick = async () => {
  if (await guard(() => rpc("close_project"))) {
    state.summary = null;
    state.info = await rpc("app_info");
    go("inicio");
  }
};

async function render() {
  setChrome();
  const v = view();
  v.textContent = "";
  $("#main").scrollTop = 0;
  if (!state.summary) return renderHome(v);
  const views = { resumen: renderSummary, referencias: renderReferences, ia: renderAI, conocimiento: renderKnowledge,
    capitulos: renderChapters, revision: renderReview, exportar: renderExport };
  return (views[state.view] || renderSummary)(v);
}

/* ---------------------------------------------------------------- inicio */

function renderHome(v) {
  const info = state.info;
  const langSelect = h("select", { id: "new-lang" }, Object.entries(info.idiomas).filter(([k]) => k !== "en").map(([k, name]) =>
    h("option", { value: k, selected: k === "es" }, `${name} (${k})`)));
  const variant = h("select", { id: "new-variant" },
    h("option", { value: "auto" }, "Igual que las traducciones oficiales"),
    h("option", { value: "es-ES" }, "Español de España"),
    h("option", { value: "es-419" }, "Español neutro latinoamericano"),
    h("option", { value: "es-AR" }, "Español rioplatense (voseo)"),
    h("option", { value: "es-MX" }, "Español de México"),
    h("option", { value: "pt-BR" }, "Portugués de Brasil"),
    h("option", { value: "pt-PT" }, "Portugués de Portugal"));

  put(v,
    h("section", { class: "hero" },
      h("img", { class: "hero-logo", src: "logo.svg", alt: "", width: 108, height: 108 }),
      h("div", {},
        h("div", { class: "eyebrow" }, "Libre, gratuito y de código abierto"),
        h("h1", {}, "Traduce libros con la voz de su traductor oficial"),
        h("p", { class: "tagline" }, "Enséñale a la IA cómo se tradujeron otros libros del mismo autor y revisa la traducción capítulo a capítulo, conservando el formato del libro.")),
      h("ol", { class: "how" },
        h("li", {}, h("strong", {}, "Elige el libro"), h("span", {}, "Un EPUB sin DRM, o conviértelo antes desde PDF.")),
        h("li", {}, h("strong", {}, "Enséñale el estilo"), h("span", {}, "Otros libros del autor junto con su traducción oficial.")),
        h("li", {}, h("strong", {}, "Traduce y revisa"), h("span", {}, "Con tu IA o copiando en cualquier chat, capítulo a capítulo.")))),
    h("div", { class: "grid2" },
      h("div", { class: "card stack" },
        cardHead("libro", "Nuevo proyecto"),
        h("div", { class: "field" }, h("label", {}, "Libro a traducir ", h("span", { class: "hint" }, "(EPUB sin DRM)")), pathField("new-epub", "C:\\Libros\\mi-libro.epub")),
        h("div", { class: "row" },
          h("div", { class: "field", style: "flex:1" }, h("label", {}, "Traducir al"), langSelect),
          h("div", { class: "field", style: "flex:1" }, h("label", {}, "Variante"), variant)),
        h("div", { class: "field" }, h("label", {}, "Carpeta del proyecto ", h("span", { class: "hint" }, "(opcional)")),
          pathField("new-folder", info.carpeta_por_defecto + "\\…", "carpeta")),
        h("div", { class: "row end" }, h("button", {
          class: "gold",
          onclick: async () => {
            const epub = $("#new-epub").value.trim();
            if (!epub) return toast("Elige el EPUB que quieres traducir.", true);
            const s = await guard(() => rpc("create_project", epub, $("#new-folder").value.trim(), $("#new-lang").value, $("#new-variant").value));
            if (s) await opened(s);
          },
        }, "Crear proyecto"))),
      h("div", { class: "card stack" },
        cardHead("carpeta", "Abrir proyecto"),
        h("div", { class: "field" }, h("label", {}, "Carpeta del proyecto"), pathField("open-folder", info.carpeta_por_defecto + "\\mi-libro", "carpeta")),
        h("div", { class: "row end" }, h("button", {
          onclick: async () => {
            const f = $("#open-folder").value.trim();
            if (!f) return toast("Indica la carpeta del proyecto.", true);
            const s = await guard(() => rpc("open_project", f));
            if (s) await opened(s);
          },
        }, "Abrir")),
        h("h3", {}, "Recientes"),
        info.recientes.length
          ? h("div", {}, info.recientes.map((r) => h("div", { class: "recent" },
            h("div", { style: "flex:1;min-width:0" }, h("strong", {}, r.nombre), h("div", { class: "muted small", style: "overflow-wrap:anywhere" }, r.carpeta)),
            r.existe ? h("button", { class: "small ghost", onclick: async () => {
              const s = await guard(() => rpc("open_project", r.carpeta));
              if (s) await opened(s);
            } }, "Abrir") : h("span", { class: "badge" }, "no encontrado"))))
          : h("p", { class: "muted" }, "Todavía no hay proyectos."))),
    pdfCard(),
    h("div", { class: "home-foot" }, h("div", { class: "ornament" }, h("span")),
      h("p", { class: "muted small" }, `Jerónimo ${info.version} · modo ${info.modo} · Usa solo libros que tengas derecho a traducir.`)));
}

function pdfCard() {
  const last = state.lastTask && state.lastTask.nombre === "Convertir PDF a EPUB" ? state.lastTask : null;
  const r = last && last.resultado;
  const busy = state.task && state.task.activa;
  return h("div", { class: "card stack" },
    cardHead("pdf", "Convertir PDF a EPUB"),
    h("p", { class: "muted" }, "Para tus propios PDF con texto seleccionable (no escaneados): quita encabezados y números de página, une las palabras cortadas, detecta capítulos, cursivas y notas al pie, y crea un EPUB listo para tu lector o para traducir."),
    h("div", { class: "field" }, h("label", {}, "PDF"), pathField("pdf-in", "C:\\Libros\\mi-libro.pdf", "pdf")),
    h("div", { class: "field" }, h("label", {}, "Guardar EPUB en ", h("span", { class: "hint" }, "(opcional; por defecto, junto al PDF)")),
      h("input", { id: "pdf-out", type: "text", placeholder: "C:\\Libros\\mi-libro.epub" })),
    h("div", { class: "row end" }, h("button", { disabled: busy, onclick: () => {
      const pdf = $("#pdf-in").value.trim();
      if (!pdf) return toast("Elige el PDF que quieres convertir.", true);
      startTask(() => rpc("convert_pdf", pdf, $("#pdf-out").value.trim()));
    } }, "Convertir")),
    r ? h("div", { class: "result" },
      h("p", { style: "margin-top:0" }, h("strong", {}, "EPUB creado: "), r.ruta),
      h("p", { class: "small" }, `«${r.titulo}»${r.autor ? " · " + r.autor : ""} · ${r.paginas} páginas · ${r.capitulos} capítulos · ${r.notas} notas · ${r.lineas_quitadas} encabezados o números de página quitados`
        + (r.paginas_omitidas.length ? ` · índice impreso omitido (pág. ${r.paginas_omitidas.join(", ")})` : "")),
      r.avisos.map((w) => h("p", { class: "warn small" }, w)),
      h("p", { class: "muted small" }, "Revísalo en tu lector: si algún encabezado o nota quedó mal, avísanos para mejorar el conversor."),
      h("div", { class: "row" },
        h("button", { class: "ghost", onclick: () => rpc("open_folder", r.ruta) }, "Abrir carpeta"),
        h("button", { class: "ghost", onclick: () => { $("#new-epub").value = r.ruta; $("#new-epub").scrollIntoView({ behavior: "smooth" }); toast("Elige el idioma y pulsa «Crear proyecto» para traducirlo."); } }, "Usarlo para un proyecto de traducción")))
      : null);
}

async function opened(summary) {
  state.summary = summary;
  state.info = await rpc("app_info");
  go("resumen");
  render();
}

/* ---------------------------------------------------------------- resumen */

function statusBadge(status) {
  const cls = { aprobado: "ok", traducido: "info", "en curso": "warn", omitido: "", pendiente: "", "vacío": "" }[status] || "";
  return h("span", { class: "badge " + cls }, status);
}

function costText(est) {
  if (!est) return "";
  if (est.coste_usd === 0) return "sin coste (modelo local o modo manual)";
  if (est.coste_usd === null || est.coste_usd === undefined) return "depende del modelo y del plan del proveedor";
  return `≈ ${est.coste_usd.toFixed(2)} USD`;
}

function renderSummary(v) {
  const s = state.summary;
  const prog = s.progreso;
  const provName = state.info.proveedores[s.proveedor.tipo] || s.proveedor.tipo;
  const steps = [
    { done: s.referencias.length > 0, title: "Libros de referencia", text: s.referencias.length
      ? `${s.referencias.length} añadido(s): ${s.referencias.map((r) => r.nombre).join(", ")}`
      : "Añade libros del autor con su traducción oficial (recomendado).", go: "referencias" },
    { done: s.tiene_clave && !!s.proveedor.tipo, title: "Inteligencia artificial", text: `${provName}${s.proveedor.modelo ? " · " + s.proveedor.modelo : ""}${s.tiene_clave ? "" : " · falta la clave"}`, go: "ia" },
    { done: s.estilo && s.glosario > 0, title: "Estilo y glosario", text: `Guía de estilo: ${s.estilo ? "sí" : "no"} · glosario: ${s.glosario} términos · sinopsis: ${s.sinopsis ? "sí" : "no"}`, go: "conocimiento" },
    { done: prog.total > 0 && prog.hechos === prog.total, title: "Traducir y revisar", text: `${fmt(prog.hechos)} de ${fmt(prog.total)} párrafos · ${prog.caps_aprobados} de ${prog.caps_total} documentos aprobados`, go: "capitulos" },
    { done: false, title: "Exportar el EPUB", text: "Genera el libro traducido o una edición bilingüe.", go: "exportar" },
  ];
  const nextStep = steps.find((st) => !st.done) || steps[steps.length - 1];
  put(v,
    pageHead("Resumen del proyecto", s.titulo || s.nombre,
      `${s.autor || "Autor desconocido"} · ${s.idioma_origen} → ${s.idioma_destino} · ${fmt(prog.palabras)} palabras`),
    h("div", { class: "card" },
      h("div", { class: "row" }, h("strong", {}, `Progreso: ${pct(prog.hechos, prog.total)} %`), h("div", { class: "progress" }, h("div", { style: `width:${pct(prog.hechos, prog.total)}%` })),
        h("button", { class: "gold", onclick: () => go(nextStep.go) }, `Seguir: ${nextStep.title}`)),
      h("ol", { class: "steps" }, steps.map((st) => h("li", { class: st.done ? "done" : "" },
        h("div", { class: "grow" }, h("strong", {}, st.title), h("div", { class: "muted small" }, st.text)),
        h("button", { class: "small ghost", onclick: () => go(st.go) }, "Ir"))))),
    h("div", { class: "grid2" },
      h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, "Lo que falta por traducir"),
        h("p", {}, `${fmt(s.estimacion.palabras)} palabras · ${s.estimacion.llamadas === 1 ? "1 petición" : `unas ${fmt(s.estimacion.llamadas)} peticiones`} a la IA`),
        h("p", {}, "Coste estimado: ", h("strong", {}, costText(s.estimacion))),
        h("p", { class: "muted small" }, "Estimación aproximada; el coste real depende del modelo y de cuánto razone.")),
      h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, "Consumo hasta ahora"),
        s.uso.length ? h("table", {}, h("tr", {}, h("th", {}, "Modelo"), h("th", {}, "Entrada"), h("th", {}, "Salida"), h("th", {}, "Coste")),
          s.uso.map((u) => h("tr", {}, h("td", {}, u.modelo), h("td", { class: "num" }, fmt(u.input_tokens + (u.cache_read_tokens || 0))),
            h("td", { class: "num" }, fmt(u.output_tokens)), h("td", { class: "num" }, u.coste === null ? "—" : `${u.coste.toFixed(2)} USD`))))
          : h("p", { class: "muted" }, "Todavía no se ha usado la IA en este proyecto."))));
}

/* ---------------------------------------------------------------- referencias */

function renderReferences(v) {
  const s = state.summary;
  put(v,
    pageHead("Paso 1 de 5", "Libros de referencia", "Añade otros libros del mismo autor con su traducción oficial (el original y la traducción, ambos en EPUB). La app los empareja párrafo a párrafo y aprende cómo traduce ese traductor: nombres, diálogos, tono y vocabulario."),
    s.referencias.length
      ? h("div", {}, s.referencias.map((r) => h("div", { class: "card row" },
        icon("libro"),
        h("div", { style: "flex:1" }, h("h3", { style: "margin:0 0 2px" }, r.nombre),
          h("div", { class: "muted small" }, `${r.titulo_original} ↔ ${r.titulo_traduccion}`),
          h("div", { class: "small" }, `${fmt(r.estadisticas.pairs)} pares · cobertura ${pct(r.estadisticas.coverage, 1)} % · `,
            h("span", { class: "badge " + (r.estadisticas.quality === "buena" ? "ok" : r.estadisticas.quality === "baja" ? "danger" : "warn") }, `calidad ${r.estadisticas.quality}`))),
        h("button", { class: "small ghost", onclick: async () => {
          if (!(await modal(`¿Quitar «${r.nombre}» de las referencias?`, { ok: "Quitar", okClass: "danger" }))) return;
          const sum = await guard(() => rpc("remove_reference", r.carpeta));
          if (sum) { state.summary = sum; render(); }
        } }, "Quitar"))))
      : h("div", { class: "card empty-state" }, "Aún no hay libros de referencia."),
    h("div", { class: "card stack" },
      cardHead("pluma", "Añadir libro de referencia"),
      h("div", { class: "field" }, h("label", {}, "Original ", h("span", { class: "hint" }, "(mismo idioma que el libro a traducir)")), pathField("ref-src", "C:\\Libros\\otro-libro-EN.epub")),
      h("div", { class: "field" }, h("label", {}, "Traducción oficial"), pathField("ref-tgt", "C:\\Libros\\otro-libro-ES.epub")),
      h("div", { class: "field" }, h("label", {}, "Nombre ", h("span", { class: "hint" }, "(opcional)")), h("input", { id: "ref-name", type: "text" })),
      h("div", { class: "row end" }, h("button", { class: s.referencias.length ? "" : "gold", onclick: () => {
        const a = $("#ref-src").value.trim();
        const b = $("#ref-tgt").value.trim();
        if (!a || !b) return toast("Elige los dos EPUB: el original y su traducción oficial.", true);
        startTask(() => rpc("add_reference", a, b, $("#ref-name").value.trim()));
      } }, "Emparejar"))));
}

/* ---------------------------------------------------------------- IA */

const PROVIDER_HELP = {
  anthropic: "Crea una clave en console.anthropic.com → «API Keys». Se paga por uso, aparte de la suscripción de claude.ai (que no se puede usar en otras apps). Modelos: claude-opus-5-5 (máxima calidad), claude-sonnet-5-5 (muy buena y a mitad de precio), claude-haiku-5-5 (económico, para borradores).",
  gemini: "Consigue una clave gratis en aistudio.google.com → «Get API key». El nivel gratuito tiene límites por minuto y por día; la app espera y reintenta sola. Pulsa «Cargar modelos» y elige uno de la familia «pro» para más calidad o «flash» para más velocidad.",
  openai: "Crea una clave en platform.openai.com/api-keys (pago por uso). Pulsa «Cargar modelos» para elegir.",
  openrouter: "Una sola clave para muchos modelos (Claude, GPT, Gemini, Llama…): openrouter.ai/keys. Pulsa «Cargar modelos».",
  deepseek: "Crea una clave en platform.deepseek.com. Es económico. Modelo habitual: deepseek-chat.",
  ollama: "Gratis y en tu PC. Instala Ollama (ollama.com), descarga un modelo grande (desde la terminal: ollama pull nombre-del-modelo) y pulsa «Cargar modelos». Necesitas una tarjeta gráfica con bastante memoria; la calidad es menor que en la nube.",
  lmstudio: "Gratis y en tu PC. En LM Studio, carga un modelo y activa el servidor local (puerto 1234). Luego pulsa «Cargar modelos».",
  compatible: "Cualquier servicio compatible con la API de OpenAI: indica la dirección (suele terminar en /v1) y la clave si hace falta.",
  manual: "Sin clave de API y con cualquier IA: Claude, ChatGPT, Gemini, Copilot, DeepSeek, Le Chat o un modelo en tu PC. La app copia cada bloque con todo el contexto que hace falta (guía de estilo, glosario, ejemplos oficiales, resumen del capítulo y párrafos anteriores), tú lo pegas en el chat y luego pegas la respuesta. La preparación de la guía, el glosario y el análisis del libro también se hace así, desde «Estilo y glosario → Generar con IA».",
};

function renderAI(v) {
  const s = state.summary;
  const cfg = Object.assign({ tipo: "anthropic", modelo: "", esfuerzo: "medium", base_url: "" }, s.proveedor);
  let current = cfg.tipo;
  const list = h("div", { class: "provider-list" });
  const fields = h("div", { class: "stack" });

  const draw = () => {
    list.textContent = "";
    for (const [k, name] of Object.entries(state.info.proveedores)) {
      list.appendChild(h("label", { class: k === current ? "selected" : "" },
        h("input", { type: "radio", name: "prov", value: k, checked: k === current, onchange: () => { current = k; draw(); } }),
        h("span", {}, name)));
    }
    fields.textContent = "";
    const needsKey = !["ollama", "lmstudio", "manual"].includes(current);
    const needsUrl = ["compatible", "ollama", "lmstudio"].includes(current);
    const sameKind = current === cfg.tipo;
    const datalist = h("datalist", { id: "model-list" },
      current === "anthropic" ? state.info.modelos_claude.map(([id, label]) => h("option", { value: id }, label)) : []);
    put(fields,
      h("div", { class: "help" }, PROVIDER_HELP[current] || ""),
      current === "manual" ? null : h("div", { class: "field" }, h("label", {}, "Modelo"),
        h("div", { class: "path" },
          h("input", { id: "ai-model", type: "text", list: "model-list", value: sameKind ? cfg.modelo || "" : "",
            placeholder: current === "anthropic" ? "claude-opus-5-5" : "elige o escribe el nombre del modelo" }),
          h("button", { class: "ghost", onclick: async () => {
            await saveAI(current, true);
            const models = await guard(() => rpc("list_models"));
            if (!models) return;
            datalist.textContent = "";
            models.forEach((m) => datalist.appendChild(h("option", { value: m })));
            toast(models.length ? `${models.length} modelos disponibles: haz clic en el campo para elegir.` : "No se encontraron modelos.", !models.length);
          } }, "Cargar modelos")), datalist),
      needsKey ? h("div", { class: "field" }, h("label", {}, "Clave de API ", h("span", { class: "hint" }, s.tiene_clave && sameKind ? "(ya guardada; escribe otra para cambiarla)" : "")),
        h("input", { id: "ai-key", type: "password", placeholder: s.tiene_clave && sameKind ? "••••••••  guardada" : "pega aquí tu clave", autocomplete: "off" })) : null,
      needsUrl ? h("div", { class: "field" }, h("label", {}, "Dirección del servidor ", h("span", { class: "hint" }, "(vacío = la habitual)")),
        h("input", { id: "ai-url", type: "text", value: sameKind ? cfg.base_url || "" : "",
          placeholder: current === "ollama" ? "http://localhost:11434" : current === "lmstudio" ? "http://localhost:1234/v1" : "https://…/v1" })) : null,
      current === "anthropic" ? h("div", { class: "field" }, h("label", {}, "Esfuerzo de razonamiento"),
        h("select", { id: "ai-effort" }, ["low", "medium", "high"].map((e) => h("option", { value: e, selected: (cfg.esfuerzo || "medium") === e },
          { low: "Bajo (más barato)", medium: "Medio (recomendado)", high: "Alto (más caro)" }[e])))) : null,
      h("div", { class: "row end" },
        current === "manual" ? null : h("button", { class: "ghost", onclick: async () => {
          if (!(await saveAI(current))) return;
          const r = await guard(() => rpc("test_provider"));
          if (r) toast(`Conexión correcta con ${r.modelo}. Respuesta: «${r.respuesta}»`);
        } }, "Probar conexión"),
        h("button", { class: "gold", onclick: async () => { if (await saveAI(current)) toast("Ajustes de IA guardados."); } }, "Guardar")));
  };
  draw();
  put(v,
    pageHead("Paso 2 de 5", "Inteligencia artificial", "Elige qué IA traduce. Cada persona usa la suya: tu clave se guarda en el almacén seguro de Windows, nunca dentro del proyecto. Sin clave, el modo manual funciona con cualquier chat."),
    h("div", { class: "card" }, list),
    h("div", { class: "card" }, fields),
    h("div", { class: "card" }, h("strong", {}, "Coste estimado de lo pendiente: "), costText(s.estimacion),
      h("span", { class: "muted small" }, ` (${fmt(s.estimacion.palabras)} palabras)`)));
}

async function saveAI(kind, quiet = false) {
  const cfg = {
    tipo: kind,
    modelo: $("#ai-model") ? $("#ai-model").value.trim() : "",
    base_url: $("#ai-url") ? $("#ai-url").value.trim() : "",
    esfuerzo: $("#ai-effort") ? $("#ai-effort").value : "medium",
  };
  const key = $("#ai-key") ? $("#ai-key").value.trim() : "";
  const r = await guard(() => rpc("set_provider", cfg, key));
  if (!r) return false;
  if (r.clave_en && !quiet) toast(`Clave guardada en el ${r.clave_en}.`);
  state.summary = await rpc("project_summary");
  return true;
}

/* ---------------------------------------------------------------- conocimiento */

async function renderKnowledge(v) {
  const k = await guard(() => rpc("knowledge"));
  if (!k) return;
  let tab = state.knowledgeTab || "estilo";
  const body = h("div", {});
  const tabs = h("div", { class: "tabs" });
  const tabDefs = [["estilo", "Guía de estilo"], ["glosario", `Glosario (${k.glosario.length})`], ["sinopsis", "Sinopsis y capítulos"], ["ia", "Generar con IA"]];
  const drawTabs = () => {
    tabs.textContent = "";
    tabDefs.forEach(([id, label]) => tabs.appendChild(h("button", { class: id === tab ? "active" : "", onclick: () => { tab = state.knowledgeTab = id; drawTabs(); drawBody(); } }, label)));
  };
  const drawBody = () => {
    body.textContent = "";
    if (tab === "estilo") {
      const ta = h("textarea", { class: "big", spellcheck: "true" });
      ta.value = k.estilo;
      put(body, h("p", { class: "muted" }, "La IA sigue esta guía en cada fragmento. Puedes editarla libremente (formato Markdown)."), ta,
        h("div", { class: "row end" }, h("button", { onclick: async () => { if (await guard(() => rpc("save_style", ta.value))) { k.estilo = ta.value; toast("Guía de estilo guardada."); } } }, "Guardar")));
    } else if (tab === "glosario") {
      put(body, glossaryEditor(k));
    } else if (tab === "sinopsis") {
      const syn = h("textarea", { style: "min-height:160px" });
      syn.value = k.sinopsis;
      const areas = {};
      put(body, h("p", { class: "muted" }, "Contexto que la IA recibe con cada fragmento: quién es quién, género de los personajes, tono…"),
        h("label", {}, "Sinopsis del libro"), syn,
        h("h3", {}, "Resumen de cada capítulo"),
        k.capitulos.map((c) => {
          areas[c.index] = h("textarea", { style: "min-height:70px" });
          areas[c.index].value = c.resumen;
          return h("div", { class: "field" }, h("label", {}, c.titulo), areas[c.index]);
        }),
        h("div", { class: "row end" }, h("button", { onclick: async () => {
          const sums = {};
          for (const [i, ta] of Object.entries(areas)) sums[i] = ta.value;
          if (await guard(() => rpc("save_synopsis", syn.value, sums))) toast("Sinopsis guardada.");
        } }, "Guardar")));
    } else {
      const s = state.summary;
      const manual = s.proveedor.tipo === "manual";
      const checks = [
        ["estilo", "Guía de estilo", "Analiza las traducciones oficiales y escribe la guía (reemplaza la actual).", s.referencias.length > 0],
        ["glosario", "Glosario oficial", "Cómo tradujo el traductor oficial los nombres y términos frecuentes (se añaden al glosario).", s.referencias.length > 0],
        ["analisis", "Análisis del libro", "Lee el libro entero: sinopsis, resumen de cada capítulo y lista de personajes y términos.", true],
        ["terminos", "Proponer términos", "Propone cómo traducir los términos del libro que aún no están en el glosario.", true],
      ];
      if (manual) {
        put(body, manualPrepPanel(checks));
        return;
      }
      put(body,
        h("div", { class: "card" }, checks.map(([id, title, desc, enabled]) => h("label", { class: "row", style: "font-weight:500;align-items:flex-start;padding:6px 0" },
          h("input", { type: "checkbox", value: id, class: "prep", checked: enabled && !(id === "estilo" && k.estilo), disabled: !enabled }),
          h("span", {}, h("strong", {}, title), h("br"), h("span", { class: "muted small" }, enabled ? desc : "Necesita al menos un libro de referencia."))))),
        h("div", { class: "row end" }, h("button", { class: "gold", onclick: () => {
          const steps = [...document.querySelectorAll("input.prep:checked")].map((c) => c.value);
          if (!steps.length) return toast("Marca al menos un paso.", true);
          startTask(() => rpc("prepare", steps));
        } }, "Generar")));
    }
  };
  drawTabs();
  drawBody();
  put(v, pageHead("Paso 3 de 5", "Estilo y glosario", "Todo lo que la IA sabe del libro y del estilo que debe imitar. Revisa y corrige lo que quieras: tus cambios mandan."),
    tabs, body);
}

function manualPrepPanel(checks) {
  const box = h("div", {});
  const draw = async () => {
    box.textContent = "";
    const st = await guard(() => rpc("manual_prep_status"));
    if (st === undefined) return;
    if (!st) {
      put(box,
        h("p", {}, "Modo manual: cada paso genera unas instrucciones con todo el contexto necesario. Las copias en tu chat de IA (Claude, ChatGPT, Gemini, Copilot…), y pegas aquí la respuesta. Hazlos en este orden:"),
        h("div", { class: "card" }, checks.map(([id, title, desc, enabled], i) => h("div", { class: "row", style: "padding:8px 0;border-bottom:1px solid var(--border)" },
          h("div", { style: "flex:1" }, h("strong", {}, `${i + 1}. ${title}`), h("br"), h("span", { class: "muted small" }, enabled ? desc : "Necesita al menos un libro de referencia.")),
          h("button", { class: "small", disabled: !enabled, onclick: async () => {
            if (await guard(() => rpc("manual_prep_start", id))) draw();
          } }, "Empezar")))));
      return;
    }
    const out = h("textarea", { placeholder: "Pega aquí la respuesta completa del chat (el bloque de código entero)." });
    const info = h("p", { class: "muted small", style: "margin:0" });
    const copy = async () => {
      const p = await guard(() => rpc("manual_prep_prompt"));
      if (!p) return;
      info.textContent = `Copiado: ${p.etiqueta} (${fmt(p.caracteres)} caracteres).`;
      if (await copyText(p.texto)) toast("Instrucciones copiadas. Pégalas en tu chat de IA.");
      else {
        const ta = h("textarea", { class: "big" });
        ta.value = p.texto;
        await modal(h("div", {}, h("p", {}, "Selecciona y copia este texto:"), ta), { ok: "Cerrar", cancel: null });
      }
    };
    const doImport = async (thenCopy) => {
      if (!out.value.trim()) return toast("Pega primero la respuesta del chat.", true);
      const r = await guard(() => rpc("manual_prep_import", out.value));
      if (!r) return;
      if (r.terminado) {
        toast(`${r.nombre}: listo.`);
        state.summary = await rpc("project_summary");
        render();
        return;
      }
      toast(`Parte ${r.hechos} de ${r.total} importada.`);
      await draw();
      if (thenCopy) await copy();
    };
    put(box, h("div", { class: "card manual stack" },
      h("div", { class: "eyebrow", style: "margin:0" }, "Modo manual"),
      h("h3", { style: "margin-top:0" }, `${st.nombre}: parte ${st.hechos + 1} de ${st.total}`),
      h("p", { class: "muted small", style: "margin:0" }, st.etiqueta),
      h("div", { class: "row" }, h("button", { onclick: copy }, "Copiar instrucciones"), info),
      out,
      h("div", { class: "row" },
        h("button", { class: "ghost danger-text", onclick: async () => {
          if (!(await modal("¿Cancelar este paso? Lo ya importado se conserva.", { ok: "Cancelar paso" }))) return;
          if (await guard(() => rpc("manual_prep_cancel"))) draw();
        } }, "Cancelar paso"),
        h("div", { class: "spacer" }),
        h("button", { class: "ghost", onclick: () => doImport(false) }, "Solo importar"),
        h("button", { class: "gold", onclick: () => doImport(true) }, "Importar y copiar la siguiente"))));
  };
  draw();
  return box;
}

function glossaryEditor(k) {
  let rows = k.glosario.map((e) => Object.assign({}, e));
  const search = h("input", { type: "search", placeholder: "Buscar término…", oninput: () => draw() });
  const table = h("table", {});
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    table.textContent = "";
    table.appendChild(h("tr", {}, h("th", {}, "Término (original)"), h("th", {}, "Traducción"), h("th", {}, "Tipo"), h("th", {}, "Nota"), h("th", {}, "Origen"), h("th", {}, "")));
    rows.forEach((e, i) => {
      if (q && !(`${e.termino} ${e.traduccion} ${e.nota}`.toLowerCase().includes(q))) return;
      const inp = (field, width) => h("input", { type: "text", value: e[field] || "", style: width ? `width:${width}` : "",
        oninput: (ev) => { e[field] = ev.target.value; if (field !== "nota") e.origen = "usuario"; } });
      table.appendChild(h("tr", {}, h("td", {}, inp("termino")), h("td", {}, inp("traduccion")), h("td", {}, inp("tipo", "110px")), h("td", {}, inp("nota")),
        h("td", {}, h("span", { class: "badge " + (e.origen === "usuario" ? "ok" : e.origen === "oficial" ? "info" : "warn") }, e.origen || "usuario")),
        h("td", {}, h("button", { class: "small ghost", title: "Eliminar", onclick: () => { rows.splice(i, 1); draw(); } }, "✕"))));
    });
  };
  draw();
  return h("div", {},
    h("p", { class: "muted" }, "«oficial»: tomado de las traducciones oficiales · «propuesta»: sugerido por la IA · «usuario»: decidido por ti."),
    h("div", { class: "row" }, h("div", { style: "flex:1" }, search),
      h("button", { class: "ghost", onclick: () => { rows.unshift({ termino: "", traduccion: "", tipo: "", nota: "", origen: "usuario" }); search.value = ""; draw(); } }, "Añadir término"),
      h("button", { onclick: async () => {
        const n = await guard(() => rpc("save_glossary", rows));
        if (n !== undefined) { toast(`Glosario guardado: ${n} términos.`); state.summary = await rpc("project_summary"); }
      } }, "Guardar")),
    h("div", { class: "card", style: "padding:6px 10px" }, table));
}

/* ---------------------------------------------------------------- capítulos */

function renderChapters(v) {
  const s = state.summary;
  const chapters = s.capitulos.filter((c) => c.total > 0);
  const busy = state.task && state.task.activa;
  const manual = s.proveedor.tipo === "manual";
  put(v,
    pageHead("Paso 4 de 5", "Capítulos", manual
      ? "Modo manual: en cada capítulo copias los bloques a tu chat de IA y pegas las respuestas. Lo aprobado sirve de ejemplo para los siguientes."
      : s.revision === "capitulo"
        ? "Traduce de a un capítulo, revísalo y apruébalo: lo aprobado sirve de ejemplo para los siguientes."
        : "Puedes traducir todo de corrido y revisar al final."),
    h("div", { class: "row" },
      manual ? h("button", { class: "gold", onclick: () => {
        const next = chapters.find((c) => c.status !== "omitido" && c.done < c.total);
        if (next) go("revision", next.index); else toast("No quedan capítulos pendientes.");
      } }, "Continuar en modo manual") : null,
      manual ? null : h("button", { class: "gold", disabled: busy, onclick: () => startTask(() => rpc("translate", "siguiente", null)) }, "Traducir el siguiente capítulo"),
      manual ? null : h("button", { class: "ghost", disabled: busy, onclick: async () => {
        const est = s.estimacion;
        const ok = await modal(h("div", {}, h("h3", {}, "Traducir todo lo pendiente"),
          h("p", {}, `Se traducirán ${fmt(est.palabras)} palabras en unas ${fmt(est.llamadas)} peticiones. Coste estimado: ${costText(est)}.`),
          h("p", { class: "muted" }, "Puedes detenerlo cuando quieras; lo ya traducido se conserva.")), { ok: "Traducir todo" });
        if (ok) startTask(() => rpc("translate", "todo", null));
      } }, "Traducir todo lo pendiente"),
      h("span", { class: "muted small" }, `Pendiente: ${fmt(s.estimacion.palabras)} palabras · ${costText(s.estimacion)}`)),
    h("div", { class: "card", style: "padding:6px 12px;margin-top:18px" },
      h("table", {},
        h("tr", {}, h("th", {}, "Capítulo"), h("th", {}, "Palabras"), h("th", {}, "Estado"), h("th", {}, "Progreso"), h("th", {}, "")),
        chapters.map((c) => h("tr", { class: c.status === "omitido" ? "skipped" : "" },
          h("td", {}, h("button", { class: "link", onclick: () => go("revision", c.index) }, c.title || `Documento ${c.index}`), c.in_spine ? null : h("span", { class: "muted small" }, " (índice)")),
          h("td", { class: "num" }, fmt(c.words)),
          h("td", {}, statusBadge(c.status)),
          h("td", { style: "width:180px" }, h("div", { class: "row" }, h("div", { class: "progress" }, h("div", { style: `width:${pct(c.done, c.total)}%` })), h("span", { class: "small muted" }, `${c.done}/${c.total}`))),
          h("td", {}, h("div", { class: "row", style: "justify-content:flex-end;flex-wrap:nowrap" },
            h("button", { class: "small ghost", onclick: () => go("revision", c.index) }, "Revisar"),
            c.status !== "omitido" && c.done < c.total && !manual ? h("button", { class: "small", disabled: busy, onclick: () => startTask(() => rpc("translate", "capitulo", c.index)) }, "Traducir") : null,
            h("button", { class: "small ghost", title: "No traducir este documento (créditos, publicidad…)", onclick: async () => {
              const sum = await guard(() => rpc("set_skipped", c.index, c.status !== "omitido"));
              if (sum) { state.summary = sum; render(); }
            } }, c.status === "omitido" ? "Incluir" : "Omitir"))))))));
}

/* ---------------------------------------------------------------- revisión */

let reviewData = null;
let reviewFilter = "todos";

async function refreshChapterQuietly() {
  if (state.view !== "revision") return;
  const data = await rpc("chapter", state.chapter).catch(() => null);
  if (data && reviewData && data.hechos !== reviewData.hechos) render();
}

async function renderReview(v) {
  const index = state.chapter;
  const data = await guard(() => rpc("chapter", index));
  if (!data) return;
  reviewData = data;
  const list = state.summary.capitulos.filter((c) => c.total > 0 && c.status !== "omitido");
  const pos = list.findIndex((c) => c.index === index);
  const next = pos >= 0 && pos + 1 < list.length ? list[pos + 1] : null;
  const busy = state.task && state.task.activa;
  const manualMode = state.summary.proveedor.tipo === "manual";

  const filterSel = h("select", { style: "width:auto", onchange: (e) => { reviewFilter = e.target.value; drawRows(); } },
    h("option", { value: "todos", selected: reviewFilter === "todos" }, "Todos los párrafos"),
    h("option", { value: "avisos", selected: reviewFilter === "avisos" }, "Con avisos o notas"),
    h("option", { value: "pendientes", selected: reviewFilter === "pendientes" }, "Sin traducir"));

  const rows = h("div", {});
  const drawRows = () => {
    rows.textContent = "";
    const segs = data.segmentos.filter((s) => reviewFilter === "todos" ||
      (reviewFilter === "pendientes" ? !s.tgt : (s.avisos.length || s.nota)));
    if (!segs.length) rows.appendChild(h("div", { class: "card empty-state" }, "No hay párrafos con este filtro."));
    segs.forEach((s) => rows.appendChild(segmentRow(data, s)));
  };
  drawRows();

  const approved = data.aprobado;
  put(v,
    h("div", { class: "review-head" },
      h("div", { class: "row" },
        h("button", { class: "small ghost", onclick: () => go("capitulos") }, "‹ Capítulos"),
        h("div", { style: "flex:1;min-width:0" }, h("div", { class: "eyebrow", style: "margin:0 0 2px" }, "Revisión"),
          h("h1", { style: "margin:0" }, data.titulo)),
        statusBadge(data.estado)),
      h("div", { class: "row", style: "margin-top:10px" },
        h("span", { class: "muted" }, `${data.hechos} de ${data.total} párrafos traducidos`),
        h("div", { class: "spacer" }),
        filterSel,
        !manualMode && data.hechos < data.total && !data.omitir ? h("button", { class: "small", disabled: busy, onclick: () => startTask(() => rpc("translate", "capitulo", index)) }, "Traducir lo pendiente") : null,
        h("button", { class: "small ghost", onclick: () => { const p = $("#manual-panel"); p.hidden = !p.hidden; } }, "Modo manual"),
        h("button", { class: "small " + (approved ? "ghost" : "gold"), disabled: data.hechos < data.total || data.bloqueado, onclick: async () => {
          const d = await guard(() => rpc("set_approved", index, !approved));
          if (!d) return;
          await refreshSummary();
          if (!approved) toast("Capítulo aprobado. Sus párrafos servirán de ejemplo para los siguientes.");
          render();
        } }, approved ? "Quitar aprobación" : "Aprobar capítulo"),
        next ? h("button", { class: "small ghost", onclick: () => go("revision", next.index) }, "Siguiente ›") : null),
      data.bloqueado ? h("div", { class: "warn", style: "margin-top:8px" }, "Este capítulo se está traduciendo. La vista se actualiza sola; podrás editar al terminar.") : null,
      data.resumen ? h("details", { style: "margin-top:8px" }, h("summary", { class: "muted small" }, "Resumen del capítulo"), h("p", { class: "small" }, data.resumen)) : null,
      h("div", { class: "seg-cols" }, h("span", {}, `Original · ${state.summary.idioma_origen}`), h("span", {}, `Traducción · ${state.summary.idioma_destino}`))),
    manualPanel(index),
    rows);
}

function segmentRow(data, s) {
  const marks = s.marcas || {};
  const src = h("div", { class: "src" + (/^h\d$/.test(s.tag) ? " tag-h" : "") });
  markedToDom(src, s.src, marks);
  const tgt = h("div", { class: "tgt" + (s.tgt ? "" : " empty") + (/^h\d$/.test(s.tag) ? " tag-h" : ""),
    contenteditable: data.bloqueado ? "false" : "true", spellcheck: "true", lang: state.summary.idioma_destino });
  markedToDom(tgt, s.tgt || "", marks);
  let saved = normalizeMarked(s.tgt || "");
  const status = h("span", { class: "small" });

  tgt.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); tgt.blur(); } });
  tgt.addEventListener("paste", (e) => {
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData("text/plain").replace(/[\r\n]+/g, " ");
    document.execCommand("insertText", false, text);
  });
  tgt.addEventListener("input", () => tgt.classList.toggle("empty", !tgt.textContent.trim()));
  tgt.addEventListener("blur", async () => {
    const now = normalizeMarked(domToMarked(tgt));
    if (now === saved) return;
    const r = await guard(() => rpc("save_segment", data.index, s.n, now));
    if (!r) return;
    s.tgt = r.tgt;
    s.avisos = r.avisos;
    saved = normalizeMarked(r.tgt || "");
    if (r.tgt !== now) markedToDom(tgt, r.tgt || "", marks);
    status.textContent = "✓ guardado";
    status.className = "small saved";
    meta.querySelectorAll(".warn").forEach((w) => w.remove());
    r.avisos.forEach((w) => meta.insertBefore(h("span", { class: "warn" }, w), meta.firstChild.nextSibling));
  });

  const meta = h("div", { class: "meta" },
    h("span", { class: "muted" }, `§${s.n}`),
    s.avisos.map((w) => h("span", { class: "warn" }, w)),
    s.nota ? h("span", { class: "note" }, "Nota de la IA: " + s.nota) : null,
    h("span", { class: "grow" }),
    status,
    state.summary.proveedor.tipo !== "manual" ? h("button", { class: "small ghost", disabled: data.bloqueado, onclick: async (e) => {
      e.target.disabled = true;
      e.target.textContent = "Traduciendo…";
      const r = await guard(() => rpc("retranslate_segment", data.index, s.n));
      e.target.disabled = false;
      e.target.textContent = "Retraducir";
      if (!r) return;
      s.tgt = r.tgt;
      saved = normalizeMarked(r.tgt);
      markedToDom(tgt, r.tgt, marks);
      tgt.classList.remove("empty");
      status.textContent = "✓ retraducido";
      status.className = "small saved";
    } }, "Retraducir") : null);

  const row = h("div", { class: "seg" + (!s.tgt ? " pending" : s.avisos.length ? " flagged" : "") }, src, tgt, meta);
  return row;
}

function manualPanel(index) {
  const out = h("textarea", { placeholder: "Pega aquí la respuesta completa de la IA (con las marcas <seg id=\"…\">)." });
  const info = h("p", { class: "muted small", style: "margin:0" });
  const counter = h("strong", {}, "…");
  const chatSel = h("select", { style: "width:auto", onchange: async (e) => {
    const sum = await guard(() => rpc("set_settings", { chat_manual: e.target.value }));
    if (sum) { state.summary = sum; sizeSel.value = String(sum.bloque_manual); refreshCounter(); }
  } }, (state.info.chats || []).map((c) => h("option", { value: c.id, selected: (state.summary.chat_manual || "claude") === c.id }, c.nombre)));
  const sizeSel = h("select", { style: "width:auto", onchange: async (e) => {
    const sum = await guard(() => rpc("set_settings", { bloque_manual: Number(e.target.value) }));
    if (sum) { state.summary = sum; refreshCounter(); }
  } }, [[6000, "6.000 caracteres"], [10000, "10.000 caracteres"], [12000, "12.000 caracteres"], [15000, "15.000 caracteres"], [25000, "25.000 caracteres"]].map(([v, label]) =>
    h("option", { value: v, selected: (state.summary.bloque_manual || 15000) === v }, label)));

  const refreshCounter = async () => {
    const st = await guard(() => rpc("manual_status", index));
    if (!st) return;
    counter.textContent = st.parrafos_pendientes
      ? `Quedan ${st.bloques_restantes} bloque(s) · ${st.parrafos_pendientes} párrafos sin traducir en este capítulo`
      : "Capítulo completo: revísalo y apruébalo.";
  };

  const copyNext = async () => {
    const p = await guard(() => rpc("manual_prompt", index));
    if (p === undefined) return false;
    if (p === null) {
      toast("Este capítulo ya está traducido. Revísalo y apruébalo.");
      return false;
    }
    info.textContent = `Bloque copiado: párrafos ${p.ids[0]}–${p.ids[p.ids.length - 1]} (${fmt(p.caracteres)} caracteres).`;
    if (await copyText(p.texto)) {
      const chat = (state.info.chats || []).find((c) => c.id === (state.summary.chat_manual || "claude"));
      toast(`Bloque copiado. Pégalo en ${chat && chat.id !== "otro" ? chat.nombre : "tu chat de IA"}.`);
    } else {
      const ta = h("textarea", { class: "big" });
      ta.value = p.texto;
      await modal(h("div", {}, h("p", {}, "Pulsa «Copiar» para llevarte el bloque:"), ta), { ok: "Copiar", cancel: "Cerrar" })
        .then(async (ok) => { if (ok) { ta.select(); await copyText(p.texto); toast("Bloque copiado."); } });
    }
    return true;
  };

  const doImport = async (thenCopy) => {
    if (!out.value.trim()) return toast("Pega primero la respuesta de la IA.", true);
    const r = await guard(() => rpc("manual_import", index, out.value));
    if (!r) return;
    out.value = "";
    toast(`Importados ${r.guardados} párrafos.` + (r.faltan.length ? ` Faltaron: ${r.faltan.join(", ")}.` : ""));
    if (thenCopy && r.parrafos_pendientes) await copyNext();
    await refreshSummary();
    const panel = $("#manual-panel");
    const wasOpen = panel && !panel.hidden;
    await render();
    if (wasOpen && $("#manual-panel")) $("#manual-panel").hidden = false;
  };

  const panel = h("div", { id: "manual-panel", class: "card manual stack", hidden: state.summary.proveedor.tipo !== "manual" },
    h("div", { class: "eyebrow", style: "margin:0" }, "Modo manual"),
    h("h3", { style: "margin-top:0" }, "Traduce con cualquier chat de IA"),
    h("div", { class: "row" }, counter, h("div", { class: "spacer" }),
      h("div", { class: "row", style: "gap:6px;flex-wrap:nowrap" }, h("label", { style: "margin:0" }, "Chat"), chatSel),
      h("div", { class: "row", style: "gap:6px;flex-wrap:nowrap" }, h("label", { style: "margin:0" }, "Bloque"), sizeSel)),
    h("ol", { class: "small", style: "margin:0;padding-left:1.2em;line-height:1.6" },
      h("li", {}, "Pulsa «Copiar bloque» y pégalo en tu chat de IA. Cada bloque lleva todo el contexto: guía de estilo, glosario, ejemplos oficiales, resumen del capítulo y los párrafos anteriores; puedes usar el mismo chat o uno nuevo."),
      h("li", {}, "Cuando termine, copia la respuesta con el botón de copiar del bloque de código (o selecciona todo su texto)."),
      h("li", {}, "Pégala aquí y pulsa «Importar y copiar el siguiente». Si la respuesta llegó cortada, importa igual: lo que falte vuelve en el siguiente bloque. Si se corta a menudo, baja el tamaño del bloque.")),
    h("div", { class: "row" }, h("button", { onclick: copyNext }, "Copiar bloque"), info),
    out,
    h("div", { class: "row end" },
      h("button", { class: "ghost", onclick: () => doImport(false) }, "Solo importar"),
      h("button", { class: "gold", onclick: () => doImport(true) }, "Importar y copiar el siguiente")));
  refreshCounter();
  return panel;
}

/* ---------------------------------------------------------------- exportar */

function renderExport(v) {
  const s = state.summary;
  const result = h("div", {});
  const run = async (bilingual) => {
    result.textContent = "Generando…";
    const r = await guard(() => rpc("export", bilingual));
    result.textContent = "";
    if (!r) return;
    put(result, h("div", { class: "result" },
      h("p", {}, h("strong", {}, "Listo: "), r.ruta),
      r.total_avisos ? h("p", { class: "warn" }, `${r.total_avisos} párrafo(s) con avisos de formato; revisa: ${r.avisos.slice(0, 5).join(" · ")}`) : null,
      h("button", { class: "ghost", onclick: () => rpc("open_folder", r.ruta) }, "Abrir carpeta")));
  };
  const title = h("input", { type: "text", value: s.titulo_traducido || "", placeholder: s.titulo });
  const missing = s.progreso.total - s.progreso.hechos;
  put(v,
    pageHead("Paso 5 de 5", "Exportar", "Genera el EPUB con la traducción. Conserva portada, imágenes, índice y estilos del original."),
    missing ? h("div", { class: "warn" }, `Aún faltan ${fmt(missing)} párrafos: en el EPUB quedarán en el idioma original.`) : null,
    h("div", { class: "card stack" },
      h("div", { class: "field" }, h("label", {}, "Título de la edición traducida"),
        h("div", { class: "path" }, title, h("button", { class: "ghost", onclick: async () => {
          const sum = await guard(() => rpc("set_settings", { titulo_traducido: title.value.trim() }));
          if (sum) { state.summary = sum; toast("Título guardado."); }
        } }, "Guardar"))),
      h("div", { class: "row" },
        h("button", { class: "gold", onclick: () => run(false) }, "Generar EPUB traducido"),
        h("button", { class: "ghost", onclick: () => run(true) }, "Generar EPUB bilingüe"))),
    result);
}

/* ---------------------------------------------------------------- arranque */

(async function start() {
  try {
    state.info = await rpc("app_info");
    if (state.info.proyecto_abierto) await refreshSummary();
  } catch (e) {
    view().textContent = "No se pudo iniciar: " + e.message;
    return;
  }
  const [v, arg] = location.hash.slice(1).split("/");
  if (state.summary && v) {
    state.view = v;
    state.chapter = arg !== undefined ? Number(arg) : null;
  }
  await render();
  pollTask();
})();
