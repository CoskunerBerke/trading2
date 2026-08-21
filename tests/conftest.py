"""Paylaşılan test yardımcıları — FeatureSnapshotV3 üreten gerçekçi fixture'lar.

Coverage gate artık sparse hafızayı (yalnız expected_r dolu, Core-4 gibi) bloklar; bu yüzden replay
fixture'ları gerçek snapshot şemasıyla üretilir. `sparse` modu eski davranışı taklit eder ve gate'in
gerçekten blokladığını göstermek için kullanılır.
"""
from __future__ import annotations

import math
import pandas as pd

from tradingbot.learn.snapshot import build_snapshot

BAR_MS = 86_400_000


def synth_bars(n: int = 160, *, end_ms: int, seed: int = 3, drift: float = 0.05, bar_ms: int = BAR_MS) -> pd.DataFrame:
    """Deterministik sentetik mumlar (rastgelelik yok; sinüs + drift)."""
    ts = [end_ms - (n - 1 - i) * bar_ms for i in range(n)]
    close = [100.0 + drift * i + 6.0 * math.sin((i + seed) / 9.0) + 2.0 * math.sin((i + seed) / 3.3) for i in range(n)]
    return pd.DataFrame({
        "timestamp": ts, "open": [c * 0.999 for c in close],
        "high": [c * 1.012 for c in close], "low": [c * 0.988 for c in close], "close": close,
        "volume": [1000.0 + 40.0 * math.sin((i + seed) / 5.0) + i for i in range(n)],
    })


def make_snapshot(*, symbol: str, side: str, decision_ts_ms: int, seed: int = 3, source: str = "HISTORICAL_REPLAY",
                  bar_ms: int = BAR_MS, entry: float | None = None, strength: float = 0.35) -> dict:
    """Gerçekçi, dolu FeatureSnapshotV3 (coverage gate'i geçecek kadar kapsamlı).

    `strength`: kurulumun gücü (-1..1). Ajan bias'ı, konsensüs ve beklenen R'yi birlikte hareket ettirir;
    model testlerinin ayrıştırılabilir iki sınıf üretebilmesi için gerekir (sabit fixture'da öğrenilecek
    sinyal olmaz). Yalnız `prediction_features_v3` alanlarını etkiler.
    """
    bars = synth_bars(end_ms=decision_ts_ms, seed=seed, bar_ms=bar_ms)
    btc = synth_bars(end_ms=decision_ts_ms, seed=seed + 11, drift=0.03, bar_ms=bar_ms)
    px = float(bars["close"].iloc[-1]) if entry is None else float(entry)
    sgn = 1.0 if side.upper() == "LONG" else -1.0
    snap = build_snapshot(
        symbol=symbol, market_type="USDM_PERP", timeframe="4h", side=side, decision_ts_ms=decision_ts_ms,
        bars=bars, source=source, btc_bars=btc,
        funding={"rate": 0.0001 * sgn, "z": strength * sgn},
        micro={"oi_change_pct": 1.2, "spread_pct": 0.02, "depth_ratio": 1.1, "est_slippage_pct": 0.03,
               "data_freshness_s": 12.0, "liquidity_ok": True, "basis_pct": 0.05},
        decision={"consensus_score": strength * sgn, "consensus_conf": 0.5 + 0.3 * abs(strength),
                  "n_dissent": 1 if strength > 0 else 4, "n_vetoes": 0,
                  "head_confidence": 0.5 + 0.3 * abs(strength), "risk_allowed": True,
                  "adx": 20.0 + 20.0 * strength, "trend_strength": strength},
        plan={"setup_type": "pullback", "expected_r": 1.2 + strength, "p_win": 0.54, "expected_cost_pct": 0.18,
              "entry": px, "stop": px * (0.97 if side.upper() == "LONG" else 1.03),
              "targets": [px * (1.05 if side.upper() == "LONG" else 0.95),
                          px * (1.09 if side.upper() == "LONG" else 0.91)],
              "rr": 2.1, "leverage": 1, "notional": 15.0, "margin": 15.0},
        portfolio={"btc_regime": "NEUTRAL", "breadth": 0.5, "risk_on": True, "cluster_exposure": 0.2,
                   "direction": sgn, "notional": 30.0, "open_risk_pct": 2.0, "drawdown_pct": 1.5,
                   "pnl_today_r": 0.1, "pnl_week_r": -0.2, "long_exposure": 15.0, "short_exposure": 15.0},
        pattern={"n": 40, "p_win": 0.52, "expectancy_r": 0.1, "profit_factor": 1.05, "ci_low": -0.05,
                 "distance": 0.4, "fallback_level": 1},
        agents={a: {"bias": strength * sgn, "confidence": 0.5 + 0.3 * abs(strength)} for a in
                ("trend", "momentum", "volatility", "volume_flow", "liquidity", "derivatives")},
        run_id="fixture", seed=seed, strict=True)
    return snap.to_dict()


def sparse_features() -> dict:
    """Core-4 öncesi hafızanın şekli: pratikte yalnız expected_r/p_win dolu."""
    return {"expected_r": 1.97, "p_win": 0.5, "leverage": 1}
