"""Storage 持久化层模块

职责边界说明：
    - Storage 仅负责对本地 SQLite 数据库的持久化 CRUD 操作，包括 9 张统一表
      （pool_config / pool_node / pool_edge / node_state / stock_transfer_log /
      replay_session / replay_snapshot / kline_cache / config_version）及
      备选池相关表（stocks / sectors / sector_members / user_blocks / user_block_members）。
    - Storage 不主动调用任何数据源层（TqAdapter / Provider / akshare / HQChart 等）。
    - kline_cache 表仅由 Storage 提供 CRUD 接口（save_kline / get_klines /
      clean_expired_kline_cache / prefetch_pool_klines）；数据预缓存由数据源层负责，
      Storage 不主动预取外部 K 线数据。
    - 任何需要联动数据源的逻辑应由上层（services / 业务层）编排，不得在 Storage 内反向引用。
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def safe_path_join(base: str, filename: str) -> str:
    """安全路径拼接：防止路径遍历攻击"""
    import os
    # 拒绝明显的遍历尝试
    if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
        raise ValueError(f"非法文件名: {filename}")
    joined = os.path.join(base, filename)
    # 二次校验：解析后路径必须在基目录内
    real_base = os.path.realpath(base)
    real_joined = os.path.realpath(joined)
    if not real_joined.startswith(real_base + os.sep) and real_joined != real_base:
        raise ValueError(f"路径遍历检测: {filename} 超出基目录 {base}")
    return joined


class Storage:
    # Bug #14 修复：合法表名白名单，防止 SQL 注入
    _VALID_TABLES = frozenset({
        'pool_config', 'pool_node', 'pool_edge', 'node_state',
        'stock_transfer_log', 'replay_session', 'replay_snapshot',
        'kline_cache', 'config_version', 'pools', 'executions',
    })

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent / "data" / "meta.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------
    # Unified 9-table schema
    # ------------------------------------------------------------------
    def _init_db(self):
        with self._conn() as conn:
            # 1. pool_config
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pool_config (
                    pool_id       TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    pool_type     TEXT NOT NULL DEFAULT 'dzh' CHECK (pool_type IN ('dzh', 'tdx')),
                    description   TEXT,
                    xml_source    TEXT,
                    topology_mode TEXT DEFAULT 'flow',
                    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived', 'deleted')),
                    params        TEXT DEFAULT '{}',
                    created_at    DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at    DATETIME NOT NULL DEFAULT (datetime('now','localtime'))
                )
            """)
            # 向后兼容：若旧表已存在但缺少 params 列，则添加
            try:
                conn.execute("ALTER TABLE pool_config ADD COLUMN params TEXT DEFAULT '{}'"
)
            except sqlite3.OperationalError:
                pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_config_status ON pool_config(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_config_name ON pool_config(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_config_pool_type ON pool_config(pool_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_config_created ON pool_config(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_config_updated ON pool_config(updated_at)")

            # 2. pool_node
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pool_node (
                    node_id     TEXT PRIMARY KEY,
                    pool_id     TEXT NOT NULL,
                    node_type   TEXT NOT NULL,
                    label       TEXT,
                    pos_x       REAL DEFAULT 0,
                    pos_y       REAL DEFAULT 0,
                    width       REAL DEFAULT 120,
                    height      REAL DEFAULT 60,
                    params      TEXT DEFAULT '{}',
                    created_at  DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (pool_id) REFERENCES pool_config(pool_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_node_pool_id ON pool_node(pool_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_node_type ON pool_node(node_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_node_created ON pool_node(created_at)")

            # 3. pool_edge
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pool_edge (
                    edge_id        TEXT PRIMARY KEY,
                    pool_id        TEXT NOT NULL,
                    source_node_id TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    params         TEXT DEFAULT '{}',
                    created_at     DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (pool_id) REFERENCES pool_config(pool_id) ON DELETE CASCADE,
                    FOREIGN KEY (source_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE,
                    FOREIGN KEY (target_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE,
                    CHECK (source_node_id != target_node_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_edge_pool_id ON pool_edge(pool_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_edge_source ON pool_edge(source_node_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_edge_target ON pool_edge(target_node_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_edge_created ON pool_edge(created_at)")

            # 4. node_state
            conn.execute("""
                CREATE TABLE IF NOT EXISTS node_state (
                    record_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id         TEXT NOT NULL,
                    stock_code      TEXT NOT NULL,
                    entered_at      DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
                    left_at         DATETIME,
                    current_state   TEXT NOT NULL DEFAULT 'in' CHECK (current_state IN ('in', 'out')),
                    FOREIGN KEY (node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_node_state_node_in ON node_state(node_id, current_state)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_node_state_stock ON node_state(stock_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_node_state_entered ON node_state(entered_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_node_state_left ON node_state(left_at)")

            # 5. stock_transfer_log
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_transfer_log (
                    log_id            TEXT PRIMARY KEY,
                    ts                DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
                    source_node_id    TEXT NOT NULL,
                    target_node_id    TEXT NOT NULL,
                    stock_code        TEXT NOT NULL,
                    transfer_mode     TEXT NOT NULL CHECK (transfer_mode IN ('copy', 'move', 'overwrite', 'constituent', 'force_move', 'pass_through')),
                    trigger_condition TEXT,
                    kline_time        DATETIME,
                    FOREIGN KEY (source_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE,
                    FOREIGN KEY (target_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_transfer_log_ts ON stock_transfer_log(ts DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_transfer_log_stock ON stock_transfer_log(stock_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_transfer_log_source ON stock_transfer_log(source_node_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_transfer_log_target ON stock_transfer_log(target_node_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_transfer_log_kline ON stock_transfer_log(kline_time)")

            # 6. replay_session
            conn.execute("""
                CREATE TABLE IF NOT EXISTS replay_session (
                    session_id   TEXT PRIMARY KEY,
                    pool_id      TEXT NOT NULL,
                    base_period  TEXT NOT NULL DEFAULT 'day' CHECK (base_period IN ('day', '5min', '1min')),
                    start_date   DATE NOT NULL,
                    end_date     DATE NOT NULL,
                    play_speed   REAL NOT NULL DEFAULT 1.0,
                    current_time DATETIME,
                    kline_index  INTEGER DEFAULT 0,
                    status       TEXT NOT NULL DEFAULT 'loading' CHECK (status IN ('loading', 'playing', 'paused', 'finished')),
                    created_at   DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at   DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (pool_id) REFERENCES pool_config(pool_id) ON DELETE CASCADE,
                    CHECK (end_date >= start_date)
                )
            """)
            # 迁移：为旧表添加 updated_at 列
            try:
                conn.execute("ALTER TABLE replay_session ADD COLUMN updated_at DATETIME NOT NULL DEFAULT (datetime('now','localtime'))")
            except Exception:
                pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_session_pool ON replay_session(pool_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_session_status ON replay_session(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_session_created ON replay_session(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_session_updated ON replay_session(updated_at)")

            # 7. replay_snapshot
            conn.execute("""
                CREATE TABLE IF NOT EXISTS replay_snapshot (
                    snapshot_id   TEXT PRIMARY KEY,
                    session_id    TEXT NOT NULL,
                    ts            DATETIME NOT NULL,
                    node_states   TEXT NOT NULL DEFAULT '{}',
                    recent_events TEXT DEFAULT '[]',
                    kline_data    TEXT DEFAULT '[]',
                    FOREIGN KEY (session_id) REFERENCES replay_session(session_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_replay_snapshot_session_ts ON replay_snapshot(session_id, ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_snapshot_session ON replay_snapshot(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_snapshot_ts ON replay_snapshot(ts)")

            # 8. kline_cache
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kline_cache (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code  TEXT NOT NULL,
                    period      TEXT NOT NULL,
                    kline_time  TEXT NOT NULL,
                    open        REAL NOT NULL,
                    high        REAL NOT NULL,
                    low         REAL NOT NULL,
                    close       REAL NOT NULL,
                    volume      REAL NOT NULL,
                    amount      REAL DEFAULT 0,
                    cached_at   DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
                    UNIQUE (stock_code, period, kline_time)
                )
            """)
            # 兼容迁移：为旧表添加 cached_at 列
            try:
                conn.execute("ALTER TABLE kline_cache ADD COLUMN cached_at DATETIME NOT NULL DEFAULT (datetime('now','localtime'))")
            except Exception:
                pass

            # 9. config_version
            conn.execute("""
                CREATE TABLE IF NOT EXISTS config_version (
                    version_id  TEXT PRIMARY KEY,
                    table_name  TEXT NOT NULL,
                    change_type TEXT NOT NULL CHECK (change_type IN ('create', 'update', 'delete')),
                    old_content TEXT,
                    new_content TEXT,
                    created_at  DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
                    created_by  TEXT DEFAULT 'system'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_config_version_table ON config_version(table_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_config_version_created ON config_version(created_at)")

            # 备选池相关表
            self._init_candidate_pool_tables(conn)

            # 兼容旧版表（executions / pools）— 必须在主表建好之后执行
            # 这样 save_execution / save_pool 等旧 API 不会因缺表而失败
            self._init_legacy_tables(conn)

            conn.commit()

    # ------------------------------------------------------------------
    # config_version helpers
    # ------------------------------------------------------------------
    def record_config_version(self, table_name: str, change_type: str,
                              old_content: str = None, new_content: str = None,
                              created_by: str = 'system', conn=None) -> str:
        """Record a configuration version entry for auditing / rollback."""
        version_id = uuid.uuid4().hex
        _conn = conn or self._conn()
        own_conn = conn is None
        try:
            _conn.execute("""
                INSERT INTO config_version (version_id, table_name, change_type, old_content, new_content, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (version_id, table_name, change_type, old_content, new_content, created_by))
            if own_conn:
                _conn.commit()
        finally:
            if own_conn:
                _conn.close()
        return version_id

    def rollback_config(self, version_id: str) -> bool:
        """Roll back a configuration change by version_id.

        Looks up the config_version record. For 'create' changes, deletes the
        created row. For 'delete' changes, re-inserts the old row. For 'update'
        changes, restores the old_content.
        Returns True on success, False if version not found or rollback fails.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM config_version WHERE version_id=?", (version_id,)
            ).fetchone()
            if not row:
                return False

            table_name = row["table_name"]
            change_type = row["change_type"]
            old_content = row["old_content"]
            new_content = row["new_content"]

            # Bug #14 修复：校验 table_name 白名单，防止 SQL 注入
            if table_name not in self._VALID_TABLES:
                return False

            try:
                if change_type == "create" and new_content:
                    data = json.loads(new_content) if isinstance(new_content, str) else new_content
                    # Determine the primary key column for the table
                    pk_col = self._get_pk_column(conn, table_name)
                    if pk_col and pk_col in data:
                        conn.execute(
                            f"DELETE FROM {table_name} WHERE {pk_col}=?",
                            (data[pk_col],)
                        )

                elif change_type == "delete" and old_content:
                    data = json.loads(old_content) if isinstance(old_content, str) else old_content
                    if isinstance(data, dict):
                        cols = list(data.keys())
                        placeholders = ",".join(["?"] * len(cols))
                        col_names = ",".join(cols)
                        conn.execute(
                            f"INSERT OR REPLACE INTO {table_name} ({col_names}) VALUES ({placeholders})",
                            tuple(data[c] for c in cols)
                        )

                elif change_type == "update" and old_content:
                    data = json.loads(old_content) if isinstance(old_content, str) else old_content
                    if isinstance(data, dict):
                        pk_col = self._get_pk_column(conn, table_name)
                        if pk_col and pk_col in data:
                            set_clauses = []
                            values = []
                            for k, v in data.items():
                                if k != pk_col:
                                    set_clauses.append(f"{k}=?")
                                    values.append(v)
                            if set_clauses:
                                values.append(data[pk_col])
                                conn.execute(
                                    f"UPDATE {table_name} SET {','.join(set_clauses)} WHERE {pk_col}=?",
                                    tuple(values)
                                )

                # Remove the version record after successful rollback
                conn.execute("DELETE FROM config_version WHERE version_id=?", (version_id,))
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                return False

    def _get_pk_column(self, conn, table_name: str) -> Optional[str]:
        """Get the primary key column name for a table."""
        # Bug #14 修复：校验 table_name 白名单
        if table_name not in self._VALID_TABLES:
            return None
        try:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            for r in rows:
                if r["pk"] > 0:
                    return r["name"]
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Backward-compatible API: pools → pool_config
    # ------------------------------------------------------------------
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_pool(self, pool_id: str, pool_data: Dict) -> None:
        now = self._now()
        name = pool_data.get("name", "")
        pool_type = pool_data.get("pool_type", "dzh")
        description = pool_data.get("description")
        xml_source = pool_data.get("xml_source")
        topology_mode = pool_data.get("topology_mode", "flow")
        status = pool_data.get("status", "draft")
        params = pool_data.get("params")
        if params is None:
            # Derive params from nodes/edges if present
            nodes = pool_data.get("nodes")
            edges = pool_data.get("edges")
            if nodes is not None or edges is not None:
                params = {"nodes": nodes or [], "edges": edges or []}
                if "pool_meta" in pool_data:
                    params["pool_meta"] = pool_data["pool_meta"]
                if "trades" in pool_data:
                    params["trades"] = pool_data["trades"]
                if "opentrades" in pool_data:
                    params["opentrades"] = pool_data["opentrades"]
        params_json = json.dumps(params, ensure_ascii=False) if params else None
        change_type = 'create'

        with self._conn() as conn:
            old_row = conn.execute("SELECT * FROM pool_config WHERE pool_id=?", (pool_id,)).fetchone()
            if old_row:
                change_type = 'update'

            conn.execute("""
                INSERT INTO pool_config (pool_id, name, pool_type, description, xml_source,
                    topology_mode, status, params, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT created_at FROM pool_config WHERE pool_id=?), datetime('now','localtime')),
                    datetime('now','localtime'))
                ON CONFLICT(pool_id) DO UPDATE SET
                    name=excluded.name, pool_type=excluded.pool_type,
                    description=excluded.description, xml_source=excluded.xml_source,
                    topology_mode=excluded.topology_mode, status=excluded.status,
                    params=COALESCE(excluded.params, params),
                    updated_at=datetime('now','localtime')
            """, (pool_id, name, pool_type, description, xml_source,
                  topology_mode, status, params_json, pool_id))
            conn.commit()

        # Record version AFTER connection is closed to avoid lock
        old_content = json.dumps(dict(old_row), ensure_ascii=False, default=str) if old_row else None
        with self._conn() as conn:
            new_row = conn.execute("SELECT * FROM pool_config WHERE pool_id=?", (pool_id,)).fetchone()
            new_content = json.dumps(dict(new_row), ensure_ascii=False, default=str) if new_row else None
        self.record_config_version('pool_config', change_type, old_content, new_content)

    def _parse_pool_row(self, row: sqlite3.Row) -> Dict:
        d = dict(row)
        params = d.get("params")
        if isinstance(params, str):
            try:
                d["params"] = json.loads(params)
            except json.JSONDecodeError:
                d["params"] = {}
        return d

    def get_pool(self, pool_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM pool_config WHERE pool_id=?", (pool_id,)).fetchone()
            if not row:
                return None
            return self._parse_pool_row(row)

    def list_pools(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM pool_config ORDER BY updated_at DESC").fetchall()
            return [self._parse_pool_row(r) for r in rows]

    def delete_pool(self, pool_id: str) -> None:
        old_row = None
        with self._conn() as conn:
            old_row = conn.execute("SELECT * FROM pool_config WHERE pool_id=?", (pool_id,)).fetchone()
        old_content = json.dumps(dict(old_row), ensure_ascii=False, default=str) if old_row else None
        with self._conn() as conn:
            conn.execute("DELETE FROM pool_config WHERE pool_id=?", (pool_id,))
            conn.commit()
        if old_content:
            self.record_config_version('pool_config', 'delete', old_content)

    # ------------------------------------------------------------------
    # Backward-compatible API: executions (kept as-is for compatibility)
    # ------------------------------------------------------------------
    def save_execution(self, pool_id: str, result_dict: Dict) -> None:
        exec_id = uuid.uuid4().hex
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO executions (id, pool_id, result, created_at) VALUES (?, ?, ?, ?)",
                (exec_id, pool_id, json.dumps(result_dict, ensure_ascii=False), now),
            )
            conn.commit()

    def get_executions(self, pool_id: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM executions WHERE pool_id=? ORDER BY created_at DESC", (pool_id,)
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "pool_id": r["pool_id"],
                    "result": json.loads(r["result"]),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    def get_last_execution(self, pool_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM executions WHERE pool_id=? ORDER BY created_at DESC LIMIT 1",
                (pool_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "pool_id": row["pool_id"],
                "result": json.loads(row["result"]),
                "created_at": row["created_at"],
            }

    # ------------------------------------------------------------------
    # Candidate pool tables (备选池相关表)
    # ------------------------------------------------------------------
    def _init_candidate_pool_tables(self, conn):
        """初始化备选池相关的5张数据库表及索引。"""

        # 1. stocks 表（股票主表）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                stock_code      TEXT PRIMARY KEY,
                raw_code        TEXT NOT NULL,
                name            TEXT NOT NULL,
                market          TEXT NOT NULL,
                list_date       DATE,
                delist_date     DATE,
                status          TEXT DEFAULT 'active',
                industry_sw     TEXT,
                industry_csrc   TEXT,
                market_cap      REAL,
                float_cap       REAL,
                updated_at      DATETIME NOT NULL,
                created_at      DATETIME NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_market ON stocks(market)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_status ON stocks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_industry_sw ON stocks(industry_sw)")

        # 2. sectors 表（板块主表）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sectors (
                sector_id       TEXT PRIMARY KEY,
                sector_code     TEXT NOT NULL,
                sector_name     TEXT NOT NULL,
                category        TEXT NOT NULL,
                sub_category    TEXT,
                source          TEXT NOT NULL DEFAULT 'akshare',
                description     TEXT,
                member_count    INTEGER DEFAULT 0,
                parent_id       TEXT,
                updated_at      DATETIME NOT NULL,
                created_at      DATETIME NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sectors_category ON sectors(category, source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sectors_source ON sectors(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sectors_parent ON sectors(parent_id)")

        # 3. sector_members 表（板块成分股关系表）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sector_members (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sector_id       TEXT NOT NULL,
                stock_code      TEXT NOT NULL,
                in_date         DATE,
                out_date        DATE,
                weight          REAL DEFAULT 1.0,
                is_current      INTEGER DEFAULT 1,
                updated_at      DATETIME NOT NULL,
                FOREIGN KEY (sector_id) REFERENCES sectors(sector_id),
                FOREIGN KEY (stock_code) REFERENCES stocks(stock_code),
                UNIQUE(sector_id, stock_code, is_current)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sector_members_sector ON sector_members(sector_id, is_current)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sector_members_stock ON sector_members(stock_code)")

        # 4. user_blocks 表（用户自定义板块表）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_blocks (
                block_code      TEXT PRIMARY KEY,
                block_name      TEXT NOT NULL,
                block_type      TEXT DEFAULT 'custom',
                description     TEXT,
                source          TEXT DEFAULT 'manual',
                source_sector_id TEXT,
                auto_refresh    INTEGER DEFAULT 0,
                refresh_interval INTEGER DEFAULT 300,
                resolved_from   TEXT,
                updated_at      DATETIME NOT NULL,
                created_at      DATETIME NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_blocks_type ON user_blocks(block_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_blocks_auto_refresh ON user_blocks(auto_refresh)")

        # 5. user_block_members 表（用户自定义板块成员表）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_block_members (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                block_code      TEXT NOT NULL,
                stock_code      TEXT NOT NULL,
                add_time        DATETIME NOT NULL,
                sort_order      INTEGER DEFAULT 0,
                FOREIGN KEY (block_code) REFERENCES user_blocks(block_code),
                FOREIGN KEY (stock_code) REFERENCES stocks(stock_code)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_block_members_block ON user_block_members(block_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_block_members_stock ON user_block_members(stock_code)")

        # 6. data_source_sync_log 表（数据源同步日志表）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS data_source_sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                status TEXT NOT NULL,
                record_count INTEGER DEFAULT 0,
                error_message TEXT,
                synced_at DATETIME NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_log_source ON data_source_sync_log(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_log_entity ON data_source_sync_log(entity_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_log_synced_at ON data_source_sync_log(synced_at DESC)")

    # ------------------------------------------------------------------
    # Backward-compatible API: pool_cells / pool_flows / pool_stocks / pool_trades
    # (legacy tables kept for compatibility, created if not exist)
    # ------------------------------------------------------------------
    def _init_legacy_tables(self, conn):
        """Ensure legacy tables exist for backward compatibility."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pools (
                id TEXT PRIMARY KEY,
                name TEXT,
                config TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY,
                pool_id TEXT,
                result TEXT,
                created_at TEXT
            )
        """)






    def get_pool_stocks(self, pool_id: str, cell_id: str = None) -> List[Dict]:
        """Read stocks from pool_config.params (new unified schema)."""
        pool = self.get_pool(pool_id)
        if not pool:
            return []
        params = pool.get("params") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {}
        nodes = params.get("nodes", [])
        stocks = []
        for node in nodes:
            node_id = node.get("id", "")
            if cell_id is not None and node_id != cell_id:
                continue
            node_params = node.get("params", {})
            node_stocks = node_params.get("stocks", [])
            for stk in node_stocks:
                stock_entry = {
                    "cell_id": node_id,
                    "label": stk.get("label", ""),
                    "t": stk.get("t", ""),
                    "p": stk.get("p", ""),
                }
                tid = stk.get("tid")
                if tid is not None:
                    stock_entry["tid"] = tid
                stocks.append(stock_entry)
        return stocks

    def get_pool_counts(self, pool_id: str) -> Dict:
        pool = self.get_pool(pool_id)
        if not pool:
            return {"node_count": 0, "edge_count": 0}
        params = pool.get("params") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {}
        nodes = params.get("nodes", [])
        edges = params.get("edges", [])
        return {"node_count": len(nodes), "edge_count": len(edges)}

    # ------------------------------------------------------------------
    # Backward-compatible API: node_state (new unified schema)
    # ------------------------------------------------------------------
    def save_node_state(self, pool_id: str, node_id: str, stock_code: str,
                        enter_price: float, entered_at: str, state: str = 'in') -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO node_state (node_id, stock_code, entered_at, current_state)
                VALUES (?, ?, ?, ?)
            """, (node_id, stock_code, entered_at, state))
            conn.commit()

    def get_node_stocks(self, pool_id: str, node_id: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM node_state
                WHERE node_id=? AND current_state='in'
                ORDER BY entered_at DESC
            """, (node_id,)).fetchall()
            return [dict(r) for r in rows]

    def remove_stock_from_node(self, pool_id: str, node_id: str, stock_code: str,
                               left_at: str = None) -> None:
        if left_at is None:
            left_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._conn() as conn:
            conn.execute("""
                UPDATE node_state SET current_state='out', left_at=?
                WHERE node_id=? AND stock_code=? AND current_state='in'
            """, (left_at, node_id, stock_code))
            conn.commit()

    def clear_node_stocks(self, pool_id: str, node_id: str) -> None:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._conn() as conn:
            conn.execute("""
                UPDATE node_state SET current_state='out', left_at=?
                WHERE node_id=? AND current_state='in'
            """, (now, node_id))
            conn.commit()

    # ------------------------------------------------------------------
    # Backward-compatible API: stock_transfer_log (new unified schema)
    # ------------------------------------------------------------------
    def log_stock_transfer(self, pool_id: str, source_node_id: str, target_node_id: str,
                          stock_code: str, transfer_mode: str, trigger: str = None,
                          kline_time: str = None) -> str:
        log_id = uuid.uuid4().hex
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO stock_transfer_log
                (log_id, source_node_id, target_node_id, stock_code, transfer_mode, trigger_condition, kline_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (log_id, source_node_id, target_node_id, stock_code, transfer_mode, trigger, kline_time))
            conn.commit()
        return log_id

    def insert_transfer_log(self, log_id: str, source_node_id: str, target_node_id: str,
                            stock_code: str, transfer_mode: str, trigger_condition: str = None,
                            kline_time: str = None) -> str:
        """Alias for log_stock_transfer with explicit log_id."""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO stock_transfer_log
                (log_id, source_node_id, target_node_id, stock_code, transfer_mode, trigger_condition, kline_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (log_id, source_node_id, target_node_id, stock_code, transfer_mode, trigger_condition, kline_time))
            conn.commit()
        return log_id

    def get_transfer_logs(self, pool_id: str, limit: int = 100) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM stock_transfer_log
                ORDER BY ts DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def batch_log_stock_transfers(self, transfers: List[Dict]) -> None:
        """Batch insert stock transfer logs."""
        # Bug #15 修复：用显式事务包裹批量插入，防止部分写入
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for t in transfers:
                    log_id = t.get("log_id") or uuid.uuid4().hex
                    conn.execute("""
                        INSERT INTO stock_transfer_log
                        (log_id, source_node_id, target_node_id, stock_code, transfer_mode, trigger_condition, kline_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (log_id, t.get("source_node_id", ""), t.get("target_node_id", ""),
                          t.get("stock_code", ""), t.get("transfer_mode", "copy"),
                          t.get("trigger_condition"), t.get("kline_time")))
                conn.commit()
            except Exception:
                conn.rollback()
                raise



    # ------------------------------------------------------------------
    # Backward-compatible API: replay_session / replay_snapshot
    # ------------------------------------------------------------------
    def create_replay_session(self, pool_id: str, base_period: str = 'day',
                               start_date: str = None, end_date: str = None) -> str:
        session_id = uuid.uuid4().hex
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO replay_session
                (session_id, pool_id, base_period, start_date, end_date, status)
                VALUES (?, ?, ?, ?, ?, 'loading')
            """, (session_id, pool_id, base_period, start_date or '2000-01-01', end_date or '2099-12-31'))
            conn.commit()
        return session_id

    def update_replay_session(self, session_id: str, **kwargs) -> None:
        fields = []
        values = []
        allowed = ['status', 'current_time', 'kline_index', 'play_speed', 'start_date', 'end_date']
        for k, v in kwargs.items():
            if k in allowed:
                # Map legacy column names
                if k == 'current_bar_index':
                    k = 'kline_index'
                fields.append(f"{k}=?")
                values.append(v)
        if not fields:
            return
        values.append(session_id)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE replay_session SET {', '.join(fields)}, updated_at=datetime('now','localtime') WHERE session_id=?",
                tuple(values)
            )
            conn.commit()

    def get_replay_session(self, session_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM replay_session WHERE session_id=?", (session_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            # Map new column names back to legacy names for compatibility
            if 'kline_index' in result and 'current_bar_index' not in result:
                result['current_bar_index'] = result['kline_index']
            if 'session_id' in result and 'id' not in result:
                result['id'] = result['session_id']
            return result

    def list_replay_sessions(self, pool_id: str = None) -> List[Dict]:
        with self._conn() as conn:
            if pool_id:
                rows = conn.execute("SELECT * FROM replay_session WHERE pool_id=? ORDER BY created_at DESC", (pool_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM replay_session ORDER BY created_at DESC").fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if 'kline_index' in d and 'current_bar_index' not in d:
                    d['current_bar_index'] = d['kline_index']
                if 'session_id' in d and 'id' not in d:
                    d['id'] = d['session_id']
                results.append(d)
            return results

    def save_replay_snapshot(self, session_id: str, bar_index: int, node_states: Dict,
                             recent_events: List = None, kline_data: Dict = None) -> str:
        snapshot_id = uuid.uuid4().hex
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO replay_snapshot
                (snapshot_id, session_id, ts, node_states, recent_events, kline_data)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (snapshot_id, session_id, now,
                  json.dumps(node_states, ensure_ascii=False),
                  json.dumps(recent_events or [], ensure_ascii=False),
                  json.dumps(kline_data or {}, ensure_ascii=False)))
            conn.commit()
        return snapshot_id

    def get_replay_snapshots(self, session_id: str, limit: int = 100) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM replay_snapshot
                WHERE session_id=? ORDER BY ts DESC LIMIT ?
            """, (session_id, limit)).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if 'snapshot_id' in d and 'id' not in d:
                    d['id'] = d['snapshot_id']
                results.append(d)
            return results

    def get_latest_snapshot(self, session_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM replay_snapshot
                WHERE session_id=? ORDER BY ts DESC LIMIT 1
            """, (session_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            if 'snapshot_id' in d and 'id' not in d:
                d['id'] = d['snapshot_id']
            return d

    def delete_replay_session(self, session_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM replay_snapshot WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM replay_session WHERE session_id=?", (session_id,))
            conn.commit()

    # ------------------------------------------------------------------
    # New unified schema API: pool_node / pool_edge
    # ------------------------------------------------------------------
    def save_pool_node(self, node_id: str, pool_id: str, node_type: str,
                       label: str = None, pos_x: float = 0, pos_y: float = 0,
                       width: float = 120, height: float = 60,
                       params: dict = None) -> str:
        params_json = json.dumps(params or {}, ensure_ascii=False)
        with self._conn() as conn:
            old_row = conn.execute("SELECT * FROM pool_node WHERE node_id=?", (node_id,)).fetchone()
            old_content = json.dumps(dict(old_row), ensure_ascii=False, default=str) if old_row else None

            conn.execute("""
                INSERT OR REPLACE INTO pool_node (node_id, pool_id, node_type, label, pos_x, pos_y, width, height, params)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (node_id, pool_id, node_type, label, pos_x, pos_y, width, height, params_json))

            new_row = conn.execute("SELECT * FROM pool_node WHERE node_id=?", (node_id,)).fetchone()
            new_content = json.dumps(dict(new_row), ensure_ascii=False, default=str) if new_row else None

            change_type = 'update' if old_row else 'create'
            self.record_config_version('pool_node', change_type, old_content, new_content, conn=conn)
            conn.commit()
        return node_id

    def get_pool_nodes(self, pool_id: str, node_type: str = None) -> List[Dict]:
        with self._conn() as conn:
            if node_type:
                rows = conn.execute(
                    "SELECT * FROM pool_node WHERE pool_id=? AND node_type=?",
                    (pool_id, node_type)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pool_node WHERE pool_id=?",
                    (pool_id,)
                ).fetchall()
            return [dict(r) for r in rows]

    def delete_pool_node(self, node_id: str) -> bool:
        with self._conn() as conn:
            old_row = conn.execute("SELECT * FROM pool_node WHERE node_id=?", (node_id,)).fetchone()
            if not old_row:
                return False
            old_content = json.dumps(dict(old_row), ensure_ascii=False, default=str)
            conn.execute("DELETE FROM pool_node WHERE node_id=?", (node_id,))
            self.record_config_version('pool_node', 'delete', old_content, conn=conn)
            conn.commit()
        return True

    def save_pool_edge(self, edge_id: str, pool_id: str, source_node_id: str,
                       target_node_id: str, params: dict = None) -> str:
        params_json = json.dumps(params or {}, ensure_ascii=False)
        with self._conn() as conn:
            old_row = conn.execute("SELECT * FROM pool_edge WHERE edge_id=?", (edge_id,)).fetchone()
            old_content = json.dumps(dict(old_row), ensure_ascii=False, default=str) if old_row else None

            conn.execute("""
                INSERT OR REPLACE INTO pool_edge (edge_id, pool_id, source_node_id, target_node_id, params)
                VALUES (?, ?, ?, ?, ?)
            """, (edge_id, pool_id, source_node_id, target_node_id, params_json))

            new_row = conn.execute("SELECT * FROM pool_edge WHERE edge_id=?", (edge_id,)).fetchone()
            new_content = json.dumps(dict(new_row), ensure_ascii=False, default=str) if new_row else None

            change_type = 'update' if old_row else 'create'
            self.record_config_version('pool_edge', change_type, old_content, new_content, conn=conn)
            conn.commit()
        return edge_id

    def get_pool_edges(self, pool_id: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pool_edge WHERE pool_id=?",
                (pool_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_pool_edge(self, edge_id: str) -> bool:
        with self._conn() as conn:
            old_row = conn.execute("SELECT * FROM pool_edge WHERE edge_id=?", (edge_id,)).fetchone()
            if not old_row:
                return False
            old_content = json.dumps(dict(old_row), ensure_ascii=False, default=str)
            conn.execute("DELETE FROM pool_edge WHERE edge_id=?", (edge_id,))
            self.record_config_version('pool_edge', 'delete', old_content, conn=conn)
            conn.commit()
        return True

    # ------------------------------------------------------------------
    # New unified schema API: kline_cache
    # ------------------------------------------------------------------
    def save_kline(self, stock_code: str, period: str, kline_time: str,
                   open: float, high: float, low: float, close: float,
                   volume: float, amount: float = 0) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO kline_cache
                (stock_code, period, kline_time, open, high, low, close, volume, amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (stock_code, period, kline_time, open, high, low, close, volume, amount))
            conn.commit()

    def get_klines(self, stock_code: str, period: str, limit: int = 100) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM kline_cache
                WHERE stock_code=? AND period=?
                ORDER BY kline_time DESC LIMIT ?
            """, (stock_code, period, limit)).fetchall()
            return [dict(r) for r in rows]

    def clean_expired_kline_cache(self, retention_days: int = 30) -> int:
        """清理超过指定天数的 kline_cache 缓存记录

        Args:
            retention_days: 缓存保留天数，默认 30 天

        Returns:
            删除的缓存记录数
        """
        with self._conn() as conn:
            cursor = conn.execute("""
                DELETE FROM kline_cache
                WHERE cached_at < datetime('now', 'localtime', ?)
            """, (f'-{retention_days} days',))
            conn.commit()
            deleted = cursor.rowcount
            if deleted > 0:
                # 清理后执行 VACUUM 回收磁盘空间
                conn.execute("PRAGMA auto_vacuum = FULL")
            return deleted

    def prefetch_pool_klines(self, pool_config_id: str, cell_id: str,
                             period: str = 'day', days: int = 120) -> int:
        """查询状态池内所有股票在 kline_cache 中的缓存命中情况

        本方法仅从本地 kline_cache 表读取已缓存数据，不触发外部数据源下载。
        数据预缓存由数据源层负责。

        Args:
            pool_config_id: 股票池ID
            cell_id: 状态池节点ID
            period: K线周期
            days: 预取天数（仅保留用于语义兼容，不影响查询行为）

        Returns:
            命中缓存的股票数量
        """
        # 1. 获取该状态池内所有股票代码
        stocks = self.get_pool_stocks(pool_config_id, cell_id)
        if not stocks:
            return 0

        # 2. 仅检查 kline_cache 表中是否已存在缓存记录，不触发任何外部下载
        count = 0
        for stock in stocks:
            code = stock.get('label', '') or stock.get('t', '')
            if not code:
                continue
            existing = self.get_klines(code, period, limit=1)
            if existing:
                count += 1
        return count

    # ------------------------------------------------------------------
    # Candidate pool API: stocks / sectors / sector_members / user_blocks
    # ------------------------------------------------------------------
    def upsert_stocks(self, stocks_data: List[Dict]) -> int:
        """批量插入或更新股票信息

        Args:
            stocks_data: 股票列表，每个元素为字典：
                {
                    'stock_code': 'SH600000',  # 主键
                    'raw_code': '600000',
                    'name': '浦发银行',
                    'market': 'SH',  # SH/SZ/BJ
                    'list_date': '1999-11-10',
                    'delist_date': None,
                    'status': 'active',  # active/delisted/suspended
                    'industry_sw': '银行',
                    'industry_csrc': '金融业',
                    'market_cap': 1234567890.0,
                    'float_cap': 987654321.0
                }

        Returns:
            int: 成功插入/更新的记录数
        """
        if not stocks_data:
            return 0
        count = 0
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for s in stocks_data:
                    conn.execute("""
                        INSERT OR REPLACE INTO stocks (
                            stock_code, raw_code, name, market,
                            list_date, delist_date, status,
                            industry_sw, industry_csrc, market_cap, float_cap,
                            updated_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            datetime('now','localtime'),
                            COALESCE((SELECT created_at FROM stocks WHERE stock_code=?), datetime('now','localtime'))
                        )
                    """, (
                        s.get('stock_code'), s.get('raw_code'), s.get('name'),
                        s.get('market'), s.get('list_date'), s.get('delist_date'),
                        s.get('status', 'active'),
                        s.get('industry_sw'), s.get('industry_csrc'),
                        s.get('market_cap'), s.get('float_cap'),
                        s.get('stock_code')
                    ))
                    count += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return count

    def upsert_sectors(self, sectors_data: List[Dict]) -> int:
        """批量插入或更新板块信息

        Args:
            sectors_data: 板块列表，每个元素为字典：
                {
                    'sector_id': 'tdx_880001',  # 主键
                    'sector_code': '880001',
                    'sector_name': '银行板块',
                    'category': 'industry',  # industry/concept/region/index/custom
                    'sub_category': '通达信行业',
                    'source': 'tdx',  # tdx/ths/em/akshare/manual
                    'description': '银行业相关股票',
                    'member_count': 45,
                    'parent_id': None
                }

        Returns:
            int: 成功插入/更新的记录数
        """
        if not sectors_data:
            return 0
        count = 0
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for sec in sectors_data:
                    conn.execute("""
                        INSERT OR REPLACE INTO sectors (
                            sector_id, sector_code, sector_name, category,
                            sub_category, source, description,
                            member_count, parent_id,
                            updated_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                            datetime('now','localtime'),
                            COALESCE((SELECT created_at FROM sectors WHERE sector_id=?), datetime('now','localtime'))
                        )
                    """, (
                        sec.get('sector_id'), sec.get('sector_code'),
                        sec.get('sector_name'), sec.get('category'),
                        sec.get('sub_category'), sec.get('source', 'akshare'),
                        sec.get('description'), sec.get('member_count', 0),
                        sec.get('parent_id'),
                        sec.get('sector_id')
                    ))
                    count += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return count

    def upsert_sector_members(self, sector_id: str, members: List[Dict],
                               is_current: int = 1) -> int:
        """原子更新板块成分股（带事务保护）

        Args:
            sector_id: 板块ID
            members: 成分股列表，每个元素为字典：
                {
                    'stock_code': 'SH600000',
                    'in_date': '2024-01-01',
                    'weight': 1.0,
                    'out_date': None  # 仅 is_current=0 时使用
                }
            is_current: 是否为当前成分股 (1=是, 0=历史)

        Returns:
            int: 成功插入/更新的成员数
        """
        if not members:
            return 0
        count = 0
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if is_current == 1:
                    # 先删除该板块的所有旧成分股记录（is_current=1）
                    conn.execute(
                        "DELETE FROM sector_members WHERE sector_id=? AND is_current=1",
                        (sector_id,)
                    )
                for m in members:
                    conn.execute("""
                        INSERT OR REPLACE INTO sector_members (
                            sector_id, stock_code, in_date, out_date,
                            weight, is_current, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))
                    """, (
                        sector_id, m.get('stock_code'),
                        m.get('in_date'), m.get('out_date'),
                        m.get('weight', 1.0), is_current
                    ))
                    count += 1
                # 同步更新 sectors 表的 member_count
                cnt_row = conn.execute(
                    "SELECT COUNT(*) FROM sector_members WHERE sector_id=? AND is_current=1",
                    (sector_id,)
                ).fetchone()
                if cnt_row:
                    conn.execute(
                        "UPDATE sectors SET member_count=?, updated_at=datetime('now','localtime') WHERE sector_id=?",
                        (cnt_row[0], sector_id)
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return count

    def cache_local_block_members(self, sector_id: str, members: List[Dict],
                                   source: str = 'tdx') -> int:
        """缓存本地文件解析的板块成分股到 sector_members 表。

        Args:
            sector_id: 板块ID（建议格式：local_{client}_{block_code}，如 'local_tdx_TEST'）
            members: 成分股列表，每个元素为字典：
                {'stock_code': 'SH600000', 'name': '浦发银行', ...}
            source: 数据来源标识（tdx/dzh/ths）

        Returns:
            int: 成功缓存的成员数
        """
        if not members:
            return 0
        count = 0
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 先删除该板块的旧缓存记录（is_current=1）
                conn.execute(
                    "DELETE FROM sector_members WHERE sector_id=? AND is_current=1",
                    (sector_id,)
                )
                # 插入新的缓存记录，in_date 为当前日期
                for m in members:
                    conn.execute("""
                        INSERT OR REPLACE INTO sector_members (
                            sector_id, stock_code, in_date, out_date,
                            weight, is_current, updated_at
                        ) VALUES (?, ?, date('now','localtime'), NULL, 1.0, 1, datetime('now','localtime'))
                    """, (
                        sector_id, m.get('stock_code'),
                    ))
                    count += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return count

    def get_local_block_cache_time(self, sector_id: str) -> Optional[str]:
        """获取本地板块缓存的最后更新时间。

        Args:
            sector_id: 板块ID

        Returns:
            最后更新时间字符串（ISO 格式），若缓存不存在返回 None
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT updated_at FROM sector_members WHERE sector_id=? AND is_current=1 ORDER BY updated_at DESC LIMIT 1",
                (sector_id,)
            ).fetchone()
        return row[0] if row else None

    def invalidate_local_block_cache(self, sector_id: str) -> int:
        """使本地板块缓存失效（将 is_current 标记为 0）。

        Args:
            sector_id: 板块ID

        Returns:
            int: 受影响的记录数
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 将该板块的当前缓存记录标记为历史
                cur = conn.execute(
                    "UPDATE sector_members SET is_current=0 WHERE sector_id=? AND is_current=1",
                    (sector_id,)
                )
                affected = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return affected

    def create_resolved_block(self, block_code: str, block_name: str,
                              source_sector_id: str, members: List[Dict],
                              description: str = None) -> bool:
        """创建 resolved 类型的用户自定义板块（从分类树解析而来）

        Args:
            block_code: 板块代码（如 'CSBK_AI'）
            block_name: 板块名称（如 '人工智能概念'）
            source_sector_id: 源板块ID（如 'ths_concept_人工智能'）
            members: 成员股票列表 [{'stock_code': 'SH600000', ...}, ...]
            description: 描述信息

        Returns:
            bool: 是否创建成功
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 在 user_blocks 表中插入记录
                conn.execute("""
                    INSERT OR REPLACE INTO user_blocks (
                        block_code, block_name, block_type,
                        description, source, source_sector_id,
                        resolved_from, updated_at, created_at
                    ) VALUES (?, ?, 'resolved', ?, 'auto', ?, ?,
                        datetime('now','localtime'), datetime('now','localtime'))
                """, (block_code, block_name, description, source_sector_id, source_sector_id))
                # 写入成员到 user_block_members 表
                for idx, m in enumerate(members):
                    conn.execute("""
                        INSERT OR REPLACE INTO user_block_members
                        (block_code, stock_code, add_time, sort_order)
                        VALUES (?, ?, ?, ?)
                    """, (block_code, m.get('stock_code'), now, idx))
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def get_user_block(self, block_code: str) -> Optional[Dict]:
        """获取自定义板块及其成员

        Args:
            block_code: 板块代码

        Returns:
            包含 block 信息和 members 列表的字典，若不存在返回 None
            {
                'block_code': 'CSBK_TEST',
                'block_name': '测试板块',
                ...其他字段...,
                'members': [
                    {'stock_code': 'SH600000', 'name': '浦发银行', ...},
                    ...
                ]
            }
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_blocks WHERE block_code=?", (block_code,)
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            # 关联查询 stocks 表获取股票名称
            member_rows = conn.execute("""
                SELECT ubm.*, s.name, s.raw_code, s.market
                FROM user_block_members ubm
                LEFT JOIN stocks s ON ubm.stock_code = s.stock_code
                WHERE ubm.block_code=?
                ORDER BY ubm.sort_order
            """, (block_code,)).fetchall()
            result['members'] = [dict(r) for r in member_rows]
            return result

    def upsert_user_block(self, block_code: str, block_name: str,
                          block_type: str = 'custom', description: str = None,
                          source: str = 'manual',
                          source_sector_id: str = None) -> bool:
        """插入或更新用户自定义板块（保留 auto_refresh/refresh_interval/resolved_from/created_at）。

        Args:
            block_code: 板块代码（主键）
            block_name: 板块名称
            block_type: 板块类型（custom/favorite/resolved）
            description: 描述信息
            source: 数据来源（manual/auto/local_file/...）
            source_sector_id: 源板块ID

        Returns:
            bool: 是否操作成功
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("""
                    INSERT INTO user_blocks (
                        block_code, block_name, block_type, description,
                        source, source_sector_id, updated_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?,
                        datetime('now','localtime'),
                        datetime('now','localtime'))
                    ON CONFLICT(block_code) DO UPDATE SET
                        block_name=excluded.block_name,
                        block_type=excluded.block_type,
                        description=COALESCE(excluded.description, user_blocks.description),
                        source=excluded.source,
                        source_sector_id=COALESCE(excluded.source_sector_id, user_blocks.source_sector_id),
                        updated_at=datetime('now','localtime')
                """, (block_code, block_name, block_type, description,
                      source, source_sector_id))
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def update_user_block_members(self, block_code: str,
                                   members: List[Dict],
                                   clear_existing: bool = True) -> int:
        """更新自定义板块的成员列表

        Args:
            block_code: 板块代码
            members: 新的成员列表 [{'stock_code': 'SH600000'}, ...]
            clear_existing: 是否先清除已有成员

        Returns:
            int: 成功更新的成员数
        """
        if not members and clear_existing:
            with self._conn() as conn:
                cursor = conn.execute(
                    "DELETE FROM user_block_members WHERE block_code=?",
                    (block_code,)
                )
                conn.commit()
                return cursor.rowcount
        if not members:
            return 0
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        count = 0
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if clear_existing:
                    conn.execute(
                        "DELETE FROM user_block_members WHERE block_code=?",
                        (block_code,)
                    )
                for idx, m in enumerate(members):
                    conn.execute("""
                        INSERT OR REPLACE INTO user_block_members
                        (block_code, stock_code, add_time, sort_order)
                        VALUES (?, ?, ?, ?)
                    """, (block_code, m.get('stock_code'), now, idx))
                    count += 1
                conn.execute(
                    "UPDATE user_blocks SET updated_at=datetime('now','localtime') WHERE block_code=?",
                    (block_code,)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return count

    def get_sectors_catalog(self, category: str = None,
                             source: str = None,
                             keyword: str = None,
                             parent_id: str = None) -> List[Dict]:
        """查询板块目录（支持多条件过滤）

        Args:
            category: 分类过滤（industry/concept/region/index等）
            source: 数据源过滤（tdx/ths/em/akshare/manual）
            keyword: 关键词搜索（匹配 sector_name）
            parent_id: 父级板块ID（用于构建树形结构）

        Returns:
            板块列表，按 category, sector_name 排序
        """
        conditions = []
        params = []
        if category is not None:
            conditions.append("s.category=?")
            params.append(category)
        if source is not None:
            conditions.append("s.source=?")
            params.append(source)
        if keyword is not None:
            conditions.append("s.sector_name LIKE ?")
            params.append(f"%{keyword}%")
        if parent_id is not None:
            conditions.append("s.parent_id=?")
            params.append(parent_id)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT s.*,
                       (SELECT COUNT(*) FROM sector_members sm
                        WHERE sm.sector_id=s.sector_id AND sm.is_current=1) AS actual_member_count
                FROM sectors s
                {where_clause}
                ORDER BY s.category, s.sector_name
            """, tuple(params)).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d['member_count'] = d.pop('actual_member_count', d.get('member_count', 0))
                results.append(d)
            return results

    def get_sector_members(self, sector_id: str, limit: int = None) -> List[Dict]:
        """查询板块的当前成分股列表（含个股名称）

        通过 JOIN sector_members 与 stocks 表返回带名称的成分股，
        用于备选池属性面板展开板块时展示。

        Args:
            sector_id: 板块ID
            limit: 可选，限制返回数量（用于预览）；为 None 时不限制

        Returns:
            成分股列表，每项为字典：
            [{'stock_code': 'SH600000', 'name': '浦发银行',
              'market': 'SH', 'in_date': '2024-01-01'}, ...]
            板块不存在或无成分股时返回空列表 []，不抛异常
        """
        sql = """
            SELECT sm.stock_code,
                   COALESCE(s.name, '') AS name,
                   COALESCE(s.market, '') AS market,
                   sm.in_date
            FROM sector_members sm
            LEFT JOIN stocks s ON sm.stock_code = s.stock_code
            WHERE sm.sector_id=? AND sm.is_current=1
            ORDER BY sm.stock_code
        """
        params = [sector_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 数据源同步日志 API: data_source_sync_log
    # ------------------------------------------------------------------
    def record_sync_log(self, source: str, entity_type: str, status: str,
                        record_count: int = 0, error_message: str = None) -> int:
        """记录一条数据源同步日志。

        Args:
            source: 数据源名称（local_file/dfcf/akshare/tq_dll）
            entity_type: 实体类型（stocks/sectors/sector_members/favorites/custom_blocks）
            status: 同步状态（success/failed/partial）
            record_count: 同步的记录数
            error_message: 错误信息（失败时填写）

        Returns:
            int: 新插入日志记录的 id
        """
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO data_source_sync_log
                    (source, entity_type, status, record_count, error_message, synced_at)
                VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
            """, (source, entity_type, status, record_count, error_message))
            conn.commit()
            return cur.lastrowid

    def get_sync_status(self, source: str = None) -> List[Dict]:
        """查询各数据源的最后同步状态。

        按 (source, entity_type) 分组，返回每个分组的最新一条日志记录。

        Args:
            source: 可选，指定数据源名称过滤；为 None 时返回全部数据源

        Returns:
            同步状态列表，每个元素为字典：
            {
                'source': 'dfcf',
                'entity_type': 'stocks',
                'status': 'success',
                'record_count': 5432,
                'error_message': None,
                'synced_at': '2026-06-24 10:00:00'
            }
            按 synced_at 倒序排列。
        """
        params = []
        where_clause = ""
        if source is not None:
            where_clause = "WHERE source=?"
            params.append(source)

        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT l.source, l.entity_type, l.status, l.record_count,
                       l.error_message, l.synced_at
                FROM data_source_sync_log l
                INNER JOIN (
                    SELECT source, entity_type, MAX(id) AS max_id
                    FROM data_source_sync_log
                    {where_clause}
                    GROUP BY source, entity_type
                ) latest
                ON l.id = latest.max_id
                ORDER BY l.synced_at DESC
            """, tuple(params)).fetchall()
            return [dict(r) for r in rows]
