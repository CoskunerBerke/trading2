# LEARNING SYSTEM

"LLM 7/24 açık kalınca öğrenir" varsayımı yok. Öğrenme istatistikseldir ve katmanlıdır (`tradingbot/learn/`):

1. **Değişmez trade hafızası** (`memory.py`): `state/trade_memory.jsonl` yalnız ekleme (opsiyonel SQLite `learning_features`/`trade_outcomes`). Girişte: bütün uzman raporları, Coin Head kararı, dissent/veto, risk kararı, plan, rejim, model/prompt sürümleri, veri tazeliği; çıkışta sonuç + fiyat yolu + postmortem.
2. **Yapılandırılmış postmortem** (`postmortem.py`): neden açıldı, hangi ajan haklı/haksız, açılmamalı mıydı, stop/hedef/boyut doğru mu, funding/kayma etkisi, kaçırılan hareket, `lesson_codes` (makine okunur) + Türkçe dersler.
3. **İstatistiksel model** (`model.py`, `calibration.py`): özellikler v2 deterministik (`features.py`, saat özellikleri kapalı), **train-only** StandardScaler, L2 lojistik (batch GD, sınıf ağırlığı, recency half-life), Platt/izotonik kalibrasyon, Brier/log-loss/ECE/reliability; hiyerarşik Beta shrinkage global→rejim→sembol/setup (`HierarchicalRate`, α=10) — az verili coin'e aşırı güven yok; kara liste kanıt gerektirir (posterior R<−0.1 ve P(mean<0)>0.8, n≥5).
4. **Gölge / karşı-olgusal** (`shadow.py`): reddedilen güçlü adaylar `state/shadow_book.json`'da; etiketleme yalnız `label_ts` geçtikten sonra o ana kadarki kapalı mumlarla, stop hedeften **önce** (muhafazakâr); `is_counterfactual=True` — gerçek fill kadar güvenilir sayılmaz.
5. **Champion/Challenger** (`registry.py`): `state/models.json`; challenger yalnız kapıyı (holdout ≥ 30, ECE ≤ 0.15, log-loss ve Brier iyileşmesi, beklenti şampiyonun altında değil) geçerse; PAPER'da otomatik terfi opsiyonel, TESTNET/SHADOW/LIVE'da **manuel** (`validate-model --promote --operator <ad>`); drift kontrolü (log-loss/Brier/hit-rate/özellik kayması).
6. **Retrieval** (`retrieval.py`): yapılandırılmış filtreler + standardize kosinüs benzerliği; harici vektör DB yok (SQLite FTS5 opsiyonel).

`LearnerV2.predict` önsel ile modeli n_eff'e göre harmanlar (ani geçiş yok). Legacy `learning.py` korunur; `legacy_bridge` v1 durumunu kayıpsız alır. Etiket: R bazlı WIN/LOSS/SCRATCH (|R|<0.25 scratch), pnl>0 değil.
