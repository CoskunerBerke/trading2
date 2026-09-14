# ÜRETİM HATA DÜZELTME PAKETİ — taban `e1af166`

Bu paket **yalnız doğrulanmış dar hata düzeltmelerini** taşır. Araştırma araçları (replay
ekonomi kapısı, hedef mesafesi kaldıracı, backtest parite onarımı) **bu pakette YOKTUR**.
Reddedilen `k = 0,75` ayarı da yoktur.

| alan | değer |
|---|---|
| taban | `e1af16624a94d441247429fe800779798f5ba2aa` (VPS'te çalıştığı en son doğrudan doğrulanan sürüm) |
| yama | `C:\Users\berke\trading2-deploy\fixes-on-e1af166.patch` |
| yama sha256 | `10b5fb4a0e6c4ae0f5b6eecd3bbc3484fadfe13f36b27110baaa959a1050fdd5` |
| dal / worktree | `work/prod-fixes-on-e1af166` · `C:\Users\berke\wt-fixpkg` |
| dal SHA | `4e0b6ff5b27372072d1c97206ea4b14c581cbc09` |
| değişen dosya | 7 (3 kaynak, 4 test) · +236 / −17 satır |
| test | 78 geçti · `ruff check tradingbot/` temiz |

**Bu paket henüz VPS'e UYGULANMADI ve bu oturumda uygulanmayacak.**

## İçindekiler

### 1. Ekonomi kapısında fail-open — TEK SATIR, karar yolunu etkiler

`tradingbot/engine_v3.py`: `if d.p_win:` → `if d.p_win is not None:`

`0.0` Python'da yanlış (falsy) sayıldığı için modelin "neredeyse kesin kayıp" dediği
durumda tahmin **sessizce düşüyor** ve kapı hiyerarşik prior'a (tipik 0,5) geri dönüyordu.
Yani en olumsuz sinyal kapıyı **en gevşek** hâline çekiyordu.

Alan kapıya ulaşmadan önce `engine_v3:1097` satırında kalibre/öğrenilmiş değerle **ezilir**;
head'in "≥ 0,5" sezgiseli orada geçerli değildir. Üretimin ölçülen `p_win` dağılımı zaten
0,5'in çok altında (karar günlüğünde medyan 0,278, minimum 0,091) ve `round(..., 3)` küçük
bir tahmini 0,0'a yuvarlayabilir.

**Aynı girdilerde ne değişir:** `p_win` 0,0'a yuvarlanmayan her adayda **hiçbir şey**.
Yalnız 0,0 durumunda kapı artık tahmini kullanır, `max(0.05, ...)` kelepçesine takar ve
negatif beklenti üretir — yani kapı **sıkılaşır**, gevşemez. Bu düzeltmeye kâr artışı
atfedilmiyor.

### 2. Panel birim hatası — yalnız gösterim

`coinhead/head.py` `expected_return_net` alanını yüzde biriminde üretir
(`|hedef1 − giriş| / giriş × 100 − maliyet`); panel aynı değeri `_cell_pct` ile bir kez daha
100 ile çarpıyordu. Gerçek karar kaydında ham 10,8412 ekranda **%1084,12** görünüyordu.
Ayrı biçimlendirici `_cell_pct_points` eklendi.

Tüketici taraması yapıldı: `dashboard/app.py` detay kartı, `obsidian_coinheads.py`,
`redteam.py` ve `opportunity.cost_in_r` birim olarak tutarlıydı. **Hiçbir kapı bu alanı
okumuyor**, bu yüzden kusur yalnız gösterimdedir.

### 3. Yanıltıcı başlıklar + eksik gerçek beklenti — yalnız gösterim

"Beklenen Net Getiri" ve "E[R]" başlıkları olasılıkla ağırlıklandırılmış bir beklenti
çağrıştırıyordu. İkisi de **plan geometrisidir**: ATR planı hedef1'i stop mesafesinin tam
2 katına kurduğu için `expected_r` on sembolde 1,952–1,972 arasında çıkar, yani ayırt edici
değildir.

| eski | yeni |
|---|---|
| "Beklenen Net Getiri" | "Plan getirisi — hedef 1 (maliyet sonrası)" |
| "E[R]" | "Plan R/R (maliyet sonrası)" |
| (yok) | **"Beklenen değer (R)"** ← `opportunity.net_expectancy_r` |

Yeni sütun hesaplanamadığında **"bilinmiyor"** yazar, `0` yazmaz.

## Uygulama (VPS'te, sahiplik ve ortam doğru şekilde)

Aşağıdakiler **çalıştırılmadı**; komutlar operatör içindir.

```
sudo -u tradingbot git -C /opt/tradingbot/app rev-parse HEAD      # e1af166 olmali
sudo -u tradingbot git -C /opt/tradingbot/app status --porcelain  # BOS olmali
sudo -u tradingbot git -C /opt/tradingbot/app apply --check /tmp/fixes-on-e1af166.patch
```

Üç kontrol de temizse yama uygulanır ve servis yeniden başlatılır. `reset --hard`,
`clean`, `chown` ya da global Git güven istisnası **KULLANILMAZ**. Yedek doğrulanmadan
servis mutasyonu yapılmaz:

```
sudo systemctl status tradingbot-backup.timer
ls -la /opt/tradingbot/data/backups | tail -3
```

Geri alma: `sudo -u tradingbot git -C /opt/tradingbot/app checkout -- <dosyalar>` ya da
mevcut `deploy/rollback.sh`. Yama state'e DOKUNMAZ.

## Paketin DIŞINDA bıraktıklarım

| değişiklik | neden dışarıda |
|---|---|
| replay `breakeven_at_mfe_r` paritesi | yalnız backtest; üretim davranışını değiştirmez |
| `economics_gate.py` çıkarımı + replay kapısı | araştırma altyapısı; üretimde davranış korunur ama gereksiz risk |
| `coin_heads.target_r_multiple` kaldıracı | araştırma aracı; varsayılan `None` ile atıl |
| `entry_rule` kancası | araştırma aracı |
| `k = 0,75` | **reddedildi** |
