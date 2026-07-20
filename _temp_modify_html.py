import re

with open(r'h:\new_tdx_mock\PYPlugins\meta_core\web\index.html', 'r', encoding='utf-8') as f:
    content = f.read()

timeline_html = '''
  <div id="epTimelineSection" class="ep-timeline-section">
    <div class="ep-timeline-header" id="epTimelineToggle">
      <span class="ep-timeline-title">⏱ 时间轴</span>
      <div class="ep-timeline-controls">
        <button id="btnTimelineAutoScroll" class="ep-timeline-btn active" title="暂停/继续自动滚动">⏸ 跟随</button>
        <button id="btnTimelineZoomIn" class="ep-timeline-btn" title="放大">+</button>
        <button id="btnTimelineZoomOut" class="ep-timeline-btn" title="缩小">-</button>
        <button id="btnTimelineReset" class="ep-timeline-btn" title="重置视图">⟲</button>
        <span class="ep-timeline-collapse">▼</span>
      </div>
    </div>
    <div id="epTimelineContainer" class="ep-timeline-container">
      <canvas id="epTimelineCanvas" class="ep-timeline-canvas"></canvas>
      <div id="epTimelineTooltip" class="ep-timeline-tooltip" style="display:none;"></div>
    </div>
  </div>
'''

old_str = '  <div id="eventPanelBody"'
if old_str in content:
    idx = content.find(old_str)
    new_content = content[:idx] + timeline_html + content[idx:]
    
    new_content = re.sub(r'href="css/styles\.css\?v=\d+"', 'href="css/styles.css?v=4"', new_content)
    new_content = re.sub(r'src="js/ui\.js\?v=\d+"', 'src="js/ui.js?v=4"', new_content)
    if 'src="js/ui.js?v=4"' not in new_content:
        new_content = new_content.replace('src="js/ui.js"', 'src="js/ui.js?v=4"')
    
    with open(r'h:\new_tdx_mock\PYPlugins\meta_core\web\index.html', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('HTML修改成功')
else:
    print('未找到eventPanelBody位置')
    print('查找ep-filter-bar结束位置附近...')
