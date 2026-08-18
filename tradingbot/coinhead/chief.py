"""BAŞ YÖNETİCİ (Chief Portfolio Manager) — Coin Head kararlarını portföy bağlamında birleştirir ve önceliklendirir.

Tek başına risk limitlerini geçemez. Final onay üç bayrak gerektirir:
  1) coin_head_valid  2) no_red_team_veto  3) risk_engine_allowed  (LLM onayı tek başına yeterli DEĞİLDİR)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..core import iso, utc_now
from ..risk.state import cluster_of
from .schema import CoinHeadDecision, Verdict


@dataclass
class ChiefConfig:
    max_new_positions_per_run: int = 2
    max_same_direction: int = 3
    max_same_cluster_same_direction: int = 2
    dissent_penalty: float = 0.15
    prefer_spot_when_funding_pct_above: float = 0.03


@dataclass
class ChiefDecision:
    generated_at: str
    market_risk_mode: str                       # RISK-ON | NÖTR | RISK-OFF
    btc_eth_regime: dict[str, str]
    breadth: dict[str, int]                     # {long, short, no_trade, data_invalid}
    clusters: dict[str, list[str]]
    allocation: dict[str, float]                # {spot_notional, futures_notional}
    exposure: dict[str, float]                  # {long_notional, short_notional, net_beta_est, open_risk_usdt, margin_util_pct, daily_pnl, drawdown_pct}
    ranking: list[dict[str, Any]]
    priority: list[str]
    conflicts: list[str]
    permission: dict[str, dict[str, Any]]       # symbol → {allow, reason, requires: [...]}
    rules: list[str]
    approval_flags_required: tuple[str, str, str] = ("coin_head_valid", "no_red_team_veto", "risk_engine_allowed")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["approval_flags_required"] = list(self.approval_flags_required)
        return d


class ChiefPortfolioManager:
    def __init__(self, cfg: ChiefConfig | None = None, clusters: dict[str, str] | None = None):
        self.cfg = cfg or ChiefConfig()
        self.clusters = clusters

    def decide(self, decisions: list[CoinHeadDecision], portfolio_state: dict[str, Any] | None = None,
               btc_regime: str | None = None, eth_regime: str | None = None) -> ChiefDecision:
        ps = portfolio_state or {}
        cfg = self.cfg
        acts = [d for d in decisions if d.is_actionable]
        longs = [d for d in acts if d.direction == "LONG"]
        shorts = [d for d in acts if d.direction == "SHORT"]
        btc = next((d for d in decisions if d.symbol.startswith("BTC/")), None)
        btc_r = btc_regime or (btc.regime if btc else "UNKNOWN")
        if btc_r in ("TREND_UP", "EUPHORIC", "BREAKOUT") and len(longs) >= len(shorts):
            mode = "RISK-ON"
        elif btc_r in ("TREND_DOWN", "PANIC") and len(shorts) >= len(longs):
            mode = "RISK-OFF"
        else:
            mode = "NÖTR"
        breadth = {"long": len(longs), "short": len(shorts),
                   "no_trade": sum(1 for d in decisions if d.verdict == Verdict.NO_TRADE),
                   "data_invalid": sum(1 for d in decisions if d.verdict == Verdict.DATA_INVALID),
                   "hold": sum(1 for d in decisions if d.verdict in (Verdict.HOLD, Verdict.REDUCE, Verdict.EXIT))}
        clusters: dict[str, list[str]] = {}
        for d in acts:
            clusters.setdefault(cluster_of(d.symbol, self.clusters), []).append(f"{d.symbol}:{d.direction}")
        open_pos = ps.get("open_positions", []) or []
        long_n = sum(float(p.get("notional", 0)) for p in open_pos if p.get("side") == "LONG")
        short_n = sum(float(p.get("notional", 0)) for p in open_pos if p.get("side") == "SHORT")
        exposure = {"long_notional": round(long_n, 4), "short_notional": round(short_n, 4),
                    "net_beta_est": round((long_n - short_n) / max(float(ps.get("equity", 1)) or 1, 1e-9), 3),
                    "open_risk_usdt": float(ps.get("total_open_risk_usdt", 0.0)), "margin_util_pct": float(ps.get("margin_util_pct", 0.0)),
                    "daily_pnl": float(ps.get("pnl_today", 0.0)), "drawdown_pct": float(ps.get("drawdown_pct", 0.0))}
        allocation = {"spot_notional": round(sum(float(p.get("notional", 0)) for p in open_pos if p.get("market_type") == "SPOT"), 4),
                      "futures_notional": round(sum(float(p.get("notional", 0)) for p in open_pos if p.get("market_type") != "SPOT"), 4)}
        # sıralama: beklenen R × güven − dissent cezası
        ranking = []
        for d in decisions:
            sc = (d.expected_r * d.confidence_calibrated - cfg.dissent_penalty * len(d.dissent)) if d.is_actionable else -1.0
            ranking.append({"symbol": d.symbol, "verdict": d.verdict.value, "direction": d.direction, "market_type": d.market_type,
                            "expected_r": d.expected_r, "confidence": d.confidence_calibrated, "p_win": d.p_win, "dissent": list(d.dissent),
                            "vetoes": list(d.vetoes), "score": round(sc, 4), "no_trade_reason": d.no_trade_reason,
                            "cluster": cluster_of(d.symbol, self.clusters)})
        ranking.sort(key=lambda r: r["score"], reverse=True)
        # izinler + çakışmalar
        conflicts: list[str] = []
        permission: dict[str, dict[str, Any]] = {}
        granted = 0
        dir_count = {"LONG": sum(1 for p in open_pos if p.get("side") == "LONG"), "SHORT": sum(1 for p in open_pos if p.get("side") == "SHORT")}
        cl_count: dict[tuple[str, str], int] = {}
        for p in open_pos:
            k = (cluster_of(p.get("symbol", ""), self.clusters), p.get("side", "LONG"))
            cl_count[k] = cl_count.get(k, 0) + 1
        for r in ranking:
            sym, dr = r["symbol"], r["direction"]
            if r["verdict"] not in ("SPOT_LONG", "FUTURES_LONG", "FUTURES_SHORT"):
                permission[sym] = {"allow": False, "reason": r["no_trade_reason"] or r["verdict"], "requires": []}
                continue
            reason = ""
            if r["vetoes"]:
                reason = "red team veto"
            elif granted >= cfg.max_new_positions_per_run:
                reason = f"tur başına yeni pozisyon limiti ({cfg.max_new_positions_per_run})"
            elif dir_count.get(dr, 0) >= cfg.max_same_direction:
                reason = f"aynı yönde yığılma ({dr} {dir_count[dr]})"
                conflicts.append(f"{sym}: {reason}")
            elif cl_count.get((r["cluster"], dr), 0) >= cfg.max_same_cluster_same_direction:
                reason = f"küme kalabalık ({r['cluster']} {dr})"
                conflicts.append(f"{sym}: {reason}")
            elif mode == "RISK-OFF" and dr == "LONG" and r["confidence"] < 0.6:
                reason = "RISK-OFF modunda zayıf long"
            elif mode == "RISK-ON" and dr == "SHORT" and r["confidence"] < 0.6:
                reason = "RISK-ON modunda zayıf short"
            if reason:
                permission[sym] = {"allow": False, "reason": reason, "requires": []}
                continue
            granted += 1
            dir_count[dr] = dir_count.get(dr, 0) + 1
            cl_count[(r["cluster"], dr)] = cl_count.get((r["cluster"], dr), 0) + 1
            permission[sym] = {"allow": True, "reason": "chief onayı (nihai değil)", "requires": ["coin_head_valid", "no_red_team_veto", "risk_engine_allowed"]}
        priority = [r["symbol"] for r in ranking if permission.get(r["symbol"], {}).get("allow")]
        rules = [f"Piyasa modu {mode}: " + {"RISK-ON": "long planlarına öncelik, short'larda güven ≥ 0.6", "RISK-OFF": "short planlarına öncelik, long'larda güven ≥ 0.6",
                                          "NÖTR": "her iki yönde de yalnız güçlü konsensüs, küçük boyut"}[mode],
                 f"Tur başına en fazla {cfg.max_new_positions_per_run} yeni pozisyon; aynı yönde ≤ {cfg.max_same_direction}; aynı kümede aynı yönde ≤ {cfg.max_same_cluster_same_direction}",
                 "Nihai onay = Coin Head geçerli plan + Red Team veto yok + Global Risk Engine izni (LLM tek başına yetersiz)",
                 "Aynı coin için spot long + futures long toplam net exposure'a dahil; spot long ↔ futures short çakışması yasak",
                 f"Funding > %{cfg.prefer_spot_when_funding_pct_above} iken long için spot tercih edilir"]
        return ChiefDecision(generated_at=iso(utc_now()), market_risk_mode=mode, btc_eth_regime={"btc": btc_r, "eth": eth_regime or "UNKNOWN"},
                             breadth=breadth, clusters=clusters, allocation=allocation, exposure=exposure, ranking=ranking, priority=priority,
                             conflicts=conflicts, permission=permission, rules=rules)
