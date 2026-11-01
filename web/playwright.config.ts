import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  timeout: 60000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    headless: true,
    launchOptions: {
      ...(process.env.BOLTZFIX_TEST_BROWSER
        ? { executablePath: process.env.BOLTZFIX_TEST_BROWSER }
        : {}),
      args: [
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
      ],
    },
    viewport: { width: 1440, height: 1100 },
  },
  reporter: "list",
});
