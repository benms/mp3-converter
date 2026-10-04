import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  workers: 2,
  timeout: 30000,
  expect: { timeout: 10000 },
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: [
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5173 --strictPort',
      url: 'http://127.0.0.1:5173',
      env: { VITE_SOUNDDROP_API_URL: 'https://api.sounddrop.test' },
      reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5174 --strictPort',
      url: 'http://127.0.0.1:5174',
      env: { VITE_SOUNDDROP_API_URL: '' },
      reuseExistingServer: false,
    },
  ],
});
