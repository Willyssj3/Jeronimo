// Renombrar un proyecto y quitarlo de recientes (con «Deshacer»), con el libro inventado.
// Sirve en cualquier idioma de la interfaz: busca los botones por su texto traducido y, fuera
// del español, avisa de textos que sigan en español.
// Uso: python tools/ui/correr.py ui_proyectos_test.mjs 8772 [es|en|pt]
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
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
const check = (ok, what) => { console.log(ok ? "✓" : "✗", what); if (!ok) process.exitCode = 1; };
const btn = (label, scope = page) => scope.getByRole("button", { name: T(label), exact: true }).first();
const recentNames = () => page.locator(".recent:not(.removed) strong").allTextContents();
const NEW_NAME = "Mi traducción de prueba";

const SPANISH = lang === "es" ? null
  : new RegExp(String.raw`ción(?!\p{L})|ñ|(?<!\p{L})(el|los|las|del|y|una|con|sin|nombre|carpeta|recientes|quitar|borra|deshacer|guardar|proyecto)(?!\p{L})`, "iu");
const leftovers = [];
const spanishCheck = async (name) => {
  if (!SPANISH) return;
  const texts = await page.evaluate(() => {
    // Afuera: los nombres de proyecto, las rutas y el título del libro (los escribe el usuario).
    const skip = ".recent strong, .recent .muted.small, #project-name, .title-row h1, input, select#ui-lang, .lead";
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
      if (!el.closest("[hidden]")) for (const a of ["placeholder", "title", "aria-label"]) if (el.getAttribute(a)) out.add(el.getAttribute(a).trim());
    }
    return [...out];
  });
  const found = texts.filter((x) => !x.includes(NEW_NAME) && (SPANISH.test(x) || (catalog[x] !== undefined && catalog[x] !== x)));
  if (found.length) {
    leftovers.push(...found);
    console.log(`  ${name}: textos a revisar →`, found.slice(0, 6).join(" | "));
  }
};
const shot = async (name) => {
  await page.evaluate(() => { document.querySelector("#toast").textContent = ""; });
  await page.mouse.move(1, 1);
  await page.waitForTimeout(300);
  await spanishCheck(name);
  await page.screenshot({ path: `${shots}/${name}.png` });
};
const createProject = async (folder) => {
  await page.fill("#new-epub", `${work}/book.epub`);
  await page.fill("#new-folder", folder);
  await page.locator(".card:has(#new-epub) button.gold").click();
  await page.waitForSelector("ol.steps");
};
const closeProject = async () => {
  await page.click("#btn-close-project");
  await page.waitForSelector("#new-epub");
  await page.waitForTimeout(300);
};

await page.goto(url);
await page.waitForSelector("#new-epub");
await createProject(`${work}/proyecto`);
check((await page.locator(".title-row h1").textContent()) === "A Test Book", "el proyecto nuevo se llama como el libro");

// Renombrar desde el resumen.
await btn("Cambiar nombre").click();
await page.waitForSelector("#modal-backdrop:not([hidden])");
await page.waitForTimeout(200);
await page.keyboard.press("Control+A");
await page.keyboard.type(NEW_NAME);
await shot("1-cambiar-nombre");
await page.keyboard.press("Enter");
await page.waitForFunction((n) => document.querySelector(".title-row h1")?.textContent === n, NEW_NAME);
check((await page.locator("#project-name").textContent()) === NEW_NAME, "la barra de arriba muestra el nombre nuevo");
check((await page.locator(".page-head .lead").textContent()).includes("A Test Book"), "el título del libro sigue a la vista");
await shot("2-resumen-renombrado");

// En recientes, con un segundo proyecto: quitar, deshacer y volver a quitar.
await closeProject();
check((await recentNames())[0] === NEW_NAME, "recientes muestra el nombre nuevo");
await createProject(`${work}/proyecto2`);
await closeProject();
check(JSON.stringify(await recentNames()) === JSON.stringify(["A Test Book", NEW_NAME]), "dos proyectos en recientes");
await page.locator(".recent", { hasText: NEW_NAME }).locator(".recent-x").click();
await page.waitForSelector(".recent.removed");
check(JSON.stringify(await recentNames()) === JSON.stringify(["A Test Book"]), "quitado de la lista");
await shot("3-quitado-con-deshacer");
await btn("Deshacer", page.locator(".recent.removed")).click();
await page.waitForTimeout(300);
check(JSON.stringify(await recentNames()) === JSON.stringify(["A Test Book", NEW_NAME]) && await page.locator(".recent.removed").count() === 0,
  "deshacer lo devuelve a su lugar");
await page.locator(".recent", { hasText: NEW_NAME }).locator(".recent-x").click();
await page.waitForSelector(".recent.removed");
await page.evaluate(async () => { state.info = await rpc("app_info"); render(); });
await page.waitForTimeout(300);
check(JSON.stringify(await recentNames()) === JSON.stringify(["A Test Book"]) && await page.locator(".recent.removed").count() === 0,
  "al volver al inicio, sigue fuera de la lista");
await shot("4-recientes");

// Abrir el proyecto quitado: vuelve a la lista, primero y con su nombre.
await page.fill("#open-folder", `${work}/proyecto`);
await btn("Abrir", page.locator(".card:has(#open-folder)")).click();
await page.waitForSelector("ol.steps");
check((await page.locator(".title-row h1").textContent()) === NEW_NAME, "se abre con su nombre nuevo");
await closeProject();
check((await recentNames())[0] === NEW_NAME, "y vuelve primero a recientes");

// «Apoyar el proyecto»: sin enlaces no aparece; con enlaces (de ejemplo) abre la ventana con las tres formas.
check(await page.locator(".support-line").count() === 0, "sin enlaces de apoyo no aparece nada");
await page.evaluate(() => {
  state.info.apoyo = [
    { id: "github", nombre: "GitHub Sponsors", detalle: t("desde cualquier país") },
    { id: "cafecito", nombre: "Cafecito", detalle: t("en pesos, con Mercado Pago") },
    { id: "paypal", nombre: "PayPal", detalle: t("con tarjeta o cuenta de PayPal") }];
  render();
});
await page.locator(".support-line button").click();
await page.waitForSelector("#modal-backdrop:not([hidden])");
check(await page.locator("#modal-body .support-option").count() === 3, "la ventana de apoyo muestra las tres formas");
await shot("5-apoyar-el-proyecto");
await page.click("#modal-ok");

if (SPANISH) console.log("textos a revisar:", leftovers.length ? [...new Set(leftovers)] : 0);
if (leftovers.length) process.exitCode = 1;
console.log("errores:", errors.length ? errors : "ninguno");
if (errors.length) process.exitCode = 1;
await browser.close();
