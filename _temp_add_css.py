css_to_add = '''

/* ===== Timeline Styles ===== */
.ep-timeline-section {
  border-bottom: 1px solid rgba(255,255,255,0.08);
  flex-shrink: 0;
}
.ep-timeline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: rgba(0,0,0,0.3);
  cursor: pointer;
  user-select: none;
}
.ep-timeline-title {
  font-size: 12px;
  font-weight: 600;
  color: #e0e0e0;
}
.ep-timeline-controls {
  display: flex;
  align-items: center;
  gap: 4px;
}
.ep-timeline-btn {
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.12);
  color: #ccc;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.ep-timeline-btn:hover {
  background: rgba(255,255,255,0.15);
  color: #fff;
}
.ep-timeline-btn.active {
  background: rgba(33,150,243,0.3);
  border-color: rgba(33,150,243,0.5);
  color: #64b5f6;
}
.ep-timeline-collapse {
  font-size: 10px;
  color: #888;
  margin-left: 4px;
  transition: transform 0.2s;
}
.ep-timeline-section.collapsed .ep-timeline-collapse {
  transform: rotate(-90deg);
}
.ep-timeline-container {
  position: relative;
  height: 80px;
  background: #1a1a2e;
  overflow: hidden;
}
.ep-timeline-section.collapsed .ep-timeline-container {
  display: none;
}
.ep-timeline-canvas {
  display: block;
  width: 100%;
  height: 100%;
  cursor: grab;
}
.ep-timeline-canvas:active {
  cursor: grabbing;
}
.ep-timeline-tooltip {
  position: absolute;
  background: rgba(20,20,35,0.95);
  border: 1px solid rgba(100,149,237,0.5);
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 11px;
  color: #e0e0e0;
  pointer-events: none;
  z-index: 1000;
  max-width: 280px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.5);
}
.ep-timeline-tooltip .tt-time {
  color: #64b5f6;
  font-weight: 600;
  margin-bottom: 4px;
}
.ep-timeline-tooltip .tt-type {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
  margin-bottom: 4px;
}
.ep-timeline-tooltip .tt-code {
  font-family: monospace;
  color: #ffd54f;
  margin-bottom: 2px;
}
.ep-timeline-tooltip .tt-detail {
  color: #aaa;
  font-size: 10px;
  word-break: break-all;
}
'''

with open(r'h:\new_tdx_mock\PYPlugins\meta_core\web\css\styles.css', 'a', encoding='utf-8') as f:
    f.write(css_to_add)

print('CSS样式添加成功')
