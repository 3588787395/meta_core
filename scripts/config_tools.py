"""config_tools.py - 配置生成与校验工具（合并自 config/ 2个脚本）。"""

import json
import os
import sys
import argparse
from pathlib import Path

# 合并后文件位于 scripts/，config/ 目录路径为上级的 config 子目录
CONFIG_DIR = str(Path(__file__).parent.parent / "config")
OUTPUT_DIR = CONFIG_DIR


def write_json(filename, data):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    count = len(data) if isinstance(data, list) else len(data) if isinstance(data, dict) else 1
    print(f"  {filename}: {count} entries written")


# =============================================================================
# 1. modules.json — EXACTLY 9 modules
# =============================================================================
MODULES = [
    {
        "id": "market_source",
        "name": "备选池",
        "dzh_cell_type": 202,
        "category": "data_source",
        "handler": "resolve_market",
        "icon": "cylinder",
        "description": "从交易所/板块确定初始股票范围,对应DZH备选池(type=202)",
        "node_type": "source",
        "fields": [
            {"key": "markets", "type": "list[str]", "required": True,
             "desc": "市场代码列表,对应XML attrtext"},
            {"key": "reload_sec", "type": "int", "default": 0,
             "desc": "重载间隔(秒),对应XML reload, 0=不重载"},
            {"key": "name", "type": "str", "required": True,
             "desc": "备选池显示名称"}
        ],
        "inputs": [],
        "outputs": ["stocks"]
    },
    {
        "id": "stock_state_pool",
        "name": "股票状态池",
        "dzh_cell_type": 200,
        "category": "container",
        "handler": "stock_pool_hold",
        "icon": "database",
        "description": "存储经过筛选的股票,支持TTL/显示列/进出动作,对应DZH状态池(type=200)",
        "node_type": "state",
        "fields": [
            {"key": "hold_sec", "type": "int", "default": 432000,
             "desc": "股票保留秒数(TTL),对应XML hold"},
            {"key": "show_columns", "type": "list[str]",
             "default": ["code", "name", "price", "pct_chg", "turnover", "vol_ratio", "amount", "amp", "mkt_cap", "ddx"],
             "desc": "显示列列表,对应XML col"},
            {"key": "show_overview", "type": "bool", "default": True,
             "desc": "是否显示到股票池总览,attr位0x100"},
            {"key": "record_history", "type": "bool", "default": True,
             "desc": "是否记录历史轨迹,attr位0x08"},
            {"key": "enable_profit", "type": "bool", "default": True,
             "desc": "是否启用收益分析,attr位0x80"},
            {"key": "enter_action", "type": "dict", "default": None,
             "desc": "入池动作: {type,qty,alert}"},
            {"key": "leave_action", "type": "dict", "default": None,
             "desc": "离池动作: {type,qty,to_pool}"}
        ],
        "inputs": ["stocks"],
        "outputs": ["stocks"]
    },
    {
        "id": "discard_pool",
        "name": "丢弃池",
        "dzh_cell_type": 203,
        "category": "sink",
        "handler": "discard_sink_drop",
        "icon": "trash",
        "description": "存放不满足条件的股票,对应DZH丢弃池(type=203)",
        "node_type": "sink",
        "fields": [
            {"key": "hold_sec", "type": "int", "default": 432000,
             "desc": "股票保留秒数,对应XML hold"},
            {"key": "show_columns", "type": "list[str]",
             "default": ["code", "name", "price", "pct_chg"],
             "desc": "显示列列表,对应XML col"}
        ],
        "inputs": ["stocks"],
        "outputs": []
    },
    {
        "id": "transfer_filter",
        "name": "转移条件",
        "dzh_cell_type": 201,
        "category": "flow_control",
        "handler": "transfer_condition_check",
        "icon": "filter",
        "description": "控制股票从上级池转移至下级池的条件,对应DZH转移条件(type=201)",
        "node_type": "condition",
        "fields": [
            {"key": "condition_type", "type": "str", "required": True,
             "desc": "条件类型,对应dispatch.json的condition_id"},
            {"key": "params", "type": "dict", "default": {},
             "desc": "条件参数,对应dispatch entry的params"},
            {"key": "analysis_cycle", "type": "str", "default": "tick",
             "desc": "分析周期: tick/1min/5min/15min/30min/60min/day/week/month"},
            {"key": "delete_source", "type": "bool", "default": False,
             "desc": "满足条件后是否删除源池股票,attr位0x8000000"},
            {"key": "clear_dest_first", "type": "bool", "default": False,
             "desc": "转移前是否清空目标池,对应Flow attr位0x1000"},
            {"key": "output_constituent", "type": "bool", "default": False,
             "desc": "源是板块时是否输出成份股,attr位2048"},
            {"key": "extended", "type": "bool", "default": False,
             "desc": "是否扩展态(带时间控制)"},
            {"key": "time_control", "type": "dict", "default": None,
             "desc": "扩展态时间控制配置,引用schedules.json"}
        ],
        "inputs": ["stocks"],
        "outputs": ["passed", "rejected"]
    },
    {
        "id": "bg_shape",
        "name": "图形控件",
        "dzh_cell_type": [2, 3, 4, 5, 6],
        "category": "auxiliary",
        "handler": "render_shape",
        "icon": "shapes",
        "description": "覆盖DZH type 2/3/4/5/6: 矩形/圆角矩形/椭圆/线条/箭头等视觉装饰",
        "node_type": "visual",
        "fields": [
            {"key": "shape_type", "type": "str", "default": "rect",
             "desc": "形状类型: rect/round_rect/ellipse/line/arrow"},
            {"key": "fill_color", "type": "str", "default": "#1a1a2e",
             "desc": "填充色,对应XML clr"},
            {"key": "border_color", "type": "str", "default": "#555555",
             "desc": "边框颜色"},
            {"key": "width", "type": "int", "default": 100,
             "desc": "宽度"},
            {"key": "height", "type": "int", "default": 40,
             "desc": "高度"}
        ],
        "inputs": [],
        "outputs": []
    },
    {
        "id": "flow_arrow",
        "name": "流程箭头",
        "dzh_cell_type": 6,
        "category": "flow_control",
        "handler": "render_shape",
        "icon": "arrow_right",
        "description": "可视化连接线, 定义池间流转方向",
        "node_type": "visual",
        "fields": [
            {"key": "from_node", "type": "str", "required": True, "desc": "起点 node_id"},
            {"key": "to_node", "type": "str", "required": True, "desc": "终点 node_id"},
            {"key": "line_width", "type": "int", "default": 2, "desc": "线宽"},
            {"key": "arrow_style", "type": "str", "default": "solid", "desc": "箭头样式: solid/dashed"}
        ],
        "inputs": [],
        "outputs": []
    },
    {
        "id": "text_label",
        "name": "文字标签",
        "dzh_cell_type": 1,
        "category": "auxiliary",
        "handler": "render_label",
        "icon": "type",
        "description": "标题/说明/时间标签等纯展示文字,对应DZH文字控件(type=1)",
        "node_type": "visual",
        "fields": [
            {"key": "text", "type": "str", "required": True,
             "desc": "文字内容,对应XML text"},
            {"key": "font_style", "type": "int", "default": 0,
             "desc": "字体样式attr位掩码: 1=透明/2=粗体/4=斜体/8=下划线/16=左对齐/32=居中/64=右对齐/128=竖排"},
            {"key": "font_color", "type": "str", "default": "#FFFFFF",
             "desc": "文字颜色,对应XML clr"}
        ],
        "inputs": [],
        "outputs": []
    },
    {
        "id": "time_trigger",
        "name": "时序控制",
        "dzh_cell_type": None,
        "category": "schedule",
        "handler": "time_trigger_check",
        "icon": "clock",
        "description": "定时触发条件,对应DZH Flow的begin/end时序控制",
        "node_type": None,
        "fields": [
            {"key": "schedule_id", "type": "str", "required": True,
             "desc": "引用schedules.json的schedule id"},
            {"key": "start_mode", "type": "str", "required": True,
             "desc": "启动模式: immediate/delay/before_open/after_open/at_time/before_close/after_close/specified_day"},
            {"key": "start_value", "type": "int", "default": 0,
             "desc": "启动参数(秒或HHMMSS)"},
            {"key": "duration_mode", "type": "str", "default": "continuous",
             "desc": "持续模式: continuous/until/once"},
            {"key": "duration_sec", "type": "int", "default": 0,
             "desc": "持续秒数,对应XML endt"},
            {"key": "interval_sec", "type": "int", "default": 5,
             "desc": "执行间隔(秒),对应XML interval"}
        ],
        "inputs": [],
        "outputs": ["trigger"]
    },
    {
        "id": "profit_analysis",
        "name": "收益分析",
        "dzh_cell_type": None,
        "category": "analysis",
        "handler": "profit_analysis_calc",
        "icon": "trending_up",
        "description": "五种收益分析: 日内/市场冲击/历史收益/历史分布/定位分析,对应DZH状态池的收益分析功能",
        "node_type": None,
        "fields": [
            {"key": "analysis_types", "type": "list[str]",
             "default": ["intraday", "market_impact", "history", "distribution", "positioning"],
             "desc": "启用的分析类型"},
            {"key": "target_pool", "type": "str", "required": True,
             "desc": "被分析的池node_id"}
        ],
        "inputs": ["stocks"],
        "outputs": ["report"]
    }
]


# =============================================================================
# 2. dispatch.json — flat array, 30+ conditions
# =============================================================================
DISPATCH = [
    # --- market_data engine (行情数据) ---
    {
        "condition_id": "pct_change_range",
        "name": "涨跌幅范围",
        "desc": "当日涨跌幅在[min,max]区间内",
        "engine": "market_data",
        "params": {"field": "pct_chg", "min": -3.0, "max": 9.0},
        "operator": "between"
    },
    {
        "condition_id": "turnover_min",
        "name": "最小换手率",
        "desc": "换手率大于N%",
        "engine": "market_data",
        "params": {"field": "turnover", "min": 1.0},
        "operator": "gt"
    },
    {
        "condition_id": "amount_min",
        "name": "最小成交额",
        "desc": "成交额大于N万",
        "engine": "market_data",
        "params": {"field": "amount", "min": 10000000},
        "operator": "gt"
    },
    {
        "condition_id": "vol_ratio_high",
        "name": "量比放大",
        "desc": "量比大于N",
        "engine": "market_data",
        "params": {"field": "vol_ratio", "min": 1.5},
        "operator": "gt"
    },
    {
        "condition_id": "amp_range",
        "name": "振幅范围",
        "desc": "振幅在[min,max]区间",
        "engine": "market_data",
        "params": {"field": "amp", "min": 2.0, "max": 8.0},
        "operator": "between"
    },
    {
        "condition_id": "exclude_high_price",
        "name": "排除高价股",
        "desc": "排除价格高于N的股票",
        "engine": "market_data",
        "params": {"field": "price", "max": 100},
        "operator": "lt"
    },
    {
        "condition_id": "price_range",
        "name": "价格区间",
        "desc": "股价在[min,max]区间内",
        "engine": "market_data",
        "params": {"field": "price", "min": 5.0, "max": 50.0},
        "operator": "between"
    },
    {
        "condition_id": "volume_break_ma",
        "name": "量能突破均量",
        "desc": "当日成交量大于N日均量",
        "engine": "market_data",
        "params": {"field": "volume_ma_ratio", "min": 1.5, "period": 5},
        "operator": "gt"
    },

    # --- ta_engine (技术指标) ---
    {
        "condition_id": "price_above_ma",
        "name": "价格上穿均线",
        "desc": "收盘价上穿N日均线",
        "engine": "ta_engine",
        "params": {"indicator": "ma", "period": 30},
        "operator": "cross_above"
    },
    {
        "condition_id": "price_below_ma",
        "name": "价格下穿均线",
        "desc": "收盘价下穿N日均线",
        "engine": "ta_engine",
        "params": {"indicator": "ma", "period": 30},
        "operator": "cross_below"
    },
    {
        "condition_id": "macd_golden_cross",
        "name": "MACD金叉",
        "desc": "MACD DIF上穿DEA",
        "engine": "ta_engine",
        "params": {"indicator": "macd"},
        "operator": "cross_above"
    },
    {
        "condition_id": "macd_dead_cross",
        "name": "MACD死叉",
        "desc": "MACD DIF下穿DEA",
        "engine": "ta_engine",
        "params": {"indicator": "macd"},
        "operator": "cross_below"
    },
    {
        "condition_id": "kdj_j_low",
        "name": "KDJ J值低",
        "desc": "KDJ J值小于N(默认90)",
        "engine": "ta_engine",
        "params": {"indicator": "kdj", "field": "J", "threshold": 90},
        "operator": "lt"
    },
    {
        "condition_id": "kdj_k_low",
        "name": "KDJ K值低位金叉",
        "desc": "KDJ K值小于N且K上穿D",
        "engine": "ta_engine",
        "params": {"indicator": "kdj", "field": "K", "threshold": 30},
        "operator": "cross_above"
    },
    {
        "condition_id": "rsi_oversold",
        "name": "RSI超卖",
        "desc": "RSI小于N(默认30,超卖区域)",
        "engine": "ta_engine",
        "params": {"indicator": "rsi", "period": 14, "threshold": 30},
        "operator": "lt"
    },
    {
        "condition_id": "ma_bullish_align",
        "name": "均线多头排列",
        "desc": "短/中/长均线多头排列",
        "engine": "ta_engine",
        "params": {"indicator": "ma_align", "short": 5, "mid": 20, "long": 60},
        "operator": "eq"
    },
    {
        "condition_id": "boll_break_upper",
        "name": "突破布林上轨",
        "desc": "收盘价突破布林带上轨",
        "engine": "ta_engine",
        "params": {"indicator": "boll", "period": 20, "std": 2},
        "operator": "cross_above"
    },

    # --- fundamental engine (基本面) ---
    {
        "condition_id": "pe_range",
        "name": "市盈率区间",
        "desc": "市盈率在[min,max]区间(低估值)",
        "engine": "fundamental",
        "params": {"field": "pe", "min": 0, "max": 22},
        "operator": "between"
    },
    {
        "condition_id": "pb_range",
        "name": "市净率区间",
        "desc": "市净率在[min,max]区间",
        "engine": "fundamental",
        "params": {"field": "pb", "min": 0, "max": 5},
        "operator": "between"
    },
    {
        "condition_id": "revenue_growth",
        "name": "营收增长率",
        "desc": "最新财报营收增长率>N%,连续增长",
        "engine": "fundamental",
        "params": {"field": "revenue_yoy", "min_pct": 20, "consecutive_years": 2},
        "operator": "gt"
    },
    {
        "condition_id": "exclude_st",
        "name": "排除ST",
        "desc": "排除ST股票",
        "engine": "fundamental",
        "params": {"field": "is_st", "value": False},
        "operator": "eq"
    },
    {
        "condition_id": "exclude_new_stock",
        "name": "排除新股",
        "desc": "排除上市不足N天的股票",
        "engine": "fundamental",
        "params": {"field": "listed_days", "min": 60},
        "operator": "gt"
    },
    {
        "condition_id": "market_cap_range",
        "name": "市值区间",
        "desc": "总市值在[min,max]亿区间",
        "engine": "fundamental",
        "params": {"field": "total_mv", "min": 10, "max": 500},
        "operator": "between"
    },
    {
        "condition_id": "in_sector",
        "name": "属于指定板块",
        "desc": "股票属于指定板块(行业/概念/地域)",
        "engine": "fundamental",
        "params": {"field": "sector", "sector_names": ["医药", "地产", "券商"]},
        "operator": "in"
    },

    # --- capital_flow engine (资金流) ---
    {
        "condition_id": "capital_flow_in",
        "name": "资金净流入",
        "desc": "大单资金净流入大于阈值",
        "engine": "capital_flow",
        "params": {"order_type": "big", "min_amount": 10000000},
        "operator": "gt"
    },
    {
        "condition_id": "big_order_ratio",
        "name": "大单买入占比",
        "desc": "特大单买入占流通盘比例大于N%",
        "engine": "capital_flow",
        "params": {"field": "big_buy_ratio", "min": 1.0},
        "operator": "gt"
    },
    {
        "condition_id": "ddx_positive",
        "name": "DDX为正",
        "desc": "当日DDX大于0",
        "engine": "capital_flow",
        "params": {"indicator": "ddx", "min": 0},
        "operator": "gt"
    },
    {
        "condition_id": "ddx_positive_days",
        "name": "DDX连续飘红",
        "desc": "近N日内DDX飘红天数>=M,且当日DDX>阈值",
        "engine": "capital_flow",
        "params": {"indicator": "ddx", "lookback": 10, "min_positive": 7, "today_min": 0.2},
        "operator": "count_ge"
    },
    {
        "condition_id": "in_hot_sector",
        "name": "属于当日热点板块",
        "desc": "属于当日资金流入最多的N个板块",
        "engine": "capital_flow",
        "params": {"top_n": 3, "order_by": "big_buy_ratio"},
        "operator": "in"
    },

    # --- special_pattern engine (特殊形态) ---
    {
        "condition_id": "gap_up",
        "name": "跳空高开",
        "desc": "开盘价高于昨日收盘价N%以上",
        "engine": "special_pattern",
        "params": {"field": "open", "min_gap_pct": 2.0},
        "operator": "gt"
    },
    {
        "condition_id": "limit_up",
        "name": "涨停板",
        "desc": "当日触及涨停板",
        "engine": "special_pattern",
        "params": {},
        "operator": "eq"
    },
    {
        "condition_id": "new_high_n",
        "name": "N日新高",
        "desc": "创N日内新高",
        "engine": "special_pattern",
        "params": {"period": 20},
        "operator": "eq"
    },

    # --- pool_specific engine (池内专用) ---
    {
        "condition_id": "profit_take",
        "name": "止盈条件",
        "desc": "入池后涨幅超过N%触发卖出",
        "engine": "pool_specific",
        "params": {"ref_price": "enter_price", "pct_threshold": 5.0},
        "operator": "gt"
    },
    {
        "condition_id": "stop_loss",
        "name": "止损条件",
        "desc": "入池后跌幅超过N%触发卖出",
        "engine": "pool_specific",
        "params": {"ref_price": "enter_price", "pct_threshold": -5.0},
        "operator": "lt"
    },
    {
        "condition_id": "enter_bars_gt",
        "name": "入池周期数",
        "desc": "入池后距今周期数>N",
        "engine": "pool_specific",
        "params": {"field": "enter_bars", "min": 5},
        "operator": "gt"
    },
    {
        "condition_id": "hold_expire",
        "name": "持仓到期",
        "desc": "股票在池中持有时间超过TTL",
        "engine": "pool_specific",
        "params": {"field": "hold_remain", "max": 0},
        "operator": "lt"
    }
]


# =============================================================================
# 3. pipelines.json — 3 patterns matching DZH XML topologies
# =============================================================================
PIPELINES = [
    {
        "id": "serial_branch_merge",
        "name": "串行分支汇合",
        "based_on": "超赢1号",
        "description": "多分支串行筛选后汇合到最终池。对应超赢1号XML: 沪深AB股→流动性筛选→三路分支(短线/长线/异动)→汇总→超赢1号池",
        "stages": [
            {
                "stage": "market_init",
                "step_id": 1,
                "module": "market_source",
                "input_bind": {},
                "output_bind": {"stocks": "ctx.raw_universe"},
                "desc": "步骤1: 定义备选范围(可多个并列)"
            },
            {
                "stage": "pre_filter",
                "step_id": 2,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.raw_universe"},
                "output_bind": {"passed": "ctx.pre_filtered"},
                "desc": "步骤2: 流动性初筛(如换手率TOP1000)"
            },
            {
                "stage": "pre_filter",
                "step_id": 3,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.pre_filtered"},
                "output_bind": {"stocks": "ctx.risk_control_pool"},
                "desc": "步骤3: 风险控制池暂存"
            },
            {
                "stage": "parallel_branches",
                "step_id": 4,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.risk_control_pool"},
                "output_bind": {"passed": "ctx.branch_a"},
                "desc": "步骤4a: 分支A—短线资金条件"
            },
            {
                "stage": "parallel_branches",
                "step_id": 5,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.risk_control_pool"},
                "output_bind": {"passed": "ctx.branch_b"},
                "desc": "步骤4b: 分支B—长线资金条件"
            },
            {
                "stage": "parallel_branches",
                "step_id": 6,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.risk_control_pool"},
                "output_bind": {"passed": "ctx.branch_c"},
                "desc": "步骤4c: 分支C—异动扫描条件"
            },
            {
                "stage": "merge",
                "step_id": 7,
                "module": "transfer_filter",
                "input_bind": {"stocks": ["ctx.branch_a", "ctx.branch_b", "ctx.branch_c"]},
                "output_bind": {"passed": "ctx.final_pool"},
                "desc": "步骤5: 三路汇合条件→最终池"
            },
            {
                "stage": "merge",
                "step_id": 8,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.final_pool"},
                "output_bind": {"stocks": "ctx.output_pool"},
                "desc": "步骤6: 最终输出池(超赢1号池)"
            },
            {
                "stage": "post_classify",
                "step_id": 9,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.output_pool"},
                "output_bind": {"passed": "ctx.hero_pool"},
                "desc": "步骤7a: 收益>5%→英雄榜"
            },
            {
                "stage": "post_classify",
                "step_id": 10,
                "module": "discard_pool",
                "input_bind": {"stocks": "ctx.final_pool"},
                "output_bind": {},
                "desc": "步骤7b: 弱化→观察池(丢弃池暂存)"
            }
        ],
        "config": {
            "max_branches": 5,
            "merge_strategy": "union",
            "post_classify": {"hero_threshold_pct": 5.0}
        }
    },
    {
        "id": "parallel_state_accumulation",
        "name": "并行状态累积合并",
        "based_on": "超赢7号",
        "description": "多个维度并行监控后汇合到最终池,含循环反馈。对应超赢7号XML: 沪深A股→初步筛选→超赢备选→价值A/B→强势/买点→追踪/整理→超赢7号→观察池↩循环",
        "stages": [
            {
                "stage": "market_init",
                "step_id": 1,
                "module": "market_source",
                "input_bind": {},
                "output_bind": {"stocks": "ctx.raw_universe"},
                "desc": "步骤1: 定义备选范围"
            },
            {
                "stage": "pre_filter",
                "step_id": 2,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.raw_universe"},
                "output_bind": {"passed": "ctx.pre_filtered"},
                "desc": "步骤2: 初步筛选(去ST/停牌等)"
            },
            {
                "stage": "pre_filter",
                "step_id": 3,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.pre_filtered"},
                "output_bind": {"stocks": "ctx.candidate_pool"},
                "desc": "步骤3: 超赢备选池"
            },
            {
                "stage": "parallel_states",
                "step_id": 4,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.candidate_pool"},
                "output_bind": {"passed": "ctx.value_a"},
                "desc": "步骤4a: 超赢价值A条件"
            },
            {
                "stage": "parallel_states",
                "step_id": 5,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.value_a"},
                "output_bind": {"passed": "ctx.momentum"},
                "desc": "步骤4b: 超赢强势条件"
            },
            {
                "stage": "parallel_states",
                "step_id": 6,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.candidate_pool"},
                "output_bind": {"passed": "ctx.value_b"},
                "desc": "步骤4c: 超赢价值B条件"
            },
            {
                "stage": "parallel_states",
                "step_id": 7,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.value_b"},
                "output_bind": {"passed": "ctx.buy_point"},
                "desc": "步骤4d: 超赢买点条件"
            },
            {
                "stage": "cross_analysis",
                "step_id": 8,
                "module": "transfer_filter",
                "input_bind": {"stocks": ["ctx.momentum", "ctx.buy_point"]},
                "output_bind": {"passed": "ctx.tracking"},
                "desc": "步骤5a: 汇合到超赢追踪"
            },
            {
                "stage": "cross_analysis",
                "step_id": 9,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.tracking"},
                "output_bind": {"stocks": "ctx.tracking_pool"},
                "desc": "步骤5b: 超赢追踪池"
            },
            {
                "stage": "final_decision",
                "step_id": 10,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.tracking_pool"},
                "output_bind": {"passed": "ctx.final_pool"},
                "desc": "步骤6a: 最终筛选条件"
            },
            {
                "stage": "final_decision",
                "step_id": 11,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.final_pool"},
                "output_bind": {"stocks": "ctx.output_pool"},
                "desc": "步骤6b: 超赢7号最终池"
            },
            {
                "stage": "post_classify",
                "step_id": 12,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.output_pool"},
                "output_bind": {"passed": "ctx.hero_pool"},
                "desc": "步骤7a: 收益>5%→英雄榜"
            },
            {
                "stage": "post_classify",
                "step_id": 13,
                "module": "discard_pool",
                "input_bind": {"stocks": "ctx.output_pool"},
                "output_bind": {},
                "desc": "步骤7b: 下跌>4%→观察池"
            }
        ],
        "config": {
            "parallel_dimensions": 4,
            "accumulation_mode": "intersection",
            "min_dimensions_required": 2,
            "post_classify": {"hero_threshold_pct": 5.0, "observe_threshold_pct": -4.0},
            "feedback_loop": True
        }
    },
    {
        "id": "time_phased_chain",
        "name": "时序分阶段执行",
        "based_on": "金色两点半",
        "description": "按固定时间点分阶段推进,每段时间触发下一轮筛选,能量逐渐增强,收盘前输出最佳结果。对应金色两点半XML: 全天→10:00→11:00→13:50→14:00→14:20→14:30",
        "stages": [
            {
                "stage": "market_init",
                "step_id": 1,
                "module": "market_source",
                "input_bind": {},
                "output_bind": {"stocks": "ctx.raw_universe"},
                "desc": "步骤1: 定义备选范围(沪深A股+中小+创业板)"
            },
            {
                "stage": "pre_filter",
                "step_id": 2,
                "module": "time_trigger",
                "input_bind": {},
                "output_bind": {"trigger": "ctx.schedule_tick"},
                "desc": "步骤2: 全天时序调度(间隔20秒)"
            },
            {
                "stage": "pre_filter",
                "step_id": 3,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.raw_universe"},
                "output_bind": {"passed": "ctx.initial_screen"},
                "desc": "步骤3: 初步筛选条件"
            },
            {
                "stage": "pre_filter",
                "step_id": 4,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.initial_screen"},
                "output_bind": {"stocks": "ctx.screen_pool"},
                "desc": "步骤4: 初步筛选池"
            },
            {
                "stage": "time_phases",
                "step_id": 5,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.screen_pool"},
                "output_bind": {"passed": "ctx.baseline"},
                "desc": "步骤5: 10:00→基准池条件"
            },
            {
                "stage": "time_phases",
                "step_id": 6,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.baseline"},
                "output_bind": {"stocks": "ctx.baseline_pool"},
                "desc": "步骤6: 基准池(10:00起)"
            },
            {
                "stage": "time_phases",
                "step_id": 7,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.baseline_pool"},
                "output_bind": {"passed": "ctx.first_check"},
                "desc": "步骤7: 11:00→一次考量条件"
            },
            {
                "stage": "time_phases",
                "step_id": 8,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.first_check"},
                "output_bind": {"stocks": "ctx.first_pool"},
                "desc": "步骤8: 一次考量池(11:00起)"
            },
            {
                "stage": "time_phases",
                "step_id": 9,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.first_pool"},
                "output_bind": {"passed": "ctx.second_check"},
                "desc": "步骤9: 13:50→二次考量条件"
            },
            {
                "stage": "time_phases",
                "step_id": 10,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.second_check"},
                "output_bind": {"stocks": "ctx.second_pool"},
                "desc": "步骤10: 二次考量池(13:50起)"
            },
            {
                "stage": "final_output",
                "step_id": 11,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.second_pool"},
                "output_bind": {"passed": "ctx.ready_pool"},
                "desc": "步骤11: 14:00→预备池条件(5秒超高频)"
            },
            {
                "stage": "final_output",
                "step_id": 12,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.ready_pool"},
                "output_bind": {"stocks": "ctx.ready_pool_state"},
                "desc": "步骤12: 预备池"
            },
            {
                "stage": "final_output",
                "step_id": 13,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.ready_pool_state"},
                "output_bind": {"passed": "ctx.final_pool"},
                "desc": "步骤13: 14:20→最终筛选条件(5秒超高频)"
            },
            {
                "stage": "final_output",
                "step_id": 14,
                "module": "stock_state_pool",
                "input_bind": {"stocks": "ctx.final_pool"},
                "output_bind": {"stocks": "ctx.output_pool"},
                "desc": "步骤14: 14:30→金色两点半最终池"
            },
            {
                "stage": "post_classify",
                "step_id": 15,
                "module": "transfer_filter",
                "input_bind": {"stocks": "ctx.output_pool"},
                "output_bind": {"passed": "ctx.hero_pool"},
                "desc": "步骤15a: 收益>5%→英雄榜"
            },
            {
                "stage": "post_classify",
                "step_id": 16,
                "module": "discard_pool",
                "input_bind": {"stocks": "ctx.output_pool"},
                "output_bind": {},
                "desc": "步骤15b: 弱化→观察池"
            }
        ],
        "config": {
            "time_slots": {
                "full_day": {"interval_sec": 20, "desc": "初步筛选"},
                "10:00": {"interval_sec": 20, "desc": "基准池"},
                "11:00": {"interval_sec": 20, "desc": "一次考量"},
                "13:50": {"interval_sec": 5, "desc": "二次考量"},
                "14:00": {"interval_sec": 5, "desc": "预备池"},
                "14:20": {"interval_sec": 5, "desc": "最终筛选"},
                "14:30": {"interval_sec": 0, "desc": "金色两点半输出"}
            },
            "post_classify": {"hero_threshold_pct": 5.0, "observe_threshold_pct": -4.0}
        }
    }
]


# =============================================================================
# 4. engines.json — engine IDs match dispatch.json "engine" field
# =============================================================================
ENGINES = [
    {
        "id": "tick_engine",
        "name": "分笔引擎",
        "cycle": "tick",
        "cycle_sec": 0,
        "description": "每笔成交即触发计算",
        "source": "level2_tick",
        "gateway": "tq_tick"
    },
    {
        "id": "min1_engine",
        "name": "1分钟引擎",
        "cycle": "1min",
        "cycle_sec": 60,
        "description": "每分钟触发一次计算",
        "source": "minute_kline",
        "gateway": "tq_kline"
    },
    {
        "id": "min5_engine",
        "name": "5分钟引擎",
        "cycle": "5min",
        "cycle_sec": 300,
        "description": "每5分钟触发一次计算",
        "source": "minute_kline",
        "gateway": "tq_kline"
    },
    {
        "id": "min15_engine",
        "name": "15分钟引擎",
        "cycle": "15min",
        "cycle_sec": 900,
        "description": "每15分钟触发一次计算",
        "source": "minute_kline",
        "gateway": "tq_kline"
    },
    {
        "id": "min30_engine",
        "name": "30分钟引擎",
        "cycle": "30min",
        "cycle_sec": 1800,
        "description": "每30分钟触发一次计算",
        "source": "minute_kline",
        "gateway": "tq_kline"
    },
    {
        "id": "min60_engine",
        "name": "60分钟引擎",
        "cycle": "60min",
        "cycle_sec": 3600,
        "description": "每小时触发一次计算",
        "source": "minute_kline",
        "gateway": "tq_kline"
    },
    {
        "id": "daily_engine",
        "name": "日线引擎",
        "cycle": "day",
        "cycle_sec": 86400,
        "description": "日线级别计算",
        "source": "daily_kline",
        "gateway": "tq_kline"
    },
    {
        "id": "weekly_engine",
        "name": "周线引擎",
        "cycle": "week",
        "cycle_sec": 604800,
        "description": "周线级别计算",
        "source": "weekly_kline",
        "gateway": "tq_kline"
    },
    {
        "id": "monthly_engine",
        "name": "月线引擎",
        "cycle": "month",
        "cycle_sec": 2592000,
        "description": "月线级别计算",
        "source": "monthly_kline",
        "gateway": "tq_kline"
    },
    {
        "id": "ta_engine",
        "name": "技术指标引擎",
        "cycle": "any",
        "description": "MA/MACD/KDJ/RSI/BOLL等指标计算",
        "source": "kline_any",
        "gateway": "tq_indicator",
        "fn": "compare_field"
    },
    {
        "id": "market_data",
        "name": "行情数据引擎",
        "cycle": "tick",
        "description": "实时行情数据(价/量/额/换手/振幅/量比)",
        "source": "real_time_quote",
        "gateway": "tq_quote",
        "fn": "compare_field"
    },
    {
        "id": "fundamental",
        "name": "基本面引擎",
        "cycle": "day",
        "description": "财报/估值/股本/上市天数/ST状态/板块归属",
        "source": "fundamental_db",
        "gateway": "tq_fundamental",
        "fn": "compare_field"
    },
    {
        "id": "capital_flow",
        "name": "资金流引擎",
        "cycle": "tick",
        "description": "大单/特大单买卖/DDX/DDY/板块资金流向",
        "source": "level2_order",
        "gateway": "tq_capital_flow",
        "fn": "compare_field"
    },
    {
        "id": "special_pattern",
        "name": "特殊形态引擎",
        "cycle": "tick",
        "description": "跳空/涨停/新高/新低等特殊形态检测",
        "source": "real_time_quote",
        "gateway": "tq_pattern",
        "fn": "pattern_detect"
    },
    {
        "id": "pool_specific",
        "name": "池内专用引擎",
        "cycle": "tick",
        "description": "ENTERPOOLPRICE/ENTERPOOLBARS/HOLD_EXPIRE等池内专用函数",
        "source": "pool_state",
        "gateway": "tq_pool",
        "fn": "compare_field"
    }
]


# =============================================================================
# 5. markets.json — covers all attrtext market codes from DZH XML
# =============================================================================
MARKETS = [
    {
        "id": "SH_A",
        "name": "上证A股",
        "exchange": "SH",
        "category": "A",
        "code_prefix": "SH",
        "attrtext_code": "SH#上证A股",
        "description": "上海证券交易所A股(60xxxx)"
    },
    {
        "id": "SH_B",
        "name": "上证B股",
        "exchange": "SH",
        "category": "B",
        "code_prefix": "SH",
        "attrtext_code": "SH#上证B股",
        "description": "上海证券交易所B股(90xxxx)"
    },
    {
        "id": "SZ_A",
        "name": "深证A股",
        "exchange": "SZ",
        "category": "A",
        "code_prefix": "SZ",
        "attrtext_code": "SZ#深证A股",
        "description": "深圳证券交易所A股(00xxxx/001xxx/002xxx)"
    },
    {
        "id": "SZ_B",
        "name": "深证B股",
        "exchange": "SZ",
        "category": "B",
        "code_prefix": "SZ",
        "attrtext_code": "SZ#深证B股",
        "description": "深圳证券交易所B股(20xxxx)"
    },
    {
        "id": "SZ_SME",
        "name": "中小企业板",
        "exchange": "SZ",
        "category": "SME",
        "code_prefix": "SZ",
        "attrtext_code": "SZ#中小企业",
        "description": "深圳中小企业板(002xxx/003xxx)"
    },
    {
        "id": "SZ_GEM",
        "name": "创业板",
        "exchange": "SZ",
        "category": "GEM",
        "code_prefix": "SZ",
        "attrtext_code": "SZ#创业板",
        "description": "深圳创业板(30xxxx)"
    },
    {
        "id": "SH_STAR",
        "name": "科创板",
        "exchange": "SH",
        "category": "STAR",
        "code_prefix": "SH",
        "attrtext_code": "SH#科创板",
        "description": "上海科创板(688xxx)"
    },
    {
        "id": "BJ_BSE",
        "name": "北京所",
        "exchange": "BJ",
        "category": "BSE",
        "code_prefix": "BJ",
        "attrtext_code": "BJ#北交所",
        "description": "北京证券交易所(8xxxxx)"
    },
    {
        "id": "SECTOR_INDEX",
        "name": "板块指数",
        "exchange": "B$",
        "category": "SECTOR",
        "code_prefix": "B$",
        "attrtext_code": "B$#板块指数",
        "description": "行业板块/概念板块指数(超赢1号行业路线使用)"
    },
    {
        "id": "HK_MAIN",
        "name": "港股主板",
        "exchange": "HK",
        "category": "HK",
        "code_prefix": "HK",
        "attrtext_code": "HK#港股",
        "description": "香港交易所主板(来自DZH属性文档)"
    },
    {
        "id": "SH_SZ_AB",
        "name": "沪深AB股",
        "exchange": "MIXED",
        "category": "COMPOSITE",
        "members": ["SH_A", "SH_B", "SZ_A", "SZ_B"],
        "attrtext_code": "SH#上证A股 SH#上证B股 SZ#深证A股 SZ#深证B股",
        "description": "沪深两市AB股全集(超赢1号使用)"
    },
    {
        "id": "SH_SZ_A_SME",
        "name": "沪深A股+中小板",
        "exchange": "MIXED",
        "category": "COMPOSITE",
        "members": ["SH_A", "SZ_A", "SZ_SME"],
        "attrtext_code": "SH#上证A股 SZ#深证A股 SZ#中小企业",
        "description": "沪深A股+中小企业(超赢7号使用)"
    },
    {
        "id": "ALL_A_GEM",
        "name": "全部A股",
        "exchange": "MIXED",
        "category": "COMPOSITE",
        "members": ["SH_A", "SZ_A", "SZ_SME", "SZ_GEM"],
        "attrtext_code": "SH#上证A股 SZ#深证A股 SZ#中小企业 SZ#创业板",
        "description": "沪深A股+中小企业+创业板全集(金色两点半使用)"
    }
]


# =============================================================================
# 6. schedules.json — covers all Flow begin types (0-7) + golden_two_half
# =============================================================================
SCHEDULES = [
    {
        "id": "begin_0_immediate",
        "name": "立即开始-永久执行",
        "begin_type": 0,
        "begin_param": 0,
        "end_type": 0,
        "end_param": 2147483647,
        "interval_sec": 60,
        "dzh_desc": "begin=0 begint=0 end=0 endt=INT32_MAX,即时开始持续执行(超赢1号默认)",
        "start_mode": "immediate",
        "duration_mode": "continuous_is_forever"
    },
    {
        "id": "begin_0_once",
        "name": "立即开始-执行一次",
        "begin_type": 0,
        "begin_param": 0,
        "end_type": 0,
        "end_param": 1,
        "interval_sec": 0,
        "dzh_desc": "begin=0 begint=0 end=0 endt=1,执行一次后结束",
        "start_mode": "immediate",
        "duration_mode": "once"
    },
    {
        "id": "begin_1_delay",
        "name": "延迟开始",
        "begin_type": 1,
        "begin_param": 0,
        "end_type": 0,
        "end_param": 19800,
        "interval_sec": 60,
        "dzh_desc": "begin=1 begint=N(秒),延迟N秒后开始,持续endt秒(超赢7号默认)",
        "start_mode": "delay",
        "duration_mode": "until"
    },
    {
        "id": "begin_2_before_open",
        "name": "开市前开始",
        "begin_type": 2,
        "begin_param": 0,
        "end_type": 0,
        "end_param": 2147483647,
        "interval_sec": 60,
        "dzh_desc": "begin=2 开市前开始执行",
        "start_mode": "before_open",
        "duration_mode": "continuous"
    },
    {
        "id": "begin_3_after_open",
        "name": "开市后开始",
        "begin_type": 3,
        "begin_param": 0,
        "end_type": 0,
        "end_param": 19800,
        "interval_sec": 60,
        "dzh_desc": "begin=3 开市后开始,持续至收市",
        "start_mode": "after_open",
        "duration_mode": "until"
    },
    {
        "id": "begin_4_at_time",
        "name": "指定时间开始",
        "begin_type": 4,
        "begin_param": 100000,
        "end_type": 0,
        "end_param": 18000,
        "interval_sec": 20,
        "dzh_desc": "begin=4 begint=HHMMSS,指定时刻开始(金色两点半核心机制)",
        "start_mode": "at_time",
        "duration_mode": "until"
    },
    {
        "id": "begin_5_before_close",
        "name": "收市前开始",
        "begin_type": 5,
        "begin_param": 0,
        "end_type": 0,
        "end_param": 2147483647,
        "interval_sec": 60,
        "dzh_desc": "begin=5 收市前开始执行",
        "start_mode": "before_close",
        "duration_mode": "continuous"
    },
    {
        "id": "begin_6_after_close",
        "name": "收市后开始",
        "begin_type": 6,
        "begin_param": 0,
        "end_type": 0,
        "end_param": 1,
        "interval_sec": 0,
        "dzh_desc": "begin=6 收市后执行一次(日线选股)",
        "start_mode": "after_close",
        "duration_mode": "once"
    },
    {
        "id": "begin_7_trading_day",
        "name": "指定交易日",
        "begin_type": 7,
        "begin_param": 0,
        "end_type": 0,
        "end_param": 2147483647,
        "interval_sec": 60,
        "dzh_desc": "begin=7 指定交易日时间开始",
        "start_mode": "specified_day",
        "duration_mode": "continuous"
    },
    {
        "id": "golden_two_half",
        "name": "金色两点半时序",
        "begin_type": 4,
        "begin_param": 93000,
        "end_type": 0,
        "end_param": 18000,
        "interval_sec": 5,
        "dzh_desc": "盘中五个时间节点:10:00/11:00/13:50/14:00/14:20,间隔从20秒加速到5秒,14:30最终输出",
        "start_mode": "at_time",
        "duration_mode": "until",
        "time_slots": [
            {"time": "10:00", "action": "baseline", "interval_sec": 20, "desc": "基准池建立"},
            {"time": "11:00", "action": "first_check", "interval_sec": 20, "desc": "一次考量"},
            {"time": "13:50", "action": "second_check", "interval_sec": 5, "desc": "二次考量(加速)"},
            {"time": "14:00", "action": "ready_pool", "interval_sec": 5, "desc": "预备池(超高频)"},
            {"time": "14:20", "action": "final_filter", "interval_sec": 5, "desc": "最终筛选(超高频)"},
            {"time": "14:30", "action": "output", "interval_sec": 0, "desc": "金色两点半最终输出"}
        ]
    }
]


# =============================================================================
# Generate all files
# =============================================================================
def generate_configs():
    """原 generate_configs.py 的 main 逻辑：生成 6 个 JSON 配置文件。"""
    print("Generating 6 JSON config files from DZH stock pool analysis...")
    print()
    write_json("modules.json", MODULES)
    write_json("dispatch.json", DISPATCH)
    write_json("pipelines.json", PIPELINES)
    write_json("engines.json", ENGINES)
    write_json("markets.json", MARKETS)
    write_json("schedules.json", SCHEDULES)
    print()
    print("Done. All 6 files generated.")

    print()
    print("=== VERIFICATION ===")
    print(f"  modules.json:    {len(MODULES)} modules (required: 9)")
    print(f"  dispatch.json:   {len(DISPATCH)} conditions (required: 30+)")
    print(f"  pipelines.json:  {len(PIPELINES)} pipelines (required: 3)")
    print(f"  engines.json:    {len(ENGINES)} engines")

    dc = set(e["engine"] for e in DISPATCH)
    ec = set(e["id"] for e in ENGINES)
    missing = dc - ec
    print(f"  dispatch engines: {sorted(dc)}")
    print(f"  engines.json IDs: {sorted(ec)}")
    print(f"  dispatch→engines missing refs: {sorted(missing) if missing else 'NONE ✓'}")

    print()
    print("=== MODULE dzh_cell_type SUMMARY ===")
    for m in MODULES:
        t = m["dzh_cell_type"]
        ts = str(t).replace("[", "").replace("]", "").replace(" ", "") if isinstance(t, list) else str(t)
        print(f"  {m['id']:20s} → DZH type {ts:12s} | handler: {m['handler']}")

    print()
    print("=== DISPATCH BY ENGINE ===")
    from collections import Counter
    cnt = Counter(e["engine"] for e in DISPATCH)
    for eng, c in cnt.most_common():
        print(f"  {eng:20s} → {c} conditions")

    print()
    print("=== SCHEDULES BEGIN TYPES ===")
    for s in SCHEDULES:
        print(f"  {s['id']:30s} → begin_type={s['begin_type']} | {s['dzh_desc']}")


def validate_refs():
    """原 validate_refs.py 的主逻辑：校验 field_refs / handler / config_table / time_source 引用。"""
    with open(os.path.join(CONFIG_DIR, 'modules.json'), 'r', encoding='utf-8') as f:
        modules = json.load(f)

    with open(os.path.join(CONFIG_DIR, 'field_definitions.json'), 'r', encoding='utf-8') as f:
        fd = json.load(f)

    global_keys = set(fd['global_fields'].keys())
    type_keys = {k: set(v.keys()) for k, v in fd['type_specific_fields'].items()}
    flow_keys = set(fd['flow_fields'].keys())
    bit_keys = {k: set(v.keys()) for k, v in fd['bit_fields'].items()}

    m = modules['modules']

    # Map module to its type key for field_definitions
    module_type_map = {
        'candidate_provider': '202',
        'condition_filter': '201',
        'stock_state_pool': '200',
        'discard_sink': '4',
        'text_label': '1',
        'container_box': '2',
        'state_column': '3',
        'flow_arrow': '6',
        'execution_order': '5',
    }

    print('=== Validating field_refs against type_specific_fields + global_fields ===')
    for mod_name, type_key in module_type_map.items():
        refs = set(m[mod_name]['field_refs'])
        valid = type_keys.get(type_key, set()) | global_keys
        missing = refs - valid
        print(f'  {mod_name} (type {type_key}): {"OK" if not missing else "MISSING: " + str(missing)}')

    print()
    print('=== Validating flow_schema field_refs ===')
    fs_refs = set(modules['flow_schema']['field_refs'])
    missing = fs_refs - flow_keys
    print(f'  flow_schema: {"OK" if not missing else "MISSING: " + str(missing)}')

    print()
    print('=== Validating _group_* inner fields ===')

    # stock_state_pool
    sp = m['stock_state_pool']
    print(f'  _group_alert vs bit_fields.200: {"OK" if not (set(sp["_group_alert"]["fields"]) - bit_keys["200"]) else "MISSING: " + str(set(sp["_group_alert"]["fields"]) - bit_keys["200"])}')
    print(f'  _group_display vs bit_fields.200: {"OK" if not (set(sp["_group_display"]["fields"]) - bit_keys["200"]) else "MISSING: " + str(set(sp["_group_display"]["fields"]) - bit_keys["200"])}')
    valid_200 = type_keys['200'] | global_keys
    print(f'  _group_action vs type_specific_fields.200+global: {"OK" if not (set(sp["_group_action"]["fields"]) - valid_200) else "MISSING: " + str(set(sp["_group_action"]["fields"]) - valid_200)}')
    print(f'  _group_tradeattr vs type_specific_fields.200+global: {"OK" if not (set(sp["_group_tradeattr"]["fields"]) - valid_200) else "MISSING: " + str(set(sp["_group_tradeattr"]["fields"]) - valid_200)}')

    # discard_sink
    ds = m['discard_sink']
    print(f'  discard_sink _group_attr vs bit_fields.4: {"OK" if not (set(ds["_group_attr"]["fields"]) - bit_keys["4"]) else "MISSING: " + str(set(ds["_group_attr"]["fields"]) - bit_keys["4"])}')

    # text_label
    tl = m['text_label']
    print(f'  text_label _group_font_style vs bit_fields.1: {"OK" if not (set(tl["_group_font_style"]["fields"]) - bit_keys["1"]) else "MISSING: " + str(set(tl["_group_font_style"]["fields"]) - bit_keys["1"])}')

    # container_box
    cb = m['container_box']
    print(f'  container_box _group_display vs bit_fields.2: {"OK" if not (set(cb["_group_display"]["fields"]) - bit_keys["2"]) else "MISSING: " + str(set(cb["_group_display"]["fields"]) - bit_keys["2"])}')

    # state_column
    sc = m['state_column']
    print(f'  state_column _group_display vs bit_fields.3: {"OK" if not (set(sc["_group_display"]["fields"]) - bit_keys["3"]) else "MISSING: " + str(set(sc["_group_display"]["fields"]) - bit_keys["3"])}')

    # flow_schema
    fs = modules['flow_schema']
    print(f'  flow_schema _group_transfer vs bit_fields.flow: {"OK" if not (set(fs["_group_transfer"]["fields"]) - bit_keys["flow"]) else "MISSING: " + str(set(fs["_group_transfer"]["fields"]) - bit_keys["flow"])}')
    print(f'  flow_schema _group_timing vs flow_fields: {"OK" if not (set(fs["_group_timing"]["fields"]) - flow_keys) else "MISSING: " + str(set(fs["_group_timing"]["fields"]) - flow_keys)}')
    print(f'  flow_schema _group_visual vs flow_fields: {"OK" if not (set(fs["_group_visual"]["fields"]) - flow_keys) else "MISSING: " + str(set(fs["_group_visual"]["fields"]) - flow_keys)}')

    print()
    print('All validations complete.')

    # ───────────────────────────────────────────────────────
    # 8.2 扩展验证: handler引用 + config_table引用 + time_source引用
    # ───────────────────────────────────────────────────────
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    print()
    print('=== Validating handler references ===')
    from engine import _HR
    errors_handler = []

    # edge_strategies handler 引用
    with open(os.path.join(CONFIG_DIR, 'edge_strategies.json'), encoding='utf-8') as f:
        es = json.load(f)
    for key, strat in es.get('strategies', {}).items():
        h = strat.get('handler', '')
        if h and h not in _HR:
            errors_handler.append(f"edge_strategies[{key}].handler = '{h}' 不在 _HR 中")
    for key, strat in es.get('strategies', {}).items():
        pre = strat.get('pre_inject', '')
        if pre and isinstance(pre, str) and pre not in _HR:
            errors_handler.append(f"edge_strategies[{key}].pre_inject = '{pre}' 不在 _HR 中")

    # node_init handler 引用
    for ntype, cfg in es.get('node_init', {}).items():
        h = cfg.get('handler', '')
        if h and h not in _HR:
            errors_handler.append(f"node_init[{ntype}].handler = '{h}' 不在 _HR 中")

    # post_tick_pipeline handler 引用
    with open(os.path.join(CONFIG_DIR, 'post_tick_pipeline.json'), encoding='utf-8') as f:
        ptp = json.load(f)
    for stage in ptp.get('pipeline', []):
        h = stage.get('handler', '')
        if h and h not in _HR:
            errors_handler.append(f"post_tick_pipeline[{stage.get('stage')}].handler = '{h}' 不在 _HR 中")

    # pool_roles resolution_rules handler 引用
    with open(os.path.join(CONFIG_DIR, 'pool_roles.json'), encoding='utf-8') as f:
        pr = json.load(f)
    # pool_roles 的 handler 是内嵌在 _resolve_role 中的, 此处跳过

    if errors_handler:
        for e in errors_handler:
            print(f'  ERROR: {e}')
    else:
        print('  All handler references OK')

    print()
    print('=== Validating config_table references ===')
    errors_ct = []
    # 加载所有 config JSON
    loaded_tables = set()
    for fn in os.listdir(CONFIG_DIR):
        if fn.endswith('.json'):
            loaded_tables.add(fn[:-5])  # strip .json

    for stage in ptp.get('pipeline', []):
        ct = stage.get('config_table', '')
        if ct and ct not in loaded_tables:
            errors_ct.append(f"post_tick_pipeline[{stage.get('stage')}].config_table = '{ct}' 未找到对应 JSON 文件")

    if errors_ct:
        for e in errors_ct:
            print(f'  ERROR: {e}')
    else:
        print('  All config_table references OK')

    print()
    print('=== Validating time_source_id references ===')
    errors_ts = []
    with open(os.path.join(CONFIG_DIR, 'time_sources.json'), encoding='utf-8') as f:
        ts_cfg = json.load(f)
    ts_ids = set(ts_cfg.get('time_sources', {}).keys())

    with open(os.path.join(CONFIG_DIR, 'runtime_modes.json'), encoding='utf-8') as f:
        rm = json.load(f)
    for mode_id, mode_cfg in rm.get('modes', {}).items():
        tsid = mode_cfg.get('time_source_id', '')
        if tsid and tsid not in ts_ids:
            errors_ts.append(f"runtime_modes[{mode_id}].time_source_id = '{tsid}' 不在 time_sources.json 中")

    if errors_ts:
        for e in errors_ts:
            print(f'  ERROR: {e}')
    else:
        print('  All time_source_id references OK')

    print()
    print('Extended validations complete.')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="配置生成与校验工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("generate", help="运行 generate_configs 生成 JSON 配置")
    subparsers.add_parser("validate", help="运行 validate_refs 校验引用")
    args = parser.parse_args()
    if args.command == "generate":
        generate_configs()
    elif args.command == "validate":
        validate_refs()
