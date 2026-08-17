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

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

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

    def to_dict(self) -> dict:
        return asdict(self)


class Learner:
    def __init__(self, path: Path, min_trades: int = 20):
        self.path = Path(path)
        self.min_trades = min_trades
        self.state = LearningState()
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                self.state = LearningState(**{k: v for k, v in d.items() if k in LearningState.__dataclass_fields__})
                for k in FEATURES:
                    self.state.weights.setdefault(k, 0.0)
            except Exception:  # noqa: BLE001
                self.state = LearningState()

    def save(self) -> None:
        self.state.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")

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
        return f"{setup_type}|{direction}" in self.state.blacklist

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
        # uyarlanır ajan ağırlıkları
        tot = 0.0
        new_w = {}
        for a in AGENTS:
            h, n = s.agent_hits.get(a, [0, 0])
            rate = (h + 2) / (n + 4)          # Laplace düzeltmeli isabet oranı
            new_w[a] = BASE_W[a] * (0.4 + 1.2 * rate)
            tot += new_w[a]
        s.agent_weights = {a: round(w / tot, 4) for a, w in new_w.items()}
        # 3) setup / sembol / çıkış istatistikleri
        key = f"{f.get('setup_type', '-')}|{rec.get('side', '-')}"
        st = s.setup_stats.setdefault(key, {"n": 0, "wins": 0, "sum_r": 0.0})
        st["n"] += 1; st["wins"] += int(won); st["sum_r"] += r
        sy = s.symbol_stats.setdefault(rec["symbol"], {"n": 0, "wins": 0, "sum_r": 0.0})
        sy["n"] += 1; sy["wins"] += int(won); sy["sum_r"] += r
        s.exit_stats[rec.get("exit_reason", "?")] = s.exit_stats.get(rec.get("exit_reason", "?"), 0) + 1
        s.n_trades += 1; s.n_wins += int(won); s.sum_r += r
        # kara liste: n≥10 ve beklenti < -0.1R
        s.blacklist = [k for k, v in s.setup_stats.items() if v["n"] >= 10 and v["sum_r"] / v["n"] < -0.1]
        # 4) teşhis / dersler
        lesson = self._diagnose(rec, f, won, r, right, wrong, warned, p)
        s.lessons.append(lesson)
        s.lessons = s.lessons[-200:]
        self.save()
        return lesson

    def _diagnose(self, rec, f, won, r, right, wrong, warned, p_before) -> dict:
        why: list[str] = []
        reason = rec.get("exit_reason", "")
        mae, mfe, bars = float(rec.get("mae_pct", 0)), float(rec.get("mfe_pct", 0)), int(rec.get("bars_held", 0))
        names = {"trend": "Trend", "momentum": "Momentum", "candles": "Mum yapısı", "volume": "Hacim", "levels": "Seviye", "market": "Canlı piyasa", "analog": "Geçmiş benzerlik", "edge": "Backtest"}
        if won:
            why.append(f"KÂR (+{r:.2f}R): {reason}. Destekleyen ve haklı çıkan ajanlar: {', '.join(names[a] for a in right) or '-'}.")
            if wrong:
                why.append(f"Yanılan ajanlar: {', '.join(names[a] for a in wrong)} — bunların uyarısına rağmen işlem çalıştı; ağırlıkları hafifçe düştü.")
            if reason == "hedef1" or (rec.get("tp1_done") and reason == "başa-baş stop"):
                why.append("Hedef2'ye ulaşılamadı; TP1'de yarı kapatma + başa-baş stop sermayeyi korudu.")
            if mfe > 0 and abs(mae) > mfe * 0.8:
                why.append(f"Giriş sonrası önce %{abs(mae):.1f} aleyhte gitti (MAE) — giriş zamanlaması geç; geri çekilme girişleri tercih et.")
        else:
            why.append(f"ZARAR ({r:.2f}R): {reason}. Yön destekçisi olup yanılan ajanlar: {', '.join(names[a] for a in wrong) or '-'}.")
            if warned:
                why.append(f"Karşı görüş bildiren ve haklı çıkan ajanlar: {', '.join(names[a] for a in warned)} — bunların ağırlığı arttı.")
            if reason == "likidasyon":
                why.append(f"LİKİDASYON: kaldıraç ({rec.get('leverage')}x) stop mesafesine göre fazlaydı; kaldıraç tavanı düşürülmeli.")
            elif reason == "stop" and bars <= 2:
                why.append("Stop çok hızlı geldi (≤2 bar): giriş gürültüye denk geldi ya da stop ATR'ye göre dar; kırılım girişlerinde kapanış teyidi bekle.")
            elif mfe >= 1.0 and reason == "stop":
                why.append(f"İşlem önce %{mfe:.1f} lehte gitti (MFE) ama kâr alınmadı → TP1 daha yakına çekilmeli / stop lehte hareketle daha erken başa-baş'a alınmalı.")
            nw = int(f.get("n_warnings", 0))
            if nw >= 5:
                why.append(f"Girişte {nw} uyarı vardı (YAPMA listesi kalabalık) — uyarı sayısı ≥5 iken boyut yarıya inmeli / işlem atlanmalı.")
            if float(f.get("btc_align", 0)) < 0:
                why.append("BTC risk moduna ters yönde işlemdi (baş yöneticiye karşı) — bu tür işlemler filtrelenmeli.")
            if float(f.get("funding_dir", 0)) < -0.3:
                why.append("Funding aleyhteydi (kalabalık taraftaydık) — funding aşırılığında aynı yöne girme.")
            if float(f.get("rr", 0)) < 2:
                why.append(f"R/R {f.get('rr')} düşüktü; hedef daha uzak seviyeye konmalı ya da işlem atlanmalı.")
        why.append(f"Model giriş öncesi P(kazanç)=%{p_before*100:.0f} demişti → {'isabetli' if (p_before >= 0.5) == won else 'yanıldı'}; model güncellendi.")
        return {"id": rec.get("id"), "symbol": rec["symbol"], "side": rec.get("side"), "r": r, "pnl": rec["pnl"], "won": won,
                "exit": reason, "bars": bars, "mae": mae, "mfe": mfe, "why": why, "at": rec.get("closed_at"),
                "setup": f.get("setup_type", "-"), "right": right, "wrong": wrong}

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
