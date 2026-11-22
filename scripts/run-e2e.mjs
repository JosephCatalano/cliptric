import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

// Own the directory and clean it only after Playwright stops both servers.
const directory = await mkdtemp(path.join(os.tmpdir(), "clipping-e2e-"));
let code = 1;
try {
  const child = spawn(
    process.execPath,
    [
      path.resolve("node_modules/@playwright/test/cli.js"),
      "test",
      ...process.argv.slice(2),
    ],
    {
      stdio: "inherit",
      env: { ...process.env, CLIPPING_TEST_DATA_DIR: directory },
    },
  );
  const interrupt = () => child.kill("SIGINT");
  process.on("SIGINT", interrupt);
  try {
    code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (status) => resolve(status ?? 1));
    });
  } finally {
    process.off("SIGINT", interrupt);
  }
} finally {
  const resolved = path.resolve(directory);
  if (
    path.dirname(resolved) !== path.resolve(os.tmpdir()) ||
    !/^clipping-e2e-[a-zA-Z0-9]+$/.test(path.basename(resolved))
  ) {
    throw new Error("Refusing to remove an unexpected test data directory.");
  }
  await rm(resolved, {
    recursive: true,
    force: true,
    maxRetries: 10,
    retryDelay: 300,
  });
}
process.exitCode = code;
