import json, urllib.request, sys

BASE = "http://127.0.0.1:18799"

def req(method, path, body=None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

pool_name = "sim-verify-v2"

print("=== Running 100 sim steps ===")
last_ns = {}
for i in range(100):
    s, b = req("POST", "/api/pool/" + pool_name + "/sim/start", {"delta": 60})
    d = b.get("data", {})
    ns = d.get("node_stocks", {})
    if (i + 1) % 25 == 0 or i == 0:
        print("  step", i+1, "clock=", d.get("virtual_clock"),
              "src=", ns.get("src", 0), "A=", ns.get("pool_A", 0),
              "B=", ns.get("pool_B", 0), "C=", ns.get("pool_C", 0))
    last_ns = ns

print()
print("=== FINAL NODE COUNTS ===")
for nid, cnt in last_ns.items():
    print("  ", nid, "=", cnt)

print()
print("=== EVENT PANEL ===")
s, b = req("GET", "/api/pool/" + pool_name + "/event-panel?limit=200")
evts = b.get("events", [])
print("Total events:", len(evts))
by_type = {}
for e in evts:
    if isinstance(e, dict):
        et = e.get("event_type", "?")
        by_type.setdefault(et, []).append(e)
for et, lst in sorted(by_type.items()):
    print("  ", et, ":", len(lst))
    for sample in lst[:2]:
        details = sample.get("details", {})
        print("    ", json.dumps(details, ensure_ascii=False)[:200])

print()
print("=== KLINE API ===")
s, b = req("GET", "/api/kline?stock_code=fz000001&period=5m&limit=5")
print("success:", b.get("success"), "count:", len(b.get("bars", [])))
if b.get("bars"):
    for bar in b["bars"][:2]:
        print("  ", json.dumps(bar))

s, b = req("GET", "/api/kline?stock_code=fz000001&period=1m&limit=5")
print("1m kline count:", len(b.get("bars", [])))

print()
print("=== SIM STATE DETAIL ===")
s, b = req("GET", "/api/pool/" + pool_name + "/sim/state")
d = b.get("data", {})
pools = d.get("pools", {})
for nid in ["pool_A", "pool_B", "pool_C"]:
    p = pools.get(nid, {})
    stocks = p.get("stocks", [])
    if stocks:
        st = stocks[0]
        tracker = st.get("_tracker", {})
        has_tracker = bool(tracker)
        print("  ", nid, "count=", len(stocks), "has_tracker=", has_tracker,
              "price=", st.get("price", 0))
        if has_tracker:
            print("    tracker:", json.dumps(tracker, ensure_ascii=False)[:200])

print()
print("=== VERIFICATION SUMMARY ===")
src_ok = last_ns.get("src", 0) > 0
a_ok = last_ns.get("pool_A", 0) > 0
b_ok = last_ns.get("pool_B", 0) > 0
c_ok = last_ns.get("pool_C", 0) > 0
evt_ok = len(evts) > 0
kline_ok = False

s, b = req("GET", "/api/kline?stock_code=fz000001&period=5m&limit=3")
kline_ok = len(b.get("bars", [])) > 0

print("  src has stocks:", src_ok, "(count:", last_ns.get("src", 0), ")")
print("  pool_A (KDJ) has stocks:", a_ok, "(count:", last_ns.get("pool_A", 0), ")")
print("  pool_B (MACD) has stocks:", b_ok, "(count:", last_ns.get("pool_B", 0), ")")
print("  pool_C (intersection) has stocks:", c_ok, "(count:", last_ns.get("pool_C", 0), ")")
print("  events exist:", evt_ok, "(total:", len(evts), ")")
print("  kline API returns data:", kline_ok)

signal_types = set()
for e in evts:
    if isinstance(e, dict) and e.get("event_type") == "Signal":
        signal_types.add(e.get("details", {}).get("signal_type", "?"))
print("  signal types seen:", signal_types)

has_buy = "BUY" in signal_types
has_sell = "SELL" in signal_types
print("  BUY signals:", has_buy)
print("  SELL signals:", has_sell)
