// Buscar y reemplazar en la interfaz (IA simulada, libro inventado).
import { createRequire } from "module";
const require = createRequire(process.env.NODE_PATH + "/");
const { chromium } = require("playwright");
const [url, work, shots] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
const clearToasts = () => page.evaluate(() => { document.querySelector("#toast").textContent = ""; });
const waitTaskDone = async () => {
  await page.waitForTimeout(500);
  await page.waitForFunction(() => document.querySelector("#taskbar").hidden === true, null, { timeout: 30000 });
  await page.waitForTimeout(600);
};
await page.goto(url);
await page.waitForSelector("text=Nuevo proyecto");
await page.fill("#new-epub", `${work}/book.epub`);
await page.fill("#new-folder", `${work}/proyecto`);
await page.click("text=Crear proyecto");
await page.waitForSelector("text=Progreso:");
await page.click("#sidebar >> text=Referencias");
await page.fill("#ref-src", `${work}/ref_en.epub`);
await page.fill("#ref-tgt", `${work}/ref_es.epub`);
await page.click("text=Emparejar");
await waitTaskDone();
await page.click("#sidebar >> text=Inteligencia artificial");
await page.click("label:has-text('Ollama')");
await page.fill("#ai-model", "modelo-de-prueba");
await page.click("button:text-is('Guardar')");
await page.waitForSelector("text=Ajustes de IA guardados");
await page.click("#sidebar >> text=Estilo y glosario");
await page.click(".tabs >> text=Generar con IA");
await page.click("button:text-is('Generar')");
await waitTaskDone();
await page.click("#sidebar >> text=Capítulos");
await page.click("text=Traducir el siguiente capítulo");
await waitTaskDone();

// 1) Buscar en la traducción y reemplazar con mayúsculas exactas.
await page.click("#sidebar >> text=Buscar");
await page.fill("input[type=search]", "HARROWGATE");
await page.fill("input[placeholder^='Texto nuevo']", "Puertoarado");
await page.check("text=Distinguir mayúsculas");
await page.click("button:text-is('Buscar')");
await page.waitForSelector("text=coincidencia");
console.log("resultado:", (await page.locator(".row:has-text('coincidencia')").first().textContent()).replace(/\s+/g, " ").trim());
await clearToasts();
await page.screenshot({ path: `${shots}/search-results.png` });
await page.click("button:text-is('Reemplazar en los marcados')");
await page.click("#modal-ok");
await page.waitForSelector("text=reemplazo en");
console.log("deshacer visible:", await page.locator("text=Último reemplazo").count());

// 2) Buscar en el original.
await page.selectOption("#view select", "original");
await page.fill("input[type=search]", "keeper");
await page.uncheck("text=Distinguir mayúsculas");
await page.click("button:text-is('Buscar')");
await page.waitForSelector("text=Traducción:");
await clearToasts();
await page.screenshot({ path: `${shots}/search-original.png` });

// 3) Cambiar la traducción en el glosario: ofrece actualizar lo ya traducido.
await page.click("#sidebar >> text=Estilo y glosario");
await page.click(".tabs >> text=Glosario");
await page.fill("input[value='Puertoarado']", "Puerto Arado");
await page.click("button:text-is('Guardar')");
await page.waitForSelector("text=¿Actualizar lo ya traducido?");
await page.screenshot({ path: `${shots}/glossary-offer.png` });
await page.click("button:text-is('Revisar y reemplazar')");
await page.waitForSelector("text=coincidencia");
console.log("precargado:", await page.inputValue("input[type=search]"), "→", await page.inputValue("input[placeholder^='Texto nuevo']"));
await page.click("button:text-is('Reemplazar en los marcados')");
await page.click("#modal-ok");
await page.waitForSelector("text=reemplazo en");
// 4) Deshacer.
await page.click("button:text-is('Deshacer')");
await page.waitForSelector("text=Reemplazo deshecho");
console.log("errores:", errors.length ? errors : "ninguno");
await browser.close();
