# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2 testleri için yardımcı (ağ YOK): sentetik piyasa (araştırma deposuna yazılır ve
mühürlenir), canlı motorun çerçeve okumasını taklit eden "işçi benzetimi" (kararı `paper_rules.decide_for`, açılışı
`strategy_paper._entry_features` + `FuturesLedgerV2.open`, yolu bar içi sıra açılış → ters → lehte → kapanış ile defterin
kendi `tick`i) ve bunları P1a sahte VPS'ine (`FakeVps`) bağlayan küçük araçlar. Test dosyası DEĞİLDİR.

Sentetik piyasa: her sembol için uzun bir geçmiş (1d/4h/1h; sabit tohumlu rastgele yürüyüş) ve son günler için TEK bir
1m yürüyüşünden türetilen 5m/1h/4h/1d barları (bütün dilimler son dönemde birbirine tutarlı). Fiyatlar 2 ondalık (tick
0,01). Depo `ResearchStore.write` ile (kaynak `archive`) yazılır; mühür veri biriminin biçimiyle
(`store/_seal/<mühür>.json.gz` + `summary/data_status.json`) kurulur, gece okuyucusu `SealedReader.from_status`'tur.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pandas as pd

from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec, SlippageModel, TickData
from tradingbot.research_engine.ledgers import iso
from tradingbot.research_engine.paths import EnginePaths
from tradingbot.research_engine.store import ResearchStore

UTC = timezone.utc
M1, M5, H1, H4, D1 = 60_000, 300_000, 3_600_000, 14_400_000, 86_400_000
STEP = {"1m": M1, "5m": M5, "1h": H1, "4h": H4, "1d": D1}
LIVE_LIMITS = {"1d": 400, "4h": 700, "1h": 500, "5m": 500}
KCOLS = ["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_base",
         "taker_buy_quote"]


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def dt_of(t: int) -> datetime:
    return datetime.fromtimestamp(t / 1000, tz=UTC)


# ============================================================================ sentetik barlar
def _bar(o: float, c: float, rng: random.Random, vol: float) -> tuple[float, float, float, float]:
    h = max(o, c) * (1 + abs(rng.gauss(0, vol)) * 0.6)
    lo = min(o, c) * (1 - abs(rng.gauss(0, vol)) * 0.6)
    o, c, h, lo = round(o, 2), round(c, 2), round(h, 2), round(lo, 2)
    h = max(h, o, c)
    lo = min(lo, o, c)
    return o, h, lo, c


def walk(seed: str, start_ms: int, n: int, step: int, p0: float, *, vol: float, drift: float = 0.0) -> pd.DataFrame:
    """Sabit tohumlu rastgele yürüyüş (açılış = önceki kapanış). Hacim ve taker alanları da deterministik."""
    rng = random.Random(seed)
    rows = []
    p = float(p0)
    for i in range(n):
        o = p
        c = max(0.05, o * (1 + drift + rng.gauss(0, vol)))
        o, h, lo, c = _bar(o, c, rng, vol)
        v = round(100 + 50 * rng.random(), 3)
        t = start_ms + i * step
        rows.append([t, o, h, lo, c, v, t + step - 1, round(v * c, 4), 10 + i % 7, round(v * 0.5, 3), round(v * 0.5 * c, 4)])
        p = c
    return pd.DataFrame(rows, columns=KCOLS)


def aggregate(df: pd.DataFrame, step: int) -> pd.DataFrame:
    """Alt dilimden üst dilim (açılış = ilk açılış, uçlar max/min, kapanış = son kapanış, hacimler toplam)."""
    g = df.assign(_b=(df["timestamp"] // step) * step).groupby("_b", sort=True)
    out = pd.DataFrame({"timestamp": g["timestamp"].min().index.astype("int64"), "open": g["open"].first().values,
                        "high": g["high"].max().values, "low": g["low"].min().values, "close": g["close"].last().values,
                        "volume": g["volume"].sum().values})
    out["close_time"] = out["timestamp"] + step - 1
    out["quote_volume"] = (out["volume"] * out["close"]).round(4)
    out["trades"] = 10
    out["taker_buy_base"] = (out["volume"] / 2).round(3)
    out["taker_buy_quote"] = (out["taker_buy_base"] * out["close"]).round(4)
    return out[KCOLS].reset_index(drop=True)


def market(seed: str, *, recent_from: datetime, recent_to: datetime, p0: float, hist_drift: float = 0.002,
           vol_1m: float = 0.0012, drift_1m: float = 0.0) -> dict[str, pd.DataFrame]:
    """Bir sembolün dilimleri: `recent_from`'dan önce bağımsız geçmiş (1d 720, 4h 760, 1h 520 bar), sonrası tek 1m
    yürüyüşünden türetilmiş 5m/1h/4h/1d. Geçmişin son kapanışı 1m yürüyüşünün başlangıcıdır (seviye sürekli)."""
    a, b = ms(recent_from), ms(recent_to)
    hist = {}
    for tf, n, vol, dr in (("1d", 720, 0.025, hist_drift), ("4h", 760, 0.01, hist_drift / 6), ("1h", 520, 0.005, hist_drift / 24)):
        st = STEP[tf]
        hist[tf] = walk(f"{seed}:{tf}", a - n * st, n, st, p0, vol=vol, drift=dr)
    p1 = float(hist["1d"]["close"].iloc[-1])
    # 4h/1h geçmişini günlük seviyeye ölçekle (son kapanış = p1)
    for tf in ("4h", "1h"):
        k = p1 / float(hist[tf]["close"].iloc[-1])
        for c in ("open", "high", "low", "close"):
            hist[tf][c] = (hist[tf][c] * k).round(2)
    m1 = walk(f"{seed}:1m", a, (b - a) // M1, M1, p1, vol=vol_1m, drift=drift_1m)
    out = {"1m": m1, "5m": aggregate(m1, M5)}
    for tf in ("1h", "4h", "1d"):
        out[tf] = pd.concat([hist[tf], aggregate(m1, STEP[tf])], ignore_index=True)
    return out


def from_rows(rows: list[dict], step: int) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["close_time"] = df["timestamp"] + step - 1
    df["quote_volume"] = (df["volume"] * df["close"]).round(4)
    df["trades"] = 10
    df["taker_buy_base"] = (df["volume"] / 2).round(3)
    df["taker_buy_quote"] = (df["taker_buy_base"] * df["close"]).round(4)
    return df[KCOLS]


# ============================================================================ depo ve mühür
class StoreFixture:
    def __init__(self, paths: EnginePaths, *, now: datetime):
        self.paths = paths
        self.now_ms = ms(now)
        self.store = ResearchStore.for_paths(paths, clock_ms=lambda: self.now_ms)

    def add(self, market_: str, sym: str, tf: str, df: pd.DataFrame) -> None:
        self.store.write(market_, sym, tf, df, src="archive", now_ms=self.now_ms)

    def add_all(self, sym: str, frames: dict[str, pd.DataFrame], market_: str = "futures", tfs: tuple[str, ...] | None = None) -> None:
        for tf, df in frames.items():
            if tfs is None or tf in tfs:
                self.add(market_, sym, tf, df)

    def seal(self) -> str:
        seal, listing = self.store.data_seal()
        rel = f"store/_seal/{seal}.json.gz"
        p = self.paths.research / rel
        if not p.exists():
            self.paths.write_json_gz(p, {"schema": "engine_data_seal_v1", "data_seal": seal, "parts": [list(x) for x in listing]})
        self.paths.write_json(self.paths.data_status, {"schema": "engine_data_status_v1", "data_seal": seal, "seal_file": rel,
                                                       "sealed_at": iso(dt_of(self.now_ms))})
        return seal

    def source(self):
        from tradingbot.research_engine.journal import open_store
        src, info = open_store(self.paths)
        assert src is not None, info
        return src


# ============================================================================ işçi benzetimi (canlı çerçeve + karar)
def live_frames(series: dict[str, pd.DataFrame], now_ms: int, tfs: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """`runner._frames`: açılışı ≤ now olan son `limit` satır (oluşan bar dahil) → `prepare` → `drop_unclosed_last_bar`
    → `add_snapshot_indicators`."""
    from tradingbot import indicators as ind
    from tradingbot.data import drop_unclosed_last_bar, prepare
    out = {}
    for tf in tfs:
        df = series[tf]
        df = df[df["timestamp"] <= now_ms].tail(LIVE_LIMITS[tf])[["timestamp", "open", "high", "low", "close", "volume"]]
        df = drop_unclosed_last_bar(prepare(df.reset_index(drop=True)), tf, now_ms=now_ms)
        out[tf] = ind.add_snapshot_indicators(df)
    return out


def worker_decide(name: str, series: dict[str, pd.DataFrame], now_ms: int, params: Any,
                  btc: dict[str, pd.DataFrame] | None = None) -> tuple[dict | None, dict, dict]:
    """Canlı defter yolu: çerçeveler → `paper_rules.decide_for` (yapı katmanı KAPALI) → (eylem, kullanılan bar, BTC barı)."""
    from tradingbot import paper_rules
    from tradingbot.candle_confirmation import closed_bars
    from tradingbot.ema200_trend import daily_rows_from_frame
    from tradingbot.strategy_paper import frame_freshness
    tfs = paper_rules.rule_timeframes(name)
    fr = live_frames(series, now_ms, tfs)
    used = {tf: frame_freshness(fr[tf], tf, now_ms, tolerance_ms=10**12)[1] for tf in tfs}
    btc_rows, btc_used = None, {}
    if btc is not None and paper_rules.needs_btc(name):
        bfr = live_frames(btc, now_ms, ("1d",))
        btc_rows = closed_bars(daily_rows_from_frame(bfr["1d"]), now_ms=now_ms, tf="1d")
        btc_used = {"1d": frame_freshness(bfr["1d"], "1d", now_ms, tolerance_ms=10**12)[1]}
    act = paper_rules.decide_for(name, frames=fr, btc_rows=btc_rows, now_ms=now_ms, position=None, params=params)
    return (act if act and str(act.get("action")).upper() == "OPEN" else None), used, btc_used


def price_at(m1: pd.DataFrame, t_ms: int) -> float:
    """`t` anındaki "canlı mark": `t`'yi içeren 1m barının açılışı."""
    row = m1[m1["timestamp"] == (t_ms // M1) * M1]
    return float(row["open"].iloc[0])


def open_trade(led: FuturesLedgerV2, sym: str, act: dict, *, now: datetime, mark: float, used: dict, btc_used: dict,
               notional: str = "200", lev: int = 3):
    """`strategy_paper.apply_action`'ın açılış adımları (taban yol): hedef girişten (C4), özellikler `_entry_features`."""
    from tradingbot.strategy_paper import DataVerdict, _entry_features
    direction = str(act.get("direction") or "LONG").upper()
    entry, stop = float(mark), float(act["stop"])
    tr = act.get("target_r_from_entry")
    if tr:
        s = 1.0 if direction == "LONG" else -1.0
        act["targets"] = [entry + s * float(tr) * abs(entry - stop)]
    btc = {"required": bool(btc_used), "ok": True, "market": "USDM_PERP", "reason": "", "bars": dict(btc_used), "detail": {}} if btc_used else {"required": False}
    data = DataVerdict(ok=True, entry_ok=True, market="USDM_PERP", source="sentetik", tour_id="t", bars=dict(used), btc=btc,
                       as_of_ms=ms(now))
    feats, data_src = _entry_features(act, data)
    pos = led.open(sym, direction, D(str(entry)), SizeSpec(D(notional), AmountType.NOTIONAL, lev), stop=stop,
                   targets=list(act.get("targets") or []), setup_type=str(act.get("setup_type") or "strategy"),
                   trigger_text=str(act.get("reason") or ""), features=feats, now=now,
                   meta={"run_id": "t", "strategy": str(act.get("name") or ""), "data_source": data_src})
    assert pos is not None, led.last_reject_reason
    return pos


def walk_ledger(led: FuturesLedgerV2, sym: str, m1: pd.DataFrame, *, opened_ms: int, until_ms: int,
                clip_stop: bool = True, funding: Any = None, gap_at_ms: int | None = None):
    """Pozisyonu 1m yolu boyunca defterin kendi `tick`iyle yürüt (açılış → ters → lehte → kapanış). `clip_stop`: koruyucu
    izleyici stopu seviyesinde yakalar (canlı 60 sn izleyici); `gap_at_ms` barında kırpma YOK (boşluk dolumu). Kapanan
    kaydı döndürür; `until_ms`'e kadar kapanmazsa None."""
    from tradingbot.learning_cf import _bar_path
    path = m1[(m1["timestamp"] > opened_ms) & (m1["timestamp"] <= until_ms)]
    for t, o, h, lo, c in zip(path["timestamp"], path["open"], path["high"], path["low"], path["close"]):
        pos = led.positions.get(sym)
        if pos is None:
            return None
        long = pos.side.value == "LONG"
        for i, (pt, px) in enumerate(_bar_path(long, float(o), float(h), float(lo), float(c))):
            pos = led.positions.get(sym)
            if pos is None:
                break
            at = dt_of(int(t)) if pt == "open" else dt_of(int(t) + M1)
            price = D(str(px))
            if clip_stop and int(t) != gap_at_ms and pos.stop is not None:
                beyond = price <= pos.stop if long else price >= pos.stop
                if beyond and pt != "open":
                    price = pos.stop
            recs = led.tick({sym: TickData(last=price, mark=price, ts=iso(at))}, now_utc=at, funding_rate_lookup=funding,
                            bar_advance=(i == 0))
            if recs:
                return recs[-1]
    return None


def walk_sampled(led: FuturesLedgerV2, sym: str, m1: pd.DataFrame, *, opened_ms: int, until_ms: int,
                 bar_ms: int | None = None, funding: Any = None):
    """CANLI izleme benzetimi (2026-10-08, P2 incelemesi B1): koruyucu izleyicinin 60 sn fiyat ÖRNEKLERİ — her 1m barının
    kapanışı tek fiyat-yalnız tick (`first_source = PRICE`): stop, seviyenin ÖTESİNDEKİ ilk örnekten dolar
    (`GAP_FILL_AT_FIRST_OBSERVATION`, gerçek defterin `exit_decision` kuralı 2) — ve isteğe bağlı kural diliminin bar tiki
    (`bar_ms`, ör. Box 5m: açılış/uçlar/kapanış; örneklerin kaçırdığı fitil stopu seviyeden yakalanır). Seviyeye kırpma YOK
    (`walk_ledger(clip_stop=True)` gerçekçi değildir: canlı dolum seviyeden değil örnekten olur)."""
    path = m1[(m1["timestamp"] > opened_ms) & (m1["timestamp"] <= until_ms)]
    for t, c in zip(path["timestamp"], path["close"]):
        if led.positions.get(sym) is None:
            return None
        at = dt_of(int(t) + M1)
        recs = led.tick({sym: TickData(last=D(str(c)), mark=D(str(c)), ts=iso(at))}, now_utc=at, funding_rate_lookup=funding,
                        bar_advance=True)
        if recs:
            return recs[-1]
        end = int(t) + M1
        if bar_ms and end % bar_ms == 0 and end - bar_ms > opened_ms:
            seg = m1[(m1["timestamp"] >= end - bar_ms) & (m1["timestamp"] < end)]
            last = D(str(float(seg["close"].iloc[-1])))
            td = TickData(last=last, mark=last, high=D(str(float(seg["high"].max()))), low=D(str(float(seg["low"].min()))),
                          open=D(str(float(seg["open"].iloc[0]))), ts=iso(at))
            recs = led.tick({sym: td}, now_utc=at, funding_rate_lookup=funding)
            if recs:
                return recs[-1]
    return None


def close_at(led: FuturesLedgerV2, sym: str, m1: pd.DataFrame, at_ms: int, reason: str = "STRATEGY_EXIT"):
    """Kural çıkışı: `at`'de biten 1m barının kapanışından `close_manual`."""
    row = m1[m1["timestamp"] == at_ms - M1]
    return led.close_manual(sym, D(str(float(row["close"].iloc[0]))), reason=reason, now=dt_of(at_ms))


def run_trade(led: FuturesLedgerV2, sym: str, m1: pd.DataFrame, *, opened_ms: int, hold_ms: int, sampled: bool = False,
              **kw):
    """Yolu `hold_ms` boyunca yürüt; stop/hedef gelmezse kural çıkışı (dakika sınırında `close_manual`). `sampled`: canlı
    60 sn örnek izleyicisi (`walk_sampled`; kural dilimi `bar_ms` kw'si ile)."""
    until = (opened_ms // M1) * M1 + hold_ms
    walk = walk_sampled if sampled else walk_ledger
    rec = walk(led, sym, m1, opened_ms=opened_ms, until_ms=until - M1, **kw)
    return rec if rec is not None else close_at(led, sym, m1, until)


def new_ledger(equity: str = "10000") -> FuturesLedgerV2:
    return FuturesLedgerV2(D(equity), slippage=SlippageModel(fixed_bps=D("3")), max_positions=20, enforce_position_cap=False)


class SettlementRates:
    """Gerçekleşmiş oran + settlement mark'ı (1m barının açılışı) — canlı `FundingRates` benzeri, ağsız."""

    def __init__(self, rate: str, m1_by_sym: dict[str, pd.DataFrame]):
        self.rate = D(rate)
        self.m1 = m1_by_sym

    def __call__(self, symbol: str, when: datetime):
        return self.rate

    def settlement_mark(self, symbol: str, when: datetime):
        df = self.m1.get(symbol)
        if df is None:
            return None
        row = df[df["timestamp"] == ms(when)]
        return D(str(float(row["open"].iloc[0]))) if len(row) else None


def write_memory(state: Path, book: str, pos: Any, act: dict) -> None:
    """`StrategyBook._on_opened`'ın işlem hafızası giriş satırı (ölçülmüş `signal_close` vb.)."""
    p = state / book / "trade_memory.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"kind": "entry", "trade_id": pos.id, "recorded_at": pos.opened_at, "source": "STRATEGY_PAPER", "symbol": pos.symbol,
           "direction": pos.side.value, "market_type": "USDM_PERP", "setup_type": "trend",
           "features": {"strategy": act.get("name"), "signal_close": act.get("signal_close"), "ema200": act.get("ema200"),
                        "atr14": act.get("atr14"), "signal_ts": act.get("signal_ts"), "stop_at_entry": act.get("stop")}}
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def config_text() -> str:
    """Depodaki `config.yaml`'ın strateji defteri bölümü (rehydrate parametreleri ham YAML'dan okunur)."""
    root = Path(__file__).resolve().parents[1]
    return (root / "config.yaml").read_text(encoding="utf-8")


# ============================================================================ hazır dünya (strateji + ana bot + spot)
RECENT_FROM, RECENT_TO = utc(2026, 9, 25), utc(2026, 10, 6)
SEAL_AT = utc(2026, 10, 6, 1)
#: dünya sembolleri: ETH (T2 yükselen), BTC (T2/M2 BTC kapısı + situation), SOL (Box yatay), ADA (M2/D4/C4)
_MARKETS: dict[str, dict[str, pd.DataFrame]] = {}


def world_markets() -> dict[str, dict[str, pd.DataFrame]]:
    """Deterministik dünya piyasası (önbellekli; çağıran DEĞİŞTİRMEZ — bozmak için `.copy()`)."""
    if not _MARKETS:
        kw = {"recent_from": RECENT_FROM, "recent_to": RECENT_TO}
        _MARKETS["ETHUSDT"] = market("ETH", p0=2000, hist_drift=0.003, vol_1m=0.0012, drift_1m=0.00002, **kw)
        _MARKETS["BTCUSDT"] = market("BTC", p0=60000, hist_drift=0.003, vol_1m=0.001, drift_1m=0.00002, **kw)
        _MARKETS["SOLUSDT"] = market("SOL", p0=150, hist_drift=0.0, vol_1m=0.0025, **kw)
        _MARKETS["ADAUSDT"] = market("ADA", p0=20, hist_drift=0.003, vol_1m=0.0015, drift_1m=0.00003, **kw)
    return _MARKETS


def rule_params(rc: Any, name: str, **override: Any) -> Any:
    """Canlı defterin parametreleri (ham config: `atr_mult` + `rule_params`), isteğe bağlı üzerine yazma."""
    from tradingbot import paper_rules
    b = rc.books[name]
    rp = {**(b.get("rule_params") or {}), **override}
    kw: dict[str, Any] = {"rule_params": rp}
    if b.get("atr_mult") is not None:
        kw["atr_mult"] = b["atr_mult"]
    return paper_rules.build_params(name, **kw)


class World:
    """P1a sahte VPS'i + sentetik piyasa + gerçek muhasebe sınıflarıyla işçi benzetimi; `seal()` depoyu kurar."""

    BOOK_SYMBOL = {"strategy_paper": "ETH/USDT", "strategy_paper_m2": "ADA/USDT", "strategy_paper_box": "SOL/USDT",
                   "strategy_paper_trend4h": "ADA/USDT", "strategy_paper_candle4h": "ADA/USDT"}

    def __init__(self, root: Path):
        from research_engine_fixtures import FakeVps

        from tradingbot.research_engine.rawconfig import read_raw_config
        self.root = Path(root)
        self.v = FakeVps(self.root)
        self.app = self.root / "app"
        self.app.mkdir(parents=True, exist_ok=True)
        (self.app / "config.yaml").write_text(config_text(), encoding="utf-8")
        self.rc = read_raw_config(self.app / "config.yaml")
        self.mk = world_markets()
        self.records: dict[str, list] = {}
        #: defter → açılan her işlemin canlı kural eylemi (hedefler, stop, sinyal bağlamı) — rehydrate testinin "gerçeği"
        self.meta: dict[str, list[dict]] = {}
        self.sf: StoreFixture | None = None

    @property
    def paths(self) -> EnginePaths:
        return self.v.paths

    def m1(self, sym: str) -> pd.DataFrame:
        return self.mk[sym.replace("/", "")]["1m"]

    # ------------------------------------------------------------------ strateji defterleri
    def strategy(self, book: str, name: str, times: list[int], *, hold_ms: int, max_trades: int, funding: Any = None,
                 **override: Any) -> list:
        """`times` anlarında (tur `now`'ı) kuralı çalıştır; açılırsa işlem hafızası satırı yaz ve yolu yürüt."""
        sym = self.BOOK_SYMBOL[book]
        led = self.v.futs.get(book) or new_ledger()
        self.v.futs[book] = led
        params = rule_params(self.rc, name, **override)
        out = self.records.setdefault(book, [])
        busy_until = 0
        n = 0
        for t in times:
            if n >= max_trades:
                break
            if t < busy_until:
                continue
            act, used, bu = worker_decide(name, self.mk[sym.replace("/", "")], t, params, btc=self.mk["BTCUSDT"])
            if not act:
                continue
            pos = open_trade(led, sym, act, now=dt_of(t), mark=price_at(self.m1(sym), t), used=used, btc_used=bu)
            write_memory(self.v.state, book, pos, act)
            self.meta.setdefault(book, []).append({"id": pos.id, "opened_at": pos.opened_at, "act": json.loads(json.dumps(act, default=str))})
            rec = run_trade(led, sym, self.m1(sym), opened_ms=t, hold_ms=hold_ms, funding=funding)
            out.append(rec)
            busy_until = (t // M1) * M1 + hold_ms + M1
            n += 1
        return out

    def all_strategies(self) -> None:
        """T2 (ETH, günlük), M2 (ADA, günlük), Box (SOL, üç config döneminde), D4 ve C4 (ADA, 4h)."""
        day0 = utc(2026, 9, 25)
        fund_eth = SettlementRates("0.0001", {"ETH/USDT": self.m1("ETH/USDT")})
        self.strategy("strategy_paper", "t2_trend_regime", [ms(day0 + timedelta(days=k, minutes=20, seconds=13)) for k in range(1, 11)],
                      hold_ms=10 * H1, max_trades=10, funding=fund_eth)
        self.strategy("strategy_paper_m2", "m2_tsmom28", [ms(day0 + timedelta(days=k, minutes=20, seconds=47)) for k in range(1, 11)],
                      hold_ms=9 * H1, max_trades=10)
        four = [ms(day0 + timedelta(hours=4 * k, minutes=7, seconds=13)) for k in range(1, 64)]
        self.strategy("strategy_paper_trend4h", "d4_donchian_20_10", four, hold_ms=8 * H1, max_trades=6)
        self.strategy("strategy_paper_candle4h", "c4_candle_variations", four, hold_ms=12 * H1, max_trades=6)
        for start in (utc(2026, 9, 27, 12), utc(2026, 10, 1, 1), utc(2026, 10, 4, 1)):
            a = ms(start)
            self.strategy("strategy_paper_box", "b1_box_fade", [a + k * M5 + 2 * M1 + 7_000 for k in range(0, 288)],
                          hold_ms=4 * H1, max_trades=2, min_stop_pct=0.3)

    # ------------------------------------------------------------------ ana bot (vadeli + spot)
    def provenance(self, trade_id: str, sym: str, direction: str, *, stop: float, targets: list[float], opened_at: str) -> None:
        from tradingbot.learn.provenance import build_entry_provenance
        row = build_entry_provenance(trade_id=trade_id, symbol=sym, direction=direction, decision_id=f"dec-{trade_id}",
                                     code_sha="c" * 40, config_hash="f" * 16, policy_id="pol-1", p_win=0.55, expected_r=0.4,
                                     specialist_scores={"trend": 0.6}, regime="TREND_UP", stop=stop, targets=targets,
                                     size_usdt=200, leverage=3, opened_at=opened_at)
        row["recorded_at"] = opened_at
        with open(self.v.state / "entry_provenance.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def main_trade(self, sym: str, opened: datetime, *, side: str = "LONG", stop_pct: float = 0.012, target_r: float = 1.5,
                   hold_ms: int = 3 * H1, funding: Any = None, provenance: bool = True):
        led = self.v.futs.get("") or new_ledger()
        self.v.futs[""] = led
        t = ms(opened)
        e = price_at(self.m1(sym), t)
        sg = 1.0 if side == "LONG" else -1.0
        stop = round(e * (1 - sg * stop_pct), 2)
        tgt = [round(e + sg * target_r * abs(e - stop), 2)]
        pos = led.open(sym, side, D(str(e)), SizeSpec(D("300"), AmountType.NOTIONAL, 3), stop=stop, targets=tgt,
                       setup_type="ensemble_long" if side == "LONG" else "ensemble_short", now=opened)
        assert pos is not None, led.last_reject_reason
        if provenance:
            self.provenance(pos.id, sym, side, stop=stop, targets=tgt, opened_at=pos.opened_at)
        rec = run_trade(led, sym, self.m1(sym), opened_ms=t, hold_ms=hold_ms, funding=funding)
        self.records.setdefault("main_fut", []).append(rec)
        return rec

    def spot_round(self, sym: str, bought: datetime, sells: list[tuple[datetime, str]], *, qty: str = "0.5",
                   stop_pct: float = 0.03):
        """Spot alış + kısmi satışlar; alış emrinin provenance'ı (giriş stopu) — ana botun spot yolu."""
        sp = self.v.spot(cash="5000")
        px = price_at(self.m1(sym), ms(bought))
        o = sp.market_buy(sym, qty=D(qty), ref_price=D(str(px)), now=bought)
        assert o.status.value == "FILLED", o
        self.provenance(o.id, sym, "LONG", stop=round(px * (1 - stop_pct), 2), targets=[round(px * 1.05, 2)], opened_at=iso(bought))
        for at, q in sells:
            spx = price_at(self.m1(sym), ms(at))
            so = sp.market_sell(sym, qty=D(q), ref_price=D(str(spx)), now=at)
            assert so.status.value == "FILLED", so
        return o

    def standard(self) -> None:
        """Standart dünya: bütün strateji defterleri + ana bot vadeli (biri 08:00 fonlaması BEKLEYEN) + spot (iki kısmi
        satış); 2026-10-06 gecesi (S1a + S3) ve mühürlü depo."""
        from research_engine_fixtures import night_of
        self.all_strategies()
        self.main_trade("SOL/USDT", utc(2026, 9, 29, 3, 11, 5))
        self.main_trade("ETH/USDT", utc(2026, 9, 30, 7, 12, 9), hold_ms=2 * H1)        # 08:00 uzlaşması oransız
        self.spot_round("ETH/USDT", utc(2026, 9, 30, 10, 0, 31), [(utc(2026, 10, 1, 9, 0, 3), "0.04"),
                                                                 (utc(2026, 10, 2, 15, 30, 9), "0.02")], qty="0.1")
        self.seal()
        self.v.night(night_of("2026-10-06"))
        (self.root / "world_meta.json").write_text(json.dumps(self.meta, sort_keys=True), encoding="utf-8")

    @classmethod
    def attach(cls, root: Path) -> "World":
        """Diske yazılmış bir dünyaya bağlan (ledger'lar JSON'dan yeniden yüklenir; depo dosyaları yerinde)."""
        w = cls(root)
        mp = Path(root) / "world_meta.json"
        if mp.exists():
            w.meta = json.loads(mp.read_text(encoding="utf-8"))
        for p in sorted(w.v.state.rglob("futures_ledger.json")):
            book = "" if p.parent == w.v.state else p.parent.name
            w.v.futs[book] = FuturesLedgerV2.from_dict(json.loads(p.read_text(encoding="utf-8")))
        sp = w.v.state / "spot_ledger.json"
        if sp.exists():
            from tradingbot.accounting import SpotLedger
            w.v.spot_led = SpotLedger.from_dict(json.loads(sp.read_text(encoding="utf-8")))
        return w

    def source(self):
        """Mühürlü depo okuyucusu (gece biriminin gördüğü; `journal.open_store`)."""
        from tradingbot.research_engine.journal import open_store
        src, info = open_store(self.paths)
        assert src is not None, info
        return src

    # ------------------------------------------------------------------ depo
    def seal(self, *, now: datetime = SEAL_AT, frames: dict[str, dict[str, pd.DataFrame]] | None = None,
             spot: tuple[str, ...] = ("ETHUSDT",)) -> StoreFixture:
        """Araştırma deposunu dünya piyasasından kur ve mühürle (spot: aynı barlar, `spot` pazarı)."""
        sf = StoreFixture(self.paths, now=now)
        mk = frames if frames is not None else self.mk
        for sym, fr in mk.items():
            sf.add_all(sym, fr)
            if sym in spot:
                sf.add_all(sym, fr, market_="spot", tfs=("1m", "5m", "1d"))
        sf.seal()
        self.sf = sf
        return sf


_WORLD_CACHE: dict[str, Path] = {}


def standard_world(dst: Path) -> World:
    """Standart dünyanın bir KOPYASI (dünya süreç başına bir kez kurulur — ~30 sn; kopyası `dst`'ye). Kopyalar
    birbirinden bağımsızdır (geç fonlama, rotasyon vb. testleri kendi kopyasında değiştirir)."""
    import atexit
    import shutil
    import tempfile
    base = _WORLD_CACHE.get("standard")
    if base is None or not base.exists():
        base = Path(tempfile.mkdtemp(prefix="re_p2_world_"))
        atexit.register(shutil.rmtree, base, True)
        World(base).standard()
        _WORLD_CACHE["standard"] = base
    shutil.copytree(base, dst, dirs_exist_ok=True)
    return World.attach(dst)


__all__ = ["D1", "H1", "H4", "LIVE_LIMITS", "M1", "M5", "RECENT_FROM", "RECENT_TO", "SEAL_AT", "STEP", "SettlementRates",
           "StoreFixture", "World", "aggregate", "close_at", "config_text", "dt_of", "from_rows", "live_frames", "market", "ms",
           "new_ledger", "open_trade", "price_at", "rule_params", "run_trade", "utc", "walk", "walk_ledger", "worker_decide",
           "standard_world", "walk_sampled", "world_markets", "write_memory"]
