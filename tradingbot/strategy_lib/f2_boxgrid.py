"""P3b-2 — F2 ailesi (Box ızgarası): `BOX_n{10,20}_{mid,2r}_ms{0.5,1.0}` (SAF; docs/ENGINE_BRAIN_V1.md §2.2).

Mühürlü satır: "Box kuralı; kenar bandı × çıkış × en az stop %; diğer parametreler K_BOX'unki" (ızgara yalnız üç ekseni
oynatır; LIB okunuşu). Kural yeniden yazılmaz: canlı Box kuralı (`paper_rules.decide_from_rows("b1_box_fade")` →
`box_theory.decide`) `K_BOX` kopyasının yolundan (`k_box.BoxCopy`) çağrılır; parametreler = mühürlü K_BOX parametreleri
(motor `spec["base_params"]` olarak verir) + bu satırın üç ekseni:

* `nNN` → `near_frac` = NN / 100 (kenar bandı kutu yüksekliğinin %NN'i; `edge_band_n` ile tutarlılığı denetlenir);
* `mid` → `exit_kind = "box_mid"` (kutu ortası hedefi); `2r` → `exit_kind = "r_multiple"` ve canlı kuralın `exit_r`'ı
  (K_BOX'ta 2,0) satırın `target_r`'ına eşit olmalı (2R hedef; hedef, canlı kural gibi sinyal kapanışından ölçülen
  mutlak fiyattır);
* `msX` → `min_stop_pct` = X.

Stop, gün sonu düzleşmesi ve SHADOW yapı kipi K_BOX'unkidir (`max_hold`: "LIVE_RULE (K_BOX gün sonu)").
`BOX_n10_mid_ms0.5 ≡ K_BOX` (§2.2): o ızgara üyesi kataloğun alias'ıdır ve K_BOX kopyasını çalıştırır (aynı `spec_sha`);
bu modülün aynı eksenlerle kurulumu K_BOX'un kural parametrelerini birebir verir (test).
"""
from __future__ import annotations

from typing import Any, Mapping

from . import k_box
from .contract import check_params

PARAM_KEYS = ("edge_band_n", "near_frac", "exit", "min_stop_pct")
SPEC_KEYS = ("family", "timeframes", "signal_tf", "sides", "exits", "base_params")
EXIT_KIND = {"box_mid": "box_mid", "2R": "r_multiple"}
#: kimliğe giren canlı modüller: K_BOX kopyasınınkiler (aynı canlı kural yolu)
LIVE_MODULES: tuple[str, ...] = k_box.LIVE_MODULES


def grid_params(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> dict[str, Any]:
    """K_BOX parametreleri + ızgaranın üç ekseni (kapalı hata: ValueError)."""
    p = check_params(variant_id, params, PARAM_KEYS)
    if spec is None or set(spec) != set(SPEC_KEYS):
        raise ValueError(f"{variant_id}: spec anahtarları {sorted(SPEC_KEYS)} olmalı")
    if spec["family"] != "F2" or list(spec["timeframes"]) != ["1d", "5m"] or spec["signal_tf"] != "5m":
        raise ValueError(f"{variant_id}: F2 satırı 1d + 5m, sinyal 5m olmalı")
    base = dict(spec["base_params"])
    n, near, ex, ms = p["edge_band_n"], p["near_frac"], p["exit"], p["min_stop_pct"]
    if isinstance(n, bool) or not isinstance(n, int) or float(near) != n / 100:
        raise ValueError(f"{variant_id}: near_frac = edge_band_n / 100 olmalı: {n!r} / {near!r}")
    if ex not in EXIT_KIND:
        raise ValueError(f"{variant_id}: çıkış {sorted(EXIT_KIND)} olmalı: {ex!r}")
    tr = spec["exits"].get("target_r")
    if ex == "2R" and (tr is None or float(tr) != float(base.get("exit_r", -1))):
        raise ValueError(f"{variant_id}: 2R hedefi canlı kuralın exit_r'ına eşit olmalı: {tr!r} / {base.get('exit_r')!r}")
    if ex == "box_mid" and tr is not None:
        raise ValueError(f"{variant_id}: kutu ortası çıkışında R hedefi yok")
    base.update(near_frac=float(near), exit_kind=EXIT_KIND[ex], min_stop_pct=float(ms))
    return base


class BoxGrid(k_box.BoxCopy):
    """K_BOX kopyasının yolu, ızgaranın üç ekseniyle."""

    def __init__(self, variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None):
        super().__init__(variant_id, grid_params(variant_id, params, spec))
        self.family = "F2"
        self.params = dict(params)


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> BoxGrid:
    return BoxGrid(variant_id, params, spec)


__all__ = ["BoxGrid", "EXIT_KIND", "LIVE_MODULES", "PARAM_KEYS", "SPEC_KEYS", "grid_params", "make"]
