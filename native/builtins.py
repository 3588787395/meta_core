# === 流水线层（自 native/pipeline.py 合并）===
import ast
import base64
import copy
import json
import logging
import os
import statistics
import time
from typing import Any, Dict, Optional

try:
    from ..services.tq_adapter import decode_formula, map_period
except ImportError:
    from services.tq_adapter import decode_formula, map_period

try:
    from ..core.schemas import ConfigLoadError
except ImportError:
    from core.schemas import ConfigLoadError

try:
    from ..core.screening_module import _eval_derived_expr
except ImportError:
    from core.screening_module import _eval_derived_expr

try:
    from ..core.domain import _stock_code
except ImportError:
    from core.domain import _stock_code

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# 公共工具函数（原 builtins.py）
# ──────────────────────────────────────────────────────────────

def _decode_formula_base64(indi_b64, ency=0):
    """解码 DZH indi Base64 字段为 (公式文本, 原始字节数)。

    使用 _common.decode_formula 的二进制解析器提取公式文本，
    而非简单的 UTF-8/GBK 文本解码。
    """
    if not indi_b64 or indi_b64 == "0;":
        return "", 0
    try:
        raw = base64.b64decode(indi_b64)
        text = decode_formula(indi_b64, ency)
        return text, len(raw)
    except Exception:
        return "", 0


# DZH analysis_cycle 整数码（editor.js DEFAULT_CYCLE_NAMES：1=分笔…9=月线）→ 标准周期字符串
_DZH_CYCLE_TO_PERIOD = {
    1: 'tick', 2: '1m', 3: '5m', 4: '15m', 5: '30m', 6: '60m',
    7: '1d', 8: '1wk', 9: '1mon',
}
# 字符串周期别名归一化（覆盖中文名 / 英文全称 / 标准代码）
_PERIOD_STR_ALIASES = {
    'tick': 'tick', '分笔': 'tick',
    '1m': '1m', '1min': '1m', '1分': '1m', '1分钟': '1m',
    '5m': '5m', '5min': '5m', '5分': '5m', '5分钟': '5m',
    '15m': '15m', '15min': '15m', '15分': '15m', '15分钟': '15m',
    '30m': '30m', '30min': '30m', '30分': '30m', '30分钟': '30m',
    '60m': '60m', '60min': '60m', '60分': '60m', '60分钟': '60m',
    '1d': '1d', 'day': '1d', 'daily': '1d', '日': '1d', '日线': '1d',
    '1wk': '1wk', '1w': '1wk', 'week': '1wk', 'weekly': '1wk', '周': '1wk', '周线': '1wk',
    '1mon': '1mon', 'month': '1mon', 'monthly': '1mon', '月': '1mon', '月线': '1mon',
}


def _resolve_period_str(cycle) -> str:
    """将 DZH analysis_cycle（整数 1-9 或字符串别名）解析为标准周期字符串。

    支持 DZH 整数编码（1=分笔…9=月线）、中文名（'日'/'周线'）、
    英文全称（'daily'/'weekly'）与标准代码（'1d'/'1wk'）。
    缺失或无效返回 '1d'，保持向后兼容。
    """
    if cycle is None or isinstance(cycle, bool):
        return '1d'
    if isinstance(cycle, int):
        return _DZH_CYCLE_TO_PERIOD.get(cycle, '1d')
    s = str(cycle).strip()
    if not s:
        return '1d'
    if s.isdigit():
        return _DZH_CYCLE_TO_PERIOD.get(int(s), '1d')
    return _PERIOD_STR_ALIASES.get(s, _PERIOD_STR_ALIASES.get(s.lower(), '1d'))


def _parse_indiparam(indiparam) -> Optional[dict]:
    """解析 DZH indiparam 字段为参数字典。

    indiparam 格式如 '(1,25,100,30,15,30)'（括号包裹的逗号分隔数值），
    不含参数名，按位置映射为 P1/P2/... 键。前端 formula_args（含参数名）优先。
    """
    if not indiparam:
        return None
    s = str(indiparam).strip()
    if s.startswith('(') and s.endswith(')'):
        s = s[1:-1]
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if not parts:
        return None
    args = {}
    for i, p in enumerate(parts, 1):
        try:
            args[f"P{i}"] = float(p)
        except ValueError:
            try:
                args[f"P{i}"] = int(p)
            except ValueError:
                continue
    return args or None


_JSON_CACHE = {}
_CFG_DIR = os.path.join(os.path.dirname(__file__), "..", "config")
def _load_config_json(filename, *, raise_on_error=False):
    """加载 config/<filename> 并缓存（单缓存层，参数化错误策略）。

    I38 统一：合并历史 _load_builtin_json（fail-fast）与 _load_json（静默）两套实现，
    消除双缓存 + 双定义。语义由 raise_on_error 显式声明，禁止隐式分派。

    raise_on_error=True : fail-fast，抛 ConfigLoadError（禁止静默返回空 dict）。
    raise_on_error=False（默认）: 静默返回 {}（兼容历史 _load_json 语义）。
    """
    if filename not in _JSON_CACHE:
        path = os.path.join(_CFG_DIR, filename)
        try:
            with open(path, encoding="utf-8") as f:
                _JSON_CACHE[filename] = json.load(f)
        except (OSError, json.JSONDecodeError) as ex:
            if raise_on_error:
                raise ConfigLoadError(
                    f"无法加载配置表 {filename}: {ex}（fail-fast：禁止静默返回空 dict）"
                ) from ex
            _JSON_CACHE[filename] = {}
    return _JSON_CACHE[filename]


# 降级条件检查器表：condition 名 → 检查函数（差异在表内容非代码分支）
_COND_CHECKERS = {
    "tq_available": lambda ctx: ctx["tq_available"],
    "bar_data_available": lambda ctx: bool(ctx["current_bar_data"]),
    "always": lambda ctx: True,
}


def _resolve_fallback(chain_name, stock_list, inputs):
    """统一降级分发器：根据 fallback_chain.json 配置选择处理方式，返回 (passed, rejected)。

    链耗尽策略由 ``on_exhaustion`` 字段驱动（表驱动）：
      - ``raise_error``（默认）：全部拒绝 + warning，禁止静默放行
      - ``pass_through``：返回原 stock_list，用于内联回退场景（bar_data 缺失时透传）
    支持两种配置格式：
      - 旧格式（list）：[{condition, handler}, ...]  （如 nset5_set_operation）
      - 新格式（dict）：{chain: [...], on_exhaustion, on_exhaustion_message, description}
    """
    tq = inputs.get("tq_adapter")
    tq_available = tq is not None and not getattr(tq, "mock_mode", False)
    try:
        chains = _load_config_json("fallback_chain.json", raise_on_error=True).get("chains", {})
    except Exception:
        chains = {}
    chain_config = chains.get(chain_name, [])
    on_exhaustion = "raise_error"
    on_exhaustion_message = "所有降级路径均不可用，显式失败（禁止 pass_through 静默放行）"
    if isinstance(chain_config, dict):
        chain = chain_config.get("chain", [])
        on_exhaustion = chain_config.get("on_exhaustion", "raise_error")
        on_exhaustion_message = chain_config.get(
            "on_exhaustion_message", on_exhaustion_message)
    else:
        chain = chain_config
    checker_ctx = {"tq_available": tq_available, "current_bar_data": inputs.get("current_bar_data")}
    for step in chain:
        cond, handler_name = step.get("condition", ""), step.get("handler", "")
        checker = _COND_CHECKERS.get(cond)
        if checker and not checker(checker_ctx):
            continue
        if handler_name == "pass_through":
            return list(stock_list), []
        if handler_name == "reject_all":
            return [], list(stock_list)
        handler = _HANDLERS.get(handler_name)
        if handler:
            try:
                result = handler({**inputs, "stock_list": stock_list})
                if isinstance(result, tuple) and len(result) == 2:
                    return result
                if isinstance(result, dict):
                    return result.get("passed", stock_list), result.get("rejected", [])
            except Exception:
                continue
    # 链耗尽：按 on_exhaustion 策略分派（表驱动，无 if/elif 分支）
    if on_exhaustion == "pass_through":
        return list(stock_list), []
    logger.warning(
        "fallback_chain[%s] exhausted: %s (stock_count=%d)",
        chain_name, on_exhaustion_message, len(stock_list) if stock_list else 0)
    return [], list(stock_list)


def _propagate(node_stocks, src_id, tgt_id, is_move, is_overwrite):
    # 表驱动：根据 flow_mode_registry.json 的 propagate_rules 决定清源/清目标/合并行为
    registry = _load_config_json("flow_mode_registry.json", raise_on_error=True)
    flag_map = registry.get("propagate_flag_map", {})
    mode = flag_map.get(f"{int(bool(is_move))},{int(bool(is_overwrite))}", "copy")
    rule = registry.get("propagate_rules", {}).get(mode, {})
    clear_source = rule.get("clear_source", bool(is_move))
    clear_dest = rule.get("clear_dest", bool(is_overwrite))

    src = node_stocks.get(src_id, [])
    if clear_dest:
        # 覆盖：先清空目标池再写入（深拷贝源）
        node_stocks[tgt_id] = [copy.deepcopy(s) if isinstance(s, dict) else s for s in src]
    else:
        existing = node_stocks.get(tgt_id, [])
        # 构建目标池映射：code → stock
        target_map = {}
        for s in existing:
            target_map[_stock_code(s)] = s
        # 流转进来的股票与同 code 的旧数据合并（旧数据独有字段保留，新数据字段覆盖）
        for s in src:
            code = _stock_code(s)
            if code in target_map and isinstance(target_map[code], dict) and isinstance(s, dict):
                merged = copy.deepcopy(target_map[code])
                merged.update(s)
                target_map[code] = merged
            else:
                target_map[code] = copy.deepcopy(s) if isinstance(s, dict) else s
        node_stocks[tgt_id] = list(target_map.values())
    if clear_source:
        node_stocks[src_id] = []


def _gen_sector_stocks(sector_code):
    try:
        data = _load_config_json("mock_data.json", raise_on_error=True)
    except Exception:
        return []
    sectors = data.get("sector_stocks", {})
    for key, stocks in sectors.items():
        if key.startswith(sector_code + "_"):
            return list(stocks)
    rules = data.get("sector_generation_rules", {})
    if rules.get("enabled"):
        import random
        min_s, max_s = rules.get("min_stocks", 8), rules.get("max_stocks", 25)
        sh_r = rules.get("sh_range", [600000, 601999])
        sz_r = rules.get("sz_range", [1, 3999])
        n = random.randint(min_s, max_s)
        result = [f"{c:06d}.SH" for c in random.sample(range(sh_r[0], sh_r[1] + 1), min(n // 2, sh_r[1] - sh_r[0] + 1))]
        result += [f"{c:06d}.SZ" for c in random.sample(range(sz_r[0], sz_r[1] + 1), min(n - len(result), sz_r[1] - sz_r[0] + 1))]
        return result
    return []


# ──────────────────────────────────────────────────────────────
# 筛选逻辑（原 builtins_filters.py）
# ──────────────────────────────────────────────────────────────

def _gen_stock_codes(market_id):
    try:
        markets = {m["id"]: m for m in _load_config_json("markets.json").get("markets", []) if "id" in m}
    except Exception: markets = {}
    m = markets.get(market_id)
    if not m: return []
    pat = m.get("stock_code_pattern", "")
    # HARDCODED: 不可剥离，理由：测试用 mock 股票代码生成规则，随 markets.json 的 regex 模式联动，无运行时配置必要
    if "SH\\d{6}" in pat: return [f"{c:06d}.SH" for c in range(600000, 600010)]
    if "SZ(00|30)" in pat: return [f"{c:06d}.SZ" for c in list(range(1, 11)) + list(range(300001, 300011))]
    if "SH9" in pat: return [f"9{c:05d}.SH" for c in range(10000, 10010)]
    if "SZ2" in pat: return [f"2{c:05d}.SZ" for c in range(10000, 10010)]
    if "SZ00" in pat: return [f"{c:06d}.SZ" for c in range(1, 11)]
    if "SZ30" in pat: return [f"{c:06d}.SZ" for c in range(300001, 300011)]
    if "B$" in pat: return [f"B${c:06d}" for c in range(1, 11)]
    return []
def _filter_by_bar_data(stock_list, bar_data):
    if not bar_data or not isinstance(bar_data, dict): return list(stock_list), []
    passed = [s for s in stock_list if _stock_code(s) in bar_data]
    return passed, [s for s in stock_list if s not in passed]
def _decode_type201_attr(attr_int):
    try: attr_int = int(attr_int)
    except (ValueError, TypeError): attr_int = 0
    try:
        bits = _load_config_json("field_definitions.json").get("bit_fields", {}).get("201", {})
    except Exception: bits = {}
    result = {"raw": attr_int}
    for name, info in bits.items():
        mask = info.get("mask_hex", "")
        if isinstance(mask, str):
            try: mask = int(mask, 16)
            except ValueError: mask = 0
        result[name] = bool(attr_int & mask) if mask else False
    return result
def _decode_action(action_val):
    if isinstance(action_val, dict): return action_val
    # HARDCODED: 不可剥离，理由：空值/零值守卫是输入校验的基础防御，不承载业务规则
    if action_val is None or action_val == 0 or action_val == "": return {}
    # 表驱动：委托共用 decode_action（单一数据源 filter_action_rules.json），消除重复解码逻辑
    try:
        from ..converters import decode_action as _shared_decode_action
    except ImportError:
        from converters import decode_action as _shared_decode_action
    return _shared_decode_action(action_val) or {}
def _apply_formula_mode(passed, rejected, mode, params):
    """按 formula_modes.json 的 action 查 formula_action_handlers 表分发。

    差异显示于表内容（handler 取值不同）：reverse→_action_reverse, pass_all→_action_pass_all, default→_action_default。
    rank 模式含 top_n 切片与越界保护，属于算法逻辑，在 _action_default 内部处理。
    """
    try:
        modes = _load_config_json("formula_modes.json").get("modes", {})
    except Exception: modes = {}
    cfg = modes.get(mode, {})
    action = cfg.get("action", "default")
    # 查 formula_action_handlers 表获取处理器函数名
    handlers = _load_config_json("analysis_config.json").get("formula_action_handlers", {})
    handler_cfg = handlers.get(action, handlers.get("default", {}))
    handler_name = handler_cfg.get("handler", "_action_default")
    handler = globals().get(handler_name)
    if handler:
        return handler(passed, rejected, mode=mode, params=params)
    return list(passed), list(rejected)
def _action_reverse(passed, rejected, **kw):
    """反向动作：交换 passed/rejected。"""
    return list(rejected), list(passed)
def _action_pass_all(passed, rejected, **kw):
    """全部放行动作：合并 passed 和 rejected。"""
    return list(passed) + list(rejected), []
def _action_default(passed, rejected, mode=None, params=None, **kw):
    """默认动作：保留 passed。

    rank 模式含 top_n 切片与越界保护，属于算法逻辑而非静态映射。
    """
    if mode == "rank" and params:
        try:
            n = int(params.get("top_n", 0))
            if 0 < n < len(passed): return list(passed[:n]), list(passed[n:]) + list(rejected)
        except (ValueError, TypeError) as e:
            logger.debug("rank top_n 解析失败，回退到全量返回: %s", e)
    return list(passed), list(rejected)
def _build_dzh_extra(passed, hs, cl, so, rh, cfp, ks, cdf, ds, oc, ctype, fcrc, ftype, is_ftype_zero, fdec, fsize, iparam, st, ea, xa):
    try:
        defaults = _load_config_json("dzh_extra_fields.json").get("defaults", {})
    except Exception: defaults = {}
    result = {"hold_sec": hs, "col_list": cl or [], "show_overview": so, "record_history": rh,
              "calc_profit_from_prev": cfp, "keep_source": ks, "clear_dest_first": cdf,
              "delete_source": ds, "output_constituent": oc, "condition_type": ctype,
              "formula_crc": fcrc, "formula_inditype": ftype, "is_inditype_zero": is_ftype_zero,
              "formula_decoded": fdec, "formula_size": fsize, "indiparam": iparam,
              "sort_type": st, "enter_action": ea, "exit_action": xa, "stocks": list(passed)}
    result.update({k: v for k, v in defaults.items() if k not in result})
    return result
def render_label(inputs): return {"passed": [], "rejected": []}
def render_shape(inputs):
    tq = inputs.get("tq_adapter"); result = tq.render_shape(inputs) if tq and not getattr(tq, "mock_mode", False) else {"shape_type": inputs.get("shape_type", ""), "rendered": True}; return {"passed": [result], "rejected": []}
def stock_pool_hold(inputs):
    stocks = inputs.get("stocks") or inputs.get("passed") or inputs.get("stock_list") or []
    enter_action = _decode_action(inputs.get("enter_action", {}))
    exit_action = _decode_action(inputs.get("exit_action", inputs.get("leave_action", {})))
    tq = inputs.get("tq_adapter")
    if tq and not getattr(tq, "mock_mode", False) and stocks:
        snapshots = tq.get_snapshot(stocks)
        holdings = []
        for s in stocks:
            code = _stock_code(s)
            snap = snapshots.get(code, {})
            entry = dict(s) if isinstance(s, dict) else {"code": code}
            entry["entry_price"] = snap.get("close", 0)
            entry["entry_volume"] = snap.get("volume", 0)
            holdings.append(entry)
        stocks = holdings
    return {"passed": stocks, "rejected": [], "stocks": stocks, "hold_sec": inputs.get("hold_sec", 432000), "extended": inputs.get("extended", False), "show_columns": inputs.get("show_columns", []), "enable_profit": inputs.get("enable_profit", False), "record_history": inputs.get("record_history", False), "show_overview": inputs.get("show_overview", False), "enter_action": enter_action, "exit_action": exit_action}
def _topn_filter(result, st, default=None):
    try:
        n = abs(int(st)) if str(st).lstrip('-').isdigit() else (default or 0)
        return result[:n] if 0 < n < len(result) else result
    except (ValueError, TypeError): return result[:default] if default and default < len(result) else result
def transfer_condition_check(inputs):
    sl = inputs.get("stock_list") or inputs.get("stocks") or inputs.get("passed") or []
    node = inputs.get("node", {})
    params = node.get("params", {}) if isinstance(node, dict) else inputs.get("params", {})
    araw = params.get("attr_int", 0) or params.get("dzh_attr", {})
    attr = araw if isinstance(araw, dict) else _decode_type201_attr(int(araw) if araw else 0)
    bar_data, st = inputs.get("current_bar_data"), params.get("sorttype", "")
    result = list(sl)
    if attr.get("indicator_condition"):
        formula = params.get("indi", "") or params.get("formula_decoded", "") or params.get("formula_script", "")
        if formula:
            _ency = params.get("ency", 0) or inputs.get("ency", 0)
            ft = decode_formula(formula, _ency) if params.get("indi") else formula
            tq = inputs.get("tq_adapter")
            formula_router = inputs.get("formula_router")
            cs = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ:;()<>=,+-*/._0123456789 \t\n\r')
            valid = ft and sum(1 for c in ft if c in cs) / max(len(ft), 1) > 0.5
            if valid and result:
                # 优先使用 FormulaRouter（正确路由到 Python/HQChart 引擎）
                if formula_router and hasattr(formula_router, 'eval_batch'):
                    try:
                        from ..core.screening_module import _run_async
                        # 动态分析周期与公式参数透传（替代硬编码 '1d' 与空 args）
                        tc_period = _resolve_period_str(
                            inputs.get("analysis_cycle") or params.get("analysis_cycle")
                            or params.get("period", "1d"))
                        tc_args = (inputs.get("formula_args") or params.get("formula_args")
                                   or _parse_indiparam(params.get("indiparam") or inputs.get("indiparam")))
                        eval_result = _run_async(
                            lambda: formula_router.eval_batch(
                                ft, result, period=tc_period, args=tc_args,
                                context={'bars': bar_data}))
                        if eval_result:
                            rd = {k: v for k, v in eval_result.items()}
                            # 检查是否有至少一个有效的评估结果（非 None/False）
                            has_valid = any(v is not None and v is not False for v in rd.values())
                            if has_valid:
                                result = [s for s in result
                                          if isinstance(rd.get(s), (int, float)) and not isinstance(rd.get(s), bool) and rd[s] > 0
                                          or rd.get(s) is True]
                            else:
                                # I33：公式评估全部失败，表驱动回退（formula_eval_inner_fallback 子链）
                                logger.warning("公式评估全部失败（无有效结果），回退到 bar_data 过滤")
                                result = _resolve_fallback("formula_eval_inner_fallback", result, inputs)[0]
                    except Exception as e:
                        logger.warning("FormulaRouter.eval_batch 失败，回退到 bar_data 过滤: %s", e)
                        result = _resolve_fallback("formula_eval_inner_fallback", result, inputs)[0]
                else:
                    logger.warning("formula_router 不可用；公式评估跳过")
                    result = _resolve_fallback("formula_eval_inner_fallback", result, inputs)[0]
            elif result:
                result = _resolve_fallback("formula_eval_inner_fallback", result, inputs)[0]
    if attr.get("basic_condition") and not attr.get("indicator_condition"): result = _topn_filter(result, st)
    if attr.get("ranking_condition"): result = _topn_filter(result, st, 10)
    if attr.get("reverse_transfer"):
        ac = inputs.get("all_codes", []); result = [s for s in ac if s not in set(result)] if ac else result
    if attr.get("cross_section"): result = _filter_by_bar_data(result, bar_data)[0]
    if attr.get("basic_condition") and attr.get("indicator_condition"): result = _topn_filter(result, params.get("sorttype", ""))
    return {"passed": result, "rejected": [s for s in sl if s not in result]}
def resolve_market(inputs):
    markets = inputs.get("markets", ["sh_a", "sz_a"])
    tq = inputs.get("tq_adapter")
    if tq and not getattr(tq, "mock_mode", False):
        stock_list = [c for m in markets for c in tq.resolve_market(markets).get(m, [])]
    else:
        stock_list = [c for m in markets for c in _gen_stock_codes(m)]
    sector_codes = inputs.get("sector_codes")
    if sector_codes and stock_list:
        if isinstance(sector_codes, str): sector_codes = [s.strip() for s in sector_codes.split(',') if s.strip()]
        for sc in sector_codes: stock_list = sector_filter({"stock_list": stock_list, "sector_code": sc, "tq_adapter": tq}).get("passed", stock_list)
    custom_stocks = inputs.get("custom_stocks")
    if custom_stocks:
        if isinstance(custom_stocks, str): custom_stocks = [s.strip() for s in custom_stocks.split(',') if s.strip()]
        existing = set(stock_list)
        for cs in custom_stocks:
            if cs not in existing:
                stock_list.append(cs)
                existing.add(cs)
    return {"passed": stock_list, "rejected": [], "stock_list": stock_list, "count": len(stock_list)}
def discard_sink_drop(inputs):
    stocks, rejected = inputs.get("stocks", []), (inputs.get("rejected") or inputs.get("rejected_list") or [])
    src, cond = inputs.get("source_node", ""), inputs.get("condition", {})
    if not rejected and stocks: rejected = list(stocks)
    import time as _time
    discarded = []
    for stock in rejected:
        if isinstance(stock, dict):
            s = dict(stock)
            s["_discarded_at"] = _time.time()
            s["_discard_source"] = src or cond.get("name", "unknown")
            discarded.append(s)
        else:
            discarded.append({"code": str(stock), "_discarded_at": _time.time(), "_discard_source": src, "original": stock})
    return {"passed": discarded, "rejected": [], "stocks": discarded, "count": len(discarded), "source": src, "condition": cond}
def time_trigger_check(inputs):
    import datetime
    times = inputs.get("times", [])
    result = {"triggered": False} if not times else {"triggered": datetime.datetime.now().strftime("%H:%M") in times, "current_time": datetime.datetime.now().strftime("%H:%M"), "times": times}
    result["passed"] = []
    result["rejected"] = []
    return result
def _fetch_snapshot(tq, samples, tcfg, fetch_cfg):
    """get_snapshot 数据获取：返回 (snaps, kdata={})。"""
    return tq.get_snapshot(samples), {}


def _fetch_kline_data(tq, samples, tcfg, fetch_cfg):
    """get_kline_data 数据获取：从 tcfg 取 count/period 后返回 (snaps={}, kdata)。"""
    count = tcfg.get(fetch_cfg.get("count_key", "tq_kline_count"), 6)
    period = tcfg.get(fetch_cfg.get("period_key", "tq_kline_period"), 60)
    return {}, tq.get_kline_data(samples, count, period)


# 数据获取方法表：method 名 → (tq, samples, tcfg, fetch_cfg) → (snaps, kdata)
_DATA_METHODS = {
    "get_snapshot": _fetch_snapshot,
    "get_kline_data": _fetch_kline_data,
}


def profit_analysis_calc(inputs):
    """按 analysis_types[*].result_structure 查 result_structure_handlers 表分发结果构建。

    差异显示于表内容（handler/items_key/summary_field/atype 取值不同）：
      aggregate→_build_aggregate_report, per_stock→_build_per_stock_report(stocks),
      per_stock_with_summary→_build_per_stock_report(positions, total_market_value, positioning)。
    数据获取逻辑（get_snapshot/get_kline_data）查 _DATA_METHODS 表分派。
    字段计算顺序由 config 中 fields 的顺序决定，后字段可依赖前字段（通过 item 上下文）。
    """
    stocks = inputs.get("stocks") or inputs.get("passed") or inputs.get("stock_list") or []
    atype, tq = inputs.get("analysis_type", "intraday"), inputs.get("tq_adapter")
    tcfg = _load_config_json("analysis_config.json").get("analysis_types", {}).get(atype, {})
    samples = stocks if stocks else tcfg.get("default_sample_stocks", ["000001.SZ", "600000.SH"])
    snaps, kdata = {}, {}
    if tq and not getattr(tq, "mock_mode", False):
        fetch_cfg = tcfg.get("data_fetch", {})
        fetcher = _DATA_METHODS.get(fetch_cfg.get("method"))
        if fetcher:
            snaps, kdata = fetcher(tq, samples, tcfg, fetch_cfg)
    title, structure, fields = tcfg.get("title", atype), tcfg.get("result_structure", "per_stock"), tcfg.get("fields", [])
    real = bool(snaps or kdata)
    # 查 result_structure_handlers 表获取处理器函数名
    handlers = _load_config_json("analysis_config.json").get("result_structure_handlers", {})
    handler_cfg = handlers.get(structure, handlers.get("per_stock", {}))
    handler_name = handler_cfg.get("handler")
    handler = globals().get(handler_name) if handler_name else None
    if handler:
        return {"passed": [], "rejected": [], "report": handler(title, atype, fields, samples, snaps, kdata, real, handler_cfg)}
    # 回退：普通 per_stock 报告
    return {"passed": [], "rejected": [], "report": _build_per_stock_report(title, atype, fields, samples, snaps, kdata, real, {"items_key": "stocks"})}
def _build_aggregate_report(title, atype, fields, samples, snaps, kdata, real, cfg):
    """构建聚合报告：各字段通过 _calc_aggregate_field_from_formula 计算，无数据时取 default。"""
    result = {"title": title, "analysis_type": atype}
    for field in fields:
        fn = field["name"]
        v = _calc_aggregate_field_from_formula(field, snaps, kdata) if real else None
        result[fn] = v if v is not None else field.get("default", 0)
    return result
def _build_per_stock_report(title, atype, fields, samples, snaps, kdata, real, cfg):
    """构建逐股报告：各字段通过 _calc_field_from_formula 计算，code 字段透传。

    items_key 决定结果中股票列表的键名（stocks 或 positions）。
    summary_field 存在时额外计算汇总值（如 total_market_value）。
    atype 过滤：per_stock_with_summary 仅对配置的 atype 生效，否则回退到普通 per_stock。
    """
    # atype 过滤：per_stock_with_summary 仅对配置的 atype 生效
    expected_atype = cfg.get("atype")
    if expected_atype and atype != expected_atype:
        cfg = {"items_key": "stocks"}
    items, tmv = [], 0
    for code in samples:
        item = {"code": code}
        snap = snaps.get(code, {}) if isinstance(snaps, dict) else {}
        for field in fields:
            fn = field["name"]
            # code 是标识字段，必须跳过计算直接透传
            if fn == "code": continue
            if real and (v := _calc_field_from_formula(field, code, snap, item)) is not None: item[fn] = v
            else: item[fn] = _calc_mock_field(field, code)
        items.append(item)
        tmv += item.get("market_value", 0)
    items_key = cfg.get("items_key", "stocks")
    summary_field = cfg.get("summary_field")
    result = {"title": title, "analysis_type": atype, items_key: items}
    if summary_field:
        result[summary_field] = round(tmv, 2)
    return result
def _calc_mock_field(field, code):
    """查 mock_field_ranges.json 表按 (suffix, modulus, offset, divisor, round, sign) 生成 mock 值。

    消除内联 magic numbers（% 1000/% 9000/% 9500 等），范围常量外置到 config。
    """
    fn, c = field["name"], code
    spec = _load_config_json("mock_field_ranges.json", raise_on_error=True).get("fields", {}).get(fn)
    if not spec:
        return field.get("default", 0)
    raw = (hash(c + spec.get("suffix", "")) % spec.get("modulus", 1) + spec.get("offset", 0)) / spec.get("divisor", 1)
    return round(spec.get("sign", 1) * raw, spec.get("round", 2))
# 聚合函数实现查表：按 aggregate_funcs 表的 impl 字段查表获取实现
_AGGR_IMPLS = {
    "mean": lambda values: sum(values) / len(values),
    "stdev": statistics.stdev,
}


def _calc_field_from_formula(field, code, snap, item):
    """按字段公式配置计算字段值（ast 受控求值，禁止 eval）。

    上下文为 snap 与 item 的合并：公式可引用快照字段（close/volume 等）和已计算字段。
    guard 条件为假或 NULL 传播时返回 None；公式求值失败返回 None。
    round 字段指定四舍五入精度。
    差异显示于表内容（formula/guard/round 取值不同），消除 fn if 分支。
    """
    if not field.get("formula"): return None
    formula = field["formula"]
    guard = field.get("guard")
    context = {**snap, **item}
    try:
        value = _eval_derived_expr(formula, context, guard)
        if value is None:
            return None
        rnd = field.get("round")
        return round(value, rnd) if rnd is not None else value
    except Exception as e:
        logger.warning("calc_field_from_formula 字段计算异常 field=%s: %s", field["name"], e)
    return None
def _calc_aggregate_field_from_formula(field, snapshots, kline_data):
    """按字段公式配置计算聚合字段值（ast 解析函数调用，查 aggregate_funcs 表分发）。

    公式格式为 func_name(field_name)，如 mean(change_pct)、std(change_pct)。
    数据源为 snapshots 中各快照的 field_name 值。
    差异显示于表内容（impl/min_count 取值不同），消除 fn if 分支。
    min_count 为最小数据量要求，不足则返回 None。
    """
    if not field.get("formula"): return None
    formula = field["formula"]
    try:
        tree = ast.parse(formula, mode="eval")
        call = tree.body
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            return None
        func_name = call.func.id
        if not call.args or not isinstance(call.args[0], ast.Name):
            return None
        arg_field = call.args[0].id
        # 查 aggregate_funcs 表获取实现名和最小数据量
        aggr_cfg = _load_config_json("analysis_config.json").get("aggregate_funcs", {}).get(func_name)
        if not aggr_cfg:
            return None
        impl_name = aggr_cfg.get("impl")
        min_count = aggr_cfg.get("min_count", 1)
        impl = _AGGR_IMPLS.get(impl_name)
        if not impl or not snapshots:
            return None
        # 收集数据：从各快照中提取 arg_field 值，缺失默认 0
        values = [s.get(arg_field, 0) for s in snapshots.values() if isinstance(s, dict)]
        if len(values) < min_count:
            return None
        result = impl(values)
        rnd = field.get("round")
        return round(result, rnd) if rnd is not None else result
    except Exception as e:
        logger.warning("calc_aggregate_field 字段计算异常 field=%s: %s", field["name"], e)
    return None
def candidate_resolve(inputs): return resolve_market(inputs)
def accumulate_state(inputs): return stock_pool_hold(inputs)
def discard_stocks(inputs): return discard_sink_drop(inputs)
def formula_eval(inputs):
    """公式评估：基于 FormulaRouter 对股票列表执行公式筛选。

    失败处理契约（禁止 pass_through 静默放行，对就是对错就是错）：
      - formula_router 不可用：记录 error 并显式返回失败（passed=[], rejected=全部）
      - 公式文本为空：记录 error 并显式返回失败
      - 公式评估抛异常：记录 error 并显式返回失败
      - 股票列表为空：返回空结果（非失败，无股票可评估，不构成 pass_through）

    返回 dict，包含 passed/rejected/mode 字段；失败时额外包含 success=False 和 error 字段。
    禁止调用 _resolve_fallback；禁止 pass_through（无条件放行）。
    """
    ctype, params = inputs.get("condition_type", ""), inputs.get("params", {})
    sl = inputs.get("stock_list") or inputs.get("stocks") or inputs.get("passed") or []
    mode = inputs.get("mode", "indi")
    hs, cl = inputs.get("hold_sec", 432000), inputs.get("col_list", [])
    so, rh = inputs.get("show_overview", False), inputs.get("record_history", False)
    cfp, si = inputs.get("calc_profit_from_prev", False), inputs.get("simple_intermediate", False)
    ks, cdf = inputs.get("keep_source", False), inputs.get("clear_dest_first", False)
    ds, oc, st, fr = inputs.get("delete_source", False), inputs.get("output_constituent", False), inputs.get("sort_type", ""), inputs.get("formula_raw", "")
    fcrc, ftype, iparam = inputs.get("formula_crc", ""), inputs.get("formula_inditype", ""), inputs.get("indiparam")
    ea, xa = _decode_action(inputs.get("enter_action", {})), _decode_action(inputs.get("exit_action", {}))
    is_dzh = any(k in inputs for k in ("hold_sec", "col_list", "enter_action", "exit_action", "formula_raw", "formula_crc", "formula_inditype", "simple_intermediate", "sort_type"))
    _ency = params.get("ency", 0) or inputs.get("ency", 0)
    if fr: fdec, fsize = _decode_formula_base64(fr, _ency)
    elif inputs.get("indi"): fdec, fsize = _decode_formula_base64(inputs["indi"], _ency)
    else: fdec, fsize = "", 0
    # HARDCODED: 不可剥离，理由：inditype==0 是 DZH 公式类型零值哨兵，用于 _build_dzh_extra 中
    # is_inditype_zero 标志；未来可由 field_definitions.json 的公式类型默认值表覆盖。
    dzh_args = (hs, cl, so, rh, cfp, ks, cdf, ds, oc, ctype, fcrc, ftype, str(ftype) == "0", fdec, fsize, iparam, st, ea, xa)
    def _build(p, r):
        base = {"passed": p, "rejected": r, "mode": mode}
        if is_dzh: base.update(_build_dzh_extra(p, *dzh_args))
        return base
    def _fail(error_msg, exc=None):
        """显式失败：记录 error 日志并返回失败结果（禁止 pass_through 静默放行）。"""
        logger.error("formula_eval 失败: %s (stock_count=%d, mode=%s)%s",
                     error_msg, len(sl) if sl else 0, mode,
                     f" 异常: {exc}" if exc else "")
        base = _build([], list(sl))
        base["success"] = False
        base["error"] = error_msg
        return base
    # 公式评估统一由 FormulaRouter 路由，不再 fallback 到 tq_adapter
    formula_router = inputs.get("formula_router")
    has_router = formula_router and hasattr(formula_router, 'eval_batch')
    if not has_router:
        return _fail("formula_router 不可用，无法执行公式评估")
    # 股票列表为空：返回空结果（非失败，无股票可评估，不构成 pass_through）
    if not sl:
        logger.warning("formula_eval: 股票列表为空，无股票可评估 (mode=%s)", mode)
        return _build([], [])
    # 解析公式文本
    indi = inputs.get("indi") or inputs.get("formula", "") or params.get("indi", "") or fr
    ft = decode_formula(indi, _ency) if indi else fdec
    if not ft:
        ft = inputs.get("formula_decoded", "") or params.get("formula_decoded", "")
    # 公式文本为空：显式失败，禁止 pass_through
    if not ft:
        return _fail("公式文本为空，无法执行公式评估")
    # 动态分析周期：从 analysis_cycle 解析为标准周期字符串（替代硬编码 '1d'）
    period = _resolve_period_str(inputs.get("analysis_cycle") or params.get("period", "1d"))
    # 公式参数：优先使用前端 formula_args（含参数名），无则解析 indiparam（按位置映射）
    formula_args = inputs.get("formula_args") or params.get("formula_args")
    if not formula_args:
        formula_args = _parse_indiparam(iparam)
    # 执行公式评估：统一使用 FormulaRouter
    rd = {}
    try:
        from ..core.screening_module import _run_async
        raw = _run_async(lambda: formula_router.eval_batch(
            ft, sl, period=period, args=formula_args))
        if isinstance(raw, dict):
            rd = {k: v for k, v in raw.items()}
    except Exception as ex:
        return _fail("FormulaRouter.eval_batch 抛异常", exc=ex)
    passed, rejected = [], []
    for code in sl:
        v = rd.get(code, 0)
        (passed if isinstance(v, (int, float)) and v > 0 or (isinstance(v, str) and v.upper() in ("PASS", "TRUE", "1")) else rejected).append(code)
    passed, rejected = _apply_formula_mode(passed, rejected, mode, params)
    if st: passed = _topn_filter(passed, st)
    base = _build(passed, rejected)
    if is_dzh and si: base["intermediate_mode"] = True
    return base
def sector_filter(inputs):
    sl = inputs.get("stock_list") or inputs.get("stocks") or inputs.get("passed") or []
    sc, tq = inputs.get("sector_code", ""), inputs.get("tq_adapter")
    if tq and not getattr(tq, "mock_mode", False):
        ss = set(tq.get_block_members(sc) if sc else [])
        p = [s for s in sl if _stock_code(s) in ss]
        return {"passed": p, "rejected": [s for s in sl if s not in p], "sector": sc}
    p, r = _resolve_fallback("sector_filter", sl, inputs)
    return {"passed": p, "rejected": r, "sector": sc}
def cross_section_eval(inputs):
    sl = inputs.get("stock_list") or inputs.get("stocks") or inputs.get("passed") or []
    formula_router = inputs.get("formula_router")
    has_router = formula_router and hasattr(formula_router, 'eval_batch')
    if has_router:
        params = inputs.get("params", {})
        indi = inputs.get("indi") or params.get("indi", "")
        ft = decode_formula(indi) if indi else ""
        n = params.get("top_n", 0)
        if ft and sl:
            rd = {}
            try:
                from ..core.screening_module import _run_async
                raw = _run_async(lambda: formula_router.eval_batch(ft, sl, period='1d'))
                if isinstance(raw, dict):
                    rd = raw
            except Exception:
                pass
            scored = [(c, rd.get(c, 0)) for c in sl]
            scored.sort(key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0, reverse=True)
            s = max(1, min(n, len(scored) - 1)) if n and n < len(scored) else len(scored) // 2
            return {"passed": [x[0] for x in scored[:s]], "rejected": [x[0] for x in scored[s:]]}
        return {"passed": list(sl), "rejected": []}
    p, r = _resolve_fallback("cross_section_eval", sl, inputs)
    return {"passed": p, "rejected": r}
def basic_filter(inputs):
    sl = inputs.get("stock_list") or inputs.get("stocks") or inputs.get("passed") or []
    tq = inputs.get("tq_adapter")
    if tq and not getattr(tq, "mock_mode", False):
        params = inputs.get("params", {})
        pmax, pbmax, rmin = params.get("pe_max", 100), params.get("pb_max", 10), params.get("roe_min", 0)
        if sl:
            fd = tq.get_financial_data(sl, params.get("fields", ["pe", "pb", "roe"]))
            passed, rejected = [], []
            for s in sl:
                fin = fd.get(_stock_code(s), {})
                ok = (fin.get("pe", 999) is None or (isinstance(fin.get("pe", 999), (int, float)) and fin.get("pe", 999) <= pmax)) and (fin.get("pb", 999) is None or (isinstance(fin.get("pb", 999), (int, float)) and fin.get("pb", 999) <= pbmax)) and (fin.get("roe", -999) is None or (isinstance(fin.get("roe", -999), (int, float)) and fin.get("roe", -999) >= rmin))
                (passed if ok else rejected).append(s)
            return {"passed": passed, "rejected": rejected}
        return {"passed": [], "rejected": []}
    p, r = _resolve_fallback("basic_filter", sl, inputs)
    return {"passed": p, "rejected": r}
def pass_through(inputs):
    return {"passed": inputs.get("stock_list") or inputs.get("stocks") or inputs.get("passed") or [], "rejected": [], "mode": "passthrough"}

# 表驱动：set_op → 集合合并算子映射，1 个通用解释器替代 AND/OR 分派方法
_SET_OPS = {
    "intersection": lambda acc, s: acc & s,
    "union": lambda acc, s: acc | s,
}
def condition_dispatcher(inputs):
    stocks = inputs.get("stock_list") or inputs.get("stocks") or inputs.get("passed") or []
    conditions, match_mode = inputs.get("conditions", []), inputs.get("match_mode", "AND")
    st, di = inputs.get("sort_type"), inputs.get("_dispatch_index") or inputs.get("dispatch_index") or {}
    if not stocks: return {"passed": [], "rejected": [], "count": 0}
    active, cresults = [], {}
    formula_router = inputs.get("formula_router")
    has_router = formula_router and hasattr(formula_router, 'eval_batch')
    if not has_router:
        passed, failed = _resolve_fallback("condition_dispatcher", stocks, inputs)
        if st and passed: passed = _topn_filter(passed, st)
        rks = list(set(r["rule"] for r in active))
        return {"passed": passed, "rejected": failed, "count": len(passed), "sort_applied": st, "match_mode": match_mode, "dispatch_rules_used": rks, "active_rules": active, "dispatch_table": list(di.keys())}
    for cond in conditions:
        ca = cond.get("attr", cond.get("attr_int", 0)) if isinstance(cond, dict) else (int(cond) if isinstance(cond, str) and cond.lstrip('-').isdigit() else 0)
        matched = "PASSTHROUGH"
        for rk, rv in di.items():
            if rv.get("bit_mask") is not None and (ca & rv["bit_mask"]): matched = rk; break
        active.append({"rule": matched, "gateway": di.get(matched, {}).get("gateway", "pass_through"), "attr": ca, "cond": cond})
        cf = cond.get("indi", "") if isinstance(cond, dict) else ""
        ft = decode_formula(cf) if cf else ""
        if ft and stocks:
            rd = {}
            try:
                from ..core.screening_module import _run_async
                raw = _run_async(lambda: formula_router.eval_batch(ft, stocks, period='1d'))
                if isinstance(raw, dict):
                    rd = raw
            except Exception:
                pass
            cresults[ca] = {c for c in stocks if (isinstance(rd.get(c), (int, float)) and rd[c] > 0) or (isinstance(rd.get(c), str) and rd[c].upper() in ("PASS", "TRUE", "1"))}
        else:
            cresults[ca] = set(stocks)
    if not cresults:
        passed = list(stocks)
    else:
        # 表驱动：查 match_modes.json 的 set_op 字段，通用集合合并器按 set_op 执行交集/并集
        mm_cfg = _load_config_json("match_modes.json").get("match_modes", {})
        mode_entry = mm_cfg.get(match_mode.upper()) or mm_cfg.get(match_mode) or {}
        set_op = mode_entry.get("set_op", "union")
        merge = _SET_OPS.get(set_op, _SET_OPS["union"])
        sets = list(cresults.values())
        ps = sets[0]
        for s in sets[1:]: ps = merge(ps, s)
        passed = [s for s in stocks if s in ps]
    failed = [s for s in stocks if s not in set(passed)]
    if st and passed: passed = _topn_filter(passed, st)
    rks = list(set(r["rule"] for r in active))
    return {"passed": passed, "rejected": failed, "count": len(passed), "sort_applied": st, "match_mode": match_mode, "dispatch_rules_used": rks, "active_rules": active, "dispatch_table": list(di.keys())}


# ──────────────────────────────────────────────────────────────
# 动作逻辑（原 builtins_actions.py）—— 表驱动 action pipeline
# 差异显示于 action_pipeline.json 的 steps 列表内容，非独立函数体
# ──────────────────────────────────────────────────────────────

def _step_resolve(inputs):
    """step: 解析源节点候选股票（通过 resolve_fn handler）。"""
    ns, nodes = inputs["node_stocks"], inputs["nodes"]
    sid = inputs["sid"]
    # 表驱动：markets 默认回退到 ["sh_a", "sz_a"]，避免股票列表被清空
    markets = inputs.get("markets") or ["sh_a", "sz_a"]
    if inputs.get("resolve_fn"):
        r = _handler_registry.get(inputs["resolve_fn"])
        if r:
            res = r({"pool_config": inputs.get("pool_config", {}), "node": nodes.get(sid, {}),
                     "node_stocks": ns, "tq_adapter": inputs.get("tq_adapter"),
                     "markets": markets})
            sl = res.get("stock_list", []) if isinstance(res, dict) else []
            # 表驱动：若 sl 为空（数据源不可用），保留原 sid 股票不覆盖
            existing = ns.get(sid) or []
            if sl:
                ns[sid] = [{"code": c, "label": c} if isinstance(c, str) else c for c in sl]
            else:
                ns[sid] = list(existing)
    ns[sid] = ns.get(sid) or []
    return inputs

def _step_pass(inputs):
    """step: 拷贝源池股票到目标池（支持 is_move 清空源）。"""
    ns = inputs["node_stocks"]
    sid, tid = inputs["sid"], inputs["tid"]
    ns[sid] = ns.get(sid) or []
    ns[tid] = list(ns[sid])
    if inputs.get("is_move", False): ns[sid] = []
    return inputs

def _step_filter(inputs):
    """step: 应用转移条件过滤（condition_dispatcher 或 transfer_condition_check 回退）。"""
    ns, nodes = inputs["node_stocks"], inputs["nodes"]
    sid, tid = inputs["sid"], inputs["tid"]
    cn = nodes.get(sid, {})
    sc = [_stock_code(s) for s in ns.get(sid, [])]
    cb = inputs.get("current_bar_data")
    fr = inputs.get("formula_router")
    cn_params = cn.get("params", {}) if isinstance(cn, dict) else {}
    conditions = cn_params.get("conditions", [])
    # 若 conditions 数组为空但节点携带 formula_script/indi/formula_decoded 字段，
    # 则回退到 transfer_condition_check（支持单公式字段，condition_dispatcher 仅支持 conditions 数组）
    if "condition_dispatcher" in _HANDLERS and conditions:
        r = condition_dispatcher({"stock_list": sc, "conditions": conditions,
            "_dispatch_index": inputs.get("dispatch_index", {}),
            "tq_adapter": inputs.get("tq_adapter"), "node": cn, "current_bar_data": cb,
            "formula_router": fr})
    else:
        r = transfer_condition_check({"pool_config": inputs.get("pool_config", {}), "node": cn,
            "node_stocks": ns, "tq_adapter": inputs.get("tq_adapter"), "stock_list": sc, "stocks": sc,
            "current_bar_data": cb, "formula_router": fr})
    fl = r.get("passed") if isinstance(r, dict) and r.get("passed") is not None else []
    rejected = r.get("rejected") if isinstance(r, dict) else []
    # 保存 rejected 股票到特殊字段，供同源的 discard_rejected 边读取
    if rejected:
        ns["_rejected_from_" + sid] = list(rejected)
    ns[sid] = [s for s in ns.get(sid, []) if _stock_code(s) in set(fl)]
    return inputs

def _step_dzh_filter(inputs):
    """step: DZH 公式条件过滤（构建 formula_eval 输入并执行）。"""
    ns, nodes = inputs["node_stocks"], inputs["nodes"]
    sid = inputs["sid"]
    ss = ns.get(sid, [])
    sc = [_stock_code(s) for s in ss]
    sn = nodes.get(sid, {})
    sp = sn.get("params", {}) if isinstance(sn, dict) else {}
    ei = {"stock_list": sc, "stocks": sc, "tq_adapter": inputs.get("tq_adapter"),
          "current_bar_data": inputs.get("current_bar_data"), "node": sn,
          "hold_sec": 432000, "col_list": [], "enter_action": {}, "exit_action": {}}
    for k in ("sort_type", "formula_raw", "formula_crc", "formula_inditype", "indiparam",
              "condition_type", "show_overview", "record_history", "calc_profit_from_prev",
              "simple_intermediate", "keep_source", "clear_dest_first", "delete_source",
              "output_constituent", "analysis_cycle", "indi", "formula", "mode"):
        ei[k] = inputs.get(k, sp.get(k))
    r = formula_eval(ei)
    ft = r.get("passed", sc) if isinstance(r, dict) else sc
    ns[sid] = [s for s in ss if _stock_code(s) in set(ft)]
    return inputs

def _step_propagate(inputs):
    """step: 传播源池到目标池（copy/move + overwrite）。"""
    ns = inputs["node_stocks"]
    _propagate(ns, inputs["sid"], inputs["tid"], inputs.get("is_move", False), inputs.get("is_overwrite", False))
    return inputs

def _step_transfer(inputs):
    """step: 池间转移（propagate）。"""
    ns = inputs["node_stocks"]
    _propagate(ns, inputs["sid"], inputs["tid"], inputs.get("is_move", False), inputs.get("is_overwrite", False))
    return inputs

def _step_remove(inputs):
    """step: 从池中移除股票（读取 rejected 传播到丢弃池，或 move 模式清空源）。"""
    ns = inputs["node_stocks"]
    sid, tid = inputs["sid"], inputs["tid"]
    # 从 _step_filter 保存的 rejected 股票字段读取，传播到丢弃池
    rejected_key = "_rejected_from_" + sid
    rejected_stocks = ns.pop(rejected_key, None)
    if rejected_stocks:
        ns[tid] = list(rejected_stocks)
    elif inputs.get("is_move", False):
        ns[sid] = []
    return inputs

# step 函数注册表：step 名 → 函数
_STEP_FUNCS = {
    "resolve": _step_resolve,
    "pass": _step_pass,
    "filter": _step_filter,
    "dzh_filter": _step_dzh_filter,
    "propagate": _step_propagate,
    "transfer": _step_transfer,
    "remove": _step_remove,
}

def _execute_action(action_name, inputs):
    """通用 action 执行器：按 action_pipeline.json 的 steps 列表顺序执行 step 函数。

    表驱动：多 action 差异隐含于 steps 列表内容，非独立函数体。
    """
    steps = _load_config_json("action_pipeline.json").get("actions", {}).get(action_name, {}).get("steps", [])
    for step in steps:
        fn = _STEP_FUNCS.get(step)
        if fn:
            inputs = fn(inputs)
    return inputs

# 表驱动：action 名 → _execute_action 绑定（消除独立函数体，差异在 action_pipeline.json 的 steps 列表）
def _make_action(action_name):
    return lambda inputs: _execute_action(action_name, inputs)

_action_resolve_and_pass = _make_action("resolve_and_pass")
_action_apply_filter = _make_action("apply_filter")
_action_dzh_condition_filter = _make_action("dzh_condition_filter")
_action_pass_pool_stocks = _make_action("pass_pool_stocks")
_action_transfer_between_pools = _make_action("transfer_between_pools")
_action_remove_from_pool = _make_action("remove_from_pool")

def tdx_condition_evaluator(inputs):
    ns, nodes = inputs["node_stocks"], inputs["nodes"]
    sid, tid = inputs["sid"], inputs["tid"]
    sp = inputs.get("src_params", {})
    ss = ns.get(sid, [])
    if sp.get("emptyps", 0) != 1 and not ss: return ns
    cn = nodes.get(tid, {})
    params = cn.get("params", {}) if isinstance(cn, dict) else {}
    func = params.get("tdx_func") or params.get("func") or {}
    if not isinstance(func, dict): func = {}
    sc = [_stock_code(s) for s in ss]
    try:
        if '_dispatch_cache' not in globals():
            with open(os.path.join(os.path.dirname(__file__), "..", "config", "architecture", "dispatch.json"), encoding="utf-8") as f:
                globals()['_dispatch_cache'] = json.load(f).get("nset_dispatch", {})
        nset_dispatch = _dispatch_cache
    except Exception: nset_dispatch = {}
    try:
        nset_val = str(int(func.get("nset", 1)))
    except (ValueError, TypeError):
        nset_val = "1"
    entry = nset_dispatch.get(nset_val)
    passed = []
    # 表驱动：查 dispatch.json 的 bypass_eval_tdx_condition + direct_handler
    # bypass=true 时直接调用 direct_handler（如 nset=5 集合运算），跳过 eval_tdx_condition
    if entry and entry.get("bypass_eval_tdx_condition") and entry.get("direct_handler"):
        try:
            from ..core.screening_module import eval_nset5_set_operation
            passed = eval_nset5_set_operation(
                {"src_params": {"tdx_func": func}, "stock_list": sc,
                 "tq_adapter": inputs.get("tq_adapter"), "current_bar_data": inputs.get("current_bar_data"),
                 "node_stocks": ns, "sid": sid, "tid": tid, "edges": inputs.get("edges", []),
                 "formula_router": inputs.get("formula_router"),
                 "market_data_port": inputs.get("market_data_port")})
        except Exception as ex:
            import logging as _logging
            _logging.getLogger(__name__).warning("tdx_condition_evaluator bypass异常 (sid=%s, tid=%s): %s", sid, tid, ex)
            passed = []
    elif entry:
        try:
            from ..core.screening_module import eval_tdx_condition
            passed = eval_tdx_condition(entry.get("dispatch_key", ""),
                {"src_params": {"tdx_func": func}, "stock_list": sc,
                 "tq_adapter": inputs.get("tq_adapter"), "current_bar_data": inputs.get("current_bar_data"),
                 "node_stocks": ns, "sid": sid, "tid": tid, "edges": inputs.get("edges", []),
                 "formula_router": inputs.get("formula_router"),
                 "market_data_port": inputs.get("market_data_port")})
        except Exception as ex:
            import logging as _logging
            _logging.getLogger(__name__).warning("tdx_condition_evaluator 评估器异常 (sid=%s, tid=%s): %s", sid, tid, ex)
            passed = []
    # 防御：评估器返回 None / 非列表 → 视为无股票通过
    if not isinstance(passed, list):
        passed = []
    # 条件评估为空意味着没有股票通过，不再降级穿透
    if not passed and sc:
        pass  # 保持 passed=[]，不调用 _resolve_fallback
    ns[tid] = [s for s in ss if _stock_code(s) in set(passed)]
    if inputs.get("is_move", False):
        passed_set = set(passed)
        ns[sid] = [s for s in ss if _stock_code(s) not in passed_set]
    return ns

def edge_default_transfer(inputs):
    # 表驱动：兼容两种调用方式：
    #   1) engine 直接调用：edge_default_transfer(ai)  → ai 即 action_inputs
    #   2) 测试/外部封装：edge_default_transfer({"action_inputs": ai, "strategy": strat})
    if isinstance(inputs, dict) and "action_inputs" in inputs and isinstance(inputs["action_inputs"], dict):
        ai = inputs.get("action_inputs") or {}
        strat = inputs.get("strategy") or {}
    else:
        ai = inputs or {}
        strat = {}
    pre = (strat.get("pre_inject") or {}) if isinstance(strat, dict) else {}
    sp, ns, sid = ai.get("src_params", {}), ai.get("node_stocks", {}), ai.get("sid", "")
    for k in pre.get("from_source_params", []):
        if k not in ai and sp.get(k) is not None: ai[k] = sp.get(k)
    if pre.get("from_source_attrtext") and not ai.get("markets"):
        raw = sp.get("attrtext", [])
        if isinstance(raw, str): raw = [m.strip() for m in raw.replace('\t', ',').split(',') if m.strip()]
        if raw: ai["markets"] = raw
    for tk in pre.get("from_source_stocks_as", []):
        if tk not in ai:
            src = ns.get(sid, [])
            ai[tk] = [_stock_code(s) for s in src] if tk == "stock_list" else [dict(s) for s in src]
    if pre.get("from_source_node") and "node" not in ai: ai["node"] = ai.get("src_node", {})
    if pre.get("set_source_node") and "source_node" not in ai: ai["source_node"] = sid
    # 无 pre_inject 配置时(*:* 通配策略)，执行实际 copy/move 转移
    if not pre and ns:
        tid = ai.get("tid", "")
        src_stocks = ns.get(sid, [])
        if ai.get("is_overwrite"):
            ns[tid] = []
        existing = {_stock_code(x) for x in ns.get(tid, [])}
        for s in src_stocks:
            code = _stock_code(s)
            if code not in existing:
                ns.setdefault(tid, []).append(dict(s) if isinstance(s, dict) else s)
                existing.add(code)
        if ai.get("is_move", False):
            ns[sid] = []
    return ai

def transfer_with_market_data_handler(inputs):
    stocks = inputs.get("stocks", [])
    if not stocks: return {"stocks": []}
    cd = inputs.get("col_def") or inputs.get("default_col_def", [2, -1, -2, -3, 7, 14, 8, 10, 17, 45])
    tq = inputs.get("tq_adapter")
    if tq: return {"stocks": tq.get_stock_table_data(stocks, cd).get('data', [])}
    return {"stocks": [{"code": s, "label": s} for s in stocks]}

def log_transfer_handler(inputs):
    import uuid
    from datetime import datetime as _dt
    flow = inputs.get("flow", {})
    entry = {"log_id": str(uuid.uuid4()), "ts": _dt.now().isoformat(),
             "source_node_id": flow.get('from') or (flow.get('source') or {}).get('node_id', ''),
             "target_node_id": flow.get('to') or (flow.get('target') or {}).get('node_id', ''),
             "stock_code": inputs.get("stock_code", ""), "transfer_mode": inputs.get("transfer_mode", "copy"),
             "trigger_condition": f"flow[{flow.get('id', '')}]", "kline_time": inputs.get("kline_time", "")}
    if inputs.get("storage"):
        try: inputs["storage"].insert_transfer_log(entry)
        except Exception as e: logger.warning("storage.insert_transfer_log 失败 stock_code=%s: %s", inputs.get("stock_code", ""), e)
    if inputs.get("emit_fn"): inputs["emit_fn"]("stock_transfer", entry)
    return {"log_entry": entry}

def condition_dispatch_handler(inputs):
    node, sl = inputs.get("node", {}), inputs.get("stock_list", inputs.get("stocks", []))
    di = inputs.get("dispatch_index", {})
    tq = inputs.get("tq_adapter")
    try:
        conditions = node.get('params', {}).get('conditions', [])
        args = {'pool_config': {}, 'node': node, 'node_stocks': {}, 'tq_adapter': tq,
                'stock_list': sl, 'stocks': sl}
        if conditions:
            cd = _HANDLERS.get("condition_dispatcher")
            result = cd({'stock_list': sl, 'conditions': conditions,
                '_dispatch_index': di, 'tq_adapter': tq,
                'node': node}) if cd else transfer_condition_check(args)
        else:
            result = transfer_condition_check(args)
        passed = result if isinstance(result, list) else result.get('passed', sl)
        final = passed if passed else []
        return {"passed": final}
    except Exception:
        raise

def init_market_source(inputs):
    node, rp = inputs.get("node", {}), inputs.get("resolve_param")
    params = node.get('params', {})
    # 优先使用 params.stocks 中已有的股票
    existing_stocks = params.get('stocks', [])
    if existing_stocks:
        result = []
        for s in existing_stocks:
            if isinstance(s, dict) and s.get('code'):
                result.append(s)
            elif isinstance(s, str) and s:
                result.append({'code': s, 'label': s})
        if result:
            return result
    # 原有逻辑：从 markets/sector_codes/custom_stocks 解析
    # 表驱动默认值：markets/sector_codes/custom_stocks 缺失时回退到通用默认
    markets = (rp(params, 'markets') if rp else None) or ["sh_a", "sz_a"]
    sector_codes = (rp(params, 'sector_codes') if rp else None) or None
    custom_stocks = (rp(params, 'custom_stocks') if rp else None) or None
    sl = candidate_resolve({'pool_config': {}, 'node': node, 'node_stocks': {},
        'tq_adapter': inputs.get("tq_adapter"),
        'markets': markets,
        'sector_codes': sector_codes,
        'custom_stocks': custom_stocks}).get('stock_list', [])
    # 统一代码格式：600000.SH → SH600000
    normalized = []
    for c in sl:
        if isinstance(c, str) and '.' in c:
            parts = c.split('.')
            if len(parts) == 2 and len(parts[1]) == 6 and parts[1].isdigit():
                normalized.append(parts[1] + parts[0])  # 600000.SH → SH600000
            else:
                normalized.append(c)
        else:
            normalized.append(c)
    return [{'code': c, 'label': c} if isinstance(c, str) else c for c in normalized]

def init_stock_state_pool(inputs):
    result = []
    for s in inputs.get("node", {}).get("params", {}).get("stocks", []):
        if isinstance(s, dict):
            code = s.get('code', s.get('label', ''))
            if code:
                # 保留股票的原始字段（如 indate/intime/_tracker），避免状态池入池时间语义丢失
                result.append({"code": code, "label": s.get("label", code), **{k: v for k, v in s.items() if k not in ('code', 'label')}})
        elif isinstance(s, str) and s:
            result.append({"code": s, "label": s})
    return result

def init_tdx_candidate(inputs):
    result = []
    params = inputs.get("node", {}).get("params", {})
    # 优先读内部格式 stocks，回退读 TDX 原生 tdx_stocks（字符串或 {setcode,code} 字典）
    raw = params.get("stocks") or params.get("tdx_stocks") or []
    for s in raw:
        if isinstance(s, dict) and s.get("code"):
            e = {"code": s["code"], "label": s.get("label", s["code"])}
            e.update({k: v for k, v in s.items() if k not in ("code", "label")})
            result.append(e)
        elif isinstance(s, str) and s: result.append({"code": s, "label": s})
    return result

def tdx_convert_from_file(xml_path):
    from ..converters import parse_tdx_xml, tdx_to_internal
    from ..converters import convert_tdx_to_config
    return convert_tdx_to_config(tdx_to_internal(parse_tdx_xml(xml_path)))

def tdx_convert_from_pool(pool_model):
    from ..converters import convert_tdx_to_config
    return convert_tdx_to_config(pool_model)


# ──────────────────────────────────────────────────────────────
# 池角色解析 handler（原 builtins_pool_roles.py）
# ──────────────────────────────────────────────────────────────
# Handler 签名: (rule: dict, context: dict) -> bool
#   - rule: pool_roles.json role_resolution.rules 中的单条规则
#           含 handler / params / role / priority / desc
#   - context: 调用方传入的节点上下文
#           含 psatt (dict): 节点 tdx_psatt / psatt 字典
#              node_type (str): 节点 type 字段
#              role (dict, 可选): 目标角色定义（用于 baimpool_value 查表）

_POOL_ROLES_CACHE: Optional[Dict[str, Any]] = None


def _load_pool_roles() -> Dict[str, Any]:
    """加载 pool_roles.json，缓存以避免重复 IO。"""
    global _POOL_ROLES_CACHE
    if _POOL_ROLES_CACHE is None:
        try:
            with open(os.path.join(_CFG_DIR, "pool_roles.json"), encoding="utf-8") as f:
                _POOL_ROLES_CACHE = json.load(f)
        except Exception:
            _POOL_ROLES_CACHE = {"roles": {}, "role_resolution": {"rules": []}}
    return _POOL_ROLES_CACHE


def _get_psatt_baimpool_value(rule: dict) -> Any:
    """从 rule.params.value 或 rule.params.role 关联的 roles[].baimpool_value 读取判定值。

    优先级:
      1. rule.params.value（直接配置值）
      2. rule.params.role 对应 roles[].baimpool_value（查表）
      3. 回退到 1（与历史默认一致）
    """
    params = rule.get("params", {}) or {}
    if "value" in params:
        return params.get("value")
    role_key = params.get("role") or rule.get("role")
    if role_key:
        roles = _load_pool_roles().get("roles", {}) or {}
        role_def = roles.get(role_key) or {}
        if "baimpool_value" in role_def and role_def["baimpool_value"] is not None:
            return role_def["baimpool_value"]
    return 1


def check_baimpool(rule: dict, context: dict) -> bool:
    """判定节点的 psatt.baimpool 是否匹配规则期望值（查表，禁止硬编码）。

    rule.params.value 优先；缺失时回退到 rule.params.role -> roles[].baimpool_value。
    """
    psatt = context.get("psatt") if isinstance(context, dict) else None
    if not isinstance(psatt, dict):
        return False
    expected = _get_psatt_baimpool_value(rule)
    try:
        actual = int(psatt.get("baimpool", 0))
    except (TypeError, ValueError):
        actual = 0
    try:
        return actual == int(expected)
    except (TypeError, ValueError):
        return False


def check_node_type(rule: dict, context: dict) -> bool:
    """判定节点的 type 字段是否等于 rule.params.type。"""
    nt = context.get("node_type") if isinstance(context, dict) else None
    expected = (rule.get("params", {}) or {}).get("type", "")
    return bool(nt) and nt == expected


def default(rule: dict, context: dict) -> bool:
    """默认回退 handler：永远返回 True。"""
    return True


# ──────────────────────────────────────────────────────────────
# Post-tick 流水线 handler（原 builtins_post_tick.py）
# ──────────────────────────────────────────────────────────────
# 每个函数签名: (cfg, stocks, current_bar_data, now) → result
# stocks = [(stock_dict, code_str, tracker_dict), ...]

_POST_TICK_SAFE = {"max": max, "min": min, "abs": abs, "round": round}

# 表达式编译缓存 — 避免每 tick 重复 compile
_post_tick_cc = {}
def _post_tick_ce(expr_str):
    """compile-and-cache: 相同表达式只编译一次"""
    if expr_str not in _post_tick_cc:
        try: _post_tick_cc[expr_str] = compile(str(expr_str), '<post_tick>', 'eval')
        except SyntaxError: _post_tick_cc[expr_str] = None
    return _post_tick_cc[expr_str]


def _eval_post_tick(expr_str, t, code, current_bar_data):
    """统一 post-tick eval：构建安全上下文并执行编译后的表达式。

    消除 4 个 _ra_* 函数中重复的 `{**t, **_POST_TICK_SAFE, **bar}` 上下文构建
    与 `eval(compiled, ...) if compiled else eval(str(f), ...)` 双分支。
    I63：__builtins__ 从 None 改为 {}——None 上下文下未定义名称经 builtins 回退
    触发 None[name] 抛误导性 TypeError 'NoneType' not subscriptable（掩盖真因：
    字段缺失）；{} 上下文同样禁用全部内建，但未定义名称正确抛 NameError。
    I64：本函数有意使用 eval() 而非 CompiledExpression（ast 受控求值）——post_tick
    公式（analysis_config.json）含三元 if/else 表达式（如 "x if cond else 0"），
    _eval_derived_ast 不支持 ast.IfExp 节点（实测抛 ValueError）。两套 eval 路径
    是有意的设计分工非范式分裂：CompiledExpression 服务 tracker_schema 简单算术
    公式（无三元、None 传播、int→float 归一），_eval_post_tick 服务 post_tick 全
    语法规则（含三元、原始类型保留）。统一会破坏三元公式或要求扩展 tracker eval
    内核（scope creep）。
    """
    compiled = _post_tick_ce(expr_str)
    safe_ctx = {**t, **_POST_TICK_SAFE, **(current_bar_data.get(code, {}) if current_bar_data else {})}
    return eval(compiled, {"__builtins__": {}}, safe_ctx) if compiled else eval(str(expr_str), {"__builtins__": {}}, safe_ctx)


def _ra_score_weighted_sort(cfg, stocks, current_bar_data, now, sc, kw):
    """result_action: 多维度加权评分排序（pk_ranking）。

    差异显示于表内容：cfg_field=dimensions, eval_field=formula, weight_field=weight, rank_change=true。
    """
    push_event = kw.get("push_event")
    prev_rankings = kw.get("prev_rankings", {})
    cfg_field = sc.get("cfg_field", "dimensions")
    eval_field = sc.get("eval_field", "formula")
    weight_field = sc.get("weight_field", "weight")
    scores = []
    for s, code, t in stocks:
        total = 0.0
        for d in cfg.get(cfg_field, {}).values():
            try:
                v = _eval_post_tick(d.get(eval_field, '0'), t, code, current_bar_data)
                total += v * d.get(weight_field, 0)
            except Exception:
                pass
        scores.append({'code': code, 'pk_score': round(total, 4), **t})
    scores.sort(key=lambda x: x['pk_score'], reverse=True)
    if sc.get("rank_change") and push_event:
        prev = {r['code']: i for i, r in enumerate(prev_rankings.get('rankings', []))}
        for i, r in enumerate(scores):
            c = r['code']; p = prev.get(c)
            if p is not None and abs(p - i) >= cfg.get('rank_change_rules', {}).get('thresholds', [{}])[2].get('delta', 5):
                push_event("RANK_CHANGED", c, "", {"old_rank": p, "new_rank": i, "score": r['pk_score']})
    return {'rankings': scores, 'updated': now}


def _ra_score_per_group_sort(cfg, stocks, current_bar_data, now, sc, kw):
    """result_action: 按分组分别评分排序（analysis_angles）。

    差异显示于表内容：cfg_field=angles, eval_field=formula, sort_order_field=sort_order。
    """
    cfg_field = sc.get("cfg_field", "angles")
    eval_field = sc.get("eval_field", "formula")
    sort_order_field = sc.get("sort_order_field", "sort_order")
    results = {}
    for aid, a in cfg.get(cfg_field, {}).items():
        res = []
        for s, code, t in stocks:
            try:
                sc_val = _eval_post_tick(a.get(eval_field, '0'), t, code, current_bar_data)
                res.append({'code': code, 'score': round(sc_val, 4), **t})
            except Exception:
                pass
        res.sort(key=lambda x: x['score'], reverse=(a.get(sort_order_field) == 'desc'))
        results[aid] = res
    return results


def _panel_angle_results(kw):
    """_angle_results 面板数据源：将 angle_results dict 转为 top_code/top_score/count 列表。"""
    angle_results = kw.get("angle_results", {})
    return [{'angle_id': k, 'top_code': v[0]['code'] if v else None,
             'top_score': v[0]['score'] if v else None, 'count': len(v)}
            for k, v in angle_results.items()]


# 面板数据源表：data_source 名 → (kw) → 数据源（差异在表内容非代码分支）
_PANEL_SOURCES = {
    "_pk_rankings": lambda kw: kw.get("pk_rankings", {}),
    "_alert_events": lambda kw: kw.get("alert_events", []),
    "_angle_results": _panel_angle_results,
}


def _ra_aggregate_panels(cfg, stocks, current_bar_data, now, sc, kw):
    """result_action: 汇总面板数据（dashboard）。

    差异显示于表内容：cfg_field=panels，按 data_source 查 _PANEL_SOURCES 表选择数据源。
    """
    cfg_field = sc.get("cfg_field", "panels")
    panels = {}
    for pid, p in cfg.get(cfg_field, {}).items():
        ds = p.get('data_source', '')
        src = _PANEL_SOURCES.get(ds, lambda kw: {})(kw)
        panels[pid] = {'title': p.get('title'), 'updated': now, 'data': src}
    return panels


def _ra_collect_alerts(cfg, stocks, current_bar_data, now, sc, kw):
    """result_action: 收集告警事件（alerts）。

    差异显示于表内容：cfg_field=rules, eval_field=condition, cooldown_field=cooldown_sec。
    """
    alert_events = kw.get("alert_events", [])
    alert_queue = kw.get("alert_queue")
    alert_cooldown = kw.get("alert_cooldown", {})
    cfg_field = sc.get("cfg_field", "rules")
    eval_field = sc.get("eval_field", "condition")
    cooldown_field = sc.get("cooldown_field", "cooldown_sec")
    for rid, r in cfg.get(cfg_field, {}).items():
        cd = r.get(cooldown_field, 0)
        for s, code, t in stocks:
            try:
                cond_result = _eval_post_tick(r.get(eval_field, 'False'), t, code, current_bar_data)
                if cond_result and now - alert_cooldown.get((rid, code), 0) >= cd:
                    al = {'time': now, 'code': code, 'rule_id': rid, 'rule_name': r.get('name'),
                          'severity': r.get('severity'), 'message': f"{r.get('name')}: {code}"}
                    alert_events.append(al)
                    if alert_queue:
                        try: alert_queue.put_nowait(al)
                        except Exception as e: logger.debug("alert_queue.put_nowait 失败 rule_id=%s code=%s: %s", rid, code, e)
                    alert_cooldown[(rid, code)] = now
            except Exception as e:
                logger.warning("alert 规则评估异常 rule_id=%s code=%s: %s", rid, code, e)
    return alert_events


# result_action 函数注册表：result_action 名 → 函数
_RESULT_ACTION_FUNCS = {
    "score_weighted_sort": _ra_score_weighted_sort,
    "score_per_group_sort": _ra_score_per_group_sort,
    "aggregate_panels": _ra_aggregate_panels,
    "collect_alerts": _ra_collect_alerts,
}


def _execute_stage(cfg, stocks, current_bar_data, now, stage_cfg=None, **kw):
    """通用 stage 执行器：按 post_tick_pipeline.json 的 result_action 分派到 result_action 处理器。

    表驱动：多 stage 差异隐含于 cfg_table/eval_field/result_action 字段内容，非独立函数体。
    """
    sc = stage_cfg or {}
    result_action = sc.get("result_action", "")
    ra_fn = _RESULT_ACTION_FUNCS.get(result_action)
    if not ra_fn:
        return {}
    return ra_fn(cfg, stocks, current_bar_data, now, sc, kw)


# ──────────────────────────────────────────────────────────────
# Handler Registry
# ──────────────────────────────────────────────────────────────

_handler_registry = {name: obj for name, obj in globals().items() if callable(obj) and not name.startswith("__")}

_HANDLERS = {
    "candidate_resolve": candidate_resolve, "resolve_market": resolve_market,
    "transfer_condition_check": transfer_condition_check, "condition_dispatcher": condition_dispatcher,
    "accumulate_state": accumulate_state, "stock_pool_hold": stock_pool_hold,
    "discard_stocks": discard_stocks, "discard_sink_drop": discard_sink_drop,
    "formula_eval": formula_eval, "sector_filter": sector_filter,
    "cross_section_eval": cross_section_eval, "basic_filter": basic_filter,
    "pass_through": pass_through, "profit_analysis_calc": profit_analysis_calc,
    "time_trigger_check": time_trigger_check, "render_label": render_label,
    "render_shape": render_shape,
    "_action_resolve_and_pass": _action_resolve_and_pass,
    "_action_apply_filter": _action_apply_filter,
    "_action_dzh_condition_filter": _action_dzh_condition_filter,
    "_action_pass_pool_stocks": _action_pass_pool_stocks,
    "_action_transfer_between_pools": _action_transfer_between_pools,
    "_action_remove_from_pool": _action_remove_from_pool,
    "edge_default_transfer": edge_default_transfer,
    "transfer_with_market_data_handler": transfer_with_market_data_handler,
    "log_transfer_handler": log_transfer_handler,
    "condition_dispatch_handler": condition_dispatch_handler,
    "init_market_source": init_market_source, "init_stock_state_pool": init_stock_state_pool,
    "init_tdx_candidate": init_tdx_candidate,
    "tdx_condition_evaluator": tdx_condition_evaluator,
    "tdx_convert_from_file": tdx_convert_from_file, "tdx_convert_from_pool": tdx_convert_from_pool,
    "_execute_stage": _execute_stage,
    "check_baimpool": check_baimpool, "check_node_type": check_node_type, "default": default,
    "_filter_by_bar_data": lambda inputs: _filter_by_bar_data_handler(inputs),
    "_eval_real": lambda inputs: (list(inputs.get("stock_list", [])), []),
}


def _filter_by_bar_data_handler(inputs):
    """适配 _filter_by_bar_data 到 _resolve_fallback 的调用约定（单 dict 参数）。"""
    stock_list = inputs.get("stock_list", [])
    bar_data = inputs.get("current_bar_data", {}) or {}
    passed, rejected = _filter_by_bar_data(stock_list, bar_data)
    return passed, rejected


# ──────────────────────────────────────────────────────────────
# Refresh 流水线 handler（自 native/pipeline.py 合并）
# ──────────────────────────────────────────────────────────────
# I12 大瘦身：gate/injector/pre_tick 9 个死 handler 已删（零 Python 调用方）。
# 仅保留 Refresh 三 handler（engine._refresh_bar_data 经 getattr(_pipeline, handler_name) 消费）。
#
# I21：tq_snapshot_refresh / mock_advance_refresh 共享的预检 + tq.get_snapshot + 异常吞咽
# 提取为 _apply_tq_snapshot 高阶函数；语义差异（替换 vs 合并）收敛到 merge 参数。
# noop_refresh 因无预检不参与提取，保持单行 return。
#
# 注：原 pipeline.py 的 __all__ 不再单独保留——builtins.py 原本无 __all__，
# 所有公开符号（含下列三个 refresh handler）按默认规则即可导出。

# === Refresh ===

def _apply_tq_snapshot(ctx, current_bar_data, merge: bool, fail_msg: str):
    """共享预检 + tq.get_snapshot 调用 + 异常吞咽。

    Args:
        ctx: refresh 上下文，需含 engine 引用
        current_bar_data: 刷新前的 bar 数据
        merge: True 时合并到 current_bar_data（mock_advance 语义），
               False 时整表替换（tq_snapshot 语义）
        fail_msg: 异常日志模板

    Returns:
        dict: 刷新后的 bar_data（刷新失败或预检不过则返回原 current_bar_data）
    """
    engine = ctx.get('engine')
    if engine is None:
        return current_bar_data
    tq = getattr(engine, 'tq_adapter', None)
    if not tq or not current_bar_data:
        return current_bar_data
    try:
        snapshot = tq.get_snapshot(list(current_bar_data.keys()))
        if snapshot:
            return {**current_bar_data, **snapshot} if merge else snapshot
    except Exception as ex:
        logger.warning(fail_msg, ex)
    return current_bar_data


def tq_snapshot_refresh(cfg, current_bar_data, **ctx):
    """实盘模式行情刷新：调用 tq_adapter.get_snapshot(codes)，整表替换。

    Args:
        cfg: refresh 配置
        current_bar_data: 刷新前的 bar 数据
        **ctx: 上下文，需含 engine 引用

    Returns:
        dict: 刷新后的 bar_data（刷新失败则返回原 current_bar_data）
    """
    return _apply_tq_snapshot(ctx, current_bar_data, merge=False,
                              fail_msg="行情刷新失败: %s")


def noop_refresh(cfg, current_bar_data, **ctx):
    """空操作刷新：直接返回原 current_bar_data。

    回放模式无需刷新行情（数据由 kline_sequence 驱动）。
    """
    return current_bar_data


def mock_advance_refresh(cfg, current_bar_data, **ctx):
    """仿真模式推进刷新：调用 mock_provider 生成新 mock 数据，合并到 current_bar_data。

    Args:
        cfg: refresh 配置
        current_bar_data: 当前 bar 数据
        **ctx: 上下文，需含 engine 引用

    Returns:
        dict: 刷新后的 bar_data（生成失败则返回原 current_bar_data）
    """
    return _apply_tq_snapshot(ctx, current_bar_data, merge=True,
                              fail_msg="mock_advance_refresh 生成 mock 数据失败: %s")

