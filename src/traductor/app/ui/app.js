"use strict";

/* Traductor de Libros — interfaz. Habla con Python por pywebview (ventana)
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

const $ = (sel, root = document) => root.querySelector(sel);
const view = () => $("#view");
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
  if (!(await modal("¿Cerrar Traductor de Libros? El progreso ya está guardado.", { ok: "Salir" }))) return;
  if (await guard(() => rpc("quit_app"))) {
    document.body.innerHTML = "<p style='padding:40px;font-size:1.2rem'>Puedes cerrar esta pestaña.</p>";
  }
};

function setChrome() {
  const open = !!state.summary;
  $("#btn-quit").hidden = !(state.info && state.info.modo === "navegador");
  $("#sidebar").hidden = !open;
  $("#btn-close-project").hidden = !open;
  $("#project-name").textContent = open ? `— ${state.summary.nombre}` : "";
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
  v.scrollTop = 0;
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

  v.append(
    h("h1", {}, "Traduce libros conservando el estilo oficial"),
    h("p", { class: "lead" }, "Crea un proyecto con el EPUB que quieres traducir, añade libros del mismo autor junto con su traducción oficial y la IA imitará la voz de ese traductor."),
    h("div", { class: "grid2" },
      h("div", { class: "card stack" },
        h("h2", { style: "margin-top:0" }, "Nuevo proyecto"),
        h("div", { class: "field" }, h("label", {}, "Libro a traducir ", h("span", { class: "hint" }, "(EPUB sin DRM)")), pathField("new-epub", "C:\\Libros\\mi-libro.epub")),
        h("div", { class: "row" },
          h("div", { class: "field", style: "flex:1" }, h("label", {}, "Traducir al"), langSelect),
          h("div", { class: "field", style: "flex:1" }, h("label", {}, "Variante"), variant)),
        h("div", { class: "field" }, h("label", {}, "Carpeta del proyecto ", h("span", { class: "hint" }, "(opcional)")),
          pathField("new-folder", info.carpeta_por_defecto + "\\…", "carpeta")),
        h("div", { class: "row end" }, h("button", {
          onclick: async () => {
            const epub = $("#new-epub").value.trim();
            if (!epub) return toast("Elige el EPUB que quieres traducir.", true);
            const s = await guard(() => rpc("create_project", epub, $("#new-folder").value.trim(), $("#new-lang").value, $("#new-variant").value));
            if (s) await opened(s);
          },
        }, "Crear proyecto"))),
      h("div", { class: "card stack" },
        h("h2", { style: "margin-top:0" }, "Abrir proyecto"),
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
          ? h("div", {}, info.recientes.map((r) => h("div", { class: "row", style: "padding:6px 0;border-bottom:1px solid var(--border)" },
            h("div", { style: "flex:1;min-width:0" }, h("strong", {}, r.nombre), h("div", { class: "muted small" }, r.carpeta)),
            r.existe ? h("button", { class: "small ghost", onclick: async () => {
              const s = await guard(() => rpc("open_project", r.carpeta));
              if (s) await opened(s);
            } }, "Abrir") : h("span", { class: "badge" }, "no encontrado"))))
          : h("p", { class: "muted" }, "Todavía no hay proyectos."))),
    pdfCard(),
    h("p", { class: "muted small" }, `Versión ${info.version} · modo ${info.modo}. Usa solo libros que tengas derecho a traducir.`));
}

function pdfCard() {
  const last = state.lastTask && state.lastTask.nombre === "Convertir PDF a EPUB" ? state.lastTask : null;
  const r = last && last.resultado;
  const busy = state.task && state.task.activa;
  return h("div", { class: "card stack" },
    h("h2", { style: "margin-top:0" }, "Convertir PDF a EPUB"),
    h("p", { class: "muted" }, "Para tus propios PDF con texto seleccionable (no escaneados): quita encabezados y números de página, une las palabras cortadas, detecta capítulos, cursivas y notas al pie, y crea un EPUB listo para tu lector o para traducir."),
    h("div", { class: "field" }, h("label", {}, "PDF"), pathField("pdf-in", "C:\\Libros\\mi-libro.pdf", "pdf")),
    h("div", { class: "field" }, h("label", {}, "Guardar EPUB en ", h("span", { class: "hint" }, "(opcional; por defecto, junto al PDF)")),
      h("input", { id: "pdf-out", type: "text", placeholder: "C:\\Libros\\mi-libro.epub" })),
    h("div", { class: "row end" }, h("button", { disabled: busy, onclick: () => {
      const pdf = $("#pdf-in").value.trim();
      if (!pdf) return toast("Elige el PDF que quieres convertir.", true);
      startTask(() => rpc("convert_pdf", pdf, $("#pdf-out").value.trim()));
    } }, "Convertir")),
    r ? h("div", { class: "card", style: "background:var(--panel-2)" },
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
  v.append(
    h("h1", {}, s.titulo || s.nombre),
    h("p", { class: "lead" }, `${s.autor || "Autor desconocido"} · ${s.idioma_origen} → ${s.idioma_destino} · ${fmt(prog.palabras)} palabras`),
    h("div", { class: "card" },
      h("div", { class: "row" }, h("strong", {}, `Progreso: ${pct(prog.hechos, prog.total)} %`), h("div", { class: "progress" }, h("div", { style: `width:${pct(prog.hechos, prog.total)}%` }))),
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
  v.append(
    h("h1", {}, "Libros de referencia"),
    h("p", { class: "lead" }, "Añade otros libros del mismo autor con su traducción oficial (el original y la traducción, ambos en EPUB). La app los empareja párrafo a párrafo y aprende cómo traduce ese traductor: nombres, diálogos, tono y vocabulario."),
    s.referencias.length
      ? h("div", {}, s.referencias.map((r) => h("div", { class: "card row" },
        h("div", { style: "flex:1" }, h("strong", {}, r.nombre),
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
      h("h2", { style: "margin-top:0" }, "Añadir libro de referencia"),
      h("div", { class: "field" }, h("label", {}, "Original ", h("span", { class: "hint" }, "(mismo idioma que el libro a traducir)")), pathField("ref-src", "C:\\Libros\\otro-libro-EN.epub")),
      h("div", { class: "field" }, h("label", {}, "Traducción oficial"), pathField("ref-tgt", "C:\\Libros\\otro-libro-ES.epub")),
      h("div", { class: "field" }, h("label", {}, "Nombre ", h("span", { class: "hint" }, "(opcional)")), h("input", { id: "ref-name", type: "text" })),
      h("div", { class: "row end" }, h("button", { onclick: () => {
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
  manual: "Sin clave de API: en la revisión de cada capítulo pulsa «Copiar bloque», pégalo en el chat de tu IA (claude.ai, ChatGPT, Gemini…), copia la respuesta completa y pégala en la app. Es más lento, pero funciona con cualquier IA. La preparación automática de estilo y glosario no está disponible en este modo: puedes editarlos a mano.",
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
    fields.append(
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
        h("button", { onclick: async () => { if (await saveAI(current)) toast("Ajustes de IA guardados."); } }, "Guardar")));
  };
  draw();
  v.append(
    h("h1", {}, "Inteligencia artificial"),
    h("p", { class: "lead" }, "Elige qué IA traduce. Cada persona usa la suya: tu clave se guarda en el almacén seguro de Windows, nunca dentro del proyecto."),
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
      body.append(h("p", { class: "muted" }, "La IA sigue esta guía en cada fragmento. Puedes editarla libremente (formato Markdown)."), ta,
        h("div", { class: "row end" }, h("button", { onclick: async () => { if (await guard(() => rpc("save_style", ta.value))) { k.estilo = ta.value; toast("Guía de estilo guardada."); } } }, "Guardar")));
    } else if (tab === "glosario") {
      body.append(glossaryEditor(k));
    } else if (tab === "sinopsis") {
      const syn = h("textarea", { style: "min-height:160px" });
      syn.value = k.sinopsis;
      const areas = {};
      body.append(h("p", { class: "muted" }, "Contexto que la IA recibe con cada fragmento: quién es quién, género de los personajes, tono…"),
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
      body.append(manual ? h("div", { class: "warn" }, "En modo manual no se puede generar automáticamente: escribe la guía y el glosario a mano o elige una IA con clave.") : null,
        h("div", { class: "card" }, checks.map(([id, title, desc, enabled]) => h("label", { class: "row", style: "font-weight:500;align-items:flex-start;padding:6px 0" },
          h("input", { type: "checkbox", value: id, class: "prep", checked: enabled && !manual && !(id === "estilo" && k.estilo), disabled: !enabled || manual }),
          h("span", {}, h("strong", {}, title), h("br"), h("span", { class: "muted small" }, enabled ? desc : "Necesita al menos un libro de referencia."))))),
        h("div", { class: "row end" }, h("button", { disabled: manual, onclick: () => {
          const steps = [...document.querySelectorAll("input.prep:checked")].map((c) => c.value);
          if (!steps.length) return toast("Marca al menos un paso.", true);
          startTask(() => rpc("prepare", steps));
        } }, "Generar")));
    }
  };
  drawTabs();
  drawBody();
  v.append(h("h1", {}, "Estilo y glosario"),
    h("p", { class: "lead" }, "Todo lo que la IA sabe del libro y del estilo que debe imitar. Revisa y corrige lo que quieras: tus cambios mandan."),
    tabs, body);
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
  v.append(
    h("h1", {}, "Capítulos"),
    h("p", { class: "lead" }, s.revision === "capitulo"
      ? "Traduce de a un capítulo, revísalo y apruébalo: lo aprobado sirve de ejemplo para los siguientes."
      : "Puedes traducir todo de corrido y revisar al final."),
    h("div", { class: "row" },
      h("button", { disabled: busy, onclick: () => startTask(() => rpc("translate", "siguiente", null)) }, "Traducir el siguiente capítulo"),
      h("button", { class: "ghost", disabled: busy, onclick: async () => {
        const est = s.estimacion;
        const ok = await modal(h("div", {}, h("h3", {}, "Traducir todo lo pendiente"),
          h("p", {}, `Se traducirán ${fmt(est.palabras)} palabras en unas ${fmt(est.llamadas)} peticiones. Coste estimado: ${costText(est)}.`),
          h("p", { class: "muted" }, "Puedes detenerlo cuando quieras; lo ya traducido se conserva.")), { ok: "Traducir todo" });
        if (ok) startTask(() => rpc("translate", "todo", null));
      } }, "Traducir todo lo pendiente"),
      h("span", { class: "muted small" }, `Pendiente: ${fmt(s.estimacion.palabras)} palabras · ${costText(s.estimacion)}`)),
    h("div", { class: "card", style: "padding:6px 10px;margin-top:16px" },
      h("table", {},
        h("tr", {}, h("th", {}, "Capítulo"), h("th", {}, "Palabras"), h("th", {}, "Estado"), h("th", {}, "Progreso"), h("th", {}, "")),
        chapters.map((c) => h("tr", { class: c.status === "omitido" ? "skipped" : "" },
          h("td", {}, h("button", { class: "link", onclick: () => go("revision", c.index) }, c.title || `Documento ${c.index}`), c.in_spine ? null : h("span", { class: "muted small" }, " (índice)")),
          h("td", { class: "num" }, fmt(c.words)),
          h("td", {}, statusBadge(c.status)),
          h("td", { style: "width:180px" }, h("div", { class: "row" }, h("div", { class: "progress" }, h("div", { style: `width:${pct(c.done, c.total)}%` })), h("span", { class: "small muted" }, `${c.done}/${c.total}`))),
          h("td", { class: "row", style: "justify-content:flex-end" },
            h("button", { class: "small ghost", onclick: () => go("revision", c.index) }, "Revisar"),
            c.status !== "omitido" && c.done < c.total ? h("button", { class: "small", disabled: busy, onclick: () => startTask(() => rpc("translate", "capitulo", c.index)) }, "Traducir") : null,
            h("button", { class: "small ghost", title: "No traducir este documento (créditos, publicidad…)", onclick: async () => {
              const sum = await guard(() => rpc("set_skipped", c.index, c.status !== "omitido"));
              if (sum) { state.summary = sum; render(); }
            } }, c.status === "omitido" ? "Incluir" : "Omitir")))))));
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
  v.append(
    h("div", { class: "review-head" },
      h("div", { class: "row" },
        h("button", { class: "small ghost", onclick: () => go("capitulos") }, "‹ Capítulos"),
        h("h1", { style: "margin:0;flex:1" }, data.titulo),
        statusBadge(data.estado)),
      h("div", { class: "row", style: "margin-top:10px" },
        h("span", { class: "muted" }, `${data.hechos} de ${data.total} párrafos traducidos`),
        h("div", { class: "spacer" }),
        filterSel,
        !manualMode && data.hechos < data.total && !data.omitir ? h("button", { class: "small", disabled: busy, onclick: () => startTask(() => rpc("translate", "capitulo", index)) }, "Traducir lo pendiente") : null,
        h("button", { class: "small ghost", onclick: () => { const p = $("#manual-panel"); p.hidden = !p.hidden; } }, "Modo manual"),
        h("button", { class: "small " + (approved ? "ghost" : ""), disabled: data.hechos < data.total || data.bloqueado, onclick: async () => {
          const d = await guard(() => rpc("set_approved", index, !approved));
          if (!d) return;
          await refreshSummary();
          if (!approved) toast("Capítulo aprobado. Sus párrafos servirán de ejemplo para los siguientes.");
          render();
        } }, approved ? "Quitar aprobación" : "Aprobar capítulo"),
        next ? h("button", { class: "small ghost", onclick: () => go("revision", next.index) }, "Siguiente ›") : null),
      data.bloqueado ? h("div", { class: "warn", style: "margin-top:8px" }, "Este capítulo se está traduciendo. La vista se actualiza sola; podrás editar al terminar.") : null,
      data.resumen ? h("details", { style: "margin-top:8px" }, h("summary", { class: "muted small" }, "Resumen del capítulo"), h("p", { class: "small" }, data.resumen)) : null),
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
  const info = h("p", { class: "muted small" });
  return h("div", { id: "manual-panel", class: "card manual stack", hidden: state.summary.proveedor.tipo !== "manual" },
    h("h3", { style: "margin-top:0" }, "Modo manual (copiar y pegar)"),
    h("ol", { class: "small" },
      h("li", {}, "Pulsa «Copiar bloque»: se copia el siguiente bloque pendiente con todas las instrucciones."),
      h("li", {}, "Pégalo en el chat de tu IA y espera la respuesta completa."),
      h("li", {}, "Copia la respuesta, pégala abajo y pulsa «Importar». Repite hasta terminar el capítulo.")),
    h("div", { class: "row" },
      h("button", { onclick: async () => {
        const p = await guard(() => rpc("manual_prompt", index));
        if (p === undefined) return;
        if (p === null) return toast("Este capítulo ya está traducido.");
        if (await copyText(p.texto)) {
          info.textContent = `Copiado: párrafos ${p.ids[0]}–${p.ids[p.ids.length - 1]} (${fmt(p.caracteres)} caracteres).`;
          toast("Bloque copiado. Pégalo en el chat de tu IA.");
        } else {
          await modal(h("div", {}, h("p", {}, "No se pudo copiar automáticamente. Selecciona y copia este texto:"), h("textarea", { class: "big" }, p.texto)), { ok: "Cerrar", cancel: null });
        }
      } }, "Copiar bloque"),
      info),
    out,
    h("div", { class: "row end" }, h("button", { onclick: async () => {
      if (!out.value.trim()) return toast("Pega primero la respuesta de la IA.", true);
      const r = await guard(() => rpc("manual_import", index, out.value));
      if (!r) return;
      toast(`Importados ${r.guardados} párrafos.` + (r.faltan.length ? ` Faltaron: ${r.faltan.join(", ")}.` : ""));
      out.value = "";
      await refreshSummary();
      render();
    } }, "Importar respuesta")));
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
    result.append(h("div", { class: "card" },
      h("p", {}, h("strong", {}, "Listo: "), r.ruta),
      r.total_avisos ? h("p", { class: "warn" }, `${r.total_avisos} párrafo(s) con avisos de formato; revisa: ${r.avisos.slice(0, 5).join(" · ")}`) : null,
      h("button", { class: "ghost", onclick: () => rpc("open_folder", r.ruta) }, "Abrir carpeta")));
  };
  const title = h("input", { type: "text", value: s.titulo_traducido || "", placeholder: s.titulo });
  const missing = s.progreso.total - s.progreso.hechos;
  v.append(
    h("h1", {}, "Exportar"),
    h("p", { class: "lead" }, "Genera el EPUB con la traducción. Conserva portada, imágenes, índice y estilos del original."),
    missing ? h("div", { class: "warn" }, `Aún faltan ${fmt(missing)} párrafos: en el EPUB quedarán en el idioma original.`) : null,
    h("div", { class: "card stack" },
      h("div", { class: "field" }, h("label", {}, "Título de la edición traducida"),
        h("div", { class: "path" }, title, h("button", { class: "ghost", onclick: async () => {
          const sum = await guard(() => rpc("set_settings", { titulo_traducido: title.value.trim() }));
          if (sum) { state.summary = sum; toast("Título guardado."); }
        } }, "Guardar"))),
      h("div", { class: "row" },
        h("button", { onclick: () => run(false) }, "Generar EPUB traducido"),
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
