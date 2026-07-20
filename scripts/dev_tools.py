"""dev_tools.py - 开发工具集（合并自 6 个脚本）。

合并自：
  - scripts/analyze_dzh.py        : DZH XML 分析工具集（合并自 doc/tempdzh/ 4个分析脚本）
  - scripts/config_tools.py       : 配置生成与校验工具（合并自 config/ 2个脚本）
  - scripts/decode_formulas.py    : DZH 大智慧股票池公式解码器 v3
  - scripts/debug_formula.py      : DZH 公式解码调试
  - scripts/merge_config_tables.py: 配置表收敛（Task 11）
  - scripts/xml_tools.py          : XML 检查与注入工具（合并自 web/ 2个脚本）

命令行入口（子命令模式）：
  python scripts/dev_tools.py analyze_dzh <xml|xml2|xml3|tianji_bfs>
  python scripts/dev_tools.py config_tools <generate|validate>
  python scripts/dev_tools.py decode_formulas
  python scripts/dev_tools.py debug_formula [--xml-path PATH]
  python scripts/dev_tools.py merge_config_tables
  python scripts/dev_tools.py xml_tools <check|inject XML_FILE [OUT_FILE]>
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List


# ════════════════════════════════════════════════════════════════════
# 1. analyze_dzh - DZH XML 分析工具集（合并自 doc/tempdzh/ 4个分析脚本）
# ════════════════════════════════════════════════════════════════════

def analyze_dzh_analyze_xml():
    """原 analyze_xml.py 的主逻辑：分析综合设置下多个 XML 的池节点与边。"""
    files = [
        r'h:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\泛系游鹤竞价版.xml',
        r'h:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\超赢7号.xml',
        r'h:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\七剑下天山.xml',
        r'h:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\屠龙刀-倚天剑.xml',
        r'h:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\《天机-0号》-竞价股票池.xml',
    ]

    for f in files:
        try:
            # Read as bytes, decode with GB2312/GBK
            with open(f, 'rb') as fh:
                raw = fh.read()
            text = raw.decode('gbk', errors='replace')
            tree = ET.fromstring(text)
            root = tree
            cells = root.find('cells')
            flows = root.find('flows')

            # Pool nodes (type 200, 201, 202)
            pool_nodes = {}
            for c in cells.findall('cell'):
                t = c.get('type')
                if t in ('200', '201', '202'):
                    pool_nodes[c.get('id')] = {'type': t, 'text': c.get('text', ''), 'clr': c.get('clr', '')}

            # Edges
            edges = []
            if flows is not None:
                for fl in flows.findall('flow'):
                    edges.append({
                        'from': fl.get('from'),
                        'to': fl.get('to'),
                        'begin': fl.get('begin', ''),
                        'begint': fl.get('begint', ''),
                        'endt': fl.get('endt', ''),
                        'interval': fl.get('interval', ''),
                        'attr': fl.get('attr', ''),
                    })

            n202 = sum(1 for v in pool_nodes.values() if v['type'] == '202')
            n200 = sum(1 for v in pool_nodes.values() if v['type'] == '200')
            n201 = sum(1 for v in pool_nodes.values() if v['type'] == '201')

            fname = os.path.basename(f)
            print(f'=== {fname} ===')
            print(f'  Pool nodes: {len(pool_nodes)} (202={n202}, 200={n200}, 201={n201})')
            print(f'  Edges: {len(edges)}')

            for nid, info in sorted(pool_nodes.items(), key=lambda x: int(x[0])):
                txt = info['text'][:30]
                print(f'    node {nid}: type={info["type"]} text={txt}')

            for e in edges:
                print(f'    edge {e["from"]}->{e["to"]} begin={e["begin"]} begint={e["begint"]} endt={e["endt"]} interval={e["interval"]} attr={e["attr"]}')

            source_ids = [nid for nid, v in pool_nodes.items() if v['type'] == '202']
            print(f'  Source pools: {source_ids}')

            # Build adjacency (pool->pool via condition nodes)
            # We need to find composite edges: pool->cond->pool
            # First, find which nodes are condition nodes (type=201)
            cond_nodes = set(nid for nid, v in pool_nodes.items() if v['type'] == '201')
            pool_only = set(nid for nid, v in pool_nodes.items() if v['type'] in ('200', '202'))

            # Build direct pool->pool edges (composite)
            composite_edges = []
            for e in edges:
                fid = e['from']
                tid = e['to']
                if fid in pool_only and tid in cond_nodes:
                    # Find outgoing from cond to pool
                    for e2 in edges:
                        if e2['from'] == tid and e2['to'] in pool_only:
                            composite_edges.append({
                                'from': fid, 'to': e2['to'], 'cond': tid,
                                'begin': e.get('begin', ''), 'begint': e.get('begint', ''),
                                'endt': e.get('endt', ''), 'interval': e.get('interval', ''),
                                'attr': e.get('attr', ''),
                            })
                elif fid in pool_only and tid in pool_only:
                    composite_edges.append({
                        'from': fid, 'to': tid, 'cond': None,
                        'begin': e.get('begin', ''), 'begint': e.get('begint', ''),
                        'endt': e.get('endt', ''), 'interval': e.get('interval', ''),
                        'attr': e.get('attr', ''),
                    })

            print(f'  Composite edges: {len(composite_edges)}')

            # BFS
            total_rows = 0
            for sid in source_ids:
                visited_local = set()
                q = [(sid, 0)]
                visited_local.add(sid)
                while q:
                    pid, depth = q.pop(0)
                    total_rows += 1  # pool-decl row
                    pool_edges = [ce for ce in composite_edges if ce['from'] == pid]
                    for pe in pool_edges:
                        total_rows += 1  # edge row
                        target = pe['to']
                        if target not in visited_local:
                            visited_local.add(target)
                            q.append((target, depth + 1))

            print(f'  BFS total rows (algorithm): {total_rows}')
            print()
        except Exception as e:
            fname = os.path.basename(f)
            print(f'=== {fname} === ERROR: {e}')
            print()


def analyze_dzh_analyze_xml2():
    """原 analyze_xml2.py 的主逻辑：分析节点/流并按源 BFS 统计行数。"""
    files = [
        r'h:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\泛系游鹤竞价版.xml',
        r'h:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\《天机-0号》-竞价股票池.xml',
    ]

    for f in files:
        print(f'\n{"=" * 60}')
        print(f'FILE: {os.path.basename(f)}')
        print(f'{"=" * 60}')
        with open(f, 'rb') as fh:
            raw = fh.read()
        text = raw.decode('gbk', errors='replace')
        tree = ET.fromstring(text)
        pool = tree
        print('nextid:', pool.get('nextid'))
        cells = pool.find('cells')
        all_cells = cells.findall('cell')
        print(f'Total cells: {len(all_cells)}')

        nodes = []
        for c in all_cells:
            t = int(c.get('type'))
            if t in (200, 201, 202, 7, 8):
                nodes.append({
                    'id': c.get('id'),
                    'type': t,
                    'text': c.get('text', ''),
                    'attr': c.get('attr', ''),
                })
        print(f'State/cond/source nodes: {len(nodes)}')
        for n in nodes:
            type_name = {200: 'STATE', 201: 'COND', 202: 'SOURCE', 7: 'TDX_CAND', 8: 'TDX_STATE'}[n['type']]
            print(f'  id={n["id"]} type={type_name} text={n["text"]}')

        flows = pool.find('flows')
        flow_list = flows.findall('flow')
        print(f'\nTotal flows: {len(flow_list)}')
        for fl in flow_list:
            print(f'  {fl.get("from")} -> {fl.get("to")} attr={fl.get("attr")} begin={fl.get("begin")} begint={fl.get("begint")} endt={fl.get("endt")} interval={fl.get("interval")}')

        # BFS row count
        pool_ids = set()
        cond_ids = set()
        for n in nodes:
            if n['type'] in (200, 8):
                pool_ids.add(n['id'])
            elif n['type'] == 201:
                cond_ids.add(n['id'])

        source_ids = set()
        for n in nodes:
            if n['type'] in (202, 7):
                source_ids.add(n['id'])

        # Build adjacency: pool -> [(cond, target_pool)]
        adj = {}
        for fl in flow_list:
            fid = fl.get('from')
            tid = fl.get('to')
            if fid in pool_ids and tid in cond_ids:
                if fid not in adj:
                    adj[fid] = []
                adj[fid].append((tid, fl))

        # For each cond, find outgoing to pool
        cond_to_pool = {}
        for fl in flow_list:
            fid = fl.get('from')
            tid = fl.get('to')
            if fid in cond_ids and tid in pool_ids:
                cond_to_pool[fid] = (tid, fl)

        # Composite edges: pool -> cond -> pool
        composite = []
        for pid in adj:
            for (cid, upstream_fl) in adj[pid]:
                if cid in cond_to_pool:
                    target_pid, downstream_fl = cond_to_pool[cid]
                    composite.append({
                        'from': pid,
                        'to': target_pid,
                        'cond': cid,
                        'upstream': upstream_fl,
                        'downstream': downstream_fl,
                    })

        # BFS from each source
        total_rows = 0
        for src in sorted(source_ids):
            visited = set()
            queue = [src]
            visited.add(src)
            rows = 1  # source row
            while queue:
                cur = queue.pop(0)
                # pool-decl row if not source
                if cur != src:
                    rows += 1
                # edges from this pool
                if cur in adj:
                    for (cid, upstream_fl) in adj[cur]:
                        if cid in cond_to_pool:
                            target_pid, _ = cond_to_pool[cid]
                            rows += 1  # edge row
                            if target_pid not in visited:
                                visited.add(target_pid)
                                queue.append(target_pid)
            print(f'\nBFS from source {src}: {rows} rows, visited {len(visited)} pools')
            total_rows += rows
        print(f'TOTAL ROWS: {total_rows}')


def analyze_dzh_analyze_xml3():
    """原 analyze_xml3.py 的主逻辑：分类节点、构建复合边并 BFS 统计总行数。"""

    def analyze_file(f):
        print(f'\n{"=" * 80}')
        print(f'FILE: {os.path.basename(f)}')
        print(f'{"=" * 80}')
        with open(f, 'rb') as fh:
            raw = fh.read()
        text = raw.decode('gbk', errors='replace')
        tree = ET.fromstring(text)
        pool = tree

        nextid = int(pool.get('nextid', '0'))
        print(f'nextid: {nextid}')

        cells = pool.find('cells')
        all_cells = cells.findall('cell')
        print(f'Total cells: {len(all_cells)}')

        # Categorize nodes
        source_pools = []   # type 202 or 7 (candidate pools)
        state_pools = []    # type 200 or 8 (state pools)
        cond_nodes = []     # type 201 (condition nodes)

        for c in all_cells:
            t = int(c.get('type'))
            node_info = {
                'id': c.get('id'),
                'type': t,
                'text': c.get('text', ''),
            }
            if t in (202, 7):
                source_pools.append(node_info)
            elif t in (200, 8):
                state_pools.append(node_info)
            elif t == 201:
                cond_nodes.append(node_info)

        print(f'Source pools (备选池): {len(source_pools)}')
        for s in source_pools:
            print(f'  id={s["id"]} text={s["text"]}')

        print(f'State pools (状态池): {len(state_pools)}')
        for s in state_pools:
            print(f'  id={s["id"]} text={s["text"]}')

        print(f'Condition nodes (条件节点): {len(cond_nodes)}')

        # Parse flows
        flows = pool.find('flows')
        flow_list = flows.findall('flow')
        print(f'\nTotal flows: {len(flow_list)}')

        # Build sets for quick lookup
        source_ids = set(s['id'] for s in source_pools)
        state_pool_ids = set(s['id'] for s in state_pools)
        cond_ids = set(c['id'] for c in cond_nodes)

        # All pool IDs (source + state) - used to check if a node is a "pool"
        all_pool_ids = source_ids | state_pool_ids

        # Build adjacency: pool -> [(cond_node_id, upstream_flow)]
        adj = {}
        for fl in flow_list:
            fid = fl.get('from')
            tid = fl.get('to')
            # Pool -> Cond flow
            if fid in all_pool_ids and tid in cond_ids:
                if fid not in adj:
                    adj[fid] = []
                adj[fid].append((tid, fl))

        # Build cond_to_pool: cond_node_id -> (target_pool_id, downstream_flow)
        cond_to_pool = {}
        for fl in flow_list:
            fid = fl.get('from')
            tid = fl.get('to')
            # Cond -> Pool flow (target must be a STATE pool, not source)
            if fid in cond_ids and tid in state_pool_ids:
                cond_to_pool[fid] = (tid, fl)

        # Find composite edges: pool -> cond -> state_pool
        composite_edges = []
        for pid in adj:
            for (cid, upstream_fl) in adj[pid]:
                if cid in cond_to_pool:
                    target_pid, downstream_fl = cond_to_pool[cid]
                    composite_edges.append({
                        'from': pid,
                        'to': target_pid,
                        'cond': cid,
                        'upstream': upstream_fl,
                    })

        print(f'\nComposite edges (pool->cond->pool): {len(composite_edges)}')
        for ce in composite_edges:
            print(f'  {ce["from"]} -> {ce["cond"]} -> {ce["to"]}')

        # BFS from each source pool
        total_rows = 0
        for src in source_pools:
            src_id = src['id']
            visited = set()
            queue = [src_id]
            visited.add(src_id)
            rows = 1  # source row

            while queue:
                cur = queue.pop(0)

                # pool-decl row for non-source pools
                if cur != src_id:
                    rows += 1

                # Get all composite edges from this pool
                outgoing = [ce for ce in composite_edges if ce['from'] == cur]

                for ce in outgoing:
                    rows += 1  # edge row
                    target = ce['to']
                    if target not in visited:
                        visited.add(target)
                        queue.append(target)

            print(f'\nBFS from source {src_id} ({src["text"]}): {rows} rows, visited {len(visited)} pools')
            total_rows += rows

        print(f'\n>>> TOTAL ROWS: {total_rows}')
        return total_rows

    # Analyze both files
    files = [
        r'h:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\泛系游鹤竞价版.xml',
        r'h:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\《天机-0号》-竞价股票池.xml',
    ]

    results = {}
    for f in files:
        results[os.path.basename(f)] = analyze_file(f)

    print(f'\n\n{"=" * 80}')
    print('SUMMARY')
    print(f'{"=" * 80}')
    for name, count in results.items():
        print(f'{name}: {count} rows')


def analyze_dzh_analyze_tianji_bfs():
    """原 analyze_tianji_bfs.py 的主逻辑：对《天机-0号》做 BFS 并逐行打印。"""
    f = r'h:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\《天机-0号》-竞价股票池.xml'
    with open(f, 'rb') as fh:
        raw = fh.read()
    text = raw.decode('gbk', errors='replace')
    tree = ET.fromstring(text)
    pool = tree

    cells = pool.find('cells')
    all_cells = cells.findall('cell')

    source_pools = []
    state_pools = []
    cond_nodes = []

    for c in all_cells:
        t = int(c.get('type'))
        node_info = {'id': c.get('id'), 'type': t, 'text': c.get('text', '')}
        if t in (202, 7):
            source_pools.append(node_info)
        elif t in (200, 8):
            state_pools.append(node_info)
        elif t == 201:
            cond_nodes.append(node_info)

    source_ids = set(s['id'] for s in source_pools)
    state_pool_ids = set(s['id'] for s in state_pools)
    cond_ids = set(c['id'] for c in cond_nodes)
    all_pool_ids = source_ids | state_pool_ids

    flows = pool.find('flows')
    flow_list = flows.findall('flow')

    adj = {}
    for fl in flow_list:
        fid = fl.get('from')
        tid = fl.get('to')
        if fid in all_pool_ids and tid in cond_ids:
            if fid not in adj:
                adj[fid] = []
            adj[fid].append((tid, fl))

    cond_to_pool = {}
    for fl in flow_list:
        fid = fl.get('from')
        tid = fl.get('to')
        if fid in cond_ids and tid in state_pool_ids:
            cond_to_pool[fid] = (tid, fl)

    composite_edges = []
    for pid in adj:
        for (cid, upstream_fl) in adj[pid]:
            if cid in cond_to_pool:
                target_pid, downstream_fl = cond_to_pool[cid]
                composite_edges.append({
                    'from': pid,
                    'to': target_pid,
                    'cond': cid,
                })

    # Build name lookup
    name_map = {}
    for s in source_pools:
        name_map[s['id']] = s['text']
    for s in state_pools:
        name_map[s['id']] = s['text']

    # BFS and print row by row
    for src in source_pools:
        src_id = src['id']
        visited = set()
        queue = [src_id]
        visited.add(src_id)
        row_num = 0

        while queue:
            cur = queue.pop(0)
            cur_name = name_map.get(cur, cur)
            is_source = (cur == src_id)

            row_num += 1
            if is_source:
                print(f'{row_num:3d}. [SOURCE] {cur_name} (id={cur})')
            else:
                print(f'{row_num:3d}. [POOL]   {cur_name} (id={cur})')

            outgoing = [ce for ce in composite_edges if ce['from'] == cur]
            for ce in outgoing:
                row_num += 1
                target_name = name_map.get(ce['to'], ce['to'])
                already_visited = ce['to'] in visited
                marker = ' (already visited, no pool-decl)' if already_visited else ''
                print(f'{row_num:3d}. [EDGE]   └─▶ {target_name} (id={ce["to"]}){marker}')
                if not already_visited:
                    visited.add(ce['to'])
                    queue.append(ce['to'])

        print(f'\nTotal rows: {row_num}')
        print(f'Visited pools: {len(visited)}')


# ════════════════════════════════════════════════════════════════════
# 2. config_tools - 配置生成与校验工具（合并自 config/ 2个脚本）
# ════════════════════════════════════════════════════════════════════

# 合并后文件位于 scripts/，config/ 目录路径为上级的 config 子目录
CONFIG_TOOLS_CONFIG_DIR = str(Path(__file__).parent.parent / "config")
CONFIG_TOOLS_OUTPUT_DIR = CONFIG_TOOLS_CONFIG_DIR


def config_tools_write_json(filename, data):
    path = os.path.join(CONFIG_TOOLS_OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    count = len(data) if isinstance(data, list) else len(data) if isinstance(data, dict) else 1
    print(f"  {filename}: {count} entries written")


# =============================================================================
# 1. modules.json — EXACTLY 9 modules
# =============================================================================
CONFIG_TOOLS_MODULES = [
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
CONFIG_TOOLS_DISPATCH = [
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
CONFIG_TOOLS_PIPELINES = [
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
CONFIG_TOOLS_ENGINES = [
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
CONFIG_TOOLS_MARKETS = [
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
CONFIG_TOOLS_SCHEDULES = [
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
def config_tools_generate_configs():
    """原 generate_configs.py 的 main 逻辑：生成 6 个 JSON 配置文件。"""
    print("Generating 6 JSON config files from DZH stock pool analysis...")
    print()
    config_tools_write_json("modules.json", CONFIG_TOOLS_MODULES)
    config_tools_write_json("dispatch.json", CONFIG_TOOLS_DISPATCH)
    config_tools_write_json("pipelines.json", CONFIG_TOOLS_PIPELINES)
    config_tools_write_json("engines.json", CONFIG_TOOLS_ENGINES)
    config_tools_write_json("markets.json", CONFIG_TOOLS_MARKETS)
    config_tools_write_json("schedules.json", CONFIG_TOOLS_SCHEDULES)
    print()
    print("Done. All 6 files generated.")

    print()
    print("=== VERIFICATION ===")
    print(f"  modules.json:    {len(CONFIG_TOOLS_MODULES)} modules (required: 9)")
    print(f"  dispatch.json:   {len(CONFIG_TOOLS_DISPATCH)} conditions (required: 30+)")
    print(f"  pipelines.json:  {len(CONFIG_TOOLS_PIPELINES)} pipelines (required: 3)")
    print(f"  engines.json:    {len(CONFIG_TOOLS_ENGINES)} engines")

    dc = set(e["engine"] for e in CONFIG_TOOLS_DISPATCH)
    ec = set(e["id"] for e in CONFIG_TOOLS_ENGINES)
    missing = dc - ec
    print(f"  dispatch engines: {sorted(dc)}")
    print(f"  engines.json IDs: {sorted(ec)}")
    print(f"  dispatch→engines missing refs: {sorted(missing) if missing else 'NONE ✓'}")

    print()
    print("=== MODULE dzh_cell_type SUMMARY ===")
    for m in CONFIG_TOOLS_MODULES:
        t = m["dzh_cell_type"]
        ts = str(t).replace("[", "").replace("]", "").replace(" ", "") if isinstance(t, list) else str(t)
        print(f"  {m['id']:20s} → DZH type {ts:12s} | handler: {m['handler']}")

    print()
    print("=== DISPATCH BY ENGINE ===")
    cnt = Counter(e["engine"] for e in CONFIG_TOOLS_DISPATCH)
    for eng, c in cnt.most_common():
        print(f"  {eng:20s} → {c} conditions")

    print()
    print("=== SCHEDULES BEGIN TYPES ===")
    for s in CONFIG_TOOLS_SCHEDULES:
        print(f"  {s['id']:30s} → begin_type={s['begin_type']} | {s['dzh_desc']}")


def config_tools_validate_refs():
    """原 validate_refs.py 的主逻辑：校验 field_refs / handler / config_table / time_source 引用。"""
    with open(os.path.join(CONFIG_TOOLS_CONFIG_DIR, 'modules.json'), 'r', encoding='utf-8') as f:
        modules = json.load(f)

    with open(os.path.join(CONFIG_TOOLS_CONFIG_DIR, 'field_definitions.json'), 'r', encoding='utf-8') as f:
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
    with open(os.path.join(CONFIG_TOOLS_CONFIG_DIR, 'edge_strategies.json'), encoding='utf-8') as f:
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
    with open(os.path.join(CONFIG_TOOLS_CONFIG_DIR, 'post_tick_pipeline.json'), encoding='utf-8') as f:
        ptp = json.load(f)
    for stage in ptp.get('pipeline', []):
        h = stage.get('handler', '')
        if h and h not in _HR:
            errors_handler.append(f"post_tick_pipeline[{stage.get('stage')}].handler = '{h}' 不在 _HR 中")

    # pool_roles resolution_rules handler 引用
    with open(os.path.join(CONFIG_TOOLS_CONFIG_DIR, 'pool_roles.json'), encoding='utf-8') as f:
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
    for fn in os.listdir(CONFIG_TOOLS_CONFIG_DIR):
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
    with open(os.path.join(CONFIG_TOOLS_CONFIG_DIR, 'time_sources.json'), encoding='utf-8') as f:
        ts_cfg = json.load(f)
    ts_ids = set(ts_cfg.get('time_sources', {}).keys())

    with open(os.path.join(CONFIG_TOOLS_CONFIG_DIR, 'runtime_modes.json'), encoding='utf-8') as f:
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


# ════════════════════════════════════════════════════════════════════
# 3. decode_formulas - DZH 大智慧股票池公式解码器 v3
# ════════════════════════════════════════════════════════════════════
#
# 核心改进:
#   1. 基于二进制末尾特征 (;\0) 精准定位公式文本
#   2. 全局扫描所有 type=201 单元的 possible patterns
#   3. 处理 dzhpool 全部 XML 文件
#   4. 输出结构化报告
#
# 已知局限:
#   - type=201 的 indi 是公式筛选条件, 小部分可能是纯二进制参数
#   - 文本段中若包含 GBK 编码的中文字符将无法提取
#   - 极少数公式使用非 ;\0 结尾, 会降级到传统 ASCII 提取

# DZH 内部指标代码 → 可读名称翻译表
DECODE_FORMULAS_DZH_INDICATOR_MAP = {
    # 超赢系列指标 (aa-bz)
    "aa": "CYS",         "ab": "CYF",         "ac": "AR",
    "ad": "ASI",         "ae": "ATR",         "af": "BIAS",
    "ag": "BOLL",        "ah": "BR",          "ai": "CCI",
    "aj": "CDP",         "ak": "CR",          "al": "DMA",
    "am": "DMI",         "an": "DPO",         "ao": "EMV",
    "ap": "ENE",         "aq": "EXPMA",       "ar": "KDJ",
    "as": "LWR",         "at": "MACD",        "au": "MIKE",
    "av": "OBV",         "aw": "OSC",         "ax": "PSY",
    "ay": "PVT",         "az": "ROC",         "ba": "RSI",
    "bb": "SAR",         "bc": "SOBV",        "bd": "TRIX",
    "be": "VR",          "bf": "VROC",        "bg": "VRSI",
    "bh": "W%R",         "bi": "WR",          "bj": "XD",
    "bk": "ZLMM",        "bl": "ZMM",         "bm": "AD",
    "bn": "ADL",         "bo": "A/D",
    # DDX/DDY 系列
    "dd": "DDX",         "de": "DDY",         "df": "DDZ",
    "dg": "DDX1",        "dh": "DDY1",        "di": "DDZ1",
    "dj": "DDX2",        "dk": "DDY2",
    # 资金流向系列
    "ef": "主力力度",    "eg": "散户力度",    "eh": "资金流向",
    "ei": "大单比率",    "ej": "小单比率",
    # 常见公式变量名
    "sp": "选股条件",    "si": "筛选条件",
    "y0": "条件0",       "y1": "条件1",       "y2": "条件2",
    "y3": "条件3",       "y4": "条件4",       "y5": "条件5",
    # 常见输出线名
    "a1": "条件A1", "a2": "条件A2", "b1": "条件B1", "b2": "条件B2",
    "c1": "条件C1", "d1": "输出D1", "e1": "输出E1", "f1": "输出F1",
    "ma": "移动平均(MA)", "mb": "移动平均B", "mc": "移动平均C", "md": "移动平均D",
}

# DZH 公式函数名
DECODE_FORMULAS_DZH_FUNCTIONS = {
    "O": "开盘价", "H": "最高价", "L": "最低价", "C": "收盘价",
    "V": "成交量", "A": "均价", "AMO": "成交额",
    "OPEN": "开盘价", "HIGH": "最高价", "LOW": "最低价", "CLOSE": "收盘价",
    "VOL": "成交量", "AMOUNT": "成交额",
    "COUNT": "计数", "SUM": "求和", "IF": "条件判断",
    "REF": "引用", "MA": "移动平均", "HHV": "最高值", "LLV": "最低值",
    "ABS": "绝对值", "MAX": "最大值", "MIN": "最小值",
    "CROSS": "上穿", "FILTER": "过滤", "BACKSET": "赋值",
}

# 公式文本有效字符模式 (用于后置验证)
DECODE_FORMULAS_PATTERN = re.compile(
    r'^[a-zA-Z_][a-zA-Z0-9_]*'   # 变量/函数开头
    r'(\s*[\(\)\+\-\*\/\,\<\>\=\!\&\|\[\]\s\d\.\%\;\:]*)*$'
)


def decode_formulas_read_xml(filepath):
    """读取 XML 文件，正确处理 GB2312 编码"""
    with open(filepath, 'rb') as f:
        raw_bytes = f.read()
    if raw_bytes.startswith(b'<?xml'):
        encoding_match = re.search(rb'encoding=["\']([^"\']+)["\']', raw_bytes[:200])
        if encoding_match:
            enc = encoding_match.group(1).decode('ascii', errors='ignore').upper()
            if enc in ('GB2312', 'GBK', 'GB18030'):
                raw_bytes = raw_bytes.decode('gbk', errors='replace')
            else:
                raw_bytes = raw_bytes.decode(enc.lower(), errors='replace')
        else:
            raw_bytes = raw_bytes.decode('gbk', errors='replace')
    else:
        raw_bytes = raw_bytes.decode('gbk', errors='replace')
    return raw_bytes


def decode_formulas_decode_xml_entities(text):
    """解码 XML 实体字符"""
    if not text:
        return text
    text = text.replace('&#xA;', '\n').replace('&#xa;', '\n').replace('&#10;', '\n')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&amp;', '&').replace('&quot;', '"')
    text = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    return text


def decode_formulas_extract_text_segments(raw_bytes):
    """提取二进制中所有 ASCII 文本段"""
    segments = []
    i = 0
    while i < len(raw_bytes):
        if 0x20 <= raw_bytes[i] <= 0x7E:
            start = i
            while i < len(raw_bytes) and 0x20 <= raw_bytes[i] <= 0x7E:
                i += 1
            segments.append({
                'start': start, 'end': i, 'len': i - start,
                'text': raw_bytes[start:i].decode('ascii', errors='replace'),
            })
        else:
            i += 1
    return segments


def decode_formulas_try_gbk_decode(raw_bytes):
    """尝试 GBK 解码整个二进制数据"""
    try:
        return raw_bytes.decode('gbk', errors='replace')
    except:
        return None


def decode_formulas_is_valid_formula(text):
    """验证文本是否像有效的 DZH 公式（严格版）"""
    if not text or len(text) < 3:  # 至少 3 字符
        return False

    # 排除包含非法特殊字符的文本
    illegal_chars = set(r'\`~@#$%^&?{|}=')

    # 检查非法字符
    for c in text:
        if c in illegal_chars:
            return False

    alpha_count = sum(1 for c in text if c.isalpha())
    digit_count = sum(1 for c in text if c.isdigit())
    total_len = len(text)

    # 至少 2 个字母或 3 个字母数字混合
    if alpha_count < 2 and alpha_count + digit_count < 3:
        return False

    # 字母+数字占比必须 > 40%
    alpha_digit_ratio = (alpha_count + digit_count) / total_len if total_len > 0 else 0
    if alpha_digit_ratio < 0.4:
        return False

    # 检查是否以合法的 DZH 公式起始模式开头
    # 有效开头: 函数名、指标代码(2-3字母)、变量名、操作符
    valid_starters = [
        r'^[A-Z][A-Za-z0-9]*\(',         # 函数名开头: DYNAINFO(3), MA(c,5), IF(...
        r'^[a-z]{2,3}[\(\)\[\]\s\d]',    # 指标代码开头: aa(, ab[, si<, sp]
        r'^and\s',                        # and 开头
        r'^or\s',                         # or 开头
        r'^not\(',                        # not( 开头
        r'^[A-Z][\s]*[><=]',             # 单字母+比较符: C>0, O<10, V>=100
        r'^[A-Z][\s]*\(',                # 单字母+括号: O(16), C(5), V(10)
        r'^[a-z][\s]*[><=]',             # 单小写字母+比较符
        r'^[a-z][\s]*\(',                # 单小写字母+括号
        r'^[A-Za-z]{2,3}\s+[A-Za-z]',   # 指标代码+空格: ad ddy, aa count
    ]

    if not any(re.match(p, text) for p in valid_starters):
        return False

    # 必须有操作符特征（括号、比较符、逗号）
    has_paren = '(' in text or ')' in text
    has_comparison = any(op in text for op in ('>', '<', '='))
    has_comma = ',' in text

    # 长文本（>5）必须有操作符
    if len(text) > 5:
        if not (has_paren or has_comparison or has_comma):
            return False

    # 短文本必须有括号或比较符
    if len(text) <= 5:
        if not (has_paren or has_comparison):
            return False

    # 检查不能纯小写 3-5 字符无操作符
    if re.match(r'^[a-z]{3,5}$', text):
        return False

    return True


def decode_formulas_extract_formula_from_binary(raw_bytes):
    """
    从二进制数据中精准提取公式文本。

    策略:
    1. 从末尾向前查找 ;\0 模式定位公式起始点
    2. 扩展提取完整文本段
    3. 验证文本是否为有效公式
    4. 降级: 传统 ASCII 文本段提取
    """
    if not raw_bytes:
        return "", ""

    # ── 策略1: 从末尾查找 ;\0 或 ; 结尾 ──
    formula_text = ""

    # 在最后 64 字节中搜索公式模式
    tail = raw_bytes[-64:] if len(raw_bytes) > 64 else raw_bytes

    # 查找 ;\0 结尾 (标准DZH公式终止符)
    for end_pos in range(len(tail) - 1, -1, -1):
        if tail[end_pos] == 0x3B:  # ';'
            # 检查后面是否跟 \0
            null_terminated = (end_pos + 1 < len(tail) and tail[end_pos + 1] == 0x00)
            crlf_terminated = (end_pos + 2 < len(tail) and tail[end_pos:end_pos+3] == b';\r\n')

            if null_terminated or crlf_terminated:
                # 从 ; 向前扩展提取完整文本
                seg_end = end_pos
                seg_start = seg_end
                while seg_start > 0 and 0x20 <= raw_bytes[-64:][seg_start - 1] <= 0x7E:
                    seg_start -= 1

                candidate = raw_bytes[-64:][seg_start:seg_end + 1].decode('ascii', errors='replace')
                # 后处理: 去掉前导非字母数字字符
                candidate = re.sub(r'^[^a-zA-Z0-9_\(]+', '', candidate)

                # 尝试去掉前导可疑字符(1-2个)，检查是否为更有效的公式
                stripped_candidate = candidate
                # 只在原始文本验证不通过时尝试剥离
                if not decode_formulas_is_valid_formula(candidate):
                    for strip_n in range(1, min(4, len(candidate))):
                        test = candidate[strip_n:]
                        if test and decode_formulas_is_valid_formula(test):
                            # 检查去掉的部分是否明显是噪声(单字符或非字母)
                            removed = candidate[:strip_n]
                            if len(removed) <= 2 and (not removed.isalpha() or len(removed) == 1):
                                stripped_candidate = test
                                break

                candidate = stripped_candidate
                if candidate and decode_formulas_is_valid_formula(candidate):
                    formula_text = candidate
                    break

    # ── 策略2: 查找最后一段以 ; 结尾的长文本 ──
    if not formula_text:
        segments = decode_formulas_extract_text_segments(raw_bytes)
        # 从后往前找，长度 >= 4 且以 ; 结尾的段
        for seg in reversed(segments):
            if seg['len'] >= 4 and seg['text'].rstrip().endswith(';'):
                candidate = seg['text'].rstrip('; \r\n\t\0')
                if candidate and decode_formulas_is_valid_formula(candidate):
                    formula_text = candidate
                    break

    # ── 策略3: 合并策略 ──
    if not formula_text:
        segments = decode_formulas_extract_text_segments(raw_bytes)
        # 找最后一段 >= 4 的文本
        for seg in reversed(segments):
            if seg['len'] >= 5 and seg['end'] >= len(raw_bytes) * 0.7:
                candidate = seg['text'].rstrip('; \r\n\t\0')
                if candidate and decode_formulas_is_valid_formula(candidate):
                    formula_text = candidate
                    break

        # 找最长段
        if not formula_text and segments:
            longest = max(segments, key=lambda s: s['len'])
            if longest['len'] >= 3:
                candidate = longest['text'].rstrip('; \r\n\t\0')
                if candidate and decode_formulas_is_valid_formula(candidate):
                    formula_text = candidate

    # ── 解码后的格式化输出 ──
    readable, explanation = decode_formulas_format_formula_text(formula_text)
    return formula_text, readable, explanation


def decode_formulas_format_formula_text(formula_text):
    """
    格式化 DZH 内部公式文本为人类可读形式。
    """
    if not formula_text:
        return "", ""

    text = formula_text.strip().rstrip(';').strip()
    if not text:
        return formula_text, ""

    explanation_parts = []

    # 翻译 DZH 双字母指标代码
    indicator_match = re.match(r'^([a-zA-Z]{2,3})', text)
    display = text
    if indicator_match:
        code = indicator_match.group(1)
        known = DECODE_FORMULAS_DZH_INDICATOR_MAP.get(code)
        if known:
            display = known + text[len(code):]
            explanation_parts.append(f"指标: {code}→{known}")

    # 翻译函数名
    func_match = re.match(r'^([A-Z]+)\(', display)
    if func_match:
        fname = func_match.group(1)
        if fname in DECODE_FORMULAS_DZH_FUNCTIONS and fname not in ('MA',):  # MA 可能是指标也可能是函数
            pass  # 保持原样

    # 翻译操作符
    display = re.sub(r'<=', '≤', display)
    display = re.sub(r'>=', '≥', display)
    display = re.sub(r'!=', '≠', display)
    display = re.sub(r'(?<![<>])<(?!=)', '<', display)
    display = re.sub(r'(?<![<>])>(?!=)', '>', display)

    explanation = ' | '.join(explanation_parts) if explanation_parts else ""
    return display, explanation


def decode_formulas_decode_formula(indi_b64):
    """解码单个 indi base64 数据"""
    result = {
        'raw_bytes': b'', 'raw_size': 0, 'hex_dump': '',
        'text_segments': [], 'gbk_text': '',
        'formula_text': '', 'readable': '', 'explanation': '',
        'is_binary_only': True, 'has_valid_formula': False,
    }

    if not indi_b64:
        return result

    try:
        raw = base64.b64decode(indi_b64)
    except Exception as e:
        result['formula_text'] = f"[Base64解码失败: {e}]"
        result['readable'] = result['formula_text']
        return result

    result['raw_bytes'] = raw
    result['raw_size'] = len(raw)

    # 十六进制摘要
    if len(raw) <= 48:
        result['hex_dump'] = raw.hex()
    else:
        result['hex_dump'] = f"{raw[:48].hex()} ... {raw[-16:].hex()}"

    # 提取 ASCII 文本段
    segments = decode_formulas_extract_text_segments(raw)
    result['text_segments'] = segments

    # 尝试 GBK 解码
    gbk_text = decode_formulas_try_gbk_decode(raw)
    if gbk_text:
        result['gbk_text'] = gbk_text[:200]  # 截取前 200 字符

    # 判断是否有实际文本内容
    meaningful = [s for s in segments if s['len'] >= 3]
    if meaningful:
        result['is_binary_only'] = False

    # 提取公式文本 (新方法)
    formula_text, readable, explanation = decode_formulas_extract_formula_from_binary(raw)
    result['formula_text'] = formula_text
    result['readable'] = readable
    result['explanation'] = explanation
    result['has_valid_formula'] = bool(formula_text and decode_formulas_is_valid_formula(formula_text))

    if not formula_text:
        result['readable'] = "[纯二进制参数，无可提取的公式文本]"

    return result


def decode_formulas_process_xml(filepath, verbose=True):
    """处理单个 XML 文件，分析所有 DZH 公式"""
    filename = os.path.basename(filepath)

    try:
        xml_str = decode_formulas_read_xml(filepath)
    except Exception as e:
        return {'file': filename, 'path': filepath, 'error': str(e), 'total': 0, 'formulas': []}

    try:
        root = ET.fromstring(xml_str)
    except Exception as e:
        return {'file': filename, 'path': filepath, 'error': f"XML解析失败: {e}", 'total': 0, 'formulas': []}

    cells_container = root.find('cells')
    if cells_container is None:
        return {'file': filename, 'path': filepath, 'total': 0, 'formulas': []}

    type201_cells = []
    for cell_elem in cells_container.findall('cell'):
        cell_type = cell_elem.get('type', '')
        if cell_type == '201':
            indi = cell_elem.get('indi', '')
            if indi:
                type201_cells.append({
                    'id': cell_elem.get('id', ''),
                    'inditype': cell_elem.get('inditype', ''),
                    'crc': cell_elem.get('crc', ''),
                    'sorttype': cell_elem.get('sorttype', ''),
                    'attr': cell_elem.get('attr', ''),
                    'indi': indi,
                    'text': decode_formulas_decode_xml_entities(cell_elem.get('text', '')),
                    'pos': cell_elem.get('pos', ''),
                    'clr': cell_elem.get('clr', ''),
                })

    result = {
        'file': filename,
        'path': filepath,
        'total': len(type201_cells),
        'formulas': [],
    }

    # 按 inditype 分组
    by_type = defaultdict(list)
    for cell in type201_cells:
        it = cell['inditype'] or 'unknown'
        by_type[it].append(cell)

    result['by_type'] = {k: len(v) for k, v in by_type.items()}

    # 解码每个公式
    for it, cells in by_type.items():
        for cell in cells:
            decoded = decode_formulas_decode_formula(cell['indi'])
            entry = {
                'id': cell['id'],
                'inditype': it,
                'attr': cell['attr'],
                'crc': cell['crc'],
                'sorttype': cell['sorttype'],
                'label': cell['text'],
                'b64_len': len(cell['indi']),
                'raw_size': decoded['raw_size'],
                'hex': decoded['hex_dump'],
                'segments': [
                    {'pos': s['start'], 'text': s['text'], 'len': s['len']}
                    for s in decoded['text_segments'] if s['len'] >= 3
                ],
                'gbk_preview': decoded['gbk_text'][:100] if decoded['gbk_text'] else '',
                'formula_text': decoded['formula_text'],
                'readable': decoded['readable'],
                'explanation': decoded['explanation'],
                'is_binary_only': decoded['is_binary_only'],
                'has_valid_formula': decoded['has_valid_formula'],
            }
            result['formulas'].append(entry)

            if verbose and decoded['has_valid_formula']:
                print(f"  [{filename}] 公式 #{cell['id']} ({it}): {decoded['readable']}")

    return result


def decode_formulas_print_result(result):
    """格式化输出处理结果"""
    if 'error' in result:
        print(f"\n  ⚠ [{result['file']}] 错误: {result['error']}")
        return

    print(f"\n{'─'*60}")
    print(f"  文件: {result['file']}")
    print(f"  type=201 公式单元: {result['total']} 个")
    if 'by_type' in result:
        for it, cnt in result['by_type'].items():
            print(f"    inditype={it!r}: {cnt} 个")

    valid_formulas = [f for f in result['formulas'] if f['has_valid_formula']]
    binary_only = [f for f in result['formulas'] if f['is_binary_only']]

    if valid_formulas:
        print(f"\n  ▸ 有效公式 ({len(valid_formulas)} 个):")
        for f in valid_formulas:
            print(f"    [{f['id']}] {f['readable']}")
            if f['explanation']:
                print(f"         解析: {f['explanation']}")
    else:
        print(f"\n  ▸ 无有效公式")

    if binary_only:
        print(f"  ▸ 纯二进制参数: {len(binary_only)} 个")

    # 原始文本段列表
    all_segments = []
    for f in result['formulas']:
        all_segments.extend(f['segments'])
    if all_segments:
        print(f"  ▸ 文本段总计: {len(all_segments)} 个")


def decode_formulas_run():
    """原 decode_formulas.py 的 main 逻辑：处理 dzhpool 下所有 XML 文件。"""
    # 修复 Windows GBK 编码问题
    if sys.stdout.encoding and sys.stdout.encoding.upper() in ('GBK', 'GB2312'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dzh_dir = os.path.join(base_dir, 'dzhpool')

    print("=" * 80)
    print("  DZH 大智慧股票池 Type 201 公式解码器 v3")
    print("  处理全部 XML 文件 + 精准公式提取")
    print("=" * 80)

    # 收集所有 XML 文件
    xml_files = sorted([
        os.path.join(dzh_dir, f) for f in os.listdir(dzh_dir)
        if f.endswith('.xml') and os.path.isfile(os.path.join(dzh_dir, f))
    ])

    print(f"\n  找到 {len(xml_files)} 个 XML 文件\n")

    all_results = []
    total_formulas = 0
    total_valid = 0

    for filepath in xml_files:
        try:
            result = decode_formulas_process_xml(filepath, verbose=True)
            all_results.append(result)
            total_formulas += result['total']
            total_valid += sum(1 for f in result['formulas'] if f['has_valid_formula'])
        except Exception as e:
            print(f"\n  ⚠ [{os.path.basename(filepath)}] 处理异常: {e}")

    # 汇总统计
    print(f"\n{'='*80}")
    print(f"  汇总统计")
    print(f"{'='*80}")
    print(f"  XML 文件总数: {len(xml_files)}")
    print(f"  type=201 公式单元总数: {total_formulas}")
    print(f"  有效公式总数: {total_valid}")
    print(f"  纯二进制参数(无文本): {total_formulas - total_valid}")

    # 所有有效公式列表
    all_valid = []
    for r in all_results:
        if 'formulas' in r:
            for f in r['formulas']:
                if f['has_valid_formula']:
                    all_valid.append((r['file'], f))

    if all_valid:
        print(f"\n  {'='*60}")
        print(f"  全部有效公式清单 ({len(all_valid)} 个)")
        print(f"  {'='*60}")
        for fname, f in all_valid:
            label = f' [{f["label"]}]' if f['label'] else ''
            print(f"    [{fname}] #{f['id']} ({f['inditype']}): {f['readable']}{label}")

    # 统计无公式的文件
    no_formula_files = [r for r in all_results if r['total'] > 0 and
                        not any(f['has_valid_formula'] for f in r.get('formulas', []))]
    if no_formula_files:
        print(f"\n  [!] 有 type=201 单元但无有效公式的文件: {len(no_formula_files)} 个")
        for r in no_formula_files[:10]:
            print(f"    - {r['file']} ({r['total']} 个二进制参数)")

    # 指标代码使用统计
    code_counter = Counter()
    for _, f in all_valid:
        m = re.match(r'^([a-zA-Z]{2,3})', f['readable'])
        if m:
            code = m.group(1)
            code_counter[code] += 1
    if code_counter:
        print(f"\n  指标代码使用统计:")
        for code, cnt in code_counter.most_common(10):
            known = DECODE_FORMULAS_DZH_INDICATOR_MAP.get(code, '?')
            print(f"    {code} ({known}): {cnt} 次")

    print(f"\n{'='*80}")
    print(f"  解码完成!")
    print(f"  DZH 指标代码表支持 {len(DECODE_FORMULAS_DZH_INDICATOR_MAP)} 个代码翻译")
    print(f"{'='*80}\n")


# ════════════════════════════════════════════════════════════════════
# 4. debug_formula - DZH 公式解码调试
# ════════════════════════════════════════════════════════════════════

def debug_formula_analyze_indi(xml_path):
    """分析指定 XML 中所有 type=201 单元的 indi 二进制数据。"""
    with open(xml_path, 'r', encoding='gbk') as f:
        content = f.read()
    content = re.sub(r'<\?xml[^>]+\?>', '', content)
    content = '<?xml version="1.0" encoding="utf-8"?>\n' + content
    root = ET.fromstring(content.encode('utf-8'))

    cells_container = root.find('cells')
    if cells_container is not None:
        for i, cell in enumerate(cells_container):
            typ = cell.get('type', '')
            indi = cell.get('indi', '')
            if typ == '201':
                label = cell.get('text', '')
                print(f"\n{'='*80}")
                print(f"Cell[{i}]: type={typ}, id={cell.get('id')}, text={label}")
                _debug_formula_analyze_binary(indi)
    else:
        print("No <cells> container found")


def _debug_formula_analyze_binary(indi_b64):
    if not indi_b64 or indi_b64 == "0;":
        print("  Empty indi")
        return
    try:
        raw = base64.b64decode(indi_b64)
    except Exception as e:
        print(f"  Base64 decode error: {e}")
        return
    print(f"  Binary length: {len(raw)} bytes")
    for i in range(0, len(raw), 32):
        hex_part = ' '.join(f'{b:02x}' for b in raw[i:i+32])
        ascii_part = ''.join(chr(b) if 0x20 <= b <= 0x7E else '.' for b in raw[i:i+32])
        print(f"  {i:04x}: {hex_part:<96s} {ascii_part}")

    # Find all ASCII segments
    segments = []
    j = 0
    while j < len(raw):
        if 0x20 <= raw[j] <= 0x7E:
            start = j
            while j < len(raw) and 0x20 <= raw[j] <= 0x7E:
                j += 1
            text = raw[start:j].decode('ascii', errors='replace')
            segments.append((start, j, text))
        else:
            j += 1

    print(f"\n  ASCII segments: {len(segments)}")
    for idx, (s, e, t) in enumerate(segments):
        print(f"    [{idx}] pos={s}-{e}, len={len(t)}: {repr(t[:200])}")

    print(f"\n  Current decoder result: {repr(_debug_formula_decode_dzh_formula_text(indi_b64))}")


def _debug_formula_decode_dzh_formula_text(indi_b64):
    if not indi_b64 or indi_b64 == "0;":
        return ""
    try:
        raw = base64.b64decode(indi_b64)
    except Exception:
        return ""
    if not raw:
        return ""
    tail = raw[-64:] if len(raw) > 64 else raw
    for end_pos in range(len(tail) - 1, -1, -1):
        if tail[end_pos] == 0x3B:
            null_terminated = (end_pos + 1 < len(tail) and tail[end_pos + 1] == 0x00)
            crlf_terminated = (end_pos + 2 < len(tail) and tail[end_pos:end_pos+3] == b';\r\n')
            if null_terminated or crlf_terminated:
                seg_start = end_pos
                while seg_start > 0 and 0x20 <= tail[seg_start - 1] <= 0x7E:
                    seg_start -= 1
                candidate = tail[seg_start:end_pos + 1].decode('ascii', errors='replace')
                candidate = re.sub(r'^[^a-zA-Z0-9_\(]+', '', candidate)
                if candidate:
                    return candidate
    segments = []
    i = 0
    while i < len(raw):
        if 0x20 <= raw[i] <= 0x7E:
            start = i
            while i < len(raw) and 0x20 <= raw[i] <= 0x7E:
                i += 1
            segments.append((start, i, raw[start:i].decode('ascii', errors='replace')))
        else:
            i += 1
    if segments:
        for start, end, text in reversed(segments):
            if len(text) >= 4 and text.rstrip().endswith(';'):
                return text.rstrip('; \r\n\t\0')
        longest = max(segments, key=lambda s: len(s[2]))
        if longest[2] and len(longest[2]) >= 3:
            return longest[2].rstrip('; \r\n\t\0')
    return ""


# ════════════════════════════════════════════════════════════════════
# 5. merge_config_tables - 配置表收敛（Task 11）
# ════════════════════════════════════════════════════════════════════
#
# 将 meta_core/config/ 下职责重叠的旧表合并到 30 张目标核心引擎配置表中，
# 原表保留在原位置以确保向后兼容与审计追溯，同时把被合并的旧表复制到
# config/_archived/ 作为归档。
#
# 合并策略：在目标表中新增以旧表 stem 命名的顶层键，旧表完整内容作为该键的值，
# 因此不会覆盖目标表已有字段，也不会丢失旧表语义。

MERGE_CONFIG_TABLES_CONFIG_DIR = Path(__file__).parent.parent / "config"
MERGE_CONFIG_TABLES_ARCHIVE_DIR = MERGE_CONFIG_TABLES_CONFIG_DIR / "_archived"

# 30 张目标核心引擎配置表（按 execute-architecture-sigration Task 11 规格）
MERGE_CONFIG_TABLES_TARGET_TABLES: set = {
    "timing.json",
    "edge_strategies.json",
    "dispatch.json",
    "engines.json",
    "modules.json",
    "tdx_psatt.json",
    "fallback_chain.json",
    "runtime_modes.json",
    "time_sources.json",
    "data_sources.json",
    "trade_interfaces.json",
    "side_effect_scopes.json",
    "post_tick_pipeline.json",
    "pre_tick_pipeline.json",
    "edge_semantics.json",
    "capability_registry.json",
    "pk_config.json",
    "analysis_config.json",
    "dashboard_schema.json",
    "alert_rules.json",
    "event_rules.json",
    "signal_rules.json",
    "pool_roles.json",
    "action_table.json",
    "cell_type_registry.json",
    "dzh_type_map.json",
    "defaults.json",
    "field_definitions.json",
    "xml_mapping.json",
    "data_config.json",
}

# 旧表 -> 目标表的合并映射。每个旧表会被归档到 config/_archived/。
MERGE_CONFIG_TABLES_MERGE_MAP: Dict[str, List[str]] = {
    "engines.json": [
        "formula_funcs.json",
        "formula_routing.json",
        "formula_modes.json",
        "builtin_formulas.json",
        "custom_formulas.json",
    ],
    "dispatch.json": [
        "tdx_system_indicators.json",
        "tdx_indicators.json",
        "tdx_ntjindexno_lookup.json",
        "tdx_indicator_formula_map.json",
        "tdx_noperate_rules.json",
    ],
    "action_table.json": [
        "behavior_actions.json",
        "filter_action_rules.json",
        "actions.json",
        "action_rules.json",
        "action_pipeline.json",
    ],
    "data_config.json": [
        "data_source_contract.json",
        "data_source_mappings.json",
        "data_source_routes.json",
        "data_providers.json",
        "mock_data.json",
        "mock_field_ranges.json",
        "data_mappings.json",
        "local_file_paths.json",
    ],
    "edge_strategies.json": [
        "flow_mode_registry.json",
        "flow_mode_rules.json",
        "topology.json",
        "topology_patterns.json",
    ],
    "field_definitions.json": [
        "fields.json",
        "column_definitions.json",
        "price_fields.json",
        "tdx_field_visibility.json",
    ],
    "dzh_type_map.json": [
        "dzh_cell_type_schema.json",
        "dzh_extra_fields.json",
        "dzh_market_mappings.json",
        "dzh_condition_fallback.json",
        "dzh_reload_schedule.json",
    ],
    "defaults.json": [
        "match_modes.json",
        "tdx_enums.json",
        "value_extractors.json",
    ],
    "pre_tick_pipeline.json": [
        "data_pipeline.json",
    ],
}


def _merge_config_tables_load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _merge_config_tables_save_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def merge_config_tables_merge() -> Dict[str, List[str]]:
    """执行合并并返回实际归档清单。"""
    MERGE_CONFIG_TABLES_ARCHIVE_DIR.mkdir(exist_ok=True)
    archived: Dict[str, List[str]] = {}

    for target_name, source_names in MERGE_CONFIG_TABLES_MERGE_MAP.items():
        target_path = MERGE_CONFIG_TABLES_CONFIG_DIR / target_name
        if not target_path.exists():
            # 目标表不存在时创建一个空字典容器
            target_data: Dict[str, Any] = {"version": "2.0"}
        else:
            target_data = _merge_config_tables_load_json(target_path)
            if not isinstance(target_data, dict):
                # 极少数目标表为列表时，包装为字典保留原内容
                target_data = {"_original": target_data, "version": "2.0"}

        merged_sources: List[str] = []
        for source_name in source_names:
            source_path = MERGE_CONFIG_TABLES_CONFIG_DIR / source_name
            if not source_path.exists():
                continue

            # 归档：复制到 _archived/，保留原位置
            archive_path = MERGE_CONFIG_TABLES_ARCHIVE_DIR / source_name
            shutil.copy2(source_path, archive_path)

            stem = Path(source_name).stem
            if stem in target_data:
                raise RuntimeError(
                    f"目标表 {target_name} 已存在键 {stem}，无法安全合并 {source_name}"
                )
            target_data[stem] = _merge_config_tables_load_json(source_path)
            merged_sources.append(source_name)

        if merged_sources:
            # 记录收敛来源，便于追踪
            target_data.setdefault("_convergence", {})
            target_data["_convergence"]["merged_from"] = merged_sources
            target_data["_convergence"]["target_count"] = len(MERGE_CONFIG_TABLES_TARGET_TABLES)
            _merge_config_tables_save_json(target_path, target_data)
            archived[target_name] = merged_sources

    return archived


def merge_config_tables_verify() -> None:
    """简单自校验：目标表存在且被合并旧表字段可找到。"""
    missing_targets = [t for t in MERGE_CONFIG_TABLES_TARGET_TABLES if not (MERGE_CONFIG_TABLES_CONFIG_DIR / t).exists()]
    if missing_targets:
        raise RuntimeError(f"缺失目标表: {missing_targets}")

    for target_name, source_names in MERGE_CONFIG_TABLES_MERGE_MAP.items():
        target_path = MERGE_CONFIG_TABLES_CONFIG_DIR / target_name
        target_data = _merge_config_tables_load_json(target_path)
        if not isinstance(target_data, dict):
            raise RuntimeError(f"目标表 {target_name} 不是字典，无法验证合并")
        for source_name in source_names:
            source_path = MERGE_CONFIG_TABLES_CONFIG_DIR / source_name
            if not source_path.exists():
                continue
            stem = Path(source_name).stem
            if stem not in target_data:
                raise RuntimeError(
                    f"目标表 {target_name} 缺少被合并旧表 {source_name} 的内容（键 {stem}）"
                )
            old_data = _merge_config_tables_load_json(source_path)
            if target_data[stem] != old_data:
                raise RuntimeError(f"目标表 {target_name}.{stem} 与原表 {source_name} 不一致")
            archive_path = MERGE_CONFIG_TABLES_ARCHIVE_DIR / source_name
            if not archive_path.exists():
                raise RuntimeError(f"旧表 {source_name} 未归档到 {MERGE_CONFIG_TABLES_ARCHIVE_DIR}")

    print("配置表合并校验通过")


def merge_config_tables_run():
    """原 merge_config_tables.py 的 __main__ 逻辑：执行合并并校验。"""
    archived = merge_config_tables_merge()
    print("已归档的旧表：")
    for target, sources in archived.items():
        print(f"  {target} <- {sources}")
    merge_config_tables_verify()


# ════════════════════════════════════════════════════════════════════
# 6. xml_tools - XML 检查与注入工具（合并自 web/ 2个脚本）
# ════════════════════════════════════════════════════════════════════

def xml_tools_check_xml():
    """原 check_xml.py 的主逻辑：打印指定 id 的 cell 信息。"""
    with open(r'H:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\超赢7号.xml', 'r', encoding='gb2312', errors='replace') as f:
        content = f.read()

    root = ET.fromstring(content)
    cells = root.find('cells')
    for c in cells.findall('cell'):
        if c.get('id') in ('133', '160', '89', '107', '119', '123', '61', '165'):
            print(f"id={c.get('id')}, type={c.get('type')}, text={c.get('text')}")


def xml_tools_parse_xml_to_pool_data(xml_path):
    """原 inject_xml.py 的辅助函数：将 DZH XML 解析为 pool_data 结构。"""
    with open(xml_path, 'r', encoding='gb2312', errors='replace') as f:
        content = f.read()
    root = ET.fromstring(content)

    nodes = []
    edges = []

    # Parse cells (nodes)
    cells = root.find('cells')
    if cells is not None:
        for cell in cells.findall('cell'):
            node = {
                'id': cell.get('id'),
                'dzh_cell_type': int(cell.get('type', 0)),
                'label': cell.get('text', ''),
                'clr': int(cell.get('clr', -1)),
                'params': {}
            }
            # Copy all attributes to params
            for k, v in cell.attrib.items():
                if k not in ('id', 'type', 'text', 'clr', 'pos'):
                    if v.isdigit() or (v.startswith('-') and v[1:].isdigit()):
                        node['params'][k] = int(v)
                    else:
                        node['params'][k] = v
            # Normalize DZH XML attribute names to internal params names
            if 'hold' in node['params'] and 'hold_sec' not in node['params']:
                node['params']['hold_sec'] = node['params'].pop('hold')
            if 'reload' in node['params'] and 'reload_sec' not in node['params']:
                node['params']['reload_sec'] = node['params'].pop('reload')
            if 'interval' in node['params'] and 'interval_sec' not in node['params']:
                node['params']['interval_sec'] = node['params'].pop('interval')
            nodes.append(node)

    # Parse flows (edges)
    flows = root.find('flows')
    if flows is not None:
        for flow in flows.findall('flow'):
            edge = {
                'id': flow.get('from') + '_' + flow.get('to'),
                'source': {'node_id': flow.get('from')},
                'target': {'node_id': flow.get('to')},
                'params': {},
                'attr': {'attr': int(flow.get('attr', 0))}
            }
            for k, v in flow.attrib.items():
                if k not in ('from', 'to', 'attr', 'clr'):
                    if v.isdigit() or (v.startswith('-') and v[1:].isdigit()):
                        edge['params'][k] = int(v)
                    else:
                        edge['params'][k] = v
            edges.append(edge)

    pool_data = {
        'hasData': True,
        'data': {
            'nodes': nodes,
            'edges': edges,
            'pool_meta': {
                'type': 'tdx',
                'ver': '1.0',
                'mode': '1'
            }
        }
    }
    return pool_data


def xml_tools_inject_xml(xml_file, out_file='output.json'):
    """原 inject_xml.py 的 __main__ 逻辑：解析 XML 并写出 JSON。"""
    pool_data = xml_tools_parse_xml_to_pool_data(xml_file)
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(pool_data, f, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════════════════════
# 主入口：argparse 子命令调度
# ════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="dev_tools.py",
        description="开发工具集（合并自 analyze_dzh / config_tools / decode_formulas / "
                    "debug_formula / merge_config_tables / xml_tools 6 个脚本）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    # analyze_dzh: xml / xml2 / xml3 / tianji_bfs
    p_analyze = subparsers.add_parser(
        "analyze_dzh", help="DZH XML 分析工具集（合并自 doc/tempdzh/ 4个分析脚本）")
    sub_analyze = p_analyze.add_subparsers(dest="subcommand", required=True, metavar="<sub>")
    sub_analyze.add_parser("xml", help="运行 analyze_xml")
    sub_analyze.add_parser("xml2", help="运行 analyze_xml2")
    sub_analyze.add_parser("xml3", help="运行 analyze_xml3")
    sub_analyze.add_parser("tianji_bfs", help="运行 analyze_tianji_bfs")

    # config_tools: generate / validate
    p_config = subparsers.add_parser(
        "config_tools", help="配置生成与校验工具（合并自 config/ 2个脚本）")
    sub_config = p_config.add_subparsers(dest="subcommand", required=True, metavar="<sub>")
    sub_config.add_parser("generate", help="运行 generate_configs 生成 JSON 配置")
    sub_config.add_parser("validate", help="运行 validate_refs 校验引用")

    # decode_formulas: 无子命令
    subparsers.add_parser(
        "decode_formulas", help="DZH 大智慧股票池公式解码器 v3（处理 dzhpool 全部 XML）")

    # debug_formula: --xml-path
    p_debug = subparsers.add_parser(
        "debug_formula", help="DZH 公式解码调试")
    p_debug.add_argument(
        "--xml-path",
        default=r"h:\new_tdx_mock\PYPlugins\meta_core\dzhpool\超赢7号.xml",
        help="待调试的 XML 文件路径")

    # merge_config_tables: 无子命令
    subparsers.add_parser(
        "merge_config_tables", help="配置表收敛（Task 11）：合并旧表到 30 张目标核心配置")

    # xml_tools: check / inject
    p_xml = subparsers.add_parser(
        "xml_tools", help="XML 检查与注入工具（合并自 web/ 2个脚本）")
    sub_xml = p_xml.add_subparsers(dest="subcommand", required=True, metavar="<sub>")
    sub_xml.add_parser("check", help="运行 check_xml")
    p_inject = sub_xml.add_parser("inject", help="运行 inject_xml")
    p_inject.add_argument("xml_file", help="输入 XML 文件路径")
    p_inject.add_argument("out_file", nargs="?", default="output.json", help="输出 JSON 文件路径")

    args = parser.parse_args()

    if args.command == "analyze_dzh":
        if args.subcommand == "xml":
            analyze_dzh_analyze_xml()
        elif args.subcommand == "xml2":
            analyze_dzh_analyze_xml2()
        elif args.subcommand == "xml3":
            analyze_dzh_analyze_xml3()
        elif args.subcommand == "tianji_bfs":
            analyze_dzh_analyze_tianji_bfs()
    elif args.command == "config_tools":
        if args.subcommand == "generate":
            config_tools_generate_configs()
        elif args.subcommand == "validate":
            config_tools_validate_refs()
    elif args.command == "decode_formulas":
        decode_formulas_run()
    elif args.command == "debug_formula":
        debug_formula_analyze_indi(args.xml_path)
    elif args.command == "merge_config_tables":
        merge_config_tables_run()
    elif args.command == "xml_tools":
        if args.subcommand == "check":
            xml_tools_check_xml()
        elif args.subcommand == "inject":
            xml_tools_inject_xml(args.xml_file, args.out_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
