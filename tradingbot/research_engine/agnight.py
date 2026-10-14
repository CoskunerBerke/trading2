"""P3g — S1d `LIB_AG_V1`'in gece bağlantısı (docs/ENGINE_BRAIN_V1.md §8.6 S1d "P3g'nin çağıracağı EKLEME API'si", §8.4
P3g notu). Yalnız PAPER araştırma kodu: ağsız, kayıt-yalnız; terfi yok, hüküm yok. LIB_V1 yolları DEĞİŞMEDİ; AG bu modül ve
`lib_ag` üzerinden aynı mühürlü makineye (çekirdek, maliyet, fonlama mark'ı, WF katmanları, istatistik) bağlanır.

* **Her gece önce mühür:** `seal_s1d.check()` (→ S1c → S1b). Tutmazsa AG o gece atlanır (`AG_SKIPPED`, açık neden);
  LIB_V1 işleri etkilenmez.
* **Deneme kaydı, sonuçlardan ÖNCE:** AG'nin 38 `SPEC` satırı (`lib_ag.seal_entries(lib_ag.module_src_shas())`) deneme
  defterine eklenir (`TrialsLedger.append`; aynı spec ikinci kez sayılmaz → gece başına bir çağrı, ilk geceden sonra hep
  `DUPLICATE`). Ekleme yapılamazsa (zincir kırık, git kimliği yok) AG o gece KOŞMAZ (`AG_TRIALS_NOT_REGISTERED`): hiçbir
  AG sonucu, denemesi defterde sayılmadan üretilmez.
* **Koşu grupları (S4w birimi ve S4x lockstep grubu):** aile × sinyal dilimi (P3 5m/15m ayrı); örtüler taban başına
  (`AGO_<taban>`, 3 varyant). Sembol başına 14 grup; 41 sembol (S1b'nin 40 coin'i + XAUUSDT).
* **Kapı A ailesi:** `registry.gate_a_rows()` + `lib_ag.gate_a_rows()` (818 + 38 = 856; m büyür → BH daha sıkı, asla
  gevşek). Seçim grupları `lib_ag.selection_group_key`, plasebo `placebo.KernelPlacebo(lib_ag.make_variant(vid), data)`,
  takvim penceresi `lib_ag.cal_flag`. Kapı A satır kurucuları ve bakışın kendisi P4a'dadır (bu sürümde HESAPLANMADI).
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Mapping, Sequence

from . import lib_ag as LA
from . import registry as RG
from . import seal_s1d as S1D

F_AG_SKIPPED = "AG_SKIPPED"
F_AG_TRIALS = "AG_TRIALS_NOT_REGISTERED"
LIB_VERSION = LA.LIB_VERSION
SCOPE = LA.SCOPE
#: S4w değerlendirme biriminin adı (bütün sembol birimlerinden sonra; varyant başına `lib_ag.evaluate`)
EVAL_UNIT = f"AG_EVAL@{SCOPE}"


# ============================================================================ mühür ve evren
def status(entry_symbols: Iterable[Any] | None = None) -> dict[str, Any]:
    """Gecenin AG durumu: S1d mührü (S1c/S1b dahil) ve AG evreni (S1b'nin 40'ı + XAUUSDT; giriş evreni farkı varsa boş)."""
    ok, why = S1D.check()
    out: dict[str, Any] = {"seal_ok": bool(ok), "seal": why if not ok else "OK", "s1d_sha": S1D.S1D_SHA,
                           "s1d_seal_ms": int(S1D.S1D_SEAL_MS), "lib": LIB_VERSION, "variants": len(LA.variant_ids())}
    if entry_symbols is not None:
        d = LA.universe_diff(entry_symbols)
        out["symbols"] = list(d["ag_symbols"])
        out["universe_ok"] = bool(d["ok"])
    else:
        out["symbols"] = LA.universe_symbols()
        out["universe_ok"] = True
    out["ok"] = bool(ok) and bool(out["symbols"])
    if not out["ok"]:
        out["reason"] = (f"S1D_SEAL: {why}" if not ok else "AG evreni yok (giriş evreni S1b'nin 40'ıyla aynı değil)")[:300]
    return out


def coins(symbols: Sequence[str] | None = None) -> list[str]:
    from .universe import coin_of
    return [coin_of(s) for s in (LA.universe_symbols() if symbols is None else symbols)]


# ============================================================================ deneme kaydı
def module_src_shas() -> dict[str, str]:
    """AG varyantlarının `module_src_sha`'sı (salt-okunur git; `GIT_OPTIONAL_LOCKS=0` — engine-app ProtectSystem=strict)."""
    old = os.environ.get("GIT_OPTIONAL_LOCKS")
    os.environ["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        return LA.module_src_shas()
    finally:
        if old is None:
            os.environ.pop("GIT_OPTIONAL_LOCKS", None)
        else:
            os.environ["GIT_OPTIONAL_LOCKS"] = old


def register_trials(paths: Any, *, module_src_sha: Mapping[str, str] | None = None) -> dict[str, Any]:
    """AG'nin 38 `SPEC` satırını deftere ekle (tekilleştirilmiş). Dönen `ok` yanlışsa AG o gece koşmaz."""
    from .trials import TrialsLedger
    try:
        ms = dict(module_src_sha) if module_src_sha is not None else module_src_shas()
        entries = LA.seal_entries(ms)
        res = TrialsLedger(paths).append(entries)
    except Exception as exc:  # noqa: BLE001 — zincir kırık, git yok, geçersiz satır: AG bu gece koşmaz
        return {"ok": False, "status": "FAILED", "reason": f"{type(exc).__name__}: {exc}"[:300]}
    n_new = sum(1 for r in res if r.get("status") == "APPENDED")
    return {"ok": len(res) == len(entries) == len(LA.gate_a_rows()), "status": "APPENDED" if n_new else "PRESENT",
            "appended": n_new, "duplicate": sum(1 for r in res if r.get("status") == "DUPLICATE"), "rows": len(res)}


# ============================================================================ koşu grupları
def group_key(vid: str) -> str:
    v = LA.variant(vid)
    if v["family"] == "AG_O":
        return f"AGO_{v['cover']['base']}"
    return f"{v['family']}.{v['signal_tf']}"


def groups() -> list[tuple[str, tuple[str, ...]]]:
    """(grup, varyantlar) — mühür sırasıyla (belirlenimci)."""
    out: dict[str, list[str]] = {}
    for vid in LA.variant_ids():
        out.setdefault(group_key(vid), []).append(vid)
    return [(k, tuple(v)) for k, v in out.items()]


# ============================================================================ Kapı A ailesi
def gate_a_rows() -> list[dict[str, Any]]:
    """Kapı A BH ailesi: LIB_V1 satırları + AG satırları (sonuçlara bakılmadan; sıra: LIB, sonra AG)."""
    return list(RG.gate_a_rows()) + LA.gate_a_rows()


def family_size() -> dict[str, int]:
    lib, ag = len(RG.gate_a_rows()), len(LA.gate_a_rows())
    return {"lib": lib, "ag": ag, "m": lib + ag}


def placebo_for(vid: str, data: Any) -> Any:
    """AG satırının plasebo üreticisi: LIB_V1 ile aynı kurulum (`placebo.KernelPlacebo`), AG varyantıyla."""
    from . import placebo as PL
    return PL.KernelPlacebo(LA.make_variant(vid), data)


__all__ = ["EVAL_UNIT", "F_AG_SKIPPED", "F_AG_TRIALS", "LIB_VERSION", "SCOPE", "coins", "family_size", "gate_a_rows",
           "group_key", "groups", "module_src_shas", "placebo_for", "register_trials", "status"]
