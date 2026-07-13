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
   * 校验配置表 buttons 数组与渲染后 DOM 按钮数量一致
   * @returns {Object} {expected, rendered, ok}
   */
  function validateButtonCount() {
    var expected = (_toolbarConfig && _toolbarConfig.buttons) ? _toolbarConfig.buttons.length : 0;
    var rendered = _renderedButtons.length;
    return { expected: expected, rendered: rendered, ok: expected === rendered };
  }

  /**
   * 获取已渲染按钮清单
   */
  function getRenderedButtons() {
    return _renderedButtons.slice();
  }

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
    validateButtonCount: validateButtonCount,
    getRenderedButtons: getRenderedButtons,
    getConfigs: getConfigs,
    loadConfigs: loadConfigs
  };

})(typeof window !== 'undefined' ? window : this);
