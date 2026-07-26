/**
 * 事件面板 - 可视化版
 * 职责：仅渲染后端已格式化的事件流与计时器队列，不持有业务真值源。
 * - 事件统一来自 /api/events/stream（SSE），历史首批通过 /api/events/recent
 * - 所有 category / display_ts / display_time / state / trigger_type 等字段直接信任后端
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  // DOM 元素
  const eventPanel = $('eventPanel');
  const eventCountEl = $('etpEventCount');
  const epDragHandle = $('epDragHandle');
  const epHeader = $('epHeader');
  const epResizeHandle = $('epResizeHandle');
  const epToggleSym = $('epToggleSym');
  const btnPauseResume = $('btnPauseResume');
  const btnClearEvents = $('btnClearEvents');
  const btnToggleEvents = $('btnToggleEvents');
  const btnCloseEvents = $('btnCloseEvents');
  const btnPauseIcon = $('btnPauseIcon');
  const eventViz = $('eventViz');
  const eventVizBody = $('eventVizBody');
  const eventMatrix = $('eventMatrix');
  const eventMatrixCanvas = $('eventMatrixCanvas');
  const eventScatterCanvas = $('eventScatterCanvas');
  const eventTimerQueue = $('eventTimerQueue');
  const timerQueueCount = $('timerQueueCount');
  const timerQueueBody = $('timerQueueBody');
  const timerQueueCanvas = $('timerQueueCanvas');
  const eventDetail = $('eventDetail');
  const eventDetailTitle = $('eventDetailTitle');
  const eventDetailBody = $('eventDetailBody');
  const btnCloseDetail = $('btnCloseDetail');
  const btnToggleDetail = $('btnToggleDetail');
  const eventDetailToggleSym = $('eventDetailToggleSym');

  // 分类展示配置：颜色、图标、显示名称（纯展示表）
  const CATEGORY_CONFIG = {
    tick:     { color: '#9e9e9e', icon: '📡', label: 'Tick' },
    bar:      { color: '#2196f3', icon: '📈', label: 'Bar' },
    formula:  { color: '#4caf50', icon: '🧮', label: 'Formula' },
    edge:     { color: '#ff9800', icon: '🔀', label: 'Edge' },
    transfer: { color: '#9c27b0', icon: '🔄', label: 'Transfer' },
    signal:   { color: '#f44336', icon: '🔔', label: 'Signal' },
    order:    { color: '#ffc107', icon: '📋', label: 'Order' },
    ttl:      { color: '#b71c1c', icon: '⏰', label: 'TTL' },
    system:   { color: '#00bcd4', icon: '⚙', label: 'System' }
  };

  const CATEGORIES = Object.keys(CATEGORY_CONFIG);

  // 状态（仅展示缓冲与纯 UI 状态）
  let events = [];
  let pendingEvents = [];
  let timerQueue = [];
  let eventSource = null;
  let reconnectTimer = null;
  let sessionId = null;
  let totalEventCount = 0;
  let isPaused = false;
  let isCollapsed = false;
  let isPanelHidden = false;
  let isDragging = false;
  let isResizing = false;
  let dragStartX = 0, dragStartY = 0;
  let panelStartRight = 0, panelStartBottom = 0;
  let resizeStartY = 0, panelStartHeight = 0;
  let activeCategory = null;
  let selectedEventId = null;
  let activeFilters = new Set(CATEGORIES);
  let currentViz = 'matrix';
  let scatterLayout = 'category';
  let isDetailCollapsed = true;
  let scatterHitRegions = [];
  let matrixHitRegions = [];
  let timerHitRegions = [];
  const RECONNECT_DELAY = 3000;
  const MAX_EVENTS = 2000;
  const RENDER_THROTTLE = 200;
  const STORAGE_KEY = 'metacore_event_panel_state_v3';
  const DEFAULT_TIME_WINDOW = 60000;
  const MAX_TIME_WINDOW = 300000;
  const MATRIX_AXIS_HEIGHT = 20;
  const MATRIX_LABEL_WIDTH = 86;
  let renderTimer = null;
  let matrixNowLine = null;
  let matrixHoverEvent = null;
  let lastMatrixLayout = null;
  let lastScatterLayout = null;
  let categoryRowContainer = null;

  // 简单展示用状态样式（无推断逻辑）
  const STATE_STYLE = {
    pending:   { borderStyle: [3, 2], borderColor: '#ffc107', fillAlpha: 0.6, glowColor: 'rgba(255,193,7,0.4)', glowSize: 4 },
    triggered: { borderStyle: null,  borderColor: null,      fillAlpha: 1.0, glowColor: null,                glowSize: 5 },
    expired:   { borderStyle: [2, 2], borderColor: '#f44336', fillAlpha: 0.5, glowColor: 'rgba(244,67,54,0.5)', glowSize: 6 }
  };

  const TIMER_LIST_STYLE = {
    waiting: { label: '等待中', listBg: 'transparent',           listOpacity: 1.0, textDecoration: 'none',           fillColor: null,      strokeColor: null,      lineStyle: null,      fillAlpha: 1.0 },
    fired:   { label: '已触发', listBg: 'rgba(158,158,158,0.15)', listOpacity: 0.5, textDecoration: 'line-through', fillColor: '#9e9e9e', strokeColor: '#9e9e9e', lineStyle: [4, 3],    fillAlpha: 0.6 },
    expired: { label: '已过期', listBg: 'rgba(244,67,54,0.1)',    listOpacity: 0.5, textDecoration: 'line-through', fillColor: null,      strokeColor: '#f44336', lineStyle: [2, 2],    fillAlpha: 0.5 }
  };

  const TRIGGER_TYPE_COLORS = {
    '边定时器': '#ff9800',
    'TTL超时': '#b71c1c',
    'Tick定时器': '#9e9e9e',
    '一次性': '#9c27b0',
    '循环': '#4caf50',
    '定时器': '#2196f3'
  };

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function formatCode(code) {
    if (!code) return '';
    code = String(code);
    if (code.length === 6 && /^\d+$/.test(code)) return code;
    if (code.length === 5 && /^\d+$/.test(code)) return code.padStart(6, '0');
    return code;
  }

  function formatCodes(codes) {
    if (!codes || !Array.isArray(codes) || codes.length === 0) return '';
    const list = codes.slice(0, 3).map(formatCode);
    if (codes.length > 3) list.push('+' + (codes.length - 3));
    return list.join(',');
  }

  function buildEventId(ev) {
    return (ev.category || 'system') + '-' + (ev.display_ts || ev.ts || 0) + '-' + (ev.event_type || '') + '-' + Math.random().toString(36).slice(2, 7);
  }

  function buildDetailsSummary(ev) {
    const d = ev.details || {};
    const parts = [];
    if (d.source && d.target) parts.push(escapeHtml(d.source) + '→' + escapeHtml(d.target));
    else if (d.source) parts.push('src:' + escapeHtml(d.source));
    else if (d.target) parts.push('tgt:' + escapeHtml(d.target));
    if (d.formula || d.formula_ref) parts.push('formula:' + escapeHtml(d.formula || d.formula_ref));
    if (d.period) parts.push('period:' + escapeHtml(d.period));
    if (d.interval != null) parts.push('interval:' + escapeHtml(d.interval));
    if (d.price != null && d.quantity != null) parts.push(escapeHtml(d.quantity) + '@' + escapeHtml(d.price));
    else if (d.price != null) parts.push('@' + escapeHtml(d.price));
    else if (d.quantity != null) parts.push('qty:' + escapeHtml(d.quantity));
    if (d.entered && Array.isArray(d.entered) && d.entered.length > 0) parts.push('in:' + formatCodes(d.entered));
    if (d.exited && Array.isArray(d.exited) && d.exited.length > 0) parts.push('out:' + formatCodes(d.exited));
    if (d.codes && Array.isArray(d.codes) && d.codes.length > 0) parts.push('codes:' + formatCodes(d.codes));
    if (d.message) parts.push(escapeHtml(String(d.message).slice(0, 80)));
    else if (d.reason) parts.push('reason:' + escapeHtml(d.reason));
    if (d.fire_at != null) {
      const fa = Number(d.fire_at);
      if (!isNaN(fa)) parts.push('fire_at:' + escapeHtml(String(d.fire_at)));
      else parts.push('fire_at:' + escapeHtml(String(d.fire_at)));
    }
    if (d.queue_position != null) parts.push('queue:' + escapeHtml(String(d.queue_position)));
    if (ev.edge_id) parts.push('edge:' + escapeHtml(ev.edge_id));
    if (ev.pool_id) parts.push('pool:' + escapeHtml(ev.pool_id));
    if (parts.length === 0) {
      const keys = Object.keys(d).slice(0, 2);
      keys.forEach(k => {
        const v = d[k];
        if (v != null && typeof v !== 'object') parts.push(escapeHtml(k) + ':' + escapeHtml(String(v).slice(0, 30)));
      });
    }
    return parts.join(' | ');
  }

  function getEventPosTs(ev) {
    return ev.category === 'ttl' ? (ev.fire_at_ms || ev.display_ts || 0) : (ev.display_ts || 0);
  }

  // 添加事件：统一入口，仅做展示缓冲
  function addEvent(ev) {
    if (!ev || !ev.event_type) return;
    const eventObj = {
      id: ev.id || buildEventId(ev),
      ts: ev.display_ts != null ? Number(ev.display_ts) : 0,
      category: ev.category || 'system',
      event_type: ev.event_type,
      code: ev.code || '',
      codes: ev.codes || null,
      pool_id: ev.pool_id || '',
      edge_id: ev.edge_id || '',
      details: ev.details || {},
      display_time: ev.display_time || '',
      display_time_ms: ev.display_time_ms || '',
      fire_at_ms: ev.fire_at_ms != null ? Number(ev.fire_at_ms) : null,
      display_fire_time_ms: ev.display_fire_time_ms || '',
      time_mode: ev.time_mode || 'wall',
      state: ev.state || 'triggered',
      raw: ev,
      pending: isPaused
    };

    if (isPaused) {
      pendingEvents.push(eventObj);
      updateEventCount();
      render();
      return;
    }

    events.push(eventObj);
    totalEventCount++;
    while (events.length > MAX_EVENTS) events.shift();
    updateEventCount();
    scheduleRender();
  }

  function flushPendingEvents() {
    if (!pendingEvents.length) return;
    const batch = pendingEvents.slice();
    pendingEvents = [];
    batch.forEach(function (ev) {
      ev.pending = false;
      events.push(ev);
      totalEventCount++;
    });
    while (events.length > MAX_EVENTS) events.shift();
    updateEventCount();
    render();
  }

  function clearEvents() {
    events = [];
    pendingEvents = [];
    timerQueue = [];
    totalEventCount = 0;
    activeCategory = null;
    selectedEventId = null;
    updateEventCount();
    render();
    renderDetailEmpty();
  }

  function updateEventCount() {
    if (!eventCountEl) return;
    const total = totalEventCount > 9999 ? '9999+' : totalEventCount;
    const pending = pendingEvents.length;
    eventCountEl.textContent = pending > 0 ? total + '/' + pending : total;
  }

  function getCurrentTime() {
    if (window.RuntimeState && typeof window.RuntimeState.getCurrentDisplayTime === 'function') {
      return window.RuntimeState.getCurrentDisplayTime();
    }
    return Date.now();
  }

  function scheduleRender() {
    render();
  }

  // ===== 渲染：分类矩阵 =====
  function ensureMatrixNowLine() {
    if (matrixNowLine) return matrixNowLine;
    if (!eventMatrix) return null;
    var line = document.createElement('div');
    line.className = 'etp-matrix-now-line';
    line.style.cssText = 'position:absolute;top:0;bottom:' + MATRIX_AXIS_HEIGHT + 'px;width:2px;background:#f44336;box-shadow:0 0 6px rgba(244,67,54,0.7),0 0 2px rgba(244,67,54,0.9);z-index:2;pointer-events:none;left:0;will-change:transform;';
    var label = document.createElement('div');
    label.style.cssText = 'position:absolute;bottom:-2px;left:50%;transform:translateX(-50%);font-size:8px;color:#f44336;white-space:nowrap;font-family:Consolas,monospace;font-weight:700;text-shadow:0 0 3px rgba(0,0,0,0.8);';
    label.textContent = 'NOW';
    line.appendChild(label);
    eventMatrix.style.position = 'relative';
    eventMatrix.appendChild(line);
    matrixNowLine = line;
    return line;
  }

  function updateMatrixNowLinePosition() {
    if (!matrixNowLine || currentViz !== 'matrix') return;
    if (!lastMatrixLayout) return;
    var l = lastMatrixLayout;
    var now = getCurrentTime();
    var ratio = Math.max(0, Math.min(1, (now - l.minTs) / l.timeSpan));
    var x = l.plotX + ratio * l.plotW;
    matrixNowLine.style.transform = 'translateX(' + x + 'px)';
  }

  function computeTimeWindow(all) {
    var now = getCurrentTime();
    var minTs = now - DEFAULT_TIME_WINDOW;
    var maxTs = now;
    all.forEach(function (ev) {
      var evTs = getEventPosTs(ev);
      if (evTs < minTs) minTs = evTs;
      if (evTs > maxTs) maxTs = evTs;
    });
    var timeSpan = maxTs - minTs;
    if (timeSpan < DEFAULT_TIME_WINDOW) {
      minTs = now - DEFAULT_TIME_WINDOW;
      timeSpan = DEFAULT_TIME_WINDOW;
    }
    if (timeSpan > MAX_TIME_WINDOW) {
      minTs = now - MAX_TIME_WINDOW;
      timeSpan = MAX_TIME_WINDOW;
    }
    return { minTs: minTs, timeSpan: timeSpan };
  }

  function drawLabelArea(ctx, opts) {
    var labelWidth = opts.labelWidth;
    var plotH = opts.plotH;
    var W = opts.W;
    ctx.fillStyle = 'rgba(0,0,0,0.25)';
    ctx.fillRect(0, 0, labelWidth - 1, plotH);
    ctx.strokeStyle = 'rgba(255,255,255,0.12)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(labelWidth - 1, 0);
    ctx.lineTo(labelWidth - 1, plotH);
    ctx.stroke();
  }

  function drawHorizontalGrid(ctx, opts) {
    var plotX = opts.plotX;
    var W = opts.W;
    var plotH = opts.plotH;
    var rowH = opts.rowH;
    var catCount = opts.catCount;
    var padRight = opts.padRight == null ? 8 : opts.padRight;
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.lineWidth = 1;
    for (var i = 0; i <= catCount; i++) {
      var gy = i * rowH;
      ctx.beginPath();
      ctx.moveTo(plotX, gy);
      ctx.lineTo(W - padRight, gy);
      ctx.stroke();
    }
  }

  function drawTimeAxis(ctx, opts) {
    var plotX = opts.plotX;
    var plotW = opts.plotW;
    var plotH = opts.plotH;
    var H = opts.H;
    var minTs = opts.minTs;
    var timeSpan = opts.timeSpan;
    var all = opts.all;
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 1;
    ctx.fillStyle = '#8080a0';
    ctx.font = '9px Consolas, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';

    // 以事件 display_ts 为刻度，使用后端已格式化的 display_time_ms
    var ticks = [];
    var seen = new Set();
    all.forEach(function (ev) {
      var t = getEventPosTs(ev);
      var key = Math.round(t / 1000) + '-' + ev.category;
      if (!seen.has(key) && t >= minTs && t <= minTs + timeSpan) {
        seen.add(key);
        ticks.push({ ts: t, label: ev.display_time_ms || ev.display_time || '' });
      }
    });
    ticks.sort(function (a, b) { return a.ts - b.ts; });
    // 避免过密：间隔至少 60px
    var minPx = 60;
    var filtered = [];
    var lastX = -Infinity;
    ticks.forEach(function (tick) {
      var x = plotX + ((tick.ts - minTs) / timeSpan) * plotW;
      if (x - lastX >= minPx) {
        filtered.push(tick);
        lastX = x;
      }
    });

    filtered.forEach(function (tick) {
      var x = plotX + ((tick.ts - minTs) / timeSpan) * plotW;
      ctx.beginPath();
      ctx.setLineDash([2, 3]);
      ctx.moveTo(x, 0);
      ctx.lineTo(x, plotH);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillText(tick.label, x, H - 4);
    });

    ctx.strokeStyle = 'rgba(255,255,255,0.2)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(plotX, plotH);
    ctx.lineTo(plotX + plotW, plotH);
    ctx.stroke();
  }

  function drawEventIcon(ctx, opts) {
    var ex = opts.ex;
    var ey = opts.ey;
    var cfg = opts.cfg;
    var state = opts.state || 'triggered';
    var iconSize = opts.iconSize || 14;
    var labelFont = opts.labelFont;
    var isHover = opts.isHover;
    var style = STATE_STYLE[state] || STATE_STYLE.triggered;
    if (style.borderStyle) {
      ctx.save();
      ctx.setLineDash(style.borderStyle);
      ctx.strokeStyle = style.borderColor;
      ctx.lineWidth = 1.2;
      ctx.globalAlpha = 0.8;
      ctx.strokeRect(ex - iconSize / 2 - 2, ey - iconSize / 2 - 2, iconSize + 4, iconSize + 4);
      ctx.setLineDash([]);
      ctx.restore();
    }
    ctx.save();
    ctx.globalAlpha = style.fillAlpha;
    ctx.shadowColor = style.glowColor || cfg.color;
    ctx.shadowBlur = style.glowSize || 4;
    ctx.font = '14px ' + labelFont;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(cfg.icon, ex, ey);
    ctx.shadowBlur = 0;
    ctx.restore();
    if (isHover) {
      ctx.save();
      ctx.strokeStyle = 'rgba(255,255,255,0.7)';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([3, 2]);
      ctx.beginPath();
      ctx.arc(ex, ey, iconSize / 2 + 4, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();
    }
  }

  function drawNowLine(ctx, opts) {
    var now = opts.now;
    var plotX = opts.plotX;
    var plotW = opts.plotW;
    var plotH = opts.plotH;
    var minTs = opts.minTs;
    var timeSpan = opts.timeSpan;
    var ratio = Math.max(0, Math.min(1, (now - minTs) / timeSpan));
    var x = plotX + ratio * plotW;
    ctx.strokeStyle = '#f44336';
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1.5;
    ctx.shadowColor = 'rgba(244,67,54,0.6)';
    ctx.shadowBlur = 4;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, plotH);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.shadowBlur = 0;
  }

  function renderMatrix() {
    if (!eventMatrixCanvas) return;
    eventMatrix.style.display = 'block';
    eventMatrixCanvas.style.display = 'block';
    eventScatterCanvas.style.display = 'none';
    if (categoryRowContainer) categoryRowContainer.style.display = '';
    ensureMatrixNowLine();

    var canvas = eventMatrixCanvas;
    var rect = eventMatrix.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    canvas.width = rect.width * window.devicePixelRatio;
    canvas.height = rect.height * window.devicePixelRatio;
    var ctx = canvas.getContext('2d');
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    var W = rect.width, H = rect.height;
    ctx.clearRect(0, 0, W, H);
    matrixHitRegions = [];

    var labelWidth = MATRIX_LABEL_WIDTH;
    var padRight = 8;
    var axisH = MATRIX_AXIS_HEIGHT;
    var plotX = labelWidth;
    var plotW = Math.max(W - labelWidth - padRight, 80);
    var plotH = H - axisH;
    var rowH = plotH / CATEGORIES.length;
    var catCount = CATEGORIES.length;

    var all = events.concat(pendingEvents).filter(function (ev) { return activeFilters.has(ev.category); });
    if (all.length === 0) {
      ctx.fillStyle = '#505070';
      ctx.font = '12px Segoe UI Emoji, Apple Color Emoji, Microsoft YaHei, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('暂无事件', W / 2, H / 2);
      if (matrixNowLine) matrixNowLine.style.display = 'none';
      lastMatrixLayout = null;
      var emptyByCat = {};
      CATEGORIES.forEach(function (c) { emptyByCat[c] = []; });
      updateCategoryRows({ rowH: rowH }, emptyByCat);
      return;
    }

    if (matrixNowLine) matrixNowLine.style.display = '';

    var now = getCurrentTime();
    var win = computeTimeWindow(all);
    var minTs = win.minTs;
    var timeSpan = win.timeSpan;

    lastMatrixLayout = { plotX: plotX, plotW: plotW, plotTop: 0, plotH: plotH, minTs: minTs, timeSpan: timeSpan, rowH: rowH, W: W, H: H, axisH: axisH, labelWidth: labelWidth };

    var byCat = {};
    CATEGORIES.forEach(function (c) { byCat[c] = []; });
    all.forEach(function (ev) { byCat[ev.category].push(ev); });
    updateCategoryRows(lastMatrixLayout, byCat);

    var labelFont = 'Segoe UI Emoji, Apple Color Emoji, Segoe UI, Microsoft YaHei, system-ui, sans-serif';
    drawLabelArea(ctx, { labelWidth: labelWidth, plotH: plotH, W: W });
    drawHorizontalGrid(ctx, { plotX: plotX, W: W, plotH: plotH, rowH: rowH, catCount: catCount, padRight: padRight });
    drawTimeAxis(ctx, { plotX: plotX, plotW: plotW, plotH: plotH, H: H, minTs: minTs, timeSpan: timeSpan, all: all });

    var buckets = {};
    all.forEach(function (ev) {
      var posTs = getEventPosTs(ev);
      var key = ev.category + '-' + posTs;
      if (!buckets[key]) buckets[key] = [];
      buckets[key].push(ev);
    });

    var iconSize = 14;
    var jitterStep = 10;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    all.forEach(function (ev) {
      var idx = CATEGORIES.indexOf(ev.category);
      if (idx < 0) return;
      var cfg = CATEGORY_CONFIG[ev.category];
      var posTs = getEventPosTs(ev);
      if (posTs < minTs || posTs > minTs + timeSpan) return;
      var ex = plotX + ((posTs - minTs) / timeSpan) * plotW;
      var baseY = idx * rowH + rowH / 2;
      var key = ev.category + '-' + posTs;
      var bucket = buckets[key];
      var bucketIdx = bucket ? bucket.indexOf(ev) : 0;
      var jitter = (bucketIdx - ((bucket ? bucket.length : 1) - 1) / 2) * jitterStep;
      var maxJitter = Math.max(0, (rowH - iconSize) / 2);
      if (jitter > maxJitter) jitter = maxJitter;
      if (jitter < -maxJitter) jitter = -maxJitter;
      var ey = baseY + jitter;
      var exMin = plotX + iconSize / 2;
      var exMax = plotX + plotW - iconSize / 2;
      if (ex < exMin) ex = exMin;
      if (ex > exMax) ex = exMax;
      var eyMin = idx * rowH;
      var eyMax = (idx + 1) * rowH;
      if (ey < eyMin) ey = eyMin;
      if (ey > eyMax) ey = eyMax;

      drawEventIcon(ctx, {
        ex: ex, ey: ey, cfg: cfg, state: ev.state,
        iconSize: iconSize, labelFont: labelFont,
        isHover: !!(matrixHoverEvent && matrixHoverEvent.id === ev.id)
      });

      matrixHitRegions.push({ x: ex - iconSize / 2, y: ey - iconSize / 2, w: iconSize, h: iconSize, ev: ev });
    });

    updateMatrixNowLinePosition();
  }

  function getMatrixEventAt(x, y) {
    var best = null, bestDist = Infinity;
    matrixHitRegions.forEach(function (r) {
      var cx = r.x + r.w / 2;
      var cy = r.y + r.h / 2;
      var d2 = (x - cx) * (x - cx) + (y - cy) * (y - cy);
      if (d2 < 196 && d2 < bestDist) {
        bestDist = d2;
        best = r;
      }
    });
    return best;
  }

  function initMatrixInteraction() {
    if (!eventMatrixCanvas) return;
    var canvas = eventMatrixCanvas;
    var tooltip = document.createElement('div');
    tooltip.className = 'etp-scatter-tooltip';
    tooltip.style.display = 'none';
    document.body.appendChild(tooltip);
    var moveRafId = null;
    var latestEvent = null;

    function processMouseMove(e) {
      moveRafId = null;
      var rect = canvas.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var y = e.clientY - rect.top;
      var hit = getMatrixEventAt(x, y);
      var ev = hit ? hit.ev : null;
      var prevHover = matrixHoverEvent;
      matrixHoverEvent = ev;
      if (ev) {
        var cfg = CATEGORY_CONFIG[ev.category];
        var stateLabel = ev.state === 'pending' ? ' [排队中]' : (ev.state === 'expired' ? ' [已过期]' : '');
        var stateColor = ev.state === 'pending' ? '#ffc107' : (ev.state === 'expired' ? '#f44336' : '#4caf50');
        var displayTime = ev.category === 'ttl' ? (ev.display_fire_time_ms || ev.display_time_ms) : ev.display_time_ms;
        tooltip.innerHTML =
          '<div class="tt-head"><span style="color:' + cfg.color + '">' + cfg.icon + '</span> ' +
          escapeHtml(ev.event_type) + '<span style="color:' + stateColor + '">' + stateLabel + '</span></div>' +
          '<div class="tt-row"><b>' + escapeHtml(displayTime) + '</b></div>' +
          '<div class="tt-row">类型: ' + escapeHtml(ev.event_type) + ' | ' + escapeHtml(ev.code || formatCodes(ev.codes) || '-') + '</div>' +
          '<div class="tt-detail">' + escapeHtml(buildDetailsSummary(ev)) + '</div>';
        tooltip.style.display = 'block';
        tooltip.style.left = (e.clientX + 12) + 'px';
        tooltip.style.top = (e.clientY + 12) + 'px';
        canvas.style.cursor = 'pointer';
      } else {
        tooltip.style.display = 'none';
        canvas.style.cursor = 'default';
      }
      if (prevHover !== ev && currentViz === 'matrix') {
        renderMatrix();
      }
    }

    canvas.addEventListener('mousemove', function (e) {
      latestEvent = e;
      if (!moveRafId) {
        moveRafId = requestAnimationFrame(function () {
          processMouseMove(latestEvent);
        });
      }
    });

    canvas.addEventListener('mouseleave', function () {
      tooltip.style.display = 'none';
      if (matrixHoverEvent) {
        matrixHoverEvent = null;
        if (currentViz === 'matrix') renderMatrix();
      }
    });

    canvas.addEventListener('click', function (e) {
      var rect = canvas.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var y = e.clientY - rect.top;
      var hit = getMatrixEventAt(x, y);
      if (hit) {
        activeCategory = hit.ev.category;
        selectedEventId = hit.ev.id;
        renderDetailForEvent(hit.ev);
      } else {
        var ch = canvas.height / window.devicePixelRatio;
        var plotH = lastMatrixLayout ? lastMatrixLayout.plotH : (ch - MATRIX_AXIS_HEIGHT);
        var cfg = getCategoryRowConfig();
        var rowH = plotH / cfg.rows.length;
        var idx = Math.floor(y / rowH);
        if (idx >= 0 && idx < cfg.rows.length) {
          activeCategory = cfg.rows[idx].cat;
          selectedEventId = null;
          if (activeCategory === 'all') renderDetailForAll();
          else renderDetailForCategory(activeCategory);
        }
      }
      render();
    });
  }

  // ===== 分类行 DOM =====
  let currentCategoryRowConfig = null;

  function getCategoryRowConfig() {
    if (currentViz === 'scatter' && scatterLayout === 'merged') {
      return { rows: [{ cat: 'all', label: '事件', icon: '📊', color: '#e0e0ff' }], single: true };
    }
    return { rows: CATEGORIES.map(function (cat) { return { cat: cat, ...CATEGORY_CONFIG[cat] }; }), single: false };
  }

  function ensureCategoryRows() {
    const config = getCategoryRowConfig();
    const same = currentCategoryRowConfig &&
      currentCategoryRowConfig.single === config.single &&
      currentCategoryRowConfig.rows.length === config.rows.length &&
      currentCategoryRowConfig.rows.every(function (r, i) { return r.cat === config.rows[i].cat; });
    if (same && categoryRowContainer) return config;
    currentCategoryRowConfig = config;

    if (categoryRowContainer) {
      categoryRowContainer.remove();
      categoryRowContainer = null;
    }
    var parent = (currentViz === 'matrix' && eventMatrix) ? eventMatrix : eventVizBody;
    if (!parent) return config;

    categoryRowContainer = document.createElement('div');
    categoryRowContainer.className = 'event-category-rows';
    categoryRowContainer.style.cssText = 'position:absolute;top:0;left:0;width:' + MATRIX_LABEL_WIDTH + 'px;pointer-events:none;z-index:5;';

    config.rows.forEach(function (rowCfg) {
      const cat = rowCfg.cat;
      const row = document.createElement('div');
      row.className = 'event-category-row';
      row.setAttribute('role', 'button');
      row.setAttribute('tabindex', '0');
      row.setAttribute('aria-label', rowCfg.label + (cat === 'all' ? '' : ' 分类'));
      row.dataset.category = cat;
      row.style.cssText = 'position:absolute;left:0;width:100%;cursor:pointer;pointer-events:auto;display:flex;align-items:center;gap:4px;padding:0 4px;box-sizing:border-box;overflow:hidden;';

      if (cat !== 'all') {
        var toggleSpan = document.createElement('span');
        toggleSpan.className = 'ecr-toggle';
        toggleSpan.textContent = '☑';
        toggleSpan.title = '点击切换该分类事件显隐';
        toggleSpan.style.cssText = 'font-size:12px;line-height:1;cursor:pointer;padding:0 2px;color:#4caf50;flex-shrink:0;';
        toggleSpan.addEventListener('click', function (e) {
          e.stopPropagation();
          if (activeFilters.has(cat)) {
            activeFilters.delete(cat);
            toggleSpan.textContent = '☐';
            toggleSpan.style.color = '#8080a0';
          } else {
            activeFilters.add(cat);
            toggleSpan.textContent = '☑';
            toggleSpan.style.color = '#4caf50';
          }
          activeCategory = cat;
          selectedEventId = null;
          renderDetailForCategory(cat);
          render();
        });
        row.appendChild(toggleSpan);
      }

      var iconSpan = document.createElement('span');
      iconSpan.className = 'ecr-icon';
      iconSpan.textContent = rowCfg.icon;
      iconSpan.style.cssText = 'font-size:12px;line-height:1;flex-shrink:0;';
      var labelSpan = document.createElement('span');
      labelSpan.className = 'ecr-label';
      labelSpan.textContent = rowCfg.label;
      labelSpan.style.cssText = 'font-size:11px;font-weight:bold;color:' + rowCfg.color + ';flex-shrink:0;';
      var countSpan = document.createElement('span');
      countSpan.className = 'ecr-count';
      countSpan.textContent = '0';
      countSpan.style.cssText = 'font-size:10px;color:rgba(255,255,255,0.5);font-family:Consolas,monospace;flex-shrink:0;';
      row.appendChild(iconSpan);
      row.appendChild(labelSpan);
      row.appendChild(countSpan);

      row.addEventListener('click', function () {
        activeCategory = cat;
        selectedEventId = null;
        if (cat === 'all') renderDetailForAll();
        else renderDetailForCategory(cat);
        render();
      });
      row.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
          e.preventDefault();
          activeCategory = cat;
          selectedEventId = null;
          if (cat === 'all') renderDetailForAll();
          else renderDetailForCategory(cat);
          render();
        }
      });
      categoryRowContainer.appendChild(row);
    });
    parent.appendChild(categoryRowContainer);
    return config;
  }

  function updateCategoryRows(layout, byCat) {
    if (!eventVizBody) return;
    const config = ensureCategoryRows();
    if (!categoryRowContainer || !layout) return;
    categoryRowContainer.style.display = '';
    var rows = categoryRowContainer.children;
    var rowH = layout.rowH;
    var cfgRows = config.rows;
    for (var i = 0; i < cfgRows.length && i < rows.length; i++) {
      var row = rows[i];
      row.style.top = (i * rowH) + 'px';
      row.style.height = rowH + 'px';
      var cat = cfgRows[i].cat;
      var countEl = row.querySelector('.ecr-count');
      if (countEl) {
        var count = 0;
        if (cat === 'all') {
          CATEGORIES.forEach(function (c) { count += (byCat && byCat[c]) ? byCat[c].length : 0; });
        } else {
          count = (byCat && byCat[cat]) ? byCat[cat].length : 0;
        }
        countEl.textContent = String(count);
      }
      var toggleEl = row.querySelector('.ecr-toggle');
      if (toggleEl) {
        if (activeFilters.has(cat)) {
          toggleEl.textContent = '☑';
          toggleEl.style.color = '#4caf50';
        } else {
          toggleEl.textContent = '☐';
          toggleEl.style.color = '#8080a0';
        }
      }
      if (activeCategory === cat || (cat === 'all' && activeCategory != null)) {
        row.style.background = 'rgba(255,255,255,0.08)';
      } else {
        row.style.background = 'transparent';
      }
    }
  }

  // ===== 散点聚合：仅按时间窗口聚合，状态信任后端 =====
  const SCATTER_AGGREGATE_WINDOW = 500;
  const SCATTER_TOOLTIP_DELAY = 100;

  function aggregateScatterEvents(allEvents, minTs, span, layout) {
    const windowMs = SCATTER_AGGREGATE_WINDOW;
    const sorted = allEvents.slice().sort((a, b) => getEventPosTs(a) - getEventPosTs(b));
    const clusters = [];

    sorted.forEach(ev => {
      const posTs = getEventPosTs(ev);
      if (posTs < minTs || posTs > minTs + span) return;
      let found = null;
      const cat = ev.category;
      for (let i = clusters.length - 1; i >= 0; i--) {
        const c = clusters[i];
        if (layout === 'category' && c.primaryCategory !== cat) continue;
        if (Math.abs(c.centerTs - posTs) <= windowMs) {
          found = c;
          break;
        }
      }
      if (found) {
        found.events.push(ev);
        found.centerTs = found.events.reduce((sum, e) => sum + getEventPosTs(e), 0) / found.events.length;
      } else {
        clusters.push({ centerTs: posTs, events: [ev], primaryCategory: cat });
      }
    });

    clusters.forEach(cluster => {
      const catCounts = {};
      const codes = new Set();
      cluster.events.forEach(ev => {
        const cat = ev.category;
        catCounts[cat] = (catCounts[cat] || 0) + 1;
        if (ev.code) codes.add(formatCode(ev.code));
        if (ev.codes && Array.isArray(ev.codes)) ev.codes.forEach(c => codes.add(formatCode(c)));
      });
      cluster.catCounts = catCounts;
      cluster.codes = Array.from(codes);
      const states = cluster.events.map(ev => ev.state);
      cluster.state = states.includes('pending') ? 'pending' : (states.includes('expired') ? 'expired' : 'triggered');
    });

    return clusters.sort((a, b) => a.centerTs - b.centerTs);
  }

  // ===== 渲染：散点分布 =====
  function renderScatter() {
    if (!eventScatterCanvas) return;
    eventMatrix.style.display = 'none';
    eventMatrixCanvas.style.display = 'none';
    eventScatterCanvas.style.display = 'block';

    const canvas = eventScatterCanvas;
    const rect = canvas.parentElement.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    canvas.width = rect.width * window.devicePixelRatio;
    canvas.height = rect.height * window.devicePixelRatio;
    const ctx = canvas.getContext('2d');
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    const W = rect.width, H = rect.height;
    ctx.clearRect(0, 0, W, H);
    scatterHitRegions = [];

    const labelPadLeft = MATRIX_LABEL_WIDTH;
    const axisH = MATRIX_AXIS_HEIGHT;
    const plotW = Math.max(W - labelPadLeft - 8, 80);
    const plotH = H - axisH;

    const catCount = CATEGORIES.length;
    const rowH = plotH / catCount;
    lastScatterLayout = { minTs: 0, span: DEFAULT_TIME_WINDOW, W: W, H: H, rowH: rowH, catCount: catCount };

    if (events.length === 0 && pendingEvents.length === 0) {
      ctx.fillStyle = '#505070';
      ctx.font = '12px Segoe UI Emoji, Apple Color Emoji, Microsoft YaHei, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('暂无事件', W / 2, H / 2);
      var emptyByCat = {};
      CATEGORIES.forEach(function (c) { emptyByCat[c] = []; });
      updateCategoryRows(lastScatterLayout, emptyByCat);
      return;
    }

    const all = events.concat(pendingEvents).filter(ev => activeFilters.has(ev.category));
    if (all.length === 0) {
      var emptyByCat2 = {};
      CATEGORIES.forEach(function (c) { emptyByCat2[c] = []; });
      updateCategoryRows(lastScatterLayout, emptyByCat2);
      return;
    }

    const now = getCurrentTime();
    const win = computeTimeWindow(all);
    const minTs = win.minTs;
    const span = win.timeSpan;
    lastScatterLayout = { minTs: minTs, span: span, W: W, H: H, rowH: rowH, catCount: catCount };

    const catIndex = {};
    CATEGORIES.forEach((c, i) => catIndex[c] = i);

    const labelFont = 'Segoe UI Emoji, Apple Color Emoji, Segoe UI, Microsoft YaHei, system-ui, sans-serif';

    const byCat = {};
    CATEGORIES.forEach(c => byCat[c] = []);
    all.forEach(ev => { byCat[ev.category].push(ev); });
    updateCategoryRows(lastScatterLayout, byCat);

    drawLabelArea(ctx, { labelWidth: labelPadLeft, plotH: plotH, W: W });
    drawHorizontalGrid(ctx, { plotX: labelPadLeft, W: W, plotH: plotH, rowH: rowH, catCount: catCount, padRight: 8 });
    drawTimeAxis(ctx, { plotX: labelPadLeft, plotW: plotW, plotH: plotH, H: H, minTs: minTs, timeSpan: span, all: all });

    const clusters = aggregateScatterEvents(all, minTs, span, scatterLayout);
    const iconSize = 14;
    const badgeRadius = 8;

    clusters.forEach(cluster => {
      const cat = cluster.primaryCategory;
      const cfg = CATEGORY_CONFIG[cat];
      const idx = catIndex[cat];
      if (idx < 0) return;
      const cx = labelPadLeft + ((cluster.centerTs - minTs) / span) * plotW;
      const cy = scatterLayout === 'category' ? idx * rowH + rowH / 2 : plotH / 2;
      const isCluster = cluster.events.length > 1;
      const hasSelected = cluster.events.some(e => e.id === selectedEventId);

      drawEventIcon(ctx, { ex: cx, ey: cy, cfg: cfg, state: cluster.state, iconSize: iconSize, labelFont: labelFont, isHover: hasSelected });

      if (isCluster) {
        const badgeX = cx + iconSize / 2 - 2;
        const badgeY = cy - iconSize / 2 + 2;
        ctx.beginPath();
        ctx.arc(badgeX, badgeY, badgeRadius, 0, Math.PI * 2);
        ctx.fillStyle = '#ff5722';
        ctx.fill();
        ctx.strokeStyle = 'rgba(0,0,0,0.5)';
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.fillStyle = '#fff';
        ctx.font = 'bold 9px Consolas';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const countText = cluster.events.length > 99 ? '99+' : String(cluster.events.length);
        ctx.fillText(countText, badgeX, badgeY);
      }

      scatterHitRegions.push({ cx: cx, cy: cy, cluster: cluster });
    });

    drawNowLine(ctx, { now: now, plotX: labelPadLeft, plotW: plotW, plotH: plotH, minTs: minTs, timeSpan: span });
  }

  function getScatterClusterAt(x, y) {
    let best = null, bestDist = Infinity;
    scatterHitRegions.forEach(r => {
      const dx = x - r.cx;
      const dy = y - r.cy;
      const d2 = dx * dx + dy * dy;
      if (d2 < 256 && d2 < bestDist) {
        bestDist = d2;
        best = r;
      }
    });
    return best;
  }

  function initScatterInteraction() {
    if (!eventScatterCanvas) return;
    const canvas = eventScatterCanvas;
    const tooltip = document.createElement('div');
    tooltip.className = 'etp-scatter-tooltip';
    tooltip.style.display = 'none';
    document.body.appendChild(tooltip);

    let moveRafId = null;
    let latestEvent = null;
    let tooltipTimer = null;
    let currentHit = null;

    function buildTooltipContent(hit) {
      const cluster = hit.cluster;
      const events = cluster.events;
      const isMulti = events.length > 1;
      const centerEv = events.reduce((best, ev) => {
        const d = Math.abs(getEventPosTs(ev) - cluster.centerTs);
        return d < best.d ? { ev: ev, d: d } : best;
      }, { ev: events[0], d: Infinity }).ev;

      let html = '<div class="tt-head">';
      html += '<span style="color:#ff9800">⏱</span> <b>' + escapeHtml(centerEv.display_time_ms || centerEv.display_time || '') + '</b>';
      if (isMulti) html += ' <span style="color:#ff5722">(' + events.length + '个事件)</span>';
      html += '</div>';

      html += '<div class="tt-row" style="margin-top:4px;">';
      const catKeys = Object.keys(cluster.catCounts).sort((a, b) => cluster.catCounts[b] - cluster.catCounts[a]);
      catKeys.forEach(cat => {
        const cfg = CATEGORY_CONFIG[cat];
        html += '<span style="margin-right:8px;"><span style="color:' + cfg.color + '">' + cfg.icon + '</span>' + cluster.catCounts[cat] + '</span>';
      });
      html += '</div>';

      if (cluster.codes.length > 0) {
        html += '<div class="tt-row" style="font-size:10px;color:#9090b0;">';
        const displayCodes = cluster.codes.slice(0, 5);
        html += '📋 ' + displayCodes.map(escapeHtml).join(', ');
        if (cluster.codes.length > 5) html += ' <span style="color:#ff9800">+' + (cluster.codes.length - 5) + '</span>';
        html += '</div>';
      }

      if (!isMulti) {
        const ev = events[0];
        html += '<div class="tt-detail" style="margin-top:4px;">' + escapeHtml(buildDetailsSummary(ev)) + '</div>';
      }

      return html;
    }

    function showTooltip(e, hit) {
      tooltip.innerHTML = buildTooltipContent(hit);
      tooltip.style.display = 'block';
      let tx = e.clientX + 14;
      let ty = e.clientY + 14;
      if (tx + 220 > window.innerWidth) tx = e.clientX - 230;
      if (ty + 150 > window.innerHeight) ty = e.clientY - 160;
      tooltip.style.left = tx + 'px';
      tooltip.style.top = ty + 'px';
      canvas.style.cursor = 'pointer';
    }

    function hideTooltip() {
      tooltip.style.display = 'none';
      canvas.style.cursor = 'default';
      currentHit = null;
      if (tooltipTimer) {
        clearTimeout(tooltipTimer);
        tooltipTimer = null;
      }
    }

    function processMouseMove(e) {
      moveRafId = null;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const hit = getScatterClusterAt(x, y);

      if (hit) {
        if (hit !== currentHit) {
          currentHit = hit;
          if (tooltipTimer) clearTimeout(tooltipTimer);
          tooltipTimer = setTimeout(function () {
            tooltipTimer = null;
            if (currentHit === hit) {
              showTooltip(e, hit);
            }
          }, SCATTER_TOOLTIP_DELAY);
        } else if (tooltip.style.display === 'block') {
          let tx = e.clientX + 14;
          let ty = e.clientY + 14;
          if (tx + 220 > window.innerWidth) tx = e.clientX - 230;
          if (ty + 150 > window.innerHeight) ty = e.clientY - 160;
          tooltip.style.left = tx + 'px';
          tooltip.style.top = ty + 'px';
        }
        canvas.style.cursor = 'pointer';
      } else {
        hideTooltip();
      }
    }

    canvas.addEventListener('mousemove', function (e) {
      latestEvent = e;
      if (!moveRafId) {
        moveRafId = requestAnimationFrame(function () {
          processMouseMove(latestEvent);
        });
      }
    });

    canvas.addEventListener('mouseleave', function () {
      hideTooltip();
    });

    canvas.addEventListener('click', function (e) {
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const hit = getScatterClusterAt(x, y);
      if (hit) {
        const cluster = hit.cluster;
        isDetailCollapsed = false;
        eventDetail.classList.remove('collapsed');
        if (eventDetailToggleSym) eventDetailToggleSym.textContent = '▼';
        savePanelState();

        if (cluster.events.length === 1) {
          const ev = cluster.events[0];
          activeCategory = ev.category;
          selectedEventId = ev.id;
          renderDetailForEvent(ev);
        } else {
          selectedEventId = cluster.events[0].id;
          activeCategory = cluster.primaryCategory;
          renderDetailForCluster(cluster);
        }
        render();
      }
    });
  }

  // ===== 渲染：定时器队列 =====
  function renderTimerQueue() {
    if (!timerQueueBody || !timerQueueCanvas) return;
    timerQueueCount.textContent = timerQueue.length;
    renderTimerQueueTimeline();

    timerQueueBody.innerHTML = '';
    if (timerQueue.length === 0) {
      timerQueueBody.innerHTML = '<div class="etp-timer-item"><span class="ti-detail">定时器队列为空</span></div>';
      return;
    }

    var headerRow = document.createElement('div');
    headerRow.className = 'etp-timer-item etp-timer-header';
    headerRow.style.cssText = 'font-weight:600;color:#c0c0e0;background:rgba(255,255,255,0.04);border-bottom:1px solid #3a3a5a;cursor:default;';
    headerRow.innerHTML =
      '<span class="ti-time">触发时间</span>' +
      '<span class="ti-trigger">触发类型</span>' +
      '<span class="ti-type">事件类型</span>' +
      '<span class="ti-code">股票</span>' +
      '<span class="ti-detail">详情</span>' +
      '<span class="ti-remain">剩余</span>';
    timerQueueBody.appendChild(headerRow);

    const sortedItems = timerQueue.slice().sort((a, b) => {
      const fa = a.ev.fire_at_ms != null ? a.ev.fire_at_ms : a.ev.display_ts;
      const fb = b.ev.fire_at_ms != null ? b.ev.fire_at_ms : b.ev.display_ts;
      return fa - fb;
    });

    sortedItems.forEach((timerItem) => {
      const ev = timerItem.ev;
      const state = ev.state || 'waiting';
      const style = TIMER_LIST_STYLE[state] || TIMER_LIST_STYLE.waiting;
      const cfg = CATEGORY_CONFIG[ev.category] || CATEGORY_CONFIG.ttl;
      const triggerType = ev.trigger_type || '定时器';
      const triggerColor = TRIGGER_TYPE_COLORS[triggerType] || '#c0c0e0';

      const item = document.createElement('div');
      item.className = 'etp-timer-item' + (selectedEventId === ev.id ? ' selected' : '');
      item.setAttribute('role', 'button');
      item.setAttribute('tabindex', '0');
      item.style.backgroundColor = style.listBg;
      item.style.opacity = style.listOpacity;
      item.style.textDecoration = style.textDecoration;

      let timeDisplay;
      let statusColor;
      if (state === 'waiting') {
        timeDisplay = ev.remaining_text || '';
        statusColor = cfg.color;
      } else {
        timeDisplay = style.label;
        statusColor = state === 'expired' ? '#f44336' : '#9e9e9e';
      }

      const fireTimeDisplay = ev.display_fire_time_ms || ev.display_time_ms || '';
      item.innerHTML =
        '<span class="ti-time">' + escapeHtml(fireTimeDisplay) + '</span>' +
        '<span class="ti-trigger" style="color:' + triggerColor + ';font-weight:600;">' + escapeHtml(triggerType) + '</span>' +
        '<span class="ti-type" style="color:' + cfg.color + '" title="' + escapeHtml(ev.event_type) + '">' + escapeHtml(ev.event_type) + '</span>' +
        '<span class="ti-code">' + escapeHtml(formatCode(ev.code) || '-') + '</span>' +
        '<span class="ti-detail">' + escapeHtml(buildDetailsSummary(ev)) + '</span>' +
        '<span class="ti-remain" style="color:' + statusColor + ';font-weight:600;">' + escapeHtml(timeDisplay) + '</span>';

      item.addEventListener('click', function () {
        activeCategory = 'ttl';
        selectedEventId = ev.id;
        isDetailCollapsed = false;
        if (eventDetail) eventDetail.classList.remove('collapsed');
        if (eventDetailToggleSym) eventDetailToggleSym.textContent = '▼';
        renderDetailForEvent(ev);
        render();
      });

      timerQueueBody.appendChild(item);
    });
  }

  function renderTimerQueueTimeline() {
    const canvas = timerQueueCanvas;
    const rect = canvas.parentElement.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    canvas.width = rect.width * window.devicePixelRatio;
    canvas.height = 52 * window.devicePixelRatio;
    const ctx = canvas.getContext('2d');
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    const W = rect.width, H = 52;
    ctx.clearRect(0, 0, W, H);
    timerHitRegions = [];

    if (timerQueue.length === 0) {
      ctx.fillStyle = '#505070';
      ctx.font = '11px Microsoft YaHei';
      ctx.textAlign = 'center';
      ctx.fillText('暂无排队事件', W / 2, H / 2 + 4);
      return;
    }

    const now = getCurrentTime();
    const items = timerQueue.slice().sort((a, b) => {
      const fa = a.ev.fire_at_ms != null ? a.ev.fire_at_ms : a.ev.display_ts;
      const fb = b.ev.fire_at_ms != null ? b.ev.fire_at_ms : b.ev.display_ts;
      return fa - fb;
    }).map(t => {
      const ev = t.ev;
      const fireAt = ev.fire_at_ms != null ? ev.fire_at_ms : ev.display_ts;
      return { ev: ev, fireAt: fireAt, state: ev.state || 'waiting' };
    });

    let minTs = now - 5000;
    let maxTs = now + 60000;
    items.forEach(it => {
      if (it.fireAt < minTs) minTs = it.fireAt;
      if (it.fireAt > maxTs) maxTs = it.fireAt;
    });
    const span = Math.max(maxTs - minTs, 30000);

    const plotX = 10;
    const plotW = W - 20;
    const axisY = H - 16;
    const markerY = H / 2 - 2;
    const markerRadius = 6;

    const timeToX = function (ts) {
      const ratio = Math.max(0, Math.min(1, (ts - minTs) / span));
      return plotX + ratio * plotW;
    };

    ctx.strokeStyle = 'rgba(255,255,255,0.12)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(plotX, axisY);
    ctx.lineTo(plotX + plotW, axisY);
    ctx.stroke();

    ctx.fillStyle = '#606080';
    ctx.font = '9px Consolas';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    const pad2 = n => String(n).padStart(2, '0');
    for (let i = 0; i <= 4; i++) {
      const x = plotX + plotW * i / 4;
      const t = minTs + span * i / 4;
      const totalSec = Math.floor(t / 1000);
      const m = Math.floor(totalSec / 60) % 60;
      const s = totalSec % 60;
      ctx.fillText(pad2(m) + ':' + pad2(s), x, H - 4);
    }

    const nowX = timeToX(now);

    items.forEach((it) => {
      const x = timeToX(it.fireAt);
      const cfg = CATEGORY_CONFIG[it.ev.category] || CATEGORY_CONFIG.ttl;
      const style = TIMER_LIST_STYLE[it.state] || TIMER_LIST_STYLE.waiting;

      ctx.save();
      ctx.globalAlpha = style.fillAlpha;

      if (it.state === 'expired' || style.fillColor === null) {
        ctx.beginPath();
        ctx.arc(x, markerY, markerRadius, 0, Math.PI * 2);
        ctx.strokeStyle = style.strokeColor || cfg.color;
        ctx.lineWidth = 2;
        ctx.stroke();
        if (style.lineStyle) {
          ctx.setLineDash(style.lineStyle);
          ctx.beginPath();
          ctx.arc(x, markerY, markerRadius + 3, 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      } else {
        ctx.beginPath();
        ctx.arc(x, markerY, markerRadius, 0, Math.PI * 2);
        const fillColor = style.fillColor || cfg.color;
        ctx.fillStyle = fillColor;
        ctx.shadowColor = fillColor;
        ctx.shadowBlur = it.state === 'waiting' ? 5 : 3;
        ctx.fill();
        ctx.shadowBlur = 0;
        if (style.lineStyle) {
          ctx.strokeStyle = style.strokeColor || fillColor;
          ctx.lineWidth = 1.5;
          ctx.setLineDash(style.lineStyle);
          ctx.beginPath();
          ctx.arc(x, markerY, markerRadius + 3, 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }

      ctx.restore();

      timerHitRegions.push({ x: x - markerRadius - 4, y: markerY - markerRadius - 4, w: markerRadius * 2 + 8, h: markerRadius * 2 + 8, ev: it.ev });
    });

    ctx.save();
    ctx.strokeStyle = '#4caf50';
    ctx.lineWidth = 2;
    ctx.shadowColor = 'rgba(76,175,80,0.7)';
    ctx.shadowBlur = 6;
    ctx.beginPath();
    ctx.moveTo(nowX, 2);
    ctx.lineTo(nowX, axisY);
    ctx.stroke();
    ctx.shadowBlur = 0;

    ctx.fillStyle = '#4caf50';
    ctx.beginPath();
    ctx.moveTo(nowX, 0);
    ctx.lineTo(nowX - 4, 6);
    ctx.lineTo(nowX + 4, 6);
    ctx.closePath();
    ctx.fill();

    ctx.fillStyle = '#4caf50';
    ctx.font = 'bold 8px Consolas';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText('NOW', nowX, 8);
    ctx.restore();
  }

  function getTimerEventAt(x, y) {
    let best = null, bestDist = Infinity;
    timerHitRegions.forEach(r => {
      const cx = r.x + r.w / 2;
      const cy = r.y + r.h / 2;
      const d2 = (x - cx) * (x - cx) + (y - cy) * (y - cy);
      if (d2 < 144 && d2 < bestDist) {
        bestDist = d2;
        best = r.ev;
      }
    });
    return best;
  }

  function initTimerQueueInteraction() {
    if (!timerQueueCanvas) return;
    const canvas = timerQueueCanvas;
    const tooltip = document.createElement('div');
    tooltip.className = 'etp-scatter-tooltip';
    tooltip.style.display = 'none';
    document.body.appendChild(tooltip);

    canvas.addEventListener('mousemove', function (e) {
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const ev = getTimerEventAt(x, y);
      if (ev) {
        const fireTime = ev.display_fire_time_ms || ev.display_time_ms || '';
        tooltip.innerHTML =
          '<div class="tt-head"><span style="color:#b71c1c">⏰</span> ' + escapeHtml(ev.event_type) + '</div>' +
          '<div class="tt-row">fire_at: ' + escapeHtml(fireTime) + '</div>' +
          '<div class="tt-row">' + escapeHtml(ev.code || formatCodes(ev.codes) || '-') + '</div>' +
          '<div class="tt-detail">' + escapeHtml(buildDetailsSummary(ev)) + '</div>';
        tooltip.style.display = 'block';
        tooltip.style.left = (e.clientX + 12) + 'px';
        tooltip.style.top = (e.clientY + 12) + 'px';
        canvas.style.cursor = 'pointer';
      } else {
        tooltip.style.display = 'none';
        canvas.style.cursor = 'default';
      }
    });

    canvas.addEventListener('mouseleave', function () {
      tooltip.style.display = 'none';
    });

    canvas.addEventListener('click', function (e) {
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const ev = getTimerEventAt(x, y);
      if (ev) {
        activeCategory = 'ttl';
        selectedEventId = ev.id;
        renderDetailForEvent(ev);
        render();
      }
    });
  }

  // ===== 渲染：详情区域 =====
  function renderDetailEmpty() {
    if (!eventDetailBody) return;
    eventDetailTitle.textContent = '事件记录';
    eventDetailBody.innerHTML = '<div class="etp-detail-empty">点击上方图标或分类查看事件详情</div>';
  }

  function renderDetailForCategory(cat) {
    if (!eventDetailBody) return;
    if (eventDetail) {
      eventDetail.classList.remove('collapsed');
      isDetailCollapsed = false;
      if (eventDetailToggleSym) eventDetailToggleSym.textContent = '▼';
      savePanelState();
    }
    const cfg = CATEGORY_CONFIG[cat];
    eventDetailTitle.textContent = cfg.icon + ' ' + cfg.label + ' 事件记录';
    eventDetailBody.innerHTML = '';

    const list = events.filter(ev => ev.category === cat);
    const pendingList = pendingEvents.filter(ev => ev.category === cat);
    if (list.length === 0 && pendingList.length === 0) {
      eventDetailBody.innerHTML = '<div class="etp-detail-empty">该分类暂无事件</div>';
      return;
    }

    pendingList.slice().reverse().forEach(ev => eventDetailBody.appendChild(createDetailItem(ev, true)));
    list.slice().reverse().forEach(ev => eventDetailBody.appendChild(createDetailItem(ev, false)));
  }

  function renderDetailForAll() {
    if (!eventDetailBody) return;
    if (eventDetail) {
      eventDetail.classList.remove('collapsed');
      isDetailCollapsed = false;
      if (eventDetailToggleSym) eventDetailToggleSym.textContent = '▼';
      savePanelState();
    }
    eventDetailTitle.textContent = '📊 全部事件记录';
    eventDetailBody.innerHTML = '';

    const list = events.slice();
    const pendingList = pendingEvents.slice();
    if (list.length === 0 && pendingList.length === 0) {
      eventDetailBody.innerHTML = '<div class="etp-detail-empty">暂无事件</div>';
      return;
    }
    pendingList.slice().reverse().forEach(ev => eventDetailBody.appendChild(createDetailItem(ev, true)));
    list.slice().reverse().forEach(ev => eventDetailBody.appendChild(createDetailItem(ev, false)));
  }

  function renderDetailForEvent(ev) {
    if (!eventDetailBody) return;
    if (eventDetail) {
      eventDetail.classList.remove('collapsed');
      isDetailCollapsed = false;
      if (eventDetailToggleSym) eventDetailToggleSym.textContent = '▼';
      savePanelState();
    }
    const cfg = CATEGORY_CONFIG[ev.category];
    eventDetailTitle.textContent = cfg.icon + ' ' + ev.event_type;
    eventDetailBody.innerHTML = '';
    eventDetailBody.appendChild(createDetailItem(ev, ev.pending));
    const list = events.filter(e => e.category === ev.category && e.id !== ev.id).slice(-20);
    if (list.length > 0) {
      const sep = document.createElement('div');
      sep.style.cssText = 'padding:6px 10px;color:#505070;font-size:10px;border-top:1px solid #2a2a4a;margin-top:4px;';
      sep.textContent = '同分类其他事件';
      eventDetailBody.appendChild(sep);
      list.slice().reverse().forEach(e => eventDetailBody.appendChild(createDetailItem(e, false)));
    }
  }

  function renderDetailForCluster(cluster) {
    if (!eventDetailBody) return;
    if (eventDetail) {
      eventDetail.classList.remove('collapsed');
      isDetailCollapsed = false;
      if (eventDetailToggleSym) eventDetailToggleSym.textContent = '▼';
      savePanelState();
    }
    const centerEv = cluster.events.reduce((best, ev) => {
      const d = Math.abs(getEventPosTs(ev) - cluster.centerTs);
      return d < best.d ? { ev: ev, d: d } : best;
    }, { ev: cluster.events[0], d: Infinity }).ev;
    eventDetailTitle.textContent = '📌 ' + (centerEv.display_time_ms || centerEv.display_time || '') + ' (' + cluster.events.length + '个事件)';
    eventDetailBody.innerHTML = '';
    const evs = cluster.events.slice().sort((a, b) => a.ts - b.ts);
    evs.forEach((ev, i) => {
      const d = createDetailItem(ev, false);
      const idxSpan = document.createElement('span');
      idxSpan.className = 'ev-idx';
      idxSpan.style.cssText = 'color:#505070;font-size:9px;margin-right:4px;';
      idxSpan.textContent = '#' + (i + 1);
      d.insertBefore(idxSpan, d.children[1]);
      eventDetailBody.appendChild(d);
    });
  }

  function createDetailItem(ev, isPending) {
    const div = document.createElement('div');
    div.className = 'event-item ev-' + ev.category + (isPending ? ' pending' : '') + (selectedEventId === ev.id ? ' selected' : '');
    div.setAttribute('role', 'button');
    div.setAttribute('tabindex', '0');
    div.dataset.id = ev.id;
    const cfg = CATEGORY_CONFIG[ev.category];
    const codeStr = ev.code ? formatCode(ev.code) : (ev.codes && ev.codes.length > 0 ? formatCodes(ev.codes) : '-');
    const detailsStr = buildDetailsSummary(ev);
    const tsDisplay = ev.display_time || '';
    div.innerHTML =
      '<span class="ev-time">' + escapeHtml(tsDisplay) + '</span>' +
      '<span class="ev-icon" title="' + cfg.label + '" style="color:' + cfg.color + '">' + cfg.icon + '</span>' +
      '<span class="ev-type">' + escapeHtml(ev.event_type) + '</span>' +
      '<span class="ev-code">' + escapeHtml(codeStr) + '</span>' +
      '<span class="ev-details">' + escapeHtml(detailsStr) + '</span>';

    div.addEventListener('click', function () {
      selectedEventId = ev.id;
      activeCategory = ev.category;
      renderDetailForEvent(ev);
      render();
    });
    return div;
  }

  // ===== 总渲染 =====
  function render() {
    if (renderTimer) return;
    renderTimer = setTimeout(function () {
      renderTimer = null;
      if (currentViz === 'matrix') renderMatrix();
      else renderScatter();
      renderTimerQueue();
    }, RENDER_THROTTLE);
  }

  // ===== 视图切换 =====
  function updateScatterLayoutBtn() {
    const btn = $('btnScatterLayout');
    if (!btn) return;
    if (currentViz === 'scatter') {
      btn.style.display = '';
      btn.textContent = scatterLayout === 'category' ? '📊 分类' : '📑 同行';
      btn.title = scatterLayout === 'category' ? '当前按分类分行显示，点击切换为同行显示' : '当前同行显示，点击切换为分类分行';
    } else {
      btn.style.display = 'none';
    }
  }

  function initVizTabs() {
    const tabs = document.querySelectorAll('.etp-viz-tab');
    tabs.forEach(tab => {
      tab.addEventListener('click', function () {
        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        currentViz = tab.dataset.viz;
        updateScatterLayoutBtn();
        render();
      });
    });

    const layoutBtn = $('btnScatterLayout');
    if (layoutBtn) {
      layoutBtn.addEventListener('click', function () {
        scatterLayout = scatterLayout === 'category' ? 'merged' : 'category';
        updateScatterLayoutBtn();
        render();
      });
    }
  }

  // ===== 拖拽/缩放 =====
  function initDragAndResize() {
    if (!eventPanel || !epDragHandle || !epHeader) return;

    function onDragStart(e) {
      if (e.button !== 0) return;
      isDragging = true;
      dragStartX = e.clientX;
      dragStartY = e.clientY;
      const rect = eventPanel.getBoundingClientRect();
      panelStartRight = window.innerWidth - rect.right;
      panelStartBottom = window.innerHeight - rect.bottom;
      eventPanel.style.transition = 'none';
      e.preventDefault();
    }
    epDragHandle.addEventListener('mousedown', onDragStart);
    epHeader.addEventListener('mousedown', onDragStart);

    if (epResizeHandle) {
      epResizeHandle.addEventListener('mousedown', function (e) {
        if (e.button !== 0) return;
        isResizing = true;
        resizeStartY = e.clientY;
        panelStartHeight = eventPanel.offsetHeight;
        eventPanel.style.transition = 'none';
        e.preventDefault();
        e.stopPropagation();
      });
    }

    document.addEventListener('mousemove', function (e) {
      if (isDragging) {
        const dx = e.clientX - dragStartX;
        const dy = e.clientY - dragStartY;
        let newRight = panelStartRight - dx;
        let newBottom = panelStartBottom - dy;
        newRight = Math.max(8, Math.min(newRight, window.innerWidth - 80));
        newBottom = Math.max(8, Math.min(newBottom, window.innerHeight - 60));
        eventPanel.style.right = newRight + 'px';
        eventPanel.style.bottom = newBottom + 'px';
        eventPanel.style.left = 'auto';
        eventPanel.style.top = 'auto';
      }
      if (isResizing) {
        const dy = resizeStartY - e.clientY;
        let newHeight = panelStartHeight + dy;
        newHeight = Math.max(200, Math.min(newHeight, window.innerHeight - 80));
        eventPanel.style.height = newHeight + 'px';
      }
    });

    document.addEventListener('mouseup', function () {
      if (isDragging) { isDragging = false; eventPanel.style.transition = ''; savePanelState(); }
      if (isResizing) { isResizing = false; eventPanel.style.transition = ''; savePanelState(); }
    });
  }

  function updatePauseBtn() {
    if (btnPauseIcon) btnPauseIcon.textContent = isPaused ? '▶' : '⏸';
    if (btnPauseResume) {
      btnPauseResume.classList.toggle('paused', isPaused);
      btnPauseResume.title = isPaused ? '继续接收事件' : '暂停接收事件';
    }
  }

  function toggleCollapse() {
    isCollapsed = !isCollapsed;
    eventPanel.classList.toggle('collapsed', isCollapsed);
    if (epToggleSym) epToggleSym.textContent = isCollapsed ? '+' : '−';
    savePanelState();
  }

  function toggleDetailCollapsed() {
    isDetailCollapsed = !isDetailCollapsed;
    eventDetail.classList.toggle('collapsed', isDetailCollapsed);
    if (eventDetailToggleSym) eventDetailToggleSym.textContent = isDetailCollapsed ? '▲' : '▼';
    savePanelState();
  }

  function savePanelState() {
    try {
      const state = {
        right: eventPanel.style.right,
        bottom: eventPanel.style.bottom,
        height: eventPanel.style.height,
        collapsed: isCollapsed,
        hidden: isPanelHidden,
        detailCollapsed: isDetailCollapsed
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) { /* ignore */ }
  }

  function restorePanelState() {
    try {
      eventPanel.style.left = 'auto';
      eventPanel.style.top = 'auto';
      eventPanel.style.right = '16px';
      eventPanel.style.bottom = '16px';
      eventPanel.style.width = '';
      eventPanel.style.height = '';

      const raw = localStorage.getItem(STORAGE_KEY);
      isDetailCollapsed = true;
      eventDetail.classList.add('collapsed');
      if (eventDetailToggleSym) eventDetailToggleSym.textContent = '▲';
      isPanelHidden = true;
      eventPanel.classList.add('hidden');
      eventPanel.classList.remove('visible');
      if (!raw) return;
      const state = JSON.parse(raw);
      var _vw = window.innerWidth;
      var _vh = window.innerHeight;
      if (state.right != null && state.right !== '' && state.right !== 'auto') {
        var _r = parseInt(state.right, 10);
        if (!isNaN(_r) && _r >= 8 && _r <= _vw - 200) eventPanel.style.right = _r + 'px';
      }
      if (state.bottom != null && state.bottom !== '' && state.bottom !== 'auto') {
        var _b = parseInt(state.bottom, 10);
        if (!isNaN(_b) && _b >= 8 && _b <= _vh - 100) eventPanel.style.bottom = _b + 'px';
      }
      if (state.height != null && state.height !== '') {
        var _h = parseInt(state.height, 10);
        if (!isNaN(_h) && _h >= 240 && _h <= _vh - 80) eventPanel.style.height = _h + 'px';
      }
      if (state.collapsed) { isCollapsed = true; eventPanel.classList.add('collapsed'); if (epToggleSym) epToggleSym.textContent = '+'; }
      if (state.hidden) { isPanelHidden = true; eventPanel.classList.add('hidden'); }
      if (state.detailCollapsed != null) {
        isDetailCollapsed = state.detailCollapsed;
      } else {
        isDetailCollapsed = true;
      }
      eventDetail.classList.toggle('collapsed', isDetailCollapsed);
      if (eventDetailToggleSym) eventDetailToggleSym.textContent = isDetailCollapsed ? '▲' : '▼';
    } catch (e) { /* ignore */ }
  }

  function initControls() {
    if (btnPauseResume) {
      btnPauseResume.addEventListener('click', function () {
        const wasPaused = isPaused;
        isPaused = !isPaused;
        updatePauseBtn();
        if (wasPaused) {
          const count = pendingEvents.length;
          flushPendingEvents();
          console.log('[EventPanel] 事件接收已继续，已刷新 ' + count + ' 条排队事件');
        } else {
          console.log('[EventPanel] 事件接收已暂停，新事件进入排队队列');
        }
      });
    }

    if (btnClearEvents) {
      btnClearEvents.addEventListener('click', function () {
        clearEvents();
      });
    }

    if (btnToggleEvents) {
      btnToggleEvents.addEventListener('click', function (e) {
        e.stopPropagation();
        toggleCollapse();
      });
    }

    if (epHeader) epHeader.addEventListener('dblclick', toggleCollapse);

    if (btnCloseEvents) {
      btnCloseEvents.addEventListener('click', function () {
        eventPanel.classList.add('hidden');
        isPanelHidden = true;
        savePanelState();
      });
    }

    if (btnCloseDetail) {
      btnCloseDetail.addEventListener('click', function () {
        activeCategory = null;
        selectedEventId = null;
        renderDetailEmpty();
        render();
      });
    }

    if (btnToggleDetail) {
      btnToggleDetail.addEventListener('click', function () {
        toggleDetailCollapsed();
      });
    }

    if (eventTimerQueue) {
      const header = eventTimerQueue.querySelector('.etp-timer-queue-header');
      if (header) {
        header.addEventListener('click', function () {
          eventTimerQueue.classList.toggle('collapsed');
        });
      }
    }
  }

  // ===== SSE：唯一事件入口 =====
  const _eventSubscribers = [];

  function subscribeEvents(callback) {
    _eventSubscribers.push(callback);
    return function () {
      var idx = _eventSubscribers.indexOf(callback);
      if (idx !== -1) _eventSubscribers.splice(idx, 1);
    };
  }

  function _notifyEventSubscribers(ev) {
    _eventSubscribers.forEach(function (cb) {
      try { cb(ev); } catch (e) { console.error('[EventPanel] event subscriber error:', e); }
    });
  }

  async function loadRecentEvents() {
    try {
      const res = await fetch('/api/events/recent');
      if (!res.ok) return;
      const data = await res.json();
      const list = (data && data.events) || (Array.isArray(data) ? data : []);
      if (Array.isArray(list)) list.forEach(ev => addEvent(ev));
    } catch (e) { /* ignore */ }
  }

  function initSSE() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (eventSource) { eventSource.close(); eventSource = null; }
    try {
      eventSource = new EventSource('/api/events/stream');
      eventSource.onopen = function () { console.log('[EventPanel] SSE connected'); };
      eventSource.onmessage = function (e) {
        try {
          const ev = JSON.parse(e.data);
          _notifyEventSubscribers(ev);
          addEvent(ev);
        } catch (err) { console.error('[EventPanel] Parse SSE event failed:', err); }
      };
      eventSource.onerror = function () {
        console.warn('[EventPanel] SSE error, reconnect in ' + (RECONNECT_DELAY / 1000) + 's...');
        if (eventSource) { eventSource.close(); eventSource = null; }
        if (!reconnectTimer) reconnectTimer = setTimeout(function () { reconnectTimer = null; initSSE(); }, RECONNECT_DELAY);
      };
    } catch (e) {
      console.error('[EventPanel] SSE init failed:', e);
      if (!reconnectTimer) reconnectTimer = setTimeout(function () { reconnectTimer = null; initSSE(); }, RECONNECT_DELAY);
    }
  }

  function closeSSE() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (eventSource) { eventSource.close(); eventSource = null; }
  }

  // ===== 计时器队列轮询：仅作为展示 =====
  let timerPollTimer = null;

  async function syncTimerQueue() {
    try {
      var sid = sessionId || '';
      var url = '/api/events/timer-queue' + (sid ? '?session_id=' + encodeURIComponent(sid) : '');
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      if (!data.success || !Array.isArray(data.timers)) return;
      timerQueue = data.timers.map(spec => {
        const fireMs = Number(spec.fire_at_ms);
        if (isNaN(fireMs)) return null;
        const code = spec.code || '';
        const key = fireMs + '-' + (spec.edge_id || '') + '-' + code;
        return {
          key: key,
          ev: {
            id: 'timer-' + key,
            ts: fireMs,
            display_ts: fireMs,
            category: spec.category || 'ttl',
            event_type: spec.event_type || 'TimerQueued',
            code: code,
            pool_id: spec.pool_id || '',
            edge_id: spec.edge_id || '',
            display_fire_time: spec.display_fire_time || '',
            display_fire_time_ms: spec.display_fire_time_ms || '',
            state: spec.state || 'waiting',
            trigger_type: spec.trigger_type || '定时器',
            remaining_text: spec.remaining_text || '',
            details: spec.details || {},
            raw: spec,
            pending: false
          },
          updated: Date.now()
        };
      }).filter(function (t) { return t != null; });
      scheduleRender();
    } catch (e) { /* ignore */ }
  }

  function startTimerPolling() {
    if (timerPollTimer) return;
    syncTimerQueue();
    timerPollTimer = setInterval(syncTimerQueue, 1000);
  }

  function stopTimerPolling() {
    if (timerPollTimer) {
      clearInterval(timerPollTimer);
      timerPollTimer = null;
    }
    timerQueue = [];
  }

  function setSession(sid) {
    sessionId = sid;
    window.sessionId = sid;
  }

  function showEventPanel() {
    if (eventPanel) {
      eventPanel.classList.remove('hidden');
      eventPanel.classList.add('visible');
      isPanelHidden = false;
      savePanelState();
      requestAnimationFrame(function () {
        if (renderTimer) clearTimeout(renderTimer);
        renderTimer = null;
        if (currentViz === 'matrix') renderMatrix();
        else renderScatter();
        renderTimerQueue();
      });
    }
  }

  function hideEventPanel() {
    if (eventPanel) {
      eventPanel.classList.remove('visible');
      eventPanel.classList.add('hidden');
      isPanelHidden = true;
      savePanelState();
    }
  }

  function toggleEventPanel() {
    if (isPanelHidden || !eventPanel.classList.contains('visible')) {
      showEventPanel();
    } else {
      hideEventPanel();
    }
  }

  // 暴露最小必要全局接口
  window.EventPanelBus = { subscribe: subscribeEvents };
  window.eventPanelSetSession = setSession;
  window.showEventPanel = showEventPanel;
  window.hideEventPanel = hideEventPanel;
  window.toggleEventPanel = toggleEventPanel;
  window.getEventCount = function() { return totalEventCount; };
  window.clearEventPanel = clearEvents;

  // ===== 初始化 =====
  function init() {
    if (!eventPanel) return;
    initDragAndResize();
    restorePanelState();
    initControls();
    initVizTabs();
    initMatrixInteraction();
    initScatterInteraction();
    initTimerQueueInteraction();
    ensureCategoryRows();
    updatePauseBtn();
    renderDetailEmpty();
    if (eventTimerQueue) eventTimerQueue.classList.remove('collapsed');

    window.addEventListener('resize', function () {
      if (currentViz === 'matrix') renderMatrix();
      else if (currentViz === 'scatter') renderScatter();
    });

    loadRecentEvents();
    initSSE();
    startTimerPolling();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.addEventListener('beforeunload', closeSSE);
})();
