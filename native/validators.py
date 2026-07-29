"""validators.py - 配置校验与热重载模块（合并自 schema_validator / config_validator / table_loader / matchers）。

包含：
  - SchemaValidator: 三级校验（语法/逻辑/业务规则）验证所有配置表完整性
  - ConfigIntegrityValidator: 配置完整性校验器，全覆盖验证所有节点类型及属性
  - TableLoader: 热重载模块，监控 config/ 目录并在 JSON 文件变更时自动重载与验证
  - should_fire: Flow/Edge 时机判断（合并自 native/matchers.py）
  - TopologyPatternMatcher: 拓扑模式匹配器（合并自 native/matchers.py）

Usage:
    from native.validators import SchemaValidator, ConfigIntegrityValidator, TableLoader
    from native.validators import should_fire, TopologyPatternMatcher

    validator = SchemaValidator(config_dir, handler_registry)
    loader = TableLoader(config_dir, validator, engine_callback=on_reload)
    loader.reload_all()
    loader.start_watching()
"""

# === 匹配器层（自 native/matchers.py 合并）===

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from ..core.table_engine import get_global_config_store
except ImportError:
    from core.table_engine import get_global_config_store

logger = logging.getLogger(__name__)


def _get_table(filename):
    """通过 ConfigStore.get_table 加载配置表（Task 9.2 统一入口）。

    替代 SchemaValidator._load_json。空表返回 None（兼容历史 ``is None`` 语义）。
    filename 可带 .json 后缀（自动剥离），与 ConfigStore 的 stem key 对齐。
    """
    name = filename[:-5] if filename.endswith(".json") else filename
    cs = get_global_config_store()
    table = cs.get_table(name) if cs else {}
    return table if table else None


# ════════════════════════════════════════════════════════════════
# SchemaValidator - 验证所有配置表在启动和热重载时的完整性
# ════════════════════════════════════════════════════════════════
#
# 支持三级校验机制：
#   1. 语法校验层（SyntaxValidator）：JSON格式、必填字段、数据类型
#   2. 逻辑校验层（LogicValidator）：字段依赖、互斥条件、枚举值合法性
#   3. 业务规则校验层（BusinessValidator）：属性所有权一致性、handler引用完整性、类型映射完整性


# ──────────────────────────────────────────────────────────────
# 校验结果项
# ──────────────────────────────────────────────────────────────

class ValidationResult:
    """单条校验结果"""

    def __init__(self, level: str, file: str, entry: str, field: str,
                 message: str, suggestion: str = ""):
        self.level = level          # "error" / "warning" / "info"
        self.file = file
        self.entry = entry
        self.field = field
        self.message = message
        self.suggestion = suggestion

    def to_dict(self) -> dict:
        d = {
            "level": self.level,
            "file": self.file,
            "entry": self.entry,
            "field": self.field,
            "message": self.message,
        }
        # 兼容旧 API：同时提供 error/warning/info 键
        if self.level == "error":
            d["error"] = self.message
        elif self.level == "warning":
            d["warning"] = self.message
        elif self.level == "info":
            d["info"] = self.message
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d


# ──────────────────────────────────────────────────────────────
# 第一层：语法校验
# ──────────────────────────────────────────────────────────────

class SyntaxValidator:
    """语法校验层：JSON格式、必填字段、数据类型"""

    # 各配置表的必填顶层字段定义
    REQUIRED_SECTIONS = {
        "field_definitions.json": ["global_fields", "type_specific_fields", "flow_fields", "bit_fields"],
        "property_ownership.json": ["ownership", "type_ownership", "type_mapping", "rules"],
        "ui_components.json": ["components"],
        "behavior_actions.json": ["actions"],
        "data_source_mappings.json": ["mappings"],
        "cell_type_registry.json": ["types"],
        "ui_layouts.json": ["layouts"],
        "flow_mode_registry.json": ["resolve_rules"],
        "modules.json": ["modules"],
        "dispatch.json": ["dispatch_rules"],
    }

    # behavior_actions 中每个 action 的必填字段
    ACTION_REQUIRED_FIELDS = ("action_id", "module", "input_ports", "output_ports")

    # data_source_mappings 中每个 mapping 的必填字段
    MAPPING_REQUIRED_FIELDS = ("col_id", "display_name", "tq_field", "schema_field")

    def __init__(self, config_dir: Path):
        self._config_dir = config_dir
        self._results: List[ValidationResult] = []

    def validate_syntax(self, table_name: str, data: Any) -> List[ValidationResult]:
        """对指定表执行语法校验，返回校验结果列表"""
        self._results = []
        filename = f"{table_name}.json"

        # 1. JSON 格式校验（data 已解析，此处检查根元素类型）
        if not isinstance(data, dict):
            self._results.append(ValidationResult(
                "error", filename, "", "",
                f"根元素必须是dict，实际类型为 {type(data).__name__}",
                "请确保JSON文件顶层为对象 {}"
            ))
            return self._results

        # 2. 必填字段校验
        required = self.REQUIRED_SECTIONS.get(filename, [])
        missing_fields = []
        for field in required:
            if field not in data:
                self._results.append(ValidationResult(
                    "error", filename, "", field,
                    f"缺少必填字段: {field}",
                    f"请在 {filename} 中添加 '{field}' 字段"
                ))
                missing_fields.append(field)

        # 3. 数据类型校验（按表名分派）
        if filename == "behavior_actions.json":
            self._validate_action_types(data, filename)
        elif filename == "data_source_mappings.json":
            self._validate_mapping_types(data, filename)
        elif filename == "field_definitions.json":
            self._validate_field_def_types(data, filename)
        elif filename == "ui_components.json":
            self._validate_component_types(data, filename)
        elif filename == "property_ownership.json":
            self._validate_ownership_types(data, filename)
        elif filename == "cell_type_registry.json":
            self._validate_cell_type_registry_types(data, filename)
        elif filename == "ui_layouts.json":
            self._validate_ui_layouts_types(data, filename)
        elif filename == "flow_mode_registry.json":
            self._validate_flow_mode_registry_types(data, filename)
        elif filename == "modules.json":
            self._validate_modules_types(data, filename)
        elif filename == "dispatch.json":
            self._validate_dispatch_types(data, filename)

        # 4. 语法校验通过时记录 info 信息
        error_count = sum(1 for r in self._results if r.level == "error")
        if error_count == 0:
            if required:
                self._results.append(ValidationResult(
                    "info", filename, "", "",
                    f"语法校验通过，已校验 {len(required)} 个必填字段",
                    ""
                ))
            else:
                self._results.append(ValidationResult(
                    "info", filename, "", "",
                    "语法校验通过（无必填字段约束）",
                    ""
                ))

        return self._results

    def _validate_action_types(self, data: dict, filename: str):
        """校验 behavior_actions 中 action 的数据类型"""
        actions = data.get("actions", {})
        if not isinstance(actions, dict):
            self._results.append(ValidationResult(
                "error", filename, "", "actions",
                f"actions 字段必须是dict，实际类型为 {type(actions).__name__}",
                "请将 actions 定义为对象 {action_id: action_def}"
            ))
            return
        for action_id, action in actions.items():
            if not isinstance(action, dict):
                self._results.append(ValidationResult(
                    "error", filename, action_id, "",
                    f"action 定义必须是dict，实际类型为 {type(action).__name__}",
                    "请将每个 action 定义为对象"
                ))
                continue
            for req in self.ACTION_REQUIRED_FIELDS:
                if req not in action:
                    self._results.append(ValidationResult(
                        "error", filename, action_id, req,
                        f"缺少必填字段 '{req}'",
                        f"请在 action '{action_id}' 中添加 '{req}' 字段"
                    ))

    def _validate_mapping_types(self, data: dict, filename: str):
        """校验 data_source_mappings 中 mapping 的数据类型"""
        mappings = data.get("mappings", {})
        if not isinstance(mappings, dict):
            self._results.append(ValidationResult(
                "error", filename, "", "mappings",
                f"mappings 字段必须是dict，实际类型为 {type(mappings).__name__}",
                "请将 mappings 定义为对象"
            ))
            return
        for key, mapping in mappings.items():
            if not isinstance(mapping, dict):
                self._results.append(ValidationResult(
                    "error", filename, key, "",
                    f"mapping 定义必须是dict，实际类型为 {type(mapping).__name__}",
                    "请将每个 mapping 定义为对象"
                ))
                continue
            for req in self.MAPPING_REQUIRED_FIELDS:
                if req not in mapping:
                    self._results.append(ValidationResult(
                        "error", filename, key, req,
                        f"缺少必填字段 '{req}'",
                        f"请在 mapping '{key}' 中添加 '{req}' 字段"
                    ))

    def _validate_field_def_types(self, data: dict, filename: str):
        """校验 field_definitions 中字段定义的数据类型"""
        for section in ("global_fields", "flow_fields"):
            fields = data.get(section, {})
            if not isinstance(fields, dict):
                continue
            for fname, finfo in fields.items():
                if not isinstance(finfo, dict):
                    self._results.append(ValidationResult(
                        "error", filename, fname, section,
                        f"字段定义必须是dict，实际类型为 {type(finfo).__name__}",
                        "请将每个字段定义设为对象"
                    ))
        # applies_to_types 类型校验
        for fname, finfo in data.get("global_fields", {}).items():
            if not isinstance(finfo, dict):
                continue
            applies = finfo.get("applies_to_types", [])
            if isinstance(applies, list):
                for v in applies:
                    if not isinstance(v, int):
                        self._results.append(ValidationResult(
                            "error", filename, fname, "applies_to_types",
                            f"applies_to_types 值 '{v}' 不是有效的整数",
                            "请将 applies_to_types 中的值设为整数"
                        ))

    def _validate_component_types(self, data: dict, filename: str):
        """校验 ui_components 中组件定义的数据类型"""
        components = data.get("components", {})
        if not isinstance(components, dict):
            self._results.append(ValidationResult(
                "error", filename, "", "components",
                f"components 字段必须是dict，实际类型为 {type(components).__name__}",
                "请将 components 定义为对象"
            ))
            return
        for name, comp in components.items():
            if not isinstance(comp, dict):
                self._results.append(ValidationResult(
                    "error", filename, name, "",
                    f"组件定义必须是dict，实际类型为 {type(comp).__name__}",
                    "请将每个组件定义设为对象"
                ))
                continue
            if "component_type" not in comp:
                self._results.append(ValidationResult(
                    "error", filename, name, "component_type",
                    "缺少必填字段 'component_type'",
                    "请为组件添加 component_type 字段"
                ))

    def _validate_ownership_types(self, data: dict, filename: str):
        """校验 property_ownership 中数据类型"""
        rules = data.get("rules", {})
        if not isinstance(rules, dict):
            self._results.append(ValidationResult(
                "error", filename, "", "rules",
                f"rules 字段必须是dict，实际类型为 {type(rules).__name__}",
                "请将 rules 定义为对象"
            ))
        else:
            for rule_key in ("dzh_import", "tdx_import"):
                if rule_key in rules:
                    blocked = rules[rule_key].get("blocked_attrs", [])
                    if not isinstance(blocked, list):
                        self._results.append(ValidationResult(
                            "error", filename, rule_key, "blocked_attrs",
                            f"blocked_attrs 必须是list，实际类型为 {type(blocked).__name__}",
                            "请将 blocked_attrs 设为数组"
                        ))
        # 校验 ownership 结构
        ownership = data.get("ownership", {})
        if isinstance(ownership, dict):
            for pool_type in ("dzh", "tdx"):
                pool_data = ownership.get(pool_type, {})
                if not isinstance(pool_data, dict):
                    self._results.append(ValidationResult(
                        "error", filename, pool_type, "ownership",
                        f"ownership.{pool_type} 必须是dict，实际类型为 {type(pool_data).__name__}",
                        "请将 ownership 的子项定义为对象"
                    ))
        # 校验 type_ownership 结构
        type_ownership = data.get("type_ownership", {})
        if isinstance(type_ownership, dict):
            for type_key, type_info in type_ownership.items():
                if not isinstance(type_info, dict):
                    self._results.append(ValidationResult(
                        "error", filename, type_key, "type_ownership",
                        f"type_ownership.{type_key} 必须是dict，实际类型为 {type(type_info).__name__}",
                        "请将 type_ownership 的子项定义为对象"
                    ))

    def _validate_cell_type_registry_types(self, data: dict, filename: str):
        """校验 cell_type_registry 中数据类型"""
        types = data.get("types", {})
        if not isinstance(types, dict):
            self._results.append(ValidationResult(
                "error", filename, "", "types",
                f"types 字段必须是dict，实际类型为 {type(types).__name__}",
                "请将 types 定义为对象"
            ))
            return
        for type_key, type_info in types.items():
            if not isinstance(type_info, dict):
                self._results.append(ValidationResult(
                    "error", filename, type_key, "",
                    f"类型定义必须是dict，实际类型为 {type(type_info).__name__}",
                    "请将每个类型定义设为对象"
                ))
                continue
            if "pool_type" not in type_info:
                self._results.append(ValidationResult(
                    "error", filename, type_key, "pool_type",
                    "缺少必填字段 'pool_type'",
                    "请在类型定义中添加 pool_type 字段"
                ))

    def _validate_ui_layouts_types(self, data: dict, filename: str):
        """校验 ui_layouts 中数据类型"""
        layouts = data.get("layouts", {})
        if not isinstance(layouts, dict):
            self._results.append(ValidationResult(
                "error", filename, "", "layouts",
                f"layouts 字段必须是dict，实际类型为 {type(layouts).__name__}",
                "请将 layouts 定义为对象"
            ))
            return
        for layout_id, layout in layouts.items():
            if not isinstance(layout, dict):
                self._results.append(ValidationResult(
                    "error", filename, layout_id, "",
                    f"布局定义必须是dict，实际类型为 {type(layout).__name__}",
                    "请将每个布局定义设为对象"
                ))
                continue
            if "target_type" not in layout:
                self._results.append(ValidationResult(
                    "error", filename, layout_id, "target_type",
                    "缺少必填字段 'target_type'",
                    "请在布局定义中添加 target_type 字段"
                ))

    def _validate_flow_mode_registry_types(self, data: dict, filename: str):
        """校验 flow_mode_registry 中数据类型（attr_bits 已迁移至 field_definitions.bit_fields.flow
        单一真相源，此处仅校验 resolve_rules）"""
        resolve_rules = data.get("resolve_rules", [])
        if not isinstance(resolve_rules, list):
            self._results.append(ValidationResult(
                "error", filename, "", "resolve_rules",
                f"resolve_rules 必须是list，实际类型为 {type(resolve_rules).__name__}",
                "请将 resolve_rules 设为数组"
            ))

    def _validate_modules_types(self, data: dict, filename: str):
        """校验 modules 中数据类型"""
        modules = data.get("modules", {})
        if not isinstance(modules, dict):
            self._results.append(ValidationResult(
                "error", filename, "", "modules",
                f"modules 字段必须是dict，实际类型为 {type(modules).__name__}",
                "请将 modules 定义为对象"
            ))
            return
        for module_id, module in modules.items():
            if not isinstance(module, dict):
                self._results.append(ValidationResult(
                    "error", filename, module_id, "",
                    f"模块定义必须是dict，实际类型为 {type(module).__name__}",
                    "请将每个模块定义设为对象"
                ))
                continue
            if "handler" not in module:
                self._results.append(ValidationResult(
                    "error", filename, module_id, "handler",
                    "缺少必填字段 'handler'",
                    "请在模块定义中添加 handler 字段"
                ))

    def _validate_dispatch_types(self, data: dict, filename: str):
        """校验 dispatch 中数据类型"""
        dispatch_rules = data.get("dispatch_rules", {})
        if not isinstance(dispatch_rules, dict):
            self._results.append(ValidationResult(
                "error", filename, "", "dispatch_rules",
                f"dispatch_rules 字段必须是dict，实际类型为 {type(dispatch_rules).__name__}",
                "请将 dispatch_rules 定义为对象"
            ))
            return
        for rule_key, rule in dispatch_rules.items():
            if not isinstance(rule, dict):
                self._results.append(ValidationResult(
                    "error", filename, rule_key, "",
                    f"分发规则定义必须是dict，实际类型为 {type(rule).__name__}",
                    "请将每个分发规则设为对象"
                ))
                continue
            if "gateway" not in rule:
                self._results.append(ValidationResult(
                    "error", filename, rule_key, "gateway",
                    "缺少必填字段 'gateway'",
                    "请在分发规则中添加 gateway 字段"
                ))


# ──────────────────────────────────────────────────────────────
# 第二层：逻辑校验
# ──────────────────────────────────────────────────────────────

class LogicValidator:
    """逻辑校验层：字段依赖关系、互斥条件、枚举值合法性"""

    # Flow attr 中互斥的位标志组合
    FLOW_MUTEX_BITS = [
        ("bit0", "bit12", "delete_source 与 output_constituent 互斥"),
    ]

    def __init__(self, config_dir: Path):
        self._config_dir = config_dir
        self._results: List[ValidationResult] = []
        self._cache: Dict[str, Any] = {}

    def _load(self, filename: str) -> Optional[dict]:
        if filename in self._cache:
            return self._cache[filename]
        path = self._config_dir / filename
        if not path.exists():
            self._cache[filename] = None
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._cache[filename] = data
            return data
        except (json.JSONDecodeError, OSError):
            self._cache[filename] = None
            return None

    def validate_logic(self, table_name: str, data: Any) -> List[ValidationResult]:
        """对指定表执行逻辑校验，返回校验结果列表"""
        self._results = []
        filename = f"{table_name}.json"

        if filename == "behavior_actions.json":
            self._validate_action_dependencies(data, filename)
            self._validate_action_enums(data, filename)
        elif filename == "field_definitions.json":
            self._validate_bit_field_logic(data, filename)
        elif filename == "property_ownership.json":
            self._validate_type_mapping_consistency(data, filename)
        elif filename == "flow_mode_registry.json":
            self._validate_flow_mutex(data, filename)

        # 逻辑校验通过时记录 info 信息
        error_count = sum(1 for r in self._results if r.level == "error")
        warning_count = sum(1 for r in self._results if r.level == "warning")
        if error_count == 0:
            if warning_count == 0:
                self._results.append(ValidationResult(
                    "info", filename, "", "",
                    "逻辑校验通过，无错误和警告",
                    ""
                ))
            else:
                self._results.append(ValidationResult(
                    "info", filename, "", "",
                    f"逻辑校验通过，但有 {warning_count} 条警告",
                    ""
                ))

        return self._results

    def _validate_action_dependencies(self, data: dict, filename: str):
        """校验 behavior_actions 中 depends_on 引用的字段必须存在"""
        actions = data.get("actions", {})
        if not isinstance(actions, dict):
            return
        action_ids = set(actions.keys())
        for action_id, action in actions.items():
            if not isinstance(action, dict):
                continue
            depends = action.get("depends_on", [])
            if isinstance(depends, list):
                for dep in depends:
                    if dep not in action_ids:
                        self._results.append(ValidationResult(
                            "error", filename, action_id, "depends_on",
                            f"depends_on '{dep}' 引用的 action_id 不存在",
                            f"请确保 depends_on 中的值在 actions 中已定义"
                        ))

    def _validate_action_enums(self, data: dict, filename: str):
        """校验 behavior_actions 中枚举值的合法性"""
        actions = data.get("actions", {})
        if not isinstance(actions, dict):
            return
        valid_modules = {"condition", "source", "pool", "transfer", "schedule", "analysis", "highlight"}
        for action_id, action in actions.items():
            if not isinstance(action, dict):
                continue
            module = action.get("module", "")
            if module and module not in valid_modules:
                self._results.append(ValidationResult(
                    "warning", filename, action_id, "module",
                    f"module '{module}' 不在已知模块列表中",
                    f"已知模块: {', '.join(sorted(valid_modules))}"
                ))

    def _validate_bit_field_logic(self, data: dict, filename: str):
        """校验 bit_fields 中位标志的互斥条件"""
        bit_fields = data.get("bit_fields", {})
        if not isinstance(bit_fields, dict):
            return
        for type_key, bit_defs in bit_fields.items():
            if not isinstance(bit_defs, dict):
                continue
            # 检查同 parent_field 内的 bit_position 不重复
            parent_positions = {}
            for bit_name, bit_info in bit_defs.items():
                if not isinstance(bit_info, dict):
                    continue
                parent = bit_info.get("parent_field", "")
                bit_pos = bit_info.get("bit_position")
                if parent and bit_pos is not None:
                    if parent not in parent_positions:
                        parent_positions[parent] = {}
                    if bit_pos in parent_positions[parent]:
                        self._results.append(ValidationResult(
                            "error", filename, bit_name, "bit_position",
                            f"parent_field '{parent}' 中 bit_position {bit_pos} 重复"
                            f"（已被 '{parent_positions[parent][bit_pos]}' 使用）",
                            "请确保同一 parent_field 内的 bit_position 唯一"
                        ))
                    else:
                        parent_positions[parent][bit_pos] = bit_name

    def _validate_type_mapping_consistency(self, data: dict, filename: str):
        """校验 property_ownership 中 type_mapping 的双向一致性"""
        type_mapping = data.get("type_mapping", {})
        if not isinstance(type_mapping, dict):
            return
        dzh_to_tdx = type_mapping.get("dzh_to_tdx", {})
        tdx_to_dzh = type_mapping.get("tdx_to_dzh", {})
        if not isinstance(dzh_to_tdx, dict) or not isinstance(tdx_to_dzh, dict):
            return
        for dzh_type, tdx_type in dzh_to_tdx.items():
            if tdx_type not in tdx_to_dzh:
                self._results.append(ValidationResult(
                    "error", filename, dzh_type, "type_mapping",
                    f"dzh_to_tdx 映射 {dzh_type}->{tdx_type} 但无反向映射",
                    f"请在 tdx_to_dzh 中添加 {tdx_type} 的反向映射"
                ))

    def _validate_flow_mutex(self, data: dict, filename: str):
        """校验 Flow attr 中互斥的位标志组合"""
        attr_bits = data.get("attr_bits", {})
        if not isinstance(attr_bits, dict):
            return
        for bit1_name, bit2_name, desc in self.FLOW_MUTEX_BITS:
            if bit1_name in attr_bits and bit2_name in attr_bits:
                self._results.append(ValidationResult(
                    "warning", filename, "", "attr_bits",
                    f"互斥位标志: {desc} ({bit1_name} 与 {bit2_name} 不应同时存在)",
                    f"请确保运行时不会同时设置 {bit1_name} 和 {bit2_name}"
                ))


# ──────────────────────────────────────────────────────────────
# 第三层：业务规则校验
# ──────────────────────────────────────────────────────────────

class BusinessValidator:
    """业务规则校验层：属性所有权一致性、handler引用完整性、类型映射完整性"""

    def __init__(self, config_dir: Path, handler_registry: dict):
        self._config_dir = config_dir
        self._handler_registry = handler_registry
        self._results: List[ValidationResult] = []
        self._cache: Dict[str, Any] = {}

    def _load(self, filename: str) -> Optional[dict]:
        if filename in self._cache:
            return self._cache[filename]
        path = self._config_dir / filename
        if not path.exists():
            self._cache[filename] = None
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._cache[filename] = data
            return data
        except (json.JSONDecodeError, OSError):
            self._cache[filename] = None
            return None

    def validate_business(self, table_name: str, data: Any) -> List[ValidationResult]:
        """对指定表执行业务规则校验，返回校验结果列表"""
        self._results = []
        filename = f"{table_name}.json"

        if filename == "property_ownership.json":
            self._validate_ownership_field_consistency(data, filename)
            self._validate_type_mapping_completeness(data, filename)
        elif filename == "modules.json":
            self._validate_handler_integrity(data, filename)
        elif filename == "behavior_actions.json":
            self._validate_action_handler_callable(data, filename)
        elif filename == "cell_type_registry.json":
            self._validate_type_layout_coverage(data, filename)

        # 业务规则校验通过时记录 info 信息
        error_count = sum(1 for r in self._results if r.level == "error")
        warning_count = sum(1 for r in self._results if r.level == "warning")
        if error_count == 0:
            if warning_count == 0:
                self._results.append(ValidationResult(
                    "info", filename, "", "",
                    "业务规则校验通过，无错误和警告",
                    ""
                ))
            else:
                self._results.append(ValidationResult(
                    "info", filename, "", "",
                    f"业务规则校验通过，但有 {warning_count} 条警告",
                    ""
                ))
        elif warning_count > 0:
            self._results.append(ValidationResult(
                "info", filename, "", "",
                f"业务规则校验发现 {error_count} 个错误和 {warning_count} 个警告",
                "请修复错误以确保系统正常运行"
            ))

        return self._results

    def _validate_ownership_field_consistency(self, data: dict, filename: str):
        """校验 type_ownership 中列出的属性必须在 fields 定义中存在"""
        type_ownership = data.get("type_ownership", {})
        if not isinstance(type_ownership, dict):
            return

        # 加载 field_definitions 获取所有已知字段
        fd = self._load("field_definitions.json")
        all_field_names = set()
        if fd and isinstance(fd, dict):
            all_field_names.update(fd.get("global_fields", {}).keys())
            for fields in fd.get("type_specific_fields", {}).values():
                if isinstance(fields, dict):
                    all_field_names.update(fields.keys())
            all_field_names.update(fd.get("flow_fields", {}).keys())

        if not all_field_names:
            # 无法校验，跳过
            return

        for type_key, type_info in type_ownership.items():
            if not isinstance(type_info, dict):
                continue
            for pool_type in ("dzh", "tdx"):
                attrs = type_info.get(pool_type)
                if attrs is None:
                    continue
                if not isinstance(attrs, list):
                    continue
                for attr in attrs:
                    if attr not in all_field_names:
                        self._results.append(ValidationResult(
                            "warning", filename, type_key, f"type_ownership.{pool_type}",
                            f"属性 '{attr}' 在 type_ownership 中列出但不在 field_definitions 中定义",
                            f"请在 field_definitions.json 中添加 '{attr}' 字段定义，或从 type_ownership 中移除"
                        ))

    def _validate_type_mapping_completeness(self, data: dict, filename: str):
        """校验 type_mapping 中引用的类型必须在 cell_type_registry 中定义"""
        cell_types = self._load("cell_type_registry.json")
        if not cell_types:
            return
        registry_types = set(cell_types.get("types", {}).keys())

        type_mapping = data.get("type_mapping", {})
        if not isinstance(type_mapping, dict):
            return

        for direction in ("dzh_to_tdx", "tdx_to_dzh"):
            mapping = type_mapping.get(direction, {})
            if not isinstance(mapping, dict):
                continue
            for src_type, dst_type in mapping.items():
                if src_type not in registry_types:
                    self._results.append(ValidationResult(
                        "warning", filename, src_type, f"type_mapping.{direction}",
                        f"类型映射源 '{src_type}' 不在 cell_type_registry 中定义",
                        "请在 cell_type_registry.json 中添加该类型定义"
                    ))
                if dst_type not in registry_types:
                    self._results.append(ValidationResult(
                        "warning", filename, dst_type, f"type_mapping.{direction}",
                        f"类型映射目标 '{dst_type}' 不在 cell_type_registry 中定义",
                        "请在 cell_type_registry.json 中添加该类型定义"
                    ))

    def _validate_handler_integrity(self, data: dict, filename: str):
        """校验 modules.json 的 handler 必须在 builtins.py 中存在"""
        modules = data.get("modules", {})
        if not isinstance(modules, dict):
            return
        for module_id, module in modules.items():
            if not isinstance(module, dict):
                continue
            handler = module.get("handler")
            if handler:
                if handler not in self._handler_registry:
                    self._results.append(ValidationResult(
                        "error", filename, module_id, "handler",
                        f"handler '{handler}' 在 builtins.py 中不存在",
                        f"请在 builtins.py 中实现 '{handler}' 函数"
                    ))
                elif not callable(self._handler_registry.get(handler)):
                    self._results.append(ValidationResult(
                        "error", filename, module_id, "handler",
                        f"handler '{handler}' 存在但不可调用",
                        f"请确保 '{handler}' 是一个可调用对象"
                    ))

    def _validate_action_handler_callable(self, data: dict, filename: str):
        """校验 behavior_actions 中的 action_id 必须在 handler_registry 中可调用"""
        actions = data.get("actions", {})
        if not isinstance(actions, dict):
            return
        for action_id, action in actions.items():
            if not isinstance(action, dict):
                continue
            if action_id not in self._handler_registry:
                self._results.append(ValidationResult(
                    "error", filename, action_id, "action_id",
                    f"action_id '{action_id}' 在 handler_registry 中不存在",
                    f"请在 builtins.py 中实现 '{action_id}' 函数"
                ))
            elif not callable(self._handler_registry.get(action_id)):
                self._results.append(ValidationResult(
                    "error", filename, action_id, "action_id",
                    f"action_id '{action_id}' 存在但不可调用",
                    f"请确保 '{action_id}' 是一个可调用对象"
                ))

    def _validate_type_layout_coverage(self, data: dict, filename: str):
        """校验 cell_type_registry 中引用的类型必须有对应的 UI 布局"""
        ui_layouts = self._load("ui_layouts.json")
        if not ui_layouts:
            return

        types = data.get("types", {})
        if not isinstance(types, dict):
            return

        layouts = ui_layouts.get("layouts", {})
        # 收集所有布局覆盖的类型
        covered_types = set()
        for layout_id, layout in layouts.items():
            target = layout.get("target_type")
            if target is None:
                continue
            targets = target if isinstance(target, list) else [target]
            for t in targets:
                covered_types.add(str(t))

        for type_key, type_info in types.items():
            if not isinstance(type_info, dict):
                continue
            pool_type = type_info.get("pool_type")
            if pool_type is None:
                continue
            if type_key not in covered_types:
                self._results.append(ValidationResult(
                    "warning", filename, type_key, "pool_type",
                    f"类型 {type_key} ({type_info.get('name')}) 有 pool_type={pool_type} "
                    f"但在 ui_layouts.json 中无对应布局",
                    f"请在 ui_layouts.json 中为类型 {type_key} 添加布局定义"
                ))


# ──────────────────────────────────────────────────────────────
# 主校验器（兼容旧 API + 三级校验汇总）
# ──────────────────────────────────────────────────────────────

class SchemaValidator:
    def __init__(self, config_dir: str, handler_registry: dict):
        self.config_dir = Path(config_dir)
        self.handler_registry = handler_registry
        self.errors = []
        # 三级校验器实例
        self._syntax_validator = SyntaxValidator(self.config_dir)
        self._logic_validator = LogicValidator(self.config_dir)
        self._business_validator = BusinessValidator(self.config_dir, handler_registry)

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def validate_all(self) -> list[dict]:
        """Run all validations (three-level). Returns list of error dicts.

        兼容旧 API：返回格式与原来相同 [{file, entry, field, error}]。
        同时可通过 validate_all_detailed() 获取包含 level 和 suggestion 的详细结果。
        """
        report = self.validate_all_detailed()
        # 兼容旧格式：只取 error 级别的结果，格式化为旧格式
        self.errors = [r.to_dict() for r in report if r.level == "error"]
        return self.errors

    def validate_all_detailed(self) -> List[ValidationResult]:
        """运行三级校验，返回详细的 ValidationResult 列表"""
        all_results: List[ValidationResult] = []

        # 收集所有需要校验的表
        tables_to_validate = {}
        for path in sorted(self.config_dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    tables_to_validate[path.stem] = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                all_results.append(ValidationResult(
                    "error", path.name, "", "",
                    f"JSON 解析错误: {e}",
                    "请检查JSON文件格式是否正确"
                ))

        # 第一层：语法校验
        for table_name, data in tables_to_validate.items():
            results = self._syntax_validator.validate_syntax(table_name, data)
            all_results.extend(results)

        # 第二层：逻辑校验
        for table_name, data in tables_to_validate.items():
            results = self._logic_validator.validate_logic(table_name, data)
            all_results.extend(results)

        # 第三层：业务规则校验
        for table_name, data in tables_to_validate.items():
            results = self._business_validator.validate_business(table_name, data)
            all_results.extend(results)

        # 保留原有的校验逻辑（向后兼容）
        self.errors = []
        self._validate_field_definitions()
        self._validate_ui_components()
        self._validate_behavior_actions()
        self._validate_data_source_mappings()
        self._validate_references()
        self._validate_property_ownership()
        self._validate_pool_type_consistency()

        # 将旧格式的 errors 合并到 all_results
        for err in self.errors:
            # 避免重复：检查是否已有相同 file+entry+field+error 的结果
            duplicate = False
            err_msg = err.get("error", "")
            for r in all_results:
                if (r.file == err.get("file") and r.entry == err.get("entry")
                        and r.field == err.get("field") and r.message == err_msg):
                    duplicate = True
                    break
            if not duplicate:
                all_results.append(ValidationResult(
                    "error", err.get("file", ""), err.get("entry", ""),
                    err.get("field", ""), err_msg
                ))

        return all_results

    def validate_all_summary(self) -> dict:
        """运行三级校验，返回按 error/warning/info 分级汇总的报告。

        Returns:
            {
                "valid": bool,          # True 表示无 error（可加载）
                "blocked": bool,        # True 表示有 error（阻止加载）
                "total": int,           # 总结果数
                "by_level": {
                    "error": int,
                    "warning": int,
                    "info": int
                },
                "results": {
                    "error": [...],     # 阻止加载的错误
                    "warning": [...],   # 记录但不阻止的警告
                    "info": [...]       # 仅信息提示
                }
            }
        """
        all_results = self.validate_all_detailed()

        error_results = [r.to_dict() for r in all_results if r.level == "error"]
        warning_results = [r.to_dict() for r in all_results if r.level == "warning"]
        info_results = [r.to_dict() for r in all_results if r.level == "info"]

        return {
            "valid": len(error_results) == 0,
            "blocked": len(error_results) > 0,
            "total": len(all_results),
            "by_level": {
                "error": len(error_results),
                "warning": len(warning_results),
                "info": len(info_results),
            },
            "results": {
                "error": error_results,
                "warning": warning_results,
                "info": info_results,
            },
        }

    def validate_syntax(self, table_name: str, data: Any) -> List[ValidationResult]:
        """仅执行语法校验"""
        return self._syntax_validator.validate_syntax(table_name, data)

    def validate_logic(self, table_name: str, data: Any) -> List[ValidationResult]:
        """仅执行逻辑校验"""
        return self._logic_validator.validate_logic(table_name, data)

    def validate_business(self, table_name: str, data: Any) -> List[ValidationResult]:
        """仅执行业务规则校验"""
        return self._business_validator.validate_business(table_name, data)

    def validate_file(self, filepath: str) -> list[dict]:
        """Validate a single config file after hot-reload. Returns errors."""
        self.errors = []
        filename = Path(filepath).name
        table_name = Path(filepath).stem

        # 三级校验
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self._add_error(filename, "", "", f"JSON parse error: {e}")
            return self.errors

        # 语法校验
        syntax_results = self._syntax_validator.validate_syntax(table_name, data)
        # 逻辑校验
        logic_results = self._logic_validator.validate_logic(table_name, data)
        # 业务规则校验
        business_results = self._business_validator.validate_business(table_name, data)

        # 合并所有 error 级别结果
        for r in syntax_results + logic_results + business_results:
            if r.level == "error":
                self._add_error(r.file, r.entry, r.field, r.message)

        # 保留原有的按文件名分派逻辑（向后兼容）
        if filename == "field_definitions.json":
            self._validate_field_definitions()
        elif filename == "ui_components.json":
            self._validate_ui_components()
        elif filename == "behavior_actions.json":
            self._validate_behavior_actions()
        elif filename == "data_source_mappings.json":
            self._validate_data_source_mappings()
        elif filename in ("modules.json", "dispatch.json"):
            self._validate_references()

        return self.errors

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _add_error(self, file, entry, field, error):
        self.errors.append({"file": file, "entry": entry, "field": field, "error": error})

    # Task 9.2: _load_json 已删除，统一改用模块级 _get_table()（通过 ConfigStore.get_table）

    # ──────────────────────────────────────────────
    # 1. field_definitions.json
    # ──────────────────────────────────────────────

    def _validate_field_definitions(self):
        fd = _get_table("field_definitions.json")
        if fd is None:
            return

        ui = _get_table("ui_components.json")
        valid_components = set(ui.get("components", {}).keys()) if ui else set()

        # Required sections
        for section in ("global_fields", "type_specific_fields", "flow_fields", "bit_fields"):
            if section not in fd:
                self._add_error("field_definitions.json", "", "", f"Missing required section: {section}")

        # Collect all field-ui_component pairs for cross-checking
        # global_fields + type_specific_fields + flow_fields
        all_fields_with_ui = []

        # global_fields
        for fname, finfo in fd.get("global_fields", {}).items():
            if not isinstance(finfo, dict):
                continue
            all_fields_with_ui.append((fname, finfo))

        # type_specific_fields
        for type_key, fields in fd.get("type_specific_fields", {}).items():
            if not isinstance(fields, dict):
                continue
            for fname, finfo in fields.items():
                if not isinstance(finfo, dict):
                    continue
                all_fields_with_ui.append((fname, finfo))

        # flow_fields
        for fname, finfo in fd.get("flow_fields", {}).items():
            if not isinstance(finfo, dict):
                continue
            all_fields_with_ui.append((fname, finfo))

        # Check ui_component references
        for fname, finfo in all_fields_with_ui:
            ui_comp = finfo.get("ui_component", "")
            if ui_comp and ui_comp not in valid_components:
                self._add_error("field_definitions.json", fname, "ui_component",
                                f"ui_component '{ui_comp}' not found in ui_components.json")

        # Check applies_to_types are valid integers
        for fname, finfo in fd.get("global_fields", {}).items():
            if not isinstance(finfo, dict):
                continue
            applies = finfo.get("applies_to_types", [])
            if isinstance(applies, list):
                for v in applies:
                    if not isinstance(v, int):
                        self._add_error("field_definitions.json", fname, "applies_to_types",
                                        f"applies_to_types value '{v}' is not a valid integer")

        # Validate bit_fields
        # Build a map: type_key -> set of field names that exist for that type
        type_field_names = {}
        # global_fields apply to all types
        global_field_names = set(fd.get("global_fields", {}).keys())
        for type_key in fd.get("type_specific_fields", {}):
            type_field_names[type_key] = global_field_names | set(fd["type_specific_fields"][type_key].keys())
        # flow doesn't have a type_key in type_specific_fields, but bit_fields has "flow"
        type_field_names["flow"] = global_field_names | set(fd.get("flow_fields", {}).keys())

        for type_key, bit_defs in fd.get("bit_fields", {}).items():
            if not isinstance(bit_defs, dict):
                continue
            existing_fields = type_field_names.get(type_key, global_field_names)

            # Track bit_position per parent_field within this type
            parent_positions = {}

            for bit_name, bit_info in bit_defs.items():
                if not isinstance(bit_info, dict):
                    continue
                parent = bit_info.get("parent_field", "")
                bit_pos = bit_info.get("bit_position")

                # Check parent_field exists
                if parent and parent not in existing_fields:
                    self._add_error("field_definitions.json", bit_name, "parent_field",
                                    f"parent_field '{parent}' not found in type {type_key} fields")

                # Check duplicate bit_position within same parent_field
                if parent and bit_pos is not None:
                    if parent not in parent_positions:
                        parent_positions[parent] = {}
                    if bit_pos in parent_positions[parent]:
                        self._add_error("field_definitions.json", bit_name, "bit_position",
                                        f"duplicate bit_position {bit_pos} in parent_field '{parent}' "
                                        f"(already used by '{parent_positions[parent][bit_pos]}')")
                    else:
                        parent_positions[parent][bit_pos] = bit_name

    # ──────────────────────────────────────────────
    # 2. ui_components.json
    # ──────────────────────────────────────────────

    def _validate_ui_components(self):
        ui = _get_table("ui_components.json")
        if ui is None:
            return

        components = ui.get("components", {})
        if not components:
            self._add_error("ui_components.json", "", "", "No components defined")
            return

        # Required base components
        required = {"text", "number_input", "checkbox", "select", "color",
                     "textarea", "multiline_text", "readonly_text", "readonly_number",
                     "readonly_datetime", "base64_readonly", "text_readonly",
                     "flag_group", "group", "action_compound"}
        for name in required:
            if name not in components:
                self._add_error("ui_components.json", name, "component_type",
                                f"Required component '{name}' is missing")

        for name, comp in components.items():
            if not isinstance(comp, dict):
                continue

            # Every component must have component_type
            if "component_type" not in comp:
                self._add_error("ui_components.json", name, "component_type",
                                "Missing required field 'component_type'")

            # Compound / renderer components should have child_component or renderer
            ct = comp.get("component_type", "")
            if ct in ("flag_group", "group", "action_compound", "readonly_text",
                       "readonly_number", "readonly_datetime", "base64_readonly",
                       "text_readonly"):
                if not comp.get("child_component") and not comp.get("renderer"):
                    self._add_error("ui_components.json", name, "renderer",
                                    f"Compound/readonly component '{name}' missing child_component or renderer")

    # ──────────────────────────────────────────────
    # 3. behavior_actions.json
    # ──────────────────────────────────────────────

    def _validate_behavior_actions(self):
        ba = _get_table("behavior_actions.json")
        if ba is None:
            return

        actions = ba.get("actions", {})
        if not actions:
            self._add_error("behavior_actions.json", "", "", "No actions defined")
            return

        action_ids = set(actions.keys())

        for action_id, action in actions.items():
            if not isinstance(action, dict):
                continue

            # Required fields
            for req in ("action_id", "module", "input_ports", "output_ports"):
                if req not in action:
                    self._add_error("behavior_actions.json", action_id, req,
                                    f"Missing required field '{req}'")

            # Check action_id exists as callable in handler_registry
            if action_id not in self.handler_registry:
                self._add_error("behavior_actions.json", action_id, "action_id",
                                f"action_id '{action_id}' not found in handler_registry")
            elif not callable(self.handler_registry.get(action_id)):
                self._add_error("behavior_actions.json", action_id, "action_id",
                                f"action_id '{action_id}' exists in handler_registry but is not callable")

            # Check depends_on references are valid (they reference other action_ids)
            depends = action.get("depends_on", [])
            if isinstance(depends, list):
                for dep in depends:
                    if dep not in action_ids:
                        self._add_error("behavior_actions.json", action_id, "depends_on",
                                        f"depends_on '{dep}' is not a valid action_id")

    # ──────────────────────────────────────────────
    # 4. data_source_mappings.json
    # ──────────────────────────────────────────────

    def _validate_data_source_mappings(self):
        dsm = _get_table("data_source_mappings.json")
        if dsm is None:
            return

        mappings = dsm.get("mappings", {})
        if not mappings:
            self._add_error("data_source_mappings.json", "", "", "No mappings defined")
            return

        seen_col_ids = {}

        for key, mapping in mappings.items():
            if not isinstance(mapping, dict):
                continue

            # Required fields
            for req in ("col_id", "display_name", "tq_field", "schema_field"):
                if req not in mapping:
                    self._add_error("data_source_mappings.json", key, req,
                                    f"Missing required field '{req}'")

            # Check duplicate col_id
            col_id = mapping.get("col_id")
            if col_id is not None:
                if col_id in seen_col_ids:
                    self._add_error("data_source_mappings.json", key, "col_id",
                                    f"Duplicate col_id {col_id} (already used by '{seen_col_ids[col_id]}')")
                else:
                    seen_col_ids[col_id] = key

    # ──────────────────────────────────────────────
    # 5. Cross-table references
    # ──────────────────────────────────────────────

    def _validate_references(self):
        ba = _get_table("behavior_actions.json")
        action_ids = set(ba.get("actions", {}).keys()) if ba else set()

        fd = _get_table("field_definitions.json")
        # Collect all field names from field_definitions.json
        all_field_names = set()
        if fd:
            all_field_names.update(fd.get("global_fields", {}).keys())
            for fields in fd.get("type_specific_fields", {}).values():
                if isinstance(fields, dict):
                    all_field_names.update(fields.keys())
            all_field_names.update(fd.get("flow_fields", {}).keys())

        mod = _get_table("modules.json")
        if mod:
            # modules.json handler fields exist in behavior_actions.json
            for module_id, module in mod.get("modules", {}).items():
                if not isinstance(module, dict):
                    continue
                handler = module.get("handler")
                if handler and handler not in action_ids:
                    self._add_error("modules.json", module_id, "handler",
                                    f"handler '{handler}' not found in behavior_actions.json")

            # modules.json field references exist in field_definitions.json
            for module_id, module in mod.get("modules", {}).items():
                if not isinstance(module, dict):
                    continue
                mod_fields = module.get("fields", {})
                if not isinstance(mod_fields, dict):
                    continue
                for fname in mod_fields:
                    # Skip group fields (prefixed with _)
                    if fname.startswith("_"):
                        continue
                    if fname not in all_field_names:
                        self._add_error("modules.json", module_id, fname,
                                        f"field '{fname}' not found in field_definitions.json")

        disp = _get_table("dispatch.json")
        if disp:
            # dispatch.json gateway fields exist in behavior_actions.json
            for rule_key, rule in disp.get("dispatch_rules", {}).items():
                if not isinstance(rule, dict):
                    continue
                gateway = rule.get("gateway")
                if gateway and gateway not in action_ids:
                    self._add_error("dispatch.json", rule_key, "gateway",
                                    f"gateway '{gateway}' not found in behavior_actions.json")

    # ──────────────────────────────────────────────
    # 6. property_ownership.json - 属性所有权校验
    # ──────────────────────────────────────────────

    def _validate_property_ownership(self):
        """验证属性所有权配置的完整性和一致性"""
        po = _get_table("property_ownership.json")
        if po is None:
            return

        # 检查必要结构
        for section in ("ownership", "type_ownership", "type_mapping", "rules"):
            if section not in po:
                self._add_error("property_ownership.json", "", "", f"Missing required section: {section}")

        # 检查 type_ownership 中的类型是否在 cell_type_registry 中存在
        cell_types = _get_table("cell_type_registry.json")
        if cell_types:
            registry_types = set(cell_types.get("types", {}).keys())
            for type_key in po.get("type_ownership", {}):
                if type_key not in registry_types:
                    self._add_error("property_ownership.json", type_key, "type_ownership",
                                    f"Type '{type_key}' not found in cell_type_registry.json")

        # 检查 rules 中的 blocked_attrs 是否合理
        rules = po.get("rules", {})
        for rule_key in ("dzh_import", "tdx_import"):
            if rule_key not in rules:
                self._add_error("property_ownership.json", "", "rules",
                                f"Missing required rule: {rule_key}")
            else:
                blocked = rules[rule_key].get("blocked_attrs", [])
                if not isinstance(blocked, list):
                    self._add_error("property_ownership.json", rule_key, "blocked_attrs",
                                    "blocked_attrs must be a list")

        # 检查 ownership 中的 exclusive_attrs
        ownership = po.get("ownership", {})
        for pool_type in ("dzh", "tdx"):
            if pool_type not in ownership:
                self._add_error("property_ownership.json", "", "ownership",
                                f"Missing ownership definition for: {pool_type}")

        # 检查 type_mapping 双向一致性
        type_mapping = po.get("type_mapping", {})
        dzh_to_tdx = set(type_mapping.get("dzh_to_tdx", {}).keys())
        tdx_to_dzh = set(type_mapping.get("tdx_to_dzh", {}).keys())
        # 检查 key 类型的交叉引用
        for dzh_type in dzh_to_tdx:
            tdx_type = type_mapping["dzh_to_tdx"][dzh_type]
            if tdx_type not in tdx_to_dzh:
                self._add_error("property_ownership.json", dzh_type, "type_mapping",
                                f"dzh_to_tdx maps {dzh_type}->{tdx_type} but no reverse mapping exists")

    # ──────────────────────────────────────────────
    # 7. pool_type 一致性校验
    # ──────────────────────────────────────────────

    def _validate_pool_type_consistency(self):
        """验证 cell_type_registry 和 ui_layouts 中 pool_type 的一致性"""
        cell_types = _get_table("cell_type_registry.json")
        ui_layouts = _get_table("ui_layouts.json")

        if not cell_types or not ui_layouts:
            return

        registry_types = cell_types.get("types", {})
        layouts = ui_layouts.get("layouts", {})

        # 检查每个布局的 pool_type 是否与 cell_type_registry 中的 pool_type 一致
        for layout_id, layout in layouts.items():
            target_type = layout.get("target_type")
            layout_pool_types = layout.get("pool_type", [])

            if target_type is None:
                continue

            # 处理 target_type 为数组的情况
            target_types = target_type if isinstance(target_type, list) else [target_type]
            for tt in target_types:
                tt_str = str(tt)
                registry_type = registry_types.get(tt_str)
                if registry_type:
                    registry_pool = registry_type.get("pool_type")
                    if registry_pool and registry_pool not in layout_pool_types:
                        self._add_error("ui_layouts.json", layout_id, "pool_type",
                                        f"Layout pool_type {layout_pool_types} inconsistent with "
                                        f"cell_type_registry pool_type '{registry_pool}' for type {tt_str}")

        # 检查所有 cell_type_registry 中的类型是否都有对应的 UI 布局
        for type_key, type_info in registry_types.items():
            pool_type = type_info.get("pool_type")
            if pool_type is None:
                continue

            has_layout = False
            for layout_id, layout in layouts.items():
                lt = layout.get("target_type")
                if lt is None:
                    continue
                lt_list = lt if isinstance(lt, list) else [lt]
                if type_key in [str(t) for t in lt_list]:
                    has_layout = True
                    break

            if not has_layout:
                self._add_error("cell_type_registry.json", type_key, "pool_type",
                                f"Type {type_key} ({type_info.get('name')}) has pool_type={pool_type} "
                                f"but no matching UI layout found in ui_layouts.json")


# ════════════════════════════════════════════════════════════════
# ConfigIntegrityValidator - 配置完整性校验器
# ════════════════════════════════════════════════════════════════
#
# 全覆盖验证所有节点类型及属性的完整性、一致性和正确性。
# 基于 table_schemas.json 定义的模式进行结构化校验。
# 支持三种校验层级：
#   1. 语法校验：JSON格式、类型检查、必填字段
#   2. 逻辑校验：跨表引用、位标志合理性、属性所有权
#   3. 业务规则校验：节点类型覆盖、属性完整性、配置冲突检测


class ConfigIntegrityValidator:
    """配置完整性校验器：全覆盖验证所有节点类型及属性"""

    def __init__(self, config_dir: str):
        self._config_dir = Path(config_dir)
        self._errors: List[Dict] = []
        self._warnings: List[Dict] = []
        self._stats: Dict[str, Any] = {}

    # ─── Public API ──────────────────────────────────────────

    def validate_all(self) -> Dict[str, Any]:
        """运行全部校验，返回完整的校验报告"""
        self._errors = []
        self._warnings = []

        # 1. 语法校验
        self._validate_json_syntax()
        # 2. 逻辑校验
        self._validate_cross_table_refs()
        self._validate_bit_flags()
        self._validate_property_ownership_complete()
        # 3. 业务规则校验
        self._validate_node_type_coverage()
        self._validate_attribute_completeness()
        self._validate_config_conflicts()
        # 4. 统计信息
        self._compute_stats()

        return self._build_report()

    def _build_report(self) -> Dict[str, Any]:
        return {
            "valid": len(self._errors) == 0,
            "errors": self._errors,
            "warnings": self._warnings,
            "error_count": len(self._errors),
            "warning_count": len(self._warnings),
            "stats": self._stats
        }

    # ─── Helpers ─────────────────────────────────────────────

    def _add_error(self, file: str, entry: str, field: str, message: str):
        self._errors.append({"file": file, "entry": entry, "field": field, "error": message})

    def _add_warning(self, file: str, entry: str, field: str, message: str):
        self._warnings.append({"file": file, "entry": entry, "field": field, "warning": message})

    def _load(self, filename: str) -> Optional[Dict]:
        path = self._config_dir / filename
        if not path.exists():
            self._add_error(filename, "", "", f"File not found: {filename}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            self._add_error(filename, "", "", f"JSON parse error: {e}")
            return None

    # ─── 1. 语法校验 ─────────────────────────────────────────

    def _validate_json_syntax(self):
        """校验所有JSON文件的格式和基本结构"""
        required_files = [
            "cell_type_registry.json",
            "ui_layouts.json",
            "ui_components.json",
            "action_rules.json",
            "behavior_actions.json",
            "modules.json",
            "dispatch.json",
            "flow_mode_registry.json",
            "property_ownership.json",
            "table_schemas.json",
            "data_source_mappings.json",
            "pool_types.json",
            "engines.json",
            "markets.json",
        ]

        for filename in required_files:
            data = self._load(filename)
            if data is None:
                continue
            # 检查 version 字段
            if "version" not in data:
                self._add_warning(filename, "", "version", "Missing version field")

    # ─── 2. 逻辑校验（跨表引用） ─────────────────────────────

    def _validate_cross_table_refs(self):
        """校验跨表引用完整性"""
        cell_types = self._load("cell_type_registry.json")
        ui_layouts = self._load("ui_layouts.json")
        modules = self._load("modules.json")
        behavior_actions = self._load("behavior_actions.json")
        action_rules = self._load("action_rules.json")

        # 2.1 ui_layouts → cell_type_registry 引用
        if cell_types and ui_layouts:
            registry_types = set(cell_types.get("types", {}).keys())
            layout_targets = set()
            for layout_id, layout in ui_layouts.get("layouts", {}).items():
                target = layout.get("target_type")
                if target is None:
                    continue
                targets = target if isinstance(target, list) else [target]
                for t in targets:
                    layout_targets.add(str(t))

            # 检查布局中的类型是否都在注册表中
            for t in layout_targets:
                if t not in registry_types and t != "pool_meta" and t != "flow":
                    self._add_warning("ui_layouts.json", t, "target_type",
                                      f"Type '{t}' not found in cell_type_registry.json")

        # 2.2 modules → behavior_actions 引用
        if modules and behavior_actions:
            action_ids = set(behavior_actions.get("actions", {}).keys())
            for mod_id, mod in modules.get("modules", {}).items():
                if not isinstance(mod, dict):
                    continue
                handler = mod.get("handler")
                if handler and handler not in action_ids:
                    self._add_warning("modules.json", mod_id, "handler",
                                      f"handler '{handler}' not found in behavior_actions.json")

        # 2.3 action_rules → behavior_actions 引用
        if action_rules and behavior_actions:
            action_ids = set(behavior_actions.get("actions", {}).keys())
            for rule in action_rules.get("rules", []):
                handler_ref = rule.get("handler_ref")
                if handler_ref and handler_ref not in action_ids:
                    self._add_warning("action_rules.json", rule.get("rule_id", "?"), "handler_ref",
                                      f"handler_ref '{handler_ref}' not found in behavior_actions.json")

    # ─── 2.5 位标志校验 ──────────────────────────────────────

    def _validate_bit_flags(self):
        """校验位标志配置的合理性：
        - 检查同类型内无重复位偏移
        - 检查bits定义与flags字段一致性
        """
        cell_types = self._load("cell_type_registry.json")
        if not cell_types:
            return

        for type_key, type_info in cell_types.get("types", {}).items():
            attr_bits = type_info.get("attr_bits", {})
            if not attr_bits:
                continue

            # 检查重复位偏移
            seen_bits = {}
            for bit_name, bit_info in attr_bits.items():
                bit_pos = bit_info.get("bit")
                if bit_pos is not None:
                    if bit_pos in seen_bits:
                        self._add_error("cell_type_registry.json", type_key, f"attr_bits.{bit_name}",
                                        f"Duplicate bit position {bit_pos} with '{seen_bits[bit_pos]}'")
                    else:
                        seen_bits[bit_pos] = bit_name

            # 校验hex值与bit位移一致性
            for bit_name, bit_info in attr_bits.items():
                bit_pos = bit_info.get("bit")
                hex_val = bit_info.get("hex")
                if bit_pos is not None and hex_val is not None:
                    if isinstance(hex_val, str):
                        expected_hex = hex(1 << bit_pos)
                        if hex_val.lower() != expected_hex.lower():
                            self._add_warning("cell_type_registry.json", type_key, f"attr_bits.{bit_name}",
                                              f"hex '{hex_val}' inconsistent with bit {bit_pos} (expected {expected_hex})")

    # ─── 3.1 属性所有权完整性校验 ─────────────────────────────

    def _validate_property_ownership_complete(self):
        """校验属性所有权配置的完整性"""
        po = self._load("property_ownership.json")
        cell_types = self._load("cell_type_registry.json")

        if not po or not cell_types:
            return

        # 3.1.1 检查 cell_type_registry 中所有类型都有所有权定义
        type_ownership = po.get("type_ownership", {})
        for type_key, type_info in cell_types.get("types", {}).items():
            pool_type = type_info.get("pool_type")
            if pool_type is None:
                self._add_warning("cell_type_registry.json", type_key, "pool_type",
                                  f"Type {type_key} ({type_info.get('name')}) has no pool_type defined")
                continue

            if type_key not in type_ownership:
                self._add_warning("property_ownership.json", "", "type_ownership",
                                  f"Type {type_key} ({type_info.get('name')}) not in property_ownership.json")
                continue

            # 检查该类型在所属池类型下有属性定义
            to = type_ownership[type_key]
            allowed = to.get(pool_type)
            if allowed is None:
                self._add_warning("property_ownership.json", type_key, pool_type,
                                  f"Type {type_key} belongs to {pool_type} but no attributes defined for {pool_type}")

        # 3.1.2 检查 type_mapping 覆盖所有核心类型
        type_mapping = po.get("type_mapping", {})
        core_dzh_types = {"200", "201", "202"}  # DZH核心类型
        core_tdx_types = {"7", "8", "3"}         # TDX核心类型

        dzh_to_tdx = type_mapping.get("dzh_to_tdx", {})
        for dt in core_dzh_types:
            if dt not in dzh_to_tdx:
                self._add_warning("property_ownership.json", dt, "type_mapping.dzh_to_tdx",
                                  f"DZH core type {dt} missing in dzh_to_tdx mapping")

        tdx_to_dzh = type_mapping.get("tdx_to_dzh", {})
        for tt in core_tdx_types:
            if tt not in tdx_to_dzh:
                self._add_warning("property_ownership.json", tt, "type_mapping.tdx_to_dzh",
                                  f"TDX core type {tt} missing in tdx_to_dzh mapping")

    # ─── 3.2 节点类型覆盖率校验 ───────────────────────────────

    def _validate_node_type_coverage(self):
        """校验所有节点类型都有完整的配置覆盖"""
        cell_types = self._load("cell_type_registry.json")
        ui_layouts = self._load("ui_layouts.json")
        modules = self._load("modules.json")

        if not cell_types:
            return

        types = cell_types.get("types", {})

        # 3.2.1 每个节点类型都有UI布局
        if ui_layouts:
            layouts = ui_layouts.get("layouts", {})
            layout_targets = set()
            for lid, layout in layouts.items():
                target = layout.get("target_type")
                if target is None:
                    continue
                targets = target if isinstance(target, list) else [target]
                for t in targets:
                    layout_targets.add(str(t))

            for type_key, type_info in types.items():
                pool_type = type_info.get("pool_type")
                if pool_type is None:
                    continue
                if type_key not in layout_targets:
                    self._add_warning("cell_type_registry.json", type_key, "layout",
                                      f"Type {type_key} ({type_info.get('name')}) has no UI layout")

        # 3.2.2 每个核心节点类型都有模块配置
        if modules:
            mods = modules.get("modules", {})
            type_module_map = {}
            for mod_id, mod in mods.items():
                if isinstance(mod, dict):
                    cell_types_list = mod.get("dzh_cell_types", [])
                    for ct_val in cell_types_list:
                        type_module_map[str(ct_val)] = mod_id

            core_types = {"200", "201", "202", "203"}
            for ct in core_types:
                if ct in types and ct not in type_module_map:
                    if types[ct].get("handler"):
                        self._add_warning("modules.json", "", "modules",
                                          f"DZH core type {ct} ({types[ct].get('name')}) has no module definition")

    # ─── 3.3 属性完整性校验 ────────────────────────────────────

    def _validate_attribute_completeness(self):
        """校验所有节点类型的属性完整性"""
        cell_types = self._load("cell_type_registry.json")
        ui_layouts = self._load("ui_layouts.json")
        field_definitions = self._load("field_definitions.json")

        if not cell_types:
            return

        types = cell_types.get("types", {})

        for type_key, type_info in types.items():
            attrs = type_info.get("attrs", [])
            field_mappings = type_info.get("field_mappings", {})
            default_params = type_info.get("default_params", {})

            # 检查 attrs 列表与 field_mappings 一致性
            mapped_attrs = set(field_mappings.keys())
            for attr in mapped_attrs:
                if attr not in attrs and attr not in ("attr_int", "attr_decoded",
                                                       "hold_sec", "col_list", "width_list",
                                                       "dzh_attr", "markets", "reload_sec",
                                                       "tdx_spinfo", "tdx_stocks", "tdx_psatt",
                                                       "tdx_func", "name", "label"):
                    # 允许部分映射属性不在attrs中（这些是派生/内部属性）
                    pass

            # 检查 default_params 覆盖所有 field_mappings 的 target key
            for target_key in field_mappings.values():
                if target_key not in default_params and target_key not in ("attr", "attr_int", "attr_decoded",
                                                                            "analysis_cycle", "text", "modules"):
                    pass  # 某些映射目标是特殊的，不需要默认值

        # 3.3.2 检查ui_layouts中的字段是否在cell_type_registry中有对应属性
        if ui_layouts and cell_types:
            for layout_id, layout in ui_layouts.get("layouts", {}).items():
                target_types = layout.get("target_type")
                if target_types is None:
                    continue
                targets = target_types if isinstance(target_types, list) else [target_types]

                for tt in targets:
                    tt_str = str(tt)
                    if tt_str == "flow" or tt_str == "pool_meta":
                        continue
                    type_info = types.get(tt_str)
                    if type_info is None:
                        continue

                    type_attrs = set(type_info.get("attrs", []))
                    layout_data_paths = set()
                    for section in layout.get("sections", []):
                        for field in section.get("fields", []):
                            dp = field.get("data_path", "")
                            if dp:
                                layout_data_paths.add(dp.split(".")[0])

                    # 检查布局字段是否对应已知属性
                    for dp in layout_data_paths:
                        if dp not in type_attrs and dp not in ("dzh_attr", "attr_int", "tdx_psatt",
                                                                 "tdx_func", "tdx_spinfo", "tdx_stocks",
                                                                 "name", "hold_sec", "col_list",
                                                                 "attr_flags", "alert_flags", "cond_flags",
                                                                 "stock_data", "stock_list", "delete_source",
                                                                 "keep_source", "reload_sec"):
                            pass  # 某些data_path是派生字段，不需要在attrs中

    # ─── 3.4 配置冲突检测 ──────────────────────────────────────

    def _validate_config_conflicts(self):
        """检测配置间的潜在冲突"""
        cell_types = self._load("cell_type_registry.json")
        property_ownership = self._load("property_ownership.json")
        ui_layouts = self._load("ui_layouts.json")

        if not all([cell_types, property_ownership, ui_layouts]):
            return

        types = cell_types.get("types", {})
        type_ownership = property_ownership.get("type_ownership", {})
        layouts = ui_layouts.get("layouts", {})

        # 3.4.1 检测同一类型多次定义冲突
        seen_names = {}
        for type_key, type_info in types.items():
            name = type_info.get("name", "")
            if name and name in seen_names:
                self._add_warning("cell_type_registry.json", type_key, "name",
                                  f"Duplicate name '{name}' already used by type {seen_names[name]}")
            seen_names[name] = type_key

        # 3.4.2 检测布局中pool_type与属性所有权冲突
        for layout_id, layout in layouts.items():
            layout_pool_types = layout.get("pool_type", [])
            target_type = layout.get("target_type")
            if target_type is None:
                continue
            targets = target_type if isinstance(target_type, list) else [target_type]

            for tt in targets:
                tt_str = str(tt)
                to = type_ownership.get(tt_str)
                if to:
                    # 检查布局的pool_type是否与type_ownership定义一致
                    for lpt in layout_pool_types:
                        if lpt != "any" and to.get(lpt) is None:
                            self._add_warning("ui_layouts.json", layout_id, "pool_type",
                                              f"Layout has pool_type '{lpt}' but type {tt_str} has no attributes defined for {lpt}")

    # ─── 4. 统计信息 ──────────────────────────────────────────

    def _compute_stats(self):
        """计算配置统计信息"""
        cell_types = self._load("cell_type_registry.json")
        ui_layouts = self._load("ui_layouts.json")
        property_ownership = self._load("property_ownership.json")

        stats = {
            "total_types": 0,
            "dzh_types": 0,
            "tdx_types": 0,
            "shared_types": 0,
            "total_layouts": 0,
            "node_layouts": 0,
            "edge_layouts": 0,
            "total_mapped_types": 0,
        }

        if cell_types:
            types = cell_types.get("types", {})
            stats["total_types"] = len(types)
            for type_key, type_info in types.items():
                pt = type_info.get("pool_type", "unknown")
                if pt == "dzh":
                    stats["dzh_types"] += 1
                elif pt == "tdx":
                    stats["tdx_types"] += 1
                else:
                    stats["shared_types"] += 1

        if ui_layouts:
            layouts = ui_layouts.get("layouts", {})
            stats["total_layouts"] = len(layouts)
            for layout_id, layout in layouts.items():
                scope = layout.get("target_scope", "node")
                if scope == "node":
                    stats["node_layouts"] += 1
                elif scope == "edge":
                    stats["edge_layouts"] += 1

        if property_ownership:
            to = property_ownership.get("type_ownership", {})
            stats["total_mapped_types"] = len(to)

        self._stats = stats


# ─── 快捷函数 ─────────────────────────────────────────────────

def validate_configs(config_dir: str) -> Dict[str, Any]:
    """快捷函数：运行所有配置校验"""
    validator = ConfigIntegrityValidator(config_dir)
    return validator.validate_all()


# ════════════════════════════════════════════════════════════════
# TableLoader - 热重载模块
# ════════════════════════════════════════════════════════════════
#
# 监控 config/ 目录并在 JSON 文件变更时自动重载与验证。


class TableLoader:
    def __init__(self, config_dir: str, validator, engine_callback=None, storage=None):
        self.config_dir = Path(config_dir)
        self.validator = validator  # SchemaValidator instance
        self.engine_callback = engine_callback  # called after successful reload
        self._storage = storage  # Storage 引用，用于记录配置版本
        self._loaded_tables = {}  # cached table content
        self._file_mtimes = {}  # last modification times
        self._running = False
        self._lock = threading.Lock()
        self._thread = None

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def set_engine_callback(self, callback):
        """Register a callback invoked after every successful reload."""
        self.engine_callback = callback

    def set_storage(self, storage):
        """设置 Storage 引用，用于记录配置版本历史。"""
        self._storage = storage

    def start_watching(self, poll_interval=2.0):
        """Start monitoring config directory with polling (no external deps).

        Spawns a daemon background thread that checks file mtimes every
        poll_interval seconds.  When a .json file changes, reload_file() is
        called automatically.
        """
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, args=(poll_interval,), daemon=True
        )
        self._thread.start()
        logger.info("TableLoader started watching %s", self.config_dir)

    def stop_watching(self):
        """Stop the background monitoring thread."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("TableLoader stopped watching")

    def reload_file(self, filepath: str) -> tuple:
        """Reload a single config file.

        Returns:
            (success: bool, errors: list[dict])

        On success the table is atomically replaced in _loaded_tables.
        On failure the old table is kept and the returned errors list
        describes what went wrong.
        """
        filepath_obj = Path(filepath)
        table_name = filepath_obj.stem

        if not filepath_obj.exists():
            msg = "File not found"
            logger.warning("reload_file: %s", msg)
            return (False, [{"file": filepath_obj.name, "entry": "", "field": "", "error": msg}])

        # ── 1. Parse ─────────────────────────────────────────
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                new_content = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to parse %s: %s", filepath_obj.name, e)
            return (False, [{"file": filepath_obj.name, "entry": "", "field": "", "error": str(e)}])

        # ── 2. 三级校验 ──────────────────────────────────────
        errors = self._run_three_level_validation(table_name, new_content, filepath_obj.name)
        if errors:
            logger.warning("三级校验失败，%s 保留旧配置: %s", filepath_obj.name, errors)
            return (False, errors)

        # ── 3. 记录旧配置用于版本追踪 ─────────────────────────
        old_content = self._loaded_tables.get(table_name)
        old_content_str = json.dumps(old_content, ensure_ascii=False) if old_content else None
        new_content_str = json.dumps(new_content, ensure_ascii=False)

        # ── 4. Atomic update ─────────────────────────────────
        with self._lock:
            self._loaded_tables[table_name] = new_content

        logger.info("Successfully reloaded %s", filepath_obj.name)

        # ── 5. 记录配置版本 ──────────────────────────────────
        if self._storage is not None:
            try:
                change_type = 'update' if old_content else 'create'
                self._storage.record_config_version(
                    table_name=table_name,
                    change_type=change_type,
                    old_content=old_content_str,
                    new_content=new_content_str,
                    created_by='hot_reload'
                )
            except Exception as e:
                logger.warning("记录配置版本 %s 失败: %s", table_name, e)

        # ── 6. Notify engine ─────────────────────────────────
        if self.engine_callback:
            try:
                self.engine_callback()
            except Exception as e:
                logger.warning("Engine callback failed after reloading %s: %s",
                               filepath_obj.name, e)

        return (True, [])

    def reload_all(self) -> tuple:
        """Reload every .json config file from scratch.

        The operation is atomic: if *any* file fails validation or parsing,
        **none** of the tables are updated and the old state is preserved.

        Returns:
            (success: bool, errors: list[dict])
        """
        all_errors = []
        new_tables = {}
        old_tables_snapshot = dict(self._loaded_tables)  # 保存旧配置快照用于版本追踪

        if not self.config_dir.exists():
            msg = "Config directory not found"
            return (False, [{"file": str(self.config_dir), "entry": "", "field": "", "error": msg}])

        for filepath in sorted(self.config_dir.glob("*.json")):
            filepath_str = str(filepath)

            # Update mtime tracking
            self._file_mtimes[filepath_str] = os.path.getmtime(filepath_str)

            # Parse
            try:
                with open(filepath_str, "r", encoding="utf-8") as f:
                    new_content = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                all_errors.append({
                    "file": filepath.name, "entry": "", "field": "", "error": str(e)
                })
                continue

            # 三级校验
            errors = self._run_three_level_validation(filepath.stem, new_content, filepath.name)
            if errors:
                all_errors.extend(errors)
                continue

            new_tables[filepath.stem] = new_content

        if all_errors:
            logger.warning("reload_all aborted with %d error(s)", len(all_errors))
            return (False, all_errors)

        # Atomic replacement
        with self._lock:
            self._loaded_tables = new_tables

        logger.info("reload_all succeeded: loaded %d table(s)", len(new_tables))

        # 记录所有变更的配置版本
        if self._storage is not None:
            for table_name, new_data in new_tables.items():
                try:
                    old_data = old_tables_snapshot.get(table_name)
                    old_content_str = json.dumps(old_data, ensure_ascii=False) if old_data else None
                    new_content_str = json.dumps(new_data, ensure_ascii=False)
                    change_type = 'update' if old_data else 'create'
                    self._storage.record_config_version(
                        table_name=table_name,
                        change_type=change_type,
                        old_content=old_content_str,
                        new_content=new_content_str,
                        created_by='reload_all'
                    )
                except Exception as e:
                    logger.warning("记录配置版本 %s 失败: %s", table_name, e)

        if self.engine_callback:
            try:
                self.engine_callback()
            except Exception as e:
                logger.warning("Engine callback failed after reload_all: %s", e)

        return (True, [])

    def get_table(self, table_name: str) -> dict:
        """Get the currently loaded content for *table_name*."""
        with self._lock:
            return self._loaded_tables.get(table_name, {})

    @property
    def loaded_tables(self) -> dict:
        """Return a shallow snapshot of all loaded tables."""
        with self._lock:
            return dict(self._loaded_tables)

    # ──────────────────────────────────────────────
    # Internals
    # ──────────────────────────────────────────────

    def _run_three_level_validation(self, table_name: str, data: dict, filename: str) -> list:
        """执行三级校验（语法/逻辑/业务规则），返回 error 级别的错误列表。

        校验失败时保留旧配置不变，仅记录错误日志。
        返回空列表表示校验通过。
        """
        all_errors = []

        # 第一层：语法校验
        try:
            syntax_results = self.validator.validate_syntax(table_name, data)
            syntax_errors = [r for r in syntax_results if r.level == "error"]
            if syntax_errors:
                for r in syntax_errors:
                    msg = f"语法校验失败[{filename}]: {r.message}"
                    logger.error(msg)
                    all_errors.append({
                        "file": filename, "entry": r.entry, "field": r.field,
                        "error": r.message, "level": "syntax"
                    })
        except Exception as e:
            logger.error("语法校验异常[%s]: %s", filename, e)
            all_errors.append({
                "file": filename, "entry": "", "field": "",
                "error": f"语法校验异常: {e}", "level": "syntax"
            })

        # 第二层：逻辑校验
        try:
            logic_results = self.validator.validate_logic(table_name, data)
            logic_errors = [r for r in logic_results if r.level == "error"]
            if logic_errors:
                for r in logic_errors:
                    msg = f"逻辑校验失败[{filename}]: {r.message}"
                    logger.error(msg)
                    all_errors.append({
                        "file": filename, "entry": r.entry, "field": r.field,
                        "error": r.message, "level": "logic"
                    })
        except Exception as e:
            logger.error("逻辑校验异常[%s]: %s", filename, e)
            all_errors.append({
                "file": filename, "entry": "", "field": "",
                "error": f"逻辑校验异常: {e}", "level": "logic"
            })

        # 第三层：业务规则校验
        try:
            business_results = self.validator.validate_business(table_name, data)
            business_errors = [r for r in business_results if r.level == "error"]
            if business_errors:
                for r in business_errors:
                    msg = f"业务规则校验失败[{filename}]: {r.message}"
                    logger.error(msg)
                    all_errors.append({
                        "file": filename, "entry": r.entry, "field": r.field,
                        "error": r.message, "level": "business"
                    })
        except Exception as e:
            logger.error("业务规则校验异常[%s]: %s", filename, e)
            all_errors.append({
                "file": filename, "entry": "", "field": "",
                "error": f"业务规则校验异常: {e}", "level": "business"
            })

        if not all_errors:
            logger.info("三级校验通过[%s]: 语法/逻辑/业务规则均无错误", filename)

        return all_errors

    def _poll_loop(self, poll_interval):
        while self._running:
            self._check_for_changes()
            # Sleep in small increments so we can exit cleanly
            for _ in range(int(poll_interval * 10)):
                if not self._running:
                    return
                threading.Event().wait(0.1)

    def _check_for_changes(self):
        if not self.config_dir.exists():
            return
        for filepath in self.config_dir.glob("*.json"):
            filepath_str = str(filepath)
            try:
                current_mtime = os.path.getmtime(filepath_str)
            except OSError:
                continue

            previous_mtime = self._file_mtimes.get(filepath_str)

            if previous_mtime is not None and current_mtime == previous_mtime:
                continue

            self._file_mtimes[filepath_str] = current_mtime
            if previous_mtime is not None:
                # File was already tracked → modification detected
                logger.info("Detected change in %s, reloading...", filepath.name)
                self.reload_file(filepath_str)


# ════════════════════════════════════════════════════════════════
# 匹配器层（自 native/matchers.py 合并）
# ════════════════════════════════════════════════════════════════
# 时机判断（原 timing.py）
# ════════════════════════════════════════════════════════════════

def _now_dt() -> datetime:
    return datetime.now()


def _safe_int(val, default=0):
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _time_to_seconds(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def _parse_hhmmss(val) -> Optional[int]:
    if val is None or val == "" or val == "0":
        return None
    try:
        s = str(int(val)).zfill(6)
        h, m, sec = int(s[0:2]), int(s[2:4]), int(s[4:6])
        return h * 3600 + m * 60 + sec
    except (ValueError, TypeError):
        return None


def should_fire(
    edge: dict,
    current_time: datetime,
    param_aliases: dict = None,
    *,
    market_open: datetime = None,
    market_close: datetime = None,
    replay_start_time: float = None,
    flow_fire_counts: dict = None,
    flow_last_fire: dict = None,
) -> bool:
    """判断 Flow/Edge 是否应在当前时间触发。

    统一支持 DZH executor 和 K-line replay engine 的时机判断需求：
    - begin/begint: 开始时间窗口
    - end/endt: 结束时间窗口
    - interval_sec/interval: 触发间隔（秒）
    - cst/cet/cstt/cett/c_period: 自定义周期时间

    Args:
        edge: 包含时控参数的字典（通常含 params / attr）
        current_time: 当前时间
        param_aliases: 参数名映射，如 {"begin": ["starttype", "tdx_starttype"], ...}
        market_open: 开盘时间（replay 场景使用）
        market_close: 收盘时间（replay 场景使用）
        replay_start_time: 回放开始时间戳（用于 delay / duration 判断）
        flow_fire_counts: 各 flow 已触发次数（用于 end_mode==2 执行一次判断）
        flow_last_fire: 各 flow 上次触发时间戳（用于 interval 判断）
    """
    # 参数源：优先 params，其次 attr，最后 edge 本身
    raw_params = edge.get("params")
    params = raw_params if isinstance(raw_params, dict) else (edge if isinstance(edge, dict) else {})
    attr = edge.get("attr", {}) if isinstance(edge.get("attr"), dict) else {}

    def _get_param(key: str, default=None):
        val = params.get(key)
        if val is not None:
            return val
        val = attr.get(key)
        if val is not None:
            return val
        aliases = (param_aliases or {}).get(key, [])
        if isinstance(aliases, str):
            aliases = [aliases]
        for alias in aliases:
            val = params.get(alias)
            if val is not None:
                return val
            val = attr.get(alias)
            if val is not None:
                return val
        return default

    begin_mode = _safe_int(_get_param("begin"), 0)
    begint_val = _get_param("begint", "0") or "0"
    end_mode = _safe_int(_get_param("end"), 0)
    endt_val = _get_param("endt", "0") or "0"
    interval_sec = _safe_int(_get_param("interval_sec", _get_param("interval")), 0)

    cst = _get_param("cst")
    cet = _get_param("cet")
    cstt = _get_param("cstt")
    cett = _get_param("cett")

    now_sec = _time_to_seconds(current_time.time())
    now_ts = current_time.timestamp()

    # 自定义周期时间优先
    if cst is not None or cet is not None:
        try:
            cst_sec = int(cst) if cst else 0
            cet_sec = int(cet) if cet else 86400
            if now_sec < cst_sec or now_sec > cet_sec:
                return False
        except (ValueError, TypeError):
            pass

    if cstt is not None or cett is not None:
        try:
            cstt_sec = int(cstt) if cstt else 0
            cett_sec = int(cett) if cett else 86400
            if now_sec < cstt_sec or now_sec > cett_sec:
                return False
        except (ValueError, TypeError):
            pass

    # begin 窗口判断
    should_begin = False
    if begin_mode == 0:
        should_begin = True
    elif begin_mode == 1:
        delay = _safe_int(begint_val, 0)
        if replay_start_time is not None:
            should_begin = (now_ts - replay_start_time) >= delay
        else:
            should_begin = True  # delay 已在首次调度时处理
    elif begin_mode == 2:
        offset = _safe_int(begint_val, 0)
        if market_open is not None and offset > 0:
            fire_time = market_open - timedelta(seconds=offset)
            should_begin = current_time >= fire_time
        else:
            should_begin = True  # before_open，由外部交易日判断
    elif begin_mode == 3:
        offset = _safe_int(begint_val, 0)
        if market_open is not None:
            open_secs = _time_to_seconds(market_open.time())
            fire_secs = open_secs + offset
            should_begin = now_sec >= fire_secs
        else:
            # DZH 场景：mode 3 作为 HHMMSS 处理
            target_sec = _parse_hhmmss(begint_val)
            should_begin = now_sec >= target_sec if target_sec is not None else True
    elif begin_mode == 4:
        offset = _safe_int(begint_val, 0)
        if market_close is not None:
            close_secs = _time_to_seconds(market_close.time())
            fire_secs = close_secs - offset
            should_begin = now_sec >= fire_secs
        else:
            # DZH 场景：mode 4 作为 HHMMSS 处理
            target_sec = _parse_hhmmss(begint_val)
            should_begin = now_sec >= target_sec if target_sec is not None else True
    elif begin_mode == 5:
        offset = _safe_int(begint_val, 0)
        if market_close is not None:
            fire_time = market_close + timedelta(seconds=offset)
            should_begin = current_time >= fire_time
        else:
            should_begin = True
    elif begin_mode == 7:
        target_sec = _parse_hhmmss(begint_val)
        should_begin = now_sec >= target_sec if target_sec is not None else True
    else:
        should_begin = True

    if not should_begin:
        return False

    # end 窗口判断
    if end_mode == 0:
        pass
    elif end_mode == 1:
        duration = _safe_int(endt_val, 0)
        if replay_start_time is not None:
            elapsed = now_ts - replay_start_time
            if elapsed > duration:
                return False
        else:
            if duration <= 0:
                return False
    elif end_mode == 2:
        if flow_fire_counts is not None:
            eid = edge.get("id", "")
            if flow_fire_counts.get(eid, 0) > 0:
                return False
        # DZH 场景：执行一次由外部调度器管理
    elif end_mode in (3, 4, 7):
        target_sec = _parse_hhmmss(endt_val)
        if target_sec is not None:
            if now_sec > target_sec:
                return False
        # 解析失败则默认通过

    # interval 判断
    if interval_sec > 0 and flow_last_fire is not None:
        eid = edge.get("id", "")
        last_fire_ts = flow_last_fire.get(eid, 0.0)
        if last_fire_ts > 0:
            elapsed_since_fire = now_ts - last_fire_ts
            if elapsed_since_fire < interval_sec:
                return False

    return True


# ════════════════════════════════════════════════════════════════
# 拓扑模式匹配（原 topology_matcher.py）
# ════════════════════════════════════════════════════════════════

class TopologyPatternMatcher:
    """拓扑模式匹配器：从配置表加载模式并按优先级匹配。"""

    # 被视为"源"的节点类型（market_source / candidate）
    _SOURCE_TYPES = {"market_source", "tdx_candidate", "candidate_provider"}
    # 被视为"条件/枢纽"的节点类型
    _CONDITION_TYPES = {"transfer_condition", "tdx_condition", "dzh_condition_pool", "condition_filter"}
    # 被视为"汇/状态池"的节点类型
    _SINK_TYPES = {"stock_state_pool", "tdx_state_pool", "stock_state_fallback", "discard_pool"}

    def __init__(self, config_path=None):
        self._patterns = []
        self._priority = []
        self._fallback = {"pattern_id": "unknown", "execution_strategy": "serial",
                          "cache_policy": "source_fingerprint", "cache_key": "source_id"}
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "topology_patterns.json"
        self.load(config_path)

    def load(self, path):
        """从 JSON 文件加载拓扑模式配置。"""
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        self._patterns = {p["pattern_id"]: p for p in cfg.get("patterns", [])}
        self._priority = cfg.get("detection_priority", list(self._patterns.keys()))
        self._fallback = cfg.get("fallback", self._fallback)

    def _resolve_node_type(self, node):
        """统一解析节点类型字符串。"""
        rt = node.get("type", "") if isinstance(node, dict) else ""
        if isinstance(rt, int):
            rt = str(rt)
        if not rt and isinstance(node, dict):
            rt = str(node.get("dzh_cell_type", 0) or "")
        return rt

    def _is_type(self, node_type, category):
        if category == "source":
            return node_type in self._SOURCE_TYPES
        if category == "condition":
            return node_type in self._CONDITION_TYPES
        if category == "sink":
            return node_type in self._SINK_TYPES
        return node_type == category

    def _build_graph(self, nodes, edges):
        """构建有向图特征：邻接表、入度、出度、源节点、汇节点。"""
        adj = {nid: [] for nid in nodes}
        radj = {nid: [] for nid in nodes}
        in_degree = {nid: 0 for nid in nodes}
        out_degree = {nid: 0 for nid in nodes}

        def _endpoint(edge, *keys):
            for k in keys:
                v = edge.get(k, "")
                if not v:
                    continue
                if isinstance(v, str):
                    return v
                if isinstance(v, dict):
                    nid = v.get("node_id", "") or v.get("id", "")
                    if nid:
                        return nid
            return ""

        for edge in edges:
            sid = _endpoint(edge, "from", "source", "startid")
            tid = _endpoint(edge, "to", "target", "endid")
            if sid in nodes and tid in nodes:
                adj[sid].append(tid)
                radj[tid].append(sid)
                out_degree[sid] += 1
                in_degree[tid] += 1
        return adj, radj, in_degree, out_degree

    def _has_cycle(self, nodes, adj):
        """DFS 检测有向图环。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in nodes}

        def dfs(nid):
            color[nid] = GRAY
            for nxt in adj.get(nid, []):
                if nxt not in color:
                    continue
                if color[nxt] == GRAY:
                    return True
                if color[nxt] == WHITE and dfs(nxt):
                    return True
            color[nid] = BLACK
            return False

        for nid in nodes:
            if color[nid] == WHITE and dfs(nid):
                return True
        return False

    def _longest_chain_depth(self, nodes, adj, in_degree):
        """从源节点出发的最长链深度（节点数）。"""
        indeg = {nid: in_degree.get(nid, 0) for nid in nodes}
        q = deque([nid for nid in nodes if indeg[nid] == 0])
        depth = {nid: 1 for nid in nodes}
        max_depth = 1
        while q:
            cur = q.popleft()
            for nxt in adj.get(cur, []):
                depth[nxt] = max(depth[nxt], depth[cur] + 1)
                max_depth = max(max_depth, depth[nxt])
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    q.append(nxt)
        return max_depth

    def _evaluate_rule(self, rule, features):
        """单条规则求值。"""
        metric = rule.get("metric")
        expected = rule.get("value")
        min_val = rule.get("min")
        max_val = rule.get("max")

        if metric == "has_cycle":
            return bool(features["has_cycle"]) == bool(expected)
        if metric == "targets_are_condition":
            return bool(features["targets_are_condition"]) == bool(expected)

        value = features.get(metric)
        if value is None:
            return False

        if expected is not None:
            return value == expected
        if min_val is not None and value < min_val:
            return False
        if max_val is not None and value > max_val:
            return False
        return True

    def match_pattern(self, nodes, edges, resolved_types=None):
        """识别拓扑模式，返回匹配到的 pattern 字典（含 pattern_id / execution_strategy 等）。

        Args:
            nodes: dict {node_id: node_dict}
            edges: list of edge dicts
            resolved_types: optional dict {node_id: resolved_type_str}; 若提供则直接使用，
                            否则基于节点自身 type / dzh_cell_type 解析。

        Returns:
            dict: 匹配到的 pattern 配置（包含 pattern_id, execution_strategy, cache_policy, cache_key）
        """
        if not nodes or not edges:
            return self._fallback

        if resolved_types is None:
            resolved_types = {nid: self._resolve_node_type(nd) for nid, nd in nodes.items()}

        adj, radj, in_degree, out_degree = self._build_graph(nodes, edges)
        has_cycle = self._has_cycle(nodes, adj)
        chain_depth = self._longest_chain_depth(nodes, adj, in_degree)

        source_nodes = [nid for nid, nt in resolved_types.items() if self._is_type(nt, "source")]
        sink_nodes = [nid for nid, nt in resolved_types.items() if self._is_type(nt, "sink")]
        condition_nodes = [nid for nid, nt in resolved_types.items() if self._is_type(nt, "condition")]

        # 源节点出度统计
        source_out_degrees = [out_degree.get(nid, 0) for nid in source_nodes]
        max_source_out = max(source_out_degrees) if source_out_degrees else 0

        # 汇节点入度统计
        sink_in_degrees = [in_degree.get(nid, 0) for nid in sink_nodes]
        max_sink_in = max(sink_in_degrees) if sink_in_degrees else 0

        # 全图最大入度/出度
        max_in = max(in_degree.values()) if in_degree else 0
        max_out = max(out_degree.values()) if out_degree else 0

        # 源节点所有出边目标是否都是条件节点
        targets_are_condition = False
        if source_nodes:
            targets = set()
            for sid in source_nodes:
                for tid in adj.get(sid, []):
                    targets.add(tid)
            targets_are_condition = bool(targets) and all(
                self._is_type(resolved_types.get(tid, ""), "condition") for tid in targets
            )

        features = {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "source_count": len(source_nodes),
            "min_source_count": len(source_nodes),
            "sink_count": len(sink_nodes),
            "condition_node_count": len(condition_nodes),
            "max_in_degree": max_in,
            "max_out_degree": max_out,
            "max_source_out_degree": max_source_out,
            "max_sink_in_degree": max_sink_in,
            "min_chain_depth": chain_depth,
            "has_cycle": has_cycle,
            "targets_are_condition": targets_are_condition,
        }

        for pid in self._priority:
            pattern = self._patterns.get(pid)
            if not pattern:
                continue
            rules = pattern.get("match_rules", [])
            if all(self._evaluate_rule(rule, features) for rule in rules):
                return {k: v for k, v in pattern.items() if k != "match_rules"}

        return self._fallback
