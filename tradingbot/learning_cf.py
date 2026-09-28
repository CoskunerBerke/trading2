"""ÖĞRENME MODU KARŞI-OLGUSAL KAYDI (2026-09-28, öğrenme modu) — defter başına bir kayıtçı.

Öğrenme modunda bile açılamayan geçerli sinyal kaybolmaz: `learn.shadow.ShadowBook` üstüne (aynı `ShadowTrade` şeması,
`is_counterfactual=True`, arşiv) kaydedilir ve kapanmış barlarla etiketlenir. Sözleşme:

* `plan_id` = barın SİNYAL ANAHTARI (P1) — tur kimliğinden TÜRETİLMEZ; aynı bar 16 tur boyunca bloke kalsa da tek kayıt.
* Tekillik anahtarı (defter, sinyal anahtarı, sembol, yön, varyasyon) — etiketlenmiş kayıt da tekrarı engeller.
* Yalnız `as_planned` varyantı; defter, özkaynak ve P&L karnesine DOKUNMAZ.
* Bekleyen kayıt tavanı (`max_pending`): aşılırsa EN ESKİ bekleyen düşürülür ve `dropped` sayılır.
* Etiketleme yalnız `now` anında KAPANMIŞ barlarla (açılış + tf ≤ now), önce stop sonra hedef (`label_with_candles`).
  TARGET_STOP_TIME: ufuk kuralın en uzun tutma süresi. HORIZON: 30 bar, `approx=True`. RULE_EXIT (kural çıkış yüklemi)
  bu sürümde HORIZON ile etiketlenir (`label_method=HORIZON_FALLBACK`, `approx=True`).
* `record`/`label_pending` diske YAZMAZ; geçiş sonunda `save()` çağrılır (tek atomik yazım).
* Aynı sinyal sonraki bir turda GERÇEK işlem olarak açılırsa bekleyen kaydı `supersede` ile düşürülür (aynı gözlem iki kez
  sayılmaz; `superseded` sayacı).
* Etiketleme ucuzdur (2026-09-28, öğrenme modu): yol, stop/hedef hiç değmediyse ve ufuk dolmadıysa yürütülmez (numpy ön
  kontrol); tam yürütme (`label_with_candles`) yalnız sonuç kesinleşebilecekken yapılır — sonuçlar aynıdır.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .core import from_iso, iso, stable_id
from .learn.shadow import ShadowBook, ShadowTrade, label_with_candles
from .learning_mode import counterfactual_ok
from .timeframes import TF_MS

LABEL_TARGET_STOP_TIME = "TARGET_STOP_TIME"
LABEL_RULE_EXIT = "RULE_EXIT"
LABEL_HORIZON = "HORIZON"
LABEL_KINDS = (LABEL_TARGET_STOP_TIME, LABEL_RULE_EXIT, LABEL_HORIZON)
#: HORIZON (ve bu sürümde RULE_EXIT) ufku — bar.
HORIZON_BARS = 30
#: Ufuk + bu kadar bar geçtiği hâlde kapanmış barlarla etiketlenemeyen kayıt (veri boşluğu) bekleyenden çıkarılır.
STALE_GRACE_BARS = 10
SCHEMA_VERSION = "learning_cf_v1"
_FINAL_EXITS = ("stop", "breakeven_stop", "target")
_TF_BY_MIN = {v // 60_000: k for k, v in TF_MS.items()}


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _clean(x: Any, depth: int = 0) -> Any:
    """JSON'a güvenli kopya: sonlu olmayan float → None, Decimal → float, datetime → ISO; derinlik sınırlı."""
    if depth > 6:
        return None
    if x is None or isinstance(x, (bool, int, str)):
        return x
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, Decimal):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, datetime):
        return iso(x)
    if isinstance(x, dict):
        return {str(k): _clean(v, depth + 1) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_clean(v, depth + 1) for v in x]
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return str(x)


def _to_ms(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return int(_aware(v).timestamp() * 1000)
    if isinstance(v, pd.Timestamp):
        t = v.tz_localize("UTC") if v.tzinfo is None else v.tz_convert("UTC")
        return int(t.value // 1_000_000)
    if isinstance(v, str):
        try:
            return int(from_iso(v).timestamp() * 1000)
        except ValueError:
            try:
                return int(float(v))
            except ValueError:
                return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _frame_rows(raw: Any) -> pd.DataFrame | None:
    """DataFrame (timestamp sütunu ya da datetime index) ya da dict satır listesi → `timestamp`(ms)+OHLC DataFrame."""
    if raw is None:
        return None
    if isinstance(raw, pd.DataFrame):
        if raw.empty or not {"high", "low", "close"} <= set(raw.columns):
            return None
        df = raw
        if "timestamp" in df.columns:
            ts = [_to_ms(v) for v in df["timestamp"].tolist()]
        else:
            ts = [_to_ms(v) for v in df.index.tolist()]
        rows = [{"timestamp": t, "high": h, "low": lo, "close": c}
                for t, h, lo, c in zip(ts, df["high"].tolist(), df["low"].tolist(), df["close"].tolist())]
    elif isinstance(raw, (list, tuple)):
        rows = []
        for r in raw:
            if not isinstance(r, dict):
                continue
            t = _to_ms(r.get("timestamp", r.get("open_time", r.get("ts", r.get("t")))))
            rows.append({"timestamp": t, "high": r.get("high"), "low": r.get("low"), "close": r.get("close")})
    else:
        return None
    clean = []
    for r in rows:
        try:
            t, h, lo, c = r["timestamp"], float(r["high"]), float(r["low"]), float(r["close"])
        except (TypeError, ValueError, KeyError):
            continue
        if t is None or not (math.isfinite(h) and math.isfinite(lo) and math.isfinite(c)):
            continue
        clean.append({"timestamp": int(t), "high": h, "low": lo, "close": c})
    if not clean:
        return None
    df = pd.DataFrame(clean).drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    return df


def _frame_for(frames: dict[str, Any] | None, tf_minutes: int) -> Any:
    if not frames:
        return None
    key = _TF_BY_MIN.get(int(tf_minutes))
    raw = frames.get(key) if key is not None else None
    if raw is None:
        raw = frames.get(int(tf_minutes))
    if raw is None:
        raw = frames.get(str(tf_minutes))
    return raw


def _eval_view(t: ShadowTrade) -> ShadowTrade:
    """Etiketleme görünümü: TARGET_STOP_TIME kaydın kendisi; HORIZON/RULE_EXIT hedefsiz, ufuk kapanışında R
    (stop yine ÖNCE) — `label_with_candles`ın `hold_h` yolu. Kaydın kendi varyantı değişmez."""
    if str(t.label_kind or LABEL_TARGET_STOP_TIME) == LABEL_TARGET_STOP_TIME:
        return t
    return replace(t, variant="hold_h")


def _may_finalise(v: ShadowTrade, arr: tuple, created_ms: int, label_ms: int) -> bool:
    """Ucuz ön kontrol (muhafazakâr ÜST küme): yolda stop ya da hedef seviyesine değen bar var mı. False → tam yürütme
    `horizon` dışında bir sonuç VEREMEZ (ufuk dolmadıysa kesinleşmez), yürütmeye gerek yok."""
    if v.variant not in ("as_planned", "hold_h"):
        return True
    ts, hi, lo = arr
    i0 = int(np.searchsorted(ts, created_ms, side="left"))          # ⊇ `ts > created`
    i1 = int(np.searchsorted(ts, label_ms, side="right"))           # `ts <= label`
    if i1 <= i0:
        return False
    h, lw = hi[i0:i1], lo[i0:i1]
    long = v.direction == "LONG"
    if bool((lw <= v.stop).any()) if long else bool((h >= v.stop).any()):
        return True
    tg = list(v.targets or []) if v.variant != "hold_h" else []
    if tg:
        return bool((h >= min(tg)).any()) if long else bool((lw <= max(tg)).any())
    return False


def label_records(trades: list[ShadowTrade], frames_by_symbol: dict[str, dict[str, Any]] | None,
                  now: datetime) -> tuple[int, list[ShadowTrade]]:
    """Bekleyen kayıtları (outcome None) YALNIZ `now` anında kapanmış barlarla etiketler; kayıtları yerinde günceller.

    Kesinleşme: stop/hedef (önce stop) ya da ufuk penceresinin son barı kapanmış VE veri pencereyi kapsıyor. Ufuk +
    `STALE_GRACE_BARS` geçtiği hâlde etiketlenemeyen kayıt (veri boşluğu / evrenden çıkmış sembol) bayattır.
    Döner: (bu çağrıda etiketlenen sayı, bayat kayıtlar). Defter başı kayıtçı ve ana botun öğrenme gölgeleri ORTAK."""
    now = _aware(now)
    now_ms = int(now.timestamp() * 1000)
    n = 0
    stale: list[ShadowTrade] = []
    cache: dict[tuple, tuple[pd.DataFrame, tuple] | None] = {}
    for t in trades:
        if t.outcome is not None:
            continue
        tf_ms = int(t.tf_minutes) * 60_000
        label_ms = int(from_iso(t.label_ts).timestamp() * 1000)
        ck = (t.symbol, int(t.tf_minutes))
        if ck not in cache:
            df = _frame_rows(_frame_for((frames_by_symbol or {}).get(t.symbol), t.tf_minutes))
            if df is not None:
                df = df[df["timestamp"] + tf_ms <= now_ms].reset_index(drop=True)   # yalnız kapanmış barlar
            if df is not None and not df.empty:
                cache[ck] = (df, (df["timestamp"].to_numpy(dtype="int64"), df["high"].to_numpy(dtype=float),
                                  df["low"].to_numpy(dtype=float)))
            else:
                cache[ck] = None
        hit = cache[ck]
        horizon_done = now_ms >= label_ms + tf_ms
        if hit is not None:
            df, arr = hit
            v = _eval_view(t)
            created_ms = int(from_iso(t.created_at).timestamp() * 1000)
            res = label_with_candles(v, df) if (horizon_done or _may_finalise(v, arr, created_ms, label_ms)) else None
            if res is not None:
                final = res.get("exit_reason") in _FINAL_EXITS
                if not final and horizon_done:
                    # ufuk penceresinin son barı kapandı; veri pencereyi kapsıyor mu (boşluk → bekle)
                    in_win = df[(df["timestamp"] <= label_ms)]
                    final = (not in_win.empty) and int(in_win["timestamp"].iloc[-1]) > label_ms - tf_ms
                if final:
                    kind = str(t.label_kind or LABEL_TARGET_STOP_TIME)
                    out = dict(res)
                    out.update({"label_kind": kind, "approx": bool(t.approx),
                                "label_method": ("PATH" if kind == LABEL_TARGET_STOP_TIME else
                                                 LABEL_HORIZON if kind == LABEL_HORIZON else "HORIZON_FALLBACK")})
                    t.outcome, t.labeled_at = out, iso(now)
                    n += 1
                    continue
        if now_ms > label_ms + (STALE_GRACE_BARS + 1) * tf_ms:
            stale.append(t)
    return n, stale


class CounterfactualRecorder:
    """Defter başına karşı-olgusal kayıtçı (`state_dir/counterfactual_trades.json`)."""

    def __init__(self, path: Path | str, *, book: str, max_pending: int = 2000, archive: Any | None = None):
        self.path = Path(path)
        self.book_name = str(book)
        self.max_pending = max(1, int(max_pending))
        self.sb = ShadowBook(self.path, archive=archive)
        m = self.sb.meta
        self.dropped = int(m.get("dropped", 0) or 0)
        self.expired = int(m.get("expired", 0) or 0)
        self.recorded_total = int(m.get("recorded_total", 0) or 0)
        self.superseded = int(m.get("superseded", 0) or 0)
        #: Defterin öğrenme sayaçlarının KALICI yedeği (2026-09-28, ikinci doğrulama turu): özetin `learning` alanı öğrenme
        #: kapalıyken ve eski kodda yazılmaz (sayaçlar silinirdi); bu dosyaya ise ne kapalı yol ne eski kod dokunur.
        self.book_counters: dict[str, int] = {str(k): int(v) for k, v in (m.get("book_counters") or {}).items()
                                              if isinstance(v, (int, float)) and not isinstance(v, bool)}
        self._keys: set[tuple] = {self._key(t.signal_key or t.plan_id, t.symbol, t.direction, t.variation)
                                  for t in self.sb.trades}
        #: Düşürülen kayıtların anahtarları (süreç içi, sınırlı): aynı bar yeniden kaydı tekrar tetiklemesin.
        self._gone: deque[tuple] = deque(maxlen=self.max_pending * 2)
        self._dirty = False

    # ------------------------------------------------------------ kimlik
    def _key(self, signal_key, symbol, direction, variation) -> tuple:
        return (self.book_name, str(signal_key), str(symbol), str(direction).upper(), str(variation or ""))

    # ------------------------------------------------------------ kayıt
    def record(self, *, signal_key: str, symbol: str, direction: str, entry: float, stop: float, targets: list[float],
               reason: str, created_at: datetime, tf_minutes: int, horizon_bars: int, label_kind: str,
               features: dict | None = None, variation: str | None = None, rule_version: str | None = None) -> bool:
        """Kaydedildiyse True. False: geçersiz geometri/girdi, neden karşı-olgusala uygun değil, ya da tekrar."""
        if not signal_key or not symbol or not counterfactual_ok(reason):
            return False
        side = str(direction or "").upper()
        if side not in ("LONG", "SHORT"):
            return False
        try:
            px, st = float(entry), float(stop)
            tf, h = int(tf_minutes), int(horizon_bars)
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(px) and math.isfinite(st)) or px <= 0 or st <= 0 or px == st:
            return False
        if (side == "LONG" and st >= px) or (side == "SHORT" and st <= px):
            return False
        kind = str(label_kind or "").upper()
        if kind not in LABEL_KINDS or tf <= 0 or not isinstance(created_at, datetime):
            return False
        if kind == LABEL_TARGET_STOP_TIME and h <= 0:
            return False
        key = self._key(signal_key, symbol, side, variation)
        if key in self._keys or key in self._gone:
            return False
        tgts = []
        for t in targets or []:
            try:
                tv = float(t)
            except (TypeError, ValueError):
                continue
            if math.isfinite(tv) and tv > 0 and ((tv > px) if side == "LONG" else (tv < px)):
                tgts.append(tv)
        horizon = h if kind == LABEL_TARGET_STOP_TIME else HORIZON_BARS
        created = _aware(created_at)
        st_rec = ShadowTrade(
            id="cf_" + stable_id("cf", *key), plan_id=str(signal_key), symbol=str(symbol), market_type="USDM_PERP",
            direction=side, created_at=iso(created), entry=px, stop=st, targets=tgts, horizon_bars=int(horizon),
            variant="as_planned", reason_not_opened=[str(reason)],
            label_ts=iso(created + timedelta(minutes=tf * horizon)), tf_minutes=tf, leverage=1.0,
            book=self.book_name, signal_key=str(signal_key), variation=(str(variation) if variation else None),
            label_kind=kind, features=_clean(dict(features)) if features else None, learning_unlocked=False,
            rule_version=(str(rule_version) if rule_version else None), approx=(kind != LABEL_TARGET_STOP_TIME))
        self.sb.trades.append(st_rec)
        self._keys.add(key)
        self.recorded_total += 1
        self._dirty = True
        self._enforce_cap()
        return True

    def _enforce_cap(self) -> None:
        pending = [t for t in self.sb.trades if t.outcome is None]
        over = len(pending) - self.max_pending
        if over <= 0:
            return
        drop = {id(t) for t in pending[:over]}          # EN ESKİ bekleyenler (liste ekleme sırası)
        for t in pending[:over]:
            k = self._key(t.signal_key or t.plan_id, t.symbol, t.direction, t.variation)
            self._keys.discard(k)
            self._gone.append(k)
        self.sb.trades = [t for t in self.sb.trades if id(t) not in drop]
        self.dropped += over

    def has_pending(self, *, symbol: str, reason: str, hypothetical_only: bool = False) -> bool:
        """Bu sembolde `reason` nedenli, henüz ETİKETLENMEMİŞ kayıt var mı (yön/varyasyon fark etmez)? A15 "+CF": taban
        kuralın varsayımsal pozisyonu (POSITION_OPEN kaydı) sonuçlanmadan aynı hareketin yeni sinyalleri kaydedilmez
        (2026-09-28, öğrenme modu; üçüncü doğrulama turu). `hypothetical_only`: taban kapılarının durduracağı kayıtlar
        (`features.baseline_blocked_by` dolu) varsayımsal pozisyon SAYILMAZ."""
        sym, r = str(symbol), str(reason)
        return any(t.outcome is None and t.symbol == sym and list(t.reason_not_opened or [])[:1] == [r]
                   and not (hypothetical_only and (t.features or {}).get("baseline_blocked_by"))
                   for t in self.sb.trades)

    # ------------------------------------------------------------ gerçek işlemle değişim
    def supersede(self, *, signal_key: str | None, symbol: str, direction: str, variation: str | None = None) -> int:
        """Aynı sinyal (defter, anahtar, sembol, yön, varyasyon) sonradan GERÇEK işlem olarak açıldı: karşı-olgusal kaydı
        düşürülür (aynı gözlem hem dolum hem "açılmadı" olarak sayılmasın). Anahtar tekillik kümesinde KALIR (yeniden
        kaydedilmez). Döner: düşürülen kayıt sayısı."""
        if not signal_key:
            return 0
        key = self._key(signal_key, symbol, str(direction or "").upper(), variation)
        keep, n = [], 0
        for t in self.sb.trades:
            if self._key(t.signal_key or t.plan_id, t.symbol, t.direction, t.variation) == key:
                n += 1
            else:
                keep.append(t)
        if n:
            self.sb.trades = keep
            self.superseded += n
            self._dirty = True
        return n

    # ------------------------------------------------------------ etiketleme
    @staticmethod
    def _frame_for(frames: dict[str, Any] | None, tf_minutes: int) -> Any:
        return _frame_for(frames, tf_minutes)

    def label_pending(self, frames_by_symbol: dict[str, dict[str, Any]], now: datetime) -> int:
        """Bekleyen kayıtları yalnız KAPANMIŞ barlarla etiketler (`label_records`). Döner: bu çağrıda etiketlenen sayı."""
        n, stale = label_records(self.sb.trades, frames_by_symbol, now)
        if n:
            self._dirty = True
        if stale:
            gone = {id(t) for t in stale}
            for t in stale:
                k = self._key(t.signal_key or t.plan_id, t.symbol, t.direction, t.variation)
                self._keys.discard(k)
                self._gone.append(k)
            self.sb.trades = [t for t in self.sb.trades if id(t) not in gone]
            self.expired += len(stale)
            self._dirty = True
        return n

    @staticmethod
    def _eval_view(t: ShadowTrade) -> ShadowTrade:
        return _eval_view(t)

    # ------------------------------------------------------------ kalıcılık / rapor
    def sync_book_counters(self, counters: dict | None) -> dict[str, int]:
        """Defter sayaçlarını yedekle birleştirir (anahtar başına EN BÜYÜK — sayaçlar yalnız artar) ve birleşik sözlüğü
        döner; yedek değiştiyse kayıt kirlenir (sonraki `save` yazar)."""
        merged = dict(self.book_counters)
        for k, v in (counters or {}).items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            merged[str(k)] = max(int(v), int(merged.get(str(k), 0)))
        if merged != self.book_counters:
            self.book_counters = merged
            self._dirty = True
        return dict(merged)

    def save(self) -> None:
        """Tek atomik yazım (arşiv taşması `ShadowBook.save` kurallarıyla). Değişiklik yoksa dokunmaz."""
        if not self._dirty and self.path.exists():
            return
        self.sb.meta = {"schema_version": SCHEMA_VERSION, "book": self.book_name, "dropped": self.dropped,
                        "expired": self.expired, "recorded_total": self.recorded_total, "superseded": self.superseded}
        if self.book_counters:
            self.sb.meta["book_counters"] = dict(self.book_counters)
        self.sb.save()
        self._dirty = False

    def stats(self) -> dict:
        pending = sum(1 for t in self.sb.trades if t.outcome is None)
        return {"book": self.book_name, "pending": pending, "labeled": len(self.sb.trades) - pending,
                "dropped": self.dropped, "expired": self.expired, "recorded_total": self.recorded_total,
                "superseded": self.superseded, "max_pending": self.max_pending}


__all__ = ["CounterfactualRecorder", "HORIZON_BARS", "LABEL_HORIZON", "LABEL_KINDS", "LABEL_RULE_EXIT",
           "LABEL_TARGET_STOP_TIME", "SCHEMA_VERSION", "label_records"]
