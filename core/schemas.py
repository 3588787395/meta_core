"""
DZH大智慧完整整数属性模型
基于 cell_type_registry.json 和 flow_mode_registry.json 定义的全部属性
"""
from __future__ import annotations
from typing import Optional, List, Dict, Any, Literal, Union, Set, ClassVar, Protocol, runtime_checkable
from pydantic import BaseModel, Field, field_validator, model_validator, PrivateAttr
import json
from pathlib import Path


# ═══════════════════════════════════════════════════
# 注册表动态加载辅助函数
# ═══════════════════════════════════════════════════


class ConfigLoadError(RuntimeError):
    """配置表加载失败时抛出（fail-fast），禁止静默回退硬编码值。"""


_registry_cache = None


def _load_registry() -> Dict[str, Any]:
    """读取 config/cell_type_registry.json 并缓存结果。

    fail-fast：配置缺失或解析失败时抛出 ConfigLoadError，禁止静默回退空字典。
    """
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    registry_path = Path(__file__).parent.parent / "config" / "architecture" / "cell_type_registry.json"
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            _registry_cache = json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        raise ConfigLoadError(
            f"无法加载配置表 {registry_path}: {ex}（fail-fast：禁止静默回退空字典）"
        ) from ex
    return _registry_cache


# ═══════════════════════════════════════════════════
# field_definitions.json 动态加载
# ═══════════════════════════════════════════════════

_field_defs_cache: Optional[Dict[str, Any]] = None
_bit_fields_cache: Optional[Dict[str, Any]] = None


def _load_field_defs() -> Dict[str, Any]:
    """加载 config/field_definitions.json 并缓存结果。

    fail-fast：配置缺失或解析失败时抛出 ConfigLoadError，禁止静默回退空字典。
    """
    global _field_defs_cache, _bit_fields_cache
    if _field_defs_cache is not None:
        return _field_defs_cache
    path = Path(__file__).parent.parent / "config" / "ui" / "field_definitions.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            _field_defs_cache = json.load(f)
        _bit_fields_cache = _field_defs_cache.get("bit_fields", {})
    except (OSError, json.JSONDecodeError) as ex:
        raise ConfigLoadError(
            f"无法加载配置表 {path}: {ex}（fail-fast：禁止静默回退空字典）"
        ) from ex
    return _field_defs_cache


_defaults_cache: Optional[Dict[str, Any]] = None


def _load_defaults() -> Dict[str, Any]:
    """加载 config/defaults.json 并缓存结果。

    fail-fast：配置缺失或解析失败时抛出 ConfigLoadError，禁止静默回退硬编码值。
    """
    global _defaults_cache
    if _defaults_cache is not None:
        return _defaults_cache
    path = Path(__file__).parent.parent / "config" / "runtime" / "defaults.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            _defaults_cache = json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        raise ConfigLoadError(
            f"无法加载配置表 {path}: {ex}（fail-fast：禁止静默回退硬编码值）"
        ) from ex
    return _defaults_cache


def _parse_attr_bits(type_key, attr_int: int) -> Dict[str, bool]:
    """Parse attr int into boolean sub-properties based on bit_fields entries.

    Args:
        type_key: 类型标识（整数 type_id 或 "flow"）
        attr_int: 原始 attr 整数值
    Returns:
        {field_name: bool} 字典
    """
    _load_field_defs()
    bit_fields = _bit_fields_cache.get(str(type_key), {})
    result: Dict[str, bool] = {}
    for name, info in bit_fields.items():
        mask = int(info["mask_hex"], 16)
        result[name] = bool(attr_int & mask)
    return result


def _compose_attr_int(type_key, data: dict) -> int:
    """Compose boolean sub-properties back into attr int.

    Args:
        type_key: 类型标识（整数 type_id 或 "flow"）
        data: 包含 bit field boolean 值的字典
    Returns:
        组合后的 attr 整数值
    """
    _load_field_defs()
    bit_fields = _bit_fields_cache.get(str(type_key), {})
    v = 0
    for name, info in bit_fields.items():
        if data.get(name):
            v |= int(info["mask_hex"], 16)
    return v


# ═══════════════════════════════════════════════════
# DynamicCellModel — 通用 Cell 模型
# ═══════════════════════════════════════════════════

class DynamicCellModel:
    """通用 Cell 模型，根据 field_definitions.json 动态加载字段定义。

    支持 dict 风格访问 (model['key'], model.get('key')) 和属性访问 (model.key)。
    attr 整数字段自动展开为 bit_fields 中定义的 boolean 子属性。
    未知字段存储在 _extra 字典中。
    """

    _COMMON_KEYS = {"id", "type", "pos", "clr", "text", "attr"}

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._extra: Dict[str, Any] = {}
        self._present_attrs: Set[str] = set()

    @classmethod
    def from_dict(cls, data: dict) -> "DynamicCellModel":
        """从原始字典创建 DynamicCellModel。

        自动解析：
        - pos → PositionModel
        - attr → bit_fields boolean 子属性
        - tradeattr → TradeAttrModel
        - tdx_psatt / tdx_func / tdx_spinfo / tdx_stocks → 对应 TDX 模型
        """
        _load_field_defs()
        obj = cls()

        # 确定 cell_type
        cell_type = data.get("type", data.get("cell_type", 0))
        if isinstance(cell_type, str):
            try:
                cell_type = int(cell_type)
            except ValueError:
                pass

        type_str = str(cell_type)
        type_specific = _field_defs_cache.get("type_specific_fields", {}).get(type_str, {})
        known_field_keys = set(type_specific.keys()) | cls._COMMON_KEYS

        # 存储所有输入数据
        for key, value in data.items():
            if key in known_field_keys:
                obj._data[key] = value
            else:
                obj._extra[key] = value

        # 确保 type 和 cell_type 都设置
        obj._data["cell_type"] = cell_type
        if "type" not in obj._data:
            obj._data["type"] = cell_type

        # 解析 position — 支持三种输入格式：
        #   1. position=<PositionModel> 对象（直接使用）
        #   2. pos=<tuple> (x1,y1,x2,y2) 元组
        #   3. pos=<str> "x1,y1,x2,y2" 字符串
        position_obj = data.get("position")
        if isinstance(position_obj, PositionModel):
            obj._data["position"] = position_obj
        else:
            pos_val = data.get("pos", "")
            if isinstance(pos_val, tuple) and len(pos_val) == 4:
                x1, y1, x2, y2 = pos_val
                obj._data["position"] = PositionModel(x=x1, y=y1, width=x2 - x1, height=y2 - y1)
            elif pos_val and isinstance(pos_val, str):
                obj._data["position"] = PositionModel.from_pos_str(pos_val)
            else:
                obj._data["position"] = PositionModel()

        # 解析 attr bits
        attr_int = data.get("attr", 0)
        if isinstance(attr_int, str):
            try:
                attr_int = int(attr_int)
            except ValueError:
                attr_int = 0
        bit_props = _parse_attr_bits(cell_type, attr_int)
        for name, val in bit_props.items():
            obj._data[name] = val

        # 处理嵌套对象: tradeattr
        tradeattr_data = data.get("tradeattr")
        if tradeattr_data and isinstance(tradeattr_data, dict):
            obj._data["tradeattr"] = TradeAttrModel.from_dict(tradeattr_data)

        # 处理嵌套对象（表驱动，替代重复 if/isinstance 块）
        # _NESTED_MODELS 定义在模块级（TDX 模型类之后），运行时按字段名查表解析
        for _field, (_ModelCls, _is_list) in _NESTED_MODELS.items():
            _val = data.get(_field)
            if _is_list:
                if _val and isinstance(_val, list):
                    obj._data[_field] = [_ModelCls.from_dict(s) if isinstance(s, dict) else s for s in _val]
            else:
                if _val and isinstance(_val, dict):
                    obj._data[_field] = _ModelCls.from_dict(_val)
                elif isinstance(_val, _ModelCls):
                    obj._data[_field] = _val

        obj._present_attrs = set(data.keys())

        return obj

    # ── dict-style access ──────────────────────────────────────

    def __getitem__(self, key):
        if key in self._data:
            return self._data[key]
        if key in self._extra:
            return self._extra[key]
        raise KeyError(key)

    def __setitem__(self, key, value):
        self._data[key] = value

    def __contains__(self, key):
        return key in self._data or key in self._extra

    def get(self, key, default=None):
        """dict-style get，支持默认值。"""
        if key in self._data:
            return self._data[key]
        if key in self._extra:
            return self._extra[key]
        return default

    def keys(self):
        """返回所有数据键的视图。"""
        return set(self._data.keys()) | set(self._extra.keys())

    # ── attribute-style access ─────────────────────────────────

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        if name in self._data:
            return self._data[name]
        if name in self._extra:
            return self._extra[name]
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def __setattr__(self, name, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
        else:
            self._data[name] = value

    # ── serialization ──────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """序列化回字典，将 bit fields 重新组合成 attr int。"""
        result = {}
        result.update(self._extra)
        result.update(self._data)

        cell_type = self._data.get("cell_type", self._data.get("type", 0))

        # 重新组合 attr
        attr_int = _compose_attr_int(cell_type, self._data)
        result["attr"] = attr_int

        # 转换 position 回 pos 字符串
        pos = self._data.get("position")
        if isinstance(pos, PositionModel):
            result["pos"] = pos.to_pos_str()

        # 移除展开的 bit fields（已组合到 attr 中）
        _load_field_defs()
        bit_fields = _bit_fields_cache.get(str(cell_type), {})
        for name in bit_fields:
            result.pop(name, None)

        # 移除内部字段
        result.pop("cell_type", None)

        return result

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """兼容 Pydantic model_dump() 接口，委托 to_dict()。"""
        return self.to_dict()

    def __repr__(self):
        ct = self._data.get("cell_type", "?")
        cid = self._data.get("id", "?")
        return f"<DynamicCellModel type={ct} id={cid}>"


# ═══════════════════════════════════════════════════
# DynamicFlowModel — 通用 Flow 模型
# ═══════════════════════════════════════════════════

# ═══════════════════════════════════════════════════
# flow_mode_registry.json 动态加载
# ═══════════════════════════════════════════════════

_flow_mode_registry_cache: Optional[Dict[str, Any]] = None


def _load_flow_mode_registry() -> Dict[str, Any]:
    """加载 config/flow_mode_registry.json 并缓存结果。

    fail-fast：配置缺失或解析失败时抛出 ConfigLoadError，禁止静默回退空字典。
    """
    global _flow_mode_registry_cache
    if _flow_mode_registry_cache is not None:
        return _flow_mode_registry_cache
    path = Path(__file__).parent.parent / "config" / "architecture" / "flow_mode_registry.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            _flow_mode_registry_cache = json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        raise ConfigLoadError(
            f"无法加载配置表 {path}: {ex}（fail-fast：禁止静默回退空字典）"
        ) from ex
    return _flow_mode_registry_cache


def _build_flow_mode_map() -> Dict[int, str]:
    """从 flow_mode_registry.json 的 resolve_rules 构建 mode_index -> mode_name 映射。

    fail-fast：resolve_rules 缺失时抛出 ConfigLoadError，禁止回退硬编码字典。
    """
    registry = _load_flow_mode_registry()
    resolve_rules = registry.get("resolve_rules", [])
    if not resolve_rules:
        raise ConfigLoadError(
            "flow_mode_registry.json 缺少 resolve_rules 段（fail-fast：禁止回退硬编码 flow mode 字典）"
        )
    # 按 priority 排序，分配递增索引
    sorted_rules = sorted(resolve_rules, key=lambda r: r.get("priority", 99))
    mode_map: Dict[int, str] = {}
    for idx, rule in enumerate(sorted_rules):
        mode_name = rule.get("mode", "pass_through")
        mode_map[idx] = mode_name
    return mode_map


_FLOW_MODE_MAP: Dict[int, str] = _build_flow_mode_map()


def _resolve_flow_mode_from_bits(data: Dict[str, Any]) -> str:
    """根据 flow_mode_registry.json 的 resolve_rules 解析流转模式（单一真相源实现）。

    流程：
    1. 加载 flow_mode_registry.json
    2. 按 priority 排序 resolve_rules
    3. 对每条规则，检查 check_bits 全部为 True 且 check_bits_absent 全部为 False
    4. 返回第一条匹配规则的 mode 名称

    fail-fast：resolve_rules 缺失时抛出 ConfigLoadError，禁止回退 7 层 if 硬编码。
    """
    registry = _load_flow_mode_registry()
    resolve_rules = registry.get("resolve_rules", [])
    if not resolve_rules:
        raise ConfigLoadError(
            "flow_mode_registry.json 缺少 resolve_rules 段（fail-fast：禁止回退 7 层 if 硬编码）"
        )

    sorted_rules = sorted(resolve_rules, key=lambda r: r.get("priority", 99))
    for rule in sorted_rules:
        check_bits = rule.get("check_bits", [])
        check_bits_absent = rule.get("check_bits_absent", [])

        all_present = all(data.get(bit_name) for bit_name in check_bits)
        if not all_present:
            continue

        all_absent = all(not data.get(bit_name) for bit_name in check_bits_absent)
        if all_present and all_absent:
            return rule.get("mode", "pass_through")

    return "pass_through"


# ═══════════════════════════════════════════════════
# 字段别名归一化表（DynamicFlowModel.from_dict 表驱动）
# ═══════════════════════════════════════════════════
# canonical_field -> [(alias, needs_dict_extract), ...]
# 首个匹配的别名生效；canonical 已存在时不覆盖。
# needs_dict_extract=True 时从 {"node_id": ...} / {"id": ...} / 非空 str 提取 cell id，
# 并同步填充短别名（from/to）以兼容旧代码。
_FIELD_ALIASES: Dict[str, List[tuple]] = {
    "from_cell_id": [("from", False), ("source", True)],
    "to_cell_id": [("to", False), ("target", True)],
    "begin_type": [("begin", False)],
    "begin_param": [("begint", False)],
    "end_type": [("end", False)],
    "end_param": [("endt", False)],
    "interval_sec": [("interval", False)],
}


class DynamicFlowModel:
    """通用 Flow 模型，根据 field_definitions.json 的 flow_fields 动态加载字段定义。

    支持 dict 风格访问和属性访问。
    attr 整数字段自动展开为 bit_fields.flow 中定义的 boolean 子属性。
    """

    _FLOW_KEYS = {"from", "to", "attr", "begin", "begint", "end", "endt",
                  "interval", "clr", "mid", "count"}

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._extra: Dict[str, Any] = {}
        self._present_attrs: Set[str] = set()

    @classmethod
    def from_dict(cls, data: dict) -> "DynamicFlowModel":
        """从原始字典创建 DynamicFlowModel。"""
        _load_field_defs()
        obj = cls()

        flow_fields = _field_defs_cache.get("flow_fields", {})
        known_keys = set(flow_fields.keys()) | cls._FLOW_KEYS

        for key, value in data.items():
            if key in known_keys:
                obj._data[key] = value
            else:
                obj._extra[key] = value

        # 归一化字段别名（表驱动，替代密集 if/elif 链）
        # _FIELD_ALIASES: canonical -> [(alias, needs_dict_extract), ...]
        for canonical, alias_specs in _FIELD_ALIASES.items():
            if canonical in obj._data:
                continue  # canonical 已存在，不覆盖
            for alias, extract in alias_specs:
                if alias not in obj._data:
                    continue
                val = obj._data[alias]
                if extract:
                    # source/target: 从 dict 或非空 str 提取 cell id
                    if isinstance(val, dict):
                        resolved = val.get("node_id", "") or val.get("id", "")
                    elif isinstance(val, str) and val:
                        resolved = val
                    else:
                        continue
                    obj._data[canonical] = resolved
                    # 兼容旧代码：同步填充短别名（from/to）
                    obj._data[canonical.split("_")[0]] = resolved
                else:
                    obj._data[canonical] = val
                break

        # 解析 flow attr bits
        attr_int = data.get("attr", 0)
        if isinstance(attr_int, str):
            try:
                attr_int = int(attr_int)
            except ValueError:
                attr_int = 0
        bit_props = _parse_attr_bits("flow", attr_int)
        for name, val in bit_props.items():
            obj._data[name] = val

        obj._present_attrs = set(data.keys())

        return obj

    @classmethod
    def from_int(cls, attr_int: int) -> "DynamicFlowModel":
        """兼容旧 FlowAttrBitsModel.from_int() 接口。

        从 int 解析 flow attr bits，返回包含 boolean 属性的 DynamicFlowModel。
        """
        obj = cls()
        bit_props = _parse_attr_bits("flow", attr_int)
        obj._data.update(bit_props)
        obj._data["raw_attr"] = attr_int
        return obj

    def model_dump(self, exclude=None) -> Dict[str, Any]:
        """兼容旧 pydantic model_dump() 接口。

        Args:
            exclude: 要排除的字段名集合
        """
        exclude = exclude or set()
        return {k: v for k, v in self._data.items() if k not in exclude}

    def to_int(self) -> int:
        """将当前 boolean 属性重新组合成 attr int。"""
        return _compose_attr_int("flow", self._data)

    # ── mode properties ────────────────────────────────────────

    def identify_mode(self) -> str:
        """根据 flow_mode_registry.json 的 resolve_rules 识别流转模式。"""
        return _resolve_flow_mode_from_bits(self._data)

    @property
    def mode_name(self) -> str:
        """流转模式名称。"""
        return self.identify_mode()

    @property
    def is_inflow(self) -> bool:
        """是否为入流（直通模式）。"""
        return self.mode_name == "pass_through"

    @property
    def is_outflow(self) -> bool:
        """是否为出流（非直通模式）。"""
        return self.mode_name != "pass_through"

    # ── dict-style access ──────────────────────────────────────

    def __getitem__(self, key):
        if key in self._data:
            return self._data[key]
        if key in self._extra:
            return self._extra[key]
        raise KeyError(key)

    def __setitem__(self, key, value):
        self._data[key] = value

    def __contains__(self, key):
        return key in self._data or key in self._extra

    def get(self, key, default=None):
        if key in self._data:
            return self._data[key]
        if key in self._extra:
            return self._extra[key]
        return default

    def keys(self):
        return set(self._data.keys()) | set(self._extra.keys())

    # ── attribute-style access ─────────────────────────────────

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        if name in self._data:
            return self._data[name]
        if name in self._extra:
            return self._extra[name]
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def __setattr__(self, name, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
        else:
            self._data[name] = value

    # ── serialization ──────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """序列化回字典，将 bit fields 重新组合成 attr int。"""
        result = {}
        result.update(self._extra)
        result.update(self._data)

        # 重新组合 attr
        attr_int = _compose_attr_int("flow", self._data)
        result["attr"] = attr_int

        # 移除展开的 bit fields
        _load_field_defs()
        bit_fields = _bit_fields_cache.get("flow", {})
        for name in bit_fields:
            result.pop(name, None)

        # 移除内部兼容字段
        for key in ("from_cell_id", "to_cell_id", "begin_type", "begin_param",
                     "end_type", "end_param", "interval_sec", "raw_attr"):
            result.pop(key, None)

        return result

    def __repr__(self):
        fid = self._data.get("from", "?")
        tid = self._data.get("to", "?")
        return f"<DynamicFlowModel {fid}->{tid}>"


# FlowModel 别名：向后兼容 tdx_exporter 等模块的 import
FlowModel = DynamicFlowModel

# FlowAttrBitsModel 别名：向后兼容 runtime_simulator 等模块的 import
# DynamicFlowModel 已包含 delete_source/keep_source/clear_dest_first 等位标志属性
FlowAttrBitsModel = DynamicFlowModel

# StatePoolCellModel / ConditionCellModel 别名：向后兼容 runtime_simulator
# DynamicCellModel 根据 cell_type 自动加载对应字段，type=200 即状态池，type=201 即条件
StatePoolCellModel = DynamicCellModel
ConditionCellModel = DynamicCellModel
CandidateCellModel = DynamicCellModel
Type203CellModel = DynamicCellModel
DiscardCellModel = DynamicCellModel
LabelCellModel = DynamicCellModel
ContainerCellModel = DynamicCellModel
StateColumnModel = DynamicCellModel
ArrowCellModel = DynamicCellModel


# ═══════════════════════════════════════════════════
# CellNNNAttrBitsModel 兼容类
# ═══════════════════════════════════════════════════

class _CellAttrBitsCompat:
    """兼容旧 Cell200AttrBitsModel / Cell201AttrBitsModel 接口的适配器。
    提供 from_int() 和 model_dump() 方法，内部委托 DynamicCellModel。"""

    _CELL_TYPE: int = 0  # 子类覆盖

    def __init__(self, **kwargs):
        self._data = kwargs
        self.raw_attr = kwargs.get("raw_attr", kwargs.get("attr", 0))

    @classmethod
    def from_int(cls, attr_int: int):
        model = DynamicCellModel.from_dict({"attr": attr_int, "type": cls._CELL_TYPE})
        obj = cls(**model._data, raw_attr=attr_int)
        return obj

    def model_dump(self, exclude=None):
        exclude = exclude or set()
        return {k: v for k, v in self._data.items() if k not in exclude}

    def to_int(self) -> int:
        """从当前布尔属性值重新组合为位标志整数。"""
        return _compose_attr_int(self._CELL_TYPE, self._data)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return self._data.get(name)

    def __repr__(self):
        return f"<{type(self).__name__} raw_attr={self.raw_attr}>"


class Cell200AttrBitsModel(_CellAttrBitsCompat):
    """兼容旧 Cell200AttrBitsModel（type=200 状态池 attr 位标志）"""
    _CELL_TYPE = 200


class Cell201AttrBitsModel(_CellAttrBitsCompat):
    """兼容旧 Cell201AttrBitsModel（type=201 转移条件 attr 位标志）"""
    _CELL_TYPE = 201


class Cell202AttrBitsModel(_CellAttrBitsCompat):
    """兼容旧 Cell202AttrBitsModel（type=202 备选池 attr 位标志）"""
    _CELL_TYPE = 202


# ═══════════════════════════════════════════════════
# 基础模型（保留）
# ═══════════════════════════════════════════════════

class PositionModel(BaseModel):
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @classmethod
    def from_pos_str(cls, pos_str: str) -> PositionModel:
        parts = pos_str.split(",")
        if len(parts) != 4:
            return cls()
        return cls(x=int(parts[0]), y=int(parts[1]), width=int(parts[2]) - int(parts[0]), height=int(parts[3]) - int(parts[1]))

    def to_pos_str(self) -> str:
        return f"{self.x},{self.y},{self.x + self.width},{self.y + self.height}"

    def to_tuple(self) -> tuple:
        return (self.x, self.y, self.x + self.width, self.y + self.height)


class TradeAttrModel(BaseModel):
    accountno: Optional[str] = None
    entertradetype: Optional[str] = None
    enterrate: Optional[str] = None
    enterbuytype: Optional[str] = None
    enterbuyvalue: Optional[str] = None
    enterbuypricetype: Optional[str] = None
    enterselltype: Optional[str] = None
    entersellvalue: Optional[str] = None
    entersellpricetype: Optional[str] = None
    leavetradetype: Optional[str] = None
    leaverate: Optional[str] = None
    leavebuytype: Optional[str] = None
    leavebuyvalue: Optional[str] = None
    leavebuypricetype: Optional[str] = None
    leaveselltype: Optional[str] = None
    leavesellvalue: Optional[str] = None
    leavesellpricetype: Optional[str] = None
    buycontrolnbhs: Optional[str] = None
    buycontrolmbsc: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict) -> TradeAttrModel:
        return cls(**{k: v for k, v in data.items() if k in cls.model_fields})


def _get_action_type_map() -> Dict[int, str]:
    """从 defaults.json 加载 action_type_map（fail-fast，禁止硬编码回退表）。"""
    atm = _load_defaults().get("action_type_map", {})
    return {int(k): v for k, v in atm.items()}


def _get_action_unit_map() -> Dict[int, str]:
    """从 defaults.json 加载 action_unit_map（fail-fast，禁止硬编码回退表）。"""
    aum = _load_defaults().get("action_unit_map", {})
    return {int(k): v for k, v in aum.items()}


class ActionModel(BaseModel):
    action_type: Literal["none", "buy_amount", "buy_shares", "sell_shares", "unknown"] = "none"
    param: int = 0
    param_unit: str = ""
    raw: int = 0

    @classmethod
    def from_raw(cls, raw: int) -> ActionModel:
        action_type_map = _get_action_type_map()
        action_type = action_type_map.get(raw, "unknown")
        param = raw if raw in action_type_map else 0
        unit_map = _get_action_unit_map()
        param_unit = unit_map.get(raw, "")
        return cls(action_type=action_type, param=param, param_unit=param_unit, raw=raw)


class StockSnapshotModel(BaseModel):
    label: str = ""
    t: str = ""
    p: str = ""
    tid: Optional[str] = None
    setcode: Optional[int] = None
    code: Optional[str] = None
    indate: Optional[str] = None
    intime: Optional[str] = None
    inprice: Optional[str] = None
    income: Optional[str] = None
    now: Optional[str] = None
    rise: Optional[str] = None
    volume: Optional[str] = None
    maxrate: Optional[str] = None
    maxperiod: Optional[str] = None
    maxtime: Optional[str] = None
    maxprice: Optional[str] = None
    idaynum: Optional[str] = None


StkEntry = StockSnapshotModel


# ═══════════════════════════════════════════════════
# CellType 与 parse_cell（全部使用 DynamicCellModel）
# ═══════════════════════════════════════════════════

CellType = DynamicCellModel

_CELL_TYPE_MAP_FALLBACK: Dict[int, type] = {
    # 所有类型统一使用 DynamicCellModel（通用模型），未知类型亦同，
    # 故兜底表只需 1 条哨兵条目；parse_cell 对未命中类型回退到 DynamicCellModel。
    0: DynamicCellModel,
}


def _build_cell_type_map_from_registry() -> Dict[int, type]:
    """从 cell_type_registry.json 读取类型定义，构建 type_id -> model_class 映射。"""
    registry = _load_registry()
    result: Dict[int, type] = {}
    for type_id_str, type_def in registry.get("types", {}).items():
        # Skip non-numeric keys like "tdx_0", "tdx_3", etc.
        try:
            type_id = int(type_id_str)
        except (ValueError, TypeError):
            continue
        model_class_name = type_def.get("model_class")
        if model_class_name:
            # 所有类型统一使用 DynamicCellModel
            cls = globals().get(model_class_name)
            if cls is not None:
                result[type_id] = cls
    return result


_CELL_TYPE_MAP: Dict[int, type] = {}


def _init_cell_type_map() -> None:
    """初始化 _CELL_TYPE_MAP：优先从注册表构建，兜底使用硬编码。"""
    global _CELL_TYPE_MAP
    reg_map = _build_cell_type_map_from_registry()
    if reg_map:
        # 如果注册表中有可用的映射，合并（注册表优先）
        merged = dict(_CELL_TYPE_MAP_FALLBACK)
        merged.update(reg_map)
        _CELL_TYPE_MAP = merged
    else:
        _CELL_TYPE_MAP = dict(_CELL_TYPE_MAP_FALLBACK)


_init_cell_type_map()


def parse_cell(data: Dict[str, Any]) -> CellType:
    ct = data.get("cell_type", data.get("type"))
    if ct is not None and ct in _CELL_TYPE_MAP:
        return _CELL_TYPE_MAP[ct].from_dict(data)
    # 未知类型统一使用 DynamicCellModel（通用模型），不再抛出 ValueError
    return DynamicCellModel.from_dict(data)


# ═══════════════════════════════════════════════════
# PoolMetaModel（更新为使用 DynamicFlowModel）
# ═══════════════════════════════════════════════════

class PoolMetaModel(BaseModel):
    pool_type: str = "ss-pool"
    ver: str = "1.0"
    mode: str = "1"
    nextid: int = 0
    backcolor: int = 16777216
    ency: Optional[int] = None
    warning: Optional[str] = None
    system: Optional[str] = None

    cells: List[Any] = Field(default_factory=list)
    flows: List[Any] = Field(default_factory=list)
    stocks: List[StockSnapshotModel] = Field(default_factory=list)
    trades: List[Dict] = Field(default_factory=list)
    opentrades: List[Dict] = Field(default_factory=list)

    _present_attrs: Set[str] = PrivateAttr(default_factory=set)

    def get_stats(self) -> Dict[str, Any]:
        type_counts: Dict[int, int] = {}
        for c in self.cells:
            ct = c.get("cell_type", c.get("type", 0)) if isinstance(c, DynamicCellModel) else getattr(c, "cell_type", 0)
            type_counts[ct] = type_counts.get(ct, 0) + 1
        return {
            "total_cells": len(self.cells),
            "total_flows": len(self.flows),
            "cell_types": type_counts,
            "state_pools": type_counts.get(200, 0),
            "conditions": type_counts.get(201, 0),
            "candidates": type_counts.get(202, 0),
        }

    def get_topology_info(self) -> Dict[str, Any]:
        inflows: List[str] = []
        outflows: List[str] = []
        for f in self.flows:
            if f.is_inflow:
                inflows.append(f"{f.from_cell_id}->{f.to_cell_id}")
            elif f.is_outflow:
                outflows.append(f"{f.from_cell_id}->{f.to_cell_id}")
        cell_ids = {}
        for c in self.cells:
            if isinstance(c, DynamicCellModel):
                cell_ids[c.get("id", "?")] = c.get("cell_type", c.get("type", "?"))
            else:
                cell_ids[getattr(c, "id", "?")] = getattr(c, "cell_type", "?")
        return {
            "nodes": cell_ids,
            "inflow_edges": inflows,
            "outflow_edges": outflows,
            "flow_modes": {f"{f.from_cell_id}->{f.to_cell_id}": f.mode_name for f in self.flows},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PoolMetaModel:
        cells_raw = data.get("cells") or data.get("nodes", [])
        cells = []
        for c in cells_raw:
            if isinstance(c, dict):
                cells.append(parse_cell(c))
            else:
                cells.append(c)
        flows_raw = data.get("flows") or data.get("edges", [])
        flows = [DynamicFlowModel.from_dict(f) if isinstance(f, dict) else f for f in flows_raw]
        kwargs = {k: v for k, v in data.items() if k not in ("cells", "flows", "nodes", "edges")}
        kwargs["cells"] = cells
        kwargs["flows"] = flows
        obj = cls(**kwargs)
        return obj


# ═══════════════════════════════════════════════════
# TDX (通达信) 股票池数据模型
# ═══════════════════════════════════════════════════

def _get_setcode_map():
    scm = _load_defaults().get("setcode_map", {})
    return {int(k): v for k, v in scm.items()}

# Backward-compatible aliases for external imports
SETCODE_MAP = _get_setcode_map()
SETCODE_REVERSE = {v: k for k, v in SETCODE_MAP.items()}

# TDX↔DZH 类型映射单一真相源：config/architecture/dzh_type_map.json
# 读取统一通过 ConfigStore.get_table("dzh_type_map")（见 converters.py 等调用方）。


# ═══════════════════════════════════════════════════
# 表驱动 to_xml_attrs 通用 Mixin
# ═══════════════════════════════════════════════════
# 子类只需声明 _XML_FIELDS 列表，即可自动生成 XML 属性字典：
#   str 字段保持原值，float 字段格式化为 :.6f，其它字段 str() 转换。
class _XmlAttrMixin:
    _XML_FIELDS: ClassVar[List[str]] = []

    def to_xml_attrs(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for field in self._XML_FIELDS:
            val = getattr(self, field)
            if isinstance(val, str):
                result[field] = val
            elif isinstance(val, float):
                result[field] = f"{val:.6f}"
            else:
                result[field] = str(val)
        return result

    @classmethod
    def from_dict(cls, data: Dict) -> "_XmlAttrMixin":
        """通用 ``from_dict``：按 ``model_fields`` 过滤未知键后构造实例。

        底层运行逻辑洞察（Code = Data + Dispatcher）：5 个 TDX 叶子模型
        （Func/Psatt/Spinfo/Stk/Flow）的 ``from_dict`` 逐字相同，仅返回类型注解不同。
        提升至 mixin 后子类零实现 ``from_dict``，新增模型自动获得该能力。
        ``TdxCellModel`` / ``TdxPoolMetaModel`` 含嵌套解析，保留各自 ``from_dict``。
        """
        known = set(cls.model_fields.keys())
        return cls(**{k: v for k, v in data.items() if k in known})


class TdxFuncModel(_XmlAttrMixin, BaseModel):
    nset: int = 1
    ntjindexno: int = 0
    accode: str = ""
    nperiod: int = 4
    nfirst: int = 0
    cfirst: str = ""
    noperate: int = 0
    nsecond: int = -1
    csecond: str = ""
    fsecond: float = 0.0
    nbeginday: int = 0
    nendday: int = 0
    bnost: int = 0
    bnotp: int = 0
    bnotq: int = 0
    nperiodnum: int = 0
    # 公式计算扩展字段（用于自定义指标编号时指定公式名和参数）
    formula_name: str = ""
    formula_arg: str = ""
    return_count: int = 3

    _XML_FIELDS: ClassVar[List[str]] = ["nset", "ntjindexno", "accode", "nperiod", "nfirst", "cfirst",
                   "noperate", "nsecond", "csecond", "fsecond", "nbeginday", "nendday",
                   "bnost", "bnotp", "bnotq", "nperiodnum"]


class TdxPsattModel(_XmlAttrMixin, BaseModel):
    """TDX状态池属性模型（psatt子元素），14个字段。

    基于通达信客户端「股票池状态属性」对话框 + 10个控制变量XML样本验证。
    核心发现：ndeltype 是时间单位(0=天,1=小时,2=分钟,3=秒)，不是删除策略。

    ⚠️ DZH vs TDX 范围差异（✅ 已确认 2026-06-10）：
      - TDX ndeltype: 0=天, 1=小时, 2=分钟, 3=秒（本模型，validator限制0~3）
      - DZH deltype:  0=自然日, 1=交易日, 2=小时, 3=分钟, 4=秒（多一个值，见runtime_simulator.py DelType枚举）
      两者语义不同！DZH的deltype=0/1都是"天"但含义不同（自然日vs交易日），
      TDX将两者合并为ndeltype=0。转换时需注意映射关系。
    """
    bdel: int = 0          # 是否启用自动删除 (0=关闭, 1=启用)
    ndelnum: int = 0        # 时间数量（与ndeltype配合，如3天/18小时/5分钟）
    ndeltype: int = 0       # 时间单位: 0=天(×86400s), 1=小时(×3600s), 2=分钟(×60s), 3=秒(×1s), 4=秒(DZH兼容,×1s)
    baimpool: int = 0       # 设置为目标池标记 (0/1)
    bsound: int = 0         # 启用声音预警 (0/1)
    nsoundtype: int = 0     # 声音类型: 0=系统声音, 1=自定义WAV文件
    nsyssound: int = 0      # 老版系统声音编号（V6.x，新版已被nsoundtype+soundfile替代）
    soundfile: str = ""     # 自定义声音文件完整路径（nsoundtype=1时有效）
    btip: int = 0           # 右下角弹窗提示 (0/1)
    bsavetoblock: int = 0   # 自动保存到板块 (0/1)
    blockfile: str = ""     # 目标板块名称（用户自建板块短名，如TEST/E2ETEST/TJQRM）
    bclearblock: int = 0    # 写入模式: 0=追加, 1=清空后覆盖
    bsavehis: int = 0       # 保存历史入池记录 (0/1)

    @field_validator('ndeltype')
    @classmethod
    def _validate_ndeltype(cls, v: int) -> int:
        if v not in (0, 1, 2, 3, 4):
            raise ValueError(f'ndeltype must be 0(days)/1(hours)/2(minutes)/3(seconds)/4(seconds_DZH), got {v}')
        return v

    _XML_FIELDS: ClassVar[List[str]] = ["bdel", "ndelnum", "ndeltype", "baimpool", "bsound", "nsoundtype",
                   "nsyssound", "soundfile", "btip", "bsavetoblock", "blockfile",
                   "bclearblock", "bsavehis"]


class TdxSpinfoModel(_XmlAttrMixin, BaseModel):
    """备选池（type=7）spinfo 子元素模型。

    Attributes:
        type: 候选池类型枚举值。
            0 = 自设监控品种；
            1 = 沪深300+中证500；
            2 = 所有A股；
            3 = 自选股；
            4 = 自定义板块；
            5 = 板块指数；
            6 = ETF基金；
            7 = 可转债。
        customblockname: 自定义板块名称（当 type=4 时有效）。
        size: 候选股票数量。
        market: 市场选择（"SZ"/"SH"/"SZ,SH"），从stk的setcode推断或spinfo语义推导。
        sector_type: 板块类型细分（0=普通, 1=行业板块, 2=概念板块, 3=地区板块）。
    """
    type: int = 0
    customblockname: str = ""
    size: int = 0
    market: str = ""
    sector_type: int = 0

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: int) -> int:
        """校验 type 只能是合法枚举值：0 ~ 7。

        0=自设监控品种; 1=沪深300+中证500; 2=所有A股; 3=自选股;
        4=自定义板块; 5=板块指数; 6=ETF基金; 7=可转债。
        """
        allowed = {0, 1, 2, 3, 4, 5, 6, 7}
        if v not in allowed:
            raise ValueError(f"spinfo.type 必须为 {sorted(allowed)} 之一，实际值为 {v}")
        return v

    _XML_FIELDS: ClassVar[List[str]] = ["type", "customblockname", "size", "market", "sector_type"]


class TdxStkModel(_XmlAttrMixin, BaseModel):
    setcode: int = 0
    code: str = ""
    indate: str = ""
    intime: str = ""
    inprice: str = ""
    income: str = "0.00"
    now: str = ""
    rise: str = ""
    volume: str = ""
    maxrate: str = "0.00"
    maxperiod: str = "0"
    maxtime: str = "0"
    maxprice: str = "0.00"
    idaynum: str = "0"

    @property
    def tq_code(self) -> str:
        scm = _get_setcode_map()
        if self.setcode and self.setcode in scm:
            suffix = scm[self.setcode]
        else:
            # Infer from code's first digit when setcode is 0 or missing
            c = str(self.code or "0")
            if c.startswith('6'):
                suffix = "SH"
            elif c.startswith(('0', '3')):
                suffix = "SZ"
            elif c.startswith(('8', '4')):
                suffix = "BJ"
            else:
                suffix = "SZ"  # default
        return f"{self.code}.{suffix}"

    _XML_FIELDS: ClassVar[List[str]] = ["setcode", "code", "indate", "intime", "inprice", "income", "now",
                   "rise", "volume", "maxrate", "maxperiod", "maxtime", "maxprice", "idaynum"]


# ═══════════════════════════════════════════════════
# 嵌套模型解析表（DynamicCellModel.from_dict 表驱动）
# ═══════════════════════════════════════════════════
# field_name -> (ModelClass, is_list)
# is_list=True 表示该字段是模型列表（如 tdx_stocks），需逐元素解析
_NESTED_MODELS: Dict[str, tuple] = {
    "tdx_psatt": (TdxPsattModel, False),
    "tdx_func": (TdxFuncModel, False),
    "tdx_spinfo": (TdxSpinfoModel, False),
    "tdx_stocks": (TdxStkModel, True),
}


class TdxCellModel(BaseModel):
    id: int = 0
    type: int = 0
    attr: int = 0
    pos_x: int = 0
    pos_y: int = 0
    width: int = 0
    height: int = 0
    clr: int = -1
    clrtext: int = 0
    solid: int = 0
    text: str = ""
    disabled: bool = False       # 条件节点停止运算标志（type=3有效）
    show_row_num: bool = False   # 状态池显示行号标志（type=8有效）
    func: Optional[TdxFuncModel] = None
    psatt: Optional[TdxPsattModel] = None
    spinfo: Optional[TdxSpinfoModel] = None
    stks: List[TdxStkModel] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict) -> TdxCellModel:
        known = set(cls.model_fields.keys())
        kws = {k: v for k, v in data.items() if k in known}
        if "func" in data and isinstance(data["func"], dict):
            kws["func"] = TdxFuncModel.from_dict(data["func"])
        if "psatt" in data and isinstance(data["psatt"], dict):
            kws["psatt"] = TdxPsattModel.from_dict(data["psatt"])
        if "spinfo" in data and isinstance(data["spinfo"], dict):
            kws["spinfo"] = TdxSpinfoModel.from_dict(data["spinfo"])
        if "stks" in data and isinstance(data["stks"], list):
            kws["stks"] = [TdxStkModel.from_dict(s) if isinstance(s, dict) else s for s in data["stks"]]
        return cls(**kws)


class TdxFlowModel(_XmlAttrMixin, BaseModel):
    startid: int = 0
    endid: int = 0
    clr: int = -1
    size: int = 1
    tran: int = 0
    emptyps: int = 0
    starttype: int = 0
    starttime: int = 0
    starttimetype: int = 0
    starttimehms: int = 0
    cxtype: int = 0
    cxtime: int = 0
    cxtimetype: int = 0
    jgtime: int = 0

    @property
    def mode_name(self) -> str:
        return "move" if self.tran == 1 else "copy"

    _XML_FIELDS: ClassVar[List[str]] = ["startid", "endid", "clr", "size", "tran", "emptyps", "starttype",
                   "starttime", "starttimetype", "starttimehms", "cxtype", "cxtime",
                   "cxtimetype", "jgtime"]


class TdxPoolMetaModel(BaseModel):
    nextid: int = 0
    backcolor: int = 16777216
    cells: List[TdxCellModel] = Field(default_factory=list)
    flows: List[TdxFlowModel] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TdxPoolMetaModel:
        cells_raw = data.get("cells", [])
        cells = [TdxCellModel.from_dict(c) if isinstance(c, dict) else c for c in cells_raw]
        flows_raw = data.get("flows", [])
        flows = [TdxFlowModel.from_dict(f) if isinstance(f, dict) else f for f in flows_raw]
        kwargs = {k: v for k, v in data.items() if k not in ("cells", "flows")}
        kwargs["cells"] = cells
        kwargs["flows"] = flows
        return cls(**kwargs)


# ═══════════════════════════════════════════════════
# 边执行步骤协议（EdgeExecutor 表驱动步骤化）
# ═══════════════════════════════════════════════════

class StepResult(BaseModel):
    """步骤执行结果。"""
    should_continue: bool = True
    data: Optional[dict] = None


class StepSpec(BaseModel):
    """步骤规格（编译期产出）。"""
    step_name: str
    enabled: bool = True


@runtime_checkable
class EdgeStep(Protocol):
    """边执行步骤协议。每步实现此接口。"""
    def run(self, ctx: Any) -> StepResult: ...