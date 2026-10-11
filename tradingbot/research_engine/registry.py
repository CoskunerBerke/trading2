"""P3a — mühür S1 (docs/ENGINE_BRAIN_V1.md §2–§4, §8.1 P3a, §8.2): `LIB_V1`, `WF_PROTOCOL_V1`, `STATS_V1`,
`LESSON_REGISTRY_V1`, `PIT_RULES_V1`, `PRIOR_TRIALS_V1` (`prior_trials.json`), `CONFIG_EPOCHS_V1`
(`library/config_epochs.json`; üreticisi `epochs.py` + `scripts/gen_config_epochs.py`) ve `T_SEAL_MS`.

Kurallar (kodda zorlanır, testli):

* **Donuk ve kanonik.** Kayıtlar iç içe `MappingProxyType` + tuple'dır (çalışma anında değiştirilemez). Kanonik bayt =
  `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` UTF-8; `<AD>_SHA` =
  sha256(kanonik bayt). Sha'lar `tests/test_research_engine_registry.py`'de ve belgenin §8.2 tablosunda sabittir; HER alan
  değişikliği sha'yı değiştirir (özellik testi). Mühürden sonra her değişiklik `_v2` + yeni `SPEC`/`OPTION` satırları +
  belgeye tarihli "Değişiklik" notudur.
* **Uydurma yok.** Belgenin söylemediği bir ayrıntı `gap("…")` = `{"status": "UNSPECIFIED", "gap": "…"}` ile AÇIKÇA
  yazılır (`unspecified_gaps()`; liste testte sabit). Varyantın boşlukları = kendi alanlarındakiler + `requires`
  bağımlılıklarındakiler (`defaults.er20_timeframe`, `covers.factors.<F>`, `copy_books.<taban>`; `variant_gaps`).
  Boşluğu olan varyant gerçek veride HİÇBİR katmanda (WF, keşif, kapı) çalıştırılmaz (`UNSPECIFIED_PARAM`) ama deneme
  bütçesinde SAYILIR (sayılar mühürden sonra küçültülemez, §2.5). Protokol boşlukları (`WF_PROTOCOL_V1`, `STATS_V1`,
  `LESSON_REGISTRY_V1`, `PIT_RULES_V1`) dolmadan hiçbir gerçek veri WF/kapı/ders koşusu yapılmaz (`protocol_gaps`).
  Belgenin açıkça yazdığı okunuşlar (`reading_tr`) gerekçesiyle yazılır. Sahibin §8.5 cevabıyla (2026-10-11,
  "Önerilenleri kabul et") dolan boşluklar `decided(...)` ile yazılır (`decision` = §8.5 maddesi, `reading_tr` "sahip
  kararı 2026-10-11, §8.5 önerilen"); öneri olmayanlar (B9, altın stopları) boşluk kalır (`blocked_budget`).
* **Sayılar koddan.** Bütçe tablosu (818 Kapı A satırı / 1.738 ham deneme), öncül toplamı (2.775), mühürde kümülatif N
  (4.513), keşif seri sayısı (3.745) ve §2.2 "Oto" sütunu (S5 öncesi, 64) `gate_a_rows` / `budget_table` / `prior_total` /
  `explore_series` / `oto_column_pre_s5_count` ile kayıttan YENİDEN ÜRETİLİR; belgedeki sayılara eşitlik testlidir.
* **Asla gevşetme** (sahip kararı 2, §8.2). Eşikli her kaydın `thresholds` ağacındaki HER sayının ve eşik gibi davranan
  kapı-dışı alanların (`xsec_requires_pit`, `step_equals_test`, `dsr.sr_var_floor`, `budget_declared`, …) yönü
  `THRESHOLD_DIRECTIONS_V1`'dedir (kök-göreli yol; `max` = üst sınır: q ≤, PBO <; `min` = alt sınır: n ≥, DSR ≥;
  `fixed` = tanım, hiç değişmez — iki yönde de sonucu oynatabilen her değer). Tablo S1 mühür kümesinin parçasıdır (sha
  sabit); `looser_than(v1, vN)` sonraki bir sürümün v1'den gevşek her değerini döndürür ve test kırılır. Kapı A/B, Holm
  ve otomatik terfi eşikleri S2'dedir (`PROMOTION_REGISTRY_V1`, `AUTO_POLICY_V1`; sahibin kapı metni onayından sonra);
  S2 yönlerini AYRI bir tabloyla ekler, S1 tablosu değişmez.
* Yalnız standart kütüphane; ağ, pandas, `tradingbot` dışı import yok (AST testi). Hiçbir yere yazmaz.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

PKG_DIR = Path(__file__).resolve().parent
PRIOR_TRIALS_FILE = PKG_DIR / "prior_trials.json"
CONFIG_EPOCHS_FILE = PKG_DIR / "library" / "config_epochs.json"

UNSPECIFIED = "UNSPECIFIED"
LIB_VERSION = "LIB_v1"
SEAL_ID = "S1"
SEAL_DOC = "docs/ENGINE_BRAIN_V1.md §8.2"

#: Mühür anı (UTC epoch ms, tam dakika). Mühürlü içeriği (sabitlenmiş sha'lar, prior_trials.json, config_epochs.json)
#: değiştiren EN SON commit'in işleyen zamanına EŞİT ya da ondan SONRADIR (test git'ten doğrular; commit tarihi elle
#: ayarlanmaz). Keşif katmanı ve Kapı A'nın ileri okuması YALNIZ açılışı bu andan sonra olan barları kullanır (§5.1).
#: Mühür commit'leri yalnız fast-forward (ya da merge commit) ile birleşir; rebase/squash/cherry-pick işleyen zamanı
#: değiştirir → test kırılır → yeniden mühür.
T_SEAL_MS = 1791747600000


# ============================================================================ kanonik biçim ve donma
def gap(text: str) -> dict:
    """Belgenin söylemediği bir ayrıntının açık işareti (uydurulmaz)."""
    return {"status": UNSPECIFIED, "gap": text}


#: §8.5 boşluklarına sahibin cevabı (2026-10-11, "Önerilenleri kabul et"): önerilen varsayılanların hepsi kabul; öneri
#: olmayanlar (B9 spread/XSEC, B10 altın stopları) boşluk kalır.
OWNER_GAP_DECISION_TR = "sahip kararı 2026-10-11, §8.5 önerilen"


def decided(item: str, text: str, **values: Any) -> dict:
    """§8.5 boşluğunun sahip kararıyla doldurulmuş değeri: değer alanları + `decision` (§8.5 maddesi) + `reading_tr`."""
    return {**values, "decision": item, "reading_tr": f"{OWNER_GAP_DECISION_TR} ({item}): {text}"}


def thaw(x: Any) -> Any:
    """Donuk kaydı düz JSON değerine çevir (dict/list)."""
    if isinstance(x, Mapping):
        return {str(k): thaw(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [thaw(v) for v in x]
    return x


def freeze(x: Any) -> Any:
    """Derin dondurma: dict → `MappingProxyType`, list/tuple → tuple. Anahtarlar str olmalı."""
    if isinstance(x, Mapping):
        out = {}
        for k, v in x.items():
            if not isinstance(k, str):
                raise TypeError(f"kayıt anahtarı str olmalı: {k!r}")
            out[k] = freeze(v)
        return MappingProxyType(out)
    if isinstance(x, (list, tuple)):
        return tuple(freeze(v) for v in x)
    if isinstance(x, float) and not math.isfinite(x):
        raise ValueError("kayıtta NaN/sonsuz yok")
    if x is None or isinstance(x, (str, bool, int, float)):
        return x
    raise TypeError(f"kayıtta izin dışı tür: {type(x).__name__}")


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(thaw(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_canonical(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def pretty_json(obj: Any) -> str:
    """Depoya yazılan veri dosyalarının (prior_trials.json, config_epochs.json) okunur biçimi; sha kanonik içerikten."""
    return json.dumps(thaw(obj), sort_keys=True, ensure_ascii=False, indent=1, allow_nan=False) + "\n"


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ============================================================================ LIB_V1 (§2, §4.3)
SIDES_LO, SIDES_LS = "LO", "LS"
P23: tuple[str, ...] = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "NEAR", "TRX", "BCH",
                        "UNI", "FIL", "XLM", "AAVE", "APT", "ARB", "OP", "INJ", "SUI")
CLUSTERS: dict[str, tuple[str, ...]] = {
    "CL_MAJ": ("BTC", "ETH"),
    "CL_L1": ("BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX", "BCH"),
    "CL_ALT": ("NEAR", "UNI", "FIL", "XLM", "AAVE", "APT", "ARB", "OP", "INJ", "SUI"),
}
FAMILY_GROUPS: dict[str, tuple[str, ...]] = {"STANDARD": ("B", "C", "D", "E", "F1", "F2", "H"), "COPY": ("K",),
                                             "COVER": ("O",), "OTHER": ("J", "I", "G")}
COIN_FIT_FAMILIES = FAMILY_GROUPS["STANDARD"]
DEFAULT_MAX_HOLD = 300
#: belge §2.2 tablosu: aile → (ad, dilim metni, benzersiz varyant sayısı, "Oto" sütunu aynen — S5 ÖNCESİ, bkz. sahip kararı S5)
FAMILY_TABLE: dict[str, tuple[str, str, int, str]] = {
    "K": ("Canlı defter kopyaları", "defterin kendi", 13, "K_BOX, K_FM hariç"),
    "B": ("TSMOM", "1d", 10, "evet"),
    "C": ("Ortalama kesişimi", "4h, 1d", 8, "evet"),
    "D": ("Donchian", "4h, 1d", 7, "evet"),
    "E": ("Oynaklık kırılımı", "1h, 4h", 4, "yalnız 4h (2)"),
    "F1": ("Aralık dönüşü", "1h, 4h", 4, "yalnız 4h (2)"),
    "F2": ("Box ızgarası", "1d + 5m", 7, "hayır (5m)"),
    "H": ("Fonlama", "4h + fonlama", 5, "hayır (veri)"),
    "I": ("BTC–ETH spread", "1h", 2, "hayır"),
    "J": ("Kesitsel (XSEC)", "1d", 5, "hayır"),
    "G": ("Altına özgü", "1h, 1d", 6, "hayır (veri)"),
    "O": ("Örtüler (bir seferde bir faktör)", "tabanınki", 35, "T2/M2/D4/C4 × {F_ER, F_BTC, F_VOL, X_TRAIL2, X_CHAN3, X_TIME} = 24"),
}
WF_H1, WF_INTRADAY, WF_GOLD = "H1_PLUS", "INTRADAY", "GOLD_FUT"


def _atr_stop(mult: float, tf_note: str = "sinyal dilimi") -> dict:
    return {"kind": "ATR", "atr_mult": mult, "atr": f"ATR14 ({tf_note})"}


ER20_TF = "defaults.er20_timeframe"


def _variant(variant_id: str, family: str, *, params: dict, timeframes: list[str], signal_tf: str, sides: Any,
             rule_tr: str, stop: Any, exit_rule: Any, target_r: Any = None, max_hold_bars: Any = DEFAULT_MAX_HOLD,
             oto_pre_s5: bool, wf_class: str, inputs: list[str], alias_of: str | None = None, book: dict | None = None,
             cover: dict | None = None, requires: list[str] | None = None) -> dict:
    """`requires`: varyantın kural/WF tanımının dayandığı LIB-düzeyi kayıtlar (kök-göreli yol); oradaki her UNSPECIFIED
    boşluk varyantın da boşluğudur (`variant_gaps`)."""
    return {"variant_id": variant_id, "family": family, "params": params, "timeframes": timeframes, "signal_tf": signal_tf,
            "sides": sides, "rule_tr": rule_tr, "exits": {"stop": stop, "exit_rule": exit_rule, "target_r": target_r,
                                                          "max_hold_bars": max_hold_bars},
            "oto_column_pre_s5": oto_pre_s5, "wf_class": wf_class, "inputs": inputs, "alias_of": alias_of, "book": book,
            "cover": cover, "requires": sorted(requires or [])}


#: canlı defter kopyaları (§2.1: yeniden yazılmaz; `paper_rules.decide_from_rows` / `pattern_trader` kural fonksiyonları
#: çağrılır). Değerler mühür anındaki canlı config'tir (`config.yaml` @ db5db96 = son `CONFIG_EPOCHS_V1` dönemi); kernel
#: büyüklüğü/kaldıracı kendisi belirler (§2.1), bu yüzden defterin bakiye/kaldıraç/slot ayarları kopya spec'ine girmez.
#: `max_hold`: canlı kuralın zaman stopu (WF arındırma/ambargosu = 1 sinyal barı + max_hold, §3.1; O_<taban>_X_TIME =
#: max_hold'un yarısı, §2.2); değerler canlı koddan okunur ve testte koda karşı sınanır. Canlı T2/M2 kuralında zaman stopu
#: yoktur: sahip kararı B2 (a) ile §2.2 varsayılanı YALNIZ WF için kullanılır (`wf_only`; kural değişmez).
def _wf_only_max_hold(b: str) -> dict:
    return decided("B2 (a)", f"canlı {b} kuralında (ema200_trend.decide) zaman stopu yok ve kural DEĞİŞMEZ: {b} kopyası "
                             "zaman stopuyla çıkmaz. §2.2'nin 'max_hold aksi yazılmadıkça 300 sinyal barı' varsayılanı YALNIZ "
                             "WF arındırma/ambargosunun (1 sinyal barı + max_hold, §3.1) ve O_" + b + "_X_TIME'ın "
                             "('max_hold'un yarısı' = 150 × 1d) tanımıdır",
                   bars=DEFAULT_MAX_HOLD, tf="1d", wf_only=True, x_time_bars=DEFAULT_MAX_HOLD // 2,
                   source="§2.2 varsayılanı (300 sinyal barı); canlı kuralda zaman stopu yok")


COPY_BOOKS: dict[str, dict] = {
    "T2": {"book": "strategy_paper", "rule": "t2_trend_regime", "timeframes": ["1d"], "own_symbols": "LIVE_BOOK",
           "live": {"atr_mult": 3.0, "breakeven_at_mfe_r": 0.0, "max_entry_drift_pct": 0.0, "structures": "ENFORCE"},
           "max_hold": _wf_only_max_hold("T2")},
    "M2": {"book": "strategy_paper_m2", "rule": "m2_tsmom28", "timeframes": ["1d"], "own_symbols": "LIVE_BOOK",
           "live": {"atr_mult": 3.0, "breakeven_at_mfe_r": 0.0, "max_entry_drift_pct": 0.0, "structures": "ENFORCE"},
           "max_hold": _wf_only_max_hold("M2")},
    "BOX": {"book": "strategy_paper_box", "rule": "b1_box_fade", "timeframes": ["1d", "5m"], "own_symbols": "LIVE_BOOK",
            "live": {"near_frac": 0.1, "trigger": "break_prev", "long_stop": "day_low", "exit_kind": "box_mid", "exit_r": 2.0,
                     "eod_close": True, "allow_long": True, "allow_short": True, "breakeven_at_mfe_r": 0.0,
                     "min_stop_pct": 0.5, "structures": "SHADOW"},
            "max_hold": {"rule_tr": "UTC gün sonu (eod_close)", "source": "box_theory eod_close; X_TIME için §2.2 '48 × 5m'"},
            "reading_tr": "en az stop %0,5 = learning_mode.books.b1_box_fade.min_stop_pct (belge §2.2 'en az stop %0,5'); "
                          "rule_params.min_stop_pct 2,22 öğrenme modunda yerini bu tabana bırakır"},
    "D4": {"book": "strategy_paper_trend4h", "rule": "d4_donchian_20_10", "timeframes": ["4h"], "own_symbols": "P23",
           "live": {"entry_window_min": 60, "breakeven_at_mfe_r": 0.0, "max_entry_drift_pct": 0.0},
           "max_hold": {"bars": 300, "tf": "4h", "source": "donchian_trend.MAX_BARS"}},
    "C4": {"book": "strategy_paper_candle4h", "rule": "c4_candle_variations", "timeframes": ["4h"], "own_symbols": "P23",
           "live": {"entry_window_min": 60, "breakeven_at_mfe_r": 0.0, "max_entry_drift_pct": 0.0},
           "max_hold": {"bars": 24, "tf": "4h", "source": "candle_variations CV001–CV008 definition.exit.max_hold_bars"}},
    "FM": {"book": "pattern_trader", "rule": "pattern_trader", "timeframes": ["4h"], "own_symbols": "LIVE_BOOK",
           "live": {"protocol": "momentum_4h_v3", "structures": "ENFORCE"},
           "max_hold": {"bars": 24, "tf": "4h", "source": "pattern_trader/strategy_v3.V3_SIGNAL.hold_bars (= 384 × 15m)"},
           "reading_tr": "kural sabitleri pattern_trader/strategy_v3.py'dedir (V3_SIGNAL, V3_SYMBOLS; module_src_sha ile "
                         "sabitlenir); config pattern_trader bölümünün 15m formasyon ayarları protokol v3'te kural değildir"},
}
C4_VARIATIONS: tuple[str, ...] = ("CV001_BREAKOUT20_TREND_VOL_L", "CV002_BREAKOUT20_TREND_VOL_S", "CV003_PULLBACK_ENGULF_L",
                                  "CV004_PULLBACK_ENGULF_S", "CV005_SUPPORT_HARAMI_L", "CV006_RESIST_HARAMI_S",
                                  "CV007_SWEEP_REJECT_ENGULF_L", "CV008_SWEEP_REJECT_ENGULF_S")
COPY_RULE_TR = {
    "T2": "canlı T2 kuralı (EMA200 trend rejimi), kendi fonksiyonu", "M2": "canlı M2 kuralı (TSMOM28), kendi fonksiyonu",
    "BOX": "canlı Box kuralı (önceki günün kutusu, 5m'de soluklama), kendi fonksiyonu",
    "D4": "canlı D4 kuralı: 4h kapanış önceki 20 barın tepesini ilk kez aşar → LONG; stop 2 × ATR14; kapanış önceki 10 barın "
          "dibinin altına inince çık; en çok 300 bar",
    "C4": "canlı C4 mum varyasyonu, kendi tanımı (definition_sha)",
    "FM": "canlı Formasyon protokolü momentum_4h_v3 (4h üç beyaz asker + RSI14 > 70 → LONG)",
}


def _copies() -> list[dict]:
    out = []
    order = (("K_T2", "T2", None), ("K_M2", "M2", None), ("K_BOX", "BOX", None), ("K_D4", "D4", None))
    order += tuple((f"K_C4_CV{i:03d}", "C4", C4_VARIATIONS[i - 1]) for i in range(1, 9))
    order += (("K_FM", "FM", None),)
    for vid, base, cv in order:
        b = COPY_BOOKS[base]
        book = {"book": b["book"], "rule": b["rule"], "own_symbols": b["own_symbols"], "values_release": "db5db96",
                "params_source": "LIVE_CONFIG_AT_SEAL"}
        if "reading_tr" in b:
            book["reading_tr"] = b["reading_tr"]
        params = dict(b["live"])
        if cv:
            params["variation"] = cv
        wf = WF_INTRADAY if base == "BOX" else WF_H1
        sides = "canlı kural" if base not in ("D4", "FM") else SIDES_LO
        out.append(_variant(vid, "K", params=params, timeframes=list(b["timeframes"]), signal_tf=b["timeframes"][-1],
                            sides=sides, rule_tr=COPY_RULE_TR[base], stop="LIVE_RULE", exit_rule="LIVE_RULE",
                            max_hold_bars="LIVE_RULE", oto_pre_s5=vid not in ("K_BOX", "K_FM"), wf_class=wf,
                            inputs=["ohlcv"], book=book, requires=[f"copy_books.{base}"]))
    return out


def _standard() -> list[dict]:
    out: list[dict] = []
    for lb in (7, 14, 28, 56, 84):
        for s in (SIDES_LO, SIDES_LS):
            out.append(_variant(
                f"TS_{lb}_{s}", "B", params={"lookback_days": lb}, timeframes=["1d"], signal_tf="1d", sides=s,
                rule_tr=f"{lb} günlük getirinin işareti: + → long" + ("; − → short" if s == SIDES_LS else "; − → pozisyon yok"),
                stop=_atr_stop(3.0), exit_rule="işaret dönünce çık", oto_pre_s5=True, wf_class=WF_H1, inputs=["ohlcv"]))
    for fast, slow in ((20, 50), (50, 200)):
        for tf in ("4h", "1d"):
            for s in (SIDES_LO, SIDES_LS):
                out.append(_variant(
                    f"MAX_{fast}_{slow}_{tf}_{s}", "C", params={"fast_ema": fast, "slow_ema": slow}, timeframes=[tf],
                    signal_tf=tf, sides=s, rule_tr=f"EMA{fast} / EMA{slow} kesişimi", stop=_atr_stop(3.0),
                    exit_rule="ters kesişim", oto_pre_s5=True, wf_class=WF_H1, inputs=["ohlcv"]))
    for ent, ex in ((20, 10), (55, 20)):
        for tf in ("4h", "1d"):
            for s in (SIDES_LO, SIDES_LS):
                vid = f"DON_{ent}_{ex}_{tf}_{s}"
                out.append(_variant(
                    vid, "D", params={"entry_channel_bars": ent, "exit_channel_bars": ex}, timeframes=[tf], signal_tf=tf,
                    sides=s, rule_tr=f"Donchian {ent} kanal kırılımı", stop=_atr_stop(2.0),
                    exit_rule=f"karşı kanal ({ex} bar)", oto_pre_s5=True, wf_class=WF_H1, inputs=["ohlcv"],
                    alias_of="K_D4" if vid == "DON_20_10_4h_LO" else None))
    for tf in ("1h", "4h"):
        out.append(_variant(
            f"KEL_20_2_{tf}", "E", params={"keltner_period": 20, "band_atr_mult": 2.0,
                                           "center_line": decided("B7", "Keltner orta çizgisi EMA20, kapanış fiyatından",
                                                                  ma="EMA", period=20, price="close")},
            timeframes=[tf], signal_tf=tf, sides=SIDES_LS, rule_tr="Keltner 20, 2 ATR kapanış kırılımı",
            stop=_atr_stop(1.5), exit_rule="hedef ya da max_hold", target_r=2.0, max_hold_bars=48, oto_pre_s5=tf == "4h",
            wf_class=WF_H1, inputs=["ohlcv"]))
        out.append(_variant(
            f"BBSQ_20_{tf}", "E", params={"bb_period": 20, "bb_std": 2.0, "squeeze_lookback_bars": 120, "squeeze_pct_max": 20,
                                          "breakout_ref": decided("B7", "sıkışmadaki kırılım = kapanışın BB(20,2) bandının "
                                                                  "dışında olması (üst bandın üstü → long, alt bandın altı "
                                                                  "→ short)", rule="CLOSE_OUTSIDE_BB", bb_period=20,
                                                                  bb_std=2.0)},
            timeframes=[tf], signal_tf=tf, sides=SIDES_LS,
            rule_tr="BB(20,2) genişliği son 120 barın ≤ %20'liğinde iken kırılım", stop=_atr_stop(1.5),
            exit_rule="hedef ya da max_hold", target_r=2.0, max_hold_bars=48, oto_pre_s5=tf == "4h", wf_class=WF_H1,
            inputs=["ohlcv"]))
    for tf in ("1h", "4h"):
        out.append(_variant(
            f"RSI2_{tf}", "F1", params={"rsi_period": 2, "long_below": 10, "short_above": 90, "er20_max": 0.3},
            timeframes=[tf], signal_tf=tf, sides=SIDES_LS, rule_tr="RSI2 < 10 long / > 90 short; yalnız ER20 < 0,3",
            stop=_atr_stop(1.5),
            exit_rule=decided("B5", "RSI2'nin 'orta bant' çıkışı = sinyal dilimindeki BB(20,2) orta çizgisi (BBRE ile "
                                    "aynı; RSI 50 değil)", rule_tr="orta bant (BB(20,2) orta çizgisi)", bb_period=20,
                              bb_std=2.0),
            max_hold_bars=24, oto_pre_s5=tf == "4h", wf_class=WF_H1, inputs=["ohlcv"], requires=[ER20_TF]))
        out.append(_variant(
            f"BBRE_{tf}", "F1", params={"bb_period": 20, "bb_std": 2.0, "er20_max": 0.3}, timeframes=[tf], signal_tf=tf,
            sides=SIDES_LS, rule_tr="BB(20,2) dışına çıkıp içine kapanış; yalnız ER20 < 0,3", stop=_atr_stop(1.5),
            exit_rule="orta bant (BB(20,2) orta çizgisi)", max_hold_bars=24, oto_pre_s5=tf == "4h", wf_class=WF_H1,
            inputs=["ohlcv"], requires=[ER20_TF]))
    for n in (10, 20):
        for ex in ("mid", "2r"):
            for ms in (0.5, 1.0):
                vid = f"BOX_n{n}_{ex}_ms{ms}"
                out.append(_variant(
                    vid, "F2", params={"edge_band_n": n, "near_frac": n / 100, "exit": "box_mid" if ex == "mid" else "2R",
                                       "min_stop_pct": ms},
                    timeframes=["1d", "5m"], signal_tf="5m", sides="K_BOX ile aynı (canlı kural)",
                    rule_tr="Box kuralı; kenar bandı × çıkış × en az stop %; diğer parametreler K_BOX'unki",
                    stop="LIVE_RULE (K_BOX) + en az stop %", exit_rule="kutu ortası" if ex == "mid" else "2R hedef",
                    target_r=None if ex == "mid" else 2.0, max_hold_bars="LIVE_RULE (K_BOX gün sonu)", oto_pre_s5=False,
                    wf_class=WF_INTRADAY, inputs=["ohlcv"], alias_of="K_BOX" if vid == "BOX_n10_mid_ms0.5" else None,
                    requires=["copy_books.BOX"]))
    fund_hold = {"24h": 6, "72h": 18}

    def fund_exit(h: str) -> dict:
        return decided("B8", f"'{h}' eki = en çok tutuş ({fund_hold[h]} × 4h bar); çıkış en çok tutuş dolunca (zaman) ya da "
                             "stop 3 ATR(4h)", rule_tr=f"en çok tutuş ({h} = {fund_hold[h]} × 4h)",
                       max_hold_bars=fund_hold[h])

    for h in ("24h", "72h"):
        out.append(_variant(
            f"FUND_FADE_{h}", "H", params={"horizon_label": h, "funding": "f8", "pct_window_days": 20, "short_pct_min": 95,
                                           "long_pct_max": 5},
            timeframes=["4h"], signal_tf="4h", sides=SIDES_LS,
            rule_tr="f8 son 20 günün ≥ %95'liği → short, ≤ %5'liği → long", stop=_atr_stop(3.0, "4h"), exit_rule=fund_exit(h),
            max_hold_bars=fund_hold[h], oto_pre_s5=False, wf_class=WF_H1, inputs=["ohlcv", "funding"]))
    for h in ("24h", "72h"):
        out.append(_variant(
            f"FUND_FADE_LOW_{h}", "H", params={"horizon_label": h, "funding": "f8", "pct_window_days": 20,
                                               "short_pct_min": 95, "long_pct_max": 5, "er20_max": 0.3},
            timeframes=["4h"], signal_tf="4h", sides=SIDES_LS, rule_tr="FUND_FADE ile aynı, yalnız ER20 < 0,3",
            stop=_atr_stop(3.0, "4h"), exit_rule=fund_exit(h), max_hold_bars=fund_hold[h], oto_pre_s5=False, wf_class=WF_H1,
            inputs=["ohlcv", "funding"], requires=[ER20_TF]))
    out.append(_variant(
        "FUND_CARRY", "H", params={"funding": "f8", "enter_abs_pct_min": 90, "exit_abs_pct_below": 50,
                                   "pct_window": decided("B8", "FUND_CARRY yüzdelik penceresi 20 gün (FUND_FADE'inkiyle "
                                                                "aynı)", days=20)},
        timeframes=["4h"], signal_tf="4h", sides="fonlamayı alan taraf",
        rule_tr="|f8| ≥ %90'lık iken fonlama alan taraf; |f8| < %50'lik olunca çık", stop=_atr_stop(3.0, "4h"),
        exit_rule="|f8| < %50'lik", oto_pre_s5=False, wf_class=WF_H1, inputs=["ohlcv", "funding"]))
    return out


def _other() -> list[dict]:
    out: list[dict] = []
    for z in (2.0, 2.5):
        out.append(_variant(
            f"BTCETH_Z_{z}", "I", params={"series": "log(BTC/ETH)", "z_window_days": 20, "z_entry_abs": z, "z_exit": 0.0,
                                          "legs": 2, "leg_sizing": gap("iki bacağın boyut oranı (nominal/beta) yazılmadı")},
            timeframes=["1h"], signal_tf="1h", sides="iki bacak (z yönünün tersi)",
            rule_tr="log oranın 20 günlük z'si; z = 0'da çık; en çok 72 saat; iki bacak",
            stop=gap("spread satırının stopu (R birimi) yazılmadı"), exit_rule="z = 0", max_hold_bars=72, oto_pre_s5=False,
            wf_class=WF_H1, inputs=["ohlcv"]))
    xs_stop = gap("XSEC portföy satırının stopu / R birimi yazılmadı")
    beta = gap("BTC-beta nötrlüğünün beta tahmin penceresi yazılmadı")
    for lb in (7, 28):
        for k in (3, 5):
            out.append(_variant(
                f"XS_MOM_{lb}_k{k}", "J", params={"rank_lookback_days": lb, "k": k, "rebalance": "haftalık",
                                                   "rebalance_time": gap("haftalık yeniden dengeleme günü/saati yazılmadı"),
                                                   "beta_neutral": "BTC", "beta_window": beta, "universe": "PIT"},
                timeframes=["1d"], signal_tf="1d", sides="üst-k long / alt-k short",
                rule_tr=f"kesitsel {lb} gün momentum, haftalık, üst-{k} long / alt-{k} short, BTC-beta nötr", stop=xs_stop,
                exit_rule="sonraki yeniden dengeleme", oto_pre_s5=False, wf_class=WF_H1, inputs=["ohlcv"]))
    out.append(_variant(
        "XS_REV_1D_k5", "J", params={"rank_lookback_days": 1, "k": 5,
                                     "rebalance": gap("XS_REV_1D yeniden dengeleme sıklığı yazılmadı"),
                                     "beta_neutral": gap("XS_REV_1D'de beta nötrlüğü yazılmadı"), "universe": "PIT"},
        timeframes=["1d"], signal_tf="1d", sides="alt-k long / üst-k short (dönüş)", rule_tr="kesitsel 1 günlük dönüş, k = 5",
        stop=xs_stop, exit_rule="sonraki yeniden dengeleme", oto_pre_s5=False, wf_class=WF_H1, inputs=["ohlcv"]))
    g_stop = gap("altın taktiklerinin stopu (R birimi) yazılmadı")
    out.append(_variant(
        "G_LDN_ORB", "G", params={"range_utc": ["07:00", "08:00"], "entry_until_utc": "12:00", "exit_utc": "16:00"},
        timeframes=["1h"], signal_tf="1h", sides="kırılım yönü", rule_tr="07:00–08:00 UTC aralık kırılımı 12:00'ye kadar, "
        "16:00'da çık", stop=g_stop, exit_rule="16:00 UTC", oto_pre_s5=False, wf_class=WF_GOLD, inputs=["ohlcv"]))
    out.append(_variant(
        "G_NY_ORB", "G", params={"range_utc": ["13:00", "14:00"], "exit_utc": "20:00",
                                 "entry_until_utc": decided("B10", "G_NY_ORB giriş son saati 18:00 UTC (LDN'deki 'aralık "
                                                                   "sonu + 4 saat' benzeşimi: 08:00 → 12:00, 14:00 → 18:00)",
                                                            utc="18:00")},
        timeframes=["1h"], signal_tf="1h", sides="kırılım yönü", rule_tr="13:00–14:00 UTC aralığı, 20:00'de çık", stop=g_stop,
        exit_rule="20:00 UTC", oto_pre_s5=False, wf_class=WF_GOLD, inputs=["ohlcv"]))
    out.append(_variant(
        "G_ASIA_DRIFT", "G", params={"window_utc": ["23:00", "07:00"],
                                     "direction": decided("B10", "'önceki gün yönü' = girişin (23:00 UTC) UTC gününden bir "
                                                                 "önceki UTC gününün açılış → kapanış getirisinin işareti "
                                                                 "(kapanmış 1d bar); okunuş: sıfır getiri yön vermez → o gece "
                                                                 "giriş yok (keyfi yön seçilmez)",
                                                          rule="SIGN(close - open) of the previous UTC day",
                                                          zero_return="NO_ENTRY")},
        timeframes=["1h"],
        signal_tf="1h", sides="önceki günün yönü", rule_tr="önceki gün yönünde 23:00 → 07:00", stop=g_stop,
        exit_rule="07:00 UTC", oto_pre_s5=False, wf_class=WF_GOLD, inputs=["ohlcv"]))
    out.append(_variant(
        "G_MONTH_END", "G", params={"last_utc_days": 2}, timeframes=["1d"], signal_tf="1d", sides=SIDES_LO,
        rule_tr="ayın son 2 UTC günü long", stop=g_stop, exit_rule="ay sonu", oto_pre_s5=False, wf_class=WF_GOLD,
        inputs=["ohlcv"]))
    for lb in (28, 56):
        out.append(_variant(
            f"G_XAU_BTC_{lb}", "G", params={"ratio": "XAU/BTC", "momentum_days": lb}, timeframes=["1d"], signal_tf="1d",
            sides=SIDES_LO, rule_tr=f"XAU/BTC oranı {lb} gün momentumu > 0 iken long", stop=g_stop,
            exit_rule="momentum ≤ 0", oto_pre_s5=False, wf_class=WF_GOLD, inputs=["ohlcv", "btc"]))
    return out


COVER_BASES: dict[str, dict] = {
    "T2": {"variants": ["K_T2"]}, "M2": {"variants": ["K_M2"]}, "BOX": {"variants": ["K_BOX"]}, "D4": {"variants": ["K_D4"]},
    "C4": {"variants": [f"K_C4_CV{i:03d}" for i in range(1, 9)], "note_tr": "taban C4 defterinin kendisi (8 varyasyon birlikte)"},
}
COVER_FACTORS: dict[str, dict] = {
    "F_ER": {"kind": "filter", "rule_tr": "T2/M2/D4/C4'te ER20 ≥ 0,3, Box'ta ER20 < 0,3", "er20_min_trend": 0.3,
             "er20_max_box": 0.3},
    "F_BTC": {"kind": "filter", "rule_tr": "BTC 4h rejimi aynı yön",
              "regime": decided("B4", "T2'nin kullandığı regime_gate.btc_regime tanımı BTC 4h barlarına uygulanır: son "
                                      "kapanmış BTC 4h barında kapanış > EMA200 (4h kapanışları, regime_gate.ema_last) → UP, "
                                      "değilse DOWN; EMA200 hesaplanamıyorsa rejim bilinmiyor → giriş yok (regime_gate "
                                      "fail-closed). 'Aynı yön' = LONG yalnız UP'ta, SHORT yalnız DOWN'da",
                                definition="regime_gate.btc_regime", timeframe="4h", ema_len=200)},
    "F_VOL": {"kind": "filter", "rule_tr": "ATR% ≤ son 120 barın %80'liği", "lookback_bars": 120, "atr_pct_max_pctile": 80},
    "X_TRAIL2": {"kind": "exit", "rule_tr": "+1R'den sonra 2 ATR iz süren", "arm_r": 1.0, "trail_atr": 2.0},
    "X_CHAN3": {"kind": "exit", "rule_tr": "chandelier 3 × ATR22", "atr_period": 22, "atr_mult": 3.0},
    "X_TIME": {"kind": "exit", "rule_tr": "max_hold'un yarısı; Box: 48 × 5m", "max_hold_fraction": 0.5, "box_bars_5m": 48},
    "X_TPL": {"kind": "exit", "rule_tr": "1R/2R/3R'de üçte bir", "targets_r": [1.0, 2.0, 3.0], "fraction_each": "1/3"},
}
AUTO_COVER_BASES = ("T2", "M2", "D4", "C4")
AUTO_COVER_FACTORS = ("F_ER", "F_BTC", "F_VOL", "X_TRAIL2", "X_CHAN3", "X_TIME")


def _covers() -> list[dict]:
    out = []
    for base in ("T2", "M2", "BOX", "D4", "C4"):
        for fac in ("F_ER", "F_BTC", "F_VOL", "X_TRAIL2", "X_CHAN3", "X_TIME", "X_TPL"):
            b = COPY_BOOKS[base]
            f = COVER_FACTORS[fac]
            req = [f"copy_books.{base}", f"covers.factors.{fac}"] + ([ER20_TF] if fac == "F_ER" else [])
            out.append(_variant(
                f"O_{base}_{fac}", "O", params={"base": base, "factor": fac,
                                                "factor_params": {k: v for k, v in f.items() if k != "rule_tr"}},
                timeframes=list(b["timeframes"]),
                signal_tf=b["timeframes"][-1], sides="tabanınki", rule_tr=f"{base} + {COVER_FACTORS[fac]['rule_tr']}",
                stop="tabanınki" if COVER_FACTORS[fac]["kind"] == "filter" else "tabanınki (çıkış örtüsüyle)",
                exit_rule="tabanınki" if COVER_FACTORS[fac]["kind"] == "filter" else fac, max_hold_bars="tabanınki",
                oto_pre_s5=base in AUTO_COVER_BASES and fac in AUTO_COVER_FACTORS,
                wf_class=WF_INTRADAY if base == "BOX" else WF_H1, inputs=["ohlcv", "btc_4h"] if fac == "F_BTC" else ["ohlcv"],
                cover={"base": base, "factor": fac, "base_variants": COVER_BASES[base]["variants"]}, requires=req))
    return out


def _scopes() -> dict:
    std = list(FAMILY_GROUPS["STANDARD"])
    cl = {name: {"applies_to": std + ["O"], "unit": "VARIANT", "symbols": name, "instances": 1,
                 "def_tr": "aynı parametre, bir kümenin coin'leri"} for name in CLUSTERS}
    return {
        "POOLED_P23": {"applies_to": std, "unit": "VARIANT", "symbols": "P23", "instances": 1,
                       "def_tr": "aynı parametre, P23'ün 23 coin'i; işlemler birleştirilir"},
        "POOLED_OWN": {"applies_to": ["K", "O"], "unit": "VARIANT", "symbols": "OWN_BOOK", "instances": 1,
                       "def_tr": "kopyalar ve örtüler kendi canlı sembol kümelerinde"},
        **cl,
        "COIN_FIT": {"applies_to": list(COIN_FIT_FAMILIES), "unit": "FAMILY_GRID", "symbols": "P23", "instances": "PER_SYMBOL",
                     "options": "FAMILY_GRID", "id_pattern": "COIN_FIT:<SYM>",
                     "def_tr": "aile ızgarasından coin'e özel seçim (§3.2), P23'ün her coin'i"},
        "COIN": {"applies_to": ["K"], "unit": "VARIANT", "symbols": "P23", "instances": "PER_SYMBOL", "id_pattern": "COIN:<SYM>",
                 "def_tr": "bir kopyanın tek coin'de sonucu"},
        "PORT_XSEC": {"applies_to": ["J"], "unit": "VARIANT", "symbols": "PIT_UNIVERSE", "instances": 1, "def_tr": "portföy"},
        "PAIR_BTCETH": {"applies_to": ["I"], "unit": "VARIANT", "symbols": ["BTC", "ETH"], "instances": 1, "def_tr": "çift"},
        "XAU": {"applies_to": ["G"], "unit": "VARIANT", "symbols": ["XAUUSDT"], "instances": 1, "def_tr": "XAUUSDT vadeli"},
        "PAXG": {"applies_to": ["G"], "unit": "VARIANT", "symbols": ["PAXGUSDT"], "instances": 1, "def_tr": "PAXGUSDT vadeli"},
        "GOLD_POOLED": {"applies_to": ["G"], "unit": "VARIANT", "symbols": ["XAUUSDT", "PAXGUSDT"], "instances": 1,
                        "def_tr": "ikisi birlikte; uzun vekiller (PAXG spot 2020+, Dukascopy XAUUSD) yalnız gösterim, kapıya "
                                  "girmez"},
    }


def _lib() -> dict:
    variants = _copies() + _standard() + _other() + _covers()
    return {
        "id": LIB_VERSION, "doc": "docs/ENGINE_BRAIN_V1.md §2, §4.3 (ana belge §6.2)",
        "defaults": {
            "r_unit": "ilk stop", "max_hold_signal_bars": DEFAULT_MAX_HOLD, "sides": {"LO": "yalnız long", "LS": "iki yön"},
            "er20": "20 barlık verim oranı (Kaufman)",
            "er20_timeframe": decided("B3", "ER20 varyantın sinyal diliminde hesaplanır (ATR ile aynı); örtülerde (F_ER) "
                                            "tabanın sinyal diliminde (T2/M2 1d, D4/C4 4h); Box tabanında (sinyal dilimi "
                                            "5m) 1d, çünkü kutu günlüktür", rule="SIGNAL_TF", covers="BASE_SIGNAL_TF",
                                      box="1d"),
            "atr": "ATR14 (sinyal dilimi)",
        },
        "contract": {
            "package": "tradingbot/strategy_lib/ (saf: G/Ç, saat, ağ yok; research_engine'i import etmez; motor ve worker aynı "
                       "modül)",
            "signals": "signals(bars, ctx) -> list[Intent]", "manage": "manage(pos, bars, ctx) -> Action",
            "bars": "{tf: kapanmış bar dizisi}", "ctx": ["now_ms", "btc_bars?", "funding?"],
            "intent": ["signal_ts", "side", "entry_ref", "stop", "targets[]", "exit_rule", "max_hold_bars",
                       "one_entry_per_signal", "risk_atr_bounds"],
            "action": ["NONE", "CLOSE(reason)", "MOVE_STOP(price)"], "action_on": "yalnız kapanmış bar",
            "copies": "K_* yeniden yazılmaz: paper_rules.decide_from_rows ve pattern_trader kural fonksiyonları çağrılır",
            "spec_sha_fields": ["family", "variant_id", "params", "timeframes", "exits", "sizing", "module_src_sha"],
            "spec_sha": "sha256(kanonik JSON); aynı spec_sha iki kez deneme sayılmaz; farklı kapsam farklı satırdır; alias_of'lu "
                        "ızgara üyesi tabanının spec'ini taşır",
            "kernel": "research_engine/kernel.py (WF_PROTOCOL_V1.kernel)",
            "parity_fail_label": "PARITY_FAIL (kopya lider tablosuna ve kapıya girmez)",
        },
        "sizing": {"risk_usdt": 1, "base_equity_usdt": 200, "risk_pct": 0.5, "leverage": "büyüklükten türer",
                   "same_as": "keşif katmanı (§5.1) ve P4 adayı (§6.1)"},
        "families": {code: {"name_tr": t[0], "timeframes_tr": t[1], "unique_variants": t[2], "oto_column_pre_s5_tr": t[3]}
                     for code, t in FAMILY_TABLE.items()},
        "family_groups": {k: list(v) for k, v in FAMILY_GROUPS.items()},
        "variants": variants,
        "copy_books": {k: {"book": v["book"], "rule": v["rule"], "timeframes": v["timeframes"], "own_symbols": v["own_symbols"],
                           "max_hold": v["max_hold"]} for k, v in COPY_BOOKS.items()},
        "covers": {"bases": COVER_BASES, "factors": COVER_FACTORS, "id_pattern": "O_<taban>_<faktör>",
                   "scope_tr": "örtüler yalnız bu beş canlı tabana uygulanır (sonuca bakılarak seçilmiş alt küme değil); aile "
                               "üst sınırı (ana belge §6.2 '≤ 8–12') O'da taban başına 7"},
        "p23": {"symbols": list(P23), "quote": "USDT", "market": "USDⓈ-M perpetual",
                "basis_tr": "D4/C4'ün turda zaten çekilen 23 coin'i (mühürlü)"},
        "clusters": {"members": {k: list(v) for k, v in CLUSTERS.items()},
                     "basis_tr": "performansa değil, mühür anındaki sabit niteliğe göre"},
        "non_p23_tr": "giriş evreninin geri kalanı yalnız keşif katmanında izlenir; kısa geçmişle coin satırı açılmaz",
        "scopes": _scopes(),
        "budget_row_types": {
            "POOLED": "STANDARD × POOLED_P23", "CLUSTER": "STANDARD × CL_*", "COIN_FIT": "STANDARD aileleri × COIN_FIT:<SYM> "
            "(ham deneme = aile ızgarası × 23)", "COPY": "K × (POOLED_OWN + COIN:<SYM>)", "COVER": "O × (POOLED_OWN + CL_*)",
            "OTHER": "J × PORT_XSEC + I × PAIR_BTCETH + G × (XAU, PAXG, GOLD_POOLED)"},
        "budget_declared": {"POOLED": [45, 45], "CLUSTER": [135, 135], "COIN_FIT": [161, 1081], "COPY": [312, 312],
                            "COVER": [140, 140], "OTHER": [25, 25], "TOTAL": [818, 1738], "PRIOR_MIN": 2775,
                            "N_AT_SEAL_MIN": 4513, "COIN_FIT_GRID": {"B": 10, "C": 8, "D": 8, "E": 4, "F1": 4, "F2": 8, "H": 5},
                            "UNIQUE_VARIANTS": 106, "EXPLORE_SERIES": 3745, "OTO_COLUMN_PRE_S5": 64},
        "budget_rules_tr": [
            "üst sınırdır: §2.4 madde 1'i geçmeyen coin satırı açılmaz ama yine sayılır (seçenek değerlendirildiyse)",
            "sayılar mühürden sonra küçültülemez",
            "keşif serileri v1'in kapısına girmez, bir sonraki LIB sürümü mühürlenirken N'e eklenir (görülmüş sayılır)",
            "kapsamlar sonuçlara bakılmadan sabitlendi"],
        "specialisation_rules": {
            "1_min_data_tr": "COIN_FIT/COIN satırı için: P23'te; mühürden önce ≥ 730 gün (1h ve üstü) veya ≥ 240 gün (5m) "
                             "geçmiş; boşluk ≤ %1 ve arşivle doğrulanmış satır payı ≥ %99; varyantın (ya da aile ızgarasının "
                             "medyanının) mühür öncesi tüm pencerede o coin'de ürettiği işlem sayısı ≥ 100. Sayım yalnız "
                             "girişleri sayar, sonuçlara bakmaz; ilk gece hesaplanıp trials.jsonl'a (ELIG) yazılır ve donar",
            "2_shrinkage": {"score": "s_c(v) = (n_c·r̄_c + k·r̃_K) / (n_c + k)", "cluster": "r̃_K = (n_K·r̄_K + k·r̄_P) / (n_K + k)",
                            "r_bar": "ADVERSE ortalama net R", "K": "coin'in kümesi (coin hariç)", "P": "havuz",
                            "selection_tr": "§3.2 üç yollu düzen; fark < 0,02R ise havuzun seçimi alınır"},
            "3_cluster_coherence_tr": "COIN_FIT/COIN satırı Kapı A'yı ancak aynı seçimin/kopyanın coin hariç kümesindeki OOS "
                                      "ADVERSE ortalama net R'ı ≥ 0 ise geçer (Kapı A'ya ek ön kayıt koşulu; gevşetme değil)",
            "4_single_instrument_tr": "havuz ve küme satırlarında tek enstrüman OOS kârın en çok %50'si; coin satırları muaf "
                                      "(yerine madde 3)",
            "4_profit_share_def": decided("B11", "'OOS kârın payı' = enstrümanın OOS net R'ı / net R'ı pozitif enstrümanların "
                                                 "net R toplamı (daha sıkı; toplam ≤ 0 iken satır zaten geçemez)",
                                          denominator="SUM_POSITIVE_INSTRUMENT_NET_R"),
            "5_full_count_tr": "coin satırları BH ailesine ve DSR N'ine tam girer; COIN_FIT'in her ızgara seçeneği ayrı ham deneme",
            "6_promotion_tr": "aynı coin'de en çok 1, toplamda en çok 2 tek-coin otomatik defter (§7.6)",
        },
        "transforms": {
            "doc": "§4.3 (ana belge §5.9); dönüşüm kodu LIB_v1 mührünün parçasıdır; tek otomatik fikir üreticisi",
            "list": {
                "T1": {"name": "CELL_FILTER", "arg": "I=c", "from_lesson": "FİLTRE: taban T, enstrüman c'de kaybettiriyor",
                       "spec_tr": "T'nin kümesi − {c}", "scope": "POOLED_OWN", "dedup": None},
                "T2": {"name": "STOP_FLOOR", "arg": "k", "from_lesson": "IZGARA stop ekseni",
                       "domain": {"k_atr": [0.75, 1.0, 1.5, 2.0], "cfgrid_v1_cells": ["S_ATR075", "S_ATR100", "S_ATR150",
                                                                                     "S_ATR200"]},
                       "spec_tr": "T + stop kuralı", "scope": "ders hücresinin kapsamı", "dedup": None},
                "T3": {"name": "EXIT_VARIANT", "arg": "e", "from_lesson": "IZGARA çıkış ekseni",
                       "domain": {"cfgrid_v1_cells": ["X_T1R", "X_T2R", "X_T3R", "X_TRAIL2ATR", "X_NONE_TIME"],
                                  "tr": "1R/2R/3R, 2 ATR iz süren, hedefsiz + zaman"},
                       "spec_tr": "T + çıkış", "scope": "ders hücresinin kapsamı",
                       "dedup": "X_* ile spec_sha aynıysa yeni deneme yok, o satıra bağlanır"},
                "T4": {"name": "EXCLUDE_REGIME", "arg": "X", "from_lesson": "FİLTRE: taban T, kova X'te kaybettiriyor",
                       "domain": {"bucket": "situation_v1 (4h trend × 4h oynaklık) kümesi"},
                       "spec_tr": "T + 'X'te giriş yok'", "scope": "POOLED_OWN",
                       "dedup": "O ailesindeki F_ER/F_BTC/F_VOL ile spec_sha aynıysa yeni deneme yok, o satıra bağlanır"},
            },
            "no_transform_tr": {"T yalnız c'de kazanıyor": "zaten LIB_v1 satırı COIN:c (yeni deneme yok)",
                                "MALİYET": "v1'de yok", "AYRIM": "v1'de yok"},
            "domains_tr": "kova ∈ situation_v1 kümesi, coin ∈ P23, stop k ∈ {0,75; 1; 1,5; 2}, çıkış ∈ sabit liste",
            "live_rule_check_tr": "canlıda zaten olan kural (ham config'e karşı denetim; ör. Box en az stop %0,5) aday olmaz",
            "derived_specs": {"file": "library/derived_specs.jsonl (yalnız eklenir, motor yazar)",
                              "monthly_seal": "LIB_v1.<AAAA-AA>, her ayın 1'inden sonraki ilk gece",
                              "order_tr": "düşük q ve büyük |Δ| önce", "own_t_seal": True,
                              "dedup_family_tr": "deneme tekilleştirmesi LIB AİLESİNDE yapılır: aynı (spec_sha, kapsam) "
                                                 "LIB_v1 ve LIB_v1.<AAAA-AA> mühürleri arasında BİR kez sayılır (T3/T4 'o "
                                                 "satıra bağlanır'); yeni ana sürüm (LIB_v2) ayrı ailedir, yeniden sayılır",
                              "new_family_or_transform_tr": "kod PR'ı (yeni LIB sürümü)"},
            "existing_books_tr": "hiçbir dönüşüm mevcut defteri değiştirmez; geçen aday yalnız yeni defter olabilir (§7)",
        },
        "explore": {"doc": "§5.1", "entry_universe_size": 40, "gold_instruments": ["XAUUSDT", "PAXGUSDT_FUT", "PAXG_SPOT"],
                    "formula_tr": "45 × 40 + 13 × 40 + 35 × 40 + 5 + 2 + 6 × 3"},
        "readings_tr": [
            "LO = yalnız long (sinyal tersine dönünce pozisyon yok); LS = iki yön",
            "F2 'n10/n20' = kenar bandı kutu yüksekliğinin %10/%20'si (near_frac 0,10/0,20): BOX_n10_mid_ms0.5 ≡ K_BOX ve "
            "K_BOX'un canlı near_frac'ı 0,10; 'mid' = box_mid çıkışı, '2r' = 2R hedef",
            "F2'nin diğer bütün parametreleri K_BOX'unkidir (ızgara yalnız üç ekseni oynatır)",
            "F1 BBRE 'orta bant' = kendi BB(20,2)'sinin orta çizgisi; RSI2'de de sinyal dilimindeki BB(20,2) orta çizgisi "
            "(sahip kararı 2026-10-11, B5)",
            "H 'f8' = 8 saate normalize fonlama oranı (fut_v1 laboratuvarının tanımı: oran × 8 / aralık)",
            "I: z yüksekken oran pahalı → z'nin tersi yönde iki bacak; z = 0'da çık (ortalamaya dönüş)",
            "J XS_REV_1D: 'dönüş' = alt-k long / üst-k short",
            "G ORB yönü kırılımın yönüdür; G_XAU_BTC 'momentum > 0 iken long' → momentum ≤ 0'da çık",
            "kopyaların (K_*) bakiye/kaldıraç/slot ayarları spec'e girmez: çekirdek sabit 1 USDT risk ve büyüklükten türeyen "
            "kaldıraç kullanır (§2.1)",
        ],
        "owner_decisions": [
            {"date": "2026-10-06", "id": "S5", "text_tr": "5m, 1h, altın ve fonlama taktikleri de otomatik yola girer; worker'ın ek "
             "veri çekmesi AUTO_v1 sürümüne eklenir (A/B ile ölçülür). S5, §2.2'nin 'Oto' sütununu GEÇERSİZ KILAR: varyant "
             "alanı oto_column_pre_s5 yalnız S5 ÖNCESİ tablonun tarihsel kaydıdır (H, G, 1h ve 5m için artık yanlıştır) ve "
             "hiçbir yönlendirmede kullanılmaz. Otomatik yol / Kapı C yönlendirmesi YALNIZ S2'nin AUTO_CATALOG_V1'iyle yapılır"},
            {"date": "2026-10-06", "id": "AUTO", "text_tr": "Kapı A + Kapı B + aylık Holm'u geçen aday yeni bir PAPER defteri "
             "olarak açılır; kapılar ve eşikler bu karardan sonra gevşetilmez"},
            {"date": "2026-10-06", "id": "S8", "text_tr": "otomatik defter başlangıç özsermayesi 1.000 USDT (S2: AUTO_POLICY_V1)"},
            {"date": "2026-10-11", "id": "P3A_GAPS", "text_tr": "ENGINE_BRAIN §8.5 P3a boşlukları: 'Önerilenleri kabul et' — "
             "önerilen varsayılanların hepsi kabul (B1 (b), B2 (a), B3, B4, B5, B6, B7, B8, B10 NY giriş son saati ve Asya "
             "yönü, B11); öneri olmayanlar (B9: spread stopu/bacak oranı, XSEC stopu/R birimi, beta penceresi, dengeleme anı, "
             "XS_REV sıklığı ve beta nötrlüğü; B10: altın stopları) boşluk kalır ve o varyantlar çalıştırılmaz. Dolan "
             "alanlar reading_tr'de 'sahip kararı 2026-10-11, §8.5 önerilen' ile işaretlidir; S1 düzeltme mührüyle yeniden "
             "mühürlendi (_v2 değil: henüz hiçbir gerçek veri koşusu yok)"},
        ],
        "thresholds": {
            "parity_kernel": {"min_synthetic_trades_per_family": 200, "min_signal_identity_share": 1.0,
                              "max_median_abs_dr": 0.02, "max_p95_abs_dr": 0.05},
            "parity_copy": {"min_policy_entries_per_epoch": 20, "min_same_bar_side_share": 0.95, "max_median_abs_dr": 0.05},
            "sizing": {"min_liq_distance_stop_mult": 2.0},
            "eligibility": {"min_history_days_1h_plus": 730, "min_history_days_5m": 240, "max_gap_share": 0.01,
                            "min_archive_verified_share": 0.99, "min_pre_seal_trades": 100},
            "shrinkage": {"k_trades": 50, "pool_tie_margin_r": 0.02},
            "cluster_coherence": {"min_ex_coin_oos_adverse_mean_r": 0.0},
            "single_instrument": {"max_oos_profit_share": 0.5},
            "promotion_side": {"max_single_coin_books_same_coin": 1, "max_single_coin_books_total": 2},
            "derived_specs": {"max_specs_per_month": 20},
            "transforms": {"stop_floor_k_atr": [0.75, 1.0, 1.5, 2.0]},
        },
    }


# ============================================================================ WF_PROTOCOL_V1 (§3.1–§3.3, §2.1 çekirdek, §5.2 önbellek)
def _wf() -> dict:
    return {
        "id": "WF_PROTOCOL_v1", "doc": "docs/ENGINE_BRAIN_V1.md §3.1–§3.3, §2.1, §5.2 (ana belge §6.3)",
        "engine": "quant.walkforward.make_folds(validation_days > 0) + run_three_way (her adım yalnız kendi verisini görür)",
        "classes": {
            WF_H1: {"applies_tr": "sinyal dilimi 1h, 4h, 1d", "mode": "anchored",
                    "train_tr": "veri başından genişleyen (make_folds anchored: train_days = ilk eğitim)"},
            WF_INTRADAY: {"applies_tr": "sinyal dilimi 5m, 15m", "mode": "rolling", "train_tr": "kayan"},
            WF_GOLD: {"applies_tr": "altın vadelileri (XAUUSDT, PAXGUSDT)", "mode": "anchored",
                      "train_tr": "genişleyen", "label": "KISA_GEÇMİŞ"},
        },
        "step_equals_test": True,
        "data_start_tr": "serinin listelendiği gün + ısınma barları (göstergeler); en erken 2020-01-01",
        "data_start_earliest": "2020-01-01",
        "purge_embargo": {"each_boundary_tr": "her iki sınırda 1 sinyal barı + max_hold",
                          "make_folds_split": decided(
                              "B1 (b)", "her sınırdaki TOPLAM boşluk purge_bars + embargo_bars = 1 sinyal barı + max_hold "
                              "(§3.1; make_folds yalnız toplamı kullanır: gap = (purge_bars + embargo_bars) × bar); "
                              "bölüşüm purge_bars = ⌊(1 + max_hold) / 2⌋, embargo_bars = kalan (tek bar fazlası ambargoda; "
                              "max_hold 300 → 150 + 151, §8.5 örneği). max_hold = varyantın sinyal dilimindeki en çok "
                              "tutuşu (exits.max_hold_bars; kopyalarda copy_books.<taban>.max_hold). Sızıntıyı (a) kadar "
                              "örter (sınırdan önce açılan işlemin tamamı; çapalı WF'de eğitim hep testten öncedir)",
                              sum_bars="1 + max_hold", purge_bars="floor((1 + max_hold) / 2)",
                              embargo_bars="(1 + max_hold) - purge_bars",
                              example={"max_hold": 300, "purge_bars": 150, "embargo_bars": 151})},
        "leakage_tr": "leakage_check geçmeyen katman atılır (LEAK)",
        "oos_tr": "OOS = test katmanlarının birleşimi, her satır türünde aynı tanım (CSCV ortak zaman ekseni)",
        "fixed_param_rows_tr": "sabit parametreli satırların doğrulama dilimi yalnız gösterilir; seçim yok (parametreler mühürde)",
        "positive_fold_share_tr": "ADVERSE ortalaması > 0 olan test katmanlarının oranı (yalnız ≥ 5 işlemli katmanlar)",
        "rolling_role_tr": "kayan pencere ikinci bir satır değildir; çapalı sonucun yanında sağlamlık sütunu",
        "selection_coin_fit": {
            "fit_fn": "eğitim diliminde her ızgara seçeneğinin coin, küme (coin hariç) ve havuz ADVERSE istatistikleri",
            "candidates_fn": "LIB_V1 specialisation_rules.2 büzülmüş puanına göre en iyi 3 seçenek",
            "select_fn": "doğrulama diliminde aynı büzülmeyle en iyisi (eşitlikte veya fark < LIB pool_tie_margin_r ise havuzun "
                         "seçimi)",
            "evaluate_fn": "seçilen seçenek test diliminde; katmanlar birleşir",
            "log": "seçim her katmanda trials.jsonl'a SELECT satırı",
        },
        "costs": {
            "scenarios": {
                "base": {"fee_tr": "taker %0,05 × 2", "slippage_tr": "3 bps", "extra_tr": "gerçek fonlama, gerçek uzlaşma anlarında"},
                "ADVERSE": {"fee_tr": "taker %0,05 × 2", "slippage_tr": "6 bps", "extra_tr": "+ 1 tick spread (serinin fiyat adımı)",
                            "gate": True},
                "STRESS": {"fee_tr": "taker %0,07 × 2", "slippage_tr": "9 bps", "extra_tr": "+ 1 tick spread"},
            },
            "funding_tr": "her senaryoda gerçek (vekil yok); ADVERSE/STRESS yeniden maliyetlendirmesi fonlamayı değiştirmez",
            "funding_gap_tr": "funding verisi eksik dönemdeki işlem FUNDING_GAP ile işaretlenir ve kapı istatistiğinden çıkar "
                              "(sayısı gösterilir)",
            "recost_tr": "üç senaryo aynı yol üzerinde aritmetik yeniden maliyetlendirme",
        },
        "kernel": {
            "module": "research_engine/kernel.py", "decision": "bar kapanışında",
            "fill": "sonraki barın açılışında ± kayma", "intrabar_order": "learning_cf kuralı: açılış → ters → lehte → kapanış",
            "stop_fill": "seviyede; boşlukta açılışta", "funding": "deponun gerçek uzlaşma zaman damgalarında",
            "liquidation": "bracket formülü", "sizing": "LIB_V1.sizing (sabit 1 USDT risk = 200 USDT × %0,5)",
            "leverage": "büyüklükten türer; likidasyon mesafesi ≥ LIB min_liq_distance_stop_mult × stop",
        },
        "cache": {"path": "backtests/cache/<spec_sha>/<kapsam>/<veri_ay_mührü>.parquet", "content": "işlem listesi + günlük getiri",
                  "invalidate": "ay parçasının mührü değişince", "rotation": "her gece 1/7 sıfırdan hesaplanır ve karşılaştırılır",
                  "mismatch": "WF_CACHE_MISMATCH → seri yeniden kurulur"},
        "thresholds": {
            "folds": {
                WF_H1: {"min_train_days": 365, "validation_days": 90, "test_days": 90, "step_days": 90},
                WF_INTRADAY: {"train_days": 120, "validation_days": 30, "test_days": 14, "step_days": 14},
                WF_GOLD: {"min_train_days": 180, "validation_days": 30, "test_days": 30, "step_days": 30},
            },
            "warmup_bars": 210,
            "purge_signal_bars": 1,
            "positive_fold_min_trades": 5,
            "candidates_top_k": 3,
            "costs": {
                "base": {"taker_fee_pct": 0.05, "fee_sides": 2, "slippage_bps": 3, "spread_ticks": 0},
                "ADVERSE": {"taker_fee_pct": 0.05, "fee_sides": 2, "slippage_bps": 6, "spread_ticks": 1},
                "STRESS": {"taker_fee_pct": 0.07, "fee_sides": 2, "slippage_bps": 9, "spread_ticks": 1},
            },
        },
    }


# ============================================================================ STATS_V1 (§3.6, §3.5 N)
def _stats() -> dict:
    return {
        "id": "STATS_v1", "doc": "docs/ENGINE_BRAIN_V1.md §3.5–§3.6 (ana belge §6.4)",
        "p_day": {
            "h0": "ortalama net R ≤ 0", "cost": "ADVERSE", "test": "gün kümelenmeli (günler yeniden örneklenir) tek yönlü bootstrap",
            "null": "sıfır ortalamaya kaydırılmış dağılım",
            "seed": "sha256(row_id|look_no)'nun ilk 8 baytı (int(hexdigest[:16], 16), cfgrid_v1 tohum biçimi)",
            "rng": decided("B6", "bootstrap üreteci Python random.Random (Mersenne Twister), tohumu cfgrid_v1 biçimiyle",
                           algorithm="random.Random",
                           seeding="random.Random(int(sha256(f'{row_id}|{look_no}'.encode('utf-8')).hexdigest()[:16], 16))"),
            "chunk_resamples": 1000, "memory_mb_max": 16,
            "single_statistic_tr": "Kapı A, Kapı B, Kapı C ve ders bakışları aynı istatistiği kullanır; CI'lar yalnız gösterim",
        },
        "bh_fdr": {"family_tr": "o Kapı A bakışında değerlendirilen BÜTÜN etkin LIB satırları (spec × kapsam/enstrüman; kapıya "
                                "kapalı XSEC ve UNSPECIFIED yüzünden çalıştırılmayanlar dahil); işlem sayısı süzgeci YOK",
                   "no_p_tr": "p_day'i hesaplanamayan satır (işlemsiz, çalıştırılmamış) aileye p = 1 ile girer (m küçülmez)",
                   "reading_tr": "ENGINE_BRAIN §3.6'daki '≥ 30 OOS işlemi olan' süzgeci m'yi küçültür (BH'yi gevşetir); ana "
                                 "belge §6.4 'aile = o bakışta değerlendirilen bütün spec × enstrüman satırları' daha sıkıdır "
                                 "ve ENGINE_BRAIN önsözü gereği o geçerlidir"},
        "dsr": {
            "function": "validation.deflated_sharpe(sr, n_trials=N, T, skew, kurt, sr_var=...)",
            "sr": "OOS günlük getirinin Sharpe'ı (1 USDT risk, 200 USDT tabanı; pozisyonsuz gün 0)", "T": "OOS gün sayısı",
            "skew_kurt": "ampirik",
            "sr_var": "o bakışın ailesindeki satırların günlük Sharpe'larının varyansı, tabanı 1/(T−1) (fonksiyonun 0,01 "
                      "varsayılan tabanı kullanılmaz; okunuş kesinleştirmesi, gevşetme değil)",
            "sr_var_floor": "1/(T-1)",
            "N": "N_raw_cum + N_lesson_cum + N_explore_cum (o anki değer; trials.jsonl)",
            "effective_n_tr": "|ρ| > 0,7 hiyerarşik kümelemeyle etkin N yalnız ikincil sütun; hiçbir kapıda kullanılmaz",
        },
        "pbo": {
            "method": "CSCV (cscv.py)", "matrix": "gün × yapılandırma (OOS günlük getiri)", "splits": "C(16,8) bölünmenin hepsi",
            "definition": "IS-en-iyi yapılandırmanın OOS göreli sırasının logit ≤ 0 olduğu bölünme payı",
            "groups": {"standard": "aynı kapsamdaki ailenin bütün varyantları", "COIN_FIT": "o coin'deki ızgara",
                       "COIN": "kopyanın 23 coin satırı (coin seçimi)",
                       "copy_cover_pooled_cluster": "{kopya + 7 örtüsü}", "gold": "enstrüman başına 6 varyant",
                       "XSEC": "5", "spread": "2"},
            "not_computable_tr": "grup < 2 yapılandırma veya < 16 × 5 OOS gün → 'hesaplanamaz' → kapı geçmez",
        },
        "placebo": {
            "per_trade_tr": "her gerçek işlem için K rastgele işlem: aynı sembol, yön, tutuş süresi (bar), ATR katı stop; giriş "
                            "zamanı aynı test katmanında kuralın sinyal vermediği barlardan (gerçek işlemlerin ± tutuşu hariç) "
                            "düzgün",
            "seed": "sha256(row_id|trade_key|k)",
            "seed_int": decided("B6", "plasebo tohumu cfgrid_v1 biçimiyle tam sayıya çevrilir (sha256'nın ilk 8 baytı) ve "
                                      "çekimler random.Random ile yapılır; trade_key = günlükteki tj_v1 anahtarı",
                                seed_int="int(sha256(f'{row_id}|{trade_key}|{k}'.encode('utf-8')).hexdigest()[:16], 16)",
                                rng="random.Random(seed_int)",
                                trade_key="tj_v1 trade_key (defter|trade_id|opened_at; research_engine/journal.py)"),
            "diff": "gerçek − plasebo ortalaması, gün kümelenmeli CI",
            "flipped_sign_tr": "işaret çevrilmiş sinyal yalnız gösterilir",
            "lab_verdicts_tr": "signal_lab.verdict / verdict_strict yalnız gösterim sütunu",
        },
        "null_world": {"tr": "boş dünyada (aynı satır yapısı, rastgele yürüyüş) Kapı A geçme oranı ≤ bh q; dikilmiş avantajlı "
                             "dünyada geçer; oran raporlanır", "pass_rate_max": "bh_q"},
        "looks_tr": "Kapı A yalnız aylık kayıtlı bakışta (her ayın 5'inden sonraki ilk gece); ara gece 'ara görünüm'",
        "thresholds": {
            "p_day": {"resamples": 10000},
            "bh_q": 0.10,
            "effective_n_abs_rho": 0.7,
            "pbo": {"blocks": 16, "min_configs": 2, "min_oos_days_per_block": 5},
            "placebo": {"k_per_trade": 5, "ci_level": 0.95, "min_ci_lower": 0.0},
            "null_world": {"worlds": 200},
        },
    }


# ============================================================================ LESSON_REGISTRY_V1 (§4.1, §4.2, §4.4)
def _lessons() -> dict:
    return {
        "id": "LESSON_REGISTRY_v1", "doc": "docs/ENGINE_BRAIN_V1.md §4.1, §4.2, §4.4 (ana belge §5.7–§5.8)",
        "inputs": {"attribution": "P2b attribution/YYYY-MM.jsonl.gz (attribution_v1 kodları, cfgrid_v1 hücreleri, R ayrıştırması)",
                   "journal": "tj_v1 (kohort, config dönemi, giriş kovası)",
                   "evidence_kinds": {"REAL": "gerçek işlem", "CF": "karşı-olgusal (cf_aux_v1 muhafazakâr R)"},
                   "separation_tr": "REAL ve CF ayrı satır, birleştirilmez; POLICY ve öğrenme-ekstra kohortları karışmaz; hücreler "
                                    "config dönemi (CONFIG_EPOCHS_V1) içinde hesaplanır"},
        "cells": {"L0": "defter", "L1": "defter × taktik", "L2": "L1 × enstrüman",
                  "L3": "L1 × durum kovası (situation_v1 4h trend × 4h oynaklık)", "L4": "L1 × enstrüman × kova",
                  "rule_tr": "veri olan en kaba düzey kullanılır; çapraz derinlik ≤ 2"},
        "cell_stats": ["n ve gün sayısı", "ortalama net R", "Wilson kazanma oranı", "ebeveyne HierarchicalRate büzülmesi",
                       "gün kümelenmeli CI95 (sabit tohum)", "her EX_ANTE cfgrid_v1 hücresi için gerçeğe göre eşli ΔR ve CI",
                       "p_day (STATS_V1)"],
        "ci": {"method_tr": "gün kümelenmeli bootstrap CI95 (thresholds.ci)",
               "seed": decided("B6", "ders CI95'inin sabit tohumu sha256(cell_id|look_no), cfgrid_v1 biçimiyle tam sayıya "
                                     "çevrilir; üreteç random.Random", seed="sha256(cell_id|look_no)",
                               seed_int="int(sha256(f'{cell_id}|{look_no}'.encode('utf-8')).hexdigest()[:16], 16)",
                               rng="random.Random(seed_int)")},
        "lesson_types": {
            "GRID": {"tr": "IZGARA: EX_ANTE hücre ΔR > 0", "candidates": True},
            "FILTER": {"tr": "FİLTRE: hücrede atla, eşli Δ = −r", "candidates": True},
            "COST": {"tr": "MALİYET: yalnız raporlanır (cfgrid_v2 ister)", "candidates": False},
            "DISCRIMINATION": {"tr": "AYRIM: yalnız walk-forward OOS AUC CI > 0,5 ise raporlanır", "candidates": False},
        },
        "looks": {"tr": "hücrenin n'i eşikleri geçtiği gece; her bakış bir LESSON_LOOK satırı (N_lesson_cum +1)",
                  "bh_family_tr": "o gece bakışı olan BÜTÜN (hücre, ders türü/EX_ANTE hücresi) çiftleri",
                  "overflow_tr": "gecelik üst sınırı aşan çiftler (n'i büyük olan önce, sonra cell_id) ertesi geceye kalır ve sayılır"},
        "forward_validation": {"tr": "bulgu T_k anında hash'lenir (RESEARCH_HYPOTHESIS); doğrulama yalnız T_k'dan sonra kapanan "
                                     "işlemlerle, hücrenin bir sonraki bakışında: aynı işaret, p_day ≤ o bakışın alfası, ≥ 3 "
                                     "aylık katmanın ≥ 2/3'ünde işaret tutarlılığı"},
        "store": {"module": "learn/lesson_store (lesson_v2, build_lesson, transition) + learn/journal_archive.SegmentArchive",
                  "root": "data/research/lessons", "keys": ["B|", "I|", "A|", "T|", "V|", "C|", "X|", "E|"],
                  "untouched": "state/lesson_archive"},
        "states": ["OBSERVATION", "RESEARCH_HYPOTHESIS", "VALIDATED_POLICY_CANDIDATE", "APPLIED_BOUNDED", "REJECTED", "RETIRED"],
        "applied_bounded_tr": "yalnız sahibin engine-query --approve komutu ve normal sürümle, yalnız mevcut defter değişikliğinde; "
                              "gece çalıştırıcısı APPLIED_BOUNDED yazamaz (test)",
        "auto_promotion_tr": "otomatik terfi bir ders durumu değildir: ders VALIDATED_POLICY_CANDIDATE kalır ve linked_promo_id alır",
        "retired_tr": "sonraki bakışta işaret döndü; ya da attribution_v1/cfgrid_v1/veri mührü değişikliği dersin işlemlerini "
                      "geçersizleştirdi (yeniden hesaplanır, yeni LESSON_LOOK satırları)",
        "journal_prior_tr": "P2b'nin geçici regime_fit önceli (JOURNAL_PRIOR) ders deposu kurulunca LESSON_PRIOR'a geçer "
                            "(as_of < opened_at)",
        "trial_cost": {
            "attribution": "trials yok; N etkisi yok (hüküm yok)", "LESSON_LOOK": "1 / (hücre, tür) / bakış; N_lesson_cum +1",
            "SPEC": "1 / türeyen spec (+ ızgara seçeneği varsa OPTION); N_raw_cum +1 (+ seçenekler)",
            "LOOK_A": "1 / satır / ay; N etkisi yok (BH + DSR + ileri okuma öder)",
            "EXPLORE_SEEN": "sürüm mühründe; sonraki sürümde N_explore_cum += seri sayısı",
            "LOOK_B": "1 / aday / bakış; N etkisi yok (alfa harcaması öder)", "HOLM": "1 / aday / ay; N etkisi yok (aile hatası öder)",
        },
        "thresholds": {
            "eligibility": {"min_n": 30, "min_distinct_days": 10},
            "looks": {"n_thresholds": [30, 60, 120, 240], "alphas": [0.01, 0.01, 0.015, 0.015], "alpha_total": 0.05},
            "bh_q": 0.10,
            "nightly_pair_cap": 2000,
            "hierarchical_rate_alpha": 10,
            "ci": {"bootstrap_resamples": 5000, "level": 0.95},
            "forward_validation": {"min_monthly_folds": 3, "min_sign_consistent_share": 2 / 3},
            "discrimination": {"min_auc_ci_lower": 0.5},
        },
    }


# ============================================================================ PIT_RULES_V1 (§3.4)
def _pit() -> dict:
    return {
        "id": "PIT_RULES_v1", "doc": "docs/ENGINE_BRAIN_V1.md §3.4",
        "module": "pit_universe.py (veri birimi; gece import grafiğine girmez)",
        "source": {"listing": "data.binance.vision S3 listesi ?prefix=data/futures/um/monthly/klines/&delimiter=/ (delist "
                              "olmuşlar dahil)",
                   "per_symbol_tr": "ilk ve son aylık 1d zip'i; son ayda günlük zip'lerle gün düzeyi",
                   "cross_check": ["exchangeinfo/ anlık görüntüleri", "store/_meta/delisted.json (P1b)"]},
        "output": {"path": "pit_universe/listings.json", "fields": ["symbol", "first_day", "last_day|null", "status", "source"]},
        "membership_tr": "gün d: d − 60 günden önce listelenmiş, d'de delist olmamış ve d'den önceki 30 günün (d − 30 … d − 1) "
                         "medyan günlük hacmi (USDT) ≥ eşik (config universe.min_quote_volume_24h ile aynı)",
        "membership": {"listed_before": "first_day < d − listed_min_days", "not_delisted": "last_day is null or d ≤ last_day",
                       "volume": "median(quote_volume_1d over days d-30 … d-1) ≥ min",
                       "volume_window_includes_d": decided("B11", "30 günlük hacim penceresi d'yi İÇERMEZ: d − 30 … d − 1 "
                                                                  "(d'nin hacmi gün bitmeden bilinmez)",
                                                           includes_d=False, window="d-30 … d-1")},
        "data_need_tr": "bütün tarihî USDⓈ-M sembollerinin 1d mumları: tek seferlik ≈ 25 bin küçük zip (≈ 100 MB), sonra günlük "
                        "ek; --backfill-pit P1b doldurma kurallarıyla (pencereler, disk, .CHECKSUM)",
        "usage_tr": "XSEC satırları YALNIZ PIT evrende değerlendirilir; PIT kurulmadan Kapı A'ya kapalı; diğer satırlar "
                    "'HAYATTA KALAN EVREN' etiketini taşır",
        "xsec_requires_pit": True,
        "thresholds": {"listed_min_days": 60, "volume_window_days": 30, "min_median_daily_quote_volume_usdt": 20_000_000},
    }


# ============================================================================ asla gevşetme: yön tablosu (§8.2; S1'in parçası)
MAX, MIN, FIXED = "max", "min", "fixed"
_T = "thresholds."


def _directions() -> dict:
    """kayıt adı (sürümsüz) → {KÖK-GÖRELİ yol: yön}. `thresholds` altındaki HER sayısal yaprak burada olmalıdır (test);
    eşik gibi davranan kapı-dışı alanlar da (bool/metin: `fixed`). Liste değerlerinde yön ÖĞE BAŞINA uygulanır (uzunluk
    sabit). `fixed` = iki yönde de sonucu oynatabilen tanım (ör. yeniden örnekleme sayısı p'nin çözünürlüğünü, plasebo
    K'si CI genişliğini değiştirir)."""
    folds = {f"{_T}folds.{c}.{k}": (MIN if k.startswith("min_") else FIXED)
             for c, ks in ((WF_H1, ("min_train_days", "validation_days", "test_days", "step_days")),
                           (WF_INTRADAY, ("train_days", "validation_days", "test_days", "step_days")),
                           (WF_GOLD, ("min_train_days", "validation_days", "test_days", "step_days"))) for k in ks}
    costs = {f"{_T}costs.{sc}.{k}": (FIXED if k == "fee_sides" else MIN)
             for sc in ("base", "ADVERSE", "STRESS") for k in ("taker_fee_pct", "fee_sides", "slippage_bps", "spread_ticks")}
    budget = {f"budget_declared.{k}": MIN for k in ("POOLED", "CLUSTER", "COIN_FIT", "COPY", "COVER", "OTHER", "TOTAL",
                                                     "PRIOR_MIN", "N_AT_SEAL_MIN", "UNIQUE_VARIANTS", "EXPLORE_SERIES")}
    budget.update({f"budget_declared.COIN_FIT_GRID.{f}": MIN for f in FAMILY_GROUPS["STANDARD"]})
    budget["budget_declared.OTO_COLUMN_PRE_S5"] = FIXED
    return {
        "LIB": {
            f"{_T}parity_kernel.min_synthetic_trades_per_family": MIN, f"{_T}parity_kernel.min_signal_identity_share": MIN,
            f"{_T}parity_kernel.max_median_abs_dr": MAX, f"{_T}parity_kernel.max_p95_abs_dr": MAX,
            f"{_T}parity_copy.min_policy_entries_per_epoch": MIN, f"{_T}parity_copy.min_same_bar_side_share": MIN,
            f"{_T}parity_copy.max_median_abs_dr": MAX,
            f"{_T}sizing.min_liq_distance_stop_mult": MIN,
            f"{_T}eligibility.min_history_days_1h_plus": MIN, f"{_T}eligibility.min_history_days_5m": MIN,
            f"{_T}eligibility.max_gap_share": MAX, f"{_T}eligibility.min_archive_verified_share": MIN,
            f"{_T}eligibility.min_pre_seal_trades": MIN,
            f"{_T}shrinkage.k_trades": MIN, f"{_T}shrinkage.pool_tie_margin_r": MIN,
            f"{_T}cluster_coherence.min_ex_coin_oos_adverse_mean_r": MIN,
            f"{_T}single_instrument.max_oos_profit_share": MAX,
            f"{_T}promotion_side.max_single_coin_books_same_coin": MAX, f"{_T}promotion_side.max_single_coin_books_total": MAX,
            f"{_T}derived_specs.max_specs_per_month": MAX,
            f"{_T}transforms.stop_floor_k_atr": FIXED,
            "specialisation_rules.4_profit_share_def.denominator": FIXED,
            **budget,
        },
        "WF_PROTOCOL": {
            **folds, **costs,
            f"{_T}warmup_bars": FIXED, f"{_T}purge_signal_bars": MIN, f"{_T}positive_fold_min_trades": FIXED,
            f"{_T}candidates_top_k": FIXED,
            "step_equals_test": FIXED, "data_start_earliest": FIXED,
            "purge_embargo.make_folds_split.sum_bars": FIXED,
        },
        "STATS": {
            f"{_T}p_day.resamples": FIXED, f"{_T}bh_q": MAX, f"{_T}effective_n_abs_rho": FIXED,
            f"{_T}pbo.blocks": FIXED, f"{_T}pbo.min_configs": MIN, f"{_T}pbo.min_oos_days_per_block": MIN,
            f"{_T}placebo.k_per_trade": FIXED, f"{_T}placebo.ci_level": MIN, f"{_T}placebo.min_ci_lower": MIN,
            f"{_T}null_world.worlds": FIXED,
            "bh_fdr.family_tr": FIXED, "bh_fdr.no_p_tr": FIXED, "dsr.sr_var_floor": FIXED, "null_world.pass_rate_max": FIXED,
        },
        "LESSON_REGISTRY": {
            f"{_T}eligibility.min_n": MIN, f"{_T}eligibility.min_distinct_days": MIN, f"{_T}looks.n_thresholds": FIXED,
            f"{_T}looks.alphas": MAX, f"{_T}looks.alpha_total": MAX, f"{_T}bh_q": MAX, f"{_T}nightly_pair_cap": FIXED,
            f"{_T}hierarchical_rate_alpha": FIXED, f"{_T}ci.bootstrap_resamples": FIXED, f"{_T}ci.level": MIN,
            f"{_T}forward_validation.min_monthly_folds": MIN, f"{_T}forward_validation.min_sign_consistent_share": MIN,
            f"{_T}discrimination.min_auc_ci_lower": MIN,
        },
        "PIT_RULES": {f"{_T}listed_min_days": FIXED, f"{_T}volume_window_days": FIXED,
                      f"{_T}min_median_daily_quote_volume_usdt": FIXED, "xsec_requires_pit": FIXED,
                      "membership.volume_window_includes_d.includes_d": FIXED},
    }


LIB_V1 = freeze(_lib())
WF_PROTOCOL_V1 = freeze(_wf())
STATS_V1 = freeze(_stats())
LESSON_REGISTRY_V1 = freeze(_lessons())
PIT_RULES_V1 = freeze(_pit())
PRIOR_TRIALS_V1 = freeze(_load_json(PRIOR_TRIALS_FILE))
CONFIG_EPOCHS_V1 = freeze(_load_json(CONFIG_EPOCHS_FILE))
THRESHOLD_DIRECTIONS_V1 = freeze(_directions())
#: S1 yön tablosu (değişmez; S2 ve sonraki sürümler yönlerini AYRI tabloyla ekler)
THRESHOLD_DIRECTIONS = THRESHOLD_DIRECTIONS_V1

LIB_V1_SHA = sha256_canonical(LIB_V1)
WF_PROTOCOL_V1_SHA = sha256_canonical(WF_PROTOCOL_V1)
STATS_V1_SHA = sha256_canonical(STATS_V1)
LESSON_REGISTRY_V1_SHA = sha256_canonical(LESSON_REGISTRY_V1)
PIT_RULES_V1_SHA = sha256_canonical(PIT_RULES_V1)
PRIOR_TRIALS_V1_SHA = sha256_canonical(PRIOR_TRIALS_V1)
CONFIG_EPOCHS_V1_SHA = sha256_canonical(CONFIG_EPOCHS_V1)
THRESHOLD_DIRECTIONS_V1_SHA = sha256_canonical(THRESHOLD_DIRECTIONS_V1)

#: S1 mühür kümesi (ad → kayıt). Sıra = belge §8.2 sırası.
REGISTRIES: Mapping[str, Any] = MappingProxyType({
    "LIB_V1": LIB_V1, "WF_PROTOCOL_V1": WF_PROTOCOL_V1, "STATS_V1": STATS_V1, "LESSON_REGISTRY_V1": LESSON_REGISTRY_V1,
    "PIT_RULES_V1": PIT_RULES_V1, "PRIOR_TRIALS_V1": PRIOR_TRIALS_V1, "CONFIG_EPOCHS_V1": CONFIG_EPOCHS_V1,
    "THRESHOLD_DIRECTIONS_V1": THRESHOLD_DIRECTIONS_V1})
SHAS: Mapping[str, str] = MappingProxyType({name: sha256_canonical(reg) for name, reg in REGISTRIES.items()})
S1_SEAL = freeze({"seal": SEAL_ID, "doc": SEAL_DOC, "shas": dict(SHAS), "t_seal_ms": T_SEAL_MS})
#: `trials.jsonl` satırlarının ve (S2'de) manifestin `registry_sha`'sı: S1 mühür kümesinin sha'sı
REGISTRY_SHA = sha256_canonical(S1_SEAL)


# ============================================================================ kimlik (spec_sha, §2.1)
SPEC_KEYS = ("family", "variant_id", "params", "timeframes", "exits", "sizing", "module_src_sha")


def is_sha256(x: Any) -> bool:
    return isinstance(x, str) and len(x) == 64 and all(c in "0123456789abcdef" for c in x)


def spec_sha(spec: Mapping[str, Any]) -> str:
    """§2.1: sha256(kanonik JSON {family, variant_id, params, timeframes, exits, sizing, module_src_sha}). Anahtar kümesi
    TAM olmalı (eksik/fazla anahtar = ValueError); `module_src_sha` 64 hex."""
    if set(spec) != set(SPEC_KEYS):
        raise ValueError(f"spec anahtarları {sorted(SPEC_KEYS)} olmalı: {sorted(spec)}")
    if not is_sha256(spec.get("module_src_sha")):
        raise ValueError("module_src_sha 64 karakterlik küçük harf hex olmalı")
    return sha256_canonical({k: spec[k] for k in SPEC_KEYS})


def variants_by_id(lib: Mapping = LIB_V1) -> dict[str, Mapping]:
    return {v["variant_id"]: v for v in lib["variants"]}


def lib_spec(variant_id: str, module_src_sha: str, lib: Mapping = LIB_V1) -> dict:
    """Bir LIB varyantının spec'i. `alias_of`'lu ızgara üyesi (DON_20_10_4h_LO ≡ K_D4, BOX_n10_mid_ms0.5 ≡ K_BOX)
    tabanının spec'ini taşır → aynı `spec_sha` (tekilleşir, §2.2)."""
    vs = variants_by_id(lib)
    v = vs[variant_id]
    if v.get("alias_of"):
        v = vs[v["alias_of"]]
    return {"family": v["family"], "variant_id": v["variant_id"], "params": thaw(v["params"]),
            "timeframes": thaw(v["timeframes"]), "exits": thaw(v["exits"]), "sizing": thaw(lib["sizing"]),
            "module_src_sha": module_src_sha}


# ============================================================================ bütçe (§2.5) — koddan yeniden üretim
def _group_of(family: str, lib: Mapping) -> str:
    for g, fams in lib["family_groups"].items():
        if family in fams:
            return g
    raise KeyError(family)


def _scope_symbols(scope: Mapping, lib: Mapping) -> list[str]:
    s = scope["symbols"]
    if s == "P23":
        return list(lib["p23"]["symbols"])
    raise ValueError(f"kapsam başına sembol çözümü yalnız P23 için: {s}")


def gate_a_rows(lib: Mapping = LIB_V1) -> list[dict]:
    """Bütün Kapı A satırları (kapsam tablosundan; sonuçlara bakılmadan). Her satır: `row_id`, `row_type`, `unit`
    (varyant ya da COIN_FIT ailesi), `scope`, `raw_trials` (COIN_FIT'te aile ızgarası; diğerlerinde 1)."""
    unique = [v for v in lib["variants"] if not v.get("alias_of")]
    rows: list[dict] = []

    def add(row_type: str, unit: str, scope: str, raw: int) -> None:
        rows.append({"row_id": f"{unit}@{scope}", "row_type": row_type, "unit": unit, "scope": scope, "raw_trials": raw})

    for sname, sc in lib["scopes"].items():
        fams = set(sc["applies_to"])
        if sc["unit"] == "FAMILY_GRID":
            for fam in lib["family_groups"]["STANDARD"]:
                if fam not in fams:
                    continue
                grid = sum(1 for v in lib["variants"] if v["family"] == fam)      # ızgara = aliaslar DAHİL bütün üyeler
                for sym in _scope_symbols(sc, lib):
                    add("COIN_FIT", fam, f"COIN_FIT:{sym}", grid)
            continue
        for v in unique:
            if v["family"] not in fams:
                continue
            g = _group_of(v["family"], lib)
            if g == "STANDARD":
                row_type = "POOLED" if sname == "POOLED_P23" else "CLUSTER"
            else:
                row_type = {"COPY": "COPY", "COVER": "COVER", "OTHER": "OTHER"}[g]
            if sc["instances"] == "PER_SYMBOL":
                for sym in _scope_symbols(sc, lib):
                    add(row_type, v["variant_id"], f"{sname}:{sym}", 1)
            else:
                add(row_type, v["variant_id"], sname, 1)
    return rows


BUDGET_ORDER = ("POOLED", "CLUSTER", "COIN_FIT", "COPY", "COVER", "OTHER")


def budget_table(lib: Mapping = LIB_V1) -> dict[str, tuple[int, int]]:
    """§2.5 tablosu: satır türü → (Kapı A satırı, ham deneme); "TOTAL" dahil."""
    rows = gate_a_rows(lib)
    out = {k: (sum(1 for r in rows if r["row_type"] == k), sum(r["raw_trials"] for r in rows if r["row_type"] == k))
           for k in BUDGET_ORDER}
    out["TOTAL"] = (len(rows), sum(r["raw_trials"] for r in rows))
    return out


def coin_fit_grid(lib: Mapping = LIB_V1) -> dict[str, int]:
    return {fam: sum(1 for v in lib["variants"] if v["family"] == fam) for fam in lib["family_groups"]["STANDARD"]}


def unique_variant_count(lib: Mapping = LIB_V1) -> int:
    return sum(1 for v in lib["variants"] if not v.get("alias_of"))


def family_counts(lib: Mapping = LIB_V1) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in lib["variants"]:
        if not v.get("alias_of"):
            out[v["family"]] = out.get(v["family"], 0) + 1
    return out


def oto_column_pre_s5_count(lib: Mapping = LIB_V1) -> int:
    """§2.2 "Oto" sütununun (S5 ÖNCESİ; §7.5 taslağı) toplamı: 64. Yalnız tarihsel kayıt: S5 bu sütunu geçersiz kılar,
    yönlendirme S2'nin `AUTO_CATALOG_V1`'iyle yapılır."""
    return sum(1 for v in lib["variants"] if not v.get("alias_of") and v["oto_column_pre_s5"])


def explore_series(lib: Mapping = LIB_V1) -> int:
    """§5.1: 45 bağımsız kripto varyantı × 40 + 13 kopya × 40 + 35 örtü × 40 + XSEC 5 + spread 2 + altın 6 × 3."""
    fc = family_counts(lib)
    n_u = lib["explore"]["entry_universe_size"]
    std = sum(fc.get(f, 0) for f in lib["family_groups"]["STANDARD"])
    return (std + fc["K"] + fc["O"]) * n_u + fc["J"] + fc["I"] + fc["G"] * len(lib["explore"]["gold_instruments"])


def prior_total(prior: Mapping = PRIOR_TRIALS_V1) -> int:
    """§2.6: içe alınan öncül denemelerin (belgelenmiş alt sınır) toplamı."""
    return sum(int(r["count"]) for r in prior["rows"])


def n_at_seal(lib: Mapping = LIB_V1, prior: Mapping = PRIOR_TRIALS_V1) -> int:
    return budget_table(lib)["TOTAL"][1] + prior_total(prior)


# ============================================================================ UNSPECIFIED boşlukları
def _walk(x: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    yield path, x
    if isinstance(x, Mapping):
        for k in sorted(x):
            yield from _walk(x[k], f"{path}.{k}" if path else k)
    elif isinstance(x, (list, tuple)):
        for i, v in enumerate(x):
            tag = v["variant_id"] if isinstance(v, Mapping) and isinstance(v.get("variant_id"), str) else i
            yield from _walk(v, f"{path}[{tag}]")


def is_gap(x: Any) -> bool:
    return isinstance(x, Mapping) and x.get("status") == UNSPECIFIED and set(x) == {"status", "gap"}


def unspecified_gaps(registries: Mapping[str, Any] = REGISTRIES) -> list[tuple[str, str]]:
    """Bütün `UNSPECIFIED` işaretleri: (AD.yol, boşluk metni), yol sırasıyla."""
    out = []
    for name, reg in registries.items():
        for p, v in _walk(reg):
            if is_gap(v):
                out.append((f"{name}.{p}", v["gap"]))
    return out


def variant_gaps(variant: Mapping, lib: Mapping = LIB_V1) -> list[str]:
    """Bir varyantın UNSPECIFIED boşlukları, LIB-köküne göre yol olarak: kendi alanları (`variants[<id>].…`) + `requires`
    bağımlılıklarındaki (`defaults.er20_timeframe`, `covers.factors.<F>.…`, `copy_books.<taban>.…`). Boş değilse varyant
    gerçek veride hiçbir katmanda çalıştırılmaz (`UNSPECIFIED_PARAM`), bütçede sayılır."""
    vid = variant["variant_id"]
    out = [f"variants[{vid}].{p}" for p, v in _walk(variant) if is_gap(v)]
    for dep in variant.get("requires") or ():
        for p, v in _walk(get_path(lib, dep)):
            if is_gap(v):
                out.append(f"{dep}.{p}" if p else dep)
    return sorted(set(out))


def blocked_variants(lib: Mapping = LIB_V1) -> dict[str, list[str]]:
    """varyant → boşlukları (yalnız boşluğu olanlar; alias'lar dahil)."""
    out = {}
    for v in lib["variants"]:
        g = variant_gaps(v, lib)
        if g:
            out[v["variant_id"]] = g
    return out


def blocked_budget(lib: Mapping = LIB_V1) -> dict[str, int]:
    """Boşluk yüzünden gerçek veride çalıştırılmayan ama bütçede sayılan pay (koddan): `variants` (alias hariç),
    `gate_a_rows` (birimi engelli varyant olan satır; `COIN_FIT`'te ızgaranın BÜTÜN seçenekleri engelliyse) ve
    `raw_trials` (engelli birimin `SPEC`'i + engelli ızgara seçeneklerinin `OPTION`'ları; `trials.seal_entries` sayımı)."""
    blocked = blocked_variants(lib)
    vs = variants_by_id(lib)
    rows = raw = 0
    for r in gate_a_rows(lib):
        if r["row_type"] == "COIN_FIT":
            opts = [v["variant_id"] for v in lib["variants"] if v["family"] == r["unit"]]
            k = sum(1 for o in opts if o in blocked)
            raw += k
            rows += int(k == len(opts))
        elif r["unit"] in blocked:
            rows += 1
            raw += r["raw_trials"]
    return {"variants": sum(1 for v in blocked if not vs[v].get("alias_of")), "gate_a_rows": rows, "raw_trials": raw}


def protocol_gaps(registries: Mapping[str, Any] = REGISTRIES) -> list[tuple[str, str]]:
    """Varyanta bağlanmayan boşluklar (WF/istatistik/ders/PIT protokolü, §2.4 kuralları): dolmadan hiçbir gerçek veri WF,
    Kapı A ya da ders bakışı koşusu yapılmaz (ilgili satır türü için)."""
    lib = registries["LIB_V1"]
    deps = {d for v in lib["variants"] for d in (v.get("requires") or ())}
    out = []
    for path, text in unspecified_gaps(registries):
        rel = path[len("LIB_V1."):] if path.startswith("LIB_V1.") else None
        if rel is not None and (rel.startswith("variants[") or any(rel == d or rel.startswith(d + ".") for d in deps)):
            continue
        out.append((path, text))
    return out


# ============================================================================ asla gevşetme (§8.2)
#: kayıt adı → sürümler (v1 önce). Yeni bir sürüm (`STATS_V2` …) buraya EKLENİR; test her sürümü v1'e karşı sınar.
REGISTRY_VERSIONS: Mapping[str, tuple] = MappingProxyType({
    "LIB": (LIB_V1,), "WF_PROTOCOL": (WF_PROTOCOL_V1,), "STATS": (STATS_V1,), "LESSON_REGISTRY": (LESSON_REGISTRY_V1,),
    "PIT_RULES": (PIT_RULES_V1,)})


def get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    return cur


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def threshold_leaves(reg: Mapping) -> dict[str, Any]:
    """`thresholds` altındaki bütün sayısal yapraklar, KÖK-GÖRELİ yolla (`thresholds.…`; sayı listesi tek yaprak)."""
    out: dict[str, Any] = {}

    def rec(x: Any, p: str) -> None:
        if isinstance(x, Mapping):
            for k in sorted(x):
                rec(x[k], f"{p}.{k}" if p else k)
        elif _num(x) or (isinstance(x, (list, tuple)) and x and all(_num(i) for i in x)):
            out[p] = x
    rec(reg.get("thresholds", {}), "thresholds")
    return out


def _looser(direction: str, old: Any, new: Any) -> bool:
    if direction == FIXED:
        return thaw(old) != thaw(new)
    if isinstance(old, (list, tuple)) or isinstance(new, (list, tuple)):
        if not (isinstance(old, (list, tuple)) and isinstance(new, (list, tuple))) or len(old) != len(new):
            return True
        return any(_looser(direction, a, b) for a, b in zip(old, new))
    if not (_num(old) and _num(new)):
        return True
    return new > old if direction == MAX else new < old


def looser_than(v1: Mapping, vn: Mapping, directions: Mapping[str, str]) -> list[str]:
    """`vn`'in yön tablosundaki (kök-göreli) değerlerinden v1'e göre GEVŞEK olanlar (eksik eşik de gevşektir)."""
    bad = []
    for path, d in sorted(directions.items()):
        old = get_path(v1, path)
        try:
            new = get_path(vn, path)
        except KeyError:
            bad.append(f"{path}: eşik kaldırıldı")
            continue
        if _looser(d, old, new):
            bad.append(f"{path}: {d} {thaw(old)} → {thaw(new)}")
    return bad


__all__ = ["CLUSTERS", "CONFIG_EPOCHS_FILE", "CONFIG_EPOCHS_V1", "CONFIG_EPOCHS_V1_SHA", "FIXED", "LESSON_REGISTRY_V1",
           "LESSON_REGISTRY_V1_SHA", "LIB_V1", "LIB_V1_SHA", "LIB_VERSION", "MAX", "MIN", "P23", "PIT_RULES_V1",
           "PIT_RULES_V1_SHA", "PRIOR_TRIALS_FILE", "PRIOR_TRIALS_V1", "PRIOR_TRIALS_V1_SHA", "REGISTRIES", "REGISTRY_SHA",
           "REGISTRY_VERSIONS", "S1_SEAL", "SHAS", "SPEC_KEYS", "STATS_V1", "STATS_V1_SHA", "THRESHOLD_DIRECTIONS",
           "THRESHOLD_DIRECTIONS_V1", "THRESHOLD_DIRECTIONS_V1_SHA", "T_SEAL_MS", "UNSPECIFIED", "WF_PROTOCOL_V1",
           "WF_PROTOCOL_V1_SHA", "blocked_budget", "blocked_variants", "budget_table", "canonical_bytes", "coin_fit_grid", "explore_series",
           "decided", "family_counts", "freeze", "gap", "gate_a_rows", "get_path", "is_gap", "is_sha256", "lib_spec", "looser_than",
           "n_at_seal", "oto_column_pre_s5_count", "pretty_json", "prior_total", "protocol_gaps", "sha256_canonical",
           "spec_sha", "thaw", "threshold_leaves", "unique_variant_count", "unspecified_gaps", "variant_gaps",
           "variants_by_id"]
