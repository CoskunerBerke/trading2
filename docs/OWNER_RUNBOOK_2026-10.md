# Sahip el kitabı — 7–14 Ekim 2026

Bu belge, Claude'a yazamadığın günlerde ne yapacağını anlatır. GitHub'da açık; Claude olmadan da okunur.
Bütün komutları VPS'te sen çalıştırırsın (`ssh ubuntu@vps-5426050a.vps.ovh.net`). Bot yalnız PAPER çalışır.

## 0. Önce bil

- **Bot kendi başına işlem açar ve kapatır.** Senin bir şey yapman gerekmez. Botun kendi öğrenme modu zaten
  açık (`learning_mode`).
- **Tur düzeltmesi şu an KAPALI.** 6 Ekim 16:55'te kapatma anahtarı uygulandı (`config.yaml` 172. satır:
  `evidence_subprocess: false`). Bu satır kalsın, silme (hızlı hesap kurulumu onu kendisi tanır ve güvenle kaldırır). Bazı turlar ~40 dk sürebilir ve `--check`
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

## 2. Kurulumlar

**Öğrenme motoru (P1a): KURULDU — 7 Ekim 2026 21:20 (25/25, deneme SUCCESS).** Betik `~/tb-engine-4962209.sh`
(sha256 `6a33bb9b9a0fbf49fb80ffb08162b13b92c4f6711226a2ef0a54ce0b56890da6`). Her gece 04:37'de kendi çalışır.

**Hızlı formasyon hesabı (worker sürümü db5db96): 8 Ekim sabahı.** Uzun turları ve Box'un mum kaçırmasını düzeltir.
Betik zaten `~/tb-deploy-db5db96.sh` (sha256 `d6a843e58d12bd8717b3d8866e204a40e6834c989b922631cdcd1fa6e298da11`); 7 Ekim
akşamı deneme çalıştırması 56/56 geçti.

1. Önce motorun ilk gecesine bak (bölüm 3). Sonuç `SUCCESS` ya da `AB_OFF` olmalı.
2. Saat (TR): **07:40–09:30** (ya da 11:40–13:30). 03:00–07:39 arası başlatma.
3. Kurulum (bağlantıdan bağımsız; ekran donsa ya da bağlantı kopsa da sürer):
   ```
   sudo bash ~/tb-deploy-db5db96.sh --detach
   ```
   Sonunda **"DAĞITIM TAMAM: 8db1faf → db5db96"** görünmeli. Hata olursa betik 8db1faf'a ve kapatma satırlı config'e
   kendisi döner. Bağlantı koparsa: `sudo journalctl -u tb-deploy-db5db96 -o cat | tail -25`
4. ~1 saat sonra ve bir sonraki 4 saatlik kapanıştan (11:00/15:00) sonra:
   ```
   sudo bash ~/tb-deploy-db5db96.sh --check > ~/check-knn-$(date +%F-%H%M).txt 2>&1; grep -E "\[(tamam|TETİKLENDİ|ölçülemedi)\]|HIZLI|ön ısıt" ~/check-knn-*.txt | tail -20
   ```
   Beklenen: "HIZLI yol", ön ısıtma birkaç saniye, çakışma turu ~5–6 dk `[tamam]`, Box kaçan bar 0.
5. Kapatma (yalnız sorun olursa, **bir kez**): betiğin `--check` çıktısındaki `evidence_fast_knn: false` satırı.

Bağlanırken kopmaması için PowerShell'de: `ssh -o ServerAliveInterval=30 ubuntu@vps-5426050a.vps.ovh.net`

## 3. 8 Ekim'den itibaren her sabah — motor kontrolü

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
