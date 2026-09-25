# -*- coding: utf-8 -*-
"""SİNYAL LABORATUVARI — hangi sinyal, hangi zaman diliminde, hangi bağlamda, MALİYET SONRASI gerçekten kazandırıyor?

Salt araştırma: defterlere, stratejilere ve parametrelere DOKUNMAZ; yalnız geçmiş mumları okur ve rapor yazar.

Yöntem (her sinyal aynı kuralla ölçülür):
* Sinyaller: (1) ortak katalog (`structures.analyze` — botların okuduğu mum/grafik/senaryo kayıtları, çekiç, kayan
  yıldız, sabah/akşam yıldızı, yutan, harami, üçgen, bayrak, flama, OBO, çift tepe/dip ...), GEÇMİŞTE ADIM ADIM
  (`stride` ≤ tazelik penceresi + 1: teyitli kayıt tazeyken mutlaka görülür) ve yalnız o ana kadarki barlarla;
  (2) katalogda olmayan, literatürde test edilmiş ek varyasyonlar (`EXTRA_SIGNALS`).
* İşlem: teyit barının KAPANIŞINDAN sonraki barın açılışında giriş (botlar gibi); stop = sinyalin stop'u; hedef =
  sinyalin yapısal hedefi, yoksa `default_rr` × risk; `max_hold_bars` sonunda kapanış. Aynı barda hem stop hem hedef
  → STOP sayılır (iyimserlik yok). Boşlukla açılışta açılış fiyatı kullanılır. Giriş tetikten `chase_atr` ATR'den
  fazla uzaksa işlem YOK (botların kovalama kuralı).
* Maliyet: taraf başına taker ücreti + kayma (config: %0,05 + 3 bps → gidiş-dönüş ≈ %0,16). R = net ÷ risk.
* Bağlam (yalnız o ana kadarki barlardan): hacim (son 20 bar ortalamasına oran), RSI14, EMA50/EMA200 trend yönü
  (sinyal tarafına göre trendle/trende karşı), volatilite (ATR%'nin son 200 bar medyanına oranı).
* Keşif / doğrulama: her zaman diliminde dönemin ilk `split` kısmı keşif, kalanı doğrulama. ADAY olmak için iki
  dönemde de ortalama R > 0 ve yeterli işlem gerekir; GÜÇLÜ ADAY için İKİ dönemin de %95 aralığı 0'ın üstünde olmalı
  ve sinyal, aynı dilim/yön/bağlamdaki rastgele girişi iki dönemde de geçmelidir (yalnız piyasa yönü olmasın). ZAYIF İZ (iki dönem pozitif, aralık 0'ı içeriyor) tek başına güvenilmez: rastgele yürüyüş verisinde
  kombinasyonların %6–11'i ZAYIF İZ, %0–0,5'i GÜÇLÜ ADAY çıktı (`tests/test_signal_lab.py`, mum içi yol da rastgele). Rastgele an/yön PLASEBO
  sinyali aynı hükümden geçer; aralıklar GÜN KÜMELİ bootstrap'tır (aynı gün birlikte hareket eden coinler bağımsız
  sayılmaz); gerçek sinyallerin aday oranı plaseboyu açıkça geçmiyorsa liste tesadüf olabilir.

PAPER/geçmiş testtir; kâr garantisi değildir.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import time
import zipfile
import zlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .timeframes import SUPPORTED_TIMEFRAMES, tf_ms

LONG, SHORT = "LONG", "SHORT"
MARKET = "USDM_PERP"
DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT",
                   "LINK/USDT", "LTC/USDT", "DOT/USDT", "NEAR/USDT")
#: sağlamlık denemesi için geniş liste (uzun geçmişli, likit USDⓈ-M perpetual'lar; sonradan listelenenler kendi başlangıcından)
WIDE_SYMBOLS = DEFAULT_SYMBOLS + ("TRX/USDT", "BCH/USDT", "UNI/USDT", "ATOM/USDT", "ETC/USDT", "FIL/USDT", "XLM/USDT",
                                  "AAVE/USDT", "APT/USDT", "ARB/USDT", "OP/USDT", "INJ/USDT", "SUI/USDT", "HBAR/USDT",
                                  "ICP/USDT", "SAND/USDT", "ALGO/USDT", "VET/USDT")
DEFAULT_TFS = ("5m", "15m", "1h", "4h")   # ortak analizin desteklediği dilimler (30m/1m YOK)
#: zaman dilimi başına varsayılan geçmiş (gün) — 1m yalnız maliyet karşılaştırması için (istenirse)
DEFAULT_DAYS = {"5m": 60, "15m": 180, "1h": 365, "4h": 730, "1d": 1460, "1w": 2920}
V_STRONG, V_WEAK, V_LOSS, V_NONE, V_THIN = "GÜÇLÜ ADAY", "ZAYIF İZ", "KAYBETTİRİR", "KANIT YOK", "VERİ AZ"


@dataclass(frozen=True)
class LabConfig:
    fee_pct: float = 0.05            # taraf başına taker (%), config.yaml v3.fees.futures_taker_pct
    slippage_bps: float = 3.0        # taraf başına kayma (bps), config.yaml v3.fees.slippage_bps
    window: int = 300                # analiz penceresi (bar)
    stride: int = 3                  # katalog değerlendirme adımı (≤ tazelik penceresi + 1)
    max_hold_bars: int = 24
    default_rr: float = 2.0
    chase_atr: float = 1.0
    min_risk_atr: float = 0.1
    max_risk_atr: float = 5.0
    split: float = 2 / 3
    min_is: int = 30
    min_oos: int = 20
    bootstrap_iters: int = 1000

    @property
    def cost_per_side(self) -> float:
        return self.fee_pct / 100.0 + self.slippage_bps / 10_000.0


@dataclass
class Event:
    symbol: str
    tf: str
    family: str
    name: str
    side: str
    i: int                           # teyit barının indeksi (karar bu barın kapanışında)
    t_ms: int                        # karar anı (teyit barının kapanışı)
    stop: float
    trigger: float | None = None
    target: float | None = None
    ctx: dict[str, str] = field(default_factory=dict)
    r: float | None = None
    cost_r: float = 0.0              # maliyetin R cinsinden payı (kısa dilimde küçük stop → büyük pay)
    exit: dict[str, Any] = field(default_factory=dict)   # kural çıkışı (algoritmalar); boş → sabit hedef/zaman
    exit_reason: str = ""
    hold: int = 0
    period: str = ""


# ---------------------------------------------------------------------------- veri
def download(provider: Any, symbol: str, tf: str, start_ms: int, end_ms: int, *, page: int = 1500) -> pd.DataFrame:
    step = tf_ms(tf)
    frames, cur = [], int(start_ms)
    while cur < end_ms:
        df = provider.klines(symbol, tf, limit=page, start_ms=cur, end_ms=end_ms)
        if df is None or len(df) == 0:
            break
        frames.append(df)
        last = int(df["timestamp"].iloc[-1])
        if last + step <= cur:
            break
        cur = last + step
    if not frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "close_time"])
    out = pd.concat(frames).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    if "is_closed" in out:
        out = out[out["is_closed"].astype(bool)]
    return out[["timestamp", "open", "high", "low", "close", "volume", "close_time"]].reset_index(drop=True)


ARCHIVE_BASE = "https://data.binance.vision/data/futures/um"
_DAY = 86_400_000


def _http_get(url: str, timeout: float = 60.0) -> bytes | None:
    """Arşiv dosyası (yoksa None). 404 dışındaki hatalar yükselir (sessiz boş veri YOK)."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "tradingbot-signal-lab/1"})
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise ConnectionError(f"arşiv indirilemedi: {url}: {last}")


def _parse_archive_zip(data: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        raw = zf.read(zf.namelist()[0]).decode("utf-8")
    df = pd.read_csv(io.StringIO(raw), header=None, dtype=str)
    df = df[df[0].str.isdigit()]                        # başlık satırı (yeni dosyalar) atılır
    out = pd.DataFrame({"timestamp": df[0].astype("int64"), "open": df[1].astype(float), "high": df[2].astype(float),
                        "low": df[3].astype(float), "close": df[4].astype(float), "volume": df[5].astype(float),
                        "close_time": df[6].astype("int64")})
    big = out["timestamp"] > 10 ** 14                   # mikro saniye → milisaniye
    out.loc[big, ["timestamp", "close_time"]] = out.loc[big, ["timestamp", "close_time"]] // 1000
    return out


def _month_start(ms: int) -> int:
    d = pd.Timestamp(int(ms), unit="ms", tz="UTC")
    return int(pd.Timestamp(year=d.year, month=d.month, day=1, tz="UTC").timestamp() * 1000)


def _next_month(ms: int) -> int:
    d = pd.Timestamp(int(ms), unit="ms", tz="UTC")
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return int(pd.Timestamp(year=y, month=m, day=1, tz="UTC").timestamp() * 1000)


class ArchiveProvider:
    """Binance toplu veri arşivi (data.binance.vision, USDⓈ-M): biten aylar ay dosyasından, içinde bulunulan ay gün
    dosyalarından; yalnız BİTMİŞ günler (bugün yok). `klines` imzası REST sağlayıcıyla aynıdır (`download` ortak)."""

    def __init__(self, fetch: Callable[[str], bytes | None] = _http_get, clock_ms: Callable[[], int] | None = None):
        self.fetch = fetch
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._files: dict[tuple, pd.DataFrame | None] = {}

    def _file(self, sym: str, tf: str, kind: str, stamp: str) -> pd.DataFrame | None:
        key = (sym, tf, kind, stamp)
        if key not in self._files:
            data = self.fetch(f"{ARCHIVE_BASE}/{kind}/klines/{sym}/{tf}/{sym}-{tf}-{stamp}.zip")
            self._files[key] = _parse_archive_zip(data) if data else None
        return self._files[key]

    def _days(self, sym: str, tf: str, month_ms: int, today_ms: int) -> pd.DataFrame | None:
        parts, day = [], month_ms
        while day < min(_next_month(month_ms), today_ms):
            f = self._file(sym, tf, "daily", pd.Timestamp(day, unit="ms", tz="UTC").strftime("%Y-%m-%d"))
            if f is not None:
                parts.append(f)
            day += _DAY
        return pd.concat(parts) if parts else None

    def klines(self, symbol: str, interval: str, limit: int = 1500, start_ms: int | None = None,
               end_ms: int | None = None) -> pd.DataFrame:
        sym = symbol.split(":")[0].replace("/", "").upper()
        now = int(self.clock_ms())
        today = now - now % _DAY
        start, end = int(start_ms or 0), int(end_ms if end_ms is not None else now)
        this_month, cur, got = _month_start(now), _month_start(start), []
        while cur <= min(end, today) and sum(len(x) for x in got) < limit:
            f = self._file(sym, interval, "monthly", pd.Timestamp(cur, unit="ms", tz="UTC").strftime("%Y-%m")) \
                if cur < this_month else None
            if f is None and cur >= _month_start(today - _DAY * 40):   # ay dosyası henüz yayımlanmadı → gün dosyaları
                f = self._days(sym, interval, cur, today)
            if f is not None and len(f):
                got.append(f[(f["timestamp"] >= start) & (f["timestamp"] <= end)])
            cur = _next_month(cur)
        if not got:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "close_time", "is_closed"])
        out = pd.concat(got).drop_duplicates("timestamp").sort_values("timestamp").head(limit).reset_index(drop=True)
        out["is_closed"] = out["close_time"] < now
        return out


def cache_path(cache_dir: Path, symbol: str, tf: str) -> Path:
    return Path(cache_dir) / f"{symbol.replace('/', '').upper()}_{tf}.csv.gz"


def load_series(symbol: str, tf: str, *, days: int, cache_dir: Path, provider_factory: Callable[[], Any] | None,
                now_ms: int) -> pd.DataFrame:
    """Önbellekten okur; eksik baş/son varsa sağlayıcıdan tamamlar ve önbelleğe yazar (yalnız kapanmış barlar)."""
    step = tf_ms(tf)
    end = now_ms - now_ms % step
    start = end - int(days) * 86_400_000
    p = cache_path(cache_dir, symbol, tf)
    meta_p = p.with_name(p.name + ".meta.json")
    df = pd.read_csv(p) if p.exists() else pd.DataFrame()
    asked = json.loads(meta_p.read_text(encoding="utf-8")).get("requested_start_ms") if meta_p.exists() else None
    # borsada daha eski bar yoksa (sonradan listelenmiş) aynı başlangıç bir daha istenmez
    need_head = df.empty or (int(df["timestamp"].iloc[0]) > start + step and (asked is None or int(asked) > start))
    need_tail = df.empty or int(df["timestamp"].iloc[-1]) < end - 2 * step
    if (need_head or need_tail) and provider_factory is not None:
        prov = provider_factory()
        parts = [df] if not df.empty else []
        if need_head:
            parts.append(download(prov, symbol, tf, start, int(df["timestamp"].iloc[0]) if not df.empty else end))
        if need_tail and not df.empty:
            parts.append(download(prov, symbol, tf, int(df["timestamp"].iloc[-1]) + step, end))
        df = pd.concat([x for x in parts if len(x)]).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p, index=False, compression="gzip")
        if need_head:
            meta_p.write_text(json.dumps({"requested_start_ms": int(start)}), encoding="utf-8")
    if df.empty:
        return df
    return df[df["timestamp"] >= start].reset_index(drop=True)


# ---------------------------------------------------------------------------- göstergeler (nedensel)
def indicators(df: pd.DataFrame) -> dict[str, np.ndarray]:
    h, lo, c, v = (df[k].astype(float) for k in ("high", "low", "close", "volume"))
    prev_c = c.shift(1)
    tr = pd.concat([h - lo, (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    atr_pct = atr / c
    return {"atr": atr.to_numpy(), "rsi": rsi.to_numpy(),
            "ema50": c.ewm(span=50, adjust=False, min_periods=50).mean().to_numpy(),
            "ema200": c.ewm(span=200, adjust=False, min_periods=200).mean().to_numpy(),
            "vol_avg": v.rolling(20, min_periods=20).mean().shift(1).to_numpy(),           # ÖNCEKİ 20 bar
            "atr_med": atr_pct.rolling(200, min_periods=50).median().shift(1).to_numpy(),
            "atr_pct": atr_pct.to_numpy()}


def context(ind: dict[str, np.ndarray], arr: dict[str, np.ndarray], i: int, side: str) -> dict[str, str]:
    c = arr["close"][i]
    vr = arr["volume"][i] / ind["vol_avg"][i] if ind["vol_avg"][i] and ind["vol_avg"][i] > 0 else float("nan")
    rsi, e50, e200 = ind["rsi"][i], ind["ema50"][i], ind["ema200"][i]
    vx = ind["atr_pct"][i] / ind["atr_med"][i] if ind["atr_med"][i] and ind["atr_med"][i] > 0 else float("nan")
    if math.isnan(e200) or math.isnan(e50):
        trend = "bilinmiyor"
    else:
        up, down = c > e50 > e200, c < e50 < e200
        trend = "karışık" if not (up or down) else ("trendle" if (up and side == LONG) or (down and side == SHORT) else "trende_karşı")
    return {"hacim": "bilinmiyor" if math.isnan(vr) else ("düşük(<0.8x)" if vr < 0.8 else ("yüksek(>1.5x)" if vr > 1.5 else "normal")),
            "rsi": "bilinmiyor" if math.isnan(rsi) else ("<30" if rsi < 30 else ("30-50" if rsi < 50 else ("50-70" if rsi < 70 else ">70"))),
            "trend": trend,
            "volatilite": "bilinmiyor" if math.isnan(vx) else ("düşük" if vx < 0.8 else ("yüksek" if vx > 1.25 else "normal"))}


# ---------------------------------------------------------------------------- sinyaller
def catalog_events(df: pd.DataFrame, symbol: str, tf: str, cfg: LabConfig) -> list[Event]:
    """Ortak katalog kayıtları, geçmişte adım adım (yalnız o ana kadarki barlarla). Kayıt yalnız TEYİT edildiği
    değerlendirme aralığında alınır; seviyeler teyitte donmuştur (`structures.audit` ile denetlenen sözleşme)."""
    from .structures.analysis import analyze, clear_cache
    rows = df[["timestamp", "open", "high", "low", "close", "volume", "close_time"]].to_dict("records")
    idx = {int(r["timestamp"]): k for k, r in enumerate(rows)}
    step = tf_ms(tf)
    out: list[Event] = []
    seen: set[tuple] = set()
    prev_asof = None
    ends = list(range(cfg.window, len(rows) + 1, cfg.stride))
    if ends and ends[-1] != len(rows):
        ends.append(len(rows))                          # son kapanmış bar da değerlendirilir
    for n_eval, end in enumerate(ends):
        sub = rows[end - cfg.window:end]
        as_of = int(sub[-1]["timestamp"]) + step
        if prev_asof is None:                           # ilk değerlendirme yalnız kendi barını alır (adım adımla aynı)
            prev_asof = as_of - step
        an = analyze(market=MARKET, symbol=symbol, timeframe=tf, bars=sub, as_of_ms=as_of)
        for r in an.get("records") or []:
            c_ms, side = r.get("confirmed_at_ms"), r.get("side")
            if not c_ms or side not in (LONG, SHORT) or not (prev_asof < int(c_ms) <= as_of) or r.get("stop") is None:
                continue
            i = idx.get(int(c_ms) - step)
            # aynı kırılımın kardeş yorumları (aynı ad + taraf + teyit barı) TEK işlem sayılır (bağımsızlık)
            pid = (str(r.get("name")), side, i)
            if i is None or pid in seen:
                continue
            seen.add(pid)
            tg = [float(t) for t in (r.get("targets") or []) if t is not None]
            out.append(Event(symbol, tf, str(r.get("family")), str(r.get("name")), side, i, int(c_ms), float(r["stop"]),
                             trigger=(r.get("trigger") or {}).get("level"), target=tg[0] if tg else None))
        prev_asof = as_of
        if n_eval % 400 == 399:
            clear_cache()
    clear_cache()
    return out


def _ev(symbol, tf, name, side, i, arr, stop, trigger, family="extra") -> Event:
    step = int(arr["step"])
    return Event(symbol, tf, family, name, side, i, int(arr["timestamp"][i]) + step, float(stop), trigger=float(trigger))


def extra_events(df: pd.DataFrame, symbol: str, tf: str, atr: np.ndarray) -> list[Event]:
    """Katalogda olmayan, literatürde sınanmış varyasyonlar (tanımlar sabit; sonuca göre AYARLANMAZ):
    THREE_INSIDE_UP/DOWN (üç yukarı/aşağı dönüş), THREE_OUTSIDE_UP/DOWN, NR7 ve İÇ BAR kırılımı (Crabel: daralma →
    genişleme), DONCHIAN20 kırılımı (kaplumbağa; stop 2 ATR)."""
    o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    arr = {"timestamp": df["timestamp"].to_numpy(dtype=np.int64), "step": tf_ms(tf)}
    out: list[Event] = []
    n = len(df)
    buf = 0.25
    for i in range(7, n):
        a = atr[i]
        if not a or math.isnan(a):
            continue
        b1, b2, b3 = i - 2, i - 1, i
        body1 = abs(c[b1] - o[b1])
        down_before, up_before = c[b1] < c[b1 - 5], c[b1] > c[b1 - 5]
        lo3, hi3 = min(lo[b1], lo[b2], lo[b3]), max(h[b1], h[b2], h[b3])
        # üç yukarı dönüş: uzun ayı → içinde boğa (harami) → 1. mumun açılışının üstünde boğa kapanış
        if down_before and c[b1] < o[b1] and body1 >= 0.5 * a and c[b2] > o[b2] and o[b2] >= c[b1] and c[b2] <= o[b1] \
                and c[b3] > o[b3] and c[b3] > o[b1]:
            out.append(_ev(symbol, tf, "THREE_INSIDE_UP", LONG, i, arr, lo3 - buf * a, c[i]))
        if up_before and c[b1] > o[b1] and body1 >= 0.5 * a and c[b2] < o[b2] and o[b2] <= c[b1] and c[b2] >= o[b1] \
                and c[b3] < o[b3] and c[b3] < o[b1]:
            out.append(_ev(symbol, tf, "THREE_INSIDE_DOWN", SHORT, i, arr, hi3 + buf * a, c[i]))
        # üç dış: ayı → onu yutan boğa → daha yüksek boğa kapanış
        body2 = abs(c[b2] - o[b2])
        if down_before and c[b1] < o[b1] and c[b2] > o[b2] and o[b2] <= c[b1] and c[b2] >= o[b1] and body2 > body1 \
                and c[b3] > c[b2]:
            out.append(_ev(symbol, tf, "THREE_OUTSIDE_UP", LONG, i, arr, lo3 - buf * a, c[i]))
        if up_before and c[b1] > o[b1] and c[b2] < o[b2] and o[b2] >= c[b1] and c[b2] <= o[b1] and body2 > body1 \
                and c[b3] < c[b2]:
            out.append(_ev(symbol, tf, "THREE_OUTSIDE_DOWN", SHORT, i, arr, hi3 + buf * a, c[i]))
    # daralma kırılımları: NR7 / iç bar → sonraki 3 bar içinde İLK kapanış kırılımı
    rng = h - lo
    for m in range(7, n - 1):
        setups = []
        if rng[m] <= rng[m - 6:m + 1].min():
            setups.append(("NR7_BREAKOUT", h[m], lo[m]))
        if h[m] < h[m - 1] and lo[m] > lo[m - 1]:
            setups.append(("INSIDE_BAR_BREAKOUT", h[m - 1], lo[m - 1]))
        for name, top, bot in setups:
            for k in range(m + 1, min(n, m + 4)):
                if c[k] > top:
                    out.append(_ev(symbol, tf, name, LONG, k, arr, bot, top))
                    break
                if c[k] < bot:
                    out.append(_ev(symbol, tf, name, SHORT, k, arr, top, bot))
                    break
    # Donchian 20: ilk kapanış kırılımı (önceki bar kanal içindeydi)
    hi20 = pd.Series(h).rolling(20).max().shift(1).to_numpy()
    lo20 = pd.Series(lo).rolling(20).min().shift(1).to_numpy()
    for i in range(22, n):
        a = atr[i]
        if not a or math.isnan(a) or math.isnan(hi20[i]):
            continue
        if c[i] > hi20[i] and c[i - 1] <= hi20[i - 1]:
            out.append(_ev(symbol, tf, "DONCHIAN20_BREAKOUT", LONG, i, arr, c[i] - 2 * a, hi20[i]))
        elif c[i] < lo20[i] and c[i - 1] >= lo20[i - 1]:
            out.append(_ev(symbol, tf, "DONCHIAN20_BREAKOUT", SHORT, i, arr, c[i] + 2 * a, lo20[i]))
    # PLASEBO: rastgele an + rastgele yön (stop 1,5 ATR) — "tesadüfen kaç aday çıkar" ölçüsü; gerçek sinyal bunu geçmeli
    #  (bar başına zaman damgasından türetilir: seri uzunluğuna/geleceğe bağlı DEĞİL, her çalıştırmada aynı)
    for i in range(22, n):
        u = zlib.crc32(f"{symbol}|{tf}|{int(arr['timestamp'][i])}".encode()) / 2 ** 32
        a = atr[i]
        if u >= 0.05 or not a or math.isnan(a):
            continue
        side = LONG if u < 0.025 else SHORT
        out.append(_ev(symbol, tf, "PLACEBO_RANDOM", side, i, arr, c[i] - 1.5 * a if side == LONG else c[i] + 1.5 * a,
                       c[i], family="placebo"))
    return out


# ---------------------------------------------------------------------------- algoritmalar (kural çıkışlı)
#: Formasyondan farklı, literatürde sınanmış işlem ALGORİTMALARI. Tanımlar sabit (sonuca göre AYARLANMAZ); her biri kendi
#: çıkış kuralıyla simüle edilir ve AYNI çıkış kuralını kullanan rastgele girişli eşiyle (PLACEBO_<ad>) karşılaştırılır.
ALGOS = ("TREND_DONCHIAN_20_10", "TSMOM_28", "RSI2_REVERSION", "BB_SQUEEZE_BREAKOUT")
XSMOM = "XSMOM_28_WEEKLY"


def _wilder_rsi(c: pd.Series, n: int) -> np.ndarray:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy()


def aux_series(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Algoritmaların nedensel yardımcı serileri (i barında yalnız ≤ i barları)."""
    h, lo, c = (df[k].astype(float) for k in ("high", "low", "close"))
    sd20 = c.rolling(20).std(ddof=0)
    sma20 = c.rolling(20).mean()
    bbw = (4 * sd20 / sma20)
    return {"hi20": h.rolling(20).max().shift(1).to_numpy(), "lo20": lo.rolling(20).min().shift(1).to_numpy(),
            "hi10": h.rolling(10).max().shift(1).to_numpy(), "lo10": lo.rolling(10).min().shift(1).to_numpy(),
            "mom28": (c / c.shift(28) - 1).to_numpy(), "sma5": c.rolling(5).mean().to_numpy(), "sma20": sma20.to_numpy(),
            "sma200": c.rolling(200).mean().to_numpy(), "rsi2": _wilder_rsi(c, 2),
            "bb_up": (sma20 + 2 * sd20).to_numpy(), "bb_dn": (sma20 - 2 * sd20).to_numpy(),
            "squeeze": (bbw <= bbw.rolling(120, min_periods=60).quantile(0.1).shift(1)).to_numpy()}


def _h(*parts: Any) -> float:
    return zlib.crc32("|".join(str(x) for x in parts).encode()) / 2 ** 32


def algo_events(df: pd.DataFrame, symbol: str, tf: str, atr: np.ndarray, aux: dict[str, np.ndarray]) -> list[Event]:
    """Algoritma girişleri (karar i barının kapanışında) + her biri için aynı çıkışlı rastgele eş (plasebo)."""
    c = df["close"].to_numpy(dtype=float)
    arr = {"timestamp": df["timestamp"].to_numpy(dtype=np.int64), "step": tf_ms(tf)}
    n, out = len(df), []
    nan = lambda x: x is None or x != x  # noqa: E731

    def add(name, side, i, stop_mult, exit_spec, stop=None):
        a = atr[i]
        st = stop if stop is not None else (c[i] - stop_mult * a if side == LONG else c[i] + stop_mult * a)
        out.append(Event(symbol, tf, "algo", name, side, i, int(arr["timestamp"][i]) + arr["step"], float(st),
                         trigger=None, exit=dict(exit_spec)))

    for i in range(210, n):
        a = atr[i]
        if nan(a) or a <= 0:
            continue
        # 1) kaplumbağa trend takibi: 20 bar kırılımı, 10 bar karşı kanal çıkışı, 2 ATR ilk stop
        if not nan(aux["hi20"][i]) and c[i] > aux["hi20"][i] and c[i - 1] <= aux["hi20"][i - 1]:
            add("TREND_DONCHIAN_20_10", LONG, i, 2.0, {"kind": "channel", "max_bars": 300})
        elif not nan(aux["lo20"][i]) and c[i] < aux["lo20"][i] and c[i - 1] >= aux["lo20"][i - 1]:
            add("TREND_DONCHIAN_20_10", SHORT, i, 2.0, {"kind": "channel", "max_bars": 300})
        # 2) zaman serisi momentumu (28 bar getirisi işaret değiştirince), 3 ATR felaket stopu
        m0, m1 = aux["mom28"][i], aux["mom28"][i - 1]
        if not nan(m0) and not nan(m1):
            if m0 > 0 >= m1:
                add("TSMOM_28", LONG, i, 3.0, {"kind": "sign", "max_bars": 300})
            elif m0 < 0 <= m1:
                add("TSMOM_28", SHORT, i, 3.0, {"kind": "sign", "max_bars": 300})
        # 3) RSI(2) geri dönüş (Connors): 200 ortalama yönünde aşırı uç, 5 ortalamayı geçince çık, en çok 10 bar
        r0, r1, sma = aux["rsi2"][i], aux["rsi2"][i - 1], aux["sma200"][i]
        if not nan(r0) and not nan(r1) and not nan(sma):
            if c[i] > sma and r0 < 10 <= r1:
                add("RSI2_REVERSION", LONG, i, 3.0, {"kind": "sma_cross", "max_bars": 10})
            elif c[i] < sma and r0 > 90 >= r1:
                add("RSI2_REVERSION", SHORT, i, 3.0, {"kind": "sma_cross", "max_bars": 10})
        # 4) Bollinger sıkışma kırılımı: bant genişliği son 120 barın en dar %10'unda iken ilk kapanış band dışı
        if bool(aux["squeeze"][i - 1]) and not nan(aux["bb_up"][i]):
            if c[i] > aux["bb_up"][i] and c[i - 1] <= aux["bb_up"][i - 1]:
                add("BB_SQUEEZE_BREAKOUT", LONG, i, 0.0, {"kind": "mid_cross", "max_bars": 60}, stop=min(aux["sma20"][i], c[i] - a))
            elif c[i] < aux["bb_dn"][i] and c[i - 1] >= aux["bb_dn"][i - 1]:
                add("BB_SQUEEZE_BREAKOUT", SHORT, i, 0.0, {"kind": "mid_cross", "max_bars": 60}, stop=max(aux["sma20"][i], c[i] + a))
    # EŞ (plasebo): her algoritma için barların %1'inde rastgele an ve yön, AYNI çıkış kuralı ve tipik stop mesafesi.
    # Seçim yalnız o barın zaman damgasından türetilir (gerçek sinyale ve seri uzunluğuna bağlı DEĞİL).
    for name, mult, spec in PLACEBO_SPECS:
        for j in range(210, n):
            u = _h(symbol, tf, name, int(arr["timestamp"][j]))
            a = atr[j]
            if u >= 0.01 or nan(a) or a <= 0:
                continue
            side = LONG if u < 0.005 else SHORT
            out.append(Event(symbol, tf, "placebo", "PLACEBO_" + name, side, j, int(arr["timestamp"][j]) + arr["step"],
                             float(c[j] - mult * a if side == LONG else c[j] + mult * a), trigger=None, exit=dict(spec)))
    return out


#: (algoritma, tipik ilk stop ATR katı, çıkış kuralı) — eşlerin (plasebo) tanımı
PLACEBO_SPECS = (("TREND_DONCHIAN_20_10", 2.0, {"kind": "channel", "max_bars": 300}),
                 ("TSMOM_28", 3.0, {"kind": "sign", "max_bars": 300}),
                 ("RSI2_REVERSION", 3.0, {"kind": "sma_cross", "max_bars": 10}),
                 ("BB_SQUEEZE_BREAKOUT", 1.5, {"kind": "mid_cross", "max_bars": 60}))


def _rule_exit(kind: str, k: int, s: float, c: np.ndarray, aux: dict[str, np.ndarray], spec: dict, j: int) -> bool:
    """k barının KAPANIŞINDA çıkış koşulu (çıkış bir sonraki barın açılışında)."""
    if kind == "channel":
        ref = aux["lo10"][k] if s > 0 else aux["hi10"][k]
        return ref == ref and ((c[k] < ref) if s > 0 else (c[k] > ref))
    if kind == "sign":
        m = aux["mom28"][k]
        return m == m and ((m <= 0) if s > 0 else (m >= 0))
    if kind == "sma_cross":
        m = aux["sma5"][k]
        return m == m and ((c[k] > m) if s > 0 else (c[k] < m))
    if kind == "mid_cross":
        m = aux["sma20"][k]
        return m == m and ((c[k] < m) if s > 0 else (c[k] > m))
    if kind == "hold":
        return k >= j + int(spec["bars"]) - 1
    raise ValueError(f"bilinmeyen çıkış: {kind}")


def simulate_rule(ev: Event, arr: dict[str, np.ndarray], atr: np.ndarray, aux: dict[str, np.ndarray], cfg: LabConfig) -> str:
    """Kural çıkışlı işlem: sonraki açılışta giriş; ilk stop bar içinde (kötümser); kural kapanışta tetiklenirse sonraki
    açılışta çıkış; `max_bars` dolarsa kapanışta. Veri bitmeden kapanmayan işlem SAYILMAZ (sona doğru yanlılık yok)."""
    o, h, lo, c = arr["open"], arr["high"], arr["low"], arr["close"]
    n, j = len(o), ev.i + 1
    if j >= n - 1:
        return "NO_FUTURE_DATA"
    s = 1.0 if ev.side == LONG else -1.0
    entry, stop, a = float(o[j]), float(ev.stop), float(atr[ev.i])
    if not a or a != a:
        return "NO_ATR"
    risk = s * (entry - stop)
    if risk <= cfg.min_risk_atr * a:
        return "STOP_TOO_CLOSE"
    if risk > 10 * a:
        return "STOP_TOO_FAR"
    spec = ev.exit
    kind, max_bars = str(spec["kind"]), int(spec.get("max_bars") or spec.get("bars") or 300)
    exit_px, reason, k = None, "TIME", j
    for k in range(j, min(n, j + max_bars)):
        if (s > 0 and lo[k] <= stop) or (s < 0 and h[k] >= stop):
            exit_px, reason = ((min(o[k], stop) if s > 0 else max(o[k], stop)) if k > j else stop), "STOP"
            break
        if _rule_exit(kind, k, s, c, aux, spec, j):
            if k + 1 >= n:
                return "NO_FUTURE_DATA"
            exit_px, reason, k = float(o[k + 1]), "RULE", k + 1
            break
    else:
        if j + max_bars > n:
            return "NO_FUTURE_DATA"
        exit_px, k = float(c[j + max_bars - 1]), j + max_bars - 1
    if exit_px is None:
        return "NO_FUTURE_DATA"
    cost = (entry + float(exit_px)) * cfg.cost_per_side
    ev.r, ev.cost_r, ev.exit_reason, ev.hold = (s * (float(exit_px) - entry) - cost) / risk, cost / risk, reason, k - j + 1
    return ""


def xsmom_events(frames: dict[str, pd.DataFrame], cfg: LabConfig, *, lookback: int = 28, top: int = 5, hold: int = 7) -> list[Event]:
    """Coinler arası momentum (1d): her 7 günde bir, son 28 günün en güçlü `top` coini LONG, en zayıf `top` coini SHORT;
    bir sonraki yeniden dengelemede çıkış. Felaket stopu 3 ATR (R birimi). Eşi: aynı günlerde rastgele seçilen coinler."""
    tf = "1d"
    closes = pd.DataFrame({s: df.set_index("timestamp")["close"] for s, df in frames.items() if len(df)}).sort_index()
    if closes.shape[1] < 2 * top:
        return []
    prep = {}
    for s, df in frames.items():
        if len(df) < 60:
            continue
        ind = indicators(df)
        prep[s] = (df, {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}, ind,
                   {int(t): i for i, t in enumerate(df["timestamp"])}, aux_series(df))
    out: list[Event] = []
    ts = list(closes.index)
    for r in range(lookback + 1, len(ts) - hold - 1, hold):
        ret = (closes.iloc[r] / closes.iloc[r - lookback] - 1).dropna()
        ret = ret[[s for s in ret.index if s in prep]]
        if len(ret) < 2 * top:
            continue
        legs = [(s, LONG, XSMOM) for s in ret.nlargest(top).index] + [(s, SHORT, XSMOM) for s in ret.nsmallest(top).index]
        pool = sorted(ret.index)
        rnd = sorted(pool, key=lambda s: _h("xs", ts[r], s))
        legs += [(s, LONG, "PLACEBO_" + XSMOM) for s in rnd[:top]] + [(s, SHORT, "PLACEBO_" + XSMOM) for s in rnd[top:2 * top]]
        for s, side, name in legs:
            df, arr, ind, idx, aux = prep[s]
            i = idx.get(int(ts[r]))
            if i is None or ind["atr"][i] != ind["atr"][i]:
                continue
            a = ind["atr"][i]
            ev = Event(s, tf, "placebo" if name.startswith("PLACEBO_") else "algo", name, side, i, int(ts[r]) + tf_ms(tf),
                       float(arr["close"][i] - 3 * a if side == LONG else arr["close"][i] + 3 * a),
                       exit={"kind": "hold", "bars": hold})
            if simulate_rule(ev, arr, ind["atr"], aux, cfg):
                continue
            ev.ctx = context(ind, arr, ev.i, ev.side)
            out.append(ev)
    return out


EXTRA_SIGNALS = ("THREE_INSIDE_UP", "THREE_INSIDE_DOWN", "THREE_OUTSIDE_UP", "THREE_OUTSIDE_DOWN", "NR7_BREAKOUT",
                 "INSIDE_BAR_BREAKOUT", "DONCHIAN20_BREAKOUT")


# ---------------------------------------------------------------------------- işlem simülasyonu
def simulate(ev: Event, arr: dict[str, np.ndarray], atr: np.ndarray, cfg: LabConfig) -> str:
    """Olayı işleme çevirir; `ev.r`/`exit_reason`/`hold` doldurur. Döner: "" (işlem) ya da atlama nedeni."""
    o, h, lo, c = arr["open"], arr["high"], arr["low"], arr["close"]
    n = len(o)
    j = ev.i + 1
    if j + cfg.max_hold_bars > n:
        return "NO_FUTURE_DATA"
    s = 1.0 if ev.side == LONG else -1.0
    entry, stop, a = float(o[j]), float(ev.stop), float(atr[ev.i])
    if not a or math.isnan(a):
        return "NO_ATR"
    risk = s * (entry - stop)
    if risk <= cfg.min_risk_atr * a:
        return "STOP_TOO_CLOSE"
    if risk > cfg.max_risk_atr * a:
        return "STOP_TOO_FAR"
    if ev.trigger is not None and s * (entry - float(ev.trigger)) > cfg.chase_atr * a:
        return "CHASE"
    target = float(ev.target) if ev.target is not None and s * (float(ev.target) - entry) > 0 else entry + s * cfg.default_rr * risk
    exit_px, reason, k = None, "TIME", j
    for k in range(j, j + cfg.max_hold_bars):
        if s > 0:
            if lo[k] <= stop:
                exit_px, reason = (min(o[k], stop) if k > j else stop), "STOP"
            elif h[k] >= target:
                exit_px, reason = (max(o[k], target) if k > j else target), "TARGET"
        else:
            if h[k] >= stop:
                exit_px, reason = (max(o[k], stop) if k > j else stop), "STOP"
            elif lo[k] <= target:
                exit_px, reason = (min(o[k], target) if k > j else target), "TARGET"
        if exit_px is not None:
            break
    if exit_px is None:
        exit_px = float(c[j + cfg.max_hold_bars - 1])
    cost = (entry + float(exit_px)) * cfg.cost_per_side
    pnl = s * (float(exit_px) - entry) - cost
    ev.r, ev.cost_r, ev.exit_reason, ev.hold = pnl / risk, cost / risk, reason, k - j + 1
    return ""


def process_series(df: pd.DataFrame, symbol: str, tf: str, cfg: LabConfig, *, catalog: bool = True,
                   algos: bool = True) -> tuple[list[Event], dict]:
    arr = {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
    ind = indicators(df)
    aux = aux_series(df) if algos else {}
    evs = (catalog_events(df, symbol, tf, cfg) if catalog else []) + extra_events(df, symbol, tf, ind["atr"]) \
        + (algo_events(df, symbol, tf, ind["atr"], aux) if algos else [])
    skipped: dict[str, int] = {}
    done: list[Event] = []
    for ev in evs:
        why = simulate_rule(ev, arr, ind["atr"], aux, cfg) if ev.exit else simulate(ev, arr, ind["atr"], cfg)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        ev.ctx = context(ind, arr, ev.i, ev.side)
        done.append(ev)
    return done, {"symbol": symbol, "tf": tf, "bars": len(df), "signals": len(evs), "trades": len(done), "skipped": skipped,
                  "first": int(df["timestamp"].iloc[0]) if len(df) else None, "last": int(df["timestamp"].iloc[-1]) if len(df) else None}


class DownloadAborted(RuntimeError):
    """İndirme art arda başarısız: rapor eksik geçmişle üretilmez."""


def coverage_problem(meta: dict, days: int, tf: str, cache_dir: Path, now_ms: int) -> str | None:
    """İstenen geçmişin %90'ından azı varsa uyarı metni — coin o tarihte listelenmemişse (en az bu kadar geriden
    istenip borsada daha eski bar çıkmadığı kayıtlıysa) uyarı YOK."""
    if meta.get("error"):
        return meta["error"]
    step = tf_ms(tf)
    want = int(days) * 86_400_000 // step
    if meta.get("bars", 0) >= 0.9 * want:
        return None
    start = (now_ms - now_ms % step) - int(days) * 86_400_000
    mp = cache_path(cache_dir, meta["symbol"], tf)
    mp = mp.with_name(mp.name + ".meta.json")
    asked = json.loads(mp.read_text(encoding="utf-8")).get("requested_start_ms") if mp.exists() else None
    if asked is not None and int(asked) <= start:
        return None
    return f"EKSİK_GEÇMİŞ ({meta.get('bars', 0)}/{want} bar)"


def _task(args: tuple) -> tuple[list[dict], dict]:
    cache_dir, symbol, tf, days, now_ms, cfg_d, catalog, algos = args
    cfg = LabConfig(**cfg_d)
    df = load_series(symbol, tf, days=days, cache_dir=Path(cache_dir), provider_factory=None, now_ms=now_ms)
    if len(df) < cfg.window + cfg.max_hold_bars + 10:
        return [], {"symbol": symbol, "tf": tf, "bars": len(df), "error": "YETERSİZ_VERİ"}
    evs, meta = process_series(df, symbol, tf, cfg, catalog=catalog, algos=algos)
    return [asdict(e) for e in evs], meta


# ---------------------------------------------------------------------------- istatistik ve hüküm
def r_stats(rs: np.ndarray, iters: int, seed: int = 20260925, days: np.ndarray | None = None) -> dict[str, Any]:
    """Özet + ortalama R için %95 aralık. `days` verilirse GÜN KÜMELİ bootstrap: aynı gün açılan işlemler (coinler birlikte
    hareket eder) birlikte yeniden örneklenir; işlemleri bağımsız sayıp aralığı yapay daraltmaz."""
    n = int(len(rs))
    if n == 0:
        return {"n": 0}
    wins, losses = rs[rs > 0], rs[rs < 0]
    eq = np.cumsum(rs)
    dd = float(np.min(eq - np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:])) if n else 0.0
    out = {"n": n, "mean_r": round(float(rs.mean()), 4), "win_rate": round(float(len(wins) / n), 4),
           "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if len(losses) and losses.sum() < 0 else None,
           "sum_r": round(float(rs.sum()), 2), "max_drawdown_r": round(dd, 2), "ci95": None}
    rng = np.random.default_rng(seed)
    if days is not None and n >= 5:
        _, inv = np.unique(days, return_inverse=True)
        sums, counts = np.bincount(inv, weights=rs), np.bincount(inv).astype(float)
        d = len(sums)
        out["days"] = int(d)
        if d >= 5:
            idx = rng.integers(0, d, size=(iters, d))
            means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
            out["ci95"] = [round(float(np.quantile(means, 0.025)), 4), round(float(np.quantile(means, 0.975)), 4)]
        return out
    if n > 4000:                                    # büyük örneklem: normal yaklaşım (bootstrap belleği n×iters)
        se = float(rs.std(ddof=1)) / math.sqrt(n)
        out["ci95"] = [round(float(rs.mean()) - 1.96 * se, 4), round(float(rs.mean()) + 1.96 * se, 4)]
    elif n >= 5:
        means = rs[rng.integers(0, n, size=(iters, n))].mean(axis=1)
        out["ci95"] = [round(float(np.quantile(means, 0.025)), 4), round(float(np.quantile(means, 0.975)), 4)]
    return out


def _ci_lo(st: dict) -> float:
    return st["ci95"][0] if st.get("ci95") else float("-inf")


def replicated(is_st: dict, oos_st: dict, cfg: LabConfig) -> bool:
    """İki dönemde de yeterli işlem ve %95 aralığın alt ucu 0'ın üstünde (keşifte şans, doğrulamada şans değil)."""
    return (is_st.get("n", 0) >= cfg.min_is and oos_st.get("n", 0) >= cfg.min_oos
            and _ci_lo(is_st) > 0 and _ci_lo(oos_st) > 0)


def verdict(is_st: dict, oos_st: dict, cfg: LabConfig, vs_placebo: dict | None = None) -> str:
    """GÜÇLÜ ADAY: iki dönemde de aralık 0'ın üstünde VE aynı dilim/yön/bağlamdaki rastgele girişi iki dönemde de geçiyor
    (yalnız "yükselen piyasada long" olmasın). ZAYIF İZ: iki dönemde ortalama pozitif ama bu şartlardan biri eksik."""
    if is_st.get("n", 0) < cfg.min_is or oos_st.get("n", 0) < cfg.min_oos:
        return V_THIN
    if oos_st.get("ci95") and oos_st["ci95"][1] < 0:
        return V_LOSS
    if is_st["mean_r"] > 0 and oos_st["mean_r"] > 0:
        beats = bool(vs_placebo) and vs_placebo.get("IS", -1) > 0 and vs_placebo.get("OOS", -1) > 0
        if replicated(is_st, oos_st, cfg) and beats:
            return V_STRONG
        if replicated(is_st, oos_st, cfg) or _ci_lo(oos_st) > 0 or (oos_st.get("profit_factor") or 0) > 1.1:
            return V_WEAK
    return V_NONE


def aggregate(events: list[dict], cfg: LabConfig) -> dict[str, Any]:
    if not events:
        return {"groups": [], "tested": 0}
    ev = pd.DataFrame(events)
    if "cost_r" not in ev:
        ev["cost_r"] = 0.0
    cut = {}
    for tf, g in ev.groupby("tf"):
        lo_t, hi_t = int(g["t_ms"].min()), int(g["t_ms"].max())
        cut[tf] = lo_t + cfg.split * (hi_t - lo_t)
    ev["period"] = ["IS" if t <= cut[tf] else "OOS" for t, tf in zip(ev["t_ms"], ev["tf"])]
    ev["day"] = ev["t_ms"] // 86_400_000
    ctx = pd.json_normalize(ev["ctx"]).add_prefix("ctx.")
    ev = pd.concat([ev.drop(columns=["ctx"]).reset_index(drop=True), ctx.reset_index(drop=True)], axis=1)
    dims = [c for c in ev.columns if c.startswith("ctx.")]
    groups = []
    keys = ["tf", "family", "name", "side"]
    for key, g in ev.groupby(keys):
        slices = [("HEPSİ", "HEPSİ", g)] + [(d[4:], b, gg) for d in dims for b, gg in g.groupby(d) if b != "bilinmiyor"]
        for dim, bucket, gg in slices:
            is_m, oos_m = gg["period"] == "IS", gg["period"] == "OOS"
            if len(gg) < 10:
                continue
            is_st = r_stats(gg.loc[is_m, "r"].to_numpy(dtype=float), cfg.bootstrap_iters, days=gg.loc[is_m, "day"].to_numpy())
            oos_st = r_stats(gg.loc[oos_m, "r"].to_numpy(dtype=float), cfg.bootstrap_iters, days=gg.loc[oos_m, "day"].to_numpy())
            for st, m in ((is_st, is_m), (oos_st, oos_m)):
                if st.get("n"):
                    st["cost_r"] = round(float(gg.loc[m, "cost_r"].mean()), 4)
            groups.append({**dict(zip(keys, key)), "context": dim, "bucket": bucket, "IS": is_st, "OOS": oos_st,
                           "symbols": int(gg["symbol"].nunique())})
    # aynı dilim/yön/bağlamdaki PLASEBO (rastgele an + rastgele yön) ile karşılaştırma
    plac = {(g["name"], g["tf"], g["side"], g["context"], g["bucket"]): g for g in groups if g["family"] == "placebo"}
    for g in groups:
        k = (g["tf"], g["side"], g["context"], g["bucket"])
        # algoritma → aynı çıkış kuralını kullanan kendi eşi; formasyon → genel rastgele giriş
        pg = plac.get(("PLACEBO_" + g["name"],) + k) or plac.get(("PLACEBO_RANDOM",) + k)
        vs = None
        if g["family"] != "placebo" and pg and pg["IS"].get("n", 0) >= cfg.min_oos and pg["OOS"].get("n", 0) >= cfg.min_oos \
                and g["IS"].get("n") and g["OOS"].get("n"):
            vs = {"IS": round(g["IS"]["mean_r"] - pg["IS"]["mean_r"], 4), "OOS": round(g["OOS"]["mean_r"] - pg["OOS"]["mean_r"], 4),
                  "placebo_mean_r": [pg["IS"]["mean_r"], pg["OOS"]["mean_r"]], "placebo_n": [pg["IS"]["n"], pg["OOS"]["n"]]}
        g["vs_placebo"] = vs
        g["replicated"] = replicated(g["IS"], g["OOS"], cfg)
        g["verdict"] = verdict(g["IS"], g["OOS"], cfg, vs)
    judged = [g for g in groups if g["verdict"] != V_THIN]
    real = [g for g in judged if g["family"] != "placebo"]
    pl = [g for g in judged if g["family"] == "placebo"]
    rate = lambda gs: round(sum(1 for g in gs if g["replicated"] and g["IS"]["mean_r"] > 0) / len(gs), 4) if gs else None  # noqa: E731
    tf_summary = {}
    for tf, g in ev.groupby("tf"):
        r_ = g[g["family"] != "placebo"]
        p_ = g[g["family"] == "placebo"]
        tf_summary[tf] = {"trades": int(len(r_)), "mean_r": round(float(r_["r"].mean()), 4) if len(r_) else None,
                          "cost_r": round(float(r_["cost_r"].mean()), 4) if len(r_) else None,
                          "placebo_mean_r": round(float(p_["r"].mean()), 4) if len(p_) else None}
    return {"groups": groups, "tested": len(real),
            "candidate_rate": {"real": rate(real), "placebo": rate(pl), "placebo_tested": len(pl)},
            "tf_summary": tf_summary, "cutoff_ms": {k: int(v) for k, v in cut.items()}, "events": int(len(ev))}


# ---------------------------------------------------------------------------- çalıştırma
def run(*, symbols: list[str], tfs: list[str], cache_dir: Path, out_dir: Path, cfg: LabConfig, provider_factory,
        days: dict[str, int] | None = None, jobs: int = 1, catalog: bool = True, now_ms: int | None = None,
        log: Callable[[str], None] = print, algos: bool = True) -> dict[str, Any]:
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    bad = [tf for tf in tfs if tf not in SUPPORTED_TIMEFRAMES]
    if bad:
        raise ValueError(f"desteklenmeyen zaman dilimi: {', '.join(bad)} — ortak analiz yalnız {', '.join(SUPPORTED_TIMEFRAMES)} okur")
    days = {**DEFAULT_DAYS, **(days or {})}
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    fails, failed = 0, []
    for s in symbols:                                       # indirme sırayla (hız sınırı), işlem paralel
        for tf in tfs:
            try:
                df = load_series(s, tf, days=days[tf], cache_dir=cache_dir, provider_factory=provider_factory, now_ms=now_ms)
                log(f"veri {s} {tf}: {len(df)} bar")
                fails = 0
            except Exception as exc:  # noqa: BLE001 — bir sembolün verisi diğerlerini durdurmaz
                log(f"veri {s} {tf}: HATA {type(exc).__name__}: {str(exc)[:160]}")
                failed.append(f"{s} {tf}")
                fails += 1
                if fails >= 3:                              # bağlantı yok: eksik veriyle SESSİZCE test etme
                    raise DownloadAborted(f"Binance'ten art arda {fails} seri indirilemedi ({', '.join(failed[-3:])}). "
                                          "Bağlantı ya da Binance tarafında geçici kısıtlama olabilir; 10-15 dk sonra "
                                          "tekrar deneyin (inen veri önbellekte kalır). Test ÇALIŞTIRILMADI.")
    tasks = [(str(cache_dir), s, tf, days[tf], now_ms, asdict(cfg), catalog, algos) for s in symbols for tf in tfs]
    events, metas = [], []
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(_task, t): t for t in tasks}
            for f in as_completed(futs):
                t = futs[f]
                try:
                    evs, meta = f.result()
                except Exception as exc:  # noqa: BLE001
                    evs, meta = [], {"symbol": t[1], "tf": t[2], "error": f"{type(exc).__name__}: {exc}"}
                events += evs
                metas.append(meta)
                log(f"tarandı {meta['symbol']} {meta['tf']}: {meta.get('trades', 0)} işlem ({time.time() - t0:.0f} sn)")
    else:
        for t in tasks:
            evs, meta = _task(t)
            events += evs
            metas.append(meta)
            log(f"tarandı {meta['symbol']} {meta['tf']}: {meta.get('trades', 0)} işlem ({time.time() - t0:.0f} sn)")
    if algos and "1d" in tfs:                         # coinler arası momentum: bütün coinlerin günlük serisi birlikte
        frames = {s: load_series(s, "1d", days=days["1d"], cache_dir=cache_dir, provider_factory=None, now_ms=now_ms) for s in symbols}
        xs = xsmom_events(frames, cfg)
        events += [asdict(e) for e in xs]
        log(f"coinler arası momentum: {sum(1 for e in xs if e.family == 'algo')} işlem")
    agg = aggregate(events, cfg)
    warnings = [f"{m['symbol']} {m['tf']}: {w}" for m in metas
                if (w := coverage_problem(m, days[m["tf"]], m["tf"], cache_dir, now_ms))]
    with gzip.open(out_dir / "signal_lab_events.csv.gz", "wt", newline="", encoding="utf-8") as fh:
        cols = ["symbol", "tf", "family", "name", "side", "t_ms", "r", "cost_r", "exit_reason", "hold", "ctx"]
        w = csv.writer(fh)
        w.writerow(cols)
        for e in events:
            w.writerow([e[c] if c != "ctx" else json.dumps(e["ctx"], ensure_ascii=False) for c in cols])
    report = {"kind": "SIGNAL_LAB", "config": asdict(cfg), "cost_round_trip_pct": round(2 * cfg.cost_per_side * 100, 3),
              "symbols": symbols, "timeframes": tfs, "days": {tf: days[tf] for tf in tfs}, "series": metas,
              "seconds": round(time.time() - t0, 1), "data_warnings": warnings, **agg,
              "note_tr": "Geçmiş test (PAPER değil, canlı değil). GÜÇLÜ ADAY = iki dönemde de %95 aralık 0'ın üstünde ve aynı "
                         "bağlamdaki rastgele girişi iki dönemde de geçiyor. Kâr garantisi değildir."}
    (out_dir / "signal_lab_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def render(report: dict[str, Any], *, top: int = 25) -> str:
    g = report.get("groups") or []
    fmt = lambda st: (f"n={st['n']:<4} ort.R={st['mean_r']:+.3f} kazanma={st['win_rate'] * 100:3.0f}%"  # noqa: E731
                      + (f" %95[{st['ci95'][0]:+.2f},{st['ci95'][1]:+.2f}]" if st.get("ci95") else "")) if st.get("n") else "n=0"
    lines = [f"SİNYAL LABORATUVARI · {len(report.get('symbols') or [])} coin · {', '.join(report.get('timeframes') or [])} · "
             f"{report.get('events', 0)} işlem · gidiş-dönüş maliyet %{report.get('cost_round_trip_pct')} · {report.get('seconds')} sn"]
    if report.get("data_warnings"):
        lines.append(f"UYARI — eksik veri ({len(report['data_warnings'])} seri): " + "; ".join(report["data_warnings"][:10]))
    lines.append("\n== ZAMAN DİLİMİ ÖZETİ (bütün gerçek sinyaller) ==")
    lines.append(f"{'dilim':>5} {'işlem':>8} {'ort.R':>8} {'maliyet(R)':>11} {'rastgele ort.R':>15}")
    for tf, t in (report.get("tf_summary") or {}).items():
        f3 = lambda x: "—" if x is None else f"{x:+.3f}"  # noqa: E731
        lines.append(f"{tf:>5} {t['trades']:>8} {f3(t['mean_r']):>8} {f3(t['cost_r']):>11} {f3(t['placebo_mean_r']):>15}")
    cr = report.get("candidate_rate") or {}
    pct = lambda x: "—" if x is None else f"%{x * 100:.1f}"  # noqa: E731
    lines.append(f"\nİki dönemde de sıfırın üstünde kalan kombinasyon oranı: gerçek sinyaller {pct(cr.get('real'))} · RASTGELE "
                 f"{pct(cr.get('placebo'))} (hükme giren: {report.get('tested', 0)} gerçek, {cr.get('placebo_tested', 0)} rastgele)")
    real = [x for x in g if x["family"] != "placebo"]
    for v in (V_STRONG, V_WEAK):
        rows = sorted([x for x in real if x["verdict"] == v], key=lambda x: -x["OOS"]["mean_r"])[:top]
        lines.append(f"\n== {v} ({sum(1 for x in real if x['verdict'] == v)}) ==")
        for x in rows:
            vs = x.get("vs_placebo")
            vtxt = f" · rastgeleye göre {vs['IS']:+.2f}/{vs['OOS']:+.2f}" if vs else " · rastgele karşılaştırması yok"
            lines.append(f"{x['tf']:>4} {x['name']:<26}{x['side']:<6}{x['context'] + '=' + str(x['bucket']):<24} "
                         f"keşif {fmt(x['IS'])} | doğrulama {fmt(x['OOS'])}{vtxt} · {x['symbols']} coin")
    algo_rows = sorted([x for x in real if x["family"] == "algo" and x["context"] == "HEPSİ"], key=lambda x: (x["name"], x["tf"], x["side"]))
    if algo_rows:
        lines.append("\n== ALGORİTMALAR (bağlamsız; eşi = aynı çıkış kuralıyla rastgele giriş) ==")
        for x in algo_rows:
            vs = x.get("vs_placebo")
            vtxt = f" · eşine göre {vs['IS']:+.2f}/{vs['OOS']:+.2f}" if vs else " · eş karşılaştırması yok"
            lines.append(f"{x['tf']:>4} {x['name']:<22}{x['side']:<6} keşif {fmt(x['IS'])} | doğrulama {fmt(x['OOS'])}{vtxt} · {x['verdict']}")
    base = [x for x in real if x["context"] == "HEPSİ" and x["verdict"] != V_THIN]
    loss = sorted([x for x in base if x["verdict"] == V_LOSS], key=lambda x: x["OOS"]["mean_r"])[:15]
    lines.append(f"\n== {V_LOSS} (bağlamsız, en kötü 15) ==")
    for x in loss:
        lines.append(f"{x['tf']:>4} {x['name']:<26}{x['side']:<6} doğrulama {fmt(x['OOS'])} · maliyet {x['OOS'].get('cost_r', 0):.2f}R")
    lines.append(f"\nNot: {V_STRONG} = iki dönemde de %95 aralık 0'ın üstünde VE aynı bağlamdaki rastgele girişi iki dönemde de "
                 f"geçiyor. {V_WEAK} tek başına güvenilmez. Geçmiş test; kâr garantisi değildir.")
    return "\n".join(lines)
