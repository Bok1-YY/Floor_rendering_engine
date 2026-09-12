import { defineConfig } from '@playwright/test';

export default defineConfig({
  timeout: 15000,
  testDir: './tests/browser',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:8799', headless: true, screenshot: 'only-on-failure' },
  webServer: {
    command: process.platform === 'win32' ? '..\\.venv\\Scripts\\python.exe ..\\tests\\serve_static.py' : '../.venv/bin/python ../tests/serve_static.py',
    url: 'http://127.0.0.1:8799/design/',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
