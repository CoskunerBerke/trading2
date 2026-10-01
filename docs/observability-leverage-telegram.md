# Panel doğruluğu, canlı PnL, 2x–5x kaldıraç ve Telegram

Bu paket **gözlemlenebilirlik/kullanılabilirlik** katmanıdır. Coin seçimi, indikatörler, MA/RSI,
sinyal eşikleri, veto mantığı, stop/TP stratejisi ve risk bütçesi **değiştirilmemiştir**. Tek
davranış değişikliği yeni PAPER futures işlemleri için dinamik `2x–5x` kaldıraç seçimidir ve o da
**varsayılan olarak kapalıdır**.

---

## 1. Kavram sözlüğü (panelde karıştırılmaması gerekenler)

| Kavram | Anlamı | Kaynak |
|---|---|---|
| **İşlem adayı** (`breadth.long`) | Son strateji turunda LONG kararı üretilen sembol sayısı | `coin_heads.json → chief` |
| **Açık pozisyon** | Defterdeki gerçek pozisyon | `futures_ledger.json → positions` |
| **HOLD** | Açık pozisyonu korunan sembol (aday değil) | `chief.breadth.hold` |
| **Coin adedi** | Coin/kontrat **adedi** — USDT değil | `position.qty` |
| **Notional** | Pozisyon değeri (USDT) = `qty × entry` | hesaplanır |
| **Teminat** | Kullanılan başlangıç marjı = `notional / leverage` | `position.isolated_margin` |
| **Beklenen Net Getiri** | İşlem **öncesi model tahmini** — gerçekleşen sonuç değildir | coin-head kararı |

> `breadth.long = 3` iken `açık pozisyon = 2` **tutarsızlık değildir**: 3 yeni LONG adayı +
> 2 açık pozisyon (bunlar `hold` içinde sayılır) demektir.

Panel şu değişmezleri denetler ve ihlalinde `⚠ Veri tutarsızlığı tespit edildi` gösterir:

```text
gerçek_açık_pozisyon_sayısı == açık_pozisyonlar_tablosundaki_satır_sayısı
gerçek_açık_long + gerçek_açık_short == gerçek_açık_toplam
```

---

## 2. Kanonik PnL (panel ve Telegram AYNI katmanı kullanır)

`tradingbot/pnl.py` tek kaynaktır. Ücret ve funding **asla iki kez** düşülmez.

```text
Futures LONG   brüt = qty × (mark − entry)
Futures SHORT  brüt = qty × (entry − mark)

net gerçekleşmemiş = brüt − açılış ücreti − tahmini kapanış ücreti ± funding
net gerçekleşen    = kapanış kaydının net_pnl alanı
                   = gross − giriş ücreti − çıkış ücreti ± funding   (ledger _finalize)
```

Yüzde paydası: **FUTURES → kullanılan başlangıç teminatı**, **SPOT → yatırılan tutar**. Panelde
tablo altında açıkça yazılır.

Küçük değerler `+0.00`'a yuvarlanmaz: `|x| < $0.01` iken 4–6 ondalık gösterilir (`+$0.004213`).
Hesap `Decimal` ile yapılır; yuvarlama yalnız sunum katmanındadır.

---

## 3. Canlı panel

Panel **salt-okunur**dur ve worker'ı etkilemez. Tarayıcı **Binance'a doğrudan bağlanmaz**; veriler
worker'ın yazdığı state dosyalarından okunur, bu yüzden her tarayıcı isteği yeni bir borsa API
çağrısı üretmez.

Uçlar: `/api/live/positions`, `/api/live/summary`, `/api/live/health`.

```yaml
dashboard:
  poll_positions_s: 7          # açık pozisyon mark/PnL      (5–10 sn önerilir)
  poll_portfolio_s: 20         # bakiye/teminat/açık risk    (10–30 sn)
  poll_health_s: 12            # sağlık + heartbeat          (10–15 sn)
  stale_price_s: 90            # üzerinde "FİYAT VERİSİ GÜNCEL DEĞİL"
  stale_run_s: 2400            # strateji turu yaşı uyarısı
  background_backoff_mult: 4   # arka plan sekmesinde aralık çarpanı
  timezone_label: "UTC"
```

Koruma önlemleri: aynı anda tek istek (overlap yok), `AbortController` ile zaman aşımı, arka plan
sekmesinde backoff, bağlantı koparsa **CANLI etiketi yeşil kalmaz**.

Fiyat yaşı ile **strateji turu yaşı ayrı** gösterilir — biri diğerinin yerine kullanılmaz.

---

## 4. Dinamik 2x–5x kaldıraç (PAPER)

### Neden gerekliydi
`CoinHead._plan_from_atr` planı `PlanSize(..., leverage=1)` ile üretiyor, `size_position` da
`max(1, min(max_leverage, 1)) = 1` yapıyordu → **bütün futures işlemleri 1x** açılıyordu.

### En kritik ilke — kaldıraç riski artırmaz

```text
notional        = risk_bütçesi / stop_mesafesi     (kaldıraçtan BAĞIMSIZ)
initial_margin  = notional / leverage              (kaldıraç YALNIZ bunu belirler)
```

2x, 3x, 4x ve 5x için stopta beklenen maksimum dolar zararı **aynıdır**. Aynı teminatı koruyup
notional'ı kaldıraç kadar büyütmek yasaktır.

### Seviyeler (kümülatif — 5x için 2x/3x/4x koşulları da sağlanmalıdır)

| Seviye | Koşul |
|---|---|
| **NO_TRADE** | Taban kapıları geçilemedi → **zayıf sinyal 2x ile açılmaz** |
| **2x** | Bütün kalite/risk kapıları geçildi, daha yüksek için ek marj yok |
| **3x** | Güçlü sinyal, uygun volatilite, güvenilir stop, yeterli likidite |
| **4x** | + yüksek likidite, dar spread, uygun funding, düşük yoğunlaşma, `liq_buffer ≥ 3.5` |
| **5x** | + en yüksek güven/edge, düşük korelasyon, rejim uyumu, `liq_buffer ≥ 4.5` |

Girdiler: stop mesafesi, ATR/volatilite, sinyal güveni, likidite (derinlik+spread), funding,
piyasa rejimi, toplam açık risk oranı, aynı yön yoğunlaşması, portföy korelasyonu, likidasyon
tamponu. **Bilinmeyen girdi yükseltmeyi engeller** (fail-closed). Veri eksik/stale/çelişkili ise
işlem açılmaz. Spot işlemlere kaldıraç uygulanmaz.

```yaml
leverage:
  enabled: false          # VARSAYILAN KAPALI — bilinçli olarak açılır
  paper_only: true        # LIVE/TESTNET için varsayılan kapalı
  min_leverage: 2         # 1x yeni futures işlemi açılamaz
  max_leverage: 5         # MUTLAK üst sınır (config ile aşılamaz)
  # eşikler: conf_3x/4x/5x, edge_3x/4x/5x, max_atr_pct_*, min_depth_*,
  #          max_spread_*, max_funding_*, max_open_risk_frac_*, max_same_dir_*,
  #          max_corr_5x, liq_buffer_4x, liq_buffer_5x
```

### Mevcut açık pozisyonlar korunur
Deployment sırasında açık olan **eski `1x` pozisyonlar hiç değişmez**: kaldıraç, miktar, giriş,
stop, TP ve işlem ID'leri aynı kalır; pozisyon kapatılıp yeniden açılmaz; sentetik leverage
migration yapılmaz; PnL geçmişi yeniden yazılmaz. Yeni politika **yalnız deployment sonrası açılan
yeni** PAPER futures pozisyonlarına uygulanır.

Her yeni pozisyon `meta` içinde kalıcı snapshot saklar:
`leverage_decision` (seçilen seviye, gerekçeler, neden daha yükseği seçilmedi, likidasyon tamponu)
ve `risk_snapshot` (`final_notional`, `initial_margin`, `stop_frac`, `max_loss_at_stop_usdt`,
`execution_entry`).

---

## 5. Telegram bildirimleri

### Token güvenliği
Token **hiçbir zaman** repoya, log'a, exception mesajına, panel API'sine veya outbox dosyasına
yazılmaz. Config yalnız **ortam değişkeni adını** tutar; değer VPS'teki gizli env dosyasında kalır.
`config_v3` bir token değerini env adı yerine yazma girişimini **reddeder**.

```bash
# /opt/tradingbot/env.d/telegram.env   (chmod 600, repoya GİRMEZ)
# Aynı dosya hem worker hem `tradingbot-alert@.service` tarafından EnvironmentFile ile okunur.
TRADINGBOT_TELEGRAM_ENABLED=false
TRADINGBOT_TELEGRAM_BOT_TOKEN=
TRADINGBOT_TELEGRAM_CHAT_ID=
```

```yaml
telegram:
  enabled: false                 # VARSAYILAN KAPALI → hiçbir ağ çağrısı yapılmaz
  bot_token_env: "TRADINGBOT_TELEGRAM_BOT_TOKEN"
  chat_id_env: "TRADINGBOT_TELEGRAM_CHAT_ID"
  timeout_s: 8.0
  max_retries: 3                 # sonsuz retry YOK
  outbox_file: "notify_outbox.json"
  suppress_backlog_on_start: true
  daily_summary_enabled: true
  daily_summary_hour_utc: 21
```

### Olaylar
İşlem açıldı / kapandı (stop-loss, take-profit, likidasyon, trailing, sinyal çıkışı ayrı
etiketlenir), worker sağlığı bozuldu / düzeldi, günlük PAPER performans özeti.

Her mesaj `PAPER` etiketiyle başlar; zarar `🔴` ve negatif işaretle gösterilir.

### Olay kapıları ve zamanlama (hepsi GERÇEKTEN tüketilir)

| Ayar | Etki |
|---|---|
| `notify_open: false` | Açılış olayı **üretilmez** (outbox'a bile yazılmaz) |
| `notify_close: false` | Kapanış/stop/TP olayı üretilmez |
| `notify_health: false` | Sağlık, worker-failure ve worker-recovery olayları üretilmez |
| `daily_summary_enabled: false` | Günlük özet üretilmez |
| `daily_summary_hour_utc` | `0–23`; bu UTC saatinden itibaren o gün **tam bir kez** özet |
| `retry_backoff_s` | Başarısız olayın bir sonraki denemesine kadar bekleme tabanı |
| `retry_batch` | Bir worker turunda en çok kaç başarısız olay yeniden denenir |

Günlük özet event id'si gün bazlıdır (`daily_summary:portfolio:YYYY-MM-DD`): worker aynı gün
yeniden başlasa da ikinci özet gitmez. Özet saatinde worker kapalıysa **sonraki uygun turda** aynı
günün özeti bir kez gönderilir; geçmiş günler için toplu mesaj üretilmez.

### Otomatik yeniden deneme

Her worker turu sonunda `retry_pending()` çalışır: **yalnız zamanı gelmiş** (`next_attempt_at`)
olaylar, **en çok `retry_batch`** tanesi denenir. Backoff üsteldir ve üst sınırlıdır
(`retry_backoff_s × 2^(deneme−1)`, en çok 1 saat). `max_retries` dolan olay bir daha denenmez —
sonsuz retry yoktur ve tur bloklanmaz. `sent`/`suppressed` olaylar asla yeniden gönderilmez.
Yeniden deneme, outbox'ta saklanan **orijinal mesajı** gönderir (uydurma metin yok).

### Gönderim, giriş kilidinin DIŞINDA

Kritik bölgede (`_entry_lock`) yalnız defter kaydı ve **hızlı, yerel** outbox yazımı yapılır.
Telegram HTTP'si kilit bırakıldıktan sonra `flush()` ile denenir. Böylece yavaş/asılı bir taşıma
giriş kilidini tutmaz; gönderim başarısız olsa bile **açılmış işlem geri alınmaz** ve olay retry
kuyruğunda kalır.

### Worker SÜRECİ öldüğünde (harici uyarı)

Süreç içi notifier kendi ölümünü bildiremez. Bunun için kaynak-kontrollü bir systemd hook'u vardır:

* `deploy/tradingbot-worker.service` → `OnFailure=tradingbot-alert@%n.service`
* `deploy/tradingbot-alert@.service` → `Type=oneshot`, `tradingbot worker-alert --event failure`

Özellikler ve sınırları:

* Token **komut satırına yazılmaz**; yalnız `EnvironmentFile` ile gelir → `systemctl status`,
  process list ve journal'a sızmaz. Kabuk interpolasyonu yoktur.
* `systemctl stop` `OnFailure=` **tetiklemez** → operatörün kontrollü durdurması yanlış "çöktü"
  bildirimi üretmez (systemd sözleşmesi).
* Worker `Restart=on-failure` + `StartLimitBurst=5` ile önce kendi kendine dener; unit ancak
  yeniden başlatma bütçesi tükendiğinde `failed` olur → hook restart döngüsünde **mesaj yağmuru
  üretmez**. Ek olarak aynı `--ref` ile ikinci çalıştırma outbox tarafından yutulur.
* Telegram kapalıysa komut **temiz no-op** (çıkış 0, ağ çağrısı yok).
* Kurtarma mesajı worker **gerçekten ready/healthy** olduğunda gönderilir, failure olayına
  bağlanır ve iki kez gönderilmez.

**Kurulum** (bu görevde yapılmadı):
```bash
sudo cp deploy/tradingbot-alert@.service /etc/systemd/system/
sudo cp deploy/tradingbot-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### İdempotency
Olay kimliği: `işlem ID + yaşam döngüsü olayı + fill/close referansı`. Kalıcı, atomik outbox
(`state/notify_outbox.json`) her olayı `pending/sent/failed/suppressed` olarak işaretler.

* Worker yeniden başladığında **mevcut açık pozisyonlar için sahte "yeni işlem" bildirimi gönderilmez**
  (`suppress`); bu pozisyonlar **kapandığında gerçek kapanış bildirimi yine gönderilir**.
* Aynı olay iki kez gönderilmez (yeni süreçte de, çünkü outbox kalıcıdır).
* Telegram hatası trade döngüsünü **durdurmaz** ve pozisyon kaydını **geri almaz**.

### Bozuk outbox: kurtarma ve kalan risk

Outbox atomik **ve yedekli** yazılır (`keep_backup=True`). Ana dosya bozulursa `read_json`
otomatik olarak `.bak` kopyasından kurtarır ve bozuk dosyayı `<ad>.corrupt-N` olarak kenara alır
(silmez). Bozuk veri **asla "gönderildi" sayılmaz**.

**Kalan risk:** hem ana dosya hem `.bak` aynı anda okunamaz hâle gelirse idempotency geçmişi
kaybolur (fail-open). Bu durumda bir kapanış/özet bildirimi ikinci kez gidebilir. Açılış tarafında
`bootstrap_open_positions()` mevcut açık pozisyonları yeniden bastırdığı için sahte "yeni işlem"
bildirimi oluşmaz. Olasılık düşüktür (iki dosyanın birlikte bozulması gerekir) ve etkisi yalnız
tekrarlanan bir bilgilendirme mesajıdır — işlem/defter etkilenmez.

---

## 6. Gelecekteki VPS deployment kontrol listesi

> Bu görevde deployment **yapılmadı**. Aşağıdaki adımlar bir sonraki oturum içindir.

1. **Backup / checkpoint**: `tradingbot-backup.timer` ile veya elle `/opt/tradingbot/data` yedeği.
2. **Açık pozisyon semantic snapshot** (deployment ÖNCESİ kaydet):
   her pozisyon için `id, symbol, side, qty, entry_avg, leverage, stop, targets, isolated_margin`.
3. **Env anahtarları**: `telegram.env` oluştur (chmod 600). Token yalnız burada.
4. **Servis sırası**: `worker` durdur → kod güncelle → `dashboard` yeniden başlat → `worker` başlat.
5. **State karşılaştırması**: deployment SONRASI aynı snapshot'ı çıkar ve **birebir** karşılaştır —
   ID, miktar, giriş, stop, TP ve **kaldıraç** değişmemeli.
6. **PAPER/LIVE güvenlik kontrolü**: `mode.json → PAPER`, `live_trading=false`, gerçek emir sayısı `0`.
7. **Ready/heartbeat**: `/health/ready` 200 ve heartbeat yaşı eşiğin altında.
8. **Telegram test mesajı**: yalnız operatör onayıyla, tek seferlik; `enabled=true` yapmadan önce
   outbox'ın boş/temiz olduğunu doğrula.
9. **Kaldıraç açma**: `leverage.enabled=true` YALNIZ yukarıdaki adımlar temiz çıktıktan sonra ve
   yalnız PAPER'da.
10. **Rollback**: kodu önceki SHA'ya al, `worker`/`dashboard` yeniden başlat. `leverage.enabled` ve
    `telegram.enabled` `false` yapılırsa davranış eski hâline döner; **açık pozisyonlar etkilenmez**
    (kaldıraç snapshot'ı pozisyonda saklıdır ve okunmaya devam eder).
