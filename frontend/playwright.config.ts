import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8011',
    trace: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // The backend serves frontend/dist, so run `npm run build` first.
    command: 'cd ../backend && .venv/bin/python tests/e2e_server.py 8011',
    url: 'http://127.0.0.1:8011/api/health',
    reuseExistingServer: false,
    // The server seeds a small fake experiment for about 20s before it listens.
    timeout: 120_000,
  },
});
