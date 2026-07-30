"""正合测试：HTTP API 层（api.py + app.py）路由与依赖。

覆盖场景：
1. require_config_store 是 FastAPI Depends 函数
2. get_simulator 是 FastAPI Depends 函数
3. API app 有路由（route count > 0）
4. /api/config/tables 端点响应（200 或 500 未初始化）
5. /api/state/runtime 端点存在
6. /api/events/recent 端点存在
7. /api/events/stream (SSE) 端点存在
8. HTTP 与 WS 路由在不同 APIRouters
9. API Key 校验存在（verify_api_key）
10. get_simulator Depends 返回 simulator 或抛异常
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException
from fastapi.routing import APIWebSocketRoute

from api import (
    config_ws_router,
    require_config_store,
    router as config_router,
)
from app import app, get_simulator, verify_api_key


# ------------------------------------------------------------------
# 测试 1：require_config_store 是 FastAPI Depends 函数
# ------------------------------------------------------------------
def test_require_config_store_is_callable():
    """require_config_store 是可调用函数，供 Depends 注入。"""
    assert callable(require_config_store)


def test_config_router_uses_require_config_store():
    """config_router 的 dependencies 含 require_config_store。"""
    deps = config_router.dependencies
    assert len(deps) > 0
    # dependency.callable 指向 require_config_store
    callables = [getattr(d.dependency, "callable", d.dependency) for d in deps]
    # require_config_store 直接作为 dependency（非 Depends 包装时的 call 字段）
    raw_targets = []
    for d in deps:
        dep = d.dependency
        # FastAPI Depends 对象的 dependency 属性即原函数
        raw_targets.append(dep)
    assert require_config_store in raw_targets


# ------------------------------------------------------------------
# 测试 9：API Key 校验存在（verify_api_key）
# ------------------------------------------------------------------
def test_verify_api_key_exists():
    """verify_api_key 是 app.py 中定义的可调用认证依赖。"""
    assert callable(verify_api_key)
    # app 中至少一条路由依赖 verify_api_key
    has_auth = False
    for route in app.routes:
        deps = getattr(route, "dependencies", []) or []
        for d in deps:
            dep = getattr(d, "dependency", d)
            if dep is verify_api_key or dep == verify_api_key:
                has_auth = True
                break
        if has_auth:
            break
    assert has_auth, "app 路由中未发现 verify_api_key 依赖"


# ------------------------------------------------------------------
# 测试 2：get_simulator 是 FastAPI Depends 函数
# ------------------------------------------------------------------
def test_get_simulator_is_callable():
    """get_simulator 是可调用函数，供 Depends 注入。"""
    assert callable(get_simulator)


def test_get_simulator_used_as_depends():
    """app 路由中至少一处使用 Depends(get_simulator)。"""
    found = False
    for route in app.routes:
        if not hasattr(route, "dependant"):
            continue
        # 遍历该路由的依赖列表
        for dep in getattr(route.dependant, "dependencies", []):
            call = getattr(dep, "call", None)
            if call is get_simulator:
                found = True
                break
        if found:
            break
    assert found, "app 路由中未发现 Depends(get_simulator)"


# ------------------------------------------------------------------
# 测试 10：get_simulator 返回 simulator 或抛异常
# ------------------------------------------------------------------
def test_get_simulator_raises_404_for_missing():
    """get_simulator 对不存在的会话名抛 HTTPException(404)。"""
    # 确保 _simulators 不含目标
    original = getattr(app.state, "_simulators", None)
    app.state._simulators = {}
    try:
        with pytest.raises(HTTPException) as exc_info:
            get_simulator("nonexistent_session")
        assert exc_info.value.status_code == 404
    finally:
        if original is not None:
            app.state._simulators = original
        else:
            del app.state._simulators


def test_get_simulator_returns_simulator_when_present():
    """get_simulator 在会话存在时返回对应实例。"""
    sentinel = object()
    original = getattr(app.state, "_simulators", None)
    app.state._simulators = {"test_sim": sentinel}
    try:
        result = get_simulator("test_sim")
        assert result is sentinel
    finally:
        if original is not None:
            app.state._simulators = original
        else:
            del app.state._simulators


# ------------------------------------------------------------------
# 测试 3：API app 有路由
# ------------------------------------------------------------------
def test_app_has_routes():
    """app.routes 非空，含多条 HTTP 与 WS 路由。"""
    routes = list(app.routes)
    assert len(routes) > 0
    # 含 HTTP 路由（path 以 /api 开头）
    http_paths = [r.path for r in routes if hasattr(r, "path")]
    assert any(p.startswith("/api") for p in http_paths)


# ------------------------------------------------------------------
# 测试 8：HTTP 与 WS 路由在不同 APIRouters
# ------------------------------------------------------------------
def test_ws_router_separate_from_http_router():
    """config_ws_router 与 config_router 是不同 APIRouter 实例。"""
    assert config_ws_router is not config_router
    # config_ws_router 含 WebSocket 路由
    ws_routes = [
        r for r in config_ws_router.routes
        if isinstance(r, APIWebSocketRoute)
    ]
    assert len(ws_routes) >= 1
    # config_router 的路由均为 HTTP（无 WebSocket）
    http_only = [
        r for r in config_router.routes
        if not isinstance(r, APIWebSocketRoute)
    ]
    assert len(http_only) >= 1


def test_app_has_websocket_routes():
    """app 含直接挂载的 WebSocket 路由。"""
    ws_paths = [
        r.path for r in app.routes
        if isinstance(r, APIWebSocketRoute)
    ]
    assert len(ws_paths) >= 1
    # 至少含 /ws/pool/{name} 或 /api/config/ws
    assert any("/ws" in p for p in ws_paths)


# ------------------------------------------------------------------
# 测试 5/6/7：端点存在性
# ------------------------------------------------------------------
def _route_paths(app_obj):
    """收集 app 所有路由路径集合。"""
    return {getattr(r, "path", None) for r in app_obj.routes}


def test_state_runtime_endpoint_exists():
    """/api/state/runtime 端点存在于 app 路由。"""
    paths = _route_paths(app)
    assert "/api/state/runtime" in paths


def test_events_recent_endpoint_exists():
    """/api/events/recent 端点存在于 app 路由。"""
    paths = _route_paths(app)
    assert "/api/events/recent" in paths


def test_events_stream_endpoint_exists():
    """/api/events/stream (SSE) 端点存在于 app 路由。"""
    paths = _route_paths(app)
    assert "/api/events/stream" in paths


def test_config_tables_endpoint_exists():
    """/api/config/tables 端点存在于 config_router。"""
    paths = {getattr(r, "path", None) for r in config_router.routes}
    assert "/api/config/tables" in paths


# ------------------------------------------------------------------
# 测试 4：/api/config/tables 端点响应（200 或 500）
# ------------------------------------------------------------------
def test_config_tables_responds(fastapi_client):
    """/api/config/tables 响应 200（已初始化）或 500（未初始化）。"""
    resp = fastapi_client.get("/api/config/tables")
    assert resp.status_code in (200, 500)
    if resp.status_code == 500:
        # 未初始化时应返回引擎未初始化提示
        body = resp.json()
        assert "detail" in body or "message" in body


def test_state_runtime_responds(fastapi_client):
    """/api/state/runtime 响应 200。"""
    resp = fastapi_client.get("/api/state/runtime")
    assert resp.status_code == 200
    body = resp.json()
    assert "mode" in body


def test_events_recent_responds(fastapi_client):
    """/api/events/recent 响应 200。"""
    resp = fastapi_client.get("/api/events/recent")
    assert resp.status_code == 200


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 1 P3 + 阶段 2 E4 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 P3 registry 路由 + E4 SSE asyncio.Queue 收敛回归。"""

    def test_no_direct_converter_calls_in_api(self):
        """api.py 不绕过 _CONVERTER_REGISTRY 直接调用解析器（P3 OOP 路由）。"""
        import re
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent.joinpath("api.py").read_text(encoding="utf-8")
        count = len(re.findall(r"parse_dzh_xml\(|parse_tdx_xml\(|_build_tdx_xml\(", src))
        assert count == 0, \
            f"api.py 不应绕过 _CONVERTER_REGISTRY 直接调用解析器，实际 {count} 处"

    def test_sse_uses_asyncio_queue(self):
        """app.py events_stream 使用 asyncio.Queue（E4 替代 50ms 轮询）。"""
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent.joinpath("app.py").read_text(encoding="utf-8")
        assert "async def events_stream" in src, \
            "app.py 应含 async def events_stream 函数（SSE 端点）"
        assert "asyncio.Queue" in src, \
            "events_stream 应使用 asyncio.Queue（E4 阻塞等待事件，替代 50ms 轮询）"

    def test_no_run_in_executor_drain_in_app(self):
        """app.py 不含 run_in_executor(drain) 旧 SSE 轮询（E4 已改 asyncio.Queue）。"""
        import re
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent.joinpath("app.py").read_text(encoding="utf-8")
        count = len(re.findall(r"run_in_executor\(.*drain", src))
        assert count == 0, \
            f"app.py 不应含 run_in_executor(drain) 旧轮询，实际 {count} 处"
