"""MFE/MAE reconciliation against real 1m bars, strictly inside [opened_at, closed_at]."""
import json, io, urllib.request, urllib.error, time, os
from datetime import datetime, timezone, timedelta

SNAP = r'C:/Users/berke/AppData/Local/Temp/claude/C--Users-berke-Trading-bot/259cbfe4-0b60-466a-b8f2-d5652499b5ef/snapshot'
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'k1m')
os.makedirs(CACHE, exist_ok=True)
FAPI = 'https://fapi.binance.com'


def klines(sym, start_ms, end_ms):
    """All 1m klines [start_ms, end_ms], cached per (sym,start,end) chunk."""
    out, cur = [], start_ms
    while cur < end_ms:
        key = os.path.join(CACHE, f'{sym}_{cur}.json')
        if os.path.exists(key):
            d = json.load(io.open(key, encoding='utf-8'))
        else:
            url = f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=1m&startTime={cur}&endTime={end_ms}&limit=1500'
            d = None
            for _ in range(4):
                try:
                    with urllib.request.urlopen(url, timeout=25) as r:
                        d = json.loads(r.read().decode())
                    break
                except Exception:
                    time.sleep(2)
            if d is None:
                return out, False
            json.dump(d, io.open(key, 'w', encoding='utf-8'))
        if not d:
            break
        out.extend(d)
        nxt = int(d[-1][0]) + 60000
        if nxt <= cur:
            break
        cur = nxt
        if len(d) < 1500:
            break
    return out, True


def main():
    led = json.load(io.open(SNAP + '/futures_ledger.json', encoding='utf-8'))
    print(f"{'id':8} {'symbol':13} {'side':5} {'bars':>6} {'mfe_rec':>8} {'mfe_true':>9} {'d_mfe':>8} {'mae_rec':>8} {'mae_true':>9} {'d_mae':>8} {'pre1h':>7}")
    rows = []
    for t in led['history']:
        sym = t['symbol'].replace('/', '')
        o = datetime.fromisoformat(t['opened_at'])
        c = datetime.fromisoformat(t['closed_at'])
        entry = float(t['entry'])
        sign = 1 if t['side'] == 'LONG' else -1
        o_ms, c_ms = int(o.timestamp() * 1000), int(c.timestamp() * 1000)
        # 1m bars strictly inside the position lifetime (bar open >= entry minute, bar open <= exit)
        ks, ok = klines(sym, o_ms - 3600000, c_ms + 120000)
        if not ok or not ks:
            print(f"{t['id']:8} {sym:13} {t['side']:5} {'-':>6} DATA_MISSING")
            continue
        inlife = [k for k in ks if o_ms <= int(k[0]) <= c_ms]
        if not inlife:
            inlife = [k for k in ks if o_ms - 60000 <= int(k[0]) <= c_ms + 60000]
        hi = max(float(k[2]) for k in inlife)
        lo = min(float(k[3]) for k in inlife)
        best = hi if sign > 0 else lo
        worst = lo if sign > 0 else hi
        mfe_true = (best / entry - 1) * 100 * sign
        mae_true = (worst / entry - 1) * 100 * sign
        # what the pre-entry part of the entry hour would have given
        h_start = int(o.replace(minute=0, second=0, microsecond=0).timestamp() * 1000)
        pre = [k for k in ks if h_start <= int(k[0]) < o_ms]
        pre_best = None
        if pre:
            pb = max(float(k[2]) for k in pre) if sign > 0 else min(float(k[3]) for k in pre)
            pre_best = (pb / entry - 1) * 100 * sign
        rec_mfe, rec_mae = float(t['mfe_pct']), float(t['mae_pct'])
        rows.append((t['id'], sym, t['side'], rec_mfe, mfe_true, rec_mae, mae_true, pre_best, len(inlife)))
        print(f"{t['id']:8} {sym:13} {t['side']:5} {len(inlife):6d} {rec_mfe:8.2f} {mfe_true:9.2f} {rec_mfe-mfe_true:+8.2f} "
              f"{rec_mae:8.2f} {mae_true:9.2f} {rec_mae-mae_true:+8.2f} {('%7.2f' % pre_best) if pre_best is not None else '      -'}")
    imposs = [r for r in rows if r[3] > r[4] + 0.05]
    under = [r for r in rows if r[3] < r[4] - 0.05]
    print(f"\nn={len(rows)}  MFE overstated (physically impossible): {len(imposs)}  understated: {len(under)}  exact-ish: {len(rows)-len(imposs)-len(under)}")
    for r in imposs:
        expl = 'pre-entry hour explains it' if (r[7] is not None and r[7] >= r[3] - 0.05) else 'NOT explained by pre-entry hour'
        print(f"  OVER {r[0]} {r[1]:12} rec {r[3]:.2f} > true {r[4]:.2f}  pre-entry-best {r[7] if r[7] is None else round(r[7],2)}  -> {expl}")
    mae_imposs = [r for r in rows if r[5] < r[6] - 0.05]
    print(f"MAE overstated (worse than any real bar): {len(mae_imposs)}")
    for r in mae_imposs:
        print(f"  OVER-MAE {r[0]} {r[1]:12} rec {r[5]:.2f} < true {r[6]:.2f}")
    json.dump([list(r) for r in rows], io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mfe_rows.json'), 'w', encoding='utf-8'))


main()
