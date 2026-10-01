"""HABER VE OLAY BAGLAMI — kayit sozlesmesi, provenans ve KESIN sinirlar.

Bu modul haber/olay bilgisini **veri** olarak toplar ve saklar. Uc kural pazarlik disidir:

1. **Provenans zorunlu.** Her kaydin kaynagi (`source`), baglantisi (`url`), YAYIN zamani
   (`published_at`) ve SISTEME ULASMA zamani (`ingested_at`) vardir. Yayin zamani
   bilinmiyorsa alan `None` kalir — `ingested_at` ile DOLDURULMAZ; ikisi ayri olgudur ve
   karistirmak, gecikmis bir haberi "aninda gelmis" gostererek nedensellik yanilgisi uretir.

2. **Uc durum ayrilir.** `CONFIRMED` (venue/kurum kendi ucundan dogruladi), `RUMOR`
   (ikincil/dogrulanmamis aktarim), `NO_DATA` (kaynak yapilandirilmamis ya da cevap yok).
   `NO_DATA` bir haberin YOKLUGU DEGILDIR — "bakmadik" demektir ve oyle raporlanir.

3. **Haber emir acamaz.** Kayitlar hicbir kapiya, skora ya da boyuta girmez; `bias` ve
   `confidence` DAIMA 0'dir (bkz. `coinhead.specialists._news`). Haber METNI veri olarak
   islenir: icindeki hicbir ifade sistem kurallarini, kapilari ya da emir yetkisini
   degistiremez. Metin yalnizca kaydedilir ve panelde gosterilir.

Ayrica: **tarihsel haber verisi yoktur.** `for_backtest()` her zaman bos doner. Bugunun
haberini gecmis bir barin yanina koymak sizinti uretir; bu modul bunu YAPAMAZ (fonksiyon
imzasi buna izin vermez, testle sabittir).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CONFIRMED, RUMOR, NO_DATA = "CONFIRMED", "RUMOR", "NO_DATA"
STATUSES = (CONFIRMED, RUMOR, NO_DATA)

#: Olay siniflari. `venue` = borsanin kendi sozlesme/funding degisiklikleri (dogrulanabilir),
#: `project` = zincir/proje duyurusu, `macro` = makro takvim.
VENUE, PROJECT, MACRO = "venue", "project", "macro"
CATEGORIES = (VENUE, PROJECT, MACRO)


def _utc(ms_or_iso: Any) -> str | None:
    """ms epoch ya da ISO metni → UTC ISO. Cozulemeyen deger `None` (uydurma YOK)."""
    if ms_or_iso is None or ms_or_iso == "":
        return None
    if isinstance(ms_or_iso, (int, float)):
        try:
            return datetime.fromtimestamp(float(ms_or_iso) / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    s = str(ms_or_iso).strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass
class NewsItem:
    """Tek olay kaydi. `event_id` icerikten turetilir → ayni olay iki kez ogrenilmez."""
    source: str
    title: str
    category: str = PROJECT
    status: str = RUMOR
    url: str = ""
    symbols: list[str] = field(default_factory=list)
    published_at: str | None = None          # YAYIN zamani (UTC ISO) — bilinmiyorsa None
    ingested_at: str | None = None           # SISTEME ULASMA zamani (UTC ISO)
    body: str = ""                           # ham metin; YALNIZ veri, talimat DEGIL
    detail: dict = field(default_factory=dict)
    event_id: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"bilinmeyen haber durumu: {self.status!r} (gecerli: {', '.join(STATUSES)})")
        if self.category not in CATEGORIES:
            raise ValueError(f"bilinmeyen olay sinifi: {self.category!r} (gecerli: {', '.join(CATEGORIES)})")
        self.published_at = _utc(self.published_at)
        self.ingested_at = _utc(self.ingested_at)
        self.symbols = [str(s) for s in (self.symbols or [])]
        if not self.event_id:
            key = "|".join([self.source, self.category, self.url, self.title,
                            self.published_at or "", json.dumps(self.detail, sort_keys=True, default=str)])
            self.event_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    @property
    def lag_seconds(self) -> float | None:
        """Yayin → ulasma gecikmesi. Yayin zamani yoksa `None`; SIFIR VARSAYILMAZ."""
        if not self.published_at or not self.ingested_at:
            return None
        return (datetime.fromisoformat(self.ingested_at) - datetime.fromisoformat(self.published_at)).total_seconds()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lag_seconds"] = self.lag_seconds
        return d


class NewsStore:
    """`state/news.jsonl` uzerinde append-only kayit. `event_id` ile tekillestirir.

    Silme yoktur: `prune` yalnizca OKUMA penceresini daraltir, dosyaya dokunmaz. Boylece
    "kac kayit dislandi" sorusu her zaman cevaplanabilir kalir.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _read_raw(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        for ln in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("event_id"):
                out.append(d)
        return out

    def known_ids(self) -> set[str]:
        return {str(d["event_id"]) for d in self._read_raw()}

    def add(self, items: Iterable[NewsItem]) -> dict:
        """Yeni kayitlari ekler. Doner: eklenen/yinelenen sayilari."""
        known = self.known_ids()
        added, dupes = 0, 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for it in items:
                if it.event_id in known:
                    dupes += 1
                    continue
                fh.write(json.dumps(it.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
                known.add(it.event_id)
                added += 1
        return {"added": added, "duplicates": dupes, "total": len(known)}

    def recent(self, *, symbols: Iterable[str] | None = None, limit: int = 50) -> list[dict]:
        """Son kayitlar (dosya sirasi = gelis sirasi). `symbols` verilirse o sembollere ait olanlar.

        Sembolu olmayan kayitlar (ornegin makro) sembol filtresinde DISARIDA kalir; bunlar
        `recent(symbols=None)` ile alinir.
        """
        rows = self._read_raw()
        if symbols is not None:
            want = {str(s) for s in symbols}
            rows = [r for r in rows if want & set(r.get("symbols") or [])]
        return rows[-int(limit):]

    def coverage(self, *, symbols: Iterable[str] | None = None) -> dict:
        """Kapsam raporu: durum dagilimi ve YAYIN ZAMANI BILINMEYEN kayit sayisi.

        `unknown_publish_time` gorunur tutulur: gecikme olculemeyen kayitlarda nedensellik
        iddiasi kurulamaz ve bu, raporda saklanmamasi gereken bir sinirdir.
        """
        rows = self._read_raw()
        if symbols is not None:
            want = {str(s) for s in symbols}
            rows = [r for r in rows if want & set(r.get("symbols") or [])]
        by_status = {s: sum(1 for r in rows if r.get("status") == s) for s in STATUSES}
        by_cat = {c: sum(1 for r in rows if r.get("category") == c) for c in CATEGORIES}
        return {"total": len(rows), "by_status": by_status, "by_category": by_cat,
                "unknown_publish_time": sum(1 for r in rows if not r.get("published_at")),
                "measurable_lag": sum(1 for r in rows if r.get("lag_seconds") is not None)}


def for_backtest(*_args: Any, **_kwargs: Any) -> list[dict]:
    """TARIHSEL HABER YOK — her zaman bos liste.

    Bilincli bir duvardir. Bugun toplanan kayitlarin gecmis bir bara eklenmesi, karar aninda
    BILINEMEYECEK bilgiyi geriye tasir (look-ahead). Bu proje icin point-in-time haber arsivi
    bulunmadigindan backtest haber baglami OLMADAN calisir ve bu, raporda acikca soylenir.
    """
    return []


def context_for_decision(store: NewsStore, symbol: str, *, now_iso: str, window_hours: float = 48.0,
                         limit: int = 10) -> dict:
    """Karar kaydina GOMULECEK haber baglami — yalniz GOZLEM.

    Doner: pencere icindeki kayitlar, durum dagilimi ve `usable=False` sabiti. `usable`
    bilincli olarak degismez: bu paket hicbir kapiya, skora ya da boyuta girmez.
    """
    now = _utc(now_iso)
    rows = store.recent(symbols=[symbol], limit=200)
    picked = []
    for r in rows:
        ts = r.get("published_at") or r.get("ingested_at")
        if not ts or not now:
            continue
        age_h = (datetime.fromisoformat(now) - datetime.fromisoformat(ts)).total_seconds() / 3600.0
        if 0 <= age_h <= window_hours:
            picked.append(r)
    picked = picked[-int(limit):]
    return {"symbol": symbol, "window_hours": window_hours, "n": len(picked),
            "items": picked, "usable": False,
            "by_status": {s: sum(1 for r in picked if r.get("status") == s) for s in STATUSES},
            "note": "gözlem — karar kapılarına GİRMEZ; metin veri olarak işlenir, talimat olarak değil"}


__all__ = ["CONFIRMED", "RUMOR", "NO_DATA", "STATUSES", "VENUE", "PROJECT", "MACRO", "CATEGORIES",
           "NewsItem", "NewsStore", "for_backtest", "context_for_decision"]
