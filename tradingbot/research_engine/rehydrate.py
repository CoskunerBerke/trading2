"""P2 — kural bağlamının yeniden kurulması (`rehydrate`; §5.2) ve config dönemi etiketi.

Strateji defterleri kapanışta HEDEFLERİ ve sinyal bağlamını düşürür; ilk stop ölçülmüştür (`features.initial_stop`).
Motor, defterin deterministik kural fonksiyonunu araştırma deposunun barları üzerinde, kaydın OKUDUĞU bar anına göre
yeniden çalıştırır ve yalnız hedefleri ve sinyal bağlamını geri kazanır. Stop her durumda ÖLÇÜLMÜŞ olandır.

Defterler (§5.2 tablosu; C4S C4'ün aynı kuralıdır):

| Defter (state klasörü) | Kural | Geri kazanılan |
|---|---|---|
| `strategy_paper` (T2), `strategy_paper_m2` (M2) | `ema200_trend` | `ema200`, `atr14`, rejim, (M2) referans kapanış; hedef yok |
| `strategy_paper_box` (Box) | `box_theory` | `box_high/low/mid`, konum, gün aralığı, `params_label`, hedefler |
| `strategy_paper_trend4h` (D4) | `donchian_trend` | `channel_high20`, ATR sınırları; hedef yok |
| `strategy_paper_candle4h(_strict)` (C4/C4S) | `candle_dsl` + kayıt | sinyal bloğu (varyasyon, formasyon uçları, ATR), hedefler |

**Aynı okuma.** Barlar canlı motorun çerçevesiyle aynı biçimde kurulur: kaydın `features.data_source.bars[tf]`'si
(kuralın gerçekten okuduğu son KAPANMIŞ bar) ile biten, canlı perp çerçevesinin kapanmış bar sayısı kadar satır
(`engine.TradingEngine.PERP_FRAME_LIMITS` − oluşan bar: 1d 399, 4h 699, 5m 499); günlük çerçeveye `add_snapshot_indicators`
ile aynı fonksiyonlar (`indicators.ema`/`atr`) `ema200`/`atr14` sütunlarını ekler; satır okuması kuralın kendi
okuyucularıdır (`paper_rules.daily_rows`/`intraday_rows`, `candle_book.window_rows`). Karar anı = `opened_at` (turun
`now`'ı). Geometri kuralın kendi gösterim fonksiyonlarıyla (`rule_state`: karar ile AYNI yol) ya da C4'te
`candle_dsl.detect_last` ile hesaplanır.

**Kabul koşulu (§5.2; okuma — P2 uygulama notları):** yeniden hesaplanan ilk stop ölçülmüş `features.initial_stop` ile
**1 tick** içinde eşleşmelidir. "Giriş referansı" şöyle denetlenir: defter girişi canlı mark'tan yapıldığı için (dolum
`fills[entry].ref_price`) kural fiyatına eşit olamaz; bu yüzden (a) sinyal barı KİMLİĞİ — depoda kaydın okuduğu bar
(`data_source.bars[tf]`) tam olarak bulunmalı (doğru bar), (b) kuralın giriş referansı (`signal_close`) işlem
hafızasındaki ÖLÇÜLMÜŞ kopyasıyla (`trade_memory` `features.signal_close`) varsa 1 tick, (c) C4'te formasyon uçları
(`features.candle_variation.pattern_high/low`) 1 tick ve `definition_sha` eşitliği, (d) defter girişinin stopun doğru
tarafında olması. Biri tutmazsa `RECONSTRUCT_FAILED` (neden yazılır), hedefler ve `signal_ctx` `MISSING` kalır;
tahmin edilmez. Tick: kaydın giriş dolum fiyatının ondalık hassasiyeti (defter dolumu `price_tick`'e kuantize eder;
`SymbolFilters` kayda yazılmaz).

**Parametreler:** canlı config'in HAM YAML'ı (`rawconfig`; `strategy_paper` + `extra[]`: `atr_mult`, `rule_params`);
yoksa kuralın varsayılanı (`params_source=DEFAULT`). Box'ta `min_stop_pct` bir FİLTREDİR (geometri değil; öğrenme
modunda dönemle değişti): geometri hesabında 0 alınır (`filter_params_ignored`). Geometri parametresi bir dönemde
değiştiyse stop tutmaz ve işlem dürüstçe `RECONSTRUCT_FAILED` olur.

**Config dönemi (§6.2):** mühürlü `library/config_epochs.json` P3'tedir. O yokken satırın dönemi belgenin kayıtlı sahip
kararlarından GEÇİCİ olarak türetilir (`PROVISIONAL_BOUNDARIES`; 2026-09-30 Box `slots`, 2026-10-03 Box `min_stop_pct`
+ `record_selectivity`); etiket `RECONSTRUCTED`, kaynak `PROVISIONAL_DOC_BOUNDARIES`. P3 dosyası varsa o kullanılır.

Bu modül ağ kullanmaz, hiçbir yere yazmaz; yalnız `pathrec.BarSource` üzerinden mühürlü depoyu okur.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from .ledgers import dec_or_none, parse_ts
from .pathrec import BarSource

UTC = timezone.utc
REHYDRATE_VERSION = "rehydrate_v1"
OK, FAILED, NOT_APPLICABLE, NO_DATA = "OK", "RECONSTRUCT_FAILED", "NOT_APPLICABLE", "NO_DATA"

#: state klasörü (defter kimliği) → kural adı (paper_rules varyantı)
RULE_BOOKS: dict[str, str] = {
    "strategy_paper": "t2_trend_regime", "strategy_paper_m2": "m2_tsmom28", "strategy_paper_box": "b1_box_fade",
    "strategy_paper_trend4h": "d4_donchian_20_10", "strategy_paper_candle4h": "c4_candle_variations",
    "strategy_paper_candle4h_strict": "c4s_candle_variations_strict",
}
#: canlı perp çerçevesinin bar sayısı (`engine.TradingEngine.PERP_FRAME_LIMITS`) − oluşan bar
LIVE_CLOSED_BARS = {"1d": 399, "4h": 699, "1h": 499, "5m": 499, "15m": 499}
TF_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
BTC_RAW = "BTCUSDT"

#: GEÇİCİ config dönemi sınırları (P3 `library/config_epochs.json` mühürlenene kadar) — belgedeki sahip kararları (§6.2
#: "Örnek sınırlar", §5.10). (an, defterler ("*" = hepsi), açıklama).
PROVISIONAL_BOUNDARIES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("2026-09-30T00:00:00+00:00", ("strategy_paper_box",), "Box slots 20→40"),
    ("2026-10-03T00:00:00+00:00", ("*",), "Box min_stop_pct 0,32→0,5 + learning_mode.extra_entries: record_selectivity"),
)
EPOCH_SRC_PROVISIONAL, EPOCH_SRC_SEALED = "PROVISIONAL_DOC_BOUNDARIES", "LIBRARY_CONFIG_EPOCHS"


# ============================================================================ config dönemi
def _sealed_epochs(research: Path) -> list[dict] | None:
    p = research / "library" / "config_epochs.json"
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    eps = d.get("epochs") if isinstance(d, dict) else None
    return [e for e in eps if isinstance(e, dict)] if isinstance(eps, list) else None


def config_epoch(book: str, opened_at: Any, *, research: Path | None = None) -> tuple[str | None, str]:
    """(dönem kimliği, kaynak). Mühürlü P3 dosyası (`{"epochs": [{"id", "from", "to"?, "books"?: [...]}]}`) varsa o;
    yoksa `PROVISIONAL_BOUNDARIES` (defterin sınırları; ilk sınırdan önce `E0`)."""
    t = parse_ts(opened_at)
    if t is None:
        return None, "MISSING"
    eps = _sealed_epochs(research) if research is not None else None
    if eps:
        best = None
        for e in eps:
            books = e.get("books")
            if books and book not in books and "*" not in books:
                continue
            a, b = parse_ts(e.get("from")), parse_ts(e.get("to"))
            if a is not None and t >= a and (b is None or t < b):
                if best is None or (parse_ts(best.get("from")) or a) <= a:
                    best = e
        if best is not None:
            return str(best.get("id")), EPOCH_SRC_SEALED
    eid = "E0"
    for at, books, _why in PROVISIONAL_BOUNDARIES:
        if ("*" in books or book in books) and t >= parse_ts(at):
            eid = "E" + at[:10]
    return eid, EPOCH_SRC_PROVISIONAL


# ============================================================================ sonuç
@dataclass
class Rehydrated:
    status: str
    rule: str | None = None
    family: str | None = None
    reason: str | None = None
    targets: list[float] | None = None
    signal_ctx: dict[str, Any] | None = None
    match: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] | None = None
    params_source: str | None = None
    params_hash: str | None = None
    variation_id: str | None = None
    parts: list[tuple[str, str, str]] = field(default_factory=list)
    diag: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"version": REHYDRATE_VERSION, "status": self.status, "rule": self.rule, "family": self.family,
                "reason": self.reason, "targets": self.targets, "signal_ctx": self.signal_ctx, "match": self.match,
                "params": self.params, "params_source": self.params_source, "params_hash": self.params_hash,
                "variation_id": self.variation_id, "diag": self.diag}


def tick_of(rec: dict) -> Decimal:
    """1 tick: giriş dolum fiyatının YAZILDIĞI ondalık hassasiyet (defter dolumu `price_tick`'e kuantize eder ve Decimal
    dizgesi sondaki sıfırları korur: "222.70" → 0,01). En ince 1e-8."""
    fills = [f for f in (rec.get("fills") or []) if isinstance(f, dict) and f.get("kind") == "entry"]
    raw = str((fills[0].get("price") if fills else None) or rec.get("entry") or "")
    d = dec_or_none(raw)
    if d is None or d <= 0:
        return Decimal("1e-8")
    exp = d.as_tuple().exponent
    return Decimal(1).scaleb(max(min(int(exp), 0), -8)) if isinstance(exp, int) else Decimal("1e-8")


def _d(x: Any) -> Decimal | None:
    if x is None:
        return None
    try:
        return Decimal(repr(float(x)))
    except (TypeError, ValueError):
        return None


def _f6(x: Any) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def module_source_sha(mod: Any) -> str:
    """Kural modülünün kaynak baytlarının sha256'sı (params_hash'e girer; P6'ya kadar, §4.3)."""
    try:
        return hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest()
    except (OSError, AttributeError, TypeError):
        return "unknown"


def params_hash(mod: Any, params: dict[str, Any]) -> str:
    blob = json.dumps({"src": module_source_sha(mod), "params": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ============================================================================ canlı çerçeve benzeri pencere
def live_frame(src: BarSource, symbol: str, tf: str, last_open_ms: int, *, indicators: bool = False
               ) -> tuple[pd.DataFrame | None, list, str | None]:
    """`last_open_ms` ile biten, canlı çerçevenin kapanmış bar sayısı kadar satır. `indicators`: günlük `ema200`/`atr14`
    (`add_snapshot_indicators` ile aynı fonksiyonlar). Son satır tam `last_open_ms` değilse (o bar depoda yok) None."""
    from .. import indicators as ind
    n = LIVE_CLOSED_BARS[tf]
    step = TF_MS[tf]
    df, used, st = src.last_closed("futures", symbol, tf, int(last_open_ms), n, lookback_ms=(n + 5) * step)
    if st is not None:
        return None, used, st
    if not len(df) or int(df["timestamp"].iloc[-1]) != int(last_open_ms):
        return None, used, "SIGNAL_BAR_MISSING"
    df = df.reset_index(drop=True)
    if indicators:
        df = df.copy()
        df["ema200"] = ind.ema(df["close"], 200)
        df["atr14"] = ind.atr(df["high"], df["low"], df["close"], 14)
    return df, used, None


# ============================================================================ parametreler
def rule_params_for(name: str, rawconfig: Any) -> tuple[dict[str, Any], float | None, str]:
    """(rule_params, atr_mult, kaynak) — ham config'ten (kural adıyla), yoksa varsayılan."""
    b = (getattr(rawconfig, "books", None) or {}).get(name) if rawconfig is not None else None
    if isinstance(b, dict) and (b.get("rule_params") is not None or b.get("atr_mult") is not None):
        return dict(b.get("rule_params") or {}), b.get("atr_mult"), "CONFIG_RAW"
    return {}, None, "DEFAULT"


def _build_params(name: str, rp: dict[str, Any], atr_mult: float | None) -> tuple[Any, dict[str, Any], list[str]]:
    from .. import paper_rules
    rp = dict(rp)
    ignored: list[str] = []
    fam = paper_rules.spec_for(name).family
    if fam == "box" and rp.get("min_stop_pct"):
        rp["min_stop_pct"] = 0.0
        ignored.append("min_stop_pct")
    if fam == "candle":
        rp["variations"] = []                     # geometri kayıttaki tek varyasyondan; kapı/öncelik listesi gerekmez
    kw: dict[str, Any] = {"rule_params": rp}
    if atr_mult is not None:
        kw["atr_mult"] = float(atr_mult)
    params = paper_rules.build_params(name, **kw)
    used = dict(rp)
    if fam == "trend":
        used["atr_mult"] = float(getattr(params, "atr_mult", atr_mult or 0.0))
    return params, used, ignored


# ============================================================================ aileler
def _entry_ref(rec: dict) -> Decimal | None:
    fills = [f for f in (rec.get("fills") or []) if isinstance(f, dict) and f.get("kind") == "entry"]
    if fills:
        return dec_or_none(fills[0].get("ref_price")) or dec_or_none(fills[0].get("price"))
    return dec_or_none(rec.get("entry"))


def _trend(name: str, rec: dict, src: BarSource, bars: dict, btc_bars: dict, now_ms: int, params: Any
           ) -> tuple[dict[str, Any] | None, list, str | None]:
    from .. import ema200_trend, paper_rules
    from ..candle_confirmation import closed_bars
    sym = str(rec.get("symbol"))
    t1 = bars.get("1d")
    if t1 is None:
        return None, [], "BARS_UNKNOWN_1D"
    fr, used, st = live_frame(src, sym, "1d", int(t1), indicators=True)
    if fr is None:
        return None, used, st
    rows = paper_rules.daily_rows({"1d": fr}, now_ms=now_ms)
    btc = None
    bt = (btc_bars or {}).get("1d", t1)
    if bt is not None:
        bfr, bused, bst = live_frame(src, BTC_RAW, "1d", int(bt), indicators=True)
        used = used + bused
        if bfr is not None:
            btc = closed_bars(ema200_trend.daily_rows_from_frame(bfr), now_ms=now_ms, tf="1d")
    rs = ema200_trend.rule_state(name, daily_rows=rows, btc_daily_rows=btc, atr_mult=float(params.atr_mult))
    if not rs.get("ok"):
        return None, used, "RULE_STATE_" + str(rs.get("reason") or "?")
    return {"stop": rs.get("stop_if_open"), "targets": [], "side": "LONG", "signal_ts": rs.get("signal_ts"),
            "signal_close": rs.get("close"),
            "ctx": {"ema200": _f6(rs.get("ema200")), "atr14": _f6(rs.get("atr14")), "signal_close": _f6(rs.get("close")),
                    "signal_ts": rs.get("signal_ts"), "regime": rs.get("regime"), "above": rs.get("above"),
                    "ref_close": _f6(rs.get("ref_close")), "ref_ts": rs.get("ref_ts"), "atr_mult": float(params.atr_mult),
                    "btc_bar_ts": bt}}, used, None


def _box(name: str, rec: dict, src: BarSource, bars: dict, now_ms: int, params: Any
         ) -> tuple[dict[str, Any] | None, list, str | None]:
    from .. import box_theory, paper_rules
    sym = str(rec.get("symbol"))
    t1, t5 = bars.get("1d"), bars.get("5m")
    if t1 is None or t5 is None:
        return None, [], "BARS_UNKNOWN_" + ("1D" if t1 is None else "5M")
    f1, u1, s1 = live_frame(src, sym, "1d", int(t1), indicators=True)
    f5, u5, s5 = live_frame(src, sym, "5m", int(t5))
    used = u1 + u5
    if f1 is None or f5 is None:
        return None, used, s1 or s5
    d1 = paper_rules.daily_rows({"1d": f1}, now_ms=now_ms)
    m5 = paper_rules.intraday_rows({"5m": f5}, tf="5m", now_ms=now_ms)
    rs = box_theory.rule_state(name, daily_rows=d1, m5_rows=m5, params=params)
    if not rs.get("ok") or rs.get("side") is None:
        return None, used, "RULE_STATE_" + str(rs.get("reason") or "NO_SIDE")
    hi, lo = rs.get("box_high"), rs.get("box_low")
    return {"stop": rs.get("stop_if_open"), "targets": list(rs.get("targets_if_open") or []), "side": rs.get("side"),
            "signal_ts": rs.get("signal_ts"), "signal_close": rs.get("close"),
            "ctx": {"box_high": _f6(hi), "box_low": _f6(lo), "box_mid": _f6(rs.get("box_mid")), "location": rs.get("location"),
                    "day_low": _f6(rs.get("day_low")), "day_high": _f6(rs.get("day_high")),
                    "day_range": _f6(rs["day_high"] - rs["day_low"]) if rs.get("day_high") is not None and rs.get("day_low") is not None else None,
                    "signal_close": _f6(rs.get("close")), "signal_ts": rs.get("signal_ts"), "triggered": rs.get("triggered"),
                    "params_label": rs.get("params_label"), "exit_kind": rs.get("exit_kind"), "side": rs.get("side")}}, used, None


def _donchian(name: str, rec: dict, src: BarSource, bars: dict, now_ms: int, params: Any
              ) -> tuple[dict[str, Any] | None, list, str | None]:
    from .. import donchian_trend, paper_rules
    sym = str(rec.get("symbol"))
    t4 = bars.get("4h")
    if t4 is None:
        return None, [], "BARS_UNKNOWN_4H"
    f4, used, st = live_frame(src, sym, "4h", int(t4))
    if f4 is None:
        return None, used, st
    rows = paper_rules.intraday_rows({"4h": f4}, tf="4h", now_ms=now_ms)
    rs = donchian_trend.rule_state(name, rows=rows, params=params)
    if not rs.get("ok"):
        return None, used, "RULE_STATE_" + str(rs.get("reason") or "?")
    return {"stop": rs.get("stop_if_open"), "targets": [], "side": "LONG", "signal_ts": rs.get("signal_ts"),
            "signal_close": rs.get("close"),
            "ctx": {"channel_high20": _f6(rs.get("channel_high20")), "channel_low10": _f6(rs.get("channel_low10")),
                    "atr14": _f6(rs.get("atr14")), "signal_close": _f6(rs.get("close")), "signal_ts": rs.get("signal_ts"),
                    "fresh_breakout": rs.get("fresh_breakout"), "stop_atr": donchian_trend.STOP_ATR,
                    "risk_atr_bounds": [donchian_trend.MIN_RISK_ATR, donchian_trend.MAX_RISK_ATR],
                    "max_bars": donchian_trend.MAX_BARS}}, used, None


def _candle(name: str, rec: dict, src: BarSource, bars: dict, now_ms: int
            ) -> tuple[dict[str, Any] | None, list, str | None]:
    from .. import candle_book, candle_dsl, candle_variations
    sym = str(rec.get("symbol"))
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    snap = feats.get("candle_variation") if isinstance(feats.get("candle_variation"), dict) else {}
    st_ = str(rec.get("setup_type") or "")
    vid = snap.get("id") or (st_[len(candle_book.SETUP_PREFIX):] if st_.startswith(candle_book.SETUP_PREFIX) else None)
    if not vid:
        return None, [], "VARIATION_UNKNOWN"
    t4 = bars.get("4h")
    if t4 is None:
        return None, [], "BARS_UNKNOWN_4H"
    try:
        var = candle_variations.get(str(vid))
    except Exception as exc:  # noqa: BLE001 — kayıtta olmayan/bozuk varyasyon: yeniden kurulamaz
        return None, [], "VARIATION_UNLOADABLE:" + type(exc).__name__
    f4, used, st = live_frame(src, sym, "4h", int(t4))
    if f4 is None:
        return None, used, st
    rows = candle_book.window_rows({"4h": f4}, now_ms=now_ms)
    win, why = candle_dsl.window_from_rows(rows, candle_book.STEP_MS)
    if win is None:
        return None, used, "WINDOW_" + str(why)
    hit = candle_dsl.detect_last(win, var)
    if hit is None:
        return None, used, "NO_PATTERN_HIT"
    ref = _entry_ref(rec)
    targets: list[float] = []
    if var.target_r is not None and ref is not None:
        e, s = float(ref), float(hit.stop)
        sg = 1.0 if str(var.side).upper() == "LONG" else -1.0
        targets = [e + sg * float(var.target_r) * abs(e - s)]          # `apply_action.target_r_from_entry` ile aynı
    return {"stop": float(hit.stop), "targets": targets, "side": str(var.side).upper(), "signal_ts": int(hit.signal_ts),
            "signal_close": float(hit.close), "variation": var, "hit": hit,
            "ctx": {"variation_id": var.id, "definition_sha": var.definition_sha, "pattern_high": float(hit.pattern_high),
                    "pattern_low": float(hit.pattern_low), "atr14": float(hit.atr_i), "signal_close": float(hit.close),
                    "signal_ts": int(hit.signal_ts), "signal_close_ms": int(hit.signal_close_ms),
                    "target_r": float(var.target_r) if var.target_r is not None else None,
                    "max_hold_bars": int(var.max_hold_bars), "side": str(var.side).upper(),
                    "risk_atr_bounds": [float(var.risk_atr_bounds[0]), float(var.risk_atr_bounds[1])]}}, used, None


# ============================================================================ ana fonksiyon
def rehydrate(rec: dict, book: str, *, src: BarSource | None, rawconfig: Any = None,
              memory_row: dict | None = None) -> Rehydrated:
    """Tek kaydın kural bağlamı. `memory_row`: defterin `trade_memory.jsonl` giriş satırı (ÖLÇÜLMÜŞ `signal_close`)."""
    from .. import box_theory, candle_dsl, donchian_trend, ema200_trend, paper_rules
    name = RULE_BOOKS.get(book)
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    if name is None:
        return Rehydrated(status=NOT_APPLICABLE, reason="BOOK_HAS_NO_RULE_REHYDRATE")
    fam = paper_rules.spec_for(name).family
    out = Rehydrated(status=FAILED, rule=name, family=fam)
    if src is None:
        out.status, out.reason = NO_DATA, "NO_STORE"
        return out
    stop_m = dec_or_none(feats.get("initial_stop"))
    if stop_m is None:
        out.reason = "INITIAL_STOP_MISSING"
        return out
    o = parse_ts(rec.get("opened_at"))
    if o is None:
        out.reason = "OPENED_AT_UNREADABLE"
        return out
    now_ms = int(o.timestamp() * 1000)
    ds = feats.get("data_source") if isinstance(feats.get("data_source"), dict) else {}
    bars = {str(k): v for k, v in (ds.get("bars") or {}).items()} if isinstance(ds.get("bars"), dict) else {}
    btc = ds.get("btc") if isinstance(ds.get("btc"), dict) else {}
    btc_bars = btc.get("bars") if isinstance(btc.get("bars"), dict) else {}
    rp, atr_mult, psrc = rule_params_for(name, rawconfig)
    try:
        params, used_params, ignored = _build_params(name, rp, atr_mult)
    except Exception as exc:  # noqa: BLE001 — bozuk config parametresi: yeniden kurulamaz
        out.reason = "PARAMS_INVALID:" + type(exc).__name__
        return out
    mod = {"trend": ema200_trend, "box": box_theory, "donchian": donchian_trend, "candle": candle_dsl}[fam]
    out.params, out.params_source = used_params, psrc
    if ignored:
        out.diag["filter_params_ignored"] = ignored
    try:
        if fam == "trend":
            geo, used, why = _trend(name, rec, src, bars, btc_bars, now_ms, params)
        elif fam == "box":
            geo, used, why = _box(name, rec, src, bars, now_ms, params)
        elif fam == "donchian":
            geo, used, why = _donchian(name, rec, src, bars, now_ms, params)
        else:
            geo, used, why = _candle(name, rec, src, bars, now_ms)
    except Exception as exc:  # noqa: BLE001 — kural arızası: dürüstçe başarısız (tahmin yok)
        out.reason = "RULE_ERROR:" + type(exc).__name__
        out.diag["error"] = str(exc)[:200]
        return out
    out.parts = sorted(set(used or []))
    if geo is None:
        out.status = NO_DATA if why in ("DATA_MOVING", "SIGNAL_BAR_MISSING") else FAILED
        out.reason = why
        return out
    if fam == "candle":
        var = geo.pop("variation")
        geo.pop("hit")
        out.variation_id = var.id
        out.params_hash = var.definition_sha
        out.params = {"variation_id": var.id, "definition_sha": var.definition_sha}
    else:
        out.params_hash = params_hash(mod, used_params)
        if fam == "box":
            out.variation_id = str((geo.get("ctx") or {}).get("params_label") or "")
        elif fam == "trend":
            out.variation_id = "atr%g" % float(used_params.get("atr_mult", 0.0))
        else:
            out.variation_id = "donchian_%d_%d" % (donchian_trend.ENTRY_N, donchian_trend.EXIT_N)
    # ---- eşleşme denetimi
    tick = tick_of(rec)
    stop_r = _d(geo.get("stop"))
    side = str(rec.get("side") or "").upper()
    checks: dict[str, Any] = {"tick": format(tick, "f")}
    reasons: list[str] = []
    if stop_r is None:
        reasons.append("STOP_NOT_COMPUTED")
    else:
        dstop = abs(stop_r - stop_m)
        checks["stop"] = {"measured": format(stop_m, "f"), "recomputed": format(stop_r, "f"), "abs_diff": format(dstop, "f"),
                          "ok": dstop <= tick}
        if dstop > tick:
            reasons.append("STOP_MISMATCH")
    if str(geo.get("side") or side) != side:
        reasons.append("SIDE_MISMATCH")
    sig_bar = bars.get({"trend": "1d", "box": "5m", "donchian": "4h", "candle": "4h"}[fam])
    checks["signal_bar"] = {"recorded": sig_bar, "recomputed": geo.get("signal_ts"),
                            "ok": sig_bar is not None and int(geo.get("signal_ts") or -1) == int(sig_bar)}
    if not checks["signal_bar"]["ok"]:
        reasons.append("SIGNAL_BAR_MISMATCH")
    mem_sc = dec_or_none(((memory_row or {}).get("features") or {}).get("signal_close")) if memory_row else None
    if mem_sc is not None and geo.get("signal_close") is not None:
        dsc = abs(_d(geo["signal_close"]) - mem_sc)
        checks["entry_ref"] = {"basis": "SIGNAL_CLOSE_VS_TRADE_MEMORY", "measured": format(mem_sc, "f"),
                               "recomputed": format(_d(geo["signal_close"]), "f"), "ok": dsc <= tick}
        if dsc > tick:
            reasons.append("ENTRY_REF_MISMATCH")
    else:
        checks["entry_ref"] = {"basis": "SIGNAL_BAR_IDENTITY", "ok": checks["signal_bar"]["ok"]}
    if fam == "candle":
        snap = feats.get("candle_variation") if isinstance(feats.get("candle_variation"), dict) else {}
        ctx = geo.get("ctx") or {}
        if snap.get("definition_sha") and snap.get("definition_sha") != ctx.get("definition_sha"):
            reasons.append("DEFINITION_SHA_MISMATCH")
        for k in ("pattern_high", "pattern_low"):
            if snap.get(k) is not None and ctx.get(k) is not None and abs(_d(snap[k]) - _d(ctx[k])) > tick:
                reasons.append(k.upper() + "_MISMATCH")
        checks["candle_snapshot"] = {"definition_sha": snap.get("definition_sha") == ctx.get("definition_sha") if snap else None}
    ref = _entry_ref(rec)
    if ref is not None and stop_m is not None:
        wrong = (ref <= stop_m) if side == "LONG" else (ref >= stop_m)
        checks["entry_side_of_stop"] = not wrong
        if wrong:
            reasons.append("ENTRY_WRONG_SIDE_OF_STOP")
    out.match = {**checks, "ok": not reasons}
    if reasons:
        out.status, out.reason = FAILED, ",".join(reasons)
        out.diag["recomputed_targets"] = geo.get("targets")
        return out
    out.status = OK
    out.targets = [float(x) for x in (geo.get("targets") or [])]
    out.signal_ctx = geo.get("ctx")
    return out


def match_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Defter × config dönemi başına eşleşme oranı (§5.2 hedefi ≥ %95). `rows`: günlük satırları (`rehydrate` alanı)."""
    acc: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        rh = r.get("rehydrate") or {}
        if rh.get("status") in (None, NOT_APPLICABLE):
            continue
        b = acc.setdefault(str(r.get("book")), {}).setdefault(str(r.get("config_epoch")), {"n": 0, "ok": 0, "failed": []})
        b["n"] += 1
        if rh.get("status") == OK:
            b["ok"] += 1
        else:
            b["failed"].append({"trade_key": r.get("trade_key"), "status": rh.get("status"), "reason": rh.get("reason")})
    out: dict[str, Any] = {}
    for book, eps in sorted(acc.items()):
        out[book] = {}
        for ep, v in sorted(eps.items()):
            rate = v["ok"] / v["n"] if v["n"] else None
            out[book][ep] = {"n": v["n"], "ok": v["ok"], "rate": round(rate, 4) if rate is not None else None,
                             "target": 0.95, "meets_target": bool(rate is not None and rate >= 0.95),
                             "failed": v["failed"][:20]}
    return out


__all__ = ["EPOCH_SRC_PROVISIONAL", "EPOCH_SRC_SEALED", "FAILED", "LIVE_CLOSED_BARS", "NOT_APPLICABLE", "NO_DATA", "OK",
           "PROVISIONAL_BOUNDARIES", "REHYDRATE_VERSION", "RULE_BOOKS", "Rehydrated", "config_epoch", "live_frame",
           "match_stats", "module_source_sha", "params_hash", "rehydrate", "rule_params_for", "tick_of"]
