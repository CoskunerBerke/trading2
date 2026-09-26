# -*- coding: utf-8 -*-
"""MUM VARYASYONLARI (4h) — C4 kâğıt defterinin kuralı; canlı motor ile replay'in ORTAK tek kaynağı (2026-09-26).

Defter YALNIZ kullanıcının gönderdiği, çevirisi onaylanmış, laboratuvarda ölçülmüş ve kullanıcının açıkça izin verdiği mum
varyasyonlarını işler. Liste (`rule_params.variations`) BOŞ başlar: defter açıktır ama işlem açmaz ("varyasyon bekliyor").
PAPER, gerçek para YOK, kaldıraç 1. Kâr garantisi değildir.

Kural (docs/CANDLE_VARIATIONS_4H.md):

* Pencere: son `WINDOW` (=500) kapanmış 4h bar, hacim dahil (`window_rows`). Okunamayan pencere (eksik bar, zaman
  boşluğu, okunamaz OHLC, hacim yok) → sinyal yok (fail-closed; yeni listelenen coin hiç sinyal üretmez).
* Dedektör: `candle_dsl.detect_last(pencere, varyasyon)` — laboratuvarın (`candle_lab`) AYNI pencereyle çağırdığı TEK
  fonksiyon. Parite tanım gereğidir; EMA200 de "son 500 barın EMA200'ü"dür.
* Kapı: her varyasyon `candle_variations.gate` ile denetlenir (çeviri onayı, CI laboratuvar kaydı, `definition_sha`,
  hüküm, kullanıcı onayı). Kapı ASLA yükseltmez; reddedilen varyasyon yalnız kendisi kapanır, nedeni `rule_state`te.
* Öncelik: config listesinin sırası. Aynı barda eşleşen diğerleri `also_matched`e yazılır.
* Giriş: sinyal kapanışından sonraki ilk doğrulanmış perp fiyatı, en geç `entry_window_min` (60 dk); geç giriş yok.
  Stop ve ATR14 dedektörden; hedef GİRİŞ fiyatından `target_r` × risk (`apply_action.target_r_from_entry`).
* Çıkış: stop ve hedef defterden (tick); zaman sınırı burada — girişin barından sayılan `max_hold_bars` bar kapanınca.
  H girişteki anlık görüntüden (`features.candle_variation.max_hold_bars`) okunur: varyasyon sonradan emekli edilse de
  açık pozisyon kendi kuralıyla biter.

Fonksiyonlar SAFTIR: dosya/ağ yok (yalnız kapının laboratuvar kaydı okuması), girdiler değiştirilmez. Barlar KRONOLOJİK
ve KAPANMIŞ olmalı (çağıran `window_rows`/`closed_bars` uygular). Tek yan etki: kapının reddi süreç başına (kimlik,
neden) bir kez günlüğe yazılır — etkinleştirilmiş ama geçemeyen varyasyon sunucuda sessiz kalmaz.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from . import candle_dsl, candle_variations
from .candle_confirmation import closed_bars
from .timeframes import tf_ms

VARIANTS = ("c4_candle_variations",)
TIMEFRAME = "4h"
STEP_MS = tf_ms(TIMEFRAME)
WINDOW = candle_dsl.WINDOW
#: İşlem kaydındaki kurulum adı: "candle:<id>" (karne `by_setup`/`by_variation` bununla ayırır).
SETUP_PREFIX = "candle:"
#: Satır okuyucunun sütunları — box okuyucusundan FARKLI olarak hacim dahil (mum hacim oranı koşulları).
ROW_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
EVIDENCE = "Yalnız kullanıcının onayladığı ve laboratuvarda ölçülen mum varyasyonları; PAPER, kâr garantisi değildir."

log = logging.getLogger(__name__)
#: Günlüğe yazılmış kapı retleri (kimlik, neden): süreç başına bir kez.
_GATE_LOGGED: set[tuple[str, str]] = set()


def _note_gate_refusal(vid: Any, why: Any) -> None:
    """Etkin listedeki varyasyon kapıdan geçmedi: süreç başına (kimlik, neden) bir kez UYARI. ASLA yükseltmez."""
    try:
        key = (str(vid), str(why))
        if key in _GATE_LOGGED:
            return
        _GATE_LOGGED.add(key)
        log.warning("C4 mum varyasyonu %s kapıdan geçmedi: %s — işlem açmaz (ayrıntı: rule_state)", key[0], key[1])
    except Exception:  # noqa: BLE001
        pass


@dataclass(frozen=True)
class CandleParams:
    """Yalnız UYGULAMA ayarları ve etkin varyasyon listesi. Varyasyonun TANIMI kayıttadır (`candle_variations`) ve
    buradan DEĞİŞTİRİLEMEZ."""

    leverage: int = 1
    #: 0 = kaldıraç yükseltilmez. Tek pozisyon tavanını aşan işlem küçültülerek açılır.
    leverage_max: int = 0
    #: Sinyal kapanışından sonra bu kadar dakika içinde girilmezse sinyal kaçmış sayılır.
    entry_window_min: int = 60
    #: Etkin varyasyon kimlikleri; liste SIRASI önceliktir. Boş = defter bekler.
    variations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # YAML listesi → demet (dondurulmuş nesne). Diğer tipler `validate`te reddedilir.
        if self.variations is None:
            object.__setattr__(self, "variations", ())
        elif isinstance(self.variations, list):
            object.__setattr__(self, "variations", tuple(self.variations))

    def validate(self) -> "CandleParams":
        if int(self.leverage) != 1:
            raise ValueError("leverage 1 olmalı (mum varyasyonu defteri, PAPER): %r" % (self.leverage,))
        if int(self.leverage_max) != 0:
            raise ValueError("leverage_max 0 olmalı (kaldıraç yükseltilmez): %r" % (self.leverage_max,))
        if not (1 <= int(self.entry_window_min) <= 240):
            raise ValueError("entry_window_min 1 ile 240 dakika arasında olmalı: %r" % (self.entry_window_min,))
        if not isinstance(self.variations, tuple):
            raise ValueError("variations kimlik listesi olmalı: %r" % (self.variations,))
        known = {e.get("id") for e in tuple(candle_variations.VARIATIONS) if isinstance(e, dict)}
        seen: set[str] = set()
        for vid in self.variations:
            if not isinstance(vid, str) or not candle_dsl.ID_PATTERN.fullmatch(vid):
                raise ValueError("variations: geçersiz kimlik %r (^CV\\d{3}_[A-Z0-9_]{2,40}$)" % (vid,))
            if vid in seen:
                raise ValueError("variations: kimlik iki kez yazılmış: %r" % (vid,))
            if vid not in known:
                raise ValueError("variations: kayıtta olmayan kimlik %r (tradingbot/candle_variations.py)" % (vid,))
            seen.add(vid)
        return self


DEFAULT_PARAMS = CandleParams()


# ---------------------------------------------------------------------------- satırlar
def rows_from_frame(frame, tail: int = WINDOW + 2) -> list[dict[str, Any]]:
    """DataFrame'den kural satırları (timestamp + OHLC + HACİM). OHLC eksikse boş liste; hacim sütunu yoksa satırlar
    hacimsiz döner ve pencere `VOLUME_MISSING` ile reddedilir (fail-closed, gerekçe görünür)."""
    if frame is None:
        return []
    try:
        cols = [c for c in ROW_COLUMNS if c in frame.columns]
        if not all(c in cols for c in ROW_COLUMNS[:5]):
            return []
        return frame.tail(int(tail))[cols].to_dict("records")
    except (AttributeError, TypeError, ValueError, KeyError):
        return []


def window_rows(frames: dict | None, now_ms: int) -> list[dict[str, Any]]:
    """Kuralın okuduğu pencere: `now_ms` anında KAPANMIŞ son `WINDOW` 4h bar (oluşan bar düşer). Karar ve panel AYNI
    okumayı kullanır (`paper_rules._frames_rows` / `intraday_for`)."""
    fr = (frames or {}).get(TIMEFRAME)
    return closed_bars(rows_from_frame(fr, tail=WINDOW + 2), now_ms=int(now_ms), tf=TIMEFRAME)[-WINDOW:]


# ---------------------------------------------------------------------------- yardımcılar
def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _dig(d: Any, *keys: str) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def lab_info(vid: str) -> dict[str, Any]:
    """Laboratuvar kaydının özeti (işlem kaydına anlık görüntü olarak girer). Kayıt okunamazsa alanlar None. ASLA
    yükseltmez."""
    out: dict[str, Any] = {"lab_verdict": None, "lab_run_url": None, "lab_oos_mean_r": None, "lab_oos_ci95": None,
                           "lab_oos_n": None}
    try:
        rec = candle_variations.read_record(vid)
        if not rec:
            return out
        oos = _dig(rec, "by_tf", str(rec.get("primary_tf") or TIMEFRAME), "OOS") or {}
        ci = oos.get("ci95") if isinstance(oos, dict) else None
        out.update(lab_verdict=rec.get("verdict") if isinstance(rec.get("verdict"), str) else None,
                   lab_run_url=_dig(rec, "run", "run_url"), lab_oos_mean_r=_f(oos.get("mean_r")) if isinstance(oos, dict) else None,
                   lab_oos_ci95=[_f(ci[0]), _f(ci[1])] if isinstance(ci, (list, tuple)) and len(ci) == 2 else None,
                   lab_oos_n=oos.get("n") if isinstance(oos, dict) and isinstance(oos.get("n"), int) else None)
    except Exception:  # noqa: BLE001 — bilgi alanı; kayıt bozuksa işlem yine kapının hükmüyle açılır
        pass
    return out


def _snapshot(var: Any, hit: Any) -> dict[str, Any]:
    """`features.candle_variation`: işlemin hangi tanımla, hangi kanıtla ve hangi çıkışla açıldığı (sonradan değişmez)."""
    info = lab_info(var.id)
    return {"id": var.id, "definition_sha": var.definition_sha, "dsl_version": candle_dsl.DSL_VERSION,
            "lab_verdict": info["lab_verdict"], "lab_run_url": info["lab_run_url"], "lab_oos_mean_r": info["lab_oos_mean_r"],
            "lab_oos_ci95": info["lab_oos_ci95"], "observation": bool((var.approval or {}).get("observation") is True),
            "target_r": var.target_r, "max_hold_bars": int(var.max_hold_bars),
            "pattern_high": float(hit.pattern_high), "pattern_low": float(hit.pattern_low)}


def _hold_bars(position: dict[str, Any]) -> int | None:
    """Açık pozisyonun zaman sınırı (bar): önce girişteki anlık görüntü, yoksa kayıttaki tanım (`setup_type`)."""
    snap = _dig(position.get("features") or {}, "candle_variation")
    h = snap.get("max_hold_bars") if isinstance(snap, dict) else None
    if isinstance(h, (int, float)) and not isinstance(h, bool) and float(h).is_integer() and 1 <= int(h) <= 300:
        return int(h)
    st = str(position.get("setup_type") or "")
    if st.startswith(SETUP_PREFIX):
        try:
            return int(candle_variations.get(st[len(SETUP_PREFIX):]).max_hold_bars)
        except Exception:  # noqa: BLE001 — bilinmeyen/bozuk kimlik: çağıran TIME_STOP_UNKNOWN_VARIATION döner
            return None
    return None


def _time_stop(variant: str, rows: list[dict[str, Any]], position: dict[str, Any]) -> dict[str, Any] | None:
    opened = position.get("opened_ts")
    if opened is None:
        return None
    hold = _hold_bars(position)
    if hold is None:
        return {"action": "NONE", "reason": "TIME_STOP_UNKNOWN_VARIATION", "name": variant}
    try:
        last = int(rows[-1]["timestamp"]) if rows else None
    except (KeyError, TypeError, ValueError):
        last = None
    if last is None:
        return None
    entry_bar_open = int(opened) // STEP_MS * STEP_MS
    held = (last + STEP_MS - entry_bar_open) // STEP_MS          # girişin barı dahil kapanmış bar sayısı
    if held >= hold:
        # laboratuvar: çıkış close[j+H-1] (j = giriş barı); kesintide kaçan barlar `late_bars`a yazılır
        return {"action": "CLOSE", "reason": "TIME_STOP_%d_BARS" % hold, "name": variant, "late_bars": int(held - hold)}
    return None


# ---------------------------------------------------------------------------- karar
def decide(variant: str = VARIANTS[0], *, rows: list[dict[str, Any]], position: dict[str, Any] | None = None,
           now_ms: int | None = None, params: CandleParams = DEFAULT_PARAMS) -> dict[str, Any] | None:
    """Tek karar: {"action": "OPEN"|"CLOSE"|"NONE", ...} ya da None. Bilinmeyen veri → None (fail-closed).

    `position`: {"opened_ts", "setup_type", "features"}. Açık pozisyonda yalnız zaman sınırı denetlenir (stop/hedef
    defterden); yeni sinyal açık pozisyonu değiştirmez."""
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen strateji varyanti: %r" % (variant,))
    p = params.validate()
    if position:
        return _time_stop(variant, list(rows or []), position)
    if not p.variations:
        return None                                     # defter bekler: etkin varyasyon yok
    win, _why = candle_dsl.window_from_rows(rows, STEP_MS)
    if win is None:
        return None
    signal_close_ms = int(win.ts[-1]) + STEP_MS
    if now_ms is not None and int(now_ms) - signal_close_ms > int(p.entry_window_min) * 60_000:
        return None                                     # sinyal kaçtı (kesinti): geç giriş laboratuvarda yok
    hits: list[tuple[Any, Any]] = []
    for vid in p.variations:                            # config sırası = öncelik
        var, gate_why = candle_variations.gate(vid)
        if var is None:
            _note_gate_refusal(vid, gate_why)
            continue
        hit = candle_dsl.detect_last(win, var)
        if hit is not None:
            hits.append((var, hit))
    if not hits:
        return None
    var, hit = hits[0]
    act: dict[str, Any] = {
        "action": "OPEN", "direction": var.side, "stop": float(hit.stop), "targets": [],
        "leverage": int(p.leverage), "leverage_max": int(p.leverage_max), "reason": "CANDLE_" + var.id, "name": variant,
        "setup_type": SETUP_PREFIX + var.id, "lab_algo": var.id,
        "signal_close": float(hit.close), "signal_ts": int(hit.signal_ts), "signal_close_ms": int(hit.signal_close_ms),
        "atr14": float(hit.atr_i),
        # uygulayıcıya: tanımın risk aralığı (laboratuvarın STOP_TOO_CLOSE/FAR sınırı), aynı sinyalle tek giriş,
        # tavanı aşan işlem reddedilmez küçültülür (kaldıraç 1, risk bütçenin altında kalır)
        "risk_atr_bounds": [float(var.risk_atr_bounds[0]), float(var.risk_atr_bounds[1])], "one_entry_per_signal": True,
        "cap_notional_to_position_pct": True,
        "variation": _snapshot(var, hit), "also_matched": [v.id for v, _h in hits[1:]]}
    if var.target_r is not None:
        act["target_r_from_entry"] = float(var.target_r)   # hedef GERÇEK girişten ölçülür (laboratuvar: sonraki açılış)
    return act


# ---------------------------------------------------------------------------- panel
def rule_state(variant: str = VARIANTS[0], *, rows: list[dict[str, Any]],
               params: CandleParams = DEFAULT_PARAMS) -> dict[str, Any]:
    """Kuralın okuduğu değerler — gösterim için, `decide` ile AYNI pencere ve AYNI kapı. Etkin her varyasyon için
    eşleşme, ilk başarısız koşul ve bekleyen kırılım (`candle_dsl.explain_last`)."""
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen strateji varyanti: %r" % (variant,))
    p = params.validate()
    rows = list(rows or [])
    out: dict[str, Any] = {"variant": variant, "ok": False, "timeframe": TIMEFRAME, "window": WINDOW, "n_bars": len(rows),
                           "signal_ts": None, "reason": None, "window_reason": None,
                           "volume_present": bool(rows) and all(isinstance(r, dict) and _f(r.get("volume")) is not None
                                                                for r in rows),
                           "status_tr": None, "evidence": EVIDENCE, "variations": []}
    win, why = candle_dsl.window_from_rows(rows, STEP_MS)
    if win is not None:
        out.update(ok=True, signal_ts=int(win.ts[-1]))
    out["window_reason"] = why
    if not p.variations:
        out.update(reason="NO_VARIATIONS", status_tr="varyasyon bekliyor")
        return out
    out["reason"] = why
    for vid in p.variations:
        var, gwhy = candle_variations.gate(vid)
        row: dict[str, Any] = {"id": vid, "active": var is not None, "gate_reason": gwhy, "matched": None,
                               "first_fail": None, "first_fail_clause": None, "pending_break": None, "stop_if_open": None,
                               "lab_verdict": lab_info(vid)["lab_verdict"],
                               "observation": bool((var.approval or {}).get("observation") is True) if var is not None else None}
        if var is not None and win is not None:
            try:
                e = candle_dsl.explain_last(win, var)
                row.update(matched=bool(e["matched"]), first_fail=e["first_fail"], first_fail_clause=e["first_fail_clause"],
                           pending_break=(e["pending_breaks"][0] if e["pending_breaks"] else None),
                           stop_if_open=(e["hit"] or {}).get("stop"))
            except Exception as exc:  # noqa: BLE001 — panel satırı arızayı gösterir, diğer varyasyonlar etkilenmez
                row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        out["variations"].append(row)
    out["status_tr"] = ("%d/%d varyasyon etkin" % (sum(1 for r in out["variations"] if r["active"]), len(out["variations"])))
    return out


__all__ = ["DEFAULT_PARAMS", "CandleParams", "EVIDENCE", "ROW_COLUMNS", "SETUP_PREFIX", "STEP_MS", "TIMEFRAME", "VARIANTS",
           "WINDOW", "decide", "lab_info", "rows_from_frame", "rule_state", "window_rows"]
