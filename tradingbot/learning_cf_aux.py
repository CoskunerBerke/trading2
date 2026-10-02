"""KARŞI-OLGUSAL YARDIMCI ETİKETLER — `cf_aux_v1` (2026-10-01, Box karşı-olgusal / gerçek farkı incelemesi). YALNIZ KAYIT.

Yeni etiketlenen her NET karşı-olgusal sonuca (`learning_cf.label_records`, `exec_model` verilmişken) TEK ad alanlı bir
sözlük eklenir: `outcome["aux"]`. Mevcut alanların hiçbiri (`r_net`, `r_multiple`, `r_gross`, `cost_r`, `cost_parts_r`,
`label_version`, ...) değişmez; `r_net`in ne zaman ve nasıl etiketlendiği de değişmez. Araştırma eşleşmesi (`outcome_r` →
`learn/research_policy.py` BLOCKED `baseline_r`), ortak deneyim satırları (yalnız adlandırılmış alanlar: `rows.cf_row`,
`rows.cf_r`, `rows.cf_fp`) ve bütün karar yolları bayt bayt aynı girdiyi alır. Eski kayıtlar (alan yok) aynen yüklenir;
geriye dönük doldurma YOKTUR (`relabel_net` ve çevrimdışı `scripts/cf_backfill_net.py` bu alanı yazmaz).

İncelemede doğrulanan iki etiketleyici iyimserliği burada ÖLÇÜLÜR, `r_net`e uygulanmaz:

1. **Stop dolumu.** Karşı-olgusal stop SEVİYEDEN dolar (`STOP_AT_LEVEL`); gerçek defter stopu, seviyenin ötesindeki İLK
   60 sn örneğinden doldurur (`exit_fill.basis == GAP_FILL_AT_FIRST_OBSERVATION`, `first_source == PRICE`). Tahmin:
   aynı defterin son `OVERSHOOT_WINDOW` (200) böyle gerçek stop çıkışında `yön·(stop − first_price)/giriş·100`
   medyanı (`overshoot_pct_est`, en az `OVERSHOOT_MIN_N` = 10 gözlem; yoksa null). `first_source == BAR_OPEN` olan çıkışlar
   (1h bar açılışı boşluğu) SAYILMAZ: karşı-olgusal bar açılışı boşluğunu zaten kendi kuralıyla (`GAP_FILL_AT_BAR_OPEN`)
   doldurur. Medyan, kesinti kaynaklı birkaç büyük boşluğa karşı dayanıklıdır. Tahmin geçiş başına ve defter başına EN
   FAZLA BİR KEZ, yalnız o geçişte bir kayıt kesinleştiğinde hesaplanır (`AuxPass`), geçmiş değişmedikçe geçişler arası
   önbellekten okunur; maliyet geçmiş uzunluğuyla sınırlı (en fazla `history_keep`, sondan tarama 200 gözlemde durur).
   `r_net_sampled_est = r_net − overshoot_pct_est / stop_pct` yalnız seviyeden dolan stop çıkışlarında (`net_exit_basis ==
   STOP_AT_LEVEL`; `stop_pct` kaydın KENDİ giriş dolumundan); başa-baş stopta yalnız miktarın tamamı açıkken (TP1 kısmi
   kapanışı olamıyorsa; aksi halde kalan pay bilinmez → null). Diğer çıkışlarda `r_net`e eşittir. Aşmanın ek ücreti
   (taker %0,05 × aşma ≈ 0) ihmal edilir.
2. **Giriş barı.** Brüt etiketleyici ve net yeniden oynatma yalnız `ts > created` barlarını yürür; girişin olduğu bar
   (`ts ≤ created < ts + tf`) hiç denetlenmez, gerçek pozisyon ise açıldığı andan izlenir. Giriş barı etiketleyicinin ELDEKİ
   kapanmış çerçevesinde varsa (Box: 300 × 5m ≈ 25 sa çerçeve, kesinleşme giriş barından SONRAKİ bir barın kapanmasını
   gerektirdiğinden giriş barı o an kapanmış ve çerçevededir; ana bot: `runner.last_frames` 4h çerçevesi, 730 gün; formasyon:
   taramanın bar listesi) barın TERS ucu ihtiyatlı biçimde girişten SONRA sayılır (OHLC'den girişten önceki kısım
   ayrılamaz → üst sınır: girişten önceki bir fitil de "değdi" sayılabilir). Ters uç stopa değdiyse ihtiyatlı sonuç o
   barda seviyeden stoptur; R'si `r_net` ile AYNI yoldan (atılık `FuturesLedgerV2`, `ExecModel`, aynı giriş dolumu, seviye
   + çıkış kayması, bar kapanışında tick) hesaplanır → `r_net_entry_bar`. Değmediyse `r_net_entry_bar = r_net`. Lehte uç
   (hedef / MFE başa-baş) ihtiyatlı olarak YOK sayılır. Bar çerçevede yoksa veri ÇEKİLMEZ: `entry_bar.checked = false`.

`r_net_conservative` ikisini birlikte uygular: giriş barında stop → `r_net_entry_bar − overshoot_pct_est/stop_pct`; aksi
halde `r_net_sampled_est`. Hesaplanamayan her değer null'dır (uydurulmaz). Hata etiketlemeyi ETKİLEMEZ (`aux_status`).
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable

import numpy as np

from .accounting import EXIT_BE_STOP, EXIT_STOP, AmountType, SizeSpec, SlippageModel, TickData
from .core import D, from_iso, iso
from .learning_cf import _quiet_rates, _r6, _replay_filters, _replay_notional

AUX_VERSION = "cf_aux_v1"
#: Aşma tahmini: aynı defterin son N gerçek ilk-örnek stop çıkışı; en az bu kadar gözlem yoksa tahmin null.
OVERSHOOT_WINDOW = 200
OVERSHOOT_MIN_N = 10
#: Gerçek defterin ilk-örnek stop dolumu (`accounting.futures_ledger.exit_decision`) ve 60 sn fiyat-yalnız tick kaynağı.
OVERSHOOT_BASIS = "GAP_FILL_AT_FIRST_OBSERVATION"
OVERSHOOT_SOURCE = "PRICE"
#: Karşı-olgusal net yeniden oynatmanın seviyeden stop dolumu (`learning_cf._net_replay`).
CF_LEVEL_BASIS = "STOP_AT_LEVEL"
#: Kapatma anahtarı (testler / acil durum): False → `aux` hiç yazılmaz, sonuç bu özellikten önceki ile bit-aynı.
ENABLED = True


def _fin(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _side_str(x: Any) -> str:
    return str(getattr(x, "value", x) or "").upper()


def _overshoot_of(rec: Any) -> float | None:
    """Tek gerçek kaydın aşma yüzdesi (giriş fiyatının yüzdesi, pozitif = aleyhte) ya da uygun değilse None."""
    if str(getattr(rec, "exit_reason", "") or "") not in (EXIT_STOP, EXIT_BE_STOP):
        return None
    feats = getattr(rec, "features", None)
    ef = feats.get("exit_fill") if isinstance(feats, dict) else None
    if not isinstance(ef, dict) or ef.get("basis") != OVERSHOOT_BASIS or ef.get("first_source") != OVERSHOOT_SOURCE:
        return None
    first, stop, entry = _fin(ef.get("first_price")), _fin(ef.get("stop")), _fin(getattr(rec, "entry", None))
    side = _side_str(getattr(rec, "side", None))
    if first is None or stop is None or entry is None or entry <= 0 or side not in ("LONG", "SHORT"):
        return None
    sg = 1.0 if side == "LONG" else -1.0
    return sg * (stop - first) / entry * 100.0


def overshoot_estimate(history: Iterable[Any] | None, *, window: int | None = None,
                       min_n: int | None = None) -> tuple[float | None, int]:
    """(medyan aşma %, gözlem sayısı) — geçmişin SONUNDAN geriye, en çok `window` uygun gerçek stop çıkışı. Varsayılanlar
    çağrı anında modül sabitlerinden okunur (`OVERSHOOT_WINDOW`, `OVERSHOOT_MIN_N`)."""
    window = OVERSHOOT_WINDOW if window is None else window
    min_n = OVERSHOOT_MIN_N if min_n is None else min_n
    vals: list[float] = []
    seq = history if isinstance(history, list) else list(history or ())
    for rec in reversed(seq):
        if len(vals) >= int(window):
            break
        v = _overshoot_of(rec)
        if v is not None:
            vals.append(v)
    n = len(vals)
    if n < max(1, int(min_n)):
        return None, n
    return _r6(statistics.median(vals)), n


class AuxPass:
    """Tek etiketleme geçişinin bağlamı: aşma tahmini TEMBEL ve geçiş içinde bir kez (yalnız bir kayıt kesinleşirse).
    `history`: defterin gerçek kapanış listesini döndüren çağrılabilir (ör. `lambda: ledger.history`); `cache`: geçişler
    arası önbellek (kayıtçıda / motorda tutulur) — geçmiş (kimlik, uzunluk, son kayıt) değişmedikçe yeniden hesaplanmaz."""

    def __init__(self, history: Callable[[], Any] | None = None, cache: dict | None = None) -> None:
        self._history = history
        self._cache = cache if cache is not None else {}
        self._ov: tuple[float | None, int] | None = None

    def overshoot(self) -> tuple[float | None, int]:
        if self._ov is None:
            self._ov = self._compute()
        return self._ov

    def _compute(self) -> tuple[float | None, int]:
        if self._history is None:
            return None, 0
        try:
            h = self._history()
            seq = h if isinstance(h, list) else list(h or ())
            sig = (id(h), len(seq), id(seq[-1]) if seq else None)
            hit = self._cache.get("overshoot")
            if isinstance(hit, tuple) and len(hit) == 2 and hit[0] == sig:
                return hit[1]
            est = overshoot_estimate(seq)
        except Exception:  # noqa: BLE001 — gerçek geçmiş okunamazsa tahmin yok (null), etiket etkilenmez
            return None, 0
        self._cache["overshoot"] = (sig, est)
        return est

    def build(self, view: Any, arr: tuple, out: dict[str, Any], *, model: Any, filters: Any = None,
              funding_lookup: Any = None) -> dict[str, Any] | None:
        """Kesinleşen NET etiketin `aux` sözlüğü; anahtar kapalıysa None. Arıza → yalnız `aux_status` (sayı yok)."""
        if not ENABLED:
            return None
        try:
            return _build(self, view, arr, out, model=model, filters=filters, funding_lookup=funding_lookup)
        except Exception as exc:  # noqa: BLE001 — yardımcı etiket arızası net etiketi ETKİLEMEZ
            return {"aux_version": AUX_VERSION, "aux_status": "AUX_ERROR:%s" % type(exc).__name__}


def entry_bar_check(view: Any, arr: tuple) -> dict[str, Any]:
    """Giriş barı (`ts ≤ created < ts + tf`) ELDEKİ kapanmış çerçevede mi; varsa ters ucu stopa değdi mi."""
    ts, hi, lo = arr
    tf_ms = int(view.tf_minutes) * 60_000
    created_ms = int(from_iso(view.created_at).timestamp() * 1000)
    i = int(np.searchsorted(ts, created_ms, side="right")) - 1
    if i < 0 or int(ts[i]) + tf_ms <= created_ms:
        return {"checked": False, "stop_hit": None, "adverse": None, "why": "ENTRY_BAR_NOT_IN_FRAME"}
    long = str(view.direction).upper() == "LONG"
    adv = float(lo[i]) if long else float(hi[i])
    stop = float(view.stop)
    hit = (adv <= stop) if long else (adv >= stop)                # brüt etiketleyicinin karşılaştırması (değme dahil)
    return {"checked": True, "stop_hit": bool(hit), "adverse": adv, "bar_close_ms": int(ts[i]) + tf_ms}


def _entry_bar_stop_r(view: Any, *, model: Any, filters: Any, funding_lookup: Any, close_ms: int,
                      entry_fill: float | None) -> float | None:
    """Giriş barında seviyeden stopun NET R'si — `learning_cf._net_replay` ile AYNI açılış (atılık defter, aynı giriş dolumu
    kuralı, 1x, aynı büyüklük) ve aynı stop dolumu (seviye + çıkış kayması), barın kapanış anında tek fiyat-yalnız tick.
    Giriş dolumu net etiketinkiyle aynı değilse (beklenmez) None."""
    sym = str(view.symbol)
    f = _replay_filters(sym, filters)
    entry, stop = D(float(view.entry)), D(float(view.stop))
    targets = [] if view.variant == "hold_h" else [D(float(t)) for t in (view.targets or [])]
    created = from_iso(view.created_at)
    led = model.new_ledger()
    pre_filled = str(((view.features or {}) if isinstance(view.features, dict) else {}).get("entry_ref") or "") == \
        "quantized_entry"
    pos = led.open(sym, view.direction, entry, SizeSpec(_replay_notional(f, entry), AmountType.NOTIONAL, 1), stop=stop,
                   targets=targets, filters=f, now=created, slippage=SlippageModel.zero() if pre_filled else None)
    if pos is None:
        return None
    ent = [x for x in pos.fills if x.kind == "entry"]
    if not ent or entry_fill is None or abs(float(ent[0].price) - float(entry_fill)) > 1e-9 * max(1.0, abs(entry_fill)):
        return None
    at = datetime.fromtimestamp(int(close_ms) / 1000.0, tz=timezone.utc)
    px = pos.stop if pos.stop is not None else stop
    recs = led.tick({sym: TickData(last=px, mark=px, ts=iso(at))}, now_utc=at, funding_rate_lookup=_quiet_rates(funding_lookup),
                    bar_advance=True)
    if not recs or str(recs[-1].exit_reason) != EXIT_STOP:
        return None
    return _r6(float(recs[-1].r_multiple))


def _full_qty_at_be(view: Any, model: Any) -> bool:
    """Başa-baş stop çıkışında miktarın TAMAMI açık mıydı: TP1 kısmi kapanışı olamıyorsa (hedefsiz / tek hedef / TP1 tamamını
    kapatır) başa-baş yalnız MFE başa-başıdır."""
    tg = [] if view.variant == "hold_h" else list(view.targets or [])
    return len(tg) < 2 or Decimal(str(model.tp1_fraction)) >= 1


def _build(ap: AuxPass, view: Any, arr: tuple, out: dict[str, Any], *, model: Any, filters: Any,
           funding_lookup: Any) -> dict[str, Any]:
    r_net = _fin(out.get("r_net"))
    eb = entry_bar_check(view, arr)
    close_ms = eb.pop("bar_close_ms", None)
    ov_pct, ov_n = ap.overshoot()
    e_fill, stop = _fin(out.get("entry_fill")), float(view.stop)
    stop_pct = (abs(e_fill - stop) / e_fill * 100.0) if (e_fill is not None and e_fill > 0 and e_fill != stop) else None
    ov_r = (ov_pct / stop_pct) if (ov_pct is not None and stop_pct) else None
    # 1) örneklenmiş dolum tahmini: yalnız seviyeden dolan (tam miktarlı) stop çıkışları düzeltilir
    reason, basis = str(out.get("net_exit_reason") or ""), str(out.get("net_exit_basis") or "")
    if r_net is None:
        r_sampled = None
    elif reason not in (EXIT_STOP, EXIT_BE_STOP) or basis != CF_LEVEL_BASIS:
        r_sampled = r_net
    elif reason == EXIT_BE_STOP and not _full_qty_at_be(view, model):
        r_sampled = None
    else:
        r_sampled = _r6(r_net - ov_r) if ov_r is not None else None
    # 2) giriş barı: ters uç stopa değdiyse o barda seviyeden stop (aynı net yol)
    if r_net is None or not eb["checked"]:
        r_eb = None
    elif eb["stop_hit"]:
        r_eb = _entry_bar_stop_r(view, model=model, filters=filters, funding_lookup=funding_lookup, close_ms=int(close_ms),
                                 entry_fill=e_fill)
    else:
        r_eb = r_net
    # 3) ikisi birlikte
    if r_eb is None:
        r_cons = None
    elif eb["stop_hit"]:
        r_cons = _r6(r_eb - ov_r) if ov_r is not None else None
    else:
        r_cons = r_sampled
    return {"aux_version": AUX_VERSION, "entry_bar": eb, "r_net_entry_bar": r_eb, "overshoot_pct_est": ov_pct,
            "overshoot_n": int(ov_n), "r_net_sampled_est": r_sampled, "r_net_conservative": r_cons}


def aux_of(outcome: Any) -> dict[str, Any] | None:
    """Sonucun `aux` sözlüğü (yoksa / bozuksa None) — okuyucular için tek giriş."""
    a = outcome.get("aux") if isinstance(outcome, dict) else None
    return a if isinstance(a, dict) and a.get("aux_version") else None


__all__ = ["AUX_VERSION", "AuxPass", "CF_LEVEL_BASIS", "ENABLED", "OVERSHOOT_BASIS", "OVERSHOOT_MIN_N", "OVERSHOOT_SOURCE",
           "OVERSHOOT_WINDOW", "aux_of", "entry_bar_check", "overshoot_estimate"]
