import { expect, test } from "@playwright/test";

test("Autopilot displays real saved scans and activation blockers", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/twitch-discovery/scans", {
    data: {
      name: "Autopilot browser test",
      source: "game",
      game_id: "32982",
      game_name: "Grand Theft Auto V",
    },
  });
  expect(created.status()).toBe(201);
  const scan = await created.json();
  try {
    await page.goto("/autopilot");
    await expect(
      page.getByRole("heading", { name: "Autopilot", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Review queue" }),
    ).toBeVisible();
    await page.getByLabel("Autopilot browser test").check();
    await page.getByLabel("Mode", { exact: true }).selectOption("discover");
    await page.getByRole("button", { name: "Enable Autopilot" }).click();
    await expect(
      page.getByText(
        /configure Twitch app credentials|choose a saved Twitch scan with recurrence enabled/i,
      ),
    ).toBeVisible();
    await expect(
      page.getByText("No Autopilot imports yet.", { exact: false }),
    ).toBeVisible();
  } finally {
    await request.delete(`/api/twitch-discovery/scans/${scan.id}`);
  }
});
