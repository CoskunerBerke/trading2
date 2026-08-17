"""Trading Bot — çok-coinli analiz, karar ve sinyal motoru (Obsidian entegrasyonlu).

Katmanlar:
  data        -> borsadan OHLCV çeker/önbellekler (ccxt, public)
  indicators  -> RSI / EMA / ATR / ADX / Bollinger / Donchian
  strategies  -> parametrik strateji aileleri (long-only, spot)
  backtest    -> komisyon+kayma+ATR stop'lu backtester, in-sample / out-of-sample metrikler
  sweep       -> parametre taraması, en iyi konfigürasyonu seçer
  analyzer    -> coin başına analiz düğümü ("yuvarlak")
  decision    -> portföy seviyesinde karar düğümü
  signals     -> AL/SAT çıktısı + kağıt portföy
  obsidian    -> Canvas şeması + notlar
"""

__version__ = "1.0.0"
