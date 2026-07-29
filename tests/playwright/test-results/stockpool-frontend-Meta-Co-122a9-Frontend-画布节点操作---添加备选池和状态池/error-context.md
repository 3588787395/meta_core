# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: stockpool-frontend.spec.js >> Meta Core Stockpool Frontend >> 画布节点操作 - 添加备选池和状态池
- Location: stockpool-frontend.spec.js:192:3

# Error details

```
Error: expect(locator).not.toHaveClass(expected) failed

Locator: locator('#addNodeDropdown')
Expected pattern: not /hidden/
Received string: "tb-dropdown-menu hidden"
Timeout: 5000ms

Call log:
  - Expect "not toHaveClass" with timeout 5000ms
  - waiting for locator('#addNodeDropdown')
    13 × locator resolved to <div id="addNodeDropdown" class="tb-dropdown-menu hidden">…</div>
       - unexpected value "tb-dropdown-menu hidden"

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
  - text: 181%
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
  - img: 1m w=1.5 1m w=1.5 w=2
  - text: A股备选池
  - img
  - text: 设 TOP10 初选池 历史 1天
  - img
  - text: 设 精选池 历史 5天 ✕ 丢弃池 DZH 股票池示例 - 所有支持的节点类型
  - button "+"
  - text: 181%
  - button "−"
  - button "⊡"
- complementary:
  - text: 属性面板
  - button "▶"
  - text: 📋 选择节点或连线 查看/编辑属性
- contentinfo: "就绪 节点: 7 | 连线: 5 | X:61 Y:-60 | 13:52:34 | 181%"
- text: ⏱ 事件监控 1
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
  150 |     await page.locator('#btnPauseResume').click();
  151 |     await btnClearEvents.click();
  152 |     await page.waitForTimeout(300);
  153 | 
  154 |     await expect(eventCount).toHaveText(/^(0|0\/\d+)$/);
  155 |   });
  156 | 
  157 |   test('TR-18.4: 导入导出测试 - JSON格式导入导出', async ({ page }) => {
  158 |     const btnImport = page.locator('#btnImport');
  159 |     const btnExport = page.locator('#btnExport');
  160 |     const importDropdown = page.locator('#importDropdown');
  161 |     const exportDropdown = page.locator('#exportDropdown');
  162 |     const importModal = page.locator('#importModal');
  163 |     const dropZone = page.locator('#dropZone');
  164 | 
  165 |     await btnImport.click();
  166 |     await expect(importDropdown).not.toHaveClass(/hidden/);
  167 | 
  168 |     const jsonImportOption = importDropdown.locator('[data-format="json"]');
  169 |     await jsonImportOption.click();
  170 |     await expect(importModal).not.toHaveClass(/hidden/);
  171 | 
  172 |     const btnCancelImport = page.locator('#btnCancelImport');
  173 |     await btnCancelImport.click();
  174 |     await expect(importModal).toHaveClass(/hidden/);
  175 | 
  176 |     await page.locator('#btnLoadDemo').click();
  177 |     await page.waitForTimeout(1000);
  178 | 
  179 |     await btnExport.click();
  180 |     await expect(exportDropdown).not.toHaveClass(/hidden/);
  181 | 
  182 |     const jsonExportOption = exportDropdown.locator('[data-format="json"]');
  183 |     const [download] = await Promise.all([
  184 |       page.waitForEvent('download'),
  185 |       jsonExportOption.click()
  186 |     ]);
  187 | 
  188 |     const path = await download.path();
  189 |     expect(path).toBeTruthy();
  190 |   });
  191 | 
  192 |   test('画布节点操作 - 添加备选池和状态池', async ({ page }) => {
  193 |     const btnAddNode = page.locator('#btnAddNode');
  194 |     const addNodeDropdown = page.locator('#addNodeDropdown');
  195 |     const statusNodes = page.locator('#statusNodes');
  196 | 
  197 |     await btnAddNode.click();
> 198 |     await expect(addNodeDropdown).not.toHaveClass(/hidden/);
      |                                       ^ Error: expect(locator).not.toHaveClass(expected) failed
  199 | 
  200 |     const addSourceOption = addNodeDropdown.locator('[data-action="addSource"]');
  201 |     await addSourceOption.click();
  202 |     await page.waitForTimeout(500);
  203 | 
  204 |     await expect(statusNodes).toContainText('节点: 1');
  205 | 
  206 |     await btnAddNode.click();
  207 |     const addStatePoolOption = addNodeDropdown.locator('[data-action="addStatePool"]');
  208 |     await addStatePoolOption.click();
  209 |     await page.waitForTimeout(500);
  210 | 
  211 |     await expect(statusNodes).toContainText('节点: 2');
  212 |   });
  213 | 
  214 |   test('属性面板编辑 - 选择节点并查看属性', async ({ page }) => {
  215 |     const panelBody = page.locator('#panelBody');
  216 |     const panelPlaceholder = page.locator('#panelPlaceholder');
  217 |     const panelTitle = page.locator('#panelTitle');
  218 | 
  219 |     await expect(panelPlaceholder).toBeVisible();
  220 | 
  221 |     await page.locator('#btnLoadDemo').click();
  222 |     await page.waitForTimeout(1000);
  223 | 
  224 |     const canvasWrapper = page.locator('#canvasWrapper');
  225 |     await canvasWrapper.click({ position: { x: 60, y: 220 } });
  226 |     await page.waitForTimeout(500);
  227 | 
  228 |     await expect(panelPlaceholder).not.toBeVisible();
  229 |     await expect(panelTitle).toHaveText('属性面板');
  230 | 
  231 |     const tdFields = panelBody.locator('.td-field');
  232 |     const fieldCount = await tdFields.count();
  233 |     expect(fieldCount).toBeGreaterThan(0);
  234 |   });
  235 | 
  236 |   test('新建股票池 - 创建空白池', async ({ page }) => {
  237 |     const btnNew = page.locator('#btnNew');
  238 |     const newPoolModal = page.locator('#newPoolModal');
  239 |     const newPoolName = page.locator('#newPoolName');
  240 |     const btnConfirmNew = page.locator('#btnConfirmNew');
  241 |     const poolNameDisplay = page.locator('#poolNameDisplay');
  242 | 
  243 |     await btnNew.click();
  244 |     await expect(newPoolModal).not.toHaveClass(/hidden/);
  245 | 
  246 |     await newPoolName.fill('测试股票池');
  247 |     await btnConfirmNew.click();
  248 |     await page.waitForTimeout(500);
  249 | 
  250 |     await expect(newPoolModal).toHaveClass(/hidden/);
  251 |     await expect(poolNameDisplay).toContainText('测试股票池');
  252 |   });
  253 | 
  254 |   test('数据源切换测试', async ({ page }) => {
  255 |     const btnDatasource = page.locator('#btnDatasource');
  256 |     const datasourceDropdown = page.locator('#datasourceDropdown');
  257 | 
  258 |     await btnDatasource.click();
  259 |     await expect(datasourceDropdown).not.toHaveClass(/hidden/);
  260 |   });
  261 | 
  262 |   test('画布缩放控制', async ({ page }) => {
  263 |     const btnFit = page.locator('#btnFit');
  264 |     const topbarZoom = page.locator('#topbarZoom');
  265 | 
  266 |     await btnFit.click();
  267 |     await page.waitForTimeout(300);
  268 |     await expect(topbarZoom).toBeVisible();
  269 |   });
  270 | 
  271 |   test('撤销重做功能', async ({ page }) => {
  272 |     const btnUndo = page.locator('#btnUndo');
  273 |     const btnRedo = page.locator('#btnRedo');
  274 | 
  275 |     await expect(btnUndo).toBeDisabled();
  276 |     await expect(btnRedo).toBeDisabled();
  277 | 
  278 |     await page.locator('#btnLoadDemo').click();
  279 |     await page.waitForTimeout(1000);
  280 | 
  281 |     await page.locator('#btnAddNode').click();
  282 |     const addSourceOption = page.locator('#addNodeDropdown [data-action="addSource"]');
  283 |     await addSourceOption.click();
  284 |     await page.waitForTimeout(500);
  285 | 
  286 |     await expect(btnUndo).not.toBeDisabled();
  287 |     await btnUndo.click();
  288 |     await page.waitForTimeout(300);
  289 | 
  290 |     await expect(btnRedo).not.toBeDisabled();
  291 |   });
  292 | });
```