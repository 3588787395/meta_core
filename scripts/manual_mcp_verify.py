import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:18766"


def req(method, path, body=None):
    url = f"{BASE}{path}"
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))
    except Exception as e:
        return -1, str(e)


print("=== Step 1: Create pool with DZH TTL state pool + reload candidate pool ===")
pool = {
    "name": "manual-mcp-verify",
    "pool_type": "dzh",
    "nodes": [
        {"id": "src1", "type": "dzh_candidate", "label": "备选池",
         "params": {"attrtext": "SH#上证A股", "reload": -57387, "reload_mode": "daily_time", "reload_param": "093000"},
         "position": {"x": 100, "y": 200}},
        {"id": "state1", "type": "dzh_state_pool", "label": "状态池",
         "params": {"hold": 0, "deltype": 0, "delstocktype": 1, "endtime": 5400},
         "position": {"x": 400, "y": 200}}
    ],
    "edges": [{"id": "e1", "from": "src1", "to": "state1", "params": {}}],
    "pool_meta": {"ver": "1.0", "mode": "flow"}
}
status, body = req("POST", "/api/pools", pool)
print(f"  status={status} code={body.get('code') if isinstance(body, dict) else 'N/A'} msg={body.get('message', '') if isinstance(body, dict) else body}")
pool_id = body.get("data", {}).get("pool_id", "") if isinstance(body, dict) else ""
print(f"  pool_id={pool_id}")

print("\n=== Step 2: Mock run ===")
status, body = req("POST", f"/api/pools/{pool_id}/run", {"mode": "mock"})
print(f"  status={status} code={body.get('code') if isinstance(body, dict) else 'N/A'}")
if isinstance(body, dict) and "data" in body:
    print(f"  nodes={list(body['data'].get('node_stocks', {}).keys())}")
    print(f"  state1_count={len(body['data'].get('node_stocks', {}).get('state1', []))}")

print("\n=== Step 3: Simulation start & step ===")
status, body = req("POST", "/api/sim/start", {"pool_id": pool_id, "speed": 1.0})
print(f"  status={status} code={body.get('code') if isinstance(body, dict) else 'N/A'}")
sid = body.get("data", {}).get("session_id", "") if isinstance(body, dict) else ""
print(f"  session={sid[:20] if sid else 'N/A'}")
if sid:
    status, body = req("POST", "/api/sim/control", {"session_id": sid, "action": "step"})
    print(f"  step1 status={status} code={body.get('code') if isinstance(body, dict) else 'N/A'}")
    status, body = req("POST", "/api/sim/control", {"session_id": sid, "action": "stop"})
    print(f"  stop status={status} code={body.get('code') if isinstance(body, dict) else 'N/A'}")

print("\n=== Step 4: Replay start & step ===")
status, body = req("POST", "/api/replay/start", {"pool_id": pool_id, "speed": 1.0})
print(f"  status={status} code={body.get('code') if isinstance(body, dict) else 'N/A'}")
rid = body.get("data", {}).get("session_id", "") if isinstance(body, dict) else ""
print(f"  session={rid[:20] if rid else 'N/A'}")
if rid:
    status, body = req("POST", "/api/replay/control", {"session_id": rid, "action": "next"})
    print(f"  next1 status={status} code={body.get('code') if isinstance(body, dict) else 'N/A'}")
    status, body = req("POST", "/api/replay/control", {"session_id": rid, "action": "stop"})
    print(f"  stop status={status} code={body.get('code') if isinstance(body, dict) else 'N/A'}")

print("\n=== Step 5: Get pool back ===")
status, body = req("GET", f"/api/pools/{pool_id}")
print(f"  status={status} code={body.get('code') if isinstance(body, dict) else 'N/A'} name={(body.get('data') or {}).get('name', '') if isinstance(body, dict) else 'N/A'}")

print("\n=== Done ===")
