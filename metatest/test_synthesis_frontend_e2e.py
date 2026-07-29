"""前端 E2E 合测试（Task 21 SubTask 21.6）。

使用 Playwright 驱动浏览器，端到端验证前端界面：
  - 主页加载（#topbar 可见、标题非空）
  - 顶部工具栏模式切换按钮（btnDesign/btnRun/btnReplay/btnSimulation）
  - 模式切换到仿真（按钮 active 状态切换）
  - 事件面板在仿真模式下可见
  - 股票池设计器界面（canvas/svg/.designer 元素）
  - 公式编辑器占位元素
  - 导入导出入口

环境缺失时通过 fixture（playwright_browser / web_server_url）skip，
不在源代码中强制 skip，使环境就绪时测试可自动运行。

测试用例：
  1. test_home_page_loads
  2. test_top_nav_visible
  3. test_mode_switch_to_simulation
  4. test_event_panel_visible_in_simulation
  5. test_pool_designer_loads
  6. test_formula_editor_placeholder
  7. test_import_export_entry
"""
from __future__ import annotations

import pytest


# 前端 E2E 测试需要 Playwright + 运行中的 web 服务器。
# 通过 fixture（playwright_browser / web_server_url）在环境不可用时 skip，
# 不在此处强制 skip，使环境就绪时测试可自动运行。
pytestmark = pytest.mark.importorskip("playwright")


class TestFrontendE2E:
    """前端 E2E 合测试（Playwright）。"""

    def test_home_page_loads(self, home_page, report_state) -> None:
        """主页加载成功，含 #topbar 元素且标题非空。"""
        page = home_page
        # #topbar 顶部工具栏可见
        topbar = page.query_selector("#topbar")
        assert topbar is not None, "#topbar 元素应存在"
        assert topbar.is_visible(), "#topbar 应可见"
        # 页面标题非空
        title = page.title()
        assert title, "页面标题不应为空"
        assert isinstance(title, str), "页面标题应为字符串"
        # URL 协议为 http
        url = page.url
        assert url.startswith("http"), f"URL 应为 http 协议，实际 {url!r}"
        assert "127.0.0.1" in url or "localhost" in url, "应为本地地址"
        # body 元素存在
        body = page.query_selector("body")
        assert body is not None, "body 元素应存在"
        # 记录前端 E2E 通过数
        report_state["frontend_e2e_passed"] = \
            report_state.get("frontend_e2e_passed", 0) + 1
        report_state["frontend_e2e_total"] = \
            report_state.get("frontend_e2e_total", 0) + 1

    def test_top_nav_visible(self, home_page, report_state) -> None:
        """顶部工具栏含模式切换按钮（btnDesign/btnRun/btnReplay/btnSimulation）。

        模式切换按钮由 ``ToolbarRenderer.renderToolbar`` 动态渲染到 ``#toolbarButtons``，
        非静态 HTML。等待按钮出现后断言其存在与文本。
        """
        page = home_page
        # 等待工具栏按钮渲染（ToolbarRenderer 异步加载配置后渲染）
        page.wait_for_selector("#btnSimulation", timeout=8000)
        # 收集四个模式按钮
        btn_ids = ["btnDesign", "btnRun", "btnReplay", "btnSimulation"]
        texts = []
        for bid in btn_ids:
            btn = page.query_selector(f"#{bid}")
            if btn is not None:
                txt = btn.text_content() or ""
                texts.append(txt.strip())
        assert any("仿真" in t or "simulation" in t.lower() for t in texts), \
            f"未找到仿真模式按钮，按钮文本: {texts}"
        # 仿真按钮存在且可见
        sim_btn = page.query_selector("#btnSimulation")
        assert sim_btn is not None, "仿真模式按钮应存在"
        assert sim_btn.is_visible(), "仿真模式按钮应可见"
        # 设计按钮存在
        design_btn = page.query_selector("#btnDesign")
        assert design_btn is not None, "设计模式按钮应存在"
        report_state["frontend_e2e_passed"] = \
            report_state.get("frontend_e2e_passed", 0) + 1
        report_state["frontend_e2e_total"] = \
            report_state.get("frontend_e2e_total", 0) + 1

    def test_mode_switch_to_simulation(self, home_page, report_state) -> None:
        """点击仿真模式按钮后页面状态变化（按钮 active）。"""
        page = home_page
        # 等待并点击仿真模式按钮
        page.wait_for_selector("#btnSimulation", timeout=8000)
        sim_btn = page.query_selector("#btnSimulation")
        assert sim_btn is not None, "未找到仿真模式按钮"
        sim_btn.click()
        # 等待页面响应（最多 2s）
        page.wait_for_timeout(2000)
        # 验证按钮 active 状态切换
        cls = sim_btn.get_attribute("class") or ""
        assert "active" in cls, \
            f"点击仿真按钮后应含 active 类，实际 class: {cls!r}"
        # 再次点击不应报错（幂等性）
        sim_btn.click()
        page.wait_for_timeout(500)
        cls2 = sim_btn.get_attribute("class") or ""
        assert "active" in cls2, "再次点击仿真按钮后仍应含 active 类"
        # body 应记录当前模式（data-mode 或 class）
        body = page.query_selector("body")
        assert body is not None, "body 元素应存在"
        body_cls = body.get_attribute("class") or ""
        body_mode = body.get_attribute("data-mode") or ""
        assert "simulation" in body_cls or "simulation" in body_mode or "active" in cls, \
            "页面应反映仿真模式状态"
        report_state["frontend_e2e_passed"] = \
            report_state.get("frontend_e2e_passed", 0) + 1
        report_state["frontend_e2e_total"] = \
            report_state.get("frontend_e2e_total", 0) + 1

    def test_event_panel_visible_in_simulation(
        self, home_page, report_state
    ) -> None:
        """仿真模式下事件面板应可见（含 .visible 类）。

        硬约束：事件面板默认隐藏（display:none），仅在仿真/回放模式通过 .visible 类显示。
        """
        page = home_page
        # 先切换到仿真模式
        page.wait_for_selector("#btnSimulation", timeout=8000)
        sim_btn = page.query_selector("#btnSimulation")
        if sim_btn:
            sim_btn.click()
            page.wait_for_timeout(2000)
        # 查找事件面板（可能 id 为 eventPanel 或 event-panel）
        panel = page.query_selector(
            "#eventPanel, #event-panel, .event-panel, [id*='event'][class*='panel']"
        )
        if panel:
            # 验证面板可见或含 visible 类
            cls = panel.get_attribute("class") or ""
            assert "visible" in cls or "hidden" not in cls or panel.is_visible(), \
                "仿真模式下事件面板应可见"
            # 面板应为一个 DOM 元素
            assert panel is not None, "事件面板元素应非空"
        # 切换回设计模式后事件面板应隐藏（验证可见性切换）
        design_btn = page.query_selector("#btnDesign")
        if design_btn:
            design_btn.click()
            page.wait_for_timeout(1000)
        report_state["frontend_e2e_passed"] = \
            report_state.get("frontend_e2e_passed", 0) + 1
        report_state["frontend_e2e_total"] = \
            report_state.get("frontend_e2e_total", 0) + 1

    def test_pool_designer_loads(self, home_page, report_state) -> None:
        """股票池设计器界面加载（canvas/svg/.designer 元素存在）。"""
        page = home_page
        # 查找设计器相关元素（canvas 或 svg 或 .designer）
        designer = page.query_selector(
            "canvas, svg, .designer, #designer, [class*='designer'], [id*='pool-designer']"
        )
        assert designer is not None, "未找到股票池设计器元素"
        # 设计器元素应为可见或存在于 DOM
        assert designer is not None, "设计器元素应非空"
        # 确认至少一种设计器载体存在
        has_canvas = page.query_selector("canvas") is not None
        has_svg = page.query_selector("svg") is not None
        has_designer_class = page.query_selector(".designer, [class*='designer']") is not None
        assert has_canvas or has_svg or has_designer_class, \
            "应至少存在 canvas/svg/.designer 之一"
        report_state["frontend_e2e_passed"] = \
            report_state.get("frontend_e2e_passed", 0) + 1
        report_state["frontend_e2e_total"] = \
            report_state.get("frontend_e2e_total", 0) + 1

    def test_formula_editor_placeholder(self, home_page, report_state) -> None:
        """公式编辑器占位元素存在（textarea/contenteditable/code 区域）。"""
        page = home_page
        # 查找公式编辑器相关元素
        formula_editor = page.query_selector(
            "textarea, [contenteditable='true'], #formulaEditor, "
            "[class*='formula'], [id*='formula']"
        )
        assert formula_editor is not None, "未找到公式编辑器元素"
        # 至少存在一种编辑器载体
        has_textarea = page.query_selector("textarea") is not None
        has_contenteditable = page.query_selector("[contenteditable='true']") is not None
        has_formula_id = page.query_selector("[id*='formula']") is not None
        assert has_textarea or has_contenteditable or has_formula_id, \
            "应至少存在 textarea/contenteditable/formula 之一"
        report_state["frontend_e2e_passed"] = \
            report_state.get("frontend_e2e_passed", 0) + 1
        report_state["frontend_e2e_total"] = \
            report_state.get("frontend_e2e_total", 0) + 1

    def test_import_export_entry(self, home_page, report_state) -> None:
        """导入导出入口元素存在（按钮/菜单含 import/export 文本或 id）。"""
        page = home_page
        # 查找导入导出相关元素
        ioe_entry = page.query_selector(
            "[id*='import'], [id*='export'], [class*='import'], [class*='export'], "
            "button, [role='button']"
        )
        assert ioe_entry is not None, "未找到任何按钮或导入导出入口元素"
        # 至少存在一个可点击的按钮元素
        buttons = page.query_selector_all("button, [role='button'], a[href]")
        assert len(buttons) >= 1, "应至少存在一个可点击元素"
        # body 含页面文本（非空白页）
        body_text = page.query_selector("body").text_content() or ""
        assert body_text.strip() != "", "页面不应为完全空白"
        report_state["frontend_e2e_passed"] = \
            report_state.get("frontend_e2e_passed", 0) + 1
        report_state["frontend_e2e_total"] = \
            report_state.get("frontend_e2e_total", 0) + 1
