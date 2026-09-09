# Açık kusur — kapının olasılığı ile kazanç büyüklüğü aynı olasılıktan gelmiyor

**Durum:** ÖLÇÜLDÜ, **ONARILMADI**. Bilerek bu turda dokunulmadı; gerekçe aşağıda.
**Ölçüm tabanı:** `1c4cba1` + dondurulmuş 2797 aday · `scripts/measure_loss_magnitude_v1.py`

---

## 1. Mekanizma

`engine_v3._assess_opportunities` şunu yapar:

```python
stats = hierarchical_expectancy(learner=self.learner2, symbol=sym, ...)   # W'yi KENDİ p'siyle çözer
if d.p_win:                       # "kalibre model tahmini onceliklidir"
    stats["p_win"] = ...d.p_win   # olasılık DEĞİŞTİRİLİR
a = assess(..., p_win=stats["p_win"], avg_win_r=stats["avg_win_r"], ...)  # W AYNEN kalır
```

`avg_win_r`, `realised_win_r = (exp_r + (1−p_h)·L) / p_h` ile **hiyerarşik** `p_h`'den geri
çözülür; sonra brüt beklenti **kalibre** `p_c` ile hesaplanır. İki olasılık aynı değildir ve
üstelik iki farklı hiyerarşi yaprağından gelir:

| Büyüklük | Yaprak anahtarı |
|---|---|
| `learner.win` → `p_h` | `symbol\|setup` |
| `learner.exp_r` → `exp_r` | `setup\|side` |
| `d.p_win` → `p_c` | LearnerV2 kalibre tahmini (ayrı model) |

## 2. Ölçüm (2773 kırpılmamış aday)

| | Değer |
|---|---:|
| `p_c` / `p_h` oranı (ortalama) | **%47** |
| `p_c − p_h` ortalama / medyan | −0.338 / −0.340 |
| Tutarlı tek olasılık kullanılsaydı edge farkı (ortalama) | **−0.266 R** |
| medyan / \|Δ\| p95 | −0.176 R / 0.631 R |
| `tradeable` kararı değişen aday | **666 / 2773 = %24** |
| Bugünkü karma biçim → `tradeable` | **32** |
| Tutarlı tek olasılık → `tradeable` | **698** |

## 3. Bunun `1c4cba1` için anlamı

Kapı sırası onarımı işlem yapılabilir aday oranını %91.7 → %1.9'a düşürmüştü (52/2797 — bu sayı
bağımsız olarak yeniden üretildi ve dağıtım raporundaki oranla birebir tutuyor). **Bu düşüşün
önemli bir kısmı, doğru olasılığın kullanılmasından değil, tutarsızlığın kendisinden gelir.**
Tutarlı biçimde tek bir olasılık kullanılsaydı 698 aday işlem yapılabilir kalırdı, 32 değil.

Bu, `1c4cba1`'in yanlış olduğu anlamına **gelmez** — kapının kalibre olasılığı kullanması
doğrudur. Ama "işlem sayısının sıfıra inmesi onarımın kendisinin sonucudur" ifadesi eksiktir ve
kayda böyle geçmelidir.

## 4. Neden bu turda onarılmadı

**Saf çözüm bir gerilemedir.** Kazanç büyüklüğünü kalibre `p_c` ile geri çözmek
(`W = (exp_r + (1−p_c)·L)/p_c`) `w = 1`'de `gross = exp_r` yapar ve **kalibre olasılığı
formülden tamamen sadeleştirir** — yani `1c4cba1`'in kapattığı kusuru başka biçimde geri açar.

**Asıl kusur daha derinde:** `avg_win_r` hiç ölçülmüyor, geri çözülüyor. Doğru onarım, kayıp
büyüklüğünde bu turda yapılanın aynısıdır: **kazanç büyüklüğünü de learner'ın kendi kapanışlarından
DOĞRUDAN ölçmek** (`loss_r` düğümünün ikizi olarak bir `win_r` düğümü). O zaman `p` ve `W` iki
bağımsız ölçüm olur, ters çözüm ortadan kalkar ve tutarsızlık kendiliğinden kapanır — kalibre
olasılığı sadeleştirmeden.

Destekleyen gözlem: bugünkü karma `avg_win_r` ortalaması **1.917**, gerçekleşmiş ortalama kazanç
ise **1.908 R** (n=7). Yani bugünkü değer kazara doğru büyüklüğe oturuyor; onu doğrudan ölçmek
seviyeyi bozmadan türetimi düzeltir.

**Neden ayrı tur:** bu, işlem seçiciliğinde 32 ↔ 698 arası bir kaldıraçtır. Üç başka değişiklikle
aynı dağıtıma konursa hiçbirinin etkisi ayrıştırılamaz. Kendi ön-kayıtlı kabul ölçütleriyle,
ayrı bir challenger olarak değerlendirilmelidir.

## 5. Sıradaki adım (öneri, uygulanmadı)

1. `LearnerV2`'ye `win_r` hiyerarşik düğümü (yalnız pozitif R), `loss_r` ile aynı geriye uyumlu
   göç ve aynı belirsizlik daraltması.
2. `hierarchical_expectancy` ters çözümü bırakır; `W` ölçülen değer, `L` ölçülen değer, `p`
   kalibre değer olur — üçü de bağımsız.
3. Ön-kayıtlı kabul ölçütü: aday sayısındaki değişim ölçülür, ama kabul **sonuç kalitesine**
   bağlanır (kalibrasyon Brier'i, taban orana karşı), aday sayısına değil.
4. n=7 kazanç ile `win_r` düğümü çok gürültülüdür: daraltma ağırlığı 1.0'a değil **plan
   geometrisine** (`plan.expected_r`) doğru olmalıdır, yani bugünkü soğuk başlangıç davranışı
   korunur.
