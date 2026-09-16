# T2/M2 PAPER İŞLEM YOLU — VERİ KAYNAĞI DOĞRULAMA DÜZELTMESİ (kapanış, 2026-09-16)

Kapsam: `work/chart-analysis-v1`, 25adb9d üzerine. T2/M2 sinyal formülleri, on coin evreni, sermaye/risk ayarları, stop
geometrisi ve PAPER modu değişmedi; ana botun mevcut giriş engeli (`_entry_data_blocked`) zayıflatılmadı; grafik
düzeltmeleri ve tarihsel kayıtların değişmezliği korundu. VPS'e dağıtım YAPILMADI; VPS HEAD'i okunmadı. Tasarım/sözleşme:
`docs/STRATEGY_PAPER_DATA_SOURCE_V1.md`.

## 1. Kusurun yeniden üretimi (25adb9d) ve önce/sonra

Betik `evidence-2026-09-16/repro_trade.py` — gerçek motor turu (`tests/test_engine_v3._engine` harness'ı: ağ yok, sentetik
çerçeveler + sahte canlı snapshot) + gerçek `StrategyBook`/`FuturesLedgerV2`. Sağlayıcı taklit edilir; doğrulama yolu değil.

| senaryo | 25adb9d (`rt_before.txt`) | onarım (`rt_after.txt`) |
|---|---|---|
| S1 SPOT-only çerçeve (perp yok) | T2 ve M2 ETH/SOL futures pozisyonu AÇTI (opened=2, ret yok) | pozisyon yok; `data_rejected=2`, `rejections={"DATA_MARKET_SPOT": 2}` |
| S2 coin USDM_PERP, BTC provenansı yok | AÇTI | pozisyon yok; `DATA_BTC_PROVENANCE_MISSING` |
| S4 tur 1 doğrulanmış giriş → pozisyonlar kapatıldı → tur 2 indirme başarısız (bayat çerçeve, bu turda provenans yok) | yeniden giriş yok (brief/marks yolu tesadüfen engelledi) | yeniden giriş yok, artık GEREKÇELİ: `DATA_PROVENANCE_MISSING` |
| S5 açık pozisyon; tur 2 SPOT ikamesi + SPOT 1h fitili stop altında (±20% süzgeci içinde), perp mark stop üstünde | 4 pozisyon `stop` ile KAPANDI (spot fitili futures stop'unu tetikledi) | pozisyonlar açık kaldı; `DATA_MARKET_SPOT` reddi; kapanış 0 |

## 2. Kaynak kontrolü sağlayıcıdan ortak uygulayıcıya

`perp_frames` → `_frame_provenance` (market/entry_ok) → **`_bind_provenance`** (tur kimliği + dilim başına son bar zaman
damgası + perp mark; run_symbol başarısızsa provenans silinir) → `_strategy_paper_tour` (`provenance_by_symbol`,
`_paper_marks`) → `StrategyBook.step` (**`verify_paper_data`**: provenans var, bu turun, USDM_PERP, bağlı bar ==
bellekteki bar; `entry_ok`; BTC aynı kontrol) → **`apply_action(data=DataVerdict)`** fail-closed (hüküm yoksa/ok değilse
OPEN ve CLOSE reddi; OPEN için `entry_ok`). Replay aynı fonksiyonu arşiv manifest'inden türetilen hükümle çağırır.

## 3. BTC referansı, tick high/low, açık pozisyon çıkışları

* BTC: yeni girişte zorunlu (`decide` rejim okur) — SPOT/eksik/bayat BTC → `DATA_BTC_*`, giriş yok. Açık pozisyonun kural
  kapanışında zorunlu değil (`decide` position_open iken BTC okumaz) → BTC boşluğu çıkışı engellemez (`test_6`).
* Fiyat: kâğıt defterler artık spot ticker `last` yerine **doğrulanmış USDⓈ-M perp mark** (`funding.markPrice`) kullanır;
  1h high/low yalnız USDM_PERP provenanslı çerçeveden (spot fitili tetiklemez); doğrulanmış fiyat yoksa tick yok, uydurma
  gerçekleşme yok, `data_gaps` + `PRICE_GAP` olayı, pozisyon izlenir; fiyat gelince doğrulanmış mark ile stop çalışır
  (`test_5`). Kural CLOSE sembol verisi doğrulanmışsa uygulanır; SPOT ikamesiyle kural CLOSE üretilmez (`test_6`).
* Ana botun (`ledger2`) tick yolu bu turda DEĞİŞMEDİ: `_marks` hâlâ spot ticker `last` + 1h uçları (kapsam dışı; ayrı
  karar gerektirir).

## 4. Parite ve ret kanıtı

* Doğrulanmış veri: `test_4` (perp çerçeve fiyatları spotun 2 katı, aynı zaman damgaları) — giriş perp mark'tan,
  `features.data_source` (market/tour/bar/BTC) kayıtta; tekrar turda yinelenen giriş yok; `test_7` chart-analysis
  açık/kapalı aynı sonuç; mevcut `test_strategy_paper_*` (kural, boyut, risk, evren, sayaç) ve
  `test_replay_strategy_mode_v1` (aynı `apply_action`, futures manifest) geçiyor.
* Geçersiz veri: `test_1`, `test_2` (`apply_action` hükümsüz/kötü hükümle REJECTED, saf sözleşme kodları), `test_3`,
  `test_8` (spot arşivi `DATA_MARKET_SPOT`, manifest'siz `DATA_ARCHIVE_MANIFEST_MISSING`).

## 5. İzlenebilirlik ve geçmiş

Özet dosyalarında `counters.data_rejected` (kalıcı), `rejections[DATA_*]`, `data_checks`, `data_gaps`,
`data_events_recent` (kalıcı kuyruk), `last_actions` (`DATA_REJECTED`/`DATA_GAP`/`OPENED`+`data`), `data_policy`; açılan
pozisyonda `features.data_source`. Bu commit'ten önceki T2/M2 işlemlerinde kaynak kanıtı YOK → "bilinmiyor"; topluca
temiz/geçersiz ilan edilmedi; defter/bakiye/açık pozisyon/ileri test başlangıcı sıfırlanmadı. VPS geçmişi okunmadı.

## 6. Test ve CI kapsamı; VPS'te doğrulanmayan

* Yerel: `tests/test_strategy_paper_data_source_v1.py` 9 test; ilgili eski testler (`test_strategy_paper_*`,
  `test_replay_strategy_mode_v1`, `test_chart_analysis_v1*`, `test_dashboard`, `test_engine_v3`, `test_entry_universe`,
  mum/grafik/rejim motor testleri) 126 geçti. `test_f9_*`'daki "SPOT ile açılan pozisyon" beklentisi kaldırıldı, sıfır
  yanlış giriş beklentisi eklendi. Test harness'ında `_install` artık doğrulanmış USDM_PERP ilan eder (varsayılan);
  `market=None/btc_market=None` SPOT/eksik senaryoları kurar.
* Tam paket ve CI: devir notunda (`C:\Users\berke\trading2-deploy\RESUME-paper-data-source-<sha>.md`).
* VPS: bu davranış üretimde çalıştırılmadı; gerçek Binance perp mark/fetch_funding_rate yolu yalnız kod/CI ile doğrulandı.
