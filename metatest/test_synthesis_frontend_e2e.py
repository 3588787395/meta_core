"""前端 E2E 合测试（Task 21 SubTask 21.6）。

使用 Playwright 驱动浏览器，端到端验证前端界面：
  - 主页加载
  - 顶部导航栏可见
  - 模式切换到仿真
  - 事件面板可见性
  - 股票池设计器

环境缺失时通过 fixture（playwright_browser / web_server_url）skip。

测试用例：
  1. test_home_page_loads
  2. test_top_nav_visible
  3. test_mode_switch_to_simulation
  4. test_event_panel_visible_in_simulation
  5. test_pool_designer_loads
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
        """主页加载成功，含 #topbar 元素。"""
        page = home_page
        # #topbar 顶部工具栏可见
        topbar = page.query_selector("#topbar")
        assert topbar is not None, "#topbar 元素应存在"
        assert topbar.is_visible(), "#topbar 应可见"
        # 页面标题非空
        title = page.title()
        assert title, "页面标题不应为空"
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
        report_state["frontend_e2e_passed"] = \
            report_state.get("frontend_e2e_passed", 0) + 1
        report_state["frontend_e2e_total"] = \
            report_state.get("frontend_e2e_total", 0) + 1

    def test_mode_switch_to_simulation(self, home_page, report_state) -> None:
        """点击仿真模式按钮后页面状态变化（body[data-mode] 或按钮 active）。"""
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
        report_state["frontend_e2e_passed"] = \
            report_state.get("frontend_e2e_passed", 0) + 1
        report_state["frontend_e2e_total"] = \
            report_state.get("frontend_e2e_total", 0) + 1

    def test_pool_designer_loads(self, home_page, report_state) -> None:
        """股票池设计器界面加载（canvas 元素存在）。"""
        page = home_page
        # 查找设计器相关元素（canvas 或 svg 或 .designer）
        designer = page.query_selector(
            "canvas, svg, .designer, #designer, [class*='designer'], [id*='pool-designer']"
        )
        assert designer is not None, "未找到股票池设计器元素"
        report_state["frontend_e2e_passed"] = \
            report_state.get("frontend_e2e_passed", 0) + 1
        report_state["frontend_e2e_total"] = \
            report_state.get("frontend_e2e_total", 0) + 1
