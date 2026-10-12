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
  papelera: '<path d="M4.5 7h15"/><path d="M9.5 7V4.8h5V7"/><path d="M6.5 7l.9 12.6A1.5 1.5 0 0 0 8.9 21h6.2a1.5 1.5 0 0 0 1.5-1.4L17.5 7"/><path d="M10.3 11v6M13.7 11v6"/>',
};
const svgIcon = (name) => h("span", { class: "btn-icon", html: `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>` });
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
const plural = (n, one, many) => (n === 1 ? `1 ${one}` : `${fmt(n)} ${many}`);

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

/* ---------------------------------------------------------------- versiones nuevas */

/* Notas de versión en Markdown sencillo → nodos de texto (sin HTML). */
function releaseNotes(text) {
  const box = h("div", { class: "release-notes small" });
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const head = line.match(/^#+\s*(.*)$/);
    const item = line.match(/^[-*]\s+(.*)$/);
    const clean = (s) => s.replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1");
    if (head) box.appendChild(h("strong", { style: "display:block;margin-top:6px" }, clean(head[1])));
    else if (item) box.appendChild(h("div", { style: "padding-left:1em;text-indent:-.8em" }, "• " + clean(item[1])));
    else box.appendChild(h("div", {}, clean(line)));
  }
  return box;
}

async function checkUpdate() {
  const r = await rpc("check_update").catch(() => null);
  if (!r) return;
  const btn = $("#btn-update");
  btn.textContent = `Nueva versión ${r.version}`;
  btn.title = "Hay una versión nueva de Jerónimo";
  btn.hidden = false;
  btn.onclick = async () => {
    const ok = await modal(h("div", {},
      h("div", { class: "eyebrow" }, "Actualización disponible"),
      h("h3", {}, `Jerónimo ${r.version}`),
      h("p", {}, `Tienes la versión ${state.info.version}. Descarga el instalador nuevo y ejecútalo: se instala encima `
        + "y conserva tus proyectos, ajustes y claves."),
      r.notas ? releaseNotes(r.notas) : null), { ok: "Descargar", cancel: "Ahora no" });
    if (ok) await guard(() => rpc("open_url", r.url));
    else btn.hidden = true;
  };
}

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
    a.classList.toggle("active", a.dataset.view === state.view || (["revision", "lectura"].includes(state.view) && a.dataset.view === "capitulos"));
  }
}

function go(viewName, chapter = null) {
  state.view = viewName;
  state.chapter = chapter;
  location.hash = ["revision", "lectura"].includes(viewName) ? `${viewName}/${chapter}` : viewName;
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
  stopSpeech();
  setChrome();
  const v = view();
  v.textContent = "";
  $("#main").scrollTop = 0;
  if (!state.summary) return renderHome(v);
  const views = { resumen: renderSummary, referencias: renderReferences, ia: renderAI, conocimiento: renderKnowledge,
    capitulos: renderChapters, revision: renderReview, lectura: renderReader, calidad: renderQuality, buscar: renderSearch,
    exportar: renderExport };
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
  const profileSel = (info.perfiles || []).length ? h("select", { id: "new-profile" },
    h("option", { value: "" }, "Ninguno (empezar de cero)"),
    info.perfiles.map((p) => h("option", { value: p.id }, `${p.nombre} · ${plural(p.libros.length, "libro", "libros")}, ${plural(p.referencias, "referencia", "referencias")}`))) : null;

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
        profileSel ? h("div", { class: "field" },
          h("label", {}, "Perfil de autor ", h("span", { class: "hint" }, "(referencias, guía y glosario de sus otros libros)"),
            h("button", { class: "link inline small", type: "button", style: "margin-left:8px", onclick: manageProfiles }, "Gestionar")),
          profileSel) : null,
        h("div", { class: "row end" }, h("button", {
          class: "gold",
          onclick: async () => {
            const epub = $("#new-epub").value.trim();
            if (!epub) return toast("Elige el EPUB que quieres traducir.", true);
            const s = await guard(() => rpc("create_project", epub, $("#new-folder").value.trim(), $("#new-lang").value, $("#new-variant").value,
              $("#new-profile") ? $("#new-profile").value : ""));
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

async function manageProfiles() {
  const list = h("div", {});
  const draw = (items) => {
    list.textContent = "";
    if (!items.length) { list.append(h("p", { class: "muted" }, "No quedan perfiles.")); return; }
    items.forEach((p) => list.append(h("div", { class: "qa-row", style: "grid-template-columns:1fr auto" },
      h("div", {}, h("strong", {}, p.nombre),
        h("div", { class: "muted small" }, `${p.libros.join(", ")} · ${plural(p.referencias, "referencia", "referencias")} · ${plural(p.glosario, "término", "términos")}`)),
      h("button", { class: "small ghost danger-text", onclick: async () => {
        const rest = await guard(() => rpc("delete_author_profile", p.id));
        if (rest) { state.info.perfiles = rest; draw(rest); }
      } }, "Borrar"))));
  };
  draw(state.info.perfiles || []);
  await modal(h("div", {}, h("div", { class: "eyebrow" }, "Perfiles de autor"), h("h3", {}, "Gestionar perfiles"),
    h("p", { class: "small muted" }, "Borrar un perfil no toca los proyectos que lo usaron."), list), { ok: "Cerrar", cancel: null });
  render();
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
      ? `${plural(s.referencias.length, "añadido", "añadidos")}: ${s.referencias.map((r) => r.nombre).join(", ")}`
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
    profileCard(),
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

function profileCard() {
  const s = state.summary;
  const profilesList = state.info.perfiles || [];
  const name = h("input", { type: "text", value: s.perfil_autor || s.autor || "", placeholder: "Nombre del autor" });
  const select = profilesList.length ? h("select", { style: "width:auto;min-width:240px" },
    profilesList.map((p) => h("option", { value: p.id, selected: p.nombre === s.perfil_autor }, p.nombre))) : null;
  const replace = h("input", { type: "checkbox" });
  return h("div", { class: "card stack" },
    cardHead("libro", "Perfil de autor"),
    h("p", { class: "muted", style: "margin-bottom:0" }, "Guarda lo aprendido de este autor (referencias emparejadas, guía de estilo, glosario y los capítulos que ya aprobaste) para empezar su próximo libro con todo listo."
      + (s.perfil_autor ? ` Este proyecto usa el perfil «${s.perfil_autor}».` : "")),
    h("div", { class: "row" },
      h("div", { style: "flex:1;min-width:220px" }, name),
      h("button", { onclick: async () => {
        const r = await guard(() => rpc("save_author_profile", name.value.trim()));
        if (!r) return;
        state.info = await rpc("app_info");
        toast(`Perfil «${r.nombre}» guardado: ${plural(r.referencias, "referencia", "referencias")}, ${plural(r.glosario, "término", "términos")}.`);
        render();
      } }, "Guardar perfil")),
    select ? h("div", { class: "row" },
      h("span", { class: "muted small" }, "Usar en este proyecto:"), select,
      h("label", { class: "row", style: "margin:0;gap:6px;font-weight:500" }, replace, "reemplazar la guía de estilo"),
      h("button", { class: "ghost", onclick: async () => {
        const r = await guard(() => rpc("apply_author_profile", select.value, replace.checked));
        if (!r) return;
        state.summary = r.resumen;
        toast(`Perfil aplicado: ${plural(r.referencias, "referencia nueva", "referencias nuevas")}, ${plural(r.terminos, "término nuevo", "términos nuevos")}`
          + (r.estilo ? " y la guía de estilo." : "."));
        render();
      } }, "Aplicar")) : null);
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
      current === "manual" && state.summary.proveedor.tipo === "manual" ? packageBox(draw) : null,
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
        h("button", { class: "gold", onclick: async () => {
          if (!(await saveAI(current))) return;
          toast("Ajustes de IA guardados.");
          draw();
        } }, "Guardar")));
  };
  draw();
  put(v,
    pageHead("Paso 2 de 5", "Inteligencia artificial", "Elige qué IA traduce. Cada persona usa la suya: tu clave se guarda en el almacén seguro de Windows, nunca dentro del proyecto. Sin clave, el modo manual funciona con cualquier chat."),
    h("div", { class: "card" }, list),
    h("div", { class: "card" }, fields),
    h("div", { class: "card" }, h("strong", {}, "Coste estimado de lo pendiente: "), costText(s.estimacion),
      h("span", { class: "muted small" }, ` (${fmt(s.estimacion.palabras)} palabras)`)));
}

/* Paquete para Proyectos: instrucciones y conocimiento que se suben una vez a un
   proyecto del chat, para que cada bloque del modo manual vaya liviano. */
function packageBox(redraw) {
  const s = state.summary;
  const pk = s.paquete;
  const when = (iso) => (iso || "").replace("T", " a las ");
  const copyInstructions = async () => {
    const text = await guard(() => rpc("package_instructions"));
    if (text && (await copyText(text))) toast("Instrucciones copiadas: pégalas en las instrucciones de tu proyecto.");
  };
  const generate = async () => {
    const r = await guard(() => rpc("project_package"));
    if (!r) return;
    s.paquete = r.paquete;
    redraw();
    const ok = await modal(h("div", {},
      h("div", { class: "eyebrow" }, "Paquete para Proyectos"),
      h("h3", {}, "Paquete listo"),
      h("p", {}, `Instrucciones: ${fmt(r.caracteres_instrucciones)} caracteres. Archivo de conocimiento: ${fmt(r.caracteres_conocimiento)} caracteres, guardado en:`),
      h("p", { class: "small", style: "overflow-wrap:anywhere" }, r.ruta),
      h("p", { class: "muted small" }, "Copia las instrucciones en las instrucciones del proyecto y sube el archivo a sus archivos o conocimiento.")),
    { ok: "Copiar instrucciones", cancel: "Cerrar" });
    if (ok) await copyInstructions();
  };
  const light = h("input", { type: "checkbox", checked: !!s.bloques_livianos, onchange: async (e) => {
    const sum = await guard(() => rpc("set_settings", { bloques_livianos: e.target.checked }));
    if (!sum) { e.target.checked = !e.target.checked; return; }
    state.summary = sum;
    toast(e.target.checked ? "Bloques livianos: cada bloque llevará solo el capítulo." : "Bloques completos: cada bloque lleva todo el contexto.");
  } });
  return h("div", { class: "card sunk stack", style: "margin-bottom:0" },
    h("div", { class: "eyebrow", style: "margin:0" }, "Opcional · para chats con proyectos"),
    h("h3", { style: "margin:2px 0 0" }, "Paquete para Proyectos"),
    h("p", { class: "small", style: "margin-bottom:0" }, "Si tu chat tiene proyectos (los Proyectos de claude.ai, los proyectos de ChatGPT o las Gems de Gemini), sube una sola vez las instrucciones y el material de referencia. Así cada bloque va liviano: solo el capítulo, sin repetir la guía de estilo, el glosario completo ni los ejemplos."),
    h("ol", { class: "small", style: "margin-bottom:0;padding-left:1.2em;line-height:1.6" },
      h("li", {}, "Pulsa «Generar paquete»."),
      h("li", {}, "En tu chat, crea un proyecto: pega las instrucciones en las instrucciones del proyecto y sube el archivo de conocimiento a sus archivos."),
      h("li", {}, "Marca «Usar bloques livianos» y traduce en chats abiertos dentro de ese proyecto.")),
    h("div", { class: "row" },
      h("button", { onclick: generate }, pk ? "Volver a generar" : "Generar paquete"),
      pk ? h("button", { class: "ghost", onclick: copyInstructions }, "Copiar instrucciones") : null,
      pk ? h("button", { class: "ghost", onclick: () => rpc("open_folder", pk.ruta) }, "Abrir carpeta del archivo") : null),
    pk ? (pk.desactualizado
      ? h("div", { class: "warn small" }, `La guía de estilo, el glosario o el análisis cambiaron desde que generaste el paquete (${when(pk.fecha)}). Vuelve a generarlo y reemplázalo en tu proyecto.`)
      : h("div", { class: "muted small" }, `Paquete generado el ${when(pk.fecha)}.`)) : null,
    h("label", { class: "row", style: "margin:14px 0 0;gap:8px;font-weight:600" }, light, "Usar bloques livianos (ya subí el paquete a un proyecto)"));
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

/* Tras cambiar traducciones del glosario, ofrece reemplazarlas en lo ya traducido. */
async function offerGlossaryUpdate(before, after) {
  const old = new Map(before.filter((e) => e.termino && e.traduccion).map((e) => [e.termino, e.traduccion.trim()]));
  const changed = after.filter((e) => e.termino && e.traduccion && old.has(e.termino) && old.get(e.termino) !== e.traduccion.trim())
    .map((e) => ({ termino: e.termino, antes: old.get(e.termino), ahora: e.traduccion.trim() }));
  const found = [];
  for (const c of changed.slice(0, 10)) {
    const r = await rpc("find_text", c.antes, { palabra: true }).catch(() => null);
    if (r && r.coincidencias) found.push(Object.assign(c, { veces: r.coincidencias, parrafos: r.parrafos }));
  }
  if (!found.length) return;
  let pick = null;
  const done = modal(h("div", {},
    h("div", { class: "eyebrow" }, "Glosario"),
    h("h3", {}, "¿Actualizar lo ya traducido?"),
    h("p", {}, "Cambiaste traducciones que ya aparecen en párrafos traducidos:"),
    found.map((c) => h("div", { class: "qa-row", style: "grid-template-columns:1fr auto" },
      h("div", {}, h("strong", {}, c.termino), h("div", { class: "small" }, `«${c.antes}» → «${c.ahora}» · ${plural(c.veces, "vez", "veces")} en ${plural(c.parrafos, "párrafo", "párrafos")}`)),
      h("button", { class: "small", onclick: () => { pick = c; $("#modal-cancel").click(); } }, "Revisar y reemplazar")))),
  { ok: "Ahora no", cancel: null });
  await done;
  if (pick) {
    state.searchPrefill = { buscar: pick.antes, reemplazar: pick.ahora, palabra: true };
    go("buscar");
  }
}

function glossaryEditor(k) {
  let rows = k.glosario.map((e) => Object.assign({}, e));
  let original = k.glosario.map((e) => Object.assign({}, e));
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
        if (n === undefined) return;
        toast(`Glosario guardado: ${n} términos.`);
        state.summary = await rpc("project_summary");
        await offerGlossaryUpdate(original, rows);
        original = rows.map((e) => Object.assign({}, e));
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
            c.done ? h("button", { class: "small ghost danger-text icon-only", title: "Borrar la traducción de este capítulo", "aria-label": "Borrar la traducción",
              disabled: busy && state.task && state.task.capitulo === c.index, onclick: () => clearChapter(c.index, c.title || `Documento ${c.index}`, c.done) }, svgIcon("papelera")) : null,
            h("button", { class: "small ghost", title: "No traducir este documento (créditos, publicidad…)", onclick: async () => {
              const sum = await guard(() => rpc("set_skipped", c.index, c.status !== "omitido"));
              if (sum) { state.summary = sum; render(); }
            } }, c.status === "omitido" ? "Incluir" : "Omitir"))))))));
}

/* ---------------------------------------------------------------- lectura y voz */

const speech = { active: false, paused: false, queue: [], token: 0 };
const prefs = (() => { try { return JSON.parse(localStorage.getItem("jeronimo-lectura") || "{}"); } catch (e) { return {}; } })();
const savePrefs = () => { try { localStorage.setItem("jeronimo-lectura", JSON.stringify(prefs)); } catch (e) { /* sin almacenamiento */ } };

function stopSpeech() {
  speech.token += 1;
  speech.active = speech.paused = false;
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  document.querySelectorAll(".reader-p.speaking").forEach((p) => p.classList.remove("speaking"));
}

/* Trozos cortos (por frases): Chromium corta las locuciones muy largas. */
function speechChunks(text) {
  const parts = text.match(/[^.!?…]+[.!?…]+[»”"')\]]*\s*|[^.!?…]+$/g) || [text];
  const chunks = [];
  for (const p of parts) {
    if (chunks.length && (chunks[chunks.length - 1] + p).length < 220) chunks[chunks.length - 1] += p;
    else chunks.push(p);
  }
  return chunks.map((c) => c.trim()).filter(Boolean);
}

async function renderReader(v) {
  const index = state.chapter;
  const data = await guard(() => rpc("chapter", index));
  if (!data) return;
  const lang = state.summary.idioma_destino;
  const list = state.summary.capitulos.filter((c) => c.total > 0 && c.status !== "omitido");
  const pos = list.findIndex((c) => c.index === index);
  const prev = pos > 0 ? list[pos - 1] : null;
  const next = pos >= 0 && pos + 1 < list.length ? list[pos + 1] : null;
  const size = prefs.size || 1.25;

  const page = h("article", { class: "reader-page", lang, style: `font-size:${size}rem` });
  const paras = [];
  for (const s of data.segmentos) {
    const heading = /^h\d$/.test(s.tag);
    const el = h(heading ? "h2" : "p", { class: (heading ? "reader-h" : "reader-p") + (s.tgt ? "" : " untranslated"),
      title: s.tgt ? "Haz clic para escuchar desde aquí" : "Sin traducir (se muestra el original)" });
    markedToDom(el, s.tgt || s.src, s.marcas || {});
    el.dataset.n = s.n;
    if (heading) el.classList.add("reader-p");
    page.appendChild(el);
    // Lo que se lee en voz alta: sin las llamadas a notas ni otros elementos fijos.
    const spoken = el.cloneNode(true);
    spoken.querySelectorAll(".mk.x").forEach((x) => x.remove());
    paras.push({ el, text: s.tgt ? spoken.textContent.replace(/\s+/g, " ").trim() : "", translated: !!s.tgt });
  }
  const done = paras.filter((p) => p.translated).length;

  // Voz.
  const synth = window.speechSynthesis;
  const voiceSel = h("select", { style: "width:auto;max-width:280px", onchange: () => { prefs.voice = voiceSel.value; savePrefs(); } });
  const rateSel = h("select", { style: "width:auto", onchange: () => { prefs.rate = Number(rateSel.value); savePrefs(); } },
    [[0.8, "Lenta"], [0.9, "Pausada"], [1, "Normal"], [1.15, "Ágil"], [1.3, "Rápida"]].map(([r, label]) =>
      h("option", { value: r, selected: (prefs.rate || 1) === r }, label)));
  const voiceNote = h("span", { class: "muted small" });
  const fillVoices = () => {
    if (!synth) return;
    const all = synth.getVoices();
    const base = lang.split("-")[0].toLowerCase();
    const mine = all.filter((x) => x.lang.toLowerCase().startsWith(base));
    voiceSel.textContent = "";
    mine.forEach((x) => voiceSel.appendChild(h("option", { value: x.name, selected: x.name === prefs.voice },
      x.name.replace(/^Microsoft /, "").replace(/ Online \(Natural\)/, " (natural)"))));
    voiceSel.hidden = !mine.length;
    voiceNote.textContent = mine.length ? "" : (all.length
      ? `Windows no tiene voces instaladas para «${lang}». Añádelas en Configuración → Hora e idioma → Voz.`
      : "Cargando voces…");
  };
  if (synth) {
    fillVoices();
    synth.onvoiceschanged = fillVoices;
  }

  const playBtn = h("button", { class: "gold", disabled: !synth || !done }, "▶ Escuchar");
  const pauseBtn = h("button", { class: "ghost", disabled: true }, "Pausa");
  const stopBtn = h("button", { class: "ghost", disabled: true }, "Detener");
  const setButtons = () => {
    playBtn.textContent = speech.active && speech.paused ? "▶ Seguir" : "▶ Escuchar";
    playBtn.disabled = !synth || !done || (speech.active && !speech.paused);
    pauseBtn.disabled = !speech.active || speech.paused;
    stopBtn.disabled = !speech.active;
  };

  const speakFrom = (start) => {
    stopSpeech();
    const token = speech.token;
    speech.active = true;
    setButtons();
    const voice = synth.getVoices().find((x) => x.name === voiceSel.value);
    let k = start;
    const nextPara = () => {
      while (k < paras.length && !paras[k].text) k += 1;
      if (token !== speech.token) return;
      if (k >= paras.length) { stopSpeech(); setButtons(); return; }
      const p = paras[k];
      document.querySelectorAll(".reader-p.speaking").forEach((x) => x.classList.remove("speaking"));
      p.el.classList.add("speaking");
      p.el.scrollIntoView({ block: "center", behavior: "smooth" });
      const chunks = speechChunks(p.text);
      let c = 0;
      const sayNext = () => {
        if (token !== speech.token) return;
        if (c >= chunks.length) { k += 1; nextPara(); return; }
        const u = new SpeechSynthesisUtterance(chunks[c]);
        c += 1;
        u.lang = voice ? voice.lang : lang;
        if (voice) u.voice = voice;
        u.rate = Number(rateSel.value) || 1;
        u.onend = sayNext;
        u.onerror = (e) => { if (e.error !== "interrupted" && e.error !== "canceled") sayNext(); };
        synth.speak(u);
      };
      sayNext();
    };
    nextPara();
  };

  playBtn.onclick = () => {
    if (speech.active && speech.paused) {
      synth.resume();
      speech.paused = false;
      setButtons();
      return;
    }
    const current = paras.findIndex((p) => p.el.classList.contains("speaking"));
    speakFrom(current >= 0 ? current : 0);
  };
  pauseBtn.onclick = () => { synth.pause(); speech.paused = true; setButtons(); };
  stopBtn.onclick = () => { stopSpeech(); setButtons(); };
  paras.forEach((p, k) => p.el.addEventListener("click", () => { if (synth && p.text) speakFrom(k); }));

  const resize = (delta) => {
    prefs.size = Math.min(1.8, Math.max(0.95, Math.round(((prefs.size || 1.25) + delta) * 100) / 100));
    savePrefs();
    page.style.fontSize = `${prefs.size}rem`;
  };

  put(v,
    h("div", { class: "review-head reader-head" },
      h("div", { class: "row" },
        h("button", { class: "small ghost", onclick: () => go("revision", index) }, "‹ Revisión"),
        h("div", { style: "flex:1;min-width:0" }, h("div", { class: "eyebrow", style: "margin:0 0 2px" }, "Lectura"),
          h("h1", { style: "margin:0" }, data.titulo)),
        h("span", { class: "muted small" }, `${done} de ${data.total} párrafos traducidos`),
        h("button", { class: "small ghost", title: "Letra más pequeña", onclick: () => resize(-0.1) }, "A−"),
        h("button", { class: "small ghost", title: "Letra más grande", onclick: () => resize(0.1) }, "A+")),
      h("div", { class: "row voice-bar" },
        playBtn, pauseBtn, stopBtn,
        h("span", { class: "muted small" }, "Voz"), voiceSel, voiceNote,
        h("span", { class: "muted small" }, "Velocidad"), rateSel,
        synth ? null : h("span", { class: "muted small" }, "La lectura en voz alta no está disponible en este navegador."))),
    h("p", { class: "muted small reader-tip" }, "Consejo: escuchar la traducción ayuda a detectar frases torpes, repeticiones y errores que leyendo se escapan. Haz clic en un párrafo para escuchar desde ahí."),
    page,
    h("div", { class: "row", style: "justify-content:space-between;max-width:760px;margin:22px auto 0" },
      prev ? h("button", { class: "ghost", onclick: () => go("lectura", prev.index) }, `‹ ${prev.title || "Anterior"}`) : h("span"),
      next ? h("button", { class: "ghost", onclick: () => go("lectura", next.index) }, `${next.title || "Siguiente"} ›`) : h("span")));
  setButtons();
}

/* ---------------------------------------------------------------- borrar y recuperar la traducción de un capítulo */

async function clearChapter(index, title, done) {
  const ok = await modal(h("div", {},
    h("div", { class: "eyebrow" }, "Borrar traducción"),
    h("h3", {}, title),
    h("p", {}, `Se borrarán los ${done === 1 ? "1 párrafo traducido" : `${fmt(done)} párrafos traducidos`} de este capítulo, también tus correcciones, y dejará de estar aprobado.`),
    h("p", { class: "muted small" }, "Se guarda una copia: si te arrepientes, puedes recuperarla desde la revisión del capítulo.")),
  { ok: "Borrar traducción", okClass: "danger" });
  if (!ok) return;
  const r = await guard(() => rpc("clear_translation", index));
  if (!r) return;
  state.summary = r.resumen;
  toast(`Traducción borrada: ${fmt(r.borrados)} párrafos.`);
  render();
}

async function restoreChapter(index) {
  const r = await guard(() => rpc("restore_translation", index));
  if (!r) return;
  state.summary = r.resumen;
  toast(r.recuperados ? `Traducción recuperada: ${fmt(r.recuperados)} párrafos.` : "No había párrafos vacíos que recuperar.");
  render();
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
  const quality = await rpc("quality", index).catch(() => null);
  const qaBySeg = {};
  if (quality) {
    qaTypes = quality.tipos;
    // Los avisos de formato ya se muestran en cada párrafo.
    quality.problemas.filter((p) => p.tipo !== "formato").forEach((p) => (qaBySeg[p.n] = qaBySeg[p.n] || []).push(p));
  }
  if (state.focusSeg !== null && state.focusSeg !== undefined) reviewFilter = "todos";

  const filterSel = h("select", { style: "width:auto", onchange: (e) => { reviewFilter = e.target.value; drawRows(); } },
    h("option", { value: "todos", selected: reviewFilter === "todos" }, "Todos los párrafos"),
    h("option", { value: "avisos", selected: reviewFilter === "avisos" }, "Con avisos o notas"),
    h("option", { value: "pendientes", selected: reviewFilter === "pendientes" }, "Sin traducir"),
    h("option", { value: "sugerencias", selected: reviewFilter === "sugerencias" }, "Con sugerencias de revisión"));

  const rows = h("div", {});
  const drawRows = () => {
    rows.textContent = "";
    const segs = data.segmentos.filter((s) => reviewFilter === "todos" ||
      (reviewFilter === "pendientes" ? !s.tgt : reviewFilter === "sugerencias" ? s.sugerencia
        : (s.avisos.length || s.nota || qaBySeg[s.n])));
    if (!segs.length) rows.appendChild(h("div", { class: "card empty-state" }, "No hay párrafos con este filtro."));
    segs.forEach((s) => rows.appendChild(segmentRow(data, s, qaBySeg[s.n] || [])));
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
        h("button", { class: "small " + (approved ? "ghost" : "gold"), disabled: data.hechos < data.total || data.bloqueado, onclick: async () => {
          const d = await guard(() => rpc("set_approved", index, !approved));
          if (!d) return;
          await refreshSummary();
          if (!approved) toast("Capítulo aprobado. Sus párrafos servirán de ejemplo para los siguientes.");
          render();
        } }, approved ? "Quitar aprobación" : "Aprobar capítulo"),
        next ? h("button", { class: "small ghost", onclick: () => go("revision", next.index) }, "Siguiente ›") : null),
      h("div", { class: "row tools-row" },
        !manualMode && data.hechos < data.total && !data.omitir ? h("button", { class: "small", disabled: busy, onclick: () => startTask(() => rpc("translate", "capitulo", index)) }, "Traducir lo pendiente") : null,
        h("button", { class: "small ghost", onclick: () => { const p = $("#manual-panel"); p.hidden = !p.hidden; } }, "Modo manual"),
        data.hechos ? h("button", { class: "small ghost", title: "Una IA relee la traducción y propone correcciones", onclick: () => {
          const p = $("#review-panel");
          p.hidden = !p.hidden;
          state.reviewOpen = !p.hidden;
        } }, "Revisión con IA") : null,
        h("button", { class: "small ghost", title: "Leer el capítulo como en el lector y escucharlo en voz alta", onclick: () => go("lectura", index) }, svgIcon("libro"), "Leer y escuchar"),
        h("div", { class: "spacer" }),
        data.hechos ? h("button", { class: "small ghost danger-text", disabled: data.bloqueado, title: "Borrar toda la traducción de este capítulo",
          onclick: () => clearChapter(index, data.titulo, data.hechos) }, svgIcon("papelera"), "Borrar traducción") : null),
      data.copia_borrada ? h("div", { class: "note row", style: "margin-top:8px;padding:8px 12px" },
        h("span", { style: "flex:1" }, "Hay una traducción borrada de este capítulo."),
        h("button", { class: "small ghost", disabled: data.bloqueado, onclick: () => restoreChapter(index) }, "Recuperarla")) : null,
      data.bloqueado ? h("div", { class: "warn", style: "margin-top:8px" }, "Este capítulo se está traduciendo. La vista se actualiza sola; podrás editar al terminar.") : null,
      data.resumen ? h("details", { style: "margin-top:8px" }, h("summary", { class: "muted small" }, "Resumen del capítulo"), h("p", { class: "small" }, data.resumen)) : null,
      h("div", { class: "seg-cols" }, h("span", {}, `Original · ${state.summary.idioma_origen}`), h("span", {}, `Traducción · ${state.summary.idioma_destino}`))),
    manualPanel(index),
    data.hechos ? reviewPanel(index, data) : null,
    rows);
  if (state.focusSeg !== null && state.focusSeg !== undefined) {
    const el = $(`#seg-${state.focusSeg}`);
    state.focusSeg = null;
    if (el) {
      el.scrollIntoView({ block: "center" });
      el.classList.add("flash");
    }
  }
}

let qaTypes = {};

function qaChip(data, s, p) {
  const chip = h("span", { class: "qa " + p.nivel, title: (qaTypes[p.tipo] || {}).nombre || "" }, p.mensaje);
  chip.appendChild(h("button", { class: "qa-x", title: "Ignorar este aviso", onclick: async () => {
    if (await guard(() => rpc("quality_ignore", data.index, s.n, p.tipo, p.clave || ""))) chip.remove();
  } }, "×"));
  return chip;
}

function segmentRow(data, s, issues = []) {
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
    issues.map((p) => qaChip(data, s, p)),
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

  const flagged = s.avisos.length || issues.some((p) => p.nivel === "error");
  let suggestion = null;
  if (s.sugerencia) {
    const resolve = async (accept) => {
      const st = await guard(() => rpc("review_resolve", data.index, s.n, accept));
      if (!st) return;
      if (accept) {
        s.tgt = s.sugerencia.texto;
        saved = normalizeMarked(s.tgt);
        markedToDom(tgt, s.tgt, marks);
        tgt.classList.remove("empty");
        status.textContent = "✓ corrección aceptada";
        status.className = "small saved";
      }
      s.sugerencia = null;
      suggestion.remove();
      updateReviewStatus(st);
    };
    suggestion = h("div", { class: "suggestion" },
      h("div", { class: "row" },
        h("span", { class: "eyebrow", style: "margin:0" }, "Sugerencia de la revisión"),
        s.sugerencia.motivo ? h("span", { class: "muted small", style: "font-style:italic" }, s.sugerencia.motivo) : null),
      diffView(s.tgt || "", s.sugerencia.texto, marks),
      (s.sugerencia.avisos || []).map((w) => h("span", { class: "warn small" }, w)),
      h("div", { class: "row" },
        h("button", { class: "small gold", disabled: data.bloqueado, onclick: () => resolve(true) }, "Aceptar"),
        h("button", { class: "small ghost", disabled: data.bloqueado, onclick: () => resolve(false) }, "Descartar")));
  }
  const row = h("div", { id: `seg-${s.n}`, class: "seg" + (!s.tgt ? " pending" : flagged ? " flagged" : "") }, src, tgt, suggestion, meta);
  return row;
}

/* Diferencias palabra a palabra entre la traducción y la sugerencia. */
function plainOf(marked, marks) {
  const el = h("div", {});
  markedToDom(el, marked || "", marks || {});
  el.querySelectorAll(".mk.x").forEach((x) => x.remove());
  return el.textContent.replace(/\s+/g, " ").trim();
}

function wordDiff(a, b) {
  const A = a.split(/(\s+)/).filter((t) => t !== "");
  const B = b.split(/(\s+)/).filter((t) => t !== "");
  const n = A.length;
  const m = B.length;
  if (n * m > 250000) return [{ type: "del", text: a }, { type: "ins", text: " " + b }];
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  }
  const out = [];
  const push = (type, text) => {
    const last = out[out.length - 1];
    if (last && last.type === type) last.text += text;
    else out.push({ type, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { push("eq", A[i]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) push("del", A[i++]);
    else push("ins", B[j++]);
  }
  while (i < n) push("del", A[i++]);
  while (j < m) push("ins", B[j++]);
  return out;
}

function diffView(before, after, marks) {
  const parts = wordDiff(plainOf(before, marks), plainOf(after, marks));
  return h("div", { class: "diff" }, parts.map((p) => (p.type === "eq" ? p.text
    : h(p.type === "del" ? "del" : "ins", { class: "d-" + p.type }, p.text))));
}

let updateReviewStatus = () => {};

function reviewPanel(index, data) {
  const manual = state.summary.proveedor.tipo === "manual";
  const busy = state.task && state.task.activa;
  const counter = h("strong", {}, "…");
  const bulk = h("div", { class: "row" });
  const out = h("textarea", { placeholder: "Pega aquí la respuesta completa del chat (el bloque de código con las correcciones <fix …>)." });
  const info = h("p", { class: "muted small", style: "margin:0" });
  let current = null;

  updateReviewStatus = (st) => {
    current = st;
    counter.textContent = st.por_revisar
      ? `${plural(st.por_revisar, "párrafo sin revisar", "párrafos sin revisar")} · ${plural(st.sugerencias, "sugerencia pendiente", "sugerencias pendientes")}`
      : st.sugerencias ? `Capítulo revisado · ${plural(st.sugerencias, "sugerencia pendiente", "sugerencias pendientes")}` : "Capítulo revisado: no quedan sugerencias pendientes.";
    bulk.textContent = "";
    if (st.sugerencias) {
      put(bulk,
        h("button", { class: "small ghost", onclick: () => resolveAll(true) }, "Aceptar todas"),
        h("button", { class: "small ghost danger-text", onclick: () => resolveAll(false) }, "Descartar todas"));
    }
    if (!st.por_revisar && st.revisados) {
      put(bulk, h("button", { class: "small ghost", title: "Marcar todo el capítulo como pendiente de revisar", onclick: async () => {
        const r = await guard(() => rpc("review_reset", index));
        if (r) updateReviewStatus(r);
      } }, "Revisar todo otra vez"));
    }
    if (reviewBtn) reviewBtn.disabled = !st.por_revisar || busy || data.bloqueado;
  };

  const resolveAll = async (accept) => {
    if (!accept && !(await modal(`¿Descartar las ${current.sugerencias} sugerencias de este capítulo?`, { ok: "Descartar todas" }))) return;
    const st = await guard(() => rpc("review_resolve", index, null, accept));
    if (!st) return;
    toast(accept ? `${plural(st.resueltas, "corrección aceptada", "correcciones aceptadas")}.` : "Sugerencias descartadas.");
    state.reviewOpen = true;
    await refreshSummary();
    render();
  };

  const copyNext = async () => {
    const p = await guard(() => rpc("review_manual_prompt", index));
    if (p === undefined) return false;
    if (p === null) {
      toast("No quedan párrafos por revisar en este capítulo.");
      return false;
    }
    info.textContent = `Bloque copiado: párrafos ${p.ids[0]}–${p.ids[p.ids.length - 1]} (${fmt(p.caracteres)} caracteres).`;
    if (await copyText(p.texto)) toast("Bloque de revisión copiado. Pégalo en tu chat de IA.");
    return true;
  };

  const doImport = async (thenCopy) => {
    if (!out.value.trim()) return toast("Pega primero la respuesta del chat.", true);
    const r = await guard(() => rpc("review_manual_import", index, out.value));
    if (!r) return;
    out.value = "";
    toast(r.sugerencias ? `${plural(r.sugerencias, "sugerencia", "sugerencias")} en ${plural(r.revisados, "párrafo revisado", "párrafos revisados")}.`
      : `Sin correcciones en ${plural(r.revisados, "párrafo revisado", "párrafos revisados")}.`);
    if (thenCopy && r.por_revisar) await copyNext();
    state.reviewOpen = true;
    render();
  };

  const reviewBtn = manual ? null : h("button", { class: "gold", onclick: () => {
    state.reviewOpen = true;
    startTask(() => rpc("review_start", index));
  } }, "Revisar con IA");

  const panel = h("div", { id: "review-panel", class: "card manual stack", hidden: !state.reviewOpen },
    h("div", { class: "eyebrow", style: "margin:0" }, "Segunda pasada"),
    h("h3", { style: "margin-top:0" }, "Revisión con IA"),
    h("p", { class: "small", style: "margin:0" }, "Una IA relee la traducción junto al original, con la guía de estilo y el glosario, y propone correcciones: errores de sentido, omisiones, glosario, tuteo o usted, frases poco naturales y puntuación. Nada se cambia solo: cada sugerencia aparece bajo su párrafo para aceptarla o descartarla."),
    h("div", { class: "row" }, counter, h("div", { class: "spacer" }), bulk),
    manual
      ? [h("div", { class: "row" }, h("button", { onclick: copyNext }, "Copiar bloque de revisión"), info), out,
        h("div", { class: "row end" },
          h("button", { class: "ghost", onclick: () => doImport(false) }, "Solo importar"),
          h("button", { class: "gold", onclick: () => doImport(true) }, "Importar y copiar el siguiente"))]
      : h("div", { class: "row" }, reviewBtn, h("span", { class: "muted small" }, "Revisa los párrafos que aún no se revisaron; usa la IA configurada.")));
  rpc("review_status", index).then((st) => {
    updateReviewStatus(st);
    if (st.sugerencias && !state.reviewOpen) { panel.hidden = false; }
  }).catch(() => {});
  return panel;
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
    state.summary.bloques_livianos ? h("div", { class: (state.summary.paquete && state.summary.paquete.desactualizado) ? "warn small" : "note small" },
      "Bloques livianos: pega cada bloque en un chat abierto dentro del proyecto donde subiste el paquete."
      + (state.summary.paquete && state.summary.paquete.desactualizado ? " El paquete está desactualizado: vuelve a generarlo en «Inteligencia artificial»." : "")) : null,
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

/* ---------------------------------------------------------------- control de calidad */

async function renderQuality(v) {
  const r = await guard(() => rpc("quality"));
  if (!r) return;
  let filter = r.resumen[state.qaFilter] ? state.qaFilter : "";
  const chips = h("div", { class: "qa-summary" });
  const list = h("div", {});
  const recount = () => {
    r.resumen = {};
    r.problemas.forEach((p) => { r.resumen[p.tipo] = (r.resumen[p.tipo] || 0) + 1; });
    if (!r.resumen[filter]) filter = "";
  };
  const draw = () => {
    chips.textContent = "";
    const total = r.problemas.length;
    chips.append(h("button", { class: "qa-chip" + (filter ? "" : " active"), onclick: () => { filter = state.qaFilter = ""; draw(); } },
      h("span", { class: "count" }, fmt(total)), "Todos"));
    for (const [k, t] of Object.entries(r.tipos)) {
      if (!r.resumen[k]) continue;
      chips.append(h("button", { class: `qa-chip ${t.nivel}` + (filter === k ? " active" : ""), onclick: () => { filter = state.qaFilter = k; draw(); } },
        h("span", { class: "count" }, fmt(r.resumen[k])), t.nombre));
    }
    list.textContent = "";
    const items = r.problemas.filter((p) => !filter || p.tipo === filter);
    if (!items.length) {
      list.append(h("div", { class: "card empty-state" }, h("div", { class: "ornament" }, h("span")),
        r.parrafos ? `No hay avisos en los ${fmt(r.parrafos)} párrafos traducidos.` : "Todavía no hay párrafos traducidos para revisar."));
      return;
    }
    const groups = new Map();
    items.forEach((p) => { if (!groups.has(p.doc)) groups.set(p.doc, []); groups.get(p.doc).push(p); });
    for (const [doc, ps] of groups) {
      list.append(h("div", { class: "card qa-group" },
        h("h3", {}, ps[0].titulo, h("span", { class: "muted small qa-count" }, ` · ${ps.length === 1 ? "1 aviso" : `${fmt(ps.length)} avisos`}`)),
        ps.slice(0, 300).map((p) => h("div", { class: "qa-row" },
          h("span", { class: "dot " + p.nivel, title: p.nivel === "error" ? "Probable error" : "Para revisar" }),
          h("div", { style: "min-width:0" },
            h("div", {}, h("strong", {}, r.tipos[p.tipo].nombre), h("span", { class: "muted" }, ` · §${p.n}`)),
            h("div", {}, p.mensaje),
            p.fragmento ? h("div", { class: "frag" }, p.fragmento) : null),
          h("div", { class: "row", style: "flex-wrap:nowrap" },
            h("button", { class: "small ghost", onclick: () => { state.focusSeg = p.n; go("revision", doc); } }, "Revisar"),
            h("button", { class: "small ghost", title: "No volver a avisar de esto", onclick: async () => {
              if (!(await guard(() => rpc("quality_ignore", p.doc, p.n, p.tipo, p.clave || "")))) return;
              r.problemas = r.problemas.filter((x) => x !== p && !(p.clave && x.clave === p.clave));
              recount();
              draw();
            } }, "Ignorar"))))));
    }
  };
  draw();
  put(v,
    pageHead("Paso 4 de 5 · Revisión", "Control de calidad",
      "Revisa toda la traducción sin gastar IA: términos del glosario, párrafos que pueden haber quedado incompletos, palabras en el idioma original, números, signos de apertura, rayas de diálogo y nombres escritos de varias formas. Son avisos para revisar: si alguno no es un error, ignóralo."),
    h("div", { class: "row" },
      h("span", { class: "muted small" }, h("span", { class: "dot error" }), " probable error   ", h("span", { class: "dot aviso" }), " para revisar"),
      h("div", { class: "spacer" }),
      h("span", { class: "muted small" }, `${fmt(r.parrafos)} párrafos revisados`),
      h("button", { class: "ghost small", onclick: () => render() }, "Volver a revisar")),
    chips, list);
}

/* ---------------------------------------------------------------- buscar y reemplazar */

function renderSearch(v) {
  const pre = state.searchPrefill || {};
  state.searchPrefill = null;
  // Lo que llega del glosario se busca siempre en la traducción y sin distinguir mayúsculas.
  const last = pre.buscar !== undefined ? {} : state.lastSearch || {};
  const query = h("input", { type: "search", placeholder: "Palabra o frase", value: pre.buscar ?? last.buscar ?? "",
    onkeydown: (e) => { if (e.key === "Enter") run(); } });
  const repl = h("input", { type: "text", placeholder: "Texto nuevo (opcional)", value: pre.reemplazar ?? last.reemplazar ?? "",
    onkeydown: (e) => { if (e.key === "Enter") run(); } });
  const caseBox = h("input", { type: "checkbox", checked: !!last.mayusculas });
  const wordBox = h("input", { type: "checkbox", checked: pre.palabra ?? !!last.palabra });
  const where = h("select", { style: "width:auto" },
    h("option", { value: "traduccion", selected: last.en !== "original" }, "en la traducción"),
    h("option", { value: "original", selected: last.en === "original" }, "en el original"));
  const results = h("div", {});
  const undoBox = h("div", {});
  const options = () => ({ mayusculas: caseBox.checked, palabra: wordBox.checked, en: where.value });

  const drawUndo = (info) => {
    undoBox.textContent = "";
    if (!info) return;
    put(undoBox, h("div", { class: "note row", style: "padding:8px 12px;margin-top:12px" },
      h("span", { style: "flex:1" }, `Último reemplazo: «${info.buscar}» → «${info.reemplazar}» en ${info.parrafos === 1 ? "1 párrafo" : `${fmt(info.parrafos)} párrafos`}.`),
      h("button", { class: "small ghost", onclick: async () => {
        const r = await guard(() => rpc("undo_replace"));
        if (!r) return;
        toast(`Reemplazo deshecho en ${fmt(r.restaurados)} párrafos.` + (r.omitidos ? ` ${fmt(r.omitidos)} ya se habían vuelto a editar y no se tocaron.` : ""));
        await refreshSummary();
        run();
      } }, "Deshacer")));
  };

  async function run() {
    const q = query.value.trim();
    if (!q) return toast("Escribe qué quieres buscar.", true);
    state.lastSearch = Object.assign({ buscar: q, reemplazar: repl.value }, options());
    const r = await guard(() => rpc("find_text", q, options()));
    if (!r) return;
    drawResults(r);
  }

  function drawResults(r) {
    results.textContent = "";
    drawUndo(r.deshacer);
    const inSource = where.value === "original";
    if (!r.resultados.length) {
      results.append(h("div", { class: "card empty-state" }, `No se encontró «${query.value.trim()}» ${inSource ? "en el original" : "en la traducción"}.`));
      return;
    }
    const checks = [];
    const count = h("strong", {});
    const replaceBtn = h("button", { class: "gold", onclick: async () => {
      const chosen = checks.filter((c) => c.box.checked).map((c) => [c.doc, c.n]);
      if (!chosen.length) return toast("Marca al menos un párrafo.", true);
      const newText = repl.value;
      const ok = await modal(h("div", {},
        h("div", { class: "eyebrow" }, "Reemplazar"),
        h("p", {}, `¿Cambiar «${query.value.trim()}» por «${newText}» en ${chosen.length === 1 ? "1 párrafo" : `${fmt(chosen.length)} párrafos`}?`),
        newText ? null : h("p", { class: "warn small" }, "El texto nuevo está vacío: se borrará lo encontrado."),
        h("p", { class: "muted small" }, "El formato se conserva y podrás deshacerlo.")), { ok: "Reemplazar" });
      if (!ok) return;
      const res = await guard(() => rpc("replace_text", query.value.trim(), newText, options(), chosen));
      if (!res) return;
      toast(`${plural(res.reemplazos, "reemplazo", "reemplazos")} en ${plural(res.parrafos, "párrafo", "párrafos")}.`);
      await refreshSummary();
      run();
    } }, "Reemplazar en los marcados");
    const updateCount = () => {
      const n = checks.filter((c) => c.box.checked).length;
      count.textContent = plural(n, "marcado", "marcados");
      replaceBtn.disabled = !n;
    };
    const groups = new Map();
    r.resultados.forEach((x) => { if (!groups.has(x.doc)) groups.set(x.doc, []); groups.get(x.doc).push(x); });
    const cards = [];
    for (const [doc, xs] of groups) {
      cards.push(h("div", { class: "card qa-group" },
        h("h3", {}, xs[0].titulo),
        xs.map((x) => {
          const box = h("input", { type: "checkbox", checked: true, onchange: updateCount });
          if (!inSource) checks.push({ box, doc, n: x.n });
          const f = x.fragmento;
          return h("div", { class: "qa-row search-row" },
            inSource ? h("span", { class: "muted small" }, `§${x.n}`) : h("label", { class: "row", style: "margin:0;gap:8px" }, box, h("span", { class: "muted small" }, `§${x.n}`)),
            h("div", { style: "min-width:0" },
              h("div", { class: "frag" }, f.antes, h("mark", {}, f.texto), f.despues),
              x.coincidencias > 1 ? h("div", { class: "muted small" }, `${fmt(x.coincidencias)} veces en este párrafo`) : null,
              inSource ? h("div", { class: "small", style: "margin-top:4px" }, h("span", { class: "muted" }, "Traducción: "), x.traduccion || h("em", { class: "muted" }, "sin traducir")) : null),
            h("button", { class: "small ghost", onclick: () => { state.focusSeg = x.n; go("revision", doc); } }, "Revisar"));
        })));
    }
    put(results,
      h("div", { class: "row", style: "margin:18px 0 4px" },
        h("span", {}, h("strong", {}, plural(r.coincidencias, "coincidencia", "coincidencias")), ` en ${plural(r.parrafos, "párrafo", "párrafos")}` + (r.truncado ? " (se muestran los primeros)" : "")),
        h("div", { class: "spacer" }),
        inSource ? null : h("button", { class: "small ghost", onclick: () => { const all = checks.some((c) => !c.box.checked); checks.forEach((c) => { c.box.checked = all; }); updateCount(); } }, "Marcar o desmarcar todo"),
        inSource ? null : count,
        inSource ? null : replaceBtn),
      cards);
    if (!inSource) updateCount();
  }

  put(v,
    pageHead("Herramientas", "Buscar y reemplazar",
      "Busca una palabra o frase en todo el libro, en la traducción o en el original. Puedes reemplazarla en los párrafos que marques: las cursivas, notas e imágenes se conservan y el último reemplazo se puede deshacer."),
    h("div", { class: "card stack" },
      h("div", { class: "grid2", style: "margin:0" },
        h("div", { class: "field", style: "margin:0" }, h("label", {}, "Buscar"), query),
        h("div", { class: "field", style: "margin:0" }, h("label", {}, "Reemplazar por ", h("span", { class: "hint" }, "(solo en la traducción)")), repl)),
      h("div", { class: "row" },
        h("label", { class: "row", style: "margin:0;gap:8px;font-weight:500" }, caseBox, "Distinguir mayúsculas"),
        h("label", { class: "row", style: "margin:0;gap:8px;font-weight:500" }, wordBox, "Palabra completa"),
        h("span", { class: "muted small" }, "Buscar"), where,
        h("div", { class: "spacer" }),
        h("button", { onclick: run }, "Buscar"))),
    undoBox, results);
  if (query.value.trim()) run();
  else query.focus();
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
  const qaBox = h("div", {});
  rpc("quality").then((q) => {
    const errors = q.problemas.filter((p) => p.nivel === "error").length;
    if (errors) {
      put(qaBox, h("div", { class: "warn", style: "margin-top:12px" },
        `El control de calidad encontró ${errors === 1 ? "1 posible error" : `${fmt(errors)} posibles errores`}. `,
        h("button", { class: "link inline", onclick: () => go("calidad") }, "Revisarlos")));
    }
  }).catch(() => {});
  const missing = s.progreso.total - s.progreso.hechos;
  put(v,
    pageHead("Paso 5 de 5", "Exportar", "Genera el EPUB con la traducción. Conserva imágenes, índice y estilos del original; la portada puede ser la original o una nueva con el título traducido."),
    missing ? h("div", { class: "warn" }, `Aún faltan ${fmt(missing)} párrafos: en el EPUB quedarán en el idioma original.`) : null,
    qaBox,
    coverCard(),
    otherFormatsCard(),
    h("div", { class: "card stack" },
      h("div", { class: "field" }, h("label", {}, "Título de la edición traducida ", h("span", { class: "hint" }, "(el que muestra el lector en la biblioteca)")),
        h("div", { class: "path" }, title, h("button", { class: "ghost", onclick: async () => {
          const sum = await guard(() => rpc("set_settings", { titulo_traducido: title.value.trim() }));
          if (sum) { state.summary = sum; toast("Título guardado."); }
        } }, "Guardar"))),
      h("div", { class: "row" },
        h("button", { class: "gold", onclick: () => run(false) }, "Generar EPUB traducido"),
        h("button", { class: "ghost", onclick: () => run(true) }, "Generar EPUB bilingüe"))),
    result);
}

/* ---------------------------------------------------------------- Word y PDF */

function otherFormatsCard() {
  const busy = state.task && state.task.activa;
  const last = state.lastTask && /^Exportar a (Word|PDF)/.test(state.lastTask.nombre) ? state.lastTask : null;
  const r = last && last.resultado;
  const btn = (kind, label) => h("button", { class: "ghost", disabled: busy, onclick: () => startTask(() => rpc("export_document", kind)) }, label);
  return h("div", { class: "card stack" },
    cardHead("pdf", "Otros formatos"),
    h("p", { class: "muted", style: "margin-bottom:0" }, "Para correctores, editoriales o para imprimir. Conservan cursivas, negritas y llamadas a notas; las imágenes van solo en el EPUB."),
    h("div", { class: "row" },
      btn("docx", "Word (.docx)"),
      btn("docx-bilingue", "Word bilingüe (tabla)"),
      btn("pdf-a5", "PDF tamaño libro (A5)"),
      btn("pdf-a4", "PDF A4")),
    r ? h("div", { class: "result" },
      h("p", {}, h("strong", {}, "Listo: "), r.ruta),
      h("button", { class: "ghost small", onclick: () => rpc("open_folder", r.ruta) }, "Abrir carpeta")) : null);
}

/* ---------------------------------------------------------------- portada traducida */

function coverCard() {
  const box = h("div", { class: "card" }, h("p", { class: "muted" }, "Cargando portada…"));
  guard(() => rpc("cover_settings")).then((cfg) => {
    if (!cfg) return;
    box.textContent = "";
    const use = h("input", { type: "checkbox", checked: cfg.usar });
    const style = h("select", { style: "width:auto" },
      h("option", { value: "difuminada", selected: cfg.estilo === "difuminada", disabled: !cfg.tiene_original }, "Con la portada original difuminada de fondo"),
      h("option", { value: "lisa", selected: cfg.estilo === "lisa" || !cfg.tiene_original }, "Fondo azul tinta con filetes dorados"));
    const title = h("input", { type: "text", value: cfg.titulo, placeholder: "Título de la edición traducida" });
    const author = h("input", { type: "text", value: cfg.autor, placeholder: "Autor" });
    const imgNew = h("img", { class: "cover-img", alt: "Portada nueva" });
    const imgOld = h("img", { class: "cover-img", alt: "Portada original" });
    const warn = h("div", {});
    const values = () => ({ usar: use.checked, estilo: style.value, titulo: title.value, autor: author.value });
    let timer = null;
    const refresh = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        await guard(() => rpc("set_cover", values()));
        const pv = await guard(() => rpc("cover_preview", values()));
        if (!pv) return;
        imgNew.src = pv.nueva;
        if (pv.original) imgOld.src = pv.original;
        warn.textContent = "";
        if (pv.faltan.length) {
          put(warn, h("div", { class: "warn small" }, `La tipografía de la portada no tiene estos caracteres: ${pv.faltan.join(" ")}. Se mantendrá la portada original.`));
        }
        newFig.classList.toggle("off", !use.checked);
      }, 350);
    };
    [use, style].forEach((el) => el.addEventListener("change", refresh));
    [title, author].forEach((el) => el.addEventListener("input", refresh));
    const newFig = h("figure", { class: "cover-fig" }, imgNew, h("figcaption", {}, "Nueva"));
    put(box,
      cardHead("libro", "Portada"),
      h("div", { class: "cover-layout" },
        h("div", { class: "stack", style: "flex:1;min-width:280px" },
          h("label", { class: "row", style: "gap:8px;font-weight:600;margin:0" }, use, "Usar una portada nueva con el título traducido"),
          h("p", { class: "muted small", style: "margin-bottom:0" }, cfg.tiene_original
            ? "Se reemplaza la imagen de portada del EPUB (en el lector y en la biblioteca). La edición bilingüe conserva siempre la original."
            : "El libro no tiene portada: se añadirá esta."),
          h("div", { class: "field", style: "margin-bottom:0" }, h("label", {}, "Título en la portada"), title),
          h("div", { class: "field", style: "margin-bottom:0" }, h("label", {}, "Autor en la portada"), author),
          h("div", { class: "field", style: "margin-bottom:0" }, h("label", {}, "Estilo"), style),
          warn),
        h("div", { class: "cover-previews" },
          cfg.tiene_original ? h("figure", { class: "cover-fig" }, imgOld, h("figcaption", {}, "Original")) : null,
          newFig)));
    refresh();
  });
  return box;
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
  checkUpdate();
})();
