# -*- coding: utf-8 -*-
"""M2X POLİTİKASI v1 — agresif M2 kâğıt defterinin boyut ve risk freni (ön kayıt: docs/M2_AGGRESSIVE_V1.md §2).

PAPER. Gerçek para yok. Bu modül TEK KAYNAKTIR: simülasyon (`m2x_sim`) ve ileride M2X defteri aynı fonksiyonları
çağırır. Fonksiyonlar SAFTIR (dosya/ağ/saat yok); durum `PolicyState` nesnesinde taşınır.

* Sayılar `M2X_POLICY_V1`dedir ve `M2X_POLICY_SHA` mührü bir testle sabitlenir (gold_v1 deseni). Değişiklik = yeni
  sürüm (`m2x_v2`) + yeni ön kayıt; config'ten sayı DEĞİŞTİRİLMEZ.
* `Knobs` yalnız simülasyonun ön kayıtlı BİLGİ kolları içindir (§3.4: c, d, e, f, g, h1, h2, i1, i2, j, p). v1 =
  `Knobs()` (varsayılanlar); bilgi kolları v1'i değiştirmez.

Tanımlar (§2.1): E = mark'a göre özkaynak; P = günlük görüntülerde E'nin koşan en büyüğü (gerçek zirve); Pₖ = kademe
zirvesi (B anahtarıyla yeniden demirlenir); DD = 1 − E/P; DDₖ = 1 − E/Pₖ; OR = Σ q·max(0, m − stop);
CL = Σ min(q·(m − liq) + 0,005·q·m, q·m·(X + 0,001)), X = 0,50; kriz bütçesi H_CL = E − 0,5·P − CL;
OR boşluğu H_OR = tavan·E − OR.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Sequence

POLICY_VERSION = "m2x_v1"
POLICY_DOC = "docs/M2_AGGRESSIVE_V1.md"

#: Ön kayıtlı sayılar (§0.2, §2). Sözlük sırası mühür için önemsizdir (sort_keys).
M2X_POLICY_V1: dict[str, Any] = {
    "version": POLICY_VERSION,
    "starting_equity_usdt": 200.0,
    # kademeler: (ad, DDₖ alt sınırı, işlem başı risk %, açık risk tavanı %) — K4 yumuşak durdurma (yeni giriş yok)
    "tiers": [["K1", 0.0, 2.0, 20.0], ["K2", 0.15, 1.0, 12.0], ["K3", 0.25, 0.5, 6.0], ["K4_SOFT_HALT", 0.35, 0.0, 0.0]],
    "halt_dd": 0.50,                    # gerçek zirveden DD ≥ %50 → DUR (yalnız sahip açar)
    "step_up_band": 0.05,               # A: DDₖ ≤ kademenin alt sınırı − 5 puan
    "step_up_streak": 5,                # ardışık geçen günlük görüntü
    "u_lookback": 20,                   # B: ΔU20 = U(bugün) − U(20 görüntü önce) > 0
    "crash_x": 0.50,                    # kriz senaryosu: her pozisyon %50 düşer ya da likide olur
    "crash_exit_cost": 0.001,           # en kötü uçtan dolum ve çıkış maliyeti
    "crash_liq_fee": 0.005,             # likidasyon ücreti (q·m oranı)
    "crash_budget_peak_frac": 0.5,      # H_CL = E − 0,5·P − CL ≥ 0 (giriş sonrası)
    "min_scale": 0.25,                  # ortak küçültme tabanı (λ < 0,25 → sırayla 0,25 boy)
    "slots": 10,                        # pozisyon başına marj ≤ (1 − rezerv)·E / 10
    "reserve_pct": 5.0,
    "leverage_max": 4,
    "liq_buffer_mult": 2.0,             # likidasyon mesafesi ≥ 2 × stop mesafesi
    "mmr_floor": 0.004,
    "hard_cap_pct": 2.0,                # işlem tavanı (RiskEngine PAPER_RESEARCH kopyası)
    "bump_risk_mult": 2.0,              # min-notional çıkarma: risk ≤ min(2 × kademe riski, %2) × E
    "max_positions": 40,
    "max_position_pct": 30.0,           # RiskEngine MAX_POSITION_PCT (fit_size'a önceden verilir)
    "warmup_days": 28,
}
M2X_POLICY_SHA = hashlib.sha256(json.dumps(M2X_POLICY_V1, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

TIER_NAMES: tuple[str, ...] = tuple(t[0] for t in M2X_POLICY_V1["tiers"])
SOFT_HALT_TIER = 3
HALTED = "HALTED"
SNAPSHOT, ENTRY = "SNAPSHOT", "ENTRY"

#: Atlama nedenleri (§1.3) — sayaç anahtarları.
SKIP_HALTED = "M2X_HALTED"
SKIP_SOFT_HALT = "M2X_SOFT_HALT"
SKIP_OR = "M2X_OPEN_RISK_CAP"
SKIP_CL = "M2X_CRASH_BUDGET"
SKIP_MIN_NOTIONAL = "M2X_MIN_NOTIONAL"
SKIP_MARGIN = "M2X_MARGIN"
SKIP_LIQ = "M2X_LIQ_BUFFER"
SKIP_DATA_GAP = "M2X_DATA_GAP"
SKIP_PARENT_CLOSED = "M2X_PARENT_ALREADY_CLOSED"
SKIP_NEW_ENTRIES_OFF = "M2X_NEW_ENTRIES_OFF"
SKIP_MISSED_TOUR = "MISSED_PARENT_TOUR"


@dataclass(frozen=True)
class Knobs:
    """Politika anahtarları. `Knobs()` = v1 (ön kayıt). Diğer değerler yalnız simülasyonun BİLGİ kollarıdır."""
    risk_pct: tuple[float, ...] = tuple(t[2] for t in M2X_POLICY_V1["tiers"])
    or_cap_pct: tuple[float, ...] = tuple(t[3] for t in M2X_POLICY_V1["tiers"])
    proportional_n: int = 0             # (p): risk = min(kademe riski, kademe OR tavanı / n); 0 = kapalı
    ladder: bool = True                 # (c), (d): False → hep K1
    halt: bool = True                   # (c), (d): False → DUR yok
    or_cap: bool = True                 # (d): False → OR tavanı yok
    crash_budget: bool = True           # (d): False → kriz bütçesi yok
    or_measure: str = "mark"            # (f): "entry" → OR girişteki riskle
    leverage_mode: str = "l_liq"        # (g): "l_need" → fit_size'ın seçtiği kaldıraç
    slots: int = int(M2X_POLICY_V1["slots"])   # (d): 1 → pozisyon başı marj tavanı yok (yalnız %95 toplam)
    key_b: bool = True                  # (j): False → yalnız A anahtarı
    peak_basis: str = "mark"            # (i1): "wallet", (i2): "stop" — P ve Pₖ'nin ölçüldüğü özkaynak
    halt_close: bool = False            # (h1): DUR'da bütün pozisyonlar o turun mark'ıyla kapanır
    tier_down_shrink: bool = False      # (h2): kademe düşüşünde açık pozisyonlar orantılı küçültülür

    def tier_risk_pct(self, tier: int) -> float:
        r = float(self.risk_pct[tier])
        if self.proportional_n > 0:
            r = min(r, float(self.or_cap_pct[tier]) / float(self.proportional_n))
        return r


V1 = Knobs()


def tier_lower(tier: int) -> float:
    return float(M2X_POLICY_V1["tiers"][int(tier)][1])


def tier_of_dd(ddk: float) -> int:
    """DDₖ → kademe indeksi (0 = K1 … 3 = K4 yumuşak durdurma)."""
    t = 0
    for i, row in enumerate(M2X_POLICY_V1["tiers"]):
        if ddk >= float(row[1]) - 1e-12:
            t = i
    return t


# ---------------------------------------------------------------------------- durum ve gözlem (§2.2, §2.7, §2.8)
@dataclass
class PolicyState:
    peak: float                         # P (gerçek zirve; ölçü özkaynağı `Knobs.peak_basis`)
    tier_peak: float                    # Pₖ
    tier: int = 0
    halted: bool = False
    halted_at: str | None = None
    streak: int = 0
    last_key: str | None = None
    u_hist: list = field(default_factory=list)   # [(görüntü anı, U | None)] — son u_lookback + 1
    events: list = field(default_factory=list)   # kademe/durdurma olayları (simülasyon raporu)

    @classmethod
    def start(cls, x: float) -> "PolicyState":
        return cls(peak=float(x), tier_peak=float(x))

    @property
    def tier_name(self) -> str:
        return HALTED if self.halted else TIER_NAMES[self.tier]

    def dd(self, x: float) -> float:
        return 1.0 - float(x) / self.peak if self.peak > 0 else 0.0

    def ddk(self, x: float) -> float:
        return 1.0 - float(x) / self.tier_peak if self.tier_peak > 0 else 0.0


def du20(st: PolicyState) -> float | None:
    """ΔU20 = U(bugün) − U(20 görüntü önce). Geçmiş yoksa ya da iki uçtan biri ölçülemediyse None."""
    n = int(M2X_POLICY_V1["u_lookback"])
    if len(st.u_hist) < n + 1:
        return None
    now_u, old_u = st.u_hist[-1][1], st.u_hist[-1 - n][1]
    if now_u is None or old_u is None:
        return None
    return float(now_u) - float(old_u)


def observe(st: PolicyState, *, x: float, kind: str, at: str = "", u: float | None = None, data_gap: bool = False,
            knobs: Knobs = V1) -> list[dict[str, Any]]:
    """Gözlem noktası (§2.7). `x`: DD ölçü özkaynağı (v1: E, mark'a göre). `kind`: SNAPSHOT (O1, günlük görüntü) |
    ENTRY (O2, M2'nin giriş turu). Döner: bu gözlemin olayları (DOWN / UP / HALT / GAP).

    Sıra (okunuş, sonuç görülmeden yazıldı): veri boşluğu → hiçbir şey değişmez, görüntüde sayaç sıfırlanır; görüntüde
    P ve Pₖ güncellenir ve U kaydedilir; DUR (DD ≥ %50); aşağı inme (birden çok kademe, sayaç sıfır); inme yoksa ve
    görüntüyse yukarı çıkış değerlendirmesi (A ya da B; 5 ardışık → bir kademe, B ile çıkışta Pₖ demirlenir)."""
    ev: list[dict[str, Any]] = []
    if data_gap:
        if kind == SNAPSHOT:
            st.streak = 0
            st.u_hist.append((at, None))
            st.u_hist[:] = st.u_hist[-(int(M2X_POLICY_V1["u_lookback"]) + 1):]
        ev.append({"at": at, "kind": "GAP", "obs": kind})
        return ev
    x = float(x)
    if kind == SNAPSHOT:
        st.peak = max(st.peak, x)
        st.tier_peak = min(max(st.tier_peak, x), st.peak)
        st.u_hist.append((at, None if u is None else float(u)))
        st.u_hist[:] = st.u_hist[-(int(M2X_POLICY_V1["u_lookback"]) + 1):]
    dd, ddk = st.dd(x), st.ddk(x)
    if st.halted:
        return ev
    if knobs.halt and dd >= float(M2X_POLICY_V1["halt_dd"]) - 1e-12:
        st.halted, st.halted_at, st.streak = True, at, 0
        ev.append({"at": at, "kind": "HALT", "obs": kind, "dd": dd, "ddk": ddk, "x": x, "peak": st.peak})
        st.events.extend(ev)
        return ev
    if not knobs.ladder:
        return ev
    t = tier_of_dd(ddk)
    if t > st.tier:
        ev.append({"at": at, "kind": "DOWN", "obs": kind, "from": st.tier, "to": t, "dd": dd, "ddk": ddk, "x": x})
        st.tier, st.streak = t, 0
        st.events.extend(ev)
        return ev
    if kind != SNAPSHOT or st.tier == 0:
        return ev
    band = float(M2X_POLICY_V1["step_up_band"])
    a_ok = ddk <= tier_lower(st.tier) - band + 1e-12
    d = du20(st) if knobs.key_b else None
    b_ok = bool(knobs.key_b and d is not None and d > 0 and dd < float(M2X_POLICY_V1["halt_dd"]))
    if a_ok or b_ok:
        st.streak += 1
        st.last_key = "A" if a_ok else "B"
    else:
        st.streak = 0
    if st.streak >= int(M2X_POLICY_V1["step_up_streak"]):
        old = st.tier
        st.tier = old - 1
        st.streak = 0
        anchored = False
        if not a_ok:
            st.tier_peak = min(x / (1.0 - (tier_lower(old) - band)), st.peak)
            anchored = True
        ev.append({"at": at, "kind": "UP", "obs": kind, "from": old, "to": st.tier, "key": "A" if a_ok else "B",
                   "anchored": anchored, "dd": dd, "ddk": ddk, "x": x, "du20": d})
        st.events.extend(ev)
    return ev


def entry_gate(st: PolicyState, *, new_entries: bool = True) -> str | None:
    """Yeni giriş kapısı: None (açık) ya da atlama nedeni."""
    if not new_entries:
        return SKIP_NEW_ENTRIES_OFF
    if st.halted:
        return SKIP_HALTED
    if st.tier >= SOFT_HALT_TIER:
        return SKIP_SOFT_HALT
    return None


# ---------------------------------------------------------------------------- risk ölçüleri (§2.1)
def crash_loss_one(qty: float, mark: float, liq: float | None) -> float:
    """CLᵢ = min(q·(m − liq) + 0,005·q·m, q·m·(X + 0,001)); likidasyon fiyatı yoksa ikinci terim."""
    q, m = float(qty), float(mark)
    second = q * m * (float(M2X_POLICY_V1["crash_x"]) + float(M2X_POLICY_V1["crash_exit_cost"]))
    if liq is None or not (float(liq) > 0):
        return second
    first = q * (m - float(liq)) + float(M2X_POLICY_V1["crash_liq_fee"]) * q * m
    return min(first, second)


def open_risk_one(qty: float, mark: float, stop: float | None, *, entry: float | None = None, measure: str = "mark") -> float:
    """ORᵢ = q·max(0, m − stop) (mark ölçüsü; v1) ya da q·max(0, giriş − stop) (kol f)."""
    if stop is None:
        return float(qty) * float(mark)
    ref = float(entry) if (measure == "entry" and entry is not None) else float(mark)
    return float(qty) * max(0.0, ref - float(stop))


@dataclass(frozen=True)
class PosView:
    """Açık pozisyonun politika görünümü (LONG)."""
    symbol: str
    qty: float
    entry: float
    stop: float | None
    liq: float | None
    mark: float


def totals(positions: Iterable[PosView], *, measure: str = "mark") -> tuple[float, float]:
    """(OR, CL) — açık pozisyonların toplamı."""
    o = c = 0.0
    for p in positions:
        o += open_risk_one(p.qty, p.mark, p.stop, entry=p.entry, measure=measure)
        c += crash_loss_one(p.qty, p.mark, p.liq)
    return o, c


def gaps(*, equity: float, peak: float, open_risk: float, crash_loss: float, or_cap_pct: float,
         knobs: Knobs = V1) -> tuple[float, float]:
    """(H_OR, H_CL). Kapalı kapı (kol d) → +sonsuz."""
    h_or = (float(or_cap_pct) / 100.0 * float(equity) - float(open_risk)) if knobs.or_cap else math.inf
    h_cl = (float(equity) - float(M2X_POLICY_V1["crash_budget_peak_frac"]) * float(peak) - float(crash_loss)) \
        if knobs.crash_budget else math.inf
    return h_or, h_cl


# ---------------------------------------------------------------------------- ortak küçültme (§2.4)
def allocate(cands: Sequence[tuple[float, float]], h_or: float, h_cl: float) -> tuple[float, list[tuple[float | None, str | None]]]:
    """Adaylar M2'nin açılış sırasıyla [(ORᵢ, CLᵢ) tam boyda]. Döner: (λ, [(ölçek | None, atlama nedeni | None)]).

    λ = min(1, H_OR / ΣOR, H_CL / ΣCL); λ ≥ 0,25 → hepsi λ; aksi hâlde sırayla 0,25 boy, iki boşluğa sığdıkça; sığmayan
    bağlayan boşluğa göre M2X_OPEN_RISK_CAP / M2X_CRASH_BUDGET."""
    if not cands:
        return 1.0, []
    so = sum(max(0.0, c[0]) for c in cands)
    sc = sum(max(0.0, c[1]) for c in cands)
    lam = 1.0
    if so > 0 and math.isfinite(h_or):
        lam = min(lam, h_or / so)
    if sc > 0 and math.isfinite(h_cl):
        lam = min(lam, h_cl / sc)
    mn = float(M2X_POLICY_V1["min_scale"])
    if lam >= mn - 1e-12:
        return lam, [(lam, None) for _ in cands]
    out: list[tuple[float | None, str | None]] = []
    ro, rc = h_or, h_cl
    for o, c in cands:
        o4, c4 = mn * max(0.0, o), mn * max(0.0, c)
        if o4 <= ro + 1e-12 and c4 <= rc + 1e-12:
            out.append((mn, None))
            ro -= o4
            rc -= c4
        else:
            out.append((None, SKIP_OR if o4 > ro + 1e-12 else SKIP_CL))
    return lam, out


# ---------------------------------------------------------------------------- boyut, kaldıraç, bracket (§2.5, §2.6)
def liq_price_long(entry: float, qty: float, margin: float, *, brackets=None, fee_cushion_pct: float = 0.0) -> float:
    """Defterin bracket'lı likidasyon formülü (izole, LONG) — `FuturesLedgerV2._liq_price` ile aynı girdi."""
    from .accounting.filters import bracket_for, default_brackets
    from .accounting.liquidation import liquidation_price
    notional = Decimal(repr(float(qty))) * Decimal(repr(float(entry)))
    b = bracket_for(notional, brackets or default_brackets())
    cushion = notional * Decimal(repr(float(fee_cushion_pct))) / Decimal(100)
    return float(liquidation_price("LONG", Decimal(repr(float(entry))), Decimal(repr(float(qty))),
                                   Decimal(repr(float(margin))), b.mmr, b.maint_amount, cushion))


def bracket_mmr(notional: float, brackets=None) -> float:
    from .accounting.filters import bracket_for, default_brackets
    return float(bracket_for(Decimal(repr(max(0.0, float(notional)))), brackets or default_brackets()).mmr)


@dataclass(frozen=True)
class EntryPlan:
    ok: bool
    reason: str | None = None
    notional: float = 0.0
    qty: float = 0.0
    leverage: int = 1
    margin: float = 0.0
    risk_usdt: float = 0.0
    size_rule: str | None = None
    l_need: int = 0
    l_liq: int = 0
    lev_reduced: int = 0                # bracket denetimi yüzünden indirilen kaldıraç adımı
    liq: float | None = None
    detail: dict = field(default_factory=dict)


def plan_entry(*, equity: float, fill: float, stop: float, risk_usdt: float, tier_risk_pct: float, available: float,
               min_notional: float, knobs: Knobs = V1, max_leverage: int | None = None, brackets=None,
               qty_step: float | None = None, min_qty: float | None = None, min_risk_usdt: float = 0.0) -> EntryPlan:
    """Tek adayın boyutu: `learning_mode.fit_size` (aynı kod) + kaldıraç L_liq'e çıkarma + defterin bracket'lı
    likidasyon denetimi (gerekirse kaldıraç bir bir indirilir, en çok fit'in seçtiği kaldıraca kadar) + min-notional
    çıkarma tavanı. `min_risk_usdt`: küçülmüş (SHRUNK) boyutun alt sınırı (0,25 × d)."""
    from .learning_mode import SIZE_BUMP, SIZE_SHRUNK, fit_size
    E = float(equity)
    if not (E > 0 and fill > 0 and 0 < stop < fill and risk_usdt > 0):
        return EntryPlan(False, SKIP_LIQ if (stop is not None and stop >= fill) else "M2X_BAD_INPUT")
    s = (fill - stop) / fill
    lmax = int(M2X_POLICY_V1["leverage_max"])
    if max_leverage is not None:
        lmax = min(lmax, int(max_leverage))
    mmr = max(float(M2X_POLICY_V1["mmr_floor"]), bracket_mmr(risk_usdt / s, brackets))
    fit = fit_size(equity=E, entry=fill, stop=stop, slots=int(knobs.slots), leverage_max=max(1, lmax),
                   risk_pct=risk_usdt / E * 100.0, reserve_pct=float(M2X_POLICY_V1["reserve_pct"]),
                   liq_buffer_mult=float(M2X_POLICY_V1["liq_buffer_mult"]), mmr=mmr, min_notional=float(min_notional),
                   qty_step=qty_step, price_for_step=fill, hard_cap_pct=float(M2X_POLICY_V1["hard_cap_pct"]),
                   min_notional_bump=True, available_margin=float(available), min_qty=min_qty,
                   max_position_pct=float(M2X_POLICY_V1["max_position_pct"]))
    det = dict(fit.detail)
    if not fit.ok:
        why = {"MIN_ORDER_CONFLICT": SKIP_MIN_NOTIONAL, "INSUFFICIENT_MARGIN": SKIP_MARGIN,
               "LIQ_BUFFER_TOO_THIN": SKIP_LIQ}.get(fit.reason, "M2X_SIZE_" + str(fit.reason))
        if fit.reason == "MIN_ORDER_CONFLICT" and det.get("why") == "MARGIN":
            why = SKIP_MARGIN                   # en küçük emir bile serbest marja sığmıyor: marj atlaması
        return EntryPlan(False, why, detail={"fit_reason": fit.reason, **{k: det.get(k) for k in ("why", "l_liq", "l_need")}})
    if fit.size_rule == SIZE_BUMP:
        cap = min(float(M2X_POLICY_V1["bump_risk_mult"]) * float(tier_risk_pct), float(M2X_POLICY_V1["hard_cap_pct"]))
        if fit.risk_usdt > cap / 100.0 * E * (1.0 + 1e-9):
            return EntryPlan(False, SKIP_MIN_NOTIONAL, detail={"bump_risk_usdt": fit.risk_usdt, "cap_usdt": cap / 100.0 * E})
    if fit.size_rule == SIZE_SHRUNK and fit.risk_usdt < float(min_risk_usdt) * (1.0 - 1e-9):
        return EntryPlan(False, SKIP_MARGIN, detail={"shrunk_risk_usdt": fit.risk_usdt, "min_risk_usdt": min_risk_usdt})
    notional = float(fit.notional)
    qty = notional / fill
    if fit.size_rule == SIZE_BUMP and det.get("qty") is not None:
        qty = float(det["qty"])
        notional = qty * fill
    l_fit, l_liq = int(fit.leverage), int(det.get("l_liq") or fit.leverage)
    lev = max(l_fit, l_liq) if knobs.leverage_mode == "l_liq" else l_fit
    reduced = 0
    need = float(M2X_POLICY_V1["liq_buffer_mult"]) * s
    while True:
        liq = liq_price_long(fill, qty, notional / lev, brackets=brackets)
        if (fill - liq) / fill >= need - 1e-12:
            break
        if lev <= l_fit:
            return EntryPlan(False, SKIP_LIQ, detail={"lev": lev, "liq": liq, "need": need})
        lev -= 1
        reduced += 1
    return EntryPlan(True, None, notional=notional, qty=qty, leverage=int(lev), margin=notional / lev,
                     risk_usdt=qty * (fill - stop), size_rule=fit.size_rule, l_need=int(det.get("l_need") or l_fit),
                     l_liq=l_liq, lev_reduced=reduced, liq=liq, detail={"stop_frac": s, "fit_leverage": l_fit})


def shrink_fraction(*, equity: float, peak: float, open_risk: float, crash_loss: float, or_cap_pct: float | None,
                    knobs: Knobs = V1) -> float:
    """(h2) kademe düşüşünde açık pozisyonların KALACAK payı f: OR·f ≤ tavan·E ve E − 0,5·P − f·CL ≥ 0. Tavanı olmayan
    kademede (K4, DUR) yalnız kriz bütçesi uygulanır (okunuş)."""
    f = 1.0
    if or_cap_pct is not None and open_risk > 0:
        f = min(f, float(or_cap_pct) / 100.0 * float(equity) / float(open_risk))
    if crash_loss > 0:
        f = min(f, (float(equity) - float(M2X_POLICY_V1["crash_budget_peak_frac"]) * float(peak)) / float(crash_loss))
    return max(0.0, min(1.0, f))


def knobs_dict(k: Knobs) -> dict[str, Any]:
    from dataclasses import asdict
    return asdict(k)


__all__ = ["ENTRY", "HALTED", "Knobs", "M2X_POLICY_SHA", "M2X_POLICY_V1", "POLICY_VERSION", "PolicyState", "PosView",
           "SNAPSHOT", "TIER_NAMES", "V1", "EntryPlan", "allocate", "crash_loss_one", "du20", "entry_gate", "gaps",
           "liq_price_long", "observe", "open_risk_one", "plan_entry", "shrink_fraction", "tier_of_dd", "totals"]
