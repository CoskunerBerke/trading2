"""Coin Yöneticisi ajanı + Portföy Baş Yöneticisi.

CoinManager: uzman ajan raporlarını ağırlıklı birleştirir → yön (LONG / SHORT / BEKLE), kanaat (0-100),
futures planı (giriş koşulları, stop, hedefler, R/R, max kaldıraç, marj), YAP / YAPMA listesi ve
"EĞER … İSE …" koşullu kuralları üretir.

Chief: tüm coin özetlerini alır, BTC rejimine göre risk modunu belirler, coinleri kanaate göre sıralar.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .base import AgentReport, CoinContext, fmt_px, pct


@dataclass
class TradePlan:
    direction: str = "BEKLE"          # LONG / SHORT / BEKLE
    entry_type: str = ""              # "kırılım" / "geri çekilme"
    entry: float | None = None
    trigger_text: str = ""
    stop: float | None = None
    stop_pct: float = 0.0
    target1: float | None = None
    target2: float | None = None
    rr: float = 0.0
    max_leverage: int = 1
    suggested_leverage: int = 1
    margin_usdt: float = 0.0
    notional_usdt: float = 0.0
    risk_usdt: float = 0.0
    valid: bool = False
    invalid_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CoinBrief:
    symbol: str
    price: float
    verdict: str                      # LONG / SHORT / BEKLE
    score: float                      # -1..1
    conviction: int                   # 0-100
    headline: str
    reports: list[AgentReport]
    plan: TradePlan
    do_list: list[str] = field(default_factory=list)
    dont_list: list[str] = field(default_factory=list)
    if_then: list[str] = field(default_factory=list)
    key_levels: dict[str, float] = field(default_factory=dict)
    generated_at: str = ""
    last_close_4h: float = 0.0
    last_bar_4h: str = ""
    scan_score: float = 0.0
    scan_direction: str = ""
    p_win: float | None = None            # öğrenen modelin tahmini
    chart: str = ""                       # Obsidian görsel yolu

    @property
    def base(self) -> str:
        return self.symbol.split("/")[0]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["reports"] = [r.to_dict() for r in self.reports]
        d["plan"] = self.plan.to_dict()
        return d


class CoinManagerAgent:
    LONG_T, SHORT_T = 0.22, -0.22

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = weights or {}

    def decide(self, ctx: CoinContext, reports: list[AgentReport]) -> CoinBrief:
        price = ctx.price
        rep = {r.agent: r for r in reports}
        num, den = 0.0, 0.0
        for r in reports:
            w = self.weights.get(r.agent, getattr(r, "weight", None) or _default_weight(r.agent))
            if w <= 0 or r.error:
                continue
            num += w * r.bias * (0.5 + 0.5 * r.confidence / 100.0)
            den += w
        score = num / den if den else 0.0
        verdict = "LONG" if score >= self.LONG_T else ("SHORT" if score <= self.SHORT_T else "BEKLE")
        conviction = int(min(100, abs(score) / 0.6 * 100))
        agree = sum(1 for r in reports if (r.bias > 0.15) == (score > 0) and abs(r.bias) > 0.15 and not r.error)
        n_dir = sum(1 for r in reports if abs(r.bias) > 0.15 and not r.error)

        levels = {}
        for r in reports:
            levels.update(r.levels)
        lvrep = rep.get("levels")
        self._lv_lists = {"resistances": (lvrep.metrics.get("resistances") if lvrep else []) or [],
                          "supports": (lvrep.metrics.get("supports") if lvrep else []) or []}
        vol = rep.get("volatility")
        max_lev = int((vol.metrics.get("max_leverage") if vol else 2) or 2)
        atr4 = None
        h4 = ctx.frame("4h")
        if h4 is not None:
            atr4 = float(h4["atr14"].iloc[-1])
        stop_dist = (atr4 * ctx.atr_stop_mult) if atr4 else price * (vol.metrics.get("stop_pct", 5) / 100 if vol else 0.05)

        plan = self._plan(verdict, price, levels, stop_dist, max_lev, ctx, conviction)
        do, dont, ifthen = self._rules(verdict, score, reports, levels, plan, price, ctx)

        head = self._headline(verdict, conviction, agree, n_dir, rep, price)
        key = {k: v for k, v in levels.items() if k in ("r1", "r2", "s1", "s2", "ema_support", "ema_resistance", "ema20_1d", "ema50_1d", "ema200_1d", "high_24h", "low_24h", "strategy_stop")}
        return CoinBrief(symbol=ctx.symbol, price=price, verdict=verdict, score=round(score, 3), conviction=conviction,
                         headline=head, reports=reports, plan=plan, do_list=do, dont_list=dont, if_then=ifthen, key_levels=key)

    # ------------------------------------------------------------------ plan
    def _plan(self, verdict: str, price: float, lv: dict, stop_dist: float, max_lev: int, ctx: CoinContext, conviction: int) -> TradePlan:
        p = TradePlan(direction=verdict, max_leverage=max_lev)
        if verdict == "BEKLE" or price <= 0 or stop_dist <= 0:
            p.invalid_reason = "Yön yok → plan yok, bekle"
            return p
        r1, r2, s1, s2 = lv.get("r1"), lv.get("r2"), lv.get("s1"), lv.get("s2")
        ress = list(self._lv_lists.get("resistances", [])) if hasattr(self, "_lv_lists") else []
        sups = list(self._lv_lists.get("supports", [])) if hasattr(self, "_lv_lists") else []

        def _targets(entry: float, risk: float, up: bool):
            """riskin ≥1.5 katı uzaklıktaki ilk seviye hedef1; sonraki hedef2; yoksa 2R / 3R projeksiyon."""
            cands = [x for x in (ress if up else sups) if (x > entry if up else x < entry)]
            cands = sorted(cands, reverse=not up)
            far = [x for x in cands if abs(x - entry) >= 1.5 * risk]
            t1 = far[0] if far else (entry + 2 * risk if up else entry - 2 * risk)
            after = [x for x in far if (x > t1 if up else x < t1)]
            t2 = after[0] if after else (entry + 3 * risk if up else entry - 3 * risk)
            return t1, t2

        if verdict == "LONG":
            # tercih: dirence uzaksa kırılım değil, geri çekilme; direnç yakınsa kırılım kapanışı bekle
            if r1 and pct(r1, price) < 1.5:
                p.entry_type, p.entry = "kırılım", r1 * 1.001
                p.trigger_text = f"4h mum {fmt_px(r1)} üstünde kapanırsa long"
                p.stop = min(price - stop_dist, (s1 if s1 else price - stop_dist))
                p.stop = max(p.stop, p.entry - stop_dist * 1.2)
                p.target1, p.target2 = _targets(p.entry, p.entry - p.stop, True)
            else:
                lvl = max([x for x in (s1, lv.get("ema_support"), lv.get("ema20_4h")) if x and x < price] or [price - stop_dist * 0.5])
                p.entry_type, p.entry = "geri çekilme", lvl
                p.trigger_text = f"{fmt_px(lvl)} desteğine geri çekilmede alıcı mumuyla long (kovalama)"
                p.stop = lvl - stop_dist
                p.target1, p.target2 = _targets(p.entry, p.entry - p.stop, True)
        else:  # SHORT
            if s1 and pct(price, s1) < 1.5:
                p.entry_type, p.entry = "kırılım", s1 * 0.999
                p.trigger_text = f"4h mum {fmt_px(s1)} altında kapanırsa short"
                p.stop = max(price + stop_dist, (r1 if r1 else price + stop_dist))
                p.stop = min(p.stop, p.entry + stop_dist * 1.2)
                p.target1, p.target2 = _targets(p.entry, p.stop - p.entry, False)
            else:
                lvl = min([x for x in (r1, lv.get("ema_resistance"), lv.get("ema20_4h")) if x and x > price] or [price + stop_dist * 0.5])
                p.entry_type, p.entry = "geri çekilme", lvl
                p.trigger_text = f"{fmt_px(lvl)} direncine tepkide satıcı mumuyla short (kovalama)"
                p.stop = lvl + stop_dist
                p.target1, p.target2 = _targets(p.entry, p.stop - p.entry, False)
        risk_per_unit = abs(p.entry - p.stop)
        reward = abs(p.target1 - p.entry)
        p.rr = round(reward / risk_per_unit, 2) if risk_per_unit else 0.0
        p.stop_pct = round(risk_per_unit / p.entry * 100, 2)
        # boyut: 50 USDT hesapta risk %2 → 1 USDT; notional = risk / stop%
        risk_usdt = ctx.equity_usdt * ctx.risk_pct / 100.0
        notional = risk_usdt / (p.stop_pct / 100.0) if p.stop_pct else 0.0
        notional = min(notional, ctx.equity_usdt * max_lev * 0.9)
        lev = max(1, min(max_lev, int(round(notional / max(ctx.equity_usdt * 0.30, 1e-9)))))
        p.suggested_leverage = lev if conviction >= 60 else max(1, min(lev, 2))
        p.notional_usdt = round(min(notional, ctx.equity_usdt * 0.30 * p.suggested_leverage), 2)
        p.margin_usdt = round(p.notional_usdt / p.suggested_leverage, 2)
        p.risk_usdt = round(p.notional_usdt * p.stop_pct / 100.0, 2)
        if p.rr < 1.5:
            p.invalid_reason = f"Risk/ödül {p.rr} < 1.5 → plan geçersiz, daha iyi seviye bekle"
        elif p.notional_usdt < 5:
            p.invalid_reason = "Binance min emir (5 USDT) sağlanamıyor"
        else:
            p.valid = True
        return p

    # ------------------------------------------------------------------ kurallar
    def _rules(self, verdict, score, reports, lv, plan, price, ctx):
        do, dont, ifthen = [], [], []
        for r in reports:
            for w in r.warnings:
                if w not in dont:
                    dont.append(w)
        rep = {r.agent: r for r in reports}
        r1, s1, r2, s2 = lv.get("r1"), lv.get("s1"), lv.get("r2"), lv.get("s2")
        if verdict == "LONG":
            do.append(f"Yön LONG (kanaat %{int(min(100, abs(score)/0.6*100))}). {plan.trigger_text}." if plan.trigger_text else "Yön LONG.")
            do.append(f"Stop {fmt_px(plan.stop)} (−%{plan.stop_pct}); hedef1 {fmt_px(plan.target1)}, hedef2 {fmt_px(plan.target2)}; R/R {plan.rr}" if plan.stop else "")
            do.append(f"Kaldıraç ≤ {plan.max_leverage}x (öneri {plan.suggested_leverage}x), marj ≈ {plan.margin_usdt} USDT, riske atılan ≈ {plan.risk_usdt} USDT")
            dont.append("Piyasa emriyle kovalama; sadece koşul gerçekleşince gir")
            dont.append("Stop'u aşağı taşıma; hedef1'de yarısını kapat, kalanını başa-baş stopla taşı")
            if s1:
                ifthen.append(f"EĞER 4h kapanış {fmt_px(s1)} altına inerse → long fikri iptal, {fmt_px(s2) if s2 else 'aralık altı'} riski")
            if r1:
                ifthen.append(f"EĞER 4h kapanış {fmt_px(r1)} üstüne çıkarsa → hedef {fmt_px(r2) if r2 else 'sonraki direnç'}")
        elif verdict == "SHORT":
            do.append(f"Yön SHORT (kanaat %{int(min(100, abs(score)/0.6*100))}). {plan.trigger_text}." if plan.trigger_text else "Yön SHORT.")
            do.append(f"Stop {fmt_px(plan.stop)} (+%{plan.stop_pct}); hedef1 {fmt_px(plan.target1)}, hedef2 {fmt_px(plan.target2)}; R/R {plan.rr}" if plan.stop else "")
            do.append(f"Kaldıraç ≤ {plan.max_leverage}x (öneri {plan.suggested_leverage}x), marj ≈ {plan.margin_usdt} USDT, riske atılan ≈ {plan.risk_usdt} USDT")
            dont.append("Dipten short kovalama; sadece koşul gerçekleşince gir")
            dont.append("Short'ta funding pozitifse taşıma maliyeti lehine; negatifse squeeze riski — pozisyonu uzun tutma")
            if r1:
                ifthen.append(f"EĞER 4h kapanış {fmt_px(r1)} üstüne çıkarsa → short fikri iptal, {fmt_px(r2) if r2 else 'sonraki direnç'} riski")
            if s1:
                ifthen.append(f"EĞER 4h kapanış {fmt_px(s1)} altına inerse → hedef {fmt_px(s2) if s2 else 'sonraki destek'}")
        else:
            do.append("Yön yok → BEKLE. Pozisyon açma; sadece izle.")
            do.append(f"Kaldıraç kullanma. Yeniden değerlendirme: {'direnç ' + fmt_px(r1) + ' üstünde kapanış (long)' if r1 else ''}{' / ' if r1 and s1 else ''}{'destek ' + fmt_px(s1) + ' altında kapanış (short)' if s1 else ''}")
            if r1:
                ifthen.append(f"EĞER 4h mum {fmt_px(r1)} üstünde hacimle kapanırsa → long senaryosu açılır (hedef {fmt_px(r2) if r2 else 'sonraki direnç'})")
            if s1:
                ifthen.append(f"EĞER 4h mum {fmt_px(s1)} altında hacimle kapanırsa → short senaryosu açılır (hedef {fmt_px(s2) if s2 else 'sonraki destek'})")
        if plan.direction != "BEKLE" and not plan.valid:
            dont.insert(0, f"Şu an GİRME: {plan.invalid_reason}")
        edge = rep.get("edge")
        if edge and edge.metrics.get("has_edge") is False:
            dont.append("Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut")
        vol = rep.get("volatility")
        if vol and vol.metrics.get("regime") == "YÜKSEK":
            do.append("Volatilite yüksek: stop'u geniş, kaldıracı düşük tut")
        do = [x for x in do if x]
        return do, dont, ifthen

    def _headline(self, verdict, conviction, agree, n_dir, rep, price):
        emoji = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "⚪"}[verdict]
        tr = rep.get("trend")
        return f"{emoji} {verdict} · kanaat %{conviction} · {agree}/{n_dir} yönlü ajan hemfikir · fiyat {fmt_px(price)}" + (f" · trend {tr.stance_lower}" if tr else "")


def _default_weight(agent: str) -> float:
    return {"trend": 0.25, "momentum": 0.15, "candles": 0.12, "volume": 0.10, "levels": 0.13, "market": 0.12, "edge": 0.20, "volatility": 0.0}.get(agent, 0.1)


# ---------------------------------------------------------------------- Baş Yönetici
@dataclass
class ChiefBrief:
    risk_mode: str                    # RISK-ON / NÖTR / RISK-OFF
    btc_verdict: str
    headline: str
    ranking: list[dict]               # [{symbol, verdict, conviction, plan_valid}]
    rules: list[str]
    max_concurrent: int
    generated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class ChiefAgent:
    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent

    def decide(self, briefs: list[CoinBrief]) -> ChiefBrief:
        btc = next((b for b in briefs if b.symbol.startswith("BTC/")), None)
        btc_v = btc.verdict if btc else "BİLİNMİYOR"
        longs = sum(1 for b in briefs if b.verdict == "LONG")
        shorts = sum(1 for b in briefs if b.verdict == "SHORT")
        if btc_v == "LONG" and longs >= shorts:
            mode = "RISK-ON"
        elif btc_v == "SHORT" and shorts >= longs:
            mode = "RISK-OFF"
        else:
            mode = "NÖTR"
        ranking = sorted(({"symbol": b.symbol, "verdict": b.verdict, "conviction": b.conviction, "plan_valid": b.plan.valid,
                           "rr": b.plan.rr, "lev": b.plan.suggested_leverage} for b in briefs),
                         key=lambda x: (x["verdict"] != "BEKLE", x["plan_valid"], x["conviction"]), reverse=True)
        rules = [
            f"Piyasa modu {mode}: " + {"RISK-ON": "long planlarına öncelik, short'larda boyut yarım",
                                       "RISK-OFF": "short planlarına öncelik, long'larda boyut yarım ve sadece güçlü kanaat",
                                       "NÖTR": "yalnızca R/R ≥ 2 ve kanaat ≥ 60 olan planlar; her iki yönde de küçük boyut"}[mode],
            f"Aynı anda en fazla {self.max_concurrent} pozisyon; toplam riske atılan sermaye ≤ %6",
            "Altcoinler BTC ile yüksek korelasyonlu: BTC yön değiştirirse tüm alt planlarını yeniden değerlendir",
            "Her plan 4h kapanışına göre tetiklenir; bar içi fitillere göre işlem açma",
        ]
        valid = [r for r in ranking if r["plan_valid"]]
        head = f"{mode} · BTC {btc_v} · {longs} LONG / {shorts} SHORT / {len(briefs)-longs-shorts} BEKLE · geçerli plan: {len(valid)}"
        return ChiefBrief(risk_mode=mode, btc_verdict=btc_v, headline=head, ranking=ranking, rules=rules, max_concurrent=self.max_concurrent)
