# Box olsaydı–gerçek farkı denetimi (`scripts/box_cf_gap_audit.py`)

Box defterinin karşı-olgusalları (açılmayan geçerli sinyaller, "olsaydı") aynı dönemin gerçek Box işlemlerinden belirgin
biçimde yüksek net R gösteriyor (2026-10-01 karnesi: olsaydı +0,42 R, gerçek +0,09 R). Bu betik farkın **nereden
geldiğini ölçer**; hiçbir kararı, parametreyi, stop/hedefi ya da state dosyasını değiştirmez. Sonuç PAPER verisidir ve
kâr iddiası değildir.

## Neyi ölçer (H1–H10)

| | Hipotez | Düzeltilmiş fark (hükmün dayandığı ölçü) |
|---|---|---|
| H1 | Stop/maliyet bileşimi: olsaydılar geniş stoplu (R cinsinden ucuz), gerçekler çoğu zaman dar stoplu. | Yön × stop kovası katmanlı fark |
| H2 | Dönem ve tekrar sayım: olsaydıların çoğu birkaç kalabalık günden, aynı hareket her 5 dk barında yeniden sayılıyor. | Bölüm (sembol, gün, yön) ortalamaları, güne göre katmanlı |
| H3 | POSITION_OPEN alt kümesi: taban parametresiyle (min stop %2,22) ve elde tutulan işlem yaşarken yazılan kayıtlar. | POSITION_OPEN olmadan fark |
| H4 | Stop taşması: olsaydı stopu seviyeden doldurur, gerçek stop ilk örneklenen fiyattan dolar. | Olsaydı stoplarına gerçek ortalama taşma eklenmiş fark |
| H5 | Giriş barı (B1): olsaydı etiketleyicisi girişin 5 dk barını yürümez, gerçek pozisyon açılıştan itibaren izlenir. | B1 üst sınır yanlılığı düşülmüş fark |
| H6 | Kalabalık defter, gün içi saat, evren sırası ve boşalan slotun yeniden doldurulması. | Doluluk bandı × yön katmanlı fark |
| H7 | Yalnız gerçekte olabilen sinyaller: aynı gün stoptan sonra yeniden giriş ve 23:50 sinyalleri. | Bu gerçek işlemler olmadan fark |
| H8 | Gün sonu ve funding: olsaydının eksik 00:00 funding'i, gerçeğin 00:05 kapanışı. | Eksik funding tamamlanmış fark |
| H9 | Yanlış taraf hedef: gerçek defter girişin zarar tarafındaki box_mid hedefiyle anında 'hedef1' kaybı yazıyor. | Bu çıkışlar 0 R sayılmış fark |
| H10 | Fiyat kaynağı: 60 sn mark ile kapanmış 1h bar çıkışları (betimsel). | 1h barla karar verilmiş gerçek çıkışlar olmadan fark |

Ayrıca: S0 envanter (dosya boyutları, etiket sürümleri, dışarıda kalan kayıtlar, karne kuralıyla tutarlılık satırı),
S-MAIN (ana botun karşı-olgusalları, `shadow_book.json` varsa) ve isteğe bağlı R (gerçek işlemlerin olsaydı etiketleyicisiyle
yeniden oynatılması, `--replay-klines` ile). H9'daki yanlış taraf hedef hatası gerçek çıkışları değiştirdiği için burada
yalnız **ölçülür**; düzeltilmesi sahibin onayına bağlıdır.

## Salt okunur sözleşme

- Ağ yok. State klasörüne hiçbir şey yazmaz; tek çıktı `--json-out` dosyasıdır. Bu yol state içindeyse ya da bir girdi
  dosyasıyla aynıysa betik çıkış kodu 2 ile durur. `__pycache__` yazmaz.
- Varsayılan yol yalnız Python standart kütüphanesini kullanır. `--config` verilirse yalnız `tradingbot.config`,
  `--replay-klines` verilirse yalnız `tradingbot.learning_cf`, `learn.shadow` ve `accounting` (ve pandas) yüklenir.
- `.jsonl` dosyaları satır satır okunur. JSON dosyaları 200 MB'ı aşarsa okunmaz (mesajla); okunanlar ayrıştırılırken
  inceltilir. Aynı `--seed` aynı sonucu verir.
- Çıkış kodları: 0 tamam · 2 hatalı argüman ya da gerekli dosya (`counterfactual_trades.json`, `futures_ledger.json`)
  yok · 3 gerekli dosya bozuk.
- Ölçülen maliyet (VPS boyutunda sentetik state: 1.500 olsaydı kaydı, 600 gerçek işlem, 200 bin satırlık
  `trade_memory.jsonl`, 50 MB `experience.jsonl`, 11 MB `shadow_book.json`, `--boot 2000`): yaklaşık 3,5 sn ve en çok
  60 MB bellek; canlı işçinin yanında küçük bir yüktür.

## Çalıştırma

Komutların hepsi `tradingbot` kullanıcısıyla ve salt okunur çalışır. `<COMMIT>` ve `<SHA256>` yerine yayın notundaki
değerler yazılır.

**1) Dağıtımdan önce** (VPS hâlâ eski sürümü çalıştırırken): yalnız bu dosya `/tmp`'ye indirilir, özeti doğrulanır ve
uygulama klasörü `PYTHONPATH` ile verilir.

```bash
curl -fsSL -o /tmp/box_cf_gap_audit.py \
  "https://raw.githubusercontent.com/CoskunerBerke/trading2/<COMMIT>/scripts/box_cf_gap_audit.py"
echo "<SHA256>  /tmp/box_cf_gap_audit.py" | sha256sum -c -
chmod 644 /tmp/box_cf_gap_audit.py
sudo -u tradingbot env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/tradingbot/app \
  TRADINGBOT_STATE_DIR=/opt/tradingbot/data/state \
  /opt/tradingbot/venv/bin/python /tmp/box_cf_gap_audit.py \
  --config /opt/tradingbot/app/config.yaml --json-out /tmp/box_gap_$(date -u +%Y%m%d).json
```

Depo özelse `curl` yerine yetkili bir oturumdan aynı dosya (aynı commit) indirilir; `sha256sum -c` satırı yine çalıştırılır.

**2) Dağıtımdan sonra** (betik uygulama klasöründe; `PYTHONPATH` gerekmez):

```bash
sudo -u tradingbot env PYTHONDONTWRITEBYTECODE=1 TRADINGBOT_STATE_DIR=/opt/tradingbot/data/state \
  /opt/tradingbot/venv/bin/python /opt/tradingbot/app/scripts/box_cf_gap_audit.py \
  --config /opt/tradingbot/app/config.yaml --json-out /tmp/box_gap_$(date -u +%Y%m%d).json
```

Seçenekler: `--since/--until` (t0 penceresi), `--learning-since` (varsayılan `learning_mode.json`), `--slots-change`
(varsayılan: `slots == 40` olan ilk işlem), `--boot`, `--seed`, `--min-cell`, `--pair-window-min`,
`--refill-window-min`, `--top`. `--replay-klines KLASÖR` Binance public-data 5m CSV'lerini
(`<SYMBOL>-5m-YYYY-MM-DD.csv`) okur; betik bunları **indirmez**, dosyalar ayrıca hazırlanır. Klasörde dosya yoksa
bölüm `VERİ YOK` yazar.

## Çıktıyı okumak

1. **Türkçe özet** (en çok 30 satır, ondalık virgül): olsaydı ve gerçek ortalamaları, ham fark, yön × stop katmanlı fark,
   Δbrüt − Δmaliyet ayrışması, her hipotez için bir satır, `Uyarılar` ve son satır `Sonuç:`.
2. **Ayrıntı tabloları** (İngilizce sütun adları, ondalık nokta). En sonda `Sonuç:` satırı yeniden yazılır.
3. **JSON** (`--json-out`): `verdicts` listesi `{h, effect_r, ci95, verdict, n_cf, n_real, n_days, ...}`.

`GA` %95 güven aralığıdır (UTC gününe göre kümeli bootstrap). `tek gün` tek günlük veride aralık olmadığı anlamına
gelir. `etki` = ham fark − düzeltilmiş fark, yani hipotezin açıkladığı kısımdır. Hüküm kelimeleri önceden sabitlenmiştir:

| Hüküm | Anlamı |
|---|---|
| AÇIKLIYOR | Düzeltme farkı en az %50 küçültüyor ve düzeltilmiş farkın GA'sı 0'ı içeriyor. |
| KISMEN | Fark en az %20 küçülüyor (ya da %50'den fazla küçülüyor ama GA 0'ı içermiyor). |
| AÇIKLAMIYOR | Fark %20'den az küçülüyor. |
| VERİ AZ | Karşılaştırılan hücrelerde 30'dan az olsaydı kaydı, 30'dan az gerçek işlem ya da 3'ten az UTC günü var. |
| VERİ YOK | İsteğe bağlı bir dosya ya da bölüm verisi yok; bölüm atlanır (hüküm değildir). |

`Uyarılar` bloğunda arşivin okunmadığı (`counterfactual_archive/`), eksik isteğe bağlı dosyalar, gerçek işlem ayrıştırma
uyuşmazlığı (`decompose_mismatch`), 3'ten az gün ve karne kuralıyla tutarsızlık görünür. S0'daki tutarlılık satırında
CF_NET ortalaması `scripts/bot_scorecard.py` kuralıyla 1e-3 içinde aynı olmalıdır. Hükümler tek başına işlem kararı
değildir; bir hipotez "açıklıyor" çıksa bile strateji değişikliği ayrı bir karardır.
