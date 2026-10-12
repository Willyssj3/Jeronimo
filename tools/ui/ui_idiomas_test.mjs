// Recorre todas las pantallas en el idioma pedido (es, en o pt), saca capturas y avisa de
// textos de la interfaz que sigan en español. Busca los botones por su texto traducido
// (lo lee de src/traductor/locales/<idioma>.json), así sirve para cualquier idioma.
// Uso: python tools/ui/correr.py ui_idiomas_test.mjs 8765 en
import { createRequire } from "module";
import { readFileSync, writeFileSync } from "fs";
const require = createRequire(process.env.NODE_PATH + "/");
const { chromium } = require("playwright");
const [url, work, shots] = process.argv.slice(2);
const lang = process.env.UI_IDIOMA || "es";  // lo pone correr.py
const root = new URL("../../", import.meta.url);
const catalog = lang === "es" ? {} : JSON.parse(readFileSync(new URL(`src/traductor/locales/${lang}.json`, root), "utf-8"));
const T = (es, vars) => {
  let s = catalog[es] ?? es;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return s;
};

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, locale: { es: "es-ES", en: "en-US", pt: "pt-BR" }[lang] });
await context.grantPermissions(["clipboard-read", "clipboard-write"]);
const page = await context.newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));

const step = (s) => console.log("·", s);
const btn = (label, scope = page) => scope.getByRole("button", { name: T(label), exact: true }).first();
const nav = async (v) => { await page.click(`#sidebar a[data-view=${v}]`); await page.waitForTimeout(500); };
const clearToasts = () => page.evaluate(() => { document.querySelector("#toast").textContent = ""; });
const waitTaskDone = async () => {
  await page.waitForTimeout(500);
  await page.waitForFunction(() => document.querySelector("#taskbar").hidden === true, null, { timeout: 30000 });
  await page.waitForTimeout(800);
};

// Textos que se ven en la interfaz (sin el contenido del libro ni lo que escribe la IA).
const visibleTexts = () => page.evaluate(() => {
  const skip = ".src, .tgt, .reader-page, .frag, .diff, textarea, .suggestion .muted, .log, #project-name, .seg .note, .recent, select#ui-lang, details > p";
  const out = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement;
    const text = n.nodeValue.replace(/\s+/g, " ").trim();
    if (!text || !el || el.closest(skip) || el.closest("[hidden]") || el.closest("script, style")) continue;
    if (el.closest("#modal-backdrop") && document.querySelector("#modal-backdrop").hidden) continue;
    out.add(text);
  }
  for (const el of document.querySelectorAll("[placeholder], [title], [aria-label]")) {
    if (el.closest("[hidden]") || el.closest(skip)) continue;
    for (const a of ["placeholder", "title", "aria-label"]) if (el.getAttribute(a)) out.add(el.getAttribute(a).trim());
  }
  return [...out];
});

// Palabras que delatan un texto en español (en portugués no se cuentan las que también son portuguesas).
const SPANISH_WORDS = {
  en: "el|los|las|del|y|una|con|sin|libros?|párrafos?|traducción|aquí|también|pulsa|elige|pega|añade|añadir|borrar|quitar|guardar|todavía|aún|ahora|capítulos?|referencias?|glosario|guía|resumen|exportar|revisión",
  pt: "el|del|y|una|con|sin|hay|libros?|párrafos?|traducción|aquí|también|pulsa|elige|añade|añadir|borrar|quitar|todavía|aún|ahora|referencias|glosario|guía|resumen|revisión",
}[lang];
const SPANISH = SPANISH_WORDS ? new RegExp(String.raw`ción(?!\p{L})|ñ|¿|¡|(?<!\p{L})(${SPANISH_WORDS})(?!\p{L})`, "iu") : null;
const report = [];
let suspicious = 0;
async function check(name) {
  if (!SPANISH) return;
  const found = [];
  for (const text of await visibleTexts()) {
    const untranslated = catalog[text] !== undefined && catalog[text] !== text;
    if (untranslated || SPANISH.test(text)) found.push(text);
  }
  report.push(`## ${name}`, ...found.map((t) => `  ${t}`));
  if (found.length) {
    suspicious += found.length;
    console.log(`  ${name}: ${found.length} texto(s) a revisar →`, found.slice(0, 6).map((t) => t.slice(0, 70)).join(" | "));
  }
}

async function shot(name, { full = false } = {}) {
  await page.waitForTimeout(300);
  await check(name);
  if (full) {
    const h = await page.evaluate(() => document.querySelector(".topbar").offsetHeight + document.querySelector("#main").scrollHeight + 4);
    await page.setViewportSize({ width: 1360, height: Math.max(900, Math.min(h, 6000)) });
    await page.waitForTimeout(300);
  }
  await page.screenshot({ path: `${shots}/${name}.png` });
  if (full) await page.setViewportSize({ width: 1360, height: 900 });
}

// Un libro en TXT para probar la conversión a EPUB.
writeFileSync(`${work}/notas.txt`, "Chapter One\n\nMirela walked to the harbor before dawn.\n\nThe nets were wet.\n\nChapter Two\n\nThe wind kept blowing from the north.\n", "utf-8");

await page.goto(url);
await page.waitForSelector("#new-epub");
await shot("01-inicio", { full: true });

step("convertir a EPUB");
await page.fill("#pdf-in", `${work}/notas.txt`);
await page.locator(".card:has(#pdf-in) .row.end button").click();
await waitTaskDone();
await page.waitForSelector(".card:has(#pdf-in) .result");
await clearToasts();
await page.locator(".card:has(#pdf-in)").scrollIntoViewIfNeeded();
await shot("02-convertir");

step("crear proyecto");
await page.fill("#new-epub", `${work}/book.epub`);
await page.fill("#new-folder", `${work}/proyecto`);
await page.selectOption("#new-lang", "es");  // el libro de prueba está en inglés y la referencia, en español
await page.locator(".card:has(#new-epub) button.gold").click();
await page.waitForSelector("ol.steps");
await clearToasts();
await shot("03-resumen");

step("referencias y perfil de autor");
await nav("referencias");
await page.fill("#ref-src", `${work}/ref_en.epub`);
await page.fill("#ref-tgt", `${work}/ref_es.epub`);
await page.fill("#ref-name", "Nets");
await page.locator(".card:has(#ref-src) .row.end button").click();
await waitTaskDone();
await page.waitForSelector(".card.row .badge");
await btn("Guardar perfil").click();
await page.waitForTimeout(600);
await shot("04-referencias", { full: true });
await clearToasts();

step("inteligencia artificial");
await nav("ia");
await page.click("label:has(input[value=anthropic])");
await shot("05-ia-anthropic", { full: true });
await page.click("label:has(input[value=ollama])");
await page.fill("#ai-model", "modelo-de-prueba");
await page.locator(".card .row.end button.gold").click();
await page.waitForTimeout(800);
await shot("06-ia-ollama", { full: true });
await clearToasts();

step("estilo y glosario");
await nav("conocimiento");
await page.locator(".tabs button").nth(3).click();
await shot("07-conocimiento-generar");
await page.locator(".row.end button.gold").click();
await waitTaskDone();
await clearToasts();
await page.locator(".tabs button").nth(0).click();
await shot("08-guia-de-estilo");
await page.locator(".tabs button").nth(1).click();
await page.waitForSelector("input[value='Harrowgate']");
await shot("09-glosario");
await page.locator(".tabs button").nth(2).click();
await shot("10-sinopsis", { full: true });

step("capítulos");
await nav("capitulos");
await page.locator("tr:has-text('Credits') button").last().click();
await page.waitForSelector("tr.skipped");
await btn("Traducir el siguiente capítulo").click();
await waitTaskDone();
await clearToasts();
await shot("11-capitulos");
await btn("Traducir todo lo pendiente").click();
await page.waitForSelector("#modal-backdrop:not([hidden])");
await shot("12-modal-traducir-todo");
await page.click("#modal-cancel");

step("revisión");
await page.click("tr:has-text('The Harbor') button.link");
await page.waitForSelector(".seg");
const firstTgt = page.locator(".seg .tgt").first();
await firstTgt.click();
await page.keyboard.press("End");
await page.keyboard.type(" (+)");
await page.locator("h1").first().click();
await page.waitForSelector(".meta .saved");
await shot("13-revision");
// Si el clic cae justo mientras la vista se redibuja, la tarea no arranca: se reintenta.
for (let i = 0; i < 3; i++) {
  await page.evaluate(() => { state.reviewOpen = true; return render(); });
  await page.waitForSelector("#review-panel:not([hidden])");
  await page.waitForTimeout(400);
  if (await page.locator(".suggestion").count()) break;
  await page.locator("#review-panel button.gold:not([disabled])").click();
  await waitTaskDone();
  await page.waitForSelector(".suggestion", { timeout: 6000 }).catch(() => {});
  if (await page.locator(".suggestion").count()) break;
}
await page.waitForSelector(".suggestion", { timeout: 1000 });
await clearToasts();
await page.locator("#review-panel").scrollIntoViewIfNeeded();
await shot("14-revision-con-ia");
await page.locator(".suggestion").first().scrollIntoViewIfNeeded();
await shot("15-sugerencia");
await page.evaluate(() => { document.querySelector("#manual-panel").hidden = false; document.querySelector("#main").scrollTop = 0; });
await shot("16-panel-modo-manual", { full: true });
await page.evaluate(() => { document.querySelector("#manual-panel").hidden = true; });

step("borrar y recuperar la traducción");
await page.locator(".tools-row button.danger-text").click();
await page.waitForSelector("#modal-backdrop:not([hidden])");
await shot("17-modal-borrar");
await page.click("#modal-ok");
await page.waitForSelector(".review-head .note");
await clearToasts();
await shot("18-traduccion-borrada");
await page.locator(".review-head .note button").click();
await page.waitForTimeout(800);
await clearToasts();

step("lectura");
await page.evaluate(() => go("lectura", state.chapter));
await page.waitForSelector(".reader-page");
await page.waitForTimeout(600);
await shot("19-lectura");

step("control de calidad");
await nav("calidad");
await page.waitForSelector(".qa-summary");
await shot("20-calidad", { full: true });

step("buscar y reemplazar");
await nav("buscar");
await page.fill("input[type=search]", "HARROWGATE");
await page.locator(".card .grid2 input[type=text]").fill("Puertoarado");
await page.locator(".card .row label input[type=checkbox]").first().check();
await page.locator(".card .row > button").last().click();
await page.waitForSelector(".search-row");
await clearToasts();
await shot("21-buscar");
await page.locator(".row button.gold").click();
await page.waitForSelector("#modal-backdrop:not([hidden])");
await shot("22-modal-reemplazar");
await page.click("#modal-ok");
await page.waitForSelector(".note.row");
await page.waitForTimeout(600);
await clearToasts();
await shot("23-deshacer");

step("glosario: actualizar lo ya traducido");
await nav("conocimiento");
await page.locator(".tabs button").nth(1).click();
await page.fill("input[value='Puertoarado']", "Puerto Arado");
await page.locator(".row > button:not(.ghost)").last().click();
await page.waitForSelector("#modal-backdrop:not([hidden])");
await shot("24-modal-glosario");
await page.click("#modal-ok");

step("exportar");
await nav("exportar");
await page.waitForSelector(".meta-grid");
await page.waitForSelector(".cover-img[src^='data:']");
await shot("25-exportar", { full: true });
await btn("Generar EPUB traducido").click();
await page.waitForSelector(".result");
await btn("Word (.docx)").click();
await waitTaskDone();
await clearToasts();
await page.evaluate(() => { const m = document.querySelector("#main"); m.scrollTop = m.scrollHeight; });
await shot("26-exportar-resultado");
await page.evaluate(() => document.querySelector("#btn-task-log").click());
await page.waitForSelector("#modal-backdrop:not([hidden])");
await shot("27-modal-registro");
await page.click("#modal-ok");

step("modo manual");
await nav("ia");
await page.click("label:has(input[value=manual])");
await page.locator(".card .row.end button.gold").click();
await page.waitForTimeout(1000);
await clearToasts();
await shot("28-ia-modo-manual", { full: true });
await nav("conocimiento");
await page.locator(".tabs button").nth(3).click();
await page.waitForTimeout(600);
await shot("29-preparacion-manual");
await page.locator("#view .card .row button.small").first().click();
await page.waitForSelector(".card.manual");
await btn("Copiar instrucciones").click();
await page.waitForTimeout(500);
await clearToasts();
await shot("30-preparacion-manual-paso");
await page.locator(".card.manual button.danger-text").click();
await page.click("#modal-ok");
await nav("capitulos");
await shot("31-capitulos-modo-manual");
await page.click("tr:has-text('Second') button.link");
await page.waitForSelector("#manual-panel:not([hidden])");
await btn("Copiar bloque").click();
await page.waitForTimeout(600);
await clearToasts();
await shot("32-revision-modo-manual", { full: true });

step("resumen final, inicio y avisos");
await nav("resumen");
await shot("33-resumen-final", { full: true });
await page.click("#btn-close-project");
await page.waitForSelector("#new-epub");
await shot("34-inicio-recientes", { full: true });
await page.locator(".card button.link").first().click();
await page.waitForSelector("#modal-backdrop:not([hidden])");
await shot("35-modal-perfiles");
await page.click("#modal-ok");
await page.waitForSelector("#new-epub");
// Versión nueva y avisos al abrir, simulados. El aviso de versión nueva se abre solo y espera
// una respuesta: por eso no se espera a checkUpdate() ni a startupNotices().
const fakes = {
  check_update: { version: "9.9.9", url: "https://github.com/Willyssj3/Jeronimo/releases", notas: "## v9.9.9\n- …", auto: true },
  startup_notices: { actualizado_a: "9.9.9", ofrecer_accesos: true },
  create_shortcuts: [],
};
await page.evaluate((fakes) => {
  window.realRpc = window.rpc;
  window.rpc = (name, ...args) => (name in fakes ? Promise.resolve(fakes[name]) : window.realRpc(name, ...args));
  checkUpdate();
}, fakes);
await page.waitForSelector("#modal-backdrop:not([hidden])");
await shot("36-modal-version-nueva");
await page.click("#modal-cancel");
await page.waitForSelector("#modal-backdrop", { state: "hidden" });
await shot("37-aviso-version-nueva");
await page.evaluate(() => { startupNotices(); });
await page.waitForSelector("#modal-backdrop:not([hidden])");
await shot("37b-modal-accesos-directos");
await page.click("#modal-cancel");
await page.waitForSelector("#modal-backdrop", { state: "hidden" });
await page.evaluate(() => { window.rpc = window.realRpc; });
await page.click("#btn-quit");
await page.waitForSelector("#modal-backdrop:not([hidden])");
await shot("38-modal-salir");
await page.click("#modal-cancel");

writeFileSync(`${work}/textos-a-revisar.txt`, report.join("\n") + "\n", "utf-8");
console.log(`textos a revisar: ${suspicious}${suspicious ? ` (lista en ${work}/textos-a-revisar.txt)` : ""}`);
console.log("errores de consola:", errors.length ? errors : "ninguno");
await browser.close();
