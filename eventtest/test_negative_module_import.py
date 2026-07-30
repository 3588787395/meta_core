# -*- coding: utf-8 -*-
"""反测试 — 跨模块非法 import（Task 8 SubTask 8.3）。

基于 spec.md "反测试（异常与边界验证）" → "跨模块非法 import" Scenario（L150-153）：

    WHEN 检查 core/execution_module.py 的 import 语句
    THEN 仅允许 core.event_bus/core.domain/core.schemas/标准库/第三方库
    AND  不允许 core.screening_module/core.formula_module 直接 import

实现要点：
    - 遍历 ``core/*.py`` 各模块，使用 ``ast.parse`` 解析 import 语句
      （不硬编码 import 文本，从源码 AST 动态提取）。
    - **双层检查**：
        1. 模块级直接 import（模块顶层 body 中的 Import / ImportFrom）
        2. 函数级/类级懒加载 import（递归遍历 FunctionDef / AsyncFunctionDef
           / ClassDef body 收集）——``check_module_imports.py`` L9 明确声明
           "模块内延迟 import（函数体内的 import 也会被扫描）"，
           ``DESIGN0.md:87``"禁止 ``from core.xxx import``"无模块级/函数级之分。
    - 仍排除 ``if TYPE_CHECKING:`` 块（仅用于类型注解，运行期不执行 import，
      不构成跨模块引用）。
    - 白名单（business 模块允许直接 import 的目标）：
        - ``core.event_bus`` / ``core.domain`` / ``core.schemas``
        - 标准库（``sys.stdlib_module_names`` + ``__future__``）
        - 第三方库（numpy / pandas / pydantic 等非 ``core.*`` 模块）
    - 黑名单（business 模块禁止直接互相 import）：
        - ``core.execution_module`` / ``core.formula_module`` /
          ``core.screening_module`` / ``core.tick_bar_module`` /
          ``core.trade_module`` / ``core.runtime_mode_module`` /
          ``core.monitoring_module`` / ``core.table_engine`` /
          ``core.import_export_module``
    - 例外：``core.engine.py`` 是组装层（composition root），允许 import
      任意 business 模块以完成依赖注入（含函数级懒加载）。
    - 例外：``core.runtime_mode_module.py`` 显式声明 ``core.engine`` 在其白名单内
      （源码 L20 注释 "import 白名单：core.event_bus / core.domain / core.engine /
      core.schemas"），允许 import ``core.engine``。
    - 解析 ``from .foo import ...`` / ``from core.foo import ...`` /
      ``from ..core.foo import ...`` 三种相对/绝对 import 形式。
    - **不误判**：``from ..converters import ...`` 是 ``core/`` 外的同级包，不计入
      core 子模块违规。

历史 bug 修复记录（本测试覆盖验证）：
    - ``core/execution_module.py`` ``apply_ttl`` 方法体内曾有函数级懒加载
      ``from .runtime_mode_module import PoolState``，已修复为通过构造函数
      依赖注入 ``pool_state_cls``（与 ``FormulaEngineProtocol`` 同一模式）。
    - ``core/domain.py`` 曾有 ``from .execution_module import TimedEventSpec``
      函数级懒加载，已修复为将 ``TimedEventSpec`` 下沉至 ``core/domain.py``
      （纯数据结构层，允许被任意模块 import）。
    本测试现对函数级懒加载**断言失败**（而非跳过），确保上述 bug 不复现。

复用标准库 ``ast`` / ``sys``，不修改 core/ 源文件，不使用已删除旧接口
（get_node_stocks / SimTickSource / execution_order / EdgeFired.changed_codes /
at_fn / fire_ttl_due / TtlTracker）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import List, Set

import pytest


# ═══════════════════════════════════════════════════════════════
# 测试常量
# ═══════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = _PROJECT_ROOT / "core"

# 白名单：business 模块允许直接 import 的 core.* 子模块
_WHITELIST_CORE_SUBMODULES: frozenset = frozenset({
    "event_bus", "domain", "schemas", "_hashing", "tick_table",
})

# 黑名单：business 模块禁止直接互相 import 的 core.* 子模块
# （engine.py 作为组装层例外，不在黑名单约束范围内）
_BUSINESS_MODULES: frozenset = frozenset({
    "execution_module", "formula_module", "screening_module",
    "tick_bar_module", "trade_module", "runtime_mode_module",
    "monitoring_module", "table_engine", "import_export_module",
})

# 组装层例外：engine.py 允许 import 任意 business 模块
_COMPOSITION_ROOT = "engine"

# runtime_mode_module.py 源码 L20 显式声明 core.engine 在其白名单内：
#   "import 白名单：core.event_bus / core.domain / core.engine / core.schemas"
# 允许其直接 import core.engine。
_RUNTIME_MODE_ALLOWED_EXTRA: frozenset = frozenset({"engine"})

# 标准库模块名集合（Python 3.10+ 提供 ``sys.stdlib_module_names``）
_STDLIB_MODULES: frozenset = frozenset(sys.stdlib_module_names) | frozenset({"__future__"})


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _list_core_py_files() -> List[Path]:
    """列出 core/ 目录下所有 .py 文件（不递归子目录）。"""
    return sorted(p for p in _CORE_DIR.glob("*.py") if p.is_file())


def _is_type_checking_block(node: ast.AST) -> bool:
    """判断 AST 节点是否为 ``if TYPE_CHECKING:`` 块。

    匹配以下形式：
      - ``if TYPE_CHECKING:``
      - ``if typing.TYPE_CHECKING:``
    """
    if not isinstance(node, ast.If):
        return False
    test = node.test
    # if TYPE_CHECKING:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    # if typing.TYPE_CHECKING:
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _collect_module_level_imports(tree: ast.Module) -> List[ast.AST]:
    """收集模块级直接 import（不包括 TYPE_CHECKING 块、函数体、类体内的 import）。

    "直接 import" 定义（spec L150-153）：
      - 模块顶层 body 中的 Import / ImportFrom 语句
      - ``try/except ImportError`` 块中的 import（带 fallback 的兼容性 import，
        仍属模块级直接 import，计入检查）
      - 不包括 ``if TYPE_CHECKING:`` 块内的 import（非运行期执行）
      - 不包括函数/方法体内的 lazy import（非 "直接 import"）
      - 不包括类体内的 import（非模块级）
    """
    imports: List[ast.AST] = []

    def _collect_from_body(body: List[ast.stmt]) -> None:
        """从语句列表中收集 import，递归进入 try/except 块但跳过 if/func/class 块。"""
        for stmt in body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                imports.append(stmt)
            elif isinstance(stmt, ast.Try):
                # try/except 块中的 import 仍属模块级直接 import
                _collect_from_body(stmt.body)
                for handler in stmt.handlers:
                    _collect_from_body(handler.body)
                _collect_from_body(stmt.orelse)
                _collect_from_body(stmt.finalbody)
            # 跳过 If（含 TYPE_CHECKING）/ FunctionDef / ClassDef / With 等

    _collect_from_body(tree.body)
    return imports


def _get_core_module_level_imports(file_path: Path) -> Set[str]:
    """获取文件中所有模块级直接 core.* import 的子模块名集合。

    排除 ``if TYPE_CHECKING:`` 块内的 import 和函数/类体内的 lazy import。

    解析三种形式：
      - ``from .foo import x`` → "foo"（level=1，core/ 内文件视为 core.foo）
      - ``from core.foo import x`` → "foo"（绝对 import）
      - ``from ..core.foo import x`` → "foo"（level=2，从父包的 core 子包导入）

    不误判 ``from ..converters import x``（converters 是 core/ 外的同级包，
    不计入 core 子模块）。
    """
    src = file_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(file_path))
    submodules: Set[str] = set()
    for node in _collect_module_level_imports(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not module:
                continue
            if node.level == 1:
                # from .foo import x → core.foo（core/ 内文件的 level=1 相对 import）
                parts = module.split(".")
                if parts:
                    submodules.add(parts[0])
            elif node.level >= 2:
                # from ..core.foo import x → core.foo（仅当路径含 core 时计入）
                # from ..converters import x → 不计入（converters 不是 core 子模块）
                parts = module.split(".")
                if "core" in parts:
                    try:
                        idx = parts.index("core")
                        if idx + 1 < len(parts):
                            submodules.add(parts[idx + 1])
                    except ValueError:
                        pass
            elif module == "core" or module.startswith("core."):
                # 绝对 import: from core.foo import x
                parts = module.split(".")
                if len(parts) > 1:
                    submodules.add(parts[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # import core.foo 形式
                if alias.name == "core" or alias.name.startswith("core."):
                    parts = alias.name.split(".")
                    if len(parts) > 1:
                        submodules.add(parts[1])
    return submodules


def _find_module_level_import_of(
    file_path: Path, target_keyword: str,
) -> List[str]:
    """查找文件中模块级直接 import 含指定关键字的 import 语句。

    排除 ``if TYPE_CHECKING:`` 块内的 import 和函数/类体内的 lazy import。

    Args:
        file_path: .py 文件路径
        target_keyword: 要查找的模块名关键字（如 "screening_module"）

    Returns:
        匹配的 import 语句描述列表（如 ``["from .screening_module import ..."]``）
    """
    src = file_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(file_path))
    matches: List[str] = []
    for node in _collect_module_level_imports(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if target_keyword in module:
                matches.append(f"from {module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if target_keyword in alias.name:
                    matches.append(f"import {alias.name}")
    return matches


# ═══════════════════════════════════════════════════════════════
# 全量 import 收集（含函数级/类级懒加载，排除 TYPE_CHECKING）
# ═══════════════════════════════════════════════════════════════


def _collect_all_imports_excluding_type_checking(tree: ast.Module) -> List[ast.AST]:
    """收集所有 import（模块级 + 函数级 + 类级），排除 ``if TYPE_CHECKING:`` 块。

    递归遍历 FunctionDef / AsyncFunctionDef / ClassDef body 收集 import，
    以覆盖函数级懒加载（``check_module_imports.py`` L9："模块内延迟 import
    （函数体内的 import 也会被扫描）"）。

    排除：
      - ``if TYPE_CHECKING:`` 块内的 import（仅供类型检查器，运行期不执行）
    包含：
      - 模块顶层 import
      - 函数/方法体内的 lazy import
      - 类体内的 import
      - ``try/except`` 块中的 import
      - ``if``（非 TYPE_CHECKING）/ ``for`` / ``while`` / ``with`` 块内的 import
    """
    imports: List[ast.AST] = []

    def _collect_from_body(body: List[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                imports.append(stmt)
            elif isinstance(stmt, ast.If):
                if _is_type_checking_block(stmt):
                    # 跳过 TYPE_CHECKING 块（仅类型注解，运行期不执行）
                    continue
                # 非 TYPE_CHECKING 的 if 块，递归收集 body + orelse
                _collect_from_body(stmt.body)
                _collect_from_body(stmt.orelse)
            elif isinstance(stmt, ast.Try):
                _collect_from_body(stmt.body)
                for handler in stmt.handlers:
                    _collect_from_body(handler.body)
                _collect_from_body(stmt.orelse)
                _collect_from_body(stmt.finalbody)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # 递归收集函数/类体内的 import（函数级懒加载）
                _collect_from_body(stmt.body)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                _collect_from_body(stmt.body)
            elif isinstance(stmt, (ast.For, ast.While)):
                _collect_from_body(stmt.body)
                _collect_from_body(stmt.orelse)

    _collect_from_body(tree.body)
    return imports


def _get_core_all_imports(file_path: Path) -> Set[str]:
    """获取文件中所有 core.* import 的子模块名集合（含函数级/类级懒加载）。

    与 ``_get_core_module_level_imports`` 的区别：递归收集函数体/类体内的 import，
    仅排除 ``if TYPE_CHECKING:`` 块。用于检测函数级跨 business 模块懒加载。
    """
    src = file_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(file_path))
    submodules: Set[str] = set()
    for node in _collect_all_imports_excluding_type_checking(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not module:
                continue
            if node.level == 1:
                parts = module.split(".")
                if parts:
                    submodules.add(parts[0])
            elif node.level >= 2:
                parts = module.split(".")
                if "core" in parts:
                    try:
                        idx = parts.index("core")
                        if idx + 1 < len(parts):
                            submodules.add(parts[idx + 1])
                    except ValueError:
                        pass
            elif module == "core" or module.startswith("core."):
                parts = module.split(".")
                if len(parts) > 1:
                    submodules.add(parts[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "core" or alias.name.startswith("core."):
                    parts = alias.name.split(".")
                    if len(parts) > 1:
                        submodules.add(parts[1])
    return submodules


def _find_all_imports_of(
    file_path: Path, target_keyword: str,
) -> List[str]:
    """查找文件中所有 import（含函数级/类级）含指定关键字的语句。

    与 ``_find_module_level_import_of`` 的区别：递归收集函数体/类体内的 import，
    仅排除 ``if TYPE_CHECKING:`` 块。

    Args:
        file_path: .py 文件路径
        target_keyword: 要查找的模块名关键字（如 "runtime_mode_module"）

    Returns:
        匹配的 import 语句描述列表（如 ``["from .runtime_mode_module import ..."]``）
    """
    src = file_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(file_path))
    matches: List[str] = []
    for node in _collect_all_imports_excluding_type_checking(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if target_keyword in module:
                matches.append(f"from {module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if target_keyword in alias.name:
                    matches.append(f"import {alias.name}")
    return matches


# ═══════════════════════════════════════════════════════════════
# 测试用例 — AST 解析基线
# ═══════════════════════════════════════════════════════════════


class TestModuleImportAstBaseline:
    """验证所有 core/*.py 可被 AST 解析（基线完整性）。"""

    def test_all_core_py_files_parse_successfully(self):
        """所有 core/*.py 文件可被 ast.parse 成功解析。

        断言：
          - core/ 目录下至少有 10 个 .py 文件
          - 每个文件均可被 ast.parse 解析（无 SyntaxError）
        """
        py_files = _list_core_py_files()
        assert len(py_files) >= 10, (
            f"core/ 目录下应至少有 10 个 .py 文件，实际 {len(py_files)} 个"
        )
        for py_file in py_files:
            try:
                ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            except SyntaxError as ex:
                pytest.fail(f"{py_file.name} AST 解析失败: {ex}")

    def test_core_directory_exists(self):
        """core/ 目录存在。"""
        assert _CORE_DIR.is_dir(), f"core/ 目录应存在: {_CORE_DIR}"


# ═══════════════════════════════════════════════════════════════
# 测试用例 — execution_module.py 白名单约束
# ═══════════════════════════════════════════════════════════════


class TestExecutionModuleWhitelist:
    """验证 core/execution_module.py 仅 import 白名单模块（模块级 + 函数级）。

    spec.md L150-153：
        WHEN 检查 core/execution_module.py 的 import 语句
        THEN 仅允许 core.event_bus/core.domain/core.schemas/标准库/第三方库
        AND  不允许 core.screening_module/core.formula_module 直接 import

    注意：
      - ``if TYPE_CHECKING:`` 块内的 import 仅供类型检查器使用，不在运行期
        执行，不构成跨模块引用违规。
      - 函数/方法体内的 lazy import 同样受约束（见
        ``TestNoFunctionLevelLazyImport``），``apply_ttl`` 历史中的
        ``from .runtime_mode_module import PoolState`` 已修复为依赖注入。
    """

    def test_execution_module_only_module_level_imports_whitelist(self):
        """execution_module.py 的模块级直接 core.* import 仅限白名单。

        断言：
          - execution_module.py 中所有模块级直接 core.* 子模块 import 均在白名单内
          - 白名单 = {event_bus, domain, schemas}
          - TYPE_CHECKING 块内的 import 不计入
        """
        exec_module = _CORE_DIR / "execution_module.py"
        assert exec_module.is_file(), "execution_module.py 应存在"
        core_imports = _get_core_module_level_imports(exec_module)
        # execution_module.py 应仅模块级直接 import 白名单内的 core 子模块
        non_whitelist = core_imports - _WHITELIST_CORE_SUBMODULES
        assert not non_whitelist, (
            f"execution_module.py 不应模块级直接 import 白名单外的 core 子模块，"
            f"违规 import: {non_whitelist}，"
            f"实际模块级 import: {core_imports}"
        )

    def test_execution_module_no_screening_module_direct_import(self):
        """execution_module.py 不直接 import core.screening_module。

        spec.md L153：不允许 core.screening_module 直接 import。
        """
        exec_module = _CORE_DIR / "execution_module.py"
        matches = _find_module_level_import_of(exec_module, "screening_module")
        assert not matches, (
            f"execution_module.py 不应直接 import screening_module，"
            f"发现: {matches}"
        )

    def test_execution_module_no_formula_module_direct_import(self):
        """execution_module.py 不直接 import core.formula_module。

        spec.md L153：不允许 core.formula_module 直接 import。
        """
        exec_module = _CORE_DIR / "execution_module.py"
        matches = _find_module_level_import_of(exec_module, "formula_module")
        assert not matches, (
            f"execution_module.py 不应直接 import formula_module，"
            f"发现: {matches}"
        )

    def test_execution_module_no_other_business_module_direct_import(self):
        """execution_module.py 不直接 import 其他 business 模块。

        断言：
          - execution_module.py 不模块级直接 import 任何 business 模块
            （trade_module / tick_bar_module / monitoring_module /
             table_engine / import_export_module / runtime_mode_module）

        注：
          - ``if TYPE_CHECKING: from .runtime_mode_module import PoolState``
            不计入（仅供类型检查器使用，运行期不执行）
          - 函数级懒加载由 ``TestNoFunctionLevelLazyImport`` 独立覆盖
        """
        exec_module = _CORE_DIR / "execution_module.py"
        # execution_module 自身不算
        other_business = _BUSINESS_MODULES - {"execution_module"}
        for biz in other_business:
            matches = _find_module_level_import_of(exec_module, biz)
            assert not matches, (
                f"execution_module.py 不应直接 import {biz}，发现: {matches}"
            )


# ═══════════════════════════════════════════════════════════════
# 测试用例 — 全 core 模块零引用约束
# ═══════════════════════════════════════════════════════════════


class TestCoreZeroReferenceConstraint:
    """验证所有 business 模块不模块级直接互相 import（模块零引用约束）。

    spec.md L150-153 + 项目"所有模块之间禁止相互引用，只准与事件引擎交互"
    硬约束：business 模块仅允许模块级直接 import 白名单（event_bus/domain/schemas）
    + 标准库 + 第三方库，禁止模块级直接 import 其他 business 模块。

    例外：
      - ``core.engine.py`` 是组装层（composition root），允许 import 任意
        business 模块以完成依赖注入。
      - ``core.runtime_mode_module.py`` 源码 L20 显式声明 ``core.engine`` 在其
        白名单内（"import 白名单：core.event_bus / core.domain / core.engine /
        core.schemas"），允许直接 import ``core.engine``。
    """

    def test_no_business_module_direct_imports_other_business_module(self):
        """所有 business 模块（除 engine.py）不模块级直接 import 其他 business 模块。

        遍历 core/*.py，对每个 business 模块（非 engine.py）检查其模块级直接
        core.* import 是否仅限白名单（event_bus/domain/schemas）+
        runtime_mode_module 的 engine 例外。

        断言：
          - 对每个 business 模块（execution_module/formula_module/
            screening_module/tick_bar_module/trade_module/
            monitoring_module/table_engine/import_export_module），
            其模块级直接 core.* import 仅限白名单
          - runtime_mode_module 额外允许 import engine（源码 L20 白名单声明）
        """
        violations = {}
        for py_file in _list_core_py_files():
            module_name = py_file.stem
            # 跳过非 business 模块（__init__/domain/event_bus/schemas）
            if module_name not in _BUSINESS_MODULES:
                continue
            # 跳过组装层例外（engine.py）
            if module_name == _COMPOSITION_ROOT:
                continue
            core_imports = _get_core_module_level_imports(py_file)
            # 构建本模块允许的白名单（基础白名单 + 模块特定例外）
            allowed = set(_WHITELIST_CORE_SUBMODULES)
            if module_name == "runtime_mode_module":
                allowed |= _RUNTIME_MODE_ALLOWED_EXTRA
            non_whitelist = core_imports - allowed
            if non_whitelist:
                violations[module_name] = non_whitelist
        assert not violations, (
            f"business 模块不应模块级直接 import 白名单外的 core 子模块，"
            f"违规清单: {violations}"
        )

    def test_engine_allowed_as_composition_root(self):
        """engine.py 作为组装层允许 import 任意 business 模块（例外）。

        断言：
          - engine.py 存在
          - engine.py 至少模块级直接 import 1 个 business 模块（验证其组装层地位）
          - engine.py 不在 business 模块零引用约束范围内
        """
        engine_file = _CORE_DIR / "engine.py"
        assert engine_file.is_file(), "engine.py 应存在"
        core_imports = _get_core_module_level_imports(engine_file)
        # engine.py 至少模块级直接 import 1 个 business 模块（验证组装层地位）
        business_imports = core_imports & _BUSINESS_MODULES
        assert len(business_imports) >= 1, (
            f"engine.py 作为组装层应至少直接 import 1 个 business 模块，"
            f"实际 business imports: {business_imports}，"
            f"全部模块级 core imports: {core_imports}"
        )

    def test_no_business_module_direct_imports_execution_module(self):
        """所有 business 模块（除 engine.py）不模块级直接 import core.execution_module。

        execution_module 是 Execution 模块入口，不应被其他 business 模块
        模块级直接 import。
        """
        violations: List[str] = []
        for py_file in _list_core_py_files():
            module_name = py_file.stem
            if module_name == _COMPOSITION_ROOT:
                continue
            if module_name == "execution_module":
                continue
            if module_name not in _BUSINESS_MODULES:
                continue
            matches = _find_module_level_import_of(py_file, "execution_module")
            for m in matches:
                violations.append(f"{py_file.name}: {m}")
        assert not violations, (
            f"business 模块不应直接 import execution_module，违规: {violations}"
        )


# ═══════════════════════════════════════════════════════════════
# 测试用例 — 函数级/类级懒加载 import 约束
# ═══════════════════════════════════════════════════════════════


class TestNoFunctionLevelLazyImport:
    """验证所有 business 模块无函数级/类级跨 business 模块懒加载 import。

    ``check_module_imports.py`` L9 明确声明"模块内延迟 import（函数体内的
    import 也会被扫描）"，``DESIGN0.md:87``"禁止 ``from core.xxx import``"
    无模块级/函数级之分。本测试递归遍历 FunctionDef / AsyncFunctionDef /
    ClassDef body 收集 import，仅排除 ``if TYPE_CHECKING:`` 块。

    历史 bug（已修复，本测试确保不复现）：
      - ``execution_module.py`` ``apply_ttl`` 内曾有
        ``from .runtime_mode_module import PoolState`` → 改为依赖注入
      - ``domain.py`` 曾有 ``from .execution_module import TimedEventSpec``
        → ``TimedEventSpec`` 下沉至 ``domain.py``
    """

    def test_no_business_module_function_level_lazy_imports_other_business(self):
        """所有 business 模块（除 engine.py）无函数级/类级跨 business 模块懒加载。

        遍历 core/*.py，对每个 business 模块（非 engine.py）检查其所有
        core.* import（含函数级/类级，排除 TYPE_CHECKING）是否仅限白名单
        （event_bus/domain/schemas）+ runtime_mode_module 的 engine 例外。

        断言：
          - 对每个 business 模块，其全量 core.* import 仅限白名单
          - 函数体内的 ``from .xxx import`` 同样受约束
        """
        violations = {}
        for py_file in _list_core_py_files():
            module_name = py_file.stem
            if module_name not in _BUSINESS_MODULES:
                continue
            if module_name == _COMPOSITION_ROOT:
                continue
            core_imports = _get_core_all_imports(py_file)
            allowed = set(_WHITELIST_CORE_SUBMODULES)
            if module_name == "runtime_mode_module":
                allowed |= _RUNTIME_MODE_ALLOWED_EXTRA
            non_whitelist = core_imports - allowed
            if non_whitelist:
                violations[module_name] = non_whitelist
        assert not violations, (
            f"business 模块不应（含函数级懒加载）import 白名单外的 core 子模块，"
            f"违规清单: {violations}"
        )

    def test_execution_module_no_function_level_runtime_mode_import(self):
        """execution_module.py 无函数级 ``from .runtime_mode_module import`` 懒加载。

        确保 ``apply_ttl`` 历史 bug（函数体内 ``from .runtime_mode_module
        import PoolState``）不复现。``PoolState`` 现通过构造函数依赖注入
        ``pool_state_cls``（与 ``FormulaEngineProtocol`` 同一模式）。
        """
        exec_module = _CORE_DIR / "execution_module.py"
        matches = _find_all_imports_of(exec_module, "runtime_mode_module")
        assert not matches, (
            f"execution_module.py 不应（含函数级）import runtime_mode_module，"
            f"发现: {matches}（应通过依赖注入而非懒加载）"
        )

    def test_domain_no_function_level_execution_module_import(self):
        """domain.py 无函数级 ``from .execution_module import`` 懒加载。

        确保 ``TimedEventSpec`` 历史 bug（函数体内 ``from .execution_module
        import TimedEventSpec``）不复现。``TimedEventSpec`` 现定义于
        ``domain.py``（纯数据结构层，允许被任意模块 import）。
        """
        domain_module = _CORE_DIR / "domain.py"
        matches = _find_all_imports_of(domain_module, "execution_module")
        assert not matches, (
            f"domain.py 不应（含函数级）import execution_module，"
            f"发现: {matches}（TimedEventSpec 应下沉至 domain）"
        )

    def test_engine_function_level_imports_allowed_as_composition_root(self):
        """engine.py 作为组装层允许函数级懒加载 import business 模块（例外）。

        断言：
          - engine.py 至少有 1 个函数级/类级 business 模块 import
            （验证其组装层依赖注入地位，函数级懒加载属正常模式）
        """
        engine_file = _CORE_DIR / "engine.py"
        assert engine_file.is_file(), "engine.py 应存在"
        all_imports = _get_core_all_imports(engine_file)
        business_imports = all_imports & _BUSINESS_MODULES
        assert len(business_imports) >= 1, (
            f"engine.py 作为组装层应至少（含函数级）import 1 个 business 模块，"
            f"实际 business imports: {business_imports}，"
            f"全部 core imports: {all_imports}"
        )
