"""Deneyim havuzu — gerçek PAPER kapanışları + ETİKETLENMİŞ gölge sonuçlar, tek sözleşmede.

Neden: retrieval yalnız `TradeMemory` kapanışlarını görüyordu; `ShadowBook`'taki etiketlenmiş
karşı-olgusal sonuçlar öğrenmeye hiç katılmıyordu. Ama gölge sonuç gerçek fill DEĞİLDİR —
eşit kanıt sayılamaz.

Sözleşme:
* Her deneyim `source` (`REAL_PAPER` | `SHADOW`), `provenance`, `execution_fidelity`,
  `label_ts_ms`, `decision_ts_ms` ve `weight` taşır.
* Gerçek PAPER varsayılan ağırlık 1.0; gölge `shadow_weight` (varsayılan 0.25) × fidelity.
* ETİKETSİZ gölge kayıt havuza GİREMEZ.
* `as_of_ms` sonrası ETİKETLENMİŞ gölge de giremez (no-lookahead; etiket zamanı esastır).
* Aynı aday hem gerçek hem gölge tarafta varsa TEK deneyim sayılır — gerçek olan kazanır.
* Ölçeklenebilirlik: dosya (mtime, size) imzasıyla önbelleklenir; tur başına en fazla bir kez
  okunur. Önbellek bozuk/eski ise baseline fail-safe (boş havuz) döner.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .features import build_features, feature_names, to_vector

SCHEMA_VERSION = "experience_pool_v1"

REAL_PAPER, SHADOW = "REAL_PAPER", "SHADOW"

#: Gölge sonucun gerçek fill'e göre yürütme sadakati — bar OHLC ile yürütülmüş karşı-olgusaldır.
DEFAULT_SHADOW_FIDELITY = 0.5

#: Feature profili sürümü — deterministik ve SINIRLI kova sayısı (yüksek kardinalite YOK).
FEATURE_PROFILE_VERSION = 1
_PROFILE_EDGES = {
    "atr_pct": (0.15, 0.3, 0.6),
    "rr": (1.5, 2.5, 4.0),
    "bias_trend": (-0.2, 0.2, 0.6),
    "conviction": (0.3, 0.6),
}
_COST_EDGES = (0.15, 0.5)          # maliyetin R cinsinden payı


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ms(v: Any) -> int | None:
    if isinstance(v, (int, float)) and math.isfinite(float(v)):
        return int(v)
    if isinstance(v, str) and len(v) >= 10:
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000)
        except (ValueError, TypeError):
            return None
    return None


def _bucket(x: float | None, edges: tuple[float, ...]) -> int:
    if x is None:
        return -1
    for i, e in enumerate(edges):
        if x < e:
            return i
    return len(edges)


def feature_profile(features: dict[str, Any] | None) -> str:
    """Deterministik, versiyonlu ve SINIRLI feature profili anahtarı.

    Kova sayısı sabittir (her boyut ≤5 kova) → yüksek kardinaliteli sınırsız bucket ÜRETMEZ.
    """
    f = features or {}
    parts = [f"v{FEATURE_PROFILE_VERSION}"]
    for name in sorted(_PROFILE_EDGES):
        parts.append(f"{name[:4]}{_bucket(_f(f.get(name)), _PROFILE_EDGES[name])}")
    return "|".join(parts)


def cost_sensitivity(outcome: dict[str, Any] | None) -> str:
    """`COST_DOMINATED` | `COST_SENSITIVE` | `COST_RESILIENT` | `UNKNOWN`.

    Brüt kârın maliyetle (fee+funding+slippage) ne kadar eridiğini R cinsinden sınıflar.
    """
    o = outcome or {}
    r = _f(o.get("r_multiple"))
    drags = [abs(_f(o.get(k)) or 0.0) for k in ("fee_drag_r", "funding_drag_r", "slippage_drag_r")]
    total = sum(drags)
    if r is None or (total == 0.0 and not any(k in o for k in
                                              ("fee_drag_r", "funding_drag_r", "slippage_drag_r"))):
        return "UNKNOWN"
    gross_r = r + total
    if gross_r > 0 and r <= 0:
        return "COST_DOMINATED"                 # brüt kâr net zarara döndü
    if gross_r > 0 and total / gross_r >= _COST_EDGES[1]:
        return "COST_DOMINATED"
    if gross_r > 0 and total / gross_r >= _COST_EDGES[0]:
        return "COST_SENSITIVE"
    return "COST_RESILIENT"


@dataclass
class Experience:
    """Tek geçmiş deneyim — kaynağı ve ağırlığı açık."""
    outcome_id: str
    source: str
    symbol: str | None = None
    direction: str | None = None
    setup: str | None = None
    regime: str | None = None
    r_multiple: float | None = None
    similarity: float | None = None
    weight: float = 1.0
    execution_fidelity: float = 1.0
    outcome_quality: str = "OBSERVED_FILL"
    provenance: str = "paper_ledger_close"
    label_ts_ms: int | None = None
    decision_ts_ms: int | None = None
    feature_profile: str | None = None
    cost_sensitivity: str = "UNKNOWN"
    lesson_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"outcome_id": self.outcome_id, "source": self.source, "symbol": self.symbol,
                "direction": self.direction, "setup": self.setup, "regime": self.regime,
                "r_multiple": self.r_multiple, "similarity": self.similarity,
                "weight": round(self.weight, 6), "execution_fidelity": self.execution_fidelity,
                "outcome_quality": self.outcome_quality, "provenance": self.provenance,
                "label_ts_ms": self.label_ts_ms, "decision_ts_ms": self.decision_ts_ms,
                "feature_profile": self.feature_profile,
                "cost_sensitivity": self.cost_sensitivity, "lesson_codes": self.lesson_codes}


class ExperienceIndex:
    """Dosya imzasıyla önbelleklenen deneyim indeksi — tur başına en fazla bir okuma.

    `signature` = (mtime_ns, size). Değişmediyse önceki liste yeniden kullanılır. Okuma hatası
    boş havuz döndürür (fail-safe); çağıran baseline'da kalır.
    """

    def __init__(self) -> None:
        self._sig: dict[str, tuple[int, int]] = {}
        self._rows: dict[str, list[dict[str, Any]]] = {}
        self.loads = 0
        self.errors = 0

    @staticmethod
    def _signature(path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def rows(self, key: str, path: Path | str, loader) -> list[dict[str, Any]]:
        p = Path(path)
        sig = self._signature(p)
        if sig is None:
            self._rows[key] = []
            return []
        if self._sig.get(key) == sig and key in self._rows:
            return self._rows[key]
        try:
            rows = list(loader())
        except Exception:  # noqa: BLE001 — bozuk state öğrenmeyi durdurur, worker'ı DEĞİL
            self.errors += 1
            self._rows[key] = []
            self._sig[key] = sig
            return []
        self.loads += 1
        self._sig[key] = sig
        self._rows[key] = rows
        return rows

    def stats(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "loads": self.loads, "errors": self.errors,
                "cached_keys": sorted(self._rows), "n_rows": {k: len(v) for k, v in self._rows.items()}}


def _identity(symbol: Any, direction: Any, setup: Any, opened: Any) -> str:
    """Aynı adayın gerçek ve gölge kaydını EŞLEŞTİREN kimlik (duplicate koruması)."""
    return "|".join(str(x or "") for x in (symbol, direction, setup, opened))


def real_experiences(rows: Iterable[dict[str, Any]], *, as_of_ms: int | None) -> list[Experience]:
    """`TradeMemory` kapanışları → deneyim. `as_of_ms` sonrası kapananlar HARİÇ."""
    out: list[Experience] = []
    for r in rows:
        o = r.get("outcome") or {}
        closed = _ms(o.get("closed_at") or r.get("closed_at"))
        if as_of_ms is not None and (closed is None or closed > int(as_of_ms)):
            continue
        rm = _f(o.get("r_multiple"))
        if rm is None:
            continue
        feats = r.get("features") or {}
        out.append(Experience(
            outcome_id=_identity(r.get("symbol"), r.get("direction"), r.get("setup_type"),
                                 o.get("opened_at") or r.get("opened_at")),
            source=REAL_PAPER, symbol=r.get("symbol"), direction=r.get("direction"),
            setup=r.get("setup_type"), regime=r.get("regime"), r_multiple=rm,
            weight=1.0, execution_fidelity=1.0, outcome_quality="OBSERVED_FILL",
            provenance="paper_ledger_close", label_ts_ms=closed,
            decision_ts_ms=_ms(r.get("decision_ts") or r.get("recorded_at")),
            feature_profile=feature_profile(feats),
            cost_sensitivity=cost_sensitivity(o),
            lesson_codes=list((r.get("postmortem") or {}).get("lesson_codes") or [])[:8]))
    return out


def shadow_experiences(trades: Iterable[dict[str, Any]], *, as_of_ms: int | None,
                       weight: float = 0.25,
                       fidelity: float = DEFAULT_SHADOW_FIDELITY) -> list[Experience]:
    """`ShadowBook` kayıtları → deneyim.

    * `outcome` YOKSA (etiketlenmemiş) havuza GİRMEZ.
    * `labeled_at` > `as_of_ms` ise GİRMEZ (gelecekte etiketlenmiş sonuç sızamaz).
    * Ağırlık `weight × fidelity` — gerçek fill'den DAİMA düşüktür.
    """
    out: list[Experience] = []
    for t in trades:
        o = t.get("outcome")
        if not isinstance(o, dict) or not o:
            continue                                   # ETİKETSİZ → dışarıda
        labeled = _ms(t.get("labeled_at")) or _ms(t.get("label_ts"))
        if as_of_ms is not None and (labeled is None or labeled > int(as_of_ms)):
            continue                                   # no-lookahead
        rm = _f(o.get("r_multiple"))
        if rm is None:
            continue
        eff = max(0.0, min(1.0, float(weight))) * max(0.0, min(1.0, float(fidelity)))
        out.append(Experience(
            outcome_id=_identity(t.get("symbol"), t.get("direction"), t.get("variant"),
                                 t.get("created_at")),
            source=SHADOW, symbol=t.get("symbol"), direction=t.get("direction"),
            setup=t.get("variant"), regime=None, r_multiple=rm,
            weight=eff, execution_fidelity=float(fidelity),
            outcome_quality="COUNTERFACTUAL_LABEL", provenance="shadow_book_label",
            label_ts_ms=labeled, decision_ts_ms=_ms(t.get("created_at")),
            feature_profile=None, cost_sensitivity="UNKNOWN",
            lesson_codes=[]))
    return out


def merge_sources(*groups: list[Experience]) -> list[Experience]:
    """Birden çok kaynağı TEK deneyim kümesine indirger — `outcome_id` bazında tekilleştirir.

    Öncelik: **gerçek fill (`REAL_PAPER`) DAİMA kazanır**, hangi grupta göründüğünden bağımsız.
    Aynı kaynak sınıfında ilk görülen kalır; gruplar öncelik sırasına göre verilmelidir
    (gerçek → aktif gölge → indekslenmiş arşiv geçmişi). Aynı outcome iki kez SAYILMAZ.
    """
    by_id: dict[str, Experience] = {}
    for group in groups:
        for e in group or []:
            cur = by_id.get(e.outcome_id)
            if cur is None:
                by_id[e.outcome_id] = e
            elif cur.source != REAL_PAPER and e.source == REAL_PAPER:
                by_id[e.outcome_id] = e
    return [by_id[k] for k in sorted(by_id)]


def merge_experiences(real: list[Experience], shadow: list[Experience]) -> list[Experience]:
    """Aynı adayın gerçek ve gölge kaydını TEK deneyime indirger — gerçek olan kazanır."""
    return merge_sources(real, shadow)


def experience_vector(e: Experience, rows_by_id: dict[str, dict[str, Any]],
                      names: list[str]) -> tuple[list[float], float]:
    """Deneyimin özellik vektörü + normu — **TEK uygulama**.

    Aktif havuz ve arşiv indeksi AYNI fonksiyonu kullanır; bir kaydın arşive taşınması
    benzerlik skorunu DEĞİŞTİREMEZ (aksi halde rotasyon kararı etkilerdi).
    """
    row = rows_by_id.get(e.outcome_id) or {}
    feats = row.get("features") or row or {}
    try:
        v = to_vector(build_features(feats), names)
    except Exception:  # noqa: BLE001
        v = [0.0] * len(names)
    return v, (math.sqrt(sum(x * x for x in v)) or 1.0)


def rows_by_identity(memory_rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`outcome_id` → ham `TradeMemory` satırı (vektörleme için)."""
    out: dict[str, dict[str, Any]] = {}
    for r in memory_rows:
        o = r.get("outcome") or {}
        out[_identity(r.get("symbol"), r.get("direction"), r.get("setup_type"),
                      o.get("opened_at") or r.get("opened_at"))] = r
    return out


def score_similarity(pool: list[Experience], rows_by_id: dict[str, dict[str, Any]],
                     query: dict[str, Any], *, names: list[str] | None = None) -> None:
    """Havuzdaki her deneyime deterministik benzerlik yazar (yerinde). NumPy'siz, ucuz."""
    names = names or feature_names()
    try:
        q = to_vector(build_features(query), names)
    except Exception:  # noqa: BLE001
        return
    qn = math.sqrt(sum(v * v for v in q)) or 1.0
    q_dir, q_setup, q_reg, q_sym = (query.get("direction"), query.get("setup_type"),
                                    query.get("regime"), query.get("symbol"))
    q_prof = feature_profile(query)
    for e in pool:
        row = rows_by_id.get(e.outcome_id) or {}
        feats = row.get("features") or row or {}
        try:
            x = to_vector(build_features(feats), names)
        except Exception:  # noqa: BLE001
            x = [0.0] * len(names)
        xn = math.sqrt(sum(v * v for v in x)) or 1.0
        cos = sum(a * b for a, b in zip(x, q)) / (xn * qn)
        bonus = 0.0
        bonus += 0.05 if (q_dir and e.direction == q_dir) else 0.0
        bonus += 0.05 if (q_setup and e.setup == q_setup) else 0.0
        bonus += 0.05 if (q_reg and e.regime == q_reg) else 0.0
        bonus += 0.05 if (q_sym and e.symbol == q_sym) else 0.0
        bonus += 0.03 if (e.feature_profile and e.feature_profile == q_prof) else 0.0
        e.similarity = round(cos + bonus, 6)


def build_pool(*, memory_rows: list[dict[str, Any]], shadow_trades: list[dict[str, Any]],
               query: dict[str, Any], as_of_ms: int | None, top_k: int = 5,
               shadow_weight: float = 0.25,
               shadow_fidelity: float = DEFAULT_SHADOW_FIDELITY) -> list[Experience]:
    """Gerçek + gölge deneyimlerden top-K havuz (deterministik, no-lookahead, dedup'lı)."""
    real = real_experiences(memory_rows, as_of_ms=as_of_ms)
    shad = shadow_experiences(shadow_trades, as_of_ms=as_of_ms,
                              weight=shadow_weight, fidelity=shadow_fidelity)
    pool = merge_experiences(real, shad)
    rows_by_id: dict[str, dict[str, Any]] = {}
    for r in memory_rows:
        o = r.get("outcome") or {}
        rows_by_id[_identity(r.get("symbol"), r.get("direction"), r.get("setup_type"),
                             o.get("opened_at") or r.get("opened_at"))] = r
    score_similarity(pool, rows_by_id, query)
    pool.sort(key=lambda e: (-(e.similarity if e.similarity is not None else -9.0), e.outcome_id))
    return pool[:max(1, int(top_k))]


# ---------------------------------------------------------------- ölçeklenebilir havuz

#: Aday başına taranacak deneyim ÜST SINIRI. Havuz bu sınırın altındaysa TAMAMI taranır
#: (davranış birebir eskisi gibidir); üstündeyse tarama sembol/yön kovaları + en yeni
#: kullanılabilir kuyruk ile SINIRLANIR. Böylece maliyet arşiv toplamıyla doğrusal büyümez.
DEFAULT_MAX_SCAN = 5_000


@dataclass
class PreparedPool:
    """Tur başına BİR KEZ hazırlanan deneyim havuzu + SINIRLI sorgu için erişim yapıları.

    Ölçüm (bu makinede, `build_pool` ile aday başına yeniden vektörleme): 100 deneyimde ~4 ms,
    1.000'de ~45 ms, 10.000'de ~455 ms. 20 adaylı bir turda 10k deneyim ~9 sn ederdi — worker
    15 dk aralıkla çalışsa da bu maliyet arşiv büyüdükçe doğrusal artardı. Bu yüzden deneyim
    VEKTÖRLERİ burada bir kez hesaplanır ve ek olarak `label_ts_ms`'e göre SIRALI erişim
    listeleri kurulur: aday başına yalnız sınırlı bir alt küme taranır.

    `order`/`by_symbol`/`by_symdir` listeleri `label_ts_ms` ARTAN sıradadır; sorgu `as_of`
    konumunu ikili aramayla bulup en yeni kullanılabilir kayıtlardan geriye doğru okur.
    """
    experiences: list[Experience] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)
    norms: list[float] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    signature: tuple | None = None
    order: list[int] = field(default_factory=list)              # label_ts artan indeksler
    label_ts: list[int] = field(default_factory=list)            # `order` ile hizalı zamanlar
    by_symbol: dict[str, list[int]] = field(default_factory=dict)
    by_symdir: dict[tuple, list[int]] = field(default_factory=dict)
    #: Arşiv geçmişinin sınırlı toplam defteri (bkz. aggregate_memory) — exemplar penceresi
    #: dışında kalan eski sonuçların kanıtı buradan gelir. None = yalnız exemplar kanalı.
    aggregate_book: Any = None

    def __len__(self) -> int:
        return len(self.experiences)

    def build_access(self) -> None:
        """Sıralı erişim yapılarını kurar (hazırlık aşamasında BİR KEZ)."""
        idx = [i for i, e in enumerate(self.experiences) if e.label_ts_ms is not None]
        idx.sort(key=lambda i: (int(self.experiences[i].label_ts_ms), self.experiences[i].outcome_id))
        self.order = idx
        self.label_ts = [int(self.experiences[i].label_ts_ms) for i in idx]
        by_sym: dict[str, list[int]] = {}
        by_sd: dict[tuple, list[int]] = {}
        for i in idx:
            e = self.experiences[i]
            by_sym.setdefault(str(e.symbol or ""), []).append(i)
            by_sd.setdefault((str(e.symbol or ""), str(e.direction or "")), []).append(i)
        self.by_symbol = by_sym
        self.by_symdir = by_sd


def prepare_pool(*, memory_rows: list[dict[str, Any]], shadow_trades: list[dict[str, Any]],
                 indexed_history: list[tuple[Experience, list[float], float]] | None = None,
                 aggregate_base: Any = None,
                 shadow_weight: float = 0.25,
                 shadow_fidelity: float = DEFAULT_SHADOW_FIDELITY,
                 names: list[str] | None = None) -> PreparedPool:
    """Deneyimleri VE vektörlerini bir kez hazırlar. `as_of` filtresi sorgu anında uygulanır.

    `indexed_history`: arşivden BİR KEZ normalize edilmiş uzun vadeli geçmiş
    (deneyim, vektör, norm). Aktif dosyalardan çıkmış gölge sonuçlar buradan gelir; aynı
    `outcome_id` aktifte de varsa TEK kez sayılır ve gerçek fill daima kazanır.
    """
    names = names or feature_names()
    real = real_experiences(memory_rows, as_of_ms=None)
    shad = shadow_experiences(shadow_trades, as_of_ms=None,
                              weight=shadow_weight, fidelity=shadow_fidelity)
    hist = list(indexed_history or [])
    stored = {id(e): (v, n) for e, v, n in hist}
    pool = merge_sources(real, shad, [e for e, _, _ in hist])
    rows_by_id = rows_by_identity(memory_rows)
    vecs: list[list[float]] = []
    norms: list[float] = []
    for e in pool:
        hit = stored.get(id(e))
        if hit is None:
            hit = experience_vector(e, rows_by_id, names)
        vecs.append(hit[0])
        norms.append(hit[1])
    prepared = PreparedPool(experiences=pool, vectors=vecs, norms=norms, names=names)
    prepared.aggregate_book = aggregate_base
    prepared.build_access()
    return prepared


def _scan_indices(prepared: PreparedPool, *, symbol: Any, direction: Any,
                  as_of_ms: int | None, max_scan: int) -> list[int]:
    """Taranacak deneyim indeksleri — SINIRLI ve deterministik.

    Havuz `max_scan`'in altındaysa TAMAMI döner: davranış eski (tam tarama) haliyle BİREBİR
    aynıdır. Üstündeyse bütçe şu sırayla dağıtılır ve her kaynaktan `as_of`'a göre EN YENİ
    kullanılabilir kayıtlar alınır (ikili arama + geriye okuma):

    1. aynı sembol + aynı yön  (en güçlü benzerlik sinyali)
    2. aynı sembol, yön farketmez
    3. genel en yeni kuyruk    (sembol ötesi analoglar kaybolmasın)

    Maliyet O(log N + max_scan)'dir; arşiv toplamıyla DOĞRUSAL BÜYÜMEZ.
    """
    # Sınırın altında TAM tarama: etiket zamanı olmayan kayıtlar dahil, eski davranış birebir.
    if len(prepared.experiences) <= max_scan:
        return list(range(len(prepared.experiences)))

    def _tail(idx_list: list[int], budget: int) -> list[int]:
        if budget <= 0 or not idx_list:
            return []
        if as_of_ms is None:
            return idx_list[-budget:]
        # `idx_list` label_ts artan sıralı → as_of'tan sonrası kesilir (no-lookahead ucuz)
        lo, hi = 0, len(idx_list)
        while lo < hi:
            mid = (lo + hi) // 2
            t = prepared.experiences[idx_list[mid]].label_ts_ms
            if t is not None and int(t) <= int(as_of_ms):
                lo = mid + 1
            else:
                hi = mid
        return idx_list[max(0, lo - budget):lo]

    sym, dirn = str(symbol or ""), str(direction or "")
    picked: list[int] = []
    seen: set[int] = set()
    for source, budget in ((prepared.by_symdir.get((sym, dirn)), max_scan // 2),
                           (prepared.by_symbol.get(sym), max_scan // 4),
                           (prepared.order, max_scan)):
        for i in _tail(source or [], budget):
            if i not in seen:
                seen.add(i)
                picked.append(i)
            if len(picked) >= max_scan:
                return picked
    return picked


def query_pool(prepared: PreparedPool, query: dict[str, Any], *, as_of_ms: int | None,
               top_k: int = 5, max_scan: int | None = DEFAULT_MAX_SCAN) -> list[Experience]:
    """Hazır havuzdan top-K — `as_of_ms` filtresi burada uygulanır (ucuz tamsayı karşılaştırması).

    NO-LOOKAHEAD: etiket/kapanış zamanı `as_of_ms`'ten sonra olan deneyim ELENİR; zamanı
    bilinmeyen de elenir (fail-closed).

    SINIRLI TARAMA: `max_scan` aday başına taranan deneyim üst sınırıdır (bkz. `_scan_indices`).
    `None` verilirse tam tarama yapılır (offline analiz için). Havuz sınırın altındaysa iki yol
    AYNI sonucu verir.
    """
    if not prepared.experiences:
        return []
    if not prepared.order and len(prepared.experiences) > (max_scan or 0):
        prepared.build_access()                      # elle kurulmuş havuzlar için (tembel)
    try:
        q = to_vector(build_features(query), prepared.names)
    except Exception:  # noqa: BLE001
        return []
    qn = math.sqrt(sum(v * v for v in q)) or 1.0
    q_dir, q_setup, q_reg, q_sym = (query.get("direction"), query.get("setup_type"),
                                    query.get("regime"), query.get("symbol"))
    q_prof = feature_profile(query)
    if max_scan is None:
        indices = range(len(prepared.experiences))
    else:
        indices = _scan_indices(prepared, symbol=q_sym, direction=q_dir,
                                as_of_ms=as_of_ms, max_scan=max(1, int(max_scan)))
    out: list[Experience] = []
    for i in indices:
        e = prepared.experiences[i]
        vec, xn = prepared.vectors[i], prepared.norms[i]
        if as_of_ms is not None:
            t = e.label_ts_ms
            if t is None or t > int(as_of_ms):
                continue
        cos = sum(a * b for a, b in zip(vec, q)) / (xn * qn)
        bonus = 0.0
        bonus += 0.05 if (q_dir and e.direction == q_dir) else 0.0
        bonus += 0.05 if (q_setup and e.setup == q_setup) else 0.0
        bonus += 0.05 if (q_reg and e.regime == q_reg) else 0.0
        bonus += 0.05 if (q_sym and e.symbol == q_sym) else 0.0
        bonus += 0.03 if (e.feature_profile and e.feature_profile == q_prof) else 0.0
        hit = Experience(**{**e.__dict__})
        hit.similarity = round(cos + bonus, 6)
        out.append(hit)
    out.sort(key=lambda x: (-(x.similarity if x.similarity is not None else -9.0), x.outcome_id))
    return out[:max(1, int(top_k))]
