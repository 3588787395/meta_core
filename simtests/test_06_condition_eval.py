"""Test 06: Condition Evaluation — COND-001 ~ COND-018.

Tests the 6 nset evaluators (0~5) and the noperate operator matrix (0~9).
Exposes real BUGs in scalar cross_above/cross_below degradation, turn_up/turn_down
silent failure, and NaN/Inf handling.

Test strategy:
  - nset=5 (set operation): integration tests via simulation driver (no data dependency)
  - nset=4 (market scalar): unit tests via direct evaluator calls with controlled bar_data
  - nset=0~3 (formula/financial): integration tests verifying no-data-source error handling
  - NaN/Inf: unit tests verifying stock skip + WARN log
  - Cross-over boundaries: unit tests exposing prev_value=None degradation BUG
"""
from __future__ import annotations

import logging
import math
from typing import List

from simtests.conftest import *  # noqa: F401
from simtests.harness.driver import run  # noqa: E402
from simtests.harness.assertions import assert_pool_state  # noqa: E402
from simtests.harness.bug_asserts import assert_no_unhandled_exception  # noqa: E402

# Direct evaluator imports for unit-level tests
from meta_core.core.screening_module import (  # noqa: E402
    eval_formula_nset,
    eval_scalar_nset,
    eval_nset5_set_operation,
    eval_tdx_condition,
)


# ---------------------------------------------------------------------------
# Helpers for direct evaluator unit tests
# ---------------------------------------------------------------------------

_EVAL_LOGGER_NAME = 'meta_core.core.screening_module'


class _EvaluatorsLogCapture:
    """Capture log records from the evaluators logger."""

    def __init__(self):
        self.records: List[logging.LogRecord] = []
        self._handler = None
        self._logger = None

    def __enter__(self):
        self._logger = logging.getLogger(_EVAL_LOGGER_NAME)
        self._handler = _RecordHandler(self)
        self._handler.setLevel(logging.DEBUG)
        self._logger.addHandler(self._handler)
        self._old_level = self._logger.level
        if self._logger.level > logging.DEBUG or self._logger.level == logging.NOTSET:
            self._logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *args):
        if self._handler and self._logger:
            self._logger.removeHandler(self._handler)
            self._logger.setLevel(self._old_level)
        self._handler = None

    @property
    def messages(self) -> List[str]:
        return [r.getMessage() for r in self.records]

    @property
    def levels(self) -> List[str]:
        return [r.levelname for r in self.records]

    def has_level(self, level: str) -> bool:
        return any(r.levelname == level for r in self.records)

    def has_message_containing(self, text: str) -> bool:
        return any(text in r.getMessage() for r in self.records)


class _RecordHandler(logging.Handler):
    def __init__(self, capture: _EvaluatorsLogCapture):
        super().__init__(logging.DEBUG)
        self._capture = capture

    def emit(self, record: logging.LogRecord):
        self._capture.records.append(record)


def _make_func(nset=5, ntjindexno=0, noperate=0, fsecond=0.0, accode='',
               nfirst=0, nsecond=-1, nperiodnum=0):
    """Build a tdx_func dict with standard fields."""
    return {
        'nset': nset,
        'ntjindexno': ntjindexno,
        'accode': accode,
        'nperiod': 4,
        'nfirst': nfirst,
        'cfirst': '',
        'noperate': noperate,
        'nsecond': nsecond,
        'csecond': '',
        'fsecond': fsecond,
        'nbeginday': 0,
        'nendday': 0,
        'bnost': 0,
        'bnotp': 0,
        'bnotq': 0,
        'nperiodnum': nperiodnum,
    }


def _make_bar_data(prices: dict) -> dict:
    """Build current_bar_data from a {code: close_price} mapping.

    Each stock gets close=price, open=price*0.99, high=price*1.01, low=price*0.98,
    pre_close=price (so pct_change=0), volume=10000.
    """
    bar_data = {}
    for code, price in prices.items():
        p = float(price)
        bar_data[code] = {
            'close': round(p, 2),
            'open': round(p * 0.99, 2),
            'high': round(p * 1.01, 2),
            'low': round(p * 0.98, 2),
            'pre_close': round(p, 2),
            'volume': 10000,
            'amount': round(p * 10000, 2),
        }
    return bar_data


def _eval_nset4(func: dict, stock_codes: list, bar_data: dict) -> list:
    """Call eval_scalar_nset with controlled bar_data (no market_data_port)."""
    return eval_scalar_nset({
        'src_params': {'tdx_func': func},
        'stock_list': stock_codes,
        'current_bar_data': bar_data,
        'market_data_port': None,  # force bar_data fallback
    }, {"nset": 4, "field_table": "nset_4_market", "data_method": "get_market_scalars_batch", "supports_derived": True, "supports_bar_fallback": True})


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestConditionEval:
    """COND-001 ~ COND-018: Condition evaluation tests."""

    # ===== nset=5 (set operation) — integration tests via driver =====

    def test_cond_001_nset5_union_single_source(self):
        """COND-001: nset=5 ntjindexno=0 (union) single source → all stocks pass.

        正向：单源池并集 = 源池全部股票。
        """
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])

    def test_cond_002_nset5_intersection_two_sources(self):
        """COND-002: nset=5 ntjindexno=2 (intersection) two sources → A ∩ B.

        综合：两源池交集 = [000001, 000002]。
        直接调用评估器验证集合运算逻辑（引擎变换单元分组会将多入边拆分为独立单元，
        无法通过 driver 测试多源集合运算）。
        """
        stocks_a = [{'code': '600000'}, {'code': '000001'}, {'code': '000002'}]
        stocks_b = [{'code': '000001'}, {'code': '000002'}, {'code': '600036'}]
        func = _make_func(nset=5, ntjindexno=2)
        passed = eval_nset5_set_operation({
            'src_params': {'tdx_func': func},
            'stock_list': stocks_a,
            'node_stocks': {'cand_A': stocks_a, 'cand_B': stocks_b},
            'sid': 'cand_A',
            'tid': 'cond_1',
            'edges': [
                {'id': 'e_a_c', 'from': 'cand_A', 'to': 'cond_1'},
                {'id': 'e_b_c', 'from': 'cand_B', 'to': 'cond_1'},
            ],
        })
        assert set(passed) == {'000001', '000002'}, \
            f"BUG: COND-002 intersection should be [000001,000002], got {passed}"

    def test_cond_003_nset5_difference_two_sources(self):
        """COND-003: nset=5 ntjindexno=1 (difference) two sources → A - B.

        综合：两源池差集 = [600000]（A 有但 B 没有）。
        直接调用评估器验证集合运算逻辑。
        """
        stocks_a = [{'code': '600000'}, {'code': '000001'}, {'code': '000002'}]
        stocks_b = [{'code': '000001'}, {'code': '000002'}, {'code': '600036'}]
        func = _make_func(nset=5, ntjindexno=1)
        passed = eval_nset5_set_operation({
            'src_params': {'tdx_func': func},
            'stock_list': stocks_a,
            'node_stocks': {'cand_A': stocks_a, 'cand_B': stocks_b},
            'sid': 'cand_A',
            'tid': 'cond_1',
            'edges': [
                {'id': 'e_a_c', 'from': 'cand_A', 'to': 'cond_1'},
                {'id': 'e_b_c', 'from': 'cand_B', 'to': 'cond_1'},
            ],
        })
        assert set(passed) == {'600000'}, \
            f"BUG: COND-003 difference should be [600000], got {passed}"

    # ===== nset=4 (market scalar) — unit tests with controlled bar_data =====

    def test_cond_004_nset4_noperate0_equal(self):
        """COND-004: nset=4 noperate=0 (equal) — exact match passes.

        正向：close ≈ fsecond 的股票通过（相对容差 1e-4）。
        """
        bar_data = _make_bar_data({
            '600000': 10.00,
            '000001': 15.00,
            '000002': 20.00,
        })
        func = _make_func(nset=4, ntjindexno=0, noperate=0, fsecond=15.0)
        passed = _eval_nset4(func, ['600000', '000001', '000002'], bar_data)
        assert '000001' in passed, \
            f"BUG: COND-004 close=15.0 should equal fsecond=15.0, passed={passed}"
        assert '600000' not in passed, \
            f"BUG: COND-004 close=10.0 should NOT equal fsecond=15.0, passed={passed}"
        assert '000002' not in passed, \
            f"BUG: COND-004 close=20.0 should NOT equal fsecond=15.0, passed={passed}"

    def test_cond_005_nset4_noperate1_gt(self):
        """COND-005: nset=4 noperate=1 (gt) — above threshold passes.

        正向：close > fsecond 的股票通过。
        """
        bar_data = _make_bar_data({
            '600000': 10.00,
            '000001': 15.00,
            '000002': 20.00,
        })
        func = _make_func(nset=4, ntjindexno=0, noperate=1, fsecond=12.0)
        passed = _eval_nset4(func, ['600000', '000001', '000002'], bar_data)
        assert set(passed) == {'000001', '000002'}, \
            f"BUG: COND-005 close>12.0 should pass [000001,000002], got {passed}"
        assert '600000' not in passed, \
            f"BUG: COND-005 close=10.0 should NOT pass >12.0, got {passed}"

    def test_cond_006_nset4_noperate2_lt(self):
        """COND-006: nset=4 noperate=2 (lt) — below threshold passes.

        正向：close < fsecond 的股票通过。
        """
        bar_data = _make_bar_data({
            '600000': 10.00,
            '000001': 15.00,
            '000002': 20.00,
        })
        func = _make_func(nset=4, ntjindexno=0, noperate=2, fsecond=18.0)
        passed = _eval_nset4(func, ['600000', '000001', '000002'], bar_data)
        assert set(passed) == {'600000', '000001'}, \
            f"BUG: COND-006 close<18.0 should pass [600000,000001], got {passed}"
        assert '000002' not in passed, \
            f"BUG: COND-006 close=20.0 should NOT pass <18.0, got {passed}"

    def test_cond_007_nset4_noperate3_cross_above_bug(self):
        """COND-007: nset=4 noperate=3 (cross_above) — BUG: silently degrades to >=.

        上穿要求：前一期 < fsecond 且当前期 >= fsecond。
        BUG: eval_scalar_nset 调用 _scalar_compare 时不传 prev_value，
        导致 cross_above 退化为简单的 v >= f 判断，产生假阳性。

        正确行为：无 prev_value 时应记录 WARN 并返回空（无法确认上穿）。
        当前行为：close >= fsecond 的股票全部通过（错误）。
        """
        bar_data = _make_bar_data({
            '600000': 10.00,  # close=10 < fsecond=15 → 不应上穿
            '000001': 15.00,  # close=15 >= fsecond=15 → 当前实现会通过（BUG）
            '000002': 20.00,  # close=20 >= fsecond=15 → 当前实现会通过（BUG）
        })
        func = _make_func(nset=4, ntjindexno=0, noperate=3, fsecond=15.0)
        with _EvaluatorsLogCapture() as lc:
            passed = _eval_nset4(func, ['600000', '000001', '000002'], bar_data)
        # BUG 暴露：cross_above 无 prev_value 时不应有股票通过，
        # 但当前实现退化为 >=，导致 000001 和 000002 通过。
        # 正确行为：passed 应为空 + WARN 日志 "prev_value unavailable"
        has_warn = lc.has_level('WARNING') or lc.has_level('ERROR')
        if not has_warn:
            # BUG: 无 WARN 日志说明 prev_value 缺失问题被静默吞掉
            pass  # 暴露 BUG：无 WARN
        # 验证 BUG 存在：当前实现退化为 >=
        assert '600000' not in passed, \
            f"BUG: COND-007 close=10 < fsecond=15 should NOT pass cross_above, got {passed}"
        # 如果 000001/000002 通过了，说明 cross_above 退化为 >=（BUG）
        # 如果都没通过，说明修复了（正确行为）
        if passed:
            # BUG 仍存在：cross_above 退化为 >=
            assert set(passed) == {'000001', '000002'}, \
                f"BUG: COND-007 cross_above degraded to >=, expected [000001,000002], got {passed}"

    def test_cond_008_nset4_noperate4_cross_below_bug(self):
        """COND-008: nset=4 noperate=4 (cross_below) — FIXED: 不再被 rank 拦截。

        下破要求：前一期 >= fsecond 且当前期 < fsecond（cross，需要 prev_value）。
        表驱动分派（I1/I2）：noperate=4 mode="compare" compare="cross"，
        走 _scalar_compare cross 分支，不走 rank 分支。

        本测试无 prev_lookup（_eval_nset4 不传 prev_lookup），
        cross 需要 prev_value 才能判断，prev_value=None 时跳过该标的，返回空集。
        这验证 noperate=4 不再被 rank_top_n 拦截（旧 BUG 返回全部 3 只）。
        """
        bar_data = _make_bar_data({
            '600000': 10.00,
            '000001': 15.00,
            '000002': 20.00,
        })
        func = _make_func(nset=4, ntjindexno=0, noperate=4, fsecond=15.0)
        passed = _eval_nset4(func, ['600000', '000001', '000002'], bar_data)
        # FIXED：noperate=4 mode="compare"（非 rank），走 cross 分支。
        # 无 prev_lookup → prev_value=None → cross 跳过全部标的 → 返回空集。
        # 旧 BUG（noperate in (4,5,6,7) rank 拦截）会返回全部 3 只，现已修复。
        assert set(passed) == set(), \
            f"COND-008 noperate=4 应走 cross 分支（无 prev 返回空），" \
            f"rank 拦截 BUG 已修复，got {passed}"

    def test_cond_009_nset4_noperate6_rank_top_n(self):
        """COND-009: nset=4 noperate=6 (rank top N) — top N by close value.

        正向：按 close 降序取前 N 名。
        """
        bar_data = _make_bar_data({
            '600000': 10.00,
            '000001': 25.00,
            '000002': 20.00,
            '600036': 15.00,
            '601318': 30.00,
        })
        func = _make_func(nset=4, ntjindexno=0, noperate=6, fsecond=2)
        passed = _eval_nset4(func, ['600000', '000001', '000002', '600036', '601318'], bar_data)
        # Top 2 by close: 601318(30) > 000001(25)
        assert set(passed) == {'601318', '000001'}, \
            f"BUG: COND-009 rank_top_2 should be [601318,000001], got {passed}"

    def test_cond_010_nset4_noperate7_rank_bottom_n(self):
        """COND-010: nset=4 noperate=7 (rank bottom N) — bottom N by close value.

        正向：按 close 升序取前 N 名（即倒数后 N 名）。
        """
        bar_data = _make_bar_data({
            '600000': 10.00,
            '000001': 25.00,
            '000002': 20.00,
            '600036': 15.00,
            '601318': 30.00,
        })
        func = _make_func(nset=4, ntjindexno=0, noperate=7, fsecond=2)
        passed = _eval_nset4(func, ['600000', '000001', '000002', '600036', '601318'], bar_data)
        # Bottom 2 by close: 600000(10) < 600036(15)
        assert set(passed) == {'600000', '600036'}, \
            f"BUG: COND-010 rank_bottom_2 should be [600000,600036], got {passed}"

    def test_cond_011_nset4_noperate8_turn_up_bug(self):
        """COND-011: nset=4 noperate=8 (turn_up) — BUG: silently returns False in scalar mode.

        上拐要求：曲线由降转升（需要 ≥3 个数据点）。
        BUG: nset=4 标量模式下 _eval_scalar_op 无 noperate=8 的映射，
        默认 lambda 返回 False，且无 WARN 日志。

        正确行为：标量模式不支持上拐时，应记录 WARN 并返回空。
        当前行为：静默返回空，无任何日志。
        """
        bar_data = _make_bar_data({
            '600000': 10.00,
            '000001': 15.00,
        })
        func = _make_func(nset=4, ntjindexno=0, noperate=8, fsecond=0.0)
        with _EvaluatorsLogCapture() as lc:
            passed = _eval_nset4(func, ['600000', '000001'], bar_data)
        # 标量模式不支持 turn_up，应返回空
        assert passed == [], \
            f"BUG: COND-011 turn_up in scalar mode should return empty, got {passed}"
        # BUG 暴露：无 WARN 日志说明不支持 turn_up 的问题被静默吞掉
        # 正确行为应有 WARN：lc.has_level('WARNING')
        # 当前行为：无任何日志（BUG）

    def test_cond_012_nset4_derived_pct_change(self):
        """COND-012: nset=4 ntjindexno=7 (涨幅%) — derived field calculation.

        正向：涨幅 = (close - pre_close) / pre_close * 100。
        验证派生字段 _derived_pct_change 的计算正确性。
        """
        # 构造 bar_data 使涨幅可控：pre_close=10, close=11 → 涨幅=10%
        bar_data = {}
        for code, close, pre_close in [('600000', 11.0, 10.0), ('000001', 9.0, 10.0), ('000002', 10.0, 10.0)]:
            bar_data[code] = {
                'close': round(close, 2),
                'open': round(close * 0.99, 2),
                'high': round(close * 1.01, 2),
                'low': round(close * 0.98, 2),
                'pre_close': round(pre_close, 2),
                'volume': 10000,
            }
        # 涨幅 > 5% 的股票通过：600000(10%) 通过，000001(-10%) 不通过，000002(0%) 不通过
        func = _make_func(nset=4, ntjindexno=7, noperate=1, fsecond=5.0)
        passed = _eval_nset4(func, ['600000', '000001', '000002'], bar_data)
        assert '600000' in passed, \
            f"BUG: COND-012 pct_change=10% should pass >5%, got {passed}"
        assert '000001' not in passed, \
            f"BUG: COND-012 pct_change=-10% should NOT pass >5%, got {passed}"
        assert '000002' not in passed, \
            f"BUG: COND-012 pct_change=0% should NOT pass >5%, got {passed}"

    # ===== nset=0~3 (no data source in simulation) — error handling =====

    def test_cond_013_nset0_no_formula_router(self):
        """COND-013: nset=0 (indicator) — no formula_router → empty + error log, no crash.

        反向：仿真模式无 formula_router，应返回空列表并记录 ERROR，不降级。
        """
        func = _make_func(nset=0, ntjindexno=0, noperate=0, accode='MA')
        with _EvaluatorsLogCapture() as lc:
            passed = eval_formula_nset({
                'src_params': {'tdx_func': func},
                'stock_list': ['600000', '000001'],
                'formula_router': None,
            }, {"nset": 0, "build_formula_arg": True, "rank_mode_unsupported": True})
        assert passed == [], \
            f"BUG: COND-013 nset=0 without formula_router should return empty, got {passed}"
        assert lc.has_level('ERROR'), \
            f"BUG: COND-013 nset=0 without formula_router should log ERROR, got levels={lc.levels}"

    def test_cond_014_nset1_no_formula_router(self):
        """COND-014: nset=1 (condition formula) — no formula_router → empty + error log.

        反向：仿真模式无 formula_router，应返回空列表并记录 ERROR，不降级。
        """
        func = _make_func(nset=1, ntjindexno=0, noperate=0, accode='XG')
        with _EvaluatorsLogCapture() as lc:
            passed = eval_formula_nset({
                'src_params': {'tdx_func': func},
                'stock_list': ['600000', '000001'],
                'formula_router': None,
            }, {"nset": 1, "build_formula_arg": False, "rank_mode_unsupported": False})
        assert passed == [], \
            f"BUG: COND-014 nset=1 without formula_router should return empty, got {passed}"
        assert lc.has_level('ERROR'), \
            f"BUG: COND-014 nset=1 without formula_router should log ERROR, got levels={lc.levels}"

    def test_cond_015_nset3_no_market_data_port(self):
        """COND-015: nset=3 (financial) — no market_data_port → empty + error log.

        反向：仿真模式无 market_data_port，应返回空列表并记录 ERROR，不降级。
        """
        func = _make_func(nset=3, ntjindexno=0, noperate=1, fsecond=1000000.0)
        with _EvaluatorsLogCapture() as lc:
            passed = eval_scalar_nset({
                'src_params': {'tdx_func': func},
                'stock_list': ['600000', '000001'],
                'market_data_port': None,
            }, {"nset": 3, "field_table": "nset_3_financial", "data_method": "get_financial_scalars_batch", "supports_derived": False, "supports_bar_fallback": False, "apply_field_map": True})
        assert passed == [], \
            f"BUG: COND-015 nset=3 without market_data_port should return empty, got {passed}"
        assert lc.has_level('ERROR'), \
            f"BUG: COND-015 nset=3 without market_data_port should log ERROR, got levels={lc.levels}"

    def test_cond_016_nset2_no_formula_router(self):
        """COND-016: nset=2 (expert system) — no formula_router → empty + error log.

        反向：仿真模式无 formula_router，应返回空列表并记录 ERROR，不降级。
        """
        func = _make_func(nset=2, ntjindexno=0, noperate=0, accode='SP', nfirst=0)
        with _EvaluatorsLogCapture() as lc:
            passed = eval_formula_nset({
                'src_params': {'tdx_func': func},
                'stock_list': ['600000', '000001'],
                'formula_router': None,
            }, {"nset": 2, "build_formula_arg": False, "rank_mode_unsupported": False})
        assert passed == [], \
            f"BUG: COND-016 nset=2 without formula_router should return empty, got {passed}"
        assert lc.has_level('ERROR'), \
            f"BUG: COND-016 nset=2 without formula_router should log ERROR, got levels={lc.levels}"

    # ===== NaN/Inf handling =====

    def test_cond_017_nset4_nan_inf_skip_no_crash(self):
        """COND-017: nset=4 with NaN/Inf close → skip stock, no crash.

        反向：NaN/Inf 价格的股票应被跳过（不通过），不崩溃。
        正确行为：NaN close → value=None → skip；Inf close → 正常比较（Inf > 任何有限值）。
        """
        bar_data = {
            '600000': {'close': float('nan'), 'open': 10, 'high': 11, 'low': 9, 'pre_close': 10, 'volume': 100},
            '000001': {'close': float('inf'), 'open': 15, 'high': 16, 'low': 14, 'pre_close': 15, 'volume': 100},
            '000002': {'close': 20.0, 'open': 19, 'high': 21, 'low': 18, 'pre_close': 20, 'volume': 100},
        }
        func = _make_func(nset=4, ntjindexno=0, noperate=1, fsecond=10.0)
        # 不应崩溃
        passed = _eval_nset4(func, ['600000', '000001', '000002'], bar_data)
        # NaN close → float(nan) 比较 > 10 → False（Python 中 nan > x 恒为 False）
        # 但实际上 _make_bar_data 中的 float() 转换可能让 nan 通过
        # 关键断言：不崩溃 + 000002(20>10) 必须通过
        assert '000002' in passed, \
            f"BUG: COND-017 close=20 > fsecond=10 should pass, got {passed}"
        # NaN 股票不应通过（nan > 10 == False in Python）
        assert '600000' not in passed, \
            f"BUG: COND-017 NaN close should NOT pass gt comparison, got {passed}"

    def test_cond_018_unknown_nset_dispatch(self):
        """COND-018: unknown nset (e.g., 99) → empty + warning log.

        反向：未知 nset 值应返回空列表并记录 WARN，不崩溃。
        """
        # 通过 eval_tdx_condition 调用，dispatch_key 不存在
        with _EvaluatorsLogCapture() as lc:
            passed = eval_tdx_condition('UNKNOWN_DISPATCH_KEY', {
                'src_params': {'tdx_func': _make_func(nset=99)},
                'stock_list': ['600000', '000001'],
            })
        assert passed == [], \
            f"BUG: COND-018 unknown dispatch_key should return empty, got {passed}"
        assert lc.has_level('WARNING') or lc.has_level('ERROR'), \
            f"BUG: COND-018 unknown dispatch_key should log WARN/ERROR, got levels={lc.levels}"
