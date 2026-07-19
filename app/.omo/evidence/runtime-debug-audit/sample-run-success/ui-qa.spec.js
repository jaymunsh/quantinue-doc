const { test, expect } = require("@playwright/test");

const RUN_ID = "f5117bb1d4024989bbdbfd67b7b7bae0";
const OUT = ".omo/evidence/runtime-debug-audit/sample-run-success";

for (const viewport of [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`${viewport.name} completed run detail`, async ({ page }) => {
    const consoleErrors = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => consoleErrors.push(error.message));

    await page.setViewportSize(viewport);
    await page.goto("http://127.0.0.1:8001/", { waitUntil: "networkidle" });

    await expect(page.getByText(RUN_ID, { exact: true }).first()).toBeVisible();
    await expect(page.locator(".role-detail-record")).toHaveCount(11);
    await expect(page.locator(".role-detail-record.role-detail-completed")).toHaveCount(11);
    await expect(page.locator('.role-item-list[aria-label="01 상세 항목"] > li')).toHaveCount(50);
    await expect(page.locator('.role-item-list[aria-label="02 상세 항목"] > li')).toHaveCount(45);
    await expect(page.locator('.role-item-list[aria-label="03 상세 항목"] > li')).toHaveCount(10);
    await expect(page.getByText("qwen3.6:35b-a3b-nvfp4", { exact: true }).first()).toBeVisible();

    const layout = await page.evaluate(() => {
      const viewportWidth = document.documentElement.clientWidth;
      const overflowing = [...document.querySelectorAll("body *")]
        .filter((node) => {
          const rect = node.getBoundingClientRect();
          return rect.left < -1 || rect.right > viewportWidth + 1;
        })
        .map((node) => ({ tag: node.tagName, className: node.className, text: (node.textContent || "").slice(0, 80) }));
      return {
        viewportWidth,
        documentWidth: document.documentElement.scrollWidth,
        overflowing,
      };
    });
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
    expect(layout.overflowing).toEqual([]);
    expect(consoleErrors).toEqual([]);

    await page.screenshot({ path: `${OUT}/${viewport.name}-full.png`, fullPage: true });
    await page.locator(".role-detail-panel").screenshot({ path: `${OUT}/${viewport.name}-roles.png` });
  });
}
