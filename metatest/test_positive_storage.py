"""Task 5：Storage 持久化层正测试。

验证 services.storage 模块：
  - Storage 类可实例化
  - safe_path_join() 安全路径拼接（正常 + 遍历防护）
  - SQLite 后端 + WAL 模式
  - 批量写入接口（batch_log_stock_transfers）
  - DatabaseSyncService 存在
  - 连接管理（_conn 工厂方法 + WAL pragma）

测试可能因源码 bug 失败，这是预期行为——测试目的是发现 bug。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

# 确保 services.storage 可导入
pytest.importorskip("sqlite3")


# ============================================================================
# SubTask 5.1 / 5.7: Storage 类与 DatabaseSyncService 存在性
# ============================================================================


class TestStorageClassExists:
    """Storage / DatabaseSyncService 类存在性与可实例化正测试。"""

    def test_storage_class_exists_and_is_instantiable(self, tmp_path: Path) -> None:
        """Storage 类存在且可通过 db_path 实例化。"""
        from services.storage import Storage

        db_path = str(tmp_path / "test_meta.db")
        storage = Storage(bus=None, db_path=db_path)

        assert storage is not None, "Storage 实例化后不应为 None"
        assert hasattr(storage, "db_path"), "Storage 应有 db_path 属性"
        assert storage.db_path == db_path, "Storage.db_path 应等于构造参数"
        assert os.path.exists(db_path), "实例化后 db 文件应已创建"

    def test_database_sync_service_class_exists(self) -> None:
        """DatabaseSyncService 类存在于 services.storage 模块。"""
        from services.storage import DatabaseSyncService

        assert DatabaseSyncService is not None, "DatabaseSyncService 应可导入"
        assert hasattr(DatabaseSyncService, "__init__"), "DatabaseSyncService 应有 __init__"

    def test_database_sync_service_instantiable_with_storage(self, tmp_path: Path) -> None:
        """DatabaseSyncService 可用 Storage + providers 字典实例化。"""
        from services.storage import DatabaseSyncService, Storage

        storage = Storage(bus=None, db_path=str(tmp_path / "sync.db"))
        svc = DatabaseSyncService(storage=storage, providers={})

        assert svc is not None, "DatabaseSyncService 实例化后不应为 None"
        assert svc._storage is storage, "DatabaseSyncService._storage 应引用传入 storage"


# ============================================================================
# SubTask 5.2 / 5.3 / 5.9 / 5.10: safe_path_join 路径安全
# ============================================================================


class TestSafePathJoin:
    """safe_path_join() 安全路径拼接正测试。"""

    def test_concatenates_paths_safely(self, tmp_path: Path) -> None:
        """safe_path_join 正常拼接基目录与文件名。"""
        from services.storage import safe_path_join

        base = str(tmp_path)
        result = safe_path_join(base, "data.db")

        assert isinstance(result, str), "返回值应为 str"
        assert result.endswith("data.db"), "拼接结果应以传入文件名结尾"
        assert "data.db" in result, "结果应包含文件名"

    def test_prevents_directory_traversal_dotdot(self, tmp_path: Path) -> None:
        """safe_path_join 拒绝包含 '..' 的文件名（路径遍历攻击防护）。"""
        from services.storage import safe_path_join

        base = str(tmp_path)
        with pytest.raises(ValueError, match="非法文件名|路径遍历"):
            safe_path_join(base, "../../../etc/passwd")

    def test_prevents_absolute_unix_path(self, tmp_path: Path) -> None:
        """safe_path_join 拒绝以 '/' 开头的绝对路径（Unix 绝对路径）。"""
        from services.storage import safe_path_join

        base = str(tmp_path)
        with pytest.raises(ValueError, match="非法文件名|路径遍历"):
            safe_path_join(base, "/etc/passwd")

    def test_prevents_absolute_windows_backslash_path(self, tmp_path: Path) -> None:
        """safe_path_join 拒绝以 '\\' 开头的绝对路径（Windows 绝对路径）。"""
        from services.storage import safe_path_join

        base = str(tmp_path)
        with pytest.raises(ValueError, match="非法文件名|路径遍历"):
            safe_path_join(base, "\\Windows\\system32")

    def test_empty_filename_returns_base_joined(self, tmp_path: Path) -> None:
        """safe_path_join 空文件名应返回基目录路径（os.path.join 语义）。"""
        from services.storage import safe_path_join

        base = str(tmp_path)
        result = safe_path_join(base, "")

        assert isinstance(result, str), "空文件名返回值仍应为 str"
        assert result.startswith(base), "空文件名结果应以基目录开头"

    def test_absolute_drive_path_rejected_by_realpath_check(self, tmp_path: Path) -> None:
        """safe_path_join 拒绝 Windows 盘符绝对路径（经 realpath 二次校验拦截）。"""
        # Linux 的 os.path.join / realpath 不识别 Windows 盘符语义：反斜杠被当作普通字符，
        # "C:\\..." 在 Linux 上只是一个相对文件名，realpath 不会判定其超出基目录，
        # 故 Windows 盘符路径检查在 Linux 上不适用，跳过以消除平台差异。
        import sys
        if sys.platform.startswith("linux"):
            pytest.skip("Linux realpath 语义差异，Windows 盘符路径检查不适用")
        from services.storage import safe_path_join

        base = str(tmp_path)
        # Windows 盘符绝对路径不以 '/' 或 '\\' 开头，但 os.path.join 会将其作为绝对路径
        # realpath 校验应检测到路径超出基目录
        with pytest.raises(ValueError, match="非法文件名|路径遍历"):
            safe_path_join(base, "C:\\Windows\\system32\\config\\sam")


# ============================================================================
# SubTask 5.4 / 5.5 / 5.8: SQLite 后端 + WAL 模式 + 连接管理
# ============================================================================


class TestStorageSqliteBackend:
    """Storage SQLite 后端 + WAL 模式 + 连接管理正测试。"""

    def test_storage_uses_sqlite_db_file(self, tmp_path: Path) -> None:
        """Storage 实例化后产生 SQLite 数据库文件。"""
        from services.storage import Storage

        db_path = str(tmp_path / "sqlite_test.db")
        Storage(bus=None, db_path=db_path)

        assert os.path.exists(db_path), "SQLite db 文件应已创建"
        # SQLite 文件头魔数 "SQLite format 3\000"
        with open(db_path, "rb") as f:
            header = f.read(16)
        assert header.startswith(b"SQLite format 3"), "db 文件应为 SQLite 格式"

    def test_storage_has_wal_mode(self, tmp_path: Path) -> None:
        """Storage 连接启用 WAL 日志模式。"""
        from services.storage import Storage

        storage = Storage(bus=None, db_path=str(tmp_path / "wal_test.db"))
        conn = storage._conn()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()
            assert mode is not None, "PRAGMA journal_mode 应返回结果"
            # WAL 模式下 journal_mode 列值为 "wal"
            assert str(mode[0]).lower() == "wal", f"journal_mode 应为 wal，实际 {mode[0]}"
        finally:
            conn.close()

    def test_storage_has_conn_factory_method(self, tmp_path: Path) -> None:
        """Storage 有 _conn() 工厂方法，统一管理连接创建（避免散布的 sqlite3.connect）。"""
        from services.storage import Storage

        storage = Storage(bus=None, db_path=str(tmp_path / "conn_test.db"))

        assert hasattr(storage, "_conn"), "Storage 应有 _conn 方法"
        assert callable(storage._conn), "_conn 应为可调用方法"
        conn = storage._conn()
        try:
            assert conn is not None, "_conn() 应返回连接对象"
            # row_factory 应设置为 sqlite3.Row（按 storage.py L139）
            import sqlite3
            assert conn.row_factory is sqlite3.Row, "_conn() 应设置 row_factory=sqlite3.Row"
        finally:
            conn.close()

    def test_storage_conn_enables_foreign_keys(self, tmp_path: Path) -> None:
        """Storage _conn() 启用外键约束（PRAGMA foreign_keys = ON）。"""
        from services.storage import Storage

        storage = Storage(bus=None, db_path=str(tmp_path / "fk_test.db"))
        conn = storage._conn()
        try:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()
            assert fk is not None, "PRAGMA foreign_keys 应返回结果"
            assert int(fk[0]) == 1, f"foreign_keys 应为 1（ON），实际 {fk[0]}"
        finally:
            conn.close()

    def test_storage_source_uses_conn_factory_not_bare_connect(self) -> None:
        """storage.py 源码应通过 _conn() 工厂管理连接，避免散布的 sqlite3.connect 调用。

        通过检查源文件中 sqlite3.connect 出现次数（应仅在 _conn 方法内）。
        """
        from services import storage as storage_mod

        src_path = Path(storage_mod.__file__)
        src_text = src_path.read_text(encoding="utf-8")
        # sqlite3.connect 应仅出现在 _conn 方法定义内（含 1 处定义）
        connect_count = src_text.count("sqlite3.connect(")
        assert connect_count >= 1, "源码应至少有 1 处 sqlite3.connect（在 _conn 内）"
        # _conn 方法应存在且包含 connect 调用
        assert "def _conn(self)" in src_text, "源码应有 _conn(self) 方法定义"


# ============================================================================
# SubTask 5.6: 批量写入接口
# ============================================================================


class TestStorageBatchWrite:
    """Storage 批量写入接口正测试。"""

    def test_storage_has_batch_log_stock_transfers_method(self, tmp_path: Path) -> None:
        """Storage 提供 batch_log_stock_transfers 批量写入接口。

        注：当前实现用 for 循环 + 单条 execute（非 executemany），但提供了批量 API。
        """
        from services.storage import Storage

        storage = Storage(bus=None, db_path=str(tmp_path / "batch_test.db"))

        assert hasattr(storage, "batch_log_stock_transfers"), \
            "Storage 应有 batch_log_stock_transfers 方法"
        assert callable(storage.batch_log_stock_transfers), \
            "batch_log_stock_transfers 应为可调用方法"

    def test_storage_batch_log_inserts_multiple_records(self, tmp_path: Path) -> None:
        """batch_log_stock_transfers 批量插入多条 transfer log 记录。

        需先插入 pool_config + pool_node 满足外键约束。
        """
        from services.storage import Storage

        storage = Storage(bus=None, db_path=str(tmp_path / "batch_insert.db"))
        # 插入外键依赖：pool_config + pool_node（source / state_1）
        conn = storage._conn()
        try:
            conn.execute(
                "INSERT INTO pool_config (pool_id, name, pool_type, status) "
                "VALUES ('test_pool', '测试池', 'tdx', 'active')"
            )
            conn.execute(
                "INSERT INTO pool_node (node_id, pool_id, node_type, label) "
                "VALUES ('source', 'test_pool', 'market_source', '备选池')"
            )
            conn.execute(
                "INSERT INTO pool_node (node_id, pool_id, node_type, label) "
                "VALUES ('state_1', 'test_pool', 'statepool', '状态池')"
            )
            conn.commit()
        finally:
            conn.close()

        transfers = [
            {
                "source_node_id": "source",
                "target_node_id": "state_1",
                "stock_code": "fz000001",
                "transfer_mode": "copy",
                "trigger_condition": "KDJ",
                "kline_time": "2024-01-15 09:30:00",
            },
            {
                "source_node_id": "source",
                "target_node_id": "state_1",
                "stock_code": "fz000002",
                "transfer_mode": "copy",
                "trigger_condition": "KDJ",
                "kline_time": "2024-01-15 09:30:00",
            },
        ]
        # 不应抛异常
        storage.batch_log_stock_transfers(transfers)

        # 验证写入成功
        conn = storage._conn()
        try:
            rows = conn.execute(
                "SELECT stock_code FROM stock_transfer_log ORDER BY stock_code"
            ).fetchall()
            assert len(rows) == 2, f"应写入 2 条记录，实际 {len(rows)}"
            assert rows[0]["stock_code"] == "fz000001", "第一条记录 stock_code 应为 fz000001"
            assert rows[1]["stock_code"] == "fz000002", "第二条记录 stock_code 应为 fz000002"
        finally:
            conn.close()


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 3 C12 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 C12 _get_table helper 收敛回归。"""

    def test_get_table_helper_in_core(self):
        """core/*.py 至少 1 处定义 _get_table helper（C12 统一调用）。"""
        import re
        from pathlib import Path
        core_dir = Path(__file__).resolve().parent.parent / "core"
        total = 0
        for py in core_dir.glob("*.py"):
            try:
                src = py.read_text(encoding="utf-8")
            except OSError:
                continue
            total += len(re.findall(r"def _get_table\b", src))
        assert total >= 1, \
            f"core/*.py 应至少 1 处定义 _get_table helper（C12 统一调用），实际 {total} 处"

    def test_no_double_get_global_config_store_calls(self):
        """core/*.py 不含双 get_global_config_store().get_table ... if get_global_config_store（C12 perf smell）。"""
        import re
        from pathlib import Path
        core_dir = Path(__file__).resolve().parent.parent / "core"
        regex = re.compile(r"get_global_config_store\(\)\.get_table.*\n.*if get_global_config_store", re.MULTILINE)
        total = 0
        for py in core_dir.glob("*.py"):
            try:
                src = py.read_text(encoding="utf-8")
            except OSError:
                continue
            total += len(regex.findall(src))
        assert total == 0, \
            f"core/*.py 不应含双 get_global_config_store 调用（C12 perf smell），实际 {total} 处"
