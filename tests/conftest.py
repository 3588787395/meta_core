"""pytest 共享 fixture 与测试工厂函数。"""
import sys
from datetime import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# 确保 meta_core 在 sys.path
_META_CORE = Path(__file__).resolve().parent.parent
if str(_META_CORE) not in sys.path:
    sys.path.insert(0, str(_META_CORE))

# HQChartPy2 依赖本地 C++ 扩展，若当前环境无法加载则跳过相关脚本级测试文件
#（它们不是 pytest 函数式测试，而是引擎验证脚本）。
try:
    import HQChartPy2  # noqa: F401
except Exception:
    collect_ignore = ["test_engine_real.py", "test_quick.py"]

# 兼容旧测试的模块别名（不创建新文件，仅注册到 sys.modules）
if 'tdx_evaluators' not in sys.modules:
    try:
        from meta_core.core import evaluators as _evaluators_module
        sys.modules['tdx_evaluators'] = _evaluators_module
    except Exception:
        pass
if 'schemas' not in sys.modules:
    try:
        from meta_core.core import schemas as _schemas_module
        sys.modules['schemas'] = _schemas_module
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 行情数据/结果提取辅助函数
# ---------------------------------------------------------------------------

def make_sample_bar_data(codes: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """构造默认行情数据。"""
    if codes is None:
        codes = ['600000', '000001', '000002']
    bar_data: Dict[str, Dict[str, Any]] = {}
    defaults = {
        '600000': {'open': 10.0, 'high': 10.5, 'low': 9.9, 'close': 10.2, 'volume': 10000, 'amount': 102000, 'pre_close': 10.0},
        '000001': {'open': 15.0, 'high': 15.3, 'low': 14.8, 'close': 15.1, 'volume': 8000, 'amount': 120800, 'pre_close': 15.0},
        '000002': {'open': 8.0, 'high': 8.2, 'low': 7.9, 'close': 8.1, 'volume': 12000, 'amount': 97200, 'pre_close': 8.0},
    }
    for code in codes:
        bar_data[code] = dict(defaults.get(code, {
            'open': 10.0, 'high': 10.2, 'low': 9.9, 'close': 10.1,
            'volume': 10000, 'amount': 101000, 'pre_close': 10.0,
        }))
    return bar_data


def extract_stock_codes(result: Dict[str, Any], node_id: str) -> List[str]:
    """从 run_pool 结果中提取指定节点的股票代码列表。"""
    node_states = result.get('node_states', {}) if isinstance(result, dict) else {}
    node = node_states.get(node_id, {}) if isinstance(node_states, dict) else {}
    stocks = node.get('stocks', []) if isinstance(node, dict) else []
    return [s.get('code', '') for s in stocks if isinstance(s, dict) and s.get('code')]


def extract_node_stocks(result: Dict[str, Any], node_id: str) -> List[Dict[str, Any]]:
    """从 run_pool 结果中提取指定节点的股票详情列表。"""
    node_states = result.get('node_states', {}) if isinstance(result, dict) else {}
    node = node_states.get(node_id, {}) if isinstance(node_states, dict) else {}
    stocks = node.get('stocks', []) if isinstance(node, dict) else []
    return [s for s in stocks if isinstance(s, dict)]


# ---------------------------------------------------------------------------
# 股票池配置工厂函数
# ---------------------------------------------------------------------------

_DEFAULT_EDGE_PARAMS = {
    'tran': 0,
    'emptyps': 0,
    'starttype': 0,
    'starttime': 0,
    'starttimetype': 0,
    'starttimehms': 0,
    'cxtype': 0,
    'cxtime': 0,
    'cxtimetype': 0,
    'jgtime': 0,
}

_DEFAULT_PSATT = {
    'bdel': 0,
    'ndelnum': 0,
    'ndeltype': 0,
    'baimpool': 0,
    'bsound': 0,
    'nsoundtype': 0,
    'nsyssound': 0,
    'soundfile': '',
    'btip': 0,
    'bsavetoblock': 0,
    'blockfile': '',
    'bclearblock': 0,
    'bsavehis': 0,
}


def _make_stock(code: str, label: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    stock = {'code': code, 'label': label or code}
    stock.update(extra)
    return stock


def _make_node(nid: str, ntype: Any, dzh_type: int, name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        'id': nid,
        'type': str(ntype),
        'dzh_cell_type': dzh_type,
        'text': name,
        'attr': 0,
        'pos': '0,0,200,100',
        'params': params or {},
    }


def _make_edge(eid: str, from_id: str, to_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    p = dict(_DEFAULT_EDGE_PARAMS)
    if params:
        p.update(params)
    return {
        'id': eid,
        'from': from_id,
        'to': to_id,
        'attr': 0,
        'params': p,
    }


def make_tdx_simple_pool(
    candidate_stocks: Optional[List[Dict[str, Any]]] = None,
    direct_edge: bool = False,
    edge_tran: int = 0,
    edge_starttype: int = 0,
    condition_nset: int = 4,
    condition_params: Optional[Dict[str, Any]] = None,
    psatt_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造一个简单的 TDX 股票池：候选池 -> [条件] -> 状态池。"""
    if candidate_stocks is None:
        candidate_stocks = [
            _make_stock('600000', '浦发银行'),
            _make_stock('000001', '平安银行'),
            _make_stock('000002', '万科A'),
        ]

    psatt = dict(_DEFAULT_PSATT)
    if psatt_params:
        psatt.update(psatt_params)

    func = {
        'nset': condition_nset,
        'ntjindexno': 0,
        'noperate': 1,
        'fsecond': 10.0,
        'accode': '现价',
    }
    if condition_params:
        func.update(condition_params)

    nodes: List[Dict[str, Any]] = [
        _make_node('candidate_1', '7', 7, '候选池', {
            'tdx_spinfo': {'type': 0, 'customblockname': '', 'size': 0, 'market': '', 'sector_type': 0},
            'stocks': list(candidate_stocks),
        }),
        _make_node('state_pool_1', '8', 8, '状态池1', {
            'tdx_psatt': psatt,
            'stocks': [],
        }),
    ]
    edges: List[Dict[str, Any]] = []

    edge_common = {'tran': edge_tran, 'starttype': edge_starttype}

    if direct_edge:
        edges.append(_make_edge('edge_candidate_to_state', 'candidate_1', 'state_pool_1', edge_common))
    else:
        nodes.insert(1, _make_node('condition_1', '3', 201, '条件1', {
            'tdx_func': func,
        }))
        edges.append(_make_edge('edge_candidate_to_condition', 'candidate_1', 'condition_1', edge_common))
        edges.append(_make_edge('edge_condition_to_state', 'condition_1', 'state_pool_1', edge_common))

    return {
        'id': 'test_tdx_simple',
        'name': '简单TDX测试池',
        'nodes': nodes,
        'edges': edges,
    }


def make_tdx_unconditional_pool(
    candidate_stocks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """构造一个无条件边股票池：候选池 -> 状态池（直接无条件传播）。"""
    if candidate_stocks is None:
        candidate_stocks = [
            _make_stock('600000', '浦发银行'),
            _make_stock('000001', '平安银行'),
        ]
    return make_tdx_simple_pool(
        candidate_stocks=candidate_stocks,
        direct_edge=True,
        psatt_params={'bsavehis': 1},
    )


def make_tdx_ttl_pool(
    candidate_stocks: Optional[List[Dict[str, Any]]] = None,
    ttl_ndeltype: int = 3,
    ttl_ndelnum: int = 3600,
    direct_edge: bool = False,
    edge_tran: int = 0,
) -> Dict[str, Any]:
    """构造带 TTL 的 TDX 股票池（默认 2 只候选股，便于 TTL 计数断言）。

    为避免仿真驱动每 tick 生成 mock 行情触发源节点边持续重入，边默认设为
    cxtype=2（仅执行一次），使 TTL 过期后状态池保持为空。
    """
    if candidate_stocks is None:
        candidate_stocks = [
            _make_stock('600000', '浦发银行'),
            _make_stock('000001', '平安银行'),
        ]
    psatt = dict(_DEFAULT_PSATT)
    psatt.update({'bdel': 1, 'ndelnum': ttl_ndelnum, 'ndeltype': ttl_ndeltype})
    config = make_tdx_simple_pool(
        candidate_stocks=candidate_stocks,
        direct_edge=direct_edge,
        edge_tran=edge_tran,
        psatt_params=psatt,
    )
    for edge in config.get('edges', []):
        edge.setdefault('params', {}).update({'cxtype': 2})
    return config


def make_tdx_multi_level_pool() -> Dict[str, Any]:
    """构造多级 TDX 股票池：候选池 -> 条件A -> 状态池A -> 条件B -> 状态池B。"""
    stocks = [
        _make_stock('600000', '浦发银行'),
        _make_stock('000001', '平安银行'),
        _make_stock('000002', '万科A'),
        _make_stock('600036', '招商银行'),
        _make_stock('601318', '中国平安'),
    ]
    psatt = dict(_DEFAULT_PSATT)
    return {
        'id': 'test_tdx_multi_level',
        'name': '多级TDX测试池',
        'nodes': [
            _make_node('candidate_1', '7', 7, '候选池', {
                'tdx_spinfo': {'type': 0, 'customblockname': '', 'size': 0, 'market': '', 'sector_type': 0},
                'stocks': stocks,
            }),
            _make_node('condition_A', '3', 201, '条件A', {
                'tdx_func': {'nset': 4, 'ntjindexno': 0, 'noperate': 1, 'fsecond': 5.0, 'accode': '现价'},
            }),
            _make_node('state_A', '8', 8, '状态池A', {'tdx_psatt': psatt, 'stocks': []}),
            _make_node('condition_B', '3', 201, '条件B', {
                'tdx_func': {'nset': 4, 'ntjindexno': 0, 'noperate': 1, 'fsecond': 8.0, 'accode': '现价'},
            }),
            _make_node('state_B', '8', 8, '状态池B', {'tdx_psatt': psatt, 'stocks': []}),
        ],
        'edges': [
            _make_edge('e_ca', 'candidate_1', 'condition_A'),
            _make_edge('e_as', 'condition_A', 'state_A'),
            _make_edge('e_sb', 'state_A', 'condition_B'),
            _make_edge('e_bs', 'condition_B', 'state_B'),
        ],
    }


def make_dzh_simple_pool(
    candidate_stocks: Optional[List[Dict[str, Any]]] = None,
    state_pool_params: Optional[Dict[str, Any]] = None,
    flow_attr: Optional[int] = None,
    condition_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造一个简单的 DZH 股票池：备选池 -> [条件] -> 状态池。

    当传入 ``condition_params`` 时，会在备选池和状态池之间插入条件节点，
    供需要条件节点（dzh_cell_type=201）的测试使用。
    """
    if candidate_stocks is None:
        candidate_stocks = [_make_stock('600000', '浦发银行')]

    state_params: Dict[str, Any] = {'stocks': []}
    if state_pool_params:
        state_params.update(state_pool_params)

    psatt = dict(_DEFAULT_PSATT)
    psatt.update({k: v for k, v in state_params.items() if k in _DEFAULT_PSATT})
    state_params['tdx_psatt'] = psatt

    func = {
        'nset': 4,
        'ntjindexno': 0,
        'noperate': 1,
        'fsecond': 10.0,
        'accode': '现价',
    }
    cond_extra_params: Dict[str, Any] = {}
    if condition_params:
        func.update(condition_params)
        # 部分测试期望 sort_type 等字段同时出现在节点 params 层级
        for k in ('sort_type',):
            if k in condition_params:
                cond_extra_params[k] = condition_params[k]

    nodes: List[Dict[str, Any]] = [
        _make_node('candidate_1', '202', 202, '备选池', {
            'stocks': list(candidate_stocks),
        }),
    ]
    edges: List[Dict[str, Any]] = []

    if condition_params:
        nodes.append(_make_node('condition_1', '201', 201, '条件1', {
            'tdx_func': func,
            **cond_extra_params,
        }))
        nodes.append(_make_node('state_pool_1', '200', 200, '状态池1', state_params))
        edges.append(_make_edge('e_c_cond', 'candidate_1', 'condition_1'))
        edges.append(_make_edge('e_cond_state', 'condition_1', 'state_pool_1'))
    else:
        nodes.append(_make_node('state_pool_1', '200', 200, '状态池1', state_params))
        edge = _make_edge('e_candidate_state', 'candidate_1', 'state_pool_1')
        if flow_attr is not None:
            edge['attr'] = flow_attr
        edges.append(edge)

    return {
        'id': 'test_dzh_simple',
        'name': '简单DZH测试池',
        'nodes': nodes,
        'edges': edges,
    }


def make_dzh_multi_condition_pool() -> Dict[str, Any]:
    """构造多条件 DZH 股票池：备选池 -> 条件1 -> 状态1 -> 条件2 -> 状态2。"""
    stocks = [
        _make_stock('600000', '浦发银行'),
        _make_stock('000001', '平安银行'),
    ]
    psatt = dict(_DEFAULT_PSATT)
    func1 = {'nset': 4, 'ntjindexno': 0, 'noperate': 1, 'fsecond': 5.0, 'accode': '现价'}
    func2 = {'nset': 4, 'ntjindexno': 0, 'noperate': 1, 'fsecond': 8.0, 'accode': '现价'}
    return {
        'id': 'test_dzh_multi_condition',
        'name': '多条件DZH测试池',
        'nodes': [
            _make_node('candidate_1', '202', 202, '备选池', {'stocks': stocks}),
            _make_node('condition_1', '201', 201, '条件1', {'tdx_func': func1}),
            _make_node('state_1', '200', 200, '状态1', {'tdx_psatt': psatt, 'stocks': []}),
            _make_node('condition_2', '201', 201, '条件2', {'tdx_func': func2}),
            _make_node('state_2', '200', 200, '状态2', {'tdx_psatt': psatt, 'stocks': []}),
        ],
        'edges': [
            _make_edge('e_c1', 'candidate_1', 'condition_1'),
            _make_edge('e_1s', 'condition_1', 'state_1'),
            _make_edge('e_s2', 'state_1', 'condition_2'),
            _make_edge('e_2s', 'condition_2', 'state_2'),
        ],
    }


def make_ths_simple_pool(
    candidate_stocks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """构造一个简单的同花顺测试池（兼容占位）。"""
    return make_tdx_simple_pool(candidate_stocks=candidate_stocks, direct_edge=True)


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 时机门控测试助手（I31：替代死集群 _tdx_should_execute / _tdx_check_duration）
# 直接调用生产路径 edge_executor._starttype_gate / _CXTYPE_POST_GATES，
# 与 run_pool → EdgeExecutor._gate 完全一致，消除 247 行死集群依赖。
# ---------------------------------------------------------------------------

def gate_starttype_passes(engine, edge, mock_time=None, pool_start=None):
    """测试助手：直接调用生产 _starttype_gate（替代死集群 _tdx_should_execute）。

    从 edge['params'] 编译 TimingSpec，设置 state.time_source 后调用
    edge_executor._starttype_gate，与生产 run_pool 路径完全一致。

    Args:
        engine: MetaEngine 实例
        edge: 边字典（含 params.starttype/starttime/starttimetype/starttimehms）
        mock_time: 模拟当前时间（datetime），None 用 wall_clock
        pool_start: 池启动时间（datetime），用于 starttype=1 elapsed，None=now

    Returns:
        bool: True=starttype 门控放行
    """
    from meta_core.core.compiler import Compiler
    from meta_core.core.edge_executor import _starttype_gate, _current_seconds_of_day
    from meta_core.core.time_util import _safe_timestamp

    spec = Compiler._build_timing_spec(edge)
    pe = engine._pool_engine
    if pe is None:
        pe = engine._ensure_pool_engine({'id': '_gate_test_', 'nodes': [], 'edges': []})
    now_dt = mock_time if mock_time is not None else _dt.now()
    now_ts = _safe_timestamp(now_dt)
    start_ts = _safe_timestamp(pool_start) if pool_start is not None else now_ts
    pe.state.time_source = {
        'driver_type': 'wall_clock',
        'current_ts': now_ts,
        'start_ts': start_ts,
        'kind': 'test',
    }
    eid = edge.get('id', '') or 'test_edge'
    # I42：与生产 _gate 一致，双时间值传入（now_unix=now_ts, now_sec 由 time_at 决议）
    now_sec = _current_seconds_of_day(now_ts)
    return _starttype_gate(spec, pe.state, eid, now_ts, now_sec)


def gate_duration_expired(engine, edge, mock_time=None, first_fire=None, exec_count=None):
    """测试助手：直接调用生产 _CXTYPE_POST_GATES[cxtype]（替代死集群 _tdx_check_duration）。

    返回 True=已过期（不应执行），与 _tdx_check_duration 语义一致。

    Args:
        engine: MetaEngine 实例
        edge: 边字典（含 params.cxtype/cxtime/cxtimetype）
        mock_time: 模拟当前时间（datetime），None 用现有 time_source
        first_fire: 首次触发时间（datetime 或 float Unix ts），None=不设置
        exec_count: 执行计数（int），None=不设置

    Returns:
        bool: True=已过期（cxtype 后置门控拒绝）
    """
    from meta_core.core.compiler import Compiler
    from meta_core.core.edge_executor import _CXTYPE_POST_GATES, _cxtype_forever, _now_ts
    from meta_core.core.time_util import _safe_timestamp

    spec = Compiler._build_timing_spec(edge)
    pe = engine._pool_engine
    if pe is None:
        pe = engine._ensure_pool_engine({'id': '_gate_test_', 'nodes': [], 'edges': []})

    if mock_time is not None:
        now_ts = _safe_timestamp(mock_time)
        pe.state.time_source = {
            'driver_type': 'wall_clock',
            'current_ts': now_ts,
            'start_ts': now_ts,
            'kind': 'test',
        }
    else:
        now_ts = _now_ts(pe.state)

    eid = edge.get('id', '') or 'test_edge'
    exec_ctx = pe.state.get_exec_ctx(eid)
    if first_fire is not None:
        exec_ctx['first_fire'] = (
            _safe_timestamp(first_fire) if hasattr(first_fire, 'timestamp') else float(first_fire)
        )
    if exec_count is not None:
        exec_ctx['count'] = int(exec_count)

    handler = _CXTYPE_POST_GATES.get(spec.cxtype, _cxtype_forever)
    return not handler(spec, exec_ctx, now_ts)


@pytest.fixture(scope="module")
def local_provider():
    """LocalFileProvider 实例（模块级缓存）。

    依赖本地安装通达信/大智慧/同花顺客户端，未安装时跳过测试。
    """
    from services.providers.local_file_provider import LocalFileProvider
    provider = LocalFileProvider()
    if not provider.is_ready():
        pytest.skip("本地客户端未安装，跳过依赖本地文件的测试")
    return provider


@pytest.fixture
def engine():
    """返回一个重置状态的 MetaEngine 实例。"""
    from meta_core.core.engine import MetaEngine
    eng = MetaEngine()
    # 重置运行时状态，确保每次测试独立
    eng._flow_exec_counts = {}
    eng._flow_first_fire_ts = {}
    eng.events = []
    return eng


@pytest.fixture
def synthetic_kline():
    """生成用于指标计算的合成日 K 线数据（pandas DataFrame）。"""
    import pandas as pd
    import numpy as np

    np.random.seed(42)
    n = 60
    base = 10.0
    close = base + np.cumsum(np.random.randn(n) * 0.2)
    close = np.maximum(close, 5.0)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    low = np.maximum(low, 1.0)
    open_ = low + np.random.rand(n) * (high - low)
    volume = np.random.randint(1_000_000, 10_000_000, size=n)
    amount = close * volume

    df = pd.DataFrame({
        'open': open_,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume,
        'amount': amount,
    })
    df.index = pd.date_range(end=pd.Timestamp('2026-06-30'), periods=n, freq='D')
    return df


@pytest.fixture
def multi_stock_klines():
    """生成多只股票合成 K 线数据，返回 {code: DataFrame}。"""
    import pandas as pd
    import numpy as np

    np.random.seed(7)
    n = 60
    result = {}
    for code, base in [('600000', 10.0), ('000001', 15.0), ('000002', 8.0)]:
        close = base + np.cumsum(np.random.randn(n) * 0.15)
        close = np.maximum(close, base * 0.5)
        high = close + np.abs(np.random.randn(n) * 0.25)
        low = close - np.abs(np.random.randn(n) * 0.25)
        low = np.maximum(low, 1.0)
        open_ = low + np.random.rand(n) * (high - low)
        volume = np.random.randint(500_000, 5_000_000, size=n)
        amount = close * volume
        df = pd.DataFrame({
            'open': open_,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume,
            'amount': amount,
        })
        df.index = pd.date_range(end=pd.Timestamp('2026-06-30'), periods=n, freq='D')
        result[code] = df
    return result
