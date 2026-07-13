import os
import re
import json

CONFIG_DIR = r"h:\new_tdx_mock\PYPlugins\meta_core\config"
META_CORE_DIR = r"h:\new_tdx_mock\PYPlugins\meta_core"
SYSTEM_FILES = {".locks.json", "table_categories.json"}


def get_table_names():
    tables = []
    for fname in os.listdir(CONFIG_DIR):
        if fname.endswith(".json") and fname not in SYSTEM_FILES:
            table_name = fname[:-5]  # remove .json
            tables.append(table_name)
    return sorted(tables)


def get_code_files():
    code_files = []
    for root, dirs, files in os.walk(META_CORE_DIR):
        if "node_modules" in root or ".git" in root:
            continue
        for f in files:
            if f.endswith(".py") or f.endswith(".js"):
                code_files.append(os.path.join(root, f))
    return code_files


def count_references(table_name, code_files):
    count = 0
    matches = []
    pattern = re.compile(re.escape(table_name))
    for fpath in code_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            for m in pattern.finditer(content):
                count += 1
                line_num = content[: m.start()].count("\n") + 1
                matches.append({"file": fpath, "line": line_num})
        except UnicodeDecodeError:
            try:
                with open(fpath, "r", encoding="gbk") as f:
                    content = f.read()
                for m in pattern.finditer(content):
                    count += 1
                    line_num = content[: m.start()].count("\n") + 1
                    matches.append({"file": fpath, "line": line_num})
            except Exception:
                pass
    return count, matches


def secondary_check_dead_tables(dead_tables, code_files):
    results = {}
    for table in dead_tables:
        findings = []
        
        lower_pattern = re.compile(re.escape(table.lower()))
        upper_pattern = re.compile(re.escape(table.upper()))
        camel_pattern = re.compile(re.escape(table.replace("_", "").lower()))
        
        snake_variants = []
        parts = table.split("_")
        if len(parts) > 1:
            for i in range(len(parts) - 1):
                variant = "_".join(parts[:i+1]) + "_" + "_".join(parts[i+1:])
                snake_variants.append(variant)
        
        for fpath in code_files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
            except UnicodeDecodeError:
                try:
                    with open(fpath, "r", encoding="gbk") as f:
                        content = f.read()
                except Exception:
                    continue
            
            content_lower = content.lower()
            
            for pattern, ptype in [
                (lower_pattern, "case_insensitive"),
                (upper_pattern, "case_insensitive"),
            ]:
                for m in pattern.finditer(content):
                    line_num = content[: m.start()].count("\n") + 1
                    line_content = content.split("\n")[line_num - 1].strip()
                    findings.append({
                        "type": ptype,
                        "file": fpath,
                        "line": line_num,
                        "content": line_content[:200]
                    })
            
            if table.replace("_", "") in content.replace("_", "").lower():
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    if table.replace("_", "").lower() in line.replace("_", "").lower():
                        findings.append({
                            "type": "underscore_variant",
                            "file": fpath,
                            "line": i + 1,
                            "content": line.strip()[:200]
                        })
            
            string_concat_patterns = [
                re.compile(r'["\']' + re.escape(table[:len(table)//2]) + r'["\']\s*\+\s*["\']'),
                re.compile(r'["\']\s*\+\s*["\']' + re.escape(table[len(table)//2:]) + r'["\']'),
            ]
            for sc_pattern in string_concat_patterns:
                for m in sc_pattern.finditer(content):
                    line_num = content[: m.start()].count("\n") + 1
                    line_content = content.split("\n")[line_num - 1].strip()
                    findings.append({
                        "type": "string_concat_possible",
                        "file": fpath,
                        "line": line_num,
                        "content": line_content[:200]
                    })
        
        results[table] = findings
    return results


def main():
    print("=" * 60)
    print("Meta Core 配置表全量引用审计")
    print("=" * 60)
    
    tables = get_table_names()
    print(f"\n共发现 {len(tables)} 张配置表（排除系统文件）")
    
    code_files = get_code_files()
    print(f"共发现 {len(code_files)} 个代码文件（.py + .js）")
    
    print("\n正在统计引用次数...")
    table_stats = []
    for table in tables:
        count, matches = count_references(table, code_files)
        table_stats.append({"name": table, "count": count, "matches": matches})
    
    table_stats.sort(key=lambda x: x["count"])
    
    dead_tables = [t for t in table_stats if t["count"] == 0]
    suspected_dead = [t for t in table_stats if 1 <= t["count"] <= 2]
    active_tables = [t for t in table_stats if t["count"] >= 3]
    
    print(f"\n死表（0次引用）：{len(dead_tables)} 张")
    print(f"疑似死表（1-2次引用）：{len(suspected_dead)} 张")
    print(f"活跃表（3次以上引用）：{len(active_tables)} 张")
    
    print("\n" + "=" * 60)
    print("死表清单（0次引用）")
    print("=" * 60)
    for t in dead_tables:
        print(f"  - {t['name']}")
    
    print("\n" + "=" * 60)
    print("疑似死表清单（1-2次引用）")
    print("=" * 60)
    for t in suspected_dead:
        print(f"  - {t['name']} ({t['count']}次)")
    
    print("\n" + "=" * 60)
    print("活跃表清单（3次以上引用）")
    print("=" * 60)
    for t in active_tables:
        print(f"  - {t['name']} ({t['count']}次)")
    
    print("\n" + "=" * 60)
    print("对死表进行二次确认...")
    print("=" * 60)
    
    dead_table_names = [t["name"] for t in dead_tables]
    secondary_results = secondary_check_dead_tables(dead_table_names, code_files)
    
    for table, findings in secondary_results.items():
        if findings:
            print(f"\n{table}: 发现 {len(findings)} 个潜在匹配")
            unique_findings = []
            seen = set()
            for f in findings:
                key = (f["file"], f["line"], f["type"])
                if key not in seen:
                    seen.add(key)
                    unique_findings.append(f)
            for f in unique_findings[:10]:
                print(f"  [{f['type']}] {os.path.relpath(f['file'], META_CORE_DIR)}:{f['line']}")
                print(f"    {f['content']}")
        else:
            print(f"\n{table}: 未发现其他形式的引用")
    
    output = {
        "total_tables": len(tables),
        "total_code_files": len(code_files),
        "dead_tables": [t["name"] for t in dead_tables],
        "suspected_dead_tables": [{"name": t["name"], "count": t["count"]} for t in suspected_dead],
        "active_tables": [{"name": t["name"], "count": t["count"]} for t in active_tables],
        "table_stats": [{"name": t["name"], "count": t["count"]} for t in table_stats],
        "secondary_check": {k: len(v) for k, v in secondary_results.items()}
    }
    
    with open(os.path.join(META_CORE_DIR, "audit_results.json"), "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n详细结果已保存到 audit_results.json")
    
    return output


if __name__ == "__main__":
    main()
