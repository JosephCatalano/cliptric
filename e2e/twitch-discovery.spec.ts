import { expect, test } from "@playwright/test";

test("Discover saves, edits, pauses, clones and deletes a Twitch scan", async ({
  page,
}) => {
  await page.route("**/api/twitch-discovery/lookup?*", async (route) => {
    await route.fulfill({
      json: [{ id: "32982", name: "Grand Theft Auto V" }],
    });
  });
  await page.route("**/api/twitch-discovery", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: { ...(await response.json()), configured: true },
    });
  });
  await page.goto("/discover");
  await expect(
    page.getByRole("heading", { name: "Twitch", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Scan name").fill("Gaming clips");
  await page.getByLabel("Source", { exact: true }).selectOption("game");
  await page.getByLabel("Search categories").fill("Grand Theft Auto");
  await page
    .getByRole("button", { name: "Grand Theft Auto V", exact: true })
    .click();
  await page.getByRole("button", { name: "Save scan", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Scan runs" })).toBeVisible();
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Pause", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Resume", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Clone", exact: true }).click();
  await expect(page.getByLabel("Scan name")).toHaveValue("Gaming clips copy");
  await page.getByRole("button", { name: "Save scan", exact: true }).click();
  await page.getByRole("button", { name: "Delete scan", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Delete scan", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /Gaming clips copy/ }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /Gaming clips Manual/ }),
  ).toBeVisible();
});
