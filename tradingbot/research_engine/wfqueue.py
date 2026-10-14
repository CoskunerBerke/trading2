"""P3g — S4w walk-forward kuyruğu (docs/ENGINE_BRAIN_V1.md §5.2, §5.3; mühür `WF_PROTOCOL_V1`; P3d `wf.py`). Yalnız
PAPER araştırma kodu: ağsız, kayıt-yalnız; deneme defterine yazmaz, terfi ettirmez, hüküm üretmez (Kapı A satırlarının
kurulması ve bakışı bu adımda YOKTUR — §8.4 P3g notu).

* **Birim (kuyruğun iş parçası):** giriş evreninin (S1b `ENTRY_UNIVERSE_V1`, 40 coin) her coin'i × koşu grubu. Kopya-örtü
  aileleri tek birimdir (`wf.run_cover_family`: paylaşılan akış); diğer çalıştırılabilir varyantlar (standart aileler,
  C4 varyasyon kopyaları, Formasyon) aile × sinyal dilimi başına tek birimdir (`wf.run_variants`, `kernel.run_lockstep`).
  Boşluklu (UNSPECIFIED) varyantlar birim değildir (keşifle aynı plan: `explore.series_plan`).
* **Pencere (veri ay mührü):** birim serinin bütün geçmişini (yalnız arşivle doğrulanmış, kapanmış barlar; WF veri başı
  `wf.data_start_ms`) TAM UTC aylarına kadar okur: hedef ay = (gece − `ARCHIVE_LAG_DAYS`) anının ayından önceki ay
  (arşivin gecikmesi). Bir ayın kurulumu başladıktan sonra BİTENE kadar sürer (yeni ay beklemede kalır); biten kurulum
  "tam" olur ve önceki ayın önbelleği silinir. Kapı A yalnız tam kurulumu okur (P4a); bitmemiş satır `HESAPLANMADI`.
* **Çıktı:** `backtests/cache/<ay>/<birim>.jsonl.gz` — başlık (`H`: birim, ay, pencere, varyantların `spec` kimliği,
  veri parçalarının mühür özeti), işlem satırları (`T`: `wf.trade_rows`, günlük piyasa değerlemesi dahil) ve kuralın
  sinyal anları (`S`: plasebo dışlaması için). Belirlenimci (aynı mühür + aynı kod → aynı baytlar).
* **Kontrol noktası ve sürdürme:** `backtests/queue.json` her birimden ÖNCE (`in_progress`) ve SONRA (biten birim, süre,
  içerik sha'sı) atomik yazılır; kesilen gece (son tarih, `MEMORY_GUARD`, sinyal, OOM) kaldığı birimden sürer. Önceki
  gece bir birimin ORTASINDA ölmüşse (`in_progress` kalmış) sayaç artar; `MAX_KILLS` kez ölen birim
  `KILLED_SKIPPED` olur (kuyruk tıkanmaz, raporlanır). Hata atan birim `MAX_UNIT_ERRORS` denemeden sonra `ERROR`.
* **Birim ortası bekçi (P3g ölçümü: gerçek uzunlukta 5m Box birimi tek başına bir gecenin S4w payı mertebesinde):**
  ana iş parçacığında `SIGALRM` ile her `WATCH_EVERY_S`'de son tarih ve `VmRSS` (ucuz; eşik aşılırsa `guard()` gc ile
  doğrular) birimin ORTASINDA da denetlenir — birimin kendi yüklemesi dahil; aşılırsa birim bırakılır (`UnitAborted`,
  `BaseException`: çekirdeğin `except Exception`'ı yutmaz; yazım birimden sonra olduğundan yarım dosya yok), kontrol
  noktası yazılır. Son tarihte de bellekte de S4w o gece durur (S6/S7/S7b'nin payı korunur; gc sonrası RSS çoğu zaman yüksek
  kalır, sonraki birimler sahte "büyük" sayılmasın); kuyruk ertesi gece buradan sürer. Birim, payın en az yarısını kullanıp son tarihe takıldıysa ya da
  bellek korumasına takıldıysa sayılır — YALNIZ o birim; `MAX_ABORTS` kezde `TOO_LONG_SKIPPED` / `TOO_BIG_SKIPPED` olur
  (kuyruk tıkanmaz, raporlanır; 5m parçalama sonraki sürüm).
* **Haftalık doğrulama (§5.2):** kurulum tamken ve süre kalırsa, birim adının sha'sına göre günün 1/7'si sıfırdan
  hesaplanır ve içerik sha'sıyla karşılaştırılır; fark → `WF_CACHE_MISMATCH`, birim yeniden yazılır.
* **Bellek (§5.3):** coin'ler TEK TEK yüklenir ve bırakılır; bir birim YALNIZ kendi dizilerini yükler (sonraki birim
  eksik diziyi ekler), büyük diziler (15m/5m) yalnız şimdiki birim istedikçe, küçükler coin'in kalan birimlerinden biri
  istedikçe tutulur. Yüklemeden önce tahmin (`BAR_BYTES`, `RUN_BYTES_PER_BAR`: listeden bar sayısı) + şimdiki RSS eşiği
  aşarsa birim yüklenmez (yalnız o birime sayılır, kuyruk sürer). Her birimden önce (gereksiz diziler bırakıldıktan
  sonra) `guard()` (gece: `MEMORY_GUARD`, RSS > 0,7 × MemoryMax → kontrol noktası yazılmış olarak durur).
* **Süre:** birim türü başına ölçülen ortalama süreden tahmin; tahmin kalan payı aşıyorsa birim başlatılmaz.

* **S1d LIB_AG_V1 (P3g; `agnight`):** AG birimleri AYNI kuyruktadır (aynı pay, kontrol noktası, bekçi ve
  `MEMORY_GUARD`): sembol × koşu grubu (`agnight.groups`, 14) — 40 coin'de o coin'in LIB birimlerinden hemen sonra (coin
  geçmişi bir kez yüklenir), XAUUSDT en sonda (listelenmeden önceki geçmiş yoktur: `NO_DATA`, hata değil). Çekirdek
  `lib_ag.run_variants`, satırlar `lib_ag.trade_rows` (AG sinyal dilimiyle günlük MTM). Planın son birimi
  `AG_EVAL@AG_POOLED`: bütün AG sembol birimleri bitince varyant başına `lib_ag.evaluate(vid, satırlar,
  lib_ag.plan_for([vid], başlangıç, bitiş))` (41 sembolün birleşimi; başlangıç = birimlerin en erken veri başı) — kompakt
  WF özeti + `lib_ag.cal_flag` (Kapı A girdisi değil: satır kurucuları P4a). Gece AG kapalıysa (S1d mührü tutmuyor ya da
  deneme satırları deftere eklenemedi) AG birimleri o gece KOŞMAZ, planda bekler (kurulum tamamlanmaz; LIB birimleri sürer).

Rapor: `WF_BACKLOG` done/total ve ETA (gece) — özet ve `engine-status` için.
"""
from __future__ import annotations

import gc
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import explore as EX
from . import kernel as KN
from . import registry as RG
from . import seal_s1d as S1D
from . import universe as U
from . import wf as WF

VERSION = "wfq_v1"
QUEUE_SCHEMA = "wf_queue_v1"
CACHE_SCHEMA = "wf_cache_v1"
ROOT_NAME = "backtests"
DAY_MS = 86_400_000
#: arşiv (aylık/günlük zip) gecikmesi payı: hedef ay, gece − 3 günün ayından önceki aydır
ARCHIVE_LAG_DAYS = 3
VERIFY_ROTATION = 7
MAX_UNIT_ERRORS = 2
MAX_KILLS = 2
#: tahmin yokken birim süresi varsayımı (sn) — ilk ölçümden sonra ortalama kullanılır
DEFAULT_UNIT_S = 30.0
#: kuyruk dosyasının en seyrek yazım aralığı (sn); bundan uzun sürmesi beklenen birimden önce her zaman yazılır
SAVE_EVERY_S = 5.0
#: bellek koruması en seyrek (coin değişiminde her zaman)
GUARD_EVERY_S = 5.0
#: birim ortası bekçinin aralığı (sn; `SIGALRM`). Her atışta `VmRSS` okunur (ucuz, `gc.collect()` yok); eşik aşılırsa
#: `guard()` (gc + RSS) aşımı doğrular.
WATCH_EVERY_S = 1.0
#: Yükleme öncesi bellek tahmini (P3g inceleme düzeltmesi; 5m parçalama sonraki sürüm). Bir bar listede sözlük olarak
#: ≈ `BAR_BYTES` tutar (ölçüm: 1 yıllık 5m BTC, taze süreç, pandas ara nesneleri dahil 550–605 B); çekirdeğin çalışma kümesi
#: birimin yüklü barı başına ≈ `RUN_BYTES_PER_BAR[tür]` (taze süreçte birim başına ölçüldü, paylı: ör. FAM_BOX 951 → 1100,
#: AG_P2.5m 135 → 200 B; listede olmayan tür `DEFAULT_RUN_BYTES_PER_BAR`). Tahmin + şimdiki RSS eşiği aşarsa birim
#: YÜKLENMEDEN bırakılır. S4w başındaki RSS + tahmin de eşiği aşıyorsa (taze gecede de sığmıyor) sayım yalnız o birime
#: yazılır (MAX_ABORTS kezde `TOO_BIG_SKIPPED`) ve kuyruk sürer; aşmıyorsa (önceki birimlerin bıraktığı bellek) sayılmaz ve
#: S4w o gece durur. Tahminin kaçırdığını birim ortası bekçi yakalar.
BAR_BYTES = 600
DEFAULT_RUN_BYTES_PER_BAR = 1000
RUN_BYTES_PER_BAR: dict[str, int] = {"FAM_BOX": 1100, "F2.5m": 780, "AGO_BOX": 450, "AG_P1.5m": 200, "AG_P2.5m": 200,
                                      "AG_P3.5m": 200, "AG_P4.5m": 200, "AG_P5.5m": 800, "AG_P3.15m": 350}
#: büyük diziler: yalnız şimdiki birim istiyorsa bellekte tutulur (sonraki birim isterse yeniden yüklenir); küçükler
#: (≥ 1h) coin'in kalan birimlerinden biri istedikçe tutulur
LARGE_TFS = ("15m", "5m")
#: birim ortasında bırakılan birim bu kadar kez sayılınca atlanır
MAX_ABORTS = 2
#: son tarihe takılan birim, gecenin S4w nominal payının en az bu kesrini kullandıysa sayılır (geç başlayan birim sayılmaz)
ABORT_MIN_FRACTION = 0.5
ST_DONE, ST_ERROR, ST_NO_DATA, ST_KILLED, ST_NO_TICK = "DONE", "ERROR", "NO_DATA", "KILLED_SKIPPED", "NO_TICK"
ST_TOO_LONG, ST_TOO_BIG = "TOO_LONG_SKIPPED", "TOO_BIG_SKIPPED"
#: AG değerlendirmesi: pencere üç yollu WF katmanlarına yetmiyor (hata değil; satır yine yazılır)
ST_WF_SHORT = "WF_WINDOW_TOO_SHORT"
SKIPPED_STATES = (ST_ERROR, ST_KILLED, ST_TOO_LONG, ST_TOO_BIG)
STOP_DONE, STOP_DEADLINE, STOP_GUARD, STOP_ESTIMATE = "DONE", "DEADLINE", "MEMORY_GUARD", "ESTIMATE_EXCEEDS_BUDGET"
STOP_MOVING = "DATA_MOVING"
TF_ORDER = {"1d": 0, "4h": 1, "1h": 2, "15m": 3, "5m": 4}


class UnitAborted(BaseException):
    """Birim ortasında bırakma (son tarih ya da bellek koruması). `BaseException`: çekirdekteki `except Exception`
    yakalamaz."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


class _Watch:
    """Birim ortası bekçi: ana iş parçacığında `SIGALRM` (`setitimer`) ile her `every_s`'de `check()`; bir neden
    dönerse `UnitAborted` atar. Neden bir kez saptanınca (`why`) gövde bitene kadar HER atışta yeniden atılır: sinyal bir
    sonlandırıcının (`__del__`; ör. `gc.collect()` altında `GzipFile`) içine düşerse Python istisnayı yutar ("Exception
    ignored in …") — tek atışlı bekçi o zaman bir daha atmaz ve birim son tarihe/bellek korumasına rağmen sürerdi (P3g tam
    test koşusunda, yük altında yakalandı). Ana iş parçacığı değilse ya da platform desteklemiyorsa etkisizdir. Gövde
    bitince `done()` çağrılır; `__exit__` önce işleyiciyi susturur, sonra zamanlayıcıyı kapatır: ondan sonra işleyici hiç
    atmaz, `except UnitAborted` içindeki temizlik kesilemez. Kurulu bekçi `_Watch.current`'tadır: `__exit__` hiç
    çağrılmadan kaçan bir `UnitAborted`'tan sonra `disarm_watch()` zamanlayıcıyı kapatır (gecenin yedeği)."""

    current: "_Watch | None" = None

    def __init__(self, check: Callable[[], str | None], every_s: float) -> None:
        self.check, self.every_s = check, float(every_s)
        self.on, self.armed, self.prev = False, False, None
        self.why: str | None = None
        self.fired = 0

    def __enter__(self) -> "_Watch":
        import signal
        import threading
        if self.every_s > 0 and hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread():
            self.prev = signal.signal(signal.SIGALRM, self._handler)
            self.armed = self.on = True
            _Watch.current = self
            signal.setitimer(signal.ITIMER_REAL, self.every_s, self.every_s)
        return self

    def _handler(self, signum: int, frame: Any) -> None:
        if not self.on:
            return
        if frame is not None and frame.f_code in _WATCH_QUIET:
            return                                       # `__exit__`/`done` `on=False`'tan önce: atış kaçmasın (yarış)
        if self.why is None:
            try:
                self.why = self.check() or None
            except Exception:  # noqa: BLE001 — bekçi denetimi arızası birimi bırakmaz
                self.why = None
        if self.why and self.on:
            self.fired += 1
            raise UnitAborted(self.why)

    def done(self) -> None:
        self.on = False

    def __exit__(self, *exc: Any) -> bool:
        self.on = False
        if self.armed:
            import signal
            signal.setitimer(signal.ITIMER_REAL, 0, 0)
            signal.signal(signal.SIGALRM, self.prev if self.prev is not None else signal.SIG_DFL)
            self.armed = False
        if _Watch.current is self:
            _Watch.current = None
        return False


def disarm_watch() -> bool:
    """Gecenin yedeği: S4w'nin dışına kaçan bir `UnitAborted`'tan sonra hâlâ kurulu bekçi varsa susturur ve zamanlayıcıyı
    kapatır (yoksa S6/S7/S7b'de yeniden atardı). Kapattıysa True."""
    w = _Watch.current
    if w is None or not w.armed:
        return False
    w.__exit__(None, None, None)
    return True


#: işleyicinin hiç atmadığı çerçeveler: `__exit__` ilk satırı (`on=False`) çalışmadan önce gelen bir atış `UnitAborted`'ı
#: `with` bloğunun DIŞINA taşırdı (gövde bitmiş, `except UnitAborted` geride kalmış → gece S6/S7/S7b'siz düşerdi)
_WATCH_QUIET = (_Watch.__exit__.__code__, _Watch.done.__code__)


LIB_UNIT, AG_UNIT, AG_EVAL = "LIB", "AG", "AG_EVAL"


@dataclass(frozen=True)
class Unit:
    key: str
    coin: str
    base: str | None
    variants: tuple[str, ...]
    signal_tf: str
    lib: str = LIB_UNIT                      # LIB | AG | AG_EVAL (P3g, S1d)

    @property
    def kind(self) -> str:
        """Süre tahmini anahtarı (coin'den bağımsız)."""
        return self.key.split("@", 1)[0]


# ============================================================================ plan
def target_month(now_ms: int) -> tuple[str, int]:
    """(ay etiketi "AAAA-AA", pencere sonu = ayın bitişi ms). Ay, (gece − ARCHIVE_LAG_DAYS) anının ayından önceki aydır."""
    t = datetime.fromtimestamp((int(now_ms) - ARCHIVE_LAG_DAYS * DAY_MS) / 1000, tz=timezone.utc)
    end = datetime(t.year, t.month, 1, tzinfo=timezone.utc)
    prev = datetime(end.year - (end.month == 1), 12 if end.month == 1 else end.month - 1, 1, tzinfo=timezone.utc)
    return prev.strftime("%Y-%m"), int(end.timestamp() * 1000)


def ag_units(coin: str) -> list[Unit]:
    """Bir sembolün AG birimleri (`agnight.groups`; dilim büyükten küçüğe)."""
    from . import agnight as AGN
    from . import lib_ag as LA
    units = [Unit(f"{g}@{coin}", coin, None, vids, str(LA.variant(vids[0])["signal_tf"]), AG_UNIT) for g, vids in AGN.groups()]
    units.sort(key=lambda u: (TF_ORDER.get(u.signal_tf, 9), u.key))
    return units


def ag_eval_unit() -> Unit:
    from . import agnight as AGN
    from . import lib_ag as LA
    return Unit(AGN.EVAL_UNIT, AGN.SCOPE, None, tuple(LA.variant_ids()), "1d", AG_EVAL)


def build_units(entry_symbols: Sequence[str], ag_symbols: Sequence[str] | None = None) -> list[Unit]:
    """Birim planı (belirlenimci): coin'ler dondurulmuş evren sırasıyla; coin içinde sinyal dilimi büyükten küçüğe (5m
    birimleri coin'in sonunda — 5m dizisi yalnız onlar için yüklenir). Evren tutmazsa `explore.EntryUniverseRefused`.
    `ag_symbols` (P3g): AG evreni (`lib_ag.universe_diff(...)["ag_symbols"]`) → coin'in LIB birimlerinden sonra AG birimleri,
    evren dışı AG sembolleri (XAUUSDT) en sonda, en son `AG_EVAL@AG_POOLED`."""
    ag_coins = [U.coin_of(s) for s in ag_symbols] if ag_symbols else []
    plan = EX.series_plan(entry_symbols)
    by_coin: dict[str, list[EX.Series]] = {}
    for s in plan:
        if s.group in ("STANDARD", "COPY", "COVER") and not s.blocked:
            by_coin.setdefault(s.instrument, []).append(s)
    out: list[Unit] = []
    for coin in U.ENTRY_UNIVERSE_FROZEN:
        units: list[Unit] = []
        singles: dict[tuple[str, str], list[str]] = {}
        for base, members in EX.coin_groups(by_coin.get(coin, [])):
            if base is not None:
                ids = tuple(s.variant_id for s in members)
                units.append(Unit(f"FAM_{base}@{coin}", coin, base, ids, EX.meta(ids[0]).signal_tf))
            else:
                s = members[0]
                singles.setdefault((s.family, EX.meta(s.variant_id).signal_tf), []).append(s.variant_id)
        for (fam, tf), ids in singles.items():
            units.append(Unit(f"{fam}.{tf}@{coin}", coin, None, tuple(ids), tf))
        units.sort(key=lambda u: (TF_ORDER.get(u.signal_tf, 9), u.key))
        out += units
        if coin in ag_coins:
            out += ag_units(coin)
    if ag_coins:
        for coin in ag_coins:
            if coin not in U.ENTRY_UNIVERSE_FROZEN:
                out += ag_units(coin)
        out.append(ag_eval_unit())
    return out


def root_of(paths: Any) -> Path:
    return Path(paths.research) / ROOT_NAME


def cache_file(root: Path, month: str, unit_key: str) -> Path:
    return Path(root) / "cache" / month / f"{unit_key}.jsonl.gz"


# ============================================================================ veri (yalnız arşivle doğrulanmış, ≤ pencere sonu)
@dataclass
class History:
    coin: str
    bars: dict[str, list[dict[str, Any]]]
    funding: list[tuple[int, float, float]] | None
    markpx: list[dict[str, Any]] | None
    parts: dict[str, list[list[str]]]          # dizi → [[ay, sha], ...] (mühür listesi; birim kimliği)


def _arrays_for(units: Iterable[Unit]) -> set[str]:
    out: set[str] = set()
    for u in units:
        for v in u.variants:
            out |= set(EX.meta(v).arrays)
    return out


def _needs_btc(units: Iterable[Unit]) -> tuple[bool, bool]:
    ms = [EX.meta(v) for u in units for v in u.variants]
    return any(m.needs_btc for m in ms), any(m.needs_btc_4h for m in ms)


def listed_bars(reader: Any, coin: str, kind: str, end_ms: int, *, market: str = "futures") -> int | None:
    """Mühür listesinden dizinin ≤ `end_ms` ayındaki bar sayısının ÜST sınırı (tam aylar; yüklemeden). Liste yoksa None."""
    listing = getattr(reader, "listing", None)
    if not isinstance(listing, Mapping):
        return None
    got = listing.get(f"{market}/{EX.coin_of(coin)}USDT/{kind}") or {}
    t = datetime.fromtimestamp((int(end_ms) - 1) / 1000, tz=timezone.utc)
    last = f"{t.year:04d}/{t.month:02d}"
    months = sum(1 for ym in got if str(ym).replace("-", "/") <= last)
    step = EX.MARK_TF_MS if kind == "markpx_1h" else EX.TF_MS[kind]
    return int(months * 31 * DAY_MS // step)


def predict_unit_bytes(reader: Any, unit: Unit, loaded: Mapping[str, Sequence[Any]] | None, end_ms: int, *,
                       first_load: bool) -> int | None:
    """Birimin bellekte EKLEYECEĞİ baytın tahmini: yüklenecek diziler × `BAR_BYTES` + birimin bütün barları ×
    `RUN_BYTES_PER_BAR[tür]` (çekirdeğin çalışma kümesi). `loaded`: coin'in bellekteki dizileri. Liste yoksa None."""
    have = loaded or {}
    new = total = 0
    for tf in _arrays_for([unit]):
        if tf in have:
            n = len(have[tf])
        else:
            n = listed_bars(reader, unit.coin, tf, end_ms)
            if n is None:
                return None
            new += n
        total += n
    if first_load:
        new += listed_bars(reader, unit.coin, "markpx_1h", end_ms) or 0
    return new * BAR_BYTES + total * RUN_BYTES_PER_BAR.get(unit.kind, DEFAULT_RUN_BYTES_PER_BAR)


def load_history(reader: Any, coin: str, arrays: Iterable[str], end_ms: int, *, funding: bool = True,
                 market: str = "futures") -> History:
    """Coin'in dizileri: yalnız `_src == archive` ve kapanışı ≤ `end_ms` satırlar (boşluk boşluk kalır). Parça mühürden
    sonra değiştiyse `provider.DataMoving` yükselir (birim o gece atlanır)."""
    from .store import FUNDING, MARKPX_1H, SRC_COL
    raw = f"{EX.coin_of(coin)}USDT"
    listing = getattr(reader, "listing", None)

    def _parts(kind: str) -> list[list[str]]:
        if not isinstance(listing, Mapping):
            return []
        got = listing.get(f"{market}/{raw}/{kind}") or {}
        return [[ym, sha] for ym, sha in sorted(got.items())]

    bars: dict[str, list[dict[str, Any]]] = {}
    parts: dict[str, list[list[str]]] = {}
    for tf in sorted(set(arrays), key=lambda t: EX.TF_MS[t]):
        step = EX.TF_MS[tf]
        rows: list[dict[str, Any]] = []
        for _ym, df in reader.iter_parts(market, raw, tf, None, int(end_ms), keep_src=True):
            if not len(df):
                continue
            df = df[(df[SRC_COL] == EX.ARCHIVE_SRC) & (df["timestamp"] + step <= int(end_ms))]
            cols = [c for c in ("timestamp", "open", "high", "low", "close", "volume") if c in df.columns]
            rows += [{k: (int(r[k]) if k == "timestamp" else float(r[k])) for k in cols}
                     for r in df[cols].to_dict("records")]
        rows.sort(key=lambda r: r["timestamp"])
        bars[tf] = rows
        parts[tf] = _parts(tf)
    fr: list[tuple[int, float, float]] | None = None
    mk: list[dict[str, Any]] | None = None
    if funding:
        fr = []
        for _ym, df in reader.iter_parts(market, raw, FUNDING, None, int(end_ms), keep_src=True):
            if len(df):
                df = df[df[SRC_COL] == EX.ARCHIVE_SRC]
                fr += [(int(t), EX._num(r), EX._num(m)) for t, r, m in zip(df["timestamp"], df["rate"],
                                                                         df["mark"] if "mark" in df.columns else
                                                                         [float("nan")] * len(df))]
        fr.sort()
        parts[FUNDING] = _parts(FUNDING)
        mk = []
        for _ym, df in reader.iter_parts(market, raw, MARKPX_1H, None, int(end_ms), keep_src=True):
            if len(df):
                df = df[(df[SRC_COL] == EX.ARCHIVE_SRC) & (df["timestamp"] + EX.MARK_TF_MS <= int(end_ms))]
                cols = [c for c in ("timestamp", "open", "high", "low", "close") if c in df.columns]
                mk += [{k: (int(r[k]) if k == "timestamp" else float(r[k])) for k in cols}
                       for r in df[cols].to_dict("records")]
        mk.sort(key=lambda r: r["timestamp"])
        parts[MARKPX_1H] = _parts(MARKPX_1H)
    return History(coin=EX.coin_of(coin), bars=bars, funding=fr, markpx=mk, parts=parts)


def unit_data(h: History, btc: History | None, unit: Unit, tick: float) -> KN.SeriesData:
    metas = [EX.meta(v) for v in unit.variants]
    tfs = sorted({tf for m in metas for tf in m.arrays}, key=lambda t: EX.TF_MS[t])
    btc_1d = btc.bars.get("1d") if (btc is not None and any(m.needs_btc for m in metas)) else None
    btc_4h = btc.bars.get("4h") if (btc is not None and any(m.needs_btc_4h for m in metas)) else None
    return KN.SeriesData(symbol=f"{h.coin}/USDT", bars={tf: h.bars.get(tf) or [] for tf in tfs}, btc_1d=btc_1d,
                         funding=h.funding, price_tick=float(tick), btc_4h=btc_4h, markpx_1h=h.markpx)


def unit_identity(unit: Unit, h: History, btc: History | None, month: str, end_ms: int,
                  spec_of: Callable[[str], str | None]) -> dict[str, Any]:
    """Birimin girdi kimliği: kod (çekirdek sürümü, kayıt sha'sı, S1b, varyant spec'leri) + veri (kullanılan dizilerin
    mühür parçaları ≤ pencere sonu ayı)."""
    metas = [EX.meta(v) for v in unit.variants]
    keys = sorted({tf for m in metas for tf in m.arrays} | {"funding", "markpx_1h"})
    data = {k: h.parts.get(k) or [] for k in keys}
    if btc is not None and any(m.needs_btc or m.needs_btc_4h for m in metas):
        data.update({f"BTC:{k}": btc.parts.get(k) or [] for k in ("1d", "4h")})
    out = {"version": VERSION, "kernel": KN.KERNEL_VERSION, "registry_sha": RG.REGISTRY_SHA,
           "s1b": EX._s1b_sha(), "month": month, "end_ms": int(end_ms),
           "specs": {v: spec_of(v) for v in unit.variants}, "data_parts": data}
    if unit.lib != LIB_UNIT:
        out["s1d"] = S1D.S1D_SHA
    return out


def run_unit(unit: Unit, h: History, btc: History | None, *, tick: float | None, month: str, end_ms: int,
             spec_of: Callable[[str], str | None]) -> tuple[list[bytes], dict[str, Any]]:
    """Birimin çekirdek koşusu → (satır baytları, özet). Belirlenimci. Veri yoksa `NO_DATA`, fiyat adımı yoksa `NO_TICK`
    (yalnız başlık satırı; o ayın kurulumunda birim bitmiş sayılır, durumu raporlanır)."""
    sig_rows = h.bars.get(unit.signal_tf) or []
    ident = unit_identity(unit, h, btc, month, end_ms, spec_of)
    meta: dict[str, Any] = {"unit": unit.key, "variants": list(unit.variants), "trades": 0, "status": ST_NO_DATA}
    no_tick = tick is None or not float(tick) > 0
    if len(sig_rows) < WF.WARMUP_BARS + 2 or no_tick:
        meta["status"] = ST_NO_DATA if len(sig_rows) < WF.WARMUP_BARS + 2 else ST_NO_TICK
        head = {"k": "H", "schema": CACHE_SCHEMA, "unit": unit.key, "status": meta["status"],
                "bars": len(sig_rows), "identity": ident}
        return [(EX.dumps(EX._json_norm(head)) + "\n").encode("utf-8")], meta
    data = unit_data(h, btc, unit, float(tick))
    start = WF.data_start_ms(int(sig_rows[0]["timestamp"]), unit.signal_tf)
    if unit.lib == AG_UNIT:                     # S1d: aynı çekirdek, AG varyantları (`lib_ag`; S1d tutmazsa kurulmaz)
        from . import lib_ag as LA
        res, sig = LA.run_variants(list(unit.variants), data, start_ms=start, end_ms=int(end_ms))
        rows = LA.trade_rows([res[v] for v in sorted(res)], data)
    else:
        if unit.base is not None:
            res, sig = WF.run_cover_family(unit.base, data, start_ms=start, end_ms=int(end_ms))
        else:
            res, sig = WF.run_variants(list(unit.variants), data, start_ms=start, end_ms=int(end_ms))
        rows = WF.trade_rows([res[v] for v in sorted(res)], data)
    rows.sort(key=lambda r: (r["option"], r["ts_ms"], r["trade_key"]))
    head = {"k": "H", "schema": CACHE_SCHEMA, "unit": unit.key, "status": ST_DONE, "start_ms": start,
            "end_ms": int(end_ms), "trades": len(rows), "identity": ident}
    out = [(EX.dumps(EX._json_norm(head)) + "\n").encode("utf-8")]
    out += [(EX.dumps(EX._json_norm({"k": "T", **r})) + "\n").encode("utf-8") for r in rows]
    out += [(EX.dumps({"k": "S", "variant": v, "times": sorted(int(x) for x in sig.get(v, ()))}) + "\n").encode("utf-8")
            for v in sorted(sig)]
    meta.update(trades=len(rows), status=ST_DONE)
    return out, meta


def run_ag_eval(unit: Unit, root: Path, bld: Mapping[str, Any], *, units: Sequence[Unit]) -> tuple[list[bytes], dict[str, Any]]:
    """`AG_EVAL@AG_POOLED`: kurulumun AG sembol birimlerinin önbelleklerinden varyant başına WF değerlendirmesi
    (`lib_ag.evaluate`, 41 sembolün birleşimi; plan `lib_ag.plan_for([vid], başlangıç, bitiş)`, başlangıç = varyantın
    birimlerinin en erken veri başı, bitiş = ay sonu). Bellek: varyant varyant akışla okunur (grubunun 41 dosyasından yalnız
    o varyantın satırları), değerlendirilir, bırakılır. Çıktı: başlık (girdi birimlerinin içerik sha'ları) + varyant başına `R` (kompakt: OOS işlem/gün, katmanlar,
    üç maliyetle ortalama R, sembol başına net R, kapı dışı özet, takvim işareti). Belirlenimci."""
    from . import agnight as AGN
    from . import lib_ag as LA
    month, end_ms = str(bld["month"]), int(bld["end_ms"])
    recs = bld.get("units") or {}
    ag = [u for u in units if u.lib == AG_UNIT]
    inputs = {u.key: (recs.get(u.key) or {}).get("sha256") for u in ag}
    ident = {"version": VERSION, "kernel": KN.KERNEL_VERSION, "registry_sha": RG.REGISTRY_SHA, "s1b": EX._s1b_sha(),
             "s1d": S1D.S1D_SHA, "month": month, "end_ms": end_ms, "inputs": inputs}
    by_group: dict[str, list[Unit]] = {}
    for u in ag:
        by_group.setdefault(u.key.split("@", 1)[0], []).append(u)
    out_rows: list[dict[str, Any]] = []
    trades = 0
    for g, vids in AGN.groups():
        done = [u for u in by_group.get(g, []) if (recs.get(u.key) or {}).get("status") == ST_DONE]
        for v in vids:                          # varyant başına akışla (bellek: tek varyantın 41 sembollük satırları)
            rr: list[dict[str, Any]] = []
            starts: list[int] = []
            syms: set[str] = set()
            for u in done:
                for r in read_cache(cache_file(root, month, u.key)):
                    k = r.pop("k", None)
                    if k == "H" and r.get("start_ms") is not None:
                        starts.append(int(r["start_ms"]))
                    elif k == "T" and r.get("option") == v:
                        rr.append(r)
                        syms.add(str(r.get("symbol")))
            rec: dict[str, Any] = {"k": "R", "row_id": LA.row_id(v), "variant": v, "group": g,
                                   "symbols_with_trades": len(syms), "rows": len(rr)}
            if not starts:
                rec["status"] = "NO_DATA"
                out_rows.append(rec)
                continue
            start = min(starts)
            try:
                plan = LA.plan_for([v], start, end_ms)
            except ValueError as exc:                    # üç yollu katmanlar sığmıyor (kısa geçmiş; ör. XAUUSDT geç listelendi)
                rec.update(status=ST_WF_SHORT, start_ms=start, end_ms=end_ms, reason=str(exc)[:200])
                out_rows.append(rec)
                continue
            res = LA.evaluate(v, rr, plan)
            means = res.means
            rec.update(status=ST_DONE, start_ms=start, end_ms=end_ms, cal_flag=LA.cal_flag(start, end_ms),
                       wf_class=res.wf_class, plan_meta=res.plan_meta, n_oos=len(res.trades), oos_days=len(res.oos_days),
                       folds=[{k: f.get(k) for k in ("idx", "leak", "skipped", "n", "n_excluded", "mean_adverse")}
                              for f in res.folds],
                       mean_r=means, excluded=res.excluded,
                       instrument_net_r={k: round(float(x), 6) for k, x in sorted(res.instrument_net_r.items())})
            trades += len(rr)
            out_rows.append(rec)
            del rr, res
    head = {"k": "H", "schema": CACHE_SCHEMA, "unit": unit.key, "status": ST_DONE, "end_ms": end_ms,
            "variants": len(out_rows), "identity": ident}
    lines = [(EX.dumps(EX._json_norm(head)) + "\n").encode("utf-8")]
    lines += [(EX.dumps(EX._json_norm(r)) + "\n").encode("utf-8") for r in out_rows]
    return lines, {"unit": unit.key, "variants": [r["variant"] for r in out_rows], "trades": trades, "status": ST_DONE,
                   "evaluated": sum(1 for r in out_rows if r.get("status") == ST_DONE)}


def read_cache(path: Path | str) -> Iterable[dict[str, Any]]:
    """Önbellek satırları (salt-okunur; Kapı A okuyucusu P4a)."""
    import gzip
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


# ============================================================================ kuyruk durumu
def code_identity() -> dict[str, Any]:
    """Kurulumun kod kimliği (değişirse eski kurulum atılır)."""
    return {"version": VERSION, "kernel": KN.KERNEL_VERSION, "registry_sha": RG.REGISTRY_SHA, "s1b": EX._s1b_sha(),
            "s1d": S1D.S1D_SHA}


def _new_build(month: str, end_ms: int, total: int) -> dict[str, Any]:
    return {"month": month, "end_ms": int(end_ms), "total": int(total), "units": {}, "errors": {}, "kills": {},
            "started_at_ms": None, "code": code_identity()}


def read_queue(root: Path | str) -> dict[str, Any]:
    try:
        with open(Path(root) / "queue.json", "r", encoding="utf-8") as fh:
            q = json.load(fh)
    except (OSError, ValueError):
        q = None
    if not isinstance(q, dict) or q.get("schema") != QUEUE_SCHEMA:
        q = {"schema": QUEUE_SCHEMA, "version": VERSION, "building": None, "complete": None, "in_progress": None,
             "avg_s": {}, "verify": {}, "nights": 0}
    return q


def _save(paths: Any, root: Path, q: Mapping[str, Any]) -> None:
    paths.write_json(Path(root) / "queue.json", q)


def backlog(q: Mapping[str, Any]) -> dict[str, Any]:
    """`WF_BACKLOG` görünümü: kurulan ay, done/total, tam kurulumun ayı."""
    b, c = q.get("building"), q.get("complete")
    out: dict[str, Any] = {"complete_month": (c or {}).get("month"), "building_month": (b or {}).get("month")}
    if b:
        out.update(done=len(b.get("units") or {}), total=int(b.get("total") or 0))
    elif c:
        out.update(done=len(c.get("units") or {}), total=int(c.get("total") or 0))
    else:
        out.update(done=0, total=None)
    sk = sum(1 for x in ((b or c or {}).get("units") or {}).values() if x.get("status") in SKIPPED_STATES)
    if sk:
        out["skipped"] = sk                        # atlanan (hata/öldü/uzun/büyük) birim: kurulum tamamlanabilir
    return out


def eta_nights(q: Mapping[str, Any], night_budget_s: float) -> float | None:
    """Kalan birimlerin ölçülmüş ortalama süreyle tahmini gece sayısı (bu makinede)."""
    b = q.get("building")
    if not b:
        return 0.0
    remaining = int(b.get("total") or 0) - len(b.get("units") or {})
    avg = q.get("avg_s") or {}
    if not avg or night_budget_s <= 0:
        return None
    mean = sum(float(x[0]) for x in avg.values()) / len(avg)
    return round(remaining * mean / float(night_budget_s), 1)


# ============================================================================ S4w adımı
def run_s4w(paths: Any, reader: Any, *, entry_symbols: Sequence[str], ticks: Mapping[str, float], now_ms: int,
            deadline: float, clock: Callable[[], float] = time.monotonic,
            guard: Callable[[], Mapping[str, Any]] | None = None,
            spec_of: Callable[[str], str | None] | None = None, night_budget_s: float = 3600.0,
            units: Sequence[Unit] | None = None, ag_symbols: Sequence[str] | None = None,
            ag_run: bool = True, rss: Callable[[], int | None] | None = None,
            rss_limit: int | None = None) -> dict[str, Any]:
    """Bir gecelik S4w adımı (modül belgesi). `deadline`: `clock()` biriminde (monotonic sn) son an. `guard()`: bellek
    koruması (`{"ok": bool, ...}`; gc + RSS); `ok` yanlışsa kontrol noktası yazılmış olarak durulur. `rss()`: ucuz şimdiki
    RSS (gc yok; bekçinin her atışında ve yükleme tahmininde), `rss_limit`: eşiği (bayt; gece 0,7 × MemoryMax) — ikisi
    yoksa yalnız `guard()` (en geç `GUARD_EVERY_S`'de, birim başında sıfırlanır). `units`: test alt kümesi.
    `ag_symbols`: AG evreni (planın AG birimleri); `ag_run` yanlışsa AG birimleri bu gece koşmaz (planda bekler).
    Dönen: rapor (`backlog`, `stop`, sayılar)."""
    from .provider import DataMoving
    root = Path(paths.require_research(root_of(paths)))
    t0 = clock()
    q = read_queue(root)
    q["nights"] = int(q.get("nights") or 0) + 1
    rep: dict[str, Any] = {"schema": "wf_run_v1", "version": VERSION, "units_done": 0, "units_error": 0,
                           "units_no_data": 0, "units_killed": 0, "units_aborted": 0, "verified": 0, "mismatch": 0,
                           "trades": 0,
                           "moving": {}, "errors": [], "stop": STOP_DONE, "guard": None}
    plan = list(units) if units is not None else (build_units(entry_symbols, ag_symbols) if ag_symbols
                                                  else build_units(entry_symbols))
    by_key = {u.key: u for u in plan}
    n_ag = sum(1 for u in plan if u.lib != LIB_UNIT)
    if n_ag:
        rep["ag"] = {"units_planned": n_ag, "run": bool(ag_run), "units_built": 0, "units_done": 0, "trades": 0}
    month, end_ms = target_month(now_ms)
    # (1) önceki gece bir birimin ortasında öldüyse say
    ip = q.get("in_progress")
    if ip and q.get("building") and ip.get("month") == q["building"]["month"]:
        k = str(ip.get("unit"))
        kills = q["building"].setdefault("kills", {})
        kills[k] = int(kills.get(k, 0)) + 1
        rep["previous_night_killed_in"] = k
        if kills[k] >= MAX_KILLS:
            q["building"]["units"][k] = {"status": ST_KILLED, "kills": kills[k]}
            rep["units_killed"] += 1
    q["in_progress"] = None
    # (2) kod kimliği değiştiyse (çekirdek, kayıt, S1b, kuyruk sürümü) eski sonuçlar kullanılmaz: yeniden kurulum
    code = code_identity()
    for slot in ("building", "complete"):
        if q.get(slot) and q[slot].get("code") != code:
            rep.setdefault("discarded_stale_code", []).append(q[slot].get("month"))
            q[slot] = None
    # (3) kurulacak ay
    if q.get("building") is None and (q.get("complete") or {}).get("month") != month:
        q["building"] = _new_build(month, end_ms, len(plan))
    b = q.get("building")
    if b is not None and b.get("started_at_ms") is None:
        b["started_at_ms"] = int(now_ms)
    _save(paths, root, q)
    spec_of = spec_of or (lambda v: None)
    saved = {"t": clock()}

    def _ckpt(force: bool = False) -> None:
        """Kontrol noktası: uzun birimden önce ve en geç her `SAVE_EVERY_S`'de (kısa birimlerde her birimde fsync yok;
        yazılmış ama kaydedilmemiş önbellek dosyası yeniden hesaplanır — atomik yazım, bozulma yok)."""
        if force or clock() - saved["t"] >= SAVE_EVERY_S:
            _save(paths, root, q)
            saved["t"] = clock()
    cur: dict[str, Any] = {"coin": None, "hist": None}
    btc_hist: dict[str, Any] = {"h": None}

    gstate = {"t": None, "coin": None}
    base = {"rss": None}                                       # S4w başındaki RSS: "taze gecede de büyük mü?" ölçüsü
    cheap = guard is not None and rss is not None and bool(rss_limit)

    def _rss_now() -> int | None:
        try:
            return rss() if rss is not None else None
        except Exception:  # noqa: BLE001 — ölçüm arızası birimi bırakmaz (guard() yine çalışır)
            return None

    def _confirm() -> dict[str, Any]:
        """Tam denetim (`guard()`: gc + RSS); zamanlayıcıyı sıfırlar."""
        g = dict(guard())
        gstate["t"] = clock()
        rep["guard"] = g
        return g

    def _guard_ok(coin: str | None = None) -> bool:
        """Birimden önce bellek koruması: ucuz RSS eşiği aşıyorsa hemen doğrulanır; yoksa tam denetim yeni coin'de ve en
        geç her `GUARD_EVERY_S`'de (`gc.collect()` pahalıdır)."""
        if guard is None:
            return True
        if cheap:
            r = _rss_now()
            if r is not None and r > int(rss_limit):
                gstate["coin"] = coin
                return bool(_confirm().get("ok", True))
        if coin == gstate["coin"] and gstate["t"] is not None and clock() - gstate["t"] < GUARD_EVERY_S:
            return True
        gstate["coin"] = coin
        return bool(_confirm().get("ok", True))

    def _mid_check() -> str | None:
        """Birim ortası (bekçi; birimin kendi yüklemesi dahil): son tarih ve ucuz RSS her atışta (eşik aşılırsa `guard()`
        doğrular); ucuz ölçüm yoksa `guard()` birim başından itibaren en geç `GUARD_EVERY_S`'de."""
        if clock() > deadline:
            return STOP_DEADLINE
        if guard is None:
            return None
        if cheap:
            r = _rss_now()
            if r is not None:
                if r > int(rss_limit) and not _confirm().get("ok", True):
                    return STOP_GUARD
                return None
        if gstate["t"] is None or clock() - gstate["t"] >= GUARD_EVERY_S:
            if not _confirm().get("ok", True):
                return STOP_GUARD
        return None

    def _est(u: Unit) -> float:
        a = (q.get("avg_s") or {}).get(u.kind)
        return float(a[0]) if a else DEFAULT_UNIT_S

    def _record(u: Unit, sec: float) -> None:
        avg = q.setdefault("avg_s", {})
        m, n = avg.get(u.kind, [0.0, 0])
        avg[u.kind] = [round((float(m) * int(n) + sec) / (int(n) + 1), 3), int(n) + 1]

    def _release(u: Unit, pending: Sequence[Unit]) -> None:
        """Coin'in bellekteki dizilerinden bu birimin istemediği büyükler (15m/5m) ve coin'in kalan birimlerinin hiçbirinin
        istemediği küçükler bırakılır (coin değişince hepsi). Bellek koruması böylece yalnız bu birimin yükünü ölçer."""
        h = cur["hist"]
        if h is None:
            return
        if cur["coin"] != u.coin:
            cur["coin"], cur["hist"] = None, None
            gc.collect()
            return
        mine = _arrays_for([u])
        later = _arrays_for([x for x in pending if x.coin == u.coin])
        for tf in [tf for tf in h.bars if tf not in mine and (tf in LARGE_TFS or tf not in later)]:
            h.bars.pop(tf, None)
            h.parts.pop(tf, None)

    def _hist_for(u: Unit, end: int) -> History:
        """Birimin geçmişi: YALNIZ kendi dizileri yüklenir (coin'in ilk biriminde fonlama + markpx de); sonraki birim
        eksik diziyi ekler (`_release` gereksizleri önceden bırakır)."""
        if cur["coin"] != u.coin or cur["hist"] is None:
            cur["coin"], cur["hist"] = None, None
            cur["hist"] = load_history(reader, u.coin, _arrays_for([u]), end)
            cur["coin"] = u.coin
        else:
            need = _arrays_for([u]) - set(cur["hist"].bars)
            if need:
                extra = load_history(reader, u.coin, need, end, funding=False)
                cur["hist"].bars.update(extra.bars)
                cur["hist"].parts.update(extra.parts)
        return cur["hist"]

    def _too_big(u: Unit, end: int) -> dict[str, Any] | None:
        """Yükleme öncesi tahmin (`predict_unit_bytes`) + şimdiki RSS eşiği aşıyor mu (gc'li doğrulamayla)."""
        if not cheap or u.lib == AG_EVAL:
            return None
        h = cur["hist"] if cur["coin"] == u.coin else None
        try:
            pred = predict_unit_bytes(reader, u, h.bars if h is not None else None, end, first_load=h is None)
        except Exception:  # noqa: BLE001 — tahmin arızası birimi bırakmaz (bekçi yine izler)
            pred = None
        if pred is None:
            return None
        r = _rss_now()
        if r is None or r + pred <= int(rss_limit):
            return None
        g = _confirm()                                                   # gc sonrası RSS ile yeniden
        r2 = g.get("rss_bytes") if g.get("rss_bytes") is not None else r
        if int(r2) + pred <= int(rss_limit):
            return None
        b0 = base["rss"]
        fresh = b0 is not None and int(b0) + pred > int(rss_limit)     # S4w başındaki bellekle de sığmıyor → gerçekten büyük
        return {"unit": u.key, "predicted_bytes": int(pred), "rss_bytes": int(r2), "limit_bytes": int(rss_limit),
                "base_rss_bytes": None if b0 is None else int(b0), "counted": bool(fresh)}

    def _btc_for(units_: Sequence[Unit], end: int) -> History | None:
        nb, nb4 = _needs_btc(units_)
        if not (nb or nb4):
            return None
        if btc_hist["h"] is None:
            btc_hist["h"] = load_history(reader, "BTC", ("1d", "4h"), end, funding=False)
        return btc_hist["h"]

    def _count_abort(u: Unit, bld: Mapping[str, Any], why: str, ran_s: float, **extra: Any) -> None:
        """Bırakma sayımı YALNIZ bu birime; `MAX_ABORTS` kezde `TOO_BIG_SKIPPED` / `TOO_LONG_SKIPPED`."""
        ab_n = bld.setdefault("aborts", {})
        ab_n[u.key] = int(ab_n.get(u.key, 0)) + 1
        if ab_n[u.key] >= MAX_ABORTS:
            bld["units"][u.key] = {"status": ST_TOO_BIG if why == STOP_GUARD else ST_TOO_LONG, "aborts": ab_n[u.key],
                                   "last_after_s": round(ran_s, 1), **extra}
            rep["units_aborted"] += 1

    def _do(u: Unit, bld: Mapping[str, Any], *, verify: bool) -> bool:
        """Birimi koş ve yaz (ya da doğrula). False = dur. Çağıran önce `_release` + `_guard_ok` yapar."""
        tick = ticks.get(u.coin)
        forced = False
        end = int(bld["end_ms"])
        if u.lib != AG_EVAL:
            big = _too_big(u, end)
            if big is not None:                     # yüklenmeden bırakılır
                rep.setdefault("guard_refused", []).append(big)
                rep["guard_refused"] = rep["guard_refused"][-20:]
                if not big["counted"]:              # yalnız önceki birimlerin bıraktığı bellek yüzünden: sayılmaz, S4w bu
                    rep["stop_why"] = STOP_GUARD    # gece durur (taze gecede kuyruk buradan sürer; sahte atlama yok)
                    return False
                if not verify:                      # taze gecede de sığmıyor: yalnız bu birime sayılır, kuyruk sürer
                    _count_abort(u, bld, STOP_GUARD, 0.0, predicted_bytes=big["predicted_bytes"])
                    _ckpt(force=True)
                return True
        if not verify:
            q["in_progress"] = {"month": bld["month"], "unit": u.key}
            if _est(u) >= SAVE_EVERY_S:
                _ckpt(force=True)
                forced = True
        t = clock()
        gstate["t"] = t                             # bekçinin tam denetim zamanlayıcısı birim başında sıfırlanır
        try:
            with _Watch(_mid_check, WATCH_EVERY_S) as w:
                if u.lib == AG_EVAL:
                    cur["coin"], cur["hist"] = None, None             # son sembolün geçmişi bırakılır (bellek)
                    btc_hist["h"] = None
                    gc.collect()
                    lines, meta = run_ag_eval(u, root, bld, units=plan)
                else:
                    h = _hist_for(u, end)
                    btc = _btc_for([u], end)
                    lines, meta = run_unit(u, h, btc, tick=tick, month=str(bld["month"]), end_ms=end, spec_of=spec_of)
                w.done()
        except UnitAborted as ab:
            ran_s = clock() - t
            rep["aborted"] = {"unit": u.key, "why": ab.why, "after_s": round(ran_s, 1), "verify": bool(verify)}
            rep["stop_why"] = ab.why
            cur["coin"], cur["hist"] = None, None                       # yarım yüklenmiş geçmiş bırakılır
            btc_hist["h"] = None
            gc.collect()
            if not verify:
                if ab.why == STOP_GUARD or ran_s >= ABORT_MIN_FRACTION * float(night_budget_s):
                    _count_abort(u, bld, ab.why, ran_s)
                q["in_progress"] = None
                _ckpt(force=True)
            if ab.why == STOP_GUARD:                # bellek: yalnız bu birime sayıldı; gc sonrası RSS çoğu zaman yüksek kalır,
                rep.setdefault("guard_aborts", []).append(u.key)   # sonraki birimler sahte "büyük" sayılmasın diye S4w bu
                rep["guard_aborts"] = rep["guard_aborts"][-20:]    # gece durur
                gstate["t"] = None
            return False
        except DataMoving as exc:
            rep["moving"][u.coin] = f"{exc.series} {exc.ym}: {exc.reason}"[:200]
            cur["coin"], cur["hist"] = None, None
            q["in_progress"] = None
            _ckpt(force=forced)
            return True
        except Exception as exc:  # noqa: BLE001 — birim hatası kuyruğu durdurmaz; sayılır, sınırdan sonra ERROR
            rep["errors"].append(f"{u.key}: {type(exc).__name__}: {exc}"[:300])
            if not verify:
                errs = bld.setdefault("errors", {})
                errs[u.key] = int(errs.get(u.key, 0)) + 1
                if errs[u.key] >= MAX_UNIT_ERRORS:
                    bld["units"][u.key] = {"status": ST_ERROR, "error": f"{type(exc).__name__}: {exc}"[:200]}
                rep["units_error"] += 1
                q["in_progress"] = None
                _ckpt(force=True)
            return True
        sec = clock() - t
        raw = b"".join(lines)
        sha = hashlib.sha256(raw).hexdigest()
        path = cache_file(root, str(bld["month"]), u.key)
        if verify:
            rec = bld["units"].get(u.key) or {}
            rep["verified"] += 1
            q.setdefault("verify", {})[u.key] = int(now_ms) // DAY_MS
            if rec.get("sha256") != sha:
                rep["mismatch"] += 1
                paths.write_gzip_stream(path, [raw])
                bld["units"][u.key] = dict(rec, sha256=sha, rows=len(lines), status=meta["status"],
                                           mismatch_at_ms=int(now_ms))
        else:
            paths.write_gzip_stream(path, [raw])
            bld["units"][u.key] = {"status": meta["status"], "sha256": sha, "rows": len(lines),
                                   "trades": meta["trades"], "seconds": round(sec, 3)}
            rep["units_done" if meta["status"] == ST_DONE else "units_no_data"] += 1
            if meta["status"] == ST_NO_TICK:
                rep["units_no_tick"] = rep.get("units_no_tick", 0) + 1
            if u.lib == AG_UNIT:
                rep["ag"]["units_built"] += 1                       # bu gece kurulan AG birimi (veri yok/adım yok dahil)
                rep["ag"]["units_done"] += int(meta["status"] == ST_DONE)
                rep["ag"]["trades"] += int(meta["trades"])
            elif u.lib == AG_EVAL:
                rep["ag"]["evaluated"] = int(meta.get("evaluated") or 0)
            else:
                rep["trades"] += int(meta["trades"])
            q["in_progress"] = None
        _record(u, sec)
        _ckpt(force=forced)                      # diske yazılan `in_progress` hemen temizlenir (yanlış suçlama yok)
        del lines, raw
        return True

    stop = STOP_DONE
    base["rss"] = _rss_now() if cheap else None
    b = q.get("building")
    if b is not None:
        pending = [u for u in plan if u.key not in (b.get("units") or {}) and (ag_run or u.lib == LIB_UNIT)]
        if n_ag and not ag_run:
            rep["ag"]["waiting"] = sum(1 for u in plan if u.lib != LIB_UNIT and u.key not in (b.get("units") or {}))
        ran = 0
        for i, u in enumerate(pending):
            if u.coin in rep["moving"]:                                 # coin'in parçası mühürden sonra değişti
                continue
            if u.lib == AG_EVAL and any(x.lib == AG_UNIT and x.key not in b["units"] for x in plan):
                rep["ag"]["eval"] = "WAITING_FOR_AG_UNITS"              # bir AG birimi bu gece koşamadı (veri hareketi)
                continue
            if clock() > deadline:
                stop = STOP_DEADLINE
                break
            if ran and clock() + _est(u) > deadline:                   # ilk birim yalnız son tarihe bakar (tıkanmaz)
                stop = STOP_ESTIMATE
                break
            _release(u, pending[i:])                                    # denetim yalnız bu birimin önündeki belleği ölçer
            if not _guard_ok(u.coin):
                stop = STOP_GUARD
                break
            if not _do(u, b, verify=False):                             # birim ortasında bırakıldı
                stop = rep.get("stop_why") or STOP_DEADLINE
                break
            ran += 1
        if stop == STOP_DONE and rep["moving"]:
            stop = STOP_MOVING
        if stop == STOP_DONE and len(b["units"]) >= int(b["total"]):
            old = q.get("complete")
            q["complete"] = dict(b, completed_at_ms=int(now_ms))
            q["building"] = None
            rep["completed_month"] = b["month"]
            _save(paths, root, q)
            if old and old.get("month") != b["month"]:
                import shutil
                d = root / "cache" / str(old["month"])
                if d.is_dir() and paths.is_under_research(d):
                    shutil.rmtree(d, ignore_errors=True)
    # (4) haftalık 1/7 doğrulama (kurulum tamken, süre kalırsa)
    c = q.get("complete")
    if stop == STOP_DONE and q.get("building") is None and c:
        day = int(now_ms) // DAY_MS
        todo = [by_key[k] for k, rec in sorted(c["units"].items())
                if k in by_key and rec.get("status") == ST_DONE and _bucket(k) == day % VERIFY_ROTATION
                and int((q.get("verify") or {}).get(k, -1)) != day and (ag_run or by_key[k].lib == LIB_UNIT)]
        for i, u in enumerate(todo):
            if clock() > deadline or clock() + _est(u) > deadline:
                stop = STOP_DEADLINE
                break
            _release(u, todo[i:])
            if not _guard_ok(u.coin):
                stop = STOP_GUARD
                break
            if not _do(u, c, verify=True):
                stop = rep.get("stop_why") or STOP_DEADLINE
                break
        # tam kurulum varken yeni ay hazırsa: sonraki gece kuruluma başlar
        if c.get("month") != month and q.get("building") is None:
            q["building"] = _new_build(month, end_ms, len(plan))
            rep["next_month_queued"] = month
    rep["stop"] = stop
    rep["backlog"] = backlog(q)
    rep["eta_nights"] = eta_nights(q, night_budget_s)
    rep["seconds"] = round(clock() - t0, 3)
    rep["errors"] = rep["errors"][:20]
    cur["hist"] = None
    btc_hist["h"] = None
    _save(paths, root, q)
    gc.collect()
    return rep


def _bucket(key: str) -> int:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % VERIFY_ROTATION


__all__ = ["ABORT_MIN_FRACTION", "ARCHIVE_LAG_DAYS", "CACHE_SCHEMA", "DEFAULT_UNIT_S", "History", "MAX_ABORTS", "MAX_KILLS",
           "MAX_UNIT_ERRORS", "QUEUE_SCHEMA", "ROOT_NAME", "SKIPPED_STATES", "STOP_DEADLINE", "STOP_DONE", "STOP_ESTIMATE",
           "STOP_GUARD", "STOP_MOVING", "ST_DONE", "ST_ERROR", "ST_KILLED", "ST_NO_DATA", "ST_NO_TICK", "ST_TOO_BIG", "ST_TOO_LONG",
           "Unit", "UnitAborted", "WATCH_EVERY_S", "disarm_watch", "VERIFY_ROTATION", "VERSION", "backlog", "build_units", "cache_file", "code_identity", "eta_nights",
           "load_history", "read_cache", "read_queue", "root_of", "run_s4w", "run_unit", "target_month", "unit_data",
           "unit_identity"]
