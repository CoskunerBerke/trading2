"""TradingView veri akışına doğrudan bağlantı (websocket).

TradingView'in grafiklerinin kullandığı gerçek zamanlı veri protokolüne bağlanır ve
`BINANCE:BTCUSDT` gibi bir sembolün OHLCV mumlarını çeker. Giriş (login) gerekmez;
anonim oturum kullanılır. Yalnızca OKUMA yapar.

Protokol özeti:
  ~m~<len>~m~{"m":"<method>","p":[...]}   → çerçeve formatı
  ~h~<n>                                   → heartbeat (aynen geri gönderilir)
  timescale_update / du                    → mum verisi ("s":[{"i":0,"v":[ts,o,h,l,c,vol]},...])
  series_completed                         → seri tamamlandı
"""
from __future__ import annotations

import json
import logging
import random
import re
import string
import time

import pandas as pd

log = logging.getLogger(__name__)

WS_URL = "wss://data.tradingview.com/socket.io/websocket"
ORIGIN = "https://data.tradingview.com"

# ccxt zaman dilimi → TradingView çözünürlüğü
TV_INTERVAL = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240",
               "6h": "360", "12h": "720", "1d": "1D", "1w": "1W"}


def tv_symbol(exchange: str, symbol: str) -> str:
    """'BTC/USDT' + 'binance' → 'BINANCE:BTCUSDT'"""
    return f"{exchange.upper()}:{symbol.replace('/', '')}"


def _rand(prefix: str) -> str:
    return prefix + "_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _frame(method: str, params: list) -> str:
    body = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    return f"~m~{len(body)}~m~{body}"


_FRAME_RE = re.compile(r"~m~(\d+)~m~")


def _split_frames(raw: str) -> list[str]:
    out, pos = [], 0
    while True:
        m = _FRAME_RE.match(raw, pos)
        if not m:
            break
        length = int(m.group(1))
        start = m.end()
        out.append(raw[start:start + length])
        pos = start + length
    return out


class TradingViewFeed:
    """Anonim TradingView websocket istemcisi (yalnızca veri okuma)."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def fetch_ohlcv(self, symbol: str, interval: str, n_bars: int = 5000) -> pd.DataFrame:
        import websocket  # websocket-client

        ws = websocket.create_connection(WS_URL, header={"Origin": ORIGIN}, timeout=self.timeout)
        chart, quote = _rand("cs"), _rand("qs")
        try:
            def send(method, params):
                ws.send(_frame(method, params))

            send("set_auth_token", ["unauthorized_user_token"])
            send("chart_create_session", [chart, ""])
            send("resolve_symbol", [chart, "symbol_1",
                                    "=" + json.dumps({"symbol": symbol, "adjustment": "splits", "session": "regular"}, separators=(",", ":"))])
            send("create_series", [chart, "s1", "s1", "symbol_1", interval, n_bars])
            send("switch_timezone", [chart, "exchange"])

            bars: dict[int, list] = {}
            deadline = time.time() + max(self.timeout, 30)
            done = False
            while not done and time.time() < deadline:
                raw = ws.recv()
                if not raw:
                    continue
                for frame in _split_frames(raw):
                    if frame.startswith("~h~"):
                        ws.send(f"~m~{len(frame)}~m~{frame}")  # heartbeat cevabı
                        continue
                    if not frame.startswith("{"):
                        continue
                    try:
                        msg = json.loads(frame)
                    except json.JSONDecodeError:
                        continue
                    m = msg.get("m")
                    if m in ("timescale_update", "du"):
                        for payload in msg.get("p", [])[1:]:
                            if not isinstance(payload, dict):
                                continue
                            series = payload.get("s1") or {}
                            for item in series.get("s", []) or []:
                                v = item.get("v")
                                if v and len(v) >= 5:
                                    bars[int(v[0])] = v
                    elif m == "series_completed":
                        done = True
                    elif m in ("symbol_error", "series_error", "critical_error", "protocol_error"):
                        raise RuntimeError(f"TradingView hata: {msg}")
            if not bars:
                raise RuntimeError(f"TradingView'den {symbol} için veri gelmedi")
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

        rows = []
        for ts in sorted(bars):
            v = bars[ts]
            vol = float(v[5]) if len(v) > 5 and v[5] is not None else 0.0
            rows.append([ts * 1000, float(v[1]), float(v[2]), float(v[3]), float(v[4]), vol])
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        log.info("TradingView %s %s: %d bar", symbol, interval, len(df))
        return df
