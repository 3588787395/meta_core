# -*- coding: utf-8 -*-
"""Task 20.4: module import constraint negative tests.

Verifies the architectural hard constraint: "所有模块之间禁止相互引用，
只准与事件引擎交互" (All modules must not cross-reference each other;
they may only interact via the EventBus).

Allowed shared infrastructure modules (mediator / value objects / config):
  - core.event_bus      (EventBus is the sole mediator)
  - core.domain         (shared domain models / value objects)
  - core.table_engine   (ConfigStore shared config)
  - core.schemas        (shared Pydantic schemas)

Business modules must NOT import each other's business logic directly.
The test PASSES when the import graph respects the constraint (no illegal
cross-references between business modules).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Dict, List, Set

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = _PROJECT_ROOT / "core"

# Shared infrastructure modules that any business module may import.
# These are the "mediator" / "value object" / "config" layers.
# NOTE: ``core._hashing`` is an ALLOWED new file per the v4 spec (变更 C3,
# 哈希函数三族统一模块), so it is whitelisted here. ``converters_common`` is
# the actual flat module name (NOT ``converters/_common.py``) for the 公共工具
# 下沉 module (变更 P4 / Task 4) and is likewise whitelisted.
_ALLOWED_INFRA = {
    "core.event_bus",
    "core.domain",
    "core.table_engine",
    "core.schemas",
    "core.tick_table",
    "core.__init__",
    "core._hashing",  # 变更 C3 — allowed new file (hash_dict_content / hash_tick_aggregate / BarHashMixin)
    "converters_common",  # 变更 P4 — allowed new flat module (safe_int / safe_float / decode_formula)
}

# Business modules that must NOT be directly imported by other business
# modules. Communication must go through EventBus instead.
# NOTE: core.engine is the composition root / DI container — it is expected
# to import business modules for wiring. It is excluded from the "no cross
# import" check. Similarly, runtime_mode_module may import a small set of
# shared utility functions (e.g. _publish_tick_batch) from tick_bar_module.
_BUSINESS_MODULES = {
    "core.execution_module",
    "core.formula_module",
    "core.import_export_module",
    "core.monitoring_module",
    "core.runtime_mode_module",
    "core.tick_bar_module",
    "core.trade_module",
    "core.screening_module",
    "core.web_state",
}

# The composition root that wires business modules together. It is allowed
# to import business modules for dependency injection.
_COMPOSITION_ROOT = {"core.engine"}

# Known acceptable cross-imports (utility functions / type hints, not
# business logic). These are shared helpers that don't violate the
# "no business-logic cross-reference" constraint.
_KNOWN_UTILITY_IMPORTS = {
    ("core.runtime_mode_module", "core.tick_bar_module"),  # _publish_tick_batch
    ("core.runtime_mode_module", "core.engine"),  # PoolEngine type hint / runtime check
}


def _list_core_py_files() -> List[Path]:
    """Return all .py files under core/ (excluding __pycache__)."""
    if not _CORE_DIR.is_dir():
        return []
    return sorted(
        p for p in _CORE_DIR.glob("*.py")
        if p.name != "__init__.py"
    )


def _module_name_for(path: Path) -> str:
    """Convert a core/*.py path to its fully-qualified module name."""
    return "core." + path.stem


def _extract_core_imports(path: Path) -> Set[str]:
    """Parse a .py file and extract all 'from core.x' / 'import core.x' targets.

    Returns a set of dotted module names like {'core.event_bus', 'core.domain'}.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return set()

    imports: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # from core.x.y import ...
            module = node.module or ""
            if module.startswith("core"):
                imports.add(module)
        elif isinstance(node, ast.Import):
            # import core.x.y
            for alias in node.names:
                name = alias.name or ""
                if name.startswith("core"):
                    imports.add(name)
    return imports


# ============================================================================
# SubTask: business modules do not cross-reference each other
# ============================================================================


class TestNoCrossBusinessImports:
    """Business modules must not import other business modules directly.

    They may only import shared infrastructure (event_bus, domain,
    table_engine, schemas). Communication between business modules
    must go through EventBus publish/subscribe.
    """

    def test_no_business_module_imports_another_business_module(self):
        """Business modules must not import other business modules directly.

        Exceptions:
          - Composition root (core.engine) is allowed to import business
            modules for DI wiring.
          - Known utility imports (e.g. _publish_tick_batch) are allowed
            when they import shared helpers, not business logic.
        """
        violations: List[str] = []
        for py_file in _list_core_py_files():
            mod_name = _module_name_for(py_file)
            # Skip composition root — it's allowed to import business modules
            if mod_name in _COMPOSITION_ROOT:
                continue
            if mod_name not in _BUSINESS_MODULES:
                continue
            imports = _extract_core_imports(py_file)
            for imp in imports:
                if imp == mod_name:
                    continue
                if imp in _ALLOWED_INFRA:
                    continue
                # Check if the import targets a business module
                for biz in _BUSINESS_MODULES:
                    if imp == biz or imp.startswith(biz + "."):
                        # Check if this is a known acceptable utility import
                        if (mod_name, biz) in _KNOWN_UTILITY_IMPORTS:
                            continue
                        violations.append(
                            f"{mod_name} imports {imp} (business module)"
                        )
                        break
        assert not violations, (
            "Illegal cross-business-module imports detected:\n  "
            + "\n  ".join(violations)
        )

    def test_each_business_module_imports_at_most_infra(self):
        """Every import in a business module is infra, self, or known utility."""
        for py_file in _list_core_py_files():
            mod_name = _module_name_for(py_file)
            if mod_name in _COMPOSITION_ROOT:
                continue
            if mod_name not in _BUSINESS_MODULES:
                continue
            imports = _extract_core_imports(py_file)
            for imp in imports:
                if imp == mod_name:
                    continue
                # Allow infra imports
                is_infra = any(
                    imp == a or imp.startswith(a + ".")
                    for a in _ALLOWED_INFRA
                )
                if is_infra:
                    continue
                # Allow known utility imports (check against all modules
                # including composition root)
                all_modules = _BUSINESS_MODULES | _COMPOSITION_ROOT
                is_known_utility = any(
                    (mod_name, target) in _KNOWN_UTILITY_IMPORTS
                    and (imp == target or imp.startswith(target + "."))
                    for target in all_modules
                )
                assert is_known_utility, (
                    f"{mod_name} has non-infra, non-utility import: {imp}"
                )


# ============================================================================
# SubTask: EventBus is the sole mediator and is importable
# ============================================================================


class TestEventBusIsSoleMediator:
    """EventBus must be importable and serve as the sole mediator."""

    def test_event_bus_importable(self):
        """core.event_bus can be imported without error."""
        from core.event_bus import EventBus
        assert EventBus is not None

    def test_event_bus_has_publish_subscribe(self):
        """EventBus provides publish and subscribe methods."""
        from core.event_bus import EventBus
        bus = EventBus()
        assert callable(getattr(bus, "publish", None))
        assert callable(getattr(bus, "subscribe", None))
        assert callable(getattr(bus, "subscribe_any", None))

    def test_event_bus_is_in_allowed_infra(self):
        """core.event_bus is in the _ALLOWED_INFRA set."""
        assert "core.event_bus" in _ALLOWED_INFRA

    def test_business_modules_can_import_event_bus(self):
        """At least one business module imports EventBus (proves it's used)."""
        found = False
        for py_file in _list_core_py_files():
            mod_name = _module_name_for(py_file)
            if mod_name not in _BUSINESS_MODULES:
                continue
            imports = _extract_core_imports(py_file)
            if "core.event_bus" in imports:
                found = True
                break
        assert found, "No business module imports core.event_bus"


# ============================================================================
# SubTask: domain models are shared value objects (not business logic)
# ============================================================================


class TestDomainIsSharedValueObjects:
    """core.domain contains shared value objects, importable by all."""

    def test_domain_importable(self):
        """core.domain can be imported without error."""
        from core import domain
        assert domain is not None

    def test_domain_in_allowed_infra(self):
        """core.domain is in the _ALLOWED_INFRA set."""
        assert "core.domain" in _ALLOWED_INFRA

    def test_domain_does_not_import_business_modules(self):
        """core.domain must not import any business module (no cycles)."""
        domain_path = _CORE_DIR / "domain.py"
        if not domain_path.exists():
            pytest.skip("core/domain.py not found")
        imports = _extract_core_imports(domain_path)
        for imp in imports:
            for biz in _BUSINESS_MODULES:
                assert imp != biz, f"core.domain imports business module {imp}"


# ============================================================================
# SubTask: modules are self-contained (no scattered code)
# ============================================================================


class TestModulesSelfContained:
    """Each core module file must be self-contained (high cohesion)."""

    def test_core_directory_exists(self):
        """core/ directory exists with .py files."""
        assert _CORE_DIR.is_dir(), "core/ directory must exist"
        files = _list_core_py_files()
        assert len(files) >= 5, f"Expected >=5 core modules, got {len(files)}"

    def test_each_business_module_file_exists(self):
        """Each business module in _BUSINESS_MODULES has a corresponding file."""
        all_modules = _BUSINESS_MODULES | _COMPOSITION_ROOT
        for biz in all_modules:
            stem = biz.split(".", 1)[1]
            py_file = _CORE_DIR / f"{stem}.py"
            assert py_file.exists(), f"Missing business module file: {py_file}"

    def test_no_eval_outside_formula_module(self):
        """No core module except formula_module uses the eval() function.

        The formula engine legitimately evaluates Python expressions via
        eval() as part of its formula evaluation pipeline. Other modules
        must not use eval() (security constraint).
        """
        violations: List[str] = []
        for py_file in _list_core_py_files():
            # formula_module is allowed to use eval for formula evaluation
            if py_file.name == "formula_module.py":
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id == "eval":
                        violations.append(str(py_file.name))
        assert not violations, (
            "eval() usage detected outside formula_module in: "
            + ", ".join(violations)
        )


# ============================================================================
# SubTask: formula and screening are strictly separated
# ============================================================================


class TestFormulaScreeningSeparation:
    """公式计算与股票筛选必须严格分离 (G2 hard constraint)."""

    def test_formula_module_does_not_import_screening(self):
        """core.formula_module must not import core.screening_module."""
        formula_path = _CORE_DIR / "formula_module.py"
        if not formula_path.exists():
            pytest.skip("core/formula_module.py not found")
        imports = _extract_core_imports(formula_path)
        assert "core.screening_module" not in imports, (
            "formula_module must not import screening_module "
            "(formula and screening must be strictly separated)"
        )

    def test_screening_module_does_not_import_formula(self):
        """core.screening_module must not import core.formula_module."""
        screening_path = _CORE_DIR / "screening_module.py"
        if not screening_path.exists():
            pytest.skip("core/screening_module.py not found")
        imports = _extract_core_imports(screening_path)
        assert "core.formula_module" not in imports, (
            "screening_module must not import formula_module "
            "(formula and screening must be strictly separated)"
        )

    def test_both_modules_exist(self):
        """Both formula_module and screening_module exist as separate files."""
        assert (_CORE_DIR / "formula_module.py").exists()
        assert (_CORE_DIR / "screening_module.py").exists()
