# Trading Bot — çok-coinli analiz · karar · sinyal motoru (Obsidian entegrasyonlu)

> **Ne yapar:** 10 coin için ayrı ayrı analiz yapar, her coin için binlerce strateji varyasyonunu
> backtest'ten geçirir (in-sample / out-of-sample), portföy bağlamında **AL / SAT / TUT / BEKLE**
> kararı verir, kağıt (paper) portföyü günceller ve her şeyi **Obsidian**'a şema + not olarak yazar.
>
> **Ne yapmaz:** Gerçek borsa emri göndermez, API key istemez. Tetik insanda kalır
> ("keep AI on analysis — the trigger stays human"). Kâr garantisi yoktur; geçmiş performans
> gelecek getiriyi garanti etmez.

## Mimari (Obsidian şemasıyla birebir)

```
 ┌──────────── ANALİZ (10 yuvarlak) ────────────┐
 │ BTC  ETH  SOL  BNB  XRP  ADA  DOGE AVAX LINK DOT │      ┌─────────────┐      ┌───────────────┐
 │ gösterge snapshot · rejim · strateji taraması    │ ──►  │ KARAR MOTORU │ ──►  │ AL/SAT SİNYALİ │ ──► Kağıt portföy
 │ IS/OOS backtest · skor 0-100 · BUY/SELL/HOLD/WATCH│      │ risk · boyut │      │ + Obsidian log │
 └──────────────────────────────────────────────────┘      └─────────────┘      └───────────────┘
```

| Katman | Dosya | Görev |
|---|---|---|
| Veri | `tradingbot/data.py` | ccxt ile public OHLCV (binance → bybit → okx → kucoin fallback), CSV önbellek, açık barı düşürme |
| Göstergeler | `tradingbot/indicators.py` | EMA/SMA/RSI/ATR/ADX/Bollinger/Donchian (saf pandas) |
| Stratejiler | `tradingbot/strategies.py` | 4 aile: RSI ortalamaya dönüş, EMA trend, Donchian kırılım, Bollinger dönüş — parametre ızgaraları |
| Backtest | `tradingbot/backtest.py` | Bar-bar, sonraki bar açılışında giriş (look-ahead yok), komisyon+kayma, ATR iz süren stop, risk bazlı boyut; Sharpe/MaxDD/WinRate/PF/CAGR/Buy&Hold |
| Tarama | `tradingbot/sweep.py` | Her konfigürasyonu %70 in-sample / %30 out-of-sample test eder, edge kuralını uygular |
| Analiz düğümü | `tradingbot/analyzer.py` | Coin başına `CoinAnalysis` (snapshot, rejim, en iyi strateji, sinyal, skor) |
| Karar düğümü | `tradingbot/decision.py` | Portföy bağlamında AL/SAT/TUT/BEKLE, boyutlama, stop, BTC rejim filtresi, max pozisyon |
| Sinyal düğümü | `tradingbot/signals.py` | Kağıt işlem, `state/signals.json`, `signals_log.jsonl`, konsol tablosu |
| Obsidian | `tradingbot/obsidian.py` | Canvas şeması, Mermaid dashboard, coin/sinyal/backtest/portföy notları |
| CLI | `tradingbot/cli.py` | `run / analyze / sweep / fetch / obsidian / reset-portfolio` |

## Kurulum

```bash
pip install -r requirements.txt
```

`config.yaml` içinde coin listesi, zaman dilimi, risk ve Obsidian vault yolu ayarlanır.
Vault yolu varsayılan olarak Obsidian'da açtığın kasa: `C:/Users/berke/Trading bot/Trading_bot`.

## Kullanım

```bash
python -m tradingbot run                    # tek döngü (≈30 sn): veri → analiz → karar → kağıt işlem → Obsidian
python -m tradingbot run --loop 240         # her 4 saatte bir tekrar (bar kapanışları)
python -m tradingbot analyze --symbols BTC/USDT SOL/USDT
python -m tradingbot sweep --symbols SOL/USDT --top 20 --families ema_trend donchian
python -m tradingbot fetch                  # sadece veri önbelleği
python -m tradingbot obsidian               # son rapordan Obsidian'ı yeniden yaz (ağ gerekmez)
python -m tradingbot reset-portfolio        # kağıt portföyü sıfırla (eskisi yedeklenir)
```

Windows: `scripts\run_bot.bat` (tek sefer) · `scripts\run_bot_loop.bat` (sürekli).
Görev Zamanlayıcı ile `run_bot.bat`'ı 4 saatte bir (örn. 00:02, 04:02, 08:02 … UTC+3 için 03:02, 07:02, …) çalıştırabilirsin.

## Obsidian'da ne görürsün

Kasa: `Trading_bot/`

- **`Trading Bot Şeması.canvas`** — sol sütunda 10 coin analiz düğümü (renk = karar: 🟢 AL, 🔴 SAT, 🟡 TUT, ⚪ BEKLE),
  ortada **Karar Motoru**, sağda **AL/SAT Sinyali**, üstte **Piyasa Rejimi**, altta **Portföy** ve **Backtest Sıralaması**.
  Oklar coin → karar → sinyal → portföy akışını gösterir; her ok üzerinde karar + güven yazar. Her düğüm ilgili nota bağlıdır.
- **`Dashboard.md`** — Mermaid ile gerçek yuvarlaklı akış şeması + coin özet tablosu + portföy.
- **`Coins/<COIN>.md`** — anlık göstergeler, karar gerekçeleri, en iyi strateji, IS/OOS/tüm-veri metrik tablosu, taramanın ilk 5 konfigürasyonu.
- **`Signals/`** — her çalışmanın karar tablosu (`Son Sinyal.md` her zaman en güncel).
- **`Backtests/Sweep.md`** — coinlerin en iyi stratejilerinin OOS Sharpe'a göre sıralaması, buy&hold kıyası (slayt-4 tarzı).
- **`Portfolio.md`** — kağıt portföy: açık pozisyonlar, stoplar, kapanan işlemler.

## Karar mantığı (özet)

1. Her coin için 46 strateji konfigürasyonu backtest edilir; **in-sample Sharpe**'a göre en iyisi seçilir
   (min. 15 işlem şartı).
2. Seçilen strateji **hiç görmediği son %30 veride** (OOS) test edilir. `min_oos_sharpe` (0.30) ve
   `min_oos_profit_factor` (1.10) eşiklerini geçmezse coin **edge yok → BEKLE**.
3. Edge'i olan coinde son kapanan barda giriş sinyali varsa **AL**; boyut = `equity × risk% / (ATR × stop çarpanı)`,
   üst sınır `max_position_pct`, `max_open_positions` ve nakit.
4. Pozisyon varken: stop'a değdiyse / strateji çıkış verdiyse / strateji artık FLAT ise **SAT**, aksi halde **TUT** (iz süren stop yukarı çekilir).
5. BTC rejimi **DÜŞÜŞ** ise altcoin AL boyutu %50, güven −10.
6. Skor 0-100 = edge kalitesi (45) + rejim (25) + RSI konumu (20) + volatilite (10). `min_confidence_to_buy` altı → BEKLE.

Bu eşikler kasıtlı olarak **muhafazakâr**dır: ayı piyasasında bot çoğunlukla "BEKLE" der ve sermayeyi korur.
Daha agresif olmak için `config.yaml → backtest.min_oos_sharpe / min_oos_profit_factor` düşürülebilir
(overfit riski artar).

## Test

```bash
python -m pytest tests -q
```
Testler ağ kullanmaz (sentetik OHLCV): göstergeler, look-ahead kontrolü, stop mekanizması, karar/portföy akışı, canvas JSON geçerliliği.

## Dosya yapısı

```
Trading bot/
├─ config.yaml            ayarlar
├─ requirements.txt
├─ tradingbot/            paket (yukarıdaki katmanlar)
├─ tests/test_bot.py
├─ scripts/run_bot.bat, run_bot_loop.bat
├─ data/                  OHLCV önbelleği (git dışı)
├─ state/                 portfolio.json, signals.json, signals_log.jsonl (git dışı)
└─ Trading_bot/           Obsidian kasası (bot buraya yazar)
```
