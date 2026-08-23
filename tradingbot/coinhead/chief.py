"""BAŞ YÖNETİCİ (Chief Portfolio Manager) — Coin Head kararlarını portföy bağlamında SIRALAR ve AÇIKLAR.

GÖREVİ ÜÇ ŞEYLE SINIRLIDIR: (1) sıralama, (2) açıklama, (3) üst sınırlı yığılma/rejim yumuşak cezası.

CHIEF AUTHORITATIVE RİSK REZERVASYONU YAPMAZ.
Eski davranış hatalıydı: `decide()` sıralamadaki her adayı gezerken `risk_used` değerini HEMEN
artırıyordu. Oysa tetik (trigger), duplicate, araştırma politikası ve gerçek emir/ledger kabulü
DAHA SONRA `engine_v3.py` içinde kontrol ediliyordu. Sonuç: tetiklenmeyen ya da duplicate olan en
güçlü aday kapasiteyi tüketiyor, gerçekten tetiklenen sonraki aday `RISK_CAPACITY_BLOCKED` alıyordu.

Yeni sözleşme:
  * Chief `permission[sym]["allow"]` yalnız (a) aday işlenebilir mi ve (b) SERT red-team vetosu var mı
    sorularını yanıtlar.
  * `capacity_projection` YALNIZ RAPORLAMA amaçlıdır (`advisory: True`). Hiçbir adayı engellemez.
  * Risk kapasitesi YETKİLİ olarak `RiskEngine.evaluate()` içinde, NİHAİ boyut/risk üzerinden ve
    yalnız gerçekten açılmış pozisyonların riskine karşı zorlanır. Risk yalnız başarılı açılıştan
    sonra tüketilir; açılış reddedilirse rezervasyon diye bir şey olmadığı için serbest kalır.

Final onay üç bayrak gerektirir:
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
    yalnizca GERCEK risk kapasitesi (toplam acik risk / margin) durdurur; bu kapi CHIEF'te DEGIL,
    `RiskEngine.evaluate()` icinde NIHAI boyut uzerinden zorlanir.
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
                      "futures_notional": round(sum(float(p.get("notional", 0)) for p in open_pos if p.get("market_type") != "SPOT"), 4),
                      # UC AYRI KAVRAM — spot notional ile futures stop riski TOPLANMAZ
                      "futures_stop_risk_usdt": ps.get("futures_stop_risk_usdt"),
                      "spot_exposure_usdt": ps.get("spot_exposure_usdt")}
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
        # izinler + çakışmalar — SIRALAMA/ACIKLAMA/YUMUSAK CEZA. RISK REZERVASYONU YOK.
        conflicts: list[str] = []
        permission: dict[str, dict[str, Any]] = {}
        permitted = 0
        # Yigilma sayaclari YALNIZ GERCEK acik pozisyonlardan gelir. Sirasi gelmis fakat henuz
        # tetiklenmemis adaylar sayaci ARTIRMAZ; aksi halde hic acilmayacak bir aday, sonraki
        # gercek adayin boyutunu kucultur (risk rezervasyonuyla ayni sinifta hayalet etki).
        dir_count = {"LONG": sum(1 for p in open_pos if p.get("side") == "LONG"), "SHORT": sum(1 for p in open_pos if p.get("side") == "SHORT")}
        cl_count: dict[tuple[str, str], int] = {}
        for p in open_pos:
            k = (cluster_of(p.get("symbol", ""), self.clusters), p.get("side", "LONG"))
            cl_count[k] = cl_count.get(k, 0) + 1
        equity = max(float(ps.get("equity", 0) or 0), 1e-9)
        # YETKILI kapiyla AYNI kova: futures stop riski. Spot notional bu projeksiyona KARISMAZ
        # (kendi `SPOT_ALLOCATION` kapisi vardir). Alan yoksa eski birlesik toplam kullanilir.
        _fut = ps.get("futures_stop_risk_usdt")
        risk_open_now = float((_fut if _fut is not None else ps.get("total_open_risk_usdt", 0.0)) or 0.0)
        risk_budget = equity * float(cfg.max_total_open_risk_pct) / 100.0
        projected = risk_open_now          # YALNIZ RAPORLAMA: hicbir adayi engellemez
        advisory_fit = 0
        for r in ranking:
            sym, dr = r["symbol"], r["direction"]
            if r["verdict"] not in ("SPOT_LONG", "FUTURES_LONG", "FUTURES_SHORT"):
                permission[sym] = {"allow": False, "reason": r["no_trade_reason"] or r["verdict"],
                                   "block_code": "NOT_ACTIONABLE", "requires": [], "size_penalty_r": 0.0,
                                   "soft_codes": [], "capacity_projection": None}
                continue
            # --- SERT: yalnizca GERCEK red-team sert vetosu (ekonomik zayifliklar burada DEGIL) ---
            if r["vetoes"]:
                permission[sym] = {"allow": False, "reason": "red team hard veto", "block_code": "RED_TEAM_HARD_VETO",
                                   "requires": [], "size_penalty_r": 0.0, "soft_codes": [],
                                   "capacity_projection": None}
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
            # --- ADVISORY kapasite projeksiyonu: KAPI DEGIL, yalnizca raporlama ---
            req_pct = r.get("risk_pct_requested")
            req = equity * float(req_pct if req_pct is not None else cfg.risk_per_trade_pct) / 100.0
            fits = (projected + req) <= risk_budget + 1e-9
            if fits:
                projected += req
                advisory_fit += 1
            permitted += 1
            permission[sym] = {"allow": True, "reason": "chief sıralaması (NİHAİ DEĞİL)",
                               "block_code": None, "size_penalty_r": round(penalty, 6),
                               "soft_codes": soft_codes,
                               # ADVISORY: yetkili kapasite kontrolu RiskEngine'de NIHAI boyutla yapilir.
                               "capacity_projection": {"advisory": True, "would_fit": bool(fits),
                                                       "requested_risk_usdt": round(req, 6),
                                                       "projected_used_usdt": round(projected, 6),
                                                       "budget_usdt": round(risk_budget, 6)},
                               "requires": ["coin_head_valid", "no_red_team_veto", "risk_engine_allowed"]}
        priority = [r["symbol"] for r in ranking if permission.get(r["symbol"], {}).get("allow")]
        rules = [f"Piyasa modu {mode}: " + {"RISK-ON": "long planlarına öncelik, short'larda güven ≥ 0.6", "RISK-OFF": "short planlarına öncelik, long'larda güven ≥ 0.6",
                                          "NÖTR": "her iki yönde de yalnız güçlü konsensüs, küçük boyut"}[mode],
                 "Sabit işlem sayısı kotası YOK (tur başına / günlük): yeni girişi yalnız GERÇEK risk "
                 f"kapasitesi durdurur (toplam açık risk ≤ %{cfg.max_total_open_risk_pct}) ve bu kapı "
                 "CHIEF'te değil, nihai boyut üzerinden Global Risk Engine'de zorlanır. Chief risk "
                 "REZERVE ETMEZ: tetiklenmeyen/duplicate/reddedilen aday kapasite tüketmez",
                 f"Aynı yönde ≥{cfg.crowded_same_direction_at} veya aynı kümede ≥{cfg.crowded_same_cluster_at} "
                 "AÇIK pozisyon: boyut küçültülür, işlem reddedilmez",
                 "Nihai onay = Coin Head geçerli plan + Red Team veto yok + Global Risk Engine izni (LLM tek başına yetersiz)",
                 "Aynı coin için spot long + futures long toplam net exposure'a dahil; spot long ↔ futures short çakışması yasak",
                 f"Funding > %{cfg.prefer_spot_when_funding_pct_above} iken long için spot tercih edilir"]
        exposure |= {"risk_budget_usdt": round(risk_budget, 6),
                     # GERCEK acik risk (rezervasyon DEGIL) ve ondan kalan gercek kapasite.
                     "risk_used_usdt": round(risk_open_now, 6),
                     "risk_capacity_left_usdt": round(max(0.0, risk_budget - risk_open_now), 6),
                     # ADVISORY projeksiyon: hicbir adayi engellemedi, yalnizca raporlanir.
                     "risk_projected_if_all_open_usdt": round(projected, 6),
                     "advisory_capacity_fit": advisory_fit,
                     "authoritative_risk_reservation": False,
                     "ranked": permitted, "granted_this_run": permitted,
                     "daily_trade_cap": None, "per_run_trade_cap": None}
        return ChiefDecision(generated_at=iso(utc_now()), market_risk_mode=mode, btc_eth_regime={"btc": btc_r, "eth": eth_regime or "UNKNOWN"},
                             breadth=breadth, clusters=clusters, allocation=allocation, exposure=exposure, ranking=ranking, priority=priority,
                             conflicts=conflicts, permission=permission, rules=rules)
