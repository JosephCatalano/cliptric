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

test("A connected Twitch editor can prepare a clip directly", async ({
  page,
  request,
}) => {
  const saved = await request.post("/api/twitch-discovery/scans", {
    data: { name: "Download test scan", source: "game", game_id: "32982" },
  });
  expect(saved.ok()).toBeTruthy();
  const scan = await saved.json();
  await page.route("**/api/twitch-discovery", (route) =>
    route.fulfill({
      json: {
        configured: true,
        scans: [scan],
        editor: {
          available: true,
          profile: { editor_id: "12", login: "editor" },
        },
        intake: [],
      },
    }),
  );
  await page.route(`**/api/twitch-discovery/scans/${scan.id}`, (route) =>
    route.fulfill({
      json: {
        ...scan,
        runs: [],
        intake: [],
        clips: [
          {
            id: "TestClip",
            broadcaster_id: "34",
            broadcaster_name: "Test Channel",
            title: "Test clip",
            duration: 25,
            view_count: 10,
            created_at: "2026-11-23T12:00:00Z",
            state: "new",
            rank: 1,
            can_request_download: true,
          },
        ],
      },
    }),
  );
  await page.route(
    `**/api/twitch-discovery/scans/${scan.id}/prepare`,
    (route) => {
      expect(route.request().postDataJSON()).toEqual({ ids: ["TestClip"] });
      return route.fulfill({ status: 202, json: { queued: 1 } });
    },
  );

  await page.goto("/twitch");
  await page.getByRole("button", { name: /Download test scan/ }).click();
  await page.getByRole("checkbox", { name: "Select Test clip" }).check();
  await page.getByRole("button", { name: "Prepare selected" }).click();
  await expect(
    page.getByRole("status").getByText("Queued 1 clip."),
  ).toBeVisible();
});
