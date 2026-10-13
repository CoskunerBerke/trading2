"""S1d — `LIB_AG_V1` saati: America/New_York yerel saati, oturumlar ve öldürme bölgeleri (SAF: G/Ç, saat, ağ yok).

Bütün saat kuralları New York yerel saatindedir (sahip kararı 2026-10-13). Binance 7/24 işlem gördüğü için barın AÇILIŞ
zamanı (UTC ms) New York'a çevrilir. Çeviri `zoneinfo` ile DEĞİL, ABD'nin 2007'den beri geçerli yaz saati kuralının saf
aritmetiğiyle yapılır (paket saflık testi `zoneinfo`'yu — saat dilimi veritabanını DOSYADAN okur — yasaklar):

* EDT (UTC−4): Mart'ın İKİNCİ Pazarı yerel 02:00 EST (= 07:00 UTC) ile Kasım'ın İLK Pazarı yerel 02:00 EDT (= 06:00 UTC)
  arası; dışında EST (UTC−5). Kural 2007'den önce farklıdır → 2007 öncesi bir an `ValueError` (kapalı hata).
* Eşitlik testi (`tests/test_strategy_lib_ag_s1d.py`): 2019–2030 arasında her saat başı ve her geçiş gününde her 5 dakikada
  `zoneinfo.ZoneInfo("America/New_York")` ile aynı yerel saat. ABD kuralı değişirse (ör. sürekli yaz saati yasası) bu test
  güncel tz veritabanıyla kırılır → yeni mühür.

Oturumlar dakika cinsinden yarı açık aralıklardır [başlangıç, bitiş) ve parametre olarak MÜHÜRDEN gelir (bu modül sayı
yazmaz; yalnız aralık denetimi yapar). Yaz saatine geçiş gününde yerel 02:00–02:59 hiç yaşanmaz, geri dönüş gününde
01:00–01:59 iki kez yaşanır; üyelik barın yerel dakikasıyla sınanır (iki durum da kendiliğinden doğru).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
MIN_MS = 60_000
EDT_MS = -4 * HOUR_MS
EST_MS = -5 * HOUR_MS
#: kuralın geçerli olduğu ilk yıl (Energy Policy Act 2005, 2007'den beri)
RULE_FIRST_YEAR = 2007


def days_from_civil(y: int, m: int, d: int) -> int:
    """Gregoryen (y, m, d) → 1970-01-01'den gün (H. Hinnant'ın algoritması; saf tam sayı)."""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z: int) -> tuple[int, int, int]:
    """1970-01-01'den gün → (y, m, d)."""
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return y + (m <= 2), m, d


def weekday(z: int) -> int:
    """Gün numarasının haftanın günü: 0 = Pazartesi … 6 = Pazar (1970-01-01 Perşembe)."""
    return (z + 3) % 7


def nth_sunday(y: int, m: int, n: int) -> int:
    z0 = days_from_civil(y, m, 1)
    return z0 + (6 - weekday(z0)) % 7 + 7 * (n - 1)


_DST: dict[int, tuple[int, int]] = {}


def dst_bounds_utc(y: int) -> tuple[int, int]:
    """(yaz saati başlangıcı, bitişi) UTC ms: [başlangıç, bitiş) EDT."""
    got = _DST.get(y)
    if got is None:
        if y < RULE_FIRST_YEAR:
            raise ValueError(f"ABD yaz saati kuralı {RULE_FIRST_YEAR} öncesi farklıdır: {y}")
        got = (nth_sunday(y, 3, 2) * DAY_MS + 7 * HOUR_MS, nth_sunday(y, 11, 1) * DAY_MS + 6 * HOUR_MS)
        _DST[y] = got
    return got


def ny_offset_ms(t_ms: int) -> int:
    """UTC anının New York farkı (ms; EDT −4 h, EST −5 h)."""
    t = int(t_ms)
    s, e = dst_bounds_utc(civil_from_days(t // DAY_MS)[0])
    return EDT_MS if s <= t < e else EST_MS


def ny_local_ms(t_ms: int) -> int:
    return int(t_ms) + ny_offset_ms(t_ms)


def ny_day(t_ms: int) -> int:
    """UTC anının New York takvim günü (1970-01-01'den gün)."""
    return ny_local_ms(t_ms) // DAY_MS


def ny_minute(t_ms: int) -> int:
    """UTC anının New York yerel dakikası (0–1439)."""
    return (ny_local_ms(t_ms) % DAY_MS) // MIN_MS


def ny_date_iso(t_ms: int) -> str:
    y, m, d = civil_from_days(ny_day(t_ms))
    return f"{y:04d}-{m:02d}-{d:02d}"


def iso_day(day: int) -> str:
    y, m, d = civil_from_days(int(day))
    return f"{y:04d}-{m:02d}-{d:02d}"


def day_of_iso(s: str) -> int:
    y, m, d = (int(x) for x in str(s)[:10].split("-"))
    return days_from_civil(y, m, d)


def ny_to_utc(day: int, minute: int) -> int | None:
    """New York yerel (gün, dakika) → UTC ms. Yaşanmayan yerel an (yaz saatine geçişte 02:00–02:59) None; iki kez yaşanan
    an (geri dönüşte 01:00–01:59) İLKİ (EDT). Oturum sınırları (02:00, 09:30, 10:00, 12:00, …) bu durumlara düşmez
    (02:00 geçiş gününde yaşanmaz → None; çağıran o günün oturum sınırını kullanmaz)."""
    local = int(day) * DAY_MS + int(minute) * MIN_MS
    for off in (EDT_MS, EST_MS):
        t = local - off
        if ny_offset_ms(t) == off:
            return t
    return None


def parse_sessions(sessions: Mapping[str, Sequence[int]], kz: Iterable[str]) -> tuple[dict[str, tuple[int, int]],
                                                                                         tuple[tuple[int, int], ...]]:
    """Mühürlü oturum tablosu ({ad: [başlangıç dk, bitiş dk]}) ve öldürme bölgesi adları → (oturumlar, KZ aralıkları)."""
    out: dict[str, tuple[int, int]] = {}
    for name, se in sessions.items():
        a, b = (int(x) for x in se)
        if not 0 <= a < b <= 1440:
            raise ValueError(f"oturum {name}: [{a}, {b}) geçersiz")
        out[str(name)] = (a, b)
    kzs = tuple(out[str(k)] for k in kz)
    if not kzs:
        raise ValueError("öldürme bölgesi boş")
    return out, kzs


def in_ranges(minute: int, ranges: Sequence[tuple[int, int]]) -> bool:
    return any(a <= minute < b for a, b in ranges)


def in_kz(t_ms: int, kz: Sequence[tuple[int, int]]) -> bool:
    """UTC anı (barın açılışı ya da giriş anı) New York'ta öldürme bölgesinde mi."""
    return in_ranges(ny_minute(t_ms), kz)


def entries_on_day(history: Any, day: int) -> int:
    """`ctx["history"]` girişlerinden açılışı (dolum anı) New York günü `day` olanların sayısı. Geçmiş kronolojiktir
    (çekirdek ekler): sondan geriye, gün `day`'den önceye düşünce durulur."""
    n = 0
    hist = history or ()
    for k in range(len(hist) - 1, -1, -1):
        d = ny_day(int(hist[k].opened_ms))
        if d == int(day):
            n += 1
        elif d < int(day):
            break
    return n


LIVE_MODULES: tuple[str, ...] = ()

__all__ = ["DAY_MS", "EDT_MS", "EST_MS", "HOUR_MS", "LIVE_MODULES", "MIN_MS", "RULE_FIRST_YEAR", "civil_from_days",
           "day_of_iso", "days_from_civil", "dst_bounds_utc", "entries_on_day", "in_kz", "in_ranges", "iso_day",
           "ny_date_iso", "ny_day", "ny_local_ms", "ny_minute", "ny_offset_ms", "ny_to_utc", "nth_sunday", "parse_sessions",
           "weekday"]
