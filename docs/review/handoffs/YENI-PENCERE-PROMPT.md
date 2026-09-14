# YENİ CLAUDE PENCERESİ İÇİN BAŞLANGIÇ PROMPTU

Aşağıdaki metni yeni sohbete olduğu gibi yapıştır. Claude'un hafızası (`memory/MEMORY.md`) otomatik
yüklenir; bu dosya yalnız bu turun kaldığı yeri anlatır.

---

Trading bot projesinde (C:\Users\berke\Trading bot, çalışma dalı `work/entry-research-v1`, worktree
`C:\Users\berke\wt-entry`) kaldığımız yerden devam ediyoruz. Önce şunu oku ve her iddiayı kaynaktan
doğrula, önceki oturumun raporuna güvenme:

    C:\Users\berke\trading2-deploy\RESUME-entry-research-v1.md   (en alttaki bölümler en yeni)

Durum özeti (2026-09-13):

* VPS `0796c91` çalıştırıyor (PAPER, 100 USDT sanal): rejim kapısı ENFORCE r1 (yalnız LONG, yalnız
  BTC 1d close > EMA200), mum vetosu ENFORCE c3, grafik formasyonları SHADOW, candle_v1.2.0. İlk tur
  doğrulandı. Canlı kontrol: `trading2-deploy/tb-check.sh` (scp + `sudo bash ~/tb-check.sh`).
* SSH bu oturumdan yapılamaz (anahtar parola korumalı); komutları kullanıcıya PowerShell için ver:
  `scp -i $env:USERPROFILE\.ssh\trading2_ovh <dosyalar> ubuntu@<VPS_HOST>:~/` ve
  `ssh -t -i $env:USERPROFILE\.ssh\trading2_ovh ubuntu@<VPS_HOST> "sudo bash ~/<betik>"`.
  Deploy betikleri ROOT ile çalışır, git işlemlerini `sudo -u tradingbot` ile yapar, bundle kullanır
  (GitHub'a VPS'ten erişim yok). Şablon: `trading2-deploy/tb-deploy-0796c91.sh` ve
  `gen_deploy_v7.py` (scratchpad'de olabilir; yoksa betiği şablondan türet).
* DENEY_V9 (research/entry_v1/out/DENEY_V9.md): tek kurallı EMA200 trend stratejisi (T2: BTC rejimi UP
  iken günlük close > EMA200 → LONG piyasa, close < EMA200 → kapat, felaket stopu 3×ATR14(1d), kaldıraç 1,
  hedef yok, başa-baş koruması KAPALI) botun KENDİ defterinde üç pencerede pozitif: +107% / +34% / +10%,
  maksDD %22; şu anki üretim +141% / −28% / −40%. Replay strateji modu `HistoricalReplay(strategy=...)`
  commit `0ab33af`; kural `research/entry_v1/strategy_rules.py` (`t2_trend_regime`).
* Kullanıcı kararı: bu kuralı VPS'te PAPER modda, AYRI defterle, mevcut botun yanında CANLI İLERİ TESTE
  almak. Gerçek para YOK; kullanıcı "her gün yeşil görmeden canlıya almam" dedi ve bu doğru.
* Kullanıcıya ASLA "hatasız kâr" vaat etme; 13 giriş süzgeci + 3 formasyon deneyi reddedildi (hafızada).
  Yeni gösterge/formasyon talebi gelirse DENEY_V4/V5/V8 gösterilir, yeniden koşulmaz.

Bu turun işi (1–4 TAMAMLANDI: kod commit edildi, testler ve parite geçti; tam paket ve VPS deploy durumu RESUME'nin en altındaki "V10" bölümünde):

1. Üretim motoruna (`tradingbot/engine_v3.py`) replay'deki ile AYNI strateji modu kancası: ayrı
   `FuturesLedgerV2` (state/strategy_t2/), ayrı trade memory, mevcut bot dokunulmadan yanında; kural
   `tradingbot/ema200_trend.py` içinde TEK KAYNAK olsun ve replay `strategy_rules.py` ona
   delege etsin; parite: config'i izleyen replay hash'i `v9_t2_p1` ile aynı çıkmalı.
2. Config: `strategy_paper` bölümü (enabled, name, starting_equity_usdt=100, risk_per_trade_pct=2,
   leverage=1); LIVE modda ENFORCE reddi; başa-baş koruması bu defterde KAPALI.
3. Dashboard'a "Trend (kâğıt ileri test)" satırı: bakiye, açık pozisyonlar, kapanışlar, rejim.
4. Testler: birim + motor döngüsü + parite AST; tam paket (`python -m pytest tests -q` ~35 dk).
5. Bundle + root deploy betiği (taban `0796c91`), kullanıcı çalıştırır, `DEPLOY_OK` bekle, sonra
   `tb-check.sh` benzeri kontrolle ilk turu doğrula. Hafıza ve RESUME güncelle.

Kurallar: protokol/ölçüm disiplini aynen (bkz. hafıza `handoff-verification-protocol`,
`split-is-not-an-experiment`). Kullanıcı Türkçe; kısa, dürüst, sayılar tabloda. Token verimliliği:
önce ara, sonra oku; büyük çıktıları filtrele.

EK (V12, 2026-09-13): T2 VPS'te canlı (a619fb0). Momentum deneyi V11: M2 (28 günlük TSMOM) üç pencerede
5/5 şart; karar: M2 ikinci kâğıt defter olarak T2'nin yanına (strategy_paper.extra). Commit `f25cb39`, paket
`tb-f25cb39.bundle` + `tb-deploy-f25cb39.sh` (taban a619fb0). Parite ve tam paket sonucu, deploy durumu:
RESUME'nin en altındaki V12 bölümü. Kullanıcı deploy'u kendisi çalıştırır; sonra `tb-check.sh` ile ilk tur.

EK (V13, 2026-09-14): f25cb39 VPS'te CANLI (T2 + M2 yan yana). İlk tur kontrolünde T2 evren dışı ZEN/USDT açmıştı
(tur listesi ≠ giriş evreni) ve sayaçlar restart'ta sıfırlanıyordu. Onarım commit `3501304`: defterler yalnız
on coinlik ölçülen evrende açar, kendi açık pozisyonlarını yönetir, sayaçlar kalıcı. Paket `tb-3501304.bundle` +
`tb-deploy-3501304.sh` (taban f25cb39). Tam paket ve deploy durumu: RESUME'nin en altındaki V13 bölümü. Deploy
sonrası `tb-check.sh`: iki özette counters.opened = açık + kapanış; ZEN yalnız yönetilir, yeni evren dışı giriş yok.

EK (V13R/V14, 2026-09-14): V13R sağlamlık taraması bitti (`research/entry_v1/out/DENEY_V13R.md`): M2 sıfır bayrak,
T2 tek pencerelik iki bayrak; VPS değişmedi. Sıradaki tur V14 = hayatta kalma yanlılığı: Ekim-2020 ilk 10 evreninde
(BTC ETH LINK YFI BCH UNI BNB LTC XRP DOT) T2/M2 üç pencerede yeniden ölçülür; veri `trading2-deploy/tb-2020-universe.tar.gz`
(VPS `vps-collect-2020-universe.sh`, csv.gz), ayrı önbellek dizinine açılır ve `TRADINGBOT_CACHE_DIR` ile koşulur;
PROTOCOL_V14.md önce yazılır. Yeni sembollerin borsa filtreleri (`symbol_filters.json`) eksikse önce tamamlanır.

EK (V14 SONUÇ, 2026-09-14): hayatta kalma testi bitti (`out/DENEY_V14.md`): 2020 evreninde T2 +89/−12/+35, M2
+101/+21/+10; bugünkü evren sayıları artık ÜST SINIR. VPS `3501304`, değişiklik yok. Bekleyen kod işi YOK; sıradaki
iş haftalık `tb-check.sh` okuması ve üç eğrinin (ana bot, T2, M2) izlenmesi. Kullanıcı yeni gösterge/strateji
isterse önce DENEY_V4…V14 gösterilir; ileri test kirletilmez.
