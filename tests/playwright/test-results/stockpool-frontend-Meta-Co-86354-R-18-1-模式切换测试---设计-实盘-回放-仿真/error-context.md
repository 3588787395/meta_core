# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: stockpool-frontend.spec.js >> Meta Core Stockpool Frontend >> TR-18.1: 模式切换测试 - 设计/实盘/回放/仿真
- Location: stockpool-frontend.spec.js:10:3

# Error details

```
Error: expect(locator).toHaveClass(expected) failed

Locator: locator('#btnRun')
Expected pattern: /active/
Received string:  "tb-btn run-mode-btn"
Timeout: 5000ms

Call log:
  - Expect "toHaveClass" with timeout 5000ms
  - waiting for locator('#btnRun')
    14 × locator resolved to <button id="btnRun" title="实盘模式" class="tb-btn run-mode-btn">▶ 实盘</button>
       - unexpected value "tb-btn run-mode-btn"

```

```yaml
- button "▶ 实盘"
```

# Test source

```ts
  1   | const { test, expect } = require('@playwright/test');
  2   | 
  3   | test.describe('Meta Core Stockpool Frontend', () => {
  4   |   test.beforeEach(async ({ page }) => {
  5   |     await page.goto('http://localhost:8000');
  6   |     await page.waitForLoadState('load');
  7   |     await page.waitForSelector('#view-main', { state: 'visible' });
  8   |   });
  9   | 
  10  |   test('TR-18.1: 模式切换测试 - 设计/实盘/回放/仿真', async ({ page }) => {
  11  |     const btnDesign = page.locator('#btnDesign');
  12  |     const btnRun = page.locator('#btnRun');
  13  |     const btnReplay = page.locator('#btnReplay');
  14  |     const btnSimulation = page.locator('#btnSimulation');
  15  |     const modeIndicator = page.locator('#modeIndicator');
  16  | 
  17  |     await expect(btnDesign).toHaveClass(/active/);
  18  | 
  19  |     await btnRun.click();
> 20  |     await expect(btnRun).toHaveClass(/active/);
      |                          ^ Error: expect(locator).toHaveClass(expected) failed
  21  |     await expect(btnDesign).not.toHaveClass(/active/);
  22  | 
  23  |     await btnReplay.click();
  24  |     await expect(btnReplay).toHaveClass(/active/);
  25  |     await expect(btnRun).not.toHaveClass(/active/);
  26  | 
  27  |     await btnSimulation.click();
  28  |     await expect(btnSimulation).toHaveClass(/active/);
  29  |     await expect(btnReplay).not.toHaveClass(/active/);
  30  | 
  31  |     await btnDesign.click();
  32  |     await expect(btnDesign).toHaveClass(/active/);
  33  |     await expect(btnSimulation).not.toHaveClass(/active/);
  34  |   });
  35  | 
  36  |   test('TR-18.2: 仿真运行测试 - 启动/暂停/步进/重置', async ({ page }) => {
  37  |     await page.locator('#btnSimulation').click();
  38  | 
  39  |     await page.locator('#btnLoadDemo').click();
  40  |     await page.waitForTimeout(1000);
  41  | 
  42  |     const simBtnStart = page.locator('#simBtnStart');
  43  |     const simBtnPause = page.locator('#simBtnPause');
  44  |     const simBtnStep = page.locator('#simBtnStep');
  45  |     const simBtnReset = page.locator('#simBtnReset');
  46  |     const simClock = page.locator('#simulationClock');
  47  |     const simStepCount = page.locator('#simulationStepCount');
  48  | 
  49  |     await expect(simBtnStart).toBeVisible();
  50  |     await expect(simBtnPause).toHaveCSS('display', 'none');
  51  | 
  52  |     await simBtnStart.click();
  53  |     await page.waitForTimeout(500);
  54  | 
  55  |     await expect(simBtnPause).toBeVisible();
  56  |     await expect(simBtnStart).toHaveCSS('display', 'none');
  57  | 
  58  |     await simBtnPause.click();
  59  |     await page.waitForTimeout(300);
  60  | 
  61  |     await expect(simBtnStart).toBeVisible();
  62  |     await expect(simBtnPause).toHaveCSS('display', 'none');
  63  | 
  64  |     await simBtnStep.click();
  65  |     await page.waitForTimeout(200);
  66  | 
  67  |     const stepText = await simStepCount.textContent();
  68  |     expect(stepText).toContain('步数:');
  69  | 
  70  |     await simBtnReset.click();
  71  |     await page.waitForTimeout(200);
  72  | 
  73  |     const resetStepText = await simStepCount.textContent();
  74  |     expect(resetStepText).toContain('步数: 0');
  75  |   });
  76  | 
  77  |   test('TR-18.3: 事件面板测试 - 接收和显示事件', async ({ page }) => {
  78  |     const eventPanel = page.locator('#eventPanel');
  79  |     const eventCount = page.locator('#etpEventCount');
  80  |     const btnClearEvents = page.locator('#btnClearEvents');
  81  |     const eventDetailBody = page.locator('#eventDetailBody');
  82  |     const btnToggleDetail = page.locator('#btnToggleDetail');
  83  | 
  84  |     await expect(eventPanel).toBeVisible();
  85  |     // 暂停接收事件，避免 SSE 实时事件干扰计数
  86  |     await page.locator('#btnPauseResume').click();
  87  |     await btnClearEvents.click();
  88  |     await page.waitForTimeout(300);
  89  |     await expect(eventCount).toHaveText(/^(0|0\/\d+)$/);
  90  | 
  91  |     // 直接注入测试事件，避免依赖仿真模式状态机
  92  |     await page.evaluate(() => {
  93  |       const now = Date.now();
  94  |       for (let i = 0; i < 20; i++) {
  95  |         timelineAddEvent({
  96  |           event_type: 'formulaevaluated',
  97  |           timestamp: (now - i * 1000) / 1000,
  98  |           code: '60' + String(1000 + i).padStart(4, '0'),
  99  |           details: { source: '备选池', target: 'A池', formula: 'KDJ金叉', period: '5min' }
  100 |         });
  101 |       }
  102 |       timelineAddEvent({
  103 |         event_type: 'timerqueued',
  104 |         timestamp: (now + 30000) / 1000,
  105 |         code: '000001',
  106 |         details: { fire_at: (now + 30000) / 1000, queue_position: 1, formula: 'MACD金叉' }
  107 |       });
  108 |     });
  109 |     await page.waitForTimeout(300);
  110 | 
  111 |     // 恢复接收，将排队事件刷入事件列表
  112 |     await page.locator('#btnPauseResume').click();
  113 |     await page.waitForTimeout(300);
  114 | 
  115 |     const countText = await eventCount.textContent();
  116 |     const count = parseInt(countText, 10);
  117 |     expect(count).toBeGreaterThan(0);
  118 | 
  119 |     // 验证矩阵 Canvas 已绘制
  120 |     await expect(page.locator('#eventMatrixCanvas')).toBeVisible();
```