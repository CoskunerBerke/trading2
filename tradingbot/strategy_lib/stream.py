"""P3b-2b — paylaşılan sinyal akışı (docs/ENGINE_BRAIN_V1.md §5.3 önlem 1; SAF: G/Ç, saat, ağ, rastgelelik yok).

Bir taban kopyanın (K_T2, K_M2, K_BOX, K_D4, C4 defteri) `signals`'ı pozisyondan bağımsızdır: çekirdek onu yalnız
pozisyonsuzken çağırır ve sonuç yalnız (seri, karar anı, kopyanın okuduğu `ctx` alanları) ile belirlenir. Tabanın ve
yedi örtüsünün sekiz çekirdek koşusu aynı seride aynı karar anında aynı `signals`'ı ister → `SharedBase` onu (seri, karar
anı, `base.signal_key(ctx)`) başına BİR kez hesaplar ve sonraki koşulara aynı (değişmez) niyet nesnelerini verir. Aynı
ilke `manage` için: sonuç (seri, karar anı, `base.manage_key(pos, ctx)`) ile belirlenir; örtüler çoğu zaman aynı
girişte aynı pozisyonu taşır (filtre ya da çıkış farkı yol ayırana kadar).

* **Anahtar kopyanın beyanıdır** (`signal_key` / `manage_key`; kopya modülünde gerekçesiyle): kopyanın kararı okuduğu
  `ctx`/pozisyon alanlarının TAMAMI (ör. T2/M2: kullanılmış yapı kümesinin kaynağı olan geçmiş içeriği; D4: açılış anı;
  C4: açılış anı + varyasyon anlık görüntüsü). `bars` ve diğer `ctx` alanları (BTC barları, fonlama, sembol) seri ve karar
  anıyla belirlenir — tabanın ve örtülerin penceresi aynıdır (`lookback` tabanınki). `manage_key` None → paylaşılmaz
  (Box: ön-süzgeçle O(1), bellek harcamaya değmez).
* **Seri bağlama.** Çekirdek her koşunun başında `bind_series(seri)` çağırır (`kernel._Sim`); farklı seri nesnesi
  önbelleği sıfırlar. Bağlanmamış akış hiç önbelleklemez (doğrudan çağrı = düz yol; parite başvurusu böyle çağırır).
* **Bellek.** Boş sonuç + anahtarsız karar, serinin ilk sorulan barından başlayan `now // adım` bit haritasında (5m 3 yıl
  ≈ 40 KB; daha erken bir an sorulursa sözlüğe düşer), dolu sonuçlar sözlükte.
* Eşitlik: örtü ailesi paylaşılan akışla ve her örtü kendi (paylaşımsız) tabanıyla koşulduğunda `KernelResult.to_dict`
  bayt-aynı (`tests/test_strategy_lib_covers_p3b.py`).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contract import TF_MS, Action, Intent, PosView, Variant

#: tabandan kopyalanan (çekirdeğin okuduğu) öznitelikler
_ATTRS = ("variant_id", "family", "signal_tf", "timeframes", "lookback", "needs_btc", "btc_lookback", "needs_funding",
          "funding_lookback_ms", "live_modules", "params")


class SharedBase(Variant):
    """Tabanın paylaşılan akışı (modül belgesi). Kararlar tabanın kendisidir; yalnız tekrar hesaplanmaz."""

    def __init__(self, base: Variant):
        if isinstance(base, SharedBase):
            raise ValueError("SharedBase iç içe kurulmaz")
        self.base = base
        for a in _ATTRS:
            setattr(self, a, getattr(base, a))
        self.step = int(TF_MS[base.signal_tf])
        self._token: Any = None
        self.stats = {"signals_hit": 0, "signals_miss": 0, "manage_hit": 0, "manage_miss": 0, "manage_unshared": 0}
        self._reset()

    def _reset(self) -> None:
        self._sig: dict[tuple, tuple[Intent, ...]] = {}
        self._empty = bytearray()
        self._k0: int | None = None
        self._man: dict[tuple, Action] = {}

    def bind_series(self, token: Any) -> None:
        """Çekirdek koşusunun serisi; farklı nesne → önbellek sıfırlanır."""
        if token is not self._token:
            self._token = token
            self._reset()

    @property
    def fast(self) -> bool:
        return bool(getattr(self.base, "fast", False))

    @fast.setter
    def fast(self, v: bool) -> None:
        self.base.fast = bool(v)
        self._reset()

    # ------------------------------------------------------------------ bit haritası (boş + anahtarsız)
    def _bit(self, now: int) -> tuple[int, int] | None:
        if now % self.step:
            return None
        k = now // self.step
        if self._k0 is None:
            self._k0 = k
        k -= self._k0
        if k < 0:
            return None
        return k >> 3, 1 << (k & 7)

    def _is_empty(self, now: int) -> bool:
        b = self._bit(now)
        return b is not None and b[0] < len(self._empty) and bool(self._empty[b[0]] & b[1])

    def _set_empty(self, now: int) -> bool:
        b = self._bit(now)
        if b is None:
            return False
        if b[0] >= len(self._empty):
            self._empty.extend(bytes(b[0] + 1 - len(self._empty)))
        self._empty[b[0]] |= b[1]
        return True

    # ------------------------------------------------------------------ sözleşme
    def signal_key(self, ctx: Mapping[str, Any]) -> Any:
        return self.base.signal_key(ctx)

    def manage_key(self, pos: PosView, ctx: Mapping[str, Any]) -> Any:
        return self.base.manage_key(pos, ctx)

    def signals(self, bars: Mapping[str, Sequence[Mapping[str, Any]]], ctx: Mapping[str, Any]) -> list[Intent]:
        if self._token is None:
            return list(self.base.signals(bars, ctx))
        now = int(ctx["now_ms"])
        key = self.base.signal_key(ctx)
        if key is None and self._is_empty(now):
            self.stats["signals_hit"] += 1
            return []
        got = self._sig.get((now, key))
        if got is not None:
            self.stats["signals_hit"] += 1
            return list(got)
        self.stats["signals_miss"] += 1
        res = tuple(self.base.signals(bars, ctx))
        if not res and key is None and self._set_empty(now):
            return []
        self._sig[(now, key)] = res
        return list(res)

    def manage(self, pos: PosView, bars: Mapping[str, Sequence[Mapping[str, Any]]], ctx: Mapping[str, Any]) -> Action:
        key = self.base.manage_key(pos, ctx) if self._token is not None else None
        if key is None:
            self.stats["manage_unshared"] += 1
            return self.base.manage(pos, bars, ctx)
        k = (int(ctx["now_ms"]), key)
        got = self._man.get(k)
        if got is not None:
            self.stats["manage_hit"] += 1
            return got
        self.stats["manage_miss"] += 1
        act = self.base.manage(pos, bars, ctx)
        self._man[k] = act
        return act


__all__ = ["SharedBase"]
