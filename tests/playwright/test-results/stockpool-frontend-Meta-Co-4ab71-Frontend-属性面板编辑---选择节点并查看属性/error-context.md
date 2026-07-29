# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: stockpool-frontend.spec.js >> Meta Core Stockpool Frontend >> 属性面板编辑 - 选择节点并查看属性
- Location: stockpool-frontend.spec.js:214:3

# Error details

```
Error: expect(locator).toHaveText(expected) failed

Locator:  locator('#panelTitle')
Expected: "属性面板"
Received: "股票池属性"
Timeout:  5000ms

Call log:
  - Expect "toHaveText" with timeout 5000ms
  - waiting for locator('#panelTitle')
    13 × locator resolved to <span id="panelTitle" class="panel-title">股票池属性</span>
       - unexpected value "股票池属性"

```

```yaml
- text: 股票池属性
```

# Test source

```ts
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
  198 |     await expect(addNodeDropdown).not.toHaveClass(/hidden/);
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
> 229 |     await expect(panelTitle).toHaveText('属性面板');
      |                              ^ Error: expect(locator).toHaveText(expected) failed
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