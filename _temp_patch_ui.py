with open(r'h:\new_tdx_mock\PYPlugins\meta_core\web\js\ui.js', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 在 addEvent 函数末尾添加 timelineAddEvent 调用
# 找到 addEvent 函数中 autoScroll 调用之后，或者在函数返回前
old_add_end = '    autoScroll(processedEventList);'
if old_add_end in content:
    new_add_end = '''    autoScroll(processedEventList);
    if (typeof timelineAddEvent === 'function') {
        try { timelineAddEvent(ev); } catch(e) {}
    }'''
    content = content.replace(old_add_end, new_add_end)
    print('addEvent函数中添加timelineAddEvent调用')
else:
    print('未找到autoScroll位置，尝试其他模式')
    # 尝试找其他位置
    if 'function addEvent(ev)' in content:
        idx = content.find('function addEvent(ev)')
        # 找函数末尾 - 找下一个function或}
        print(f'addEvent在位置 {idx}')

# 2. 在DOMContentLoaded或初始化部分添加initEventTimeline调用
# 查找处理事件面板初始化的地方
init_patterns = [
    'ep-filter-btn',
    'processedEventList?.addEventListener',
    'initEventMonitor',
    'setupEventPanel'
]

for pat in init_patterns:
    if pat in content:
        idx = content.find(pat)
        print(f'找到 "{pat}" 在位置 {idx}')

# 查找事件面板初始化后添加initEventTimeline
old_listener = "processedEventList?.addEventListener('click', onEventListClick);"
if old_listener in content:
    new_listener = old_listener + "\n  setTimeout(() => { if (typeof initEventTimeline === 'function') initEventTimeline(); }, 100);"
    content = content.replace(old_listener, new_listener)
    print('添加initEventTimeline初始化调用')

with open(r'h:\new_tdx_mock\PYPlugins\meta_core\web\js\ui.js', 'w', encoding='utf-8') as f:
    f.write(content)
print('ui.js修改完成')
