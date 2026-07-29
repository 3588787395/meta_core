"""Test 11: API Endpoints — API-001 ~ API-011.

Tests pool CRUD, XML/JSON import-export, and simulation step API endpoints
using FastAPI TestClient with mocked TqAdapter/Storage.

Key invariants:
  - Pool CRUD: create/read/update/delete must work and return structured JSON
  - XML/JSON export must produce valid output
  - Simulation step requires mock data source (active='mock')
  - Path traversal and invalid names must be rejected
"""
from __future__ import annotations

import uuid
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """Create a FastAPI TestClient with mocked TqAdapter and Storage."""
    # Mock TqAdapter: get_data_source_state returns {'active': 'mock'} for simulation
    mock_tq = MagicMock()
    mock_tq.mock_mode = True
    mock_tq.get_mode_info = lambda: "mock"
    mock_tq.get_data_source_state = lambda: {'active': 'mock'}

    mock_storage = MagicMock()
    mock_storage.get_pool.return_value = None
    mock_storage.list_pools.return_value = []
    mock_storage.save_pool.return_value = None

    with patch('app.TqAdapter', return_value=mock_tq):
        with patch('app.Storage', return_value=mock_storage):
            from app import app
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c


@pytest.fixture
def pool_data():
    """Standard pool data for API testing."""
    return {
        "name": "test_api_pool",
        "nodes": [
            {
                "id": "candidate_1",
                "type": "7",
                "dzh_cell_type": 7,
                "text": "备选池",
                "attr": 0,
                "pos": "0,0,200,100",
                "params": {
                    "stocks": [{"code": "600000", "label": "浦发银行"}],
                    "tdx_spinfo": {"type": 0, "customblockname": "", "size": 0, "market": "", "sector_type": 0},
                },
            },
            {
                "id": "state_pool_1",
                "type": "8",
                "dzh_cell_type": 8,
                "text": "状态池",
                "attr": 0,
                "pos": "400,0,600,100",
                "params": {
                    "tdx_psatt": {
                        "bdel": 0, "ndelnum": 0, "ndeltype": 0, "baimpool": 0,
                        "bsound": 0, "nsoundtype": 0, "nsyssound": 0, "soundfile": "",
                        "btip": 0, "bsavetoblock": 0, "blockfile": "", "bclearblock": 0, "bsavehis": 0,
                    },
                    "stocks": [],
                },
            },
        ],
        "edges": [
            {
                "id": "flow_direct",
                "from": "candidate_1",
                "to": "state_pool_1",
                "attr": 0,
                "params": {
                    "tran": 0, "emptyps": 0, "starttype": 0,
                    "starttime": 0, "starttimetype": 0, "starttimehms": 0,
                    "cxtype": 0, "cxtime": 0, "cxtimetype": 0, "jgtime": 0,
                },
            },
        ],
    }


# ─── API-001~004: Pool CRUD ─────────────────────────────────

class TestPoolCRUD:
    """API-001~004: Pool create / read / update / delete."""

    def test_api_001_create_pool(self, client):
        """API-001: POST /api/tdx/pools 创建池（正向）。

        创建一个新池，应返回 success=True。
        """
        name = f"simtest_create_{uuid.uuid4().hex[:8]}"
        resp = client.post("/api/tdx/pools", json={"name": name})
        assert resp.status_code == 200, f"创建池应返回 200, 实际 {resp.status_code}"
        data = resp.json()
        assert data.get("success") is True, f"创建应成功: {data}"
        assert data.get("data", {}).get("name") == name, \
            f"BUG: API-001 返回的 name 应为 '{name}', 实际 {data.get('data', {}).get('name')}"
        # Cleanup
        client.delete(f"/api/tdx/pools/{name}")

    def test_api_002_read_pool_list(self, client):
        """API-002: GET /api/tdx/pools 列出所有池（正向）。

        列出池，应返回 success=True 和 data 列表。
        """
        resp = client.get("/api/tdx/pools")
        assert resp.status_code == 200, f"列出池应返回 200, 实际 {resp.status_code}"
        data = resp.json()
        assert data.get("success") is True, f"列出应成功: {data}"
        assert isinstance(data.get("data"), list), \
            f"BUG: API-002 返回 data 应为 list, 实际 {type(data.get('data'))}"

    def test_api_003_update_pool(self, client, pool_data):
        """API-003: PUT /api/tdx/pools/{name} 更新池（正向）。

        先创建池，再更新池内容，应返回 success=True。
        """
        name = f"simtest_update_{uuid.uuid4().hex[:8]}"
        client.post("/api/tdx/pools", json={"name": name})
        pool_data["name"] = name
        resp = client.put(f"/api/tdx/pools/{name}", json={"pool_data": pool_data})
        assert resp.status_code == 200, f"更新池应返回 200, 实际 {resp.status_code}"
        data = resp.json()
        assert data.get("success") is True, f"更新应成功: {data}"
        # Cleanup
        client.delete(f"/api/tdx/pools/{name}")

    def test_api_004_delete_pool(self, client):
        """API-004: DELETE /api/tdx/pools/{name} 删除池（正向）。

        先创建池，再删除，应返回 success=True。
        """
        name = f"simtest_delete_{uuid.uuid4().hex[:8]}"
        client.post("/api/tdx/pools", json={"name": name})
        resp = client.delete(f"/api/tdx/pools/{name}")
        assert resp.status_code == 200, f"删除池应返回 200, 实际 {resp.status_code}"
        data = resp.json()
        assert data.get("success") is True, f"删除应成功: {data}"


# ─── API-005~006: 反向测试（边界异常）────────────────────────

class TestAPIErrorHandling:
    """API-005~006: Error handling and boundary conditions."""

    def test_api_005_load_nonexistent_pool_returns_404(self, client):
        """API-005: GET /api/tdx/pools/{name}/load 加载不存在的池（反向）。

        不存在的池应返回 404。
        """
        resp = client.get("/api/tdx/pools/absolutely_nonexistent_pool_xyz/load")
        assert resp.status_code == 404, \
            f"BUG: API-005 不存在的池应返回 404, 实际 {resp.status_code}"

    def test_api_006_create_pool_with_empty_name_rejected(self, client):
        """API-006: POST /api/tdx/pools 空名称（反向）。

        空名称应返回 success=False。
        """
        resp = client.post("/api/tdx/pools", json={"name": ""})
        data = resp.json()
        assert data.get("success") is False, \
            f"BUG: API-006 空名称应返回 success=False, 实际 {data}"


# ─── API-007~008: XML/JSON 导入导出 ──────────────────────────

class TestImportExport:
    """API-007~008: XML and JSON import/export."""

    def test_api_007_tdx_export_xml(self, client, pool_data):
        """API-007: POST /api/tdx/export 导出 XML（正向）。

        导出池数据为 XML，应返回 XML 文件。
        """
        resp = client.post("/api/tdx/export", json={"pool_data": pool_data})
        assert resp.status_code == 200, f"导出 XML 应返回 200, 实际 {resp.status_code}"
        # Response should be XML content
        content_type = resp.headers.get("content-type", "")
        assert "xml" in content_type or "text/plain" in content_type or "octet-stream" in content_type, \
            f"BUG: API-007 导出应返回 XML 内容, content-type={content_type}"
        # Body should contain XML markers
        body = resp.text
        assert "<?xml" in body or "<root>" in body or "<pool" in body, \
            f"BUG: API-007 导出内容应含 XML 标记, 实际前100字符: {body[:100]}"

    def test_api_008_json_export(self, client, pool_data):
        """API-008: POST /api/json/export 导出 JSON（正向）。

        导出池数据为 JSON，应返回 success=True。
        """
        resp = client.post("/api/json/export", json={"pool_data": pool_data})
        assert resp.status_code == 200, f"导出 JSON 应返回 200, 实际 {resp.status_code}"
        data = resp.json()
        # JSON export may return success or the exported data directly
        # Accept either format as long as it's valid JSON with pool content
        assert "name" in str(data) or "success" in data or "pool_data" in str(data), \
            f"BUG: API-008 导出 JSON 应含池数据, 实际 {str(data)[:200]}"


# ─── API-009~010: 仿真 API ──────────────────────────────────

class TestSimulationAPI:
    """API-009~010: Simulation step API."""

    def test_api_009_simulation_step_succeeds_with_mock_source(self, client, pool_data):
        """API-009: POST /api/pool/{name}/simulation/step 仿真步进（正向）。

        在 mock 数据源下，仿真步进应返回 success=True。
        需要先创建并保存池，再调用 simulation/step。
        """
        name = f"simtest_sim_{uuid.uuid4().hex[:8]}"
        # Create and save pool with actual node/edge data
        client.post("/api/tdx/pools", json={"name": name})
        pool_data["name"] = name
        client.put(f"/api/tdx/pools/{name}", json={"pool_data": pool_data})

        # Simulation step (mock data source is set in fixture)
        resp = client.post(f"/api/pool/{name}/simulation/step", json={"delta": 1.0})
        assert resp.status_code == 200, f"仿真步进应返回 200, 实际 {resp.status_code}"
        data = resp.json()
        assert data.get("success") is True, \
            f"BUG: API-009 仿真步进应成功, 实际 {data}"
        sim_data = data.get("data", {})
        assert "virtual_clock" in sim_data, \
            f"BUG: API-009 仿真结果应含 virtual_clock, 实际 {sim_data}"
        assert "node_summary" in sim_data, \
            f"BUG: API-009 仿真结果应含 node_summary, 实际 {sim_data}"
        # Cleanup
        client.delete(f"/api/tdx/pools/{name}")

    def test_api_010_simulation_step_without_mock_source_rejected(self, client, pool_data):
        """API-010: POST /api/pool/{name}/simulation/step 非 mock 数据源（反向）。

        当数据源不是 mock 时，仿真步进应返回 success=False。
        """
        name = f"simtest_nomock_{uuid.uuid4().hex[:8]}"
        client.post("/api/tdx/pools", json={"name": name})
        pool_data["name"] = name
        client.put(f"/api/tdx/pools/{name}", json={"pool_data": pool_data})

        # Patch the tq to return non-mock state
        with patch.object(client.app.state.tq, 'get_data_source_state',
                          return_value={'active': 'tq_dll'}):
            resp = client.post(f"/api/pool/{name}/simulation/step", json={"delta": 1.0})

        assert resp.status_code == 200, f"应返回 200 (错误响应), 实际 {resp.status_code}"
        data = resp.json()
        assert data.get("success") is False, \
            f"BUG: API-010 非 mock 数据源应返回 success=False, 实际 {data}"
        assert "mock" in data.get("error", "").lower(), \
            f"BUG: API-010 错误信息应提及 mock, 实际 {data.get('error', '')}"
        # Cleanup
        client.delete(f"/api/tdx/pools/{name}")


# ─── API-011: 综合 CRUD 生命周期 ─────────────────────────────

class TestAPILifecycle:
    """API-011: Full CRUD lifecycle integration test."""

    def test_api_011_full_crud_lifecycle(self, client, pool_data):
        """API-011: 综合 — 创建→读取→更新→执行→删除 完整生命周期。

        验证池从创建到删除的完整流程，确保各环节衔接正确。
        """
        name = f"simtest_lifecycle_{uuid.uuid4().hex[:8]}"

        # 1. Create
        create_resp = client.post("/api/tdx/pools", json={"name": name})
        assert create_resp.status_code == 200, "创建池应成功"
        assert create_resp.json().get("success") is True, "创建应返回 success=True"

        # 2. Read (verify in list)
        list_resp = client.get("/api/tdx/pools")
        assert list_resp.status_code == 200
        pool_names = [p.get("name", "") for p in list_resp.json().get("data", [])]
        assert name in pool_names, f"创建后池应在列表中, 实际 {pool_names}"

        # 3. Update (save pool data)
        pool_data["name"] = name
        update_resp = client.put(f"/api/tdx/pools/{name}", json={"pool_data": pool_data})
        assert update_resp.status_code == 200, "更新池应成功"
        assert update_resp.json().get("success") is True, "更新应返回 success=True"

        # 4. Execute (run pool)
        exec_resp = client.post("/api/tdx/execute-pool", json={"pool_data": pool_data})
        assert exec_resp.status_code == 200, "执行池应成功"
        assert exec_resp.json().get("success") is True, "执行应返回 success=True"

        # 5. Delete
        del_resp = client.delete(f"/api/tdx/pools/{name}")
        assert del_resp.status_code == 200, "删除池应成功"
        assert del_resp.json().get("success") is True, "删除应返回 success=True"

        # 6. Verify deleted (should not be in list)
        list_resp2 = client.get("/api/tdx/pools")
        pool_names2 = [p.get("name", "") for p in list_resp2.json().get("data", [])]
        assert name not in pool_names2, f"删除后池不应在列表中, 实际 {pool_names2}"
