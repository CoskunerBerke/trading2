# GÖLGE PAPER PAKETİ — ileri dönem doğrulaması

Bu paket, **geliştirmede ayakta kalan tek aday** için ileri dönem (gerçek zamanlı, henüz
var olmayan veri) doğrulaması kurar. Çalışan worker'a **DOKUNMAZ**: ayrı süreç, ayrı state,
ayrı defter, ayrı öğrenme dosyası, ayrı işlem kimliği.

**VPS'te çalıştırmak bu promptla yetkilendirilmedi.** Aşağıdakiler operatör onayına kalır.

## 1. İzolasyon sözleşmesi

| alan | üretim | gölge |
|---|---|---|
| state dizini | `/opt/tradingbot/data/state` | `/opt/tradingbot/data/shadow/<aday>/state` |
| defter | `state/futures_ledger.json` | `shadow/<aday>/state/futures_ledger.json` |
| öğrenme | `state/learn_v2.json` | `shadow/<aday>/state/learn_v2.json` |
| işlem kimliği | `F00001…` | `S<aday>-00001…` (`ledger.id_prefix`) |
| yetki markörü | `state/worker_authority.json` | ayrı dizinde, ayrı markör — split-brain YOK |
| vault | `data/vault` | **YAZILMAZ** (`TRADINGBOT_VAULT_PATH` boş) |
| borsa | PAPER, anahtar yok | PAPER, anahtar yok |

Gölge süreç `TRADINGBOT_STATE_DIR` ile ayrılır; `authority.py` markörü state dizinine bağlı
olduğu için iki worker birbirinin yetkisini almaz (kaynaktan doğrulandı).

Aynı `TRADINGBOT_CACHE_DIR` (arşiv) **okunur**, yazılmaz: arşiv tazeleme yalnız üretim
worker'ında açık kalır (`history_refresh` gölgede kapatılır).

## 2. Kaynak sınırı

| sınır | değer | neden |
|---|---|---|
| tur aralığı | 15 dk (üretimle aynı) | kadans karşılaştırılabilir olmalı |
| sembol | on coin | evren değişmez |
| ağ isteği | tur başına ≤ 45 | üretimin ölçülen 42'si + pay |
| `MemoryMax` | 900 MB (systemd) | üretim worker'ıyla aynı kova |
| `CPUQuota` | %50 | üretim turunu geciktirmemeli |
| pattern indeksi | KAPALI | bellek ve süre; gölge karşılaştırması buna bağlı değil |

## 3. Durdurma ölçütleri (önceden yazılır, sonradan değiştirilmez)

Gölge süreç şu koşullardan **biri** oluşursa kendini durdurur ve durumu `shadow/<aday>/
STOPPED.json` dosyasına yazar:

1. **Süre doldu:** 90 takvim günü. Bu süre sonuca bakılarak UZATILMAZ.
2. **Kapanış hedefi:** 60 kapanmış işleme ulaşıldı (hangisi önce gelirse).
3. **Zarar tavanı:** gölge özkaynak 100 → 75 USDT (−%25 düşüş).
4. **Bozukluk:** üst üste 3 turda `DATA_INVALID` ya da funding kapsamı `complete=false`.
5. **Üretim çakışması:** gölge tur süresi 10 dakikayı aşarsa (üretimi geciktirme riski).

Durdurma ölçütü tetiklendiğinde **hiçbir terfi otomatik yapılmaz.** Sonuç rapor edilir,
karar operatöre kalır.

## 4. Değerlendirme (süre dolmadan BAKILMAZ)

Üretim worker'ı ile gölge aynı takvimde çalıştığı için karşılaştırma eşleştirilmiştir.
Raporlanacaklar: net USDT, hesap getirisi %, işlem sayısı, ortalama/medyan net R, profit
factor, maksimum düşüş, maruziyet (gün oranı), coin/rejim dağılımı, funding kapsamı.

Başarı ölçütü **önceden**: gölge net hesap getirisi üretimin (B1a) üstünde **ve pozitif**
olmalı; ayrıca en az 6 farklı coin ve 40 kapanış. Bu sağlanmazsa aday **araştırma adayı**
olarak kalır; "kârlı strateji" DENMEZ.

## 5. Kurulum iskeleti (çalıştırılmadı)

```
# 1) izole dizin — uretim state'ine DOKUNULMAZ
sudo -u tradingbot mkdir -p /opt/tradingbot/data/shadow/<aday>/state

# 2) config kopyasi: yalniz aday kurali + id_prefix + history_refresh kapali
sudo -u tradingbot cp /opt/tradingbot/app/config.yaml \
     /opt/tradingbot/data/shadow/<aday>/config.yaml

# 3) systemd birimi (ornek, deploy/tradingbot-worker.service'ten turetilir)
#    Environment=TRADINGBOT_STATE_DIR=/opt/tradingbot/data/shadow/<aday>/state
#    Environment=TRADINGBOT_VAULT_PATH=
#    MemoryMax=900M
#    CPUQuota=50%
```

Gölge birimi üretim birimini **After=** ile takip eder ve aynı anda tur açmaz.

## 6. Bu turda neden boş

Geliştirme penceresinde §4 şartlarını geçen aday çıkmazsa bu paket **kurulmaz**.
Aday adı, kural ve parametreleri `research/entry_v1/out/DENEY_V2.md` dosyasındaki karar
bölümünden alınır. Paket iskeleti aday çıkmasa da teslim edilir ki bir sonraki tur sıfırdan
başlamasın.
