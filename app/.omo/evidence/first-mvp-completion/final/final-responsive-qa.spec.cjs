const { test, expect } = require("@playwright/test");

const viewports = [
  { width: 1440, height: 1000 },
  { width: 768, height: 1024 },
  { width: 375, height: 812 },
];

for (const viewport of viewports) {
  test(`current Docker dashboard ${viewport.width}px`, async ({ page }) => {
    const consoleErrors = [];
    const pageErrors = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.setViewportSize(viewport);
    const response = await page.goto("http://127.0.0.1:8011/", {
      waitUntil: "networkidle",
    });
    expect(response?.status()).toBe(200);

    await expect(page.getByRole("heading", { name: "자산·보유·주문 원장" })).toBeVisible();
    await expect(page.getByText("$1,000,000.00", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("해당 없음 · 1차 매수 전용")).toBeVisible();
    await expect(page.getByRole("heading", { name: "01–11 역할별 상세 처리 데이터" })).toBeVisible();
    await expect(page.locator(".role-detail-record")).toHaveCount(11);
    await expect(page.locator(".role-component-01")).toContainText("50개");
    await expect(page.locator(".role-component-02")).toContainText("20개");
    await expect(page.locator(".role-component-03")).toContainText("10개");
    await expect(page.locator(".role-component-06")).toContainText("뉴스");
    await expect(page.locator(".role-component-06")).toContainText("전체 수집100");
    await expect(page.locator(".role-component-06")).toContainText("관련 뉴스94");
    await expect(page.locator(".role-component-06")).toContainText("제외6");
    await expect(page.locator(".role-component-06")).toContainText("대표 분석1");
    await expect(page.locator(".role-component-06")).toContainText("관련성 규칙 대표 뉴스");
    await expect(page.locator(".role-component-07 .role-description")).toContainText("매수·보유 제안");
    await expect(page.locator(".role-component-07 .role-description")).not.toContainText("매도");
    await expect(page.getByText("0cd2184291fc45f7b112c5cebed276fa", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "주문 내역" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "체결 내역" })).toBeVisible();

    const overflow = await page.evaluate(() => ({
      document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      body: document.body.scrollWidth - document.body.clientWidth,
    }));
    expect(overflow.document).toBeLessThanOrEqual(0);
    expect(overflow.body).toBeLessThanOrEqual(0);
    expect(consoleErrors).toEqual([]);
    expect(pageErrors).toEqual([]);

    await page.screenshot({
      path: `.omo/evidence/first-mvp-completion/final/dashboard-${viewport.width}.png`,
      fullPage: true,
    });
  });
}
