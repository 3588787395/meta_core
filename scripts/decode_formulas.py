"""
decode_formulas.py - DZH 大智慧股票池公式解码器 v3

核心改进:
  1. 基于二进制末尾特征 (;\0) 精准定位公式文本
  2. 全局扫描所有 type=201 单元的 possible patterns
  3. 处理 dzhpool 全部 XML 文件
  4. 输出结构化报告

已知局限:
  - type=201 的 indi 是公式筛选条件, 小部分可能是纯二进制参数
  - 文本段中若包含 GBK 编码的中文字符将无法提取
  - 极少数公式使用非 ;\0 结尾, 会降级到传统 ASCII 提取
"""
import os
import sys
import re
import base64
import xml.etree.ElementTree as ET
from collections import defaultdict, Counter

# 修复 Windows GBK 编码问题
if sys.stdout.encoding and sys.stdout.encoding.upper() in ('GBK', 'GB2312'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ═══════════════════════════════════════════════════════════════
# DZH 内部指标代码 → 可读名称翻译表
# ═══════════════════════════════════════════════════════════════
DZH_INDICATOR_MAP = {
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
DZH_FUNCTIONS = {
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
FORMULA_PATTERN = re.compile(
    r'^[a-zA-Z_][a-zA-Z0-9_]*'   # 变量/函数开头
    r'(\s*[\(\)\+\-\*\/\,\<\>\=\!\&\|\[\]\s\d\.\%\;\:]*)*$'
)


def read_xml(filepath):
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


def decode_xml_entities(text):
    """解码 XML 实体字符"""
    if not text:
        return text
    text = text.replace('&#xA;', '\n').replace('&#xa;', '\n').replace('&#10;', '\n')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&amp;', '&').replace('&quot;', '"')
    text = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    return text


def extract_text_segments(raw_bytes):
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


def try_gbk_decode(raw_bytes):
    """尝试 GBK 解码整个二进制数据"""
    try:
        return raw_bytes.decode('gbk', errors='replace')
    except:
        return None


def is_valid_formula(text):
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


def extract_formula_from_binary(raw_bytes):
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
                if not is_valid_formula(candidate):
                    for strip_n in range(1, min(4, len(candidate))):
                        test = candidate[strip_n:]
                        if test and is_valid_formula(test):
                            # 检查去掉的部分是否明显是噪声(单字符或非字母)
                            removed = candidate[:strip_n]
                            if len(removed) <= 2 and (not removed.isalpha() or len(removed) == 1):
                                stripped_candidate = test
                                break
                
                candidate = stripped_candidate
                if candidate and is_valid_formula(candidate):
                    formula_text = candidate
                    break

    # ── 策略2: 查找最后一段以 ; 结尾的长文本 ──
    if not formula_text:
        segments = extract_text_segments(raw_bytes)
        # 从后往前找，长度 >= 4 且以 ; 结尾的段
        for seg in reversed(segments):
            if seg['len'] >= 4 and seg['text'].rstrip().endswith(';'):
                candidate = seg['text'].rstrip('; \r\n\t\0')
                if candidate and is_valid_formula(candidate):
                    formula_text = candidate
                    break

    # ── 策略3: 合并策略 ──
    if not formula_text:
        segments = extract_text_segments(raw_bytes)
        # 找最后一段 >= 4 的文本
        for seg in reversed(segments):
            if seg['len'] >= 5 and seg['end'] >= len(raw_bytes) * 0.7:
                candidate = seg['text'].rstrip('; \r\n\t\0')
                if candidate and is_valid_formula(candidate):
                    formula_text = candidate
                    break
        
        # 找最长段
        if not formula_text and segments:
            longest = max(segments, key=lambda s: s['len'])
            if longest['len'] >= 3:
                candidate = longest['text'].rstrip('; \r\n\t\0')
                if candidate and is_valid_formula(candidate):
                    formula_text = candidate

    # ── 解码后的格式化输出 ──
    readable, explanation = format_formula_text(formula_text)
    return formula_text, readable, explanation


def format_formula_text(formula_text):
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
        known = DZH_INDICATOR_MAP.get(code)
        if known:
            display = known + text[len(code):]
            explanation_parts.append(f"指标: {code}→{known}")

    # 翻译函数名
    func_match = re.match(r'^([A-Z]+)\(', display)
    if func_match:
        fname = func_match.group(1)
        if fname in DZH_FUNCTIONS and fname not in ('MA',):  # MA 可能是指标也可能是函数
            pass  # 保持原样

    # 翻译操作符
    display = re.sub(r'<=', '≤', display)
    display = re.sub(r'>=', '≥', display)
    display = re.sub(r'!=', '≠', display)
    display = re.sub(r'(?<![<>])<(?!=)', '<', display)
    display = re.sub(r'(?<![<>])>(?!=)', '>', display)

    explanation = ' | '.join(explanation_parts) if explanation_parts else ""
    return display, explanation


def decode_formula(indi_b64):
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
    segments = extract_text_segments(raw)
    result['text_segments'] = segments

    # 尝试 GBK 解码
    gbk_text = try_gbk_decode(raw)
    if gbk_text:
        result['gbk_text'] = gbk_text[:200]  # 截取前 200 字符

    # 判断是否有实际文本内容
    meaningful = [s for s in segments if s['len'] >= 3]
    if meaningful:
        result['is_binary_only'] = False

    # 提取公式文本 (新方法)
    formula_text, readable, explanation = extract_formula_from_binary(raw)
    result['formula_text'] = formula_text
    result['readable'] = readable
    result['explanation'] = explanation
    result['has_valid_formula'] = bool(formula_text and is_valid_formula(formula_text))

    if not formula_text:
        result['readable'] = "[纯二进制参数，无可提取的公式文本]"

    return result


def process_xml(filepath, verbose=True):
    """处理单个 XML 文件，分析所有 DZH 公式"""
    filename = os.path.basename(filepath)
    
    try:
        xml_str = read_xml(filepath)
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
                    'text': decode_xml_entities(cell_elem.get('text', '')),
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
            decoded = decode_formula(cell['indi'])
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


def print_result(result):
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


def main():
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
            result = process_xml(filepath, verbose=True)
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
            known = DZH_INDICATOR_MAP.get(code, '?')
            print(f"    {code} ({known}): {cnt} 次")

    print(f"\n{'='*80}")
    print(f"  解码完成!")
    print(f"  DZH 指标代码表支持 {len(DZH_INDICATOR_MAP)} 个代码翻译")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()