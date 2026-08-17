# Trading Bot — çok-coinli analiz · karar · sinyal motoru (Obsidian entegrasyonlu)

> **Ne yapar:** Mumları doğrudan **TradingView veri akışından** (`BINANCE:BTCUSDT` …) çeker, 10 coin için
> ayrı ayrı analiz yapar, her coin için 64 strateji konfigürasyonunu **walk-forward** backtest'ten geçirir,
> portföy bağlamında **AL / SAT / TUT / BEKLE** kararı verir, **Binance'in gerçek emir kurallarıyla**
> (min 5 USDT, lot adımı, %0.10 komisyon) 50 USDT'lik kağıt (paper) portföyü günceller ve her şeyi
> **Obsidian**'a şema + not olarak yazar.
>
> **Ne yapmaz:** Gerçek borsa emri göndermez, API key istemez. Tetik insanda kalır
> ("keep AI on analysis — the trigger stays human"). Kâr garantisi yoktur; geçmiş performans
> gelecek getiriyi garanti etmez.

## AI Trader döngüsü (7/24): TARA → ANALİZ → SETUP → RİSK → KAĞIT İŞLEM → İZLE → ÖĞREN

| Adım | Modül | Ne olur |
|---|---|---|
| 01 TARA | `scanner.py` | **Tüm Binance USDT perpetual evreni** (24s hacim ≥ 20M, ~110-160 sembol) taranır; her sembol için Trend/Momentum/Hacim/Tetikleyici/Risk (25'er puan) → 0-100 **AI skoru**, long & short ayrı; huni: evren → tarandı → işaretlendi (≥60) → **setup** (ilk 12) |
| 02 ANALİZ | `agents/` | Setup'lar + çekirdek coinler için 8 uzman ajan (TradingView 1d/4h/1h + Binance canlı) |
| 03 SETUP | `agents/manager.py` | Coin yöneticisi: LONG/SHORT/BEKLE, tetik ("4h mum X üstünde kapanırsa"), giriş, stop, TP1/TP2 (≥1.5R), kaldıraç tavanı, marj |
| 04 RİSK | `agents/manager.py` (Chief) + `engine.py` | Baş yönetici risk modu; R/R < 1.5 → plan geçersiz; kaldıraç volatiliteye göre; kovalama sınırı %1.5; öğrenen model P(kazanç) < eşik → işlem yok; kara listedeki setup tipleri atlanır |
| 05 KAĞIT İŞLEM | `paper_futures.py` | 50 USDT kağıt futures defteri: tetik gerçekleşince pozisyon (komisyon %0.05 + kayma + funding), TP1'de yarı kapat + stop başa-baş, likidasyon yaklaşık |
| 06 İZLE | `engine.py` | Her tur canlı fiyatla stop/TP kontrolü, MAE/MFE takibi, alarmlar |
| 07 ÖĞREN | `learning.py` | Her kapanan işlem: giriş anındaki 8 ajan bias/güveni + piyasa özellikleri × sonuç → **hangi ajan haklıydı**, "neden kâr/zarar" dersleri; online lojistik regresyon (P(kazanç)); ajan isabet oranlarından **uyarlanır ağırlıklar**; setup×yön beklentisi negatifse **kara liste**; ≥20 işlemden sonra yönetici öğrenilen ağırlıkları kullanır |
| Görsel | `charts.py` | Her setup/pozisyon için TradingView tarzı PNG: mumlar, EMA20/50/200, ZigZag dalga etiketleri (1)…(5), S/R, GİRİŞ çizgisi, kırmızı STOP kutusu, yeşil TP1/TP2 kutuları |

Komutlar: `python -m tradingbot scan` (sadece tarama) · `python -m tradingbot tour` (tek tur) · `python -m tradingbot watch --interval 15 --scan-every 2` (7/24) · `scripts\watch_7_24.bat`.
Obsidian: `Scanner.md` (huni + setup tablosu), `Paper Futures.md` (açık/kapanan pozisyonlar + görseller), `Learning/Öğrenme.md` (isabet oranları, öğrenilen ağırlıklar, setup istatistikleri, model özellikleri), `Learning/Dersler.md` (her işlemin NEDEN analizi), `Charts/*.png`, `Agents/<COIN>.canvas` (görsel düğümü dahil).

> ⚠️ Gerçek para/API bağlanmaz. Bu sistem kağıt işlemle **kanıt biriktirir**; gerçek paraya geçiş kararı ve anahtar bağlama kullanıcıya aittir ve en az yüzlerce kağıt işlemde pozitif beklenti görmeden önerilmez.

## Çoklu ajan katmanı (coin başına uzmanlar → yönetici → baş yönetici)

Her coin için 8 uzman ajan aynı anda çalışır ve o coinin **yönetici ajanına** rapor verir; yönetici futures/kaldıraç
planı çıkarır; **Baş Yönetici** tüm coinleri BTC rejimine göre sıralar. Sistem `watch` ile 7/24 döner.

| Ajan | Baktığı şey | Kaynak |
|---|---|---|
| 🌡️ Volatilite | günlük/4h ATR, ATR yüzdeliği, Bollinger genişliği, 30g gerçekleşen vol → **max kaldıraç** ve stop mesafesi | TradingView 1d/4h |
| 📈 Trend & EMA çizgileri | 1d/4h/1h EMA 20/50/100/200 dizilimi, eğim, ADX; TradingView "Key facts" tarzı yakın destek/direnç EMA | TradingView 1d/4h/1h |
| 🕯️ Mum yapısı | son mumun aralıkta nerede kapandığı, gövde/fitil oranı, yutan/çekiç/kayan yıldız/doji/iç bar, HH-HL / LH-LL yapısı | 1d/4h |
| 📊 Hacim | hacim/20-bar ort., alım-satım hacmi oranı, OBV eğimi, hareket hacimle onaylı mı | 1d/4h |
| 🧱 Destek/Direnç | swing yüksek/alçaklar (kümelenmiş), Donchian, 20g H/L; "X üstünde kapanış → hedef Y" | 1d/4h |
| 🚀 Momentum | RSI 1d/4h/1h, MACD histogram, ROC, basit uyumsuzluk | 1d/4h/1h |
| 🧪 Backtest/Edge | walk-forward sonucu (edge var mı, strateji LONG/FLAT mı) | son `run` |
| 📡 Binance canlı | 24s istatistik, emir defteri dengesi (ilk 20 kademe), funding, open interest, long/short hesap oranı | Binance public |

**Coin yöneticisi** ağırlıklı skoru (−1…+1) → **LONG / SHORT / BEKLE** + kanaat %, futures planı (tetik: "4h mum X üstünde/altında
kapanırsa", giriş, stop, hedef1/2, R/R ≥ 1.5 şartı, max kaldıraç, marj/notional/risk USDT), **YAP / YAPMA / EĞER…İSE** listeleri.
**Baş Yönetici**: RISK-ON / NÖTR / RISK-OFF modu, coin sıralaması, portföy kuralları. Karar değişiklikleri `Agents/Alarmlar.md` + `state/alerts.log`.

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
| Veri | `tradingbot/tradingview.py`, `data.py` | **TradingView websocket** (anonim, yalnızca okuma) → BINANCE mumları; düşerse ccxt zinciri (binance→bybit→okx→kucoin); CSV önbellek, açık barı düşürme |
| Borsa kuralları | `tradingbot/exchange_rules.py` | Binance market filtreleri (min emir tutarı, adet adımı, fiyat adımı, komisyon) — kağıt işlemler bunlara uyar |
| Göstergeler | `tradingbot/indicators.py` | EMA/SMA/RSI/ATR/ADX/Bollinger/Donchian (saf pandas) |
| Stratejiler | `tradingbot/strategies.py` | 6 aile: RSI(2) trend-içi geri çekilme, EMA geri çekilme, RSI ortalamaya dönüş, EMA trend, Donchian kırılım, Bollinger dönüş — 64 konfigürasyon |
| Backtest | `tradingbot/backtest.py` | Bar-bar, sonraki bar açılışında giriş (look-ahead yok), komisyon+kayma, ATR iz süren stop, risk bazlı boyut; Sharpe/MaxDD/WinRate/PF/CAGR/Buy&Hold |
| Tarama | `tradingbot/sweep.py` | **Walk-forward** (anchored, 4 adım): her adımda o ana kadarki veriyle en iyi konfigürasyon seçilir, sonraki görülmemiş dönemde test edilir; OOS dönemleri birleştirilir → edge kuralı |
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
python -m tradingbot agents                 # yalnızca ajan katmanı (≈1 dk): coin yöneticileri + baş yönetici → Obsidian Agents/
python -m tradingbot watch --interval 15    # 7/24: her 15 dk ajanlar, her 4h bar kapanışında tam döngü
python -m tradingbot obsidian               # son rapordan Obsidian'ı yeniden yaz (ağ gerekmez)
python -m tradingbot reset-portfolio        # kağıt portföyü sıfırla (eskisi yedeklenir)
```

Windows: `scripts\run_bot.bat` (tek sefer) · `scripts\run_bot_loop.bat` (4h döngü) · **`scripts\watch_7_24.bat` (7/24 ajan izleme; çökerse kendini yeniden başlatır)**.
Bilgisayar açık kaldığı sürece çalışır; kapanınca durur (VPS'e taşınabilir).
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
- **`Agents/<COIN>.canvas`** — coin başına ajan şeması: solda 8 uzman ajan (renk = ajanın yönü), ortada **COIN YÖNETİCİSİ**, sağda **FUTURES PLANI**, **YAPMA**, **EĞER…İSE**, altta kilit seviyeler.
- **`Agents/<COIN>.md`** — tüm ajan bulguları/uyarıları/metrikleri + yönetici brifingi. **`Agents/Baş Yönetici.md`**, **`Agents/Alarmlar.md`**.
- Ana şemadaki her coin düğümünde yöneticinin kararı ve ajan şemasına bağlantı bulunur.

## Karar mantığı (özet)

1. Her coin için 64 strateji konfigürasyonu **walk-forward** test edilir: ilk %45 veriyle en iyi seçilir →
   sonraki dilimde test; pencere genişletilerek 4 kez tekrarlanır. Test dilimleri birleştirilir (WFO OOS).
2. WFO OOS `min_oos_sharpe` (0.30), `min_oos_profit_factor` (1.10) ve `min_oos_trades` (8) eşiklerini
   geçmezse coin **edge yok → BEKLE**. Canlı sinyal için tüm veride en iyi konfigürasyon kullanılır.
3. Edge'i olan coinde son kapanan barda giriş sinyali varsa **AL**; boyut = `equity × risk% / (ATR × stop çarpanı)`,
   üst sınır `max_position_pct`, `max_open_positions` ve nakit; Binance min emir tutarı (5 USDT) ve lot adımına yuvarlanır.
4. Pozisyon varken: stop'a değdiyse / strateji çıkış verdiyse / strateji artık FLAT ise **SAT**, aksi halde **TUT** (iz süren stop yukarı çekilir).
5. BTC rejimi **DÜŞÜŞ** ise altcoin AL boyutu %50, güven −10.
6. Skor 0-100 = edge kalitesi (45) + rejim (25) + RSI konumu (20) + volatilite (10). `min_confidence_to_buy` altı → BEKLE.

Bu eşikler kasıtlı olarak **muhafazakâr**dır: ayı piyasasında bot çoğunlukla "BEKLE" der ve sermayeyi korur
(2026-08 itibarıyla 10 coinin tamamında son 13 ay buy&hold −11 % … −80 %; hiçbir long-only konfigürasyon WFO'da pozitif edge göstermedi → bot nakitte).
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
├─ tradingbot/            paket (yukarıdaki katmanlar; tradingview.py = TradingView istemcisi)
│  ├─ scanner.py          tüm piyasa tarayıcı · engine.py 7/24 motor · paper_futures.py kağıt futures · learning.py öğrenme · charts.py görseller
│  └─ agents/             base, technical (7 ajan), market (Binance canlı), manager (coin yöneticisi + baş yönetici), runner
├─ tests/test_bot.py
├─ scripts/run_bot.bat, run_bot_loop.bat, watch_7_24.bat
├─ data/                  OHLCV önbelleği (git dışı)
├─ state/                 portfolio.json, signals.json, signals_log.jsonl (git dışı)
└─ Trading_bot/           Obsidian kasası (bot buraya yazar)
```
