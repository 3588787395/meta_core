/**
 * ui.js — UI 组件四件套合并文件
 * ============================================================
 * 合并自：
 *   - panel.js            (TableDrivenPanel + HighlightManager)
 *   - toolbar-renderer.js (ToolbarRenderer 表驱动工具栏)
 *   - event-panel.js      (EventPanel 事件浮窗 + K线面板)
 *   - formula-manager.js  (FormulaManager 公式管理器前端交互)
 *
 * 顶层 IIFE 包裹避免全局污染；内部按文件分节注释分隔。
 * 原 ES module export 已转换为 window 全局引用。
 */

(function () {
  'use strict';

  // === from panel.js ===

/**
 * panel.js — 面板与高亮管理合并文件
 * ============
 * 此文件由以下两个模块合并而成：
 *   - TableDrivenPanel: 表驱动属性面板引擎（来源: table-driven-panel.js）
 *   - HighlightManager: 高亮管理器（来源: highlight-manager.js）
 *
 * 合并目的：减少文件数量，将面板渲染与高亮管理集中管理。
 *
 * 全局导出（TableDrivenPanel IIFE）：
 *   - window.TableDrivenPanel
 *   - window.TableDrivenForm
 *   - window.ComponentRegistry
 *   - window.DataBinder
 *   - window.ValidationEngine
 *
 * ES Module 导出：
 *   - HighlightManager
 */

// ============================================================================
// ===== 来源: table-driven-panel.js =====
// ============================================================================
/**
 * 表驱动属性面板引擎
 * ==================
 * 完全基于配置表驱动的属性面板。
 * 所有组件类型、交互行为、样式及校验规则均由后端配置表决定。
 *
 * 核心设计原则：
 * 1. 零硬编码：不包含任何节点类型判断或业务逻辑
 * 2. 组件注册表：所有UI组件类型通过注册表动态扩展
 * 3. 字段联动：通过 depends_on/active_when 配置驱动
 * 4. 位标志自动编解码：flag_group 组件自动处理位标志
 * 5. 热加载：配置变更即时生效
 */

(function (global) {
  'use strict';

  // ─── 组件注册表 ─────────────────────────────────────────────

  var ComponentRegistry = {
    _components: {},

    register: function (type, renderer) {
      this._components[type] = renderer;
    },

    get: function (type) {
      return this._components[type] || this._components['text_input'];
    },

    list: function () {
      return Object.keys(this._components);
    },

    /**
     * 从 ui_components.json 配置自动注册组件
     * 配置格式: { components: { comp_id: { renderer_type: "text_input", ... } } }
     */
    registerFromConfig: function (uiComponentsConfig) {
      if (!uiComponentsConfig || !uiComponentsConfig.components) return;
      var self = this;
      Object.keys(uiComponentsConfig.components).forEach(function (compId) {
        var comp = uiComponentsConfig.components[compId];
        // 如果配置中指定了 renderer_type，且该类型已注册，则创建别名
        if (comp.renderer_type && self._components[comp.renderer_type] && compId !== comp.renderer_type) {
          self.register(compId, self._components[comp.renderer_type]);
        }
      });
    }
  };

  // ─── 校验规则引擎 ──────────────────────────────────────────

  var ValidationEngine = {
    /**
     * 校验字段值，返回错误消息列表
     * 校验规则来自字段配置中的 validation 对象：
     *   required: true          - 必填
     *   min: number             - 最小值
     *   max: number             - 最大值
     *   pattern: "regex"        - 正则匹配
     *   custom: "handler_name"  - 自定义校验handler
     */
    validate: function (fieldConfig, value, allData) {
      var v = fieldConfig.validation;
      if (!v) return [];
      var errors = [];

      if (v.required && (value === null || value === undefined || value === '')) {
        errors.push(fieldConfig.label + '不能为空');
      }

      if (v.min !== undefined && value !== null && value !== undefined && value < v.min) {
        errors.push(fieldConfig.label + '不能小于' + v.min);
      }

      if (v.max !== undefined && value !== null && value !== undefined && value > v.max) {
        errors.push(fieldConfig.label + '不能大于' + v.max);
      }

      if (v.pattern && value) {
        var re = new RegExp(v.pattern);
        if (!re.test(String(value))) {
          errors.push(v.pattern_msg || fieldConfig.label + '格式不正确');
        }
      }

      if (v.custom && this._customValidators[v.custom]) {
        var customErr = this._customValidators[v.custom](value, allData, fieldConfig);
        if (customErr) errors.push(customErr);
      }

      return errors;
    },

    _customValidators: {},

    registerValidator: function (name, fn) {
      this._customValidators[name] = fn;
    }
  };

  // 注册常用业务校验器
  ValidationEngine.registerValidator('indicator_params', function (value, allData, fieldConfig) {
    // 仅当公式脚本中出现 KDJ/MACD 函数时校验参数个数
    var script = '';
    if (allData) {
      script = (DataBinder.get(allData, 'formula_decoded') || '') + ' ' + (DataBinder.get(allData, 'indi') || '');
    }
    var indiparam = (value === undefined || value === null) ? '' : String(value).trim();
    var kdjMatch = /KDJ\s*\(([^)]*)\)/i.exec(script);
    var macdMatch = /MACD\s*\(([^)]*)\)/i.exec(script);
    if (!kdjMatch && !macdMatch) return null;
    var params = [];
    if (indiparam) {
      var s = indiparam;
      if (s.charAt(0) === '(' && s.charAt(s.length - 1) === ')') s = s.substring(1, s.length - 1);
      params = s.split(',').map(function (p) { return p.trim(); }).filter(function (p) { return p !== ''; });
    }
    if (kdjMatch) {
      if (params.length !== 3) return 'KDJ 参数应为 3 个 (N,M1,M2)，如 (9,3,3)';
      for (var i = 0; i < params.length; i++) {
        var n = parseInt(params[i], 10);
        if (isNaN(n) || n <= 0) return 'KDJ 参数必须为正整数';
      }
    }
    if (macdMatch) {
      if (params.length !== 3) return 'MACD 参数应为 3 个 (SHORT,LONG,MID)，如 (12,26,9)';
      for (var j = 0; j < params.length; j++) {
        var m = parseInt(params[j], 10);
        if (isNaN(m) || m <= 0) return 'MACD 参数必须为正整数';
      }
    }
    return null;
  });

  ValidationEngine.registerValidator('ttl_value_unit', function (value, allData, fieldConfig) {
    var ttlMode = '';
    if (allData) ttlMode = String(DataBinder.get(allData, 'ttl_mode') || '');
    var num = parseFloat(value);
    if (ttlMode === 'interval' && (isNaN(num) || num <= 0)) {
      return 'TTL 模式为固定间隔时，保留秒数必须大于 0';
    }
    if (ttlMode === 'market_open' && (!isNaN(num) && num !== 0)) {
      return 'TTL 模式为开市持续时，保留秒数应设为 0（由开市时间决定）';
    }
    return null;
  });

  ValidationEngine.registerValidator('edge_condition_required', function (value, allData, fieldConfig) {
    if (!allData) return null;
    var ctype = String(DataBinder.get(allData, 'condition_type') || '');
    if (!ctype || ctype === '直接转移') return null;
    if (ctype === 'INTERSECTION') return null; // 交集依赖 intersection_source，由其它字段校验
    var ref = String(value || '').trim();
    if (!ref) return '选择条件公式后必须填写公式引用（如 KDJ_5MIN_CROSS / MACD_1MIN_CROSS）';
    return null;
  });

  ValidationEngine.registerValidator('formula_period_required', function (value, allData, fieldConfig) {
    if (!allData) return null;
    // 当已选择公式脚本时，周期为必填参数
    var script = String(DataBinder.get(allData, 'params.tdx_func.formula_script') || '').trim();
    var accode = String(DataBinder.get(allData, 'params.tdx_func.accode') || '').trim();
    if (!script && !accode) return null;
    if (value === '' || value == null || value === undefined) return '请选择公式分析周期';
    return null;
  });

  // ─── 数据绑定工具 ────────────────────────────────────────────

  var DataBinder = {
    get: function (data, path, defaultVal) {
      if (!data || !path) return defaultVal;
      var keys = path.split('.');
      var cur = data;
      for (var i = 0; i < keys.length; i++) {
        if (cur == null || typeof cur !== 'object') return defaultVal;
        cur = cur[keys[i]];
      }
      if (cur === undefined) return defaultVal;
      // 如果结果是 {raw: number, bits: {...}} 格式的位标志对象，自动提取 raw
      if (cur !== null && typeof cur === 'object' && typeof cur.raw === 'number' && cur.bits !== undefined) {
        return cur.raw;
      }
      return cur;
    },

    set: function (data, path, value) {
      if (!data || !path) return;
      var keys = path.split('.');
      var cur = data;
      for (var i = 0; i < keys.length - 1; i++) {
        if (!(keys[i] in cur) || typeof cur[keys[i]] !== 'object') {
          cur[keys[i]] = {};
        }
        cur = cur[keys[i]];
      }
      var lastKey = keys[keys.length - 1];
      cur[lastKey] = value;
    },

    decodeAttrFlags: function (attrInt, flags) {
      var result = {};
      for (var i = 0; i < flags.length; i++) {
        var mask = typeof flags[i].hex === 'string' ? parseInt(flags[i].hex, 16) : flags[i].hex;
        result[flags[i].name] = !!(attrInt & mask);
      }
      return result;
    },

    encodeAttrFlags: function (flagsDict, flags) {
      var result = 0;
      for (var i = 0; i < flags.length; i++) {
        if (flagsDict[flags[i].name]) {
          var mask = typeof flags[i].hex === 'string' ? parseInt(flags[i].hex, 16) : flags[i].hex;
          result |= mask;
        }
      }
      return result;
    },

    decodeActionCompound: function (raw) {
      if (raw == null || raw === 0) return { action_type: 0, param: 0 };
      // 如果是对象格式 {type: 0, params: 10000}
      if (typeof raw === 'object' && raw !== null) {
        return { action_type: raw.type || 0, param: raw.params || 0 };
      }
      // 如果是整数位编码格式
      return { action_type: (raw >> 28) & 0xF, param: raw & 0xFFFF };
    },
    encodeActionCompound: function (actionType, param) {
      return { type: actionType, params: param };
    },

    resolveFlowMode: function (attr) {
      // Try loading resolve_rules from flow_mode_registry
      var pd = window.__poolData;
      var registry = pd && pd._flowModeRegistry;
      if (registry && registry.resolve_rules) {
        var rules = registry.resolve_rules;
        // Sort by priority (lower priority number = higher precedence)
        var sorted = rules.slice().sort(function(a, b) { return (a.priority || 99) - (b.priority || 99); });
        for (var i = 0; i < sorted.length; i++) {
          var rule = sorted[i];
          var mask = rule.mask;
          var value = rule.value;
          if ((attr & mask) === value) {
            return rule.mode;
          }
        }
      }
      // Fallback to hardcoded rules
      if (attr & 0x4000) return "output_components";
      if (attr & 0x2000) return "overwrite";
      if ((attr & 0x1000) && !(attr & 0x1)) return "copy";
      if ((attr & 0x3) === 0x3) return "force_move";
      if ((attr & 0x1) && !(attr & 0x1000)) return "move";
      return "pass_through";
    }
  };

  // ─── 内置组件渲染器 ──────────────────────────────────────────

  // 文本输入框
  ComponentRegistry.register('text_input', function (field, data) {
    var val = DataBinder.get(data, field.data_path, field.default || '');
    var html = '<div class="td-field td-field-text" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    html += '<input type="text" class="td-input" data-path="' + field.data_path + '" value="' + _escHtml(val) + '"';
    if (field.readonly) html += ' readonly';
    if (field.nullable) html += ' data-nullable="1"';
    html += '>';
    html += '</div>';
    return html;
  });

  // 多行文本输入框
  ComponentRegistry.register('textarea', function (field, data) {
    var val = DataBinder.get(data, field.data_path, field.default || '');
    var rows = field.rows || 3;
    var html = '<div class="td-field td-field-textarea" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    html += '<textarea class="td-input td-textarea" data-path="' + field.data_path + '" rows="' + rows + '"';
    if (field.readonly) html += ' readonly';
    if (field.nullable) html += ' data-nullable="1"';
    html += '>' + _escHtml(val) + '</textarea>';
    html += '</div>';
    return html;
  });

  // 数字输入框
  ComponentRegistry.register('number_input', function (field, data) {
    var val = DataBinder.get(data, field.data_path, field.default);
    var html = '<div class="td-field td-field-number" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-number-wrap">';
    html += '<input type="number" class="td-input" data-path="' + field.data_path + '"';
    if (val != null) html += ' value="' + val + '"';
    if (field.min != null) html += ' min="' + field.min + '"';
    if (field.max != null) html += ' max="' + field.max + '"';
    if (field.step != null) html += ' step="' + field.step + '"';
    if (field.nullable) html += ' data-nullable="1"';
    if (field.readonly) html += ' readonly';
    html += '>';
    if (field.unit) {
      html += '<span class="td-input-unit">' + _escHtml(field.unit) + '</span>';
    }
    html += '</div>';
    if (field.unit_options) {
      html += '<select class="td-unit-select" data-key="' + field.key + '_unit">';
      for (var i = 0; i < field.unit_options.length; i++) {
        html += '<option value="' + field.unit_options[i].factor + '">' + field.unit_options[i].label + '</option>';
      }
      html += '</select>';
    }
    html += '</div>';
    return html;
  });

  // 条件公式摘要（边编辑器中直接显示当前条件公式）
  ComponentRegistry.register('condition_summary', function (field, data) {
    var ctype = DataBinder.get(data, 'condition_type') || '';
    var fref = DataBinder.get(data, 'formula_ref') || '';
    var isrc = DataBinder.get(data, 'intersection_source') || '';
    var summary = '-';
    if (ctype === 'INTERSECTION' && isrc) summary = 'INTERSECTION (' + _escHtml(isrc) + ')';
    else if (ctype === 'INTERSECTION') summary = 'INTERSECTION';
    else if (fref) summary = _escHtml(fref);
    else if (ctype) summary = _escHtml(ctype);
    var html = '<div class="td-field td-field-condition-summary" data-key="' + field.key + '">';
    html += '<label>' + (field.label || '当前条件公式') + '</label>';
    html += '<div class="td-condition-summary-box" title="当前条件公式">' + summary + '</div>';
    html += '</div>';
    return html;
  });

  // 下拉选择框
  ComponentRegistry.register('select', function (field, data) {
    var val = DataBinder.get(data, field.data_path, field.default);
    var html = '<div class="td-field td-field-select" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    html += '<select class="td-select" data-path="' + field.data_path + '">';
    if (field.nullable) {
      html += '<option value="">--</option>';
    }
    for (var i = 0; i < field.options.length; i++) {
      var opt = field.options[i];
      var sel = (opt.value == val || (val == null && opt.value === field.default)) ? ' selected' : '';
      html += '<option value="' + _escAttr(opt.value) + '"' + sel + '>' + _escHtml(opt.label) + '</option>';
    }
    html += '</select>';
    // 显示 hint（如果有）
    if (field.hint) {
      html += '<div class="td-field-hint">' + _escHtml(field.hint) + '</div>';
    }
    html += '</div>';
    return html;
  });

  // TDX增强枚举选择器（带描述提示的select）
  ComponentRegistry.register('tdx_enum_select', function (field, data) {
    var val = DataBinder.get(data, field.data_path, field.default);
    var currentDesc = '';
    for (var i = 0; i < field.options.length; i++) {
      if (field.options[i].value == val) { currentDesc = field.options[i].desc || ''; break; }
    }
    var html = '<div class="td-field td-field-tdx-enum" data-key="' + field.key + '">';
    html += '<label>' + _escHtml(field.label || '枚举选择') + '</label>';
    html += '<div class="td-enum-select-wrap">';
    html += '<select class="td-select td-enum-select" data-path="' + field.data_path + '" data-enum-key="' + _escAttr(field.enum_key || field.key) + '">';
    if (field.nullable) {
      html += '<option value="">-- 请选择 --</option>';
    }
    for (var j = 0; j < field.options.length; j++) {
      var opt2 = field.options[j];
      var sel2 = (opt2.value == val || (val == null && opt2.value === field.default)) ? ' selected' : '';
      var titleAttr = opt2.desc ? ' title="' + _escAttr(opt2.desc) + '"' : '';
      html += '<option value="' + _escAttr(opt2.value) + '"' + sel2 + titleAttr + '>' + _escHtml(opt2.label) + '</option>';
    }
    html += '</select>';
    html += '</div>';
    // 描述提示区
    html += '<div class="td-enum-desc" data-enum-desc-for="' + _escAttr(field.key) + '">';
    html += currentDesc ? '<span class="td-enum-desc-text">' + _escHtml(currentDesc) + '</span>' : '';
    html += '</div>';
    if (field.hint) {
      html += '<div class="td-field-hint">' + _escHtml(field.hint) + '</div>';
    }
    html += '</div>';
    return html;
  });

  // 颜色选择器（增强版：色块+名称+hex可视化显示，点击可复制）
  ComponentRegistry.register('color_picker', function (field, data) {
    var val = DataBinder.get(data, field.data_path, field.default || -1);
    // 处理字符串格式的颜色值（如 "16744448"）
    var intVal = (typeof val === 'string' && val !== '') ? parseInt(val, 10) : (typeof val === 'number' ? val : -1);
    if (isNaN(intVal)) intVal = -1;
    var colorHex = (intVal >= 0) ? _intToHex(intVal) : '#ffffff';
    // 使用 renderDzhColorBadge 生成颜色可视化（如果全局函数可用则使用，否则降级为简单显示）
    var colorBadgeHtml = '';
    if (typeof renderDzhColorBadge === 'function') {
      colorBadgeHtml = renderDzhColorBadge(intVal, '#808080');
    } else {
      colorBadgeHtml = '<span style="display:inline-flex;align-items:center;gap:4px;vertical-align:middle;">' +
        '<span style="display:inline-block;width:16px;height:16px;border-radius:3px;border:1px solid rgba(255,255,255,0.2);background:' + colorHex + ';"></span>' +
        '<span style="color:#ccc;font-size:11px;font-family:monospace;">' + colorHex + '</span> (' + intVal + ')' +
        '</span>';
    }
    var html = '<div class="td-field td-field-color" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-color-wrap">';
    html += '<input type="color" class="td-color-input" data-path="' + field.data_path + '" value="' + colorHex + '">';
    html += '<span class="td-color-value">' + intVal + '</span>';
    html += '</div>';
    // 颜色可视化徽章（在输入框下方显示详细信息）
    html += '<div class="td-color-badge-wrap" style="margin-top:4px;padding:4px 6px;background:var(--bg-secondary);border-radius:3px;border:1px solid var(--border-color);">' + colorBadgeHtml + '</div>';
    html += '</div>';
    return html;
  });

  // 位标志组（复选框组）
  ComponentRegistry.register('flag_group', function (field, data) {
    var attrInt = DataBinder.get(data, field.data_path, 0);
    var decoded = DataBinder.decodeAttrFlags(attrInt, field.flags || []);
    var html = '<div class="td-field td-field-flags" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-flag-group">';
    for (var i = 0; i < field.flags.length; i++) {
      var flag = field.flags[i];
      var checked = decoded[flag.name] ? ' checked' : '';
      html += '<label class="td-flag-item">';
      html += '<input type="checkbox" class="td-flag-cb" data-flag-name="' + flag.name + '" data-flag-hex="' + flag.hex + '"' + checked + '>';
      html += '<span>' + _escHtml(flag.label) + '</span>';
      html += '</label>';
    }
    html += '</div>';
    html += '</div>';
    return html;
  });

  // 动作复合组件（动作类型+参数）
  ComponentRegistry.register('action_compound', function (field, data) {
    var rawVal = DataBinder.get(data, field.data_path, 0);
    var decoded = DataBinder.decodeActionCompound(rawVal);
    var html = '<div class="td-field td-field-action" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-action-wrap">';
    html += '<select class="td-action-type" data-key="' + field.key + '_type">';
    for (var i = 0; i < field.action_types.length; i++) {
      var at = field.action_types[i];
      var sel = (at.value === decoded.action_type) ? ' selected' : '';
      html += '<option value="' + at.value + '"' + sel + '>' + _escHtml(at.label) + '</option>';
    }
    html += '</select>';
    html += '<input type="number" class="td-action-param" data-key="' + field.key + '_param" value="' + decoded.param + '" min="0">';
    html += '</div>';
    html += '</div>';
    return html;
  });

  // 市场选择器（复选框组+特殊格式）
  ComponentRegistry.register('market_selector', function (field, data) {
    var rawVal = DataBinder.get(data, field.data_path, []);
    // 支持数组格式 ["sh_a", "sz_a"] 和管道分隔字符串格式 "sh_a|sz_a"
    var selectedMarkets = Array.isArray(rawVal) ? rawVal : (rawVal ? String(rawVal).split('|') : []);
    var html = '<div class="td-field td-field-markets" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-market-group">';
    for (var i = 0; i < field.markets.length; i++) {
      var m = field.markets[i];
      var checked = (selectedMarkets.indexOf(m.code) >= 0) ? ' checked' : '';
      html += '<label class="td-market-item">';
      html += '<input type="checkbox" class="td-market-cb" data-market-code="' + _escAttr(m.code) + '"' + checked + '>';
      html += '<span>' + _escHtml(m.label) + '</span>';
      html += '</label>';
    }
    html += '</div>';
    html += '</div>';
    return html;
  });

  // Base64只读显示
  ComponentRegistry.register('base64_readonly', function (field, data) {
    var val = DataBinder.get(data, field.data_path, '');
    var decoded = '';
    try { decoded = atob(val); } catch (e) { decoded = val; }
    var html = '<div class="td-field td-field-base64" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-base64-wrap">';
    html += '<textarea class="td-base64-display" readonly>' + _escHtml(decoded) + '</textarea>';
    html += '</div>';
    html += '</div>';
    return html;
  });

  // 指标选择器（TDX系统指标下拉）
  ComponentRegistry.register('indicator_select', function (field, data) {
    var val = DataBinder.get(data, field.data_path, 0);
    var html = '<div class="td-field td-field-indicator" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    html += '<input type="number" class="td-input" data-path="' + field.data_path + '" value="' + (val || 0) + '" min="0" placeholder="指标编号">';
    html += '<button class="td-indicator-browse" data-key="' + field.key + '">浏览</button>';
    html += '</div>';
    return html;
  });

  // ── bsavehis 保存历史数据面板（checkbox + 日期选择 + 查看/导出/入池日志） ──
  ComponentRegistry.register('bsavehis_panel', function (field, data) {
    var val = DataBinder.get(data, field.data_path, field.default || 0);
    var checked = val ? ' checked' : '';
    var uid = 'bsavehis_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    var html = '<div class="td-field td-field-bsavehis" data-key="' + field.key + '" data-path="' + field.data_path + '" data-uid="' + uid + '">';
    html += '<label class="td-bsavehis-label"><input type="checkbox" class="td-bsavehis-cb" data-path="' + field.data_path + '"' + checked + '> ' + _escHtml(field.label) + '</label>';
    // 控制区（选中时显示）
    html += '<div class="td-bsavehis-controls' + (val ? '' : ' td-bsavehis-hidden') + '">';
    //   日期选择
    html += '<input type="date" class="td-bsavehis-date" id="' + uid + '_date">';
    //   按钮
    html += '<button type="button" class="td-btn td-bsavehis-btn-view" data-uid="' + uid + '">查看</button>';
    html += '<button type="button" class="td-btn td-bsavehis-btn-export" data-uid="' + uid + '">导出</button>';
    html += '<a href="#" class="td-bsavehis-link-log" data-uid="' + uid + '">入池日志</a>';
    html += '</div>';
    // 结果弹窗
    html += '<div class="td-bsavehis-result" id="' + uid + '_result"></div>';
    html += '</div>';
    return html;
  });

  // begint时间输入（根据begin值切换格式）
  ComponentRegistry.register('begint_input', function (field, data) {
    var beginVal = DataBinder.get(data, field.depends_on, 0);
    var begintVal = DataBinder.get(data, field.data_path, 0);
    var hhmmssModes = field.hhmmss_modes || [3, 4, 7];
    var isHhmmss = hhmmssModes.indexOf(beginVal) >= 0;
    var html = '<div class="td-field td-field-begint" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    if (isHhmmss) {
      html += '<input type="text" class="td-input td-begint-hhmmss" data-path="' + field.data_path + '" value="' + _formatHhmmss(begintVal) + '" placeholder="HHMMSS">';
    } else {
      var showInput = (beginVal === 1 || beginVal === 2 || beginVal === 5 || beginVal === 6);
      if (showInput) {
        html += '<input type="number" class="td-input td-begint-seconds" data-path="' + field.data_path + '" value="' + begintVal + '" min="0">';
      } else {
        html += '<span class="td-begint-disabled">不适用</span>';
      }
    }
    html += '</div>';
    return html;
  });

  // 只读日期时间
  ComponentRegistry.register('readonly_datetime', function (field, data) {
    var val = DataBinder.get(data, field.data_path, '');
    var html = '<div class="td-field td-field-datetime" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    html += '<span class="td-readonly-value">' + _escHtml(val || '-') + '</span>';
    html += '</div>';
    return html;
  });

  // 股票列表管理组件（TDX备选池专用）
  ComponentRegistry.register('stock_list_editor', function (field, data) {
    var stocks = DataBinder.get(data, field.data_path, []);
    if (typeof stocks === 'string') {
      try { stocks = JSON.parse(stocks); } catch (e) { stocks = []; }
    }
    if (!Array.isArray(stocks)) stocks = [];

    var html = '<div class="td-field td-field-stock-list" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-stock-list-wrap">';

    // 添加股票表单
    html += '<div class="td-stock-add-form">';
    html += '<select class="td-stock-market" data-key="' + field.key + '_market">';
    html += '<option value="0">深圳</option>';
    html += '<option value="1">上海</option>';
    html += '<option value="2">北交所</option>';
    html += '</select>';
    html += '<input type="text" class="td-stock-code" data-key="' + field.key + '_code" placeholder="股票代码" maxlength="6">';
    html += '<button class="td-stock-add-btn" data-key="' + field.key + '_add">添加</button>';
    html += '</div>';

    // 股票列表表格
    html += '<table class="td-stock-table">';
    html += '<thead><tr><th>市场</th><th>代码</th><th>操作</th></tr></thead>';
    html += '<tbody>';
    var marketNames = {'0': '深圳', '1': '上海', '2': '北交所'};
    for (var i = 0; i < stocks.length; i++) {
      var stk = stocks[i];
      var setcode = String(stk.setcode || stk.market || 0);
      var code = stk.code || '';
      html += '<tr data-idx="' + i + '">';
      html += '<td>' + (marketNames[setcode] || setcode) + '</td>';
      html += '<td>' + _escHtml(code) + '</td>';
      html += '<td><button class="td-stock-del-btn" data-idx="' + i + '">删除</button></td>';
      html += '</tr>';
    }
    html += '</tbody></table>';

    // 底部操作
    html += '<div class="td-stock-actions">';
    html += '<span class="td-stock-count">共 ' + stocks.length + ' 只</span>';
    html += '<button class="td-stock-clear-btn" data-key="' + field.key + '_clear">清空</button>';
    html += '</div>';

    html += '</div></div>';
    return html;
  });

  // 指标浏览选择组件（TDX条件专用）
  ComponentRegistry.register('indicator_browser', function (field, data) {
    var val = DataBinder.get(data, field.data_path, 0);
    var html = '<div class="td-field td-field-indicator-browser" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-indicator-wrap">';
    html += '<input type="number" class="td-input td-indicator-no" data-path="' + field.data_path + '" value="' + (val || 0) + '" min="0" placeholder="指标编号">';
    html += '<button class="td-indicator-browse-btn" data-key="' + field.key + '_browse">浏览</button>';
    html += '</div>';
    html += '</div>';
    return html;
  });

  // 只读文本显示
  ComponentRegistry.register('readonly', function (field, data) {
    var val = DataBinder.get(data, field.data_path, field.default || '');
    var displayAttr = field.display;
    var html = '<div class="td-field td-field-readonly" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    if (displayAttr === 'raw') {
      // 显示原始整数值+十六进制
      html += '<span class="td-readonly-value">' + _escHtml(String(val)) + ' (0x' + ((val >>> 0) || 0).toString(16) + ')</span>';
    } else {
      html += '<span class="td-readonly-value">' + _escHtml(String(val != null ? val : '-')) + '</span>';
    }
    html += '</div>';
    return html;
  });

  // 公式参数容器（DZH condition_filter 专用：由 _injectDzhFormulaPicker 动态填充）
  // 渲染一个空的 .td-formula-args-box 占位，_renderFormulaArgsForm 向 .td-formula-args-list 写入参数行
  ComponentRegistry.register('formula_args_container', function (field, data) {
    var html = '<div class="td-field td-formula-args-box" data-key="' + _escAttr(field.key || 'formula_args') + '" style="display:none;margin-top:6px;">';
    html += '<label style="font-size:11px;font-weight:600;">' + _escHtml(field.label || '公式参数') + '</label>';
    html += '<div class="td-formula-args-list" style="margin-top:4px;"></div>';
    html += '</div>';
    return html;
  });

  // 流程线信息显示（只读，显示源/目标节点标签）
  ComponentRegistry.register('flow_info', function (field, data) {
    var val = DataBinder.get(data, field.data_path, field.default || '?');
    var html = '<div class="td-field td-field-readonly" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    html += '<span class="td-readonly-value" style="font-size:12px;color:var(--text-secondary);">' + _escHtml(String(val)) + '</span>';
    html += '</div>';
    return html;
  });

  // 流转模式显示标签
  ComponentRegistry.register('flow_mode_display', function (field, data) {
    var attrVal = DataBinder.get(data, field.data_path, 0);
    var modeInfo = DataBinder.resolveFlowMode(attrVal);
    var modeColors = {
      "move": "#c0392b", "overwrite": "#e67e22", "copy": "#2980b9",
      "force_move": "#9b59b6", "output_components": "#e67e22", "pass_through": "#27ae60"
    };
    var modeLabels = {
      "move": "移动", "overwrite": "覆盖", "copy": "复制",
      "force_move": "强制移动", "output_components": "输出成份", "pass_through": "直通"
    };
    var color = modeColors[modeInfo] || "#8c8c8c";
    var label = modeLabels[modeInfo] || modeInfo;
    var html = '<div class="td-field td-field-readonly" data-key="' + field.key + '">';
    html += '<label>' + field.label + '</label>';
    html += '<span class="td-readonly-value" style="color:' + color + ';font-weight:bold;">' + _escHtml(label) + '</span>';
    html += '</div>';
    return html;
  });

  // 转移模式选择器（radio按钮组，映射到attr位标志）
  ComponentRegistry.register('transfer_mode', function (field, data) {
    var attrVal = DataBinder.get(data, field.data_path, 0);
    var currentMode = DataBinder.resolveFlowMode(attrVal);
    var html = '<div class="td-field td-field-transfer-mode" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-transfer-mode-group" style="display:flex;flex-direction:column;gap:4px;">';
    for (var i = 0; i < field.options.length; i++) {
      var opt = field.options[i];
      var checked = (opt.value === currentMode) ? ' checked' : '';
      html += '<label class="td-transfer-mode-item" style="font-size:12px;cursor:pointer;">';
      html += '<input type="radio" name="transfer_mode" class="td-transfer-mode-radio" data-mode="' + opt.value + '"' + checked + '>';
      html += '<span>' + _escHtml(opt.label) + '</span>';
      html += '</label>';
    }
    html += '</div>';
    html += '</div>';
    return html;
  });

  // 板块树形选择器（备选池专用）
  ComponentRegistry.register('sector_tree', function (field, data) {
    var html = '<div class="td-field td-field-sector-tree" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    // 隐藏的板块树容器（仅用于后台加载数据到缓存，不在面板内显示小树）
    html += '<div class="td-sector-tree-container" id="tdSectorTree" style="display:none;"></div>';
    // 简洁的选择按钮 + 已选数量 + 同步按钮
    html += '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">';
    html += '<button type="button" class="td-btn td-btn-primary td-btn-sm td-btn-sector-modal" id="tdSectorModal" data-action="open_sector_modal" title="点击在大窗口中选择板块" style="flex:1;min-width:120px;">';
    html += '<span class="td-btn-text">选择板块...</span>';
    html += '</button>';
    html += '<button type="button" class="td-btn td-btn-sm td-btn-sector-sync" id="tdSectorSync" data-action="sync_all_sectors" title="从数据源同步板块和股票到数据库">';
    html += '<span class="td-btn-text">🔄</span>';
    html += '</button>';
    html += '</div>';
    html += '</div>';
    return html;
  });

  // 股票来源编辑器（备选池专用：输入框+添加按钮+文件导入+列表）
  ComponentRegistry.register('stock_source_editor', function (field, data) {
    var stocks = DataBinder.get(data, field.data_path, []);
    if (!Array.isArray(stocks)) stocks = [];
    var html = '<div class="td-field td-field-stock-source" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-stock-source-wrap">';
    // 输入区
    html += '<div style="display:flex;gap:4px;margin-bottom:6px;">';
    html += '<input type="text" class="td-stock-source-input" data-key="' + field.key + '_input" placeholder="输入代码如600519或600519.SH" style="flex:1;padding:4px 8px;border:1px solid var(--border-color);border-radius:3px;font-size:12px;">';
    html += '<button type="button" class="td-btn td-btn-primary td-btn-sm td-stock-source-add" data-key="' + field.key + '_add">加入个股</button>';
    html += '</div>';
    // 文件导入
    html += '<div style="margin-bottom:6px;">';
    html += '<label class="td-btn td-btn-sm" style="cursor:pointer;display:inline-block;padding:4px 10px;font-size:11px;">加入文件<input type="file" class="td-stock-source-file" accept=".txt,.csv" style="display:none;" data-key="' + field.key + '_file"></label>';
    html += '</div>';
    // 已选列表
    html += '<div class="td-stock-source-list" style="max-height:120px;overflow-y:auto;border:1px solid var(--border-color);padding:4px;background:var(--bg-secondary);border-radius:4px;">';
    for (var i = 0; i < stocks.length; i++) {
      html += '<div class="td-stock-source-item" style="display:flex;justify-content:space-between;align-items:center;padding:2px 4px;font-size:11px;">';
      html += '<span>' + _escHtml(stocks[i]) + '</span>';
      html += '<button class="td-stock-source-del" data-idx="' + i + '" style="background:none;border:none;color:#c0392b;cursor:pointer;font-size:11px;">✕</button>';
      html += '</div>';
    }
    html += '</div>';
    html += '</div></div>';
    return html;
  });

  // 重载模式选择器（radio按钮组）
  ComponentRegistry.register('reload_mode', function (field, data) {
    var val = DataBinder.get(data, field.data_path, field.default || 'never');
    var html = '<div class="td-field td-field-reload-mode" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-reload-mode-group" style="display:flex;flex-direction:column;gap:4px;">';
    for (var i = 0; i < field.options.length; i++) {
      var opt = field.options[i];
      var checked = (opt.value === val) ? ' checked' : '';
      html += '<label style="font-size:12px;cursor:pointer;">';
      html += '<input type="radio" name="reload_mode" class="td-reload-mode-radio" data-value="' + opt.value + '"' + checked + '>';
      html += '<span>' + _escHtml(opt.label) + '</span>';
      html += '</label>';
    }
    html += '</div>';
    html += '</div>';
    return html;
  });

  // 公式编辑器（DZH条件专用：显示解码后的公式文本 + 自动Base64编码回写）
  ComponentRegistry.register('formula_editor', function (field, data) {
    var val = DataBinder.get(data, field.data_path, '');
    // data_path 指向 formula_decoded（后端已解码的明文），直接使用
    var decoded = val || '';
    var encodeTo = field.encode_to || '';
    var html = '<div class="td-field td-field-formula" data-key="' + field.key + '" data-path="' + field.data_path + '"';
    if (encodeTo) html += ' data-encode-to="' + encodeTo + '"';
    html += '>';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-formula-wrap">';
    html += '<textarea class="td-formula-textarea" data-key="' + field.key + '_decoded" style="font-family:Consolas,monospace;font-size:11px;width:100%;min-height:80px;padding:4px 8px;border:1px solid var(--border-color);border-radius:3px;resize:vertical;background:var(--bg-primary);color:var(--text-primary);">' + _escHtml(decoded) + '</textarea>';
    html += '<div class="td-formula-actions" style="margin-top:4px;display:flex;gap:6px;">';
    html += '<button type="button" class="td-btn td-btn-primary td-btn-sm td-formula-validate" data-key="' + field.key + '_validate" style="padding:3px 10px;font-size:11px;">验证公式</button>';
    html += '<span class="td-formula-encoded-hint" style="font-size:10px;color:var(--text-secondary);">修改后自动编码为Base64</span>';
    html += '</div>';
    html += '<div class="td-formula-result" style="margin-top:4px;font-size:11px;display:none;"></div>';
    html += '</div>';
    html += '</div>';
    return html;
  });

  // 股票数据表格组件（TDX状态池专用）
  ComponentRegistry.register('stock_data_table', function (field, data) {
    var stocks = DataBinder.get(data, field.data_path, []);
    if (!Array.isArray(stocks)) stocks = [];

    var columns = [
      {key: 'code', label: '代码'},
      {key: 'name', label: '名称'},
      {key: 'now', label: '现价'},
      {key: 'rise', label: '涨幅'},
      {key: 'income', label: '收益'},
      {key: 'volume', label: '成交量'},
      {key: 'inprice', label: '入池价'},
      {key: 'indate', label: '入池日期'},
      {key: 'maxrate', label: '最大收益率'},
      {key: 'idaynum', label: '持仓天数'}
    ];

    var html = '<div class="td-field td-field-stock-data" data-key="' + field.key + '" data-path="' + field.data_path + '">';
    html += '<label>' + field.label + '</label>';
    html += '<div class="td-stock-data-wrap">';
    html += '<span class="td-stock-data-count">共 ' + stocks.length + ' 只</span>';
    html += '<table class="td-stock-data-table">';
    html += '<thead><tr>';
    for (var c = 0; c < columns.length; c++) {
      html += '<th>' + columns[c].label + '</th>';
    }
    html += '</tr></thead>';
    html += '<tbody>';
    for (var i = 0; i < stocks.length; i++) {
      var stk = stocks[i];
      html += '<tr>';
      for (var c2 = 0; c2 < columns.length; c2++) {
        var colKey = columns[c2].key;
        var cellVal = stk[colKey] != null ? stk[colKey] : '-';
        var cellClass = '';
        // 涨幅/收益红绿着色
        if (colKey === 'rise' || colKey === 'income' || colKey === 'maxrate') {
          var numVal = parseFloat(cellVal);
          if (!isNaN(numVal)) {
            cellClass = numVal > 0 ? ' td-cell-red' : (numVal < 0 ? ' td-cell-green' : '');
          }
        }
        html += '<td class="' + cellClass + '">' + _escHtml(String(cellVal)) + '</td>';
      }
      html += '</tr>';
    }
    html += '</tbody></table>';
    html += '</div></div>';
    return html;
  });

  // K线图组件
  ComponentRegistry.register('kline_chart', function (field, data) {
    var html = '<div class="td-field td-field-kline-chart" data-key="' + field.key + '" data-path="' + (field.data_path || '') + '">';
    html += '<label>' + _escHtml(field.label || 'K线图') + '</label>';
    html += '<div class="td-kline-chart-container" data-key="' + field.key + '"></div>';
    html += '</div>';
    return html;
  });

  // 指标走势图组件
  ComponentRegistry.register('indicator_chart', function (field, data) {
    var html = '<div class="td-field td-field-indicator-chart" data-key="' + field.key + '" data-path="' + (field.data_path || '') + '">';
    html += '<label>' + _escHtml(field.label || '指标走势') + '</label>';
    html += '<div class="td-indicator-chart-container" data-key="' + field.key + '"></div>';
    html += '</div>';
    return html;
  });

  // 规则编辑器组件
  ComponentRegistry.register('rule_editor', function (field, data) {
    var html = '<div class="td-field td-field-rule-editor" data-key="' + field.key + '" data-path="' + (field.data_path || '') + '">';
    html += '<label>' + _escHtml(field.label || '规则编辑') + '</label>';
    html += '<div class="td-rule-editor-container" data-key="' + field.key + '">';
    html += '<div class="td-rule-editor-placeholder">规则编辑器需配合 rule-editor.js 使用</div>';
    html += '</div>';
    html += '</div>';
    return html;
  });

  // 股票列表组件（支持虚拟滚动，>100条自动启用）
  ComponentRegistry.register('stock_list', function (field, data) {
    var stocks = DataBinder.get(data, field.data_path, []);
    if (typeof stocks === 'string') {
      try { stocks = JSON.parse(stocks); } catch (e) { stocks = []; }
    }
    if (!Array.isArray(stocks)) stocks = [];

    var useVirtualScroll = field.virtual_scroll !== false && stocks.length > 100;
    var rowHeight = field.row_height || 32;
    var visibleRows = field.visible_rows || 20;
    var columns = field.columns || ['code', 'name', 'now', 'rise', 'volume', 'inprice', 'indate', 'maxrate', 'idaynum'];

    var html = '<div class="td-field td-field-stock-list" data-key="' + field.key + '" data-path="' + (field.data_path || '') + '">';
    html += '<label>' + _escHtml(field.label || '股票列表') + '</label>';
    html += '<span class="td-stock-list-count">共 ' + stocks.length + ' 只</span>';

    var containerHeight = useVirtualScroll ? (visibleRows * rowHeight + 28) : 'auto';
    html += '<div class="td-stock-list-container" style="max-height:' + containerHeight + 'px;overflow-y:auto;border:1px solid var(--border-color);border-radius:4px;background:var(--bg-secondary);"';
    if (useVirtualScroll) {
      html += ' data-virtual-scroll="1" data-row-height="' + rowHeight + '" data-visible-rows="' + visibleRows + '" data-total-count="' + stocks.length + '"';
    }
    html += '>';

    // 表头
    var colLabels = { 'code': '代码', 'name': '名称', 'now': '现价', 'rise': '涨幅', 'volume': '成交量', 'inprice': '入池价', 'indate': '入池日期', 'maxrate': '最大收益率', 'idaynum': '持仓天数' };
    html += '<table class="td-stock-list-table" style="width:100%;border-collapse:collapse;font-size:11px;table-layout:fixed;">';
    html += '<thead style="position:sticky;top:0;z-index:1;">';
    html += '<tr style="background:var(--bg-input);">';
    for (var c = 0; c < columns.length; c++) {
      html += '<th style="padding:3px 5px;text-align:left;color:var(--text-secondary);font-weight:500;border-bottom:1px solid var(--border-color);">' + (colLabels[columns[c]] || columns[c]) + '</th>';
    }
    html += '</tr></thead>';

    if (useVirtualScroll) {
      // 虚拟滚动模式：占位容器 + 仅渲染可见行
      var totalHeight = stocks.length * rowHeight;
      html += '<tbody style="position:relative;height:' + totalHeight + 'px;display:block;">';
      // 初始渲染前 visibleRows 条
      var renderCount = Math.min(visibleRows + 3, stocks.length); // +3 overscan
      for (var i = 0; i < renderCount; i++) {
        var stk = stocks[i];
        html += '<tr style="position:absolute;top:' + (i * rowHeight) + 'px;height:' + rowHeight + 'px;display:flex;width:100%;" data-row-idx="' + i + '">';
        for (var c2 = 0; c2 < columns.length; c2++) {
          var colKey = columns[c2];
          var cellVal = stk[colKey] != null ? stk[colKey] : '-';
          var cellClass = '';
          if (colKey === 'rise' || colKey === 'maxrate') {
            var numVal = parseFloat(cellVal);
            if (!isNaN(numVal)) {
              cellClass = numVal > 0 ? 'td-cell-red' : (numVal < 0 ? 'td-cell-green' : '');
            }
          }
          html += '<td style="flex:1;padding:2px 5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;' + (cellClass ? 'color:' + (cellClass === 'td-cell-red' ? '#ff4444' : '#00cc66') : '') + '">' + _escHtml(String(cellVal)) + '</td>';
        }
        html += '</tr>';
      }
      html += '</tbody>';
      // 存储完整数据供虚拟滚动使用
      html += '<script type="application/json" class="td-stock-list-data" data-key="' + field.key + '">' + _escHtml(JSON.stringify(stocks)) + '</' + 'script>';
    } else {
      // 普通渲染模式（≤100条）
      html += '<tbody>';
      for (var j = 0; j < stocks.length; j++) {
        var stk2 = stocks[j];
        html += '<tr>';
        for (var c3 = 0; c3 < columns.length; c3++) {
          var colKey2 = columns[c3];
          var cellVal2 = stk2[colKey2] != null ? stk2[colKey2] : '-';
          var cellClass2 = '';
          if (colKey2 === 'rise' || colKey2 === 'maxrate') {
            var numVal2 = parseFloat(cellVal2);
            if (!isNaN(numVal2)) {
              cellClass2 = numVal2 > 0 ? 'td-cell-red' : (numVal2 < 0 ? 'td-cell-green' : '');
            }
          }
          html += '<td class="' + cellClass2 + '">' + _escHtml(String(cellVal2)) + '</td>';
        }
        html += '</tr>';
      }
      html += '</tbody>';
    }

    html += '</table></div></div>';
    return html;
  });

  // Canvas容器组件
  ComponentRegistry.register('canvas_container', function (field, data) {
    var html = '<div class="td-field td-field-canvas-container" data-key="' + field.key + '" data-path="' + (field.data_path || '') + '">';
    html += '<label>' + _escHtml(field.label || 'Canvas画布') + '</label>';
    html += '<div class="td-canvas-container" data-key="' + field.key + '" style="position:relative;border:1px solid var(--border-color);border-radius:4px;background:var(--bg-primary);min-height:400px;overflow:hidden;">';
    html += '<canvas class="td-canvas-layer" data-key="' + field.key + '" style="position:absolute;top:0;left:0;width:100%;height:100%;"></canvas>';
    html += '<svg class="td-canvas-svg-layer" data-key="' + field.key + '" style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;"></svg>';
    html += '<div class="td-canvas-controls" style="position:absolute;bottom:8px;right:8px;display:flex;gap:4px;z-index:5;">';
    html += '<button class="td-canvas-zoom-in btn-sm" data-key="' + field.key + '" title="放大">+</button>';
    html += '<button class="td-canvas-zoom-out btn-sm" data-key="' + field.key + '" title="缩小">-</button>';
    html += '<span class="td-canvas-zoom-level" style="font-size:11px;color:var(--text-secondary);padding:2px 6px;">100%</span>';
    html += '</div></div>';
    html += '</div>';
    return html;
  });

  // 属性面板容器组件
  ComponentRegistry.register('property_panel', function (field, data) {
    var html = '<div class="td-field td-field-property-panel" data-key="' + field.key + '" data-path="' + (field.data_path || '') + '">';
    html += '<div class="td-property-panel" data-key="' + field.key + '" style="display:flex;flex-direction:column;height:100%;">';
    if (field.show_tabs !== false) {
      html += '<div class="td-property-panel-tabs prop-tabs" data-key="' + field.key + '">';
      html += '<button class="td-property-tab prop-tab active" data-tab="props">属性</button>';
      html += '<button class="td-property-tab prop-tab" data-tab="charts">图表</button>';
      html += '<button class="td-property-tab prop-tab" data-tab="rules">规则</button>';
      html += '</div>';
    }
    html += '<div class="td-property-panel-body" data-key="' + field.key + '" style="flex:1;overflow-y:auto;padding:12px;"></div>';
    html += '</div></div>';
    return html;
  });

  // Canvas画布组件（独立Canvas渲染）
  ComponentRegistry.register('canvas', function (field, data) {
    var html = '<div class="td-field td-field-canvas" data-key="' + field.key + '" data-path="' + (field.data_path || '') + '">';
    html += '<label>' + _escHtml(field.label || 'Canvas') + '</label>';
    html += '<div class="td-canvas-wrapper" data-key="' + field.key + '" style="position:relative;width:100%;min-height:400px;background:var(--bg-primary);border:1px solid var(--border-color);border-radius:4px;overflow:hidden;">';
    html += '<canvas class="td-canvas-render" data-key="' + field.key + '" style="position:absolute;top:0;left:0;width:100%;height:100%;"></canvas>';
    html += '</div></div>';
    return html;
  });

  // ─── 面板引擎主类 ────────────────────────────────────────────

  class TableDrivenPanel {
    constructor(containerEl, titleEl, poolData) {
      this.container = typeof containerEl === 'string'
        ? document.querySelector(containerEl)
        : containerEl;
      this.titleEl = typeof titleEl === 'string'
        ? document.querySelector(titleEl)
        : titleEl;
      this.poolData = poolData || null;
      this.apiBase = '/api/v1/table';
      this._layoutCache = new window.LRUCache(50, 5 * 60 * 1000);
      this._data = {};
      this._nodeType = '';
      this._poolType = 'dzh';
      this._changeListeners = [];
      this._reloadTimer = null;
      this._hotReloadInterval = 0;
      this._readOnly = false;
      // 当前编辑的节点/边信息（用于数据写回）
      this._currentNodeId = null;
      this._currentEdgeId = null;
      this._currentItem = null;
      this._persistTimer = null;
      // onPropertyChange 回调（兼容 PropertyPanel 接口）
      this.onPropertyChange = null;
    }

    // ── TDX Lookup 数据缓存 ──────────────────────────────

    // 显示节点面板（兼容 PropertyPanel 接口：接受 nodeId）
    showForNode(nodeId) {
      this._currentEdgeId = null;

      // 如果传入的是 nodeId 字符串，通过 poolData 解析
      if (typeof nodeId === 'string' && this.poolData) {
        var node = this.poolData.getNodeById(nodeId);
        if (!node) {
          this.container.innerHTML = '<div class="td-error">节点不存在: ' + _escHtml(nodeId) + '</div>';
          return;
        }
        this._currentNodeId = nodeId;
        this._currentItem = node;

        // 判定 poolType
        var poolMeta = this.poolData.data && this.poolData.data.pool_meta;
        if ((poolMeta && poolMeta.type === 'tdx') || (node.type && node.type.indexOf('tdx_') === 0)) {
          this._poolType = 'tdx';
        } else {
          this._poolType = 'dzh';
        }

        this._nodeType = node.type || '';
        // 构建完整 _data：
        //   - TDX 节点的 data_path 以 "params." 开头（如 "params.tdx_psatt.baimpool"），需要包装一层 params
        //   - DZH 节点的 data_path 直接用字段名，不需要额外包装
        //   - 顶层节点属性(id/type/label/text)始终包含
        var isTDXNode = this._poolType === 'tdx' || (node.type && node.type.indexOf('tdx_') === 0);
        if (isTDXNode && node.params) {
          this._data = Object.assign(
            { id: node.id, type: node.type, label: node.label || '', text: node.text || '' },
            { params: _deepClone(node.params) }
          );
        } else {
          this._data = Object.assign(
            { id: node.id, type: node.type, label: node.label || '', text: node.text || '' },
            node.params ? _deepClone(node.params) : {}
          );
        }

        // DZH 转移条件节点(type=201)：formula_decoded 缺失时调用后端解码 API（支持 GBK + ency 加密）
        if (String(this._nodeType) === '201') {
          var indiBase64 = DataBinder.get(this._data, 'indi');
          var existingDecoded = DataBinder.get(this._data, 'formula_decoded');
          if (indiBase64 && !existingDecoded) {
            this._decodeFormulaViaBackend(indiBase64, this._getPoolEncy());
          }
        }

        // 设置面板标题
        if (this.titleEl) {
          this.titleEl.textContent = node.label || node.type || '节点属性';
        }

        // 构建精简数据给API（排除股票列表等大字段，避免请求体过大导致422/500）
        var slimData = {};
        Object.keys(this._data).forEach(function(k) {
          if (k !== 'stocks' && k !== '_orig_stks' && k !== 'stock_list' && k !== 'tdx_stocks') {
            slimData[k] = this._data[k];
          }
        }.bind(this));

        var self = this;
        this._fetchPanelConfig(this._nodeType, this._poolType, slimData, function (config) {
          self._renderPanel(config);
          self._bindEvents();
          if (self._readOnly) self.setReadOnly(true);
        });
      }
      // 兼容旧调用方式：直接传 nodeType, poolType, data
      else if (typeof nodeId === 'object' && nodeId !== null) {
        // 检测传入的是否为画布节点对象（有 type/params/id 字段）
        if (nodeId.type && (nodeId.params || nodeId.id)) {
          // 这是节点对象，按字符串分支的逻辑处理
          this._currentNodeId = nodeId.id;
          this._currentItem = nodeId;

          var poolMeta2 = this.poolData && this.poolData.data && this.poolData.data.pool_meta;
          if ((poolMeta2 && poolMeta2.type === 'tdx') || (nodeId.type.indexOf('tdx_') === 0)) {
            this._poolType = 'tdx';
          } else {
            this._poolType = 'dzh';
          }

          this._nodeType = nodeId.type || '';
          // 构建完整 _data：同字符串分支的条件包装逻辑
          var isTDXNode2 = this._poolType === 'tdx' || (nodeId.type && nodeId.type.indexOf('tdx_') === 0);
          if (isTDXNode2 && nodeId.params) {
            this._data = Object.assign(
              { id: nodeId.id, type: nodeId.type, label: nodeId.label || '', text: nodeId.text || '' },
              { params: _deepClone(nodeId.params) }
            );
          } else {
            this._data = Object.assign(
              { id: nodeId.id, type: nodeId.type, label: nodeId.label || '', text: nodeId.text || '' },
              nodeId.params ? _deepClone(nodeId.params) : {}
            );
          }

          // DZH 转移条件节点(type=201)：formula_decoded 缺失时调用后端解码 API（支持 GBK + ency 加密）
          if (String(this._nodeType) === '201') {
            var indiBase642 = DataBinder.get(this._data, 'indi');
            var existingDecoded2 = DataBinder.get(this._data, 'formula_decoded');
            if (indiBase642 && !existingDecoded2) {
              this._decodeFormulaViaBackend(indiBase642, this._getPoolEncy());
            }
          }

          if (this.titleEl) {
            this.titleEl.textContent = nodeId.label || nodeId.type || '节点属性';
          }

          var slimData2 = {};
          Object.keys(this._data).forEach(function(k) {
            if (k !== 'stocks' && k !== '_orig_stks' && k !== 'stock_list' && k !== 'tdx_stocks') {
              slimData2[k] = this._data[k];
            }
          }.bind(this));

          var self2 = this;
          this._fetchPanelConfig(this._nodeType, this._poolType, slimData2, function (config) {
            self2._renderPanel(config);
            self2._bindEvents();
            if (self2._readOnly) self2.setReadOnly(true);
          });
        } else {
          // 真正的旧接口 options 对象 {nodeType, poolType, data}
          this._nodeType = arguments[0] || '';
          this._poolType = arguments[1] || 'dzh';
          this._data = arguments[2] || {};
          var self3 = this;
          this._fetchPanelConfig(this._nodeType, this._poolType, this._data, function (config) {
            self3._renderPanel(config);
            self3._bindEvents();
          });
        }
      }
    }

    // 显示边面板（兼容 PropertyPanel 接口：接受 edgeId）
    showForEdge(edgeId) {
      this._currentNodeId = null;

      // 如果传入的是 edgeId 字符串，通过 poolData 解析
      if (typeof edgeId === 'string' && this.poolData) {
        var edge = this.poolData.getEdgeById(edgeId);
        if (!edge) {
          this.container.innerHTML = '<div class="td-error">连线不存在: ' + _escHtml(edgeId) + '</div>';
          return;
        }
        this._currentEdgeId = edgeId;
        this._currentItem = edge;

        // 判定 poolType：边参数含 tran/jgtime → tdx
        var ep = edge.params || {};
        if (ep.tran !== undefined || ep.jgtime !== undefined) {
          this._poolType = 'tdx';
        } else {
          var poolMeta2 = this.poolData.data && this.poolData.data.pool_meta;
          this._poolType = (poolMeta2 && poolMeta2.type === 'tdx') ? 'tdx' : 'dzh';
        }

        this._nodeType = 'flow';
        this._data = edge.params ? _deepClone(edge.params) : {};

        // 注入源/目标节点标签（用于 flow_info 组件显示）
        if (this.poolData) {
          var srcNode = this.poolData.getNodeById(edge.source ? edge.source.node_id : null);
          var tgtNode = this.poolData.getNodeById(edge.target ? edge.target.node_id : null);
          this._data._source_label = srcNode ? srcNode.label : '?';
          this._data._target_label = tgtNode ? tgtNode.label : '?';
        }

        // 设置面板标题
        if (this.titleEl) {
          this.titleEl.textContent = '连线属性';
        }

        // 构建精简数据给API（排除大字段）
        var slimData2 = {};
        Object.keys(this._data).forEach(function(k) {
          if (k !== 'stocks' && k !== '_orig_stks' && k !== 'stock_list' && k !== 'tdx_stocks') {
            slimData2[k] = this._data[k];
          }
        }.bind(this));

        var self = this;
        this._fetchPanelConfig(this._nodeType, this._poolType, slimData2, function (config) {
          self._renderPanel(config);
          self._bindEvents();
          if (self._readOnly) self.setReadOnly(true);
        });
      }
    }

    // 显示池元数据面板（兼容 PropertyPanel 接口）
    showForPool(poolMeta) {
      this._currentNodeId = null;
      this._currentEdgeId = null;
      this._currentItem = null;
      this._nodeType = 'pool_meta';
      this._poolType = (poolMeta && poolMeta.type === 'tdx') ? 'tdx' : 'dzh';
      this._data = poolMeta ? _deepClone(poolMeta) : {};

      if (this.titleEl) {
        this.titleEl.textContent = '股票池属性';
      }

      // Try loading from ui_layouts pool_meta
      var self = this;
      this._fetchPanelConfig('pool_meta', 'any', this._data, function(config) {
        if (config && config.sections) {
          self._renderPanel(config);
          self._bindEvents();
          if (self._readOnly) self.setReadOnly(true);
        } else {
          // Fallback to hardcoded fields
          self._renderPoolMetaFallback(poolMeta);
        }
      });
    }

    // 池元数据面板硬编码兜底渲染
    _renderPoolMetaFallback(poolMeta) {
      // 渲染池元数据面板
      var html = '<div class="td-panel">';
      html += '<div class="td-panel-header">股票池属性</div>';
      html += '<div class="td-section"><div class="td-section-body">';

      var fields = [
        { key: 'type', label: '类型', value: poolMeta ? poolMeta.type : '', readonly: true },
        { key: 'ver', label: '版本', value: poolMeta ? poolMeta.ver : '', readonly: true },
        { key: 'mode', label: '模式', value: poolMeta ? poolMeta.mode : '', readonly: false },
        { key: 'nextid', label: '下一个ID', value: poolMeta ? poolMeta.nextid : '', readonly: false },
        { key: 'backcolor', label: '背景色', value: poolMeta ? poolMeta.backcolor : 16777216, readonly: false, comp: 'color_picker' },
        { key: 'name', label: '名称', value: poolMeta ? poolMeta.name : '', readonly: false }
      ];

      for (var i = 0; i < fields.length; i++) {
        var f = fields[i];
        if (f.comp === 'color_picker') {
          var colorHex = (f.value >= 0) ? _intToHex(f.value) : '#ffffff';
          html += '<div class="td-field td-field-color" data-key="' + f.key + '">';
          html += '<label>' + f.label + '</label>';
          html += '<div class="td-color-wrap">';
          html += '<input type="color" class="td-color-input" data-path="' + f.key + '" value="' + colorHex + '">';
          html += '<span class="td-color-value">' + f.value + '</span>';
          html += '</div></div>';
        } else {
          html += '<div class="td-field td-field-text" data-key="' + f.key + '">';
          html += '<label>' + f.label + '</label>';
          html += '<input type="text" class="td-input" data-path="' + f.key + '" value="' + _escHtml(f.value) + '"';
          if (f.readonly) html += ' readonly';
          html += '></div>';
        }
      }

      html += '</div></div></div>';
      this.container.innerHTML = html;

      // 绑定事件
      var self = this;
      var panel = this.container.querySelector('.td-panel');
      if (panel) {
        panel.addEventListener('change', function (e) {
          self._handlePoolChange(e.target);
        });
        panel.addEventListener('input', function (e) {
          if (e.target.tagName === 'INPUT' && e.target.type === 'text') {
            self._handlePoolChange(e.target);
          }
        });
      }
    }

    // 池元数据变更处理
    _handlePoolChange(target) {
      var path = target.getAttribute('data-path');
      if (!path) return;

      var value;
      if (target.classList.contains('td-color-input')) {
        value = _hexToInt(target.value);
        var valSpan = target.parentElement.querySelector('.td-color-value');
        if (valSpan) valSpan.textContent = value;
      } else {
        value = target.value;
      }

      // 写回 poolData
      if (this.poolData && this.poolData.data && this.poolData.data.pool_meta) {
        this.poolData.data.pool_meta[path] = value;
        this.poolData._notify();
      }

      // 触发 onPropertyChange
      if (this.onPropertyChange) {
        this.onPropertyChange(this.poolData.data.pool_meta, path, value);
      }
    }

    // 显示占位提示（兼容 PropertyPanel 接口）
    showPlaceholder() {
      this._currentNodeId = null;
      this._currentEdgeId = null;
      this._currentItem = null;
      this._nodeType = '';
      this._data = {};

      if (this.titleEl) {
        this.titleEl.textContent = '属性面板';
      }

      this.container.innerHTML =
        '<div class="panel-placeholder">' +
        '<div class="placeholder-icon">📋</div>' +
        '<div class="placeholder-text">选择节点或连线<br>查看/编辑属性</div>' +
        '</div>';
    }

    // 销毁面板
    destroy() {
      if (this._reloadTimer) {
        clearInterval(this._reloadTimer);
        this._reloadTimer = null;
      }
      if (this._encodeTimer) {
        clearTimeout(this._encodeTimer);
        this._encodeTimer = null;
      }
      if (this._persistTimer) {
        clearTimeout(this._persistTimer);
        this._persistTimer = null;
      }
      this.container.innerHTML = '';
      this._changeListeners = [];
    }

    // 重新渲染当前面板（用于转移模式等需要刷新整个面板的操作）
    _reRenderCurrentPanel() {
      if (this._currentNodeId) {
        this.showForNode(this._currentNodeId);
      } else if (this._currentEdgeId) {
        this.showForEdge(this._currentEdgeId);
      }
    }

    // 注册变更监听器
    onChange(listener) {
      this._changeListeners.push(listener);
    }

    // 设置只读模式
    setReadOnly(readOnly) {
      this._readOnly = !!readOnly;
      var panel = this.container.querySelector('.td-panel');
      if (!panel) return;

      if (readOnly) {
        panel.classList.add('td-readonly');
        var inputs = panel.querySelectorAll('input, select, textarea, button');
        for (var i = 0; i < inputs.length; i++) {
          inputs[i].disabled = true;
        }
      } else {
        panel.classList.remove('td-readonly');
        var inputs2 = panel.querySelectorAll('input, select, textarea, button');
        for (var j = 0; j < inputs2.length; j++) {
          inputs2[j].disabled = false;
        }
        // 恢复联动隐藏的字段
        this._handleLinkage('*', null);
      }
    }

    // 获取当前数据
    getData() {
      return this._data;
    }

    // ─── 内部方法 ────────────────────────────────────────────────

    _fetchPanelConfig(nodeType, poolType, data, callback) {
      var cacheKey = nodeType + ':' + poolType;
      if (this._layoutCache.has(cacheKey)) {
        callback(this._buildPanelFromLayout(this._layoutCache.get(cacheKey), data));
        return;
      }

      var self = this;
      this._ajax('POST', this.apiBase + '/panel', {
        node_type: nodeType,
        pool_type: poolType,
        data: data
      }, function (resp) {
        if (resp && !resp.error) {
          // 边属性面板：补充计算参数与 K 线配置字段（Task 1）
          if ((resp.layout_id === 'flow_edge' || resp.layout_id === 'tdx_flow_edge') && resp.sections) {
            var existingKeys = {};
            resp.sections.forEach(function(sec) {
              (sec.fields || []).forEach(function(f) { existingKeys[f.key] = true; });
            });
            var extraSections = [
              {
                title: '计算参数',
                collapsible: true,
                fields: [
                  { key: 'formula_ref', comp: 'text_input', label: '公式引用', data_path: 'formula_ref', default: '', hint: '如 KDJ / MACD' },
                  { key: 'operator', comp: 'select', label: '操作符', data_path: 'operator', default: '', options: [
                    { value: '', label: '未设置' }, { value: '0', label: '等于' }, { value: '1', label: '大于' },
                    { value: '2', label: '小于' }, { value: '3', label: '金叉' }, { value: '4', label: '死叉' },
                    { value: '5', label: '排名为' }, { value: '6', label: '排名前N' }, { value: '7', label: '排名后N' },
                    { value: '8', label: '上拐' }, { value: '9', label: '下拐' }
                  ]},
                  { key: 'threshold', comp: 'number_input', label: '阈值', data_path: 'threshold', default: '', step: 0.01 },
                  { key: 'nset', comp: 'select', label: '公式类型(nset)', data_path: 'nset', default: '', options: [
                    { value: '', label: '未设置' }, { value: '0', label: '技术指标' }, { value: '1', label: '条件选股' },
                    { value: '2', label: '专家系统' }, { value: '3', label: '最新财务' },
                    { value: '4', label: '实时行情' }, { value: '5', label: '逻辑运算' }
                  ]},
                  { key: 'noperate', comp: 'select', label: '操作(noperate)', data_path: 'noperate', default: '', options: [
                    { value: '', label: '未设置' }, { value: '0', label: '等于' }, { value: '1', label: '大于' },
                    { value: '2', label: '小于' }, { value: '3', label: '金叉' }, { value: '4', label: '死叉' },
                    { value: '5', label: '排名为' }, { value: '6', label: '排名前N' }, { value: '7', label: '排名后N' },
                    { value: '8', label: '上拐' }, { value: '9', label: '下拐' }
                  ]}
                ]
              },
              {
                title: 'K 线配置',
                collapsible: true,
                fields: [
                  { key: 'period', comp: 'select', label: '周期(period)', data_path: 'period', default: '', options: [
                    { value: '', label: '未设置' }, { value: '0', label: '分笔' }, { value: '1', label: '1分钟' },
                    { value: '2', label: '5分钟' }, { value: '3', label: '15分钟' }, { value: '4', label: '30分钟' },
                    { value: '5', label: '60分钟' }, { value: '6', label: '日线' },
                    { value: '7', label: '周线' }, { value: '8', label: '月线' }
                  ]},
                  { key: 'length', comp: 'number_input', label: '长度(length)', data_path: 'length', default: '', min: 0 },
                  { key: 'bar_type', comp: 'select', label: 'K线类型(bar_type)', data_path: 'bar_type', default: '', options: [
                    { value: '', label: '未设置' }, { value: '0', label: '分笔' }, { value: '1', label: '1分钟' },
                    { value: '5', label: '5分钟' }, { value: '15', label: '15分钟' }, { value: '30', label: '30分钟' },
                    { value: '60', label: '60分钟' }, { value: 'daily', label: '日线' },
                    { value: 'weekly', label: '周线' }, { value: 'monthly', label: '月线' }
                  ]}
                ]
              }
            ];
            extraSections.forEach(function(sec) {
              sec.fields = sec.fields.filter(function(f) { return !existingKeys[f.key]; });
              if (sec.fields.length) resp.sections.push(sec);
            });
          }
          // 缓存布局配置（用于后续字段查找和联动处理）
          if (resp.layout_id) {
            self._layoutCache.set(cacheKey, resp);
          }
          callback(resp);
        } else {
          self.container.innerHTML = '<div class="td-error">面板配置加载失败: ' + (resp.error || '未知错误') + '</div>';
        }
      }, function () {
        self.container.innerHTML = '<div class="td-error">面板配置请求失败</div>';
      });
    }

    _buildPanelFromLayout(layout, data) {
      // 从缓存的layout + 当前data构建panel配置
      var panel = {
        layout_id: layout.layout_id,
        name: layout.name,
        pool_type: layout.pool_type,
        node_type: layout.node_type,
        blocked_attrs: layout.blocked_attrs,
        allowed_attrs: layout.allowed_attrs,
        disabled_fields: layout.disabled_fields,
        sections: []
      };

      for (var s = 0; s < layout.sections.length; s++) {
        var section = layout.sections[s];
        var sectionData = {
          title: section.title,
          collapsible: section.collapsible,
          fields: []
        };
        for (var f = 0; f < section.fields.length; f++) {
          var field = section.fields[f];
          var fieldData = this._resolveField(field, data);
          sectionData.fields.push(fieldData);
        }
        panel.sections.push(sectionData);
      }
      return panel;
    }

    _resolveField(fieldConfig, data) {
      var result = {
        key: fieldConfig.key,
        comp: fieldConfig.comp,
        label: fieldConfig.label,
        data_path: fieldConfig.data_path
      };

      // 复制配置属性
      var copyKeys = ['options', 'min', 'max', 'step', 'readonly', 'disabled', 'nullable',
        'depends_on', 'active_when', 'markets', 'unit_options', 'flags',
        'flag_source', 'hhmmss_modes', 'action_types', 'default', 'hint', 'encode_to', 'rows'];
      for (var i = 0; i < copyKeys.length; i++) {
        if (fieldConfig[copyKeys[i]] !== undefined) {
          result[copyKeys[i]] = fieldConfig[copyKeys[i]];
        }
      }

      // 从数据取值
      var value = DataBinder.get(data, fieldConfig.data_path);

      if (fieldConfig.comp === 'flag_group') {
        var flags = fieldConfig.flags || [];
        result.value = (value != null)
          ? DataBinder.decodeAttrFlags(value, flags)
          : {};
        for (var j = 0; j < flags.length; j++) {
          if (!(flags[j].name in result.value)) result.value[flags[j].name] = false;
        }
      } else if (fieldConfig.comp === 'action_compound') {
        result.value = (value != null)
          ? DataBinder.decodeActionCompound(value)
          : { action_type: 0, param: 0 };
      } else {
        result.value = (value != null) ? value : fieldConfig.default;
      }

      return result;
    }

    _renderPanel(config) {
      var html = '<div class="td-panel" data-layout="' + (config.layout_id || '') + '">';
      html += '<div class="td-panel-header">';
      html += '<span class="td-panel-title">' + _escHtml(config.name || '属性') + '</span>';

      // 池类型标识标签
      var poolTypeLabel = this._poolType === 'tdx' ? 'TDX' : 'DZH';
      var poolTypeClass = this._poolType === 'tdx' ? 'td-pool-tag-tdx' : 'td-pool-tag-dzh';
      html += '<span class="td-pool-tag ' + poolTypeClass + '">' + poolTypeLabel + '</span>';
      html += '</div>';

      // 封锁属性警告
      if (config.blocked_attrs && config.blocked_attrs.length > 0) {
        html += '<div class="td-blocked-notice">';
        html += '<span class="td-blocked-icon">&#9888;</span>';
        html += '<span class="td-blocked-text">' + poolTypeLabel + '池不支持部分属性编辑，已自动隐藏</span>';
        html += '</div>';
      }

      // 如果是边面板，显示流转模式标签
      if (this._nodeType === 'flow' && this._data && this._data.attr !== undefined) {
        var modeInfo = this._resolveFlowModeDisplay(this._data.attr);
        if (modeInfo) {
          html += '<div class="td-mode-tag" style="background:' + modeInfo.color + '">' + _escHtml(modeInfo.label) + '</div>';
        }
      }

      for (var s = 0; s < config.sections.length; s++) {
        var section = config.sections[s];
        html += '<div class="td-section" data-section="' + s + '">';
        html += '<div class="td-section-header">';
        html += '<span class="td-section-title">' + _escHtml(section.title) + '</span>';
        if (section.collapsible) {
          html += '<span class="td-section-toggle">▼</span>';
        }
        html += '</div>';
        html += '<div class="td-section-body">';

        for (var f = 0; f < section.fields.length; f++) {
          var field = section.fields[f];
          var isFieldDisabled = field.disabled || field.readonly;
          var renderer = ComponentRegistry.get(field.comp);

          if (isFieldDisabled) {
            // disabled 字段：渲染为灰显只读
            html += '<div class="td-field td-field-disabled" data-key="' + _escAttr(field.key || '') + '">';
            html += '<label class="td-disabled-label">' + _escHtml(field.label || field.key || '') + '</label>';
            html += '<span class="td-disabled-value">' + _escHtml(_formatFieldValue(field)) + '</span>';
            html += '</div>';
          } else {
            var fieldHtml = renderer(field, this._data);

            // 处理字段联动（depends_on）
            if (field.depends_on && field.active_when) {
              // 通过 _getFieldPath 将 key 解析为完整 data_path（如 "baimpool" → "params.tdx_psatt.baimpool"）
              var parentDataPath = this._getFieldPath(field.depends_on);
              var parentVal = DataBinder.get(this._data, parentDataPath);
              var isActive = this._checkActiveWhen(parentVal, field.active_when);
              // 仅对当前字段的 HTML 做替换（避免匹配到之前累积的字段）
              fieldHtml = fieldHtml.replace(
                'class="td-field td-field-',
                'class="td-field td-field-' + (isActive ? 'active' : 'inactive') + ' td-field-'
              );
            }

            html += fieldHtml;
          }
        }

        html += '</div></div>';
      }

      html += '</div>';
      this.container.innerHTML = html;

      // 注入"从公式库选择"按钮（TDX/DZH 转移条件面板）
      this._injectFormulaPicker();
      // 注入当前条件/周期/间隔摘要，方便用户即时查看
      this._injectConditionSummary();
    }

    /**
     * 注入"从公式库选择"按钮：
     * - TDX (tdx_condition)：在 accode 字段后注入按钮 + 参数表单容器，nset=0/1/2 时可见
     * - DZH (condition_filter)：在 formula_editor 的操作区注入按钮
     */
    _injectFormulaPicker() {
      var panelEl = this.container.querySelector('.td-panel');
      var layoutId = panelEl ? panelEl.getAttribute('data-layout') : '';
      if (layoutId === 'tdx_condition') {
        this._injectTdxFormulaPicker();
      } else if (layoutId === 'condition_filter') {
        this._injectDzhFormulaPicker();
      }
    }

    /**
     * TDX 转移条件面板：注入"从公式库选择"按钮 + 参数表单容器。
     * 按钮插入到 accode 字段之后；参数表单容器插入到按钮之后。
     * 可见性由 _refreshTdxFuncFieldsVisibility 根据 nset 控制。
     */
    _injectTdxFormulaPicker() {
      var accodeField = this.container.querySelector('.td-field[data-key="accode"]');
      if (!accodeField) return;

      // 避免重复注入
      if (this.container.querySelector('.td-formula-picker-tdx')) return;

      var wrap = document.createElement('div');
      wrap.className = 'td-field td-formula-picker-tdx';
      wrap.setAttribute('data-key', 'formula_picker');
      wrap.style.marginTop = '4px';
      wrap.innerHTML =
        '<button type="button" class="td-btn td-btn-primary td-btn-sm td-formula-pick-btn" ' +
        'style="padding:3px 10px;font-size:11px;">从公式库选择</button>' +
        '<button type="button" class="td-btn td-btn-sm td-formula-validate-btn" ' +
        'style="padding:3px 10px;font-size:11px;margin-left:6px;display:none;">验证公式</button>' +
        '<span class="td-formula-pick-name" style="margin-left:8px;font-size:11px;color:var(--text-secondary);"></span>' +
        '<div class="td-formula-result" style="margin-top:4px;font-size:11px;display:none;"></div>';

      // 参数表单容器
      var argsBox = document.createElement('div');
      argsBox.className = 'td-field td-formula-args-box';
      argsBox.setAttribute('data-key', 'formula_args');
      argsBox.style.display = 'none';
      argsBox.style.marginTop = '6px';
      argsBox.innerHTML = '<label style="font-size:11px;font-weight:600;">公式参数</label>' +
        '<div class="td-formula-args-list" style="margin-top:4px;"></div>';

      accodeField.parentNode.insertBefore(argsBox, accodeField.nextSibling);
      accodeField.parentNode.insertBefore(wrap, argsBox);

      // 绑定按钮点击
      var self = this;
      var btn = wrap.querySelector('.td-formula-pick-btn');
      btn.addEventListener('click', function () {
        self._openTdxFormulaSelector();
      });

      // 绑定验证公式按钮点击
      var validateBtn = wrap.querySelector('.td-formula-validate-btn');
      validateBtn.addEventListener('click', function () {
        var script = DataBinder.get(self._data, 'params.tdx_func.formula_script') || '';
        if (!script) {
          self._showToast && self._showToast('请先选择公式', 'error');
          return;
        }
        self._validateFormula(wrap, script);
      });

      // 初始可见性：根据当前 nset 值
      var nsetVal = DataBinder.get(this._data, 'params.tdx_func.nset');
      this._updateTdxFormulaPickerVisibility(nsetVal);

      // 若已有 formula_args 数据且按钮可见（nset=0/1/2），恢复参数表单
      var nsetNum = parseInt(nsetVal);
      if (isNaN(nsetNum)) nsetNum = 0;
      var pickerVisible = (nsetNum === 0 || nsetNum === 1 || nsetNum === 2);
      if (pickerVisible) {
        var existingArgs = DataBinder.get(this._data, 'params.tdx_func.formula_args');
        var existingScript = DataBinder.get(this._data, 'params.tdx_func.formula_script');
        if (existingArgs && typeof existingArgs === 'object' && existingScript) {
          var nameEl = wrap.querySelector('.td-formula-pick-name');
          var existingName = DataBinder.get(this._data, 'params.tdx_func.formula_name') || '';
          if (nameEl && existingName) nameEl.textContent = '已选: ' + existingName;
          // 恢复"验证公式"按钮可见
          var restoreValidateBtn = wrap.querySelector('.td-formula-validate-btn');
          if (restoreValidateBtn) restoreValidateBtn.style.display = 'inline-block';
          this._renderFormulaArgsForm(existingArgs, existingScript, existingName);
        }
      }
    }

    /**
     * 打开 TDX 公式选择器，选中后回填 accode/formula_script 并生成参数表单。
     */
    _openTdxFormulaSelector() {
      var self = this;
      var nsetVal = DataBinder.get(this._data, 'params.tdx_func.nset');
      var nsetNum = parseInt(nsetVal);
      if (isNaN(nsetNum)) nsetNum = null;

      if (!window.ComprehensiveSettings || !window.ComprehensiveSettings.utils ||
          typeof window.ComprehensiveSettings.utils.openFormulaSelector !== 'function') {
        this._showToast && this._showToast('公式选择器不可用', 'error');
        return;
      }

      window.ComprehensiveSettings.utils.openFormulaSelector(nsetNum, function (formula) {
        self._onTdxFormulaSelected(formula);
      });
    }

    /**
     * TDX 公式选中回调：回填字段并生成参数表单。
     */
    _onTdxFormulaSelected(formula) {
      // 回填 formula_script（完整脚本）和 accode（标识/脚本）
      DataBinder.set(this._data, 'params.tdx_func.formula_script', formula.script || '');
      this._notifyChange('params.tdx_func.formula_script', formula.script || '');

      // accode 字段回填公式名称作为标识（保留原 accode 语义，同时存 name）
      DataBinder.set(this._data, 'params.tdx_func.formula_name', formula.name || '');
      this._notifyChange('params.tdx_func.formula_name', formula.name || '');

      // 同步 accode 的 data_path（params.tdx_func.accode），保证保存后重新打开面板能正确显示
      DataBinder.set(this._data, 'params.tdx_func.accode', formula.name || '');
      this._notifyChange('params.tdx_func.accode', formula.name || '');

      // 同步显示到 accode 输入框（若可见）
      var accodeInput = this.container.querySelector('.td-field[data-key="accode"] input, .td-field[data-key="accode"] textarea');
      if (accodeInput) {
        accodeInput.value = formula.name || '';
      }

      // 公式已选，若分析周期未填写则立即标红提示
      var nperiodVal = DataBinder.get(this._data, 'params.tdx_func.nperiod');
      this._validateField('params.tdx_func.nperiod', nperiodVal);

      // 更新按钮旁的已选提示
      var nameEl = this.container.querySelector('.td-formula-pick-name');
      if (nameEl && formula.name) nameEl.textContent = '已选: ' + formula.name;

      // 显示"验证公式"按钮
      var validateBtn = this.container.querySelector('.td-formula-validate-btn');
      if (validateBtn) validateBtn.style.display = 'inline-block';

      // 构造 formula_args 对象并生成参数表单
      var argsObj = {};
      var argsArr = formula.args || [];
      for (var i = 0; i < argsArr.length; i++) {
        var a = argsArr[i];
        var aName = a.name || ('param' + i);
        argsObj[aName] = (a.value !== undefined ? a.value : '');
      }
      DataBinder.set(this._data, 'params.tdx_func.formula_args', argsObj);
      this._notifyChange('params.tdx_func.formula_args', argsObj);

      this._renderFormulaArgsForm(argsObj, formula.script || '', formula.name || '');
    }

    /**
     * DZH 转移条件面板：在 formula_editor 操作区注入"从公式库选择"按钮。
     */
    _injectDzhFormulaPicker() {
      var formulaField = this.container.querySelector('.td-field-formula');
      if (!formulaField) return;
      // 避免重复注入
      if (formulaField.querySelector('.td-formula-pick-btn')) return;

      var actions = formulaField.querySelector('.td-formula-actions');
      if (!actions) return;

      var self = this;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'td-btn td-btn-primary td-btn-sm td-formula-pick-btn';
      btn.textContent = '从公式库选择';
      btn.style.padding = '3px 10px';
      btn.style.fontSize = '11px';
      btn.addEventListener('click', function () {
        self._openDzhFormulaSelector();
      });
      actions.insertBefore(btn, actions.firstChild);

      // 注入参数表单容器（与 TDX 的 .td-formula-args-box 结构一致，供 _renderFormulaArgsForm 渲染）
      if (!this.container.querySelector('.td-formula-args-box')) {
        var argsBox = document.createElement('div');
        argsBox.className = 'td-field td-formula-args-box';
        argsBox.setAttribute('data-key', 'formula_args');
        argsBox.style.display = 'none';
        argsBox.style.marginTop = '6px';
        argsBox.innerHTML = '<label style="font-size:11px;font-weight:600;">公式参数</label>' +
          '<div class="td-formula-args-list" style="margin-top:4px;"></div>';
        formulaField.parentNode.insertBefore(argsBox, formulaField.nextSibling);
      }

      // 若已有 formula_args 数据，恢复参数表单（DZH 参数存储在 _data 顶层 formula_args）
      var existingArgs = DataBinder.get(this._data, 'formula_args');
      if (existingArgs && typeof existingArgs === 'object') {
        var existingScript = DataBinder.get(this._data, 'formula_decoded') || '';
        this._renderFormulaArgsForm(existingArgs, existingScript, '', 'formula_args');
      } else {
        // indiparam 兜底：导入的 DZH 节点无 formula_args，从 indiparam 解析位置参数
        // indiparam 格式如 (1,25,100,30,15,30)，按位置映射为 P1/P2/...
        var indiparamStr = DataBinder.get(this._data, 'indiparam');
        var parsedArgs = this._parseIndiparam(indiparamStr);
        if (parsedArgs) {
          var fallbackScript = DataBinder.get(this._data, 'formula_decoded') || '';
          // 同步生成 formula_args dict，供后端 formula_eval 优先使用
          DataBinder.set(this._data, 'formula_args', parsedArgs);
          this._notifyChange('formula_args', parsedArgs);
          this._renderFormulaArgsForm(parsedArgs, fallbackScript, '', 'formula_args');
        }
      }
    }

    /**
     * 在面板顶部注入当前条件/周期/触发间隔摘要，减少用户记忆成本。
     * 支持 flow_edge / condition_filter / tdx_condition 三种布局。
     */
    _injectConditionSummary() {
      var panelEl = this.container.querySelector('.td-panel');
      if (!panelEl) return;
      var layoutId = panelEl.getAttribute('data-layout');
      if (['flow_edge', 'condition_filter', 'tdx_condition'].indexOf(layoutId) < 0) return;
      if (panelEl.querySelector('.td-condition-summary')) return;

      var summary = { html: '', fields: [] };
      if (layoutId === 'flow_edge') {
        var ct = DataBinder.get(this._data, 'condition_type') || '';
        var formula = DataBinder.get(this._data, 'formula_ref') || '';
        var interval = DataBinder.get(this._data, 'time_gate_interval');
        if (interval === undefined || interval === null) interval = DataBinder.get(this._data, 'interval_sec');
        var ttl = DataBinder.get(this._data, 'ttl_hold_seconds');
        var ttlMode = DataBinder.get(this._data, 'ttl_mode') || '';
        var src = this._data._source_label || '';
        var tgt = this._data._target_label || '';
        var ctLabel = { 'INDICATOR': '指标条件', 'INTERSECTION': '交集运算', 'RANKING': '排名条件', '': '直接转移' }[String(ct).toUpperCase()] || ct;
        summary.html = '<div class="td-summary-line"><span class="td-summary-route">' + _escHtml(src) + ' → ' + _escHtml(tgt) + '</span></div>';
        summary.html += '<div class="td-summary-badges">';
        summary.html += '<span class="td-summary-badge">条件: ' + _escHtml(ctLabel) + '</span>';
        if (formula) summary.html += '<span class="td-summary-badge">公式: ' + _escHtml(formula) + '</span>';
        summary.html += '<span class="td-summary-badge">触发间隔: ' + (interval !== undefined && interval !== '' ? _escHtml(interval) + 's' : '未设置') + '</span>';
        if (ttl !== undefined && ttl !== null && ttl !== '') summary.html += '<span class="td-summary-badge">TTL: ' + _escHtml(ttl) + 's/' + _escHtml(ttlMode || 'interval') + '</span>';
        summary.html += '</div>';
        summary.fields = ['condition_type', 'formula_ref', 'time_gate_interval', 'interval_sec', 'ttl_hold_seconds', 'ttl_mode'];
      } else if (layoutId === 'condition_filter') {
        var itype = DataBinder.get(this._data, 'inditype') || '';
        var itypeMap = { 'indi': '技术指标', '': '条件选股', 'trade_sys': '交易系统', 'basic': '基本面', 'dynamic': '动态行情', 'ref': '引用公式' };
        var decoded = (DataBinder.get(this._data, 'formula_decoded') || '').substring(0, 60);
        var iparam = DataBinder.get(this._data, 'indiparam') || '';
        summary.html = '<div class="td-summary-badges">';
        summary.html += '<span class="td-summary-badge">公式类型: ' + _escHtml(itypeMap[itype] || itype) + '</span>';
        if (iparam) summary.html += '<span class="td-summary-badge">参数: ' + _escHtml(iparam) + '</span>';
        summary.html += '</div>';
        if (decoded) summary.html += '<div class="td-summary-script">' + _escHtml(decoded) + (decoded.length >= 60 ? '…' : '') + '</div>';
        summary.fields = ['inditype', 'formula_decoded', 'indiparam'];
      } else if (layoutId === 'tdx_condition') {
        var nset = DataBinder.get(this._data, 'params.tdx_func.nset');
        var nperiod = DataBinder.get(this._data, 'params.tdx_func.nperiod');
        var noperate = DataBinder.get(this._data, 'params.tdx_func.noperate');
        var nsetMap = { 0: '技术指标', 1: '条件选股', 2: '专家系统', 3: '最新财务', 4: '实时行情', 5: '逻辑运算' };
        var periodMap = { 0: '分笔', 1: '1分钟', 2: '5分钟', 3: '15分钟', 4: '日线', 5: '周线', 6: '月线' };
        var operateMap = { 0: '等于', 1: '大于', 2: '小于', 3: '上穿(金叉)', 4: '下破(死叉)', 5: '持股', 6: '排名前N', 7: '排名后N', 8: '上拐', 9: '下拐' };
        summary.html = '<div class="td-summary-badges">';
        summary.html += '<span class="td-summary-badge">' + _escHtml(nsetMap[nset] || ('nset=' + nset)) + '</span>';
        summary.html += '<span class="td-summary-badge">周期: ' + _escHtml(periodMap[nperiod] || nperiod) + '</span>';
        summary.html += '<span class="td-summary-badge">操作: ' + _escHtml(operateMap[noperate] || noperate) + '</span>';
        summary.html += '</div>';
        summary.fields = ['params.tdx_func.nset', 'params.tdx_func.nperiod', 'params.tdx_func.noperate'];
      }

      if (!summary.html) return;
      var box = document.createElement('div');
      box.className = 'td-condition-summary';
      box.innerHTML = summary.html;
      var header = panelEl.querySelector('.td-panel-header');
      if (header && header.nextSibling) {
        panelEl.insertBefore(box, header.nextSibling);
      } else {
        panelEl.appendChild(box);
      }

      // 字段变化时即时更新摘要文本
      var self = this;
      function updateSummary() {
        var newHtml = summary.html;
        if (layoutId === 'flow_edge') {
          var ct2 = DataBinder.get(self._data, 'condition_type') || '';
          var formula2 = DataBinder.get(self._data, 'formula_ref') || '';
          var interval2 = DataBinder.get(self._data, 'time_gate_interval');
          if (interval2 === undefined || interval2 === null) interval2 = DataBinder.get(self._data, 'interval_sec');
          var ttl2 = DataBinder.get(self._data, 'ttl_hold_seconds');
          var ttlMode2 = DataBinder.get(self._data, 'ttl_mode') || '';
          var ctLabel2 = { 'INDICATOR': '指标条件', 'INTERSECTION': '交集运算', 'RANKING': '排名条件', '': '直接转移' }[String(ct2).toUpperCase()] || ct2;
          var html2 = '<div class="td-summary-line"><span class="td-summary-route">' + _escHtml(self._data._source_label || '') + ' → ' + _escHtml(self._data._target_label || '') + '</span></div>';
          html2 += '<div class="td-summary-badges">';
          html2 += '<span class="td-summary-badge">条件: ' + _escHtml(ctLabel2) + '</span>';
          if (formula2) html2 += '<span class="td-summary-badge">公式: ' + _escHtml(formula2) + '</span>';
          html2 += '<span class="td-summary-badge">触发间隔: ' + (interval2 !== undefined && interval2 !== '' ? _escHtml(interval2) + 's' : '未设置') + '</span>';
          if (ttl2 !== undefined && ttl2 !== null && ttl2 !== '') html2 += '<span class="td-summary-badge">TTL: ' + _escHtml(ttl2) + 's/' + _escHtml(ttlMode2 || 'interval') + '</span>';
          html2 += '</div>';
          box.innerHTML = html2;
        } else if (layoutId === 'condition_filter') {
          var itype2 = DataBinder.get(self._data, 'inditype') || '';
          var itypeMap2 = { 'indi': '技术指标', '': '条件选股', 'trade_sys': '交易系统', 'basic': '基本面', 'dynamic': '动态行情', 'ref': '引用公式' };
          var decoded2 = (DataBinder.get(self._data, 'formula_decoded') || '').substring(0, 60);
          var iparam2 = DataBinder.get(self._data, 'indiparam') || '';
          var html2 = '<div class="td-summary-badges">';
          html2 += '<span class="td-summary-badge">公式类型: ' + _escHtml(itypeMap2[itype2] || itype2) + '</span>';
          if (iparam2) html2 += '<span class="td-summary-badge">参数: ' + _escHtml(iparam2) + '</span>';
          html2 += '</div>';
          if (decoded2) html2 += '<div class="td-summary-script">' + _escHtml(decoded2) + (decoded2.length >= 60 ? '…' : '') + '</div>';
          box.innerHTML = html2;
        } else if (layoutId === 'tdx_condition') {
          var nset2 = DataBinder.get(self._data, 'params.tdx_func.nset');
          var nperiod2 = DataBinder.get(self._data, 'params.tdx_func.nperiod');
          var noperate2 = DataBinder.get(self._data, 'params.tdx_func.noperate');
          var nsetMap2 = { 0: '技术指标', 1: '条件选股', 2: '专家系统', 3: '最新财务', 4: '实时行情', 5: '逻辑运算' };
          var periodMap2 = { 0: '分笔', 1: '1分钟', 2: '5分钟', 3: '15分钟', 4: '日线', 5: '周线', 6: '月线' };
          var operateMap2 = { 0: '等于', 1: '大于', 2: '小于', 3: '上穿(金叉)', 4: '下破(死叉)', 5: '持股', 6: '排名前N', 7: '排名后N', 8: '上拐', 9: '下拐' };
          var html2 = '<div class="td-summary-badges">';
          html2 += '<span class="td-summary-badge">' + _escHtml(nsetMap2[nset2] || ('nset=' + nset2)) + '</span>';
          html2 += '<span class="td-summary-badge">周期: ' + _escHtml(periodMap2[nperiod2] || nperiod2) + '</span>';
          html2 += '<span class="td-summary-badge">操作: ' + _escHtml(operateMap2[noperate2] || noperate2) + '</span>';
          html2 += '</div>';
          box.innerHTML = html2;
        }
      }

      var container = this.container;
      summary.fields.forEach(function (path) {
        var key = self._pathToKey(path);
        var selector = key ? '.td-field[data-key="' + key + '"] input, .td-field[data-key="' + key + '"] select, .td-field[data-key="' + key + '"] textarea' : '';
        if (!selector) selector = '.td-field[data-path="' + path + '"] input, .td-field[data-path="' + path + '"] select, .td-field[data-path="' + path + '"] textarea';
        container.querySelectorAll(selector).forEach(function (el) {
          el.addEventListener('change', updateSummary);
          el.addEventListener('input', updateSummary);
        });
      });
    }

    /**
     * 解析 DZH indiparam 字符串为位置参数字典。
     * indiparam 格式如 '(1,25,100,30,15,30)'，按位置映射为 {P1:1, P2:25, P3:100, ...}。
     * 与后端 _parse_indiparam（native/builtins.py）逻辑保持一致。
     */
    _parseIndiparam(indiparamStr) {
      if (!indiparamStr || typeof indiparamStr !== 'string') return null;
      var s = indiparamStr.trim();
      if (s.charAt(0) === '(' && s.charAt(s.length - 1) === ')') {
        s = s.substring(1, s.length - 1);
      }
      var parts = s.split(',').map(function (p) { return p.trim(); }).filter(function (p) { return p.length > 0; });
      if (parts.length === 0) return null;
      var args = {};
      for (var i = 0; i < parts.length; i++) {
        var num = parseFloat(parts[i]);
        if (!isNaN(num)) {
          args['P' + (i + 1)] = num;
        }
      }
      return Object.keys(args).length > 0 ? args : null;
    }

    /**
     * 打开 DZH 公式选择器，选中后回填 textarea。
     * 根据当前 inditype 推断 nset 以限定公式分类：
     *   indi       → nset=0（技术指标）
     *   trade_sys  → nset=2（交易系统）
     *   其他       → nset=null（显示全部）
     */
    _openDzhFormulaSelector() {
      var self = this;
      if (!window.ComprehensiveSettings || !window.ComprehensiveSettings.utils ||
          typeof window.ComprehensiveSettings.utils.openFormulaSelector !== 'function') {
        this._showToast && this._showToast('公式选择器不可用', 'error');
        return;
      }

      var curInditype = DataBinder.get(this._data, 'inditype');
      var nset = null;
      if (curInditype === 'indi') {
        nset = 0;
      } else if (curInditype === 'trade_sys') {
        nset = 2;
      }

      window.ComprehensiveSettings.utils.openFormulaSelector(nset, function (formula) {
        self._onDzhFormulaSelected(formula);
      });
    }

    /**
     * DZH 公式选中回调：回填 textarea 并触发 input 事件以同步 Base64 编码。
     */
    _onDzhFormulaSelected(formula) {
      var formulaField = this.container.querySelector('.td-field-formula');
      if (!formulaField) return;
      var textarea = formulaField.querySelector('.td-formula-textarea');
      if (!textarea) return;

      textarea.value = formula.script || '';

      // 触发 input 事件以复用既有编码回写逻辑（明文 → data_path + Base64 → encode_to）
      var inputEvt;
      try {
        inputEvt = new Event('input', { bubbles: true });
      } catch (e) {
        inputEvt = document.createEvent('Event');
        inputEvt.initEvent('input', true, false);
      }
      textarea.dispatchEvent(inputEvt);

      // 根据 formula.category 智能设置 inditype：
      //   indicator / xg → "indi"（条件选股在 DZH 中归为指标条件）
      //   exp            → "trade_sys"（交易系统）
      var newInditype = 'indi';
      if ((formula.category || '') === 'exp') {
        newInditype = 'trade_sys';
      }
      DataBinder.set(this._data, 'inditype', newInditype);
      this._notifyChange('inditype', newInditype);
      // 兼容 data-key 和 data-path 两种写法
      var inditypeSelect = this.container.querySelector('.td-field[data-key="inditype"] select, .td-field[data-path="inditype"] select');
      if (inditypeSelect) inditypeSelect.value = newInditype;

      // 自动设置 attr_int 的 bit 20（indicator_condition, 0x100000）
      // 运行时 transfer_condition_check 检查此位为 True 才执行公式
      var currentAttr = DataBinder.get(this._data, 'attr_int') || 0;
      if (typeof currentAttr === 'string') currentAttr = parseInt(currentAttr) || 0;
      currentAttr = currentAttr | 0x100000;  // 设置 bit 20
      DataBinder.set(this._data, 'attr_int', currentAttr);
      this._notifyChange('attr_int', currentAttr);
      var indicatorFlagCb = this.container.querySelector('.td-flag-cb[data-flag-name="indicator_condition"]');
      if (indicatorFlagCb) indicatorFlagCb.checked = true;

      // 构造 formula_args 对象 {name: value} 并渲染参数表单
      // DZH 参数存储在 _data 顶层 formula_args（对应后端 node.params.formula_args），
      // 区别于 TDX 的 params.tdx_func.formula_args

      // 清空 indiparam（导入节点可能携带位置参数，避免与新 formula_args 并存）
      var existingIndiparam = DataBinder.get(this._data, 'indiparam');
      if (existingIndiparam) {
        DataBinder.set(this._data, 'indiparam', '');
        this._notifyChange('indiparam', '');
        var indiparamInput = this.container.querySelector('.td-field[data-key="indiparam"] input, .td-field[data-path="indiparam"] input');
        if (indiparamInput) indiparamInput.value = '';
      }

      var argsObj = {};
      var argsArr = formula.args || [];
      for (var i = 0; i < argsArr.length; i++) {
        var a = argsArr[i];
        var aName = a.name || ('param' + i);
        argsObj[aName] = (a.value !== undefined ? a.value : '');
      }
      DataBinder.set(this._data, 'formula_args', argsObj);
      this._notifyChange('formula_args', argsObj);

      this._renderFormulaArgsForm(argsObj, formula.script || '', formula.name || '', 'formula_args');
    }

    /**
     * 渲染公式参数表单（方案 A：参数值存储在 formula_args 对象中）。
     * TDX 与 DZH 共用此方法：TDX 传 dataPath='params.tdx_func.formula_args'，
     * DZH 传 dataPath='formula_args'（DZH _data 为 params 字段平铺，顶层即为 node.params.*）。
     * @param {Object} argsObj - {SHORT: 12, LONG: 26, MID: 9}
     * @param {String} script - 公式脚本（仅用于显示提示）
     * @param {String} formulaName - 公式名称（仅用于显示提示）
     * @param {String} [dataPath] - 参数对象在 _data 中的存储路径，默认 'params.tdx_func.formula_args'
     */
    _renderFormulaArgsForm(argsObj, script, formulaName, dataPath) {
      dataPath = dataPath || 'params.tdx_func.formula_args';
      var argsBox = this.container.querySelector('.td-formula-args-box');
      if (!argsBox) return;

      var listEl = argsBox.querySelector('.td-formula-args-list');
      if (!listEl) return;

      var html = '';
      var keys = Object.keys(argsObj);
      if (keys.length === 0) {
        html = '<div style="font-size:11px;color:var(--text-secondary);">该公式无可配置参数</div>';
      } else {
        for (var i = 0; i < keys.length; i++) {
          var pName = keys[i];
          var pVal = argsObj[pName];
          html += '<div class="td-formula-arg-row" style="display:flex;align-items:center;gap:6px;margin-bottom:4px;" data-arg-name="' + _escAttr(pName) + '">';
          html += '<label style="font-size:11px;min-width:60px;color:var(--text-primary);">' + _escHtml(pName) + '</label>';
          html += '<input type="text" class="td-formula-arg-input" data-arg-name="' + _escAttr(pName) + '" value="' + _escAttr(String(pVal)) + '" style="font-size:11px;width:80px;padding:2px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">';
          html += '</div>';
        }
      }
      listEl.innerHTML = html;
      argsBox.style.display = '';

      // 绑定参数输入事件（回写到 dataPath 指定的存储路径）
      var self = this;
      var inputs = listEl.querySelectorAll('.td-formula-arg-input');
      for (var j = 0; j < inputs.length; j++) {
        inputs[j].addEventListener('input', function (e) {
          var argName = e.target.getAttribute('data-arg-name');
          var argVal = e.target.value;
          // 数字字符串尝试转为数字
          var numVal = parseFloat(argVal);
          if (!isNaN(numVal) && String(numVal) === argVal.trim()) {
            argVal = numVal;
          }
          var currentArgs = DataBinder.get(self._data, dataPath) || {};
          currentArgs[argName] = argVal;
          DataBinder.set(self._data, dataPath, currentArgs);
          self._notifyChange(dataPath, currentArgs);
          // 当 dataPath='formula_args' 且参数为 P1/P2/... 位置参数时，同步回写 indiparam 字符串
          // 这样导入的 DZH 节点编辑参数后，indiparam (v1,v2,...) 与 formula_args dict 保持一致
          if (dataPath === 'formula_args') {
            var pKeys = Object.keys(currentArgs).filter(function (k) { return /^P\d+$/.test(k); });
            if (pKeys.length > 0) {
              pKeys.sort(function (a, b) { return parseInt(a.substring(1), 10) - parseInt(b.substring(1), 10); });
              var ipStr = '(' + pKeys.map(function (k) { return currentArgs[k]; }).join(',') + ')';
              DataBinder.set(self._data, 'indiparam', ipStr);
              self._notifyChange('indiparam', ipStr);
            }
          }
        });
      }
    }

    /**
     * 根据 nset 值控制 TDX"从公式库选择"按钮的可见性。
     * nset=0/1/2 显示；nset=3/4/5 隐藏。
     */
    _updateTdxFormulaPickerVisibility(nsetVal) {
      var picker = this.container.querySelector('.td-formula-picker-tdx');
      if (!picker) return;
      // nsetVal 为 undefined/null 时默认为 0（技术指标公式）
      if (nsetVal === undefined || nsetVal === null || nsetVal === '') {
        nsetVal = 0;
      }
      var nsetNum = parseInt(nsetVal);
      if (isNaN(nsetNum)) nsetNum = 0;
      var show = (nsetNum === 0 || nsetNum === 1 || nsetNum === 2);
      picker.style.display = show ? '' : 'none';
      // 隐藏按钮时同时隐藏参数表单
      if (!show) {
        var argsBox = this.container.querySelector('.td-formula-args-box');
        if (argsBox) argsBox.style.display = 'none';
      }
    }

    _resolveFlowModeDisplay(attr) {
      // 从后端获取mode_colors或使用默认
      var modeColors = this._modeColors || {
        "move": "#1890ff", "overwrite": "#fa8c16", "copy": "#52c41a",
        "force_move": "#f5222d", "output_components": "#722ed1", "pass_through": "#8c8c8c"
      };
      var modeLabels = {
        "move": "移动", "overwrite": "覆盖", "copy": "复制",
        "force_move": "强制移动", "output_components": "输出成份", "pass_through": "直通"
      };
      var mode = DataBinder.resolveFlowMode(attr);
      if (mode === "unknown") return null;
      return { mode: mode, label: modeLabels[mode] || mode, color: modeColors[mode] || "#8c8c8c" };
    }

    _bindEvents() {
      var self = this;
      var panel = this.container.querySelector('.td-panel');
      if (!panel) return;

      // 事件委托
      panel.addEventListener('change', function (e) {
        var target = e.target;
        self._handleChange(target);
      });

      panel.addEventListener('input', function (e) {
        var target = e.target;
        if (target.tagName === 'INPUT' && target.type === 'text') {
          self._handleChange(target);
        } else if (target.tagName === 'TEXTAREA' && target.classList.contains('td-textarea')) {
          self._handleChange(target);
        }
      });

      // formula textarea 输入事件：明文回写 data_path + 后端编码回写 encode_to
      panel.addEventListener('input', function (e) {
        if (e.target.classList.contains('td-formula-textarea')) {
          var decoded = e.target.value;
          var formulaField = e.target.closest('.td-field-formula');
          if (formulaField) {
            // 回写明文到 data_path（formula_decoded）
            var path = formulaField.getAttribute('data-path');
            DataBinder.set(self._data, path, decoded);
            self._notifyChange(path, decoded);
            // 后端编码回写到 encode_to（indi）：防抖调用 /api/formula/encode（支持 GBK + ency 加密）
            var encodeTo = formulaField.getAttribute('data-encode-to');
            if (encodeTo) {
              if (self._encodeTimer) { clearTimeout(self._encodeTimer); }
              self._encodeTimer = setTimeout(function () {
                self._encodeFormulaViaBackend(decoded, encodeTo);
              }, 400);
            }
          }
        }
      });

      // TDX枚举选择器：切换选项时动态更新描述提示
      panel.addEventListener('change', function (e) {
        if (e.target.classList.contains('td-enum-select')) {
          var enumField = e.target.closest('.td-field-tdx-enum');
          if (!enumField) return;
          var descContainer = enumField.querySelector('.td-enum-desc');
          if (!descContainer) return;
          // 从选中option的title属性获取描述
          var selectedOpt = e.target.options[e.target.selectedIndex];
          var descText = selectedOpt ? (selectedOpt.getAttribute('title') || selectedOpt.textContent || '') : '';
          descContainer.innerHTML = descText
            ? '<span class="td-enum-desc-text">' + _escHtml(descText) + '</span>'
            : '';
        }
      });

      // formula 验证按钮 → 调用后端 /api/formula/validate 做语法检查
      panel.addEventListener('click', function (e) {
        if (e.target.classList.contains('td-formula-validate')) {
          var formulaField = e.target.closest('.td-field-formula');
          if (formulaField) {
            var textarea = formulaField.querySelector('.td-formula-textarea');
            var formulaText = textarea ? textarea.value : '';
            if (!formulaText.trim()) {
              self._showToast('公式内容为空', 'error');
              return;
            }
            self._validateFormula(formulaField, formulaText);
          }
        }
      });

      // section折叠
      panel.addEventListener('click', function (e) {
        if (e.target.classList.contains('td-section-toggle')) {
          var body = e.target.closest('.td-section').querySelector('.td-section-body');
          body.classList.toggle('td-collapsed');
          e.target.textContent = body.classList.contains('td-collapsed') ? '▶' : '▼';
        }
      });

      // K线图组件初始化
      var klineContainers = panel.querySelectorAll('.td-kline-chart-container');
      for (var k = 0; k < klineContainers.length; k++) {
        var klineEl = klineContainers[k];
        var fieldKey = klineEl.getAttribute('data-key');
        var fieldConfig = self._getFieldConfig(fieldKey);
        var defaultProps = (fieldConfig && fieldConfig.default_props) || {};
        if (typeof KlineChart === 'function') {
          var chart = new KlineChart(klineEl, { default_props: defaultProps });
          var stockCode = DataBinder.get(self._data, fieldConfig ? fieldConfig.data_path : null);
          if (stockCode) {
            chart.loadData(stockCode, defaultProps.period || 'day');
          }
        }
      }

      // 指标走势图组件初始化
      var indicatorContainers = panel.querySelectorAll('.td-indicator-chart-container');
      for (var ic = 0; ic < indicatorContainers.length; ic++) {
        var indicatorEl = indicatorContainers[ic];
        var indicatorFieldKey = indicatorEl.getAttribute('data-key');
        var indicatorFieldConfig = self._getFieldConfig(indicatorFieldKey);
        var indicatorDefaultProps = (indicatorFieldConfig && indicatorFieldConfig.default_props) || {};
        if (typeof IndicatorChart === 'function') {
          var indicatorChart = new IndicatorChart(indicatorEl, { default_props: indicatorDefaultProps });
          var nodeId = DataBinder.get(self._data, indicatorFieldConfig ? indicatorFieldConfig.data_path : null);
          if (nodeId) {
            indicatorChart.loadData(nodeId, '');
          }
        }
      }

      // 虚拟滚动事件绑定（股票列表 >100 条）
      var virtualScrollContainers = panel.querySelectorAll('.td-stock-list-container[data-virtual-scroll="1"]');
      for (var vs = 0; vs < virtualScrollContainers.length; vs++) {
        (function (container) {
          var rowHeight = parseInt(container.getAttribute('data-row-height'), 10) || 32;
          var visibleRows = parseInt(container.getAttribute('data-visible-rows'), 10) || 20;
          var totalCount = parseInt(container.getAttribute('data-total-count'), 10) || 0;
          var overscan = 3;
          var dataScript = container.querySelector('.td-stock-list-data');
          var allStocks = [];
          if (dataScript) {
            try { allStocks = JSON.parse(dataScript.textContent); } catch (e) { allStocks = []; }
          }
          var columns = ['code', 'name', 'now', 'rise', 'volume', 'inprice', 'indate', 'maxrate', 'idaynum'];
          var colLabels = { 'code': '代码', 'name': '名称', 'now': '现价', 'rise': '涨幅', 'volume': '成交量', 'inprice': '入池价', 'indate': '入池日期', 'maxrate': '最大收益率', 'idaynum': '持仓天数' };
          var tbody = container.querySelector('tbody');
          var lastRenderedStart = 0;
          var lastRenderedEnd = 0;

          function renderVisibleRows(scrollTop) {
            var startIdx = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
            var endIdx = Math.min(totalCount, startIdx + visibleRows + overscan * 2);
            if (startIdx === lastRenderedStart && endIdx === lastRenderedEnd) return;
            lastRenderedStart = startIdx;
            lastRenderedEnd = endIdx;

            var html = '';
            for (var i = startIdx; i < endIdx; i++) {
              var stk = allStocks[i];
              if (!stk) continue;
              html += '<tr style="position:absolute;top:' + (i * rowHeight) + 'px;height:' + rowHeight + 'px;display:flex;width:100%;" data-row-idx="' + i + '">';
              for (var c = 0; c < columns.length; c++) {
                var colKey = columns[c];
                var cellVal = stk[colKey] != null ? stk[colKey] : '-';
                var cellStyle = '';
                if (colKey === 'rise' || colKey === 'maxrate') {
                  var numVal = parseFloat(cellVal);
                  if (!isNaN(numVal)) {
                    cellStyle = numVal > 0 ? 'color:#ff4444' : (numVal < 0 ? 'color:#00cc66' : '');
                  }
                }
                html += '<td style="flex:1;padding:2px 5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;' + cellStyle + '">' + _escHtml(String(cellVal)) + '</td>';
              }
              html += '</tr>';
            }
            tbody.innerHTML = html;
          }

          container.addEventListener('scroll', function () {
            renderVisibleRows(container.scrollTop);
          });

          // 初始渲染
          renderVisibleRows(0);
        })(virtualScrollContainers[vs]);
      }

      // ── bsavehis 保存历史数据 事件绑定 ──
      self._bindBsavehisEvents(panel);

      // ── 板块树 (sector_tree) 事件绑定 ──
      self._bindCandidatePoolEvents(panel);
    }

    // ── bsavehis 事件处理 ──
    _bindBsavehisEvents(panel) {
      var self = this;

      // 获取 pool_name 的辅助方法
      function _getPoolName() {
        return (self.poolData && self.poolData._poolId)
          || (self.poolData && self.poolData.data && self.poolData.data.pool_meta && self.poolData.data.pool_meta.name)
          || '';
      }

      // checkbox 切换 → 显示/隐藏控制区
      panel.addEventListener('change', function (e) {
        if (e.target.classList.contains('td-bsavehis-cb')) {
          var fieldEl = e.target.closest('.td-field-bsavehis');
          if (!fieldEl) return;
          var controls = fieldEl.querySelector('.td-bsavehis-controls');
          var isChecked = e.target.checked;
          if (controls) {
            controls.classList.toggle('td-bsavehis-hidden', !isChecked);
          }
          // 如果选中，加载可用日期列表
          if (isChecked) {
            var dateInput = fieldEl.querySelector('.td-bsavehis-date');
            if (dateInput && !dateInput.getAttribute('data-dates-loaded')) {
              _loadDates(fieldEl, dateInput);
            }
          }
        }
      });

      // 加载可用日期列表
      function _loadDates(fieldEl, dateInput) {
        var poolName = _getPoolName();
        var nodeId = self._currentNodeId;
        if (!poolName || !nodeId) return;
        var xhr = new XMLHttpRequest();
        xhr.open('GET', '/api/tdx/history/' + encodeURIComponent(poolName) + '/' + encodeURIComponent(nodeId) + '/dates');
        xhr.onload = function () {
          if (xhr.status === 200) {
            try {
              var resp = JSON.parse(xhr.responseText);
              if (resp.success && resp.dates && resp.dates.length > 0) {
                // 默认选中最新日期
                var latest = resp.dates[0];
                dateInput.value = latest.substring(0, 4) + '-' + latest.substring(4, 6) + '-' + latest.substring(6, 8);
                dateInput.setAttribute('data-dates-loaded', '1');
                // 存储日期列表供日历使用
                dateInput.setAttribute('data-available-dates', JSON.stringify(resp.dates));
              } else {
                dateInput.value = '';
                dateInput.placeholder = '无历史数据';
              }
            } catch (err) { console.error('解析日期列表失败', err); }
          }
        };
        xhr.send();
      }

      // 查看按钮 → 弹出历史数据表格
      panel.addEventListener('click', function (e) {
        if (!e.target.classList.contains('td-bsavehis-btn-view')) return;
        var fieldEl = e.target.closest('.td-field-bsavehis');
        if (!fieldEl) return;
        var dateInput = fieldEl.querySelector('.td-bsavehis-date');
        var resultDiv = fieldEl.querySelector('.td-bsavehis-result');
        if (!dateInput || !resultDiv) return;

        var dateVal = dateInput.value;
        if (!dateVal) { self._showToast('请先选择日期', 'error'); return; }
        var dateStr = dateVal.replace(/-/g, '');

        resultDiv.innerHTML = '<div class="td-bsavehis-loading">加载中...</div>';
        resultDiv.style.display = 'block';

        var poolName = _getPoolName();
        var nodeId = self._currentNodeId;
        var xhr = new XMLHttpRequest();
        xhr.open('GET', '/api/tdx/history/' + encodeURIComponent(poolName) + '/' + encodeURIComponent(nodeId) + '/' + dateStr);
        xhr.onload = function () {
          if (xhr.status === 200) {
            try {
              var resp = JSON.parse(xhr.responseText);
              if (resp.success) {
                self._renderHistoryTable(resultDiv, resp.stocks || [], resp.date);
              } else {
                resultDiv.innerHTML = '<div class="td-bsavehis-error">错误: ' + _escHtml(resp.error) + '</div>';
              }
            } catch (err) {
              resultDiv.innerHTML = '<div class="td-bsavehis-error">解析失败</div>';
            }
          } else {
            resultDiv.innerHTML = '<div class="td-bsavehis-error">请求失败 (' + xhr.status + ')</div>';
          }
        };
        xhr.send();
      });

      // 导出按钮 → 下载文件
      panel.addEventListener('click', function (e) {
        if (!e.target.classList.contains('td-bsavehis-btn-export')) return;
        var fieldEl = e.target.closest('.td-field-bsavehis');
        if (!fieldEl) return;
        var dateInput = fieldEl.querySelector('.td-bsavehis-date');
        if (!dateInput) return;
        var dateVal = dateInput.value;
        if (!dateVal) { self._showToast('请先选择日期', 'error'); return; }
        var dateStr = dateVal.replace(/-/g, '');
        var poolName = _getPoolName();
        var nodeId = self._currentNodeId;
        var url = '/api/tdx/history/' + encodeURIComponent(poolName) + '/' + encodeURIComponent(nodeId) + '/' + dateStr + '/export';
        // 触发下载
        var a = document.createElement('a');
        a.href = url;
        a.download = '';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        self._showToast('导出成功', 'success');
      });

      // 入池日志链接 → 弹出完整日志查看器
      panel.addEventListener('click', function (e) {
        if (!e.target.classList.contains('td-bsavehis-link-log')) return;
        e.preventDefault();
        self._showEntryLogDialog();
      });

      // 关闭历史表格
      panel.addEventListener('click', function (e) {
        if (!e.target.classList.contains('td-bsavehis-close')) return;
        var resultDiv = e.target.closest('.td-bsavehis-result');
        if (resultDiv) resultDiv.style.display = 'none';
        // 同时关闭日志弹窗
        var logOverlay = document.getElementById('td-entry-log-overlay');
        if (logOverlay) logOverlay.style.display = 'none';
      });

      // ── 自动初始化：渲染时如果 checkbox 已勾选，自动加载日期 ──
      var checkedCbs = panel.querySelectorAll('.td-bsavehis-cb:checked');
      for (var ci = 0; ci < checkedCbs.length; ci++) {
        var cb = checkedCbs[ci];
        var fieldEl = cb.closest('.td-field-bsavehis');
        if (!fieldEl) continue;
        var controls = fieldEl.querySelector('.td-bsavehis-controls');
        if (controls) controls.classList.remove('td-bsavehis-hidden');
        var dateInput = fieldEl.querySelector('.td-bsavehis-date');
        if (dateInput && !dateInput.getAttribute('data-dates-loaded')) {
          _loadDates(fieldEl, dateInput);
        }
      }
    }

    // ── 板块树 (sector_tree) 事件处理 ──
    _bindCandidatePoolEvents(panel) {
      var self = this;

      // ── 板块树形选择器：从数据库 API 加载并渲染 ──
      var sectorTreeEl = panel.querySelector('#tdSectorTree');
      if (sectorTreeEl) {
        // 分类标签映射（attrtext 格式：BLK-{中文分类}{板块名}，如 BLK-概念锂电池）
        var SECTOR_CAT_LABELS = { concept: '概念', industry: '行业', index: '指数', style: '风格', region: '地区', custom: '自定义板块', favorite: '自选股' };
        var SECTOR_CAT_ORDER = ['concept', 'industry', 'index', 'style', 'region', 'custom', 'favorite'];

        // 数据源标签映射（Task 9: 按数据源分组显示）
        var SOURCE_LABELS = {
          tdx_local: '通达信',
          dzh_local: '大智慧',
          ths_local: '同花顺',
          dfcf: '东方财富',
          local_file: '本地文件',
          akshare: 'AKShare'
        };
        var SOURCE_TAGS = {
          tdx_local: '[TDX]',
          dzh_local: '[DZH]',
          ths_local: '[THS]',
          dfcf: '[DFCF]',
          local_file: '[LOCAL]',
          akshare: '[AK]'
        };
        var SOURCE_ORDER = ['tdx_local', 'dzh_local', 'ths_local', 'dfcf', 'local_file', 'akshare'];

        // 当前已选的板块代码（从 attrtext 中提取，格式 BLK-概念锂电池）
        function getSelectedSectors() {
          var attrtextVal = '';
          try { attrtextVal = self._data.params && self._data.params.attrtext || self._data.attrtext || ''; } catch(e) {}
          var selected = {};
          attrtextVal.split(/[\t\n;]/).forEach(function(part) {
            part = part.trim();
            if (part.indexOf('BLK-') === 0) selected[part.substring(4)] = true;
          });
          return selected;
        }

        // 缓存全部板块数据（供本地搜索过滤）
        var allSectorsCache = {};
        var searchDebounceTimer = null;

        // 从 API 响应中提取分类板块字典（兼容新旧格式，并动态包含未知分类）
        function extractSectorsFromResponse(resp) {
          var data = (resp && resp.data) ? resp.data : (resp || {});
          var result = {};
          // 新格式：data.concept, data.industry 等直接是数组
          SECTOR_CAT_ORDER.forEach(function(cat) {
            if (Array.isArray(data[cat])) result[cat] = data[cat];
          });
          // 旧格式：data.sectors.concept 等
          if (data.sectors && typeof data.sectors === 'object' && !Array.isArray(data.sectors)) {
            SECTOR_CAT_ORDER.forEach(function(cat) {
              if (!result[cat] && Array.isArray(data.sectors[cat])) result[cat] = data.sectors[cat];
            });
          }
          // 动态追加未知分类（API 返回但不在 SECTOR_CAT_ORDER 中的 key）
          function collectUnknownKeys(src) {
            if (!src || typeof src !== 'object' || Array.isArray(src)) return;
            Object.keys(src).forEach(function(key) {
              if (key === 'sectors' || key === 'success' || key === 'message' || key === 'count' || key === 'total') return;
              if (SECTOR_CAT_ORDER.indexOf(key) !== -1) return;
              if (result[key]) return;
              if (Array.isArray(src[key]) && src[key].length >= 0) result[key] = src[key];
            });
          }
          collectUnknownKeys(data);
          if (data.sectors && typeof data.sectors === 'object' && !Array.isArray(data.sectors)) {
            collectUnknownKeys(data.sectors);
          }
          return result;
        }

        // Task 9: 将按分类分组的板块重新按数据源分组
        // 输入: {concept: [{sector_name, source, ...}, ...], industry: [...]}
        // 输出: {tdx_local: {concept: [...], industry: [...]}, dzh_local: {...}, ...}
        function groupSectorsBySource(sectorsByCat) {
          var bySource = {};
          Object.keys(sectorsByCat).forEach(function(cat) {
            var sectors = sectorsByCat[cat] || [];
            sectors.forEach(function(s) {
              var src = s.source || 'local_file';
              if (!bySource[src]) bySource[src] = {};
              if (!bySource[src][cat]) bySource[src][cat] = [];
              bySource[src][cat].push(s);
            });
          });
          return bySource;
        }

        // 渲染板块树（Task 9: 按数据源分组显示）
        // targetEl: 可选，渲染目标元素（大窗口弹窗使用），默认为 sectorTreeEl
        function renderSectorTree(sectorsByCat, keyword, targetEl) {
          var root = targetEl || sectorTreeEl;
          var selectedSectors = getSelectedSectors();
          var html = '';
          var hasAny = false;

          // 按数据源分组
          var bySource = groupSectorsBySource(sectorsByCat);

          // 构建数据源顺序：已知数据源按 SOURCE_ORDER，未知追加末尾
          var orderedSources = [];
          SOURCE_ORDER.forEach(function(src) {
            if (bySource.hasOwnProperty(src)) orderedSources.push(src);
          });
          Object.keys(bySource).forEach(function(src) {
            if (SOURCE_ORDER.indexOf(src) === -1) orderedSources.push(src);
          });

          orderedSources.forEach(function(src) {
            var catsOfSource = bySource[src] || {};
            // 统计该数据源下板块总数
            var sourceTotal = 0;
            Object.keys(catsOfSource).forEach(function(c) {
              sourceTotal += (catsOfSource[c] || []).length;
            });
            if (sourceTotal === 0) return;
            hasAny = true;

            var sourceLabel = SOURCE_LABELS[src] || src;
            var sourceTag = SOURCE_TAGS[src] || '[' + src.toUpperCase() + ']';
            // 是否可按数据源同步（tdx_local/dzh_local/ths_local/dfcf 支持）
            var canSyncBySource = (src === 'tdx_local' || src === 'dzh_local' || src === 'ths_local' || src === 'dfcf');

            // 数据源分组顶层节点
            html += '<div class="td-sector-source" data-source="' + _escAttr(src) + '" style="margin-bottom:4px;border-left:2px solid var(--border-color);padding-left:6px;">';
            html += '<div class="td-sector-source-header" data-collapsed="0" style="display:flex;align-items:center;gap:4px;padding:4px 6px;cursor:pointer;font-size:12px;font-weight:700;border-radius:3px;background:var(--bg-tertiary);">';
            html += '<span class="td-sector-source-arrow" style="display:inline-block;transition:transform 0.2s;transform:rotate(90deg);">▼</span>';
            html += '<span style="color:var(--text-primary);">' + _escHtml(sourceLabel) + '</span>';
            html += '<span style="font-size:10px;color:var(--text-secondary);font-weight:normal;">(' + sourceTotal + ')</span>';
            // 数据源独立同步按钮（Task 9.3）
            if (canSyncBySource) {
              html += '<button type="button" class="td-btn td-btn-sm td-btn-source-sync" data-source="' + _escAttr(src) + '" title="仅同步' + _escAttr(sourceLabel) + '数据源" style="margin-left:auto;padding:1px 6px;font-size:10px;border:1px solid var(--border-color);border-radius:2px;background:var(--bg-secondary);color:var(--text-primary);cursor:pointer;">🔄</button>';
            }
            html += '</div>';
            html += '<div class="td-sector-source-body" style="padding-left:8px;">';

            // 该数据源下按分类分组
            var orderedCats = [];
            SECTOR_CAT_ORDER.forEach(function(cat) {
              if (catsOfSource.hasOwnProperty(cat)) orderedCats.push(cat);
            });
            Object.keys(catsOfSource).forEach(function(cat) {
              if (SECTOR_CAT_ORDER.indexOf(cat) === -1) orderedCats.push(cat);
            });

            orderedCats.forEach(function(cat) {
              var sectors = catsOfSource[cat] || [];
              // 本地关键词过滤
              if (keyword) {
                var kw = keyword.toLowerCase();
                sectors = sectors.filter(function(s) {
                  var name = (s.sector_name || s.name || '').toLowerCase();
                  return name.indexOf(kw) !== -1;
                });
              }
              if (sectors.length === 0) return;

              var catLabel = SECTOR_CAT_LABELS[cat] || cat;
              html += '<div class="td-sector-cat" style="margin-bottom:2px;">';
              html += '<div class="td-sector-cat-header" data-collapsed="0" style="display:flex;align-items:center;gap:4px;padding:3px 4px;cursor:pointer;font-size:11px;font-weight:600;border-radius:3px;">';
              html += '<span class="td-sector-arrow" style="display:inline-block;transition:transform 0.2s;transform:rotate(90deg);">▼</span>';
              html += '<span>' + _escHtml(catLabel) + '</span>';
              html += '<span style="font-size:10px;color:var(--text-secondary);font-weight:normal;">(' + sectors.length + ')</span>';
              html += '</div>';
              html += '<div class="td-sector-cat-body" style="padding-left:16px;">';
              for (var j = 0; j < sectors.length; j++) {
                var s = sectors[j];
                var sectorId = s.sector_id || s.id || '';
                var sectorName = s.sector_name || s.name || '';
                var memberCount = s.member_count;
                var blkCode = 'BLK-' + catLabel + sectorName;
                var isSel = !!selectedSectors[blkCode.substring(4)];
                html += '<div class="td-sector-item-wrap">';
                html += '<div class="td-sector-item' + (isSel ? ' td-sector-item-selected' : '') + '" data-sector-id="' + _escAttr(sectorId) + '" data-sector-name="' + _escAttr(sectorName) + '" data-category="' + _escAttr(cat) + '" data-source="' + _escAttr(src) + '" data-blk-code="' + _escAttr(blkCode) + '" style="display:flex;align-items:center;gap:4px;padding:2px 4px;font-size:11px;border-radius:2px;">';
                html += '<span class="td-sector-expand" data-expanded="0" style="cursor:pointer;width:14px;text-align:center;color:var(--text-secondary);user-select:none;">▶</span>';
                html += '<span class="td-sector-check" style="width:14px;text-align:center;color:' + (isSel ? '#27ae60' : 'var(--text-secondary)') + ';cursor:pointer;">' + (isSel ? '✓' : '□') + '</span>';
                // Task 9.4: 板块节点显示来源标识
                html += '<span style="font-size:9px;color:var(--text-secondary);font-weight:bold;min-width:36px;">' + _escHtml(sourceTag) + '</span>';
                html += '<span class="td-sector-name" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer;">' + _escHtml(sectorName) + '</span>';
                if (memberCount != null) {
                  html += '<span style="font-size:9px;color:var(--text-secondary);white-space:nowrap;">' + memberCount + '</span>';
                }
                html += '</div>';
                html += '<div class="td-sector-members" style="display:none;padding-left:24px;"></div>';
                html += '</div>';
              }
              html += '</div>';
              html += '</div>';
            });

            html += '</div>'; // td-sector-source-body
            html += '</div>'; // td-sector-source
          });

          if (!hasAny) {
            html = '<div style="font-size:11px;color:var(--text-secondary);padding:4px 0;">' + (keyword ? '无匹配板块' : '无可用板块') + '</div>';
          }
          root.innerHTML = html;
          bindSectorTreeEvents(root);
        }

        // 绑定板块树事件
        // targetEl: 可选，事件绑定目标元素（大窗口弹窗使用），默认为 sectorTreeEl
        function bindSectorTreeEvents(targetEl) {
          var root = targetEl || sectorTreeEl;

          // 数据源分组折叠/展开（Task 9.1）
          root.querySelectorAll('.td-sector-source-header').forEach(function(hdr) {
            hdr.addEventListener('click', function(e) {
              // 点击同步按钮时不触发展开/折叠
              if (e.target && e.target.classList && e.target.classList.contains('td-btn-source-sync')) return;
              var body = hdr.nextElementSibling;
              var arrow = hdr.querySelector('.td-sector-source-arrow');
              var collapsed = hdr.getAttribute('data-collapsed') === '1';
              if (collapsed) {
                body.style.display = '';
                arrow.style.transform = 'rotate(90deg)';
                hdr.setAttribute('data-collapsed', '0');
              } else {
                body.style.display = 'none';
                arrow.style.transform = '';
                hdr.setAttribute('data-collapsed', '1');
              }
            });
          });

          // 数据源独立同步按钮（Task 9.3）
          root.querySelectorAll('.td-btn-source-sync').forEach(function(syncBtn) {
            syncBtn.addEventListener('click', function(e) {
              e.stopPropagation();
              var src = syncBtn.getAttribute('data-source');
              if (!src) return;
              var originalText = syncBtn.textContent;
              syncBtn.disabled = true;
              syncBtn.textContent = '...';

              fetch('/api/meta/candidate-pool/sync/source/' + encodeURIComponent(src), { method: 'POST' })
                .then(function(r) { return r.json(); })
                .then(function(resp) {
                  if (resp && resp.success === false) {
                    throw new Error(resp.error || '同步失败');
                  }
                  var summary = (resp && resp.data && resp.data.summary) || {};
                  var msg = '同步完成';
                  var details = [];
                  if (summary.total_sectors) details.push('板块 ' + summary.total_sectors + ' 条');
                  if (summary.total_members) details.push('成分股 ' + summary.total_members + ' 条');
                  if (details.length) msg += '：' + details.join('，');
                  if (summary.elapsed_ms) msg += '（' + summary.elapsed_ms + 'ms）';
                  self._showToast(msg, 'success');
                  // Task 9.5: 仅刷新对应数据源的板块列表
                  refreshSourceSectors(src);
                })
                .catch(function(err) {
                  self._showToast('同步失败: ' + (err.message || err), 'error');
                })
                .finally(function() {
                  syncBtn.disabled = false;
                  syncBtn.textContent = originalText;
                });
            });
          });

          // 分类折叠/展开
          sectorTreeEl.querySelectorAll('.td-sector-cat-header').forEach(function(hdr) {
            hdr.addEventListener('click', function() {
              var body = hdr.nextElementSibling;
              var arrow = hdr.querySelector('.td-sector-arrow');
              var collapsed = hdr.getAttribute('data-collapsed') === '1';
              if (collapsed) {
                body.style.display = '';
                arrow.style.transform = 'rotate(90deg)';
                hdr.setAttribute('data-collapsed', '0');
              } else {
                body.style.display = 'none';
                arrow.style.transform = '';
                hdr.setAttribute('data-collapsed', '1');
              }
            });
          });

          // 板块项：展开图标点击 → 加载成分股
          root.querySelectorAll('.td-sector-expand').forEach(function(expandIcon) {
            expandIcon.addEventListener('click', function(e) {
              e.stopPropagation();
              var item = expandIcon.closest('.td-sector-item');
              var wrap = item.parentElement;
              var membersEl = wrap.querySelector('.td-sector-members');
              var sectorId = item.getAttribute('data-sector-id');
              var isExpanded = expandIcon.getAttribute('data-expanded') === '1';

              if (isExpanded) {
                // 折叠
                membersEl.style.display = 'none';
                expandIcon.textContent = '▶';
                expandIcon.setAttribute('data-expanded', '0');
              } else {
                // 展开
                if (membersEl.getAttribute('data-loaded') === '1') {
                  membersEl.style.display = 'block';
                  expandIcon.textContent = '▼';
                  expandIcon.setAttribute('data-expanded', '1');
                } else {
                  // 加载成分股：GET /api/meta/candidate-pool/sectors/{sector_id}/members
                  expandIcon.textContent = '...';
                  fetch('/api/meta/candidate-pool/sectors/' + encodeURIComponent(sectorId) + '/members')
                    .then(function(r) { return r.json(); })
                    .then(function(resp) {
                      var members = (resp && resp.data && resp.data.members) || (resp && resp.members) || [];
                      var mHtml = '';
                      if (members.length === 0) {
                        mHtml = '<div style="font-size:10px;color:var(--text-secondary);padding:2px 0;">无成分股</div>';
                      } else {
                        members.forEach(function(m) {
                          mHtml += '<div style="display:flex;gap:6px;padding:1px 4px;font-size:10px;color:var(--text-secondary);">';
                          mHtml += '<span>' + _escHtml(m.stock_code || m.code || '') + '</span>';
                          mHtml += '<span>' + _escHtml(m.name || '') + '</span>';
                          mHtml += '</div>';
                        });
                      }
                      membersEl.innerHTML = mHtml;
                      membersEl.style.display = 'block';
                      membersEl.setAttribute('data-loaded', '1');
                      expandIcon.textContent = '▼';
                      expandIcon.setAttribute('data-expanded', '1');
                    })
                    .catch(function() {
                      expandIcon.textContent = '▶';
                      membersEl.innerHTML = '<div style="font-size:10px;color:#e74c3c;padding:2px 0;">加载失败</div>';
                      membersEl.style.display = 'block';
                    });
                }
              }
            });
          });

          // 板块项：名称/复选框点击 → 加入/移除 attrtext（格式 BLK-{分类}{板块名}）
          root.querySelectorAll('.td-sector-item').forEach(function(item) {
            var nameSpan = item.querySelector('.td-sector-name');
            var checkSpan = item.querySelector('.td-sector-check');
            function toggleSelect() {
              var blkCode = item.getAttribute('data-blk-code');
              var label = item.getAttribute('data-sector-name');
              var wasSelected = item.classList.contains('td-sector-item-selected');

              var params = self._data.params || self._data;
              var currentAttr = params.attrtext || '';
              var entries = currentAttr ? currentAttr.split(/[\t\n]/) : [];

              if (wasSelected) {
                entries = entries.filter(function(e) { return e.trim() !== blkCode; });
                item.classList.remove('td-sector-item-selected');
                checkSpan.textContent = '□';
                checkSpan.style.color = 'var(--text-secondary)';
              } else {
                entries.push(blkCode);
                item.classList.add('td-sector-item-selected');
                checkSpan.textContent = '✓';
                checkSpan.style.color = '#27ae60';
              }
              params.attrtext = entries.join('\t');
              self._notifyChange('attrtext', params.attrtext);
              self._showToast(wasSelected ? '已移除: ' + label : '已添加: ' + label);
            }
            if (nameSpan) nameSpan.addEventListener('click', function(e) { e.stopPropagation(); toggleSelect(); });
            if (checkSpan) checkSpan.addEventListener('click', function(e) { e.stopPropagation(); toggleSelect(); });
          });
        }

        // Task 9.5: 仅刷新指定数据源的板块列表（保留其他数据源的缓存）
        function refreshSourceSectors(src) {
          var url = '/api/meta/candidate-pool/local-sectors?source=' + encodeURIComponent(src);
          fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(resp) {
              var newSectorsByCat = extractSectorsFromResponse(resp);
              // 合并到缓存：移除旧 source 的板块，加入新 source 的板块
              // 先从缓存中移除指定 source 的板块
              Object.keys(allSectorsCache).forEach(function(cat) {
                allSectorsCache[cat] = (allSectorsCache[cat] || []).filter(function(s) {
                  return s.source !== src;
                });
                if (allSectorsCache[cat].length === 0) delete allSectorsCache[cat];
              });
              // 加入新数据
              Object.keys(newSectorsByCat).forEach(function(cat) {
                if (!allSectorsCache[cat]) allSectorsCache[cat] = [];
                allSectorsCache[cat] = allSectorsCache[cat].concat(newSectorsByCat[cat]);
              });
              // 重新渲染
              renderSectorTree(allSectorsCache, '');
            })
            .catch(function() {
              // 刷新失败时静默处理（已有 toast 提示同步状态）
            });
        }

        // 加载板块数据到缓存（不在面板内显示小树，仅用于大窗口弹窗）
        function loadSectorTree(keyword) {
          var url = '/api/meta/candidate-pool/local-sectors';
          if (keyword) {
            url += '?keyword=' + encodeURIComponent(keyword);
          }
          return fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(resp) {
              var sectorsByCat = extractSectorsFromResponse(resp);
              if (!keyword) allSectorsCache = sectorsByCat;
              return sectorsByCat;
            })
            .catch(function() {
              return {};
            });
        }

        // 更新选择按钮文字（显示已选数量）
        function updateSectorButton() {
          var btnText = panel.querySelector('#tdSectorModal .td-btn-text');
          if (!btnText) return;
          var selected = getSelectedSectors();
          var count = Object.keys(selected).length;
          if (count > 0) {
            btnText.textContent = '已选板块: ' + count + ' 个';
          } else {
            btnText.textContent = '选择板块...';
          }
        }

        // 初始加载（后台获取数据到缓存，不渲染小树）
        loadSectorTree();
        updateSectorButton();

        // 更新同步按钮：POST /api/meta/candidate-pool/sync/all
        var syncBtn = panel.querySelector('#tdSectorSync');
        if (syncBtn) {
          syncBtn.addEventListener('click', function() {
            var btnText = syncBtn.querySelector('.td-btn-text');
            syncBtn.disabled = true;
            if (btnText) btnText.textContent = '同步中...';

            fetch('/api/meta/candidate-pool/sync/all', { method: 'POST' })
              .then(function(r) { return r.json(); })
              .then(function(resp) {
                // 检查后端返回的 success 字段（后端异常时返回 {success:false, error:...}）
                if (resp && resp.success === false) {
                  throw new Error(resp.error || '同步失败');
                }
                var report = (resp && resp.data && resp.data.report) || {};
                // 汇总各 provider 的同步数量（local_file + dfcf + ...）
                function sumProviderCount(phase, field) {
                  field = field || 'count';
                  var phaseReport = report[phase] || {};
                  var providers = phaseReport.providers || {};
                  var total = 0;
                  for (var p in providers) {
                    if (providers.hasOwnProperty(p) && providers[p] && providers[p].success) {
                      total += (providers[p][field] || 0);
                    }
                  }
                  return total;
                }
                var stockCount = sumProviderCount('stocks');
                var sectorCount = sumProviderCount('sectors');
                var memberCount = sumProviderCount('sector_members');
                var favCount = sumProviderCount('favorites');
                var customBlockCount = sumProviderCount('custom_blocks', 'block_count');

                // 统计失败的 provider（用于降级提示）
                var failedProviders = [];
                for (var phase in report) {
                  if (!report.hasOwnProperty(phase) || !report[phase] || !report[phase].providers) continue;
                  for (var p in report[phase].providers) {
                    if (report[phase].providers.hasOwnProperty(p)) {
                      var pr = report[phase].providers[p];
                      if (pr && pr.success === false && failedProviders.indexOf(p) === -1) {
                        failedProviders.push(p);
                      }
                    }
                  }
                }

                var msg = '同步完成';
                var details = [];
                if (stockCount) details.push('股票 ' + stockCount + ' 条');
                if (sectorCount) details.push('板块 ' + sectorCount + ' 条');
                if (memberCount) details.push('成分股 ' + memberCount + ' 条');
                if (favCount) details.push('自选股 ' + favCount + ' 条');
                if (customBlockCount) details.push('自定义板块 ' + customBlockCount + ' 个');
                if (details.length) msg += '：' + details.join('，');
                // DFCF 等数据源失败时追加降级提示
                if (failedProviders.length) {
                  msg += '（' + failedProviders.join('/') + ' 数据源同步失败）';
                }

                if (btnText) btnText.textContent = '🔄';
                syncBtn.disabled = false;
                self._showToast(msg, failedProviders.length ? 'warning' : 'success');
                // 刷新板块数据缓存
                loadSectorTree();
              })
              .catch(function(err) {
                if (btnText) btnText.textContent = '同步失败';
                self._showToast('更新同步失败: ' + (err.message || err), 'error');
                setTimeout(function() {
                  if (btnText) btnText.textContent = '🔄';
                  syncBtn.disabled = false;
                }, 2000);
              });
          });
        }

        // ── 大窗口选择板块（属性面板空间太小） ──
        var modalBtn = panel.querySelector('#tdSectorModal');
        if (modalBtn) {
          modalBtn.addEventListener('click', function() {
            openSectorModal();
          });
        }

        // 打开板块选择大窗口弹窗（股票池备选股设置 — 匹配 DZH 界面）
        // SubTask 4.1: 左侧树形选择器（60%）+ 右侧已选列表（40%）+ 底部操作按钮栏
        function openSectorModal() {
          // 移除已有弹窗
          var existing = document.getElementById('td-sector-modal-overlay');
          if (existing) existing.remove();

          // ── 已选列表数据结构（SubTask 4.7/4.8）──
          // 每项: {type: 'market'|'sector'|'favorite'|'stock', code: string, name: string, sector_id?: string}
          // code 即 attrtext 条目（如 'SH#上证A股'、'BLK-概念锂电池'、'SH600000'）
          var selectedItems = [];

          // 从现有 attrtext 初始化 selectedItems
          (function initSelected() {
            var attrtextVal = '';
            try { attrtextVal = (self._data.params && self._data.params.attrtext) || self._data.attrtext || ''; } catch(e) {}
            attrtextVal.split(/[\t\n;]/).forEach(function(part) {
              part = part.trim();
              if (!part) return;
              if (part.indexOf('BLK-') === 0) {
                var rest = part.substring(4);
                // SubTask 4.10: 自选股 code 格式 BLK-自选股1，板块 code 格式 BLK-概念锂电池
                if (rest.indexOf('自选股') === 0) {
                  selectedItems.push({type: 'favorite', code: part, name: rest});
                } else {
                  selectedItems.push({type: 'sector', code: part, name: rest});
                }
              } else if (part.indexOf('SH#') === 0 || part.indexOf('SZ#') === 0) {
                // 市场条目：SH#上证A股
                selectedItems.push({type: 'market', code: part, name: part.split('#')[1] || part});
              } else {
                // 个股条目：直接代码
                selectedItems.push({type: 'stock', code: part, name: part});
              }
            });
          })();

          // ── 选择操作工具函数 ──
          function indexOfItem(code) {
            for (var i = 0; i < selectedItems.length; i++) {
              if (selectedItems[i].code === code) return i;
            }
            return -1;
          }
          function isItemSelected(code) { return indexOfItem(code) >= 0; }

          // 同步左侧所有同 code 的 checkbox 状态
          function syncCheckboxesByCode(code, checked) {
            if (!code || !leftPane) return;
            leftPane.querySelectorAll('input[type="checkbox"]').forEach(function(cb) {
              if (cb.dataset && cb.dataset.code === code) cb.checked = checked;
            });
          }

          // 添加条目到已选列表
          function addSelection(item) {
            if (indexOfItem(item.code) < 0) {
              selectedItems.push(item);
              renderSelectedList();
              syncCheckboxesByCode(item.code, true);
              return true;
            }
            return false;
          }
          // 从已选列表移除条目（同时取消左侧勾选）
          function removeSelection(code) {
            var idx = indexOfItem(code);
            if (idx >= 0) {
              selectedItems.splice(idx, 1);
              renderSelectedList();
              syncCheckboxesByCode(code, false);
            }
          }

          // ── 创建遮罩 + 弹窗骨架（SubTask 4.1）──
          var overlay = document.createElement('div');
          overlay.id = 'td-sector-modal-overlay';
          overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;';

          var dialog = document.createElement('div');
          dialog.style.cssText = 'background:var(--bg-primary);border:1px solid var(--border-color);border-radius:8px;width:90vw;height:80vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,0.5);';

          // 标题栏
          var header = document.createElement('div');
          header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:10px 16px;border-bottom:1px solid var(--border-color);flex-shrink:0;background:var(--bg-tertiary);';
          var titleSpan = document.createElement('span');
          titleSpan.textContent = '股票池备选股设置';
          titleSpan.style.cssText = 'font-size:14px;font-weight:600;color:var(--text-primary);';
          header.appendChild(titleSpan);
          var closeBtn = document.createElement('button');
          closeBtn.type = 'button';
          closeBtn.innerHTML = '&times;';
          closeBtn.style.cssText = 'background:none;border:none;font-size:20px;color:var(--text-muted);cursor:pointer;padding:4px 8px;border-radius:4px;';
          closeBtn.title = '关闭';
          closeBtn.onmouseenter = function() { closeBtn.style.color = 'var(--text-primary)'; closeBtn.style.background = 'var(--bg-hover)'; };
          closeBtn.onmouseleave = function() { closeBtn.style.color = 'var(--text-muted)'; closeBtn.style.background = 'none'; };
          header.appendChild(closeBtn);
          dialog.appendChild(header);

          // 主体区：左侧树（60%）+ 右侧已选列表（40%）
          var body = document.createElement('div');
          body.style.cssText = 'flex:1;display:flex;overflow:hidden;';

          // 左侧树形选择器容器
          var leftPane = document.createElement('div');
          leftPane.style.cssText = 'flex:3;overflow-y:auto;padding:8px;background:var(--bg-secondary);border-right:1px solid var(--border-color);';
          body.appendChild(leftPane);

          // 右侧已选列表容器
          var rightPane = document.createElement('div');
          rightPane.style.cssText = 'flex:2;display:flex;flex-direction:column;overflow:hidden;';
          var rightHeader = document.createElement('div');
          rightHeader.style.cssText = 'padding:6px 12px;border-bottom:1px solid var(--border-color);font-size:12px;font-weight:600;color:var(--text-primary);flex-shrink:0;';
          rightHeader.textContent = '已选列表';
          rightPane.appendChild(rightHeader);
          var rightList = document.createElement('div');
          rightList.style.cssText = 'flex:1;overflow-y:auto;';
          rightPane.appendChild(rightList);
          body.appendChild(rightPane);
          dialog.appendChild(body);

          // 底部按钮栏（SubTask 4.9）
          var footer = document.createElement('div');
          footer.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 16px;border-top:1px solid var(--border-color);flex-shrink:0;background:var(--bg-tertiary);';
          var addStockBtn = document.createElement('button');
          addStockBtn.type = 'button';
          addStockBtn.className = 'td-btn td-btn-sm';
          addStockBtn.textContent = '加入个股';
          var addFileBtn = document.createElement('button');
          addFileBtn.type = 'button';
          addFileBtn.className = 'td-btn td-btn-sm';
          addFileBtn.textContent = '加入文件';
          addFileBtn.disabled = true;
          addFileBtn.title = '功能预留';
          addFileBtn.style.opacity = '0.5';
          addFileBtn.style.cursor = 'not-allowed';
          var footerSpacer = document.createElement('div');
          footerSpacer.style.cssText = 'flex:1;';
          footer.appendChild(addStockBtn);
          footer.appendChild(addFileBtn);
          footer.appendChild(footerSpacer);
          var okBtn = document.createElement('button');
          okBtn.type = 'button';
          okBtn.className = 'td-btn td-btn-primary td-btn-sm';
          okBtn.textContent = '确定';
          var cancelBtn = document.createElement('button');
          cancelBtn.type = 'button';
          cancelBtn.className = 'td-btn td-btn-sm';
          cancelBtn.textContent = '取消';
          footer.appendChild(okBtn);
          footer.appendChild(cancelBtn);
          dialog.appendChild(footer);

          overlay.appendChild(dialog);
          document.body.appendChild(overlay);

          // ── 左侧树辅助函数 ──
          // 创建根节点（可展开/折叠），返回 {root, body}
          function createRootNode(label, expanded) {
            var root = document.createElement('div');
            root.style.cssText = 'margin-bottom:4px;border-left:2px solid var(--border-color);padding-left:6px;';
            var hdr = document.createElement('div');
            hdr.style.cssText = 'display:flex;align-items:center;gap:4px;padding:4px 6px;cursor:pointer;font-size:12px;font-weight:700;border-radius:3px;background:var(--bg-tertiary);';
            var arrow = document.createElement('span');
            arrow.style.cssText = 'display:inline-block;transition:transform 0.2s;width:12px;text-align:center;';
            var labelEl = document.createElement('span');
            labelEl.textContent = label;
            labelEl.style.color = 'var(--text-primary)';
            hdr.appendChild(arrow);
            hdr.appendChild(labelEl);
            var bodyEl = document.createElement('div');
            bodyEl.style.cssText = 'padding-left:8px;';
            var isExpanded = !!expanded;
            arrow.textContent = isExpanded ? '▼' : '▶';
            bodyEl.style.display = isExpanded ? 'block' : 'none';
            hdr.addEventListener('click', function() {
              isExpanded = !isExpanded;
              bodyEl.style.display = isExpanded ? 'block' : 'none';
              arrow.textContent = isExpanded ? '▼' : '▶';
            });
            root.appendChild(hdr);
            root.appendChild(bodyEl);
            return {root: root, body: bodyEl};
          }

          // 创建带 checkbox 的叶子节点
          // opts: {label, code, type, name, sector_id?, indent?, extraText?}
          function createCheckNode(opts) {
            var node = document.createElement('div');
            node.style.cssText = 'display:flex;align-items:center;gap:4px;padding:2px 4px;font-size:11px;padding-left:' + (opts.indent || 0) + 'px;';
            // 占位箭头（与可展开节点对齐）
            var spacer = document.createElement('span');
            spacer.style.cssText = 'display:inline-block;width:14px;';
            node.appendChild(spacer);
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.style.cssText = 'margin:0;cursor:pointer;';
            if (opts.code) {
              cb.dataset.code = opts.code;
              if (isItemSelected(opts.code)) cb.checked = true;
            }
            cb.addEventListener('change', function() {
              if (cb.checked) {
                addSelection({type: opts.type, code: opts.code, name: opts.name || opts.label, sector_id: opts.sector_id});
              } else {
                removeSelection(opts.code);
              }
            });
            node.appendChild(cb);
            var labelEl = document.createElement('span');
            labelEl.textContent = opts.label;
            labelEl.style.cssText = 'cursor:pointer;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
            labelEl.addEventListener('click', function() { cb.click(); });
            node.appendChild(labelEl);
            if (opts.extraText) {
              var extra = document.createElement('span');
              extra.textContent = opts.extraText;
              extra.style.cssText = 'font-size:10px;color:var(--text-secondary);white-space:nowrap;';
              node.appendChild(extra);
            }
            return node;
          }

          // 创建可展开的 checkbox 节点（板块：含 checkbox + 展开箭头 + 成分股容器）
          // opts: {label, code, type, name, sector_id?, indent?, extraText?, onExpand(membersEl)}
          function createExpandableCheckNode(opts) {
            var wrap = document.createElement('div');
            var node = document.createElement('div');
            node.style.cssText = 'display:flex;align-items:center;gap:4px;padding:2px 4px;font-size:11px;padding-left:' + (opts.indent || 0) + 'px;';
            var arrow = document.createElement('span');
            arrow.textContent = '▶';
            arrow.style.cssText = 'cursor:pointer;width:14px;text-align:center;user-select:none;color:var(--text-secondary);';
            var arrowExpanded = false;
            var membersEl = document.createElement('div');
            membersEl.style.cssText = 'padding-left:' + ((opts.indent || 0) + 16) + 'px;display:none;';
            arrow.addEventListener('click', function(e) {
              e.stopPropagation();
              arrowExpanded = !arrowExpanded;
              if (arrowExpanded) {
                arrow.textContent = '▼';
                membersEl.style.display = 'block';
                if (opts.onExpand) opts.onExpand(membersEl);
              } else {
                arrow.textContent = '▶';
                membersEl.style.display = 'none';
              }
            });
            node.appendChild(arrow);
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.style.cssText = 'margin:0;cursor:pointer;';
            if (opts.code) {
              cb.dataset.code = opts.code;
              if (isItemSelected(opts.code)) cb.checked = true;
            }
            cb.addEventListener('change', function() {
              if (cb.checked) {
                addSelection({type: opts.type, code: opts.code, name: opts.name || opts.label, sector_id: opts.sector_id});
              } else {
                removeSelection(opts.code);
              }
            });
            node.appendChild(cb);
            var labelEl = document.createElement('span');
            labelEl.textContent = opts.label;
            labelEl.style.cssText = 'cursor:pointer;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
            labelEl.addEventListener('click', function() { cb.click(); });
            node.appendChild(labelEl);
            if (opts.extraText) {
              var extra = document.createElement('span');
              extra.textContent = opts.extraText;
              extra.style.cssText = 'font-size:10px;color:var(--text-secondary);white-space:nowrap;';
              node.appendChild(extra);
            }
            wrap.appendChild(node);
            wrap.appendChild(membersEl);
            return wrap;
          }

          // ── SubTask 4.3: 市场根节点 ──
          // 调用 GET /api/meta/candidate-pool/markets，渲染 交易所 → 市场分类
          // 勾选时 attrtext 格式为 SH#上证A股（从 code 字段取）
          var marketsRoot = createRootNode('市场', true);
          leftPane.appendChild(marketsRoot.root);
          marketsRoot.body.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:4px;">加载中...</div>';
          fetch('/api/meta/candidate-pool/markets')
            .then(function(r) { return r.json(); })
            .then(function(resp) {
              var data = (resp && resp.data) || {};
              marketsRoot.body.innerHTML = '';
              var exchangeLabels = {SH: '上海证券交易所', SZ: '深圳证券交易所'};
              var hasAny = false;
              ['SH', 'SZ'].forEach(function(ex) {
                if (!data[ex] || !data[ex].length) return;
                hasAny = true;
                var exNode = createRootNode(exchangeLabels[ex] || ex, true);
                marketsRoot.body.appendChild(exNode.root);
                data[ex].forEach(function(m) {
                  exNode.body.appendChild(createCheckNode({
                    label: m.name, code: m.code, type: 'market', name: m.name, indent: 8
                  }));
                });
              });
              if (!hasAny) {
                marketsRoot.body.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:4px;">无市场数据</div>';
              }
            })
            .catch(function() {
              marketsRoot.body.innerHTML = '<div style="font-size:11px;color:#e74c3c;padding:4px;">加载失败</div>';
            });

          // ── SubTask 4.4 + 4.6: 板块根节点 ──
          // 按数据源分组（通达信/大智慧/同花顺/东方财富），每源下按分类，展开板块显示成分股名称
          var sectorsRoot = createRootNode('板块', true);
          leftPane.appendChild(sectorsRoot.root);
          sectorsRoot.body.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:4px;">加载中...</div>';
          (function loadSectorsRoot() {
            // 渲染单个数据源下的分类和板块到指定容器
            // srcNodeBody: 数据源根节点的 body 元素
            // src: 数据源标识（如 tdx_local）
            // catsOfSource: {分类: [板块列表]}
            function renderSourceSectors(srcNodeBody, src, catsOfSource) {
              srcNodeBody.innerHTML = '';
              var orderedCats = [];
              SECTOR_CAT_ORDER.forEach(function(cat) {
                if (cat !== 'favorite' && catsOfSource.hasOwnProperty(cat)) orderedCats.push(cat);
              });
              Object.keys(catsOfSource).forEach(function(cat) {
                if (cat === 'favorite') return;
                if (orderedCats.indexOf(cat) === -1) orderedCats.push(cat);
              });
              orderedCats.forEach(function(cat) {
                var sectors = catsOfSource[cat] || [];
                if (!sectors.length) return;
                var catLabel = SECTOR_CAT_LABELS[cat] || cat;
                var catNode = createRootNode(catLabel);
                srcNodeBody.appendChild(catNode.root);
                sectors.forEach(function(s) {
                  var sectorId = s.sector_id || s.id || '';
                  var sectorName = s.sector_name || s.name || '';
                  var memberCount = s.member_count;
                  // 保留 API 返回的 members 字段（LocalFileProvider 已解析的成分股列表）
                  var presetMembers = s.members || null;
                  // SubTask 4.10: 板块 code 格式 BLK-{分类}{名称}，如 BLK-概念锂电池
                  var blkCode = 'BLK-' + catLabel + sectorName;
                  catNode.body.appendChild(createExpandableCheckNode({
                    label: sectorName,
                    code: blkCode,
                    type: 'sector',
                    name: catLabel + sectorName,
                    sector_id: sectorId,
                    indent: 8,
                    extraText: (memberCount != null) ? '(' + memberCount + ')' : null,
                    onExpand: function(membersEl) {
                      if (membersEl.getAttribute('data-loaded') === '1') return;
                      // 渲染成分股列表的内部函数（支持字符串列表和字典列表两种格式）
                      function renderMembers(members) {
                        membersEl.innerHTML = '';
                        if (!members || !members.length) {
                          membersEl.innerHTML = '<div style="font-size:10px;color:var(--text-secondary);padding:2px;">无成分股</div>';
                          return;
                        }
                        members.forEach(function(m) {
                          var stockCode, stockName;
                          if (typeof m === 'string') {
                            // members 是字符串列表（如 ['SH600000', 'SZ000001']）
                            stockCode = m;
                            stockName = '名称待补全';
                          } else {
                            stockCode = m.stock_code || m.code || '';
                            stockName = m.name || '名称待补全';
                          }
                          if (!stockCode) return;
                          membersEl.appendChild(createCheckNode({
                            label: stockCode + ' ' + stockName,
                            code: stockCode,
                            type: 'stock',
                            name: stockName,
                            indent: 8
                          }));
                        });
                      }
                      // 优先使用 API 已返回的 members 字段（避免二次请求）
                      if (presetMembers && presetMembers.length) {
                        renderMembers(presetMembers);
                        membersEl.setAttribute('data-loaded', '1');
                        return;
                      }
                      // 降级到 API 获取
                      membersEl.innerHTML = '<div style="font-size:10px;color:var(--text-secondary);padding:2px;">加载中...</div>';
                      fetch('/api/meta/candidate-pool/sectors/' + encodeURIComponent(sectorId) + '/members')
                        .then(function(r) { return r.json(); })
                        .then(function(resp2) {
                          var members = (resp2 && resp2.data && resp2.data.members) || (resp2 && resp2.members) || [];
                          renderMembers(members);
                          membersEl.setAttribute('data-loaded', '1');
                        })
                        .catch(function() {
                          membersEl.innerHTML = '<div style="font-size:10px;color:#e74c3c;padding:2px;">加载失败</div>';
                        });
                    }
                  }));
                });
              });
            }

            // 数据源 key 映射（前端标识 → 同步 API 标识）
            var SYNC_KEY_MAP = {tdx_local: 'tdx', dzh_local: 'dzh', ths_local: 'ths'};

            fetch('/api/meta/candidate-pool/local-sectors')
              .then(function(r) { return r.json(); })
              .then(function(resp) {
                var sectorsByCat = extractSectorsFromResponse(resp);
                var bySource = groupSectorsBySource(sectorsByCat);
                sectorsRoot.body.innerHTML = '';
                // 构建数据源顺序：已知按 SOURCE_ORDER，未知追加末尾
                var orderedSources = [];
                SOURCE_ORDER.forEach(function(src) { if (bySource.hasOwnProperty(src)) orderedSources.push(src); });
                Object.keys(bySource).forEach(function(src) { if (SOURCE_ORDER.indexOf(src) === -1) orderedSources.push(src); });
                var hasAny = false;
                orderedSources.forEach(function(src) {
                  var catsOfSource = bySource[src] || {};
                  var sourceTotal = 0;
                  Object.keys(catsOfSource).forEach(function(c) { sourceTotal += (catsOfSource[c] || []).length; });
                  if (sourceTotal === 0) return;
                  hasAny = true;
                  var sourceLabel = SOURCE_LABELS[src] || src;
                  var sourceTag = SOURCE_TAGS[src] || '[' + src.toUpperCase() + ']';
                  var srcNode = createRootNode(sourceTag + ' ' + sourceLabel);
                  sectorsRoot.body.appendChild(srcNode.root);
                  // 在 header 添加分别同步按钮（分别显示存储更新）
                  var srcHdr = srcNode.root.firstChild;
                  var syncBtn = document.createElement('button');
                  syncBtn.type = 'button';
                  syncBtn.textContent = '🔄';
                  syncBtn.title = '同步' + sourceLabel + '板块数据到数据库';
                  syncBtn.style.cssText = 'background:none;border:1px solid var(--border-color);border-radius:3px;cursor:pointer;font-size:11px;padding:0 4px;line-height:1.4;color:var(--text-secondary);margin-left:auto;';
                  var syncKey = SYNC_KEY_MAP[src] || src;
                  syncBtn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    if (syncBtn.disabled) return;
                    syncBtn.disabled = true;
                    syncBtn.textContent = '⏳';
                    syncBtn.title = '正在同步' + sourceLabel + '...';
                    fetch('/api/meta/candidate-pool/sync/source/' + encodeURIComponent(syncKey), {method: 'POST'})
                      .then(function(r) { return r.json(); })
                      .then(function() {
                        // 同步完成，重新加载该数据源的板块
                        return fetch('/api/meta/candidate-pool/local-sectors?source=' + encodeURIComponent(src))
                          .then(function(r2) { return r2.json(); })
                          .then(function(resp2) {
                            var secByCat2 = extractSectorsFromResponse(resp2);
                            var bySource2 = groupSectorsBySource(secByCat2);
                            var cats2 = bySource2[src] || {};
                            renderSourceSectors(srcNode.body, src, cats2);
                            syncBtn.textContent = '✅';
                            syncBtn.title = sourceLabel + '同步完成';
                            setTimeout(function() { syncBtn.textContent = '🔄'; syncBtn.title = '同步' + sourceLabel + '板块数据到数据库'; }, 2000);
                          });
                      })
                      .catch(function() {
                        syncBtn.textContent = '❌';
                        syncBtn.title = sourceLabel + '同步失败';
                        setTimeout(function() { syncBtn.textContent = '🔄'; syncBtn.title = '同步' + sourceLabel + '板块数据到数据库'; }, 2000);
                      })
                      .finally(function() {
                        syncBtn.disabled = false;
                      });
                  });
                  srcHdr.appendChild(syncBtn);
                  // 渲染该数据源下的分类和板块
                  renderSourceSectors(srcNode.body, src, catsOfSource);
                });
                if (!hasAny) {
                  sectorsRoot.body.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:4px;">无板块数据</div>';
                }
              })
              .catch(function() {
                sectorsRoot.body.innerHTML = '<div style="font-size:11px;color:#e74c3c;padding:4px;">加载失败</div>';
              });
          })();

          // ── 板块指数根节点 ──
          // 调用 GET /api/meta/candidate-pool/sector-indices 获取各软件板块指数列表
          // 板块指数代码（880xxx）是衡量板块走势的指数代码，与板块本身一一对应
          // 勾选 code 格式 IDX-{板块指数代码}（如 IDX-880201）
          var sectorIndexRoot = createRootNode('板块指数', true);
          leftPane.appendChild(sectorIndexRoot.root);
          sectorIndexRoot.body.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:4px;">加载中...</div>';
          fetch('/api/meta/candidate-pool/sector-indices')
            .then(function(r) { return r.json(); })
            .then(function(resp) {
              var sectorsBySource = (resp && resp.data && resp.data.sectors) || {};
              sectorIndexRoot.body.innerHTML = '';
              // 板块指数子类型中文标签
              var SUB_TYPE_LABELS = {
                industry: '行业指数',
                region: '地区指数',
                concept: '概念指数',
                style: '风格指数',
                other: '其他指数'
              };
              var hasAny = false;
              // 按数据源顺序渲染
              var orderedSources = [];
              SOURCE_ORDER.forEach(function(src) {
                var srcKey = (src === 'tdx_local') ? 'tdx' :
                             (src === 'dzh_local') ? 'dzh' :
                             (src === 'ths_local') ? 'ths' : null;
                if (srcKey && sectorsBySource.hasOwnProperty(srcKey) && sectorsBySource[srcKey].length) {
                  orderedSources.push({srcKey: srcKey, srcLabel: src});
                }
              });
              orderedSources.forEach(function(item) {
                var srcKey = item.srcKey;
                var srcLabel = item.srcLabel;
                var indices = sectorsBySource[srcKey] || [];
                if (!indices.length) return;
                hasAny = true;
                var sourceLabel = SOURCE_LABELS[srcLabel] || srcLabel;
                var sourceTag = SOURCE_TAGS[srcLabel] || '[' + srcLabel.toUpperCase() + ']';
                var srcNode = createRootNode(sourceTag + ' ' + sourceLabel);
                sectorIndexRoot.body.appendChild(srcNode.root);
                // 按 sub_type 分组渲染
                var bySubType = {};
                indices.forEach(function(idx) {
                  var st = idx.sub_type || 'other';
                  if (!bySubType[st]) bySubType[st] = [];
                  bySubType[st].push(idx);
                });
                var subTypeOrder = ['industry', 'region', 'concept', 'style', 'other'];
                subTypeOrder.forEach(function(st) {
                  if (!bySubType[st]) return;
                  var stLabel = SUB_TYPE_LABELS[st] || st;
                  var stNode = createRootNode(stLabel + ' (' + bySubType[st].length + ')');
                  srcNode.body.appendChild(stNode.root);
                  bySubType[st].forEach(function(idx) {
                    var idxCode = idx.code || idx.sector_index_code || '';
                    var idxName = idx.name || '';
                    var blkCode = 'IDX-' + idxCode;
                    stNode.body.appendChild(createCheckNode({
                      label: idxName + ' (' + idxCode + ')',
                      code: blkCode,
                      type: 'sector',
                      name: '板块指数' + idxName,
                      indent: 8,
                      extraText: null
                    }));
                  });
                });
              });
              if (!hasAny) {
                sectorIndexRoot.body.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:4px;">无板块指数数据</div>';
              }
            })
            .catch(function() {
              sectorIndexRoot.body.innerHTML = '<div style="font-size:11px;color:#e74c3c;padding:4px;">加载失败</div>';
            });

          // ── 自定义板块根节点 ──
          // 调用 GET /api/meta/candidate-pool/custom-blocks?include_members=true 获取各软件自定义板块
          // 勾选 code 格式 CUS-{板块名}（如 CUS-0722JD）
          var customBlocksRoot = createRootNode('自定义板块', true);
          leftPane.appendChild(customBlocksRoot.root);
          customBlocksRoot.body.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:4px;">加载中...</div>';
          fetch('/api/meta/candidate-pool/custom-blocks?include_members=true')
            .then(function(r) { return r.json(); })
            .then(function(resp) {
              var sectorsBySource = (resp && resp.data && resp.data.sectors) || {};
              customBlocksRoot.body.innerHTML = '';
              var hasAny = false;
              var orderedSources = [];
              SOURCE_ORDER.forEach(function(src) {
                var srcKey = (src === 'tdx_local') ? 'tdx' :
                             (src === 'dzh_local') ? 'dzh' :
                             (src === 'ths_local') ? 'ths' : null;
                if (srcKey && sectorsBySource.hasOwnProperty(srcKey) && sectorsBySource[srcKey].length) {
                  orderedSources.push({srcKey: srcKey, srcLabel: src});
                }
              });
              orderedSources.forEach(function(item) {
                var srcKey = item.srcKey;
                var srcLabel = item.srcLabel;
                var blocks = sectorsBySource[srcKey] || [];
                if (!blocks.length) return;
                hasAny = true;
                var sourceLabel = SOURCE_LABELS[srcLabel] || srcLabel;
                var sourceTag = SOURCE_TAGS[srcLabel] || '[' + srcLabel.toUpperCase() + ']';
                var srcNode = createRootNode(sourceTag + ' ' + sourceLabel + ' (' + blocks.length + ')');
                customBlocksRoot.body.appendChild(srcNode.root);
                blocks.forEach(function(b) {
                  var blkName = b.name || b.code || '';
                  var blkCode = 'CUS-' + blkName;
                  var memberCount = b.member_count || 0;
                  var presetMembers = b.members || null;
                  srcNode.body.appendChild(createExpandableCheckNode({
                    label: blkName,
                    code: blkCode,
                    type: 'sector',
                    name: '自定义' + blkName,
                    indent: 8,
                    extraText: '(' + memberCount + ')',
                    onExpand: function(membersEl) {
                      if (membersEl.getAttribute('data-loaded') === '1') return;
                      function renderMembers(members) {
                        membersEl.innerHTML = '';
                        if (!members || !members.length) {
                          membersEl.innerHTML = '<div style="font-size:10px;color:var(--text-secondary);padding:2px;">无成分股</div>';
                          return;
                        }
                        members.forEach(function(m) {
                          var stockCode = (typeof m === 'string') ? m : (m.stock_code || m.code || '');
                          var stockName = (typeof m === 'string') ? '名称待补全' : (m.name || '名称待补全');
                          if (!stockCode) return;
                          membersEl.appendChild(createCheckNode({
                            label: stockCode + ' ' + stockName,
                            code: stockCode,
                            type: 'stock',
                            name: stockName,
                            indent: 8
                          }));
                        });
                      }
                      if (presetMembers && presetMembers.length) {
                        renderMembers(presetMembers);
                        membersEl.setAttribute('data-loaded', '1');
                        return;
                      }
                      membersEl.innerHTML = '<div style="font-size:10px;color:var(--text-secondary);padding:2px;">无成分股</div>';
                      membersEl.setAttribute('data-loaded', '1');
                    }
                  }));
                });
              });
              if (!hasAny) {
                customBlocksRoot.body.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:4px;">无自定义板块数据</div>';
              }
            })
            .catch(function() {
              customBlocksRoot.body.innerHTML = '<div style="font-size:11px;color:#e74c3c;padding:4px;">加载失败</div>';
            });

          // ── SubTask 4.5: 自选股根节点 ──
          // 调用 GET /api/meta/candidate-pool/local-sectors?category=favorite 获取自选股分组
          // 勾选时 attrtext 格式为 BLK-自选股1
          var favRoot = createRootNode('自选股', true);
          leftPane.appendChild(favRoot.root);
          favRoot.body.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:4px;">加载中...</div>';
          fetch('/api/meta/candidate-pool/local-sectors?category=favorite')
            .then(function(r) { return r.json(); })
            .then(function(resp) {
              var sectorsByCat = extractSectorsFromResponse(resp);
              var favs = sectorsByCat.favorite || [];
              favRoot.body.innerHTML = '';
              if (!favs.length) {
                favRoot.body.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:4px;">无自选股分组</div>';
                return;
              }
              favs.forEach(function(s) {
                var sectorName = s.sector_name || s.name || '';
                var sectorId = s.sector_id || s.id || '';
                var blkCode = 'BLK-' + sectorName;
                favRoot.body.appendChild(createCheckNode({
                  label: sectorName, code: blkCode, type: 'favorite', name: sectorName, sector_id: sectorId, indent: 8
                }));
              });
            })
            .catch(function() {
              favRoot.body.innerHTML = '<div style="font-size:11px;color:#e74c3c;padding:4px;">加载失败</div>';
            });

          // ── SubTask 4.8: 渲染右侧已选列表 ──
          // 显示类型标签（[个股]/[市场]/[板块]/[自选股]）和名称，每条带删除按钮
          function renderSelectedList() {
            if (!rightList) return;
            rightList.innerHTML = '';
            if (!selectedItems.length) {
              rightList.innerHTML = '<div style="font-size:11px;color:var(--text-secondary);padding:8px 12px;">暂无已选条目</div>';
              return;
            }
            var typeLabels = {stock: '[个股]', market: '[市场]', sector: '[板块]', favorite: '[自选股]'};
            selectedItems.forEach(function(item) {
              var row = document.createElement('div');
              row.style.cssText = 'display:flex;align-items:center;gap:4px;padding:4px 12px;font-size:11px;border-bottom:1px solid var(--border-color);';
              var tag = document.createElement('span');
              tag.textContent = typeLabels[item.type] || '[未知]';
              tag.style.cssText = 'font-size:10px;font-weight:600;color:var(--text-secondary);min-width:48px;';
              var nameEl = document.createElement('span');
              nameEl.textContent = item.name;
              nameEl.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
              var delBtn = document.createElement('button');
              delBtn.type = 'button';
              delBtn.innerHTML = '&times;';
              delBtn.style.cssText = 'background:none;border:none;color:#e74c3c;cursor:pointer;font-size:14px;padding:0 4px;line-height:1;';
              delBtn.title = '移除';
              delBtn.addEventListener('click', function() { removeSelection(item.code); });
              row.appendChild(tag);
              row.appendChild(nameEl);
              row.appendChild(delBtn);
              rightList.appendChild(row);
            });
          }
          renderSelectedList();

          // ── SubTask 4.9: 底部按钮事件 ──
          // 加入个股：弹出输入框输入股票代码
          addStockBtn.addEventListener('click', function() {
            var code = window.prompt('请输入股票代码（如 SH600000）：');
            if (!code) return;
            code = code.trim();
            if (!code) return;
            if (!addSelection({type: 'stock', code: code, name: code})) {
              self._showToast('该条目已在列表中', 'warning');
            }
          });
          // 加入文件：功能预留（disabled）

          // ── SubTask 4.10: 确定按钮保存逻辑 ──
          // 关闭对话框，将右侧已选列表转为 attrtext（Tab 分隔），保存到节点数据
          function confirmAndClose() {
            var attrtext = selectedItems.map(function(item) { return item.code; }).join('\t');
            try {
              var params = self._data.params || self._data;
              params.attrtext = attrtext;
              self._notifyChange('attrtext', attrtext);
            } catch(e) { console.error('保存 attrtext 失败:', e); }
            overlay.remove();
            updateSectorButton();
            self._showToast('已保存 ' + selectedItems.length + ' 条备选', 'success');
          }

          okBtn.addEventListener('click', confirmAndClose);
          cancelBtn.addEventListener('click', function() { overlay.remove(); });
          closeBtn.addEventListener('click', function() { overlay.remove(); });
          overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
          // ESC 键关闭
          var escHandler = function(e) {
            if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', escHandler); }
          };
          document.addEventListener('keydown', escHandler);
        }
      }
    }

    // ── 入池日志完整弹窗 ──
    _showEntryLogDialog() {
      var poolName = (this.poolData && this.poolData._poolId)
        || (this.poolData && this.poolData.data && this.poolData.data.pool_meta && this.poolData.data.pool_meta.name)
        || '';
      var nodeId = this._currentNodeId;
      if (!poolName || !nodeId) { this._showToast('请先选择节点', 'error'); return; }

      // 检查是否已有弹窗，有则复用
      var overlay = document.getElementById('td-entry-log-overlay');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'td-entry-log-overlay';
        overlay.className = 'td-log-overlay';
        document.body.appendChild(overlay);
      }
      overlay.style.display = 'flex';

      // 显示加载状态
      overlay.innerHTML =
        '<div class="td-log-dialog">' +
          '<div class="td-log-dialog-header">' +
            '<span class="td-log-title">入池日志 — ' + _escHtml(nodeId) + '</span>' +
            '<button class="td-btn td-log-close" title="关闭">&times;</button>' +
          '</div>' +
          '<div class="td-log-body"><div class="td-log-loading">正在加载入池记录...</div></div>' +
        '</div>';

      // 绑定关闭事件
      overlay.querySelector('.td-log-close').onclick = function () { overlay.style.display = 'none'; };
      overlay.onclick = function (e) { if (e.target === overlay) overlay.style.display = 'none'; };

      // 请求数据
      var xhr = new XMLHttpRequest();
      xhr.open('GET', '/api/tdx/history/' + encodeURIComponent(poolName) + '/' + encodeURIComponent(nodeId) + '/log');
      var selfRef = this;
      xhr.onload = function () {
        if (xhr.status !== 200) {
          overlay.querySelector('.td-log-body').innerHTML = '<div class="td-log-error">请求失败 (' + xhr.status + ')</div>';
          return;
        }
        try {
          var resp = JSON.parse(xhr.responseText);
          if (!resp.success) {
            overlay.querySelector('.td-log-body').innerHTML = '<div class="td-log-error">' + _escHtml(resp.error) + '</div>';
            return;
          }
          selfRef._renderEntryLog(overlay.querySelector('.td-log-body'), resp);
        } catch (err) {
          overlay.querySelector('.td-log-body').innerHTML = '<div class="td-log-error">解析失败</div>';
        }
      };
      xhr.send();
    }

    _renderEntryLog(container, data) {
      var log = data.log || [];
      if (!log.length) {
        container.innerHTML = '<div class="td-log-empty">暂无入池记录</div>';
        return;
      }

      var html = '';
      html += '<div class="td-log-summary">';
      html += '<span>共 <b>' + data.total_dates + '</b> 个交易日，<b>' + data.total_entries + '</b> 条入池记录</span>';
      html += '</div>';

      for (var di = 0; di < log.length; di++) {
        var day = log[di];
        html += '<div class="td-log-day">';
        html += '<div class="td-log-day-header">';
        html += '<span class="td-log-date">' + day.date_fmt + '</span>';
        html += '<span class="td-log-day-count">' + day.count + ' 只</span>';
        html += '</div>';
        html += '<table class="td-log-day-table"><thead><tr>';
        html += '<th>代码</th><th>名称</th><th>时间</th><th>入池价</th><th>最高收益</th><th>周期</th>';
        html += '</tr></thead><tbody>';

        var dayStocks = day.stocks || [];
        for (var si = 0; si < dayStocks.length; si++) {
          var s = dayStocks[si];
          var rate = parseFloat(s.maxrate) || 0;
          var rc = rate >= 0 ? 'td-positive' : 'td-negative';
          html += '<tr>';
          html += '<td class="td-log-code">' + _escHtml(s.code) + '</td>';
          html += '<td>' + _escHtml(s.name) + '</td>';
          html += '<td>' + _escHtml(s.intime) + '</td>';
          html += '<td>' + _escHtml(s.inprice) + '</td>';
          html += '<td class="' + rc + '">' + _escHtml(s.maxrate) + '%</td>';
          html += '<td>' + _escHtml(s.maxperiod) + '</td>';
          html += '</tr>';
        }
        html += '</tbody></table></div>';
      }

      container.innerHTML = html;
    }

    // 渲染历史数据表格
    _renderHistoryTable(container, stocks, date) {
      if (!stocks.length) {
        container.innerHTML = '<div class="td-bsavehis-empty">该日期无入池记录</div>';
        // 即使为空也通知画布（清空历史视图状态）
        this._notifyHistoryView(date, []);
        return;
      }
      var html = '<div class="td-bsavehis-table-wrap">';
      html += '<div class="td-bsavehis-table-header">';
      html += '<span>历史入池记录 (' + date + ') — 共 ' + stocks.length + ' 只</span>';
      html += '<button class="td-btn td-bsavehis-close" title="关闭">×</button>';
      html += '</div>';
      html += '<table class="td-bsavehis-table"><thead><tr>';
      html += '<th>市场</th><th>代码</th><th>名称</th><th>进入日期</th><th>时间</th><th>进入价</th><th>最高收益</th><th>最高周期</th><th>最高价</th>';
      html += '</tr></thead><tbody>';
      for (var i = 0; i < stocks.length; i++) {
        var s = stocks[i];
        var marketLabel = s.market === '0' ? '深圳' : (s.market === '1' ? '上海' : s.market);
        var rateClass = parseFloat(s.maxrate) >= 0 ? 'td-positive' : 'td-negative';
        html += '<tr>';
        html += '<td>' + marketLabel + '</td>';
        html += '<td>' + _escHtml(s.code) + '</td>';
        html += '<td>' + _escHtml(s.name) + '</td>';
        html += '<td>' + _escHtml(s.indate) + '</td>';
        html += '<td>' + _escHtml(s.intime) + '</td>';
        html += '<td>' + _escHtml(s.inprice) + '</td>';
        html += '<td class="' + rateClass + '">' + _escHtml(s.maxrate) + '%</td>';
        html += '<td>' + _escHtml(s.maxperiod) + '</td>';
        html += '<td>' + _escHtml(s.maxprice) + '</td>';
        html += '</tr>';
      }
      html += '</tbody></table></div>';
      container.innerHTML = html;
      // 通知画布：该节点正在查看历史数据，更新节点外观
      this._notifyHistoryView(date, stocks);
    }

    // 通知画布：历史数据查看事件（让节点变色+显示股票列表）
    _notifyHistoryView(date, stocks) {
      if (!this.container) return;
      var evt = new CustomEvent('tdx:historyView', {
        bubbles: true,
        detail: {
          nodeId: this._currentNodeId,
          date: date,
          stocks: stocks || [],
          stockCount: (stocks || []).length
        }
      });
      this.container.dispatchEvent(evt);
    }

    _handleChange(target) {
      // 检查字段是否被 disabled（属性所有权规则）
      var fieldEl = target.closest('.td-field');
      if (fieldEl && fieldEl.classList.contains('td-field-disabled')) {
        return; // 禁止修改 disabled 字段
      }

      var path = target.getAttribute('data-path');
      // flag checkbox 没有 data-path，从父元素获取
      if (!path && target.classList.contains('td-flag-cb')) {
        var flagContainer = target.closest('.td-field-flags');
        if (flagContainer) path = flagContainer.getAttribute('data-path');
      }
      // market checkbox 没有 data-path，从父元素获取
      if (!path && target.classList.contains('td-market-cb')) {
        var marketContainer = target.closest('.td-field-markets');
        if (marketContainer) path = marketContainer.getAttribute('data-path');
      }
      if (!path) return;

      var value;

      // 位标志复选框
      if (target.classList.contains('td-flag-cb')) {
        var flagGroup = target.closest('.td-field-flags');
        // 收集所有共享同一 data_path 的 flag groups 的值
        var allFlags = this._collectAllFlagGroupsForPath(path);
        // 获取该 data_path 下所有 flags 配置（合并多个 flag groups）
        var allFlagsConfig = this._getAllFlagsConfigForPath(path);
        value = DataBinder.encodeAttrFlags(allFlags, allFlagsConfig);
        DataBinder.set(this._data, path, value);
      }
      // 市场复选框
      else if (target.classList.contains('td-market-cb')) {
        var marketGroup = target.closest('.td-field-markets');
        value = this._collectMarketValues(marketGroup);
        DataBinder.set(this._data, path, value);
      }
      // 动作类型下拉
      else if (target.classList.contains('td-action-type')) {
        var actionField = target.closest('.td-field-action');
        var paramInput = actionField.querySelector('.td-action-param');
        var actionType = parseInt(target.value, 10);
        var param = parseInt(paramInput.value, 10) || 0;
        value = DataBinder.encodeActionCompound(actionType, param);
        DataBinder.set(this._data, path, value);
      }
      // 动作参数输入
      else if (target.classList.contains('td-action-param')) {
        var actionField2 = target.closest('.td-field-action');
        var typeSelect = actionField2.querySelector('.td-action-type');
        var actionType2 = parseInt(typeSelect.value, 10);
        var param2 = parseInt(target.value, 10) || 0;
        value = DataBinder.encodeActionCompound(actionType2, param2);
        DataBinder.set(this._data, path, value);
      }
      // 颜色选择器
      else if (target.classList.contains('td-color-input')) {
        value = _hexToInt(target.value);
        DataBinder.set(this._data, path, value);
        var valSpan = target.parentElement.querySelector('.td-color-value');
        if (valSpan) valSpan.textContent = value;
      }
      // 转移模式 radio 按钮
      else if (target.classList.contains('td-transfer-mode-radio')) {
        var mode = target.getAttribute('data-mode');
        var attrVal = DataBinder.get(this._data, path, 0);
        // 根据模式设置位标志
        // copy: keep_source=1 (bit12=0x1000), delete_source=0
        // move: keep_source=0, delete_source=1 (bit0=0x1)
        // overwrite: clear_dest_first=1 (bit13=0x2000)
        // constituent: output_constituent=1 (bit14=0x4000)
        if (mode === 'copy') {
          attrVal = (attrVal & ~0x1) | 0x1000;  // set keep_source, clear delete_source
        } else if (mode === 'move') {
          attrVal = (attrVal & ~0x1000) | 0x1;  // set delete_source, clear keep_source
        } else if (mode === 'overwrite') {
          attrVal = attrVal | 0x2000;  // set clear_dest_first
        } else if (mode === 'constituent') {
          attrVal = attrVal | 0x4000;  // set output_constituent
        }
        value = attrVal;
        DataBinder.set(this._data, path, value);
        // 同时更新 delete_source 和 keep_source 字段
        this._data.delete_source = (mode === 'move');
        this._data.keep_source = (mode === 'copy');
        // 刷新面板以更新模式标签
        this._reRenderCurrentPanel();
        return;
      }
      // 数字输入
      else if (target.type === 'number') {
        value = target.value === '' ? null : parseFloat(target.value);
        DataBinder.set(this._data, path, value);
      }
      // 下拉选择
      else if (target.tagName === 'SELECT') {
        var rawVal = target.value;
        value = rawVal === '' ? null : (isNaN(rawVal) ? rawVal : parseFloat(rawVal));
        DataBinder.set(this._data, path, value);
      }
      // 文本输入
      else {
        value = target.value;
        DataBinder.set(this._data, path, value);
      }

      // 处理字段联动
      this._handleLinkage(path, value);

      // 校验字段值
      this._validateField(path, value);

      // 通知变更监听器
      this._notifyChange(path, value);

      // 边编辑器条件公式摘要联动更新
      if (['condition_type', 'formula_ref', 'intersection_source'].indexOf(path) >= 0) {
        this._updateConditionSummary();
      }
    }

    _updateConditionSummary() {
      var box = this.container.querySelector('.td-condition-summary-box');
      if (!box) return;
      var ctype = DataBinder.get(this._data, 'condition_type') || '';
      var fref = DataBinder.get(this._data, 'formula_ref') || '';
      var isrc = DataBinder.get(this._data, 'intersection_source') || '';
      var summary = '-';
      if (ctype === 'INTERSECTION' && isrc) summary = 'INTERSECTION (' + _escHtml(isrc) + ')';
      else if (ctype === 'INTERSECTION') summary = 'INTERSECTION';
      else if (fref) summary = _escHtml(fref);
      else if (ctype) summary = _escHtml(ctype);
      box.textContent = summary;
      box.title = summary;
    }

    _handleLinkage(changedPath, value) {
      // 构建反向依赖图（缓存）
      if (!this._dependentsMap) {
        this._dependentsMap = {};
        var cacheKey = this._nodeType + ':' + this._poolType;
        var layout = this._layoutCache.get(cacheKey);
        if (layout) {
          for (var s = 0; s < layout.sections.length; s++) {
            for (var f = 0; f < layout.sections[s].fields.length; f++) {
              var field = layout.sections[s].fields[f];
              if (field.depends_on) {
                if (!this._dependentsMap[field.depends_on]) {
                  this._dependentsMap[field.depends_on] = [];
                }
                this._dependentsMap[field.depends_on].push(field.key);
              }
            }
          }
        }
      }

      // 递归更新字段可见性
      var self = this;
      function updateFieldVisibility(fieldKey, parentVisible) {
        var fieldEl = self.container.querySelector('.td-field[data-key="' + fieldKey + '"]');
        if (!fieldEl) return;

        var fieldConfig = self._getFieldConfig(fieldKey);
        if (!fieldConfig) return;

        var isVisible = parentVisible;
        if (fieldConfig.depends_on && fieldConfig.active_when) {
          if (!parentVisible) {
            isVisible = false;
          } else {
            var parentVal = DataBinder.get(self._data, self._getFieldPath(fieldConfig.depends_on));
            isVisible = self._checkActiveWhen(parentVal, fieldConfig.active_when);
          }
        }

        if (isVisible) {
          fieldEl.classList.remove('td-field-inactive');
          fieldEl.classList.add('td-field-active');
        } else {
          fieldEl.classList.add('td-field-inactive');
          fieldEl.classList.remove('td-field-active');
        }

        // 级联传播
        var children = self._dependentsMap[fieldKey] || [];
        for (var i = 0; i < children.length; i++) {
          updateFieldVisibility(children[i], isVisible);
        }
      }

      // 找到变更字段对应的key
      var changedKey = this._pathToKey(changedPath);
      if (changedKey) {
        // 更新直接依赖此字段的子字段
        var children = this._dependentsMap[changedKey] || [];
        var parentEl = this.container.querySelector('.td-field[data-key="' + changedKey + '"]');
        var parentVisible = !parentEl || !parentEl.classList.contains('td-field-inactive');
        for (var i = 0; i < children.length; i++) {
          updateFieldVisibility(children[i], parentVisible);
        }

        // TDX nset 变更级联：动态更新 ntjindexno / noperate 选项及字段可见性
        if (changedKey === 'nset' || changedKey.indexOf('nset') >= 0) {
          var newNsetVal = DataBinder.get(this._data, changedPath);
          // 1. 动态填充 ntjindexno 选项
          this._populateNtjIndexnoOptions(newNsetVal);
          // 2. 过滤 noperate 选项
          this._filterNoperateOptions(newNsetVal);
          // 3. 强制刷新所有 func 子字段的可见性（确保 visibility 矩阵生效）
          this._refreshTdxFuncFieldsVisibility(newNsetVal);
        }
      }
    }

    // ── TDX 股票池 UI 动态联动方法 ──────────────────────

    /**
     * 获取 TDX ntjindexno lookup 数据（带缓存）。
     * 数据来源：window._tdxEnumsData.tdx_ntjindexno_lookup
     */
    _getTdxLookup() {
      if (!TableDrivenPanel._tdxLookupCache) {
        if (window._tdxEnumsData && window._tdxEnumsData.tdx_ntjindexno_lookup) {
          TableDrivenPanel._tdxLookupCache = window._tdxEnumsData.tdx_ntjindexno_lookup;
        } else {
          TableDrivenPanel._tdxLookupCache = { nset_3_financial: { fields: [] }, nset_4_market: { fields: [] } };
        }
      }
      return TableDrivenPanel._tdxLookupCache;
    }

    /**
     * 获取 TDX field visibility 数据（带缓存）。
     * 数据来源：window._tdxEnumsData.tdx_field_visibility
     */
    _getTdxVisibility() {
      if (!TableDrivenPanel._tdxVisibilityCache) {
        if (window._tdxEnumsData && window._tdxEnumsData.tdx_field_visibility) {
          TableDrivenPanel._tdxVisibilityCache = window._tdxEnumsData.tdx_field_visibility;
        } else {
          TableDrivenPanel._tdxVisibilityCache = { noperate_availability: {} };
        }
      }
      return TableDrivenPanel._tdxVisibilityCache;
    }

    /**
     * 在 select 元素中查找指定 value 的 option 是否存在。
     */
    _findOptionByValue(selectEl, value) {
      for (var i = 0; i < selectEl.options.length; i++) {
        if (String(selectEl.options[i].value) === String(value)) return true;
      }
      return false;
    }

    /**
     * 根据当前 nset 值填充 ntjindexno 下拉框的 options。
     * nset=3 时显示财务字段（30项），nset=4 时显示行情字段（12项）。
     */
    _populateNtjIndexnoOptions(nsetValue) {
      var lookup = this._getTdxLookup();
      var selectEl = this.container.querySelector('select[data-path*="ntjindexno"]');
      if (!selectEl) return;

      var fields = [];
      if (nsetValue == 3) {
        fields = (lookup.nset_3_financial || {}).fields || [];
      } else if (nsetValue == 4) {
        fields = (lookup.nset_4_market || {}).fields || [];
      }

      if (fields.length === 0) return;

      var currentVal = selectEl.value;
      var html = '<option value="">-- 请选择 --</option>';
      for (var i = 0; i < fields.length; i++) {
        var f = fields[i];
        var sel = (String(f.value) === String(currentVal)) ? ' selected' : '';
        var titleAttr = f.desc ? ' title="' + _escAttr(f.desc) + '"' : '';
        html += '<option value="' + _escAttr(f.value) + '"' + sel + titleAttr + '>' +
                _escHtml(f.label) + '</option>';
      }
      selectEl.innerHTML = html;

      // 如果之前有值且在新 options 中存在，保持选中
      if (currentVal && this._findOptionByValue(selectEl, currentVal)) {
        selectEl.value = currentVal;
      }
    }

    /**
     * 根据当前 nset 值过滤 noperate 下拉框的可选操作符。
     * 使用 visibility 矩阵中的 noperate_availability 映射。
     */
    _filterNoperateOptions(nsetValue) {
      var vis = this._getTdxVisibility();
      var availability = (vis.noperate_availability || {})['nset_' + nsetValue];

      var selectEl = this.container.querySelector('select[data-path*="noperate"]');
      if (!selectEl || !availability) return;

      // 完整的 noperate options 定义（10项）
      var allOptions = [
        { value: 0, label: '等于' },
        { value: 1, label: '大于' },
        { value: 2, label: '小于' },
        { value: 3, label: '上穿(金叉)' },
        { value: 4, label: '下破(死叉)' },
        { value: 5, label: '持股' },
        { value: 6, label: '排名前N' },
        { value: 7, label: '排名后N' },
        { value: 8, label: '上拐' },
        { value: 9, label: '下拐' }
      ];

      var currentVal = selectEl.value;
      var html = '<option value="">-- 请选择 --</option>';
      for (var i = 0; i < allOptions.length; i++) {
        if (availability.indexOf(allOptions[i].value) >= 0) {
          var opt = allOptions[i];
          var sel = (String(opt.value) === String(currentVal)) ? ' selected' : '';
          html += '<option value="' + opt.value + '"' + sel + '>' + _escHtml(opt.label) + '</option>';
        }
      }
      selectEl.innerHTML = html;

      if (currentVal && this._findOptionByValue(selectEl, currentVal)) {
        selectEl.value = currentVal;
      }
    }

    /**
     * 根据 visibility 矩阵强制刷新所有 func 子字段的可见性。
     * TDX 使用统一的字段结构，但不同 nset 类型只读取部分字段。
     */
    _refreshTdxFuncFieldsVisibility(nsetValue) {
      // 同步"从公式库选择"按钮可见性（nset=0/1/2 显示，3/4/5 隐藏）
      // 放在 matrix 早返回之前，确保 nset=5（无 matrix）时也能隐藏按钮
      this._updateTdxFormulaPickerVisibility(nsetValue);

      var vis = this._getTdxVisibility();
      var matrix = (vis.visibility || {})['nset_' + nsetValue];
      if (!matrix) return;

      // 遍历所有 td-field 元素，检查其 key 是否在 visibility 矩阵中
      var fieldEls = this.container.querySelectorAll('.td-field[data-key]');
      for (var i = 0; i < fieldEls.length; i++) {
        var el = fieldEls[i];
        var key = el.getAttribute('data-key');
        if (key && matrix[key] !== undefined) {
          var status = matrix[key];  // 'valid' | 'residual' | 'na'
          if (status === 'valid') {
            el.classList.remove('td-field-inactive');
            el.classList.add('td-field-active');
            el.style.display = '';
          } else {
            // residual 或 na: 隐藏但不禁用
            el.classList.add('td-field-inactive');
            el.classList.remove('td-field-active');
            el.style.display = 'none';
          }
        }
      }
    }

    _checkActiveWhen(parentVal, activeWhen) {
      if (!activeWhen) return true;
      if (Array.isArray(activeWhen)) {
        return activeWhen.indexOf(parentVal) >= 0;
      }
      return parentVal === activeWhen;
    }

    _getFieldPath(fieldKey) {
      var cacheKey = this._nodeType + ':' + this._poolType;
      var layout = this._layoutCache.get(cacheKey);
      if (!layout) return fieldKey;
      for (var s = 0; s < layout.sections.length; s++) {
        for (var f = 0; f < layout.sections[s].fields.length; f++) {
          if (layout.sections[s].fields[f].key === fieldKey) {
            return layout.sections[s].fields[f].data_path;
          }
        }
      }
      return fieldKey;
    }

    _pathToKey(path) {
      var cacheKey = this._nodeType + ':' + this._poolType;
      var layout = this._layoutCache.get(cacheKey);
      if (!layout) return null;
      for (var s = 0; s < layout.sections.length; s++) {
        for (var f = 0; f < layout.sections[s].fields.length; f++) {
          if (layout.sections[s].fields[f].data_path === path) {
            return layout.sections[s].fields[f].key;
          }
        }
      }
      return null;
    }

    _collectFlagGroupValues(flagGroupEl) {
      var cbs = flagGroupEl.querySelectorAll('.td-flag-cb');
      var result = {};
      for (var i = 0; i < cbs.length; i++) {
        result[cbs[i].getAttribute('data-flag-name')] = cbs[i].checked;
      }
      return result;
    }

    _collectMarketValues(marketGroupEl) {
      var cbs = marketGroupEl.querySelectorAll('.td-market-cb');
      var selected = [];
      for (var i = 0; i < cbs.length; i++) {
        if (cbs[i].checked) selected.push(cbs[i].getAttribute('data-market-code'));
      }
      return selected;
    }

    _getFlagsConfig(fieldKey) {
      var cacheKey = this._nodeType + ':' + this._poolType;
      var layout = this._layoutCache.get(cacheKey);
      if (!layout) return [];
      for (var s = 0; s < layout.sections.length; s++) {
        for (var f = 0; f < layout.sections[s].fields.length; f++) {
          if (layout.sections[s].fields[f].key === fieldKey) {
            return layout.sections[s].fields[f].flags || [];
          }
        }
      }
      return [];
    }

    // 获取原始值（不自动提取 .raw）
    _getRawValue(path) {
      if (!this._data || !path) return undefined;
      var keys = path.split('.');
      var cur = this._data;
      for (var i = 0; i < keys.length; i++) {
        if (cur == null || typeof cur !== 'object') return undefined;
        cur = cur[keys[i]];
      }
      return cur;
    }

    // 收集所有共享同一 data_path 的 flag groups 的值
    _collectAllFlagGroupsForPath(path) {
      var allFlags = {};
      var containers = this.container.querySelectorAll('.td-field-flags[data-path="' + path + '"]');
      for (var c = 0; c < containers.length; c++) {
        var groupFlags = this._collectFlagGroupValues(containers[c]);
        for (var k in groupFlags) {
          allFlags[k] = groupFlags[k];
        }
      }
      return allFlags;
    }

    // 获取指定 data_path 的所有 flags 配置（合并多个 flag groups）
    _getAllFlagsConfigForPath(path) {
      var cacheKey = this._nodeType + ':' + this._poolType;
      var layout = this._layoutCache.get(cacheKey);
      if (!layout) return [];
      var allFlags = [];
      for (var s = 0; s < layout.sections.length; s++) {
        for (var f = 0; f < layout.sections[s].fields.length; f++) {
          var field = layout.sections[s].fields[f];
          if (field.data_path === path && field.flags) {
            allFlags = allFlags.concat(field.flags);
          }
        }
      }
      return allFlags;
    }

    _getFieldConfig(fieldKey) {
      var cacheKey = this._nodeType + ':' + this._poolType;
      var layout = this._layoutCache.get(cacheKey);
      if (!layout) return null;
      for (var s = 0; s < layout.sections.length; s++) {
        for (var f = 0; f < layout.sections[s].fields.length; f++) {
          var field = layout.sections[s].fields[f];
          if (field.key === fieldKey || field.data_path === fieldKey) {
            return field;
          }
        }
      }
      return null;
    }

    _validateField(path, value) {
      // 查找对应字段的DOM元素和配置
      var fieldEl = this.container.querySelector('.td-field[data-path="' + path + '"], .td-field-flags[data-path="' + path + '"]');
      if (!fieldEl) return;

      var key = fieldEl.getAttribute('data-key');
      var fieldConfig = this._getFieldConfig(key);
      if (!fieldConfig || !fieldConfig.validation) return;

      var errors = ValidationEngine.validate(fieldConfig, value, this._data);

      // 清除旧的错误状态
      fieldEl.classList.remove('td-field-error');
      var oldMsg = fieldEl.querySelector('.td-field-error-msg');
      if (oldMsg) oldMsg.remove();

      // 显示新的错误状态
      if (errors.length > 0) {
        fieldEl.classList.add('td-field-error');
        var msgEl = document.createElement('div');
        msgEl.className = 'td-field-error-msg';
        msgEl.textContent = errors[0];
        fieldEl.appendChild(msgEl);
      }
    }

    _notifyChange(path, value) {
      // TDX 节点的 _data 结构为 {id, type, label, params: {...}}，path 以 "params." 开头
      // 但 this._currentItem.params 已是 params 对象本身，需要剥离 "params." 前缀
      var syncPath = path;
      if (syncPath.indexOf('params.') === 0) {
        syncPath = syncPath.substring(7);
      }
      // 1. 写回 poolData
      if (this.poolData) {
        if (this._currentNodeId) {
          var updateObj = {};
          // 如果值是整数但原始数据是 {raw, bits} 对象，需要构造完整的对象
          var rawOriginal = this._getRawValue(path);
          if (typeof value === 'number' && typeof rawOriginal === 'object' && rawOriginal !== null && typeof rawOriginal.raw === 'number') {
            // 位标志对象：更新 raw 并重新计算 bits
            var newRaw = value;
            var newBits = {};
            // 获取该 data_path 下所有 flags 配置来解码
            var allFlagsConfig = this._getAllFlagsConfigForPath(path);
            if (allFlagsConfig.length > 0) {
              newBits = DataBinder.decodeAttrFlags(newRaw, allFlagsConfig);
            }
            updateObj[syncPath] = { raw: newRaw, bits: newBits };
          } else {
            updateObj[syncPath] = value;
          }
          if (typeof this.poolData.updateNodeParams === 'function') {
            this.poolData.updateNodeParams(this._currentNodeId, updateObj);
          }
        } else if (this._currentEdgeId) {
          var edgeUpdate = {};
          edgeUpdate[syncPath] = value;
          if (typeof this.poolData.updateEdge === 'function') {
            this.poolData.updateEdge(this._currentEdgeId, edgeUpdate);
          }
        }
      }

      // 2. 触发 onPropertyChange 回调（兼容 PropertyPanel 接口）
      if (this.onPropertyChange && this._currentItem) {
        // 将变更同步到 currentItem.params（使用剥离前缀的 syncPath）
        if (this._currentItem.params) {
          DataBinder.set(this._currentItem.params, syncPath, value);
        }
        this.onPropertyChange(this._currentItem, path, value);
      }

      // 3. 触发 onChange 注册监听器
      for (var i = 0; i < this._changeListeners.length; i++) {
        try {
          this._changeListeners[i](path, value, this._data);
        } catch (e) {
          console.error('变更监听器异常:', e);
        }
      }

      // 4. 持久化变更到后端（防抖 300ms）
      this._schedulePersist();
    }

    // 调度持久化（防抖）
    _schedulePersist() {
      if (this._persistTimer) {
        clearTimeout(this._persistTimer);
      }
      var self = this;
      this._persistTimer = setTimeout(function () {
        self._persistTimer = null;
        self._persistChange();
      }, 300);
    }

    // 持久化变更到后端 API
    _persistChange() {
      var self = this;

      try {
        // 优先使用 poolData 的保存方法
        if (this.poolData) {
          if (this._currentNodeId && typeof this.poolData.saveTDXNode === 'function') {
            this.poolData.saveTDXNode(this._currentNodeId).catch(function (err) {
              console.error('持久化节点失败:', err);
              self._showPersistError();
            });
            return;
          }
          if (this._currentEdgeId && typeof this.poolData.saveTDXEdge === 'function') {
            this.poolData.saveTDXEdge(this._currentEdgeId).catch(function (err) {
              console.error('持久化连线失败:', err);
              self._showPersistError();
            });
            return;
          }
        }

        // 回退：直接 fetch 调用
        if (this._currentNodeId) {
          var nodeUrl, nodeBody;
          if (this._poolType === 'tdx') {
            var poolName = (this.poolData && this.poolData._poolId)
              || (this.poolData && this.poolData.data && this.poolData.data.pool_meta && this.poolData.data.pool_meta.name)
              || '';
            nodeUrl = '/api/tdx/pools/' + encodeURIComponent(poolName) + '/cells/' + encodeURIComponent(this._currentNodeId);
            nodeBody = JSON.stringify({ params: this._data });
          } else {
            nodeUrl = '/api/dzh/cells/' + encodeURIComponent(this._currentNodeId);
            nodeBody = JSON.stringify({ params: this._data });
          }
          fetch(nodeUrl, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: nodeBody })
            .then(function (resp) {
              if (!resp.ok) throw new Error('HTTP ' + resp.status);
            })
            .catch(function (err) {
              console.error('持久化节点失败:', err);
              self._showPersistError();
            });
        } else if (this._currentEdgeId) {
          var edgeUrl, edgeBody;
          if (this._poolType === 'tdx') {
            var poolName2 = (this.poolData && this.poolData._poolId)
              || (this.poolData && this.poolData.data && this.poolData.data.pool_meta && this.poolData.data.pool_meta.name)
              || '';
            edgeUrl = '/api/tdx/pools/' + encodeURIComponent(poolName2) + '/flows/' + encodeURIComponent(this._currentEdgeId);
            edgeBody = JSON.stringify({ params: this._data });
          } else {
            edgeUrl = '/api/dzh/flows/' + encodeURIComponent(this._currentEdgeId);
            edgeBody = JSON.stringify({ params: this._data });
          }
          fetch(edgeUrl, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: edgeBody })
            .then(function (resp) {
              if (!resp.ok) throw new Error('HTTP ' + resp.status);
            })
            .catch(function (err) {
              console.error('持久化连线失败:', err);
              self._showPersistError();
            });
        }
      } catch (e) {
        console.error('持久化异常:', e);
      }
    }

    // 简短的持久化错误提示
    _showPersistError() {
      var panel = this.container.querySelector('.td-panel');
      if (!panel) return;
      var indicator = panel.querySelector('.td-persist-error');
      if (indicator) return; // 已有提示
      indicator = document.createElement('div');
      indicator.className = 'td-persist-error';
      indicator.textContent = '保存失败';
      indicator.style.cssText = 'position:absolute;top:4px;right:4px;background:#ff4d4f;color:#fff;padding:2px 8px;border-radius:3px;font-size:12px;z-index:10;';
      panel.style.position = 'relative';
      panel.appendChild(indicator);
      setTimeout(function () {
        if (indicator.parentNode) indicator.parentNode.removeChild(indicator);
      }, 3000);
    }

    // 热加载
    _startHotReload() {
      var self = this;
      this._reloadTimer = setInterval(function () {
        self._ajax('POST', self.apiBase + '/reload', {}, function (resp) {
          if (resp && resp.changed && resp.changed.length > 0) {
            // 配置变更，重新渲染面板
            self._layoutCache.clear();
            if (self._currentNodeId) {
              self.showForNode(self._currentNodeId);
            } else if (self._currentEdgeId) {
              self.showForEdge(self._currentEdgeId);
            }
          }
        });
      }, this._hotReloadInterval);
    }

    // Toast提示
    _showToast(msg, type) {
      var toast = document.createElement('div');
      toast.className = 'td-toast td-toast-' + (type || 'info');
      toast.textContent = msg;
      var bg = type === 'error' ? '#c0392b' : type === 'success' ? '#27ae60' : type === 'warning' ? '#e67e22' : '#2980b9';
      toast.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:' + bg + ';color:#fff;padding:8px 20px;border-radius:4px;font-size:12px;z-index:10000;transition:opacity 0.3s;max-width:80%;box-sizing:border-box;';
      document.body.appendChild(toast);
      setTimeout(function () {
        toast.style.opacity = '0';
        setTimeout(function () { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
      }, 2000);
    }

    // 公式验证：调用后端 /api/formula/validate 做语法检查，并展示结果
    async _decodeFormulaViaBackend(indiBase64, ency) {
      var self = this;
      try {
        var resp = await fetch('/api/formula/decode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ indi_b64: indiBase64, ency: ency })
        });
        var data = await resp.json();
        if (data && data.success && data.data && data.data.decoded) {
          DataBinder.set(self._data, 'formula_decoded', data.data.decoded);
          var textarea = self.container.querySelector('.td-formula-textarea');
          if (textarea) {
            textarea.value = data.data.decoded;
          }
          self._notifyChange('formula_decoded', data.data.decoded);
        }
      } catch (e) {
        // 解码失败忽略
      }
    }

    // 获取当前池的 ency（优先 pool_meta 字符串以避免大整数精度丢失）
    _getPoolEncy() {
      var pm = this.poolData && this.poolData.data && this.poolData.data.pool_meta;
      return (pm && pm.ency) || DataBinder.get(this._data, 'ency') || 0;
    }

    async _encodeFormulaViaBackend(formulaText, encodeTo) {
      try {
        var resp = await fetch('/api/formula/encode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ formula_text: formulaText, ency: this._getPoolEncy() })
        });
        var data = await resp.json();
        if (data && data.success && data.data && data.data.encoded) {
          DataBinder.set(this._data, encodeTo, data.data.encoded);
          this._notifyChange(encodeTo, data.data.encoded);
        }
      } catch (e) {
        // 编码失败忽略
      }
    }

    async _validateFormula(formulaField, script) {
      var resultDiv = formulaField.querySelector('.td-formula-result');
      if (!resultDiv) {
        resultDiv = document.createElement('div');
        resultDiv.className = 'td-formula-result';
        resultDiv.style.cssText = 'margin-top:4px;font-size:11px;padding:4px 8px;border-radius:3px;';
        var wrap = formulaField.querySelector('.td-formula-wrap');
        if (wrap) wrap.appendChild(resultDiv);
      }
      resultDiv.style.display = 'block';
      resultDiv.style.background = 'var(--bg-secondary, #f5f5f5)';
      resultDiv.style.color = 'var(--text-secondary, #888)';
      resultDiv.textContent = '验证中...';

      var data;
      try {
        var resp = await fetch('/api/formula/validate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ script: script, formula_type: 'indicator' })
        });
        data = await resp.json();
      } catch (e) {
        this._renderFormulaResult(resultDiv, false, [], ['验证请求失败: ' + e.message]);
        return;
      }

      var valid = data.valid === true;
      var outvars = data.outvars || [];
      var errors = data.errors || [];
      this._renderFormulaResult(resultDiv, valid, outvars, errors);

      // 可选增强：验证通过后调用 test 端点对默认股票（000001）做计算
      if (valid) {
        this._testFormula(resultDiv, script);
      }
    }

    // 渲染公式验证结果（通过=绿色，失败=红色）
    _renderFormulaResult(resultDiv, valid, outvars, errors) {
      var html;
      if (valid) {
        resultDiv.style.background = 'rgba(39, 174, 96, 0.1)';
        resultDiv.style.color = '#27ae60';
        html = '✓ 验证通过';
        if (outvars && outvars.length) {
          html += '，输出变量：' + outvars.map(function (v) { return _escHtml(v); }).join(', ');
        }
      } else {
        resultDiv.style.background = 'rgba(192, 57, 43, 0.1)';
        resultDiv.style.color = '#c0392b';
        html = '✗ 验证失败';
        if (errors && errors.length) {
          html += '：<br>' + errors.map(function (err) { return '· ' + _escHtml(err); }).join('<br>');
        }
      }
      resultDiv.innerHTML = html;
    }

    // 可选增强：调用 /api/formula/test 对默认股票（000001）执行计算
    async _testFormula(resultDiv, script) {
      var testSpan = document.createElement('span');
      testSpan.style.color = 'var(--text-secondary, #888)';
      testSpan.textContent = '计算测试中（000001）...';
      resultDiv.appendChild(document.createElement('br'));
      resultDiv.appendChild(testSpan);

      var data;
      try {
        var resp = await fetch('/api/formula/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ script: script, stock_code: '000001', period: '1d' })
        });
        data = await resp.json();
      } catch (e) {
        testSpan.style.color = '#c0392b';
        testSpan.textContent = '计算测试失败: ' + e.message;
        return;
      }
      if (data.success) {
        var result = (data.data && data.data.result) || {};
        var lines = [];
        for (var code in result) {
          if (!result.hasOwnProperty(code)) continue;
          var val = result[code];
          if (val && typeof val === 'object') {
            var parts = [];
            for (var k in val) {
              if (!val.hasOwnProperty(k)) continue;
              parts.push(k + '=' + (typeof val[k] === 'number' ? val[k].toFixed(4) : val[k]));
            }
            lines.push(code + ': ' + parts.join(', '));
          } else {
            lines.push(code + ': ' + (typeof val === 'number' ? val.toFixed(4) : val));
          }
        }
        testSpan.style.color = '#27ae60';
        testSpan.textContent = '计算结果：' + lines.join(' | ');
      } else {
        testSpan.style.color = '#c0392b';
        testSpan.textContent = '计算测试失败: ' + (data.error || '未知错误');
      }
    }

    // AJAX工具
    _ajax(method, url, data, onSuccess, onError) {
      var xhr = new XMLHttpRequest();
      var sep = url.indexOf('?') === -1 ? '?' : '&';
      xhr.open(method, url + sep + '_t=' + Date.now(), true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            onSuccess(JSON.parse(xhr.responseText));
          } catch (e) {
            onError && onError(e);
          }
        } else {
          onError && onError(xhr.statusText);
        }
      };
      xhr.onerror = function () { onError && onError('Network error'); };
      xhr.send(JSON.stringify(data));
    }
  }

  TableDrivenPanel._tdxLookupCache = null;
  TableDrivenPanel._tdxVisibilityCache = null;

  // ─── 工具函数 ────────────────────────────────────────────────

  function _deepClone(obj) {
    if (obj == null || typeof obj !== 'object') return obj;
    try {
      return JSON.parse(JSON.stringify(obj));
    } catch (e) {
      var clone = Array.isArray(obj) ? [] : {};
      for (var k in obj) {
        if (obj.hasOwnProperty(k)) clone[k] = _deepClone(obj[k]);
      }
      return clone;
    }
  }

  function _escHtml(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function _escAttr(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  }

  function _formatFieldValue(field) {
    var val = field.value;
    if (val == null) return field.default || '';
    if (typeof val === 'object') {
      // flag_group / action_compound 等对象值
      try { return JSON.stringify(val); } catch (e) { return String(val); }
    }
    return String(val);
  }

  // dzhIntToCssHex 已合并至 canvas.js（统一实现，消除重复代码）
  // 通过 window.dzhIntToCssHex 全局引用，_intToHex 及其他调用方可直接使用

  function _intToHex(n) { return dzhIntToCssHex(n); }

  function _hexToInt(hex) {
    // CSS hex (#RRGGBB) → DZH BGR 整数
    hex = hex.replace('#', '');
    if (hex.length !== 6) return -1;
    var r = parseInt(hex.substring(0, 2), 16);
    var g = parseInt(hex.substring(2, 4), 16);
    var b = parseInt(hex.substring(4, 6), 16);
    return (b << 16) | (g << 8) | r;  // BGR格式存储
  }

  function _formatHhmmss(val) {
    if (!val) return '';
    var s = String(val);
    while (s.length < 6) s = '0' + s;
    return s.substring(0, 2) + ':' + s.substring(2, 4) + ':' + s.substring(4, 6);
  }

  // ─── 动态表单引擎类（从 table-driven-form.js 合并） ──────────

  function field_key_from_target(target) {
    var field = target.closest('.tdf-field');
    return field ? field.getAttribute('data-key') : '';
  }

  class TableDrivenForm {
    constructor(container, options) {
      this.container = typeof container === 'string' ? document.querySelector(container) : container;
      this.options = options || {};
      this._data = {};
      this._layout = null;
      this._onChange = options.onChange || null;
      this._onValidate = options.onValidate || null;
    }

    render(layout, data) {
      this._layout = layout;
      this._data = data || {};
      if (!this.container) return;
      var html = '';
      var sections = layout.sections || [];
      for (var i = 0; i < sections.length; i++) {
        html += this._renderSection(sections[i]);
      }
      this.container.innerHTML = html;
      this._bindEvents();
    }

    _renderSection(section) {
      var html = '<div class="tdf-section" data-title="' + _escHtml(section.title || '') + '">';
      html += '<div class="tdf-section-header">' + _escHtml(section.title || '') + '</div>';
      html += '<div class="tdf-section-body">';
      var fields = section.fields || [];
      for (var i = 0; i < fields.length; i++) {
        html += this._renderField(fields[i]);
      }
      html += '</div></div>';
      return html;
    }

    _renderField(fieldConfig) {
      var renderer = ComponentRegistry.get(fieldConfig.comp);
      if (!renderer) return '<div class="tdf-field tdf-unknown">未知组件: ' + fieldConfig.comp + '</div>';
      return renderer(fieldConfig, this._data);
    }

    _bindEvents() {
      var self = this;
      this.container.addEventListener('change', function (e) {
        var target = e.target;
        var path = target.getAttribute('data-path');
        if (!path) return;
        var value = self._getInputValue(target);
        DataBinder.set(self._data, path, value);
        if (self._onChange) self._onChange(field_key_from_target(target), value, path, self._data);
      });
    }

    _getInputValue(el) {
      if (el.type === 'checkbox') return el.checked;
      if (el.type === 'number') return parseFloat(el.value) || 0;
      return el.value;
    }

    getValue() {
      return JSON.parse(JSON.stringify(this._data));
    }

    validate() {
      var errors = [];
      if (!this._layout) return errors;
      var sections = this._layout.sections || [];
      for (var i = 0; i < sections.length; i++) {
        var fields = sections[i].fields || [];
        for (var j = 0; j < fields.length; j++) {
          var f = fields[j];
          var val = DataBinder.get(this._data, f.data_path);
          var fieldErrors = ValidationEngine.validate(f, val, this._data);
          if (fieldErrors.length) {
            errors.push({ key: f.key, errors: fieldErrors });
          }
        }
      }
      return errors;
    }
  }

  // ─── 导出 ────────────────────────────────────────────────────

  global.TableDrivenPanel = TableDrivenPanel;
  global.TableDrivenForm = TableDrivenForm;
  global.ComponentRegistry = ComponentRegistry;
  global.DataBinder = DataBinder;
  global.ValidationEngine = ValidationEngine;

})(window);

// ============================================================================
// ===== 来源: highlight-manager.js =====
// ============================================================================
class HighlightManager {
  constructor(canvas) {
    this.canvas = canvas;
    this.activeHighlights = new Map();
    this.ws = null;
    this.pollingInterval = null;
    this.lastEventTime = null;
    this.autoHidePaused = false;
    this.isReplayMode = false;
    this._animationFrameId = null;
    this._config = null;
    this._configLoading = null;
    this._fallbackTimer = null;
  }

  init() {
    if (!this._config && !this._configLoading) {
      this._configLoading = this._loadConfig();
    }

    this.connectWebSocket();

    if (this._fallbackTimer) clearTimeout(this._fallbackTimer);
    this._fallbackTimer = setTimeout(() => {
      this._fallbackTimer = null;
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        console.warn('[HighlightManager] WebSocket连接失败,降级为轮询模式');
        this.startPolling();
      }
    }, 3000);
  }

  async _loadConfig() {
    try {
      var [hlRes, defRes] = await Promise.all([
        fetch('/api/config/tables/highlight_rules').then(function (r) { return r.json(); }),
        fetch('/api/config/tables/defaults').then(function (r) { return r.json(); })
      ]);
      this._config = {
        highlightRules: hlRes || {},
        defaults: defRes || {}
      };
    } catch (e) {
      console.warn('[HighlightManager] 加载配置表失败,使用默认值:', e);
      this._config = { highlightRules: {}, defaults: {} };
    }
  }

  _getCellDuration() {
    var rules = this._config && this._config.highlightRules;
    var cell = rules && rules.rules && rules.rules.cell;
    if (cell && cell.default && cell.default.duration_ms) {
      return cell.default.duration_ms.runtime;
    }
    return 2000;
  }

  _getOtherDuration() {
    var rules = this._config && this._config.highlightRules;
    var flow = rules && rules.rules && rules.rules.flow;
    if (flow && flow.default && flow.default.duration_ms) {
      return flow.default.duration_ms.runtime;
    }
    return 1500;
  }

  _getReplayDuration() {
    var rules = this._config && this._config.highlightRules;
    var cell = rules && rules.rules && rules.rules.cell;
    if (cell && cell.default && cell.default.duration_ms) {
      return cell.default.duration_ms.replay;
    }
    return 4000;
  }

  _getPollingInterval() {
    var rules = this._config && this._config.highlightRules;
    if (rules && rules.polling_interval_ms) {
      return Math.max(1000, rules.polling_interval_ms);
    }
    return 1500;
  }

  _getWsUrl() {
    var defaults = this._config && this._config.defaults;
    var ws = defaults && defaults.highlight && defaults.highlight.ws;
    var scheme = (ws && ws.scheme) || 'ws';
    var path = (ws && ws.path) || '/ws/highlight';
    return scheme + '://' + window.location.host + path;
  }

  connectWebSocket() {
    try {
      var wsUrl = this._getWsUrl();
      this.ws = new WebSocket(wsUrl);

      this.ws.onmessage = (event) => {
        try {
          var data = JSON.parse(event.data);
          this.handleHighlightEvent(data);
        } catch (e) {
          console.error('[HighlightManager] 解析事件失败:', e);
        }
      };

      this.ws.onopen = () => {
        this.ws.send(JSON.stringify({ type: 'subscribe_highlight' }));
      };

      this.ws.onerror = (error) => {
        console.warn('[HighlightManager] WebSocket错误:', error);
      };

      this.ws.onclose = () => {
        if (!this.pollingInterval) {
          this.startPolling();
        }
      };
    } catch (e) {
      console.warn('[HighlightManager] WebSocket不可用:', e);
    }
  }

  startPolling() {
    if (this.pollingInterval) return;

    this.pollingInterval = setInterval(async () => {
      try {
        var url = '/api/highlight-events?since=' + (this.lastEventTime || '') + '&limit=50';
        var response = await fetch(url);
        var result = await response.json();

        if (result.code === 0 && result.events && result.events.length > 0) {
          result.events.forEach(event => this.handleHighlightEvent(event));
          this.lastEventTime = result.events[result.events.length - 1].timestamp;
        }
      } catch (error) {
        console.error('[HighlightManager] 轮询高亮事件失败:', error);
      }
    }, this._getPollingInterval());
  }

  stopPolling() {
    if (this.pollingInterval) {
      clearInterval(this.pollingInterval);
      this.pollingInterval = null;
    }
  }

  handleHighlightEvent(event) {
    if (!event || event.type !== 'highlight') return;

    var target_type = event.target_type;
    var target_id = event.target_id;
    var action = event.action;
    var duration_ms = event.duration_ms || (target_type === 'cell' ? this._getCellDuration() : this._getOtherDuration());

    if (action === 'start') {
      this.startHighlight(target_type, target_id, duration_ms);
    } else if (action === 'stop') {
      this.stopHighlight(target_type, target_id);
    }
  }

  startHighlight(targetType, targetId, durationMs) {
    if (!targetType || !targetId) return;

    if (this.canvas) {
      if (targetType === 'cell') {
        this.canvas.highlightNode(targetId);
      } else if (targetType === 'flow') {
        this.canvas.highlightEdge(targetId);
      }
    }

    var existingTimer = this.activeHighlights.get(targetId);
    if (existingTimer) {
      clearTimeout(existingTimer.timer);
    }

    if (!this.autoHidePaused) {
      var timer = setTimeout(() => {
        this.stopHighlight(targetType, targetId);
      }, durationMs);

      this.activeHighlights.set(targetId, {
        timer: timer,
        targetType: targetType,
        startTime: Date.now(),
        durationMs: durationMs
      });
    } else {
      this.activeHighlights.set(targetId, {
        timer: null,
        targetType: targetType,
        startTime: Date.now(),
        durationMs: durationMs
      });
    }
  }

  stopHighlight(targetType, targetId) {
    if (!targetId) return;

    var highlight = this.activeHighlights.get(targetId);
    if (!highlight) return;

    if (this.canvas) {
      if (targetType === 'cell') {
        this.canvas.unhighlightNode(targetId);
      } else if (targetType === 'flow') {
        this.canvas.unhighlightEdge(targetId);
      }
    }

    if (highlight.timer) {
      clearTimeout(highlight.timer);
    }

    this.activeHighlights.delete(targetId);
  }

  extendAllActive(extraDurationMs) {
    this.activeHighlights.forEach((highlight, id) => {
      if (highlight.timer) {
        clearTimeout(highlight.timer);
      }

      if (!this.autoHidePaused) {
        highlight.timer = setTimeout(() => {
          this.stopHighlight(highlight.targetType, id);
        }, extraDurationMs);
      }
    });
  }

  pauseAutoHide() {
    this.autoHidePaused = true;
    this.activeHighlights.forEach((highlight) => {
      if (highlight.timer) {
        clearTimeout(highlight.timer);
        highlight.timer = null;
      }
    });
  }

  resumeAutoHide() {
    this.autoHidePaused = false;
    var self = this;
    var defaultDuration = this.isReplayMode ? this._getReplayDuration() : this._getCellDuration();

    this.activeHighlights.forEach((highlight, id) => {
      var remaining = highlight.duration_ms - (Date.now() - highlight.startTime);
      if (remaining <= 0) {
        remaining = defaultDuration;
      }

      highlight.timer = setTimeout(function() {
        self.stopHighlight(highlight.targetType, id);
      }, remaining);
    });
  }

  setReplayMode(enabled) {
    this.isReplayMode = enabled;
  }

  destroy() {
    this.activeHighlights.forEach((_, id) => {
      var highlight = this.activeHighlights.get(id);
      if (highlight) {
        this.stopHighlight(highlight.targetType, id);
      }
    });

    this.stopPolling();

    if (this._fallbackTimer) {
      clearTimeout(this._fallbackTimer);
      this._fallbackTimer = null;
    }

    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      this.ws.onopen = null;
      try { this.ws.close(); } catch (e) {}
      this.ws = null;
    }

    if (this._animationFrameId) {
      cancelAnimationFrame(this._animationFrameId);
      this._animationFrameId = null;
    }
  }
}

// ============================================================================
// ===== ES Module 导出 =====
// ============================================================================

window.HighlightManager = (typeof HighlightManager !== 'undefined') ? HighlightManager : null;
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { HighlightManager };
}
  // === from toolbar-renderer.js ===

/**
 * toolbar-renderer.js — 工具栏表驱动渲染器
 * ============================================================
 * 读取 config/toolbar_config.json 和 config/ui_state.json，
 * 动态渲染工具栏按钮、下拉菜单、分组分隔符。
 *
 * 核心函数：
 *   - renderToolbar(container, config, state)  渲染工具栏按钮
 *   - evaluateCondition(expr, state)           表驱动条件求值
 *   - updateButtonStates(state)                按表更新按钮启用/禁用
 *
 * 设计原则：
 *   1. 零硬编码：按钮结构由配置表驱动，不写 if (id === 'btnXxx') 分支
 *   2. 条件求值：enabled_when 表达式由 ui_state.json 的 conditions 表解释
 *   3. 分组渲染：按 group 字段分组，组间插入分隔符
 *   4. 下拉菜单：dropdown + dropdown_items 自动渲染
 *
 * 全局导出：window.ToolbarRenderer
 */

(function (global) {
  'use strict';

  // ─── 内部状态 ─────────────────────────────────────────────────
  var _toolbarConfig = null;   // toolbar_config.json
  var _uiStateConfig = null;   // ui_state.json
  var _configLoading = null;   // 加载 Promise
  var _renderedButtons = [];   // 已渲染按钮清单（用于校验）

  // ─── 配置加载 ─────────────────────────────────────────────────

  function loadConfigs() {
    if (_configLoading) return _configLoading;
    _configLoading = Promise.all([
      fetch('/api/config/tables/toolbar_config').then(function (r) { return r.json(); }),
      fetch('/api/config/tables/ui_state').then(function (r) { return r.json(); })
    ]).then(function (results) {
      _toolbarConfig = results[0] || { groups: [], buttons: [] };
      _uiStateConfig = results[1] || { conditions: {}, buttons: {}, panels: {} };
      return { toolbar: _toolbarConfig, uiState: _uiStateConfig };
    }).catch(function (err) {
      console.warn('[ToolbarRenderer] 配置加载失败,使用空配置:', err);
      _toolbarConfig = { groups: [], buttons: [] };
      _uiStateConfig = { conditions: {}, buttons: {}, panels: {} };
      return { toolbar: _toolbarConfig, uiState: _uiStateConfig };
    });
    return _configLoading;
  }

  // ─── 条件表达式求值 ───────────────────────────────────────────
  // 表驱动：按 ui_state.json 的 conditions 表求值，不硬编码 if 分支
  //
  // 支持的语法：
  //   - 命名条件：is_edit_mode, pool_loaded, can_undo, always, ...
  //   - 逻辑组合：A && B, A || B, !A, A && (B || C)
  //   - 内联表达式：state.mode == 'edit', state.pool_loaded == true
  //   - 常量：true, false

  function evaluateCondition(expr, state, conditions) {
    if (!expr) return true;
    state = state || {};
    conditions = conditions || (_uiStateConfig && _uiStateConfig.conditions) || {};

    // 常量快捷方式
    if (expr === 'always' || expr === 'true') return true;
    if (expr === 'never' || expr === 'false') return false;

    // 递归求值：按 || 分割，再按 && 分割，最后处理 ! 前缀
    return _evalOr(expr.trim(), state, conditions);
  }

  function _evalOr(expr, state, conditions) {
    var parts = _splitTopLevel(expr, '||');
    for (var i = 0; i < parts.length; i++) {
      if (_evalAnd(parts[i].trim(), state, conditions)) return true;
    }
    return false;
  }

  function _evalAnd(expr, state, conditions) {
    var parts = _splitTopLevel(expr, '&&');
    for (var i = 0; i < parts.length; i++) {
      if (!_evalNot(parts[i].trim(), state, conditions)) return false;
    }
    return true;
  }

  function _evalNot(expr, state, conditions) {
    expr = expr.trim();
    // 处理括号
    if (expr.charAt(0) === '(' && expr.charAt(expr.length - 1) === ')') {
      return _evalOr(expr.slice(1, -1), state, conditions);
    }
    // 处理取反
    if (expr.charAt(0) === '!') {
      return !_evalNot(expr.slice(1), state, conditions);
    }
    return _evalLeaf(expr, state, conditions);
  }

  function _evalLeaf(expr, state, conditions) {
    expr = expr.trim();
    if (!expr) return true;

    // 命名条件：查 ui_state.json conditions 表
    if (conditions[expr]) {
      return _evalStateExpr(conditions[expr], state);
    }

    // 内联状态表达式：state.xxx == 'yyy' / state.xxx == true / state.xxx != null
    return _evalStateExpr(expr, state);
  }

  // 求值 state 表达式：支持 ==, !=, >=, <=, >, <
  function _evalStateExpr(expr, state) {
    expr = expr.trim();

    // state.xxx 形式
    var stateMatch = expr.match(/^state\.(\w+)\s*(==|!=|>=|<=|>|<)\s*(.+)$/);
    if (stateMatch) {
      var key = stateMatch[1];
      var op = stateMatch[2];
      var raw = stateMatch[3].trim();
      var actual = state[key];
      var expected = _parseLiteral(raw);
      return _compare(actual, op, expected);
    }

    // 裸变量名（无 state. 前缀，如 mode == 'edit'）
    var bareMatch = expr.match(/^(\w+)\s*(==|!=|>=|<=|>|<)\s*(.+)$/);
    if (bareMatch) {
      var bareKey = bareMatch[1];
      var bareOp = bareMatch[2];
      var bareRaw = bareMatch[3].trim();
      var bareActual = state[bareKey];
      var bareExpected = _parseLiteral(bareRaw);
      return _compare(bareActual, bareOp, bareExpected);
    }

    // 纯命名条件引用（如 pool_loaded, can_undo）
    if (expr === 'true') return true;
    if (expr === 'false') return false;
    // 当作布尔变量：state[expr]
    var val = state[expr];
    return val === true || val === 1;
  }

  function _parseLiteral(raw) {
    raw = raw.trim();
    // 字符串字面量
    if ((raw.charAt(0) === '\'' && raw.charAt(raw.length - 1) === '\'') ||
        (raw.charAt(0) === '"' && raw.charAt(raw.length - 1) === '"')) {
      return raw.slice(1, -1);
    }
    // 布尔/数字/null
    if (raw === 'true') return true;
    if (raw === 'false') return false;
    if (raw === 'null') return null;
    if (raw === 'undefined') return undefined;
    var num = Number(raw);
    if (!isNaN(num)) return num;
    return raw;
  }

  function _compare(actual, op, expected) {
    switch (op) {
      case '==': return actual == expected;  // eslint-disable-line eqeqeq
      case '!=': return actual != expected;  // eslint-disable-line eqeqeq
      case '>=': return actual >= expected;
      case '<=': return actual <= expected;
      case '>':  return actual > expected;
      case '<':  return actual < expected;
    }
    return false;
  }

  // 按顶层运算符分割（忽略括号内的）
  function _splitTopLevel(expr, op) {
    var parts = [];
    var depth = 0;
    var current = '';
    var i = 0;
    while (i < expr.length) {
      var ch = expr.charAt(i);
      if (ch === '(') depth++;
      else if (ch === ')') depth--;
      if (depth === 0 && i + op.length <= expr.length && expr.substr(i, op.length) === op) {
        parts.push(current);
        current = '';
        i += op.length;
        continue;
      }
      current += ch;
      i++;
    }
    parts.push(current);
    return parts;
  }

  // ─── 工具栏渲染 ───────────────────────────────────────────────

  /**
   * 渲染工具栏按钮到容器
   * @param {HTMLElement} container - 按钮容器
   * @param {Object} config - toolbar_config.json
   * @param {Object} state - 当前 UI 状态
   */
  function renderToolbar(container, config, state) {
    if (!container || !config || !config.buttons) return;
    state = state || {};
    container.innerHTML = '';

    var groups = (config.groups || []).slice().sort(function (a, b) {
      return (a.order || 0) - (b.order || 0);
    });
    var buttons = config.buttons.slice().sort(function (a, b) {
      return (a.order || 0) - (b.order || 0);
    });

    _renderedButtons = [];
    var firstGroup = true;

    groups.forEach(function (group) {
      // 组间分隔符
      if (!firstGroup) {
        var sep = document.createElement('span');
        sep.className = 'tb-sep';
        container.appendChild(sep);
      }
      firstGroup = false;

      // 渲染该组的按钮
      buttons.filter(function (btn) { return btn.group === group.group_id; })
        .forEach(function (btn) {
          var el = _renderButton(btn, state, false);
          if (el) {
            container.appendChild(el);
            _renderedButtons.push(btn.id);
          }
        });
    });
  }

  /**
   * 渲染单个按钮（或下拉按钮）
   * @param {Object} btn - 按钮配置
   * @param {Object} state - 当前状态
   * @param {boolean} isOverflow - 是否溢出菜单项
   */
  function _renderButton(btn, state, isOverflow) {
    var suffix = isOverflow ? 'Overflow' : '';
    var id = btn.id + suffix;

    // 链接型按钮（如配置中心）
    if (btn.is_link && btn.href) {
      var link = document.createElement('a');
      link.href = btn.href;
      link.className = 'tb-btn';
      link.id = id;
      link.title = btn.label + (btn.shortcut ? ' (' + btn.shortcut + ')' : '');
      link.style.textDecoration = 'none';
      link.textContent = (btn.icon || '') + ' ' + (btn.label || '');
      return link;
    }

    // 下拉型按钮
    if (btn.dropdown && btn.dropdown_items && btn.dropdown_items.length) {
      var wrapper = document.createElement('div');
      wrapper.className = 'tb-dropdown-wrapper';

      var dropBtn = document.createElement('button');
      dropBtn.className = 'tb-btn tb-btn-dropdown';
      dropBtn.id = id;
      dropBtn.title = btn.label + (btn.shortcut ? ' (' + btn.shortcut + ')' : '');
      dropBtn.innerHTML = (btn.icon || '') + ' ' + (btn.label || '') +
        ' <span class="tb-dropdown-arrow">▼</span>';
      wrapper.appendChild(dropBtn);

      var menuId = _dropdownId(btn.id, isOverflow);
      var menu = document.createElement('div');
      menu.className = 'tb-dropdown-menu hidden';
      menu.id = menuId;
      btn.dropdown_items.forEach(function (item) {
        var itemEl = document.createElement('div');
        itemEl.className = 'tb-dropdown-item';
        // data-* 属性
        if (item.data_node_type) itemEl.setAttribute('data-node-type', item.data_node_type);
        if (item.data_format) itemEl.setAttribute('data-format', item.data_format);
        if (item.action) itemEl.setAttribute('data-action', item.action);
        itemEl.textContent = (item.icon || '') + ' ' + (item.label || '');
        menu.appendChild(itemEl);
      });
      wrapper.appendChild(menu);
      return wrapper;
    }

    // 普通按钮
    var button = document.createElement('button');
    button.className = 'tb-btn';
    button.id = id;
    button.title = btn.label + (btn.shortcut ? ' (' + btn.shortcut + ')' : '');
    button.textContent = (btn.icon || '') + ' ' + (btn.label || '');

    // 表驱动 enabled_when 求值
    if (btn.enabled_when) {
      var uiStateBtn = _uiStateConfig && _uiStateConfig.buttons && _uiStateConfig.buttons[btn.id];
      var condExpr = (uiStateBtn && uiStateBtn.enabled) || btn.enabled_when;
      button.disabled = !evaluateCondition(condExpr, state);
    }

    return button;
  }

  // 生成下拉菜单 ID（与 main.js 期望一致）
  // btnAddNode → addNodeDropdown / addNodeDropdownOverflow
  function _dropdownId(btnId, isOverflow) {
    var name = btnId.replace(/^btn/, '');
    name = name.charAt(0).toLowerCase() + name.slice(1);
    return name + 'Dropdown' + (isOverflow ? 'Overflow' : '');
  }

  /**
   * 渲染溢出菜单（移动端镜像）
   * @param {HTMLElement} container - overflowMenu 容器
   * @param {Object} config - toolbar_config.json
   */
  function renderOverflowMenu(container, config) {
    if (!container || !config || !config.buttons) return;
    container.innerHTML = '';

    // 溢出菜单只渲染部分按钮（与原 HTML 一致：新建/添加/导入/导出/适应/顺序/连线/列表/规则/综合设置/撤销/重做）
    var overflowIds = [
      'btnNew', 'btnAddNode', 'btnImport', 'btnExport',
      'btnFit', 'btnExecOrder', 'btnFlowMode', 'btnTdxPools',
      'btnRuleEditor', 'btnComprehensiveSettings', 'btnUndo', 'btnRedo'
    ];

    overflowIds.forEach(function (bid) {
      var btn = config.buttons.find(function (b) { return b.id === bid; });
      if (!btn) return;
      var el = _renderButton(btn, {}, true);
      if (el) container.appendChild(el);
    });
  }

  // ─── 按钮状态更新 ─────────────────────────────────────────────

  /**
   * 按表更新所有按钮的 enabled/disabled 状态
   * @param {Object} state - 当前 UI 状态
   */
  function updateButtonStates(state) {
    if (!_toolbarConfig || !_toolbarConfig.buttons) return;
    state = state || {};
    var conditions = (_uiStateConfig && _uiStateConfig.conditions) || {};

    _toolbarConfig.buttons.forEach(function (btn) {
      var el = document.getElementById(btn.id);
      if (!el) return;

      // 优先使用 ui_state.json 的 buttons 表
      var uiStateBtn = _uiStateConfig && _uiStateConfig.buttons && _uiStateConfig.buttons[btn.id];
      var condExpr = (uiStateBtn && uiStateBtn.enabled) || btn.enabled_when;
      if (condExpr) {
        el.disabled = !evaluateCondition(condExpr, state, conditions);
      }

      // 同步溢出菜单按钮
      var overflowEl = document.getElementById(btn.id + 'Overflow');
      if (overflowEl) {
        overflowEl.disabled = el.disabled;
      }
    });
  }

  // ─── 面板显隐更新 ─────────────────────────────────────────────

  /**
   * 按表更新面板显隐
   * @param {Object} state - 当前 UI 状态
   */
  function updatePanelVisibility(state) {
    if (!_uiStateConfig || !_uiStateConfig.panels) return;
    state = state || {};
    var conditions = _uiStateConfig.conditions || {};

    Object.keys(_uiStateConfig.panels).forEach(function (panelId) {
      var rule = _uiStateConfig.panels[panelId];
      var el = document.getElementById(panelId);
      if (!el || !rule.visible) return;
      var visible = evaluateCondition(rule.visible, state, conditions);
      if (visible) {
        el.classList.remove('hidden');
      } else {
        el.classList.add('hidden');
      }
    });
  }

  // ─── 校验 ─────────────────────────────────────────────────────

  /**
   * 获取已加载的配置（供外部使用）
   */
  function getConfigs() {
    return { toolbar: _toolbarConfig, uiState: _uiStateConfig };
  }

  // ─── 初始化 ───────────────────────────────────────────────────

  /**
   * 初始化：加载配置并渲染工具栏
   * @param {Object} opts - {toolbarContainer, overflowContainer, state}
   */
  function init(opts) {
    opts = opts || {};
    var toolbarContainer = opts.toolbarContainer || document.getElementById('toolbarButtons');
    var overflowContainer = opts.overflowContainer || document.getElementById('overflowMenu');
    var state = opts.state || {};

    return loadConfigs().then(function () {
      if (toolbarContainer) {
        renderToolbar(toolbarContainer, _toolbarConfig, state);
      }
      if (overflowContainer) {
        renderOverflowMenu(overflowContainer, _toolbarConfig);
      }
      updateButtonStates(state);
      return { toolbar: _toolbarConfig, uiState: _uiStateConfig };
    });
  }

  // ─── 导出 ─────────────────────────────────────────────────────

  global.ToolbarRenderer = {
    init: init,
    renderToolbar: renderToolbar,
    renderOverflowMenu: renderOverflowMenu,
    evaluateCondition: evaluateCondition,
    updateButtonStates: updateButtonStates,
    updatePanelVisibility: updatePanelVisibility,
    getConfigs: getConfigs,
    loadConfigs: loadConfigs
  };

})(typeof window !== 'undefined' ? window : this);

// === K线面板 (保留原有功能) ===
(function () {
  const $ = id => document.getElementById(id);
  const chartPanel = $('chartPanel');

  if ($('btnCloseChart')) {
    $('btnCloseChart').addEventListener('click', function () {
      if (chartPanel) chartPanel.style.display = 'none';
    });
  }

  async function fetchBars(code, period) {
    const sid = window.sessionId;
    if (!sid) { console.warn('请先启动仿真'); return; }
    if (!chartPanel) return;
    try {
      const res = await fetch('/api/sim/bars?session_id=' + sid + '&code=' + encodeURIComponent(code) + '&period=' + period);
      const data = await res.json();
      if (data.code === 0 && data.data) {
        const bars = data.data.bars || [];
        drawMiniChart(bars, code);
        const fr = $('formulaResult');
        if (fr) {
          fr.textContent = data.data.formula_result || '';
          if (data.data.position) fr.textContent += ' | Pos: ' + JSON.stringify(data.data.position);
        }
      }
    } catch (e) { /* ignore */ }
  }

  function drawMiniChart(bars, code) {
    const cvs = $('miniChart');
    if (!cvs) return;
    const ctx2 = cvs.getContext('2d');
    const W = cvs.width, H = cvs.height;
    ctx2.clearRect(0, 0, W, H);
    if (!bars || bars.length === 0) {
      ctx2.fillStyle = '#555'; ctx2.font = '12px Consolas';
      ctx2.fillText('No data: ' + code, 20, H / 2);
      return;
    }
    const padding = { top: 10, right: 10, bottom: 20, left: 50 };
    const chartW = W - padding.left - padding.right;
    const chartH = H - padding.top - padding.bottom;
    const closes = bars.map(b => b.close || b.c || 0);
    const highs = bars.map(b => b.high || b.h || 0);
    const lows = bars.map(b => b.low || b.l || 0);
    const opens = bars.map(b => b.open || b.o || 0);
    const minP = Math.min.apply(null, lows) * 0.998;
    const maxP = Math.max.apply(null, highs) * 1.002;
    const range = maxP - minP || 1;
    const barW = chartW / bars.length;
    const y = p => padding.top + chartH * (1 - (p - minP) / range);
    ctx2.strokeStyle = '#1a1a2e'; ctx2.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
      const p = minP + (range * i / 4);
      const yy = y(p);
      ctx2.beginPath(); ctx2.moveTo(padding.left, yy); ctx2.lineTo(W - padding.right, yy); ctx2.stroke();
      ctx2.fillStyle = '#555'; ctx2.font = '9px Consolas';
      ctx2.fillText(p.toFixed(2), 2, yy + 3);
    }
    for (let bi = 0; bi < bars.length; bi++) {
      const x = padding.left + bi * barW + barW / 2;
      const o = opens[bi], c = closes[bi], h = highs[bi], l = lows[bi];
      const isUp = c >= o;
      const color = isUp ? '#26a69a' : '#ef5350';
      ctx2.strokeStyle = color; ctx2.lineWidth = 1;
      ctx2.beginPath(); ctx2.moveTo(x, y(h)); ctx2.lineTo(x, y(l)); ctx2.stroke();
      const bodyTop = y(Math.max(o, c));
      const bodyBot = y(Math.min(o, c));
      const bodyH = Math.max(bodyBot - bodyTop, 1);
      ctx2.fillStyle = color;
      ctx2.fillRect(x - barW * 0.35, bodyTop, barW * 0.7, bodyH);
    }
    ctx2.fillStyle = '#aaa'; ctx2.font = '11px Consolas';
    ctx2.fillText(code + ' | ' + (closes[closes.length - 1] ? closes[closes.length - 1].toFixed(2) : '-'), padding.left + 5, H - 4);
  }

  function openChartForCode(code) {
    if (!code || !chartPanel) return;
    chartPanel.style.display = '';
    const sel = $('chartCodeSelect');
    if (sel) {
      let found = false;
      for (let i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === code) { found = true; break; }
      }
      if (!found) {
        const opt = document.createElement('option');
        opt.value = code;
        opt.textContent = code;
        sel.appendChild(opt);
      }
      sel.value = code;
    }
    fetchBars(code, ($('chartPeriodSelect') && $('chartPeriodSelect').value) || '1min');
  }
})();

  // === from formula-manager.js ===

/**
 * 公式管理器 - 前端交互逻辑
 * 负责公式列表展示、编辑、测试等功能的实现
 */

// 全局状态
let currentFormulaId = null;
let formulaList = [];
let isEditing = false;

// API 基础路径
const API_BASE = '/api/formula';

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    loadFormulaList();
});

/**
 * 初始化事件监听器
 */
function initEventListeners() {
    // 顶部按钮
    document.getElementById('btn-health').addEventListener('click', checkEngineHealth);
    document.getElementById('btn-refresh').addEventListener('click', loadFormulaList);
    
    // 左侧面板按钮
    document.getElementById('btn-create').addEventListener('click', createNewFormula);
    
    // 右侧编辑器按钮
    document.getElementById('btn-save').addEventListener('click', saveFormula);
    document.getElementById('btn-test').addEventListener('click', openTestDialog);
    document.getElementById('btn-delete').addEventListener('click', deleteFormula);
    
    // 参数管理
    document.getElementById('btn-add-arg').addEventListener('click', addParameter);
    
    // 测试对话框
    document.getElementById('btn-close-test').addEventListener('click', closeTestResult);
    
    // 搜索和过滤
    document.getElementById('search-input').addEventListener('input', filterFormulas);
    document.getElementById('filter-category').addEventListener('change', filterFormulas);
    
    // 表单变更检测
    document.getElementById('formula-form').addEventListener('input', () => {
        isEditing = true;
    });
}

/**
 * 检查引擎健康状态
 */
async function checkEngineHealth() {
    try {
        const response = await fetch(`${API_BASE}/health`);
        const data = await response.json();
        
        if (data.status === 'ready') {
            alert(`引擎状态: 就绪\n版本: ${data.version}`);
        } else {
            alert(`引擎状态: 不可用\n错误: ${data.error || '未知错误'}`);
        }
    } catch (error) {
        alert(`检查引擎状态失败: ${error.message}`);
    }
}

/**
 * 加载公式列表
 */
async function loadFormulaList() {
    try {
        const response = await fetch(`${API_BASE}/list`);
        const data = await response.json();
        
        if (data.success) {
            formulaList = data.data || [];
            renderFormulaList(formulaList);
        } else {
            alert(`加载公式列表失败: ${data.error}`);
        }
    } catch (error) {
        alert(`加载公式列表失败: ${error.message}`);
    }
}

/**
 * 渲染公式列表
 */
function renderFormulaList(formulas) {
    const container = document.getElementById('formula-list');
    
    if (formulas.length === 0) {
        container.innerHTML = '<div class="empty-state">暂无公式</div>';
        return;
    }
    
    container.innerHTML = formulas.map(formula => {
        const categoryClass = getCategoryClass(formula.category);
        const categoryName = getCategoryName(formula.category);
        const sourceLabel = formula.source === 'builtin' ? '内置' : '自定义';
        
        return `
            <div class="formula-item" data-id="${formula.id}" onclick="selectFormula('${formula.id}')">
                <div class="formula-item-header">
                    <span class="formula-item-name">${escapeHtml(formula.name)}</span>
                    <span class="formula-item-category ${categoryClass}">${categoryName}</span>
                </div>
                <div class="formula-item-description">${escapeHtml(formula.description || '')}</div>
                <div class="formula-item-source">来源: ${sourceLabel}</div>
            </div>
        `;
    }).join('');
}

/**
 * 过滤公式列表
 */
function filterFormulas() {
    const searchText = document.getElementById('search-input').value.toLowerCase();
    const category = document.getElementById('filter-category').value;
    
    const filtered = formulaList.filter(formula => {
        const matchText = !searchText || 
            formula.name.toLowerCase().includes(searchText) ||
            (formula.description && formula.description.toLowerCase().includes(searchText));
        const matchCategory = !category || formula.category === category;
        return matchText && matchCategory;
    });
    
    renderFormulaList(filtered);
}

/**
 * 选择公式
 */
function selectFormula(formulaId) {
    const formula = formulaList.find(f => f.id === formulaId);
    if (!formula) return;
    
    currentFormulaId = formulaId;
    isEditing = false;
    
    // 更新 UI 选中状态
    document.querySelectorAll('.formula-item').forEach(item => {
        item.classList.toggle('active', item.dataset.id === formulaId);
    });
    
    // 填充表单
    document.getElementById('formula-name').value = formula.name || '';
    document.getElementById('formula-category').value = formula.category || 'indicator';
    document.getElementById('formula-type').value = formula.formula_type || 'indicator';
    document.getElementById('formula-description').value = formula.description || '';
    document.getElementById('formula-script').value = formula.script || '';
    
    // 渲染参数
    renderParameters(formula.args || []);
    
    // 更新编辑器标题
    document.getElementById('editor-title').textContent = `编辑公式: ${formula.name}`;
    
    // 控制按钮状态
    const isBuiltin = formula.source === 'builtin';
    document.getElementById('btn-delete').disabled = isBuiltin;
    document.getElementById('btn-delete').title = isBuiltin ? '内置公式不可删除' : '';
}

/**
 * 创建新公式
 */
function createNewFormula() {
    currentFormulaId = null;
    isEditing = true;
    
    // 清空表单
    document.getElementById('formula-name').value = '';
    document.getElementById('formula-category').value = 'indicator';
    document.getElementById('formula-type').value = 'indicator';
    document.getElementById('formula-description').value = '';
    document.getElementById('formula-script').value = '';
    
    // 清空参数
    renderParameters([]);
    
    // 更新编辑器标题
    document.getElementById('editor-title').textContent = '新建公式';
    
    // 启用所有按钮
    document.getElementById('btn-delete').disabled = true;
    document.getElementById('btn-delete').title = '请先保存公式';
    
    // 清除选中状态
    document.querySelectorAll('.formula-item').forEach(item => {
        item.classList.remove('active');
    });
}

/**
 * 保存公式
 */
async function saveFormula() {
    const name = document.getElementById('formula-name').value.trim();
    const category = document.getElementById('formula-category').value;
    const formula_type = document.getElementById('formula-type').value;
    const description = document.getElementById('formula-description').value.trim();
    const script = document.getElementById('formula-script').value.trim();
    const args = collectParameters();
    
    if (!name) {
        alert('请输入公式名称');
        return;
    }
    
    if (!script) {
        alert('请输入公式脚本');
        return;
    }
    
    const payload = {
        name,
        category,
        formula_type,
        description,
        script,
        args
    };
    
    try {
        let response;
        if (currentFormulaId) {
            // 更新现有公式
            response = await fetch(`${API_BASE}/${currentFormulaId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        } else {
            // 创建新公式
            response = await fetch(`${API_BASE}/create`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        }
        
        const data = await response.json();
        
        if (data.success) {
            alert('保存成功');
            isEditing = false;
            
            // 如果是新建，更新当前 ID
            if (!currentFormulaId && data.data && data.data.id) {
                currentFormulaId = data.data.id;
            }
            
            // 重新加载列表
            await loadFormulaList();
            
            // 重新选中当前公式
            if (currentFormulaId) {
                selectFormula(currentFormulaId);
            }
        } else {
            alert(`保存失败: ${data.error}`);
        }
    } catch (error) {
        alert(`保存失败: ${error.message}`);
    }
}

/**
 * 删除公式
 */
async function deleteFormula() {
    if (!currentFormulaId) {
        alert('请先选择要删除的公式');
        return;
    }
    
    const formula = formulaList.find(f => f.id === currentFormulaId);
    if (formula && formula.source === 'builtin') {
        alert('内置公式不可删除');
        return;
    }
    
    if (!confirm(`确定要删除公式 "${formula?.name}" 吗？`)) {
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE}/${currentFormulaId}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (data.success) {
            alert('删除成功');
            currentFormulaId = null;
            createNewFormula();
            await loadFormulaList();
        } else {
            alert(`删除失败: ${data.error}`);
        }
    } catch (error) {
        alert(`删除失败: ${error.message}`);
    }
}

/**
 * 打开测试对话框
 */
function openTestDialog() {
    if (!document.getElementById('formula-script').value.trim()) {
        alert('请先输入公式脚本');
        return;
    }
    
    const category = document.getElementById('formula-category').value;
    const isXg = category === 'xg';
    
    // 显示/隐藏选股公式的股票列表输入
    document.getElementById('test-xg-stocks').classList.toggle('hidden', !isXg);
    
    document.getElementById('test-dialog').classList.remove('hidden');
}

/**
 * 关闭测试对话框
 */
function closeTestDialog() {
    document.getElementById('test-dialog').classList.add('hidden');
}

/**
 * 执行测试
 */
async function executeTest() {
    const category = document.getElementById('formula-category').value;
    const script = document.getElementById('formula-script').value.trim();
    const period = document.getElementById('test-period').value;
    
    if (!script) {
        alert('请输入公式脚本');
        return;
    }
    
    closeTestDialog();
    
    try {
        let response;
        let resultData;
        
        if (category === 'xg') {
            // 选股公式测试
            const stockListText = document.getElementById('test-stock-list').value.trim();
            const stockList = stockListText.split('\n').map(s => s.trim()).filter(s => s);
            
            if (stockList.length === 0) {
                alert('请输入至少一个股票代码');
                return;
            }
            
            response = await fetch(`${API_BASE}/test-xg`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    script,
                    stock_list: stockList,
                    period
                })
            });
            
            resultData = await response.json();
            renderXgTestResult(resultData);
        } else {
            // 指标公式测试
            const stockCode = document.getElementById('test-stock-code').value.trim() || '000001';

            // 从参数列表 UI 收集参数值，构建 args 对象
            const args = {};
            const argItems = document.querySelectorAll('#formula-args .arg-item');
            argItems.forEach(item => {
                const name = item.querySelector('.arg-name')?.value.trim();
                const valueStr = item.querySelector('.arg-value')?.value.trim();
                if (name && valueStr) {
                    // 尝试转为数字，非数字保留原字符串
                    const num = Number(valueStr);
                    args[name] = isNaN(num) ? valueStr : num;
                }
            });

            response = await fetch(`${API_BASE}/test`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    script,
                    stock_code: stockCode,
                    period,
                    args: args
                })
            });
            
            resultData = await response.json();
            renderIndicatorTestResult(resultData);
        }
        
        // 显示测试结果面板
        document.getElementById('test-result-panel').classList.remove('hidden');
        
    } catch (error) {
        alert(`测试失败: ${error.message}`);
    }
}

/**
 * 格式化指标测试结果值
 * 判断结果是标量（单输出变量）还是对象（多输出变量），返回对应的 HTML 字符串
 * @param {*} value - result[stockCode] 的值，标量（单输出）或对象（多输出）
 * @returns {string} HTML 字符串
 */
function formatTestResult(value) {
    // 多输出变量：对象形式，以表格展示每个输出变量名和值
    if (value !== null && value !== undefined && typeof value === 'object' && !Array.isArray(value)) {
        const entries = Object.entries(value);
        if (entries.length === 0) {
            return '<span class="test-result-value">N/A</span>';
        }
        let html = `
            <table class="test-result-table">
                <tr>
                    <th>输出变量</th>
                    <th>值</th>
                </tr>
        `;
        for (const [varName, varValue] of entries) {
            html += `
                <tr>
                    <td>${escapeHtml(String(varName))}</td>
                    <td class="test-result-value">${varValue !== undefined && varValue !== null ? varValue : 'N/A'}</td>
                </tr>
            `;
        }
        html += '</table>';
        return html;
    }

    // 单输出变量：标量形式
    return `<span class="test-result-value">${value !== undefined && value !== null ? value : 'N/A'}</span>`;
}

/**
 * 渲染指标测试结果
 */
function renderIndicatorTestResult(data) {
    const container = document.getElementById('test-result-content');

    if (!data.success) {
        container.innerHTML = `
            <div class="test-result-item">
                <h4>测试失败</h4>
                <div class="test-result-error">${escapeHtml(data.error || '未知错误')}</div>
            </div>
        `;
        return;
    }

    const result = data.data?.result || {};
    const stockCode = Object.keys(result)[0] || '未知';
    const value = result[stockCode];
    const isMultiOutput = value !== null && value !== undefined
        && typeof value === 'object' && !Array.isArray(value);

    if (isMultiOutput) {
        // 多输出变量：展示股票代码 + 各输出变量的值
        container.innerHTML = `
            <div class="test-result-item">
                <h4>测试结果</h4>
                <div class="test-result-stock">${escapeHtml(stockCode)} 计算结果：</div>
                ${formatTestResult(value)}
            </div>
        `;
    } else {
        // 单输出变量：保持原展示方式（股票代码 + 指标值表格）
        container.innerHTML = `
            <div class="test-result-item">
                <h4>测试结果</h4>
                <table class="test-result-table">
                    <tr>
                        <th>股票代码</th>
                        <th>指标值</th>
                    </tr>
                    <tr>
                        <td>${escapeHtml(stockCode)}</td>
                        <td class="test-result-value">${value !== undefined && value !== null ? value : 'N/A'}</td>
                    </tr>
                </table>
            </div>
        `;
    }
}

/**
 * 渲染选股测试结果
 */
function renderXgTestResult(data) {
    const container = document.getElementById('test-result-content');
    
    if (!data.success) {
        container.innerHTML = `
            <div class="test-result-item">
                <h4>测试失败</h4>
                <div class="test-result-error">${escapeHtml(data.error || '未知错误')}</div>
            </div>
        `;
        return;
    }
    
    const result = data.data?.result || {};
    const selectedCodes = data.data?.selected_codes || [];
    
    let resultHtml = `
        <div class="test-result-item">
            <h4>选股结果</h4>
            <div class="test-result-success">符合条件的股票: ${selectedCodes.length} 只</div>
            <table class="test-result-table">
                <tr>
                    <th>股票代码</th>
                    <th>是否符合</th>
                </tr>
    `;
    
    for (const [code, value] of Object.entries(result)) {
        const isSelected = selectedCodes.includes(code);
        resultHtml += `
            <tr>
                <td>${escapeHtml(code)}</td>
                <td class="${isSelected ? 'test-result-success' : ''}">${isSelected ? '✓ 符合' : '✗ 不符合'}</td>
            </tr>
        `;
    }
    
    resultHtml += '</table></div>';
    container.innerHTML = resultHtml;
}

/**
 * 关闭测试结果
 */
function closeTestResult() {
    document.getElementById('test-result-panel').classList.add('hidden');
}

/**
 * 渲染参数列表
 */
function renderParameters(args) {
    const container = document.getElementById('formula-args');
    
    if (args.length === 0) {
        container.innerHTML = '<div class="empty-state">暂无参数</div>';
        return;
    }
    
    container.innerHTML = args.map((arg, index) => `
        <div class="arg-item" data-index="${index}">
            <input type="text" placeholder="参数名" value="${escapeHtml(arg.name || '')}" class="arg-name">
            <input type="text" placeholder="默认值" value="${escapeHtml(String(arg.value || ''))}" class="arg-value">
            <input type="text" placeholder="描述" value="${escapeHtml(arg.description || '')}" class="arg-description">
            <button type="button" class="btn btn-sm btn-danger" onclick="removeParameter(${index})">删除</button>
        </div>
    `).join('');
}

/**
 * 添加参数
 */
function addParameter() {
    const container = document.getElementById('formula-args');
    const currentArgs = collectParameters();
    
    currentArgs.push({
        name: '',
        value: '',
        description: ''
    });
    
    renderParameters(currentArgs);
}

/**
 * 删除参数
 */
function removeParameter(index) {
    const currentArgs = collectParameters();
    currentArgs.splice(index, 1);
    renderParameters(currentArgs);
}

/**
 * 收集参数
 */
function collectParameters() {
    const argItems = document.querySelectorAll('.arg-item');
    const args = [];
    
    argItems.forEach(item => {
        const name = item.querySelector('.arg-name').value.trim();
        const value = item.querySelector('.arg-value').value.trim();
        const description = item.querySelector('.arg-description').value.trim();
        
        if (name) {
            args.push({
                name,
                value: isNaN(value) ? value : Number(value),
                description
            });
        }
    });
    
    return args;
}

/**
 * 获取分类样式类名
 */
function getCategoryClass(category) {
    const classMap = {
        'indicator': 'category-indicator',
        'xg': 'category-xg',
        'exp': 'category-exp'
    };
    return classMap[category] || 'category-indicator';
}

/**
 * 获取分类显示名称
 */
function getCategoryName(category) {
    const nameMap = {
        'indicator': '指标',
        'xg': '选股',
        'exp': '专家系统'
    };
    return nameMap[category] || '指标';
}

/**
 * HTML 转义
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ===== Event Timeline Implementation =====
const EVENT_TYPE_COLORS = {
    tick:     { color: '#2196f3', emoji: '📊', label: 'Tick' },
    bar:      { color: '#4caf50', emoji: '📈', label: 'Bar' },
    formula:  { color: '#00bcd4', emoji: '🧮', label: 'Formula' },
    edge:     { color: '#ff9800', emoji: '⚡', label: 'Edge' },
    transfer: { color: '#9c27b0', emoji: '🔄', label: 'Transfer' },
    signal:   { color: '#f44336', emoji: '💰', label: 'Signal' },
    order:    { color: '#ffc107', emoji: '📋', label: 'Order' },
    ttl:      { color: '#e91e63', emoji: '⏰', label: 'TTL' },
    system:   { color: '#9e9e9e', emoji: '🔧', label: 'System' }
};

class EventTimeline {
    constructor() {
        this.canvas = $('epTimelineCanvas');
        this.container = $('epTimelineContainer');
        this.tooltip = $('epTimelineTooltip');
        this.ctx = this.canvas.getContext('2d');
        this.events = [];
        this.filteredTypes = new Set(Object.keys(EVENT_TYPE_COLORS));
        this.autoScroll = true;
        this.collapsed = false;
        
        this.viewStart = null;
        this.viewEnd = null;
        this.minTimeRange = 1000;
        this.maxTimeRange = 24 * 60 * 60 * 1000;
        
        this.isDragging = false;
        this.dragStartX = 0;
        this.dragViewStart = 0;
        this.dragViewEnd = 0;
        
        this.hoveredEvent = null;
        this.dpr = window.devicePixelRatio || 1;
        
        this.resize();
        this.bindEvents();
        this.resetView();
        this.render();
    }
    
    resize() {
        const rect = this.container.getBoundingClientRect();
        this.width = rect.width;
        this.height = rect.height;
        this.canvas.width = this.width * this.dpr;
        this.canvas.height = this.height * this.dpr;
        this.canvas.style.width = this.width + 'px';
        this.canvas.style.height = this.height + 'px';
        this.ctx.scale(this.dpr, this.dpr);
    }
    
    bindEvents() {
        window.addEventListener('resize', () => {
            this.resize();
            this.render();
        });
        
        this.canvas.addEventListener('wheel', (e) => {
            e.preventDefault();
            const zoomFactor = e.deltaY > 0 ? 1.2 : 0.8;
            this.zoom(e.offsetX, zoomFactor);
        });
        
        this.canvas.addEventListener('mousedown', (e) => {
            this.isDragging = true;
            this.dragStartX = e.offsetX;
            this.dragViewStart = this.viewStart;
            this.dragViewEnd = this.viewEnd;
            this.canvas.style.cursor = 'grabbing';
        });
        
        window.addEventListener('mousemove', (e) => {
            if (this.isDragging) {
                const rect = this.canvas.getBoundingClientRect();
                const dx = e.clientX - rect.left - this.dragStartX;
                const range = this.dragViewEnd - this.dragViewStart;
                const shift = -(dx / this.width) * range;
                this.viewStart = this.dragViewStart + shift;
                this.viewEnd = this.dragViewEnd + shift;
                this.autoScroll = false;
                this.updateAutoScrollBtn();
                this.render();
            } else {
                const rect = this.canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                if (x >= 0 && x <= this.width && y >= 0 && y <= this.height) {
                    this.checkHover(x, y, e.clientX, e.clientY);
                } else {
                    this.hideTooltip();
                }
            }
        });
        
        window.addEventListener('mouseup', () => {
            this.isDragging = false;
            this.canvas.style.cursor = 'grab';
        });
        
        $('btnTimelineAutoScroll')?.addEventListener('click', () => {
            this.autoScroll = !this.autoScroll;
            this.updateAutoScrollBtn();
            if (this.autoScroll && this.events.length > 0) {
                this.scrollToLatest();
            }
        });
        
        $('btnTimelineZoomIn')?.addEventListener('click', () => {
            this.zoom(this.width * 0.8, 0.7);
        });
        
        $('btnTimelineZoomOut')?.addEventListener('click', () => {
            this.zoom(this.width * 0.8, 1.4);
        });
        
        $('btnTimelineReset')?.addEventListener('click', () => {
            this.resetView();
            this.autoScroll = true;
            this.updateAutoScrollBtn();
            this.render();
        });
        
        $('epTimelineToggle')?.addEventListener('click', (e) => {
            if (e.target.closest('.ep-timeline-btn')) return;
            this.collapsed = !this.collapsed;
            $('epTimelineSection').classList.toggle('collapsed', this.collapsed);
            if (!this.collapsed) {
                setTimeout(() => {
                    this.resize();
                    this.render();
                }, 50);
            }
        });
    }
    
    updateAutoScrollBtn() {
        const btn = $('btnTimelineAutoScroll');
        if (btn) {
            btn.classList.toggle('active', this.autoScroll);
            btn.textContent = this.autoScroll ? '⏸ 跟随' : '▶ 手动';
        }
    }
    
    classifyEventType(evType) {
        const t = (evType || '').toLowerCase();
        if (t.includes('tick') || t.includes('datachanged')) return 'tick';
        if (t.includes('bar')) return 'bar';
        if (t.includes('formula') || t.includes('filtered') || t.includes('crossover')) return 'formula';
        if (t.includes('edge')) return 'edge';
        if (t.includes('transfer') || t.includes('executed')) return 'transfer';
        if (t.includes('signal')) return 'signal';
        if (t.includes('order') || t.includes('position') || t.includes('filled')) return 'order';
        if (t.includes('ttl') || t.includes('timeout') || t.includes('expired')) return 'ttl';
        return 'system';
    }
    
    addEvent(ev) {
        const ts = ev.ts || Date.now();
        const type = this.classifyEventType(ev.type);
        const eventData = ev.event || ev;
        const code = eventData.code || eventData.codes || '';
        let detail = '';
        try {
            if (typeof eventData === 'object') {
                const simple = {};
                for (const k in eventData) {
                    if (typeof eventData[k] !== 'object' && typeof eventData[k] !== 'function') {
                        simple[k] = eventData[k];
                    }
                }
                detail = JSON.stringify(simple).slice(0, 150);
            } else {
                detail = String(eventData).slice(0, 150);
            }
        } catch(e) { detail = ''; }
        
        this.events.push({
            ts: ts,
            type: type,
            typeName: ev.type || type,
            code: code,
            detail: detail,
            raw: ev
        });
        
        if (this.events.length > 2000) {
            this.events = this.events.slice(-1500);
        }
        
        if (this.autoScroll) {
            this.scrollToLatest();
        }
        this.render();
    }
    
    scrollToLatest() {
        if (this.events.length === 0) return;
        const latest = this.events[this.events.length - 1].ts;
        const range = this.viewEnd - this.viewStart;
        this.viewEnd = latest + range * 0.05;
        this.viewStart = this.viewEnd - range;
    }
    
    resetView() {
        const now = Date.now();
        this.viewStart = now - 60000;
        this.viewEnd = now + 5000;
    }
    
    zoom(centerX, factor) {
        const range = this.viewEnd - this.viewStart;
        const newRange = Math.max(this.minTimeRange, Math.min(this.maxTimeRange, range * factor));
        const centerTime = this.viewStart + (centerX / this.width) * range;
        const relPos = centerX / this.width;
        this.viewStart = centerTime - relPos * newRange;
        this.viewEnd = this.viewStart + newRange;
        this.autoScroll = false;
        this.updateAutoScrollBtn();
        this.render();
    }
    
    timeToX(t) {
        const range = this.viewEnd - this.viewStart;
        return ((t - this.viewStart) / range) * this.width;
    }
    
    xToTime(x) {
        const range = this.viewEnd - this.viewStart;
        return this.viewStart + (x / this.width) * range;
    }
    
    formatTime(ts) {
        const d = new Date(ts);
        const pad = (n) => String(n).padStart(2, '0');
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }
    
    formatTimeMs(ts) {
        const d = new Date(ts);
        const pad = (n) => String(n).padStart(2, '0');
        const p3 = (n) => String(n).padStart(3, '0');
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) + '.' + p3(d.getMilliseconds());
    }
    
    getNiceTickInterval(range) {
        const targets = [100, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000, 600000, 900000, 1800000, 3600000];
        const approx = range / 8;
        let best = targets[0];
        for (const t of targets) {
            if (t <= approx) best = t;
        }
        return best;
    }
    
    checkHover(x, y, clientX, clientY) {
        let closest = null;
        let closestDist = Infinity;
        const visibleEvents = this.events.filter(e => this.filteredTypes.has(e.type));
        const centerY = this.height / 2;
        
        for (const ev of visibleEvents) {
            const ex = this.timeToX(ev.ts);
            if (ex < -10 || ex > this.width + 10) continue;
            const dist = Math.sqrt((x - ex) ** 2 + (y - centerY) ** 2);
            if (dist < 12 && dist < closestDist) {
                closest = ev;
                closestDist = dist;
            }
        }
        
        if (closest) {
            this.hoveredEvent = closest;
            this.showTooltip(closest, clientX, clientY);
        } else {
            this.hoveredEvent = null;
            this.hideTooltip();
        }
        this.render();
    }
    
    showTooltip(ev, clientX, clientY) {
        const info = EVENT_TYPE_COLORS[ev.type] || EVENT_TYPE_COLORS.system;
        this.tooltip.innerHTML = `
            <div class="tt-time">${this.formatTimeMs(ev.ts)}</div>
            <div class="tt-type" style="background:${info.color}20;color:${info.color};border:1px solid ${info.color}50;">
                ${info.emoji} ${ev.typeName}
            </div>
            ${ev.code ? '<div class="tt-code">' + escapeHtml(ev.code) + '</div>' : ''}
            <div class="tt-detail">${escapeHtml(ev.detail)}</div>
        `;
        this.tooltip.style.display = 'block';
        
        const rect = this.container.getBoundingClientRect();
        let left = clientX - rect.left + 15;
        let top = clientY - rect.top - 10;
        if (left + 280 > this.width) left = left - 300;
        if (top + 100 > this.height) top = top - 80;
        this.tooltip.style.left = Math.max(5, left) + 'px';
        this.tooltip.style.top = Math.max(5, top) + 'px';
    }
    
    hideTooltip() {
        if (this.hoveredEvent) {
            this.hoveredEvent = null;
            this.render();
        }
        this.tooltip.style.display = 'none';
    }
    
    applyFilter(activeFilters) {
        this.filteredTypes = new Set(activeFilters);
        this.render();
    }
    
    render() {
        if (!this.ctx || this.collapsed) return;
        
        const ctx = this.ctx;
        ctx.clearRect(0, 0, this.width, this.height);
        
        ctx.fillStyle = '#1a1a2e';
        ctx.fillRect(0, 0, this.width, this.height);
        
        const centerY = this.height / 2;
        ctx.strokeStyle = 'rgba(255,255,255,0.15)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, centerY);
        ctx.lineTo(this.width, centerY);
        ctx.stroke();
        
        const interval = this.getNiceTickInterval(this.viewEnd - this.viewStart);
        const firstTick = Math.ceil(this.viewStart / interval) * interval;
        
        ctx.fillStyle = 'rgba(255,255,255,0.4)';
        ctx.font = '10px monospace';
        ctx.textAlign = 'center';
        
        for (let t = firstTick; t <= this.viewEnd; t += interval) {
            const x = this.timeToX(t);
            if (x < -20 || x > this.width + 20) continue;
            
            ctx.strokeStyle = 'rgba(255,255,255,0.1)';
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, this.height);
            ctx.stroke();
            
            ctx.fillStyle = 'rgba(180,180,200,0.6)';
            ctx.fillText(this.formatTime(t), x, this.height - 5);
        }
        
        const visibleEvents = this.events.filter(e => 
            this.filteredTypes.has(e.type) && 
            e.ts >= this.viewStart - 1000 && 
            e.ts <= this.viewEnd + 1000
        );
        
        for (const ev of visibleEvents) {
            const x = this.timeToX(ev.ts);
            if (x < -5 || x > this.width + 5) continue;
            
            const info = EVENT_TYPE_COLORS[ev.type] || EVENT_TYPE_COLORS.system;
            const isHovered = this.hoveredEvent === ev;
            const radius = isHovered ? 7 : 5;
            
            ctx.beginPath();
            ctx.arc(x, centerY, radius + 2, 0, Math.PI * 2);
            ctx.fillStyle = info.color + '40';
            ctx.fill();
            
            ctx.beginPath();
            ctx.arc(x, centerY, radius, 0, Math.PI * 2);
            ctx.fillStyle = info.color;
            ctx.fill();
            
            if (isHovered) {
                ctx.strokeStyle = '#fff';
                ctx.lineWidth = 2;
                ctx.stroke();
            }
        }
        
        const now = Date.now();
        if (now >= this.viewStart && now <= this.viewEnd) {
            const nx = this.timeToX(now);
            ctx.strokeStyle = 'rgba(255,100,100,0.5)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.moveTo(nx, 0);
            ctx.lineTo(nx, this.height);
            ctx.stroke();
            ctx.setLineDash([]);
        }
    }
}

let __eventTimeline = null;

function initEventTimeline() {
    if (__eventTimeline) return;
    const section = $('epTimelineSection');
    if (!section) return;
    __eventTimeline = new EventTimeline();
    
    const filterBtns = document.querySelectorAll('.ep-filter-btn');
    const updateFilters = () => {
        if (!__eventTimeline) return;
        const active = [];
        filterBtns.forEach(btn => {
            if (btn.classList.contains('active')) {
                const f = btn.dataset.filter;
                if (f && f !== 'all') active.push(f);
            }
        });
        if (active.length === 0) {
            filterBtns.forEach(b => b.classList.add('active'));
            updateFilters();
            return;
        }
        __eventTimeline.applyFilter(active);
    };
    
    filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            setTimeout(updateFilters, 0);
        });
    });
}

})();
