"""Değişmezlik parmak izleri (`fingerprint_v1`) — TEK OTORİTE, vacuous kanıt üretmez.

Deployment denetimlerinde "pozisyonlar değişmedi" iddiası bir hash'e dayanır. O hash yanlış
alan kümesi üzerinden alınırsa iddia **boş** olur ve bunu kimse fark etmez. İki gerçek örnek:

* `take_profit` — futures pozisyon nesnesinde **BÖYLE BİR ALAN YOKTUR**
  (`accounting.models.Position`). Hedefler `targets` + `targets_hit` + `tp1_done` içindedir.
  Olmayan bir anahtarı projeksiyona koymak, her pozisyon için sabit `None` hash'lemek demektir:
  hash değişmez ama hiçbir şey kanıtlamaz.
* Spot defterinde `positions` anahtarı **YOKTUR**; varlıklar `assets`/`lots` altındadır.
  Boş sözlük üzerinden hash almak aynı hatadır.

Bu modül alan kümesini `Position` dataclass'ından **türetir ve doğrular**. Zorunlu bir alan
kaynakta yoksa `FingerprintError` yükselir — sessizce `None` hash'lenmez.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "fingerprint_v1"


class FingerprintError(Exception):
    """Zorunlu alan yok ya da projeksiyon boş — parmak izi KANIT SAYILMAZ."""


#: Futures pozisyonunun değişmezlik yüzeyi. Hepsi `accounting.models.Position` alanıdır;
#: `_assert_schema()` bunu her çağrıda doğrular. `take_profit` BİLİNÇLİ OLARAK YOKTUR.
FUTURES_POSITION_FIELDS: tuple[str, ...] = (
    "side", "qty", "entry_avg", "stop", "targets", "targets_hit", "leverage",
    "isolated_margin", "tp1_done", "initial_stop", "initial_qty",
)

#: Pozisyon ekonomisi — mark'tan BAĞIMSIZ, gerçekleşmiş büyüklükler.
FUTURES_ECONOMICS_FIELDS: tuple[str, ...] = (
    "realized_pnl", "fees_paid", "funding_paid", "funding_received", "entry_fee", "exit_fee",
    "slippage_cost",
)

#: Spot defteri yüzeyi. `positions` BİLİNÇLİ OLARAK YOKTUR (defterde böyle bir anahtar yok).
SPOT_LEDGER_FIELDS: tuple[str, ...] = (
    "assets", "lots", "locked_assets", "position_meta", "cash", "open_orders",
)

#: Bu alanların hepsi eksikse projeksiyon vacuous'tur; kimlik yüzeyi tamamen boş demektir.
_FUT_CORE = ("side", "qty", "entry_avg")


def _position_field_names() -> set[str]:
    """`Position` dataclass alan adları. Import edilemezse boş küme (denetim atlanmaz, uyarılır)."""
    try:
        from dataclasses import fields as _fields

        from ..accounting.models import Position
        return {f.name for f in _fields(Position)}
    except Exception:  # noqa: BLE001 — şema doğrulaması denetimi ÇÖKERTMEZ
        return set()


def assert_schema() -> dict[str, Any]:
    """Alan kümelerinin gerçekten `Position` şemasında olduğunu doğrular.

    Şema değişir de bir alan kalkarsa bu fonksiyon patlar; parmak izi sessizce anlamsızlaşmaz.
    """
    known = _position_field_names()
    out = {"schema_source": "accounting.models.Position", "known_fields": len(known),
           "verified": bool(known)}
    if not known:
        out["note"] = "Position şeması okunamadı — alan doğrulaması YAPILAMADI"
        return out
    for name, group in (("FUTURES_POSITION_FIELDS", FUTURES_POSITION_FIELDS),
                        ("FUTURES_ECONOMICS_FIELDS", FUTURES_ECONOMICS_FIELDS)):
        unknown = [f for f in group if f not in known]
        if unknown:
            raise FingerprintError(
                f"{name} içinde `Position` şemasında OLMAYAN alan(lar): {unknown}. "
                "Olmayan alan hash'lemek vacuous kanıt üretir.")
    if "take_profit" in FUTURES_POSITION_FIELDS:
        raise FingerprintError("`take_profit` gerçek bir pozisyon alanı DEĞİLDİR")
    out["futures_fields"] = list(FUTURES_POSITION_FIELDS)
    out["economics_fields"] = list(FUTURES_ECONOMICS_FIELDS)
    return out


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _as_map(p: Any) -> Mapping[str, Any]:
    if isinstance(p, Mapping):
        return p
    if hasattr(p, "to_dict"):
        try:
            d = p.to_dict()
            if isinstance(d, Mapping):
                return d
        except Exception:  # noqa: BLE001
            pass
    return {k: getattr(p, k) for k in dir(p) if not k.startswith("_")}


def futures_fingerprint(positions: Mapping[str, Any] | Iterable[Any], *,
                        fields: tuple[str, ...] = FUTURES_POSITION_FIELDS,
                        require_all: bool = True) -> dict[str, Any]:
    """Açık futures pozisyonlarının değişmezlik parmak izi.

    Boş defter geçerlidir (`n_positions=0`, `vacuous=True` ile açıkça işaretlenir): kanıt
    olarak kullanılamaz ama hata da değildir. Pozisyon VARSA ve zorunlu alanlar eksikse
    `FingerprintError`.
    """
    assert_schema()
    items = (sorted(positions.items()) if isinstance(positions, Mapping)
             else sorted(((str(_as_map(p).get("symbol") or i), p)
                          for i, p in enumerate(positions)), key=lambda kv: kv[0]))
    canon: dict[str, dict[str, Any]] = {}
    missing: dict[str, list[str]] = {}
    for sym, pos in items:
        m = _as_map(pos)
        absent = [f for f in fields if f not in m]
        if absent:
            missing[str(sym)] = absent
        canon[str(sym)] = {f: m.get(f) for f in fields}
    if missing and require_all:
        raise FingerprintError(
            f"parmak izi alanları pozisyonlarda YOK: {missing}. Eksik alanı `None` sayıp "
            "hash almak, ölçmediğini ölçtün gibi göstermektir.")
    core_present = all(
        all(f in _as_map(p) for f in _FUT_CORE) for _, p in items) if items else False
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "futures_positions",
        "fingerprint": _digest(canon),
        "n_positions": len(canon),
        "fields_used": list(fields),
        "missing_fields": missing,
        # Boş defter ya da çekirdek alanların yokluğu → bu hash KANIT DEĞİLDİR.
        "vacuous": bool(not canon or not core_present),
        "symbols": sorted(canon.keys()),
    }


def futures_economics_fingerprint(positions: Mapping[str, Any] | Iterable[Any]) -> dict[str, Any]:
    """Gerçekleşmiş ekonomi parmak izi (mark hareketinden ETKİLENMEZ)."""
    return futures_fingerprint(positions, fields=FUTURES_ECONOMICS_FIELDS) | {
        "kind": "futures_economics"}


def spot_fingerprint(ledger: Mapping[str, Any], *,
                     fields: tuple[str, ...] = SPOT_LEDGER_FIELDS) -> dict[str, Any]:
    """Spot defteri parmak izi. `positions` anahtarı BEKLENMEZ — yoksa bu bir hata değildir.

    Beklenen anahtarların HİÇBİRİ yoksa `vacuous=True`: boş bir sözlüğün hash'i kanıt değildir.
    """
    if not isinstance(ledger, Mapping):
        raise FingerprintError("spot defteri sözlük değil")
    present = [f for f in fields if f in ledger]
    canon = {f: ledger.get(f) for f in fields}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "spot_ledger",
        "fingerprint": _digest(canon),
        "fields_used": list(fields),
        "fields_present": present,
        "missing_fields": [f for f in fields if f not in ledger],
        "has_positions_key": "positions" in ledger,
        "vacuous": not present,
    }


def capital_fingerprint(futures_ledger: Mapping[str, Any], spot_ledger: Mapping[str, Any],
                        adjustments: Any = None) -> dict[str, Any]:
    """Katkı sermayesi parmak izi — `starting_equity` + uygulanmış düzeltmeler."""
    canon = {
        "futures_starting_equity": (futures_ledger or {}).get("starting_equity"),
        "spot_starting_equity": (spot_ledger or {}).get("starting_equity"),
        "adjustments": adjustments,
    }
    known = [k for k, v in canon.items() if v is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "contributed_capital",
        "fingerprint": _digest(canon),
        "fields_present": known,
        "vacuous": not known,
    }


__all__ = ["SCHEMA_VERSION", "FingerprintError", "FUTURES_POSITION_FIELDS",
           "FUTURES_ECONOMICS_FIELDS", "SPOT_LEDGER_FIELDS", "assert_schema",
           "futures_fingerprint", "futures_economics_fingerprint", "spot_fingerprint",
           "capital_fingerprint"]
