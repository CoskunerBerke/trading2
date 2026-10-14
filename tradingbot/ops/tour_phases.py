"""Ana tur faz süreleri — YALNIZ ÖLÇÜM (karar, defter, öğrenme bu değerleri OKUMAZ).

`EngineV3.tour()` her numaralı adımın sonunda `lap(<faz>)` çağırır; süre bir önceki işaretten bu yana geçen duvar
saatidir. Toplam, `health.json["seconds"]` ile aynı saatten ölçülür (`time.time`) ve fazların toplamı o değere eşittir
(yuvarlama farkı hariç). Sonuç `health.json["phases"]`a yazılır ve tek bir "tur fazları: ..." satırı loglanır.

Saat seçimi bilinçlidir: `time.time` dondurulmuş saatli altın testlerde (time_machine, tick=False) donar; böylece iki
özdeş koşunun `health.json`ı bayt bayt aynı kalır. `perf_counter` orada donmaz ve health.json'u koşudan koşuya
değiştirirdi. Üretimde fark yoktur: 0,1 sn çözünürlükle yuvarlanır.
"""
from __future__ import annotations

import time
from typing import Callable

#: (health.json anahtarı, log etiketi) — tur sırasıyla. Anahtar kümesi SABİTTİR (atlanan faz 0.0 yazılır).
PHASES: tuple[tuple[str, str], ...] = (
    ("prep", "hazırlık"),                       # heartbeat, kesinti uzlaştırması, venue olayları, arka plan iş parçacıkları
    ("scan", "tarama"),                         # tarayıcı + değerlendirme evreni (tarayıcı kapalıyken ~0)
    ("symbols", "semboller"),                   # sembol döngüsü: perp mumları + ajan anlık görüntüleri (ağ)
    ("coin_heads", "coin head"),                # girdiler (pattern kanıtı DAHİL) + kafalar + p_win + fırsat + şef
    ("execute", "yürütme"),                     # risk + tetik + kâğıt yürütme
    ("main_tick", "ana defter tiki"),           # yapı yönetimi + funding + perp mark + tick + kayıt
    ("strategy_books", "kâğıt defterler"),      # strateji defterleri + spot defter
    ("learning", "öğrenme"),                    # kapanış öğrenimi, zincir onarımı, gölge etiketleri, araştırma
    ("charts", "grafikler"),                    # PNG grafikler
    ("state", "durum"),                         # karar/risk durum dosyaları
    ("chart_analysis", "grafik analizi"),       # grafik analizi kayıtları
    ("journal", "günlük"),                      # formasyon raporu, karar günlüğü, ortak deneyim, pozisyon yolu
    ("exit_eval", "çıkış değerlendirmesi"),
    ("entry_eval", "giriş değerlendirmesi"),
    ("experiment", "deney"),                    # giriş sonuç etiketleri + kârlılık deneyi
    ("wrap_up", "kapanış"),                     # bellek bırakma, durum/ajan dosyaları, uyarılar
    ("obsidian", "obsidian"),
)
#: Bir fazın İÇİNDE ölçülen alt süreler (toplama ayrıca EKLENMEZ).
SUB_PHASES: tuple[tuple[str, str, str], ...] = (
    ("pattern_evidence", "pattern kanıtı", "coin_heads"),
)


def _wall() -> float:
    return time.time()                          # her çağrıda modül özniteliği okunur (test saati yamaları geçerli)


class TourPhases:
    """Tur içi faz zamanlayıcısı (lap). İstisna ATMAZ; ölçüm hatası turu etkilemez."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or _wall
        self._last = self._clock()
        self.seconds: dict[str, float] = {}
        self.sub: dict[str, float] = {}

    def lap(self, name: str) -> None:
        """Son işaretten bu yana geçen süreyi `name` fazına yazar (aynı faz iki kez işaretlenirse toplanır)."""
        try:
            now = self._clock()
            self.seconds[name] = self.seconds.get(name, 0.0) + max(0.0, now - self._last)
            self._last = now
        except Exception:  # noqa: BLE001 — ölçüm turu durduramaz
            pass

    def add_sub(self, name: str, dt: float) -> None:
        try:
            self.sub[name] = self.sub.get(name, 0.0) + max(0.0, float(dt))
        except Exception:  # noqa: BLE001
            pass

    def as_health(self) -> dict[str, float]:
        """Sabit anahtar kümesi, tur sırasıyla; değerler 0,1 sn'ye yuvarlanır."""
        out = {k: round(self.seconds.get(k, 0.0), 1) for k, _ in PHASES}
        for k, v in self.seconds.items():                 # bilinmeyen faz adı kaybolmaz
            if k not in out:
                out[k] = round(v, 1)
        for k, _, _ in SUB_PHASES:
            out[k] = round(self.sub.get(k, 0.0), 1)
        return out

    def log_line(self) -> str:
        h = self.as_health()
        sub_of = {parent: (k, label) for k, label, parent in SUB_PHASES}
        parts = []
        for k, label in PHASES:
            s = "%s %.1fs" % (label, h.get(k, 0.0))
            if k in sub_of:
                sk, sl = sub_of[k]
                s += " (%s %.1fs)" % (sl, h.get(sk, 0.0))
            parts.append(s)
        total = sum(self.seconds.values())
        return "tur fazları: " + " · ".join(parts) + " · toplam %.1fs" % total


__all__ = ["PHASES", "SUB_PHASES", "TourPhases"]
