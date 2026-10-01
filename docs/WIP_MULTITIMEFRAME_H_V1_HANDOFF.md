# WIP — MULTITIMEFRAME_H_V1 devir belgesi

**Tarih:** 2026-09-04 · **Durum:** kod tamam, CI yeşil, **VPS dağıtımı BEKLİYOR**.

---

## 1. Sürüm

| Alan | Değer |
| --- | --- |
| Branch | `feature/quant-evaluation-v1` |
| Başlangıç SHA (lokal = origin = VPS) | `a7e73406d7c9be14ab91e58483db653c8fe6219e` |
| Bakım commit'i (Faz 1) | `62bf96c` |
| H commit'i (Faz 2–13) | `710df45aea39c2965c9d3028074b11149c5d9735` |
| Lokal HEAD | `710df45` |
| Origin HEAD | `710df45` (eşit) |
| **VPS HEAD** | **`a7e7340` — DEĞİŞMEDİ, dağıtım yapılmadı** |
| Rollback SHA | `a7e7340` |
| CI | 3/3 **success** (`deploy-tests`, `learning-tests`, `quant-evaluation-tests`) |

## 2. Test ve kapılar

| Ölçüm | Sonuç |
| --- | --- |
| Taban (dağıtım öncesi, `a7e7340`) | **1632 passed, 22 skipped** |
| Son (`710df45`) | **1748 passed, 22 skipped, 0 failed** (307 s) |
| Yeni test | +116 (45 bakım/bağ + 70 H + 1 izolasyon) |
| Ruff | temiz |
| `compileall` | temiz |
| Gizli anahtar taraması | temiz (yalnız mevcut env-var **adları**, değer yok) |
| Determinizm | bağlam 5 koşuda birebir aynı; `config_id` kararlı ve 4 varyant için ayrı |

## 3. Tamamlanan fazlar

0 (denetim, VPS hariç) · 1 (dört bakım düzeltmesi) · 2 (çerçeve kapsamı kararı) ·
3–7 (bağlam modülü, point-in-time, mekanik tanımlar, karar sözleşmesi, varyantlar) ·
8 (değişmez snapshot + bağ) · 9 (atıf) · 10 (kapılar) · 11 (pano) · 12 (64 regresyon) ·
13 (belge + ayrı commit'ler) · 14 (CI kapısı).

## 4. Tamamlanmayan fazlar

| Faz | Durum | Neden |
| --- | --- | --- |
| **0.8–0.10** (VPS durumu, F00030 zinciri, F00031/F00032) | **YAPILMADI** | SSH anahtarı passphrase'li; oturumdan bağlanılamıyor. `vps_audit_phase0.sh` kullanıcıya verildi. |
| **15** (dağıtım) | **YAPILMADI** | Aynı sebep. Bundle + fail-closed script hazır. |
| **16** (iki tur canary) | **YAPILMADI** | Dağıtım yapılmadı. `vps_canary.sh` hazır. |

## 5. Dağıtım paketi

```
mtf_h_v1_710df45aea39.bundle     sha256 a399b5b66e5ec5e705e9dc192eaddb7de0552ef7ce892b7cc8ed61c405c62f08
vps_audit_phase0.sh              salt okunur denetim (ÖNCE bu)
vps_deploy_710df45.sh            fail-closed dağıtım
vps_canary.sh                    iki doğal tur sonrası salt okunur doğrulama
```

`vps_deploy_710df45.sh` şu koşullardan biri bozulursa **durur**: VPS HEAD `a7e7340` değil ·
çalışma ağacı kirli · bundle SHA256 uyuşmuyor · `git bundle verify` düşüyor · bundle ucu
hedef SHA değil · merge fast-forward değil · config doğrulaması PAPER/SHADOW vermiyor.
Zorlama yolu yoktur.

## 6. Sıradaki adım (tam sıra)

1. VPS'te `bash vps_audit_phase0.sh` → çıktıyı paylaş.
   Beklenen: HEAD `a7e7340`, tree temiz, H modülü/state YOK, PAPER, `applied=0`.
2. Beklenmeyen bir şey varsa (kısmi dağıtım, kirli ağaç, farklı SHA) **DUR ve raporla**.
3. `scp mtf_h_v1_710df45aea39.bundle root@…:/tmp/` → `bash vps_deploy_710df45.sh`.
4. **İki doğal tur bekle.** Tur elle tetiklenmez, işlem elle açılmaz.
5. `bash vps_canary.sh` → çıktıyı paylaş.
6. Sonuç kodu: `MULTITIMEFRAME_H_V1_SHADOW_DEPLOYED_PENDING_FIRST_H_LINK` ya da
   `…PENDING_FIRST_H_CLOSE`.

## 7. Dağıtım sonrası doğrulanacaklar

worker/dashboard active + `NRestarts=0` · `health/live=200`, `ready` doğal olarak 200 ·
PAPER + `live_trading=false` + `ALLOW_LIVE_TRADING` unset · kill switch ARMED ·
outbox pending 0 · H `mode=SHADOW`, `applied=0`, `auto_promotion=false` · giriş/çıkış
`applied=0` · yeni snapshot'lar `mtf_context` taşıyor · `dropped_unclosed`/`dropped_future`
sayaçları kapanmamış/gelecek bar kullanılmadığını gösteriyor · D→H1 kapsamı ·
H4→M15 `DATA_UNAVAILABLE_ABSTAIN` · M5/M1 isteği yok · bağ sayaçları çalışıyor ·
maliyet alanları ayrı · kapı durumları dürüst · yeni Traceback/CRITICAL/ERROR yok ·
aktif pozisyon/risk/defter parmak izi yalnız **doğal piyasa hareketiyle** değişmiş.

## 8. Bilinen sınırlamalar (dağıtımdan bağımsız)

1. H'nin kârlı olduğuna dair **hiçbir kanıt yoktur**; örneklem sıfırdır.
2. `H4→M15` kapalı (ölçülmüş gerekçe: `docs/MULTI_TIMEFRAME_LIQUIDITY_CONFIRMATION_V1.md`
   §5.2). `H1→M5` / `M15→M1` kapsam dışı.
3. VPS durumu bu oturumda **doğrulanamadı**; §1'deki VPS satırı son bilinen değerdir,
   yeniden ölçülmelidir.
4. F00030/F00031/F00032'nin güncel durumu yeniden ölçülmedi; hepsi ön-H olduğu için
   sonuçları H terfi kanıtına **giremez** (kod bunu zorlar, denetim değil).
5. 1A bağ sayaçları yalnız ileriye dönüktür; geçmiş turların bağ sağlığı bilinmez.
6. Dört varyant aynı veriyi paylaşır; çoklu karşılaştırma düzeltmesi uygulanmamıştır.

## 9. İlgili belgeler

`docs/MULTI_TIMEFRAME_LIQUIDITY_CONFIRMATION_V1.md` (tam sözleşme) ·
`docs/WIP_ENTRY_SELECTIVITY_V1_HANDOFF.md` · `docs/WEEKLY_MARKET_STRUCTURE_V1.md` ·
`docs/VPS_DEPLOYMENT.md` · `docs/BACKUP_RESTORE.md`
