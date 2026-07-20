import requests, json
BASE = 'http://127.0.0.1:8000'

with open('config/pools/target_pool_100.json', 'r', encoding='utf-8') as f:
    pool_config = json.load(f)

print('Pool config id:', pool_config.get('id'), 'nodes:', len(pool_config.get('nodes', [])), 'edges:', len(pool_config.get('edges', [])))

r = requests.post(f'{BASE}/api/sim/start', json={'config': pool_config, 'speed': 1.0}, timeout=10)
print('sim_start status:', r.status_code)
d = r.json()
print('sim_start response code:', d.get('code'), 'msg:', d.get('msg', ''))
if d.get('code') != 0:
    exit(1)
session_id = d['data']['session_id']
print('session_id:', session_id)

event_types = {}
for i in range(10):
    r = requests.post(f'{BASE}/api/sim/control', json={
        'session_id': session_id,
        'action': 'step',
        'params': {'delta': 5.0}
    }, timeout=30)
    d = r.json()
    if d.get('code') != 0:
        print(f'step {i} error:', d.get('msg'))
        break
    data = d.get('data', {})
    events = data.get('events', [])
    clock = data.get('clock', '?')
    for e in events:
        t = e.get('type', 'unknown')
        event_types[t] = event_types.get(t, 0) + 1
    if i < 5 or len(events) > 0:
        print(f'step {i}: clock={clock:.1f}, events={len(events)}')
        if events:
            for e in events[:5]:
                etype = e.get('type')
                ecode = e.get('code', '')
                print(f'    type={etype} code={ecode}')

print()
print('=== Event type counts ===')
for t, c in sorted(event_types.items()):
    print(f'  {t}: {c}')
