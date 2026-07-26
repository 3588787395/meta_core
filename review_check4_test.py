#!/usr/bin/env python3
"""Check 4 复核测试脚本：使用 urllib 实测股票池相关 API（修正版）。"""
import json
import traceback
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:8000"
RESULTS = []


def record(name, ok, detail="", data=None):
    RESULTS.append({"name": name, "ok": ok, "detail": detail, "data": data})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: {detail}")


def _request(method, path, body=None, headers=None, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    req_headers = headers or {}
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        else:
            data = body
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, raw, dict(e.headers)


def text_request(method, path, body=None, params=None):
    status, raw, hdrs = _request(method, path, body=body, params=params)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    return status, text, hdrs


def json_request(method, path, body=None, params=None):
    status, raw, hdrs = _request(method, path, body=body, params=params)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    try:
        return status, json.loads(text), hdrs
    except Exception:
        return status, {"_raw": text[:500], "_parse_error": True}, hdrs


def upload_file(path, field_name, file_path, mime, extra_fields=None):
    import uuid
    boundary = uuid.uuid4().hex
    parts = []
    fname = Path(file_path).name
    with open(file_path, "rb") as f:
        content = f.read()
    if extra_fields:
        for k, v in extra_fields.items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field_name}\"; filename=\"{fname}\"\r\nContent-Type: {mime}\r\n\r\n")
    body = b"".join([p.encode("utf-8") for p in parts]) + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return _request("POST", path, body=body, headers=headers)


def main():
    import urllib.parse  # noqa

    sample_dir = Path("/workspace/docs/samples/pools")
    tdx_dir = Path("/workspace/tdxpool")
    dzh_xml = sample_dir / "超赢7号.xml"
    tdx_xml = tdx_dir / "黑马一号池.xml"
    json_file = sample_dir / "cys.json"

    # 1. DZH XML import-and-save
    try:
        status, raw, hdrs = upload_file("/api/dzh/import-and-save", "file", dzh_xml, "application/xml")
        text = raw.decode("utf-8", errors="replace")
        dzh_import_data = json.loads(text)
        ok = status == 200 and dzh_import_data.get("success")
        detail = f"status={status}, nodes={dzh_import_data.get('meta',{}).get('node_count')}, edges={dzh_import_data.get('meta',{}).get('edge_count')}"
        record("DZH XML import-and-save", ok, detail, dzh_import_data)
    except Exception as e:
        record("DZH XML import-and-save", False, f"异常: {e}")
        dzh_import_data = {}

    # 2. DZH export
    try:
        cfg = dzh_import_data.get("data") or {}
        if not cfg:
            record("DZH export", False, "无可用配置")
        else:
            status, raw, hdrs = _request("POST", "/api/dzh/export", body={"config": cfg})
            ctype = hdrs.get("Content-Type", "").lower()
            ok = status == 200 and ("xml" in ctype or "octet-stream" in ctype) and len(raw) > 0
            detail = f"status={status}, content-type={hdrs.get('Content-Type')}, bytes={len(raw)}"
            record("DZH export", ok, detail)
    except Exception as e:
        record("DZH export", False, f"异常: {e}")

    # 3. DZH export-meta
    try:
        if not cfg:
            record("DZH export-meta", False, "无可用配置")
        else:
            status, raw, hdrs = _request("POST", "/api/dzh/export-meta", body={"config": cfg})
            ctype = hdrs.get("Content-Type", "").lower()
            ok = status == 200 and len(raw) > 0
            detail = f"status={status}, content-type={hdrs.get('Content-Type')}, bytes={len(raw)}"
            record("DZH export-meta", ok, detail)
    except Exception as e:
        record("DZH export-meta", False, f"异常: {e}")

    # 4. TDX XML import (通过 /api/dzh/import 自动检测)
    try:
        status, raw, hdrs = upload_file("/api/dzh/import", "file", tdx_xml, "application/xml")
        text = raw.decode("utf-8", errors="replace")
        tdx_import_data = json.loads(text)
        ok = status == 200 and tdx_import_data.get("success")
        meta = tdx_import_data.get("meta") or {}
        detail = f"status={status}, nodes={meta.get('node_count')}, edges={meta.get('edge_count')}, keys={list(tdx_import_data.keys())}"
        record("TDX XML import (via /api/dzh/import)", ok, detail, tdx_import_data)
    except Exception as e:
        record("TDX XML import", False, f"异常: {e}")
        tdx_import_data = {}

    # 5. JSON import
    try:
        status, raw, hdrs = upload_file("/api/json/import", "file", json_file, "application/json")
        text = raw.decode("utf-8", errors="replace")
        json_import_data = json.loads(text)
        ok = status == 200 and json_import_data.get("success")
        data = json_import_data.get("data") or {}
        detail = f"status={status}, has_nodes={'nodes' in data}, has_data={'data' in data}, top_keys={list(json_import_data.keys())[:5]}"
        record("JSON import", ok, detail, json_import_data)
    except Exception as e:
        record("JSON import", False, f"异常: {e}")
        json_import_data = {}

    # 6. JSON export
    try:
        cfg2 = json_import_data.get("data") or {}
        if not cfg2:
            record("JSON export", False, "无可用配置")
        else:
            status, raw, hdrs = _request("POST", "/api/json/export", body={"config": cfg2})
            ctype = hdrs.get("Content-Type", "").lower()
            ok = status == 200 and ("json" in ctype or "octet-stream" in ctype) and len(raw) > 0
            detail = f"status={status}, content-type={hdrs.get('Content-Type')}, bytes={len(raw)}"
            record("JSON export", ok, detail)
    except Exception as e:
        record("JSON export", False, f"异常: {e}")

    # 7. indicator values
    try:
        status, ind_data, _ = json_request("GET", "/api/indicator/values", params={"node_id": "000001", "formula": "MA(C,5)", "period": "1d"})
        ok = status == 200 and ind_data.get("code") == 0 and isinstance(ind_data.get("data"), list)
        detail = f"status={status}, code={ind_data.get('code')}, count={len(ind_data.get('data', []))}"
        record("Indicator values (MA(C,5))", ok, detail, ind_data)
    except Exception as e:
        record("Indicator values", False, f"异常: {e}")

    # 8. kline
    try:
        status, kline_data, _ = json_request("GET", "/api/kline", params={"stock_code": "600000", "period": "1d", "limit": 50})
        ok = status == 200 and kline_data.get("code") == 0 and isinstance(kline_data.get("data"), list)
        detail = f"status={status}, code={kline_data.get('code')}, count={len(kline_data.get('data', []))}"
        record("K-line", ok, detail, kline_data)
    except Exception as e:
        record("K-line", False, f"异常: {e}")

    # 9. flows source/target
    try:
        status, flows_data, _ = json_request("GET", "/api/dzh/flows")
        flows = flows_data.get("flows", [])
        has_src_tgt = all("source" in f and "target" in f for f in flows)
        ok = status == 200 and isinstance(flows, list) and (len(flows) == 0 or has_src_tgt)
        detail = f"status={status}, count={len(flows)}, source/target_ok={has_src_tgt}"
        record("DZH flows source/target", ok, detail, flows_data)
    except Exception as e:
        record("DZH flows source/target", False, f"异常: {e}")

    # 10. timer queue
    try:
        status, tq_data, _ = json_request("GET", "/api/events/timer-queue")
        ok = status == 200 and tq_data.get("success") and isinstance(tq_data.get("timers"), list)
        detail = f"status={status}, count={tq_data.get('count')}, timers={len(tq_data.get('timers', []))}"
        record("Events timer-queue", ok, detail, tq_data)
    except Exception as e:
        record("Events timer-queue", False, f"异常: {e}")

    # 11. sim start / pause / resume / stop
    session_id = None
    try:
        cfg_for_sim = json_import_data.get("data") if json_import_data.get("success") else None
        if not cfg_for_sim:
            with open("/workspace/config/pools/sim_demo_pool.json", "r", encoding="utf-8") as f:
                cfg_for_sim = json.load(f)
        status, sim_data, _ = json_request("POST", "/api/sim/start", body={"config": cfg_for_sim, "speed": 1.0})
        ok = status == 200 and sim_data.get("code") == 0
        session_id = sim_data.get("data", {}).get("session_id")
        detail = f"status={status}, code={sim_data.get('code')}, session_id={session_id}"
        record("Sim start", ok, detail, sim_data)
    except Exception as e:
        record("Sim start", False, f"异常: {e}")

    if session_id:
        for action in ("pause", "resume"):
            try:
                status, data, _ = json_request("POST", "/api/sim/control", body={"session_id": session_id, "action": action})
                ok = status == 200 and data.get("code") == 0
                record(f"Sim {action}", ok, f"status={status}, msg={data.get('msg')}", data)
            except Exception as e:
                record(f"Sim {action}", False, f"异常: {e}")

        try:
            status, bars_data, _ = json_request("GET", "/api/sim/bars", params={"session_id": session_id, "code": "000001", "period": "1min"})
            ok = status == 200 and bars_data.get("code") == 0 and isinstance(bars_data.get("data", {}).get("bars"), list)
            detail = f"status={status}, bars={len(bars_data.get('data',{}).get('bars',[]))}"
            record("Sim bars", ok, detail, bars_data)
        except Exception as e:
            record("Sim bars", False, f"异常: {e}")

        try:
            status, stop_data, _ = json_request("POST", "/api/sim/control", body={"session_id": session_id, "action": "stop"})
            ok = status == 200 and stop_data.get("code") == 0
            record("Sim stop", ok, f"status={status}, msg={stop_data.get('msg')}", stop_data)
        except Exception as e:
            record("Sim stop", False, f"异常: {e}")

    # 12. CRUD 单元
    try:
        status, cell_data, _ = json_request("POST", "/api/dzh/cells", body={"cell_type": 200, "position": {"x": 10, "y": 20, "width": 100, "height": 80}, "params": {"label": "test_state_pool"}})
        ok = status == 200 and cell_data.get("success")
        cell_id = cell_data.get("data", {}).get("id")
        record("Cell create", ok, f"status={status}, id={cell_id}", cell_data)

        if cell_id:
            status, upd_data, _ = json_request("PUT", f"/api/dzh/cells/{cell_id}", body={"label": "updated_pool", "params": {"hold_sec": 3600}})
            ok2 = status == 200 and upd_data.get("success")
            record("Cell update", ok2, f"status={status}", upd_data)

            status, cell2_data, _ = json_request("POST", "/api/dzh/cells", body={"cell_type": 202, "position": {"x": 200, "y": 20, "width": 100, "height": 80}, "params": {"label": "test_candidate"}})
            cell2_id = cell2_data.get("data", {}).get("id")
            record("Cell create (candidate)", status == 200 and cell2_data.get("success"), f"id={cell2_id}", cell2_data)

            if cell2_id:
                status, flow_data, _ = json_request("POST", "/api/dzh/flows", body={"source": cell2_id, "target": cell_id, "params": {"conditional": True, "line_style": "solid", "desc": "test edge", "width": 2}})
                flow_id = flow_data.get("data", {}).get("id")
                ok3 = status == 200 and flow_data.get("success")
                record("Flow create", ok3, f"status={status}, id={flow_id}", flow_data)

                if flow_id:
                    status, _, _ = json_request("PUT", f"/api/dzh/flows/{flow_id}", body={"params": {"conditional": False, "line_style": "dashed", "desc": "updated edge", "width": 3}})
                    record("Flow update", status == 200, f"status={status}", _)

                    status, _, _ = json_request("DELETE", f"/api/dzh/flows/{flow_id}")
                    record("Flow delete", status == 200, f"status={status}", _)

            status, _, _ = json_request("DELETE", f"/api/dzh/cells/{cell_id}")
            record("Cell delete", status == 200, f"status={status}", _)
    except Exception as e:
        record("CRUD tests", False, f"异常: {traceback.format_exc()}")

    # 汇总
    print("\n========== 汇总 ==========")
    passed = sum(1 for x in RESULTS if x["ok"])
    total = len(RESULTS)
    print(f"通过: {passed}/{total}")
    for x in RESULTS:
        print(f"{'[PASS]' if x['ok'] else '[FAIL]'} {x['name']}: {x['detail']}")

    with open("/workspace/review_check4_results.json", "w", encoding="utf-8") as f:
        # 避免保存过大数据
        slim = []
        for r in RESULTS:
            d = r.get("data")
            if isinstance(d, dict):
                d = {k: v for k, v in d.items() if k not in ("data", "nodes", "edges", "flows", "cells")}
            slim.append({"name": r["name"], "ok": r["ok"], "detail": r["detail"], "data_keys": list(d.keys()) if isinstance(d, dict) else None})
        json.dump({"passed": passed, "total": total, "results": slim}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
