/**
 * 事件面板 - 可视化版
 * 功能：
 * - 分类矩阵视图：每类事件一行图标，点击分类/图标在下方显示事件文本记录
 * - 散点分布视图：Canvas 绘制时间轴上的事件点（已发生/排队中）
 * - 定时器队列：单独显示排队中的 timer 事件
 * - SSE 实时事件流 + 历史事件加载
 * - 暂停/继续/清空、折叠/展开、拖拽
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

  // 9 个事件分类配置：颜色、图标、显示名称
  // 图标语义对齐：tick=📡(实时行情流), bar=📈(K线), formula=🧮(计算),
  //   edge=🔀(边触发分叉), transfer=🔄(状态流转), signal=🔔(信号铃),
  //   order=📋(订单), ttl=⏰(计时器), system=⚙(系统齿轮)
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

  const EVENT_TYPE_TO_CATEGORY = {
    'tickreceived': 'tick', 'datachanged': 'tick', 'tick': 'tick', 'tick_update': 'tick', 'tickdue': 'tick',
    'barcomposed': 'bar', 'bar': 'bar', 'kline': 'bar',
    'formulaevaluated': 'formula', 'stockfiltered': 'formula', 'formula': 'formula', 'filter': 'formula',
    'edgefired': 'edge', 'crossoverdetected': 'edge', 'edge': 'edge', 'cross': 'edge', 'crossover': 'edge',
    'transferexecuted': 'transfer', 'executed': 'transfer', 'rankingchanged': 'transfer',
    'transfer': 'transfer', 'enter': 'transfer', 'exit': 'transfer', 'rank_changed': 'transfer',
    'signal': 'signal', 'buy': 'signal', 'sell': 'signal',
    'orderplaced': 'order', 'orderfilled': 'order', 'positionupdated': 'order', 'order': 'order',
    'ttlexpired': 'ttl', 'ttldue': 'ttl', 'timeout': 'ttl', 'ttl': 'ttl',
    'modechanged': 'system', 'timeadvanced': 'system', 'poolloaded': 'system', 'configloaded': 'system',
    'configchanged': 'system', 'simulationstep': 'system', 'replaystep': 'system', 'replaystarted': 'system',
    'importstarted': 'system', 'exportcompleted': 'system', 'statisticsupdated': 'system',
    'snapshotupdated': 'system', 'eventlogged': 'system', 'alertraised': 'system', 'alert': 'system',
    'loaded': 'system', 'saved': 'system', 'import': 'system', 'export': 'system', 'system': 'system',
    'timerqueued': 'ttl', 'timerfired': 'ttl', 'timerexpired': 'ttl', 'edgetimer': 'ttl', 'ticktimer': 'ttl', 'unknown': 'system'
  };

  // 状态
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
  let activeCategory = null; // 当前选中的分类
  let selectedEventId = null; // 当前选中的事件
  let activeFilters = new Set(CATEGORIES);
  let currentViz = 'matrix'; // 'matrix' | 'scatter'
  let scatterLayout = 'category'; // 'category'(按分类分行) | 'merged'(同行显示)
  let isDetailCollapsed = true; // 详情区默认折叠，增大可视化区
  let scatterHitRegions = []; // 散点图标点击热区
  let matrixHitRegions = []; // 矩阵图标点击热区
  let timerHitRegions = []; // 定时器图标点击热区
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
  let categoryRowContainer = null; // 分类行 DOM 容器（无障碍访问）

  const EVENT_STATE_STYLES = {
    pending: {
      borderStyle: [3, 2],
      borderColor: '#ffc107',
      fillAlpha: 0.6,
      glowColor: 'rgba(255,193,7,0.4)',
      glowSize: 4
    },
    triggered: {
      borderStyle: null,
      borderColor: null,
      fillAlpha: 1.0,
      glowColor: null,
      glowSize: 5
    },
    expired: {
      borderStyle: [2, 2],
      borderColor: '#f44336',
      fillAlpha: 0.5,
      glowColor: 'rgba(244,67,54,0.5)',
      glowSize: 6
    }
  };

  const TIMER_STATE_STYLES = {
    waiting: {
      label: '等待中',
      fillColor: null,
      strokeColor: null,
      fillAlpha: 1.0,
      lineStyle: null,
      listBg: 'transparent',
      listOpacity: 1.0,
      textDecoration: 'none',
      canvasShape: 'filled-circle'
    },
    fired: {
      label: '已触发',
      fillColor: '#9e9e9e',
      strokeColor: '#9e9e9e',
      fillAlpha: 0.6,
      lineStyle: [4, 3],
      listBg: 'rgba(158,158,158,0.15)',
      listOpacity: 0.5,
      textDecoration: 'line-through',
      canvasShape: 'filled-circle'
    },
    expired: {
      label: '已过期',
      fillColor: 'rgba(0,0,0,0)',
      strokeColor: '#f44336',
      fillAlpha: 0.5,
      lineStyle: [2, 2],
      listBg: 'rgba(244,67,54,0.1)',
      listOpacity: 0.5,
      textDecoration: 'line-through',
      canvasShape: 'hollow-circle'
    }
  };

  function getTimerState(timerItem, now) {
    if (timerItem.state) return timerItem.state;
    const ev = timerItem.ev;
    const fireAt = getFireAtMs(ev);
    const type = String(ev.event_type || '').toLowerCase();
    if (type.includes('fired') || type.includes('expired') || type.includes('triggered')) {
      return 'fired';
    }
    if (fireAt < now - 100) {
      return 'expired';
    }
    return 'waiting';
  }

  // 定时器触发类型分类（队列列表"触发类型"列）：
  //   边定时器=边到时触发，TTL超时=股票在池中停留超限，Tick定时器=按tick间隔触发，
  //   一次性=只触发一次后注销，循环=周期性触发
  const TIMER_TRIGGER_TYPES = [
    { key: 'edge_timer',    label: '边定时器',   match: /edge.*timer|edgetimer|edgefired|crossover/i,    color: '#ff9800' },
    { key: 'ttl',           label: 'TTL超时',    match: /ttl|ttldue|ttlexpired|timeout/i,                 color: '#b71c1c' },
    { key: 'tick_timer',    label: 'Tick定时器', match: /ticktimer|tick.*timer|tickdue|\btick\b/i,         color: '#9e9e9e' },
    { key: 'one_shot',      label: '一次性',     match: /oneshot|one_shot|count_gte_1|single/i,           color: '#9c27b0' },
    { key: 'recurring',     label: '循环',       match: /recurring|periodic|interval|cxtype.*0/i,         color: '#4caf50' },
    { key: 'timer',         label: '定时器',     match: /timer|fire|due/i,                                  color: '#2196f3' }
  ];
  const TIMER_TRIGGER_COLORS = TIMER_TRIGGER_TYPES.reduce((m, t) => { m[t.label] = t.color; return m; }, {});

  function getTimerTriggerType(ev) {
    if (ev && ev.trigger_type) return ev.trigger_type;
    const d = (ev && ev.details) || {};
    const hints = [
      String(ev.event_type || ''),
      String(d.trigger_type || d.timer_kind || d.kind || ''),
      String(d.fire_reason || d.reason || ''),
      String(d.edge_id ? 'edge' : ''),
      String(d.ttl ? 'ttl' : ''),
      String(d.interval ? 'recurring' : ''),
      String(d.one_shot ? 'oneshot' : ''),
      String(d.count === 1 ? 'single' : '')
    ].join(' ').toLowerCase();
    for (const t of TIMER_TRIGGER_TYPES) {
      if (t.match.test(hints)) return t.label;
    }
    return '定时器';
  }

  function formatRemainingTime(fireAt, now, ev) {
    if (ev && ev.remaining_text) return ev.remaining_text;
    const diff = fireAt - now;
    if (diff <= 0) return '已触发';
    if (diff < 1000) return Math.floor(diff) + 'ms后触发';
    if (diff < 60000) return (diff / 1000).toFixed(1) + 's后触发';
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm' + Math.floor((diff % 60000) / 1000) + 's后触发';
    return Math.floor(diff / 3600000) + 'h后触发';
  }

  const SCATTER_AGGREGATE_WINDOW = 500;
  const SCATTER_TOOLTIP_DELAY = 100;

  function classifyEvent(ev) {
    // Task 1: 优先使用后端已计算的 category，仅对未格式化的遗留事件做极简兜底分类
    if (ev.category) return ev.category;
    const rawType = ev.event_type || ev.type || 'UNKNOWN';
    const type = String(rawType).toLowerCase();
    if (EVENT_TYPE_TO_CATEGORY[type]) return EVENT_TYPE_TO_CATEGORY[type];
    if (type.includes('buy') || type.includes('sell')) return 'signal';
    if (type.includes('tick') || type.includes('data')) return 'tick';
    if (type.includes('bar') || type.includes('kline')) return 'bar';
    if (type.includes('formula') || type.includes('filter')) return 'formula';
    if (type.includes('edge') || type.includes('cross')) return 'edge';
    if (type.includes('transfer') || type.includes('executed') || type.includes('rank') || type.includes('enter') || type.includes('exit')) return 'transfer';
    if (type.includes('order') || type.includes('position')) return 'order';
    if (type.includes('ttl') || type.includes('timeout') || type.includes('expire') || type.includes('timer')) return 'ttl';
    return 'system';
  }

  function isSimulationMode() {
    return window.AppState && typeof window.AppState.isSimulationMode === 'function'
      ? window.AppState.isSimulationMode()
      : false;
  }


  // Task 1: 时间戳统一由后端 web_state.py 归一化，前端仅做展示。
  // 以下极简函数仅对未格式化的遗留事件做兜底，不再包含模式换算/仿真基准等复杂逻辑。
  function getEventTs(ev) {
    if (ev.display_ts != null) return Number(ev.display_ts);
    if (ev.ts != null) return Number(ev.ts) < 1e12 ? Number(ev.ts) * 1000 : Number(ev.ts);
    if (ev.timestamp != null) return Number(ev.timestamp) < 1e12 ? Number(ev.timestamp) * 1000 : Number(ev.timestamp);
    if (ev.time != null) {
      const n = Number(ev.time);
      if (!isNaN(n)) return n < 1e12 ? n * 1000 : n;
    }
    return Date.now();
  }

  function getFireAtMs(ev) {
    if (ev.fire_at_ms != null) return Number(ev.fire_at_ms);
    const d = ev.details || {};
    let t = null;
    if (d.fire_at != null) t = Number(d.fire_at);
    else if (d.fire_time != null) t = Number(d.fire_time);
    else if (d.next_fire_time != null) t = Number(d.next_fire_time);
    if (t == null || isNaN(t)) return getEventTs(ev);
    return t < 1e12 ? t * 1000 : t;
  }

  function formatSimDuration(ms) {
    if (ms < 0) ms = 0;
    const totalSec = Math.floor(ms / 1000);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    const milli = Math.floor(ms % 1000);
    const pad = n => String(n).padStart(2, '0');
    if (h > 0) return h + ':' + pad(m) + ':' + pad(s);
    if (m > 0) return pad(m) + ':' + pad(s);
    return s + '.' + String(milli).padStart(3, '0').slice(0, 1) + 's';
  }

  function formatSimDurationMs(ms) {
    if (ms < 0) ms = 0;
    const totalSec = Math.floor(ms / 1000);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    const milli = Math.floor(ms % 1000);
    const pad = n => String(n).padStart(2, '0');
    const p3 = n => String(n).padStart(3, '0');
    if (h > 0) return h + ':' + pad(m) + ':' + pad(s) + '.' + p3(milli);
    if (m > 0) return pad(m) + ':' + pad(s) + '.' + p3(milli);
    return s + '.' + p3(milli) + 's';
  }

  function formatTime(ts, timeMode) {
    if (timeMode == null) timeMode = isSimulationMode() ? 'relative' : 'wall';
    if (timeMode === 'relative') {
      return formatSimDuration(ts);
    }
    const d = new Date(ts);
    const pad = n => String(n).padStart(2, '0');
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  function formatTimeMs(ts, timeMode) {
    if (timeMode == null) timeMode = isSimulationMode() ? 'relative' : 'wall';
    if (timeMode === 'relative') {
      return formatSimDurationMs(ts);
    }
    const d = new Date(ts);
    const pad = n => String(n).padStart(2, '0');
    const p3 = n => String(n).padStart(3, '0');
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) + '.' + p3(d.getMilliseconds());
  }

  // 极简兜底：未从后端获取到 display_time 时使用
  function formatTimeFallback(ts, timeMode) {
    return formatTime(ts, timeMode);
  }
  function formatTimeMsFallback(ts, timeMode) {
    return formatTimeMs(ts, timeMode);
  }

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
    return ev.category + '-' + ev.ts + '-' + (ev.event_type || '') + '-' + Math.random().toString(36).slice(2, 7);
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
    if (d.fire_at) {
      const fa = Number(d.fire_at);
      if (!isNaN(fa)) parts.push('fire_at:' + escapeHtml(formatTimeMs(fa < 1e12 ? fa * 1000 : fa)));
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

  // 添加事件
  // Task 1: 事件已由后端 format_event 格式化，前端直接使用 category / display_ts / display_time 等字段；
  // 对未格式化的遗留事件保留极简兜底。
  function addEvent(ev) {
    const cat = ev.category || classifyEvent(ev);
    const ts = ev.display_ts != null ? Number(ev.display_ts) : getEventTs(ev);
    const eventObj = {
      id: ev.id || buildEventId(ev),
      ts: ts,
      category: cat,
      event_type: ev.event_type || ev.type || 'UNKNOWN',
      code: ev.code || '',
      codes: ev.codes || null,
      pool_id: ev.pool_id || '',
      edge_id: ev.edge_id || '',
      details: ev.details || {},
      display_time: ev.display_time || formatTimeFallback(ts, ev.time_mode),
      display_time_ms: ev.display_time_ms || formatTimeMsFallback(ts, ev.time_mode),
      time_mode: ev.time_mode || (ts < 86400000.0 ? 'relative' : 'wall'),
      raw: ev,
      pending: isPaused
    };

    // 如果是 timer 排队事件，加入定时器队列
    if (cat === 'ttl' && (eventObj.event_type.toLowerCase().includes('timer') || eventObj.details.fire_at || eventObj.details.queue_position != null)) {
      addTimerQueueItem(eventObj);
    }

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

  function addTimerQueueItem(ev) {
    const fireAtMs = getFireAtMs(ev);
    const key = fireAtMs + '-' + ev.event_type + '-' + (ev.code || '');
    const idx = timerQueue.findIndex(t => t.key === key);
    if (idx >= 0) timerQueue[idx] = { key, ev, updated: ev.ts, source: 'event' };
    else timerQueue.push({ key, ev, updated: ev.ts, source: 'event' });
    cleanupExpiredTimers();
  }

  function cleanupExpiredTimers() {
    const now = getCurrentTime();
    const realNow = Date.now();
    timerQueue = timerQueue.filter(t => {
      if (t.source === 'poll') {
        const fireAt = getFireAtMs(t.ev);
        if (fireAt < now - 5000) return false;
        return true;
      }
      if (t.updated < realNow - 120000) return false;
      const fireAt = getFireAtMs(t.ev);
      const type = String(t.ev.event_type || '').toLowerCase();
      if (type.includes('fired') || type.includes('expired') || type.includes('triggered')) {
        return fireAt > now - 60000;
      }
      return true;
    });
  }

  let timerPollTimer = null;
  async function syncTimerQueue() {
    if (!isSimulationMode()) return;
    try {
      var sid = (typeof window.simSessionId !== 'undefined') ? window.simSessionId : '';
      var url = '/api/events/timer-queue' + (sid ? '?session_id=' + encodeURIComponent(sid) : '');
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      if (!data.success || !Array.isArray(data.timers)) return;
      // Task 1: 计时器队列已由后端 format_timer_queue 格式化，前端直接使用 display_fire_ms / state / trigger_type 等字段
      const now = Number(data.now_ms) || getCurrentTime();
      const pollKeys = new Set();
      data.timers.forEach(spec => {
        const fireMs = Number(spec.display_fire_ms) || Number(spec.fire_at) || 0;
        if (fireMs <= 0) return;
        const code = spec.code || '';
        const key = 'poll-' + fireMs + '-' + (spec.edge_id || '') + '-' + code;
        pollKeys.add(key);
        const existingIdx = timerQueue.findIndex(t => t.key === key);
        const pseudoEvent = {
          id: 'timer-' + key,
          ts: now,
          display_ts: fireMs,
          category: spec.category || 'ttl',
          event_type: spec.kind === 'edge' ? 'EdgeTimer' : 'TimerQueued',
          code: code,
          pool_id: spec.pool_id || '',
          edge_id: spec.edge_id || '',
          display_fire_time: spec.display_fire_time || '',
          state: spec.state || 'waiting',
          trigger_type: spec.trigger_type || '定时器',
          remaining_text: spec.remaining_text || '',
          details: {
            fire_at: fireMs < 1e12 ? fireMs / 1000 : fireMs,
            queue_position: 0,
            kind: spec.kind || 'ttl'
          },
          raw: spec,
          pending: false
        };
        if (existingIdx >= 0) {
          timerQueue[existingIdx].ev = pseudoEvent;
          timerQueue[existingIdx].updated = now;
          timerQueue[existingIdx].state = spec.state || 'waiting';
        } else {
          timerQueue.push({ key, ev: pseudoEvent, updated: now, source: 'poll', state: spec.state || 'waiting' });
        }
      });
      timerQueue = timerQueue.filter(t => {
        if (t.source === 'poll') {
          return pollKeys.has(t.key);
        }
        return true;
      });
      cleanupExpiredTimers();
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
    timerQueue = timerQueue.filter(t => t.source !== 'poll');
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
    if (window.AppState && typeof window.AppState.getCurrentDisplayTime === 'function') {
      return window.AppState.getCurrentDisplayTime();
    }
    return Date.now();
  }

  function getEventState(ev, now) {
    if (ev.pending) return 'pending';
    if (ev.category === 'ttl') {
      const fireAt = getFireAtMs(ev);
      const type = String(ev.event_type || '').toLowerCase();
      if (type.includes('timerqueued') && fireAt > now) return 'pending';
      if (fireAt > 0 && fireAt < now - 5000 && type.includes('timer') && !type.includes('fired') && !type.includes('expired')) return 'expired';
    }
    return 'triggered';
  }

  function scheduleRender() {
    render();
  }

  // ===== 渲染：分类矩阵（每行按时间轴分布图标） =====
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

  function chooseTickInterval(span) {
    var targets = [5000, 10000, 15000, 20000, 30000, 60000];
    for (var i = 0; i < targets.length; i++) {
      if (span / targets[i] >= 4 && span / targets[i] <= 12) return targets[i];
    }
    if (span <= 30000) return 5000;
    if (span <= 60000) return 10000;
    if (span <= 120000) return 20000;
    return 30000;
  }

  // ===== 共用绘制辅助函数：标签区/时间轴/事件图标 =====

  function getEventPosTs(ev) {
    return ev.category === 'ttl' ? getFireAtMs(ev) : getEventTs(ev);
  }

  function computeTimeWindow(all, now) {
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
    if (isSimulationMode() && minTs < 0) {
      var shift = -minTs;
      minTs = 0;
      timeSpan = Math.max(timeSpan - shift, DEFAULT_TIME_WINDOW);
      if (timeSpan > MAX_TIME_WINDOW) timeSpan = MAX_TIME_WINDOW;
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
    var tickInterval = chooseTickInterval(timeSpan);
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 1;
    ctx.fillStyle = '#8080a0';
    ctx.font = '9px Consolas, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    var firstTick = Math.ceil(minTs / tickInterval) * tickInterval;
    for (var t = firstTick; t <= minTs + timeSpan + 1; t += tickInterval) {
      var ratio = (t - minTs) / timeSpan;
      var tx = plotX + ratio * plotW;
      if (tx >= plotX && tx <= plotX + plotW) {
        ctx.beginPath();
        ctx.setLineDash([2, 3]);
        ctx.moveTo(tx, 0);
        ctx.lineTo(tx, plotH);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillText(formatTime(t), tx, H - 4);
      }
    }
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
    var state = opts.state;
    var iconSize = opts.iconSize || 14;
    var labelFont = opts.labelFont;
    var isHover = opts.isHover;
    var style = EVENT_STATE_STYLES[state];
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
      // 即使无事件也更新分类行位置和计数（全部为 0）
      var emptyByCat = {};
      CATEGORIES.forEach(function (c) { emptyByCat[c] = []; });
      updateCategoryRows({ rowH: rowH }, emptyByCat);
      return;
    }

    if (matrixNowLine) matrixNowLine.style.display = '';

    var now = getCurrentTime();
    var win = computeTimeWindow(all, now);
    var minTs = win.minTs;
    var timeSpan = win.timeSpan;

    lastMatrixLayout = { plotX: plotX, plotW: plotW, plotTop: 0, plotH: plotH, minTs: minTs, timeSpan: timeSpan, rowH: rowH, W: W, H: H, axisH: axisH, labelWidth: labelWidth };

    // 分类统计与绘制
    var byCat = {};
    CATEGORIES.forEach(function (c) { byCat[c] = []; });
    all.forEach(function (ev) { byCat[ev.category].push(ev); });
    updateCategoryRows(lastMatrixLayout, byCat);

    var labelFont = 'Segoe UI Emoji, Apple Color Emoji, Segoe UI, Microsoft YaHei, system-ui, sans-serif';
    drawLabelArea(ctx, { labelWidth: labelWidth, plotH: plotH, W: W });
    drawHorizontalGrid(ctx, { plotX: plotX, W: W, plotH: plotH, rowH: rowH, catCount: catCount, padRight: padRight });
    drawTimeAxis(ctx, { plotX: plotX, plotW: plotW, plotH: plotH, H: H, minTs: minTs, timeSpan: timeSpan });

    // 分桶处理同毫秒事件
    var buckets = {};
    all.forEach(function (ev) {
      var posTs = ev.category === 'ttl' ? getFireAtMs(ev) : getEventTs(ev);
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
      var posTs = ev.category === 'ttl' ? getFireAtMs(ev) : getEventTs(ev);
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
      // 严格裁剪：事件位置必须在绘图区和当前行内
      var exMin = plotX + iconSize / 2;
      var exMax = plotX + plotW - iconSize / 2;
      if (ex < exMin) ex = exMin;
      if (ex > exMax) ex = exMax;
      var eyMin = idx * rowH;
      var eyMax = (idx + 1) * rowH;
      if (ey < eyMin) ey = eyMin;
      if (ey > eyMax) ey = eyMax;

      var state = getEventState(ev, now);
      drawEventIcon(ctx, {
        ex: ex, ey: ey, cfg: cfg, state: state,
        iconSize: iconSize, labelFont: labelFont,
        isHover: !!(matrixHoverEvent && matrixHoverEvent.id === ev.id)
      });

      matrixHitRegions.push({ x: ex - iconSize / 2, y: ey - iconSize / 2, w: iconSize, h: iconSize, ev: ev, state: state });
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
        var state = hit.state;
        var stateLabel = state === 'pending' ? ' [排队中]' : (state === 'expired' ? ' [已过期]' : '');
        var stateColor = state === 'pending' ? '#ffc107' : (state === 'expired' ? '#f44336' : '#4caf50');
        var displayTs = ev.category === 'ttl' && ev.details.fire_at ? getFireAtMs(ev) : getEventTs(ev);
        var displayTime = ev.display_time_ms || formatTimeMs(displayTs, ev.time_mode);
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

  // ===== 分类行 DOM 元素（统一矩阵与散点的左侧标签区，避免 Canvas 与 DOM 重复绘制）=====
  let currentCategoryRowConfig = null;

  function getCategoryRowConfig() {
    if (currentViz === 'scatter' && scatterLayout === 'merged') {
      return {
        rows: [{ cat: 'all', label: '事件', icon: '📊', color: '#e0e0ff' }],
        single: true
      };
    }
    return {
      rows: CATEGORIES.map(function (cat) { return { cat: cat, ...CATEGORY_CONFIG[cat] }; }),
      single: false
    };
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

  // ===== 散点聚合辅助函数：按时间窗口聚合 =====
  // layout='category' 时按分类独立分行聚合；layout='merged' 时所有分类同行聚合。
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
        clusters.push({
          centerTs: posTs,
          events: [ev],
          primaryCategory: cat,
          timeMode: ev.time_mode || (posTs < 86400000.0 ? 'relative' : 'wall')
        });
      }
    });

    clusters.forEach(cluster => {
      const catCounts = {};
      const codes = new Set();
      let hasPending = false;
      let anyExpired = false;
      cluster.events.forEach(ev => {
        const cat = ev.category;
        catCounts[cat] = (catCounts[cat] || 0) + 1;
        if (ev.code) codes.add(formatCode(ev.code));
        if (ev.codes && Array.isArray(ev.codes)) ev.codes.forEach(c => codes.add(formatCode(c)));
        if (ev.pending) hasPending = true;
        const state = getEventState(ev, getCurrentTime());
        if (state === 'expired') anyExpired = true;
      });
      cluster.catCounts = catCounts;
      cluster.codes = Array.from(codes);
      cluster.hasPending = hasPending;
      cluster.anyExpired = anyExpired;
    });

    return clusters.sort((a, b) => a.centerTs - b.centerTs);
  }

  // ===== 渲染：散点分布（图标沿时间轴分布，带聚合） =====
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
    const win = computeTimeWindow(all, now);
    const minTs = win.minTs;
    const span = win.timeSpan;
    lastScatterLayout = { minTs: minTs, span: span, W: W, H: H, rowH: rowH, catCount: catCount };

    const catIndex = {};
    CATEGORIES.forEach((c, i) => catIndex[c] = i);

    const labelFont = 'Segoe UI Emoji, Apple Color Emoji, Segoe UI, Microsoft YaHei, system-ui, sans-serif';

    // 分类统计与绘制
    const byCat = {};
    CATEGORIES.forEach(c => byCat[c] = []);
    all.forEach(ev => { byCat[ev.category].push(ev); });
    updateCategoryRows(lastScatterLayout, byCat);

    drawLabelArea(ctx, { labelWidth: labelPadLeft, plotH: plotH, W: W });
    drawHorizontalGrid(ctx, { plotX: labelPadLeft, W: W, plotH: plotH, rowH: rowH, catCount: catCount, padRight: 8 });
    drawTimeAxis(ctx, { plotX: labelPadLeft, plotW: plotW, plotH: plotH, H: H, minTs: minTs, timeSpan: span });

    const clusters = aggregateScatterEvents(all, minTs, span, scatterLayout);
    const iconSize = 14;
    const badgeRadius = 8;

    clusters.forEach(cluster => {
      const cat = cluster.primaryCategory;
      const cfg = CATEGORY_CONFIG[cat];
      const idx = catIndex[cat];
      if (idx < 0) return;
      const cx = labelPadLeft + ((cluster.centerTs - minTs) / span) * plotW;
      // 按布局决定垂直位置：category 模式在分类行中线上；merged 模式全部在中线
      const cy = scatterLayout === 'category' ? idx * rowH + rowH / 2 : plotH / 2;
      const isCluster = cluster.events.length > 1;
      const hasSelected = cluster.events.some(e => e.id === selectedEventId);

      drawEventIcon(ctx, {
        ex: cx, ey: cy, cfg: cfg,
        state: cluster.anyExpired ? 'expired' : (cluster.hasPending ? 'pending' : 'triggered'),
        iconSize: iconSize, labelFont: labelFont,
        isHover: hasSelected
      });

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

  function getScatterEventAt(x, y) {
    const hit = getScatterClusterAt(x, y);
    return hit ? (hit.cluster.events.length === 1 ? hit.cluster.events[0] : null) : null;
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

      let html = '<div class="tt-head">';
      html += '<span style="color:#ff9800">⏱</span> <b>' + escapeHtml(formatTimeMs(cluster.centerTs, cluster.timeMode)) + '</b>';
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

  // ===== 渲染：定时器队列（时间分布图 + 列表） =====
  function renderTimerQueue() {
    if (!timerQueueBody || !timerQueueCanvas) return;
    cleanupExpiredTimers();
    timerQueueCount.textContent = timerQueue.length;
    renderTimerQueueTimeline();

    timerQueueBody.innerHTML = '';
    if (timerQueue.length === 0) {
      timerQueueBody.innerHTML = '<div class="etp-timer-item"><span class="ti-detail">定时器队列为空</span></div>';
      return;
    }

    // 添加列标题行，明确各列含义
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

    const now = getCurrentTime();
    const sortedItems = timerQueue.slice().sort((a, b) => {
      return getFireAtMs(a.ev) - getFireAtMs(b.ev);
    });

    sortedItems.forEach((timerItem) => {
      const ev = timerItem.ev;
      const fireAt = getFireAtMs(ev);
      const state = timerItem.state || getTimerState(timerItem, now);
      const style = TIMER_STATE_STYLES[state];
      const cfg = CATEGORY_CONFIG[ev.category] || CATEGORY_CONFIG.ttl;
      const triggerType = ev.trigger_type || getTimerTriggerType(ev);
      const triggerColor = TIMER_TRIGGER_COLORS[triggerType] || '#c0c0e0';

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
        timeDisplay = ev.remaining_text || formatRemainingTime(fireAt, now, ev);
        statusColor = cfg.color;
      } else if (state === 'expired') {
        timeDisplay = style.label;
        statusColor = '#f44336';
      } else {
        timeDisplay = style.label;
        statusColor = '#9e9e9e';
      }

      const fireTimeDisplay = ev.display_fire_time_ms || ev.display_time_ms || formatTimeMs(fireAt, ev.time_mode);
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
      return getFireAtMs(a.ev) - getFireAtMs(b.ev);
    }).map(t => {
      const state = t.state || getTimerState(t, now);
      return {
        ev: t.ev,
        fireAt: getFireAtMs(t.ev),
        createdAt: t.ev.ts || t.updated,
        state: state
      };
    });

    let minTs = now - 5000;
    let maxTs = now + 60000;
    items.forEach(it => {
      if (it.fireAt < minTs) minTs = it.fireAt;
      if (it.createdAt && it.createdAt < minTs) minTs = it.createdAt;
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
      const style = TIMER_STATE_STYLES[it.state];

      if (it.createdAt && it.state === 'waiting' && it.createdAt < it.fireAt) {
        const startX = timeToX(it.createdAt);
        if (startX < x - 2) {
          ctx.save();
          ctx.strokeStyle = cfg.color;
          ctx.globalAlpha = 0.35;
          ctx.lineWidth = 2;
          ctx.setLineDash([3, 2]);
          ctx.beginPath();
          ctx.moveTo(startX, markerY);
          ctx.lineTo(x - markerRadius - 2, markerY);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.restore();
        }
      }

      ctx.save();
      ctx.globalAlpha = style.fillAlpha;

      if (style.canvasShape === 'hollow-circle') {
        ctx.beginPath();
        ctx.arc(x, markerY, markerRadius, 0, Math.PI * 2);
        ctx.strokeStyle = style.strokeColor;
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

      timerHitRegions.push({ x: x - markerRadius - 4, y: markerY - markerRadius - 4, w: markerRadius * 2 + 8, h: markerRadius * 2 + 8, ev: it.ev, state: it.state });
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
        const fireAt = getFireAtMs(ev);
        const fireTime = ev.display_fire_time_ms || ev.display_time_ms || formatTimeMs(fireAt, ev.time_mode);
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
    // 自动展开详情区
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

    // 排队中的先显示
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
    // 自动展开详情区
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
    // 同时显示同分类相邻事件
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
    // 自动展开详情区
    if (eventDetail) {
      eventDetail.classList.remove('collapsed');
      isDetailCollapsed = false;
      if (eventDetailToggleSym) eventDetailToggleSym.textContent = '▼';
      savePanelState();
    }
    const evs = cluster.events.slice().sort((a, b) => a.ts - b.ts);
    eventDetailTitle.textContent = '📌 ' + formatTimeMs(cluster.centerTs, cluster.timeMode) + ' (' + evs.length + '个事件)';
    eventDetailBody.innerHTML = '';
    evs.forEach((ev, i) => {
      const d = document.createElement('div');
      d.className = 'event-item ev-' + ev.category + (ev.pending ? ' pending' : '') + (selectedEventId === ev.id ? ' selected' : '');
      d.setAttribute('role', 'button');
      d.setAttribute('tabindex', '0');
      d.dataset.id = ev.id;
      const cfg = CATEGORY_CONFIG[ev.category];
      const cs = ev.code ? formatCode(ev.code) : (ev.codes && ev.codes.length > 0 ? formatCodes(ev.codes) : '-');
      const ds = buildDetailsSummary(ev);
      const ts = ev.category === 'ttl' ? getFireAtMs(ev) : getEventTs(ev);
      const tsDisplay = ev.display_time_ms || formatTimeMs(ts, ev.time_mode);
      d.innerHTML =
        '<span class="ev-time">' + escapeHtml(tsDisplay) + '</span>' +
        '<span class="ev-idx" style="color:#505070;font-size:9px;margin-right:4px;">#' + (i + 1) + '</span>' +
        '<span class="ev-icon" title="' + cfg.label + '" style="color:' + cfg.color + '">' + cfg.icon + '</span>' +
        '<span class="ev-type">' + escapeHtml(ev.event_type) + '</span>' +
        '<span class="ev-code">' + escapeHtml(cs) + '</span>' +
        '<span class="ev-details">' + escapeHtml(ds) + '</span>';
      d.addEventListener('click', function () {
        selectedEventId = ev.id;
        activeCategory = ev.category;
        renderDetailForEvent(ev);
        render();
      });
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

    const tsDisplay = ev.display_time || formatTime(ev.ts, ev.time_mode);
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

  // ===== 总渲染（带节流，避免高频事件下 DOM 抖动）=====
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
      if (!raw) {
        return;
      }
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
          logSystem('事件接收已继续，已刷新 ' + count + ' 条排队事件');
        } else {
          logSystem('事件接收已暂停，新事件进入排队队列');
        }
      });
    }

    if (btnClearEvents) {
      btnClearEvents.addEventListener('click', function () {
        clearEvents();
        logSystem('事件已清空');
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

    // 定时器队列折叠
    if (eventTimerQueue) {
      const header = eventTimerQueue.querySelector('.etp-timer-queue-header');
      if (header) {
        header.addEventListener('click', function () {
          eventTimerQueue.classList.toggle('collapsed');
        });
      }
    }
  }

  // ===== SSE =====
  async function loadRecentEvents() {
    try {
      const res = await fetch('/api/events/recent');
      if (!res.ok) return;
      const data = await res.json();
      const list = Array.isArray(data) ? data : (data.events || data.data || []);
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
        try { const ev = JSON.parse(e.data); addEvent(ev); } catch (err) { console.error('[EventPanel] Parse SSE event failed:', err); }
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

  // ===== 对外接口 =====
  function logSystem(msg) {
    addEvent({ event_type: 'EventLogged', time: new Date().toTimeString().slice(0, 8), details: { message: msg } });
  }

  function setSession(sid) {
    sessionId = sid;
    window.sessionId = sid;
    if (sid) logSystem('Session: ' + sid.slice(0, 8));
  }

  function showEventPanel() {
    if (eventPanel) {
      eventPanel.classList.remove('hidden');
      eventPanel.classList.add('visible');
      isPanelHidden = false;
      savePanelState();
      // 面板可能因之前 hidden/collapsed 导致 Canvas 尺寸为 0，强制重绘
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

  window.logSystemEvent = logSystem;
  window.eventPanelSetSession = setSession;
  window.showEventPanel = showEventPanel;
  window.hideEventPanel = hideEventPanel;
  window.toggleEventPanel = toggleEventPanel;
  window.getEventCount = function() { return totalEventCount; };
  window.MetaSim = { startSim: () => logSystem('Simulation started...'), stopSim: () => logSystem('Simulation stopped'), clearEvents: clearEvents, showPanel: showEventPanel };
  window.eventPanelLoad = function () {};
  window.clearEventPanel = clearEvents;
  window.timelineAddEvent = addEvent;

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

    if (window.AppState && typeof window.AppState.subscribe === 'function') {
      window.AppState.subscribe(function (key) {
        if (key === 'mode' || key === 'simulationReset') {
          if (isSimulationMode()) {
            startTimerPolling();
          } else {
            stopTimerPolling();
          }
          if (key === 'simulationReset') {
            timerQueue = timerQueue.filter(t => t.source !== 'poll');
          }
          scheduleRender();
        }
      });
    }
    if (window.RuntimeState && typeof window.RuntimeState.subscribe === 'function') {
      window.RuntimeState.subscribe(function (key) {
        if (key === 'runtimeState') {
          updateMatrixNowLinePosition();
          scheduleRender();
        }
      });
    }
    if (isSimulationMode()) {
      startTimerPolling();
    }

    loadRecentEvents();
    initSSE();

    setTimeout(function () { logSystem('事件面板就绪'); }, 300);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.addEventListener('beforeunload', closeSSE);
})();
