"""Verify open-position MFE (and the three MFE-breakeven triggers) against real 1m bars."""
import json, io, urllib.request, os, time
from datetime import datetime, timedelta, timezone

SNAP = r'C:/Users/berke/AppData/Local/Temp/claude/C--Users-berke-Trading-bot/259cbfe4-0b60-466a-b8f2-d5652499b5ef/snapshot'
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'k1m')
SNAP_TS = datetime(2026, 9, 9, 18, 7, 59, tzinfo=timezone.utc)   # ledger updated_at


def kl(sym, s, e, iv='1m'):
    out, cur = [], s
    while cur < e:
        key = os.path.join(CACHE, f'{sym}_{iv}_{cur}.json')
        if os.path.exists(key):
            d = json.load(io.open(key, encoding='utf-8'))
        else:
            url = f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={iv}&startTime={cur}&endTime={e}&limit=1500'
            d = None
            for _ in range(4):
                try:
                    with urllib.request.urlopen(url, timeout=25) as r:
                        d = json.loads(r.read().decode())
                    break
                except Exception:
                    time.sleep(2)
            if d is None:
                return None
            json.dump(d, io.open(key, 'w', encoding='utf-8'))
        if not d:
            break
        out.extend(d)
        nxt = int(d[-1][0]) + (60000 if iv == '1m' else 3600000)
        if nxt <= cur or len(d) < 1500:
            break
        cur = nxt
    return out


led = json.load(io.open(SNAP + '/futures_ledger.json', encoding='utf-8'))
print(f"{'id':7} {'symbol':13} {'side':5} {'mfe_rec':>8} {'mfe_true':>9} {'delta':>7} {'risk%':>7} {'R_rec':>6} {'R_true':>7} {'BE?':>5} {'verdict'}")
for s, p in led['positions'].items():
    sym = s.replace('/', '')
    o = datetime.fromisoformat(p['opened_at'])
    be = (p.get('meta') or {}).get('be_by_mfe')
    end = datetime.fromisoformat(be['at']) if be else SNAP_TS
    entry = float(p['entry_avg']); sign = 1 if p['side'] == 'LONG' else -1
    ist = float(p['initial_stop']) if p.get('initial_stop') else None
    risk_pct = abs(entry - ist) / entry * 100 if ist else None
    ks = kl(sym, int(o.timestamp() * 1000), int(end.timestamp() * 1000) + 60000)
    if not ks:
        print(f"{p['id']:7} {sym:13} {p['side']:5} {float(p['mfe_pct']):8.2f} {'NO_DATA':>9}")
        continue
    inlife = [k for k in ks if int(o.timestamp() * 1000) <= int(k[0]) <= int(end.timestamp() * 1000)]
    if not inlife:
        inlife = ks
    best = max(float(k[2]) for k in inlife) if sign > 0 else min(float(k[3]) for k in inlife)
    mfe_true = (best / entry - 1) * 100 * sign
    rec = float(be['mfe_pct']) if be else float(p['mfe_pct'])
    r_rec = rec / risk_pct if risk_pct else float('nan')
    r_true = mfe_true / risk_pct if risk_pct else float('nan')
    if be:
        verdict = 'BE JUSTIFIED (true MFE >= 1.0R)' if r_true >= 1.0 else '*** BE FIRED ON FALSE MFE ***'
    else:
        verdict = 'overstated' if rec > mfe_true + 0.05 else ('understated' if rec < mfe_true - 0.05 else 'ok')
    print(f"{p['id']:7} {sym:13} {p['side']:5} {rec:8.2f} {mfe_true:9.2f} {rec-mfe_true:+7.2f} "
          f"{(risk_pct if risk_pct else 0):7.2f} {r_rec:6.2f} {r_true:7.2f} {'YES' if be else '-':>5} {verdict}")
