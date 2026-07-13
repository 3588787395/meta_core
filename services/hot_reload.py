"""
热加载管理器
===========
独立的热加载模块，从 ConfigStore.check_hot_reload() 提取而来。
支持：
1. Watchdog 文件监控 config/ 目录
2. 三级校验闭环（语法→逻辑→业务规则）
3. HotSwap 原子替换
4. WebSocket 推送配置变更事件
5. 版本记录与回滚
"""

import json
import time
import hashlib
import logging
import asyncio
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class HotReloadManager:
    """热加载管理器：文件监控 + 原子替换 + WebSocket 推送"""

    def __init__(self, config_dir: str, config_store=None, storage=None,
                 schema_validator=None, on_change: Optional[Callable] = None):
        """
        Args:
            config_dir: 配置目录路径
            config_store: ConfigStore 实例（用于加载和替换配置）
            storage: Storage 实例（用于版本记录和回滚）
            schema_validator: SchemaValidator 实例（用于三级校验）
            on_change: 配置变更后的回调函数，签名为 on_change(changed: List[str])
        """
        self._config_dir = Path(config_dir)
        self._config_store = config_store
        self._storage = storage
        self._schema_validator = schema_validator
        self._on_change = on_change
        self._hashes: Dict[str, str] = {}
        self._watch_task: Optional[asyncio.Task] = None
        self._watch_enabled = False
        self._watch_interval = 2.0
        self._ws_connections: Set = set()
        self._observer = None  # watchdog observer

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
        """检测配置文件变更（仅检测，不替换）"""
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

    def validate_and_swap(self, table_name: str, new_data: Dict) -> tuple:
        """校验并原子替换单个配置表

        Returns:
            (success: bool, errors: List[str])
        """
        errors = []

        # 三级校验
        if self._schema_validator is not None:
            # 语法校验
            syntax_results = self._schema_validator.validate_syntax(table_name, new_data)
            syntax_errors = [r for r in syntax_results if r.level == "error"]
            errors.extend(r.message for r in syntax_errors)

            if not syntax_errors:
                # 逻辑校验
                logic_results = self._schema_validator.validate_logic(table_name, new_data)
                logic_errors = [r for r in logic_results if r.level == "error"]
                errors.extend(r.message for r in logic_errors)

                if not logic_errors:
                    # 业务规则校验
                    business_results = self._schema_validator.validate_business(table_name, new_data)
                    business_errors = [r for r in business_results if r.level == "error"]
                    errors.extend(r.message for r in business_errors)

        return len(errors) == 0, errors

    def check_and_reload(self) -> List[str]:
        """检测变更并执行热加载，返回变更的表名列表

        三级校验闭环：
        1. 检测文件变更
        2. 解析新配置
        3. 三级校验（语法→逻辑→业务规则）
        4. 校验通过→原子替换 + 版本记录
        5. 校验失败→保留旧配置，记录错误
        """
        changed = []

        for path in self._config_dir.glob("*.json"):
            name = path.stem
            try:
                raw = path.read_text(encoding="utf-8")
                file_hash = hashlib.md5(raw.encode()).hexdigest()

                if self._hashes.get(name) == file_hash:
                    continue  # 无变更

                # 检测到变更，先解析新配置
                try:
                    new_data = json.loads(raw)
                except json.JSONDecodeError as e:
                    logger.error(f"热加载 {name} 失败: JSON解析错误 {e}，保留旧配置")
                    continue

                # 三级校验
                success, errors = self.validate_and_swap(name, new_data)
                if not success:
                    for err in errors:
                        logger.error(f"热加载 {name} 校验失败: {err}")
                    logger.warning(f"热加载 {name} 校验失败，保留旧配置不变")
                    continue

                # 校验通过，记录旧配置用于版本追踪
                old_data = self._config_store.get(name) if self._config_store else None
                old_content = json.dumps(old_data, ensure_ascii=False) if old_data else None
                new_content = json.dumps(new_data, ensure_ascii=False)

                # 原子替换：更新 ConfigStore
                if self._config_store:
                    self._config_store._tables[name] = new_data
                    self._config_store._hashes[name] = file_hash
                    self._config_store._load_times[name] = time.time()

                # 更新本地哈希
                self._hashes[name] = file_hash
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

        # 触发变更回调
        if changed and self._on_change:
            try:
                self._on_change(changed)
            except Exception as e:
                logger.warning(f"热加载回调执行失败: {e}")

        return changed

    def rollback(self, version_id: str) -> bool:
        """回滚配置到指定版本"""
        if self._storage is None:
            logger.error("回滚失败: 未设置 Storage 引用")
            return False

        try:
            success = self._storage.rollback_config(version_id)
            if not success:
                logger.error(f"回滚配置版本 {version_id} 失败")
                return False

            # 回滚后重新加载所有配置
            if self._config_store:
                self._config_store.load_all()
            self._snapshot_hashes()
            logger.info(f"回滚配置版本 {version_id} 成功")
            return True
        except Exception as e:
            logger.error(f"回滚配置版本 {version_id} 异常: {e}")
            return False

    # ─── 定时轮询模式 ────────────────────────────────────────

    async def start_polling(self, interval: float = 2.0) -> None:
        """启动定时轮询热加载"""
        self._watch_enabled = True
        self._watch_interval = interval
        logger.info(f"热加载轮询已启动 (间隔={interval}s)")

        while self._watch_enabled:
            try:
                changed = self.check_and_reload()
                if changed:
                    await self._broadcast_changes(changed)
            except Exception as e:
                logger.error(f"热加载轮询异常: {e}")
            await asyncio.sleep(interval)

    def stop_polling(self) -> None:
        """停止定时轮询"""
        self._watch_enabled = False
        logger.info("热加载轮询已停止")

    # ─── Watchdog 模式 ───────────────────────────────────────

    def start_watchdog(self) -> bool:
        """启动 watchdog 文件监控"""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileModifiedEvent

            class ConfigHandler(FileSystemEventHandler):
                def __init__(self, manager: HotReloadManager):
                    self._manager = manager

                def on_modified(self, event):
                    if event.is_directory or not event.src_path.endswith('.json'):
                        return
                    name = Path(event.src_path).stem
                    logger.debug(f"Watchdog 检测到变更: {name}")
                    # 标记需要重新加载
                    self._manager._pending_changes.add(name)

            self._pending_changes: Set[str] = set()
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
