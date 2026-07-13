/**
 * canvas.js - 画布引擎模块合并文件 [v=80 edgeLineType+orthogonal]
 * ============
 * 此文件由以下两个模块合并而成：
 *   - DZH 颜色转换工具（来源: utils/color.js）
 *   - FlowCanvas: xyflow-style flow canvas engine（原 canvas.js）
 *
 * 全局导出（颜色工具）：
 *   - window.dzhColorToCss
 *   - window.dzhIntToCssHex
 *   - window.DZH_COLOR_UTILS
 *
 * ES Module 导出：
 *   - FlowCanvas
 */

// ============================================================================
// ===== 来源: utils/color.js =====
// ============================================================================

// DZH 颜色转换工具 - 统一实现
// 消除 canvas.js、comprehensive-settings.js、table-driven-panel.js 中的重复代码
// 以 canvas.js 中的增强版实现为准（支持元信息返回模式）

var DZH_COLOR_UTILS = (function() {
  // 20色调色板中英文名称表（与 Python 端 dzh_constants.DZH_PALETTE 完全一致）
  var DZH_PALETTE_NAMES = {
    0x00: {name:'黑色', en:'Black'},
    0x01: {name:'深红', en:'DarkRed'},
    0x02: {name:'深绿', en:'DarkGreen'},
    0x03: {name:'橄榄', en:'Olive'},
    0x04: {name:'深蓝', en:'Navy'},
    0x05: {name:'紫色', en:'Purple'},
    0x06: {name:'青色', en:'Teal'},
    0x07: {name:'灰色', en:'Gray'},
    0x08: {name:'浅绿灰', en:'PaleGreen'},
    0x09: {name:'浅蓝灰', en:'LightBlue'},
    0x0A: {name:'米白', en:'AntiqueWhite'},
    0x0B: {name:'银灰', en:'Silver'},
    0x0C: {name:'深灰', en:'DarkGray'},
    0x0D: {name:'红色', en:'Red'},
    0x0E: {name:'亮绿', en:'Lime'},
    0x0F: {name:'黄色', en:'Yellow'},
    0x10: {name:'蓝色', en:'Blue'},
    0x11: {name:'品红', en:'Magenta'},
    0x12: {name:'青(Cyan)', en:'Cyan'},
    0x13: {name:'白色', en:'White'}
  };

  // DZH 默认颜色调色板（20色标准）
  var PALETTE = {
    0x00: '#000000',
    0x01: '#800000',
    0x02: '#008000',
    0x03: '#808000',
    0x04: '#000080',
    0x05: '#800080',
    0x06: '#008080',
    0x07: '#c0c0c0',
    0x08: '#c0dcc0',
    0x09: '#a6caf0',
    0x0A: '#F9F7F0',
    0x0B: '#a0a0a4',
    0x0C: '#808080',
    0x0D: '#ff0000',
    0x0E: '#00ff00',
    0x0F: '#ffff00',
    0x10: '#0000ff',
    0x11: '#ff00ff',
    0x12: '#00ffff',
    0x13: '#ffffff'
  };

  /**
   * DZH颜色值转CSS — 支持元信息返回模式
   *
   * @param {string|number} clrStr - DZH颜色值（调色板索引整数或BGR直接色）
   * @param {string} [fallback] - 回退CSS值，默认 'transparent'
   * @param {boolean} [returnMeta=false] - 为true时返回结构化元信息对象而非纯字符串
   * @returns {string|object} 默认返回CSS字符串；returnMeta=true时返回包含9个字段的对象
   *
   * 向后兼容：所有现有调用 dzhColorToCss(value, fallback) 无需修改
   */
  function dzhColorToCss(clrStr, fallback, returnMeta) {
    if (clrStr === undefined || clrStr === null || clrStr === '' || clrStr === '-1') {
      var fb = fallback || 'transparent';
      if (returnMeta) {
        return { css: fb, hex: fb, name: '透明/继承', en: '', type: 'special', index: null, rgb: null, raw: -1 };
      }
      return fb;
    }
    var val = parseInt(clrStr);
    if (isNaN(val) || val === -1) {
      var fb2 = fallback || 'transparent';
      if (returnMeta) {
        return { css: fb2, hex: fb2, name: '透明/继承', en: '', type: 'special', index: null, rgb: null, raw: val };
      }
      return fb2;
    }
    if (val < 0) val += 0x100000000;

    // DZH调色板索引: 高位字节0x01表示调色板索引
    if ((val & 0xFF000000) === 0x01000000) {
      var idx = val & 0xFF;
      var cssVal = PALETTE[idx];
      if (cssVal !== undefined) {
        if (returnMeta) {
          // hex转RGB数组
          var ph = cssVal.replace('#', '');
          var pr = parseInt(ph.substr(0,2), 16), pg = parseInt(ph.substr(2,2), 16), pb = parseInt(ph.substr(4,2), 16);
          var names = DZH_PALETTE_NAMES[idx] || {name:'未知'+idx, en:'Unknown'+idx};
          return {
            css: cssVal,
            hex: cssVal,
            name: names.name,
            en: names.en,
            type: 'palette',
            index: idx,
            rgb: [pr, pg, pb],
            raw: val
          };
        }
        return cssVal;
      }
      // 未定义索引: 返回fallback
      var fb3 = fallback || 'transparent';
      if (returnMeta) {
        return { css: fb3, hex: fb3, name: '未定义索引('+idx+')', en: 'Undefined('+idx+')', type: 'palette', index: idx, rgb: null, raw: val };
      }
      return fb3;
    }

    // 直接颜色解码 (XML中存储的是BGR格式，高位=B, 中位=G, 低位=R)
    var b = (val >> 16) & 0xFF;
    var g = (val >> 8) & 0xFF;
    var r = val & 0xFF;
    var rgbStr = 'rgb(' + r + ',' + g + ',' + b + ')';

    if (returnMeta) {
      // RGB转hex (#RRGGBB)
      var rh = ('0' + r.toString(16)).slice(-2);
      var gh = ('0' + g.toString(16)).slice(-2);
      var bh = ('0' + b.toString(16)).slice(-2);
      return {
        css: rgbStr,
        hex: '#' + rh + gh + bh,
        name: 'BGR直接色',
        en: 'BGR Direct',
        type: 'bgr_direct',
        index: null,
        rgb: [r, g, b],
        raw: val
      };
    }

    return rgbStr;
  }

  /**
   * DZH颜色整数 → CSS hex颜色值（支持调色板索引 + BGR直接色 + 特殊值）
   * 兼容 table-driven-panel.js 中的 dzhIntToCssHex 调用
   *
   * @param {string|number} n - DZH颜色整数值
   * @returns {string} CSS hex颜色值（如 '#808080'）
   */
  function dzhIntToCssHex(n) {
    // 特殊值处理
    if (n === undefined || n === null || n === '' || n === -1 || isNaN(n)) return '#808080';
    var val = parseInt(n);
    if (isNaN(val) || val === -1) return '#808080';
    if (val < 0) val += 0x100000000;

    // DZH调色板索引: 高位字节0x01表示调色板索引
    if ((val & 0xFF000000) === 0x01000000) {
      var idx = val & 0xFF;
      return PALETTE[idx] || '#808080';  // 未定义索引返回灰色
    }

    // BGR直接色解码（高位=B, 中位=G, 低位=R）
    var b = (val >> 16) & 0xFF;
    var g = (val >> 8) & 0xFF;
    var r = val & 0xFF;
    return '#' + ('0' + r.toString(16)).slice(-2) + ('0' + g.toString(16)).slice(-2) + ('0' + b.toString(16)).slice(-2);
  }

  return {
    dzhColorToCss: dzhColorToCss,
    dzhIntToCssHex: dzhIntToCssHex,
    DZH_PALETTE_NAMES: DZH_PALETTE_NAMES
  };
})();

// 兼容多种导出方式
if (typeof window !== 'undefined') {
  window.dzhColorToCss = DZH_COLOR_UTILS.dzhColorToCss;
  window.dzhIntToCssHex = DZH_COLOR_UTILS.dzhIntToCssHex;
  window.DZH_COLOR_UTILS = DZH_COLOR_UTILS;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = DZH_COLOR_UTILS;
}

// ============================================================================
// ===== 来源: canvas.js（原 FlowCanvas 画布引擎） =====
// ============================================================================

/**
 * FlowCanvas - xyflow-style flow canvas engine
 * Replaces the old DZHCanvas with a declarative state, DOM-based nodes,
 * SVG-based edges, handle connection drag, minimap, and viewport management.
 */

// ─── VirtualScroller (kept from original) ────────────────────────────────────

class VirtualScroller {
  constructor(scrollContainer, rowHeight, bufferRows) {
    rowHeight = rowHeight || 28;
    bufferRows = bufferRows || 5;
    this.container = scrollContainer;
    this.rowHeight = rowHeight;
    this.bufferRows = bufferRows;
    this.data = [];
    this.renderRow = null;
    this._spacer = null;
    this._visibleContainer = null;
    this._rafId = null;
    this._init();
  }

  _init() {
    this._spacer = document.createElement('div');
    this._spacer.className = 'vs-spacer';
    this._spacer.style.cssText = 'width:100%;';

    this._visibleContainer = document.createElement('div');
    this._visibleContainer.className = 'vs-visible';
    this._visibleContainer.style.cssText = 'position:absolute;top:0;left:0;width:100%;';

    this._wrapper = document.createElement('div');
    this._wrapper.className = 'vs-wrapper';
    this._wrapper.style.cssText = 'position:relative;width:100%;';
    this._wrapper.appendChild(this._spacer);
    this._wrapper.appendChild(this._visibleContainer);

    this.container.appendChild(this._wrapper);

    var self = this;
    this.container.addEventListener('scroll', function () {
      if (self._rafId) return;
      self._rafId = requestAnimationFrame(function () {
        self._rafId = null;
        self._renderVisibleRows();
      });
    });
  }

  setData(data) {
    this.data = data || [];
    this._updateScrollHeight();
    this._renderVisibleRows();
  }

  _updateScrollHeight() {
    this._spacer.style.height = this.data.length * this.rowHeight + 'px';
  }

  _renderVisibleRows() {
    var scrollTop = this.container.scrollTop;
    var containerHeight = this.container.clientHeight;
    var startIndex = Math.max(0, Math.floor(scrollTop / this.rowHeight) - this.bufferRows);
    var endIndex = Math.min(this.data.length, Math.ceil((scrollTop + containerHeight) / this.rowHeight) + this.bufferRows);
    this._visibleContainer.innerHTML = '';
    this._visibleContainer.style.transform = 'translateY(' + startIndex * this.rowHeight + 'px)';
    for (var i = startIndex; i < endIndex; i++) {
      if (this.renderRow) {
        this._visibleContainer.appendChild(this.renderRow(this.data[i], i));
      }
    }
  }

  destroy() {
    if (this._rafId) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
  }
}

// ─── DZH Column Map (kept from original) ─────────────────────────────────────

var DEFAULT_DZH_COL_MAP = {
  2: { name: '现价', field: 'current_price', w: 48 },
  '-1': { name: '代码', field: 'code', w: 55 },
  '-2': { name: '名称', field: 'name', w: 55 },
  '-3': { name: '涨幅', field: 'change_pct', w: 48, signed: true },
  7: { name: '涨跌', field: 'change_amt', w: 48, signed: true },
  14: { name: '成交额', field: 'amount', w: 55 },
  8: { name: '买入价', field: 'bid_price', w: 48 },
  10: { name: '成交量', field: 'volume', w: 55 },
  17: { name: '换手率', field: 'turnover_rate', w: 48 },
  45: { name: '市盈率', field: 'pe_ratio', w: 48 },
  1: { name: '序号', field: 'seq', w: 30 },
  5: { name: '开盘价', field: 'open_price', w: 48 },
  6: { name: '最高价', field: 'high_price', w: 48 },
  '-4': { name: '最低价', field: 'low_price', w: 48 },
  24: { name: '换手率', field: 'turnover_rate', w: 48 },
  '-5': { name: '涨跌额', field: 'change_amt', w: 48, signed: true },
  '-6': { name: '成交量', field: 'volume', w: 55 },
  101: { name: 'DDX飘红', field: 'ddx_red_days', w: 42 },
  108: { name: '量比', field: 'volume_ratio', w: 42 },
  287: { name: 'BBD', field: 'bbd', w: 42 },
  400: { name: '主力净买', field: 'main_inflow', w: 55 },
  401: { name: 'DDX', field: 'ddx', w: 42 }
};

// ─── Node type colour / shape registry ───────────────────────────────────────

var DEFAULT_NODE_TYPE_DEFAULTS = {
  market_source:       { color: '#DAA520', shape: 'cylinder',     label: '备选池' },   // DZH原生默认金黄色(截图验证)
  stock_state_pool:    { color: '#FF1493', shape: 'rounded-rect', label: '状态池' },   // DZH原生默认深粉红DeepPink(超赢7号→超赢追踪/整理 clr=-1时截图验证:非纯品红,而是偏红的粉紫色#FF1493系)
  transfer_condition:  { color: '#f39c12', shape: 'diamond',      label: '转移条件' }, // UI fallback(橙黄色,接近DZH原生菱形色)
  discard_pool:        { color: '#c0392b', shape: 'small-rect',   label: '丢弃池' },   // UI fallback(红色系)
  tdx_candidate:       { color: '#9b59b6', shape: 'cylinder',     label: 'TDX候选' },  // TDX系统默认色
  tdx_state_pool:      { color: '#27ae60', shape: 'rounded-rect', label: 'TDX状态池' },// TDX系统默认色
  tdx_condition:       { color: '#e67e22', shape: 'diamond',      label: 'TDX条件' }   // TDX系统默认色
};

// ─── DZH Native Rendering Constants ──────────────────────────────────────

var DEFAULT_LABEL_COLOR = '#ffffff';        // DZH原生标签默认文字颜色(黑色背景上白色)
var CONTAINER_DASHED_COLOR = '#555555';     // DZH容器虚线兜底色
var CONTAINER_DEFAULT_BORDER = '#333333';   // DZH容器默认边框色

// DZH背景色特殊值映射表（基于原生客户端行为）
var DEFAULT_BACKCOLOR_SPECIAL_MAP = {
  16777216: '#000000',   // 0x01000000 → 纯黑
  '-1': '#000000'        // -1 → 纯黑(DZH窗口默认背景)
};
var DEFAULT_BACKCOLOR = '#000000';          // 终极兜底背景色（DZH原生默认纯黑）

// ─── DZH Node Default Sizes (原生客户端节点默认尺寸) ─────────────────────
var DEFAULT_DZH_NODE_SIZES = {
  stock_state_pool:    { width: 110, height: 64 },  // 状态池默认尺寸 - 来源:DZH原生客户端标准尺寸
  transfer_condition:  { width: 26,  height: 24 },   // DZH条件三角形 - 来源:精确匹配XML原始25×24
  market_source:       { width: 70,  height: 80 },   // DZH备选池圆柱体 - 来源:接近XML原始67×78
  discard_pool:        { width: 56,  height: 36 },   // 丢弃池默认尺寸 - 来源:DZH原生客户端标准尺寸
  container:           { width: 200, height: 150 },  // 容器默认尺寸 - 来源:DZH原生客户端标准尺寸(通常由XML指定)
  column:              { width: 100, height: 28 },   // 列节点默认尺寸 - 来源:DZH原生客户端标准尺寸
  label:               { width: 'auto', height: 'auto' }   // 标签由内容决定 - 来源:动态计算
};

// ─── DZH Border Widths (原生客户端边框宽度) ───────────────────────────────
var DEFAULT_DZH_BORDER_WIDTHS = {
  state_pool:  2,      // 状态池边框 - 来源:DZH原生客户端标准(2px)
  candidate:   2,      // 备选池细边框 - 来源:DZH原生客户端标准(2px)
  discard:     4,      // 丢弃池粗边框 - 来源:DZH原生客户端标准(4px)
  container:   2,      // 容器边框 - 来源:DZH原生客户端标准(2px)
  container_dashed: 2, // 容器虚线边框 - 来源:DZH原生客户端标准(2px)
  column:      2,      // 列节点边框 - 来源:DZH原生客户端标准(2px)
  generic:     1       // 通用/默认边框 - 来源:DZH原生客户端标准(1px)
};

// ─── DZH Font Sizes (字体大小层级) ──────────────────────────────────────
var DEFAULT_DZH_FONT_SIZES = {
  title_bar:   13,     // 状态池标题栏 - 来源:DZH原生客户端标准字号(13px)
  label:       'auto', // 标签文字 - 来源:根据容器高度动态计算
  candidate:   11,     // 备选池文字 - 来源:DZH原生客户端标准字号(11px)
  badge:       10,     // 徽章文字 - 来源:DZH原生客户端标准字号(10px)
  edge_label:  10,     // 边线标签 - 来源:DZH原生客户端标准字号(10px)
  tiny:        8,      // 超小文字(如加载时间) - 来源:DZH原生客户端标准字号(8px)
  stock_count: 10      // 股票计数 - 来源:DZH原生客户端标准字号(10px)
};

// ─── DZH Edge Default Widths by Strategy (边线策略默认宽度) ─────────────
var DEFAULT_DZH_EDGE_WIDTHS = {
  pass:      1.5, // 传递 - 细线(DZH原生)
  copy:      1.5, // 复制 - 细线
  overwrite: 2,   // 覆盖 - 中线
  move:      2,   // 移动 - 中线
  force:     3,   // 强制 - 粗线
  reject:    2    // 拒绝 - 中线
};

// cell_type → node type mapping
var DEFAULT_CELL_TYPE_MAP = {
  202: 'market_source',
  200: 'stock_state_pool',
  201: 'transfer_condition',
  4:   'discard_pool'
};

// ─── Edge strategy colours ────────────────────────────────────────────────────

var DEFAULT_EDGE_STRATEGY_COLORS = {
  pass:      '#FFD700', // UI默认边线色(传递策略)
  copy:      '#FFA500', // UI默认边线色(复制策略)
  overwrite: '#FF8800', // UI默认边线色(覆盖策略)
  move:      '#FF4400', // UI默认边线色(移动策略)
  force:     '#FF0000'  // UI默认边线色(强制策略)
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ─── DZH Palette Names (与Python端 dzh_constants.DZH_PALETTE 完全一致) ─────────
// 20色调色板中英文名称表，供元信息模式和颜色可视化使用
var DEFAULT_DZH_PALETTE_NAMES = {
  0x00: {name:'黑色', en:'Black'},
  0x01: {name:'深红', en:'DarkRed'},
  0x02: {name:'深绿', en:'DarkGreen'},
  0x03: {name:'橄榄', en:'Olive'},
  0x04: {name:'深蓝', en:'Navy'},
  0x05: {name:'紫色', en:'Purple'},
  0x06: {name:'青色', en:'Teal'},
  0x07: {name:'灰色', en:'Gray'},
  0x08: {name:'浅绿灰', en:'PaleGreen'},
  0x09: {name:'浅蓝灰', en:'LightBlue'},
  0x0A: {name:'米白', en:'AntiqueWhite'},
  0x0B: {name:'银灰', en:'Silver'},
  0x0C: {name:'深灰', en:'DarkGray'},
  0x0D: {name:'红色', en:'Red'},
  0x0E: {name:'亮绿', en:'Lime'},
  0x0F: {name:'黄色', en:'Yellow'},
  0x10: {name:'蓝色', en:'Blue'},
  0x11: {name:'品红', en:'Magenta'},
  0x12: {name:'青(Cyan)', en:'Cyan'},
  0x13: {name:'白色', en:'White'}
};

// ─── 运行时常量（从配置表加载，回退到默认值） ───────────────────────────────
var DZH_COL_MAP = Object.assign({}, DEFAULT_DZH_COL_MAP);
var NODE_TYPE_DEFAULTS = Object.assign({}, DEFAULT_NODE_TYPE_DEFAULTS);
var BACKCOLOR_SPECIAL_MAP = Object.assign({}, DEFAULT_BACKCOLOR_SPECIAL_MAP);
var DZH_NODE_SIZES = Object.assign({}, DEFAULT_DZH_NODE_SIZES);
var DZH_BORDER_WIDTHS = Object.assign({}, DEFAULT_DZH_BORDER_WIDTHS);
var DZH_FONT_SIZES = Object.assign({}, DEFAULT_DZH_FONT_SIZES);
var DZH_EDGE_WIDTHS = Object.assign({}, DEFAULT_DZH_EDGE_WIDTHS);
var CELL_TYPE_MAP = Object.assign({}, DEFAULT_CELL_TYPE_MAP);
var EDGE_STRATEGY_COLORS = Object.assign({}, DEFAULT_EDGE_STRATEGY_COLORS);
var DZH_PALETTE_NAMES = Object.assign({}, DEFAULT_DZH_PALETTE_NAMES);

// ─── 配置表加载（表驱动：从 /api/config/tables/<table_name> 拉取） ──────────
var _configPromise = null;

function ensureConfigLoaded() {
  if (_configPromise) return _configPromise;
  _configPromise = Promise.all([
    fetch('/api/config/tables/column_definitions').then(function(r) { return r.json(); }).catch(function() { return null; }),
    fetch('/api/config/tables/cell_type_registry').then(function(r) { return r.json(); }).catch(function() { return null; }),
    fetch('/api/config/tables/edge_strategies').then(function(r) { return r.json(); }).catch(function() { return null; }),
    fetch('/api/config/tables/theme_config').then(function(r) { return r.json(); }).catch(function() { return null; })
  ]).then(function(results) {
    var colDefs = results[0];
    var cellReg = results[1];
    var edgeStrat = results[2];
    var themeCfg = results[3];

    // SubTask 18.1: DZH_COL_MAP ← column_definitions.canvas_col_map
    if (colDefs && colDefs.canvas_col_map) {
      DZH_COL_MAP = colDefs.canvas_col_map;
    }

    // SubTask 18.2: NODE_TYPE_DEFAULTS/DZH_NODE_SIZES/DZH_BORDER_WIDTHS/DZH_FONT_SIZES ← cell_type_registry
    // SubTask 18.6: CELL_TYPE_MAP ← cell_type_registry.canvas_cell_type_map
    if (cellReg) {
      if (cellReg.canvas_node_defaults) NODE_TYPE_DEFAULTS = cellReg.canvas_node_defaults;
      if (cellReg.canvas_node_sizes) DZH_NODE_SIZES = cellReg.canvas_node_sizes;
      if (cellReg.canvas_border_widths) DZH_BORDER_WIDTHS = cellReg.canvas_border_widths;
      if (cellReg.canvas_font_sizes) DZH_FONT_SIZES = cellReg.canvas_font_sizes;
      if (cellReg.canvas_cell_type_map) CELL_TYPE_MAP = cellReg.canvas_cell_type_map;
    }

    // SubTask 18.3: DZH_EDGE_WIDTHS/EDGE_STRATEGY_COLORS ← edge_strategies
    if (edgeStrat) {
      if (edgeStrat.canvas_edge_widths) DZH_EDGE_WIDTHS = edgeStrat.canvas_edge_widths;
      if (edgeStrat.canvas_strategy_colors) EDGE_STRATEGY_COLORS = edgeStrat.canvas_strategy_colors;
    }

    // SubTask 18.4: DZH_PALETTE_NAMES ← theme_config.palette_names
    // SubTask 18.5: BACKCOLOR_SPECIAL_MAP ← theme_config.backcolor_special_map
    if (themeCfg) {
      if (themeCfg.palette_names) {
        // 配置表使用十进制字符串键，转换为数值键以保持与原 hex 键兼容
        var palette = {};
        Object.keys(themeCfg.palette_names).forEach(function(k) {
          palette[parseInt(k, 10)] = themeCfg.palette_names[k];
        });
        DZH_PALETTE_NAMES = palette;
      }
      if (themeCfg.backcolor_special_map) {
        BACKCOLOR_SPECIAL_MAP = themeCfg.backcolor_special_map;
      }
    }

    return results;
  }).catch(function(err) {
    console.error('[canvas.js] Failed to load config tables:', err);
    return null;
  });
  return _configPromise;
}

// 模块加载时即开始拉取配置（异步，加载完成前使用默认值）
ensureConfigLoaded();

// dzhColorToCss 已合并至本文件开头（来源: utils/color.js，统一实现，消除重复代码）
// 通过 window.dzhColorToCss 全局引用，本模块内可直接调用

/**
 * 生成颜色可视化的HTML片段（色块+名称+hex+类型标注）
 * 用于属性面板中显示DZH颜色值的可视化效果
 *
 * @param {string|number} clrValue - DZH颜色值
 * @param {string} [fallback] - 回退色，默认 '#808080'
 * @returns {string} HTML字符串
 */
function renderDzhColorBadge(clrValue, fallback) {
  var meta = dzhColorToCss(clrValue, fallback || '#808080', true);
  if (!meta) return '<span style="color:#888">无效颜色</span>';

  var typeLabel = '';
  if (meta.type === 'palette') typeLabel = ' | 索引:' + meta.index;
  else if (meta.type === 'bgr_direct') typeLabel = ' | BGR直接色';
  else if (meta.type === 'special') typeLabel = ' | 特殊值';

  return [
    '<span class="dzh-color-badge" style="display:inline-flex;align-items:center;gap:4px;vertical-align:middle;">',
    '  <span class="dzh-color-swatch" style="',
    '    display:inline-block;width:16px;height:16px;border-radius:3px;',
    '    border:1px solid rgba(255,255,255,0.2);',
    '    background:', meta.css, ';',
    '    cursor:pointer;',
    '    title="点击复制: ', meta.hex, '"',
    '    onclick="copyToClipboard(\'', meta.hex, '\')"',
    '  "></span>',
    '  <span class="dzh-color-name" style="color:#ccc;font-size:11px;">',
    meta.name, ' (<span style="color:#8af;font-family:monospace;">', meta.hex, '</span>)', typeLabel,
    '  </span>',
    '</span>'
  ].join('');
}

/**
 * 复制文本到剪贴板（带toast提示）
 */
function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function() {
      showTooltip('已复制: ' + text);
    });
  } else {
    // fallback for older browsers
    var ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showTooltip('已复制: ' + text);
  }
}

/**
 * 显示临时提示消息（右下角浮动）
 */
function showTooltip(message, duration) {
  duration = duration || 1500;
  var tip = document.createElement('div');
  tip.textContent = message;
  tip.style.cssText = 'position:fixed;bottom:20px;right:20px;background:#333;color:#fff;padding:6px 12px;border-radius:4px;font-size:12px;z-index:99999;box-shadow:0 2px 8px rgba(0,0,0,0.3);transition:opacity 0.3s;';
  document.body.appendChild(tip);
  setTimeout(function() { tip.style.opacity = '0'; setTimeout(function() { tip.remove(); }, 300); }, duration);
}


function parseCssColor(cssColor) {
  if (!cssColor || cssColor === 'transparent') return null;
  var m = cssColor.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
  if (m) return [parseInt(m[1]), parseInt(m[2]), parseInt(m[3])];
  var hex = cssColor.match(/^#([a-fA-F0-9]{6})$/);
  if (hex) {
    return [
      parseInt(hex[1].substr(0,2), 16),
      parseInt(hex[1].substr(2,2), 16),
      parseInt(hex[1].substr(4,2), 16)
    ];
  }
  return null;
}

function darkenColor(cssColor, pct) {
  var rgb = parseCssColor(cssColor);
  if (!rgb) return '#222';
  var f = 1 - pct / 100;
  return 'rgb(' + Math.round(rgb[0] * f) + ',' + Math.round(rgb[1] * f) + ',' + Math.round(rgb[2] * f) + ')';
}

function lightenColor(cssColor, pct) {
  var rgb = parseCssColor(cssColor);
  if (!rgb) return '#888';
  var f = 1 + pct / 100;
  return 'rgb(' + Math.min(255, Math.round(rgb[0] * f)) + ',' + Math.min(255, Math.round(rgb[1] * f)) + ',' + Math.min(255, Math.round(rgb[2] * f)) + ')';
}

// ─── FlowCanvas ───────────────────────────────────────────────────────────────

export class FlowCanvas {
  /**
   * @param {HTMLElement} containerEl  - the canvas wrapper div
   * @param {Object} options          - { onConnect, onNodeClick, onEdgeClick, ... }
   */
  constructor(containerEl, options) {
    options = options || {};
    this.container = containerEl;

    // ── Declarative state ──
    this._nodes = [];
    this._edges = [];
    this._poolMeta = null;
    this._changeListeners = [];

    // ── Callbacks ──
    this.onConnect = options.onConnect || null;
    this.onNodeClick = options.onNodeClick || null;
    this.onEdgeClick = options.onEdgeClick || null;
    this.onCanvasClick = options.onCanvasClick || null;
    this.onNodeDoubleClick = options.onNodeDoubleClick || null;
    this.onNodeDragEnd = options.onNodeDragEnd || null;
    this.onZoomChangeCb = options.onZoomChange || null;
    this._toolClickHandler = null;

    // ── Viewport transform ──
    this.transform = { x: 0, y: 0, zoom: 1 };

    // ── Selection ──
    this.selectedNodeId = null;
    this.selectedEdgeId = null;
    this.selectedNodeIds = [];

    // ── Interaction state ──
    this._isPanning = false;
    this._panStartX = 0;
    this._panStartY = 0;
    this._lastPanX = 0;
    this._lastPanY = 0;
    this._isBoxSelecting = false;
    this._boxSelectStart = null;

    // ── Connection drag state ──
    this._connectingFrom = null;   // { nodeId, handleType, handleId }
    this._tempEdge = null;         // SVG path for drag preview

    // ── Edge line type ──
    this.edgeLineType = 'bezier';  // 'bezier' | 'orthogonal' | 'straight'

    // ── Mode ──
    this._runMode = false;
    this._showExecOrder = false;
    this._execOrderCounter = 0;   // 执行顺序编号模式下的当前计数器
    this.editable = true;
    this.draggable = true;
    this.connectable = true;
    this.selectable = true;
    this.poolId = null;

    // ── Element maps ──
    this.nodeElements = new Map();  // nodeId -> DOM element
    this._edgeElements = new Map(); // edgeId -> { path, hitPath, label, edge }
    this._handleElements = new Map(); // 'nodeId-handleId' -> DOM element

    // ── Highlight ──
    this.highlightedNodes = new Set();
    this.highlightedEdges = new Set();

    // ── Resize handles ──
    this._resizeHandlesEl = null;
    this._currentResizeNodeId = null;

    // ── Node renderer override ──
    this._nodeRenderer = null;  // custom nodeRenderer(type) => html

    // ── Build DOM ──
    this._buildDOM();
    this._initEvents();
  }

  // ── State management ──────────────────────────────────────────────────────

  onChange(cb) { this._changeListeners.push(cb); }

  _emitChange() {
    var self = this;
    this._changeListeners.forEach(function (cb) { cb(self._nodes, self._edges); });
  }

  setNodes(nodes) { this._nodes = nodes || []; this._renderNodes(); this._emitChange(); }
  setEdges(edges) { this._edges = edges || []; this._renderEdges(); this._emitChange(); }
  getNodes() { return this._nodes; }
  getEdges() { return this._edges; }

  // ── DOM structure ─────────────────────────────────────────────────────────

  _buildDOM() {
    // Save existing special elements before clearing
    var preserved = [];
    var preserveIds = ['tdxPoolBrowser'];
    var self = this;
    preserveIds.forEach(function(id) {
      var el = self.container.querySelector('#' + id);
      if (el) preserved.push(el);
    });

    // Clear container
    this.container.innerHTML = '';

    // Viewport (overflow hidden)
    this.viewportEl = document.createElement('div');
    this.viewportEl.className = 'flow-canvas-viewport';
    this.viewportEl.style.cssText = 'overflow:hidden;position:relative;width:100%;height:100%;background:#000000;'; // DZH原生画布背景色: 纯黑

    // Transform layer
    this.transformEl = document.createElement('div');
    this.transformEl.className = 'flow-canvas-transform';
    this.transformEl.style.cssText = 'position:absolute;top:0;left:0;transform-origin:0 0;';

    // SVG edges layer
    this.svgEl = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    this.svgEl.setAttribute('class', 'flow-edges-layer');
    this.svgEl.style.cssText = 'position:absolute;top:0;left:0;width:8000px;height:6000px;pointer-events:none;';
    this.svgEl.setAttribute('xmlns', 'http://www.w3.org/2000/svg');

    // SVG defs (grid, arrow markers)
    this._initSVGDefs();

    // Grid background
    this._initGrid();

    // Edge group
    this._edgeGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    this._edgeGroup.setAttribute('id', 'flowEdgeGroup');
    this.svgEl.appendChild(this._edgeGroup);

    // Edge label group
    this._edgeLabelGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    this._edgeLabelGroup.setAttribute('id', 'flowEdgeLabelGroup');
    this.svgEl.appendChild(this._edgeLabelGroup);

    this.transformEl.appendChild(this.svgEl);

    // Nodes layer (DOM elements)
    this.nodesEl = document.createElement('div');
    this.nodesEl.className = 'flow-nodes-layer';
    this.nodesEl.style.cssText = 'position:absolute;top:0;left:0;width:8000px;height:6000px;';
    this.transformEl.appendChild(this.nodesEl);

    this.viewportEl.appendChild(this.transformEl);

    // Resize handles overlay
    this._resizeHandlesEl = document.createElement('div');
    this._resizeHandlesEl.className = 'node-resize-handles-container';
    this._resizeHandlesEl.style.cssText = 'position:absolute;display:none;pointer-events:none;z-index:50;';
    this._attachResizeHandles();
    this.transformEl.appendChild(this._resizeHandlesEl);

    // Minimap
    this.minimapEl = document.createElement('div');
    this.minimapEl.className = 'flow-minimap';
    this.minimapEl.style.cssText = 'position:absolute;bottom:12px;right:12px;width:180px;height:120px;background:rgba(15,15,30,0.9);border:1px solid rgba(255,255,255,0.1);border-radius:6px;overflow:hidden;z-index:60;cursor:pointer;';
    this._minimapCanvas = document.createElement('canvas');
    this._minimapCanvas.width = 180;
    this._minimapCanvas.height = 120;
    this._minimapCanvas.style.cssText = 'width:180px;height:120px;';
    this.minimapEl.appendChild(this._minimapCanvas);
    this._minimapViewport = document.createElement('div');
    this._minimapViewport.className = 'minimap-viewport';
    this._minimapViewport.style.cssText = 'position:absolute;border:1.5px solid rgba(74,144,217,0.7);background:rgba(74,144,217,0.08);pointer-events:none;border-radius:2px;';
    this.minimapEl.appendChild(this._minimapViewport);
    this.viewportEl.appendChild(this.minimapEl);

    // Zoom controls
    this.zoomControlsEl = document.createElement('div');
    this.zoomControlsEl.className = 'flow-zoom-controls';
    this.zoomControlsEl.style.cssText = 'position:absolute;bottom:140px;right:12px;display:flex;flex-direction:column;gap:4px;z-index:60;';
    var btnStyle = 'width:32px;height:32px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:rgba(30,30,60,0.9);color:#ccc;cursor:pointer;font-size:16px;display:flex;align-items:center;justify-content:center;';
    var self = this;
    var btnIn = document.createElement('button');
    btnIn.style.cssText = btnStyle;
    btnIn.textContent = '+';
    btnIn.title = '放大';
    btnIn.addEventListener('click', function () { self.zoomIn(); });
    var btnOut = document.createElement('button');
    btnOut.style.cssText = btnStyle;
    btnOut.textContent = '−';
    btnOut.title = '缩小';
    btnOut.addEventListener('click', function () { self.zoomOut(); });
    var btnFit = document.createElement('button');
    btnFit.style.cssText = btnStyle;
    btnFit.textContent = '⊡';
    btnFit.title = '适应画布';
    btnFit.addEventListener('click', function () { self.fitToContent(40); });
    this._zoomLabel = document.createElement('span');
    this._zoomLabel.style.cssText = 'text-align:center;font-size:11px;color:#9090a8;';
    this._zoomLabel.textContent = '100%';
    this.zoomControlsEl.appendChild(btnIn);
    this.zoomControlsEl.appendChild(this._zoomLabel);
    this.zoomControlsEl.appendChild(btnOut);
    this.zoomControlsEl.appendChild(btnFit);
    this.viewportEl.appendChild(this.zoomControlsEl);

    // Box select rectangle
    this._boxSelectEl = document.createElement('div');
    this._boxSelectEl.className = 'flow-box-select';
    this._boxSelectEl.style.cssText = 'position:absolute;border:1.5px dashed rgba(74,144,217,0.7);background:rgba(74,144,217,0.08);display:none;pointer-events:none;z-index:55;';
    this.viewportEl.appendChild(this._boxSelectEl);

    this.container.appendChild(this.viewportEl);

    // Re-append preserved elements
    preserved.forEach(function(el) {
      self.viewportEl.appendChild(el);
    });
  }

  _initSVGDefs() {
    var ns = 'http://www.w3.org/2000/svg';
    var defs = document.createElementNS(ns, 'defs');

    // Arrow markers for each strategy
    var markers = [
      { id: 'arrowPass',      color: EDGE_STRATEGY_COLORS.pass },
      { id: 'arrowCopy',      color: EDGE_STRATEGY_COLORS.copy },
      { id: 'arrowOverwrite', color: EDGE_STRATEGY_COLORS.overwrite },
      { id: 'arrowMove',      color: EDGE_STRATEGY_COLORS.move },
      { id: 'arrowForce',     color: EDGE_STRATEGY_COLORS.force },
      { id: 'arrowDefault',   color: '#f39c12' } // UI默认箭头色(未知策略时)
    ];
    markers.forEach(function (m) {
      var marker = document.createElementNS(ns, 'marker');
      marker.setAttribute('id', m.id);
      marker.setAttribute('viewBox', '0 0 10 10');
      marker.setAttribute('markerWidth', '4');
      marker.setAttribute('markerHeight', '4');
      marker.setAttribute('refX', '9');
      marker.setAttribute('refY', '5');
      marker.setAttribute('orient', 'auto');
      marker.innerHTML = '<polygon points="0,2 10,5 0,8" fill="' + m.color + '"/>';
      defs.appendChild(marker);
    });

    this.svgEl.appendChild(defs);
  }

  _initGrid() {
    var ns = 'http://www.w3.org/2000/svg';
    var defs = this.svgEl.querySelector('defs');

    // Remove old grid patterns if re-rendering
    var oldP1 = defs.querySelector('#flowGridPattern');
    var oldP2 = defs.querySelector('#flowGridPatternStrong');
    if (oldP1) oldP1.remove();
    if (oldP2) oldP2.remove();

    var p1 = document.createElementNS(ns, 'pattern');
    p1.setAttribute('id', 'flowGridPattern');
    p1.setAttribute('width', '20');
    p1.setAttribute('height', '20');
    p1.setAttribute('patternUnits', 'userSpaceOnUse');
    p1.innerHTML = '<path d="M 20 0 L 0 0 0 20" fill="none" stroke="rgba(255,255,255,0.03)" stroke-width="0.5"/>';
    defs.appendChild(p1);

    var p2 = document.createElementNS(ns, 'pattern');
    p2.setAttribute('id', 'flowGridPatternStrong');
    p2.setAttribute('width', '100');
    p2.setAttribute('height', '100');
    p2.setAttribute('patternUnits', 'userSpaceOnUse');
    p2.innerHTML = '<rect width="100" height="100" fill="url(#flowGridPattern)"/><path d="M 100 0 L 0 0 0 100" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="0.8"/>';
    defs.appendChild(p2);

    // Remove old grid rect
    var oldRect = this.svgEl.querySelector('.canvas-grid');
    if (oldRect) oldRect.remove();

    this._gridRect = document.createElementNS(ns, 'rect');
    this._gridRect.setAttribute('width', '8000');
    this._gridRect.setAttribute('height', '6000');
    this._gridRect.setAttribute('fill', 'url(#flowGridPatternStrong)');
    this._gridRect.setAttribute('class', 'canvas-grid');
    this._gridRect.style.pointerEvents = 'none';
    // Insert grid rect before edge group
    this.svgEl.insertBefore(this._gridRect, this._edgeGroup);
  }

  // ── Events ────────────────────────────────────────────────────────────────

  _initEvents() {
    var self = this;

    // Wheel zoom
    this.viewportEl.addEventListener('wheel', function (e) {
      e.preventDefault();
      var rect = self.viewportEl.getBoundingClientRect();
      var mx = e.clientX - rect.left;
      var my = e.clientY - rect.top;
      var delta = e.deltaY > 0 ? 0.9 : 1.1;
      var newZoom = Math.max(0.1, Math.min(5, self.transform.zoom * delta));
      var ratio = newZoom / self.transform.zoom;
      self.transform.x = mx - ratio * (mx - self.transform.x);
      self.transform.y = my - ratio * (my - self.transform.y);
      self.transform.zoom = newZoom;
      self._applyTransform();
      self._onZoomChange();
      self._updateMinimap();
    }, { passive: false });

    // Mouse down on viewport (pan / box select)
    this.viewportEl.addEventListener('mousedown', function (e) {
      if (e.target !== self.viewportEl && e.target !== self.transformEl &&
          e.target !== self.svgEl && e.target !== self._gridRect &&
          e.target !== self.nodesEl) return;

      if (e.button === 0 || e.button === 1) {
        if (e.button === 1 || e.shiftKey) {
          // Middle button or Shift+Left: Pan
          self._isPanning = true;
          self._panStartX = e.clientX;
          self._panStartY = e.clientY;
          self._lastPanX = self.transform.x;
          self._lastPanY = self.transform.y;
          self.viewportEl.style.cursor = 'grabbing';
        } else {
          // 编号模式下点击空白 → 退出编号模式
          if (self._showExecOrder) {
            self._showExecOrder = false;
            self._execOrderCounter = 0;
            if (self.onExecOrderComplete) self.onExecOrderComplete();
            return;
          }
          // Left button on blank: Box select
          self._isBoxSelecting = true;
          var rect = self.viewportEl.getBoundingClientRect();
          self._boxSelectStart = { x: e.clientX - rect.left, y: e.clientY - rect.top };
          self._boxSelectEl.style.display = 'block';
          self._boxSelectEl.style.left = self._boxSelectStart.x + 'px';
          self._boxSelectEl.style.top = self._boxSelectStart.y + 'px';
          self._boxSelectEl.style.width = '0px';
          self._boxSelectEl.style.height = '0px';
        }
        if (!self._isBoxSelecting && self.onCanvasClick) self.onCanvasClick(e);
      }
    });

    // Global mouse move
    document.addEventListener('mousemove', function (e) {
      if (self._isPanning) {
        self.transform.x = self._lastPanX + (e.clientX - self._panStartX);
        self.transform.y = self._lastPanY + (e.clientY - self._panStartY);
        self._applyTransform();
        self._updateMinimap();
      }
      if (self._isBoxSelecting && self._boxSelectStart) {
        var rect = self.viewportEl.getBoundingClientRect();
        var cx = e.clientX - rect.left;
        var cy = e.clientY - rect.top;
        var x = Math.min(self._boxSelectStart.x, cx);
        var y = Math.min(self._boxSelectStart.y, cy);
        var w = Math.abs(cx - self._boxSelectStart.x);
        var h = Math.abs(cy - self._boxSelectStart.y);
        self._boxSelectEl.style.left = x + 'px';
        self._boxSelectEl.style.top = y + 'px';
        self._boxSelectEl.style.width = w + 'px';
        self._boxSelectEl.style.height = h + 'px';
      }
      // Connection drag
      if (self._connectingFrom && self._tempEdge) {
        var vpRect = self.viewportEl.getBoundingClientRect();
        var mx = (e.clientX - vpRect.left - self.transform.x) / self.transform.zoom;
        var my = (e.clientY - vpRect.top - self.transform.y) / self.transform.zoom;
        var srcPos = self._getHandlePosition(self._connectingFrom.nodeId, self._connectingFrom.handleId, self._connectingFrom.handleType);
        if (srcPos) {
          self._tempEdge.setAttribute('d', self._buildEdgePath(srcPos, { x: mx, y: my }, 0));
        }
      }
    });

    // Global mouse up
    document.addEventListener('mouseup', function (e) {
      if (self._isPanning) {
        self._isPanning = false;
        self.viewportEl.style.cursor = '';
      }
      if (self._isBoxSelecting) {
        self._finishBoxSelect(e);
        self._isBoxSelecting = false;
        self._boxSelectEl.style.display = 'none';
      }
      // Connection drag end
      if (self._connectingFrom) {
        // Check if we released on a target handle
        var targetHandle = self._findHandleAtPoint(e.clientX, e.clientY);
        if (targetHandle && targetHandle.handleType === 'target' && targetHandle.nodeId !== self._connectingFrom.nodeId) {
          if (self.onConnect) {
            self.onConnect({
              source: self._connectingFrom.nodeId,
              sourceHandle: self._connectingFrom.handleId,
              target: targetHandle.nodeId,
              targetHandle: targetHandle.handleId
            });
          }
        }
        // Remove temp edge
        if (self._tempEdge && self._tempEdge.parentNode) {
          self._tempEdge.parentNode.removeChild(self._tempEdge);
        }
        self._connectingFrom = null;
        self._tempEdge = null;
      }
    });

    // Right-click context menu
    this.viewportEl.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      // Let the main.js handle context menu via the existing mechanism
    });

    // Minimap click to navigate
    this.minimapEl.addEventListener('mousedown', function (e) {
      var mmRect = self.minimapEl.getBoundingClientRect();
      var mmX = e.clientX - mmRect.left;
      var mmY = e.clientY - mmRect.top;
      self._navigateMinimap(mmX, mmY);
    });
  }

  _finishBoxSelect(e) {
    if (!this._boxSelectStart) return;
    var rect = this.viewportEl.getBoundingClientRect();
    var cx = e.clientX - rect.left;
    var cy = e.clientY - rect.top;
    var x1 = Math.min(this._boxSelectStart.x, cx);
    var y1 = Math.min(this._boxSelectStart.y, cy);
    var x2 = Math.max(this._boxSelectStart.x, cx);
    var y2 = Math.max(this._boxSelectStart.y, cy);

    // If drag distance is tiny, treat as click (deselect)
    if (x2 - x1 < 5 && y2 - y1 < 5) {
      this.selectedNodeIds = [];
      this.selectedNodeId = null;
      this.selectedEdgeId = null;
      this._syncSelectionStyles();
      if (this.onCanvasClick) this.onCanvasClick(e);
      return;
    }

    // Convert viewport coords to canvas coords
    var canvasX1 = (x1 - this.transform.x) / this.transform.zoom;
    var canvasY1 = (y1 - this.transform.y) / this.transform.zoom;
    var canvasX2 = (x2 - this.transform.x) / this.transform.zoom;
    var canvasY2 = (y2 - this.transform.y) / this.transform.zoom;

    var selectedIds = [];
    this._nodes.forEach(function (n) {
      var pos = n.position || { x: 0, y: 0, width: 110, height: 64 };
      var nx = pos.x, ny = pos.y, nw = pos.width || 110, nh = pos.height || 64;
      if (nx < canvasX2 && nx + nw > canvasX1 && ny < canvasY2 && ny + nh > canvasY1) {
        selectedIds.push(n.id);
      }
    });
    this.selectedNodeIds = selectedIds;
    this.selectedNodeId = selectedIds.length === 1 ? selectedIds[0] : null;
    this.selectedEdgeId = null;
    this._syncSelectionStyles();
  }

  // ── Handle connection drag ────────────────────────────────────────────────

  _startConnectionDrag(nodeId, handleId, handleType, e) {
    if (this._runMode || !this.connectable) return;
    e.stopPropagation();
    e.preventDefault();

    this._connectingFrom = { nodeId: nodeId, handleId: handleId, handleType: handleType };

    // Create temp edge SVG path
    var ns = 'http://www.w3.org/2000/svg';
    this._tempEdge = document.createElementNS(ns, 'path');
    this._tempEdge.setAttribute('class', 'flow-temp-edge');
    this._tempEdge.setAttribute('stroke', '#4a90d9'); // UI交互色: 临时拖拽连线
    this._tempEdge.setAttribute('stroke-width', '2');
    this._tempEdge.setAttribute('stroke-dasharray', '6,3');
    this._tempEdge.setAttribute('fill', 'none');
    this._tempEdge.style.pointerEvents = 'none';
    this._edgeGroup.appendChild(this._tempEdge);
  }

  _findHandleAtPoint(clientX, clientY) {
    var found = null;
    this._handleElements.forEach(function (el, key) {
      var rect = el.getBoundingClientRect();
      if (clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom) {
        var parts = key.split('-');
        var handleId = parts.length > 1 ? parts[parts.length - 1] : 'default';
        var nodeId = parts.slice(0, parts.length - 1).join('-');
        // Re-derive nodeId and handleId from data attributes
        found = {
          nodeId: el.getAttribute('data-node-id'),
          handleId: el.getAttribute('data-handle-id'),
          handleType: el.getAttribute('data-handle-type')
        };
      }
    });
    return found;
  }

  _getHandlePosition(nodeId, handleId, handleType) {
    var key = nodeId + '-' + handleId;
    var el = this._handleElements.get(key);
    if (el) {
      // Position relative to transform layer
      var nodeEl = this.nodeElements.get(nodeId);
      if (nodeEl) {
        var hRect = el.getBoundingClientRect();
        var nRect = nodeEl.getBoundingClientRect();
        var zoom = this.transform.zoom;
        return {
          x: nodeEl.offsetLeft + (hRect.left - nRect.left + hRect.width / 2) / zoom * zoom / zoom,
          y: nodeEl.offsetTop + (hRect.top - nRect.top + hRect.height / 2) / zoom * zoom / zoom
        };
      }
    }
    // Fallback: use node center edges
    var node = this._findNode(nodeId);
    if (!node) return null;
    var pos = node.position || { x: 0, y: 0, width: 110, height: 64 };
    if (handleType === 'source') {
      return { x: pos.x + (pos.width || 110), y: pos.y + (pos.height || 64) / 2 };
    }
    return { x: pos.x, y: pos.y + (pos.height || 64) / 2 };
  }

  // ── Transform ─────────────────────────────────────────────────────────────

  _applyTransform() {
    var t = 'translate(' + this.transform.x + 'px,' + this.transform.y + 'px) scale(' + this.transform.zoom + ')';
    this.transformEl.style.transform = t;
  }

  _onZoomChange() {
    var pct = Math.round(this.transform.zoom * 100);
    this._zoomLabel.textContent = pct + '%';
    // Dispatch zoomchange event on the SVG element for compatibility
    var evt = new CustomEvent('zoomchange', { detail: { zoom: this.transform.zoom } });
    this.svgEl.dispatchEvent(evt);
    if (this.onZoomChangeCb) this.onZoomChangeCb(this.transform.zoom);
  }

  setZoom(zoom) {
    this.transform.zoom = Math.max(0.1, Math.min(5, zoom));
    this._applyTransform();
    this._onZoomChange();
    this._updateMinimap();
  }

  zoomIn() { this.setZoom(this.transform.zoom * 1.2); }
  zoomOut() { this.setZoom(this.transform.zoom / 1.2); }

  fitToContent(padding) {
    padding = padding || 60;
    if (!this._nodes.length) {
      this.transform = { x: 0, y: 0, zoom: 1 };
      this._applyTransform();
      this._onZoomChange();
      return;
    }
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    this._nodes.forEach(function (n) {
      var pos = n.position || { x: 0, y: 0, width: 120, height: 64 };
      minX = Math.min(minX, pos.x);
      minY = Math.min(minY, pos.y);
      maxX = Math.max(maxX, pos.x + (pos.width || 120));
      maxY = Math.max(maxY, pos.y + (pos.height || 64));
    });
    var vpW = this.viewportEl.clientWidth - padding * 2;
    var vpH = this.viewportEl.clientHeight - padding * 2;
    var contentW = maxX - minX || 800;
    var contentH = maxY - minY || 600;
    var scale = Math.min(vpW / contentW, vpH / contentH, 2);
    this.transform.zoom = scale;
    this.transform.x = padding - minX * scale + (vpW - contentW * scale) / 2;
    this.transform.y = padding - minY * scale + (vpH - contentH * scale) / 2;
    this._applyTransform();
    this._onZoomChange();
    this._updateMinimap();
  }

  // Alias
  fitView(padding) { this.fitToContent(padding); }

  // ── Render ────────────────────────────────────────────────────────────────

  render(data) {
    this._nodes = (data && data.nodes) || [];
    this._edges = (data && data.edges) || [];
    this._poolMeta = (data && data.pool_meta) || null;
    this.clearSelection();
    this._renderEdges();
    this._renderNodes();
    this._updateBackground(this._poolMeta);
    // 自动适应视口，确保内容以合适比例显示
    if (this._nodes.length > 0) { this.fitToContent(40); }
    this._updateMinimap();
  }

  refresh(data) {
    var d = data || { nodes: this._nodes, edges: this._edges, pool_meta: this._poolMeta };
    this.render(d);
    if (this._runMode) {
      var self = this;
      setTimeout(function () { self.refreshStockTables(); }, 50);
    }
  }

  _updateBackground(poolMeta) {
    if (!poolMeta || poolMeta.backcolor === undefined) {
      this.viewportEl.style.backgroundColor = DEFAULT_BACKCOLOR;
      return;
    }
    var bc = parseInt(poolMeta.backcolor);
    if (isNaN(bc)) {
      this.viewportEl.style.backgroundColor = DEFAULT_BACKCOLOR;
      return;
    }
    // 优先使用特殊值映射表
    if (BACKCOLOR_SPECIAL_MAP[bc] !== undefined) {
      this.viewportEl.style.backgroundColor = BACKCOLOR_SPECIAL_MAP[bc];
      return;
    }
    if (BACKCOLOR_SPECIAL_MAP[String(bc)] !== undefined) {
      this.viewportEl.style.backgroundColor = BACKCOLOR_SPECIAL_MAP[String(bc)];
      return;
    }
    // 使用dzhColorToCss转换，fallback为纯黑
    this.viewportEl.style.backgroundColor = dzhColorToCss(String(bc), '#000000');
  }

  // ── Node rendering ────────────────────────────────────────────────────────

  // 处理历史数据查看事件（由面板 bsavehis"查看"按钮触发）
  handleHistoryView(detail) {
    var self = this;
    if (!detail || !detail.nodeId) {
      // 清除所有节点的历史数据
      this._nodes.forEach(function(node) {
        if (node.params) {
          delete node.params.history_date;
          delete node.params.history_stocks;
          delete node.params.history_stockCount;
        }
      });
      this._renderNodes();
      return;
    }
    var node = this._findNode(detail.nodeId);
    if (node) {
      if (!node.params) node.params = {};
      node.params.history_date = detail.date || '';
      node.params.history_stocks = detail.stocks || [];
      node.params.history_stockCount = detail.stockCount || 0;
    }
    // 仅重渲染目标节点
    this._rerenderNode(detail.nodeId);
  }

  _renderNodes() {
    this.nodesEl.innerHTML = '';
    this.nodeElements.clear();
    this._handleElements.clear();

    // Sort by z-order
    var sorted = [].concat(this._nodes).sort(function (a, b) {
      var order = { container: 0, state_column: 1, flow_arrow: 2, text_label: 4, tdx_container: 0, tdx_deco_line: 2, tdx_text_label: 4, tdx_deco_text: 4 };
      return (order[a.type] || 3) - (order[b.type] || 3);
    });

    var self = this;
    sorted.forEach(function (node) {
      self._renderNode(node);
    });
  }

  _renderNode(node) {
    var pos = node.position || { x: 0, y: 0, width: 110, height: 64 };

    switch (node.type) {
      case 'stock_state_pool':    this._renderStatePool(node, pos); break;
      case 'transfer_condition':  this._renderCondition(node, pos); break;
      case 'market_source':       this._renderCandidate(node, pos); break;
      case 'discard_pool':        this._renderDiscard(node, pos); break;
      case 'text_label':          this._renderLabel(node, pos); break;
      case 'container':           this._renderContainer(node, pos); break;
      case 'state_column':        this._renderColumn(node, pos); break;
      case 'execution_order':     this._renderExecutionOrder(node, pos); break;
      case 'flow_arrow':          this._renderArrowDeco(node, pos); break;
      case 'tdx_candidate':       this._renderTdxCandidate(node, pos); break;
      case 'tdx_state_pool':      this._renderTdxStatePool(node, pos); break;
      case 'tdx_condition':       this._renderTdxCondition(node, pos); break;
      case 'tdx_deco_text':       this._renderLabel(node, pos); break;
      case 'tdx_text_label':      this._renderLabel(node, pos); break;
      case 'tdx_container':       this._renderContainer(node, pos); break;
      case 'tdx_deco_line':       this._renderArrowDeco(node, pos); break;
      default:                    this._renderGenericNode(node, pos); break;
    }
  }

  _createNodeEl(node, className, styleStr) {
    var self = this;
    var el = document.createElement('div');
    el.className = 'flow-node ' + className;
    el.setAttribute('data-node-id', node.id);
    el.style.cssText = styleStr;

    el.addEventListener('click', function (e) {
      e.stopPropagation();
      self.selectNode(node.id);
      if (self.onNodeClick) self.onNodeClick(node.id, node);
      if (self._toolClickHandler) self._toolClickHandler(node.id);
    });
    el.addEventListener('dblclick', function (e) {
      e.stopPropagation();
      if (self.onNodeDoubleClick) self.onNodeDoubleClick(node.id, node);
    });
    el.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      e.stopPropagation();
    });

    this._attachNodeDrag(el, node);
    this.nodesEl.appendChild(el);
    this.nodeElements.set(node.id, el);

    // Add handles
    this._addHandles(el, node);

    return el;
  }

  _addHandles(el, node) {
    if (node.type === 'text_label' || node.type === 'container' || node.type === 'state_column' ||
        node.type === 'flow_arrow' || node.type === 'execution_order' || node.type === 'tdx_deco_text' ||
        node.type === 'tdx_text_label' || node.type === 'tdx_container' || node.type === 'tdx_deco_line') {
      return; // No handles for decorative nodes
    }

    var self = this;
    var pos = node.position || { x: 0, y: 0, width: 110, height: 64 };

    // Source handle (right side)
    var sourceHandle = document.createElement('div');
    sourceHandle.className = 'flow-handle flow-handle-source';
    sourceHandle.setAttribute('data-node-id', node.id);
    sourceHandle.setAttribute('data-handle-id', 'source');
    sourceHandle.setAttribute('data-handle-type', 'source');
    sourceHandle.style.cssText = 'position:absolute;right:-5px;top:50%;transform:translateY(-50%);width:10px;height:10px;border-radius:50%;background:#4a90d9;border:2px solid #2a5a99;cursor:crosshair;z-index:10;'; // UI交互色: 源连接点
    sourceHandle.addEventListener('mousedown', function (e) {
      self._startConnectionDrag(node.id, 'source', 'source', e);
    });
    el.appendChild(sourceHandle);
    this._handleElements.set(node.id + '-source', sourceHandle);

    // Target handle (left side)
    var targetHandle = document.createElement('div');
    targetHandle.className = 'flow-handle flow-handle-target';
    targetHandle.setAttribute('data-node-id', node.id);
    targetHandle.setAttribute('data-handle-id', 'target');
    targetHandle.setAttribute('data-handle-type', 'target');
    targetHandle.style.cssText = 'position:absolute;left:-5px;top:50%;transform:translateY(-50%);width:10px;height:10px;border-radius:50%;background:#27ae60;border:2px solid #1a7a44;cursor:crosshair;z-index:10;'; // UI交互色: 目标连接点
    el.appendChild(targetHandle);
    this._handleElements.set(node.id + '-target', targetHandle);
  }

  _attachNodeDrag(el, node) {
    var self = this;
    var dragStartX = 0, dragStartY = 0, origPositions = {}, moved = false;
    el.addEventListener('mousedown', function (e) {
      if (e.button !== 0) return;
      if (self._runMode || !self.draggable) return;
      // Don't start drag if clicking on a handle
      if (e.target.classList.contains('flow-handle')) return;
      e.preventDefault();

      // Determine which nodes to drag
      var dragNodeIds;
      if (self.selectedNodeIds.indexOf(node.id) !== -1) {
        dragNodeIds = self.selectedNodeIds.slice();
      } else {
        dragNodeIds = [node.id];
      }

      dragStartX = e.clientX;
      dragStartY = e.clientY;
      origPositions = {};
      dragNodeIds.forEach(function (nid) {
        var n = self._findNode(nid);
        var p = n && n.position ? n.position : { x: 0, y: 0 };
        origPositions[nid] = { x: p.x, y: p.y };
      });
      moved = false;

      var onMove = function (me) {
        var dx = me.clientX - dragStartX;
        var dy = me.clientY - dragStartY;
        if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;
        var ddx = dx / self.transform.zoom;
        var ddy = dy / self.transform.zoom;
        dragNodeIds.forEach(function (nid) {
          var n = self._findNode(nid);
          if (!n) return;
          var orig = origPositions[nid] || { x: 0, y: 0 };
          var newX = Math.max(0, orig.x + ddx);
          var newY = Math.max(0, orig.y + ddy);
          if (!n.position) n.position = {};
          n.position.x = newX;
          n.position.y = newY;
          var nEl = self.nodeElements.get(nid);
          if (nEl) {
            nEl.style.left = newX + 'px';
            nEl.style.top = newY + 'px';
          }
          self._reRenderEdgeForNode(nid);
        });
        self._updateMinimap();
      };
      var onUp = function () {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (moved && self.onNodeDragEnd) {
          dragNodeIds.forEach(function (nid) {
            var n = self._findNode(nid);
            if (n) self.onNodeDragEnd(nid, n);
          });
        }
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  // ── Node type renderers ───────────────────────────────────────────────────

  _renderStatePool(node, pos) {
    var params = node.params || {};
    var dzhAttr = params.dzh_attr || {};
    var bits = dzhAttr.bits || dzhAttr;
    var clr = dzhColorToCss(params.clr, '#FF1493'); // fallback: DZH原生默认深粉红DeepPink(超赢追踪/整理 clr=-1时截图验证:非纯品红#ff00ff,而是偏红粉紫#FF1493系)
    var selected = (this.selectedNodeId === node.id || this.selectedNodeIds.indexOf(node.id) !== -1) ? ' selected' : '';
    var attrInt = params.attr_int || params.attr || 0;
    var w = pos.width || DZH_NODE_SIZES.stock_state_pool.width;
    var h = pos.height || DZH_NODE_SIZES.stock_state_pool.height;
    if (node.position) { node.position.width = w; node.position.height = h; }

    // DZH原生渲染: 标题栏背景=clr色(与边框同色), 标题栏文字=白色, 主体背景=纯黑, 边框=clr色
    var titleBarBg = clr;           // 标题栏与边框同色（均来自cell.clr）
    var bodyBg = '#000000';       // DZH原生状态池主体默认纯黑
    // DZH原生边框: 上下左右均使用clr色 (2px实线边框)
    var borderStyle = 'border:' + DZH_BORDER_WIDTHS.state_pool + 'px solid ' + clr + ';';
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;width:' + w + 'px;' +
      'height:' + h + 'px;background:' + bodyBg + ';' + borderStyle + 'position:absolute;box-sizing:border-box;';

    var el = this._createNodeEl(node, 'node-state-pool' + selected, style);
    var badgesHtml = '';
    if (bits.record_history) badgesHtml += '<span class="nsp-badge badge-history">历史</span>';
    if (bits.show_overview) badgesHtml += '<span class="nsp-badge badge-overview">总览</span>';
    if (bits.calc_profit_from_prev) badgesHtml += '<span class="nsp-badge badge-profit">收益</span>';
    if (bits.simple_intermediate) badgesHtml += '<span class="nsp-badge badge-simple">简易</span>';
    var stocks = params.stocks || [];
    var stockCountHtml = stocks.length > 0 ? '<div class="nsp-stock-count">' + stocks.length + '只</div>' : '';
    var holdSecHtml = '';
    var holdSec = parseInt(params.hold_sec) || 0;
    if (holdSec > 0) {
      var days = Math.round(holdSec / 86400);
      if (days > 0) holdSecHtml = '<div class="nsp-hold-days">' + days + '天</div>';
      else if (holdSec >= 3600) holdSecHtml = '<div class="nsp-hold-days">' + Math.round(holdSec / 3600) + '时</div>';
      else if (holdSec >= 60) holdSecHtml = '<div class="nsp-hold-days">' + Math.round(holdSec / 60) + '分</div>';
    }
    // DZH原生渲染: 标题栏背景=clr色(与边框同色), 标题栏文字=白色, 主体背景=纯黑, 边框=clr色
    var titleBarHtml = '<div class="nsp-title-bar" style="background:' + titleBarBg + ';color:#ffffff;padding:2px 4px;font-size:' + DZH_FONT_SIZES.title_bar + 'px;font-weight:bold;white-space:nowrap;text-align:center;border-bottom:1px solid ' + clr + ';">' + escHtml(node.label) + '</div>';
    el.innerHTML =
      '<div class="nsp-header">' + titleBarHtml +
      (badgesHtml ? '<div class="nsp-badges">' + badgesHtml + '</div>' : '') +
      stockCountHtml + '</div>' +
      (holdSecHtml ? '<div class="nsp-hold-sec">' + holdSecHtml + '</div>' : '');

    // 预取股票数据供运行模式使用（静默获取，不渲染）
    var stockCount = stocks.length;
    if (!params._stockData && !params._stockDataFetching && stockCount > 0) { this._fetchStockData(node.id); }

    if (this._runMode) {
      var stockOverlay = document.createElement('div');
      stockOverlay.className = 'node-stock-overlay';
      stockOverlay.setAttribute('data-stock-overlay', node.id);
      el.appendChild(stockOverlay);
      this._renderStockTable(node, stockOverlay);
    }
  }

  _renderCondition(node, pos) {
    var params = node.params || {};
    var clr = dzhColorToCss(params.clr, '#f39c12'); // fallback: UI默认色(NODE_TYPE_DEFAULTS.transfer_condition)
    var selected = (this.selectedNodeId === node.id || this.selectedNodeIds.indexOf(node.id) !== -1) ? ' selected' : '';
    var MIN_COND_W = 22, MIN_COND_H = 20;  // DZH原生条件三角形最小尺寸(接近XML原始25×24)
    var w = Math.max(pos.width || DZH_NODE_SIZES.transfer_condition.width, MIN_COND_W);
    var h = Math.max(pos.height || DZH_NODE_SIZES.transfer_condition.height, MIN_COND_H);
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;width:' + w + 'px;height:' + h + 'px;position:absolute;';
    // DZH原生条件三角形: 无独立背景色(透明), 整个形状由SVG polygon clr色填充
    // 注意: 之前错误地使用DEFAULT_BACKCOLOR=#000000导致条件节点显示为黑色块
    var condBg = dzhColorToCss(params.backcolor, 'transparent');
    style += 'background:' + condBg + ';';
    // 强制覆盖CSS中.node-condition的min-width/min-height，确保XML原始尺寸生效
    style += 'min-width:' + w + 'px !important;min-height:' + h + 'px !important;';
    var el = this._createNodeEl(node, 'node-condition shape-triangle' + selected, style);
    var sortType = params.sorttype;
    var inditype = params.inditype;
    var iconText = '设';
    if (inditype !== undefined && inditype !== null && inditype !== '') {
      if (String(inditype) === '0' || inditype === 0) iconText = '指';
      else if (String(inditype) === '1' || inditype === 1) iconText = '引';
    }
    var sortLabel = '';
    if (sortType !== undefined && sortType !== null && String(sortType).trim() !== '') {
      var st = String(sortType).trim();
      if (/^-?\d+$/.test(st)) {
        var n = parseInt(st);
        sortLabel = ' TOP' + Math.abs(n);
      } else {
        sortLabel = ' ' + escHtml(st);
      }
    }
    var stksCount = (params.stks && params.stks.length) ? params.stks.length : 0;
    var stksBadge = '';
    if (stksCount > 0) {
      stksBadge = '<span class="nc-stks-badge" style="position:absolute;top:-6px;right:-6px;background:#e67e22;color:#fff;font-size:10px;font-weight:bold;line-height:1;padding:1px 5px;border-radius:8px;min-width:14px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.3);z-index:2;">' + stksCount + '</span>'; // #e67e22: UI默认徽章色
    }
    el.innerHTML =
      '<svg class="shape-svg" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
        '<polygon points="0,0 0,' + h + ' ' + w + ',' + (h/2) + '" fill="' + clr + '"/>' +
      '</svg>' +
      '<div class="shape-content shape-content-triangle" style="position:relative;">' +
        '<span class="nc-icon">' + iconText + '</span>' +
        '<span class="nc-sort">' + sortLabel + '</span>' +
        stksBadge +
      '</div>';
  }

  _renderCandidate(node, pos) {
    var params = node.params || {};
    var rawClr = dzhColorToCss(params.clr, '');
    var clr = rawClr || '#DAA520';  // fallback: DZH原生默认金黄色(与NODE_TYPE_DEFAULTS一致)

    // DZH原生模式: 备选池显示为3D圆柱体
    var MIN_CAND_W = 60, MIN_CAND_H = 75;  // DZH原生最小圆柱体尺寸(接近XML原始67×78)
    var w = Math.max(pos.width || DZH_NODE_SIZES.market_source.width, MIN_CAND_W);
    var h = Math.max(pos.height || DZH_NODE_SIZES.market_source.height, MIN_CAND_H);
    if (node.position) { node.position.width = w; node.position.height = h; }

    // 圆柱体参数
    var ry = Math.min(w * 0.22, 14);  // 椭圆半径
    var bodyH = h - 2 * ry;

    // 渐变色生成（基于clr）
    var lighterClr = lightenColor(clr, 25);
    var darkerClr = darkenColor(clr, 25);
    var darkestClr = darkenColor(clr, 40);

    var cstyle = 'left:' + pos.x + 'px;top:' + pos.y + 'px;width:' + w + 'px;' +
      'height:' + h + 'px;position:absolute;overflow:hidden;';

    var cel = this._createNodeEl(node, 'node-candidate-dzh', cstyle);

    // 3D圆柱体DOM结构（使用CSS中已有的.cyl-top/.cyl-body/.cyl-bottom样式）
    cel.innerHTML =
      '<div class="cyl-top" style="width:100%;height:' + ry + 'px;border-radius:50%;background:linear-gradient(to bottom,' + lighterClr + ',' + clr + ');flex-shrink:0;"></div>' +
      '<div class="cyl-body" style="width:100%;height:' + bodyH + 'px;display:flex;flex-direction:column;align-items:center;justify-content:center;' +
        'border-left:2px solid ' + darkerClr + ';' +
        'border-right:2px solid ' + darkenColor(clr, 15) + ';' +
        'background:linear-gradient(to right,' + darkestClr + ' 0%,' + darkenColor(clr, 30) + ' 15%,' + clr + ' 50%,' + darkenColor(clr, 30) + ' 85%,' + darkestClr + ' 100%);">' +
        '<div class="cyl-name" style="font-size:11px;font-weight:600;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,0.7);text-align:center;word-break:break-all;padding:0 4px;">' + escHtml(node.label || params.text || '') + '</div>' +
      '</div>' +
      '<div class="cyl-bottom" style="width:100%;height:' + (ry * 0.8) + 'px;border-radius:50%;background:linear-gradient(to bottom,' + clr + ',' + darkestClr + ');flex-shrink:0;"></div>';

    return cel;
  }

  _renderDiscard(node, pos) {
    var params = node.params || {};
    var clr = dzhColorToCss(params.clr, '#c0392b'); // fallback: UI默认色(NODE_TYPE_DEFAULTS.discard_pool)
    var selected = (this.selectedNodeId === node.id || this.selectedNodeIds.indexOf(node.id) !== -1) ? ' selected' : '';
    // 背景色严格来自XML的backcolor属性，未提供时使用通用默认值
    var discardBg = dzhColorToCss(params.backcolor, DEFAULT_BACKCOLOR);
    var discardBorder = 'border:' + DZH_BORDER_WIDTHS.discard + 'px solid ' + clr + ';';
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;width:' + (pos.width || DZH_NODE_SIZES.discard_pool.width) + 'px;height:' + (pos.height || DZH_NODE_SIZES.discard_pool.height) + 'px;background:' + discardBg + ';' + discardBorder + 'position:absolute;';
    var el = this._createNodeEl(node, 'node-discard' + selected, style);
    el.innerHTML = '<span class="nd-icon">✕</span><span class="nd-label">' + escHtml(node.label || '') + '</span>';
  }

  _getLabelStyleByAttr(attrInt, baseFontSize) {
    // DZH type=1 标签的attr值映射到字体样式（通用，适用于所有股票池）
    var style = '';
    if (attrInt <= 0) {
      // 默认样式
      style += 'font-size:' + baseFontSize + 'px;';
    } else if (attrInt >= 1 && attrInt <= 31) {
      // 小标题/说明样式 (attr=19,21,22,23,25)
      style += 'font-size:' + Math.max(baseFontSize, 12) + 'px;font-weight:bold;';
    } else if (attrInt >= 32 && attrInt <= 63) {
      // 主标题样式 (attr=41,55,57)
      style += 'font-size:' + Math.max(baseFontSize + 2, 14) + 'px;font-weight:bold;';
    } else if (attrInt >= 64 && attrInt <= 95) {
      // 描述/副标题样式 (attr=85,86,87,89,90,91,92,95)
      style += 'font-size:' + Math.max(baseFontSize - 1, 10) + 'px;';
    } else if (attrInt >= 96 && attrInt <= 127) {
      // 特殊说明样式 (attr=98,118)
      style += 'font-size:' + Math.max(baseFontSize - 2, 9) + 'px;';
    } else {
      style += 'font-size:' + baseFontSize + 'px;';
    }
    return style;
  }

  _renderLabel(node, pos) {
    var params = node.params || {};
    var isTdx = node.type === 'tdx_text_label' || node.type === 'tdx_deco_text';
    var selected = (this.selectedNodeId === node.id || this.selectedNodeIds.indexOf(node.id) !== -1) ? ' selected' : '';
    var attrInt = params.attr_int || params.attr || 0;
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;position:absolute;';
    if (pos.width) style += 'width:' + pos.width + 'px;';
    var baseFontSize = 12;
    if (pos.height) {
      style += 'height:' + pos.height + 'px;';
      baseFontSize = Math.max(10, Math.min(Math.round(pos.height * 0.55), 20));
    }

    if (isTdx) {
      // TDX: clr=填充色/边框色, clrtext=文字颜色, solid=是否填充
      var clr = dzhColorToCss(params.clr, '');
      var clrtext = dzhColorToCss(params.clrtext, '');
      var solid = params.solid !== undefined ? params.solid : 0;
      if (solid === 1 || solid === '1') {
        if (clr) style += 'background:' + clr + ';';
      } else {
        if (clr) style += 'border:1px solid ' + clr + ';';
      }
      if (clrtext) style += 'color:' + clrtext + ';';
      style += 'font-size:' + baseFontSize + 'px;';
    } else {
      // DZH: clr=文字颜色，backcolor=背景颜色，attr控制字体样式
      var clr = dzhColorToCss(params.clr, '');
      var backcolor = dzhColorToCss(params.backcolor, '');
      if (clr) style += 'color:' + clr + ';';
      else style += 'color:' + DEFAULT_LABEL_COLOR + ';';
      if (backcolor && backcolor !== 'transparent') style += 'background:' + backcolor + ';';
      style += this._getLabelStyleByAttr(attrInt, baseFontSize);
    }

    var urlHint = '';
    var urlClass = '';
    if (params.url && String(params.url).trim() !== '') {
      urlClass = ' label-has-url';
      urlHint = ' title="' + escHtml(params.url) + '"';
    }
    var el = this._createNodeEl(node, 'node-label' + urlClass + selected, style);
    el.innerHTML = '<span' + urlHint + '>' + escHtml(params.text || node.label || '') + '</span>';
  }

  _renderContainer(node, pos) {
    var params = node.params || {};
    var isTdx = node.type === 'tdx_container';
    var attrInt = params.attr_int || params.attr || 0;
    var clr = dzhColorToCss(params.clr, '');
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;width:' + pos.width + 'px;height:' + pos.height + 'px;position:absolute;';

    if (isTdx) {
      // TDX: clr=填充色, clrtext=文字颜色, solid=是否填充
      var clrtext = dzhColorToCss(params.clrtext, '');
      var solid = params.solid !== undefined ? params.solid : 0;
      if (solid === 1 || solid === '1') {
        if (clr) style += 'background:' + clr + ';';
      } else {
        if (clr) style += 'border:2px solid ' + clr + ';';
      }
      if (clrtext) style += 'color:' + clrtext + ';';
    } else {
      // DZH: 容器默认显示灰色虚线边框, 背景透明
      // clr指定时作为边框色(极少使用), 否则使用默认灰色虚线
      style += 'background:transparent;';
      var containerBorderClr = (clr && clr !== 'transparent') ? clr : CONTAINER_DASHED_COLOR;
      style += 'border:' + DZH_BORDER_WIDTHS.container_dashed + 'px dashed ' + containerBorderClr + ';';
    }

    var el = document.createElement('div');
    el.className = 'node-container';
    el.setAttribute('data-node-id', node.id);
    el.style.cssText = style;
    var self = this;
    el.addEventListener('click', function (e) { e.stopPropagation(); self.selectNode(node.id); if (self.onNodeClick) self.onNodeClick(node.id, node); });
    el.addEventListener('dblclick', function (e) { e.stopPropagation(); if (self.onNodeDoubleClick) self.onNodeDoubleClick(node.id, node); });
    el.title = params.text || node.label || '';
    this.nodesEl.appendChild(el);
    this.nodeElements.set(node.id, el);
  }

  _renderColumn(node, pos) {
    var params = node.params || {};
    var clr = dzhColorToCss(params.clr, '');
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;position:absolute;';
    if (pos.width) style += 'width:' + pos.width + 'px;';
    if (pos.height) style += 'height:' + pos.height + 'px;';
    // 背景色严格来自XML的backcolor属性，未提供时使用通用默认值
    var colBg = dzhColorToCss(params.backcolor, DEFAULT_BACKCOLOR);
    style += 'background:' + colBg + ';';
    if (clr && clr !== 'transparent') style += 'border:' + DZH_BORDER_WIDTHS.column + 'px solid ' + clr + ';';
    var el = document.createElement('div');
    el.className = 'node-column';
    el.setAttribute('data-node-id', node.id);
    el.style.cssText = style;
    var self = this;
    el.addEventListener('click', function (e) { e.stopPropagation(); self.selectNode(node.id); if (self.onNodeClick) self.onNodeClick(node.id, node); });
    el.addEventListener('dblclick', function (e) { e.stopPropagation(); if (self.onNodeDoubleClick) self.onNodeDoubleClick(node.id, node); });
    el.title = params.text || node.label || '';
    this.nodesEl.appendChild(el);
    this.nodeElements.set(node.id, el);
  }

  _renderArrowDeco(node, pos) {
    var params = node.params || {};
    var clr = dzhColorToCss(params.clr, '');
    var selected = (this.selectedNodeId === node.id || this.selectedNodeIds.indexOf(node.id) !== -1) ? ' selected' : '';
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;position:absolute;';
    if (pos.width) style += 'width:' + pos.width + 'px;';
    if (pos.height) style += 'height:' + pos.height + 'px;';
    if (clr) style += 'color:' + clr + ';';
    var el = this._createNodeEl(node, 'node-arrow-deco' + selected, style);
    var text = params.text || node.label || '';
    el.innerHTML = '<span class="arrow-icon">\u27A4</span>' + (text ? '<span class="arrow-text">' + escHtml(text) + '</span>' : '');
  }

  _renderExecutionOrder(node, pos) {
    var params = node.params || {};
    var clr = dzhColorToCss(params.clr, '');
    var selected = (this.selectedNodeId === node.id || this.selectedNodeIds.indexOf(node.id) !== -1) ? ' selected' : '';
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;width:' + (pos.width || 28) + 'px;height:' + (pos.height || 28) + 'px;position:absolute;overflow:hidden;';
    if (clr) style += 'color:' + clr + ';';
    var el = this._createNodeEl(node, 'node-exec-order' + selected, style);
    el.innerHTML = '<span class="eo-number">' + escHtml(node.label || '1') + '</span>';
  }

  _renderGenericNode(node, pos) {
    var params = node.params || {};
    var clr = '#333355'; // UI默认通用节点边框色
    // 背景色严格来自XML的backcolor属性，未提供时使用通用默认值
    var bgColor = dzhColorToCss(params.backcolor, DEFAULT_BACKCOLOR);
    var selected = (this.selectedNodeId === node.id || this.selectedNodeIds.indexOf(node.id) !== -1) ? ' selected' : '';
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;width:' + (pos.width || 100) + 'px;min-height:' + (pos.height || 50) + 'px;background:' + bgColor + ';border-radius:6px;border:1px solid #444466;display:flex;align-items:center;justify-content:center;color:#ccc;font-size:12px;position:absolute;'; // #444466,#ccc: UI默认通用节点配色
    var el = this._createNodeEl(node, 'dzh-node' + selected, style);
    el.innerHTML = escHtml(node.label) + '<br><small style="opacity:0.5">' + node.type + '</small>';
  }

  _renderTdxCandidate(node, pos) {
    var params = node.params || {};
    var clr = dzhColorToCss(params.clr, '#3498db'); // fallback: UI默认色(TDX候选节点)
    var clrtext = dzhColorToCss(params.clrtext, '#ffffff'); // fallback: DZH原生文字默认白色
    var solid = params.solid !== undefined ? params.solid : 1;
    var selected = (this.selectedNodeId === node.id || this.selectedNodeIds.indexOf(node.id) !== -1) ? ' selected' : '';
    var isHollow = (solid === 0 || solid === '0');
    var w = pos.width || 250;
    var h = pos.height || 180;
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;width:' + w + 'px;height:' + h + 'px;color:' + clrtext + ';position:absolute;';
    var el = this._createNodeEl(node, 'node-tdx-candidate shape-cylinder' + selected, style);
    var stockCount = 0;
    if (params.tdx_stocks && Array.isArray(params.tdx_stocks)) stockCount = params.tdx_stocks.length;
    else if (params.stocks && Array.isArray(params.stocks)) stockCount = params.stocks.length;
    var spinfo = params.tdx_spinfo || {};
    var spinfoHtml = spinfo.type ? '<div class="tdx-spinfo-type">类型: ' + spinfo.type + '</div>' : '';
    // Cylinder SVG: rect body (gradient) + bottom ellipse (filled) + top ellipse (filled, lighter)
    var ry = Math.min(w * 0.15, 18);
    var stroke = isHollow ? clr : 'none';
    var sw = isHollow ? 2 : 0;
    var topFill = isHollow ? 'none' : lightenColor(clr, 20);
    var bodyFill = isHollow ? 'none' : 'url(#cylGrad' + node.id + ')';
    var bodyStroke = isHollow ? clr : 'none';
    el.innerHTML =
      '<svg class="shape-svg" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
        '<defs>' +
          '<linearGradient id="cylGrad' + node.id + '" x1="0" y1="0" x2="1" y2="0">' +
            '<stop offset="0%" stop-color="' + darkenColor(clr, 30) + '"/>' +
            '<stop offset="10%" stop-color="' + darkenColor(clr, 22) + '"/>' +
            '<stop offset="20%" stop-color="' + darkenColor(clr, 14) + '"/>' +
            '<stop offset="30%" stop-color="' + darkenColor(clr, 6) + '"/>' +
            '<stop offset="38.2%" stop-color="' + lightenColor(clr, 25) + '"/>' +
            '<stop offset="46%" stop-color="' + lightenColor(clr, 10) + '"/>' +
            '<stop offset="56%" stop-color="' + clr + '"/>' +
            '<stop offset="70%" stop-color="' + darkenColor(clr, 8) + '"/>' +
            '<stop offset="82%" stop-color="' + darkenColor(clr, 18) + '"/>' +
            '<stop offset="100%" stop-color="' + darkenColor(clr, 30) + '"/>' +
          '</linearGradient>' +
        '</defs>' +
        '<rect x="0" y="' + ry + '" width="' + w + '" height="' + (h - 2*ry) + '" fill="' + bodyFill + '" stroke="' + bodyStroke + '" stroke-width="' + sw + '"/>' +
        '<ellipse cx="' + (w/2) + '" cy="' + (h - ry) + '" rx="' + (w/2) + '" ry="' + ry + '" fill="' + bodyFill + '" stroke="' + bodyStroke + '" stroke-width="' + sw + '"/>' +
        '<ellipse cx="' + (w/2) + '" cy="' + ry + '" rx="' + (w/2) + '" ry="' + ry + '" fill="' + topFill + '" stroke="' + bodyStroke + '" stroke-width="' + sw + '"/>' +
      '</svg>' +
      '<div class="shape-content">' +
        '<div class="tdx-badge tdx-badge-candidate">TDX候选</div>' +
        '<div class="tdx-title">' + escHtml(node.label) + '</div>' +
        spinfoHtml +
        '<div class="tdx-stock-count">' + stockCount + ' 只</div>' +
      '</div>';
  }

  _renderTdxStatePool(node, pos) {
    var params = node.params || {};
    // 视觉属性（颜色/文字色）严格来自XML，禁止运行时硬编码覆盖
    var clr = dzhColorToCss(params.clr, '#27ae60'); // fallback: UI默认色(NODE_TYPE_DEFAULTS.tdx_state_pool)
    var clrtext = dzhColorToCss(params.clrtext, '#ffffff'); // fallback: DZH原生文字默认白色
    var solid = params.solid !== undefined ? params.solid : 1;
    var selected = (this.selectedNodeId === node.id || this.selectedNodeIds.indexOf(node.id) !== -1) ? ' selected' : '';
    var isHollow = (solid === 0 || solid === '0');
    var w = pos.width || 250;
    var h = pos.height || 180;

    // 历史数据通过params注入，视觉属性仍由XML控制
    var historyDate = params.history_date;
    var historyStocks = params.history_stocks || [];
    var isHistoryView = !!(historyDate && historyStocks.length > 0);
    if (isHistoryView) {
      // 高度由内容数量驱动（非视觉硬编码）
      var hvCount = historyStocks.length;
      h = Math.max(180, 95 + Math.min(hvCount, 12) * 22);
    }

    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;width:' + w + 'px;height:' + h + 'px;position:absolute;overflow:hidden;';
    if (isHollow) {
      style += 'background:transparent;border:2px solid ' + clr + ';color:' + clrtext + ';';
    } else {
      style += 'background:' + clr + ';color:' + clrtext + ';';
    }
    var el = this._createNodeEl(node, 'node-tdx-state-pool shape-rectangle' + selected + (isHistoryView ? ' history-view-mode' : ''), style);
    var stockCount = 0;
    if (params.stocks && Array.isArray(params.stocks)) stockCount = params.stocks.length;
    var psatt = params.tdx_psatt || {};
    var badgesHtml = '';
    if (psatt.baimpool) badgesHtml += '<span class="tdx-psatt-badge">目标池</span>';
    if (psatt.btip) badgesHtml += '<span class="tdx-psatt-badge">弹窗</span>';
    if (psatt.bdel) badgesHtml += '<span class="tdx-psatt-badge">自动删除</span>';

    var innerHtml =
      '<div class="tdx-badge tdx-badge-state">TDX状态</div>' +
      '<div class="tdx-title">' + escHtml(node.label) + '</div>' +
      (badgesHtml ? '<div class="tdx-badges">' + badgesHtml + '</div>' : '');

    if (isHistoryView) {
      var hvStockCount = params.history_stockCount || historyStocks.length;
      innerHtml += '<div class="tdx-history-label">' + escHtml(historyDate) + ' · ' + hvStockCount + '只</div>';
      innerHtml += '<div class="tdx-history-stocks">';
      var displayStocks = historyStocks.slice(0, 12); // 最多显示12条
      for (var i = 0; i < displayStocks.length; i++) {
        var s = displayStocks[i];
        var rate = parseFloat(s.maxrate) || 0;
        var rateCls = rate >= 0 ? 'hv-up' : 'hv-down';
        var rateSign = rate >= 0 ? '+' : '';
        innerHtml += '<div class="hv-row">';
        innerHtml += '<span class="hv-code">' + escHtml(s.code) + '</span>';
        innerHtml += '<span class="hv-name">' + escHtml(s.name) + '</span>';
        innerHtml += '<span class="hv-price">' + escHtml(s.inprice) + '</span>';
        innerHtml += '<span class="hv-rate ' + rateCls + '">' + rateSign + escHtml(s.maxrate) + '%</span>';
        innerHtml += '</div>';
      }
      if (hvStockCount > 12) {
        innerHtml += '<div class="hv-more">... 等 ' + hvStockCount + ' 只</div>';
      }
      innerHtml += '</div>';
    } else {
      innerHtml += '<div class="tdx-stock-count">' + stockCount + ' 只</div>';
    }

    el.innerHTML = innerHtml;

    // ── 运行模式：显示股票表格覆盖层（与非TDX _renderStatePool一致）──
    if (this._runMode) {
      var stockOverlay = document.createElement('div');
      stockOverlay.className = 'node-stock-overlay';
      stockOverlay.setAttribute('data-stock-overlay', node.id);
      el.appendChild(stockOverlay);
      this._renderStockTable(node, stockOverlay);
    }
  }

  _renderTdxCondition(node, pos) {
    var params = node.params || {};
    var clr = dzhColorToCss(params.clr, '#f39c12'); // fallback: UI默认色(NODE_TYPE_DEFAULTS.tdx_condition)
    var clrtext = dzhColorToCss(params.clrtext, '#ffffff'); // fallback: DZH原生文字默认白色
    var solid = params.solid !== undefined ? params.solid : 1;
    var selected = (this.selectedNodeId === node.id || this.selectedNodeIds.indexOf(node.id) !== -1) ? ' selected' : '';
    var isHollow = (solid === 0 || solid === '0');
    var w = pos.width || 50;
    var h = pos.height || 30;
    var style = 'left:' + pos.x + 'px;top:' + pos.y + 'px;width:' + w + 'px;height:' + h + 'px;position:absolute;';
    var el = this._createNodeEl(node, 'node-tdx-condition shape-triangle' + selected, style);
    // Disabled visual feedback
    var isDisabled = !!params.disabled;
    if (isDisabled) {
      el.classList.add('node-disabled');
    }
    var func = params.tdx_func || {};
    var nset = func.nset;
    var iconText = '设';
    if (nset === 0) iconText = '自';
    else if (nset === 1) iconText = '系';
    else if (nset === 4) iconText = '排';
    else if (nset === 5) iconText = '直';
    var condName = node.label || func.accode || '';
    var fill = isHollow ? 'none' : clr;
    var stroke = isHollow ? clr : 'none';
    var sw = isHollow ? 2 : 0;
    if (isDisabled) {
      fill = 'rgba(128,128,128,0.3)'; // UI默认禁用状态填充色
      stroke = '#888'; // UI默认禁用状态边框色
      sw = 2;
    }
    // Triangle pointing right: top-left → bottom-left → right-center
    el.innerHTML =
      '<svg class="shape-svg" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
        '<polygon points="0,0 0,' + h + ' ' + w + ',' + (h/2) + '" fill="' + fill + '" stroke="' + stroke + '" stroke-width="' + sw + '"/>' +
      '</svg>' +
      '<div class="shape-content shape-content-triangle">' +
        '<div class="tdx-cond-icon">' + iconText + '</div>' +
        '<div class="tdx-cond-name">' + escHtml(condName) + '</div>' +
        (isDisabled ? '<div class="disabled-overlay"></div>' : '') +
      '</div>';
  }

  // ── Edge rendering ────────────────────────────────────────────────────────

  _renderEdges() {
    this._edgeGroup.innerHTML = '';
    this._edgeLabelGroup.innerHTML = '';
    // Clean up dynamic arrow markers from previous render
    var defs = this.svgEl.querySelector('defs');
    if (defs) {
      var dynamicMarkers = defs.querySelectorAll('[id^="arrow-dynamic-"]');
      dynamicMarkers.forEach(function(m) { m.remove(); });
    }
    this._edgeElements.clear();

    var self = this;
    (this._edges || []).forEach(function (edge) {
      self._renderEdge(edge);
    });
  }

  _renderEdge(edge) {
    var srcId = edge.source ? edge.source.node_id : null;
    var tgtId = edge.target ? edge.target.node_id : null;
    if (!srcId || !tgtId) return;

    var sp = this._getNodeOutPoint(srcId);
    var tp = this._getNodeInPoint(tgtId);
    if (!sp || !tp) return;

    var params = edge.params || {};
    var modeInfo = this._getEdgeModeInfo(params, tgtId);
    var midVal = parseFloat(params.mid) || 0;
    var ns = 'http://www.w3.org/2000/svg';
    var self = this;
    var d = this._buildEdgePath(sp, tp, midVal);

    // Hit area
    var hitPath = document.createElementNS(ns, 'path');
    hitPath.setAttribute('d', d);
    hitPath.setAttribute('class', 'edge-hit');
    (function (eid) {
      hitPath.addEventListener('click', function (evt) {
        evt.stopPropagation();
        if (self._showExecOrder) {
          self._assignExecOrder(eid);
        } else {
          self.selectEdge(eid);
          if (self.onEdgeClick) self.onEdgeClick(eid, edge);
        }
      });
    })(edge.id);
    this._edgeGroup.appendChild(hitPath);

    // Visible path
    var path = document.createElementNS(ns, 'path');
    path.setAttribute('d', d);
    path.setAttribute('class', 'edge-path');
    path.setAttribute('stroke', modeInfo.color);
    path.setAttribute('stroke-width', String(modeInfo.width));
    path.setAttribute('style', '--stroke-w: ' + modeInfo.width + 'px');
    // When edge color is overridden by XML clr, use a color-matched arrow marker
    if (modeInfo.color !== EDGE_STRATEGY_COLORS[modeInfo.name]) {
      var markerId = 'arrow-dynamic-' + edge.id;
      // Check if dynamic marker already exists
      var existingMarker = this.svgEl.querySelector('#' + markerId);
      if (!existingMarker) {
        var marker = document.createElementNS(ns, 'marker');
        marker.setAttribute('id', markerId);
        marker.setAttribute('viewBox', '0 0 10 10');
        marker.setAttribute('refX', '9');
        marker.setAttribute('refY', '5');
        marker.setAttribute('markerWidth', '6');
        marker.setAttribute('markerHeight', '6');
        marker.setAttribute('orient', 'auto-start-reverse');
        var markerPath = document.createElementNS(ns, 'path');
        markerPath.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
        markerPath.setAttribute('fill', modeInfo.color);
        marker.appendChild(markerPath);
        this.svgEl.querySelector('defs').appendChild(marker);
      }
      path.setAttribute('marker-end', 'url(#' + markerId + ')');
    } else {
      path.setAttribute('marker-end', 'url(#' + modeInfo.marker + ')');
    }
    path.setAttribute('data-edge-id', edge.id);

    // TDX: tran=1 means move (dashed)
    // DZH fidelity: move/force strategy edges are also dashed
    var isMoveEdge = (params.tran !== undefined && parseInt(params.tran) === 1)
      || modeInfo.name === 'move' || modeInfo.name === 'force' || modeInfo.name === 'reject';
    if (isMoveEdge) {
      path.setAttribute('stroke-dasharray', '6,3');
    }

    if (this.selectedEdgeId === edge.id) {
      path.classList.add('edge-selected');
    }

    (function (eid) {
      path.addEventListener('click', function (evt) {
        evt.stopPropagation();
        if (self._showExecOrder) {
          self._assignExecOrder(eid);
        } else {
          self.selectEdge(eid);
          if (self.onEdgeClick) self.onEdgeClick(eid, edge);
        }
      });
    })(edge.id);

    this._edgeGroup.appendChild(path);

    // Edge label — 条件边显示时间间隔，无条件边只显示线宽
    var mx, my;
    if (midVal !== 0) {
      mx = (sp.x + tp.x) / 2 + midVal * 2;
      my = (sp.y + tp.y) / 2;
    } else {
      mx = (sp.x + tp.x) / 2;
      my = (sp.y + tp.y) / 2;
    }

    // 判断边类型：按源节点区分（全量XML分析602条边验证）
    //   条件边(conditional)：源为备选池/状态池/数据源 → 有时间间隔属性(interval_sec)
    //   无条件边(unconditional)：源为条件节点(公式/筛选) → 直通，只有线宽属性(width)
    var srcNode = this._findNode(srcId);
    var CONDITIONAL_SOURCE_TYPES = [
      'market_source', 'candidate_dzh', 'tdx_candidate',     // 数据源/备选池
      'stock_state_pool', 'state_pool', 'tdx_state_pool'      // 状态池（作为源出发时）
    ];
    var isConditionalEdge = srcNode && CONDITIONAL_SOURCE_TYPES.indexOf(srcNode.type) !== -1;

    // 表驱动：从 params 中读取实际属性值
    var labelText = '';
    if (isConditionalEdge) {
      // 条件边：显示时间间隔
      var interval = params.interval_sec !== undefined ? parseInt(params.interval_sec) : 60;
      if (interval <= 0) labelText = '即时';
      else if (interval < 60) labelText = interval + 's';
      else if (interval < 3600) labelText = Math.round(interval / 60) + 'm';
      else if (interval < 86400) labelText = Math.round(interval / 3600) + 'h';
      else labelText = Math.round(interval / 86400) + 'd';
    } else {
      // 无条件边：只显示线宽（来自 params.size 或 modeInfo.width）
      var edgeWidth = params.size ? parseInt(params.size) : modeInfo.width;
      labelText = 'w=' + edgeWidth;
    }

    var label = document.createElementNS(ns, 'text');
    label.setAttribute('x', mx);
    label.setAttribute('y', my - 6);
    label.setAttribute('text-anchor', 'middle');
    label.setAttribute('fill', modeInfo.color);
    label.setAttribute('font-size', '10');
    label.setAttribute('font-family', 'Microsoft YaHei, sans-serif');
    label.setAttribute('pointer-events', 'none');
    // DZH fidelity: text outline for readability against dark backgrounds
    label.setAttribute('stroke', 'rgba(0,0,0,0.75)');
    label.setAttribute('stroke-width', '3');
    label.setAttribute('paint-order', 'stroke');
    label.textContent = labelText;
    this._edgeLabelGroup.appendChild(label);

    // 执行顺序编号模式：徽标仅渲染在条件边上（源节点是池类型）（DZH§6.2）
    if (this._showExecOrder && this._isConditionEdge(edge)) {
      var orderVal = (params._order !== undefined && params._order !== null) ? params._order : -1;
      var badgeR = 12;
      // 计算源端和目标端徽标位置：从端点向边内侧偏移 24px
      var bdx = tp.x - sp.x;
      var bdy = tp.y - sp.y;
      var blen = Math.sqrt(bdx * bdx + bdy * bdy);
      var offset = 24;
      var srcBx, srcBy, tgtBx, tgtBy;
      if (blen > 0) {
        srcBx = sp.x + (bdx / blen) * offset;
        srcBy = sp.y + (bdy / blen) * offset;
        tgtBx = tp.x - (bdx / blen) * offset;
        tgtBy = tp.y - (bdy / blen) * offset;
      } else {
        srcBx = sp.x; srcBy = sp.y;
        tgtBx = tp.x; tgtBy = tp.y;
      }
      var badgeFill = orderVal === this._execOrderCounter ? '#e8a317' : 'rgba(40,40,70,0.92)';

      // 源端徽标
      var srcCircle = document.createElementNS(ns, 'circle');
      srcCircle.setAttribute('cx', srcBx);
      srcCircle.setAttribute('cy', srcBy);
      srcCircle.setAttribute('r', badgeR);
      srcCircle.setAttribute('fill', badgeFill);
      srcCircle.setAttribute('stroke', '#fff');
      srcCircle.setAttribute('stroke-width', '1.5');
      srcCircle.setAttribute('pointer-events', 'none');
      this._edgeLabelGroup.appendChild(srcCircle);

      var srcText = document.createElementNS(ns, 'text');
      srcText.setAttribute('x', srcBx);
      srcText.setAttribute('y', srcBy + 4);
      srcText.setAttribute('text-anchor', 'middle');
      srcText.setAttribute('fill', '#fff');
      srcText.setAttribute('font-size', '11');
      srcText.setAttribute('font-weight', 'bold');
      srcText.setAttribute('font-family', 'Microsoft YaHei, sans-serif');
      srcText.setAttribute('pointer-events', 'none');
      srcText.textContent = (orderVal >= 0) ? String(orderVal) : '?';
      this._edgeLabelGroup.appendChild(srcText);

      // 目标端徽标
      var tgtCircle = document.createElementNS(ns, 'circle');
      tgtCircle.setAttribute('cx', tgtBx);
      tgtCircle.setAttribute('cy', tgtBy);
      tgtCircle.setAttribute('r', badgeR);
      tgtCircle.setAttribute('fill', badgeFill);
      tgtCircle.setAttribute('stroke', '#fff');
      tgtCircle.setAttribute('stroke-width', '1.5');
      tgtCircle.setAttribute('pointer-events', 'none');
      this._edgeLabelGroup.appendChild(tgtCircle);

      var tgtText = document.createElementNS(ns, 'text');
      tgtText.setAttribute('x', tgtBx);
      tgtText.setAttribute('y', tgtBy + 4);
      tgtText.setAttribute('text-anchor', 'middle');
      tgtText.setAttribute('fill', '#fff');
      tgtText.setAttribute('font-size', '11');
      tgtText.setAttribute('font-weight', 'bold');
      tgtText.setAttribute('font-family', 'Microsoft YaHei, sans-serif');
      tgtText.setAttribute('pointer-events', 'none');
      tgtText.textContent = (orderVal >= 0) ? String(orderVal) : '?';
      this._edgeLabelGroup.appendChild(tgtText);
    }

    // Selected: 条件边额外显示线宽，无条件边无需重复
    if (this.selectedEdgeId === edge.id && isConditionalEdge) {
      var widthTip = document.createElementNS(ns, 'title');
      widthTip.textContent = '线条宽度: ' + modeInfo.width;
      path.appendChild(widthTip);

      var widthLabel = document.createElementNS(ns, 'text');
      widthLabel.setAttribute('x', mx);
      widthLabel.setAttribute('y', my + 10);
      widthLabel.setAttribute('text-anchor', 'middle');
      widthLabel.setAttribute('fill', modeInfo.color);
      widthLabel.setAttribute('font-size', '9');
      widthLabel.setAttribute('font-family', 'Microsoft YaHei, sans-serif');
      widthLabel.setAttribute('pointer-events', 'none');
      widthLabel.setAttribute('stroke', 'rgba(0,0,0,0.75)');
      widthLabel.setAttribute('stroke-width', '3');
      widthLabel.setAttribute('paint-order', 'stroke');
      widthLabel.textContent = 'w=' + modeInfo.width;
      this._edgeLabelGroup.appendChild(widthLabel);
    }

    this._edgeElements.set(edge.id, { path: path, hitPath: hitPath, label: label, edge: edge });
  }

  // 执行顺序编号模式：点击边分配/交换编号（DZH§6.2 兼容）
  // 仅对条件边分配编号；编号从1开始；所有条件边编号完毕后自动退出
  _assignExecOrder(edgeId) {
    var self = this;
    var edges = this._edges || [];
    if (!edges.length) return;

    var edge = edges.find(function(e) { return e.id === edgeId; });
    if (!edge) return;

    // 仅对条件边分配编号（源节点是池类型的边）
    if (!this._isConditionEdge(edge)) return;

    if (!edge.params) edge.params = {};

    // 条件边数量（用于判断是否全部编号完毕）
    var condCount = edges.filter(function(e) { return self._isConditionEdge(e); }).length;

    var counter = this._execOrderCounter;
    var currentOrder = (edge.params._order !== undefined && edge.params._order !== null) ? edge.params._order : -1;

    if (currentOrder === counter) {
      // 点击当前编号的边：无操作
      return;
    }

    if (currentOrder >= 1 && currentOrder !== counter) {
      // 交换：找到当前 counter 编号的边，将其编号改为该边原编号
      var swapEdge = edges.find(function(e) {
        return e.params && e.params._order === counter;
      });
      if (swapEdge) {
        if (!swapEdge.params) swapEdge.params = {};
        swapEdge.params._order = currentOrder;
      }
    }

    // 分配当前 counter 给该边
    edge.params._order = counter;
    this._execOrderCounter = counter + 1;

    // 所有条件边编号完毕 → 自动退出
    if (this._execOrderCounter > condCount) {
      this._showExecOrder = false;
      this._execOrderCounter = 0;
      // 通知 main.js 退出模式并持久化
      if (this.onExecOrderComplete) this.onExecOrderComplete();
    }

    this.refresh();
  }

  _buildEdgePath(sp, tp, midVal, type) {
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
  }

  setEdgeLineType(type) {
    if (!['bezier', 'orthogonal', 'straight'].includes(type)) return;
    this.edgeLineType = type;
    // Re-render all edges with new line type
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
  }

  _getNodeOutPoint(nodeId) {
    var el = this.nodeElements.get(nodeId);
    if (!el) {
      var node = this._findNode(nodeId);
      if (node) {
        var pos = node.position || { x: 0, y: 0, width: 80, height: 40 };
        return { x: pos.x + (pos.width || 80), y: pos.y + (pos.height || 40) / 2 };
      }
      return null;
    }
    return { x: el.offsetLeft + el.offsetWidth, y: el.offsetTop + el.offsetHeight / 2 };
  }

  _getNodeInPoint(nodeId) {
    var el = this.nodeElements.get(nodeId);
    if (!el) {
      var node = this._findNode(nodeId);
      if (node) {
        var pos = node.position || { x: 0, y: 0, width: 80, height: 40 };
        return { x: pos.x, y: pos.y + (pos.height || 40) / 2 };
      }
      return null;
    }
    return { x: el.offsetLeft, y: el.offsetTop + el.offsetHeight / 2 };
  }

  _getEdgeModeInfo(params, tgtId) {
    var tgtNode = this._findNode(tgtId);
    var isDiscard = tgtNode && (tgtNode.type === 'discard_pool' || tgtNode.dzh_cell_type === 4);
    var info;
    if (params.force_move || params.force_transfer) info = { color: EDGE_STRATEGY_COLORS.force, width: DZH_EDGE_WIDTHS.force, marker: 'arrowForce', name: 'force' };
    else if (params.delete_source && !params.keep_source) info = { color: EDGE_STRATEGY_COLORS.move, width: DZH_EDGE_WIDTHS.move, marker: 'arrowMove', name: 'move' };
    else if (isDiscard) info = { color: EDGE_STRATEGY_COLORS.move, width: DZH_EDGE_WIDTHS.reject, marker: 'arrowMove', name: 'reject' };
    else if (params.output_constituent || params.clear_dest_first) info = { color: EDGE_STRATEGY_COLORS.overwrite, width: DZH_EDGE_WIDTHS.overwrite, marker: 'arrowOverwrite', name: 'overwrite' };
    else if (params.keep_source) info = { color: EDGE_STRATEGY_COLORS.copy, width: DZH_EDGE_WIDTHS.copy, marker: 'arrowCopy', name: 'copy' };
    else info = { color: EDGE_STRATEGY_COLORS.pass, width: DZH_EDGE_WIDTHS.pass, marker: 'arrowPass', name: 'pass_through' };
    // 配置中的clr/size优先
    if (params.clr && String(params.clr) !== '-1') { var c = dzhColorToCss(params.clr, ''); if (c && c !== 'transparent') info.color = c; }
    if (params.size) { var s = parseInt(params.size); if (s > 0) info.width = Math.max(2, Math.min(16, s)); }
    return info;
  }

  _reRenderEdgeForNode(nodeId) {
    var self = this;
    this._edges.forEach(function (edge) {
      if ((edge.source && edge.source.node_id === nodeId) || (edge.target && edge.target.node_id === nodeId)) {
        var existing = self._edgeElements.get(edge.id);
        if (existing) {
          existing.path.remove();
          if (existing.hitPath) existing.hitPath.remove();
          if (existing.label && existing.label.parentNode) existing.label.remove();
          self._edgeElements.delete(edge.id);
        }
        self._renderEdge(edge);
      }
    });
  }

  // ── Selection ─────────────────────────────────────────────────────────────

  selectNode(nodeId) {
    this.selectedNodeId = nodeId;
    this.selectedNodeIds = nodeId ? [nodeId] : [];
    this.selectedEdgeId = null;
    this._syncSelectionStyles();
    if (nodeId) {
      var node = this._findNode(nodeId);
      if (node) this._showResizeHandles(nodeId);
      else this._hideResizeHandles();
    } else {
      this._hideResizeHandles();
    }
  }

  selectEdge(edgeId) {
    this.selectedEdgeId = edgeId;
    this.selectedNodeId = null;
    this.selectedNodeIds = [];
    this._syncSelectionStyles();
    this._hideResizeHandles();
  }

  clearSelection() {
    this.selectedNodeId = null;
    this.selectedEdgeId = null;
    this.selectedNodeIds = [];
    this._syncSelectionStyles();
    this._hideResizeHandles();
  }

  getSelectedNodeId() { return this.selectedNodeId; }
  getSelectedEdgeId() { return this.selectedEdgeId; }
  getSelectedNode() { return this._findNode(this.selectedNodeId); }
  getSelectedEdge() {
    var self = this;
    return this._edges.find(function (e) { return e.id === self.selectedEdgeId; }) || null;
  }

  _syncSelectionStyles() {
    var self = this;
    this.nodeElements.forEach(function (el, nid) {
      el.classList.toggle('selected', nid === self.selectedNodeId || self.selectedNodeIds.indexOf(nid) !== -1);
    });
    this._edgeElements.forEach(function (v, eid) {
      v.path.classList.toggle('edge-selected', eid === self.selectedEdgeId);
    });
  }

  // ── Resize handles ────────────────────────────────────────────────────────

  _attachResizeHandles() {
    var self = this;
    var directions = ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'];
    directions.forEach(function (dir) {
      var handle = document.createElement('div');
      handle.className = 'node-resize-handle node-resize-' + dir;
      switch (dir) {
        case 'nw': handle.style.cssText = 'left:-3.5px;top:-3.5px;pointer-events:auto;'; break;
        case 'n':  handle.style.cssText = 'left:50%;top:-3.5px;transform:translateX(-50%);pointer-events:auto;'; break;
        case 'ne': handle.style.cssText = 'right:-3.5px;top:-3.5px;pointer-events:auto;'; break;
        case 'w':  handle.style.cssText = 'left:-3.5px;top:50%;transform:translateY(-50%);pointer-events:auto;'; break;
        case 'e':  handle.style.cssText = 'right:-3.5px;top:50%;transform:translateY(-50%);pointer-events:auto;'; break;
        case 'sw': handle.style.cssText = 'left:-3.5px;bottom:-3.5px;pointer-events:auto;'; break;
        case 's':  handle.style.cssText = 'left:50%;bottom:-3.5px;transform:translateX(-50%);pointer-events:auto;'; break;
        case 'se': handle.style.cssText = 'right:-3.5px;bottom:-3.5px;pointer-events:auto;'; break;
      }

      handle.addEventListener('mousedown', function (e) {
        e.stopPropagation();
        e.preventDefault();
        var targetNodeId = self._currentResizeNodeId;
        var targetNode = self._findNode(targetNodeId);
        var targetEl = self.nodeElements.get(targetNodeId);
        if (!targetNode || !targetEl) return;
        var pos = targetNode.position || { x: 0, y: 0, width: 110, height: 64 };
        var startClientX = e.clientX;
        var startClientY = e.clientY;
        var origW = pos.width || 110;
        var origH = pos.height || 64;
        var origX = pos.x;
        var origY = pos.y;

        var onMove = function (me) {
          var dx = (me.clientX - startClientX) / self.transform.zoom;
          var dy = (me.clientY - startClientY) / self.transform.zoom;
          var newW = origW, newH = origH, newX = origX, newY = origY;
          if (dir === 'e')  newW = Math.max(40, origW + dx);
          if (dir === 'w') { newW = Math.max(40, origW - dx); newX = origX + origW - newW; }
          if (dir === 's')  newH = Math.max(30, origH + dy);
          if (dir === 'n') { newH = Math.max(30, origH - dy); newY = origY + origH - newH; }
          if (dir === 'se') { newW = Math.max(40, origW + dx); newH = Math.max(30, origH + dy); }
          if (dir === 'sw') { newW = Math.max(40, origW - dx); newX = origX + origW - newW; newH = Math.max(30, origH + dy); }
          if (dir === 'ne') { newW = Math.max(40, origW + dx); newH = Math.max(30, origH - dy); newY = origY + origH - newH; }
          if (dir === 'nw') { newW = Math.max(40, origW - dx); newX = origX + origW - newW; newH = Math.max(30, origH - dy); newY = origY + origH - newH; }
          targetNode.position.x = Math.max(0, newX);
          targetNode.position.y = Math.max(0, newY);
          targetNode.position.width = newW;
          targetNode.position.height = newH;
          targetEl.style.left = targetNode.position.x + 'px';
          targetEl.style.top = targetNode.position.y + 'px';
          targetEl.style.width = newW + 'px';
          targetEl.style.height = newH + 'px';
          self._updateHandlePositions();
          self._reRenderEdgeForNode(targetNode.id);
          self._updateMinimap();
        };
        var onUp = function () {
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      });

      self._resizeHandlesEl.appendChild(handle);
    });
  }

  _showResizeHandles(nodeId) {
    var el = this.nodeElements.get(nodeId);
    var node = this._findNode(nodeId);
    if (!el || !node) return;
    var pos = node.position || { x: 0, y: 0, width: 110, height: 64 };
    this._resizeHandlesEl.style.left = pos.x + 'px';
    this._resizeHandlesEl.style.top = pos.y + 'px';
    this._resizeHandlesEl.style.width = pos.width + 'px';
    this._resizeHandlesEl.style.height = pos.height + 'px';
    this._resizeHandlesEl.style.display = 'block';
    this._resizeHandlesEl.style.pointerEvents = 'none';
    this._currentResizeNodeId = nodeId;
  }

  _hideResizeHandles() {
    if (this._resizeHandlesEl) {
      this._resizeHandlesEl.style.display = 'none';
      this._resizeHandlesEl.style.pointerEvents = 'none';
    }
    this._currentResizeNodeId = null;
  }

  _updateHandlePositions() {
    if (!this._resizeHandlesEl || !this._currentResizeNodeId) return;
    var node = this._findNode(this._currentResizeNodeId);
    if (!node) return;
    var pos = node.position || { x: 0, y: 0, width: 110, height: 64 };
    this._resizeHandlesEl.style.left = pos.x + 'px';
    this._resizeHandlesEl.style.top = pos.y + 'px';
    this._resizeHandlesEl.style.width = pos.width + 'px';
    this._resizeHandlesEl.style.height = pos.height + 'px';
  }

  // ── Minimap ───────────────────────────────────────────────────────────────

  _updateMinimap() {
    var ctx = this._minimapCanvas.getContext('2d');
    var w = 180, h = 120;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = 'rgba(15,15,30,0.9)';
    ctx.fillRect(0, 0, w, h);

    if (!this._nodes.length) return;

    var nodes = this._nodes;
    var xs = nodes.map(function (n) { return (n.position || {}).x || 0; });
    var ys = nodes.map(function (n) { return (n.position || {}).y || 0; });
    var minX = Math.min.apply(null, xs.length ? xs : [0]) - 40;
    var minY = Math.min.apply(null, ys.length ? ys : [0]) - 40;
    var maxX = Math.max.apply(null, xs.length ? xs : [800]) + 160;
    var maxY = Math.max.apply(null, ys.length ? ys : [600]) + 120;
    var vw = maxX - minX || 800;
    var vh = maxY - minY || 600;
    var scale = Math.min(w / vw, h / vh);
    var ox = -minX * scale;
    var oy = -minY * scale;

    var tx = function (x) { return x * scale + ox; };
    var ty = function (y) { return y * scale + oy; };

    // Draw edges
    var edges = this._edges || [];
    var self = this;
    edges.forEach(function (e) {
      var sid = e.source ? e.source.node_id : null;
      var tid = e.target ? e.target.node_id : null;
      var sn = self._findNode(sid);
      var tn = self._findNode(tid);
      if (sn && tn) {
        var sp = sn.position || { x: 0, y: 0, width: 80, height: 40 };
        var tp = tn.position || { x: 0, y: 0, width: 80, height: 40 };
        ctx.strokeStyle = 'rgba(255,255,255,0.2)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(tx(sp.x + (sp.width || 80)), ty(sp.y + (sp.height || 40) / 2));
        ctx.lineTo(tx(tp.x), ty(tp.y + (tp.height || 40) / 2));
        ctx.stroke();
      }
    });

    // Draw nodes
    var typeColors = {};
    Object.keys(NODE_TYPE_DEFAULTS).forEach(function(k) {
      typeColors[k] = NODE_TYPE_DEFAULTS[k].color;
    });
    nodes.forEach(function (n) {
      var p = n.position || { x: 0, y: 0, width: 80, height: 40 };
      ctx.fillStyle = typeColors[n.type] || '#555'; // UI默认缩略图未知节点色
      ctx.fillRect(tx(p.x), ty(p.y), Math.max((p.width || 80) * scale, 3), Math.max((p.height || 40) * scale, 3));
    });

    // Viewport rectangle
    var vpW = this.viewportEl.clientWidth;
    var vpH = this.viewportEl.clientHeight;
    var vpX = (-this.transform.x + minX * scale * this.transform.zoom) / (this.transform.zoom * vw / w);
    var vpY = (-this.transform.y + minY * scale * this.transform.zoom) / (this.transform.zoom * vh / h);
    var vpRW = vpW / this.transform.zoom * scale;
    var vpRH = vpH / this.transform.zoom * scale;

    this._minimapViewport.style.left = vpX + 'px';
    this._minimapViewport.style.top = vpY + 'px';
    this._minimapViewport.style.width = vpRW + 'px';
    this._minimapViewport.style.height = vpRH + 'px';
  }

  _navigateMinimap(mmX, mmY) {
    // Convert minimap coords to canvas coords, then center viewport there
    if (!this._nodes.length) return;
    var nodes = this._nodes;
    var xs = nodes.map(function (n) { return (n.position || {}).x || 0; });
    var ys = nodes.map(function (n) { return (n.position || {}).y || 0; });
    var minX = Math.min.apply(null, xs.length ? xs : [0]) - 40;
    var minY = Math.min.apply(null, ys.length ? ys : [0]) - 40;
    var maxX = Math.max.apply(null, xs.length ? xs : [800]) + 160;
    var maxY = Math.max.apply(null, ys.length ? ys : [600]) + 120;
    var vw = maxX - minX || 800;
    var vh = maxY - minY || 600;
    var scale = Math.min(180 / vw, 120 / vh);

    var canvasX = (mmX / scale) + minX;
    var canvasY = (mmY / scale) + minY;

    this.transform.x = -canvasX * this.transform.zoom + this.viewportEl.clientWidth / 2;
    this.transform.y = -canvasY * this.transform.zoom + this.viewportEl.clientHeight / 2;
    this._applyTransform();
    this._updateMinimap();
  }

  // ── Node helpers ──────────────────────────────────────────────────────────

  _findNode(nodeId) {
    return this._nodes.find(function (n) { return n.id === nodeId; }) || null;
  }

  // 判断边是否为条件边（源节点是池类型：DZH 0/200/202，TDX 7/8）
  _isConditionEdge(edge) {
    if (!edge) return false;
    var srcId = edge.source ? edge.source.node_id : (edge.from || null);
    if (!srcId) return false;
    var srcNode = this._findNode(srcId);
    if (!srcNode) return false;
    var ct = null;
    if (srcNode.dzh_cell_type !== undefined && srcNode.dzh_cell_type !== null) {
      ct = Number(srcNode.dzh_cell_type);
    } else if (srcNode.params && srcNode.params.type !== undefined && srcNode.params.type !== null) {
      ct = Number(srcNode.params.type);
    } else if (srcNode.type !== undefined && srcNode.type !== null) {
      ct = Number(srcNode.type);
    }
    return !isNaN(ct) && [0, 200, 202, 7, 8].indexOf(ct) >= 0;
  }

  _rerenderNode(nodeId) {
    var oldEl = this.nodeElements.get(nodeId);
    if (oldEl && oldEl.parentNode) oldEl.parentNode.removeChild(oldEl);
    this.nodeElements.delete(nodeId);
    // Clean up handle elements for this node
    var keysToRemove = [];
    this._handleElements.forEach(function (el, key) {
      if (key.startsWith(nodeId + '-')) keysToRemove.push(key);
    });
    keysToRemove.forEach(function (k) { this._handleElements.delete(k); }.bind(this));
    var node = this._findNode(nodeId);
    if (node) {
      this._renderNode(node);
      this._reRenderEdgeForNode(nodeId);
    }
  }

  // ── Mode management ───────────────────────────────────────────────────────

  setPoolId(id) { this.poolId = id; }

  setRunMode(enabled) {
    this._runMode = enabled;
    if (enabled) {
      this.disableEditing();
      this.refreshStockTables();
    } else {
      this.enableEditing();
      var overlays = this.nodesEl.querySelectorAll('.node-stock-overlay');
      overlays.forEach(function (o) { o.remove(); });
      this._refreshAllStatePoolNodes();
    }
  }

  disableEditing() {
    this.editable = false;
    this.draggable = false;
    this.connectable = false;
    this.selectable = true;
    this.nodeElements.forEach(function (el) { el.style.cursor = 'default'; });
  }

  enableEditing() {
    this.editable = true;
    this.draggable = true;
    this.connectable = true;
    this.selectable = true;
    this.nodeElements.forEach(function (el) { el.style.cursor = 'move'; });
  }

  setToolClickHandler(fn) { this._toolClickHandler = fn; }

  // ── Stock data ────────────────────────────────────────────────────────────

  _fetchStockData(nodeId) {
    var self = this;
    var node = this._findNode(nodeId);
    if (!node || !node.params) return;
    node.params._stockDataFetching = true;
    var apiUrl = '/api/dzh/cells/' + nodeId + '/stocks?mode=mock';
    fetch(apiUrl)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!node.params) return;
        var stockData = data.data || [];
        if (stockData.length === 0 && node.params.stocks && node.params.stocks.length > 0) {
          stockData = node.params.stocks.map(function (s) {
            return { code: s.label || '', name: s.name || '', latest_price: s.p || '', current_price: s.p || '', enter_price: s.ep || '', change_pct: s.cp || '', profit_pct: s.pp || '' };
          });
        }
        node.params._stockData = stockData;
        delete node.params._stockDataFetching;
        self._rerenderNode(nodeId);
      })
      .catch(function (err) {
        console.error('[_fetchStockData] Failed for node ' + nodeId + ':', err);
        if (node.params) delete node.params._stockDataFetching;
      });
  }

  refreshStockData() {
    this._nodes.forEach(function (node) {
      if (node.type === 'stock_state_pool' && node.params) {
        delete node.params._stockData;
        delete node.params._stockDataFetching;
      }
    });
    this._renderNodes();
  }

  refreshStockTables() {
    var self = this;
    this._nodes.forEach(function (node) {
      if (node.type === 'stock_state_pool' || node.type === 'tdx_state_pool') {
        var overlay = self.nodesEl.querySelector('[data-stock-overlay="' + node.id + '"]');
        if (!overlay) {
          var el = self.nodeElements.get(node.id);
          if (el) {
            overlay = document.createElement('div');
            overlay.className = 'node-stock-overlay';
            overlay.setAttribute('data-stock-overlay', node.id);
            el.appendChild(overlay);
          }
        }
        if (overlay) { self._renderStockTable(node, overlay); }
        if (node.type === 'tdx_state_pool') {
          var el = self.nodeElements.get(node.id);
          if (el) {
            var countEl = el.querySelector('.tdx-stock-count');
            if (countEl) {
              var stocks = (node.params && node.params.stocks) || [];
              countEl.textContent = stocks.length + ' 只';
            }
          }
        }
      }
    });
  }

  refreshStatePool(cellId) {
    var node = this._findNode(cellId);
    if (!node || (node.type !== 'stock_state_pool' && node.type !== 'tdx_state_pool')) return;
    var overlay = this.nodesEl.querySelector('[data-stock-overlay="' + cellId + '"]');
    if (!overlay) {
      var el = this.nodeElements.get(cellId);
      if (el) {
        overlay = document.createElement('div');
        overlay.className = 'node-stock-overlay';
        overlay.setAttribute('data-stock-overlay', cellId);
        el.appendChild(overlay);
      }
    }
    if (overlay) {
      var scrollWrap = overlay.querySelector('.stock-table-scroll');
      if (scrollWrap) {
        scrollWrap.classList.add('stock-table-refreshing');
        setTimeout(function () { scrollWrap.classList.remove('stock-table-refreshing'); }, 300);
      }
      this._renderStockTable(node, overlay);
    }
  }

  _renderStockTable(node, container) {
    var self = this;
    var params = node.params || {};
    var colListStr = params.col_list || '2,-1,-2,-3,7,14,8,10,17,45';
    var colList;
    if (Array.isArray(colListStr)) { colList = colListStr.map(String); }
    else { colList = String(colListStr).split(','); for (var i = 0; i < colList.length; i++) colList[i] = colList[i].trim(); }

    var widthListStr = params.width_list || '';
    var widthList;
    if (Array.isArray(widthListStr)) { widthList = widthListStr.map(String); }
    else if (widthListStr) { widthList = String(widthListStr).split(','); for (var i = 0; i < widthList.length; i++) widthList[i] = widthList[i].trim(); }
    else { widthList = []; }

    container.innerHTML = '<div class="stock-loading">加载中...</div>';

    // Priority: in-memory stocks
    var memoryStocks = params.stocks || [];
    if (memoryStocks.length > 0) {
      var firstStk = memoryStocks[0];
      var isTDXRich = firstStk && typeof firstStk === 'object' && (firstStk.setcode !== undefined || firstStk.inprice !== undefined);

      if (isTDXRich) {
        if (memoryStocks.length > 100) {
          var tdxFields = [
            { field: 'code', name: '代码', w: 80 },
            { field: 'indate', name: '进入日期', w: 72 },
            { field: 'intime', name: '进入时间', w: 64 },
            { field: 'inprice', name: '进入价格', w: 64 },
            { field: 'income', name: '收益', w: 52 },
            { field: 'now', name: '当前价', w: 56 },
            { field: 'rise', name: '涨幅%', w: 56 },
            { field: 'volume', name: '成交量', w: 64 },
            { field: 'maxrate', name: '最高涨幅', w: 64 },
            { field: 'maxperiod', name: '最高周期', w: 56 },
            { field: 'maxtime', name: '最高时间', w: 64 },
            { field: 'maxprice', name: '最高价', w: 56 },
            { field: 'idaynum', name: '持仓天数', w: 56 }
          ];
          self._renderVirtualStockTable(container, memoryStocks, tdxFields, tdxFields.map(function (f) { return { name: f.name, w: f.w }; }));
          return;
        }
        var html = '<div class="stock-table-scroll"><table class="stock-table tdx-stock-table"><thead><tr>';
        if (params.show_row_num) { html += '<th class="row-num-head">#</th>'; }
        html += '<th>代码</th><th>进入日期</th><th>进入时间</th><th>进入价格</th><th>收益</th><th>当前价</th><th>涨幅%</th><th>成交量</th><th>最高涨幅</th><th>最高周期</th><th>最高时间</th><th>最高价</th><th>持仓天数</th>';
        html += '</tr></thead><tbody>';
        var showMax = Math.min(memoryStocks.length, 50);
        for (var i = 0; i < showMax; i++) {
          var s = memoryStocks[i];
          var code = s.label || s.code || '';
          var riseClass = '';
          if (s.rise > 0) riseClass = 'stock-rise';
          else if (s.rise < 0) riseClass = 'stock-fall';
          html += '<tr>';
          if (params.show_row_num) { html += '<td class="row-num-cell">' + (i + 1) + '</td>'; }
          html += '<td class="stock-code" data-stock-code="' + escHtml(code) + '">' + escHtml(code) + '</td>';
          html += '<td>' + (s.indate || '-') + '</td>';
          html += '<td>' + (s.intime || '-') + '</td>';
          html += '<td>' + (s.inprice != null ? s.inprice : '-') + '</td>';
          html += '<td>' + (s.income != null ? s.income : '-') + '</td>';
          html += '<td>' + (s.now != null ? s.now : '-') + '</td>';
          html += '<td class="' + riseClass + '">' + (s.rise != null ? s.rise : '-') + '</td>';
          html += '<td>' + (s.volume != null ? s.volume : '-') + '</td>';
          html += '<td>' + (s.maxrate != null ? s.maxrate : '-') + '</td>';
          html += '<td>' + (s.maxperiod != null ? s.maxperiod : '-') + '</td>';
          html += '<td>' + (s.maxtime != null ? s.maxtime : '-') + '</td>';
          html += '<td>' + (s.maxprice != null ? s.maxprice : '-') + '</td>';
          html += '<td>' + (s.idaynum != null ? s.idaynum : '-') + '</td>';
          html += '</tr>';
        }
        if (memoryStocks.length > 50) {
          html += '<tr><td colspan="' + (params.show_row_num ? 14 : 13) + '" class="stock-empty">...共 ' + memoryStocks.length + ' 只</td></tr>';
        }
        html += '</tbody></table></div>';
        container.innerHTML = html;
        self._bindStockCodeDblClick(container);
        return;
      }

      // Simple stock list
      if (memoryStocks.length > 100) {
        self._renderVirtualStockTable(container, memoryStocks.map(function (s) {
          return { code: s.label || s.code || '' };
        }), ['code', 'seq'], [{ name: '代码', w: 80 }, { name: '序号', w: 42 }]);
        return;
      }
      var html = '<div class="stock-table-scroll"><table class="stock-table"><thead><tr>';
      html += '<th style="width:80px">代码</th><th style="width:42px">序号</th>';
      html += '</tr></thead><tbody>';
      var showMax = Math.min(memoryStocks.length, 50);
      for (var i = 0; i < showMax; i++) {
        var s = memoryStocks[i];
        var code = s.label || s.code || '';
        html += '<tr><td class="stock-code" data-stock-code="' + escHtml(code) + '">' + escHtml(code) + '</td><td>' + (i + 1) + '</td></tr>';
      }
      if (memoryStocks.length > 50) {
        html += '<tr><td colspan="2" class="stock-empty">...共 ' + memoryStocks.length + ' 只</td></tr>';
      }
      html += '</tbody></table></div>';
      container.innerHTML = html;
      self._bindStockCodeDblClick(container);
      return;
    }

    // Fetch from API
    var apiUrl = '/api/dzh/cells/' + encodeURIComponent(node.id) + '/stocks?mode=mock';
    fetch(apiUrl)
      .then(function (r) { return r.json(); })
      .then(function (result) {
        var stockData = result.data || [];
        var apiColumns = result.columns || [];
        var colMapFromApi = {};
        apiColumns.forEach(function (col) { colMapFromApi[col.id] = col; });
        if (stockData.length === 0 && params.stocks && params.stocks.length > 0) {
          stockData = params.stocks.map(function (s) {
            return { code: s.label || '', name: s.name || '', current_price: s.p || '', change_pct: s.cp || '', volume: '', turnover_rate: '' };
          });
        }

        if (stockData.length > 100) {
          var vFields = [];
          var vHeaders = [];
          for (var i = 0; i < colList.length; i++) {
            var colId = colList[i];
            var colFromApi = colMapFromApi[Number(colId)] || colMapFromApi[colId];
            var colInfo = colFromApi || DZH_COL_MAP[colId];
            var field = colInfo ? (colInfo.key || colInfo.field) : colId;
            var colW = widthList[i] || (colInfo ? String(colInfo.w) : '42');
            vFields.push({ field: field, colInfo: colInfo, colId: colId });
            vHeaders.push({ name: colInfo ? colInfo.name : colId, w: parseInt(colW) || 42 });
          }
          self._renderVirtualStockTable(container, stockData, vFields, vHeaders, colMapFromApi);
          return;
        }

        var html = '<div class="stock-table-scroll"><table class="stock-table"><thead><tr>';
        for (var i = 0; i < colList.length; i++) {
          var colId = colList[i];
          var colFromApi = colMapFromApi[Number(colId)] || colMapFromApi[colId];
          var colInfo = colFromApi || DZH_COL_MAP[colId];
          var colName = colInfo ? colInfo.name : colId;
          var colW = widthList[i] || (colInfo ? String(colInfo.w) : '42');
          html += '<th style="width:' + colW + 'px">' + escHtml(colName) + '</th>';
        }
        html += '</tr></thead><tbody>';
        if (stockData.length === 0) {
          html += '<tr><td colspan="' + colList.length + '" class="stock-empty">暂无数据</td></tr>';
        } else {
          for (var s = 0; s < stockData.length; s++) {
            var stk = stockData[s];
            html += '<tr>';
            for (var c = 0; c < colList.length; c++) {
              var cid = colList[c];
              var colFromApi = colMapFromApi[Number(cid)] || colMapFromApi[cid];
              var ci = colFromApi || DZH_COL_MAP[cid];
              var field = ci ? (ci.key || ci.field) : null;
              var val = field ? (stk[field] !== undefined && stk[field] !== null ? stk[field] : '') : '';
              var cls = '';
              if (ci && ci.signed) {
                var numVal = parseFloat(val);
                if (!isNaN(numVal)) { if (numVal > 0) cls = ' stock-up'; else if (numVal < 0) cls = ' stock-down'; }
              }
              if (field === 'code') cls += ' stock-code';
              if (typeof val === 'number') {
                if (field === 'volume') { val = val >= 10000 ? (val / 10000).toFixed(0) + '万' : val; }
                else if (!Number.isInteger(val)) { val = val.toFixed(2); }
              }
              var stockCodeAttr = (field === 'code') ? ' data-stock-code="' + escHtml(String(val)) + '"' : '';
              html += '<td class="' + cls.trim() + '"' + stockCodeAttr + '>' + escHtml(String(val)) + '</td>';
            }
            html += '</tr>';
          }
        }
        html += '</tbody></table></div>';
        container.innerHTML = html;
        self._bindStockCodeDblClick(container);
      })
      .catch(function () {
        container.innerHTML = '<div class="stock-empty">加载失败</div>';
      });
  }

  _renderVirtualStockTable(container, stockData, fields, headers, colMapFromApi) {
    var self = this;
    var headerHtml = '<tr>';
    for (var h = 0; h < headers.length; h++) {
      var hdr = headers[h];
      if (typeof hdr === 'string') { headerHtml += '<th style="width:80px">' + escHtml(hdr) + '</th>'; }
      else { headerHtml += '<th style="width:' + (hdr.w || 42) + 'px">' + escHtml(hdr.name || '') + '</th>'; }
    }
    headerHtml += '</tr>';

    var scrollEl = document.createElement('div');
    scrollEl.className = 'stock-table-scroll stock-table-virtual';
    scrollEl.style.cssText = 'overflow-y:auto;position:relative;';

    var headerTable = document.createElement('table');
    headerTable.className = 'stock-table stock-table-virtual-header';
    headerTable.innerHTML = '<thead>' + headerHtml + '</thead>';
    scrollEl.appendChild(headerTable);

    var bodyContainer = document.createElement('div');
    bodyContainer.className = 'stock-table-virtual-body';
    bodyContainer.style.cssText = 'overflow-y:auto;position:relative;';
    scrollEl.appendChild(bodyContainer);

    container.innerHTML = '';
    container.appendChild(scrollEl);

    var vs = new VirtualScroller(bodyContainer, 28, 5);
    vs.renderRow = function (stk, index) {
      var tr = document.createElement('tr');
      for (var f = 0; f < fields.length; f++) {
        var fieldDef = fields[f];
        var td = document.createElement('td');
        if (typeof fieldDef === 'string') {
          if (fieldDef === 'seq') { td.textContent = index + 1; }
          else {
            var val = stk[fieldDef] !== undefined ? stk[fieldDef] : '';
            td.textContent = String(val);
            if (fieldDef === 'code') {
              td.className = 'stock-code';
              td.setAttribute('data-stock-code', escHtml(String(val)));
              td.addEventListener('dblclick', function () {
                var code = this.getAttribute('data-stock-code');
                if (code) self._showKlineModal(code);
              });
            }
          }
        } else {
          var field = fieldDef.field;
          var ci = fieldDef.colInfo;
          var val = field ? (stk[field] !== undefined && stk[field] !== null ? stk[field] : '') : '';
          var cls = '';
          if (ci && ci.signed) {
            var numVal = parseFloat(val);
            if (!isNaN(numVal)) { if (numVal > 0) cls = 'stock-up'; else if (numVal < 0) cls = 'stock-down'; }
          }
          if (field === 'code') cls += (cls ? ' ' : '') + 'stock-code';
          if (typeof val === 'number') {
            if (field === 'volume') { val = val >= 10000 ? (val / 10000).toFixed(0) + '万' : val; }
            else if (!Number.isInteger(val)) { val = val.toFixed(2); }
          }
          td.className = cls;
          td.textContent = String(val);
          if (field === 'code') {
            td.setAttribute('data-stock-code', escHtml(String(val)));
            td.addEventListener('dblclick', function () {
              var code = this.getAttribute('data-stock-code');
              if (code) self._showKlineModal(code);
            });
          }
        }
        tr.appendChild(td);
      }
      return tr;
    };
    vs.setData(stockData);
  }

  _refreshAllStatePoolNodes() {
    var self = this;
    this._nodes.forEach(function (node) {
      if (node.type === 'stock_state_pool' || node.type === 'tdx_state_pool') {
        self._rerenderNode(node.id);
      }
    });
  }

  _bindStockCodeDblClick(container) {
    var self = this;
    var codeCells = container.querySelectorAll('td[data-stock-code]');
    for (var i = 0; i < codeCells.length; i++) {
      codeCells[i].addEventListener('dblclick', function (e) {
        var stockCode = e.target.getAttribute('data-stock-code');
        if (stockCode) self._showKlineModal(stockCode);
      });
    }
  }

  _showKlineModal(stockCode) {
    var existing = document.querySelector('.kline-modal-overlay');
    if (existing) existing.remove();

    var overlay = document.createElement('div');
    overlay.className = 'kline-modal-overlay';

    var modal = document.createElement('div');
    modal.className = 'kline-modal';

    var header = document.createElement('div');
    header.className = 'kline-modal-header';
    var title = document.createElement('h3');
    title.textContent = stockCode + ' — K线图';
    var closeBtn = document.createElement('button');
    closeBtn.className = 'kline-modal-close';
    closeBtn.textContent = '×';
    closeBtn.onclick = function () { overlay.remove(); };
    header.appendChild(title);
    header.appendChild(closeBtn);
    modal.appendChild(header);

    var body = document.createElement('div');
    body.className = 'kline-modal-body';
    modal.appendChild(body);

    overlay.appendChild(modal);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);

    if (typeof KlineChart === 'function') {
      var chart = new KlineChart(body, {
        default_props: { height: 400, show_volume: true, show_ma: [5, 10, 20, 60], period: 'day' }
      });
      chart.loadData(stockCode, 'day');
    } else {
      body.innerHTML = '<div style="padding:40px;text-align:center;color:#888;">K线图组件未加载</div>';
    }
  }

  // ── Highlight ─────────────────────────────────────────────────────────────

  highlightNode(nodeId) {
    if (!this.highlightedNodes.has(nodeId)) {
      this.highlightedNodes.add(nodeId);
      var el = this.nodeElements.get(nodeId);
      if (el) { el.classList.add('highlight-flash'); el.style.zIndex = '1000'; }
    }
  }

  unhighlightNode(nodeId) {
    if (this.highlightedNodes.has(nodeId)) {
      this.highlightedNodes.delete(nodeId);
      var el = this.nodeElements.get(nodeId);
      if (el) { el.classList.remove('highlight-flash'); el.style.zIndex = ''; }
    }
  }

  highlightEdge(edgeId) {
    if (!this.highlightedEdges.has(edgeId)) {
      this.highlightedEdges.add(edgeId);
      var edgeData = this._edgeElements.get(edgeId);
      if (edgeData && edgeData.path) { edgeData.path.classList.add('flow-active'); }
    }
  }

  unhighlightEdge(edgeId) {
    if (this.highlightedEdges.has(edgeId)) {
      this.highlightedEdges.delete(edgeId);
      var edgeData = this._edgeElements.get(edgeId);
      if (edgeData && edgeData.path) { edgeData.path.classList.remove('flow-active'); }
    }
  }

  clearAllHighlights() {
    var self = this;
    this.highlightedNodes.forEach(function (nodeId) { self.unhighlightNode(nodeId); });
    this.highlightedEdges.forEach(function (edgeId) { self.unhighlightEdge(edgeId); });
  }

  getHighlightedNodes() { return new Set(this.highlightedNodes); }
  getHighlightedEdges() { return new Set(this.highlightedEdges); }

  // ── SVG compatibility (for zoomchange event dispatch) ─────────────────────

  get svg() { return this.svgEl; }

  // ── Overlay compatibility (main.js uses canvas.overlay) ───────────────────

  get overlay() { return this.nodesEl; }
}
