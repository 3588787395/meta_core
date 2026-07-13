"""
手动 MCP 验证脚本：创建仿真股票池并验证所有功能
  - 100 fz 股票 → KDJ金叉(5m)→A池 → MACD金叉(1m)→B池 → A∩B→C池 → 入池买100股 → TTL出池卖全部
"""
import json
import time
import urllib.request
import urllib.error
import sys

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


def p(stage, status, body):
    code = body.get("code", "?") if isinstance(body, dict) else "?"
    msg = body.get("msg", body.get("message", "")) if isinstance(body, dict) else str(body)[:200]
    data_summary = ""
    if isinstance(body, dict) and "data" in body:
        d = body["data"]
        if isinstance(d, dict):
            data_summary = json.dumps({k: (v if not isinstance(v, list) else f"[{len(v)} items]")
                                        for k, v in d.items()}, ensure_ascii=False)[:300]
        else:
            data_summary = str(d)[:200]
    print(f"[{stage}] status={status} code={code} msg={msg}")
    if data_summary:
        print(f"       data={data_summary}")


# ══════════════════════════════════════════════════════════════
# Step 1: 创建仿真股票池
# ══════════════════════════════════════════════════════════════
print("=" * 70)
print("Step 1: 创建仿真股票池 (KDJ金叉→A, MACD金叉→B, A∩B→C)")
print("=" * 70)

pool_config = {
    "name": "sim-kdj-macd-demo",
    "pool_type": "dzh",
    "nodes": [
        {
            "id": "src",
            "type": "market_source",
            "label": "候选源",
            "params": {"markets": "fz_a"},
            "position": {"x": 100, "y": 300}
        },
        {
            "id": "pool_A",
            "type": "state_pool",
            "label": "A池-KDJ金叉",
            "params": {"hold": 100, "deltype": 3, "delstocktype": 0},
            "position": {"x": 350, "y": 150}
        },
        {
            "id": "pool_B",
            "type": "state_pool",
            "label": "B池-MACD金叉",
            "params": {"hold": 200, "deltype": 3, "delstocktype": 0},
            "position": {"x": 350, "y": 450}
        },
        {
            "id": "pool_C",
            "type": "state_pool",
            "label": "C池-交集",
            "params": {"hold": 20, "deltype": 3, "delstocktype": 0},
            "position": {"x": 650, "y": 300}
        },
    ],
    "edges": [
        {
            "id": "e_kdj",
            "from": "src",
            "to": "pool_A",
            "label": "KDJ金叉(5m)",
            "params": {
                "starttype": 1,
                "cxtype": 1,
                "nperiod": 2,
                "nperiodnum": 5,
                "ntjindexno": 0,
                "condition": "KDJ金叉",
                "formula_ref": "KDJ金叉",
                "formula_period": "5m",
                "jgtime": 60,
            }
        },
        {
            "id": "e_macd",
            "from": "src",
            "to": "pool_B",
            "label": "MACD金叉(1m)",
            "params": {
                "starttype": 1,
                "cxtype": 1,
                "nperiod": 1,
                "nperiodnum": 1,
                "ntjindexno": 0,
                "condition": "MACD金叉",
                "formula_ref": "MACD金叉",
                "formula_period": "1m",
                "jgtime": 10,
            }
        },
        {
            "id": "e_intersect_a",
            "from": "pool_A",
            "to": "pool_C",
            "label": "A→C(交集)",
            "params": {
                "starttype": 1,
                "cxtype": 1,
                "nset": 5,
                "ntjindexno": 2,
                "ntjindexs": "pool_A,pool_B",
                "nperiod": 0,
                "jgtime": 5,
            }
        },
        {
            "id": "e_intersect_b",
            "from": "pool_B",
            "to": "pool_C",
            "label": "B→C(交集)",
            "params": {
                "starttype": 1,
                "cxtype": 1,
                "nset": 5,
                "ntjindexno": 2,
                "ntjindexs": "pool_A,pool_B",
                "nperiod": 0,
                "jgtime": 5,
            }
        },
    ],
    "pool_meta": {"ver": "1.0", "mode": "flow"}
}

status, body = req("POST", "/api/pools", pool_config)
p("CREATE", status, body)
pool_id = ""
if isinstance(body, dict):
    pool_id = body.get("data", {}).get("pool_id", "") if isinstance(body.get("data"), dict) else body.get("data", "")
    if not pool_id and status == 200:
        pool_id = body.get("pool_id", body.get("id", ""))
print(f"  pool_id = {pool_id}")

# ══════════════════════════════════════════════════════════════
# Step 2: 启动仿真会话
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("Step 2: 启动仿真会话")
print("=" * 70)

sim_body = {"config": pool_config, "speed": 60.0}
if pool_id:
    sim_body["pool_id"] = pool_id

status, body = req("POST", "/api/sim/start", sim_body)
p("SIM-START", status, body)
session_id = ""
if isinstance(body, dict) and isinstance(body.get("data"), dict):
    session_id = body["data"].get("session_id", "")
print(f"  session_id = {session_id[:30]}")

if not session_id:
    # 尝试直接用 config
    sim_body2 = {"config": pool_config, "speed": 60.0}
    status, body = req("POST", "/api/sim/start", sim_body2)
    p("SIM-START-RETRY", status, body)
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        session_id = body["data"].get("session_id", "")

if not session_id:
    print("  ERROR: 无法启动仿真会话，退出")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════
# Step 3: 运行多步仿真 (300步 = 300s虚拟时间 = 5min)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("Step 3: 运行多步仿真 (每步60s, 共200步 = 200min虚拟时间)")
print("=" * 70)

node_stocks = {}
for i in range(200):
    status, body = req("POST", "/api/sim/control", {
        "session_id": session_id,
        "action": "step",
        "params": {"delta": 60.0}
    })
    if i % 20 == 0 or i == 199:
        if isinstance(body, dict) and isinstance(body.get("data"), dict):
            ns = body["data"].get("node_stocks", {})
            counts = {k: len(v) if isinstance(v, list) else "?" for k, v in ns.items()}
            print(f"  step {i+1}: node_counts={counts}")
        elif i % 50 == 0:
            code = body.get("code", "?") if isinstance(body, dict) else "?"
            msg = body.get("msg", "")[:80] if isinstance(body, dict) else str(body)[:80]
            print(f"  step {i+1}: code={code} msg={msg}")
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        node_stocks = body["data"].get("node_stocks", {})

# ══════════════════════════════════════════════════════════════
# Step 4: 查看仿真状态
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("Step 4: 查看仿真状态")
print("=" * 70)

status, body = req("GET", f"/api/sim/state?session_id={session_id}")
p("SIM-STATE", status, body)
if isinstance(body, dict) and isinstance(body.get("data"), dict):
    d = body["data"]
    clock = d.get("clock", d.get("current_ts", "?"))
    ns = d.get("node_stocks", {})
    print(f"  clock={clock}")
    for nid, stocks in ns.items():
        if isinstance(stocks, list):
            codes = [s.get("code", s) if isinstance(s, dict) else str(s) for s in stocks[:5]]
            print(f"  {nid}: {len(stocks)} stocks, sample={codes}")

# ══════════════════════════════════════════════════════════════
# Step 5: 查看事件
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("Step 5: 查看仿真事件")
print("=" * 70)

status, body = req("GET", f"/api/sim/events?session_id={session_id}")
p("SIM-EVENTS", status, body)
if isinstance(body, dict):
    events = body.get("data", body.get("events", []))
    if isinstance(events, list):
        print(f"  total events = {len(events)}")
        for ev in events[:10]:
            if isinstance(ev, dict):
                etype = ev.get("event_type", ev.get("type", "?"))
                code = ev.get("code", ev.get("details", {}).get("code", ""))
                pool = ev.get("pool_id", ev.get("details", {}).get("pool_id", ""))
                print(f"  {etype}: code={code} pool={pool}")

# ══════════════════════════════════════════════════════════════
# Step 6: 停止仿真
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("Step 6: 停止仿真会话")
print("=" * 70)

status, body = req("POST", "/api/sim/control", {
    "session_id": session_id,
    "action": "stop"
})
p("SIM-STOP", status, body)

# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("验证摘要")
print("=" * 70)

pool_A_count = len(node_stocks.get("pool_A", [])) if isinstance(node_stocks.get("pool_A"), list) else 0
pool_B_count = len(node_stocks.get("pool_B", [])) if isinstance(node_stocks.get("pool_B"), list) else 0
pool_C_count = len(node_stocks.get("pool_C", [])) if isinstance(node_stocks.get("pool_C"), list) else 0
src_count = len(node_stocks.get("src", [])) if isinstance(node_stocks.get("src"), list) else 0

print(f"  候选源 (src):     {src_count} stocks")
print(f"  A池 (KDJ金叉):    {pool_A_count} stocks")
print(f"  B池 (MACD金叉):   {pool_B_count} stocks")
print(f"  C池 (交集):       {pool_C_count} stocks")

if src_count > 0:
    print("  ✅ fz 候选源股票生成正常")
else:
    print("  ❌ fz 候选源股票为空")

if pool_A_count > 0 or pool_B_count > 0:
    print("  ✅ 公式求值产生入池信号（KDJ/MACD 金叉检测到了）")
else:
    print("  ⚠️  A池/B池为空 — 可能需要更多仿真步数让金叉出现")

if pool_C_count > 0:
    print("  ✅ 交集运算 A∩B→C 正常")
else:
    print("  ⚠️  C池为空 — 可能A∩B尚未同时满足")

print("\nDone.")
