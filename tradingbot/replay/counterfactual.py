"""Karsi-olgusal aday replay'i — `entry_snapshot.jsonl`'deki HER adayin ileri sonucunu olcer.

Amac: "kabul edilen adaylar reddedilenlerden gercekten daha mi iyiydi?" sorusunu, kabul kararindan
BAGIMSIZ olarak cevaplayabilmek. Her aday icin karar aninda kayitli plan (giris/stop/hedefler)
gercek 1m mumlarla, uretimin KENDI muhasebesiyle (`FuturesLedgerV2`) yeniden kosulur.

Sinirlar — pazarlik konusu degil
--------------------------------
* ILERI BAKIS YOK. Ilk mum, adayin `ts`ini takip eden ILK 1m mumdur; giris mumunun oncesi gorulmez.
* BAR ICI SIRALAMA KOTUMSERDIR. Uretim defteri her mumda once likidasyon, sonra STOP, en son hedef
  kontrol eder (`futures_ledger.tick`). Ayni mumda hem stop hem hedef menzildeyse STOP kazanir.
  Bu `worst_case` politikasidir ve burada DEGISTIRILMEZ.
* IZOLE DEFTER. Her aday kendi defterinde kosar: marj rekabeti, pozisyon tavani ve siralama YOKTUR.
  Bu yuzden bu modul "aday basina ileri getiri" olcer; "portfoy sonucu" OLCMEZ. Portfoy sorusu icin
  `portfolio.py` kullanilir.
* OLGUNLUK. `ts + horizon` mevcut verinin sonunu asiyorsa aday OLGUNLASMAMISTIR ve birincil
  tahminden DISLANIR (`mature=False`), gizlenmez.
"""
from __future__ import annotations

import bisect
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Sequence

from ..accounting.models import AmountType, SizeSpec, TickData
from ..core import D, ZERO
from .fidelity import BinanceBars, Bar, ReplayConfig, exit_family

MIN_MS = 60_000
BAR_ADVANCE_MS = 14_400_000


# --------------------------------------------------------------------------- aday kaydi
@dataclass(frozen=True)
class Candidate:
    """`entry_snapshot_v1` satirindan KARAR ANI plani. Sonuc alani YOKTUR."""
    candidate_id: str
    ts_ms: int
    ts: str
    symbol: str
    direction: str
    setup: str
    regime: str
    p_win: float
    confidence: float
    consensus_score: float
    expected_r: float
    conservative_net_edge_r: float
    net_expectancy_r: float
    expected_cost_pct: float
    stop_distance_pct: float
    entry_price: float
    stop_price: float
    targets: tuple[float, ...]
    planned_notional: float
    planned_leverage: float
    accepted: bool
    reject_reason: str
    rank: int
    run_id: str
    cycle_id: str
    portfolio_open_positions: float
    sample_size: float
    n_dissent: float
    n_vetoes: float
    spec: dict
    raw: dict

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.ts_ms / 1000, tz=timezone.utc)


def iter_candidates(path: Path | str) -> Iterator[Candidate]:
    """`entry_snapshot.jsonl`'i SATIR SATIR okur (dosya 93 MB; asla tumu belege alinmaz).

    `kind` alani olan satirlar LINK kayitlaridir (aday degil) ve atlanir.
    """
    with io.open(str(path), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("ts_ms") is None or d.get("kind") is not None:
                continue
            tg = d.get("targets") or []
            yield Candidate(
                candidate_id=str(d.get("candidate_id")), ts_ms=int(d["ts_ms"]), ts=str(d.get("ts")),
                symbol=str(d.get("symbol")), direction=str(d.get("direction")), setup=str(d.get("setup") or ""),
                regime=str(d.get("regime") or ""), p_win=float(d.get("p_win") or 0.0),
                confidence=float(d.get("confidence") or 0.0), consensus_score=float(d.get("consensus_score") or 0.0),
                expected_r=float(d.get("expected_r") or 0.0),
                conservative_net_edge_r=float(d.get("conservative_net_edge_r") or 0.0),
                net_expectancy_r=float(d.get("net_expectancy_r") or 0.0),
                expected_cost_pct=float(d.get("expected_cost_pct") or 0.0),
                stop_distance_pct=float(d.get("stop_distance_pct") or 0.0),
                entry_price=float(d["entry_price"]), stop_price=float(d["stop_price"]),
                targets=tuple(float(t) for t in tg),
                planned_notional=float(d.get("planned_notional") or 0.0),
                planned_leverage=float(d.get("planned_leverage") or 1.0),
                accepted=bool(d.get("baseline_accepted")), reject_reason=str(d.get("baseline_reject_reason") or ""),
                rank=int(d.get("baseline_rank") or 0), run_id=str(d.get("run_id") or ""),
                cycle_id=str(d.get("cycle_id") or ""),
                portfolio_open_positions=float(d.get("portfolio_open_positions") or 0.0),
                sample_size=float(d.get("sample_size") or 0.0), n_dissent=float(d.get("n_dissent") or 0.0),
                n_vetoes=float(d.get("n_vetoes") or 0.0), spec=dict(d.get("specialist_scores") or {}),
                raw={k: d.get(k) for k in ("timeframe", "market_type", "link_status", "chief_allow", "risk_allowed",
                                           "size_multiplier", "avg_win_r", "avg_loss_r", "n_missing")},
            )


# --------------------------------------------------------------------------- mum ve funding onbellegi
class SymbolWindow:
    """Bir sembolun TUM pencere mumlarini BIR kez yukler, sonra dilimleri bellekten servis eder.

    `BinanceBars` parca dosyalarini disk onbelleginden okur; 2797 aday icin dosya ayristirmayi
    tekrarlamamak adina sonuc burada tutulur.
    """

    def __init__(self, source: BinanceBars, symbol: str, start_ms: int, end_ms: int, interval: str = "1m"):
        self.symbol = symbol
        self.interval = interval
        self.bars: list[Bar] = source.bars(symbol, interval, start_ms, end_ms)
        self._open_ms = [b.open_ms for b in self.bars]
        try:
            self.funding: list[tuple[int, Decimal]] = source.funding_rates(symbol, start_ms - 8 * 3_600_000, end_ms)
        except Exception:                                            # noqa: BLE001 — funding yoksa sonuc None doner
            self.funding = []
        self._f_ms = [t for t, _ in self.funding]

    def slice(self, start_ms: int, end_ms: int) -> Sequence[Bar]:
        i = bisect.bisect_left(self._open_ms, start_ms)
        j = bisect.bisect_right(self._open_ms, end_ms)
        return self.bars[i:j]

    def funding_lookup(self, tolerance_ms: int = 300_000):
        """Ikili arama tabanli `RateLookup`; settlement'a ±tolerans icinde oran yoksa None."""
        if not self.funding:
            return None
        ms, rates = self._f_ms, [r for _, r in self.funding]

        def _lookup(_symbol: str, when: datetime):
            target = int(when.timestamp() * 1000)
            i = bisect.bisect_left(ms, target)
            best, best_d = None, tolerance_ms + 1
            for k in (i - 1, i):
                if 0 <= k < len(ms):
                    d = abs(ms[k] - target)
                    if d <= tolerance_ms and d < best_d:
                        best, best_d = rates[k], d
            # Kayitlar venue gecmisinden: DOGRULANMIS -> defterin donemi kapatmasina izin verir.
            return None if best is None else {"rate": best, "source": "venue_history", "verified": True}

        return _lookup

    def settlement_source(self):
        """Venue'nun GERCEK settlement zamanlari (sabit 00/08/16 grid'i YOK)."""
        stamps = list(self._f_ms)

        def _source(_symbol: str, start: datetime, end: datetime) -> list[datetime]:
            lo, hi = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
            return [datetime.fromtimestamp(t / 1000, tz=timezone.utc) for t in stamps if lo < t <= hi]

        return _source


# --------------------------------------------------------------------------- tek aday replay'i
@dataclass
class CandidateOutcome:
    """Bir adayin izole ileri sonucu. `mature=False` ise R KULLANILMAZ."""
    candidate_id: str
    symbol: str
    ts_ms: int
    closed: bool
    mature: bool
    exit_reason: str | None = None
    r_multiple: float | None = None
    net_pnl: float | None = None
    risk_usdt: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    minutes_held: int = 0
    n_bars: int = 0
    ambiguous_bars: int = 0
    cost_r: float | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def replay_candidate(cand: Candidate, win: SymbolWindow, cfg: ReplayConfig, *, horizon_h: float,
                     data_end_ms: int, be_mfe_r: Decimal | None = None,
                     notional_override: float | None = None) -> CandidateOutcome:
    """Adayin planini gercek mumlarla kos. Ufukta kapanmadiysa son mum KAPANISINDAN piyasa emriyle kapat.

    `be_mfe_r` verilmezse defter anlik goruntusundeki deger kullanilir. Ufuk sonu kapanis gercek bir
    politika degil, OLCUM SINIRIDIR: her politikaya AYNI sekilde uygulanir ve `exit_reason='HORIZON'`
    ile isaretlenir.
    """
    horizon_ms = int(horizon_h * 3_600_000)
    start_ms = ((cand.ts_ms + MIN_MS - 1) // MIN_MS) * MIN_MS
    end_ms = cand.ts_ms + horizon_ms
    mature = end_ms <= data_end_ms
    bars = win.slice(start_ms, end_ms)
    out = CandidateOutcome(candidate_id=cand.candidate_id, symbol=cand.symbol, ts_ms=cand.ts_ms,
                           closed=False, mature=mature, n_bars=len(bars))
    if not bars:
        out.note = "NO_BARS"
        return out
    if not cand.targets or cand.stop_price <= 0 or cand.entry_price <= 0:
        out.note = "NO_PLAN"
        return out

    led = cfg.new_ledger(breakeven_at_mfe_r=be_mfe_r)
    notional = D(str(notional_override if notional_override is not None else cand.planned_notional))
    size = SizeSpec(amount=notional, amount_type=AmountType("NOTIONAL"), leverage=int(cand.planned_leverage or 1))
    pos = led.open(cand.symbol, cand.direction, D(str(cand.entry_price)), size,
                   stop=D(str(cand.stop_price)), targets=[D(str(t)) for t in cand.targets],
                   now=cand.dt, setup_type=cand.setup)
    if pos is None:
        out.note = f"OPEN_REJECTED:{led.last_reject_reason}"
        return out
    risk = abs(pos.entry_avg - D(str(cand.stop_price))) * pos.qty
    out.risk_usdt = float(risk) if risk > 0 else None
    lookup = win.funding_lookup()
    if lookup is not None:
        led.funding.settlement_source = win.settlement_source()

    prev_slot = cand.ts_ms // BAR_ADVANCE_MS
    rec = None
    for b in bars:
        slot = b.open_ms // BAR_ADVANCE_MS
        advance = slot != prev_slot
        prev_slot = slot
        stop_now, tgt_now = pos.stop, (pos.targets[pos.targets_hit] if pos.targets_hit < len(pos.targets) else None)
        if stop_now is not None and tgt_now is not None and b.low <= stop_now <= b.high and b.low <= tgt_now <= b.high:
            out.ambiguous_bars += 1
        td = TickData(last=b.close, mark=b.close, high=b.high, low=b.low, ts=b.close_dt.isoformat(),
                      bar_open=b.open_dt.isoformat())
        recs = led.tick({cand.symbol: td}, now_utc=b.close_dt, funding_rate_lookup=lookup, bar_advance=advance)
        if recs:
            rec = recs[-1]
            out.closed = True
            out.minutes_held = (b.open_ms - start_ms) // MIN_MS + 1
            break
    if rec is None:
        last = bars[-1]
        rec = led.close_manual(cand.symbol, last.close, reason="HORIZON", now=last.close_dt)
        out.minutes_held = (last.open_ms - start_ms) // MIN_MS + 1
        if rec is None:
            out.note = "NO_CLOSE"
            return out
        out.note = "HORIZON_CLOSE"
    out.exit_reason = "HORIZON" if out.note == "HORIZON_CLOSE" else exit_family(rec.exit_reason)
    out.r_multiple = float(rec.r_multiple)
    out.net_pnl = float(rec.net_pnl)
    ist = D(str(cand.stop_price))
    risk_pct = abs(pos.entry_avg - ist) / pos.entry_avg * 100 if pos.entry_avg > 0 else ZERO
    if risk_pct > 0:
        out.mfe_r = float(D(rec.mfe_pct) / risk_pct)
        out.mae_r = float(D(rec.mae_pct) / risk_pct)
    fees = D(rec.entry_fee) + D(rec.exit_fee) - D(rec.funding)
    out.cost_r = float(fees / risk) if risk > 0 else None
    return out


# --------------------------------------------------------------------------- toplu kosum
def run_all(candidates: Sequence[Candidate], source: BinanceBars, cfg: ReplayConfig, *, horizon_h: float,
            data_end_ms: int, window_start_ms: int, be_mfe_r: Decimal | None = None,
            progress: Any = None) -> list[dict[str, Any]]:
    """Butun adaylari SEMBOL SEMBOL kos (mumlar sembol basina bir kez yuklenir, sonra birakilir)."""
    by_sym: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_sym.setdefault(c.symbol, []).append(c)
    rows: list[dict[str, Any]] = []
    for i, (sym, group) in enumerate(sorted(by_sym.items())):
        end = min(data_end_ms, max(c.ts_ms for c in group) + int(horizon_h * 3_600_000)) + MIN_MS
        try:
            win = SymbolWindow(source, sym, window_start_ms, end)
        except Exception as exc:                                     # noqa: BLE001 — sembol yoksa isaretlenir
            for c in group:
                rows.append({**_meta(c), "closed": False, "mature": False, "note": f"NO_SYMBOL:{exc}"})
            continue
        for c in group:
            o = replay_candidate(c, win, cfg, horizon_h=horizon_h, data_end_ms=data_end_ms, be_mfe_r=be_mfe_r)
            rows.append({**_meta(c), **o.as_dict()})
        if progress:
            progress(i + 1, len(by_sym), sym, len(group), len(win.bars))
        del win
    rows.sort(key=lambda r: r["ts_ms"])
    return rows


def _meta(c: Candidate) -> dict[str, Any]:
    """Karar ani alanlari — sonuc ASLA buraya sizmaz."""
    return {
        "candidate_id": c.candidate_id, "ts_ms": c.ts_ms, "ts": c.ts, "symbol": c.symbol,
        "direction": c.direction, "setup": c.setup, "regime": c.regime, "p_win": c.p_win,
        "confidence": c.confidence, "consensus_score": c.consensus_score, "expected_r": c.expected_r,
        "conservative_net_edge_r": c.conservative_net_edge_r, "net_expectancy_r": c.net_expectancy_r,
        "expected_cost_pct": c.expected_cost_pct, "stop_distance_pct": c.stop_distance_pct,
        "planned_notional": c.planned_notional, "planned_leverage": c.planned_leverage,
        "accepted": c.accepted, "reject_reason": c.reject_reason, "rank": c.rank, "run_id": c.run_id,
        "cycle_id": c.cycle_id, "sample_size": c.sample_size, "n_dissent": c.n_dissent, "n_vetoes": c.n_vetoes,
        "market_type": c.raw.get("market_type"), "spec_mtf": c.spec.get("multi_timeframe"),
        "spec_trend": c.spec.get("trend"), "spec_momentum": c.spec.get("momentum"),
        "spec_volume": c.spec.get("volume"), "spec_levels": c.spec.get("levels"),
        "spec_derivatives": c.spec.get("derivatives"), "spec_liquidity": c.spec.get("market:liquidity"),
    }


# --------------------------------------------------------------------------- kumeleme
def assign_episodes(rows: Sequence[dict[str, Any]], block_h: float = 72.0) -> None:
    """Bagimlilik etiketleri yazar (yerinde). 2797 satir 2797 BAGIMSIZ islem DEGILDIR.

    Uc seviye birden yazilir, cunku hicbiri tek basina yeterli degil:

    * ``episode``  — `(sembol, yon, floor(ts / block_h))`. `block_h` = degerlendirme ufku oldugunda
      ayni bloktaki adaylarin sonuc pencereleri buyuk olcude ORTUSUR; tek gozlem sayilirlar.
      Zincirleme YOK: sabit takvim bloklari kullanilir, yoksa bir haftalik akis tek bolume cokerdi.
    * ``cluster``  — sembol. Ayni sembolun butun adaylari tek kume; kume-saglam hata payi icin.
    * ``day``      — UTC takvim gunu. Ayni gun butun sembollerde ortak piyasa hareketi vardir;
      gun bloklu bootstrap bunu yakalar (7 gunluk pencerede yalnizca ~7 blok — zayif, ama gizlenmez).
    """
    block_ms = int(block_h * 3_600_000)
    for r in rows:
        r["episode"] = f"{r['symbol']}|{r['direction']}|{r['ts_ms'] // block_ms}"
        r["cluster"] = r["symbol"]
        r["day"] = datetime.fromtimestamp(r["ts_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


__all__ = ["Candidate", "CandidateOutcome", "SymbolWindow", "assign_episodes",
           "iter_candidates", "replay_candidate", "run_all"]
