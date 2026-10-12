// Corregir los avisos de calidad con un clic: al instante (con deshacer), unificar nombres y
// propuestas de la IA (conectada y en modo manual), con la IA simulada y el libro inventado.
// Sirve en cualquier idioma de la interfaz: busca los botones por su texto traducido y, fuera
// del español, avisa de textos que sigan en español.
// Uso: python tools/ui/correr.py ui_qa_fix_test.mjs 8771 [es|en|pt]
import { createRequire } from "module";
import { readFileSync } from "fs";
const require = createRequire(process.env.NODE_PATH + "/");
const { chromium } = require("playwright");
const [url, work, shots] = process.argv.slice(2);
const lang = process.env.UI_IDIOMA || "es";  // lo pone correr.py
const catalog = lang === "es" ? {} : JSON.parse(readFileSync(new URL(`../../src/traductor/locales/${lang}.json`, import.meta.url), "utf-8"));
const T = (es) => catalog[es] ?? es;

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, locale: { es: "es-ES", en: "en-US", pt: "pt-BR" }[lang] });
await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: new URL(url).origin });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
const call = (name, ...args) => page.evaluate(([n, a]) => rpc(n, ...a), [name, args]);
const waitTaskDone = async () => {
  await page.waitForTimeout(500);
  await page.waitForFunction(() => document.querySelector("#taskbar").hidden === true, null, { timeout: 30000 });
  await page.waitForTimeout(600);
};
const btn = (label, scope = page) => scope.getByRole("button", { name: T(label), exact: true }).first();
const btnStarting = (label) => page.getByRole("button", { name: T(label).split(" (")[0] }).first();
const rowOf = (kind) => page.locator(".qa-group:not(.qa-recent) .qa-row").filter({ hasText: T(kind) });
const kinds = async () => (await page.locator(".qa-chip").allTextContents()).join(" | ");
const check = (ok, what) => { console.log(ok ? "✓" : "✗", what); if (!ok) process.exitCode = 1; };
const segText = async (doc, n) => (await call("chapter", doc)).segmentos[n].tgt;
const openQuality = async () => {
  // Una sola vez: si ya está en Calidad, el cambio de pantalla no la vuelve a dibujar.
  await page.evaluate(async () => {
    state.summary = await rpc("project_summary");
    if (state.view === "calidad") await render();
    else go("calidad");
  });
  await page.waitForSelector(".qa-summary");
  await page.waitForTimeout(500);
};

// Fuera del español: textos de la interfaz que sigan en español (sin el libro ni lo que escribe la IA;
// los signos ¿ ¡ sí pueden aparecer, porque el aviso habla de ellos).
const SPANISH = lang === "es" ? null
  : new RegExp(String.raw`ción(?!\p{L})|ñ|(?<!\p{L})(el|los|las|del|y|una|con|sin|párrafos?|traducción|aquí|pulsa|elige|pega|todavía|ahora|glosario|corregir|deshacer|propuesta)(?!\p{L})`, "iu");
const leftovers = [];
const spanishCheck = async (name) => {
  if (!SPANISH) return;
  const texts = await page.evaluate(() => {
    const skip = ".frag, .diff, textarea, .qa-proposal .muted, .qa-opinion, select#ui-lang, .src, .tgt, #modal-body label";
    const out = new Set();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement;
      const text = n.nodeValue.replace(/\s+/g, " ").trim();
      if (!text || !el || el.closest(skip) || el.closest("[hidden]") || el.closest("script, style")) continue;
      out.add(text);
    }
    for (const el of document.querySelectorAll("[placeholder], [title]")) {
      if (!el.closest("[hidden]")) for (const a of ["placeholder", "title"]) if (el.getAttribute(a)) out.add(el.getAttribute(a).trim());
    }
    return [...out];
  });
  const found = texts.filter((x) => SPANISH.test(x) || (catalog[x] !== undefined && catalog[x] !== x));
  if (found.length) {
    leftovers.push(...found);
    console.log(`  ${name}: textos a revisar →`, found.slice(0, 6).join(" | "));
  }
};
const shot = async (name) => {
  await page.evaluate(() => { document.querySelector("#toast").textContent = ""; });
  await page.waitForTimeout(300);
  await spanishCheck(name);
  await page.screenshot({ path: `${shots}/${name}.png` });
};

// Proyecto, referencia, IA conectada (simulada), conocimiento y el primer capítulo traducido al español.
await page.goto(url);
await page.waitForSelector("#new-epub");
await page.fill("#new-epub", `${work}/book.epub`);
await page.fill("#new-folder", `${work}/proyecto`);
await page.selectOption("#new-lang", "es");
await page.locator(".card:has(#new-epub) button.gold").click();
await page.waitForSelector("ol.steps");
await page.click("#sidebar a[data-view=referencias]");
await page.fill("#ref-src", `${work}/ref_en.epub`);
await page.fill("#ref-tgt", `${work}/ref_es.epub`);
await page.locator(".card:has(#ref-src) .row.end button").click();
await waitTaskDone();
await call("set_provider", { tipo: "ollama", modelo: "modelo-de-prueba" }, "");
await page.evaluate(() => startTask(() => rpc("prepare", ["estilo", "glosario", "analisis", "terminos"])));
await waitTaskDone();
await page.evaluate(() => startTask(() => rpc("translate", "siguiente")));
await waitTaskDone();
// Errores a propósito: guion de diálogo y espacio antes del punto; signo de apertura y un nombre con dos grafías.
await call("save_segment", 0, 2, "- Los barcos llegan tarde - dijo el farero<x1/> .");
await call("save_segment", 0, 3, "Asintió y miró a Tobias, y Tobías miró el agua. Tardarán mucho?");

await openQuality();
console.log("avisos:", await kinds());
await shot("1-avisos");

// Corregir uno solo y deshacerlo.
await btn("Corregir", rowOf("Signos de apertura")).click();
await page.waitForSelector(".qa-recent");
check(await rowOf("Signos de apertura").count() === 0, "signo de apertura corregido");
check((await segText(0, 3)).endsWith("¿Tardarán mucho?"), "el párrafo quedó con «¿Tardarán mucho?»");
await shot("2-corregido-con-deshacer");
await btn("Deshacer", page.locator(".qa-recent")).click();
await page.waitForTimeout(600);
check(await rowOf("Signos de apertura").count() === 1, "deshacer devuelve el aviso");
check((await segText(0, 3)).endsWith(" Tardarán mucho?"), "y el texto original");

// Corregir al instante todo lo mecánico.
await btnStarting("Corregir al instante ({n})").click();
await page.waitForTimeout(800);
check(await rowOf("Signos de apertura").count() === 0 && await rowOf("Rayas de diálogo").count() === 0
  && await rowOf("Espacios y puntuación").count() === 0, "corregir al instante arregla signos, rayas y espacios");
check(await segText(0, 2) === "—Los barcos llegan tarde —dijo el farero<x1/>.", "la nota al pie quedó en su lugar");
console.log("después:", await kinds());

// Unificar un nombre en todo el libro.
await btn("Unificar…", rowOf("Nombres con varias grafías")).click();
await page.waitForSelector("#modal-backdrop:not([hidden])");
await page.click("#modal-body label:has-text('Tobías')");
await shot("3-unificar-nombre");
await page.click("#modal-ok");
await page.waitForTimeout(800);
check(await rowOf("Nombres con varias grafías").count() === 0, "nombre unificado");
check(!(await segText(0, 3)).includes("Tobias"), "ya no queda «Tobias»");

// Con la IA conectada: propuesta para el glosario, aceptar.
await btn("Corregir con IA", rowOf("Glosario no respetado")).click();
await page.waitForSelector(".qa-proposal");
await page.locator(".qa-proposal").scrollIntoViewIfNeeded();
await shot("4-propuesta-de-la-ia");
await btn("Aceptar", page.locator(".qa-proposal")).click();
await page.waitForTimeout(800);
check(await rowOf("Glosario no respetado").count() === 0, "propuesta aceptada: el aviso del glosario desaparece");
check((await segText(0, 1)).includes("PUERTOARADO"), "el párrafo dice «PUERTOARADO»");

// Varias a la vez, en segundo plano: una propuesta (glosario) y un párrafo que la IA da por bueno (números).
await call("save_segment", 0, 1, "Mirela caminó hasta Harrowgate antes del alba, como cada <g1>santo</g1> día.");
await call("save_segment", 0, 0, "El puerto 2");
await openQuality();
await btnStarting("Proponer con IA ({n})").click();
await waitTaskDone();
await page.waitForSelector(".qa-proposal");
check(await page.locator(".qa-opinion").count() === 1, "la IA dio por bueno el párrafo de los números");
await page.locator(".qa-proposal").scrollIntoViewIfNeeded();
await shot("5-propuestas-en-segundo-plano");
await btn("Descartar", page.locator(".qa-proposal")).click();
await page.waitForTimeout(800);
check(await btn("Corregir con IA", rowOf("Glosario no respetado")).count() === 1,
  "propuesta descartada: el aviso vuelve a ofrecer «Corregir con IA»");

// Modo manual: copiar instrucciones, pegar la respuesta del chat e importar.
await call("set_provider", { tipo: "manual" }, "");
await openQuality();
await btn("Corregir con IA", rowOf("Glosario no respetado")).click();
await page.waitForSelector(".card.manual");
await page.waitForTimeout(400);
const clip = await page.evaluate(() => navigator.clipboard.readText());
check(clip.includes("# Segments to fix") && clip.includes("Harrowgate → Puertoarado"), "instrucciones copiadas al portapapeles");
await page.fill(".card.manual textarea", '```\n<fix id="1"><text>Mirela caminó hasta Puertoarado antes del alba, como cada <g1>santo</g1> día.</text><why>El glosario pide «Puertoarado».</why></fix>\n```');
await shot("6-modo-manual");
await btn("Importar", page.locator(".card.manual")).click();
await page.waitForSelector(".qa-proposal");
check(await page.locator(".qa-proposal", { hasText: "El glosario pide «Puertoarado»." }).count() === 1, "la respuesta del chat aparece como propuesta");
await btn("Aceptar", page.locator(".qa-proposal")).click();
await page.waitForTimeout(800);
check((await segText(0, 1)).includes("Puertoarado"), "propuesta del chat aceptada");
await shot("7-final");

if (SPANISH) console.log("textos a revisar:", leftovers.length ? [...new Set(leftovers)] : 0);
if (leftovers.length) process.exitCode = 1;
console.log("errores:", errors.length ? errors : "ninguno");
if (errors.length) process.exitCode = 1;
await browser.close();
