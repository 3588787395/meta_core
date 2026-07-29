# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: stockpool-frontend.spec.js >> Meta Core Stockpool Frontend >> TR-18.2: 仿真运行测试 - 启动/暂停/步进/重置
- Location: stockpool-frontend.spec.js:36:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator:  locator('#simBtnStart')
Expected: visible
Received: hidden
Timeout:  5000ms

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for locator('#simBtnStart')
    12 × locator resolved to <button title="启动自动步进" id="simBtnStart" class="simulation-btn sim-btn-start">▶ 启动</button>
       - unexpected value "hidden"

```

```yaml
- navigation:
  - link "主页":
    - /url: "#/"
  - link "配置中心":
    - /url: "#/config"
  - link "公式管理":
    - /url: "#/formula"
- banner:
  - text: ◆ MetaCore
  - button "📡 -- ▼"
  - text: 仿真测试池-100只股票
  - button "🆕 新建"
  - button "📊 加载示例池"
  - button "➕ 添加 ▼"
  - button "📥 导入 ▼"
  - button "📤 导出 ▼"
  - button "💾 保存"
  - button "↩ 撤销" [disabled]
  - button "↪ 重做" [disabled]
  - button "⊡ 适应"
  - button "🔢 顺序"
  - button "🔗 连线"
  - button "📐 线形 ▼"
  - button "▶ 开始"
  - button "⏸ 暂停" [disabled]
  - button "⏹ 停止" [disabled]
  - button "🎨 设计模式"
  - button "▶ 实盘"
  - button "⏪ 回放"
  - button "🔬 仿真"
  - button "◀ 列表"
  - button "⚙ 规则"
  - button "⚙ 综合设置"
  - link "📐 公式":
    - /url: "#/formula"
  - link "⚙ 配置中心":
    - /url: "#/config"
  - text: 200%
- complementary:
  - text: 股票池列表
  - button "◀"
  - button "通达信"
  - button "大智慧"
  - button "实例"
  - button "已保存"
  - textbox "搜索..."
  - list:
    - listitem:
      - text: 📄 2019游资榜-百里光耀.xml XML 156.6KB
      - img "2019游资榜-百里光耀.xml"
    - listitem:
      - text: 📄 2_资金分析系统.xml XML 77.5KB
      - img "2_资金分析系统.xml"
    - listitem: 📄 api_test_create_044403cb.xml XML 139B
    - listitem: 📄 api_test_create_2afb30cb.xml XML 139B
    - listitem: 📄 api_test_create_4fcd7e4d.xml XML 139B
    - listitem: 📄 api_test_create_72179ad2.xml XML 139B
    - listitem: 📄 api_test_create_73f205d3.xml XML 139B
    - listitem: 📄 api_test_create_910892dc.xml XML 139B
    - listitem: 📄 api_test_create_9281b778.xml XML 139B
    - listitem: 📄 api_test_create_a767944f.xml XML 139B
    - listitem: 📄 api_test_update.xml XML 1016B
    - listitem: 📄 dup_test_pool.xml XML 139B
    - listitem: 📄 【短线T+1】10W池.xml XML 163.2KB
    - listitem: 📄 七种武器.xml XML 113.5KB
    - listitem:
      - text: 📄 初选池分类.xml XML 1175.7KB
      - img "初选池分类.xml"
    - listitem: 📄 圣唐资本-热点分析.xml XML 141.1KB
    - listitem:
      - text: 📄 大路终结池.xml XML 380.8KB
      - img "大路终结池.xml"
    - listitem:
      - text: 📄 天使之翼.xml XML 118.6KB
      - img "天使之翼.xml"
    - listitem:
      - text: 📄 天晴一号.xml XML 87.9KB
      - img "天晴一号.xml"
    - listitem:
      - text: 📄 天晴二号.xml XML 85.5KB
      - img "天晴二号.xml"
    - listitem:
      - text: 📄 强势金叉.xml XML 380.2KB
      - img "强势金叉.xml"
    - listitem:
      - text: 📄 抄底股票池.xml XML 537.3KB
      - img "抄底股票池.xml"
    - listitem: 📄 操盘铁律战略池-自动交易.xml XML 11.6KB
    - listitem:
      - text: 📄 每日题材概念热点股.xml XML 215.0KB
      - img "每日题材概念热点股.xml"
    - listitem:
      - text: 📄 涨停专用.xml XML 135.0KB
      - img "涨停专用.xml"
    - listitem: 📄 源自神灯.xml XML 34.9KB
    - listitem:
      - text: 📄 牛魔王pro.xml XML 166.2KB
      - img "牛魔王pro.xml"
    - listitem: 📄 盘中异动预警池.xml XML 4.7KB
    - listitem:
      - text: 📄 盘后.xml XML 165.0KB
      - img "盘后.xml"
    - listitem:
      - text: 📄 看盘快照.xml XML 117.7KB
      - img "看盘快照.xml"
    - listitem:
      - text: 📄 短线量化选股器.xml XML 91.5KB
      - img "短线量化选股器.xml"
    - listitem:
      - text: 📄 神奇的KDJ测试池.xml XML 117.0KB
      - img "神奇的KDJ测试池.xml"
    - listitem: 📄 竞价·三步倒-0301.xml XML 86.7KB
    - listitem: 📄 竞价·三步倒.xml XML 120.8KB
    - listitem: 📄 精确伏击-盘中池A.xml XML 10.0KB
    - listitem: 📄 精确出击-友情版.xml XML 5.3KB
    - listitem: 📄 精确出击-源码版.xml XML 7.2KB
    - listitem:
      - text: 📄 缠论股票池.xml XML 188.4KB
      - img "缠论股票池.xml"
    - listitem:
      - text: 📄 股票池.xml XML 190.0KB
      - img "股票池.xml"
    - listitem:
      - text: 📄 资金活跃度.xml XML 97.5KB
      - img "资金活跃度.xml"
    - listitem:
      - text: 📄 连板池.xml XML 131.9KB
      - img "连板池.xml"
    - listitem:
      - text: 📄 逍遥复盘竟价池.xml XML 123.9KB
      - img "逍遥复盘竟价池.xml"
    - listitem:
      - text: 📄 通达信--逍遥的春天-短线.xml XML 122.1KB
      - img "通达信--逍遥的春天-短线.xml"
    - listitem: 📄 醉仙加强池.xml XML 172.1KB
    - listitem:
      - text: 📄 黑牛战法精华版.xml XML 1458.3KB
      - img "黑牛战法精华版.xml"
    - listitem:
      - text: 📄 黑马一号池.xml XML 379.5KB
      - img "黑马一号池.xml"
    - listitem:
      - text: 📄 黑马二号池.xml XML 185.8KB
      - img "黑马二号池.xml"
    - listitem:
      - text: 📄 黑马全息股池.xml XML 87.1KB
      - img "黑马全息股池.xml"
    - listitem: 📄 黑马启动.xml XML 134.1KB
- main:
  - img: "1m w=1.5 10s w=1.5 5s #30 5s #31 w=1.5"
  - text: "100 备选池 ƒKDJ金叉条件 指标: KDJ 15m/金叉 ƒMACD金叉条件 指标: MACD 15m/金叉 ∩交集条件 指标: 交集 A池-5分钟KDJ金叉 B池-1分钟MACD金叉 C池-交集交易"
  - button "+"
  - text: 200%
  - button "−"
  - button "⊡"
- complementary:
  - text: 属性面板
  - button "▶"
  - text: 📋 选择节点或连线 查看/编辑属性
- contentinfo: "就绪 节点: 7 | 连线: 7 | X:57 Y:-22 | 13:52:13 | 200%"
- text: ⏱ 事件监控 2
- button "⏸"
- button "🗑"
- button "−"
- button "✕"
- button "📑 矩阵"
- button "⏱ 散点"
- text: ⏰ 定时器队列 0 定时器队列为空 事件记录
- button "▲"
- button "✕"
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
  20  |     await expect(btnRun).toHaveClass(/active/);
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
> 49  |     await expect(simBtnStart).toBeVisible();
      |                               ^ Error: expect(locator).toBeVisible() failed
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
  121 |     const matrixCanvasWidth = await page.locator('#eventMatrixCanvas').evaluate(el => el.width);
  122 |     expect(matrixCanvasWidth).toBeGreaterThan(0);
  123 | 
  124 |     // 展开详情区后点击矩阵 Canvas 的 Formula 分类行，显示该分类事件记录
  125 |     await btnToggleDetail.click();
  126 |     await page.waitForTimeout(400);
  127 |     const matrixCanvas = page.locator('#eventMatrixCanvas');
  128 |     const matrixRect = await matrixCanvas.evaluate(el => ({ width: el.width / (window.devicePixelRatio || 1), height: el.height / (window.devicePixelRatio || 1) }));
  129 |     // Formula 行（第3行）中心约 28% 高度，点击行内任意位置即可选中该分类
  130 |     await matrixCanvas.click({ position: { x: matrixRect.width * 0.25, y: matrixRect.height * 0.28 } });
  131 |     await page.waitForTimeout(400);
  132 |     await expect(eventDetailBody.locator('.event-item')).toHaveCount(20);
  133 | 
  134 |     // 验证定时器队列显示排队事件
  135 |     await expect(page.locator('#timerQueueCount')).toHaveText('1');
  136 |     await expect(page.locator('#timerQueueCanvas')).toBeVisible();
  137 |     const timerCanvasWidth = await page.locator('#timerQueueCanvas').evaluate(el => el.width);
  138 |     expect(timerCanvasWidth).toBeGreaterThan(0);
  139 | 
  140 |     // 验证散点分布视图可切换，且 Canvas 有绘制内容
  141 |     await page.locator('[data-viz="scatter"]').click();
  142 |     await expect(page.locator('#eventScatterCanvas')).toBeVisible();
  143 |     const scatterCanvasWidth = await page.locator('#eventScatterCanvas').evaluate(el => el.width);
  144 |     expect(scatterCanvasWidth).toBeGreaterThan(0);
  145 | 
  146 |     await page.locator('[data-viz="matrix"]').click();
  147 |     await expect(page.locator('#eventMatrixCanvas')).toBeVisible();
  148 | 
  149 |     // 先暂停再清空，避免 SSE 实时事件导致计数不为 0
```