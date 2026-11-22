import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

const fixture = path.join(
  process.env.CLIPPING_TEST_DATA_DIR!,
  "test-pattern.mp4",
);

test.beforeAll(() => {
  execFileSync(
    path.resolve(".venv/Scripts/python.exe"),
    ["scripts/make_test_video.py", "--output", fixture],
    { timeout: 40_000 },
  );
});

test("forwards a valid import larger than 10 MiB without truncation", async ({
  request,
}) => {
  // A legal MP4 free atom enlarges the fixture without changing its streams.
  const padding = Buffer.alloc(12 * 1024 ** 2);
  padding.writeUInt32BE(padding.length, 0);
  padding.write("free", 4, "ascii");
  const data = Buffer.concat([readFileSync(fixture), padding]);
  const response = await request.post(
    "/api/import?filename=large-proxy-test.mp4",
    {
      data,
      headers: { "Content-Type": "application/octet-stream" },
    },
  );
  expect(response.status()).toBe(201);
  expect((await response.json()).size_bytes).toBe(data.length);
});

test("import, preview, validate, export, download, and reopen", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/studio");
  await expect(page).toHaveTitle("Cliptric | Video workspace");
  await expect(
    page.getByRole("heading", { name: "Clip studio", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Import recording", exact: true }),
  ).toBeEnabled();
  const previousSource = (await page.locator(".source-video").count())
    ? await page.locator(".source-video").getAttribute("src")
    : null;
  const importedResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/import" &&
      response.request().method() === "POST",
  );
  await page
    .getByLabel("Choose recording", { exact: true })
    .setInputFiles(fixture);
  const imported = await importedResponse;
  expect(imported.status()).toBe(201);
  await expect(page.locator(".filename")).toHaveText("test-pattern.mp4");
  const source = page.getByLabel("Source recording preview", { exact: true });
  await expect(source).toHaveAttribute(
    "src",
    /\/api\/recordings\/[a-f0-9-]+\/media/,
  );
  if (previousSource)
    await expect(source).not.toHaveAttribute("src", previousSource);
  await expect
    .poll(() => source.evaluate((video: HTMLVideoElement) => video.readyState))
    .toBeGreaterThanOrEqual(1);
  await source.evaluate(async (video: HTMLVideoElement) => {
    video.muted = true;
    await video.play();
  });
  await expect
    .poll(() => source.evaluate((video: HTMLVideoElement) => video.currentTime))
    .toBeGreaterThan(0.2);
  await source.evaluate((video: HTMLVideoElement) => {
    video.pause();
    video.currentTime = 1;
  });
  await expect
    .poll(() => source.evaluate((video: HTMLVideoElement) => video.currentTime))
    .toBeCloseTo(1, 1);
  await page
    .getByRole("button", { name: "Use playhead", exact: true })
    .first()
    .click();
  await expect(page.getByLabel("Start (seconds)")).toHaveValue("1.000");
  await page.getByLabel("End (seconds)").fill("0.5");
  await expect(
    page.getByRole("button", { name: "Export clip", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("End (seconds)").fill("3");
  await expect(
    page.getByRole("button", { name: "Export clip", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Export clip", exact: true }).click();
  const download = page
    .getByRole("link", { name: "Download", exact: true })
    .first();
  await expect(download).toBeVisible({ timeout: 40_000 });
  await expect(page.locator(".export-item").first()).toContainText("Ready");
  const downloadEvent = page.waitForEvent("download");
  await download.click();
  const saved = await downloadEvent;
  expect(saved.suggestedFilename()).toMatch(/^clip-.*\.mp4$/);
  expect(await saved.failure()).toBeNull();
  await page.reload();
  await expect(page.locator(".filename")).toHaveText("test-pattern.mp4");
  await expect(
    page.getByRole("link", { name: "Download", exact: true }).first(),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});

// Explicit API simulations exercise ordering and recovery, not media correctness.
const firstRecording = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  name: "first.mp4",
  duration: 8,
  width: 640,
  height: 360,
  has_audio: true,
  size_bytes: 1000,
  created_at: "2026-11-17T00:00:00Z",
};
const secondRecording = {
  ...firstRecording,
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  name: "second.mp4",
};
const secondClip = {
  id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  recording_id: secondRecording.id,
  start: 1,
  end: 3,
  mode: "pad",
  status: "succeeded",
  duration: 2,
  error: null,
  size_bytes: 1000,
};

async function mockEditingEndpoints(page: import("@playwright/test").Page) {
  await page.route("**/api/recordings/*/draft", (route) =>
    route.fulfill({
      json: {
        start: 0,
        end: 8,
        mode: "pad",
        captions_enabled: false,
        caption_position: "lower",
        revision: 0,
      },
    }),
  );
  await page.route("**/api/recordings/*/transcription", (route) =>
    route.fulfill({ json: null }),
  );
}

test("switching recordings discards a late history response", async ({
  page,
}) => {
  await mockEditingEndpoints(page);
  let release!: () => void;
  let completed!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const finished = new Promise<void>((resolve) => {
    completed = resolve;
  });
  await page.route(/\/api\/recordings$/, (route) =>
    route.fulfill({ json: [firstRecording, secondRecording] }),
  );
  await page.route("**/api/exports/active", (route) =>
    route.fulfill({ json: null }),
  );
  await page.route("**/api/**/media", (route) =>
    route.fulfill({ path: fixture, contentType: "video/mp4" }),
  );
  await page.route(
    `**/api/recordings/${firstRecording.id}/clips?*`,
    async (route) => {
      await gate;
      await route.fulfill({
        json: [
          {
            ...secondClip,
            id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            recording_id: firstRecording.id,
          },
        ],
      });
      completed();
    },
  );
  await page.route(`**/api/recordings/${secondRecording.id}/clips?*`, (route) =>
    route.fulfill({ json: [secondClip] }),
  );
  const requested = page.waitForRequest((request) =>
    request.url().includes(`${firstRecording.id}/clips`),
  );
  await page.goto("/studio");
  await requested;
  await page
    .getByLabel("Recording", { exact: true })
    .selectOption(secondRecording.id);
  await expect(
    page.getByRole("heading", { name: "Clip cccccccc" }),
  ).toBeVisible();
  release();
  await finished;
  await expect(page.locator(".filename")).toHaveText("second.mp4");
  await expect(page.locator(".export-item")).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: "Clip cccccccc" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Clip dddddddd" }),
  ).toHaveCount(0);
});

test("status polling retries and exposes cancellation across recordings", async ({
  page,
}) => {
  await mockEditingEndpoints(page);
  let attempts = 0;
  let cancelled = false;
  let allowRecovery = false;
  await page.route(/\/api\/recordings$/, (route) =>
    route.fulfill({ json: [firstRecording, secondRecording] }),
  );
  await page.route("**/api/recordings/*/clips?*", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route("**/api/**/media", (route) =>
    route.fulfill({ path: fixture, contentType: "video/mp4" }),
  );
  await page.route("**/api/exports/active", (route) => {
    attempts++;
    if (!allowRecovery)
      return route.fulfill({
        status: 503,
        json: { detail: "Temporary status failure" },
      });
    return route.fulfill({
      json: cancelled
        ? null
        : {
            ...secondClip,
            status: "rendering",
            duration: null,
            size_bytes: null,
          },
    });
  });
  await page.route(`**/api/clips/${secondClip.id}/cancel`, (route) => {
    cancelled = true;
    return route.fulfill({ json: { ...secondClip, status: "cancelled" } });
  });
  await page.goto("/studio");
  await expect(page.getByText(/Export status unavailable/)).toBeVisible();
  allowRecovery = true;
  await expect(
    page.getByRole("button", { name: "Cancel active export" }),
  ).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/Export status unavailable/)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Export processing...", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Cancel active export" }).click();
  await expect(
    page.getByRole("button", { name: "Export clip", exact: true }),
  ).toBeEnabled();
  expect(attempts).toBeGreaterThan(1);
});
