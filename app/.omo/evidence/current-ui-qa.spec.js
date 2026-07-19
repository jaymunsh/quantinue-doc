const { test, expect } = require("@playwright/test");

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`${viewport.name} role ledger`, async ({ page }) => {
    const errors = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    await page.setViewportSize(viewport);
    await page.goto("http://127.0.0.1:8001/", { waitUntil: "networkidle" });
    await expect(page.locator(".role-detail-record")).toHaveCount(11);
    await expect(page.locator(".role-phase-divider")).toHaveCount(4);
    await expect(page.locator(".role-component-01 .role-item-list li")).toHaveCount(50);
    await expect(page.locator(".role-component-02 .role-item-list li")).toHaveCount(20);
    await expect(page.locator(".role-component-03 .role-item-list li")).toHaveCount(10);
    await expect(page.locator(".role-component-05 .role-item-list li")).toHaveCount(1003);
    await expect(page.locator(".role-component-06 .role-item-list li")).toHaveCount(25);
    await expect(page.locator(".decision-strip")).toHaveCount(3);
    await expect(page.locator(".role-component-10")).toContainText("로컬 모의 처리");
    await expect(page.locator(".role-component-11")).toContainText("T+1~T+5");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    expect(overflow).toBe(false);
    expect(errors).toEqual([]);
    await page.locator(".role-component-09").scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `.omo/evidence/runtime-debug-audit/ui-50-20-${viewport.name}.png`,
      fullPage: false,
    });
    for (const component of ["10", "11"]) {
      await page.locator(`.role-component-${component}`).screenshot({
        path: `.omo/evidence/runtime-debug-audit/ui-role-${component}-${viewport.name}.png`,
      });
    }
  });
}
