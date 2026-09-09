"""For each overstated MFE, find which 1h bar could have produced the recorded value."""
import json, io, urllib.request, os, time
from datetime import datetime, timedelta

SNAP = r'C:/Users/berke/AppData/Local/Temp/claude/C--Users-berke-Trading-bot/259cbfe4-0b60-466a-b8f2-d5652499b5ef/snapshot'
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'k1h')
os.makedirs(CACHE, exist_ok=True)

def k1h(sym, s, e):
    key = os.path.join(CACHE, f'{sym}_{s}_{e}.json')
    if os.path.exists(key):
        return json.load(io.open(key, encoding='utf-8'))
    url = f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&startTime={s}&endTime={e}&limit=1500'
    for _ in range(4):
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                d = json.loads(r.read().decode())
            json.dump(d, io.open(key, 'w', encoding='utf-8'))
            return d
        except Exception:
            time.sleep(2)
    return []

TARGETS = {'F00015': 'STXUSDT', 'F00020': 'AAPLUSDT', 'F00025': 'SPCXUSDT',
           'F00022': 'SUIUSDT', 'F00030': 'NATGASUSDT', 'F00034': 'NVDAUSDT'}
led = json.load(io.open(SNAP + '/futures_ledger.json', encoding='utf-8'))
for t in led['history']:
    if t['id'] not in TARGETS:
        continue
    sym = TARGETS[t['id']]
    o = datetime.fromisoformat(t['opened_at']); c = datetime.fromisoformat(t['closed_at'])
    entry = float(t['entry']); sign = 1 if t['side'] == 'LONG' else -1
    rec = float(t['mfe_pct'])
    need = entry * (1 + sign * rec / 100)          # price that would produce the recorded MFE
    ks = k1h(sym, int((o - timedelta(days=10)).timestamp() * 1000), int(c.timestamp() * 1000))
    hits = []
    for k in ks:
        bt = datetime.utcfromtimestamp(int(k[0]) / 1000)
        ext = float(k[2]) if sign > 0 else float(k[3])
        reached = ext >= need if sign > 0 else ext <= need
        if reached:
            hits.append((bt, ext, (ext / entry - 1) * 100 * sign))
    pre = [h for h in hits if h[0] < o.replace(tzinfo=None)]
    print(f"{t['id']} {sym} {t['side']} entry {entry} opened {o:%Y-%m-%d %H:%M} closed {c:%Y-%m-%d %H:%M}")
    print(f"   recorded MFE {rec:.2f}% -> needs price {need:.6g}; 1h bars reaching it: {len(hits)} total, {len(pre)} BEFORE entry")
    if pre:
        first, last = pre[0], pre[-1]
        print(f"   nearest pre-entry 1h bar reaching it: {last[0]:%Y-%m-%d %H:%M} ext {last[1]:.6g} ({last[2]:+.2f}% vs entry)"
              f"  |  lag {(o.replace(tzinfo=None)-last[0]).total_seconds()/3600:.1f} h before entry")
    elif hits:
        print(f"   only reached AFTER entry at {hits[0][0]:%Y-%m-%d %H:%M} (inside lifetime) -> not a staleness case")
    else:
        print("   NEVER reached in [entry-10d, close] -> unexplained by 1h bars")
