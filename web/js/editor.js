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
      return (n.dzh_cell_type === 202 || n.dzh_cell_type === 7) && inEdges[n.id].length === 0;
    });
    if (!source) {
      source = data.nodes.find(function(n) {
        return n.dzh_cell_type === 202 || n.dzh_cell_type === 7;
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
    return n && (n.dzh_cell_type === 202 || n.dzh_cell_type === 200 ||
                 n.dzh_cell_type === 7 || n.dzh_cell_type === 8);
  }
  function isConditionNode(n) {
    return n && n.dzh_cell_type === 201;
  }
  function isStatePoolNode(n) {
    return n && (n.dzh_cell_type === 200 || n.dzh_cell_type === 8);
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
      var fromIsPool = fromNode && (fromNode.dzh_cell_type === 202 || fromNode.dzh_cell_type === 7 ||
                                    fromNode.dzh_cell_type === 200 || fromNode.dzh_cell_type === 8);
      if (!fromNode || !fromIsPool) return;

      // 情形 A: 源池 → 条件节点 (上游边)
      if (toNode && toNode.dzh_cell_type === 201) {
        if (!edge.params || edge.params.begin === undefined) return;
        var outEdge = (outEdges[toId] || []).find(function(e) {
          var outTargetId = e.target ? e.target.node_id : e.to;
          var outNode = nodeMap[outTargetId];
          return outNode && (outNode.dzh_cell_type === 200 || outNode.dzh_cell_type === 8 ||
                             outNode.dzh_cell_type === 202 || outNode.dzh_cell_type === 7);
        });
        if (!outEdge) return;
        var targetId   = outEdge.target ? outEdge.target.node_id : outEdge.to;
        var targetNode = nodeMap[targetId];
        if (!targetNode) return;
        candidates.push({ edgeIndex: edgeIndex, fromPool: fromNode, condNode: toNode, targetPool: targetNode, upstreamEdge: edge });
      }
      // 情形 B: 源池 → 目标池（无条件直连）
      else if (toNode && (toNode.dzh_cell_type === 200 || toNode.dzh_cell_type === 8 ||
                          toNode.dzh_cell_type === 202 || toNode.dzh_cell_type === 7)) {
        candidates.push({ edgeIndex: edgeIndex, fromPool: fromNode, condNode: null, targetPool: toNode, upstreamEdge: edge });
      }
    });

    // BFS 遍历 —— 支持多备选池，每个备选池独立 BFS（独立的 visited）
    // 这样当股票池包含多个不连通的备选池树时，每棵树都能完整显示
    var sourcePools = data.nodes.filter(function(n) {
      return n.dzh_cell_type === 202 || n.dzh_cell_type === 7;
    });

    sourcePools.forEach(function(sourcePool) {
      var visited = {};     // 已访问的池（已输出声明行），每个备选池独立

      // 先输出源池声明
      rows.push({
        type: sourcePool.dzh_cell_type === 202 || sourcePool.dzh_cell_type === 7 ? 'source' : 'pool-decl',
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
    if (node.dzh_cell_type !== 202 && node.dzh_cell_type !== 7) {
      throw new Error('[CS] openCandidatePoolEditor: 节点 ' + nodeId + ' 不是备选池(type=202|7)');
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
    if (node.dzh_cell_type !== 200 && node.dzh_cell_type !== 8) {
      throw new Error('[CS] openStateAttributeEditor: 节点 ' + nodeId + ' 不是状态池(type=200|8)');
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

    // 对象隔离：必须是条件节点 (type=201)
    if (condNode && condNode.dzh_cell_type !== 201) {
      throw new Error('[CS] openConditionEditor: 目标不是条件节点(type=201)');
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
    if (!node || (node.dzh_cell_type !== 202 && node.dzh_cell_type !== 7)) {
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
    if (!node || (node.dzh_cell_type !== 200 && node.dzh_cell_type !== 8)) {
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
        (schemaMissing ? '<span class="cfg-table-warn" title="Schema 未覆盖" style="font-size:11px;color:#f39c12">⚠</span>' : '') +
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
      metaHtml += '<span style="color:#f39c12">🔒 已锁定</span>';
    }
    metaHtml += ' <button class="cfg-lock-btn ' + (locked ? 'unlock' : 'lock') +
      '" onclick="ConfigManager.toggleLock(\'' + escHtml(name) + '\')">' +
      (locked ? '🔓 解锁' : '🔒 加锁') + '</button>';
    document.getElementById('edMeta').innerHTML = metaHtml;

    // Disable save button when locked (Task 14.1)
    var saveBtn = document.getElementById('btnSave');
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
          html += '<div class="cfg-diff-section"><strong style="color:#2ecc71;">新增 (+' + diff.added.length + ')</strong><ul>';
          diff.added.forEach(function (item) { html += '<li style="color:#2ecc71;">+ ' + escHtml(JSON.stringify(item)) + '</li>'; });
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
          html += '<div class="cfg-diff-section"><strong style="color:#f39c12;">修改</strong><ul>';
          Object.keys(diff.modified).forEach(function (field) {
            var change = diff.modified[field];
            html += '<li style="color:#f39c12;">~ ' + escHtml(field) + ': <span style="color:#e74c3c;">' + escHtml(JSON.stringify(change.old)) + '</span> → <span style="color:#2ecc71;">' + escHtml(JSON.stringify(change.new)) + '</span></li>';
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
    var btn = document.getElementById('btnSave');
    btn.style.display = state.modified ? '' : 'none';
    document.getElementById('btnUndo').disabled = state.undoStack.length === 0;
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
      '.cfg-history-op.create { background: rgba(46,204,113,.15); color: #2ecc71; }',
      '.cfg-history-op.update { background: rgba(74,144,217,.15); color: #4a90d9; }',
      '.cfg-history-op.delete { background: rgba(231,76,60,.15); color: #e74c3c; }',
      '.cfg-history-op.rollback { background: rgba(155,89,182,.15); color: #9b59b6; }',
      '.cfg-history-summary { flex: 1; color: #d8d8ec; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
      '.cfg-history-actions { display: flex; gap: 4px; }',
      '.cfg-lock-btn { padding: 2px 8px; border: 1px solid #3a3a6e; border-radius: 3px; background: rgba(255,255,255,.03); color: #9090b0; font-size: 10px; cursor: pointer; white-space: nowrap; }',
      '.cfg-lock-btn:hover { background: rgba(255,255,255,.07); color: #d8d8ec; }',
      '.cfg-lock-btn.lock { color: #f39c12; border-color: rgba(243,156,18,.4); }',
      '.cfg-lock-btn.unlock { color: #2ecc71; border-color: rgba(46,204,113,.4); }'
    ].join('\n');
    document.head.appendChild(style);
  }

  // ═══════════════════════════════════════════════════════════
  //  Init
  // ═══════════════════════════════════════════════════════════

  var listenersAttached = false;

  function init() {
    // Inject dynamic styles for new UI elements
    injectStyles();

    // Set up event listeners only once (idempotent for retry)
    if (!listenersAttached) {
      listenersAttached = true;

      // Search
      document.getElementById('schInput').addEventListener('input', function () {
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
      var jsonTa = document.getElementById('jsonEd');
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
      document.getElementById('btnSave').addEventListener('click', saveTable);
      document.getElementById('btnReload').addEventListener('click', hotReload);
      document.getElementById('btnVal').addEventListener('click', validateAll);
      document.getElementById('btnCopy').addEventListener('click', function () {
        var ta = document.getElementById('jsonEd');
        navigator.clipboard.writeText(ta.value).then(function () {
          toast('已复制到剪贴板', 'success');
        });
      });
      document.getElementById('btnUndo').addEventListener('click', function () {
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
        console.error('[ConfigSync] WebSocket 错误', e);
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
