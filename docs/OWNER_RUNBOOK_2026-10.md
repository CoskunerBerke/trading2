# Sahip el kitabı — 7–14 Ekim 2026

Bu belge, Claude'a yazamadığın günlerde ne yapacağını anlatır. GitHub'da açık; Claude olmadan da okunur.
Bütün komutları VPS'te sen çalıştırırsın (`ssh ubuntu@vps-5426050a.vps.ovh.net`). Bot yalnız PAPER çalışır.

## 0. Önce bil

- **Bot kendi başına işlem açar ve kapatır.** Senin bir şey yapman gerekmez. Botun kendi öğrenme modu zaten
  açık (`learning_mode`).
- **Tur düzeltmesi şu an KAPALI.** 6 Ekim 16:55'te kapatma anahtarı uygulandı (`config.yaml` 172. satır:
  `evidence_subprocess: false`). Bu satır kalsın, silme. Bazı turlar ~40 dk sürebilir ve `--check`
  "tur süresi [TETİKLENDİ]" diyebilir: bu **beklenen eski durum**, geri alma gerekmez.
- **Öğrenme motoru (P1a)** kurulunca her gece 04:37'de (TR) kendi çalışır. Yapay zekâ ya da token kullanmaz.
  Her kapanan işlemi kaydeder ve her gün %1 hedefini ölçer. Botun kararlarına dokunmaz.
- Çıktıları **dosyaya kaydet**. Claude'a yeniden yazabildiğinde onları yapıştırırsın.

## 1. Her sabah — sağlık kontrolü (1 dakika, isteğe bağlı ama önerilir)

```
sudo bash /tmp/tb-deploy-8db1faf.sh --check > ~/check-bot-$(date +%F).txt 2>&1; sed -n '/== servisler/,/== ÖĞRENME MODU/p' ~/check-bot-$(date +%F).txt; grep -E "\[(tamam|TETİKLENDİ|ölçülemedi)\]" ~/check-bot-$(date +%F).txt
```

`/tmp/tb-deploy-8db1faf.sh` yoksa (VPS yeniden başlarsa silinir):

```
cd /tmp && curl -fsSLo tb-deploy-8db1faf.sh https://raw.githubusercontent.com/CoskunerBerke/trading2/257ef8e1de6671b277020c6d1a23b141045dc589/deploy/releases/tb-deploy-8db1faf.sh && sha256sum tb-deploy-8db1faf.sh
```

sha256 şu olmalı: `83a22ab55e2f8ba7a091f9806313e0c673252fa4c33256a7b510db899427eae3`

**Neye bakılır:**

| Satır | İyi | Ne yaparsın |
|---|---|---|
| `tradingbot-worker.service: active` | active, yeniden başlama 0 | active değilse → bölüm 4 |
| `OOM kill:` | 0 | 0 değilse → çıktıyı sakla, bölüm 4 |
| `[tamam] bellek` | tamam | `[TETİKLENDİ] bellek` → bölüm 4, "bellek" |
| `[TETİKLENDİ] tur süresi` | — | **Beklenen** (tur düzeltmesi kapalı). Bir şey yapma |
| `[tamam] Box kaçan bar` | tamam | tetiklenirse çıktıyı sakla, bir şey yapma |

## 2. 9 Ekim — öğrenme motoru (P1a) kurulumu

**Önkoşul:** 7 ve 8 Ekim sabah kontrollerinde worker `active`, OOM kill 0 ve bellek `[tamam]`. Değilse kurma.

**Saat (TR):** 09:10–10:30 ya da 11:45–14:30. Bu saatlerin dışında betik kendisi durur (gece 03:00–07:00,
her 4 saatlik kapanıştan sonraki ilk 40 dk ve bir sonraki kapanışa 20 dk kala yasak). 9 Ekim 09:05'ten önce de durur
(3 gün kuralı).

1. İndir ve doğrula:
   ```
   cd ~ && curl -fsSLo tb-engine-4962209.sh https://raw.githubusercontent.com/CoskunerBerke/trading2/8d37d052886d039da8be9b24d4f0497d488c431b/deploy/releases/tb-engine-4962209.sh && sha256sum tb-engine-4962209.sh
   ```
   sha256 tam olarak şu olmalı: `f9aab6eb2b61aacd37eac037c9730c51b829335416f0bb5f86f70bd73a84fc9d`
   Farklıysa **dur**, hiçbir şey çalıştırma.

2. Deneme (hiçbir şey değişmez):
   ```
   sudo bash ~/tb-engine-4962209.sh --dry-run > ~/engine-dryrun.txt 2>&1; echo "çıkış kodu: $?"; tail -30 ~/engine-dryrun.txt
   ```
   Çıkış kodu **0** olmalı ve `DUR:` satırı olmamalı. Değilse **dur**; dosyayı sakla.

3. Kurulum:
   ```
   sudo bash ~/tb-engine-4962209.sh > ~/engine-deploy.txt 2>&1; echo "çıkış kodu: $?"; tail -40 ~/engine-deploy.txt
   ```

   | Çıkış kodu | Anlamı | Ne yaparsın |
   |---|---|---|
   | 0 | Kuruldu, deneme çalıştırması geçti, gece zamanlayıcısı açık | Hiçbir şey. Bitti |
   | 1 | Ön denetimde durdu, **hiçbir şey değişmedi** | Dosyayı sakla, bekle |
   | 2 | Deneme çalıştırması geçmedi ya da saat uygun değil; zamanlayıcı **kapalı** | Dosyayı sakla, bekle. Güvenli |
   | 3 | Bellek ayarı kaydı; motor birimleri **geri alındı** | Dosyayı sakla, bekle |
   | 4 | Kuruldu ama bir denetim kaldı | Motoru durdur (bölüm 4, "motor"), dosyayı sakla |

   Worker ve panel bu kurulumda **durmaz ve yeniden başlamaz**.

## 3. 10 Ekim'den itibaren her sabah — motor kontrolü

```
sudo bash ~/tb-engine-4962209.sh --check > ~/check-engine-$(date +%F).txt 2>&1; echo "çıkış kodu: $?"; tail -60 ~/check-engine-$(date +%F).txt
```

- İlk 14 gecenin yarısında motor bilerek **kapalı** çalışır (tek/çift gün sırası). Bu gecelerde sonuç `AB_OFF`
  görünür: **normal**. Böylece motorun botu yavaşlatıp yavaşlatmadığı ölçülür.
- "GÜNLÜK HEDEF" bölümü her defterin günlük sonucunu ve %1 hedefine göre durumunu gösterir.
- Çıktıyı sakla; Claude'a yazabildiğinde yapıştır. 14. geceden sonra bir kez:
  `sudo bash ~/tb-engine-4962209.sh --ab-report > ~/engine-ab.txt 2>&1`

## 4. Acil durumlar

**Worker çalışmıyor** (`inactive` / `failed`):
```
sudo systemctl status tradingbot-worker --no-pager | head -20 > ~/worker-down-$(date +%F-%H%M).txt; sudo journalctl -u tradingbot-worker -n 80 --no-pager >> ~/worker-down-$(date +%F-%H%M).txt; sudo systemctl restart tradingbot-worker; sleep 90; systemctl is-active tradingbot-worker
```
`active` yazarsa tamam. Yine düşerse bir daha deneme; dosyayı sakla ve Claude'u bekle.

**Bellek** (`[TETİKLENDİ] bellek` ya da OOM kill > 0): worker'ı bir kez yeniden başlat (belleği boşaltır):
`sudo systemctl restart tradingbot-worker`

**Motor** (P1a sorun çıkarırsa; botu etkilemez, yalnız motoru durdurur):
`sudo systemctl stop tradingbot-engine-night.timer`

**Disk** (`df -h /opt` çıktısında boş alan 10 GB'ın altına inerse): hiçbir şeyi elle silme; çıktıyı sakla, bekle.

## 5. YAPMA

- `do-release-upgrade`, `apt upgrade`, VPS'i yeniden başlatma ("System restart required" yazısı beklesin).
- `config.yaml`'ı elle düzenleme, kapatma satırını silme ya da ikinci kez ekleme.
- Eski dağıtım betiklerini dağıtım kipinde çalıştırma (`--check` dışında).
- Hızlı formasyon hesabı ve öğrenme motorunun sonraki aşamaları **henüz kurulmaz**; Claude ile birlikte kurulacak.

## 6. Claude'a geri döndüğünde

Şunları yapıştır: `~/engine-dryrun.txt` ve `~/engine-deploy.txt` (son 40 satır), en son `~/check-engine-*.txt`
ve `~/check-bot-*.txt` (yukarıdaki süzgeçle), varsa `~/worker-down-*.txt`.
