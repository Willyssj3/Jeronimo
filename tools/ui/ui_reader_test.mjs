// Vista de lectura y voz (voz de Windows simulada; IA simulada; libro inventado).
import { createRequire } from "module";
const require = createRequire(process.env.NODE_PATH + "/");
const { chromium } = require("playwright");
const [url, work, shots, scheme = "light"] = process.argv.slice(2);
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, colorScheme: scheme });
await ctx.addInitScript(() => {
  // Voz simulada: «dice» cada trozo en 40 ms y lo apunta.
  window.__spoken = [];
  class FakeUtterance { constructor(text) { this.text = text; } }
  const synth = {
    _timer: null, speaking: false, paused: false,
    getVoices: () => [{ name: "Microsoft Sabina - Spanish (Mexico)", lang: "es-MX" }, { name: "Microsoft Zira", lang: "en-US" }],
    speak(u) { this.speaking = true; this._timer = setTimeout(() => { window.__spoken.push(u.text); this.speaking = false; u.onend && u.onend(); }, 40); },
    cancel() { clearTimeout(this._timer); this.speaking = false; },
    pause() { this.paused = true; }, resume() { this.paused = false; },
    onvoiceschanged: null,
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth });
  window.SpeechSynthesisUtterance = FakeUtterance;
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
const waitTaskDone = async () => {
  await page.waitForTimeout(500);
  await page.waitForFunction(() => document.querySelector("#taskbar").hidden === true, null, { timeout: 30000 });
  await page.waitForTimeout(600);
};
await page.goto(url);
await page.waitForSelector("text=Nuevo proyecto");
await page.fill("#new-epub", `${work}/book.epub`);
await page.fill("#new-folder", `${work}/proyecto-${scheme}`);
await page.click("text=Crear proyecto");
await page.waitForSelector("text=Progreso:");
await page.click("#sidebar >> text=Inteligencia artificial");
await page.click("label:has-text('Modo manual')");
await page.click("button:text-is('Guardar')");
await page.waitForSelector("text=Ajustes de IA guardados");
await page.click("#sidebar >> text=Capítulos");
await page.click("button.link:text-is('The Harbor')");
await page.waitForSelector("#manual-panel:not([hidden])");
await page.fill("#manual-panel textarea", [
  '<seg id="0">El puerto</seg>',
  '<seg id="1">Mirela caminó hasta Puertoarado antes del alba, como cada <g1>santo</g1> día.</seg>',
  '<seg id="2">—Los barcos llegan tarde —dijo el farero<x1/>.</seg>',
].join("\n"));
await page.click("button:text-is('Solo importar')");
await page.waitForSelector("text=Importados: 3");
await page.click("button:has-text('Leer y escuchar')");
await page.waitForSelector(".reader-page");
console.log("voces:", await page.locator(".voice-bar select >> nth=0").locator("option").allTextContents());
console.log("párrafos:", await page.locator(".reader-page .reader-p").count(), "sin traducir:", await page.locator(".reader-p.untranslated").count());
await page.evaluate(() => { document.querySelector("#toast").textContent = ""; });
await page.screenshot({ path: `${shots}/reader-${scheme}.png` });
await page.click("button:text-is('▶ Escuchar')");
await page.waitForFunction(() => window.__spoken.length >= 3, null, { timeout: 5000 });
await page.waitForTimeout(300);
console.log("leído:", JSON.stringify(await page.evaluate(() => window.__spoken)));
console.log("al terminar, botón:", await page.textContent(".voice-bar button.gold"), "| resaltados:", await page.locator(".reader-p.speaking").count());
// Clic en un párrafo: empieza desde ahí.
await page.evaluate(() => { window.__spoken = []; });
await page.click(".reader-page .reader-p >> nth=2");
await page.waitForFunction(() => window.__spoken.length >= 1, null, { timeout: 5000 });
await page.waitForTimeout(200);
console.log("desde el párrafo 3:", JSON.stringify(await page.evaluate(() => window.__spoken)));
// A+ cambia el tamaño y se recuerda.
await page.click("button:text-is('A+')");
console.log("tamaño:", await page.evaluate(() => document.querySelector(".reader-page").style.fontSize));
console.log("errores:", errors.length ? errors : "ninguno");
await browser.close();
