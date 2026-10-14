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


def zigzag(n: int, up: float, down: float, start: float = 0.0) -> list[tuple]:
    """Sırayla `up` yükselen ve `down` düşen barlar (ilk bar yükselir), aralık 1,0. `up > down` → RSI'ı 100 olmayan
    yükseliş trendi (ör. 0,6/0,4 → bar başına ortalama +0,1, RSI ≈ 60)."""
    out, o = [], start
    for k in range(n):
        c = o + (up if k % 2 == 0 else -down)
        pad_ = (1.0 - abs(c - o)) / 2.0
        out.append((o, max(o, c) + pad_, min(o, c) - pad_, c, 1.0))
        o = c
    return out


def mirror(seq: list[tuple]) -> list[tuple]:
    """Fiyat aynası (BASE etrafında): açılış/kapanış işaret değiştirir, tepe ↔ dip; hacim aynı. ATR aynı kalır, RSI →
    100 − RSI, trend yönü döner. SHORT aynası kimliklerin örnekleri LONG örneklerin aynasıdır."""
    return [(-b[0], -b[2], -b[1], -b[3]) + tuple(b[4:]) for b in seq]


def _mirror_example(ex: dict, rename: dict[str, str] | None = None) -> dict:
    """LONG örneğinin SHORT aynası; koşul adları aynıdır (ayna tanımında sıra aynı), `rename` farklı adları eşler."""
    rename = rename or {}
    out = {"match": mirror(ex["match"]), "near_miss": {rename.get(k, k): mirror(v) for k, v in ex["near_miss"].items()}}
    if "p_back" in ex:
        out["p_back"] = ex["p_back"]
    return out


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


# ---------------------------------------------------------------------------- 1. parti (CV001–CV008)
def _with_range(trend: list[tuple], n: int = 25) -> list[tuple]:
    """Trendin sonuna `n` barlık düz yatay bölge (açılış/kapanış ±0,1, uçlar ±0,5). Son kapanış X = trend sonu + 0,1;
    bölgenin tepesi X + 0,4, dibi X − 0,6."""
    x = trend[-1][3]
    return list(trend) + [_shift(b, x) for b in flat(n)]


def _pullback(trend: list[tuple], n: int = 4, step: float = 0.3) -> list[tuple]:
    """Trendin sonuna `n` bar × `step` ATR geri çekilme."""
    return list(trend) + decline(n, step, start=trend[-1][3])


def _seq(pre: list[tuple], *bars: tuple) -> list[tuple]:
    """Formasyon barları X'e (öncesinin son kapanışı) göre kaydırılır."""
    x = pre[-1][3] if pre else 0.0
    return list(pre) + [_shift(b, x) for b in bars]


#: CV001: 150 bar × 0,1 ATR yükseliş (kapanış > EMA50 > EMA200) + 25 barlık yatay; kırılım barı hacimli, güçlü gövdeli.
CV001_TREND = rally(150, 0.1)
CV001_PRE = _with_range(CV001_TREND)
CV001_C0 = (0.0, 0.95, -0.1, 0.9, 2.0)                  # gövde 0,86; kapanış X + 0,9 > bölge tepesi X + 0,4; hacim 2x


def cv001(before: list[tuple] | None = None, c0: tuple = CV001_C0) -> list[tuple]:
    return _seq(CV001_PRE if before is None else before, c0)


def _cv001_second_break() -> list[tuple]:
    """Önceki bar bölge tepesinin üstünde kapanmış (ilk kırılım değil); son bar yine yeni tepenin üstünde kapanır."""
    pre = list(CV001_PRE)
    pre[-1] = _shift((-0.1, 0.7, -0.5, 0.6, 1.0), CV001_TREND[-1][3])
    return cv001(before=pre)


#: CV003: 200 bar zikzak yükseliş (+0,6/−0,4) + 4 bar × 0,3 geri çekilme (RSI ≈ 50, kapanış EMA50'nin üstünde);
#: kırmızı c0, gövdesini yutan yeşil c1.
CV003_PRE = _pullback(zigzag(200, 0.6, 0.4))
CV003_C0 = (0.0, 0.1, -0.45, -0.35, 1.0)
CV003_C1 = (-0.4, 0.3, -0.45, 0.2, 1.2)                 # gövde [−0,4, 0,2] ⊇ c0 gövdesi [−0,35, 0]; hacim 1,2x


def cv003(before: list[tuple] | None = None, c0: tuple = CV003_C0, c1: tuple = CV003_C1) -> list[tuple]:
    return _seq(CV003_PRE if before is None else before, c0, c1)


#: CV005: 30 bar × 0,3 düşüş (RSI düşük; önceki 20 barın dibi X − 0,35); uzun kırmızı c0 desteğe iner (dibi desteğe
#: iki yönde de 0,5 ATR'den yakın), içinde kalan yeşil c1 (harami) desteğin üstünde, daha yüksek hacimle kapanır.
CV005_PRE = decline(30, 0.3)
CV005_C0 = (0.0, 0.05, -0.8, -0.75, 1.0)                # gövde 0,88; dip X − 0,8: destek X − 0,35'e 0,45 ATR
CV005_C1 = (-0.7, -0.1, -0.72, -0.2, 1.2)               # gövde [−0,7, −0,2] ⊆ [−0,75, 0]; kapanış > X − 0,35; hacim > c0


def cv005(before: list[tuple] | None = None, c0: tuple = CV005_C0, c1: tuple = CV005_C1) -> list[tuple]:
    return _seq(CV005_PRE if before is None else before, c0, c1)


def _cv005_far_from_support() -> list[tuple]:
    """Önceki 20 barın birinde (sondan 5.) derin alt fitil: destek X − 3; c0'ın dibi ondan 0,5 ATR'den fazla yukarıda."""
    pre = list(CV005_PRE)
    o, h, _lo, c, v = pre[-5]
    pre[-5] = (o, h, pre[-1][3] - 3.0, c, v)
    return cv005(before=pre)


#: CV007: düz öncesi (önceki 20 barın dibi X − 0,6); c0 dibi süpürüp geri kapanır (alt fitil 0,77), hacimli yeşil c1
#: gövdesini yutar.
CV007_C0 = (0.0, 0.1, -1.2, -0.2, 1.0)
CV007_C1 = (-0.25, 0.6, -0.3, 0.5, 2.0)


def cv007(before: list[tuple] | None = None, c0: tuple = CV007_C0, c1: tuple = CV007_C1) -> list[tuple]:
    return _seq(flat(30) if before is None else before, c0, c1)


_CV001 = {
    "match": cv001(),
    "near_miss": {
        "bars[0].color": cv001(c0=(0.9, 0.95, -0.1, 0.0, 2.0)),                                 # kırmızı
        "bars[0].body_range": cv001(c0=(0.0, 0.95, -0.85, 0.9, 2.0)),                          # gövde 0,50 < 0,55
        "relations[0]": cv001(c0=(0.0, 0.4, -0.05, 0.35, 2.0)),                                # kapanış X + 0,35 ≤ tepe X + 0,4
        "relations[1]": _cv001_second_break(),                                                 # ilk kırılım değil
        "bars[0].volume_ratio": cv001(c0=CV001_C0[:4] + (1.2,)),                               # hacim 1,2x < 1,3x
        "context.trend": cv001(before=_with_range(decline(150, 0.1))),                          # öncesi düşüş trendi
    },
}
_CV003 = {
    "match": cv003(),
    "near_miss": {
        "bars[0].color": cv003(c0=(-0.35, 0.1, -0.45, 0.0, 1.0)),                              # c0 yeşil
        "bars[1].color": cv003(c1=(0.2, 0.3, -0.45, -0.4, 1.2)),                               # c1 kırmızı
        "relations[0]": cv003(c1=(-0.4, 0.05, -0.45, -0.05, 1.2)),                             # c1 gövde tepesi < c0 gövde tepesi
        "relations[1]": cv003(c1=(-0.3, 0.3, -0.35, 0.2, 1.2)),                                # c1 gövde dibi > c0 gövde dibi
        "bars[1].volume_ratio": cv003(c1=CV003_C1[:4] + (0.9,)),                               # hacim 0,9x < 1,0x
        "context.rsi14": cv003(before=zigzag(200, 0.8, 0.2)),                                  # geri çekilmesiz güçlü yükseliş
        "context.trend": cv003(before=_pullback(zigzag(200, 0.4, 0.6), n=2, step=0.1)),        # öncesi düşüş trendi
    },
}
_CV005 = {
    "match": cv005(),
    "near_miss": {
        "bars[0].color": cv005(c0=(-0.75, 0.05, -0.8, 0.0, 1.0)),                              # c0 yeşil
        "bars[0].body_range": cv005(c0=(0.0, 0.3, -0.7, -0.5, 1.0)),                           # gövde 0,50 < 0,60
        "bars[1].color": cv005(c1=(-0.2, -0.1, -0.72, -0.6, 1.2)),                             # c1 kırmızı
        "relations[0]": cv005(c1=(-0.7, 0.2, -0.72, 0.1, 1.2)),                                # c1 gövdesi c0 gövdesinin üstüne taşar
        "relations[1]": cv005(c1=(-0.78, -0.1, -0.79, -0.2, 1.2)),                             # c1 gövdesi c0 gövdesinin altına taşar
        "relations[4]": cv005(c1=(-0.7, -0.35, -0.72, -0.45, 1.2)),                            # kapanış desteğin altında
        "relations[5]": cv005(c1=CV005_C1[:4] + (1.0,)),                                       # hacim artmıyor (c1 = c0)
        "relations[2]": _cv005_far_from_support(),                                             # c0 desteğin 0,5 ATR'den fazla üstünde
        # c0 desteğin 0,75 ATR altına iner (kırılım, destek testi değil); c1 yine harami ve desteğin üstünde kapanır
        "relations[3]": cv005(c0=(0.0, 0.1, -1.1, -1.0, 1.0), c1=(-0.9, -0.1, -0.95, -0.2, 1.2)),
        "context.rsi14": cv005(before=flat(30)),                                               # düz öncesi: RSI > 40
    },
}
_CV007 = {
    "match": cv007(),
    "near_miss": {
        "bars[0].lower_wick_range": cv007(c0=(0.0, 0.1, -0.8, -0.5, 1.0)),                     # alt fitil 0,33 < 0,45
        "bars[1].color": cv007(c1=(0.5, 0.6, -0.3, -0.25, 2.0)),                               # c1 kırmızı
        "relations[0]": cv007(c0=(0.0, 0.05, -0.55, -0.2, 1.0)),                               # dip süpürülmedi (X − 0,55 > X − 0,6)
        "relations[1]": cv007(c0=(0.0, 0.05, -2.0, -0.7, 1.0)),                                # dibin altında kapandı
        "relations[2]": cv007(c1=(-0.25, 0.05, -0.3, -0.05, 2.0)),                             # c1 gövde tepesi < c0 gövde tepesi
        "relations[3]": cv007(c1=(-0.15, 0.6, -0.3, 0.5, 2.0)),                                # c1 gövde dibi > c0 gövde dibi
        "bars[1].volume_ratio": cv007(c1=CV007_C1[:4] + (1.4,)),                               # hacim 1,4x < 1,5x
    },
}

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
    # 1. parti: SHORT örnekleri LONG örneklerin aynasıdır. Aynada gövde tepesi ↔ gövde dibi yer değiştirir, bu yüzden
    # yutan/harami ilişkilerinin adları (ve fitil adı) eşlenir.
    "CV001_BREAKOUT20_TREND_VOL_L": _CV001,
    "CV002_BREAKOUT20_TREND_VOL_S": _mirror_example(_CV001),
    "CV003_PULLBACK_ENGULF_L": _CV003,
    "CV004_PULLBACK_ENGULF_S": _mirror_example(_CV003, {"relations[0]": "relations[1]", "relations[1]": "relations[0]"}),
    "CV005_SUPPORT_HARAMI_L": _CV005,
    "CV006_RESIST_HARAMI_S": _mirror_example(_CV005, {"relations[0]": "relations[1]", "relations[1]": "relations[0]"}),
    "CV007_SWEEP_REJECT_ENGULF_L": _CV007,
    "CV008_SWEEP_REJECT_ENGULF_S": _mirror_example(_CV007, {"bars[0].lower_wick_range": "bars[0].upper_wick_range",
                                                             "relations[2]": "relations[3]", "relations[3]": "relations[2]"}),
}
