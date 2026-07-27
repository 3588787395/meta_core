// 端到端仿真验证：创建真实股票池 → 启动仿真 → 步进 → 事件统计
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

(async () => {
  const report = {
    timestamp: new Date().toISOString(),
    poolCreated: false,
    poolId: null,
    sessionId: null,
    simInitialized: false,
    steps: [],
    eventTypes: {},
    eventTotal: 0,
    finalClock: null,
    finalState: null,
    poolDeleted: false,
    errors: [],
    passed: false
  };

  try {
    // 1. 切换数据源到 mock
    const dsRes = await http('POST', '/api/data_source/select/mock');
    report.mockDsSelected = dsRes.ok;
    if (!dsRes.ok) report.errors.push('mock 数据源切换失败: ' + dsRes.status);
    await sleep(300);

    // 2. 创建真实股票池（TDX 原生格式：候选池→状态池）
    const poolConfig = {
      name: 'E2E_Sim_Verify_' + Date.now(),
      pool_type: 'tdx',
      pool_meta: { name: 'E2E仿真验证池', pool_type: 'tdx', type: 'tdx' },
      nodes: [
        {
          id: 'e2e_candidate',
          type: '7',
          dzh_cell_type: 7,
          text: '候选池',
          attr: 0,
          pos: '0,0,200,100',
          params: {
            tdx_spinfo: { type: 0, customblockname: '', size: 0, market: '', sector_type: 0 },
            stocks: [
              { code: '600000', label: '浦发银行' },
              { code: '000001', label: '平安银行' },
              { code: '600519', label: '贵州茅台' }
            ]
          }
        },
        {
          id: 'e2e_state_a',
          type: '8',
          dzh_cell_type: 8,
          text: '状态池A',
          attr: 0,
          pos: '0,0,200,100',
          params: {
            tdx_psatt: {
              bdel: 0, ndelnum: 0, ndeltype: 0, baimpool: 0,
              bsound: 0, nsoundtype: 0, nsyssound: 0, soundfile: '',
              btip: 0, bsavetoblock: 0, blockfile: '', bclearblock: 0, bsavehis: 1
            },
            stocks: []
          }
        },
        {
          id: 'e2e_state_b',
          type: '8',
          dzh_cell_type: 8,
          text: '状态池B',
          attr: 0,
          pos: '0,0,200,100',
          params: {
            tdx_psatt: {
              bdel: 0, ndelnum: 0, ndeltype: 0, baimpool: 0,
              bsound: 0, nsoundtype: 0, nsyssound: 0, soundfile: '',
              btip: 0, bsavetoblock: 0, blockfile: '', bclearblock: 0, bsavehis: 1
            },
            stocks: []
          }
        }
      ],
      edges: [
        {
          id: 'e2e_edge_ca_a',
          from: 'e2e_candidate',
          to: 'e2e_state_a',
          attr: 0,
          params: {
            tran: 0, emptyps: 0, starttype: 0, starttime: 0,
            starttimetype: 0, starttimehms: 0, cxtype: 0, cxtime: 0,
            cxtimetype: 0, jgtime: 0
          }
        },
        {
          id: 'e2e_edge_a_b',
          from: 'e2e_state_a',
          to: 'e2e_state_b',
          attr: 0,
          params: {
            tran: 0, emptyps: 0, starttype: 0, starttime: 0,
            starttimetype: 0, starttimehms: 0, cxtype: 0, cxtime: 0,
            cxtimetype: 0, jgtime: 0
          }
        }
      ]
    };

    const createRes = await http('POST', '/api/pools', poolConfig);
    report.poolCreateStatus = createRes.status;
    if (!createRes.ok || !createRes.json || createRes.json.code !== 0) {
      report.errors.push('创建池失败: ' + createRes.status + ' ' + (createRes.text || '').slice(0, 200));
      throw new Error('create pool failed');
    }
    report.poolId = createRes.json.data.pool_id;
    report.poolCreated = true;
    await sleep(300);

    // 3. 启动仿真
    const startRes = await http('POST', '/api/sim/start', { pool_id: report.poolId, speed: 1.0 });
    report.simStartStatus = startRes.status;
    if (!startRes.ok || !startRes.json || startRes.json.code !== 0) {
      report.errors.push('启动仿真失败: ' + startRes.status + ' ' + (startRes.text || '').slice(0, 200));
      throw new Error('sim start failed');
    }
    report.sessionId = startRes.json.data.session_id;
    await sleep(1500); // 等待 _warm_simulator 后台初始化

    // 4. 轮询确认初始化完成
    for (let i = 0; i < 20; i++) {
      const stepRes = await http('POST', '/api/sim/control', {
        session_id: report.sessionId,
        action: 'step',
        params: { delta: 60 }
      });
      if (stepRes.ok && stepRes.json && stepRes.json.code === 0) {
        report.simInitialized = true;
        report.steps.push({
          step: 1,
          clock: stepRes.json.data.clock,
          event_count: stepRes.json.data.event_count,
          changed_codes: stepRes.json.data.changed_codes,
          events_preview: (stepRes.json.data.events || []).slice(0, 3).map(e => e.event_type)
        });
        break;
      }
      if (stepRes.json && stepRes.json.code === 102) {
        await sleep(500);
        continue;
      }
      report.errors.push('step 初始化失败: code=' + (stepRes.json && stepRes.json.code) + ' ' + (stepRes.text || '').slice(0, 200));
      await sleep(500);
    }

    if (!report.simInitialized) {
      throw new Error('仿真未能初始化');
    }

    // 5. 连续步进 10 次（delta=60s），收集事件
    let lastTotal = 0;
    for (let s = 2; s <= 11; s++) {
      const stepRes = await http('POST', '/api/sim/control', {
        session_id: report.sessionId,
        action: 'step',
        params: { delta: 60 }
      });
      if (!stepRes.ok || !stepRes.json || stepRes.json.code !== 0) {
        report.errors.push('step ' + s + ' 失败: ' + (stepRes.text || '').slice(0, 150));
        continue;
      }
      const d = stepRes.json.data;
      // 查询累计事件
      const eventsRes = await http('GET', '/api/sim/events?session_id=' + report.sessionId + '&limit=500');
      let total = 0;
      let typeCounts = {};
      if (eventsRes.ok && eventsRes.json && eventsRes.json.code === 0) {
        total = eventsRes.json.data.total || 0;
        (eventsRes.json.data.events || []).forEach(e => {
          const t = e.event_type || 'unknown';
          typeCounts[t] = (typeCounts[t] || 0) + 1;
        });
      }
      report.steps.push({
        step: s,
        clock: d.clock,
        event_count: d.event_count,
        events_this_step: (d.events || []).length,
        events_preview: (d.events || []).slice(0, 3).map(e => e.event_type),
        cumulative_total: total,
        cumulative_types: Object.assign({}, typeCounts)
      });
      // 累加类型到总体
      Object.keys(typeCounts).forEach(t => {
        report.eventTypes[t] = (report.eventTypes[t] || 0) + typeCounts[t];
      });
      lastTotal = total;
      await sleep(200);
    }
    report.eventTotal = lastTotal;

    // 6. 查询仿真状态（状态池股票分布）
    const stateRes = await http('GET', '/api/sim/state?session_id=' + report.sessionId);
    if (stateRes.ok && stateRes.json && stateRes.json.code === 0) {
      report.finalState = stateRes.json.data;
      report.finalClock = stateRes.json.data.clock;
    }

    // 7. 暂停 → 恢复 → 停止
    const pauseRes = await http('POST', '/api/sim/control', { session_id: report.sessionId, action: 'pause' });
    report.simPauseOk = pauseRes.ok && pauseRes.json && pauseRes.json.code === 0;
    await sleep(300);
    const resumeRes = await http('POST', '/api/sim/control', { session_id: report.sessionId, action: 'resume' });
    report.simResumeOk = resumeRes.ok && resumeRes.json && resumeRes.json.code === 0;
    await sleep(300);
    const stopRes = await http('POST', '/api/sim/control', { session_id: report.sessionId, action: 'stop' });
    report.simStopOk = stopRes.ok && stopRes.json && stopRes.json.code === 0;

    // 8. 删除测试池
    const delRes = await http('DELETE', '/api/pools/' + encodeURIComponent(report.poolId));
    report.poolDeleted = delRes.ok && delRes.json && delRes.json.code === 0;

  } catch (e) {
    report.errors.push('FATAL: ' + e.message);
    // 尝试清理
    if (report.sessionId) {
      try { await http('POST', '/api/sim/control', { session_id: report.sessionId, action: 'stop' }); } catch (_) {}
    }
    if (report.poolId) {
      try { await http('DELETE', '/api/pools/' + encodeURIComponent(report.poolId)); } catch (_) {}
    }
  }

  // 判定
  const hasEvents = report.eventTotal > 0;
  const clockAdvanced = report.steps.length >= 2 &&
                        report.steps[report.steps.length - 1].clock > report.steps[0].clock;
  const typeDiverse = Object.keys(report.eventTypes).length >= 2;
  const controlOk = report.simPauseOk && report.simResumeOk && report.simStopOk;
  report.passed = report.poolCreated && report.simInitialized &&
                  hasEvents && clockAdvanced && typeDiverse &&
                  controlOk && report.poolDeleted &&
                  report.errors.length === 0;

  fs.writeFileSync(path.join(outDir, 'sim-e2e-result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({
    passed: report.passed,
    poolCreated: report.poolCreated,
    poolId: report.poolId,
    sessionId: report.sessionId,
    simInitialized: report.simInitialized,
    stepsExecuted: report.steps.length,
    eventTotal: report.eventTotal,
    eventTypes: report.eventTypes,
    clockAdvanced: clockAdvanced,
    typeDiverse: typeDiverse,
    controlOk: controlOk,
    poolDeleted: report.poolDeleted,
    errors: report.errors
  }, null, 2));
})();
