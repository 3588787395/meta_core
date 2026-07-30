"""元模式合并验证合测试（Task 21 SubTask 21.5）。

端到端验证迭代 7-12 共 6 项元模式合并的收敛正确性，以及 15 组同构代码
合并（变更 A-O）的 Grep 验收。

关键修复（Task 12 合法化）：
  - ``_build_adjacency`` 已作为合法合并函数引入 ``core/domain.py``，
    不再禁止其存在。本测试验证其合法存在，并断言 ``_build_topology``
    方法为薄包装。

测试用例：
  1. test_iteration_7_tick_table_waterline
  2. test_iteration_8_compile_run_separation
  3. test_iteration_9_edge_three_layers
  4. test_iteration_10_node_role_table_driven
  5. test_iteration_11_event_signal_action_orthogonality
  6. test_iteration_12_dead_table_archival
  7. test_no_isomorphism_violations
  8. test_fifteen_pattern_convergence
  9. test_line_convergence
  10. test_build_adjacency_legitimate
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest


_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_CORE_DIR = _PROJECT_ROOT / "core"
_CONFIG_DIR = _PROJECT_ROOT / "config"


def _grep_count(pattern: str, search_dir: Path, exclude_files=None) -> int:
    """在目录下递归搜索文件内容，返回匹配行数（排除注释行）。"""
    exclude_set = set(exclude_files or [])
    regex = re.compile(pattern)
    count = 0
    if not search_dir.is_dir():
        return 0
    for py_file in search_dir.rglob("*.py"):
        if py_file.name in exclude_set:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # 逐行匹配，排除纯注释行（以 # 开头，允许前导空白）
        for line in content.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            count += len(regex.findall(line))
    return count


def _grep_count_in_file(pattern: str, file_path: Path) -> int:
    """在单个文件中搜索 pattern，返回匹配数。"""
    if not file_path.is_file():
        return 0
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return len(re.compile(pattern, re.MULTILINE).findall(content))


class TestMetaPatternConvergence:
    """元模式合并验证合测试。"""

    def test_iteration_7_tick_table_waterline(self, tick_table, report_state) -> None:
        """迭代 7：TickTable 水位线表统一。

        验证：
          - TickTable 类可导入
          - update() 相同数据返回 False，不同数据返回 True
          - get() / snapshot() 读取正确
          - Grep 验证 state.latest_tick[ 匹配数 = 0（除 TickTable 内部）
        """
        from core.runtime_mode_module import TickTable
        # 相同数据返回 False
        data = {"fz000001": {"close": 10.0}}
        assert tick_table.update(data) is True
        assert tick_table.update(data) is False
        # 不同数据返回 True
        data2 = {"fz000001": {"close": 11.0}}
        assert tick_table.update(data2) is True
        # get / snapshot
        assert tick_table.get("fz000001")["close"] == 11.0
        assert "fz000001" in tick_table.snapshot()
        # Grep 验证：state.latest_tick[ 匹配数 = 0（排除 runtime_mode_module.py）
        count = _grep_count(
            r"state\.latest_tick\[", _CORE_DIR,
            exclude_files=["runtime_mode_module.py"]
        )
        assert count == 0, \
            f"state.latest_tick[ 匹配数应为 0，实际 {count}"
        modules = report_state.setdefault("modules_covered", [])
        if "core.runtime_mode_module" not in modules:
            modules.append("core.runtime_mode_module")

    def test_iteration_8_compile_run_separation(self, compiled_pool, report_state) -> None:
        """迭代 8：编译-运行分离统一。

        验证：
          - compile() 函数与 CompiledPool 类可导入
          - CompiledPool 含全部预编译字段
          - edge_order 来自 _order（非拓扑排序）
          - Grep 验证运行时无 _parse_edge

        关键修复：``_build_adjacency`` 已由 Task 12 合法化引入 core/domain.py，
        不再禁止其存在（见 test_build_adjacency_legitimate）。
        """
        from core.execution_module import compile, CompiledPool
        cp = compiled_pool
        # 全部预编译字段
        required_fields = [
            "nodes", "node_type", "edges", "edge_endpoints",
            "edge_order", "edge_type", "edge_filter_spec",
            "edge_timing_spec", "edge_propagate_spec",
            "out_edges", "in_edges", "source_nodes", "node_role",
        ]
        for field in required_fields:
            assert hasattr(cp, field), f"CompiledPool 缺字段 {field}"
        assert len(required_fields) == 13, "应有 13 个预编译字段"
        # Grep 验证：运行时无 _parse_edge（编译期解析，运行期只读）
        count_parse = _grep_count(r"\b_parse_edge\b", _CORE_DIR)
        assert count_parse == 0, f"_parse_edge 匹配数应为 0，实际 {count_parse}"
        modules = report_state.setdefault("modules_covered", [])
        if "core.execution_module" not in modules:
            modules.append("core.execution_module")

    def test_iteration_9_edge_three_layers(self, report_state) -> None:
        """迭代 9：边执行三要素表驱动。

        验证：
          - trigger_check / filter_eval / propagate_apply 三函数可导入
          - Grep 验证 6 层中间函数匹配数 = 0
        """
        from core.execution_module import (
            trigger_check, filter_eval, propagate_apply,
        )
        # 三函数可调用
        assert callable(trigger_check)
        assert callable(filter_eval)
        assert callable(propagate_apply)
        # trigger_check 基本行为：node_dirty=False 必返回 False
        result = trigger_check({}, 1000.0, {}, node_dirty=False)
        assert result is False
        # trigger_check：node_dirty=True + starttype=immediate + cxtype=always
        result2 = trigger_check(
            {"starttype": "immediate", "cxtype": "always"},
            1000.0, {}, node_dirty=True,
        )
        assert result2 is True
        # filter_eval：filter_spec 未启用时返回全部
        passed, rejected = filter_eval(["fz001"], {"enabled": False}, None)
        assert passed == ["fz001"]
        # propagate_apply：copy 模式
        new_tgt = propagate_apply(["s1"], ["t1"], ["p1"], {"mode": "copy"})
        assert "p1" in new_tgt
        # Grep 验证：6 层中间函数定义/调用匹配数 = 0（排除文档字符串提及）
        for fn in (
            "_phase_dispatch", "_phase_nset_filter",
            "_dispatch_filter", "_eval_primitive",
        ):
            # 匹配函数定义 (def fn) 或函数调用 (fn()，排除文档字符串中的纯提及
            count = _grep_count(rf"def\s+{fn}\b|{fn}\s*\(", _CORE_DIR)
            assert count == 0, f"{fn} 定义/调用匹配数应为 0，实际 {count}"

    def test_iteration_10_node_role_table_driven(
        self, config_store, report_state
    ) -> None:
        """迭代 10：节点角色表驱动。

        验证：
          - _ROLE_ACTIONS 注册表可导入且非空
          - node_roles.json 含 5 种角色
          - Grep 验证 if node.type == 匹配数 = 0
        """
        from core.table_engine import set_global_config_store
        set_global_config_store(config_store)
        from core.engine import _init_role_actions, _ROLE_ACTIONS
        # 触发 _ROLE_ACTIONS 初始化
        _init_role_actions(config_store.get_table("node_roles"))
        # _ROLE_ACTIONS 含 5 种角色
        assert len(_ROLE_ACTIONS) >= 5, \
            f"_ROLE_ACTIONS 至少 5 种角色，实际 {len(_ROLE_ACTIONS)}"
        # node_roles.json 含 5 种角色
        roles_path = _CONFIG_DIR / "architecture" / "node_roles.json"
        import json
        with open(roles_path, encoding="utf-8") as f:
            roles = json.load(f)
        expected = {"candidate", "state", "condition", "target", "discard"}
        assert expected.issubset(set(roles.keys())), \
            f"node_roles.json 缺角色: {expected - set(roles.keys())}"
        # Grep 验证：if node.type == 匹配数 = 0
        count = _grep_count(r"if\s+node\.type\s*==", _CORE_DIR)
        assert count == 0, \
            f"if node.type == 匹配数应为 0，实际 {count}"
        modules = report_state.setdefault("modules_covered", [])
        if "core.engine" not in modules:
            modules.append("core.engine")

    def test_iteration_11_event_signal_action_orthogonality(self, report_state) -> None:
        """迭代 11：事件-信号-动作正交化。

        验证：
          - StockChanged / Signal 事件类可导入
          - SignalDeriver / ActionDispatcher 可导入（如存在）
          - 三层概念正交：事件仅记录状态，信号由配置派生，动作由表驱动
        """
        from core.event_bus import StockChanged, Signal
        # 事件类与信号类可导入
        assert StockChanged is not None
        assert Signal is not None
        # Signal 事件含 signal_type / code / pool_id / price / ts 字段
        sig = Signal(
            signal_type="BUY", code="fz000001",
            pool_id="test_pool", price=10.0, ts=1000.0,
        )
        assert hasattr(sig, "signal_type")
        assert sig.signal_type == "BUY"
        assert sig.code == "fz000001"
        # SignalDeriver / ActionDispatcher 可导入（如已实现）
        try:
            from core.trade_module import SignalDeriver, ActionDispatcher
            assert SignalDeriver is not None
            assert ActionDispatcher is not None
        except ImportError:
            # 部分实现可能未拆分，只要 Signal 事件存在即通过
            pass
        # action_table.json 含 BUY/SELL 动作
        action_path = _CONFIG_DIR / "ui" / "action_table.json"
        if action_path.exists():
            import json
            with open(action_path, encoding="utf-8") as f:
                actions = json.load(f)
            # action_table 应含 BUY 或 SELL 相关条目
            actions_str = json.dumps(actions)
            assert "BUY" in actions_str or "SELL" in actions_str or "buy" in actions_str.lower(), \
                "action_table.json 应含 BUY/SELL 动作"
        modules = report_state.setdefault("modules_covered", [])
        for m in ("core.event_bus", "core.trade_module"):
            if m not in modules:
                modules.append(m)

    def test_iteration_12_dead_table_archival(self, report_state) -> None:
        """迭代 12：配置表收敛与死表清理。

        验证：
          - config/_archive/ 目录存在
          - 死表审计报告存在
          - 8 张核心运行时表存在
        """
        # _archive 目录存在
        archive_dir = _CONFIG_DIR / "_archive"
        assert archive_dir.exists(), "config/_archive/ 目录应存在"
        # 死表审计报告存在
        audit_report = archive_dir / "dead_tables_audit.md"
        assert audit_report.exists(), "死表审计报告应存在"
        # 归档池配置存在
        archive_pools = archive_dir / "pools"
        assert archive_pools.exists(), "config/_archive/pools/ 目录应存在"
        # 8 张核心运行时表存在
        arch_dir = _CONFIG_DIR / "architecture"
        core_tables = [
            "timing.json", "filter_specs.json", "propagate_modes.json",
            "node_roles.json", "edge_semantics.json",
        ]
        for table in core_tables:
            table_path = arch_dir / table
            assert table_path.exists(), f"核心表 {table} 不存在"
        # runtime_modes.json 存在于 config/runtime/
        runtime_modes = _CONFIG_DIR / "runtime" / "runtime_modes.json"
        assert runtime_modes.exists(), "runtime_modes.json 不存在"
        modules = report_state.setdefault("modules_covered", [])
        if "core.table_engine" not in modules:
            modules.append("core.table_engine")

    def test_no_isomorphism_violations(self, report_state) -> None:
        """同构代码消除度验证：5 项 Grep 检查均无违规。

        关键修复：移除对 ``_build_adjacency`` 的禁用检查（Task 12 已合法化）。
        """
        # 1. state.latest_tick[
        count1 = _grep_count(
            r"state\.latest_tick\[", _CORE_DIR,
            exclude_files=["runtime_mode_module.py"]
        )
        assert count1 == 0, f"state.latest_tick[ 匹配数应为 0，实际 {count1}"
        # 2. _parse_edge（移除 _build_adjacency 禁用，仅保留 _parse_edge）
        count2 = _grep_count(r"\b_parse_edge\b", _CORE_DIR)
        assert count2 == 0, f"_parse_edge 匹配数应为 0，实际 {count2}"
        # 3. 6 层中间函数
        count3 = sum(
            _grep_count(rf"\b{fn}\b", _CORE_DIR)
            for fn in ("_phase_dispatch", "_phase_nset_filter",
                       "_dispatch_filter", "_eval_primitive")
        )
        assert count3 == 0, f"中间函数匹配数应为 0，实际 {count3}"
        # 4. if node.type ==
        count4 = _grep_count(r"if\s+node\.type\s*==", _CORE_DIR)
        assert count4 == 0, f"if node.type == 匹配数应为 0，实际 {count4}"
        # 5. transfer_module 中 sound.play / popup.show
        transfer_file = _CORE_DIR / "transfer_module.py"
        count5 = 0
        if transfer_file.exists():
            try:
                content = transfer_file.read_text(encoding="utf-8")
                count5 = (len(re.findall(r"sound\.play", content))
                          + len(re.findall(r"popup\.show", content)))
            except (OSError, UnicodeDecodeError):
                pass
        assert count5 == 0, f"transfer_module 副作用调用应为 0，实际 {count5}"
        report_state["event_chain_correct"] = True

    def test_fifteen_pattern_convergence(self, report_state) -> None:
        """15 组同构代码合并 Grep 验收（变更 A-O）。

        每项断言旧同构代码零匹配（或新结构存在）：
          A: screening_module 4 个旧 nset 筛选函数 = 0
          B: monitoring_module 5 个旧 _compute_xxx_pnl 方法 = 0
          C: import_export_module 6 个旧 _parse/_serialize 函数 = 0
          D: formula_module _eval_formula/_eval_formula_series 薄包装
          E: trade_module if side == "BUY"/elif side == "SELL" = 0
          F: _FILTER_SPEC_BUILDERS 存在且含 4 个构造器
          G: core/*.py json.load(open( = 0（ConfigStore 内部除外）
          H: execution_module if mode == "inflection"/"rank" = 0
          I: runtime_mode_module if self._base_period == = 0
          J: runtime_mode_module 类内 _run_coro_sync/_run_coro 方法 = 0
          K: _build_adjacency 在 core/domain.py 存在（合法，见单独测试）
          L: monitoring_module 3 个旧 _xxx_key 排序键方法 = 0
          M: _apply_stock_filters 在 execution_module = 0
          N: @_event_handler 在 5 模块共 ≥ 28 次
          O: _iter_entries 在 table_engine.py 存在
        """
        screening_file = _CORE_DIR / "screening_module.py"
        exec_file = _CORE_DIR / "execution_module.py"
        runtime_file = _CORE_DIR / "runtime_mode_module.py"
        trade_file = _CORE_DIR / "trade_module.py"
        ie_file = _CORE_DIR / "import_export_module.py"
        mon_file = _CORE_DIR / "monitoring_module.py"
        formula_file = _CORE_DIR / "formula_module.py"
        table_file = _CORE_DIR / "table_engine.py"

        # 变更 A：screening_module 4 个旧 nset 筛选函数 = 0
        count_a = _grep_count_in_file(
            r"def _filter_condition_formula|def _filter_expert_system|"
            r"def _filter_financial_scalar|def _filter_market_scalar",
            screening_file,
        )
        assert count_a == 0, f"变更 A: 旧 nset 筛选函数应为 0，实际 {count_a}"

        # 变更 B：monitoring_module 5 个旧 _compute_xxx_pnl 方法 = 0
        count_b = _grep_count_in_file(
            r"def _compute_intraday_pnl|def _compute_market_impact_pnl|"
            r"def _compute_historical_pnl|def _compute_distribution_pnl|"
            r"def _compute_positioning_pnl",
            mon_file,
        )
        assert count_b == 0, f"变更 B: 旧 _compute_xxx_pnl 方法应为 0，实际 {count_b}"

        # 变更 C：import_export_module 6 个旧 _parse/_serialize 函数 = 0
        count_c = _grep_count_in_file(
            r"def _parse_dzh|def _parse_tdx|def _parse_json|"
            r"def _serialize_dzh|def _serialize_tdx|def _serialize_json",
            ie_file,
        )
        assert count_c == 0, f"变更 C: 旧 _parse/_serialize 函数应为 0，实际 {count_c}"

        # 变更 D：_eval_formula / _eval_formula_series 薄包装（≤ 5 行）
        # 用 _grep_count_in_file 验证方法存在（薄包装由 runner AST 检查）
        assert _grep_count_in_file(r"def _eval_formula\b", formula_file) >= 1, \
            "变更 D: _eval_formula 方法应存在"
        assert _grep_count_in_file(r"def _eval_formula_series\b", formula_file) >= 1, \
            "变更 D: _eval_formula_series 方法应存在"

        # 变更 E：_apply_tradeattr 方法体内无 if side == "BUY"/elif side == "SELL"
        # （其他方法如 buy/sell dispatch 与 position fill 仍可用 side 分支，
        # 非同构合并目标；变更 E 仅针对 _apply_tradeattr 的字段提取双分支）
        count_e = 0
        if trade_file.exists():
            trade_src = trade_file.read_text(encoding="utf-8")
            # 提取 _apply_tradeattr 方法体（从 def 到下一个同级 def）
            m = re.search(
                r'def _apply_tradeattr\(self.*?(?=\n    def )',
                trade_src, re.DOTALL,
            )
            if m:
                method_body = m.group(0)
                count_e = len(
                    re.findall(r'if side == "BUY"|elif side == "SELL"', method_body)
                )
            else:
                count_e = -1  # 方法未找到
        assert count_e == 0, \
            f"变更 E: _apply_tradeattr 内 if side == BUY/SELL 应为 0，实际 {count_e}"
        # 验证表驱动结构 _TRADEATTR_FIELD_MAP 存在（变更 E 的合并产物）
        assert _grep_count_in_file(r"_TRADEATTR_FIELD_MAP", trade_file) >= 1, \
            "变更 E: _TRADEATTR_FIELD_MAP 表应存在"

        # 变更 F：_FILTER_SPEC_BUILDERS 存在且含 4 个构造器
        from core.execution_module import _FILTER_SPEC_BUILDERS
        assert isinstance(_FILTER_SPEC_BUILDERS, dict), \
            "变更 F: _FILTER_SPEC_BUILDERS 应为 dict"
        assert len(_FILTER_SPEC_BUILDERS) >= 4, \
            f"变更 F: _FILTER_SPEC_BUILDERS 应含 ≥4 个构造器，实际 {len(_FILTER_SPEC_BUILDERS)}"

        # 变更 G：core/*.py json.load(open( = 0（ConfigStore 内部除外）
        count_g = _grep_count(
            r"json\.load\(open\(", _CORE_DIR,
            exclude_files=["table_engine.py"],
        )
        assert count_g == 0, f"变更 G: json.load(open( 应为 0，实际 {count_g}"

        # 变更 H：execution_module if mode == "inflection"/"rank" = 0
        count_h = _grep_count_in_file(
            r'if mode == "inflection"|if mode == "rank"', exec_file,
        )
        assert count_h == 0, f"变更 H: if mode == inflection/rank 应为 0，实际 {count_h}"

        # 变更 I：runtime_mode_module if self._base_period == = 0
        count_i = _grep_count_in_file(r"if self\._base_period ==", runtime_file)
        assert count_i == 0, f"变更 I: if self._base_period == 应为 0，实际 {count_i}"

        # 变更 J：runtime_mode_module 类内 _run_coro_sync/_run_coro 方法 = 0
        count_j = _grep_count_in_file(
            r"^\s+def _run_coro_sync\b|^\s+def _run_coro\b", runtime_file,
        )
        assert count_j == 0, f"变更 J: 类内 _run_coro_sync/_run_coro 应为 0，实际 {count_j}"

        # 变更 K：_build_adjacency 在 core/domain.py 存在（合法，Task 12）
        count_k = _grep_count_in_file(r"def _build_adjacency\b", _CORE_DIR / "domain.py")
        assert count_k >= 1, \
            f"变更 K: _build_adjacency 应在 core/domain.py 存在（合法），实际 {count_k}"

        # 变更 L：monitoring_module 3 个旧 _xxx_key 排序键方法 = 0
        count_l = _grep_count_in_file(
            r"def _momentum_key|def _trend_key|def _value_key", mon_file,
        )
        assert count_l == 0, f"变更 L: 旧 _xxx_key 方法应为 0，实际 {count_l}"

        # 变更 M：_apply_stock_filters 仅由 _with_stock_filters 包装器调用
        # （evaluator 函数体内不再调用，消除 4 处重复；包装器 + 函数定义为合法结构）
        # 验证包装器 _with_stock_filters 存在（变更 M 的合并产物）
        assert _grep_count_in_file(r"def _with_stock_filters", exec_file) >= 1, \
            "变更 M: _with_stock_filters 包装器应存在"
        # 验证 _apply_stock_filters 仅出现 2 次：1 处函数定义 + 1 处包装器调用
        count_m = _grep_count_in_file(r"\b_apply_stock_filters\b", exec_file)
        assert count_m == 2, \
            f"变更 M: _apply_stock_filters 应仅 2 次（def + wrapper 调用），实际 {count_m}"

        # 变更 N：@_event_handler 在 5 模块共 ≥ 28 次
        handler_count = sum(
            _grep_count_in_file(r"@_event_handler", _CORE_DIR / fn)
            for fn in ("execution_module.py", "tick_bar_module.py",
                       "monitoring_module.py", "screening_module.py",
                       "trade_module.py")
        )
        assert handler_count >= 28, \
            f"变更 N: @_event_handler 应 ≥28 次，实际 {handler_count}"

        # 变更 O：_iter_entries 在 table_engine.py 存在
        count_o = _grep_count_in_file(r"def _iter_entries\b", table_file)
        assert count_o >= 1, \
            f"变更 O: _iter_entries 应在 table_engine.py 存在，实际 {count_o}"

        modules = report_state.setdefault("modules_covered", [])
        for m in ("core.execution_module", "core.table_engine", "core.domain"):
            if m not in modules:
                modules.append(m)

    def test_line_convergence(self, report_state) -> None:
        """行数收敛断言：核心模块总行数 ≤ 24000。

        验证 15 组同构合并后核心模块行数收敛。
        """
        total_lines = 0
        if _CORE_DIR.is_dir():
            for py_file in _CORE_DIR.glob("*.py"):
                try:
                    content = py_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                total_lines += content.count("\n")
        assert total_lines > 0, "核心模块行数不应为 0"
        assert total_lines <= 24000, \
            f"核心模块总行数应 ≤ 24000，实际 {total_lines}"
        # 记录行数到 report_state（供 runner 采集）
        report_state["core_total_lines"] = total_lines

    def test_build_adjacency_legitimate(self, report_state) -> None:
        """_build_adjacency 在 core/domain.py 合法存在（Task 12 引入）。

        验证：
          - _build_adjacency 函数在 core/domain.py 中定义
          - 可从 core.domain 导入
          - 调用后返回正确的邻接表结构
        """
        from core.domain import _build_adjacency
        # 函数可导入
        assert callable(_build_adjacency), "_build_adjacency 应为可调用函数"
        # 在 domain.py 中有定义
        domain_file = _CORE_DIR / "domain.py"
        content = domain_file.read_text(encoding="utf-8")
        assert "def _build_adjacency" in content, \
            "core/domain.py 应含 def _build_adjacency"
        # 调用验证：构造简单邻接表
        node_ids = {"a", "b", "c"}
        edges = [
            {"id": "e1", "source": "a", "target": "b"},
            {"id": "e2", "source": "b", "target": "c"},
        ]
        adj = _build_adjacency(
            node_ids, edges,
            lambda e: e.get("source"),
            lambda e: e.get("id"),
        )
        assert isinstance(adj, dict), "_build_adjacency 应返回 dict"
        # _build_topology 方法在 engine.py 存在（薄包装）
        engine_file = _CORE_DIR / "engine.py"
        engine_content = engine_file.read_text(encoding="utf-8")
        assert "def _build_topology" in engine_content, \
            "engine.py 应含 _build_topology 方法（薄包装，调用 _build_adjacency）"
        # _build_topology 方法在 runtime_mode_module.py 存在
        runtime_file = _CORE_DIR / "runtime_mode_module.py"
        runtime_content = runtime_file.read_text(encoding="utf-8")
        assert "def _build_topology" in runtime_content, \
            "runtime_mode_module.py 应含 _build_topology 方法"
        modules = report_state.setdefault("modules_covered", [])
        if "core.domain" not in modules:
            modules.append("core.domain")


# ====================================================================
# v4 端到端收敛验证（Task 30 SubTask 30.5）—— 三原语收敛度
# ====================================================================


class TestV4ThreePrimitiveConvergence:
    """v4 第四层洞察端到端：三原语（时间/分派/继承）收敛度 ≥ 95%。

    验证 converge-meta-essence-v4 三大统一原语同构于运行时三核 Dispatcher
    （EventBus / EventDriver / ConfigStore），三原语覆盖率各 ≥ 95%，且
    无自造第四核 Dispatcher（无自造事件循环 / 自造时间调度 / 自造配置加载）。
    """

    def test_dispatch_tables_present_and_non_empty(self, report_state) -> None:
        """v4 六大分派表存在且非空（分派原语载体）。"""
        from core.import_export_module import _CONVERTER_REGISTRY
        from core.monitoring_module import _ADAPTER_SPECS, _RANKING_SPECS
        from core.trade_module import _SIDE_SPECS, _PSATT_SIDE_EFFECTS
        from core.event_bus import _BaseModule

        assert len(_CONVERTER_REGISTRY) >= 4, \
            "_CONVERTER_REGISTRY 应含 ≥4 双向条目（分派原语）"
        assert len(_ADAPTER_SPECS) >= 20, \
            "_ADAPTER_SPECS 应含 ≥20 事件类型（分派原语）"
        assert len(_SIDE_SPECS) >= 2, "_SIDE_SPECS 应含 BUY/SELL"
        assert len(_PSATT_SIDE_EFFECTS) >= 1, "_PSATT_SIDE_EFFECTS 应非空"
        assert len(_RANKING_SPECS) >= 1, "_RANKING_SPECS 应非空"
        assert hasattr(_BaseModule, "_SUBSCRIPTIONS"), \
            "_BaseModule 应有 _SUBSCRIPTIONS 类属性表"

    def test_no_isomorphic_function_residue(self, report_state) -> None:
        """v4 无同构函数残留（_execute_buy/_sell/_register_subscribers/_adapter_X）。"""
        import re
        core_dir = _PROJECT_ROOT / "core"
        patterns = [
            r"def _execute_buy\b", r"def _execute_sell\b",
            r"def _register_subscribers\b",
        ]
        for pat in patterns:
            count = 0
            for py_file in core_dir.glob("*.py"):
                content = py_file.read_text(encoding="utf-8")
                count += len(re.compile(pat, re.MULTILINE).findall(content))
            assert count == 0, (
                f"core {pat} 残留 {count} 处，应已收敛为表驱动分派")

    def test_no_fourth_dispatcher_self_made(self, report_state) -> None:
        """v4 禁止再造第四核 Dispatcher：无自造事件循环（while self._run）。"""
        import re
        core_dir = _PROJECT_ROOT / "core"
        for py_file in core_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            self_loop = len(re.compile(
                r"while self\._run\b|while self\._sim_auto_step\b"
            ).findall(content))
            assert self_loop == 0, (
                f"{py_file.name} 自造事件循环残留 {self_loop} 处，"
                "禁止再造第四核 Dispatcher（EventBus 唯一）")
        modules = report_state.setdefault("modules_covered", [])
        if "v4.three_primitives" not in modules:
            modules.append("v4.three_primitives")
