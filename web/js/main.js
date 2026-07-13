/**
 * main.js — Single-page application controller for MetaCore Stock Pool Platform
 *
 * Orchestrates FlowCanvas, PoolDataManager, TableDrivenPanel, HighlightManager,
 * and all UI interactions (toolbar, sidebar, modes, replay, context menu, etc.)
 */

import { FlowCanvas } from './canvas.js?v=86';
import { PoolDataManager } from './data.js?v=2';
import { HighlightManager } from './panel.js?v=3';

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

  var currentMode = 'design';   // 'design' | 'run' | 'replay' | 'simulation'
  var flowMode = false;
  var flowSourceId = null;

  var replaySessionId = null;
  var replayPollingInterval = null;
  var currentReplayTime = '';
  var stockPollingInterval = null;

  var simSessionId = null;
  var simPollingInterval = null;
  var simStepCount = 0;

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
    // Thumbnail base path by category
    var thumbBasePath = category === 'tdx' ? '/tdxpool/'
      : category === 'dzh' ? '/dzhpool/' : null;
    // Filter: only show stock pool config files
    var filtered = files.filter(function (file) {
      if (category === 'tdx' || category === 'dzh') return file.ext === 'xml';
      if (category === 'example') return file.ext === 'json';
      return true;
    });
    filtered.forEach(function (file) {
      var li = document.createElement('li');
      li.setAttribute('data-filename', file.name);
      li.setAttribute('data-category', category);
      li.classList.add('loadable');

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

      // Thumbnail: for txd/dzh, try to load a PNG screenshot with the same name
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
          poolData.setData(json.data);
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
          poolData.setData(json.data);
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
    // Exit current mode first
    if (currentMode === 'run') exitRunMode();
    if (currentMode === 'replay') exitReplayMode();
    if (currentMode === 'simulation') exitSimulationMode();

    currentMode = mode;

    if (mode === 'design') {
      $('modeIndicator').className = '';
      $('modeIndicator').style.display = 'none';
      enableEditingUI();
      canvas.setRunMode(false);
      propPanel.setReadOnly(false);
    } else if (mode === 'run') {
      $('modeIndicator').className = 'mode-run';
      $('modeIndicator').textContent = '实盘中';
      $('modeIndicator').style.display = '';
      disableEditingUI();
      canvas.setRunMode(true);
      propPanel.setReadOnly(true);
      executePool();
      startStockPolling();
    } else if (mode === 'replay') {
      $('modeIndicator').className = 'mode-replay';
      $('modeIndicator').textContent = '回放中';
      $('modeIndicator').style.display = '';
      disableEditingUI();
      canvas.setRunMode(true);
      propPanel.setReadOnly(true);
      $('replayPanel').classList.add('visible');
      if (highlightManager) {
        highlightManager.setReplayMode(true);
        highlightManager.destroy();
        highlightManager.init();
      }
      startReplaySession();
    } else if (mode === 'simulation') {
      $('modeIndicator').className = 'mode-simulation';
      $('modeIndicator').textContent = '仿真中';
      $('modeIndicator').style.display = '';
      disableEditingUI();
      canvas.setRunMode(true);
      propPanel.setReadOnly(true);
      $('simulationPanel').classList.add('visible');
      $('eventPanel').classList.add('visible');
      eventPanelLoad();
      if (highlightManager) {
        highlightManager.destroy();
        highlightManager.init();
      }
      startSimulationSession();
    }
    updateModeButtons();
  }

  function updateModeButtons() {
    var btnRun = $('btnRun');
    var btnReplay = $('btnReplay');
    var btnSimulation = $('btnSimulation');
    var btnRunOverflow = $('btnRunOverflow');
    var btnReplayOverflow = $('btnReplayOverflow');
    var btnSimulationOverflow = $('btnSimulationOverflow');
    if (btnRun) {
      if (currentMode === 'run') {
        btnRun.textContent = '⏹ 停止';
        btnRun.classList.add('active');
      } else {
        btnRun.textContent = '▶ 实盘';
        btnRun.classList.remove('active');
      }
    }
    if (btnReplay) {
      if (currentMode === 'replay') {
        btnReplay.textContent = '⏹ 停止';
        btnReplay.classList.add('active');
      } else {
        btnReplay.textContent = '⏪ 回放';
        btnReplay.classList.remove('active');
      }
    }
    if (btnSimulation) {
      if (currentMode === 'simulation') {
        btnSimulation.textContent = '⏹ 停止';
        btnSimulation.classList.add('active');
      } else {
        btnSimulation.textContent = '🔬 仿真';
        btnSimulation.classList.remove('active');
      }
    }
    // Sync overflow menu buttons
    if (btnRunOverflow) {
      btnRunOverflow.textContent = currentMode === 'run' ? '⏹ 停止' : '▶ 实盘';
      btnRunOverflow.classList.toggle('active', currentMode === 'run');
    }
    if (btnReplayOverflow) {
      btnReplayOverflow.textContent = currentMode === 'replay' ? '⏹ 停止' : '⏪ 回放';
      btnReplayOverflow.classList.toggle('active', currentMode === 'replay');
    }
    if (btnSimulationOverflow) {
      btnSimulationOverflow.textContent = currentMode === 'simulation' ? '⏹ 停止' : '🔬 仿真';
      btnSimulationOverflow.classList.toggle('active', currentMode === 'simulation');
    }
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
    canvas.setRunMode(false);
    propPanel.setReadOnly(false);
    if (highlightManager) {
      highlightManager.setReplayMode(false);
      highlightManager.destroy();
      highlightManager.init();
    }
  }

  function exitSimulationMode() {
    stopSimulationPolling();
    simSessionId = null;
    simStepCount = 0;
    $('simulationPanel').classList.remove('visible');
    $('eventPanel').classList.remove('visible');
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
      if (currentMode !== 'design') propPanel.setReadOnly(true);
      // 联动综合设置表格：画布选中节点时高亮表格中所有同 node id 行
      try { if (window.ComprehensiveSettings && window.ComprehensiveSettings.syncFromCanvas) window.ComprehensiveSettings.syncFromCanvas(nodeId); } catch (e) {}
      // Prefetch K-lines for state pools
      if (node && (node.type === 'stock_state_pool' || node.type === 'tdx_state_pool' || node.dzh_cell_type === 200 || node.dzh_cell_type === '200')) {
        var pid = poolData._poolId;
        if (pid) {
          fetch('/api/pools/' + encodeURIComponent(pid) + '/state-pools/' + encodeURIComponent(nodeId) + '/prefetch-klines?period=day', { method: 'POST' })
            .catch(function () { });
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
    if (currentMode !== 'design') propPanel.setReadOnly(true);
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
      mode: currentMode === 'design' ? 'edit' : currentMode,
      pool_loaded: !!poolData.hasData,
      can_undo: poolData.canUndo(),
      can_redo: poolData.canRedo(),
      has_unsaved_changes: !!poolData.hasData,
      is_running: currentMode === 'run',
      is_replay_mode: currentMode === 'replay',
      selection: (canvas && canvas.selectedNodeId) ? canvas.selectedNodeId : null,
      has_clipboard: !!(window._clipboardData),
      is_edit_mode: currentMode === 'design',
      is_design_mode: currentMode === 'design',
      design_mode: currentMode === 'design'
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
    if (cond === "mode == 'edit'") return currentMode === 'design';
    if (cond === "pool_loaded && mode == 'edit'") return poolLoaded && currentMode === 'design';
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

    // Run mode (实盘) — symmetric toggle
    $('btnRun').addEventListener('click', function () {
      if (currentMode === 'run') {
        setMode('design');
      } else {
        setMode('run');
      }
    });

    // Replay mode (回放) — symmetric toggle
    $('btnReplay').addEventListener('click', function () {
      if (currentMode === 'replay') {
        setMode('design');
      } else {
        setMode('replay');
      }
    });

    // Simulation mode (仿真) — symmetric toggle
    $('btnSimulation').addEventListener('click', function () {
      if (currentMode === 'simulation') {
        setMode('design');
      } else {
        setMode('simulation');
      }
    });

    // Overflow menu mode buttons — same symmetric toggle logic
    var btnRunOverflow = $('btnRunOverflow');
    if (btnRunOverflow) {
      btnRunOverflow.addEventListener('click', function () {
        if (currentMode === 'run') setMode('design');
        else setMode('run');
      });
    }
    var btnReplayOverflow = $('btnReplayOverflow');
    if (btnReplayOverflow) {
      btnReplayOverflow.addEventListener('click', function () {
        if (currentMode === 'replay') setMode('design');
        else setMode('replay');
      });
    }
    var btnSimulationOverflow = $('btnSimulationOverflow');
    if (btnSimulationOverflow) {
      btnSimulationOverflow.addEventListener('click', function () {
        if (currentMode === 'simulation') setMode('design');
        else setMode('simulation');
      });
    }

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
    var format = $('fileInput').getAttribute('data-format') || 'dzh';
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
        if (currentMode !== 'design') return;
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
        if (currentMode !== 'design') return;
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
      var isDesign = currentMode === 'design';
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
      _ctxIsRuntime = currentMode !== 'design';

      var isDesign = currentMode === 'design';
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
      if (currentMode !== 'design') return;
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

    // Simulation panel controls
    $('simBtnClose').addEventListener('click', function () {
      setMode('design');
    });
    $('simBtnStep').addEventListener('click', function () {
      if (currentMode !== 'simulation') return;
      var delta = parseFloat($('simDeltaSelect').value) || 60;
      runSimulationStep(delta);
    });
    $('simBtnReset').addEventListener('click', function () {
      if (currentMode !== 'simulation') return;
      simStepCount = 0;
      $('simulationStepCount').textContent = '步数: 0';
      $('simulationClock').textContent = '--';
      clearEventPanel();
      var delta = parseFloat($('simDeltaSelect').value) || 60;
      runSimulationStep(delta);
    });

    $('eventPanelClear').addEventListener('click', function () {
      clearEventPanel();
    });

    $('eventPanelToggle').addEventListener('click', function () {
      _eventPanelCollapsed = !_eventPanelCollapsed;
      $('eventPanelBody').classList.toggle('collapsed', _eventPanelCollapsed);
      $('eventPanelToggle').textContent = _eventPanelCollapsed ? '▴' : '▾';
    });

    $('replaySpeedSelect').addEventListener('change', function () {
      var val = this.value;
      replaySetSpeed(val === 'MAX' ? 'MAX' : parseInt(val));
    });

    $('replayPeriodSelect').addEventListener('change', function () {
      if (currentMode === 'replay') startReplaySession();
    });

    // Date Range 选择器：日期变更时重启回放会话
    $('replayStartDate').addEventListener('change', function () {
      if (currentMode === 'replay') startReplaySession();
    });
    $('replayEndDate').addEventListener('change', function () {
      if (currentMode === 'replay') startReplaySession();
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
    var poolId = poolData._poolId;
    var poolName = poolId || (poolData._data && poolData._data.name) || '';
    if (!poolName && poolData._isTDX && poolData._tdxFilename) {
      poolName = poolData._tdxFilename.replace(/\.xml$/i, '');
    }
    if (!poolName) {
      alert('请先加载股票池');
      setMode('design');
      return;
    }
    simStepCount = 0;
    // Step 1: switch data source to mock
    fetch('/api/data_source/select/mock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    })
      .then(function (r) { return r.json(); })
      .then(function () {
        // Step 2: call sim/start to do first step
        return runSimulationStep(60);
      })
      .catch(function (err) {
        console.error('仿真启动失败:', err);
      });
  }

  function runSimulationStep(delta) {
    var poolId = poolData._poolId;
    var poolName = poolId || (poolData._data && poolData._data.name) || '';
    if (!poolName && poolData._isTDX && poolData._tdxFilename) {
      poolName = poolData._tdxFilename.replace(/\.xml$/i, '');
    }
    var url = '/api/pool/' + encodeURIComponent(poolName) + '/sim/start';
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delta: delta || 60 })
    })
      .then(function (r) { return r.json(); })
      .then(function (result) {
        if (result.success && result.data) {
          simStepCount++;
          var clock = result.data.virtual_clock;
          if (clock != null) {
            var sec = Math.floor(clock);
            var hh = String(Math.floor(sec / 3600) % 24).padStart(2, '0');
            var mm = String(Math.floor(sec / 60) % 60).padStart(2, '0');
            var ss = String(sec % 60).padStart(2, '0');
            $('simulationClock').textContent = hh + ':' + mm + ':' + ss;
          }
          $('simulationStepCount').textContent = '步数: ' + simStepCount;
          eventPanelLoad();
          // Update node stocks on canvas
          if (result.data.node_stocks) {
            syncExecResult(result.data);
            canvas.refreshStockTables();
          }
        } else {
          console.error('仿真步进失败:', result.error);
        }
      })
      .catch(function (err) {
        console.error('仿真步进请求失败:', err);
      });
  }

  function stopSimulationPolling() {
    if (simPollingInterval) {
      clearInterval(simPollingInterval);
      simPollingInterval = null;
    }
  }

  var _eventPanelCollapsed = false;
  var _lastEventPanelCount = 0;

  function eventPanelLoad() {
    var poolId = poolData._poolId;
    var poolName = poolId || (poolData._data && poolData._data.name) || '';
    if (!poolName && poolData._isTDX && poolData._tdxFilename) {
      poolName = poolData._tdxFilename.replace(/\.xml$/i, '');
    }
    if (!poolName) return;
    fetch('/api/pool/' + encodeURIComponent(poolName) + '/event-panel?limit=500')
      .then(function (r) { return r.json(); })
      .then(function (result) {
        if (!result.success) return;
        var events = result.events || [];
        _lastEventPanelCount = events.length;
        $('eventPanelCount').textContent = events.length;
        var empty = $('eventPanelEmpty');
        var body = $('eventPanelBody');
        if (events.length === 0) {
          if (empty) empty.style.display = '';
          return;
        }
        if (empty) empty.style.display = 'none';
        var rows = body.querySelectorAll('.event-row');
        var existingCount = rows.length;
        for (var i = existingCount; i < events.length; i++) {
          var ev = events[i];
          var type = ev.event_type || '?';
          if (type === 'RANK_CHANGED') continue;
          var row = document.createElement('div');
          row.className = 'event-row';
          var ts = ev.ts ? formatEventTs(ev.ts) : '';
          var detail = formatEventDetail(type, ev.details || {});
          row.innerHTML = '<span class="event-row-time">' + escHtml(ts) + '</span>'
            + '<span class="event-row-type type-' + escHtml(type) + '">' + escHtml(type) + '</span>'
            + '<span class="event-row-detail">' + escHtml(detail) + '</span>';
          body.appendChild(row);
        }
        body.scrollTop = body.scrollHeight;
      })
      .catch(function () {});
  }

  function formatEventTs(ts) {
    if (typeof ts !== 'number') return String(ts);
    var d = new Date(ts * 1000);
    if (isNaN(d.getTime())) return String(ts);
    var hh = String(d.getHours()).padStart(2, '0');
    var mm = String(d.getMinutes()).padStart(2, '0');
    var ss = String(d.getSeconds()).padStart(2, '0');
    return hh + ':' + mm + ':' + ss;
  }

  function formatEventDetail(type, d) {
    if (type === 'Signal') return (d.signal_type || '') + ' ' + (d.code || '') + ' @' + (d.price || 0) + ' qty=' + (d.quantity || 0);
    if (type === 'Executed') return (d.edge_id || '') + ' enter:' + (d.entered || []).join(',') + ' exit:' + (d.exited || []).join(',');
    if (type === 'DomainEvent') return (d.event_type || '') + ' ' + (d.code || '') + '→' + (d.pool_id || '');
    if (type === 'DataChanged') return (d.source || '') + ' ' + (d.period || '') + ' [' + (d.codes || []).join(',') + ']';
    return JSON.stringify(d);
  }

  function escHtml(s) {
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function clearEventPanel() {
    var body = $('eventPanelBody');
    var rows = body.querySelectorAll('.event-row');
    rows.forEach(function (r) { r.remove(); });
    var empty = $('eventPanelEmpty');
    if (empty) empty.style.display = '';
    $('eventPanelCount').textContent = '0';
    _lastEventPanelCount = 0;
  }

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
    if (currentMode !== 'design') return;
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

  function updateStatusBar() {
    if (!poolData.data) return;
    $('statusNodes').textContent = '节点: ' + poolData.getNodeCount();
    $('statusEdges').textContent = '连线: ' + poolData.getEdgeCount();
    var now = new Date();
    $('statusTime').textContent = now.getHours().toString().padStart(2, '0') + ':' +
      now.getMinutes().toString().padStart(2, '0') + ':' +
      now.getSeconds().toString().padStart(2, '0');
    $('statusZoom').textContent = Math.round(canvas.transform.zoom * 100) + '%';
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
    setInterval(function () {
      var el = $('statusTime');
      if (!el) return;
      var now = new Date();
      el.textContent = now.getHours().toString().padStart(2, '0') + ':' +
        now.getMinutes().toString().padStart(2, '0') + ':' +
        now.getSeconds().toString().padStart(2, '0');
    }, 1000);
  }

  // ─── Toast Notifications ────────────────────────────────────────────────────

  function showToast(msg, type) {
    var t = document.createElement('div');
    t.className = 'toast toast-' + (type || 'success');
    t.textContent = msg;
    t.style.cssText = 'position:fixed;top:60px;right:20px;z-index:9999;padding:10px 20px;border-radius:6px;font-size:13px;color:#fff;pointer-events:none;opacity:0.95;transition:opacity 0.3s;' +
      'background:' + (type === 'error' ? '#e74c3c' : type === 'warn' ? '#f39c12' : '#27ae60') + ';';
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
