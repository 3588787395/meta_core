"""analyze_dzh.py - DZH XML 分析工具集（合并自 doc/tempdzh/ 4个分析脚本）。"""

import xml.etree.ElementTree as ET
import os
import argparse


def analyze_xml():
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


def analyze_xml2():
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


def analyze_xml3():
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


def analyze_tianji_bfs():
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DZH XML 分析工具集")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("xml", help="运行 analyze_xml")
    subparsers.add_parser("xml2", help="运行 analyze_xml2")
    subparsers.add_parser("xml3", help="运行 analyze_xml3")
    subparsers.add_parser("tianji_bfs", help="运行 analyze_tianji_bfs")
    args = parser.parse_args()
    if args.command == "xml":
        analyze_xml()
    elif args.command == "xml2":
        analyze_xml2()
    elif args.command == "xml3":
        analyze_xml3()
    elif args.command == "tianji_bfs":
        analyze_tianji_bfs()
