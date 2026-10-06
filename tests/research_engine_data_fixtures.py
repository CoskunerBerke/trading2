# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1b testleri için yardımcı: sahte data.binance.vision + sahte Binance REST (ağ YOK), sahte
saat ve sahte `journalctl`. Test dosyası DEĞİLDİR (pytest toplamaz).

Sahte arşiv her URL'yi istendiği anda deterministik olarak üretir (fiyat = zaman damgasının saf fonksiyonu; iki
ondalık), gerçek dosya biçimleriyle: vadeli kline zip'leri başlıklı, spot zip'leri başlıksız ve 2025-01-01'den itibaren
µs; `fundingRate` aylık (±ms oynamalı `calc_time`), `metrics` günlük (5 dk, `create_time` dizgesi, karışık sıra);
`.CHECKSUM` dosyaları `<sha256>  <ad>` biçiminde. REST uç noktaları aynı fiyat fonksiyonundan JSON döner (oluşmakta
olan bar dahil — yalnız kapanmış bar yazımı sınansın diye).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from tradingbot.research_engine.datastore import HttpResponse

UTC = timezone.utc
DAY = 86_400_000
TF = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
SPOT_US_FROM = 1_735_689_600_000


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def month_bounds(y: int, m: int) -> tuple[int, int]:
    return ms(utc(y, m, 1)), ms(utc(y + (m == 12), (m % 12) + 1, 1))


class FakeClock:
    """Sahte duvar saati: `now()` ve `sleep(s)` (saati ilerletir; uykular kaydedilir)."""

    def __init__(self, start: datetime):
        self.t = start
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(float(s))
        self.t = self.t + timedelta(seconds=float(s))

    def advance(self, s: float) -> None:
        self.t = self.t + timedelta(seconds=float(s))


def px(sym: str, ts: int, step: int) -> float:
    k = (int(ts) // step) % 97
    base = 100.0 + (sum(ord(c) for c in sym) % 50)
    return round(base + k * 0.25, 2)


def kline_row(sym: str, ts: int, step: int, *, us: bool = False, mark: bool = False) -> list[str]:
    o = px(sym, ts, step)
    c = px(sym, ts + step, step)
    h, lo = max(o, c) + 0.5, min(o, c) - 0.5
    vol = "0" if mark else f"{1 + (ts // step) % 7}.500"
    qv = "0" if mark else f"{(1 + (ts // step) % 7) * 1.5 * o:.4f}"
    n = 3600 if mark else 10 + (ts // step) % 13
    tb = "0" if mark else f"{0.5 + (ts // step) % 3}"
    tq = "0" if mark else f"{(0.5 + (ts // step) % 3) * o:.4f}"
    mul = 1000 if us else 1
    return [str(ts * mul), f"{o:.2f}", f"{h:.2f}", f"{lo:.2f}", f"{c:.2f}", vol, str((ts + step - 1) * mul + (999 if us else 0)),
            qv, str(n), tb, tq, "0"]


def zip_bytes(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zi, text)
    return buf.getvalue()


KHEAD = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"
MHEAD = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
         "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio")


def metrics_vals(sym: str, t: int) -> list[str]:
    k = (t // 300_000) % 50
    return [f"{1000 + k}.5000000000000000", f"{(1000 + k) * 100.0:.4f}", f"{1 + k / 100:.8f}", f"{2 + k / 100:.8f}",
            f"{1.5 + k / 100:.8f}", f"{0.9 + k / 100:.6f}"]


class FakeBinance:
    """`http(url, params, timeout) -> HttpResponse`. Seçenekler testlerde alan alan değiştirilir."""

    def __init__(self, clock: FakeClock, *, listing: dict[str, int] | None = None, monthly_lag_days: int = 2,
                 daily_lag_h: float = 0.0, per_request_s: float = 0.0):
        self.clock = clock
        self.listing = dict(listing or {})          # "futures/BTCUSDT" → ilk veri ms
        self.monthly_lag_days = monthly_lag_days
        self.daily_lag_h = daily_lag_h
        self.per_request_s = per_request_s
        self.no_checksum: set[str] = set()           # URL alt dizgeleri: .CHECKSUM 404
        self.bad_checksum: set[str] = set()          # URL alt dizgeleri: .CHECKSUM yanlış
        self.fail: dict[str, int] = {}               # URL alt dizgesi → kaç kez 503
        self.fail_always: set[str] = set()           # URL alt dizgesi → hep 503
        self.status_for: dict[str, int] = {}         # URL alt dizgesi → bu HTTP durumu (ör. 403 CDN engeli)
        #: delist: "market/SEMBOL" → delist anı (ms). Arşiv o ana kadar veri verir; REST `-1121 Invalid symbol`;
        #: vadeli exchangeInfo `SETTLING` (+ deliveryDate) ya da `delisted_absent` ise listede hiç yok
        self.delisted: dict[str, int] = {}
        self.delisted_absent = False
        self.delisted_in_exchangeinfo = True         # False: exchangeInfo hâlâ TRADING der (yalnız REST -1121 bilir)
        self.funding_interval_h: dict[str, int] = {}
        self.rest_status: int = 200
        self.rest_status_after: int | None = None    # bu kadar REST isteğinden sonra rest_status
        self.used_weight: Callable[[int], int] | None = None
        self.rest_value_shift: float = 0.0           # REST fiyatlarını kaydır (uzlaştırma farkı testi)
        self.log: list[tuple[datetime, str, dict]] = []
        self.rest_calls = 0

    # ------------------------------------------------------------------ yardımcılar
    def now_ms(self) -> int:
        return ms(self.clock.now())

    def first(self, market: str, sym: str) -> int:
        return self.listing.get(f"{market}/{sym}", ms(utc(2026, 7, 1)))

    def __call__(self, url: str, params: Any = None, timeout: float = 0.0) -> HttpResponse:
        self.log.append((self.clock.now(), url, dict(params or {})))
        if self.per_request_s:
            self.clock.advance(self.per_request_s)
        for k in list(self.fail):
            if k in url and self.fail[k] > 0:
                self.fail[k] -= 1
                return HttpResponse(503, {}, b"unavailable")
        if any(k in url for k in self.fail_always):
            return HttpResponse(503, {}, b"unavailable")
        for k, code in self.status_for.items():
            if k in url:
                return HttpResponse(int(code), {}, b"<Error><Code>AccessDenied</Code></Error>")
        if url.startswith("https://data.binance.vision/"):
            body = self.archive(url[len("https://data.binance.vision"):])
            return HttpResponse(200, {}, body) if body is not None else HttpResponse(404, {}, b"<Error>NoSuchKey</Error>")
        self.rest_calls += 1
        st = self.rest_status
        if self.rest_status_after is not None and self.rest_calls <= self.rest_status_after:
            st = 200
        hdr = {}
        if self.used_weight is not None:
            hdr["x-mbx-used-weight-1m"] = str(self.used_weight(self.rest_calls))
        if st != 200:
            return HttpResponse(st, hdr, b'{"code":-1003,"msg":"Too many requests"}')
        if self._rest_invalid_symbol(url, dict(params or {})):
            return HttpResponse(400, hdr, b'{"code":-1121,"msg":"Invalid symbol."}')
        return HttpResponse(200, hdr, json.dumps(self.rest(url, dict(params or {}))).encode("utf-8"))

    def _rest_invalid_symbol(self, url: str, p: dict) -> bool:
        market = "spot" if url.startswith("https://api.binance.com") else "futures"
        if p.get("symbol") and f"{market}/{p['symbol']}" in self.delisted:
            return True
        if p.get("symbols"):
            return any(f"spot/{s}" in self.delisted for s in json.loads(p["symbols"]))
        return False

    # ------------------------------------------------------------------ arşiv
    _RE = re.compile(r"^/data/(?P<seg>spot|futures/um)/(?P<kind>monthly|daily)/(?P<ds>[A-Za-z]+)/(?P<sym>[A-Z0-9]+)/"
                     r"(?:(?P<tf>[0-9a-z]+)/)?(?P<name>[^/]+?)(?P<chk>\.CHECKSUM)?$")

    def archive(self, path: str) -> bytes | None:
        m = self._RE.match(path)
        if not m:
            return None
        name = m.group("name")
        url = "https://data.binance.vision" + path
        if m.group("chk"):
            if any(k in url for k in self.no_checksum):
                return None
            data = self.archive(path[: -len(".CHECKSUM")])
            if data is None:
                return None
            sha = hashlib.sha256(data).hexdigest()
            if any(k in url for k in self.bad_checksum):
                sha = "0" * 64
            return f"{sha}  {name}\n".encode("ascii")
        market = "spot" if m.group("seg") == "spot" else "futures"
        sym, ds, tf, kind = m.group("sym"), m.group("ds"), m.group("tf"), m.group("kind")
        stamp = re.search(r"(\d{4}-\d{2}(?:-\d{2})?)\.zip$", name)
        if not stamp:
            return None
        s = stamp.group(1)
        if kind == "monthly":
            y, mo = (int(x) for x in s.split("-"))
            a, b = month_bounds(y, mo)
            if b + self.monthly_lag_days * DAY > self.now_ms():
                return None
        else:
            y, mo, d = (int(x) for x in s.split("-"))
            a = ms(utc(y, mo, d))
            b = a + DAY
            if b + int(self.daily_lag_h * 3_600_000) > self.now_ms():
                return None
        lo = self.first(market, sym)
        if b <= lo:
            return None
        hi = self.delisted.get(f"{market}/{sym}")
        if hi is not None:
            if a >= hi:
                return None
            b = min(b, hi)
        a2 = max(a, lo)
        csvname = name[:-4] + ".csv"
        if ds in ("klines", "markPriceKlines", "premiumIndexKlines"):
            step = TF[tf]
            us = market == "spot" and a >= SPOT_US_FROM
            rows = [kline_row(sym + ds, t, step, us=us, mark=ds != "klines") for t in range(a2 - a2 % step if a2 % step else a2, b, step)
                    if t >= a2]
            body = "\n".join(",".join(r) for r in rows) + "\n"
            if market == "futures":
                body = KHEAD + "\n" + body
            return zip_bytes(csvname, body)
        if ds == "fundingRate":
            iv = self.funding_interval_h.get(sym, 8) * 3_600_000
            out = ["calc_time,funding_interval_hours,last_funding_rate"]
            t = a2 + (-a2) % iv
            i = 0
            while t < b:
                jitter = i % 3                          # gerçek dosyalardaki gibi +0…+26 ms
                out.append(f"{t + jitter},{iv // 3_600_000},{0.0001 * ((t // iv) % 5):.8f}")
                t += iv
                i += 1
            return zip_bytes(csvname, "\n".join(out) + "\n")
        if ds == "metrics":
            if kind != "daily":
                return None
            ts = [t for t in range(a2 + (-a2) % 300_000, b, 300_000)]
            ts = ts[1::2] + ts[0::2]                     # karışık sıra (gerçek dosyalar gibi sırasız)
            lines = [MHEAD] + [",".join([datetime.fromtimestamp(t / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S"), sym,
                                         *metrics_vals(sym, t)]) for t in ts]
            return zip_bytes(csvname, "\n".join(lines) + "\n")
        return None

    # ------------------------------------------------------------------ REST
    def rest(self, url: str, p: dict) -> Any:
        now = self.now_ms()
        path = url.split("://", 1)[1].split("/", 1)[1]
        path = "/" + path
        sym = str(p.get("symbol") or "")
        if path in ("/fapi/v1/klines", "/api/v3/klines", "/fapi/v1/markPriceKlines", "/fapi/v1/premiumIndexKlines"):
            step = TF[str(p["interval"])]
            market = "spot" if path.startswith("/api") else "futures"
            ds = {"/fapi/v1/markPriceKlines": "markPriceKlines", "/fapi/v1/premiumIndexKlines": "premiumIndexKlines"}.get(path, "klines")
            st = max(int(p.get("startTime") or 0), self.first(market, sym))
            st += (-st) % step
            end = min(int(p.get("endTime") or now), now)
            lim = int(p.get("limit") or 500)
            rows = []
            t = st
            while t <= end and len(rows) < lim:
                r = kline_row(sym + ds, t, step, mark=ds != "klines")
                if self.rest_value_shift:
                    r[4] = f"{float(r[4]) + self.rest_value_shift:.2f}"
                rows.append(r)
                t += step
            return rows
        if path == "/fapi/v1/fundingRate":
            iv = self.funding_interval_h.get(sym, 8) * 3_600_000
            st = max(int(p.get("startTime") or 0), self.first("futures", sym))
            end = min(int(p.get("endTime") or now), now)
            t = st + (-st) % iv
            out = []
            while t <= end and len(out) < int(p.get("limit") or 100):
                out.append({"symbol": sym, "fundingTime": t, "fundingRate": f"{0.0001 * ((t // iv) % 5):.8f}",
                            "markPrice": "100.0"})
                t += iv
            return out
        if path.startswith("/futures/data/"):
            st = int(p.get("startTime") or 0)
            st += (-st) % 300_000
            end = min(int(p.get("endTime") or now), now)
            out = []
            t = st
            while t <= end and len(out) < int(p.get("limit") or 30):
                v = metrics_vals(sym, t)
                out.append({"symbol": sym, "timestamp": t, "sumOpenInterest": v[0], "sumOpenInterestValue": v[1],
                            "longShortRatio": {"topLongShortAccountRatio": v[2], "topLongShortPositionRatio": v[3],
                                               "globalLongShortAccountRatio": v[4]}.get(path.rsplit("/", 1)[1], v[2]),
                            "buySellRatio": v[5]})
                t += 300_000
            return out
        if path == "/fapi/v1/exchangeInfo":
            out = []
            for k, v in self.listing.items():
                if not k.startswith("futures/"):
                    continue
                dl = self.delisted.get(k) if self.delisted_in_exchangeinfo else None
                if dl is not None and self.delisted_absent:
                    continue
                out.append({"symbol": k.split("/")[1], "status": "SETTLING" if dl is not None else "TRADING",
                            "contractType": "PERPETUAL", "onboardDate": v,
                            "deliveryDate": dl if dl is not None else 4133404800000, "quoteAsset": "USDT"})
            return {"symbols": out}
        if path == "/api/v3/exchangeInfo":
            syms = json.loads(p.get("symbols") or "[]") or ([p["symbol"]] if p.get("symbol") else [])
            return {"symbols": [{"symbol": s, "status": "TRADING", "baseAsset": s[:-4], "quoteAsset": "USDT"} for s in syms]}
        return []

    # ------------------------------------------------------------------ sorgular
    def urls(self, part: str = "") -> list[str]:
        return [u for _, u, _ in self.log if part in u]

    def rest_log(self) -> list[tuple[datetime, str, dict]]:
        return [x for x in self.log if not x[1].startswith("https://data.binance.vision/")]


class Proc:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class FakeJournal:
    """`journalctl` yerine-geçeni. `lines` worker günlüğü; `rc`/`err` okunamayan günlük benzetimi. `clock` verilirse
    her okumanın (sahte) anı `times`'a yazılır; `then` = (n, satırlar): n. okumadan SONRA günlük bu satırları verir
    (ör. REST sürerken worker 429 almaya başlar)."""

    def __init__(self, lines: list[str] | None = None, *, rc: int = 0, err: str = "", clock: "FakeClock | None" = None,
                 then: tuple[int, list[str]] | None = None):
        self.lines = list(lines if lines is not None else ["tur 41 tamam: 40 sembol", "tur 42 tamam: 40 sembol"])
        self.rc, self.err = rc, err
        self.calls: list[list[str]] = []
        self.clock, self.then = clock, then
        self.times: list[datetime] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        assert cmd[0] == "journalctl", cmd
        if self.clock is not None:
            self.times.append(self.clock.now())
        lines = self.lines
        if self.then is not None and len(self.calls) > self.then[0]:
            lines = list(self.then[1])
        return Proc(self.rc, "\n".join(lines) + ("\n" if lines else ""), self.err)


class DataEnv:
    """Sahte VPS (P1a `FakeVps` + `FakeHost`) + sahte Binance + sahte saat + sahte günlük; `run(mode)` veri birimini
    bunlarla çalıştırır. `only` verilirse plan o seri anahtarlarına (ör. "futures/BTCUSDT/1h") süzülür."""

    def __init__(self, root, *, start: datetime, listing: dict[str, int] | None = None, config: str | None = None,
                 only: set[str] | None = None, **fb_kw: Any):
        from research_engine_fixtures import FakeHost, FakeVps
        self.root = root
        self.v = FakeVps(root)
        self.host = FakeHost(root, config_text=config if config is not None else CONFIG_ONE)
        self.clock = FakeClock(start)
        self.fb = FakeBinance(self.clock, listing=listing, **fb_kw)
        self.journal = FakeJournal()
        self.only = set(only) if only is not None else None
        self.paths = self.v.paths

    def run(self, mode: str = "update", **kw: Any) -> dict:
        from tradingbot.research_engine import datastore as DS
        flt = kw.pop("plan_filter", None)
        if flt is None and self.only is not None:
            keys = self.only
            flt = lambda s: s.key in keys  # noqa: E731
        return DS.run_data(self.paths, mode=mode, clock=self.clock.now, sleep=self.clock.sleep, http=self.fb,
                           runner=kw.pop("runner", self.journal), probes=kw.pop("probes", self.host.probes()),
                           env={"ALLOW_LIVE_TRADING": "false"}, app_dir=self.host.app,
                           free_bytes=kw.pop("free_bytes", 50 * 10 ** 9), plan_filter=flt, **kw)

    def store(self):
        from tradingbot.research_engine.store import ResearchStore
        return ResearchStore.for_paths(self.paths, clock_ms=lambda: ms(self.clock.now()))

    def status(self) -> dict:
        return json.loads(self.paths.data_status.read_text(encoding="utf-8"))


CONFIG_ONE = ("risk: {starting_equity_usdt: 1000}\nlearning_mode: {extra_entries: record_selectivity}\n"
              "entry_universe:\n  enabled: true\n  symbols: [BTC/USDT, SOL/USDT]\n")


__all__ = ["CONFIG_ONE", "DAY", "DataEnv", "FakeBinance", "FakeClock", "FakeJournal", "KHEAD", "Proc", "TF", "kline_row", "metrics_vals",
           "month_bounds", "ms", "px", "utc", "zip_bytes"]
