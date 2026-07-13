import json, urllib.request, urllib.error, sys

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
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        return -1, str(e)

last_ns = {}
last_events = []
for i in range(100):
    status, body = req("POST", "/api/pool/sim-kdj-macd-demo/sim/start", {"delta": 60})
    if i % 20 == 0 or i == 99:
        if isinstance(body, dict) and body.get("success"):
            d = body.get("data", {})
            ns = d.get("node_stocks", {})
            events = d.get("events", [])
            clock = d.get("virtual_clock", "?")
            src_c = ns.get("src", 0)
            a_c = ns.get("pool_A", 0)
            b_c = ns.get("pool_B", 0)
            c_c = ns.get("pool_C", 0)
            ev_c = d.get("event_count", 0)
            print(f"step {i+1}: clock={clock} src={src_c} A={a_c} B={b_c} C={c_c} events={ev_c}")
            last_ns = ns
            last_events = events

print()
print("=== Final State ===")
for nid, count in last_ns.items():
    print(f"  {nid}: {count} stocks")

if last_events:
    print()
    print("=== Last Events ===")
    for ev in last_events[:15]:
        print(f"  {json.dumps(ev, ensure_ascii=False)[:200]}")
else:
    print()
    print("=== No events in last response ===")
