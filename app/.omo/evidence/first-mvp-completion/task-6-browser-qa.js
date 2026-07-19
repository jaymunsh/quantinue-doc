const { chromium } = require("playwright");
const fs = require("node:fs");

const base = "http://127.0.0.1:8765";
const out = ".omo/evidence/first-mvp-completion";
const viewports = [
  ["desktop", 1440, 1000],
  ["tablet", 768, 1024],
  ["mobile", 375, 812],
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const results = [];
  for (const state of ["empty", "filled"]) {
    if (state === "filled") {
      const response = await fetch(`${base}/api/runs`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ticker: "NVDA" }),
      });
      if (!response.ok) throw new Error(`fixture run failed: ${response.status}`);
    }
    for (const [name, width, height] of viewports) {
      const page = await browser.newPage({ viewport: { width, height } });
      const consoleErrors = [];
      const pageErrors = [];
      page.on("console", (message) => {
        if (message.type() === "error") consoleErrors.push(message.text());
      });
      page.on("pageerror", (error) => pageErrors.push(error.message));
      await page.goto(base, { waitUntil: "networkidle" });
      const facts = await page.evaluate(() => {
        const root = document.documentElement;
        return {
          scrollWidth: root.scrollWidth,
          clientWidth: root.clientWidth,
          mode: document.querySelector(".runtime-contract")?.textContent || "",
          positions: document.querySelectorAll(".portfolio-table tbody tr").length,
          orders: document.querySelectorAll("#local-orders-title").length
            ? document.querySelector("#local-orders-title").closest("section").querySelectorAll(".ledger-list > li").length
            : 0,
          fills: document.querySelectorAll("#local-fills-title").length
            ? document.querySelector("#local-fills-title").closest("section").querySelectorAll(".ledger-list > li").length
            : 0,
          newsItems: document.querySelectorAll(".news-selection-list > li").length,
          hasRealized: document.body.textContent.includes("해당 없음 · 1차 매수 전용"),
          hasExternalOff: document.body.textContent.includes("외부 주문 OFF"),
          portfolioTabIndex: document.querySelector(".portfolio-table-wrap")?.tabIndex ?? -1,
        };
      });
      if (facts.scrollWidth > facts.clientWidth) throw new Error(`${state}/${name} overflow`);
      if (!facts.hasRealized || !facts.hasExternalOff) throw new Error(`${state}/${name} copy`);
      if (consoleErrors.length || pageErrors.length) throw new Error(`${state}/${name} errors`);
      if (state === "empty" && facts.positions !== 0) throw new Error(`${name} not empty`);
      if (state === "filled" && facts.positions !== 1) throw new Error(`${name} not filled`);
      if (state === "empty" && (facts.orders !== 0 || facts.fills !== 0)) throw new Error(`${name} ledger not empty`);
      if (state === "filled" && (facts.orders !== 1 || facts.fills !== 1)) throw new Error(`${name} ledger not filled`);
      const expectedTabIndex = state === "filled" && name === "tablet" ? 0 : -1;
      if (facts.portfolioTabIndex !== expectedTabIndex) throw new Error(`${state}/${name} tabindex`);
      if (facts.portfolioTabIndex === 0) {
        await page.focus(".portfolio-table-wrap");
        const outline = await page.locator(".portfolio-table-wrap").evaluate(
          (element) => getComputedStyle(element).outlineStyle,
        );
        if (outline === "none") throw new Error(`${state}/${name} focus ring`);
      }
      await page.screenshot({ path: `${out}/task-6-${state}-${name}.png`, fullPage: true });
      results.push({ state, name, width, height, ...facts, consoleErrors, pageErrors });
      await page.close();
    }
  }
  await browser.close();
  fs.writeFileSync(`${out}/task-6-browser-qa.json`, JSON.stringify(results, null, 2));
})().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
