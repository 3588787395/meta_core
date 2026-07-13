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
            table_name = fname[:-5]
            tables.append(table_name)
    return sorted(tables)


def get_all_code_files():
    code_files = []
    for root, dirs, files in os.walk(META_CORE_DIR):
        if "node_modules" in root or ".git" in root:
            continue
        for f in files:
            if f.endswith(".py") or f.endswith(".js"):
                code_files.append(os.path.join(root, f))
    return code_files


def get_all_json_files():
    json_files = []
    for root, dirs, files in os.walk(CONFIG_DIR):
        for f in files:
            if f.endswith(".json") and f not in SYSTEM_FILES:
                json_files.append(os.path.join(root, f))
    return json_files


def count_in_files(table_name, files):
    count = 0
    matches = []
    pattern = re.compile(re.escape(table_name))
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            for m in pattern.finditer(content):
                count += 1
                line_num = content[: m.start()].count("\n") + 1
                rel_path = os.path.relpath(fpath, META_CORE_DIR)
                matches.append({"file": rel_path, "line": line_num})
        except UnicodeDecodeError:
            try:
                with open(fpath, "r", encoding="gbk") as f:
                    content = f.read()
                for m in pattern.finditer(content):
                    count += 1
                    line_num = content[: m.start()].count("\n") + 1
                    rel_path = os.path.relpath(fpath, META_CORE_DIR)
                    matches.append({"file": rel_path, "line": line_num})
            except Exception:
                pass
    return count, matches


def find_indirect_references(table_name):
    """查找间接引用：通过其他配置表的 config_table 等字段引用"""
    findings = []
    
    json_files = get_all_json_files()
    for jf in json_files:
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
            rel_path = os.path.relpath(jf, META_CORE_DIR)
            
            def search_obj(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k == "config_table" and v == table_name:
                            findings.append({
                                "type": "config_table_ref",
                                "file": rel_path,
                                "path": path + "." + k,
                                "value": v
                            })
                        if isinstance(v, str) and v == table_name and k != "name":
                            findings.append({
                                "type": "value_ref",
                                "file": rel_path,
                                "path": path + "." + k,
                                "value": v
                            })
                        search_obj(v, path + "." + k)
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        search_obj(item, f"{path}[{i}]")
            
            search_obj(data)
        except Exception:
            pass
    
    return findings


def main():
    tables = get_table_names()
    print(f"总配置表数: {len(tables)}")
    
    code_files = get_all_code_files()
    print(f"代码文件数: {len(code_files)}")
    
    print("\n正在执行全量审计...")
    
    results = []
    for table in tables:
        code_count, code_matches = count_in_files(table, code_files)
        indirect = find_indirect_references(table)
        indirect_count = len(indirect)
        total_count = code_count + indirect_count
        
        results.append({
            "name": table,
            "code_refs": code_count,
            "indirect_refs": indirect_count,
            "total_refs": total_count,
            "code_matches": code_matches[:5],
            "indirect_matches": indirect
        })
    
    results.sort(key=lambda x: x["total_refs"])
    
    dead_tables = [t for t in results if t["total_refs"] == 0]
    suspected_dead = [t for t in results if 1 <= t["total_refs"] <= 2]
    active_tables = [t for t in results if t["total_refs"] >= 3]
    
    print(f"\n{'='*70}")
    print(f"死表（0次引用，含间接引用）：{len(dead_tables)} 张")
    print(f"{'='*70}")
    for t in dead_tables:
        print(f"  - {t['name']}")
    
    print(f"\n{'='*70}")
    print(f"疑似死表（1-2次引用，含间接引用）：{len(suspected_dead)} 张")
    print(f"{'='*70}")
    for t in suspected_dead:
        print(f"  - {t['name']} (代码:{t['code_refs']}次, 间接:{t['indirect_refs']}次)")
    
    print(f"\n{'='*70}")
    print(f"活跃表（3次以上引用，含间接引用）：{len(active_tables)} 张")
    print(f"{'='*70}")
    for t in active_tables:
        print(f"  - {t['name']} (总计:{t['total_refs']}次, 代码:{t['code_refs']}次, 间接:{t['indirect_refs']}次)")
    
    print(f"\n{'='*70}")
    print("死表详情与二次确认")
    print(f"{'='*70}")
    for t in dead_tables:
        print(f"\n【{t['name']}】")
        print(f"  代码引用: {t['code_refs']} 次")
        print(f"  间接引用: {t['indirect_refs']} 次")
        if t["indirect_matches"]:
            print(f"  间接引用详情:")
            for m in t["indirect_matches"]:
                print(f"    - [{m['type']}] {m['file']}{m['path']} = {m['value']}")
    
    output = {
        "total_tables": len(tables),
        "dead_tables": [t["name"] for t in dead_tables],
        "suspected_dead_tables": [{"name": t["name"], "code_refs": t["code_refs"], "indirect_refs": t["indirect_refs"], "total": t["total_refs"]} for t in suspected_dead],
        "active_tables": [{"name": t["name"], "code_refs": t["code_refs"], "indirect_refs": t["indirect_refs"], "total": t["total_refs"]} for t in active_tables],
        "all_tables": [{"name": t["name"], "code_refs": t["code_refs"], "indirect_refs": t["indirect_refs"], "total": t["total_refs"]} for t in results],
        "dead_table_details": []
    }
    
    for t in dead_tables:
        output["dead_table_details"].append({
            "name": t["name"],
            "code_refs": t["code_refs"],
            "indirect_refs": t["indirect_refs"],
            "indirect_matches": t["indirect_matches"]
        })
    
    with open(os.path.join(META_CORE_DIR, "audit_results_v2.json"), "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n详细结果已保存到 audit_results_v2.json")


if __name__ == "__main__":
    main()
