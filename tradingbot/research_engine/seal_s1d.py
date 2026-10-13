"""Mühür S1d (sahip kararı 2026-10-13): `LIB_AG_V1` — ANTİGRAVİTY klasöründeki takdir tüccarı dökümlerinden (ICT tarzı:
öldürme bölgeleri, öğle süpürmesi, 10:00 "TENM" açılış modeli, MSS/FVG, ret blokları, MSNR) türetilen 5 taktik + 3 örtünün
MEKANİK varyantları; S1 (`registry.py`), S1b (`seal_s1b.py`) ve S1c (`seal_s1c.py`) DOKUNULMADAN, AYRI ADLI, AYRI DOSYALI
mühür (docs/ENGINE_BRAIN_V1.md §8.6 S1d, §8.2 tablosu).

* `LIB_AG_V1` — 38 varyant (P1 LUNCH_SWEEP 4, P2 TENM 4, P3 MSS_FVG 8, P4 RB_LIMIT 2, P5 MSNR 2, AG_O örtüleri 18 = {KZ,
  NOREACT, CAL} × {T2, M2, BOX, D4, C4, FM}); her varyantın TAM parametreleri (`params` → `spec_sha`), dilimler, pencereler,
  çıkışlar, WF sınıfı, kapsam (`AG_POOLED`: 41 sembol birleşik; sembol başına sonuç yalnız rapor), seçim grupları, deneme
  bütçesi ve takdir okumaları (`readings_tr`; dökümlerin ima ettiği ile birlikte). Kapılar LIB_V1'inkilerdir (walk-forward,
  maliyet senaryoları, BH/DSR, CSCV, plasebo); hiçbir eşik gevşetilmez, yeni eşik yoktur.
* `AG_UNIVERSE_V1` — S1b'nin dondurulmuş 40 coin'i (`ENTRY_UNIVERSE_V1`, DEĞİŞMEDİ) + XAUUSDT = 41.
* `AG_CALENDAR_V1` — 2023-01-01 … 2026-12-31 NYSE tam tatilleri ve FOMC açıklama günleri (New York tarihi); kapsam dışı
  (öncesi/sonrası) CAL varyantları için takvim yok → giriş yok, `CAL_UNCOVERED` (tahmin edilmez).
* `S1D_DIRECTIONS_V1` — asla gevşetme: üç kaydın bütün tanım alt ağaçları `fixed` (S1'in `registry.looser_than`'ıyla).

Kanonik bayt `registry.canonical_bytes`; kayıtlar derin donuk. `S1D_SHA` = {dört sha, `S1D_SEAL_MS`, genişlettiği S1
`REGISTRY_SHA`, S1b `S1B_SHA`, S1c `S1C_SHA`} kanonik sha'sı. `S1D_SEAL_MS` = bu mühür commit'inin işleyen zamanından birkaç
dakika sonraki tam dakika (T_SEAL_MS kuralı, §8.2; test git'ten doğrular). `PINNED` tutmazsa (`check()`) `lib_ag` hiçbir
AG varyantını kurmaz (kapalı hata). Ağsız; yalnız `registry` ve S1b/S1c sabitleri.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from . import registry as RG
from . import seal_s1b as S1B
from . import seal_s1c as S1C

S1D_ID = "S1d"
S1D_DOC = "docs/ENGINE_BRAIN_V1.md §8.6 (S1d — LIB_AG_V1, 2026-10-13)"
#: bu mühür commit'inin UTC ms'si (commit'ten birkaç dakika sonraki tam dakika; elle geriye alınmaz)
S1D_SEAL_MS = 1791926460000
FIXED = RG.FIXED
LIB_AG_VERSION = "LIB_AG_v1"
OWNER_DECISION_TR = ("sahip kararı 2026-10-13 (koordinatör iletisi): dökümlerdeki 5 taktik + 3 örtü MEKANİK varyantlara "
                     "çevrilir, ayrı adlı LIB_AG_V1 mührüyle (S1 dokunulmadı), LIB_V1 ile AYNI kapılardan geçer (walk-forward, "
                     "maliyet senaryoları, BH/DSR, CSCV, plasebo), deneme defterinde ve çoklu sınamada sayılır; bu mührün "
                     "evrenine XAUUSDT eklenir (S1b'nin 40'ı değişmez)")

# ============================================================================ ortak kurallar (her varyantın params.common'ı)
SESSIONS_MIN = {"ASIA": [1200, 1440], "LONDON": [120, 300], "NYAM": [570, 660], "LUNCH": [720, 780], "PM": [810, 960]}
KZ = ["LONDON", "NYAM", "LUNCH", "PM"]
COMMON = {
    "clock": "America/New_York",
    "sessions_min": SESSIONS_MIN,
    "kz": KZ,
    "swing": "FRACTAL_3_RIGHT_NEIGHBOUR_CLOSED",
    "atr_len": 14, "atr_window": 100, "atr_at": "PREVIOUS_BAR",
    "buffer_frac": 0.0005,
    "stop_pct_min": 0.0008, "stop_pct_max": 0.012,
    "per_ny_day": 1,
    "limit_fill": "ONE_BAR_THROUGH_1_TICK_AT_LEVEL_TAKER",
}
#: dökümde zaman çıkışı yazılmayan ailelerin (P2–P5) zaman stopu (sinyal barı; takdir okuması R9)
NO_STATED_TIME_EXIT_BARS = 48
WF_INTRADAY, WF_H1 = RG.WF_INTRADAY, RG.WF_H1


def _row(vid: str, family: str, params: dict, timeframes: list[str], *, rule_tr: str, stop: str, exit_rule: str,
         target: str, max_hold: Any, entry: str, wf_class: str, inputs: list[str], cover: dict | None = None,
         structurally_empty: bool = False) -> dict:
    return {"variant_id": vid, "family": family, "params": params, "timeframes": timeframes, "signal_tf": timeframes[0]
            if family != "AG_O" else timeframes[-1], "sides": "LS" if family != "AG_O" else "BASE", "rule_tr": rule_tr,
            "exits": {"stop": stop, "exit_rule": exit_rule, "target": target, "max_hold_bars": max_hold},
            "entry": entry, "wf_class": wf_class, "inputs": inputs, "cover": cover, "structurally_empty": structurally_empty}


def _p1() -> list[dict]:
    out = []
    for b in ("none", "htf"):
        for a in ("none", "quiet"):
            out.append(_row(
                f"AG_LS_{b.upper()}_{a.upper()}", "AG_P1",
                {"common": COMMON, "window": {"5m": 120, "15m": 2112, "4h": 300}, "bias": b, "accum": a,
                 "ref_window_min": [570, 720], "lunch_start_min": 720, "trigger_open_min": [720, 810], "time_exit_min": 960,
                 "min_tp_r": 1.0, "htf": {"tf": "4h", "ema": 50}, "quiet": {"tf": "15m", "days": 20, "stat": "MEDIAN",
                                                                            "cmp": "LE"}},
                ["5m", "15m", "4h"],
                rule_tr=f"öğle süpürmesi: NYAM (09:30–12:00) ya da koşan öğle tepesi/dibi süpürülüp geri kapanınca karşı yöne; "
                        f"bias={b}, accum={a}",
                stop="SWEEP_BAR_EXTREME_PLUS_BUFFER", exit_rule="TIME_EXIT_1600_NY",
                target="RANGE_OPPOSITE_EXTREME_SKIP_IF_LT_1R", max_hold=NO_STATED_TIME_EXIT_BARS, entry="MARKET_NEXT_OPEN",
                wf_class=WF_INTRADAY, inputs=["ohlcv_5m", "ohlcv_15m", "ohlcv_4h"]))
    return out


def _p2() -> list[dict]:
    out = []
    for mode in ("A", "B"):
        for wl in (True, False):
            out.append(_row(
                f"AG_TENM_{mode}_{'WL' if wl else 'ANY'}", "AG_P2",
                {"common": COMMON, "window": {"5m": 160}, "mode": mode, "wickless_required": wl, "line_bar_min": 600,
                 "window_min": [600, 720], "mode_a_after_min": 605, "wickless_max_frac": 0.10, "dead_after_crosses": 3,
                 "mode_a_min_tp_r": 1.5, "mode_b_disp_atr": 1.5, "mode_b_target_r": 3.0},
                ["5m"],
                rule_tr=("10:00 çizgisi L; kip A: FVG'ye dokunup L'ye doğru kapanış → L'ye" if mode == "A" else
                         "10:00 çizgisi L; kip B: 1,5 ATR gövdeli kırılım → L'de ilk geri dönüşte limit, 3R") +
                        (", fitilsiz 10:00 barı şart" if wl else ""),
                stop="MANIPULATION_EXTREME_PLUS_BUFFER" if mode == "A" else "DISPLACEMENT_OPPOSITE_EXTREME_PLUS_BUFFER",
                exit_rule="NO_STATED_TIME_EXIT_MAX_HOLD", target="LINE_SKIP_IF_LT_1.5R" if mode == "A" else "3R",
                max_hold=NO_STATED_TIME_EXIT_BARS, entry="MARKET_NEXT_OPEN" if mode == "A" else "LIMIT_ONE_BAR_REEMIT",
                wf_class=WF_INTRADAY, inputs=["ohlcv_5m"]))
    return out


def _p3() -> list[dict]:
    out = []
    for tf in ("5m", "15m"):
        for r in (2, 3):
            for s in ("KZ", "ALL"):
                out.append(_row(
                    f"AG_MSS_{tf}_{r}R_{s}", "AG_P3",
                    {"common": COMMON, "window": {tf: 120}, "tf": tf, "target_r": float(r), "session": s, "disp_atr": 1.5,
                     "valid_bars": 12, "swing_lookback": 100},
                    [tf], rule_tr=f"{tf} MSS (1,5 ATR gövdeli yer değiştirme son karşı salınımın ötesine kapanır) + FVG "
                                  f"kenarında 12 bar limit, {r}R, oturum {s}",
                    stop="PRE_DISPLACEMENT_SWING_PLUS_BUFFER", exit_rule="NO_STATED_TIME_EXIT_MAX_HOLD", target=f"{r}R",
                    max_hold=NO_STATED_TIME_EXIT_BARS, entry="LIMIT_ONE_BAR_REEMIT", wf_class=WF_INTRADAY,
                    inputs=[f"ohlcv_{tf}"]))
    return out


def _p4() -> list[dict]:
    return [_row(f"AG_RB_{e.upper()}", "AG_P4",
                 {"common": COMMON, "window": {"5m": 120}, "entry": e, "session": "KZ", "prior_swing_bars": 48,
                  "valid_bars": 24, "min_tp_r": 2.0},
                 ["5m"], rule_tr=f"ret bloğu (önceki salınımı fitille süpürüp içeri kapanan doğrulanmış salınım barı), gövde "
                                 f"kenarı çizgisi, giriş {e}, yalnız KZ",
                 stop="WICK_EXTREME_PLUS_BUFFER", exit_rule="NO_STATED_TIME_EXIT_MAX_HOLD",
                 target="NEAREST_UNSWEPT_OPPOSING_SWING_IF_GE_2R_ELSE_2R", max_hold=NO_STATED_TIME_EXIT_BARS,
                 entry="LIMIT_ONE_BAR_REEMIT" if e == "direct" else "MARKET_NEXT_OPEN", wf_class=WF_INTRADAY,
                 inputs=["ohlcv_5m"]) for e in ("direct", "confirm")]


def _p5() -> list[dict]:
    return [_row(f"AG_MSNR_{'2R' if t == '2R' else 'LEVEL'}", "AG_P5",
                 {"common": COMMON, "window": {"5m": 120, "1h": 168}, "target": t, "level_tol_frac": 0.0005,
                  "level_lookback_1h": 168, "min_bars": 2, "mss_wait_bars": 12, "next_level_min_r": 1.5, "target_r": 2.0},
                 ["5m", "1h"], rule_tr=f"1h gövde seviyesi (%0,05 içinde ≥ 2 gövde ucu), gövde geçişiyle taze, ilk dokunuşta "
                                       f"≤ 12 bar içinde 5m MSS → giriş; hedef {t}",
                 stop="TOUCH_EXTREME_PLUS_BUFFER", exit_rule="NO_STATED_TIME_EXIT_MAX_HOLD",
                 target="2R" if t == "2R" else "NEXT_LEVEL_SKIP_IF_LT_1.5R", max_hold=NO_STATED_TIME_EXIT_BARS,
                 entry="MARKET_NEXT_OPEN", wf_class=WF_INTRADAY, inputs=["ohlcv_5m", "ohlcv_1h"])
            for t in ("2R", "NEXT_LEVEL")]


# ============================================================================ takvim (AG_CALENDAR_V1)
NYSE_FULL_HOLIDAYS = {
    "2023": ["2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29", "2023-06-19", "2023-07-04", "2023-09-04",
             "2023-11-23", "2023-12-25"],
    "2024": ["2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02",
             "2024-11-28", "2024-12-25"],
    "2025": ["2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
             "2025-09-01", "2025-11-27", "2025-12-25"],
    "2026": ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
             "2026-11-26", "2026-12-25"],
}
FOMC_STATEMENT_DAYS = {
    "2023": ["2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13"],
    "2024": ["2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18"],
    "2025": ["2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10"],
    "2026": ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"],
}
CAL_FROM, CAL_UNTIL = "2023-01-01", "2026-12-31"
CAL_UNCERTAIN = ["2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]


def _blocked() -> list[str]:
    return sorted({d for ys in (NYSE_FULL_HOLIDAYS, FOMC_STATEMENT_DAYS) for v in ys.values() for d in v})


AG_CALENDAR_V1: Mapping[str, Any] = RG.freeze({
    "id": "AG_CALENDAR_V1",
    "tz": "America/New_York", "date_of": "ENTRY_TIME_NY_DATE",
    "covered_from": CAL_FROM, "covered_until": CAL_UNTIL,
    "nyse_full_holidays": NYSE_FULL_HOLIDAYS,
    "fomc_statement_days": FOMC_STATEMENT_DAYS,
    "blocked_dates": _blocked(),
    "uncovered": {"rule": "NO_ENTRY", "flag": "CAL_UNCOVERED",
                  "reading_tr": "kapsam dışındaki (2023-01-01 öncesi, 2026-12-31 sonrası) New York tarihlerinde CAL "
                                "varyantının takvimi yoktur: giriş yok, satır/koşu CAL_UNCOVERED işaretlenir; tarih tahmin "
                                "edilmez, yeni takvim yeni mühür ister"},
    "source": {
        "nyse": "NYSE tatil kuralları (yeni yıl/bağımsızlık/Noel hafta sonu kaydırması, Juneteenth 2022+, Kutsal Cuma) ve "
                "2025-01-09 Başkan Carter ulusal yas günü kapanışı; bilgiyle yazıldı, testte kural hesabıyla sınandı",
        "fomc": "federalreserve.gov/monetarypolicy/fomccalendars.htm bu ortamdan ERİŞİLEMEDİ (2026-10-13, DNS); tarihler "
                "bilgiyle yazıldı: iki günlük toplantının ikinci günü (açıklama günü); 2024-11-07 Perşembe (seçim haftası)",
        "verified_online": False,
    },
    "uncertain": CAL_UNCERTAIN,
    "uncertain_tr": "2026 ikinci yarı toplantıları yayımlanmış takvimden bilgiyle; erteleme ya da olağan dışı (planlanmamış) "
                    "açıklama olduysa listede yoktur. 2023–2026 için bilinen planlanmamış FOMC açıklaması yok",
    "excluded_tr": "yarım günler (erken kapanış) tam tatil değildir, listede yok; planlanmamış toplantılar listede yok",
})

# ============================================================================ evren (AG_UNIVERSE_V1)
_S1B_COINS = list(S1B.ENTRY_UNIVERSE_V1["coins"])
AG_UNIVERSE_V1: Mapping[str, Any] = RG.freeze({
    "id": "AG_UNIVERSE_V1",
    "coins": _S1B_COINS + ["XAU"],
    "symbols": [c + "USDT" for c in _S1B_COINS] + ["XAUUSDT"],
    "size": len(_S1B_COINS) + 1,
    "base": {"seal": "S1b", "registry": "ENTRY_UNIVERSE_V1", "size": len(_S1B_COINS), "unchanged": True},
    "extra": {"XAU": {"symbol": "XAUUSDT", "market": "Binance USDⓈ-M sürekli vadeli (TradFi altın; 7/24 bar)",
                      "first_archive_bar": "2025-12-11", "plan_start": "2025-12-01", "from_listing": True,
                      "listing_source": "gold_lab_v2 mekân verisi (arşiv pencere başından 10 gün sonra başlıyor: 1h 7.048 / "
                                        "4h 1.762 / 1d 294 bar 2026-09-30'a kadar, eksik yok); kesin an çalışma anında "
                                        "exchangeInfo onboardDate",
                      "data_tiers": ["1m", "5m", "15m", "1h", "4h", "1d", "funding", "markpx_1h"],
                      "added_tier": "markpx_1h (S1b fonlama mark'ı; önceden altın vadelilerde yoktu)"}},
    "order": "S1B_ORDER_THEN_XAU",
    "rule": "EVERY_AG_VARIANT_ON_ALL_41_POOLED_GATE_ROW_PER_SYMBOL_REPORT_ONLY",
    "history_before_listing": "ABSENT",
    "reading_tr": "LIB_AG_V1'in bütün varyantları 41 sembolde koşar; Kapı A satırı 41'in birleşimidir (AG_POOLED), sembol "
                  "başına sonuç yalnız rapordur (deneme değil, kapıya girmez). XAUUSDT'nin listelenmeden önceki geçmişi yoktur "
                  "(boş; doldurulmaz). S1b'nin 40 coin'lik ENTRY_UNIVERSE_V1'i ve sha'sı değişmedi",
})

# ============================================================================ örtüler (AG_O)
OVERLAY_BASES = ("T2", "M2", "BOX", "D4", "C4", "FM")
OVERLAYS = ("KZ", "NOREACT", "CAL")
_OVERLAY_PARAMS = {
    "KZ": {"sessions_min": SESSIONS_MIN, "kz": KZ, "clock": "America/New_York"},
    "NOREACT": {"bars": 3, "min_mfe_r": 0.3, "exit": "CLOSE_AT_BAR_CLOSE_NEXT_OPEN"},
    "CAL": {"calendar": "AG_CALENDAR_V1", "blocked_dates": _blocked(), "covered_from": CAL_FROM, "covered_until": CAL_UNTIL,
            "uncovered": "NO_ENTRY", "clock": "America/New_York"},
}
_OVERLAY_TR = {"KZ": "girişler yalnız öldürme bölgesinde", "NOREACT": "3 giriş barında MFE < 0,3R → 3. barın kapanışında çık",
               "CAL": "NYSE tam tatili ve FOMC açıklama günü (New York tarihi) giriş yok"}


def _base_variants(base: str) -> list[str]:
    return [f"K_C4_CV{i:03d}" for i in range(1, 9)] if base == "C4" else [f"K_{base}"]


def _overlays() -> list[dict]:
    out = []
    books = RG.LIB_V1["copy_books"]
    for base in OVERLAY_BASES:
        tfs = list(books[base]["timeframes"])
        for ov in OVERLAYS:
            out.append(_row(
                f"AGO_{base}_{ov}", "AG_O", {"base": base, "overlay": ov, "overlay_params": _OVERLAY_PARAMS[ov]}, tfs,
                rule_tr=f"{base} canlı kopyası + {_OVERLAY_TR[ov]}", stop="BASE",
                exit_rule="BASE_PLUS_NOREACT" if ov == "NOREACT" else "BASE", target="BASE", max_hold="BASE",
                entry="BASE", wf_class=WF_INTRADAY if base == "BOX" else WF_H1,
                inputs=["ohlcv"] + (["btc_1d"] if base in ("T2", "M2") else []),
                cover={"base": base, "overlay": ov, "base_variants": _base_variants(base),
                       "base_params_from": "S1 LIB_V1 (mühürlü K satırları; C4 = 8 varyasyonlu C4 defteri)"},
                structurally_empty=(ov == "KZ" and base in ("T2", "M2"))))
    return out


_VARIANTS = _p1() + _p2() + _p3() + _p4() + _p5() + _overlays()
FAMILY_COUNTS = {"AG_P1": 4, "AG_P2": 4, "AG_P3": 8, "AG_P4": 2, "AG_P5": 2, "AG_O": 18}
_N = len(_VARIANTS)
_N_SYMBOLS = len(_S1B_COINS) + 1


def _groups() -> dict[str, list[str]]:
    g: dict[str, list[str]] = {}
    for v in _VARIANTS:
        key = f"AG_FAM:{v['family']}@AG_POOLED" if v["family"] != "AG_O" else f"AG_COVER:{v['cover']['base']}@AG_POOLED"
        g.setdefault(key, []).append(f"{v['variant_id']}@AG_POOLED")
    return g


READINGS_TR = [
    "R1 saat: bütün saat kuralları New York yereli (barın açılış zamanından); yaz saati ABD 2007 kuralının saf hesabıyla "
    "(strategy_lib.ag_clock; zoneinfo paket saflık testince yasak — tz veritabanını dosyadan okur), zoneinfo "
    "America/New_York ile 2019–2030 eşitliği testli; oturumlar yarı açık [başlangıç, bitiş)",
    "R2 'kapanışı 12:00–13:30'da' (P1 tetiği) = açılışı [12:00, 13:30) olan barlar: 12:00'de kapanan bar NYAM aralığının son "
    "barıdır, tetik olamaz; koşan öğle uçları tetik barından ÖNCEKİ kapanmış öğle barlarıdır",
    "R3 P1 iki başvuruyu aynı barda iki yönde süpüren bar belirsizdir → işlem yok; hedef aralığı tetik barı dahil",
    "R4 P1 quiet aralıkları 15m barlarından (her gün 10 bar; bugün dahil aynı tanım); 20 önceki günden biri eksikse ölçülemez "
    "→ işlem yok; 'önceki 20 New York günü' takvim günüdür (7/24 piyasa)",
    "R5 ATR14 = Wilder, barın kendisinde biten SABİT 100 barlık pencerede; büyüklük karşılaştırmasında önceki barın ATR'si "
    "(yer değiştirme barı kendi ATR'sini şişirmez)",
    "R6 limit emri: çekirdekte limit kuralı yoktu → S1d tek-bar limit eki (kernel.limit_through): seviye ≥ 1 tik İÇİNDEN "
    "geçilirse seviyeden dolum, maliyet piyasa girişiyle aynı (kayma + spread + taker; istenen 'taker ücreti'nden daha "
    "ihtiyatlı); emir her bar yeniden verilir, seviyeye İLK dokunuş kurulumu tüketir (dokunup geçmeyen bar dolum sayılmaz)",
    "R7 dolum barında stop barın bütün aralığıyla sınanır; çekirdeğin giriş saati kuralı (girişi içeren 1h diliminde hedef "
    "kredisi yok) AG'ye de uygulanır — 5m kurulumlarında hedef en erken bir sonraki saatte alınabilir (ihtiyatlı, dökümde yok)",
    "R8 1R/1,5R hedef tabanları çekirdeğin mevcut R/R kapısıyla DOLUM fiyatından ölçülür (min_rr_after_cost, maliyet payı 0); "
    "%0,08–%1,2 stop mesafesi de dolum fiyatıyla",
    "R9 zaman çıkışı yalnız P1'de yazılı (16:00); P2–P5 için dökümde yok → 48 sinyal barı zaman stopu (5m'de 4 saat, 15m'de 12 "
    "saat; P1'in güvenlik ağı da 48); tek kural, sonuca bakılmadan",
    "R10 P2 'L'yi 3 kez kesme' = 10:00 barından itibaren 5m kapanışlarının L'ye göre yanının 3 kez değişmesi (eşit kapanış yan "
    "değiştirmez); 10:00 barının kapanışı ilk yanı verir",
    "R11 P2 kip A: FVG'nin üç barı da o New York gününde (00:00'dan sonra); İLK dokunuş açılışı ≥ 10:05 olan barda olmalı "
    "(daha önce dokunulmuşsa azaltılmış sayılır); 'L'ye doğru kapanış' = FVG'nin L tarafına dışarı İLK kapanış ve hâlâ L'nin "
    "berisinde; arada FVG'nin öbür ötesine kapanış kurulumu bozar; manipülasyon ucu = 10:00 barından karar barına kadarki uç",
    "R12 P2 kip B: kırılım = L'yi KESEN kapanış (önceki kapanış ≤ L < kapanış; 10:00 barında önceki = L) ve gövde ≥ 1,5 ATR; "
    "en son kırılım geçerlidir; emir 12:00'ye kadar",
    "R13 P3 MSS: kapanış, i − 1'de doğrulanmış en son karşı salınımın ötesine o salınımdan beri İLK kapanış; salınım araması "
    "yer değiştirmeden önceki 100 barla sınırlı; stop salınımı = yer değiştirme barında doğrulanmış en son karşı-yön salınım; "
    "yalnız en son kurulum geçerli; KZ'de emir yalnız bölgede yaşar ama bölge dışındaki dokunuş da kurulumu tüketir",
    "R14 P4 RB: önceki salınım [j − 48, j − 2] içinde ve j'den önce doğrulanmış; dış bar iki yönde RB ise belirsiz → yok; ilk "
    "dönüş j + 2'den sayılır (j + 1 doğrulama barı), 24 bar; hedef salınımı = karar anına kadar hiç aşılmamış (süpürülmemiş) en "
    "yakın karşı salınım (ICT 'likidite' okuması); yalnız en son RB geçerli",
    "R15 P5 seviye kümelemesi açgözlü (sıralı değerler, ilk üyenin %0,05 üstüne kadar); seviye fiyatı üyelerin ortalaması; "
    "tepeler ve dipler ayrı kümeler; taze = oluşmadan sonraki EN SON gövde geçişi; MSS dokunuş barı dahil 0–12 bar içinde; "
    "stop = dokunuştan MSS barına kadarki uç; NEXT_LEVEL hedefi bütün kümelerden (taze olsun olmasın)",
    "R16 örtüler LIB_V1 tabanlarının AYRI kopyalarıdır (K satırları ve LIB_V1 O örtüleri değişmez); C4 tabanı LIB_V1 "
    "örtülerindeki gibi C4 defteri (8 varyasyon, ilk eşleşen) — temsilî spec; FM LIB_V1'de örtüsüz canlı defterdir, burada "
    "örtülenir; T2/M2 × KZ yapısal olarak işlemsizdir (1d karar 00:00 UTC = New York 19:00/20:00) ama deneme sayılır",
    "R17 NOREACT: 'giriş dilimi' tabanın sinyal dilimi; MFE dolum fiyatından (kayma dahil); 3. barın kapanışında karar, çıkış "
    "sonraki açılışta (7/24 seride kapanışa eşdeğer)",
    "R18 CAL: giriş anının New York tarihi; takvim dışı tarih giriş yok (CAL_UNCOVERED)",
    "R19 günde bir işlem sınırı dolum anının New York günüdür; reddedilen giriş (R/R, stop yüzdesi, limit dolmadı) işlem "
    "değildir — aynı gün sonraki tetik geçerlidir",
]
CAVEATS_TR = [
    "konuşmacının sonuçları doğrulanmamış tekrar oynatma (replay) iddialarıdır; kanıt değildir",
    "konuşmacı kriptoyu bilmediğini söylüyor; anlattığı piyasalar NQ/ES, FX ve altındı — 7/24 kripto vadelilere aktarım bir "
    "varsayımdır (oturum saatleri ABD hisse piyasasınındır)",
    "takdir öğeleri (bağlam, 'temiz' yapı, haber okuma) mekanikleştirilirken kaybolur; mühürlü kural dökümün kendisi değil, "
    "sınanabilir bir okumasıdır",
    "38 yeni deneme çoklu sınama bedelini artırır (DSR N, BH ailesi); kazanan çıkmazsa bu beklenen sonuçtur",
]

LIB_AG_V1: Mapping[str, Any] = RG.freeze({
    "id": LIB_AG_VERSION,
    "doc": S1D_DOC,
    "decision": OWNER_DECISION_TR,
    "source_tr": "sahibin ANTİGRAVİTY klasöründeki takdir tüccarı dökümleri (öldürme bölgeleri, öğle süpürmesi, 10:00 TENM, "
                 "MSS/FVG, ret blokları, MSNR); sahip 5 taktik + 3 örtüyü onayladı (2026-10-13)",
    "common": COMMON,
    "sessions_tr": "ASIA 20:00–24:00, LONDON 02:00–05:00, NYAM 09:30–11:00, LUNCH 12:00–13:00, PM 13:30–16:00 (New York); "
                   "KZ = LONDON ∪ NYAM ∪ LUNCH ∪ PM",
    "families": {"AG_P1": {"name": "LUNCH_SWEEP", "tf": "5m", "variants": 4},
                 "AG_P2": {"name": "TENM", "tf": "5m (çekirdekte 1m yok)", "variants": 4},
                 "AG_P3": {"name": "MSS_FVG", "tf": "5m, 15m", "variants": 8},
                 "AG_P4": {"name": "RB_LIMIT", "tf": "5m", "variants": 2},
                 "AG_P5": {"name": "MSNR", "tf": "1h seviye + 5m giriş", "variants": 2},
                 "AG_O": {"name": "OVERLAYS (KZ, NOREACT, CAL)", "tf": "tabanınki", "variants": 18,
                          "bases": list(OVERLAY_BASES)}},
    "variants": {v["variant_id"]: v for v in _VARIANTS},
    "variant_order": [v["variant_id"] for v in _VARIANTS],
    "sizing": RG.thaw(RG.LIB_V1["sizing"]),
    "kernel": {"version": "kernel_v2 + S1d eki (limit_through, stop_pct, target_beyond_entry; LIB_V1 davranışı aynı)",
               "costs": "WF_PROTOCOL_V1.thresholds.costs (base / ADVERSE / STRESS)", "funding_mark": "S1b FUNDING_MARK_V1",
               "gate_exclusions": "WF_PROTOCOL_V1.gate_exclusions"},
    "universe": "AG_UNIVERSE_V1",
    "calendar": "AG_CALENDAR_V1",
    "scopes": {"AG_POOLED": {"applies_to": list(FAMILY_COUNTS), "unit": "VARIANT", "symbols": "AG_UNIVERSE_V1",
                             "instances": 1, "row_id_pattern": "<varyant>@AG_POOLED",
                             "def_tr": "aynı parametre, 41 sembol; işlemler birleştirilir; sembol başına sonuç yalnız rapor"}},
    "gates": {"same_as": "LIB_V1 (WF_PROTOCOL_V1, STATS_V1; S2 eşikleri mühürlenince onlar)",
              "bh_family": "o Kapı A bakışındaki LIB_V1 satırları + bu 38 AG satırı (tek aile; m büyür → daha sıkı)",
              "dsr_n": "N_raw_cum (+ 38 SPEC) + N_lesson_cum + N_explore_cum",
              "pbo_groups": _groups(), "placebo": "STATS_V1.placebo (aynı kurulum)",
              "row_type": "AG", "new_thresholds": "NONE"},
    "trials": {"lib_version": LIB_AG_VERSION, "kind": "SPEC", "rows": _N, "dedup_family": "LIB_AG_v1 (LIB_v1'den ayrı aile)",
               "spec_sha": "registry.spec_sha({family, variant_id, params, timeframes, exits, sizing, module_src_sha})"},
    "budget": {"variants": _N, "family_counts": FAMILY_COUNTS, "gate_a_rows": _N, "raw_trials": _N,
               "explore_series": _N * _N_SYMBOLS, "lib_v1_n_at_seal": 4513, "n_at_seal_with_ag": 4513 + _N,
               "structurally_empty": [v["variant_id"] for v in _VARIANTS if v["structurally_empty"]]},
    "explore": {"t_seal": "S1D_SEAL_MS", "series": _N * _N_SYMBOLS,
                "reading_tr": "AG keşfi (yapılırsa) S1D_SEAL_MS'den sonraki barlarla ileri; seriler bir sonraki LIB mühründe "
                              "N_explore'a eklenir (LIB_V1 kuralı)"},
    "readings_tr": READINGS_TR,
    "caveats_tr": CAVEATS_TR,
})

# ============================================================================ asla gevşetme
_META = ("id", "doc", "decision", "source_tr", "sessions_tr", "readings_tr", "caveats_tr", "reading_tr", "uncertain_tr",
         "excluded_tr")


def _dirs(reg: Mapping[str, Any]) -> dict[str, str]:
    return {k: FIXED for k in reg if k not in _META}


S1D_DIRECTIONS_V1: Mapping[str, Any] = RG.freeze({
    "LIB_AG": _dirs(LIB_AG_V1), "AG_UNIVERSE": _dirs(AG_UNIVERSE_V1), "AG_CALENDAR": _dirs(AG_CALENDAR_V1),
})

REGISTRIES: Mapping[str, Any] = MappingProxyType({"LIB_AG_V1": LIB_AG_V1, "AG_UNIVERSE_V1": AG_UNIVERSE_V1,
                                                  "AG_CALENDAR_V1": AG_CALENDAR_V1, "S1D_DIRECTIONS_V1": S1D_DIRECTIONS_V1})
S1D_VERSIONS: Mapping[str, tuple] = MappingProxyType({"LIB_AG": (LIB_AG_V1,), "AG_UNIVERSE": (AG_UNIVERSE_V1,),
                                                      "AG_CALENDAR": (AG_CALENDAR_V1,)})
META_KEYS = _META


def shas(registries: Mapping[str, Any] | None = None) -> dict[str, str]:
    regs = REGISTRIES if registries is None else registries
    return {name: RG.sha256_canonical(reg) for name, reg in regs.items()}


def seal_doc(registries: Mapping[str, Any] | None = None, seal_ms: int | None = None, s1_registry_sha: str | None = None,
             s1b_sha: str | None = None, s1c_sha: str | None = None) -> dict[str, Any]:
    """S1d'nin kanonik belgesi: dört sha, `S1D_SEAL_MS`, genişlettiği S1 `REGISTRY_SHA`, S1b `S1B_SHA`, S1c `S1C_SHA`."""
    return {"seal_id": S1D_ID, "shas": shas(registries), "s1d_seal_ms": int(S1D_SEAL_MS if seal_ms is None else seal_ms),
            "extends_s1_registry_sha": RG.REGISTRY_SHA if s1_registry_sha is None else s1_registry_sha,
            "extends_s1b_sha": S1B.S1B_SHA if s1b_sha is None else s1b_sha,
            "extends_s1c_sha": S1C.S1C_SHA if s1c_sha is None else s1c_sha}


SHAS: Mapping[str, str] = MappingProxyType(shas())
LIB_AG_V1_SHA = SHAS["LIB_AG_V1"]
AG_UNIVERSE_V1_SHA = SHAS["AG_UNIVERSE_V1"]
AG_CALENDAR_V1_SHA = SHAS["AG_CALENDAR_V1"]
S1D_DIRECTIONS_V1_SHA = SHAS["S1D_DIRECTIONS_V1"]
S1D_SEAL: Mapping[str, Any] = RG.freeze(seal_doc())
S1D_SHA = RG.sha256_canonical(S1D_SEAL)

#: mühür anında sabitlenen değerler (içerik değişip bunlar yenilenmezse `check()` tutmaz → AG varyantı kurulmaz)
PINNED: Mapping[str, str] = MappingProxyType({
    "LIB_AG_V1": "6421cce57d83d7178a5cfeebf32c6b8141f7977dbfba65e93a9a52a566be82a1",
    "AG_UNIVERSE_V1": "7e856fa946d6f0668df1e44e6a0cddd371cbaee8a5b70b72001fb27ed8c4a3ec",
    "AG_CALENDAR_V1": "1ac8e04cd3158aaaf80b29bd8a4f8b19e94b7bf1124607216c4db60ba140bb9d",
    "S1D_DIRECTIONS_V1": "a69cd988100a7be82efa88f86c2a1596e3e269bc49d141c5b5415ec4b19e1c15",
    "S1D": "065b1e7d77d26b6131a149621b75167d4f05666c67cc69cadb9e309d25bb2798",
})


class S1dSealError(RuntimeError):
    """S1d mührü tutmuyor (eksik ya da sha uyuşmazlığı): LIB_AG_V1 varyantları kurulmaz/koşulmaz."""


def check() -> tuple[bool, str]:
    """(tutuyor mu, neden). S1c (→ S1b) ve S1d sha'ları ŞİMDİ yeniden hesaplanır ve sabitlerle karşılaştırılır."""
    ok, why = S1C.check()
    if not ok:
        return False, f"S1D_EXTENDS_BROKEN_S1C: {why}"
    now = shas()
    for name, pin in PINNED.items():
        if name == "S1D":
            continue
        if now.get(name) != pin:
            return False, f"S1D_SHA_MISMATCH: {name} {str(now.get(name))[:12]} ≠ sabit {pin[:12]}"
    s1d = RG.sha256_canonical(seal_doc())
    if s1d != PINNED["S1D"]:
        return False, f"S1D_SHA_MISMATCH: S1D {s1d[:12]} ≠ sabit {PINNED['S1D'][:12]} (S1, S1b, S1c ya da S1D_SEAL_MS değişti)"
    return True, "OK"


def require() -> None:
    ok, why = check()
    if not ok:
        raise S1dSealError(why)


__all__ = ["AG_CALENDAR_V1", "AG_CALENDAR_V1_SHA", "AG_UNIVERSE_V1", "AG_UNIVERSE_V1_SHA", "CAL_FROM", "CAL_UNCERTAIN",
           "CAL_UNTIL", "COMMON", "FAMILY_COUNTS", "FOMC_STATEMENT_DAYS", "LIB_AG_V1", "LIB_AG_V1_SHA", "LIB_AG_VERSION",
           "META_KEYS", "NO_STATED_TIME_EXIT_BARS", "NYSE_FULL_HOLIDAYS", "OVERLAYS", "OVERLAY_BASES", "PINNED",
           "REGISTRIES", "S1D_DIRECTIONS_V1", "S1D_DIRECTIONS_V1_SHA", "S1D_ID", "S1D_SEAL", "S1D_SEAL_MS", "S1D_SHA",
           "S1D_VERSIONS", "SHAS", "S1dSealError", "check", "require", "seal_doc", "shas"]
