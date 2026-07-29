"""metatest 共享 pytest fixture。

提供 8 个 fixture，全部复用 ``core/`` 现有类，禁止兼容已删除旧接口：
  - ``virtual_clock``：虚拟时钟对象，起点 ``34500.0``（=09:30:00），提供 ``advance(seconds)``
  - ``fz_stocks``：工厂 fixture，``fz_stocks(n=100)`` 返回 ``fz000001`` ~ ``fz000100``
    共 n 个 ``fz`` 前缀股票代码（直接生成，不依赖配置文件）
  - ``pool_engine``：工厂 fixture，``pool_engine(pool_config_path=...)`` 装配并返回
    ``PoolEngine`` 实例（复用 ``core.engine.PoolEngine``，内部完成 EventBus /
    EventDriver / EdgeExecutor / CompiledSchedule 依赖注入）
  - ``event_collector``：提供事件收集器，创建独立 EventBus 并订阅全部事件，
    返回 ``(bus, collected)`` 元组（v2：订阅所有事件用于断言）
  - ``tick_table``：提供干净的 ``TickTable`` 实例（v2 新增）
  - ``compiled_pool``：提供预编译的 ``CompiledPool`` 实例（v2 新增，最小化测试池配置）
  - ``signal_collector``：提供信号收集器，订阅 ``Signal`` 事件，返回 ``(bus, collected)``
    （v2 新增）
  - ``pool_snapshot``：工厂 fixture，``pool_snapshot(engine, pool_name)`` 返回
    指定池节点的当前股票代码列表（经 ``engine.state.get_pool(nid).get_stock_codes()``）
  - ``fastapi_client``：返回 ``fastapi.testclient.TestClient(app)``（参考 ``app.py``）
  - ``playwright_browser``：返回 Playwright browser 实例（未安装时 skip）
  - ``config_store``：返回 ``ConfigStore`` 实例（参考 ``core.table_engine``）

另提供 ``report_state`` fixture，暴露共享报告状态字典，供合测试填充量化数据，
``metatest/runner.py`` 在测试运行后读取以生成量化评分报告。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest


# ---------------------------------------------------------------------------
# 项目根目录定位（meta_core/），使配置路径与 CWD 无关
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_FIXTURES_DIR = _THIS_DIR / "fixtures"
_DEFAULT_POOL_CONFIG = _FIXTURES_DIR / "test_pool_config.json"

# 确保项目根在 sys.path 上，使 ``from core.engine import PoolEngine`` 等导入可用
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 共享报告状态：runner.py 启动时初始化为空 dict，合测试通过 ``report_state``
# fixture 写入实际量化数据，运行器在测试结束后读取以生成评分报告。
# ---------------------------------------------------------------------------
def _new_report_state() -> Dict[str, Any]:
    return {
        "modules_covered": [],
        "event_types_seen": [],
        "event_chain_correct": False,
        "sim_1000_tick_time_s": 0.0,
        "frontend_e2e_passed": 0,
        "frontend_e2e_total": 0,
    }


# 模块级单例：pytest.main() 在同进程内运行，状态跨 fixture 持久。
REPORT_STATE: Dict[str, Any] = _new_report_state()


# ---------------------------------------------------------------------------
# VirtualClock —— 仿真模式虚拟时钟（起点 34500.0 = 09:30:00）
# ---------------------------------------------------------------------------


class VirtualClock:
    """可推进的虚拟时钟对象。

    仿真模式虚拟时钟起点 ``34500.0``（=当日 09:30:00 的秒数偏移），
    与 ``core.runtime_mode_module.RuntimeSimulator.clock`` 语义一致。
    ``advance(seconds)`` 推进时钟并返回新时刻。
    ``__float__`` 使其可直接作为数值参与时间比较。
    """

    def __init__(self, start: float = 34500.0) -> None:
        self.start: float = float(start)
        self.now: float = float(start)

    def advance(self, seconds: float) -> float:
        """推进虚拟时钟 ``seconds`` 秒，返回推进后的时刻。"""
        self.now += float(seconds)
        return self.now

    def reset(self) -> float:
        """重置到起点。"""
        self.now = self.start
        return self.now

    def __float__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# EventCollector —— 订阅 EventBus 全部事件并收集
# ---------------------------------------------------------------------------


class EventCollector:
    """订阅 EventBus 全部事件类型并收集。

    通过 ``EventBus.subscribe_any`` 注册通配订阅者，收集所有已发布事件。
    提供 ``.events``（按时间戳排序）/ ``.count_by_type()`` / ``.filter()`` /
    ``.clear()`` 接口，供正反合测试断言事件链顺序与计数。
    """

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._events: List[Any] = []
        # subscribe_any 返回取消订阅函数，保留以便 disconnect
        self._unsubscribe: Optional[Callable[[], None]] = bus.subscribe_any(self._on_event)

    def _on_event(self, event: Any) -> None:
        self._events.append(event)

    @staticmethod
    def _event_ts(event: Any) -> float:
        ts = getattr(event, "ts", None)
        if isinstance(ts, (int, float)):
            return float(ts)
        return 0.0

    @property
    def events(self) -> List[Any]:
        """按时间戳排序的事件列表（同 ts 按发布顺序稳定排序）。"""
        return sorted(self._events, key=lambda e: (self._event_ts(e),))

    def count_by_type(self) -> Dict[str, int]:
        """按事件类型名分组计数，返回 ``Dict[str, int]``。"""
        counts: Dict[str, int] = {}
        for ev in self._events:
            name = type(ev).__name__
            counts[name] = counts.get(name, 0) + 1
        return counts

    def filter(
        self,
        code: Optional[str] = None,
        type: Optional[str] = None,
    ) -> List[Any]:
        """过滤事件。

        Args:
            code: 仅保留携带该股票代码的事件（事件有 ``code`` 属性且相等）；
                  None 表示不过滤代码。
            type: 事件类型名（如 ``"TickReceived"``）；None 表示不过滤类型。

        Returns:
            过滤后的事件列表（按时间戳排序）。
        """
        result: List[Any] = []
        for ev in self._events:
            if type is not None and ev.__class__.__name__ != type:
                continue
            if code is not None:
                ev_code = getattr(ev, "code", None)
                if ev_code != code:
                    continue
            result.append(ev)
        return sorted(result, key=lambda e: (self._event_ts(e),))

    def clear(self) -> None:
        """清空已收集事件。"""
        self._events.clear()

    def disconnect(self) -> None:
        """取消 EventBus 订阅（清理资源）。"""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def virtual_clock() -> VirtualClock:
    """虚拟时钟对象，起点 ``34500.0``（=09:30:00），提供 ``advance(seconds)`` 推进方法。

    ``float(virtual_clock) == 34500.0``，可直接用于时间比较与时间源初始化。
    """
    return VirtualClock(start=34500.0)


@pytest.fixture
def fz_stocks() -> Callable[..., List[str]]:
    """工厂 fixture：``fz_stocks(n=100)`` 返回 n 个 ``fz`` 前缀股票代码列表。

    代码格式为 ``fz000001`` ~ ``fz000100``（6 位数字补零），直接生成，
    不依赖配置文件，使 fixture 自包含可复用。
    """

    def _fz_stocks(n: int = 100) -> List[str]:
        if n <= 0:
            return []
        return [f"fz{i:06d}" for i in range(1, n + 1)]

    return _fz_stocks


@pytest.fixture
def pool_engine() -> Callable[..., Any]:
    """工厂 fixture：``pool_engine(pool_config_path=...)`` 装配并返回 ``PoolEngine`` 实例。

    复用 ``core.engine.PoolEngine``，通过 ``pool_config=cfg`` 构造触发
    ``_init_pool_runtime``，内部完成 EventBus / EventDriver / EdgeExecutor /
    CompiledSchedule / FormulaEngine / DataUpdater / BarComposer / TradeModule
    等组件的依赖注入。

    默认加载 ``metatest/fixtures/test_pool_config.json``；可传入自定义路径。
    """

    def _pool_engine(
        pool_config_path: str = str(_DEFAULT_POOL_CONFIG),
    ) -> Any:
        cfg_path = Path(pool_config_path)
        if not cfg_path.is_absolute():
            cfg_path = _PROJECT_ROOT / cfg_path
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        from core.engine import PoolEngine
        from core.tick_bar_module import TickBarModule

        engine = PoolEngine(pool_config=cfg)
        bus = engine._components["event_bus"]
        tick_bar = TickBarModule(bus=bus)
        engine._components["tick_bar_module"] = tick_bar
        return engine

    return _pool_engine


@pytest.fixture
def event_collector():
    """提供事件收集器工厂：``event_collector(bus)`` 返回 ``EventCollector`` 实例。

    测试传入自己的 ``EventBus``，工厂创建 ``EventCollector`` 并订阅该 bus
    的全部事件，返回收集器对象（支持 ``.events`` / ``._events`` /
    ``.count_by_type()`` / ``.filter()`` / ``.disconnect()``）。
    """
    def _factory(bus):
        return EventCollector(bus)
    return _factory


@pytest.fixture
def pool_snapshot() -> Callable[..., List[str]]:
    """工厂 fixture：``pool_snapshot(engine, pool_name)`` 返回指定池的当前股票代码列表。

    通过 ``engine.state.get_pool(pool_name).get_stock_codes()`` 获取（StatePoolView 视图接口）。
    返回排序后的代码列表，便于断言比对。
    """

    def _pool_snapshot(engine: Any, pool_name: str) -> List[str]:
        pool = engine.state.get_pool(pool_name)
        return sorted(pool.get_stock_codes())

    return _pool_snapshot


@pytest.fixture
def fastapi_client() -> Any:
    """返回 ``fastapi.testclient.TestClient(app)``，用于 API 层正反合测试。

    导入 ``app.py`` 的 ``app`` 对象；若 FastAPI 或 app 模块不可用则 skip。
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    try:
        from app import app
    except ImportError as exc:
        pytest.skip(f"无法导入 app.py 的 app 对象: {exc}")
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def playwright_browser() -> Any:
    """返回 Playwright browser 实例，用于前端 E2E 合测试。

    优先使用 ``pytest-playwright`` 插件提供的 ``browser`` fixture；
    若插件未安装则回退到 ``playwright.sync_api`` 手动启动。
    两者均不可用时 skip，使无前端环境下的纯后端测试不受影响。
    """
    pytest.importorskip("playwright")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright 未安装，跳过前端 E2E 测试")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:
        pw.stop()
        pytest.skip(f"Playwright browser 启动失败: {exc}")
    yield browser
    try:
        browser.close()
    finally:
        pw.stop()


@pytest.fixture
def config_store() -> Any:
    """返回 ``ConfigStore`` 实例（参考 ``core.table_engine``）。

    装配独立 ``ConfigStore``（无 EventBus 注入），自动加载 ``config/`` 目录。
    供配置层正反合测试与 runner 的模块覆盖统计使用。
    """
    from core.table_engine import ConfigStore
    return ConfigStore()


@pytest.fixture(scope="session")
def report_state() -> Dict[str, Any]:
    """暴露共享报告状态字典，供合测试填充量化数据。

    返回模块级 ``REPORT_STATE`` 单例，合测试在仿真运行后写入：
      - ``modules_covered``: ``List[str]`` 被测试覆盖的模块名
      - ``event_types_seen``: ``List[str]`` 出现的事件类型名
      - ``event_chain_correct``: ``bool`` 事件链顺序是否正确
      - ``sim_1000_tick_time_s``: ``float`` 仿真 1000 tick 耗时
      - ``frontend_e2e_passed``: ``int`` 前端 E2E 通过数
      - ``frontend_e2e_total``: ``int`` 前端 E2E 总数

    ``metatest/runner.py`` 在测试运行结束后读取以生成 6 维评分报告。

    session-scoped：使 module-scoped 合测试 fixture 能引用此 fixture
    （function-scoped fixture 不能被 module-scoped fixture 引用，会触发 ScopeMismatch）。
    ``REPORT_STATE`` 本身就是模块级单例，session scope 不改变其语义。
    """
    return REPORT_STATE


# ---------------------------------------------------------------------------
# web_server_url —— 启动 uvicorn 服务器供前端 E2E 测试使用
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def web_server_url():
    """启动 FastAPI 服务器并返回基础 URL；启动失败则 skip。

    供前端 E2E 测试（test_frontend_*.py）使用。在后台线程启动 uvicorn，
    最多等待 5s 服务器就绪；若 uvicorn 或 app 不可导入则 skip。
    """
    pytest.importorskip("uvicorn")
    try:
        from app import app
    except ImportError as exc:
        pytest.skip(f"无法导入 app: {exc}")

    import socket
    import threading
    import time
    import uvicorn

    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    port = _find_free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5.0
    ready = False
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except (OSError, ConnectionError):
            time.sleep(0.1)
    if not ready:
        pytest.skip("uvicorn 服务器未能在 5s 内启动")

    yield url

    server.should_exit = True
    thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# 辅助 fixture：在 page 上打开主页
# ---------------------------------------------------------------------------


@pytest.fixture
def home_page(playwright_browser, web_server_url):
    """打开主页并返回 page 对象；测试结束自动关闭。

    等待顶部工具栏（``#topbar``）可见后返回，``#topbar`` 在设计模式下默认可见，
    而 ``nav.top-nav``（hash 路由入口）默认 ``display:none`` 故不可作为就绪信号。
    """
    page = playwright_browser.new_page()
    page.goto(web_server_url, wait_until="domcontentloaded", timeout=10000)
    # #topbar 在主页视图设计模式下默认可见；nav.top-nav 由 CSS 隐藏故不可用作就绪信号
    page.wait_for_selector("#topbar", timeout=8000)
    yield page
    page.close()


# ---------------------------------------------------------------------------
# v2 新增 fixture：tick_table / compiled_pool / signal_collector
# ---------------------------------------------------------------------------


@pytest.fixture
def tick_table():
    """提供干净的 TickTable 实例。

    复用 ``core.runtime_mode_module.TickTable``，每次测试获得独立实例，
    供水位线（waterline）相关底层逻辑测试使用。
    """
    from core.runtime_mode_module import TickTable
    return TickTable()


@pytest.fixture
def compiled_pool():
    """提供预编译的 CompiledPool 实例（使用示例池配置）。

    复用 ``core.execution_module.compile``，使用最小化的测试池配置
    （源节点 → 目标节点单边），一次性产出扁平 CompiledPool，
    供编译-运行分离、三要素等底层逻辑测试使用。
    """
    from core.execution_module import compile
    # 使用最小化的测试池配置
    # starttype/cxtype 为整数（0=立即/始终），与 _compile_timing_spec 的 int() 要求一致
    pool_config = {
        "nodes": {
            "src": {"type": "candidate", "label": "源节点"},
            "tgt": {"type": "target", "label": "目标节点"},
        },
        "edges": [
            {"id": "e1", "from": "src", "to": "tgt",
             "params": {"_order": 0, "starttype": 0, "cxtype": 0}},
        ],
    }
    return compile(pool_config)


@pytest.fixture
def signal_collector():
    """提供信号收集器，订阅 Signal 事件用于断言。

    创建独立 EventBus 并订阅 ``Signal`` 事件，返回 ``(bus, collected)`` 元组：
      - ``bus``: 新建的 EventBus 实例，测试可向其发布 Signal
      - ``collected``: 已收集的 Signal 事件列表，测试可断言信号派生与分发

    供正交化（StockChanged → Signal → SignalDeriver → ActionDispatcher）
    底层逻辑测试使用。
    """
    from core.event_bus import EventBus, Signal
    collected: List[Any] = []
    bus = EventBus()
    bus.subscribe(Signal, lambda s: collected.append(s))
    return bus, collected
