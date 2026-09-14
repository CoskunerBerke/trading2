# research/entry_v1 — giriş araştırması paketi (V2…V14)

Bu klasör, `work/entry-research-v1` dalındaki üretim kodunu **ölçmek** için yazılmış araştırma betiklerinin,
ön kayıtlı protokollerin ve sonuç raporlarının bire bir kopyasıdır. Üretim koduna girmez; üretim modülleri
tek kaynaktır ve buradaki kural dosyaları (`strategy_rules.py`, `candle_rules.py`, `regime_rules.py`, …) o
modüllere **delege eder**. Araştırmaya özgü varyantlar (`momentum_rules.py`: EMA100, 21/42/91 gün, `t2r_*`
kontrol/komşular) üretimde YOKTUR.

## Ne yüklendi, ne yüklenmedi

| yüklendi | yüklenmedi (neden) |
|---|---|
| betikler `*.py`, protokoller `PROTOCOL_V*.md` | `state/` (511 MB replay defterleri; koşuyla yeniden üretilir) |
| `out/DENEY_V*.md` sonuç raporları, `out/*_sonuc.json`, `out/*_meta.json`, `out/meta_<run_id>.json` (koşu başına provenans: pencere, semboller, determinizm hash'i, ret sayaçları) | mum arşivleri `wt-ten/data`, `wt-2020/data` (bkz. `DATA_MANIFEST.md`) |
| `out/parity_*.log` (üretim modülü ↔ araştırma kuralı parite hash'leri) | `symbol_filters.json` (sha256 manifestte) |
| `out/v14_run_INVALID_wtten.log` — **geçersiz** ilk V14 koşusunun logu (yanlış arşiv; bkz. PROTOCOL_V14 / RESUME) | `.env`, VPS state, üretim defterleri |

Bu dosyalardaki `C:/Users/berke/...` yolları ve `<VPS_HOST>` yer tutucusu olduğu gibi bırakıldı; başka
makinede `run_rule.py` (WT, DATA, ST), `run_v13r.py`, `run_v14.py` (ROOT, CACHE, TAR), `analyze.py` (ST)
sabitlerinin düzenlenmesi gerekir.

## Protokol → rapor eşlemesi

| protokol | rapor | koşu / soru |
|---|---|---|
| PROTOCOL_V2/V3 | DENEY_V2, DENEY_V3, ARASTIRMA_KAYDI_V2, OLCUM_TEMSILI, KAPANIS | giriş süzgeçleri temeli ve ölçüm temsili |
| PROTOCOL_V4 | DENEY_V4 | mum formasyonu onayı/vetosu (4 kol) |
| PROTOCOL_V5 | DENEY_V5 | grafik formasyonları (4 kol) |
| PROTOCOL_V6 | DENEY_V6 | seans / hafta sonu kapısı |
| PROTOCOL_V7 | DENEY_V7 | BTC rejim kapısı (R1–R3) |
| PROTOCOL_V8 | DENEY_V8 | formasyonu sinyal olarak kullanma + öğrenen kollar |
| PROTOCOL_V9 | DENEY_V9 | sadeleştirme: tek kurallı EMA200 trend (T1/T2/T3) |
| — (V10, V12 mühendislik) | RESUME (docs/review) | T2 kâğıt defteri, çoklu defter; parite: `parity_v10_t2_p1.log`, `parity_v12_m2_p1.log` |
| PROTOCOL_V11 | DENEY_V11 | momentum tanımı (M1 EMA100, M2 28g, M3 91g) |
| PROTOCOL_V13R | DENEY_V13R | sağlamlık: coin çıkarma (LOO), komşu parametre, pencere kaydırma, yoğunlaşma (81 koşu) |
| PROTOCOL_V14 | DENEY_V14 | hayatta kalma yanlılığı: Ekim-2020 ilk 10 evreni (6 koşu) |

Pencereler: P1 2020-11-01→2022-08-31, P2 2022-09-01→2024-08-31, P3 2024-09-01→2026-08-31.

## Yeniden üretim

1. Üretim kodu: `work/entry-research-v1` dalı, commit `35013040ce2bac31107634828dc943aa68a406e2` (bu paketin
   ölçtüğü sürüm). `run_rule.py` `WT` sabitini o çalışma ağacına yöneltin.
2. Veri: `python -m tradingbot history-collect --market futures --timeframes 1h 4h 1d --from 2020-09-01 --to 2026-09-10 --symbols BTC/USDT ETH/USDT SOL/USDT BNB/USDT XRP/USDT LINK/USDT DOGE/USDT AVAX/USDT LTC/USDT AAVE/USDT`
   (funding serileri config `history.include_funding` ile birlikte iner). Manifest saglamaları `DATA_MANIFEST.md`.
   2020 evreni için `deploy/releases/vps-collect-2020-universe.sh` (Binance'e erişebilen bir makinede).
3. Borsa filtreleri: `symbol_filters.json` üretim motorunun `ensure_symbol_filters` yenilemesinden (exchangeInfo,
   STRICT). Araştırma bugünkü değerleri kullanır; tarihsel filtre değişimi modellenmez.
4. Tek koşu: `python run_rule.py --rule none --strategy m2_tsmom28 --run-id v11_m2_p1 --from 2020-11-01 --to 2022-08-31 --no-breakeven`
   → `out/meta_<run_id>.json` + `state/replay/<run_id>/futures_ledger.json`. Aynı girdilerle `determinism_hash`
   aynı çıkmalıdır (parite kanıtı bu hash'tir).
5. Toplu: `run_v11.py` (9 koşu), `run_v13r.py` (81 koşu; `--exclude` LOO sargısı), `run_v14.py --prepare` sonra
   `run_v14.py` (6 koşu; `--cache-dir` ile ayrı arşiv; meta `history_root` doğrulanır).
6. Rapor: `make_deney_v11.py`, `make_deney_v13r.py`, `make_deney_v14.py` → `out/DENEY_*.md` + `out/*_sonuc.json`
   (bayraklar protokolde önceden yazılı kurallarla mekanik uygulanır; yorum eklenmez).

## Bilinen sınırlamalar (raporlarda da yazılı)

* Aynı üç pencere V4'ten V14'e kadar tekrar kullanıldı; parametre/kural seçimi bu pencerelerde yapıldı.
* İşlem sayıları küçük; kâr her pencerede birkaç büyük işleme yoğun (DENEY_V13R R4).
* Hayatta kalma yanlılığı V14 ile yalnız kısmen ölçüldü (tek ay sıralaması; delist edilmiş coin verisi yok).
* 100 USDT hesap ve kaldıraç 1 ile BTC/ETH açılamıyor (STEP_ZERO_QTY / MIN_NOTIONAL) — her iki evrende aynı.
* Replay 4 saatlik bar kapanışında karar verir; canlı motor 15 dakikalık turlarla çalışır.
