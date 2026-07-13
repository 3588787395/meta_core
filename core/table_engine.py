"""
表驱动架构核心引擎
==================
统一驱动所有逻辑与渲染的表解析执行引擎。
程序主干仅包含通用的表解析与执行逻辑，不含任何领域知识。

核心设计原则：
1. 所有业务逻辑提取为表中的规则配置
2. 引擎仅包含通用的表解析与执行逻辑
3. UI界面完全由配置表决定
4. 支持热加载与校验
"""

import json
import time
import copy
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

# ─── 配置表加载器 ───────────────────────────────────────────────

class ConfigStore:
    """配置表存储：加载、缓存、校验、热加载"""

    def __init__(self, config_dir: str, storage=None):
        self._config_dir = Path(config_dir)
        self._tables: Dict[str, Dict] = {}
        self._hashes: Dict[str, str] = {}  # 文件hash，用于热加载检测
        self._load_times: Dict[str, float] = {}
        self._validators: Dict[str, Callable] = {}
        self._watch_enabled = False
        self._storage = storage  # Storage 引用，用于版本记录和回滚
        self._schema_validator = None  # SchemaValidator 引用，用于三级校验
        # 分类元数据（Task 2: table_categories.json）
        self._categories: List[Dict] = []
        self._category_consistency: Dict[str, Any] = {
            "missing_on_disk": [],
            "extra_on_disk": [],
            "consistent": False,
        }
        # 表锁定状态（Task 13: .locks.json）
        self._locks: Dict[str, Dict] = {}

    def load_all(self) -> None:
        """加载所有配置表"""
        for path in sorted(self._config_dir.glob("*.json")):
            name = path.stem
            self._load_table(name, path)
        self._load_categories()
        self._load_locks()

    def _load_categories(self) -> None:
        """加载 table_categories.json 并与 config 目录实际 .json 文件做一致性校验。"""
        # SubTask 2.1: 加载分类元数据
        self._categories = []
        self._category_consistency = {
            "missing_on_disk": [],
            "extra_on_disk": [],
            "consistent": False,
        }
        categories_path = self._config_dir / "table_categories.json"
        try:
            with open(categories_path, "r", encoding="utf-8") as f:
                cat_data = json.load(f)
            self._categories = cat_data.get("categories", [])
        except (OSError, json.JSONDecodeError) as ex:
            logger.warning("Failed to load table_categories.json: %s", ex)
            return

        # SubTask 2.2: 一致性校验 —— 分类声明 vs 磁盘实际 .json 文件
        declared_tables = set()
        for cat in self._categories:
            for t in cat.get("tables", []):
                if "name" in t:
                    declared_tables.add(t["name"])

        system_files = {"table_categories", ".locks"}
        actual_tables = set()
        try:
            for path in self._config_dir.iterdir():
                if path.suffix != ".json":
                    continue
                base = path.stem
                if base in system_files or base.startswith("."):
                    continue
                actual_tables.add(base)
        except OSError as ex:
            logger.warning("Failed to list config dir for consistency check: %s", ex)

        missing_on_disk = sorted(declared_tables - actual_tables)
        extra_on_disk = sorted(actual_tables - declared_tables)
        self._category_consistency = {
            "missing_on_disk": missing_on_disk,
            "extra_on_disk": extra_on_disk,
            "consistent": len(missing_on_disk) == 0 and len(extra_on_disk) == 0,
        }
        if missing_on_disk:
            logger.warning("Tables declared in categories but missing on disk: %s", missing_on_disk)
        if extra_on_disk:
            logger.warning("Tables on disk but not in categories: %s", extra_on_disk)

    def _load_table(self, name: str, path: Path) -> None:
        """加载单个配置表并校验"""
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            file_hash = hashlib.md5(raw.encode()).hexdigest()

            # 校验
            errors = self._validate_table(name, data)
            if errors:
                logger.warning(f"配置表 {name} 校验警告: {errors}")

            self._tables[name] = data
            self._hashes[name] = file_hash
            self._load_times[name] = time.time()
            logger.info(f"配置表 {name} 加载成功 (hash={file_hash[:8]})")
        except Exception as e:
            logger.error(f"配置表 {name} 加载失败: {e}")

    def _validate_table(self, name: str, data: Dict) -> List[str]:
        """校验配置表结构"""
        errors = []

        # 基本结构校验
        if not isinstance(data, dict):
            errors.append("根元素必须是dict")
            return errors

        if "version" not in data:
            errors.append("缺少version字段")

        # 不校验元数据表自身
        if name == "table_schemas":
            return errors

        # 使用table_schemas中的定义进行深度校验
        schema = self._tables.get("table_schemas", {}).get("schemas", {}).get(name)
        if schema:
            required = schema.get("required_fields", [])
            # 对于包含集合包装的表（如ui_layouts的layouts字典），
            # required_fields应校验集合中的每个条目，而非顶层
            collection_key = self._get_collection_key(name, data)
            if collection_key and collection_key in data:
                collection = data[collection_key]
                if isinstance(collection, dict):
                    # 字典集合：如 ui_layouts.layouts = {id: entry, ...}
                    for entry_id, entry in collection.items():
                        if isinstance(entry, dict):
                            for field in required:
                                if field not in entry:
                                    errors.append(f"{collection_key}.{entry_id} 缺少必填字段: {field}")
                elif isinstance(collection, list):
                    # 数组集合：如 action_rules.rules = [entry, ...]
                    for idx, entry in enumerate(collection):
                        if isinstance(entry, dict):
                            for field in required:
                                if field not in entry:
                                    errors.append(f"{collection_key}[{idx}] 缺少必填字段: {field}")
            else:
                for field in required:
                    if field not in data:
                        errors.append(f"缺少必填字段: {field}")

        # 自定义校验器
        if name in self._validators:
            try:
                custom_errors = self._validators[name](data)
                if custom_errors:
                    errors.extend(custom_errors)
            except Exception as e:
                errors.append(f"自定义校验器异常: {e}")

        return errors

    def validate_table_with_report(self, name: str, data: Dict) -> Dict[str, Any]:
        """校验配置表并返回含 schema 级别的富报告。

        schema 级别：
        - "full": table_schemas 中存在完整 schema 定义（无 "schema": "partial" 标记）
        - "partial": table_schemas 中标注了 "schema": "partial"
        - "none": table_schemas 中无该表定义

        对于 partial/none 级别，执行降级校验：结构存在性检查（非空 + 顶层类型）。
        """
        errors = self._validate_table(name, data)

        # 判定 schema 级别
        schema = self._tables.get("table_schemas", {}).get("schemas", {}).get(name)
        if schema:
            schema_level = schema.get("schema", "full")  # "full" 或 "partial"
        else:
            schema_level = "none"

        # 对 partial/none 级别补充结构存在性校验
        # （顶层类型校验已在 _validate_table 中完成：根元素必须是 dict）
        if schema_level in ("partial", "none"):
            if not data:
                errors.append("表数据为空")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "schema": schema_level,
        }

    def register_validator(self, name: str, validator: Callable) -> None:
        """注册自定义校验器"""
        self._validators[name] = validator

    def set_schema_validator(self, validator) -> None:
        """设置三级校验器（SchemaValidator 实例）"""
        self._schema_validator = validator

    def set_storage(self, storage) -> None:
        """设置 Storage 引用，用于版本记录和回滚"""
        self._storage = storage

    def _get_collection_key(self, name: str, data: Dict) -> Optional[str]:
        """获取配置表中集合条目的包装键名（从table_schemas自动推断）"""
        # 从table_schemas的meta.skip_validate读取跳过校验的表
        schemas_meta = self._tables.get("table_schemas", {}).get("meta", {})
        skip_validate = set(schemas_meta.get("skip_validate", []))
        if name in skip_validate:
            return None

        # 1. 从table_schemas的schemas定义中查找collection_key
        schema = self._tables.get("table_schemas", {}).get("schemas", {}).get(name)
        if schema and schema.get("collection_key"):
            return schema["collection_key"]

        # 2. 启发式规则：与表名同名的复数键
        if name in data and isinstance(data[name], (dict, list)):
            return name

        # 3. 启发式规则：常见的集合键名
        for key in ("items", "entries", "list"):
            if key in data and isinstance(data[key], (dict, list)):
                return key

        return None

    def get(self, name: str, default: Any = None) -> Any:
        """获取配置表"""
        return self._tables.get(name, default)

    def get_layout(self, layout_id: str) -> Optional[Dict]:
        """获取UI布局配置"""
        layouts = self._tables.get("ui_layouts", {}).get("layouts", {})
        return layouts.get(layout_id)

    def get_layout_for_type(self, target_type: str, pool_type: str = "dzh") -> Optional[Dict]:
        """根据节点类型和池类型查找布局

        支持三种匹配方式：
        1. 通过 target_type 字段匹配（数字类型如 "200"，或数组如 ["3", "201"]）
        2. 通过 layout key 匹配（字符串类型如 "stock_state_pool"）
        3. 通过别名映射匹配（如 transfer_condition → condition_filter）
        """
        # 先尝试别名映射
        type_aliases = self._tables.get("cell_type_registry", {}).get("type_aliases", {})
        lookup_type = type_aliases.get(target_type, target_type)
        # TDX 池专用映射：DZH类型名映射到TDX布局
        tdx_aliases = self._tables.get("cell_type_registry", {}).get("tdx_type_aliases", {})
        # 类型分发（不可剥离）：TDX 池需切换到 tdx_type_aliases 别名子表，
        # 因 TDX 与 DZH 的类型命名空间不同（数据已在 cell_type_registry.json 中）
        if pool_type == "tdx" and target_type in tdx_aliases:
            lookup_type = tdx_aliases[target_type]

        layouts = self._tables.get("ui_layouts", {}).get("layouts", {})
        for layout_key, layout in layouts.items():
            # 支持 target_type 为数组（如 ["8", "200"]）
            layout_target = layout.get("target_type")
            if isinstance(layout_target, list):
                type_match = str(lookup_type) in [str(t) for t in layout_target]
            else:
                type_match = str(layout_target) == str(lookup_type)

            if (type_match or str(layout_key) == str(lookup_type)) and \
               layout.get("target_scope") in ("node", "edge", "global") and \
               self._pool_type_match(pool_type, layout.get("pool_type", ["dzh"])):
                return layout
        return None

    @staticmethod
    def _pool_type_match(pool_type: str, layout_pool_type) -> bool:
        """检查 pool_type 是否匹配布局的 pool_type 配置。
        支持 'any' 通配符，匹配所有池类型。
        """
        if layout_pool_type == "any":
            return True
        if isinstance(layout_pool_type, list):
            return pool_type in layout_pool_type or "any" in layout_pool_type
        if isinstance(layout_pool_type, str):
            return pool_type == layout_pool_type or layout_pool_type == "any"
        return False

    def get_layout_with_fallback(self, node_type: str, pool_type: str) -> Optional[Dict]:
        """获取布局，按 pool_types.json 的 layout_fallback_chain 回退查找。

        例如 custom 模式声明 layout_fallback_chain=["dzh","tdx"]，
        当 custom 自身无布局时依次尝试 dzh、tdx 布局。
        """
        layout = self.get_layout_for_type(node_type, pool_type)
        if layout:
            return layout
        pool_cfg = self._tables.get("pool_types", {}).get("pool_types", {}).get(pool_type, {})
        for fallback in pool_cfg.get("layout_fallback_chain", []):
            layout = self.get_layout_for_type(node_type, fallback)
            if layout:
                return layout
        return None

    def get_rules(self, tags: Optional[List[str]] = None) -> List[Dict]:
        """获取行为规则，支持按标签过滤"""
        rules = self._tables.get("action_rules", {}).get("rules", [])
        if tags:
            rules = [r for r in rules if any(t in r.get("tags", []) for t in tags)]
        return sorted(rules, key=lambda r: r.get("priority", 100))

    def check_hot_reload(self) -> List[str]:
        """检测配置表变更并热加载，返回变更的表名列表。

        三级校验闭环：
        1. 加载新配置前，先进行三级校验
        2. 如果校验失败，保留旧配置不变，记录错误日志
        3. 如果校验通过，替换为新配置
        4. 配置变更时，调用 Storage.record_config_version 记录版本历史
        """
        changed = []
        for path in self._config_dir.glob("*.json"):
            name = path.stem
            try:
                raw = path.read_text(encoding="utf-8")
                file_hash = hashlib.md5(raw.encode()).hexdigest()
                if self._hashes.get(name) != file_hash:
                    # 检测到变更，先解析新配置
                    try:
                        new_data = json.loads(raw)
                    except json.JSONDecodeError as e:
                        logger.error(f"热加载 {name} 失败: JSON解析错误 {e}，保留旧配置")
                        continue

                    # 三级校验
                    validation_passed = True
                    if self._schema_validator is not None:
                        # 语法校验
                        syntax_results = self._schema_validator.validate_syntax(name, new_data)
                        syntax_errors = [r for r in syntax_results if r.level == "error"]
                        if syntax_errors:
                            validation_passed = False
                            for r in syntax_errors:
                                logger.error(f"热加载 {name} 语法校验失败: {r.message}")

                        # 逻辑校验
                        logic_results = self._schema_validator.validate_logic(name, new_data)
                        logic_errors = [r for r in logic_results if r.level == "error"]
                        if logic_errors:
                            validation_passed = False
                            for r in logic_errors:
                                logger.error(f"热加载 {name} 逻辑校验失败: {r.message}")

                        # 业务规则校验
                        business_results = self._schema_validator.validate_business(name, new_data)
                        business_errors = [r for r in business_results if r.level == "error"]
                        if business_errors:
                            validation_passed = False
                            for r in business_errors:
                                logger.error(f"热加载 {name} 业务规则校验失败: {r.message}")

                    if not validation_passed:
                        logger.warning(f"热加载 {name} 校验失败，保留旧配置不变")
                        continue

                    # 校验通过，记录旧配置用于版本追踪
                    old_data = self._tables.get(name)
                    old_content = json.dumps(old_data, ensure_ascii=False) if old_data else None
                    new_content = json.dumps(new_data, ensure_ascii=False)

                    # 替换为新配置
                    self._tables[name] = new_data
                    self._hashes[name] = file_hash
                    self._load_times[name] = time.time()
                    changed.append(name)

                    # 记录配置版本
                    if self._storage is not None:
                        try:
                            change_type = 'update' if old_data else 'create'
                            self._storage.record_config_version(
                                table_name=name,
                                change_type=change_type,
                                old_content=old_content,
                                new_content=new_content,
                                created_by='hot_reload'
                            )
                        except Exception as e:
                            logger.warning(f"记录配置版本 {name} 失败: {e}")

                    logger.info(f"热加载 {name} 成功 (hash={file_hash[:8]})")
            except Exception as e:
                logger.error(f"热加载检测 {name} 失败: {e}")
        return changed

    def rollback_config(self, version_id: str) -> bool:
        """回滚配置到指定版本。

        通过 Storage.rollback_config 实现，回滚后重新加载配置。
        返回 True 表示回滚成功，False 表示失败。
        """
        if self._storage is None:
            logger.error("回滚失败: 未设置 Storage 引用")
            return False

        try:
            success = self._storage.rollback_config(version_id)
            if not success:
                logger.error(f"回滚配置版本 {version_id} 失败: Storage 回滚返回 False")
                return False

            # 回滚成功后，重新加载所有配置以同步状态
            self.load_all()
            logger.info(f"回滚配置版本 {version_id} 成功，已重新加载所有配置")
            return True
        except Exception as e:
            logger.error(f"回滚配置版本 {version_id} 异常: {e}")
            return False

    def start_watch(self, interval: float = 2.0) -> None:
        """启动配置表热加载监控"""
        self._watch_enabled = True
        self._watch_interval = interval
        logger.info(f"配置表热加载监控已启动 (间隔={interval}s)")

    def stop_watch(self) -> None:
        """停止配置表热加载监控"""
        self._watch_enabled = False
        logger.info("配置表热加载监控已停止")

    @property
    def table_names(self) -> List[str]:
        return list(self._tables.keys())

    @property
    def load_times(self) -> Dict[str, float]:
        return dict(self._load_times)

    def invalidate_all_caches(self) -> None:
        """使所有关联引擎缓存失效，热加载后调用。
        
        当 ConfigStore 的配置表发生变更时，需要通知所有依赖
        ConfigStore 的引擎组件刷新缓存，确保下次操作使用最新配置。
        """
        logger.info(f"ConfigStore.invalidate_all_caches: 刷新 {len(self._tables)} 张表的缓存引用")
        # 清除 schema_validator 的缓存（如果有）
        if self._schema_validator is not None:
            if hasattr(self._schema_validator, 'invalidate_cache'):
                self._schema_validator.invalidate_cache()

    def get_categories(self) -> Dict[str, Any]:
        """返回分类树，含 schema 覆盖统计、锁状态与一致性信息。"""
        categories = copy.deepcopy(self._categories)
        for cat in categories:
            for t in cat.get("tables", []):
                t["locked"] = self.is_table_locked(t.get("name", ""))
        return {
            "categories": categories,
            "category_consistency": self._category_consistency,
            "schema_coverage": self.get_schema_coverage(),
            "locks": self.get_lock_status(),
        }

    def get_schema_coverage(self) -> Dict[str, Any]:
        """返回 schema 覆盖统计，如 {'covered': 17, 'total': 61, 'ratio': '17/61'}。"""
        total = 0
        covered = 0
        for cat in self._categories:
            for t in cat.get("tables", []):
                total += 1
                if t.get("schema_covered"):
                    covered += 1
        return {"covered": covered, "total": total, "ratio": f"{covered}/{total}"}

    # ─── 表锁定管理（Task 13） ─────────────────────────────────

    def _load_locks(self) -> None:
        """从 .locks.json 加载锁状态。"""
        self._locks = {}
        locks_path = self._config_dir / ".locks.json"
        try:
            if locks_path.exists():
                with open(locks_path, "r", encoding="utf-8") as f:
                    self._locks = json.load(f)
        except (OSError, json.JSONDecodeError) as ex:
            logger.warning("Failed to load .locks.json: %s", ex)
            self._locks = {}

    def _save_locks(self) -> None:
        """持久化锁状态到 .locks.json。"""
        locks_path = self._config_dir / ".locks.json"
        try:
            with open(locks_path, "w", encoding="utf-8") as f:
                json.dump(self._locks, f, ensure_ascii=False, indent=2)
        except OSError as ex:
            logger.warning("Failed to save .locks.json: %s", ex)

    def is_table_locked(self, table_name: str) -> bool:
        """检查指定表是否被锁定。"""
        return self._locks.get(table_name, {}).get("locked", False)

    def lock_table(self, table_name: str, reason: str = "") -> Dict[str, Any]:
        """锁定一张表，返回锁信息。"""
        lock_info = {
            "locked": True,
            "locked_at": datetime.now().isoformat(),
            "reason": reason,
        }
        self._locks[table_name] = lock_info
        self._save_locks()
        return lock_info

    def unlock_table(self, table_name: str) -> bool:
        """解锁一张表，返回该表是否曾被锁定。"""
        if table_name in self._locks:
            del self._locks[table_name]
            self._save_locks()
            return True
        return False

    def get_lock_status(self) -> Dict[str, Any]:
        """返回所有表的锁状态。"""
        return dict(self._locks)


# ─── 规则执行引擎 ───────────────────────────────────────────────

class RuleEngine:
    """规则执行引擎：根据触发条件匹配并执行规则"""

    def __init__(self, store: ConfigStore):
        self._store = store
        self._handlers: Dict[str, Callable] = {}
        self._context: Dict[str, Any] = {}

    def invalidate_cache(self) -> None:
        """使 RuleEngine 内部缓存失效，热加载后调用。

        清除执行上下文，确保下次规则执行使用最新配置。
        """
        self._context = {}
        logger.info("RuleEngine 缓存已失效")

    def register_handler(self, action: str, handler: Callable) -> None:
        """注册动作处理器"""
        self._handlers[action] = handler

    def set_context(self, key: str, value: Any) -> None:
        """设置执行上下文"""
        self._context[key] = value

    def fire(self, trigger_type: str, field: str, value: Any = None,
             context: Optional[Dict] = None) -> List[Dict]:
        """触发规则执行，返回执行结果列表"""
        ctx = {**self._context, **(context or {})}
        rules = self._store.get_rules()
        results = []

        for rule in rules:
            trigger = rule.get("trigger", {})
            guard = rule.get("guard")

            # 匹配触发条件
            if trigger.get("type") != trigger_type:
                continue
            if trigger.get("field") and trigger.get("field") != field:
                continue

            # 检查守卫条件
            if guard and not self._check_guard(guard, ctx):
                continue

            # 执行handler
            action = rule.get("action")
            handler = self._handlers.get(action) or self._handlers.get(rule.get("handler_ref"))

            if handler:
                try:
                    params = rule.get("params_template", {})
                    result = handler(value=value, context=ctx, params=params)
                    results.append({
                        "rule_id": rule["rule_id"],
                        "action": action,
                        "result": result
                    })
                except Exception as e:
                    logger.error(f"规则 {rule['rule_id']} 执行失败: {e}")
                    results.append({
                        "rule_id": rule["rule_id"],
                        "action": action,
                        "error": str(e)
                    })

            if rule.get("stop_on_match", True):
                break

        return results

    def _check_guard(self, guard: Dict, context: Dict) -> bool:
        """检查守卫条件"""
        for key, expected in guard.items():
            actual = context.get(key)
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True


# ─── 数据绑定引擎 ───────────────────────────────────────────────

class DataBinder:
    """数据绑定引擎：处理前端字段与后端数据模型的双向绑定"""

    @staticmethod
    def get_value(data: Dict, path: str, default: Any = None) -> Any:
        """从数据对象中按路径取值 (如 'params.hold' -> data['params']['hold'])
        自动处理 {raw: int, bits: {...}} 格式的位标志对象"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return default
            if current is None:
                return default
        # 如果结果是 {raw: number, bits: {...}} 格式的位标志对象，自动提取 raw
        if isinstance(current, dict) and "raw" in current and "bits" in current and isinstance(current["raw"], int):
            return current["raw"]
        return current

    @staticmethod
    def set_value(data: Dict, path: str, value: Any) -> None:
        """向数据对象中按路径设值"""
        keys = path.split(".")
        current = data
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value

    @staticmethod
    def decode_attr_flags(attr_int, flags_config: List[Dict]) -> Dict[str, bool]:
        """解码位标志整数为布尔字典，支持 {raw: int, bits: {...}} 对象格式"""
        # 如果是 {raw: number, bits: {...}} 格式，提取 raw
        if isinstance(attr_int, dict) and "raw" in attr_int and "bits" in attr_int:
            attr_int = attr_int["raw"]
        # 如果是 {raw: number, show_overview: bool, ...} 格式（前端传来的已解码对象），提取 raw
        elif isinstance(attr_int, dict) and "raw" in attr_int:
            attr_int = attr_int["raw"]
        # 如果是纯布尔字典（没有raw），直接返回各标志值
        elif isinstance(attr_int, dict):
            result = {}
            for flag in flags_config:
                name = flag["name"]
                result[name] = bool(attr_int.get(name, False))
            return result
        if attr_int is None:
            attr_int = 0
        result = {}
        for flag in flags_config:
            mask = int(flag["hex"], 16) if isinstance(flag["hex"], str) else flag["hex"]
            result[flag["name"]] = bool(attr_int & mask)
        return result

    @staticmethod
    def encode_attr_flags(flags_dict: Dict[str, bool], flags_config: List[Dict]) -> int:
        """将布尔字典编码为位标志整数"""
        result = 0
        for flag in flags_config:
            if flags_dict.get(flag["name"], False):
                mask = int(flag["hex"], 16) if isinstance(flag["hex"], str) else flag["hex"]
                result |= mask
        return result

    @staticmethod
    def decode_action_compound(raw) -> Dict:
        """解码enter/exit动作编码，支持对象格式 {type: 0, params: 10000}"""
        if raw is None or raw == 0:
            return {"action_type": 0, "param": 0}
        # 对象格式 {type: 0, params: 10000}
        if isinstance(raw, dict):
            return {"action_type": raw.get("type", 0), "param": raw.get("params", 0)}
        # 整数位编码格式
        action_type = (raw >> 28) & 0xF
        param = raw & 0xFFFF
        return {"action_type": action_type, "param": param}

    @staticmethod
    def encode_action_compound(action_type: int, param: int) -> int:
        """编码enter/exit动作"""
        return (action_type << 28) | (param & 0xFFFF)

    @staticmethod
    def decode_tdx_action_hex(action_int):
        """解码 TDX 动作十六进制编码为结构化动作字典"""
        if action_int is None or action_int == 0 or action_int == "":
            return None
        try:
            action_int = int(action_int)
        except (ValueError, TypeError):
            return None
        if action_int == 0:
            return None

        high_type = (action_int >> 28) & 0xF
        if high_type != 0:
            param = action_int & 0xFFFF
            type_map = {1: "buy_amount", 2: "buy_shares", 3: "sell_shares"}
            if high_type in type_map:
                return {"type": type_map[high_type], "param": param, "raw": action_int}
            return {"type": "unknown", "action_type": high_type, "param": param, "raw": action_int}

        byte_type = (action_int >> 16) & 0xFF
        if byte_type != 0:
            param1 = (action_int >> 8) & 0xFF
            param2 = action_int & 0xFF
            byte_type_map = {6: "buy", 7: "sell"}
            if byte_type in byte_type_map:
                return {
                    "type": byte_type_map[byte_type],
                    "type_id": byte_type,
                    "param1": param1,
                    "param2": param2,
                    "raw": action_int,
                }
            return {
                "type": "unknown",
                "type_id": byte_type,
                "param1": param1,
                "param2": param2,
                "raw": action_int,
            }

        return None

    @staticmethod
    def encode_tdx_action_hex(action_type, param=0, param2=0):
        """编码 TDX 动作为十六进制整数"""
        hi_type_map = {"none": 0, "buy_amount": 1, "buy_shares": 2, "sell_shares": 3}
        tid = hi_type_map.get(action_type)
        if tid is not None and tid != 0:
            param = max(0, min(param, 0xFFFF))
            return (tid << 28) | param

        byte_type_map = {"buy": 6, "sell": 7}
        btid = byte_type_map.get(action_type)
        if btid is not None:
            param1 = max(0, min(param, 0xFF))
            param3 = max(0, min(param2, 0xFF))
            return (btid << 16) | (param1 << 8) | param3

        return 0

    # TODO: 确认无外部调用后可删除，功能与 builtins._stock_code 重复
    @staticmethod
    def stock_code(s):
        if isinstance(s, str): return s
        return s.get('code', s.get('label', str(s))) if isinstance(s, dict) else str(s)


# ─── 面板生成引擎 ───────────────────────────────────────────────

class PanelGenerator:
    """面板生成引擎：根据布局配置生成前端面板描述"""

    def __init__(self, store: ConfigStore):
        self._store = store
        self._binder = DataBinder()

    def invalidate_cache(self) -> None:
        """使 PanelGenerator 内部缓存失效，热加载后调用。

        确保下次生成面板时使用最新的布局配置。
        """
        logger.info("PanelGenerator 缓存已失效")

    def generate_panel(self, node_type: str, pool_type: str,
                   data: Dict) -> Dict:
        """生成面板描述（供前端渲染），自动根据属性所有权规则标记 disabled 字段

        pool_type='custom' 时，所有 DZH 和 TDX 的属性均可编辑，不允许禁用任何字段。
        """
        layout = self._store.get_layout_with_fallback(node_type, pool_type)
        if not layout:
            return {"error": f"未找到布局: type={node_type}, pool_type={pool_type}"}

        # 获取属性所有权管理器，计算当前池类型下应禁用的字段
        ownership = PropertyOwnershipManager(self._store)
        disabled_fields = ownership.get_disabled_fields(pool_type, node_type)

        panel = {
            "layout_id": layout["layout_id"],
            "name": layout.get("name", layout.get("title", "")),
            "pool_type": pool_type,
            "node_type": node_type,
            "blocked_attrs": ownership.get_blocked_attrs(pool_type),
            "allowed_attrs": ownership.get_allowed_attrs(pool_type, node_type),
            "disabled_fields": disabled_fields,
            "sections": []
        }

        for section in layout.get("sections", []):
            section_data = {
                "title": section["title"],
                "collapsible": section.get("collapsible", False),
                "fields": []
            }

            for field in section.get("fields", []):
                field_data = self._resolve_field(field, data)
                # 标记不属于当前池类型的字段为 disabled
                field_key = field.get("key", "")
                data_path = field.get("data_path", "")
                # 检查字段的 data_path 顶层键是否在 disabled_fields 中
                top_key = data_path.split(".")[0] if "." in data_path else data_path
                if top_key in disabled_fields or field_key in disabled_fields:
                    field_data["disabled"] = True
                    field_data["readonly"] = True
                section_data["fields"].append(field_data)

            panel["sections"].append(section_data)

        return panel

    def _resolve_field(self, field_config: Dict, data: Dict) -> Dict:
        """解析字段配置，合并数据值（表驱动：按 comp_type 查 comp_type_rules 执行）"""
        result = {
            "key": field_config["key"],
            "comp": field_config["comp"],
            "label": field_config["label"],
            "data_path": field_config["data_path"],
        }

        # 从数据中取值
        value = self._binder.get_value(data, field_config["data_path"])

        # 表驱动：按 comp_type 查 comp_type_rules 解析值，消除 flag_group/action_compound if/elif
        comp_rules = self._store.get("field_definitions", {}).get("comp_type_rules", {})
        rule = comp_rules.get(field_config["comp"], comp_rules.get("default", {}))
        result["value"] = self._resolve_comp_value(rule, field_config, value)

        # 复制规则声明的额外键（差异由表内容 copy_to_result 决定）
        for result_key, config_key in rule.get("copy_to_result", {}).items():
            if config_key in field_config:
                result[result_key] = field_config[config_key]

        # 复制其他属性
        for key in ["options", "min", "max", "step", "readonly", "nullable",
                     "depends_on", "active_when", "markets", "unit_options",
                     "flag_source", "hhmmss_modes", "action_types", "validation",
                     "hint", "encode_to", "rows"]:
            if key in field_config:
                result[key] = field_config[key]

        # tdx_enum_select: 通过 enum_key 从 tdx_enums.json 解析 options
        if "options" not in result and "enum_key" in field_config:
            enum_key = field_config["enum_key"]
            tdx_enums = self._store.get("tdx_enums", {}).get("enums", {})
            if enum_key in tdx_enums:
                result["options"] = tdx_enums[enum_key]
                result["enum_key"] = enum_key

        # 自动生成校验规则（从min/max/nullable等推断）
        auto_validation = {}
        if field_config.get("min") is not None:
            auto_validation["min"] = field_config["min"]
        if field_config.get("max") is not None:
            auto_validation["max"] = field_config["max"]
        if not field_config.get("nullable", False) and not field_config.get("readonly", False):
            auto_validation["required"] = True
        if auto_validation:
            existing = result.get("validation", {})
            if isinstance(existing, dict):
                merged = {**auto_validation, **existing}
                result["validation"] = merged
            else:
                result["validation"] = auto_validation

        return result

    def _resolve_comp_value(self, rule: Dict, field_config: Dict, value: Any) -> Any:
        """通用组件值解析器：按 comp_type_rules 表的 decode_method/empty_mode 解析。

        差异隐含于表结构：decode_method 决定解码函数，empty_mode 决定空值取值规则。
        """
        decode_method = rule.get("decode_method")
        if value is not None and decode_method:
            fn = getattr(self._binder, decode_method)
            args = [field_config.get(a) for a in rule.get("decode_config_args", [])]
            return fn(value, *args)
        if value is not None:
            return value
        # value 为 None：按 empty_mode 取默认值（差异由表内容决定）
        empty_mode = rule.get("empty_mode", "field_default")
        if empty_mode == "literal":
            return rule.get("empty_value")
        if empty_mode == "flags_all_false":
            flags_key = rule.get("empty_config_args", ["flags"])[0]
            flags = field_config.get(flags_key, [])
            return {f["name"]: False for f in flags}
        return field_config.get("default")

    def apply_change(self, node_type: str, pool_type: str,
                 data: Dict, field_key: str, value: Any) -> Dict:
        """应用字段变更到数据对象，返回更新后的数据（表驱动：按 comp_type 查 comp_type_rules 执行）"""
        layout = self._store.get_layout_with_fallback(node_type, pool_type)
        if not layout:
            return data

        # 查找字段配置
        field_config = self._find_field(layout, field_key)
        if not field_config:
            return data

        data_path = field_config["data_path"]

        # 表驱动：按 comp_type 查 comp_type_rules 执行变更，消除 flag_group/action_compound if/elif
        comp_rules = self._store.get("field_definitions", {}).get("comp_type_rules", {})
        rule = comp_rules.get(field_config["comp"], comp_rules.get("default", {}))
        self._apply_comp_change(rule, data, data_path, field_config, value)

        return data

    def _apply_comp_change(self, rule: Dict, data: Dict, data_path: str,
                           field_config: Dict, value: Any) -> None:
        """通用组件变更处理器：按 comp_type_rules 表的 apply_mode 执行。

        差异隐含于表结构：apply_mode 决定编码/合并/直设策略，方法名由表内容指定。
        """
        apply_mode = rule.get("apply_mode", "direct_set")
        if apply_mode == "direct_set":
            self._binder.set_value(data, data_path, value)
        elif apply_mode == "encode_extract":
            fn = getattr(self._binder, rule["apply_encode_method"])
            extracted = [value.get(k, 0) for k in rule.get("apply_extract_keys", [])]
            self._binder.set_value(data, data_path, fn(*extracted))
        elif apply_mode == "merge_encode":
            decode_fn = getattr(self._binder, rule["apply_decode_method"])
            encode_fn = getattr(self._binder, rule["apply_encode_method"])
            args = [field_config.get(a) for a in rule.get("apply_config_args", [])]
            current = self._binder.get_value(data, data_path, 0)
            current_decoded = decode_fn(current, *args)
            current_decoded.update(value)
            self._binder.set_value(data, data_path, encode_fn(current_decoded, *args))

    def _find_field(self, layout: Dict, field_key: str) -> Optional[Dict]:
        """在布局中查找字段配置"""
        for section in layout.get("sections", []):
            for field in section.get("fields", []):
                if field["key"] == field_key:
                    return field
        return None

    def compute_field_visibility(self, node_type: str, pool_type: str, data: Dict) -> Dict[str, bool]:
        layout = self._store.get_layout_with_fallback(node_type, pool_type)
        if not layout:
            return {}
        ktp, fcs = {}, {}
        for sec in layout.get("sections", []):
            for f in sec.get("fields", []):
                ktp[f.get("key", "")] = f.get("data_path", "")
                fcs[f.get("key", "")] = f
        vis = {}
        for k, f in fcs.items():
            do, aw = f.get("depends_on"), f.get("active_when")
            if not do or not aw:
                vis[k] = True
                continue
            pv = DataBinder.get_value(data, ktp.get(do, do))
            vis[k] = pv in aw if isinstance(aw, list) else pv == aw
        deps = {}
        for k, f in fcs.items():
            d = f.get("depends_on")
            if d:
                deps.setdefault(d, []).append(k)
        def _prop(pk):
            for c in deps.get(pk, []):
                if vis.get(c, True):
                    vis[c] = False
                    _prop(c)
        for k, v in list(vis.items()):
            if not v:
                _prop(k)
        return vis

    def validate_field(self, node_type: str, pool_type: str, field_key: str, value: Any) -> List[str]:
        layout = self._store.get_layout_with_fallback(node_type, pool_type)
        if not layout:
            return []
        fc = self._find_field(layout, field_key)
        if not fc:
            return []
        errors, vd = [], fc.get("validation", {})
        req = vd.get("required", not fc.get("nullable", False) and not fc.get("readonly", False))
        if vd.get("required") is False:
            req = False
        if req and (value is None or value == ""):
            errors.append(f"{fc.get('label', field_key)}不能为空")
        mn = vd.get("min", fc.get("min"))
        if mn is not None and value is not None and value < mn:
            errors.append(f"{fc.get('label', field_key)}不能小于{mn}")
        mx = vd.get("max", fc.get("max"))
        if mx is not None and value is not None and value > mx:
            errors.append(f"{fc.get('label', field_key)}不能大于{mx}")
        return errors


# ─── 属性所有权管理器 ───────────────────────────────────────────

class PropertyOwnershipManager:
    """属性所有权管理器：基于 property_ownership.json 配置表，
    管理每种节点类型在不同池类型下的属性所有权。
    确保导入DZH配置时禁用TDX独有属性，导入TDX配置时禁用DZH独有属性。
    """

    def __init__(self, store: "ConfigStore"):
        self._store = store
        self._ownership = None

    def _bypasses_ownership(self, pool_type: str) -> bool:
        """查表判断该池类型是否绕过所有权限制（如 custom 模式所有属性均可编辑）。

        由 pool_types.json 的 bypass_ownership_restrictions 字段驱动，
        避免在各查询方法中硬编码 pool_type == "custom"。
        """
        pool_cfg = self._store.get("pool_types", {}).get("pool_types", {}).get(pool_type, {})
        return bool(pool_cfg.get("bypass_ownership_restrictions", False))

    def _resolve_node_type(self, node_type: str, pool_type: str = "dzh") -> str:
        """将节点类型名解析为 type_ownership 中的 key

        支持 pool_type 感知：当 pool_type='tdx' 时，优先检查 tdx_{node_type} 前缀 key，
        以区分 TDX 和 DZH 中相同数字但不同含义的类型（如 type 3 在 DZH 为状态列，在 TDX 为转移条件）。
        """
        type_ownership = self.ownership.get("type_ownership", {})

        # 表驱动：按 pool_type 查 pool_type_attrs 获取 type_prefix，消除 if pool_type == 分支
        # TDX 池需优先查 tdx_ 前缀 key，因为同一数字类型在 DZH/TDX 含义不同
        # （如 type 3 在 DZH 为状态列、在 TDX 为转移条件），前缀命名空间隔离是
        # property_ownership.json 的结构前提
        pool_cfg = self.ownership.get("pool_type_attrs", {}).get(pool_type, {})
        prefix = pool_cfg.get("type_prefix")
        if prefix:
            prefixed_key = f"{prefix}{node_type}"
            if prefixed_key in type_ownership:
                return prefixed_key

        # 先尝试直接匹配
        if node_type in type_ownership:
            return node_type

        # 再尝试别名映射
        type_name_mapping = self._store.get("property_ownership", {}).get("type_name_mapping", {})
        return type_name_mapping.get(node_type, node_type)

    @property
    def ownership(self) -> Dict:
        """获取属性所有权配置（懒加载）"""
        if self._ownership is None:
            self._ownership = self._store.get("property_ownership", {})
        return self._ownership

    def invalidate_cache(self):
        """使缓存失效，下次访问时重新加载"""
        self._ownership = None

    def get_blocked_attrs(self, pool_type: str) -> List[str]:
        """获取指定池类型下被封锁的属性列表

        绕过所有权限制的池类型（如 custom）没有封锁属性，所有属性均可编辑。
        """
        if self._bypasses_ownership(pool_type):
            return []
        rules = self.ownership.get("rules", {})
        # 表驱动：按 pool_type 查 pool_type_attrs 获取 rules_key，消除 if pool_type == 分支
        pool_cfg = self.ownership.get("pool_type_attrs", {}).get(pool_type, {})
        rules_key = pool_cfg.get("rules_key")
        return rules.get(rules_key, {}).get("blocked_attrs", []) if rules_key else []

    def is_attr_allowed(self, pool_type: str, node_type: str, attr_name: str) -> bool:
        """检查指定属性在指定池类型和节点类型下是否允许编辑

        绕过所有权限制的池类型（如 custom）下所有属性均可编辑。
        """
        if self._bypasses_ownership(pool_type):
            return True

        resolved_type = self._resolve_node_type(node_type, pool_type)

        # 1. 检查 type_ownership 精确映射
        type_ownership = self.ownership.get("type_ownership", {})
        type_info = type_ownership.get(resolved_type)
        if type_info:
            allowed = type_info.get(pool_type)
            if allowed is None:
                # 该类型在指定池类型下不存在，所有属性均不可编辑
                return False
            return attr_name in allowed

        # 2. 对于类型不精确匹配的情况，使用共享属性
        shared = self.ownership.get("shared", {}).get("attrs", {})
        shared_attrs = shared.get("node", []) + shared.get("edge", [])
        if attr_name in shared_attrs:
            return True

        # 3. 检查独占属性
        blocked = self.get_blocked_attrs(pool_type)
        if attr_name in blocked:
            return False

        return True

    def get_allowed_attrs(self, pool_type: str, node_type: str) -> Optional[List[str]]:
        """获取指定池类型和节点类型下允许编辑的属性列表

        绕过所有权限制的池类型（如 custom）下返回 None（表示所有属性均允许，无限制）。
        """
        if self._bypasses_ownership(pool_type):
            return None  # None 表示无限制，所有属性均可编辑

        resolved_type = self._resolve_node_type(node_type, pool_type)
        type_ownership = self.ownership.get("type_ownership", {})
        type_info = type_ownership.get(resolved_type)
        if type_info:
            return type_info.get(pool_type)
        return None

    def get_pool_type_for_type(self, node_type: str) -> Optional[str]:
        """根据节点类型推断其所属的池类型"""
        # 检查 type_ownership
        type_ownership = self.ownership.get("type_ownership", {})
        type_info = type_ownership.get(node_type)

        # 如果直接匹配不到，尝试 tdx_ 前缀（TDX 独有类型）
        if type_info is None:
            tdx_key = f"tdx_{node_type}"
            type_info = type_ownership.get(tdx_key)

        if type_info:
            if type_info.get("dzh") and not type_info.get("tdx"):
                return "dzh"
            if type_info.get("tdx") and not type_info.get("dzh"):
                return "tdx"
            if type_info.get("dzh") and type_info.get("tdx"):
                return "shared"

        # 检查 cell_type_registry
        cell_types = self._store.get("cell_type_registry", {}).get("types", {})
        cell_type = cell_types.get(node_type)
        if cell_type and cell_type.get("pool_type"):
            return cell_type["pool_type"]

        return None

    def get_type_mapping(self, from_pool: str, to_pool: str) -> Dict[str, str]:
        """获取池类型间的节点类型映射"""
        mapping_key = f"{from_pool}_to_{to_pool}"
        return self.ownership.get("type_mapping", {}).get(mapping_key, {})

    def filter_data(self, pool_type: str, node_type: str, data: Dict) -> Dict:
        """过滤数据对象，移除不属于当前池类型的属性

        绕过所有权限制的池类型（如 custom）下不过滤，返回原始数据。
        """
        if self._bypasses_ownership(pool_type):
            return data

        allowed = self.get_allowed_attrs(pool_type, node_type)
        if allowed is None:
            return data
        allowed_set = set(allowed)
        return {k: v for k, v in data.items() if k in allowed_set}

    def get_disabled_fields(self, pool_type: str, node_type: str) -> List[str]:
        """获取指定池类型下应禁用的字段名列表（用于前端灰显）

        绕过所有权限制的池类型（如 custom）下所有字段均可编辑，无禁用字段。

        逻辑：
        1. 如果 type_ownership 中有精确匹配，取另一种池类型允许但当前池类型不允许的属性
        2. 如果另一种池类型的允许属性为 null（该类型在另一种池类型下不存在），
           则使用全局独占属性列表来确定 disabled 字段
        """
        if self._bypasses_ownership(pool_type):
            return []

        resolved_type = self._resolve_node_type(node_type, pool_type)
        type_ownership = self.ownership.get("type_ownership", {})
        type_info = type_ownership.get(resolved_type)
        if not type_info:
            return []

        allowed = type_info.get(pool_type)
        if allowed is None:
            # 当前池类型不支持此节点类型，所有属性均禁用
            return []

        # 表驱动：按 pool_type 查 pool_type_attrs 获取对照池类型 other_pool，消除 if pool_type == 分支
        # 所有权模型是 dzh/tdx 二元对照，需取"另一种"池类型的允许属性来计算差异
        pool_cfg = self.ownership.get("pool_type_attrs", {}).get(pool_type, {})
        other_pool = pool_cfg.get("other_pool", "")
        other_allowed = type_info.get(other_pool)

        if other_allowed is not None:
            # 另一种池类型存在，取差异
            allowed_set = set(allowed)
            return [attr for attr in other_allowed if attr not in allowed_set]
        else:
            # 另一种池类型不存在此节点类型（如 tdx_state_pool 在 DZH 下不存在），
            # 使用全局独占属性列表来确定 disabled 字段
            ownership_data = self.ownership.get("ownership", {})
            other_exclusive = ownership_data.get(other_pool, {}).get("exclusive_attrs", {})
            # 判断是节点还是边
            scope = "edge" if node_type == "flow" else "node"
            other_exclusive_list = other_exclusive.get(scope, [])
            # 当前允许的属性中，属于另一种池类型独占的属性即为 disabled
            # 但更准确的做法是：面板中出现的字段如果属于另一种池类型的独占属性，则 disabled
            # 这里返回另一种池类型的独占属性列表，前端/后端在生成面板时逐字段检查
            return other_exclusive_list

