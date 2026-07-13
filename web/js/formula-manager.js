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
