"""ÖĞRENME MOTORU — her kağıt işlemden öğrenir.

1) Kayıt   : pozisyon açılırken o anki TÜM özellikler (8 ajanın bias/güveni, kanaat, R/R, volatilite, funding,
             emir defteri, RSI'lar, setup tipi, yön, BTC risk modu, tarayıcı skoru, saat, yok sayılan uyarı sayısı) saklanır.
2) Atıf    : kapanışta sonuç (P&L, R-katı, MAE/MFE, çıkış nedeni) ile özellikler karşılaştırılır:
             - hangi ajanlar haklıydı / hangileri yanılttı (yönle uyum × sonuç)
             - stop mu, hedef mi, likidasyon mu; erken stop / kâr alınmadı / kaldıraç fazla gibi teşhisler
             - Türkçe "NEDEN" dersleri üretilir
3) Model   : online lojistik regresyon (SGD) → P(kazanç) tahmini; her ajan için isabet oranı → uyarlanır ağırlıklar;
             setup tipi × yön için beklenti (R) → negatifse o setup kara listeye alınır.
4) Kural   : yönetici, yeterli işlem birikince (min_trades) öğrenilen ağırlıkları ve filtreleri kullanır.

Tamamen deterministik/istatistiksel; dış servis gerekmez. state/learning.json'da yaşar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

AGENTS = ["trend", "momentum", "candles", "volume", "levels", "market", "analog", "edge"]
BASE_W = {"trend": 0.22, "momentum": 0.13, "candles": 0.10, "volume": 0.09, "levels": 0.12, "market": 0.11, "analog": 0.15, "edge": 0.18}
FEATURES = ([f"bias_{a}" for a in AGENTS] + [f"conf_{a}" for a in AGENTS] +
            ["conviction", "rr", "atr_pct", "funding_dir", "ob_dir", "rsi4_dir", "n_warnings", "leverage", "scan_score", "hour_sin", "hour_cos", "is_breakout", "btc_align"])


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))


# ---------------------------------------------------------------- özellik çıkarımı
def features_from_brief(brief, chief=None, scan_score: float | None = None) -> dict:
    """CoinBrief (+chief, +tarayıcı) → düz özellik sözlüğü (giriş anı)."""
    d = 1.0 if brief.verdict == "LONG" else (-1.0 if brief.verdict == "SHORT" else 0.0)
    rep = {r.agent: r for r in brief.reports}
    f: dict = {"direction": brief.verdict, "symbol": brief.symbol, "conviction": brief.conviction / 100.0, "rr": brief.plan.rr,
               "leverage": brief.plan.suggested_leverage, "setup_type": brief.plan.entry_type or "-", "n_warnings": len(brief.dont_list),
               "scan_score": (scan_score or 0.0) / 100.0, "is_breakout": 1.0 if brief.plan.entry_type == "kırılım" else 0.0,
               "initial_stop": brief.plan.stop, "price": brief.price}
    for a in AGENTS:
        r = rep.get(a)
        f[f"bias_{a}"] = (r.bias * d) if r else 0.0          # yönle hizalı bias (+ = ajan yönü destekliyor)
        f[f"conf_{a}"] = (r.confidence / 100.0) if r else 0.0
    vol = rep.get("volatility")
    f["atr_pct"] = float(vol.metrics.get("atr_pct_1d", 0.0)) / 10.0 if vol else 0.0
    f["vol_regime"] = vol.metrics.get("regime", "-") if vol else "-"
    mk = rep.get("market")
    fr = float(mk.metrics.get("funding_pct", 0.0)) if mk else 0.0
    f["funding_dir"] = -fr * d * 10           # long iken pozitif funding aleyhte
    ob = float(mk.metrics.get("ob_imbalance", 0.5)) if mk else 0.5
    f["ob_dir"] = (ob - 0.5) * 2 * d
    mo = rep.get("momentum")
    rsi4 = float(mo.metrics.get("rsi_4h", 50)) if mo else 50.0
    f["rsi4_dir"] = (rsi4 - 50) / 50 * d
    now = datetime.now(timezone.utc)
    f["hour_sin"], f["hour_cos"] = math.sin(2 * math.pi * now.hour / 24), math.cos(2 * math.pi * now.hour / 24)
    f["btc_align"] = 0.0
    if chief is not None:
        mode = getattr(chief, "risk_mode", "NÖTR")
        f["btc_mode"] = mode
        f["btc_align"] = 1.0 if (mode == "RISK-ON" and d > 0) or (mode == "RISK-OFF" and d < 0) else (-1.0 if mode in ("RISK-ON", "RISK-OFF") else 0.0)
    f["warnings"] = list(brief.dont_list[:8])
    f["agent_stances"] = {a: (rep[a].stance if a in rep else "-") for a in AGENTS}
    return f


# ---------------------------------------------------------- setup anahtarı (İLERİYE DÖNÜK)
#: "Kurulum yok" durumunun bir kurulum ADI gibi davranmasına izin veren token'lar. Üretimde
#: `setup_type` eksik olduğunda anahtar `"-"` ile kurulur ve `-|LONG` gibi ANLAMSIZ bir kara
#: liste anahtarı doğardı: "kurulumu bilmiyoruz" ile "şu kurulum kötü" aynı şeye dönüşürdü.
INVALID_SETUP_TOKENS = frozenset({"", "-", "--", "?", "NONE", "NULL", "N/A", "NA", "UNKNOWN"})

#: Geçmişte üretilmiş geçersiz anahtarlar SESSİZCE SİLİNMEZ; bu kodla RAPORLANIR.
LEGACY_INVALID_SETUP_KEY = "LEGACY_INVALID_SETUP_KEY"


def normalize_setup_token(x: Any) -> str | None:
    """Bir setup/yön parçasını normalize eder. Ölçülemeyen parça `None` döner — `"-"` DEĞİL."""
    if x is None or isinstance(x, bool):
        return None
    t = str(x).strip()
    if t.upper() in INVALID_SETUP_TOKENS:
        return None
    return t


def setup_stat_key(setup: Any, side: Any) -> str | None:
    """`setup|yön` anahtarı. İki parçadan biri bile ölçülemediyse anahtar ÜRETİLMEZ.

    İleriye dönük düzeltme: eksik kurulum artık `"-"` ile temsil edilmez, hiç anahtar
    kurulmaz. Eski satırlar OLDUĞU GİBİ kalır (geçmiş yeniden yazılmaz).
    """
    a, b = normalize_setup_token(setup), normalize_setup_token(side)
    if a is None or b is None:
        return None
    return f"{a}|{b}"


def is_valid_setup_key(key: Any) -> bool:
    """Bir kara liste/istatistik anahtarının GERÇEK bir kurulum+yön taşıyıp taşımadığı."""
    if not isinstance(key, str) or "|" not in key:
        return False
    setup, _, side = key.partition("|")
    return (normalize_setup_token(setup) is not None
            and normalize_setup_token(side) is not None)


def legacy_invalid_setup_keys(stats: dict | None, blacklist: Iterable[str] | None = None
                              ) -> list[dict]:
    """Durumda ZATEN bulunan geçersiz anahtarları görünür kılar. Hiçbir şeyi değiştirmez."""
    bl = set(blacklist or ())
    out: list[dict] = []
    for k, v in (stats or {}).items():
        if is_valid_setup_key(k):
            continue
        out.append({"key": k, "code": LEGACY_INVALID_SETUP_KEY,
                    "n": int((v or {}).get("n", 0) or 0) if isinstance(v, dict) else 0,
                    "in_blacklist": k in bl,
                    "blocks_decisions": False,
                    "note_tr": ("Geçersiz anahtar: kurulum ölçülemediği için üretilmişti. "
                                "Silinmez; hiçbir kararı ENGELLEYEMEZ.")})
    for k in bl:
        if not is_valid_setup_key(k) and not any(o["key"] == k for o in out):
            out.append({"key": k, "code": LEGACY_INVALID_SETUP_KEY, "n": 0,
                        "in_blacklist": True, "blocks_decisions": False,
                        "note_tr": "Geçersiz kara liste anahtarı; hiçbir kararı ENGELLEYEMEZ."})
    return sorted(out, key=lambda o: str(o["key"]))


# ---------------------------------------------------------------- model
@dataclass
class LearningState:
    weights: dict = field(default_factory=lambda: {k: 0.0 for k in FEATURES})   # lojistik regresyon
    bias: float = 0.0
    n_trades: int = 0
    n_wins: int = 0
    sum_r: float = 0.0
    agent_hits: dict = field(default_factory=lambda: {a: [0, 0] for a in AGENTS})   # [doğru, toplam]
    setup_stats: dict = field(default_factory=dict)      # "kırılım|LONG": {"n":..,"wins":..,"sum_r":..}
    symbol_stats: dict = field(default_factory=dict)
    exit_stats: dict = field(default_factory=dict)       # neden → sayı
    lessons: list[dict] = field(default_factory=list)    # son dersler
    blacklist: list[str] = field(default_factory=list)   # setup|yön
    agent_weights: dict = field(default_factory=lambda: dict(BASE_W))
    updated_at: str = ""
    lr: float = 0.05
    l2: float = 0.001
    #: Ömür boyu ders sayacı — sıcak `lessons` penceresi budansa bile ARTMAYA devam eder.
    #: Arşivdeki ders sayısı `lesson_retention` altında ayrıca raporlanır.
    n_lessons_lifetime: int = 0
    #: Kayıpsız saklama durumu (LessonStore.stats) — dashboard bunu olduğu gibi gösterir.
    lesson_retention: dict = field(default_factory=dict)
    #: No-lookahead kalibrasyon kovaları (prob_semantics.CalibrationBook.stats).
    calibration: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class Learner:
    def __init__(self, path: Path, min_trades: int = 20, *, lesson_store: Any = None,
                 hot_window: int = 200):
        self.path = Path(path)
        self.min_trades = min_trades
        #: Kayıpsız ders arşivi. `None` ise BUDAMA DA YAPILMAZ — arşivsiz silme yasaktır.
        self.lesson_store = lesson_store
        self.hot_window = max(1, int(hot_window))
        #: No-lookahead kalibrasyon havuzu — `learn()` sırasında SONUÇTAN ÖNCEKİ kova okunur.
        from .learn.prob_semantics import CalibrationBook
        self.calibration = CalibrationBook()
        self.state = LearningState()
        if self.path.exists():
            import logging
            from .core import read_json
            d = read_json(self.path, default=None)      # bozuksa .bak'a duser; bozuk kopya kenara alinir (silinmez)
            if isinstance(d, dict):
                try:
                    self.state = LearningState(**{k: v for k, v in d.items() if k in LearningState.__dataclass_fields__})
                except TypeError:
                    logging.getLogger(__name__).error("learning.json semasi taninmadi - yeni state ile devam (eski dosya .bak/.corrupt olarak korunur)")
                    self.state = LearningState()
                for k in FEATURES:
                    self.state.weights.setdefault(k, 0.0)
            else:
                logging.getLogger(__name__).error("learning.json okunamadi ve yedek yok - ogrenme sifirdan (dosya .corrupt-N olarak korundu)")

    def save(self) -> None:
        self.state.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from .core import atomic_write_json
        atomic_write_json(self.path, self.state.to_dict(), keep_backup=True)

    # ------------------------------------------------------------ tahmin
    def _vec(self, f: dict) -> dict:
        return {k: float(f.get(k, 0.0) or 0.0) for k in FEATURES}

    def predict(self, f: dict) -> float:
        """P(kazanç). Yeterli işlem yoksa 0.5 civarı (bilgisiz)."""
        x = self._vec(f)
        z = self.state.bias + sum(self.state.weights.get(k, 0.0) * v for k, v in x.items())
        p = _sigmoid(z)
        if self.state.n_trades < self.min_trades:
            lam = self.state.n_trades / self.min_trades
            return 0.5 * (1 - lam) + p * lam
        return p

    @property
    def ready(self) -> bool:
        return self.state.n_trades >= self.min_trades

    def learned_agent_weights(self) -> dict | None:
        return self.state.agent_weights if self.ready else None

    def is_blacklisted(self, setup_type: str, direction: str) -> bool:
        """Geçersiz anahtar HİÇBİR KARARI ENGELLEYEMEZ.

        Kurulum ya da yön ölçülemediyse anahtar kurulmaz ve `False` döner: "ölçemedik"
        gerekçesiyle işlem elemek, ölçtüğümüzü iddia etmenin başka biçimidir.
        """
        key = setup_stat_key(setup_type, direction)
        if key is None:
            return False
        return key in self.state.blacklist

    def legacy_invalid_setup_keys(self) -> list[dict]:
        """Durumdaki geçersiz anahtarların denetim listesi (salt gözlem)."""
        return legacy_invalid_setup_keys(self.state.setup_stats, self.state.blacklist)

    # ------------------------------------------------------------ öğrenme
    def learn(self, rec: dict) -> dict:
        """Kapanan işlem kaydından öğren; ders sözlüğü döner."""
        s = self.state
        f = rec.get("features") or {}
        won = rec["pnl"] > 0
        r = float(rec.get("r_multiple", 0.0))
        y = 1.0 if won else 0.0
        # 1) lojistik regresyon SGD adımı
        x = self._vec(f)
        p = _sigmoid(s.bias + sum(s.weights[k] * v for k, v in x.items()))
        g = p - y
        for k, v in x.items():
            s.weights[k] -= s.lr * (g * v + s.l2 * s.weights[k])
        s.bias -= s.lr * g
        # 2) ajan isabetleri: yönle hizalı bias > 0.15 → "destekledi"; sonuçla karşılaştır
        right, wrong, warned = [], [], []
        for a in AGENTS:
            b = float(f.get(f"bias_{a}", 0.0))
            if abs(b) < 0.15:
                continue
            supported = b > 0
            hit = (supported and won) or ((not supported) and (not won))
            s.agent_hits.setdefault(a, [0, 0])
            s.agent_hits[a][1] += 1
            if hit:
                s.agent_hits[a][0] += 1
            (right if hit else wrong).append(a)
            if (not supported) and (not won):
                warned.append(a)
        # uyarlanır ajan ağırlıkları — ÖNCE/DELTA/SONRA açıkça kaydedilir.
        # "Trend yanıldı, ağırlığı düştü" TEK BAŞINA yetersizdir: hangi örnek sayısıyla, ne kadar
        # büzülmeyle ve kaç birim değiştiği görünmeden ağırlık değişimi denetlenemez.
        before = {a: float(s.agent_weights.get(a, BASE_W[a]) or 0.0) for a in AGENTS}
        tot = 0.0
        new_w = {}
        for a in AGENTS:
            h, n = s.agent_hits.get(a, [0, 0])
            rate = (h + 2) / (n + 4)          # Laplace düzeltmeli isabet oranı (prior kütlesi 4)
            new_w[a] = BASE_W[a] * (0.4 + 1.2 * rate)
            tot += new_w[a]
        s.agent_weights = {a: round(w / tot, 4) for a, w in new_w.items()}
        agent_contributions = []
        for a in AGENTS:
            h, n = s.agent_hits.get(a, [0, 0])
            after = float(s.agent_weights.get(a, 0.0))
            # Tek sonuç büyük sıçrama YARATAMAZ: n büyüdükçe Laplace priorunun etkisi azalır ama
            # tek adımın delta'sı da 1/(n+4) mertebesinde kalır. Bu satır bunu görünür kılar.
            agent_contributions.append({
                "agent": a, "supported": a in right or a in wrong,
                "outcome_contribution": ("HIT" if a in right else ("MISS" if a in wrong else "NEUTRAL")),
                "sample_count": int(n), "hits": int(h),
                "laplace_rate": round((h + 2) / (n + 4), 4),
                "shrinkage_prior": 4,
                "weight_before": round(before[a], 4),
                "applied_delta": round(after - before[a], 5),
                "weight_after": round(after, 4),
                "context_key": "GLOBAL",
                "evidence_quality": ("SUFFICIENT" if n >= 20 else "LOW_SAMPLE")})
        # 3) setup / sembol / çıkış istatistikleri
        # İLERİYE DÖNÜK: kurulum ya da yön ölçülemediyse anahtar KURULMAZ. Eski `-|LONG`
        # benzeri satırlar durumda kalır ve `legacy_invalid_setup_keys()` ile raporlanır.
        #
        # `setup_type` ÖLÇÜLMÜŞ bir alandır ama kaydın KÖKÜNDE durur (`TradeRecord.setup_type`),
        # `features` içinde değil. Eskiden yalnız `features` okunduğu için üretimde HER kayıt
        # `"-"` görüyor ve `-|LONG` doğuyordu. Doğru yerden okumak bir UYDURMA DEĞİL, ölçülmüş
        # bir alanın doğru adresten alınmasıdır; ölçülemezse yine anahtar kurulmaz.
        key = setup_stat_key(f.get("setup_type", rec.get("setup_type")), rec.get("side"))
        if key is not None:
            st = s.setup_stats.setdefault(key, {"n": 0, "wins": 0, "sum_r": 0.0})
            st["n"] += 1; st["wins"] += int(won); st["sum_r"] += r
        sy = s.symbol_stats.setdefault(rec["symbol"], {"n": 0, "wins": 0, "sum_r": 0.0})
        sy["n"] += 1; sy["wins"] += int(won); sy["sum_r"] += r
        s.exit_stats[rec.get("exit_reason", "?")] = s.exit_stats.get(rec.get("exit_reason", "?"), 0) + 1
        s.n_trades += 1; s.n_wins += int(won); s.sum_r += r
        # kara liste: n≥10 ve beklenti < -0.1R
        # Geçersiz anahtar kara listeye GİREMEZ (mevcut geçersiz satırlar da temizlenir:
        # kara liste türetilmiş bir görünümdür, tarihsel kayıt DEĞİLDİR — `setup_stats`
        # dokunulmadan kalır).
        s.blacklist = [k for k, v in s.setup_stats.items()
                       if is_valid_setup_key(k) and v["n"] >= 10 and v["sum_r"] / v["n"] < -0.1]
        # 4) teşhis / dersler
        lesson = self._diagnose(rec, f, won, r, right, wrong, warned, p)
        lesson["agent_contributions"] = agent_contributions
        s.lessons.append(lesson)
        s.n_lessons_lifetime = int(s.n_lessons_lifetime or 0) + 1
        self._retain_lessons()
        self.save()
        return lesson

    # ------------------------------------------------------------ kayıpsız saklama
    def _retain_lessons(self) -> None:
        """Sıcak pencereyi sınırlı tutar ama HİÇBİR dersi arşivlemeden SİLMEZ.

        Arşiv yoksa ya da mühürleme başarısızsa sıcak liste OLDUĞU GİBİ kalır (fail-closed).
        Eskiden burada `s.lessons = s.lessons[-200:]` vardı ve 200. dersten sonra her yeni
        ders bir eskisini kalıcı olarak yok ediyordu.
        """
        s = self.state
        if self.lesson_store is None:
            s.lesson_retention = {
                "hot_window": self.hot_window, "hot_lessons": len(s.lessons),
                "archived_lessons": 0, "lifetime_lessons": int(s.n_lessons_lifetime or 0),
                "archive_health": "DISABLED", "deletes_detail_on_overflow": False,
                "retrieval_scopes": ["HOT"],
                "note_tr": ("Ders arşivi kapalı — budama da YAPILMAZ; hiçbir ders silinmez. "
                            "Sıcak liste sınırsız büyür.")}
            return
        res = self.lesson_store.rotate(s.lessons)
        s.lessons = res.get("hot") or s.lessons
        s.lesson_retention = self.lesson_store.stats(hot_count=len(s.lessons))
        s.lesson_retention["lifetime_lessons"] = max(
            int(s.lesson_retention.get("lifetime_lessons") or 0), int(s.n_lessons_lifetime or 0))
        if res.get("error"):
            s.lesson_retention["last_rotation_error"] = res["error"]

    def _diagnose(self, rec, f, won, r, right, wrong, warned, p_before) -> dict:
        """GÖZLEM + araştırma HİPOTEZİ üretir; TEK işlemden politika ÇIKARMAZ.

        Önceki sürüm iki hata yapıyordu:
        1. "P(kazanç)=%29 demişti → yanıldı" — tek sonuç bir olasılık tahminini yanlışlayamaz;
           bunun yerine Brier/log-loss katkısı ve sürpriz ölçüsü yazılır (`prob_semantics`).
        2. "TP1 daha yakına çekilmeli", "boyut yarıya inmeli" gibi buyruklar — tek işlemden
           çıkarılan politika kararlarıydı. Artık bunlar `hypotheses` altında ve YALNIZ
           `OBSERVATION` kanıt seviyesinde; politika olabilmeleri için OOS doğrulaması şart.
        """
        from .learn.edge_execution import (COST_FILTER_CANDIDATE, ENTRY_QUALITY_CANDIDATE,
                                           EXIT_POLICY_CANDIDATE, NO_POLICY_CHANGE, OBSERVATION,
                                           REGIME_FILTER_CANDIDATE, classify_edge_execution)
        from .learn.labels import label_outcome
        from .learn.prob_semantics import outcome_probability_evidence, probability_note_tr
        why: list[str] = []
        hyps: list[dict] = []
        reason = rec.get("exit_reason", "")
        mae, mfe, bars = float(rec.get("mae_pct", 0)), float(rec.get("mfe_pct", 0)), int(rec.get("bars_held", 0))
        names = {"trend": "Trend", "momentum": "Momentum", "candles": "Mum yapısı", "volume": "Hacim", "levels": "Seviye", "market": "Canlı piyasa", "analog": "Geçmiş benzerlik", "edge": "Backtest"}

        def hyp(code: str, text: str) -> None:
            """Araştırılabilir soru — POLİTİKA DEĞİL. Tek işlem `OBSERVATION`ı aşamaz."""
            hyps.append({"code": code, "text_tr": text, "evidence_level": OBSERVATION,
                         "n_supporting": 1, "causal_claim": False,
                         "requires": "walk-forward OOS + execution senaryoları"})

        if won:
            why.append(f"KÂR (+{r:.2f}R): {reason}. Destekleyen ve haklı çıkan ajanlar: {', '.join(names[a] for a in right) or '-'}.")
            if wrong:
                why.append(f"Karşı taraftaki ajanlar: {', '.join(names[a] for a in wrong)} — bu sonuçta yön destekçileriyle uyuşmadılar; ağırlık etkisi örnek sayısıyla büzülür.")
            if reason == "hedef1" or (rec.get("tp1_done") and reason == "başa-baş stop"):
                why.append("Hedef2'ye ulaşılamadı; TP1'de yarı kapatma + başa-baş stop sermayeyi korudu.")
            if mfe > 0 and abs(mae) > mfe * 0.8:
                why.append(f"Giriş sonrası önce %{abs(mae):.1f} aleyhte gitti (MAE), sonra %{mfe:.1f} lehte (MFE).")
                hyp(ENTRY_QUALITY_CANDIDATE, "Geri çekilme girişi bu koşulda daha iyi olabilir mi? — ölçülmeli.")
        else:
            why.append(f"ZARAR ({r:.2f}R): {reason}. Yön destekçisi olup sonuçla uyuşmayan ajanlar: {', '.join(names[a] for a in wrong) or '-'}.")
            if warned:
                why.append(f"Karşı görüş bildiren ve sonuçla uyuşan ajanlar: {', '.join(names[a] for a in warned)}.")
            if reason == "likidasyon":
                why.append(f"LİKİDASYON: kaldıraç ({rec.get('leverage')}x) stop mesafesine göre fazlaydı.")
                hyp(EXIT_POLICY_CANDIDATE, "Bu stop mesafesinde kaldıraç tavanı düşük olmalı mı? — ölçülmeli.")
            elif reason == "stop" and bars <= 2:
                why.append("Stop ≤2 barda geldi: giriş gürültüye denk gelmiş ya da stop ATR'ye göre dar olabilir.")
                hyp(ENTRY_QUALITY_CANDIDATE, "Kırılım girişinde kapanış teyidi beklemek bu koşulda ölçülebilir fayda sağlar mı?")
            elif mfe >= 1.0 and reason == "stop":
                why.append(f"İşlem önce %{mfe:.1f} lehte gitti (MFE) ama sonuç stop oldu.")
                hyp(EXIT_POLICY_CANDIDATE, "Daha erken kısmi kâr / başa-baş bu koşulda net beklentiyi artırır mı? — büyük kazananları da kesmediği OOS'ta gösterilmeli.")
            nw = int(f.get("n_warnings", 0))
            if nw >= 5:
                why.append(f"Girişte {nw} uyarı vardı (YAPMA listesi kalabalık).")
                hyp(ENTRY_QUALITY_CANDIDATE, "Uyarı yoğunluğu eşiğiyle seçicilik artmalı mı? — selectivity challenger konusu.")
            if float(f.get("btc_align", 0)) < 0:
                why.append("BTC risk moduna ters yönde işlemdi (baş yöneticiye karşı).")
                hyp(REGIME_FILTER_CANDIDATE, "BTC moduna ters işlemler filtrelenmeli mi? — rejim kesitinde OOS ölçülmeli.")
            if float(f.get("funding_dir", 0)) < -0.3:
                why.append("Funding aleyhteydi (kalabalık taraftaydık).")
                hyp(COST_FILTER_CANDIDATE, "Funding aşırılığı bir maliyet filtresi olmalı mı? — ölçülmeli.")
            if float(f.get("rr", 0)) < 2:
                why.append(f"R/R {f.get('rr')} düşüktü.")
                hyp(ENTRY_QUALITY_CANDIDATE, "Asgari R/R eşiği yükseltilmeli mi? — işlem sıklığı kapısıyla birlikte ölçülmeli.")
        if not hyps:
            hyps.append({"code": NO_POLICY_CHANGE, "text_tr": "Bu işlem politika değişikliği gerektirmiyor.",
                         "evidence_level": OBSERVATION, "n_supporting": 1, "causal_claim": False,
                         "requires": None})

        # --- olasılık semantiği: kova ÖNCE okunur (no-lookahead), sonuç SONRA eklenir
        bucket_before = self.calibration.bucket_stats(p_before)
        prob_ev = outcome_probability_evidence(p_before, won, book=self.calibration,
                                               trade_id=rec.get("id"), as_of=rec.get("closed_at"))
        prob_ev["bucket_before_outcome"] = bucket_before
        self.calibration.add(trade_id=rec.get("id"), p=p_before, won=won,
                             label_ts=rec.get("closed_at"))
        self.state.calibration = self.calibration.stats()
        why.append(probability_note_tr(prob_ev))

        # --- edge ↔ execution gözlemi (R cinsinden MFE/MAE + capture ratio)
        # MALİYET ALANLARI `label_outcome`tan GELİR: `Learner.learn()`e gelen legacy sözlükte
        # `fee_drag_r`/`funding_drag_r`/`slippage_drag_r` YOKTUR (bunları `labels.py` hesaplar).
        # `labels` geçilmezse üç alan da None kalır ve `COST_DOMINATED` üretimde ASLA tetiklenemez
        # — 2026-08-28 VPS auditinde F00012/F00005 derslerinde bu boşluk ölçüldü.
        labels = label_outcome(rec)
        edge = classify_edge_execution(rec | {"features": f}, labels=labels,
                                       regime_at_entry=f.get("regime") or rec.get("regime"),
                                       regime_at_exit=rec.get("regime_at_exit"))
        for code in edge["hypothesis_codes"]:
            if code != NO_POLICY_CHANGE and not any(h["code"] == code for h in hyps):
                hyp(code, f"{code} — edge/execution sınıflandırmasından türedi; OOS'ta ölçülmeli.")

        return {"id": rec.get("id"), "symbol": rec["symbol"], "side": rec.get("side"), "r": r, "pnl": rec["pnl"], "won": won,
                "exit": reason, "bars": bars, "mae": mae, "mfe": mfe, "why": why, "at": rec.get("closed_at"),
                "setup": f.get("setup_type", "-"), "right": right, "wrong": wrong,
                "direction": rec.get("side"), "regime": f.get("regime") or rec.get("regime"),
                "observation": edge, "hypotheses": hyps,
                "evidence_level": OBSERVATION, "policy_status": OBSERVATION,
                "calibration": prob_ev, "causal_claim": False,
                "as_of": rec.get("closed_at")}

    # ------------------------------------------------------------ özet
    def snapshot(self) -> dict:
        s = self.state
        n = s.n_trades
        return {"trades": n, "win_rate": round(100 * s.n_wins / n, 1) if n else 0.0, "expectancy_r": round(s.sum_r / n, 3) if n else 0.0,
                "ready": self.ready, "min_trades": self.min_trades,
                "agent_hit_rates": {a: (round(100 * h / t, 1) if t else None) for a, (h, t) in s.agent_hits.items()},
                "agent_weights": s.agent_weights, "blacklist": s.blacklist, "exit_stats": s.exit_stats,
                "setup_stats": {k: {"n": v["n"], "win_rate": round(100 * v["wins"] / v["n"], 1), "exp_r": round(v["sum_r"] / v["n"], 3)} for k, v in s.setup_stats.items()},
                "top_features": sorted(((k, round(w, 3)) for k, w in s.weights.items()), key=lambda kv: -abs(kv[1]))[:10]}


def learning_notes(learner: Learner, ledger_summary: dict) -> dict[str, str]:
    """Obsidian için Learning/ notları."""
    snap = learner.snapshot()
    local = datetime.now().strftime("%Y-%m-%d %H:%M")
    health = "İYİ" if snap["expectancy_r"] > 0.2 else ("ZAYIF" if snap["trades"] >= 10 and snap["expectancy_r"] < 0 else "VERİ TOPLUYOR")
    main = ["---", "tags: [trading, learning]", "---", "# 🧠 Öğrenme Motoru", f"> {local} · durum: **{health}** · model {'AKTİF (ağırlıklar öğrenilmiş)' if snap['ready'] else f'ısınıyor ({snap['trades']}/{snap['min_trades']} işlem)'}", "",
            "## Anlık görüntü", "| İşlem | Kazanma % | Beklenti (R) | Kağıt equity | Getiri % | Açık | Komisyon |", "|---|---|---|---|---|---|---|",
            f"| {snap['trades']} | {snap['win_rate']} | {snap['expectancy_r']:+.2f} | {ledger_summary.get('equity_mtm', 0):.2f} | {ledger_summary.get('return_pct', 0):+.2f} | {ledger_summary.get('open', 0)} | {ledger_summary.get('total_fees', 0):.2f} |", "",
            "```mermaid", "flowchart LR",
            '    T["01 İŞLEM<br/>plan tetiklenir"] --> R["02 SONUÇ<br/>P&L, R, MAE/MFE"] --> V["03 İNCELEME<br/>hangi ajan haklıydı?"] --> M["04 HATA/EDGE<br/>teşhis + ders"] --> U["05 KURAL GÜNCELLE<br/>ağırlıklar, kara liste"] --> B["06 BACKTEST<br/>WFO ile yeniden doğrula"] -.-> T',
            "    classDef c fill:#1e3a5f,stroke:#64b5f6,color:#fff", "    class T,R,V,M,U,B c", "```", "",
            "## Ajan isabet oranları → öğrenilen ağırlıklar", "| Ajan | İsabet % | Ağırlık (öğrenilen) | Taban |", "|---|---|---|---|"]
    for a in AGENTS:
        hr = snap["agent_hit_rates"].get(a)
        main.append(f"| {a} | {hr if hr is not None else '-'} | {snap['agent_weights'].get(a, BASE_W[a])} | {BASE_W[a]} |")
    main += ["", "## Setup istatistikleri", "| Setup × yön | n | Kazanma % | Beklenti R | Durum |", "|---|---|---|---|---|"]
    for k, v in snap["setup_stats"].items():
        main.append(f"| {k} | {v['n']} | {v['win_rate']} | {v['exp_r']:+.2f} | {'⛔ kara liste' if k in snap['blacklist'] else '✅'} |")
    main += ["", "## Çıkış nedenleri", *[f"- {k}: {v}" for k, v in snap["exit_stats"].items()], "",
             "## Modelin en etkili özellikleri (lojistik regresyon ağırlıkları)", *[f"- `{k}`: {w:+.3f}" for k, w in snap["top_features"]], "",
             "Dersler: [[Learning/Dersler]] · İşlem günlüğü: [[Learning/Günlük]] · [[Paper Futures]] · [[Dashboard]]"]
    lessons = ["---", "tags: [trading, learning]", "---", "# 📓 Dersler — her işlemden çıkarılan NEDEN", ""]
    for l in reversed(learner.state.lessons[-60:]):
        lessons += [f"## {'✅' if l['won'] else '❌'} {l['symbol']} {l['side']} · {l['r']:+.2f}R · {l['exit']} · {l['at']}", *[f"- {w}" for w in l["why"]], ""]
    if len(learner.state.lessons) == 0:
        lessons.append("Henüz kapanan işlem yok. İlk plan tetiklenip kapandığında burada 'neden' analizi görünecek.")
    return {"Learning/Öğrenme.md": "\n".join(main), "Learning/Dersler.md": "\n".join(lessons)}
