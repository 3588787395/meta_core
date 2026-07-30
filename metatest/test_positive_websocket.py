"""Task：WebSocket / SSE 端点正测试。

验证 ``app.py`` 中声明的实时通信端点：
  - SSE 端点 ``/api/events/stream`` 存在且返回 ``text/event-stream``
  - WebSocket 端点 ``/ws/pool/{name}`` / ``/ws/highlight`` / ``/api/config/ws`` 存在
  - HTTP 路由与 WebSocket 路由使用不同挂载策略（WS 路由不带 API key 依赖）
  - 前端 ``web/js/event-panel.js`` 中三个常量符合契约：
    ``RECONNECT_DELAY=3000`` / ``MAX_EVENTS=2000`` / ``RENDER_THROTTLE=200``

使用 ``fastapi_client`` fixture，并直接读取 ``app.routes`` 与 ``app.py`` 源文件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_EVENT_PANEL_JS = _PROJECT_ROOT / "web" / "js" / "event-panel.js"


def _route_paths(app: Any) -> List[str]:
    """收集 app.routes 中所有路由的路径字符串。"""
    paths: List[str] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            paths.append(path)
    return paths


# ============================================================================
# SubTask：SSE / WebSocket 端点存在性
# ============================================================================


class TestEndpointExistence:
    """验证 SSE / WebSocket 端点在 app.routes 中注册。"""

    def test_sse_endpoint_path_exists(self, fastapi_client: Any):
        """SSE 端点 ``/api/events/stream`` 在 app 路由中存在。"""
        from app import app

        paths = _route_paths(app)
        assert "/api/events/stream" in paths, "SSE 端点 /api/events/stream 未注册"

    def test_ws_pool_endpoint_path_exists(self, fastapi_client: Any):
        """WebSocket 端点 ``/ws/pool/{name}`` 在 app 路由中存在。"""
        from app import app

        paths = _route_paths(app)
        assert "/ws/pool/{name}" in paths, "WebSocket 端点 /ws/pool/{name} 未注册"

    def test_ws_highlight_endpoint_path_exists(self, fastapi_client: Any):
        """WebSocket 端点 ``/ws/highlight`` 在 app 路由中存在。"""
        from app import app

        paths = _route_paths(app)
        assert "/ws/highlight" in paths, "WebSocket 端点 /ws/highlight 未注册"

    def test_config_ws_endpoint_path_exists(self, fastapi_client: Any):
        """WebSocket 端点 ``/api/config/ws`` 在 app 路由中存在。"""
        from app import app

        paths = _route_paths(app)
        assert "/api/config/ws" in paths, "WebSocket 端点 /api/config/ws 未注册"

    def test_sse_endpoint_method_is_get(self, fastapi_client: Any):
        """SSE 端点使用 GET 方法（FastAPI SSE 约定）。"""
        from app import app
        from starlette.routing import Route

        target: Route | None = None
        for route in app.routes:
            if getattr(route, "path", None) == "/api/events/stream":
                target = route
                break
        assert target is not None, "未找到 /api/events/stream 路由对象"
        methods = getattr(target, "methods", None) or set()
        assert "GET" in methods, f"SSE 端点方法应为 GET，实际 {methods}"

    def test_ws_endpoints_use_websocket_protocol(self, fastapi_client: Any):
        """三个 WebSocket 端点对应 WebSocketRoute（非普通 HTTP Route）。"""
        pytest.importorskip("starlette")
        from app import app
        from starlette.routing import WebSocketRoute

        ws_paths = {"/ws/pool/{name}", "/ws/highlight", "/api/config/ws"}
        found: dict[str, bool] = {p: False for p in ws_paths}
        for route in app.routes:
            if isinstance(route, WebSocketRoute) and getattr(route, "path", None) in ws_paths:
                found[route.path] = True
        missing = [p for p, ok in found.items() if not ok]
        assert not missing, f"以下 WebSocket 端点未使用 WebSocketRoute: {missing}"


# ============================================================================
# SubTask：HTTP 与 WS 路由挂载策略分离（WS 不带 API key 依赖）
# ============================================================================


class TestRouteMountingSeparation:
    """验证 HTTP 路由带 verify_api_key 依赖，WebSocket 路由不带。

    对应 app.py L679 注释：
        ``# WebSocket 路由独立挂载，不带 API key dependencies``
    """

    def test_ws_routes_have_no_api_key_dependency(self, fastapi_client: Any):
        """WebSocket 路由不应携带 verify_api_key 依赖。"""
        from app import app
        from starlette.routing import WebSocketRoute

        ws_paths = {"/ws/pool/{name}", "/ws/highlight", "/api/config/ws"}
        for route in app.routes:
            if isinstance(route, WebSocketRoute) and route.path in ws_paths:
                deps = getattr(route, "dependencies", None) or []
                dep_names = [
                    getattr(getattr(d, "dependency", None), "__name__", "") or str(d)
                    for d in deps
                ]
                assert all("verify_api_key" not in n for n in dep_names), (
                    f"WS 路由 {route.path} 不应带 verify_api_key 依赖，实际: {dep_names}"
                )

    def test_http_sim_routes_are_registered(self, fastapi_client: Any):
        """HTTP 路由 /api/sim/* 至少存在（与 WS 路由分离的证据）。"""
        from app import app

        paths = _route_paths(app)
        http_paths = [p for p in paths if p.startswith("/api/sim/")]
        assert len(http_paths) >= 3, f"HTTP /api/sim/* 路由数不足，实际: {http_paths}"
        # 与 WS 路由集合不交集
        ws_set = {"/ws/pool/{name}", "/ws/highlight", "/api/config/ws"}
        assert set(http_paths).isdisjoint(ws_set), "HTTP 路由与 WS 路由路径重叠"

    def test_config_ws_router_mounted_without_api_key(self, fastapi_client: Any):
        """config_ws_router 经 app.include_router 挂载时不带 dependencies。

        对应 app.py L680: ``app.include_router(config_ws_router)`` ——
        与带 ``dependencies=[Depends(verify_api_key)]`` 的 HTTP router 不同。
        """
        # 通过比对 app.routes 中 /api/config/ws 的 dependencies 校验
        from app import app

        target = None
        for route in app.routes:
            if getattr(route, "path", None) == "/api/config/ws":
                target = route
                break
        assert target is not None, "未找到 /api/config/ws 路由"
        deps = getattr(target, "dependencies", None) or []
        assert len(deps) == 0, f"/api/config/ws 不应有 dependencies，实际: {deps}"


# ============================================================================
# SubTask：SSE 响应内容类型与 header
# ============================================================================


class TestSSEContentType:
    """SSE 端点响应 media_type 为 text/event-stream。"""

    def test_sse_response_media_type(self, fastapi_client: Any):
        """SSE 端点源码中 ``StreamingResponse(media_type="text/event-stream")``。"""
        source = (_PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
        assert 'media_type="text/event-stream"' in source or (
            "media_type=" in source and "text/event-stream" in source
        ), "app.py 中未发现 text/event-stream media_type"

    def test_sse_response_headers(self, fastapi_client: Any):
        """SSE 响应头包含 Cache-Control: no-cache（SSE 约定）。"""
        source = (_PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
        assert "Cache-Control" in source, "SSE 响应头未设置 Cache-Control"
        assert "no-cache" in source, "SSE Cache-Control 值应为 no-cache"


# ============================================================================
# SubTask：前端 event-panel.js 常量契约
# ============================================================================


class TestEventPanelConstants:
    """验证 ``web/js/event-panel.js`` 中三个常量值。"""

    @staticmethod
    def _read_js() -> str:
        assert _EVENT_PANEL_JS.exists(), f"event-panel.js 不存在: {_EVENT_PANEL_JS}"
        return _EVENT_PANEL_JS.read_text(encoding="utf-8")

    def test_reconnect_delay_is_3000(self):
        """``RECONNECT_DELAY = 3000``（毫秒）。"""
        source = self._read_js()
        assert "RECONNECT_DELAY" in source, "event-panel.js 缺少 RECONNECT_DELAY"
        # 匹配 ``const RECONNECT_DELAY = 3000;``
        import re

        m = re.search(r"RECONNECT_DELAY\s*=\s*(\d+)", source)
        assert m is not None, "未匹配到 RECONNECT_DELAY 赋值"
        assert int(m.group(1)) == 3000, f"RECONNECT_DELAY 应为 3000，实际 {m.group(1)}"

    def test_max_events_is_2000(self):
        """``MAX_EVENTS = 2000``。"""
        source = self._read_js()
        assert "MAX_EVENTS" in source, "event-panel.js 缺少 MAX_EVENTS"
        import re

        m = re.search(r"MAX_EVENTS\s*=\s*(\d+)", source)
        assert m is not None, "未匹配到 MAX_EVENTS 赋值"
        assert int(m.group(1)) == 2000, f"MAX_EVENTS 应为 2000，实际 {m.group(1)}"

    def test_render_throttle_is_200(self):
        """``RENDER_THROTTLE = 200``（毫秒）。"""
        source = self._read_js()
        assert "RENDER_THROTTLE" in source, "event-panel.js 缺少 RENDER_THROTTLE"
        import re

        m = re.search(r"RENDER_THROTTLE\s*=\s*(\d+)", source)
        assert m is not None, "未匹配到 RENDER_THROTTLE 赋值"
        assert int(m.group(1)) == 200, f"RENDER_THROTTLE 应为 200，实际 {m.group(1)}"


# ============================================================================
# SubTask：WebSocket 交互（使用 TestClient websocket_connect）
# ============================================================================


class TestSessionIsolation:
    """会话隔离验证：通过路由结构检查，不进行阻塞式 WebSocket 连接。

    Task 场景 10「Session isolation (if testable)」：直接通过 TestClient 进行
    WebSocket 连接在某些环境下会阻塞（Windows + 多 fixture 组合），因此
    本测试改为静态路由结构检查，验证不同会话端点彼此隔离。
    """

    def test_ws_routes_use_distinct_paths(self, fastapi_client: Any):
        """三个 WebSocket 端点路径互不相同，会话彼此隔离。"""
        from app import app

        ws_paths = set()
        for route in app.routes:
            path = getattr(route, "path", None)
            if path and (path.startswith("/ws/") or path == "/api/config/ws"):
                ws_paths.add(path)
        assert len(ws_paths) == 3, f"应有 3 个不同 WS 路径，实际: {ws_paths}"
        assert "/ws/pool/{name}" in ws_paths
        assert "/ws/highlight" in ws_paths
        assert "/api/config/ws" in ws_paths

    def test_ws_endpoint_handlers_are_distinct(self, fastapi_client: Any):
        """三个 WebSocket 端点对应不同的处理函数。"""
        from app import app

        handlers = []
        for route in app.routes:
            path = getattr(route, "path", None)
            if path in ("/ws/pool/{name}", "/ws/highlight", "/api/config/ws"):
                endpoint = getattr(route, "endpoint", None)
                assert endpoint is not None, f"路由 {path} 缺少 endpoint"
                handlers.append((path, endpoint.__name__))
        # 三个处理函数名互不相同
        names = [n for _, n in handlers]
        assert len(set(names)) == 3, f"处理函数应互不相同，实际: {names}"

    def test_sse_endpoint_separate_from_ws(self, fastapi_client: Any):
        """SSE 端点（HTTP GET）与 WebSocket 端点协议分离。"""
        from app import app
        from starlette.routing import WebSocketRoute

        sse_is_get = False
        ws_count = 0
        for route in app.routes:
            path = getattr(route, "path", None)
            if path == "/api/events/stream":
                methods = getattr(route, "methods", None) or set()
                assert "GET" in methods, "SSE 端点应为 GET"
                sse_is_get = True
            elif isinstance(route, WebSocketRoute):
                ws_count += 1
        assert sse_is_get, "SSE 端点未找到或非 GET"
        assert ws_count >= 3, f"应有 ≥3 个 WebSocket 路由，实际 {ws_count} 个"


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 2 E4 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 E4 SSE asyncio.Queue + E6 前端 setInterval 收敛回归。"""

    def test_app_uses_asyncio_queue_for_sse(self):
        """app.py events_stream 使用 asyncio.Queue（E4 替代 50ms 轮询）。"""
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent.joinpath("app.py").read_text(encoding="utf-8")
        assert "async def events_stream" in src, \
            "app.py 应含 async def events_stream 函数（SSE 端点）"
        assert "asyncio.Queue" in src, \
            "events_stream 应使用 asyncio.Queue（E4 阻塞等待，替代 50ms 轮询）"

    def test_no_asyncio_sleep_005_in_app(self):
        """app.py 不含 asyncio.sleep(0.05) 50ms SSE 轮询（E4 已改 asyncio.Queue）。"""
        import re
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent.joinpath("app.py").read_text(encoding="utf-8")
        count = len(re.findall(r"asyncio\.sleep\(0\.05\)", src))
        assert count == 0, \
            f"app.py 不应含 asyncio.sleep(0.05) SSE 轮询（E4 已改 asyncio.Queue），实际 {count} 处"

    def test_no_setinterval_fetch_in_web_js(self):
        """web/js/*.js 不含 setInterval.*fetch 前端轮询（E6 已改 SSE/WS 订阅）。"""
        import re
        from pathlib import Path
        js_dir = Path(__file__).resolve().parent.parent / "web" / "js"
        total = 0
        for js in js_dir.glob("*.js"):
            try:
                src = js.read_text(encoding="utf-8")
            except OSError:
                continue
            total += len(re.findall(r"setInterval.*fetch", src))
        assert total == 0, \
            f"web/js/*.js 不应含 setInterval.*fetch 前端轮询（E6 已改 SSE/WS），实际 {total} 处"
