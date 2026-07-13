"""Debug script to analyze DZH formula decoding."""
import base64
import re as _re
import xml.etree.ElementTree as ET

def analyze_indi(xml_path):
    with open(xml_path, 'r', encoding='gbk') as f:
        content = f.read()
    content = _re.sub(r'<\?xml[^>]+\?>', '', content)
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
                _analyze_binary(indi)
    else:
        print("No <cells> container found")

def _analyze_binary(indi_b64):
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
    
    print(f"\n  Current decoder result: {repr(_decode_dzh_formula_text(indi_b64))}")

def _decode_dzh_formula_text(indi_b64):
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
                candidate = _re.sub(r'^[^a-zA-Z0-9_\(]+', '', candidate)
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

if __name__ == "__main__":
    xml_path = r"h:\new_tdx_mock\PYPlugins\meta_core\dzhpool\超赢7号.xml"
    analyze_indi(xml_path)