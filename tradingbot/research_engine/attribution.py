"""P2b — kural kodları `attribution_v1` (§5.4; eşikler MÜHÜRLÜ) — "neden kaybetti / neden kazandı".

Kodlar yalnız işlemin KENDİ verisinden üretilir: günlük satırı (`tj_v1`: maliyet, R, yol ölçüleri, bağlam, giriş kararı),
yolun barları (`pathrec`; işlem barları + 48 barlık kuyruk) ve S2'nin mühürlü depodan okuduğu giriş anı ölçüleri (giriş
diliminde ATR14, çıkıştaki ATR oranı, BTC'nin işlem süresince hareketi, defterin ampirik gürültü bandı — hepsi
`opened_at`'tan ÖNCE kapanmış barlardan ya da işlemin kendi penceresinden; ileriye bakma yok). Alternatif (ızgara)
sonuçları kod tanımına GİRMEZ; onlar ayrı tutulur (`cfgrid`, §5.5). Bir işlem birden çok kod taşıyabilir; birincil kod
`LOSS_PRIORITY` / `WIN_PRIORITY` sırasıyla seçilir. **Tek işlem kodu gözlemdir, neden kanıtı değildir** (kanıt yalnız
toplu istatistikten gelir, §5.7).

Tanımlar `ATTRIBUTION_SPEC`'tedir; sha'sı (`ATTRIBUTION_SHA`) testte ve belgede sabittir — eşik/kural değişikliği =
`attribution_v2` + yeni deneme sayımı (§2.1 madde 7). Belgenin özet tablosunun açık bıraktığı yerlerin OKUMASI (sonuçlar
görülmeden, P2 uygulama notlarında da):

* **Sonuç sınıfı:** `net_r > 0` → WIN (kazanç kodları), aksi halde LOSS (kayıp kodları). R'si olmayan satırda (eski kayıt,
  stopsuz spot) sınıf `net_pnl` işaretidir ve R gerektiren kodlar `not_evaluable`'a yazılır (tahmin edilmez).
* **`r_gross`** (COST_KILLED, TIME_DECAY): kayma ÖNCESİ brüt = günlüğün `gross_r` + `cost_r.slippage_in_fills`
  (kayma dolum fiyatlarının içindedir; aksi halde `_SLIPPAGE` alt türü hiç oluşamazdı). `cost_R` = ücret + kayma +
  net fonlama (§5.6 "ücret + kayma + fonlama"); `funding_R` = −(fonlama maliyeti).
* **Sıraya dayanan kodlar** (`ORDER_CODES`) yol kaynağı bar değilse (`EXTREMES_ONLY`/`MISSING`) ya da bar içi sıra
  belirsizse (`order_ambiguous` veya `ambiguous_bars > 0`, P2 notu 2) değerlendirilmez (`excluded_order`).
* **GIVEBACK'in ikinci kolu** (`capture < 0,3`) yalnız `MFE_R ≥ 0,3` iken: aksi halde neredeyse her kayıp (capture < 0)
  bu kodu alırdı. 0,3 belgenin kendi eşiğidir (WRONG_DIRECTION'ın MFE sınırı; iki kod MFE'ye göre ayrılır).
* **STOPPED_THEN_REVERSED** "orijinal hedefi" `targets[0]`'dır; hedefsiz kurallarda (T2/M2/D4: `targets == []`) +1R
  seviyesidir. Ufuk = kuyruk barları ∩ kuralın tutma sınırı (Box gün sonu, D4/C4 bar sınırı; bilinmiyorsa kuyruk).
* **STOP_TOO_TIGHT gürültü bandı:** aynı defterin bu işlem AÇILMADAN ÖNCE kapanmış son 200 kazancının |MAE %|
  medyanı (en az 30 kazanç; yoksa yalnız ATR kolu). **Giriş dilimi** `ENTRY_TF` (kuralın karar dilimi).
* **LATE_ENTRY** dolum kolu: dolum, sinyal kapanışının ALEYHTE yönde > 0,3 ATR (kuralın ATR'si, yoksa giriş dilimi
  ATR14) ötesinde; sinyal kapanışı `signal_ctx.signal_close` (rehydrate) — yoksa yalnız MAE kolu.
* **BREAKEVEN_SAVED:** çıkış nedeni başa-baş stop (fiyat MFE'den geri dönüp başa-baş seviyesine geldi) ve kazanç.
* **AGAINST_BTC:** giriş anı BTC 4h trendi (`btc_ctx_entry.trend`) işlem yönüne ters (LONG↔DOWN, SHORT↔UP) VE BTC'nin
  giriş→çıkış hareketi işlem yönünün aleyhine.
* **Ana bot kodları** provenance'taki GİRİŞ kararından (`agents_ctx.entry_features`): DISSENT_WAS_RIGHT
  `n_dissent ≥ 1` ve kayıp; TOO_MANY_WARNINGS `n_dissent + n_vetoes ≥ 5` (postmortem eşiği); LOW_RR girişteki `rr < 2`
  (yoksa provenance stop/hedefinden planlanan R/R).
* **NOISE_LOSS:** başka kayıp kodu yok ve brüt hareket maliyet bandında: |r_gross| ≤ cost_R. ("|net_R| maliyet
  bandında"nın harfiyen okuması — net ∈ [−cost_R, 0] — yalnız r_gross ≥ 0 iken mümkündür, r_gross > 0 ise de işlem
  COST_KILLED alır; kod yalnız r_gross = 0'da oluşurdu. Bant bu yüzden brüt hareket üzerindedir: |net_R| ≤ 2 × cost_R.)
  Hiç kod yoksa birincil kod `None` ("sınıflanmadı"; sayılır, uydurulmaz).

Bu modül saf hesaptır (ağ yok, yazım yok); S2 orkestrasyonu `analysis.py`'dedir.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

ATTRIBUTION_VERSION = "attribution_v1"
WIN, LOSS = "WIN", "LOSS"

#: kuralın karar dilimi (giriş dilimi; STOP_TOO_TIGHT / VOL_SPIKE / LATE_ENTRY ATR'si ve cfgrid ATR'si)
ENTRY_TF: dict[str, str] = {
    "strategy_paper": "1d", "strategy_paper_m2": "1d", "strategy_paper_m2x": "1d", "strategy_paper_box": "5m",
    "strategy_paper_trend4h": "4h", "strategy_paper_candle4h": "4h", "strategy_paper_candle4h_strict": "4h",
    "pattern_trader": "4h", "main_fut": "4h", "main_spot": "4h",
}
DEFAULT_ENTRY_TF = "4h"
STOP_EXITS = ("stop",)
BE_EXITS = ("başa-baş stop",)
TARGET_EXITS = ("hedef1", "hedef2")
LIQ_EXITS = ("likidasyon",)
TIME_EXIT_PREFIXES = ("TIME_STOP",)
TIME_EXITS = ("BOX_EOD_FLAT", "horizon")
BAR_SOURCES = ("STORE_1M", "STORE_5M", "POSITION_PATH")

#: MÜHÜRLÜ eşikler (§5.4 tablosu)
TH: dict[str, float] = {
    "WRONG_DIRECTION_MFE_R": 0.3, "STOP_TOO_TIGHT_ATR": 0.5, "NOISE_BAND_MIN_N": 30, "NOISE_BAND_WINDOW": 200,
    "LATE_ENTRY_FILL_ATR": 0.3, "LATE_ENTRY_MAE_R": 0.7, "GIVEBACK_MFE_R": 1.0, "GIVEBACK_CAPTURE": 0.3,
    "GIVEBACK_CAPTURE_MIN_MFE_R": 0.3, "TIME_DECAY_GROSS_R": 0.25, "TIGHT_STOP_COST_R": 0.25, "FUNDING_AGAINST_R": 0.1,
    "VOL_SPIKE_RATIO": 1.5, "CLEAN_ENTRY_MAE_R": 0.3, "TRAIL_CAPTURE": 0.6, "FUNDING_TAILWIND_R": 0.1,
    "COST_EFFICIENT_R": 0.05, "LUCKY_WIN_MAE_R": 0.8, "TOO_MANY_WARNINGS_N": 5, "LOW_RR": 2.0, "DISSENT_MIN": 1,
}

LOSS_RULES: dict[str, str] = {
    "LIQUIDATION_LEVERAGE": "exit_basis == LIQUIDATION or exit_reason == likidasyon (stoptan önce likidasyon)",
    "GAP_FILL": "exit_basis == GAP",
    "COST_KILLED_FEE": "r_gross_pre > 0 >= net_r; en büyük maliyet payı ücret",
    "COST_KILLED_FUNDING": "r_gross_pre > 0 >= net_r; en büyük maliyet payı fonlama",
    "COST_KILLED_SLIPPAGE": "r_gross_pre > 0 >= net_r; en büyük maliyet payı kayma",
    "STOPPED_THEN_REVERSED": "exit_reason == stop and kuyruk (orijinal ufuk içinde) orijinal hedefe ulaştı "
                             "(targets[0]; hedefsiz kuralda +1R)",
    "WRONG_DIRECTION": "mfe_r < 0.3 and exit_reason == stop",
    "STOP_TOO_TIGHT": "|entry - initial_stop| < 0.5 * ATR14(giriş dilimi) or stop_dist_pct < defter gürültü bandı "
                      "(önceki 200 kazancın |mae_pct| medyanı, n >= 30)",
    "LATE_ENTRY": "dolum sinyal kapanışının aleyhte > 0.3 ATR ötesinde; or (order == MAE_FIRST and mae_r < -0.7)",
    "GIVEBACK": "(mfe_r >= 1 and net_r <= 0) or (mfe_r >= 0.3 and capture_ratio < 0.3)",
    "PROFIT_NOT_TAKEN": "mfe_r >= TP1 mesafesi (R) and not tp1_done",
    "TIME_DECAY": "zaman çıkışı (TIME_STOP*, BOX_EOD_FLAT, horizon) and |r_gross_pre| < 0.25",
    "TIGHT_STOP_COST_MULTIPLIER": "fee_r + slippage_r > 0.25 (1/stop ile ölçeklenen maliyet)",
    "FUNDING_AGAINST": "funding_R < -0.1",
    "REGIME_SHIFT": "situation_v1 h4_trend veya h4_vol_regime girişten çıkışa değişti",
    "AGAINST_BTC": "btc_ctx_entry.trend işlem yönüne ters and BTC giriş->çıkış aleyhte",
    "VOL_SPIKE": "atr_pct(çıkış) >= 1.5 * atr_pct(giriş), giriş dilimi",
    "DISSENT_WAS_RIGHT": "ana bot: giriş kararında n_dissent >= 1",
    "TOO_MANY_WARNINGS": "ana bot: giriş kararında n_dissent + n_vetoes >= 5",
    "LOW_RR": "ana bot: giriş kararında rr < 2",
    "NOISE_LOSS": "başka kayıp kodu yok and |r_gross_pre| <= cost_R (brüt hareket maliyet bandında)",
}
WIN_RULES: dict[str, str] = {
    "LUCKY_GAP": "exit_reason hedef and exit_basis == GAP",
    "LUCKY_WIN": "order == MAE_FIRST and mae_r <= -0.8 (derslerde FRAGILE / zayıf kazanç)",
    "TREND_CONTINUATION": "order == MFE_FIRST and exit_reason hedef and situation_v1 trend/vol kovası değişmedi",
    "MEAN_REVERSION_DONE": "family == FADE and (exit_reason hedef or mfe >= box_mid)",
    "BREAKEVEN_SAVED": "exit_reason == başa-baş stop",
    "TRAIL_CAPTURE": "capture_ratio >= 0.6",
    "CLEAN_ENTRY": "+1R'a ulaşmadan önce en kötü MAE_R > -0.3",
    "FUNDING_TAILWIND": "funding_R > +0.1",
    "COST_EFFICIENT": "cost_R < 0.05",
}
LOSS_PRIORITY: tuple[str, ...] = (
    "LIQUIDATION_LEVERAGE", "GAP_FILL", "COST_KILLED_FEE", "COST_KILLED_FUNDING", "COST_KILLED_SLIPPAGE",
    "STOPPED_THEN_REVERSED", "WRONG_DIRECTION", "STOP_TOO_TIGHT", "LATE_ENTRY", "GIVEBACK", "PROFIT_NOT_TAKEN",
    "TIME_DECAY", "TIGHT_STOP_COST_MULTIPLIER", "FUNDING_AGAINST", "REGIME_SHIFT", "AGAINST_BTC", "VOL_SPIKE",
    "DISSENT_WAS_RIGHT", "TOO_MANY_WARNINGS", "LOW_RR", "NOISE_LOSS")
WIN_PRIORITY: tuple[str, ...] = ("LUCKY_GAP", "LUCKY_WIN", "TREND_CONTINUATION", "MEAN_REVERSION_DONE", "BREAKEVEN_SAVED",
                                 "TRAIL_CAPTURE", "CLEAN_ENTRY", "FUNDING_TAILWIND", "COST_EFFICIENT")
ORDER_CODES: tuple[str, ...] = ("LATE_ENTRY:MAE", "LUCKY_WIN", "TREND_CONTINUATION", "CLEAN_ENTRY")
MAIN_BOOKS = ("main_fut", "main_spot")

ATTRIBUTION_SPEC: dict[str, Any] = {
    "id": ATTRIBUTION_VERSION, "outcome": "WIN if net_r > 0 else LOSS; no R -> sign(net_pnl), R codes not_evaluable",
    "r_gross_pre": "gross_r + cost_r.slippage_in_fills", "cost_R": "fee + slippage + funding (signed)",
    "funding_R": "-cost_r.funding", "order_exclusion": "path_source not in BAR_SOURCES or order_ambiguous or ambiguous_bars > 0",
    "thresholds": TH, "loss_rules": LOSS_RULES, "win_rules": WIN_RULES, "loss_priority": list(LOSS_PRIORITY),
    "win_priority": list(WIN_PRIORITY), "order_codes": list(ORDER_CODES), "entry_tf": ENTRY_TF,
    "exits": {"stop": list(STOP_EXITS), "be": list(BE_EXITS), "target": list(TARGET_EXITS), "liq": list(LIQ_EXITS),
              "time_prefix": list(TIME_EXIT_PREFIXES), "time": list(TIME_EXITS)},
    "bar_sources": list(BAR_SOURCES), "main_books": list(MAIN_BOOKS),
    "horizon": "tail bars (phase 1) with ts < opened + max_hold (Box: end of UTC day; bars x tf); unknown -> whole tail",
}
ATTRIBUTION_SHA = hashlib.sha256(json.dumps(ATTRIBUTION_SPEC, sort_keys=True, ensure_ascii=False,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


# ============================================================================ girdiler
@dataclass
class AttrInputs:
    """S2'nin depodan / önceki satırlardan hesapladığı, koda giren ölçüler (hepsi ileriye bakmasız)."""
    atr_entry: float | None = None            # ATR14, giriş dilimi, opened_at'tan önce kapanmış barlar
    atr_pct_entry: float | None = None        # ATR14 / kapanış (giriş)
    atr_pct_exit: float | None = None         # ATR14 / kapanış (closed_at'ta kapanmış barlar)
    btc_move_pct: float | None = None         # BTC giriş → çıkış (%; 5m kapanışları)
    noise_band_pct: float | None = None       # defterin önceki kazançlarının |mae_pct| medyanı
    noise_band_n: int = 0
    entry_tf: str = DEFAULT_ENTRY_TF
    bars: pd.DataFrame | None = None          # yol barları (phase 0 işlem, 1 kuyruk)
    notes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"atr_entry": _r6(self.atr_entry), "atr_pct_entry": _r6(self.atr_pct_entry), "atr_pct_exit": _r6(self.atr_pct_exit),
                "btc_move_pct": _r6(self.btc_move_pct), "noise_band_pct": _r6(self.noise_band_pct),
                "noise_band_n": int(self.noise_band_n), "entry_tf": self.entry_tf, "notes": dict(sorted(self.notes.items()))}


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


def _f(x: Any) -> float | None:
    return _r6(x)


def closed_key(r: dict) -> tuple[str, str]:
    """Önceki satırların tek sırası: (normalleştirilmiş `closed_at`, `trade_key`)."""
    from .ledgers import iso, parse_ts
    t = parse_ts(r.get("closed_at"))
    return (iso(t) if t is not None else "", str(r.get("trade_key")))


def side_sign(row: dict) -> int:
    return -1 if str(row.get("side") or "").upper() in ("SHORT", "SELL") else 1


def is_time_exit(reason: str) -> bool:
    return reason in TIME_EXITS or any(reason.startswith(p) for p in TIME_EXIT_PREFIXES)


def outcome_of(row: dict) -> str:
    nr = _f(row.get("net_r"))
    v = nr if nr is not None else _f(row.get("net_pnl"))
    return WIN if (v is not None and v > 0) else LOSS


def cost_parts(row: dict) -> dict[str, float | None]:
    """R cinsinden maliyet parçaları (pozitif = maliyet) ve brüt (kayma öncesi)."""
    c = row.get("cost_r") if isinstance(row.get("cost_r"), dict) else {}
    fee, fund, slip = _f(c.get("fee")), _f(c.get("funding")), _f(c.get("slippage_in_fills"))
    gross = _f(row.get("gross_r"))
    if fee is None or fund is None or slip is None or gross is None:
        return {"fee": fee, "funding": fund, "slippage": slip, "cost_all": None, "gross_pre": None}
    return {"fee": fee, "funding": fund, "slippage": slip, "cost_all": _r6(fee + slip + fund), "gross_pre": _r6(gross + slip)}


def rpu_of(row: dict) -> float | None:
    """Birim risk (fiyat): risk_usdt / qty (= |giriş − ilk stop|)."""
    risk, qty = _f(row.get("risk_usdt")), _f(row.get("qty"))
    if risk is None or not qty or risk <= 0:
        return None
    return risk / qty


def max_hold_end_ms(row: dict, opened_ms: int) -> int | None:
    """Kuralın tutma sınırının bittiği an (ms): Box gün sonu (UTC), `{bars, tf}` → bar × dilim; bilinmiyorsa None."""
    mh = row.get("max_hold") if isinstance(row.get("max_hold"), dict) else None
    if not mh:
        return None
    if mh.get("rule") == "EOD_UTC":
        return (opened_ms // 86_400_000 + 1) * 86_400_000
    bars, tf = mh.get("bars"), mh.get("tf")
    if bars and tf in TF_MS:
        return opened_ms + int(bars) * TF_MS[tf]
    return None


def _ms(ts: Any) -> int | None:
    from .ledgers import parse_ts
    t = parse_ts(ts)
    return int(t.timestamp() * 1000) if t is not None else None


def _situ(row: dict, key: str) -> tuple[str | None, str | None]:
    s = row.get(key) if isinstance(row.get(key), dict) else {}
    tr, vr = s.get("h4_trend"), s.get("h4_vol_regime")
    tr = None if tr in (None, "UNKNOWN") else str(tr)
    vr = None if vr in (None, "UNKNOWN") else str(vr)
    return tr, vr


def order_ok(row: dict) -> bool:
    """Sıraya dayanan kodlar değerlendirilebilir mi (P2 notu 2)."""
    p = row.get("path") if isinstance(row.get("path"), dict) else {}
    src = row.get("path_source") or p.get("path_source")
    if src not in BAR_SOURCES:
        return False
    if p.get("order_ambiguous") or int(row.get("ambiguous_bars") or 0) > 0:
        return False
    return row.get("order") in ("MFE_FIRST", "MAE_FIRST")


def original_target_r(row: dict) -> float | None:
    """Orijinal hedefin R uzaklığı (girişten): `targets[0]`; hedefsiz kuralda (`targets == []`) +1R; bilinmiyorsa None."""
    t = row.get("targets")
    rpu = rpu_of(row)
    entry = _f(row.get("entry_fill"))
    if rpu is None or entry is None:
        return None
    if isinstance(t, list) and t:
        return side_sign(row) * (float(t[0]) - entry) / rpu
    if isinstance(t, list) and not t:
        return 1.0
    return None


def tail_reached_r(row: dict, bars: pd.DataFrame | None, level_r: float) -> bool | None:
    """Kuyruk (phase 1; orijinal ufuk içinde) girişten `level_r` R'ye ulaştı mı (lehte uç)."""
    if bars is None or not len(bars) or "phase" not in bars.columns:
        return None
    rpu, entry = rpu_of(row), _f(row.get("entry_fill"))
    om = _ms(row.get("opened_at"))
    if rpu is None or entry is None or om is None:
        return None
    tail = bars[bars["phase"] == 1]
    end = max_hold_end_ms(row, om)
    if end is not None:
        tail = tail[tail["timestamp"] < end]
    if not len(tail):
        return False
    sg = side_sign(row)
    fav = float(tail["high"].max()) if sg > 0 else float(tail["low"].min())
    return sg * (fav - entry) / rpu >= level_r


def mae_before_1r(row: dict, bars: pd.DataFrame | None) -> float | None:
    """+1R'a ilk ulaşılan bara kadar (o bar dahil; bar içi kural: ters uç önce) en kötü ters hareket (R); 1R yoksa None."""
    if bars is None or not len(bars):
        return None
    rpu, entry = rpu_of(row), _f(row.get("entry_fill"))
    if rpu is None or entry is None:
        return None
    tb = bars[bars["phase"] == 0] if "phase" in bars.columns else bars
    sg = side_sign(row)
    worst = 0.0
    for h, lo in zip(tb["high"].tolist(), tb["low"].tolist()):
        adv = (float(lo) if sg > 0 else float(h))
        fav = (float(h) if sg > 0 else float(lo))
        worst = min(worst, sg * (adv - entry) / rpu)
        if sg * (fav - entry) / rpu >= 1.0:
            return worst
    return None


def reached_level(row: dict, bars: pd.DataFrame | None, level: float) -> bool | None:
    """İşlem barlarının lehte ucu `level` fiyatına ulaştı mı."""
    if bars is None or not len(bars):
        return None
    tb = bars[bars["phase"] == 0] if "phase" in bars.columns else bars
    if not len(tb):
        return None
    sg = side_sign(row)
    fav = float(tb["high"].max()) if sg > 0 else float(tb["low"].min())
    return sg * (fav - level) >= 0


# ============================================================================ kodlar
def attribute(row: dict, inp: AttrInputs | None = None) -> dict[str, Any]:
    """Tek günlük satırının `attribution_v1` kodları (saf; aynı girdi aynı çıktı)."""
    inp = inp or AttrInputs()
    out_cls = outcome_of(row)
    has_r = _f(row.get("net_r")) is not None and rpu_of(row) is not None
    net = _f(row.get("net_r"))
    mfe, mae = _f(row.get("mfe_r")), _f(row.get("mae_r"))
    cap = _f(row.get("capture_ratio"))
    reason = str(row.get("exit_reason") or "")
    basis = str(row.get("exit_basis") or "")
    cp = cost_parts(row)
    sg = side_sign(row)
    ord_ok = order_ok(row)
    fired: dict[str, dict[str, Any]] = {}
    not_eval: list[str] = []

    def need(code: str, ok: bool) -> bool:
        if not ok:
            not_eval.append(code)
        return ok

    if out_cls == LOSS:
        if basis == "LIQUIDATION" or reason in LIQ_EXITS:
            fired["LIQUIDATION_LEVERAGE"] = {"exit_basis": basis, "exit_reason": reason}
        if basis == "GAP":
            fired["GAP_FILL"] = {"exit_basis": basis}
        if need("COST_KILLED", has_r and cp["gross_pre"] is not None):
            if cp["gross_pre"] > 0 >= net:
                parts = (("FEE", cp["fee"]), ("FUNDING", cp["funding"]), ("SLIPPAGE", cp["slippage"]))
                best = max(parts, key=lambda x: x[1])          # eşitlikte sıra: ücret, fonlama, kayma
                fired["COST_KILLED_" + best[0]] = {"r_gross_pre": cp["gross_pre"], "net_r": net,
                                                   "fee_r": cp["fee"], "funding_r": cp["funding"], "slippage_r": cp["slippage"]}
        if reason in STOP_EXITS:
            tr = original_target_r(row)
            if need("STOPPED_THEN_REVERSED", has_r and tr is not None and inp.bars is not None):
                hit = tail_reached_r(row, inp.bars, tr)
                if hit:
                    fired["STOPPED_THEN_REVERSED"] = {"target_r": _r6(tr)}
                elif hit is None:
                    not_eval.append("STOPPED_THEN_REVERSED")
            if need("WRONG_DIRECTION", mfe is not None):
                if mfe < TH["WRONG_DIRECTION_MFE_R"]:
                    fired["WRONG_DIRECTION"] = {"mfe_r": mfe}
        entry, stop = _f(row.get("entry_fill")), _f(row.get("initial_stop"))
        if need("STOP_TOO_TIGHT", entry is not None and stop is not None and (
                inp.atr_entry is not None or inp.noise_band_pct is not None)):
            dist = abs(entry - stop)
            ev: dict[str, Any] = {}
            if inp.atr_entry is not None and inp.atr_entry > 0 and dist < TH["STOP_TOO_TIGHT_ATR"] * inp.atr_entry:
                ev["stop_dist_atr"] = _r6(dist / inp.atr_entry)
            sdp = _f(row.get("stop_dist_pct"))
            if (inp.noise_band_pct is not None and inp.noise_band_n >= TH["NOISE_BAND_MIN_N"] and sdp is not None
                    and sdp < inp.noise_band_pct):
                ev.update(stop_dist_pct=sdp, noise_band_pct=_r6(inp.noise_band_pct), noise_band_n=inp.noise_band_n)
            if ev:
                fired["STOP_TOO_TIGHT"] = ev
        sc = (row.get("signal_ctx") or {}).get("signal_close") if isinstance(row.get("signal_ctx"), dict) else None
        atr_rule = (row.get("signal_ctx") or {}).get("atr14") if isinstance(row.get("signal_ctx"), dict) else None
        atr_l = _f(atr_rule) or inp.atr_entry
        late: dict[str, Any] = {}
        if sc is not None and entry is not None and atr_l:
            beyond = sg * (entry - float(sc)) / atr_l
            if beyond > TH["LATE_ENTRY_FILL_ATR"]:
                late["fill_beyond_signal_atr"] = _r6(beyond)
        if ord_ok and mae is not None and row.get("order") == "MAE_FIRST" and mae < -TH["LATE_ENTRY_MAE_R"]:
            late["mae_r_before_mfe"] = mae
        if late:
            fired["LATE_ENTRY"] = late
        elif sc is None and not ord_ok:
            not_eval.append("LATE_ENTRY")
        if need("GIVEBACK", has_r and mfe is not None):
            if (mfe >= TH["GIVEBACK_MFE_R"] and net <= 0) or (
                    mfe >= TH["GIVEBACK_CAPTURE_MIN_MFE_R"] and cap is not None and cap < TH["GIVEBACK_CAPTURE"]):
                fired["GIVEBACK"] = {"mfe_r": mfe, "capture_ratio": cap, "net_r": net}
        t = row.get("targets")
        rpu = rpu_of(row)
        if isinstance(t, list) and t and rpu and entry is not None and mfe is not None:
            tp1_r = sg * (float(t[0]) - entry) / rpu
            if tp1_r > 0 and mfe >= tp1_r and not row.get("tp1_done"):
                fired["PROFIT_NOT_TAKEN"] = {"mfe_r": mfe, "tp1_r": _r6(tp1_r)}
        if is_time_exit(reason) and need("TIME_DECAY", cp["gross_pre"] is not None):
            if abs(cp["gross_pre"]) < TH["TIME_DECAY_GROSS_R"]:
                fired["TIME_DECAY"] = {"r_gross_pre": cp["gross_pre"], "exit_reason": reason}
        if need("TIGHT_STOP_COST_MULTIPLIER", cp["fee"] is not None and cp["slippage"] is not None):
            if cp["fee"] + cp["slippage"] > TH["TIGHT_STOP_COST_R"]:
                fired["TIGHT_STOP_COST_MULTIPLIER"] = {"fee_r": cp["fee"], "slippage_r": cp["slippage"],
                                                       "stop_dist_pct": _f(row.get("stop_dist_pct"))}
        if cp["funding"] is not None and cp["funding"] > TH["FUNDING_AGAINST_R"]:
            fired["FUNDING_AGAINST"] = {"funding_R": _r6(-cp["funding"])}
        _regime(row, fired, not_eval, "REGIME_SHIFT", changed=True)
        bt = (row.get("btc_ctx_entry") or {}).get("trend") if isinstance(row.get("btc_ctx_entry"), dict) else None
        if need("AGAINST_BTC", bt is not None and inp.btc_move_pct is not None):
            against = (sg > 0 and bt == "DOWN") or (sg < 0 and bt == "UP")
            if against and sg * inp.btc_move_pct < 0:
                fired["AGAINST_BTC"] = {"btc_trend_entry": bt, "btc_move_pct": _r6(inp.btc_move_pct)}
        if need("VOL_SPIKE", inp.atr_pct_entry is not None and inp.atr_pct_exit is not None):
            if inp.atr_pct_entry > 0 and inp.atr_pct_exit >= TH["VOL_SPIKE_RATIO"] * inp.atr_pct_entry:
                fired["VOL_SPIKE"] = {"atr_ratio": _r6(inp.atr_pct_exit / inp.atr_pct_entry)}
        if row.get("book") in MAIN_BOOKS:
            _main_codes(row, fired, not_eval)
        if not fired and has_r and cp["cost_all"] is not None and abs(cp["gross_pre"]) <= cp["cost_all"]:
            fired["NOISE_LOSS"] = {"net_r": net, "r_gross_pre": cp["gross_pre"], "cost_R": cp["cost_all"]}
        primary = next((c for c in LOSS_PRIORITY if c in fired), None)
        codes_loss = [c for c in LOSS_PRIORITY if c in fired]
        codes_win: list[str] = []
    else:
        if reason in TARGET_EXITS and basis == "GAP":
            fired["LUCKY_GAP"] = {"exit_basis": basis}
        if ord_ok and row.get("order") == "MAE_FIRST" and mae is not None and mae <= -TH["LUCKY_WIN_MAE_R"]:
            fired["LUCKY_WIN"] = {"mae_r": mae, "alias": "FRAGILE"}
        if ord_ok and row.get("order") == "MFE_FIRST" and reason in TARGET_EXITS:
            _regime(row, fired, not_eval, "TREND_CONTINUATION", changed=False)
        if str(row.get("family") or "") == "FADE":
            mid = (row.get("signal_ctx") or {}).get("box_mid") if isinstance(row.get("signal_ctx"), dict) else None
            hit_mid = reached_level(row, inp.bars, float(mid)) if mid is not None else None
            if reason in TARGET_EXITS or hit_mid:
                fired["MEAN_REVERSION_DONE"] = {"exit_reason": reason, "reached_mid": hit_mid}
        if reason in BE_EXITS:
            fired["BREAKEVEN_SAVED"] = {"exit_reason": reason}
        if cap is not None and cap >= TH["TRAIL_CAPTURE"]:
            fired["TRAIL_CAPTURE"] = {"capture_ratio": cap}
        if ord_ok and need("CLEAN_ENTRY", inp.bars is not None and has_r):
            w = mae_before_1r(row, inp.bars)
            if w is not None and w > -TH["CLEAN_ENTRY_MAE_R"]:
                fired["CLEAN_ENTRY"] = {"mae_r_before_1r": _r6(w)}
        if cp["funding"] is not None and cp["funding"] < -TH["FUNDING_TAILWIND_R"]:
            fired["FUNDING_TAILWIND"] = {"funding_R": _r6(-cp["funding"])}
        if cp["cost_all"] is not None and cp["cost_all"] < TH["COST_EFFICIENT_R"]:
            fired["COST_EFFICIENT"] = {"cost_R": cp["cost_all"]}
        primary = next((c for c in WIN_PRIORITY if c in fired), None)
        codes_win = [c for c in WIN_PRIORITY if c in fired]
        codes_loss = []
    return {"version": ATTRIBUTION_VERSION, "registry_sha": ATTRIBUTION_SHA, "outcome": out_cls, "net_r": net,
            "has_r": has_r, "codes_loss": codes_loss, "codes_win": codes_win, "primary_code": primary,
            "evidence": {k: fired[k] for k in sorted(fired)}, "not_evaluable": sorted(set(not_eval)),
            "excluded_order": not ord_ok, "costs_r": cp, "inputs": inp.to_dict()}


def _regime(row: dict, fired: dict, not_eval: list[str], code: str, *, changed: bool) -> None:
    te, ve = _situ(row, "situation_entry")
    tx, vx = _situ(row, "situation_exit")
    if None in (te, ve, tx, vx):
        not_eval.append(code)
        return
    diff = (te != tx) or (ve != vx)
    if diff == changed:
        fired[code] = {"entry": [te, ve], "exit": [tx, vx]}


def _main_codes(row: dict, fired: dict, not_eval: list[str]) -> None:
    ac = row.get("agents_ctx") if isinstance(row.get("agents_ctx"), dict) else None
    ef = (ac or {}).get("entry_features") if isinstance((ac or {}).get("entry_features"), dict) else {}
    nd, nv, rr = _f(ef.get("n_dissent")), _f(ef.get("n_vetoes")), _f(ef.get("rr"))
    if nd is None:
        not_eval.append("DISSENT_WAS_RIGHT")
    elif nd >= TH["DISSENT_MIN"]:
        fired["DISSENT_WAS_RIGHT"] = {"n_dissent": nd}
    if nd is None and nv is None:
        not_eval.append("TOO_MANY_WARNINGS")
    elif (nd or 0) + (nv or 0) >= TH["TOO_MANY_WARNINGS_N"]:
        fired["TOO_MANY_WARNINGS"] = {"n_dissent": nd, "n_vetoes": nv}
    src = "entry_features.rr"
    if rr is None:
        t, stop, ref = row.get("targets"), _f(row.get("initial_stop")), _f(row.get("ref_price")) or _f(row.get("entry_fill"))
        if isinstance(t, list) and t and stop is not None and ref is not None and ref != stop:
            rr, src = abs(float(t[0]) - ref) / abs(ref - stop), "provenance stop/hedef"
    if rr is None:
        not_eval.append("LOW_RR")
    elif rr < TH["LOW_RR"]:
        fired["LOW_RR"] = {"rr": _r6(rr), "source": src}


# ============================================================================ önceki satırlardan ölçüler (ileriye bakmasız)
def noise_band(prior_rows: list[dict]) -> tuple[float | None, int]:
    """Defterin ampirik gürültü bandı: verilen ÖNCEKİ satırlardan (çağıran `closed_at < opened_at` süzer) en yeni
    `NOISE_BAND_WINDOW` kazancın |mae_pct| medyanı. (bant %, n)."""
    vals = []
    for r in sorted(prior_rows, key=closed_key, reverse=True):
        if outcome_of(r) != WIN:
            continue
        mp = ((r.get("path") or {}).get("mae_pct") if isinstance(r.get("path"), dict) else None)
        v = _f(mp)
        if v is None:
            continue
        vals.append(abs(v))
        if len(vals) >= int(TH["NOISE_BAND_WINDOW"]):
            break
    if not vals:
        return None, 0
    vals.sort()
    n = len(vals)
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    return _r6(med), n


def registry() -> dict[str, Any]:
    return {"spec": ATTRIBUTION_SPEC, "sha": ATTRIBUTION_SHA}


#: Türkçe kısa açıklamalar (özet ve sorgu; şablon, LLM yok)
CODE_TR: dict[str, str] = {
    "LIQUIDATION_LEVERAGE": "stoptan önce likidasyon (kaldıraç)", "GAP_FILL": "stop boşlukla doldu",
    "COST_KILLED_FEE": "brüt kârı ücret yedi", "COST_KILLED_FUNDING": "brüt kârı fonlama yedi",
    "COST_KILLED_SLIPPAGE": "brüt kârı kayma yedi", "STOPPED_THEN_REVERSED": "stop oldu, sonra hedefe döndü",
    "WRONG_DIRECTION": "yön yanlış (MFE < 0,3R, stop)", "STOP_TOO_TIGHT": "stop gürültüye göre dar",
    "LATE_ENTRY": "geç giriş", "GIVEBACK": "kârı geri verdi", "PROFIT_NOT_TAKEN": "TP1'e değdi, alınmadı",
    "TIME_DECAY": "zaman çıkışı, hareket yok", "TIGHT_STOP_COST_MULTIPLIER": "dar stop maliyeti büyüttü",
    "FUNDING_AGAINST": "fonlama aleyhte", "REGIME_SHIFT": "rejim değişti", "AGAINST_BTC": "BTC'ye ters, BTC aleyhte",
    "VOL_SPIKE": "oynaklık sıçradı", "DISSENT_WAS_RIGHT": "karşı görüş haklı çıktı",
    "TOO_MANY_WARNINGS": "girişte çok uyarı", "LOW_RR": "girişte R/R düşük", "NOISE_LOSS": "maliyet bandında gürültü",
    "LUCKY_GAP": "hedef boşlukla doldu (şans)", "LUCKY_WIN": "önce derin MAE (zayıf kazanç)",
    "TREND_CONTINUATION": "trend sürdü, hedef", "MEAN_REVERSION_DONE": "ortaya dönüş tamamlandı",
    "BREAKEVEN_SAVED": "başa-baş kurtardı", "TRAIL_CAPTURE": "hareketin çoğu alındı", "CLEAN_ENTRY": "temiz giriş",
    "FUNDING_TAILWIND": "fonlama lehte", "COST_EFFICIENT": "maliyet düşük",
}


__all__ = ["ATTRIBUTION_SHA", "ATTRIBUTION_SPEC", "ATTRIBUTION_VERSION", "AttrInputs", "CODE_TR", "DEFAULT_ENTRY_TF", "ENTRY_TF",
           "LOSS", "LOSS_PRIORITY", "LOSS_RULES", "ORDER_CODES", "TH", "WIN", "WIN_PRIORITY", "WIN_RULES", "attribute",
           "closed_key", "cost_parts", "is_time_exit", "max_hold_end_ms", "noise_band", "order_ok", "original_target_r", "outcome_of",
           "registry", "rpu_of", "side_sign"]
