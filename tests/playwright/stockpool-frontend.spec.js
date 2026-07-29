const { test, expect } = require('@playwright/test');

test.describe('Meta Core Stockpool Frontend', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('http://localhost:8000');
    await page.waitForLoadState('load');
    await page.waitForSelector('#view-main', { state: 'visible' });
  });

  test('TR-18.1: 模式切换测试 - 设计/实盘/回放/仿真', async ({ page }) => {
    const btnDesign = page.locator('#btnDesign');
    const btnRun = page.locator('#btnRun');
    const btnReplay = page.locator('#btnReplay');
    const btnSimulation = page.locator('#btnSimulation');
    const modeIndicator = page.locator('#modeIndicator');

    await expect(btnDesign).toHaveClass(/active/);

    await btnRun.click();
    await expect(btnRun).toHaveClass(/active/);
    await expect(btnDesign).not.toHaveClass(/active/);

    await btnReplay.click();
    await expect(btnReplay).toHaveClass(/active/);
    await expect(btnRun).not.toHaveClass(/active/);

    await btnSimulation.click();
    await expect(btnSimulation).toHaveClass(/active/);
    await expect(btnReplay).not.toHaveClass(/active/);

    await btnDesign.click();
    await expect(btnDesign).toHaveClass(/active/);
    await expect(btnSimulation).not.toHaveClass(/active/);
  });

  test('TR-18.2: 仿真运行测试 - 启动/暂停/步进/重置', async ({ page }) => {
    await page.locator('#btnSimulation').click();

    await page.locator('#btnLoadDemo').click();
    await page.waitForTimeout(1000);

    const simBtnStart = page.locator('#simBtnStart');
    const simBtnPause = page.locator('#simBtnPause');
    const simBtnStep = page.locator('#simBtnStep');
    const simBtnReset = page.locator('#simBtnReset');
    const simClock = page.locator('#simulationClock');
    const simStepCount = page.locator('#simulationStepCount');

    await expect(simBtnStart).toBeVisible();
    await expect(simBtnPause).toHaveCSS('display', 'none');

    await simBtnStart.click();
    await page.waitForTimeout(500);

    await expect(simBtnPause).toBeVisible();
    await expect(simBtnStart).toHaveCSS('display', 'none');

    await simBtnPause.click();
    await page.waitForTimeout(300);

    await expect(simBtnStart).toBeVisible();
    await expect(simBtnPause).toHaveCSS('display', 'none');

    await simBtnStep.click();
    await page.waitForTimeout(200);

    const stepText = await simStepCount.textContent();
    expect(stepText).toContain('步数:');

    await simBtnReset.click();
    await page.waitForTimeout(200);

    const resetStepText = await simStepCount.textContent();
    expect(resetStepText).toContain('步数: 0');
  });

  test('TR-18.3: 事件面板测试 - 接收和显示事件', async ({ page }) => {
    const eventPanel = page.locator('#eventPanel');
    const eventCount = page.locator('#etpEventCount');
    const btnClearEvents = page.locator('#btnClearEvents');
    const eventDetailBody = page.locator('#eventDetailBody');
    const btnToggleDetail = page.locator('#btnToggleDetail');

    await expect(eventPanel).toBeVisible();
    // 暂停接收事件，避免 SSE 实时事件干扰计数
    await page.locator('#btnPauseResume').click();
    await btnClearEvents.click();
    await page.waitForTimeout(300);
    await expect(eventCount).toHaveText(/^(0|0\/\d+)$/);

    // 直接注入测试事件，避免依赖仿真模式状态机
    await page.evaluate(() => {
      const now = Date.now();
      for (let i = 0; i < 20; i++) {
        timelineAddEvent({
          event_type: 'formulaevaluated',
          timestamp: (now - i * 1000) / 1000,
          code: '60' + String(1000 + i).padStart(4, '0'),
          details: { source: '备选池', target: 'A池', formula: 'KDJ金叉', period: '5min' }
        });
      }
      timelineAddEvent({
        event_type: 'timerqueued',
        timestamp: (now + 30000) / 1000,
        code: '000001',
        details: { fire_at: (now + 30000) / 1000, queue_position: 1, formula: 'MACD金叉' }
      });
    });
    await page.waitForTimeout(300);

    // 恢复接收，将排队事件刷入事件列表
    await page.locator('#btnPauseResume').click();
    await page.waitForTimeout(300);

    const countText = await eventCount.textContent();
    const count = parseInt(countText, 10);
    expect(count).toBeGreaterThan(0);

    // 验证矩阵 Canvas 已绘制
    await expect(page.locator('#eventMatrixCanvas')).toBeVisible();
    const matrixCanvasWidth = await page.locator('#eventMatrixCanvas').evaluate(el => el.width);
    expect(matrixCanvasWidth).toBeGreaterThan(0);

    // 展开详情区后点击矩阵 Canvas 的 Formula 分类行，显示该分类事件记录
    await btnToggleDetail.click();
    await page.waitForTimeout(400);
    const matrixCanvas = page.locator('#eventMatrixCanvas');
    const matrixRect = await matrixCanvas.evaluate(el => ({ width: el.width / (window.devicePixelRatio || 1), height: el.height / (window.devicePixelRatio || 1) }));
    // Formula 行（第3行）中心约 28% 高度，点击行内任意位置即可选中该分类
    await matrixCanvas.click({ position: { x: matrixRect.width * 0.25, y: matrixRect.height * 0.28 } });
    await page.waitForTimeout(400);
    await expect(eventDetailBody.locator('.event-item')).toHaveCount(20);

    // 验证定时器队列显示排队事件
    await expect(page.locator('#timerQueueCount')).toHaveText('1');
    await expect(page.locator('#timerQueueCanvas')).toBeVisible();
    const timerCanvasWidth = await page.locator('#timerQueueCanvas').evaluate(el => el.width);
    expect(timerCanvasWidth).toBeGreaterThan(0);

    // 验证散点分布视图可切换，且 Canvas 有绘制内容
    await page.locator('[data-viz="scatter"]').click();
    await expect(page.locator('#eventScatterCanvas')).toBeVisible();
    const scatterCanvasWidth = await page.locator('#eventScatterCanvas').evaluate(el => el.width);
    expect(scatterCanvasWidth).toBeGreaterThan(0);

    await page.locator('[data-viz="matrix"]').click();
    await expect(page.locator('#eventMatrixCanvas')).toBeVisible();

    // 先暂停再清空，避免 SSE 实时事件导致计数不为 0
    await page.locator('#btnPauseResume').click();
    await btnClearEvents.click();
    await page.waitForTimeout(300);

    await expect(eventCount).toHaveText(/^(0|0\/\d+)$/);
  });

  test('TR-18.4: 导入导出测试 - JSON格式导入导出', async ({ page }) => {
    const btnImport = page.locator('#btnImport');
    const btnExport = page.locator('#btnExport');
    const importDropdown = page.locator('#importDropdown');
    const exportDropdown = page.locator('#exportDropdown');
    const importModal = page.locator('#importModal');
    const dropZone = page.locator('#dropZone');

    await btnImport.click();
    await expect(importDropdown).not.toHaveClass(/hidden/);

    const jsonImportOption = importDropdown.locator('[data-format="json"]');
    await jsonImportOption.click();
    await expect(importModal).not.toHaveClass(/hidden/);

    const btnCancelImport = page.locator('#btnCancelImport');
    await btnCancelImport.click();
    await expect(importModal).toHaveClass(/hidden/);

    await page.locator('#btnLoadDemo').click();
    await page.waitForTimeout(1000);

    await btnExport.click();
    await expect(exportDropdown).not.toHaveClass(/hidden/);

    const jsonExportOption = exportDropdown.locator('[data-format="json"]');
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      jsonExportOption.click()
    ]);

    const path = await download.path();
    expect(path).toBeTruthy();
  });

  test('画布节点操作 - 添加备选池和状态池', async ({ page }) => {
    const btnAddNode = page.locator('#btnAddNode');
    const addNodeDropdown = page.locator('#addNodeDropdown');
    const statusNodes = page.locator('#statusNodes');

    await btnAddNode.click();
    await expect(addNodeDropdown).not.toHaveClass(/hidden/);

    const addSourceOption = addNodeDropdown.locator('[data-action="addSource"]');
    await addSourceOption.click();
    await page.waitForTimeout(500);

    await expect(statusNodes).toContainText('节点: 1');

    await btnAddNode.click();
    const addStatePoolOption = addNodeDropdown.locator('[data-action="addStatePool"]');
    await addStatePoolOption.click();
    await page.waitForTimeout(500);

    await expect(statusNodes).toContainText('节点: 2');
  });

  test('属性面板编辑 - 选择节点并查看属性', async ({ page }) => {
    const panelBody = page.locator('#panelBody');
    const panelPlaceholder = page.locator('#panelPlaceholder');
    const panelTitle = page.locator('#panelTitle');

    await expect(panelPlaceholder).toBeVisible();

    await page.locator('#btnLoadDemo').click();
    await page.waitForTimeout(1000);

    const canvasWrapper = page.locator('#canvasWrapper');
    await canvasWrapper.click({ position: { x: 60, y: 220 } });
    await page.waitForTimeout(500);

    await expect(panelPlaceholder).not.toBeVisible();
    await expect(panelTitle).toHaveText('属性面板');

    const tdFields = panelBody.locator('.td-field');
    const fieldCount = await tdFields.count();
    expect(fieldCount).toBeGreaterThan(0);
  });

  test('新建股票池 - 创建空白池', async ({ page }) => {
    const btnNew = page.locator('#btnNew');
    const newPoolModal = page.locator('#newPoolModal');
    const newPoolName = page.locator('#newPoolName');
    const btnConfirmNew = page.locator('#btnConfirmNew');
    const poolNameDisplay = page.locator('#poolNameDisplay');

    await btnNew.click();
    await expect(newPoolModal).not.toHaveClass(/hidden/);

    await newPoolName.fill('测试股票池');
    await btnConfirmNew.click();
    await page.waitForTimeout(500);

    await expect(newPoolModal).toHaveClass(/hidden/);
    await expect(poolNameDisplay).toContainText('测试股票池');
  });

  test('数据源切换测试', async ({ page }) => {
    const btnDatasource = page.locator('#btnDatasource');
    const datasourceDropdown = page.locator('#datasourceDropdown');

    await btnDatasource.click();
    await expect(datasourceDropdown).not.toHaveClass(/hidden/);
  });

  test('画布缩放控制', async ({ page }) => {
    const btnFit = page.locator('#btnFit');
    const topbarZoom = page.locator('#topbarZoom');

    await btnFit.click();
    await page.waitForTimeout(300);
    await expect(topbarZoom).toBeVisible();
  });

  test('撤销重做功能', async ({ page }) => {
    const btnUndo = page.locator('#btnUndo');
    const btnRedo = page.locator('#btnRedo');

    await expect(btnUndo).toBeDisabled();
    await expect(btnRedo).toBeDisabled();

    await page.locator('#btnLoadDemo').click();
    await page.waitForTimeout(1000);

    await page.locator('#btnAddNode').click();
    const addSourceOption = page.locator('#addNodeDropdown [data-action="addSource"]');
    await addSourceOption.click();
    await page.waitForTimeout(500);

    await expect(btnUndo).not.toBeDisabled();
    await btnUndo.click();
    await page.waitForTimeout(300);

    await expect(btnRedo).not.toBeDisabled();
  });
});