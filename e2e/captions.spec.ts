import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

const fixture = path.join(
  process.env.CLIPPING_TEST_DATA_DIR!,
  "caption-test.mp4",
);
const recording = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "speech.mp4",
  duration: 8,
  width: 640,
  height: 360,
  has_audio: true,
  size_bytes: 1000,
  created_at: "2026-11-17T00:00:00Z",
};
const otherRecording = {
  ...recording,
  id: "22222222-2222-4222-8222-222222222222",
  name: "other.mp4",
};
const initialDraft = {
  start: 0,
  end: 8,
  mode: "pad",
  captions_enabled: false,
  caption_position: "lower",
  revision: 0,
};
const initialJob = {
  id: "33333333-3333-4333-8333-333333333333",
  recording_id: recording.id,
  status: "succeeded",
  progress: 1,
  model: "small.en",
  language: "en",
  error: null as string | null,
  warning: null as string | null,
  word_count: 2,
  revision: 1,
};
const initialWords = [
  { id: 0, start: 1, end: 1.4, text: "Helo", probability: 0.4, edited: false },
  {
    id: 1,
    start: 1.5,
    end: 2,
    text: "world.",
    probability: 0.98,
    edited: false,
  },
];

// These explicit API simulations verify browser state transitions. Real speech
// inference and rendered caption accuracy are verified by backend integration checks.
async function workspace(page: Page) {
  const state = {
    drafts: new Map([
      [recording.id, { ...initialDraft }],
      [otherRecording.id, { ...initialDraft }],
    ]),
    job: { ...initialJob } as typeof initialJob | null,
    words: initialWords.map((word) => ({ ...word })),
    exported: null as Record<string, unknown> | null,
    clips: [] as Record<string, unknown>[],
  };
  await page.route(/\/api\/recordings$/, (route) =>
    route.fulfill({ json: [recording, otherRecording] }),
  );
  await page.route("**/api/**/media*", (route) =>
    route.fulfill({ path: fixture, contentType: "video/mp4" }),
  );
  await page.route("**/api/exports/active", (route) =>
    route.fulfill({ json: null }),
  );
  await page.route("**/api/recordings/*/draft", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON();
      if (body.revision !== state.drafts.get(id)?.revision)
        return route.fulfill({
          status: 409,
          json: { detail: "Selection changed" },
        });
      state.drafts.set(id, { ...body, revision: body.revision + 1 });
    }
    return route.fulfill({ json: state.drafts.get(id) });
  });
  await page.route("**/api/recordings/*/transcription", (route) =>
    route.fulfill({
      json: new URL(route.request().url()).pathname.includes(recording.id)
        ? state.job
        : null,
    }),
  );
  await page.route(`**/api/transcriptions/${initialJob.id}/words?*`, (route) =>
    route.fulfill({
      json: {
        id: initialJob.id,
        revision: state.job?.revision ?? 0,
        words: state.words,
      },
    }),
  );
  await page.route(`**/api/transcriptions/${initialJob.id}/words`, (route) => {
    const body = route.request().postDataJSON();
    if (!state.job || body.revision !== state.job.revision)
      return route.fulfill({
        status: 409,
        json: { detail: "Transcript changed" },
      });
    for (const update of body.updates) {
      const word = state.words.find((item) => item.id === update.id)!;
      word.text = update.text;
      word.edited = true;
    }
    state.job.revision++;
    return route.fulfill({
      json: { id: initialJob.id, revision: state.job.revision },
    });
  });
  await page.route("**/api/recordings/*/clips?*", (route) =>
    route.fulfill({ json: state.clips }),
  );
  await page.route("**/api/recordings/*/clips", (route) => {
    state.exported = route.request().postDataJSON();
    const clip = {
      ...state.exported,
      id: "44444444-4444-4444-8444-444444444444",
      recording_id: recording.id,
      status: "succeeded",
      duration: Number(state.exported!.end) - Number(state.exported!.start),
      error: null,
      size_bytes: 1000,
    };
    state.clips = [clip];
    return route.fulfill({ status: 202, json: clip });
  });
  return state;
}

test.beforeAll(() => {
  execFileSync(
    path.resolve(".venv/Scripts/python.exe"),
    ["scripts/make_test_video.py", "--output", fixture],
    { timeout: 40_000 },
  );
});

test("corrections and selected caption settings survive export, switching and reopening", async ({
  page,
}) => {
  const state = await workspace(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/studio");
  await expect(page.getByLabel("Start (seconds)")).toBeEnabled();
  await page.getByLabel("Start (seconds)").fill("0.5");
  await page.getByLabel("End (seconds)").fill("3");
  const word = page.getByRole("textbox", { name: "Word 1 at 1.00s" });
  await expect(word).toHaveValue("Helo");
  await word.fill("Hello");
  await expect(page.getByLabel("Start (seconds)")).toBeDisabled();
  await expect(page.getByLabel("Recording", { exact: true })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Export clip", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Save corrections", exact: true })
    .click();
  await expect(page.getByText("Caption corrections saved.")).toBeVisible();
  await page.getByLabel("Include captions").check();
  await page.getByLabel("Caption position").selectOption("upper");
  await page.getByRole("button", { name: "Export clip", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "Download", exact: true }),
  ).toBeVisible();
  expect(state.exported).toEqual({
    start: 0.5,
    end: 3,
    mode: "pad",
    captions: {
      transcription_id: initialJob.id,
      revision: 2,
      position: "upper",
    },
  });
  await page
    .getByLabel("Recording", { exact: true })
    .selectOption(otherRecording.id);
  await expect(page.getByLabel("Start (seconds)")).toHaveValue("0");
  await page
    .getByLabel("Recording", { exact: true })
    .selectOption(recording.id);
  await expect(page.getByLabel("Start (seconds)")).toHaveValue("0.5");
  await expect(page.getByLabel("Include captions")).toBeChecked();
  await page.reload();
  await expect(page.getByLabel("Start (seconds)")).toHaveValue("0.5");
  await expect(page.getByLabel("End (seconds)")).toHaveValue("3");
  await expect(page.getByLabel("Caption position")).toHaveValue("upper");
  await expect(word).toHaveValue("Hello");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});

test("switching while the initial saved selection loads ignores its late response", async ({
  page,
}) => {
  await workspace(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let completed!: () => void;
  const finished = new Promise<void>((resolve) => {
    completed = resolve;
  });
  await page.route(`**/api/recordings/${recording.id}/draft`, async (route) => {
    await gate;
    await route.fulfill({ json: { ...initialDraft, start: 2, end: 5 } });
    completed();
  });
  const requested = page.waitForRequest(
    `**/api/recordings/${recording.id}/draft`,
  );
  await page.goto("/studio");
  await requested;
  await expect(page.getByLabel("Start (seconds)")).toBeDisabled();
  await page
    .getByLabel("Recording", { exact: true })
    .selectOption(otherRecording.id);
  await expect(page.getByLabel("Start (seconds)")).toBeEnabled();
  await page.getByLabel("Start (seconds)").fill("1");
  release();
  await finished;
  await expect(page.locator(".filename")).toHaveText("other.mp4");
  await expect(page.getByLabel("Start (seconds)")).toHaveValue("1");
  await expect(
    page.getByRole("button", { name: "Save selection", exact: true }),
  ).toBeEnabled();
});

test("a conflicting transcript preserves unsaved text until explicit reload", async ({
  page,
}) => {
  const state = await workspace(page);
  await page.goto("/studio");
  const word = page.getByRole("textbox", { name: "Word 1 at 1.00s" });
  await expect(word).toHaveValue("Helo");
  await word.fill("My correction");
  state.job!.revision = 2;
  state.words[0].text = "Other correction";
  await page
    .getByRole("button", { name: "Save corrections", exact: true })
    .click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "Your unsaved text is still here",
  );
  await expect(word).toHaveValue("My correction");
  await expect(page.getByLabel("Recording", { exact: true })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Save corrections", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Discard edits and reload saved words" })
    .click();
  await expect(word).toHaveValue("Other correction");
  await expect(page.getByLabel("Include captions")).toBeEnabled({
    timeout: 10_000,
  });
});

test("a conflicting selection retains current values and can reload the saved revision", async ({
  page,
}) => {
  const state = await workspace(page);
  await page.goto("/studio");
  await expect(page.getByLabel("Start (seconds)")).toBeEnabled();
  await page.getByLabel("Start (seconds)").fill("1");
  state.drafts.set(recording.id, {
    ...initialDraft,
    start: 2,
    end: 6,
    revision: 1,
  });
  await page
    .getByRole("button", { name: "Save selection", exact: true })
    .click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "Your current values are preserved",
  );
  await expect(page.getByLabel("Start (seconds)")).toHaveValue("1");
  await expect(page.getByLabel("Recording", { exact: true })).toBeDisabled();
  await page
    .getByRole("button", { name: "Discard changes and reload selection" })
    .click();
  await expect(page.getByLabel("Start (seconds)")).toHaveValue("2");
  await expect(page.getByLabel("End (seconds)")).toHaveValue("6");
  await expect(page.getByLabel("Recording", { exact: true })).toBeEnabled();
});

test("transcription supports progress, cancellation, retry and no-speech exports", async ({
  page,
}) => {
  const state = await workspace(page);
  state.job = null;
  await page.route(
    `**/api/recordings/${recording.id}/transcription`,
    (route) => {
      if (route.request().method() === "POST")
        state.job = {
          ...initialJob,
          status: "transcribing",
          progress: 0.4,
          word_count: 0,
        };
      return route.fulfill({ json: state.job });
    },
  );
  await page.route(`**/api/transcriptions/${initialJob.id}/cancel`, (route) => {
    state.job = {
      ...initialJob,
      status: "cancelled",
      progress: 0.4,
      word_count: 0,
    };
    return route.fulfill({ json: state.job });
  });
  await page.goto("/studio");
  await page
    .getByRole("button", { name: "Transcribe recording", exact: true })
    .click();
  await expect(
    page.getByRole("progressbar", { name: "Transcription progress" }),
  ).toHaveAttribute("value", "0.4");
  await page
    .getByRole("button", { name: "Cancel transcription", exact: true })
    .click();
  await expect(page.getByText(/Transcription cancelled/)).toBeVisible();
  await page
    .getByRole("button", { name: "Retry transcription", exact: true })
    .click();
  state.words = [];
  state.job = {
    ...initialJob,
    word_count: 0,
    warning: "Review the source audio before continuing.",
  };
  await expect(
    page.getByText(
      "No speech was detected. You can still export this recording without captions.",
    ),
  ).toBeVisible({ timeout: 10_000 });
  await expect(
    page.getByText("Review the source audio before continuing."),
  ).toBeVisible();
  await expect(page.getByLabel("Include captions")).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Export clip", exact: true }),
  ).toBeEnabled();
});

test("reloading the same recording after an export error restores its saved selection", async ({
  page,
}) => {
  await workspace(page);
  await page.route("**/api/recordings/*/clips", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Export service unavailable" },
    }),
  );
  await page.goto("/studio");
  await expect(
    page.getByRole("button", { name: "Export clip", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Export clip", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "Export service unavailable",
  );
  await page.getByRole("button", { name: "Reload workspace" }).click();
  await expect(page.getByLabel("Start (seconds)")).toBeEnabled();
  await expect(
    page.getByRole("textbox", { name: "Word 1 at 1.00s" }),
  ).toHaveValue("Helo");
  await expect(
    page.getByRole("button", { name: "Export clip", exact: true }),
  ).toBeEnabled();
});
