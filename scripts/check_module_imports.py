"""模块零引用静态检查脚本。

扫描 core/、services/、converters/ 下所有 .py 文件的 import 语句，
断言除白名单外无其他跨模块 import。

支持：
- 绝对 import: ``from core.xxx import Yyy`` / ``import core.xxx``
- 相对 import: ``from .xxx import Yyy`` / ``from ..xxx import Yyy`` / ``from ...xxx import Yyy``
- 模块内延迟 import（函数体内的 import 也会被扫描）

白名单规则（spec.md §五）：
- ``core.event_bus`` / ``core.domain``（含子模块）/ ``core.schemas`` —— 任意模块可 import
- 8 个 aggregator module 文件允许 import 其聚合的子组件（模块内组合）
- 标准库 + 第三方库（fastapi/pydantic/numpy 等）不计入跨模块引用
"""
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# ════════════════════════════════════════════════════════════
# 白名单配置
# ════════════════════════════════════════════════════════════

# core 白名单前缀：core.event_bus / core.domain / core.schemas 及其子模块
# （core.domain.base / core.domain.specs 等子模块均允许，因 Domain 为纯数据模型）
WHITELIST_CORE_PREFIXES = {"core.event_bus", "core.domain", "core.schemas"}

# 全局白名单前缀：整个 native 包允许任意模块 import（原生工具层）
WHITELIST_GLOBAL_PREFIXES = {"native"}

# 特殊例外：aggregator module 允许 import 其聚合的子组件（模块内组合）
# key 为相对项目根的 POSIX 路径，value 为允许 import 的模块集合
MODULE_INTERNAL_WHITELIST: Dict[str, Set[str]] = {
    "core/tick_bar_module.py": {
        # SubTask 27.2: data_updater / bar_composer / minute_aggregator 已合并入本文件
        # tick_source 已在 SubTask 27.1 删除，TickSource/MockDataSource 由 core.domain 提供
    },
    "core/formula_module.py": {
        # SubTask 27.3: formula / formula_engine / formula_router 已合并入本文件
        # SubTask 28.4: services/formula_cache.py 已合并入本文件，无需跨层 import
    },
    "core/trade_module.py": {
        # SubTask 27.5: trade_executor / trading_service 已合并入本文件
    },
    "core/import_export_module.py": {
        "converters",
    },
    # SubTask 27.8: _safe_int / _safe_float / _hms_to_seconds / _decode_formula
    # 从 converters/_common.py 合并至 core/import_export_module.py，converters
    # 反向引用这些 helper（架构例外：helper 单一真相源位于聚合模块内）
    # SubTask 29.3: converters/ 包合并为单文件 converters.py
    "converters.py": {
        "core.import_export_module",
    },
    "core/runtime_mode_module.py": {
        "core.replay", "core.simulator",
    },
    "core/execution_module.py": {
        "core.compiler", "core.engine", "core.edge_executor", "core.time_util",
    },
    "core/monitoring_module.py": {
        # SubTask 27.6: event_panel / snapshot_builder 已合并入本文件
        # 原子组件 _EventPanel / _SnapshotBuilder 现为模块内私有类
    },
    "core/screening_module.py": {
        # SubTask 28.1: core/evaluators.py 已合并入本文件
        # 评估器层（_apply_noperate_mode / _extract_indicator_scalar 等）现为模块内私有
    },
}


# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════

def _file_to_package(file_path: Path, root: Path) -> str:
    """根据文件路径推导其所在的 Python 包名。

    例如：
        core/foo.py            -> "core"
        core/domain/foo.py     -> "core.domain"
        services/providers/foo.py -> "services.providers"
        converters/foo.py      -> "converters"
    """
    rel = file_path.relative_to(root)
    parts = rel.parts[:-1]  # 去掉文件名，保留目录
    return ".".join(parts)


def _resolve_relative(module: str, current_pkg: str) -> str:
    """将相对 import 模块路径解析为绝对模块路径。

    Python 相对 import 语义：
        from . import X     -> level=1, 当前包
        from .X import Y     -> level=1, 当前包.X
        from ..X import Y   -> level=2, 父包.X
        from ...X import Y  -> level=3, 祖父包.X

    例如：
        (".event_bus", "core")              -> "core.event_bus"
        ("..converters", "core")            -> "converters"
        ("...core.event_bus", "services.providers") -> "core.event_bus"
        (".", "core.domain")                -> "core.domain"
        ("._common", "services.providers")  -> "services.providers._common"
    """
    if not module.startswith("."):
        return module  # 绝对 import，直接返回

    # 计算相对层级（前导点数）
    level = 0
    for ch in module:
        if ch == ".":
            level += 1
        else:
            break

    # 去除前导点后的剩余部分
    rest = module[level:]

    # 当前包的层级
    pkg_parts = current_pkg.split(".") if current_pkg else []

    if level == 1:
        base_parts = pkg_parts[:]
    else:
        # level >= 2: 向上回溯 (level-1) 层
        up = level - 1
        if up >= len(pkg_parts):
            # 超出根包（例如 services/providers/tq.py 中 from ...core.event_bus）
            # 此时 base 为空，rest 即为顶层绝对模块
            base_parts = []
        else:
            base_parts = pkg_parts[: len(pkg_parts) - up]

    if rest:
        return ".".join(base_parts + [rest])
    else:
        return ".".join(base_parts)


def _is_whitelisted(module: str, extra_whitelist: Set[str]) -> bool:
    """检查模块是否在白名单中。

    规则：
        1. native 包及其子模块 -> 允许（全局白名单）
        2. core.event_bus / core.domain / core.schemas 及其子模块 -> 允许
        3. 在 extra_whitelist 中或为其子模块 -> 允许
    """
    # 检查全局白名单前缀（native 包）
    for prefix in WHITELIST_GLOBAL_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True

    # 检查 core 白名单前缀
    for prefix in WHITELIST_CORE_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True

    # 检查特殊白名单（精确匹配或前缀匹配，允许子模块）
    for allowed in extra_whitelist:
        if module == allowed or module.startswith(allowed + "."):
            return True

    return False


# ════════════════════════════════════════════════════════════
# 扫描逻辑
# ════════════════════════════════════════════════════════════

# 正则：匹配 from XXX import YYY（捕获模块名与导入名）
_RE_FROM_IMPORT = re.compile(r"^from\s+([\w\.]+)\s+import\s+(.+)")
# 正则：匹配 import XXX
_RE_IMPORT = re.compile(r"^import\s+([\w\.]+)")


def _parse_imported_names(import_part: str) -> List[str]:
    """从 ``from X import a, b as c, d`` 的导入部分提取模块名候选。

    对于 ``from core import schemas``，返回 ``["schemas"]``，
    用于组合 ``core.schemas`` 检查是否命中白名单（``from package import submodule``
    语义等价于 ``import package.submodule``）。
    """
    names: List[str] = []
    for token in import_part.split(","):
        token = token.strip()
        if not token or token == "*":
            continue
        # 取 as 前的名字
        name = token.split(" as ")[0].strip()
        if name:
            names.append(name)
    return names


def scan_file(file_path: Path, root: Path) -> List[Tuple[int, str, str]]:
    """扫描单个文件的 import 语句，返回违规列表 [(line_no, line, reason)]。

    扫描所有行（包括函数体内的延迟 import），将相对 import 解析为绝对模块路径后
    检查是否违反白名单规则。
    """
    violations: List[Tuple[int, str, str]] = []
    rel_path = str(file_path.relative_to(root)).replace("\\", "/")
    current_pkg = _file_to_package(file_path, root)
    extra_whitelist = MODULE_INTERNAL_WHITELIST.get(rel_path, set())

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line_no, raw_line in enumerate(f, 1):
                stripped = raw_line.strip()

                # 跳过注释行和空行
                if not stripped or stripped.startswith("#"):
                    continue

                # 匹配 from xxx import yyy
                m = _RE_FROM_IMPORT.match(stripped)
                if m:
                    raw_module = m.group(1)
                    module = _resolve_relative(raw_module, current_pkg)
                    imported_names = _parse_imported_names(m.group(2))
                    reason = _check_module(module, extra_whitelist, raw_module, current_pkg, imported_names)
                    if reason:
                        violations.append((line_no, stripped, reason))
                    continue

                # 匹配 import xxx
                m = _RE_IMPORT.match(stripped)
                if m:
                    raw_module = m.group(1)
                    module = _resolve_relative(raw_module, current_pkg)
                    reason = _check_module(module, extra_whitelist, raw_module, current_pkg)
                    if reason:
                        violations.append((line_no, stripped, reason))
    except (OSError, UnicodeDecodeError) as ex:
        violations.append((0, str(file_path), f"文件读取失败: {ex}"))

    return violations


def _check_module(module: str, extra_whitelist: Set[str], raw_module: str, current_pkg: str = "", imported_names: List[str] = None) -> str:
    """检查单个 import 模块是否违规，返回原因字符串；无违规返回空串。

    规则：
        1. native 包及其子模块 -> 允许（全局白名单）
        2. core.event_bus / core.domain / core.schemas 及其子模块 -> 允许（白名单）
        3. aggregator module 的 MODULE_INTERNAL_WHITELIST -> 允许
        4. ``from package import submodule`` 命中白名单 -> 允许
           （如 ``from ..core import schemas`` 等价于 ``import core.schemas``，
           core.schemas 在白名单内）
        5. 同包内 import（core→core / services→services / converters→converters /
           web→web / native→native）-> 允许（同模块内部组织，非跨层违规）
        6. core 层禁止直接 import services / web / converters（除特殊白名单外）
           （应通过 Protocol 接口注入或 EventBus 解耦）
        7. 其余跨层 import -> 违规
    """
    # 规则 1/2/3：白名单检查
    if _is_whitelisted(module, extra_whitelist):
        return ""

    # 规则 4：from package import submodule 命中白名单
    # （from ..core import schemas → module="core"，组合 "core.schemas" 检查白名单）
    if imported_names:
        all_whitelisted = True
        for name in imported_names:
            full = f"{module}.{name}" if module else name
            if not _is_whitelisted(full, extra_whitelist):
                all_whitelisted = False
                break
        if all_whitelisted:
            return ""

    # 所有已知顶层包
    TOP_PACKAGES = ("core", "services", "converters", "web", "native")

    # 规则 5：同包内 import 检查
    if current_pkg:
        current_top = current_pkg.split(".")[0]
        for top_pkg in TOP_PACKAGES:
            if module == top_pkg or module.startswith(top_pkg + "."):
                if top_pkg == current_top:
                    return ""
                else:
                    return f"违规 {top_pkg} import: {module}" + (f" (相对: {raw_module})" if raw_module != module else "")
                break

    # 规则 6/7：兜底跨层违规检测
    for top_pkg in TOP_PACKAGES:
        if module == top_pkg or module.startswith(top_pkg + "."):
            return f"违规 {top_pkg} import: {module}" + (f" (相对: {raw_module})" if raw_module != module else "")
    return ""


# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════

def main() -> int:
    """主入口：扫描 core/services 目录 + 根目录 converters.py，输出违规报告。

    Returns:
        0 表示全部通过，1 表示发现违规。
    """
    root = Path(__file__).parent.parent
    all_violations: Dict[str, List[str]] = {}

    for subdir in ["core", "services"]:
        dir_path = root / subdir
        if not dir_path.is_dir():
            continue
        for py_file in sorted(dir_path.rglob("*.py")):
            if py_file.name == "__init__.py":
                continue
            violations = scan_file(py_file, root)
            if not violations:
                continue
            rel = str(py_file.relative_to(root)).replace("\\", "/")
            entries = []
            for line_no, line, reason in violations:
                if line_no == 0:
                    entries.append(f"  [{reason}]")
                else:
                    entries.append(f"  L{line_no}: {reason}\n    {line}")
            all_violations[rel] = entries

    root_converters = root / "converters.py"
    if root_converters.is_file():
        violations = scan_file(root_converters, root)
        if violations:
            rel = "converters.py"
            entries = []
            for line_no, line, reason in violations:
                if line_no == 0:
                    entries.append(f"  [{reason}]")
                else:
                    entries.append(f"  L{line_no}: {reason}\n    {line}")
            all_violations[rel] = entries

    if all_violations:
        total = sum(len(v) for v in all_violations.values())
        print(f"❌ 发现 {total} 处违规（分布在 {len(all_violations)} 个文件中）:\n")
        for file_path in sorted(all_violations.keys()):
            entries = all_violations[file_path]
            print(f"── {file_path} ({len(entries)} 处) ──")
            for entry in entries:
                print(entry)
            print()
        return 1
    else:
        print("✅ 所有模块 import 语句符合白名单规则")
        return 0


if __name__ == "__main__":
    sys.exit(main())
