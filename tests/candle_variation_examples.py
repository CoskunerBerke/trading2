# -*- coding: utf-8 -*-
"""Mum varyasyonu ÖRNEKLERİ — her kayıt kimliği için bir EŞLEŞEN dizi ve her ana koşul için bir KIL PAYI KAÇAN dizi.

`EXAMPLES[id] = {"match": [...], "near_miss": {koşul: [...]}}`. Koşul adı `candle_dsl.explain_last`in ilk başarısız
koşul adıdır ("bars[1].body_range", "relations[0]", "context.prior_move" ...): kaçan dizide o koşul İLK başarısız olmalıdır.

Birimler ATR'dir: dizideki her bar (o, h, l, c, hacim) BASE'e göre farktır; `pad` diziyi düz bir önekin (açılış ≈ kapanış
± 0,2, aralık 1,0, hacim 1,0 → ATR ≈ 1, RSI ≈ 50, hacim ortalaması ≈ 1) sonuna ekler ve 500 barlık pencereyi tamamlar.
Son bar teyit barıdır. Break teyitli bir varyasyonda formasyonun son barının teyit barından kaç önce olduğu `p_back`
anahtarıyla verilir (varsayılan 0).

Yeni varyasyon eklenince buraya da eklenir: `tests/test_candle_dsl.py::test_registry_examples` eksik kimlikte düşer.
"""
from __future__ import annotations

BASE = 100.0
H4 = 14_400_000
#: Son barın açılışı (4h hizalı).
T_LAST = 1_700_000_000_000 // H4 * H4
WINDOW = 500


def flat(n: int) -> list[tuple]:
    """Düz önek: açılış/kapanış ±0,1 (sırayla), uçlar ±0,5 → aralık 1,0, gövde 0,2; hacim 1,0. Parite sondan sayılır."""
    out = []
    for k in range(n):
        o = -0.1 if (n - k) % 2 else 0.1
        out.append((o, 0.5, -0.5, -o, 1.0))
    return out


def decline(n: int, step: float, start: float = 0.0) -> list[tuple]:
    """Her bar `step` düşen, aralığı 1,0 olan barlar (açılış = önceki kapanış)."""
    out, o = [], start
    for _ in range(n):
        c = o - step
        pad_ = (1.0 - step) / 2.0
        out.append((o, o + pad_, c - pad_, c, 1.0))
        o = c
    return out


def rally(n: int, step: float, start: float = 0.0) -> list[tuple]:
    """Her bar `step` yükselen, aralığı 1,0 olan barlar."""
    out, o = [], start
    for _ in range(n):
        c = o + step
        pad_ = (1.0 - step) / 2.0
        out.append((o, c + pad_, o - pad_, c, 1.0))
        o = c
    return out


def pad(seq: list[tuple], total: int = WINDOW, *, t_last: int = T_LAST, base: float = BASE) -> list[dict]:
    """Diziyi düz önekle `total` bara tamamlar; zaman damgaları 4h adımla ardışık, son barın açılışı `t_last`."""
    bars = flat(total - len(seq)) + list(seq)
    return [{"timestamp": t_last - (total - 1 - k) * H4, "open": base + b[0], "high": base + b[1], "low": base + b[2],
             "close": base + b[3], "volume": float(b[4]) if len(b) > 4 else 1.0} for k, b in enumerate(bars)]


def _shift(bar: tuple, x: float) -> tuple:
    return (bar[0] + x, bar[1] + x, bar[2] + x, bar[3] + x) + tuple(bar[4:])


# ---------------------------------------------------------------------------- CV000_EXAMPLE_BULL3
#: Formasyon barları X'e (öncesinin son kapanışı) göre: c0 uzun kırmızı, c1 küçük bekleme (dibi süpürür), c2 hacimli yeşil.
CV000_C0 = (0.0, 0.1, -1.8, -1.6, 1.0)
CV000_C1 = (-1.55, -1.4, -1.95, -1.45, 1.0)
CV000_C2 = (-1.45, -0.4, -1.5, -0.5, 2.0)


def cv000(before: list[tuple] | None = None, c0: tuple = CV000_C0, c1: tuple = CV000_C1, c2: tuple = CV000_C2) -> list[tuple]:
    """Varsayılan öncesi: 10 bar × 0,3 ATR düşüş (prior_move ≈ −2,7 ATR)."""
    pre = decline(10, 0.3) if before is None else list(before)
    x = pre[-1][3] if pre else 0.0
    return pre + [_shift(c0, x), _shift(c1, x), _shift(c2, x)]


def _cv000_sweep_missed() -> list[tuple]:
    """Önceki 10 barın birinde (p0-5) uzun alt fitil: c1 artık o dibi süpürmüyor."""
    pre = decline(10, 0.3)
    o, h, _lo, c, v = pre[5]
    pre[5] = (o, h, -5.5, c, v)                          # X - 2,5 (c1.low = X - 1,95'in altında)
    return cv000(before=pre)


EXAMPLES: dict[str, dict] = {
    "CV000_EXAMPLE_BULL3": {
        "match": cv000(),
        "near_miss": {
            "bars[0].color": cv000(c0=(-1.6, 0.1, -1.8, 0.0, 1.0)),
            "bars[0].body_range": cv000(c0=(-0.3, 0.2, -1.7, -1.3, 1.0)),                      # gövde 0,53 < 0,60
            "bars[0].range_atr": cv000(c0=(0.0, 0.1, -1.1, -1.0, 1.0),                         # aralık 1,2 ATR < 1,3
                                       c2=(-1.45, -0.2, -1.5, -0.3, 2.0)),
            "bars[1].body_range": cv000(c1=(-1.2, -1.15, -1.75, -1.7, 1.0)),                   # gövde 0,83 > 0,35
            "bars[1].range_atr": cv000(c1=(-1.55, -1.2, -2.1, -1.45, 1.0)),                    # aralık 0,9 ATR > 0,7
            "bars[2].color": cv000(c2=(0.2, 0.25, -0.75, -0.7, 2.0)),                           # kırmızı (gerisi uyar)
            "bars[2].body_range": cv000(c2=(-1.45, -0.4, -2.2, -0.5, 2.0)),                    # gövde 0,53 < 0,60
            "bars[2].upper_wick_range": cv000(c2=(-1.45, 0.39, -1.61, -0.21, 2.0)),            # üst fitil 0,30 > 0,25
            "bars[2].volume_ratio": cv000(c2=CV000_C2[:4] + (1.2,)),                           # hacim 1,2x < 1,5x
            "relations[0]": cv000(c1=(-0.85, -0.7, -1.2, -0.75, 1.0)),                         # c1 gövdesi c0 ortasının üstünde
            "relations[1]": _cv000_sweep_missed(),
            "relations[2]": cv000(c2=(-1.9, -0.8, -1.95, -0.85, 2.0)),                         # c2 kapanışı c0 ortasının altında
            "context.prior_move": cv000(before=flat(10)),                                      # öncesi düz
            "context.rsi14": cv000(before=rally(40, 0.5) + decline(10, 0.08, start=20.0)),    # güçlü yükseliş, kısa düşüş
        },
    },
}
