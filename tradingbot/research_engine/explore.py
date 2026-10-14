"""P3e — keşif katmanı (S4x; docs/ENGINE_BRAIN_V1.md §5.1, §2.5, §3.7 (6); ana belge §6.5, §6.8; mühür `LIB_V1.explore`,
`T_SEAL_MS`). Yalnız PAPER araştırma kodu: ağsız, kayıt-yalnız, terfi ettirmez, deneme defterine yazmaz, canlı toplama
girmez. Gece bağlantısı (S4x bütçesi, `DATA_STALE`/A-B KAPALI geceleri) P3g'dedir.

* **Seriler (3.745, kayıttan):** 45 bağımsız kripto varyantı (STANDARD, alias'sız) × giriş evreninin 40 coin'i + 13 kopya
  × 40 + 35 örtü × 40 + XSEC 5 (`PORT_XSEC`) + spread 2 (`PAIR_BTCETH`) + altın 6 × {XAUUSDT, PAXGUSDT vadeli, PAXG spot}
  (`series_plan`; sayı `registry.explore_series()`'e eşit, evren 40 değilse kapalı hata). Boşluklu (UNSPECIFIED) 13
  varyantın 25 serisi planda SAYILIR ama çalıştırılmaz (`UNSPECIFIED_PARAM`).
* **Veri (zaman yolculuğu yok):** yalnız mühürlü (`provider.SealedReader`) ve ARŞİVLE DOĞRULANMIŞ (`_src == archive`)
  satırlar. Açılışı `T_SEAL_MS`'den önce olan barlar YALNIZ göstergeleri ısıtır (varyantın mühürlü penceresi kadar,
  sabit `warm_start`); ilk karar, açılışı ≥ `T_SEAL_MS` olan ilk barın kapanışındadır (`start_ms` = T_SEAL + dilim; vekil
  `_Fwd` ayrıca karar anını denetler). İleri satırlar ilk doğrulanmamış/kapanmamış satırda kesilir (cephe); serinin
  cephesi kullandığı bütün dizilerin (sinyal ve görünüm dilimleri, 1h yol barları, BTC dizileri) en küçüğüdür.
  `DATA_MOVING` (mühürden sonra değişen parça) olan coin'in serileri o gece atlanır (durum dosyası değişmez).
* **Çekirdek:** `kernel.run_lockstep` (P3b); kopya + örtüler `kernel.make_family` ile tek paylaşılan akış (P3b-2b notu
  (3)). Maliyet (base/ADVERSE/STRESS) `kernel.cost_trade`, fonlama `kernel.funding_of`, kapı dışı işaret
  `kernel.gate_excluded`, günlük piyasa değerlemesi `stats.mtm_allocation` (U3), çıkış referansı `wf.exit_ref` — yeni
  istatistik yok.
* **Artımlı = baştan (bayt-özdeş, test):** durum = son "düz" karar anı (`checkpoint_ms`: açık pozisyon varsa onun karar
  anı, yoksa cephedeki son sinyal barının kapanışı). Orada çekirdeğin bütün durumu (pozisyon yok, bekleyen eylem yok)
  önceki girişlerin geçmişi (`ctx.history`; JSON biçimine çevrilmiş — iki yolda aynı) ve kullanılmış sinyallerdir
  (`one_entry_per_signal`); vekil bunları yeniden verir ve karar anından önce niyet döndürmez. Çekirdek nedenseldir; kapanmış
  işlemin yolu kesindir. Fonlamadan türeyen alanlar (fonlama, `FUNDING_GAP`, R, net, MTM) seri penceresinin iki yanındaki
  uzlaşmalara bağlıdır (`kernel.FundingSeries` yerel aralığı): işlem, çıkışından sonra ≥ `SETTLE_ROWS` uzlaşma gelene kadar
  `unsettled` kalır ve her gece aynı fonksiyonlarla yeniden türetilir; sonra `<SYM>.jsonl`'a eklenir (yalnız eklenir).
  Günlük satırlar (`D`) yalnız gün kesinleşince eklenir; satırlar zaman anahtarına göre sıralıdır, kesinleşen küme her
  zaman önektir → artımlı ekleme baştan yazımla aynı baytlar. Önceki cephenin verisi değiştiyse (parmak izi), sürüm/mühür
  değiştiyse ya da dosya boyu tutmuyorsa seri BAŞTAN kurulur.
* **Dosyalar:** `explore/LIB_v1/<varyant>/<COIN>.state.json` (kanonik JSON: cephe, karar anı, kullanılmış sinyaller,
  kesinleşmemiş işlemler, açık pozisyon, toplamlar, özet, veri parmak izi) ve `<COIN>.jsonl` (kesin işlemler `T` ve günlük
  özsermaye `D`: üç maliyetle günlük %, 1 USDT risk / 200 USDT taban); `explore/LIB_v1/_run.json` son adımın raporu.
* **Kapı yolu:** keşif Kapı A/B'ye doğrudan girmez. Tek okuyucu `fwd_reads` → `stats.gate_a_look(fwd=…)` (§3.7 (6)):
  satırın KENDİ serileri (havuz/küme: üyelerin birleşimi; `COIN_FIT`: mühürde donmuş seçim, verilmezse yok; `POOLED_OWN`:
  defterin kendi sembolleri, verilmezse yok), yalnız kapı-içi kapanmış işlemler; `{days, n, mean_adverse, intraday}`.
* **Gösterim:** `leaderboard_lines` (lider tablosunun KEŞİF bölümü), `digest_lines` (özet), `query_lines`
  (`engine-query explore [--gold] [--variant <id>]`, salt-okunur, ≤ 150 satır / 4 KB). Betimseldir: hüküm kelimesi
  üretmez (`verdict_word_violations`; metin testi) — "en iyi 5" başlığı "seçim yanlılığı, kanıt değil" der.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Sequence as _SeqABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import kernel as KN
from . import registry as RG
from . import stats as ST
from . import universe as U
from .wf import exit_ref
from ..strategy_lib import BookEntry

DAY_MS = 86_400_000
TF_MS: Mapping[str, int] = KN.TF_MS
LIB_VERSION = RG.LIB_VERSION
T_SEAL_MS = int(RG.T_SEAL_MS)
EXPLORE_VERSION = "explore_v1"
STATE_SCHEMA = "explore_state_v1"
SOURCE = "EXPLORATION_FWD"
ROOT_NAME = "explore"
_EX = RG.LIB_V1["explore"]
UNIVERSE_SIZE = int(_EX["entry_universe_size"])                       # 40
GOLD_INSTRUMENTS: tuple[str, ...] = tuple(_EX["gold_instruments"])     # XAUUSDT, PAXGUSDT_FUT, PAXG_SPOT
XSEC_INSTRUMENT, SPREAD_INSTRUMENT = "PORT_XSEC", "PAIR_BTCETH"
SCENARIOS: tuple[str, ...] = KN.SCENARIOS                               # base, ADVERSE, STRESS
GATE_COST = ST.GATE_COST                                                # ADVERSE
_GI = SCENARIOS.index(GATE_COST)
EQUITY_BASE_USDT = ST.EQUITY_BASE_USDT                                  # 200 (1 USDT risk = %0,5)
INTRADAY_TFS = ("5m", "15m")
#: çekirdeğe verilen dilimin karar anından önce taşıdığı ek bar (görünüm penceresi + bu pay)
SLICE_MARGIN = 2
#: yükleme penceresinin pencereye göre payı (veri boşlukları: görünüm "son L kapanmış bar"dır)
WARM_SLACK = 1.5
#: fonlama satırlarının karar penceresinden önceki payı
FUND_MARGIN_MS = 7 * DAY_MS
#: işlem çıkışından sonra gelen bu kadar uzlaşmadan sonra fonlamadan türeyen alanları kesindir (FundingSeries yerel
#: aralığı ± FUNDING_LOCAL_SPAN aralık okur)
SETTLE_ROWS = KN.FUNDING_LOCAL_SPAN + 1
FUNDING_FLAGS: tuple[str, ...] = KN.FUNDING_FLAGS
#: mark fiyatı 1h dizisi (fonlama mark'ı yalnız buradan: `kernel.settlement_marks`)
MARKPX = "markpx_1h"
MARK_TF_MS = KN.MARK_TF_MS
ARCHIVE_SRC = "archive"

ST_OK, ST_BLOCKED, ST_NO_FORWARD = "OK", "UNSPECIFIED_PARAM", "NO_FORWARD_DATA"
ST_DATA_MOVING, ST_NO_TICK, ST_ERROR, ST_DEADLINE, ST_UNCHANGED = ("DATA_MOVING", "NO_TICK", "ERROR", "DEADLINE",
                                                                   "UNCHANGED")
QUERY_MAX_LINES, QUERY_MAX_BYTES = 150, 4096
TOP_K = 5


@dataclass(frozen=True)
class LibCtx:
    """Keşfin kütüphane bağlamı (P3g): `LIB_V1` (varsayılan; davranışı ve baytları DEĞİŞMEDİ) ya da S1d `LIB_AG_V1`
    (`ag_ctx()`; ileri veri `S1D_SEAL_MS`'den, kök `explore/LIB_AG_v1`). `extra`: durum dosyasına eklenen ve sürdürmede
    denetlenen kimlik alanları (LIB_V1'de yok)."""

    lib: str
    t_seal_ms: int
    extra: tuple[tuple[str, Any], ...] = ()

    @property
    def is_ag(self) -> bool:
        return self.lib != LIB_VERSION


LIB_CTX = LibCtx(LIB_VERSION, T_SEAL_MS)


def ag_ctx() -> LibCtx:
    from . import seal_s1d as S1D
    return LibCtx(S1D.LIB_AG_VERSION, int(S1D.S1D_SEAL_MS), (("s1d_sha", S1D.S1D_SHA),))


def is_ag_variant(variant_id: str) -> bool:
    from . import lib_ag as LA
    return LA.is_ag(variant_id)


def make_any(variant_id: str) -> Any:
    """Kayıttan varyant: LIB_V1 (`kernel.make_variant`) ya da LIB_AG_V1 (`lib_ag.make_variant`; S1d tutmazsa kurulmaz)."""
    if is_ag_variant(variant_id):
        from . import lib_ag as LA
        return LA.make_variant(variant_id)
    return KN.make_variant(variant_id)


class ExploreRefused(RuntimeError):
    """Kapalı hata: mühürlü keşif sayımı/evreni tutmuyor."""


# ============================================================================ seri planı (§5.1; 3.745)
@dataclass(frozen=True)
class Series:
    variant_id: str
    instrument: str          # coin (ör. "BTC"), PORT_XSEC, PAIR_BTCETH ya da altın enstrümanı
    family: str
    group: str               # STANDARD | COPY | COVER | OTHER
    gaps: tuple[str, ...] = ()

    @property
    def series_id(self) -> str:
        return f"{self.variant_id}@{self.instrument}"

    @property
    def blocked(self) -> bool:
        return bool(self.gaps)

    @property
    def gold(self) -> bool:
        return self.family == "G"


coin_of = U.coin_of


class EntryUniverseRefused(ExploreRefused):
    """Çalışma anındaki giriş evreni dondurulmuş 40 coin'lik listeyle (`universe.ENTRY_UNIVERSE_FROZEN`) aynı değil:
    keşif çalışmaz; fark `diff`te raporlanır."""

    def __init__(self, diff: Mapping[str, Any]):
        super().__init__(f"ENTRY_UNIVERSE_DIFF: eksik {list(diff['missing'])} · fazla {list(diff['extra'])} · "
                         f"yinelenen {list(diff['duplicates'])} · mühür {diff.get('seal')}")
        self.diff = dict(diff)


def _group_of(family: str) -> str:
    for g, fams in RG.LIB_V1["family_groups"].items():
        if family in fams:
            return g
    raise KeyError(family)


def series_plan(entry_symbols: Sequence[str]) -> list[Series]:
    """Mühürlü keşif serileri (kayıt sırası × dondurulmuş evren sırası). Çalışma anındaki evren dondurulmuş 40 coin'le
    (sırasız) aynı değilse `EntryUniverseRefused` (fark raporlanır, koşu yok); dondurulmuş liste mühürlü sayıyı
    (`LIB_V1.explore.entry_universe_size`) tutmazsa `ExploreRefused`."""
    diff = U.entry_universe_diff(entry_symbols)
    if not diff["ok"]:
        raise EntryUniverseRefused(diff)
    coins = list(U.ENTRY_UNIVERSE_FROZEN)
    if len(coins) != UNIVERSE_SIZE:
        raise ExploreRefused(f"ENTRY_UNIVERSE_SIZE: dondurulmuş liste {len(coins)} coin, mühür {UNIVERSE_SIZE}")
    blocked = RG.blocked_variants()
    out: list[Series] = []
    for v in RG.LIB_V1["variants"]:
        if v.get("alias_of"):
            continue
        vid, fam = v["variant_id"], v["family"]
        grp = _group_of(fam)
        if grp in ("STANDARD", "COPY", "COVER"):
            inst: Sequence[str] = coins
        elif fam == "J":
            inst = (XSEC_INSTRUMENT,)
        elif fam == "I":
            inst = (SPREAD_INSTRUMENT,)
        elif fam == "G":
            inst = GOLD_INSTRUMENTS
        else:                                                   # pragma: no cover - kayıt değişirse kapalı hata
            raise ExploreRefused(f"keşif planında yeri olmayan aile: {fam}")
        gaps = tuple(blocked.get(vid, ()))
        out += [Series(vid, i, fam, grp, gaps) for i in inst]
    if len(out) != RG.explore_series():
        raise ExploreRefused(f"EXPLORE_SERIES: plan {len(out)} ≠ kayıt {RG.explore_series()}")
    return out


def plan_counts(plan: Sequence[Series]) -> dict[str, int]:
    c = {"total": len(plan), "run": sum(1 for s in plan if not s.blocked), "blocked": sum(1 for s in plan if s.blocked),
         "gold": sum(1 for s in plan if s.gold)}
    for g in ("STANDARD", "COPY", "COVER"):
        c[g] = sum(1 for s in plan if s.group == g)
    c["XSEC"] = sum(1 for s in plan if s.family == "J")
    c["SPREAD"] = sum(1 for s in plan if s.family == "I")
    return c


# ============================================================================ varyant bilgisi ve ısınma
@dataclass(frozen=True)
class Meta:
    variant_id: str
    signal_tf: str
    timeframes: tuple[str, ...]
    lookback: Mapping[str, int]
    needs_btc: bool
    btc_lookback: int
    needs_btc_4h: bool
    btc_4h_lookback: int
    funding_lookback_ms: int
    t_seal_ms: int = T_SEAL_MS              # AG: S1D_SEAL_MS (P3g)

    @property
    def step(self) -> int:
        return TF_MS[self.signal_tf]

    @property
    def start_ms(self) -> int:
        """İlk karar anı: açılışı ≥ T_SEAL olan ilk barın kapanışı (her hizalamada kapanış ≥ T_SEAL + dilim)."""
        return self.t_seal_ms + self.step

    @property
    def arrays(self) -> tuple[str, ...]:
        """Serinin okuduğu coin dizileri (görünüm dilimleri + 1h yol barları)."""
        tfs = set(self.timeframes) | {self.signal_tf}
        if self.step > TF_MS[KN.PATH_TF]:
            tfs.add(KN.PATH_TF)
        return tuple(sorted(tfs, key=lambda t: TF_MS[t]))


_META: dict[str, Meta] = {}


def meta(variant_id: str) -> Meta:
    m = _META.get(variant_id)
    if m is None:
        ag = is_ag_variant(variant_id)
        v = make_any(variant_id)
        lb = {str(k): int(x) for k, x in dict(v.lookback).items()}
        for tf in set(v.timeframes) | {v.signal_tf}:
            if int(lb.get(tf, 0) or 0) <= 0:          # sınırsız pencere → artımlı = baştan kanıtlanamaz (kapalı hata)
                raise ExploreRefused(f"{variant_id}: {tf} penceresi sınırsız")
        if v.needs_btc and int(v.btc_lookback or 0) <= 0:
            raise ExploreRefused(f"{variant_id}: BTC 1d penceresi sınırsız")
        if getattr(v, "needs_btc_4h", False) and int(getattr(v, "btc_4h_lookback", 0) or 0) <= 0:
            raise ExploreRefused(f"{variant_id}: BTC 4h penceresi sınırsız")
        m = Meta(variant_id, v.signal_tf, tuple(v.timeframes), lb, bool(v.needs_btc), int(v.btc_lookback or 0),
                 bool(getattr(v, "needs_btc_4h", False)), int(getattr(v, "btc_4h_lookback", 0) or 0),
                 int(v.funding_lookback_ms or 0) if getattr(v, "needs_funding", False) else 0,
                 ag_ctx().t_seal_ms if ag else T_SEAL_MS)
        _META[variant_id] = m
    return m


def runnable_variants() -> list[str]:
    blocked = RG.blocked_variants()
    return [v["variant_id"] for v in RG.LIB_V1["variants"] if not v.get("alias_of") and v["variant_id"] not in blocked
            and _group_of(v["family"]) in ("STANDARD", "COPY", "COVER")]


_WARM: dict[str, dict[str, int]] = {}


def ag_runnable_variants() -> list[str]:
    """LIB_AG_V1'in bütün varyantları (boşluk yok; S1d)."""
    from . import lib_ag as LA
    return LA.variant_ids()


def warm_bars(ctx: LibCtx | None = None) -> dict[str, int]:
    """Dizi başına en uzun mühürlü pencere (bar): coin dilimleri, `btc_1d`, `btc_4h` (BTC dizileri BTC'nin kendi
    dilimleriyle aynı yüklenir). `ctx` AG ise LIB_AG_V1 varyantlarınınki (P3g)."""
    ag = ctx is not None and ctx.is_ag
    key = "AG" if ag else "LIB"
    if key not in _WARM:
        w: dict[str, int] = {}
        for vid in (ag_runnable_variants() if ag else runnable_variants()):
            m = meta(vid)
            for tf in m.arrays:
                w[tf] = max(w.get(tf, 0), int(m.lookback.get(tf, 0) or 0))
            if m.needs_btc:
                w["1d"] = max(w.get("1d", 0), m.btc_lookback)
            if m.needs_btc_4h:
                w["4h"] = max(w.get("4h", 0), m.btc_4h_lookback)
        _WARM[key] = w
    return dict(_WARM[key])


def warm_start(tf: str, t_seal_ms: int = T_SEAL_MS, ctx: LibCtx | None = None) -> int:
    """Dizinin yükleme başı (SABİT: yalnız mühüre bağlı): T_SEAL − ⌈(pencere + pay) × 1,5⌉ bar."""
    bars = math.ceil((warm_bars(ctx).get(tf, 0) + SLICE_MARGIN) * WARM_SLACK)
    return int(t_seal_ms) - bars * TF_MS[tf]


def funding_warm_start(t_seal_ms: int = T_SEAL_MS, ctx: LibCtx | None = None) -> int:
    vids = ag_runnable_variants() if (ctx is not None and ctx.is_ag) else runnable_variants()
    lb = max((meta(v).funding_lookback_ms for v in vids), default=0)
    return int(t_seal_ms) - lb - FUND_MARGIN_MS


# ============================================================================ girdiler (mühürlü, arşivle doğrulanmış)
@dataclass
class CoinData:
    """Bir coin'in keşif girdisi: `bars[tf]` (yalnız doğrulanmış, kapanmış; `warm_start`'tan cepheye), `frontier[tf]`
    (ileri barların son kapanışı; ileri bar yoksa None), `funding` [(ms, oran, mark)] (aynı kurallar), `tick`."""

    coin: str
    bars: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    frontier: dict[str, int | None] = field(default_factory=dict)
    funding: list[tuple[int, float, float]] | None = None
    tick: float | None = None
    moving: str | None = None
    _close_t: dict[str, list[int]] = field(default_factory=dict, repr=False)
    _fp: dict[tuple[str, int], str] = field(default_factory=dict, repr=False)

    @property
    def symbol(self) -> str:
        return f"{self.coin}/USDT"

    def close_t(self, tf: str) -> list[int]:
        ct = self._close_t.get(tf)
        if ct is None:
            step = MARK_TF_MS if tf == MARKPX else TF_MS[tf]
            ct = self._close_t[tf] = [int(r["timestamp"]) + step for r in self.bars.get(tf) or ()]
        return ct

    def fingerprint(self, name: str, upto: int) -> str:
        """Dizinin başından `upto`'ya (bar kapanışı / uzlaşma anı ≤ upto) satırlarının sha256'sı."""
        k = (name, int(upto))
        if k not in self._fp:
            h = hashlib.sha256()
            if name == "funding":
                for t, r, m in self.funding or ():
                    if t > upto:
                        break
                    h.update(f"{t},{r!r},{m!r};".encode())
            else:
                for r, ct in zip(self.bars.get(name) or (), self.close_t(name)):
                    if ct > upto:
                        break
                    h.update(f"{r['timestamp']},{r['open']!r},{r['high']!r},{r['low']!r},{r['close']!r},"
                             f"{r.get('volume')!r};".encode())
            self._fp[k] = h.hexdigest()
        return self._fp[k]

    def markpx_upto(self, upto: int) -> list[dict[str, Any]]:
        """Kapanışı ≤ `upto` mark fiyatı barları (fonlama mark'ı; `kernel.settlement_marks`)."""
        return (self.bars.get(MARKPX) or [])[:bisect.bisect_right(self.close_t(MARKPX), int(upto))]

    def mark_front(self, upto: int) -> int | None:
        f = self.frontier.get(MARKPX)
        return None if f is None else min(int(f), int(upto))


def _src_ok(row: Mapping[str, Any]) -> bool:
    return row.get("_src", ARCHIVE_SRC) == ARCHIVE_SRC


def inputs_from_rows(coin: str, bars: Mapping[str, Sequence[Mapping[str, Any]]],
                     funding: Sequence[Any] | None, tick: float | None, *, now_ms: int,
                     t_seal_ms: int = T_SEAL_MS, markpx: Sequence[Mapping[str, Any]] | None = None,
                     ctx: LibCtx | None = None) -> CoinData:
    """Satırlardan girdi (depo okuyucusu `_src` sütununu verir; `_src` yoksa satır doğrulanmış sayılır — yalnız sentetik
    testler). Kural: `warm_start` öncesi atılır; mühür öncesi yalnız `archive`; ileri satırlar ilk doğrulanmamış ya da
    kapanmamış satırda KESİLİR (sonrası yok sayılır: cephe). `markpx`: mark fiyatı 1h barları (aynı kurallar; yükleme
    başı fonlamanınki) — fonlamanın mark'ı yalnız buradan."""
    cd = CoinData(coin=coin_of(coin), tick=(float(tick) if tick is not None and float(tick) > 0 else None))
    arrays = dict(bars)
    if markpx is not None:
        arrays[MARKPX] = markpx
    for tf, rows in arrays.items():
        step = MARK_TF_MS if tf == MARKPX else TF_MS[tf]
        w0 = funding_warm_start(t_seal_ms, ctx) if tf == MARKPX else warm_start(tf, t_seal_ms, ctx)
        by: dict[int, Mapping[str, Any]] = {}
        for r in rows:
            by[int(r["timestamp"])] = r
        out: list[dict[str, Any]] = []
        front = None
        for ts in sorted(by):
            r = by[ts]
            if ts < w0:
                continue
            if ts < t_seal_ms:
                if _src_ok(r):
                    out.append(_bar(r, ts))
                continue
            if not _src_ok(r) or ts + step > now_ms:
                break
            out.append(_bar(r, ts))
            front = ts + step
        cd.bars[tf] = out
        cd.frontier[tf] = front
    if funding is not None:
        f0 = funding_warm_start(t_seal_ms, ctx)
        fr: dict[int, tuple[int, float, float, bool]] = {}
        for x in funding:
            if isinstance(x, Mapping):
                t, r, m, ok = int(x["timestamp"]), x.get("rate"), x.get("mark"), _src_ok(x)
            else:
                t, r, m, ok = int(x[0]), x[1], x[2], (len(x) < 4 or x[3] == ARCHIVE_SRC)
            fr[t] = (t, _num(r), _num(m), ok)
        rows_f: list[tuple[int, float, float]] = []
        for t in sorted(fr):
            t_, r, m, ok = fr[t]
            if t < f0:
                continue
            if t < t_seal_ms:
                if ok:
                    rows_f.append((t_, r, m))
                continue
            if not ok or t > now_ms:
                break
            rows_f.append((t_, r, m))
        cd.funding = rows_f
    return cd


def _num(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v


def _bar(r: Mapping[str, Any], ts: int) -> dict[str, Any]:
    return {"timestamp": ts, "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
            "close": float(r["close"]), "volume": float(r.get("volume") or 0.0)}


def funding_intervals(funding: Sequence[tuple[int, float, float]] | None, since_ms: int = T_SEAL_MS) -> dict[str, Any]:
    """P3g gerçek veri sayacı (§8.4 P3b-2a m-D): mühürden sonraki uzlaşma aralıkları (saat, yuvarlanmış) ve aralığın
    değiştiği geçiş sayısı. Dürüst sınır: eksik bir uzlaşma da (ör. 8h → 16h) geçiş gibi görünür; borsa aralık geçmişiyle
    (fundingInfo) karşılaştırma veri biriminde yoktur."""
    ts = [int(x[0]) for x in (funding or ()) if int(x[0]) >= int(since_ms)]
    hs = [round((b - a) / 3_600_000) for a, b in zip(ts, ts[1:]) if b > a]
    return {"intervals_h": sorted(set(hs)), "changes": sum(1 for a, b in zip(hs, hs[1:]) if a != b),
            "settlements": len(ts)}


def load_coin(reader: Any, coin: str, *, tick: float | None, now_ms: int, market: str = "futures",
              t_seal_ms: int = T_SEAL_MS, tfs: Sequence[str] | None = None, ctx: LibCtx | None = None) -> CoinData:
    """Mühürlü depo okuyucusundan (`provider.SealedReader`; testte `ResearchStore`) girdi. Parça mühürden sonra değiştiyse
    `moving` dolu döner (coin'in serileri o gece atlanır). `tfs` (P3g parite (b)): yalnız bu diziler (varsayılan: bütün
    keşif dizileri)."""
    from .provider import DataMoving
    from .store import FUNDING, MARKPX_1H, SRC_COL

    raw = f"{coin_of(coin)}USDT"
    tfs = sorted(warm_bars(ctx) if tfs is None else set(tfs), key=lambda t: TF_MS[t])
    bars: dict[str, list[dict[str, Any]]] = {}
    fund: list[dict[str, Any]] = []
    mark: list[dict[str, Any]] = []
    try:
        for tf in tfs:
            rows: list[dict[str, Any]] = []
            for _ym, df in reader.iter_parts(market, raw, tf, warm_start(tf, t_seal_ms, ctx), None, keep_src=True):
                if len(df):
                    cols = [c for c in ("timestamp", "open", "high", "low", "close", "volume", SRC_COL) if c in df.columns]
                    rows += df[cols].to_dict("records")
            bars[tf] = rows
        for _ym, df in reader.iter_parts(market, raw, FUNDING, funding_warm_start(t_seal_ms, ctx), None, keep_src=True):
            if len(df):
                fund += df[[c for c in ("timestamp", "rate", "mark", SRC_COL) if c in df.columns]].to_dict("records")
        for _ym, df in reader.iter_parts(market, raw, MARKPX_1H, funding_warm_start(t_seal_ms, ctx), None,
                                         keep_src=True):
            if len(df):
                mark += df[[c for c in ("timestamp", "open", "high", "low", "close", SRC_COL)
                            if c in df.columns]].to_dict("records")
    except DataMoving as exc:
        return CoinData(coin=coin_of(coin), moving=f"{exc.series} {exc.ym}: {exc.reason}")
    for rows in list(bars.values()) + [fund, mark]:
        for r in rows:
            r["_src"] = r.pop(SRC_COL, None) or "?"              # kaynaksız satır doğrulanmamış sayılır
    return inputs_from_rows(coin, bars, fund, tick, now_ms=now_ms, t_seal_ms=t_seal_ms, markpx=mark, ctx=ctx)


# ============================================================================ JSON
def _json_norm(x: Any) -> Any:
    """JSON-yerli biçim (eşleme → str anahtarlı dict, dizi → list, numpy sayıları → Python, sonlu olmayan → None)."""
    if isinstance(x, Mapping):
        return {str(k): _json_norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_norm(v) for v in x]
    if x is None or isinstance(x, (str, bool)):
        return x
    if type(x).__name__ in ("bool_",):
        return bool(x)
    if isinstance(x, int) or type(x).__name__.startswith(("int", "uint")):
        try:
            return int(x)
        except (TypeError, ValueError):
            return str(x)
    if isinstance(x, float) or type(x).__name__.startswith("float"):
        v = float(x)
        return v if math.isfinite(v) else None
    return str(x)


def dumps(obj: Any) -> str:
    """Kanonik JSON (sıralı anahtar, `(",", ":")`, UTF-8, NaN yok)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _roundtrip(obj: Any) -> Any:
    return json.loads(dumps(obj))


# ============================================================================ çekirdek vekili (ileri koşu)
class _History(_SeqABC):
    """`ctx["history"]`: önceki koşuların girişleri (tembel yüklenir; yalnız okuyan varyantta) + bu koşunun girişleri;
    hepsi JSON biçiminde (iki yolda aynı)."""

    def __init__(self, proxy: "_Fwd", k: int) -> None:
        self._p, self._k = proxy, k
        self._all: tuple | None = None

    def _items(self) -> tuple:
        if self._all is None:
            self._all = self._p.head() + tuple(self._p.norm[:self._k])
        return self._all

    def __len__(self) -> int:
        return len(self._items())

    def __getitem__(self, i: Any) -> Any:
        return self._items()[i]

    def __iter__(self):
        return iter(self._items())


def _norm_entry(e: Any) -> BookEntry:
    return BookEntry(str(e.symbol), str(e.side), int(e.signal_ts), int(e.opened_ms), _json_norm(dict(e.evidence or {})))


class _Fwd:
    """Varyant vekili: karar anı `start`'tan önceyse niyet yok (zaman yolculuğu yok; ilk karar ≥ T_SEAL + dilim);
    `ctx.history` = önceki girişler + bu koşunun girişleri (JSON biçimi); önceki koşularda kullanılmış
    `one_entry_per_signal` sinyalleri düşer (çekirdeğin kendi denetimiyle aynı sonuç); niyetlerin bayrağı kaydedilir."""

    def __init__(self, inner: Any, start: int, head_loader: Callable[[], Sequence[BookEntry]] | None,
                 used0: Iterable[int]) -> None:
        self._inner = inner
        self._start = int(start)
        self._loader = head_loader
        self._head: tuple | None = None if head_loader is not None else ()
        self._used0 = frozenset(int(x) for x in used0)
        self.norm: list[BookEntry] = []
        self.oeps: dict[tuple[int, int, str], bool] = {}
        self.early_calls = 0

    def head(self) -> tuple:
        if self._head is None:
            self._head = tuple(self._loader() if self._loader else ())
        return self._head

    def _ctx(self, ctx: Mapping[str, Any]) -> dict[str, Any]:
        hist = ctx.get("history") or ()
        while len(self.norm) < len(hist):
            self.norm.append(_norm_entry(hist[len(self.norm)]))
        return dict(ctx, history=_History(self, len(hist)))

    def signals(self, bars: Any, ctx: Mapping[str, Any]) -> list:
        now = int(ctx["now_ms"])
        if now < self._start:
            self.early_calls += 1
            return []
        out = []
        for it in self._inner.signals(bars, self._ctx(ctx)) or ():
            o = bool(getattr(it, "one_entry_per_signal", False))
            if o and int(it.signal_ts) in self._used0:
                continue
            self.oeps.setdefault((now, int(it.signal_ts), str(it.side)), o)
            out.append(it)
        return out

    def manage(self, pos: Any, bars: Any, ctx: Mapping[str, Any]) -> Any:
        return self._inner.manage(pos, bars, self._ctx(ctx))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# ============================================================================ işlem satırları
def _path_row(t: KN.KernelTrade, sid: str, oeps: bool) -> dict[str, Any]:
    d = t.to_dict()
    row = {"k": "T", "series": sid, "source": SOURCE, "variant_id": d["variant_id"], "symbol": d["symbol"],
           "side": d["side"], "signal_ts": int(d["signal_ts"]), "entry_ts": int(d["entry_ts"]),
           "entry_ref": d["entry_ref"], "initial_stop": d["initial_stop"], "targets": d["targets"],
           "leverage": d["leverage"], "qty": d["qty"], "notional": d["notional"], "margin": d["margin"],
           "liq_price": d["liq_price"], "risk_usdt": d["risk_usdt"], "legs": d["legs"], "exit_ts": d["exit_ts"],
           "exit_kind": d["exit_kind"], "exit_reason": d["exit_reason"], "bars_held": d["bars_held"],
           "path_flags": [f for f in d["flags"] if f not in FUNDING_FLAGS], "move_stop_ignored": d["move_stop_ignored"],
           "oeps": bool(oeps), "evidence": _json_norm(dict(t.evidence or {}))}
    return _roundtrip(row)


_PATH_KEYS = ("k", "series", "source", "variant_id", "symbol", "side", "signal_ts", "entry_ts", "entry_ref", "initial_stop",
              "targets", "leverage", "qty", "notional", "margin", "liq_price", "risk_usdt", "legs", "exit_ts", "exit_kind",
              "exit_reason", "bars_held", "path_flags", "move_stop_ignored", "oeps", "evidence")


def _trade(row: Mapping[str, Any]) -> KN.KernelTrade:
    legs = [KN.Leg(lg["kind"], float(lg["ref"]), float(lg["frac"]), bool(lg["market"]), int(lg["ts"]), lg["point"],
                   lg.get("reason") or "", int(lg["t_from"]), lg.get("basis") or "") for lg in row["legs"]]
    return KN.KernelTrade(symbol=row["symbol"], variant_id=row["variant_id"], side=row["side"],
                          signal_ts=int(row["signal_ts"]), entry_ts=int(row["entry_ts"]), entry_ref=float(row["entry_ref"]),
                          initial_stop=float(row["initial_stop"]), targets=tuple(float(x) for x in row["targets"]),
                          leverage=int(row["leverage"]), qty=float(row["qty"]), notional=float(row["notional"]),
                          margin=float(row["margin"]), liq_price=float(row["liq_price"]),
                          risk_usdt=float(row["risk_usdt"]), legs=legs,
                          exit_ts=None if row["exit_ts"] is None else int(row["exit_ts"]), exit_kind=row["exit_kind"],
                          exit_reason=row["exit_reason"] or "", bars_held=int(row["bars_held"]),
                          flags=list(row["path_flags"]))


def _derive(path: Mapping[str, Any], fs: KN.FundingSeries | None, tick: float, bars: Sequence[Mapping[str, Any]],
            close_t: Sequence[int], step: int, *, open_: bool) -> dict[str, Any]:
    """Yol satırından türeyen alanlar — çekirdeğin döngü sonrası hesabının AYNISI (`funding_of`, işaretler,
    `cost_trade`), kapı dışı işaret (`gate_excluded`) ve üç maliyetle günlük piyasa değerlemesi (`stats.mtm_allocation`).
    `fs` mark'ı markpx'ten (`kernel.settlement_marks`)."""
    t = _trade(path)
    KN.apply_funding(t, fs)
    r, pnl = {}, {}
    for sc in SCENARIOS:
        r[sc], pnl[sc] = KN.cost_trade(t, KN.scenario(sc), tick)
    row = {k: path[k] for k in _PATH_KEYS}
    xr = exit_ref(t)
    row.update(funding_usdt=t.funding_usdt, funding_events=t.funding_events,
               funding_ambiguous_usdt=t.funding_ambiguous_usdt, funding_ambiguous_events=t.funding_ambiguous_events,
               flags=list(t.flags), r=r, pnl_usdt=pnl, excluded=KN.gate_excluded(t), exit_ref=xr,
               open=bool(open_))
    if open_:
        row["mtm"], row["mtm_method"] = None, None
    else:
        days: dict[int, list[float]] = {}
        method = None
        for i, sc in enumerate(SCENARIOS):
            rl = {"ts_ms": t.entry_ts, "exit_ms": int(t.exit_ts if t.exit_ts is not None else t.entry_ts),
                  "pnl": {GATE_COST: pnl[sc]}, "r": {GATE_COST: r[sc]}, "side": t.side, "entry_ref": t.entry_ref,
                  "exit_ref": xr, "initial_stop": t.initial_stop}
            alloc, method = ST.mtm_allocation(rl, bars, step, close_t=close_t)
            for d, v in alloc.items():
                days.setdefault(int(d), [0.0, 0.0, 0.0])[i] = float(v)
        row["mtm"] = [[d] + days[d] for d in sorted(days)]
        row["mtm_method"] = method
    return _roundtrip(row)


def _settle_end(row: Mapping[str, Any]) -> int:
    ends = [int(lg["ts"]) for lg in row["legs"]] + [int(lg["t_from"]) for lg in row["legs"]]
    if row.get("exit_ts") is not None:
        ends.append(int(row["exit_ts"]))
    return max(ends) if ends else int(row["entry_ts"])


def _settled(row: Mapping[str, Any], fs_ts: Sequence[int], mark_front: int | None) -> bool:
    """Fonlamadan türeyen alanlar kesin mi: çıkıştan sonra ≥ SETTLE_ROWS uzlaşma var VE mark fiyatı barları çıkıştan
    sonraki ilk saatin sonuna kadar doğrulanmış (pencerenin her uzlaşmasının "o anda başlayan bar"ı kesin var ya da
    kesin yok)."""
    end = _settle_end(row)
    return len(fs_ts) - bisect.bisect_right(fs_ts, end) >= SETTLE_ROWS and mark_front is not None \
        and mark_front >= end + MARK_TF_MS


# ============================================================================ toplamlar
def _agg0() -> dict[str, Any]:
    return {"n": 0, "n_excluded": 0, "sum_r": [0.0, 0.0, 0.0], "days": 0, "cum_pct": [0.0, 0.0, 0.0],
            "peak_pct": [0.0, 0.0, 0.0], "max_dd_pct": [0.0, 0.0, 0.0], "days_ge_1pct": [0, 0, 0]}


def _add_trade(agg: dict[str, Any], row: Mapping[str, Any]) -> None:
    if row["excluded"]:
        agg["n_excluded"] += 1
        return
    agg["n"] += 1
    for i, sc in enumerate(SCENARIOS):
        agg["sum_r"][i] += float(row["r"][sc])


def _add_day(agg: dict[str, Any], pct: Sequence[float]) -> None:
    agg["days"] += 1
    for i in range(3):
        agg["cum_pct"][i] += float(pct[i])
        agg["peak_pct"][i] = max(agg["peak_pct"][i], agg["cum_pct"][i])
        agg["max_dd_pct"][i] = max(agg["max_dd_pct"][i], agg["peak_pct"][i] - agg["cum_pct"][i])
        agg["days_ge_1pct"][i] += int(float(pct[i]) >= 1.0)


def _carry_add(carry: dict[int, list[float]], row: Mapping[str, Any]) -> None:
    if row["excluded"] or not row.get("mtm"):
        return
    for d, *v in row["mtm"]:
        c = carry.setdefault(int(d), [0.0, 0.0, 0.0])
        for i in range(3):
            c[i] += float(v[i])


def _pct(usdt: Sequence[float]) -> list[float]:
    return [float(x) / EQUITY_BASE_USDT * 100.0 for x in usdt]


def _summary(agg: Mapping[str, Any], carry: Mapping[int, list[float]], unsettled: Sequence[Mapping[str, Any]],
             open_row: Mapping[str, Any] | None, next_day: int, frontier: int, signal_tf: str,
             t_seal_ms: int = T_SEAL_MS) -> dict[str, Any]:
    a = _roundtrip(agg)
    for r in unsettled:
        _add_trade(a, r)
    prov: dict[int, list[float]] = {int(d): list(v) for d, v in carry.items()}
    for r in unsettled:
        _carry_add(prov, r)
    for d in range(int(next_day), int(frontier) // DAY_MS):
        _add_day(a, _pct(prov.get(d, [0.0, 0.0, 0.0])))
    n = a["n"]
    op = None
    if open_row is not None:
        op = {"side": open_row["side"], "entry_ts": open_row["entry_ts"],
              "r_mark": [open_row["r"][sc] for sc in SCENARIOS], "excluded": open_row["excluded"]}
    return {"n": n, "n_excluded": a["n_excluded"], "sum_r": a["sum_r"],
            "mean_r": [x / n for x in a["sum_r"]] if n else None, "axis_days": a["days"],
            "cum_pct": a["cum_pct"], "max_dd_pct": a["max_dd_pct"],
            "share_days_ge_1pct": [x / a["days"] for x in a["days_ge_1pct"]] if a["days"] else None,
            "days_since_seal": max(0, (int(frontier) - int(t_seal_ms)) // DAY_MS), "frontier_ms": int(frontier),
            "intraday": signal_tf in INTRADAY_TFS, "open": op}


# ============================================================================ depo (dosyalar)
class SeriesFiles:
    def __init__(self, root: Path, s: Series) -> None:
        d = Path(root) / s.variant_id
        self.dir = d
        self.state = d / f"{s.instrument}.state.json"
        self.rows = d / f"{s.instrument}.jsonl"

    def read_state(self) -> dict[str, Any] | None:
        try:
            with open(self.state, "r", encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            return None
        return st if isinstance(st, dict) else None

    def rows_size(self) -> int:
        try:
            return os.path.getsize(self.rows)
        except OSError:
            return 0

    def iter_rows(self) -> Iterable[dict[str, Any]]:
        try:
            with open(self.rows, "r", encoding="utf-8") as fh:
                for ln in fh:
                    if ln.strip():
                        yield json.loads(ln)
        except FileNotFoundError:
            return

    def reset(self) -> None:
        for p in (self.rows, self.state):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def append(self, lines: Sequence[str]) -> int:
        self.dir.mkdir(parents=True, exist_ok=True)
        if lines:
            with open(self.rows, "ab") as fh:
                fh.write("".join(x + "\n" for x in lines).encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
        return self.rows_size()

    def write_state(self, st: Mapping[str, Any]) -> None:
        _atomic_write(self.state, (dumps(st) + "\n").encode("utf-8"))


def _atomic_write(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ============================================================================ grup koşusu
#: kopya-örtü aileleri (tek paylaşılan akış); C4'te paylaşılan taban K satırı değildir (sekiz varyasyon ayrı K serisi)
def _families() -> dict[str, list[str]]:
    out = {}
    for base in RG.COVER_BASES:
        members = KN.covers_of(base)
        if base != "C4":
            members = [f"K_{base}"] + members
        out[base] = members
    return out


def coin_groups(series: Sequence[Series]) -> list[tuple[str | None, list[Series]]]:
    """Bir coin'in çalıştırılabilir serileri → koşu grupları: (taban, üyeler) kopya-örtü aileleri; diğerleri tek başına."""
    by = {s.variant_id: s for s in series if not s.blocked}
    out: list[tuple[str | None, list[Series]]] = []
    seen: set[str] = set()
    for base, members in _families().items():
        ms = [by[v] for v in members if v in by]
        if ms:
            out.append((base, ms))
            seen.update(s.variant_id for s in ms)
    out += [(None, [s]) for vid, s in by.items() if vid not in seen]
    return out


def _variants(base: str | None, members: Sequence[Series]) -> dict[str, Any]:
    if base is None:
        return {s.variant_id: make_any(s.variant_id) for s in members}
    shared, covers = KN.make_family(base)
    vs = dict(covers)
    if base != "C4":
        vs[shared.variant_id] = shared
    return {s.variant_id: vs[s.variant_id] for s in members}


def _group_frontier(cd: CoinData, btc: CoinData | None, metas: Sequence[Meta]) -> int | None:
    fr: list[int | None] = []
    for m in metas:
        fr += [cd.frontier.get(tf) for tf in m.arrays]
        if m.needs_btc:
            fr.append(btc.frontier.get("1d") if btc is not None else None)
        if m.needs_btc_4h:
            fr.append(btc.frontier.get("4h") if btc is not None else None)
    if not fr or any(f is None for f in fr):
        return None
    return int(min(fr))  # type: ignore[type-var]


def _slice(rows: list[dict[str, Any]], close_t: list[int], start: int, frontier: int, lb: int) -> list[dict[str, Any]]:
    hi = bisect.bisect_right(close_t, frontier)
    m = bisect.bisect_right(close_t, start) - 1
    lo = max(0, m - lb - SLICE_MARGIN)
    return rows[lo:hi]


def _group_data(cd: CoinData, btc: CoinData | None, metas: Sequence[Meta], start: int, frontier: int) -> KN.SeriesData:
    tfs = sorted({tf for m in metas for tf in m.arrays}, key=lambda t: TF_MS[t])
    bars = {tf: _slice(cd.bars.get(tf) or [], cd.close_t(tf), start, frontier,
                       max(int(m.lookback.get(tf, 0) or 0) for m in metas)) for tf in tfs}
    btc_1d = btc_4h = None
    if any(m.needs_btc for m in metas) and btc is not None:
        btc_1d = _slice(btc.bars.get("1d") or [], btc.close_t("1d"), start, frontier, max(m.btc_lookback for m in metas))
    if any(m.needs_btc_4h for m in metas) and btc is not None:
        btc_4h = _slice(btc.bars.get("4h") or [], btc.close_t("4h"), start, frontier,
                        max(m.btc_4h_lookback for m in metas))
    funding = None
    if cd.funding is not None:
        f0 = start - max(m.funding_lookback_ms for m in metas) - FUND_MARGIN_MS
        funding = [x for x in cd.funding if f0 <= x[0] <= frontier]
    mark = [b for b in cd.markpx_upto(frontier) if int(b["timestamp"]) >= start - FUND_MARGIN_MS - MARK_TF_MS] \
        if cd.bars.get(MARKPX) is not None else None
    return KN.SeriesData(symbol=cd.symbol, bars=bars, btc_1d=btc_1d, funding=funding, price_tick=float(cd.tick or 0.0),
                         btc_4h=btc_4h, markpx_1h=mark)


def _fp(cd: CoinData, btc: CoinData | None, metas: Sequence[Meta], upto: int) -> str:
    parts: dict[str, str] = {}
    for tf in sorted({tf for m in metas for tf in m.arrays}):
        parts[tf] = cd.fingerprint(tf, upto)
    if any(m.needs_btc for m in metas) and btc is not None:
        parts["btc_1d"] = btc.fingerprint("1d", upto)
    if any(m.needs_btc_4h for m in metas) and btc is not None:
        parts["btc_4h"] = btc.fingerprint("4h", upto)
    parts["funding"] = cd.fingerprint("funding", upto)
    parts[MARKPX] = cd.fingerprint(MARKPX, upto)
    parts["tick"] = repr(cd.tick)
    return hashlib.sha256(dumps(parts).encode("utf-8")).hexdigest()


def _s1b_sha() -> str | None:
    """S1b mührünün sha'sı (çekirdeğin yüklediği `seal_s1b`; yoksa None — o durumda adım zaten çalışmaz)."""
    return KN.S1B.S1B_SHA if KN.S1B is not None else None


def _state_ok(st: Mapping[str, Any] | None, s: Series, files: SeriesFiles, ctx: LibCtx = LIB_CTX) -> bool:
    return bool(st) and st.get("schema") == STATE_SCHEMA and st.get("version") == EXPLORE_VERSION \
        and st.get("registry_sha") == RG.REGISTRY_SHA and st.get("s1b_sha") == _s1b_sha() \
        and st.get("kernel") == KN.KERNEL_VERSION \
        and st.get("t_seal_ms") == ctx.t_seal_ms and st.get("series") == s.series_id \
        and all(st.get(k) == v for k, v in ctx.extra) \
        and int(st.get("rows_bytes", -1)) == files.rows_size()


def _head_loader(files: SeriesFiles, unsettled: Sequence[Mapping[str, Any]]) -> Callable[[], list[BookEntry]]:
    def load() -> list[BookEntry]:
        rows = [r for r in files.iter_rows() if r.get("k") == "T"] + list(unsettled)
        return [BookEntry(r["symbol"], r["side"], int(r["signal_ts"]), int(r["entry_ts"]), r.get("evidence") or {})
                for r in rows]
    return load


def _row_key(r: Mapping[str, Any]) -> tuple[int, int]:
    return ((int(r["d"]) + 1) * DAY_MS, 0) if r["k"] == "D" else (int(r["entry_ts"]), 1)


def _iso_day(d: int) -> str:
    return datetime.fromtimestamp(int(d) * DAY_MS / 1000, tz=timezone.utc).date().isoformat()


def step_group(root: Path, base: str | None, members: Sequence[Series], cd: CoinData, btc: CoinData | None,
               report: dict[str, Any], ctx: LibCtx = LIB_CTX) -> None:
    """Bir coin'in bir koşu grubunu ileri taşı (artımlı ya da gerekirse baştan). Sonuç durum dosyalarına yazılır."""
    metas = [meta(s.variant_id) for s in members]
    files = {s.series_id: SeriesFiles(root, s) for s in members}
    F = _group_frontier(cd, btc, metas)
    if cd.tick is None:
        for s in members:
            report["status"][ST_NO_TICK] = report["status"].get(ST_NO_TICK, 0) + 1
        return
    if F is None or F < min(m.start_ms for m in metas):
        for s in members:
            report["status"][ST_NO_FORWARD] = report["status"].get(ST_NO_FORWARD, 0) + 1
        return
    plan: list[dict[str, Any]] = []
    for s, m in zip(members, metas):
        fl = files[s.series_id]
        st = fl.read_state()
        mode = "SCRATCH"
        if _state_ok(st, s, fl, ctx) and int(st["frontier_ms"]) <= F and st.get("data_fp") == _fp(cd, btc, metas, int(st["frontier_ms"])):
            mode = "UNCHANGED" if int(st["frontier_ms"]) == F else "INCR"
        if mode == "UNCHANGED":
            report["status"][ST_UNCHANGED] = report["status"].get(ST_UNCHANGED, 0) + 1
            continue
        if mode == "SCRATCH":
            if st is not None or fl.rows_size():
                report["rebuilt"] += 1
            fl.reset()
            st = None
        plan.append({"s": s, "m": m, "st": st, "mode": mode, "files": fl,
                     "resume": int(st["checkpoint_ms"]) if st else m.start_ms})
    if not plan:
        return
    start = min(p["resume"] for p in plan)
    data = _group_data(cd, btc, [p["m"] for p in plan], start, F)
    vs = _variants(base, [p["s"] for p in plan])
    proxies = []
    for p in plan:
        st = p["st"]
        unsettled = st["unsettled"] if st else []
        loader = _head_loader(p["files"], unsettled) if st else None
        proxies.append(_Fwd(vs[p["s"].variant_id], max(p["resume"], p["m"].start_ms), loader,
                            st["used"] if st else ()))
    results = KN.run_lockstep(proxies, data, start_ms=start)
    fs_all = None
    if cd.funding is not None:
        rows_f, mism = KN.settlement_marks([x for x in cd.funding if x[0] <= F], cd.markpx_upto(F))
        fs_all = KN.FundingSeries(rows_f, mism)
    fs_ts = fs_all.ts if fs_all is not None else []
    fp_new = _fp(cd, btc, metas, F)
    for p, px, res in zip(plan, proxies, results):
        _finish_member(p, px, res, cd, fs_all, fs_ts, F, fp_new, report, ctx)


def _finish_member(p: Mapping[str, Any], px: _Fwd, res: KN.KernelResult, cd: CoinData, fs: KN.FundingSeries | None,
                   fs_ts: Sequence[int], F: int, fp_new: str, report: dict[str, Any], ctx: LibCtx = LIB_CTX) -> None:
    s, m, st, fl = p["s"], p["m"], p["st"], p["files"]
    resume = int(p["resume"])
    tf, step = m.signal_tf, m.step
    bars = cd.bars.get(tf) or []
    ct = cd.close_t(tf)
    closed_new, open_new = [], None
    for t in res.trades:
        if int(t.entry_ts) < max(resume, m.start_ms):           # savunma: vekil + çekirdek bunu olanaksız kılar
            raise ExploreRefused(f"{s.series_id}: karar anı öncesi giriş {t.entry_ts}")
        key = (int(t.entry_ts), int(t.signal_ts), str(t.side))
        if key not in px.oeps:
            raise ExploreRefused(f"{s.series_id}: niyet kaydı yok {key}")
        pr = _path_row(t, s.series_id, px.oeps[key])
        if KN.FLAG_OPEN_AT_END in t.flags:
            open_new = pr
        else:
            closed_new.append(pr)
    unsettled_old = [{k: r[k] for k in _PATH_KEYS} for r in (st["unsettled"] if st else [])]
    pending = unsettled_old + closed_new
    derived = [_derive(r, fs, float(cd.tick), bars, ct, step, open_=False) for r in pending]
    k = 0
    mark_front = cd.mark_front(F)
    while k < len(derived) and _settled(derived[k], fs_ts, mark_front):
        k += 1
    final_new, unsettled = derived[:k], derived[k:]
    open_row = _derive(open_new, fs, float(cd.tick), bars, ct, step, open_=True) if open_new is not None else None
    if open_row is not None:
        checkpoint = int(open_row["entry_ts"])
    else:
        j = bisect.bisect_right(ct, F) - 1
        checkpoint = max(resume, ct[j]) if j >= 0 else resume
    used = set(int(x) for x in (st["used"] if st else ()))
    used.update(int(r["signal_ts"]) for r in closed_new if r["oeps"])
    agg = _roundtrip(st["agg"]) if st else _agg0()
    carry = {int(x[0]): [float(y) for y in x[1:]] for x in (st["carry"] if st else [])}
    next_day = int(st["next_day"]) if st else ctx.t_seal_ms // DAY_MS
    for r in final_new:
        _add_trade(agg, r)
        if r.get("mtm") and not r["excluded"] and int(r["mtm"][0][0]) < next_day:
            raise ExploreRefused(f"{s.series_id}: kesinleşmiş güne yeni katkı")
        _carry_add(carry, r)
    limit = min(F // DAY_MS, checkpoint // DAY_MS,
                *[int(r["entry_ts"]) // DAY_MS for r in unsettled + ([open_row] if open_row else [])])
    day_rows = []
    for d in range(next_day, max(next_day, limit)):
        pct = _pct(carry.pop(d, [0.0, 0.0, 0.0]))
        _add_day(agg, pct)
        day_rows.append({"k": "D", "series": s.series_id, "d": d, "day": _iso_day(d), "pct": pct})
    next_day = max(next_day, limit)
    out_rows = sorted([dict(r) for r in final_new] + day_rows, key=_row_key)
    size = fl.append([dumps(r) for r in out_rows])
    summary = _summary(agg, carry, unsettled, open_row, next_day, F, tf, ctx.t_seal_ms)
    state = {"schema": STATE_SCHEMA, "version": EXPLORE_VERSION, "lib": ctx.lib, "registry_sha": RG.REGISTRY_SHA,
             "s1b_sha": _s1b_sha(),
             "kernel": KN.KERNEL_VERSION, "source": SOURCE, "series": s.series_id, "variant_id": s.variant_id,
             "instrument": s.instrument, "family": s.family, "signal_tf": tf, "t_seal_ms": ctx.t_seal_ms,
             "start_ms": m.start_ms, "frontier_ms": int(F), "checkpoint_ms": int(checkpoint), "used": sorted(used),
             "unsettled": unsettled, "open": open_row, "carry": [[d] + carry[d] for d in sorted(carry)],
             "next_day": next_day, "agg": agg, "rows_bytes": size, "data_fp": fp_new, "summary": summary,
             "status": ST_OK}
    state.update(ctx.extra)
    fl.write_state(_roundtrip(state))
    report["status"][ST_OK] = report["status"].get(ST_OK, 0) + 1
    tf_count = report.setdefault("trade_flags", {})
    for r in final_new:                                         # P3g: gerçek veri sayaçları (bu gece kesinleşenler)
        report["trades_final"] = report.get("trades_final", 0) + 1
        for f in r.get("flags") or ():
            tf_count[str(f)] = tf_count.get(str(f), 0) + 1
    report["trades_new"] += len(closed_new)
    report["rows_appended"] += len(out_rows)
    report["bars"] += max(0, bisect.bisect_right(ct, F) - bisect.bisect_right(ct, resume))


# ============================================================================ S4x adımı
def explore_root(paths: Any) -> Path:
    return Path(paths.research) / ROOT_NAME / LIB_VERSION


def _btc_view(btc: CoinData | None) -> CoinData | None:
    """Diğer coin'lerin okuduğu BTC dizileri (1d, 4h); BTC'nin 5m/1h'si bellekte tutulmaz."""
    if btc is None or btc.moving:
        return None
    keep = ("1d", "4h")
    return CoinData(coin=btc.coin, bars={k: v for k, v in btc.bars.items() if k in keep},
                    frontier={k: v for k, v in btc.frontier.items() if k in keep}, funding=None, tick=btc.tick)


def explore_step(root: Path | str, data: Mapping[str, CoinData] | Callable[[str], CoinData | None], *,
                 entry_symbols: Sequence[str],
                 deadline: float | None = None, clock: Callable[[], float] = time.monotonic,
                 only: Iterable[str] | None = None) -> dict[str, Any]:
    """S4x: planın bütün çalıştırılabilir serilerini yeni barlara taşı. `data`: {coin: CoinData} ya da coin → CoinData
    yükleyicisi (gece: coin'ler TEK TEK yüklenir ve bırakılır — bellek; BTC'nin 1d/4h'si diğerleri için tutulur; eksik
    coin = veri yok). `deadline` (monotonic saniye): aşılınca kalan gruplar dokunulmadan bırakılır (`DEADLINE`; artımlı tasarım
    yarım geceyi güvenli kılar). `only`: yalnız bu varyantlar (test/ölçüm alt kümesi; gece None). Döner: rapor (ayrıca
    `_run.json`)."""
    t0 = clock()
    root = Path(root)
    refused = None
    ok, why = KN.s1b_status()
    if not ok:                                                          # S1b mührü tutmuyor: hiçbir seri koşmaz
        refused = {"reason": why.split(":")[0], "detail": why, "missing": [], "extra": [], "duplicates": []}
    else:
        try:
            plan = series_plan(entry_symbols)
        except EntryUniverseRefused as exc:                             # fark raporlanır, hiçbir seri koşmaz
            refused = {"reason": "ENTRY_UNIVERSE_DIFF", **exc.diff}
    if refused is not None:
        rep0 = {"schema": "explore_run_v1", "lib": LIB_VERSION, "t_seal_ms": T_SEAL_MS, "status": {"REFUSED": 1},
                "refused": refused, "seconds": round(clock() - t0, 3)}
        _atomic_write(root / "_run.json", (dumps(_roundtrip(rep0)) + "\n").encode("utf-8"))
        return rep0
    rep: dict[str, Any] = {"schema": "explore_run_v1", "lib": LIB_VERSION, "t_seal_ms": T_SEAL_MS,
                           "plan": plan_counts(plan), "status": {}, "rebuilt": 0, "trades_new": 0, "rows_appended": 0,
                           "bars": 0, "errors": [], "moving": {}}
    rep["status"][ST_BLOCKED] = rep["plan"]["blocked"]
    get: Callable[[str], CoinData | None] = data if callable(data) else data.get
    btc_full = get("BTC")
    btc = _btc_view(btc_full)
    keep = set(only) if only is not None else None
    by_coin: dict[str, list[Series]] = {}
    for s in plan:
        if s.group in ("STANDARD", "COPY", "COVER") and (keep is None or s.variant_id in keep):
            by_coin.setdefault(s.instrument, []).append(s)
    stopped = False
    for coin, ss in by_coin.items():
        groups = coin_groups(ss)
        n_series = sum(len(g[1]) for g in groups)
        if stopped or (deadline is not None and clock() > deadline):
            stopped = True
            rep["status"][ST_DEADLINE] = rep["status"].get(ST_DEADLINE, 0) + n_series
            continue
        cd = btc_full if coin == "BTC" else get(coin)
        if coin == "BTC":
            btc_full = None                                             # yalnız 1d/4h görünümü kalır
        if cd is None:
            rep["status"][ST_NO_FORWARD] = rep["status"].get(ST_NO_FORWARD, 0) + n_series
            continue
        if cd.moving:
            rep["moving"][coin] = cd.moving
            rep["status"][ST_DATA_MOVING] = rep["status"].get(ST_DATA_MOVING, 0) + n_series
            continue
        fi = funding_intervals(cd.funding)
        if fi["changes"] or fi["intervals_h"] not in ([], [8]):
            rep.setdefault("funding_intervals", {})[coin] = fi
        for base, members in groups:
            try:
                step_group(root, base, members, cd, btc, rep)
            except Exception as exc:                                    # seri grubu atlanır; gece sürer
                rep["status"][ST_ERROR] = rep["status"].get(ST_ERROR, 0) + len(members)
                rep["errors"].append(f"{coin}/{base or members[0].variant_id}: {type(exc).__name__}: {exc}"[:300])
    rep["seconds"] = round(clock() - t0, 3)
    rep["errors"] = rep["errors"][:50]
    _atomic_write(root / "_run.json", (dumps(_roundtrip(rep)) + "\n").encode("utf-8"))
    return rep


def run_s4x(paths: Any, reader: Any, *, entry_symbols: Sequence[str], ticks: Mapping[str, float], now_ms: int,
            budget_s: float = 600.0) -> dict[str, Any]:
    """Gece birimi girişi (P3g bağlar): mühürlü okuyucudan (`provider.SealedReader.from_status`) coin girdileri, sonra
    `explore_step`. Kök araştırma kökünün içinde olmalı (`EnginePaths.require_research`)."""
    root = Path(paths.require_research(explore_root(paths)))
    deadline = time.monotonic() + float(budget_s)
    def load(coin: str) -> CoinData:
        return load_coin(reader, coin, tick=ticks.get(coin), now_ms=now_ms)
    return explore_step(root, load, entry_symbols=entry_symbols, deadline=deadline)


# ============================================================================ S1d LIB_AG_V1 keşfi (P3g)
def ag_explore_root(paths: Any) -> Path:
    from . import seal_s1d as S1D
    return Path(paths.research) / ROOT_NAME / S1D.LIB_AG_VERSION


def ag_series_plan(ag_symbols: Sequence[str]) -> list[Series]:
    """AG keşif serileri (`lib_ag.series_plan()`: 38 varyant × 41 sembol = 1.558; mühür sırası × AG evren sırası).
    `ag_symbols` AG evreninin kendisi olmalı (`lib_ag.universe_diff(...)["ag_symbols"]`); değilse `ExploreRefused`."""
    from . import lib_ag as LA
    want = LA.universe_symbols()
    if list(ag_symbols) != want:
        raise ExploreRefused(f"AG_UNIVERSE: {len(list(ag_symbols))} sembol, mühür {len(want)}")
    plan = [Series(v, coin_of(sym), str(LA.variant(v)["family"]), "AG") for v, sym in LA.series_plan()]
    if len(plan) != LA.budget()["explore_series"]:
        raise ExploreRefused(f"AG_EXPLORE_SERIES: plan {len(plan)} ≠ {LA.budget()['explore_series']}")
    return plan


def ag_coin_groups(series: Sequence[Series]) -> list[tuple[str, list[Series]]]:
    """Bir sembolün AG serileri → lockstep grupları (`agnight.groups`: aile × sinyal dilimi, örtüler taban başına).
    Örtüler LIB_V1 tabanlarının AYRI kopyalarıdır (paylaşılan akış yok): grup yalnız aynı dilimde birlikte koşar."""
    from . import agnight as AGN
    by = {s.variant_id: s for s in series}
    return [(g, [by[v] for v in vids if v in by]) for g, vids in AGN.groups() if any(v in by for v in vids)]


def explore_step_ag(root: Path | str, data: Mapping[str, CoinData] | Callable[[str], CoinData | None], *,
                    ag_symbols: Sequence[str], deadline: float | None = None, clock: Callable[[], float] = time.monotonic,
                    only: Iterable[str] | None = None) -> dict[str, Any]:
    """S4x'in AG adımı: `explore_step` ile AYNI makine (`step_group`, artımlı = baştan, cephe, fonlama yerleşimi), bağlam
    `ag_ctx()`: ileri karar anı `S1D_SEAL_MS` + dilim; ısınma AG varyantlarının pencereleri; durum dosyalarında `s1d_sha`.
    S1d mührü tutmazsa hiçbir seri koşmaz (`REFUSED`). Dosyalar `explore/LIB_AG_v1/<varyant>/<COIN>.*`."""
    from . import seal_s1d as S1D
    t0 = clock()
    root = Path(root)
    ctx = ag_ctx()
    refused = None
    ok, why = S1D.check()
    if not ok:
        refused = {"reason": "S1D_SEAL", "detail": why}
    else:
        try:
            plan = ag_series_plan(ag_symbols)
        except ExploreRefused as exc:
            refused = {"reason": "AG_UNIVERSE", "detail": str(exc)}
    if refused is not None:
        rep0 = {"schema": "explore_run_v1", "lib": ctx.lib, "t_seal_ms": ctx.t_seal_ms, "status": {"REFUSED": 1},
                "refused": refused, "seconds": round(clock() - t0, 3)}
        _atomic_write(root / "_run.json", (dumps(_roundtrip(rep0)) + "\n").encode("utf-8"))
        return rep0
    rep: dict[str, Any] = {"schema": "explore_run_v1", "lib": ctx.lib, "t_seal_ms": ctx.t_seal_ms,
                           "plan": {"total": len(plan), "run": len(plan), "blocked": 0, "AG": len(plan)}, "status": {},
                           "rebuilt": 0, "trades_new": 0, "rows_appended": 0, "bars": 0, "errors": [], "moving": {}}
    get: Callable[[str], CoinData | None] = data if callable(data) else data.get
    keep = set(only) if only is not None else None
    by_coin: dict[str, list[Series]] = {}
    for s in plan:
        if keep is None or s.variant_id in keep:
            by_coin.setdefault(s.instrument, []).append(s)
    need_btc = any(meta(s.variant_id).needs_btc or meta(s.variant_id).needs_btc_4h for ss in by_coin.values() for s in ss)
    btc_full = get("BTC") if need_btc or "BTC" in by_coin else None
    btc = _btc_view(btc_full)
    stopped = False
    for coin, ss in by_coin.items():
        groups = ag_coin_groups(ss)
        n_series = sum(len(g[1]) for g in groups)
        if stopped or (deadline is not None and clock() > deadline):
            stopped = True
            rep["status"][ST_DEADLINE] = rep["status"].get(ST_DEADLINE, 0) + n_series
            continue
        cd = btc_full if coin == "BTC" else get(coin)
        if coin == "BTC":
            btc_full = None
        if cd is None:
            rep["status"][ST_NO_FORWARD] = rep["status"].get(ST_NO_FORWARD, 0) + n_series
            continue
        if cd.moving:
            rep["moving"][coin] = cd.moving
            rep["status"][ST_DATA_MOVING] = rep["status"].get(ST_DATA_MOVING, 0) + n_series
            continue
        for g, members in groups:
            try:
                step_group(root, None, members, cd, btc, rep, ctx)
            except Exception as exc:                                    # seri grubu atlanır; gece sürer
                rep["status"][ST_ERROR] = rep["status"].get(ST_ERROR, 0) + len(members)
                rep["errors"].append(f"{coin}/{g}: {type(exc).__name__}: {exc}"[:300])
        del cd
    rep["seconds"] = round(clock() - t0, 3)
    rep["errors"] = rep["errors"][:50]
    _atomic_write(root / "_run.json", (dumps(_roundtrip(rep)) + "\n").encode("utf-8"))
    return rep


def run_s4x_ag(paths: Any, reader: Any, *, ag_symbols: Sequence[str], ticks: Mapping[str, float], now_ms: int,
               budget_s: float = 600.0) -> dict[str, Any]:
    """Gece birimi girişi (AG): `run_s4x`'in eşi; semboller TEK TEK yüklenir (XAUUSDT listelenmeden önce geçmişsizdir —
    hata değil: ileri veri gelene kadar `NO_FORWARD_DATA`)."""
    root = Path(paths.require_research(ag_explore_root(paths)))
    deadline = time.monotonic() + float(budget_s)
    ctx = ag_ctx()

    def load(coin: str) -> CoinData:
        return load_coin(reader, coin, tick=ticks.get(coin), now_ms=now_ms, t_seal_ms=ctx.t_seal_ms, ctx=ctx)
    return explore_step_ag(root, load, ag_symbols=ag_symbols, deadline=deadline)


# ============================================================================ okuma (salt-okunur)
def read_states(root: Path | str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(Path(root).glob("*/*.state.json")):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and st.get("schema") == STATE_SCHEMA and isinstance(st.get("summary"), dict):
            out[str(st.get("series"))] = st
    return out


def read_run(root: Path | str) -> dict[str, Any] | None:
    try:
        with open(Path(root) / "_run.json", "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _row_members(row: Mapping[str, Any], own_symbols: Mapping[str, Sequence[str]] | None,
                 coin_fit_selection: Mapping[str, str] | None) -> list[str] | None:
    """§3.7 (6): satırın KENDİ ileri serileri; çözülemezse None (kapı `FWD_MISSING` ile kapalı kalır)."""
    rt, unit, scope = row["row_type"], row["unit"], str(row["scope"])
    if rt == "OTHER":
        return None
    if rt == "AG":                                   # S1d: AG_POOLED = 41 sembolün birleşimi (keşif kökü LIB_AG_v1)
        from . import lib_ag as LA
        return [f"{unit}@{coin_of(sym)}" for sym in LA.universe_symbols()]
    if rt == "COIN_FIT":
        sel = (coin_fit_selection or {}).get(row["row_id"])
        if not sel:
            return None
        if RG.variants_by_id().get(sel, {}).get("family") != unit:
            raise ExploreRefused(f"{row['row_id']}: donmuş seçim {sel} ailede değil")
        return [f"{sel}@{scope.split(':', 1)[1]}"]
    if scope.startswith("COIN:"):
        return [f"{unit}@{scope.split(':', 1)[1]}"]
    if scope == "POOLED_P23":
        coins: Sequence[str] = RG.LIB_V1["p23"]["symbols"]
    elif scope in RG.CLUSTERS:
        coins = RG.CLUSTERS[scope]
    elif scope == "POOLED_OWN":
        v = RG.variants_by_id()[unit]
        base = v["cover"]["base"] if v["family"] == "O" else unit[2:]
        base = "C4" if base.startswith("C4_") else base
        coins = (own_symbols or {}).get(base) or ()
        if not coins:
            return None
    else:
        return None
    return [f"{unit}@{coin_of(c)}" for c in coins]


def fwd_reads(root: Path | str, rows: Sequence[Mapping[str, Any]] | None = None, *,
              own_symbols: Mapping[str, Sequence[str]] | None = None,
              coin_fit_selection: Mapping[str, str] | None = None,
              states: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Kapı A'nın mühür sonrası ileri okuması (§3.7 (6)) — keşfin kapıya giden TEK yolu. Satır başına: üyelerin birleşik
    KAPI-İÇİ kapanmış işlemleri (`kernel.gate_excluded` çıkar): `n`, `mean_adverse`, `days` (üyelerin mühürden bu yana en
    az günü), `intraday` (5m/15m). Bir üyenin durumu yoksa satır döndürülmez (kapı `FWD_MISSING`)."""
    rows = list(rows) if rows is not None else RG.gate_a_rows()
    sts = dict(states) if states is not None else read_states(root)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        mem = _row_members(row, own_symbols, coin_fit_selection)
        if not mem:
            continue
        ss = [sts.get(sid) for sid in sorted(mem)]
        if any(st is None or st.get("status") != ST_OK for st in ss):
            continue
        n = sum(int(st["summary"]["n"]) for st in ss)
        tot = 0.0
        for st in ss:
            tot += float(st["summary"]["sum_r"][_GI])
        out[row["row_id"]] = {"source": SOURCE, "members": len(ss), "n": n,
                              "mean_adverse": (tot / n) if n else None,
                              "days": min(int(st["summary"]["days_since_seal"]) for st in ss),
                              "intraday": any(bool(st["summary"]["intraday"]) for st in ss),
                              "excluded": sum(int(st["summary"]["n_excluded"]) for st in ss),
                              "frontier_ms": min(int(st["summary"]["frontier_ms"]) for st in ss)}
    return out


# ============================================================================ metin (betimsel; hüküm kelimesi yok)
#: hüküm kelimeleri (Türkçe küçük harf; sözcük sınırıyla): keşif metninde hiç geçmez (ana belge §6.8)
VERDICT_WORDS: tuple[str, ...] = (
    "geçti", "geçmedi", "geçer", "geçemez", "kazandı", "kaybetti", "onaylandı", "onaylı", "onay", "kanıtlandı",
    "kanıtlanmış", "tuttu", "tutmadı", "güçlü aday", "zayıf iz", "kanıt yok", "kaybettirir", "veri az", "başarılı",
    "başarısız", "hedef günü", "kazanan", "kaybeden", "terfi etti", "pass", "fail", "pass_a", "fail_a", "proven",
    "validated", "winner")
_TR_LOWER = str.maketrans({"I": "ı", "İ": "i"})
_WORD = r"[0-9a-zçğıöşüâîû_]"
_VERDICT_RE = re.compile("|".join(rf"(?<!{_WORD}){re.escape(w)}(?!{_WORD})" for w in VERDICT_WORDS))


def tr_lower(s: str) -> str:
    return str(s).translate(_TR_LOWER).lower()


def verdict_word_violations(lines: Iterable[str]) -> list[str]:
    """Hüküm kelimesi taşıyan satırlar (Türkçe küçük harf — I → ı, İ → i — ve Latin küçük harf ikisi de denenir)."""
    return [ln for ln in lines if _VERDICT_RE.search(tr_lower(ln)) or _VERDICT_RE.search(str(ln).lower())]


def _checked(lines: list[str]) -> list[str]:
    bad = verdict_word_violations(lines)
    if bad:
        raise ValueError(f"keşif metninde hüküm kelimesi: {bad[:2]}")
    return lines


def _n(x: int) -> str:
    return f"{int(x):,}".replace(",", ".")


def _f(x: Any, nd: int = 2, sign: bool = True) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s.replace(".", ",").replace("-", "−")


def _pctf(x: Any) -> str:
    return "—" if x is None else "%" + _f(float(x) * 100.0, 0, sign=False)


def _when(ms: int | None) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _result(st: Mapping[str, Any]) -> float:
    return float(st["summary"]["sum_r"][_GI])


def _series_line(st: Mapping[str, Any], label: str | None = None) -> str:
    sm = st["summary"]
    mr = sm.get("mean_r") or [None, None, None]
    share = sm.get("share_days_ge_1pct") or [None, None, None]
    op = " · açık pozisyon" if sm.get("open") else ""
    ex = f" · kapı dışı {sm['n_excluded']}" if sm.get("n_excluded") else ""
    return (f"{label or st['series']} · n {sm['n']} · ort. R {_f(mr[0])} / {_f(mr[1])} / {_f(mr[2])} · toplam "
            f"{_f(sm['sum_r'][_GI])}R · P(gün ≥ +%1) {_pctf(share[_GI])} · en büyük düşüş %"
            f"{_f(sm['max_dd_pct'][_GI], 2, sign=False)} · {sm['days_since_seal']} gün{ex}{op}")


def _median(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = len(s) // 2
    return s[k] if len(s) % 2 else (s[k - 1] + s[k]) / 2.0


TOP_TITLE = "{n} seri arasından en iyi 5 — seçim yanlılığı, kanıt değil"
COST_NOTE = "R sırası base / ADVERSE / STRESS; toplam, P(gün ≥ +%1) ve düşüş ADVERSE (%0,5 risk, 200 USDT taban)"


def _crypto(states: Mapping[str, Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [st for st in states.values() if st.get("family") != "G"]


def digest_lines(states: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Özetin keşif satırları (ana belge §6.8: yalnız ileri verili seri sayısı, medyan serinin sonucu ve en iyi 5)."""
    total = RG.explore_series()
    cs = [st for st in _crypto(states) if st["summary"]["n"] > 0]
    med = _median([_result(st) for st in cs])
    top = sorted(cs, key=lambda st: (-_result(st), st["series"]))[:TOP_K]
    lines = [f"Keşif (ileri, mühür sonrası; kayıt-yalnız): ileri verili seri {_n(len(cs))} / {_n(total)} · medyan serinin "
             f"sonucu (ADVERSE toplam R) {_f(med)}",
             TOP_TITLE.format(n=_n(total)) + ":"]
    lines += [f"  {i}. {_series_line(st)}" for i, st in enumerate(top, 1)] or ["  (henüz kapanmış ileri işlem yok)"]
    return _checked(lines)


def leaderboard_lines(states: Mapping[str, Mapping[str, Any]], run: Mapping[str, Any] | None = None) -> list[str]:
    """Lider tablosunun KEŞİF bölümü (ana belge §6.5/§6.8): betimsel; sıralama ileri ADVERSE toplam R'yedir ve "seçim
    yanlılığı, kanıt değil" başlığı altındadır; altın ayrı."""
    total = RG.explore_series()
    bb = RG.blocked_budget()
    fc = RG.family_counts()
    nu = UNIVERSE_SIZE
    std = sum(fc.get(f, 0) for f in RG.LIB_V1["family_groups"]["STANDARD"])
    blocked_series = 0
    for vid in RG.blocked_variants():
        fam = RG.variants_by_id()[vid]
        if fam.get("alias_of"):
            continue
        blocked_series += (nu if _group_of(fam["family"]) != "OTHER" else
                           len(GOLD_INSTRUMENTS) if fam["family"] == "G" else 1)
    cs = _crypto(states)
    with_fwd = [st for st in cs if st["summary"]["n"] > 0]
    excl = sum(int(st["summary"]["n_excluded"]) for st in cs)
    days = [int(st["summary"]["days_since_seal"]) for st in cs]
    lines = ["KEŞİF — mühür sonrası ileri seriler (EXPLORATION_FWD; kayıt-yalnız: terfi ettirmez, canlı toplama ve Kapı A/B "
             "değerlendirmesine girmez; Kapı A yalnız satırın kendi ileri birleşimini okur)",
             f"Seri planı {_n(total)} = {std} × {nu} + {fc['K']} × {nu} + {fc['O']} × {nu} + {fc['J']} + {fc['I']} + "
             f"{fc['G']} × {len(GOLD_INSTRUMENTS)} · çalıştırılmayan (UNSPECIFIED) {_n(blocked_series)} seri "
             f"({bb['variants']} varyant) · durum dosyası {_n(len(states))}",
             f"İleri verili seri {_n(len(with_fwd))} · mühürden bu yana gün {min(days) if days else 0}–"
             f"{max(days) if days else 0} · kapı dışı işlem {_n(excl)} (FUNDING_GAP / PATH_1H_GAP / F8_UNMEASURABLE; "
             "istatistiklere girmez)"]
    if run and run.get("refused"):
        rf = run["refused"]
        if rf.get("reason") == "ENTRY_UNIVERSE_DIFF":
            lines.append(f"Son adım: ÇALIŞMADI — giriş evreni dondurulmuş 40 coin'den (S1b) farklı: eksik "
                         f"{', '.join(rf['missing']) or '—'} · fazla {', '.join(rf['extra']) or '—'} · yinelenen "
                         f"{', '.join(rf['duplicates']) or '—'}")
        else:
            lines.append(f"Son adım: ÇALIŞMADI — S1b mührü: {rf.get('detail') or rf.get('reason')}")
    elif run:
        stt = run.get("status") or {}
        lines.append("Son adım: " + " · ".join(f"{k} {_n(v)}" for k, v in sorted(stt.items())) +
                     f" · yeniden kurulan {_n(run.get('rebuilt', 0))} · {_f(run.get('seconds'), 1, sign=False)} sn")
    med = _median([_result(st) for st in with_fwd])
    lines.append(f"Medyan serinin sonucu (ADVERSE toplam R): {_f(med)} · {COST_NOTE}")
    lines.append(TOP_TITLE.format(n=_n(total)) + ":")
    top = sorted(with_fwd, key=lambda st: (-_result(st), st["series"]))[:TOP_K]
    lines += [f"  {i}. {_series_line(st)}" for i, st in enumerate(top, 1)] or ["  (henüz kapanmış ileri işlem yok)"]
    gold = [st for st in states.values() if st.get("family") == "G"]
    n_gold = fc["G"] * len(GOLD_INSTRUMENTS)
    lines.append(f"Altın (ayrı): {n_gold} seri ({', '.join(GOLD_INSTRUMENTS)}); durum dosyası {len(gold)} — G ailesi "
                 "UNSPECIFIED (stop) yüzünden çalıştırılmıyor")
    return _checked(lines)


def _bound(lines: list[str]) -> list[str]:
    note = f"… (sınır: {QUERY_MAX_LINES} satır / {QUERY_MAX_BYTES // 1024} KB; daha dar bir sorgu: --variant <id>)"
    out = list(lines)
    if len(out) > QUERY_MAX_LINES:
        out = out[:QUERY_MAX_LINES - 1] + [note]
    while len("\n".join(out).encode("utf-8")) > QUERY_MAX_BYTES and len(out) > 2:
        out = out[:-2] + [note]
    return out


def query_lines(paths: Any, *, gold: bool = False, variant: str | None = None,
                root: Path | str | None = None) -> list[str]:
    """`engine-query explore [--gold] [--variant <id>]` — salt-okunur (kilit almaz, hiçbir şey yazmaz); ≤ 150 satır /
    ≤ 4 KB; hüküm kelimesi yok."""
    root = Path(root) if root is not None else explore_root(paths)
    states = read_states(root)
    run = read_run(root)
    if variant:
        vs = RG.variants_by_id()
        if variant not in vs:
            return _bound(_checked([f"bilinmeyen varyant: {variant} (engine-query trials --like {variant})"]))
        v = vs[variant]
        blocked = RG.blocked_variants().get(variant)
        mine = sorted((st for st in states.values() if st.get("variant_id") == variant), key=lambda st: st["instrument"])
        lines = [f"KEŞİF — {variant} ({v['family']}; {','.join(v['timeframes'])}) · ileri seriler (kayıt-yalnız)", COST_NOTE]
        if v.get("alias_of"):
            lines.append(f"{variant} ≡ {v['alias_of']} (tekilleşir; keşif serisi {v['alias_of']}'ın)")
        if blocked:
            lines.append(f"UNSPECIFIED_PARAM — çalıştırılmıyor ({len(blocked)} boşluk: {', '.join(blocked[:3])})")
        lines.append(f"Durum dosyası {len(mine)}")
        lines += [f"  {_series_line(st, st['instrument'])} · cephe {_when(st['summary']['frontier_ms'])}" for st in mine]
        return _bound(_checked(lines))
    if gold:
        gs = sorted((st for st in states.values() if st.get("family") == "G"), key=lambda st: st["series"])
        lines = [f"KEŞİF — altın serileri ({', '.join(GOLD_INSTRUMENTS)}; ayrı gösterim, kayıt-yalnız)"]
        for vv in RG.LIB_V1["variants"]:
            if vv["family"] != "G":
                continue
            gaps = RG.blocked_variants().get(vv["variant_id"])
            lines.append(f"  {vv['variant_id']} × {len(GOLD_INSTRUMENTS)} · "
                         + (f"UNSPECIFIED_PARAM ({len(gaps)} boşluk) — çalıştırılmıyor" if gaps else "çalıştırılır"))
        lines += [f"  {_series_line(st)}" for st in gs]
        return _bound(_checked(lines))
    return _bound(_checked(leaderboard_lines(states, run)))


__all__ = ["CoinData", "DAY_MS", "EntryUniverseRefused", "MARKPX", "EXPLORE_VERSION", "ExploreRefused", "GOLD_INSTRUMENTS", "LIB_VERSION", "Meta",
           "ROOT_NAME", "SETTLE_ROWS", "SOURCE", "STATE_SCHEMA", "ST_BLOCKED", "ST_DATA_MOVING", "ST_DEADLINE",
           "ST_ERROR", "ST_NO_FORWARD", "ST_NO_TICK", "ST_OK", "ST_UNCHANGED", "Series", "SeriesFiles", "T_SEAL_MS",
           "funding_intervals",
           "TOP_TITLE", "UNIVERSE_SIZE", "VERDICT_WORDS", "coin_groups", "coin_of", "digest_lines", "dumps",
           "explore_root", "explore_step", "funding_warm_start", "fwd_reads", "inputs_from_rows", "leaderboard_lines",
           "load_coin", "meta", "plan_counts", "query_lines", "read_run", "read_states", "run_s4x", "runnable_variants",
           "series_plan", "step_group", "tr_lower", "verdict_word_violations", "warm_bars", "warm_start",
           "LIB_CTX", "LibCtx", "ag_coin_groups", "ag_ctx", "ag_explore_root", "ag_runnable_variants", "ag_series_plan",
           "explore_step_ag", "is_ag_variant", "make_any", "run_s4x_ag"]
