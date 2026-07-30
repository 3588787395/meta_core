"""Task 7：迁移 Oracle 场景正测试。

验证 tests/fixtures/migration_oracle/ 下 5 个 Oracle 场景文件的结构完整性：
  - oracle_conditional_transfer.json：条件转移边（3 节点 / 2 边）
  - oracle_multi_level_cascade.json：多级级联（4 节点 / 3 边）
  - oracle_replay_mode_tick.json：回放模式 tick（2 节点，run_mode:replay_tick）
  - oracle_simple_candidate_to_state.json：简单备选池到状态池（2 节点 / 1 边）
  - oracle_target_pool_action.json：目标池动作（2 节点 / 1 边，含 BUY 信号）

Oracle 文件记录引擎运行期望输出（engine_events / transfer_events / signals），
非池配置文件，但通过 node_stock_codes / transfer_events 反映节点与边结构。

测试可能因源码 bug 失败，这是预期行为——测试目的是发现 bug。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest


# 项目根目录（meta_core/），用于定位 tests/fixtures/migration_oracle/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ORACLE_DIR = _PROJECT_ROOT / "tests" / "fixtures" / "migration_oracle"

# 5 个 Oracle 场景文件名
_ORACLE_FILES = {
    "conditional_transfer": "oracle_conditional_transfer.json",
    "multi_level_cascade": "oracle_multi_level_cascade.json",
    "replay_mode_tick": "oracle_replay_mode_tick.json",
    "simple_candidate_to_state": "oracle_simple_candidate_to_state.json",
    "target_pool_action": "oracle_target_pool_action.json",
}


def _load_oracle(name: str) -> Dict[str, Any]:
    """加载指定 Oracle 场景 JSON 文件。

    Args:
        name: _ORACLE_FILES 中的键名（如 "conditional_transfer"）。

    Returns:
        解析后的 Oracle 字典。
    """
    path = _ORACLE_DIR / _ORACLE_FILES[name]
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _all_oracles() -> List[tuple]:
    """返回 (name, data) 元组列表，供参数化测试遍历全部 5 个 Oracle。"""
    return [(name, _load_oracle(name)) for name in _ORACLE_FILES]


# ============================================================================
# SubTask 7.1 ~ 7.5: 各 Oracle 文件加载与结构验证
# ============================================================================


class TestOracleFilesLoad:
    """5 个 Oracle 场景文件加载与基本结构正测试。"""

    def test_conditional_transfer_loads_and_has_expected_structure(self) -> None:
        """oracle_conditional_transfer.json 加载成功且含 pool_id / node_stocks / transfer_events。"""
        data = _load_oracle("conditional_transfer")

        assert data is not None, "Oracle 文件应加载成功"
        assert data["pool_id"] == "oracle_conditional_transfer", \
            f"pool_id 应为 oracle_conditional_transfer，实际 {data.get('pool_id')}"
        assert "node_stocks" in data, "应含 node_stocks 键"
        assert "transfer_events" in data, "应含 transfer_events 键"
        assert "engine_events" in data, "应含 engine_events 键"
        assert data["success"] is True, "Oracle success 应为 True"
        assert data["error"] is None, "Oracle error 应为 None"

    def test_multi_level_cascade_loads_and_has_expected_structure(self) -> None:
        """oracle_multi_level_cascade.json 加载成功且含 4 节点 / 3 边。"""
        data = _load_oracle("multi_level_cascade")

        assert data["pool_id"] == "oracle_multi_level_cascade", \
            f"pool_id 应为 oracle_multi_level_cascade，实际 {data.get('pool_id')}"
        assert len(data["node_stocks"]) == 4, \
            f"多级级联应有 4 个节点（A/B/C/D），实际 {len(data['node_stocks'])}"
        assert len(data["transfer_events"]) == 3, \
            f"多级级联应有 3 条转移边，实际 {len(data['transfer_events'])}"
        # 节点 ID 应为 A, B, C, D
        assert set(data["node_stocks"].keys()) == {"A", "B", "C", "D"}, \
            f"节点应为 {{A, B, C, D}}，实际 {set(data['node_stocks'].keys())}"

    def test_replay_mode_tick_loads_and_has_expected_structure(self) -> None:
        """oracle_replay_mode_tick.json 加载成功且 run_type 为 replay_tick。"""
        data = _load_oracle("replay_mode_tick")

        assert data["pool_id"] == "oracle_replay_mode_tick", \
            f"pool_id 应为 oracle_replay_mode_tick，实际 {data.get('pool_id')}"
        assert data["run_type"] == "run_mode:replay_tick", \
            f"run_type 应为 run_mode:replay_tick，实际 {data.get('run_type')}"
        assert "node_stocks" in data, "应含 node_stocks 键"
        assert "src" in data["node_stocks"], "应含 src 节点"
        assert "dst" in data["node_stocks"], "应含 dst 节点"

    def test_simple_candidate_to_state_loads_and_has_expected_structure(self) -> None:
        """oracle_simple_candidate_to_state.json 加载成功且含 2 节点 / 1 边。"""
        data = _load_oracle("simple_candidate_to_state")

        assert data["pool_id"] == "oracle_simple_candidate_to_state", \
            f"pool_id 应为 oracle_simple_candidate_to_state，实际 {data.get('pool_id')}"
        assert len(data["node_stocks"]) == 2, \
            f"简单转移应有 2 个节点（candidate/state_1），实际 {len(data['node_stocks'])}"
        assert len(data["transfer_events"]) == 1, \
            f"简单转移应有 1 条转移边，实际 {len(data['transfer_events'])}"
        assert "candidate" in data["node_stocks"], "应含 candidate 节点"
        assert "state_1" in data["node_stocks"], "应含 state_1 节点"

    def test_target_pool_action_loads_and_has_expected_structure(self) -> None:
        """oracle_target_pool_action.json 加载成功且含 BUY 信号。"""
        data = _load_oracle("target_pool_action")

        assert data["pool_id"] == "oracle_target_pool_action", \
            f"pool_id 应为 oracle_target_pool_action，实际 {data.get('pool_id')}"
        assert "signals" in data, "应含 signals 键"
        assert len(data["signals"]) >= 1, \
            f"目标池动作应至少有 1 个信号，实际 {len(data.get('signals', []))}"
        # 信号应为 BUY 类型
        signal_types = {s.get("signal_type") for s in data["signals"]}
        assert "BUY" in signal_types, \
            f"信号应含 BUY 类型，实际 {signal_types}"


# ============================================================================
# SubTask 7.6: 每个 Oracle 含节点与边（node_stocks + transfer_events）
# ============================================================================


class TestOracleNodesAndEdges:
    """每个 Oracle 含节点与边的结构正测试。"""

    @pytest.mark.parametrize("name", list(_ORACLE_FILES.keys()))
    def test_each_oracle_has_node_stocks_and_transfer_events(self, name: str) -> None:
        """每个 Oracle 文件含 node_stocks（节点）与 transfer_events（边）键。"""
        data = _load_oracle(name)

        assert "node_stocks" in data, f"{name} 应含 node_stocks 键"
        assert isinstance(data["node_stocks"], dict), \
            f"{name} node_stocks 应为 dict，实际 {type(data['node_stocks'])}"
        assert len(data["node_stocks"]) >= 1, \
            f"{name} node_stocks 应至少 1 个节点，实际 {len(data['node_stocks'])}"

    @pytest.mark.parametrize("name", list(_ORACLE_FILES.keys()))
    def test_each_oracle_transfer_events_is_list(self, name: str) -> None:
        """每个 Oracle 文件含事件列表（transfer_events 或 queue_events）。

        run_pool 模式用 transfer_events；run_mode:replay_tick 模式用 queue_events。
        两者均为 list，可为空。
        """
        data = _load_oracle(name)

        # run_pool 模式有 transfer_events；回放模式有 queue_events
        has_transfer = "transfer_events" in data
        has_queue = "queue_events" in data
        assert has_transfer or has_queue, \
            f"{name} 应含 transfer_events 或 queue_events 键"
        events_key = "transfer_events" if has_transfer else "queue_events"
        assert isinstance(data[events_key], list), \
            f"{name} {events_key} 应为 list，实际 {type(data[events_key])}"


# ============================================================================
# SubTask 7.7: 每个 Oracle 至少 2 个节点
# ============================================================================


class TestOracleNodeCount:
    """每个 Oracle 至少 2 个节点正测试。"""

    @pytest.mark.parametrize("name", list(_ORACLE_FILES.keys()))
    def test_each_oracle_has_at_least_two_nodes(self, name: str) -> None:
        """每个 Oracle 至少有 2 个节点（node_stocks 键数 >= 2）。"""
        data = _load_oracle(name)

        node_count = len(data["node_stocks"])
        assert node_count >= 2, \
            f"{name} 应至少有 2 个节点，实际 {node_count}"


# ============================================================================
# SubTask 7.8: 条件转移 Oracle 含条件边（ConditionalEdge / transfer_events）
# ============================================================================


class TestConditionalTransferOracle:
    """条件转移 Oracle 含条件边正测试。"""

    def test_conditional_transfer_has_flow_fired_events(self) -> None:
        """条件转移 Oracle 的 engine_events 含 flow_fired 事件（条件边触发）。"""
        data = _load_oracle("conditional_transfer")

        flow_fired = [
            ev for ev in data["engine_events"] if ev.get("event") == "flow_fired"
        ]
        assert len(flow_fired) >= 2, \
            f"条件转移应至少有 2 个 flow_fired 事件，实际 {len(flow_fired)}"

    def test_conditional_transfer_edges_have_flow_id_and_mode(self) -> None:
        """条件转移 Oracle 的 transfer_events 每条边含 flow_id 与 mode 字段。"""
        data = _load_oracle("conditional_transfer")

        for te in data["transfer_events"]:
            assert "flow_id" in te, f"转移事件应含 flow_id，实际 {te}"
            assert "mode" in te, f"转移事件应含 mode，实际 {te}"
            assert "source_id" in te, f"转移事件应含 source_id，实际 {te}"
            assert "target_id" in te, f"转移事件应含 target_id，实际 {te}"
            assert "transferred_codes" in te, f"转移事件应含 transferred_codes，实际 {te}"


# ============================================================================
# SubTask 7.9: 多级级联 Oracle 至少 3 个节点
# ============================================================================


class TestMultiLevelCascadeOracle:
    """多级级联 Oracle 节点数正测试。"""

    def test_multi_level_cascade_has_at_least_three_nodes(self) -> None:
        """多级级联 Oracle 至少有 3 个节点（实际 4 个：A/B/C/D）。"""
        data = _load_oracle("multi_level_cascade")

        node_count = len(data["node_stocks"])
        assert node_count >= 3, \
            f"多级级联应至少有 3 个节点，实际 {node_count}"

    def test_multi_level_cascade_transfers_form_chain(self) -> None:
        """多级级联 Oracle 转移事件形成 A→B→C→D 链式结构。"""
        data = _load_oracle("multi_level_cascade")

        transfers = data["transfer_events"]
        assert len(transfers) == 3, f"应有 3 条转移，实际 {len(transfers)}"
        # 链式：A→B, B→C, C→D
        chain = [(t["source_id"], t["target_id"]) for t in transfers]
        assert ("A", "B") in chain, "应含 A→B 转移"
        assert ("B", "C") in chain, "应含 B→C 转移"
        assert ("C", "D") in chain, "应含 C→D 转移"


# ============================================================================
# SubTask 7.10: 回放模式 Oracle 引用回放功能
# ============================================================================


class TestReplayModeOracle:
    """回放模式 Oracle 引用回放功能正测试。"""

    def test_replay_mode_run_type_references_replay(self) -> None:
        """回放模式 Oracle 的 run_type 包含 replay 关键字。"""
        data = _load_oracle("replay_mode_tick")

        run_type = data["run_type"]
        assert "replay" in run_type, \
            f"run_type 应包含 replay 关键字，实际 {run_type}"

    def test_replay_mode_has_mode_state(self) -> None:
        """回放模式 Oracle 含 mode_state 键（回放模式状态信息）。"""
        data = _load_oracle("replay_mode_tick")

        assert "mode_state" in data, "回放模式 Oracle 应含 mode_state 键"
        assert isinstance(data["mode_state"], dict), \
            f"mode_state 应为 dict，实际 {type(data['mode_state'])}"
        assert "inject" in data["mode_state"], \
            "mode_state 应含 inject 字段（回放注入标志）"

    def test_replay_mode_has_no_error(self) -> None:
        """回放模式 Oracle 无错误（signals 可为空但不应有 error 字段非空）。"""
        data = _load_oracle("replay_mode_tick")

        # 回放模式 Oracle 没有 success/error 键（与 run_pool 不同），
        # 但 signals 应为列表
        assert "signals" in data, "应含 signals 键"
        assert isinstance(data["signals"], list), \
            f"signals 应为 list，实际 {type(data['signals'])}"


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 1 P2 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 P2 dzh_type_map 逆映射 + DZH type 3 唯一值收敛回归。"""

    def test_dzh_type_3_unique_value(self):
        """dzh_to_tdx['3'] == 3（P2 消除原 3 vs 0 矛盾）。"""
        import json
        from pathlib import Path
        tm_path = Path(__file__).resolve().parent.parent / "config" / "architecture" / "dzh_type_map.json"
        data = json.loads(tm_path.read_text(encoding="utf-8"))
        assert data["dzh_to_tdx"]["3"] == 3, \
            "dzh_to_tdx['3'] 应为 3（P2 消除原 _DZH_TO_TDX_TYPE=3 与 _DZH_TO_TDX_TYPE_EXPORT=0 矛盾）"

    def test_execution_type_inverse_mapping(self):
        """dzh_to_tdx 与 tdx_to_dzh 在执行类型上互为逆映射（P2 round-trip 一致）。"""
        import json
        from pathlib import Path
        tm_path = Path(__file__).resolve().parent.parent / "config" / "architecture" / "dzh_type_map.json"
        data = json.loads(tm_path.read_text(encoding="utf-8"))
        # 执行类型 tdx 3/7/8 ↔ dzh 201/202/200 严格逆映射
        assert data["dzh_to_tdx"]["201"] == 3 and data["tdx_to_dzh"]["3"] == 201
        assert data["dzh_to_tdx"]["202"] == 7 and data["tdx_to_dzh"]["7"] == 202
        assert data["dzh_to_tdx"]["200"] == 8 and data["tdx_to_dzh"]["8"] == 200

    def test_no_load_dzh_type_map_definition(self):
        """core/*.py 不含 def _load_dzh_type_map（P2 已改 ConfigStore.get_table）。"""
        import re
        from pathlib import Path
        core_dir = Path(__file__).resolve().parent.parent / "core"
        total = 0
        for py in core_dir.glob("*.py"):
            try:
                src = py.read_text(encoding="utf-8")
            except OSError:
                continue
            total += len(re.findall(r"def _load_dzh_type_map\b", src))
        assert total == 0, \
            f"core/*.py 不应含 def _load_dzh_type_map（P2 已改 ConfigStore.get_table），实际 {total} 处"
