"""Olasılık semantiği — TEK sonuçtan "model haklıydı / yanıldı" ÇIKARILAMAZ.

Sorun: hem `learning.py::_diagnose` hem `learn/postmortem.py` şu cümleyi üretiyordu:

    "P(kazanç)=%29 demişti → yanıldı"   (işlem kazandığı için)
    "P(kazanç)=%45 demişti → isabetli"  (işlem kaybettiği için)

Bu olasılık bilimi açısından YANLIŞTIR. %29 olasılıklı bir olay yaklaşık her üç denemenin
birinde gerçekleşir; gerçekleşmesi tahmini yanlışlamaz. Bir olasılık tahmini ancak ÇOK sayıda
tahminin toplu davranışıyla (kalibrasyon) değerlendirilebilir.

Bu modül tek sonucu şu şekilde kaydeder:

* `brier_contribution`   — (p − y)², bu tek sonucun toplam Brier'e katkısı,
* `log_loss_contribution`— −[y·ln p + (1−y)·ln(1−p)], aynı şekilde katkı,
* `calibration_bucket`   — tahminin düştüğü güvenilirlik kovası,
* `bucket_n`             — o kovadaki (no-lookahead) örnek sayısı,
* `shrunk_observed_rate` — kova gözlemi, kova orta noktasına doğru büzülmüş,
* `ci_low` / `ci_high`   — Wilson skor aralığı,
* `surprise_bits`        — −log2 P(gerçekleşen sonuç); sürpriz ÖLÇÜSÜ, hata ölçüsü DEĞİL.

Ve YALNIZ şu dört koddan birini verir:

    HIGH_SURPRISE_OUTCOME | LOW_SURPRISE_OUTCOME
    CALIBRATION_EVIDENCE_ADDED | INSUFFICIENT_CALIBRATION_SAMPLE

`MODEL_WAS_RIGHT` / `MODEL_WAS_WRONG` üretilmez; `FORBIDDEN_VERDICTS` bunu testte sabitler.

No-lookahead: `CalibrationBook.add()` yalnız `as_of`tan ÖNCE etiketlenmiş sonuçları alır ve
aynı `trade_id` iki kez sayılmaz. Gerçek PAPER sonucu gölgeden ağırdır; gölge tek başına bir
kovayı "yeterli örnek" yapamaz (`real_n` ayrı sayılır ve yeterlilik ondan okunur).
"""
from __future__ import annotations

import math
from typing import Any, Iterable

SCHEMA_VERSION = "prob_semantics_v1"

#: Tek sonuçtan ASLA üretilmeyecek hükümler — test bu listeyi tarar.
FORBIDDEN_VERDICTS = ("MODEL_WAS_RIGHT", "MODEL_WAS_WRONG")

HIGH_SURPRISE = "HIGH_SURPRISE_OUTCOME"
LOW_SURPRISE = "LOW_SURPRISE_OUTCOME"
EVIDENCE_ADDED = "CALIBRATION_EVIDENCE_ADDED"
INSUFFICIENT = "INSUFFICIENT_CALIBRATION_SAMPLE"

#: Bir kovanın "değerlendirilebilir" sayılması için gereken asgari GERÇEK sonuç sayısı.
#: fail-closed: altındaysa kova hükmü yoktur, yalnız katkı kaydedilir.
MIN_BUCKET_SAMPLE = 20

#: Sürpriz eşiği (bit). p_realized < 2^-1.75 ≈ %29.7 → yüksek sürpriz.
HIGH_SURPRISE_BITS = 1.75

#: Kova orta noktasına büzme gücü (beta-binom prior kütlesi).
BUCKET_PRIOR_STRENGTH = 10.0

#: Varsayılan güvenilirlik kova kenarları (10 eşit kova).
DEFAULT_EDGES = tuple(round(i / 10, 2) for i in range(11))

#: Gölge sonucun gerçek fill'e göre azami kanıt ağırlığı — 1.0 OLAMAZ.
MAX_SHADOW_WEIGHT = 0.5

REAL, SHADOW = "REAL_PAPER", "SHADOW"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ms(v: Any) -> int | None:
    """ISO ya da epoch-ms → epoch-ms. Çözülemezse None (no-lookahead kapısı fail-closed)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v) if math.isfinite(float(v)) else None
    if isinstance(v, str) and len(v) >= 10:
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return None
    return None


def bucket_index(p: float, edges: tuple[float, ...] = DEFAULT_EDGES) -> int:
    """p → kova indeksi. Son kova sağdan KAPALI ([0.9, 1.0])."""
    n = len(edges) - 1
    for i in range(n):
        hi = edges[i + 1]
        if p < hi or (i == n - 1 and p <= hi):
            return i
    return n - 1


def bucket_label(i: int, edges: tuple[float, ...] = DEFAULT_EDGES) -> str:
    return f"[{edges[i]:.2f},{edges[i + 1]:.2f}{']' if i == len(edges) - 2 else ')'}"


def brier_contribution(p: float, y: float) -> float:
    return (p - y) ** 2


def log_loss_contribution(p: float, y: float, eps: float = 1e-7) -> float:
    q = min(max(p, eps), 1 - eps)
    return -(y * math.log(q) + (1 - y) * math.log(1 - q))


def surprise_bits(p: float, y: float, eps: float = 1e-7) -> float:
    """−log2 P(gerçekleşen sonuç). SÜRPRİZ ölçüsüdür; "model yanıldı" DEMEK DEĞİLDİR."""
    realized = p if y >= 0.5 else (1 - p)
    return -math.log2(min(max(realized, eps), 1.0))


def wilson_interval(k: float, n: float, z: float = 1.96) -> tuple[float | None, float | None]:
    """Wilson skor aralığı — küçük n'de normal yaklaşımdan çok daha dürüst."""
    if n is None or n <= 0:
        return None, None
    phat = k / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(max(phat * (1 - phat) / n + z * z / (4 * n * n), 0.0)) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def shrunk_rate(k: float, n: float, prior_p: float, prior_strength: float = BUCKET_PRIOR_STRENGTH) -> float:
    """Beta-binom büzme: az örnekte kova ORTA NOKTASINA yakın kalır."""
    return (k + prior_strength * prior_p) / (n + prior_strength)


class CalibrationBook:
    """No-lookahead güvenilirlik kovaları. Yalnız GEÇMİŞ etiketlenmiş sonuçları toplar.

    Sözleşme:
    * `add()` aynı `trade_id`yi ikinci kez saymaz (duplicate outcome bir kez).
    * `label_ts_ms` yoksa ya da `as_of_ms`tan sonraysa kayıt ALINMAZ (fail-closed).
    * Gölge sonuç `weight < 1` ile girer ve `real_n`e SAYILMAZ; yeterlilik yalnız `real_n`den okunur.
    * Kova sayısı sabittir — yüksek kardinalite oluşamaz.
    """

    def __init__(self, *, edges: tuple[float, ...] = DEFAULT_EDGES,
                 min_bucket_sample: int = MIN_BUCKET_SAMPLE,
                 shadow_weight: float = 0.25) -> None:
        if shadow_weight >= MAX_SHADOW_WEIGHT or shadow_weight < 0:
            raise ValueError(f"shadow_weight [0, {MAX_SHADOW_WEIGHT}) olmalı — gölge gerçek fill'e eşit sayılamaz")
        self.edges = tuple(edges)
        self.min_bucket_sample = int(min_bucket_sample)
        self.shadow_weight = float(shadow_weight)
        self._n = len(self.edges) - 1
        # kova → {"n": ağırlıklı toplam, "k": ağırlıklı kazanç, "real_n": gerçek adet,
        #          "real_k": gerçek kazanç, "p_sum": ağırlıklı p toplamı}
        self._b: list[dict[str, float]] = [
            {"n": 0.0, "k": 0.0, "real_n": 0.0, "real_k": 0.0, "p_sum": 0.0} for _ in range(self._n)]
        self._seen: set[str] = set()
        self.rejected_future = 0
        self.rejected_duplicate = 0
        self.rejected_invalid = 0

    # ------------------------------------------------------------------ yazım
    def add(self, *, trade_id: Any, p: Any, won: Any, label_ts: Any = None,
            as_of_ms: int | None = None, source: str = REAL) -> bool:
        """Tek sonucu havuza ekler. Kabul edilirse True.

        `as_of_ms` verildiyse `label_ts` bilinmiyor ya da `as_of_ms`tan büyükse REDDEDİLİR —
        gelecekteki sonuç bugünkü kalibrasyona giremez.
        """
        pv = _f(p)
        if pv is None or not (0.0 <= pv <= 1.0) or won is None:
            self.rejected_invalid += 1
            return False
        tid = str(trade_id or "")
        if not tid:
            self.rejected_invalid += 1
            return False
        key = f"{source}:{tid}"
        if key in self._seen or (source == SHADOW and f"{REAL}:{tid}" in self._seen):
            self.rejected_duplicate += 1
            return False
        if as_of_ms is not None:
            ts = _ms(label_ts)
            if ts is None or ts > as_of_ms:
                self.rejected_future += 1
                return False
        y = 1.0 if bool(won) else 0.0
        w = 1.0 if source == REAL else self.shadow_weight
        cell = self._b[bucket_index(pv, self.edges)]
        cell["n"] += w
        cell["k"] += w * y
        cell["p_sum"] += w * pv
        if source == REAL:
            cell["real_n"] += 1.0
            cell["real_k"] += y
        self._seen.add(key)
        return True

    def add_many(self, rows: Iterable[dict[str, Any]], *, as_of_ms: int | None = None) -> int:
        n = 0
        for r in rows or ():
            if self.add(trade_id=r.get("trade_id") or r.get("id"), p=r.get("p_win", r.get("p")),
                        won=r.get("won"), label_ts=r.get("label_ts") or r.get("closed_at"),
                        as_of_ms=as_of_ms, source=str(r.get("source") or REAL)):
                n += 1
        return n

    # ------------------------------------------------------------------ okuma
    def bucket_stats(self, p: Any) -> dict[str, Any]:
        """Bir tahminin düştüğü kovanın (no-lookahead) durumu."""
        pv = _f(p)
        if pv is None or not (0.0 <= pv <= 1.0):
            return {"bucket": None, "n": 0, "real_n": 0, "sufficient": False,
                    "state": "INVALID_PREDICTION"}
        i = bucket_index(pv, self.edges)
        c = self._b[i]
        mid = (self.edges[i] + self.edges[i + 1]) / 2
        real_n = int(c["real_n"])
        sufficient = real_n >= self.min_bucket_sample
        lo, hi = wilson_interval(c["real_k"], real_n) if real_n else (None, None)
        return {"bucket": bucket_label(i, self.edges), "bucket_index": i,
                "bucket_mid": round(mid, 4),
                "n": round(c["n"], 4), "real_n": real_n,
                "observed_win_rate": (round(c["real_k"] / real_n, 4) if real_n else None),
                "shrunk_observed_rate": round(shrunk_rate(c["real_k"], real_n, mid), 4),
                "mean_predicted_p": (round(c["p_sum"] / c["n"], 4) if c["n"] else None),
                "ci95_low": lo, "ci95_high": hi,
                "sufficient": sufficient,
                "min_bucket_sample": self.min_bucket_sample,
                "state": "OK" if sufficient else INSUFFICIENT}

    def reliability(self) -> list[dict[str, Any]]:
        out = []
        for i, c in enumerate(self._b):
            if c["n"] <= 0:
                continue
            real_n = int(c["real_n"])
            lo, hi = wilson_interval(c["real_k"], real_n) if real_n else (None, None)
            out.append({"bucket": bucket_label(i, self.edges), "bucket_index": i,
                        "n": round(c["n"], 4), "real_n": real_n,
                        "mean_predicted_p": round(c["p_sum"] / c["n"], 4),
                        "observed_win_rate": (round(c["real_k"] / real_n, 4) if real_n else None),
                        "shrunk_observed_rate": round(
                            shrunk_rate(c["real_k"], real_n, (self.edges[i] + self.edges[i + 1]) / 2), 4),
                        "ci95_low": lo, "ci95_high": hi,
                        "sufficient": real_n >= self.min_bucket_sample})
        return out

    def ece(self) -> float | None:
        """Expected Calibration Error — yalnız GERÇEK sonuçlar üzerinden."""
        rows = [b for b in self.reliability() if b["real_n"] > 0]
        tot = sum(b["real_n"] for b in rows)
        if not tot:
            return None
        return round(sum(b["real_n"] / tot * abs(b["mean_predicted_p"] - b["observed_win_rate"])
                         for b in rows), 5)

    def stats(self) -> dict[str, Any]:
        rows = self.reliability()
        real_total = int(sum(b["real_n"] for b in rows))
        return {"schema_version": SCHEMA_VERSION, "buckets": rows,
                "n_real": real_total, "n_weighted": round(sum(b["n"] for b in rows), 4),
                "n_sufficient_buckets": sum(1 for b in rows if b["sufficient"]),
                "ece": self.ece(),
                "rejected_future": self.rejected_future,
                "rejected_duplicate": self.rejected_duplicate,
                "rejected_invalid": self.rejected_invalid,
                "min_bucket_sample": self.min_bucket_sample,
                "shadow_weight": self.shadow_weight}


def outcome_probability_evidence(p: Any, won: Any, *, book: CalibrationBook | None = None,
                                 trade_id: Any = None, as_of: Any = None,
                                 data_available: bool = True) -> dict[str, Any]:
    """TEK sonucun kalibrasyon kanıtı. HÜKÜM İÇERMEZ — yalnız katkı ve sürpriz ölçüsü.

    `book` verilmişse kova bağlamı da eklenir; kova yetersizse `INSUFFICIENT_CALIBRATION_SAMPLE`.
    """
    pv = _f(p)
    base: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "trade_id": (str(trade_id) if trade_id else None),
                            "predicted_p": pv, "as_of": as_of,
                            "single_outcome_verdict": False,
                            "causal_claim": False,
                            "data_available": bool(data_available)}
    if pv is None or not (0.0 <= pv <= 1.0) or won is None or not data_available:
        return base | {"codes": [INSUFFICIENT], "outcome": None, "brier_contribution": None,
                       "log_loss_contribution": None, "surprise_bits": None,
                       "bucket": None,
                       "note_tr": "tahmin ya da sonuç yok/geçersiz — kalibrasyon kanıtı eklenemedi"}
    y = 1.0 if bool(won) else 0.0
    bits = surprise_bits(pv, y)
    codes = [HIGH_SURPRISE if bits >= HIGH_SURPRISE_BITS else LOW_SURPRISE]
    bstat = book.bucket_stats(pv) if book is not None else None
    if bstat is None:
        codes.append(INSUFFICIENT)
    else:
        codes.append(EVIDENCE_ADDED if bstat.get("sufficient") else INSUFFICIENT)
    return base | {
        "outcome": "WIN" if y else "LOSS",
        "brier_contribution": round(brier_contribution(pv, y), 6),
        "log_loss_contribution": round(log_loss_contribution(pv, y), 6),
        "surprise_bits": round(bits, 4),
        "high_surprise_threshold_bits": HIGH_SURPRISE_BITS,
        "bucket": bstat,
        "codes": codes,
        "note_tr": (f"P(kazanç)=%{pv * 100:.0f} tahmini ile sonuç "
                    f"{'KAZANÇ' if y else 'KAYIP'}: sürpriz {bits:.2f} bit. "
                    "Tek sonuç modeli doğrulamaz ya da yanlışlamaz; "
                    "yalnız kalibrasyon havuzuna kanıt ekler."),
    }


def probability_note_tr(ev: dict[str, Any]) -> str:
    """Ders metnine giren tek satır — 'isabetli/yanıldı' İÇERMEZ."""
    if not ev or ev.get("predicted_p") is None:
        return "Giriş öncesi P(kazanç) kaydı yok — kalibrasyon kanıtı eklenemedi."
    pv, bits = float(ev["predicted_p"]), ev.get("surprise_bits")
    b = ev.get("bucket") or {}
    tail = (f"kova {b.get('bucket')} n={b.get('real_n')} "
            f"(gözlem %{(b.get('shrunk_observed_rate') or 0) * 100:.0f}, büzülmüş)"
            if b.get("bucket") else "kova bağlamı yok")
    verdict = ("YETERSİZ ÖRNEK — bu kova için henüz hüküm yok"
               if INSUFFICIENT in (ev.get("codes") or []) else "kalibrasyon kanıtı eklendi")
    return (f"Giriş öncesi P(kazanç)=%{pv * 100:.0f}; sonuç {ev.get('outcome')}. "
            f"Sürpriz {bits:.2f} bit, Brier katkısı {ev.get('brier_contribution')}. "
            f"{tail}. {verdict}. Tek sonuç modeli doğrulamaz/yanlışlamaz.")
