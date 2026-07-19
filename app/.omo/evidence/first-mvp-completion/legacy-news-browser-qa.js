const { chromium } = require("playwright");
const fs = require("node:fs");

const base = "http://127.0.0.1:8011";
const out = ".omo/evidence/first-mvp-completion";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const results = [];
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 375, 812]]) {
    const page = await browser.newPage({ viewport: { width, height } });
    const errors = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(base, { waitUntil: "networkidle" });
    const facts = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      hasUnscored: document.body.textContent.includes("관련성 점수 미산정"),
      hasExplanation: document.body.textContent.includes("기존 실행은 관련성 선별 점수를 기록하지 않았으며"),
      fixtureLinks: document.querySelectorAll('a[href="https://example.invalid/fixture-news"]').length,
    }));
    if (facts.scrollWidth > facts.clientWidth) throw new Error(`${name}: horizontal overflow`);
    if (!facts.hasUnscored || !facts.hasExplanation) throw new Error(`${name}: legacy explanation missing`);
    if (facts.fixtureLinks !== 0) throw new Error(`${name}: fixture URL is clickable`);
    if (errors.length) throw new Error(`${name}: browser errors: ${errors.join(" | ")}`);
    await page.screenshot({ path: `${out}/legacy-news-${name}.png`, fullPage: true });
    results.push({ name, width, height, ...facts, errors });
    await page.close();
  }
  await browser.close();
  fs.writeFileSync(`${out}/legacy-news-browser-qa.json`, JSON.stringify(results, null, 2));
})().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
