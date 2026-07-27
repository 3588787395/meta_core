const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const sleep = ms => new Promise(r => setTimeout(r, ms));
const outDir = '/workspace/tmp-frontend-qa';
if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const logs = [];
  const errs = [];
  page.on('console', m => logs.push({ level: m.type(), text: m.text() }));
  page.on('pageerror', e => errs.push(e.message));

  // Page identity
  await page.goto('http://127.0.0.1:8000/index.html', { waitUntil: 'networkidle', timeout: 30000 });
  await sleep(2000);
  const url = page.url();
  const title = await page.title();

  // Not blank + framework overlay check
  const snapshot = await page.content();
  const bodyText = await page.locator('body').innerText().catch(() => '');
  const hasAppContent = bodyText.trim().length > 10;
  const frameworkOverlay = /(webpack|vite|next\.js|react|framework)/i.test(snapshot) &&
                           /(error|overlay|hmr|failed)/i.test(snapshot);

  // Screenshot: initial
  await page.screenshot({ path: path.join(outDir, '01_initial.png'), fullPage: false });

  // Interaction: right-click canvas -> context menu
  await page.mouse.click(800, 450, { button: 'right' });
  await sleep(500);
  const menuVisible = await page.locator('#contextMenu').isVisible().catch(() => false);
  const menuText = bodyText;
  await page.screenshot({ path: path.join(outDir, '02_context_menu.png'), fullPage: false });

  // Interaction: click 开始
  const startBefore = await page.locator('text=开始').first().isVisible().catch(() => false);
  if (startBefore) {
    await page.locator('text=开始').first().click();
    await sleep(800);
  }
  const startAfter = await page.locator('text=开始').first().isVisible().catch(() => false);
  await page.screenshot({ path: path.join(outDir, '03_start_clicked.png'), fullPage: false });

  // Console health: filter relevant errors
  const relevantErrors = logs.filter(l => l.level === 'error' || l.level === 'warning')
    .filter(l => !/akshare|requests|HQChartPy2|watchdog|未安装|不可用|警告/.test(l.text));

  await browser.close();

  const result = {
    url,
    title,
    hasAppContent,
    frameworkOverlay,
    menuVisible,
    menuTextPreview: menuText.slice(0, 500),
    startBefore,
    startAfter,
    relevantErrors,
    pageErrors: errs,
    passed: hasAppContent && !frameworkOverlay && menuVisible && startBefore && relevantErrors.length === 0 && errs.length === 0
  };

  fs.writeFileSync(path.join(outDir, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ passed: result.passed, url, title, relevantErrors: relevantErrors.length, pageErrors: errs.length }, null, 2));
})();
