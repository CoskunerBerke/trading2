"""Yapılandırılmış postmortem — işlem kapanınca 'neden açıldı / hangi kanıt doğruydu / açılmamalı mıydı' analizi. Saf fonksiyon."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .labels import label_outcome

POSTMORTEM_VERSION = "pm-v2"
NAMES = {"trend": "Trend", "momentum": "Momentum", "candles": "Mum yapısı", "volume": "Hacim", "levels": "Seviye", "market": "Canlı piyasa",
         "analog": "Geçmiş benzerlik", "edge": "Backtest"}


@dataclass
class Postmortem:
    trade_id: str
    symbol: str
    outcome_class: str
    r_multiple: float
    why_opened: list[str] = field(default_factory=list)
    evidence_right: list[str] = field(default_factory=list)
    evidence_wrong: list[str] = field(default_factory=list)
    agents_right: list[str] = field(default_factory=list)
    agents_wrong: list[str] = field(default_factory=list)
    dissent_was_right: bool | None = None
    should_not_have_opened: bool = False
    should_not_reasons: list[str] = field(default_factory=list)
    stop_ok: bool | None = None
    target_ok: bool | None = None
    size_ok: bool | None = None
    funding_impact: str = ""
    slippage_killed_edge: bool = False
    better_entry_possible: bool | None = None
    missed_max_move_pct: float = 0.0
    alternative_exit: dict[str, Any] = field(default_factory=dict)
    lesson_codes: list[str] = field(default_factory=list)
    lesson_text_tr: list[str] = field(default_factory=list)
    labels: dict[str, Any] = field(default_factory=dict)
    #: SINIRLI ileri politika — deterministik gelecek iddiası DEĞİL: aynı koşul tekrarında
    #: güvenin hangi yönde ve ne kadar (bounded) oynayacağı + sert kapıların yine geçmesi şartı.
    next_time_policy: dict[str, Any] = field(default_factory=dict)
    postmortem_version: str = POSTMORTEM_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


def structured_postmortem(rec: dict[str, Any], decision_snapshot: dict[str, Any] | None = None) -> Postmortem:
    lab = label_outcome(rec)
    f = rec.get("features") or {}
    snap = decision_snapshot or {}
    side = str(rec.get("side", f.get("direction", "")))
    won = lab["won"]
    r = lab["r_multiple"]
    pm = Postmortem(trade_id=str(rec.get("id", "")), symbol=str(rec.get("symbol", "")), outcome_class=lab["outcome_class"], r_multiple=r, labels=lab)
    # ajan haklı/haksız (yönle hizalı bias)
    right, wrong, warned = [], [], []
    for a in NAMES:
        b = float(f.get(f"bias_{a}", 0.0) or 0.0)
        if abs(b) < 0.15:
            continue
        supported = b > 0
        hit = (supported and r > 0) or ((not supported) and r <= 0)
        (right if hit else wrong).append(a)
        if not supported and r <= 0:
            warned.append(a)
    pm.agents_right, pm.agents_wrong = right, wrong
    pm.why_opened = [f"Yön {side}, setup {rec.get('setup_type', f.get('setup_type', '-'))}, tetik: {rec.get('trigger_text', '')}"[:200]]
    if snap.get("consensus_score") is not None:
        pm.why_opened.append(f"Konsensüs {snap['consensus_score']:+.2f} (güven {snap.get('consensus_conf', 0):.2f}), dissent: {', '.join(snap.get('dissent', []) or []) or '-'}")
    pm.evidence_right = [f"{NAMES[a]} yönü destekledi ve haklı çıktı" for a in right] if won else [f"{NAMES[a]} karşı çıktı ve haklı çıktı" for a in warned]
    pm.evidence_wrong = [f"{NAMES[a]} yanılttı" for a in wrong]
    if snap.get("dissent"):
        pm.dissent_was_right = (not won)
    codes: list[str] = []
    text: list[str] = []
    reason = lab["exit_quality"]
    mae, mfe, bars = lab["mae_pct"], lab["mfe_pct"], lab["bars_held"]
    if won:
        text.append(f"KÂR (+{r:.2f}R): {rec.get('exit_reason')}. Haklı çıkan: {', '.join(NAMES[a] for a in right) or '-'}.")
        if reason == "TP1_THEN_BE":
            codes.append("TP2_NOT_REACHED"); text.append("Hedef2'ye ulaşılamadı; TP1 + gerçek başa-baş sermayeyi korudu.")
        if mfe > 0 and abs(mae) > mfe * 0.8:
            codes.append("LATE_ENTRY"); text.append(f"Önce %{abs(mae):.1f} aleyhte gitti (MAE) — giriş geç; geri çekilme girişleri tercih et.")
            pm.better_entry_possible = True
    else:
        text.append(f"ZARAR ({r:.2f}R): {rec.get('exit_reason')}. Yanılan: {', '.join(NAMES[a] for a in wrong) or '-'}.")
        if warned:
            codes.append("DISSENT_WAS_RIGHT"); text.append(f"Karşı görüş bildiren ve haklı çıkan: {', '.join(NAMES[a] for a in warned)}.")
        if reason == "LIQUIDATION":
            codes.append("LIQUIDATION_LEVERAGE"); text.append(f"LİKİDASYON: kaldıraç ({rec.get('leverage')}x) stop mesafesine göre fazlaydı.")
            pm.size_ok = False
        elif reason == "STOP" and bars and bars <= 2:
            codes.append("STOP_TOO_FAST"); text.append("Stop ≤2 barda geldi: giriş gürültüye denk geldi ya da stop ATR'ye göre dar.")
            pm.stop_ok = False
        elif mfe >= 1.0 and reason == "STOP":
            codes.append("PROFIT_NOT_TAKEN"); text.append(f"Önce %{mfe:.1f} lehte gitti ama kâr alınmadı → TP1 daha yakın / erken başa-baş.")
            pm.target_ok = False
        if int(f.get("n_warnings", 0) or 0) >= 5:
            codes.append("TOO_MANY_WARNINGS"); text.append("Girişte ≥5 uyarı vardı — boyut yarıya inmeli / atlanmalı.")
            pm.should_not_have_opened = True; pm.should_not_reasons.append("n_warnings>=5")
        if float(f.get("btc_align", 0) or 0) < 0:
            codes.append("AGAINST_BTC_MODE"); text.append("BTC risk moduna ters yönde işlemdi.")
            pm.should_not_have_opened = True; pm.should_not_reasons.append("against_btc_mode")
        if float(f.get("funding_dir", 0) or 0) < -0.3:
            codes.append("FUNDING_AGAINST"); text.append("Funding aleyhteydi (kalabalık taraftaydık).")
        if float(f.get("rr", 0) or 0) and float(f.get("rr", 0)) < 2:
            codes.append("LOW_RR"); text.append(f"R/R {f.get('rr')} düşüktü.")
    # maliyet etkileri
    fd, sd, fund = lab.get("fee_drag_r"), lab.get("slippage_drag_r"), lab.get("funding_drag_r")
    if fd is not None and fd > 0.25:
        codes.append("FEE_HEAVY"); text.append(f"Komisyon {fd:.2f}R yedi — stop mesafesi çok dar ya da boyut küçük.")
    if sd is not None and sd > 0.15:
        pm.slippage_killed_edge = True; codes.append("SLIPPAGE_HEAVY")
    if fund is not None:
        pm.funding_impact = "aleyhte" if fund > 0.05 else ("lehte" if fund < -0.05 else "ihmal edilebilir")
        if fund > 0.2:
            codes.append("FUNDING_HEAVY")
    pm.missed_max_move_pct = round(max(0.0, mfe), 3)
    if rec.get("initial_stop") or f.get("initial_stop"):
        pm.alternative_exit = {"trail_at_mfe_half": round(mfe / 2, 3)}
    if snap.get("vetoes"):
        pm.should_not_have_opened = True; pm.should_not_reasons.append("had_vetoes")
    p_before = f.get("p_win")
    if p_before is not None:
        text.append(f"Giriş öncesi P(kazanç)=%{float(p_before)*100:.0f} → {'isabetli' if (float(p_before) >= 0.5) == won else 'yanıldı'}.")
    pm.lesson_codes, pm.lesson_text_tr = list(dict.fromkeys(codes)), text
    # SINIRLI ileri politika — "bir dahaki sefere kesin LONG/SHORT gir" YOK. Yalnız:
    # aynı koşullar tekrar oluşursa güvenin yönü (increase/decrease/hold), etkinin sınırı
    # (mevcut max_fraction sözleşmesi) ve sert kapıların YİNE geçmesi şartı.
    cost_dominated = bool(fd and fd > 0.25) or bool(fund is not None and fund > 0.2) or pm.slippage_killed_edge
    if won and not cost_dominated and not pm.should_not_have_opened:
        bias = "increase"
        why_tr = "kazanç maliyete yenilmedi ve giriş kanıtı doğrulandı"
    elif (not won) or cost_dominated or pm.should_not_have_opened:
        bias = "decrease"
        why_tr = ("maliyet kârı yuttu" if cost_dominated else
                  ("giriş kanıtı yanlışlandı" if not won else "açılmaması gereken kanıt vardı"))
    else:
        bias = "hold"
        why_tr = "kanıt çelişkili — güven değişmez"
    pm.next_time_policy = {
        "confidence_bias": bias,
        "why_tr": why_tr,
        "expected_bounded_effect": "p_win ayarı mevcut max_fraction sınırında kalır; "
                                   "tek sonuç büyük sıçrama YAPAMAZ",
        "hard_gates_still_required": True,
        "deterministic_future_claim": False}
    return pm
