# Kanıt Onarımı V1 — dağıtım kaydı (2026-09-09)

Ne dağıtıldı: `feature/evidence-repairs-v1` @ **8c99b8f** (taban 9be55d1), VPS dalı `feature/quant-evaluation-v1` üzerine
ff-only. Tasarım ve ölçüm: `docs/EVIDENCE_REPAIRS_V1.md` (release ağacında). Operatör GO'su: "GO: şimdi dağıt" (etiketleme
bayrağı açılmadı).

## Zaman çizelgesi (UTC)

| An | Olay |
|---|---|
| 11:22:52 | Doğrulanmış manuel yedek `tradingbot-manual-20260909T112252Z.tar.gz` (873 üye, sidecar OK, yollar güvenli) |
| 11:23:xx | `.last_good_commit=9be55d1`, tag `backup/vps-pre-evrepairs-v1-9be55d1`; 12 açık pozisyon parmak izi `/tmp/evr1_fp_before.json` |
| 11:23:4x | Bundle sha256 `119d89b5…a742` doğrulandı; fetch + ff-only; import/config/bayrak kontrolü; doctor OK |
| 11:23:51 | `systemctl restart` worker + dashboard; `/health/live` 200 **4 sn** sonra |
| 11:24:45 | TUR #1 başladı; 11:24:46 spot listeleme yenilendi (**1364 sembol**, `binance_spot`) |
| 11:26:41 | Tarama kapısı: **9 yalnız-vadeli aday derin analize alınmadı** |
| ~11:35 | TUR #1 bitti (626 sn); `/health/ready` 503 → **200**; HEALTHY; 16 karar, 0 açılış, 0 kapanış |

Kesinti: servis 4 sn; karar üretimi ilk soğuk tur boyunca (~10 dk, önceki dağıtımlarla aynı). NRestarts=0.

## Doğrulama (post-check, salt okunur)

* Kod: HEAD 8c99b8f, ağaç temiz. Hata/Traceback: **0** (worker + dashboard, restart sonrası).
* `state/spot_listing.json`: n=1364, `ETHUSDT` listeli, `NVDAUSDT` değil, `XAUTUSDT` listeli.
* Defter: `breakeven_at_mfe_r=1.0` diske yazıldı (11:34:44Z); seq 39, 12 açık, 27 kapanış — **deploy öncesiyle birebir**;
  hiçbir stop değişmedi (ZEN 0,95R ve ETH 0,87R eşik altı, beklenen). Equity 100,58 (unrealized).
* Karar günlüğü (deploy sonrası 66 satır): HOLD 11, FUTURES_LONG 3, SPOT_LONG 1, REDUCE 1. **FUTURES_SHORT aday yok**;
  `SHORT_DISABLED`/`NOT_SPOT_LISTED` bu turda tetiklenmedi (aday yoktu; testlerle kanıtlı, canlı gözlem sonraki turlara).
  Açık SHORT pozisyonlar (GOOGL, XAUT) HOLD — kapı yalnız yeni girişe uygulanır, çıkışlar sürüyor.
* Yalnız-vadeli 9 aday tarama aşamasında elendi → kapıya hiç ulaşmadı (istenen davranış; maliyet de düştü).

## Bayrak durumu (canlı config.yaml)

`futures_v3.allow_short=false` · `universe.require_spot_listing=true` · `universe.spot_listing_ttl_minutes=1440` ·
`futures_v3.breakeven_at_mfe_r=1.0` · `learning_v3.outcome_labeling_enabled=false` (ayrı GO ile açılır).

## Geri alma

`bash deploy/rollback.sh` (`.last_good_commit` → 9be55d1) ya da bayrakları eski değere çekip restart. State'e dokunulmaz;
zaten taşınmış bir başa-baş stop'u (`meta.be_by_mfe`) kasıtlı olarak geri alınmaz.

## Bilinen kusurlar / öğrenilenler

* `backups/manual` yalnız `tradingbot` okur: `sudo -n ls …*.tar.gz` glob'u ubuntu kabuğunda boş kalır → `sudo -n bash -c`.
  İlk predeploy denemesi bu yüzden SIDECAR_MISMATCH ile durdu; yedek zaten alınmıştı, doğrulama root kabuğunda tekrarlandı.
* `decision_funnel.json` `rolling_24h` dict; `[-1]` KeyError (post-check betiği düzeltildi).

## Sonraki adımlar

1. Sonraki turlarda `SHORT_DISABLED`/`NOT_SPOT_LISTED` canlı gözlemi (journal + `coin_heads.json` `opportunity.hard_block_codes`).
2. Etiketleme bayrağı için ayrı GO; açılınca birikmiş 2.677 aday ~10 turda etiketlenir (`entry_outcomes.jsonl`).
3. Öğrenme katmanının `entry_outcomes.jsonl`'ı tüketmesi (kalibratör fit'i için 40+ örnek) — henüz yazılmadı.
4. Kripto LONG kesitinin ileriye dönük, önceden kayıtlı ölçümü; kesitin kenarı **kanıtlanmış değil**.

---

# Ek — V1.1 dağıtımı (2026-09-09, aynı gün)

**Operatör kararı:** "hiçbir coine ya da yöne kesin engel koymayalım." V1'in sert kapıları (`SHORT_DISABLED`,
`NOT_SPOT_LISTED`) ve tarama önü eleme kaldırıldı; yerine ölçülen açık kadar **yumuşak kanıt cezası**:
`SHORT_SEGMENT_PENALTY` (`futures_v3.short_penalty_r=0.50`), `FUTURES_ONLY_SEGMENT_PENALTY`
(`universe.futures_only_penalty_r=0.35`). Ceza yalnız muhafazakâr beklentiden düşer; net beklentisi pozitif aday en
kötü araştırma boyutunda (0,25×) açılır, hiçbir zaman sıfırlanmaz; toplam yumuşak ceza tavanı 0,60R.
Commit **8163a79**; tam paket 2011 geçti / 22 atlandı.

| An (UTC) | Olay |
|---|---|
| 12:24:20 | Doğrulanmış yedek `tradingbot-manual-20260909T122420Z.tar.gz` (877 üye, sidecar OK); `.last_good_commit=8c99b8f`; tag `backup/vps-pre-evrepairs-v1-8c99b8f` |
| 12:24:4x | Bundle sha256 `aca4849a…aee5` doğrulandı; 8c99b8f → 8163a79 ff-only; bayrak kontrolü (config uyarısı 0); doctor OK |
| 12:24:47 | Restart; `/health/live` 200 **4 sn** sonra |
| 12:25:40 | TUR #1 başladı; 12:37:39 bitti (720 sn); `/health/ready` 200; HEALTHY; 22 karar, **1 açılış**, 0 kapanış; hata 0 |

**Canlı gözlem — mekanik tasarlandığı gibi çalışıyor (coin_heads.json, generated_at > deploy):**

| Aday | Karar | Kesit cezası | Muhafazakâr beklenti | Boyut çarpanı |
|---|---|---|---:|---:|
| ATOM, NEAR, ZEC (spot'ta listeli kripto) | FUTURES_LONG | yok | +0,39 / +0,74 / +0,69 | **1,00** |
| ANTHROPIC | FUTURES_LONG | yalnız-vadeli 0,35 | +0,18 | 0,62 |
| OPENAI | FUTURES_LONG | yalnız-vadeli 0,35 | +0,29 | 0,86 |
| META, LITE, CRWV | FUTURES_LONG | yalnız-vadeli 0,35 | +0,05 … +0,07 | 0,33 … 0,37 |
| AMZN | FUTURES_SHORT | SHORT 0,50 + yalnız-vadeli 0,35 → tavan 0,60 | −0,08 | **0,25** (araştırma; yasak değil) |

Sert engel kodu taşıyan aday: yok. Tarama önü eleme log satırı: yok (kaldırıldı). Spot listeleme önbelleği taze
(11:24:46Z, 1364 sembol), yeniden çekilmedi.

**İlk işlem (V1.1):** `F00040 ZEC/USDT LONG` 12:25:40Z, giriş 1264,34, stop 1140,48, hedefler 1515,03 / 1639,88,
qty 0,010, notional 13,78 USDT, marj 4,59, 3×. Kesit cezası yok, tam boyut. Açık 13, seq 40, history 27.

**V1 döneminde tetiklenen kâr koruması (12:24Z öncesi):** `meta.be_by_mfe=True` ile CL 87,72→90,42, BZ 92,30→95,08,
GOOGL SHORT 345,72→339,14 — üçü de giriş üstü/altı kilitte. Diğer 9 pozisyon eşik altı, değişmedi.

**Geri alma:** `bash deploy/rollback.sh` (→8c99b8f, sert kapılı) ya da cezaları `0.0` yapıp restart (yasak da ceza da yok).
**Sonraki:** ilk haftada cezalı adayların açılış sayısı ve sonuçları; ceza miktarlarının ölçümle güncellenmesi;
etiketleme GO'su; öğrenme katmanının `entry_outcomes.jsonl` tüketimi.
