# -*- coding: utf-8 -*-
"""Aday C1 / temel B0 replay kosucusu — PROTOCOL.md sec. 2-4."""
import io, json, os, subprocess, sys, time, hashlib
from pathlib import Path

WT   = Path(r"C:/Users/berke/wt-entry")
DATA = r"C:/Users/berke/wt-ten/data"
ST   = Path(r"C:/Users/berke/research/entry_v1/state")
CFGD = Path(r"C:/Users/berke/research/entry_v1/configs"); CFGD.mkdir(exist_ok=True)
OUT  = Path(r"C:/Users/berke/research/entry_v1/out"); OUT.mkdir(exist_ok=True)
SYMS = ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","XRP/USDT","LINK/USDT","DOGE/USDT","AVAX/USDT","LTC/USDT","AAVE/USDT"]

def make_config(k, k2, fee_mult=1.0, slip_mult=1.0):
    """config.yaml'i TEK yerden turetir: yalniz hedef katlari (ve stres carpanlari) degisir."""
    src = (WT/"config.yaml").read_text(encoding="utf-8").splitlines()
    out, in_ch, in_fees = [], False, False
    for line in src:
        stripped = line.strip()
        if stripped and not line.startswith((" ", "\t")):
            if in_ch:                                   # coin_heads blogu bitti -> ekle
                out.append("  target_r_multiple: %s" % ("null" if k is None else k))
                out.append("  target2_r_multiple: %s" % ("null" if k2 is None else k2))
                in_ch = False
            in_fees = stripped.startswith("fees:")
            in_ch = stripped.startswith("coin_heads:")
        if in_fees and fee_mult != 1.0 and stripped.startswith(("futures_taker_pct:", "futures_maker_pct:", "spot_taker_pct:")):
            key, val = stripped.split(":", 1)
            out.append("  %s: %s" % (key, round(float(val.strip()) * fee_mult, 6))); continue
        if in_fees and slip_mult != 1.0 and stripped.startswith("slippage_bps:"):
            out.append("  slippage_bps: %s" % round(float(stripped.split(":",1)[1].strip()) * slip_mult, 6)); continue
        out.append(line)
    if in_ch:
        out.append("  target_r_multiple: %s" % ("null" if k is None else k))
        out.append("  target2_r_multiple: %s" % ("null" if k2 is None else k2))
    text = "\n".join(out) + "\n"
    name = "cfg_k%s_f%s_s%s.yaml" % (k, fee_mult, slip_mult)
    p = CFGD/name; p.write_text(text, encoding="utf-8")
    return p, hashlib.sha256(text.encode()).hexdigest()[:16]

def verify_config(path, k, k2, fee_mult, slip_mult):
    """FAIL-CLOSED: `load_config` EKSIK dosyada sessizce VARSAYILAN dondurur. Kosmadan once
    uretilen dosyanin gercekten yuklendigini ve degerlerin istenen degerler oldugunu dogrula."""
    sys.path.insert(0, str(WT))
    from tradingbot.config import load_config
    c = load_config(str(path)); ch = c.v3.coin_heads
    base_taker, base_slip = 0.05, 3.0
    want = {"target_r_multiple": k, "target2_r_multiple": k2,
            "futures_taker_pct": round(base_taker*fee_mult, 6), "slippage_bps": round(base_slip*slip_mult, 6)}
    got = {"target_r_multiple": ch.target_r_multiple, "target2_r_multiple": ch.target2_r_multiple,
           "futures_taker_pct": c.v3.fees.futures_taker_pct, "slippage_bps": c.v3.fees.slippage_bps}
    if got != want:
        raise SystemExit("CONFIG DOGRULAMA BASARISIZ | istenen: %s | yuklenen: %s | dosya: %s" % (want, got, path))
    return got


def run(run_id, k, k2, date_from, date_to, fee_mult=1.0, slip_mult=1.0, stride=1, symbols=None, gate=True):
    cfg, cfg_sha = make_config(k, k2, fee_mult, slip_mult)
    verified = verify_config(cfg, k, k2, fee_mult, slip_mult)
    env = dict(os.environ, TRADINGBOT_CACHE_DIR=DATA, TRADINGBOT_STATE_DIR=str(ST))
    cmd = [sys.executable, "-m", "tradingbot", "--config", str(cfg), "historical-replay",
           "--symbols", *(symbols or SYMS), "--market", "futures", "--tf", "4h",
           "--from", date_from, "--to", date_to, "--stride", str(stride),
           "--run-id", run_id, "--no-patterns"] + ([] if gate else ["--no-economics-gate"]) + [
           "--train-days", "180", "--test-days", "30", "--purge", "6", "--embargo", "6"]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(WT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    el = time.time() - t0
    meta = {"run_id": run_id, "k": k, "k2": k2, "from": date_from, "to": date_to,
            "fee_mult": fee_mult, "slip_mult": slip_mult, "stride": stride, "economics_gate": bool(gate),
            "config_sha256_16": cfg_sha, "config_verified": verified,
            "rc": r.returncode, "elapsed_s": round(el, 1),
            "symbols": symbols or SYMS}
    res_p = ST/"replay"/run_id/"replay_result.json"
    if res_p.exists():
        res = json.load(io.open(res_p, encoding="utf-8"))
        meta.update({kk: res.get(kk) for kk in ("n_decisions","n_actionable","n_opened","determinism_hash","funding_coverage","rejections")})
    if r.returncode != 0:
        meta["stderr_tail"] = r.stderr[-1200:]
    (OUT/("meta_%s.json" % run_id)).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True); ap.add_argument("--k", default="null")
    ap.add_argument("--k2", default="null"); ap.add_argument("--from", dest="f", required=True)
    ap.add_argument("--to", required=True); ap.add_argument("--fee-mult", type=float, default=1.0)
    ap.add_argument("--slip-mult", type=float, default=1.0); ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--no-gate", action="store_true")
    a = ap.parse_args()
    k = None if a.k == "null" else float(a.k); k2 = None if a.k2 == "null" else float(a.k2)
    print(json.dumps(run(a.run_id, k, k2, a.f, a.to, a.fee_mult, a.slip_mult, a.stride, gate=not a.no_gate), ensure_ascii=False))
