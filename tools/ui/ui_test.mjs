import { createRequire } from "module";
const require = createRequire(process.env.NODE_PATH + "/");
const { chromium } = require("playwright");
const [url, work, shots] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));
const shot = (n) => page.screenshot({ path: `${shots}/${n}.png`, fullPage: false });
const waitTaskDone = async () => {
  await page.waitForTimeout(500);
  await page.waitForFunction(() => document.querySelector("#taskbar").hidden === true, null, { timeout: 30000 });
  await page.waitForTimeout(800);
};
const step = (s) => console.log("·", s);

await page.goto(url);
await page.waitForSelector("text=Nuevo proyecto");
await shot("01-inicio");
step("crear proyecto");
await page.fill("#new-epub", `${work}/book.epub`);
await page.fill("#new-folder", `${work}/proyecto`);
await page.click("text=Crear proyecto");
await page.waitForSelector("text=Progreso:");
await shot("02-resumen");

step("añadir referencia");
await page.click("#sidebar >> text=Referencias");
await page.fill("#ref-src", `${work}/ref_en.epub`);
await page.fill("#ref-tgt", `${work}/ref_es.epub`);
await page.fill("#ref-name", "Nets");
await page.click("text=Emparejar");
await waitTaskDone();
await page.waitForSelector("text=calidad");
await shot("03-referencias");

step("configurar IA (local)");
await page.click("#sidebar >> text=Inteligencia artificial");
await page.click("label:has-text('Ollama')");
await page.fill("#ai-model", "modelo-de-prueba");
await page.click("button:has-text('Guardar')");
await page.waitForSelector("text=Ajustes de IA guardados");
await shot("04-ia");

step("generar conocimiento");
await page.click("#sidebar >> text=Estilo y glosario");
await page.click(".tabs >> text=Generar con IA");
await page.click("button:text-is('Generar')");
await waitTaskDone();
await page.click(".tabs >> text=Glosario");
await page.waitForSelector("input[value='Harrowgate']");
await shot("05-glosario");

step("omitir créditos y traducir");
await page.click("#sidebar >> text=Capítulos");
await page.click("tr:has-text('Credits') >> button:has-text('Omitir')");
await page.waitForSelector("tr.skipped");
await page.click("text=Traducir el siguiente capítulo");
await waitTaskDone();
await shot("06-capitulos");

step("revisar y editar");
await page.click("tr:has-text('The Harbor') >> button:has-text('Revisar')");
await page.waitForSelector(".seg");
const firstTgt = page.locator(".seg .tgt").first();
await firstTgt.click();
await page.keyboard.press("End");
await page.keyboard.type(" (editado)");
await page.locator("h1").first().click();
await page.waitForSelector("text=✓ guardado");
await shot("07-revision");
const segTexts = await page.locator(".seg .tgt").allTextContents();
console.log("tgt[0..1]:", JSON.stringify(segTexts.slice(0, 2)));
const italic = await page.locator(".seg .tgt .mk.t-i").count();
const chips = await page.locator(".seg .tgt .mk.x").count();
console.log("italic spans:", italic, "chips:", chips);

step("aprobar");
await page.click("button:has-text('Aprobar capítulo')");
await page.waitForSelector("text=Quitar aprobación");

step("modo manual en el capítulo 2");
await page.click("button:has-text('Siguiente ›')");
await page.waitForSelector("text=Modo manual");
await page.click("button:has-text('Modo manual')");
await page.click("button:text-is('Copiar bloque')");
await page.waitForSelector("text=Bloque copiado: párrafos");
await page.fill("#manual-panel textarea", '<seg id="0">Mirela durmió <g1>profundamente</g1> esa noche.</seg>\n<seg id="1">El viento siguió soplando del norte.</seg>');
await page.click("button:text-is('Solo importar')");
await page.waitForSelector("text=Importados: 2");
await shot("08-manual");

step("exportar");
await page.click("#sidebar >> text=Exportar");
await page.click("text=Generar EPUB traducido");
await page.waitForSelector("text=Listo:");
const exported = await page.locator(".result >> text=Listo:").locator("..").textContent();
console.log("export:", exported);
await shot("09-exportar");
await page.click("#sidebar >> text=Resumen");
await page.waitForSelector("text=Progreso:");
await shot("10-resumen-final");
console.log("errores de consola:", errors.length ? errors : "ninguno");
await browser.close();
