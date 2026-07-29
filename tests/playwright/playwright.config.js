module.exports = {
  testDir: '.',
  timeout: 60000,
  use: {
    baseURL: 'http://localhost:8000',
    browserName: 'chromium',
    viewport: { width: 1920, height: 1080 },
    headless: true,
    ignoreHTTPSErrors: true,
  },
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: '../../playwright-report' }]
  ],
};