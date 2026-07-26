#!/usr/bin/env python3
"""Check 4 复核探测脚本：覆盖重点子条款，避免 step 超时导致整体中断。"""
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://localhost:8000"
RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def req(method, path, body=None, headers=None, timeout=30):
    data = None
    h = headers or {}
    if body is not None:
        if isinstance(body, dict):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        else:
            data = body
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def jreq(method, path, body=None, params=None, timeout=30):
    p = path
    if params:
        p += "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    st, raw, _ = req(method, p, body, timeout=timeout)
    text = raw.decode("utf-8", errors="replace")
    try:
        return st, json.loads(text)
    except Exception:
        return st, {"_raw": text[:300]}


def upload(path, field, file_path, mime):
    boundary = uuid.uuid4().hex
    content = open(file_path, "rb").read()
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{field}"; filename="{Path(file_path).name}"\r\n'
        f'Content-Type: {mime}\r\n\r\n'
    ).encode() + content + f'\r\n--{boundary}--\r\n'.encode()
    h = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return req("POST", path, body, h)


def jupload(path, field, file_path, mime):
    st, raw, _ = upload(path, field, file_path, mime)
    text = raw.decode("utf-8", errors="replace")
    try:
        return st, json.loads(text)
    except Exception:
        return st, {"_raw": text[:300]}


def main():
    import urllib.parse  # noqa

    dzh_xml = "/workspace/docs/samples/pools/超赢7号.xml"
    tdx_xml = "/workspace/tdxpool/黑马一号池.xml"

    # 1. DZH import-and-save
    st, d = jupload("/api/dzh/import-and-save", "file", dzh_xml, "application/xml")
    ok = st == 200 and d.get("success")
    dzh_cfg = d.get("data") if ok else None
    record("DZH XML import-and-save", ok,
           f"status={st}, nodes={d.get('meta',{}).get('node_count')}, edges={d.get('meta',{}).get('edge_count')}")

    # 2. DZH export (valid XML)
    if dzh_cfg:
        st, raw, hs = req("POST", "/api/dzh/export", {"config": dzh_cfg})
        ok = st == 200 and len(raw) > 0 and raw.lstrip().startswith(b"<?xml")
        record("DZH XML export", ok, f"status={st}, bytes={len(raw)}, ctype={hs.get('Content-Type')}")

        st, raw2, hs2 = req("POST", "/api/dzh/export-meta", {"config": dzh_cfg})
        ok2 = st == 200 and len(raw2) > 0 and raw2.lstrip().startswith(b"<?xml")
        record("DZH XML export-meta", ok2, f"status={st}, bytes={len(raw2)}, ctype={hs2.get('Content-Type')}")
    else:
        record("DZH XML export", False, "no config")
        record("DZH XML export-meta", False, "no config")

    # 3. TDX dedicated import
    st, raw, _ = upload("/api/dzh/tdx/import", "file", tdx_xml, "application/xml")
    text = raw.decode("utf-8", errors="replace")
    try:
        dtdx = json.loads(text)
    except Exception:
        dtdx = {"_raw": text[:200]}
    record("TDX XML import (/api/dzh/tdx/import)", st == 200 and dtdx.get("success"),
           f"status={st}, success={dtdx.get('success')}, error={dtdx.get('error','')[:120]}")

    # 4. TDX auto-detect via /api/dzh/import
    st, raw, _ = upload("/api/dzh/import", "file", tdx_xml, "application/xml")
    text = raw.decode("utf-8", errors="replace")
    try:
        dtdx2 = json.loads(text)
    except Exception:
        dtdx2 = {"_raw": text[:200]}
    record("TDX XML import (/api/dzh/import auto-detect)", st == 200 and dtdx2.get("success"),
           f"status={st}, success={dtdx2.get('success')}, error={dtdx2.get('error','')[:120]}")

    # 5. JSON import v2
    valid_json = {
        "version": 2,
        "pool_meta": {"pool_type": "dzh", "name": "check4_demo", "mode": "1", "ver": "1.0"},
        "nodes": [
            {"id": "1", "type": "202", "label": "沪深A股",
             "params": {"dzh_cell_type": 202, "attr": 128, "attrtext": "SH#上证A股"},
             "position": {"x": 10, "y": 10, "width": 100, "height": 80}},
            {"id": "2", "type": "200", "label": "状态池",
             "params": {"dzh_cell_type": 200, "attr": 0, "hold_sec": 3600},
             "position": {"x": 200, "y": 10, "width": 120, "height": 90}}
        ],
        "edges": [
            {"id": "e1", "from": "1", "to": "2",
             "params": {"conditional": True, "line_style": "solid", "desc": "条件连线", "width": 2}}
        ]
    }
    json_path = "/workspace/check4_valid.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(valid_json, f, ensure_ascii=False)
    st, djson = jupload("/api/json/import", "file", json_path, "application/json")
    json_cfg = djson.get("data") if st == 200 and djson.get("success") else None
    record("JSON import (valid v2)", json_cfg is not None,
           f"status={st}, success={djson.get('success')}, nodes={len(json_cfg.get('nodes', [])) if json_cfg else None}")

    # 6. JSON export roundtrip
    if json_cfg:
        st, raw, _ = req("POST", "/api/json/export", {"config": json_cfg})
        try:
            exported = json.loads(raw.decode("utf-8"))
        except Exception:
            exported = None
        record("JSON export roundtrip", st == 200 and exported is not None and "version" in exported,
               f"status={st}, has_version={exported is not None and 'version' in exported}")
    else:
        record("JSON export roundtrip", False, "no config")

    # 7. indicator / kline / flows / timer-queue
    st, d = jreq("GET", "/api/indicator/values", params={"node_id": "000001", "formula": "MA(C,5)", "period": "1d"})
    record("Indicator values", st == 200 and d.get("code") == 0 and isinstance(d.get("data"), list),
           f"status={st}, code={d.get('code')}, count={len(d.get('data',[]))}")

    st, d = jreq("GET", "/api/kline", params={"stock_code": "600000", "period": "1d", "limit": 50})
    record("K-line", st == 200 and d.get("code") == 0 and isinstance(d.get("data"), list),
           f"status={st}, code={d.get('code')}, count={len(d.get('data',[]))}")

    st, d = jreq("GET", "/api/dzh/flows")
    flows = d.get("flows", [])
    has_src_tgt = all("source" in f and "target" in f for f in flows)
    record("DZH flows source/target", st == 200 and isinstance(flows, list) and (len(flows) == 0 or has_src_tgt),
           f"status={st}, count={len(flows)}, src/tgt_ok={has_src_tgt}")

    st, d = jreq("GET", "/api/events/timer-queue")
    record("Events timer-queue", st == 200 and d.get("success") and isinstance(d.get("timers"), list),
           f"status={st}, count={d.get('count')}, timers={len(d.get('timers',[]))}")

    # 8. Runtime mode initial
    st, d = jreq("GET", "/api/state/runtime")
    initial_mode = d.get("mode", "unknown") if st == 200 else "unknown"
    record("Runtime state (initial)", st == 200 and d.get("mode") is not None,
           f"status={st}, mode={initial_mode}")

    # 9. Sim start + wait init + pause/resume/stop
    sim_cfg = json_cfg if json_cfg else dzh_cfg
    st, d = jreq("POST", "/api/sim/start", {"config": sim_cfg, "speed": 1.0})
    sim_ok = st == 200 and d.get("code") == 0
    sim_sid = d.get("data", {}).get("session_id") if sim_ok else None
    record("Sim start", sim_ok, f"status={st}, session_id={sim_sid}")

    if sim_sid:
        init_ok = False
        for _ in range(30):
            st, d = jreq("GET", "/api/sim/state", params={"session_id": sim_sid})
            if st == 200 and d.get("code") == 0:
                init_ok = True
                break
            time.sleep(0.5)
        record("Sim init complete", init_ok, f"ok={init_ok}")

        for action in ("pause", "resume", "stop"):
            try:
                st, d = jreq("POST", "/api/sim/control", {"session_id": sim_sid, "action": action}, timeout=30)
                record(f"Sim {action}", st == 200 and d.get("code") == 0,
                       f"status={st}, code={d.get('code')}, msg={d.get('msg')}")
            except Exception as e:
                record(f"Sim {action}", False, f"异常/超时: {e}")

    # 10. Replay start + pause/stop
    st, d = jreq("POST", "/api/replay/start", {"config": sim_cfg, "speed": 1.0, "base_period": "day"}, timeout=60)
    rep_ok = st == 200 and d.get("code") == 0
    rep_sid = d.get("data", {}).get("session_id") if rep_ok else None
    record("Replay start", rep_ok, f"status={st}, session_id={rep_sid}, msg={d.get('msg')}")

    if rep_sid:
        for action in ("pause", "stop"):
            try:
                st, d = jreq("POST", "/api/replay/control", {"session_id": rep_sid, "action": action}, timeout=30)
                record(f"Replay {action}", st == 200 and d.get("code") == 0,
                       f"status={st}, code={d.get('code')}, msg={d.get('msg')}")
            except Exception as e:
                record(f"Replay {action}", False, f"异常/超时: {e}")

    # 11. Runtime mode after replay
    st, d = jreq("GET", "/api/state/runtime")
    after_mode = d.get("mode", "unknown") if st == 200 else "unknown"
    record("Runtime state (after replay stop)", st == 200, f"status={st}, mode={after_mode}")

    # 12. CRUD cell / flow
    st, d = jreq("POST", "/api/dzh/cells", {"cell_type": 200, "position": {"x": 10, "y": 20, "width": 100, "height": 80}, "params": {"label": "test_state"}})
    cell_ok = st == 200 and d.get("success")
    cell_id = d.get("data", {}).get("id") if cell_ok else None
    record("Cell create", cell_ok, f"status={st}, id={cell_id}")

    if cell_id:
        st, d = jreq("PUT", f"/api/dzh/cells/{cell_id}", {"label": "updated", "params": {"hold_sec": 3600}})
        record("Cell update", st == 200 and d.get("success"), f"status={st}")

        st, d = jreq("POST", "/api/dzh/cells", {"cell_type": 202, "position": {"x": 200, "y": 20, "width": 100, "height": 80}, "params": {"label": "test_src"}})
        cell2_id = d.get("data", {}).get("id") if st == 200 and d.get("success") else None
        record("Cell create (source)", cell2_id is not None, f"id={cell2_id}")

        if cell2_id:
            st, d = jreq("POST", "/api/dzh/flows", {"source": cell2_id, "target": cell_id, "params": {"conditional": True, "line_style": "solid", "desc": "test", "width": 2}})
            flow_id = d.get("data", {}).get("id") if st == 200 and d.get("success") else None
            record("Flow create", flow_id is not None, f"status={st}, id={flow_id}")

            if flow_id:
                st, d = jreq("PUT", f"/api/dzh/flows/{flow_id}", {"params": {"conditional": False, "line_style": "dashed", "desc": "updated", "width": 3}})
                record("Flow update", st == 200 and d.get("success"), f"status={st}")
                st, d = jreq("DELETE", f"/api/dzh/flows/{flow_id}")
                record("Flow delete", st == 200, f"status={st}")

        st, d = jreq("DELETE", f"/api/dzh/cells/{cell_id}")
        record("Cell delete", st == 200, f"status={st}")

    print("\n========== 汇总 ==========")
    passed = sum(1 for x in RESULTS if x["ok"])
    total = len(RESULTS)
    print(f"通过: {passed}/{total}")
    for x in RESULTS:
        print(f"[{'PASS' if x['ok'] else 'FAIL'}] {x['name']}: {x['detail']}")


if __name__ == "__main__":
    main()
