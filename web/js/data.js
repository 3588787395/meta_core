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

export class PoolDataManager {
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
    });

    (this._data.edges || []).forEach(function(e) {
      if (!e.params) e.params = {};
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
