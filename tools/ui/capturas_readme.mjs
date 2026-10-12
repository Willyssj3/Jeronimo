// Capturas del README con el libro de muestra que arma tools/capturas_readme.py.
// Uso (lo llama ese script): node capturas_readme.mjs URL CARPETA_TRABAJO CARPETA_SALIDA
import { createRequire } from "module";
import { readFileSync } from "fs";
const require = createRequire(process.env.NODE_PATH + "/");
const { chromium } = require("playwright");
const [url, work, out] = process.argv.slice(2);
const demo = JSON.parse(readFileSync(`${work}/demo.json`, "utf-8"));

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: 1.5, locale: "es-ES" });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

const call = (name, ...args) => page.evaluate(([n, a]) => rpc(n, ...a), [name, args]);
const clearToasts = () => page.evaluate(() => { document.querySelector("#toast").textContent = ""; });
const shot = async (name) => {
  await clearToasts();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${out}/${name}.png` });
  console.log("·", name);
};
const goTo = async (view, chapter = null) => {
  await page.evaluate(async ([v, c]) => {
    state.summary = await rpc("project_summary");
    go(v, c);
  }, [view, chapter]);
  await page.waitForTimeout(700);
};
// En modo manual el panel de copiar y pegar tapa los párrafos: para las capturas de revisión, se oculta.
const hideManual = () => page.evaluate(() => { const p = document.querySelector("#manual-panel"); if (p) p.hidden = true; });
const waitTaskDone = async () => {
  await page.waitForTimeout(500);
  await page.waitForFunction(() => document.querySelector("#taskbar").hidden === true, null, { timeout: 30000 });
  await page.waitForTimeout(600);
};

await page.goto(url);
await page.waitForSelector("#new-epub");
await shot("inicio");

// Proyecto, referencia y modo manual con Claude.
await page.fill("#new-epub", `${work}/book.epub`);
await page.fill("#new-folder", `${work}/proyecto`);
await page.selectOption("#new-lang", "es");
await page.locator(".card:has(#new-epub) button.gold").click();
await page.waitForSelector("ol.steps");
await page.click("#sidebar a[data-view=referencias]");
await page.fill("#ref-src", `${work}/ref_en.epub`);
await page.fill("#ref-tgt", `${work}/ref_es.epub`);
await page.fill("#ref-name", "Nets");
await page.locator(".card:has(#ref-src) .row.end button").click();
await waitTaskDone();
await call("set_provider", { tipo: "manual" }, "");

// Guía de estilo, glosario, sinopsis y la traducción escrita a mano.
const summary = await call("project_summary");
const index = Object.fromEntries(summary.capitulos.map((c) => [c.title, c.index]));
await call("save_style", demo.estilo);
await call("save_glossary", demo.glosario);
await call("save_synopsis", demo.sinopsis, {
  [index["The Harbor"]]: "Mirela llega al puerto al alba y conversa con Tobías mientras esperan a los barcos.",
  [index["The Festival"]]: "La fiesta del pueblo reúne a todos en la plaza; Tobías invita a Mirela a bailar.",
});
for (const [title, texts] of Object.entries(demo.traduccion)) {
  for (const [n, text] of texts.entries()) await call("save_segment", index[title], n, text);
}
await call("set_approved", index["The Harbor"], true);
await call("set_book_metadata", { titulo: demo.titulo });
await call("set_cover", { usar: true, estilo: "lisa", titulo: demo.titulo, autor: "Test Author" });
await call("review_manual_import", index["The Festival"], demo.revision);

await goTo("resumen");
await shot("resumen");

await goTo("revision", index["The Harbor"]);
await page.waitForSelector(".seg");
await hideManual();
await shot("revision");

await goTo("revision", index["The Festival"]);
await page.waitForSelector(".suggestion");
await hideManual();
await page.locator(".suggestion").first().evaluate((el) => el.closest(".seg").scrollIntoView({ block: "center" }));
await shot("sugerencia");

await goTo("calidad");
await page.waitForSelector(".qa-summary");
await shot("calidad");

await goTo("conocimiento");
await page.locator(".tabs button").nth(1).click();
await page.waitForSelector("input[value='Puertoarado']");
await shot("glosario");

await goTo("lectura", index["The Harbor"]);
await page.waitForSelector(".reader-page");
await shot("lectura");

await goTo("exportar");
await page.waitForSelector(".cover-img[src^='data:']");
await page.locator(".cover-layout").evaluate((el) => el.closest(".card").scrollIntoView({ block: "start" }));
await page.evaluate(() => { document.querySelector("#main").scrollTop -= 24; });
await shot("exportar");

await page.emulateMedia({ colorScheme: "dark" });
await goTo("revision", index["The Harbor"]);
await page.waitForSelector(".seg");
await hideManual();
await shot("oscuro");

console.log("errores:", errors.length ? errors : "ninguno");
await browser.close();
