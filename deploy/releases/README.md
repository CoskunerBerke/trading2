# deploy/releases — VPS sürüm betikleri (bundle tabanlı, ROOT ile çalışır)

VPS GitHub'a erişemediği için her sürüm bir **git bundle** + kendi kendini doğrulayan bir bash betiği olarak
taşındı. Betik: bundle sha256 → taban commit kontrolü → doğrulanmış yedek → `git fetch` + **ff-only** →
değişmez kontrolleri (kural tek kaynak, config sözleşmesi PAPER) → doctor → servis yeniden başlatma → sağlık.
Değişmezler düşerse betik durur ve geri alma komutunu yazar; kod zaten hedefteyse (yarım kalmış çalıştırma)
fetch/merge atlanır ve kontroller + restart yeniden yapılır (`tb-deploy-f25cb39.sh` ve sonrası).

Bundle dosyaları **yüklenmedi** (ikili; aynı commit'lerden yeniden üretilir):
`git bundle create tb-<hedef>.bundle <taban>..work/entry-research-v1`.

| betik | taban → hedef | bundle sha256 (yerelde üretilen) | VPS'te sonuç (kullanıcı çıktısı) |
|---|---|---|---|
| tb-deploy-0cfe3f3.sh | 1c4cba1 → 0cfe3f3de51d3e37681f87972d672f9fcfdfe91a | 7c964b8fbf8fba51e1e7e1472bf98bd37e020a8591588d1eb89a3a11d8fe361e | DEPLOY_OK 2026-09-12 15:23Z |
| tb-deploy-5fc4507.sh | 0cfe3f3 → 5fc4507cd4dee8e7c88ef8e686f419b787985dff | 17601774f44c99e102b9edecf8f4f04bb2fc5ba54fca35da0c4ff0878a757343 | DEPLOY_OK 2026-09-12 20:17Z |
| tb-deploy-0796c91.sh | 5fc4507 → 0796c914a1a5d8f02308755418518194bb84a778 | ffc961d7e28ac22ceb0bcffd8817fb43d941c0389eb1c6af6c21561d356f7c80 | DEPLOY_OK 2026-09-13 |
| tb-deploy-a619fb0.sh | 0796c91 → a619fb07e62b500101f9c9a16d8bde40aa7843a9 | 2166b01b15339f581b325b30fa678b1db0874517f17c7e9996e0c46cb316bd64 | DEPLOY_OK 2026-09-13 18:33Z |
| tb-deploy-f25cb39.sh | a619fb0 → f25cb39ebe8b91932e3e80cef058def5f4040966 | dcdedd330ff5000b4e93d4407d2d81ca805ec9462f043671ac09c704af81e23c | 1. deneme değişmez kontrolünde durdu (kontrol hatası), 2. deneme DEPLOY_OK 2026-09-13 19:55Z |
| tb-deploy-3501304.sh | f25cb39 → 35013040ce2bac31107634828dc943aa68a406e2 | d268fc2d77a6b04bce71b77547803518e0d239b00d7d7d13439c5fd026e58318 | DEPLOY_OK 2026-09-13 22:57Z; 2026-09-14 tb-check 22 turda doğrulandı |

Diğer dosyalar:

* `tb-check.sh` — salt okunur canlı kontrol: journal başlangıç satırları ve son 24 saat hata taraması, karar
  hunisi (bu tur / 24 saat), son kararlar (engel kodu, rejim, mum hükmü), ana defter, strateji defterleri
  (özkaynak, sayaçlar, retler, açık pozisyonlar — evren dışı pozisyon etiketlenir).
* `vps-collect-2020-universe.sh` — Ekim-2020 hacim sıralaması ve ilk 10 için tam seri toplama (VPS'te,
  ayrı önbellek dizini, üretime dokunmaz; parquet→csv.gz dönüşümü ile paketler).
* `generators/gen_deploy*.py` — betikleri üreten şablonlar (v5/v7/v10/v12/v13 zinciri; her biri bir öncekinin
  metnini yamalar). Yalnız belge amaçlı; başka makinede yol sabitleri düzenlenmelidir.

VPS ana makine adı kopyalarda `<VPS_HOST>` ile değiştirildi. Betikler `sudo bash ~/tb-deploy-<sha>.sh` ile
ROOT olarak çalışır, git işlemlerini `sudo -u tradingbot` ile yapar; hiçbir sır içermez (env dosyası okunmaz).
