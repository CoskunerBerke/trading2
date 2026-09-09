# Bellek doğrulaması V1 — OOM sorusunu tek turla değil, günlerle kapatmak

**Durum:** ölçüm altyapısı hazır ve kalıcı (systemd timer); **uzun süreli doğrulama HENÜZ
YAPILMADI** — kurulduktan sonra en az bir tam gün örnek birikmeden "OOM kapandı" denmez.

---

## 1. Neden tek tur yetmez

Worker 2026-09-09 15:57:25Z'de 4 GiB cgroup sınırında OOM ile öldürüldü
(`status=9/KILL`, `MemoryPeak=3.87 GiB`). Akış + memo onarımından sonra **tek turda** tepe
4.15 GB → 3.78 GB ölçüldü. Bu, kazancın yönünü gösterir; **sınırın aşılmayacağını göstermez**:

* tepe 3.78 GB, 4 GiB tavanın **%92.4**'ü — pay yaklaşık 300 MB,
* `entry_snapshot.jsonl` büyümeye devam ediyor (rotasyon hâlâ kapalı),
* dokunulmamış iki tur içi sıçrama duruyor: `engine_v3.py` `path_rows` ≈127 MB ve
  `position_path.paths_by_trade()` ≈130 MB (tur başına iki kez),
* farklı fonksiyonların **ayrı zamanlardaki** tepe kazançları toplanıp tek bir "süreç kazancı"
  gibi sunulamaz.

## 2. Kalıcı ölçüm — oturuma bağlı değil

| Dosya | İş |
|---|---|
| `deploy/memory_probe.sh` | TEK örnek alır, bir JSONL satırı ekler, çıkar |
| `deploy/tradingbot-memprobe.service` | `Type=oneshot`, yalnız okur, yalnız log dizinine yazar |
| `deploy/tradingbot-memprobe.timer` | 5 dakikada bir, `Persistent=true` (yeniden başlatmayı aşar) |
| `scripts/memory_probe_report.py` | biriken örnekleri özetler ve **yetersiz pencereyi açıkça söyler** |

Sürekli çalışan yeni bir daemon yoktur. Timer systemd'ye aittir; bu sohbet, SSH oturumu ya da
herhangi bir kullanıcı süreci kapandığında **çalışmaya devam eder**.

Örnek başına kaydedilenler (**bütün bellek alanları BAYT**, tek birim):

* `memory.max`, `memory.current`, `memory.peak`, `memory.swap.current` — cgroup'un kendi sayaçları,
  yolu `systemctl show -p ControlGroup` ile alınır (slice adı **varsayılmaz**),
* `memory.events`: `low / high / max / oom / oom_kill` — `max` sınıra dayanma, `oom_kill` gerçek
  öldürmedir; ikisi ayrı raporlanır,
* süreç `VmRSS` / `VmSize` (`/proc/<MainPID>/status`),
* `ActiveState`, `SubState`, **`NRestarts`**, `ActiveEnterTimestamp`,
* `health.json`'dan son tur süresi ve `heartbeat.json` ham satırı,
* `entry_snapshot.jsonl` ve `position_path.jsonl` boyutları (büyüme hızı için).

## 3. Kurulum (VPS'te bir kez)

```bash
cd /opt/tradingbot/app
sudo install -m 0755 deploy/memory_probe.sh /opt/tradingbot/memory_probe.sh
sudo cp deploy/tradingbot-memprobe.service deploy/tradingbot-memprobe.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tradingbot-memprobe.timer
sudo systemctl start tradingbot-memprobe.service      # ilk örneği hemen al
systemctl list-timers tradingbot-memprobe.timer --no-pager
tail -n 2 /opt/tradingbot/data/logs/memory_probe.jsonl
```

## 4. Okuma

```bash
/opt/tradingbot/venv/bin/python /opt/tradingbot/app/scripts/memory_probe_report.py --rows 12
```

Rapor şunu söyler: pencere uzunluğu ve örnek sayısı, cgroup sınırı, `memory.current` tepesi ve
sınırın yüzdesi, `memory.peak`, RSS, `memory.events` değişimi, `NRestarts` kümesi, tur süreleri,
`entry_snapshot.jsonl` büyüme hızı (MiB/saat).

Hüküm satırları:

* `oom_kill` arttıysa → **onarım yetersiz**,
* yalnız `events.max` arttıysa → sınıra dayanma var, öldürme yok,
* pencere 6 saatten kısaysa → **UYARI: uzun süreli doğrulama için kısa**.

## 5. Kabul ölçütü (sonuçlara bakılmadan yazıldı)

OOM işi ancak şu üçü birden sağlanınca kapanır:

1. **≥ 7 gün** kesintisiz örnek,
2. pencerede `memory.events.oom_kill` **artışı 0** ve worker `NRestarts` **artışı 0**,
3. `memory.peak` sınırın **%90'ının altında** kalmış olması.

Bunlardan biri sağlanmazsa iş açık kalır ve sıradaki adım `path_rows` /
`paths_by_trade()` yollarının akışa çevrilmesi ile `entry_snapshot.jsonl` rotasyonudur.

## 6. Kod tarafında bu turda kapatılan iki yaşam döngüsü boşluğu

`tests/test_snapshot_memory_v2.py` (6 test) — V1'in bırakmadığı iki soru:

* **Aday dosyası tur içinde, iki tüketicinin arasında büyürse**: ikinci tüketici bayat memodan
  beslenmiyor; imza `(mtime_ns, size)` boyut değişimine mtime donmuş olsa bile tepki veriyor;
  beş ardışık yeniden ayrıştırma sonrası tutulan bellek **tek grafiğin altında** kalıyor, yani
  eski grafikler gerçekten bırakılıyor (ölçüm `tracemalloc` ile, orantı proxy'siyle değil).
* **Tur istisnayla kesilirse**: üç ardışık çöken tur memoyu üst üste yığmıyor ve ilk başarılı tur
  asılı memoyu bırakıyor; `_drop_entry_snapshot_cache()` istisna sızdırmıyor.
* İmza `stat()` hatası yüzünden çözülemezse memo **devre dışı** kalır ve bayat sonuç servis edilmez.
