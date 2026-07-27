const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const sleep = ms => new Promise(r => setTimeout(r, ms));
const outDir = '/workspace/tmp-frontend-qa';
if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

async function safeClick(locator, timeout = 3000, force = false) {
  if (await locator.isVisible().catch(() => false)) {
    await locator.click({ timeout, force });
    return true;
  }
  return false;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const logs = [];
  const errs = [];
  const network = [];
  page.on('console', m => logs.push({ level: m.type(), text: m.text() }));
  page.on('pageerror', e => errs.push(e.message));
  page.on('response', async r => {
    if (r.url().includes('/api/v1/table/panel') || r.url().includes('/api/json/export') || r.url().includes('/api/json/import')) {
      try {
        const status = r.status();
        const text = await r.text().catch(() => '');
        network.push({ url: r.url(), status, preview: text.slice(0, 300) });
      } catch (e) {}
    }
  });

  const results = {
    url: '',
    title: '',
    hasAppContent: false,
    frameworkOverlay: false,
    loadPoolFromSidebar: false,
    nodeSelected: false,
    nodeSelectedDebug: '',
    nodeContextMenu: false,
    runStart: false,
    runPause: false,
    runStop: false,
    modeSwitchRun: false,
    modeSwitchReplay: false,
    modeSwitchSimulation: false,
    modeSwitchDesign: false,
    eventPanelToggle: false,
    edgeSelected: false,
    edgeSelectedFieldCount: 0,
    importJsonOk: false,
    exportJsonOk: false,
    mobileOk: false,
    relevantErrors: [],
    pageErrors: [],
    passed: false
  };

  try {
    await page.goto('http://127.0.0.1:8000/index.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await sleep(3500);
    results.url = page.url();
    results.title = await page.title();

    const snapshot = await page.content();
    const bodyText = await page.locator('body').innerText().catch(() => '');
    results.hasAppContent = bodyText.trim().length > 10;
    results.frameworkOverlay = /(webpack|vite|next\.js|react|framework)/i.test(snapshot) &&
                                /(error|overlay|hmr|failed)/i.test(snapshot);
    await page.screenshot({ path: path.join(outDir, 'e01_initial.png'), fullPage: false });

    // 1. Load a pool from the left sidebar (first item under 通达信 tab)
    const firstTdxItem = page.locator('#poolListTdx li').first();
    if (await firstTdxItem.isVisible().catch(() => false)) {
      await firstTdxItem.click();
      await sleep(2500);
      const bodyAfterLoad = await page.locator('body').innerText().catch(() => '');
      results.loadPoolFromSidebar = bodyAfterLoad.includes('已加载') || !bodyAfterLoad.includes('请先加载');
    }
    await page.screenshot({ path: path.join(outDir, 'e02_pool_loaded.png'), fullPage: false });

    // 2. Select the first meaningful node and trigger property panel
    const selectedNodeInfo = await page.evaluate(() => {
      const nodes = window.canvas.getNodes();
      const skipTypes = { text_label: 1, label: 1, tdx_text_label: 1, tdx_deco_text: 1, tdx_decorative: 1,
                          container: 1, tdx_container: 1, state_column: 1, column: 1,
                          flow_arrow: 1, tdx_deco_line: 1, execution_order: 1 };
      const preferredRe = /candidate|source|state_pool|condition/;
      let node = nodes.find(function (n) { return n.type && preferredRe.test(n.type); });
      if (!node) node = nodes.find(function (n) { return n.type && !skipTypes[n.type]; });
      if (!node && nodes.length) node = nodes[0];
      if (!node) return null;
      return { id: node.id, type: node.type, label: node.label || node.text || '' };
    });
    results.selectedNode = selectedNodeInfo;
    if (selectedNodeInfo) {
      const nodeEl = page.locator('[data-node-id="' + selectedNodeInfo.id + '"]').first();
      if (await nodeEl.isVisible().catch(() => false)) {
        await nodeEl.click();
        await sleep(600);
      } else {
        await page.evaluate((id) => {
          window.canvas.selectNode(id);
          const nodes = window.canvas.getNodes();
          const node = nodes.find(function (n) { return n.id === id; });
          if (node && typeof window.canvas.onNodeClick === 'function') {
            window.canvas.onNodeClick(id, node);
          }
        }, selectedNodeInfo.id);
        await sleep(800);
      }
      const ppInfo = await page.evaluate((id) => {
        if (window.propPanel && typeof window.propPanel.showForNode === 'function') {
          window.propPanel.showForNode(id);
          return {
            containerId: window.propPanel.container && window.propPanel.container.id,
            containerTag: window.propPanel.container && window.propPanel.container.tagName,
            titleId: window.propPanel.titleEl && window.propPanel.titleEl.id,
            currentNodeId: window.propPanel._currentNodeId
          };
        }
        return null;
      }, selectedNodeInfo.id);
      results.propPanelInfo = ppInfo;
      await sleep(200);
      for (let i = 0; i < 50; i++) {
        const bodyHtml = await page.locator('#panelBody').innerHTML().catch(() => '');
        if (!bodyHtml.includes('panel-placeholder') && bodyHtml.includes('td-field')) break;
        await sleep(100);
      }
    }
    const propPanelText = await page.locator('#panelRight').innerText().catch(() => '');
    const fieldCount = await page.locator('#panelRight .td-field').count();
    const panelBodyHtml = await page.locator('#panelBody').innerHTML().catch(() => '');
    const panelError = await page.locator('#panelBody .td-error').innerText().catch(() => '');
    results.nodeSelectedDebug = propPanelText.slice(0, 200);
    results.nodeSelectedFieldCount = fieldCount;
    results.panelBodyPreview = panelBodyHtml.slice(0, 400);
    results.panelError = panelError;
    results.panelNetwork = network;
    results.nodeSelected = !panelBodyHtml.includes('panel-placeholder') && fieldCount > 0;
    await page.screenshot({ path: path.join(outDir, 'e03_node_selected.png'), fullPage: false });

    // 3. Right-click selected node -> node-specific context menu
    const ctxInfo = await page.evaluate(() => {
      const canvasWrapper = document.getElementById('canvasWrapper');
      const nid = window.canvas.getSelectedNodeId();
      if (!nid || !canvasWrapper) return { ok: false, reason: 'no node selected' };
      const el = window.canvas.nodeElements ? window.canvas.nodeElements.get(nid) : null;
      if (!el) return { ok: false, reason: 'no DOM element for node' };
      const rect = el.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const evt = new MouseEvent('contextmenu', {
        bubbles: true,
        cancelable: true,
        button: 2,
        clientX: cx,
        clientY: cy,
        relatedTarget: canvasWrapper
      });
      canvasWrapper.dispatchEvent(evt);
      return { ok: true, cx: cx, cy: cy, nid: nid };
    });
    results.ctxTriggerInfo = ctxInfo;
    if (ctxInfo.ok) await sleep(900);
    const menuText = await page.locator('#contextMenu').innerText().catch(() => '');
    const menuVisible = await page.locator('#contextMenu').isVisible().catch(() => false);
    results.nodeContextMenu = menuVisible && (menuText.includes('属性') || menuText.includes('综合设置') || menuText.includes('复制'));
    results.nodeContextMenuText = menuText.slice(0, 200);
    await page.screenshot({ path: path.join(outDir, 'e04_node_context_menu.png'), fullPage: false });
    if (menuVisible) {
      await page.mouse.click(100, 100);
      await sleep(200);
    }

    // 4. Edge property panel: select first edge and verify table-driven panel renders
    const edgeInfo = await page.evaluate(() => {
      const edges = window.canvas.getEdges ? window.canvas.getEdges() : (window.poolData && window.poolData.data && window.poolData.data.edges);
      if (!edges || !edges.length) return null;
      const edge = edges[0];
      if (window.canvas.selectEdge && typeof window.canvas.selectEdge === 'function') {
        window.canvas.selectEdge(edge.id);
      }
      if (window.propPanel && typeof window.propPanel.showForEdge === 'function') {
        window.propPanel.showForEdge(edge.id);
      }
      return { id: edge.id, type: edge.type || '', label: edge.label || '' };
    });
    results.selectedEdge = edgeInfo;
    if (edgeInfo) {
      await sleep(600);
      for (let i = 0; i < 30; i++) {
        const bodyHtml = await page.locator('#panelBody').innerHTML().catch(() => '');
        if (!bodyHtml.includes('panel-placeholder') && bodyHtml.includes('td-field')) break;
        await sleep(100);
      }
      const edgeFieldCount = await page.locator('#panelRight .td-field').count();
      const edgePanelHtml = await page.locator('#panelBody').innerHTML().catch(() => '');
      results.edgeSelectedFieldCount = edgeFieldCount;
      results.edgeSelected = !edgePanelHtml.includes('panel-placeholder') && edgeFieldCount > 0;
      results.edgePanelPreview = edgePanelHtml.slice(0, 400);
    }
    await page.screenshot({ path: path.join(outDir, 'e05_edge_selected.png'), fullPage: false });

    // 5. Import / Export: export JSON, then import a minimal JSON pool (must be in design/edit mode)
    const importFilePath = path.join(outDir, 'qa_import_test.json');
    const minimalPool = {
      version: 2,
      name: 'QA Import Test',
      pool_type: 'dzh',
      pool_meta: { name: 'QA Import Test', pool_type: 'dzh', type: 'dzh' },
      nodes: [
        { id: 'qa_src', type: 'market_source', label: 'QA Source', x: 100, y: 100, params: {} },
        { id: 'qa_state', type: 'state_pool', label: 'QA State', x: 300, y: 100, params: {} }
      ],
      edges: [
        { id: 'qa_edge', source: 'qa_src', target: 'qa_state', type: 'flow_edge', params: {} }
      ]
    };
    fs.writeFileSync(importFilePath, JSON.stringify(minimalPool, null, 2));

    // 5a Export JSON
    try {
      const exportResponsePromise = page.waitForResponse(r => r.url().includes('/api/json/export'), { timeout: 15000 }).catch(() => null);
      await safeClick(page.locator('#btnExport').first(), 3000, true);
      await sleep(400);
      const jsonExportItem = page.locator('#exportDropdown [data-format="json"]').filter({ hasText: /JSON/ }).first();
      await safeClick(jsonExportItem, 3000, true);
      const exportResp = await exportResponsePromise;
      results.exportResponseStatus = exportResp ? exportResp.status() : null;
      await sleep(500);
      const statusAfterExport = await page.locator('#statusText').innerText().catch(() => '');
      const bodyAfterExport = await page.locator('body').innerText().catch(() => '');
      results.exportJsonOk = (exportResp && exportResp.ok()) ||
                             /JSON导出成功|导出成功/.test(bodyAfterExport) ||
                             statusAfterExport === '已导出';
      results.exportStatusText = statusAfterExport;
    } catch (exportErr) {
      results.exportJsonOk = false;
      results.exportError = exportErr.message;
    }

    // 5b Import JSON
    try {
      await safeClick(page.locator('#btnImport').first(), 3000, true);
      await sleep(400);
      const jsonImportItem = page.locator('#importDropdown [data-format="json"]').filter({ hasText: /JSON/ }).first();
      await safeClick(jsonImportItem, 3000, true);
      await sleep(700);
      const fileInput = page.locator('#fileInput');
      await fileInput.setInputFiles(importFilePath);
      await sleep(1000);
      const importResponsePromise = page.waitForResponse(r => r.url().includes('/api/json/import'), { timeout: 15000 }).catch(() => null);
      await safeClick(page.locator('#btnConfirmImport').first(), 3000, true);
      const importResp = await importResponsePromise;
      results.importResponseStatus = importResp ? importResp.status() : null;
      await sleep(500);
      const statusAfterImport = await page.locator('#statusText').innerText().catch(() => '');
      const bodyAfterImport = await page.locator('body').innerText().catch(() => '');
      results.importJsonOk = (importResp && importResp.ok()) ||
                             /JSON导入成功|导入成功/.test(bodyAfterImport) ||
                             /JSON导入成功|导入成功/.test(statusAfterImport);
      results.importBodyHint = bodyAfterImport.slice(0, 300);
      results.importStatusText = statusAfterImport;
    } catch (importErr) {
      results.importJsonOk = false;
      results.importError = importErr.message;
    }
    await page.screenshot({ path: path.join(outDir, 'e06_import_export.png'), fullPage: false });

    // 6. Event panel toggle (try keyboard shortcut first, then toggle button)
    try {
      await page.keyboard.press('e');
      await sleep(500);
      let eventPanelVisible = await page.locator('#eventPanel').isVisible().catch(() => false);
      if (!eventPanelVisible) {
        const toggleBtn = page.locator('#eventPanelToggle').first();
        if (await toggleBtn.isVisible().catch(() => false)) {
          await toggleBtn.click({ timeout: 3000, force: true });
          await sleep(500);
          eventPanelVisible = await page.locator('#eventPanel').isVisible().catch(() => false);
        }
      }
      results.eventPanelToggle = eventPanelVisible;
    } catch (eventErr) {
      results.eventPanelError = eventErr.message;
    }
    await page.screenshot({ path: path.join(outDir, 'e07_event_panel.png'), fullPage: false });

    // 7. Run controls: start, pause, stop (with visibility/enabled checks)
    try {
      const startBtn = page.locator('#btnStart').filter({ hasText: /开始/ }).first();
      if (await startBtn.isVisible().catch(() => false) && await startBtn.isEnabled().catch(() => false)) {
        await startBtn.click();
        await sleep(1200);
        results.runStart = true;
      }
      await page.screenshot({ path: path.join(outDir, 'e08_run_start.png'), fullPage: false });

      const pauseBtn = page.locator('#btnPause').filter({ hasText: /暂停/ }).first();
      if (await pauseBtn.isVisible().catch(() => false) && await pauseBtn.isEnabled().catch(() => false)) {
        await pauseBtn.click();
        await sleep(800);
        results.runPause = true;
      }

      const stopBtn = page.locator('#btnStop').filter({ hasText: /停止/ }).first();
      if (await stopBtn.isVisible().catch(() => false) && await stopBtn.isEnabled().catch(() => false)) {
        await stopBtn.click();
        await sleep(800);
        results.runStop = true;
      }
      await page.screenshot({ path: path.join(outDir, 'e09_run_controls.png'), fullPage: false });
    } catch (runErr) {
      results.runControlError = runErr.message;
      await page.screenshot({ path: path.join(outDir, 'e09_run_controls.png'), fullPage: false }).catch(() => {});
    }

    // 8. Mode switching
    try {
      const modes = [
        { name: '实盘', key: 'modeSwitchRun', id: '#btnRun' },
        { name: '回放', key: 'modeSwitchReplay', id: '#btnReplay' },
        { name: '仿真', key: 'modeSwitchSimulation', id: '#btnSimulation' },
        { name: '设计模式', key: 'modeSwitchDesign', id: '#btnDesign' }
      ];
      for (const m of modes) {
        const btn = page.locator(m.id).first();
        if (await btn.isVisible().catch(() => false) && await btn.isEnabled().catch(() => false)) {
          await btn.click({ timeout: 3000, force: true });
          results[m.key] = true;
          await sleep(600);
        }
      }
      await page.screenshot({ path: path.join(outDir, 'e10_mode_switches.png'), fullPage: false });
    } catch (modeErr) {
      results.modeSwitchError = modeErr.message;
      await page.screenshot({ path: path.join(outDir, 'e10_mode_switches.png'), fullPage: false }).catch(() => {});
    }

    await browser.close();

    // 9. Mobile viewport smoke test in a fresh browser instance
    try {
      const mobileBrowser = await chromium.launch({ headless: true });
      const mobileContext = await mobileBrowser.newContext({ viewport: { width: 375, height: 812 }, userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)' });
      const mobilePage = await mobileContext.newPage();
      await mobilePage.goto('http://127.0.0.1:8000/index.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
      await sleep(3500);
      const mobileBody = await mobilePage.locator('body').innerText().catch(() => '');
      results.mobileOk = mobileBody.trim().length > 10 &&
                         (mobileBody.includes('MetaCore') || mobileBody.includes('股票池') || mobileBody.includes('模式'));
      await mobilePage.screenshot({ path: path.join(outDir, 'e11_mobile.png'), fullPage: false });
      await mobileContext.close();
      await mobileBrowser.close();
    } catch (mobileErr) {
      results.mobileError = mobileErr.message;
    }

  } catch (e) {
    results.error = e.message;
    await page.screenshot({ path: path.join(outDir, 'e99_error.png'), fullPage: false }).catch(() => {});
    await browser.close().catch(() => {});
  }

  results.relevantErrors = logs.filter(l => l.level === 'error' || l.level === 'warning')
    .filter(l => !/akshare|requests|HQChartPy2|watchdog|未安装|不可用|cell_type_registry|WebSocket|HighlightManager|降级为轮询|SimControl|仿真会话未启动|resume failed/.test(l.text));
  results.pageErrors = errs;

  results.passed = results.hasAppContent && !results.frameworkOverlay &&
                   results.loadPoolFromSidebar && results.nodeSelected &&
                   results.nodeContextMenu && results.runStart &&
                   results.modeSwitchRun && results.modeSwitchReplay &&
                   results.modeSwitchSimulation && results.modeSwitchDesign &&
                   results.edgeSelected && results.importJsonOk &&
                   results.exportJsonOk && results.eventPanelToggle &&
                   results.mobileOk &&
                   results.relevantErrors.length === 0 && errs.length === 0;

  fs.writeFileSync(path.join(outDir, 'result-extended.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({
    passed: results.passed,
    url: results.url,
    title: results.title,
    loadPoolFromSidebar: results.loadPoolFromSidebar,
    nodeSelected: results.nodeSelected,
    nodeContextMenu: results.nodeContextMenu,
    edgeSelected: results.edgeSelected,
    edgeSelectedFieldCount: results.edgeSelectedFieldCount,
    importJsonOk: results.importJsonOk,
    exportJsonOk: results.exportJsonOk,
    runStart: results.runStart,
    runPause: results.runPause,
    runStop: results.runStop,
    modeSwitches: {
      run: results.modeSwitchRun,
      replay: results.modeSwitchReplay,
      simulation: results.modeSwitchSimulation,
      design: results.modeSwitchDesign
    },
    eventPanelToggle: results.eventPanelToggle,
    mobileOk: results.mobileOk,
    relevantErrors: results.relevantErrors.length,
    pageErrors: errs.length
  }, null, 2));
})();
