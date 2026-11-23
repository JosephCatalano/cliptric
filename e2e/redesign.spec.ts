import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

let projectName: string;
let projectId: string;

test.beforeAll(async ({ request }, info) => {
  projectName = `Redesign QA project (${info.project.name})`;
  const fixture = path.join(
    process.env.CLIPPING_TEST_DATA_DIR!,
    "redesign.mp4",
  );
  execFileSync(path.resolve(".venv/Scripts/python.exe"), [
    "scripts/make_test_video.py",
    "--output",
    fixture,
  ]);
  const upload = await request.post("/api/import?filename=redesign.mp4", {
    data: readFileSync(fixture),
    headers: { "content-type": "application/octet-stream" },
  });
  expect(upload.status()).toBe(201);
  const source = await upload.json();
  const created = await request.post("/api/projects", {
    data: {
      id: randomUUID(),
      title: projectName,
      recording_ids: [source.id],
      settings: {
        duration_mode: "full",
        analysis_start: 0,
        analysis_end: source.duration,
        caption_preset: "none",
      },
    },
  });
  expect(created.status(), await created.text()).toBe(202);
  const { id } = await created.json();
  projectId = id;
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/projects/${id}`)).json()).status,
      {
        timeout: 45_000,
      },
    )
    .toBe("ready");
});

test("library, full-page editor and saved export", async ({
  page,
  request,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/workspace");
  await expect(
    page.getByRole("heading", { name: "Create something worth sharing." }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Recent projects" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
  if (info.project.name === "desktop") {
    const thumbnail = page
      .getByRole("region", { name: "Recent projects" })
      .locator("img")
      .first();
    await expect
      .poll(async () => (await thumbnail.boundingBox())?.width)
      .toBeLessThanOrEqual(280);
  }
  await page.screenshot({
    caret: "initial",
    path: info.outputPath("home.png"),
  });

  await page.goto("/projects");
  await page.getByRole("button", { name: "List view" }).click();
  await page
    .getByRole("searchbox", { name: "Search projects" })
    .fill(projectName);
  await expect(page.getByRole("table")).toBeVisible();
  await page.getByRole("link", { name: projectName, exact: true }).click();
  await expect(
    page.getByRole("heading", { name: projectName, exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /^More options for/ })
    .first()
    .click();
  await page.getByRole("menuitem", { name: "Edit clip", exact: true }).click();
  await expect(page).toHaveURL(/\?clip=/);
  await expect(
    page.getByRole("region", { name: "Clip preview" }),
  ).toBeVisible();
  await expect(
    page.getByRole("slider", { name: "Clip preview playhead" }),
  ).toBeVisible();
  await expect(page.getByRole("slider", { name: "Trim start" })).toBeVisible();
  const title = page.getByRole("textbox", { name: "Clip title", exact: true });
  await title.fill("");
  await page.getByRole("tab", { name: "Captions" }).click();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(title).toBeFocused();
  await title.fill("Edited in the workspace");
  await page.getByRole("tab", { name: "Captions" }).click();
  await expect(
    page.getByRole("tab", { name: "Captions", selected: true }),
  ).toBeVisible();
  await page.screenshot({
    caret: "initial",
    path: info.outputPath("editor.png"),
  });
  await page.getByRole("tab", { name: "Content" }).click();
  await expect(title).toHaveValue("Edited in the workspace");
  page.once("dialog", (dialog) => void dialog.dismiss());
  await page.getByRole("button", { name: "Back to project" }).click();
  await expect(title).toHaveValue("Edited in the workspace");
  const browserBack = page.waitForEvent("dialog");
  await page.evaluate(() => window.history.back());
  await (await browserBack).dismiss();
  await expect(page).toHaveURL(/\?clip=/);
  await expect(title).toHaveValue("Edited in the workspace");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page).not.toHaveURL(/\?clip=/);
  await expect(
    page.getByRole("heading", { name: "Edited in the workspace" }),
  ).toBeVisible();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/projects/${projectId}`)).json())
          .items[0].export?.status,
    )
    .toBe("succeeded");
  await page.goBack();
  await expect(
    page.getByRole("searchbox", { name: "Search projects" }),
  ).toHaveValue(projectName);
  await expect(
    page.getByRole("button", { name: "List view", pressed: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("publishing and prompt navigation at supported widths", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/calendar");
  await expect(page.getByText("Local scheduler connected")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Queue", pressed: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Calendar", exact: true }).click();
  await expect(page.getByRole("table")).toBeVisible();
  await page
    .getByRole("button", { name: "Schedule post", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Schedule post" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await page.goto("/settings");
  const stages = page.getByRole("navigation", { name: "Prompt stages" });
  const stagePicker = page.getByRole("combobox", {
    name: "Prompt stage",
    exact: true,
  });
  const selectStage = async (index: number) => {
    if (info.project.name === "mobile")
      await stagePicker.selectOption({ index });
    else await stages.getByRole("button").nth(index).click();
  };
  const editor = page.getByRole("textbox", { name: "Instructions" });
  await editor.fill("Local QA draft");
  await selectStage(1);
  await expect(editor).not.toHaveValue("Local QA draft");
  await selectStage(0);
  await expect(editor).toHaveValue("Local QA draft");
  await page.getByRole("button", { name: "Discard draft" }).click();
  await expect(editor).not.toHaveValue("Local QA draft");

  if (info.project.name === "desktop") {
    for (const width of [1920, 1440, 1280, 1024, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const [route, heading] of [
        ["/workspace", "Create something worth sharing."],
        ["/projects", "Projects"],
        ["/calendar", "Publishing"],
        ["/connections", "Accounts"],
        ["/settings", "Settings"],
      ]) {
        await page.goto(route);
        await expect(
          page.getByRole("heading", { name: heading, exact: true }),
        ).toBeVisible();
        await expect
          .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
          .toBeLessThanOrEqual(width);
        if (width === 1440 || width === 390) {
          await page.screenshot({
            caret: "initial",
            path: info.outputPath(`${route.slice(1)}-${width}.png`),
          });
        }
      }
    }
  }
  expect(errors).toEqual([]);
});

test("publishing keeps exact account approval and renders saved media", async ({
  page,
  request,
}, info) => {
  const project = await (
    await request.get(`/api/projects/${projectId}`)
  ).json();
  const clip = project.items[0].export;
  const accounts = [
    { account_id: "channel-first", label: "First test channel" },
    { account_id: "channel-second", label: "Second test channel" },
  ];
  const writes: string[] = [];
  await page.route("**/api/youtube-publishing**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== "GET") {
      writes.push(route.request().method());
      await route.abort();
    } else if (url.pathname.endsWith("/creator")) {
      const selected = accounts.find(
        (account) => account.account_id === url.searchParams.get("account_id"),
      );
      expect(selected).toBeTruthy();
      await route.fulfill({
        json: {
          account_id: selected!.account_id,
          channel: { id: selected!.account_id, title: selected!.label },
          categories: [{ id: "20", title: "Gaming" }],
        },
      });
    } else if (url.pathname.includes("/exports/")) {
      await route.fulfill({
        json: {
          id: clip.id,
          duration: clip.duration,
          width: 1080,
          height: 1920,
          size_bytes: clip.size_bytes,
          caption_warning: null,
        },
      });
    } else if (url.pathname.endsWith("/posts")) {
      await route.fulfill({ json: [] });
    } else {
      await route.fulfill({
        json: { configured: true, connected: true, account_id: null, accounts },
      });
    }
  });
  await page.goto(`/projects/${projectId}`);
  await page
    .getByRole("button", { name: /^More options for/ })
    .first()
    .click();
  await page.getByRole("menuitem", { name: "Publish or schedule" }).click();
  await page
    .getByLabel("Account", { exact: true })
    .selectOption(accounts[0].account_id);
  await page.getByRole("link", { name: /review/i }).click();
  const video = page.getByLabel("Saved clip preview");
  await expect
    .poll(() =>
      video.evaluate((element: HTMLVideoElement) => element.readyState),
    )
    .toBeGreaterThanOrEqual(1);
  await expect(video).toHaveAttribute("src", `/api/clips/${clip.id}/media`);
  const title = page.getByRole("textbox", { name: "Title", exact: true });
  await title.fill("Approved preview title");
  const approval = page.getByRole("checkbox", {
    name: /I reviewed this exact video/,
  });
  const submit = page.getByRole("button", {
    name: "Approve and upload to YouTube",
  });
  await expect(submit).toBeDisabled();
  await approval.check();
  await expect(submit).toBeEnabled();
  await page
    .getByLabel("YouTube destination account")
    .selectOption(accounts[1].account_id);
  await expect(
    page.getByText("Upload to Second test channel", { exact: true }),
  ).toBeVisible();
  await expect(approval).not.toBeChecked();
  await expect(submit).toBeDisabled();
  expect(writes).toEqual([]);
  await page.screenshot({
    caret: "initial",
    path: info.outputPath("publish.png"),
  });
});
