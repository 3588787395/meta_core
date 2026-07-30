"""表驱动架构核心引擎：统一驱动逻辑与渲染的表解析执行引擎。"""

# === 热加载层（自 services/hot_reload.py 合并）===

import asyncio
import json
import time
import copy
import functools
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from core.event_bus import ConfigChanged, ConfigLoaded, EventBus, MetaDispatcher
from converters_common import safe_int

logger = logging.getLogger(__name__)

# ─── 配置表加载器 ───────────────────────────────────────────────

# dict/list 集合归一化迭代器（表驱动按 type 分派，供 ConfigStore._validate_table 复用）
_ENTRY_ITERATORS: Dict[type, Callable[[Any], Iterator[Tuple[str, Any]]]] = {
    dict: lambda c: ((f".{k}", v) for k, v in c.items()),
    list: lambda c: ((f"[{i}]", v) for i, v in enumerate(c)),
}


def _iter_entries(collection) -> Iterator[Tuple[str, Any]]:
    """归一化 dict/list 集合为 (location_suffix, entry) 迭代器。"""
    iterate = _ENTRY_ITERATORS.get(type(collection))
    if iterate is not None:
        yield from iterate(collection)


class ConfigStoreBase(MetaDispatcher):
    """配置存储基类——热加载与回滚的模板方法 + MetaDispatcher 查找子类（合并双实现）。"""

    def _iter_config_paths(self) -> List[Path]:
        """枚举配置文件（子类可覆盖，默认扫描 config_dir 下 *.json）。"""
        return list(self._config_dir.glob("*.json"))

    def _validate_three_tiers(self, name: str, data: Any) -> List[str]:
        """三级校验共享骨架（语法→逻辑→业务规则，逐级短路），返回错误消息列表。"""
        errors: List[str] = []
        if self._schema_validator is None:
            return errors
        for validate in (
            self._schema_validator.validate_syntax,
            self._schema_validator.validate_logic,
            self._schema_validator.validate_business,
        ):
            tier_errors = [r.message for r in validate(name, data) if r.level == "error"]
            errors.extend(tier_errors)
            if tier_errors:
                break
        return errors

    def _validate(self, name: str, data: Any) -> List[str]:
        """校验配置数据，返回错误消息列表（空=通过）。默认委托三级校验，子类可覆盖。"""
        return self._validate_three_tiers(name, data)

    def _commit(self, name: str, data: Any, file_hash: str) -> Any:
        """子类覆盖：提交配置到存储，返回旧配置数据（用于版本追踪）。"""
        raise NotImplementedError

    def _post_reload(self, changed: List[str]) -> None:
        """子类覆盖：重载后回调（默认空）。"""
        return None

    def _reload_all(self) -> None:
        """子类覆盖：回滚后重载所有配置。"""
        raise NotImplementedError

    def check_and_reload(self) -> List[str]:
        """热加载模板方法（10 步骨架）：检测→解析→校验→替换→版本记录。"""
        changed: List[str] = []
        for path in self._iter_config_paths():
            name = path.stem
            try:
                raw = path.read_text(encoding="utf-8")
                file_hash = hashlib.md5(raw.encode()).hexdigest()
                if self._hashes.get(name) == file_hash:
                    continue
                try:
                    new_data = json.loads(raw)
                except json.JSONDecodeError as e:
                    logger.error(f"热加载 {name} 失败: JSON解析错误 {e}，保留旧配置")
                    continue
                errors = self._validate(name, new_data)
                if errors:
                    for err in errors:
                        logger.error(f"热加载 {name} 校验失败: {err}")
                    logger.warning(f"热加载 {name} 校验失败，保留旧配置不变")
                    continue
                old_data = self._commit(name, new_data, file_hash)
                changed.append(name)
                if self._storage is not None:
                    try:
                        self._storage.record_config_version(
                            table_name=name,
                            change_type='update' if old_data else 'create',
                            old_content=json.dumps(old_data, ensure_ascii=False) if old_data else None,
                            new_content=json.dumps(new_data, ensure_ascii=False),
                            created_by='hot_reload',
                        )
                    except Exception as e:
                        logger.warning(f"记录配置版本 {name} 失败: {e}")
                logger.info(f"热加载 {name} 成功 (hash={file_hash[:8]})")
            except Exception as e:
                logger.error(f"热加载检测 {name} 失败: {e}")
        self._post_reload(changed)
        return changed

    def rollback(self, version_id: str) -> bool:
        """回滚模板方法：委托 Storage 回滚后重载所有配置。"""
        if self._storage is None:
            logger.error("回滚失败: 未设置 Storage 引用")
            return False
        try:
            if not self._storage.rollback_config(version_id):
                logger.error(f"回滚配置版本 {version_id} 失败")
                return False
            self._reload_all()
            logger.info(f"回滚配置版本 {version_id} 成功")
            return True
        except Exception as e:
            logger.error(f"回滚配置版本 {version_id} 异常: {e}")
            return False


class ConfigStore(ConfigStoreBase):
    """配置表存储：加载、缓存、校验、热加载"""

    def __init__(self, config_dir: Optional[str] = None, storage=None, bus: Optional[EventBus] = None):
        # SubTask 27.14: config_dir 可选，默认为模块上级的 config/ 目录
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "config"
        self._config_dir = Path(config_dir)
        self._tables: Dict[str, Dict] = {}
        # MetaDispatcher._store alias：指向 _tables（register/dispatch 默认操作对象）
        self._store = self._tables
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
        # 非配置表数据文件缓存（data/ 目录，不参与热加载）
        self._data_files: Dict[str, Dict] = {}
        # Task 15: EventBus 注入——订阅 ConfigChanged 事件自行重载，
        # load_all() 后发布 ConfigLoaded 事件
        self._bus: Optional[EventBus] = bus
        if bus is not None:
            bus.subscribe(ConfigChanged, self._on_config_changed)
        # SubTask 27.14: 独立实例（无 bus）自动加载配置，便于验证与脚本使用
        if bus is None:
            self.load_all()

    def _iter_config_files(self) -> List[Path]:
        """递归枚举 config_dir 下所有 .json 文件，跳过 _archived/ 与 .locks.json。

        SubTask 27.14: 配置文件按模块分类到子目录后，需递归扫描而非仅扫根目录。
        """
        files: List[Path] = []
        for path in self._config_dir.rglob("*.json"):
            # 跳过 _archived/ 目录（历史归档，不参与加载）
            if "_archived" in path.parts:
                continue
            # 跳过 .locks.json（由 _load_locks 单独加载）
            if path.name == ".locks.json":
                continue
            files.append(path)
        return sorted(files)

    def _find_table_path(self, name: str) -> Optional[Path]:
        """按表名递归查找配置文件路径，跳过 _archived/。

        SubTask 27.14: 配置文件移入子目录后，路径不再固定为 config_dir/name.json。
        """
        for path in self._config_dir.rglob(f"{name}.json"):
            if "_archived" in path.parts:
                continue
            return path
        return None

    def _on_config_changed(self, event: ConfigChanged) -> None:
        """``ConfigChanged`` 事件处理器：重载变更的配置表。

        由 ``HotReloadManager`` 检测到文件变更后发布 ``ConfigChanged`` 事件触发，
        ConfigStore 不再被 HotReloadManager 直接调用 ``check_hot_reload()``
        （Task 14.3：删除直接调用，改为事件订阅自行重载）。
        """
        try:
            changed_tables = getattr(event, "changed_tables", []) or []
            for name in changed_tables:
                path = self._find_table_path(name)
                if path is not None:
                    self._load_table(name, path)
            if changed_tables:
                logger.info("ConfigStore 经 ConfigChanged 事件重载表: %s", changed_tables)
        except Exception as ex:
            logger.warning("ConfigStore._on_config_changed 异常: %s", ex)

    def load_all(self) -> None:
        """加载所有配置表（SubTask 27.14: 递归扫描子目录）"""
        for path in self._iter_config_files():
            name = path.stem
            self._load_table(name, path)
        self._load_categories()
        self._load_locks()
        # Task 15: 加载完成后发布 ConfigLoaded 事件
        if self._bus is not None:
            try:
                self._bus.publish(ConfigLoaded(config_tables=dict(self._tables)))
            except Exception as ex:
                logger.warning("ConfigStore 发布 ConfigLoaded 异常: %s", ex)

    def _load_categories(self) -> None:
        """加载 table_categories.json 并与 config 目录实际 .json 文件做一致性校验。"""
        # SubTask 2.1: 加载分类元数据
        self._categories = []
        self._category_consistency = {
            "missing_on_disk": [],
            "extra_on_disk": [],
            "consistent": False,
        }
        categories_path = self._find_table_path("table_categories")
        if categories_path is None:
            logger.warning("table_categories.json 未找到（递归扫描 config_dir）")
            return
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
            for path in self._iter_config_files():
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

            # MetaDispatcher register 语义：_tables[name] = data
            self.register(name, data)
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
                # dict/list 集合归一化为 _iter_entries 迭代器（表驱动分派，无 if/elif 双分支）
                for loc, entry in _iter_entries(collection):
                    if isinstance(entry, dict):
                        for field in required:
                            if field not in entry:
                                errors.append(f"{collection_key}{loc} 缺少必填字段: {field}")
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
        """校验配置表并返回含 schema 级别的富报告。"""
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

    def register(self, key, value):
        """MetaDispatcher register 语义：_tables[key] = value（dict 赋值）。"""
        self._tables[key] = value

    def _dispatch_impl(self, key, *args, **kwargs):
        """MetaDispatcher 派发实现：纯查找无副作用（返回 _tables[key] 或 None）。"""
        return self._tables.get(key)

    def get_table(self, name: str) -> Dict[str, Any]:
        """获取配置表（统一入口，参与热加载）。"""
        table = self._tables.get(name)
        if table is None:
            # 表未加载：尝试按名递归查找并加载（兼容独立实例/脚本用法）
            path = self._find_table_path(name)
            if path is not None:
                self._load_table(name, path)
                table = self._tables.get(name, {})
            else:
                logger.warning("ConfigStore.get_table: 配置表 %s 未找到，返回空 dict", name)
                table = {}
        return table if isinstance(table, dict) else {}

    def get_data_file(self, name: str) -> Dict[str, Any]:
        """加载 ``data/`` 目录下的 JSON 文件（带缓存，不参与热加载）。"""
        if name in self._data_files:
            return self._data_files[name]
        data_path = self._config_dir.parent / "data" / f"{name}.json"
        try:
            raw = data_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as ex:
            logger.warning("ConfigStore.get_data_file: 加载 %s 失败: %s", name, ex)
            data = {}
        self._data_files[name] = data
        return data

    def get_layout(self, layout_id: str) -> Optional[Dict]:
        """获取UI布局配置"""
        layouts = self._tables.get("ui_layouts", {}).get("layouts", {})
        return layouts.get(layout_id)

    def get_layout_for_type(self, target_type: str, pool_type: str = "dzh") -> Optional[Dict]:
        """根据节点类型和池类型查找布局。"""
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
        """获取布局，按 pool_types.json 的 layout_fallback_chain 回退查找。"""
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

    def _iter_config_paths(self) -> List[Path]:
        """覆盖基类：递归扫描 config_dir，跳过 _archived/ 与 .locks.json。"""
        return self._iter_config_files()

    def _commit(self, name: str, data: Any, file_hash: str) -> Any:
        """覆盖基类 hook：写入本地 _tables/_hashes/_load_times，返回旧配置。"""
        old = self._tables.get(name)
        self._tables[name] = data
        self._hashes[name] = file_hash
        self._load_times[name] = time.time()
        return old

    def _reload_all(self) -> None:
        """覆盖基类 hook：回滚后重载所有配置。"""
        self.load_all()

    def check_hot_reload(self) -> List[str]:
        """检测配置表变更并热加载（委托基类 ``check_and_reload`` 模板方法）。"""
        return self.check_and_reload()

    def rollback_config(self, version_id: str) -> bool:
        """回滚配置到指定版本（委托基类 ``rollback`` 模板方法）。"""
        return self.rollback(version_id)

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
    def tables(self) -> Dict[str, Dict]:
        """配置表字典（SubTask 27.14 验证用）"""
        return self._tables

    @property
    def table_names(self) -> List[str]:
        return list(self._tables.keys())

    @property
    def load_times(self) -> Dict[str, float]:
        return dict(self._load_times)

    def invalidate_all_caches(self) -> None:
        """使所有关联引擎缓存失效，热加载后调用。"""
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


# ─── 全局 ConfigStore 引用（供模块级函数访问，避免在各模块重复定义 _load_* 帮助函数）───

_global_config_store: Optional["ConfigStore"] = None


def set_global_config_store(store: "ConfigStore") -> None:
    """注入全局 ConfigStore 引用，供无法通过构造函数获取 config_store 的模块级函数使用。"""
    global _global_config_store
    _global_config_store = store


def get_global_config_store() -> Optional["ConfigStore"]:
    """获取全局 ConfigStore 引用。未注入时返回 None，调用方应处理 None 情形。"""
    return _global_config_store


def load_config_table(name: str) -> Dict[str, Any]:
    """按表名加载配置表（模块级配置加载统一入口）。"""
    store = get_global_config_store()
    if store is not None:
        return store.get_table(name)
    # 回退：模块导入早期 ConfigStore 未注入，递归查找配置文件
    config_dir = Path(__file__).parent.parent / "config"
    for path in config_dir.rglob(f"{name}.json"):
        if "_archived" in path.parts:
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return {}


# ─── 规则执行引擎 ───────────────────────────────────────────────

class RuleEngine:
    """规则执行引擎：根据触发条件匹配并执行规则"""

    def __init__(self, store: ConfigStore):
        self._store = store
        self._handlers: Dict[str, Callable] = {}
        self._context: Dict[str, Any] = {}

    def invalidate_cache(self) -> None:
        """使 RuleEngine 内部缓存失效，热加载后调用。"""
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
        action_int = safe_int(action_int, 0)
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


# ─── 面板生成引擎 ───────────────────────────────────────────────

class PanelGenerator:
    """面板生成引擎：根据布局配置生成前端面板描述"""

    def __init__(self, store: ConfigStore):
        self._store = store
        self._binder = DataBinder()

    def invalidate_cache(self) -> None:
        """使 PanelGenerator 内部缓存失效，热加载后调用。"""
        logger.info("PanelGenerator 缓存已失效")

    def generate_panel(self, node_type: str, pool_type: str,
                   data: Dict) -> Dict:
        """生成面板描述（供前端渲染），自动根据属性所有权规则标记 disabled 字段。"""
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
        """通用组件值解析器：按 comp_type_rules 表的 decode_method/empty_mode 解析。"""
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
        """通用组件变更处理器：按 comp_type_rules 表的 apply_mode 执行。"""
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
    """属性所有权管理器：基于 property_ownership.json 配置表，。"""

    def __init__(self, store: "ConfigStore"):
        self._store = store
        self._ownership = None

    def _bypasses_ownership(self, pool_type: str) -> bool:
        """查表判断该池类型是否绕过所有权限制（如 custom 模式所有属性均可编辑）。"""
        pool_cfg = self._store.get("pool_types", {}).get("pool_types", {}).get(pool_type, {})
        return bool(pool_cfg.get("bypass_ownership_restrictions", False))

    def _pool_attr(self, pool_type: str, key: str, default: Any = None) -> Any:
        """从 ownership.pool_type_attrs[pool_type] 取 key（合并 4 处查表副本）。"""
        pool_cfg = self.ownership.get("pool_type_attrs", {}).get(pool_type, {})
        return pool_cfg.get(key, default)

    @staticmethod
    def _bypass_or(default_factory: Callable[[], Any]):
        """装饰器：``_bypasses_ownership(pool_type)`` 为真时短路返回 default_factory()。"""
        def decorator(method):
            @functools.wraps(method)
            def wrapper(self, pool_type, *args, **kwargs):
                if self._bypasses_ownership(pool_type):
                    return default_factory()
                return method(self, pool_type, *args, **kwargs)
            return wrapper
        return decorator

    def _resolve_node_type(self, node_type: str, pool_type: str = "dzh") -> str:
        """将节点类型名解析为 type_ownership 中的 key。"""
        type_ownership = self.ownership.get("type_ownership", {})

        # 表驱动：按 pool_type 查 pool_type_attrs 获取 type_prefix，消除 if pool_type == 分支
        prefix = self._pool_attr(pool_type, "type_prefix")
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

    @_bypass_or(list)
    def get_blocked_attrs(self, pool_type: str) -> List[str]:
        """获取指定池类型下被封锁的属性列表。"""
        rules = self.ownership.get("rules", {})
        rules_key = self._pool_attr(pool_type, "rules_key")
        return rules.get(rules_key, {}).get("blocked_attrs", []) if rules_key else []

    def is_attr_allowed(self, pool_type: str, node_type: str, attr_name: str) -> bool:
        """检查指定属性在指定池类型和节点类型下是否允许编辑。"""
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

    @_bypass_or(lambda: None)
    def get_allowed_attrs(self, pool_type: str, node_type: str) -> Optional[List[str]]:
        """获取指定池类型和节点类型下允许编辑的属性列表。"""
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
        """过滤数据对象，移除不属于当前池类型的属性。"""
        if self._bypasses_ownership(pool_type):
            return data

        allowed = self.get_allowed_attrs(pool_type, node_type)
        if allowed is None:
            return data
        allowed_set = set(allowed)
        return {k: v for k, v in data.items() if k in allowed_set}

    @_bypass_or(list)
    def get_disabled_fields(self, pool_type: str, node_type: str) -> List[str]:
        """获取指定池类型下应禁用的字段名列表（用于前端灰显）。"""
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
        other_pool = self._pool_attr(pool_type, "other_pool", "")
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


# === 热加载层（自 services/hot_reload.py 合并，SubTask 28.6）===


class HotReloadManager(ConfigStoreBase):
    """热加载管理器：文件监控 + 原子替换 + WebSocket 推送。

    事件驱动（Task 14）：
        - ``bus`` 注入后，文件变更检测时发布 ``ConfigChanged`` 事件
        - ``bus=None`` 时保持原行为（仅本地 swap + on_change 回调），向后兼容
    """

    def __init__(self, config_dir: str, config_store=None, storage=None,
                 schema_validator=None, on_change: Optional[Callable] = None,
                 bus: Optional[EventBus] = None):
        """Args:."""
        self._config_dir = Path(config_dir)
        self._config_store = config_store
        self._storage = storage
        self._schema_validator = schema_validator
        self._on_change = on_change
        self._bus = bus
        self._hashes: Dict[str, str] = {}
        self._watch_task: Optional[asyncio.Task] = None
        self._watch_enabled = False
        self._watch_interval = 2.0
        self._ws_connections: Set = set()
        self._observer = None  # watchdog observer
        # watchdog 高频触发防抖：1 秒内只发布一次 ConfigChanged
        self._last_check_ts: float = 0.0
        self._debounce_interval: float = 1.0
        # 兼容原 _pending_changes 标记（bus=None 时 watchdog 仍写入此集合）
        self._pending_changes: Set[str] = set()

        # 初始化哈希快照
        self._snapshot_hashes()

    def _snapshot_hashes(self) -> None:
        """对当前所有配置文件计算哈希快照"""
        self._hashes.clear()
        for path in self._config_dir.glob("*.json"):
            try:
                raw = path.read_text(encoding="utf-8")
                self._hashes[path.stem] = hashlib.md5(raw.encode()).hexdigest()
            except Exception:
                pass

    def _notify_changed(self, changed_tables: List[str]) -> None:
        if not changed_tables or self._bus is None:
            return
        try:
            self._bus.publish(ConfigChanged(changed_tables=changed_tables))
        except Exception as ex:
            logger.warning("HotReload publish ConfigChanged failed: %s", ex)

    def set_config_store(self, store) -> None:
        self._config_store = store

    def set_storage(self, storage) -> None:
        self._storage = storage

    def set_schema_validator(self, validator) -> None:
        self._schema_validator = validator

    def set_on_change(self, callback: Callable) -> None:
        self._on_change = callback

    # ─── 核心检测与替换 ───────────────────────────────────────

    def detect_changes(self) -> List[str]:
        """检测配置文件变更（仅检测，不替换，不更新 hash 缓存）"""
        changed = []
        for path in self._config_dir.glob("*.json"):
            name = path.stem
            try:
                raw = path.read_text(encoding="utf-8")
                file_hash = hashlib.md5(raw.encode()).hexdigest()
                if self._hashes.get(name) != file_hash:
                    changed.append(name)
            except Exception:
                pass
        return changed

    def check_changes(self) -> List[str]:
        """检查配置文件变更并发布 ``ConfigChanged`` 事件（事件驱动入口）。

        与 ``check_and_reload`` 的区别：
            - 仅检测 + 发布事件，不做三级校验/原子替换/版本记录
            - 由 Config 模块订阅 ``ConfigChanged`` 事件自行重载（Task 15 装配）
            - 更新本地 hash 缓存，避免重复发布同一变更

        Returns:
            变更的表名列表（去 ``.json`` 后缀）
        """
        changed_tables: List[str] = []
        for path in self._config_dir.glob("*.json"):
            name = path.stem
            try:
                raw = path.read_text(encoding="utf-8")
                cur_hash = hashlib.md5(raw.encode()).hexdigest()
            except Exception as ex:
                logger.warning("HotReload 计算文件 hash 失败 %s: %s", name, ex)
                continue

            prev_hash = self._hashes.get(name)
            if prev_hash is not None and cur_hash != prev_hash:
                changed_tables.append(name)
            self._hashes[name] = cur_hash

        if changed_tables:
            self._notify_changed(changed_tables)

        return changed_tables

    def on_file_modified(self, event) -> None:
        """watchdog 文件变更回调（带防抖）。"""
        if self._bus is None:
            # 向后兼容：保留原 _pending_changes 标记逻辑
            try:
                name = Path(event.src_path).stem
                self._pending_changes.add(name)
            except Exception:
                pass
            return

        try:
            now = time.time()
            if now - self._last_check_ts < self._debounce_interval:
                return  # 防抖窗口内，跳过
            self._last_check_ts = now
            # 检测 + 发布 ConfigChanged 事件
            self.check_changes()
        except Exception as ex:
            logger.warning("HotReload 文件变更处理失败: %s", ex)

    def _commit(self, name: str, data: Any, file_hash: str) -> Any:
        """覆盖基类 hook：原子替换 ConfigStore 中的表，返回旧配置。"""
        old = None
        if self._config_store:
            old = self._config_store.get(name)
            self._config_store._tables[name] = data
            self._config_store._hashes[name] = file_hash
            self._config_store._load_times[name] = time.time()
        self._hashes[name] = file_hash
        return old

    def _post_reload(self, changed: List[str]) -> None:
        """覆盖基类 hook：触发 on_change 回调并发布 ConfigChanged 事件。"""
        if changed and self._on_change:
            try:
                self._on_change(changed)
            except Exception as e:
                logger.warning(f"热加载回调执行失败: {e}")
        if changed:
            self._notify_changed(changed)

    def _reload_all(self) -> None:
        """覆盖基类 hook：回滚后重载 ConfigStore 并刷新哈希快照。"""
        if self._config_store:
            self._config_store.load_all()
        self._snapshot_hashes()

    # ─── Watchdog 模式 ───────────────────────────────────────

    def start_watchdog(self) -> bool:
        """启动 watchdog 文件监控。

        事件驱动（Task 14）：
            - ``bus`` 注入时，``on_modified`` 经 ``on_file_modified`` 触发
              ``check_changes()``，发布 ``ConfigChanged`` 事件（带 1s 防抖）
            - ``bus=None`` 时保持原行为（仅标记 ``_pending_changes``）
        """
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class ConfigHandler(FileSystemEventHandler):
                def __init__(self, manager: HotReloadManager):
                    self._manager = manager

                def on_modified(self, event):
                    if event.is_directory or not event.src_path.endswith('.json'):
                        return
                    name = Path(event.src_path).stem
                    logger.debug(f"Watchdog 检测到变更: {name}")
                    # 委托给 manager：bus 注入则发布 ConfigChanged，否则标记 _pending_changes
                    self._manager.on_file_modified(event)

            handler = ConfigHandler(self)
            self._observer = Observer()
            self._observer.schedule(handler, str(self._config_dir), recursive=False)
            self._observer.daemon = True
            self._observer.start()
            logger.info(f"Watchdog 文件监控已启动: {self._config_dir}")
            return True
        except ImportError:
            logger.warning("watchdog 未安装，使用轮询模式代替")
            return False
        except Exception as e:
            logger.error(f"Watchdog 启动失败: {e}")
            return False

    def stop_watchdog(self) -> None:
        """停止 watchdog 文件监控"""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            logger.info("Watchdog 文件监控已停止")

    # ─── WebSocket 推送 ───────────────────────────────────────

    def add_ws_connection(self, ws) -> None:
        """添加 WebSocket 连接"""
        self._ws_connections.add(ws)

    def remove_ws_connection(self, ws) -> None:
        """移除 WebSocket 连接"""
        self._ws_connections.discard(ws)

    async def _broadcast_changes(self, changed: List[str]) -> None:
        """向所有 WebSocket 连接推送配置变更事件"""
        if not self._ws_connections:
            return

        message = json.dumps({
            "type": "config_changed",
            "changed_tables": changed,
            "timestamp": time.time(),
            "checksums": {name: self._hashes.get(name, "")[:8] for name in changed}
        }, ensure_ascii=False)

        disconnected = set()
        for ws in self._ws_connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.add(ws)

        self._ws_connections -= disconnected

    # ─── 状态查询 ──────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """获取热加载管理器状态"""
        return {
            "config_dir": str(self._config_dir),
            "tables_tracked": len(self._hashes),
            "watchdog_active": self._observer is not None and self._observer.is_alive(),
            "polling_active": self._watch_enabled,
            "ws_connections": len(self._ws_connections),
            "pending_changes": list(getattr(self, '_pending_changes', set())),
        }

