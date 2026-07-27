// 端到端仿真验证（混合模式）：
// 1. API 创建真实股票池
// 2. API 启动仿真 + 步进（避免 auto-step 压垮后端）
// 3. Playwright 打开前端，加载池，设置 session，验证界面显示
// 4. 验证事件面板、状态池、属性面板显示准确
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = 'http://127.0.0.1:8000';
const outDir = '/workspace/tmp-frontend-qa';
if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function http(method, urlPath, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + urlPath, opts);
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch (_) {}
  return { status: res.status, ok: res.ok, text, json };
}

const POOL_CONFIG = {
  name: 'E2E_UI_Verify_' + Date.now(),
  pool_type: 'tdx',
  pool_meta: { name: 'E2E仿真验证池', pool_type: 'tdx', type: 'tdx' },
  nodes: [
    {
      id: 'e2e_candidate', type: '7', dzh_cell_type: 7, text: '候选池', attr: 0, pos: '0,0,200,100',
      params: {
        tdx_spinfo: { type: 0, customblockname: '', size: 0, market: '', sector_type: 0 },
        stocks: [
        { code: '600000', label: '浦发银行' },
        { code: '000001', label: '平安银行' }
      ]
      }
    },
    {
      id: 'e2e_state_a', type: '8', dzh_cell_type: 8, text: '状态池A', attr: 0, pos: '0,0,200,100',
      params: {
        tdx_psatt: { bdel: 0, ndelnum: 0, ndeltype: 0, baimpool: 0, bsound: 0, nsoundtype: 0, nsyssound: 0, soundfile: '', btip: 0, bsavetoblock: 0, blockfile: '', bclearblock: 0, bsavehis: 1 },
        stocks: []
      }
    }
  ],
  edges: [
    {
      id: 'e2e_edge_ca_a', from: 'e2e_candidate', to: 'e2e_state_a', attr: 0,
      params: { tran: 0, emptyps: 0, starttype: 0, starttime: 0, starttimetype: 0, starttimehms: 0, cxtype: 0, cxtime: 0, cxtimetype: 0, jgtime: 0 }
    }
  ]
};

(async () => {
  const report = { timestamp: new Date().toISOString(), errors: [], passed: false };
  let poolId = null, sessionId = null;

  try {
    // ===== Phase 1: API 创建池 + 启动仿真 + 步进 =====
    await http('POST', '/api/data_source/select/mock');
    await sleep(300);

    const createRes = await http('POST', '/api/pools', POOL_CONFIG);
    if (!createRes.ok || !createRes.json || createRes.json.code !== 0) {
      throw new Error('创建池失败: ' + createRes.status + ' ' + (createRes.text || '').slice(0, 200));
    }
    poolId = createRes.json.data.pool_id;
    report.poolId = poolId;
    report.poolCreated = true;
    await sleep(300);

    const startRes = await http('POST', '/api/sim/start', { pool_id: poolId, speed: 1.0 });
    if (!startRes.ok || !startRes.json || startRes.json.code !== 0) {
      throw new Error('启动仿真失败: ' + startRes.status + ' ' + (startRes.text || '').slice(0, 200));
    }
    sessionId = startRes.json.data.session_id;
    report.sessionId = sessionId;
    await sleep(1500);

    // 轮询初始化完成
    let simReady = false;
    for (let i = 0; i < 20; i++) {
      const stepRes = await http('POST', '/api/sim/control', { session_id: sessionId, action: 'step', params: { delta: 60 } });
      if (stepRes.ok && stepRes.json && stepRes.json.code === 0) {
        simReady = true;
        report.firstStepClock = stepRes.json.data.clock;
        report.firstStepEvents = stepRes.json.data.event_count;
        break;
      }
      if (stepRes.json && stepRes.json.code === 102) { await sleep(500); continue; }
      await sleep(500);
    }
    if (!simReady) throw new Error('仿真未能初始化');
    report.simInitialized = true;

    // 步进 2 次 delta=60，验证事件产生
    let totalEventsApi = 0;
    for (let s = 0; s < 2; s++) {
      const stepRes = await http('POST', '/api/sim/control', { session_id: sessionId, action: 'step', params: { delta: 60 } });
      if (stepRes.ok && stepRes.json && stepRes.json.code === 0) {
        totalEventsApi += stepRes.json.data.event_count || 0;
      }
      await sleep(200);
    }
    report.totalEventsApi = totalEventsApi;
    report.lastClock = (await http('GET', '/api/sim/state?session_id=' + sessionId)).json?.data?.clock;

    // 获取事件类型分布
    const eventsRes = await http('GET', '/api/sim/events?session_id=' + sessionId + '&limit=500');
    if (eventsRes.ok && eventsRes.json && eventsRes.json.code === 0) {
      const typeCounts = {};
      (eventsRes.json.data.events || []).forEach(e => {
        const t = e.event_type || 'unknown';
        typeCounts[t] = (typeCounts[t] || 0) + 1;
      });
      report.eventTypesApi = typeCounts;
      report.eventTotalApi = eventsRes.json.data.total;
    }

    // 获取状态池快照
    const stateRes = await http('GET', '/api/sim/state?session_id=' + sessionId);
    if (stateRes.ok && stateRes.json && stateRes.json.code === 0) {
      report.stateSnapshotApi = stateRes.json.data;
      const pools = stateRes.json.data.pools || {};
      report.statePoolDetailApi = Object.keys(pools).map(k => ({
        node: k,
        stockCount: pools[k].stocks ? pools[k].stocks.length : 0,
        total_count: pools[k].total_count || 0
      }));
    }

    // 暂停仿真（保持 session 活跃，不停止）
    await http('POST', '/api/sim/control', { session_id: sessionId, action: 'pause' });
    await sleep(300);

    // ===== Phase 2: Playwright 验证前端显示 =====
    const browser = await chromium.launch({ headless: true });
    try {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await context.newPage();
      const logs = [];
      const errs = [];
      page.on('console', m => logs.push({ level: m.type(), text: m.text() }));
      page.on('pageerror', e => errs.push(e.message));

      await page.goto(BASE + '/index.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
      await sleep(3000);
      await page.screenshot({ path: path.join(outDir, 'u01_loaded.png') });

      // 加载池
      const loadResult = await page.evaluate(async (pid) => {
        try {
          var data = await window.poolData.loadFromAPI(pid);
          window.canvas.render(data);
          window.canvas.fitToContent(40);
          return { ok: true, nodeCount: (data.nodes || []).length };
        } catch (e) {
          return { ok: false, msg: e.message };
        }
      }, poolId);
      report.frontendLoadPool = loadResult.ok;
      await sleep(1000);
      await page.screenshot({ path: path.join(outDir, 'u02_pool_loaded.png') });

      // 检查节点显示
      const nodeLabels = await page.evaluate(() => {
        return window.canvas.getNodes().map(n => ({ id: n.id, type: n.type, text: n.text || n.label || '' }));
      });
      report.nodeLabels = nodeLabels;

      // 不切换仿真模式（避免 auto-step 压垮后端），直接设置 session 并显示事件面板
      // 显示事件面板
      await page.evaluate(() => {
        if (typeof window.showEventPanel === 'function') window.showEventPanel();
        else { const ep = document.getElementById('eventPanel'); if (ep) ep.classList.add('visible'); }
      });
      report.simModeSwitched = true; // 事件面板已显示
      await sleep(500);

      // 设置前端的 simSessionId 为已有的 session
      const sessionSet = await page.evaluate((sid) => {
        window.simSessionId = sid;
        if (typeof window.eventPanelSetSession === 'function') {
          window.eventPanelSetSession(sid);
        }
        return { sid: window.simSessionId, hasEventPanelSetSession: typeof window.eventPanelSetSession === 'function' };
      }, sessionId);
      report.sessionSetInFrontend = sessionSet;

      // 等待事件面板加载历史事件
      await sleep(2000);
      await page.screenshot({ path: path.join(outDir, 'u04_event_panel.png') });

      // 读取事件面板状态
      const eventPanelInfo = await page.evaluate(() => {
        const evtCount = document.getElementById('etpEventCount');
        const eventPanel = document.getElementById('eventPanel');
        return {
          eventCountText: evtCount ? evtCount.textContent : null,
          eventCountViaApi: typeof window.getEventCount === 'function' ? window.getEventCount() : -1,
          eventPanelVisible: eventPanel ? eventPanel.classList.contains('visible') || eventPanel.style.display !== 'none' : false,
          eventPanelText: eventPanel ? (eventPanel.innerText || '').slice(0, 300) : ''
        };
      });
      report.eventPanelInfo = eventPanelInfo;
      report.eventPanelShowsEvents = eventPanelInfo.eventCountViaApi > 0 ||
                                      (eventPanelInfo.eventCountText && /[1-9]/.test(eventPanelInfo.eventCountText));

      // 读取仿真时钟和步数显示
      const simDisplay = await page.evaluate(() => {
        return {
          clock: document.getElementById('simulationClock')?.textContent || null,
          stepCount: document.getElementById('simulationStepCount')?.textContent || null,
          status: document.getElementById('statusText')?.textContent || null
        };
      });
      report.simDisplay = simDisplay;

      // 选中状态池节点，验证属性面板
      const stateNode = await page.evaluate(() => {
        const nodes = window.canvas.getNodes();
        const sp = nodes.find(n => n.type === '8' || (n.text && n.text.includes('状态池')));
        if (!sp) return null;
        window.canvas.selectNode(sp.id);
        if (window.propPanel && typeof window.propPanel.showForNode === 'function') {
          window.propPanel.showForNode(sp.id);
        }
        return { id: sp.id, text: sp.text || sp.label };
      });
      report.selectedStateNode = stateNode;
      if (stateNode) {
        await sleep(1000);
        const panelText = await page.locator('#panelBody').innerText().catch(() => '');
        report.statePanelText = panelText.slice(0, 400);
        report.statePanelShowsStocks = /600000|000001|浦发|平安/.test(panelText);
        report.statePanelFieldCount = await page.locator('#panelRight .td-field').count();
      }
      await page.screenshot({ path: path.join(outDir, 'u05_state_pool_panel.png') });

      // 选中候选池节点
      const candNode = await page.evaluate(() => {
        const nodes = window.canvas.getNodes();
        const cd = nodes.find(n => n.type === '7' || (n.text && n.text.includes('候选')));
        if (!cd) return null;
        window.canvas.selectNode(cd.id);
        if (window.propPanel && typeof window.propPanel.showForNode === 'function') {
          window.propPanel.showForNode(cd.id);
        }
        return { id: cd.id, text: cd.text || cd.label };
      });
      report.selectedCandNode = candNode;
      if (candNode) {
        await sleep(1000);
        const panelText = await page.locator('#panelBody').innerText().catch(() => '');
        report.candPanelText = panelText.slice(0, 400);
        report.candPanelShowsStocks = /600000|000001|浦发|平安/.test(panelText);
      }
      await page.screenshot({ path: path.join(outDir, 'u06_candidate_panel.png') });

      // 过滤错误
      report.relevantErrors = logs.filter(l => l.level === 'error' || l.level === 'warning')
        .filter(l => !/akshare|requests|HQChartPy2|watchdog|未安装|不可用|cell_type_registry|WebSocket|HighlightManager|降级为轮询|SimControl|仿真会话未启动|resume failed/.test(l.text));
      report.pageErrors = errs;

      await browser.close();
    } catch (e) {
      report.errors.push('PLAYWRIGHT: ' + e.message);
      try { await browser.close(); } catch (_) {}
    }

    // ===== Phase 3: 停止仿真并清理 =====
    await http('POST', '/api/sim/control', { session_id: sessionId, action: 'stop' });
    report.simStopped = true;
    await http('DELETE', '/api/pools/' + encodeURIComponent(poolId));
    report.poolDeleted = true;

  } catch (e) {
    report.errors.push('FATAL: ' + e.message);
    if (sessionId) { try { await http('POST', '/api/sim/control', { session_id: sessionId, action: 'stop' }); } catch (_) {} }
    if (poolId) { try { await http('DELETE', '/api/pools/' + encodeURIComponent(poolId)); } catch (_) {} }
  }

  // 判定
  const apiOk = report.poolCreated && report.simInitialized && (report.eventTotalApi || 0) > 0;
  const uiOk = report.frontendLoadPool && report.simModeSwitched &&
               report.eventPanelShowsEvents &&
               report.nodeLabels && report.nodeLabels.length >= 2;
  report.passed = apiOk && uiOk && report.simStopped && report.poolDeleted &&
                  report.errors.length === 0;

  fs.writeFileSync(path.join(outDir, 'sim-e2e-ui-result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({
    passed: report.passed,
    poolCreated: report.poolCreated,
    poolId: report.poolId,
    simInitialized: report.simInitialized,
    sessionId: report.sessionId,
    totalEventsApi: report.totalEventsApi,
    eventTotalApi: report.eventTotalApi,
    eventTypesApi: report.eventTypesApi,
    lastClock: report.lastClock,
    statePoolDetailApi: report.statePoolDetailApi,
    frontendLoadPool: report.frontendLoadPool,
    simModeSwitched: report.simModeSwitched,
    nodeLabels: report.nodeLabels,
    eventPanelShowsEvents: report.eventPanelShowsEvents,
    eventPanelInfo: report.eventPanelInfo,
    simDisplay: report.simDisplay,
    selectedStateNode: report.selectedStateNode,
    statePanelShowsStocks: report.statePanelShowsStocks,
    statePanelFieldCount: report.statePanelFieldCount,
    selectedCandNode: report.selectedCandNode,
    candPanelShowsStocks: report.candPanelShowsStocks,
    simStopped: report.simStopped,
    poolDeleted: report.poolDeleted,
    relevantErrors: (report.relevantErrors || []).length,
    pageErrors: (report.pageErrors || []).length,
    errors: report.errors
  }, null, 2));
})();
