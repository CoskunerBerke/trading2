"""Offline gap reconciliation — worker kapalıyken kaçan stop/TP/likidasyon/funding olaylarını
arşiv mumlarıyla OLAY-ZAMANI sırasında uzlaştırır.

Kurallar (fail-closed):
* Yapay fiyat/mum/fill üretilmez; yalnız borsadan çekilen kapanmış mumlar defterin mevcut
  `tick()` yoluna (worst-case intrabar: LİKİDASYON > STOP > TP) bar bar verilir.
* Veri eksik/belirsizse HİÇBİR tick atılmaz (all-or-nothing): durum `GAP_AMBIGUOUS` yazılır,
  yeni girişler durur, çıkışlar canlı yoldan sürmeye devam eder ve kullanıcı müdahalesi istenir.
* Watermark (`state/exit_watermark.json`) yalnız başarılı uzlaştırma sonrası ilerler →
  ikinci restart aynı pencereyi yeniden uzlaştırmaz (kapanan pozisyon defterden düştüğü,
  funding `last_funding_settlement_utc` ile korunduğu için tick'ler zaten idempotenttir).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from ..core import atomic_write_json, from_iso, iso, read_json, utc_now

log = logging.getLogger(__name__)

WATERMARK_FILE = "exit_watermark.json"
GAP_STATUS_FILE = "gap_status.json"
MIN_GAP_S = 300                      # bundan kısa kesintiler canlı exit-monitor tarafından zaten kapsanır
_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000}
_4H_MS = 14_400_000
_MAX_PAGES = 40


def read_watermark(state_dir: Path | str) -> datetime | None:
    d = read_json(Path(state_dir) / WATERMARK_FILE, default=None)
    if not isinstance(d, dict) or not d.get("last_exit_check_utc"):
        return None
    try:
        return from_iso(str(d["last_exit_check_utc"]))
    except (ValueError, TypeError):
        return None


def write_watermark(state_dir: Path | str, when: datetime, run_id: str | None = None) -> None:
    atomic_write_json(Path(state_dir) / WATERMARK_FILE, {"last_exit_check_utc": iso(when), "run_id": run_id or ""})


def read_gap_status(state_dir: Path | str) -> dict:
    d = read_json(Path(state_dir) / GAP_STATUS_FILE, default=None)
    return d if isinstance(d, dict) else {"status": "OK"}


def choose_timeframe(gap_s: float) -> str:
    """Mümkün olan en küçük güvenilir mum: ≤48 sa → 1m, ≤10 gün → 5m, üstü → 15m."""
    if gap_s <= 48 * 3600:
        return "1m"
    if gap_s <= 10 * 86_400:
        return "5m"
    return "15m"


@dataclass
class GapReport:
    status: str = "OK"                      # OK | NOOP | GAP_AMBIGUOUS
    window: tuple[str, str] | None = None
    tf: str = ""
    symbols: list[str] = field(default_factory=list)
    closed: list[Any] = field(default_factory=list)   # kapanan pozisyon kayıtları (ledger record nesneleri)
    bars_replayed: int = 0
    decisions: list[str] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)
    blocked: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {"status": self.status, "window": list(self.window) if self.window else None, "tf": self.tf,
                "symbols": self.symbols, "closed": [getattr(r, "id", str(r)) for r in self.closed],
                "bars_replayed": self.bars_replayed, "decisions": self.decisions,
                "ambiguous": self.ambiguous, "blocked": self.blocked, "reason": self.reason}


class GapReconciler:
    """Futures defteri için kesinti penceresi uzlaştırıcısı. `provider_factory` → USDⓈ-M public
    kline/fundingRate sağlayıcısı (test edilebilirlik için enjekte edilir; ağ yalnız gerektiğinde açılır)."""

    def __init__(self, ledger: Any, ledger_path: Path, state_dir: Path,
                 provider_factory: Callable[[], Any], *, min_gap_s: float = MIN_GAP_S,
                 now_fn: Callable[[], datetime] = utc_now):
        self.ledger = ledger
        self.ledger_path = Path(ledger_path)
        self.state_dir = Path(state_dir)
        self.provider_factory = provider_factory
        self.min_gap_s = float(min_gap_s)
        self.now_fn = now_fn

    # ------------------------------------------------------------------ pencere
    def _window_start(self) -> datetime | None:
        wm = read_watermark(self.state_dir)
        if wm is not None:
            return wm
        upd = getattr(self.ledger, "updated_at", "") or ""
        if upd:
            try:
                return from_iso(str(upd))
            except (ValueError, TypeError):
                return None
        return None

    # ------------------------------------------------------------------ veri çekme
    def _fetch_klines(self, provider: Any, symbol: str, tf: str, start_ms: int, end_ms: int) -> list[dict]:
        """[start_ms, end_ms] penceresini ileri sayfalayarak KAPANMIŞ mumlar listesi döndürür."""
        step = _TF_MS[tf]
        out: list[dict] = []
        cursor = start_ms
        max_limit = int(getattr(provider, "max_kline_limit", 1000))
        for _ in range(_MAX_PAGES):
            df = provider.klines(symbol, tf, limit=max_limit, start_ms=cursor, end_ms=end_ms)
            if df is None or len(df) == 0:
                break
            for _, r in df.iterrows():
                ts = int(r["timestamp"])
                close_time = int(r.get("close_time") or (ts + step - 1))
                if close_time > end_ms:                 # kapanmamış / pencere dışı bar → alma
                    continue
                out.append({"ts": ts, "open": float(r["open"]), "high": float(r["high"]),
                            "low": float(r["low"]), "close": float(r["close"]), "close_time": close_time})
            last_ts = int(df["timestamp"].iloc[-1])
            if len(df) < max_limit or last_ts + step >= end_ms:
                break
            cursor = last_ts + step
        return out

    def _fetch_funding(self, provider: Any, symbol: str, start_ms: int, end_ms: int) -> dict[int, Decimal]:
        """Settlement zamanı (saat hassasiyetinde epoch-saat) → gerçek dönem oranı."""
        try:
            rows = provider.funding_history(symbol, limit=1000, start_ms=start_ms, end_ms=end_ms) or []
        except Exception as exc:  # noqa: BLE001 — funding geçmişi alınamazsa tahmini yol (estimated) devreye girer
            log.warning("%s funding geçmişi alınamadı (%s) — son bilinen oranla tahmin edilecek", symbol, exc)
            return {}
        out: dict[int, Decimal] = {}
        for r in rows:
            fts = int(r.get("funding_ts") or 0)
            rate = r.get("rate")
            if fts and rate is not None:
                out[fts // 3_600_000] = Decimal(str(rate))
        return out

    # ------------------------------------------------------------------ uzlaştırma
    def reconcile(self, run_id: str | None = None) -> GapReport:
        from ..accounting.models import TickData

        now = self.now_fn()
        rep = GapReport()
        start = self._window_start()
        positions = dict(getattr(self.ledger, "positions", {}) or {})
        if start is None:
            # İlk kurulum: pencere bilinemez → dürüstçe bootstrap (tick yok, fill yok), watermark başlat.
            write_watermark(self.state_dir, now, run_id)
            atomic_write_json(self.state_dir / GAP_STATUS_FILE, {"status": "OK", "at": iso(now), "note": "bootstrap: watermark yoktu"})
            rep.status, rep.reason = "NOOP", "watermark yok — bootstrap"
            return rep
        gap_s = (now - start).total_seconds()
        if gap_s < self.min_gap_s or not positions:
            write_watermark(self.state_dir, now, run_id)
            atomic_write_json(self.state_dir / GAP_STATUS_FILE, {"status": "OK", "at": iso(now), "gap_s": round(gap_s, 1)})
            rep.status, rep.reason = "NOOP", f"pencere {gap_s:.0f}s (<{self.min_gap_s:.0f}s) ya da açık pozisyon yok"
            return rep

        tf = choose_timeframe(gap_s)
        step = _TF_MS[tf]
        start_ms = int(start.timestamp() * 1000) - step          # pencere başındaki barı da kapsa
        end_ms = int(now.timestamp() * 1000)
        rep.window, rep.tf, rep.symbols = (iso(start), iso(now)), tf, sorted(positions)

        try:
            provider = self.provider_factory()
        except Exception as exc:  # noqa: BLE001
            provider = None
            rep.ambiguous.append({"symbol": "*", "reason": f"sağlayıcı açılamadı: {exc}"})
        candles: dict[str, list[dict]] = {}
        funding: dict[str, dict[int, Decimal]] = {}
        if provider is not None:
            for sym in rep.symbols:
                try:
                    rows = self._fetch_klines(provider, sym, tf, start_ms, end_ms)
                except Exception as exc:  # noqa: BLE001
                    rep.ambiguous.append({"symbol": sym, "reason": f"kline çekilemedi: {exc}"})
                    continue
                if not rows:
                    rep.ambiguous.append({"symbol": sym, "reason": "pencerede kapanmış mum yok"})
                    continue
                if rows[0]["ts"] > start_ms + 2 * step or rows[-1]["close_time"] < end_ms - 3 * step:
                    rep.ambiguous.append({"symbol": sym, "reason": f"kapsama eksik: {rows[0]['ts']}..{rows[-1]['close_time']} pencere {start_ms}..{end_ms}"})
                    continue
                candles[sym] = rows
                pos = positions.get(sym)
                if pos is not None and getattr(pos, "market_type", None) is not None and "PERP" in str(getattr(pos, "market_type", "")):
                    funding[sym] = self._fetch_funding(provider, sym, start_ms, end_ms)

        if rep.ambiguous:
            # ALL-OR-NOTHING: hiçbir tick atılmaz, watermark İLERLEMEZ → ikinci denemede aynı pencere yeniden ele alınır.
            rep.status, rep.blocked = "GAP_AMBIGUOUS", True
            rep.reason = "veri eksik/belirsiz — yapay fill üretilmedi; yeni girişler durduruldu, kullanıcı müdahalesi gerekli"
            atomic_write_json(self.state_dir / GAP_STATUS_FILE,
                              {"status": "GAP_AMBIGUOUS", "at": iso(now), "window": [iso(start), iso(now)], "tf": tf,
                               "ambiguous": rep.ambiguous, "note": rep.reason})
            log.error("GAP_AMBIGUOUS: %s", rep.ambiguous)
            return rep

        def rate_lookup(symbol: str, when: datetime):
            table = funding.get(symbol) or {}
            return table.get(int(when.timestamp() * 1000) // 3_600_000)

        # Olay-zamanı birleştirme: bütün sembollerin barları zaman damgasına göre tek akışta.
        stamps = sorted({c["ts"] for rows in candles.values() for c in rows})
        by_sym_ts = {sym: {c["ts"]: c for c in rows} for sym, rows in candles.items()}
        last_4h = None
        for ts in stamps:
            marks: dict[str, TickData] = {}
            close_dt = None
            for sym, table in by_sym_ts.items():
                c = table.get(ts)
                if c is None or sym not in self.ledger.positions:
                    continue
                marks[sym] = TickData(last=Decimal(str(c["close"])), mark=Decimal(str(c["close"])),
                                      high=Decimal(str(c["high"])), low=Decimal(str(c["low"])),
                                      ts=iso(datetime.fromtimestamp(c["close_time"] / 1000, tz=start.tzinfo)))
                close_dt = datetime.fromtimestamp(c["close_time"] / 1000, tz=start.tzinfo)
            if not marks or close_dt is None:
                continue
            bar4 = ts // _4H_MS
            advance = last_4h is not None and bar4 != last_4h
            last_4h = bar4
            recs = self.ledger.tick(marks, now_utc=close_dt, funding_rate_lookup=rate_lookup, bar_advance=advance)
            rep.bars_replayed += 1
            for r in recs:
                rep.closed.append(r)
                rep.decisions.append(f"{r.id} {r.symbol} {r.exit_reason} @bar {iso(close_dt)} (worst-case intrabar: liq>stop>TP)")
            if not self.ledger.positions:
                break

        self.ledger.save(self.ledger_path)
        write_watermark(self.state_dir, now, run_id)
        atomic_write_json(self.state_dir / GAP_STATUS_FILE,
                          {"status": "OK", "at": iso(now), "window": [iso(start), iso(now)], "tf": tf,
                           "bars_replayed": rep.bars_replayed, "closed": [r.id for r in rep.closed],
                           "decisions": rep.decisions})
        if rep.closed:
            log.info("gap-reconcile: %d bar / %d kapanış: %s", rep.bars_replayed, len(rep.closed), "; ".join(rep.decisions))
        else:
            log.info("gap-reconcile: %d bar, olay yok (pencere %s → %s, %s)", rep.bars_replayed, iso(start), iso(now), tf)
        return rep


__all__ = ["GapReconciler", "GapReport", "read_watermark", "write_watermark", "read_gap_status",
           "choose_timeframe", "WATERMARK_FILE", "GAP_STATUS_FILE", "MIN_GAP_S"]
