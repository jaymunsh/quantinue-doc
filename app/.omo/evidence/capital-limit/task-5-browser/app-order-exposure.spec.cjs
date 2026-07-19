const { test, expect } = require("playwright/test");

const base = process.env.CAPITAL_LIMIT_BASE_URL || "http://127.0.0.1:8150";
const evidenceDirectory = ".omo/evidence/capital-limit/task-5-browser";
const viewports = [
  [1440, 1000],
  [1024, 900],
  [768, 900],
  [390, 844],
];

test("server-rendered app-order exposure panel stays usable across viewports", async ({ browser }) => {
  const errors = [];
  const contexts = await Promise.all(
    viewports.map(([width, height]) =>
      browser.newContext({ viewport: { width, height } }),
    ),
  );
  const pages = await Promise.all(contexts.map((context) => context.newPage()));

  for (const page of pages) {
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(error.message));
  }

  await Promise.all(pages.map((page) => page.goto(base, { waitUntil: "networkidle" })));

  for (let index = 0; index < pages.length; index += 1) {
    const page = pages[index];
    const [width] = viewports[index];
    const panel = page.locator("#app-order-exposure");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("Quantinue 앱 주문 계획 노출");
    await expect(panel).toContainText("$1,000.00");
    await expect(panel).toContainText("Alpaca 잔고·포지션·실제 체결 금액이 아닙니다.");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `${evidenceDirectory}/app-order-exposure-${width}.png`,
      fullPage: true,
    });
  }

  await pages[0].evaluate(() => window.scrollTo(0, 0));
  await pages[0].keyboard.press("Tab");
  await expect(pages[0].locator(".skip-link")).toBeFocused();
  await pages[0].keyboard.press("Tab");
  await expect(pages[0].locator("#ticker")).toBeFocused();

  expect(errors).toEqual([]);
  await Promise.all(contexts.map((context) => context.close()));
});

test("app-order exposure panel remains meaningful without JavaScript", async ({ browser }) => {
  const context = await browser.newContext({
    javaScriptEnabled: false,
    viewport: { width: 390, height: 844 },
  });
  const page = await context.newPage();

  await page.goto(base, { waitUntil: "domcontentloaded" });
  const panel = page.locator("#app-order-exposure");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("설정 한도");
  await expect(panel).toContainText("계획·예약");
  await expect(panel).toContainText("남은 계획 한도");
  await expect(panel).toContainText("$1,000.00");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `${evidenceDirectory}/app-order-exposure-390-no-js.png`,
    fullPage: true,
  });
  await context.close();
});
