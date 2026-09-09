"""Funding reconciliation: recorded ledger funding vs real Binance USD-M funding.

Recomputes with the ledger's own rule (qty * mark * rate per settlement, LONG pays when
rate>0) but with (a) the REAL rate of each settlement and (b) the REAL mark at that settlement.
"""
import json, io, urllib.request, urllib.error, time, os, sys
from datetime import datetime, timezone, timedelta

SNAP = r'C:/Users/berke/AppData/Local/Temp/claude/C--Users-berke-Trading-bot/259cbfe4-0b60-466a-b8f2-d5652499b5ef/snapshot'
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
os.makedirs(CACHE, exist_ok=True)
FAPI = 'https://fapi.binance.com'


def _get(path, params):
    q = '&'.join(f'{k}={v}' for k, v in params.items())
    url = f'{FAPI}{path}?{q}'
    key = os.path.join(CACHE, (path + '_' + q).replace('/', '_').replace('&', '_').replace('=', '-') + '.json')
    if os.path.exists(key):
        return json.load(io.open(key, encoding='utf-8'))
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.loads(r.read().decode())
            json.dump(data, io.open(key, 'w', encoding='utf-8'))
            return data
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                json.dump({'__error__': e.code}, io.open(key, 'w', encoding='utf-8'))
                return {'__error__': e.code}
            time.sleep(1.5)
        except Exception:
            time.sleep(1.5)
    return {'__error__': 'net'}


def bsym(symbol):
    return symbol.replace('/', '')


def funding_hist(symbol, start_ms, end_ms):
    out = _get('/fapi/v1/fundingRate', {'symbol': bsym(symbol), 'startTime': start_ms, 'endTime': end_ms, 'limit': 1000})
    if isinstance(out, dict):
        return None
    return out


def marks(symbol, start_ms, end_ms):
    """1h mark-price klines covering the window; returns {open_ms: close_price}."""
    out = _get('/fapi/v1/markPriceKlines', {'symbol': bsym(symbol), 'interval': '1h', 'startTime': start_ms, 'endTime': end_ms, 'limit': 1000})
    if isinstance(out, dict):
        return None
    return {int(k[0]): float(k[4]) for k in out}


def settlements(open_dt, close_dt):
    """00/08/16 UTC settlements strictly after open, <= close — the ledger's own rule."""
    t = open_dt.replace(minute=0, second=0, microsecond=0)
    res = []
    while t <= close_dt + timedelta(hours=8):
        if t.hour in (0, 8, 16) and open_dt < t <= close_dt:
            res.append(t)
        t += timedelta(hours=1)
    return res


def main():
    led = json.load(io.open(SNAP + '/futures_ledger.json', encoding='utf-8'))
    rows = []
    for t in led['history']:
        sym = t['symbol']
        o = datetime.fromisoformat(t['opened_at'])
        c = datetime.fromisoformat(t['closed_at'])
        qty = float(t['quantity'])
        side = t['side']
        rec = float(t['funding'])
        due = settlements(o, c)
        start_ms = int((o - timedelta(hours=9)).timestamp() * 1000)
        end_ms = int((c + timedelta(hours=9)).timestamp() * 1000)
        fh = funding_hist(sym, start_ms, end_ms)
        if fh is None:
            rows.append((t['id'], sym, side, len(due), rec, None, None, 'NO_VENUE'))
            continue
        by_ms = {int(x['fundingTime']) // 1000 * 1000: (float(x['fundingRate']), float(x.get('markPrice') or 0)) for x in fh}
        mk = marks(sym, start_ms, end_ms) or {}
        true_f = 0.0
        matched = 0
        for s in due:
            ms = int(s.timestamp() * 1000)
            # funding events land within a few seconds of the hour
            hit = None
            for cand in (ms, ms + 1000, ms - 1000, ms + 5000, ms + 10000):
                if cand in by_ms:
                    hit = by_ms[cand]
                    break
            if hit is None:
                near = [(abs(k - ms), v) for k, v in by_ms.items() if abs(k - ms) < 120000]
                hit = min(near)[1] if near else None
            if hit is None:
                continue
            matched += 1
            rate, mp = hit
            if not mp:
                mp = mk.get(ms - 3600000, 0) or mk.get(ms, 0)
            if not mp:
                continue
            pay = qty * mp * rate
            true_f += (-pay if side == 'LONG' else pay)
        rows.append((t['id'], sym, side, len(due), rec, true_f, matched, 'OK'))
    print(f"{'id':8} {'symbol':13} {'side':5} {'due':>4} {'matched':>7} {'recorded':>11} {'real':>11} {'err':>11} {'status'}")
    tot_rec = tot_true = tot_abserr = 0.0
    ven = 0
    for r in rows:
        tid, sym, side, due, rec, true_f, matched, st = r
        if st == 'OK':
            err = rec - true_f
            tot_rec += rec; tot_true += true_f; tot_abserr += abs(err); ven += 1
            print(f"{tid:8} {sym:13} {side:5} {due:4d} {matched:7d} {rec:+11.6f} {true_f:+11.6f} {err:+11.6f} OK")
        else:
            print(f"{tid:8} {sym:13} {side:5} {due:4d} {'-':>7} {rec:+11.6f} {'-':>11} {'-':>11} {st}")
    print(f"\nBinance USD-M perpetuals: {ven}/29 trades")
    print(f"  recorded sum {tot_rec:+.6f} | real sum {tot_true:+.6f} | net error {tot_rec-tot_true:+.6f}")
    print(f"  sum of |per-trade error| {tot_abserr:.6f} USDT")
    off = [r for r in rows if r[7] != 'OK']
    print(f"Non-Binance-perp symbols: {len(off)} trades, recorded funding sum {sum(r[4] for r in off):+.6f}")


main()
