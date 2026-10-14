"""P2b — "nasıl kâra dönebilirdi": karşı-olgusal ızgara `cfgrid_v1` (§5.5; MÜHÜRLÜ) ve R ayrıştırması (§5.6).

Bir defterin her işlemi AYNI ızgarayla oynatılır (eşli karşılaştırma; seçilmiş değil). Her hücre aynı gerçek yol
(günlüğün `paths/` dosyası: işlem barları + 48 barlık kuyruk), aynı yürütme modeli (`fidelity.exec_model`: defterin
ledger JSON'undaki ücret/kayma/TP1/MFE başa-baş/likidasyon alanları) ve aynı `learning_cf._net_replay` ile oynatılır;
fonlama kaydın KENDİ uzlaşmalarıdır (`fidelity.RecordedFunding`, kayıttan sonrası depo fonlaması). USDT riski sabittir
(R = net / |dolum − stop| × miktar; büyüklük stop mesafesine uyar). Yalnız fidelity kapısını geçen işlemler (§5.3,
|r_replay − r_actual| ≤ 0,05R) ızgaraya girer; diğerleri yalnız kural kodu alır.

**Hücreler** (`VARIANTS`; her biri gerçek işlemden TEK eksende ayrılır — "taban" `E_ACTUAL`):

| Eksen | Hücreler | Etiket |
|---|---|---|
| Giriş | gerçek; 1 bar (giriş dilimi) gecikmeli; −0,25 ATR limit (yalnız dokunursa, 1 bar geçerli; dolmazsa R = 0, `MISSED`) | EX_ANTE |
| Stop | {0,75; 1; 1,5; 2} × ATR14 (giriş dilimi) | EX_ANTE |
| Çıkış | tek hedef 1R / 2R / 3R; 2×ATR iz süren stop (hedefsiz); hedefsiz + zaman stopu | EX_ANTE |
| Yönetim | TP1 açık (%50) / kapalı; 1R'da başa-baş açık / kapalı | EX_ANTE |
| Büyüklük | yarı kaldıraç (R aynı; likidasyon/marj etkisi gerçek kaldıraçla denetlenir) | EX_ANTE |
| Atla | R = 0 | EX_ANTE |
| İşlem başına en iyi hücre | `hindsight_best` | **HINDSIGHT** — asla tek başına ders olmaz |

* **Ufuk ("orijinal ufuk"):** gerçek çıkış seviyeyle (stop / başa-baş / hedef / likidasyon) olduysa hücreler kuralın tutma
  sınırına (Box gün sonu, D4/C4 bar sınırı) ya da yolun sonuna (kuyruk 48 bar) kadar sürer; kural/zaman çıkışıysa
  (Box gün sonu, Donchian/EMA çıkışı, zaman stopu, elle) hücreler en geç GERÇEK çıkış anında kapanır (kuralın kararı
  ızgarada yeniden üretilmez, zamanı korunur). Açık kalan hücre ufuk sonunda son kapanıştan kapanır (`_net_replay`).
* **Sıralama** her zaman `cf_aux_v1` muhafazakâr R'siyle (`learning_cf_aux.AuxPass`: giriş barı + gerçek stop aşma
  medyanı — aşma geçmişi aynı defterin bu işlemden ÖNCE kapanmış kayıtlarıdır); hesaplanamazsa `r_net` (işaretli).
* **Referans medyandır**, maksimum değil: ayrıştırma ızgara medyanlarına göredir; "en iyi olası" sonuç asla ölçü olmaz.
* **Eşleşmiş rastgele giriş kontrolü:** aynı sembol, aynı gün saati (önceki 120 günün aynı saati — "aynı saat dilimi")
  ve aynı `situation_v1` durum kovasında (4h trend × 4h oynaklık rejimi; aynı saf fonksiyon) 20 rastgele giriş (sabit
  tohum: işlem anahtarı), aynı stop mesafesi (%), aynı R-hedefleri ve aynı çıkış süresiyle 5m barlarda oynatılır.
  `signal_excess_R` = gerçek net R − kontrol net R medyanı. 10'dan az eşleşme → kontrol yok (`RC_TOO_FEW`).
* **Bar sınırı:** bir oynatmanın yolu `REPLAY_MAX_BARS`'ı aşarsa yol barları daha kaba dilime birleştirilir (aynı gerçek
  yol; 5m → 15m → 1h → 4h); taban hücrenin gerçekle farkı (`baseline_gap_r`) bunu görünür kılar.

**R ayrıştırması (§5.6; harfiyen tanım, 2026-10-08 inceleme M1; artık her zaman gösterilir).** G = kayma ÖNCESİ brüt R
(dolum riskine göre). X1 = giriş/stop/çıkış/yönetim eksenlerinin bütün EX_ANTE hücrelerinin G medyanı ("nötr yürütme",
1x), X4 = taban hücre (gerçek yürütme, 1x), Me/Ms/Mx = giriş/stop/çıkış EKSENİNİN ızgara medyanı (eksen gerçek hücreyi de
içerir), B = rastgele kontrol G medyanı, G_lev = taban hücre gerçek kaldıraç/tutarla:

    signal_R = X1 − B · timing_R = X4 − Me · stop_R = X4 − Ms · exit_R = X4 − Mx · size_lev_R = G_lev − X4
    cost_R = net_R − G_gerçek (ölçülen ücret + kayma + fonlama) · artık = net_R − Σ(bileşenler)

Bütün girdiler varken artık = B + (G_gerçek − G_lev) + (Me + Ms + Mx − X1 − 2·X4) (rastgele taban, yeniden oynatma farkı,
eksen medyanlarının toplanamazlığı); üçü `residual_parts`'ta AYRI yazılır. Eksik bileşen 0 sayılmaz: `missing` listesine
girer ve artığa kalır. Tek işlemin ayrıştırması GÖZLEMDİR; kanıt toplu istatistikten gelir (§5.7).

`regime_fit` (§5.6): ders deposu P3'tedir; o gelene kadar aynı defter × taktik × giriş durum kovasının, bu işlem
AÇILMADAN ÖNCE kapanmış günlük satırlarından ortalama net R'ı (`JOURNAL_PRIOR`, n ≥ 10; ileriye bakma yok).

Bu modül ağ kullanmaz ve hiçbir yere yazmaz; S2 orkestrasyonu `analysis.py`'dedir.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable

import pandas as pd

from .attribution import (BE_EXITS, DEFAULT_ENTRY_TF, ENTRY_TF, LIQ_EXITS, STOP_EXITS, TARGET_EXITS, closed_key,
                          max_hold_end_ms)

UTC = timezone.utc
CFGRID_VERSION = "cfgrid_v1"
EX_ANTE, HINDSIGHT, REFERENCE = "EX_ANTE", "HINDSIGHT", "REFERENCE"
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
COARSER = ("5m", "15m", "1h", "4h")
REPLAY_MAX_BARS = 480
LIMIT_ATR = 0.25
STOP_ATR = (0.75, 1.0, 1.5, 2.0)
TARGET_R = (1.0, 2.0, 3.0)
TRAIL_ATR = 2.0
TP1_FRACTION_ON = "0.5"
BE_ON_R = "1"
RC_N, RC_MIN, RC_LOOKBACK_DAYS, RC_TF = 20, 10, 120, "5m"
REGIME_FIT_MIN_N = 10
# ---- sıralama (2026-10-08, inceleme M3)
RANK_CONSERVATIVE, RANK_NET = "cf_aux_v1", "r_net"
#: sıralamaya, işlem başına en iyi hücreye ve defter görünümüne GİRMEYEN eksenler: boyut/kaldıraç bir yürütme önerisi
#: değildir (kaldıraç HİPOTETİK, §7.6; R aynı kalır, yalnız likidasyon/marj etkisi `size_lev_R`'de)
RANK_EXCLUDED_AXES = ("size",)
#: defter görünümü (en iyi EX_ANTE sabit hücre): §5.7 uygunluk eşiği (n ≥ 30 ve ≥ 10 farklı gün); yalnız ortalamada
#: iyileştiren hücre; "k hücre arasından seçim" etiketiyle
BOOK_VIEW_MIN_N, BOOK_VIEW_MIN_DAYS = 30, 10
LEVEL_EXITS = STOP_EXITS + BE_EXITS + TARGET_EXITS + LIQ_EXITS

#: (kimlik, eksen, etiket, açıklama) — sıra = çıktı sırası
VARIANTS: tuple[tuple[str, str, str, str], ...] = (
    ("E_ACTUAL", "entry", EX_ANTE, "gerçek giriş, stop ve çıkış (taban)"),
    ("E_DELAY1", "entry", EX_ANTE, "1 bar (giriş dilimi) gecikmeli piyasa girişi; stop/hedef seviyeleri aynı"),
    ("E_LIMIT_025", "entry", EX_ANTE, "referans − 0,25 ATR limit; yalnız 1 bar içinde dokunursa (dolmazsa R=0, MISSED)"),
    ("S_ATR075", "stop", EX_ANTE, "stop = referans ∓ 0,75 × ATR14"),
    ("S_ATR100", "stop", EX_ANTE, "stop = referans ∓ 1 × ATR14"),
    ("S_ATR150", "stop", EX_ANTE, "stop = referans ∓ 1,5 × ATR14"),
    ("S_ATR200", "stop", EX_ANTE, "stop = referans ∓ 2 × ATR14"),
    ("X_T1R", "exit", EX_ANTE, "tek hedef 1R"),
    ("X_T2R", "exit", EX_ANTE, "tek hedef 2R"),
    ("X_T3R", "exit", EX_ANTE, "tek hedef 3R"),
    ("X_TRAIL2ATR", "exit", EX_ANTE, "hedefsiz, 2 × ATR14 iz süren stop (en iyi uçtan; bar kapanışında)"),
    ("X_NONE_TIME", "exit", EX_ANTE, "hedefsiz + zaman stopu (ufuk sonu)"),
    ("M_TP1_ON", "manage", EX_ANTE, "TP1 açık (%50; hedef yoksa 1R/2R)"),
    ("M_TP1_OFF", "manage", EX_ANTE, "TP1 kapalı (yalnız son hedef, tam çıkış)"),
    ("M_BE1R_ON", "manage", EX_ANTE, "1R'da başa-baş açık"),
    ("M_BE_OFF", "manage", EX_ANTE, "MFE başa-baş kapalı"),
    ("Z_HALF_LEV", "size", EX_ANTE, "yarı kaldıraç (R aynı; likidasyon denetlenir)"),
    ("SKIP", "skip", EX_ANTE, "atla (R = 0)"),
)
VARIANT_IDS = tuple(v[0] for v in VARIANTS)
ENTRY_ACTUAL_CELLS = tuple(v[0] for v in VARIANTS if v[1] != "entry" and v[0] != "SKIP") + ("E_ACTUAL",)
ENTRY_STOP_ACTUAL_CELLS = tuple(v[0] for v in VARIANTS if v[1] in ("exit", "manage", "size")) + ("E_ACTUAL",)

CFGRID_SPEC: dict[str, Any] = {
    "id": CFGRID_VERSION, "variants": [list(v) for v in VARIANTS], "eligibility": "futures, fidelity OK (|dR| <= 0.05), "
    "risk known, path 1m/5m with file", "replay": "learning_cf._net_replay (trail/leverage: _replay_ext, parity-tested)",
    "exec_model": "fidelity.exec_model(ledger exec params)", "funding": "fidelity.RecordedFunding(record) then store funding",
    "risk": "constant USDT risk; R per own |fill - stop| x qty", "horizon": "level exit -> min(path end, max_hold end); "
    "rule/time exit -> actual exit time", "limit": {"atr": LIMIT_ATR, "valid_bars_entry_tf": 1, "unfilled": "R=0 MISSED"},
    "delay_bars_entry_tf": 1, "stop_atr": list(STOP_ATR), "target_r": list(TARGET_R), "trail_atr": TRAIL_ATR,
    "tp1_fraction_on": TP1_FRACTION_ON, "be_on_r": BE_ON_R,
    "ranking": {"per_trade": "ONE measure per trade: cf_aux_v1 r_net_conservative if every rank-eligible cell has it, "
                "else r_net for all cells (rank_src)", "rank_eligible": "EX_ANTE, axis not in excluded, status OK/MISSED",
                "excluded_axes": list(RANK_EXCLUDED_AXES), "excluded_reason": "size/leverage is never a suggestion "
                "(leverage HYPOTHETICAL, design 7.6); its effect is size_lev_R only",
                "book_view": {"measure": "cf_aux_v1 only (r_net trades counted as excluded)", "pairing": "cell - E_ACTUAL "
                              "same trade", "min_n": BOOK_VIEW_MIN_N, "min_days": BOOK_VIEW_MIN_DAYS,
                              "only_improving": True, "label": "SELECTION: best of k fixed cells (selection bias; never a "
                              "lesson by itself)"},
                "amended": "2026-10-08 review M3"},
    "horizon_ex_ante": False,
    "horizon_note": "cells are EX_ANTE in entry/stop/target/management rules only: the horizon is the actual exit time for "
                    "rule/time exits and the path end (actual close + 48 tail bars) capped by max_hold for level exits; "
                    "E_DELAY1 has NO_BARS on trades shorter than one entry-tf bar (counted, disclosed)",
    "reference": "median, never max", "max_bars": REPLAY_MAX_BARS, "coarser": list(COARSER),
    "random_control": {"n": RC_N, "min": RC_MIN, "lookback_days": RC_LOOKBACK_DAYS, "tf": RC_TF, "time": "same UTC time of "
                       "day, k days earlier, window before opened", "bucket": "situation_v1 h4 trend x h4 vol_regime "
                       "(_tf_block full=False, W4H closed 4h bars)", "seed": "sha256(trade_key|cfgrid_v1)", "stop": "same "
                       "stop_dist_pct", "targets": "same R multiples", "exit": "same duration"},
    "decomposition": {"G": "gross pre-slippage R on fill risk", "X1": "median G of EX_ANTE cells of the entry/stop/exit/"
                      "manage axes (1x neutral execution; size and skip excluded)", "X4": "G(E_ACTUAL)",
                      "axis_median": "median G of the axis cells incl. E_ACTUAL (entry: E_*; stop: S_*; exit: X_*)",
                      "B": "median G random control", "signal": "X1-B", "timing": "X4-median(entry axis)",
                      "stop": "X4-median(stop axis)", "exit": "X4-median(exit axis)", "size_lev": "G_lev-X4",
                      "cost": "net-G_real", "residual": "net - sum (always shown) = B + (G_real - G_lev) + "
                      "(Me + Ms + Mx - X1 - 2*X4) when all inputs exist", "amended": "2026-10-08 review M1 (spec-literal)"},
    "entry_tf": ENTRY_TF, "regime_fit": {"source": "JOURNAL_PRIOR until P3 lessons", "min_n": REGIME_FIT_MIN_N},
}
CFGRID_SHA = hashlib.sha256(json.dumps(CFGRID_SPEC, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()


def _r6(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, 6) + 0.0


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=UTC).isoformat()


def _ms(ts: Any) -> int | None:
    from .ledgers import parse_ts
    t = parse_ts(ts)
    return int(t.timestamp() * 1000) if t is not None else None


def median(xs: list[float]) -> float | None:
    v = [float(x) for x in xs if x is not None]
    return _r6(statistics.median(v)) if v else None


# ============================================================================ fonlama
class StoreFunding:
    """Depo fonlama serisinden oran (uzlaşma anı ±60 sn); settlement mark'ı yok (defter pozisyon mark'ını kullanır)."""

    def __init__(self, df: pd.DataFrame | None) -> None:
        self.ts: list[int] = []
        self.rate: list[float] = []
        if df is not None and len(df) and "rate" in df.columns:
            d = df[df["rate"] == df["rate"]].sort_values("timestamp")
            self.ts = [int(x) for x in d["timestamp"].tolist()]
            self.rate = [float(x) for x in d["rate"].tolist()]

    def __call__(self, symbol: str, when: datetime) -> Decimal | None:
        if not len(self.ts):
            return None
        t = int(when.timestamp() * 1000)
        i = bisect.bisect_left(self.ts, t - 60_000)
        if i < len(self.ts) and abs(int(self.ts[i]) - t) <= 60_000:
            return Decimal(repr(float(self.rate[i])))
        return None

    def settlement_mark(self, symbol: str, when: datetime) -> Decimal | None:
        return None


class ChainFunding:
    """Kaydın kendi uzlaşmaları (watermark'a kadar), sonra depo."""

    def __init__(self, recorded: Any, store: StoreFunding | None) -> None:
        self.recorded, self.store = recorded, store

    def __call__(self, symbol: str, when: datetime) -> Decimal | None:
        v = self.recorded(symbol, when) if self.recorded is not None else None
        if v is None and self.store is not None:
            v = self.store(symbol, when)
        return v

    def settlement_mark(self, symbol: str, when: datetime) -> Decimal | None:
        if self.recorded is not None:
            m = self.recorded.settlement_mark(symbol, when)
            if m is not None:
                return m
        return None


def recorded_funding(cf_inputs: dict | None) -> Any:
    from .fidelity import RecordedFunding
    ci = cf_inputs or {}
    rec = {"features": {"funding_settlements": [{"settlement": s, "rate": r, "mark": m}
                                                for s, r, m in (ci.get("funding_settlements") or [])],
                        "funding_settled_until": ci.get("funding_settled_until")}}
    return RecordedFunding(rec)


# ============================================================================ yeniden oynatma
def gross_pre(out: dict[str, Any], entry: float, stop: float) -> float | None:
    """Oynatmanın kayma ÖNCESİ brüt R'si, DOLUM riskine göre (günlüğün `gross_r + slippage_in_fills` tanımıyla aynı):
    `_decompose` parçalarından a = r_net + giriş dolumu + çıkış kayması + ücret + fonlama (referans riskine göre),
    sonra × |ref − stop| / |dolum − stop|."""
    rn = out.get("r_net")
    parts = out.get("cost_parts_r") or {}
    ef = out.get("entry_fill")
    if rn is None or ef is None:
        return None
    a = float(rn) + sum(float(parts.get(k) or 0.0) for k in ("entry_fill", "exit_slippage", "fees", "funding"))
    den = abs(float(ef) - stop)
    if den <= 0:
        return None
    return _r6(a * abs(entry - stop) / den)


def _view(*, symbol: str, side: str, created_ms: int, entry: float, stop: float, targets: list[float], label_ms: int,
          tf: str, n: int, pre_filled: bool = False) -> Any:
    from ..learn.shadow import ShadowTrade
    return ShadowTrade(id="cf", plan_id="cf", symbol=symbol, market_type="USDM_PERP", direction=str(side).upper(),
                       created_at=_iso(created_ms), entry=float(entry), stop=float(stop), targets=[float(t) for t in targets],
                       horizon_bars=int(n), variant="as_planned", reason_not_opened=[], label_ts=_iso(label_ms),
                       tf_minutes=max(1, TF_MS[tf] // 60_000),
                       features={"entry_ref": "quantized_entry"} if pre_filled else None)


def replay(view: Any, df: pd.DataFrame, *, model: Any, funding: Any, trail_atr: float | None = None,
           leverage: int = 1, notional: Decimal | None = None) -> dict[str, Any]:
    """Tek hücre: iz süren stop / kaldıraç YOKSA doğrudan `learning_cf._net_replay`; varsa `_replay_ext` (aynı döngü,
    yalnız o iki kanca; kancasız hâli `_net_replay` ile bayt-özdeştir — test)."""
    gross = {"bars": int(len(df)), "exit_reason": "horizon"}
    if trail_atr is None and leverage == 1 and notional is None:
        from ..learning_cf import _net_replay
        return _net_replay(view, df, gross, 0.0, model=model, filters=None, funding_lookup=funding)
    return _replay_ext(view, df, gross, model=model, funding_lookup=funding, trail_atr=trail_atr, leverage=leverage,
                       notional=notional)


def _replay_ext(view: Any, df: pd.DataFrame, gross: dict[str, Any], *, model: Any, funding_lookup: Any,
                trail_atr: float | None = None, leverage: int = 1, notional: Decimal | None = None,
                stop_fill: Callable[[int], Any] | None = None) -> dict[str, Any]:
    """`learning_cf._net_replay` (cf_net_ledger_replay_v2) döngüsünün KOPYASI, üç kancayla: `trail_atr` (her işlenen
    barın SONUNDA stop = en iyi uç ∓ trail_atr; yalnız lehte taşınır, sonraki barlardan itibaren etkili — nedensel),
    `leverage`/`notional` (gerçek kaldıraçla likidasyon denetimi) ve `stop_fill` (2026-10-08, B1; yalnız `fidelity`: stop bu
    barda tetiklenirse `stop_fill(bar açılışı ms)` bir fiyat döndürürse dolum referansı o fiyattır — kaydın GÖZLEMDEN dolumu,
    `RECORDED_FILL`; fiyat stopun ötesinde değilse kullanılmaz). Kancasız çağrı `_net_replay` ile aynı sonucu verir
    (`test_research_engine_cfgrid`)."""
    from ..accounting import EXIT_BE_STOP, EXIT_STOP, AmountType, SizeSpec, SlippageModel, TickData
    from ..core import D, from_iso, iso
    from ..learning_cf import (STOP_FILL_RULE, NET_CONTRACT, _bar_path, _beyond, _decompose, _open_px, _quiet_rates,
                               _r6 as r6, _replay_filters, _replay_notional)
    sym = str(view.symbol)
    f = _replay_filters(sym, None)
    xm = model.to_dict(f)
    entry, stop = D(float(view.entry)), D(float(view.stop))
    targets = [D(float(t)) for t in (view.targets or [])]
    created, label_ts = from_iso(view.created_at), from_iso(view.label_ts)
    led = model.new_ledger()
    pre_filled = str(((view.features or {}) if isinstance(view.features, dict) else {}).get("entry_ref") or "") == \
        "quantized_entry"
    size = notional if notional is not None else _replay_notional(f, entry)
    pos = led.open(sym, view.direction, entry, SizeSpec(size, AmountType.NOTIONAL, int(leverage)), stop=stop,
                   targets=targets, filters=f, now=created, slippage=SlippageModel.zero() if pre_filled else None)
    if pos is None:
        return {"r_net": None, "net_status": "OPEN_REJECTED:%s" % (led.last_reject_reason or "?"), "exec_model": xm}
    tf_ms = int(view.tf_minutes) * 60_000
    ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    path = df.loc[((ts > created) & (ts <= label_ts)).values]
    n_bars = int(gross.get("bars") or len(path))
    path = path.iloc[:max(0, n_bars)]
    lookup = _quiet_rates(funding_lookup)
    long = str(view.direction).upper() == "LONG"
    rec, last_c, last_dt = None, None, None
    opens = path["open"].tolist() if "open" in path.columns else [None] * len(path)
    first_bar = True
    ambiguous = 0
    best = None
    for t, o, h, lo, c in zip(path["timestamp"].tolist(), opens, path["high"].tolist(), path["low"].tolist(),
                              path["close"].tolist()):
        odt = datetime.fromtimestamp(int(t) / 1000.0, tz=timezone.utc)
        cdt = datetime.fromtimestamp((int(t) + tf_ms) / 1000.0, tz=timezone.utc)
        stop_before_fav = None
        for i, (point, px) in enumerate(_bar_path(long, _open_px(o), float(h), float(lo), float(c))):
            p = led.positions.get(sym)
            if p is None:
                break
            if point == "favourable":
                stop_before_fav = p.stop
            at = odt if point == "open" else cdt
            price, rule = D(float(px)), None
            if p.stop is not None and _beyond(long, p.stop, price):
                rf = stop_fill(int(t)) if stop_fill is not None else None          # KANCA: kaydın gözlem dolumu
                if rf is not None and _beyond(long, p.stop, D(str(rf))):
                    price, rule = D(str(rf)), "RECORDED_FILL"
                elif point == "open" and not first_bar:
                    rule = "GAP_FILL_AT_BAR_OPEN"
                else:
                    price, rule = p.stop, "STOP_AT_LEVEL"
            recs = led.tick({sym: TickData(last=price, mark=price, ts=iso(at))}, now_utc=at, funding_rate_lookup=lookup,
                            bar_advance=(i == 0))
            if recs:
                rec = recs[-1]
                ef = (rec.features or {}).get("exit_fill")
                if rule is not None and isinstance(ef, dict) and rec.exit_reason in (EXIT_STOP, EXIT_BE_STOP):
                    ef.update({"basis": rule, "path_point": point, "path_price": str(D(float(px))),
                               "fill_rule": STOP_FILL_RULE, "net_contract": NET_CONTRACT})
                break
        last_c, last_dt = float(c), cdt
        first_bar = False
        if rec is not None:
            break
        p = led.positions.get(sym)
        if (p is not None and p.stop is not None and stop_before_fav is not None and p.stop != stop_before_fav
                and _beyond(long, p.stop, D(float(lo) if long else float(h)))):
            ambiguous += 1
        if trail_atr is not None and p is not None:              # KANCA: bar sonunda iz süren stop (yalnız lehte)
            fav = float(h) if long else float(lo)
            best = fav if best is None else (max(best, fav) if long else min(best, fav))
            cand = D(repr(best - trail_atr)) if long else D(repr(best + trail_atr))
            if p.stop is None or (cand > p.stop if long else cand < p.stop):
                p.stop = cand
    basis = None
    if rec is None:
        if last_c is None:
            return {"r_net": None, "net_status": "NO_BARS", "exec_model": xm}
        rec = led.close_manual(sym, D(last_c), reason="horizon", now=last_dt)
        basis = "HORIZON_CLOSE" if str(gross.get("exit_reason")) == "horizon" else "CLOSED_AT_GROSS_EXIT_BAR"
    if basis is None:
        basis = ((rec.features or {}).get("exit_fill") or {}).get("basis") or "TARGET_AT_LEVEL"
    r_net = r6(float(rec.r_multiple))
    parts = _decompose(rec, view, 0.0)
    ent = [x for x in rec.fills if x.kind == "entry"][0]
    cov = (rec.features or {}).get("funding_coverage") or {}
    return {"r_net": r_net, "cost_r": r6(0.0 - r_net), "cost_parts_r": parts, "net_exit_reason": str(rec.exit_reason),
            "net_exit_basis": str(basis), "net_exit_price": float(rec.exit_price) if rec.exit_price is not None else None,
            "entry_fill": float(ent.price), "won_net": r_net >= 0.25, "veto_was_right_net": r_net <= 0,
            "funding_complete": bool(cov.get("complete")), "funding_missing": cov.get("missing"),
            "intrabar_ambiguous_bars": int(ambiguous), "net_status": "OK", "exec_model": xm}


def coarsen(df: pd.DataFrame, tf: str, max_bars: int = REPLAY_MAX_BARS) -> tuple[pd.DataFrame, str]:
    """Yol `max_bars`'ı aşarsa daha kaba dilime birleştir (açılış = ilk açılış, uçlar max/min, kapanış = son; `phase` =
    en küçük). Aynı gerçek yol, daha az bar."""
    if len(df) <= max_bars:
        return df, tf
    for c in COARSER:
        if TF_MS[c] <= TF_MS[tf]:
            continue
        step = TF_MS[c]
        g = df.assign(_b=(df["timestamp"] // step) * step).groupby("_b", sort=True)
        out = pd.DataFrame({"timestamp": g["timestamp"].min().index.astype("int64"), "open": g["open"].first().values,
                            "high": g["high"].max().values, "low": g["low"].min().values, "close": g["close"].last().values})
        if "phase" in df.columns:
            out["phase"] = g["phase"].min().values
        if len(out) <= max_bars or c == COARSER[-1]:
            return out.reset_index(drop=True), c
    return df, tf


# ============================================================================ işlem planı
@dataclass
class TradePlan:
    """Gerçek işlemin ızgara tabanı (hücreler bundan TEK eksende ayrılır)."""
    key: str
    book: str
    symbol: str
    side: str
    opened_ms: int
    closed_ms: int
    ref: float                 # giriş referansı (canlı mark; dolum kaymayı defter uygular)
    pre_filled: bool           # referans yoksa kayıt dolumu (kayma tekrar uygulanmaz)
    stop: float
    targets: list[float]
    level_exit: bool           # gerçek çıkış seviyeyle mi (yoksa kural/zaman)
    horizon_ms: int            # hücrelerin en geç kapandığı an
    entry_tf: str
    atr: float | None
    leverage: int
    notional: Decimal | None
    net_r: float
    gross_pre_r: float | None

    @property
    def sgn(self) -> int:
        return 1 if self.side.upper() == "LONG" else -1

    @property
    def stop_dist(self) -> float:
        return abs(self.ref - self.stop)


def trade_plan(row: dict, bars: pd.DataFrame, *, atr: float | None) -> tuple[TradePlan | None, str | None]:
    from .attribution import cost_parts
    om, cm = _ms(row.get("opened_at")), _ms(row.get("closed_at"))
    stop = _r6(row.get("initial_stop"))
    ref = _r6(row.get("ref_price"))
    pre = False
    if ref is None:
        ref, pre = _r6(row.get("entry_fill")), True
    net = _r6(row.get("net_r"))
    if om is None or cm is None or stop is None or ref is None or net is None:
        return None, "PLAN_INPUT_MISSING"
    if ref == stop:
        return None, "ZERO_STOP"
    t = row.get("targets")
    if not isinstance(t, list):
        return None, "TARGETS_MISSING"
    reason = str(row.get("exit_reason") or "")
    level = reason in LEVEL_EXITS
    path_end = int(bars["timestamp"].max()) + 1 if len(bars) else cm
    if level:
        hz = path_end
        mh = max_hold_end_ms(row, om)
        if mh is not None:
            hz = min(hz, max(mh, cm))
    else:
        hz = cm
    lev = int(row.get("leverage") or 1)
    notional = None
    try:
        notional = Decimal(str(row.get("notional"))) if row.get("notional") is not None else None
    except Exception:  # noqa: BLE001
        notional = None
    return TradePlan(key=str(row.get("trade_key")), book=str(row.get("book")), symbol=str(row.get("symbol")),
                     side=str(row.get("side") or "LONG").upper(), opened_ms=om, closed_ms=cm, ref=float(ref), pre_filled=pre,
                     stop=float(stop), targets=[float(x) for x in t], level_exit=level, horizon_ms=int(hz),
                     entry_tf=ENTRY_TF.get(str(row.get("book")), DEFAULT_ENTRY_TF), atr=atr, leverage=max(1, lev),
                     notional=notional, net_r=float(net), gross_pre_r=cost_parts(row).get("gross_pre")), None


# ============================================================================ hücreler
@dataclass
class Cell:
    id: str
    axis: str
    label: str
    status: str = "OK"
    r_net: float | None = None
    r_cons: float | None = None
    g: float | None = None
    exit_reason: str | None = None
    entry: float | None = None
    stop: float | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "axis": self.axis, "label": self.label, "status": self.status, "r_net": _r6(self.r_net),
                "r_cons": _r6(self.r_cons), "g": _r6(self.g), "exit": self.exit_reason, "entry": _r6(self.entry),
                "stop": _r6(self.stop), "note": self.note}


# ============================================================================ sıralama (2026-10-08, inceleme M3)


def rank_eligible(c: Cell) -> bool:
    return c.label == EX_ANTE and c.axis not in RANK_EXCLUDED_AXES and c.status in ("OK", "MISSED")


def rank_source(cells: dict[str, Cell]) -> str:
    """İşlemin TEK sıralama ölçüsü: sıralamaya giren her hücrenin `cf_aux_v1` muhafazakâr R'si varsa o, yoksa HEPSİ için
    `r_net` (aynı işlemde iki ölçü karışmaz)."""
    elig = [c for c in cells.values() if rank_eligible(c)]
    return RANK_CONSERVATIVE if elig and all(c.r_cons is not None for c in elig) else RANK_NET


def rank_value(c: Cell, src: str) -> float | None:
    return c.r_cons if src == RANK_CONSERVATIVE else c.r_net


def profitable_cells(cells: dict[str, Cell], src: str) -> list[str]:
    """Bu işlemde kâra dönen hücreler (HINDSIGHT): sıralamaya giren, dolmuş (`OK`) ve işlemin ölçüsüyle R > 0."""
    return sorted(c.id for c in cells.values() if rank_eligible(c) and c.status == "OK" and c.id != "E_ACTUAL"
                  and (rank_value(c, src) or 0) > 0)


@dataclass
class GridEnv:
    """Bir işlemin ızgara ortamı (yol, giriş barı, model, fonlama, aşma geçmişi)."""
    plan: TradePlan
    bars: pd.DataFrame
    tf: str
    entry_bar: pd.DataFrame | None
    model: Any
    funding: Any
    history: list[Any] = field(default_factory=list)
    _aux_frame: pd.DataFrame | None = None
    memo: dict = field(default_factory=dict)

    def aux_frame(self) -> pd.DataFrame:
        if self._aux_frame is None:
            self._aux_frame = _frame_with_entry(self)
        return self._aux_frame


def _arr(df: pd.DataFrame) -> tuple:
    return (df["timestamp"].to_numpy(dtype="int64"), df["high"].to_numpy(dtype="float64"), df["low"].to_numpy(dtype="float64"))


def _aux_cons(env: GridEnv, view: Any, out: dict[str, Any], frame: pd.DataFrame, model: Any) -> float | None:
    from ..learning_cf_aux import AuxPass
    try:
        a = AuxPass(history=lambda: env.history).build(view, _arr(frame), out, model=model, filters=None,
                                                       funding_lookup=env.funding)
    except Exception:  # noqa: BLE001 — yardımcı etiket arızası sıralamayı r_net'e bırakır
        return None
    return _r6((a or {}).get("r_net_conservative"))


def _frame_with_entry(env: GridEnv) -> pd.DataFrame:
    cols = ["timestamp", "open", "high", "low", "close"]
    parts = [env.bars[cols]]
    if env.entry_bar is not None and len(env.entry_bar):
        parts.insert(0, env.entry_bar[cols])
    return pd.concat(parts, ignore_index=True).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def _run(env: GridEnv, cid: str, axis: str, *, created_ms: int, entry: float, stop: float, targets: list[float],
         pre_filled: bool, model: Any = None, trail_atr: float | None = None, leverage: int = 1,
         notional: Decimal | None = None, label: str = EX_ANTE) -> Cell:
    p = env.plan
    model = model or env.model
    cell = Cell(cid, axis, label, entry=entry, stop=stop)
    if (stop - entry) * p.sgn >= 0:
        cell.status, cell.note = "INVALID_STOP_SIDE", "stop girişin yanlış tarafında"
        return cell
    df = env.bars[(env.bars["timestamp"] > created_ms) & (env.bars["timestamp"] < p.horizon_ms)]
    if not len(df):
        cell.status = "NO_BARS"
        return cell
    view = _view(symbol=p.symbol, side=p.side, created_ms=created_ms, entry=entry, stop=stop, targets=targets,
                 label_ms=p.horizon_ms - 1, tf=env.tf, n=len(df), pre_filled=pre_filled)
    frame = df[["timestamp", "open", "high", "low", "close"]].reset_index(drop=True)
    mkey = (created_ms, repr(entry), repr(stop), tuple(repr(t) for t in targets), pre_filled, str(model.tp1_fraction),
            str(model.breakeven_at_mfe_r), trail_atr, leverage, str(notional))
    try:
        if mkey in env.memo:                                # aynı yapılandırma → aynı sonuç (yalnız hız)
            out = env.memo[mkey]
        else:
            out = replay(view, frame, model=model, funding=env.funding, trail_atr=trail_atr, leverage=leverage,
                         notional=notional)
            env.memo[mkey] = out
    except Exception as exc:  # noqa: BLE001 — hücre arızası yalnız o hücreyi düşürür
        cell.status, cell.note = "REPLAY_ERROR", f"{type(exc).__name__}: {exc}"[:120]
        return cell
    if out.get("r_net") is None:
        cell.status, cell.note = "REPLAY_ERROR", str(out.get("net_status"))[:120]
        return cell
    cell.r_net = _r6(out["r_net"])
    cell.g = gross_pre(out, entry, stop)
    cell.exit_reason = str(out.get("net_exit_reason"))
    if leverage == 1 and notional is None:                  # kaldıraç hücreleri sıralamada r_net'le (aux 1x açar)
        cell.r_cons = _aux_cons(env, view, out, env.aux_frame(), model)
    return cell


def run_grid(env: GridEnv) -> dict[str, Cell]:
    """Bütün `VARIANTS` hücreleri (+ `Z_ACTUAL_LEV` referansı)."""
    from ..core import D
    p = env.plan
    sg = p.sgn
    cells: dict[str, Cell] = {}
    base = dict(created_ms=p.opened_ms, entry=p.ref, stop=p.stop, targets=list(p.targets), pre_filled=p.pre_filled)
    cells["E_ACTUAL"] = _run(env, "E_ACTUAL", "entry", **base)
    # ---- giriş
    step = TF_MS.get(p.entry_tf, TF_MS[DEFAULT_ENTRY_TF])
    after = env.bars[(env.bars["timestamp"] >= p.opened_ms + step) & (env.bars["timestamp"] < p.horizon_ms)]
    if len(after):
        b0 = after.iloc[0]
        c = _run(env, "E_DELAY1", "entry", created_ms=int(b0["timestamp"]), entry=float(b0["open"]), stop=p.stop,
                 targets=list(p.targets), pre_filled=False)
        if c.status == "INVALID_STOP_SIDE":
            c.status, c.r_net, c.r_cons, c.g, c.note = "MISSED", 0.0, 0.0, 0.0, "gecikmeli girişte fiyat stopun ötesinde"
        cells["E_DELAY1"] = c
    else:
        cells["E_DELAY1"] = Cell("E_DELAY1", "entry", EX_ANTE, status="NO_BARS")
    if p.atr:
        lim = p.ref - sg * LIMIT_ATR * p.atr
        win = env.bars[(env.bars["timestamp"] > p.opened_ms) & (env.bars["timestamp"] < min(p.opened_ms + step, p.horizon_ms))]
        hit = win[(win["low"] <= lim)] if sg > 0 else win[(win["high"] >= lim)]
        if len(hit):
            c = _run(env, "E_LIMIT_025", "entry", created_ms=int(hit.iloc[0]["timestamp"]), entry=lim, stop=p.stop,
                     targets=list(p.targets), pre_filled=True)
        else:
            c = Cell("E_LIMIT_025", "entry", EX_ANTE, status="MISSED", r_net=0.0, r_cons=0.0, g=0.0, entry=lim, stop=p.stop,
                     note="limit dolmadı (kaçan, R=0)")
        cells["E_LIMIT_025"] = c
    else:
        cells["E_LIMIT_025"] = Cell("E_LIMIT_025", "entry", EX_ANTE, status="NO_ATR")
    # ---- stop
    for k, cid in zip(STOP_ATR, ("S_ATR075", "S_ATR100", "S_ATR150", "S_ATR200")):
        if p.atr:
            cells[cid] = _run(env, cid, "stop", created_ms=p.opened_ms, entry=p.ref, stop=p.ref - sg * k * p.atr,
                              targets=list(p.targets), pre_filled=p.pre_filled)
        else:
            cells[cid] = Cell(cid, "stop", EX_ANTE, status="NO_ATR")
    # ---- çıkış
    for k, cid in zip(TARGET_R, ("X_T1R", "X_T2R", "X_T3R")):
        cells[cid] = _run(env, cid, "exit", created_ms=p.opened_ms, entry=p.ref, stop=p.stop,
                          targets=[p.ref + sg * k * p.stop_dist], pre_filled=p.pre_filled)
    if p.atr:
        cells["X_TRAIL2ATR"] = _run(env, "X_TRAIL2ATR", "exit", created_ms=p.opened_ms, entry=p.ref, stop=p.stop, targets=[],
                                    pre_filled=p.pre_filled, trail_atr=TRAIL_ATR * p.atr)
    else:
        cells["X_TRAIL2ATR"] = Cell("X_TRAIL2ATR", "exit", EX_ANTE, status="NO_ATR")
    cells["X_NONE_TIME"] = _run(env, "X_NONE_TIME", "exit", created_ms=p.opened_ms, entry=p.ref, stop=p.stop, targets=[],
                                pre_filled=p.pre_filled)
    # ---- yönetim
    t = list(p.targets)
    if len(t) >= 2:
        t_on = t
    elif len(t) == 1:
        t_on = [t[0], p.ref + 2 * (t[0] - p.ref)]
    else:
        t_on = [p.ref + sg * p.stop_dist, p.ref + sg * 2 * p.stop_dist]
    cells["M_TP1_ON"] = _run(env, "M_TP1_ON", "manage", created_ms=p.opened_ms, entry=p.ref, stop=p.stop, targets=t_on,
                             pre_filled=p.pre_filled, model=replace(env.model, tp1_fraction=D(TP1_FRACTION_ON)))
    cells["M_TP1_OFF"] = _run(env, "M_TP1_OFF", "manage", created_ms=p.opened_ms, entry=p.ref, stop=p.stop,
                              targets=t[-1:], pre_filled=p.pre_filled)
    cells["M_BE1R_ON"] = _run(env, "M_BE1R_ON", "manage", created_ms=p.opened_ms, entry=p.ref, stop=p.stop, targets=t,
                              pre_filled=p.pre_filled, model=replace(env.model, breakeven_at_mfe_r=D(BE_ON_R)))
    cells["M_BE_OFF"] = _run(env, "M_BE_OFF", "manage", created_ms=p.opened_ms, entry=p.ref, stop=p.stop, targets=t,
                             pre_filled=p.pre_filled, model=replace(env.model, breakeven_at_mfe_r=D("0")))
    # ---- büyüklük
    half = max(1, p.leverage // 2)
    cells["Z_HALF_LEV"] = _run(env, "Z_HALF_LEV", "size", **base, leverage=half, notional=p.notional)
    cells["SKIP"] = Cell("SKIP", "skip", EX_ANTE, r_net=0.0, r_cons=0.0, g=0.0, note="işlem açılmasaydı")
    cells["Z_ACTUAL_LEV"] = _run(env, "Z_ACTUAL_LEV", "size", **base, leverage=p.leverage, notional=p.notional,
                                 label=REFERENCE)
    return cells


# ============================================================================ rastgele kontrol
def bucket_at(h4: pd.DataFrame | None, as_of_ms: int, *, cache: dict | None = None,
              key: str = "") -> tuple[str, str] | None:
    """`situation_v1`'in 4h trend × oynaklık rejimi kovası (`_tf_block(full=False)`, aynı saf fonksiyon, W4H kapanmış bar).
    Kova yalnız `as_of`'ta kapanmış son 4h barına bağlıdır; `cache` (çalıştırma içi, (anahtar, son bar) → kova) tekrar
    hesabı önler — sonuç aynıdır."""
    if h4 is None or not len(h4):
        return None
    from ..shared_experience import situation as SIT
    step = TF_MS["4h"]
    last_open = (as_of_ms // step) * step - step
    while last_open + step > as_of_ms:
        last_open -= step
    ck = (key, last_open)
    if cache is not None and ck in cache:
        return cache[ck]
    ts = h4["timestamp"].tolist()
    j = bisect.bisect_right(ts, last_open)
    sub = h4.iloc[max(0, j - (SIT.W4H + 2)):j]
    res = None
    if len(sub) >= 60:
        w = SIT.normalize_rows(sub, "4h", as_of_ms, W=SIT.W4H, source="research_store")
        b = SIT._tf_block(w, full=False)
        tr, vr = b.get("trend"), b.get("vol_regime")
        if tr not in (None, "UNKNOWN") and vr not in (None, "UNKNOWN"):
            res = (str(tr), str(vr))
    if cache is not None:
        cache[ck] = res
    return res


def rc_candidates(plan: TradePlan, h4: pd.DataFrame | None, dur_ms: int, *,
                  cache: dict | None = None) -> tuple[tuple[str, str] | None, list[int]]:
    """Aynı gün saati (k gün önce, k = 1..120), işlem penceresinden önce biten, aynı kovadaki aday anlar (sıralı)."""
    key = plan.symbol
    own = bucket_at(h4, plan.opened_ms - 1, cache=cache, key=key)
    if own is None:
        return None, []
    out = []
    for k in range(1, RC_LOOKBACK_DAYS + 1):
        t = plan.opened_ms - k * TF_MS["1d"]
        if t + dur_ms >= plan.opened_ms:
            continue
        if bucket_at(h4, t - 1, cache=cache, key=key) == own:
            out.append(t)
    return own, sorted(out)


def rc_pick(plan: TradePlan, cands: list[int]) -> list[int]:
    seed = int(hashlib.sha256(f"{plan.key}|{CFGRID_VERSION}".encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    return sorted(rng.sample(sorted(cands), min(RC_N, len(cands))))


def random_control(plan: TradePlan, *, h4: pd.DataFrame | None, bars_5m: Callable[[int, int], pd.DataFrame | None],
                   model: Any, funding: Any, cache: dict | None = None) -> dict[str, Any]:
    """Eşleşmiş rastgele giriş kontrolü (5m). `bars_5m(a, b)`: [a, b] açılışlı 5m barları (depodan)."""
    dur = plan.horizon_ms - plan.opened_ms
    own, cands = rc_candidates(plan, h4, dur, cache=cache)
    res: dict[str, Any] = {"status": "OK", "bucket": list(own) if own else None, "n_candidates": len(cands), "n": 0,
                           "entries": [], "median_r_net": None, "median_g": None}
    if own is None:
        res["status"] = "NO_BUCKET"
        return res
    picks = rc_pick(plan, cands)
    if len(picks) < RC_MIN:
        res["status"] = "RC_TOO_FEW"
    rn, gs = [], []
    stop_pct = plan.stop_dist / plan.ref
    tgt_r = [plan.sgn * (x - plan.ref) / plan.stop_dist for x in plan.targets]
    for t in picks:
        df = bars_5m(t - 2 * TF_MS["5m"], t + dur)
        if df is None or not len(df):
            res["entries"].append({"t": _iso(t), "status": "NO_BARS"})
            continue
        prior = df[df["timestamp"] + TF_MS["5m"] <= t]
        if not len(prior):
            res["entries"].append({"t": _iso(t), "status": "NO_ENTRY_BAR"})
            continue
        e = float(prior["close"].iloc[-1])
        s = e - plan.sgn * stop_pct * e
        tg = [e + plan.sgn * r * abs(e - s) for r in tgt_r]
        path = df[(df["timestamp"] > t) & (df["timestamp"] < t + dur)]
        path, tf = coarsen(path[["timestamp", "open", "high", "low", "close"]].reset_index(drop=True), RC_TF)
        if not len(path):
            res["entries"].append({"t": _iso(t), "status": "NO_BARS"})
            continue
        view = _view(symbol=plan.symbol, side=plan.side, created_ms=t, entry=e, stop=s, targets=tg, label_ms=t + dur - 1,
                     tf=tf, n=len(path))
        try:
            out = replay(view, path, model=model, funding=funding)
        except Exception as exc:  # noqa: BLE001
            res["entries"].append({"t": _iso(t), "status": "REPLAY_ERROR", "note": f"{type(exc).__name__}"[:60]})
            continue
        if out.get("r_net") is None:
            res["entries"].append({"t": _iso(t), "status": "REPLAY_ERROR"})
            continue
        g = gross_pre(out, e, s)
        rn.append(float(out["r_net"]))
        if g is not None:
            gs.append(g)
        res["entries"].append({"t": _iso(t), "status": "OK", "r_net": _r6(out["r_net"]), "g": g, "tf": tf})
    res["n"] = len(rn)
    if len(rn) < RC_MIN:
        res["status"] = "RC_TOO_FEW"
        return res
    res["median_r_net"], res["median_g"] = median(rn), median(gs)
    return res


# ============================================================================ ayrıştırma
def axis_cells(axis: str) -> tuple[str, ...]:
    """Bir eksenin ızgara hücreleri (+ taban `E_ACTUAL`: eksenin "gerçek" noktası da o eksenin ızgarasındadır)."""
    return tuple(dict.fromkeys(("E_ACTUAL",) + tuple(v[0] for v in VARIANTS if v[1] == axis)))


NEUTRAL_AXES = ("entry", "stop", "exit", "manage")


def decompose(plan: TradePlan, cells: dict[str, Cell], rc: dict[str, Any] | None) -> dict[str, Any]:
    """§5.6 R ayrıştırması — belgenin HARFİYEN tanımı (2026-10-08, inceleme M1; eski sıralı zincir eksen etkilerini
    medyanların farkına indirgiyordu ve ~10 kat küçük çıkıyordu). G = kayma öncesi brüt R (dolum riskine göre):

        signal_R = X1 − B   (X1: giriş/stop/çıkış/yönetim eksenlerinin bütün EX_ANTE hücrelerinin G medyanı — "nötr
                             yürütme", 1x; B: eşleşmiş rastgele giriş kontrolünün G medyanı)
        timing_R = X4 − medyan(giriş ekseni)   (stop ve çıkış sabit; eksen gerçek hücreyi de içerir)
        stop_R   = X4 − medyan(stop ekseni)
        exit_R   = X4 − medyan(çıkış ekseni)
        size_lev_R = G_lev − X4   (gerçek kaldıraç/tutar − 1x)
        cost_R   = net_R − G_gerçek   (ölçülen ücret + kayma + fonlama)
        artık    = net_R − Σ bileşenler   (her zaman gösterilir)

    Eksik bileşen 0 sayılmaz (`missing`, artığa kalır). Bütün girdiler varken artık = B + (G_gerçek − G_lev) +
    (Me + Ms + Mx − X1 − 2·X4) — rastgele taban, yeniden oynatma farkı ve eksen medyanlarının toplanamazlığı
    (`residual_parts`). Tek işlemin ayrıştırması GÖZLEMDİR; kanıt toplu istatistikten gelir (§5.7)."""
    def g(cid: str) -> float | None:
        c = cells.get(cid)
        return c.g if c is not None and c.status in ("OK", "MISSED") else None

    def med_axis(axis: str) -> float | None:
        return median([v for v in (g(c) for c in axis_cells(axis)) if v is not None])
    neutral = [g(v[0]) for v in VARIANTS if v[2] == EX_ANTE and v[1] in NEUTRAL_AXES]
    x1 = median([v for v in neutral if v is not None])
    me, ms_, mx = med_axis("entry"), med_axis("stop"), med_axis("exit")
    x4 = g("E_ACTUAL")
    glev = g("Z_ACTUAL_LEV")
    b = (rc or {}).get("median_g") if (rc or {}).get("status") == "OK" else None
    greal = plan.gross_pre_r

    def diff(a: float | None, c: float | None) -> float | None:
        return _r6(a - c) if (a is not None and c is not None) else None
    comp: dict[str, float | None] = {
        "signal_R": diff(x1, b), "timing_R": diff(x4, me), "stop_R": diff(x4, ms_), "exit_R": diff(x4, mx),
        "size_lev_R": diff(glev, x4), "cost_R": _r6(plan.net_r - greal) if greal is not None else None,
    }
    missing = sorted(k for k, v in comp.items() if v is None)
    total = sum(v for v in comp.values() if v is not None)
    resid = _r6(plan.net_r - total)
    full = None not in (b, greal, glev, me, ms_, mx, x1, x4)
    parts = {"random_baseline_R": _r6(b), "replay_gap_R": diff(greal, glev),
             "interaction_R": _r6(me + ms_ + mx - x1 - 2 * x4) if full else None}
    return {"net_R": _r6(plan.net_r), "components": comp, "residual_R": resid, "residual_parts": parts, "missing": missing,
            "medians": {"B": _r6(b), "X1": x1, "entry": me, "stop": ms_, "exit": mx, "X4": _r6(x4), "G_lev": _r6(glev),
                        "G_real": _r6(greal)},
            "identity_ok": abs((resid or 0.0) + total - plan.net_r) <= 1e-6}


def hindsight(cells: dict[str, Cell], actual: Cell | None, src: str) -> dict[str, Any] | None:
    """İşlem başına en iyi hücre (HINDSIGHT): sıralamaya giren hücreler (boyut ekseni hariç), işlemin TEK ölçüsüyle."""
    ok = [c for c in cells.values() if rank_eligible(c) and rank_value(c, src) is not None]
    if not ok:
        return None
    best = max(ok, key=lambda c: (rank_value(c, src), c.id == "E_ACTUAL", c.id))
    base = rank_value(actual, src) if actual is not None else None
    bv = rank_value(best, src)
    return {"label": HINDSIGHT, "cell": best.id, "r_rank": _r6(bv), "rank_src": src,
            "delta_vs_actual": _r6(bv - base) if base is not None else None,
            "note": "işlem başına en iyi hücre: geriye dönük seçim, kural DEĞİL; tek başına ders olmaz"}


def evaluate(env: GridEnv, rc: dict[str, Any] | None) -> dict[str, Any]:
    cells = run_grid(env)
    act = cells.get("E_ACTUAL")
    p = env.plan
    src = rank_source(cells)
    return {"version": CFGRID_VERSION, "registry_sha": CFGRID_SHA, "status": "OK", "tf": env.tf, "rank_src": src,
            "plan": {"ref": _r6(p.ref), "stop": _r6(p.stop), "targets": [_r6(x) for x in p.targets], "level_exit": p.level_exit,
                     "horizon": _iso(p.horizon_ms), "entry_tf": p.entry_tf, "atr": _r6(p.atr), "leverage": p.leverage},
            "baseline_gap_r": _r6(act.r_net - p.net_r) if (act is not None and act.r_net is not None) else None,
            "cells": [cells[c].to_dict() for c in VARIANT_IDS if c in cells] + [cells["Z_ACTUAL_LEV"].to_dict()],
            "hindsight_best": hindsight(cells, act, src), "profitable_cells_hindsight": profitable_cells(cells, src),
            "random_control": rc, "decomposition": decompose(p, cells, rc),
            "signal_excess_R": _r6(p.net_r - rc["median_r_net"]) if rc and rc.get("median_r_net") is not None else None}


def aux_overshoot_window() -> int:
    """`cf_aux_v1`'in aşma penceresi (`learning_cf_aux.OVERSHOOT_WINDOW`; tahmin yalnız son bu kadar gözlemi kullanır)."""
    from ..learning_cf_aux import OVERSHOOT_WINDOW
    return int(OVERSHOOT_WINDOW)


def overshoot_history(prior_rows: list[dict]) -> list[Any]:
    """`cf_aux_v1` aşma tahmininin girdisi: aynı defterin bu işlemden ÖNCE kapanmış kayıtlarının aşma yüzdeleri
    (`journal.cf_inputs.exit_overshoot_pct`), `learning_cf_aux.overshoot_estimate`'in okuduğu biçimde (kapanış sırası)."""
    out = []
    for r in sorted(prior_rows, key=closed_key):
        ov = ((r.get("cf_inputs") or {}).get("exit_overshoot_pct") if isinstance(r.get("cf_inputs"), dict) else None)
        if ov is None:
            continue
        out.append(SimpleNamespace(exit_reason="stop", entry=100.0, side="LONG",
                                   features={"exit_fill": {"basis": "GAP_FILL_AT_FIRST_OBSERVATION", "first_source": "PRICE",
                                                           "stop": 100.0, "first_price": 100.0 - float(ov)}}))
    return out


def regime_fit(row: dict, prior_rows: list[dict]) -> dict[str, Any]:
    """§5.6 `regime_fit` — ders deposu P3'e kadar `JOURNAL_PRIOR`: aynı defter × taktik × giriş kovası, işlemden ÖNCE
    kapanmış satırların ortalama net R'ı (n ≥ 10)."""
    from .attribution import _situ
    own = _situ(row, "situation_entry")
    if None in own:
        return {"source": "JOURNAL_PRIOR", "status": "NO_BUCKET", "n": 0, "mean_r": None}
    ordered = sorted(prior_rows, key=closed_key)
    acc, n = 0.0, 0
    for r in ordered:
        if r.get("tactic") == row.get("tactic") and _situ(r, "situation_entry") == own and r.get("net_r") is not None:
            acc += float(r["net_r"])
            n += 1
    return regime_fit_result(own, n, acc)


def regime_fit_result(own: tuple, n: int, total: float) -> dict[str, Any]:
    return {"source": "JOURNAL_PRIOR", "status": "OK" if n >= REGIME_FIT_MIN_N else "TOO_FEW", "bucket": list(own), "n": n,
            "mean_r": _r6(total / n) if n >= REGIME_FIT_MIN_N else None,
            "note": "ders deposu P3'te; şimdilik önceki günlük satırları (as_of < opened_at)"}


__all__ = ["BOOK_VIEW_MIN_DAYS", "BOOK_VIEW_MIN_N", "CFGRID_SHA", "CFGRID_SPEC", "CFGRID_VERSION", "Cell", "ChainFunding",
           "EX_ANTE", "GridEnv", "HINDSIGHT", "RANK_CONSERVATIVE", "RANK_EXCLUDED_AXES", "RANK_NET", "REFERENCE", "RC_MIN", "RC_N",
           "REPLAY_MAX_BARS", "StoreFunding", "TradePlan", "VARIANTS", "VARIANT_IDS", "axis_cells", "bucket_at", "coarsen",
           "decompose", "evaluate", "gross_pre", "hindsight", "median", "overshoot_history", "profitable_cells",
           "random_control", "rank_eligible", "rank_source", "rank_value", "rc_candidates", "rc_pick", "recorded_funding",
           "regime_fit", "replay", "run_grid", "trade_plan"]
