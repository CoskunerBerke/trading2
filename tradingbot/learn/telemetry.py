"""Snapshot / model sema telemetrisi — arastirma snapshot'i canli islemi BOZMAZ ama SESSIZ de kalmaz.

Neden: `_snapshot_v3` hatalari yalniz `log.warning` ile goruluyordu; operator ne kadar kaydin eksik
uretildigini olcemiyordu. Bu modul sayaclari atomik bir JSON'da tutar; dashboard `/health`, `/metrics`
ve `/api/overview` uzerinden okur.

Sozlesme:
* Sayaclar yalniz ARTAR (monoton); surec yeniden baslarsa diskten devam eder.
* Hata kodu KISA ve sanitize edilir: `TipAdi: ilk 120 karakter`, satir sonlari temizlenir.
  Secret, ham env, tam payload ya da stack trace YAZILMAZ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core import atomic_write_json, iso, read_json, utc_now

COUNTER_NAMES = ("snapshot_success_total", "snapshot_failure_total", "leakage_failure_total",
                 "schema_mismatch_total")
TELEMETRY_FILENAME = "snapshot_telemetry.json"
_MAX_CODE_LEN = 120


def sanitize_code(exc_or_text: object) -> str:
    """Kisa, tek satirlik, sinirli uzunlukta hata kodu. Payload/secret sizdirmaz."""
    if isinstance(exc_or_text, BaseException):
        text = f"{type(exc_or_text).__name__}: {exc_or_text}"
    else:
        text = str(exc_or_text or "")
    text = " ".join(text.split())                      # satir sonu/fazla bosluk temizligi
    return text[:_MAX_CODE_LEN]


@dataclass
class SnapshotTelemetry:
    """Snapshot uretim sayaclari. `path` verilmezse yalniz bellekte tutulur (testler icin)."""
    path: Path | None = None
    counters: dict[str, int] = field(default_factory=lambda: {k: 0 for k in COUNTER_NAMES})
    last_failure_code: str = ""
    last_failure_at: str = ""

    @classmethod
    def load(cls, state_path: Path | str | None) -> "SnapshotTelemetry":
        if not state_path:
            return cls()
        p = Path(state_path) / TELEMETRY_FILENAME
        d = read_json(p, default=None)
        t = cls(path=p)
        if isinstance(d, dict):
            for k in COUNTER_NAMES:
                t.counters[k] = int((d.get("counters") or {}).get(k, 0) or 0)
            t.last_failure_code = str(d.get("last_failure_code") or "")
            t.last_failure_at = str(d.get("last_failure_at") or "")
        return t

    # ------------------------------------------------------------ olaylar
    def success(self) -> None:
        self.counters["snapshot_success_total"] += 1

    def failure(self, exc_or_text: object, *, leakage: bool = False) -> None:
        self.counters["snapshot_failure_total"] += 1
        if leakage:
            self.counters["leakage_failure_total"] += 1
        self.last_failure_code = sanitize_code(exc_or_text)
        self.last_failure_at = iso(utc_now())

    def schema_mismatch(self, exc_or_text: object) -> None:
        """Model artifact semasi ile uretilen vektor semasi uyusmadi -> model KULLANILMADI."""
        self.counters["schema_mismatch_total"] += 1
        self.last_failure_code = sanitize_code(exc_or_text)
        self.last_failure_at = iso(utc_now())

    # ------------------------------------------------------------ cikti
    def to_dict(self) -> dict:
        return {"schema": "snapshot_telemetry_v1", "updated_at": iso(utc_now()),
                "counters": dict(self.counters), "last_failure_code": self.last_failure_code,
                "last_failure_at": self.last_failure_at}

    def save(self) -> None:
        """Diskteki sayaclarla BIRLESTIREREK yazar (her sayac icin max).

        Ayni surecte iki yazar var: motor (`snapshot_*`/`leakage_*`) ve LearnerV2 (`schema_mismatch_*`).
        Duz ustune yazma, digerinin artisini sessizce silerdi. Sayaclar monoton oldugu icin `max`
        birlestirmesi dogru sonucu verir; `last_failure_*` daha YENI olan kaydi korur.
        """
        if self.path is None:
            return
        prev = read_json(self.path, default=None)
        if isinstance(prev, dict):
            pc = prev.get("counters") or {}
            for k in COUNTER_NAMES:
                self.counters[k] = max(int(self.counters.get(k, 0) or 0), int(pc.get(k, 0) or 0))
            prev_at = str(prev.get("last_failure_at") or "")
            if prev_at > self.last_failure_at:                  # ISO-8601: sozluk sirasi = zaman sirasi
                self.last_failure_at = prev_at
                self.last_failure_code = str(prev.get("last_failure_code") or "")
        atomic_write_json(self.path, self.to_dict())


def read_telemetry(state_path: Path | str | None) -> dict:
    """Dashboard/CLI icin salt-okunur ozet; dosya yoksa sifir sayaclar."""
    empty = {"counters": {k: 0 for k in COUNTER_NAMES}, "last_failure_code": "", "last_failure_at": ""}
    if not state_path:
        return empty
    d = read_json(Path(state_path) / TELEMETRY_FILENAME, default=None)
    if not isinstance(d, dict):
        return empty
    return {"counters": {k: int((d.get("counters") or {}).get(k, 0) or 0) for k in COUNTER_NAMES},
            "last_failure_code": str(d.get("last_failure_code") or ""),
            "last_failure_at": str(d.get("last_failure_at") or "")}


__all__ = ["COUNTER_NAMES", "SnapshotTelemetry", "TELEMETRY_FILENAME", "read_telemetry", "sanitize_code"]
