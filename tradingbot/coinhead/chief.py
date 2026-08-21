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
    """SABIT ISLEM SAYISI KOTASI YOKTUR.

    Eski `max_new_positions_per_run = 2`, gunluk degil TUR BASINA sert bir tavandi ve kullanicinin
    gordugu "2 islem" davranisinin gercek kaynagiydi. Kaldirildi. Ayni yon / ayni kume yigilmasi ve
    RISK-ON/RISK-OFF yon uyumsuzlugu artik SERT VETO degil, boyut kucultuculeridir. Yeni girisi
    yalnizca GERCEK risk kapasitesi (toplam acik risk / margin) durdurur -> `RISK_CAPACITY_BLOCKED`.
    """
    # Raporlama sozlesmesi: bu alanlar HER ZAMAN None'dir ve kapi olarak KULLANILMAZ.
    max_new_positions_per_run: None = None
    daily_trade_cap: None = None
    # Yigilma ESIKLERI: asildiginda ceza uygulanir, veto verilmez.
    crowded_same_direction_at: int = 3
    crowded_same_cluster_at: int = 2
    crowding_penalty_r: float = 0.08
    regime_mismatch_penalty_r: float = 0.10
    regime_confidence_pref: float = 0.6
    dissent_penalty: float = 0.15
    prefer_spot_when_funding_pct_above: float = 0.03
    # Gercek risk butcesi (motor risk profilinden doldurur).
    max_total_open_risk_pct: float = 6.0
    risk_per_trade_pct: float = 2.0


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
        # SIRALAMA: adaylarin TAMAMI islenmeden once maliyet-sonrasi muhafazakar edge'e gore siralanir.
        # Boylece daha guclu ucuncu firsat, daha zayif iki firsat yuzunden keyfi bicimde disarida kalmaz.
        ranking = []
        for d in decisions:
            opp = getattr(d, "opportunity", None) or {}
            edge = opp.get("conservative_net_edge_r")
            if edge is None:                       # degerlendirme yoksa eski yaklasim (geriye uyum)
                edge = (d.expected_r * d.confidence_calibrated - cfg.dissent_penalty * len(d.dissent)) if d.is_actionable else -1.0
            ranking.append({"symbol": d.symbol, "verdict": d.verdict.value, "direction": d.direction, "market_type": d.market_type,
                            "expected_r": d.expected_r, "confidence": d.confidence_calibrated, "p_win": d.p_win, "dissent": list(d.dissent),
                            "vetoes": list(d.vetoes), "score": round(float(edge), 6), "no_trade_reason": d.no_trade_reason,
                            "conservative_net_edge_r": opp.get("conservative_net_edge_r"),
                            "opportunity_score": opp.get("opportunity_score"),
                            "risk_pct_requested": opp.get("risk_pct_requested"),
                            "size_multiplier": opp.get("size_multiplier"),
                            "cluster": cluster_of(d.symbol, self.clusters)})
        ranking.sort(key=lambda r: (-r["score"], r["symbol"], r["direction"]))     # deterministik
        # izinler + çakışmalar
        conflicts: list[str] = []
        permission: dict[str, dict[str, Any]] = {}
        granted = 0
        dir_count = {"LONG": sum(1 for p in open_pos if p.get("side") == "LONG"), "SHORT": sum(1 for p in open_pos if p.get("side") == "SHORT")}
        cl_count: dict[tuple[str, str], int] = {}
        for p in open_pos:
            k = (cluster_of(p.get("symbol", ""), self.clusters), p.get("side", "LONG"))
            cl_count[k] = cl_count.get(k, 0) + 1
        equity = max(float(ps.get("equity", 0) or 0), 1e-9)
        risk_used = float(ps.get("total_open_risk_usdt", 0.0) or 0.0)
        risk_budget = equity * float(cfg.max_total_open_risk_pct) / 100.0
        for r in ranking:
            sym, dr = r["symbol"], r["direction"]
            if r["verdict"] not in ("SPOT_LONG", "FUTURES_LONG", "FUTURES_SHORT"):
                permission[sym] = {"allow": False, "reason": r["no_trade_reason"] or r["verdict"],
                                   "block_code": "NOT_ACTIONABLE", "requires": [], "size_penalty_r": 0.0,
                                   "soft_codes": []}
                continue
            # --- SERT: yalnizca gercek guvenlik/kapasite ---
            if r["vetoes"]:
                permission[sym] = {"allow": False, "reason": "red team veto", "block_code": "RED_TEAM_HARD_VETO",
                                   "requires": [], "size_penalty_r": 0.0, "soft_codes": []}
                continue
            # --- YUMUSAK: yigilma ve rejim uyumsuzlugu boyutu KUCULTUR, veto VERMEZ ---
            penalty, soft_codes = 0.0, []
            if dir_count.get(dr, 0) >= cfg.crowded_same_direction_at:
                penalty += cfg.crowding_penalty_r
                soft_codes.append("SAME_DIRECTION_CROWDED")
                conflicts.append(f"{sym}: aynı yönde yığılma ({dr} {dir_count[dr]}) → boyut küçültüldü")
            if cl_count.get((r["cluster"], dr), 0) >= cfg.crowded_same_cluster_at:
                penalty += cfg.crowding_penalty_r
                soft_codes.append("CLUSTER_CROWDED")
                conflicts.append(f"{sym}: küme kalabalık ({r['cluster']} {dr}) → boyut küçültüldü")
            if ((mode == "RISK-OFF" and dr == "LONG") or (mode == "RISK-ON" and dr == "SHORT")) \
                    and r["confidence"] < cfg.regime_confidence_pref:
                penalty += cfg.regime_mismatch_penalty_r
                soft_codes.append("MARKET_REGIME_MISMATCH")
            # --- SERT: GERCEK risk kapasitesi (KOTA DEGIL) ---
            req_pct = r.get("risk_pct_requested")
            req = equity * float(req_pct if req_pct is not None else cfg.risk_per_trade_pct) / 100.0
            if risk_used + req > risk_budget + 1e-9:
                permission[sym] = {"allow": False,
                                   "reason": f"portföy risk kapasitesi doldu ({risk_used:.4f}+{req:.4f} > {risk_budget:.4f})",
                                   "block_code": "RISK_CAPACITY_BLOCKED", "requires": [],
                                   "size_penalty_r": round(penalty, 6), "soft_codes": soft_codes}
                continue
            risk_used += req
            granted += 1
            dir_count[dr] = dir_count.get(dr, 0) + 1
            cl_count[(r["cluster"], dr)] = cl_count.get((r["cluster"], dr), 0) + 1
            permission[sym] = {"allow": True, "reason": "chief onayı (nihai değil)",
                               "block_code": None, "size_penalty_r": round(penalty, 6),
                               "soft_codes": soft_codes,
                               "requires": ["coin_head_valid", "no_red_team_veto", "risk_engine_allowed"]}
        priority = [r["symbol"] for r in ranking if permission.get(r["symbol"], {}).get("allow")]
        rules = [f"Piyasa modu {mode}: " + {"RISK-ON": "long planlarına öncelik, short'larda güven ≥ 0.6", "RISK-OFF": "short planlarına öncelik, long'larda güven ≥ 0.6",
                                          "NÖTR": "her iki yönde de yalnız güçlü konsensüs, küçük boyut"}[mode],
                 "Sabit işlem sayısı kotası YOK (tur başına / günlük): yeni girişi yalnız GERÇEK risk "
                 f"kapasitesi durdurur (toplam açık risk ≤ %{cfg.max_total_open_risk_pct}). "
                 f"Aynı yönde ≥{cfg.crowded_same_direction_at} veya aynı kümede ≥{cfg.crowded_same_cluster_at} "
                 "pozisyon: boyut küçültülür, işlem reddedilmez",
                 "Nihai onay = Coin Head geçerli plan + Red Team veto yok + Global Risk Engine izni (LLM tek başına yetersiz)",
                 "Aynı coin için spot long + futures long toplam net exposure'a dahil; spot long ↔ futures short çakışması yasak",
                 f"Funding > %{cfg.prefer_spot_when_funding_pct_above} iken long için spot tercih edilir"]
        exposure |= {"risk_budget_usdt": round(risk_budget, 6), "risk_used_after_usdt": round(risk_used, 6),
                     "risk_capacity_left_usdt": round(max(0.0, risk_budget - risk_used), 6),
                     "granted_this_run": granted,
                     "daily_trade_cap": None, "per_run_trade_cap": None}
        return ChiefDecision(generated_at=iso(utc_now()), market_risk_mode=mode, btc_eth_regime={"btc": btc_r, "eth": eth_regime or "UNKNOWN"},
                             breadth=breadth, clusters=clusters, allocation=allocation, exposure=exposure, ranking=ranking, priority=priority,
                             conflicts=conflicts, permission=permission, rules=rules)
