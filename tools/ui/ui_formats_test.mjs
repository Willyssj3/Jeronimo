// Exportar a Word y PDF desde la interfaz (IA simulada, libro inventado).
import { createRequire } from "module";
const require = createRequire(process.env.NODE_PATH + "/");
const { chromium } = require("playwright");
const [url, work, shots] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1360, height: 1100 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
const waitTaskDone = async () => {
  await page.waitForTimeout(500);
  await page.waitForFunction(() => document.querySelector("#taskbar").hidden === true, null, { timeout: 30000 });
  await page.waitForTimeout(700);
};
await page.goto(url);
await page.waitForSelector("text=Nuevo proyecto");
await page.fill("#new-epub", `${work}/book.epub`);
await page.fill("#new-folder", `${work}/proyecto`);
await page.click("text=Crear proyecto");
await page.waitForSelector("text=Progreso:");
await page.click("#sidebar >> text=Inteligencia artificial");
await page.click("label:has-text('Ollama')");
await page.fill("#ai-model", "modelo-de-prueba");
await page.click("button:text-is('Guardar')");
await page.waitForSelector("text=Ajustes de IA guardados");
await page.click("#sidebar >> text=Capítulos");
await page.click("text=Traducir el siguiente capítulo");
await waitTaskDone();
await page.click("#sidebar >> text=Exportar");
for (const label of ["Word (.docx)", "Word bilingüe (tabla)", "PDF tamaño libro (A5)"]) {
  await page.click(`button:text-is('${label}')`);
  await waitTaskDone();
  await page.waitForSelector(".card:has-text('Otros formatos') .result");
  console.log(label, "→", (await page.textContent(".card:has-text('Otros formatos') .result p")).split("/").pop());
}
await page.evaluate(() => { document.querySelector("#toast").textContent = ""; });
await page.locator(".card:has-text('Otros formatos')").scrollIntoViewIfNeeded();
await page.screenshot({ path: `${shots}/formats-card.png` });
console.log("errores:", errors.length ? errors : "ninguno");
await browser.close();
