# Beş botun gerçek karar yolları (kaynak okuması, `2399eb5` + funding onarımı `4e64257`)

> Bu harita KODDAN okundu (satır numaraları `4e64257` ağacıdır). "Dosya var" ile "karar yoluna bağlı" ayrı yazılmıştır.
> Ortak yapı kataloğunun (`tradingbot/structures/`, bu dalda eklenir) bağlanacağı yerler **[ORTAK]** ile işaretlidir.

## Ortak parçalar (bugün)

| Parça | Dosya | Bugün kim çağırıyor |
|---|---|---|
| Mum şekilleri (tek formül) | `learn/candle_context.py::_shapes` (candle_v1.2.0) | ana bot C3 vetosu (`candle_confirmation.py`, ENFORCE), formasyon botu (`pattern_trader/detect.py`) |
| Mum ajanının KENDİ şekil formülü | `agents/technical.py::CandleAgent` (çekiç/kayan yıldız/yutan/doji — ayrı eşikler) | ana botun konsensüsü (`structure_levels` grubu) — **ikinci dedektör** |
| Grafik formasyonları | `chart_patterns.py::detect_chart_patterns` (yalnız KIRILIŞLA teyitli; oluşan yapı raporlanmaz) | ana bot p2 vetosu (`chart_confirmation.py`, config'te **SHADOW** → karara etkisi yok), panel (`chart_analysis.py`) |
| Teyitli pivot | `learn/multitimeframe_context.py::confirmed_swings` (sağda k bar) | grafik formasyonları, `chart_analysis.pivots`, formasyon botu seviyeleri |
| Seviye ajanının KENDİ salınımı | `agents/technical.py::LevelsAgent._swings` | ana bot planı (r1/s1) |

T2/M2 (`ema200_trend.py`) ve Box (`box_theory.py`) bugün hiçbir mum/grafik yapısı OKUMAZ.

## 1. Ana bot (`engine_v3`, 4h karar dilimi, LONG/SHORT)

veri → `runner.run_symbol` (perp çerçeveleri + provenans) → uzmanlar (`agents/technical.py`: trend, momentum, **candles**,
volume, **levels**, …; `coinhead/specialists.py`) → konsensüs (`coinhead/head.py:232 decide`, `factors.aggregate/consensus`;
`candles` ve `levels` ikisi de `structure_levels` grubuna yazar) → plan (`agents/manager.py:117 _plan`: kırılım = 4h kapanış
r1/s1 ötesinde; geri çekilme = seviyeye dokunuş "alıcı mumuyla" — **mum kodda denetlenmiyor**; `coinhead/head.py:103
_plan_from_legacy`, yoksa ATR planı) → şef sıralaması → `engine_v3.py:1476 _execute_locked`: evren → veri kimliği →
şef → **tetik** (`:1588`, `entry_trigger.trigger_fired`) → **C3 mum vetosu** (`:1597`) → p2 grafik (`:1607`, SHADOW) →
rejim R1 (`:1617`) → ekonomi → yinelenme (`:1654 _signal_id`) → araştırma politikası → boyut → risk (`:1761`) →
defter açılışı (`:1793`) → yönetim: `ledger2.tick` (tur `:1319`, 60 sn `exit_check` `:869`) — stop/TP/likidasyon/MFE
başa-baş. **Ana bot açık pozisyonu EXIT/REDUCE hükmüyle kapatmaz** (`engine_v3`te `close_manual` yok).

**[ORTAK]** (a) `CandleAgent` şekil katkısı katalogdan ve yapı başına BİR kez (çift oy yok); (b) `_execute_locked`
tetikten sonra yapı politikası (karşı teyitli yapı → BEKLE; geri çekilme planı → uyumlu teyitli yapı şartı); (c) planın
gerekçesine katalog seviyeleri/teyidi; (d) açık pozisyonda girişten SONRA teyitli karşı yapı → stop sıkılaştırma.
Replay ana modu (`replay/engine.py:397-460`) aynı sırayı taşır → aynı fonksiyon orada da çağrılır.

## 2. T2 (`t2_trend_regime`) ve 3. M2 (`m2_tsmom28`) — `StrategyBook`, 1d

veri → `_strategy_paper_tour` (`engine_v3.py:2348`; perp mark `_paper_marks`, kapanmış 1h uçları) → `StrategyBook.step`
(`strategy_paper.py:629`): veri hükmü `verify_paper_data` → `paper_rules.decide_for` (`:703`) → `ema200_trend.decide`
(T2: kapanış > EMA200 + BTC rejimi UP; M2: kapanış > 28 gün önceki kapanış + BTC UP; stop = kapanış − 3×ATR14; hedef yok;
yalnız LONG) → `apply_action` (`:708`, risk motoru + defter) → yönetim: defter tick'i (stop/likidasyon) + kural çıkışı
(EMA200/TSMOM aşağı kesişim, `CLOSE`). Giriş zamanlaması YOK: koşul doğruysa ilk turda piyasa girişi.

**[ORTAK]** `paper_rules.decide_for` içinde sürümlü yapı politikası (canlı `StrategyBook.step` ve replay strateji modu
AYNI fonksiyonu çağırır). M2 ayrıca açık pozisyonda yapı çıkışı.

## 4. Box (`b1_box_fade`) — `StrategyBook`, 1d + 5m

Aynı `StrategyBook` yolu; `box_theory.decide`: önceki günün kutusu (1d), 5m son iki mumun uçlarıyla konum TOP/BOTTOM,
tetik = kırmızı mum önceki mumun altına kapanış (SHORT) / yeşil mum önceki mumun üstüne (LONG), stop = önceki mum tepesi
(SHORT) / gün dibi (LONG), hedef = kutu ortası, gün sonu düzleşme; giriş `apply_action`e canlı mark ile, `signal_close`
kayma kapısı (0 = kapalı). Dış kırılım kavramı YOK.

**[ORTAK]** kenarda dönüş / taşma-geri dönüş yapıları kararı üretir; teyitli dış kırılım karşı yöndeki dönüş planını iptal eder.

## 5. Formasyon botu (`pattern_trader`) — 15m giriş, 1h yapı, 4h bağlam

veri (`MarketFeed`, USDM perp, CSV önbellek) → `PatternScanner._scan_symbol` (`scheduler.py:231`) →
`PatternBook.process_symbol` (`book.py:312`): bulgular `detect.detect_findings` (15m/1h/4h `_shapes`) + durum güncelleme →
plan tetikleri `evaluate_trigger` (`:393`) → `_try_open` (`:541`: süre, evren, veri, fiyat, kovalama, likidite, R/R,
filtre, yuvarlama) → `apply_action` (`:671`) → yönetim: defter tick'i + zaman stopu; planlar `strategy.build_plans`
(`:429`): A_TREND_PULLBACK, B_LEVEL_REVERSAL (15m mum bulgusu), C_COMPRESSION_BREAKOUT.

**[ORTAK]** bulgular katalogdan (mum + grafik yapısı + senaryolar); plan = katalog kaydının tetik/geçersizlik/stop/hedefi
(+ aile bağlam filtresi); eski üç aile v1 olarak ayrı sürüm.
