#!/usr/bin/env bash
# trading2 — VPS dağıtımı (PAPER; gerçek para YOK). Hedef: TIP değişkeni (dal impl/knn-fast; sonra PR dalına alınır).
# Ön koşul: VPS'te çalışan kod 8db1faf (pattern kanıtı alt süreci; 2026-10-05 dağıtımı, tb-deploy-8db1faf.sh: 51/51
# değişmez) ya da onun soyu (hedefin atası). Daha eskiyse HİÇBİR ŞEYE dokunmadan durur. Bu betiği ve aşağıdaki HER komutu
# VPS'te SİZ çalıştırırsınız (betik kendiliğinden ya da uzaktan çalışmaz); "sahte VPS'te denendi" notları yerel denemedir,
# gerçek VPS sonucu değildir.
#
# YEREL config.yaml DEĞİŞİKLİĞİ (2026-10-06 13:56 UTC, sahibin alt süreç kill switch'i): config.yaml'da `history:` satırının
#    ALTINA eklenmiş TEK satır `  evidence_subprocess: false` (tb-deploy-8db1faf.sh'teki sed) TANINIR. Kabul YALNIZ
#    `git diff` tam olarak bu tek eklenen satırsa (izlenen başka dosya değişmemiş, hiçbir şey sahnelenmemiş): dağıtımda
#    doğrulanmış yedekten SONRA, worker durmuşken, ileri sarmadan hemen önce servis kullanıcısıyla
#    `git checkout -- config.yaml` ile geri alınır (deploy log'a yazılır; dağıtım öncesi baytlar
#    deploy-logs/db5db96-config.pre-deploy.yaml'da saklanır). Yeni kodda bu satır gereksizdir: alt süreç artık kod
#    varsayılanında KAPALI. GERİ ALMADA (otomatik ya da aşağıdaki elle adımlar) config.yaml dağıtım öncesi baytlarına —
#    kill switch satırı DAHİL — aynen döner: 8db1faf eskisi gibi alt süreçsiz çalışır. Başka HER yerel değişiklik (satır iki
#    kez eklenmiş, başka bir değer, başka bir dosya) → DURUR, HİÇBİR ŞEYE DOKUNULMAZ.
#
# EN ERKEN DAĞITIM (sahip kararı 2026-10-06): önce öğrenme motoru P1a kurulur (tb-engine-4962209.sh, ~9 Ekim); BU worker
#    sürümü ondan EN AZ 3 gün sonra dağıtılır. Başlatma saati: 4h kapanışından sonra hh:40 — UTC 00/04/08/12/16/20:40 =
#    Türkiye saatiyle 03/07/11/15/19/23:40 — ve yeniden başlatmadan sonraki İLK tur (spot WFO + tur; f8b05fb'de 62,7 dk)
#    bir sonraki 4h kapanışından (UTC hh+4:00) ÖNCE bitmeli. TR 03:40'ta ilk tur motor gecesiyle (01:37 UTC = TR 04:37)
#    çakışır: tercihen diğer saatler. Betik hh:00–hh:39 arasında başlatılırsa UYARI basar; DURDURMAZ.
#
# BU SÜRÜM (2026-10-06): KARAR DEĞİŞTİRMEYEN TUR DÜZELTMESİ V2 — pattern kanıtı kNN sorgusu HIZLI ve KESİN.
#    Sorun (VPS günlüğü 2026-10-06, 8db1faf): her 4h kapanışında indeks İKİ kez yayımlanır (~:10 ve ~:28–:35 UTC) ve her
#    yayımdan sonra 13 sembolün kanıtı yeniden hesaplanır: sembol başına ~75 sn (ön ısıtma 977–1.077 sn; alt süreçte de
#    süreç içinde de). Yeni sürümü bekleyen turlar 23–36 dk sürdü (tur fazı "pattern kanıtı" 881–1.756 sn; olağan tur
#    5,3–6,2 dk). 8db1faf'ın alt süreci turları KISALTMADI ve worker cgroup tepesini %97 MemoryMax'a çıkardı (alt süreç özel
#    belleği 0,5–1,5 GB) → sahip 13:56 UTC'de kapattı. Kök neden: SimilarPatternEngine.query her sorguda ~152k olayı Python
#    döngüsüyle geziyordu (olay başına pandas .iloc, _std, np.corrcoef).
#    Düzeltme (docs/TOUR_CONTENTION_V2.md): iki aşamalı KESİN sorgu. 1. aşama eski süzgeci vektörel uygular ve eski mesafenin
#    KANITLI alt sınırını hesaplar; 2. aşama adayları eski TAM sıralamanın sırasıyla üretir — mesafe yalnız gereken birkaç
#    yüz adayda ESKİ kodla — ve seçimi ESKİ döngü yapar. Dönen kanıt eski döngüyle BİT BİT AYNIDIR (komşular, sıraları,
#    mesafeler, istatistik, kodlar); yayım kodu ve kuralı, indeks içeriği, önbellek anahtarı, config değerleri AYNI.
#    Yerel ölçüm (152,5k olay, 13 seri): yayım sonrası ön ısıtma 1.125,8 sn → 5,3–5,5 sn (~205×).
#    BEKLENEN VPS SİNYALİ: "pattern kanıtı ön ısıtıldı: sürüm N, 13 sembol hesaplandı, … S sn" → S birkaç sn (bugün ~1.000);
#      yayımla çakışan turlar ~5–6 dk (bugün 23–36 dk); tur fazı "pattern kanıtı" ≈ 0 sn.
#    + history.evidence_subprocess (8db1faf'ın alt süreci) kod varsayılanı artık KAPALI (true açar; ÖNERİLMEZ — bellek).
#    + Gözden geçirme küçük bulguları (2026-10-06): zaman damgası yalnız kayıpsız int64 dönüşümüyle, dizi kurulum arızası
#      motor başına bir kez, anahtar CLI'nin kanıt motoruna da ulaşır.
#    BELLEK: motor başına ~27 MB kalıcı dizi (yayım anında eski + yeni motor birlikte ~54 MB, birkaç dakika); alt süreç yok.
#    SAAT-DUVARI ZAMANI — SAHİBİN DEĞERLENDİRMESİNE (docs/TOUR_CONTENTION_V2.md §8): kod ve kurallar aynı, zamanlama değil —
#      turlar yeni sürümü BEKLEMEZ; 4h kapanışındaki İKİNCİ indeks kurulumu ön ısıtmayla CPU/GIL için yarışmaz ve daha erken
#      bitebilir; ikisi birlikte belli bir turun bir yayımdan önce mi sonra mı okuduğunu (hangi sürümü gördüğünü) saat
#      olarak değiştirebilir — bugün de makinenin yüküyle değişir; hiçbir yayım bilerek ertelenmez / öne alınmaz. Uygun
#      görmezseniz aşağıdaki KILL SWITCH eski döngüye (ve bugünkü zamanlamaya) döner.
#    BU DAĞITIMLA WORKER'A İLK KEZ GELEN DİĞER KOD (8db1faf..hedef; önceden gözden geçirildi, karar DEĞİŞTİRMEZ):
#      M2X ayna defter kodu — config.yaml'da m2x_aggressive.enabled: false (KAPALI: defter kurulmaz, config_hash'e girmez);
#      öğrenme motoru P1a kodu — worker'da ÇALIŞMAZ (ayrı engine-app klonundaki gece birimi); çevrimdışı araştırma
#      modülleri ve cli engine-* komutları. config.yaml 8db1faf'takinden YALNIZ eklenen (kapalı) m2x_aggressive bölümüyle
#      ayrılır (değişmez #43).
#    KILL SWITCH (yalnız hızlı kNN; kod aynı kalır; kanıt yine aynı — eski olay-başına döngü, ön ısıtma yine ~1.000 sn):
#      tek satır — YALNIZ BİR KEZ uygulayın (iki kez: yinelenen anahtar → ConfigError → worker BAŞLAMAZ):
#      sudo -u tradingbot sed -i 's/^history:$/history:\n  evidence_fast_knn: false/' /opt/tradingbot/app/config.yaml && sudo systemctl restart tradingbot-worker
#      denetim: grep -c evidence_fast_knn /opt/tradingbot/app/config.yaml → 1. Başlangıç satırı "pattern kanıtı kNN sorgusu:
#      ESKİ olay-başına döngü (history.evidence_fast_knn=False)" olur. Geri açmak: o satırı silin + restart.
#    DEĞİŞMEZLER (56): 8db1faf'ın 51'i (#43 GÜNCEL: config.yaml = hedef baytları 2f79d22f…, fc63481'den onaylı iki değer +
#      KAPALI M2X bölümü, 8db1faf'tan YALNIZ M2X bölümü; #49 deponun 7 alt süreç testi yeni varsayılanla; #50 alt süreç
#      anahtarı config.yaml'da YOK, kod varsayılanı KAPALI, açık true = alt süreç, dağıtım öncesi kill switch satırı =
#      varsayılan; collector.py yalnız onaylı içerikte) + YENİ #52 deponun hızlı kNN bit-aynılık testleri dağıtılan ağaçta
#      (AYRI süreç, servis ortamıyla — TRADINGBOT_* yolları hariç —, nice 19), #53 evidence_fast_knn config.yaml'da YOK,
#      varsayılan AÇIK; kill switch satırı → eski döngü (motor kurucuları dahil), config'in başka değeri değişmez; tırnaklı
#      değer ve İKİ KEZ uygulama ConfigError, #54 config_hash 8db1faf koduyla AYNI (8db1faf'ın config'i, VPS'teki kill switch
#      satırıyla ve bu sürümün anahtarlarıyla da), #55 M2X KAPALI, #56 --check'in aradığı hızlı kNN log biçimleri.
#    --check ayrıca: "PATTERN KANITI HIZLI kNN" bölümü — etkin yol (başlangıç satırı "pattern kanıtı kNN sorgusu: HIZLI
#      yol …"), yayım başına ön ısıtma saniyesi + sembol sayısı, yayımdan sonraki turun "pattern kanıtı" faz saniyesi ve
#      toplamı, yayımla çakışan sonraki turlar, hızlı yol uyarıları, worker belleği ↔ MemoryMax. 35 dk tetiği DEĞİŞMEDİ
#      (gevşetilmedi): hızlı yol AÇIKKEN > 35 dk çakışma turu GERÇEK arıza sinyalidir. İlk tur kuralı aynı.
#    ENGINE-APP (docs/OPERATIONS.md "Sürüm kuralı"): öğrenme motoru kuruluysa (/opt/tradingbot/engine-app) dağıtımın sonunda
#      AYNI motor koduyla hedefe yeniden sabitlenir + compileall (motor kodu 4962209 ile aynı → yeni A/B dönemi AÇILMAZ);
#      --check iki SHA'yı yazar. Motor gecesi o an çalışıyorsa ya da engine-app beklenmeyen durumdaysa ATLANIR (worker
#      ETKİLENMEZ; geceler SKEW = yalnız S0/S1a/S7 olur; betik elle komutu basar).
#    Önceki sürümlerin göç adımları (yedek + karşı-olgusal net dolgu) zararsız biçimde yeniden koşar (aday 0 → NO_CHANGE).
#    ÖNCEKİ SÜRÜMLER AYNEN: f8b05fb (sahip kararı 2026-10-03) learning_mode.extra_entries: record_selectivity + Box tabanı
#      0,5 — kill switch'i aşağıda; 8db1faf'ın alt süreç kodu yerinde ama varsayılan KAPALI.
#
# İŞLETİM NOTLARI (betik bunlara DOKUNMAZ):
#   * Worker VPS'te MemoryMax=6G ile çalışır (systemd override); depodaki deploy/tradingbot-worker.service 4G der. Betik
#     birim dosyalarını kopyalamaz, systemctl set-property / daemon-reload ÇALIŞTIRMAZ ve 6G'yi SIFIRLAMAZ. Birim farkını
#     (systemctl cat tradingbot-worker ↔ depodaki dosya) incelemeden ASLA `systemctl daemon-reload` çalıştırmayın.
#     `sudo systemctl edit …` de KAYDEDİNCE aynı yeniden yüklemeyi yapar (diskteki birim yüklü olandan farklıysa o fark da
#     uygulanır): edit'ten ÖNCE `systemctl show tradingbot-worker -p NeedDaemonReload` → NeedDaemonReload=no olmalı (yes ise
#     DURUN, bana iletin) ve `sudo systemctl cat tradingbot-worker` içinde MemoryMax=6G görünmeli. `systemctl revert`
#     KULLANMAYIN: edit ve set-property ile yapılan her şeyi, 6G override'ını da siler.
#   * İki saatten KISA kesintiler (dağıtımdaki worker duruşu dahil) kesinti sayılmaz: arada kapanan 1h barlar normal
#     uygulanır, yani geçmiş tarihli (bar kapanış anında) kapanış hâlâ MÜMKÜNDÜR (docs/PROTECTIVE_MONITOR_V1.md; bilinçli).
#   * 3b0ae8e'ye geri dönmek OOM döngüsünü GERİ GETİRİR (OOM onarımı d8b0c8c); bu betiğin geri alma hedefi 8db1faf'tır.
#
# YALNIZ BU SÜRÜMÜN DÜZELTMESİNİ KAPATMAK — KILL SWITCH (eski koda dönmeden; kanıt ve kararlar zaten aynıdır):
#   /opt/tradingbot/app/config.yaml içinde  history:  altına  evidence_fast_knn: false  sonra worker restart — YALNIZ BİR KEZ:
#   tek satır:  sudo -u tradingbot sed -i 's/^history:$/history:\n  evidence_fast_knn: false/' /opt/tradingbot/app/config.yaml && sudo systemctl restart tradingbot-worker
#   → eski olay-başına döngü (8db1faf'ın sorgusu; ön ısıtma yine ~1.000 sn). Geri açmak: o satırı silin + restart.
#   (config.yaml düzenlemesi bir sonraki dağıtımda / eski koda dönüşte "yerel değişiklik" olarak görünür — aşağıya bakın.)
# 8db1faf'IN ALT SÜRECİ: bu sürümde VARSAYILAN KAPALI (config satırı gerekmez). Açmak ÖNERİLMEZ (VPS'te bellek %97, tur
#   kısalmadı); gerekirse `history:` altına  evidence_subprocess: true  + restart (tek kez; kanıt yine aynı).
# ÖNCEKİ SÜRÜMÜN (f8b05fb) KARAR ANAHTARI — öğrenme-ekstra KILL SWITCH (kod, Box tabanı 0,5 ve geri kalan her şey AYNEN):
#   /opt/tradingbot/app/config.yaml içinde  learning_mode.extra_entries: open  sonra: sudo systemctl restart tradingbot-worker
#   tek satır:  sudo -u tradingbot sed -i 's/^  extra_entries: record_selectivity /  extra_entries: open /' /opt/tradingbot/app/config.yaml && sudo systemctl restart tradingbot-worker
#   → bütün öğrenme-ekstralar fc63481'deki gibi yine açılır; açık pozisyonlar ve mevcut yalnız-kayıt kayıtları kalır (onlar
#   araştırmaya/deneyime yine girmez). Box tabanını da eski haline almak ayrı değerdir: books.b1_box_fade.min_stop_pct: 0.32.
#   (config.yaml düzenlemesi bir sonraki dağıtımda / eski koda dönüşte "yerel değişiklik" olarak görünür — aşağıya bakın.)
# YALNIZ DANIŞMANI KAPATMAK (eski koda dönmeden; katman, öğrenme modu, net etiket AYNEN sürer):
#   /opt/tradingbot/app/config.yaml içinde
#     shared_experience:
#       enabled: true
#       mode: RECORD
#       advisor_mode: OFF
#   sonra: sudo systemctl restart tradingbot-worker   (ya da drop-in: sudo systemctl edit tradingbot-worker →
#   [Service] Environment=TRADINGBOT_SHARED_EXPERIENCE_ADVISOR=off, sonra restart; env yalnız KAPATABİLİR; edit'ten önce
#   yukarıdaki NeedDaemonReload / MemoryMax=6G denetimi). Danışman
#   kurulmaz, health.json'da advisor anahtarı olmaz, ana depo ve kararlar danışmansızla bayt bayt aynıdır.
#   state/shared_experience/advice/ SİLİNMEZ (yeniden açarken doğum ve üst su işareti oradan gelir; eski kod onu okumaz).
#   (config.yaml düzenlemesi bir sonraki dağıtımda "yerel değişiklik" olarak görünür; o zaman bana haber verin.)
# ORTAK DENEYİM KATMANINI KAPATMAK (danışman da kapanır): shared_experience: {enabled: false, mode: RECORD} + restart
#   (ya da drop-in Environment=TRADINGBOT_SHARED_EXPERIENCE=off; edit'ten önce aynı denetim).
#   ÖĞRENME MODUNU KAPATMAK: learning_mode: enabled: false +
#   worker/panel restart.
# KODU 8db1faf'E GERİ ALMAK: betiğin sonundaki "ESKİ KODA dönmek" adımları (betik kararlılık düşerse bunu kendisi yapar).
#   KOD VE CONFIG BİRLİKTE: izlenen config.yaml 8db1faf'taki haline döner (m2x_aggressive bölümü yok — 8db1faf o anahtarı
#   TANIMAZ) ve dağıtım öncesi kill switch satırı (evidence_subprocess: false) AYNEN geri konur: 8db1faf'ta alt süreç
#   varsayılan AÇIKTIR, satır olmadan alt süreç geri gelir. Bu sürümün kill switch'ini (evidence_fast_knn) uyguladıysanız
#   8db1faf o anahtarı bilinmeyen anahtar olarak UYARIYLA yok sayar, ama git checkout "yerel değişiklik" diye REDDEDER: önce
#   sudo -u tradingbot git -C /opt/tradingbot/app checkout -- config.yaml
#   deploy/restore.sh iki sürümde AYNI. 8db1faf bu sürümün yazdıklarını tolere eder (bu sürüm state'e yeni dosya yazmaz;
#   M2X kapalı). engine-app'e DOKUNMAYIN: 8db1faf hedefin atasıdır (app ⊑ engine-app, SKEW yok).
#
# Kod kaynağı: herkese açık GitHub deposu (bu sürüm için bundle YOK; betiğin yanında bundle varsa durur)
# (commit önce TAM SHA ile istenir, olmazsa impl/knn-fast dalı getirilip commit onun içinde aranır;
# içerik commit kimliğine bağlıdır ve doğrulanır). VPS'te (dosya adı tb-deploy-<hedefin ilk 7 hanesi>.sh):
#   sudo bash tb-deploy-db5db96.sh --dry-run   # kontroller + yeni kodun AYRI kopyada sınanması (preflight, 56 değişmez);
#                                               # çalışan kod/state/servis/config DEĞİŞMEZ
#   sudo bash tb-deploy-db5db96.sh             # dağıt (bu terminalde)
#   sudo bash tb-deploy-db5db96.sh --detach    # dağıt, SSH'tan BAĞIMSIZ ayrı systemd görevi olarak; çıktıyı izler.
#                                               # Pencere/PC kapansa da sürer. Ctrl+C yalnız İZLEMEYİ bırakır.
#   sudo bash tb-deploy-db5db96.sh --check     # dağıtım sonrası durum + geri alma tetikleri (state'e dokunmaz; yalnız
#                                               # deploy-logs'a Box / bellek / danışman ölçüm örneği ekler; tekrar koşulabilir)
#
# Sıra: [4h saat uyarısı: UTC 00/04/08/12/16/20'den sonraki 40 dk içindeyse UYARI, durdurmaz] → kaynak → HEAD + izlenen
#       dosyalar temiz YA DA yalnız bilinen config.yaml kill switch satırı → hedef commit'in getirilmesi (TAM SHA, olmazsa
#       PR dalı) → ileri sarma + çalışan sürüm (8db1faf ya da soyu) + danışman mühür commit'i (55179f4 hedefin atası;
#       55179f4..hedef arasında mühürlü dosyalar değişmemiş, kanca dosyaları — collector.py, store.py — değişmemiş ya da
#       yalnız ONAYLI içerikte) → disk, bellek sınırı, ortam (TRADINGBOT_LEARNING_MODE yok; TRADINGBOT_SHARED_EXPERIENCE ve
#       TRADINGBOT_SHARED_EXPERIENCE_ADVISOR yok ya da yalnız off), state/mode.json PAPER, state/shared_experience
#       yazılabilir, worker MemoryMax = 6G (DEĞİLSE DURUR), engine-app durumu (yalnız bilgi) → yeni kod AYRI kopyada:
#       preflight + 56 config/kod değişmezi (servis ortamıyla) → [dry-run burada biter] → dağıtım öncesi config.yaml
#       saklanır (deploy-logs) → doğrulanmış yedek → worker'ın turlar arası beklemesi → ortamın ve config.yaml'ın son
#       denetimi → worker DURUR → [yalnız bilinen satır varsa: git checkout -- config.yaml, servis kullanıcısıyla, log'a
#       yazılır] → hızlı ileri sarma (ff-only) → state/shared_experience açılır → doğrulanmış yedek (worker durmuşken, yeni
#       kodla) → karşı-olgusal net dolgu (kuru çalışma + --apply; servis kullanıcısıyla) → yalnız-kayıt sayaç tabanı →
#       [4h saat uyarısı yeniden] → worker başlar, panel yeniden başlar → 60 sn kararlılık → engine-app yeniden sabitleme
#       (motor kuruluysa; arızası worker'ı etkilemez).
#       Yedek alınamazsa git'e ve servislere DOKUNULMAZ. Preflight ya da değişmezler düşerse çalışan kod hiç değişmez.
#       config.yaml geri alındıktan / ileri sarmadan sonra herhangi bir adım (yedek, dolgu) düşerse ya da servis kararlı
#       kalkmazsa geri alınır: worker durur → eski commit (izlenen config.yaml da onunla birlikte döner) → dağıtım öncesi
#       config.yaml baytları (kill switch satırı dahil) AYNEN geri konur → worker başlar (eski kod yeni state'i tolere
#       eder; aşağıda "ESKİ KODA dönmek"). Yedeklere ve defterlere hiçbir adımda dokunulmaz.
set -Eeuo pipefail

TIP="db5db96ead8ef13d7a7ece0c79ed9b0182ac2f5f"
BUNDLE_SHA256=""                          # bu sürüm için bundle yok (GitHub kaynağı)
# PR dalı: hedef bu dala ileri sarılır. Getirme önce TAM SHA ile; olmazsa bu dal getirilir ve commit onun içinde aranır.
BRANCH_REF="refs/heads/impl/knn-fast"   # TIP bu dalda; dağıtım bitene kadar dal SİLİNMEZ
# VPS'te çalışan sürüm (2026-10-05 dağıtımı, tb-deploy-8db1faf.sh: 51/51): 8db1faf — pattern kanıtı alt süreci (sahip
# 2026-10-06 13:56 UTC'de config satırıyla kapattı). Çalışan HEAD bunun kendisi ya da soyundan olmalı (ve hedefin atası);
# daha eski kod reddedilir. Geri alma hedefi = çalışan HEAD (VPS'te 8db1faf).
PREREQ=("8db1faf08661ad10fd76ebe7d8084fed1429a7de")
# GÖLGE DANIŞMAN MÜHRÜ (2026-09-30; DANISMAN_V1.md §7): mühür commit'i hedefin atası olmalı ve mühürlü KOD dosyaları mühürden
# hedefe kadar DEĞİŞMEMİŞ olmalı (değişiklik = advisor_v2: yeni SHA, yeni ön kayıt). DANISMAN_V1.md mühürden sonra yalnız §7'nin
# "Mühür commit'i" satırı ve canlıya alma notuyla doldurulur; onun mühür tablosunu yeni kodun değişmez bloğu denetler
# (§7 = kod = beklenen değerler).
ADV_SEAL_COMMIT="55179f4b2903bed0c58f94ef04eb11025a67355e"
ADV_SEALED_FILES=(tradingbot/shared_experience/advisor.py tradingbot/shared_experience/advisor_eval.py
                  tradingbot/shared_experience/advisor_live.py tradingbot/shared_experience/advice_store.py
                  tradingbot/shared_experience/situation.py)
# (2026-09-30) danışmanın ana depoyla buluştuğu KANCA dosyaları (toplayıcı + depo): mühürden hedefe kadar değişmemiş olmalı —
# altın test (motorla OFF ↔ RECORD: ana depo bayt bayt aynı) mühürdeki bu kodla koşuldu; değişirse yeniden koşulmadan
# dağıtılmaz (dağıtım anındaki yerine: değişmez bloğunun tümleşik OFF ↔ RECORD sınaması).
ADV_HOOK_FILES=(tradingbot/shared_experience/collector.py tradingbot/shared_experience/store.py)
# (2026-10-05/06) ONAYLI kanca farkı: collector.py'ye mühürden bu yana YALNIZ üç karar-nötr satır eklendi — etiketin
# config_hash'inde history.evidence_subprocess (34ae8d2), history.evidence_fast_knn (cfdcf28) ve m2x_aggressive (M2X)
# düşülür (motorun config_hash kuralıyla aynı; ana depo satırları değişmez). İzin YALNIZ bu içeriğe: hedefteki dosyanın git
# blob kimliği sabitli; başka her fark yine DURDURUR. Altın test (OFF ↔ RECORD ana depo bayt bayt aynı,
# tests/test_shared_experience_no_decision_change_v1.py) bu içerikle yeniden koşuldu: 13/13 (yerel, 2026-10-06).
ADV_HOOK_APPROVED=("tradingbot/shared_experience/collector.py=42a989bdaed765dfe359c5ce675df9b781ec986d")
ADVISOR_SHA_EXPECT="8a89fd7e69a2d33b"
WF_SHA_EXPECT="b34d6b7a313d24d1"
SCHEMA_SHA_EXPECT="640fd10e5d6f727c"
OOM_FIX="d8b0c8c"                         # bellek (OOM) onarımı; bundan eskiye (ör. 3b0ae8e) dönmek OOM döngüsünü geri getirir
REPO_URL="https://github.com/CoskunerBerke/trading2.git"   # herkese açık depo (bundle yoksa)

BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="$BASE/app"; VENV="$BASE/venv"; DATA="$BASE/data"; STATE="$DATA/state"
SVC_USER="${TRADINGBOT_USER:-tradingbot}"
WORKER="tradingbot-worker.service"; DASH="tradingbot-dashboard.service"
# (2026-10-02) `systemctl edit` KAYDEDİNCE systemd yapılandırmasını yeniden yükler (daemon-reload ile aynı): diskteki birim
# yüklü olandan farklıysa o fark da uygulanır. Betiğin önerdiği her `systemctl edit`in yanında basılır.
UNIT_EDIT_NOTE="systemctl edit kaydedince daemon-reload yapar — ÖNCE  systemctl show $WORKER -p NeedDaemonReload  →
  NeedDaemonReload=no olmalı (yes ise DURUN, bana iletin) ve  sudo systemctl cat $WORKER  içinde MemoryMax=6G görünmeli;
  systemctl revert KULLANMAYIN (6G override'ını da siler)"
W4H_AT=""                                 # başlangıçtaki 4h saat uyarısının saati (UTC; uyarı yoksa boş)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_SRC="$HERE/tb-${TIP:0:7}.bundle"
LOGDIR="$BASE/deploy-logs"
T7="${TIP:0:7}"
MARK_RESTART="$LOGDIR/$T7-restart-at.txt"      # yeniden başlatma anı (epoch + yerel saat) — --check pencereleri
BOX_SAMPLES="$LOGDIR/$T7-box-samples.txt"      # --check Box ölçüm örnekleri (saatlik kaçan bar oranı için)
MARK_PRE_HWM="$LOGDIR/$T7-pre-hwm.txt"         # (2026-09-29) dağıtım öncesi worker tepe belleği (hwm_mb) — --check farkı
MEM_SAMPLES="$LOGDIR/$T7-mem-samples.txt"      # (2026-09-30) --check bellek örnekleri (worker rss/hwm; saatlik büyüme hızı)
ADV_SAMPLES="$LOGDIR/$T7-advisor-samples.txt"  # (2026-09-30) --check danışman örnekleri (yetişmede lag_rows azalıyor mu)
MARK_RO_BASE="$LOGDIR/$T7-ro-base.json"        # (2026-10-03) yeniden başlatma anındaki yalnız-kayıt sayaçları (--check farkı)
# (2026-10-06) sahibin 8db1faf alt süreç kill switch satırı (tb-deploy-8db1faf.sh başlığındaki sed: history: altına TEK satır)
KS_EVC_LINE="  evidence_subprocess: false"
CFG_SAVE="$LOGDIR/$T7-config.pre-deploy.yaml"  # dağıtım öncesi config.yaml baytları (yalnız bilinen satır varsa; geri almada)
# (2026-10-06) öğrenme motoru P1a (tb-engine-4962209.sh): ayrı klon; docs/OPERATIONS.md "Sürüm kuralı" — her app sürümü onu
# AYNI motor koduyla yeni app SHA'sına yeniden sabitler (S0 SKEW: app ⊑ engine-app olmalı)
ENG="$BASE/engine-app"
ENGINE_SVC="tradingbot-engine-night.service"
ENGINE_FILES=(tradingbot/research_engine deploy/tradingbot-engine-night.service deploy/tradingbot-engine-night.timer)
MEM_EXPECT=6442450944                          # worker MemoryMax beklenen 6G (bayt)
MEM_OLD=4294967296                             # depodaki eski unit değeri 4G (bayt)
# ORTAK DENEYİM (2026-09-29): katmanın state klasörü (config varsayılanı `shared_experience.state_dir`) ve karşı-olgusal
# net dolgunun raporları. Mum çekimi (ağ) en çok CF_FETCH_TIMEOUT sn sürebilir (worker bu sırada DURMUŞ); aşılırsa
# --no-fetch ile sürdürülür (yaklaşık v1c; sonraki mumlu koşu v3'e yükseltir).
XP_DIR="$STATE/shared_experience"
ADV_DIR="$XP_DIR/advice"                       # (2026-09-30) gölge danışmanın TEK yazım yeri
CF_DRY_JSON="$DATA/cf_backfill_dry.json"
CF_APPLY_JSON="$DATA/cf_backfill_apply.json"
CF_FETCH_TIMEOUT=300
MODE="${1:-deploy}"

say() { printf '\n== %s\n' "$*"; }
ok()  { printf '   OK  %s\n' "$*"; }
die() { printf '\nDUR: %s\n' "$*" >&2; exit 1; }
as_svc() { sudo -u "$SVC_USER" "$@"; }
gitc() { as_svc git -C "$APP" "$@"; }
shorts() { local s="" p; for p in "$@"; do s+="${s:+ / }${p:0:7}"; done; printf '%s' "$s"; }

# (2026-09-29) hedef TAM SHA olmalı: yer tutucu doldurulmadan betik HİÇBİR modda çalışmaz.
[[ "$TIP" =~ ^[0-9a-f]{40}$ ]] || die "TIP tam bir commit SHA'sı değil ('$TIP'): betik yayımlanmadan önce doldurulmalı. HİÇBİR ŞEYE DOKUNULMADI"

# Servisin ortamı: unit'in Environment= satırları + EnvironmentFile. Değerler KOMUT SATIRINA KONMAZ: sudo tam komut
# satırını sistem log'una yazar (2026-09-25 VPS'te görüldü; o gün değerler boştu). Geçici dosyaya (0600, servis
# kullanıcısı) yazılır, alt süreç oradan okur; çıkışta silinir.
ENV_ARGS=()
ENVFILE=""
CF_TMP=""                                 # (2026-09-29) karşı-olgusal dolgu çıktısının geçici kopyası
load_env() {
  local e line
  for e in $(systemctl show "$WORKER" -p Environment --value 2>/dev/null); do ENV_ARGS+=("$e"); done
  if [[ -r "$BASE/env" ]]; then
    local k v
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
      k="${line%%=*}"; v="${line#*=}"
      if [[ ${#v} -ge 2 && ( ( "${v:0:1}" == '"' && "${v: -1}" == '"' ) || ( "${v:0:1}" == "'" && "${v: -1}" == "'" ) ) ]]; then
        v="${v:1:${#v}-2}"                # systemd gibi: dış tırnaklar değerin parçası değil
      fi
      ENV_ARGS+=("$k=$v")
    done < "$BASE/env"
  fi
  ENV_ARGS+=("TRADINGBOT_BASE=$BASE" "ALLOW_LIVE_TRADING=false" "MPLCONFIGDIR=$BASE/.cache-deploy-mpl")
  ENVFILE="$(mktemp /tmp/tb-deploy-env.XXXXXX)"
  chmod 600 "$ENVFILE"; printf '%s\n' "${ENV_ARGS[@]}" > "$ENVFILE"; chown "$SVC_USER" "$ENVFILE"
}
trap 'rm -f "$ENVFILE" "$CF_TMP"' EXIT
# svc_run DİZİN KOMUT...: servis kullanıcısı + servis ortamı, verilen dizinde (değerler dosyadan; komut satırında yok)
svc_run() {
  local dir="$1"; shift
  as_svc bash -c 'd="$1"; f="$2"; shift 2; while IFS= read -r l; do export "$l"; done < "$f"; cd "$d" && exec "$@"' \
    _ "$dir" "$ENVFILE" "$@"
}
py() { svc_run "$APP" "$VENV/bin/python" "$@"; }

book_snapshot() {   # defterlerin bakiye ve açık pozisyonları (salt okunur)
  py - "$STATE" <<'PY'
import json, sys, pathlib
st = pathlib.Path(sys.argv[1])
for p in [st / "futures_ledger.json"] + sorted(st.glob("*/futures_ledger.json")):
    if not p.exists():
        continue
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print("  %-24s OKUNAMADI: %s" % (p.parent.name if p.parent != st else "ana", type(e).__name__)); continue
    pos = d.get("positions") or {}
    name = "ana" if p.parent == st else p.parent.name
    print("  %-24s bakiye=%-10s acik=%d %s" % (name, str(d.get("wallet_balance", d.get("equity")))[:10], len(pos),
                                            ",".join(sorted(pos))[:80]))
    for r in (d.get("history") or [])[-3:]:
        try:
            pnl = round(float(r.get("pnl") or 0), 3)
        except (TypeError, ValueError):
            pnl = r.get("pnl")
        print("      son kapanış: %-12s açılış %s  kapanış %s  %-22s pnl=%s" % (
            r.get("symbol"), str(r.get("opened_at"))[:16], str(r.get("closed_at"))[:16], r.get("exit_reason"), pnl))
PY
}

memory_report() {   # worker ve panel cgroup belleği (şu an / tepe / sınır) + OOM sayısı
  local u cg
  _mem() { local v; v="$(cat "$1" 2>/dev/null || true)"
           if [[ "$v" =~ ^[0-9]+$ ]]; then numfmt --to=iec "$v" 2>/dev/null || echo "$v"; else echo "${v:-?}"; fi; }
  for u in "$WORKER" "$DASH"; do
    cg="/sys/fs/cgroup/system.slice/$u"
    printf '   %-30s bellek (şu an / tepe / sınır): %s / %s / %s   OOM kill: %s\n' "$u" "$(_mem "$cg/memory.current")" \
      "$(_mem "$cg/memory.peak")" "$(_mem "$cg/memory.max")" "$(awk '$1=="oom_kill"{print $2}' "$cg/memory.events" 2>/dev/null || echo ?)"
  done
}

MEMMAX=""
memmax_check() {    # worker MemoryMax: 6G beklenir. 4G ise YÜKSEK SESLE uyarır. $1=gate: 6G DEĞİLSE DURUR (2026-10-05)
  local mm h
  mm="$(systemctl show "$WORKER" -p MemoryMax --value 2>/dev/null || true)"
  MEMMAX="$mm"
  if [[ "$mm" =~ ^[0-9]+$ ]]; then
    h="$(numfmt --to=iec "$mm" 2>/dev/null || echo "$mm")"
    if (( mm == MEM_EXPECT )); then
      ok "worker MemoryMax = $h ($mm bayt; beklenen 6G)"
    elif (( mm <= MEM_OLD )); then
      cat <<EOF
   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
   !!! UYARI: worker MemoryMax = $h ($mm bayt) — BEKLENEN 6G. Worker bugün ~3,4G kullanıyor, tepesi ~4,5G:
   !!! bu sınırda OOM (bellek) ile öldürülme riski YÜKSEK; öğrenme modu kayıt dosyalarını daha hızlı büyütür.
   !!! Dağıtımdan ÖNCE sınırı 6G yapmanız önerilir (kalıcı; worker'ı yeniden başlatmaz):
   !!!     sudo systemctl set-property $WORKER MemoryMax=6G
   !!! Dağıtım bu yüzden DURDURULMADI; geri alma tetiği (tepe > %90 × MemoryMax) bu sınıra göre hesaplanır.
   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
EOF
    elif (( mm < MEM_EXPECT )); then
      echo "   UYARI: worker MemoryMax = $h ($mm bayt) — beklenen 6G (tepe ~4,5G; %90 eşiği $((mm * 9 / 10 / 1048576)) MB)"
    else
      echo "   worker MemoryMax = $h ($mm bayt; 6G'den büyük)"
    fi
  else
    echo "   UYARI: worker MemoryMax okunamadı ya da sınırsız ('${mm:-?}') — beklenen 6G"
  fi
  # (2026-10-05/06) worker bütçesi 6G'ye göre değerlendirildi (hwm ~3,5 GB; motor başına ~27 MB kNN dizisi): başka sınırda
  # dağıtılmaz.
  if [[ "${1:-}" == gate && "$mm" != "$MEM_EXPECT" ]]; then
    die "worker MemoryMax = '${mm:-?}' — bu sürüm YALNIZ MemoryMax=6G ($MEM_EXPECT bayt) ile dağıtılır: worker'ın bellek
  bütçesi (hwm ~3,5 GB, yayım anında iki indeks + kNN dizileri) 6G'ye göre değerlendirildi. Önce sınırı 6G yapın
  (kalıcı; worker'ı yeniden başlatmaz):  sudo systemctl set-property $WORKER MemoryMax=6G  — sonra betiği yeniden
  çalıştırın. HİÇBİR ŞEYE DOKUNULMADI"
  fi
}

# (2026-10-02) İLK TUR + 4h YAYIMLARI — İŞLETİM UYARISI (YALNIZ basar; hiçbir şeyi durdurmaz, hiçbir şeyi değiştirmez):
# yeniden başlatmadan sonraki ilk tur (spot WFO + tur) en uzun turdur — son dağıtımda (f8b05fb) 62,7 dk; fc63481'de 44,2 dk, sonraki turlar
# ~5,5 dk; (2026-10-03) --check ilk turu AYRI raporlar (UYARI > 60 dk), 35 dk geri alma tetiği SONRAKİ turlara — ve UTC
# 00/04/08/12/16/20'deki 4h mum kapanışından sonraki dakikalarda 4h indeks yayımıyla çakışır. Bu saatlerden sonraki
# ilk 40 dk içinde (UTC hh:00–hh:39, hh 4'ün katı; 2026-10-05: yenileyici ~:14 ve ~:34'te iki kez yayımlar) uyarır:
# dağıtımın başında ve worker yeniden başlatılmadan hemen önce.
# Hata vermez (ERR tuzağı altında da çağrılır: her komut korumalı).
tour4h_warn() {     # $1: başlangıç | "yeniden başlatma"
  local hm="" h m
  hm="$(date -u +%H:%M 2>/dev/null || true)"
  [[ "$hm" =~ ^([0-9]{2}):([0-9]{2})$ ]] || return 0
  h=$((10#${BASH_REMATCH[1]})); m=$((10#${BASH_REMATCH[2]}))
  (( h % 4 == 0 && m < 40 )) || return 0
  if [[ "${1:-}" == "yeniden başlatma" ]]; then
    cat <<EOF || true
   !!! UYARI (durdurmaz): worker şimdi (UTC $hm) yeniden başlıyor — 4h mum kapanışından (UTC 00/04/08/12/16/20) sonraki
   !!! ilk 40 dk. İlk tur (spot WFO + tur; son dağıtımda 62,7 dk) 4h indeks yayımıyla çakışıp daha da uzayabilir.
   !!! --check ilk turu AYRI raporlar (kendi UYARI sınırı 60 dk); 35 dk geri alma tetiği yalnız SONRAKİ turlara
   !!! uygulanır. İlk tur 60 dk'yı aşar ama sonraki turlar 35 dk'nın altında kalırsa bu bir kod arızası değildir;
   !!! --check çıktısını bana iletin.
EOF
  else
    W4H_AT="$hm"                    # dağıtım modunda log dosyası açılınca kaydı düşülür (uyarı o dosyadan önce basılır)
    cat <<EOF || true
   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
   !!! UYARI (durdurmaz): şu an UTC $hm — 4h mum kapanışından (UTC 00/04/08/12/16/20; Türkiye saatiyle
   !!! 03/07/11/15/19/23) sonraki ilk 40 dk (iki indeks yayımı ~:14 ve ~:34). Yeniden başlatmadan sonraki İLK tur en uzun
   !!! turdur (spot WFO + tur; son dağıtımda 62,7 dk, sonraki turlar ~5,5 dk) ve yayımla çakışırsa daha da uzayabilir
   !!! (--check ilk turu ayrı raporlar: UYARI > 60 dk; 35 dk geri alma tetiği sonraki turlara).
   !!! Öneri: dağıtımı UTC $(printf '%02d' "$h"):40'tan sonra başlatın. Şimdi çıkmak güvenli (bu noktada HİÇBİR ŞEY
   !!! değişmedi): bu terminalde Ctrl+C; --detach ile başlattıysanız: sudo systemctl stop tb-deploy-$T7
   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
EOF
  fi
  return 0
}

# TRADINGBOT_LEARNING_MODE servis ortamında OLMAMALI: off/false/0/disabled öğrenmeyi SESSİZCE kapatır (dağıtım boşa
# gider), başka her değer ConfigError'dur (worker hiç başlamaz). Unit Environment= (drop-in dahil), EnvironmentFile'lar
# ve $BASE/env taranır.
lm_env_scan() {
  local hits=() e f
  for e in "${ENV_ARGS[@]}"; do
    if [[ "$e" == TRADINGBOT_LEARNING_MODE=* ]]; then hits+=("servis ortamı: $e"); fi
  done
  while IFS= read -r f; do
    [[ -n "$f" && -r "$f" ]] || continue
    if grep -qE '^[[:space:]]*(export[[:space:]]+)?TRADINGBOT_LEARNING_MODE[[:space:]]*=' "$f" 2>/dev/null; then
      hits+=("ortam dosyası: $f")
    fi
  done < <( { systemctl show "$WORKER" -p EnvironmentFiles --value 2>/dev/null | grep -oE '/[^ ]+' || true; echo "$BASE/env"; } | sort -u)
  if (( ${#hits[@]} )); then
    printf '   HATA  TRADINGBOT_LEARNING_MODE tanımlı:\n'; printf '           %s\n' "${hits[@]}"
    return 1
  fi
  ok "TRADINGBOT_LEARNING_MODE servis ortamında yok (config.yaml geçerli)"
}
lm_env_hint="off/false/0/disabled öğrenme modunu SESSİZCE kapatır, başka değer ConfigError (worker başlamaz). Satırı
  silin (dosya için: $BASE/env; unit için: sudo systemctl edit $WORKER) ve betiği yeniden çalıştırın.
  $UNIT_EDIT_NOTE"

# ORTAK DENEYİM ENV (2026-09-29): TRADINGBOT_SHARED_EXPERIENCE unit'te (drop-in dahil), EnvironmentFile'larda ve
# $BASE/env'de OLMAMALI. Varsa YÜKSEK SESLE basılır: yalnız off/false/0/disabled kabul edilir (katman KAPALI kalır —
# operatörün bilinçli kapatması; dağıtımın geri kalanı sürer); başka her değer yeni kodda ConfigError'dur (worker
# BAŞLAMAZ) → dur. Boş değer yükleyicide etkisizdir (yok sayılır).
xp_env_norm() { local v="$1"; v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
                if [[ ${#v} -ge 2 && ( ( "${v:0:1}" == '"' && "${v: -1}" == '"' ) || ( "${v:0:1}" == "'" && "${v: -1}" == "'" ) ) ]]; then
                  v="${v:1:${#v}-2}"; fi
                v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"; printf '%s' "${v,,}"; }
XP_ENV_OFF=""
xp_env_scan() {
  local hits=() vals=() e f v line bad=0
  XP_ENV_OFF=""
  for e in "${ENV_ARGS[@]}"; do
    if [[ "$e" == TRADINGBOT_SHARED_EXPERIENCE=* ]]; then hits+=("servis ortamı: $e"); vals+=("${e#*=}"); fi
  done
  while IFS= read -r f; do
    [[ -n "$f" && -r "$f" ]] || continue
    while IFS= read -r line; do
      hits+=("ortam dosyası $f: TRADINGBOT_SHARED_EXPERIENCE=$line"); vals+=("$line")
    done < <(sed -nE 's/^[[:space:]]*(export[[:space:]]+)?TRADINGBOT_SHARED_EXPERIENCE[[:space:]]*=(.*)$/\2/p' "$f" 2>/dev/null || true)
  done < <( { systemctl show "$WORKER" -p EnvironmentFiles --value 2>/dev/null | grep -oE '/[^ ]+' || true; echo "$BASE/env"; } | sort -u)
  if (( ${#hits[@]} == 0 )); then
    ok "TRADINGBOT_SHARED_EXPERIENCE servis ortamında yok (config.yaml geçerli: katman RECORD)"
    return 0
  fi
  for v in "${vals[@]}"; do
    v="$(xp_env_norm "$v")"
    if [[ -z "$v" ]]; then continue; fi
    if [[ "$v" =~ ^(off|false|0|disabled)$ ]]; then XP_ENV_OFF=1; else bad=1; fi
  done
  printf '   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
  printf '   !!! TRADINGBOT_SHARED_EXPERIENCE TANIMLI (servis ortamı / drop-in / ortam dosyası):\n'
  printf '   !!!     %s\n' "${hits[@]}"
  if (( bad )); then
    printf '   !!! HATA: yalnız off/false/0/disabled kabul edilir; başka değer yeni kodda ConfigError (worker BAŞLAMAZ).\n'
    printf '   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
    return 1
  fi
  if [[ -n "$XP_ENV_OFF" ]]; then
    printf '   !!! Ortak deneyim katmanı bu ortamla KAPALI kalacak (operatör kapatması kabul edildi). Net etiket ve canlı\n'
    printf '   !!! muhasebe düzeltmesi yine dağıtılır. Katmanı açmak için satırı silin ve worker'"'"'ı yeniden başlatın.\n'
  else
    printf '   !!! Değer boş: yükleyici yok sayar (katman config.yaml ile RECORD). Satırı silmeniz önerilir.\n'
  fi
  printf '   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
  return 0
}
xp_env_hint="yalnız off/false/0/disabled kabul edilir (katmanı kapatır); başka değer ConfigError (worker başlamaz). Satırı
  silin ya da düzeltin (dosya için: $BASE/env; unit için: sudo systemctl edit $WORKER) ve betiği yeniden çalıştırın.
  $UNIT_EDIT_NOTE"

# GÖLGE DANIŞMAN ENV (2026-09-30): TRADINGBOT_SHARED_EXPERIENCE_ADVISOR unit'te (drop-in dahil), EnvironmentFile'larda ve
# $BASE/env'de OLMAMALI ya da yalnız off/false/0/disabled olmalı (yeni yükleyici: env yalnız KAPATABİLİR; başka her değer
# ConfigError → worker BAŞLAMAZ → dur). off: operatörün bilinçli danışman kapatması — YÜKSEK SESLE basılır, dağıtımın geri
# kalanı sürer (danışman KAPALI kalır). Boş değer yükleyicide etkisizdir (yok sayılır).
ADV_ENV_OFF=""
adv_env_scan() {
  local hits=() vals=() e f v line bad=0
  ADV_ENV_OFF=""
  for e in "${ENV_ARGS[@]}"; do
    if [[ "$e" == TRADINGBOT_SHARED_EXPERIENCE_ADVISOR=* ]]; then hits+=("servis ortamı: $e"); vals+=("${e#*=}"); fi
  done
  while IFS= read -r f; do
    [[ -n "$f" && -r "$f" ]] || continue
    while IFS= read -r line; do
      hits+=("ortam dosyası $f: TRADINGBOT_SHARED_EXPERIENCE_ADVISOR=$line"); vals+=("$line")
    done < <(sed -nE 's/^[[:space:]]*(export[[:space:]]+)?TRADINGBOT_SHARED_EXPERIENCE_ADVISOR[[:space:]]*=(.*)$/\2/p' "$f" 2>/dev/null || true)
  done < <( { systemctl show "$WORKER" -p EnvironmentFiles --value 2>/dev/null | grep -oE '/[^ ]+' || true; echo "$BASE/env"; } | sort -u)
  if (( ${#hits[@]} == 0 )); then
    ok "TRADINGBOT_SHARED_EXPERIENCE_ADVISOR servis ortamında yok (config.yaml geçerli: danışman advisor_mode RECORD)"
    return 0
  fi
  for v in "${vals[@]}"; do
    v="$(xp_env_norm "$v")"
    if [[ -z "$v" ]]; then continue; fi
    if [[ "$v" =~ ^(off|false|0|disabled)$ ]]; then ADV_ENV_OFF=1; else bad=1; fi
  done
  printf '   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
  printf '   !!! TRADINGBOT_SHARED_EXPERIENCE_ADVISOR TANIMLI (servis ortamı / drop-in / ortam dosyası):\n'
  printf '   !!!     %s\n' "${hits[@]}"
  if (( bad )); then
    printf '   !!! HATA: env yalnız KAPATABİLİR (off/false/0/disabled); başka değer yeni kodda ConfigError (worker BAŞLAMAZ).\n'
    printf '   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
    return 1
  fi
  if [[ -n "$ADV_ENV_OFF" ]]; then
    printf '   !!! Gölge danışman bu ortamla KAPALI kalacak (operatör kapatması kabul edildi). Ortak deneyim katmanı ve\n'
    printf '   !!! restore.sh düzeltmesi yine dağıtılır. Danışmanı açmak için satırı silin ve worker'"'"'ı yeniden başlatın.\n'
  else
    printf '   !!! Değer boş: yükleyici yok sayar (danışman config.yaml ile RECORD). Satırı silmeniz önerilir.\n'
  fi
  printf '   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
  return 0
}
adv_env_hint="env yalnız KAPATABİLİR: off/false/0/disabled kabul edilir (danışmanı kapatır); başka değer ConfigError (worker
  başlamaz). Satırı silin ya da düzeltin (dosya için: $BASE/env; unit için: sudo systemctl edit $WORKER) ve betiği yeniden
  çalıştırın.
  $UNIT_EDIT_NOTE"

# ORTAK DENEYİM KLASÖRÜ (2026-09-29): <data>/state/shared_experience servis kullanıcısı için açılabilir ve yazılabilir
# olmalı. `check`: HİÇBİR ŞEY yazmaz (yoksa state klasörünün yazılabilirliğine bakar). `ensure`: yoksa
# `install -d -o $SVC_USER -g $SVC_USER` ile açar (worker'ın UMask=0027'siyle aynı: 0750) ve servis kullanıcısıyla
# gerçek bir deneme dosyası yazıp siler.
xp_dir_check() {
  local how="$1" own
  if [[ -e "$XP_DIR" && ! -d "$XP_DIR" ]]; then
    echo "   HATA  $XP_DIR var ama klasör değil"; return 1
  fi
  if [[ -d "$XP_DIR" ]]; then
    own="$(stat -c '%U:%G %a' "$XP_DIR" 2>/dev/null || echo '?')"
    if ! as_svc test -w "$XP_DIR" -a -x "$XP_DIR"; then
      echo "   HATA  $XP_DIR servis kullanıcısı ($SVC_USER) için yazılabilir değil ($own)"; return 1
    fi
  else
    if ! as_svc test -w "$STATE" -a -x "$STATE"; then
      echo "   HATA  $XP_DIR yok ve $STATE servis kullanıcısı ($SVC_USER) için yazılabilir değil — açılamaz"; return 1
    fi
    if [[ "$how" != ensure ]]; then
      ok "state/shared_experience yok; servis kullanıcısı açabilir (dağıtımda install -d ile açılacak)"; return 0
    fi
    install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$XP_DIR" || { echo "   HATA  install -d $XP_DIR başarısız"; return 1; }
    own="$(stat -c '%U:%G %a' "$XP_DIR" 2>/dev/null || echo '?')"
    echo "   state/shared_experience açıldı ($own)"
  fi
  if [[ "$how" == ensure ]]; then
    # shellcheck disable=SC2016
    as_svc bash -c 'f="$(mktemp "$1/.tb-deploy-probe.XXXXXX")" && rm -f -- "$f"' _ "$XP_DIR" \
      || { echo "   HATA  $XP_DIR içine servis kullanıcısıyla yazılamadı"; return 1; }
  fi
  ok "state/shared_experience servis kullanıcısı ($SVC_USER) için yazılabilir ($own)"
}

mode_check() {      # state/mode.json: yok ya da mode=PAPER (öğrenme yalnız PAPER'da açılır)
  py - "$STATE/mode.json" <<'PY'
import json, os, sys
p = sys.argv[1]
if not os.path.exists(p):
    print("   OK  state/mode.json yok (varsayılan PAPER)")
    sys.exit(0)
try:
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
except Exception as exc:  # noqa: BLE001
    print("   HATA  state/mode.json okunamadı (%s) — elle bakın" % type(exc).__name__)
    sys.exit(1)
m = d.get("mode") if isinstance(d, dict) else None
lop = d.get("live_order_path_enabled") if isinstance(d, dict) else None
if m == "PAPER" and lop is not True:
    print("   OK  state/mode.json: mode=PAPER")
    sys.exit(0)
print("   HATA  state/mode.json: mode=%r live_order_path_enabled=%r — öğrenme modu yalnız PAPER'da açılır" % (m, lop))
sys.exit(1)
PY
}

# --check: öğrenme modu durumu, defter başına K/marj/likidasyon/karşı-olgusal, dosya boyları, worker belleği (örnekler + saatlik
# büyüme), Box zamanlayıcısı ve GERİ ALMA TETİKLERİ (salt okunur; son satırlar "BOXSAMPLE ..." / "MEMSAMPLE ..." çağırana
# ölçüm örneğidir, ekrana basılmaz).
learning_report() {
  local rs_epoch="" rs_human="" tours="" wpid="" wstart="" cgcur="" et=""
  if [[ -r "$MARK_RESTART" ]]; then
    rs_epoch="$(sed -n 1p "$MARK_RESTART" || true)"; rs_human="$(sed -n 2p "$MARK_RESTART" || true)"
  fi
  # tur süreleri (yeniden başlatmadan beri; kayıt yoksa son 6 saat): worker her turdan ÖNCE "===== … — TUR #<n> =====" (n her
  # süreçte 1'den başlar) ve turun SONUNDA "🎓 ÖĞRENME: … · <saniye>s" basar. (2026-10-03) Her tur "sıra:tur no:saniye:tekrar"
  # olarak çıkarılır (tur no = önceki başlık; başlık yoksa 0): tur no 1 = yeniden başlatmadan sonraki İLK tur (WFO + soğuk
  # önbellek; AYRI raporlanır), diğerleri SONRAKİ turlar (35 dk tetiği). İlk turların hepsi + son 40 tur.
  # (2026-10-04) Süreç başlangıcı ("İzleme başladı [", her süreçte bir kez) ve tur hatası ("İzleme turu hatası": tur
  # bitmedi; worker sayacı ARTIRMAZ → aynı süreçteki tekrar YİNE "TUR #1" basar) da okunur: hata veren TUR #1'in aynı
  # süreçteki tekrarı "tekrar" işaretlenir (tekrar=1); başa "S:süreç başlangıcı:TUR #1 başlığı:tur hatası:tekrar" özeti.
  # Bu iki satır logging'den gelir: VPS'te her kayıt hem METİN ("SS:DD:ss SEVİYE ad: …") hem JSON ({"ts": …, "msg": "…")
  # basılır → ikisi ayrı sayılır ve büyüğü alınır (çift sayım yok; yalnız biri varsa o).
  tours="$(journalctl -u "$WORKER" --since "${rs_human:--6h}" -o cat --no-pager 2>/dev/null \
            | grep -aE 'TUR #[0-9]+ =====$|ÖĞRENME: .* [0-9]+(\.[0-9]+)?s$|İzleme başladı \[|İzleme turu hatası' \
            | awk 'BEGIN { T = "^[0-9][0-9]:[0-9][0-9]:[0-9][0-9] [A-Z]+ +[A-Za-z0-9_.]+: "
                           J = "^[{]\"ts\": \"[^\"]*\", \"level\": \"[A-Z]+\", \"logger\": \"[^\"]*\", \"msg\": \"" }
                   $0 ~ (T "İzleme başladı [[]") { pst++; k = ""; r = 0; last1 = 0; fail = 0; next }
                   $0 ~ (J "İzleme başladı [[]") { psj++; k = ""; r = 0; last1 = 0; fail = 0; next }
                   $0 ~ (T "İzleme turu hatası: ") { net++; fail = 1; next }
                   $0 ~ (J "İzleme turu hatası: ") { nej++; fail = 1; next }
                   /TUR #[0-9]+ =====$/ { k = $0; sub(/.*TUR #/, "", k); sub(/ .*/, "", k); h1 += (k == 1)
                                          r = (k == 1 && last1 && fail) ? 1 : 0; rt += r; last1 = (k == 1); fail = 0; next }
                   /ÖĞRENME: .* [0-9]+(\.[0-9]+)?s$/ { v = $NF; sub(/s$/, "", v); n++; K[n] = (k == "" ? 0 : k); V[n] = v
                                                       R[n] = r + 0; k = ""; r = 0; last1 = 0; fail = 0 }
                   END { printf "S:%d:%d:%d:%d ", (pst > psj ? pst : psj), h1, (net > nej ? net : nej), rt
                         for (i = 1; i <= n; i++) if (K[i] == 1 || i > n - 40) printf "%d:%s:%s:%d ", i, K[i], V[i], R[i] }' || true)"
  # (2026-09-30) bellek örnekleri yalnız AYNI süreçle karşılaştırılır: worker PID'i + süreç başlangıcı (health.json o
  # süreçten sonra yazılmış olmalı) + cgroup anlık belleği
  wpid="$(systemctl show "$WORKER" -p MainPID --value 2>/dev/null || true)"
  if [[ "$wpid" =~ ^[1-9][0-9]*$ ]]; then
    et="$(ps -o etimes= -p "$wpid" 2>/dev/null | tr -d ' ' || true)"
    if [[ "$et" =~ ^[0-9]+$ ]]; then wstart="$(( $(date +%s) - et ))"; fi
  else
    wpid=""
  fi
  cgcur="$(cat "/sys/fs/cgroup/system.slice/$WORKER/memory.current" 2>/dev/null || true)"
  local out
  out="$(py - "$STATE" "$APP" "${MEMMAX:-}" "$rs_epoch" "$tours" "$BOX_SAMPLES" "$MEM_SAMPLES" "${wpid:-0}" "${wstart:-}" \
            "${cgcur:-}" <<'PY'
import datetime as dt
import json
import os
import sys
import time

st, app, memmax, rs_epoch, tours_s, boxs, mems, wpid, wstart, cgcur = sys.argv[1:11]
now = time.time()


def rj(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        return {"__err__": type(exc).__name__}


def fl(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def epoch(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def ago(e):
    return "?" if e is None else "%.0f dk önce" % ((now - e) / 60.0)


def unreadable(d):
    """rj() okunamayan dosya için {"__err__": tür} döner: boş sayılmaz, OKUNAMADI yazılır."""
    return isinstance(d, dict) and "__err__" in d


rs = fl(rs_epoch)
warns = []
print("   (yeniden başlatma: %s)" % (dt.datetime.fromtimestamp(rs).strftime("%Y-%m-%d %H:%M:%S") if rs else "kayıt yok"))

# ---------------------------------------------------------------- öğrenme modu (health.json)
print("\n-- öğrenme modu (health.json)")
h = rj(os.path.join(st, "health.json"))
if unreadable(h):
    print("   health.json OKUNAMADI (%s)" % h["__err__"])
    warns.append("health.json okunamadı")
h = h if isinstance(h, dict) and not unreadable(h) else {}
h_at = epoch(h.get("at"))
if h:
    print("   health: durum=%s  yazıldı=%s (%s)  son tur=%ss" % (h.get("state"), h.get("at"), ago(h_at), h.get("seconds")))
lm = h.get("learning_mode")
if not isinstance(lm, dict):
    print("   learning_mode: health.json'da YOK — yeni worker ilk turunu henüz bitirmedi (WFO + tur 1 saati aşabilir)"
          " ya da öğrenme kapalı")
    warns.append("health.json'da learning_mode yok")
    lm = {}
else:
    print("   aktif=%s  neden=%s  durum değişimi=%s  öğrenme başlangıcı=%s"
          % (lm.get("active"), lm.get("reason"), lm.get("since"), lm.get("learning_mode_since")))
    if lm.get("active") is not True:
        warns.append("öğrenme modu AKTİF DEĞİL (neden %s)" % lm.get("reason"))
    mem = lm.get("memory") if isinstance(lm.get("memory"), dict) else {}
    print("   bellek: rss_mb=%s  hwm_mb=%s  deneyim önbelleği satırları=%s"
          % (mem.get("rss_mb"), mem.get("hwm_mb"), mem.get("exp_cache_rows")))
    tm = mem.get("trade_memory") if isinstance(mem.get("trade_memory"), dict) else {}
    print("   trade_memory okuyucusu: mb=%s  tam okuma=%s  artımlı okuma=%s  temiz=%s"
          % (tm.get("mb"), tm.get("full_loads"), tm.get("incremental_loads"), tm.get("clean")))
    if tm and tm.get("clean") is not True:
        warns.append("trade_memory okuyucusu temiz değil (clean=%s; eski okuyucuya düşüyor)" % tm.get("clean"))
    pb = lm.get("policy_basis") if isinstance(lm.get("policy_basis"), dict) else {}
    if pb.get("status") == "LOST":
        # görünüm öğrenme başladıktan sonra kayboldu; motor kirli öğreniciden YENİDEN KURMAZ (dördüncü doğrulama turu)
        print("   politika tabanı: KAYIP (%s; dosya durumu %s; öğrenme başlangıcı %s)"
              % (pb.get("code"), pb.get("file"), pb.get("learning_mode_since")))
        warns.append("politika tabanı KAYIP (POLICY_BASIS_LOST, dosya %s): state yedeğinden "
                     "learning_policy_basis.json (ya da .bak) geri yüklenip worker yeniden başlatılmalı; o zamana kadar "
                     "politika etiketi LEARNING_LEARNER" % pb.get("file"))
    else:
        print("   politika tabanı: seeded_at=%s  n_policy=%s  n_extra=%s  (updated_at=%s, v1_n_trades=%s)"
              % (pb.get("seeded_at"), pb.get("n_policy"), pb.get("n_extra"), pb.get("updated_at"), pb.get("v1_n_trades")))
        if not pb:
            warns.append("politika tabanı (policy_basis) yok")

# ---------------------------------------------------------------- defterler
cfg = {}
try:
    import yaml
    with open(os.path.join(app, "config.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
except Exception as exc:  # noqa: BLE001
    warns.append("config.yaml okunamadı (%s): K ve likidasyon çarpanı bilinmiyor" % type(exc).__name__)
lmc = cfg.get("learning_mode") if isinstance(cfg.get("learning_mode"), dict) else {}
lmb = lmc.get("books") if isinstance(lmc.get("books"), dict) else {}
LIQ = fl(lmc.get("liq_buffer_mult")) or 2.0
RES = fl(lmc.get("margin_reserve_pct"))
RES = RES if RES is not None else 5.0
print("\n-- defterler: açık pozisyon · K = BOYUTLAMA PAYDASI · Σmarj/E ve defterin tavanı (cüzdan − %%%.0f E)/E · "
      "likidasyon/stop (≥ %.1f×) · öğrenmede açılan (ekstra) · karşı-olgusal" % (RES, LIQ))
print("   K bir ADET TAVANI DEĞİLDİR: boyutlama paydasıdır (learning_mode.fit_size: slot marjı m_slot = (1 − rezerv) × E / K).\n"
      "   Dar stoplu işlemde kaldıraç tavana dayanır ve işlem slotun TAMAMINI kullanır; geniş stoplu işlem (kaldıraç yukarı\n"
      "   yuvarlanır) ve TP1 sonrası kısmen kapanan işlem slottan az kullanır; min-notional'a çıkarılan işlem slottan FAZLA\n"
      "   kullanabilir → açık > K OLAĞANDIR. Gerçek sınırlar (öğrenme sözleşmesi): her açılışta yeni marj ≤ serbest marj −\n"
      "   %%%.0f E, yani Σmarj ≤ cüzdan − %%%.0f E (cüzdan = gerçekleşen bakiye; kârdaki defterde tavan %%%.0f'in ÜSTÜNDE,\n"
      "   zarardakinde altındadır) ve likidasyon mesafesi ≥ %.1f × stop mesafesi. Uyarı yalnız bunlar aşılırsa (açılıştan sonra\n"
      "   gerçekleşen zarar/ücret cüzdanı ve tavanı düşürür: Σmarj'ın tavanı biraz aşması ihlal değil, 'yeni giriş yok' demektir)."
      % (RES, RES, 100.0 - RES, LIQ))


def slots_k(name):
    b = lmb.get(name) if isinstance(lmb.get(name), dict) else {}
    return b.get("slots", 20) if (lmc.get("enabled") is True and b.get("enabled") is True) else None


def liq_view(p):
    """(likidasyon mesafesi / İLK stop mesafesi, stop likidasyonun ötesinde mi). Bilinmiyorsa (None, False)."""
    e = fl(p.get("entry_avg") or p.get("entry_price"))
    s0 = fl(p.get("initial_stop") or p.get("stop"))
    s = fl(p.get("stop"))
    lq = fl(p.get("liquidation_price") or p.get("liq_price"))
    side = str(p.get("side") or "").upper()
    if not e or e <= 0 or s0 is None or lq is None or lq <= 0:
        return None, False
    ds = abs(e - s0) / e
    ratio = (abs(e - lq) / e) / ds if ds > 0 else None
    beyond = s is not None and ((side == "LONG" and s <= lq) or (side == "SHORT" and s >= lq))
    return ratio, beyond


books = [("main", "ana bot", st, None)]
idx = rj(os.path.join(st, "strategy_paper_index.json"))
if unreadable(idx):
    warns.append("strategy_paper_index.json okunamadı (%s): kâğıt defterler listelenemedi" % idx["__err__"])
for b in ((idx or {}).get("books") or []) if isinstance(idx, dict) else []:
    key = str((b or {}).get("key") or "") if isinstance(b, dict) else ""
    if key and all(c.isalnum() or c == "_" for c in key):
        books.append((str(b.get("name") or key), key, os.path.join(st, key), str(b.get("summary_file") or key + ".json")))
if os.path.isdir(os.path.join(st, "pattern_trader")):
    books.append(("pattern_trader", "pattern_trader", os.path.join(st, "pattern_trader"), "pattern_trader.json"))

for name, label, bdir, summ in books:
    led = rj(os.path.join(bdir, "futures_ledger.json"))
    if not isinstance(led, dict) or "__err__" in led:
        print("   %-30s defter %s" % (name, "yok" if led is None else "OKUNAMADI (%s)" % led.get("__err__")))
        continue
    pos = led.get("positions") or {}
    pos = list(pos.values()) if isinstance(pos, dict) else [p for p in pos if isinstance(p, dict)]
    pos = [p for p in pos if isinstance(p, dict)]
    margins = [fl(p.get("isolated_margin", p.get("margin"))) for p in pos]
    msum = sum(m for m in margins if m is not None)
    if name == "main":
        rk = rj(os.path.join(st, "risk.json"))
        eq = fl(((rk or {}).get("learning_mode") or {}).get("equity_basis") if isinstance(rk, dict) else None)
    else:
        sd = rj(os.path.join(st, summ)) if summ else None
        sd = sd if isinstance(sd, dict) else {}
        eq = fl((sd.get("summary") or {}).get("starting_equity") if isinstance(sd.get("summary"), dict) else None) \
            or fl(sd.get("starting_equity"))
    eq = eq or fl(led.get("starting_equity"))
    lt = [((p.get("meta") or {}).get("learning") if isinstance(p.get("meta"), dict) else None) for p in pos]
    n_learn = sum(1 for t in lt if isinstance(t, dict))
    n_extra = sum(1 for t in lt if isinstance(t, dict) and t.get("learning_unlocked_by"))
    # likidasyon tamponu: öğrenmede açılanlarda liq mesafesi ≥ LIQ × İLK stop mesafesi (fit_size sözleşmesi, %1 pay);
    # HER pozisyonda stop likidasyonun ötesinde olmamalı (yoksa likidasyon stoptan önce gelir)
    ratios, breach = [], []
    for p, t in zip(pos, lt):
        ratio, beyond = liq_view(p)
        sym = str(p.get("symbol") or "?")
        if isinstance(t, dict) and ratio is not None:
            ratios.append(ratio)
            if ratio < LIQ * 0.99:
                breach.append("%s liq/stop %.2f×" % (sym, ratio))
        if beyond:
            breach.append("%s stop likidasyonun ötesinde" % sym)
    if breach:
        liq_txt = "İHLAL %d" % len(breach)
    elif ratios:
        liq_txt = "en düşük %.2f×" % min(ratios)
    else:
        liq_txt = "—"
    if name == "main":
        cf = rj(os.path.join(st, "shadow_book.json"))
        rows = [t for t in (cf.get("trades") or []) if isinstance(t, dict) and t.get("book") == "main"] \
            if isinstance(cf, dict) and not unreadable(cf) else ([] if cf is None else None)
        cf_src = "shadow_book.json (book=main)"
        dropped = None
    else:
        cf = rj(os.path.join(bdir, "counterfactual_trades.json"))
        rows = [t for t in (cf.get("trades") or []) if isinstance(t, dict)] \
            if isinstance(cf, dict) and not unreadable(cf) else None
        cf_src = "counterfactual_trades.json"
        dropped = (cf.get("meta") or {}).get("dropped") if isinstance(cf, dict) and not unreadable(cf) else None
    if unreadable(cf):
        cf_txt = "karşı-olgusal: %s OKUNAMADI (%s)" % (cf_src.split(" ")[0], cf["__err__"])
        warns.append("%s: %s okunamadı" % (name, cf_src.split(" ")[0]))
    elif rows is None:
        cf_txt = "karşı-olgusal: dosya yok"
    else:
        lab = sum(1 for t in rows if isinstance(t.get("outcome"), dict))
        cf_txt = "karşı-olgusal: bekleyen %d · etiketli %d%s  [%s]" % (
            len(rows) - lab, lab, (" · düşürülen %s" % dropped) if dropped else "", cf_src)
    k = slots_k(name)
    frac = (msum / eq) if eq else None
    wal = fl(led.get("wallet_balance"))
    capf = ((wal - RES / 100.0 * eq) / eq) if (wal is not None and eq) else None     # defterin Σmarj tavanı (E'nin payı)
    extra = ""
    if name == "main":
        sp = rj(os.path.join(st, "spot_ledger.json"))
        lots = (sp or {}).get("lots") if isinstance(sp, dict) else None
        n_spot = sum(1 for v in (lots or {}).values() if v) if isinstance(lots, dict) else 0
        extra = " (+%d spot)" % n_spot if n_spot else ""
    print("   %-30s açık %3d%s  K %-4s Σmarj/E %5s (tavan %5s)  liq/stop %-14s öğrenmede %3d (ekstra %d)  %s"
          % (name, len(pos), extra, k if k is not None else "—",
             ("%.0f%%" % (frac * 100)) if frac is not None else "?",
             ("%.0f%%" % (capf * 100)) if capf is not None else "?", liq_txt, n_learn, n_extra, cf_txt))
    if frac is not None and capf is not None and frac > capf + 0.005:
        warns.append("%s: Σmarj %.2f > cüzdan %.2f − %%%.0f × E %.2f (Σmarj/E %.1f%% > tavan %.1f%%): serbest marj rezervin "
                     "ALTINDA — açılıştan sonraki zarar/ücret de yapabilir; sürekliyse bana iletin"
                     % (name, msum, wal, RES, eq, frac * 100, capf * 100))
    if breach:
        warns.append("%s: likidasyon tamponu ihlali (≥ %.1f × stop beklenir): %s" % (name, LIQ, ", ".join(breach[:6])
                                                                                 + (" …" if len(breach) > 6 else "")))

# ---------------------------------------------------------------- dosya boyları
print("\n-- büyüyen kayıt dosyaları (öğrenme açıkken daha hızlı büyür)")
for fn in ("trade_memory.jsonl", "position_path.jsonl", "profitability_experiment_v1_2_events.jsonl"):
    try:
        print("   %-46s %9.2f MB" % (fn, os.path.getsize(os.path.join(st, fn)) / 1048576.0))
    except OSError:
        print("   %-46s yok" % fn)

# ---------------------------------------------------------------- worker belleği: örnekler + saatlik büyüme (2026-09-30)
mm = int(memmax) if str(memmax).isdigit() else None
mem = lm.get("memory") if isinstance(lm.get("memory"), dict) else {}
rss, hwm = fl(mem.get("rss_mb")), fl(mem.get("hwm_mb"))
pid = int(wpid) if str(wpid).isdigit() else 0
ws = fl(wstart)
cg = (int(cgcur) / 1048576.0) if str(cgcur).isdigit() else None
print("\n-- worker belleği (health.json rss/hwm; her --check bir örnek kaydeder, büyüme hızı AYNI süreçteki örneklerden)")
msample = None
if hwm is None or rss is None or not pid or h_at is None or (ws is not None and h_at < ws - 5):
    print("   örnek alınmadı: %s" % ("worker PID yok" if not pid else "health.json bu süreçten sonra yazılmadı (ilk tur "
                                    "bitmedi) ya da bellek alanı yok"))
else:
    samples = []
    try:
        with open(mems, encoding="utf-8") as fh:
            for ln in fh:
                p_ = ln.split()
                if len(p_) >= 4 and p_[1].isdigit() and int(p_[1]) == pid:
                    e_, r_, w_ = fl(p_[0]), fl(p_[2]), fl(p_[3])
                    if None not in (e_, r_, w_):
                        samples.append((e_, r_, w_))
    except OSError:
        pass
    samples.sort()
    print("   şimdi (health %s): rss %.0f MB · hwm %.0f MB%s · PID %d%s" % (
        h.get("at"), rss, hwm, (" · cgroup şu an %.0f MB" % cg) if cg is not None else "", pid,
        (" (başlangıç %s)" % dt.datetime.fromtimestamp(ws, dt.timezone.utc).isoformat(timespec="minutes")) if ws else ""))
    prev = None
    for smp in samples:                              # en yeni, ≥ 20 dk önceki örnek
        if smp[0] <= h_at - 1200:
            prev = smp
    first = samples[0] if samples and samples[0][0] <= h_at - 1200 else None

    def rate(ref):
        hrs = (h_at - ref[0]) / 3600.0
        return hrs, (rss - ref[1]) / hrs, (hwm - ref[2]) / hrs

    if prev is not None:
        hrs, r_rss, r_hwm = rate(prev)
        print("   son örnekten (%.1f sa önce): rss %+.1f MB/sa · hwm %+.1f MB/sa" % (hrs, r_rss, r_hwm))
        if first is not None and first != prev:
            hrs1, r_rss1, r_hwm1 = rate(first)
            print("   bu sürecin ilk örneğinden (%.1f sa önce): rss %+.1f MB/sa · hwm %+.1f MB/sa" % (hrs1, r_rss1, r_hwm1))
        if mm and r_hwm > 0:
            left = 0.9 * mm / 1048576.0 - hwm
            print("   bu hızla hwm %%90 × MemoryMax eşiğine ≈ %.0f sa (yalnız yargı için; tetik eşiğin kendisidir)"
                  % max(0.0, left / r_hwm))
    else:
        print("   büyüme hızı: bu süreç için ≥ 20 dk önceki örnek yok — örnek kaydedildi; --check'i ≥ 30 dk sonra yeniden "
              "çalıştırın")
    if not samples or samples[-1][0] != h_at:
        msample = "%d %d %.1f %.1f %s" % (int(h_at), pid, rss, hwm, ("%.0f" % cg) if cg is not None else "-")

# ---------------------------------------------------------------- geri alma tetikleri
print("\n-- GERİ ALMA TETİKLERİ: (1) hwm_mb > %90 × MemoryMax  (2) SONRAKİ turlar > 35 dk (ilk tur ayrı: UYARI > 60 dk)  "
      "(3) Box kaçan bar > 0/saat (iki örnek arası)")
res = []
if mm and hwm is not None:
    thr = 0.9 * mm / 1048576.0
    res.append(("bellek", hwm > thr, "hwm_mb %.0f MB / eşik %.0f MB (MemoryMax %.0f MB)" % (hwm, thr, mm / 1048576.0)))
else:
    res.append(("bellek", None, "ölçülemedi (hwm_mb=%s, MemoryMax=%s)" % (hwm, memmax or "?")))
# (2026-10-03) İLK TUR AYRI: yeniden başlatmadan sonraki ilk tur (her süreçte "TUR #1": spot WFO + soğuk önbellek; son
# dağıtımda 44,2 dk, sonraki turlar ~5,5 dk) 35 dk tetiğine GİRMEZ, kendi UYARI sınırı 60 dk; 35 dk GERİ ALMA tetiği SONRAKİ
# turlara uygulanır. Eski ölçüt (bütün turların en uzunu) bilgi olarak AYRICA basılır.
# (2026-10-04) TEKRAR ve YENİDEN BAŞLAMA: hata veren TUR #1'in AYNI süreçteki tekrarı ("tekrar") ilk tur sayılmaz, sonraki
# tur da sayılmaz: hata erken geldiyse tekrar da soğuktur (≈ ilk tur), geç geldiyse sıcaktır — günlükten ayırt edilemez →
# 35 dk tetiğine GİRMEZ, > 35 dk UYARI (ondan sonraki turlar tetiğe girer). Süreç sayısı = max(süreç başlangıcı satırı,
# tekrar olmayan "TUR #1" başlığı); > 1 → worker yeniden başlamış (çökme/OOM ya da elle yeniden başlatma): UYARI.
toks, summ_ = [], None
for x in tours_s.split():
    p_ = x.split(":")
    if p_[0] == "S":
        summ_ = [int(q) for q in p_[1:5]] if len(p_) == 5 and all(q.isdigit() for q in p_[1:5]) else None
        continue
    v_ = fl(p_[2]) if len(p_) in (3, 4) and p_[0].isdigit() and p_[1].isdigit() else None
    if v_ is not None:
        toks.append((int(p_[0]), int(p_[1]), v_, len(p_) == 4 and p_[3] == "1"))
first = [v for _i, k, v, r in toks if k == 1 and not r]
retry = [v for _i, k, v, r in toks if k == 1 and r]
later = [v for _i, k, v, _r in toks if k != 1]
ps_, h1_, ne_, rt_ = summ_ if summ_ else (0, len(first) + len(retry), 0, len(retry))
procs = max(ps_, h1_ - rt_)
if procs > 1:
    warns.append("worker %s %d süreç başlatmış (%d kez yeniden başlamış; süreç başlangıcı satırı %d, \"TUR #1\" başlığı %d): "
                 "elle yeniden başlatma (ör. KAPATMA satırı) değilse çökme/OOM döngüsü olabilir — yukarıdaki 'yeniden başlama "
                 "sayısı' (NRestarts) ve 'OOM kill' sayısına bakın; bu çıktıyı bana iletin"
                 % ("yeniden başlatmadan (dağıtım) sonra" if rs is not None else "son 6 saatte", procs, procs - 1, ps_, h1_))
if ne_:
    warns.append("%s %d tur HATA verdi ('İzleme turu hatası': tur bitmedi, worker sonraki turda yeniden dener) — hata "
                 "satırları: sudo journalctl -u %s --since '%s' | grep -A 30 'İzleme turu hatası' ; bu çıktıyı bana iletin"
                 % ("yeniden başlatmadan beri" if rs is not None else "son 6 saatte", ne_, "tradingbot-worker",
                    dt.datetime.fromtimestamp(rs, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if rs is not None
                    else "-6h"))
if retry and max(retry) > 2100:
    warns.append("ilk tur hata verdi ve aynı süreçte TEKRARLANDI: tekrar %.1f dk > 35 dk — soğuk mu (erken hata) sıcak mı "
                 "(geç hata) ayırt edilemez, geri alma tetiği DEĞİL; sonraki tur 35 dk tetiğine girer: --check'i bir tur "
                 "sonra yeniden çalıştırın ve bu çıktıyı bana iletin" % (max(retry) / 60.0))
hs_only = False
if not toks and not any(summ_ or ()) and fl(h.get("seconds")) is not None and h_at is not None and (rs is None or h_at >= rs):
    later.append(fl(h.get("seconds")))          # günlükte worker satırı yoksa: health.json'daki son tur (ilk mi ayırt edilemez)
    hs_only = True
if later:
    worst = max(later)
    res.append(("tur süresi", worst > 2100, "SONRAKİ turlar (ilk tur hariç): en uzun %.1f dk (%d tur ölçüldü; son %.1f dk)%s"
                % (worst / 60.0, len(later), later[-1] / 60.0,
                   " — günlükte tur satırı yok: health.json'daki son tur (ilk tur mu ayırt edilemedi)" if hs_only else "")))
elif first and procs > 1:
    res.append(("tur süresi", None, "sonraki tur hiç bitmedi: %d süreç yalnız ilk turunu bitirdi — worker yeniden başlıyor "
                                    "olabilir (UYARI'ya bakın)" % len(first)))
elif first or retry:
    res.append(("tur süresi", None, "yalnız İLK tur%s bitti — sonraki turlar için --check'i bir tur sonra yeniden çalıştırın"
                % ("" if first else "un TEKRARI (ilk deneme hata verdi)")))
else:
    res.append(("tur süresi", None, "yeniden başlatmadan beri biten tur yok%s"
                % ((" — %d tur HATA verdi (UYARI'ya bakın)" % ne_) if ne_ else "")))
bt = rj(os.path.join(st, "box_timer.json"))
if unreadable(bt):
    warns.append("box_timer.json okunamadı (%s)" % bt["__err__"])
bt = bt if isinstance(bt, dict) and not unreadable(bt) else {}
le = bt.get("last_eval") if isinstance(bt.get("last_eval"), dict) else {}
le_at = epoch(le.get("at"))
missed = bt.get("missed_bars")
lags = [x for x in (fl(v) for v in (bt.get("lags_s") or [])) if x is not None]
lp50, lmax = fl(bt.get("lag_p50_s")), fl(bt.get("lag_max_s"))
lsec, llag = fl(le.get("seconds")), fl(le.get("lag_s"))
print("   Box zamanlayıcısı: toplam kaçan bar=%s (BİRİKİMLİ: box_timer.json'da yeniden başlatmalar arasında korunur; tetik "
      "yalnız iki örnek arasındaki ARTIŞ)" % missed)
print("   son değerlendirme %s (%s; önceki kaçan=%s)  hata=%s" % (le.get("bar_open"), ago(le_at), le.get("missed_before"),
                                                                   bt.get("errors")))
print("   gecikme (5m mum kapanışı → değerlendirme başlangıcı; son %d değerlendirme): p50 %s sn · en çok %s sn · son %s sn;"
      "  son değerlendirme süresi (last_eval.seconds) %s sn" % (len(lags), lp50, lmax, llag, lsec))
print("   NOT: Box zamanlayıcısı ayrı süreç DEĞİL, worker sürecinde bir İŞ PARÇACIĞIDIR (threading.Thread 'box-timer',\n"
      "   engine_v3.ensure_box_timer); ana turla aynı Python GIL'ini paylaşır → tur sürerken değerlendirme UZAYABİLİR.\n"
      "   2026-09-30 VPS'te son 100 değerlendirmede gecikme 3–23 sn, süre tipik ~20 sn idi; 09:00'daki tek seferlik 213 sn'nin\n"
      "   nedeni ÖLÇÜLMEDİ (GIL rekabeti, ağ/mum çekimi ya da saat başı yedeği — ayrı süreç, tradingbot-backup.timer — olabilir).\n"
      "   Tek uzun değerlendirme arıza değildir: mum ancak gecikme + süre ≈ 10 dk'yı aşarsa kaçar; ölçü kaçan bar ORANIDIR\n"
      "   (iki --check örneği arası, yeniden başlatma sonrası; toplam sayı birikimlidir).")
if lsec is not None and (llag or 0.0) + lsec >= 480:
    warns.append("Box: son değerlendirme gecikme %.0f sn + süre %.0f sn ≥ 480 sn — ~10 dk'yı aşarsa sonraki 5m mum kaçar "
                 "(GIL/ağ baskısı)" % (llag or 0.0, lsec))
elif lmax is not None and lmax >= 480:
    warns.append("Box: gecikme en çok %.0f sn ≥ 480 sn (son %d değerlendirme) — mum kaçırmaya yakın" % (lmax, len(lags)))
sample = None
if isinstance(missed, int) and le_at is not None and (rs is None or le_at >= rs):
    samples = []
    try:
        with open(boxs, encoding="utf-8") as fh:
            for ln in fh:
                p = ln.split()
                if len(p) == 3 and all(x.lstrip("-").isdigit() for x in p):
                    samples.append(tuple(int(x) for x in p))
    except OSError:
        pass
    ref = None
    for e, m, la in samples:          # en yeni, ≥ 50 dk önceki, yeniden başlatma sonrası örnek
        if e <= now - 3000 and (rs is None or la >= rs):
            ref = (e, m, la)
    stalled = (now - le_at) > 900
    if ref is not None:
        d = missed - ref[1]
        hrs = (now - ref[0]) / 3600.0
        res.append(("Box kaçan bar", d > 0 or stalled, "%+d kaçan bar / %.1f saat = %.2f/saat%s"
                    % (d, hrs, d / hrs, "; SON DEĞERLENDİRME %.0f dk ÖNCE (zamanlayıcı durmuş olabilir)"
                       % ((now - le_at) / 60) if stalled else "")))
    elif stalled:
        res.append(("Box kaçan bar", True, "son değerlendirme %.0f dk önce — zamanlayıcı durmuş olabilir"
                    % ((now - le_at) / 60)))
    else:
        res.append(("Box kaçan bar", None, "ölçüm örneği kaydedildi; saatlik oran için --check'i ≥ 1 saat sonra "
                                           "yeniden çalıştırın"))
    sample = "%d %d %d" % (int(now), missed, int(le_at))
else:
    res.append(("Box kaçan bar", None, "yeniden başlatmadan sonra henüz değerlendirme yok (zamanlayıcı ilk turla "
                                       "başlar; WFO + tur 1 saati aşabilir)"))
fired = [r for r in res if r[1] is True]
for name, f, txt in res:
    print("   %-13s %-14s %s" % ("[TETİKLENDİ]" if f is True else ("[tamam]" if f is False else "[ölçülemedi]"), name, txt))
if first:
    f_max = max(first)
    print("   %-13s %-14s %s" % ("[UYARI > 60]" if f_max > 3600 else "[tamam]", "ilk tur",
          "yeniden başlatmadan sonraki ilk tur %.1f dk (WFO + soğuk önbellek; 35 dk tetiğine GİRMEZ, kendi sınırı 60 dk)%s"
          % (first[0] / 60.0, ("; %d süreç ilk turu (worker arada yeniden başlamış), en uzunu %.1f dk" % (len(first), f_max / 60.0))
             if len(first) > 1 else "")))
    if f_max > 3600:
        warns.append("ilk tur %.1f dk > 60 dk — geri alma tetiği DEĞİL (WFO + 4h indeks yayımı çakışması olabilir); sonraki "
                     "turlar 35 dk'nın altındaysa bu bir kod arızası değildir: çıktıyı bana iletin" % (f_max / 60.0))
else:
    print("   %-13s %-14s %s" % ("[ölçülemedi]", "ilk tur", "günlükte yeniden başlatmadan sonraki ilk turun (\"TUR #1\") satırı yok"
                                                         " (henüz bitmedi, hata verdi ya da pencere dışında)"))
if retry:
    print("   %-13s %-14s %s" % ("[UYARI > 35]" if max(retry) > 2100 else "[tamam]", "ilk tur tekrarı",
          "hata veren TUR #1'in aynı süreçteki tekrarı %.1f dk%s (soğuk/sıcak ayırt edilemez: 35 dk tetiğine GİRMEZ, sonraki "
          "turlar girer)" % (retry[-1] / 60.0, ("; %d tekrar, en uzunu %.1f dk" % (len(retry), max(retry) / 60.0))
                             if len(retry) > 1 else "")))
allt = later if hs_only else first + retry + later
if allt:
    print("   (eski ölçüt — bütün turların en uzunu: %.1f dk, %d tur%s)" % (
        max(allt) / 60.0, len(allt), "; eski ölçütle TETİKLENİRDİ: yalnız ilk tur 35 dk'yı aştı"
        if (max(allt) > 2100 and not (later and max(later) > 2100)) else ""))
for w in warns:
    print("   UYARI: %s" % w)
if fired:
    print("\n   >>> GERİ ALMA TETİĞİ: %s. Bu --check çıktısını bana iletin. Acil durumda öğrenme modunu kapatın:"
          % ", ".join(r[0] for r in fired))
    print("       config.yaml → learning_mode.enabled: false ; sudo systemctl restart tradingbot-worker tradingbot-dashboard")
else:
    print("   tetiklenen yok%s" % ((" — ama %d UYARI var (yukarıda): bu çıktıyı bana iletin" % len(warns)) if warns else ""))
if sample:
    print("BOXSAMPLE " + sample)
if msample:
    print("MEMSAMPLE " + msample)
PY
)" || { printf '%s\n' "$out"; echo "   öğrenme raporu okunamadı"; return 0; }
  printf '%s\n' "$out" | grep -vE '^(BOXSAMPLE|MEMSAMPLE) ' || true
  local s; s="$(printf '%s\n' "$out" | sed -n 's/^BOXSAMPLE //p' | tail -n 1 || true)"
  if [[ -n "$s" && -f "$MARK_RESTART" ]]; then echo "$s" >> "$BOX_SAMPLES" 2>/dev/null || true; fi
  s="$(printf '%s\n' "$out" | sed -n 's/^MEMSAMPLE //p' | tail -n 1 || true)"
  if [[ -n "$s" && -d "$LOGDIR" ]]; then echo "$s" >> "$MEM_SAMPLES" 2>/dev/null || true; fi
}

# --check (2026-10-02): TUR FAZLARI — son turun health.json "phases"ı (yalnız ölçüm; hiçbir karar okumaz; en uzun fazlar,
# pattern kanıtı alt süresi) — ve PATTERN KANITI ÖN ISITMASI — worker günlüğünde yeniden başlatmadan beri (kayıt yoksa son
# 24 sa) indeks yayımı / ön ısıtma / bırakılan / HATA satırları (tek journalctl okuması). Salt okunur. fc63481'den eski
# worker "phases" yazmaz ve ön ısıtma satırı basmaz: o zaman "yok" basılır. PREWARM_ERR_RE ve LOG_TXT_RE değişmez #44'tekilerle
# AYNI olmalı.
# GÜNLÜK BİÇİMİ (2026-10-02): worker her kaydı günlüğe İKİ kez basar — cli'nin metin işleyicisi ("SS:DD:ss SEVİYE logger:
# ileti") ve config monitoring.json_logs: true iken JSON işleyicisi ({"ts": …, "msg": "ileti", …}). Yalnız METİN satırları
# sayılır (json_logs açık da kapalı da her kayıt için tam BİR tane) ve her ifade iletinin BAŞINA bağlanır; günlükte hiç
# metin satırı yoksa (beklenmez) JSON satırları sayılır. "son hata" da aynı süzülmüş satırlardan.
PREWARM_ERR_RE='pattern kanıtı ön ısıt(ması başarısız|ılamadı|ması kurulamadı)|yayım sonrası kanca başarısız'
LOG_TXT_RE='^[0-9]{2}:[0-9]{2}:[0-9]{2} [A-Z]+ +[A-Za-z0-9_.]+: '
LOG_JSON_RE='^\{"ts": "[^"]*", "level": "[A-Z]+", "logger": "[^"]*", "msg": "'
perf_report() {
  local rs_epoch="" rs_human="" since since_txt jl lines pfx fmt n_tour n_pub n_ok n_sym n_drop n_err last=""
  if [[ -r "$MARK_RESTART" ]]; then
    rs_epoch="$(sed -n 1p "$MARK_RESTART" || true)"; rs_human="$(sed -n 2p "$MARK_RESTART" || true)"
  fi
  py - "$STATE" "$rs_epoch" <<'PY' || echo "   tur fazları okunamadı"
import datetime as dt
import json
import os
import sys

st, rs_epoch = sys.argv[1:3]
TOP = 6
LIMIT_S = 2100.0                                    # geri alma tetiği: SONRAKİ turlar > 35 dk (ilk tur ayrı, 60 dk)
LABELS = {"prep": "hazırlık", "scan": "tarama", "symbols": "semboller", "coin_heads": "coin head", "execute": "yürütme",
          "main_tick": "ana defter tiki", "strategy_books": "kâğıt defterler", "learning": "öğrenme", "charts": "grafikler",
          "state": "durum", "chart_analysis": "grafik analizi", "journal": "günlük", "exit_eval": "çıkış değerlendirmesi",
          "entry_eval": "giriş değerlendirmesi", "experiment": "deney", "wrap_up": "kapanış", "obsidian": "obsidian"}
SUB = {"pattern_evidence": ("pattern kanıtı", "coin_heads")}     # bir fazın İÇİNDE ölçülen alt süre (toplama eklenmez)


def fl(x):
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def epoch(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


print("-- tur fazları (son tur, health.json \"phases\"; yalnız ölçüm — hiçbir karar okumaz)")
try:
    with open(os.path.join(st, "health.json"), encoding="utf-8") as fh:
        h = json.load(fh)
except FileNotFoundError:
    h = None
except Exception as exc:  # noqa: BLE001
    print("   health.json OKUNAMADI (%s)" % type(exc).__name__)
    sys.exit(0)
h = h if isinstance(h, dict) else {}
ph = h.get("phases")
if not isinstance(ph, dict) or not ph:
    print("   health.json'da \"phases\" yok — çalışan worker eski kod (fc63481'den eskisi yazmaz) ya da yeni worker ilk "
          "turunu henüz bitirmedi (ilk tur WFO ile 45 dk'yı aşabilir)")
    sys.exit(0)
vals = {str(k): fl(v) for k, v in ph.items()}
bad = sorted(k for k, v in vals.items() if v is None)
main = {k: v for k, v in vals.items() if k not in SUB and v is not None}
tot = sum(main.values())
sec = fl(h.get("seconds"))
at = epoch(h.get("at"))
rs = fl(rs_epoch)
print("   son tur %s · Σ fazlar %.1f sn · yazıldı %s%s" % (
    ("%.1f sn (%.1f dk)" % (sec, sec / 60.0)) if sec is not None else "? sn", tot, h.get("at"),
    "  (yeniden başlatmadan ÖNCE yazılmış — eski sürecin turu)" if (rs is not None and at is not None and at < rs) else ""))
top = sorted(main.items(), key=lambda kv: (-kv[1], kv[0]))
for k, v in top[:TOP]:
    line = "   %-24s %8.1f sn  %5.1f%%" % (LABELS.get(k, k), v, (100.0 * v / tot) if tot > 0 else 0.0)
    subs = ["%s %.1f sn" % (lab, vals[sk]) for sk, (lab, parent) in SUB.items() if parent == k and vals.get(sk) is not None]
    print(line + (("  (içinde %s)" % ", ".join(subs)) if subs else ""))
rest = top[TOP:]
if rest:
    print("   (diğer %d faz toplam %.1f sn)" % (len(rest), sum(v for _k, v in rest)))
for sk, (lab, parent) in SUB.items():
    if vals.get(sk) is not None and parent not in dict(top[:TOP]):
        print("   %s (%s fazının içinde): %.1f sn" % (lab, LABELS.get(parent, parent), vals[sk]))
if bad:
    print("   okunamayan faz değerleri: %s" % ", ".join(bad))
if sec is not None and sec > LIMIT_S:
    print("   (bu tur > 35 dk: ilk tursa ayrı 60 dk sınırı, sonraki tursa geri alma tetiği — ikisi de öğrenme raporunda; en "
          "uzun faz yukarıda)")
PY
  since="${rs_human:--24h}"
  jl="$(journalctl -u "$WORKER" --since "$since" -o cat --no-pager 2>/dev/null \
          | grep -aE "tur fazları: |pattern indeksi yenilendi|pattern kanıtı ön ısıt|yayım sonrası kanca başarısız" || true)"
  pfx="$LOG_TXT_RE"; fmt="metin"
  lines="$(grep -aE "$pfx" <<<"$jl" || true)"
  if [[ -z "$lines" ]]; then
    pfx="$LOG_JSON_RE"; fmt="JSON"; lines="$(grep -aE "$pfx" <<<"$jl" || true)"
  fi
  n_tour="$(grep -acE "${pfx}tur fazları: " <<<"$lines" || true)"
  n_pub="$(grep -acE "${pfx}pattern indeksi yenilendi" <<<"$lines" || true)"
  n_ok="$(grep -acE "${pfx}pattern kanıtı ön ısıtıldı: " <<<"$lines" || true)"
  n_sym="$(grep -aoE "${pfx}pattern kanıtı ön ısıtıldı: sürüm [0-9]+, [0-9]+ sembol hesaplandı" <<<"$lines" \
             | awk '{s += $(NF-2)} END {print s + 0}' || true)"
  n_drop="$(grep -acE "${pfx}pattern kanıtı ön ısıtması bırakıldı" <<<"$lines" || true)"
  n_err="$(grep -acE "${pfx}(${PREWARM_ERR_RE})" <<<"$lines" || true)"
  if [[ -n "$rs_human" ]]; then since_txt="yeniden başlatmadan ($rs_human) beri"; else since_txt="son 24 sa (yeniden başlatma kaydı yok)"; fi
  echo "-- pattern kanıtı ön ısıtması (worker günlüğü, $since_txt; her kayıt BİR kez sayılır)"
  echo "   yeni kodun biten turu (\"tur fazları\" satırı) ${n_tour:-0} · indeks yayımı ${n_pub:-0} · ön ısıtma işi ${n_ok:-0}" \
       "(hesaplanan sembol ${n_sym:-0}) · bırakılan (daha yeni yayım geldi) ${n_drop:-0} · HATA ${n_err:-0}"
  if [[ "$fmt" == JSON && -n "$lines" ]]; then
    echo "   (günlükte metin biçimli satır yok: JSON satırları sayıldı)"
  fi
  if [[ "${n_err:-0}" =~ ^[0-9]+$ ]] && (( n_err > 0 )); then
    last="$(grep -aE "${pfx}(${PREWARM_ERR_RE})" <<<"$lines" | tail -n 1 | cut -c1-220 || true)"
    echo "   son hata: $last"
    echo "   UYARI: pattern kanıtı ön ısıtması $n_err kez HATA verdi — karar ETKİLENMEZ (tur kanıtı kendisi hesaplar, yalnız"
    echo "   hız kaybı). Bu çıktıyı bana iletin."
  elif [[ "${n_tour:-0}" == 0 ]]; then
    echo "   (günlükte yeni kodun tur satırı yok: ilk tur henüz bitmedi ya da günlük okunamadı)"
  elif [[ "${n_pub:-0}" == 0 ]]; then
    echo "   (henüz indeks yayımı yok: yenileyici ilk turda başlar, yeni 4h mumdan sonra yayımlar; ön ısıtma yayımdan SONRA)"
  elif [[ "${n_ok:-0}" == 0 && "${n_drop:-0}" == 0 ]]; then
    echo "   (yayım var ama ön ısıtma satırı yok: iş sürüyor ya da ısıtılacak güncel seri yoktu — tur kanıtı kendisi hesaplar)"
  else
    echo "   ön ısıtma hatası yok"
  fi
  return 0
}

# --check (2026-10-05; 2026-10-06'dan beri alt süreç varsayılan KAPALI — yayım çakışması ve worker belleği artık knn_report'ta):
# PATTERN KANITI ALT SÜRECİ. Worker günlüğünden (yeniden başlatmadan beri; tek
# journalctl okuması): etkin yol (başlangıç satırı "pattern kanıtı sorguları: ALT SÜREÇTE | süreç içinde"), alt süreçli işler
# ve en yüksek özel belleği ("alt süreç pid N, özel bellek M MB"), arıza / bellek koruması / KAPALI / eski sürüm kapatma
# sayıları; şu an canlı alt süreç (worker MainPID'in çocukları: pid, RSS, VmHWM); worker cgroup memory.peak ↔ MemoryMax.
# YAYIM ÇAKIŞMASI: süreç içindeki SONRAKİ bir tur ("tur fazları: … toplam Xs"; bitiş − toplam = başlangıç) sırasında bir
# "pattern indeksi yenilendi" satırı loglandıysa o tur çakışmadır (başlangıç önceki turun bitişinden önce sayılmaz). 35 dk tetiği DEĞİŞMEZ (öğrenme raporundaki tablo); burada
# yalnız yorum: düzeltme KAPALIYSA > 35 dk çakışma turu bilinen düzeltme öncesi örüntüdür (yine tetik), AÇIKSA gerçek arıza
# sinyalidir. İlk tur kuralı aynı (ilk tur ayrı; burada yalnız bilgi). Salt okunur.
evc_report() {
  local rs_human="" since mpid cg jf
  if [[ -r "$MARK_RESTART" ]]; then rs_human="$(sed -n 2p "$MARK_RESTART" || true)"; fi
  since="${rs_human:--24h}"
  mpid="$(systemctl show "$WORKER" -p MainPID --value 2>/dev/null || true)"
  cg="/sys/fs/cgroup/system.slice/$WORKER"
  jf="$(mktemp)" || { echo "   kanıt alt süreci raporu: geçici dosya açılamadı"; return 0; }
  journalctl -u "$WORKER" --since "$since" -o cat --no-pager 2>/dev/null \
    | grep -aE "tur fazları: |pattern indeksi yenilendi|pattern kanıtı|İzleme başladı \[|status=14/ALRM|stop-sigterm|timed out" \
    | sed -E 's/(tur fazları: ).* (toplam [0-9.]+s)$/\1\2/' > "$jf" || true
  chmod 644 "$jf" 2>/dev/null || true
  py - "${mpid:-0}" "$cg" "${MEMMAX:-}" "${rs_human:-}" "$jf" "$STATE" <<'PY' || echo "   kanıt alt süreci raporu okunamadı"
import datetime as dt
import json
import os
import re
import subprocess
import sys

mpid, cg, memmax, rs_human, jf, state = sys.argv[1:7]
TXT = re.compile(r"^([0-9]{2}):([0-9]{2}):([0-9]{2}) [A-Z]+ +[A-Za-z0-9_.]+: (.*)$")
LIMIT_S = 2100.0
recs, jrecs, day, prev = [], [], 0, None               # metin ve JSON satırları: her kayıt iki biçimde → yalnız biri
with open(jf, encoding="utf-8", errors="replace") as fh:
    jlines = fh.read().splitlines()
n_alrm = sum(1 for ln in jlines if "status=14/ALRM" in ln)            # fork deadman (systemd satırı; worker yeniden başladı)
n_stop = sum(1 for ln in jlines if "stop-sigterm" in ln)                # systemctl stop 90 sn'yi aştı
for line in jlines:
    m = TXT.match(line)
    if m:
        sod = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        if prev is not None and sod < prev - 3600:              # gece yarısı (metin satırında tarih yok)
            day += 1
        prev = sod
        recs.append((day * 86400 + sod, m.group(4)))
        continue
    if line.startswith('{"ts": '):
        try:
            d = json.loads(line)
            jrecs.append((dt.datetime.fromisoformat(str(d["ts"]).replace("Z", "+00:00")).timestamp(), str(d["msg"])))
        except (ValueError, KeyError, TypeError):
            pass
recs = recs or jrecs
mode, tours, pubs, jobs, errs = None, [], [], [], {}
ERR = (("arıza", "pattern kanıtı alt süreci arızalandı"), ("başlatılamadı", "pattern kanıtı alt süreci başlatılamadı"),
       ("başlatılmadı", "pattern kanıtı alt süreci başlatılmadı"),
       ("bellek koruması", "pattern kanıtı alt süreci bellek koruması nedeniyle kapatıldı"),
       ("KAPALI (fork asılması)", "pattern kanıtı alt süreci KAPALI"),
       ("tur süreç içine düştü", "pattern kanıtı alt süreçte hesaplanamadı"),
       ("işte çok kez yeniden kuruldu", "pattern kanıtı alt süreci bu işte"),
       ("makinede KAPALI (fork işareti)", "pattern kanıtı alt süreci bu makinede KAPALI"))   # = değişmez #51 EVC_ERR_PREFIXES
n_super = n_refork = 0
proc, k_in_proc, prev_end = 0, 0, None
for ts, msg in recs:
    if msg.startswith("İzleme başladı ["):
        proc += 1
        k_in_proc, prev_end = 0, None
        continue
    if msg.startswith("pattern kanıtı sorguları: "):
        mode = "AÇIK" if "ALT SÜREÇTE" in msg else "KAPALI"
        continue
    if msg.startswith("pattern indeksi yenilendi"):
        pubs.append(ts)
        continue
    if msg.startswith("tur fazları: "):
        m = re.search(r"toplam ([0-9]+(?:\.[0-9]+)?)s\s*$", msg)
        if m:
            k_in_proc += 1
            t0 = ts - float(m.group(1))                       # başlangıç; önceki turun bitişinden önce olamaz (saniye yuvarlaması)
            tours.append((t0 if prev_end is None else max(t0, prev_end), ts, float(m.group(1)), k_in_proc, mode))
            prev_end = ts
        continue
    m = re.search(r"alt süreç pid ([0-9]+), özel bellek ([0-9?]+) MB; alt süreçte ([0-9]+), süreç içi ([0-9]+)", msg)
    if m:
        jobs.append((int(m.group(1)), None if m.group(2) == "?" else int(m.group(2)), int(m.group(3)), int(m.group(4))))
    if "daha yeni yayımla hemen kapatıldı" in msg:
        n_super += 1
    if "alt süreç yeniden kuruldu" in msg:
        n_refork += 1
    for label, pat in ERR:
        if msg.startswith(pat):
            errs[label] = errs.get(label, 0) + 1
since = ("yeniden başlatmadan (%s) beri" % rs_human) if rs_human else "son 24 sa"
print("   etkin yol (worker başlangıç satırı): %s" % {"AÇIK": "ALT SÜREÇTE (history.evidence_subprocess: true açıkça verilmiş)",
      "KAPALI": "süreç içinde (bu sürümün varsayılanı: alt süreç KAPALI)"}.get(mode, "satır yok (günlük okunamadı)"))
if mode != "AÇIK":
    print("   (alt süreç kapalıyken aşağıdaki sayılar 0 / 'yok' beklenir)")
mbs = [j[1] for j in jobs if j[1] is not None]
print("   alt süreçli ön ısıtma işi %d (%s) · alt süreçte %d / süreç içi %d sembol · en yüksek özel bellek %s · eski sürüm "
      "alt süreci hemen kapatıldı %d · yeniden fork %d" % (len(jobs), since, sum(j[2] for j in jobs), sum(j[3] for j in jobs),
                                                         ("%d MB" % max(mbs)) if mbs else "—", n_super, n_refork))
sig = []
if n_alrm:
    sig.append("worker fork deadman ile sonlandı (status=14/ALRM) %d kez" % n_alrm)
if n_stop:
    sig.append("kapanışta stop-sigterm zaman aşımı %d kez" % n_stop)
if os.path.exists(os.path.join(state, "pattern_evidence_fork.marker")):
    sig.append("state/pattern_evidence_fork.marker VAR (fork asıldı → alt süreç bu makinede kapalı)")
if sum(j[3] for j in jobs):
    sig.append("alt süreçli işlerde süreç içi %d sembol" % sum(j[3] for j in jobs))
if mbs and max(mbs) >= 900:
    sig.append("alt süreç özel belleği %d MB ≥ 900 MB (beklenen ≈ 300–400 MB)" % max(mbs))
if errs:
    sig.append("alt süreç uyarıları: " + ", ".join("%s %d" % kv for kv in sorted(errs.items())))
if sig:
    print("   UYARI (GERİ ALMA SİNYALİ, docs/TOUR_CONTENTION_V1.md §9): " + " · ".join(sig))
    print("   → alt süreci açtıysanız config.yaml'daki 'evidence_subprocess: true' satırını silin + worker restart (varsayılan "
          "süreç içi); çıktıyı bana iletin. Kanıt ve kararlar her durumda aynıdır (alt süreç arızasında tur süreç içi hesaplar).")
kids = []
if mpid.isdigit() and mpid != "0":
    try:
        out = subprocess.run(["ps", "-o", "pid=,rss=,etimes=", "--ppid", mpid], capture_output=True, text=True,
                             timeout=10).stdout
        for ln in out.splitlines():
            p = ln.split()
            if len(p) == 3 and p[0].isdigit():
                hwm = "?"
                try:
                    with open("/proc/%s/status" % p[0], encoding="ascii") as fh:
                        hwm = next((x.split()[1] for x in fh if x.startswith("VmHWM:")), "?")
                except OSError:
                    pass
                kids.append("pid %s RSS %d MB tepe %s MB, %s sn" % (p[0], int(p[1]) // 1024,
                                                                  (int(hwm) // 1024) if hwm.isdigit() else "?", p[2]))
    except (OSError, subprocess.SubprocessError, ValueError):
        kids = ["(ps okunamadı)"]
print("   worker'ın şu an canlı çocuk süreçleri (kanıt alt süreci dahil): %s (alt süreç yalnız bir ön ısıtma işi boyunca "
      "yaşar; yokken 'yok' NORMALDİR)"
      % ("; ".join(kids) if kids else "yok"))

PY
  rm -f "$jf" 2>/dev/null || true
  return 0
}

engine_plan() {    # 5/10 (salt okur; uyarı DURDURMAZ): engine-app durumu ve dağıtım sonundaki sabitleme yapılabilecek mi
  local e
  engine_state
  [[ -d "$ENG/.git" ]] || return 0
  e="$(as_svc git -C "$ENG" rev-parse HEAD 2>/dev/null || true)"
  [[ "$e" != "$TIP" ]] || return 0
  if [[ "$e" =~ ^[0-9a-f]{40}$ ]] && gitc merge-base --is-ancestor "$e" "$TIP" 2>/dev/null \
     && gitc diff --quiet "$e" "$TIP" -- "${ENGINE_FILES[@]}" 2>/dev/null; then
    echo "   engine-app dağıtımın sonunda $T7'e yeniden sabitlenecek (motor kodu ${e:0:7}..$T7 arasında AYNI → yeni A/B dönemi yok)"
  else
    echo "   UYARI (durdurmaz): engine-app (${e:0:7}) $T7'e sabitlenemeyecek (hedefin atası değil ya da motor kodu farklı) —"
    echo "   dağıtım sonunda ATLANIR, geceler SKEW olur; bu çıktıyı bana iletin"
  fi
  return 0
}

# --check (2026-10-06): PATTERN KANITI HIZLI kNN + YAYIM ÇAKIŞMASI. Worker günlüğünden (yeniden başlatmadan beri, kayıt yoksa
# son 24 sa; tek journalctl okuması): etkin yol (başlangıç satırı "pattern kanıtı kNN sorgusu: HIZLI yol … | ESKİ olay-başına
# döngü …" ve "pattern kanıtı sorguları: …"); her yayım ("pattern indeksi yenilendi: sürüm N, E olay, S seri, kurulum B sn")
# için ön ısıtma saniyesi ve sembol sayısı ("pattern kanıtı ön ısıtıldı: sürüm N, X sembol hesaplandı, Y zaten hazırdı,
# S sn") ve yayımdan SONRA biten ilk turun toplamı ile "pattern kanıtı" faz saniyesi ("tur fazları: … coin head …s (pattern
# kanıtı P s) … toplam T s"); YAYIM ÇAKIŞMASI = yayım süreç içindeki SONRAKİ bir turun içinde (başlangıç = bitiş − toplam,
# önceki turun bitişinden önce sayılmaz); hızlı yol uyarıları ("pattern kNN hızlı yolu …"); worker belleği (MainPID VmRSS /
# VmHWM, cgroup memory.current / memory.peak ↔ MemoryMax). 35 dk tetiği DEĞİŞMEZ (öğrenme raporundaki tablo); burada yalnız
# yorum: hızlı yol AÇIKKEN > 35 dk çakışma turu GERÇEK arıza sinyalidir, KAPALIYKEN (kill switch) bilinen eski örüntüdür
# (yine tetik). Metin satırları (saat HH:MM:SS, gün dönümü sayılır) yoksa JSON satırları okunur. Salt okunur.
# Ön ısıtma satırı süzgeci (PRE) = değişmez #56'nın KNN_PREWARM_RE'si.
knn_report() {
  local rs_human="" since mpid cg jf
  if [[ -r "$MARK_RESTART" ]]; then rs_human="$(sed -n 2p "$MARK_RESTART" || true)"; fi
  since="${rs_human:--24h}"
  mpid="$(systemctl show "$WORKER" -p MainPID --value 2>/dev/null || true)"
  cg="/sys/fs/cgroup/system.slice/$WORKER"
  jf="$(mktemp)" || { echo "   hızlı kNN raporu: geçici dosya açılamadı"; return 0; }
  journalctl -u "$WORKER" --since "$since" -o cat --no-pager 2>/dev/null \
    | grep -aE "tur fazları: |pattern indeksi yenilendi|pattern kanıtı|pattern kNN hızlı yolu|İzleme başladı \[" > "$jf" || true
  chmod 644 "$jf" 2>/dev/null || true
  py - "${mpid:-0}" "$cg" "${MEMMAX:-}" "${rs_human:-}" "$jf" <<'PY' || echo "   hızlı kNN raporu okunamadı"
import datetime as dt
import json
import re
import statistics
import sys

mpid, cg, memmax, rs_human, jf = sys.argv[1:6]
TXT = re.compile(r"^([0-9]{2}):([0-9]{2}):([0-9]{2}) [A-Z]+ +[A-Za-z0-9_.]+: (.*)$")
LIMIT_S = 2100.0                                   # = öğrenme raporunun 35 dk geri alma tetiği (DEĞİŞMEDİ)
PUB = re.compile(r"pattern indeksi yenilendi: sürüm ([0-9]+), ([0-9]+) olay, ([0-9]+) seri, kurulum ([0-9.]+) sn")   # = KNN_PUB_RE (#56)
PRE = re.compile(r"pattern kanıtı ön ısıtıldı: sürüm ([0-9]+), ([0-9]+) sembol hesaplandı, ([0-9]+) zaten hazırdı, "
                 r"([0-9]+(?:\.[0-9]+)?) sn")                                   # = KNN_PREWARM_RE (#56)
DROP = re.compile(r"pattern kanıtı ön ısıtması (?:kapanışta )?bırakıldı: sürüm ([0-9]+)")
recs, jrecs, day, prev = [], [], 0, None
with open(jf, encoding="utf-8", errors="replace") as fh:
    for line in fh.read().splitlines():
        m = TXT.match(line)
        if m:
            sod = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if prev is not None and sod < prev - 3600:          # gece yarısı (metin satırında tarih yok)
                day += 1
            prev = sod
            recs.append((day * 86400 + sod, m.group(4), True))
            continue
        if line.startswith('{"ts": '):
            try:
                d = json.loads(line)
                jrecs.append((dt.datetime.fromisoformat(str(d["ts"]).replace("Z", "+00:00")).timestamp(), str(d["msg"]),
                              False))
            except (ValueError, KeyError, TypeError):
                pass
recs = recs or jrecs


def hms(ts, txt):
    if txt:
        s = int(ts) % 86400
        return "%02d:%02d:%02d" % (s // 3600, s // 60 % 60, s % 60)
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%H:%M:%SZ")


knn_line = evc_line = None
knn_mode = None
pubs, pre, drops, tours, fwarn = [], {}, [], [], []
k_in_proc, prev_end = 0, None
for ts, msg, txt in recs:
    if msg.startswith("İzleme başladı ["):
        k_in_proc, prev_end = 0, None
        continue
    if msg.startswith("pattern kanıtı kNN sorgusu: "):
        knn_line = msg
        knn_mode = "HIZLI" if "HIZLI yol" in msg else ("ESKİ" if "ESKİ olay-başına döngü" in msg else "?")
        continue
    if msg.startswith("pattern kanıtı sorguları: "):
        evc_line = msg
        continue
    m = PUB.match(msg)
    if m:
        pubs.append((ts, int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4)), txt))
        continue
    m = PRE.match(msg)
    if m:
        pre.setdefault(int(m.group(1)), []).append((ts, int(m.group(2)), int(m.group(3)), float(m.group(4)),
                                                    "alt süreç" in msg))
        continue
    m = DROP.match(msg)
    if m:
        drops.append((ts, int(m.group(1))))
        continue
    if msg.startswith("pattern kNN hızlı yolu "):
        fwarn.append(msg)
        continue
    if msg.startswith("tur fazları: "):
        mt = re.search(r"toplam ([0-9]+(?:\.[0-9]+)?)s\s*$", msg)
        mp = re.search(r"\(pattern kanıtı ([0-9]+(?:\.[0-9]+)?)s\)", msg)
        if mt:
            k_in_proc += 1
            tot = float(mt.group(1))
            t0 = ts - tot
            tours.append((t0 if prev_end is None else max(t0, prev_end), ts, tot, float(mp.group(1)) if mp else None,
                          k_in_proc, knn_mode))
            prev_end = ts
since = ("yeniden başlatmadan (%s) beri" % rs_human) if rs_human else "son 24 sa"
print("   etkin yol (worker başlangıç satırı): %s" % (knn_line or "satır yok — eski kod (8db1faf bu satırı basmaz), ilk tur "
                                                               "henüz bitmedi ya da günlük okunamadı"))
print("   kanıt süreci: %s" % (evc_line or "satır yok"))
if knn_mode == "ESKİ":
    print("   NOT: hızlı yol KAPALI (kill switch history.evidence_fast_knn: false) — eski döngü: ön ısıtma ~1.000 sn ve yayımla "
          "çakışan turlar uzun sürer (bilinen 8db1faf örüntüsü). Kanıt ve kararlar aynı.")
print("   hızlı yol uyarısı (\"pattern kNN hızlı yolu …\") %s: %d" % (since, len(fwarn)))
if fwarn:
    print("   son uyarı: %s" % fwarn[-1][:220])
    print("   UYARI: hızlı yol bir motorda eski döngüye düştü — kanıt AYNI (eski döngü), yalnız yavaş. Bu çıktıyı bana iletin.")
print("   yayım başına (%s; son 10): yayım saati · sürüm · olay · kurulum │ ön ısıtma sn · sembol (hazır) │ yayımdan sonra "
      "biten ilk tur: toplam · \"pattern kanıtı\" fazı · çakışma" % since)
pw_secs = []
for ts, ver, nev, nser, build, txt in pubs[-10:]:
    pj = [p for p in pre.get(ver, []) if p[0] >= ts - 5]
    dj = [d for d in drops if d[1] == ver and d[0] >= ts - 5]
    if pj:
        p = pj[0]
        pw = "%8.1f sn  %3d sembol (%d hazır)%s" % (p[3], p[1], p[2], " [alt süreç]" if p[4] else "")
        pw_secs.append(p[3])
    elif dj:
        pw = "  bırakıldı (daha yeni yayım)      "
    else:
        pw = "  satır yok (sürüyor ya da yok)    "
    nt = next((t for t in tours if t[1] >= ts), None)                 # yayımı içeren (t0 < yayım ≤ bitiş) ya da sonraki tur
    if nt is None:
        tt = "henüz biten tur yok"
    else:
        coll = nt[0] < ts <= nt[1]
        tt = "%6.1f dk · %s · %s" % (nt[2] / 60.0, ("%.1f sn" % nt[3]) if nt[3] is not None else "?",
                                    ("ÇAKIŞMA (yayım bu turun içinde)" if coll else "çakışma yok")
                                    + (" — ilk tur" if nt[4] == 1 else ""))
    print("     %s  sürüm %-5d %7d olay  %6.1f sn │%s│ %s" % (hms(ts, txt), ver, nev, build, pw, tt))
if not pubs:
    print("     (henüz indeks yayımı yok: yenileyici ilk turda başlar, yeni 4h mumdan sonra yayımlar)")
if pw_secs:
    print("   ön ısıtma: %d iş · en kısa %.1f · medyan %.1f · en uzun %.1f sn (8db1faf'ta 977–1.077 sn; beklenen birkaç sn)"
          % (len(pw_secs), min(pw_secs), statistics.median(pw_secs), max(pw_secs)))
    if knn_mode == "HIZLI" and max(pw_secs) > 120:
        print("   UYARI: hızlı yol AÇIK ama bir ön ısıtma %.0f sn sürdü (beklenen birkaç sn) — uyarı satırlarına bakın; bu "
              "çıktıyı bana iletin (kanıt ve kararlar aynı)." % max(pw_secs))
later = [t for t in tours if t[4] > 1]
coll = [t for t in later if any(t[0] < p[0] <= t[1] for p in pubs)]
print("   yayım çakışması (sonraki tur sırasında \"pattern indeksi yenilendi\"): %d / %d sonraki tur%s"
      % (len(coll), len(later), (" · ilk turlarda %d" % sum(1 for t in tours if t[4] == 1 and any(t[0] < p[0] <= t[1]
                                                                                                     for p in pubs)))
         if tours else ""))
for t in coll[-6:]:
    pe = ("%.1f sn" % t[3]) if t[3] is not None else "?"
    if t[2] > LIMIT_S:
        why = ("hızlı yol AÇIK → GERÇEK ARIZA SİNYALİ (geri alma tetiği): çıktıyı bana iletin" if t[5] == "HIZLI" else
               "hızlı yol KAPALI (kill switch) → bilinen eski örüntü (yine geri alma tetiği; öğrenme raporuna bakın)")
        print("   [TETİKLENDİ] çakışma turu %.1f dk > 35 dk (pattern kanıtı %s) — %s" % (t[2] / 60.0, pe, why))
    else:
        print("   [tamam]      çakışma turu %.1f dk (pattern kanıtı %s; 8db1faf'ta 23–36 dk idi; beklenen ~5–6 dk)"
              % (t[2] / 60.0, pe))
pes = [t[3] for t in later if t[3] is not None]
if pes:
    print("   sonraki turlarda \"pattern kanıtı\" fazı: en çok %.1f sn, medyan %.1f sn (%d tur; 8db1faf'ta yeni sürümü bekleyen "
          "turlarda 881–1.756 sn)" % (max(pes), statistics.median(pes), len(pes)))


def rd(name):
    try:
        with open("%s/%s" % (cg, name), encoding="ascii") as fh:
            v = fh.read().strip()
        return int(v) if v.isdigit() else None
    except OSError:
        return None


def st_kb(key):
    if not (mpid.isdigit() and mpid != "0"):
        return None
    try:
        with open("/proc/%s/status" % mpid, encoding="ascii") as fh:
            for x in fh:
                if x.startswith(key + ":"):
                    return int(x.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


rss, hwm = st_kb("VmRSS"), st_kb("VmHWM")
cur, peak, mx = rd("memory.current"), rd("memory.peak"), rd("memory.max") or (int(memmax) if memmax.isdigit() else None)
print("   worker belleği: MainPID %s VmRSS %s · VmHWM %s · cgroup şu an %s · tepe (memory.peak) %s / MemoryMax %s%s"
      % (mpid or "?", ("%d MB" % (rss >> 10)) if rss else "?", ("%d MB" % (hwm >> 10)) if hwm else "?",
         ("%d MB" % (cur >> 20)) if cur else "?", ("%d MB" % (peak >> 20)) if peak else "?",
         ("%d MB" % (mx >> 20)) if mx else "?", (" = %%%.0f" % (100.0 * peak / mx)) if (peak and mx) else ""))
print("   (memory.peak sayfa önbelleğini DAHİL eder; 8db1faf + alt süreçte 5.957 / 6.144 MB = %97 idi. Hızlı yolda alt süreç yok:\n"
      "   motor başına ~27 MB dizi. Geri alma tetiği RSS hwm_mb > %90 × MemoryMax — öğrenme raporunda, değişmedi)")
if peak and mx and peak > 0.9 * mx:
    print("   UYARI: worker cgroup memory.peak %%%.0f > %%90 MemoryMax — sayfa önbelleği dahil; RSS tetiği (öğrenme raporu) "
          "belirleyicidir. Sürüyorsa bu çıktıyı bana iletin." % (100.0 * peak / mx))
PY
  rm -f "$jf" 2>/dev/null || true
  return 0
}

# (2026-10-06) BİLİNEN YEREL config.yaml DEĞİŞİKLİĞİ: sahibin 8db1faf alt süreç kill switch'i — tb-deploy-8db1faf.sh'teki sed
# `s/^history:$/history:\n  evidence_subprocess: false/` TEK kez. cfg_known_edit 0 döner YALNIZ şu durumda: izlenen
# dosyalardan yalnız config.yaml değişmiş ve sahnelenmemiş (porcelain tam olarak " M config.yaml") ve dosyanın baytları =
# HEAD:config.yaml'ın TEK `history:` satırının hemen altına `  evidence_subprocess: false` eklenmiş hali (başka hiçbir fark
# yok; satır iki kez eklenmişse, değer farklıysa, başka bir satır değişmişse → 1). Salt okur.
cfg_known_edit() {
  local exp rc=0
  [[ "$(gitc status --porcelain --untracked-files=no)" == " M config.yaml" ]] || return 1
  gitc diff --cached --quiet || return 1
  exp="$(mktemp)" || return 1
  if gitc show HEAD:config.yaml | awk -v L="$KS_EVC_LINE" '{ print } $0 == "history:" { print L; n++ } END { exit (n == 1 ? 0 : 1) }' > "$exp"; then
    cmp -s "$exp" "$APP/config.yaml" || rc=1
  else
    rc=1
  fi
  rm -f "$exp"
  return "$rc"
}
CFG_LOCAL=""; CFG_PRE_SHA=""; CFG_RESTORED=""
# cfg_same_as_start: izlenen ağaç başlangıçtaki gibi mi (temiz; ya da bilinen satır ve AYNI baytlar) — bekleme saatler sürebilir.
cfg_same_as_start() {
  if [[ -n "$CFG_LOCAL" ]]; then
    cfg_known_edit && [[ "$(sha256sum < "$APP/config.yaml" | awk '{print $1}')" == "$CFG_PRE_SHA" ]]
  else
    [[ -z "$(gitc status --porcelain --untracked-files=no)" ]]
  fi
}
# cfg_put_back: (geri alma) dağıtım öncesi config.yaml baytlarını — kill switch satırı DAHİL — AYNEN yerine koyar (servis
# kullanıcısı yazar; dosyanın sahibi/kipi korunur) ve sha256 ile doğrular. ERR tuzağı altında da çağrılır: hata vermez.
cfg_put_back() {
  if as_svc tee "$APP/config.yaml" < "$CFG_SAVE" > /dev/null \
     && [[ "$(sha256sum < "$APP/config.yaml" | awk '{print $1}')" == "$CFG_PRE_SHA" ]]; then
    echo "   config.yaml dağıtım öncesi baytlarına döndü (bilinen satır 'evidence_subprocess: false' DAHİL; sha256 ${CFG_PRE_SHA:0:16}…)" || true
  else
    echo "   UYARI: config.yaml dağıtım öncesi haline GERİ KONAMADI — elle: sudo cat $CFG_SAVE | sudo -u $SVC_USER tee $APP/config.yaml >/dev/null ; sonra sudo systemctl restart $WORKER" >&2 || true
  fi
  return 0
}

# (2026-10-06) ENGINE-APP YENİDEN SABİTLEME (docs/OPERATIONS.md "Sürüm kuralı"): öğrenme motoru kuruluysa (/opt/tradingbot/
# engine-app, ayrı klon, servis kullanıcısına ait) AYNI motor koduyla hedefe sabitlenir (detached checkout) ve compileall ile
# önceden derlenir; S0 SKEW denetimi app ⊑ engine-app ister. Motor kodu (research_engine + gece birimi dosyaları) engine-app
# HEAD'i ile hedef arasında AYNI olmalı (yeni A/B dönemi açılmasın; motoru değiştiren sürüm motor sürüm betiğinin işidir).
# Atlanır (worker ETKİLENMEZ, komut basılır): motor yok · motor gecesi şu an çalışıyor · klon beklenen durumda değil (sahip,
# yerel değişiklik) · hedef getirilemedi · motor kodu farklı · hedef engine-app HEAD'inin torunu değil. Dönüş HER ZAMAN 0.
engine_state() {    # --check / kuru çalışma: iki SHA ve SKEW ilişkisi (salt okur)
  local e a rel
  if [[ ! -d "$ENG/.git" ]]; then echo "   engine-app yok ($ENG) — öğrenme motoru P1a kurulu değil; sabitleme gerekmez"; return 0; fi
  e="$(as_svc git -C "$ENG" rev-parse HEAD 2>/dev/null || echo '?')"; a="$(gitc rev-parse HEAD 2>/dev/null || echo '?')"
  if as_svc git -C "$ENG" merge-base --is-ancestor "$a" "$e" 2>/dev/null; then rel="app ⊑ engine-app (SKEW yok)"
  else rel="SKEW — app sürümü engine-app'te yok/atası değil: geceler yalnız S0/S1a/S7; engine-app yeniden sabitlenmeli"; fi
  echo "   iki SHA: app ${a:0:7} · engine-app ${e:0:7} → $rel"
  return 0
}
engine_repin() {
  local st now why="" comp_bad=0
  ENG_CMD="sudo -u $SVC_USER git -C $ENG fetch -q $APP refs/deploy/$T7 && sudo -u $SVC_USER git -C $ENG -c advice.detachedHead=false checkout -q --detach $TIP && sudo -u $SVC_USER env -C $ENG nice -n 19 $VENV/bin/python -s -m compileall -q tradingbot scripts"
  if [[ ! -e "$ENG" ]]; then echo "   engine-app yok ($ENG) — öğrenme motoru P1a kurulu değil; sabitleme gerekmez" || true; return 0; fi
  if [[ ! -d "$ENG/.git" || -L "$ENG" || "$(stat -c %U "$ENG" 2>/dev/null || true)" != "$SVC_USER" ]]; then
    why="$ENG beklenen klon değil (git klonu, $SVC_USER'a ait, bağlantı değil)"
  else
    st="$(systemctl is-active "$ENGINE_SVC" 2>/dev/null || true)"
    if [[ "$st" =~ ^(active|activating|reloading|deactivating)$ ]]; then
      why="motor gecesi şu an çalışıyor ($ENGINE_SVC: $st) — kodu altından değiştirmem; bittikten sonra elle sabitleyin"
    elif [[ -n "$(as_svc git -C "$ENG" status --porcelain 2>/dev/null || echo x)" ]]; then
      why="$ENG içinde yerel değişiklik var"
    fi
  fi
  if [[ -z "$why" ]]; then
    now="$(as_svc git -C "$ENG" rev-parse HEAD 2>/dev/null || true)"
    if [[ "$now" == "$TIP" ]]; then ok "engine-app zaten $T7"; engine_state; return 0; fi
    if ! as_svc git -C "$ENG" cat-file -e "${TIP}^{commit}" 2>/dev/null; then       # önce yerel app deposu, sonra GitHub
      as_svc git -C "$ENG" fetch -q "$APP" "refs/deploy/$T7" 2>/dev/null \
        || as_svc env -C "$ENG" HOME="$BASE" git fetch -q "$REPO_URL" "$TIP" 2>/dev/null \
        || as_svc env -C "$ENG" HOME="$BASE" git fetch -q "$REPO_URL" "$BRANCH_REF" 2>/dev/null || true
    fi
    if ! as_svc git -C "$ENG" cat-file -e "${TIP}^{commit}" 2>/dev/null; then
      why="hedef commit $T7 engine-app'e getirilemedi"
    elif [[ ! "$now" =~ ^[0-9a-f]{40}$ ]] || ! as_svc git -C "$ENG" merge-base --is-ancestor "$now" "$TIP" 2>/dev/null; then
      why="hedef $T7, engine-app HEAD'inin (${now:0:7}) torunu değil"
    elif ! as_svc git -C "$ENG" diff --quiet "$now" "$TIP" -- "${ENGINE_FILES[@]}" 2>/dev/null; then
      why="motor kodu ${now:0:7}..$T7 arasında DEĞİŞMİŞ (yeni A/B dönemi = motor sürüm betiğinin işi)"
    elif ! as_svc git -C "$ENG" -c advice.detachedHead=false checkout -q --detach "$TIP" 2>/dev/null; then
      why="engine-app checkout başarısız"
    else
      as_svc env -C "$ENG" nice -n 19 "$VENV/bin/python" -s -m compileall -q tradingbot scripts >/dev/null 2>&1 \
        || { comp_bad=1; echo "   UYARI: engine-app compileall başarısız (motor yine çalışır; birim ProtectSystem=strict olduğundan önbellek yazamaz, her gece derlenmemiş kodla biraz yavaş başlar)"; } || true
      if [[ "$(as_svc git -C "$ENG" rev-parse HEAD 2>/dev/null || true)" == "$TIP" \
            && -z "$(as_svc git -C "$ENG" status --porcelain 2>/dev/null || echo x)" ]]; then
        ok "engine-app ${now:0:7} → $T7 (AYNI motor kodu; yeni A/B dönemi yok; $([[ ${comp_bad:-0} == 1 ]] && echo "DERLENEMEDİ" || echo "önceden derlendi"))"
        engine_state
        return 0
      fi
      why="engine-app sabitleme sonrası HEAD/temizlik doğrulanamadı"
    fi
  fi
  cat <<EOF || true
   !!! UYARI (worker ETKİLENMEDİ): engine-app YENİDEN SABİTLENMEDİ — $why.
   !!! Sabitlenene kadar motor geceleri SKEW yazar (yalnız S0/S1a/S7; arşiv + özet sürer). Uygun bir saatte (motor gecesi
   !!! 01:37–03:40 UTC DIŞINDA) elle:
   !!!   $ENG_CMD
   !!! Sonra: sudo bash $0 --check  ("iki SHA" satırı: app ⊑ engine-app). Bu çıktıyı bana iletin.
EOF
  engine_state
  return 0
}

# --check (2026-10-03): ÖĞRENME-EKSTRA YALNIZ KAYIT (sahip kararı) — etkin kip (config.yaml ve çalışan worker'ın health.json'u),
# KAPATMA satırı (kill switch) ve defter başına yalnız-kayıt sayıları YENİDEN BAŞLATMADAN (dağıtım) BERİ: defter sayacı
# learning_record_only (KAYIT sayar) ve rejections[LEARNING_RECORD_ONLY] (tur olayı) — ikisi de birikimlidir, dağıtım anındaki
# değer $MARK_RO_BASE'den düşülür —, ana botun huni olayı (decision_funnel geçmişi) ve karşı-olgusal dosyalarındaki
# yalnız-kayıt kayıtları (toplam / bekleyen / etiketli / yeniden başlatmadan sonra yazılan / dönüşen; etiketlilerin net R'si).
# Çalışan worker'ın kipi health.json'dan; health.json worker'ın SON başlatılmasından (dağıtım ya da kill switch sonrası restart:
# MainPID'in yaşı) önce yazılmışsa önceki sürecindir → uyumsuzluk UYARISI basılmaz (gereksiz ikinci restart'a yol açmasın).
# Salt okunur.
ro_report() {
  local rs_epoch="" wpid="" w_age="" w_start=""
  if [[ -r "$MARK_RESTART" ]]; then rs_epoch="$(sed -n 1p "$MARK_RESTART" || true)"; fi
  wpid="$(systemctl show "$WORKER" -p MainPID --value 2>/dev/null || true)"
  if [[ "$wpid" =~ ^[1-9][0-9]*$ ]]; then w_age="$(ps -o etimes= -p "$wpid" 2>/dev/null | tr -d ' ' || true)"; fi
  if [[ "$w_age" =~ ^[0-9]+$ ]]; then w_start="$(( $(date +%s) - w_age ))"; fi
  py - "$STATE" "$APP" "$rs_epoch" "$MARK_RO_BASE" "$SVC_USER" "$w_start" <<'PY' || echo "   yalnız-kayıt raporu okunamadı"
import datetime as dt
import json
import os
import sys

st, app, rs_epoch, basep, svc, ws_epoch = sys.argv[1:7]
RO = "LEARNING_RECORD_ONLY"


def rj(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        return {"__err__": type(exc).__name__}


def bad(d):
    return isinstance(d, dict) and "__err__" in d


def fl(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def num(x):
    return int(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def epoch(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


rs = fl(rs_epoch)
ws = fl(ws_epoch)                       # şimdiki worker sürecinin başlangıcı (epoch; bilinmiyorsa None)
starts = [x for x in (rs, ws) if x is not None]
w_ref = max(starts) if starts else None
warns = []
cfg = {}
try:
    import yaml
    with open(os.path.join(app, "config.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
except Exception as exc:  # noqa: BLE001
    warns.append("config.yaml okunamadı (%s)" % type(exc).__name__)
lmc = cfg.get("learning_mode") if isinstance(cfg.get("learning_mode"), dict) else {}
c_mode = lmc.get("extra_entries") if "extra_entries" in lmc else None
bx = (lmc.get("books") or {}).get("b1_box_fade") if isinstance(lmc.get("books"), dict) else None
print("   config.yaml learning_mode.extra_entries: %s  ·  Box öğrenme stop tabanı (books.b1_box_fade.min_stop_pct): %s"
      % (c_mode if c_mode is not None else "YOK (kod varsayılanı open)", bx.get("min_stop_pct") if isinstance(bx, dict) else "?"))
h = rj(os.path.join(st, "health.json"))
hl = h.get("learning_mode") if isinstance(h, dict) and not bad(h) else None
h_at = epoch(h.get("at")) if isinstance(h, dict) and not bad(h) else None
if isinstance(hl, dict) and w_ref is not None and h_at is not None and h_at < w_ref:
    print("   çalışan worker: health.json worker'ın son başlatılmasından ÖNCE yazılmış (%s; önceki süreç: extra_entries %s) —\n"
          "     şimdiki worker'ın kipi ilk turu bitince görünür (yeniden başlatma GEREKMEZ)" % (h.get("at"), hl.get("extra_entries") or "open"))
elif isinstance(hl, dict):
    r_mode = hl.get("extra_entries") or "open"
    print("   çalışan worker (health.json %s): extra_entries %s%s" % (
        h.get("at"), r_mode, "" if hl.get("extra_entries") else " (alan yok = kod varsayılanı open ya da eski kod)"))
    if (c_mode or "open") != r_mode:
        warns.append("config.yaml extra_entries=%s ama çalışan worker %s: config değişikliği yalnız worker yeniden başlatılınca "
                     "etkin olur (sudo systemctl restart tradingbot-worker)" % (c_mode or "open", r_mode))
else:
    print("   çalışan worker: health.json'da learning_mode yok (yeni worker ilk turunu henüz bitirmedi)")
print("   KAPATMAK (kill switch; kod AYNI kalır, bütün öğrenme-ekstralar fc63481'deki gibi yine AÇILIR):")
print("     %s → learning_mode.extra_entries: open   ;   sudo systemctl restart tradingbot-worker   — tek satır:"
      % os.path.join(app, "config.yaml"))
print("     sudo -u %s sed -i 's/^  extra_entries: record_selectivity /  extra_entries: open /' %s && sudo systemctl restart "
      "tradingbot-worker" % (svc, os.path.join(app, "config.yaml")))
print("     (açık pozisyonlara ve mevcut kayıtlara dokunulmaz; Box tabanı 0,5 ayrı değerdir: books.b1_box_fade.min_stop_pct.\n"
      "     Elle düzenlenen config.yaml sonraki dağıtımda / eski koda dönüşte 'yerel değişiklik' olur: önce\n"
      "     sudo -u %s git -C %s checkout -- config.yaml)" % (svc, app))
base = rj(basep) if basep else None
base = base if isinstance(base, dict) and not bad(base) else None


def cf_stats(trades):
    ro = [t for t in trades if isinstance(t, dict) and isinstance(t.get("reason_not_opened"), list)
          and t["reason_not_opened"][:1] == [RO]]
    lab = [t for t in ro if isinstance(t.get("outcome"), dict)]
    new = [t for t in ro if rs is not None and (epoch(t.get("created_at")) or 0.0) >= rs]
    ret = [t for t in ro if isinstance(t.get("features"), dict) and isinstance(t["features"].get("learning_record_only"), dict)
           and t["features"]["learning_record_only"].get("retagged_from")]
    rn = [x for x in (fl((t.get("outcome") or {}).get("r_net")) for t in lab) if x is not None]
    return [len(ro), len(ro) - len(lab), len(lab), len(new), len(ret), (sum(rn) / len(rn)) if rn else None]


print("\n   %-30s %7s %8s | %s" % ("defter", "sayaç", "olay", "yalnız-kayıt kayıtları: toplam / bekleyen / etiketli / yeni / "
                                                         "dönüşen · net ort.R"))
tot = [0, 0, 0, 0, 0]
cnt_sum = ev_sum = 0


def line(name, cnt, ev, s, extra=""):
    for i in range(5):
        tot[i] += s[i]
    print("   %-30s %7s %8s | %5d / %5d / %5d / %5d / %5d · %s%s" % (
        name, cnt, ev, s[0], s[1], s[2], s[3], s[4], ("%+.2f" % s[5]) if s[5] is not None else "—", extra))


sb = rj(os.path.join(st, "shadow_book.json"))
if bad(sb):
    warns.append("shadow_book.json okunamadı (%s)" % sb["__err__"])
mt = [t for t in (sb.get("trades") or []) if isinstance(t, dict) and t.get("book") == "main"] \
    if isinstance(sb, dict) and not bad(sb) else []
fun = rj(os.path.join(st, "decision_funnel.json"))
hist = [x for x in (fun.get("history") or []) if isinstance(x, dict)] if isinstance(fun, dict) and not bad(fun) else []
ev = sum(num(x.get("learning_record_only")) for x in hist if rs is None or (epoch(x.get("at")) or 0.0) >= rs)
note = ""
if rs is not None and hist and (epoch(hist[0].get("at")) or 0.0) > rs:
    note = "  (huni geçmişi son %d tur: yeniden başlatmadan sonrasının yalnız bir kısmı)" % len(hist)
ev_sum += ev
line("main (ana bot)", "—", ev, cf_stats(mt), note)
books = []
idx = rj(os.path.join(st, "strategy_paper_index.json"))
for b in ((idx or {}).get("books") or []) if isinstance(idx, dict) and not bad(idx) else []:
    key = str((b or {}).get("key") or "") if isinstance(b, dict) else ""
    if key and all(c.isalnum() or c == "_" for c in key):
        books.append((key, str(b.get("name") or key), str(b.get("summary_file") or key + ".json")))
if os.path.isdir(os.path.join(st, "pattern_trader")):
    books.append(("pattern_trader", "pattern_trader", "pattern_trader.json"))
for key, name, summ in books:
    sd = rj(os.path.join(st, summ))
    if bad(sd):
        warns.append("%s okunamadı (%s)" % (summ, sd["__err__"]))
    sd = sd if isinstance(sd, dict) and not bad(sd) else {}
    lc = (sd.get("learning") or {}).get("counters") if isinstance(sd.get("learning"), dict) else None
    cnt = num((lc or {}).get("learning_record_only")) if isinstance(lc, dict) else 0
    rej = num((sd.get("rejections") or {}).get(RO)) if isinstance(sd.get("rejections"), dict) else 0
    b0 = (base or {}).get(key) if isinstance((base or {}).get(key), dict) else {}
    cnt, rej = cnt - num(b0.get("counter")), rej - num(b0.get("rejections"))
    cnt_sum += cnt
    ev_sum += rej
    cf = rj(os.path.join(st, key, "counterfactual_trades.json"))
    if bad(cf):
        warns.append("%s/counterfactual_trades.json okunamadı (%s)" % (key, cf["__err__"]))
    rows = [t for t in (cf.get("trades") or []) if isinstance(t, dict)] if isinstance(cf, dict) and not bad(cf) else []
    line(name, cnt, rej, cf_stats(rows))
print("   %-30s %7s %8s | %5d / %5d / %5d / %5d / %5d" % ("TOPLAM", cnt_sum, ev_sum, tot[0], tot[1], tot[2], tot[3], tot[4]))
print("   sayaç = defterin learning_record_only sayacı (KAYIT: yeni ya da dönüşen; aynı sinyalin sonraki turları sayılmaz) ·\n"
      "   olay = her turdaki ret (rejections[LEARNING_RECORD_ONLY]; ana bot: huni learning_record_only) · ikisi de yeniden\n"
      "   başlatmadan (dağıtım) beri · yeni = yeniden başlatmadan sonra yazılan kayıt · dönüşen = aynı sinyalin başka nedenli\n"
      "   bekleyen kaydı yalnız-kayda dönmüş · net ort.R = etiketlenen kayıtların net R'si (P&L'e GİRMEZ). Bu adaylar AÇILMADI;\n"
      "   bakiyeye dokunmaz. Kayıt sayısı işlem sayısı değildir (karne: «kayda alınan ekstra»).")
if base is None:
    print("   DİKKAT: dağıtım anı tabanı %s yok — sayaç ve olay BİRİKİMLİ (dağıtımdan önceki değerler dahil)"
          % ("(%s)" % basep if basep else "dosyası"))
for w in warns:
    print("   UYARI: %s" % w)
PY
}

# (2026-10-03) Yeniden başlatma anındaki BİRİKİMLİ yalnız-kayıt sayaçları (defter learning_record_only sayacı ve
# rejections[LEARNING_RECORD_ONLY]; worker DURMUŞKEN — eski kod bunları yazmadıysa 0) → --check "yeniden başlatmadan beri"
# farkı. Salt okur; çıktı JSON (çağıran $MARK_RO_BASE'e yazar).
ro_base() {
  py - "$STATE" <<'PY' || true
import json
import os
import sys

st = sys.argv[1]
RO = "LEARNING_RECORD_ONLY"


def rj(p):
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def num(x):
    return int(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


out = {}
books = [(str(b.get("key") or ""), str(b.get("summary_file") or str(b.get("key") or "") + ".json"))
         for b in (rj(os.path.join(st, "strategy_paper_index.json")).get("books") or []) if isinstance(b, dict)]
books.append(("pattern_trader", "pattern_trader.json"))
for key, summ in books:
    if not key or not all(c.isalnum() or c == "_" for c in key):
        continue
    sd = rj(os.path.join(st, summ))
    lc = (sd.get("learning") or {}).get("counters") if isinstance(sd.get("learning"), dict) else None
    rej = sd.get("rejections") if isinstance(sd.get("rejections"), dict) else {}
    out[key] = {"counter": num((lc or {}).get("learning_record_only")) if isinstance(lc, dict) else 0,
                "rejections": num(rej.get(RO))}
print(json.dumps(out, sort_keys=True))
PY
}

# --check (2026-09-29): ORTAK DENEYİM KATMANI durumu (status.json + dosya boyları + defter başına 24 sa kapsam), son
# rapor taraması, karşı-olgusal NET / BRÜT (defter başına, etiket sürümleri), Box tek seferlik gecikme, canlı muhasebe
# düzeltmesinin izleri, yedek notu ve YALNIZ KATMAN geri alma tetikleri. Salt okunur (yalnız `shared-experience-status`
# alt süreci; o da salt okur).
xp_report() {
  local rs_epoch="" rs_human="" pre_hwm="" pre_age="" w_age="" wpid="" interval="" main_pre="" main_skip="" bk=""
  if [[ -r "$MARK_RESTART" ]]; then
    rs_epoch="$(sed -n 1p "$MARK_RESTART" || true)"; rs_human="$(sed -n 2p "$MARK_RESTART" || true)"
  fi
  if [[ -r "$MARK_PRE_HWM" ]]; then
    pre_hwm="$(sed -n 1p "$MARK_PRE_HWM" || true)"; pre_age="$(sed -n 2p "$MARK_PRE_HWM" || true)"
  fi
  wpid="$(systemctl show "$WORKER" -p MainPID --value 2>/dev/null || true)"
  if [[ "$wpid" =~ ^[1-9][0-9]*$ ]]; then w_age="$(ps -o etimes= -p "$wpid" 2>/dev/null | tr -d ' ' || true)"; fi
  interval="$(systemctl show "$WORKER" -p ExecStart --value 2>/dev/null | grep -oE -- '--interval[ =][0-9]+' | grep -oE '[0-9]+' | head -n 1 || true)"
  # ana defterin olayları yalnız günlüğe yazılır ("ana defter <sembol> <tür> <neden> {…}"); yeniden başlatmadan beri
  main_pre="$(journalctl -u "$WORKER" --since "${rs_human:--24h}" -o cat --no-pager 2>/dev/null | grep -acE 'ana defter [^ ]+ BAR_PRE_MOVE_STOP' || true)"
  main_skip="$(journalctl -u "$WORKER" --since "${rs_human:--24h}" -o cat --no-pager 2>/dev/null | grep -acE 'ana defter [^ ]+ BAR_SKIPPED OPENED_BEFORE_STOP_MOVE' || true)"
  bk="$(journalctl -u tradingbot-backup.service --since "-26h" -o cat --no-pager 2>/dev/null | grep -aoE '"skipped_xp_segments": *[0-9]+' | tail -n 1 | grep -oE '[0-9]+$' || true)"
  if ! py - "$STATE" "$APP" "$rs_epoch" "$pre_hwm" "${interval:-15}" "${main_pre:-0}" "${main_skip:-0}" "${bk:-}" \
         "${pre_age:-}" "${w_age:-}" <<'PY'
import datetime as dt
import json
import os
import subprocess
import sys
import time

st, app, rs_epoch, pre_hwm, interval_s, main_pre, main_skip, bk_skip, pre_age, w_age = sys.argv[1:11]
now = time.time()
XP = os.path.join(st, "shared_experience")


def rj(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        return {"__err__": type(exc).__name__}


def unreadable(d):
    return isinstance(d, dict) and "__err__" in d


def fl(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def epoch(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def mb(n):
    return "%.2f MB" % (n / 1048576.0) if isinstance(n, (int, float)) else "?"


def size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return None


rs = fl(rs_epoch)
alarms, warns, trig = [], [], []
h = rj(os.path.join(st, "health.json"))
h = h if isinstance(h, dict) and not unreadable(h) else {}
try:
    import yaml
    with open(os.path.join(app, "config.yaml"), encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
except Exception:  # noqa: BLE001
    raw = {}
xp_cfg = raw.get("shared_experience")
xp_env = os.environ.get("TRADINGBOT_SHARED_EXPERIENCE")
xp_on = isinstance(xp_cfg, dict) and xp_cfg.get("enabled") is True \
    and str(xp_cfg.get("mode") or "").upper() == "RECORD" and not (xp_env or "").strip()
period = max(1.0, fl(interval_s) or 15.0) * 60.0 + max(0.0, fl(h.get("seconds")) or 0.0)   # tur aralığı + son tur

# ---------------------------------------------------------------- ortak deneyim: status.json
print("-- ortak deneyim katmanı (yalnız KAYIT; karar DEĞİŞMEZ): config=%s%s" % (
    json.dumps(xp_cfg, ensure_ascii=False), ("  !!! servis ortamı TRADINGBOT_SHARED_EXPERIENCE=%s" % xp_env) if xp_env else ""))
hx = h.get("shared_experience")
print("   health.json: %s" % (json.dumps(hx, ensure_ascii=False) if hx is not None else "anahtar yok (katman kapalı ya da "
                                                                                     "yeni worker ilk turunu bitirmedi)"))
s = rj(os.path.join(XP, "status.json"))
bf_done = None
p95 = None
store = {}
if unreadable(s):
    alarms.append("status.json OKUNAMADI (%s)" % s["__err__"])
    s = None
if not isinstance(s, dict):
    print("   status.json YOK — ilk RECORD turu henüz bitmedi (yeni worker önce WFO + tur koşar) ya da katman kapalı")
    if xp_on and rs is not None and now - rs > 2 * period + 3600:
        warns.append("katman açık ama yeniden başlatmadan %.0f dk sonra status.json yok (health.json'da da %s)"
                     % ((now - rs) / 60.0, "yok" if hx is None else "var"))
else:
    state = str(s.get("state"))
    c = s.get("counters") if isinstance(s.get("counters"), dict) else {}
    store = s.get("store") if isinstance(s.get("store"), dict) else {}
    br = s.get("breaker") if isinstance(s.get("breaker"), dict) else {}
    ls = epoch(s.get("last_step_at"))
    age = (now - ls) if ls is not None else None
    tag = "OK" if state == "OK" else "!!! ALARM"
    print("   durum: %s [%s]  son adım %s (%s önce; eşik 2 tur ≈ %.0f dk)  adım %s  son hata %s"
          % (state, tag, s.get("last_step_at"), ("%.0f dk" % (age / 60.0)) if age is not None else "?",
             2 * period / 60.0, s.get("steps"), s.get("last_error_code") or "—"))
    if state.startswith("SUSPENDED"):
        alarms.append("katman ASKIDA (%s): mod PAPER değil ya da canlı emir yolu açık — satır yazılmıyor. "
                      "state/mode.json ve worker günlüğüne bakın" % state)
    elif state == "DISABLED_BY_BREAKER":
        alarms.append("katman DEVRE KESİCİ ile kapandı (son hata %s)" % s.get("last_error_code"))
    elif state == "DEGRADED":
        alarms.append("katman DEGRADED (disk tavanı %110: yazım durdu)")
    elif state != "OK":
        alarms.append("katman durumu beklenmeyen: %s" % state)
    if age is not None and age > 2 * period:
        hat = epoch(h.get("at"))
        warns.append("son katman adımı %.0f dk önce (> 2 tur ≈ %.0f dk)%s" % (
            age / 60.0, 2 * period / 60.0, "; health.json daha yeni → worker turluyor ama katman adımı yok"
            if hat is not None and ls is not None and hat - ls > period else "; worker da turlamıyor olabilir"))
    print("   satırlar: toplam %s · tür %s · son adım %s" % (
        c.get("rows_total"), json.dumps(c.get("rows_by_kind") or {}, ensure_ascii=False), s.get("rows_last")))
    print("   defter başına satır: %s" % json.dumps(c.get("rows_by_book") or {}, ensure_ascii=False, sort_keys=True))
    p95 = fl(s.get("step_ms_p95"))
    print("   adım süresi ms (YALNIZ toplayıcı; danışmanın süresi DIŞLANIR, danışman bölümünde AYRI): p50 %s · p95 %s · son %s"
          "   taslak %s %s" % (
        s.get("step_ms_p50"), s.get("step_ms_p95"), s.get("step_ms_last"), s.get("drafts"),
        json.dumps(s.get("drafts_by") or {}, ensure_ascii=False)))
    print("   devre kesici: %s (art arda hata %s/%s · art arda aşım %s/%s)" % (
        "AÇILDI" if br.get("tripped") else "kapalı", br.get("consecutive_errors"), br.get("errors_limit"),
        br.get("consecutive_overruns"), br.get("overruns_limit")))
    names = ("errors_total", "lock_busy_skips", "budget_hits", "row_cap_hits", "budget_overruns", "write_errors",
             "io_retry_steps", "rejected_rows", "duplicate_rows", "cursor_write_errors", "cursor_deferred", "rotate_errors",
             "spot_excluded", "spot_real_excluded", "spot_real_closed_seen", "main_entry_no_sigkey", "cf_reappeared",
             "pending_entries_restored", "legacy_gross_cf", "unknown_label_version")
    line = "   sayaçlar:"
    for n in names:
        item = " %s=%s" % (n, c.get(n))
        if len(line) + len(item) > 118:
            print(line)
            line = "            "
        line += item
    print(line)
    print("   kaybolan (durum başına): %s   anlık görüntü karışımı: %s" % (
        json.dumps(c.get("vanished_by_status") or {}, ensure_ascii=False, sort_keys=True),
        json.dumps(c.get("snapshot_status_mix") or {}, ensure_ascii=False, sort_keys=True)))
    sc = store.get("counters") if isinstance(store.get("counters"), dict) else {}
    print("   depo: durum %s · disk %s / tavan %s (oran %s) · sıcak %s satır (döngü sonrası %s) · döngü %s · arşiv hatası %s%s"
          " · rollback_failed %s · io_error %s" % (
              store.get("state"), mb(store.get("disk_bytes")), mb(store.get("cap_bytes")), store.get("fraction"),
              store.get("hot_lines"), store.get("hot_keep_lines"), store.get("rotations"), store.get("archive_errors"),
              (" (son: %s)" % store.get("last_archive_error")) if store.get("last_archive_error") else "",
              sc.get("rollback_failed"), sc.get("io_error")))
    if store.get("state") == "DEGRADED":
        alarms.append("depo DEGRADED (disk tavanı %110)")
    elif store.get("state") in ("WARN", "THINNING"):
        warns.append("depo disk baskısı %s (oran %s)" % (store.get("state"), store.get("fraction")))
    for n in ("errors_total", "write_errors", "rotate_errors", "cursor_write_errors", "unknown_label_version"):
        if isinstance(c.get(n), int) and c.get(n) > 0:
            warns.append("katman sayacı %s = %s" % (n, c.get(n)))
    if isinstance(sc.get("rollback_failed"), int) and sc.get("rollback_failed") > 0:
        warns.append("depo rollback_failed = %s (yarım yazım geri kesilemedi)" % sc.get("rollback_failed"))
    bf = s.get("backfill") if isinstance(s.get("backfill"), dict) else {}
    bf_done = bool(bf) and all(isinstance(v, dict) and v.get("done") is True for v in bf.values())
    print("   geri doldurma: %s (%s)" % ("BİTTİ" if bf_done else "sürüyor — ilk ~180 tur ≈ 18 sa, adım p95 ≈ 1 sn beklenir",
                                       ", ".join("%s=%s" % (k, "✓" if (v or {}).get("done") else "…")
                                                 for k, v in sorted(bf.items())) or "defter yok"))

# ---------------------------------------------------------------- dosya boyları
print("\n-- ortak deneyim dosya boyları (beklenen)")
for fn, exp, lim in (("experience.jsonl", "≤ ~15 MB", 15 * 1048576), ("cursor.json", "≤ ~3,5 MB (tam listelerde)", 3.5 * 1048576),
                     ("status.json", "~3 KB", 30 * 1024)):
    n = size(os.path.join(XP, fn))
    print("   %-26s %12s   (%s)" % (fn, mb(n) if n is not None else "yok", exp))
    if n is not None and n > 1.3 * lim:
        warns.append("%s %s — beklenenden büyük (%s)" % (fn, mb(n), exp))
man_p = os.path.join(XP, "archive", "manifest.json")
man = rj(man_p)
seg_dir = os.path.join(XP, "archive", "segments")
seg_n, seg_b = 0, 0
try:
    for e in os.scandir(seg_dir):
        if e.is_file():
            seg_n += 1
            seg_b += e.stat().st_size
except OSError:
    pass
born = None
cur = rj(os.path.join(XP, "cursor.json"))
if isinstance(cur, dict) and not unreadable(cur) and isinstance(cur.get("born_ms"), int):
    born = cur["born_ms"] / 1000.0
days = ((now - born) / 86400.0) if born else None
print("   %-26s %12s   segment %d dosya / %s%s (beklenen: sıcak dosya dolunca ≈ 1 segment/gün)" % (
    "archive/manifest.json", mb(size(man_p)) if size(man_p) is not None else "yok", seg_n, mb(seg_b),
    ("; manifest %s segment" % len(man.get("segments") or [])) if isinstance(man, dict) and not unreadable(man) else ""))
if days is not None and days >= 1.0:
    per_day = seg_n / days
    print("   doğumdan beri %.1f gün → %.1f segment/gün (ilk gün geri doldurma fazladan üretebilir)" % (days, per_day))
    if per_day > 24:
        warns.append("arşiv %.1f segment/gün (≈ 1 beklenir; tur başına döngü?)" % per_day)

# ---------------------------------------------------------------- defter başına 24 sa kapsam (CLI, salt okur)
print("\n-- defter başına kapsam (python -m tradingbot shared-experience-status --json; yazım anına göre)")
doc = None
try:
    r = subprocess.run(["nice", "-n", "10", sys.executable, "-m", "tradingbot", "shared-experience-status", "--json"],
                       capture_output=True, text=True, timeout=300, cwd=app)
    txt = r.stdout
    i = txt.find("{")
    doc = json.loads(txt[i:]) if r.returncode == 0 and i >= 0 else None
    if doc is None:
        print("   komut çalışmadı (çıkış %s): %s" % (r.returncode, (r.stderr or r.stdout).strip()[-300:]))
except Exception as exc:  # noqa: BLE001
    print("   komut çalışmadı: %s: %s" % (type(exc).__name__, exc))
if isinstance(doc, dict):
    if not doc.get("available"):
        print("   %s" % (doc.get("note_tr") or "depo yok"))
    cov = doc.get("coverage") if isinstance(doc.get("coverage"), dict) else {}
    c24 = cov.get("24h") if isinstance(cov.get("24h"), dict) else {}
    c7 = cov.get("7d") if isinstance(cov.get("7d"), dict) else {}
    nod = set(doc.get("no_data_24h") or [])
    if c24:
        print("   %-32s 24 sa: satır/giriş/kapanış/olsaydı yeni/olsaydı etiket | 7 gün satır" % "defter")
    for b in c24:
        a, w = c24.get(b) or {}, c7.get(b) or {}
        print("   %-32s %6s/%5s/%5s/%6s/%6s | %7s%s" % (b, a.get("rows"), a.get("entries"), a.get("outcomes"), a.get("cf_new"),
                                                    a.get("cf_labelled"), w.get("rows"), "   VERİ YOK" if b in nod else ""))
    if nod:
        print("   VERİ YOK (24 sa): %s — C4/D4 gibi seyrek sinyalli defterlerde beklenebilir" % ", ".join(sorted(nod)))

# ---------------------------------------------------------------- son rapor taraması
sm = rj(os.path.join(XP, "report_summary.json"))
gen = sm.get("generated_at") if isinstance(sm, dict) and not unreadable(sm) else None
print("\n-- son rapor taraması (report_summary.json): %s" % (gen or "yok (elle)"))
if not gen:
    import pwd
    print("   Otomatik yazan YOK. Elle (≈ 5 sn, ≈ 140 MB; salt okur):")
    print("   sudo -u %s bash -c 'cd %s && TRADINGBOT_STATE_DIR=%s nice -n 19 %s -m tradingbot shared-experience-report"
          " --summary-out %s'" % (pwd.getpwuid(os.getuid()).pw_name, app, st, sys.executable,
                                  os.path.join(XP, "report_summary.json")))

# ---------------------------------------------------------------- karşı-olgusal net / brüt (defter başına)
print("\n-- karşı-olgusal NET / BRÜT (defter başına; P&L'e GİRMEZ) — net ortalama yalnız net etiketlilerden")
try:
    from tradingbot.learning_cf import net_stats as _net_stats
except Exception:  # noqa: BLE001 — eski kod: aynı formül
    _net_stats = None


def net_stats(outs):
    if _net_stats is not None:
        return _net_stats(outs)
    g_, n_, c_, fin, amb, lab = [], [], [], 0, 0, 0
    for o in outs:
        if not isinstance(o, dict):
            continue
        lab += 1
        g, n = fl(o.get("r_multiple")), fl(o.get("r_net"))
        if g is not None:
            g_.append(g)
        if n is not None:
            n_.append(n)
            cc = fl(o.get("cost_r"))
            if cc is not None:
                c_.append(cc)
            fin += o.get("funding_complete") is False
            amb += (o.get("intrabar_ambiguous_bars") or 0) > 0

    def m(xs):
        return round(sum(xs) / len(xs), 4) if xs else None
    return {"n_net": len(n_), "mean_r_net": m(n_), "mean_r_gross": m(g_), "mean_cost_r": m(c_), "n_gross_only": lab - len(n_),
            "n_net_funding_incomplete": fin, "n_net_intrabar_ambiguous": amb}


LV = {"cf_label_v3": "v3", "cf_label_v2": "v2", "cf_label_v1": "v1", "cf_label_v1c": "v1c", None: "none"}
srcs = [("main", os.path.join(st, "shadow_book.json"), "main", os.path.join(st, "futures_ledger.json"), None, None)]
idx = rj(os.path.join(st, "strategy_paper_index.json"))
for b in ((idx or {}).get("books") or []) if isinstance(idx, dict) and not unreadable(idx) else []:
    key = str((b or {}).get("key") or "") if isinstance(b, dict) else ""
    if key and all(ch.isalnum() or ch == "_" for ch in key):
        srcs.append((key, os.path.join(st, key, "counterfactual_trades.json"), None, os.path.join(st, key, "futures_ledger.json"),
                     os.path.join(st, str(b.get("summary_file") or key + ".json")), "data_events_recent"))
if os.path.isdir(os.path.join(st, "pattern_trader")):
    srcs.append(("pattern_trader", os.path.join(st, "pattern_trader", "counterfactual_trades.json"), None,
                 os.path.join(st, "pattern_trader", "futures_ledger.json"), os.path.join(st, "pattern_trader.json"),
                 "events_recent"))
print("   %-32s %5s %8s %8s %7s %6s %5s %5s  etiket v3/v2/v1/v1c/none" % (
    "defter", "n_net", "ort.net", "ort.brüt", "maliyet", "brüt*", "fund-", "belir"))
for key, cfp, only, _led, _sum, _evk in srcs:
    d = rj(cfp)
    if d is None:
        continue
    if unreadable(d):
        print("   %-32s OKUNAMADI (%s)" % (key, d["__err__"]))
        warns.append("%s: karşı-olgusal dosyası okunamadı" % key)
        continue
    rows = [t for t in (d.get("trades") or []) if isinstance(t, dict) and (only is None or t.get("book") == only)]
    outs = [t.get("outcome") for t in rows if isinstance(t.get("outcome"), dict)]
    ns = net_stats(outs)
    cnt = {"v3": 0, "v2": 0, "v1": 0, "v1c": 0, "none": 0, "diğer": 0}
    new_unlabelled = 0
    for t in rows:
        o = t.get("outcome")
        if not isinstance(o, dict):
            continue
        lv = LV.get(o.get("label_version"), "diğer")
        cnt[lv] += 1
        la = epoch(t.get("labeled_at"))
        if lv == "none" and rs is not None and la is not None and la >= rs:
            new_unlabelled += 1
    print("   %-32s %5s %8s %8s %7s %6s %5s %5s  %d/%d/%d/%d/%d%s" % (
        key, ns.get("n_net"), ns.get("mean_r_net"), ns.get("mean_r_gross"), ns.get("mean_cost_r"), ns.get("n_gross_only"),
        ns.get("n_net_funding_incomplete"), ns.get("n_net_intrabar_ambiguous"), cnt["v3"], cnt["v2"], cnt["v1"], cnt["v1c"],
        cnt["none"], (" (+%d diğer)" % cnt["diğer"]) if cnt["diğer"] else ""))
    if new_unlabelled:
        warns.append("%s: yeniden başlatmadan sonra %d yeni etiket label_version TAŞIMIYOR (net etiket yolu çalışmıyor?)"
                     % (key, new_unlabelled))
print("   (ort.brüt = bütün etiketlilerin brüt ort.; brüt* = net'i olmayan etiketli (v1/v1c/hata); fund- = funding eksik net;"
      " belir = bar içi sırası belirsiz net. v1c yaklaşık net ortalamaya GİRMEZ; karnede n_approx_v1c)")

# (2026-09-30) Box değerlendirme süresi/gecikmesi öğrenme raporundaki Box zamanlayıcısı satırlarında (GIL notuyla).

# ---------------------------------------------------------------- canlı muhasebe düzeltmesi (stop taşıma)
print("\n-- canlı muhasebe düzeltmesi (stop taşıma; öğrenme kapalıyken de PAPER muhasebesini değiştirir — tasarım gereği)")
print("   %-32s %9s %9s %11s" % ("defter", "PRE_MOVE", "ATLANAN", "kapanış PRE"))
for key, _cfp, _only, ledp, sump, evk in srcs:
    n_pre = n_skip = 0
    if key == "main":
        n_pre, n_skip = int(main_pre or 0), int(main_skip or 0)       # ana defter olayları günlükte
    elif sump:
        sd = rj(sump)
        for e in ((sd or {}).get(evk) or []) if isinstance(sd, dict) and not unreadable(sd) else []:
            if not isinstance(e, dict):
                continue
            at = epoch(e.get("at"))
            if rs is not None and (at is None or at < rs):
                continue
            if e.get("kind") == "BAR_PRE_MOVE_STOP":
                n_pre += 1
            elif e.get("kind") == "BAR_SKIPPED" and e.get("reason") == "OPENED_BEFORE_STOP_MOVE":
                n_skip += 1
    led = rj(ledp)
    n_close = 0
    for r in ((led or {}).get("history") or []) if isinstance(led, dict) and not unreadable(led) else []:
        ef = ((r.get("features") or {}).get("exit_fill") or {}) if isinstance(r, dict) and isinstance(r.get("features"), dict) else {}
        if isinstance(ef, dict) and ef.get("stop_in_force") == "PRE_MOVE":
            ca = epoch(r.get("closed_at"))
            if rs is None or (ca is not None and ca >= rs):
                n_close += 1
    print("   %-32s %9d %9d %11d" % (key, n_pre, n_skip, n_close))
print("   (PRE_MOVE = BAR_PRE_MOVE_STOP olayı; ATLANAN = BAR_SKIPPED/OPENED_BEFORE_STOP_MOVE; kapanış PRE = exit_fill."
      "stop_in_force=PRE_MOVE; yeniden başlatmadan beri. Kâğıt defterlerde son 30 olay tutulur; ana defter günlükten)")

# ---------------------------------------------------------------- yedekler
print("\n-- yedekler: saatlik yedek shared_experience/archive/segments'ı ve advice/archive/segments'ı YALNIZ UTC 00'da, "
      "türetilmiş advice/advisor_state.v1.gz'yi saatlikte HİÇ taşımaz (son saatlik yedek skipped_xp_segments=%s); "
      "günlük/haftalık/elle yedek hepsini taşır. deploy/restore.sh artık GERÇEKTEN geri yükler ('restore ARŞİV --yes') ve "
      "eksik segmentleri önceki state'ten (state.pre-restore-<ts>) sha256 ile geri kopyalar; çıktıda xp_segments.missing "
      "boş değilse eksikleri en yeni UTC-00/günlük yedekten kopyalayın (danışman o segmentte WAITING_SEGMENTS ile bekler; "
      "karar etkilenmez). restore.sh geri yüklenen state'i (ve vault.restored-* kopyasını) worker'ın servis kullanıcısına "
      "chown -R ile verip worker'ı öyle başlatır; bir adım düşerse ya da Ctrl+C gelirse \"GERİ YÜKLEME BAŞARISIZ\" yazar "
      "(gerekiyorsa chown komutunu da) ve worker DURMUŞ kalır; SSH kopması ya da | head işi yarıda kesmez. Ayrıntı: "
      "dağıtım sonu metni (GERİ YÜKLEME)."
      % (bk_skip or "?"))

# ---------------------------------------------------------------- bellek (katman ≈ 25 MB kalıcı)
lm = h.get("learning_mode") if isinstance(h.get("learning_mode"), dict) else {}
hwm = fl((lm.get("memory") or {}).get("hwm_mb")) if isinstance(lm.get("memory"), dict) else None
ph, pa, wa = fl(pre_hwm), fl(pre_age), fl(w_age)
if hwm is not None and ph is not None and pa is not None and wa is not None and wa < pa:
    # (2026-09-30) hwm süreç ömrüyle büyür: genç süreç yaşlı sürecin tepesiyle karşılaştırılmaz (yanıltıcı farklar)
    print("\n-- bellek: hwm_mb %.0f · dağıtım öncesi %.0f (o süreç %.1f sa çalışmıştı; bu süreç %.1f sa) — hwm süreç ömrüyle "
          "büyür: bu süreç o yaşa gelene kadar karşılaştırılmaz; büyüme hızı öğrenme raporundaki bellek örneklerinde"
          % (hwm, ph, pa / 3600.0, wa / 3600.0))
elif hwm is not None and ph is not None:
    print("\n-- bellek: hwm_mb %.0f (dağıtım öncesi %.0f%s → %+.0f MB; katman ≈ 25 MB kalıcı, danışman ~1000 satırlık depoda "
          "birkaç MB (index_mb danışman bölümünde); eşik +50 MB)"
          % (hwm, ph, (", o süreç %.1f sa" % (pa / 3600.0)) if pa is not None else "", hwm - ph))
    if hwm - ph >= 50:
        warns.append("hwm_mb dağıtım öncesinden %+.0f MB (≥ +50 MB)" % (hwm - ph))
else:
    print("\n-- bellek: hwm_mb %s · dağıtım öncesi %s (karşılaştırılamadı)" % (hwm, ph))

# ---------------------------------------------------------------- YALNIZ KATMAN geri alma tetikleri
print("\n-- KATMAN GERİ ALMA TETİKLERİ (yalnız ortak deneyim; öğrenme modu GERİ ALINMAZ): (1) devre kesici  "
      "(2) toplayıcı adım p95 > 1500 ms (geri doldurma bittikten sonra; danışman süresi HARİÇ)  (3) depo DEGRADED")
res = []
if not isinstance(s, dict):
    res = [("devre kesici", None, "status.json yok"), ("adım p95", None, "status.json yok"), ("depo", None, "status.json yok")]
else:
    tripped = str(s.get("state")) == "DISABLED_BY_BREAKER" or bool((s.get("breaker") or {}).get("tripped"))
    res.append(("devre kesici", tripped, "durum %s" % s.get("state")))
    if p95 is None:
        res.append(("adım p95", None, "ölçüm yok"))
    elif not bf_done:
        res.append(("adım p95", None, "p95 %.0f ms — geri doldurma sürüyor (≈ 1000 ms beklenir; tetik geri doldurma bitince)" % p95))
    else:
        res.append(("adım p95", p95 > 1500, "p95 %.0f ms (kararlı < 1000 ms beklenir; tetik > 1500 ms)" % p95))
        if 1000 <= p95 <= 1500:
            warns.append("katman adım p95 %.0f ms ≥ 1000 ms (kararlı düzeyin üstünde)" % p95)
    deg = str(s.get("state")) == "DEGRADED" or store.get("state") == "DEGRADED"
    res.append(("depo", deg, "depo %s · katman %s" % (store.get("state"), s.get("state"))))
for name, f, txt in res:
    print("   %-13s %-14s %s" % ("[TETİKLENDİ]" if f is True else ("[tamam]" if f is False else "[ölçülemedi]"), name, txt))
for a in alarms:
    print("   ALARM: %s" % a)
for w in warns:
    print("   UYARI: %s" % w)
fired = [r for r in res if r[1] is True]
if fired:
    print("\n   >>> KATMAN GERİ ALMA TETİĞİ: %s. Katmanı kapatın (gölge danışman da kapanır; öğrenme modu ve net etiket "
          "AYNEN sürer):" % ", ".join(r[0] for r in fired))
    print("       config.yaml → shared_experience: {enabled: false, mode: RECORD}  (ya da drop-in "
          "Environment=TRADINGBOT_SHARED_EXPERIENCE=off) ; sudo systemctl restart tradingbot-worker")
    print("       (drop-in için systemctl edit kaydedince daemon-reload yapar: önce systemctl show tradingbot-worker -p "
          "NeedDaemonReload → no olmalı ve systemctl cat'te MemoryMax=6G görünmeli; systemctl revert KULLANMAYIN)")
    print("       state/shared_experience/ kalır ve zararsızdır. Bu --check çıktısını bana iletin.")
else:
    print("   tetiklenen yok")
PY
  then
    echo "   ortak deneyim raporu okunamadı"
  fi
}

# --check (2026-09-30): GÖLGE DANIŞMAN durumu — advice/advisor_status.json + health.json["shared_experience"]["advisor"] +
# (varsa) advice/walkforward_summary.json + tavsiye dosya boyları; yetişmede lag_rows örnekleri ($ADV_SAMPLES; son satır
# "ADVSAMPLE ..." çağırana ölçüm örneğidir). Salt okunur. Danışmanın adım süresi toplayıcının step_ms'iyle KARIŞTIRILMAZ
# (katman eşikleri — uyarı ≥ 1000 ms, geri alma > 1500 ms — yalnız toplayıcıyı ölçer).
adv_report() {
  local rs_epoch="" wpid="" interval="" out s
  if [[ -r "$MARK_RESTART" ]]; then rs_epoch="$(sed -n 1p "$MARK_RESTART" || true)"; fi
  wpid="$(systemctl show "$WORKER" -p MainPID --value 2>/dev/null || true)"
  [[ "$wpid" =~ ^[1-9][0-9]*$ ]] || wpid="0"
  interval="$(systemctl show "$WORKER" -p ExecStart --value 2>/dev/null | grep -oE -- '--interval[ =][0-9]+' | grep -oE '[0-9]+' | head -n 1 || true)"
  out="$(py - "$STATE" "$APP" "$rs_epoch" "$wpid" "${interval:-15}" "$ADV_SAMPLES" "$ADVISOR_SHA_EXPECT" "$SCHEMA_SHA_EXPECT" \
            "$WF_SHA_EXPECT" <<'PY'
import datetime as dt
import json
import os
import pwd
import sys
import time

st, app, rs_epoch, wpid, interval_s, advs, ADV_SHA, SCH_SHA, WF_SHA = sys.argv[1:10]
now = time.time()
XP = os.path.join(st, "shared_experience")
ADV = os.path.join(XP, "advice")
OFFS = ("off", "false", "0", "disabled")
LAB_REAL = ("GIR", "NOTR", "GIRME", "VERI_AZ", "LATE", "NO_GROUP")
LAB_CF = ("GIR", "NOTR", "GIRME", "VERI_AZ")


def rj(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        return {"__err__": type(exc).__name__}


def unreadable(d):
    return isinstance(d, dict) and "__err__" in d


def fl(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def epoch(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def mb(n):
    return "%.2f MB" % (n / 1048576.0) if isinstance(n, (int, float)) else "?"


def dget(d, k):
    v = d.get(k) if isinstance(d, dict) else None
    return v if isinstance(v, dict) else {}


warns = []
rs = fl(rs_epoch)
pid = int(wpid) if str(wpid).isdigit() else 0
h = rj(os.path.join(st, "health.json"))
h = h if isinstance(h, dict) and not unreadable(h) else {}
try:
    import yaml
    with open(os.path.join(app, "config.yaml"), encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
except Exception:  # noqa: BLE001
    raw = {}
xp_cfg = raw.get("shared_experience") if isinstance(raw.get("shared_experience"), dict) else {}
xp_env = (os.environ.get("TRADINGBOT_SHARED_EXPERIENCE") or "").strip()
adv_env = (os.environ.get("TRADINGBOT_SHARED_EXPERIENCE_ADVISOR") or "").strip()
cfg_on = xp_cfg.get("enabled") is True and str(xp_cfg.get("mode") or "").upper() == "RECORD" \
    and str(xp_cfg.get("advisor_mode") or "").upper() == "RECORD"
expected_on = cfg_on and not xp_env and not adv_env
why_off = []
if not cfg_on:
    why_off.append("config.yaml advisor_mode=%s (katman enabled=%s mode=%s)" % (
        xp_cfg.get("advisor_mode", "yok→OFF"), xp_cfg.get("enabled"), xp_cfg.get("mode")))
if xp_env:
    why_off.append("env TRADINGBOT_SHARED_EXPERIENCE=%s" % xp_env)
if adv_env:
    why_off.append("env TRADINGBOT_SHARED_EXPERIENCE_ADVISOR=%s" % adv_env)
period = max(1.0, fl(interval_s) or 15.0) * 60.0 + max(0.0, fl(h.get("seconds")) or 0.0)   # tur aralığı + son tur
print("-- gölge danışman: config advisor_mode=%s → %s" % (
    xp_cfg.get("advisor_mode", "yok (OFF)"), "AÇIK (RECORD)" if expected_on else "KAPALI (" + "; ".join(why_off) + ")"))
hx = h.get("shared_experience") if isinstance(h.get("shared_experience"), dict) else {}
ha = hx.get("advisor") if isinstance(hx.get("advisor"), dict) else None
print("   health.json advisor: %s" % (json.dumps(ha, ensure_ascii=False) if ha is not None else
                                     "anahtar yok (danışman kapalı ya da yeni worker ilk turunu bitirmedi)"))
if ha is not None and not expected_on:
    warns.append("danışman config/env ile KAPALI olmalı ama health.json'da advisor var (worker yeniden başlatılmadı mı?)")
if ha is not None and ha.get("state") == "INIT_ERROR":
    warns.append("danışman KURULAMADI (INIT_ERROR: %s) — toplayıcı ve kararlar etkilenmez" % ha.get("error"))

s = rj(os.path.join(ADV, "advisor_status.json"))
sample = None
if unreadable(s):
    warns.append("advice/advisor_status.json OKUNAMADI (%s)" % s["__err__"])
    s = None
if not isinstance(s, dict):
    if expected_on:
        print("   advisor_status.json YOK — ilk RECORD turu henüz bitmedi (yeni worker önce spot WFO + tur koşar)")
        if rs is not None and now - rs > 2 * period + 3600:
            warns.append("danışman açık ama yeniden başlatmadan %.0f dk sonra advisor_status.json yok" % ((now - rs) / 60.0))
    else:
        print("   danışman KAPALI — durum dosyası yok (beklenen)")
else:
    ls = epoch(s.get("last_step_at"))
    age = (now - ls) if ls is not None else None
    stale = rs is not None and ls is not None and ls < rs
    state = str(s.get("state"))
    mode = s.get("mode")
    pos = dget(s, "position")
    lag = pos.get("lag_rows")
    rec = dget(s, "record")
    seg = dget(s, "segments")
    br = dget(s, "breaker")
    cnt = dget(s, "counters")
    snap = dget(s, "snapshot")
    if not expected_on:
        print("   (danışman KAPALI; aşağıdaki durum dosyası son çalıştığı zamandan kalmadır — %s)" % s.get("last_step_at"))
    elif stale:
        print("   (durum dosyası yeniden başlatmadan ÖNCE yazılmış — yeni worker henüz danışman adımı atmadı)")
    print("   durum %s%s · iç durum %s · mod %s · lag_rows %s · adım %s · son adım %s (%s)" % (
        state, "" if state == "OK" else "  !!!", s.get("internal_state"), mode, lag, s.get("steps"), s.get("last_step_at"),
        ("%.0f dk önce" % (age / 60.0)) if age is not None else "?"))
    born = s.get("advisor_born_at")
    print("   doğum (advisor_born_at): %s%s" % (
        born or "—", "" if born else ("  (yetişme bitti; doğum yeni satır yazılan ilk CANLI toplu yazımda kurulur)"
                                      if mode == "live" else "  (yetişme sürüyor; doğum yetişme bittikten sonra kurulur)")))
    seal_ok = s.get("advisor_sha") == ADV_SHA and s.get("situation_schema_sha") == SCH_SHA
    print("   mühür: advisor_sha %s · situation_schema_sha %s → %s (beklenen %s / %s) · etki %s" % (
        s.get("advisor_sha"), s.get("situation_schema_sha"), "AYNI" if seal_ok else "!!! FARKLI", ADV_SHA, SCH_SHA,
        s.get("effect")))
    if not seal_ok:
        warns.append("danışman mührü beklenenden farklı (advisor_sha %s, schema %s)" % (s.get("advisor_sha"),
                                                                                      s.get("situation_schema_sha")))
    if s.get("effect") != "NONE":
        warns.append("danışman etkisi NONE değil (%s)" % s.get("effect"))
    # --- 24 sa: defter başına tavsiye sayıları (gerçek ve olsaydı kanalı)
    ring = s.get("ring_24h") if isinstance(s.get("ring_24h"), dict) else {}
    print("   son 24 sa tavsiye (toplu yazım saatine göre; defter · kanal: GİR/NÖTR/GİRME/VERİ AZ[/GEÇ/GRUPSUZ]):")
    if not ring:
        print("     (henüz yok)")
    for b in sorted(ring):
        r = ring.get(b) if isinstance(ring.get(b), dict) else {}
        re_ = r.get("real") if isinstance(r.get("real"), dict) else {}
        cf_ = r.get("cf") if isinstance(r.get("cf"), dict) else {}
        print("     %-32s gerçek %s   olsaydı %s" % (b, "/".join(str(int(re_.get(k, 0) or 0)) for k in LAB_REAL),
                                                    "/".join(str(int(cf_.get(k, 0) or 0)) for k in LAB_CF)))
    # --- kayıt
    emitted, written = rec.get("emitted"), rec.get("written")
    dup = cnt.get("advice_duplicate")
    print("   kayıt: üretilen %s · yazılan %s · yeniden deneme bekleyen %s · düşürülen %s · üst işaretle atlanan %s · "
          "yinelenen %s · engel %s" % (emitted, written, rec.get("retry_pending"), rec.get("dropped"),
                                       rec.get("skipped_hwm"), dup, rec.get("blocked") or "yok"))
    if isinstance(emitted, int) and isinstance(written, int) and written < emitted:
        warns.append("danışman kaydı: yazılan %d < üretilen %d (yinelenen %s, bekleyen %s, düşürülen %s)" % (
            written, emitted, dup, rec.get("retry_pending"), rec.get("dropped")))
    # --- hatalar
    print("   hatalar: %s (son: %s) · devre kesici %s (art arda hata %s/%s · aşım %s/%s × %s) · bozuk segment %s · bozuk "
          "satır %s · bekleyen segment %s · anlık görüntü ertelenen %s" % (
              s.get("errors"), s.get("last_error") or "—", "AÇILDI" if br.get("tripped") else "kapalı",
              br.get("consecutive_errors"), br.get("errors_limit"), br.get("consecutive_overruns"),
              br.get("overruns_limit"), br.get("overrun_factor"), seg.get("segments_bad"), seg.get("lines_bad"),
              json.dumps(seg.get("waiting"), ensure_ascii=False) if seg.get("waiting") else "yok",
              snap.get("deferred_consecutive")))
    wl = s.get("warnings") if isinstance(s.get("warnings"), list) else []
    if wl:
        print("   danışman uyarıları: %s" % ", ".join(str(x) for x in wl))
    # --- süre: danışman ≠ toplayıcı
    xs = rj(os.path.join(XP, "status.json"))
    xs = xs if isinstance(xs, dict) and not unreadable(xs) else {}
    print("   adım süresi ms — DANIŞMAN: p50 %s · p95 %s · son %s (health p95 %s)   |   TOPLAYICI (ayrı; katman eşikleri "
          "bunu ölçer): p50 %s · p95 %s" % (s.get("step_ms_p50"), s.get("step_ms_p95"), s.get("step_ms_last"),
                                           (ha or {}).get("step_ms_p95"), xs.get("step_ms_p50"), xs.get("step_ms_p95")))
    print("   (beklenen danışman: yetişme adımı ≈ 1,0–1,4 sn (pay 1000 ms; kesici 3 × pay aşımı art arda 3 kez); canlı "
          "≈ 5–15 ms, anlık görüntü adımı ≤ ~350 ms; son 50 adımın p95'i yetişme adımlarını da içerebilir)")
    print("   dizin %s MB / tavan %s MB" % (s.get("index_mb"), s.get("max_index_mb")))
    # --- UYARI kuralları (RELEASE_NOTES): durum != OK; yetişmede lag azalmıyor; yazılan < üretilen; bozuk segment > 0
    if expected_on and not stale:
        if state != "OK":
            why = {"WAITING_SEGMENTS": "okunamayan ana depo segmenti BEKLENİYOR (atlanmaz) — geri yüklemeden sonraysa eksik "
                                       "segmenti en yeni UTC-00/günlük yedekten kopyalayın",
                   "RECORD_BLOCKED": "tavsiye deposu yazılamıyor (disk/izin/tavan) — satırlar bellekte bekler",
                   "DEGRADED": "danışman DEGRADED (dizin/disk tavanı): canlı tavsiye durdu",
                   "DISABLED_BY_BREAKER": "danışman DEVRE KESİCİ ile kapandı (süreç boyunca; worker restart yeniden dener)"}
            warns.append("danışman durumu %s: %s (kararlar ETKİLENMEZ)" % (state, why.get(state, "beklenmeyen durum")))
        if age is not None and age > 2 * period:
            warns.append("son danışman adımı %.0f dk önce (> 2 tur ≈ %.0f dk)" % (age / 60.0, 2 * period / 60.0))
        if isinstance(seg.get("segments_bad"), int) and seg.get("segments_bad") > 0:
            warns.append("danışman bozuk segment sayısı %s > 0" % seg.get("segments_bad"))
        if isinstance(s.get("errors"), int) and s.get("errors") > 0:
            warns.append("danışman hata sayısı %s (son: %s); 5 art arda hata kesiciyi açar" % (s.get("errors"),
                                                                                             s.get("last_error")))
        if any(str(x).startswith("SNAPSHOT_DEFERRED") for x in wl):
            warns.append("danışman anlık görüntüsü art arda ertelendi (%s)" % snap.get("deferred_consecutive"))
    # --- yetişme: lag_rows iki --check arasında azalıyor mu (aynı süreç, danışman adımı ilerlemişse)
    steps = s.get("steps") if isinstance(s.get("steps"), int) else None
    lagv = lag if isinstance(lag, int) else None
    if pid and steps is not None and not stale and expected_on:
        prev = None
        try:
            with open(advs, encoding="utf-8") as fh:
                for ln in fh:
                    p_ = ln.split()
                    if len(p_) == 5 and p_[1].isdigit() and int(p_[1]) == pid and p_[4].isdigit():
                        prev = (fl(p_[0]), p_[2], int(p_[3]) if p_[3].lstrip("-").isdigit() else None, int(p_[4]))
        except OSError:
            pass
        if mode == "catch_up":
            if prev is not None and prev[1] == "catch_up" and prev[2] is not None and lagv is not None:
                if steps > prev[3] and lagv >= prev[2]:
                    warns.append("danışman yetişmesi ilerlemiyor: lag_rows %d → %d (%d danışman adımında)" % (
                        prev[2], lagv, steps - prev[3]))
                elif steps > prev[3]:
                    print("   yetişme: lag_rows %d → %d (%d adımda, adım başına %.0f satır)" % (
                        prev[2], lagv, steps - prev[3], (prev[2] - lagv) / float(steps - prev[3])))
                else:
                    print("   yetişme: iki --check arasında danışman adımı yok (tur bekleniyor); lag_rows %s" % lagv)
            else:
                print("   yetişme sürüyor: lag_rows %s — sonraki --check'te AZALMASI beklenir (VPS deposu küçük: bir-iki tur)"
                      % lag)
        if prev is None or prev[3] != steps or prev[1] != str(mode):
            sample = "%d %d %s %s %d" % (int(now), pid, mode, lagv if lagv is not None else "-", steps)

# ---------------------------------------------------------------- tavsiye dosyaları
def size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return None


seg_n, seg_b = 0, 0
try:
    for e in os.scandir(os.path.join(ADV, "archive", "segments")):
        if e.is_file():
            seg_n += 1
            seg_b += e.stat().st_size
except OSError:
    pass
if os.path.isdir(ADV):
    print("   tavsiye dosyaları: advice.jsonl %s · arşiv %d segment / %s · advisor_state.v1.gz %s · advisor_meta.json %s" % (
        mb(size(os.path.join(ADV, "advice.jsonl"))), seg_n, mb(seg_b),
        mb(size(os.path.join(ADV, "advisor_state.v1.gz"))) if size(os.path.join(ADV, "advisor_state.v1.gz")) is not None
        else "yok (ilk anlık görüntü ~60 adımda)",
        "var" if os.path.exists(os.path.join(ADV, "advisor_meta.json")) else "yok"))
else:
    print("   tavsiye klasörü (state/shared_experience/advice/) yok")

# ---------------------------------------------------------------- son walk-forward taraması (CLI; otomatik yazan YOK)
sm = rj(os.path.join(ADV, "walkforward_summary.json"))
if isinstance(sm, dict) and not unreadable(sm):
    pr = dget(sm, "primary")
    print("   son walk-forward taraması: %s · wf_sha %s%s · birincil (ALL_REAL, advice): U_mean %s CI %s · Δ %s · n_T %s · "
          "n_G %s" % (sm.get("generated_at"), sm.get("wf_sha"), "" if sm.get("wf_sha") == WF_SHA else " !!! (beklenen %s)"
                      % WF_SHA, pr.get("U_mean"), pr.get("U_mean_ci"), pr.get("delta"), pr.get("n_T"), pr.get("n_G")))
    lk = sm.get("looks") if isinstance(sm.get("looks"), dict) else {}
    for hname in ("H1", "H2"):
        v = lk.get(hname) if isinstance(lk.get(hname), dict) else None
        print("     bakış %s: %s" % (hname, ("%s (%s; L1 %s)" % (v.get("verdict"), v.get("reason"), v.get("L1")))
                                     if v else "yok (tarama --looks olmadan)"))
    print("     (PENDING / INVARIANTS_UNDECLARED, --invariants-green beyan edilene dek BEKLENİR; ara bakış kanıt değildir)")
    if sm.get("wf_sha") and sm.get("wf_sha") != WF_SHA:
        warns.append("walkforward_summary.json wf_sha %s ≠ %s" % (sm.get("wf_sha"), WF_SHA))
else:
    print("   son walk-forward taraması: yok (otomatik yazan YOK; elle, salt okur, ayrı süreç):")
    print("   sudo -u %s bash -c 'cd %s && TRADINGBOT_STATE_DIR=%s nice -n 19 %s -m tradingbot shared-experience-advisor "
          "--looks --summary-out %s'" % (pwd.getpwuid(os.getuid()).pw_name, app, st, sys.executable,
                                         os.path.join(ADV, "walkforward_summary.json")))

for w in warns:
    print("   UYARI: %s" % w)
if not warns:
    print("   danışman uyarısı yok")
print("   YALNIZ DANIŞMANI KAPATMAK (katman, öğrenme, kararlar aynen): config.yaml → shared_experience.advisor_mode: OFF ;"
      " sudo systemctl restart tradingbot-worker   (ya da drop-in Environment=TRADINGBOT_SHARED_EXPERIENCE_ADVISOR=off;"
      " systemctl edit kaydedince daemon-reload yapar: önce systemctl show tradingbot-worker -p NeedDaemonReload → no"
      " olmalı ve systemctl cat'te MemoryMax=6G görünmeli; systemctl revert KULLANMAYIN)."
      " advice/ klasörünü SİLMEYİN (doğum ve üst işaret oradan gelir).")
if sample:
    print("ADVSAMPLE " + sample)
PY
)" || { printf '%s\n' "$out"; echo "   danışman raporu okunamadı"; return 0; }
  printf '%s\n' "$out" | grep -v '^ADVSAMPLE ' || true
  s="$(printf '%s\n' "$out" | sed -n 's/^ADVSAMPLE //p' | tail -n 1 || true)"
  if [[ -n "$s" && -d "$LOGDIR" ]]; then echo "$s" >> "$ADV_SAMPLES" 2>/dev/null || true; fi
}

# Kodu dağıtım ÖNCESİ duruma döndürür: worker DURUR → (yalnız eski kod öğrenme modunu TANIMIYORSA: Formasyon öğrenme
# planları temizlenir, YENİ kod hâlâ yerindeyken) → aynı dal adı + önceki commit → requirements değiştiyse pip →
# (2026-10-06) bilinen yerel config.yaml satırı geri alındıysa dağıtım öncesi config.yaml baytları AYNEN → worker
# başlar, panel yeniden başlar. Yedeklere ve defterlere dokunmaz. Bu sürümün ön koşulu fc63481 öğrenme modunu tanıdığı için
# plan temizliği ÇALIŞMAZ (2026-09-29): eski kod öğrenme planlarını kendisi yönetir; temizlik onları gereksiz yere iptal
# ederdi. Temizlik gerekip başarısızsa (çıkış 2: plans.json okunamadı/bozuk) DURUR: worker kapalı, yeni kod yerinde kalır.
REQ_CHANGED=""
PREP_NEEDED=""
revert_code() {
  trap - ERR
  trap '' INT TERM              # geri alma yarıda kesilmez (ikinci Ctrl+C dahil); ÖNCE iş, SONRA ekrana yazı
  systemctl stop "$WORKER" || true
  if [[ -n "$PREP_NEEDED" && -f "$APP/scripts/learning_mode_rollback_prep.py" ]]; then
    local rc=0
    svc_run "$APP" "$VENV/bin/python" scripts/learning_mode_rollback_prep.py --state "$STATE" || rc=$?
    if (( rc != 0 )); then
      cat >&2 <<EOF

DUR: GERİ ALMA DURDURULDU — öğrenme planı temizliği başarısız (çıkış $rc; 2 = pattern_trader/plans.json okunamadı/bozuk).
  Durum: worker DURDURULDU; kod hâlâ ${T7} (yeni); eski kod BAŞLATILMADI; yedek ve defterlere dokunulmadı.
  Yapılacak (plans.json'a elle bakıldıktan sonra):
    sudo -u $SVC_USER $VENV/bin/python $APP/scripts/learning_mode_rollback_prep.py --state $STATE
    sudo -u $SVC_USER git -C $APP checkout -q -B $BRANCH_NOW ${PREV:0:7}     # (dal yoksa: checkout ${PREV:0:7})
    sudo systemctl start $WORKER; sudo systemctl restart $DASH
  Ya da yeni kodla sürdürmek için: sudo systemctl start $WORKER. Bu çıktıyı bana iletin.
EOF
      exit 3
    fi
  else
    echo "   (öğrenme planı temizliği gerekmiyor: ${PREV:0:7} öğrenme modunu tanıyor)" || true
  fi
  if [[ "$BRANCH_NOW" != "detached" ]]; then
    # dalı yalnız bu betiğin az önce yaptığı ileri sarmadan geri alır (dal önceden tam olarak PREV'deydi)
    gitc checkout -q -B "$BRANCH_NOW" "$PREV" || gitc checkout -q "$PREV" \
      || echo "   UYARI: checkout başarısız — elle: sudo -u $SVC_USER git -C $APP checkout ${PREV:0:7}" >&2
  else
    gitc checkout -q "$PREV" || echo "   UYARI: checkout başarısız — elle: sudo -u $SVC_USER git -C $APP checkout ${PREV:0:7}" >&2
  fi
  if [[ -n "$REQ_CHANGED" ]]; then
    "$VENV/bin/pip" install -q -r "$APP/requirements.txt" >/dev/null 2>&1 || true
    chown -R "$SVC_USER:$SVC_USER" "$VENV" 2>/dev/null || true
  fi
  local cfgnote=""
  if [[ -n "$CFG_LOCAL" && -n "$CFG_RESTORED" ]]; then        # (2026-10-06) dağıtım öncesi baytlar, kill switch satırı DAHİL
    cfg_put_back
    cfgnote="; dağıtım öncesi kill switch satırı (evidence_subprocess: false) AYNEN geri kondu — ${PREV:0:7} alt süreçsiz"
  fi
  systemctl start "$WORKER" || true
  systemctl restart "$DASH" || true
  echo "   GERİ ALINDI: kod ${PREV:0:7} (izlenen config.yaml da onunla birlikte ${PREV:0:7}'deki haline döndü — m2x_aggressive" \
       "bölümü yok, eski kod onu tanımaz${cfgnote}); worker başlatıldı, panel yeniden başlatıldı (bu sürüm state'e yeni" \
       "dosya yazmaz — M2X kapalı; karşı-olgusal .pre-cf-net-<UTC> yedekleri yanlarında; engine-app'e dokunulmadı)" || true
}

# KARŞI-OLGUSAL NET DOLGU (2026-09-29): worker DURMUŞKEN, yeni kod yerindeyken, yeniden başlatmadan ÖNCE; servis
# kullanıcısı + servis ortamıyla (TRADINGBOT_DATA/…: filtre önbelleği ve yollar worker'la aynı). Önce kuru çalışma, sonra
# --apply. Çıkış 2 (state/config okunamadı) ya da başka hata → 1 döner (çağıran kodu geri alır). Mum çekimi zaman aşımına
# düşerse ya da mumlar alınamazsa (ağ) → --no-fetch --apply (kapalı-biçim yaklaşık v1c; net ortalamasına girmez, sonraki
# mumlu koşu v3'e yükseltir).
cf_table() {        # $1: betiğin --json raporu → defter başına tablo
  py - "$1" <<'PY'
import json
import sys
try:
    rep = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:  # noqa: BLE001
    print("   rapor okunamadı: %s" % exc)
    sys.exit(0)
print("   %-30s %5s %5s %5s %6s %6s %5s %9s %9s %9s  %s" % ("defter", "aday", "net", "v1c", "v1c→v3", "v1c=", "yok",
                                                         "brüt ort", "net ort", "v1c≈net", "yazım"))
bad = []
for key, b in (rep.get("books") or {}).items():
    if "error" in b:
        print("   %-30s OKUNAMADI: %s" % (key, b["error"]))
        bad.append(key)
        continue
    print("   %-30s %5s %5s %5s %6s %6s %5s %9s %9s %9s  %s" % (
        key, b.get("candidates"), b.get("net"), b.get("v1c"), b.get("v1c_upgraded"), b.get("v1c_kept"), b.get("none"),
        b.get("net_mean_r_gross"), b.get("net_mean_r_net"), b.get("v1c_mean_r_net_approx"), b.get("write")))
    if str(b.get("write") or "").startswith("SKIPPED"):
        bad.append(key)
if bad:
    print("   UYARI: %s — dosya okunamadı ya da diskte değişti (yazılmadı; eski etiketler aynen kaldı, yeni kod tembel "
          "doldurur). Worker durmuşken değişiyorsa bana haber verin." % ", ".join(bad))
PY
}
CF_OUT=""
cf_run() {          # $1: json yolu, $2...: ek argümanlar. Çıktı canlı basılır ve CF_OUT'a alınır; dönüş = betiğin çıkış kodu
  local js="$1" rc=0; shift
  install -o "$SVC_USER" -g "$SVC_USER" -m 0640 /dev/null "$js"
  CF_TMP="$(mktemp /tmp/tb-deploy-cf.XXXXXX)"
  # (2026-09-29, sahte VPS) ALT KABUKTA + boru: işlev çağrısına bağlı `> dosya` yönlendirmesi, bu sırada gelen SIGTERM'in
  # tuzağını (on_abort → geri alma) da o dosyaya yazdırıyordu (geri alma yapılıyor ama ekranda/günlükte görünmüyordu).
  # pipefail: dönüş kodu dolgu betiğinin kodudur (tee/sed başarılıysa).
  ( trap - ERR; svc_run "$APP" timeout -k 30 "$CF_FETCH_TIMEOUT" "$VENV/bin/python" scripts/cf_backfill_net.py \
      --state "$STATE" --config "$APP/config.yaml" --json "$js" "$@" ) 2>&1 | tee "$CF_TMP" | sed -u 's/^/   | /' || rc=$?
  CF_OUT="$(cat "$CF_TMP")"; rm -f "$CF_TMP"; CF_TMP=""
  return "$rc"
}
cf_rc_why() { if [[ "$1" == 2 ]]; then printf ': state/config okunamadı'; fi; }
cf_backfill() {
  local rc=0 nofetch="" nfail=0
  echo "   (kuru çalışma: hiçbir dosyaya yazılmaz; mumlar borsadan, en çok ${CF_FETCH_TIMEOUT} sn)"
  cf_run "$CF_DRY_JSON" || rc=$?
  if (( rc == 124 || rc == 137 )); then
    echo "   !!! mum çekimi ${CF_FETCH_TIMEOUT} sn'de bitmedi (ağ?) → --no-fetch ile sürdürülüyor (yaklaşık v1c)"
    nofetch=1; rc=0
    cf_run "$CF_DRY_JSON" --no-fetch || rc=$?
  fi
  if (( rc != 0 )); then
    echo "   HATA  karşı-olgusal net dolgu kuru çalışması başarısız (çıkış $rc$(cf_rc_why "$rc"))"
    return 1
  fi
  cf_table "$CF_DRY_JSON"
  nfail="$(printf '%s\n' "$CF_OUT" | grep -c 'mumları alınamadı' || true)"
  if (( nfail > 0 )); then
    echo "   !!! kuru çalışmada $nfail sembol/dilim için mum alınamadı: o kayıtlar yaklaşık (v1c) yazılır (sonra mumla v3'e yükselir)"
  fi
  echo "   (uygula: her değişen dosya <dosya>.pre-cf-net-<UTC> olarak yedeklenir; diskte değişen dosya ATLANIR)"
  rc=0
  if [[ -n "$nofetch" ]]; then
    cf_run "$CF_APPLY_JSON" --no-fetch --apply || rc=$?
  else
    cf_run "$CF_APPLY_JSON" --apply || rc=$?
    nfail="$(printf '%s\n' "$CF_OUT" | grep -c 'mumları alınamadı' || true)"
    if (( rc == 124 || rc == 137 || (rc == 0 && nfail > 0) )); then
      echo "   !!! mum çekimi başarısız (çıkış $rc; alınamayan $nfail) → --no-fetch --apply (kalan eski etiketler yaklaşık v1c)"
      rc=0
      cf_run "$CF_APPLY_JSON" --no-fetch --apply || rc=$?
    fi
  fi
  if (( rc != 0 )); then
    echo "   HATA  karşı-olgusal net dolgu (--apply) başarısız (çıkış $rc$(cf_rc_why "$rc"))"
    return 1
  fi
  cf_table "$CF_APPLY_JSON"
  cat <<EOF
   Başka göç GEREKMEZ:
     * Öğrenme-ekstra kipi (record_selectivity) yalnız bundan sonraki YENİ girişleri etkiler: açık pozisyonlara ve mevcut
       karşı-olgusal kayıtlara dokunulmaz (yeni yalnız-kayıt kayıtları mevcut dosyalara eklenir; ayrı göç yok).
     * Ortak deneyim katmanı, gölge danışman (advice/) ve öğrenme modu dosyalarına bu sürüm göç yazmaz.
EOF
}

[[ $EUID -eq 0 ]] || die "sudo ile çalıştırın: sudo bash $0 ${MODE}"
[[ -d "$APP/.git" ]] || die "$APP bir git kopyası değil"
load_env

# ------------------------------------------------------------------ --detach: SSH'tan bağımsız ayrı görev + izleme
if [[ "$MODE" == "--detach" ]]; then
  UNIT="tb-deploy-$T7"
  if systemctl is-active --quiet "$UNIT"; then
    echo "dağıtım zaten çalışıyor ($UNIT). İzlemek için: sudo journalctl -u $UNIT -f -o cat"; exit 0
  fi
  START="$(date '+%Y-%m-%d %H:%M:%S')"
  systemd-run --unit="$UNIT" --collect --quiet --setenv=TRADINGBOT_BASE="$BASE" --setenv=TRADINGBOT_USER="$SVC_USER" \
    bash "$HERE/$(basename "${BASH_SOURCE[0]}")" deploy || die "ayrı görev başlatılamadı (systemd-run)"
  cat <<EOF
Dağıtım AYRI bir görev olarak başladı ($UNIT). Bu pencereyi ya da PC'yi kapatsanız da sürer.
  Ctrl+C yalnız İZLEMEYİ bırakır, dağıtımı ETKİLEMEZ.
  Sonra tekrar izlemek:  sudo journalctl -u $UNIT -f -o cat     (görev bitince: sudo tail -60 $LOGDIR/$T7-deploy.log)
  Dağıtımı durdurmak:    sudo systemctl stop $UNIT   (yeniden başlatmadan önceyse kod geri alınır)
EOF
  journalctl -u "$UNIT" -f -o cat --no-pager --since "$START" \
    > >(grep --line-buffered -vE '^ +root : |pam_unix\(sudo:session\)') & jp=$!
  sleep 3
  while systemctl is-active --quiet "$UNIT"; do sleep 3; done
  sleep 2; kill "$jp" 2>/dev/null || true
  echo; echo "görev bitti. Durum için: sudo bash $0 --check"
  exit 0
fi

# ------------------------------------------------------------------ --check: dağıtım sonrası durum (state'e dokunmaz)
if [[ "$MODE" == "--check" ]]; then
  say "kod"
  echo "   HEAD $(gitc rev-parse --short HEAD) ($(gitc symbolic-ref --quiet --short HEAD || echo detached)) — hedef $T7" \
       "$([[ -r "$LOGDIR/$T7-prev-commit.txt" ]] && echo "— dağıtım öncesi $(head -c 7 "$LOGDIR/$T7-prev-commit.txt")")"
  dirty_now="$(gitc status --porcelain --untracked-files=no || true)"
  if [[ -z "$dirty_now" ]]; then echo "   izlenen dosyalar: temiz"; else echo "   izlenen dosyalar: $(tr '\n' ' ' <<<"$dirty_now")"; fi
  engine_state
  say "servisler"
  for u in "$WORKER" "$DASH"; do
    echo "   $u: $(systemctl is-active "$u" || true), yeniden başlama sayısı $(systemctl show "$u" -p NRestarts --value)"
  done
  memory_report
  memmax_check
  echo "   panel: $(curl -fsS -m 5 http://127.0.0.1:8080/health/live 2>/dev/null | head -c 200 || echo 'yanıt yok')"
  say "ÖĞRENME MODU (yalnız PAPER)"
  learning_report
  say "ÖĞRENME-EKSTRA: SEÇİCİLİK YALNIZ KAYIT (2026-10-03, sahip kararı) — etkin kip, KAPATMA, defter başına sayılar"
  ro_report
  say "TUR FAZLARI + PATTERN KANITI ÖN ISITMASI (yalnız ölçüm; karar DEĞİŞMEZ)"
  perf_report
  say "PATTERN KANITI HIZLI kNN + YAYIM ÇAKIŞMASI (2026-10-06; karar DEĞİŞMEZ) — KAPATMA: history.evidence_fast_knn: false"
  knn_report
  echo "   KAPATMAK (kill switch; kod AYNI, kanıt AYNI — eski olay-başına döngü, ön ısıtma yine ~1.000 sn). YALNIZ BİR KEZ uygulayın:"
  echo "     sudo -u $SVC_USER sed -i 's/^history:\$/history:\n  evidence_fast_knn: false/' $APP/config.yaml && sudo systemctl restart $WORKER"
  echo "     (iki kez uygulanırsa yinelenen anahtar → ConfigError → worker BAŞLAMAZ; denetim: grep -c evidence_fast_knn $APP/config.yaml → 1)"
  echo "   BEKLENEN VPS SİNYALİ: yayımla çakışan turlar ~5–6 dk (8db1faf'ta 23–36 dk) · ön ısıtma birkaç sn (8db1faf'ta ~1.000 sn) ·"
  echo "   tur fazı \"pattern kanıtı\" ≈ 0 sn · \"pattern kNN hızlı yolu …\" uyarısı yok"
  say "PATTERN KANITI ALT SÜRECİ (8db1faf; bu sürümde varsayılan KAPALI — yalnız history.evidence_subprocess: true iken etkin)"
  evc_report
  say "ORTAK DENEYİM KATMANI (yalnız KAYIT) + KARŞI-OLGUSAL NET + CANLI MUHASEBE DÜZELTMESİ"
  xp_report
  say "GÖLGE DANIŞMAN (yalnız KAYIT; karar DEĞİŞMEZ)"
  adv_report
  say "defterler (şimdi)"
  book_snapshot
  if [[ -f "$LOGDIR/$T7-before.txt" ]]; then say "defterler (dağıtımdan hemen önce)"; cat "$LOGDIR/$T7-before.txt"; fi
  say "defterler: D4 (4h trend), C4 / C4S (mum varyasyonları), Box yapı modu, formasyon protokolü"
  py - "$STATE" <<'PY'
import json, os, sys
st = sys.argv[1]
for key, label in (("strategy_paper_trend4h", "D4 (4h trend, gözlem)"), ("strategy_paper_candle4h", "C4 (mum varyasyonları)"),
                   ("strategy_paper_candle4h_strict", "C4S (mum varyasyonları, sıkı)")):
    f = os.path.join(st, key + ".json")
    if not os.path.exists(f):
        print("   %s: özet henüz yok (ilk turdan sonra görünür)" % label)
        continue
    d = json.load(open(f, encoding="utf-8"))
    s, c = d.get("summary") or {}, d.get("counters") or {}
    print("   %s: özkaynak=%s açılan=%s kapanan=%s tur=%s son kural turu=%s"
          % (label, s.get("equity"), c.get("opened"), c.get("closed"), c.get("tours"), d.get("rule_evaluated_at")))
PY
  py - <<'PY' || true
import yaml
raw = yaml.safe_load(open("config.yaml", encoding="utf-8"))
print("   Box yapı modu (config):", (raw.get("structures") or {}).get("b1_box_fade"))
_ex = {b.get("name"): b for b in ((raw.get("strategy_paper") or {}).get("extra") or []) if isinstance(b, dict)}
for _n in ("c4_candle_variations", "c4s_candle_variations_strict"):
    print("   %s etkin varyasyon (config): %d" % (_n, len(((_ex.get(_n) or {}).get("rule_params") or {}).get("variations") or [])))
print("   formasyon protokolü (config):", (raw.get("pattern_trader") or {}).get("protocol"))
print("   öğrenme modu (config): enabled =", (raw.get("learning_mode") or {}).get("enabled"), "· extra_entries =",
      (raw.get("learning_mode") or {}).get("extra_entries", "YOK (open)"), "· Box min_stop_pct =",
      (((raw.get("learning_mode") or {}).get("books") or {}).get("b1_box_fade") or {}).get("min_stop_pct"))
print("   ortak deneyim (config):", raw.get("shared_experience"))
PY
  say "karne (maliyet sonrası; öğrenme öncesi/sonrası ayrı; «kayda alınan ekstra»; karşı-olgusal NET; AYLIK HEDEF; salt okunur)"
  # (2026-10-04) karne BAŞTAN basılır: başlık, sütun başlığı ve defter başına hüküm satırları en üstte. Çıktı defter sayısıyla
  # sınırlı (~2 × defter + 30 satır; 8 defterde ~60); fc63481'in `tail -n 40`ı yeni sütun ve AYLIK HEDEF bölümüyle hüküm
  # tablosunu kesiyordu. Yalnız beklenmedik uzunlukta (> 160 satır) ortası atlanır: ilk 120 + son 30 satır kalır.
  karne_out="$(py scripts/bot_scorecard.py --state "$STATE" 2>&1)" && karne_rc=0 || karne_rc=$?
  printf '%s\n' "$karne_out" | awk -v n="$(printf '%s\n' "$karne_out" | wc -l)" \
      -v hint="tamamı: cd $APP && sudo -u $SVC_USER $VENV/bin/python scripts/bot_scorecard.py --state $STATE" \
      'n <= 160 || FNR <= 120 || FNR > n - 30 { print; next } FNR == 121 { print "   … " (n - 150) " satır atlandı (" hint ") …" }'
  (( karne_rc == 0 )) || echo "   karne okunamadı (çıkış $karne_rc)"
  say "son 30 dk uyarı/hata satırları (worker)"
  journalctl -u "$WORKER" --since "-30min" -p warning --no-pager 2>/dev/null | tail -n 25 || true
  exit 0
fi

[[ "$MODE" == "deploy" || "$MODE" == "--dry-run" ]] || die "bilinmeyen seçenek: $MODE (--dry-run | --detach | --check | seçeneksiz)"
tour4h_warn başlangıç

# ------------------------------------------------------------------ 1) kontroller (hiçbir şeye dokunmaz)
say "1/10 kod kaynağı"
if [[ -f "$BUNDLE_SRC" ]]; then
  SOURCE="bundle"
  [[ -n "$BUNDLE_SHA256" ]] || die "bu sürüm için bundle tanımlı değil: $BUNDLE_SRC dosyasını kaldırın (kod GitHub'dan alınır). HİÇBİR ŞEYE DOKUNULMADI"
  got="$(sha256sum "$BUNDLE_SRC" | awk '{print $1}')"
  [[ "$got" == "$BUNDLE_SHA256" ]] || die "bundle sha256 uyuşmuyor: $got (beklenen $BUNDLE_SHA256) — dosya bozuk/eksik indirilmiş"
  ok "bundle, sha256 $got"
else
  SOURCE="github"
  ok "GitHub ($REPO_URL), commit ${TIP}"
fi

say "2/10 mevcut kod"
PREV="$(gitc rev-parse HEAD)"
BRANCH_NOW="$(gitc symbolic-ref --quiet --short HEAD || echo detached)"
echo "   HEAD ${PREV:0:7} ($BRANCH_NOW)"
if [[ "$PREV" == "$TIP" ]]; then echo "   zaten hedefte ($T7). Durum için: sudo bash $0 --check"; exit 0; fi
dirty="$(gitc status --porcelain --untracked-files=no)"
if [[ -z "$dirty" ]]; then
  ok "izlenen dosyalar temiz"
elif cfg_known_edit; then
  CFG_LOCAL=1
  CFG_PRE_SHA="$(sha256sum < "$APP/config.yaml" | awk '{print $1}')"
  ok "izlenen dosyalarda YALNIZ bilinen yerel değişiklik: config.yaml'da history: altında TEK satır '${KS_EVC_LINE#  }'"
  echo "       (sahibin 2026-10-06 alt süreç kill switch'i; config.yaml sha256 ${CFG_PRE_SHA:0:16}…). Dağıtımda doğrulanmış yedekten"
  echo "       SONRA, worker durmuşken 'git checkout -- config.yaml' ile geri alınır (yeni kodda alt süreç zaten varsayılan KAPALI);"
  echo "       geri almada dağıtım öncesi baytlarıyla AYNEN yerine konur."
else
  die "izlenen dosyalarda yerel değişiklik var — YALNIZ config.yaml'da history: altındaki TEK satır '${KS_EVC_LINE#  }'
  (2026-10-06 kill switch'i) kabul edilir; bu değişiklik ondan farklı (HİÇBİR ŞEYE DOKUNULMADI):
$dirty
  Ayrıntı: sudo -u $SVC_USER git -C $APP diff   — bana iletin"
fi

say "3/10 hedef commit'in getirilmesi (salt ekleme; çalışma ağacı değişmez)"
if [[ "$SOURCE" == "bundle" ]]; then
  install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$BASE/deploy-bundles"
  BUNDLE="$BASE/deploy-bundles/$(basename "$BUNDLE_SRC")"
  install -o "$SVC_USER" -g "$SVC_USER" -m 0640 "$BUNDLE_SRC" "$BUNDLE"
  gitc bundle verify -q "$BUNDLE" || die "bundle bu kopyaya uygulanamıyor (gereken taban $(shorts "${PREREQ[@]}") yok). HEAD ${PREV:0:7}. HİÇBİR ŞEYE DOKUNULMADI"
  gitc fetch -q "$BUNDLE" "$BRANCH_REF:refs/deploy/$T7"
  [[ "$(gitc rev-parse "refs/deploy/$T7")" == "$TIP" ]] || die "bundle'daki dal ucu beklenen commit değil"
else
  # önce tam SHA ile (GitHub destekler); olmazsa dalı getirip commit'i onun içinde ara
  if ! as_svc env -C "$APP" HOME="$BASE" git fetch -q "$REPO_URL" "$TIP" 2>/dev/null; then
    as_svc env -C "$APP" HOME="$BASE" git fetch -q "$REPO_URL" "$BRANCH_REF" \
      || die "GitHub'a erişilemedi ($REPO_URL). HİÇBİR ŞEYE DOKUNULMADI — VPS'in GitHub erişimini kontrol edin"
  fi
  gitc cat-file -e "${TIP}^{commit}" 2>/dev/null || die "hedef commit $T7 getirilemedi. HİÇBİR ŞEYE DOKUNULMADI"
  gitc update-ref "refs/deploy/$T7" "$TIP"
fi
ok "hedef commit $T7 hazır ($SOURCE)"

say "4/10 ileri sarma mümkün mü + çalışan sürüm"
gitc merge-base --is-ancestor "$PREV" "$TIP" || die "mevcut HEAD ${PREV:0:7} hedefin atası değil (ayrışmış kod). HİÇBİR ŞEYE DOKUNULMADI; bana HEAD'i iletin"
echo "   ${PREV:0:7} → $T7: $(gitc rev-list --count "$PREV..$TIP") commit"
FOUND=""
for p in "${PREREQ[@]}"; do             # en yeni eşleşen kazanır (liste eskiden yeniye)
  if gitc merge-base --is-ancestor "$p" "$PREV" 2>/dev/null; then FOUND="$p"; fi
done
[[ -n "$FOUND" ]] || die "çalışan sürüm ${PREV:0:7}, beklenenlerden ($(shorts "${PREREQ[@]}")) hiçbiri ya da soyu değil (daha ESKİ kod).
  Önce o sürümlerin betiklerini sırayla çalıştırın ya da bana HEAD'i iletin. HİÇBİR ŞEYE DOKUNULMADI"
if [[ "$PREV" == "$FOUND" ]]; then
  ok "çalışan sürüm ${FOUND:0:7} (kabul edilenler: $(shorts "${PREREQ[@]}"))"
else
  ok "çalışan sürüm ${PREV:0:7}: ${FOUND:0:7} soyundan ve hedefin atası (kabul edilenler: $(shorts "${PREREQ[@]}"))"
fi
for p in "${PREREQ[@]}"; do
  if [[ "$p" != "$FOUND" ]] && gitc merge-base --is-ancestor "$FOUND" "$p" 2>/dev/null \
     && ! gitc merge-base --is-ancestor "$p" "$PREV" 2>/dev/null; then
    echo "   ${p:0:7} sürümünün değişiklikleri de bu dağıtıma DAHİL (o sürümün betiğini ayrıca çalıştırmayın)"
  fi
done
if gitc diff --quiet "$PREV" "$TIP" -- requirements.txt; then REQ_CHANGED=""; else REQ_CHANGED=1; fi
echo "   requirements.txt: $([[ -n "$REQ_CHANGED" ]] && echo "DEĞİŞTİ (pip install yapılacak)" || echo "değişmedi (pip gerekmez)")"
# (2026-09-29) geri dönülecek kod öğrenme modunu tanıyorsa Formasyon öğrenme planları geri almada temizlenmez
if gitc cat-file -e "$PREV:tradingbot/learning_mode.py" 2>/dev/null; then PREP_NEEDED=""; else PREP_NEEDED=1; fi
ok "hızlı ileri sarma mümkün"
# (2026-09-30) GÖLGE DANIŞMAN MÜHRÜ: mühür commit'i hedefin atası ve mühürlü kod dosyaları mühürden hedefe kadar DEĞİŞMEMİŞ
# (yalnız git nesneleri; çalışma ağacına dokunulmaz). Mühür değerlerinin kendisi 6/10'da yeni kodla denetlenir.
gitc cat-file -e "${ADV_SEAL_COMMIT}^{commit}" 2>/dev/null \
  || die "danışman mühür commit'i ${ADV_SEAL_COMMIT:0:7} getirilen geçmişte yok. HİÇBİR ŞEYE DOKUNULMADI"
gitc merge-base --is-ancestor "$ADV_SEAL_COMMIT" "$TIP" \
  || die "danışman mühür commit'i ${ADV_SEAL_COMMIT:0:7} hedefin ($T7) atası değil. HİÇBİR ŞEYE DOKUNULMADI"
sealed_diff="$(gitc diff --name-only "$ADV_SEAL_COMMIT" "$TIP" -- "${ADV_SEALED_FILES[@]}")" \
  || die "mühürlü danışman dosyaları karşılaştırılamadı (git diff). HİÇBİR ŞEYE DOKUNULMADI"
[[ -z "$sealed_diff" ]] || die "mühürlü danışman dosyaları ${ADV_SEAL_COMMIT:0:7}..$T7 arasında DEĞİŞMİŞ (bu advisor_v2 demektir:
  yeni SHA + yeni ön kayıt gerekir; DANISMAN_V1.md §5):
$sealed_diff
  HİÇBİR ŞEYE DOKUNULMADI"
hook_diff="$(gitc diff --name-only "$ADV_SEAL_COMMIT" "$TIP" -- "${ADV_HOOK_FILES[@]}")" \
  || die "danışman kanca dosyaları karşılaştırılamadı (git diff). HİÇBİR ŞEYE DOKUNULMADI"
hook_left=""
for hf in $hook_diff; do
  hb="$(gitc rev-parse "$TIP:$hf" 2>/dev/null || true)"
  [[ " ${ADV_HOOK_APPROVED[*]} " == *" $hf=$hb "* ]] || hook_left+="$hf"$'\n'
done
hook_diff="${hook_left%$'\n'}"
[[ -z "$hook_diff" ]] || die "danışman kanca dosyaları ${ADV_SEAL_COMMIT:0:7}..$T7 arasında DEĞİŞMİŞ (altın test — ana depo OFF ↔
  RECORD bayt bayt aynı — mühürdeki koda karşı koşuldu; yeni kodla yeniden koşulmadan dağıtılmaz):
$hook_diff
  HİÇBİR ŞEYE DOKUNULMADI"
ok "danışman mührü: ${ADV_SEAL_COMMIT:0:7} hedefin atası; mühürlü ${#ADV_SEALED_FILES[@]} kod dosyası ve ${#ADV_HOOK_FILES[@]} kanca dosyası (collector, store) ${ADV_SEAL_COMMIT:0:7}..$T7 arasında değişmedi ya da yalnız ONAYLI içerikte (collector.py üç karar-nötr satır, blob ${ADV_HOOK_APPROVED[0]##*=})"

say "5/10 disk, servisler, bellek sınırı, ortam (öğrenme / ortak deneyim / danışman), mod, ortak deneyim klasörü"
free_kb="$(df -Pk "$DATA" | awk 'NR==2{print $4}')"
[[ "$free_kb" -gt 2097152 ]] || die "diskte 2 GB'tan az boş yer var ($((free_kb/1024)) MB) — yedek için yetersiz"
ok "boş disk $((free_kb/1024)) MB"
for u in "$WORKER" "$DASH"; do echo "   $u: $(systemctl is-active "$u" || true)"; done
memory_report
memmax_check gate
lm_env_scan || die "TRADINGBOT_LEARNING_MODE servis ortamında tanımlı: $lm_env_hint. HİÇBİR ŞEYE DOKUNULMADI"
xp_env_scan || die "TRADINGBOT_SHARED_EXPERIENCE geçersiz değerle tanımlı: $xp_env_hint. HİÇBİR ŞEYE DOKUNULMADI"
adv_env_scan || die "TRADINGBOT_SHARED_EXPERIENCE_ADVISOR geçersiz değerle tanımlı: $adv_env_hint. HİÇBİR ŞEYE DOKUNULMADI"
mode_check || die "state/mode.json PAPER değil ya da okunamıyor — öğrenme modu yalnız PAPER'da açılır. HİÇBİR ŞEYE DOKUNULMADI"
xp_dir_check check || die "state/shared_experience servis kullanıcısı için açılamaz/yazılamaz. HİÇBİR ŞEYE DOKUNULMADI"
engine_plan
say "defterler (şimdi)"
book_snapshot

# ------------------------------------------------------------------ 2) yeni kodun sınanması + değişiklik
if [[ "$MODE" == "deploy" ]]; then
  mkdir -p "$LOGDIR"
  # SSH bağlantısı koparsa (HUP) dağıtım yarıda KALMAZ: sona kadar sürer, çıktı ayrıca log dosyasına yazılır.
  trap '' HUP
  # tee de INT/TERM/HUP'u yok sayar: Ctrl+C önce tee'yi öldürürse betiğin sonraki yazımı SIGPIPE ile onu da öldürür ve
  # geri alma YARIM kalır (sahte VPS'te ölçüldü).
  exec > >(trap '' INT TERM HUP; exec tee -a "$LOGDIR/$T7-deploy.log") 2>&1
  echo "   (çıktı ayrıca: $LOGDIR/$T7-deploy.log)"
  if [[ -n "${W4H_AT:-}" ]]; then        # başlangıç uyarısı bu dosya açılmadan ÖNCE basıldı: kaydı
    echo "   !!! (başlangıç uyarısının kaydı) betik UTC $W4H_AT'de başladı: 4h mum kapanışından sonraki ilk 40 dk — ilk"
    echo "   !!! tur (son dağıtımda 62,7 dk) 4h indeks yayımıyla çakışıp daha da uzayabilir (--check ilk turu ayrı raporlar:"
    echo "   !!! UYARI > 60 dk; durdurmaz; worker yeniden başlatılmadan hemen önce saat yeniden denetlenir)."
  fi
  ( book_snapshot ) > "$LOGDIR/$T7-before.txt" 2>&1 || true    # alt kabuk: tuzak çıktısı dosyaya gitmesin
  echo "$PREV" > "$LOGDIR/$T7-prev-commit.txt"
  if [[ -n "$CFG_LOCAL" ]]; then                # (2026-10-06) geri almada AYNEN yerine konacak baytlar
    install -m 0644 "$APP/config.yaml" "$CFG_SAVE"
    [[ "$(sha256sum < "$CFG_SAVE" | awk '{print $1}')" == "$CFG_PRE_SHA" ]] \
      || die "dağıtım öncesi config.yaml kopyası doğrulanamadı ($CFG_SAVE). HİÇBİR ŞEYE DOKUNULMADI"
    echo "   dağıtım öncesi config.yaml (bilinen kill switch satırıyla) saklandı: $CFG_SAVE (sha256 ${CFG_PRE_SHA:0:16}…)"
  fi
fi

# Ctrl+C / systemctl stop: kod henüz değişmediyse yalnız çıkar (worker durdurulduysa eski kodla yeniden başlatır);
# değiştiyse geri alır; servis yeni kodla başladıysa bildirir.
MERGED=""; RESTARTED=""; STOPPED=""
WT="$BASE/deploy-wt-$T7"
cleanup_wt() { gitc worktree remove --force "$WT" >/dev/null 2>&1 || true; }
on_abort() {
  trap '' INT TERM
  if [[ -n "$RESTARTED" ]]; then
    echo "DUR: durduruldu — servisler yeni kodla başlatılmıştı; durum için: sudo bash $0 --check" >&2 || true
  elif [[ -n "$MERGED" ]]; then
    revert_code
    echo "DUR: durduruldu → kod ${PREV:0:7}'e geri alındı; worker eski kodla yeniden başlatıldı" >&2 || true
  elif [[ -n "$STOPPED" ]]; then
    systemctl start "$WORKER" || true
    echo "DUR: durduruldu — kod DEĞİŞMEDİ; durdurulan worker eski kodla yeniden başlatıldı" >&2 || true
  else
    cleanup_wt
    echo "DUR: durduruldu — kod DEĞİŞMEDİ, servisler yeniden başlatılmadı" >&2 || true
  fi
  exit 130
}
trap on_abort INT TERM
trap 'cleanup_wt' ERR

say "6/10 yeni kodun sınanması — AYRI çalışma kopyasında, servis ortamıyla (çalışan bot ve kodu DEĞİŞMEZ)"
# Kod, ancak worker boşa çıktığı ve DURDURULDUĞU anda değiştirilir. Önceki bir sürümde kod beklemeden ÖNCE değişiyordu:
# bekleme sırasında worker kendiliğinden yeniden başlarsa (OOM) yeni kodla açılıyor, betik sonra diski geri alsa da
# bellekte yeni kod çalışmaya devam ediyordu (2026-09-25/26 VPS'te oldu).
cleanup_wt
[[ -e "$WT" ]] && rm -rf -- "${WT:?}"
gitc worktree add -q --detach "$WT" "$TIP"
if ! svc_run "$WT" "$VENV/bin/python" -m tradingbot preflight --quick; then
  cleanup_wt; die "preflight (yeni kod) başarısız → kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
fi
echo "   (56 değişmez; #46-#49, #52 ve #54 deponun kendi testlerini / koşucularını / 8db1faf ağacını AYRI süreçte koşar — toplam 4-8 dk sürebilir (#52 nice 19); geçici farkta #47 bir kez yeniden koşar)"
# Değişmezler yeni kodun KENDİ yükleyicisiyle (`load_config`: yinelenen anahtar reddi + servis ortamı, ör.
# TRADINGBOT_LEARNING_MODE / TRADINGBOT_SHARED_EXPERIENCE) okunur; yükleme hatası (ConfigError) da düşme sayılır.
if ! svc_run "$WT" "$VENV/bin/python" - "$STATE" <<'PY'
import json
import os
import sys
from pathlib import Path

st = Path(sys.argv[1])
try:
    from tradingbot.config import load_config, load_yaml_strict
    cfg = load_config(Path("config.yaml"))
    raw = load_yaml_strict(Path("config.yaml").read_text(encoding="utf-8"))
except Exception as exc:  # noqa: BLE001 — ConfigError dahil (ör. geçersiz TRADINGBOT_LEARNING_MODE) → fail-closed
    print("   HATA  config yeni kodla ve servis ortamıyla yüklenemedi: %s: %s" % (type(exc).__name__, exc))
    sys.exit(1)
v3 = cfg.v3
from tradingbot import candle_variations as V
from tradingbot import learning_mode as LMM
from tradingbot.learn.research_coordinator import mode_gate
from tradingbot.risk.modes import ModeState
from tradingbot.risk.profiles import PROFILES
from tradingbot.strategy_paper import book_specs

books = {b.name: b for b in book_specs(v3)}
C4_IDS = ["CV001_BREAKOUT20_TREND_VOL_L", "CV002_BREAKOUT20_TREND_VOL_S", "CV003_PULLBACK_ENGULF_L", "CV004_PULLBACK_ENGULF_S",
          "CV005_SUPPORT_HARAMI_L", "CV006_RESIST_HARAMI_S", "CV007_SWEEP_REJECT_ENGULF_L", "CV008_SWEEP_REJECT_ENGULF_S"]
# docs/ogrenme_modu/CONTRACT.md "config.yaml values for L1" (+ 6fb39cd: ana bot leverage_max 5) — birebir
# (2026-10-03, sahip kararı) + öğrenme-ekstra kipi record_selectivity (kod varsayılanı open; kill switch: open)
LM_SCALARS = {"enabled": True, "risk_per_trade_pct": 0.5, "max_total_open_risk_pct": 100, "margin_reserve_pct": 5,
              "liq_buffer_mult": 2.0, "min_notional_bump": True, "counterfactual": True, "counterfactual_max_pending": 2000,
              "extra_entries": "record_selectivity"}
LM_BOOKS = {
    "main": {"enabled": True, "slots": 20, "leverage_max": 5},
    "t2_trend_regime": {"enabled": True, "slots": 40, "leverage_max": 4},
    "m2_tsmom28": {"enabled": True, "slots": 40, "leverage_max": 4},
    "b1_box_fade": {"enabled": True, "slots": 40, "leverage_max": 4, "min_stop_pct": 0.5},     # 2026-10-03: 0,32 → 0,5
    "d4_donchian_20_10": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
    "c4_candle_variations": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
    "c4s_candle_variations_strict": {"enabled": False},
    "pattern_trader": {"enabled": True, "slots": 30, "leverage_max": 3},
}
LM_OVR = {"regime_gate_shadow": True, "candle_veto_shadow": True,
          "structures_entry_shadow": ["main", "t2_trend_regime", "m2_tsmom28"],
          "economics_exploration": True, "leverage_confidence_fallback": True}
lmraw = raw.get("learning_mode") if isinstance(raw.get("learning_mode"), dict) else {}
lm = v3.learning_mode


def same(a, b):
    """Birebir eşitlik; bool yalnız bool'a eşit (1 == True sayılmaz), sayılar değerce."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def lm_scalars():
    typed = {k: getattr(lm, k) for k in LM_SCALARS}
    rawv = {k: lmraw.get(k) for k in LM_SCALARS}
    return same(typed, LM_SCALARS) and same(rawv, LM_SCALARS) \
        and set(lmraw) == set(LM_SCALARS) | {"books", "strategy_overrides"}


def lm_books():
    exp_norm = {n: LMM.BookLearningCfg(**c).to_dict() for n, c in LM_BOOKS.items()}
    return same(lmraw.get("books"), LM_BOOKS) and same({n: b.to_dict() for n, b in lm.books.items()}, exp_norm)


def lm_runtime():
    """Yeni kodun `LearningMode`'u config'i defterlere birebir dağıtıyor mu (kapı açık varsayımıyla)."""
    m = LMM.LearningMode.from_config(v3, lambda: (True, "OK"))
    m.refresh()
    for n in LMM.BOOK_NAMES:
        c, b = LM_BOOKS.get(n) or {}, m.book(n)
        if not c.get("enabled"):
            if b is not None:
                return False
            continue
        if b is None or (b.slots, b.leverage_max, b.risk_pct, b.reserve_pct, b.liq_buffer_mult, b.hard_cap_pct,
                         b.min_stop_pct, b.symbols) != (c["slots"], c["leverage_max"], 0.5, 5.0, 2.0, 2.0,
                                                        c.get("min_stop_pct"), c.get("symbols")):
            return False
        if b.extra_entries != "record_selectivity" or b.to_dict().get("extra_entries") != "record_selectivity":
            return False                    # (2026-10-03) kip her defter görünümüne ulaşır
    return (m.structures_entry_shadow("main") and m.structures_entry_shadow("m2_tsmom28")
            and not m.structures_entry_shadow("b1_box_fade") and m.override("economics_exploration", False) is True
            and m.extra_entries == "record_selectivity" and m.status().get("extra_entries") == "record_selectivity")


def mode_file_ok():
    p = st / "mode.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        if not (isinstance(d, dict) and d.get("mode") == "PAPER" and d.get("live_order_path_enabled") is not True):
            return False
    ms = ModeState(p)
    return mode_gate(ms.mode.value, v3.execution.gateway, ms.is_live_order_path_enabled()) == (True, "OK")


# --- ORTAK DENEYİM KATMANI v1 + NET ETİKET (2026-09-29): docs/ortak_deneyim/SPEC_V1.md §14/§18, KARARLAR.md
# (2026-09-30) + GÖLGE DANIŞMAN v1: bölüm BİREBİR {enabled: true, mode: RECORD, advisor_mode: RECORD}; danışmanın işletim
# ayarları (mühürde değil; DANISMAN_V1.md §5) kod varsayılanında kalır — config.yaml'da başka advisor_* / advice_* YOK.
XP_RAW = {"enabled": True, "mode": "RECORD", "advisor_mode": "RECORD"}
ADV_DEFAULTS = {"advisor_budget_ms": 250, "advisor_catch_up_budget_ms": 1000, "advisor_rebuild_rows_per_step": 5000,
                "advisor_snapshot_every_steps": 60, "advisor_max_index_mb": 96, "advice_hot_max_lines": 2000,
                "advice_max_total_mb": 256}
XP_DEFAULTS = dict({"state_dir": "shared_experience", "hot_max_lines": 5000, "archive_max_segments": 0, "max_total_mb": 1024,
                    "tour_budget_s": 2.0, "max_rows_per_tour": 600, "backfill": True, "cache_entries": 2048,
                    "pending_max_age_h": 48.0, "lock_timeout_s": 0.2, "lazy_fetch_max_per_tour": 0}, **ADV_DEFAULTS)
XP_ENV = os.environ.get("TRADINGBOT_SHARED_EXPERIENCE", "").strip().lower()
ADV_ENV = os.environ.get("TRADINGBOT_SHARED_EXPERIENCE_ADVISOR", "").strip().lower()
ENV_OFFS = ("off", "false", "0", "disabled")
ADV_SEAL = {"ADVISOR_SHA": "8a89fd7e69a2d33b", "WF_SHA": "b34d6b7a313d24d1", "situation_v1 SCHEMA_SHA": "640fd10e5d6f727c"}
XP_IMPORTERS = {"tradingbot/engine_v3.py", "tradingbot/cli_v3.py"}
# (2026-10-02) scripts/ altında paketi içe aktarabilecek TEK dosya: panel ekran görüntüleri için SENTETİK demo durumu yazan
# çevrimdışı betik (0d1e365; gerçek rapor kodunu sentetik satırlarla koşar). İstisna dar: çalışma zamanının hiçbir yeri
# (tradingbot/, deploy/ — sürüm betikleri hariç —, diğer scripts/) onu anmaz ve işaret dosyası olmayan dolu bir state
# klasörüne YAZMAYI REDDEDER (değişmez #33 ikisini de dağıtılan ağaçta sınar).
XP_DEMO = "scripts/demo_panel_state.py"
xp = getattr(v3, "shared_experience", None)      # bölümü tanımayan kod: her ortak deneyim değişmezi ayrı ayrı düşer


def xp_active():
    """Katman ETKİN (enabled + RECORD). Servis ortamında TRADINGBOT_SHARED_EXPERIENCE=off/false/0/disabled ise operatör
    kapatmasıdır (dağıtım öncesi yüksek sesle basıldı): o zaman yeni kod katmanı gerçekten KAPATIYOR olmalı."""
    if XP_ENV:
        return XP_ENV in ("off", "false", "0", "disabled") and xp.enabled is False and xp.mode == "OFF" \
            and xp.active is False
    return xp.enabled is True and xp.mode == "RECORD" and xp.active is True


def xp_section():
    """config.yaml bölümü BİREBİR {enabled: true, mode: RECORD, advisor_mode: RECORD}; geri kalan her alan (danışmanın
    işletim ayarları dahil) kod varsayılanında. Env yalnız kapatır: katman env'i → enabled false / mode OFF; danışman env'i
    → advisor_mode OFF."""
    from dataclasses import fields
    typed = {f.name: getattr(xp, f.name) for f in fields(xp)}
    exp = dict(XP_DEFAULTS, **XP_RAW)
    if XP_ENV:
        exp.update(enabled=False, mode="OFF")
    if ADV_ENV:
        exp.update(advisor_mode="OFF")
    return same(raw.get("shared_experience"), XP_RAW) and same(typed, exp)


# --- GÖLGE DANIŞMAN v1 (2026-09-30): docs/ortak_deneyim/DANISMAN_V1.md §1/§5/§7, RELEASE_NOTES (danışman düzeltme turu)
def _spec_sha(spec):
    import hashlib
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                          .encode("ascii")).hexdigest()[:16]


def adv_seals():
    """Mühürler spec'lerden YENİDEN hesaplanır ve beklenen değerlere eşittir; kimlik sabitleri v1; etki NONE."""
    from tradingbot.shared_experience import advice_store as AS
    from tradingbot.shared_experience import advisor as A
    from tradingbot.shared_experience import advisor_eval as AE
    from tradingbot.shared_experience import advisor_live as AL
    from tradingbot.shared_experience import situation as XS
    got = {"ADVISOR_SHA": A.ADVISOR_SHA, "WF_SHA": AE.WF_SHA, "situation_v1 SCHEMA_SHA": XS.SCHEMA_SHA}
    okk = (got == ADV_SEAL and _spec_sha(A.ADVISOR_SPEC) == A.ADVISOR_SHA and _spec_sha(AE.WF_SPEC) == AE.WF_SHA
           and A.ADVISOR_SPEC.get("situation_schema_sha") == XS.SCHEMA_SHA and XS.SCHEMA_ID == "situation_v1"
           and (A.ADVISOR_ID, A.ADVICE_SCHEMA, A.ADVICE_KIND, A.EFFECT_NONE)
           == ("advisor_v1", "shared_experience_advice_v1", "xp_advice", "NONE")
           and tuple(A.LABELS) == ("GIR", "NOTR", "GIRME", "VERI_AZ") and AE.ADVISOR_SHA == A.ADVISOR_SHA
           and AL.ADVISOR_SHA == A.ADVISOR_SHA and AS.ADVICE_DIR == "advice")
    if not okk:
        print("         mühürler: %s (beklenen %s)" % (got, ADV_SEAL))
    return okk


def adv_doc_seal():
    """DANISMAN_V1.md §7 mühür tablosu (ADVISOR_SHA / WF_SHA / situation_v1 SCHEMA_SHA) koddaki ve beklenen değerlerle AYNI;
    her anahtar için TAM BİR satır (çelişen ikinci satır — ör. eski bir SHA — düşürür)."""
    import re
    from tradingbot.shared_experience import advisor as A
    from tradingbot.shared_experience import advisor_eval as AE
    from tradingbot.shared_experience import situation as XS
    txt = Path("docs/ortak_deneyim/DANISMAN_V1.md").read_text(encoding="utf-8")
    m = re.search(r"^## 7\..*?$(.*?)(?=^## |\Z)", txt, re.S | re.M)
    pairs = re.findall(r"^\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{16})`\s*\|", m.group(1), re.M) if m else []
    rows = dict(pairs)
    code = {"ADVISOR_SHA": A.ADVISOR_SHA, "WF_SHA": AE.WF_SHA, "situation_v1 SCHEMA_SHA": XS.SCHEMA_SHA}
    okk = len(pairs) == len(rows) == len(ADV_SEAL) and rows == code == ADV_SEAL
    if not okk:
        print("         §7: %s · kod: %s" % (pairs, code))
    return okk


def worker_collector(v3x, st):
    """Toplayıcıyı WORKER'IN kurduğu yoldan kurar: SharedExperienceCollector.from_engine (→ XpSettings.from_section),
    geçici state klasöründe (gerçek state'e dokunmaz)."""
    import types
    from tradingbot.shared_experience.collector import SharedExperienceCollector
    eng = types.SimpleNamespace(cfg=types.SimpleNamespace(v3=v3x, state_path=Path(st), cache_path=None,
                                                          code_sha="tb-deploy-probe"))
    return SharedExperienceCollector.from_engine(eng)


def adv_active():
    """Danışman ETKİN (katman RECORD + advisor_mode RECORD) ve worker'ın kurulum yolu (from_engine → XpSettings.from_section)
    gerçekten LiveAdvisor kuruyor. Servis ortamında katman ya da danışman env'i off ise operatör kapatmasıdır (dağıtım
    öncesi yüksek sesle basıldı): o zaman yeni kod danışmanı gerçekten KAPATIYOR olmalı (danışman env'i → worker yolu
    danışman KURMAZ; katman env'i → motor toplayıcıyı hiç kurmaz)."""
    import tempfile
    from tradingbot.shared_experience import advisor_live as AL

    def built():
        with tempfile.TemporaryDirectory(prefix="tb-deploy-advw-") as d:
            c = worker_collector(v3, d)
            return c._advisor_on, c._advisor
    if XP_ENV or ADV_ENV:
        okk = all(e in ("",) + ENV_OFFS for e in (XP_ENV, ADV_ENV)) and xp.advisor_active is False \
            and (xp.advisor_mode == "OFF" or not ADV_ENV)
        if okk and ADV_ENV and not XP_ENV:
            on, adv = built()
            okk = on is False and adv is None
        return okk
    on, adv = built()
    return xp.advisor_mode == "RECORD" and xp.advisor_active is True and on is True and isinstance(adv, AL.LiveAdvisor)


def adv_modes():
    """Yeni yükleyici: advisor_mode yalnız OFF | RECORD (büyük harfe normalize; YAML false → OFF); ADVISE / ENFORCE / başka
    → ConfigError. Env TRADINGBOT_SHARED_EXPERIENCE_ADVISOR yalnız KAPATIR: off → OFF, on/record → ConfigError."""
    import copy
    from tradingbot.config_v3 import load_v3
    from tradingbot.core import ConfigError
    import logging
    keep = {k: os.environ.pop(k) for k in ("TRADINGBOT_SHARED_EXPERIENCE", "TRADINGBOT_SHARED_EXPERIENCE_ADVISOR")
            if k in os.environ}
    lg = logging.getLogger("tradingbot.config_v3")
    lvl = lg.level
    lg.setLevel(logging.ERROR)                      # env-kapatma uyarısı bu sınamada gürültüdür
    try:
        def load(mode, env=None):
            r = copy.deepcopy(raw)
            # katman açık varsayılır: yalnız danışman modunun ve env'inin yükleyicideki davranışı sınanır
            r["shared_experience"] = dict(r.get("shared_experience") or {}, enabled=True, mode="RECORD", advisor_mode=mode)
            if env is None:
                os.environ.pop("TRADINGBOT_SHARED_EXPERIENCE_ADVISOR", None)
            else:
                os.environ["TRADINGBOT_SHARED_EXPERIENCE_ADVISOR"] = env
            try:
                return load_v3(r).shared_experience
            finally:
                os.environ.pop("TRADINGBOT_SHARED_EXPERIENCE_ADVISOR", None)

        def refused(mode, env=None):
            try:
                load(mode, env)
            except ConfigError:
                return True
            return False
        a, b, c, d = load("record"), load("OFF"), load(False), load("RECORD", "off")
        return (all(refused(m) for m in ("ADVISE", "ENFORCE", "LIVE", "ON", "")) and refused("RECORD", "on")
                and refused("RECORD", "record") and (a.advisor_mode, a.advisor_active) == ("RECORD", True)
                and (b.advisor_mode, b.advisor_active) == ("OFF", False) and c.advisor_mode == "OFF"
                and (d.advisor_mode, d.advisor_active) == ("OFF", False))
    finally:
        os.environ.update(keep)
        lg.setLevel(lvl)


def adv_keys():
    """config.yaml'ın HİÇBİR yerinde shared_experience.advisor_mode dışında advisor_* / advice_* anahtarı yok; danışmanın
    işletim ayarları (yeni kod) varsayılanında."""
    bad_keys = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                pth = path + (str(k),)
                if str(k).lower().startswith(("advisor", "advice")) and pth != ("shared_experience", "advisor_mode"):
                    bad_keys.append(".".join(pth))
                walk(v, pth)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + (str(i),))
    walk(raw, ())
    typed = {k: getattr(xp, k) for k in ADV_DEFAULTS}
    okk = not bad_keys and same(typed, ADV_DEFAULTS)
    if not okk:
        print("         fazla anahtarlar: %s · ayarlar: %s" % (bad_keys, typed))
    return okk


ADV_DECISION_TOKENS = r"advisor(?!y)|advice"
ADV_FILE_TOKENS = (r"advisor_live|advisor_eval|advice_store|AdvisorFold|LiveAdvisor|AdviceStore|ADVICE_SCHEMA|xp_advice"
                   r"|shared_experience_advice|advice/|advisor_state|advisor_meta|advisor_status|walkforward_summary")
ADV_ALLOWED = {"tradingbot/cli_v3.py", "tradingbot/config_v3.py", "tradingbot/ops/backup.py"}
# (2026-09-30) paketin geri kalanında gölge danışmanla İLGİSİZ, bilinen "advice/advisor" ifadeleri (LLM tavsiyesi, risk
# "advisories"/AdviceContext, grafik alt yazısı). Tarama (büyük/küçük harf duyarsız) bu SABİT ifadeleri silip kalan metinde
# belirteç arar; yeni her anma düşürür.
ADV_LEGIT = {"tradingbot/charts.py": ("not financial advice",),
             "tradingbot/coinhead/head.py": ("llm_advice",),
             "tradingbot/llm/__init__.py": ("advice = svc.advise(", "`advice.veto`", "LLMAdvice"),
             "tradingbot/llm/service.py": ("_finish_advice", "_advice_payload", "LLMAdvice"),
             "tradingbot/llm/schema.py": ("LLMResearchAdvice", "LLMAdvice"),
             "tradingbot/llm/provider.py": ("NOOP_ADVICE_JSON",),
             "tradingbot/quant/risk_v2.py": ("advisories", "AdviceContext")}


def adv_ast():
    """tradingbot/ paketinin danışman paketi (shared_experience/), panel, cli_v3, config_v3 ve ops/backup DIŞINDAKİ HİÇBİR
    modülü (motor, öğrenme, strategy_paper, paper_rules, mum/Box/Donchian/EMA kuralları, signal_lab, pattern_trader, risk,
    accounting, learn, replay, …) danışmana/tavsiyeye ATIF YAPMAZ (ADV_LEGIT'teki bilinen ilgisiz ifadeler hariç; motorun
    _prepared_experience_pool'u ayrıca); paket dışında yalnız cli_v3, config_v3, ops/backup ve panel danışman dosya/şema
    adlarını anar; config_v3, ops/backup ve panel paketi İÇE AKTARMAZ (AST). tests/…advisor_no_decision_change_v1.py T-A8
    korumasının (karar modülleri listesi) dağıtılan ağaçta GENİŞLETİLMİŞ eşi."""
    import ast
    import re
    tb = Path("tradingbot")
    pkg = tb / "shared_experience"
    dec, found = [], []
    for pth in sorted(tb.rglob("*.py")):
        rel = pth.as_posix()
        if pkg in pth.parents or rel in ADV_ALLOWED or rel.startswith("tradingbot/dashboard/"):
            continue
        dec.append(rel)
        txt = pth.read_text(encoding="utf-8")
        for phrase in ADV_LEGIT.get(rel, ()):
            txt = txt.replace(phrase, "")
        m = re.search(ADV_DECISION_TOKENS, txt, re.I)
        if m:
            found.append("%s: %r" % (rel, m.group(0)))
    # T-A8'in karar modülleri taramanın içinde olmalı (ağaç düzeni değiştiyse tarama boşa düşmesin)
    t_a8 = ["tradingbot/engine_v3.py", "tradingbot/learning_mode.py", "tradingbot/learning_basis.py",
            "tradingbot/learning_cf.py", "tradingbot/strategy_paper.py", "tradingbot/box_timer.py",
            "tradingbot/paper_rules.py", "tradingbot/signal_lab.py"]
    missing = [r for r in t_a8 if r not in dec] + [s for s in ("learn", "pattern_trader", "replay", "risk", "accounting")
                                                    if not any(r.startswith("tradingbot/%s/" % s) for r in dec)]
    found += ["%s: taranmadı (yok)" % r for r in missing]
    eng = (tb / "engine_v3.py").read_text(encoding="utf-8")
    i = eng.index("def _prepared_experience_pool")
    if "advis" in eng[i:i + 6000].lower().replace("advisory", ""):
        found.append("engine_v3._prepared_experience_pool")
    for pth in sorted(tb.rglob("*.py")):
        rel = pth.as_posix()
        if pkg in pth.parents or rel in ADV_ALLOWED or rel.startswith("tradingbot/dashboard/"):
            continue
        if re.search(ADV_FILE_TOKENS, pth.read_text(encoding="utf-8")):
            found.append("%s: danışman dosya/şema adı" % rel)
    for rel in ["tradingbot/config_v3.py", "tradingbot/ops/backup.py"] + [q.as_posix() for q in
                                                                        sorted((tb / "dashboard").glob("*.py"))]:
        for n in ast.walk(ast.parse(Path(rel).read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and "shared_experience" in (n.module or ""):
                found.append("%s: from %s import" % (rel, n.module))
            if isinstance(n, ast.Import) and any("shared_experience" in a.name for a in n.names):
                found.append("%s: import shared_experience" % rel)
    if found:
        print("         atıflar: %s" % found[:8])
    return not found and len(dec) > 100


def adv_record_only():
    """Danışman YALNIZ advice/ altına yazar — iki sınama, sentetik ortak deneyim deposunda (tests/advisor_synth, üretim satır
    sözleşmesi), geçici klasörde:
    (a) TÜMLEŞİK: toplayıcı worker'ın yolundan (from_engine) advisor_mode OFF ve RECORD ile kurulur; aynı toplu yazımlar
        toplayıcının GERÇEK _flush'ından (ana depo yazımı + danışman kancası + döngü + imleç) geçer; her yazımdan sonra
        advice/ DIŞINDAKİ her dosya (experience.jsonl, arşiv, cursor.json) OFF ile RECORD'da BAYT BAYT aynı; dönüşler,
        sayaçlar ve ayarlar (advisor_mode dışında) aynı; OFF → danışman kurulmaz, advice/ açılmaz; RECORD'da danışman
        yetişip canlıya geçer, her tavsiye etki NONE, durum OK, hata 0, yazılan = üretilen > 0.
    (b) DOĞRUDAN: hazır depoda gerçek LiveAdvisor yetişir ve canlı katlar; her adımdan sonra advice/ dışı baytlar (mevcut
        imleç dahil) aynı.
    (Altın testin — motorla OFF ↔ RECORD — dağıtım anındaki yerine; pytest VPS'te yok.)"""
    import dataclasses
    import hashlib
    import sys as _sys
    import tempfile
    import time as _time
    from datetime import datetime as _dt
    _sys.path.insert(0, str(Path("tests").resolve()))
    import advisor_synth as X
    from tradingbot.shared_experience import advisor_live as AL
    from tradingbot.shared_experience.collector import XpSettings, _StepCtx
    from tradingbot.shared_experience.store import ExperienceStore

    def digest(root):
        out = {}
        for q in sorted(root.rglob("*")):
            rel = q.relative_to(root).as_posix()
            if q.is_file() and not rel.startswith("advice/"):
                out[rel] = hashlib.sha256(q.read_bytes()).hexdigest()
        return out

    def with_adv(mode):
        return dataclasses.replace(v3, shared_experience=dataclasses.replace(xp, enabled=True, mode="RECORD",
                                                                            advisor_mode=mode))
    rows = X.random_store(3)
    bs = X.batches(rows)
    half = len(bs) // 2
    with tempfile.TemporaryDirectory(prefix="tb-deploy-adv-") as d:
        # (a) tümleşik: toplayıcının gerçek _flush'ı + kancası, OFF ↔ RECORD
        off = worker_collector(with_adv("OFF"), Path(d) / "s_off")
        on = worker_collector(with_adv("RECORD"), Path(d) / "s_on")
        s_off, s_on = dataclasses.asdict(off.s), dataclasses.asdict(on.s)
        okk = (off._advisor is None and isinstance(on._advisor, AL.LiveAdvisor) and s_off.pop("advisor_mode") == "OFF"
               and s_on.pop("advisor_mode") == "RECORD" and s_off == s_on)
        n_rows = 0
        for b in bs:
            now = _dt.fromisoformat(b[0]["recorded_at"])
            res = []
            for col in (off, on):
                ctx = _StepCtx(col, env={"recorded_at": b[0]["recorded_at"]}, now=now, now_ms=int(now.timestamp() * 1000),
                               born=0, bf_on=False, deadline=_time.perf_counter() + 600, cap=10 ** 6)
                ctx.out = [(r, (lambda: None)) for r in b]
                res.append(col._flush(ctx))
            n_rows += int(res[0].get("rows") or 0)
            okk = okk and res[0] == res[1] and res[0].get("io_error") is False and digest(off.root) == digest(on.root)
        okk = (okk and n_rows > 0 and off.c == on.c and off.c["rows_total"] == n_rows
               and not (off.root / "advice").exists())
        a1 = on._advisor
        p1 = on.root / "advice" / "advice.jsonl"
        r1 = [json.loads(x) for x in p1.read_text(encoding="utf-8").splitlines()] if p1.exists() else []
        okk = (okk and bool(r1) and all(r.get("effect") == "NONE" for r in r1) and a1.state == "OK" and a1.errors == 0
               and a1.mode == AL.MODE_LIVE and a1.c["advice_written"] == a1.c["advice_emitted"] > 0)
        # (b) doğrudan: hazır depo (yarısı) + mevcut imleç; yetişme sonra canlı
        root = Path(d) / "xp"
        X.write_hot(root, [r for b in bs[:half] for r in b])
        (root / "cursor.json").write_text('{"schema": "tb-deploy-probe", "born_ms": 1}\n', encoding="utf-8")
        main = ExperienceStore(root, max_total_mb=None, hot_max_lines=50_000)
        adv = AL.LiveAdvisor(root, settings=XpSettings(advisor_mode="RECORD"), main_store=main)
        before = digest(root)
        adv.on_flush([], budget_ms=250)                     # yetişme (depo baştan)
        okk = okk and digest(root) == before and adv.mode == AL.MODE_LIVE
        for b in bs[half:]:
            w, _rej = main.append_rows(b)
            okk = okk and w == len(b)
            before = digest(root)
            adv.on_flush(b, budget_ms=250)
            okk = okk and digest(root) == before
        adv_rows = [json.loads(x) for x in (root / "advice" / "advice.jsonl").read_text(encoding="utf-8").splitlines()]
        okk = (okk and adv_rows and all(r.get("effect") == "NONE" for r in adv_rows) and adv.state == "OK"
               and adv.errors == 0 and adv.c["advice_written"] == adv.c["advice_emitted"] > 0)
    return bool(okk)


def restore_yes():
    """deploy/restore.sh gerçekten geri yükler, sahipliği verir ve hatada/kesintide worker'ı BAŞLATMAZ — iki katman.
    (1) SATIRLAR (yorumlar hariç): kuru çalışma dalı TAM OLARAK `exec "$PYBIN" -m tradingbot restore "$ARCHIVE"`; tek
    `set -euo pipefail`, hiçbir `set +…` yok; sahip geri yüklemeden ÖNCE (worker biriminin User='ı → `id -u/-g`; yoksa
    `stat -L -c '%u:%g'`; başka OWNER ataması yok); restore_failed() iletisi ("GERİ YÜKLEME BAŞARISIZ", "Worker DURDURULDU ve
    yeniden BAŞLATILMADI", chown çaresi, başlatma komutu) ve içinde exit yok; ERR tuzağı → INT/TERM tuzağı → HUP/PIPE yok
    sayma → worker'ı durdurma → `OUT=… restore "$ARCHIVE" --yes` ARDIŞIK; hemen ardından sahiplik satırı → `chown -R`
    → vault.restored-* döngüsü (root'a aitse chown) → `trap - ERR INT TERM` ARDIŞIK; tek `systemctl start` en sonda.
    (2) DAVRANIŞ: betik geçici klasörde taklit python/systemctl/chown/stat ile koşulur (gerçek state/servis/sahiplik
    DEĞİŞMEZ): başarı yolu çağrı sırası TAM (show → stop → restore --yes → chown -R <sayısal sahip> → start → status);
    root'a ait vault kopyası da chown'lanır, root'a ait olmayan dokunulmaz; doğrulama hatası / chown hatası / Ctrl+C →
    ileti (bir kez), rc≠0 (Ctrl+C 130), worker BAŞLATILMAZ; birim User='ı sahip olur, olmayan kullanıcıda worker'a
    dokunulmadan DUR; kuru çalışma yalnız `restore ARŞİV`; çıktı kapalı boruya giderken (| head) ve SSH kopmasında
    (SIGHUP) sahiplik verilir ve worker başlar."""
    import pwd
    import re
    import shutil
    import subprocess
    import tempfile
    rs = Path("deploy/restore.sh")
    R = 'OUT="$("$PYBIN" -m tradingbot restore "$ARCHIVE" --yes)"'
    TR = "trap 'restore_failed \"satır $LINENO\"' ERR"
    TS = "trap 'restore_failed \"kesildi: Ctrl+C / SIGTERM\"; exit 130' INT TERM"
    TI = "trap '' HUP PIPE"
    SP = 'echo "worker durduruluyor..." || true; systemctl stop tradingbot-worker.service || true'
    SAH = 'echo "sahiplik geri veriliyor: $OWNER → $STATE" || true'
    CH = 'chown -R "$OWNER" "$STATE"'
    VL = 'for v in "$TRADINGBOT_DATA"/vault.restored-*; do'
    VI = 'if [[ -e "$v" ]] && [[ "$(stat -c \'%U\' "$v")" == "root" ]]; then chown -R "$OWNER" "$v"; fi'
    UN = "trap - ERR INT TERM"

    def lines_ok_():
        lines = [ln.strip() for ln in rs.read_text(encoding="utf-8").splitlines()]
        code = [ln for ln in lines if ln and not ln.startswith("#")]
        try:
            i = code.index('if [[ "$DRY" == "--dry-run" ]]; then')
            j = code.index("fi", i)
            dry, post = code[i + 1:j], code[j + 1:]
            own = [post.index('STATE="$TRADINGBOT_STATE_DIR"'),
                   post.index('OWN_REF="$STATE"; [[ -d "$OWN_REF" ]] || OWN_REF="$TRADINGBOT_DATA"'),
                   post.index('SVC_U="$(systemctl show -p User --value tradingbot-worker.service 2>/dev/null || true)"'),
                   post.index('U_ID="$(id -u "$SVC_U" 2>/dev/null || true)"; G_ID="$(id -g "$SVC_U" 2>/dev/null || true)"'),
                   post.index('OWNER="$U_ID:$G_ID"'),
                   post.index('OWNER="$(stat -L -c \'%u:%g\' "$OWN_REF")"')]
            fb = post.index("restore_failed() {")
            fe = post.index("}", fb)
            tr = post.index(TR)
            r = post.index(R)
            un = post.index(UN)
        except ValueError:
            return False
        body = post[fb + 1:fe]
        msg = all(m in " ".join(body) for m in ("GERİ YÜKLEME BAŞARISIZ", "Worker DURDURULDU ve yeniden BAŞLATILMADI",
                                                "sudo chown -R $OWNER $STATE", "sudo systemctl start tradingbot-worker.service"))
        no_exit = (not any(re.search(r"\bexit\b", ln) for ln in body)
                   and [ln for ln in body if re.search(r"\breturn\b", ln)] == ['[[ -z "${RF_DONE:-}" ]] || return 0'])
        starts = [k for k, ln in enumerate(post) if "systemctl start" in ln and not fb < k < fe]
        return bool(
            dry == ['exec "$PYBIN" -m tradingbot restore "$ARCHIVE"'] and code[:i].count("set -euo pipefail") == 1
            and not any(re.search(r"(^|[;&|{(!]\s*)set\s+\+", ln) for ln in code)
            and own == sorted(own) and own[-1] < fb < fe < tr
            and sum(1 for ln in code if re.search(r"(^|[;&|\s])OWNER=", ln)) == 2
            and post[tr:tr + 5] == [TR, TS, TI, SP, R] and post[r:r + 7] == [R, SAH, CH, VL, VI, "done", UN]
            and sum(1 for ln in post if ln.startswith("trap ")) == 4 and msg and no_exit
            and len(starts) == 1 and starts[0] > un
            and re.search(r"(^|;\s*)systemctl start tradingbot-worker\.service$", post[starts[0]]))

    def behaves_():
        # ---- (2) davranış: taklitlerle, geçici klasörde (servis kullanıcısıyla; root gerekmez)
        stub_py = r"""#!/bin/bash
echo "py $*" >> "$STUB_LOG"
[[ "${STUB_FAIL:-}" == 1 ]] && { echo "StorageError: taklit doğrulama hatası" >&2; exit 1; }
if [[ " $* " == *" --yes "* ]]; then
  d="$(dirname "$TRADINGBOT_STATE_DIR")"
  if [[ -n "${STUB_SIG:-}" ]]; then kill "-$STUB_SIG" 0; [[ "$STUB_SIG" == INT ]] && exit 130; fi
  [[ -d "$TRADINGBOT_STATE_DIR" ]] && mv "$TRADINGBOT_STATE_DIR" "$d/state.pre-restore-T"
  mkdir -p "$TRADINGBOT_STATE_DIR"; echo restored > "$TRADINGBOT_STATE_DIR/marker.txt"
  [[ "${STUB_VAULT:-}" == 1 ]] && mkdir -p "$d/vault.restored-T"
  echo '{"ok": true, "members": ['; for k in $(seq 1 200); do echo "  \"state/f$k\","; done; echo '"state/"]}'
else
  echo '{"ok": true, "dry_run": true}'
fi
"""
        stub_sc = '#!/bin/bash\necho "systemctl $*" >> "$STUB_LOG"\n[[ "$1" == show ]] && echo "${STUB_USER:-}"\nexit 0\n'
        stub_ch = '#!/bin/bash\necho "chown $*" >> "$STUB_LOG"\nexit "${STUB_CHOWN_RC:-0}"\n'
        stub_st = ('#!/bin/bash\nif [[ -n "${STUB_VAULT_OWNER:-}" && "$1" == -c && "$2" == %U && "$3" == *"/vault.restored-"* ]]; '
                   'then echo "$STUB_VAULT_OWNER"; exit 0; fi\nexec "$REAL_STAT" "$@"\n')
        bash = shutil.which("bash") or "/bin/bash"
        real_stat = shutil.which("stat") or "/usr/bin/stat"
        arc = "/nonexistent/tb-inv42.tar.gz"
        SHOW, STOP = "systemctl show -p User --value tradingbot-worker.service", "systemctl stop tradingbot-worker.service"
        PYY, START = "py -m tradingbot restore %s --yes" % arc, "systemctl start tradingbot-worker.service"
        STATUS = "systemctl --no-pager status tradingbot-worker.service"
        other = next(p for p in pwd.getpwall() if p.pw_uid not in (os.getuid(), 0))   # ne kendisi ne root
        with tempfile.TemporaryDirectory(prefix="tb-inv42-") as tmp:
            td = Path(tmp)
            (td / "bin").mkdir()
            for name, body_ in (("python", stub_py), ("systemctl", stub_sc), ("chown", stub_ch), ("stat", stub_st)):
                (td / "bin" / name).write_text(body_, encoding="utf-8")
                (td / "bin" / name).chmod(0o755)

            def run(tag, *args, out=None, session=False, **envx):
                data = td / tag / "opt" / "data"
                state = data / "state"
                (td / tag / "opt" / "app").mkdir(parents=True)
                state.mkdir(parents=True)
                (state / "old.txt").write_text("old", encoding="utf-8")
                so = state.stat()
                log = td / tag / "calls.log"
                env = {"PATH": "%s:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" % (td / "bin"), "HOME": str(td),
                       "LANG": "C.UTF-8", "TRADINGBOT_BASE": str(td / tag / "opt"), "TRADINGBOT_PY": str(td / "bin" / "python"),
                       "TRADINGBOT_DATA": str(data), "TRADINGBOT_STATE_DIR": str(state), "STUB_LOG": str(log),
                       "REAL_STAT": real_stat, **envx}
                p = subprocess.run([bash, str(rs.resolve()), arc, *args], env=env, cwd=str(td), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE if out is None else out, stderr=subprocess.PIPE, text=True,
                                   timeout=60, start_new_session=session)
                calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
                return p, calls, state, "%d:%d" % (so.st_uid, so.st_gid)       # sahip = eski state'in sayısal sahibi

            p, c, s, own_ = run("ok", STUB_VAULT="1", STUB_VAULT_OWNER="tradingbot")
            ok = (p.returncode == 0 and c == [SHOW, STOP, PYY, "chown -R %s %s" % (own_, s), START, STATUS]
                  and (s / "marker.txt").exists() and "GERİ YÜKLEME BAŞARISIZ" not in p.stderr)
            p, c, s, own_ = run("vault", STUB_VAULT="1", STUB_VAULT_OWNER="root")
            ok = ok and p.returncode == 0 and c == [SHOW, STOP, PYY, "chown -R %s %s" % (own_, s),
                                                    "chown -R %s %s" % (own_, s.parent / "vault.restored-T"), START, STATUS]
            p, c, s, own_ = run("fail", STUB_FAIL="1")
            ok = (ok and p.returncode != 0 and c == [SHOW, STOP, PYY] and p.stderr.count("GERİ YÜKLEME BAŞARISIZ") == 1
                  and "Worker DURDURULDU ve yeniden BAŞLATILMADI" in p.stderr and (s / "old.txt").exists())
            p, c, s, own_ = run("chownfail", STUB_CHOWN_RC="1")
            ok = (ok and p.returncode != 0 and c == [SHOW, STOP, PYY, "chown -R %s %s" % (own_, s)]
                  and "GERİ YÜKLEME BAŞARISIZ" in p.stderr and ("sudo chown -R %s %s" % (own_, s)) in p.stderr)
            p, c, s, own_ = run("unit", STUB_USER=other.pw_name)
            ok = ok and p.returncode == 0 and c[3:4] == ["chown -R %d:%d %s" % (other.pw_uid, other.pw_gid, s)] and START in c
            p, c, s, own_ = run("nouser", STUB_USER="tb_inv42_no_such_user")
            ok = ok and p.returncode != 0 and c == [SHOW] and (s / "old.txt").exists()
            p, c, s, own_ = run("dry", "--dry-run")
            ok = ok and p.returncode == 0 and c == ["py -m tradingbot restore %s" % arc]
            rfd, wfd = os.pipe()
            os.close(rfd)
            try:
                p, c, s, own_ = run("pipe", out=wfd)
            finally:
                os.close(wfd)
            ok = ok and c == [SHOW, STOP, PYY, "chown -R %s %s" % (own_, s), START, STATUS]
            p, c, s, own_ = run("sigint", session=True, STUB_SIG="INT")
            ok = (ok and p.returncode == 130 and c == [SHOW, STOP, PYY] and p.stderr.count("GERİ YÜKLEME BAŞARISIZ") == 1
                  and (s / "old.txt").exists())
            p, c, s, own_ = run("sighup", session=True, STUB_SIG="HUP")
            ok = ok and p.returncode == 0 and c == [SHOW, STOP, PYY, "chown -R %s %s" % (own_, s), START, STATUS]
        return bool(ok)

    return bool(lines_ok_() and behaves_())

def xp_paper_only():
    """Toplayıcı (yeni kod) mod PAPER değilken ya da canlı emir yolu açıkken SUSPENDED:<mod> olur ve satır YAZMAZ.
    Geçici klasörde gerçek `SharedExperienceCollector` + gerçek `ModeState` ile sınanır (state'e dokunmaz)."""
    import tempfile
    import types
    from tradingbot.risk.modes import OperatingMode
    from tradingbot.shared_experience.collector import SharedExperienceCollector, XpSettings

    class _LiveOrderPath(ModeState):
        is_live_order_path_enabled = staticmethod(lambda: True)

    live, testnet = ModeState(None), ModeState(None)
    live.mode, testnet.mode = OperatingMode.LIVE, OperatingMode.TESTNET
    cases = (("SUSPENDED:LIVE", live), ("SUSPENDED:LIVE_ORDER_PATH_ENABLED", _LiveOrderPath(None)),
             ("SUSPENDED:TESTNET", testnet))
    with tempfile.TemporaryDirectory(prefix="tb-deploy-xp-") as d:
        root = Path(d) / "xp"
        c = SharedExperienceCollector(root, settings=XpSettings.from_section(xp))
        for want, ms in cases:
            r = c.step(types.SimpleNamespace(mode_state=ms))
            if r.get("state") != want or c.state != want or r.get("rows") != 0:
                return False
        return c.c["rows_total"] == 0 and not (root / "experience.jsonl").exists()


def xp_situation_consts():
    from tradingbot.pattern_trader.data import BARS_PER_TF
    from tradingbot.shared_experience import situation as XS
    return (XS.SCHEMA_ID == "situation_v1" and XS.SCHEMA_SHA == "640fd10e5d6f727c"
            and XS.W4H == XS.W1H == XS.WBTC == 200 <= BARS_PER_TF["4h"] == BARS_PER_TF["1h"] == 240)


def xp_row_consts():
    import tradingbot.shared_experience as XP
    from tradingbot.shared_experience import rows as XR
    from tradingbot.shared_experience import store as XST
    return (XP.ROW_SCHEMA == XR.ROW_SCHEMA == "shared_experience_row_v1" and XP.LAYER_VERSION == "1.0.0"
            and XST.ROTATE_KEEP_FRACTION == 0.5)


def cf_consts():
    from tradingbot import learning_cf as CF
    return (CF.LABEL_VERSION, CF.NET_CONTRACT, CF.INTRABAR_PATH) \
        == ("cf_label_v3", "cf_net_ledger_replay_v2", "open>adverse>favourable>close")


def _xp_imported(path):
    import ast
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = "." * n.level + (n.module or "")
            mods = [base] + [base + "." + a.name for a in n.names]
        elif isinstance(n, ast.Call):            # importlib.import_module("…") / __import__("…")
            f = n.func
            if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) in ("import_module", "__import__"):
                mods = [a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if any("shared_experience" in m for m in mods):
            return True
    return False


def xp_importers():
    """Dağıtılan ağaçta (tradingbot/ + scripts/) paketi YALNIZ engine_v3 ve cli_v3 içe aktarır; panel aktarmaz. (2026-10-02)
    scripts/ altında TEK istisna çevrimdışı SENTETİK demo betiği (XP_DEMO) ve o da ancak `_demo_isolated()` doğruysa:
    çalışma zamanından anılmıyor ve gerçek (işaretsiz) state klasörüne yazmayı reddediyor."""
    pkg = Path("tradingbot") / "shared_experience"
    found = set()
    for base in ("tradingbot", "scripts"):
        for p in sorted(Path(base).rglob("*.py")):
            if p.parent == pkg or pkg in p.parents:
                continue
            if "shared_experience" in p.read_text(encoding="utf-8") and _xp_imported(p):
                found.add(p.as_posix())
    okk = (found - {XP_DEMO}) <= XP_IMPORTERS and "tradingbot/engine_v3.py" in found \
        and not any(f.startswith("tradingbot/dashboard/") for f in found)
    if okk and XP_DEMO in found:
        okk = _demo_isolated()
    if not okk:
        print("         içe aktaranlar: %s (izinli: %s + çevrimdışı demo %s)" % (sorted(found), sorted(XP_IMPORTERS), XP_DEMO))
    return okk


def _demo_isolated():
    """XP_DEMO çalışma zamanından ANILMAZ: tradingbot/, deploy/ (deploy/releases/ hariç) ve diğer scripts/ dosyalarının
    hiçbirinde 'demo' geçmez (büyük/küçük harf; "de""mo" / "de" + "mo" gibi bölünmüş dizgiler birleştirilerek) — worker,
    panel, birimler, yedek/geri yükleme ve dağıtımın koştuğu betikler onu adıyla da glob'la da çağıramaz. Ve işaret dosyası
    olmayan dolu bir state klasörüne YAZMAZ: AYRI bir süreçte — servis ortamı AKTARILMAZ; TRADINGBOT_* yalnız geçici yem
    klasörlerini gösterir (orada da dolu bir state), HOME ve çalışma dizini geçici — build(<state ağacı>) ValueError,
    main([<state ağacı>]) 2 ve argümansız main() (çalışma dizinindeki varsayılan demo-panel/ içinde futures_ledger'sız
    yabancı state varken) 2 döner; geçici ağaçların tamamı (iki yabancı state, yem klasörleri, HOME) içe aktarma anı dahil
    bayt bayt aynı kalır."""
    import hashlib
    import re
    import subprocess
    import tempfile
    refs = []
    for base in ("tradingbot", "deploy", "scripts"):
        for p in sorted(Path(base).rglob("*")):
            rel = p.as_posix()
            if not p.is_file() or rel == XP_DEMO or rel.startswith("deploy/releases/") or "__pycache__" in rel:
                continue
            txt = re.sub(r"[\"'`]\s*\+?\s*[\"'`]", "", p.read_text(encoding="utf-8", errors="replace"))
            if re.search(r"(?i)demo", txt):
                refs.append(rel)
    if refs:
        print("         'demo' geçen çalışma zamanı dosyaları: %s" % refs[:6])
        return False

    def snap(root):
        return {q.relative_to(root).as_posix(): (hashlib.sha256(q.read_bytes()).hexdigest() if q.is_file() else "dir")
                for q in sorted(root.rglob("*"))}

    def state_tree(st_dir, ledger=True):
        files = {"mode.json": '{"mode": "PAPER"}\n', "shared_experience/experience.jsonl": '{"row": 1}\n',
                 "shared_experience/advice/advisor_status.json": '{"mode": "live"}\n'}
        if ledger:
            files["futures_ledger.json"] = '{"wallet_balance": "1"}\n'
        for rel, body in files.items():
            (st_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (st_dir / rel).write_text(body, encoding="utf-8")
    drv = ("import contextlib, importlib.util, io, json, pathlib, sys\n"
           "spec = importlib.util.spec_from_file_location('tb_deploy_demo_probe', sys.argv[1])\n"
           "mod = importlib.util.module_from_spec(spec)\n"
           "spec.loader.exec_module(mod)\n"
           "res = {}\n"
           "try:\n"
           "    mod.build(pathlib.Path(sys.argv[2]), seed=1)\n"
           "    res['build'] = 'YAZDI'\n"
           "except ValueError:\n"
           "    res['build'] = 'ret'\n"
           "for k, a in (('main_kok', [sys.argv[2]]), ('main_argumansiz', [])):\n"
           "    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):\n"
           "        try:\n"
           "            res[k] = mod.main(a)\n"
           "        except SystemExit as e:\n"
           "            res[k] = 'exit %s' % e.code\n"
           "print(json.dumps(res))\n")
    with tempfile.TemporaryDirectory(prefix="tb-deploy-demo-") as d:
        tmp = Path(d)
        root, cwd, home, envb = tmp / "x", tmp / "cwd", tmp / "home", tmp / "envbase"
        state_tree(root / "state")
        (root / "data").mkdir(parents=True)
        (root / "data" / "binanceusdm_BTC-USDT_1h.csv").write_text("timestamp,open\n1,1\n", encoding="utf-8")
        state_tree(cwd / "demo-panel" / "state", ledger=False)
        home.mkdir()
        state_tree(envb / "data" / "state")
        (tmp / "tmp").mkdir()
        env = {"PATH": os.environ.get("PATH") or "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(tmp / "tmp"),
               "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "MPLCONFIGDIR": str(tmp / "tmp"),
               "TRADINGBOT_BASE": str(envb), "TRADINGBOT_DATA": str(envb / "data"),
               "TRADINGBOT_STATE_DIR": str(envb / "data" / "state"), "TRADINGBOT_BACKUPS_DIR": str(envb / "data" / "backups"),
               "TRADINGBOT_VAULT_PATH": str(envb / "vault"), "TRADINGBOT_CACHE_DIR": str(envb / "data" / "cache"),
               "TRADINGBOT_LOG_DIR": str(envb / "data" / "logs"), "TRADINGBOT_APP": str(envb / "app")}
        dirs = (root, cwd, home, envb)
        before = [snap(x) for x in dirs]
        try:
            r = subprocess.run([sys.executable, "-c", drv, str(Path(XP_DEMO).resolve()), str(root)], cwd=str(cwd), env=env,
                               capture_output=True, text=True, timeout=180)
            out = r.stdout.strip().splitlines()
            res = json.loads(out[-1]) if (r.returncode == 0 and out) else {"rc": r.returncode, "stderr": r.stderr[-200:]}
        except (subprocess.TimeoutExpired, ValueError) as exc:
            res = {"hata": type(exc).__name__}
        after = [snap(x) for x in dirs]
    same = before == after
    okk = res == {"build": "ret", "main_kok": 2, "main_argumansiz": 2} and same
    if not okk:
        print("         demo betiği yabancı state'e yazmayı REDDETMEDİ ya da geçici ağaçlara dokundu: %s · dosyalar aynı=%s%s"
              % (res, same, "" if same else " (değişen: %s)" % [x.name for x, b, a in zip(dirs, before, after) if b != a]))
    return okk


def xp_config_hash():
    """Motorun config_hash'i shared_experience bölümüyle ve bölümsüz AYNI (motor bölümü düşürür: karar kimliği değişmez)."""
    import copy
    import types
    from tradingbot.config_v3 import load_v3
    from tradingbot.engine_v3 import TradingEngineV3

    def h(v):
        return TradingEngineV3.config_hash(types.SimpleNamespace(cfg=types.SimpleNamespace(v3=v)))
    with_sec = h(load_v3(copy.deepcopy(raw)))
    without = h(load_v3({k: copy.deepcopy(v) for k, v in raw.items() if k != "shared_experience"}))
    return with_sec is not None and with_sec == without == h(v3)


# --- ÖNCEKİ SÜRÜM (fc63481, 2026-10-02; VPS'te çalışan): tur fazları + ön ısıtma modülleri ve --check biçimleri (#44),
# cf_aux_v1 yalnız kayıt (#45) — bu sürümde de AYNEN geçerli (--check bunları okur)
PREWARM_ERR_RE = r"pattern kanıtı ön ısıt(ması başarısız|ılamadı|ması kurulamadı)|yayım sonrası kanca başarısız"   # = --check
LOG_TXT_RE = r"^[0-9]{2}:[0-9]{2}:[0-9]{2} [A-Z]+ +[A-Za-z0-9_.]+: "                 # = --check (metin satırı öneki)
# #45 senaryosunun 48 sentetik kaydının r_net'i × 10^6 — 188cf22 koduyla hesaplandı (VPS'te çalışan fc63481'de ve f8b05fb'de
# de AYNI; sha256 3bd6f26eb409312c). Tolerans 2 × 10^-6 R: yalnız son hane yuvarlaması (ortam farkı); gerçek bir r_net değişikliği
# (ör. 0,05 R kayma) bunun binlerce katıdır.
R_NET_188CF22_E6 = (1915471, -1370697, -1372160, -397232, 1586713, 101728, 1648941, 1915471, 1915471, -1372160,
                    -1370697, -1084183, -1207111, -519768, -1084183, -1370697, 1915471, 1915471, -1372160, 380220,
                    -1370697, -1205587, -1205587, -35707, 1292353, -1084183, 1686786, 1686786, 1937603, -1084183,
                    -1372160, 0, 610742, 611480, -1084183, 1312671, -1370697, 1309976, -1207111, 0, 1935413, 1686786,
                    -1372160, 1684643, 1490020, -1372160, -1085752, 610742)
TOUR_PHASE_KEYS = ["prep", "scan", "symbols", "coin_heads", "execute", "main_tick", "strategy_books", "learning", "charts",
                   "state", "chart_analysis", "journal", "exit_eval", "entry_eval", "experiment", "wrap_up", "obsidian"]


def _log_consts(path, func=None):
    """`log.<seviye>("…", …)` çağrılarının sabit ilk argümanları {seviye: [metin]} (func verilirse yalnız o fonksiyonda)."""
    import ast
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    roots = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func] if func else [tree]
    out = {}
    for r in roots:
        for n in ast.walk(r):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "log" and n.args and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)):
                out.setdefault(n.func.attr, []).append(n.args[0].value)
    return out


def new_modules():
    """Bu sürümün yeni modülleri dağıtılan ağaçta ve --check'in OKUDUĞU biçimler: ops/tour_phases (health.json "phases" = 17
    sabit faz + pattern_evidence alt fazı, fazların toplamı turun süresi; günlükte "tur fazları: … toplam Xs"); motor
    "phases"ı health.json'a yazıyor ve satırı logluyor; patterns/evidence_cache + yayım sonrası kanca (yenileyici yayımdan
    SONRA çağırıyor, motor kancayı veriyor, ön ısıtma açık); ön ısıtma/kanca uyarılarının HER biri --check'in aradığı
    ifadeyle (PREWARM_ERR_RE) loglanıyor, başarı/bırakma/yayım satırları --check'in saydığı metinle ve bu günlükçüler INFO'yu
    süzmüyor; motor her fazı turda tam bir kez ve sırayla ölçüyor (tour() içinde 17 düz _ph.lap); worker'ın metin satırı
    biçimi --check'in süzgeciyle (LOG_TXT_RE) eşleşiyor; learning_cf_aux (cf_aux_v1, AÇIK — karne "ihtiyatlı" bunu okur). --check'in raporladığı özelliklerin anahtarları (ön ısıtma, aux)
    gözden geçirildiği gibi AÇIK; raporlanmayan memo anahtarları sabitlenmez."""
    import re
    from tradingbot import learning_cf_aux as AX
    from tradingbot.engine_v3 import TradingEngineV3
    from tradingbot.ops import tour_phases as TP
    from tradingbot.patterns import evidence_cache as EC
    clock = [1000.0]
    tp = TP.TourPhases(clock=lambda: clock[0])
    for i, k in enumerate(TOUR_PHASE_KEYS):
        clock[0] += i + 1
        tp.lap(k)
    tp.add_sub("pattern_evidence", 2.0)
    hh = tp.as_health()
    ln = tp.log_line()
    okk = (list(hh) == TOUR_PHASE_KEYS + ["pattern_evidence"] and [k for k, _l in TP.PHASES] == TOUR_PHASE_KEYS
           and hh["obsidian"] == 17.0 and sum(hh[k] for k in TOUR_PHASE_KEYS) == 153.0 and hh["pattern_evidence"] == 2.0
           and ln.startswith("tur fazları: ") and ln.endswith(" · toplam 153.0s"))
    pins = [("tradingbot/engine_v3.py", '"phases": _ph.as_health()}'), ("tradingbot/engine_v3.py", 'log.info("%s", _ph.log_line())'),
            ("tradingbot/engine_v3.py", "on_publish=self._on_pattern_index_published"),
            ("tradingbot/patterns/refresher.py", "self._notify_published(new)")]
    miss = [(f, n) for f, n in pins if Path(f).read_text(encoding="utf-8").count(n) != 1]
    rx = re.compile(PREWARM_ERR_RE)
    ec = _log_consts("tradingbot/patterns/evidence_cache.py")
    warns = (ec.get("warning", []) + ec.get("error", []) + ec.get("exception", [])
             + _log_consts("tradingbot/engine_v3.py", "_on_pattern_index_published").get("warning", [])
             + _log_consts("tradingbot/patterns/refresher.py", "_notify_published").get("warning", []))
    # (2026-10-05) alt süreç uyarıları ön ısıtma HATA'sı değildir (karar ve ön ısıtma sürer, süreç içi): --check onları
    # "PATTERN KANITI ALT SÜRECİ" bölümünde sayar; öneklerin tamlığı değişmez #51'de (EVC_ERR_PREFIXES)
    unmatched = [w for w in warns if not rx.search(w) and not w.startswith("pattern kanıtı alt süre")]
    infos = ec.get("info", []) + _log_consts("tradingbot/patterns/refresher.py").get("info", [])
    texts = all(any(i.startswith(p) for i in infos) for p in ("pattern kanıtı ön ısıtıldı:",
                                                              "pattern kanıtı ön ısıtması bırakıldı",
                                                              "pattern indeksi yenilendi"))
    # (2026-10-02) --check'in saydığı INFO satırları GERÇEKTEN basılır: motorun, ön ısıtmanın ve yenileyicinin günlükçüleri
    # ve ataları (kök hariç — worker kökü INFO kurar) INFO'yu süzmez, köke iletir, kapatılmamış; logging.disable yok
    import ast
    import logging
    from tradingbot import engine_v3 as E3
    from tradingbot.patterns import refresher as RF
    chain_bad = []
    for lg in (getattr(E3, "log", None), getattr(EC, "log", None), getattr(RF, "log", None)):
        if not isinstance(lg, logging.Logger):
            chain_bad.append(repr(lg))
            continue
        x = lg
        while x is not None and x is not logging.root:
            if not (0 <= x.level <= logging.INFO) or x.disabled or not x.propagate:
                chain_bad.append("%s (seviye %s, kapalı %s, iletir %s)" % (x.name, x.level, x.disabled, x.propagate))
            x = x.parent
    if logging.root.manager.disable >= logging.INFO:
        chain_bad.append("logging.disable(%s)" % logging.root.manager.disable)
    # her faz turda tam BİR kez ve koşulsuz ölçülür: tour() gövdesinin düz deyimleri _ph.lap("<faz>") sırası = 17 faz
    # (fazların toplamı turun süresi) ve tour() içinde başka _ph.lap çağrısı yok
    def _lap(n):
        return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "lap"
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "_ph")
    tours = [n for n in ast.walk(ast.parse(Path("tradingbot/engine_v3.py").read_text(encoding="utf-8")))
             if isinstance(n, ast.FunctionDef) and n.name == "tour"]
    laps = ([st.value.args[0].value for st in tours[0].body if isinstance(st, ast.Expr) and _lap(st.value)
             and len(st.value.args) == 1 and isinstance(st.value.args[0], ast.Constant)] if len(tours) == 1 else None)
    laps_ok = laps == TOUR_PHASE_KEYS and sum(1 for n in ast.walk(tours[0]) if _lap(n)) == len(TOUR_PHASE_KEYS)
    # worker'ın metin satırı biçimi (cli._setup_logging) --check'in metin süzgeciyle (LOG_TXT_RE) ve hata ifadesiyle eşleşir
    fd = {kw.arg: kw.value.value for n in ast.walk(ast.parse(Path("tradingbot/cli.py").read_text(encoding="utf-8")))
          if isinstance(n, ast.FunctionDef) and n.name == "_setup_logging"
          for c in ast.walk(n) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "basicConfig"
          for kw in c.keywords if kw.arg in ("format", "datefmt") and isinstance(kw.value, ast.Constant)}
    rec = logging.LogRecord(EC.log.name if isinstance(getattr(EC, "log", None), logging.Logger) else "x", logging.WARNING,
                            "evidence_cache.py", 1, "pattern kanıtı ön ısıtılamadı (BTC/USDT; tur kendisi hesaplar): %s",
                            ("e",), None)
    txt_ok = (len(fd) == 2 and re.match(LOG_TXT_RE + "(" + PREWARM_ERR_RE + ")",
                                        logging.Formatter(fd["format"], fd["datefmt"]).format(rec)) is not None)
    okk = (okk and not miss and len(warns) >= 4 and not unmatched and texts and TradingEngineV3.EVIDENCE_PREWARM is True
           and callable(getattr(EC.EvidenceCache, "request_prewarm", None)) and AX.AUX_VERSION == "cf_aux_v1"
           and AX.ENABLED is True and not chain_bad and laps_ok and txt_ok)
    if not okk:
        print("         faz anahtarları %s · eksik satırlar %s · eşleşmeyen uyarılar %s · uyarı %d · info metinleri %s · aux %s"
              % (list(hh)[:3], miss, unmatched[:3], len(warns), texts, (getattr(AX, "AUX_VERSION", None), getattr(AX, "ENABLED", None))))
        print("         günlükçü süzgeci %s · tour() fazları %s · metin satırı biçimi %s"
              % (chain_bad[:3], "AYNI" if laps_ok else laps, "AYNI" if txt_ok else fd))
    return okk


def cf_aux_record_only():
    """cf_aux_v1 YALNIZ KAYIT: aynı sentetik karşı-olgusal kayıtlar (Box 5m ve ana 4h yürütme modeli — defterlerin
    kurucusuyla aynı ücret/kayma/likidasyon; gerçek defterde 12 ilk-örnek stop çıkışı) aux AÇIK ve KAPALI
    (learning_cf_aux.ENABLED) etiketlenir → "aux" DIŞINDAKİ her alan, outcome_r (araştırma eşleşmesinin okuduğu R) ve
    net_stats AYNI; aux yalnız yeni net etikette, sürümü cf_aux_v1; senaryo iki düzeltmeyi de içerir (giriş barında stop,
    örneklenmiş stop dolumu). Dağıtımın net dolgusunun yolu (relabel_net, scripts/cf_backfill_net.py) aux YAZMAZ ve sonucu
    açık/kapalı aynı. r_net DEĞİŞMEDİ: yeni net etiketin r_net'i net dolgununkiyle (aux'suz yol) AYNI ve 48 değer 188cf22
    koduyla hesaplananlarla (VPS'te çalışan fc63481'de de aynı) AYNI (R_NET_188CF22_E6, ±2·10^-6 R). Geçici klasör; gerçek state'e dokunmaz."""
    import random
    import tempfile
    from datetime import datetime as _dtm
    from datetime import timedelta, timezone
    from decimal import Decimal
    import pandas as pd
    from tradingbot import learning_cf as CF
    from tradingbot import learning_cf_aux as AX
    from tradingbot.accounting import (AmountType, FeeSchedule, FuturesLedgerV2, LiquidationParams, SizeSpec, SlippageModel,
                                       TaxPolicy, TickData, default_brackets)
    UTC = timezone.utc

    def ledger(be="0", wallet="200"):
        return FuturesLedgerV2(Decimal(wallet), fees=FeeSchedule(maker_pct=Decimal("0.02"), taker_pct=Decimal("0.05")),
                               slippage=SlippageModel(fixed_bps=Decimal("3")), brackets=default_brackets(),
                               liq_params=LiquidationParams(liq_fee_pct=Decimal("0.5")), tp1_fraction=Decimal("0.5"),
                               breakeven_at_mfe_r=Decimal(be), tax_policy=TaxPolicy.disabled())
    real = ledger(wallet="10000")
    t = _dtm(2026, 9, 29, tzinfo=UTC)
    for _ in range(12):
        if real.open("R/USDT", "LONG", Decimal("100"), SizeSpec(Decimal("1000"), AmountType.NOTIONAL, 1), stop=Decimal("99"),
                     targets=[], now=t) is None:
            print("         gerçek defter açamadı: %s" % real.last_reject_reason)
            return False
        t += timedelta(seconds=60)
        real.tick({"R/USDT": TickData(last=Decimal("98.9"), mark=Decimal("98.9"), ts=t.isoformat())}, now_utc=t,
                  bar_advance=False)
        t += timedelta(seconds=60)
    models = (("box", CF.ExecModel.of_ledger(ledger()), 5), ("main", CF.ExecModel.of_ledger(ledger(be="1.0")), 240))

    def run(enabled, base):
        AX.ENABLED = enabled
        rnd = random.Random(7)
        new, backfill = [], []
        for name, xm, tf in models:
            tf_ms = tf * 60_000
            t0 = _dtm(2026, 9, 30, tzinfo=UTC)
            for i in range(24):
                side = rnd.choice(["LONG", "SHORT"])
                sg = 1 if side == "LONG" else -1
                e = 100.0
                st_ = e * (1 - sg * rnd.choice([0.0032, 0.006, 0.015]))
                r = abs(e - st_)
                tg = [e + sg * 1.5 * r, e + sg * 3 * r] if name == "main" else [e + sg * 2.5 * r]
                px, bars = e, []
                for k in range(14):
                    o = px
                    c = px * (1 + rnd.gauss(0, 0.004))
                    bars.append({"timestamp": int(t0.timestamp() * 1000) + k * tf_ms, "open": o,
                                 "high": max(o, c) * (1 + abs(rnd.gauss(0, 0.002))),
                                 "low": min(o, c) * (1 - abs(rnd.gauss(0, 0.002))), "close": c, "volume": 1.0})
                    px = c
                frames = {"BTC/USDT": {{5: "5m", 240: "4h"}[tf]: pd.DataFrame(bars)}}
                created = t0 + timedelta(milliseconds=tf_ms * 3 + 7_000)
                now = t0 + timedelta(milliseconds=tf_ms * 15)
                kw = dict(symbol="BTC/USDT", direction=side, entry=e, stop=st_, targets=tg, reason="INSUFFICIENT_MARGIN",
                          created_at=created, tf_minutes=tf, horizon_bars=10, label_kind="TARGET_STOP_TIME")
                cf = CF.CounterfactualRecorder(Path(base) / ("%s_new_%d.json" % (name, i)), book="tb-deploy-probe")
                if not cf.record(signal_key="new%d" % i, **kw):
                    return None, None
                cf.label_pending(frames, now, exec_model=xm, real_history=lambda: real.history)
                new.append(cf.sb.trades[-1].outcome)
                old = CF.CounterfactualRecorder(Path(base) / ("%s_old_%d.json" % (name, i)), book="tb-deploy-probe")
                if not old.record(signal_key="old%d" % i, **kw):
                    return None, None
                old.label_pending(frames, now)                       # eski (brüt) etiket
                CF.relabel_net(list(old.sb.trades), frames, now, exec_model=xm)   # dağıtımın net dolgusunun yolu
                backfill.append(old.sb.trades[-1].outcome)
        return new, backfill

    keep = AX.ENABLED
    try:
        with tempfile.TemporaryDirectory(prefix="tb-deploy-aux-") as d:
            on, b_on = run(True, d)
            off, b_off = run(False, d)
    finally:
        AX.ENABLED = keep
    if on is None or off is None:
        print("         sentetik kayıt yazılamadı")
        return False
    pairs = list(zip(on, off))
    same_rest = all(isinstance(a, dict) and isinstance(b, dict) and "aux" not in b and set(a) - set(b) == {"aux"}
                    and {k: v for k, v in a.items() if k != "aux"} == b and CF.outcome_r(a) == CF.outcome_r(b)
                    and isinstance(a.get("aux"), dict) and a["aux"].get("aux_version") == "cf_aux_v1"
                    and a.get("label_version") == "cf_label_v3" for a, b in pairs)
    hits = sum(1 for a, _b in pairs if isinstance(a.get("aux"), dict)
               and ((a["aux"].get("entry_bar") or {}).get("stop_hit") is True))
    sampled = sum(1 for a, _b in pairs if isinstance(a.get("aux"), dict)
                  and a["aux"].get("r_net_sampled_est") not in (None, a.get("r_net")))
    backfill_ok = (b_on == b_off and all(isinstance(o, dict) and "aux" not in o and o.get("r_net") is not None
                                         for o in b_on))
    rn = [a.get("r_net") if isinstance(a, dict) else None for a, _b in pairs]
    rnet_bf = rn == [o.get("r_net") if isinstance(o, dict) else None for o in b_on]   # yeni etiket = net dolgu (aux'suz yol)
    gold_bad = [i for i, (x, g) in enumerate(zip(rn, R_NET_188CF22_E6))
                if isinstance(x, bool) or not isinstance(x, (int, float)) or not abs(x * 1e6 - g) <= 2]
    rnet_gold = len(rn) == len(R_NET_188CF22_E6) and not gold_bad
    okk = (len(pairs) == 48 and same_rest and CF.net_stats(on) == CF.net_stats(off) and hits > 0 and sampled > 0
           and backfill_ok and rnet_bf and rnet_gold)
    if not okk:
        print("         aux dışı alanlar aynı=%s · giriş barı stop=%d · örneklenmiş dolum=%d · net dolgu aux'suz/aynı=%s"
              % (same_rest, hits, sampled, backfill_ok))
        print("         r_net yeni etiket = net dolgu: %s · r_net 188cf22 ile aynı: %s%s"
              % (rnet_bf, rnet_gold, (" (ilk fark #%d: %s ≠ %.6f)" % (gold_bad[0], rn[gold_bad[0]], R_NET_188CF22_E6[gold_bad[0]] / 1e6))
                 if gold_bad else ""))
    return bool(okk)


# --- BU SÜRÜM (2026-10-03; hedef f8b05fb, ön koşul fc63481): SAHİP ONAYLI KARAR DEĞİŞİKLİĞİ — öğrenme-ekstra girişler nedene
# göre (seçicilik-ekstra YALNIZ KAYIT, `learning_mode.extra_entries: record_selectivity`) + Box öğrenme stop tabanı 0,32 → 0,5.
# Değişmezler yalnız ONAYLANANIN dağıtıldığını sınar: config.yaml fc63481'dekinden YALNIZ bu iki değerde ayrılır (#43), sınıf
# tablosu onaylanan tablo ve deponun tablo testi dağıtılan ağaçta geçer (#46), `open` kipi fc63481 kararlarını yeniden üretir
# ve `record_selectivity` yalnız seçicilik-ekstrayı değiştirir (#47), yalnız-kayıt kayıtları araştırma politikasına ve
# deneyim havuzuna girmez (#48). #46-#48 AYRI süreçte (servis ortamı aktarılmadan) deponun kendi testleri/koşucularıyla.
import re  # noqa: E402

PREREQ_SHA = "fc63481990e65a03fd0b9d49943a3f3e26055b8c"                                  # VPS'te çalışan, gözden geçirilmiş
PREREQ_CONFIG_SHA256 = "59e18ad1c8f4f5aa8de98f38a748c594886664318f6461be97b763b18b1d1bf4"   # git show fc63481:config.yaml
TIP_CONFIG_SHA256 = "2f79d22f709d5dcc32fc250f4832a24a2209f982806d191da520207cab969c73"      # git show <hedef>:config.yaml
PREV_CONFIG_SHA256 = "be5e3e0d1c760abf228ece68c249d416d521e1643b7b18f0a12a8b254161c398"     # git show 8db1faf:config.yaml (= f8b05fb)
#: M2X ayna defter bölümü (2026-10-05, gözden geçirildi; KAPALI) — 8db1faf → hedef config.yaml'daki TEK veri farkı
M2X_CFG = {"enabled": False, "new_entries": True, "parent": "m2_tsmom28", "state_dir": "strategy_paper_m2x",
           "starting_equity_usdt": 200, "policy_version": "m2x_v1"}
#: fc63481 → hedef config.yaml'daki TEK veri farkları: sahip kararı 2026-10-03 (iki değer) + kapalı M2X bölümü; yorum
#: satırları belge (değer değil).
APPROVED_CONFIG_DELTA = {("learning_mode", "extra_entries"): ("<yok>", "record_selectivity"),
                         ("learning_mode", "books", "b1_box_fade", "min_stop_pct"): (0.32, 0.5),
                         ("m2x_aggressive",): ("<yok>", M2X_CFG)}
#: 8db1faf (VPS'te çalışan) → hedef: YALNIZ kapalı M2X bölümü
PREV_CONFIG_DELTA = {("m2x_aggressive",): ("<yok>", M2X_CFG)}
#: Onaylanan sınıf tablosu (docs/ogrenme_modu/DURUM.md 2026-10-03; tradingbot/learning_mode.py UNLOCK_*): kod → sınıf.
APPROVED_UNLOCK = dict(
    [(c, "capacity") for c in (
        "TOTAL_OPEN_RISK", "MAX_POSITIONS", "MAX_POSITIONS_MARKET", "MAX_POSITION_PCT", "MIN_ORDER_CONFLICT",
        "NO_TRADE_MIN_ORDER_CONFLICT", "INSUFFICIENT_MARGIN", "STEP_ZERO_QTY", "MIN_QTY", "MAX_QTY", "MIN_NOTIONAL",
        "LEVERAGE_TOO_HIGH", "RISK_ABOVE_CAP_AFTER_ROUNDING", "MARGIN_UTILIZATION", "SPOT_ALLOCATION", "CLUSTER_CAP",
        "ALTCOIN_EXPOSURE", "RISK_PER_TRADE", "LEVERAGE_CAP", "ALREADY_OPEN_SAME_SYMBOL", "ALREADY_OPEN",
        "OPPOSITE_EXPOSURE_CONFLICT")]
    + [("BOX_MIN_STOP_PCT", "box_exception")]
    + [(c, "selectivity") for c in (
        "NEGATIVE_NET_EDGE", "RESEARCH_SIZE_ONLY", "SIZE_MULTIPLIER_ZERO", "BOOK_UNIVERSE", "NOT_IN_PROTOCOL_UNIVERSE",
        "RR_BELOW_MIN_AT_ENTRY", "RR_BELOW_MIN_AFTER_ROUNDING", "COOLDOWN_AFTER_LOSS", "THIN_DEPTH", "LIQUIDITY_UNKNOWN",
        "DEPTH_UNKNOWN", "KILL_SWITCH_ACTIVE", "DAILY_LOSS", "WEEKLY_LOSS", "MAX_DRAWDOWN", "CONSEC_LOSS_COOLDOWN",
        "SYMBOL_COOLDOWN", "STOP_PRESENT", "SPOT_NO_SHORT", "SPREAD", "MIN_EXPECTED_R", "LIQ_BUFFER", "BAD_PRICE",
        "RISK_DENIED", "BASELINE_UNKNOWN")])
APPROVED_PREFIXES = ("CANDLE_VETO", "REGIME_VETO", "STRUCTURE", "LEVERAGE_GATE_BLOCKED")
RO = "LEARNING_RECORD_ONLY"
#: deponun doğrudan yol koşucusunda seçicilik-ekstra içeren senaryolar (tests/test_learning_record_only_tours.py ile aynı)
SELECTIVITY_DIRECT = {"d4_universe", "d4_killswitch", "t2_structure", "pt_rr_floor", "main_neg", "main_killswitch",
                      "main_res_capacity"}
#: (2026-10-04) dağıtılan config'te öğrenmesi AÇIK strateji defterleri (C4S'in öğrenmesi kapalı) — hepsi ORTAK açılış yerinde
#: (`strategy_paper._open_learning` → `record_only`) sürücünün `books` kipiyle sınanır: deponun aile senaryosu (tabanın da
#: açtığı sinyal) + taban kapılarının GERÇEK sonucuna eklenen kod(lar); None = gerçek kill switch (iki kipte de durdurur).
BOOK_VIEWS = ("b1_box_fade", "c4_candle_variations", "d4_donchian_20_10", "m2_tsmom28", "t2_trend_regime")
BOOK_VARIANT_CODES = {"policy": [], "capacity": ["TOTAL_OPEN_RISK"], "capacity_count": ["MAX_POSITIONS", "INSUFFICIENT_MARGIN"],
                      "box_exception": ["BOX_MIN_STOP_PCT"], "universe": ["BOOK_UNIVERSE"],
                      "structure": ["STRUCTURE_TB_DEPLOY"], "mixed": ["TOTAL_OPEN_RISK", "BOOK_UNIVERSE"],
                      "unknown_code": ["TB_DEPLOY_UNKNOWN_CODE"], "kill_switch": None}
RO_PROBE_SRC = r'''# -*- coding: utf-8 -*-
"""tb-deploy (8db1faf → db5db96) değişmez #46/#47/#48/#49/#52/#54 sürücüsü — öğrenme-ekstra YALNIZ KAYIT. AYRI süreçte koşar; servis ortamı
AKTARILMAZ (çağıran yalnız PATH/HOME/TMPDIR/LANG/TZ verir; TRADINGBOT_* yok). VPS'te pytest ve time_machine KURULU DEĞİL:
deponun kendi testleri ve koşucuları burada şu küçük yerine-geçenlerle koşar — MonkeyPatch (setattr/undo/context; sınıf
özniteliğinde tanımlayıcı korunur), mark.parametrize (parametreleri saklar), fixture, raises (tür + match), approx, skip,
fail, importorskip; time_machine.travel saati DONDURMAZ (çağıran dosya baytlarındaki saat damgalarını maskeler).
Soket düzeyinde ağ kapalıdır (unix soketi hariç).
Kullanım: python ro_probe.py table AĞAÇ | research AĞAÇ GEÇİCİ | books AĞAÇ GEÇİCİ config|fixture |
evc AĞAÇ GEÇİCİ | knn AĞAÇ GEÇİCİ | hash AĞAÇ CONFIG... | run AĞAÇ KOŞUCU [koşucu argümanları]
(2026-10-06) `knn`: hızlı kNN bit-aynılık testleri (#52; tests/test_knn_fast_identity_v1.py, `caplog` yerine-geçeniyle);
`hash`: ağacın kendi kuralıyla config_hash (#54; 8db1faf ağacı ile bu ağaç).
(2026-10-05) `evc`: pattern kanıtı alt süreci (#49) — deponun tests/test_evidence_subprocess_v1.py'sinden seçili karar-
nötrlük testleri (bit-aynı kanıt, yayım anı / sürüm görünürlüğü, eski sürümün alt süreci, arıza = bugünkü sonuç, kill
switch = bugünkü yol, varsayılan AÇIK, karar kimliği) dosyanın kendi autouse temizliğiyle (`_stop_workers`)."""
import contextlib
import importlib
import inspect
import json
import logging
import re
import runpy
import sys
import time
import types
from pathlib import Path

_NOTSET = object()


class _Skip(Exception):
    pass


class MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value=_NOTSET, raising=True):
        if isinstance(target, str):
            if value is not _NOTSET:
                raise TypeError("dize hedefle iki argüman verilir")
            value = name
            path, _, name = target.rpartition(".")
            parts, obj = path.split("."), None
            for i in range(len(parts), 0, -1):
                try:
                    obj = importlib.import_module(".".join(parts[:i]))
                except ImportError:
                    continue
                for p in parts[i:]:
                    obj = getattr(obj, p)
                break
            if obj is None:
                raise ImportError(target)
            target = obj
        old = getattr(target, name, _NOTSET)
        if raising and old is _NOTSET:
            raise AttributeError(name)
        if inspect.isclass(target):
            old = target.__dict__.get(name, _NOTSET)
        self._undo.append((target, name, old))
        setattr(target, name, value)

    def undo(self):
        while self._undo:
            t, n, o = self._undo.pop()
            if o is _NOTSET:
                delattr(t, n)
            else:
                setattr(t, n, o)

    @contextlib.contextmanager
    def context(self):
        m = MonkeyPatch()
        try:
            yield m
        finally:
            m.undo()


class _Raises:
    def __init__(self, exc, match=None):
        self.exc, self.match, self.value = exc, match, None

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            raise AssertionError("BEKLENEN HATA GELMEDİ: %r" % (self.exc,))
        if not issubclass(et, self.exc):
            return False
        if self.match is not None and not re.search(self.match, str(ev)):
            raise AssertionError("hata metni %r ile eşleşmedi: %r" % (self.match, str(ev)))
        self.value = ev
        return True


class _Approx:
    def __init__(self, expected, rel=None, abs=None):
        self.e, self.rel, self.abs = expected, rel, abs

    def __eq__(self, other):
        if isinstance(self.e, (list, tuple)):
            return isinstance(other, (list, tuple)) and len(other) == len(self.e) \
                and all(_Approx(a, self.rel, self.abs) == b for a, b in zip(self.e, other))
        try:
            e, o = float(self.e), float(other)
        except (TypeError, ValueError):
            return self.e == other
        rel = 0.0 if (self.abs is not None and self.rel is None) else (1e-6 if self.rel is None else float(self.rel))
        ab = 1e-12 if self.abs is None else float(self.abs)
        return o == e or abs(o - e) <= max(rel * abs(e), ab)

    __hash__ = None

    def __repr__(self):
        return "approx(%r)" % (self.e,)


class _Mark:
    def __getattr__(self, kind):
        def deco(*a, **k):
            if kind != "parametrize" and len(a) == 1 and callable(a[0]) and not k:
                return a[0]

            def wrap(f):
                if kind == "parametrize":
                    f.__dict__.setdefault("_tb_params", []).append((a[0], list(a[1])))
                return f
            return wrap
        return deco


class _Travel:
    """time_machine.travel yerine: saati DONDURMAZ."""

    def __init__(self, destination=None, tick=True):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def start(self):
        return self

    def stop(self):
        pass

    def shift(self, delta):
        pass

    def move_to(self, destination, tick=None):
        pass

    @staticmethod
    def time():
        return time.time()


def stand_ins():
    pt = types.ModuleType("pytest")
    tm = types.ModuleType("time_machine")
    tm.travel = _Travel

    def fixture(*a, **k):
        return a[0] if (len(a) == 1 and callable(a[0]) and not k) else (lambda f: f)

    def skip(msg="", **k):
        raise _Skip(msg)

    def fail(msg="", **k):
        raise AssertionError(msg)

    def importorskip(name, *a, **k):
        if name == "time_machine":
            return tm
        try:
            return importlib.import_module(name)
        except ImportError as exc:
            raise _Skip("%s yok" % name) from exc
    pt.MonkeyPatch, pt.mark, pt.fixture, pt.raises, pt.approx = MonkeyPatch, _Mark(), fixture, _Raises, _Approx
    pt.skip, pt.fail, pt.importorskip = skip, fail, importorskip
    sys.modules["pytest"], sys.modules["time_machine"] = pt, tm


def _tree(tree):
    tree = Path(tree).resolve()
    sys.path[:0] = [str(tree), str(tree / "tests")]
    import os
    os.chdir(tree)
    import tradingbot
    if Path(tradingbot.__file__).resolve().parent != tree / "tradingbot":
        raise RuntimeError("yanlış tradingbot paketi: %s" % tradingbot.__file__)


def _extras(tree):
    _tree(tree)
    import test_learning_record_only_extras as X
    return X


def _call(out, label, fn, *a):
    try:
        fn(*a)
        out[label] = "ok"
    except BaseException as exc:  # noqa: BLE001 — _Skip ve AssertionError dahil: düşmüş sayılır
        out[label] = "HATA %s: %s" % (type(exc).__name__, str(exc)[:300])


TABLE_TESTS = ("test_classification_table_is_pinned_and_the_sets_are_disjoint",
               "test_every_code_the_code_base_can_write_into_learning_unlocked_by_has_a_class",
               "test_learning_unlocked_by_is_written_only_by_the_three_entry_paths",
               "test_record_only_needs_learning_on_the_record_mode_and_a_selectivity_code",
               "test_config_switch_defaults_to_open_validates_and_reaches_every_book_view",
               "test_record_selectivity_requires_counterfactual_recording",
               "test_decision_journal_classifies_record_only_as_a_learning_shadow",
               "test_retag_leaves_labelled_and_already_record_only_records_alone")


def mode_table(tree):
    X = _extras(tree)
    out = {}
    for name in TABLE_TESTS:
        _call(out, name, getattr(X, name))
    for _names, values in getattr(X.test_classify_unlock_codes, "_tb_params", []):
        for v in values:
            _call(out, "test_classify_unlock_codes%s" % json.dumps(list(v)), X.test_classify_unlock_codes, *v)
    return {"results": out, "table": X.TABLE, "prefixes": list(X.PREFIXES)}


def _research_open(X, tmp):
    """Kill switch sonrası (`open`, öğrenme-ekstralar yine açılır): kayıt döneminden kalan BEKLEYEN yalnız-kayıt kaydı
    etiketlenince de araştırma politikasına girmez — motorun TAM etiketleme yolu (`_label_shadows`); normal ana
    karşı-olgusal eskisi gibi gözlenir; deneyim havuzu yalnız-kayıt kaydını almaz (deponun testiyle aynı kurgu)."""
    from datetime import timedelta

    from tradingbot.core import utc_now
    from tradingbot.learn.experience import shadow_experiences
    from tradingbot.learn.shadow import ShadowTrade
    LMM, LM, RO = X.LMM, X.LM, X.RO
    mp = MonkeyPatch()
    try:
        eng = LMM._eng(Path(tmp), mp, LMM._lm())
        if LM.extra_entries_mode(eng.lm.book("main")) != LM.EXTRA_OPEN:
            raise AssertionError("motor open kipinde değil")
        now = utc_now().replace(microsecond=0)

        def _sh(i, reasons):
            return ShadowTrade(id="cf_%d" % i, plan_id="sig%d" % i, symbol=LMM.SYMS[i], market_type="USDM_PERP",
                               direction="LONG", created_at=(now - timedelta(days=2)).isoformat(), entry=100.0, stop=95.0,
                               targets=[110.0], horizon_bars=6, variant="as_planned", reason_not_opened=list(reasons),
                               label_ts=(now - timedelta(days=1)).isoformat(), tf_minutes=240, book="main",
                               signal_key="sig%d" % i, label_kind="TARGET_STOP_TIME", features={})
        rec_only, normal = _sh(0, [RO, "NEGATIVE_NET_EDGE", "KILL_SWITCH_ACTIVE"]), _sh(1, ["TOTAL_OPEN_RISK"])
        eng.shadow.trades += [rec_only, normal]
        for t in (rec_only, normal):
            eng.research.pending["p_" + t.id] = {"policy_id": "pol", "trade_id": t.id, "decision": {"reasons": ["x"]}}
        seen = []
        mp.setattr(eng.research, "observe", lambda pid, **kw: seen.append(kw["trade_id"]))

        def _label(rows, *a, **k):
            for t in rows:
                t.outcome = {"r_multiple": 1.0, "r_net": 0.8, "label_version": "cf_label_v3", "exit_reason": "target"}
                t.labeled_at = now.isoformat()
            return len(rows), []
        mp.setattr("tradingbot.learning_cf.label_records", _label)
        eng._label_shadows()
        if not (rec_only.outcome is not None and normal.outcome is not None):
            raise AssertionError("etiketleme yolu koşmadı")
        if seen != [normal.id]:
            raise AssertionError("araştırma gözlemleri %r (yalnız %r beklenir)" % (seen, normal.id))
        if "p_" + rec_only.id not in eng.research.pending:
            raise AssertionError("yalnız-kayıt kaydının bekleyen eşleşmesine dokunuldu")
        exps = shadow_experiences([t.to_dict() for t in (rec_only, normal)], as_of_ms=None)
        if [e.symbol for e in exps] != [normal.symbol]:
            raise AssertionError("deneyim havuzu %r" % [e.symbol for e in exps])
    finally:
        mp.undo()


def mode_research(tree, tmp):
    X = _extras(tree)
    out = {}
    mp = MonkeyPatch()
    try:
        _call(out, "test_record_only_counterfactuals_never_feed_the_research_policy",
              X.test_record_only_counterfactuals_never_feed_the_research_policy, Path(tmp) / "rec", mp)
    finally:
        mp.undo()
    _call(out, "open_after_kill_switch", _research_open, X, Path(tmp) / "open")
    return {"results": out}


#: (2026-10-04) strateji defterlerinin ORTAK açılış yeri (`strategy_paper._open_learning` → `record_only`) her defterde:
#: deponun aile senaryosu (`test_learning_mode_books._scenario`: tabanın da açtığı bir sinyal) + taban kapılarının
#: (`_baseline_blocks`, GERÇEK sonucu korunur) çıktısına EKLENEN kod(lar) ya da gerçek kill switch.
BOOK_VARIANTS = (("policy", (), False), ("capacity", ("TOTAL_OPEN_RISK",), False),
                 ("capacity_count", ("MAX_POSITIONS", "INSUFFICIENT_MARGIN"), False),
                 ("box_exception", ("BOX_MIN_STOP_PCT",), False), ("universe", ("BOOK_UNIVERSE",), False),
                 ("structure", ("STRUCTURE_TB_DEPLOY",), False), ("mixed", ("TOTAL_OPEN_RISK", "BOOK_UNIVERSE"), False),
                 ("unknown_code", ("TB_DEPLOY_UNKNOWN_CODE",), False), ("kill_switch", (), True))


def _book_run(LB, SP, mp, name, root, view, codes, ks, c4_ids):
    """Bir defter + bir değişke + bir görünüm: senaryonun sembolü için açılış/ret, etiketler, yalnız-kayıt kayıtları."""
    real = SP._baseline_blocks

    def _blocks(act, **kw):
        bc, bs = real(act, **kw)
        return (list(bc) + [c for c in codes if c not in bc], None) if codes else (bc, bs)
    with mp.context() as m:
        m.setattr(SP, "_baseline_blocks", _blocks)
        book, fbs, now_ms, px = LB._scenario(name, root, c4_ids)
        if ks:
            book.risk.ks.trip("TEST", "tb-deploy probe")
        LB._step(book, fbs, now_ms=now_ms, px=px, learning=view)
    sym = LB.SYM
    p = book.ledger.positions.get(sym)
    cf = list(book.cf.sb.trades) if getattr(book, "cf", None) is not None else []
    feat = getattr(importlib.import_module("tradingbot.learning_mode"), "RECORD_ONLY_FEATURE", "learning_record_only")
    lr = (getattr(p, "meta", None) or {}).get("learning") if p is not None else None
    return {"opened": p is not None, "tags": list((lr or {}).get("learning_unlocked_by") or []) if p is not None else None,
            "reason": str((book.last_actions.get(sym) or {}).get("reason") or ""),
            "rejections": dict(sorted(getattr(book, "rejections", {}).items())),
            "ro": [[list(t.reason_not_opened or []), str(((t.features or {}).get(feat) or {}).get("book") or "")]
                   for t in cf if t.symbol == sym and list(t.reason_not_opened or [])[:1] == ["LEARNING_RECORD_ONLY"]],
            "counter": int((getattr(book, "learning_counters", None) or {}).get("learning_record_only", 0) or 0),
            "positions": sorted(json.dumps([q.symbol, str(q.qty), str(q.entry_avg), str(q.stop), int(q.leverage),
                                            (q.meta or {}).get("learning")], sort_keys=True, default=str)
                                for q in book.ledger.positions.values()),
            "cf": sorted(json.dumps([t.symbol, list(t.reason_not_opened or [])]) for t in cf)}


def mode_books(tree, root, src):
    """`config`: dağıtılan config.yaml'ın AÇIK strateji defteri görünümleri (kip config'ten) ve aynı görünümün `open` kopyası;
    `fixture`: deponun fikstür görünümü (`_bl`; extra_entries alanı varsa `open`, yoksa — fc63481 — alansız)."""
    import dataclasses
    tree, root = Path(tree).resolve(), Path(root).resolve()
    _tree(tree)
    import test_learning_mode_books as LB

    from tradingbot import learning_mode as LM
    from tradingbot import strategy_paper as SP
    mp = MonkeyPatch()
    views, res = {}, {}
    gen = LB.registry(root / "registry", mp)
    try:
        (root / "registry").mkdir(parents=True, exist_ok=True)
        c4_ids = next(gen)(LB.VX, LB.VY)
        if src == "config":
            from tradingbot.config import load_yaml_strict
            from tradingbot.config_v3 import load_v3
            raw = load_yaml_strict((tree / "config.yaml").read_text(encoding="utf-8"))
            lmv = LM.LearningMode.from_config(load_v3(raw), lambda: (True, "OK"))
            lmv.refresh()
            for name in sorted((raw.get("learning_mode") or {}).get("books") or {}):
                v = lmv.book(name)
                if v is not None and name not in ("main", "pattern_trader"):
                    views[name] = v
        else:
            has = "extra_entries" in {f.name for f in dataclasses.fields(LM.BookLearning)}
            views = {n: LB._bl(n, **({"extra_entries": "open"} if has else {})) for n in LB.FAMILIES}
        for name, view in sorted(views.items()):
            if name not in LB.FAMILIES:
                res[name] = {"HATA": "senaryo yok"}
                continue
            res[name] = {}
            for var, codes, ks in BOOK_VARIANTS:
                runs = {"fx": view} if src != "config" else {"cfg": view,
                                                              "open": dataclasses.replace(view, extra_entries="open")}
                res[name][var] = {k: _book_run(LB, SP, mp, name, root / src / name / var / k, v, codes, ks, c4_ids)
                                  for k, v in runs.items()}
    finally:
        gen.close()
        mp.undo()
    return {"views": {n: str(getattr(v, "extra_entries", "<yok>")) for n, v in views.items()}, "res": res}


def no_network():
    """Ağ YOK (deponun conftest korumasına ek, soket düzeyinde): unix soketi dışındaki her bağlantı OSError — VPS'te bu
    sınamalar borsaya ya da başka bir yere bağlanamaz; ağsız makinedeki sonucun aynısı."""
    import socket

    def _blocked(self, addr, *a, **k):
        if getattr(self, "family", None) == getattr(socket, "AF_UNIX", object()):
            return _connect(self, addr, *a, **k)
        raise OSError("tb-deploy probe: ağ kapalı (%r)" % (addr,))
    _connect = socket.socket.connect
    socket.socket.connect = _blocked
    socket.socket.connect_ex = lambda self, addr: 111
    socket.create_connection = lambda addr, *a, **k: (_ for _ in ()).throw(OSError("tb-deploy probe: ağ kapalı (%r)" % (addr,)))


EVC_TESTS = ("test_child_evidence_is_bit_identical_to_in_process_and_files_match",
             "test_the_publish_moment_and_version_visibility_are_unchanged",
             "test_a_newer_publish_closes_the_old_child_and_never_serves_the_old_version",
             "test_an_error_everywhere_gives_todays_tour_result",
             "test_kill_switch_off_never_forks_and_is_todays_path",
             "test_the_switch_defaults_off_and_config_can_turn_it_on",
             "test_the_switch_is_not_part_of_the_decision_identity")


def mode_evc(tree, tmp):
    """#49: seçili testler; her test kendi geçici klasörü, MonkeyPatch'i ve dosyanın autouse temizliğiyle koşar."""
    import inspect
    _tree(tree)
    import test_evidence_subprocess_v1 as T
    out = {}
    for i, name in enumerate(EVC_TESTS):
        tp = Path(tmp) / ("evc%02d" % i)
        tp.mkdir(parents=True)
        mp = MonkeyPatch()
        gen = T._stop_workers()
        next(gen)

        def run(fn=getattr(T, name), tp=tp, mp=mp):
            have = {"tmp_path": tp, "monkeypatch": mp}
            fn(**{p: (have[p] if p in have else T.hist()) for p in inspect.signature(fn).parameters})
        _call(out, name, run)
        mp.undo()
        try:
            next(gen)
        except StopIteration:
            pass
        except BaseException as exc:  # noqa: BLE001 — temizlik düşerse test de düşmüş sayılır
            out[name] = "HATA temizlik %s: %s" % (type(exc).__name__, str(exc)[:200])
    return out


class _ListHandler(logging.Handler):
    def __init__(self, sink):
        super().__init__(0)
        self.sink = sink

    def emit(self, record):
        self.sink.append(record)


class Caplog:
    """pytest `caplog` yerine: `at_level(seviye, logger=ad)` o günlükçüye bir liste işleyicisi bağlar; `records`, `clear()`."""

    def __init__(self):
        self.records = []

    @contextlib.contextmanager
    def at_level(self, level, logger=None):
        lg = logging.getLogger(logger)
        old, h = lg.level, _ListHandler(self.records)
        lg.setLevel(level)
        lg.addHandler(h)
        try:
            yield self
        finally:
            lg.removeHandler(h)
            lg.setLevel(old)

    def clear(self):
        del self.records[:]


#: (2026-10-06) #52: deponun tests/test_knn_fast_identity_v1.py'sinden seçili testler — (ad, yalnız bu parametreler | None =
#: parametreliyse HEPSİ). Gerçek turlar testi (time_machine ister: saat donmazsa state baytları koşudan koşuya değişir) ve
#: büyük rastgele/sınır taramalarının ikinci tohumu dışarıda; kalanlar 29 durum.
KNN_TESTS = (("test_production_call_for_every_symbol_is_bit_identical", None),
             ("test_randomized_queries_are_bit_identical_on_a_realistic_13_series_index", {"seed": 1}),
             ("test_adversarial_ties_duplicates_markets_and_nan_columns_are_bit_identical", None),
             ("test_a_query_on_a_flat_path_falls_back_to_the_old_loop_with_the_same_result", None),
             ("test_embargo_and_exit_boundaries_are_bit_identical_and_actually_hit", None),
             ("test_the_candidate_list_can_run_out_and_deep_min_sep_scans_stay_identical", None),
             ("test_the_lower_bound_holds_for_every_candidate", None),
             ("test_any_lower_bound_gives_the_same_selection", {"mode": "tight"}),
             ("test_kill_switch_never_enters_the_fast_path", None),
             ("test_an_internal_error_falls_back_with_the_same_result_and_warns_once", None),
             ("test_lossy_timestamp_columns_never_enter_the_fast_path", None),
             ("test_lossless_timestamp_dtypes_are_accepted_and_equal_the_old_lookup", None),
             ("test_lossy_or_non_numeric_timestamp_dtypes_are_refused", None),
             ("test_an_array_build_failure_is_cached_per_engine_and_warned_once", None),
             ("test_the_arrays_follow_the_index_and_are_rebuilt_after_add_series", None),
             ("test_config_switch_is_bool_only_default_on_and_not_part_of_the_decision_identity", None),
             ("test_the_index_builder_applies_the_switch_and_startup_logs_it", None),
             ("test_the_cli_pattern_engine_applies_the_switch", None),
             ("test_replay_on_the_repositorys_tour_fixture_indexes_is_bit_identical", None))


def mode_knn(tree, tmp):
    """#52: seçili testler; fikstürler: `real13` (modül kapsamı → bir kez kurulur, paylaşılır), `spy` (testin MonkeyPatch'iyle),
    `tmp_path`, `monkeypatch`, `caplog`; parametreli testlerin her durumu ayrı sonuç."""
    _tree(tree)
    import test_knn_fast_identity_v1 as T
    shared, out = {}, {}
    for i, (name, only) in enumerate(KNN_TESTS):
        fn = getattr(T, name)
        combos = [{}]
        params = getattr(fn, "_tb_params", [])
        if params:
            combos = []
            for pnames, values in params:
                keys = [k.strip() for k in pnames.split(",")]
                for v in values:
                    combos.append(dict(zip(keys, v if len(keys) > 1 else (v,))))
            if only:
                combos = [c for c in combos if all(c.get(k) == w for k, w in only.items())]
        for j, combo in enumerate(combos):
            tp = Path(tmp) / ("knn%02d_%02d" % (i, j))
            tp.mkdir(parents=True)
            mp = MonkeyPatch()

            def run(fn=fn, tp=tp, mp=mp, combo=combo):
                have = dict(combo, tmp_path=tp, monkeypatch=mp, caplog=Caplog())
                kw = {}
                for p in inspect.signature(fn).parameters:
                    if p in have:
                        kw[p] = have[p]
                    elif p == "real13":
                        if "real13" not in shared:
                            shared["real13"] = T.real13()
                        kw[p] = shared["real13"]
                    elif p == "spy":
                        kw[p] = T.spy(mp)
                    else:
                        raise RuntimeError("bilinmeyen fikstür %s" % p)
                fn(**kw)
            _call(out, name + (json.dumps(combo, sort_keys=True) if combo else ""), run)
            mp.undo()
    return out


def mode_hash(tree, cfgs):
    """#54: karar kimliği (`TradingEngineV3.config_hash`) AĞACIN kendi yükleyicisi (`load_yaml_strict` + `load_v3`) ve kendi
    kuralıyla, her config dosyası için. Servis ortamı yok (TRADINGBOT_* yok): iki ağaç aynı girdiyle karşılaştırılır."""
    import types
    _tree(tree)
    from tradingbot.config import load_yaml_strict
    from tradingbot.config_v3 import load_v3
    from tradingbot.engine_v3 import TradingEngineV3
    out = {}
    for c in cfgs:
        try:
            v3 = load_v3(load_yaml_strict(Path(c).read_text(encoding="utf-8")))
            out[Path(c).name] = TradingEngineV3.config_hash(types.SimpleNamespace(cfg=types.SimpleNamespace(v3=v3)))
        except Exception as exc:  # noqa: BLE001 — düşmüş sayılır
            out[Path(c).name] = "HATA %s: %s" % (type(exc).__name__, str(exc)[:200])
    return out


def main(argv):
    sys.dont_write_bytecode = True
    stand_ins()
    no_network()
    mode = argv[1]
    if mode == "run":
        runner = argv[3]
        sys.argv = [runner] + list(argv[4:])
        runpy.run_path(runner, run_name="__main__")
        return 0
    if mode == "books":
        res = mode_books(argv[2], argv[3], argv[4])
    elif mode == "evc":
        res = mode_evc(argv[2], argv[3])
    elif mode == "knn":
        res = mode_knn(argv[2], argv[3])
    elif mode == "hash":
        res = mode_hash(argv[2], argv[3:])
    else:
        res = mode_table(argv[2]) if mode == "table" else mode_research(argv[2], argv[3])
    print("RO_PROBE " + json.dumps(res, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''
_RO = {}


def _git(*args):
    import subprocess
    return subprocess.run(["git"] + list(args), capture_output=True, timeout=120, check=True).stdout


def _ro_tmp():
    """#46-#48'in ortak geçici klasörü (sürücü dosyası + fc63481 ağacı + koşu kökleri); çıkışta silinir."""
    if "tmp" not in _RO:
        import atexit
        import shutil
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="tb-deploy-ro-"))
        atexit.register(shutil.rmtree, str(d), True)
        (d / "ro_probe.py").write_text(RO_PROBE_SRC, encoding="utf-8")
        for sub in ("home", "tmp"):
            (d / sub).mkdir()
        _RO["tmp"] = d
    return _RO["tmp"]


def _probe(*args, timeout=900, svc_env=False, low=False):
    """Sürücüyü AYRI süreçte koşar. Varsayılan: servis ortamı AKTARILMAZ (TRADINGBOT_* yok; HOME/TMPDIR geçici; saat dilimi
    UTC). (2026-10-06) svc_env=True (#52): servis ortamı aktarılır, ama canlı yolları gösteren TRADINGBOT_* değişkenleri
    DÜŞÜLÜR ve HOME/TMPDIR geçicidir; low=True: nice 19 (çalışan worker'ın turuyla CPU için yarışmasın)."""
    import subprocess
    d = _ro_tmp()
    if svc_env:
        env = {k: v for k, v in os.environ.items() if not k.startswith("TRADINGBOT_")}
        env.update({"HOME": str(d / "home"), "TMPDIR": str(d / "tmp"), "MPLCONFIGDIR": str(d / "tmp"),
                    "PYTHONDONTWRITEBYTECODE": "1", "ALLOW_LIVE_TRADING": "false"})
    else:
        env = {"PATH": os.environ.get("PATH") or "/usr/bin:/bin", "HOME": str(d / "home"), "TMPDIR": str(d / "tmp"),
               "LANG": "C.UTF-8", "TZ": "UTC", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
               "MPLCONFIGDIR": str(d / "tmp"), "ALLOW_LIVE_TRADING": "false"}
    r = subprocess.run([sys.executable, str(d / "ro_probe.py")] + [str(a) for a in args], cwd=str(d), env=env,
                       capture_output=True, text=True, timeout=timeout, preexec_fn=(lambda: os.nice(19)) if low else None)
    return r.returncode, r.stdout, r.stderr


def _probe_json(*args, **kw):
    rc, out, err = _probe(*args, **kw)
    lines = [ln for ln in out.splitlines() if ln.startswith("RO_PROBE ")]
    if rc != 0 or not lines:
        raise RuntimeError("sürücü (%s) çıkış %s: %s" % (args[0], rc, (err or out).strip()[-400:]))
    return json.loads(lines[-1][len("RO_PROBE "):])


def _prereq_tree():
    """fc63481 ağacı (`git archive fc63481 tradingbot tests`; yalnız git nesneleri, çalışma ağacına dokunulmaz)."""
    if "pre" not in _RO:
        import io
        import tarfile
        dst = _ro_tmp() / "prereq"
        with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", PREREQ_SHA, "tradingbot", "tests"))) as tf:
            tf.extractall(dst, filter="data")
        _RO["pre"] = dst
    return _RO["pre"]


def config_delta():
    """config.yaml = gözden geçirilen hedef baytları (sha256) ve (1) 8db1faf'takinden (VPS'te çalışan; sha256 ile doğrulanır)
    YALNIZ eklenen KAPALI m2x_aggressive bölümüyle, (2) fc63481'dekinden (#46-#48'in karşılaştırma ağacı; sha256 ile
    doğrulanır) YALNIZ onaylanan iki değer — learning_mode.extra_entries (yok → record_selectivity),
    learning_mode.books.b1_box_fade.min_stop_pct (0,32 → 0,5) — ve aynı M2X bölümüyle ayrılır. Başka HİÇBİR veri farkı yok
    (yorumlar belge)."""
    import hashlib
    from tradingbot.config import load_yaml_strict
    tip_b = Path("config.yaml").read_bytes()
    t_sha = hashlib.sha256(tip_b).hexdigest()
    okk, notes = t_sha == TIP_CONFIG_SHA256, ["config.yaml sha256 %s… (beklenen %s…)" % (t_sha[:16], TIP_CONFIG_SHA256[:16])]
    for name, sha, want_sha, want in (("8db1faf", PREV_SHA, PREV_CONFIG_SHA256, PREV_CONFIG_DELTA),
                                      ("fc63481", PREREQ_SHA, PREREQ_CONFIG_SHA256, APPROVED_CONFIG_DELTA)):
        pre_b = _git("show", "%s:config.yaml" % sha)
        p_sha = hashlib.sha256(pre_b).hexdigest()
        delta = {}

        def walk(a, b, path, delta=delta):
            if isinstance(a, dict) and isinstance(b, dict):
                for k in sorted(set(a) | set(b), key=str):
                    walk(a.get(k, "<yok>") if k in a else "<yok>", b.get(k, "<yok>") if k in b else "<yok>",
                         path + (str(k),))
            elif not same(a, b):
                delta[path] = (a, b)
        walk(load_yaml_strict(pre_b.decode("utf-8")), load_yaml_strict(tip_b.decode("utf-8")), ())
        okk = okk and p_sha == want_sha and delta.keys() == want.keys() and all(same(delta[k], want[k]) for k in delta)
        notes.append("%s config %s… (beklenen %s…) · veri farkları: %s" % (name, p_sha[:16], want_sha[:16],
                                                                         {".".join(k): v for k, v in list(delta.items())[:6]}))
    if not okk:
        print("         " + " · ".join(notes))
    return okk


def ro_classification():
    """Sınıf tablosu ONAYLANAN tablo (48 kod + 4 önek; kapasite/Box istisnası YALNIZ tam eşleşme, bilinmeyen kod seçicilik)
    ve kip kapısı: `open` HİÇBİR kodu ayırmaz, `record_selectivity` yalnız seçicilik kodlu girişi ayırır. Deponun KENDİ tablo
    testi (tests/test_learning_record_only_extras.py: tablo, 14 sınıflama durumu, kod tabanının AST taraması — sınıfsız kod
    yok —, etiketi yalnız üç giriş yolu yazar, kip/config doğrulaması, karar günlüğü sınıfı, dönüşüm kuralı) dağıtılan
    ağaçta, ayrı süreçte geçer ve testteki tablo = onaylanan tablo."""
    import dataclasses
    from tradingbot import learning_mode as LMX
    sets = {"capacity": LMX.UNLOCK_CAPACITY, "box_exception": LMX.UNLOCK_BOX_EXCEPTION, "selectivity": LMX.UNLOCK_SELECTIVITY}
    got = {c: k for k, s in sets.items() for c in s}
    okk = (got == APPROVED_UNLOCK and sum(len(s) for s in sets.values()) == len(APPROVED_UNLOCK)
           and tuple(LMX.UNLOCK_SELECTIVITY_PREFIXES) == APPROVED_PREFIXES and LMX.LEARNING_RECORD_ONLY == RO
           and (LMX.EXTRA_OPEN, LMX.EXTRA_RECORD_SELECTIVITY, LMX.DEFAULT_EXTRA_ENTRIES)
           == ("open", "record_selectivity", "open"))
    odd = [p + ":X" for p in APPROVED_PREFIXES] + ["STRUCTURE_WAIT_TRIGGER", "TOTAL_OPEN_RISK:x", "BOX_MIN_STOP_PCT_X",
                                                    "TB_DEPLOY_UNKNOWN_CODE", ""]
    okk = okk and all(LMX.classify_unlock_code(c) == "selectivity" for c in odd)
    view = LMX.LearningMode.from_config(v3, lambda: (True, "OK"))
    view.refresh()
    b_rec = view.book("main")
    b_open = dataclasses.replace(b_rec, extra_entries=LMX.EXTRA_OPEN)
    gate = []
    for c in list(APPROVED_UNLOCK) + odd[:-1]:
        sel = LMX.classify_unlock_code(c) == "selectivity"
        d = LMX.record_only([c], b_rec)
        gate.append(LMX.record_only([c], b_open) is None and ((d is not None and d["codes"] == [c]) if sel else d is None))
    okk = okk and b_rec.extra_entries == LMX.EXTRA_RECORD_SELECTIVITY and all(gate) \
        and LMX.record_only([], b_rec) is None and LMX.record_only(["TOTAL_OPEN_RISK", "BOX_MIN_STOP_PCT"], b_rec) is None \
        and (LMX.record_only(["TOTAL_OPEN_RISK", "BOOK_UNIVERSE"], b_rec) or {}).get("selectivity_codes") == ["BOOK_UNIVERSE"]
    res = _probe_json("table", Path.cwd())
    r = res.get("results") or {}
    bad = sorted(k for k, v in r.items() if v != "ok")
    okk = okk and len(r) == 22 and not bad and res.get("table") == APPROVED_UNLOCK \
        and tuple(res.get("prefixes") or ()) == APPROVED_PREFIXES
    if not okk:
        print("         tablo = onaylanan: %s · kip kapısı: %s · depo tablo testi %d/%d geçti%s · testteki tablo = onaylanan: %s"
              % (got == APPROVED_UNLOCK, all(gate), len(r) - len(bad), len(r),
                 (" (düşen: %s)" % ["%s → %s" % (k, r[k][:120]) for k in bad[:2]]) if bad else "",
                 res.get("table") == APPROVED_UNLOCK))
    return okk


_STALE = re.compile(rb'("[A-Za-z0-9_]*staleness[A-Za-z0-9_]*": )-?[0-9.eE+-]+')
_ISO = re.compile(rb"20\d\d-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:[+-]\d\d:\d\d|Z)?")
_EPOCH = re.compile(rb"(?<![\w.])1\d{9}(?:\d{3})?(?:\.\d+)?(?![\w])")


def _masked_files(root, lo, hi):
    """Koşu klasöründeki her dosyanın sha256'sı — kök yolu <KÖK>, koşuların duvar saatinden türeyen damgalar (ISO / epoch s-ms,
    [lo, hi] = koşular ± 6 sa içinde) <SAAT> ve "…staleness…" oranları <S> ile maskelenmiş (saat donmadığı için; günler
    önceki fikstür damgaları ve bütün karar alanları AYNEN karşılaştırılır)."""
    import datetime as _dtx
    import hashlib

    def iso(m):
        try:
            t = _dtx.datetime.fromisoformat(m.group(0).decode().replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            return m.group(0)
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dtx.timezone.utc)
        return b"<SAAT>" if lo <= t.timestamp() <= hi else m.group(0)

    def ep(m):
        v = float(m.group(0))
        v = v / 1000.0 if v > 1e11 else v
        return b"<SAAT>" if lo <= v <= hi else m.group(0)
    rb = str(root).encode("utf-8")
    out = {}
    for q in sorted(Path(root).rglob("*")):
        if q.is_file():
            b = q.read_bytes().replace(rb, "<KÖK>".encode("utf-8"))
            out[q.relative_to(root).as_posix()] = hashlib.sha256(_STALE.sub(rb"\1<S>", _EPOCH.sub(ep, _ISO.sub(iso, b))))\
                .hexdigest()
    return out


def ro_open_equals_prereq():
    """KILL SWITCH + "nothing more": deponun kendi koşucuları (tests/record_only_direct_runner.py ve
    record_only_tour_runner.py; ağsız, deterministik kimlikler) AYRI süreçte, bu ağaçla ve `git archive fc63481` ağacıyla:
    (1) `extra_entries: open` = fc63481 — doğrudan yollar (D4/T2/Box defterleri, Formasyon, ana botun seçicilik/kapasite/
    politika/kill switch adayları): karar özeti AYNI ve her dosya bayt bayt AYNI (koşu anının saat damgaları maskeli); beş
    gerçek tur (TradingEngineV3.tour, ortak deneyim RECORD) `main_gates` ve `books` senaryolarında: bütün defterlerin karar
    özeti ve xp satırları AYNI, dosya listesi AYNI. (2) `record_selectivity` YALNIZ seçicilik-ekstrayı değiştirir: doğrudan
    yollarda yalnız 7 seçicilik senaryosu değişir (o girişler AÇILMAZ, her biri LEARNING_RECORD_ONLY kaydı; aynı senaryodaki
    politika/kapasite girişleri yine açık; kill switch kaydı yalnız-kayda DÖNÜŞÜR), kapasite/politika/Box istisnası AYNI; turlarda
    `main_gates`ta açılan REGIME_VETO işlemleri kayda döner, öbür defterler aynı; `books` (seçicilik yok) AYNI. (3) HER
    açık strateji defteri (T2, M2, Box, D4, C4; `_ro_books`) ORTAK açılış yerinde: seçicilik kodu (defter evreni, yapı,
    bilinmeyen kod, kapasite + seçicilik) → AÇILMAZ, tek LEARNING_RECORD_ONLY kaydı; kapasite kodu, Box istisnası ve politika
    girişi → `open` ile AYNI; fikstür görünümüyle `open` = fc63481.
    Saat DONMAZ (VPS'te time_machine yok; koşular gerçek saatte): saatten türeyen alanlar (hour_sin/hour_cos, haftanın günü,
    hafta başı) iki koşu arasında bir saat sınırı geçilirse farklılaşır → fark görülürse BÜTÜN koşular bir kez daha (taze
    klasörlerde) koşulur ve yalnız ikinci koşuda da fark varsa düşer (kod farkı deterministiktir, iki koşuda da görünür)."""
    books = _ro_books()                      # (3) deterministik (saat damgası karşılaştırılmaz): bir kez koşar
    for w in books[:6]:
        print("         defter başına (ortak açılış yeri): " + w[:280])
    why = _ro_compare("a")
    if not why:
        return not books
    first = why[0]
    why = _ro_compare("b")
    if not why:
        print("         (ilk koşuda geçici fark — %s — yeniden koşuda AYNI: saat sınırı/zamanlama; geçti)" % first[:150])
        return not books
    for w in why[:6]:
        print("         " + w[:300])
    print("         (iki ayrı koşuda da fark: saat sınırı değil — bu çıktıyı bana iletin)")
    return False


def _ro_books():
    """(3) her AÇIK strateji defteri görünümü (dağıtılan config.yaml: kip record_selectivity) ve aynı görünümün `open`
    kopyası, deponun aile senaryosunda (tabanın da açtığı sinyal) ORTAK açılış yerine (`_open_learning` → `record_only`)
    sürülür; taban kapılarının GERÇEK sonucuna `BOOK_VARIANT_CODES` eklenir. Beklenen sınıf ONAYLANAN tablodan: seçicilik
    (kapasite / Box istisnası olmayan herhangi bir kod) → record_selectivity AÇMAZ, nedeni LEARNING_RECORD_ONLY + bütün kodlar
    olan TEK kayıt (defter adıyla), sayaç 1, ret 1; aksi halde record_selectivity = open (pozisyon, etiketler, öğrenme boyutu,
    kayıtlar, retler AYNI). `open` her değişkede açar ve etiketi = eklenen kodlar. Kill switch iki kipte de durdurur (AYNI).
    Fikstür görünümüyle (`open`) bütün sonuçlar `git archive fc63481` ağacınınkiyle AYNI. Döner: farklar (boş = AYNI)."""
    why = []
    cfg = _probe_json("books", Path.cwd(), _ro_tmp() / "books_cfg", "config")
    fx = _probe_json("books", Path.cwd(), _ro_tmp() / "books_fx", "fixture")
    pre = _probe_json("books", _prereq_tree(), _ro_tmp() / "books_pre", "fixture")
    if cfg.get("views") != {n: "record_selectivity" for n in BOOK_VIEWS}:
        why.append("açık strateji defteri görünümleri %s (beklenen: %s record_selectivity)" % (cfg.get("views"), list(BOOK_VIEWS)))

    def brief(x):
        return "açıldı=%s etiket=%s neden=%s kayıt=%s sayaç=%s" % (x.get("opened"), x.get("tags"), x.get("reason"),
                                                                 x.get("ro"), x.get("counter"))
    for name in BOOK_VIEWS:
        r = (cfg.get("res") or {}).get(name) or {}
        if set(r) != set(BOOK_VARIANT_CODES):
            why.append("%s: değişkeler %s" % (name, sorted(r)))
            continue
        for var, codes in BOOK_VARIANT_CODES.items():
            o, c = r[var].get("open") or {}, r[var].get("cfg") or {}
            if codes is None:
                okk = o.get("opened") is False and c == o
            elif any(APPROVED_UNLOCK.get(x) not in ("capacity", "box_exception") for x in codes):
                okk = o.get("opened") is True and o.get("tags") == codes and c.get("opened") is False \
                    and c.get("positions") == [] and c.get("reason") == RO and (c.get("rejections") or {}).get(RO) == 1 \
                    and c.get("counter") == 1 and c.get("ro") == [[[RO] + codes, name]]
            else:
                okk = o.get("opened") is True and o.get("tags") == codes and c == o
            if not okk:
                why.append("%s/%s: open %s · record_selectivity %s" % (name, var, brief(o), brief(c)))
    if fx.get("res") != pre.get("res") or sorted(fx.get("res") or {}) != sorted(BOOK_VIEWS):
        why.append("defter başına open ≠ fc63481: %s" % sorted("%s/%s" % (n, v) for n in fx.get("res") or {}
                                                          for v in (fx["res"][n] or {})
                                                          if (pre.get("res") or {}).get(n, {}).get(v) != fx["res"][n][v])[:6])
    return why


def _ro_compare(sfx):
    """ro_open_equals_prereq'in bir koşusu (bütün plan, `sfx` ekli taze klasörlerde); farkların listesi (boş = AYNI)."""
    import time as _tm
    here = Path.cwd()
    pre = _prereq_tree()
    tmp = _ro_tmp()
    t0 = _tm.time()
    runs = {}
    D, TR = here / "tests" / "record_only_direct_runner.py", here / "tests" / "record_only_tour_runner.py"
    plan = [("d_open", here, D, "open", None), ("d_pre", pre, D, "absent", None), ("d_rec", here, D, "record_selectivity", None)]
    for sc in ("main_gates", "books"):
        plan += [("t_open_" + sc, here, TR, "open", sc), ("t_pre_" + sc, pre, TR, "absent", sc),
                 ("t_rec_" + sc, here, TR, "record_selectivity", sc)]
    for tag, tree, runner, extra, sc in plan:
        root, out = tmp / ("r_%s_%s" % (sfx, tag)), tmp / ("%s_%s.json" % (sfx, tag))
        rc, so, se = _probe("run", tree, runner, "--tree", tree, "--root", root, "--extra", extra, "--out", out,
                            *(["--scenario", sc] if sc else []))
        if rc != 0 or not out.exists():
            return ["koşucu %s düştü (çıkış %s): %s" % (tag, rc, (se or so).strip()[-300:])]
        runs[tag] = (json.loads(out.read_text(encoding="utf-8")), root)
    t1 = _tm.time()
    why = []
    # (1) open = fc63481
    fo, fp = runs["d_open"][0]["facts"], runs["d_pre"][0]["facts"]
    win = (t0 - 6 * 3600, t1 + 6 * 3600)        # koşuların duvar saatinden türeyen damgalar (4h taban dahil); fikstür günleri dışarıda
    mo, mp_ = _masked_files(runs["d_open"][1], *win), _masked_files(runs["d_pre"][1], *win)
    dd = sorted(set(mo) ^ set(mp_)) + [k for k in sorted(set(mo) & set(mp_)) if mo[k] != mp_[k]]
    if fo != fp:
        why.append("doğrudan yollar open ≠ fc63481 karar özeti: %s" % [k for k in fo if fo.get(k) != fp.get(k)][:4])
    if dd or len(mo) < 150:
        why.append("doğrudan yollar open ≠ fc63481 dosyalar (%d dosya; farklı %d: %s)" % (len(mo), len(dd), dd[:3]))
    for sc in ("main_gates", "books"):
        a, b = runs["t_open_" + sc][0], runs["t_pre_" + sc][0]
        if a["facts"] != b["facts"] or set(a["files"]) != set(b["files"]) or len(a["files"]) < 50:
            why.append("turlar (%s) open ≠ fc63481: %s" % (sc, [k for k in a["facts"] if a["facts"].get(k) != b["facts"].get(k)][:4]
                                                         or sorted(set(a["files"]) ^ set(b["files"]))[:3]))
    # (2) record_selectivity yalnız seçicilik-ekstrayı değiştirir
    from tradingbot import learning_mode as LMX
    rec = runs["d_rec"][0]["facts"]
    if set(rec) != set(fo) or not SELECTIVITY_DIRECT < set(fo):
        why.append("doğrudan senaryo kümesi beklenmeyen: %s" % sorted(set(rec) ^ set(fo))[:4])
    else:
        for name in sorted(set(fo) - SELECTIVITY_DIRECT):
            if rec[name] != fo[name]:
                why.append("record_selectivity seçicilik dışı senaryoyu değiştirdi: %s" % name)
        for name in sorted(SELECTIVITY_DIRECT):
            was = sorted(s for s, t in fo[name]["open"] if t and LMX.classify_unlock_codes(t) == "selectivity")
            now_open = sorted(s for s, _t in rec[name]["open"])
            kept = sorted(s for s, t in fo[name]["open"] if not (t and LMX.classify_unlock_codes(t) == "selectivity"))
            ro_cf = sorted(s for s, r in rec[name]["cf"] if r and r[0] == RO)
            if not was or set(was) & set(now_open) or ro_cf != was or now_open != kept:
                why.append("%s: seçicilik açılanlar %s · şimdi açık %s · yalnız-kayıt %s" % (name, was, now_open, ro_cf))
        if rec["d4_killswitch"]["cf"] != [["SOL/USDT", [RO, "BOOK_UNIVERSE", "KILL_SWITCH_ACTIVE"]]] \
                or not rec["main_killswitch"]["cf"] \
                or any(r != [RO, "NEGATIVE_NET_EDGE", "KILL_SWITCH_ACTIVE"] for _s, r in rec["main_killswitch"]["cf"]):
            why.append("kill switch kaydı yalnız-kayda dönüşmedi: %s · %s" % (rec["d4_killswitch"]["cf"],
                                                                         rec["main_killswitch"]["cf"][:2]))
    ro_, op = runs["t_rec_main_gates"][0]["facts"], runs["t_open_main_gates"][0]["facts"]
    opened = sorted(s for s, _t in op["main"]["open"] + op["main"]["closed"])
    recorded = sorted(s for s, r in ro_["main"]["cf"] if r and r[0] == RO)
    if not (len(opened) >= 2 and all(any(c.startswith("REGIME_VETO:") for c in t) for _s, t in op["main"]["open"] + op["main"]["closed"])):
        why.append("main_gates senaryosu anlamsız (open'da REGIME_VETO işlemi yok): %s" % opened)
    if ro_["main"]["open"] or ro_["main"]["closed"] or recorded != opened \
            or any(not (r and r[0] == RO and any(str(c).startswith("REGIME_VETO:") for c in r[1:])) for _s, r in ro_["main"]["cf"]) \
            or {k: v for k, v in ro_.items() if k not in ("main", "xp")} != {k: v for k, v in op.items() if k not in ("main", "xp")} \
            or sorted([s, r] for _b, s, r in (ro_.get("xp") or {}).get("cf", []) if r and r[0] == RO) != sorted(ro_["main"]["cf"]):
        why.append("turlar (main_gates) record_selectivity: açık %s · kayıt %s (beklenen %s)"
                   % (ro_["main"]["open"] + ro_["main"]["closed"], recorded, opened))
    if runs["t_rec_books"][0]["facts"] != runs["t_open_books"][0]["facts"]:
        why.append("turlar (books, seçicilik yok) record_selectivity ≠ open")
    return why


def ro_research_excluded():
    """Yalnız-kayıt (ilk neden LEARNING_RECORD_ONLY) kayıtları araştırma politikasına (BLOCKED gözlemi) ve deneyim havuzuna
    GİRMEZ: deponun kendi testi (record_selectivity motoru, `_lm_label_main_cf`) ve kill switch sonrası (`open`) kayıt
    döneminden kalan bekleyen kayıt — motorun tam etiketleme yolu (`_label_shadows`) — ayrı süreçte; normal ana
    karşı-olgusal eskisi gibi gözlenir."""
    res = _probe_json("research", Path.cwd(), _ro_tmp() / "research")
    r = res.get("results") or {}
    okk = len(r) == 2 and all(v == "ok" for v in r.values())
    if not okk:
        print("         %s" % {k: v[:200] for k, v in r.items()})
    return okk


rp = raw.get("risk") or {}

# --- BU SÜRÜM (2026-10-05; hedef TIP, ön koşul f8b05fb): KARAR DEĞİŞTİRMEYEN tur düzeltmesi — pattern kanıtı sorguları
# fork ile ayrılan TEK bir alt süreçte (`history.evidence_subprocess`, kod varsayılanı AÇIK; config.yaml'da anahtar YOK).
# #49 deponun karar-nötrlük testleri dağıtılan ağaçta (AYRI süreçte, sürücünün `evc` kipi), #50 varsayılan AÇIK + kill
# switch satırı bugünkü süreç içi yola döndürür ve config'in başka HİÇBİR değerini değiştirmez, #51 alt süreç anahtarı
# karar kimliğine girmez ve --check'in aradığı log biçimleri kodda.
#: kill switch: config.yaml'daki `history:` satırının ALTINA `  evidence_subprocess: false` (betik başlığındaki sed ile AYNI)
EVC_KILL_RE = (r"(?m)^history:$", "history:\n  evidence_subprocess: false")
EVC_TESTS_N = 7
#: --check'in alt süreç arıza satırı önekleri (evc_report'taki ERR ile AYNI; #51 her alt süreç uyarısının birine uyduğunu sınar)
EVC_ERR_PREFIXES = ("pattern kanıtı alt süreci arızalandı", "pattern kanıtı alt süreci başlatılamadı",
                    "pattern kanıtı alt süreci başlatılmadı", "pattern kanıtı alt süreci bellek koruması nedeniyle kapatıldı",
                    "pattern kanıtı alt süreci KAPALI", "pattern kanıtı alt süreçte hesaplanamadı",
                    "pattern kanıtı alt süreci bu işte", "pattern kanıtı alt süreci bu makinede KAPALI")


def evc_child_neutral():
    """Deponun tests/test_evidence_subprocess_v1.py'sinden 7 karar-nötrlük testi dağıtılan ağaçta (AYRI süreç, servis ortamı
    aktarılmadan): alt süreç kanıtı süreç içiyle bit-bit aynı ve dosyalar aynı; yayım anı / sürüm görünürlüğü aynı (yayım
    alt süreci beklemez); daha yeni yayım eski sürümün alt sürecini kapatır ve eski kanıt hiç verilmez; her yerde hata →
    bugünkü sonuç (None); kill switch → fork yok, bugünkü yol; varsayılan AÇIK; anahtar config_hash'e girmez."""
    d = _ro_tmp() / "evc"
    d.mkdir(exist_ok=True)
    res = _probe_json("evc", Path.cwd().resolve(), d)
    bad = {k: v for k, v in res.items() if v != "ok"}
    if bad or len(res) != EVC_TESTS_N:
        print("         kanıt alt süreci testleri: %d/%d — %s" % (len(res) - len(bad), EVC_TESTS_N,
                                                               {k[5:60]: v[:160] for k, v in list(bad.items())[:3]}))
    return not bad and len(res) == EVC_TESTS_N


#: (2026-10-06) alt süreci AÇAN satır (anahtar çalışıyor mu; #50, #54)
EVC_ON_RE = (r"(?m)^history:$", "history:\n  evidence_subprocess: true")


def evc_switch():
    """#50 (2026-10-06): config.yaml'da `evidence_subprocess` YOK; kod varsayılanı KAPALI (yüklenen False, motor kuralı süreç
    içi); açık `true` satırı → True ve motor kuralı alt süreç (Linux), config'in BAŞKA hiçbir değeri değişmez; VPS'teki dağıtım
    öncesi kill switch satırı (`evidence_subprocess: false`, 8db1faf başlığındaki sed) uygulanmış config = varsayılan config
    (bütün değerler AYNI); tırnaklı "true" ConfigError (yanlış yazım sessizce açmaz)."""
    import dataclasses
    import types
    from tradingbot.config import ConfigError, load_config
    from tradingbot.engine_v3 import TradingEngineV3 as _E
    from tradingbot.patterns import evidence_child as EC
    src = Path("config.yaml").read_text(encoding="utf-8")
    dflt = load_config(Path("config.yaml"))
    on_txt, n_on = re.subn(EVC_ON_RE[0], EVC_ON_RE[1], src)
    ks_txt, n_ks = re.subn(EVC_KILL_RE[0], EVC_KILL_RE[1], src)
    p_on, p_ks = _ro_tmp() / "config.child-on.yaml", _ro_tmp() / "config.child-off.yaml"
    p_on.write_text(on_txt, encoding="utf-8")
    p_ks.write_text(ks_txt, encoding="utf-8")
    on, ks = load_config(p_on), load_config(p_ks)
    rule = (lambda c: _E._evidence_subprocess_on(types.SimpleNamespace(cfg=c)))
    a, b, c = dataclasses.asdict(dflt), dataclasses.asdict(on), dataclasses.asdict(ks)
    for x in (a, b, c):
        x.pop("project_root", None)                                   # config dosyasının klasörü (geçici kopya)
    same_ks = same(a, c)
    b["v3"]["history"].pop("evidence_subprocess")
    a2 = json.loads(json.dumps(a, default=str))
    a2["v3"]["history"].pop("evidence_subprocess")
    q = _ro_tmp() / "config.child-quoted.yaml"
    q.write_text(re.sub(EVC_ON_RE[0], 'history:\n  evidence_subprocess: "true"', src, count=1), encoding="utf-8")
    try:
        load_config(q)
        quoted = False
    except ConfigError:
        quoted = True
    okk = ("evidence_subprocess" not in (raw.get("history") or {}) and dflt.v3.history.evidence_subprocess is False
           and rule(dflt) is False and n_on == n_ks == 1 and on.v3.history.evidence_subprocess is True
           and rule(on) is EC.supported() and ks.v3.history.evidence_subprocess is False and rule(ks) is False and same_ks
           and same(json.loads(json.dumps(b, default=str)), a2) and quoted)
    if not okk:
        print("         config anahtarı %s · varsayılan %r/%r · true → %r/%r · false satırı → %r/%r, varsayılanla aynı %s · "
              "tırnaklı ret %s" % ("VAR" if "evidence_subprocess" in (raw.get("history") or {}) else "yok",
                                   dflt.v3.history.evidence_subprocess, rule(dflt), on.v3.history.evidence_subprocess, rule(on),
                                   ks.v3.history.evidence_subprocess, rule(ks), same_ks, quoted))
    return okk


# --- BU SÜRÜM (2026-10-06; hedef TIP, ön koşul 8db1faf): KARAR DEĞİŞTİRMEYEN tur düzeltmesi V2 — HIZLI ve KESİN kNN kanıt
# sorgusu (`history.evidence_fast_knn`, kod varsayılanı AÇIK; config.yaml'da anahtar YOK). #52 deponun bit-aynılık testleri
# dağıtılan ağaçta (AYRI süreç, servis ortamıyla), #53 anahtar + kill switch, #54 config_hash 8db1faf ile aynı, #55 M2X KAPALI,
# #56 --check'in aradığı hızlı kNN log biçimleri.
#: kill switch: config.yaml'daki `history:` satırının ALTINA `  evidence_fast_knn: false` (betik başlığındaki sed ile AYNI)
KNN_KILL_RE = (r"(?m)^history:$", "history:\n  evidence_fast_knn: false")
KNN_TESTS_N = 29
#: --check'in ön ısıtma satırı süzgeci (knn_report KNN_PREWARM_RE ve PRE ile AYNI)
KNN_PREWARM_RE = r"pattern kanıtı ön ısıtıldı: sürüm [0-9]+, [0-9]+ sembol hesaplandı, [0-9]+ zaten hazırdı, [0-9.]+ sn"
#: --check'in yayım satırı süzgeci (knn_report PUB ile AYNI)
KNN_PUB_RE = r"pattern indeksi yenilendi: sürüm ([0-9]+), ([0-9]+) olay, ([0-9]+) seri, kurulum ([0-9.]+) sn"
PREV_SHA = "8db1faf08661ad10fd76ebe7d8084fed1429a7de"                                    # VPS'te çalışan (geri alma hedefi)


def knn_identity():
    """#52: deponun tests/test_knn_fast_identity_v1.py'sinden 19 test (29 durum) dağıtılan ağaçta — AYRI süreç, SERVİS
    ORTAMIYLA (canlı yolları gösteren TRADINGBOT_* değişkenleri hariç; HOME/TMPDIR geçici; soket düzeyinde ağ kapalı), nice 19:
    üretim çağrısı (13 sembol × 2 yön), rastgele sorgular (bütün seviyeler, pencereler, k, tarihsel idx, embargo, min_sep),
    çekişmeli indeks (eşit mesafeler, aynı sembol spot/futures/1h, NaN sütunlar), düz yol yedeği, embargo/çıkış sınırları,
    aday listesinin tükenmesi, alt sınırın her adayda geçerliliği, sıkı alt sınırla aynı seçim, kill switch, arızada yedek +
    tek uyarı, kayıplı zaman damgası (denetçinin yeniden üretimi) ve dtype tabloları, dizi kurulum arızası motor başına bir
    kez, dizilerin yeniden kurulması, config anahtarı + karar kimliği, kurucuda atama + başlangıç satırı, CLI kanıt motoru,
    depodaki tur indekslerinde yeniden oynatma. Her karşılaştırmada sözlük `==`, JSON ve seçilen listenin TAMAMI (mesafeler
    bit bit) aynı."""
    d = _ro_tmp() / "knn"
    d.mkdir(exist_ok=True)
    res = _probe_json("knn", Path.cwd().resolve(), d, timeout=1800, svc_env=True, low=True)
    bad = {k: v for k, v in res.items() if v != "ok"}
    if bad or len(res) != KNN_TESTS_N:
        print("         hızlı kNN testleri: %d/%d — %s" % (len(res) - len(bad), KNN_TESTS_N,
                                                         {k[5:70]: v[:160] for k, v in list(bad.items())[:3]}))
    return not bad and len(res) == KNN_TESTS_N


def knn_switch():
    """#53: config.yaml'da `evidence_fast_knn` YOK, yüklenen değer True ve motor kuralı (`_evidence_fast_knn_on`) hızlı yol;
    başlıktaki kill switch satırı uygulanmış config → False, kural eski döngü ve config'in BAŞKA hiçbir değeri değişmez (v3 +
    üst düzey bölümler); anahtar kanıt motorunu kuran İKİ yerde de atanır (worker `_build_pattern_index`, CLI
    `_pattern_engine`); tırnaklı "false" ConfigError ve satırın İKİ KEZ uygulanması ConfigError (yinelenen anahtar — başlıktaki
    'YALNIZ BİR KEZ' uyarısının nedeni: worker başlamaz)."""
    import dataclasses
    import types
    from tradingbot.config import ConfigError, load_config
    from tradingbot.engine_v3 import TradingEngineV3 as _E
    src = Path("config.yaml").read_text(encoding="utf-8")
    on = load_config(Path("config.yaml"))
    ks_txt, n = re.subn(KNN_KILL_RE[0], KNN_KILL_RE[1], src)
    p = _ro_tmp() / "config.knn-killswitch.yaml"
    p.write_text(ks_txt, encoding="utf-8")
    off = load_config(p)
    rule = (lambda c: _E._evidence_fast_knn_on(types.SimpleNamespace(cfg=c)))
    a, b = dataclasses.asdict(on), dataclasses.asdict(off)
    a["v3"]["history"].pop("evidence_fast_knn"), b["v3"]["history"].pop("evidence_fast_knn")
    a.pop("project_root", None), b.pop("project_root", None)

    def refused(txt, name):
        q = _ro_tmp() / name
        q.write_text(txt, encoding="utf-8")
        try:
            load_config(q)
        except ConfigError:
            return True
        return False
    quoted = refused(re.sub(KNN_KILL_RE[0], 'history:\n  evidence_fast_knn: "false"', src, count=1), "config.knn-quoted.yaml")
    twice = refused(re.sub(KNN_KILL_RE[0], KNN_KILL_RE[1], ks_txt), "config.knn-twice.yaml")
    eng = Path("tradingbot/engine_v3.py").read_text(encoding="utf-8")
    cli = Path("tradingbot/cli_v3.py").read_text(encoding="utf-8")
    wired = (eng.count("eng.fast_query = self._evidence_fast_knn_on()") == 1
             and cli.count('eng.fast_query = getattr(cfg.v3.history, "evidence_fast_knn", True) is not False') == 1)
    okk = ("evidence_fast_knn" not in (raw.get("history") or {}) and on.v3.history.evidence_fast_knn is True
           and rule(on) is True and n == 1 and off.v3.history.evidence_fast_knn is False and rule(off) is False
           and same(a, b) and quoted and twice and wired)
    if not okk:
        print("         config anahtarı %s · yüklenen %r · kural %r · kill switch eşleşme %d → %r/%r · başka fark %s · tırnaklı "
              "ret %s · iki kez ret %s · kurucularda atama %s"
              % ("VAR" if "evidence_fast_knn" in (raw.get("history") or {}) else "yok", on.v3.history.evidence_fast_knn,
                 rule(on), n, off.v3.history.evidence_fast_knn, rule(off), not same(a, b), quoted, twice, wired))
    return okk


def _prev_tree():
    """8db1faf ağacı (`git archive 8db1faf tradingbot`; yalnız git nesneleri, çalışma ağacına dokunulmaz)."""
    if "prev" not in _RO:
        import io
        import tarfile
        dst = _ro_tmp() / "prev"
        with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", PREV_SHA, "tradingbot"))) as tf:
            tf.extractall(dst, filter="data")
        _RO["prev"] = dst
    return _RO["prev"]


def hash_vs_prev():
    """#54: karar kimliği (config_hash; karar günlüğü ve provenans satırlarına yazılır) DEĞİŞMEDİ — AYRI süreçlerde (servis
    ortamı aktarılmadan) 8db1faf KODUYLA (`git archive 8db1faf`) 8db1faf'ın config.yaml'ı ve VPS'teki hali (kill switch satırı
    `evidence_subprocess: false`) ile bu sürümün KODUYLA aynı ikisi + bu sürümün config.yaml'ı (kapalı M2X bölümüyle), hızlı
    kNN kill switch'iyle, iki satır birlikte ve alt süreç açık (`true`) iken: hepsi AYNI özet."""
    d = _ro_tmp() / "hash"
    d.mkdir(exist_ok=True)
    prev_cfg = _git("show", "%s:config.yaml" % PREV_SHA).decode("utf-8")
    tip_cfg = Path("config.yaml").read_text(encoding="utf-8")
    files = {"prev.yaml": prev_cfg, "prev_ks.yaml": re.sub(EVC_KILL_RE[0], EVC_KILL_RE[1], prev_cfg, count=1),
             "tip.yaml": tip_cfg, "tip_knn_off.yaml": re.sub(KNN_KILL_RE[0], KNN_KILL_RE[1], tip_cfg, count=1),
             "tip_both.yaml": re.sub(KNN_KILL_RE[0], KNN_KILL_RE[1],
                                     re.sub(EVC_KILL_RE[0], EVC_KILL_RE[1], tip_cfg, count=1), count=1),
             "tip_child_on.yaml": re.sub(EVC_ON_RE[0], EVC_ON_RE[1], tip_cfg, count=1)}
    for k, v in files.items():
        (d / k).write_text(v, encoding="utf-8")
    old = _probe_json("hash", _prev_tree(), d / "prev.yaml", d / "prev_ks.yaml")
    new = _probe_json("hash", Path.cwd().resolve(), *[d / k for k in files])
    vals = list(old.values()) + list(new.values())
    okk = (len(old) == 2 and len(new) == len(files) and len(set(vals)) == 1
           and all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{16,}", v) for v in vals))
    if not okk:
        print("         8db1faf kodu: %s · bu kod: %s" % (old, new))
    return okk


def m2x_off():
    """#55: bu dağıtımla worker'a ilk kez gelen M2X ayna defteri KAPALI: config.yaml bölümü birebir gözden geçirilen değerler
    (enabled: false), yüklenen değer (servis ortamıyla; TRADINGBOT_M2X yalnız kapatabilir) enabled False; motor defteri
    YALNIZ `enabled` doğruyken kurar (tek kurulum yeri, kapalıyken `m2x_book = None`); M2X strateji defteri listesinde
    (`book_specs`) yok; bölüm config_hash'e girmez (#54)."""
    mx = getattr(v3, "m2x_aggressive", None)
    eng = Path("tradingbot/engine_v3.py").read_text(encoding="utf-8")
    gate = ('        self.m2x_book = None\n        _mx = getattr(v3, "m2x_aggressive", None)\n'
            '        if _mx is not None and bool(getattr(_mx, "enabled", False)):\n')
    okk = (mx is not None and getattr(mx, "enabled", None) is False and same(raw.get("m2x_aggressive"), M2X_CFG)
           and eng.count(gate) == 1 and eng.count("M2xBook(") == 1 and not any("m2x" in str(n).lower() for n in books))
    if not okk:
        print("         M2X yüklenen %r · config bölümü %r · kurulum kapısı %d · defterler %s"
              % (getattr(mx, "enabled", None), raw.get("m2x_aggressive"), eng.count(gate), sorted(books)))
    return okk


def knn_log_formats():
    """#56: --check'in "PATTERN KANITI HIZLI kNN" bölümünün aradığı satırlar kodda ve biçimleri eşleşiyor: başlangıç satırı
    ("pattern kanıtı kNN sorgusu: HIZLI yol (…" / "… ESKİ olay-başına döngü (…") motor kurulunca VE yenileyici başlarken
    loglanır; hızlı yol uyarısı "pattern kNN hızlı yolu %s; eski döngü kullanılıyor …"; yayım satırı (KNN_PUB_RE) ve
    ön ısıtma satırının İKİ biçimi
    (süreç içi ve alt süreç eki) gerçek değerlerle doldurulunca KNN_PREWARM_RE ile eşleşir; tur fazı satırı (gerçek
    TourPhases) "coin head Xs (pattern kanıtı Ys)" ve sonda "toplam Zs" içerir; pattern kanıtı alt fazı coin head'in içinde."""
    from tradingbot.ops import tour_phases as TP
    eng = Path("tradingbot/engine_v3.py").read_text(encoding="utf-8")
    pe = Path("tradingbot/patterns/engine.py").read_text(encoding="utf-8")
    need = ('"pattern kanıtı kNN sorgusu: HIZLI yol (history.evidence_fast_knn=%s; sonuç eski döngüyle "',
            '"pattern kanıtı kNN sorgusu: ESKİ olay-başına döngü (history.evidence_fast_knn=%s)"')
    miss = [s for s in need if s not in eng]
    if eng.count("self._log_evidence_fast_knn_setting()") != 2:
        miss.append("başlangıç satırının iki çağrı yeri")
    if 'log.warning("pattern kNN hızlı yolu %s; eski döngü kullanılıyor' not in pe:
        miss.append("hızlı yol uyarısı")
    rx = re.compile(KNN_PREWARM_RE)
    fmts = [f for f in _log_consts("tradingbot/patterns/evidence_cache.py").get("info", [])
            if f.startswith("pattern kanıtı ön ısıtıldı: ")]
    filled = [f % ((812, 13, 0, 5.3) if f.count("%") == 4 else (812, 13, 0, 5.3, "alt süreç pid 1, özel bellek 1 MB"))
              for f in fmts if f.count("%") in (4, 5)]
    pre_ok = len(filled) == len(fmts) == 2 and all(rx.match(x) for x in filled)
    pubs = [f for f in _log_consts("tradingbot/patterns/refresher.py").get("info", [])
            if f.startswith("pattern indeksi yenilendi")]
    pub_ok = len(pubs) == 1 and re.match(KNN_PUB_RE, pubs[0] % (812, 152531, 13, 126.4)) is not None
    clock = [0.0]
    tp = TP.TourPhases(clock=lambda: clock[0])
    for k, _lab in TP.PHASES:
        clock[0] += 1.0
        tp.lap(k)
    tp.add_sub("pattern_evidence", 0.4)
    ln = tp.log_line()
    ph_ok = bool(re.search(r" · coin head [0-9.]+s \(pattern kanıtı 0\.4s\) · ", ln) and re.search(r"toplam [0-9.]+s$", ln)
                 and ("pattern_evidence", "pattern kanıtı", "coin_heads") in tuple(TP.SUB_PHASES))
    okk = not miss and pre_ok and pub_ok and ph_ok
    if not okk:
        print("         eksik: %s · ön ısıtma biçimleri %s · yayım satırı %s · tur fazı satırı %s"
              % (miss, filled or fmts, pubs, ln[:160]))
    return okk


def evc_log_formats():
    """--check'in aradığı satırlar kodda; motorun ve önbelleğin HER alt süreç uyarısı --check'in saydığı öneklerden birine
    uyar (EVC_ERR_PREFIXES = evc_report ERR); başlangıç satırı ("pattern kanıtı sorguları: ALT SÜREÇTE" / "süreç içinde"), iş
    sonu ("alt süreç pid N, özel bellek N MB"), arıza / bellek koruması / KAPALI satırları; eski sürüm alt sürecinin hemen
    kapatılması; makine işareti yedeğe girmez (ops/backup MACHINE_MARKERS = evidence_child.MARKER_NAME)."""
    from tradingbot.ops import backup as B
    from tradingbot.patterns import evidence_child as EC
    eng = Path("tradingbot/engine_v3.py").read_text(encoding="utf-8")
    cache = Path("tradingbot/patterns/evidence_cache.py").read_text(encoding="utf-8")
    need_e = ("pattern kanıtı sorguları: ALT SÜREÇTE", "pattern kanıtı sorguları: süreç içinde")
    need_c = ("alt süreç pid {rec['pid']}, özel bellek {mem} MB", "pattern kanıtı alt süreci arızalandı",
              "pattern kanıtı alt süreci bellek koruması nedeniyle kapatıldı", "pattern kanıtı alt süreci KAPALI",
              "eski sürümün alt süreci (sürüm %d) daha yeni yayımla hemen kapatıldı", "pattern kanıtı ön ısıtıldı: ")
    miss = [s for s in need_e if s not in eng] + [s for s in need_c if s not in cache]
    ec = _log_consts("tradingbot/patterns/evidence_cache.py")
    ws = [w for w in (ec.get("warning", []) + ec.get("error", []) + ec.get("exception", [])
                      + _log_consts("tradingbot/engine_v3.py").get("warning", []))
          if w.startswith("pattern kanıtı alt süre")]
    uncounted = [w for w in ws if not w.startswith(EVC_ERR_PREFIXES)]
    okk = len(ws) >= 7 and not uncounted and not miss and tuple(B.MACHINE_MARKERS) == (EC.MARKER_NAME,) and EC.supported() is sys.platform.startswith("linux")
    if not okk:
        print("         eksik log biçimleri: %s · --check'in saymadığı alt süreç uyarıları (%d içinde): %s · işaret %s / %s"
              % (miss[:3], len(ws), uncounted[:3], B.MACHINE_MARKERS, EC.MARKER_NAME))
    return okk


checks = [
    # --- önceki sürümün 15 değişmezi (üçü TABAN olarak yeniden adlandırıldı: öğrenme kendi kaldıraç/risk
    #     tavanını defter başına ayrı taşır; config'teki TABAN değerler AYNEN kalır)
    ("mod PAPER", lambda: v3.mode.mode == "PAPER"),
    ("gerçek işlem kapalı", lambda: not bool(getattr(v3.mode, "live_trading", False))),
    ("T2/M2/Box/D4/C4/C4S defterleri", lambda: set(books) == {"t2_trend_regime", "m2_tsmom28", "b1_box_fade",
                                                             "d4_donchian_20_10", "c4_candle_variations",
                                                             "c4s_candle_variations_strict"}),
    ("D4 TABAN kaldıraç 1 (öğrenme kaldıracı ayrı: learning_mode.books)",
     lambda: int(books["d4_donchian_20_10"].rule_params.get("leverage", 1)) == 1),
    ("D4 ayrı defter", lambda: books["d4_donchian_20_10"].state_dir == "strategy_paper_trend4h"),
    ("C4/C4S TABAN kaldıraç 1", lambda: all(int(books[n].rule_params.get("leverage", 1)) == 1
                                            for n in ("c4_candle_variations", "c4s_candle_variations_strict"))),
    ("C4: CV001–CV008 gözlem olarak etkin",
     lambda: list(books["c4_candle_variations"].rule_params.get("variations") or []) == C4_IDS),
    ("C4 varyasyonları kapıdan geçiyor (standart)", lambda: all(V.gate(v)[1] is None for v in C4_IDS)),
    ("C4S boş (sıkı hükümle GÜÇLÜ varyasyon yok)",
     lambda: not list(books["c4s_candle_variations_strict"].rule_params.get("variations") or [])),
    ("C4S sıkı hüküm", lambda: books["c4s_candle_variations_strict"].rule_params.get("verdict_mode") == "strict"),
    ("C4/C4S ayrı defterler", lambda: (books["c4_candle_variations"].state_dir,
                                       books["c4s_candle_variations_strict"].state_dir)
                                      == ("strategy_paper_candle4h", "strategy_paper_candle4h_strict")),
    ("Box yapı katmanı SHADOW (karar B)", lambda: (raw.get("structures") or {}).get("b1_box_fade") == "SHADOW"),
    ("formasyon protokolü v3", lambda: v3.pattern_trader.protocol == "momentum_4h_v3"),
    ("tek coin tavanı %30", lambda: float(rp["max_position_pct"]) == 30.0),
    ("TABAN işlem riski %2 = öğrenme sert tavanı",
     lambda: float(rp["risk_per_trade_pct"]) == 2.0 and float(LMM.HARD_CAP_PCT) == 2.0),
    # --- öğrenme modu (CONTRACT.md "Additions" dahil)
    ("öğrenme modu ETKİN (servis ortamı dahil)", lambda: lm.enabled is True),
    ("öğrenme değerleri birebir: risk %0,5 · toplam açık risk 100 · rezerv %5 · likidasyon 2,0 × stop · "
     "min-notional yükseltme · karşı-olgusal (en çok 2000 bekleyen) · öğrenme-ekstra kipi record_selectivity", lm_scalars),
    ("öğrenme defterleri birebir: main K20 L5 · T2/M2 K40 L4 · Box K40 L4 min stop 0,5 · D4/C4 K20 L3 evren · "
     "C4S kapalı · Formasyon K30 L3", lm_books),
    ("öğrenme strateji ezmeleri birebir: rejim/mum gölge · yapı girişi gölge [main, T2, M2] · keşif · kaldıraç tabanı",
     lambda: same(lmraw.get("strategy_overrides"), LM_OVR) and same(dict(lm.strategy_overrides), LM_OVR)),
    ("öğrenme anahtarı defterlere doğru dağılıyor (yeni kod LearningMode; Box tabanı 0,5 ve record_selectivity her "
     "defter görünümünde ve sağlık özetinde)", lm_runtime),
    ("risk profili PAPER_RESEARCH, overrides {}",
     lambda: str(v3.risk_profiles.profile).upper() == "PAPER_RESEARCH"
     and (raw.get("risk_profiles") or {}).get("overrides") == {} and not (v3.risk_profiles.overrides or {})),
    ("TABAN profil sabitleri değişmedi (PAPER_RESEARCH: işlem %2, toplam açık risk %6; öğrenme profili kayıtsız)",
     lambda: (PROFILES["PAPER_RESEARCH"].risk_per_trade_pct, PROFILES["PAPER_RESEARCH"].max_total_open_risk_pct)
     == (2.0, 6.0) and set(PROFILES) == {"PAPER_RESEARCH", "TESTNET", "SHADOW_LIVE", "LIVE_LIMITED", "LIVE"}),
    ("yürütme kapısı paper", lambda: str(v3.execution.gateway).lower() == "paper"),
    ("testnet kapalı", lambda: v3.execution.testnet_enabled is False),
    ("TABAN adet tavanları 3 değişmedi (risk.max_open_positions, futures.max_positions, pattern_trader)",
     lambda: int(rp["max_open_positions"]) == 3 and int((raw.get("futures") or {})["max_positions"]) == 3
     and int(v3.pattern_trader.max_open_positions) == 3),
    ("state/mode.json yok ya da PAPER; mod kapısı açık (öğrenme AKTİF olur)", mode_file_ok),
    # --- ortak deneyim katmanı v1 (yalnız KAYIT) + net etiket (2026-09-29)
    ("ortak deneyim ETKİN: enabled + mode RECORD (servis ortamı dahil; yalnız off env'i kapatabilir)", xp_active),
    ("ortak deneyim config.yaml bölümü birebir {enabled: true, mode: RECORD, advisor_mode: RECORD}; varsayılanlar: state_dir "
     "shared_experience · sıcak 5000 · arşiv 0 · 1024 MB · bütçe 2,0 sn · 600 satır · geri doldurma · önbellek 2048 · 48 sa · "
     "kilit 0,2 sn · ağ 0 (+ danışman ayarları varsayılanda)", xp_section),
    ("ortak deneyim YALNIZ PAPER: toplayıcı PAPER dışı modda / canlı emir yolu açıkken SUSPENDED:<mod>, satır yok",
     xp_paper_only),
    ("durum şeması situation_v1 / SCHEMA_SHA 640fd10e5d6f727c; W4H = W1H = WBTC = 200 ≤ BARS_PER_TF 4h = 1h = 240",
     xp_situation_consts),
    ("satır şeması shared_experience_row_v1 · katman 1.0.0 · döngü histerezisi 0,5", xp_row_consts),
    ("net etiket cf_label_v3 · cf_net_ledger_replay_v2 · bar içi yol open>adverse>favourable>close", cf_consts),
    ("paketi yalnız engine_v3 ve cli_v3 içe aktarır; panel aktarmaz (AST, dağıtılan ağaç); scripts/ altında tek istisna "
     "çevrimdışı SENTETİK demo betiği — çalışma zamanının hiçbir dosyasında 'demo' geçmez; ayrı süreçte (servis ortamı "
     "aktarılmadan) dolu state'lere yazmayı reddeder, varsayılan klasör ve ortam yemleri dahil", xp_importers),
    ("config_hash shared_experience bölümüyle ve bölümsüz AYNI (karar kimliği değişmez)", xp_config_hash),
    # --- GÖLGE DANIŞMAN v1 — yalnız KAYIT (2026-09-30; DANISMAN_V1.md §1/§5/§7)
    ("danışman mühürleri spec'ten yeniden hesaplandı: ADVISOR_SHA 8a89fd7e69a2d33b · WF_SHA b34d6b7a313d24d1 · situation_v1 "
     "SCHEMA_SHA 640fd10e5d6f727c; advisor_v1 · shared_experience_advice_v1 · xp_advice · etki NONE", adv_seals),
    ("DANISMAN_V1.md §7 mühür tablosu = kod = beklenen değerler", adv_doc_seal),
    ("danışman ETKİN: katman RECORD + advisor_mode RECORD ve worker'ın kurulum yolu (from_engine) LiveAdvisor kuruyor "
     "(servis ortamı dahil; yalnız off env'i kapatabilir)", adv_active),
    ("danışman modu yalnız OFF | RECORD: yeni yükleyici ADVISE / ENFORCE / başka değeri ConfigError ile reddeder; "
     "TRADINGBOT_SHARED_EXPERIENCE_ADVISOR yalnız kapatır (off → OFF, başka değer → ConfigError)", adv_modes),
    ("config.yaml'da shared_experience.advisor_mode dışında advisor_* / advice_* anahtarı YOK; danışman ayarları kod "
     "varsayılanında: bütçe 250 ms · yetişme 1000 ms · 5000 satır · anlık görüntü 60 adım · dizin 96 MB · tavsiye sıcak 2000 · "
     "tavsiye 256 MB", adv_keys),
    ("danışman paketi, panel, cli_v3, config_v3 ve ops/backup DIŞINDA tradingbot/'un hiçbir modülü (motor, öğrenme, "
     "strategy_paper, paper_rules, mum/Box/Donchian kuralları, signal_lab, pattern_trader, risk, accounting, learn, replay …) "
     "danışmana/tavsiyeye atıf yapmaz (bilinen ilgisiz LLM/risk ifadeleri hariç); panel/config_v3/backup paketi içe aktarmaz "
     "(AST, dağıtılan ağaç)", adv_ast),
    ("danışman YALNIZ advice/ altına yazar: toplayıcının gerçek toplu yazımı + danışman kancasıyla (worker yolu) OFF ↔ RECORD "
     "ana depo ve imleç her yazımda BAYT BAYT aynı (sayaçlar/ayarlar da); doğrudan LiveAdvisor yetişme + canlıda da aynı; her "
     "tavsiye etki NONE; advisor_mode OFF → danışman kurulmaz, advice/ açılmaz", adv_record_only),
    ("deploy/restore.sh gerçekten geri yükler ('restore \"$ARCHIVE\" --yes'; kuru çalışma dalı yalnız 'restore \"$ARCHIVE\"'); "
     "SAHİPLİK: worker biriminin User='ı (yoksa state'in gerçek sahibi; sayısal) geri yüklemeden ÖNCE alınır, SONRA worker "
     "başlamadan chown -R ile yeni state'e (ve root'a ait vault.restored-*'a) verilir; HATA / Ctrl+C: 'GERİ YÜKLEME "
     "BAŞARISIZ … Worker DURDURULDU' + chown çaresi, worker BAŞLAMAZ; | head ve SSH kopması işi kesmez (satırlar + taklitli "
     "koşu)", restore_yes),
    # --- BU SÜRÜM (2026-10-03): hedef f8b05fb, ön koşul fc63481 — SAHİP ONAYLI karar değişikliği (yalnız onaylanan)
    ("config.yaml = hedef baytları (sha256 2f79d22f709d5dcc…); 8db1faf'takinden (VPS'te çalışan; sha256 be5e3e0d…) YALNIZ "
     "eklenen KAPALI m2x_aggressive bölümüyle; fc63481'dekinden (sha256 59e18ad1…) YALNIZ onaylanan iki değer "
     "(learning_mode.extra_entries yok → record_selectivity · books.b1_box_fade.min_stop_pct 0,32 → 0,5) ve aynı M2X "
     "bölümüyle ayrılır (başka veri farkı yok)", config_delta),
    # --- önceki sürümün (fc63481) modülleri — bu sürümde de AYNEN
    ("fc63481 modülleri dağıtılan ağaçta ve --check'in okuduğu biçimler: tur fazları (health.json \"phases\" 17 faz + pattern "
     "kanıtı, \"tur fazları:\" satırı; her faz turda tam bir kez ölçülür), pattern kanıtı ön ısıtması (yayım sonrası kanca; "
     "her arıza --check'in aradığı ifadeyle loglanır; INFO satırları süzülmez), worker'ın metin satırı biçimi = --check "
     "süzgeci, cf_aux_v1 açık", new_modules),
    ("cf_aux_v1 YALNIZ KAYIT: aux açık/kapalı → aux dışındaki her alan, r_net, araştırma R'si ve net özet AYNI; r_net "
     "DEĞİŞMEDİ: 48 sentetik kaydın r_net'i 188cf22 koduyla (= fc63481) aynı ve yeni net etiketinki = net dolgununki; aux "
     "yalnız yeni net etikette; dağıtımın net dolgusu (relabel_net) aux YAZMAZ (sentetik kayıtlar, geçici klasör)", cf_aux_record_only),
    # --- BU SÜRÜM (2026-10-03): öğrenme-ekstra YALNIZ KAYIT — AYRI süreçte, servis ortamı aktarılmadan, deponun kendi testleri
    ("öğrenme-ekstra sınıf tablosu = ONAYLANAN tablo (22 kapasite · Box istisnası · 25 seçicilik + 4 önek; bilinmeyen kod "
     "seçicilik); open hiçbir kodu ayırmaz, record_selectivity yalnız seçicilik kodlu girişi ayırır; deponun tablo testi "
     "(tablo, 14 sınıflama, kod tabanının AST taraması — sınıfsız kod yok —, etiketi yalnız üç giriş yolu yazar, kip/config "
     "doğrulaması, karar günlüğü, dönüşüm) dağıtılan ağaçta 22/22 geçer", ro_classification),
    ("KILL SWITCH ve 'fazlası yok': deponun koşucularıyla (doğrudan yollar + beş gerçek tur, iki senaryo) extra_entries: open "
     "kararları `git archive fc63481` ağacınınkiyle AYNI (doğrudan yollarda her dosya bayt bayt, saat damgaları maskeli); "
     "record_selectivity YALNIZ seçicilik-ekstrayı kayda çevirir (kapasite / politika / Box istisnası aynı, kill switch kaydı "
     "dönüşür); HER açık strateji defterinde (T2, M2, Box, D4, C4) ortak açılış yerinde de: seçicilik kodu → tek yalnız-kayıt "
     "kaydı, kapasite / Box istisnası / politika → open ile aynı, open = fc63481", ro_open_equals_prereq),
    ("LEARNING_RECORD_ONLY kayıtları araştırma politikasına (BLOCKED gözlemi) ve deneyim havuzuna GİRMEZ — record_selectivity "
     "motorunda (deponun testi) ve kill switch sonrası open motorunun tam etiketleme yolunda; normal karşı-olgusal eskisi gibi "
     "gözlenir", ro_research_excluded),
    # --- ÖNCEKİ SÜRÜM (8db1faf, 2026-10-05): pattern kanıtı alt süreci — kod yerinde; 2026-10-06'dan beri varsayılan KAPALI
    ("pattern kanıtı ALT SÜRECİ (açık true ile) karar-nötr: deponun 7 testi dağıtılan ağaçta (AYRI süreç) — alt süreç kanıtı "
     "süreç içiyle bit-bit aynı ve dosyalar aynı; yayım anı ve sürüm görünürlüğü aynı; daha yeni yayım eski sürümün alt "
     "sürecini kapatır, eski kanıt verilmez; her yerde hata → bugünkü sonuç; false → fork yok; varsayılan KAPALI, true açar; "
     "config_hash aynı", evc_child_neutral),
    ("history.evidence_subprocess config.yaml'da YOK, kod varsayılanı KAPALI (süreç içi); açık true → alt süreç (Linux), "
     "config'in başka HİÇBİR değeri değişmez; VPS'teki kill switch satırı (false) = varsayılan; tırnaklı değer ConfigError",
     evc_switch),
    ("--check'in aradığı alt süreç satırları kodda (başlangıç ALT SÜREÇTE / süreç içinde, iş sonu pid + özel bellek, arıza / "
     "bellek koruması / KAPALI, eski sürüm hemen kapatıldı); fork işareti makine durumu (yedeğe girmez)", evc_log_formats),
    # --- BU SÜRÜM (2026-10-06): hedef TIP, ön koşul 8db1faf — KARAR DEĞİŞTİRMEYEN tur düzeltmesi V2 (hızlı ve kesin kNN sorgusu)
    ("hızlı kNN kanıt sorgusu ESKİ döngüyle BİT-AYNI: deponun 19 testi (29 durum) dağıtılan ağaçta (AYRI süreç, servis "
     "ortamıyla — TRADINGBOT_* yolları hariç —, nice 19): üretim çağrısı 13 sembol × 2 yön, rastgele/çekişmeli sorgular, "
     "embargo/çıkış sınırları, alt sınır her adayda geçerli, yedek + tek uyarı, kayıplı zaman damgası → eski döngü, kurulum "
     "arızası motor başına bir kez, config anahtarı/karar kimliği, kurucu + CLI ataması, depodaki tur indeksleri",
     knn_identity),
    ("history.evidence_fast_knn config.yaml'da YOK, kod varsayılanı AÇIK (hızlı yol); başlıktaki kill switch satırı → false, "
     "eski döngü (worker kurucusu ve CLI), config'in başka HİÇBİR değeri değişmez; tırnaklı değer ve satırın İKİ KEZ "
     "uygulanması ConfigError", knn_switch),
    ("config_hash (karar kimliği) 8db1faf KODUYLA AYNI: 8db1faf'ın config.yaml'ı ve VPS'teki hali (evidence_subprocess: false) "
     "8db1faf ağacında ve bu ağaçta; bu sürümün config.yaml'ı, hızlı kNN kill switch'i, iki satır birlikte, alt süreç true — "
     "hepsi tek özet (AYRI süreçler)", hash_vs_prev),
    ("M2X ayna defteri KAPALI: config bölümü birebir (enabled: false), yüklenen değer false (servis ortamıyla), motor yalnız "
     "enabled iken kurar, strateji defterlerinde M2X yok", m2x_off),
    ("--check'in aradığı hızlı kNN satırları kodda ve biçimleri eşleşiyor: başlangıç satırı HIZLI/ESKİ (iki çağrı yeri), "
     "'pattern kNN hızlı yolu' uyarısı, yayım satırı ve ön ısıtma satırının iki biçimi, tur fazı 'coin head … (pattern "
     "kanıtı Xs) … toplam Ys'", knn_log_formats),
]
bad = []
for n, fn in checks:
    try:
        okk = bool(fn())
        why = ""
    except Exception as exc:  # noqa: BLE001 — değerlendirilemeyen değişmez düşmüş sayılır
        okk, why = False, " (değerlendirilemedi: %s: %s)" % (type(exc).__name__, exc)
    if not okk:
        bad.append(n)
    print("   %s  %s%s" % ("OK  " if okk else "HATA", n, why))
if lm.enabled is not True and os.environ.get("TRADINGBOT_LEARNING_MODE"):
    print("   (servis ortamında TRADINGBOT_LEARNING_MODE=%s öğrenmeyi kapatıyor)" % os.environ["TRADINGBOT_LEARNING_MODE"])
if XP_ENV:
    print("   !!! servis ortamında TRADINGBOT_SHARED_EXPERIENCE=%s — ortak deneyim katmanı KAPALI kalacak (operatör kapatması)"
          % os.environ["TRADINGBOT_SHARED_EXPERIENCE"])
if ADV_ENV:
    print("   !!! servis ortamında TRADINGBOT_SHARED_EXPERIENCE_ADVISOR=%s — gölge danışman KAPALI kalacak (operatör kapatması)"
          % os.environ["TRADINGBOT_SHARED_EXPERIENCE_ADVISOR"])
print("   %d/%d değişmez geçti" % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
PY
then
  cleanup_wt; die "config değişmezleri (yeni kod) düştü → kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
fi
cleanup_wt
ok "yeni kod ayrı kopyada preflight ve değişmezlerden geçti"

if [[ "$MODE" == "--dry-run" ]]; then
  say "KURU ÇALIŞMA BİTTİ — çalışan kod, state, config.yaml ve servisler DEĞİŞMEDİ (hedef kod git nesnelerine eklendi, ayrı kopya silindi)."
  if [[ -n "$CFG_LOCAL" ]]; then
    echo "   config.yaml: bilinen yerel satır (history: altında 'evidence_subprocess: false', 2026-10-06) KABUL edildi — dağıtımda"
    echo "   doğrulanmış yedekten SONRA, worker durmuşken 'git checkout -- config.yaml' (servis kullanıcısı) ile geri alınır ve log'a"
    echo "   yazılır; dağıtım öncesi baytlar $CFG_SAVE'e saklanır ve GERİ ALMADA AYNEN yerine konur."
  fi
  echo "   Dağıtımda ayrıca (worker durmuşken): doğrulanmış yedek + scripts/cf_backfill_net.py kuru çalışma ve --apply"
  echo "   (aday 0 beklenir → NO_CHANGE). config.yaml 8db1faf'takinden YALNIZ eklenen KAPALI m2x_aggressive bölümüyle ayrılır"
  echo "   (#43); config_hash 8db1faf ile AYNI (#54); deploy/restore.sh 8db1faf'taki ile AYNI. Yeni worker'ın İLK turu en uzun"
  echo "   turdur (spot WFO + tur; f8b05fb'de 62,7 dk): ilk --check'i ≈ 1 saat sonra çalıştırın. --check \"PATTERN KANITI HIZLI"
  echo "   kNN\" bölümü yayım başına ön ısıtma saniyesini (beklenen birkaç sn; 8db1faf'ta ~1.000 sn) ve yayımdan sonraki turların"
  echo "   \"pattern kanıtı\" fazını gösterir; yayımla çakışan turlar ~5–6 dk beklenir (8db1faf'ta 23–36 dk). Kill switch (kod"
  echo "   aynı kalır; YALNIZ BİR KEZ — iki kez: yinelenen anahtar → ConfigError, worker BAŞLAMAZ):"
  echo "     sudo -u $SVC_USER sed -i 's/^history:\$/history:\n  evidence_fast_knn: false/' $APP/config.yaml && sudo systemctl restart $WORKER"
  echo "   engine-app (öğrenme motoru): dağıtımın sonunda AYNI motor koduyla $T7'e yeniden sabitlenir (kuruluysa; arızası worker'ı"
  echo "   etkilemez). Şu anki durum yukarıda (5/10)."
  WU="$(systemctl show -p User --value "$WORKER" 2>/dev/null || true)"
  echo "   deploy/restore.sh (188cf22'den beri aynı) GERÇEKTEN geri yükler ve geri yüklenen state'i worker'ın servis kullanıcısına"
  echo "   (birim User=${WU:-? → bildirmiyor: state klasörünün sahibi $(stat -L -c '%U:%G' "$STATE" 2>/dev/null || echo '?')}) verir; bir adım"
  echo "   düşerse ya da Ctrl+C gelirse \"GERİ YÜKLEME BAŞARISIZ\" yazar ve worker DURMUŞ kalır; SSH kopması / | head işi"
  echo "   yarıda kesmez. Ayrıntı dağıtım sonu metninde (\"GERİ YÜKLEME\")."
  echo "   Dağıtmak için: sudo bash $0   (ya da SSH'tan bağımsız: sudo bash $0 --detach)"
  exit 0
fi

say "7/10 doğrulanmış yedek (git ve servislere dokunmadan ÖNCE)"
svc_run "$APP" env TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" \
  TRADINGBOT_BACKUPS_DIR="$DATA/backups" bash "$APP/deploy/backup.sh" manual \
  || die "YEDEK BAŞARISIZ ya da DOĞRULANAMADI → dağıtım DURDURULDU (git/servis dokunulmadı)"
echo "$PREV" > "$BASE/.last_good_commit"       # deploy/rollback.sh bunu kullanır
chown "$SVC_USER:$SVC_USER" "$BASE/.last_good_commit" 2>/dev/null || true
ok "yedek alındı; geri dönüş işaretçisi ${PREV:0:7}"

say "8/10 worker'ın turlar arası beklemeye girmesi bekleniyor (kod hâlâ ${PREV:0:7})"
RESTART_AT="$(date '+%Y-%m-%d %H:%M:%S')"
key_log() {    # yalnız karar verdiren satırlar (analiz gürültüsü değil)
  journalctl -u "$WORKER" --since "$RESTART_AT" --no-pager 2>/dev/null \
    | grep -E 'Started|Stopping|Stopped|Main process exited|Scheduled restart|Failed|Traceback|ERROR|CRITICAL|Killed|oom|BLOCK|ALLOW|ConfigError|LEARNING|ortak deneyim|danışman|DEVRE KESİCİ' \
    | tail -n 40 || true
}
# Worker SIGTERM'de MEVCUT TURU bitirip çıkar; tur ~10-25 dk sürer, TimeoutStopSec 90 sn → tur ortasında durdurmak SIGKILL
# demektir (2026-09-25 ilk denemede iki kez oldu). Turlar arası beklemede ise 2 sn içinde temiz kapanır ve state yazılır.
# Bekleme aralığı: heartbeat.json yalnız turlar arasında "source": "watch" ile ~30 sn'de bir yazılır (tur başı yazımı
# bu alanı taşımaz).
idle_now() {
  py - "$STATE/heartbeat.json" <<'PY'
import datetime as dt, json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    t = dt.datetime.fromisoformat(str(d.get("ts") or d.get("at")).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    age = (dt.datetime.now(dt.timezone.utc) - t).total_seconds()
except Exception:
    sys.exit(1)
sys.exit(0 if d.get("source") == "watch" and age < 45 else 1)
PY
}
# Worker her yeniden başlatmadan sonra ve her yeni 4h mumda TURDAN ÖNCE tam spot WFO döngüsü koşar (bekleme döngüsü
# bunu kontrol etmez); WFO + tur 1 saati aşabiliyor (2026-09-25 VPS: 30 dk içinde hiç bekleme görülmedi) → 3 saat.
phase_now() {
  journalctl -u "$WORKER" --since "-6h" -o cat --no-pager 2>/dev/null \
    | grep -E '^===== .* — (SPOT WFO|TUR #)' | tail -n 1 | tr -d '=' | sed 's/^ *//;s/ *$//' || true
}
# Dağıtım öncesi tepe bellek (health.json learning_mode.memory.hwm_mb) → --check farkı (2026-09-29; salt okur). (2026-09-30)
# 2. satır: o sürecin yaşı (sn) — hwm süreç ömrüyle büyür; --check genç süreci yaşlı sürecin tepesiyle karşılaştırmaz.
pre_hwm() {
  py - "$STATE/health.json" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    print(((d.get("learning_mode") or {}).get("memory") or {}).get("hwm_mb") or "")
except Exception:  # noqa: BLE001
    print("")
PY
  local p a=""
  p="$(systemctl show "$WORKER" -p MainPID --value 2>/dev/null || true)"
  if [[ "$p" =~ ^[1-9][0-9]*$ ]]; then a="$(ps -o etimes= -p "$p" 2>/dev/null | tr -d ' ' || true)"; fi
  echo "$a"
}
waited=0
wait_idle() {
  until idle_now; do
    if (( waited >= 10800 )); then
      die "worker 3 saat içinde turlar arası beklemeye girmedi → kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
    fi
    if (( waited % 300 == 0 )); then
      echo "   $(date '+%H:%M') worker meşgul [$(phase_now)] — bitince durdurulacak (bekleniyor: $((waited / 60)) dk)"
    fi
    sleep 10; waited=$((waited + 10))
  done
}
while :; do
  wait_idle
  ok "worker turlar arası beklemede"
  say "9/10 son ortam denetimi → worker DURUR →$([[ -n "$CFG_LOCAL" ]] && echo " config.yaml'daki bilinen satır geri alınır →") kod ${PREV:0:7} → $T7 (ff-only) → yedek → karşı-olgusal net dolgu"
  # bekleme saatler sürebilir: ortam ve mod bu arada değişmiş olabilir → kodu değiştirmeden hemen önce yeniden bak
  ENV_ARGS=(); rm -f "$ENVFILE"; load_env
  lm_env_scan || die "TRADINGBOT_LEARNING_MODE beklerken tanımlanmış: $lm_env_hint. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
  xp_env_scan || die "TRADINGBOT_SHARED_EXPERIENCE beklerken geçersiz değerle tanımlanmış: $xp_env_hint. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
  adv_env_scan || die "TRADINGBOT_SHARED_EXPERIENCE_ADVISOR beklerken geçersiz değerle tanımlanmış: $adv_env_hint. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
  mode_check || die "state/mode.json beklerken PAPER dışına çıkmış. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
  xp_dir_check check || die "state/shared_experience beklerken yazılamaz olmuş. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
  cfg_same_as_start || die "izlenen dosyalar beklerken DEĞİŞMİŞ (başlangıçta: $([[ -n "$CFG_LOCAL" ]] && echo "yalnız bilinen config.yaml satırı, sha256 ${CFG_PRE_SHA:0:16}…" || echo temiz)):
$(gitc status --porcelain --untracked-files=no)
  Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı. Betiği yeniden çalıştırın (yalnız bilinen satır kabul edilir)"
  ok "izlenen dosyalar başlangıçtaki gibi ($([[ -n "$CFG_LOCAL" ]] && echo "yalnız bilinen config.yaml satırı, aynı baytlar" || echo temiz))"
  ( pre_hwm ) > "$MARK_PRE_HWM" 2>/dev/null || true             # alt kabuk: tuzak çıktısı dosyaya gitmesin
  # denetimler birkaç saniye sürdü: worker bu arada yeni tura başladıysa (tur ortasında durdurma = SIGKILL) yeniden bekle
  if idle_now; then break; fi
  echo "   worker bu arada yeni tura başladı — yeniden bekleniyor"
done
# (2026-09-29) Göç (yedek + net dolgu) worker DURMUŞKEN yapılır: çalışan worker karşı-olgusal dosyalarını kendi bellek
# kopyasıyla yeniden yazardı. Worker durduktan sonraki her hata worker'ı ESKİ kodla yeniden başlatır.
STOPPED=1
trap 'systemctl start "$WORKER" || true; echo "DUR: beklenmeyen hata (satır $LINENO) — kod DEĞİŞMEDİ; worker eski kodla yeniden başlatıldı" >&2' ERR
systemctl stop "$WORKER" || true
if [[ "$(systemctl is-active "$WORKER" || true)" == "active" ]]; then
  trap - ERR
  die "worker durdurulamadı (hâlâ aktif) → kod DEĞİŞMEDİ; worker eski kodla çalışıyor"
fi
ok "worker durduruldu (turlar arası beklemedeydi; state yazıldı)"
MERGED=1
# Bu noktadan sonra beklenmeyen her hata kodu geri alır (worker durur → eski kod → dağıtım öncesi config.yaml → worker başlar).
trap 'revert_code; echo "DUR: beklenmeyen hata (satır $LINENO) → kod ${PREV:0:7}e geri alındı" >&2' ERR
if [[ -n "$CFG_LOCAL" ]]; then
  # (2026-10-06) bilinen yerel satır: worker DURMUŞ, doğrulanmış yedek alınmış; ileri sarmadan hemen önce servis kullanıcısıyla
  # geri alınır (ff temiz ağaç ister). Geri almada cfg_put_back dağıtım öncesi baytları AYNEN koyar.
  cmp -s "$CFG_SAVE" "$APP/config.yaml" || { revert_code; die "config.yaml saklanan dağıtım öncesi kopyadan farklı → kod ${PREV:0:7}'de kaldı, worker eski kodla başlatıldı"; }
  CFG_RESTORED=1
  gitc checkout -- config.yaml
  [[ -z "$(gitc status --porcelain --untracked-files=no)" ]] || { revert_code; die "git checkout -- config.yaml sonrası ağaç temiz değil → geri alındı, worker eski kodla başlatıldı"; }
  ok "config.yaml'daki bilinen yerel satır geri alındı (sudo -u $SVC_USER git checkout -- config.yaml; config.yaml artık ${PREV:0:7} baytları, sha256 $(sha256sum < "$APP/config.yaml" | cut -c1-16)…; dağıtım öncesi hali: $CFG_SAVE)"
fi
gitc merge -q --ff-only "$TIP"
[[ "$(gitc rev-parse HEAD)" == "$TIP" ]] || { revert_code; die "ileri sarma sonrası HEAD hedef değil → kod ${PREV:0:7}'e geri alındı"; }
ok "kod ${PREV:0:7} → $T7 (ff-only)"
if [[ -n "$REQ_CHANGED" ]]; then
  "$VENV/bin/pip" install -q -r "$APP/requirements.txt"
  chown -R "$SVC_USER:$SVC_USER" "$VENV" 2>/dev/null || true
fi
if ! xp_dir_check ensure; then
  revert_code; die "state/shared_experience açılamadı/yazılamadı → kod ${PREV:0:7}'e geri alındı, worker eski kodla başlatıldı"
fi
echo "   doğrulanmış yedek (worker durmuşken, yeni kodla; göçten hemen önceki state):"
if ! svc_run "$APP" env TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" TRADINGBOT_BACKUPS_DIR="$DATA/backups" \
     "$VENV/bin/python" -m tradingbot backup --manual; then
  revert_code; die "göç öncesi YEDEK BAŞARISIZ ya da DOĞRULANAMADI → kod ${PREV:0:7}'e geri alındı, worker eski kodla başlatıldı (state'e dokunulmadı)"
fi
ok "göç öncesi yedek alındı ve doğrulandı"
say "karşı-olgusal net dolgu (scripts/cf_backfill_net.py; servis kullanıcısı; worker DURMUŞ)"
if ! cf_backfill; then
  revert_code; die "karşı-olgusal net dolgu başarısız → kod ${PREV:0:7}'e geri alındı, worker eski kodla başlatıldı. Yukarıdaki satırları bana iletin"
fi
ok "karşı-olgusal net dolgu tamam (rapor: $CF_DRY_JSON, $CF_APPLY_JSON)"

say "10/10 worker başlatma + panel yeniden başlatma + 60 sn kararlılık"
( ro_base ) > "$MARK_RO_BASE" 2>/dev/null || true    # alt kabuk: tuzak çıktısı dosyaya gitmesin (--check farkı için)
RESTART_EPOCH="$(date +%s)"
RESTART_AT="$(date -d "@$RESTART_EPOCH" '+%Y-%m-%d %H:%M:%S')"
printf '%s\n%s\n' "$RESTART_EPOCH" "$RESTART_AT" > "$MARK_RESTART"
tour4h_warn "yeniden başlatma"
RESTARTED=1
systemctl restart "$WORKER" "$DASH" || true      # preflight düşerse restart hata döner; aşağıda yakalanır
# NRestarts yalnız OTOMATİK yeniden başlamaları sayar ve elle restart'ta SIFIRLANIR: dağıtım öncesi değerle
# KARŞILAŞTIRILMAZ (2026-09-25 VPS: sağlıklı worker bu yüzden "kalkmadı" sayılıp geri alınmıştı). Taban = restart'tan
# hemen sonraki değer; kararlılık = 60 sn boyunca aynı süreç (MainPID) ve taban üstünde yeni otomatik başlama yok.
n0="$(systemctl show "$WORKER" -p NRestarts --value)"
up=""
for _ in $(seq 1 36); do                         # en çok 3 dk: preflight + başlatma
  sleep 5
  if [[ "$(systemctl is-active "$WORKER" || true)" == "active" ]]; then up=1; break; fi
done
pid1=""
if [[ -n "$up" ]]; then
  pid1="$(systemctl show "$WORKER" -p MainPID --value)"
  sleep 60
  if [[ "$(systemctl is-active "$WORKER" || true)" != "active" || -z "$pid1" || "$pid1" == "0" \
        || "$(systemctl show "$WORKER" -p MainPID --value)" != "$pid1" \
        || "$(systemctl show "$WORKER" -p NRestarts --value)" != "$n0" ]]; then
    up=""
  fi
fi
if [[ -z "$up" ]]; then
  key_log
  revert_code
  die "worker kararlı çalışmadı (aktif olmadı ya da 60 sn içinde yeniden başladı) → worker durduruldu, kod ${PREV:0:7}'e ve config.yaml dağıtım öncesi baytlarına geri alındı$([[ -n "$CFG_LOCAL" ]] && echo " (kill switch satırı dahil)") ve servisler yeniden başlatıldı (bu sürüm state'e yeni dosya yazmaz). Yukarıdaki satırları bana iletin"
fi
trap - ERR INT TERM
ok "worker çalışıyor (PID $pid1, 60 sn kararlı)"
echo "   panel: $(curl -fsS -m 5 http://127.0.0.1:8080/health/live 2>/dev/null | head -c 200 || echo 'henüz yanıt yok (birkaç sn sonra --check)')"
memory_report
memmax_check
say "engine-app (öğrenme motoru P1a) — AYNI motor koduyla $T7'e yeniden sabitleme (docs/OPERATIONS.md sürüm kuralı)"
engine_repin || true
WU="$(systemctl show -p User --value "$WORKER" 2>/dev/null || true)"

CFG_NOTE=""
if [[ -n "$CFG_LOCAL" ]]; then
  CFG_NOTE="
config.yaml'daki bilinen yerel satır (evidence_subprocess: false) geri alındı — dağıtım öncesi baytlar: $CFG_SAVE"
  RB_CFG_LINE="sudo cat $CFG_SAVE | sudo -u $SVC_USER tee $APP/config.yaml >/dev/null   # dağıtım öncesi config.yaml (alt süreç kill switch satırı) AYNEN"
else
  RB_CFG_LINE="sudo -u $SVC_USER sed -i 's/^history:\$/history:\n  evidence_subprocess: false/' $APP/config.yaml   # ${PREV:0:7}'te alt süreç varsayılan AÇIK: satır olmadan geri gelir"
fi
cat <<EOF

DAĞITIM TAMAM: ${PREV:0:7} → $T7 — KARAR DEĞİŞTİRMEYEN tur düzeltmesi V2 (2026-10-06): pattern kanıtı kNN sorgusu HIZLI
ve KESİN (history.evidence_fast_knn, kod varsayılanı AÇIK; kanıt eski olay-başına döngüyle BİT BİT aynı; yayım kodu,
indeks içeriği, önbellek anahtarı, config değerleri aynı). 8db1faf'ın alt süreci kod varsayılanında KAPALI.$CFG_NOTE
BEKLENEN VPS SİNYALİ (--check "PATTERN KANITI HIZLI kNN"): her yayımdan sonra "pattern kanıtı ön ısıtıldı: sürüm N, 13 sembol
hesaplandı, … S sn" → S birkaç sn (8db1faf'ta ~1.000 sn); yayımla çakışan turlar ~5–6 dk (8db1faf'ta 23–36 dk); tur fazı
"pattern kanıtı" ≈ 0; başlangıç satırı "pattern kanıtı kNN sorgusu: HIZLI yol (history.evidence_fast_knn=True; …)";
"pattern kNN hızlı yolu …" uyarısı YOK.
KILL SWITCH (bu düzeltme; kod aynı kalır, kanıt aynı — eski döngü, ön ısıtma yine ~1.000 sn). YALNIZ BİR KEZ uygulayın (iki
kez: yinelenen anahtar → ConfigError → worker BAŞLAMAZ; denetim: grep -c evidence_fast_knn $APP/config.yaml → 1):
  sudo -u $SVC_USER sed -i 's/^history:\$/history:\n  evidence_fast_knn: false/' $APP/config.yaml && sudo systemctl restart $WORKER
SAAT-DUVARI ZAMANI (sizin değerlendirmenize; docs/TOUR_CONTENTION_V2.md §8): kod ve kurallar aynı, zamanlama değil —
turlar yeni sürümü beklemez; 4h sonrası İKİNCİ indeks kurulumu daha erken bitebilir, bu da hangi turun hangi sürümü
gördüğünü (saat olarak) değiştirebilir. Hiçbir yayım bilerek ertelenmez / öne alınmaz; okuma kuralı aynı. Uygun
görmezseniz yukarıdaki KILL SWITCH eski döngüye döner.
GERİ ALMA SİNYALLERİ (bu düzeltme; --check işaretler): hızlı yol AÇIKKEN yayımla çakışan bir sonraki tur > 35 dk ·
"pattern kNN hızlı yolu …" uyarısı (kanıt yine aynı; yalnız yavaş) · ön ısıtma > 120 sn → çıktıyı bana iletin; acil
durumda KILL SWITCH ya da eski koda dönüş. Bellek: motor başına ~27 MB dizi (yayım anında ~54 MB); alt süreç YOK.
BU DAĞITIMLA WORKER'A GELEN DİĞER KOD (karar değiştirmez): M2X ayna defter (config'te enabled: false — KAPALI), öğrenme motoru
P1a kodu (worker'da çalışmaz), çevrimdışı araştırma modülleri. config.yaml 8db1faf'takinden YALNIZ kapalı m2x_aggressive
bölümüyle ayrılır (değişmez #43); config_hash 8db1faf ile AYNI (#54); restore.sh AYNI.
ÖNCEKİ SÜRÜMLERDEN AYNEN: f8b05fb kararları (extra_entries: record_selectivity, Box öğrenme stop tabanı 0,5; karnede «kayda
alınan ekstra» ve aylık hedef); GÖLGE DANIŞMAN (yalnız KAYIT), ORTAK DENEYİM KATMANI (RECORD), ÖĞRENME MODU (yalnız PAPER) ve
Box slot 40. Açık pozisyonlara dokunulmadı; defterler sıfırlanmadı (önceki durum: $LOGDIR/$T7-before.txt).
Karşı-olgusal net dolgu raporları: $CF_DRY_JSON · $CF_APPLY_JSON (aday 0 → değişiklik yok beklenir).

ÖNCEKİ SÜRÜMÜN KILL SWITCH'İ (öğrenme-ekstra; kod aynı kalır, bütün öğrenme-ekstralar fc63481'deki gibi yine açılır):
  $APP/config.yaml → learning_mode.extra_entries: open ; sudo systemctl restart $WORKER — tek satır:
  sudo -u $SVC_USER sed -i 's/^  extra_entries: record_selectivity /  extra_entries: open /' $APP/config.yaml && sudo systemctl restart $WORKER
  (açık pozisyonlar ve mevcut yalnız-kayıt kayıtları kalır, onlar araştırmaya/deneyime yine girmez; Box tabanı ayrı değerdir:
  books.b1_box_fade.min_stop_pct: 0.32. Elle düzenlenen config.yaml sonraki dağıtımda / eski koda dönüşte "yerel değişiklik"
  olur: aşağıdaki ESKİ KODA dönmek adımlarına bakın.)
8db1faf'ın ALT SÜRECİ: varsayılan KAPALI; açmak ÖNERİLMEZ (VPS'te bellek %97, tur kısalmadı). Gerekirse history: altına
  evidence_subprocess: true (tek kez) + restart; --check "PATTERN KANITI ALT SÜRECİ" bölümü onu raporlar.

İzleme (ilk 2-3 turda birkaç kez; 4h kapanışından sonraki iki yayımı kapsayacak şekilde; sonra günde bir):
  sudo bash $0 --check                         # hızlı kNN (yayım başına ön ısıtma, çakışma turları), öğrenme, YALNIZ KAYIT, tur fazları, ortak deneyim, danışman, bellek, TETİKLER, iki SHA
  journalctl -u $WORKER -f                     # canlı log (Ctrl+C ile çık)
Yeni worker önce tam spot WFO döngüsünü, sonra ilk turu çalıştırır: İLK TUR EN UZUNUDUR (f8b05fb'de 62,7 dk; 4h indeks
yayımıyla çakışırsa daha uzun). İlk --check'i ≈ 1 saat sonra, ilk tur bittikten sonra çalıştırın. --check ilk turu AYRI raporlar
(UYARI > 60 dk) ve 35 dk geri alma tetiğini SONRAKİ turlara uygular (değişmedi); etkin extra_entries'i, KAPATMA satırlarını
ve defter başına yalnız-kayıt sayılarını gösterir. Pattern kanıtı ön ısıtması yalnız yeni 4h indeks YAYIMINDAN SONRA koşar
(4h kapanışından sonra ~:10 ve ~:28–:35 UTC). "Olsaydı" kayıtları, aux ve tavsiyeler P&L'e/karara girmez.
Geri alma tetikleri (--check işaretler):
  genel: tepe bellek (hwm_mb) > %90 × MemoryMax · SONRAKİ turlar > 35 dk · Box kaçan bar > 0/saat (iki örnek arası) → bana
  iletin (İLK tur ayrı: > 60 dk UYARI — sonraki turlar 35 dk'nın altındaysa bu WFO + 4h yayım çakışmasıdır: çıktıyı bana
  iletin, kendiliğinden geri almayın);
  HIZLI kNN: yukarıdaki GERİ ALMA SİNYALLERİ;
  YALNIZ KATMAN: devre kesici · toplayıcı adım p95 > 1500 ms · depo DEGRADED → katmanı kapatın (aşağıda).
  DANIŞMAN (UYARI; kararı etkilemez): durum ≠ OK · yetişmede lag_rows azalmıyor · yazılan < üretilen · bozuk segment > 0
  → çıktıyı bana iletin; isterseniz yalnız danışmanı kapatın (aşağıda).
  ÖN ISITMA (UYARI; kararı etkilemez): HATA > 0 → çıktıyı bana iletin (tur kanıtı kendisi hesaplar; yalnız hız kaybı).
  WORKER YENİDEN BAŞLADI / TUR HATASI (UYARI): dağıtımdan sonra birden çok süreç ("TUR #1"), "İzleme turu hatası" ya da
  hata veren ilk turun tekrarı > 35 dk → çıktıyı bana iletin (elle yeniden başlattıysanız, ör. KAPATMA, süreç uyarısı beklenir).
  YALNIZ KAYIT (karar değişikliği): yalnız-kayıt sayıları ya da bir defterin aylık sonucu beklenmedik görünürse → çıktıyı
  bana iletin; acil durumda f8b05fb'nin KILL SWITCH'i (kod aynı kalır).
İsteğe bağlı (otomatik yazan YOK; salt okur, ayrı süreç, Nice=19): panel özeti ve danışman walk-forward taraması
  sudo -u $SVC_USER bash -c 'cd $APP && TRADINGBOT_STATE_DIR=$STATE nice -n 19 $VENV/bin/python -m tradingbot shared-experience-report --summary-out $XP_DIR/report_summary.json'
  sudo -u $SVC_USER bash -c 'cd $APP && TRADINGBOT_STATE_DIR=$STATE nice -n 19 $VENV/bin/python -m tradingbot shared-experience-advisor --looks --summary-out $ADV_DIR/walkforward_summary.json'
  (bakışlar PENDING / INVARIANTS_UNDECLARED gösterir: ara bakış kanıt değildir; ilk bakış doğumdan ≥ 28 gün sonra)
İşletim notları (betik bunlara DOKUNMADI): worker VPS'te MemoryMax=6G ile çalışır (systemd override; depodaki birim dosyası
  4G der) — birim dosyalarını kopyalamayın ve birim farkını incelemeden ASLA sudo systemctl daemon-reload çalıştırmayın.
  Aşağıdaki "sudo systemctl edit" önerileri için: $UNIT_EDIT_NOTE.
  İki saatten kısa kesintilerde (bu dağıtımdaki worker duruşu dahil) arada kapanan 1h barlar uygulanır: defterlerde
  yeniden başlatmadan ÖNCEKİ bir saate tarihli kapanış görmek bu yüzden olağandır (bilinçli politika).
  Bu betiği ve buradaki her komutu VPS'te SİZ çalıştırırsınız; "sahte VPS'te denendi" notları yerel denemedir.

YALNIZ DANIŞMANI KAPATMAK (katman, öğrenme, kararlar AYNEN; kod aynı kalır):
  config.yaml → shared_experience: {enabled: true, mode: RECORD, advisor_mode: OFF}   (ya da: sudo systemctl edit $WORKER →
  [Service] Environment=TRADINGBOT_SHARED_EXPERIENCE_ADVISOR=off; edit'ten önce yukarıdaki NeedDaemonReload / 6G
  denetimi)  ;  sudo systemctl restart $WORKER
  Danışman kurulmaz, health'te advisor anahtarı olmaz. state/shared_experience/advice/ SİLİNMEZ (doğum/üst işaret oradan).
KATMANI KAPATMAK (danışman da kapanır; öğrenme modu ve net etiket AYNEN sürer):
  config.yaml → shared_experience: {enabled: false, mode: RECORD, advisor_mode: RECORD}   (ya da drop-in
  Environment=TRADINGBOT_SHARED_EXPERIENCE=off; edit'ten önce aynı denetim)  ;  sudo systemctl restart $WORKER
ÖĞRENME MODUNU KAPATMAK (kod aynı kalır): config.yaml → learning_mode: enabled: false ; sudo systemctl restart $WORKER $DASH
ESKİ KODA (${PREV:0:7}) dönmek (yalnız gerekirse; betiğin kendi geri alma adımlarıyla aynı sıra; KOD VE CONFIG BİRLİKTE):
  sudo systemctl stop $WORKER
  sudo -u $SVC_USER git -C $APP checkout -- config.yaml                     # bu sürümün kill switch'i / elle düzenleme varsa ÖNCE bu
  sudo -u $SVC_USER git -C $APP checkout -q -B $BRANCH_NOW ${PREV:0:7}     # (dal yoksa: checkout ${PREV:0:7})
  $RB_CFG_LINE
  sudo systemctl start $WORKER; sudo systemctl restart $DASH
  ${PREV:0:7} bu sürümün kill switch anahtarını (history.evidence_fast_knn) ve m2x_aggressive bölümünü bilinmeyen anahtar
  olarak UYARIYLA yok sayar; ama git checkout yerel değişikliği REDDEDER → ikinci satır şart. ${PREV:0:7} bu sürümün yazdıklarını tolere eder (bu sürüm
  state'e yeni dosya yazmaz; M2X kapalı). restore.sh iki sürümde aynıdır. engine-app'e DOKUNMAYIN (${PREV:0:7} hedefin
  atası: app ⊑ engine-app, SKEW yok). 3b0ae8e'ye ASLA dönmeyin: OOM döngüsü geri gelir (OOM onarımı d8b0c8c).
Yedekler: saatlik yedek shared_experience/archive/segments'ı ve advice/archive/segments'ı yalnız UTC 00'da, türetilmiş
  advice/advisor_state.v1.gz'yi saatlikte HİÇ taşımaz (çıktıda skipped_xp_segments); günlük/haftalık/elle yedek hepsini
  taşır. deploy/restore.sh artık GERÇEKTEN geri yükler; çıktıda xp_segments.missing boş değilse (restore.sh ayrıca
  UYARI basar) eksik segmentleri en yeni UTC-00/günlük yedekten kopyalayın (danışman o segmentte bekler; karar etkilenmez).
GERİ YÜKLEME (yalnız gerekirse; restore.sh bu sürümde DEĞİŞMEDİ — 188cf22'den beri aynı):
  sudo bash $APP/deploy/restore.sh <arşiv> --dry-run    # yalnız doğrular + üyeleri listeler; hiçbir şeye dokunmaz
  sudo bash $APP/deploy/restore.sh <arşiv>              # worker durur → doğrula → geri yükle → sahiplik → worker başlar
  sudo systemctl restart $DASH
  Çıktı uzundur (arşivin ~1600 üyesi); yalnız sonunu görmek için komutun sonuna  2>&1 | tail -40  ekleyin. Bu sürümde
  çıktıyı | tail / | head'e vermek ya da SSH'ın kopması işi YARIDA KESMEZ (sahiplik verilir, worker başlar).
  | less KULLANMAYIN: less açık kaldıkça worker BAŞLATILMAZ (restore.sh çıktının bitmesini / q'yu bekler) ve less
  içindeyken Ctrl+C restore.sh'i iletisiz öldürür → worker DURMUŞ kalır. Tüm çıktıyı okumak için önce dosyaya alın:
    sudo bash $APP/deploy/restore.sh <arşiv> > ~/restore.out 2>&1 ; tail -40 ~/restore.out   (sonra: less ~/restore.out)
  SSH'tan tamamen bağımsız koşturmak isterseniz:  sudo systemd-run --unit tb-restore --collect bash $APP/deploy/restore.sh <arşiv>
  (izlemek: sudo journalctl -u tb-restore -f -o cat).
  restore.sh root olarak açar (açılan dosyalar root'a ait olur) ama sahibi geri yüklemeden ÖNCE belirler ve worker'ı
  başlatmadan önce yeni state'i (ve root'a ait vault.restored-* kopyasını) chown -R ile ona verir. Mevcut state
  $DATA/state.pre-restore-<ts> olarak kenara alınır (silinmez). Sahip = worker biriminin User='ı; şu an:
  ${WU:-birim bildirmiyor → $STATE klasörünün sahibi $(stat -L -c '%U:%G' "$STATE" 2>/dev/null || echo '?')}
  Sonra denetim:  sudo find $STATE ! -user $SVC_USER | head    (çıktı boş olmalı)
    çıktı boş DEĞİLSE:  sudo systemctl stop $WORKER; sudo chown -R $SVC_USER:$SVC_USER $STATE
                        sudo systemctl reset-failed $WORKER; sudo systemctl start $WORKER; sudo systemctl restart $DASH
  ve:  systemctl is-active $WORKER    (active olmalı). Değilse: "GERİ YÜKLEME BAŞARISIZ" iletisi varsa onu izleyin (aşağıda);
    ileti yoksa (ör. less içinde Ctrl+C) ve find çıktısı boşsa:  sudo systemctl start $WORKER; sudo systemctl restart $DASH
  Bir adım düşerse (bozuk arşiv / sha256, açma, chown) ya da Ctrl+C / SIGTERM gelirse restore.sh "GERİ YÜKLEME BAŞARISIZ"
  yazar ve worker DURMUŞ kalır (kendiliğinden başlatılmaz). İleti ne yapılacağını söyler: "State'e DOKUNULMADI" ise
  yalnız başlatın; "Yeni state YERİNE KONDU ama sahipliği VERİLMEMİŞ olabilir" ise önce iletideki  sudo chown -R …
  komutunu çalıştırın; "yarım kalan geçici açma klasörü" ($DATA/tbrestore-*) state değildir, silinebilir. Sonra:
    sudo systemctl start $WORKER; sudo systemctl restart $DASH
  (Sahiplik, hata, boru (| head), Ctrl+C / SIGTERM / SIGHUP yolları yerel sahte VPS'te — root'la geri yükleme + servis
  kullanıcısıyla çalışan worker — denendi; gerçek VPS'te denenmedi.)
EOF
if gitc merge-base --is-ancestor "$PREV" "$OOM_FIX" 2>/dev/null && [[ "$(gitc rev-parse "$OOM_FIX" 2>/dev/null)" != "$PREV" ]]; then
  echo "  UYARI: ${PREV:0:7} bellek (OOM) onarımından ÖNCEKİ koddur — ona dönmek OOM'u GERİ GETİRİR; sağlıklı bir dönüş değildir."
fi
