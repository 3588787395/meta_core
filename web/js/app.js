/**
 * app.js — 应用核心三件套合并文件
 * ============================================================
 * 合并自：
 *   - main.js   (应用主控制器)
 *   - data.js   (PoolDataManager + LRUCache + Charts)
 *   - editor.js (FormulaEditor + RuleEditor + ComprehensiveSettings + ConfigManager)
 *
 * 顶层 IIFE 包裹避免全局污染；内部按文件分节注释分隔。
 * 原 ES module import/export 已转换为 window 全局引用。
 */

(function () {
  'use strict';

  // ─── Global Application State Management ────────────────────────────────────
  // Task 1: 前端仅保留纯 UI 状态（当前模式、仿真运行控制态）。
  // 业务真值源（当前展示时间、计时器队列、事件队列）统一从后端 API/SSE 获取，
  // 不再在前端维护 simulationTime / simStartRealTime 等运行时状态。
  window.AppState = {
    mode: 'design',
    simulationState: 'stopped',
    _subscribers: [],

    setMode: function (newMode) {
      var oldMode = this.mode;
      this.mode = newMode;
      this._notify('mode', newMode, oldMode);
    },

    setSimulationState: function (newState) {
      var oldState = this.simulationState;
      this.simulationState = newState;
      this._notify('simulationState', newState, oldState);
    },

    resetSimulation: function () {
      this.simulationState = 'stopped';
      this._notify('simulationReset', true, false);
    },

    getCurrentDisplayTime: function () {
      return window.RuntimeState ? window.RuntimeState.getCurrentDisplayTime() : Date.now();
    },

    isSimulationMode: function () {
      return this.mode === 'simulation' || this.mode === 'replay';
    },

    subscribe: function (callback) {
      this._subscribers.push(callback);
      var self = this;
      return function unsubscribe() {
        var idx = self._subscribers.indexOf(callback);
        if (idx !== -1) self._subscribers.splice(idx, 1);
      };
    },

    _notify: function (key, newValue, oldValue) {
      this._subscribers.forEach(function (cb) {
        try { cb(key, newValue, oldValue); } catch (e) { console.error('AppState subscriber error:', e); }
      });
    }
  };

  // ─── Runtime State：业务真值源，通过 /api/state/runtime 与 SSE 保持同步 ─────
  window.RuntimeState = {
    mode: 'live',
    displayNowMs: 0,
    displayNowTime: '',
    activeSessionId: null,
    _subscribers: [],
    _pollTimer: null,

    init: function () {
      this._poll();
      this._pollTimer = setInterval(function () { window.RuntimeState._poll(); }, 1000);
    },

    _poll: function () {
      try {
        fetch('/api/state/runtime')
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data && data.success) window.RuntimeState.update(data);
          })
          .catch(function () {});
      } catch (e) { /* ignore */ }
    },

    update: function (data) {
      var old = { mode: this.mode, displayNowMs: this.displayNowMs };
      this.mode = data.mode || this.mode;
      this.displayNowMs = data.display_now_ms || this.displayNowMs;
      this.displayNowTime = data.display_now_time || this.displayNowTime;
      this.activeSessionId = data.active_session_id || this.activeSessionId;
      this._subscribers.forEach(function (cb) {
        try { cb('runtimeState', { mode: this.mode, displayNowMs: this.displayNowMs }, old); } catch (e) {}
      }.bind(this));
    },

    getCurrentDisplayTime: function () {
      return this.displayNowMs || Date.now();
    },

    subscribe: function (callback) {
      this._subscribers.push(callback);
      var self = this;
      return function unsubscribe() {
        var idx = self._subscribers.indexOf(callback);
        if (idx !== -1) self._subscribers.splice(idx, 1);
      };
    }
  };

  window.RuntimeState.init();

  // ─── Keyboard Shortcuts (表驱动) ────────────────────────────────────────────
  function toggleSimPlayPause() {
    if (AppState.mode !== 'simulation') return;
    if (_simAutoStepping) {
      stopSimAutoStep();
    } else {
      startSimAutoStep();
    }
  }

  function simStepForward() {
    if (AppState.mode !== 'simulation') return;
    stopSimAutoStep();
    runSimulationStep(_getSimDelta());
  }

  function simReset() {
    if (AppState.mode !== 'simulation') return;
    var resetBtn = $('simBtnReset');
    if (resetBtn) resetBtn.click();
  }

  function toggleEventPanel() {
    if (typeof window.toggleEventPanel === 'function') {
      window.toggleEventPanel();
    } else {
      var ep = $('eventPanel');
      if (ep) {
        if (ep.classList.contains('hidden') || !ep.classList.contains('visible')) {
          if (typeof window.showEventPanel === 'function') window.showEventPanel();
        } else {
          var closeBtn = $('btnCloseEvents');
          if (closeBtn) closeBtn.click();
        }
      }
    }
  }

  function toggleTimerPanel() {
    var tq = document.getElementById('eventTimerQueue');
    if (tq) tq.classList.toggle('collapsed');
  }

  function showShortcutsHelp() {
    var helpText = '快捷键帮助:\n\n' +
      'Space: 播放/暂停仿真\n' +
      'S: 仿真步进\n' +
      'R: 重置仿真\n' +
      'E: 切换事件面板\n' +
      'T: 切换定时器面板\n' +
      'F: 切换连线模式\n' +
      'Delete/Backspace: 删除选中项\n' +
      'Ctrl+S: 保存\n' +
      'Ctrl+Z: 撤销\n' +
      'Ctrl+Y: 重做\n' +
      '?: 显示帮助\n\n' +
      '注意: 播放/暂停/步进/重置仅在仿真模式下有效';
    alert(helpText);
  }

  var SHORTCUTS = {
    ' ': toggleSimPlayPause,
    's': simStepForward,
    'S': simStepForward,
    'r': simReset,
    'R': simReset,
    'e': toggleEventPanel,
    'E': toggleEventPanel,
    't': toggleTimerPanel,
    'T': toggleTimerPanel,
    '?': showShortcutsHelp,
    '/': showShortcutsHelp
  };

  // === from data.js ===

/**
 * data.js — 池数据与图表合并文件
 * ============
 * 此文件由以下两个模块合并而成：
 *   - PoolDataManager: 池数据管理器 + LRUCache（来源: pool-data.js）
 *   - Charts: BaseChart + KlineChart + IndicatorChart（来源: charts.js）
 *
 * 合并目的：减少文件数量，将数据管理与图表渲染集中管理。
 *
 * ES Module 导出：
 *   - PoolDataManager
 *   - LRUCache
 *
 * 全局导出（Charts IIFE）：
 *   - window.BaseChart
 *   - window.KlineChart
 *   - window.IndicatorChart
 */

// ============================================================================
// ===== 来源: pool-data.js =====
// ============================================================================
/**
 * LRU缓存类 - 支持TTL过期和最大容量限制
 * @param {number} maxSize - 最大缓存条目数
 * @param {number} ttlMs - 过期时间（毫秒），默认5分钟
 */
class LRUCache {
  constructor(maxSize = 100, ttlMs = 300000) {
    this._maxSize = maxSize;
    this._ttlMs = ttlMs;
    this._cache = new Map(); // Map保持插入顺序，完美适配LRU
  }

  get(key) {
    if (!this._cache.has(key)) return undefined;
    var entry = this._cache.get(key);
    // 检查TTL过期
    if (Date.now() - entry.timestamp > this._ttlMs) {
      this._cache.delete(key);
      return undefined;
    }
    // LRU: 移到末尾（最近使用）
    this._cache.delete(key);
    this._cache.set(key, entry);
    return entry.value;
  }

  set(key, value) {
    // 如果已存在，先删除（更新顺序）
    if (this._cache.has(key)) {
      this._cache.delete(key);
    }
    // 容量溢出时删除最老的（Map迭代器第一个）
    if (this._cache.size >= this._maxSize) {
      var oldestKey = this._cache.keys().next().value;
      this._cache.delete(oldestKey);
    }
    this._cache.set(key, { value: value, timestamp: Date.now() });
  }

  has(key) {
    if (!this._cache.has(key)) return false;
    var entry = this._cache.get(key);
    if (Date.now() - entry.timestamp > this._ttlMs) {
      this._cache.delete(key);
      return false;
    }
    return true;
  }

  delete(key) {
    return this._cache.delete(key);
  }

  clear() {
    this._cache.clear();
  }

  cleanExpired() {
    var now = Date.now();
    var self = this;
    this._cache.forEach(function(entry, key) {
      if (now - entry.timestamp > self._ttlMs) {
        self._cache.delete(key);
      }
    });
  }

  get size() {
    return this._cache.size;
  }
}

// 暴露到全局，供 table-driven-panel.js 等其他脚本使用
if (typeof window !== 'undefined') {
  window.LRUCache = LRUCache;
}

class PoolDataManager {
  constructor() {
    this._data = null;
    this._poolId = null;
    this._changeListeners = [];
    this._history = [];
    this._redoStack = [];
    this._maxHistory = 50;
    this._clipboard = [];
    this._stockCache = new LRUCache(100, 300000); // 100条, 5分钟TTL
    this._cacheVersion = 0;       // 服务端缓存版本号，用于检测热加载失效
    this._cellTypeRegistry = null;  // 从 API 加载的配置表
    this._modules = null;           // 从 API 加载的模块定义
    this._dzhTypeMap = null;        // 从 API 加载的 dzh_type_map
    this._defaults = null;          // 从 API 加载的默认参数
    this._flowModeRegistry = null;  // 从 API 加载的 flow_mode_registry
    this._edgeStrategies = null;    // 从 API 加载的 edge_strategies
    this._fieldDefs = null;         // 从 API 加载的 field_definitions（attr 位掩码单一真相源）
    this._isTDX = false;            // 当前是否为TDX池
    this._tdxFilename = null;       // TDX池文件名
  }

  /** 从后端 API 加载所有配置表 */
  async loadRegistry() {
    // Load cell_type_registry
    try {
      var res = await fetch('/api/registry/cell-types');
      if (res.ok) {
        var json = await res.json();
        if (json.data) this._cellTypeRegistry = json.data;
        else this._cellTypeRegistry = json;
      }
    } catch(e) { console.warn('Failed to load cell_type_registry:', e); }

    // Load modules
    try {
      var res = await fetch('/api/registry/modules');
      if (res.ok) {
        var json = await res.json();
        if (json.data) this._modules = json.data;
        else this._modules = json;
      }
    } catch(e) { console.warn('Failed to load modules:', e); }

    // Load dzh_type_map
    try {
      var res = await fetch('/api/registry/dzh-type-map');
      if (res.ok) {
        var json = await res.json();
        if (json.data) this._dzhTypeMap = json.data;
        else this._dzhTypeMap = json;
      }
    } catch(e) { console.warn('Failed to load dzh_type_map:', e); }

    // Load defaults
    try {
      var res = await fetch('/api/registry/defaults');
      var json = await res.json();
      if (json.data) this._defaults = json.data;
    } catch(e) { console.warn('Failed to load defaults:', e); }

    // Load flow_mode_registry
    try {
      var res = await fetch('/api/registry/flow-modes');
      var json = await res.json();
      if (json.data) this._flowModeRegistry = json.data;
    } catch(e) { console.warn('Failed to load flow_mode_registry:', e); }

    // Load edge_strategies
    try {
      var res = await fetch('/api/registry/edge-strategies');
      var json = await res.json();
      if (json.data) this._edgeStrategies = json.data;
    } catch(e) { console.warn('Failed to load edge_strategies:', e); }

    // Load field_definitions（flow attr 位掩码的单一真相源，消除 attr_bits 重复副本）
    try {
      var res = await fetch('/api/registry/field-definitions');
      var json = await res.json();
      if (json.data) this._fieldDefs = json.data;
    } catch(e) { console.warn('Failed to load field_definitions:', e); }
  }

  get data() { return this._data; }
  get poolId() { return this._poolId; }
  get hasData() { return this._data !== null; }

  onChange(fn) {
    this._changeListeners.push(fn);
    return function() { this._changeListeners = this._changeListeners.filter(function(f) { return f !== fn; }); }.bind(this);
  }

  _notify() {
    var self = this;
    this._changeListeners.forEach(function(fn) {
      try { fn(self._data); } catch (e) { console.error('PoolData listener error:', e); }
    });
  }

  _pushHistory(action) {
    this._history.push({ action: action, snapshot: JSON.stringify(this._data) });
    if (this._history.length > this._maxHistory) this._history.shift();
    this._redoStack = [];
  }

  _snapshot() {
    this._pushHistory('snapshot');
  }

  undo() {
    if (this._history.length === 0) return false;
    var entry = this._history.pop();
    this._redoStack.push({ snapshot: JSON.stringify(this._data) });
    this._data = JSON.parse(entry.snapshot);
    this._notify();
    return true;
  }

  redo() {
    if (this._redoStack.length === 0) return false;
    var entry = this._redoStack.pop();
    this._history.push({ snapshot: JSON.stringify(this._data) });
    this._data = JSON.parse(entry.snapshot);
    this._notify();
    return true;
  }

  canUndo() { return this._history.length > 0; }
  canRedo() { return this._redoStack.length > 0; }

  reset() {
    this._data = null;
    this._poolId = null;
    this._history = [];
    this._redoStack = [];
    this._clipboard = [];
    this._stockCache.clear();
    this._notify();
  }

  initNew() {
    this._poolId = null;
    this._data = {
      name: '新股票池',
      nodes: [],
      edges: [],
      pool_meta: { type: 'ss-pool', ver: '1.0', mode: '1', nextid: 100, backcolor: 16777216 },
      trades: [],
      opentrades: []
    };
    this._history = [];
    this._redoStack = [];
    this._notify();
    return this._data;
  }

  initDemo() {
    this._poolId = null;
    var localData = this.loadFromLocal();
    if (localData) {
      this._data = localData;
    } else {
      this._data = this._createDemoPool();
    }
    this._history = [];
    this._redoStack = [];
    this._notify();
    return this._data;
  }

  loadFromLocal() {
    try {
      var key = localStorage.getItem('dzh_pool_latest') || 'dzh_pool_autosave';
      var raw = localStorage.getItem(key);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (parsed && parsed.nodes && Array.isArray(parsed.nodes)) {
        return {
          name: parsed.name || '未命名',
          nodes: parsed.nodes,
          edges: parsed.edges || [],
          pool_meta: parsed.pool_meta || { type: 'ss-pool', ver: '1.0', mode: '1', nextid: 100, backcolor: 16777216 },
          trades: [],
          opentrades: []
        };
      }
      return null;
    } catch (e) {
      return null;
    }
  }

  async loadFromAPI(poolId) {
    var self = this;
    var res = await fetch('/api/pools/' + encodeURIComponent(poolId));
    var json = await res.json();
    if (json.code !== 0) throw new Error(json.msg || '加载失败');
    var poolData = json.data;
    self._poolId = poolId;
    self._isTDX = (poolData.pool_meta && poolData.pool_meta.type === 'tdx');
    self._tdxFilename = self._isTDX ? (poolData.name || poolId || '') + '.xml' : null;
    self.setData(poolData);
    return self._data;
  }

  async saveToAPI() {
    if (!this._data) throw new Error('没有数据可保存');

    // 只有 _poolId 是数据库 UUID 格式时才用 PUT，否则用 POST 创建新池
    var isDBPoolId = this._poolId && /^[0-9a-f]{8,}-/.test(this._poolId);
    var url, method;
    if (isDBPoolId) {
      url = '/api/pools/' + this._poolId;
      method = 'PUT';
    } else {
      url = '/api/pools';
      method = 'POST';
    }

    try {
      var res = await fetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: this._data.name || '未命名',
          pool_type: (this._data.pool_meta && this._data.pool_meta.type === 'tdx') ? 'tdx' : 'dzh',
          nodes: this._data.nodes || [],
          edges: this._data.edges || [],
          pool_meta: this._data.pool_meta || {}
        })
      });
      var json = await res.json();
      if (json.code !== 0) throw new Error(json.msg || '保存失败');

      if (!isDBPoolId && json.data && json.data.pool_id) {
        this._poolId = json.data.pool_id;
      }
      return true;
    } catch (err) {
      return this.saveToLocal();
    }
  }

  saveToLocal() {
    if (!this._data) throw new Error('没有数据可保存');
    try {
      var key = 'dzh_pool_' + (this._poolId || 'autosave');
      var saveData = {
        name: this._data.name || '未命名',
        nodes: this._data.nodes || [],
        edges: this._data.edges || [],
        pool_meta: this._data.pool_meta || {},
        savedAt: new Date().toISOString()
      };
      localStorage.setItem(key, JSON.stringify(saveData));
      if (!this._poolId) {
        localStorage.setItem('dzh_pool_latest', key);
      }
      return true;
    } catch (e) {
      throw new Error('本地保存失败: ' + e.message);
    }
  }

  async importXML(file) {
    var self = this;
    var formData = new FormData();
    formData.append('file', file);
    var res = await fetch('/api/dzh/import-and-save', { method: 'POST', body: formData });
    var json = await res.json();
    if (!json.success) throw new Error(json.error || json.msg || '导入失败');

    var data = json.data || json;
    self._poolId = json.pool_id || null;
    self.setData(data);
    return self._data;
  }

  async exportXML() {
    if (!this._data) throw new Error('没有可导出的数据');

    var config = this._data;
    if (config.pool_meta && config.pool_meta.type) {
    } else {
      config = JSON.parse(JSON.stringify(config));
      config.pool_meta = config.pool_meta || { type: 'ss-pool', ver: '1.0', mode: '1', nextid: 100, backcolor: 16777216 };
    }

    var res = await fetch('/api/dzh/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    });

    if (res.headers.get('content-type') && res.headers.get('content-type').indexOf('xml') !== -1) {
      var blob = await res.blob();
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = (this._data.name || 'pool') + '.xml';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return true;
    } else {
      var json = await res.json();
      throw new Error(json.msg || '导出失败');
    }
  }

  async importTDXXML(file) {
    var self = this;
    var formData = new FormData();
    formData.append('file', file);
    var res = await fetch('/api/dzh/tdx/import', { method: 'POST', body: formData });
    var json = await res.json();
    if (!json.success) throw new Error(json.error || 'TDX导入失败');

    var data = json.data || json;
    // 处理 TDX 导入返回的数据格式，适配前端模型
    if (data.cells && !data.nodes) {
      data.nodes = data.cells;
      delete data.cells;
    }
    if (data.flows && !data.edges) {
      data.edges = data.flows.map(function(f) {
        return {
          id: f.id || (f.from_cell_id + '_' + f.to_cell_id),
          from: f.from_cell_id,
          to: f.to_cell_id,
          attr: f,
          params: f
        };
      });
      delete data.flows;
    }
    // 确保节点 text/label 正确
    (data.nodes || []).forEach(function(n) {
      if (!n.text) {
        n.text = (n.params && n.params.text) || (n.params && n.params.tdx_func && n.params.tdx_func.accode) || '';
      }
      if (!n.label) {
        n.label = n.text || '';
      }
    });
    // 确保 pool_meta 存在
    if (!data.pool_meta) {
      data.pool_meta = { type: 'tdx', ver: '1.0', mode: '1', nextid: 100, backcolor: 16777216 };
    }
    data.pool_meta.type = 'tdx';
    var uploadName = (file.name || 'upload').replace(/\.xml$/i, '');
    self._poolId = uploadName;
    self._isTDX = true;
    self._tdxFilename = file.name || 'upload.xml';
    self.setData(data);
    return self._data;
  }

  async loadTDXPool(name) {
    var self = this;
    var res = await fetch('/api/tdx/pools/' + encodeURIComponent(name) + '/load?_t=' + Date.now());
    var json = await res.json();
    if (!json.success) throw new Error(json.error || 'TDX池加载失败');
    var data = json.data || json;
    if (data.cells && !data.nodes) {
      data.nodes = data.cells;
      delete data.cells;
    }
    if (data.flows && !data.edges) {
      data.edges = data.flows.map(function(f) {
        return {
          id: f.id || (f.from_cell_id + '_' + f.to_cell_id),
          from: f.from_cell_id,
          to: f.to_cell_id,
          attr: f,
          params: f
        };
      });
      delete data.flows;
    }
    // Normalize cell_type to dzh_cell_type for renderer compatibility
    (data.nodes || []).forEach(function(n) {
      if (n.cell_type !== undefined && n.dzh_cell_type === undefined) {
        n.dzh_cell_type = n.cell_type;
      }
      if (!n.label && n.text !== undefined) {
        n.label = n.text;
      }
    });
    // Ensure pool_meta is set for TDX pools
    if (!data.pool_meta) {
      data.pool_meta = { type: 'tdx', ver: '1.0', mode: '1', nextid: 100, backcolor: 16777216 };
    }
    self._poolId = name;  // TDX池名（如"盘后"），用于历史数据API路径
    self._isTDX = true;
    self._tdxFilename = name + '.xml';
    self.setData(data);
    return self._data;
  }

  async executeTDXPool(filename) {
    var body = { filename: filename };
    if (this._data && this._data.nodes) {
      body.pool_data = {
        name: this._data.name || '',
        nodes: this._data.nodes,
        edges: this._data.edges || [],
        pool_meta: this._data.pool_meta || {}
      };
    }
    var res = await fetch('/api/tdx/execute-pool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    var json = await res.json();
    if (!json.success) throw new Error(json.error || 'TDX池执行失败');
    return json.data.node_states || json.data;
  }

  exportTDXXml(data) {
    return fetch('/api/tdx/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data || this._data)
    }).then(function(res) {
      if (!res.ok) throw new Error('导出失败');
      return res.blob();
    }).then(function(blob) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'tdx_pool.xml';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    });
  }

  async importJSON(file) {
    var self = this;
    var formData = new FormData();
    formData.append('file', file);
    var res = await fetch('/api/json/import', { method: 'POST', body: formData });
    var json = await res.json();
    if (!json.success) throw new Error(json.error || 'JSON导入失败');
    var data = json.data;
    self._poolId = null;
    self.setData(data);
    return self._data;
  }

  exportJSON() {
    if (!this._data) return Promise.reject(new Error('没有可导出的数据'));
    var config = this.toExportConfig();
    return fetch('/api/json/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    }).then(function(res) {
      if (!res.ok) throw new Error('导出失败');
      return res.blob();
    }).then(function(blob) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = (this._data ? (this._data.name || 'pool') : 'pool') + '.json';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }.bind(this));
  }

  setData(poolData) {
    this._pushHistory('setData');
    this._data = JSON.parse(JSON.stringify(poolData));
    if (!this._data.nodes) this._data.nodes = [];
    if (!this._data.edges) this._data.edges = [];
    if (!this._data.pool_meta) this._data.pool_meta = { type: 'ss-pool', ver: '1.0', mode: '1', nextid: 100, backcolor: 16777216 };
    this._normalizeData();
    this._notify();
    return this._data;
  }

  _normalizeData() {
    var self = this;
    var maxId = 100;

    (this._data.nodes || []).forEach(function(n) {
      if (!n.position) {
        n.position = { x: n.x || n.x1 || 0, y: n.y || n.y1 || 0, width: n.w || (n.x2 ? (n.x2 - (n.x1 || 0)) : undefined) || n.width || 120, height: n.h || (n.y2 ? (n.y2 - (n.y1 || 0)) : undefined) || n.height || 64 };
        if (n.position.width < 20) n.position.width = 80;
        if (n.position.height < 20) n.position.height = 40;
      }
      if (!n.params) n.params = {};
      // 如果 type 是数字（cell_type），先保存到 dzh_cell_type 再映射为字符串
      if (typeof n.type === 'number' && n.dzh_cell_type === undefined) {
        n.dzh_cell_type = n.type;
      }
      if ((!n.type || typeof n.type === 'number') && n.dzh_cell_type) {
        var ctMap = self._dzhTypeMap ? self._dzhTypeMap.type_map : null;
        if (ctMap) {
          n.type = ctMap[String(n.dzh_cell_type)] || ('type_' + n.dzh_cell_type);
        } else {
          console.warn('dzh_type_map not loaded, using placeholder type for cell_type', n.dzh_cell_type);
          n.type = 'type_' + n.dzh_cell_type;
        }
      }
      var numId = parseInt(n.id);
      if (!isNaN(numId) && numId > maxId) maxId = numId;
      var m = String(n.id).match(/^m_(\d+)$/);
      if (m && parseInt(m[1]) > maxId) maxId = parseInt(m[1]);
      // Ensure label is set from name or text
      if (!n.label) {
        n.label = n.name || n.text || (n.params && n.params.text) || '';
      }
    });

    (this._data.edges || []).forEach(function(e) {
      if (!e.params) e.params = {};
      if (typeof e.source === 'string') e.source = { node_id: e.source };
      if (typeof e.target === 'string') e.target = { node_id: e.target };
      if (e.from && !e.source) e.source = { node_id: e.from };
      if (e.to && !e.target) e.target = { node_id: e.to };
      if (e.attr) {
        if (e.attr.begin !== undefined && e.params.begin === undefined) e.params.begin = e.attr.begin;
        if (e.attr.end !== undefined && e.params.end === undefined) e.params.end = e.attr.end;
        if (e.attr.begint !== undefined && e.params.begint === undefined) e.params.begint = e.attr.begint;
        if (e.attr.endt !== undefined && e.params.endt === undefined) e.params.endt = e.attr.endt;
        if (e.attr.interval !== undefined && e.params.interval_sec === undefined) e.params.interval_sec = e.attr.interval;
        if (e.attr.clr !== undefined && e.params.clr === undefined) e.params.clr = e.attr.clr;
        if (e.attr.mid !== undefined && e.params.mid === undefined) e.params.mid = e.attr.mid;
      }
      // Decode DZH flow attr bit flags from field_definitions.bit_fields.flow（单一真相源，
      // 消除 flow_mode_registry.attr_bits 重复副本——active registry 已仅保留 attr_bits_ref 引用）
      var flowAttr = e.attr ? (parseInt(e.attr.attr) || 0) : 0;
      var flowBits = (self._fieldDefs && self._fieldDefs.bit_fields && self._fieldDefs.bit_fields.flow) || null;
      if (flowBits) {
        Object.keys(flowBits).forEach(function(paramName) {
          var bitInfo = flowBits[paramName];
          var mask = bitInfo.mask_hex ? parseInt(bitInfo.mask_hex, 16) : (1 << bitInfo.bit_position);
          if (flowAttr & mask) e.params[paramName] = true;
        });
      } else if (flowAttr) {
        console.warn('field_definitions.bit_fields.flow not loaded, skipping edge attr decoding');
      }
    });

    if (this._data.pool_meta && this._data.pool_meta.type === 'tdx') {
      var tdxFrontendMap = (self._cellTypeRegistry && self._cellTypeRegistry.tdx_frontend_type_map) || null;
      (this._data.nodes || []).forEach(function(n) {
        // Apply TDX type mapping from cell_type_registry.tdx_frontend_type_map
        if (tdxFrontendMap && n.dzh_cell_type !== undefined) {
          var mappedType = tdxFrontendMap[String(n.dzh_cell_type)];
          if (mappedType) {
            // Guard: type 3 only remaps from state_column (DZH) to tdx_condition
            var skipRemap = (n.dzh_cell_type === 3 && n.type !== 'state_column') ||
                            (n.dzh_cell_type === 1 && n.type === 'tdx_condition');
            if (!skipRemap) {
              n.type = mappedType;
            }
            // Set default label for type 3 if missing
            if (n.dzh_cell_type === 3 && !n.label) {
              n.label = 'TDX条件';
            }
          }
        } else if (n.dzh_cell_type !== undefined) {
          console.warn('cell_type_registry.tdx_frontend_type_map not loaded, skipping TDX type remap for cell_type', n.dzh_cell_type);
        }

        // Move TDX top-level fields into params (API returns them at cell top level)
        if (!n.params) n.params = {};
        var tdxMoveFields = ['tdx_psatt', 'tdx_spinfo', 'tdx_stocks', 'tdx_func',
                             'stocks', 'stk_list', 'clr', 'text', 'clrtext', 'solid',
                             'attr', 'tdx_id'];
        for (var fi = 0; fi < tdxMoveFields.length; fi++) {
          var field = tdxMoveFields[fi];
          if (n[field] !== undefined && n.params[field] === undefined) {
            n.params[field] = n[field];
          }
        }
        // Create aliases for UI layout data_path compatibility
        if (n.params.tdx_psatt) n.params.psatt = n.params.tdx_psatt;
        if (n.params.tdx_spinfo) n.params.spinfo = n.params.tdx_spinfo;
        if (n.params.tdx_func) n.params.func = n.params.tdx_func;
        // Ensure label is set
        if (!n.label && n.text) {
          n.label = n.text;
        }
        if (!n.label && n.params.text) {
          n.label = n.params.text;
        }
        // Ensure TDX-specific fields are accessible at node level for rendering
        if (n.text !== undefined && n.label === undefined) n.label = n.text;
        if (n.clr === undefined && n.params.clr !== undefined) n.clr = n.params.clr;
        if (n.clrtext === undefined && n.params.clrtext !== undefined) n.clrtext = n.params.clrtext;
        if (n.solid === undefined && n.params.solid !== undefined) n.solid = n.params.solid;
      });

      // Normalize TDX-specific edge params
      (this._data.edges || []).forEach(function(e) {
        if (!e.params) e.params = {};
        // Map TDX flow attributes to params (API returns tdx_ prefixed fields)
        if (e.attr) {
          if (e.attr.tdx_tran !== undefined && e.params.tran === undefined) e.params.tran = e.attr.tdx_tran;
          if (e.attr.tdx_clr !== undefined && e.params.clr === undefined) e.params.clr = e.attr.tdx_clr;
          if (e.attr.tdx_size !== undefined && e.params.size === undefined) e.params.size = e.attr.tdx_size;
          if (e.attr.tdx_emptyps !== undefined && e.params.emptyps === undefined) e.params.emptyps = e.attr.tdx_emptyps;
          if (e.attr.tdx_starttype !== undefined && e.params.starttype === undefined) e.params.starttype = e.attr.tdx_starttype;
          if (e.attr.tdx_starttime !== undefined && e.params.starttime === undefined) e.params.starttime = e.attr.tdx_starttime;
          if (e.attr.tdx_starttimetype !== undefined && e.params.starttimetype === undefined) e.params.starttimetype = e.attr.tdx_starttimetype;
          if (e.attr.tdx_starttimehms !== undefined && e.params.starttimehms === undefined) e.params.starttimehms = e.attr.tdx_starttimehms;
          if (e.attr.tdx_cxtype !== undefined && e.params.cxtype === undefined) e.params.cxtype = e.attr.tdx_cxtype;
          if (e.attr.tdx_cxtime !== undefined && e.params.cxtime === undefined) e.params.cxtime = e.attr.tdx_cxtime;
          if (e.attr.tdx_cxtimetype !== undefined && e.params.cxtimetype === undefined) e.params.cxtimetype = e.attr.tdx_cxtimetype;
          if (e.attr.tdx_jgtime !== undefined && e.params.jgtime === undefined) e.params.jgtime = e.attr.tdx_jgtime;
        }
      });
    }

    if (this._data.pool_meta) {
      this._data.pool_meta.nextid = Math.max(maxId + 1, this._data.pool_meta.nextid || 100);
    }
  }

  getNodeById(id) {
    if (!this._data) return null;
    return this._data.nodes.find(function(n) { return String(n.id) === String(id); }) || null;
  }

  getEdgeById(id) {
    if (!this._data) return null;
    return this._data.edges.find(function(e) { return String(e.id) === String(id); }) || null;
  }

  updateNode(id, changes) {
    var node = this.getNodeById(id);
    if (!node) return false;
    this._snapshot();
    var self = this;
    Object.keys(changes).forEach(function(k) {
      if (changes[k] && typeof changes[k] === 'object' && !Array.isArray(changes[k]) &&
          node[k] && typeof node[k] === 'object' && !Array.isArray(node[k])) {
        node[k] = self._deepMerge({}, node[k], changes[k]);
      } else {
        node[k] = changes[k];
      }
    });
    this._notify();
    return true;
  }

  updateNodeParams(id, paramChanges) {
    var node = this.getNodeById(id);
    if (!node) return false;
    if (!node.params) node.params = {};
    this._snapshot();
    var self = this;
    Object.keys(paramChanges).forEach(function(k) {
      // 支持点分路径（如 "tdx_func.nset"），进行深层赋值
      if (k.indexOf('.') >= 0) {
        self._setNested(node.params, k, paramChanges[k]);
        return;
      }
      if (paramChanges[k] && typeof paramChanges[k] === 'object' && !Array.isArray(paramChanges[k]) &&
          node.params[k] && typeof node.params[k] === 'object' && !Array.isArray(node.params[k])) {
        node.params[k] = self._deepMerge({}, node.params[k], paramChanges[k]);
      } else {
        node.params[k] = paramChanges[k];
      }
    });
    this._notify();
    return true;
  }

  // 按点分路径深层赋值，如 _setNested(obj, "tdx_func.nset", 0)
  _setNested(obj, path, value) {
    if (!obj || !path) return;
    var keys = path.split('.');
    var cur = obj;
    for (var i = 0; i < keys.length - 1; i++) {
      if (cur[keys[i]] == null || typeof cur[keys[i]] !== 'object') {
        cur[keys[i]] = {};
      }
      cur = cur[keys[i]];
    }
    var lastKey = keys[keys.length - 1];
    // 若新旧值均为对象，进行深合并以保留未覆盖的字段
    if (value && typeof value === 'object' && !Array.isArray(value) &&
        cur[lastKey] && typeof cur[lastKey] === 'object' && !Array.isArray(cur[lastKey])) {
      cur[lastKey] = this._deepMerge({}, cur[lastKey], value);
    } else {
      cur[lastKey] = value;
    }
  }

  _deepMerge(target) {
    var self = this;
    for (var i = 1; i < arguments.length; i++) {
      var source = arguments[i];
      if (source && typeof source === 'object') {
        Object.keys(source).forEach(function(key) {
          if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
            if (!target[key] || typeof target[key] !== 'object') {
              target[key] = Array.isArray(source[key]) ? [] : {};
            }
            self._deepMerge(target[key], source[key]);
          } else {
            target[key] = source[key];
          }
        });
      }
    }
    return target;
  }

  updateEdge(id, changes) {
    var edge = this.getEdgeById(id);
    if (!edge) return false;
    if (!edge.params) edge.params = {};
    this._snapshot();
    Object.keys(changes).forEach(function(k) { edge.params[k] = changes[k]; });
    this._notify();
    return true;
  }

  addNode(cellType, position) {
    var typeInfo = this._getTypeInfo(cellType);
    var defaultParams = this._getDefaultParams(cellType);
    var newId = String(this._getNextId());

    var node = {
      id: newId,
      type: typeInfo.typeName,
      label: typeInfo.defaultLabel,
      params: defaultParams,
      position: {
        x: Math.max(0, position ? position.x : 80),
        y: Math.max(0, position ? position.y : 40),
        width: (position ? position.width : undefined) || typeInfo.defaultWidth,
        height: (position ? position.height : undefined) || typeInfo.defaultHeight
      },
      dzh_cell_type: cellType
    };

    this._snapshot();
    if (!this._data) {
      this._data = { name: '新股票池', nodes: [], edges: [], pool_meta: { type: 'ss-pool', ver: '1.0', mode: '1', nextid: 100, backcolor: 16777216 }, trades: [], opentrades: [] };
    }
    if (!this._data.nodes) this._data.nodes = [];
    this._data.nodes.push(node);
    this._notify();
    return node;
  }

  createNode(type) {
    var isTdx = this._data && this._data.pool_meta && this._data.pool_meta.type === 'tdx';
    var typeInfo = this._getTypeInfo(type);
    var defaultParams = this._getDefaultParams(type);
    var newId = String(this._getNextId());

    // 计算默认位置：在画布中心偏移随机位置
    var baseX = 100 + Math.floor(Math.random() * 300);
    var baseY = 100 + Math.floor(Math.random() * 200);

    var node = {
      id: newId,
      type: typeInfo.typeName,
      label: typeInfo.defaultLabel,
      params: defaultParams,
      position: {
        x: baseX,
        y: baseY,
        width: typeInfo.defaultWidth,
        height: typeInfo.defaultHeight
      },
      dzh_cell_type: type
    };

    this._snapshot();
    if (!this._data) {
      this._data = { name: '新股票池', nodes: [], edges: [], pool_meta: { type: 'ss-pool', ver: '1.0', mode: '1', nextid: 100, backcolor: 16777216 }, trades: [], opentrades: [] };
    }
    if (!this._data.nodes) this._data.nodes = [];
    this._data.nodes.push(node);
    this._notify();
    return node;
  }

  saveTDXNode(nodeId) {
    var node = this.getNodeById(nodeId);
    if (!node) return Promise.reject(new Error('节点不存在'));

    var currentPoolId = this._poolId;
    var body = { params: node.params || {} };
    if (currentPoolId) {
      body.pool_id = currentPoolId;
    }

    return fetch('/api/dzh/cells/' + encodeURIComponent(nodeId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function(r) { return r.json(); });
  }

  saveTDXEdge(edgeId) {
    var edge = this.getEdgeById(edgeId);
    if (!edge) return Promise.reject(new Error('连线不存在'));

    return fetch('/api/dzh/flows/' + encodeURIComponent(edgeId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ params: edge.params || {} })
    }).then(function(r) { return r.json(); });
  }

  createTDXPool(name) {
    return fetch('/api/tdx/pools', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, backcolor: 1114112 })
    }).then(function(r) { return r.json(); });
  }

  saveTDXPool(name) {
    if (!this._data) return Promise.reject(new Error('没有打开的股票池'));
    var poolData = this._data;
    if (poolData.model_dump) {
      poolData = poolData.model_dump();
    } else if (typeof poolData === 'object') {
      poolData = JSON.parse(JSON.stringify(poolData));
    }
    return fetch('/api/tdx/pools/' + encodeURIComponent(name), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pool_data: poolData })
    }).then(function(r) { return r.json(); });
  }

  deleteTDXPool(name) {
    return fetch('/api/tdx/pools/' + encodeURIComponent(name), {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' }
    }).then(function(r) { return r.json(); });
  }

  removeNode(id) {
    if (!this._data) return false;
    var idx = this._data.nodes.findIndex(function(n) { return n.id === id; });
    if (idx < 0) return false;

    this._snapshot();
    this._data.nodes.splice(idx, 1);

    if (this._data.edges) {
      this._data.edges = this._data.edges.filter(function(e) {
        return (e.source && e.source.node_id !== id) && (e.target && e.target.node_id !== id);
      });
    }
    this._notify();
    return true;
  }

  addEdge(fromId, toId) {
    if (!this._data) return null;
    if (fromId === toId) return null;

    var exists = this._data.edges.some(function(e) {
      return (e.source && e.source.node_id === fromId) && (e.target && e.target.node_id === toId);
    });
    if (exists) return null;

    var edge = {
      id: 'e_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8),
      source: { node_id: fromId },
      target: { node_id: toId },
      params: {
        delete_source: false,
        keep_source: true,
        force_move: false,
        clear_dest_first: false,
        output_constituent: false,
        begin: 0,
        begint: '0',
        end: 0,
        endt: '2147483647',
        interval_sec: 60,
        mid: 0,
        clr: '',
        dzh_attr: 0
      }
    };

    this._snapshot();
    if (!this._data.edges) this._data.edges = [];
    edge.params._order = this._data.edges.length;
    this._data.edges.push(edge);
    this._notify();
    return edge;
  }

  removeEdge(id) {
    if (!this._data) return false;
    var idx = this._data.edges.findIndex(function(e) { return e.id === id; });
    if (idx < 0) return false;

    this._snapshot();
    this._data.edges.splice(idx, 1);
    // 剩余边 _order 重新连续编号（0..n-1）
    this._data.edges.forEach(function(e, i) {
      if (!e.params) e.params = {};
      e.params._order = i;
    });
    this._notify();
    return true;
  }

  // 按给定 ID 顺序重排 edges，重写每条边 _order 为数组索引
  reorderEdges(edgeIds) {
    if (!this._data || !this._data.edges || !edgeIds || !edgeIds.length) return false;
    var edgeMap = {};
    this._data.edges.forEach(function(e) { edgeMap[e.id] = e; });

    this._snapshot();
    var reordered = [];
    var seen = {};
    // 按 edgeIds 顺序取出
    edgeIds.forEach(function(eid, i) {
      var e = edgeMap[eid];
      if (e && !seen[eid]) {
        if (!e.params) e.params = {};
        e.params._order = i;
        reordered.push(e);
        seen[eid] = true;
      }
    });
    // 未列出的边追加到末尾，按原相对顺序
    var nextOrder = reordered.length;
    this._data.edges.forEach(function(e) {
      if (!seen[e.id]) {
        if (!e.params) e.params = {};
        e.params._order = nextOrder++;
        reordered.push(e);
      }
    });
    this._data.edges = reordered;
    this._notify();
    return true;
  }

  // 获取节点的数字 cell type（兼容 DZH/TDX，优先 dzh_cell_type，回退 params.type / node.type）
  // 字符串类型节点（如 'market_source'/'statepool'）通过反向映射转回数字 cell type
  _getNodeCellType(node) {
    if (!node) return null;
    if (node.dzh_cell_type !== undefined && node.dzh_cell_type !== null) {
      return Number(node.dzh_cell_type);
    }
    if (node.params && node.params.type !== undefined && node.params.type !== null) {
      var pt = Number(node.params.type);
      if (!isNaN(pt)) return pt;
    }
    if (node.type !== undefined && node.type !== null) {
      var nt = Number(node.type);
      if (!isNaN(nt)) return nt;
      // 字符串类型 → 数字 cell type 反向映射（与 cell_type_registry.canvas_cell_type_map 对齐）
      var STR_TO_CELL_TYPE = {
        'market_source': 202, 'candidate_dzh': 202,
        'stock_state_pool': 200, 'statepool': 200, 'state_pool': 200,
        'transfer_condition': 201,
        'discard_pool': 4,
        'tdx_candidate': 7,
        'tdx_state_pool': 8,
        'tdx_condition': 3
      };
      if (STR_TO_CELL_TYPE[node.type] !== undefined) return STR_TO_CELL_TYPE[node.type];
    }
    return null;
  }

  // 条件边：源节点是池类型的边（DZH type=0/200/202，TDX type=7/8）
  getConditionEdges() {
    if (!this._data || !this._data.edges || !this._data.nodes) return [];
    var self = this;
    var POOL_TYPES = [0, 200, 202, 7, 8];
    var nodeMap = {};
    this._data.nodes.forEach(function(n) { nodeMap[n.id] = n; });
    return this._data.edges.filter(function(e) {
      var srcId = e.source ? e.source.node_id : (e.from || null);
      if (!srcId) return false;
      var srcNode = nodeMap[srcId];
      if (!srcNode) return false;
      var ct = self._getNodeCellType(srcNode);
      return ct !== null && POOL_TYPES.indexOf(ct) >= 0;
    });
  }

  // 无条件边：源节点是转移条件类型的边（DZH type=201，TDX type=3）
  getUnconditionalEdges() {
    if (!this._data || !this._data.edges || !this._data.nodes) return [];
    var self = this;
    var COND_TYPES = [201, 3];
    var nodeMap = {};
    this._data.nodes.forEach(function(n) { nodeMap[n.id] = n; });
    return this._data.edges.filter(function(e) {
      var srcId = e.source ? e.source.node_id : (e.from || null);
      if (!srcId) return false;
      var srcNode = nodeMap[srcId];
      if (!srcNode) return false;
      var ct = self._getNodeCellType(srcNode);
      return ct !== null && COND_TYPES.indexOf(ct) >= 0;
    });
  }

  // 按条件边编号重排 edges：条件边按新顺序从1开始编号，无条件边不编号（设为0）
  // 重排后：按条件边顺序，每条条件边后跟其对应的无条件边（条件边的 target == 无条件边的 source）
  reorderEdgesByCondition(orderedConditionEdgeIds) {
    if (!this._data || !this._data.edges || !orderedConditionEdgeIds) return false;
    this._snapshot();

    var edges = this._data.edges;
    var edgeMap = {};
    edges.forEach(function(e) { edgeMap[e.id] = e; });

    var reordered = [];
    var seen = {};
    var orderCounter = 1;

    // 按条件边顺序排列，每条条件边后跟其对应的无条件边（成对排列）
    orderedConditionEdgeIds.forEach(function(eid) {
      var e = edgeMap[eid];
      if (e && !seen[eid]) {
        if (!e.params) e.params = {};
        e.params._order = orderCounter++;  // 条件边从1开始编号
        reordered.push(e);
        seen[eid] = true;

        // 找到对应的无条件边（source == 此条件边的 target）
        var tgtId = e.target ? e.target.node_id : (e.to || null);
        if (tgtId) {
          edges.forEach(function(ue) {
            if (!seen[ue.id]) {
              var ueSrcId = ue.source ? ue.source.node_id : (ue.from || null);
              if (ueSrcId === tgtId) {
                if (!ue.params) ue.params = {};
                ue.params._order = 0;  // 无条件边不编号
                reordered.push(ue);
                seen[ue.id] = true;
              }
            }
          });
        }
      }
    });

    // 未配对的无条件边追加到末尾
    edges.forEach(function(e) {
      if (!seen[e.id]) {
        if (!e.params) e.params = {};
        e.params._order = 0;  // 无条件边不编号
        reordered.push(e);
        seen[e.id] = true;
      }
    });

    this._data.edges = reordered;
    this._notify();
    return true;
  }

  // 返回指定边的 _order 值
  getEdgeOrder(edgeId) {
    if (!this._data || !this._data.edges) return -1;
    var edge = this._data.edges.find(function(e) { return e.id === edgeId; });
    if (!edge || !edge.params) return -1;
    var order = edge.params._order;
    return (order !== undefined && order !== null) ? order : -1;
  }

  // 返回按 _order 排序的边列表（副本）
  getEdgesOrdered() {
    if (!this._data || !this._data.edges) return [];
    return this._data.edges.slice().sort(function(a, b) {
      var oa = (a.params && a.params._order !== undefined) ? a.params._order : 9999;
      var ob = (b.params && b.params._order !== undefined) ? b.params._order : 9999;
      return oa - ob;
    });
  }

  duplicateNode(id) {
    var node = this.getNodeById(id);
    if (!node) return null;

    var newPos = {
      x: (node.position ? node.position.x : 0) + 30,
      y: (node.position ? node.position.y : 0) + 30,
      width: node.position ? node.position.width : undefined,
      height: node.position ? node.position.height : undefined
    };

    var newNode = this.addNode(node.dzh_cell_type || 0, newPos);
    if (newNode && node.params) {
      newNode.params = JSON.parse(JSON.stringify(node.params));
      newNode.label = node.label + ' (副本)';
      this._notify();
    }
    return newNode;
  }

  copyToClipboard(id) {
    var node = this.getNodeById(id);
    if (!node) return;
    this._clipboard = [JSON.parse(JSON.stringify(node))];
  }

  cutToClipboard(id) {
    this.copyToClipboard(id);
    this.removeNode(id);
  }

  pasteFromClipboard() {
    var self = this;
    if (this._clipboard.length === 0) return;
    this._snapshot();
    this._clipboard.forEach(function(node) {
      var clone = JSON.parse(JSON.stringify(node));
      clone.id = String(self._getNextId());
      clone.position.x += 20;
      clone.position.y += 20;
      clone.label = (clone.label || '') + ' (粘贴)';
      if (!self._data.nodes) self._data.nodes = [];
      self._data.nodes.push(clone);
    });
    this._notify();
  }

  bringToFront(nodeId) {
    if (!this._data) return;
    var idx = this._data.nodes.findIndex(function(n) { return n.id === nodeId; });
    if (idx < 0) return;
    this._snapshot();
    var node = this._data.nodes.splice(idx, 1)[0];
    this._data.nodes.push(node);
    this._notify();
  }

  sendToBack(nodeId) {
    if (!this._data) return;
    var idx = this._data.nodes.findIndex(function(n) { return n.id === nodeId; });
    if (idx < 0) return;
    this._snapshot();
    var node = this._data.nodes.splice(idx, 1)[0];
    this._data.nodes.unshift(node);
    this._notify();
  }

  updateNodePosition(id, x, y) {
    var node = this.getNodeById(id);
    if (!node) return;
    if (!node.position) node.position = { x: 0, y: 0 };
    node.position.x = Math.max(0, x);
    node.position.y = Math.max(0, y);
    this._notify();
  }

  updateNodePositionQuiet(id, x, y) {
    var node = this.getNodeById(id);
    if (!node) return;
    if (!node.position) node.position = { x: 0, y: 0 };
    node.position.x = Math.max(0, x);
    node.position.y = Math.max(0, y);
  }

  getNodeCount() { return this._data ? (this._data.nodes ? this._data.nodes.length : 0) : 0; }
  getEdgeCount() { return this._data ? (this._data.edges ? this._data.edges.length : 0) : 0; }

  validateFlow(fromNodeId, toNodeId) {
    var fromNode = this.getNodeById(fromNodeId);
    var toNode = this.getNodeById(toNodeId);
    if (!fromNode || !toNode) return { valid: false, reason: '节点不存在' };

    var fv = this._dzhTypeMap ? this._dzhTypeMap.flow_validation : null;
    if (!fv) {
      console.warn('dzh_type_map.flow_validation not loaded, flow validation skipped');
      return { valid: false, reason: '配置表未加载，无法验证流程' };
    }
    var allowedSources = fv.allowed_sources || [];
    var allowedMiddle = fv.allowed_middle || [];
    var allowedTargets = fv.allowed_targets || [];

    var fromCt = fromNode.dzh_cell_type || fromNode.type;
    var toCt = toNode.dzh_cell_type || toNode.type;

    var isSource = allowedSources.indexOf(fromNode.type) !== -1 || fromCt === 202 || fromCt === 200 || fromCt === 7 || fromCt === 8;
    var isMiddleFrom = allowedMiddle.indexOf(fromNode.type) !== -1 || fromCt === 201 || fromCt === 3;
    var isMiddleTo = allowedMiddle.indexOf(toNode.type) !== -1 || toCt === 201 || toCt === 3;
    var isTarget = allowedTargets.indexOf(toNode.type) !== -1 || toCt === 200 || toCt === 4 || toCt === 8;

    var fromName = fromNode.label || fromNode.type;
    var toName = toNode.label || toNode.type;

    if ((isSource || isMiddleFrom) && (isTarget || isMiddleTo)) {
      return { valid: true, fromName: fromName, toName: toName };
    }

    return {
      valid: false,
      reason: '不允许从 "' + fromName + '" 连接到 "' + toName + '"\n规则: 备选池/状态池 → 条件 → 状态池/废弃池'
    };
  }

  _getNextId() {
    var maxId = parseInt(this._data && this._data.pool_meta ? this._data.pool_meta.nextid : 100) || 100;
    var allIds = (this._data ? this._data.nodes : []).map(function(n) {
      var m = String(n.id).match(/^m_(\d+)$/);
      return m ? parseInt(m[1]) : 0;
    });
    if (allIds.length > 0) maxId = Math.max(maxId, Math.max.apply(null, allIds));
    maxId += 1;
    if (this._data && this._data.pool_meta) this._data.pool_meta.nextid = maxId;
    return maxId;
  }

  _getTypeInfo(cellType) {
    var registry = this._cellTypeRegistry;
    var isTDX = this._data && this._data.pool_meta && this._data.pool_meta.type === 'tdx';
    // TDX pool: try tdx_ prefixed type_info first
    if (isTDX && registry && registry.type_info && registry.type_info['tdx_' + cellType]) {
      return registry.type_info['tdx_' + cellType];
    }
    // Try type_info from registry
    if (registry && registry.type_info && registry.type_info[String(cellType)]) {
      return registry.type_info[String(cellType)];
    }
    // TDX pool: try tdx_ prefixed types entry
    if (isTDX && registry && registry.types && registry.types['tdx_' + cellType]) {
      var tdxCt = registry.types['tdx_' + cellType];
      return {
        typeName: tdxCt.type_name || tdxCt.name,
        defaultLabel: tdxCt.name || '类型' + cellType,
        defaultWidth: tdxCt.default_width || 120,
        defaultHeight: tdxCt.default_height || 64
      };
    }
    // Try from types entry
    if (registry && registry.types && registry.types[String(cellType)]) {
      var ct = registry.types[String(cellType)];
      return {
        typeName: ct.type_name || ct.name,
        defaultLabel: ct.name || '类型' + cellType,
        defaultWidth: ct.default_width || 120,
        defaultHeight: ct.default_height || 64
      };
    }
    // Minimal default when registry not loaded
    console.warn('cell_type_registry not loaded, using minimal default for cell_type', cellType);
    return { typeName: 'unknown_' + cellType, defaultLabel: '类型' + cellType, defaultWidth: 80, defaultHeight: 40 };
  }

  _getDefaultParams(cellType) {
    // Try loading from cell_type_registry default_params
    var registry = this._cellTypeRegistry;
    if (registry && registry.types) {
      var typeKey = String(cellType);
      // For TDX types in tdx_ prefixed keys
      if (this._data && this._data.pool_meta && this._data.pool_meta.type === 'tdx') {
        var tdxKey = 'tdx_' + cellType;
        if (registry.types[tdxKey] && registry.types[tdxKey].default_params) {
          return JSON.parse(JSON.stringify(registry.types[tdxKey].default_params));
        }
      }
      if (registry.types[typeKey] && registry.types[typeKey].default_params) {
        return JSON.parse(JSON.stringify(registry.types[typeKey].default_params));
      }
    }
    // Try loading from defaults config
    if (this._defaults && this._defaults.default_params && this._defaults.default_params[cellType]) {
      return JSON.parse(JSON.stringify(this._defaults.default_params[cellType]));
    }
    // Minimal default when registry not loaded
    console.warn('cell_type_registry not loaded, using empty default params for cell_type', cellType);
    return {};
  }

  _createDemoPool() {
    return {
      name: '示例股票池',
      pool_meta: { type: 'ss-pool', ver: '1.0', mode: '1', nextid: 100, backcolor: 16777216 },
      nodes: [
        { id: 'm_100', type: 'market_source', label: 'A股备选池', dzh_cell_type: 202, position: { x: 50, y: 200, width: 70, height: 56 }, params: { markets: ['sh_a', 'sz_a', 'gem'], reload_sec: 300, clr: '', name: 'A股' } },
        { id: 'm_101', type: 'transfer_condition', label: '涨幅条件', dzh_cell_type: 201, position: { x: 200, y: 208, width: 50, height: 32 }, params: { inditype: '', sorttype: '10', attr_int: 0, clr: '', indi: '' } },
        { id: 'm_102', type: 'stock_state_pool', label: '初选池', dzh_cell_type: 200, position: { x: 340, y: 192, width: 110, height: 64 }, params: { hold_sec: 86400, col_list: '2,-1,-2,-3,7,14,8,10,17,45', dzh_attr: { raw: 134217728, bits: { show_overview: false, record_history: true } }, clr: '16744448', text: '', stocknum: 0, deltype: 0, endtime: 0, delstocktype: 0, histana: 0, wizd: '', staattr: 0, enter_action: { type: 0, params: 10000 }, exit_action: { type: 0, params: 10000 }, tradeattr: { accountno: -1, entertradetype: 0, enterrate: 0, enterbuytype: 0, enterbuyvalue: 0, enterbuypricetype: 0, enterselltype: 0, entersellvalue: 0, entersellpricetype: 0, leavetradetype: 0, leaverate: 0, leavebuytype: 0, leavebuyvalue: 0, leavebuypricetype: 0, leaveselltype: 0, leavesellvalue: 0, leavesellpricetype: 0, buycontrolnbhs: 0, buycontrolmbsc: -1 }, stocks: [], anas: [], intersection_status: '', sorttype: '', tmpl: '' } },
        { id: 'm_103', type: 'transfer_condition', label: '量比条件', dzh_cell_type: 201, position: { x: 530, y: 208, width: 50, height: 32 }, params: { inditype: '', sorttype: '', attr_int: 0, clr: '', indi: '' } },
        { id: 'm_104', type: 'stock_state_pool', label: '精选池', dzh_cell_type: 200, position: { x: 670, y: 192, width: 110, height: 64 }, params: { hold_sec: 432000, col_list: '2,-1,-2,-3,7,14,8,10,17,45', dzh_attr: { raw: 150994944, bits: { show_overview: false, record_history: true, alert_sound: true } }, clr: '8404992', text: '', stocknum: 0, deltype: 0, endtime: 0, delstocktype: 0, histana: 0, wizd: '', staattr: 0, enter_action: { type: 0, params: 10000 }, exit_action: { type: 0, params: 10000 }, tradeattr: { accountno: -1, entertradetype: 0, enterrate: 0, enterbuytype: 0, enterbuyvalue: 0, enterbuypricetype: 0, enterselltype: 0, entersellvalue: 0, entersellpricetype: 0, leavetradetype: 0, leaverate: 0, leavebuytype: 0, leavebuyvalue: 0, leavebuypricetype: 0, leaveselltype: 0, leavesellvalue: 0, leavesellpricetype: 0, buycontrolnbhs: 0, buycontrolmbsc: -1 }, stocks: [], anas: [], intersection_status: '', sorttype: '', tmpl: '' } },
        { id: 'm_105', type: 'discard_pool', label: '丢弃池', dzh_cell_type: 4, position: { x: 410, y: 310, width: 56, height: 36 }, params: { clr: '', name: '淘汰' } },
        { id: 'm_106', type: 'text_label', label: '标签', dzh_cell_type: 1, position: { x: 50, y: 130, width: 200, height: 30 }, params: { text: 'DZH 股票池示例 - 所有支持的节点类型', attr_int: 0, clr: '' } }
      ],
      edges: [
        { id: 'e_demo_1', source: { node_id: 'm_100' }, target: { node_id: 'm_101' }, params: { begin: 0, begint: '0', end: 0, endt: '2147483647', interval_sec: 60, mid: 0, keep_source: true, clr: '', dzh_attr: 0 } },
        { id: 'e_demo_2', source: { node_id: 'm_101' }, target: { node_id: 'm_102' }, params: { begin: 0, begint: '0', end: 0, endt: '2147483647', interval_sec: 120, mid: 0, keep_source: true, clr: '', dzh_attr: 0 } },
        { id: 'e_demo_3', source: { node_id: 'm_102' }, target: { node_id: 'm_103' }, params: { begin: 0, begint: '0', end: 0, endt: '2147483647', interval_sec: 60, mid: 0, keep_source: true, clr: '', dzh_attr: 0 } },
        { id: 'e_demo_4', source: { node_id: 'm_103' }, target: { node_id: 'm_104' }, params: { begin: 0, begint: '0', end: 0, endt: '2147483647', interval_sec: 60, mid: 0, keep_source: true, clr: '', dzh_attr: 0 } },
        { id: 'e_demo_5', source: { node_id: 'm_101' }, target: { node_id: 'm_105' }, params: { begin: 0, begint: '0', end: 0, endt: '2147483647', interval_sec: 60, mid: 20, keep_source: false, delete_source: true, clr: '', dzh_attr: 0 } }
      ],
      trades: [],
      opentrades: []
    };
  }

  toExportConfig() {
    if (!this._data) return null;
    return {
      name: this._data.name || '未命名股票池',
      pool_meta: this._data.pool_meta || { type: 'ss-pool', ver: '1.0', mode: '1', nextid: 100, backcolor: 16777216 },
      nodes: (this._data.nodes || []).map(function(n) { return n; }),
      edges: (this._data.edges || []).map(function(e) { return e; }),
      trades: this._data.trades || [],
      opentrades: this._data.opentrades || []
    };
  }

  fetchStockData(nodeId) {
    var self = this;
    return fetch('/api/dzh/cells/' + encodeURIComponent(nodeId) + '/stocks?mode=mock')
      .then(function(r) { return r.json(); })
      .then(function(result) {
        var stockData = result.stocks || result.data || [];
        self._stockCache.set(nodeId, stockData);
        return stockData;
      });
  }

  getCachedStockData(nodeId) {
    var cached = this._stockCache.get(nodeId);
    return cached !== undefined ? cached : null;
  }

  clearStockCache() {
    this._stockCache.clear();
  }

  /**
   * 检查服务端缓存版本号，检测到热加载时自动清除所有前端缓存
   * 应在页面加载、定时轮询或手动触发时调用
   * @returns {Promise<boolean>} 是否发生了缓存清除（版本变更）
   */
  async checkCacheVersion() {
    var self = this;
    try {
      var res = await fetch('/api/registry/cache-version?_t=' + Date.now());
      if (!res.ok) return false;
      var json = await res.json();
      var serverVersion = json.data ? json.data.version : json.version;
      if (serverVersion !== undefined && serverVersion !== self._cacheVersion) {
        self.clearAllCaches();
        self._cacheVersion = serverVersion;
        return true;
      }
      return false;
    } catch (e) {
      console.warn('检查缓存版本失败:', e);
      return false;
    }
  }

  /**
   * 清除所有前端缓存（股票缓存、配置表等）
   * 热加载或手动切换池时调用
   */
  clearAllCaches() {
    this._stockCache.clear();
    this._cellTypeRegistry = null;
    this._modules = null;
    this._dzhTypeMap = null;
    this._defaults = null;
    this._flowModeRegistry = null;
    this._edgeStrategies = null;
  }

  // ─── 流程步骤扁平化查询方法（供 comprehensive-settings-table.js 使用） ───

  /**
   * 返回扁平化的流程步骤数组
   * BFS 遍历邻接矩阵，遇到 type=201 条件节点时透传处理：
   * 不单独成行，将条件信息附加到目标状态池步骤中
   *
   * @returns {Array} 扁平化步骤对象数组
   */
  getFlatFlowSteps() {
    if (!this._data || !this._data.nodes || !this._data.edges) return [];

    var nodes = this._data.nodes;
    var edges = this._data.edges;
    var self = this;

    // 构建节点映射和入/出边索引
    var nodeMap = {};
    var inEdgeMap = {};   // nodeId -> [edge]
    var outEdgeMap = {};  // nodeId -> [edge]
    nodes.forEach(function(n) {
      nodeMap[n.id] = n;
      inEdgeMap[n.id] = [];
      outEdgeMap[n.id] = [];
    });
    edges.forEach(function(e) {
      var fromId = e.source ? e.source.node_id : e.from;
      var toId = e.target ? e.target.node_id : e.to;
      if (fromId && outEdgeMap[fromId]) outEdgeMap[fromId].push(e);
      if (toId && inEdgeMap[toId]) inEdgeMap[toId].push(e);
    });

    // 计算入度
    var inDegree = {};
    nodes.forEach(function(n) { inDegree[n.id] = inEdgeMap[n.id].length; });

    // 找到所有 type=202 或 type=7 且入度为 0 的备选池节点作为起点
    var sources = nodes.filter(function(n) {
      var ct = n.dzh_cell_type;
      return (ct === 202 || ct === 7) && (inDegree[n.id] <= 0);
    });
    // 兜底：如果没有备选池源节点，取第一个非 201 节点
    if (sources.length === 0 && nodes.length > 0) {
      var fallback = nodes.find(function(n) { return n.dzh_cell_type !== 201; });
      if (fallback) sources = [fallback];
    }

    var result = [];
    var visited = new Set();
    var queue = [];

    sources.forEach(function(s) {
      queue.push({ nodeId: s.id, level: 0, upstreamNodeId: null, edgeId: null, conditionNodeId: null, order: 0 });
      visited.add(s.id);
    });

    var levelOrderCounter = {}; // level -> 当前同级顺序计数

    while (queue.length > 0) {
      var cur = queue.shift();
      var cnode = nodeMap[cur.nodeId];
      if (!cnode) continue;

      // 确定行类型
      var stepType = 'property'; // 默认属性行
      var ct = cnode.dzh_cell_type;
      if (ct === 202 || ct === 7) {
        stepType = 'source';
      } else if (cur.conditionNodeId) {
        stepType = 'condition';
      }

      // 初始化当前层级的顺序计数器
      if (levelOrderCounter[cur.level] === undefined) levelOrderCounter[cur.level] = 0;
      var order = levelOrderCounter[cur.level]++;

      // 查找便捷引用
      var edgeRef = cur.edgeId ? edges.find(function(e) { return e.id === cur.edgeId; }) : null;
      var condNodeRef = cur.conditionNodeId ? nodeMap[cur.conditionNodeId] : null;

      result.push({
        stepType: stepType,
        nodeId: cur.nodeId,
        edgeId: cur.edgeId,
        conditionNodeId: cur.conditionNodeId,
        level: cur.level,
        order: order,
        upstreamNodeId: cur.upstreamNodeId,
        node: cnode,
        edge: edgeRef,
        conditionNode: condNodeRef
      });

      // 遍历出边，BFS 展开下游
      var outEdges = outEdgeMap[cur.nodeId] || [];
      var nextLevelOrder = 0;
      outEdges.forEach(function(edge) {
        var toId = edge.target ? edge.target.node_id : edge.to;
        var toNode = nodeMap[toId];
        if (!toNode) return;

        if (toNode.dzh_cell_type === 201 || toNode.type === 'transfer_condition' ||
            toNode.type === 'tdx_condition') {
          // 透传 type=201 节点：找到 201 的出边目标作为真正的下游
          var condOutEdges = outEdgeMap[toId] || [];
          condOutEdges.forEach(function(tEdge) {
            var finalToId = tEdge.target ? tEdge.target.node_id : tEdge.to;
            if (finalToId && !visited.has(finalToId)) {
              visited.add(finalToId);
              queue.push({
                nodeId: finalToId,
                level: cur.level + 1,
                upstreamNodeId: cur.nodeId,
                edgeId: edge.id,
                conditionNodeId: toId,
                order: nextLevelOrder++
              });
            }
          });
          // 标记 201 已访问，避免重复处理
          visited.add(toId);
        } else if (!visited.has(toId)) {
          visited.add(toId);
          queue.push({
            nodeId: toId,
            level: cur.level + 1,
            upstreamNodeId: cur.nodeId,
            edgeId: edge.id,
            conditionNodeId: null,
            order: nextLevelOrder++
          });
        }
      });
    }

    // 未访问的非 201 节点也加入结果（孤立节点）
    nodes.forEach(function(n) {
      if (!visited.has(n.id) && n.dzh_cell_type !== 201 &&
          n.type !== 'transfer_condition' && n.type !== 'tdx_condition') {
        result.push({
          stepType: 'property',
          nodeId: n.id,
          edgeId: null,
          conditionNodeId: null,
          level: 0,
          order: result.length,
          upstreamNodeId: null,
          node: n,
          edge: null,
          conditionNode: null
        });
      }
    });

    return result;
  }

  /**
   * 返回指定节点的完整入流三元组
   * 查找指向该节点的入流路径，识别是否经过 type=201 条件节点中转
   *
   * @param {string} nodeId - 目标节点ID
   * @returns {Object} 入流三元组 {upstreamPool, conditionNode, conditionEdge, transferEdge}
   */
  getIncomingTriple(nodeId) {
    if (!this._data || !this._data.edges || !this._data.nodes) {
      return { upstreamPool: null, conditionNode: null, conditionEdge: null, transferEdge: null };
    }

    var nodeMap = {};
    (this._data.nodes || []).forEach(function(n) { nodeMap[n.id] = n; });

    // 找到指向目标节点的所有入边
    var incoming = this._data.edges.filter(function(e) {
      var toId = e.target ? e.target.node_id : e.to;
      return String(toId) === String(nodeId);
    });
    if (incoming.length === 0) {
      return { upstreamPool: null, conditionNode: null, conditionEdge: null, transferEdge: null };
    }

    var edge = incoming[0];
    var fromId = edge.source ? edge.source.node_id : edge.from;
    var fromNode = nodeMap[String(fromId)];

    // 判断上游节点是否为 type=201 条件节点
    if (fromNode && (fromNode.dzh_cell_type === 201 ||
                     fromNode.type === 'transfer_condition' ||
                     fromNode.type === 'tdx_condition')) {
      // 入流来自条件节点（无条件转移边），找条件的入边（条件转移边）
      var condIncoming = this._data.edges.filter(function(e) {
        var toId = e.target ? e.target.node_id : e.to;
        return String(toId) === String(fromId);
      });
      if (condIncoming.length > 0) {
        var condEdge = condIncoming[0];
        var upstreamId = condEdge.source ? condEdge.source.node_id : condEdge.from;
        var upstreamNode = nodeMap[String(upstreamId)];
        return {
          upstreamPool: upstreamNode || null,
          conditionNode: fromNode,
          conditionEdge: condEdge,
          transferEdge: edge
        };
      }
      // 条件节点无入边，仅有条件→目标的出边
      return {
        upstreamPool: null,
        conditionNode: fromNode,
        conditionEdge: null,
        transferEdge: edge
      };
    }

    // 直接来自另一状态池（无 201 中间节点）
    return {
      upstreamPool: fromNode || null,
      conditionNode: null,
      conditionEdge: edge,
      transferEdge: null
    };
  }

  /**
   * 返回指定节点的所有出边数组
   * 用于判断某个状态池是否有后续流出
   *
   * @param {string} nodeId - 节点ID
   * @returns {Array} 出边数组（引用内部数据，非拷贝）
   */
  getOutgoingEdges(nodeId) {
    if (!this._data || !this._data.edges) return [];
    return this._data.edges.filter(function(e) {
      var fromId = e.source ? e.source.node_id : e.from;
      return String(fromId) === String(nodeId);
    });
  }
}

// ============================================================================
// ===== 来源: charts.js =====
// ============================================================================
/**
 * charts.js - 图表模块合并文件
 * ============
 * 此文件由以下三个模块合并而成：
 *   - BaseChart: Canvas 图表基类（来源: charts/base-chart.js）
 *   - KlineChart: K线图Canvas渲染组件（来源: kline-chart.js）
 *   - IndicatorChart: 指标走势图Canvas渲染组件（来源: indicator-chart.js）
 *
 * 加载顺序：BaseChart → KlineChart → IndicatorChart
 * 继承关系：KlineChart extends BaseChart, IndicatorChart extends BaseChart
 *
 * 全局导出：
 *   - window.BaseChart
 *   - window.KlineChart
 *   - window.IndicatorChart
 */

// ============================================================================
// ===== 来源: charts/base-chart.js =====
// ============================================================================

/**
 * BaseChart - Canvas 图表基类
 * ============
 * 提取 KlineChart 与 IndicatorChart 中明显重复的骨架逻辑：
 *   - DOM 容器创建（wrapper + canvas + infoPanel）
 *   - Canvas resize 处理（DPR 适配）
 *   - 鼠标事件绑定（十字光标 mousemove / mouseleave）
 *   - 坐标转换（数据坐标 ↔ 屏幕坐标）
 *   - 清空画布并绘制背景
 *   - 绘制网格（水平线）
 *
 * 用法：子类通过 ES6 继承复用，可覆盖任意方法实现自定义行为。
 *   class MyChart extends BaseChart {
 *     constructor(container, config) {
 *       super(container, config);
 *       this._defaultHeight = 300;
 *       this._buildDOM({ wrapperClass: 'my-chart-wrapper', ... });
 *       this._bindEvents();
 *     }
 *   }
 */

(function (global) {
  'use strict';

  // ─── 公共颜色常量（子类可覆盖） ──────────────────────────────
  var DEFAULT_COLORS = {
    bg: '#1a1a2e',
    gridLine: 'rgba(255,255,255,0.06)',
    axisText: '#9090a8',
    axisLine: 'rgba(255,255,255,0.12)',
    crosshair: 'rgba(255,255,255,0.3)',
    crosshairLabel: '#4a90d9'
  };

  // ─── BaseChart 类 ────────────────────────────────────────────

  class BaseChart {
    constructor(container, config) {
      this.container = typeof container === 'string'
        ? document.querySelector(container)
        : container;
      this.config = config || {};
      this.defaultProps = this.config.default_props || {};
      this.data = [];
      this._mouseX = -1;
      this._mouseY = -1;
      this._showCrosshair = false;
      this._canvasWidth = 0;
      this._canvasHeight = 0;
      this._dpr = 1;
      this._destroyed = false;
      // 子类可覆盖默认高度
      this._defaultHeight = 300;
      // 子类可覆盖颜色表
      this._colors = Object.assign({}, DEFAULT_COLORS);
    }

    // ─── DOM 容器创建 ────────────────────────────────────────────
    // 子类调用此方法构建骨架，可通过 options 自定义 class 名。
    // 返回 wrapper 元素，子类可在调用前后向 wrapper 追加自定义元素（如工具栏）。

    _buildDOM(options) {
      options = options || {};
      var wrapperClass = options.wrapperClass || 'chart-wrapper';
      var canvasContainerClass = options.canvasContainerClass || 'chart-canvas-container';
      var canvasClass = options.canvasClass || 'chart-canvas';
      var infoPanelClass = options.infoPanelClass || 'chart-info-panel';

      var wrapper = document.createElement('div');
      wrapper.className = wrapperClass;

      // Canvas 容器
      var canvasContainer = document.createElement('div');
      canvasContainer.className = canvasContainerClass;
      var canvas = document.createElement('canvas');
      canvas.className = canvasClass;
      canvasContainer.appendChild(canvas);
      this._canvas = canvas;
      this._canvasContainer = canvasContainer;
      wrapper.appendChild(canvasContainer);

      // 信息浮层
      var infoPanel = document.createElement('div');
      infoPanel.className = infoPanelClass;
      infoPanel.style.display = 'none';
      this._infoPanel = infoPanel;
      wrapper.appendChild(infoPanel);

      this._wrapper = wrapper;
      this.container.appendChild(wrapper);
      this._resizeCanvas();
      return wrapper;
    }

    // ─── Canvas resize 处理（DPR 适配） ──────────────────────────

    _resizeCanvas() {
      var rect = this._canvasContainer.getBoundingClientRect();
      var dpr = window.devicePixelRatio || 1;
      var w = Math.floor(rect.width);
      var h = this.defaultProps.height || this._defaultHeight;
      this._canvas.width = w * dpr;
      this._canvas.height = h * dpr;
      this._canvas.style.width = w + 'px';
      this._canvas.style.height = h + 'px';
      this._canvasWidth = w;
      this._canvasHeight = h;
      this._dpr = dpr;
      var ctx = this._canvas.getContext('2d');
      ctx.scale(dpr, dpr);
    }

    // ─── 鼠标事件绑定（十字光标 + 窗口 resize） ──────────────────
    // 子类如需绑定额外事件（如滚轮缩放、拖拽），可在调用 _bindEvents() 后追加。

    _bindEvents() {
      var self = this;
      var canvas = this._canvas;

      // 十字光标
      canvas.addEventListener('mousemove', function (e) {
        var rect = canvas.getBoundingClientRect();
        self._mouseX = e.clientX - rect.left;
        self._mouseY = e.clientY - rect.top;
        self._showCrosshair = true;
        self.render();
      });

      canvas.addEventListener('mouseleave', function () {
        self._showCrosshair = false;
        self._infoPanel.style.display = 'none';
        self.render();
      });

      // 窗口 resize
      this._resizeHandler = function () {
        self._resizeCanvas();
        self.render();
      };
      window.addEventListener('resize', this._resizeHandler);
    }

    // ─── 清空画布并绘制背景 ──────────────────────────────────────
    // 返回已应用 DPR transform 的 ctx，子类可直接在其上绘制。
    // 注意：调用方需在绘制结束后自行 ctx.restore()。

    _clearCanvas(bgColor) {
      var ctx = this._canvas.getContext('2d');
      var w = this._canvasWidth;
      var h = this._canvasHeight;
      var dpr = this._dpr;
      ctx.save();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = bgColor || this._colors.bg;
      ctx.fillRect(0, 0, w, h);
      return ctx;
    }

    // ─── 绘制空数据提示 ──────────────────────────────────────────

    _drawEmpty(ctx, text) {
      var w = this._canvasWidth;
      var h = this._canvasHeight;
      ctx.fillStyle = this._colors.axisText;
      ctx.font = '13px Microsoft YaHei, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(text || '暂无数据', w / 2, h / 2);
      ctx.restore();
    }

    // ─── 绘制网格（水平线） ──────────────────────────────────────
    // 简单实现：在 [paddingTop, paddingTop+chartHeight] 区间绘制 rows 条水平线。
    // 复杂图表（如 K线图含价格区+成交量区）可覆盖此方法实现自定义网格。

    _drawGrid(ctx, chartWidth, paddingTop, chartHeight, rows) {
      rows = rows || 5;
      ctx.strokeStyle = this._colors.gridLine;
      ctx.lineWidth = 0.5;
      for (var i = 0; i <= rows; i++) {
        var y = paddingTop + (chartHeight / rows) * i;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(chartWidth, y);
        ctx.stroke();
      }
    }

    // ─── 坐标转换：数据值 → Y 屏幕坐标 ──────────────────────────

    _valToY(val, valMin, range, paddingTop, chartHeight) {
      return paddingTop + (1 - (val - valMin) / range) * chartHeight;
    }

    // ─── 坐标转换：数据索引 → X 屏幕坐标 ────────────────────────

    _idxToX(idx, step, dataLength, chartWidth) {
      if (dataLength === 1) return chartWidth / 2;
      return idx * step;
    }

    // ─── 销毁：移除事件监听与 DOM ────────────────────────────────

    destroy() {
      if (this._resizeHandler) {
        window.removeEventListener('resize', this._resizeHandler);
        this._resizeHandler = null;
      }
      if (this._wrapper && this._wrapper.parentNode) {
        this._wrapper.parentNode.removeChild(this._wrapper);
      }
      this._destroyed = true;
    }
  }

  // ─── 导出 ────────────────────────────────────────────────────

  BaseChart.DEFAULT_COLORS = DEFAULT_COLORS;

  if (typeof window !== 'undefined') {
    window.BaseChart = BaseChart;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BaseChart;
  }
})(typeof window !== 'undefined' ? window : this);

// ============================================================================
// ===== 来源: kline-chart.js =====
// ============================================================================

/**
 * KlineChart - K线图Canvas渲染组件
 * ============
 * 蜡烛图 + 成交量柱状图 + MA均线叠加
 * 支持缩放(鼠标滚轮)、平移(拖拽)、十字光标、周期切换
 *
 * 数据格式: {time, open, high, low, close, volume}
 * API: GET /api/kline?stock_code={code}&period={period}
 */

(function (global) {
  'use strict';

  // ─── 默认常量（配置加载失败时的回退值） ──────────────────────
  var DEFAULT_COLORS = {
    bg: '#1a1a2e',
    gridLine: 'rgba(255,255,255,0.06)',
    axisText: '#9090a8',
    axisLine: 'rgba(255,255,255,0.12)',
    upCandle: '#ff4444',
    upCandleFill: '#ff4444',
    downCandle: '#00cc66',
    downCandleFill: '#00cc66',
    crosshair: 'rgba(255,255,255,0.3)',
    crosshairLabel: '#4a90d9',
    volUp: 'rgba(255,68,68,0.6)',
    volDown: 'rgba(0,204,102,0.6)',
    ma5: '#ffcc00',
    ma10: '#00cccc',
    ma20: '#cc66ff',
    ma60: 'rgba(255,255,255,0.5)'
  };

  var DEFAULT_MA_COLORS = {
    5: DEFAULT_COLORS.ma5,
    10: DEFAULT_COLORS.ma10,
    20: DEFAULT_COLORS.ma20,
    60: DEFAULT_COLORS.ma60
  };

  var DEFAULT_PERIOD_MAP = {
    '1min': '1分钟',
    '5min': '5分钟',
    '15min': '15分钟',
    '60min': '60分钟',
    'day': '日线',
    'week': '周线',
    'month': '月线'
  };

  // ─── 运行时常量（从 chart_config.json 加载，回退到默认值） ───
  var COLORS = Object.assign({}, DEFAULT_COLORS);
  var MA_COLORS = Object.assign({}, DEFAULT_MA_COLORS);
  var PERIOD_MAP = Object.assign({}, DEFAULT_PERIOD_MAP);

  // ─── 配置表加载（表驱动：从 chart_config.json 拉取） ──────────
  var _configPromise = null;

  function ensureConfigLoaded() {
    if (_configPromise) return _configPromise;
    _configPromise = fetch('/api/config/tables/chart_config')
      .then(function (r) { return r.json(); })
      .then(function (cfg) {
        if (cfg && cfg.charts && cfg.charts.kline) {
          var k = cfg.charts.kline;
          var c = k.colors || {};
          var mc = k.ma_colors || {};
          COLORS = {
            bg: c.bg,
            gridLine: c.grid_line,
            axisText: c.axis_text,
            axisLine: c.axis_line,
            upCandle: c.up_candle,
            upCandleFill: c.up_candle_fill,
            downCandle: c.down_candle,
            downCandleFill: c.down_candle_fill,
            crosshair: c.crosshair,
            crosshairLabel: c.crosshair_label,
            volUp: c.volume_up,
            volDown: c.volume_down,
            ma5: mc.ma5,
            ma10: mc.ma10,
            ma20: mc.ma20,
            ma60: mc.ma60
          };
          MA_COLORS = {
            5: mc.ma5,
            10: mc.ma10,
            20: mc.ma20,
            60: mc.ma60
          };
          if (k.period_map) {
            PERIOD_MAP = Object.assign({}, k.period_map);
          }
        }
        return cfg;
      })
      .catch(function (err) {
        console.error('[KlineChart] Failed to load chart_config:', err);
        return null;
      });
    return _configPromise;
  }

  // 模块加载时即开始拉取配置
  ensureConfigLoaded();

  // ─── KlineChart 类 ──────────────────────────────────────────

  class KlineChart extends BaseChart {
    constructor(container, config) {
      super(container, config);
      this.stockCode = '';
      this.period = this.defaultProps.period || 'day';
      this.showVolume = this.defaultProps.show_volume !== false;
      this.showMA = this.defaultProps.show_ma || [5, 10, 20, 60];
      this.visibleStart = 0;
      this.visibleCount = 80;
      this.minVisibleCount = 20;
      this.candleWidth = 8;
      this.candleGap = 2;
      this._isDragging = false;
      this._dragStartX = 0;
      this._dragStartVisibleStart = 0;
      this._defaultHeight = 400;

      this._buildDOM({
        wrapperClass: 'kline-chart-wrapper',
        canvasContainerClass: 'kline-canvas-container',
        canvasClass: 'kline-canvas',
        infoPanelClass: 'kline-info-panel'
      });
      this._bindEvents();
    }

    // ─── DOM构建（覆盖：在 BaseChart 基础上追加工具栏） ──────────

    _buildDOM(options) {
      var wrapper = super._buildDOM(options);

      // 工具栏（插入到 canvasContainer 之前）
      var toolbar = document.createElement('div');
      toolbar.className = 'kline-toolbar';
      var periods = ['day', 'week', 'month', '60min', '15min', '5min', '1min'];
      for (var i = 0; i < periods.length; i++) {
        var btn = document.createElement('button');
        btn.className = 'kline-period-btn' + (periods[i] === this.period ? ' active' : '');
        btn.setAttribute('data-period', periods[i]);
        btn.textContent = PERIOD_MAP[periods[i]] || periods[i];
        toolbar.appendChild(btn);
      }
      this._toolbar = toolbar;
      wrapper.insertBefore(toolbar, this._canvasContainer);

      return wrapper;
    }

    // ─── 事件绑定（覆盖：在 BaseChart 基础上追加滚轮/拖拽/周期切换） ─

    _bindEvents() {
      super._bindEvents(); // 十字光标 + resize
      var self = this;
      var canvas = this._canvas;

      // 周期切换
      this._toolbar.addEventListener('click', function (e) {
        var btn = e.target.closest('.kline-period-btn');
        if (!btn) return;
        var period = btn.getAttribute('data-period');
        if (period === self.period) return;
        self.period = period;
        var btns = self._toolbar.querySelectorAll('.kline-period-btn');
        for (var i = 0; i < btns.length; i++) {
          btns[i].classList.toggle('active', btns[i].getAttribute('data-period') === period);
        }
        self.loadData(self.stockCode, period);
      });

      // 鼠标滚轮缩放
      canvas.addEventListener('wheel', function (e) {
        e.preventDefault();
        if (self.data.length === 0) return;
        var delta = e.deltaY > 0 ? 1 : -1;
        var oldCount = self.visibleCount;
        self.visibleCount = Math.max(self.minVisibleCount, Math.min(self.data.length, self.visibleCount + delta * 5));
        if (self.visibleCount !== oldCount) {
          // 以鼠标位置为中心缩放
          var rect = canvas.getBoundingClientRect();
          var mx = e.clientX - rect.left;
          var ratio = mx / self._canvasWidth;
          var centerIdx = self.visibleStart + Math.floor(oldCount * ratio);
          self.visibleStart = Math.max(0, Math.min(self.data.length - self.visibleCount, centerIdx - Math.floor(self.visibleCount * ratio)));
          self.render();
        }
      }, { passive: false });

      // 拖拽平移
      canvas.addEventListener('mousedown', function (e) {
        if (e.button !== 0) return;
        self._isDragging = true;
        self._dragStartX = e.clientX;
        self._dragStartVisibleStart = self.visibleStart;
        canvas.style.cursor = 'grabbing';
      });

      document.addEventListener('mousemove', function (e) {
        if (self._isDragging) {
          var dx = e.clientX - self._dragStartX;
          var candleStep = self.candleWidth + self.candleGap;
          var shift = Math.round(dx / candleStep);
          self.visibleStart = Math.max(0, Math.min(self.data.length - self.visibleCount, self._dragStartVisibleStart - shift));
          self.render();
        }
      });

      document.addEventListener('mouseup', function () {
        if (self._isDragging) {
          self._isDragging = false;
          self._canvas.style.cursor = 'crosshair';
        }
      });
    }

    // ─── 数据加载 ────────────────────────────────────────────────

    loadData(stockCode, period) {
      var self = this;
      this.stockCode = stockCode;
      this.period = period || this.period;

      var url = '/api/kline?stock_code=' + encodeURIComponent(this.stockCode) + '&period=' + encodeURIComponent(this.period);
      fetch(url)
        .then(function (r) { return r.json(); })
        .then(function (result) {
          if (result && result.data && Array.isArray(result.data)) {
            self.data = result.data;
          } else if (Array.isArray(result)) {
            self.data = result;
          } else {
            self.data = [];
          }
          self.visibleStart = Math.max(0, self.data.length - self.visibleCount);
          // 等待配置表加载完成后再渲染，确保使用配置表中的常量
          ensureConfigLoaded().then(function () {
            self.render();
          });
        })
        .catch(function (err) {
          console.error('[KlineChart] Failed to load kline data:', err);
          self.data = [];
          self.render();
        });
    }

    // ─── 主渲染 ──────────────────────────────────────────────────

    render() {
      if (this._destroyed) return;
      var ctx = this._canvas.getContext('2d');
      var w = this._canvasWidth;
      var h = this._canvasHeight;
      var dpr = this._dpr;

      ctx.save();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      // 背景
      ctx.fillStyle = COLORS.bg;
      ctx.fillRect(0, 0, w, h);

      if (this.data.length === 0) {
        ctx.fillStyle = COLORS.axisText;
        ctx.font = '13px Microsoft YaHei, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(this.stockCode ? '加载中...' : '暂无数据', w / 2, h / 2);
        ctx.restore();
        return;
      }

      // 区域划分
      var paddingRight = 70;
      var paddingTop = 10;
      var paddingBottom = 24;
      var chartWidth = w - paddingRight;
      var volHeight = this.showVolume ? h * 0.25 : 0;
      var priceHeight = h - paddingTop - paddingBottom - volHeight;

      // 计算可见数据
      var start = Math.max(0, this.visibleStart);
      var end = Math.min(this.data.length, start + this.visibleCount);
      var visibleData = this.data.slice(start, end);

      if (visibleData.length === 0) {
        ctx.restore();
        return;
      }

      // 自适应蜡烛宽度
      this.candleWidth = Math.max(1, Math.floor((chartWidth - 10) / visibleData.length) - 2);
      this.candleGap = Math.max(1, Math.floor(this.candleWidth * 0.2));
      var totalCandleWidth = (this.candleWidth + this.candleGap) * visibleData.length;
      var offsetX = Math.max(0, (chartWidth - totalCandleWidth) / 2);

      // 价格范围
      var priceMin = Infinity, priceMax = -Infinity;
      var volMax = 0;
      for (var i = 0; i < visibleData.length; i++) {
        var d = visibleData[i];
        if (d.high > priceMax) priceMax = d.high;
        if (d.low < priceMin) priceMin = d.low;
        if (d.volume > volMax) volMax = d.volume;
      }
      // 包含MA范围
      for (var m = 0; m < this.showMA.length; m++) {
        var maPeriod = this.showMA[m];
        var maData = this._calcMA(maPeriod, start, end);
        for (var j = 0; j < maData.length; j++) {
          if (maData[j] !== null) {
            if (maData[j] > priceMax) priceMax = maData[j];
            if (maData[j] < priceMin) priceMin = maData[j];
          }
        }
      }
      var pricePadding = (priceMax - priceMin) * 0.05 || 1;
      priceMin -= pricePadding;
      priceMax += pricePadding;

      // 绘制网格
      this._drawGrid(ctx, chartWidth, paddingTop, priceHeight, volHeight, paddingBottom, priceMin, priceMax, volMax);

      // 绘制成交量
      if (this.showVolume && volHeight > 0) {
        this._drawVolume(ctx, visibleData, offsetX, paddingTop + priceHeight, chartWidth, volHeight, volMax);
      }

      // 绘制蜡烛图
      this._drawCandles(ctx, visibleData, offsetX, paddingTop, chartWidth, priceHeight, priceMin, priceMax);

      // 绘制MA线
      for (var m2 = 0; m2 < this.showMA.length; m2++) {
        var maP = this.showMA[m2];
        var maD = this._calcMA(maP, start, end);
        this._drawMALine(ctx, maD, visibleData, offsetX, paddingTop, priceHeight, priceMin, priceMax, MA_COLORS[maP] || '#ffffff', 'MA' + maP);
      }

      // 十字光标
      if (this._showCrosshair && this._mouseX >= 0 && this._mouseX < chartWidth) {
        this._drawCrosshair(ctx, visibleData, offsetX, paddingTop, priceHeight, volHeight, paddingBottom, chartWidth, w, h, priceMin, priceMax, volMax);
      }

      // Y轴价格标签
      this._drawPriceAxis(ctx, chartWidth, paddingRight, paddingTop, priceHeight, priceMin, priceMax);

      ctx.restore();
    }

    // ─── 网格绘制 ────────────────────────────────────────────────

    _drawGrid(ctx, chartWidth, paddingTop, priceHeight, volHeight, paddingBottom, priceMin, priceMax, volMax) {
      ctx.strokeStyle = COLORS.gridLine;
      ctx.lineWidth = 0.5;

      // 水平线 (价格区)
      var priceRows = 5;
      for (var i = 0; i <= priceRows; i++) {
        var y = paddingTop + (priceHeight / priceRows) * i;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(chartWidth, y);
        ctx.stroke();
      }

      // 水平线 (成交量区)
      if (volHeight > 0) {
        var volTop = paddingTop + priceHeight;
        ctx.beginPath();
        ctx.moveTo(0, volTop);
        ctx.lineTo(chartWidth, volTop);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(0, volTop + volHeight / 2);
        ctx.lineTo(chartWidth, volTop + volHeight / 2);
        ctx.stroke();
      }
    }

    // ─── 蜡烛图绘制 ──────────────────────────────────────────────

    _drawCandles(ctx, visibleData, offsetX, paddingTop, chartWidth, priceHeight, priceMin, priceMax) {
      var priceRange = priceMax - priceMin || 1;
      var step = this.candleWidth + this.candleGap;
      var halfCandle = Math.floor(this.candleWidth / 2);

      for (var i = 0; i < visibleData.length; i++) {
        var d = visibleData[i];
        var x = offsetX + i * step + halfCandle;
        var isUp = d.close >= d.open;
        var color = isUp ? COLORS.upCandle : COLORS.downCandle;
        var fillColor = isUp ? COLORS.upCandleFill : COLORS.downCandleFill;

        // 影线
        var highY = paddingTop + (1 - (d.high - priceMin) / priceRange) * priceHeight;
        var lowY = paddingTop + (1 - (d.low - priceMin) / priceRange) * priceHeight;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, highY);
        ctx.lineTo(x, lowY);
        ctx.stroke();

        // 实体
        var openY = paddingTop + (1 - (d.open - priceMin) / priceRange) * priceHeight;
        var closeY = paddingTop + (1 - (d.close - priceMin) / priceRange) * priceHeight;
        var bodyTop = Math.min(openY, closeY);
        var bodyHeight = Math.max(1, Math.abs(closeY - openY));

        ctx.fillStyle = fillColor;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        if (isUp) {
          // 阳线：空心或实心（这里用实心）
          ctx.fillRect(x - halfCandle, bodyTop, this.candleWidth, bodyHeight);
        } else {
          // 阴线：实心
          ctx.fillRect(x - halfCandle, bodyTop, this.candleWidth, bodyHeight);
        }
      }
    }

    // ─── 成交量绘制 ──────────────────────────────────────────────

    _drawVolume(ctx, visibleData, offsetX, volTop, chartWidth, volHeight, volMax) {
      if (volMax === 0) return;
      var step = this.candleWidth + this.candleGap;
      var halfCandle = Math.floor(this.candleWidth / 2);
      var volRange = volMax * 1.1;

      for (var i = 0; i < visibleData.length; i++) {
        var d = visibleData[i];
        var x = offsetX + i * step;
        var isUp = d.close >= d.open;
        var barHeight = (d.volume / volRange) * volHeight;
        var y = volTop + volHeight - barHeight;

        ctx.fillStyle = isUp ? COLORS.volUp : COLORS.volDown;
        ctx.fillRect(x, y, this.candleWidth, barHeight);
      }
    }

    // ─── MA计算 ──────────────────────────────────────────────────

    _calcMA(period, start, end) {
      var result = [];
      for (var i = start; i < end; i++) {
        if (i < period - 1) {
          result.push(null);
        } else {
          var sum = 0;
          for (var j = i - period + 1; j <= i; j++) {
            sum += this.data[j].close;
          }
          result.push(sum / period);
        }
      }
      return result;
    }

    // ─── MA线绘制 ────────────────────────────────────────────────

    _drawMALine(ctx, maData, visibleData, offsetX, paddingTop, priceHeight, priceMin, priceMax, color, label) {
      var priceRange = priceMax - priceMin || 1;
      var step = this.candleWidth + this.candleGap;
      var halfCandle = Math.floor(this.candleWidth / 2);

      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      var started = false;
      for (var i = 0; i < maData.length; i++) {
        if (maData[i] === null) continue;
        var x = offsetX + i * step + halfCandle;
        var y = paddingTop + (1 - (maData[i] - priceMin) / priceRange) * priceHeight;
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.stroke();

      // MA标签
      if (started && maData.length > 0) {
        var lastIdx = maData.length - 1;
        while (lastIdx >= 0 && maData[lastIdx] === null) lastIdx--;
        if (lastIdx >= 0) {
          var lastX = offsetX + lastIdx * step + halfCandle;
          var lastY = paddingTop + (1 - (maData[lastIdx] - priceMin) / priceRange) * priceHeight;
          ctx.fillStyle = color;
          ctx.font = '10px Microsoft YaHei, sans-serif';
          ctx.textAlign = 'left';
          ctx.fillText(label, lastX + 4, lastY - 3);
        }
      }
    }

    // ─── 十字光标 ────────────────────────────────────────────────

    _drawCrosshair(ctx, visibleData, offsetX, paddingTop, priceHeight, volHeight, paddingBottom, chartWidth, w, h, priceMin, priceMax, volMax) {
      var step = this.candleWidth + this.candleGap;
      var halfCandle = Math.floor(this.candleWidth / 2);
      var mx = this._mouseX;
      var my = this._mouseY;

      // 找到最近的蜡烛
      var candleIdx = Math.round((mx - offsetX - halfCandle) / step);
      if (candleIdx < 0 || candleIdx >= visibleData.length) return;

      var d = visibleData[candleIdx];
      var cx = offsetX + candleIdx * step + halfCandle;

      // 竖线
      ctx.strokeStyle = COLORS.crosshair;
      ctx.lineWidth = 0.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(cx, 0);
      ctx.lineTo(cx, h - paddingBottom);
      ctx.stroke();

      // 横线
      ctx.beginPath();
      ctx.moveTo(0, my);
      ctx.lineTo(chartWidth, my);
      ctx.stroke();
      ctx.setLineDash([]);

      // 信息浮层
      var priceRange = priceMax - priceMin || 1;
      var infoHtml = '<div class="kline-info-stock">' + this._esc(this.stockCode) + '</div>';
      infoHtml += '<div class="kline-info-row"><span>时间</span><span>' + this._esc(String(d.time || '')) + '</span></div>';
      infoHtml += '<div class="kline-info-row"><span>开盘</span><span style="color:' + (d.close >= d.open ? COLORS.upCandle : COLORS.downCandle) + '">' + d.open.toFixed(2) + '</span></div>';
      infoHtml += '<div class="kline-info-row"><span>最高</span><span style="color:' + COLORS.upCandle + '">' + d.high.toFixed(2) + '</span></div>';
      infoHtml += '<div class="kline-info-row"><span>最低</span><span style="color:' + COLORS.downCandle + '">' + d.low.toFixed(2) + '</span></div>';
      infoHtml += '<div class="kline-info-row"><span>收盘</span><span style="color:' + (d.close >= d.open ? COLORS.upCandle : COLORS.downCandle) + '">' + d.close.toFixed(2) + '</span></div>';
      infoHtml += '<div class="kline-info-row"><span>成交量</span><span>' + this._formatVolume(d.volume) + '</span></div>';

      // MA值
      var start = Math.max(0, this.visibleStart);
      var end = Math.min(this.data.length, start + this.visibleCount);
      for (var m = 0; m < this.showMA.length; m++) {
        var maP = this.showMA[m];
        var maD = this._calcMA(maP, start, end);
        if (candleIdx < maD.length && maD[candleIdx] !== null) {
          infoHtml += '<div class="kline-info-row"><span style="color:' + (MA_COLORS[maP] || '#fff') + '">MA' + maP + '</span><span>' + maD[candleIdx].toFixed(2) + '</span></div>';
        }
      }

      this._infoPanel.innerHTML = infoHtml;
      this._infoPanel.style.display = 'block';

      // X轴时间标签
      ctx.fillStyle = COLORS.crosshairLabel;
      ctx.font = '10px Microsoft YaHei, sans-serif';
      ctx.textAlign = 'center';
      var timeLabel = String(d.time || '');
      if (timeLabel.length > 10) timeLabel = timeLabel.substring(0, 10);
      var labelY = h - paddingBottom + 14;
      ctx.fillRect(cx - 32, labelY - 10, 64, 14);
      ctx.fillStyle = '#fff';
      ctx.fillText(timeLabel, cx, labelY);

      // Y轴价格标签
      if (my >= paddingTop && my <= paddingTop + priceHeight) {
        var hoverPrice = priceMax - (my - paddingTop) / priceHeight * (priceMax - priceMin);
        ctx.fillStyle = COLORS.crosshairLabel;
        ctx.fillRect(chartWidth, my - 8, 68, 16);
        ctx.fillStyle = '#fff';
        ctx.font = '10px Consolas, monospace';
        ctx.textAlign = 'left';
        ctx.fillText(hoverPrice.toFixed(2), chartWidth + 4, my + 4);
      }
    }

    // ─── Y轴价格标签 ──────────────────────────────────────────────

    _drawPriceAxis(ctx, chartWidth, paddingRight, paddingTop, priceHeight, priceMin, priceMax) {
      var rows = 5;
      var priceRange = priceMax - priceMin;
      ctx.fillStyle = COLORS.axisText;
      ctx.font = '10px Consolas, monospace';
      ctx.textAlign = 'left';

      for (var i = 0; i <= rows; i++) {
        var price = priceMax - (priceRange / rows) * i;
        var y = paddingTop + (priceHeight / rows) * i;
        ctx.fillText(price.toFixed(2), chartWidth + 6, y + 4);
      }
    }

    // ─── 工具方法 ────────────────────────────────────────────────

    _formatVolume(vol) {
      if (!vol || vol === 0) return '0';
      if (vol >= 100000000) return (vol / 100000000).toFixed(2) + '亿';
      if (vol >= 10000) return (vol / 10000).toFixed(0) + '万';
      return String(vol);
    }

    _esc(s) {
      if (!s) return '';
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // ─── 公共方法 ────────────────────────────────────────────────

    getCurrentData() {
      return {
        stock_code: this.stockCode,
        period: this.period,
        data_count: this.data.length
      };
    }
  }

  // ─── 导出 ────────────────────────────────────────────────────

  global.KlineChart = KlineChart;

})(window);

// ============================================================================
// ===== 来源: indicator-chart.js =====
// ============================================================================

/**
 * IndicatorChart - 指标走势图Canvas渲染组件
 * ================
 * 折线图 + 0轴参考线 + 正负区域着色
 * 支持鼠标悬停十字光标显示数值
 *
 * 数据格式: {time, value}
 * API: GET /api/indicator/values?node_id={id}&formula={formula}
 */

(function (global) {
  'use strict';

  // ─── 默认常量（配置加载失败时的回退值） ──────────────────────
  var DEFAULT_COLORS = {
    bg: '#1a1a2e',
    gridLine: 'rgba(255,255,255,0.06)',
    axisText: '#9090a8',
    axisLine: 'rgba(255,255,255,0.12)',
    zeroLine: 'rgba(255,255,255,0.25)',
    positiveFill: 'rgba(0,204,102,0.15)',
    negativeFill: 'rgba(255,68,68,0.15)',
    lineColor: '#4a9eff',
    crosshair: 'rgba(255,255,255,0.3)',
    crosshairLabel: '#4a90d9',
    dotColor: '#4a9eff'
  };

  var DEFAULT_LAYOUT = {
    paddingRight: 60,
    paddingTop: 10,
    paddingBottom: 24,
    rows: 5
  };

  // ─── 运行时常量（从 chart_config.json 加载，回退到默认值） ───
  var COLORS = Object.assign({}, DEFAULT_COLORS);
  var LAYOUT = Object.assign({}, DEFAULT_LAYOUT);

  // ─── 配置表加载（表驱动：从 chart_config.json 拉取） ──────────
  var _configPromise = null;

  function ensureConfigLoaded() {
    if (_configPromise) return _configPromise;
    _configPromise = fetch('/api/config/tables/chart_config')
      .then(function (r) { return r.json(); })
      .then(function (cfg) {
        if (cfg && cfg.charts && cfg.charts.indicator) {
          var ind = cfg.charts.indicator;
          var c = ind.colors || {};
          var l = ind.layout || {};
          COLORS = {
            bg: c.bg,
            gridLine: c.grid_line,
            axisText: c.axis_text,
            axisLine: c.axis_line,
            zeroLine: c.zero_line,
            positiveFill: c.positive_fill,
            negativeFill: c.negative_fill,
            lineColor: c.line_color,
            crosshair: c.crosshair,
            crosshairLabel: c.crosshair_label,
            dotColor: c.dot_color
          };
          LAYOUT = {
            paddingRight: l.padding_right,
            paddingTop: l.padding_top,
            paddingBottom: l.padding_bottom,
            rows: l.rows
          };
        }
        return cfg;
      })
      .catch(function (err) {
        console.error('[IndicatorChart] Failed to load chart_config:', err);
        return null;
      });
    return _configPromise;
  }

  // 模块加载时即开始拉取配置
  ensureConfigLoaded();

  // ─── IndicatorChart 类 ──────────────────────────────────────

  class IndicatorChart extends BaseChart {
    constructor(container, config) {
      super(container, config);
      this.showZeroLine = this.defaultProps.show_zero_line !== false;
      this.positiveColor = this.defaultProps.positive_color || '#00cc66';
      this.negativeColor = this.defaultProps.negative_color || '#ff4444';
      this.lineColor = this.defaultProps.line_color || '#4a9eff';
      this.nodeId = '';
      this.formula = '';
      this._defaultHeight = 200;

      this._buildDOM({
        wrapperClass: 'indicator-chart-wrapper',
        canvasContainerClass: 'indicator-chart-canvas-container',
        canvasClass: 'indicator-chart-canvas',
        infoPanelClass: 'indicator-chart-info'
      });
      this._bindEvents();
    }

    // ─── 数据加载 ────────────────────────────────────────────────

    loadData(nodeId, formula) {
      var self = this;
      this.nodeId = nodeId;
      this.formula = formula || '';

      var url = '/api/indicator/values?node_id=' + encodeURIComponent(this.nodeId) + '&formula=' + encodeURIComponent(this.formula);
      fetch(url)
        .then(function (r) { return r.json(); })
        .then(function (result) {
          if (result && result.data && Array.isArray(result.data)) {
            self.data = result.data;
          } else if (Array.isArray(result)) {
            self.data = result;
          } else {
            self.data = [];
          }
          // 等待配置表加载完成后再渲染，确保使用配置表中的常量
          ensureConfigLoaded().then(function () {
            self.render();
          });
        })
        .catch(function (err) {
          console.error('[IndicatorChart] Failed to load indicator data:', err);
          self.data = [];
          self.render();
        });
    }

    // ─── 主渲染 ──────────────────────────────────────────────────

    render() {
      if (this._destroyed) return;
      var ctx = this._canvas.getContext('2d');
      var w = this._canvasWidth;
      var h = this._canvasHeight;
      var dpr = this._dpr;

      ctx.save();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      // 背景
      ctx.fillStyle = COLORS.bg;
      ctx.fillRect(0, 0, w, h);

      if (this.data.length === 0) {
        ctx.fillStyle = COLORS.axisText;
        ctx.font = '13px Microsoft YaHei, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(this.nodeId ? '加载中...' : '暂无数据', w / 2, h / 2);
        ctx.restore();
        return;
      }

      // 区域划分
      var paddingRight = LAYOUT.paddingRight;
      var paddingTop = LAYOUT.paddingTop;
      var paddingBottom = LAYOUT.paddingBottom;
      var chartWidth = w - paddingRight;
      var chartHeight = h - paddingTop - paddingBottom;

      // 计算数据范围
      var valMin = Infinity, valMax = -Infinity;
      for (var i = 0; i < this.data.length; i++) {
        var v = this.data[i].value;
        if (v !== null && v !== undefined) {
          if (v < valMin) valMin = v;
          if (v > valMax) valMax = v;
        }
      }

      // 确保零线可见
      if (this.showZeroLine) {
        if (valMin > 0) valMin = 0;
        if (valMax < 0) valMax = 0;
      }

      // 10% padding
      var range = valMax - valMin || 1;
      var padding = range * 0.1;
      valMin -= padding;
      valMax += padding;
      range = valMax - valMin;

      // 计算每个数据点的X位置
      var step = chartWidth / Math.max(1, this.data.length - 1);
      if (this.data.length === 1) step = 0;

      // 坐标转换函数
      function valToY(val) {
        return paddingTop + (1 - (val - valMin) / range) * chartHeight;
      }
      function idxToX(idx) {
        return this.data.length === 1 ? chartWidth / 2 : idx * step;
      }

      // 绘制网格
      this._drawGrid(ctx, chartWidth, paddingTop, chartHeight);

      // 绘制0轴参考线
      if (this.showZeroLine && valMin <= 0 && valMax >= 0) {
        var zeroY = valToY(0);
        ctx.strokeStyle = COLORS.zeroLine;
        ctx.lineWidth = 1;
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        ctx.moveTo(0, zeroY);
        ctx.lineTo(chartWidth, zeroY);
        ctx.stroke();
        ctx.setLineDash([]);

        // 0轴标签
        ctx.fillStyle = COLORS.axisText;
        ctx.font = '10px Consolas, monospace';
        ctx.textAlign = 'left';
        ctx.fillText('0', chartWidth + 6, zeroY + 4);
      }

      // 绘制正负区域着色
      this._drawAreaFill(ctx, valToY, idxToX, chartWidth, zeroY);

      // 绘制指标线
      this._drawLine(ctx, valToY, idxToX);

      // 十字光标
      if (this._showCrosshair && this._mouseX >= 0 && this._mouseX < chartWidth) {
        this._drawCrosshair(ctx, valToY, idxToX, chartWidth, w, h, paddingTop, chartHeight, paddingBottom, valMin, valMax);
      }

      // Y轴标签
      this._drawYAxis(ctx, chartWidth, paddingRight, paddingTop, chartHeight, valMin, valMax);

      // X轴时间标签
      this._drawXAxis(ctx, paddingTop, chartHeight, paddingBottom);

      ctx.restore();
    }

    // ─── 正负区域着色 ────────────────────────────────────────────

    _drawAreaFill(ctx, valToY, idxToX, chartWidth, zeroY) {
      if (this.data.length < 2) return;

      var self = this;

      // 正区域着色 (值 > 0 的部分)
      ctx.beginPath();
      var started = false;
      for (var i = 0; i < this.data.length; i++) {
        var val = this.data[i].value;
        if (val === null || val === undefined) continue;
        var x = idxToX.call(self, i);
        var y = valToY(Math.max(0, val));
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      }
      // 沿0轴返回
      for (var j = this.data.length - 1; j >= 0; j--) {
        var val2 = this.data[j].value;
        if (val2 === null || val2 === undefined) continue;
        var x2 = idxToX.call(self, j);
        var y2 = valToY(0);
        ctx.lineTo(x2, y2);
      }
      ctx.closePath();
      ctx.fillStyle = COLORS.positiveFill;
      ctx.fill();

      // 负区域着色 (值 < 0 的部分)
      ctx.beginPath();
      started = false;
      for (var k = 0; k < this.data.length; k++) {
        var val3 = this.data[k].value;
        if (val3 === null || val3 === undefined) continue;
        var x3 = idxToX.call(self, k);
        var y3 = valToY(Math.min(0, val3));
        if (!started) {
          ctx.moveTo(x3, y3);
          started = true;
        } else {
          ctx.lineTo(x3, y3);
        }
      }
      // 沿0轴返回
      for (var l = this.data.length - 1; l >= 0; l--) {
        var val4 = this.data[l].value;
        if (val4 === null || val4 === undefined) continue;
        var x4 = idxToX.call(self, l);
        var y4 = valToY(0);
        ctx.lineTo(x4, y4);
      }
      ctx.closePath();
      ctx.fillStyle = COLORS.negativeFill;
      ctx.fill();
    }

    // ─── 指标线绘制 ──────────────────────────────────────────────

    _drawLine(ctx, valToY, idxToX) {
      if (this.data.length < 2) return;

      var self = this;
      ctx.strokeStyle = this.lineColor;
      ctx.lineWidth = 1.5;
      ctx.lineJoin = 'round';
      ctx.beginPath();

      var started = false;
      for (var i = 0; i < this.data.length; i++) {
        var val = this.data[i].value;
        if (val === null || val === undefined) continue;
        var x = idxToX.call(self, i);
        var y = valToY(val);
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          // 使用二次贝塞尔曲线平滑
          var prevVal = this.data[i - 1].value;
          if (prevVal !== null && prevVal !== undefined) {
            var prevX = idxToX.call(self, i - 1);
            var prevY = valToY(prevVal);
            var cpx = (prevX + x) / 2;
            ctx.quadraticCurveTo(prevX + (x - prevX) * 0.5, prevY, cpx, (prevY + y) / 2);
            ctx.quadraticCurveTo(cpx, y, x, y);
          } else {
            ctx.lineTo(x, y);
          }
        }
      }
      ctx.stroke();
    }

    // ─── 十字光标 ────────────────────────────────────────────────

    _drawCrosshair(ctx, valToY, idxToX, chartWidth, w, h, paddingTop, chartHeight, paddingBottom, valMin, valMax) {
      var self = this;
      var mx = this._mouseX;
      var my = this._mouseY;

      // 找到最近的数据点
      var step = this.data.length > 1 ? chartWidth / (this.data.length - 1) : chartWidth;
      var dataIdx = this.data.length === 1 ? 0 : Math.round(mx / step);
      if (dataIdx < 0 || dataIdx >= this.data.length) return;

      var d = this.data[dataIdx];
      if (!d || d.value === null || d.value === undefined) return;

      var cx = idxToX.call(self, dataIdx);
      var cy = valToY(d.value);

      // 竖线
      ctx.strokeStyle = COLORS.crosshair;
      ctx.lineWidth = 0.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(cx, 0);
      ctx.lineTo(cx, h - paddingBottom);
      ctx.stroke();

      // 横线
      ctx.beginPath();
      ctx.moveTo(0, my);
      ctx.lineTo(chartWidth, my);
      ctx.stroke();
      ctx.setLineDash([]);

      // 数据点高亮圆点
      ctx.fillStyle = COLORS.dotColor;
      ctx.beginPath();
      ctx.arc(cx, cy, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 1;
      ctx.stroke();

      // 信息浮层
      var isPositive = d.value >= 0;
      var valueColor = isPositive ? this.positiveColor : this.negativeColor;
      var infoHtml = '<div class="indicator-chart-info-row"><span>时间</span><span>' + this._esc(String(d.time || '')) + '</span></div>';
      infoHtml += '<div class="indicator-chart-info-row"><span>数值</span><span style="color:' + valueColor + '">' + d.value.toFixed(4) + '</span></div>';

      this._infoPanel.innerHTML = infoHtml;
      this._infoPanel.style.display = 'block';

      // X轴时间标签
      ctx.fillStyle = COLORS.crosshairLabel;
      ctx.font = '10px Microsoft YaHei, sans-serif';
      ctx.textAlign = 'center';
      var timeLabel = String(d.time || '');
      if (timeLabel.length > 10) timeLabel = timeLabel.substring(0, 10);
      var labelY = h - paddingBottom + 14;
      ctx.fillRect(cx - 32, labelY - 10, 64, 14);
      ctx.fillStyle = '#fff';
      ctx.fillText(timeLabel, cx, labelY);

      // Y轴数值标签
      if (my >= paddingTop && my <= paddingTop + chartHeight) {
        var hoverVal = valMax - (my - paddingTop) / chartHeight * (valMax - valMin);
        ctx.fillStyle = COLORS.crosshairLabel;
        ctx.fillRect(chartWidth, my - 8, 56, 16);
        ctx.fillStyle = '#fff';
        ctx.font = '10px Consolas, monospace';
        ctx.textAlign = 'left';
        ctx.fillText(hoverVal.toFixed(2), chartWidth + 4, my + 4);
      }
    }

    // ─── Y轴标签 ────────────────────────────────────────────────

    _drawYAxis(ctx, chartWidth, paddingRight, paddingTop, chartHeight, valMin, valMax) {
      var rows = 5;
      var range = valMax - valMin;
      ctx.fillStyle = COLORS.axisText;
      ctx.font = '10px Consolas, monospace';
      ctx.textAlign = 'left';

      for (var i = 0; i <= rows; i++) {
        var val = valMax - (range / rows) * i;
        var y = paddingTop + (chartHeight / rows) * i;
        var label = Math.abs(val) >= 1000 ? val.toFixed(0) : val.toFixed(2);
        ctx.fillText(label, chartWidth + 6, y + 4);
      }
    }

    // ─── X轴时间标签 ──────────────────────────────────────────────

    _drawXAxis(ctx, paddingTop, chartHeight, paddingBottom) {
      if (this.data.length === 0) return;

      var maxLabels = Math.min(8, this.data.length);
      var interval = Math.max(1, Math.floor(this.data.length / maxLabels));

      ctx.fillStyle = COLORS.axisText;
      ctx.font = '10px Microsoft YaHei, sans-serif';
      ctx.textAlign = 'center';

      var chartWidth = this._canvasWidth - 60;
      var step = this.data.length > 1 ? chartWidth / (this.data.length - 1) : 0;
      var y = paddingTop + chartHeight + paddingBottom - 6;

      for (var i = 0; i < this.data.length; i += interval) {
        var timeStr = String(this.data[i].time || '');
        if (timeStr.length > 10) timeStr = timeStr.substring(0, 10);
        var x = this.data.length === 1 ? chartWidth / 2 : i * step;
        ctx.fillText(timeStr, x, y);
      }
    }

    // ─── 工具方法 ────────────────────────────────────────────────

    _esc(s) {
      if (!s) return '';
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // ─── 公共方法 ────────────────────────────────────────────────

    getCurrentData() {
      return {
        node_id: this.nodeId,
        formula: this.formula,
        data_count: this.data.length
      };
    }
  }

  // ─── 导出 ────────────────────────────────────────────────────

  global.IndicatorChart = IndicatorChart;

})(window);

window.PoolDataManager = (typeof PoolDataManager !== 'undefined') ? PoolDataManager : null;

  // === from editor.js ===

/**
 * editor.js — 编辑器模块合并文件
 * ============
 * 此文件由以下四个模块合并而成：
 *   - FormulaEditor: 公式编辑器核心逻辑（来源: formula-editor.js，原 editor.js）
 *   - RuleEditor: 可视化规则配置界面（来源: rule-editor.js，原 editor.js）
 *   - ComprehensiveSettings: 股票池流程设置窗口控制器（来源: comprehensive-settings.js）
 *   - ConfigManager: 配置中心管理器（来源: config-manager.js）
 *
 * 合并目的：减少文件数量，将编辑器/配置/设置类模块集中管理。
 * 各模块各自独立，保持原有的全局导出。
 *
 * 全局导出：
 *   - window.FormulaEditor
 *   - window.ruleEditor
 *   - window.ComprehensiveSettings
 *   - window.ConfigManager (配置中心)
 */

// ============================================================================
// ===== 来源: editor.js (FormulaEditor + RuleEditor) =====
// ============================================================================
/**
 * editor.js - 编辑器模块合并文件
 * ============
 * 此文件由以下两个模块合并而成：
 *   - FormulaEditor: 公式编辑器核心逻辑（来源: formula-editor.js）
 *   - RuleEditor: 可视化规则配置界面（来源: rule-editor.js）
 *
 * 合并目的：减少文件数量，将功能相似的编辑器模块集中管理。
 * 两个模块各自独立，保持原有的全局导出（window.FormulaEditor, window.ruleEditor）。
 */

// ============================================================================
// ===== 来源: formula-editor.js =====
// ============================================================================

/**
 * FormulaEditor - 公式编辑器核心逻辑
 * 管理公式列表的加载、选择、编辑、保存、删除、测试
 */
var FormulaEditor = (function () {
  'use strict';

  // ===== State =====
  var formulas = [];
  var currentFormula = null;
  var isNewMode = false;
  var currentFilter = 'all';
  var searchQuery = '';

  // ===== API Key (from localStorage or default) =====
  function getApiKey() {
    return localStorage.getItem('metacore_api_key') || '';
  }

  // ===== API Helpers =====
  function apiGet(url) {
    var headers = { 'Content-Type': 'application/json' };
    var apiKey = getApiKey();
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }
    return fetch(url, { method: 'GET', headers: headers }).then(function (res) {
      if (!res.ok) {
        return res.json().then(function (err) {
          throw new Error(err.detail || err.message || '请求失败: ' + res.status);
        });
      }
      return res.json();
    });
  }

  function apiPost(url, data) {
    var headers = { 'Content-Type': 'application/json' };
    var apiKey = getApiKey();
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }
    return fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(data)
    }).then(function (res) {
      if (!res.ok) {
        return res.json().then(function (err) {
          throw new Error(err.detail || err.message || '请求失败: ' + res.status);
        });
      }
      return res.json();
    });
  }

  function apiPut(url, data) {
    var headers = { 'Content-Type': 'application/json' };
    var apiKey = getApiKey();
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }
    return fetch(url, {
      method: 'PUT',
      headers: headers,
      body: JSON.stringify(data)
    }).then(function (res) {
      if (!res.ok) {
        return res.json().then(function (err) {
          throw new Error(err.detail || err.message || '请求失败: ' + res.status);
        });
      }
      return res.json();
    });
  }

  function apiDelete(url) {
    var headers = { 'Content-Type': 'application/json' };
    var apiKey = getApiKey();
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }
    return fetch(url, {
      method: 'DELETE',
      headers: headers
    }).then(function (res) {
      if (!res.ok) {
        return res.json().then(function (err) {
          throw new Error(err.detail || err.message || '请求失败: ' + res.status);
        });
      }
      return res.json();
    });
  }

  // ===== Toast =====
  function showToast(message, type) {
    type = type || 'success';
    var existing = document.querySelector('.formula-toast');
    if (existing) {
      existing.remove();
    }
    var toast = document.createElement('div');
    toast.className = 'formula-toast formula-toast-' + type;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(function () {
      toast.remove();
    }, 3000);
  }

  // ===== Category Labels =====
  var categoryLabels = {
    'indicator': '指标公式',
    'xg': '选股公式',
    'exp': '专家系统'
  };

  var categoryTags = {
    'indicator': 'indicator',
    'xg': 'xg',
    'exp': 'exp'
  };

  // ===== Load Formula List =====
  function loadFormulaList() {
    apiGet('/api/formula/list').then(function (data) {
      formulas = data.formulas || data || [];
      if (!Array.isArray(formulas)) {
        formulas = [];
      }
      renderFormulaList();
    }).catch(function (err) {
      console.error('加载公式列表失败:', err);
      showToast('加载公式列表失败: ' + err.message, 'error');
      formulas = [];
      renderFormulaList();
    });
  }

  function getFilteredFormulas() {
    return formulas.filter(function (f) {
      // Filter by search
      if (searchQuery) {
        var name = (f.name || '').toLowerCase();
        var desc = (f.description || '').toLowerCase();
        var q = searchQuery.toLowerCase();
        if (name.indexOf(q) === -1 && desc.indexOf(q) === -1) {
          return false;
        }
      }
      // Filter by category
      if (currentFilter !== 'all') {
        if ((f.category || f.type || 'indicator') !== currentFilter) {
          return false;
        }
      }
      return true;
    });
  }

  function renderFormulaList() {
    var listEl = document.getElementById('formulaList');
    if (!listEl) return;

    var filtered = getFilteredFormulas();

    if (filtered.length === 0) {
      listEl.innerHTML = '<div class="formula-empty-list">' +
        '<div class="empty-icon">📋</div>' +
        '<div class="empty-text">' + (searchQuery ? '没有匹配的公式' : '暂无公式<br>点击下方按钮新建') + '</div>' +
        '</div>';
      return;
    }

    var html = '';
    filtered.forEach(function (f) {
      var name = f.name || '未命名';
      var desc = f.description || '';
      var category = f.category || f.type || 'indicator';
      var isBuiltin = f.is_builtin || f.builtin || false;
      var isActive = currentFormula && currentFormula.id === f.id;

      var tagClass = isBuiltin ? 'builtin' : 'custom';
      var tagText = isBuiltin ? '内置' : '自定义';
      var catTagClass = categoryTags[category] || 'indicator';
      var catLabel = categoryLabels[category] || category;

      html += '<div class="formula-list-item' + (isActive ? ' active' : '') + '" data-id="' + f.id + '">' +
        '<div class="formula-item-name">' + escapeHtml(name) + '</div>' +
        '<div class="formula-item-meta">' +
        '<span class="formula-item-tag ' + tagClass + '">' + tagText + '</span>' +
        '<span class="formula-item-tag ' + catTagClass + '">' + catLabel + '</span>' +
        '</div>' +
        (desc ? '<div class="formula-item-desc">' + escapeHtml(desc) + '</div>' : '') +
        '</div>';
    });

    listEl.innerHTML = html;

    // Bind click events
    var items = listEl.querySelectorAll('.formula-list-item');
    items.forEach(function (item) {
      item.addEventListener('click', function () {
        var id = parseInt(this.getAttribute('data-id'));
        var formula = formulas.find(function (f) { return f.id === id; });
        if (formula) {
          selectFormula(formula);
        }
      });
    });
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ===== Select Formula =====
  function selectFormula(formula) {
    currentFormula = formula;
    isNewMode = false;

    // Show editor form, hide placeholder
    var placeholder = document.getElementById('formulaPlaceholder');
    var editorForm = document.getElementById('formulaEditorForm');
    if (placeholder) placeholder.style.display = 'none';
    if (editorForm) editorForm.style.display = 'block';

    // Update title
    var titleEl = document.getElementById('formulaEditorTitle');
    if (titleEl) {
      titleEl.textContent = '编辑公式 - ' + (formula.name || '未命名');
    }

    // Fill fields
    var nameEl = document.getElementById('formulaName');
    var catEl = document.getElementById('formulaCategory');
    var descEl = document.getElementById('formulaDesc');
    var scriptEl = document.getElementById('formulaScript');

    if (nameEl) nameEl.value = formula.name || '';
    if (catEl) catEl.value = formula.category || formula.type || 'indicator';
    if (descEl) descEl.value = formula.description || '';
    if (scriptEl) scriptEl.value = formula.script || formula.code || '';

    // Render args
    renderArgsEditor(formula.args || formula.params || formula.parameters || []);

    // Update list highlight
    renderFormulaList();

    // Clear test results
    var resultEl = document.getElementById('formulaTestResult');
    if (resultEl) resultEl.innerHTML = '';
  }

  // ===== New Formula =====
  function newFormula() {
    currentFormula = null;
    isNewMode = true;

    var placeholder = document.getElementById('formulaPlaceholder');
    var editorForm = document.getElementById('formulaEditorForm');
    if (placeholder) placeholder.style.display = 'none';
    if (editorForm) editorForm.style.display = 'block';

    var titleEl = document.getElementById('formulaEditorTitle');
    if (titleEl) {
      titleEl.textContent = '新建公式';
    }

    // Clear fields
    var nameEl = document.getElementById('formulaName');
    var catEl = document.getElementById('formulaCategory');
    var descEl = document.getElementById('formulaDesc');
    var scriptEl = document.getElementById('formulaScript');

    if (nameEl) nameEl.value = '';
    if (catEl) catEl.value = 'indicator';
    if (descEl) descEl.value = '';
    if (scriptEl) scriptEl.value = '';

    // Clear args
    renderArgsEditor([]);

    // Update list highlight
    renderFormulaList();

    // Clear test results
    var resultEl = document.getElementById('formulaTestResult');
    if (resultEl) resultEl.innerHTML = '';
  }

  // ===== Args Editor =====
  function renderArgsEditor(args) {
    args = args || [];
    var container = document.getElementById('formulaArgsContainer');
    if (!container) return;

    var html = '';
    args.forEach(function (arg, index) {
      html += '<div class="formula-arg-row" data-arg-index="' + index + '">' +
        '<input type="text" class="arg-name" placeholder="参数名" value="' + escapeHtml(arg.name || '') + '">' +
        '<input type="text" class="arg-default" placeholder="默认值" value="' + escapeHtml(arg.default !== undefined ? arg.default : '') + '">' +
        '<input type="text" class="arg-desc" placeholder="参数描述" value="' + escapeHtml(arg.description || arg.desc || '') + '">' +
        '<button class="formula-arg-remove-btn" title="删除参数" data-remove-index="' + index + '">×</button>' +
        '</div>';
    });

    container.innerHTML = html;

    // Bind remove buttons
    var removeBtns = container.querySelectorAll('.formula-arg-remove-btn');
    removeBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        this.closest('.formula-arg-row').remove();
      });
    });
  }

  function addArgRow() {
    var container = document.getElementById('formulaArgsContainer');
    if (!container) return;

    var index = container.querySelectorAll('.formula-arg-row').length;
    var row = document.createElement('div');
    row.className = 'formula-arg-row';
    row.setAttribute('data-arg-index', index);
    row.innerHTML = '<input type="text" class="arg-name" placeholder="参数名">' +
      '<input type="text" class="arg-default" placeholder="默认值">' +
      '<input type="text" class="arg-desc" placeholder="参数描述">' +
      '<button class="formula-arg-remove-btn" title="删除参数">×</button>';

    row.querySelector('.formula-arg-remove-btn').addEventListener('click', function () {
      row.remove();
    });

    container.appendChild(row);
  }

  function getArgsFromEditor() {
    var container = document.getElementById('formulaArgsContainer');
    if (!container) return [];

    var rows = container.querySelectorAll('.formula-arg-row');
    var args = [];
    rows.forEach(function (row) {
      var nameEl = row.querySelector('.arg-name');
      var defaultEl = row.querySelector('.arg-default');
      var descEl = row.querySelector('.arg-desc');
      var name = (nameEl ? nameEl.value.trim() : '');
      if (name) {
        args.push({
          name: name,
          default: defaultEl ? defaultEl.value : '',
          description: descEl ? descEl.value.trim() : ''
        });
      }
    });
    return args;
  }

  // ===== Save Formula =====
  function saveFormula() {
    var nameEl = document.getElementById('formulaName');
    var catEl = document.getElementById('formulaCategory');
    var descEl = document.getElementById('formulaDesc');
    var scriptEl = document.getElementById('formulaScript');

    var name = nameEl ? nameEl.value.trim() : '';
    if (!name) {
      showToast('请输入公式名称', 'error');
      return;
    }

    var data = {
      name: name,
      category: catEl ? catEl.value : 'indicator',
      description: descEl ? descEl.value.trim() : '',
      script: scriptEl ? scriptEl.value : '',
      args: getArgsFromEditor()
    };

    var promise;
    if (isNewMode || !currentFormula) {
      promise = apiPost('/api/formula/create', data);
    } else {
      promise = apiPut('/api/formula/' + currentFormula.id, data);
    }

    promise.then(function (result) {
      showToast(isNewMode ? '公式创建成功' : '公式保存成功', 'success');
      // If created, update current formula
      if (result && result.id) {
        currentFormula = result;
        isNewMode = false;
      } else if (currentFormula) {
        // Update current formula data
        currentFormula.name = data.name;
        currentFormula.category = data.category;
        currentFormula.description = data.description;
        currentFormula.script = data.script;
        currentFormula.args = data.args;
      }
      // Reload list
      loadFormulaList();
    }).catch(function (err) {
      showToast('保存失败: ' + err.message, 'error');
    });
  }

  // ===== Delete Formula =====
  function deleteFormula() {
    if (!currentFormula) {
      showToast('请先选择公式', 'error');
      return;
    }

    if (!confirm('确定要删除公式 "' + (currentFormula.name || '未命名') + '" 吗？此操作不可撤销。')) {
      return;
    }

    apiDelete('/api/formula/' + currentFormula.id).then(function () {
      showToast('公式已删除', 'success');
      currentFormula = null;
      isNewMode = false;
      // Clear editor
      var placeholder = document.getElementById('formulaPlaceholder');
      var editorForm = document.getElementById('formulaEditorForm');
      if (placeholder) placeholder.style.display = 'flex';
      if (editorForm) editorForm.style.display = 'none';
      // Clear test results
      var resultEl = document.getElementById('formulaTestResult');
      if (resultEl) resultEl.innerHTML = '';
      loadFormulaList();
    }).catch(function (err) {
      showToast('删除失败: ' + err.message, 'error');
    });
  }

  // ===== Test Formula =====
  function testFormula() {
    var scriptEl = document.getElementById('formulaScript');
    var script = scriptEl ? scriptEl.value.trim() : '';

    if (!script) {
      showToast('请先输入公式脚本', 'error');
      return;
    }

    var stockCode = document.getElementById('testStockCode');
    var period = document.getElementById('testPeriod');

    var data = {
      script: script,
      stock_code: stockCode ? stockCode.value.trim() : '000001',
      period: period ? period.value : '1d',
      args: getArgsFromEditor()
    };

    var resultEl = document.getElementById('formulaTestResult');
    if (resultEl) {
      resultEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:12px;">正在测试...</div>';
    }

    var testBtn = document.getElementById('btnTestFormula');
    if (testBtn) testBtn.disabled = true;

    apiPost('/api/formula/test', data).then(function (result) {
      renderTestResult(result);
      if (testBtn) testBtn.disabled = false;
    }).catch(function (err) {
      if (resultEl) {
        resultEl.innerHTML = '<div class="formula-test-error">' +
          (err.message === 'Failed to fetch' ? 'HQChart 引擎不可用' : '测试失败: ' + err.message) +
          '</div>';
      }
      if (testBtn) testBtn.disabled = false;
    });
  }

  function renderTestResult(result) {
    var resultEl = document.getElementById('formulaTestResult');
    if (!resultEl) return;

    if (!result || result.error) {
      resultEl.innerHTML = '<div class="formula-test-error">' +
        escapeHtml(result && result.error ? result.error : 'HQChart 引擎不可用') +
        '</div>';
      return;
    }

    // The result may contain output variables and values
    var outputs = result.outputs || result.results || result;
    var outputKeys = Object.keys(outputs);

    if (outputKeys.length === 0) {
      resultEl.innerHTML = '<div class="formula-test-warning">测试完成，但没有输出数据</div>';
      return;
    }

    // Build table: first column is the variable name, remaining columns are the last 5 values
    var html = '<div class="formula-result-table-wrap"><table class="formula-result-table"><thead><tr><th>变量名</th>';

    // Determine the number of values from the first output
    var firstKey = outputKeys[0];
    var firstValues = outputs[firstKey];
    var values = Array.isArray(firstValues) ? firstValues : (firstValues !== undefined ? [firstValues] : []);
    var recentValues = values.slice(-5);
    var valueCount = recentValues.length;

    for (var i = 0; i < valueCount; i++) {
      html += '<th>值 ' + (i + 1) + '</th>';
    }
    html += '</tr></thead><tbody>';

    outputKeys.forEach(function (key) {
      var vals = outputs[key];
      var arr = Array.isArray(vals) ? vals : (vals !== undefined ? [vals] : []);
      var recent = arr.slice(-5);
      html += '<tr><td>' + escapeHtml(key) + '</td>';
      for (var j = 0; j < valueCount; j++) {
        var val = recent[j];
        html += '<td>' + (val !== undefined ? val : '—') + '</td>';
      }
      html += '</tr>';
    });

    html += '</tbody></table></div>';

    // Add note about total data points
    if (values.length > 5) {
      html += '<div style="font-size:10px;color:var(--text-muted);margin-top:4px;">共 ' + values.length + ' 个数据点，仅显示最近 5 个</div>';
    }

    resultEl.innerHTML = html;
  }

  // ===== Filters =====
  function setFilter(filter) {
    currentFilter = filter;
    // Update filter buttons
    var buttons = document.querySelectorAll('#formulaFilterBar .formula-filter-btn');
    buttons.forEach(function (btn) {
      if (btn.getAttribute('data-filter') === filter) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
    renderFormulaList();
  }

  function setSearch(query) {
    searchQuery = query;
    renderFormulaList();
  }

  // ===== Mobile Sidebar Toggle =====
  function toggleMobileSidebar() {
    var sidebar = document.getElementById('formulaSidebar');
    if (sidebar) {
      sidebar.classList.toggle('mobile-open');
    }
  }

  // ===== Init =====
  function init() {
    // Load formula list
    loadFormulaList();

    // Bind search
    var searchEl = document.getElementById('formulaSearch');
    if (searchEl) {
      searchEl.addEventListener('input', function () {
        setSearch(this.value);
      });
    }

    // Bind filter buttons
    var filterBar = document.getElementById('formulaFilterBar');
    if (filterBar) {
      filterBar.addEventListener('click', function (e) {
        var btn = e.target.closest('.formula-filter-btn');
        if (btn) {
          var filter = btn.getAttribute('data-filter');
          setFilter(filter);
        }
      });
    }

    // Bind new formula button (sidebar)
    var btnNew = document.getElementById('btnNewFormula');
    if (btnNew) {
      btnNew.addEventListener('click', newFormula);
    }

    // Bind new formula button (editor)
    var btnNewEditor = document.getElementById('btnNewFormulaEditor');
    if (btnNewEditor) {
      btnNewEditor.addEventListener('click', newFormula);
    }

    // Bind save button
    var btnSave = document.getElementById('btnSaveFormula');
    if (btnSave) {
      btnSave.addEventListener('click', saveFormula);
    }

    // Bind delete button
    var btnDelete = document.getElementById('btnDeleteFormula');
    if (btnDelete) {
      btnDelete.addEventListener('click', deleteFormula);
    }

    // Bind add arg button
    var btnAddArg = document.getElementById('btnAddArg');
    if (btnAddArg) {
      btnAddArg.addEventListener('click', addArgRow);
    }

    // Bind test button
    var btnTest = document.getElementById('btnTestFormula');
    if (btnTest) {
      btnTest.addEventListener('click', testFormula);
    }

    // Bind mobile sidebar toggle
    var btnMobile = document.getElementById('btnMobileSidebar');
    if (btnMobile) {
      btnMobile.addEventListener('click', toggleMobileSidebar);
    }

    // Close sidebar when clicking outside on mobile
    document.addEventListener('click', function (e) {
      var sidebar = document.getElementById('formulaSidebar');
      if (sidebar && sidebar.classList.contains('mobile-open')) {
        var isInside = sidebar.contains(e.target);
        var isToggle = e.target.closest('#btnMobileSidebar');
        if (!isInside && !isToggle) {
          sidebar.classList.remove('mobile-open');
        }
      }
    });
  }

  // ===== Public API =====
  return {
    init: init,
    loadFormulaList: loadFormulaList,
    selectFormula: selectFormula,
    saveFormula: saveFormula,
    deleteFormula: deleteFormula,
    testFormula: testFormula,
    newFormula: newFormula,
    renderArgsEditor: renderArgsEditor,
    getArgsFromEditor: getArgsFromEditor,
    apiGet: apiGet,
    apiPost: apiPost,
    apiPut: apiPut,
    apiDelete: apiDelete
  };
})();

// Auto-init on DOM ready
document.addEventListener('DOMContentLoaded', function () {
  FormulaEditor.init();
});

// ============================================================================
// ===== 来源: rule-editor.js =====
// ============================================================================

/**
 * RuleEditor - 可视化规则配置界面
 * 用于创建/编辑/删除/排序 action_rules.json 中的规则
 */
class RuleEditor {
  constructor() {
    this.rules = [];
    this.handlers = [];
    this.editingRuleId = null;
    this.dragSrcIndex = null;
    this.apiBase = '/api/config';

    this._buildModal();
    this._bindEvents();
  }

  // ─── Modal 构建 ─────────────────────────────────────────────

  _buildModal() {
    const overlay = document.createElement('div');
    overlay.className = 'rule-editor-overlay';
    overlay.id = 'ruleEditorOverlay';
    overlay.innerHTML = `
      <div class="rule-editor-modal">
        <div class="rule-editor-header">
          <h3>规则配置编辑器</h3>
          <button class="rule-editor-close" id="ruleEditorClose" title="关闭">&times;</button>
        </div>
        <div class="rule-editor-toolbar">
          <button class="tb-btn" id="ruleAddBtn" title="添加规则">+ 添加规则</button>
          <button class="tb-btn" id="ruleExportBtn" title="导出保存">导出保存</button>
          <button class="tb-btn" id="ruleReloadBtn" title="重新加载">重新加载</button>
          <span class="rule-editor-count" id="ruleCount"></span>
        </div>
        <div class="rule-editor-body" id="ruleEditorBody">
          <div class="rule-editor-empty">暂无规则，点击"添加规则"开始</div>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
  }

  _bindEvents() {
    const overlay = document.getElementById('ruleEditorOverlay');
    const closeBtn = document.getElementById('ruleEditorClose');
    const addBtn = document.getElementById('ruleAddBtn');
    const exportBtn = document.getElementById('ruleExportBtn');
    const reloadBtn = document.getElementById('ruleReloadBtn');

    closeBtn.addEventListener('click', () => this.close());
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) this.close();
    });
    addBtn.addEventListener('click', () => this._showRuleForm());
    exportBtn.addEventListener('click', () => this._exportRules());
    reloadBtn.addEventListener('click', () => this.loadRules());
  }

  // ─── 公共方法 ───────────────────────────────────────────────

  open() {
    const overlay = document.getElementById('ruleEditorOverlay');
    overlay.classList.add('active');
    this.loadRules();
    this._loadHandlers();
  }

  close() {
    const overlay = document.getElementById('ruleEditorOverlay');
    overlay.classList.remove('active');
    this.editingRuleId = null;
  }

  async loadRules() {
    try {
      const resp = await fetch(`${this.apiBase}/action_rules`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      this.rules = data.rules || [];
      this.rules.sort((a, b) => (a.priority || 100) - (b.priority || 100));
      this._render();
    } catch (e) {
      console.error('加载规则失败:', e);
      this._showToast('加载规则失败: ' + e.message, 'error');
    }
  }

  async _loadHandlers() {
    try {
      const resp = await fetch(`${this.apiBase}/handlers`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      this.handlers = data.handlers || [];
    } catch (e) {
      console.warn('加载 handlers 失败，从 action_rules 配置表加载:', e);
      try {
        const resp = await fetch(`${this.apiBase}/action_rules`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        this.handlers = data.handlers || [];
      } catch (e2) {
        console.error('从配置表加载 handlers 也失败:', e2);
        this.handlers = [];
      }
    }
  }

  // ─── 渲染 ───────────────────────────────────────────────────

  _render() {
    const body = document.getElementById('ruleEditorBody');
    const count = document.getElementById('ruleCount');
    count.textContent = `共 ${this.rules.length} 条规则`;

    if (this.rules.length === 0) {
      body.innerHTML = '<div class="rule-editor-empty">暂无规则，点击"添加规则"开始</div>';
      return;
    }

    body.innerHTML = '';
    this.rules.forEach((rule, idx) => {
      const card = this._createRuleCard(rule, idx);
      body.appendChild(card);
    });
  }

  _createRuleCard(rule, idx) {
    const card = document.createElement('div');
    card.className = 'rule-card';
    card.setAttribute('draggable', 'true');
    card.dataset.index = idx;

    const trigger = rule.trigger || {};
    const guard = rule.guard;
    const stopOnMatch = rule.stop_on_match !== false;
    const tags = rule.tags || [];

    let guardHtml = '<span class="rule-guard-none">无</span>';
    if (guard) {
      if (Array.isArray(guard)) {
        // OR groups
        guardHtml = guard.map(g => {
          if (g && typeof g === 'object') {
            const chips = Object.entries(g).map(([k, v]) =>
              `<span class="rule-condition-chip">${this._esc(k)}=${this._esc(String(v))}</span>`
            ).join('');
            return `<div class="rule-condition-group">${chips}</div>`;
          }
          return '';
        }).join('<span class="rule-condition-or">OR</span>');
      } else if (typeof guard === 'object') {
        const chips = Object.entries(guard).map(([k, v]) =>
          `<span class="rule-condition-chip">${this._esc(k)}=${this._esc(String(v))}</span>`
        ).join('');
        guardHtml = `<div class="rule-condition-group">${chips}</div>`;
      }
    }

    const tagHtml = tags.map(t => `<span class="rule-tag">${this._esc(t)}</span>`).join('');

    card.innerHTML = `
      <div class="rule-card-header">
        <span class="rule-drag-handle" title="拖拽排序">≡</span>
        <span class="rule-id">${this._esc(rule.rule_id || '')}</span>
        <span class="rule-priority">P${rule.priority || 100}</span>
        ${stopOnMatch ? '<span class="rule-stop-badge">STOP</span>' : ''}
      </div>
      <div class="rule-card-body">
        <div class="rule-row">
          <span class="rule-label">Trigger:</span>
          <span class="rule-value">${this._esc(trigger.type || '')} / ${this._esc(trigger.field || '')}</span>
        </div>
        <div class="rule-row">
          <span class="rule-label">Guard:</span>
          <div class="rule-conditions">${guardHtml}</div>
        </div>
        <div class="rule-row">
          <span class="rule-label">Action:</span>
          <span class="rule-value rule-action-name">${this._esc(rule.action || rule.handler_ref || '')}</span>
        </div>
        ${rule.description ? `<div class="rule-row"><span class="rule-label">Desc:</span><span class="rule-value rule-desc">${this._esc(rule.description)}</span></div>` : ''}
        ${tagHtml ? `<div class="rule-row"><span class="rule-label">Tags:</span><div class="rule-tags">${tagHtml}</div></div>` : ''}
      </div>
      <div class="rule-card-actions">
        <button class="btn-sm rule-edit-btn" data-rule-id="${this._esc(rule.rule_id || '')}">编辑</button>
        <button class="btn-sm rule-delete-btn" data-rule-id="${this._esc(rule.rule_id || '')}">删除</button>
      </div>
    `;

    // 拖拽事件
    const handle = card.querySelector('.rule-drag-handle');
    card.addEventListener('dragstart', (e) => {
      this.dragSrcIndex = idx;
      card.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(idx));
    });
    card.addEventListener('dragend', () => {
      card.classList.remove('dragging');
      this.dragSrcIndex = null;
      // 移除所有插入指示器
      document.querySelectorAll('.rule-card.drag-over').forEach(c => c.classList.remove('drag-over'));
    });
    card.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      card.classList.add('drag-over');
    });
    card.addEventListener('dragleave', () => {
      card.classList.remove('drag-over');
    });
    card.addEventListener('drop', (e) => {
      e.preventDefault();
      card.classList.remove('drag-over');
      const fromIdx = this.dragSrcIndex;
      const toIdx = idx;
      if (fromIdx !== null && fromIdx !== toIdx) {
        this._reorderRules(fromIdx, toIdx);
      }
    });

    // 按钮事件
    card.querySelector('.rule-edit-btn').addEventListener('click', () => {
      this._showRuleForm(rule.rule_id);
    });
    card.querySelector('.rule-delete-btn').addEventListener('click', () => {
      this._deleteRule(rule.rule_id);
    });

    return card;
  }

  _reorderRules(fromIdx, toIdx) {
    const moved = this.rules.splice(fromIdx, 1)[0];
    this.rules.splice(toIdx, 0, moved);
    // 更新 priority
    this.rules.forEach((r, i) => {
      r.priority = (i + 1) * 10;
    });
    this._render();
    this._showToast('规则顺序已更新，请点击"导出保存"以持久化', 'success');
  }

  // ─── 规则表单 ───────────────────────────────────────────────

  _showRuleForm(ruleId) {
    this.editingRuleId = ruleId || null;
    const existing = ruleId ? this.rules.find(r => r.rule_id === ruleId) : null;

    const trigger = existing ? existing.trigger || {} : {};
    const guard = existing ? existing.guard : null;
    const paramsTemplate = existing ? existing.params_template || {} : {};

    // 解析 guard 为条件组
    let conditionGroups = [];
    if (guard) {
      if (Array.isArray(guard)) {
        conditionGroups = guard.map(g => {
          if (g && typeof g === 'object') {
            return Object.entries(g).map(([k, v]) => ({ key: k, value: String(v) }));
          }
          return [];
        });
      } else if (typeof guard === 'object') {
        conditionGroups = [Object.entries(guard).map(([k, v]) => ({ key: k, value: String(v) }))];
      }
    }
    if (conditionGroups.length === 0) {
      conditionGroups = [[]];
    }

    const handlerOptions = this.handlers.map(h =>
      `<option value="${this._esc(h.name)}" ${existing && (existing.action === h.name || existing.handler_ref === h.name) ? 'selected' : ''}>${this._esc(h.name)}${h.description ? ' - ' + this._esc(h.description) : ''}</option>`
    ).join('');

    // 移除已有表单
    const oldForm = document.getElementById('ruleFormOverlay');
    if (oldForm) oldForm.remove();

    const formOverlay = document.createElement('div');
    formOverlay.className = 'rule-form-overlay';
    formOverlay.id = 'ruleFormOverlay';

    const guardEditorHtml = this._renderGuardEditor(conditionGroups);

    formOverlay.innerHTML = `
      <div class="rule-form-modal">
        <div class="rule-form-header">
          <h3>${existing ? '编辑规则' : '添加规则'}</h3>
          <button class="rule-editor-close" id="ruleFormClose">&times;</button>
        </div>
        <div class="rule-form-body">
          <div class="rule-form-row">
            <label>规则 ID</label>
            <input type="text" id="rfRuleId" value="${this._esc(existing ? existing.rule_id : '')}" ${existing ? 'readonly' : ''} placeholder="如: rule_001">
          </div>
          <div class="rule-form-row">
            <label>描述</label>
            <input type="text" id="rfDescription" value="${this._esc(existing ? existing.description || '' : '')}" placeholder="规则描述">
          </div>
          <div class="rule-form-section-title">触发条件 (Trigger)</div>
          <div class="rule-form-row">
            <label>触发类型</label>
            <select id="rfTriggerType">
              <option value="field_change" ${trigger.type === 'field_change' ? 'selected' : ''}>field_change</option>
              <option value="edge_attr_change" ${trigger.type === 'edge_attr_change' ? 'selected' : ''}>edge_attr_change</option>
              <option value="flag_toggle" ${trigger.type === 'flag_toggle' ? 'selected' : ''}>flag_toggle</option>
              <option value="node_init" ${trigger.type === 'node_init' ? 'selected' : ''}>node_init</option>
              <option value="edge_execute" ${trigger.type === 'edge_execute' ? 'selected' : ''}>edge_execute</option>
              <option value="schedule" ${trigger.type === 'schedule' ? 'selected' : ''}>schedule</option>
            </select>
          </div>
          <div class="rule-form-row">
            <label>触发字段</label>
            <input type="text" id="rfTriggerField" value="${this._esc(trigger.field || '')}" placeholder="如: text, attr, func.nset">
          </div>
          <div class="rule-form-section-title">守卫条件 (Guard)</div>
          <div id="rfGuardEditor">${guardEditorHtml}</div>
          <div class="rule-form-section-title">动作 (Action)</div>
          <div class="rule-form-row">
            <label>Handler</label>
            <select id="rfAction">
              <option value="">-- 选择 handler --</option>
              ${handlerOptions}
            </select>
          </div>
          <div class="rule-form-row">
            <label>优先级</label>
            <input type="number" id="rfPriority" value="${existing ? existing.priority : (this.rules.length + 1) * 10}" min="1" step="10">
          </div>
          <div class="rule-form-row rule-form-checkbox">
            <label>
              <input type="checkbox" id="rfStopOnMatch" ${existing ? (existing.stop_on_match !== false ? 'checked' : '') : 'checked'}>
              匹配后停止 (stop_on_match)
            </label>
          </div>
          <div class="rule-form-row">
            <label>标签 (逗号分隔)</label>
            <input type="text" id="rfTags" value="${this._esc((existing ? existing.tags || [] : []).join(', '))}" placeholder="如: dzh, ui_linkage">
          </div>
          <div class="rule-form-section-title">参数模板 (params_template)</div>
          <div class="rule-form-row">
            <label>JSON</label>
            <textarea id="rfParamsTemplate" rows="4" placeholder='{"key": "value"}'>${this._esc(JSON.stringify(paramsTemplate, null, 2))}</textarea>
          </div>
        </div>
        <div class="rule-form-footer">
          <button class="tb-btn" id="ruleFormCancel">取消</button>
          <button class="tb-btn" id="ruleFormSave" style="background:var(--accent-blue);color:#fff;border-color:var(--accent-blue);">保存</button>
        </div>
      </div>
    `;

    document.body.appendChild(formOverlay);

    // 绑定表单事件
    document.getElementById('ruleFormClose').addEventListener('click', () => this._closeRuleForm());
    document.getElementById('ruleFormCancel').addEventListener('click', () => this._closeRuleForm());
    formOverlay.addEventListener('click', (e) => {
      if (e.target === formOverlay) this._closeRuleForm();
    });
    document.getElementById('ruleFormSave').addEventListener('click', () => this._saveRuleForm());

    // 绑定 guard 编辑器事件
    this._bindGuardEditorEvents();
  }

  _renderGuardEditor(conditionGroups) {
    let html = '';
    conditionGroups.forEach((group, gi) => {
      const isLast = gi === conditionGroups.length - 1;
      html += `<div class="rule-guard-group" data-group-index="${gi}">`;
      html += `<div class="rule-guard-group-header">`;
      html += `<span class="rule-guard-group-label">条件组 ${gi + 1} (AND)</span>`;
      if (conditionGroups.length > 1) {
        html += `<button class="btn-sm rule-guard-remove-group" data-group-index="${gi}" title="删除此组">删除组</button>`;
      }
      html += `</div>`;
      group.forEach((cond, ci) => {
        html += `<div class="rule-guard-condition" data-group-index="${gi}" data-cond-index="${ci}">`;
        html += `<input type="text" class="rule-guard-key" value="${this._esc(cond.key)}" placeholder="key">`;
        html += `<span class="rule-guard-eq">=</span>`;
        html += `<input type="text" class="rule-guard-value" value="${this._esc(cond.value)}" placeholder="value">`;
        html += `<button class="btn-sm rule-guard-remove-cond" data-group-index="${gi}" data-cond-index="${ci}" title="删除条件">&times;</button>`;
        html += `</div>`;
      });
      html += `<button class="btn-sm rule-guard-add-cond" data-group-index="${gi}">+ 条件</button>`;
      html += `</div>`;
      if (!isLast) {
        html += `<div class="rule-guard-or-divider">OR</div>`;
      }
    });
    html += `<button class="btn-sm rule-guard-add-group">+ 添加 OR 条件组</button>`;
    return html;
  }

  _bindGuardEditorEvents() {
    const editor = document.getElementById('rfGuardEditor');
    if (!editor) return;

    editor.addEventListener('click', (e) => {
      const target = e.target;
      if (target.classList.contains('rule-guard-add-cond')) {
        const gi = parseInt(target.dataset.groupIndex);
        this._addGuardCondition(gi);
      } else if (target.classList.contains('rule-guard-remove-cond')) {
        const gi = parseInt(target.dataset.groupIndex);
        const ci = parseInt(target.dataset.condIndex);
        this._removeGuardCondition(gi, ci);
      } else if (target.classList.contains('rule-guard-remove-group')) {
        const gi = parseInt(target.dataset.groupIndex);
        this._removeGuardGroup(gi);
      } else if (target.classList.contains('rule-guard-add-group')) {
        this._addGuardGroup();
      }
    });
  }

  _getGuardFromForm() {
    const editor = document.getElementById('rfGuardEditor');
    if (!editor) return null;

    const groups = editor.querySelectorAll('.rule-guard-group');
    const conditionGroups = [];

    groups.forEach(groupEl => {
      const conditions = [];
      groupEl.querySelectorAll('.rule-guard-condition').forEach(condEl => {
        const key = condEl.querySelector('.rule-guard-key').value.trim();
        const value = condEl.querySelector('.rule-guard-value').value.trim();
        if (key) {
          conditions.push({ key, value });
        }
      });
      if (conditions.length > 0) {
        const groupObj = {};
        conditions.forEach(c => { groupObj[c.key] = c.value; });
        conditionGroups.push(groupObj);
      }
    });

    if (conditionGroups.length === 0) return null;
    if (conditionGroups.length === 1) return conditionGroups[0];
    return conditionGroups; // 多组 → OR
  }

  _addGuardCondition(groupIndex) {
    const groups = this._parseGuardGroups();
    if (groups[groupIndex]) {
      groups[groupIndex].push({ key: '', value: '' });
    }
    this._refreshGuardEditor(groups);
  }

  _removeGuardCondition(groupIndex, condIndex) {
    const groups = this._parseGuardGroups();
    if (groups[groupIndex]) {
      groups[groupIndex].splice(condIndex, 1);
    }
    this._refreshGuardEditor(groups);
  }

  _removeGuardGroup(groupIndex) {
    const groups = this._parseGuardGroups();
    groups.splice(groupIndex, 1);
    if (groups.length === 0) groups.push([]);
    this._refreshGuardEditor(groups);
  }

  _addGuardGroup() {
    const groups = this._parseGuardGroups();
    groups.push([{ key: '', value: '' }]);
    this._refreshGuardEditor(groups);
  }

  _parseGuardGroups() {
    const editor = document.getElementById('rfGuardEditor');
    if (!editor) return [[]];

    const groupEls = editor.querySelectorAll('.rule-guard-group');
    const groups = [];

    groupEls.forEach(groupEl => {
      const conditions = [];
      groupEl.querySelectorAll('.rule-guard-condition').forEach(condEl => {
        const key = condEl.querySelector('.rule-guard-key').value.trim();
        const value = condEl.querySelector('.rule-guard-value').value.trim();
        conditions.push({ key, value });
      });
      groups.push(conditions);
    });

    return groups.length > 0 ? groups : [[]];
  }

  _refreshGuardEditor(groups) {
    const editor = document.getElementById('rfGuardEditor');
    if (!editor) return;
    editor.innerHTML = this._renderGuardEditor(groups);
    this._bindGuardEditorEvents();
  }

  _closeRuleForm() {
    const form = document.getElementById('ruleFormOverlay');
    if (form) form.remove();
    this.editingRuleId = null;
  }

  async _saveRuleForm() {
    const ruleId = document.getElementById('rfRuleId').value.trim();
    const description = document.getElementById('rfDescription').value.trim();
    const triggerType = document.getElementById('rfTriggerType').value;
    const triggerField = document.getElementById('rfTriggerField').value.trim();
    const action = document.getElementById('rfAction').value;
    const priority = parseInt(document.getElementById('rfPriority').value) || 100;
    const stopOnMatch = document.getElementById('rfStopOnMatch').checked;
    const tagsStr = document.getElementById('rfTags').value.trim();
    const paramsStr = document.getElementById('rfParamsTemplate').value.trim();

    if (!ruleId) {
      this._showToast('规则 ID 不能为空', 'error');
      return;
    }
    if (!triggerType) {
      this._showToast('触发类型不能为空', 'error');
      return;
    }

    let paramsTemplate = {};
    if (paramsStr) {
      try {
        paramsTemplate = JSON.parse(paramsStr);
      } catch (e) {
        this._showToast('参数模板 JSON 格式错误: ' + e.message, 'error');
        return;
      }
    }

    const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(t => t) : [];
    const guard = this._getGuardFromForm();

    const rule = {
      rule_id: ruleId,
      description: description || undefined,
      trigger: { type: triggerType, field: triggerField },
      guard: guard,
      action: action,
      handler_ref: action,
      params_template: Object.keys(paramsTemplate).length > 0 ? paramsTemplate : undefined,
      priority: priority,
      stop_on_match: stopOnMatch,
      tags: tags.length > 0 ? tags : undefined,
    };

    // 清理 undefined 字段
    Object.keys(rule).forEach(k => rule[k] === undefined && delete rule[k]);

    if (this.editingRuleId) {
      const idx = this.rules.findIndex(r => r.rule_id === this.editingRuleId);
      if (idx !== -1) {
        this.rules[idx] = rule;
      }
    } else {
      this.rules.push(rule);
    }

    this.rules.sort((a, b) => (a.priority || 100) - (b.priority || 100));
    this._render();
    this._closeRuleForm();
    this._showToast(`规则 ${ruleId} 已${this.editingRuleId ? '更新' : '添加'}，请点击"导出保存"以持久化`, 'success');
  }

  // ─── 删除规则 ───────────────────────────────────────────────

  _deleteRule(ruleId) {
    if (!confirm(`确定删除规则 "${ruleId}" 吗？`)) return;
    this.rules = this.rules.filter(r => r.rule_id !== ruleId);
    this._render();
    this._showToast(`规则 ${ruleId} 已删除，请点击"导出保存"以持久化`, 'success');
  }

  // ─── 导出保存 ───────────────────────────────────────────────

  async _exportRules() {
    try {
      const resp = await fetch(`${this.apiBase}/action_rules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: '1.0',
          rules: this.rules,
        }),
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${resp.status}`);
      }

      const result = await resp.json();
      this._showToast(`保存成功！共 ${result.count} 条规则`, 'success');
      // 重新加载以获取服务端确认的数据
      await this.loadRules();
    } catch (e) {
      console.error('导出规则失败:', e);
      this._showToast('导出失败: ' + e.message, 'error');
    }
  }

  // ─── 工具方法 ───────────────────────────────────────────────

  _esc(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  _showToast(message, type) {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = `toast toast-${type || 'success'}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
      toast.remove();
    }, 3500);
  }
}

// 全局实例
window.ruleEditor = new RuleEditor();


// ============================================================================
// ===== 来源: comprehensive-settings.js (ComprehensiveSettings) =====
// ============================================================================
/**
 * ComprehensiveSettings — 股票池流程设置窗口控制器
 *
 * 表格视图（3列布局，承担"选股流程结构图"视觉表达）:
 *   表头: 流程标识 | 条件/属性 | 时序/操作
 *   行类型: 备选池行 / 条件行 / 属性行
 *   左列彩色标签 + ▶ 箭头前缀本身即流程结构图
 *
 * 公共 API:
 *   window.ComprehensiveSettings.open() / .close() / .toggle()
 *   window.ComprehensiveSettings.selectNode(nodeId)
 *   window.ComprehensiveSettings.syncFromCanvas(nodeId)  // 画布选中 → 表格高亮
 *   window.ComprehensiveSettings.isOpen()
 *   window.ComprehensiveSettings.isDirty()
 *   window.ComprehensiveSettings.markSaved()  // 外部"保存 XML"成功后调用以重置 dirty
 *   window.ComprehensiveSettings.markDirty()
 *   window.ComprehensiveSettings.refresh()
 *
 * 4 类严格分立的字段编辑器入口（按对象类型严格对应）:
 *   - openCandidatePoolEditor(nodeId) ↔ cell[type=202|7] (备选池)
 *   - openStateAttributeEditor(nodeId) ↔ cell[type=200|8] (状态池)
 *   - openConditionEditor(row)         ↔ cell[type=201]    (条件节点)
 *   - openTimingEditor(row)            ↔ flow 边           (上游边时序参数)
 *   不存在 openFieldEditor / openGenericEditor / openAnyEditor 这类通用入口。
 *
 * 内部工具(可导出测试):
 *   buildTableRows, formatHoldTime, formatTimingSeconds,
 *   parseTimingSeconds, parseHoldSeconds, decodeEndtime, encodeEndtime,
 *   applyCandidatePoolEditor, applyStateAttributeEditor,
 *   applyConditionEditor, applyTimingEditor,
 *   closeFieldEditor
 */

(function(global) {
  'use strict';

  // ─── 状态 ───
  var _selectedNodeId = null;
  var _isOpen = false;
  var _dirty = false;                 // 是否有未保存修改
  var _fieldEditor = null;            // 当前打开的字段编辑器上下文
  var _formulaSelectorEl = null;      // 公式选择器模态框 DOM 引用
  var _formulaSelectorOwnsOverlay = false;  // 公式选择器是否独占 overlay（panel.js 复用时为 true）

  // ─── DOM 缓存 ───
  var $overlay, $modal, $closeBtn;
  var $tableBody;
  var $fieldEditorOverlay;

  // ─── 默认常量（配置加载失败时的回退值） ──────────────────────
  var DEFAULT_TYPE_CONFIG = {
    202: { icon: '\uD83D\uDCE6', tag: '备选', typeName: 'market_source' },
    200: { icon: '\uD83C\uDFF7', tag: '状态', typeName: 'stock_state_pool' },
    201: { icon: '\uD83D\uDD00', tag: '条件', typeName: 'transfer_condition' },
    4:   { icon: '\uD83D\uDDD1', tag: '废弃', typeName: 'discard_pool' },
    1:   { icon: '\uD83D\uDCDD', tag: '标签', typeName: 'text_label' },
    2:   { icon: '\uD83D\uDCC4', tag: '容器', typeName: 'container' },
    3:   { icon: '\uD83D\uDD00', tag: '状态列', typeName: 'state_column' },
    5:   { icon: '\uD83D\uDD22', tag: '序号', typeName: 'execution_order' },
    6:   { icon: '\u27A4', tag: '箭头', typeName: 'flow_arrow' },
    7:   { icon: '\uD83D\uDCE6', tag: '候选', typeName: 'tdx_candidate' },
    8:   { icon: '\uD83C\uDFF7', tag: '状态', typeName: 'tdx_state_pool' }
  };

  var DEFAULT_CYCLE_NAMES = {
    1: '分笔', 2: '1分钟', 3: '5分钟', 4: '15分钟',
    5: '30分钟', 6: '60分钟', 7: '日线', 8: '周线', 9: '月线'
  };
  var DEFAULT_INDI_TYPE_NAMES = {
    'indi': '技术指标', '': '条件选股', 'trade_sys': '交易系统',
    'basic': '基本面', 'dynamic': '动态行情', 'ref': '引用公式'
  };
  var DEFAULT_DELTYPE_NAMES = { 0: '不删除', 1: '定时删除', 2: '条件删除' };
  var DEFAULT_BEGIN_NAMES = {
    0: '立即执行', 1: '开盘后执行', 2: '开市前执行',
    3: '开市后执行', 4: '收市前执行', 5: '收市后执行',
    6: '指定时间执行', 7: '延迟N秒执行'
  };

  // ─── 备选池重载模式默认常量（与后端 convert_reload_mode 一致） ─────────
  // 来源：DZH股票池完整技术文档 §4.9 + converters/dzh_xml_raw.py
  var DEFAULT_RELOAD_NEVER        = 2147483647;   // 0x7FFFFFFF - 不重载
  var DEFAULT_RELOAD_ON_FILE_LOAD = 2147483646;   // 0x7FFFFFFE - 文件载入时
  var DEFAULT_RELOAD_ON_STARTUP   = -57387;       // 0xFFFF4C5D - 每次启动
  var DEFAULT_RELOAD_DAILY_TIME   = -57386;       // 0xFFFF4C5E - 每天指定时间（用 -57386 区别 on_startup）
  var DEFAULT_RELOAD_INTERVAL     = -57385;       // 0xFFFF4C5F - 每隔一定时间（正整数 N 为秒数）

  // ─── 运行时常量（从配置表加载，回退到默认值） ───
  // 对象常量保持引用稳定（FIELD_META.options 持有引用），通过 _overrideInPlace 原地更新
  var TYPE_CONFIG = JSON.parse(JSON.stringify(DEFAULT_TYPE_CONFIG));
  var CYCLE_NAMES = Object.assign({}, DEFAULT_CYCLE_NAMES);
  var INDI_TYPE_NAMES = Object.assign({}, DEFAULT_INDI_TYPE_NAMES);
  var DELTYPE_NAMES = Object.assign({}, DEFAULT_DELTYPE_NAMES);
  var BEGIN_NAMES = Object.assign({}, DEFAULT_BEGIN_NAMES);
  // 基本类型常量在配置加载后直接重新赋值（decodeReloadMode/encodeReloadMode 运行时读取）
  var RELOAD_NEVER        = DEFAULT_RELOAD_NEVER;
  var RELOAD_ON_FILE_LOAD = DEFAULT_RELOAD_ON_FILE_LOAD;
  var RELOAD_ON_STARTUP   = DEFAULT_RELOAD_ON_STARTUP;
  var RELOAD_DAILY_TIME   = DEFAULT_RELOAD_DAILY_TIME;
  var RELOAD_INTERVAL     = DEFAULT_RELOAD_INTERVAL;

  // ─── 配置表加载（表驱动：从 cell_type_registry / tdx_enums / defaults 拉取） ──────────
  var _configPromise = null;

  /** 将 [{value,label},...] 数组转为 {value: label} 映射 */
  function _arrayToMap(arr) {
    var map = {};
    (arr || []).forEach(function(item) {
      map[item.value] = item.label;
    });
    return map;
  }

  /** 原地覆盖对象属性（保持引用稳定，使 FIELD_META.options 等持有引用处同步更新） */
  function _overrideInPlace(target, source) {
    if (!source) return;
    Object.keys(target).forEach(function(k) { delete target[k]; });
    Object.keys(source).forEach(function(k) { target[k] = source[k]; });
  }

  /** 从 cell_type_registry 配置构建 TYPE_CONFIG（保留默认 tag，覆盖 icon/typeName） */
  function _buildTypeConfig(cfg) {
    if (!cfg) return null;
    var typeInfo = cfg.type_info || {};
    var renderConfig = cfg.render_config || {};
    var result = {};
    // 先复制默认值（保留 tag 等自定义短标签）
    Object.keys(DEFAULT_TYPE_CONFIG).forEach(function(type) {
      result[type] = {
        icon: DEFAULT_TYPE_CONFIG[type].icon,
        tag: DEFAULT_TYPE_CONFIG[type].tag,
        typeName: DEFAULT_TYPE_CONFIG[type].typeName
      };
    });
    // 用配置表覆盖 icon 和 typeName
    Object.keys(typeInfo).forEach(function(type) {
      if (!result[type]) result[type] = { icon: '', tag: '', typeName: '' };
      var info = typeInfo[type];
      if (info.typeName) result[type].typeName = info.typeName;
      // TDX 类型(7/8)的 render_config 键为 "tdx_7"/"tdx_8"
      var rc = renderConfig[type] || renderConfig['tdx_' + type];
      if (rc && rc.icon) result[type].icon = rc.icon;
    });
    return result;
  }

  function ensureConfigLoaded() {
    if (_configPromise) return _configPromise;
    _configPromise = Promise.all([
      fetch('/api/config/tables/cell_type_registry').then(function(r) { return r.json(); }).catch(function() { return null; }),
      fetch('/api/config/tables/tdx_enums').then(function(r) { return r.json(); }).catch(function() { return null; }),
      fetch('/api/config/tables/defaults').then(function(r) { return r.json(); }).catch(function() { return null; })
    ]).then(function(results) {
      var cellTypeCfg = results[0];
      var tdxEnumsCfg = results[1];
      var defaultsCfg = results[2];

      // SubTask 20.1: TYPE_CONFIG ← cell_type_registry.json
      if (cellTypeCfg) {
        var newTypeConfig = _buildTypeConfig(cellTypeCfg);
        if (newTypeConfig) _overrideInPlace(TYPE_CONFIG, newTypeConfig);
      }

      // SubTask 20.2: CYCLE_NAMES / INDI_TYPE_NAMES / DELTYPE_NAMES / BEGIN_NAMES ← tdx_enums.json
      if (tdxEnumsCfg && tdxEnumsCfg.enums) {
        var enums = tdxEnumsCfg.enums;
        if (enums.dzh_cycle)      _overrideInPlace(CYCLE_NAMES, _arrayToMap(enums.dzh_cycle));
        if (enums.dzh_indi_type)  _overrideInPlace(INDI_TYPE_NAMES, _arrayToMap(enums.dzh_indi_type));
        if (enums.dzh_deltype)    _overrideInPlace(DELTYPE_NAMES, _arrayToMap(enums.dzh_deltype));
        if (enums.dzh_begin)      _overrideInPlace(BEGIN_NAMES, _arrayToMap(enums.dzh_begin));
      }

      // SubTask 20.3: RELOAD 常量 ← defaults.json
      if (defaultsCfg && defaultsCfg.reload_modes) {
        var rm = defaultsCfg.reload_modes;
        if (rm.never !== undefined)        RELOAD_NEVER        = rm.never;
        if (rm.on_file_load !== undefined) RELOAD_ON_FILE_LOAD = rm.on_file_load;
        if (rm.on_startup !== undefined)   RELOAD_ON_STARTUP   = rm.on_startup;
        if (rm.daily_time !== undefined)   RELOAD_DAILY_TIME   = rm.daily_time;
        if (rm.interval !== undefined)     RELOAD_INTERVAL     = rm.interval;
      }
    }).catch(function(err) {
      console.error('[CS] Failed to load config tables:', err);
    });
    return _configPromise;
  }

  // 模块加载时即开始拉取配置（异步，加载前使用默认值）
  ensureConfigLoaded();

  // ─── 字段元数据(集中管理,仅供内部 4 类严格分立的编辑器使用,**不**对外暴露为可被任意对象调用的统一 API) ───
  // type: number | select | checkbox | text | textarea | time | markets | col_list
  // path: 在 node.params / edge.params / edge.attr 内的写入路径
  // label/value/onClick 仍由渲染函数计算
  var FIELD_META = {
    // 中栏 - 状态属性 (状态池)
    hold_sec:        { kind: 'number',  section: '状态属性', label: '进入保持时间', unit: '秒',
                       apply: function(n, v) { n.params.hold_sec = parseInt(v) || 0; } },
    deltype:         { kind: 'select',  section: '状态属性', label: '删除类型', options: DELTYPE_NAMES,
                       apply: function(n, v) { n.params.deltype = parseInt(v) || 0; } },
    endtime:         { kind: 'time',    section: '状态属性', label: '删除时间',
                       apply: function(n, v) { n.params.endtime = encodeEndtime(v); } },
    histana:         { kind: 'number',  section: '状态属性', label: '记录轨迹(每N根K线)', unit: '根',
                       apply: function(n, v) { n.params.histana = parseInt(v) || 0; } },
    alert_sound:     { kind: 'checkbox',section: '状态属性', label: '发出预警声音',
                       apply: function(n, v) { ensureBits(n).alert_sound = !!v; } },
    record_history:  { kind: 'checkbox',section: '状态属性', label: '保存历史数据',
                       apply: function(n, v) { ensureBits(n).record_history = !!v; } },
    col_list:        { kind: 'col_list',section: '状态属性', label: '显示列',
                       apply: function(n, v) { n.params.col_list = v; } },

    // 中栏 - 状态属性 (备选池)
    reload_mode:     { kind: 'select',  section: '备选属性', label: '重载模式',
                       options: { 0: '每次启动载入股票', 1: '每天指定时间', 2: '每隔N秒' },
                       apply: function(n, v) { n.params.reload_sec = parseInt(v) || 0; } },
    reload_interval: { kind: 'number',  section: '备选属性', label: '重载间隔', unit: '秒',
                       apply: function(n, v) { n.params.reload_sec = parseInt(v) || 0; } },

    // 中栏 - 状态属性 (转移条件)
    analysis_cycle:  { kind: 'select',  section: '条件属性', label: '分析周期',
                       options: CYCLE_NAMES, apply: function(n, v) { n.params.analysis_cycle = parseInt(v) || 7; } },
    sorttype:        { kind: 'number',  section: '条件属性', label: '排序方式(可负数)', unit: '名',
                       apply: function(n, v) { n.params.sorttype = parseInt(v) || 0; } },
    // 转移条件公式类型（inditype，字符串键，用 INDI_TYPE_NAMES 映射）
    inditype:        { kind: 'select',  section: '指标条件', label: '条件类型', options: INDI_TYPE_NAMES,
                       apply: function(n, v) { n.params.inditype = v; } },

    // 中栏 - 状态属性 (文字标签)
    text:            { kind: 'textarea',section: '标签属性', label: '文本内容',
                       apply: function(n, v) { n.params.text = v; n.label = v; } },
    clr:             { kind: 'number',  section: '标签属性', label: '颜色(GDI整数)', unit: '',
                       apply: function(n, v) { n.params.clr = parseInt(v) || 0; } },

    // 中栏 - 指标条件 (备选池)
    markets:         { kind: 'markets', section: '备选范围', label: '市场选择',
                       apply: function(n, v) { n.params.markets = v; } },
    attrtext:        { kind: 'textarea',section: '备选范围', label: '范围明细',
                       apply: function(n, v) { n.params.attrtext = v; } },

    // 右栏 - 时序控制 (边)
    begin:           { kind: 'select',  section: '时序控制', label: '开始时机',
                       options: BEGIN_NAMES,
                       apply: function(e, v) { e.params.begin = parseInt(v) || 0; } },
    begint:          { kind: 'time',    section: '时序控制', label: '开始延迟/开始时间',
                       apply: function(e, v) { e.params.begint = encodeTimingSeconds(v); } },
    endt:            { kind: 'time',    section: '时序控制', label: '结束时间',
                       apply: function(e, v) { e.params.endt = encodeTimingSeconds(v); } },
    interval_sec:    { kind: 'number',  section: '时序控制', label: '执行间隔', unit: '秒',
                       apply: function(e, v) { e.params.interval_sec = parseInt(v) || 60; } },
    count:           { kind: 'number',  section: '时序控制', label: '执行次数(-1=不限)', unit: '次',
                       apply: function(e, v) {
                         var c = parseInt(v);
                         if (isNaN(c)) c = -1;
                         if (!e.params) e.params = {};
                         e.params.count = c;
                       } },

    // 传输模式位（操作 flow.attr bit0/12/13/14）
    transfer_mode:   { kind: 'multi_checkbox', section: '时序控制', label: '传输模式',
                       checkboxes: [
                         { key: 'transfer_delete_source',     mask: 0x1,     label: '删除源' },
                         { key: 'transfer_keep_source',       mask: 0x1000,  label: '保留源' },
                         { key: 'transfer_clear_dest',        mask: 0x2000,  label: '清空目标' },
                         { key: 'transfer_output_constituent',mask: 0x4000,  label: '输出成分股' }
                       ],
                       apply: function(e, v) {
                         if (!e.attr) e.attr = {};
                         var cur = (typeof e.attr.attr === 'number') ? e.attr.attr : 0;
                         v.forEach(function(item) {
                           if (item.checked) cur |= item.mask;
                           else cur &= ~item.mask;
                         });
                         e.attr.attr = cur;
                       } },

    // 转移选项位（操作 type=201 节点 attr bit19/20/21）
    condition_options: { kind: 'multi_checkbox', section: '条件属性', label: '转移选项',
                       checkboxes: [
                         { key: 'condition_output_constituent', mask: 0x80000,  label: '输出成分股' },
                         { key: 'condition_keep_source',        mask: 0x100000, label: '保留源池' },
                         { key: 'condition_clear_dest',         mask: 0x200000, label: '清空目标池' }
                       ],
                       apply: function(n, v) {
                         if (!n.params) n.params = {};
                         if (!n.params.dzh_attr) n.params.dzh_attr = { raw: 0, bits: {} };
                         var cur = (typeof n.params.dzh_attr.raw === 'number') ? n.params.dzh_attr.raw : 0;
                         v.forEach(function(item) {
                           if (item.checked) cur |= item.mask;
                           else cur &= ~item.mask;
                         });
                         n.params.dzh_attr.raw = cur;
                       } }
  };

  // ─── 初始化 ───
  function init() {
    $overlay = document.getElementById('comprehensiveSettingsOverlay');
    $modal = document.getElementById('comprehensiveSettingsModal');
    $closeBtn = document.getElementById('csCloseBtn');
    $tableBody = document.getElementById('csTableBody');
    $fieldEditorOverlay = document.getElementById('csFieldEditorOverlay');

    if (!$overlay || !$modal) {
      console.warn('[CS] 综合设置窗口 DOM 元素未找到，请确认 index.html 已包含窗口结构');
      return;
    }

    if ($closeBtn) $closeBtn.addEventListener('click', handleClose);
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && _isOpen) handleClose();
    });
    $overlay.addEventListener('click', function(e) {
      if (e.target === $overlay) handleClose();
    });

    // 字段编辑器遮罩点击关闭
    if ($fieldEditorOverlay) {
      $fieldEditorOverlay.addEventListener('click', function(e) {
        if (e.target === $fieldEditorOverlay) closeFieldEditor();
      });
    }
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && _fieldEditor) closeFieldEditor();
    });
  }

  // ─── 打开/关闭 ───
  function open() {
    if (typeof poolData === 'undefined' || !poolData || !poolData.hasData) {
      alert('请先加载一个股票池');
      return;
    }
    _isOpen = true;
    _dirty = false;
    $overlay.classList.remove('hidden');

    // 渲染表格
    renderComprehensiveTable();

    // 默认选中第一个节点（联动高亮）
    var rows = buildTableRows();
    if (rows.length > 0) {
      selectNode(rows[0].cell.id);
    }
  }

  // ─── 查找备选池 ───
  function findSourcePool() {
    var data = poolData.data;
    if (!data || !data.nodes) return null;

    var inEdges = {};
    data.nodes.forEach(function(n) { inEdges[n.id] = []; });
    (data.edges || []).forEach(function(e) {
      var toId = e.target ? e.target.node_id : e.to;
      if (toId && inEdges[toId] !== undefined) inEdges[toId].push(e);
    });

    var source = data.nodes.find(function(n) {
      return isSourcePoolNode(n) && inEdges[n.id].length === 0;
    });
    if (!source) {
      source = data.nodes.find(function(n) {
        return isSourcePoolNode(n);
      });
    }
    return source || null;
  }

  function handleClose() {
    if (_dirty) {
      var ok = window.confirm('设置已修改但未保存，确定关闭？');
      if (!ok) return;
    }
    close();
  }

  function close() {
    _isOpen = false;
    _selectedNodeId = null;
    _dirty = false;
    $overlay.classList.add('hidden');
  }

  /**
   * 用户主动关闭（关闭按钮 / Esc / 遮罩点击）的统一入口。
   * 若有未保存修改，必须经 window.confirm 拦截；用户取消则中止关闭。
   */
  function handleClose() {
    if (_dirty) {
      var ok = false;
      try {
        ok = window.confirm('设置已修改但未保存，确定关闭？');
      } catch (e) { ok = true; }
      if (!ok) return false;
    }
    close();
    return true;
  }

  function toggle() { _isOpen ? handleClose() : open(); }
  function isOpen() { return _isOpen; }
  function isDirty() { return _dirty; }
  function markSaved() { _dirty = false; }

  function markDirty() {
    _dirty = true;
  }

  // ─── 辅助：获取边的 dzh_attr 值（优先 params.dzh_attr，兼容 attr.attr / dzh_attr） ───
  function getEdgeAttr(e) {
    if (!e) return undefined;
    var p = e.params || {};
    if (p.dzh_attr !== undefined && p.dzh_attr !== null) return p.dzh_attr;
    if (e.dzh_attr !== undefined) return e.dzh_attr;
    if (e.attr !== undefined) {
      if (typeof e.attr === 'object' && e.attr !== null) return e.attr.attr;
      return e.attr;
    }
    return undefined;
  }

  function isPoolNode(n) {
    if (!n) return false;
    if (n.dzh_cell_type === 202 || n.dzh_cell_type === 200 ||
        n.dzh_cell_type === 7 || n.dzh_cell_type === 8) return true;
    var t = n.type;
    return t === 'market_source' || t === 'candidate_dzh' || t === 'candidate_provider' ||
           t === 'statepool' || t === 'state_pool' || t === 'stock_state_pool' ||
           t === 'tdx_state_pool' || t === 'tdx_candidate' || t === 'discard_sink';
  }
  function isConditionNode(n) {
    if (!n) return false;
    if (n.dzh_cell_type === 201) return true;
    var t = n.type;
    return t === 'condition' || t === 'transfer_condition' || t === 'tdx_condition';
  }
  function isStatePoolNode(n) {
    if (!n) return false;
    if (n.dzh_cell_type === 200 || n.dzh_cell_type === 8) return true;
    var t = n.type;
    return t === 'statepool' || t === 'state_pool' || t === 'stock_state_pool' || t === 'tdx_state_pool';
  }
  function isSourcePoolNode(n) {
    if (!n) return false;
    if (n.dzh_cell_type === 202 || n.dzh_cell_type === 7) return true;
    var t = n.type;
    return t === 'market_source' || t === 'candidate_dzh' || t === 'candidate_provider' || t === 'tdx_candidate';
  }

  // ─── 表格行模型 ───
  // 表格结构 = 备选池行 + BFS 遍历：每个池先输出声明行，再输出该池的所有出边
  //   type='source'    - 备选池行（固定 1 行，顶部；显示备选范围 + 状态中股票数 + 更新时间）
  //   type='pool-decl' - 状态池声明行（每个池只出现一次；显示该状态池自己的属性）
  //   type='edge'      - 入边行（每条复合边 1 行；左列 └─▶ + 目标池彩色标签；
  //                                中列 转移条件节点属性；右列 条件转移边时间属性）
  //
  // 行的唯一性：每个池只出现一次（作为声明行），每条边只出现一次
  //
  // BFS 顺序（与 DZH 原版一致）：
  //   1. 输出 source 行（备选池）
  //   2. 从备选池开始 BFS，对每个池先输出声明行，再输出所有出边
  //   3. 目标池仅在首次被到达时加入队列，防止循环无限
  //
  // 行数据结构:
  //   { type: 'source' | 'pool-decl' | 'edge',
  //     cell:           <poolNode>,           // 行主体标签（colored label）
  //     sourceCell:     <poolNode>|null,      // 仅 'edge' 行；源池
  //     depth:          <number>,             // BFS 深度
  //     conditionNode:  <condNode>|null,      // 仅 'edge' 行；触发条件节点
  //     upstreamEdge:   <flowEdge>|null,      // 仅 'edge' 行；上游时序边
  //     edgeIndex:      <number>,             // 原始边索引（用于排序）
  //   }
  function buildTableRows() {
    var data = poolData.data;
    if (!data || !data.nodes) return [];

    var rows = [];
    var nodeMap = {};
    data.nodes.forEach(function(n) { nodeMap[n.id] = n; });

    // 辅助：出边索引
    var outEdges = {};
    data.nodes.forEach(function(n) { outEdges[n.id] = []; });
    (data.edges || []).forEach(function(e) {
      var fromId = e.source ? e.source.node_id : e.from;
      if (fromId && outEdges[fromId]) outEdges[fromId].push(e);
    });

    // 辅助：提取所有复合边候选（与当前逻辑相同）
    // candidates[i] = { edgeIndex, fromPool, condNode|null, targetPool, upstreamEdge }
    var candidates = [];
    (data.edges || []).forEach(function(edge, edgeIndex) {
      var fromId = edge.source ? edge.source.node_id : edge.from;
      var toId   = edge.target ? edge.target.node_id : edge.to;
      var fromNode = nodeMap[fromId];
      var toNode   = nodeMap[toId];
      var fromIsPool = isPoolNode(fromNode);
      if (!fromNode || !fromIsPool) return;

      // 情形 A: 源池 → 条件节点 (上游边)
      if (isConditionNode(toNode)) {
        // 触发条件配置在连接上：支持 DZH 的 begin 字段或通用 time_gate_interval/jgtime
        var hasTiming = edge.params && (edge.params.begin !== undefined ||
                                        edge.params.time_gate_interval !== undefined ||
                                        edge.params.jgtime !== undefined);
        if (!hasTiming) return;
        var outEdge = (outEdges[toId] || []).find(function(e) {
          var outTargetId = e.target ? e.target.node_id : e.to;
          var outNode = nodeMap[outTargetId];
          return isPoolNode(outNode);
        });
        if (!outEdge) return;
        var targetId   = outEdge.target ? outEdge.target.node_id : outEdge.to;
        var targetNode = nodeMap[targetId];
        if (!targetNode) return;
        candidates.push({ edgeIndex: edgeIndex, fromPool: fromNode, condNode: toNode, targetPool: targetNode, upstreamEdge: edge });
      }
      // 情形 B: 源池 → 目标池（无条件直连）
      else if (isPoolNode(toNode)) {
        candidates.push({ edgeIndex: edgeIndex, fromPool: fromNode, condNode: null, targetPool: toNode, upstreamEdge: edge });
      }
    });

    // BFS 遍历 —— 支持多备选池，每个备选池独立 BFS（独立的 visited）
    // 这样当股票池包含多个不连通的备选池树时，每棵树都能完整显示
    var sourcePools = data.nodes.filter(function(n) {
      return isSourcePoolNode(n);
    });

    sourcePools.forEach(function(sourcePool) {
      var visited = {};     // 已访问的池（已输出声明行），每个备选池独立

      // 先输出源池声明
      rows.push({
        type: isSourcePoolNode(sourcePool) ? 'source' : 'pool-decl',
        cell: sourcePool,
        sourceCell: null,
        depth: 0,
        conditionNode: null,
        upstreamEdge: null,
        edgeIndex: -1
      });

      visited[sourcePool.id] = true;

      // DZH递归遍历算法 —— 实现精确的DZH行序
      // 使用"边批量→逐池声明+立即递归"模式（DZF深度优先变体）
      function processPoolRecursive(pool, depth) {
        // 收集该池的所有出边，按edgeIndex排序
        var poolEdges = candidates.filter(function(c) { return c.fromPool.id === pool.id; });
        poolEdges.sort(function(a, b) { return a.edgeIndex - b.edgeIndex });

        var newPools = []; // 该父池新发现的池（按边顺序）

        // Phase 1: 批量输出该父池的所有 EDGE 行（DZH先列完同级所有边）
        poolEdges.forEach(function(c) {
          rows.push({
            type: 'edge',
            cell: c.targetPool,
            sourceCell: c.fromPool,
            depth: depth + 1,
            conditionNode: c.condNode,
            upstreamEdge: c.upstreamEdge,
            edgeIndex: c.edgeIndex
          });

          // 记录首次发现的目标池
          if (!visited[c.targetPool.id]) {
            visited[c.targetPool.id] = true;
            newPools.push(c.targetPool);
          }
        });

        // Phase 2: 对每个新池：输出声明并立即递归其子树（DZH深度优先）
        newPools.forEach(function(targetPool) {
          // 输出 POOL-DECL 行
          rows.push({
            type: 'pool-decl',
            cell: targetPool,
            sourceCell: null,
            depth: depth + 1,
            conditionNode: null,
            upstreamEdge: null,
            edgeIndex: -1
          });

          // 立即递归处理该池的子树（DZH深度优先：第一个池完全展开后才处理第二个池）
          processPoolRecursive(targetPool, depth + 1);
        });
      }

      // 启动递归遍历
      processPoolRecursive(sourcePool, 0);
    });

    return rows;
  }

  // ─── 状态属性文本（属性行中间列） ───
  function buildStateAttributes(node) {
    var lines = [];
    var p = node.params || {};

    // 进入后保持时间 - 兼容 hold 和 hold_sec 两种字段名
    var holdSec = p.hold_sec !== undefined ? p.hold_sec : p.hold;
    if (holdSec !== undefined && holdSec !== '') {
      lines.push('进入后保持:' + formatHoldTime(holdSec));
    }

    // 记录轨迹：dzh_attr.bits.record_history 为 true，或 attr 位标志 bit27 (0x08000000)
    // 注意：必须确保传入的 node 是状态池节点(type=200|8)，而不是条件节点或其他节点
    var recordHistory = false;
    if (p.dzh_attr && p.dzh_attr.bits && p.dzh_attr.bits.record_history === true) {
      recordHistory = true;
    } else if (p.attr !== undefined) {
      var attrVal = parseInt(p.attr) || 0;
      recordHistory = !!(attrVal & 0x08000000);
    }
    lines.push(recordHistory ? '记录轨迹' : '不记录轨迹');

    // 声音预警：dzh_attr.bits.alert_sound 为 true，或 attr 位标志 bit24 (0x01000000)
    var alertSound = false;
    if (p.dzh_attr && p.dzh_attr.bits && p.dzh_attr.bits.alert_sound) {
      alertSound = true;
    } else if (p.attr !== undefined) {
      var attrVal2 = parseInt(p.attr) || 0;
      alertSound = !!(attrVal2 & 0x01000000);
    }
    if (alertSound) {
      lines.push('进入时发出声音');
    }

    // 收益分析
    var histana = parseInt(p.histana) || 0;
    if (histana > 0) {
      lines.push('收益分析，最多' + histana + '个');
    }

    return lines;
  }

  // ─── 备选池属性文本（备选池行中间列） ───
  function buildSourceAttributes(node) {
    var p = node.params || {};
    var attrs = [];

    // 备选范围：优先 attrtext（按 tab/空白拆分后用空格连接），其次 markets 数组
    var rangeText = '';
    if (p.attrtext) {
      var parts = String(p.attrtext).split(/[\t\s]+/).filter(function(s) { return s; });
      rangeText = parts.join(' ');
    } else if (p.markets && p.markets.length > 0) {
      rangeText = p.markets.join(' ');
    } else {
      rangeText = '默认全部A股';
    }
    attrs.push('备选范围: ' + rangeText);

    // 状态中有股票
    var stockCount = 0;
    if (p.stocknum !== undefined) stockCount = parseInt(p.stocknum) || 0;
    attrs.push('状态中有股票:' + stockCount + '只');

    return attrs;
  }

  // ─── 时序参数解析（条件行右侧列） ───
  // upstreamEdge 为 attr=4096 的边，时序参数位于 upstreamEdge.params
  // count 由 dzh_xml_raw 转换器存于 edge.params.count（line 1349）
  function parseTimingParams(upstreamEdge) {
    var lines = [];
    if (!upstreamEdge) return lines;

    var params = upstreamEdge.params || {};
    var begin = params.begin;
    var begint = params.begint;
    var endt = params.endt;
    var interval = params.interval;
    var interval_sec = params.interval_sec;
    var count = params.count;

    // 开始时机
    if (begin === 0) {
      lines.push('立即执行');
    } else if (begin === 1) {
      var bt = parseInt(begint) || 0;
      lines.push('开盘后' + bt + '秒执行');
    } else if (begin === 3) {
      // begint 为 6 位 HHMMSS 整数，左侧补零
      var bt3 = String(begint || 0).padStart(6, '0');
      lines.push('指定在工作日' + parseInt(bt3.slice(0, 2)) + ':' + bt3.slice(2, 4) + ':' + bt3.slice(4, 6) + '执行');
    } else if (begin !== undefined) {
      lines.push(BEGIN_NAMES[begin] || ('模式' + begin));
    }

    // 结束时间
    if (endt !== undefined && endt !== '') {
      var et = parseInt(endt);
      if (!isNaN(et) && et >= 2147483640) {
        lines.push('持续执行');
      } else {
        lines.push('执行' + et + '秒');
      }
    }

    // 执行间隔
    if (interval_sec !== undefined && interval_sec !== '') {
      lines.push('执行间隔时间:' + interval_sec + '秒');
    } else if (interval !== undefined && interval !== '') {
      lines.push('执行间隔时间:' + interval + '秒');
    }

    // 执行次数（保留负值原样，如 -12242）
    if (count !== undefined && count !== 0) {
      lines.push('执行次数:' + count);
    }

    return lines;
  }

  // ─── 构建条件描述文本（从条件节点提取实际内容） ───
  function buildConditionDescription(condNode) {
    if (!condNode) return '无条件转移';
    var p = condNode.params || {};

    // 检查 inditype
    var inditype = p.inditype;

    if (inditype === 'indi') {
      // 技术指标条件 - 尝试提取指标名称
      var name = '';
      if (condNode.text && condNode.text.trim()) {
        name = condNode.text.trim();
      } else if (p.indi_name) {
        name = p.indi_name;
      } else if (p.indi) {
        // indi 是编码后的公式，尝试解码或显示为通用描述
        name = ''; // 无法解码时保持通用
      }

      if (name) {
        return '技术指标:' + name;
      }
      return '满足自设定计算公式转移';
    }

    if (inditype === 'ref') {
      // 引用条件
      var refName = p.ref_name || condNode.text || '';
      if (refName) return '引用:' + refName;
      return '满足条件转移';
    }

    // 默认
    return '满足自设定计算公式转移';
  }

  // ─── 表格渲染 ───
  // 行类型：'source' (备选池) / 'edge' (复合边)
  //   'source' 行：无 └─▶ 箭头，左列只显示备选池彩色标签
  //   'edge'   行：左列 "源池名 └─▶ [目标池彩色标签]"（目标池是行主体），
  //                  中列上半显示条件描述 + 条件设置按钮，下半显示状态属性 + 状态属性设置按钮，
  //                  右列显示上游边时序参数 + 时序设置按钮
  // 每行 = 一条复合边（或备选池首行），全部唯一，不存在"重复行"概念
  function renderComprehensiveTable() {
    if (!$tableBody) return;
    $tableBody.innerHTML = '';

    var rows = buildTableRows();
    if (rows.length === 0) {
      var emptyRow = document.createElement('div');
      emptyRow.className = 'cst-row cst-row-empty';
      var emptyCell = document.createElement('div');
      emptyCell.className = 'cst-cell-empty';
      emptyCell.textContent = '请先加载股票池';
      emptyRow.appendChild(emptyCell);
      $tableBody.appendChild(emptyRow);
      return;
    }

    rows.forEach(function(row, idx) {
      var el = document.createElement('div');
      el.className = 'cst-row';
      if (row.type === 'source') el.classList.add('cst-row-source');
      else if (row.type === 'edge')  el.classList.add('cst-row-edge');
      else if (row.type === 'pool-decl')  el.classList.add('cst-row-pool-decl');
      // BFS 层级深度：source=0，后续 layer N = depth N+1
      // 同 depth 的行表示它们在 BFS 中属于同一层（如同为"初步筛选"的子节点）
      if (typeof row.depth === 'number') {
        el.classList.add('cst-row-depth-' + row.depth);
      }
      if (idx % 2 === 1) el.classList.add('cst-row-odd');

      // ── Flow cell（左列：流程标识） ──
      // DZH 实际结构 (用户最新指示):
      //   备选池行     | 备选池名               (无 └─▶)
      //   边行         | └─▶ + 目标状态池名     (无源池名!源池是上一行池行)
      //   池行         | 源状态池名             (无 └─▶)
      //   边行         | └─▶ + 目标状态池名
      //   边行         | └─▶ + 目标状态池名
      //   ...
      var flowCell = document.createElement('div');
      flowCell.className = 'cst-cell-flow';

      // 边行：仅显示 └─▶ + 目标池名（不再显示源池名，与 DZH 一致）
      if (row.type === 'edge') {
        var prefix = document.createElement('span');
        prefix.className = 'cst-flow-prefix';
        prefix.textContent = '\u2514\u25B6'; // └─▶
        flowCell.appendChild(prefix);
      }

      var label = document.createElement('span');
      label.className = 'cst-flow-label';
      var nodeName = row.cell.label || row.cell.type || (row.type === 'source' ? '备选池' : '状态池');
      label.textContent = nodeName;
      // 背景色取自节点 clr（params.clr 优先）
      var rawClr = (row.cell.params && row.cell.params.clr) || row.cell.clr;
      var fallbackColor = row.type === 'source' ? '#1abc9c' : '#3498db';
      label.style.backgroundColor = dzhColorToCss(rawClr, fallbackColor);
      // 颜色元信息提示（显示调色板名称、hex值等）
      var clrMeta = dzhColorToCss(rawClr, fallbackColor, true);
      if (clrMeta) {
        var clrTip = clrMeta.name + ' (' + clrMeta.hex + ')';
        if (clrMeta.type === 'palette') clrTip += ' [索引:' + clrMeta.index + ']';
        else if (clrMeta.type === 'bgr_direct') clrTip += ' [BGR直接色]';
        label.title = clrTip;
      }
      flowCell.appendChild(label);
      el.appendChild(flowCell);

      // Condition cell（中列：条件/属性）
      var condCell = document.createElement('div');
      condCell.className = 'cst-cell-condition';

      if (row.type === 'source') {
        // ── 备选池行：中列只显示备选范围 + 状态股票数 + 备选范围设置按钮 ──
        buildSourceAttributes(row.cell).forEach(function(text) {
          var div = document.createElement('div');
          div.className = 'cst-cond-text';
          div.textContent = text;
          condCell.appendChild(div);
        });
        var candBtn = document.createElement('button');
        candBtn.className = 'cst-btn-inline cst-btn-inline-candidate';
        candBtn.textContent = '备选范围设置';
        candBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          openCandidatePoolEditor(row.cell.id);
        });
        condCell.appendChild(candBtn);
      } else if (row.type === 'edge') {
        // ── 边行：中列只显示入边条件描述 + 条件设置按钮 ──
        var upperSection = document.createElement('div');
        upperSection.className = 'cst-cond-section cst-cond-upper';
        var condText = document.createElement('div');
        condText.className = 'cst-cond-text cst-cond-transfer';
        // 构建条件描述文本（从条件节点提取实际内容）
        var condDesc = buildConditionDescription(row.conditionNode);
        condText.textContent = condDesc;
        upperSection.appendChild(condText);
        if (row.conditionNode) {
          var condBtn = document.createElement('button');
          condBtn.className = 'cst-btn-inline cst-btn-inline-condition';
          condBtn.textContent = '条件设置';
          condBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            openConditionEditor(row);
          });
          upperSection.appendChild(condBtn);
        }
        condCell.appendChild(upperSection);
      } else if (row.type === 'pool-decl') {
        // ── 状态池声明行：中列显示状态属性 + 状态属性设置按钮 ──
        var poolSection = document.createElement('div');
        poolSection.className = 'cst-cond-section cst-cond-pool';

        // 状态属性
        buildStateAttributes(row.cell).forEach(function(text) {
          var div = document.createElement('div');
          div.className = 'cst-cond-text';
          div.textContent = text;
          poolSection.appendChild(div);
        });

        // 状态属性设置按钮
        var stateBtn = document.createElement('button');
        stateBtn.className = 'cst-btn-inline cst-btn-inline-state';
        stateBtn.textContent = '状态属性设置';
        stateBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          openStateAttributeEditor(row.cell.id);
        });
        poolSection.appendChild(stateBtn);

        condCell.appendChild(poolSection);
      }
      el.appendChild(condCell);

      // Timing cell（右列：时序/操作）
      var timingCell = document.createElement('div');
      timingCell.className = 'cst-cell-timing';

      if (row.type === 'source') {
        // ── 备选池行：右列只显示重载模式 ──
        var ttext = document.createElement('div');
        ttext.className = 'cst-timing-item';
        var reloadSec = row.cell.params && row.cell.params.reload_sec;
        ttext.textContent = formatReloadMode(reloadSec);
        timingCell.appendChild(ttext);
      } else if (row.type === 'edge') {
        // ── 边行：右列显示入边时序参数 + 时序设置按钮 ──
        var tlines = parseTimingParams(row.upstreamEdge);
        if (tlines.length === 0) {
          var noTiming = document.createElement('div');
          noTiming.className = 'cst-timing-item';
          noTiming.textContent = '--';
          timingCell.appendChild(noTiming);
        } else {
          tlines.forEach(function(text) {
            var div = document.createElement('div');
            div.className = 'cst-timing-item';
            div.textContent = text;
            timingCell.appendChild(div);
          });
        }
        var timingBtn = document.createElement('button');
        timingBtn.className = 'cst-btn-inline cst-btn-inline-timing';
        timingBtn.textContent = '时序设置';
        timingBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          openTimingEditor(row);
        });
        timingCell.appendChild(timingBtn);
      } else if (row.type === 'pool-decl') {
        // ── 状态池声明行：右列空 ──
      }
      el.appendChild(timingCell);

      // 点击行 → 选中节点（高亮所有相同 node id 的行，多转移链联动）
      el.addEventListener('click', function() {
        selectNode(row.cell.id);
      });

      // 存储目标池 id 用于选中高亮
      el.setAttribute('data-node-id', row.cell.id);
      if (row.sourceCell) {
        el.setAttribute('data-source-node-id', row.sourceCell.id);
      }

      $tableBody.appendChild(el);
    });
  }

  // ─── 打开备选池编辑器（只编辑 cell[type=202|7] 的 markets/attrtext/reload_sec） ───
  function openCandidatePoolEditor(nodeId) {
    if (!$fieldEditorOverlay) {
      $fieldEditorOverlay = document.getElementById('csFieldEditorOverlay');
    }
    if (!$fieldEditorOverlay) {
      console.warn('[CS] 字段编辑器 DOM 不存在');
      return;
    }

    // 对象隔离：必须是备选池节点（type=202 或 7）
    var data = poolData && poolData.data;
    var node = data && data.nodes ? data.nodes.find(function(n) { return n.id === nodeId; }) : null;
    if (!node) {
      throw new Error('[CS] openCandidatePoolEditor: 未找到节点 ' + nodeId);
    }
    if (!isSourcePoolNode(node)) {
      throw new Error('[CS] openCandidatePoolEditor: 节点 ' + nodeId + ' 不是备选池');
    }

    $fieldEditorOverlay.innerHTML = '';
    var modal = document.createElement('div');
    modal.className = 'cs-field-editor-modal modal';
    modal.style.maxWidth = '520px';

    var h3 = document.createElement('h3');
    h3.textContent = '备选池设置';
    modal.appendChild(h3);

    // 说明：备选池有 3 类参数（5 个字段）：
    //   1) 范围 (attrtext)   - 备选范围明细
    //   2) 市场 (markets)    - 空格分隔的市场代码
    //   3) 重载 (reload_sec) - 5 种重载模式的复合编码

    var body = document.createElement('div');
    body.className = 'cs-field-body';

    var p = node.params || {};

    // ========== 1. 备选范围明细 ==========
    var rangeLabel = document.createElement('label');
    rangeLabel.textContent = '范围明细 (attrtext)';
    rangeLabel.className = 'cs-field-label';
    body.appendChild(rangeLabel);

    var rangeTa = document.createElement('textarea');
    rangeTa.className = 'cs-field-input cs-field-textarea';
    rangeTa.id = 'cand-editor-attrtext';
    rangeTa.rows = 2;
    rangeTa.placeholder = '如: SH600000\tSH#上证A股\tBLK-3RQS\tB$#concept_锂电池';
    rangeTa.value = p.attrtext || '';
    body.appendChild(rangeTa);

    // ========== 1b. 添加备选来源（板块浏览选择器） ==========
    var sourceSection = document.createElement('div');
    sourceSection.style.cssText = 'margin-top:8px;border:1px solid var(--border-color);border-radius:4px;background:rgba(0,0,0,0.15);';

    var sourceHeader = document.createElement('div');
    sourceHeader.style.cssText = 'display:flex;align-items:center;gap:6px;padding:6px 8px;cursor:pointer;font-size:12px;font-weight:600;color:var(--text-primary);user-select:none;';
    sourceHeader.dataset.action = 'toggle-source-panel';

    var sourceArrow = document.createElement('span');
    sourceArrow.textContent = '▶';
    sourceArrow.style.cssText = 'display:inline-block;transition:transform 0.2s;';
    sourceHeader.appendChild(sourceArrow);

    var sourceTitle = document.createElement('span');
    sourceTitle.textContent = '添加备选来源（板块/概念/指数）';
    sourceHeader.appendChild(sourceTitle);
    sourceSection.appendChild(sourceHeader);

    var sourceBody = document.createElement('div');
    sourceBody.style.cssText = 'display:none;padding:6px 8px;border-top:1px solid var(--border-color);';

    // 搜索框
    var searchInp = document.createElement('input');
    searchInp.type = 'text';
    searchInp.placeholder = '搜索名称过滤...';
    searchInp.style.cssText = 'width:100%;box-sizing:border-box;padding:3px 6px;margin-bottom:6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-secondary);color:var(--text-primary);font-size:11px;';
    sourceBody.appendChild(searchInp);

    // 标签页按钮
    var tabsBar = document.createElement('div');
    tabsBar.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;';

    var tabConfigs = [
      { key: 'blocks', label: '自定义板块' },
      { key: 'concept', label: '概念' },
      { key: 'industry', label: '行业' },
      { key: 'index', label: '指数' },
      { key: 'style', label: '风格' },
      { key: 'region', label: '地区' }
    ];

    tabConfigs.forEach(function(tc) {
      var tabBtn = document.createElement('button');
      tabBtn.type = 'button';
      tabBtn.textContent = tc.label;
      tabBtn.dataset.tab = tc.key;
      tabBtn.className = 'cand-source-tab';
      tabBtn.style.cssText = 'padding:3px 8px;font-size:11px;border:1px solid var(--border-color);border-radius:3px;background:transparent;color:var(--text-primary);cursor:pointer;';
      tabsBar.appendChild(tabBtn);
    });
    sourceBody.appendChild(tabsBar);

    // 列表区
    var sourceList = document.createElement('div');
    sourceList.style.cssText = 'max-height:200px;overflow-y:auto;border:1px solid var(--border-color);border-radius:3px;background:rgba(0,0,0,0.2);';

    var sourceHint = document.createElement('div');
    sourceHint.style.cssText = 'padding:8px;font-size:11px;color:var(--text-secondary);';
    sourceHint.textContent = '请选择上方标签查看可添加的备选来源';
    sourceList.appendChild(sourceHint);
    sourceBody.appendChild(sourceList);

    sourceSection.appendChild(sourceBody);
    body.appendChild(sourceSection);

    // 当前加载的来源项（供搜索过滤用）
    var currentSourceItems = [];
    var currentTabType = null;
    // 分类标签映射（attrtext 格式：BLK-{中文分类}{板块名}，如 BLK-概念锂电池）
    var SECTOR_CAT_LABELS = { concept: '概念', industry: '行业', index: '指数', style: '风格', region: '地区' };
    var searchDebounceTimer = null;

    // 内联 HTML 转义（不依赖外部 escapeHtml）
    function _escHtmlLocal(str) {
      if (str == null) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    /** 渲染来源列表（含展开图标，可查看成分股） */
    function renderSourceList(items) {
      sourceList.innerHTML = '';
      if (!items || items.length === 0) {
        var empty = document.createElement('div');
        empty.style.cssText = 'padding:8px;font-size:11px;color:var(--text-secondary);';
        empty.textContent = '无可用来源';
        sourceList.appendChild(empty);
        return;
      }
      var currentAttr = rangeTa.value;
      items.forEach(function(item) {
        var wrap = document.createElement('div');
        wrap.style.cssText = 'border-bottom:1px solid rgba(255,255,255,0.05);';

        var row = document.createElement('div');
        var isAdded = currentAttr.indexOf(item.code) !== -1;
        row.className = 'cand-source-item' + (isAdded ? ' cand-source-item-added' : '');
        row.style.cssText = 'display:flex;align-items:center;padding:4px 8px;font-size:11px;cursor:' + (isAdded ? 'default' : 'pointer') + ';' + (isAdded ? 'opacity:0.5;' : '');
        row.dataset.code = item.code;
        row.dataset.label = item.label;
        row.dataset.sectorId = item.sectorId || '';

        // 展开图标（点击加载成分股）
        var expandIcon = document.createElement('span');
        expandIcon.className = 'cand-source-expand';
        expandIcon.dataset.expanded = '0';
        expandIcon.style.cssText = 'cursor:pointer;width:14px;text-align:center;color:var(--text-secondary);user-select:none;margin-right:4px;';
        expandIcon.textContent = '▶';
        row.appendChild(expandIcon);

        var nameSpan = document.createElement('span');
        nameSpan.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
        nameSpan.textContent = item.label;
        row.appendChild(nameSpan);

        if (item.count !== undefined) {
          var countSpan = document.createElement('span');
          countSpan.style.cssText = 'margin-left:8px;font-size:10px;color:var(--text-secondary);white-space:nowrap;';
          countSpan.textContent = item.count + ' 只';
          row.appendChild(countSpan);
        }

        if (isAdded) {
          var checkSpan = document.createElement('span');
          checkSpan.style.cssText = 'margin-left:6px;color:#27ae60;font-size:12px;';
          checkSpan.textContent = '✓';
          row.appendChild(checkSpan);
        }

        if (!isAdded) {
          row.addEventListener('mouseenter', function() { row.style.background = 'rgba(255,255,255,0.08)'; });
          row.addEventListener('mouseleave', function() { row.style.background = ''; });
        }
        wrap.appendChild(row);

        // 成分股容器（默认隐藏）
        var membersEl = document.createElement('div');
        membersEl.className = 'cand-source-members';
        membersEl.style.cssText = 'display:none;padding:2px 8px 4px 24px;background:rgba(0,0,0,0.15);';
        wrap.appendChild(membersEl);

        // 展开图标点击：加载成分股
        expandIcon.addEventListener('click', function(e) {
          e.stopPropagation();
          var isExpanded = expandIcon.dataset.expanded === '1';
          if (isExpanded) {
            membersEl.style.display = 'none';
            expandIcon.textContent = '▶';
            expandIcon.dataset.expanded = '0';
          } else {
            if (membersEl.dataset.loaded === '1') {
              membersEl.style.display = 'block';
              expandIcon.textContent = '▼';
              expandIcon.dataset.expanded = '1';
            } else {
              var sid = row.dataset.sectorId;
              if (!sid) {
                membersEl.innerHTML = '<div style="font-size:10px;color:var(--text-secondary);">无板块ID，无法加载</div>';
                membersEl.style.display = 'block';
                expandIcon.textContent = '▼';
                expandIcon.dataset.expanded = '1';
                return;
              }
              expandIcon.textContent = '...';
              fetch('/api/meta/candidate-pool/sectors/' + encodeURIComponent(sid) + '/members')
                .then(function(r) { return r.json(); })
                .then(function(resp) {
                  var members = (resp && resp.data && resp.data.members) || (resp && resp.members) || [];
                  var mHtml = '';
                  if (members.length === 0) {
                    mHtml = '<div style="font-size:10px;color:var(--text-secondary);padding:2px 0;">无成分股</div>';
                  } else {
                    members.forEach(function(m) {
                      mHtml += '<div style="display:flex;gap:6px;padding:1px 0;font-size:10px;color:var(--text-secondary);">';
                      mHtml += '<span>' + _escHtmlLocal(m.stock_code || m.code || '') + '</span>';
                      mHtml += '<span>' + _escHtmlLocal(m.name || '') + '</span>';
                      mHtml += '</div>';
                    });
                  }
                  membersEl.innerHTML = mHtml;
                  membersEl.style.display = 'block';
                  membersEl.dataset.loaded = '1';
                  expandIcon.textContent = '▼';
                  expandIcon.dataset.expanded = '1';
                })
                .catch(function() {
                  expandIcon.textContent = '▶';
                  membersEl.innerHTML = '<div style="font-size:10px;color:#e74c3c;padding:2px 0;">加载失败</div>';
                  membersEl.style.display = 'block';
                });
            }
          }
        });

        sourceList.appendChild(wrap);
      });
    }

    /** 加载来源数据（从数据库 API） */
    function loadSourceTab(tabKey, keyword) {
      currentTabType = tabKey;
      currentSourceItems = [];
      sourceList.innerHTML = '';
      var loading = document.createElement('div');
      loading.style.cssText = 'padding:8px;font-size:11px;color:var(--text-secondary);';
      loading.textContent = '加载中...';
      sourceList.appendChild(loading);

      var url;
      if (tabKey === 'blocks') {
        url = '/api/meta/candidate-pool/blocks';
      } else {
        // 从数据库 API 加载（支持 keyword 过滤）
        url = '/api/meta/candidate-pool/local-sectors?category=' + encodeURIComponent(tabKey);
        if (keyword) {
          url += '&keyword=' + encodeURIComponent(keyword);
        }
      }

      fetch(url).then(function(r) { return r.json(); }).then(function(resp) {
        var items = [];
        if (tabKey === 'blocks') {
          var blocks = resp.blocks || resp.data && resp.data.blocks || [];
          blocks.forEach(function(b) {
            items.push({
              code: 'BLK-' + (b.block_code || b.code || ''),
              label: b.block_name || b.name || b.block_code || '',
              count: b.member_count,
              sectorId: b.block_code || b.code || ''
            });
          });
        } else {
          // 新格式：data[tabKey] 直接是数组；旧格式：data.sectors[tabKey]
          var data = (resp && resp.data) ? resp.data : (resp || {});
          var sectors = data[tabKey] || (data.sectors && data.sectors[tabKey]) || [];
          var catLabel = SECTOR_CAT_LABELS[tabKey] || tabKey;
          if (Array.isArray(sectors)) {
            sectors.forEach(function(s) {
              items.push({
                code: 'BLK-' + catLabel + (s.sector_name || s.name || ''),
                label: s.sector_name || s.name || s.code || '',
                count: s.member_count,
                sectorId: s.sector_id || s.id || ''
              });
            });
          }
        }
        currentSourceItems = items;
        renderSourceList(items);
      }).catch(function(err) {
        sourceList.innerHTML = '';
        var errDiv = document.createElement('div');
        errDiv.style.cssText = 'padding:8px;font-size:11px;color:#e74c3c;';
        errDiv.textContent = '加载失败: ' + err.message;
        sourceList.appendChild(errDiv);
      });
    }

    // 折叠/展开
    sourceHeader.addEventListener('click', function() {
      var isHidden = sourceBody.style.display === 'none';
      sourceBody.style.display = isHidden ? 'block' : 'none';
      sourceArrow.style.transform = isHidden ? 'rotate(90deg)' : '';
      // 首次展开时自动加载第一个标签
      if (isHidden && currentSourceItems.length === 0) {
        loadSourceTab('blocks');
        // 高亮第一个标签
        var firstTab = tabsBar.querySelector('[data-tab="blocks"]');
        if (firstTab) firstTab.style.background = 'var(--bg-secondary)';
      }
    });

    // 标签页切换
    tabsBar.addEventListener('click', function(e) {
      var tab = e.target.closest ? e.target.closest('.cand-source-tab') : null;
      if (!tab) return;
      // 取消其他标签高亮
      var allTabs = tabsBar.querySelectorAll('.cand-source-tab');
      allTabs.forEach(function(t) { t.style.background = 'transparent'; });
      tab.style.background = 'var(--bg-secondary)';
      loadSourceTab(tab.dataset.tab);
    });

    // 来源项点击 → 添加到 attrtext
    sourceList.addEventListener('click', function(e) {
      var item = e.target.closest ? e.target.closest('.cand-source-item') : null;
      if (!item || item.classList.contains('cand-source-item-added')) return;
      var code = item.dataset.code;
      var label = item.dataset.label;
      // 追加到 attrtext（Tab 分隔）
      var currentVal = rangeTa.value.trim();
      if (currentVal) {
        rangeTa.value = currentVal + '\t' + code;
      } else {
        rangeTa.value = code;
      }
      // 刷新列表显示
      renderSourceList(currentSourceItems);
    });

    // 搜索过滤（300ms 防抖，调用 API ?keyword=xxx）
    searchInp.addEventListener('input', function() {
      var kw = searchInp.value.trim();
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(function() {
        if (!currentTabType) return;
        if (!kw) {
          // 无关键词：重新加载当前标签
          loadSourceTab(currentTabType);
        } else if (currentTabType === 'blocks') {
          // 自定义板块：本地过滤
          var kwLower = kw.toLowerCase();
          var filtered = currentSourceItems.filter(function(item) {
            return (item.label || '').toLowerCase().indexOf(kwLower) !== -1 ||
                   (item.code || '').toLowerCase().indexOf(kwLower) !== -1;
          });
          renderSourceList(filtered);
        } else {
          // 系统板块：调用 API 过滤
          loadSourceTab(currentTabType, kw);
        }
      }, 300);
    });

    // ========== 1c. 更新同步按钮：POST /api/meta/candidate-pool/sync/all ==========
    // 内联 toast 提示（不依赖外部 showToast）
    function showSyncToast(msg, type) {
      type = type || 'info';
      var t = document.createElement('div');
      t.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:' +
        (type === 'error' ? '#c0392b' : type === 'success' ? '#27ae60' : '#2980b9') +
        ';color:#fff;padding:8px 20px;border-radius:4px;font-size:12px;z-index:10000;transition:opacity 0.3s;';
      t.textContent = msg;
      document.body.appendChild(t);
      setTimeout(function() {
        t.style.opacity = '0';
        setTimeout(function() { if (t.parentNode) t.parentNode.removeChild(t); }, 300);
      }, 2500);
    }

    var syncBtnWrap = document.createElement('div');
    syncBtnWrap.style.cssText = 'margin-top:8px;';
    var syncBtn = document.createElement('button');
    syncBtn.type = 'button';
    syncBtn.className = 'btn btn-primary';
    syncBtn.style.cssText = 'padding:4px 12px;font-size:12px;';
    syncBtn.textContent = '🔄 更新同步';
    syncBtn.title = '从数据源同步板块和股票到数据库';
    syncBtn.addEventListener('click', function() {
      var originalText = syncBtn.textContent;
      syncBtn.disabled = true;
      syncBtn.textContent = '同步中...';
      fetch('/api/meta/candidate-pool/sync/all', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(resp) {
          var data = (resp && resp.data) || resp || {};
          var stockCount = data.synced_count || data.stocks_count || data.stock_count || 0;
          var sectorCount = data.synced_sectors || data.sectors_count || data.sector_count || 0;
          var msg = '同步完成';
          if (stockCount || sectorCount) {
            msg += '：股票 ' + stockCount + ' 条，板块 ' + sectorCount + ' 条';
          }
          syncBtn.disabled = false;
          syncBtn.textContent = originalText;
          showSyncToast(msg, 'success');
          // 刷新当前标签的来源列表
          if (currentTabType) {
            loadSourceTab(currentTabType);
          }
        })
        .catch(function(err) {
          syncBtn.textContent = '同步失败';
          showSyncToast('更新同步失败: ' + (err.message || err), 'error');
          setTimeout(function() {
            syncBtn.disabled = false;
            syncBtn.textContent = originalText;
          }, 2000);
        });
    });
    syncBtnWrap.appendChild(syncBtn);
    body.appendChild(syncBtnWrap);

    // ========== 2. 市场选择 ==========
    var mkLabel = document.createElement('label');
    mkLabel.textContent = '市场选择 (空格分隔)';
    mkLabel.className = 'cs-field-label';
    body.appendChild(mkLabel);

    var mkTa = document.createElement('textarea');
    mkTa.className = 'cs-field-input cs-field-textarea';
    mkTa.id = 'cand-editor-markets';
    mkTa.rows = 1;
    mkTa.placeholder = '如: sh_a sz_a';
    mkTa.value = Array.isArray(p.markets) ? p.markets.join(' ') : String(p.markets || '');
    body.appendChild(mkTa);

    // ========== 3. 重载模式 ==========
    // 5 种模式（与 DZH 一致）：
    //   on_startup    每次启动载入股票        (-57387 或 0)
    //   daily_time    每天指定时间重载        (-57387 + HHMMSS param)
    //   interval      每隔一定时间重载        (正整数 N 秒)
    //   never         不重载                 (2147483647)
    //   on_file_load  文件载入时重载          (2147483646)

    var sectionTitle = document.createElement('div');
    sectionTitle.textContent = '── 重新载入股票时间 ──';
    sectionTitle.style.cssText = 'margin: 14px 0 8px 0; font-weight:600; color: var(--text-primary); border-top: 1px solid var(--border-color); padding-top: 8px;';
    body.appendChild(sectionTitle);

    var reloadInfo = decodeReloadMode(p.reload_sec);
    var curMode = reloadInfo.mode;
    var curParam = reloadInfo.param;

    var rmLabel = document.createElement('label');
    rmLabel.textContent = '重载模式';
    rmLabel.className = 'cs-field-label';
    body.appendChild(rmLabel);

    var rmSelect = document.createElement('select');
    rmSelect.className = 'cs-field-input';
    rmSelect.id = 'cand-editor-reload-mode';
    var reloadModeOptions = [
      { value: 'on_startup',   label: '每次启动载入股票' },
      { value: 'daily_time',   label: '每天指定时间' },
      { value: 'interval',     label: '每隔一定时间' },
      { value: 'never',        label: '不重载' },
      { value: 'on_file_load', label: '文件载入时' },
    ];
    reloadModeOptions.forEach(function(opt) {
      var o = document.createElement('option');
      o.value = opt.value;
      o.textContent = opt.label;
      if (curMode === opt.value) o.selected = true;
      rmSelect.appendChild(o);
    });
    body.appendChild(rmSelect);

    // 时间参数输入（依模式显示/隐藏）
    var paramGroup = document.createElement('div');
    paramGroup.id = 'cand-editor-param-group';
    paramGroup.style.marginTop = '8px';
    body.appendChild(paramGroup);

    var paramLabel = document.createElement('label');
    paramLabel.className = 'cs-field-label';
    paramGroup.appendChild(paramLabel);

    var paramInp = document.createElement('input');
    paramInp.type = 'text';
    paramInp.className = 'cs-field-input';
    paramInp.id = 'cand-editor-reload-param';
    paramInp.style.width = '180px';
    paramGroup.appendChild(paramInp);

    // 提示文本
    var paramHint = document.createElement('div');
    paramHint.id = 'cand-editor-param-hint';
    paramHint.style.cssText = 'font-size: 11px; color: var(--text-secondary); margin-top: 4px;';
    paramGroup.appendChild(paramHint);

    /** 根据当前模式切换 param 输入框的可见性与格式 */
    function syncParamVisibility() {
      var mode = rmSelect.value;
      if (mode === 'interval') {
        paramGroup.style.display = 'block';
        paramLabel.textContent = '间隔时间 (H:MM:SS 或秒数)';
        paramInp.value = curParam ? formatIntervalSec(curParam) : '1:00:00';
        paramHint.textContent = '例: 1:30:40 = 5440秒；存储为 reload_sec=' + encodeReloadMode('interval', encodeTimingSeconds(paramInp.value));
      } else if (mode === 'daily_time') {
        paramGroup.style.display = 'block';
        paramLabel.textContent = '每日时间 (HH:MM:SS)';
        paramInp.value = '09:30:00';
        paramHint.textContent = '例: 09:30:00（每天 9:30 重载；与"每次启动"共用 -57387 编码）';
      } else {
        paramGroup.style.display = 'none';
      }
    }
    rmSelect.addEventListener('change', syncParamVisibility);
    syncParamVisibility();

    // 显示当前 reload_sec 解码结果
    var debugInfo = document.createElement('div');
    debugInfo.style.cssText = 'font-size: 11px; color: var(--text-secondary); margin-top: 8px; font-family: monospace;';
    debugInfo.textContent = '当前 reload_sec = ' + p.reload_sec + ' → ' + formatReloadMode(p.reload_sec);
    body.appendChild(debugInfo);

    modal.appendChild(body);

    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-outline';
    cancelBtn.textContent = '取消';
    cancelBtn.addEventListener('click', closeFieldEditor);
    var okBtn = document.createElement('button');
    okBtn.className = 'btn btn-primary';
    okBtn.textContent = '确定';
    okBtn.addEventListener('click', function() {
      applyCandidatePoolEditor(nodeId, {
        mode: rmSelect.value,
        param: paramInp.value
      });
    });
    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    modal.appendChild(actions);

    $fieldEditorOverlay.appendChild(modal);
    $fieldEditorOverlay.classList.remove('hidden');

    _fieldEditor = { type: 'candidate_pool', nodeId: nodeId };
  }

  // ─── 打开状态池属性编辑器（只编辑 cell[type=200|8] 的状态属性） ───
  function openStateAttributeEditor(nodeId) {
    if (!$fieldEditorOverlay) {
      $fieldEditorOverlay = document.getElementById('csFieldEditorOverlay');
    }
    if (!$fieldEditorOverlay) {
      console.warn('[CS] 字段编辑器 DOM 不存在');
      return;
    }

    // 对象隔离：必须是状态池节点（type=200 或 8）
    var data = poolData && poolData.data;
    var node = data && data.nodes ? data.nodes.find(function(n) { return n.id === nodeId; }) : null;
    if (!node) {
      throw new Error('[CS] openStateAttributeEditor: 未找到节点 ' + nodeId);
    }
    if (!isStatePoolNode(node)) {
      throw new Error('[CS] openStateAttributeEditor: 节点 ' + nodeId + ' 不是状态池');
    }

    $fieldEditorOverlay.innerHTML = '';
    var modal = document.createElement('div');
    modal.className = 'cs-field-editor-modal modal';

    var h3 = document.createElement('h3');
    h3.textContent = '状态属性';
    modal.appendChild(h3);

    var body = document.createElement('div');
    body.className = 'cs-field-body';

    var p = node.params || {};

    // 保持时间 (hold_sec)
    var hsLabel = document.createElement('label');
    hsLabel.textContent = '进入保持时间 (hold_sec, 秒)';
    hsLabel.style.display = 'block';
    hsLabel.style.fontSize = '12px';
    hsLabel.style.color = 'var(--text-secondary)';
    hsLabel.style.marginBottom = '4px';
    body.appendChild(hsLabel);
    var hsInp = document.createElement('input');
    hsInp.type = 'number';
    hsInp.className = 'cs-field-input';
    hsInp.id = 'state-editor-hold-sec';
    hsInp.value = p.hold_sec !== undefined ? String(p.hold_sec) : '0';
    body.appendChild(hsInp);

    // 删除类型
    var dtLabel = document.createElement('label');
    dtLabel.textContent = '删除类型 (deltype)';
    dtLabel.style.display = 'block';
    dtLabel.style.fontSize = '12px';
    dtLabel.style.color = 'var(--text-secondary)';
    dtLabel.style.marginBottom = '4px';
    dtLabel.style.marginTop = '10px';
    body.appendChild(dtLabel);
    var dtSel = document.createElement('select');
    dtSel.className = 'cs-field-input';
    dtSel.id = 'state-editor-deltype';
    Object.keys(DELTYPE_NAMES).forEach(function(k) {
      var o = document.createElement('option');
      o.value = k;
      o.textContent = DELTYPE_NAMES[k];
      if (parseInt(p.deltype) === parseInt(k)) o.selected = true;
      dtSel.appendChild(o);
    });
    body.appendChild(dtSel);

    // 删除时间 (endtime)
    var etLabel = document.createElement('label');
    etLabel.textContent = '删除时间 (endtime, HH:MM:SS)';
    etLabel.style.display = 'block';
    etLabel.style.fontSize = '12px';
    etLabel.style.color = 'var(--text-secondary)';
    etLabel.style.marginBottom = '4px';
    etLabel.style.marginTop = '10px';
    body.appendChild(etLabel);
    var etInp = document.createElement('input');
    etInp.type = 'text';
    etInp.className = 'cs-field-input';
    etInp.id = 'state-editor-endtime';
    etInp.placeholder = 'HH:MM:SS';
    etInp.value = formatEndtime(p.endtime);
    body.appendChild(etInp);

    // 收益分析 (histana)
    var haLabel = document.createElement('label');
    haLabel.textContent = '收益分析，最多N个 (histana)';
    haLabel.style.display = 'block';
    haLabel.style.fontSize = '12px';
    haLabel.style.color = 'var(--text-secondary)';
    haLabel.style.marginBottom = '4px';
    haLabel.style.marginTop = '10px';
    body.appendChild(haLabel);
    var haInp = document.createElement('input');
    haInp.type = 'number';
    haInp.className = 'cs-field-input';
    haInp.id = 'state-editor-histana';
    haInp.value = p.histana !== undefined ? String(p.histana) : '0';
    body.appendChild(haInp);

    // 列定义 (col_list)
    var clLabel = document.createElement('label');
    clLabel.textContent = '显示列 (col_list, 逗号或空格分隔)';
    clLabel.style.display = 'block';
    clLabel.style.fontSize = '12px';
    clLabel.style.color = 'var(--text-secondary)';
    clLabel.style.marginBottom = '4px';
    clLabel.style.marginTop = '10px';
    body.appendChild(clLabel);
    var clTa = document.createElement('textarea');
    clTa.className = 'cs-field-input cs-field-textarea';
    clTa.id = 'state-editor-col-list';
    clTa.rows = 1;
    clTa.value = Array.isArray(p.col_list) ? p.col_list.join(',') : String(p.col_list || '');
    body.appendChild(clTa);

    // dzh_attr 位标志：alert_sound / record_history
    var attrRaw = (p.dzh_attr && typeof p.dzh_attr.raw === 'number') ? p.dzh_attr.raw : 0;
    var alertSoundChecked = (p.dzh_attr && p.dzh_attr.bits && p.dzh_attr.bits.alert_sound) || !!(attrRaw & 0x01000000);
    var recordHistoryChecked = (p.dzh_attr && p.dzh_attr.bits && p.dzh_attr.bits.record_history) || !!(attrRaw & 0x08000000);

    var soundLabel = document.createElement('label');
    soundLabel.className = 'cs-field-checkbox';
    var soundCb = document.createElement('input');
    soundCb.type = 'checkbox';
    soundCb.id = 'state-editor-alert-sound';
    soundCb.checked = alertSoundChecked;
    soundLabel.appendChild(soundCb);
    var soundSp = document.createElement('span');
    soundSp.textContent = '进入时发出声音 (alert_sound)';
    soundLabel.appendChild(soundSp);
    body.appendChild(soundLabel);

    var recLabel = document.createElement('label');
    recLabel.className = 'cs-field-checkbox';
    var recCb = document.createElement('input');
    recCb.type = 'checkbox';
    recCb.id = 'state-editor-record-history';
    recCb.checked = recordHistoryChecked;
    recLabel.appendChild(recCb);
    var recSp = document.createElement('span');
    recSp.textContent = '记录历史数据 (record_history)';
    recLabel.appendChild(recSp);
    body.appendChild(recLabel);

    modal.appendChild(body);

    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-outline';
    cancelBtn.textContent = '取消';
    cancelBtn.addEventListener('click', closeFieldEditor);
    var okBtn = document.createElement('button');
    okBtn.className = 'btn btn-primary';
    okBtn.textContent = '确定';
    okBtn.addEventListener('click', function() {
      applyStateAttributeEditor(nodeId);
    });
    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    modal.appendChild(actions);

    $fieldEditorOverlay.appendChild(modal);
    $fieldEditorOverlay.classList.remove('hidden');

    _fieldEditor = { type: 'state_attribute', nodeId: nodeId };
  }

  // ─── 打开条件编辑器（只编辑 cell[type=201] 的 inditype/indi/转移选项位） ───
  function openConditionEditor(row) {
    if (!$fieldEditorOverlay) {
      $fieldEditorOverlay = document.getElementById('csFieldEditorOverlay');
    }
    if (!$fieldEditorOverlay) {
      console.warn('[CS] 字段编辑器 DOM 不存在');
      return;
    }

    var condNode = row && row.conditionNode;

    // 对象隔离：必须是条件节点
    if (condNode && !isConditionNode(condNode)) {
      throw new Error('[CS] openConditionEditor: 目标不是条件节点');
    }

    $fieldEditorOverlay.innerHTML = '';
    var modal = document.createElement('div');
    modal.className = 'cs-field-editor-modal modal';

    var h3 = document.createElement('h3');
    h3.textContent = '设置转移条件';
    modal.appendChild(h3);

    var body = document.createElement('div');
    body.className = 'cs-field-body';

    var condSection = document.createElement('div');

    if (condNode) {
      var itLabel = document.createElement('label');
      itLabel.textContent = '条件类型';
      itLabel.style.display = 'block';
      itLabel.style.fontSize = '12px';
      itLabel.style.color = 'var(--text-secondary)';
      itLabel.style.marginBottom = '4px';
      condSection.appendChild(itLabel);

      var itSelect = document.createElement('select');
      itSelect.className = 'cs-field-input';
      itSelect.id = 'cond-editor-inditype';
      Object.keys(INDI_TYPE_NAMES).forEach(function(k) {
        var o = document.createElement('option');
        o.value = k;
        o.textContent = INDI_TYPE_NAMES[k];
        if ((condNode.params && condNode.params.inditype) === k) o.selected = true;
        itSelect.appendChild(o);
      });
      condSection.appendChild(itSelect);

      var indiLabel = document.createElement('label');
      indiLabel.textContent = '编码公式';
      indiLabel.style.display = 'block';
      indiLabel.style.fontSize = '12px';
      indiLabel.style.color = 'var(--text-secondary)';
      indiLabel.style.marginBottom = '4px';
      indiLabel.style.marginTop = '10px';
      condSection.appendChild(indiLabel);

      var indiTa = document.createElement('textarea');
      indiTa.className = 'cs-field-input cs-field-textarea';
      indiTa.id = 'cond-editor-indi';
      indiTa.rows = 3;
      indiTa.value = (condNode.params && condNode.params.indi) || '';
      condSection.appendChild(indiTa);

      var formulaBtn = document.createElement('button');
      formulaBtn.className = 'btn btn-outline';
      formulaBtn.textContent = '\uD83D\uDCD0 从公式库选择';
      formulaBtn.style.marginTop = '6px';
      formulaBtn.style.fontSize = '12px';
      formulaBtn.addEventListener('click', function(e) {
        e.preventDefault();
        openFormulaSelector();
      });
      condSection.appendChild(formulaBtn);

      var coLabel = document.createElement('label');
      coLabel.textContent = '转移选项';
      coLabel.style.display = 'block';
      coLabel.style.fontSize = '12px';
      coLabel.style.color = 'var(--text-secondary)';
      coLabel.style.marginBottom = '4px';
      coLabel.style.marginTop = '10px';
      condSection.appendChild(coLabel);

      var coRaw = (condNode.params && condNode.params.dzh_attr && typeof condNode.params.dzh_attr.raw === 'number') ? condNode.params.dzh_attr.raw : 0;
      var coWrap = document.createElement('div');
      coWrap.className = 'cs-field-multi-checkbox';
      [
        { key: 'condition_output_constituent', mask: 0x80000, label: '输出成分股' },
        { key: 'condition_keep_source', mask: 0x100000, label: '保留源池' },
        { key: 'condition_clear_dest', mask: 0x200000, label: '清空目标池' }
      ].forEach(function(cb) {
        var lab = document.createElement('label');
        lab.className = 'cs-field-checkbox';
        var input = document.createElement('input');
        input.type = 'checkbox';
        input.setAttribute('data-mask', String(cb.mask));
        input.setAttribute('data-key', cb.key);
        input.checked = !!(coRaw & cb.mask);
        lab.appendChild(input);
        var sp = document.createElement('span');
        sp.textContent = cb.label;
        lab.appendChild(sp);
        coWrap.appendChild(lab);
      });
      condSection.appendChild(coWrap);
    } else {
      var noCond = document.createElement('div');
      noCond.textContent = '无条件转入';
      noCond.style.color = 'var(--text-muted)';
      noCond.style.fontSize = '12px';
      condSection.appendChild(noCond);
    }
    body.appendChild(condSection);
    modal.appendChild(body);

    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-outline';
    cancelBtn.textContent = '取消';
    cancelBtn.addEventListener('click', closeFieldEditor);
    var okBtn = document.createElement('button');
    okBtn.className = 'btn btn-primary';
    okBtn.textContent = '确定';
    okBtn.addEventListener('click', function() {
      applyConditionEditor(row);
    });
    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    modal.appendChild(actions);

    $fieldEditorOverlay.appendChild(modal);
    $fieldEditorOverlay.classList.remove('hidden');

    _fieldEditor = { type: 'condition_only', row: row };
  }

  // ─── 打开时序编辑器（只编辑 flow 边的 begin/begint/endt/interval_sec/count/attr 位） ───
  function openTimingEditor(row) {
    if (!$fieldEditorOverlay) {
      $fieldEditorOverlay = document.getElementById('csFieldEditorOverlay');
    }
    if (!$fieldEditorOverlay) {
      console.warn('[CS] 字段编辑器 DOM 不存在');
      return;
    }

    var upstreamEdge = row && row.upstreamEdge;

    // 对象隔离：必须是上游边（具备 begin 等时序参数）
    if (!upstreamEdge || !upstreamEdge.params || upstreamEdge.params.begin === undefined) {
      throw new Error('[CS] openTimingEditor: 行无有效上游边/时序参数');
    }

    $fieldEditorOverlay.innerHTML = '';
    var modal = document.createElement('div');
    modal.className = 'cs-field-editor-modal modal';

    var h3 = document.createElement('h3');
    h3.textContent = '设置时序参数';
    modal.appendChild(h3);

    var body = document.createElement('div');
    body.className = 'cs-field-body';

    var timingSection = document.createElement('div');

    if (upstreamEdge) {
      var up = upstreamEdge.params || {};

      var beginLabel = document.createElement('label');
      beginLabel.textContent = '开始时机';
      beginLabel.style.display = 'block';
      beginLabel.style.fontSize = '12px';
      beginLabel.style.color = 'var(--text-secondary)';
      beginLabel.style.marginBottom = '4px';
      timingSection.appendChild(beginLabel);

      var beginSelect = document.createElement('select');
      beginSelect.className = 'cs-field-input';
      beginSelect.id = 'cond-editor-begin';
      Object.keys(BEGIN_NAMES).forEach(function(k) {
        var o = document.createElement('option');
        o.value = k;
        o.textContent = BEGIN_NAMES[k];
        if (parseInt(up.begin) === parseInt(k)) o.selected = true;
        beginSelect.appendChild(o);
      });
      timingSection.appendChild(beginSelect);

      var begintLabel = document.createElement('label');
      begintLabel.textContent = '开始延迟/时间';
      begintLabel.style.display = 'block';
      begintLabel.style.fontSize = '12px';
      begintLabel.style.color = 'var(--text-secondary)';
      begintLabel.style.marginBottom = '4px';
      begintLabel.style.marginTop = '10px';
      timingSection.appendChild(begintLabel);

      var begintInp = document.createElement('input');
      begintInp.type = 'text';
      begintInp.className = 'cs-field-input';
      begintInp.id = 'cond-editor-begint';
      begintInp.placeholder = '秒数 或 HH:MM:SS';
      begintInp.value = up.begint !== undefined ? String(up.begint) : '';
      timingSection.appendChild(begintInp);

      var endtLabel = document.createElement('label');
      endtLabel.textContent = '结束时间';
      endtLabel.style.display = 'block';
      endtLabel.style.fontSize = '12px';
      endtLabel.style.color = 'var(--text-secondary)';
      endtLabel.style.marginBottom = '4px';
      endtLabel.style.marginTop = '10px';
      timingSection.appendChild(endtLabel);

      var endtInp = document.createElement('input');
      endtInp.type = 'text';
      endtInp.className = 'cs-field-input';
      endtInp.id = 'cond-editor-endt';
      endtInp.placeholder = '秒数 或 HH:MM:SS';
      endtInp.value = up.endt !== undefined ? String(up.endt) : '';
      timingSection.appendChild(endtInp);

      var intLabel = document.createElement('label');
      intLabel.textContent = '执行间隔(秒)';
      intLabel.style.display = 'block';
      intLabel.style.fontSize = '12px';
      intLabel.style.color = 'var(--text-secondary)';
      intLabel.style.marginBottom = '4px';
      intLabel.style.marginTop = '10px';
      timingSection.appendChild(intLabel);

      var intInp = document.createElement('input');
      intInp.type = 'number';
      intInp.className = 'cs-field-input';
      intInp.id = 'cond-editor-interval';
      intInp.value = up.interval_sec !== undefined ? String(up.interval_sec) : '';
      timingSection.appendChild(intInp);

      var countLabel = document.createElement('label');
      countLabel.textContent = '执行次数';
      countLabel.style.display = 'block';
      countLabel.style.fontSize = '12px';
      countLabel.style.color = 'var(--text-secondary)';
      countLabel.style.marginBottom = '4px';
      countLabel.style.marginTop = '10px';
      timingSection.appendChild(countLabel);

      var countInp = document.createElement('input');
      countInp.type = 'number';
      countInp.className = 'cs-field-input';
      countInp.id = 'cond-editor-count';
      var cval = (up.count !== undefined) ? up.count : '';
      countInp.value = cval !== '' ? String(cval) : '';
      timingSection.appendChild(countInp);

      var tmLabel = document.createElement('label');
      tmLabel.textContent = '传输模式';
      tmLabel.style.display = 'block';
      tmLabel.style.fontSize = '12px';
      tmLabel.style.color = 'var(--text-secondary)';
      tmLabel.style.marginBottom = '4px';
      tmLabel.style.marginTop = '10px';
      timingSection.appendChild(tmLabel);

      var tmRaw = (upstreamEdge.attr && typeof upstreamEdge.attr.attr === 'number') ? upstreamEdge.attr.attr : 0;
      var tmWrap = document.createElement('div');
      tmWrap.className = 'cs-field-multi-checkbox';
      [
        { key: 'transfer_delete_source', mask: 0x1, label: '删除源' },
        { key: 'transfer_keep_source', mask: 0x1000, label: '保留源' },
        { key: 'transfer_clear_dest', mask: 0x2000, label: '清空目标' },
        { key: 'transfer_output_constituent', mask: 0x4000, label: '输出成分股' }
      ].forEach(function(cb) {
        var lab = document.createElement('label');
        lab.className = 'cs-field-checkbox';
        var input = document.createElement('input');
        input.type = 'checkbox';
        input.setAttribute('data-mask', String(cb.mask));
        input.setAttribute('data-key', cb.key);
        input.checked = !!(tmRaw & cb.mask);
        lab.appendChild(input);
        var sp = document.createElement('span');
        sp.textContent = cb.label;
        lab.appendChild(sp);
        tmWrap.appendChild(lab);
      });
      timingSection.appendChild(tmWrap);
    } else {
      var noTiming = document.createElement('div');
      noTiming.textContent = '无时序参数';
      noTiming.style.color = 'var(--text-muted)';
      noTiming.style.fontSize = '12px';
      timingSection.appendChild(noTiming);
    }
    body.appendChild(timingSection);
    modal.appendChild(body);

    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-outline';
    cancelBtn.textContent = '取消';
    cancelBtn.addEventListener('click', closeFieldEditor);
    var okBtn = document.createElement('button');
    okBtn.className = 'btn btn-primary';
    okBtn.textContent = '确定';
    okBtn.addEventListener('click', function() {
      applyTimingEditor(row);
    });
    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    modal.appendChild(actions);

    $fieldEditorOverlay.appendChild(modal);
    $fieldEditorOverlay.classList.remove('hidden');

    _fieldEditor = { type: 'timing_only', row: row };
  }

  // ─── 应用备选池编辑器修改（只写 cell[type=202|7] 的 markets/attrtext/reload_sec） ───
  function applyCandidatePoolEditor(nodeId, reloadArg) {
    var data = poolData && poolData.data;
    var node = data && data.nodes ? data.nodes.find(function(n) { return n.id === nodeId; }) : null;
    if (!node || !isSourcePoolNode(node)) {
      // 对象隔离：错误对象类型，不写入内存
      closeFieldEditor();
      return;
    }

    var newAttrtext = document.getElementById('cand-editor-attrtext').value;
    var newMarkets = document.getElementById('cand-editor-markets').value;

    // 重载模式：根据调用方传入的 {mode, param} 重新编码
    var reloadMode, reloadParam, newReloadSec;
    if (reloadArg && typeof reloadArg === 'object' && reloadArg.mode) {
      reloadMode = reloadArg.mode;
      reloadParam = reloadArg.param;
      if (reloadMode === 'interval') {
        // 把 H:MM:SS 字符串或秒数转成整数秒
        var sec = encodeTimingSeconds(reloadParam);
        newReloadSec = encodeReloadMode('interval', sec);
      } else {
        // on_startup / daily_time / never / on_file_load
        newReloadSec = encodeReloadMode(reloadMode);
      }
    } else {
      // 旧调用约定（向后兼容）：从老 DOM 读取
      var newReloadInterval = parseInt(document.getElementById('cand-editor-reload-interval').value) || 0;
      var reloadSelect = document.getElementById('cand-editor-reload').value;
      newReloadSec = newReloadInterval >= 2 ? newReloadInterval : (parseInt(reloadSelect) || 0);
    }

    var marketsArr = String(newMarkets || '').split(/[\s,]+/).filter(function(s) { return s; });

    var changes = {
      attrtext: newAttrtext,
      markets: marketsArr,
      reload_sec: newReloadSec
    };

    if (poolData.updateNodeParams) {
      poolData.updateNodeParams(nodeId, changes);
    } else {
      node.params = node.params || {};
      Object.keys(changes).forEach(function(k) { node.params[k] = changes[k]; });
    }

    markDirty();
    closeFieldEditor();
    renderComprehensiveTable();
    if (_selectedNodeId !== null) {
      selectNode(_selectedNodeId);
    }
  }

  // ─── 应用状态属性编辑器修改（只写 cell[type=200|8] 的状态属性） ───
  function applyStateAttributeEditor(nodeId) {
    var data = poolData && poolData.data;
    var node = data && data.nodes ? data.nodes.find(function(n) { return n.id === nodeId; }) : null;
    if (!node || !isStatePoolNode(node)) {
      // 对象隔离：错误对象类型，不写入内存
      closeFieldEditor();
      return;
    }

    var newHoldSec = parseInt(document.getElementById('state-editor-hold-sec').value) || 0;
    var newDeltype = parseInt(document.getElementById('state-editor-deltype').value) || 0;
    var newEndtimeStr = document.getElementById('state-editor-endtime').value;
    var newHistana = parseInt(document.getElementById('state-editor-histana').value) || 0;
    var newColList = document.getElementById('state-editor-col-list').value;
    var alertSoundChecked = document.getElementById('state-editor-alert-sound').checked;
    var recordHistoryChecked = document.getElementById('state-editor-record-history').checked;

    var colListArr = String(newColList || '').split(/[,;\s]+/).filter(function(s) { return s; });

    // 先按已有 attr 读 raw（兼容老数据 attr 位标志）
    var existingAttrRaw = (node.params && node.params.dzh_attr && typeof node.params.dzh_attr.raw === 'number')
      ? node.params.dzh_attr.raw
      : (node.params && node.params.attr !== undefined ? (parseInt(node.params.attr) || 0) : 0);
    var newAttrRaw = existingAttrRaw;
    if (alertSoundChecked) newAttrRaw |= 0x01000000; else newAttrRaw &= ~0x01000000;
    if (recordHistoryChecked) newAttrRaw |= 0x08000000; else newAttrRaw &= ~0x08000000;

    var changes = {
      hold_sec: newHoldSec,
      deltype: newDeltype,
      endtime: encodeEndtime(newEndtimeStr),
      histana: newHistana,
      col_list: colListArr,
      dzh_attr: { raw: newAttrRaw, bits: { alert_sound: alertSoundChecked, record_history: recordHistoryChecked } }
    };

    if (poolData.updateNodeParams) {
      poolData.updateNodeParams(nodeId, changes);
    } else {
      node.params = node.params || {};
      Object.keys(changes).forEach(function(k) {
        if (k === 'dzh_attr') {
          node.params.dzh_attr = changes.dzh_attr;
        } else {
          node.params[k] = changes[k];
        }
      });
    }

    markDirty();
    closeFieldEditor();
    renderComprehensiveTable();
    if (_selectedNodeId !== null) {
      selectNode(_selectedNodeId);
    }
  }

  // ─── 应用条件编辑器修改（只保存转移条件） ───
  function applyConditionEditor(row) {
    var condNode = row.conditionNode;

    if (condNode) {
      var newInditype = document.getElementById('cond-editor-inditype').value;
      var newIndi = document.getElementById('cond-editor-indi').value;

      if (poolData.updateNodeParams) {
        poolData.updateNodeParams(condNode.id, { inditype: newInditype, indi: newIndi });
      } else {
        condNode.params = condNode.params || {};
        condNode.params.inditype = newInditype;
        condNode.params.indi = newIndi;
      }

      var coRaw = (condNode.params && condNode.params.dzh_attr && typeof condNode.params.dzh_attr.raw === 'number') ? condNode.params.dzh_attr.raw : 0;
      var allCheckboxes = $fieldEditorOverlay.querySelectorAll('.cs-field-multi-checkbox input[type=checkbox]');
      allCheckboxes.forEach(function(cb) {
        var mask = parseInt(cb.getAttribute('data-mask'), 10);
        var key = cb.getAttribute('data-key') || '';
        if (key.indexOf('condition_') === 0) {
          if (cb.checked) coRaw |= mask;
          else coRaw &= ~mask;
        }
      });

      if (poolData.updateNodeParams) {
        poolData.updateNodeParams(condNode.id, { dzh_attr: { raw: coRaw } });
      } else {
        condNode.params = condNode.params || {};
        condNode.params.dzh_attr = condNode.params.dzh_attr || { raw: 0, bits: {} };
        condNode.params.dzh_attr.raw = coRaw;
      }
    }

    markDirty();
    closeFieldEditor();
    renderComprehensiveTable();
    if (_selectedNodeId !== null) {
      selectNode(_selectedNodeId);
    }
  }

  // ─── 应用时序编辑器修改（只保存时序参数） ───
  function applyTimingEditor(row) {
    var upstreamEdge = row.upstreamEdge;

    if (upstreamEdge) {
      var newBegin = parseInt(document.getElementById('cond-editor-begin').value) || 0;
      var newBegint = document.getElementById('cond-editor-begint').value;
      var newEndt = document.getElementById('cond-editor-endt').value;
      var newInterval = document.getElementById('cond-editor-interval').value;
      var newCount = document.getElementById('cond-editor-count').value;

      var flatParamChanges = {};
      flatParamChanges.begin = newBegin;
      if (newBegint !== '') flatParamChanges.begint = parseInt(newBegint) || 0;
      if (newEndt !== '') flatParamChanges.endt = parseInt(newEndt) || 0;
      if (newInterval !== '') flatParamChanges.interval_sec = parseInt(newInterval) || 0;
      if (newCount !== '') {
        var c = parseInt(newCount);
        if (isNaN(c)) c = -1;
        flatParamChanges.count = c;
      }

      var tmRaw = (upstreamEdge.attr && typeof upstreamEdge.attr.attr === 'number') ? upstreamEdge.attr.attr : 0;
      var allCheckboxes = $fieldEditorOverlay.querySelectorAll('.cs-field-multi-checkbox input[type=checkbox]');
      allCheckboxes.forEach(function(cb) {
        var mask = parseInt(cb.getAttribute('data-mask'), 10);
        var key = cb.getAttribute('data-key') || '';
        if (key.indexOf('transfer_') === 0) {
          if (cb.checked) tmRaw |= mask;
          else tmRaw &= ~mask;
        }
      });

      if (poolData.updateEdge) {
        poolData.updateEdge(upstreamEdge.id, flatParamChanges);
      } else {
        upstreamEdge.params = upstreamEdge.params || {};
        Object.keys(flatParamChanges).forEach(function(k) {
          upstreamEdge.params[k] = flatParamChanges[k];
        });
      }

      if (!upstreamEdge.attr) upstreamEdge.attr = {};
      upstreamEdge.attr.attr = tmRaw;

      syncAttrParity(upstreamEdge, tmRaw);
    }

    markDirty();
    closeFieldEditor();
    renderComprehensiveTable();
    if (_selectedNodeId !== null) {
      selectNode(_selectedNodeId);
    }
  }

  // ─── 节点选择（高亮所有相同 node id 的行，多转移链联动） ───
  function selectNode(nodeId) {
    _selectedNodeId = nodeId;

    // 高亮表格行
    if ($tableBody) {
      var rows = $tableBody.querySelectorAll('.cst-row');
      for (var i = 0; i < rows.length; i++) {
        rows[i].classList.toggle('cst-selected', rows[i].getAttribute('data-node-id') === String(nodeId));
      }
    }

  }

  // ─── 工具函数 ───
  function ensureBits(node) {
    if (!node.params) node.params = {};
    if (!node.params.dzh_attr) node.params.dzh_attr = { raw: 0, bits: {} };
    if (!node.params.dzh_attr.bits) node.params.dzh_attr.bits = {};
    return node.params.dzh_attr.bits;
  }

  // formatHoldTime：保持时间格式化
  //   < 60 秒 → N秒
  //   < 3600 秒 → N分钟
  //   < 86400 秒 → N小时  (64800 → "18小时")
  //   >= 86400 秒 → N天  (86400 → "1天", 2592000 → "30天", 4320000 → "50天", 1728000 → "20天")
  //   <= 0 → "永久保持"
  function formatHoldTime(seconds) {
    seconds = parseInt(seconds) || 0;
    if (seconds <= 0) return '永久保持';
    if (seconds < 60) return seconds + '秒';
    if (seconds < 3600) return Math.floor(seconds / 60) + '分钟';
    if (seconds < 86400) return Math.floor(seconds / 3600) + '小时';
    return Math.floor(seconds / 86400) + '天';
  }

  function parseHoldSeconds(str) {
    if (str === null || str === undefined) return 0;
    var s = String(str).trim();
    if (!s || s === '永久' || s === '永久保持') return 0;
    var m;
    if ((m = s.match(/^(\d+)\s*秒$/))) return parseInt(m[1], 10);
    if ((m = s.match(/^(\d+)\s*分钟$/))) return parseInt(m[1], 10) * 60;
    if ((m = s.match(/^(\d+)\s*小时$/))) return parseInt(m[1], 10) * 3600;
    if ((m = s.match(/^(\d+)\s*天/))) return parseInt(m[1], 10) * 86400;
    var n = parseInt(s, 10);
    return isNaN(n) ? 0 : n;
  }

  function formatTimestamp(ts) {
    if (!ts || ts === 0) return '--';
    ts = parseInt(ts) || 0;
    var d = new Date(ts * 1000);
    if (isNaN(d.getTime())) return '--';
    return d.getFullYear() + '-' +
      String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0') + ' ' +
      String(d.getHours()).padStart(2, '0') + ':' +
      String(d.getMinutes()).padStart(2, '0') + ':' +
      String(d.getSeconds()).padStart(2, '0');
  }

  function formatTimingSeconds(sec) {
    sec = parseInt(sec) || 0;
    if (sec === 0) return '立即';
    if (sec === 2147483647) return '收市';
    if (sec >= 21600 && sec <= 23400) return '收盘附近';
    if (sec >= 3600) {
      var h = Math.floor(sec / 3600);
      var m = Math.floor((sec % 3600) / 60);
      if (h > 0 && m > 0) return h + '时' + m + '分';
      if (h > 0) return h + '小时';
    }
    return '开盘后' + sec + '秒';
  }

  function parseTimingSeconds(str) {
    if (str === null || str === undefined) return 0;
    var s = String(str).trim();
    if (!s || s === '立即') return 0;
    if (s === '收市' || s === '持续至收市') return 2147483647;
    var m;
    if ((m = s.match(/^开盘后\s*(\d+)\s*秒$/))) return parseInt(m[1], 10);
    if ((m = s.match(/^(\d{1,2}):(\d{1,2}):(\d{1,2})$/))) {
      return parseInt(m[1], 10) * 3600 + parseInt(m[2], 10) * 60 + parseInt(m[3], 10);
    }
    var n = parseInt(s, 10);
    return isNaN(n) ? 0 : n;
  }

  function decodeEndtime(v) {
    var n = parseInt(v) || 0;
    if (n <= 0) return { HH: 0, MM: 0, SS: 0, raw: 0 };
    for (var mm = 0; mm < 60; mm++) {
      for (var hh = 0; hh < 24; hh++) {
        for (var ss = 0; ss < 60; ss++) {
          if (3600 * hh - 900 * mm + ss === n) {
            return { HH: hh, MM: mm, SS: ss, raw: n };
          }
        }
      }
    }
    return { HH: 0, MM: 0, SS: 0, raw: n };
  }

  function encodeEndtime(input) {
    if (typeof input === 'number') return input;
    if (typeof input === 'string') {
      var s = input.trim();
      if (!s) return 0;
      var m = s.match(/^(\d{1,2}):(\d{1,2}):(\d{1,2})$/);
      if (m) {
        var hh = parseInt(m[1], 10), mm = parseInt(m[2], 10), ss = parseInt(m[3], 10);
        return 3600 * hh - 900 * mm + ss;
      }
      return parseInt(s, 10) || 0;
    }
    if (input && typeof input === 'object' && input.HH !== undefined) {
      return 3600 * (input.HH | 0) - 900 * (input.MM | 0) + (input.SS | 0);
    }
    return 0;
  }

  function formatEndtime(v) {
    var d = decodeEndtime(v);
    if (d.raw === 0) return '--';
    return String(d.HH).padStart(2, '0') + ':' +
           String(d.MM).padStart(2, '0') + ':' +
           String(d.SS).padStart(2, '0');
  }

  // ─── 备选池重载模式常量已在文件顶部统一定义（DEFAULT_RELOAD_* + 运行时 RELOAD_*） ─────────
  // 来源：DZH股票池完整技术文档 §4.9 + converters/dzh_xml_raw.py
  // 运行时常量从 defaults.json 的 reload_modes 加载，加载失败回退到 DEFAULT_RELOAD_*

  /**
   * 解码 reload_sec 整数值为语义化重载模式。
   * 与后端 converters/dzh_xml_raw.py#decode_reload_mode 保持等价。
   * @param {number|string} v - reload_sec 值
   * @returns {{mode: string, param: number|null}}
   *   mode ∈ {"on_startup", "daily_time", "interval", "never", "on_file_load"}
   */
  function decodeReloadMode(v) {
    var n = parseInt(v, 10);
    if (isNaN(n)) n = 0;
    if (n === RELOAD_NEVER)        return { mode: 'never', param: null };
    if (n === RELOAD_ON_FILE_LOAD) return { mode: 'on_file_load', param: null };
    if (n > 0)                     return { mode: 'interval', param: n };
    if (n === 0 || n === RELOAD_ON_STARTUP) return { mode: 'on_startup', param: null };
    // 其它负数：保持原样（on_startup）
    return { mode: 'on_startup', param: null };
  }

  /**
   * 编码语义化重载模式为 reload_sec 整数值。
   * 与后端 encode_reload_mode 保持等价。
   * @param {string} mode - on_startup | daily_time | interval | never | on_file_load
   * @param {number|null} param - interval 模式下的秒数
   * @returns {number} reload_sec 整数值
   */
  function encodeReloadMode(mode, param) {
    if (mode === 'never')        return RELOAD_NEVER;
    if (mode === 'on_file_load') return RELOAD_ON_FILE_LOAD;
    if (mode === 'interval') {
      var n = parseInt(param, 10);
      return isNaN(n) || n < 1 ? 60 : n; // 默认 60 秒
    }
    // on_startup / daily_time 都映射到 RELOAD_ON_STARTUP（XML 编码相同，靠 param 区分）
    return RELOAD_ON_STARTUP;
  }

  /** 将秒数格式化为 H:MM:SS 或 MM:SS */
  function formatIntervalSec(totalSeconds) {
    var s = parseInt(totalSeconds, 10);
    if (isNaN(s) || s < 0) s = 0;
    if (s < 60) return s + '秒';
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    var pad2 = function(x) { return x < 10 ? '0' + x : '' + x; };
    if (h > 0) return h + ':' + pad2(m) + ':' + pad2(sec);
    return m + ':' + pad2(sec);
  }

  /** 将 HHMMSS 整数（093000）格式化为 9:30:00 */
  function formatHHMMSS(n) {
    var v = parseInt(n, 10);
    if (isNaN(v) || v < 0) v = 0;
    var h = Math.floor(v / 10000);
    var m = Math.floor((v % 10000) / 100);
    var s = v % 100;
    var pad2 = function(x) { return x < 10 ? '0' + x : '' + x; };
    return pad2(h) + ':' + pad2(m) + ':' + pad2(s);
  }

  /** 解析 HH:MM:SS 字符串为 HHMMSS 整数 */
  function parseHHMMSSToInt(str) {
    if (!str) return 0;
    var m = String(str).match(/^(\d{1,2}):(\d{1,2}):(\d{1,2})$/);
    if (!m) {
      var s = parseInt(str, 10);
      return isNaN(s) ? 0 : s;
    }
    var h = Math.max(0, Math.min(23, parseInt(m[1], 10)));
    var mi = Math.max(0, Math.min(59, parseInt(m[2], 10)));
    var se = Math.max(0, Math.min(59, parseInt(m[3], 10)));
    return h * 10000 + mi * 100 + se;
  }

  /**
   * formatReloadMode：把 reload_sec 整数值格式化为右侧时序列可读文本。
   *   2147483647 → "不重载"
   *   2147483646 → "文件载入时"
   *   0 / 负数   → "每次启动载入股票"
   *   正整数 N   → "每隔H:MM:SS重载"
   */
  function formatReloadMode(v) {
    var info = decodeReloadMode(v);
    switch (info.mode) {
      case 'never':        return '不重载';
      case 'on_file_load': return '文件载入时';
      case 'interval':     return '每隔' + formatIntervalSec(info.param) + '重载';
      case 'on_startup':
      default:             return '每次启动载入股票';
    }
  }

  function encodeTimingSeconds(v) {
    if (v === null || v === undefined) return 0;
    if (typeof v === 'number') return v;
    var s = String(v).trim();
    if (!s) return 0;
    var m = s.match(/^(\d{1,2}):(\d{1,2}):(\d{1,2})$/);
    if (m) {
      return parseInt(m[1], 10) * 3600 + parseInt(m[2], 10) * 60 + parseInt(m[3], 10);
    }
    return parseInt(s, 10) || 0;
  }

  function decodeTimingSeconds(v) {
    var n = parseInt(v) || 0;
    if (n <= 0) return '0';
    if (n >= 3600) {
      var h = Math.floor(n / 3600);
      var m = Math.floor((n % 3600) / 60);
      var s = n % 60;
      return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
    }
    return String(n);
  }

  // ─── 关闭字段编辑器 (被 4 类独立入口共用) ───
  function closeFieldEditor() {
    if ($fieldEditorOverlay) {
      $fieldEditorOverlay.classList.add('hidden');
      $fieldEditorOverlay.innerHTML = '';
    }
    _fieldEditor = null;
    _formulaSelectorEl = null;
    _formulaSelectorOwnsOverlay = false;
  }

  // ─── 公式选择器 ───
  // nset→category 映射：0=indicator 1=xg 2=exp；3/4 无公式库；5/null=全部
  var FORMULA_TYPE_LABELS = {
    'indicator': '技术指标',
    'xg': '条件选股',
    'exp': '交易系统'
  };
  var FORMULA_TYPE_TO_INDITYPE = {
    'indicator': 'indi',
    'xg': '',
    'exp': 'trade_sys'
  };

  function openFormulaSelector(nset, onSelect, options) {
    // nset=3/4 对应基本面/动态行情，无公式库，直接返回
    if (nset === 3 || nset === 4) return;

    // 懒加载 overlay（与 openConditionEditor 等保持一致），使 panel.js 可复用
    if (!$fieldEditorOverlay) {
      $fieldEditorOverlay = document.getElementById('csFieldEditorOverlay');
    }
    if (!$fieldEditorOverlay) return;

    options = options || {};
    var title = options.title || '\uD83D\uDCD0 公式库';

    // 关闭已有选择器
    closeFormulaSelector();

    // nset∈{0,1,2} 时按 nset 过滤（隐藏分类按钮）；nset=null/undefined/5 时显示全部
    var nsetFiltered = (nset === 0 || nset === 1 || nset === 2);

    var modal = document.createElement('div');
    modal.className = 'cs-formula-selector-modal modal';
    modal.style.position = 'absolute';
    modal.style.top = '50%';
    modal.style.left = '50%';
    modal.style.transform = 'translate(-50%, -50%)';
    modal.style.zIndex = '10001';
    modal.style.width = '520px';
    modal.style.maxHeight = '70vh';
    modal.style.display = 'flex';
    modal.style.flexDirection = 'column';

    var h3 = document.createElement('h3');
    h3.textContent = title;
    h3.style.marginBottom = '10px';
    modal.appendChild(h3);

    // 分类筛选按钮：仅当未按 nset 过滤时显示（nset=null/undefined/5）
    var filterBar = null;
    var _currentFilter = '';
    if (!nsetFiltered) {
      filterBar = document.createElement('div');
      filterBar.style.display = 'flex';
      filterBar.style.gap = '6px';
      filterBar.style.marginBottom = '10px';
      filterBar.style.flexWrap = 'wrap';

      var filters = [
        { key: '', label: '全部' },
        { key: 'indicator', label: '技术指标' },
        { key: 'xg', label: '条件选股' },
        { key: 'exp', label: '交易系统' }
      ];

      filters.forEach(function(f) {
        var btn = document.createElement('button');
        btn.className = 'btn btn-outline';
        btn.textContent = f.label;
        btn.style.fontSize = '11px';
        btn.style.padding = '2px 10px';
        if (f.key === _currentFilter) btn.classList.add('active');
        btn.addEventListener('click', function() {
          _currentFilter = f.key;
          filterBar.querySelectorAll('button').forEach(function(b) { b.classList.remove('active'); });
          btn.classList.add('active');
          loadAndRenderFormulaList(modal, _currentFilter);
        });
        filterBar.appendChild(btn);
      });
      modal.appendChild(filterBar);
    }

    // 公式列表容器
    var listContainer = document.createElement('div');
    listContainer.className = 'cs-formula-list';
    listContainer.style.overflowY = 'auto';
    listContainer.style.flex = '1';
    listContainer.style.minHeight = '200px';
    listContainer.textContent = '加载中...';
    listContainer.style.color = 'var(--text-muted)';
    listContainer.style.fontSize = '13px';
    listContainer.style.padding = '10px 0';
    modal.appendChild(listContainer);

    // 关闭按钮
    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var closeBtn = document.createElement('button');
    closeBtn.className = 'btn btn-outline';
    closeBtn.textContent = '关闭';
    closeBtn.addEventListener('click', closeFormulaSelector);
    actions.appendChild(closeBtn);
    modal.appendChild(actions);

    // 存储上下文供 loadAndRenderFormulaList / renderFormulaSelectorList 使用
    modal._onSelect = (typeof onSelect === 'function') ? onSelect : null;
    modal._nset = nset;

    // overlay 可见性管理：若 overlay 当前隐藏（panel.js 复用场景），则由公式选择器独占
    _formulaSelectorOwnsOverlay = $fieldEditorOverlay.classList.contains('hidden');
    if (_formulaSelectorOwnsOverlay) {
      $fieldEditorOverlay.classList.remove('hidden');
    }

    $fieldEditorOverlay.appendChild(modal);
    _formulaSelectorEl = modal;

    // 加载数据
    loadAndRenderFormulaList(modal, _currentFilter);
  }

  function loadAndRenderFormulaList(modal, filter) {
    var listContainer = modal.querySelector('.cs-formula-list');
    if (!listContainer) return;
    listContainer.textContent = '加载中...';
    listContainer.style.color = 'var(--text-muted)';

    // nset∈{0,1,2} 时由后端按 nset 过滤；否则获取全部
    var nset = modal._nset;
    var url = '/api/formula/list';
    if (nset === 0 || nset === 1 || nset === 2) {
      url += '?nset=' + nset;
    }

    fetch(url)
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(data) {
        var formulas = Array.isArray(data) ? data : (data.data || data.formulas || []);
        renderFormulaSelectorList(formulas, filter, listContainer, modal._onSelect);
      })
      .catch(function(err) {
        listContainer.textContent = '加载公式列表失败: ' + err.message;
        listContainer.style.color = 'var(--text-danger, #e74c3c)';
      });
  }

  function renderFormulaSelectorList(formulas, filter, container, onSelect) {
    container.innerHTML = '';

    var filtered = formulas;
    if (filter) {
      filtered = formulas.filter(function(f) {
        return (f.category || f.type) === filter;
      });
    }

    if (filtered.length === 0) {
      container.textContent = '暂无公式';
      container.style.color = 'var(--text-muted)';
      container.style.fontSize = '13px';
      container.style.padding = '10px 0';
      return;
    }

    filtered.forEach(function(f) {
      var category = f.category || f.type || '';
      var item = document.createElement('div');
      item.className = 'cs-formula-item';
      item.style.display = 'flex';
      item.style.alignItems = 'center';
      item.style.justifyContent = 'space-between';
      item.style.padding = '8px 10px';
      item.style.borderBottom = '1px solid var(--border-color, #333)';
      item.style.cursor = 'pointer';
      item.style.transition = 'background 0.15s';
      item.style.fontSize = '13px';

      item.addEventListener('mouseenter', function() {
        item.style.background = 'var(--hover-bg, rgba(255,255,255,0.06))';
      });
      item.addEventListener('mouseleave', function() {
        item.style.background = '';
      });

      var info = document.createElement('div');
      info.style.flex = '1';
      info.style.minWidth = '0';

      var nameEl = document.createElement('div');
      nameEl.textContent = f.name || f.id || '(未命名)';
      nameEl.style.fontWeight = '500';
      info.appendChild(nameEl);

      if (f.description || f.desc) {
        var descEl = document.createElement('div');
        descEl.textContent = f.description || f.desc || '';
        descEl.style.fontSize = '11px';
        descEl.style.color = 'var(--text-secondary)';
        descEl.style.marginTop = '2px';
        descEl.style.whiteSpace = 'nowrap';
        descEl.style.overflow = 'hidden';
        descEl.style.textOverflow = 'ellipsis';
        info.appendChild(descEl);
      }

      var typeLabel = FORMULA_TYPE_LABELS[category] || category || '';
      if (typeLabel) {
        var tag = document.createElement('span');
        tag.textContent = typeLabel;
        tag.style.fontSize = '10px';
        tag.style.padding = '1px 6px';
        tag.style.borderRadius = '3px';
        tag.style.background = 'var(--accent-bg, rgba(64,128,255,0.15))';
        tag.style.color = 'var(--accent-color, #4080ff)';
        tag.style.marginLeft = '8px';
        tag.style.flexShrink = '0';
        item.appendChild(info);
        item.appendChild(tag);
      } else {
        item.appendChild(info);
      }

      item.addEventListener('click', function() {
        if (typeof onSelect === 'function') {
          // 回调模式（panel.js 复用）：传递规范化公式对象后关闭
          var formula = {
            id: f.id || '',
            name: f.name || '',
            description: f.description || f.desc || '',
            category: category,
            script: f.script || f.content || f.formula || '',
            args: f.args || [],
            formula_type: f.formula_type || category,
            source: f.source || ''
          };
          onSelect(formula);
          closeFormulaSelector();
        } else {
          // 向后兼容：回填到综合设置弹窗的固定 DOM 元素
          var indiTa = document.getElementById('cond-editor-indi');
          if (indiTa) {
            indiTa.value = f.script || f.content || f.formula || '';
          }
          var inditypeSelect = document.getElementById('cond-editor-inditype');
          if (inditypeSelect) {
            var mappedType = FORMULA_TYPE_TO_INDITYPE[category] || '';
            inditypeSelect.value = mappedType;
          }
          closeFormulaSelector();
        }
      });

      container.appendChild(item);
    });
  }

  function closeFormulaSelector() {
    if (_formulaSelectorEl && _formulaSelectorEl.parentNode) {
      _formulaSelectorEl.parentNode.removeChild(_formulaSelectorEl);
    }
    _formulaSelectorEl = null;
    // 若公式选择器独占 overlay（panel.js 复用场景），关闭时隐藏 overlay
    if (_formulaSelectorOwnsOverlay && $fieldEditorOverlay) {
      $fieldEditorOverlay.classList.add('hidden');
    }
    _formulaSelectorOwnsOverlay = false;
  }

  function formatTransferMode(attrVal) {
    attrVal = parseInt(attrVal) || 0;
    if (attrVal === 0) return '默认';
    var names = [];
    if (attrVal & 0x1) names.push('删除源');
    if (attrVal & 0x1000) names.push('保留源');
    if (attrVal & 0x2000) names.push('清空目标');
    if (attrVal & 0x4000) names.push('输出成分股');
    return names.join(', ') || '默认';
  }

  function formatConditionOptions(raw) {
    raw = parseInt(raw) || 0;
    if (raw === 0) return '默认';
    var names = [];
    if (raw & 0x80000) names.push('输出成分股');
    if (raw & 0x100000) names.push('保留源池');
    if (raw & 0x200000) names.push('清空目标池');
    return names.join(', ') || '默认';
  }

  function parseColList(str) {
    if (!str || typeof str !== 'string') return [];
    return str.split(/[,;\s]+/).filter(function(s) { return s !== ''; });
  }

  // dzhColorToCss 已合并至 canvas.js（统一实现，消除重复代码）
  // 通过 window.dzhColorToCss 全局引用，本作用域内可直接调用

  // 综合设置内部使用的调色板名称表（精简版，与canvas.js DZH_PALETTE_NAMES 一致）
  var DZH_CS_NAMES = {
    0x00:{name:'黑色',en:'Black'},0x01:{name:'深红',en:'DarkRed'},0x02:{name:'深绿',en:'DarkGreen'},
    0x03:{name:'橄榄',en:'Olive'},0x04:{name:'深蓝',en:'Navy'},0x05:{name:'紫色',en:'Purple'},
    0x06:{name:'青色',en:'Teal'},0x07:{name:'灰色',en:'Gray'},0x08:{name:'浅绿灰',en:'PaleGreen'},
    0x09:{name:'浅蓝灰',en:'LightBlue'},0x0A:{name:'米白',en:'AntiqueWhite'},0x0B:{name:'银灰',en:'Silver'},
    0x0C:{name:'深灰',en:'DarkGray'},0x0D:{name:'红色',en:'Red'},0x0E:{name:'亮绿',en:'Lime'},
    0x0F:{name:'黄色',en:'Yellow'},0x10:{name:'蓝色',en:'Blue'},0x11:{name:'品红',en:'Magenta'},
    0x12:{name:'青(Cyan)',en:'Cyan'},0x13:{name:'白色',en:'White'}
  };

  function syncAttrParity(edge, newAttr) {
    if (!edge) return;
    var data = poolData.data;
    if (data && data.edges) {
      var condNodeId = edge.source ? edge.source.node_id : edge.from;
      var incoming = data.edges.filter(function(e) {
        var toId = e.target ? e.target.node_id : e.to;
        return toId === condNodeId;
      });
      if (incoming.length > 0) {
        if (!incoming[0].attr) incoming[0].attr = {};
        incoming[0].attr.attr = (parseInt(newAttr) || 0) + 1;
      }
    }
    if (!edge.attr) edge.attr = {};
    edge.attr.attr = newAttr;
  }

  function refresh() {
    if (_isOpen) {
      renderComprehensiveTable();
    }
  }

  // syncFromCanvas：保留为 no-op 别名以兼容 main.js 调用
  // （原三栏布局下用于从画布同步选中节点；表格视图下选中由行点击驱动）
  function syncFromCanvas(nodeId) {
    if (!_isOpen) return;
    selectNode(nodeId);
  }

  // ─── 公共 API ───
  var api = {
    open: open,
    close: close,
    handleClose: handleClose,
    toggle: toggle,
    selectNode: selectNode,
    syncFromCanvas: syncFromCanvas,
    isOpen: isOpen,
    isDirty: isDirty,
    markSaved: markSaved,
    markDirty: markDirty,
    refresh: refresh,
    init: init,
    utils: {
      buildTableRows: buildTableRows,
      formatHoldTime: formatHoldTime,
      formatTimingSeconds: formatTimingSeconds,
      parseTimingSeconds: parseTimingSeconds,
      parseHoldSeconds: parseHoldSeconds,
      decodeEndtime: decodeEndtime,
      encodeEndtime: encodeEndtime,
      formatEndtime: formatEndtime,
      formatReloadMode: formatReloadMode,
      decodeReloadMode: decodeReloadMode,
      encodeReloadMode: encodeReloadMode,
      formatIntervalSec: formatIntervalSec,
      formatHHMMSS: formatHHMMSS,
      parseHHMMSSToInt: parseHHMMSSToInt,
      encodeTimingSeconds: encodeTimingSeconds,
      decodeTimingSeconds: decodeTimingSeconds,
      closeFieldEditor: closeFieldEditor,
      ensureBits: ensureBits,
      findSourcePool: findSourcePool,
      buildStateAttributes: buildStateAttributes,
      buildSourceAttributes: buildSourceAttributes,
      parseTimingParams: parseTimingParams,
      formatTransferMode: formatTransferMode,
      formatConditionOptions: formatConditionOptions,
      parseColList: parseColList,
      syncAttrParity: syncAttrParity,
      // 4 类严格分立的字段编辑器入口(替换原 openFieldEditor 通用入口)
      openCandidatePoolEditor: openCandidatePoolEditor,
      openStateAttributeEditor: openStateAttributeEditor,
      openConditionEditor: openConditionEditor,
      openTimingEditor: openTimingEditor,
      // 公式选择器（供 panel.js 等复用：openFormulaSelector(nset, onSelect, options)）
      openFormulaSelector: openFormulaSelector,
      closeFormulaSelector: closeFormulaSelector,
      // 4 类严格分立的 apply 函数(替换原 applyFieldEditor)
      applyCandidatePoolEditor: applyCandidatePoolEditor,
      applyStateAttributeEditor: applyStateAttributeEditor,
      applyConditionEditor: applyConditionEditor,
      applyTimingEditor: applyTimingEditor,
      BEGIN_NAMES: BEGIN_NAMES,
      CYCLE_NAMES: CYCLE_NAMES,
      INDI_TYPE_NAMES: INDI_TYPE_NAMES,
      DELTYPE_NAMES: DELTYPE_NAMES
      // FIELD_META **不**对外暴露为可被任意对象调用的统一 API（按 spec 要求）
    }
  };

  global.ComprehensiveSettings = api;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})(window);


// ============================================================================
// ===== 来源: config-manager.js (ConfigManager) =====
// ============================================================================
/**
 * 配置中心管理器
 * ============
 * 通用股票池程序配置管理界面
 * - 8大分类，61张配置表
 * - 表格/JSON/表单三种编辑视图
 * - 校验与热加载
 */

(function (global) {
  'use strict';

  // ═══════════════════════════════════════════════════════════
  //  分类定义 — 动态拉取自 GET /api/config/categories
  // ═══════════════════════════════════════════════════════════

  var CATEGORIES = [];

  // ═══════════════════════════════════════════════════════════
  //  State
  // ═══════════════════════════════════════════════════════════

  var state = {
    activeCategory: 'nodes',
    activeTable: null,
    tableData: {},
    tableMeta: {},
    originalJson: '',
    modified: false,
    undoStack: [],
    searchQuery: '',
    searchScope: 'metadata',
    locks: {},
    schemaCoverage: {},
    categoryConsistency: {},
    history: [],
    tableSchemas: {}
  };

  var API_BASE = '/api/config';

  // ═══════════════════════════════════════════════════════════
  //  API Helpers
  // ═══════════════════════════════════════════════════════════

  function api(method, path, body) {
    var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    return fetch(API_BASE + path, opts)
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
  }

  // ═══════════════════════════════════════════════════════════
  //  Load Categories (dynamic fetch from backend)
  // ═══════════════════════════════════════════════════════════

  function loadCategories() {
    return api('GET', '/categories')
      .then(function (data) {
        CATEGORIES = data.categories || [];
        state.locks = data.locks || {};
        state.schemaCoverage = data.schema_coverage || {};
        state.categoryConsistency = data.category_consistency || {};
      })
      .catch(function (err) {
        console.error('Failed to load categories:', err);
        throw err;
      });
  }

  // ═══════════════════════════════════════════════════════════
  //  Schema & Lock Helpers (Task 6 / Task 14)
  // ═══════════════════════════════════════════════════════════

  function loadTableSchemas() {
    return api('GET', '/tables/table_schemas')
      .then(function (data) {
        state.tableSchemas = (data && data.schemas) || {};
      })
      .catch(function (err) {
        console.error('Failed to load table schemas:', err);
        state.tableSchemas = {};
      });
  }

  function getTableInfo(name) {
    var info = null;
    CATEGORIES.forEach(function (cat) {
      cat.tables.forEach(function (t) {
        if (t.name === name) info = t;
      });
    });
    return info;
  }

  function isTableLocked(name) {
    var info = getTableInfo(name);
    if (info && info.locked === true) return true;
    var lockInfo = state.locks[name];
    if (lockInfo && lockInfo.locked === true) return true;
    return false;
  }

  function isSchemaCovered(name) {
    var info = getTableInfo(name);
    return !!(info && info.schema_covered === true);
  }

  function getTableSchema(name) {
    return state.tableSchemas[name] || null;
  }

  function getFieldType(schema, fieldKey, fallbackValue) {
    if (schema && schema.fields && schema.fields[fieldKey]) {
      var t = schema.fields[fieldKey].type;
      if (t) return t;
    }
    if (typeof fallbackValue === 'number') return 'number';
    if (typeof fallbackValue === 'boolean') return 'boolean';
    return 'string';
  }

  // ═══════════════════════════════════════════════════════════
  //  Toast
  // ═══════════════════════════════════════════════════════════

  function showToast(msg, type) {
    type = type || 'info';
    var cls = type === 'error' ? 'err' : (type === 'success' ? 'ok' : 'info');
    var el = document.createElement('div');
    el.className = 'toast ' + cls;
    el.textContent = msg;
    var c = document.getElementById('toastC');
    if (!c) return;
    c.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 3000);
  }
  var toast = showToast; // backward-compatible alias

  // ═══════════════════════════════════════════════════════════
  //  Render: Sidebar Categories
  // ═══════════════════════════════════════════════════════════

  function renderCategories() {
    var html = '';
    CATEGORIES.forEach(function (cat) {
      var isActive = cat.id === state.activeCategory;
      html += '<div class="cat' + (isActive ? ' on' : '') +
        '" data-cat="' + cat.id + '" title="' + escHtml(cat.desc) + '">' +
        '<span class="cat-icon">' + cat.icon + '</span>' +
        '<span class="cat-name">' + escHtml(cat.name) + '</span>' +
        '<span class="cat-n">' + cat.tables.length + '</span>' +
        '</div>';
    });
    document.getElementById('catList').innerHTML = html;

    // Bind clicks
    document.querySelectorAll('.cat').forEach(function (el) {
      el.addEventListener('click', function () {
        state.activeCategory = this.dataset.cat;
        state.activeTable = null;
        renderCategories();
        renderTableList();
        showEmpty();
      });
    });
  }

  // ═══════════════════════════════════════════════════════════
  //  Render: Table List
  // ═══════════════════════════════════════════════════════════

  function renderTableList() {
    var cat = CATEGORIES.find(function (c) { return c.id === state.activeCategory; });
    if (!cat) return;

    var query = state.searchQuery.toLowerCase();
    var filtered;
    if (query) {
      // 全局搜索：跨所有分类查找
      filtered = [];
      CATEGORIES.forEach(function (c) {
        c.tables.forEach(function (t) {
          if (t.name.toLowerCase().indexOf(query) >= 0 ||
            t.label.toLowerCase().indexOf(query) >= 0 ||
            (t.desc && t.desc.toLowerCase().indexOf(query) >= 0)) {
            filtered.push(t);
          }
        });
      });
    } else {
      filtered = cat.tables.filter(function () { return true; });
    }

    var html = '';
    filtered.forEach(function (t) {
      var isActive = t.name === state.activeTable;
      var badgeCls = t.badge || '';
      var meta = state.tableMeta[t.name];
      var countStr = meta && meta.entry_count ? (meta.entry_count + '条') : '';
      var isLocked = t.locked === true || state.locks[t.name] === true;
      var schemaMissing = t.schema_covered === false;
      html += '<div class="cfg-table-item' + (isActive ? ' active' : '') +
        '" data-table="' + t.name + '" title="' + escHtml(t.desc || '') + '">' +
        '<span class="cfg-table-name">' + escHtml(t.label) + '</span>' +
        (isLocked ? '<span class="cfg-table-lock" title="已锁定" style="font-size:11px">🔒</span>' : '') +
        (schemaMissing ? '<span class="cfg-table-warn" title="Schema 未覆盖" style="font-size:11px;color:#e67e22">⚠</span>' : '') +
        (badgeCls ? '<span class="cfg-table-badge ' + badgeCls + '">' + badgeCls + '</span>' : '') +
        (countStr ? '<span style="font-size:11px;color:var(--cfg-text-muted)">' + countStr + '</span>' : '') +
        '</div>';
      // Description line
      if (t.desc && !query) {
        html += '<div style="padding:0 14px 6px 14px;font-size:11px;color:var(--cfg-text-muted);line-height:1.3;margin-top:-4px">' +
          escHtml(t.desc) + '</div>';
      }
    });
    document.getElementById('tList').innerHTML = html;

    // Bind clicks
    document.querySelectorAll('.cfg-table-item').forEach(function (el) {
      el.addEventListener('click', function () {
        var name = this.dataset.table;
        loadTable(name);
      });
    });
  }

  // ═══════════════════════════════════════════════════════════
  //  Load Table Data
  // ═══════════════════════════════════════════════════════════

  function loadTable(name) {
    state.activeTable = name;
    renderTableList();

    // Show loading
    document.getElementById('emptySt').style.display = 'none';
    document.getElementById('edContent').style.display = 'flex';

    api('GET', '/tables/' + name)
      .then(function (data) {
        state.tableData[name] = data;
        state.originalJson = JSON.stringify(data, null, 2);
        state.modified = false;
        state.undoStack = [];
        renderEditor();
        updateSaveBtn();
      })
      .catch(function (err) {
        toast('加载失败: ' + err.message, 'error');
      });

    // Load history in parallel (Task 10)
    loadHistory(name).then(function () {
      renderHistoryPanel();
    });
  }

  // ═══════════════════════════════════════════════════════════
  //  Render: Editor
  // ═══════════════════════════════════════════════════════════

  function renderEditor() {
    var name = state.activeTable;
    if (!name) return;
    var data = state.tableData[name];
    if (!data) return;

    // Find table info
    var tableInfo = getTableInfo(name);

    // Breadcrumb
    var cat = CATEGORIES.find(function (c) { return c.id === state.activeCategory; });
    document.getElementById('edTitle').innerHTML =
      '<span onclick="document.querySelector(\'.cfg-cat-item[data-cat=' + cat.id + ']\').click()">' + escHtml(cat.icon + ' ' + cat.name) + '</span>' +
      '<span class="sep">›</span>' +
      '<span style="color:var(--cfg-accent)">' + escHtml(tableInfo ? tableInfo.label : name) + '</span>';

    // Meta + Lock controls (Task 14)
    var meta = state.tableMeta[name];
    var metaHtml = '';
    if (data.version) metaHtml += '<span>v' + escHtml(data.version) + '</span>';
    if (meta) {
      if (meta.entry_count) metaHtml += '<span>' + meta.entry_count + ' 条</span>';
      if (meta.hash) metaHtml += '<span>#' + escHtml(meta.hash) + '</span>';
    }
    var locked = isTableLocked(name);
    if (locked) {
      metaHtml += '<span style="color:#e67e22">🔒 已锁定</span>';
    }
    metaHtml += ' <button class="cfg-lock-btn ' + (locked ? 'unlock' : 'lock') +
      '" onclick="ConfigManager.toggleLock(\'' + escHtml(name) + '\')">' +
      (locked ? '🔓 解锁' : '🔒 加锁') + '</button>';
    document.getElementById('edMeta').innerHTML = metaHtml;

    // Disable save button when locked (Task 14.1)
    var saveBtn = document.getElementById('configBtnSave');
    if (saveBtn) saveBtn.disabled = locked;

    // Render views
    renderTableView(data);
    renderJsonView(data);
    renderFormView(data);
    renderHistoryPanel();

    // Reset tabs to table
    switchTab('table');
  }

  // ═══════════════════════════════════════════════════════════
  //  Render: Table View (auto-detect collection & render grid)
  // ═══════════════════════════════════════════════════════════

  function renderTableView(data) {
    var collection = findCollection(data);
    if (!collection) {
      document.getElementById('tableView').innerHTML =
        '<div style="padding:20px;color:var(--cfg-text-muted)">此表无集合数据，请使用 JSON 视图查看</div>';
      return;
    }

    var name = state.activeTable;
    var editable = isSchemaCovered(name) && !isTableLocked(name);
    var schema = getTableSchema(name);
    var collectionKey = collection.key;

    var items = collection.items;
    if (Array.isArray(items)) {
      renderArrayTable(items, collectionKey, editable, schema);
    } else if (typeof items === 'object') {
      renderDictTable(items, collectionKey, editable, schema);
    }

    if (editable) {
      attachCellEditListeners();
    }
  }

  function findCollection(data) {
    // Known collection keys in priority order
    var keys = [
      'rules', 'types', 'modules', 'layouts', 'components', 'mappings',
      'strategies', 'modes', 'adapters', 'signals', 'events', 'actions',
      'modes', 'flows', 'chains', 'resolvers', 'providers', 'interfaces',
      'time_sources', 'trade_interfaces', 'panels', 'fields', 'attrs',
      'begin_resolvers', 'end_resolvers', 'hhmmss_modes'
    ];
    for (var i = 0; i < keys.length; i++) {
      if (data[keys[i]] !== undefined) {
        return { key: keys[i], items: data[keys[i]] };
      }
    }
    // Fallback: first object/array that isn't a primitive field
    var fallback = null;
    Object.keys(data).forEach(function (k) {
      if (!fallback && typeof data[k] === 'object' && k !== 'version' && k !== 'description') {
        fallback = { key: k, items: data[k] };
      }
    });
    return fallback;
  }

  function renderArrayTable(items, collectionKey, editable, schema) {
    if (!items.length) {
      document.getElementById('tableView').innerHTML = '<div style="padding:20px;color:var(--cfg-text-muted)">空表</div>';
      return;
    }

    // Collect all keys
    var keys = [];
    items.forEach(function (item) {
      Object.keys(item).forEach(function (k) {
        if (keys.indexOf(k) < 0) keys.push(k);
      });
    });
    // Limit to 15 columns for readability
    if (keys.length > 15) keys = keys.slice(0, 15);

    var html = '<table class="cfg-table-grid"><thead><tr>' +
      '<th>#</th>' +
      keys.map(function (k) { return '<th>' + escHtml(k) + '</th>'; }).join('') +
      '</tr></thead><tbody>';

    items.forEach(function (item, idx) {
      html += '<tr><td class="num-cell">' + (idx + 1) + '</td>';
      keys.forEach(function (k) {
        var v = item[k];
        var fieldType = getFieldType(schema, k, v);
        html += '<td class="' + cellClass(v) + '">' +
          renderCellContent(v, fieldType, editable, idx, k, collectionKey, 'array') +
          '</td>';
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    document.getElementById('tableView').innerHTML = html;
  }

  function renderDictTable(items, collectionKey, editable, schema) {
    var keys = Object.keys(items);
    if (!keys.length) {
      document.getElementById('tableView').innerHTML = '<div style="padding:20px;color:var(--cfg-text-muted)">空表</div>';
      return;
    }

    // Detect if all values are similar objects -> show as table
    var firstVal = items[keys[0]];
    if (typeof firstVal === 'object' && firstVal !== null && !Array.isArray(firstVal)) {
      // Object table
      var propKeys = [];
      keys.forEach(function (k) {
        if (typeof items[k] === 'object' && items[k] !== null) {
          Object.keys(items[k]).forEach(function (pk) {
            if (propKeys.indexOf(pk) < 0) propKeys.push(pk);
          });
        }
      });
      if (propKeys.length > 12) propKeys = propKeys.slice(0, 12);

      var html = '<table class="cfg-table-grid"><thead><tr>' +
        '<th>' + escHtml(collectionKey || 'key') + '</th>' +
        propKeys.map(function (k) { return '<th>' + escHtml(k) + '</th>'; }).join('') +
        '</tr></thead><tbody>';

      keys.forEach(function (k) {
        html += '<tr><td class="key-cell">' + escHtml(k) + '</td>';
        var v = items[k];
        if (typeof v === 'object' && v !== null) {
          propKeys.forEach(function (pk) {
            var fieldType = getFieldType(schema, pk, v[pk]);
            html += '<td class="' + cellClass(v[pk]) + '">' +
              renderCellContent(v[pk], fieldType, editable, k, pk, collectionKey, 'dict-obj') +
              '</td>';
          });
        } else {
          html += '<td colspan="' + propKeys.length + '" class="' + cellClass(v) + '">' + cellDisplay(v) + '</td>';
        }
        html += '</tr>';
      });
      html += '</tbody></table>';
      document.getElementById('tableView').innerHTML = html;
    } else {
      // Simple key-value
      var html = '<table class="cfg-table-grid"><thead><tr>' +
        '<th>键</th><th>值</th>' +
        '</tr></thead><tbody>';
      keys.forEach(function (k) {
        var v = items[k];
        var fieldType = getFieldType(schema, k, v);
        html += '<tr><td class="key-cell">' + escHtml(k) + '</td>' +
          '<td class="' + cellClass(v) + '">' +
          renderCellContent(v, fieldType, editable, k, '', collectionKey, 'dict-simple') +
          '</td></tr>';
      });
      html += '</tbody></table>';
      document.getElementById('tableView').innerHTML = html;
    }
  }

  function cellClass(v) {
    if (v === null || v === undefined) return '';
    if (typeof v === 'number') return 'num-cell';
    if (typeof v === 'boolean') return 'bool-cell';
    if (typeof v === 'object') return 'obj-cell';
    return '';
  }

  function cellDisplay(v) {
    if (v === null) return '<span style="color:var(--cfg-text-muted)">null</span>';
    if (v === undefined) return '';
    if (typeof v === 'boolean') return v ? '✓ true' : '✗ false';
    if (typeof v === 'object') {
      var s = JSON.stringify(v);
      if (s.length > 60) s = s.substring(0, 57) + '...';
      return escHtml(s);
    }
    return escHtml(String(v));
  }

  // ═══════════════════════════════════════════════════════════
  //  Inline Cell Editing (Task 6)
  // ═══════════════════════════════════════════════════════════

  function renderCellContent(value, fieldType, editable, rowId, fieldKey, collectionKey, mode) {
    // Complex types are always read-only
    if (typeof value === 'object' && value !== null) {
      return cellDisplay(value);
    }
    if (fieldType === 'object' || fieldType === 'array') {
      return cellDisplay(value);
    }
    if (!editable) {
      return cellDisplay(value);
    }
    return renderEditableInput(value, fieldType, rowId, fieldKey, collectionKey, mode);
  }

  function renderEditableInput(value, fieldType, rowId, fieldKey, collectionKey, mode) {
    var v = (value === null || value === undefined) ? '' : String(value);
    var dataAttrs = ' data-row="' + escHtml(String(rowId)) + '"' +
      ' data-field="' + escHtml(fieldKey) + '"' +
      ' data-collection="' + escHtml(collectionKey) + '"' +
      ' data-mode="' + escHtml(mode) + '"';

    if (fieldType === 'boolean') {
      var checked = value === true ? ' checked' : '';
      return '<input type="checkbox" class="cfg-cell-edit"' + dataAttrs + checked + '>';
    }
    if (fieldType === 'number' || fieldType === 'integer') {
      return '<input type="number" class="cfg-cell-edit"' + dataAttrs +
        ' value="' + escHtml(v) + '" data-type="number">';
    }
    // string / enum / other -> text input
    return '<input type="text" class="cfg-cell-edit"' + dataAttrs +
      ' value="' + escHtml(v) + '" data-type="text">';
  }

  function attachCellEditListeners() {
    var inputs = document.querySelectorAll('#tableView .cfg-cell-edit');
    inputs.forEach(function (input) {
      input.addEventListener('change', function () {
        var row = this.dataset.row;
        var field = this.dataset.field;
        var collectionKey = this.dataset.collection;
        var mode = this.dataset.mode;
        var dataType = this.dataset.type;
        var name = state.activeTable;
        var data = state.tableData[name];
        if (!data || !data[collectionKey]) return;

        var collection = data[collectionKey];
        var newValue;

        if (this.type === 'checkbox') {
          newValue = this.checked;
        } else if (dataType === 'number') {
          newValue = this.value === '' ? null : parseFloat(this.value);
          if (isNaN(newValue)) newValue = null;
        } else {
          newValue = this.value;
        }

        if (mode === 'dict-simple') {
          collection[row] = newValue;
        } else if (mode === 'dict-obj') {
          if (collection[row] && typeof collection[row] === 'object') {
            collection[row][field] = newValue;
          }
        } else {
          // array mode
          var idx = parseInt(row, 10);
          if (collection[idx] && typeof collection[idx] === 'object') {
            collection[idx][field] = newValue;
          }
        }

        state.modified = true;
        updateSaveBtn();

        // Sync JSON view
        var ta = document.getElementById('jsonEd');
        if (ta) ta.value = JSON.stringify(data, null, 2);
      });
    });
  }

  // ═══════════════════════════════════════════════════════════
  //  Render: JSON View
  // ═══════════════════════════════════════════════════════════

  function renderJsonView(data) {
    var ta = document.getElementById('jsonEd');
    ta.value = JSON.stringify(data, null, 2);
  }

  // ═══════════════════════════════════════════════════════════
  //  Render: Form View
  // ═══════════════════════════════════════════════════════════

  function renderFormView(data) {
    var html = '';

    // Version & description
    if (data.version || data.description) {
      html += '<div class="cfg-form-group">';
      html += '<div class="cfg-form-group-title">基本信息</div>';
      if (data.version) {
        html += formRow('版本', data.version, 'version');
      }
      if (data.description) {
        html += formRow('描述', data.description, 'description');
      }
      html += '</div>';
    }

    // Collection
    var collection = findCollection(data);
    if (collection) {
      var items = collection.items;
      var count = Array.isArray(items) ? items.length : Object.keys(items).length;

      html += '<div class="cfg-form-group">';
      html += '<div class="cfg-form-group-title">' + escHtml(collection.key) +
        ' <span style="font-weight:normal;color:var(--cfg-text-muted)">(' + count + '条)</span></div>';

      if (Array.isArray(items)) {
        items.slice(0, 50).forEach(function (item, idx) {
          var label = item.rule_id || item.name || item.id || item.mapping_id || item.col_id || ('#' + (idx + 1));
          html += formRow(label, summarizeObj(item), null);
        });
        if (items.length > 50) {
          html += '<div style="padding:8px;color:var(--cfg-text-muted);font-size:11px">...共' + items.length + '条，请使用JSON视图查看完整数据</div>';
        }
      } else {
        var keys = Object.keys(items);
        keys.slice(0, 50).forEach(function (k) {
          html += formRow(k, summarizeObj(items[k]), null);
        });
        if (keys.length > 50) {
          html += '<div style="padding:8px;color:var(--cfg-text-muted);font-size:11px">...共' + keys.length + '条</div>';
        }
      }
      html += '</div>';
    }

    // Other top-level fields
    Object.keys(data).forEach(function (k) {
      if (k === 'version' || k === 'description') return;
      if (collection && k === collection.key) return;
      var v = data[k];
      if (typeof v !== 'object') {
        if (!html) html += '<div class="cfg-form-group"><div class="cfg-form-group-title">其他字段</div>';
        html += formRow(k, v, k);
      }
    });

    if (!html) html = '<div style="padding:20px;color:var(--cfg-text-muted)">无结构化数据</div>';
    document.getElementById('formView').innerHTML = html;
  }

  function formRow(label, value, key) {
    return '<div class="cfg-form-row">' +
      '<div class="cfg-form-label">' + escHtml(String(label)) + '</div>' +
      '<div class="cfg-form-value">' + escHtml(typeof value === 'object' ? JSON.stringify(value) : String(value)) + '</div>' +
      '</div>';
  }

  function summarizeObj(obj) {
    if (typeof obj !== 'object' || obj === null) return obj;
    var parts = [];
    var count = 0;
    Object.keys(obj).forEach(function (k) {
      if (count < 4) {
        var v = obj[k];
        if (typeof v !== 'object') {
          parts.push(k + ': ' + v);
          count++;
        }
      }
    });
    var s = parts.join(', ');
    if (Object.keys(obj).length > 4) s += ' ...';
    return s;
  }

  // ═══════════════════════════════════════════════════════════
  //  Validation
  // ═══════════════════════════════════════════════════════════

  function validateTable(name) {
    document.getElementById('valView').innerHTML = '<div style="padding:20px;color:var(--cfg-text-muted)">校验中...</div>';
    api('POST', '/validate/' + name)
      .then(function (result) {
        var html = '';
        if (result.valid) {
          html += '<div class="cfg-val-item ok">✓ 配置表 ' + escHtml(name) + ' 校验通过</div>';
        } else {
          html += '<div class="cfg-val-item error">✗ 配置表 ' + escHtml(name) + ' 校验失败</div>';
          if (result.errors && result.errors.length) {
            result.errors.forEach(function (err) {
              html += '<div class="cfg-val-item error" style="margin-left:16px">• ' + escHtml(typeof err === 'string' ? err : JSON.stringify(err)) + '</div>';
            });
          }
        }
        document.getElementById('valView').innerHTML = html;
      })
      .catch(function (err) {
        document.getElementById('valView').innerHTML =
          '<div class="cfg-val-item error">校验请求失败: ' + escHtml(err.message) + '</div>';
      });
  }

  function validateAll() {
    toast('正在校验所有配置...', 'info');
    api('POST', '/validate-all')
      .then(function (result) {
        var summary = result.summary || {};
        var tables = result.tables || {};
        var total = summary.total || 0;
        var passed = summary.passed || 0;
        var failed = summary.failed || 0;
        var html = '<div class="cfg-val-summary">共 ' + total + ' 张表，通过 ' + passed + '，失败 ' + failed + '</div>';
        Object.keys(tables).forEach(function (k) {
          var v = tables[k];
          var cls = v.valid ? 'ok' : 'err';
          var icon = v.valid ? '✓' : '✗';
          html += '<div class="cfg-val-item ' + cls + '">' + icon + ' ' + escHtml(k) + ' (schema: ' + (v.schema || 'none') + ')';
          if (v.errors && v.errors.length) {
            html += '<ul>';
            v.errors.forEach(function (e) { html += '<li>' + escHtml(e) + '</li>'; });
            html += '</ul>';
          }
          html += '</div>';
        });
        var vv = document.getElementById('valView');
        if (vv) vv.innerHTML = html;
        if (failed === 0) {
          toast('全部校验通过 (' + passed + '张表)', 'success');
        } else {
          toast(failed + '张表校验失败', 'error');
        }
      })
      .catch(function (err) {
        toast('校验失败: ' + err.message, 'error');
      });
  }

  // ═══════════════════════════════════════════════════════════
  //  Export & Import
  // ═══════════════════════════════════════════════════════════

  function exportAll() {
    toast('正在导出...', 'info');
    api('GET', '/export').then(function (data) {
      var blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'meta_core_config_' + new Date().toISOString().slice(0, 10) + '.json';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast('导出成功', 'success');
    }).catch(function (err) {
      toast('导出失败: ' + (err.message || err), 'error');
    });
  }

  function importConfig() {
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = function (e) {
      var file = e.target.files[0];
      if (!file) return;
      if (!confirm('确认导入配置文件？此操作将覆盖所有配置表。')) return;
      var reader = new FileReader();
      reader.onload = function (ev) {
        try {
          var payload = JSON.parse(ev.target.result);
          api('POST', '/import', payload).then(function (result) {
            toast('导入成功: ' + (result.tables_written || 0) + ' 张表', 'success');
            loadCategories().then(function () {
              renderCategories();
              renderTableList();
              if (state.activeTable) loadTable(state.activeTable);
            });
          }).catch(function (err) {
            toast('导入失败: ' + (err.message || err), 'error');
          });
        } catch (ex) {
          toast('文件解析失败: ' + ex.message, 'error');
        }
      };
      reader.readAsText(file);
    };
    input.click();
  }

  // ═══════════════════════════════════════════════════════════
  //  Save & Hot Reload
  // ═══════════════════════════════════════════════════════════

  function saveTable() {
    var name = state.activeTable;
    if (!name) return;

    var ta = document.getElementById('jsonEd');
    var content;
    try {
      content = JSON.parse(ta.value);
    } catch (e) {
      toast('JSON格式错误: ' + e.message, 'error');
      return;
    }

    toast('正在保存...', 'info');
    api('PUT', '/tables/' + name, { content: content })
      .then(function (result) {
        state.modified = false;
        state.tableData[name] = content;
        state.originalJson = ta.value;
        updateSaveBtn();
        toast('保存成功' + (result.changed && result.changed.length ? '，已热加载: ' + result.changed.join(', ') : ''), 'success');
        loadTableMeta();
        renderEditor();
      })
      .catch(function (err) {
        toast('保存失败: ' + err.message, 'error');
      });
  }

  function hotReload() {
    toast('正在热加载...', 'info');
    api('POST', '/reload')
      .then(function (result) {
        var changed = result.changed || [];
        if (changed.length) {
          toast('热加载完成: ' + changed.join(', '), 'success');
        } else {
          toast('配置无变更', 'info');
        }
        loadTableMeta();
      })
      .catch(function (err) {
        toast('热加载失败: ' + err.message, 'error');
      });
  }

  // ═══════════════════════════════════════════════════════════
  //  History & Rollback (Task 10 / Task 12)
  // ═══════════════════════════════════════════════════════════

  function loadHistory(tableName) {
    return api('GET', '/history/' + tableName)
      .then(function (data) {
        state.history = data.history || data.versions || [];
      })
      .catch(function (err) {
        console.error('Failed to load history:', err);
        state.history = [];
      });
  }

  function renderHistoryPanel() {
    var container = document.getElementById('tableView');
    if (!container) return;

    // Remove existing history panel
    var existing = container.querySelector('.cfg-history-panel');
    if (existing) existing.remove();

    var history = state.history || [];
    var html = '<div class="cfg-history-panel">';
    html += '<div class="cfg-history-header" id="cfgHistoryHeader">';
    html += '📜 历史版本 (' + history.length + ')';
    html += '<span style="margin-left:auto;font-size:10px;color:#5a5a7e">点击展开/收起</span>';
    html += '</div>';
    html += '<div class="cfg-history-body collapsed" id="cfgHistoryBody">';

    if (!history.length) {
      html += '<div style="padding:12px;color:#5a5a7e;font-size:11px">暂无历史记录</div>';
    } else {
      history.forEach(function (item) {
        var opMap = { create: '创建', update: '更新', delete: '删除', rollback: '回滚' };
        var opLabel = opMap[item.change_type] || item.change_type || '未知';
        var summary = item.created_by || 'system';
        if (item.new_content) {
          try {
            var content = JSON.parse(item.new_content);
            if (content.version) summary += ' · v' + content.version;
          } catch (e) { /* not JSON */ }
        }
        var versionLabel = opLabel + ' @ ' + (item.created_at || '');
        html += '<div class="cfg-history-item">';
        html += '<span class="cfg-history-time">' + escHtml(item.created_at || '') + '</span>';
        html += '<span class="cfg-history-op ' + escHtml(item.change_type || '') + '">' + escHtml(opLabel) + '</span>';
        html += '<span class="cfg-history-summary">' + escHtml(summary) + '</span>';
        html += '<span class="cfg-history-actions">';
        html += '<button class="cfg-lock-btn" onclick="ConfigManager.showDiff(\'\', \'' +
          escHtml(item.version_id || '') + '\', \'current\')">查看差异</button>';
        html += '<button class="cfg-lock-btn" onclick="ConfigManager.confirmRollback(\'' +
          escHtml(item.version_id || '') + '\', \'' + escHtml(versionLabel) + '\')">回滚到此版本</button>';
        html += '</span>';
        html += '</div>';
      });
    }

    html += '</div></div>';

    var panel = document.createElement('div');
    panel.innerHTML = html;
    container.appendChild(panel.firstChild);

    // Bind toggle
    var header = document.getElementById('cfgHistoryHeader');
    if (header) {
      header.addEventListener('click', function () {
        var body = document.getElementById('cfgHistoryBody');
        if (body) body.classList.toggle('collapsed');
      });
    }
  }

  function confirmRollback(versionId, versionLabel) {
    if (!confirm('确认回滚到版本 ' + versionLabel + '？此操作将覆盖当前配置。')) {
      return;
    }
    api('POST', '/rollback/' + versionId)
      .then(function () {
        toast('回滚成功', 'success');
        // Reload table data and history
        loadTable(state.activeTable);
        loadHistory(state.activeTable).then(function () {
          renderHistoryPanel();
        });
      })
      .catch(function (err) {
        toast('回滚失败: ' + (err.message || err), 'error');
      });
  }

  // ═══════════════════════════════════════════════════════════
  //  Diff Visualization
  // ═══════════════════════════════════════════════════════════

  function showDiff(tableName, fromVersion, toVersion) {
    if (!tableName) tableName = state.activeTable;
    if (!tableName) {
      toast('请先选择配置表', 'error');
      return;
    }
    var params = '?from_version=' + encodeURIComponent(fromVersion || 'current') +
                 '&to_version=' + encodeURIComponent(toVersion || 'current');
    api('GET', '/diff/' + tableName + params)
      .then(function (data) {
        var diff = data.diff || {};
        var html = '<div class="cfg-diff-view">';
        var hasChanges = false;
        if (diff.added && diff.added.length) {
          hasChanges = true;
          html += '<div class="cfg-diff-section"><strong style="color:#27ae60;">新增 (+' + diff.added.length + ')</strong><ul>';
          diff.added.forEach(function (item) { html += '<li style="color:#27ae60;">+ ' + escHtml(JSON.stringify(item)) + '</li>'; });
          html += '</ul></div>';
        }
        if (diff.removed && diff.removed.length) {
          hasChanges = true;
          html += '<div class="cfg-diff-section"><strong style="color:#e74c3c;">删除 (-' + diff.removed.length + ')</strong><ul>';
          diff.removed.forEach(function (item) { html += '<li style="color:#e74c3c;">- ' + escHtml(JSON.stringify(item)) + '</li>'; });
          html += '</ul></div>';
        }
        if (diff.modified) {
          hasChanges = true;
          html += '<div class="cfg-diff-section"><strong style="color:#e67e22;">修改</strong><ul>';
          Object.keys(diff.modified).forEach(function (field) {
            var change = diff.modified[field];
            html += '<li style="color:#e67e22;">~ ' + escHtml(field) + ': <span style="color:#e74c3c;">' + escHtml(JSON.stringify(change.old)) + '</span> → <span style="color:#27ae60;">' + escHtml(JSON.stringify(change.new)) + '</span></li>';
          });
          html += '</ul></div>';
        }
        if (!hasChanges) html += '<p style="color:var(--text3);">无差异</p>';
        html += '</div>';
        // Show in history panel
        var histPanel = document.querySelector('.cfg-hist-diff');
        if (histPanel) {
          histPanel.innerHTML = html;
          histPanel.style.display = 'block';
        } else {
          toast('Diff: ' + (diff.added ? diff.added.length : 0) + ' added, ' + (diff.removed ? diff.removed.length : 0) + ' removed', 'info');
        }
      })
      .catch(function (err) {
        toast('Diff 加载失败: ' + (err.message || err), 'error');
      });
  }

  // ═══════════════════════════════════════════════════════════
  //  Lock Management (Task 14)
  // ═══════════════════════════════════════════════════════════

  function toggleLock(tableName) {
    var locked = isTableLocked(tableName);
    if (locked) {
      if (!confirm('确认解锁表 ' + tableName + '？')) return;
      api('DELETE', '/lock/' + tableName)
        .then(function () {
          if (state.locks[tableName]) delete state.locks[tableName];
          var info = getTableInfo(tableName);
          if (info) info.locked = false;
          renderEditor();
          renderTableList();
          toast('已解锁', 'success');
        })
        .catch(function (err) {
          toast('解锁失败: ' + (err.message || err), 'error');
        });
    } else {
      var reason = prompt('加锁原因（可选）:', '');
      api('POST', '/lock/' + tableName + (reason ? '?reason=' + encodeURIComponent(reason) : ''))
        .then(function () {
          state.locks[tableName] = { locked: true, locked_at: new Date().toISOString(), reason: reason || '' };
          var info = getTableInfo(tableName);
          if (info) info.locked = true;
          renderEditor();
          renderTableList();
          toast('已加锁', 'success');
        })
        .catch(function (err) {
          toast('加锁失败: ' + (err.message || err), 'error');
        });
    }
  }

  // ═══════════════════════════════════════════════════════════
  //  Tab Switching
  // ═══════════════════════════════════════════════════════════

  function switchTab(tabId) {
    document.querySelectorAll('.cfg-tab').forEach(function (t) {
      t.classList.toggle('active', t.dataset.tab === tabId);
    });
    document.querySelectorAll('.cfg-tab-content').forEach(function (c) {
      c.classList.toggle('active', c.id === 'tab-' + tabId);
    });

    // Validate on switch to validate tab
    if (tabId === 'validate' && state.activeTable) {
      validateTable(state.activeTable);
    }
  }

  // ═══════════════════════════════════════════════════════════
  //  Helpers
  // ═══════════════════════════════════════════════════════════

  function escHtml(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function showEmpty() {
    document.getElementById('emptySt').style.display = 'flex';
    document.getElementById('edContent').style.display = 'none';
  }

  function updateSaveBtn() {
    var btn = document.getElementById('configBtnSave');
    btn.style.display = state.modified ? '' : 'none';
    document.getElementById('configBtnUndo').disabled = state.undoStack.length === 0;
  }

  function loadTableMeta() {
    api('GET', '/tables')
      .then(function (result) {
        state.tableMeta = result.tables || {};
        var total = result.total || 0;
        var tCnt = document.getElementById('tCnt');
        if (tCnt) tCnt.textContent = total + ' 张表';
        var dot = document.getElementById('dot');
        if (dot) dot.classList.remove('off');
        var stTxt = document.getElementById('stTxt');
        if (stTxt) stTxt.textContent = '在线';
        renderTableList();
      })
      .catch(function () {
        var dot = document.getElementById('dot');
        if (dot) dot.classList.add('off');
        var stTxt = document.getElementById('stTxt');
        if (stTxt) stTxt.textContent = '离线';
      });
  }

  // ═══════════════════════════════════════════════════════════
  //  Search (metadata + content scope)
  // ═══════════════════════════════════════════════════════════

  function handleSearch() {
    var q = state.searchQuery || '';
    if (!q || state.searchScope !== 'content') {
      renderTableList();
      return;
    }
    // Content search via API
    api('GET', '/search?q=' + encodeURIComponent(q) + '&scope=content&limit=50')
      .then(function (data) {
        var results = data.results || [];
        var html = '<div class="cfg-search-results"><strong>内容搜索结果 (' + (data.total || 0) + ')</strong>';
        if (results.length === 0) {
          html += '<p style="color:var(--text3);padding:8px;">无匹配结果</p>';
        } else {
          var byTable = {};
          results.forEach(function (r) {
            if (!byTable[r.table]) byTable[r.table] = [];
            byTable[r.table].push(r);
          });
          Object.keys(byTable).forEach(function (table) {
            html += '<div class="cfg-search-group"><div class="cfg-search-table">' + escHtml(table) + ' (' + byTable[table].length + ')</div>';
            byTable[table].forEach(function (r) {
              html += '<div class="cfg-search-item">' +
                '<span class="cfg-search-row">' + escHtml(r.row_key) + '</span>' +
                '<span class="cfg-search-field">' + escHtml(r.field) + '</span>' +
                '<span class="cfg-search-snippet">' + escHtml(r.snippet) + '</span>' +
                '</div>';
            });
            html += '</div>';
          });
        }
        html += '</div>';
        var tList = document.getElementById('tList');
        if (tList) tList.innerHTML = html;
      })
      .catch(function (err) {
        toast('搜索失败: ' + err.message, 'error');
      });
  }

  // ═══════════════════════════════════════════════════════════
  //  Dynamic CSS Injection (for new Task 6/10/12/14 elements)
  // ═══════════════════════════════════════════════════════════

  function injectStyles() {
    if (document.getElementById('cfg-manager-dynamic-styles')) return;
    var style = document.createElement('style');
    style.id = 'cfg-manager-dynamic-styles';
    style.textContent = [
      '.cfg-cell-edit { width: 100%; background: #151533; border: 1px solid #3a3a6e; color: #d8d8ec; font-size: 11px; padding: 2px 4px; border-radius: 2px; outline: none; box-sizing: border-box; }',
      '.cfg-cell-edit:focus { border-color: #4a90d9; }',
      '.cfg-cell-edit[type="checkbox"] { width: auto; cursor: pointer; }',
      '.cfg-history-panel { margin-top: 12px; border: 1px solid #2a2a5e; border-radius: 4px; overflow: hidden; }',
      '.cfg-history-header { padding: 8px 12px; background: rgba(255,255,255,.03); cursor: pointer; font-size: 12px; color: #9090b0; display: flex; align-items: center; gap: 6px; user-select: none; }',
      '.cfg-history-header:hover { background: rgba(255,255,255,.06); }',
      '.cfg-history-body { max-height: 300px; overflow-y: auto; }',
      '.cfg-history-body.collapsed { display: none; }',
      '.cfg-history-item { padding: 8px 12px; border-bottom: 1px solid rgba(255,255,255,.04); display: flex; align-items: center; gap: 8px; font-size: 11px; }',
      '.cfg-history-item:hover { background: rgba(255,255,255,.02); }',
      '.cfg-history-time { color: #9090b0; min-width: 120px; }',
      '.cfg-history-op { padding: 1px 6px; border-radius: 3px; font-size: 10px; }',
      '.cfg-history-op.create { background: rgba(39,174,96,.15); color: #27ae60; }',
      '.cfg-history-op.update { background: rgba(74,144,217,.15); color: #4a90d9; }',
      '.cfg-history-op.delete { background: rgba(231,76,60,.15); color: #e74c3c; }',
      '.cfg-history-op.rollback { background: rgba(155,89,182,.15); color: #9b59b6; }',
      '.cfg-history-summary { flex: 1; color: #d8d8ec; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
      '.cfg-history-actions { display: flex; gap: 4px; }',
      '.cfg-lock-btn { padding: 2px 8px; border: 1px solid #3a3a6e; border-radius: 3px; background: rgba(255,255,255,.03); color: #9090b0; font-size: 10px; cursor: pointer; white-space: nowrap; }',
      '.cfg-lock-btn:hover { background: rgba(255,255,255,.07); color: #d8d8ec; }',
      '.cfg-lock-btn.lock { color: #e67e22; border-color: rgba(230,126,34,.4); }',
      '.cfg-lock-btn.unlock { color: #27ae60; border-color: rgba(39,174,96,.4); }'
    ].join('\n');
    document.head.appendChild(style);
  }

  // ═══════════════════════════════════════════════════════════
  //  Init
  // ═══════════════════════════════════════════════════════════

  var listenersAttached = false;

  function init() {
    // 仅在配置中心页面（含 jsonEd）初始化，避免 index.html 报错
    var jsonTa = document.getElementById('jsonEd');
    if (!jsonTa) return;

    // Inject dynamic styles for new UI elements
    injectStyles();

    // Set up event listeners only once (idempotent for retry)
    if (!listenersAttached) {
      listenersAttached = true;

      // Search
      var _sch = document.getElementById('schInput');
      if (_sch) _sch.addEventListener('input', function () {
        state.searchQuery = this.value;
        handleSearch();
      });

      // Tab clicks
      document.querySelectorAll('.cfg-tab').forEach(function (tab) {
        tab.addEventListener('click', function () {
          switchTab(this.dataset.tab);
        });
      });

      // JSON editor change detection
      jsonTa.addEventListener('input', function () {
        var current = jsonTa.value;
        state.modified = (current !== state.originalJson);
        updateSaveBtn();
      });
      // Tab key in textarea
      jsonTa.addEventListener('keydown', function (e) {
        if (e.key === 'Tab') {
          e.preventDefault();
          var start = this.selectionStart;
          var end = this.selectionEnd;
          this.value = this.value.substring(0, start) + '  ' + this.value.substring(end);
          this.selectionStart = this.selectionEnd = start + 2;
        }
      });

      // Buttons
      document.getElementById('configBtnSave').addEventListener('click', saveTable);
      document.getElementById('btnReload').addEventListener('click', hotReload);
      document.getElementById('btnVal').addEventListener('click', validateAll);
      document.getElementById('btnCopy').addEventListener('click', function () {
        var ta = document.getElementById('jsonEd');
        navigator.clipboard.writeText(ta.value).then(function () {
          toast('已复制到剪贴板', 'success');
        });
      });
      document.getElementById('configBtnUndo').addEventListener('click', function () {
        if (state.undoStack.length) {
          document.getElementById('jsonEd').value = state.undoStack.pop();
          state.modified = true;
          updateSaveBtn();
        }
      });

      // Ctrl+S
      document.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
          e.preventDefault();
          if (state.modified) saveTable();
        }
      });
    }

    // Add export/import buttons to header if not present
    if (!document.getElementById('btnExport')) {
      var hdr = document.querySelector('.hdr');
      var btnVal = document.getElementById('btnVal');
      if (hdr && btnVal) {
        var btnExport = document.createElement('button');
        btnExport.className = 'btn';
        btnExport.id = 'btnExport';
        btnExport.innerHTML = '⬇ 导出';
        btnExport.addEventListener('click', exportAll);
        hdr.insertBefore(btnExport, btnVal);

        var btnImport = document.createElement('button');
        btnImport.className = 'btn';
        btnImport.id = 'btnImport';
        btnImport.innerHTML = '⬆ 导入';
        btnImport.addEventListener('click', importConfig);
        hdr.insertBefore(btnImport, btnVal);
      }
    }

    // Add search scope toggle
    if (!document.getElementById('schScope')) {
      var schWrap = document.querySelector('.sch-wrap');
      if (schWrap) {
        var scopeLabel = document.createElement('label');
        scopeLabel.style.cssText = 'font-size:10px;color:var(--text3);display:flex;align-items:center;gap:3px;margin-top:4px;cursor:pointer;';
        scopeLabel.innerHTML = '<input type="checkbox" id="schScope" style="width:auto;"> 内容搜索';
        schWrap.appendChild(scopeLabel);
        document.getElementById('schScope').addEventListener('change', function () {
          state.searchScope = this.checked ? 'content' : 'metadata';
          handleSearch();
        });
      }
    }

    // Load categories dynamically, then render
    loadCategories()
      .then(function () {
        renderCategories();
        renderTableList();
        loadTableMeta();
        loadTableSchemas();
        showEmpty();
      })
      .catch(function (err) {
        // Show error with retry button in the sidebar
        var container = document.getElementById('catList');
        if (container) {
          container.innerHTML = '<div class="cfg-error" style="padding:20px;color:#9090b0;font-size:12px;line-height:1.6">' +
            '<p style="margin-bottom:12px">分类数据加载失败: ' + escHtml(err.message || String(err)) + '</p>' +
            '<button class="btn" onclick="ConfigManager.init()" style="padding:4px 14px;border:1px solid #3a3a6e;border-radius:4px;background:rgba(74,144,217,.1);color:#4a90d9;cursor:pointer;font-size:11px">重试</button>' +
            '</div>';
        }
      });
  }

  // ═══════════════════════════════════════════════════════════
  //  ConfigSync — 配置同步客户端（ConfigManager 子模块）
  // ═══════════════════════════════════════════════════════════
  // 通过 WebSocket 监听后端配置变更事件，拉取更新并触发重新渲染。

  var DEFAULT_WS_URL = 'ws://' + window.location.host + '/api/config/ws';
  var RECONNECT_INTERVAL = 5000;
  var PING_INTERVAL = 30000;

  class ConfigSync {
    constructor(options) {
      this.options = options || {};
      this._ws = null;
      this._connected = false;
      this._reconnectTimer = null;
      this._pingTimer = null;
      this._wsUrl = this.options.wsUrl || DEFAULT_WS_URL;
      this._onConfigChanged = this.options.onConfigChanged || null;
      this._onConnected = this.options.onConnected || null;
      this._onDisconnected = this.options.onDisconnected || null;
      this._onError = this.options.onError || null;
      this._tableCache = {};
    }

    // ─── 连接管理 ──────────────────────────────────────────────

    connect() {
      if (this._ws && (this._ws.readyState === WebSocket.CONNECTING || this._ws.readyState === WebSocket.OPEN)) {
        return;
      }

      var self = this;
      try {
        this._ws = new WebSocket(this._wsUrl);
      } catch (e) {
        this._scheduleReconnect();
        return;
      }

      this._ws.onopen = function () {
        self._connected = true;
        self._startPing();
        if (self._onConnected) self._onConnected();
      };

      this._ws.onclose = function () {
        self._connected = false;
        self._stopPing();
        if (self._onDisconnected) self._onDisconnected();
        self._scheduleReconnect();
      };

      this._ws.onerror = function (e) {
        if (self._onError) self._onError(e);
        // 降级为 warn：WebSocket 断连由 onclose + _scheduleReconnect 自动重连，
        // 不应作为 error 污染控制台（连接恢复后即恢复正常）。
        console.warn('[ConfigSync] WebSocket 连接异常，将自动重连');
      };

      this._ws.onmessage = function (event) {
        self._handleMessage(event.data);
      };
    }

    disconnect() {
      this._connected = false;
      this._stopPing();
      clearTimeout(this._reconnectTimer);
      if (this._ws) {
        this._ws.onclose = null;
        this._ws.close();
        this._ws = null;
      }
    }

    _scheduleReconnect() {
      var self = this;
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = setTimeout(function () {
        self.connect();
      }, RECONNECT_INTERVAL);
    }

    // ─── 心跳 ──────────────────────────────────────────────────

    _startPing() {
      var self = this;
      this._stopPing();
      this._pingTimer = setInterval(function () {
        if (self._ws && self._ws.readyState === WebSocket.OPEN) {
          self._ws.send('ping');
        }
      }, PING_INTERVAL);
    }

    _stopPing() {
      clearInterval(this._pingTimer);
    }

    // ─── 消息处理 ──────────────────────────────────────────────

    _handleMessage(data) {
      if (data === 'pong') return;

      try {
        var msg = JSON.parse(data);
      } catch (e) {
        console.warn('[ConfigSync] 无法解析消息:', data);
        return;
      }

      switch (msg.type) {
        case 'config_changed':
          this._onConfigChangedEvent(msg);
          break;
        case 'status':
          break;
        default:
          break;
      }
    }

    _onConfigChangedEvent(msg) {
      var changedTables = msg.changed_tables || [];
      var checksums = msg.checksums || {};

      var reallyChanged = [];
      for (var i = 0; i < changedTables.length; i++) {
        var table = changedTables[i];
        var newChecksum = checksums[table];
        if (this._tableCache[table] !== newChecksum) {
          reallyChanged.push(table);
          this._tableCache[table] = newChecksum;
        }
      }

      if (reallyChanged.length === 0) return;

      this._fetchChangedTables(reallyChanged).then(function (updatedConfigs) {
        if (this._onConfigChanged) {
          this._onConfigChanged(reallyChanged, updatedConfigs);
        }
        var event = new CustomEvent('configChanged', {
          detail: { tables: reallyChanged, configs: updatedConfigs }
        });
        document.dispatchEvent(event);
      }.bind(this)).catch(function (err) {
        console.error('[ConfigSync] 拉取配置失败:', err);
      });
    }

    // ─── 配置拉取 ──────────────────────────────────────────────

    _fetchChangedTables(tables) {
      var promises = tables.map(function (tableName) {
        return fetch('/api/config/tables/' + tableName)
          .then(function (r) { return r.json(); })
          .then(function (data) {
            return { table: tableName, data: data };
          })
          .catch(function (err) {
            console.error('[ConfigSync] 拉取配置表 ' + tableName + ' 失败:', err);
            return { table: tableName, data: null, error: err };
          });
      });

      return Promise.all(promises).then(function (results) {
        var configs = {};
        for (var i = 0; i < results.length; i++) {
          if (results[i].data) {
            configs[results[i].table] = results[i].data;
          }
        }
        return configs;
      });
    }

    // ─── 手动触发重载 ──────────────────────────────────────────

    triggerReload() {
      return fetch('/api/config/reload', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (result) {
          if (result.changed && result.changed.length > 0) {
            return this._fetchChangedTables(result.changed);
          }
          return {};
        }.bind(this));
    }

    // ─── 状态查询 ──────────────────────────────────────────────

    isConnected() {
      return this._connected;
    }

    getStatus() {
      return {
        connected: this._connected,
        wsUrl: this._wsUrl,
        cachedTables: Object.keys(this._tableCache).length
      };
    }
  }

  // Expose
  global.ConfigManager = {
    init: init,
    CATEGORIES: CATEGORIES,
    toggleLock: toggleLock,
    confirmRollback: confirmRollback,
    exportAll: exportAll,
    importConfig: importConfig,
    showDiff: showDiff,
    ConfigSync: ConfigSync
  };

  // Auto-init
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window);

  // === from main.js ===

/**
 * main.js — Single-page application controller for MetaCore Stock Pool Platform
 *
 * Orchestrates FlowCanvas, PoolDataManager, TableDrivenPanel, HighlightManager,
 * and all UI interactions (toolbar, sidebar, modes, replay, context menu, etc.)
 */

var FlowCanvas = window.FlowCanvas;
// PoolDataManager defined in data.js section of this file
var HighlightManager = window.HighlightManager;
var TableDrivenPanel = window.TableDrivenPanel;

(function () {
  // ─── Helpers ────────────────────────────────────────────────────────────────
  var $ = function (id) { return document.getElementById(id); };
  var $$ = function (sel) { return document.querySelectorAll(sel); };

  // Position dropdown menu using fixed positioning to avoid overflow clipping
  function positionDropdown(menuEl, btnEl) {
    if (!menuEl || !btnEl || menuEl.classList.contains('hidden')) return;
    var rect = btnEl.getBoundingClientRect();
    menuEl.style.position = 'fixed';
    menuEl.style.top = rect.bottom + 2 + 'px';
    menuEl.style.left = rect.left + 'px';
    menuEl.style.zIndex = '9999';
  }

  // ─── Application State ──────────────────────────────────────────────────────
  var poolData = new PoolDataManager();
  var canvas = null;
  var propPanel = null;
  var highlightManager = null;

  var poolRunStatus = 'stopped';
  var flowMode = false;
  var flowSourceId = null;

  var replaySessionId = null;
  var replayPollingInterval = null;
  var currentReplayTime = '';
  var stockPollingInterval = null;

  var simSessionId = null;
  var simPollingInterval = null;
  var simStepCount = 0;
  var _simAutoStepping = false;
  var _simAutoInterval = null;
  var _simSpeed = 1.0;

  var mouseX = 0, mouseY = 0;
  var activePoolId = null;      // currently loaded pool ID in sidebar

  // Expose for table-driven-panel callbacks
  window.showStockModal = null;
  window.showColumnEditor = null;

  // ─── Table-Driven Config Loading ──────────────────────────────────────────
  // 配置表异步加载，加载前使用默认值作为 fallback（参考 kline-chart.js 模式）
  var _configRes = {};          // 已解析的配置表（同步可用）
  var _configPromises = {};     // 加载中的 Promise

  var _configDefaults = {
    cell_type_registry: {
      add_node_mapping: {
        source:    { tdx_cell_type: 7,   dzh_cell_type: 202, tdx_action: 'addSource',    dzh_action: 'addSource' },
        condition: { tdx_cell_type: 3,   dzh_cell_type: 201, tdx_action: 'addCondition', dzh_action: 'addCondition' },
        statepool: { tdx_cell_type: 8,   dzh_cell_type: 200, tdx_action: 'addStatePool', dzh_action: 'addStatePool' },
        discard:   { tdx_cell_type: 5,   dzh_cell_type: 203, tdx_action: 'addDiscard',   dzh_action: 'addDiscard' },
        label:     { tdx_cell_type: 1,   dzh_cell_type: 1,   tdx_action: 'addLabel',     dzh_action: 'addLabel' }
      }
    },
    data_providers: {
      capability_labels: {
        kline: 'K线', snapshot: '快照', market: '市场', formula: '公式',
        sector: '板块', replay: '回放', financial: '财务', user_block: '自选'
      }
    },
    defaults: {
      replay: { start_date: '2024-01-01', end_date: '2024-12-31' }
    },
    toolbar_config: { groups: [], buttons: [] },
    context_menu_config: { menus: {} },
    keyboard_shortcuts: { shortcuts: [] }
  };

  function loadConfig(tableName) {
    if (_configPromises[tableName]) return _configPromises[tableName];
    var defaultVal = _configDefaults[tableName] || {};
    _configPromises[tableName] = fetch('/api/config/tables/' + tableName)
      .then(function (r) { return r.json(); })
      .then(function (cfg) {
        _configRes[tableName] = cfg || defaultVal;
        return _configRes[tableName];
      })
      .catch(function (err) {
        console.warn('[main.js] 加载配置表 ' + tableName + ' 失败,使用默认值:', err);
        _configRes[tableName] = defaultVal;
        return defaultVal;
      });
    return _configPromises[tableName];
  }

  function getConfig(tableName) {
    return _configRes[tableName] || _configDefaults[tableName] || {};
  }

  // 根据 nodeType (source/condition/statepool/discard/label) 获取 cell_type
  function _getAddNodeCellType(nodeType) {
    var mapping = getConfig('cell_type_registry').add_node_mapping || _configDefaults.cell_type_registry.add_node_mapping;
    var entry = mapping[nodeType];
    if (!entry) return null;
    return poolData._isTDX ? entry.tdx_cell_type : entry.dzh_cell_type;
  }

  // 根据 action (addSource/addCondition/.../addContainer/addColumn) 获取 cell_type
  // 工具栏下拉和右键菜单共用此函数，保持表驱动一致性
  function _getAddNodeCellTypeByAction(action) {
    var directMap = { addContainer: 2, addColumn: 3 };
    if (directMap[action]) return directMap[action];
    var mapping = getConfig('cell_type_registry').add_node_mapping || _configDefaults.cell_type_registry.add_node_mapping;
    var nodeType = null;
    Object.keys(mapping).forEach(function (nt) {
      var entry = mapping[nt];
      var act = poolData._isTDX ? entry.tdx_action : entry.dzh_action;
      if (act === action) nodeType = nt;
    });
    return nodeType ? _getAddNodeCellType(nodeType) : (poolData._isTDX ? 7 : 202);
  }

  // ─── Datasource Selector ──────────────────────────────────────────────────
  var _dsSources = [];

  function initDatasourceSelector() {
    var btn = $('btnDatasource');
    var dropdown = $('datasourceDropdown');
    if (!btn || !dropdown) return;

    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var isHidden = dropdown.classList.contains('hidden');
      $$('.tb-dropdown-menu').forEach(function (m) { m.classList.add('hidden'); });
      if (isHidden) {
        dropdown.classList.remove('hidden');
        positionDropdown(dropdown, btn);
        loadDatasources();
      }
    });

    document.addEventListener('click', function () {
      dropdown.classList.add('hidden');
    });
    dropdown.addEventListener('click', function (e) { e.stopPropagation(); });

    loadDatasources();
  }

  function loadDatasources() {
    fetch('/api/meta/datasource/list')
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (resp.code === 0 && resp.data) {
          _dsSources = resp.data;
          renderDatasourceDropdown();
        }
      })
      .catch(function () {});
  }

  function renderDatasourceDropdown() {
    var dropdown = $('datasourceDropdown');
    var dsBtnLabel = $('dsBtnLabel');
    if (!dropdown) return;

    dropdown.innerHTML = '';

    _dsSources.forEach(function (src) {
      var item = document.createElement('div');
      item.className = 'datasource-item' + (src.active ? ' active' : '') + (!src.ready ? ' disabled' : '');

      var dot = document.createElement('span');
      dot.className = 'datasource-dot ' + (src.ready ? 'ready' : 'not-ready');

      var label = document.createElement('span');
      label.className = 'datasource-label';
      label.textContent = src.label;

      var caps = document.createElement('span');
      caps.className = 'datasource-caps';
      var capLabels = getConfig('data_providers').capability_labels || _configDefaults.data_providers.capability_labels;
      caps.textContent = (src.capabilities || []).map(function (c) { return capLabels[c] || c; }).join(' ');

      var check = document.createElement('span');
      check.className = 'datasource-check';
      check.textContent = src.active ? '✓' : '';

      item.appendChild(dot);
      item.appendChild(label);
      item.appendChild(caps);
      item.appendChild(check);

      if (src.ready && !src.active) {
        item.addEventListener('click', function () {
          switchDatasource(src.name);
        });
      }

      dropdown.appendChild(item);
    });

    var activeSrc = _dsSources.find(function (s) { return s.active; });
    if (dsBtnLabel && activeSrc) {
      dsBtnLabel.textContent = activeSrc.label;
    }
  }

  function switchDatasource(sourceName) {
    fetch('/api/meta/datasource/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: sourceName })
    })
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (resp.code === 0) {
          loadDatasources();
          var st = $('statusText');
          if (st) st.textContent = '数据源已切换: ' + sourceName;
        } else {
          alert(resp.msg || '切换数据源失败');
        }
      })
      .catch(function () { alert('切换数据源失败'); });
  }

  // ─── Initialization ─────────────────────────────────────────────────────────
  function init() {
    // 把所有下拉菜单移到 body 级容器，避免被 topbar overflow 裁剪
    var dc = document.getElementById('dropdownContainer');
    if (dc) {
      document.querySelectorAll('.tb-dropdown-menu').forEach(function (menu) {
        dc.appendChild(menu);
        menu.style.pointerEvents = 'auto';
      });
    }

    canvas = new FlowCanvas($('canvasWrapper'), {
      onConnect: onConnect,
      onNodeClick: onNodeClick,
      onEdgeClick: onEdgeClick,
      onCanvasClick: onCanvasClick,
      onNodeDoubleClick: onNodeDoubleClick,
      onNodeDragEnd: onNodeDragEnd,
      onZoomChange: onZoomChange
    });

    propPanel = new TableDrivenPanel($('panelBody'), $('panelTitle'), poolData);
    propPanel.onPropertyChange = function (item, field, value) {
      if (item && item.id) {
        var node = poolData.getNodeById(item.id);
        if (node) canvas._rerenderNode(node.id);
      }
    };

    // ── bsavehis 历史数据查看：面板事件 → 画布节点更新 ──
    $('panelBody').addEventListener('tdx:historyView', function (e) {
      canvas.handleHistoryView(e.detail);
    });

    highlightManager = new HighlightManager(canvas);
    highlightManager.init();

    window.showStockModal = showStockModal;
    window.showColumnEditor = showColumnEditor;
    window.__canvas = canvas;
    window.__poolData = poolData;
    // 暴露 poolData / canvas 为全局,供综合设置等独立模块使用
    window.poolData = poolData;
    window.canvas = canvas;

    // ── Edge line type polyfill (ensure exists even if module cached) ──
    if (typeof canvas.edgeLineType === 'undefined') {
      canvas.edgeLineType = 'bezier';
    }

    // ── _buildEdgePath polyfill (add orthogonal support) ──
    var _oldBuild = canvas._buildEdgePath;
    if (typeof _oldBuild === 'function') {
      var _testOrth = _oldBuild({x:0,y:0},{x:100,y:100},0,'orthogonal');
      // If old version doesn't produce orthogonal routing, replace it
      if (!_testOrth || _testOrth.indexOf('L50,') === -1) {
        canvas._buildEdgePath = function (sp, tp, midVal, type) {
          var lt = type || this.edgeLineType;
          // Orthogonal: 4-point horizontal/vertical routing
          if (lt === 'orthogonal') {
            var dx = tp.x - sp.x;
            var dy = tp.y - sp.y;
            var midX = sp.x + dx * 0.5 + (midVal || 0) * 2;
            return 'M' + sp.x + ',' + sp.y +
              ' L' + midX + ',' + sp.y +
              ' L' + midX + ',' + tp.y +
              ' L' + tp.x + ',' + tp.y;
          }
          // Bezier curve
          if (midVal !== 0 || lt === 'bezier') {
            var bdx = (tp.x - sp.x) * 0.4;
            var offset = midVal * 1.5;
            return 'M' + sp.x + ',' + sp.y + ' C' + (sp.x + bdx) + ',' + (sp.y + offset) + ' ' + (tp.x - bdx) + ',' + (tp.y + offset) + ' ' + tp.x + ',' + tp.y;
          }
          // Straight line
          return 'M' + sp.x + ',' + sp.y + ' L' + tp.x + ',' + tp.y;
        };
      }
    }

    if (typeof canvas.setEdgeLineType !== 'function') {
      canvas.setEdgeLineType = function (type) {
        if (!['bezier', 'orthogonal', 'straight'].includes(type)) return;
        this.edgeLineType = type;
        var self = this;
        this._edges.forEach(function (edge) {
          var existing = self._edgeElements.get(edge.id);
          if (existing) {
            existing.path.remove();
            if (existing.hitPath) existing.hitPath.remove();
            if (existing.label && existing.label.parentNode) existing.label.remove();
            self._edgeElements.delete(edge.id);
            self._renderEdge(edge);
          }
        });
      };
    }

    // ── Handle creation polyfill (fix module cache: ensure connection handles exist in DOM) ──
    var SKIP_TYPES = ['text_label', 'container', 'state_column', 'flow_arrow',
      'execution_order', 'tdx_deco_text', 'tdx_text_label', 'tdx_container', 'tdx_deco_line'];

    canvas._addHandles = function (el, node) {
      if (SKIP_TYPES.indexOf(node.type) !== -1) return;
      var self = this;
      // Source handle (right side) — blue
      var sh = document.createElement('div');
      sh.className = 'flow-handle flow-handle-source';
      sh.setAttribute('data-node-id', node.id);
      sh.setAttribute('data-handle-id', 'source');
      sh.setAttribute('data-handle-type', 'source');
      sh.style.cssText = 'position:absolute;right:-6px;top:50%;transform:translateY(-50%);width:14px;height:14px;border-radius:50%;background:#4a90d9;border:2px solid #2a5a99;cursor:crosshair;z-index:10;';
      sh.addEventListener('mousedown', function (e) { self._startConnectionDrag(node.id, 'source', 'source', e); });
      el.appendChild(sh);
      this._handleElements.set(node.id + '-source', sh);
      // Target handle (left side) — green
      var th = document.createElement('div');
      th.className = 'flow-handle flow-handle-target';
      th.setAttribute('data-node-id', node.id);
      th.setAttribute('data-handle-id', 'target');
      th.setAttribute('data-handle-type', 'target');
      th.style.cssText = 'position:absolute;left:-6px;top:50%;transform:translateY(-50%);width:14px;height:14px;border-radius:50%;background:#27ae60;border:2px solid #1a7a44;cursor:crosshair;z-index:10;';
      el.appendChild(th);
      this._handleElements.set(node.id + '-target', th);
    };

    // Ensure handles exist after every render/refresh (covers both loadPool→render and onChange→refresh paths)
    function _ensureHandles(canvas) {
      if (canvas._nodes && canvas.nodeElements) {
        canvas._nodes.forEach(function (node) {
          var el = canvas.nodeElements.get(node.id);
          if (el && el.querySelectorAll('.flow-handle').length === 0) {
            canvas._addHandles(el, node);
          }
        });
      }
    }

    var _origRender = canvas.render.bind(canvas);
    canvas.render = function (data) {
      _origRender(data);
      _ensureHandles(this);
    };

    var _origRefresh = canvas.refresh.bind(canvas);
    canvas.refresh = function (data) {
      _origRefresh(data);
      _ensureHandles(this);
    };

    // ── Ensure handles exist after initial async data load ──
    setTimeout(function () {
      if (canvas._nodes && canvas._nodes.length > 0) {
        var domHandles = document.querySelectorAll('.flow-handle').length;
        if (domHandles === 0) {
          canvas.refresh();
        }
      }
    }, 800);

    // Canvas event handlers
    canvas.setToolClickHandler(onToolClick);

    // Data change listener
    poolData.onChange(function (data) {
      canvas.refresh(data);
      updateStatusBar();
      updateToolbarButtons();
    });

    // Zoom display sync
    canvas.svg.addEventListener('zoomchange', function (e) {
      var pct = Math.round(e.detail.zoom * 100) + '%';
      $('topbarZoom').textContent = pct;
      var zl = $('zoomLevel'); if (zl) zl.textContent = pct;
      $('statusZoom').textContent = pct;
    });

    // Bind all UI (toolbar bind deferred to ToolbarRenderer.init().then())
    // bindToolbar() moved to ToolbarRenderer.init().then() callback
    bindKeyboard();
    bindContextMenu();
    bindPanelToggle();
    bindSidebar();
    bindReplayControls();
    updateModeButtons();
    bindMobileTabs();
    bindFileInput();
    bindNewPoolModal();

    // Load pool list sidebar
    loadPoolList();

    // Check URL params for pool ID or demo name
    var urlParams = new URLSearchParams(window.location.search);
    var poolId = urlParams.get('id') || urlParams.get('pool');
    var demoName = urlParams.get('demo');

    if (poolId) {
      loadPool(poolId);
    } else if (demoName) {
      loadDemo(demoName);
    } else {
      // Load demo pool
      poolData.initDemo();
      poolData._poolId = 'demo';
      canvas.setPoolId('demo');
      canvas.render(poolData.data);
      canvas.fitToContent(40);
      updateStatusBar();
      updateToolbarButtons();
    }

    // Start coordinate tracking
    startCoordinateTracking();

    // Start status bar clock (current time / replay time)
    startStatusBarClock();

    // Load config registry
    poolData.loadRegistry().catch(function(err) { console.warn('加载配置注册表失败:', err); });

    // 实例化 ConfigSync 并连接 WebSocket 监听后端配置变更
    // ConfigSync 类内部收到变更消息时已自动派发 'configChanged' CustomEvent（见 app.js:8950-8953）
    var configSync = new ConfigManager.ConfigSync({});
    configSync.connect();
    window._configSync = configSync; // 保存引用避免被 GC

    // 监听 configChanged 事件：配置变更后重新加载 registry 并刷新属性面板
    document.addEventListener('configChanged', function(e) {
      var tables = (e.detail && e.detail.tables) || [];
      var changedStr = tables.join(',');
      // 检查是否包含 loadRegistry() 关心的配置表（表名为配置文件名去掉 .json 后缀）
      if (changedStr.indexOf('field_definitions') !== -1 ||
          changedStr.indexOf('cell_type_registry') !== -1 ||
          changedStr.indexOf('dzh_type_map') !== -1 ||
          changedStr.indexOf('flow_mode_registry') !== -1 ||
          changedStr.indexOf('edge_strategies') !== -1 ||
          changedStr.indexOf('defaults') !== -1 ||
          changedStr.indexOf('modules') !== -1) {
        poolData.loadRegistry().then(function() {
          // 重新渲染当前属性面板以反映新配置
          if (propPanel && typeof propPanel._reRenderCurrentPanel === 'function') {
            propPanel._reRenderCurrentPanel();
          }
        }).catch(function(err) {
          console.warn('[ConfigSync] 重新加载 registry 失败:', err);
        });
      }
    });

    // 加载表驱动配置表（工具栏/右键菜单/快捷键/节点类型/数据源/默认值）
    loadConfig('cell_type_registry');
    loadConfig('data_providers');
    loadConfig('defaults');
    // 表驱动：ToolbarRenderer 加载配置并渲染工具栏，完成后绑定事件
    loadConfig('ui_state');
    if (typeof ToolbarRenderer !== 'undefined') {
      ToolbarRenderer.init({
        toolbarContainer: $('toolbarButtons'),
        overflowContainer: $('overflowMenu'),
        state: getUIState()
      }).then(function() {
        bindToolbar();
        updateToolbarButtons();
      }).catch(function(err) {
        console.warn('[main.js] ToolbarRenderer 初始化失败:', err);
        bindToolbar();
      });
    } else {
      // fallback: ToolbarRenderer 未加载时直接绑定
      loadConfig('toolbar_config').then(function(cfg) { applyToolbarConfig(cfg); });
      bindToolbar();
    }
    loadConfig('context_menu_config');
    loadConfig('keyboard_shortcuts').then(function(cfg) { applyKeyboardConfig(cfg); });

    // SubTask 2.2: 初始化模式指示器为设计模式（页面加载时 modeIndicator 默认为空）
    var _initMi = $('modeIndicator');
    if (_initMi && !_initMi.textContent) {
      _initMi.className = 'mode-design';
      _initMi.textContent = '设计模式';
      _initMi.style.display = '';
    }
    updateModeButtons();
  }

  // ─── Pool List Sidebar ──────────────────────────────────────────────────────

  function loadPoolList() {
    // Load TDX pool files
    fetch('/api/files/tdxpool?_t=' + Date.now())
      .then(function (r) { return r.json(); })
      .then(function (json) {
        var files = (json.data || []);
        renderFileList($('poolListTdx'), files, 'tdx');
      })
      .catch(function (e) {
        console.error('[loadPoolList] tdxpool 加载失败:', e);
      });

    // Load DZH pool files
    fetch('/api/files/dzhpool?_t=' + Date.now())
      .then(function (r) { return r.json(); })
      .then(function (json) {
        var files = (json.data || []);
        renderFileList($('poolListDzh'), files, 'dzh');
      })
      .catch(function (e) {
        console.error('[loadPoolList] dzhpool 加载失败:', e);
      });

    // Load example files
    fetch('/api/files/examples?_t=' + Date.now())
      .then(function (r) { return r.json(); })
      .then(function (json) {
        var files = (json.data || []);
        renderFileList($('poolListExamples'), files, 'example');
      })
      .catch(function (e) {
        console.error('[loadPoolList] examples 加载失败:', e);
      });
  }

  function renderFileList(listEl, files, category) {
    listEl.innerHTML = '';
    var extIcons = { xml: '📄', json: '📋', py: '🐍' };
    var thumbBasePath = category === 'tdx' ? '/tdxpool/'
      : category === 'dzh' ? '/dzhpool/' : null;
    var filtered = files.filter(function (file) {
      if (category === 'tdx' || category === 'dzh') return file.ext === 'xml';
      if (category === 'example') return file.ext === 'json';
      return true;
    });

    if (category === 'example') {
      filtered.sort(function (a, b) {
        var aIsTarget = a.name === 'target_pool_100.json';
        var bIsTarget = b.name === 'target_pool_100.json';
        if (aIsTarget && !bIsTarget) return -1;
        if (!aIsTarget && bIsTarget) return 1;
        return a.name.localeCompare(b.name);
      });
    }

    filtered.forEach(function (file) {
      var li = document.createElement('li');
      li.setAttribute('data-filename', file.name);
      li.setAttribute('data-category', category);
      li.classList.add('loadable');

      if (category === 'example' && file.name === 'target_pool_100.json') {
        li.style.background = 'rgba(26,188,156,0.15)';
        li.style.borderLeft = '3px solid #1abc9c';
      }

      var icon = extIcons[file.ext] || '📄';
      var nameSpan = document.createElement('div');
      nameSpan.className = 'pool-item-name';
      nameSpan.textContent = icon + ' ' + file.name;

      var metaDiv = document.createElement('div');
      metaDiv.className = 'pool-item-meta';
      var sizeStr = file.size > 1024 ? (file.size / 1024).toFixed(1) + 'KB' : file.size + 'B';
      metaDiv.innerHTML = '<span>' + file.ext.toUpperCase() + '</span><span>' + sizeStr + '</span>';

      li.appendChild(nameSpan);
      li.appendChild(metaDiv);

      if (thumbBasePath) {
        var thumbImg = document.createElement('img');
        thumbImg.className = 'pool-item-thumbnail';
        thumbImg.alt = file.name;
        var thumbUrl = thumbBasePath + file.name.replace(/\.xml$/i, '.png');
        thumbImg.onload = function () { this.classList.add('loaded'); };
        thumbImg.onerror = function () { this.classList.add('error'); };
        thumbImg.src = thumbUrl;
        li.appendChild(thumbImg);
      }

      li.addEventListener('click', function () {
        if (category === 'tdx') {
          loadAndDisplayTDXPool(file.name.replace(/\.xml$/i, ''));
        } else if (category === 'dzh') {
          loadDZHFileFromDir(file.name);
        } else if (category === 'example') {
          loadExampleFile(file.name);
        }
        listEl.querySelectorAll('li').forEach(function (el) { el.classList.remove('active'); });
        li.classList.add('active');
      });

      listEl.appendChild(li);
    });
  }

  function renderSavedPoolList() {
    var listEl = $('poolListSaved');
    if (!listEl) return;
    listEl.innerHTML = '<li style="padding:8px 10px;color:var(--text-muted);font-size:12px;">加载中...</li>';

    fetch('/api/pools?_t=' + Date.now())
      .then(function (r) { return r.json(); })
      .then(function (json) {
        listEl.innerHTML = '';
        var pools = (json.data || []);
        if (pools.length === 0) {
          listEl.innerHTML = '<li style="padding:8px 10px;color:var(--text-muted);font-size:12px;">暂无已保存的股票池</li>';
          return;
        }
        pools.forEach(function (pool) {
          var li = document.createElement('li');
          li.setAttribute('data-pool-id', pool.pool_id || pool.id);

          var nameSpan = document.createElement('div');
          nameSpan.className = 'pool-item-name';
          nameSpan.textContent = '💾 ' + (pool.name || '未命名池');

          var metaDiv = document.createElement('div');
          metaDiv.className = 'pool-item-meta';
          var updatedStr = pool.updated_at || pool.updated_time || '';
          if (updatedStr) {
            // Show only the datetime portion
            updatedStr = updatedStr.replace('T', ' ').substring(0, 19);
          }
          metaDiv.innerHTML = '<span>' + (updatedStr || '无更新时间') + '</span>';

          var openBtn = document.createElement('button');
          openBtn.textContent = '打开';
          openBtn.style.cssText = 'padding:2px 8px;font-size:11px;border-radius:3px;border:1px solid var(--border-light);background:var(--bg-input);color:var(--text-primary);cursor:pointer;flex-shrink:0;';
          openBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            var poolId = pool.pool_id || pool.id;
            loadPool(poolId);
          });

          var delBtn = document.createElement('button');
          delBtn.textContent = '删除';
          delBtn.style.cssText = 'padding:2px 8px;font-size:11px;border-radius:3px;border:1px solid #e74c3c;background:transparent;color:#e74c3c;cursor:pointer;flex-shrink:0;margin-left:4px;';
          delBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            var poolId = pool.pool_id || pool.id;
            var poolName = pool.name || '未命名池';
            if (!confirm('确定删除股票池 "' + poolName + '" 吗？此操作不可撤销。')) return;
            fetch('/api/pools/' + poolId, { method: 'DELETE' })
              .then(function (r) { return r.json(); })
              .then(function (json) {
                if (json.success || json.ok || json.code === 0) {
                  showToast('已删除: ' + poolName, 'success');
                  renderSavedPoolList();
                } else {
                  showToast('删除失败: ' + (json.error || '未知错误'), 'error');
                }
              })
              .catch(function (err) {
                showToast('删除失败: ' + err.message, 'error');
              });
          });

          li.appendChild(nameSpan);
          li.appendChild(metaDiv);
          li.appendChild(openBtn);
          li.appendChild(delBtn);

          li.addEventListener('click', function () {
            var poolId = pool.pool_id || pool.id;
            loadPool(poolId);
            listEl.querySelectorAll('li').forEach(function (el) { el.classList.remove('active'); });
            li.classList.add('active');
          });

          listEl.appendChild(li);
        });
      })
      .catch(function (e) {
        listEl.innerHTML = '<li style="padding:8px 10px;color:var(--accent-red);font-size:12px;">加载失败</li>';
        console.error('[renderSavedPoolList] 加载失败:', e);
      });
  }

  function loadDZHFileFromDir(filename) {
    updateLoading(true, '正在加载大智慧池...');
    fetch('/api/files/dzhpool/' + encodeURIComponent(filename) + '/load?_t=' + Date.now())
      .then(function (r) { return r.json(); })
      .then(function (json) {
        if (json.success && json.data) {
          var data = json.data;
          var pid = data.id || data.pool_id || null;
          poolData._poolId = (pid && /^[0-9a-f]{8,}-/.test(pid)) ? pid : null;
          poolData.setData(data);
          if (poolData._poolId) canvas.setPoolId(poolData._poolId);
          canvas.render(poolData.data);
          canvas.fitToContent(40);
          poolData._history = [];
          poolData._redoStack = [];
          updateStatusBar();
          updateToolbarButtons();
          updateLoading(false);
          $('statusText').textContent = '已加载DZH池: ' + (json.name || filename);
        } else {
          updateLoading(false);
          showToast('导入失败: ' + (json.error || '未知错误'), 'error');
        }
      })
      .catch(function (e) { updateLoading(false); showToast('导入失败: ' + e.message, 'error'); });
  }

  function loadExampleFile(filename) {
    updateLoading(true, '正在加载示例...');
    fetch('/api/files/examples/' + encodeURIComponent(filename) + '/load?_t=' + Date.now())
      .then(function (r) { return r.json(); })
      .then(function (json) {
        if (json.success && json.data) {
          var data = json.data;
          var pid = data.id || data.pool_id || null;
          // 只有数据库 UUID 才作为 pool_id，否则使用配置方式启动仿真
          poolData._poolId = (pid && /^[0-9a-f]{8,}-/.test(pid)) ? pid : null;
          poolData.setData(data);
          if (poolData._poolId) canvas.setPoolId(poolData._poolId);
          canvas.render(poolData.data);
          canvas.fitToContent(40);
          poolData._history = [];
          poolData._redoStack = [];
          updateStatusBar();
          updateToolbarButtons();
          updateLoading(false);
          $('statusText').textContent = '已加载示例: ' + filename;
        } else {
          updateLoading(false);
          showToast('加载失败: ' + (json.error || '未知错误'), 'error');
        }
      })
      .catch(function (e) { updateLoading(false); showToast('加载失败: ' + e.message, 'error'); });
  }

  function showPoolContextMenu(e, pool, isTDX) {
    var cm = $('contextMenu');
    // Build a temporary pool-specific menu
    var menu = document.createElement('div');
    menu.className = 'ctx-pool-menu';
    menu.style.cssText = 'position:fixed;z-index:500;background:var(--bg-panel);border:1px solid var(--border-light);border-radius:6px;padding:4px 0;min-width:140px;box-shadow:0 4px 24px rgba(0,0,0,0.3);';
    menu.style.left = e.clientX + 'px';
    menu.style.top = e.clientY + 'px';

    var items = [
      { label: '编辑', action: 'edit' },
      { label: '删除', action: 'delete', danger: true },
      { label: '导出', action: 'export' }
    ];

    items.forEach(function (item) {
      var div = document.createElement('div');
      div.className = 'ctx-item' + (item.danger ? ' ctx-danger' : '');
      div.textContent = item.label;
      div.addEventListener('click', function () {
        menu.remove();
        if (item.action === 'edit') {
          if (isTDX) loadAndDisplayTDXPool(pool.name);
          else loadPool(pool.pool_id || pool.id);
        } else if (item.action === 'delete') {
          if (isTDX) {
            if (!confirm('确定要删除股票池"' + pool.name + '"吗？此操作不可撤销！')) return;
            poolData.deleteTDXPool(pool.name).then(function (res) {
              if (res.success) { showToast('已删除: ' + pool.name); refreshPoolList(); }
              else { showToast('删除失败: ' + (res.error || '未知错误'), 'error'); }
            }).catch(function (err) { showToast('删除出错: ' + err.message, 'error'); });
          } else deletePool(pool.pool_id || pool.id);
        } else if (item.action === 'export') {
          if (isTDX) {
            poolData.loadTDXPool(pool.name).then(function () { exportTDXXML(); });
          } else {
            loadPool(pool.pool_id || pool.id).then(function () { exportXML(); });
          }
        }
      });
      menu.appendChild(div);
    });

    document.body.appendChild(menu);
    var closeHandler = function () { menu.remove(); document.removeEventListener('click', closeHandler); };
    setTimeout(function () { document.addEventListener('click', closeHandler); }, 10);
  }

  function deletePool(poolId) {
    if (!confirm('确定要删除股票池 #' + poolId + ' 吗？此操作不可撤销！')) return;
    fetch('/api/pools/' + encodeURIComponent(poolId), { method: 'DELETE' })
      .then(function (r) { return r.json(); })
      .then(function (json) {
        if (json.code === 0) {
          showToast('已删除股票池 #' + poolId);
          refreshPoolList();
        } else {
          showToast('删除失败: ' + (json.msg || '未知错误'), 'error');
        }
      })
      .catch(function (err) {
        showToast('删除失败: ' + err.message, 'error');
      });
  }

  function refreshPoolList() {
    $('poolListTdx').innerHTML = '';
    $('poolListDzh').innerHTML = '';
    $('poolListExamples').innerHTML = '';
    $('poolListSaved').innerHTML = '';
    loadPoolList();
  }

  function bindSidebar() {
    // Hamburger button: open overflow menu + sidebar
    $('btnHamburger').addEventListener('click', function () {
      var sidebar = $('sidebarLeft');
      var overflow = $('overflowMenu');
      // Toggle overflow menu
      if (overflow.classList.contains('open')) {
        overflow.classList.remove('open');
      } else {
        overflow.classList.add('open');
        // Also open sidebar on tablet
        if (window.innerWidth > 768 && window.innerWidth <= 1024) {
          sidebar.classList.add('drawer-open');
        }
      }
      // On mobile, also toggle sidebar
      if (window.innerWidth <= 768) {
        if (sidebar.classList.contains('drawer-open')) {
          sidebar.classList.remove('drawer-open');
        } else {
          sidebar.classList.add('drawer-open');
        }
      }
    });

    // Close overflow menu when clicking outside
    document.addEventListener('click', function (e) {
      var overflow = $('overflowMenu');
      var hamburger = $('btnHamburger');
      if (overflow.classList.contains('open') && !overflow.contains(e.target) && e.target !== hamburger) {
        overflow.classList.remove('open');
      }
    });

    // Close sidebar when clicking a pool item (mobile/tablet)
    document.querySelector('.pool-list-scroll').addEventListener('click', function () {
      if (window.innerWidth <= 1024) {
        $('sidebarLeft').classList.remove('drawer-open');
      }
    });

    // Sidebar toggle close
    $('sidebarToggleClose').addEventListener('click', function () {
      var sidebar = $('sidebarLeft');
      if (sidebar.classList.contains('drawer-open')) {
        sidebar.classList.remove('drawer-open');
      } else {
        sidebar.classList.toggle('collapsed');
      }
    });

    // Pool search
    $('poolSearch').addEventListener('input', function () {
      var query = this.value.toLowerCase().trim();
      var activeList = document.querySelector('.pool-list-tab.active');
      if (!activeList) return;
      var items = activeList.querySelectorAll('li');
      items.forEach(function (li) {
        var name = li.querySelector('.pool-item-name');
        var text = name ? name.textContent.toLowerCase() : '';
        li.style.display = (!query || text.indexOf(query) !== -1) ? '' : 'none';
      });
    });

    // Sidebar tab switching
    document.querySelectorAll('.sidebar-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        document.querySelectorAll('.sidebar-tab').forEach(function (t) { t.classList.remove('active'); });
        document.querySelectorAll('.pool-list-tab').forEach(function (l) { l.classList.remove('active'); });
        tab.classList.add('active');
        var tabName = tab.getAttribute('data-tab');
        var listMap = { tdxpool: 'poolListTdx', dzhpool: 'poolListDzh', examples: 'poolListExamples', saved: 'poolListSaved' };
        var listEl = $(listMap[tabName]);
        if (listEl) listEl.classList.add('active');
        if (tabName === 'saved') renderSavedPoolList();
      });
    });
  }

  // ─── Pool Loading ───────────────────────────────────────────────────────────

  function loadPool(poolId) {
    updateLoading(true, '正在加载股票池 #' + poolId + '...');
    canvas.setPoolId(poolId);
    return poolData.loadFromAPI(poolId).then(function (data) {
      canvas.render(data);
      canvas.fitToContent(40);
      poolData._history = [];
      poolData._redoStack = [];
      updateStatusBar();
      updateToolbarButtons();
      updateLoading(false);
      var nameDisplay = $('poolNameDisplay');
      if (nameDisplay) nameDisplay.textContent = poolData._data?.pool_meta?.name || '';
      return data;
    }).catch(function (err) {
      showToast('加载失败: ' + err.message, 'error');
      updateLoading(false);
      poolData.initDemo();
      canvas.render(poolData.data);
      canvas.fitToContent(40);
    });
  }

  async function loadDemo(name) {
    updateLoading(true, '正在加载演示池 ' + name + '...');
    try {
      var res = await fetch('/api/dzh/load-demo?name=' + encodeURIComponent(name));
      var json = await res.json();
      if (!json.success) throw new Error(json.error || '加载失败');
      var data = json.data;
      var demoPoolId = 'demo_' + name;
      poolData._poolId = demoPoolId;
      canvas.setPoolId(demoPoolId);
      poolData.setData(data);
      canvas.render(data);
      canvas.fitToContent(40);
      poolData._history = [];
      poolData._redoStack = [];
      updateStatusBar();
      updateToolbarButtons();
      updateLoading(false);
    } catch (err) {
      showToast('加载演示池失败: ' + err.message, 'error');
      updateLoading(false);
      poolData.initDemo();
      canvas.render(poolData.data);
      canvas.fitToContent(40);
      updateStatusBar();
      updateToolbarButtons();
    }
  }

  function loadAndDisplayTDXPool(name) {
    poolData.loadTDXPool(name)
      .then(function (data) {
        canvas.render(data);
        canvas.fitToContent(40);
        poolData._history = [];
        poolData._redoStack = [];
        updateStatusBar();
        updateToolbarButtons();
        $('statusText').textContent = '已加载TDX池: ' + name;
      })
      .catch(function (err) {
        showToast('加载TDX池失败: ' + err.message, 'error');
      });
  }

  function updateLoading(show, msg) {
    $('statusText').textContent = show ? (msg || '加载中...') : '就绪';
  }

  // ─── Mode Switching ─────────────────────────────────────────────────────────

  function setMode(mode) {
    // 仿真模式运行中允许切换模式（exitSimulationMode 会清理运行状态）
    if (poolRunStatus !== 'stopped' && mode !== AppState.mode && AppState.mode !== 'simulation') {
      showToast('股票池正在运行中，请先停止运行后再切换模式', 'error');
      return;
    }

    if (AppState.mode === 'run') exitRunMode();
    if (AppState.mode === 'replay') exitReplayMode();
    if (AppState.mode === 'simulation') exitSimulationMode();

    if (mode === 'design') {
      $('modeIndicator').className = 'mode-design';
      $('modeIndicator').textContent = '设计模式';
      $('modeIndicator').style.display = '';
      enableEditingUI();
      canvas.setRunMode(false);
      propPanel.setReadOnly(false);
      $('replayPanel').classList.remove('visible');
      $('simulationPanel').classList.remove('visible');
      if (typeof window.hideEventPanel === 'function') window.hideEventPanel(); else $('eventPanel').classList.add('hidden');
    } else if (mode === 'run') {
      $('modeIndicator').className = 'mode-run';
      $('modeIndicator').textContent = '实盘模式';
      $('modeIndicator').style.display = '';
      disableEditingUI();
      canvas.setRunMode(true);
      propPanel.setReadOnly(true);
      $('replayPanel').classList.remove('visible');
      $('simulationPanel').classList.remove('visible');
      if (typeof window.hideEventPanel === 'function') window.hideEventPanel(); else $('eventPanel').classList.add('hidden');
      executePool();
      startStockPolling();
    } else if (mode === 'replay') {
      $('modeIndicator').className = 'mode-replay';
      $('modeIndicator').textContent = '回放模式';
      $('modeIndicator').style.display = '';
      disableEditingUI();
      canvas.setRunMode(true);
      propPanel.setReadOnly(true);
      $('replayPanel').classList.add('visible');
      $('simulationPanel').classList.remove('visible');
      if (typeof window.showEventPanel === 'function') window.showEventPanel();
      if (highlightManager) {
        highlightManager.setReplayMode(true);
        highlightManager.destroy();
        highlightManager.init();
      }
      startReplaySession();
    } else if (mode === 'simulation') {
      $('modeIndicator').className = 'mode-simulation';
      $('modeIndicator').textContent = '仿真模式';
      $('modeIndicator').style.display = '';
      disableEditingUI();
      canvas.setRunMode(true);
      propPanel.setReadOnly(true);
      $('replayPanel').classList.remove('visible');
      $('simulationPanel').classList.add('visible');
      if (typeof window.showEventPanel === 'function') window.showEventPanel();
      else $('eventPanel').classList.add('visible');
      if (highlightManager) {
        highlightManager.destroy();
        highlightManager.init();
      }
      startSimulationSession();
    }
    AppState.setMode(mode);
    updateModeButtons();
  }

  function updateModeButtons() {
    var btnDesign = $('btnDesign');
    var btnRun = $('btnRun');
    var btnReplay = $('btnReplay');
    var btnSimulation = $('btnSimulation');
    var btnDesignOverflow = $('btnDesignOverflow');
    var btnRunOverflow = $('btnRunOverflow');
    var btnReplayOverflow = $('btnReplayOverflow');
    var btnSimulationOverflow = $('btnSimulationOverflow');

    var isDesign = (AppState.mode === 'design');
    if (btnDesign) btnDesign.classList.toggle('active', isDesign);
    if (btnRun) btnRun.classList.toggle('active', AppState.mode === 'run');
    if (btnReplay) btnReplay.classList.toggle('active', AppState.mode === 'replay');
    if (btnSimulation) btnSimulation.classList.toggle('active', AppState.mode === 'simulation');
    if (btnDesignOverflow) btnDesignOverflow.classList.toggle('active', isDesign);
    if (btnRunOverflow) btnRunOverflow.classList.toggle('active', AppState.mode === 'run');
    if (btnReplayOverflow) btnReplayOverflow.classList.toggle('active', AppState.mode === 'replay');
    if (btnSimulationOverflow) btnSimulationOverflow.classList.toggle('active', AppState.mode === 'simulation');
  }

  function exitRunMode() {
    stopStockPolling();
    canvas.setRunMode(false);
    propPanel.setReadOnly(false);
    if (highlightManager) {
      highlightManager.destroy();
      highlightManager.init();
    }
  }

  function exitReplayMode() {
    stopReplayPolling();
    replaySessionId = null;
    currentReplayTime = '';
    $('replayPanel').classList.remove('visible');
    if (typeof window.hideEventPanel === 'function') window.hideEventPanel(); else $('eventPanel').classList.add('hidden');
    canvas.setRunMode(false);
    propPanel.setReadOnly(false);
    if (highlightManager) {
      highlightManager.setReplayMode(false);
      highlightManager.destroy();
      highlightManager.init();
    }
  }

  // 运行控制按钮状态切换（IIFE 顶层定义，供 exitSimulationMode/updateSimBtnState/controlPool 共享）
  function updateRunControlButtons() {
    var btnStart = $('btnStart');
    var btnPause = $('btnPause');
    var btnStop = $('btnStop');
    if (!btnStart || !btnPause || !btnStop) return;

    // 仿真模式下，主工具栏按钮状态由仿真运行状态决定
    if (AppState.mode === 'simulation') {
      if (AppState.simulationState === 'running') {
        btnStart.disabled = true;
        btnPause.disabled = false;
        btnStop.disabled = false;
        btnStart.textContent = '▶ 运行中';
      } else if (AppState.simulationState === 'paused') {
        btnStart.disabled = false;
        btnPause.disabled = true;
        btnStop.disabled = false;
        btnStart.textContent = '▶ 继续';
      } else {
        btnStart.disabled = false;
        btnPause.disabled = true;
        btnStop.disabled = true;
        btnStart.textContent = '▶ 开始';
      }
      return;
    }

    if (poolRunStatus === 'running') {
      btnStart.disabled = true;
      btnPause.disabled = false;
      btnStop.disabled = false;
      btnStart.textContent = '▶ 运行中';
    } else if (poolRunStatus === 'paused') {
      btnStart.disabled = false;
      btnPause.disabled = true;
      btnStop.disabled = false;
      btnStart.textContent = '▶ 继续';
    } else {
      btnStart.disabled = false;
      btnPause.disabled = true;
      btnStop.disabled = true;
      btnStart.textContent = '▶ 开始';
    }
  }

  function exitSimulationMode() {
    stopSimAutoStep();
    stopSimulationPolling();
    simSessionId = null;
    window.simSessionId = null;
    simStepCount = 0;
    _simSpeed = 1.0;
    AppState.resetSimulation();
    // 重置主工具栏运行控制按钮状态（仿真退出后恢复为 stopped）
    updateRunControlButtons();
    $('simulationPanel').classList.remove('visible');
    if (typeof window.hideEventPanel === 'function') window.hideEventPanel(); else $('eventPanel').classList.add('hidden');
    canvas.setRunMode(false);
    propPanel.setReadOnly(false);
    if (highlightManager) {
      highlightManager.destroy();
      highlightManager.init();
    }
  }

  function disableEditingUI() {
    $('btnNew').disabled = true;
    $('btnImport').disabled = true;
    $('btnUndo').disabled = true;
    $('btnRedo').disabled = true;
    $('btnExecOrder').disabled = true;
    $('btnExport').disabled = true;
    $('btnComprehensiveSettings').disabled = true;
  }

  function enableEditingUI() {
    $('btnNew').disabled = false;
    $('btnImport').disabled = false;
    updateToolbarButtons();
    $('btnExecOrder').disabled = false;
    $('btnComprehensiveSettings').disabled = !poolData.hasData;
  }

  function executePool() {
    var poolMeta = poolData._data && poolData._data.pool_meta;
    var execUrl, execBody;

    if ((poolMeta && poolMeta.type === 'tdx') || poolData._isTDX) {
      var filename = poolData._tdxFilename || (poolMeta && poolMeta.filename) || (poolData._data.name || poolData._poolId || '') + '.xml';
      // TDX one-shot execute
      poolData.executeTDXPool(filename)
        .then(function (result) {
          syncExecResult(result.node_states || result);
          canvas.refreshStockTables();
          $('statusText').textContent = 'TDX池执行完成';
        })
        .catch(function (err) {
          showToast('TDX池执行失败: ' + err.message, 'error');
          $('statusText').textContent = 'TDX池执行失败';
        });
      return;
    }

    if (poolData._isTDX && poolData._tdxFilename) {
      execUrl = '/api/tdx/execute-pool';
      execBody = JSON.stringify({
        filename: poolData._tdxFilename,
        pool_data: { name: poolData.data.name, nodes: poolData.data.nodes, edges: poolData.data.edges, pool_meta: poolData.data.pool_meta || {} }
      });
    } else {
      execUrl = '/api/dzh/execute-pool';
      execBody = JSON.stringify({
        pool_id: poolData._poolId,
        pool_data: { name: poolData.data.name, nodes: poolData.data.nodes, edges: poolData.data.edges, pool_meta: poolData.data.pool_meta || {} }
      });
    }

    fetch(execUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: execBody })
      .then(function (r) { return r.json(); })
      .then(function (result) {
        var execResult = (result && result.data && result.data.data) || (result && result.data) || (result && result.result);
        if (!execResult && result && result.data && result.data.node_states) execResult = result.data.node_states;
        if (execResult && execResult.node_states && typeof execResult.node_states === 'object') execResult = execResult.node_states;
        syncExecResult(execResult);
        canvas.refreshStockTables();
        $('statusText').textContent = '运行模式';
      })
      .catch(function () {
        $('statusText').textContent = '运行模式 (执行失败)';
      });
  }

  function syncExecResult(execResult) {
    if (!execResult || typeof execResult !== 'object') return;
    Object.keys(execResult).forEach(function (cellId) {
      var cellResult = execResult[cellId];
      var node = poolData.data.nodes.find(function (n) { return n.id === cellId; });
      if (node && cellResult && cellResult.stocks) {
        if (!node.params) node.params = {};
        node.params.stocks = cellResult.stocks.map(function (s) {
          if (typeof s === 'string') return { label: s, code: s };
          var isTDXRich = (s.setcode !== undefined || s.inprice !== undefined);
          if (isTDXRich) {
            return {
              label: s.code || s.label, code: s.code || s.label,
              name: s.name, setcode: s.setcode, inprice: s.inprice,
              now: s.now, rise: s.rise, income: s.income,
              volume: s.volume, indate: s.indate, intime: s.intime,
              maxrate: s.maxrate, maxperiod: s.maxperiod,
              maxtime: s.maxtime, maxprice: s.maxprice, idaynum: s.idaynum
            };
          }
          return { label: s.name || s.code || s.label, code: s.code || s.label };
        });
        node.params.stock_count = cellResult.count || cellResult.stocks.length;
      }
    });
  }

  // ─── Canvas Event Handlers ──────────────────────────────────────────────────

  function formatStockCode(code) {
    if (!code) return '';
    code = String(code);
    if (code.length === 6 && code.match(/^\d+$/)) return code;
    if (code.length === 5 && code.match(/^\d+$/)) return code.padStart(6, '0');
    if (code.startsWith('fz') && code.length === 7) return 'fz' + code.slice(2).padStart(6, '0');
    return code;
  }

  async function fetchAndShowNodeStocks(poolId, nodeId) {
    try {
      var res = await fetch('/api/pools/' + encodeURIComponent(poolId) + '/nodes/' + encodeURIComponent(nodeId) + '/stocks');
      var data = await res.json();
      if (data.code === 0 && data.data && data.data.stocks) {
        var stocks = data.data.stocks;
        var node = poolData.getNodeById(nodeId);
        if (node) {
          if (!node.params) node.params = {};
          node.params.stocks = stocks.map(function(s) {
            return {
              code: formatStockCode(s.code),
              name: s.name || '',
              price: s.price || 0,
              change_pct: s.change_pct || 0,
              volume: s.volume || 0,
              enter_time: s.enter_time || ''
            };
          });
          node.params.stock_count = stocks.length;
          canvas._rerenderNode(nodeId);
        }
      }
    } catch (e) {
      console.warn('获取节点股票失败:', e);
    }
  }

  function onNodeClick(nodeId, node) {
    if (flowMode) {
      if (!flowSourceId) {
        flowSourceId = nodeId;
        highlightFlowSource(nodeId);
        $('statusText').textContent = '连线模式: 请点击目标节点';
      } else if (flowSourceId !== nodeId) {
        var result = poolData.validateFlow(flowSourceId, nodeId);
        if (result.valid) {
          var newEdge = poolData.addEdge(flowSourceId, nodeId);
          showToast('已创建连线: ' + result.fromName + ' → ' + result.toName);
          if (newEdge) {
            canvas.selectEdge(newEdge.id);
            propPanel.showForEdge(newEdge.id);
          }
        } else {
          showToast(result.reason || '不允许创建此连线', 'error');
        }
        flowSourceId = null;
        clearFlowHighlight();
        $('statusText').textContent = '就绪';
      }
    } else {
      propPanel.showForNode(nodeId);
      if (AppState.mode !== 'design') propPanel.setReadOnly(true);
      try { if (window.ComprehensiveSettings && window.ComprehensiveSettings.syncFromCanvas) window.ComprehensiveSettings.syncFromCanvas(nodeId); } catch (e) {}
      if (node && (node.type === 'stock_state_pool' || node.type === 'tdx_state_pool' || node.dzh_cell_type === 200 || node.dzh_cell_type === '200' || node.type === 'statepool')) {
        var pid = poolData._poolId;
        if (pid) {
          fetch('/api/pools/' + encodeURIComponent(pid) + '/state-pools/' + encodeURIComponent(nodeId) + '/prefetch-klines?period=day', { method: 'POST' })
            .catch(function () { });
          fetchAndShowNodeStocks(pid, nodeId);
        }
      }
    }
  }

  function onEdgeClick(edgeId, edge) {
    canvas.selectEdge(edgeId);
    propPanel.showForEdge(edgeId);
  }

  function onCanvasClick() {
    canvas.clearSelection();
    if (poolData.data && poolData.data.pool_meta) {
      propPanel.showForPool(poolData.data.pool_meta);
    } else {
      propPanel.showPlaceholder();
    }
    if (AppState.mode !== 'design') propPanel.setReadOnly(true);
    flowSourceId = null;
    clearFlowHighlight();
  }

  function onNodeDoubleClick(nodeId, node) {
    var n = poolData.getNodeById(nodeId);
    if (n && (n.type === 'stock_state_pool' || n.type === 'tdx_state_pool' || n.type === 'tdx_candidate')) {
      showStockModal(nodeId);
      return;
    }
    propPanel.showForNode(nodeId);
  }

  function onNodeDragEnd(nodeId, node) {
    poolData._snapshot();
  }

  function onConnect(params) {
    var sourceId = params.source;
    var targetId = params.target;
    if (!sourceId || !targetId || sourceId === targetId) return;

    var result = poolData.validateFlow(sourceId, targetId);
    if (result.valid) {
      var newEdge = poolData.addEdge(sourceId, targetId);
      if (newEdge) {
        showToast('已创建连线: ' + result.fromName + ' → ' + result.toName);
        canvas.selectEdge(newEdge.id);
        propPanel.showForEdge(newEdge.id);
      }
    } else {
      showToast(result.reason || '不允许创建此连线', 'error');
    }
  }

  function onZoomChange(zoom) {
    var pct = Math.round(zoom * 100) + '%';
    $('topbarZoom').textContent = pct;
    var zl = $('zoomLevel'); if (zl) zl.textContent = pct;
    $('statusZoom').textContent = pct;
  }

  function onToolClick(nodeId) {
    if (!flowMode) return;
  }

  function highlightFlowSource(nodeId) {
    var el = canvas.nodeElements.get(nodeId);
    if (el) el.classList.add('flow-source-highlight');
  }

  function clearFlowHighlight() {
    canvas.nodeElements.forEach(function (el) {
      el.classList.remove('flow-source-highlight');
    });
  }

  // ─── Toolbar Bindings ───────────────────────────────────────────────────────

  // 表驱动：获取当前 UI 状态（供 ToolbarRenderer 条件求值使用）
  function getUIState() {
    return {
      mode: AppState.mode === 'design' ? 'edit' : AppState.mode,
      pool_loaded: !!poolData.hasData,
      can_undo: poolData.canUndo(),
      can_redo: poolData.canRedo(),
      has_unsaved_changes: !!poolData.hasData,
      is_running: AppState.mode === 'run',
      is_replay_mode: AppState.mode === 'replay',
      selection: (canvas && canvas.selectedNodeId) ? canvas.selectedNodeId : null,
      has_clipboard: !!(window._clipboardData),
      is_edit_mode: AppState.mode === 'design',
      is_design_mode: AppState.mode === 'design',
      design_mode: AppState.mode === 'design'
    };
  }

  // 表驱动：评估 enabled_when 条件（委托给 ToolbarRenderer）
  function _evalEnabledWhen(cond) {
    if (typeof ToolbarRenderer !== 'undefined') {
      return ToolbarRenderer.evaluateCondition(cond, getUIState());
    }
    // fallback：ToolbarRenderer 未加载时使用简化逻辑
    if (!cond || cond === 'always') return true;
    var poolLoaded = !!poolData.hasData;
    if (cond === 'pool_loaded') return poolLoaded;
    if (cond === 'can_undo') return poolData.canUndo();
    if (cond === 'can_redo') return poolData.canRedo();
    if (cond === "mode == 'edit'") return AppState.mode === 'design';
    if (cond === "pool_loaded && mode == 'edit'") return poolLoaded && AppState.mode === 'design';
    if (cond === 'has_unsaved_changes && pool_loaded') return poolLoaded;
    return true;
  }

  // 表驱动：从 toolbar_config 渲染下拉菜单项
  function _renderDropdownFromConfig(dropdownEl, items, dataKey) {
    if (!dropdownEl || !items || !items.length) return;
    dropdownEl.innerHTML = items.map(function (it) {
      var attr = dataKey ? 'data-' + dataKey + '="' + (it[dataKey] || it.id) + '"' : '';
      var actionAttr = it.action ? ' data-action="' + it.action + '"' : '';
      return '<div class="tb-dropdown-item" ' + attr + actionAttr + '>' + (it.icon || '') + ' ' + it.label + '</div>';
    }).join('');
  }

  // 表驱动：应用 toolbar_config 到已有 HTML 按钮
  function applyToolbarConfig(cfg) {
    if (!cfg || !cfg.buttons) return;
    cfg.buttons.forEach(function (btn) {
      var el = $(btn.id);
      if (!el) return;
      // 更新 title
      if (btn.label) el.title = btn.label + (btn.shortcut ? ' (' + btn.shortcut + ')' : '');
      // 渲染下拉菜单项
      if (btn.dropdown && btn.dropdown_items) {
        var dropdownId = btn.id.replace('btn', '') + 'Dropdown';
        // 转换为首字母小写: AddNode → addNode
        dropdownId = dropdownId.charAt(0).toLowerCase() + dropdownId.slice(1);
        var dd = $(dropdownId);
        if (dd) {
          var dataKey = btn.id === 'btnAddNode' ? 'node-type' : 'format';
          _renderDropdownFromConfig(dd, btn.dropdown_items, dataKey);
        }
      }
    });
    updateToolbarButtons();
  }

  function bindToolbar() {
    // New pool
    $('btnNew').addEventListener('click', function () {
      $('newPoolModal').classList.remove('hidden');
    });

    // Add Node dropdown
    $('btnAddNode').addEventListener('click', function (e) {
      e.stopPropagation();
      $('importDropdown').classList.add('hidden');
      $('exportDropdown').classList.add('hidden');
      $('addNodeDropdown').classList.toggle('hidden');
      positionDropdown($('addNodeDropdown'), this);
    });

    $('addNodeDropdown').addEventListener('click', function (e) {
      var item = e.target.closest('.tb-dropdown-item');
      if (!item) return;
      e.stopPropagation();
      var action = item.dataset.action;
      var d = $('addNodeDropdown'); d.classList.add('hidden'); d.style.position = ''; d.style.top = ''; d.style.left = '';
      if (action) {
        var cellType = _getAddNodeCellTypeByAction(action);
        if (cellType !== null) addNodeAtCenter(cellType);
      }
    });

    // Import
    $('btnImport').addEventListener('click', function (e) {
      e.stopPropagation();
      $('exportDropdown').classList.add('hidden');
      $('importDropdown').classList.toggle('hidden');
      positionDropdown($('importDropdown'), this);
    });

    $('importDropdown').addEventListener('click', function (e) {
      var item = e.target.closest('.tb-dropdown-item');
      if (!item) return;
      e.stopPropagation();
      var format = item.dataset.format;
      var d = $('importDropdown'); d.classList.add('hidden'); d.style.position = ''; d.style.top = ''; d.style.left = '';
      $('fileInput').setAttribute('data-format', format);
      if (format === 'json') {
        $('fileInput').accept = '.json';
        $('importModalTitle').textContent = '导入JSON股票池';
        $('dropZoneText').textContent = '点击选择或拖拽 .json 文件到此处';
      } else if (format === 'tdx') {
        $('fileInput').accept = '.xml';
        $('importModalTitle').textContent = '导入通达信XML股票池';
        $('dropZoneText').textContent = '点击选择或拖拽 .xml 文件到此处';
      } else {
        $('fileInput').accept = '.xml';
        $('importModalTitle').textContent = '导入大智慧XML股票池';
        $('dropZoneText').textContent = '点击选择或拖拽 .xml 文件到此处';
      }
      $('importModal').classList.remove('hidden');
    });

    // Export
    $('btnExport').addEventListener('click', function (e) {
      e.stopPropagation();
      $('importDropdown').classList.add('hidden');
      $('exportDropdown').classList.toggle('hidden');
      positionDropdown($('exportDropdown'), this);
    });

    $('exportDropdown').addEventListener('click', function (e) {
      var item = e.target.closest('.tb-dropdown-item');
      if (!item) return;
      e.stopPropagation();
      var format = item.dataset.format;
      var d = $('exportDropdown'); d.classList.add('hidden'); d.style.position = ''; d.style.top = ''; d.style.left = '';
      if (format === 'tdx') exportTDXXML();
      else if (format === 'json') exportJSON();
      else exportXML();
    });

    // Close dropdowns on outside click
    document.addEventListener('click', function (e) {
      if (!e.target.closest('#importDropdown') && !e.target.closest('#btnImport')) {
        var d = $('importDropdown'); d.classList.add('hidden'); d.style.position = ''; d.style.top = ''; d.style.left = '';
      }
      if (!e.target.closest('#exportDropdown') && !e.target.closest('#btnExport')) {
        var d = $('exportDropdown'); d.classList.add('hidden'); d.style.position = ''; d.style.top = ''; d.style.left = '';
      }
      if (!e.target.closest('#addNodeDropdown') && !e.target.closest('#btnAddNode')) {
        var d = $('addNodeDropdown'); d.classList.add('hidden'); d.style.position = ''; d.style.top = ''; d.style.left = '';
      }
    });

    // Save
    $('btnSave').addEventListener('click', savePool);

    // Undo / Redo
    $('btnUndo').addEventListener('click', function () {
      poolData.undo();
      canvas.render(poolData.data);
      propPanel.showPlaceholder();
    });
    $('btnRedo').addEventListener('click', function () {
      poolData.redo();
      canvas.render(poolData.data);
      propPanel.showPlaceholder();
    });

    // Fit
    $('btnFit').addEventListener('click', function () {
      canvas.fitToContent(40);
    });

    // Exec Order — DZH§6.2 兼容：仅条件边编号，从1开始，点击空白退出
    var btnExecOrder = $('btnExecOrder');
    // 公共持久化函数：按条件边 _order 重排 edges（条件边+对应无条件边成对排列）
    function persistExecOrder() {
      canvas._showExecOrder = false;
      canvas._execOrderCounter = 0;
      if (btnExecOrder) btnExecOrder.style.background = '';
      if (poolData && typeof poolData.reorderEdgesByCondition === 'function') {
        var condEdges = poolData.getConditionEdges ? poolData.getConditionEdges() : [];
        condEdges.sort(function(a, b) {
          var oa = (a.params && a.params._order) || 9999;
          var ob = (b.params && b.params._order) || 9999;
          return oa - ob;
        });
        var orderedIds = condEdges.map(function(e) { return e.id; });
        poolData.reorderEdgesByCondition(orderedIds);
      }
      canvas.render(poolData.data);
    }
    function exitExecOrderMode() { persistExecOrder(); }
    // 画布自动退出（所有条件边编号完毕）时的回调
    canvas.onExecOrderComplete = function () { persistExecOrder(); };
    btnExecOrder.addEventListener('click', function () {
      if (canvas._showExecOrder) {
        // 退出模式 → 持久化
        exitExecOrderMode();
      } else {
        // 进入模式：仅对条件边初始化 _order（从1开始）
        if (poolData && poolData.data && poolData.data.edges) {
          var condEdges = poolData.getConditionEdges ? poolData.getConditionEdges() : [];
          condEdges.forEach(function(e, i) {
            if (!e.params) e.params = {};
            e.params._order = i + 1;  // 从1开始
          });
        }
        canvas._showExecOrder = true;
        canvas._execOrderCounter = 1;  // 从1开始
        this.style.background = '#3a3a5e';
        canvas.refresh();
      }
    });

    // Flow mode
    var btnFlowMode = $('btnFlowMode');
    if (btnFlowMode) {
      btnFlowMode.addEventListener('click', function () {
        flowMode = !flowMode;
        btnFlowMode.classList.toggle('active', flowMode);
        $('statusText').textContent = flowMode ? '连线模式：点击源节点再点击目标节点' : '就绪';
      });
    }

    // Edge line type selector — 按钮常驻工具栏，菜单挂到 dropdownContainer 避免残留
    var btnEdgeLineType = $('btnEdgeLineType');
    if (btnEdgeLineType) {
      var ltDropdown = $('edgeLineTypeDropdown');

      // ── Clean up stale line-type menus from previous loads ──
      var dc = document.getElementById('dropdownContainer');
      if (dc) {
        dc.querySelectorAll('.tb-dropdown-menu').forEach(function (m) {
          if (m.querySelectorAll('[data-linetype]').length > 0) m.remove();
        });
      }

      // ── Always create fresh menu in dropdownContainer ──
      var ltMenu = document.createElement('div');
      ltMenu.className = 'tb-dropdown-menu';
      ltMenu.style.minWidth = '120px';
      ltMenu.style.display = 'none';
      ltMenu.style.pointerEvents = 'auto';  // 覆盖父容器 pointer-events:none
      ['bezier', 'orthogonal', 'straight'].forEach(function (lt) {
        var item = document.createElement('div');
        item.className = 'tb-dropdown-item';
        item.setAttribute('data-linetype', lt);
        item.textContent = { bezier: '〰 贝兹曲线', orthogonal: '├ 横竖折线', straight: '─ 直线' }[lt];
        ltMenu.appendChild(item);
      });
      if (dc) { dc.appendChild(ltMenu); } else if (ltDropdown) { ltDropdown.appendChild(ltMenu); }

      var ltItems = ltMenu ? ltMenu.querySelectorAll('[data-linetype]') : [];
      if (ltItems.length) {
        btnEdgeLineType.addEventListener('click', function (e) {
          e.stopPropagation();
          if (!ltMenu) return;
          // 定位菜单到按钮下方
          var rect = btnEdgeLineType.getBoundingClientRect();
          ltMenu.style.position = 'fixed';
          ltMenu.style.left = rect.left + 'px';
          ltMenu.style.top = (rect.bottom + 2) + 'px';
          ltMenu.style.display = ltMenu.style.display === 'block' ? 'none' : 'block';
        });
        ltItems.forEach(function (item) {
          item.addEventListener('click', function () {
            var lt = this.getAttribute('data-linetype');
            canvas.setEdgeLineType(lt);
            btnEdgeLineType.textContent = { bezier: '〰 贝兹', orthogonal: '├ 横竖', straight: '─ 直线' }[lt] + ' ▼';
            if (ltMenu) ltMenu.style.display = 'none';
            showToast('线形: ' + { bezier: '贝兹曲线', orthogonal: '横竖折线', straight: '直线' }[lt]);
          });
        });
        document.addEventListener('click', function () {
          if (ltMenu) ltMenu.style.display = 'none';
        });
      }
    }

    // Mode switch buttons — 设计模式单独切换，运行模式互斥
    var btnDesign = $('btnDesign');
    var isDesignMode = true;
    var currentRunMode = 'simulation';

    if (btnDesign) btnDesign.addEventListener('click', function () {
      isDesignMode = !isDesignMode;
      if (isDesignMode) {
        setMode('design');
      } else {
        setMode(currentRunMode);
      }
      updateModeButtons();
    });

    function setRunMode(mode) {
      if (poolRunStatus !== 'stopped') {
        showToast('股票池正在运行中，请先停止运行后再切换模式', 'error');
        return;
      }
      currentRunMode = mode;
      if (isDesignMode) {
        isDesignMode = false;
      }
      setMode(mode);
      updateModeButtons();
    }

    $('btnRun').addEventListener('click', function () { setRunMode('run'); });
    $('btnReplay').addEventListener('click', function () { setRunMode('replay'); });
    $('btnSimulation').addEventListener('click', function () { setRunMode('simulation'); });

    // 加载示例池按钮
    var btnLoadDemo = $('btnLoadDemo');
    if (btnLoadDemo) {
      btnLoadDemo.addEventListener('click', function () {
        loadPool('sim_test_pool_100').then(function () {
          showToast('已加载示例池: sim_test_pool_100');
        }).catch(function (err) {
          showToast('加载示例池失败: ' + (err.message || err), 'error');
        });
      });
    }

    // 运行控制按钮
    async function controlPool(action) {
      // 仿真模式下，主工具栏运行按钮路由到仿真控制
      if (AppState.mode === 'simulation') {
        if (action === 'start' || action === 'resume') {
          startSimAutoStep();
        } else if (action === 'pause') {
          stopSimAutoStep();
        } else if (action === 'stop') {
          stopSimAutoStep();
          setMode('design');
        }
        return;
      }

      var poolId = poolData && poolData._poolId;
      if (!poolId) {
        showToast('请先加载一个股票池', 'error');
        return;
      }
      try {
        var res = await fetch('/api/pools/' + encodeURIComponent(poolId) + '/control/' + action, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });
        var data = await res.json();
        if (data.code === 0 && data.data) {
          var newStatus = data.data.status;
          if (action === 'start' || action === 'resume') poolRunStatus = 'running';
          else if (action === 'pause') poolRunStatus = 'paused';
          else if (action === 'stop') poolRunStatus = 'stopped';
          updateRunControlButtons();
          showToast('已' + (action === 'start' ? '启动' : action === 'pause' ? '暂停' : action === 'resume' ? '继续' : '停止') + '池运行');
        } else {
          showToast('操作失败: ' + (data.msg || '未知错误'), 'error');
        }
      } catch (e) {
        showToast('操作异常: ' + e.message, 'error');
      }
    }

    $('btnStart')?.addEventListener('click', function () {
      controlPool(poolRunStatus === 'paused' ? 'resume' : 'start');
    });
    $('btnPause')?.addEventListener('click', function () { controlPool('pause'); });
    $('btnStop')?.addEventListener('click', function () { controlPool('stop'); });

    updateRunControlButtons();

    // Overflow menu mode buttons
    var btnDesignOverflow = $('btnDesignOverflow');
    if (btnDesignOverflow) btnDesignOverflow.addEventListener('click', function () { setMode('design'); });
    var btnRunOverflow = $('btnRunOverflow');
    if (btnRunOverflow) btnRunOverflow.addEventListener('click', function () { setMode('run'); });
    var btnReplayOverflow = $('btnReplayOverflow');
    if (btnReplayOverflow) btnReplayOverflow.addEventListener('click', function () { setMode('replay'); });
    var btnSimulationOverflow = $('btnSimulationOverflow');
    if (btnSimulationOverflow) btnSimulationOverflow.addEventListener('click', function () { setMode('simulation'); });

    // Sidebar toggle (池列表显隐)
    var btnSidebar = $('btnTdxPools');
    function updateSidebarToggleBtn() {
      var sb = $('sidebarLeft');
      if (sb.classList.contains('collapsed')) {
        btnSidebar.textContent = '▶ 列表';
        btnSidebar.title = '展开池列表侧栏';
      } else {
        btnSidebar.textContent = '◀ 列表';
        btnSidebar.title = '隐藏池列表侧栏';
      }
    }
    btnSidebar.addEventListener('click', function () {
      $('sidebarLeft').classList.toggle('collapsed');
      updateSidebarToggleBtn();
    });
    // Initialize button text
    updateSidebarToggleBtn();

    // Rule editor
    $('btnRuleEditor').addEventListener('click', function () {
      if (window.ruleEditor) window.ruleEditor.open();
    });

    // Comprehensive settings
    $('btnComprehensiveSettings').addEventListener('click', function () {
      if (window.ComprehensiveSettings) window.ComprehensiveSettings.toggle();
    });

    // ── Datasource selector ──
    initDatasourceSelector();

    // ── Overflow menu bindings (mobile/tablet) ──
    var om = $('overflowMenu');
    if (om) {
      $('btnNewOverflow').addEventListener('click', function () { $('newPoolModal').classList.remove('hidden'); om.classList.remove('open'); });
      $('btnFitOverflow').addEventListener('click', function () { canvas.fitToContent(40); om.classList.remove('open'); });
      $('btnExecOrderOverflow').addEventListener('click', function () { $('btnExecOrder').click(); om.classList.remove('open'); });
      $('btnFlowModeOverflow').addEventListener('click', function () { $('btnFlowMode').click(); om.classList.remove('open'); });
      $('btnTdxPoolsOverflow').addEventListener('click', function () { $('btnTdxPools').click(); om.classList.remove('open'); });
      $('btnRuleEditorOverflow').addEventListener('click', function () { $('btnRuleEditor').click(); om.classList.remove('open'); });
      $('btnComprehensiveSettingsOverflow').addEventListener('click', function () { $('btnComprehensiveSettings').click(); om.classList.remove('open'); });
      $('btnUndoOverflow').addEventListener('click', function () { $('btnUndo').click(); om.classList.remove('open'); });
      $('btnRedoOverflow').addEventListener('click', function () { $('btnRedo').click(); om.classList.remove('open'); });

      // Add Node overflow dropdown
      $('btnAddNodeOverflow').addEventListener('click', function (e) {
        e.stopPropagation();
        $('importDropdownOverflow').classList.add('hidden');
        $('exportDropdownOverflow').classList.add('hidden');
        $('addNodeDropdownOverflow').classList.toggle('hidden');
      });
      $('addNodeDropdownOverflow').addEventListener('click', function (e) {
        var item = e.target.closest('.tb-dropdown-item');
        if (!item) return;
        e.stopPropagation();
        var action = item.dataset.action;
        $('addNodeDropdownOverflow').classList.add('hidden');
        om.classList.remove('open');
        if (action) {
          var cellType = _getAddNodeCellTypeByAction(action);
          if (cellType !== null) addNodeAtCenter(cellType);
        }
      });

      // Import overflow dropdown
      $('btnImportOverflow').addEventListener('click', function (e) {
        e.stopPropagation();
        $('exportDropdownOverflow').classList.add('hidden');
        $('addNodeDropdownOverflow').classList.add('hidden');
        $('importDropdownOverflow').classList.toggle('hidden');
      });
      $('importDropdownOverflow').addEventListener('click', function (e) {
        var item = e.target.closest('.tb-dropdown-item');
        if (!item) return;
        e.stopPropagation();
        var format = item.dataset.format;
        $('importDropdownOverflow').classList.add('hidden');
        om.classList.remove('open');
        $('fileInput').setAttribute('data-format', format);
        if (format === 'json') {
          $('fileInput').accept = '.json';
          $('importModalTitle').textContent = '导入JSON股票池';
          $('dropZoneText').textContent = '点击选择或拖拽 .json 文件到此处';
        } else if (format === 'tdx') {
          $('fileInput').accept = '.xml';
          $('importModalTitle').textContent = '导入通达信XML股票池';
          $('dropZoneText').textContent = '点击选择或拖拽 .xml 文件到此处';
        } else {
          $('fileInput').accept = '.xml';
          $('importModalTitle').textContent = '导入大智慧XML股票池';
          $('dropZoneText').textContent = '点击选择或拖拽 .xml 文件到此处';
        }
        $('importModal').classList.remove('hidden');
      });

      // Export overflow dropdown
      $('btnExportOverflow').addEventListener('click', function (e) {
        e.stopPropagation();
        $('importDropdownOverflow').classList.add('hidden');
        $('addNodeDropdownOverflow').classList.add('hidden');
        $('exportDropdownOverflow').classList.toggle('hidden');
      });
      $('exportDropdownOverflow').addEventListener('click', function (e) {
        var item = e.target.closest('.tb-dropdown-item');
        if (!item) return;
        e.stopPropagation();
        var format = item.dataset.format;
        $('exportDropdownOverflow').classList.add('hidden');
        om.classList.remove('open');
        if (format === 'tdx') exportTDXXML();
        else if (format === 'json') exportJSON();
        else exportXML();
      });
    }

    // Zoom controls (FlowCanvas has built-in zoom controls, but also bind if HTML elements exist)
    var zi = $('zoomIn'), zo = $('zoomOut'), zf = $('zoomFit');
    if (zi) zi.addEventListener('click', function () { canvas.zoomIn(); });
    if (zo) zo.addEventListener('click', function () { canvas.zoomOut(); });
    if (zf) zf.addEventListener('click', function () { canvas.fitToContent(40); });

    // Import modal
    var dropZone = $('dropZone');
    var importModal = $('importModal');
    var btnCancelImport = $('btnCancelImport');

    if (dropZone) {
      dropZone.addEventListener('click', function () {
        $('fileInput').click();
      });
      dropZone.addEventListener('dragover', function (e) {
        e.preventDefault();
        dropZone.classList.add('drag-over');
      });
      dropZone.addEventListener('dragleave', function () {
        dropZone.classList.remove('drag-over');
      });
      dropZone.addEventListener('drop', function (e) {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) {
          handleImportFile(e.dataTransfer.files[0]);
        }
      });
    }

    if (btnCancelImport) {
      btnCancelImport.addEventListener('click', function () {
        importModal.classList.add('hidden');
        var pv = $('importPreview'); if (pv) pv.classList.add('hidden');
        var cb = $('btnConfirmImport'); if (cb) cb.classList.add('hidden');
        _pendingImportFile = null;
      });
    }

    var btnConfirmImport = $('btnConfirmImport');
    if (btnConfirmImport) {
      btnConfirmImport.addEventListener('click', confirmImport);
    }
  }

  // ─── New Pool Modal ─────────────────────────────────────────────────────────

  function bindNewPoolModal() {
    $('btnCancelNew').addEventListener('click', function () {
      $('newPoolModal').classList.add('hidden');
    });
    $('btnConfirmNew').addEventListener('click', function () {
      var name = $('newPoolName').value.trim() || '新股票池';
      var type = $('newPoolType').value;
      var market = $('newPoolMarket').value;
      var bgColor = $('newPoolBgColor').value.trim() || '16777216';

      poolData.initNew();
      poolData._data.name = name;
      poolData._data.pool_meta.backcolor = parseInt(bgColor) || 16777216;
      poolData._data.pool_meta.market = market;
      if (type === 'tdx') {
        poolData._data.pool_meta.type = 'tdx';
        poolData._isTDX = true;
      } else {
        poolData._isTDX = false;
      }
      canvas.render(poolData.data);
      canvas.setZoom(1);
      propPanel.showPlaceholder();
      updateStatusBar();
      updateToolbarButtons();
      $('newPoolModal').classList.add('hidden');
      showToast('已创建新的空白股票池');
    });
  }

  // ─── File Import/Export ─────────────────────────────────────────────────────

  var _pendingImportFile = null;   // 待导入文件（预览确认后导入）

  function bindFileInput() {
    $('fileInput').addEventListener('change', function (e) {
      var file = e.target.files[0];
      if (!file) return;
      var format = e.target.getAttribute('data-format') || 'dzh';
      e.target.value = '';
      e.target.removeAttribute('data-format');
      e.target.accept = '.xml';
      showImportPreview(file, format);
    });
  }

  function handleImportFile(file) {
    var format = $('fileInput').getAttribute('data-format');
    if (!format) {
      var ext = (file.name.split('.').pop() || '').toLowerCase();
      if (ext === 'json') format = 'json';
      else if (ext === 'xml') format = 'dzh';
      else format = 'dzh';
    }
    showImportPreview(file, format);
  }

  /** 显示文件预览，等待用户确认后导入 */
  function showImportPreview(file, format) {
    _pendingImportFile = { file: file, format: format };
    var previewEl = $('importPreview');
    var infoEl = $('importPreviewInfo');
    var contentEl = $('importPreviewContent');
    var confirmBtn = $('btnConfirmImport');
    if (!previewEl) return;

    // 显示文件信息
    var sizeStr = file.size > 1024 ? (file.size / 1024).toFixed(1) + ' KB' : file.size + ' B';
    var formatLabel = format === 'tdx' ? '通达信XML' : format === 'json' ? 'JSON' : 'DZH XML';
    infoEl.textContent = '文件名: ' + file.name + ' | 大小: ' + sizeStr + ' | 格式: ' + formatLabel;

    // JSON 格式：做结构化校验并展示摘要
    if (format === 'json') {
      var reader = new FileReader();
      reader.onload = function (e) {
        var text = String(e.target.result || '');
        var validation = validatePoolJSON(text);
        contentEl.textContent = validation.summary + '\n' + (text.length > 1200 ? text.substring(0, 1200) + '\n... (已截断)' : text);
        if (validation.ok) {
          confirmBtn.classList.remove('hidden');
          confirmBtn.disabled = false;
        } else {
          confirmBtn.classList.add('hidden');
          showToast(validation.error, 'error');
        }
      };
      reader.onerror = function () {
        contentEl.textContent = '(无法读取文件预览)';
        confirmBtn.classList.add('hidden');
      };
      reader.readAsText(file);
      previewEl.classList.remove('hidden');
      return;
    }

    // 读取文件前 2000 字符作为预览
    var reader = new FileReader();
    reader.onload = function (e) {
      var text = String(e.target.result || '');
      contentEl.textContent = text.length > 2000 ? text.substring(0, 2000) + '\n... (已截断)' : text;
    };
    reader.onerror = function () {
      contentEl.textContent = '(无法读取文件预览)';
    };
    reader.readAsText(file.slice(0, 2048));

    previewEl.classList.remove('hidden');
    confirmBtn.classList.remove('hidden');
    confirmBtn.disabled = false;
  }

  /** 校验股票池 JSON 并返回摘要/错误 */
  function validatePoolJSON(text) {
    var result = { ok: false, summary: '', error: '' };
    var data;
    try {
      data = JSON.parse(text);
    } catch (err) {
      result.error = 'JSON 格式错误: ' + err.message;
      result.summary = '❌ ' + result.error;
      return result;
    }
    if (!data || typeof data !== 'object') {
      result.error = 'JSON 根元素必须是对象';
      result.summary = '❌ ' + result.error;
      return result;
    }
    if (data.version !== 1 && data.version !== 2) {
      result.error = '仅支持 version=1 或 version=2 的股票池配置';
      result.summary = '❌ ' + result.error;
      return result;
    }
    if (!Array.isArray(data.nodes)) {
      result.error = '缺少 nodes 数组';
      result.summary = '❌ ' + result.error;
      return result;
    }
    if (!Array.isArray(data.edges)) {
      result.error = '缺少 edges 数组';
      result.summary = '❌ ' + result.error;
      return result;
    }
    var stockCount = 0;
    data.nodes.forEach(function (n) {
      if (n && n.params && Array.isArray(n.params.stocks)) stockCount += n.params.stocks.length;
    });
    result.ok = true;
    result.summary = '✅ 校验通过\n' +
      '• 名称: ' + (data.name || (data.pool_meta && data.pool_meta.name) || '-') + '\n' +
      '• 类型: ' + (data.pool_type || (data.pool_meta && data.pool_meta.pool_type) || '-') + '\n' +
      '• 节点: ' + data.nodes.length + ' 个\n' +
      '• 连线: ' + data.edges.length + ' 条' + (stockCount ? '\n• 候选股票: ' + stockCount + ' 只' : '');
    return result;
  }

  /** 确认导入：执行实际导入操作 */
  function confirmImport() {
    if (!_pendingImportFile) return;
    var file = _pendingImportFile.file;
    var format = _pendingImportFile.format;
    _pendingImportFile = null;
    $('importModal').classList.add('hidden');
    $('importPreview').classList.add('hidden');
    $('btnConfirmImport').classList.add('hidden');
    if (format === 'tdx') doImportTDXFile(file);
    else if (format === 'json') doImportJSONFile(file);
    else doImportFile(file);
  }

  async function doImportFile(file) {
    try {
      $('statusText').textContent = '正在导入...';
      var data = await poolData.importXML(file);
      canvas.render(data);
      canvas.fitToContent(40);
      showToast('导入成功: ' + (data.name || file.name));
      poolData._history = [];
      poolData._redoStack = [];
      updateStatusBar();
      updateToolbarButtons();
    } catch (err) {
      showToast('导入失败: ' + err.message, 'error');
    }
    $('statusText').textContent = '就绪';
  }

  async function doImportTDXFile(file) {
    try {
      $('statusText').textContent = '正在导入TDX...';
      var data = await poolData.importTDXXML(file);
      canvas.render(data);
      canvas.fitToContent(40);
      showToast('TDX导入成功: ' + (data.name || file.name));
      poolData._history = [];
      poolData._redoStack = [];
      updateStatusBar();
      updateToolbarButtons();
    } catch (err) {
      showToast('TDX导入失败: ' + err.message, 'error');
    }
    $('statusText').textContent = '就绪';
  }

  async function exportXML() {
    try {
      $('statusText').textContent = '正在导出...';
      await poolData.exportXML();
      showToast('导出成功');
      $('statusText').textContent = '已导出';
    } catch (err) {
      showToast('导出失败: ' + err.message, 'error');
    }
  }

  async function exportTDXXML() {
    try {
      $('statusText').textContent = '正在导出TDX...';
      await poolData.exportTDXXml();
      showToast('TDX导出成功');
      $('statusText').textContent = '已导出';
    } catch (err) {
      showToast('TDX导出失败: ' + err.message, 'error');
    }
  }

  async function doImportJSONFile(file) {
    try {
      $('statusText').textContent = '正在导入JSON...';
      var data = await poolData.importJSON(file);
      canvas.render(data);
      canvas.fitToContent(40);
      showToast('JSON导入成功: ' + (data.name || file.name));
      poolData._history = [];
      poolData._redoStack = [];
      updateStatusBar();
      updateToolbarButtons();
    } catch (err) {
      showToast('JSON导入失败: ' + err.message, 'error');
    }
    $('statusText').textContent = '就绪';
  }

  async function exportJSON() {
    try {
      $('statusText').textContent = '正在导出JSON...';
      await poolData.exportJSON();
      showToast('JSON导出成功');
      $('statusText').textContent = '已导出';
    } catch (err) {
      showToast('JSON导出失败: ' + err.message, 'error');
    }
  }

  async function savePool() {
    try {
      $('statusText').textContent = '正在保存...';
      await poolData.saveToAPI();
      showToast('保存成功');
      $('statusText').textContent = '已保存';
      // 通知综合设置窗口重置 dirty 标记
      try { if (window.ComprehensiveSettings && window.ComprehensiveSettings.markSaved) window.ComprehensiveSettings.markSaved(); } catch (e) {}
      updateStatusBar();
    } catch (err) {
      showToast('保存失败: ' + err.message, 'error');
      $('statusText').textContent = '保存失败';
    }
  }

  // ─── Keyboard Shortcuts ─────────────────────────────────────────────────────

  // 表驱动：键盘 action 派发
  function _execKeyboardAction(action) {
    switch (action) {
      case 'newPool':
        $('newPoolModal').classList.remove('hidden');
        break;
      case 'savePool':
        savePool();
        break;
      case 'undo':
        poolData.undo();
        canvas.render(poolData.data);
        break;
      case 'redo':
        poolData.redo();
        canvas.render(poolData.data);
        break;
      case 'copyToClipboard':
        var nidC = canvas.getSelectedNodeId();
        if (nidC) { poolData.copyToClipboard(nidC); showToast('已复制'); }
        break;
      case 'pasteFromClipboard':
        poolData.pasteFromClipboard();
        canvas.render(poolData.data);
        break;
      case 'cutToClipboard':
        var nidX = canvas.getSelectedNodeId();
        if (nidX) { poolData.cutToClipboard(nidX); canvas.render(poolData.data); }
        break;
      case 'deleteSelected':
        if (AppState.mode !== 'design') return;
        if (flowMode && flowSourceId) {
          flowSourceId = null;
          clearFlowHighlight();
          $('statusText').textContent = '连线模式: 点击源节点';
          return;
        }
        var selNid = canvas.getSelectedNodeId();
        var selEid = canvas.getSelectedEdgeId();
        if (selNid) { poolData.removeNode(selNid); canvas.render(poolData.data); propPanel.showPlaceholder(); }
        else if (selEid) { poolData.removeEdge(selEid); canvas.render(poolData.data); propPanel.showPlaceholder(); }
        break;
      case 'clearSelection':
        canvas.clearSelection();
        propPanel.showPlaceholder();
        if (flowMode) {
          flowMode = false;
          flowSourceId = null;
          clearFlowHighlight();
          $('statusText').textContent = '就绪';
        }
        break;
      case 'toggleFlowMode':
        flowMode = !flowMode;
        flowSourceId = null;
        clearFlowHighlight();
        $('statusText').textContent = flowMode ? '连线模式: 点击源节点' : '就绪';
        break;
      case 'zoomIn':
        canvas.zoomIn();
        break;
      case 'zoomOut':
        canvas.zoomOut();
        break;
      // 预留 action
      case 'selectAll':
        canvas.selectedNodeIds = (canvas._nodes || []).map(function (n) { return n.id; });
        canvas.selectedNodeId = null;
        canvas.selectedEdgeId = null;
        canvas._syncSelectionStyles();
        break;
      case 'runStop':
      case 'moveNodeUp': case 'moveNodeDown': case 'moveNodeLeft': case 'moveNodeRight':
        break;
    }
  }

  // 表驱动：应用 keyboard_shortcuts 配置（配置已在 _configRes 中，keydown 时自动读取）
  function applyKeyboardConfig(cfg) {
    // 配置已存储在 _configRes 中，bindKeyboard 的 keydown 处理器会自动读取
    // 此函数作为配置加载完成的回调占位
  }

  function bindKeyboard() {
    document.addEventListener('keydown', function (e) {
      var tag = e.target.tagName;
      var isInput = (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target.isContentEditable);
      if (isInput) return;

      // 处理内置快捷键表（不覆盖Ctrl/Alt/Meta组合键）
      if (!e.ctrlKey && !e.altKey && !e.metaKey) {
        var handler = SHORTCUTS[e.key];
        if (handler) {
          if (e.key === ' ') e.preventDefault();
          handler();
          return;
        }
      }

      // 表驱动：从 keyboard_shortcuts 配置匹配快捷键
      var cfg = getConfig('keyboard_shortcuts');
      var shortcuts = cfg.shortcuts || [];
      if (shortcuts.length) {
        for (var i = 0; i < shortcuts.length; i++) {
          var sc = shortcuts[i];
          if (e.key !== sc.key) continue;
          if (!!e.ctrlKey !== !!sc.ctrl) continue;
          if (!!e.shiftKey !== !!sc.shift) continue;
          if (!!e.altKey !== !!sc.alt) continue;
          // Ctrl 组合键和 Delete/Backspace 需要 preventDefault
          if (sc.ctrl || sc.key === 'Delete' || sc.key === 'Backspace') {
            e.preventDefault();
          }
          _execKeyboardAction(sc.action);
          return;
        }
        return;
      }

      // Fallback：配置未加载时使用原逻辑
      if (e.ctrlKey && e.key === 's') {
        e.preventDefault();
        savePool();
      } else if (e.ctrlKey && e.key === 'z') {
        e.preventDefault();
        poolData.undo();
        canvas.render(poolData.data);
      } else if ((e.ctrlKey && e.key === 'y') || (e.ctrlKey && e.shiftKey && e.key === 'Z')) {
        e.preventDefault();
        poolData.redo();
        canvas.render(poolData.data);
      } else if (e.ctrlKey && e.key === 'c') {
        var nid = canvas.getSelectedNodeId();
        if (nid) { poolData.copyToClipboard(nid); showToast('已复制'); }
      } else if (e.ctrlKey && e.key === 'v') {
        e.preventDefault();
        poolData.pasteFromClipboard();
        canvas.render(poolData.data);
      } else if (e.ctrlKey && e.key === 'x') {
        var nid2 = canvas.getSelectedNodeId();
        if (nid2) { poolData.cutToClipboard(nid2); canvas.render(poolData.data); }
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        if (AppState.mode !== 'design') return;
        if (flowMode && flowSourceId) {
          flowSourceId = null;
          clearFlowHighlight();
          $('statusText').textContent = '连线模式: 点击源节点';
          return;
        }
        var selNid = canvas.getSelectedNodeId();
        var selEid = canvas.getSelectedEdgeId();
        if (selNid) { poolData.removeNode(selNid); canvas.render(poolData.data); propPanel.showPlaceholder(); }
        else if (selEid) { poolData.removeEdge(selEid); canvas.render(poolData.data); propPanel.showPlaceholder(); }
      } else if (e.key === 'Escape') {
        canvas.clearSelection();
        propPanel.showPlaceholder();
        if (flowMode) {
          flowMode = false;
          flowSourceId = null;
          clearFlowHighlight();
          $('statusText').textContent = '就绪';
        }
      } else if (e.key === 'f' && !e.ctrlKey && !e.metaKey) {
        flowMode = !flowMode;
        flowSourceId = null;
        clearFlowHighlight();
        $('statusText').textContent = flowMode ? '连线模式: 点击源节点' : '就绪';
      } else if (e.key === '+' || e.key === '=') {
        canvas.zoomIn();
      } else if (e.key === '-') {
        canvas.zoomOut();
      }
    });
  }

  // ─── Context Menu ───────────────────────────────────────────────────────────

  function bindContextMenu() {
    var cm = $('contextMenu');
    var _ctxCanvasX = 0, _ctxCanvasY = 0;
    var _ctxTargetType = null;   // 'candidate'|'condition'|'state_pool'|'cond_edge'|'uncond_edge'|null
    var _ctxTargetId = null;
    var _ctxIsRuntime = false;

    // ── 菜单项定义表 ──
    // 每个 item: { action, label, icon?, danger?, checkable?, checked? }
    function _buildMenu(items) {
      var html = '';
      items.forEach(function(it) {
        if (it === '---') { html += '<div class="ctx-sep"></div>'; return; }
        var cls = 'ctx-item';
        if (it.danger) cls += ' ctx-danger';
        if (it.checked) cls += ' ctx-checked';
        html += '<div class="' + cls + '" data-action="' + it.action + '">' +
          (it.icon || '') + ' ' + it.label + '</div>';
      });
      return html;
    }

    // ── 判断右键目标类型 ──
    function _resolveCtxTarget() {
      var nid = canvas.getSelectedNodeId();
      var eid = canvas.getSelectedEdgeId();
      if (nid) {
        var node = canvas._findNode(nid);
        if (!node) return { type: null, id: null };
        if (poolData._isTDX) {
          if (node.type === 'tdx_candidate') return { type: 'candidate', id: nid, node: node };
          if (node.type === 'tdx_condition') return { type: 'condition', id: nid, node: node };
          if (node.type === 'tdx_state_pool') return { type: 'state_pool', id: nid, node: node };
        }
        // DZH types fallback
        if (node.type === 'market_source') return { type: 'candidate', id: nid, node: node };
        if (node.type === 'transfer_condition') return { type: 'condition', id: nid, node: node };
        if (node.type === 'stock_state_pool') return { type: 'state_pool', id: nid, node: node };
        // 标签节点（DZH text_label / TDX tdx_text_label / tdx_deco_text）
        if (node.type === 'text_label' || node.type === 'tdx_text_label' || node.type === 'tdx_deco_text') {
          return { type: 'label', id: nid, node: node };
        }
        return { type: 'generic_node', id: nid, node: node };
      }
      if (eid) {
        var edge = poolData.getEdgeById(eid);
        if (!edge) return { type: null, id: null };
        var params = edge.params || {};
        // 有条件边：有条件公式参数(nset等)或有明确条件标记
        var hasCondition = params.nset !== undefined || params.condition_formula || params.tdx_func;
        return { type: hasCondition ? 'cond_edge' : 'uncond_edge', id: eid, edge: edge, params: params };
      }
      return { type: null, id: null }; // 空白区域
    }

    // ── 构建三维菜单 ──
    function _buildContextMenu(targetInfo) {
      var t = targetInfo.type;
      var isDesign = AppState.mode === 'design';
      var items = [];

      if (!t) {
        // 空白区域：添加节点子菜单
        items = [
          { action: 'addNode', label: '➕ 添加节点 ▸', isSub: true },
        ];
      } else if (t === 'candidate') {
        // 候选池: 属性 + 说明文字
        var _descLabel_C = poolData._isTDX ? '✏ 修改说明文字和样式' : '✏ 修改说明文字';
        items = [
          { action: 'properties', label: '🔧 属性' },
          { action: 'comprehensiveSettings', label: '⚙ 综合设置' },
          '---',
          { action: 'editDescText', label: _descLabel_C },
        ];
      } else if (t === 'condition') {
        // 条件节点: 属性 + 说明文字 + 停止运算
        var node = targetInfo.node || {};
        var params = node.params || {};
        var isDisabled = !!params.disabled;
        var _descLabel_N = poolData._isTDX ? '✏ 修改说明文字和样式' : '✏ 修改说明文字';
        if (!isDesign && !poolData._isTDX) {
          // DZH runtime condition: show stock list hint (original shows dropdown)
          var _stockCount = (params.stks || []).length;
          items = [
            { action: 'viewConditionStocks', label: '▼ 查看通过条件的股票 (' + _stockCount + '只)' },
          ];
        } else {
          items = [
            { action: 'properties', label: '🔧 属性' },
            { action: 'comprehensiveSettings', label: '⚙ 综合设置' },
            '---',
            { action: 'editDescText', label: _descLabel_N },
            '---',
            { action: 'toggleDisabled', label: (isDisabled ? '▶ 恢复运算' : '⏸ 停止运算'), checked: isDisabled },
          ];
        }
      } else if (t === 'state_pool') {
        var node = targetInfo.node || {};
        var params = node.params || {};
        var showRowNum = !!params.show_row_num;
        var hasStockSelection = false; // 后续可实现

        if (isDesign) {
          // 设计模式
          var _descLabel_S = poolData._isTDX ? '✏ 修改说明文字和样式' : '✏ 修改说明文字';
          items = [
            { action: 'properties', label: '🔧 属性' },
            { action: 'comprehensiveSettings', label: '⚙ 综合设置' },
            '---',
            { action: 'editDescText', label: _descLabel_S },
            '---',
            { action: 'addStockToPool', label: '➕ 手工添加股票到状态池' },
            { action: 'clearAllStocks', label: '🗑 清除状态池中所有股票', danger: true },
            { action: 'allStockToBlock', label: '📋 所有股票加入到板块股' },
            '---',
            { action: 'toggleRowNum', label: (showRowNum ? '☑ 隐藏行号' : '☐ 显示行号'), checked: showRowNum },
          ];
          if (hasStockSelection) {
            items.push('---');
            items.push({ action: 'addToFavorites', label: '★ 将当前银行加入到自选股' });
            items.push({ action: 'viewChart', label: '📊 查看|删除发行走势' });
            items.push({ action: 'deleteStockRow', label: '✕ 从本次池中删除该笔(行)', danger: true });
          }
        } else {
          // 运行时模式
          items = [
            { action: 'addToFavorites', label: '★ 将当前银行加入到自选股' },
            { action: 'viewChart', label: '📊 查看|删除发行走势' },
            '---',
            { action: 'allStockToBlock', label: '📋 所有股票加入到板块股' },
            '---',
            { action: 'toggleRowNum', label: (showRowNum ? '☑ 隐藏行号' : '☐ 显示行号'), checked: showRowNum },
          ];
        }
      } else if (t === 'cond_edge') {
        // 有条件边: 属性 + 线条宽度
        items = [
          { action: 'properties', label: '🔧 属性' },
          { action: 'comprehensiveSettings', label: '⚙ 综合设置' },
          '---',
          { action: 'editLineWidth', label: '📏 线条宽度 (' + (targetInfo.params.size || 1) + ')' },
        ];
      } else if (t === 'uncond_edge') {
        // 无条件边: 仅线条宽度
        items = [
          { action: 'editLineWidth', label: '📏 线条宽度 (' + (targetInfo.params.size || 1) + ')' },
        ];
      } else if (t === 'generic_node') {
        // 通用节点（DZH或其他）：保持原有行为
        items = [
          { action: 'properties', label: '🔧 属性' },
          { action: 'comprehensiveSettings', label: '⚙ 综合设置' },
          '---',
          { action: 'copy', label: '📋 复制' },
          { action: 'cut', label: '✂ 剪切' },
          { action: 'paste', label: '📄 粘贴' },
          '---',
          { action: 'bringToFront', label: '⬆ 置于顶层' },
          { action: 'sendToBack', label: '⬇ 置于底层' },
          '---',
          { action: 'delete', label: '🗑 删除', danger: true },
        ];
      }

      return items;
    }

    // ── 表驱动：从 context_menu_config 构建菜单 HTML ──
    function _buildCtxMenuFromConfig(targetType, cellType, isDesignMode, ctxParams) {
      var cfg = getConfig('context_menu_config');
      var menus = cfg.menus || {};
      var items = menus[targetType] || [];
      if (!items.length) return null;

      // 过滤 + 排序
      var filtered = items.filter(function (item) {
        // cell_types 过滤
        if (item.cell_types && item.cell_types.indexOf('all') === -1) {
          if (!cellType || item.cell_types.indexOf(String(cellType)) === -1) return false;
        }
        // visible_when 过滤
        if (item.visible_when === 'design_mode' && !isDesignMode) return false;
        // has_clipboard: 仅在剪贴板有内容时显示
        if (item.visible_when === 'has_clipboard') {
          return poolData._clipboard && poolData._clipboard.length > 0;
        }
        return true;
      }).sort(function (a, b) { return (a.order || 0) - (b.order || 0); });

      // 构建 HTML
      var html = '';
      filtered.forEach(function (item) {
        if (item.separator_before) html += '<div class="ctx-sep"></div>';
        var cls = 'ctx-item';
        if (item.danger) cls += ' ctx-danger';
        if (item.submenu) cls += ' ctx-has-sub';
        var label = item.label || '';
        // 动态标签替换：线宽显示当前值
        if (item.action === 'editLineWidth' && ctxParams && ctxParams.size !== undefined) {
          label = '线条宽度 (' + ctxParams.size + ')';
        }
        html += '<div class="' + cls + '" data-action="' + item.action + '">' + (item.icon || '') + ' ' + label;
        if (item.submenu) {
          html += ' ▸<div class="ctx-sub">';
          item.submenu.forEach(function (sub) {
            html += '<div class="ctx-item" data-action="' + sub.action + '">' + (sub.icon || '') + ' ' + sub.label + '</div>';
          });
          html += '</div>';
        }
        html += '</div>';
      });
      return html;
    }

    $('canvasWrapper').addEventListener('contextmenu', function (e) {
      e.preventDefault();

      // Record canvas coordinates
      var vpRect = canvas.viewportEl.getBoundingClientRect();
      _ctxCanvasX = (e.clientX - vpRect.left - canvas.transform.x) / canvas.transform.zoom;
      _ctxCanvasY = (e.clientY - vpRect.top - canvas.transform.y) / canvas.transform.zoom;

      // Resolve what was right-clicked
      var targetInfo = _resolveCtxTarget();
      _ctxTargetType = targetInfo.type;
      _ctxTargetId = targetInfo.id;
      _ctxIsRuntime = AppState.mode !== 'design';

      var isDesign = AppState.mode === 'design';
      var menuHtml = null;

      // 表驱动：尝试从 context_menu_config 构建菜单
      if (!targetInfo.type) {
        // 空白画布
        menuHtml = _buildCtxMenuFromConfig('canvas', null, isDesign);
      } else if (targetInfo.type === 'cond_edge' || targetInfo.type === 'uncond_edge') {
        // 边：从配置构建，传入当前线宽
        menuHtml = _buildCtxMenuFromConfig('edge', null, isDesign, targetInfo.params);
      } else if (targetInfo.type === 'label') {
        // 标签节点：传入 cell_type 以匹配 cell_types 过滤
        var labelCellType = targetInfo.node ? (targetInfo.node.dzh_cell_type || targetInfo.node.tdx_cell_type) : null;
        menuHtml = _buildCtxMenuFromConfig('label', labelCellType, isDesign);
      } else if (targetInfo.type === 'generic_node') {
        // 通用节点
        menuHtml = _buildCtxMenuFromConfig('node', null, isDesign);
      }

      if (menuHtml) {
        cm.innerHTML = menuHtml;
      } else {
        // 特殊节点（candidate/condition/state_pool）：保留原有逻辑
        var items = _buildContextMenu(targetInfo);
        cm.innerHTML = _buildMenu(items);
      }

      cm.classList.add('visible');
      cm.style.left = e.clientX + 'px';
      cm.style.top = e.clientY + 'px';
    });

    document.addEventListener('click', function (e) {
      // Don't close if clicking inside context menu or a dialog
      var target = e.target;
      var cmEl = target.closest('#contextMenu');
      var dlgEl = target.closest('.ctx-dialog');
      if (cmEl || dlgEl) return;
      cm.classList.remove('visible');
      _closeAllDialogs();
    });

    // ── Dialog helpers ──
    function _closeAllDialogs() {
      ['lineWidthDialog','descTextDialog','clearConfirmDialog','selectStockDialog','selectBlockDialog'].forEach(function(id) {
        var el = $(id);
        if (el) el.style.display = 'none';
      });
    }
    function _showDialog(dialogId) {
      _closeAllDialogs();
      cm.classList.remove('visible');
      var el = $(dialogId);
      if (el) {
        el.style.display = 'block';
        // Center on screen
        el.style.left = Math.max(10, (window.innerWidth - el.offsetWidth) / 2) + 'px';
        el.style.top = Math.max(10, (window.innerHeight - el.offsetHeight) / 2) + 'px';
      }
    }

    // ── Menu action dispatch (event delegation) ──
    cm.addEventListener('click', function (evt) {
      var item = evt.target.closest('.ctx-item');
      if (!item) return;
      var action = item.getAttribute('data-action');
      if (!action) return;
      _handleCtxAction(action);
      cm.classList.remove('visible');
    });

    function _handleCtxAction(action) {
      // 表驱动新增 action：setLineWidth:N 直接设置线宽
      if (action.indexOf('setLineWidth:') === 0) {
        var newSize = parseInt(action.split(':')[1]) || 1;
        var eid = _ctxTargetId;
        if (eid) {
          poolData.updateEdge(eid, { size: newSize });
          canvas.render(poolData.data);
          showToast('线条宽度: ' + newSize);
        }
        return;
      }
      switch (action) {
        // ── 现有操作 ──
        case 'addSource': case 'addCondition': case 'addStatePool': case 'addDiscard': case 'addLabel':
        case 'addContainer': case 'addColumn':
          _doAddNode(action); break;
        case 'properties':
          _doProperties(); break;
        case 'comprehensiveSettings':
          if (window.ComprehensiveSettings) window.ComprehensiveSettings.open();
          break;
        case 'copy': case 'cut': case 'paste': case 'delete': case 'bringToFront': case 'sendToBack':
          _doGenericAction(action); break;

        // ── 新增 TDX 操作 ──
        case 'editDescText':
          _openDescTextDialog(); break;
        case 'editText':
          // 标签节点编辑文本：复用说明文字对话框
          _openDescTextDialog(); break;
        case 'editLineWidth':
          _openLineWidthDialog(); break;
        case 'toggleDisabled':
          _toggleDisabled(); break;
        case 'toggleRowNum':
          _toggleRowNum(); break;
        case 'clearAllStocks':
          _showDialog('clearConfirmDialog'); break;
        case 'addStockToPool':
          _showDialog('selectStockDialog'); break;
        case 'allStockToBlock':
          _openSelectBlockDialog(); break;
        case 'addToFavorites':
          showToast('已将当前股票加入自选股'); break;
        case 'viewChart':
          showToast('查看K线走势图'); break;
        case 'deleteStockRow':
          showToast('已删除选中行'); break;
        case 'viewConditionStocks':
          _viewConditionStocks(); break;
        // ── 表驱动新增 action ──
        case 'selectAll':
          canvas.selectedNodeIds = (canvas._nodes || []).map(function (n) { return n.id; });
          canvas.selectedNodeId = null;
          canvas.selectedEdgeId = null;
          canvas._syncSelectionStyles();
          showToast('已全选 ' + canvas._nodes.length + ' 个节点');
          break;
        case 'clearAll':
          if (confirm('确定清空所有节点和连线吗？此操作不可撤销。')) {
            poolData._data.nodes = [];
            poolData._data.edges = [];
            poolData._notify();
            canvas.render(poolData.data);
            showToast('已清空');
          }
          break;
      }
    }

    // ── 操作实现 ──
    function _doAddNode(action) {
      var cellType = _getAddNodeCellTypeByAction(action);
      if (AppState.mode !== 'design') return;
      var node = poolData.addNode(cellType, { x: Math.max(0, _ctxCanvasX - 40), y: Math.max(0, _ctxCanvasY - 20) });
      if (node) {
        canvas.render(poolData.data);
        canvas.selectNode(node.id);
        propPanel.showForNode(node.id);
        $('statusText').textContent = '已创建: ' + (node.label || '节点');
      }
    }

    function _doProperties() {
      var nid = canvas.getSelectedNodeId();
      if (nid) { propPanel.showForNode(nid); }
    }

    function _doGenericAction(action) {
      var nid = canvas.getSelectedNodeId();
      var eid = canvas.getSelectedEdgeId();
      switch (action) {
        case 'copy':
          if (nid) { poolData.copyToClipboard(nid); showToast('已复制'); }
          break;
        case 'cut':
          if (nid) { poolData.cutToClipboard(nid); canvas.render(poolData.data); propPanel.showPlaceholder(); }
          break;
        case 'paste':
          poolData.pasteFromClipboard(); canvas.render(poolData.data);
          break;
        case 'delete':
          if (nid) { poolData.removeNode(nid); canvas.render(poolData.data); propPanel.showPlaceholder(); }
          else if (eid) { poolData.removeEdge(eid); canvas.render(poolData.data); propPanel.showPlaceholder(); }
          break;
        case 'bringToFront':
          if (nid) { poolData.bringToFront(nid); canvas.refresh(); }
          break;
        case 'sendToBack':
          if (nid) { poolData.sendToBack(nid); canvas.refresh(); }
          break;
      }
    }

    // ── 线条宽度对话框 ──
    function _openLineWidthDialog() {
      var eid = _ctxTargetId;
      var edge = poolData.getEdgeById(eid);
      if (!edge) return;
      var params = edge.params || {};
      var currentSize = parseInt(params.size) || 1;
      $('lineWidthInput').value = currentSize;
      _showDialog('lineWidthDialog');
      // Unbind old handlers
      var okBtn = $('lineWidthOk');
      var newOkBtn = okBtn.cloneNode(true);
      okBtn.parentNode.replaceChild(newOkBtn, okBtn);
      newOkBtn.addEventListener('click', function () {
        var newSize = parseInt($('lineWidthInput').value);
        if (isNaN(newSize) || newSize < 1) newSize = 1;
        if (newSize > 16) newSize = 16;
        poolData.updateEdge(eid, { size: newSize });
        canvas.render(poolData.data);
        _closeAllDialogs();
        showToast('线条宽度: ' + newSize);
      });
      $('lineWidthCancel').onclick = function () { _closeAllDialogs(); };
    }

    // ── 说明文字对话框 ──
    function _openDescTextDialog() {
      var nid = _ctxTargetId;
      var node = poolData.getNodeById(nid);
      if (!node) return;
      var params = node.params || {};
      var isDZH = !poolData._isTDX;

      // Hide/show color input based on DZH vs TDX
      var colorEl = $('descTextColorInput');
      var colorLabel = colorEl ? colorEl.previousElementSibling : null;
      if (isDZH) {
        if (colorEl) colorEl.style.display = 'none';
        if (colorLabel && colorLabel.tagName === 'LABEL') colorLabel.style.display = 'none';
      } else {
        if (colorEl) colorEl.style.display = '';
        if (colorLabel && colorLabel.tagName === 'LABEL') colorLabel.style.display = '';
      }

      // For DZH, prefer wizd; for TDX, use text
      var descValue = isDZH ? (params.wizd || params.text || '') : (params.text || '');
      $('descTextInput').value = descValue;
      // Convert numeric color to hex
      var clr = parseInt(params.clrtext) || 0;
      $('descTextColorInput').value = '#' + clr.toString(16).padStart(6, '0');
      _showDialog('descTextDialog');
      var okBtn = $('descTextOk');
      var newOkBtn = okBtn.cloneNode(true);
      okBtn.parentNode.replaceChild(newOkBtn, okBtn);
      newOkBtn.addEventListener('click', function () {
        var newText = $('descTextInput').value.trim();
        var colorHex = $('descTextColorInput').value;
        var colorInt = parseInt(colorHex.replace('#', ''), 16) || 0;
        var updateObj = { text: newText };
        if (!isDZH) updateObj.clrtext = colorInt;
        if (isDZH) updateObj.wizd = newText;
        poolData.updateNodeParams(nid, updateObj);
        // Sync label so canvas rendering picks up the change
        var targetNode = poolData.getNodeById(nid);
        if (targetNode) { targetNode.label = newText; }
        canvas.render(poolData.data);
        _closeAllDialogs();
        showToast('说明文字已更新');
      });
      $('descTextCancel').onclick = function () { _closeAllDialogs(); };
    }

    // ── DZH 运行时条件节点：查看通过条件的股票 ──
    function _viewConditionStocks() {
      var nid = _ctxTargetId;
      var node = poolData.getNodeById(nid);
      if (!node) return;
      var params = node.params || {};
      var stks = params.stks || [];
      if (stks.length === 0) {
        showToast('当前条件未筛选出任何股票');
        return;
      }
      var stockList = stks.join(', ');
      if (stockList.length > 200) {
        stockList = stockList.substring(0, 200) + '... (共' + stks.length + '只)';
      }
      alert('通过条件的股票 (' + stks.length + '只):\n\n' + stockList);
    }

    // ── 停止运算切换 ──
    function _toggleDisabled() {
      var nid = _ctxTargetId;
      var node = poolData.getNodeById(nid);
      if (!node) return;
      if (!node.params) node.params = {};
      var current = !!node.params.disabled;
      poolData.updateNodeParams(nid, { disabled: !current });
      canvas.render(poolData.data);
      showToast(current ? '已恢复运算' : '已停止运算');
    }

    // ── 行号切换 ──
    function _toggleRowNum() {
      var nid = _ctxTargetId;
      var node = poolData.getNodeById(nid);
      if (!node) return;
      if (!node.params) node.params = {};
      var current = !!node.params.show_row_num;
      poolData.updateNodeParams(nid, { show_row_num: !current });
      canvas.render(poolData.data);
      showToast(current ? '已隐藏行号' : '已显示行号');
    }

    // ── 清除确认对话框 ──
    (function _bindClearConfirm() {
      $('clearConfirmYes').addEventListener('click', function () {
        var nid = _ctxTargetId;
        var node = poolData.getNodeById(nid);
        if (node && node.params) {
          node.params.stks = [];
          node.params.stocks = [];
          node.params.tdx_stocks = [];
          poolData._notify();
          canvas.render(poolData.data);
          showToast('已清空状态池所有股票');
        }
        _closeAllDialogs();
      });
      $('clearConfirmNo').addEventListener('click', function () { _closeAllDialogs(); });
    })();

    // ── 选择板块对话框 ──
    function _openSelectBlockDialog() {
      var listEl = $('blockList');
      // 从 API 获取板块列表（替代硬编码测试数据）
      listEl.innerHTML = '<div style="padding:8px 12px;color:var(--text-muted);font-size:12px;">加载中...</div>';
      fetch('/api/meta/candidate-pool/blocks')
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          var blocks = [];
          if (resp && resp.success && resp.data && resp.data.blocks) {
            blocks = resp.data.blocks.map(function (b) { return b.block_name || b.name || b.block_code; }).filter(Boolean);
          }
          if (blocks.length === 0) {
            listEl.innerHTML = '<div style="padding:8px 12px;color:var(--text-muted);font-size:12px;">暂无板块</div>';
          } else {
            listEl.innerHTML = blocks.map(function(b) {
              return '<div class="ctx-block-item" data-name="' + b + '">' + b + '</div>';
            }).join('');
          }
        })
        .catch(function () {
          listEl.innerHTML = '<div style="padding:8px 12px;color:var(--text-muted);font-size:12px;">加载失败</div>';
        });
      _showDialog('selectBlockDialog');
      $('blockOk').onclick = function () {
        var selected = listEl.querySelector('.ctx-block-item.selected');
        if (selected) {
          showToast('已加入板块: ' + selected.getAttribute('data-name'));
        }
        _closeAllDialogs();
      };
      $('blockCancel').onclick = function () { _closeAllDialogs(); };
      // Block item selection
      listEl.onclick = function (e) {
        var item = e.target.closest('.ctx-block-item');
        if (!item) return;
        listEl.querySelectorAll('.ctx-block-item').forEach(function(el) { el.classList.remove('selected'); });
        item.classList.add('selected');
      };
    }
  }

  // ─── Panel Toggle ───────────────────────────────────────────────────────────

  function bindPanelToggle() {
    $('panelToggleClose').addEventListener('click', function () {
      var panel = $('panelRight');
      panel.classList.toggle('collapsed');
      this.textContent = panel.classList.contains('collapsed') ? '◀' : '▶';
    });
  }

  // ─── Replay Controls ────────────────────────────────────────────────────────

  function bindReplayControls() {
    $('replayBtnPlay').addEventListener('click', replayPlay);
    $('replayBtnPause').addEventListener('click', replayPause);
    $('replayBtnStep').addEventListener('click', replayStep);
    $('replayBtnClose').addEventListener('click', function () {
      setMode('design');
    });

    // Simulation panel controls — 严格判空，避免 ref 错误导致按钮点击失效
    var simBtnClose = $('simBtnClose');
    if (simBtnClose) simBtnClose.addEventListener('click', function () {
      stopSimAutoStep();
      setMode('design');
    });
    var simBtnStart = $('simBtnStart');
    if (simBtnStart) simBtnStart.addEventListener('click', function () {
      if (AppState.mode !== 'simulation') return;
      startSimAutoStep();
    });
    var simBtnPause = $('simBtnPause');
    if (simBtnPause) simBtnPause.addEventListener('click', function () {
      if (AppState.mode !== 'simulation') return;
      stopSimAutoStep();
    });
    var simBtnStep = $('simBtnStep');
    if (simBtnStep) simBtnStep.addEventListener('click', function () {
      if (AppState.mode !== 'simulation') return;
      stopSimAutoStep();
      runSimulationStep(_getSimDelta());
    });
    var simBtnReset = $('simBtnReset');
    if (simBtnReset) simBtnReset.addEventListener('click', function () {
      if (AppState.mode !== 'simulation') return;
      stopSimAutoStep();
      var oldSessionId = simSessionId;
      simSessionId = null;
      simStepCount = 0;
      AppState.resetSimulation();
      var stepCountEl = $('simulationStepCount');
      if (stepCountEl) stepCountEl.textContent = '步数: 0';
      var clockEl = $('simulationClock');
      if (clockEl) clockEl.textContent = '00:00:00';
      if (typeof window.clearEventPanel === 'function') window.clearEventPanel();
      function restartSim() {
        updateSimBtnState(false);
        startSimulationSession();
      }
      if (oldSessionId) {
        fetch('/api/sim/control', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: oldSessionId, action: 'stop' })
        }).then(restartSim).catch(restartSim);
      } else {
        restartSim();
      }
    });
    var simSpeedSlider = $('simSpeedSlider');
    if (simSpeedSlider) simSpeedSlider.addEventListener('input', function () {
      _simSpeed = parseFloat(this.value) || 1.0;
      var speedValEl = $('simSpeedValue');
      if (speedValEl) speedValEl.textContent = _simSpeed + 'x';

    });

    var _clearBtn = $('eventPanelClear');
    if (_clearBtn) _clearBtn.addEventListener('click', function () {
      if (typeof window.clearEventPanel === 'function') window.clearEventPanel();
    });

    var _toggleBtn = $('eventPanelToggle');
    if (_toggleBtn) _toggleBtn.addEventListener('click', function () {
      _eventPanelCollapsed = !_eventPanelCollapsed;
      var _panel = $('eventPanel');
      if (_panel) _panel.classList.toggle('collapsed', _eventPanelCollapsed);
      _toggleBtn.textContent = _eventPanelCollapsed ? '▴' : '▾';
    });

    $('replaySpeedSelect').addEventListener('change', function () {
      var val = this.value;
      replaySetSpeed(val === 'MAX' ? 'MAX' : parseInt(val));
    });

    $('replayPeriodSelect').addEventListener('change', function () {
      if (AppState.mode === 'replay') startReplaySession();
    });

    // Date Range 选择器：日期变更时重启回放会话
    $('replayStartDate').addEventListener('change', function () {
      if (AppState.mode === 'replay') startReplaySession();
    });
    $('replayEndDate').addEventListener('change', function () {
      if (AppState.mode === 'replay') startReplaySession();
    });

    // Progress Bar 拖拽跳转（支持点击和拖动）
    var _replayDragging = false;
    var _progressBar = $('replayProgressBar');

    function _replaySeek(clientX) {
      if (!replaySessionId) return;
      var rect = _progressBar.getBoundingClientRect();
      var pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
      fetch('/api/dzh/replay/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: replaySessionId, command: 'seek', progress: pct })
      });
      // 拖拽时即时更新进度条视觉反馈
      $('replayProgressFill').style.width = pct + '%';
    }

    _progressBar.addEventListener('mousedown', function (e) {
      _replayDragging = true;
      _replaySeek(e.clientX);
    });
    document.addEventListener('mousemove', function (e) {
      if (_replayDragging) _replaySeek(e.clientX);
    });
    document.addEventListener('mouseup', function () {
      _replayDragging = false;
    });
  }

  function startSimulationSession() {
    var rawPoolId = poolData._poolId;
    var config = poolData._data;
    var configId = config && config.id ? String(config.id) : '';
    var isBackendPoolId = rawPoolId && /^[0-9a-f]{8,}-/.test(rawPoolId);
    var poolId = isBackendPoolId ? rawPoolId : null;
    if (!poolId && !config) {
      config = createDefaultSimPool();
    }
    var poolName = (isBackendPoolId ? rawPoolId : null) || (config && config.name) || '';
    if (!poolName && poolData._isTDX && poolData._tdxFilename) {
      poolName = poolData._tdxFilename.replace(/\.xml$/i, '');
    }

    function createDefaultSimPool() {
      var defaultPool = {
        name: '仿真测试池',
        nodes: [
          { id: 'm_100', type: 'market_source', label: 'A股备选池', dzh_cell_type: 202, position: { x: 80, y: 150, width: 80, height: 60 }, params: { markets: ['fz_a'], reload_sec: 300, clr: '', name: 'A股' } }
        ],
        edges: [],
        pool_meta: { type: 'ss-pool', ver: '1.0', mode: '1', nextid: 101, backcolor: 16777216 },
        trades: [],
        opentrades: []
      };
      poolData.setData(defaultPool);
      return defaultPool;
    }
    simStepCount = 0;
    simSessionId = null;
    _simAutoStepping = false;
    _simSpeed = 1.0;
    AppState.resetSimulation();
    // 显式将按钮置为"已暂停"状态：步进/启动可点击，暂停隐藏。
    // 避免上一轮 auto-step 残留的 disabled 状态导致步进按钮持续禁用。
    updateSimBtnState(false);
    if (typeof window.clearEventPanel === 'function') window.clearEventPanel();
    var simSpeedValue = $('simSpeedValue');
    var simSpeedSlider = $('simSpeedSlider');
    if (simSpeedValue) simSpeedValue.textContent = '1x';
    if (simSpeedSlider) simSpeedSlider.value = 1;
    var clockEl = $('simulationClock');
    var stepCountEl = $('simulationStepCount');
    if (clockEl) clockEl.textContent = '00:00:00';
    if (stepCountEl) stepCountEl.textContent = '步数: 0';
    // Switch data source to mock, then start a new sim session
    fetch('/api/data_source/select/mock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    })
      .then(function (r) { return r.json(); })
      .then(function () {
        var body = poolId ? { pool_id: poolId, speed: _simSpeed } : { config: config, speed: _simSpeed };
        return fetch('/api/sim/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
      })
      .then(function (r) { return r.json(); })
      .then(function (result) {
        if (result.code === 0 && result.data && result.data.session_id) {
          simSessionId = result.data.session_id;
          window.simSessionId = simSessionId;
          if (typeof window.eventPanelSetSession === 'function') {
            window.eventPanelSetSession(simSessionId);
          }
          startSimAutoStep();
        } else {
          console.error('仿真启动失败:', result.msg);
          // 启动失败时恢复按钮到可步进状态，避免步进按钮持续禁用。
          updateSimBtnState(false);
        }
      })
      .catch(function (err) {
        console.error('仿真初始化失败:', err);
        // 网络异常时同样恢复按钮到可步进状态。
        updateSimBtnState(false);
      });
  }

  function _getSimDelta() {
    // 读取步长下拉框（1s/1min/5min/1h）；缺失或无效时回退到 1 秒。
    var sel = $('simDeltaSelect');
    if (sel) {
      var v = parseInt(sel.value, 10);
      if (!isNaN(v) && v > 0) return v;
    }
    return 1;
  }

  function runSimulationStep(delta) {
    if (!simSessionId) {
      console.error('仿真会话未启动，自动重新创建会话');
      startSimulationSession();
      return Promise.resolve();
    }
    var stepDelta = delta || _getSimDelta();
    return fetch('/api/sim/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: simSessionId, action: 'step', params: { delta: stepDelta } })
    })
      .then(function (r) { return r.json(); })
      .then(function (result) {
        if (result.code === 0 && result.data) {
          var clock = result.data.clock;
          if (clock != null) {
            var clockMs = Math.floor(clock * 1000);
            if (clockMs > 0) {
              // 仿真时间由后端 /api/state/runtime 提供，前端不再自行维护真值源
              simStepCount++;
              var clockEl2 = $('simulationClock');
              var stepCountEl2 = $('simulationStepCount');
              var sec = Math.floor(clock);
              var hh = String(Math.floor(sec / 3600) % 24).padStart(2, '0');
              var mm = String(Math.floor(sec / 60) % 60).padStart(2, '0');
              var ss = String(sec % 60).padStart(2, '0');
              if (clockEl2) clockEl2.textContent = hh + ':' + mm + ':' + ss;
              if (stepCountEl2) stepCountEl2.textContent = '步数: ' + simStepCount;
              window.simStepCount = simStepCount;
              // 仿真步进产生的事件统一由后端 SSE 推送到事件面板，前端不再直接注入
              refreshSimNodeStocks();
            }
          }
        } else if (result.code === 102) {
          console.log('[Sim] 初始化中，稍后重试:', result.msg);
        } else if (result.msg === '会话不存在') {
          // 会话在后端已失效（如服务器重启），清除本地 stale session_id 并重新创建会话
          console.warn('[Sim] 会话不存在，重新创建仿真会话');
          simSessionId = null;
          window.simSessionId = null;
          startSimulationSession();
        } else {
          console.error('仿真步进失败:', result.msg || result.error);
        }
      })
      .catch(function (err) {
        console.error('仿真步进请求失败:', err);
      });
  }

  function refreshSimNodeStocks() {
    if (!simSessionId) return;
    fetch('/api/sim/state?session_id=' + encodeURIComponent(simSessionId))
      .then(function (r) { return r.json(); })
      .then(function (result) {
        if (result.code !== 0 || !result.data) return;
        // V5 Task 6: 前端 API 调用必须使用 StatePoolView 视图接口（result.data.pools），
        // 严禁直接操作 node_stocks 旧扁平接口。删除 || result.data.node_stocks 兼容回退。
        var pools = result.data.pools || {};
        var execResult = {};
        Object.keys(pools).forEach(function (cellId) {
          var info = pools[cellId];
          var stocks = info.stocks || [];
          execResult[cellId] = { count: info.count || stocks.length, stocks: stocks };
        });
        syncExecResult(execResult);
        canvas.refreshStockTables();
      })
      .catch(function () {});
  }

  function updateSimBtnState(isAutoStepping) {
    _simAutoStepping = isAutoStepping;
    AppState.setSimulationState(isAutoStepping ? 'running' : 'paused');
    var startBtn = $('simBtnStart');
    var pauseBtn = $('simBtnPause');
    var stepBtn = $('simBtnStep');
    if (startBtn) {
      startBtn.style.display = isAutoStepping ? 'none' : '';
      startBtn.disabled = isAutoStepping;
    }
    if (pauseBtn) {
      pauseBtn.style.display = isAutoStepping ? '' : 'none';
      pauseBtn.disabled = !isAutoStepping;
    }
    if (stepBtn) stepBtn.disabled = isAutoStepping;
    // 同步主工具栏运行控制按钮状态（仿真模式下 btnPause/btnStart 由仿真状态决定）
    updateRunControlButtons();
  }

  function startSimAutoStep() {
    if (_simAutoStepping) return;
    updateSimBtnState(true);
    _simAutoTick();
  }

  function _simAutoTick() {
    if (!_simAutoStepping || AppState.mode !== 'simulation') {
      updateSimBtnState(false);
      return;
    }
    runSimulationStep(_getSimDelta()).then(function() {
      if (!_simAutoStepping || AppState.mode !== 'simulation') {
        updateSimBtnState(false);
        return;
      }
      var interval = Math.max(50, Math.round(1000 / _simSpeed));
      _simAutoInterval = setTimeout(_simAutoTick, interval);
    }).catch(function() {
      if (_simAutoStepping && AppState.mode === 'simulation') {
        _simAutoInterval = setTimeout(_simAutoTick, 1000);
      }
    });
  }

  function stopSimAutoStep() {
    _simAutoStepping = false;
    if (_simAutoInterval) {
      clearTimeout(_simAutoInterval);
      _simAutoInterval = null;
    }
    AppState.setSimulationState('paused');
    updateSimBtnState(false);
  }

  function stopSimulationPolling() {
    if (simPollingInterval) {
      clearInterval(simPollingInterval);
      simPollingInterval = null;
    }
  }

  var _eventPanelCollapsed = false;

  function startReplaySession() {
    var period = $('replayPeriodSelect').value;
    var poolId = poolData._poolId;
    var replayDefaults = getConfig('defaults').replay || _configDefaults.defaults.replay;
    // 优先从日期选择器读取，回退到配置默认值
    var startDate = $('replayStartDate').value || replayDefaults.start_date;
    var endDate = $('replayEndDate').value || replayDefaults.end_date;
    var body = {
      pool_id: poolId || null,
      period: period,
      start_date: startDate,
      end_date: endDate
    };
    // For TDX XML pools loaded from file, pass filename
    if (!poolId && poolData._isTDX && poolData._tdxFilename) {
      body.filename = poolData._tdxFilename;
      body.pool_data = { name: poolData._data?.name || '', nodes: poolData._data?.nodes || [], edges: poolData._data?.edges || [], pool_meta: poolData._data?.pool_meta || {} };
    }
    fetch('/api/dzh/replay/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.code === 0 && data.data) {
          replaySessionId = data.data.session_id;
          var p = data.data.progress_summary || {};
          updateReplayProgress({
            current_time: p.current_time || '',
            current_index: p.current_index || -1,
            total_bars: data.data.total_bars || 0,
            progress: p.progress || 0,
            playing: false,
            paused: true,
            speed: data.data.speed || 1
          });
        }
      });
  }

  function replayPlay() {
    if (!replaySessionId) return;
    fetch('/api/dzh/replay/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: replaySessionId, command: 'play' })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.code === 0) {
          $('replayBtnPlay').style.display = 'none';
          $('replayBtnPause').style.display = '';
          if (data.data && data.data.progress) updateReplayProgress(data.data.progress);
          startReplayPolling();
          if (highlightManager) highlightManager.resumeAutoHide();
        }
      });
  }

  function replayPause() {
    if (!replaySessionId) return;
    fetch('/api/dzh/replay/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: replaySessionId, command: 'pause' })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.code === 0) {
          $('replayBtnPlay').style.display = '';
          $('replayBtnPause').style.display = 'none';
          stopReplayPolling();
          if (highlightManager) highlightManager.pauseAutoHide();
        }
      });
  }

  function replayStep() {
    if (!replaySessionId) return;
    fetch('/api/dzh/replay/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: replaySessionId, command: 'step' })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.code === 0 && data.data && data.data.progress) {
          updateReplayProgress(data.data.progress);
        }
      });
  }

  function replaySetSpeed(speed) {
    if (!replaySessionId) return;
    fetch('/api/dzh/replay/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: replaySessionId, command: 'speed', speed: speed })
    });
  }

  function startReplayPolling() {
    stopReplayPolling();
    replayPollingInterval = setInterval(function () {
      fetch('/api/dzh/replay/status')
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.code === 0 && data.data) {
            updateReplayProgress(data.data);
            if (!data.data.playing) {
              $('replayBtnPlay').style.display = '';
              $('replayBtnPause').style.display = 'none';
            }
          }
        });
    }, 300);
  }

  function stopReplayPolling() {
    if (replayPollingInterval) {
      clearInterval(replayPollingInterval);
      replayPollingInterval = null;
    }
  }

  function startStockPolling() {
    stopStockPolling();
    stockPollingInterval = setInterval(function () {
      canvas.refreshStockTables();
    }, 5000);
  }

  function stopStockPolling() {
    if (stockPollingInterval) {
      clearInterval(stockPollingInterval);
      stockPollingInterval = null;
    }
  }

  function updateReplayProgress(data) {
    currentReplayTime = data.current_time || '';
    $('replayTime').textContent = currentReplayTime || '--';
    var idx = (data.current_index !== undefined && data.current_index !== null) ? data.current_index : -1;
    $('replayBarIndex').textContent = (idx + 1) + '/' + (data.total_bars || 0);
    var pct = data.progress || 0;
    $('replayProgressFill').style.width = pct + '%';
  }

  // ─── Mobile Tab Switching ───────────────────────────────────────────────────

  function bindMobileTabs() {
    $('mobileTabCanvas').addEventListener('click', function () {
      $('panelRight').classList.remove('tab-active');
      this.classList.add('active');
      $('mobileTabProperties').classList.remove('active');
    });
    $('mobileTabProperties').addEventListener('click', function () {
      $('panelRight').classList.add('tab-active');
      this.classList.add('active');
      $('mobileTabCanvas').classList.remove('active');
    });

    // Mobile float buttons
    var mobileAddSource = $('mobileAddSource');
    var mobileAddCondition = $('mobileAddCondition');
    var mobileAddPool = $('mobileAddPool');
    if (mobileAddSource) {
      mobileAddSource.addEventListener('click', function () {
        addNodeAtCenter(poolData._isTDX ? 7 : 202);
      });
    }
    if (mobileAddCondition) {
      mobileAddCondition.addEventListener('click', function () {
        addNodeAtCenter(poolData._isTDX ? 3 : 201);
      });
    }
    if (mobileAddPool) {
      mobileAddPool.addEventListener('click', function () {
        addNodeAtCenter(poolData._isTDX ? 8 : 200);
      });
    }
  }

  function addNodeAtCenter(cellType) {
    if (AppState.mode !== 'design') return;
    var vpRect = canvas.viewportEl.getBoundingClientRect();
    var cx = (vpRect.width / 2 - canvas.transform.x) / canvas.transform.zoom;
    var cy = (vpRect.height / 2 - canvas.transform.y) / canvas.transform.zoom;
    var node = poolData.addNode(cellType, { x: Math.max(0, cx - 40), y: Math.max(0, cy - 20) });
    if (node) {
      canvas.render(poolData.data);
      canvas.selectNode(node.id);
      propPanel.showForNode(node.id);
      $('statusText').textContent = '已创建: ' + (node.label || '节点');
    }
  }

  // ─── Status Bar ─────────────────────────────────────────────────────────────

  function getModeLabel() {
    switch (AppState.mode) {
      case 'design': return '🎨 设计';
      case 'run': return '▶ 实盘';
      case 'replay': return '⏪ 回放';
      case 'simulation': return '🔬 仿真';
      default: return AppState.mode;
    }
  }

  function updateStatusBar() {
    if (!poolData.data) return;
    $('statusNodes').textContent = '节点: ' + poolData.getNodeCount();
    $('statusEdges').textContent = '连线: ' + poolData.getEdgeCount();
    $('statusZoom').textContent = Math.round(canvas.transform.zoom * 100) + '%';
  }

  function updateStatusBarTime() {
    var timeEl = $('statusTime');
    if (!timeEl) return;

    var parts = [getModeLabel()];

    var runtimeNow = window.RuntimeState ? window.RuntimeState.displayNowMs : Date.now();
    if (AppState.mode === 'simulation' && runtimeNow > 0) {
      var simSec = Math.floor(runtimeNow / 1000);
      var hh = String(Math.floor(simSec / 3600) % 24).padStart(2, '0');
      var mm = String(Math.floor(simSec / 60) % 60).padStart(2, '0');
      var ss = String(simSec % 60).padStart(2, '0');
      parts.push('⏱ ' + hh + ':' + mm + ':' + ss);
      if (typeof simStepCount !== 'undefined' && simStepCount > 0) {
        parts.push('步:' + simStepCount);
      }
    } else {
      var now = new Date();
      parts.push(now.getHours().toString().padStart(2, '0') + ':' +
        now.getMinutes().toString().padStart(2, '0') + ':' +
        now.getSeconds().toString().padStart(2, '0'));
    }

    if (typeof window.getEventCount === 'function') {
      var evtCount = window.getEventCount();
      if (evtCount > 0) parts.push('事件:' + (evtCount > 999 ? '999+' : evtCount));
    }

    timeEl.textContent = parts.join(' | ');
  }

  function updateToolbarButtons() {
    // 表驱动：委托给 ToolbarRenderer 按表更新按钮状态
    if (typeof ToolbarRenderer !== 'undefined' && ToolbarRenderer.getConfigs().toolbar) {
      ToolbarRenderer.updateButtonStates(getUIState());
      return;
    }
    // fallback：ToolbarRenderer 未加载时使用原逻辑
    var cfg = getConfig('toolbar_config');
    var buttons = cfg.buttons || [];
    if (buttons.length) {
      buttons.forEach(function (btn) {
        var el = $(btn.id);
        if (!el || !btn.enabled_when) return;
        el.disabled = !_evalEnabledWhen(btn.enabled_when);
      });
    } else {
      $('btnUndo').disabled = !poolData.canUndo();
      $('btnRedo').disabled = !poolData.canRedo();
      $('btnSave').disabled = !poolData.hasData;
      $('btnExport').disabled = !poolData.hasData;
      if ($('btnComprehensiveSettings')) $('btnComprehensiveSettings').disabled = !poolData.hasData;
    }
  }

  function startCoordinateTracking() {
    document.addEventListener('mousemove', function (e) {
      mouseX = e.clientX;
      mouseY = e.clientY;
    });

    setInterval(function () {
      if (!canvas || !canvas.viewportEl) return;
      if (document.activeElement && (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA')) return;
      var vpRect = canvas.viewportEl.getBoundingClientRect();
      var worldX = (mouseX - vpRect.left - canvas.transform.x) / canvas.transform.zoom;
      var worldY = (mouseY - vpRect.top - canvas.transform.y) / canvas.transform.zoom;
      $('statusCoords').textContent = 'X:' + Math.round(worldX) + ' Y:' + Math.round(worldY);
    }, 200);
  }

  function startStatusBarClock() {
    updateStatusBarTime();
    setInterval(updateStatusBarTime, 1000);

    if (window.AppState && typeof window.AppState.subscribe === 'function') {
      window.AppState.subscribe(function (key) {
        if (key === 'mode' || key === 'simulationReset' || key === 'simulationState') {
          updateStatusBarTime();
        }
      });
    }
    if (window.RuntimeState && typeof window.RuntimeState.subscribe === 'function') {
      window.RuntimeState.subscribe(function (key) {
        if (key === 'runtimeState') updateStatusBarTime();
      });
    }
  }

  // ─── Toast Notifications ────────────────────────────────────────────────────

  function showToast(msg, type) {
    var t = document.createElement('div');
    t.className = 'toast toast-' + (type || 'success');
    t.textContent = msg;
    t.style.cssText = 'position:fixed;top:60px;right:20px;z-index:9999;padding:10px 20px;border-radius:6px;font-size:13px;color:#fff;pointer-events:none;opacity:0.95;transition:opacity 0.3s;' +
      'background:' + (type === 'error' ? '#e74c3c' : type === 'warn' ? '#e67e22' : '#27ae60') + ';';
    document.body.appendChild(t);
    setTimeout(function () { t.style.opacity = '0'; }, 2500);
    setTimeout(function () { t.remove(); }, 3000);
  }

  // ─── Stock Modal ────────────────────────────────────────────────────────────

  function showStockModal(nodeId) {
    var node = poolData.getNodeById(nodeId);
    if (!node) return;

    var stocks = node.params.stocks || node.params.tdx_stocks || [];
    var isTDX = node.type === 'tdx_state_pool' || node.type === 'tdx_candidate' ||
      (stocks.length > 0 && stocks[0].setcode !== undefined);

    if (isTDX) {
      showTDXStockModal(node.label || '股票列表', stocks);
      return;
    }

    var existing = document.querySelector('.stock-modal-overlay');
    if (existing) existing.remove();

    var overlay = document.createElement('div');
    overlay.className = 'stock-modal-overlay';

    var modal = document.createElement('div');
    modal.className = 'stock-modal';

    var header = document.createElement('div');
    header.className = 'stock-modal-header';
    var title = document.createElement('h3');
    title.textContent = (node.label || '状态池') + ' — 股票列表';
    var closeBtn = document.createElement('button');
    closeBtn.className = 'stock-modal-close';
    closeBtn.textContent = '×';
    closeBtn.onclick = function () { overlay.remove(); };
    header.appendChild(title);
    header.appendChild(closeBtn);
    modal.appendChild(header);

    var body = document.createElement('div');
    body.className = 'stock-modal-body';
    body.innerHTML = '<div class="stock-modal-loading">加载中...</div>';
    modal.appendChild(body);

    overlay.appendChild(modal);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);

    var url = '/api/dzh/cells/' + encodeURIComponent(nodeId) + '/stocks?mode=mock';
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (result) {
        if (!result.success && result.code !== 0) {
          body.innerHTML = '<div class="stock-modal-empty">获取数据失败: ' + (result.error || result.msg || '未知错误') + '</div>';
          return;
        }
        var stockData = result.data || result.stocks || [];
        if (stockData.length === 0) {
          body.innerHTML = '<div class="stock-modal-empty">暂无股票数据</div>';
          return;
        }
        renderStockTable(stockData, result.columns, body);
      })
      .catch(function (err) {
        body.innerHTML = '<div class="stock-modal-empty">请求失败: ' + err.message + '</div>';
      });
  }

  function showTDXStockModal(title, stocks) {
    var existing = document.querySelector('.stock-modal-overlay');
    if (existing) existing.remove();

    var overlay = document.createElement('div');
    overlay.className = 'stock-modal-overlay';

    var modal = document.createElement('div');
    modal.className = 'stock-modal';

    var header = document.createElement('div');
    header.className = 'stock-modal-header';
    var h3 = document.createElement('h3');
    h3.textContent = title + ' — TDX股票列表';
    var closeBtn = document.createElement('button');
    closeBtn.className = 'stock-modal-close';
    closeBtn.textContent = '×';
    closeBtn.onclick = function () { overlay.remove(); };
    header.appendChild(h3);
    header.appendChild(closeBtn);
    modal.appendChild(header);

    var body = document.createElement('div');
    body.className = 'stock-modal-body';

    var html = '<table class="stock-modal-table tdx-stock-table"><thead><tr>';
    html += '<th>代码</th><th>进入日期</th><th>进入时间</th><th>进入价格</th><th>收益</th><th>当前价</th><th>涨幅%</th><th>成交量</th><th>最高涨幅</th><th>最高周期</th><th>最高时间</th><th>最高价</th><th>持仓天数</th>';
    html += '</tr></thead><tbody>';

    stocks.forEach(function (stk) {
      var prefix = '0';
      if (stk.setcode == 1) prefix = '6';
      else if (stk.setcode == 2) prefix = '8';
      var code = prefix + (stk.code || stk.label || '');
      while (code.length < 6) code = code.substring(0, 1) + '0' + code.substring(1);

      var riseClass = '';
      if (stk.rise > 0) riseClass = 'stock-rise';
      else if (stk.rise < 0) riseClass = 'stock-fall';

      html += '<tr>';
      html += '<td class="stock-code" data-stock-code="' + code + '">' + code + '</td>';
      html += '<td>' + (stk.indate || '-') + '</td>';
      html += '<td>' + (stk.intime || '-') + '</td>';
      html += '<td>' + (stk.inprice != null ? stk.inprice : '-') + '</td>';
      html += '<td>' + (stk.income != null ? stk.income : '-') + '</td>';
      html += '<td>' + (stk.now != null ? stk.now : '-') + '</td>';
      html += '<td class="' + riseClass + '">' + (stk.rise != null ? stk.rise : '-') + '</td>';
      html += '<td>' + (stk.volume != null ? stk.volume : '-') + '</td>';
      html += '<td>' + (stk.maxrate != null ? stk.maxrate : '-') + '</td>';
      html += '<td>' + (stk.maxperiod != null ? stk.maxperiod : '-') + '</td>';
      html += '<td>' + (stk.maxtime != null ? stk.maxtime : '-') + '</td>';
      html += '<td>' + (stk.maxprice != null ? stk.maxprice : '-') + '</td>';
      html += '<td>' + (stk.idaynum != null ? stk.idaynum : '-') + '</td>';
      html += '</tr>';
    });

    html += '</tbody></table>';
    html += '<div style="padding:8px 16px;color:#888;font-size:11px;">共 ' + stocks.length + ' 只</div>';

    body.innerHTML = html;
    bindStockCodeDblClickInModal(body);
    modal.appendChild(body);
    overlay.appendChild(modal);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  }

  function bindStockCodeDblClickInModal(container) {
    var codeCells = container.querySelectorAll('td[data-stock-code]');
    for (var i = 0; i < codeCells.length; i++) {
      codeCells[i].addEventListener('dblclick', function (e) {
        var stockCode = e.target.getAttribute('data-stock-code');
        if (stockCode && canvas) canvas._showKlineModal(stockCode);
      });
    }
  }

  function renderStockTable(data, columns, container) {
    var sortCol = null;
    var sortAsc = true;
    var colorKeys = { 'change_pct': true, 'change_amt': true, 'profit_pct': true, 'max_profit': true };

    function buildTable() {
      var sorted = data.slice();
      if (sortCol !== null) {
        sorted.sort(function (a, b) {
          var va = a[sortCol], vb = b[sortCol];
          if (va === '-' || va === undefined) va = sortAsc ? Infinity : -Infinity;
          if (vb === '-' || vb === undefined) vb = sortAsc ? Infinity : -Infinity;
          if (typeof va === 'number' && typeof vb === 'number') return sortAsc ? va - vb : vb - va;
          va = String(va); vb = String(vb);
          return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        });
      }

      var html = '<table class="stock-modal-table"><thead><tr>';
      (columns || []).forEach(function (col) {
        var arrow = '';
        if (sortCol === col.key) {
          arrow = '<span class="sort-arrow active">' + (sortAsc ? '▲' : '▼') + '</span>';
        } else {
          arrow = '<span class="sort-arrow">▲</span>';
        }
        html += '<th class="sortable" data-key="' + col.key + '">' + col.name + arrow + '</th>';
      });
      html += '</tr></thead><tbody>';

      sorted.forEach(function (row) {
        html += '<tr>';
        (columns || []).forEach(function (col) {
          var val = row[col.key];
          if (val === undefined || val === null) val = '-';
          var cls = '';
          if (col.type === 'number') cls = 'stock-modal-cell-num';
          if (colorKeys[col.key] && typeof val === 'number') {
            if (val > 0) cls += ' stock-modal-cell-up';
            else if (val < 0) cls += ' stock-modal-cell-down';
          }
          if (typeof val === 'number') {
            if (col.key === 'volume') val = val >= 10000 ? (val / 10000).toFixed(0) + '万' : val;
            else if (!Number.isInteger(val)) val = val.toFixed(2);
          }
          var codeAttr = (col.key === 'code') ? ' data-stock-code="' + val + '"' : '';
          if (col.key === 'code') cls += ' stock-code';
          html += '<td class="' + cls + '"' + codeAttr + '>' + val + '</td>';
        });
        html += '</tr>';
      });

      html += '</tbody></table>';
      container.innerHTML = html;

      container.querySelectorAll('th.sortable').forEach(function (th) {
        th.addEventListener('click', function () {
          var key = th.getAttribute('data-key');
          if (sortCol === key) sortAsc = !sortAsc;
          else { sortCol = key; sortAsc = true; }
          buildTable();
        });
      });

      bindStockCodeDblClickInModal(container);
    }

    buildTable();
  }

  // ─── Column Editor ──────────────────────────────────────────────────────────

  function showColumnEditor(nodeId) {
    var node = poolData.getNodeById(nodeId);
    if (!node) return;
    var existing = document.querySelector('.col-editor-overlay');
    if (existing) existing.remove();

    var params = node.params || {};
    var currentColList = params.col_list || '2,-1,-2,-3,7,14,8,10,17,45';
    if (Array.isArray(currentColList)) currentColList = currentColList.join(',');
    var selectedCols = currentColList.split(',').map(function (c) { return parseInt(c.trim()); }).filter(function (n) { return !isNaN(n); });

    var overlay = document.createElement('div');
    overlay.className = 'col-editor-overlay';

    var panel = document.createElement('div');
    panel.className = 'col-editor-panel';

    var header = document.createElement('div');
    header.className = 'col-editor-header';
    var title = document.createElement('h3');
    title.textContent = '编辑显示列 — ' + (node.label || '状态池');
    var closeBtn = document.createElement('button');
    closeBtn.className = 'stock-modal-close';
    closeBtn.textContent = '×';
    closeBtn.onclick = function () { overlay.remove(); };
    header.appendChild(title);
    header.appendChild(closeBtn);
    panel.appendChild(header);

    var body = document.createElement('div');
    body.className = 'col-editor-body';

    var availableDiv = document.createElement('div');
    availableDiv.className = 'col-editor-available';
    var selectedDiv = document.createElement('div');
    selectedDiv.className = 'col-editor-selected';

    body.appendChild(availableDiv);
    body.appendChild(selectedDiv);
    panel.appendChild(body);

    var footer = document.createElement('div');
    footer.className = 'col-editor-footer';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn-cancel';
    cancelBtn.textContent = '取消';
    cancelBtn.onclick = function () { overlay.remove(); };
    var okBtn = document.createElement('button');
    okBtn.className = 'btn-ok';
    okBtn.textContent = '确定';
    footer.appendChild(cancelBtn);
    footer.appendChild(okBtn);
    panel.appendChild(footer);

    overlay.appendChild(panel);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);

    var allCols = [];
    var colMap = {};

    function renderLists() {
      availableDiv.innerHTML = '';
      var availTitleEl = document.createElement('div');
      availTitleEl.className = 'col-section-title';
      availTitleEl.textContent = '可用列';
      availableDiv.appendChild(availTitleEl);

      var selectedSet = {};
      selectedCols.forEach(function (id) { selectedSet[id] = true; });

      allCols.forEach(function (col) {
        if (selectedSet[col.id]) return;
        var item = document.createElement('div');
        item.className = 'col-editor-item available';
        item.textContent = col.id + ': ' + col.name;
        item.addEventListener('click', function () {
          selectedCols.push(col.id);
          renderLists();
        });
        availableDiv.appendChild(item);
      });

      selectedDiv.innerHTML = '';
      var selTitleEl = document.createElement('div');
      selTitleEl.className = 'col-section-title';
      selTitleEl.textContent = '已选列 (拖拽排序, 点击移除)';
      selectedDiv.appendChild(selTitleEl);

      selectedCols.forEach(function (colId, idx) {
        var colInfo = colMap[colId];
        var item = document.createElement('div');
        item.className = 'col-editor-item selected';
        item.textContent = (colInfo ? colInfo.name : '列#' + colId) + ' (' + colId + ')';
        item.draggable = true;

        item.addEventListener('click', function () {
          selectedCols.splice(idx, 1);
          renderLists();
        });
        item.addEventListener('dragstart', function (e) {
          e.dataTransfer.setData('text/plain', String(idx));
          item.classList.add('dragging');
        });
        item.addEventListener('dragend', function () { item.classList.remove('dragging'); });
        item.addEventListener('dragover', function (e) { e.preventDefault(); });
        item.addEventListener('drop', function (e) {
          e.preventDefault();
          var fromIdx = parseInt(e.dataTransfer.getData('text/plain'));
          if (isNaN(fromIdx) || fromIdx === idx) return;
          var moved = selectedCols.splice(fromIdx, 1)[0];
          selectedCols.splice(idx, 0, moved);
          renderLists();
        });

        selectedDiv.appendChild(item);
      });
    }

    okBtn.addEventListener('click', function () {
      var newColList = selectedCols.join(',');
      poolData.updateNodeParams(nodeId, { col_list: newColList });
      overlay.remove();
    });

    fetch('/api/dzh/col-definitions')
      .then(function (r) { return r.json(); })
      .then(function (result) {
        if (result.success && result.data) {
          allCols = result.data;
          allCols.forEach(function (col) { colMap[col.id] = col; });
        }
        renderLists();
      })
      .catch(function () { renderLists(); });
  }

  // ─── Boot ───────────────────────────────────────────────────────────────────
  init();
})();

})();
