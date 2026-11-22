import { defineConfig, devices } from "@playwright/test";
const testData = process.env.CLIPPING_TEST_DATA_DIR;
if (!testData) throw new Error("Run browser checks with npm run test:e2e.");

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  workers: 1,
  webServer: [
    {
      command:
        ".venv\\Scripts\\python.exe -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port 8766",
      url: "http://127.0.0.1:8766/api/health",
      env: {
        CLIPPING_DATA_DIR: testData,
        CLIPPING_WEB_ORIGIN: "http://127.0.0.1:3100",
      },
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
    },
    {
      command:
        "node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3100",
      url: "http://127.0.0.1:3100",
      env: {
        CLIPPING_API_URL: "http://127.0.0.1:8766",
        CLIPPING_TEST_DATA_DIR: testData,
      },
      reuseExistingServer: false,
      timeout: 90_000,
    },
  ],
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1000 },
      },
    },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
