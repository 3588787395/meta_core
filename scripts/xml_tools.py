"""xml_tools.py - XML 检查与注入工具（合并自 web/ 2个脚本）。"""

import xml.etree.ElementTree as ET
import json
import argparse


def check_xml():
    """原 check_xml.py 的主逻辑：打印指定 id 的 cell 信息。"""
    with open(r'H:\new_tdx_mock\PYPlugins\meta_core\doc\tempdzh\综合设置\超赢7号.xml', 'r', encoding='gb2312', errors='replace') as f:
        content = f.read()

    root = ET.fromstring(content)
    cells = root.find('cells')
    for c in cells.findall('cell'):
        if c.get('id') in ('133', '160', '89', '107', '119', '123', '61', '165'):
            print(f"id={c.get('id')}, type={c.get('type')}, text={c.get('text')}")


def parse_xml_to_pool_data(xml_path):
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


def inject_xml(xml_file, out_file='output.json'):
    """原 inject_xml.py 的 __main__ 逻辑：解析 XML 并写出 JSON。"""
    pool_data = parse_xml_to_pool_data(xml_file)
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(pool_data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="XML 检查与注入工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="运行 check_xml")
    inject_parser = subparsers.add_parser("inject", help="运行 inject_xml")
    inject_parser.add_argument("xml_file", help="输入 XML 文件路径")
    inject_parser.add_argument("out_file", nargs="?", default="output.json", help="输出 JSON 文件路径")
    args = parser.parse_args()
    if args.command == "check":
        check_xml()
    elif args.command == "inject":
        inject_xml(args.xml_file, args.out_file)
