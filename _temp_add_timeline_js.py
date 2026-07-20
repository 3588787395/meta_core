import re

with open(r'h:\new_tdx_mock\PYPlugins\meta_core\web\js\ui.js', 'r', encoding='utf-8') as f:
    content = f.read()

timeline_code = r'''
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

function timelineAddEvent(ev) {
    if (__eventTimeline) {
        __eventTimeline.addEvent(ev);
    }
}

'''

# 在最后的 })(); 之前插入代码
old_ending = '\n})();'
if old_ending in content:
    new_content = content.replace(old_ending, timeline_code + old_ending)
    
    with open(r'h:\new_tdx_mock\PYPlugins\meta_core\web\js\ui.js', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('Timeline类代码添加成功')
else:
    print('未找到文件末尾模式')
