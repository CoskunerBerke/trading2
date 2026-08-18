"""EvidencePacket — LLM'e (ya da noop'ta deterministik Türkçe şablona) verilen yapılandırılmış kanıt paketi. LLM fiyat/istatistik uyduramaz;
paket alanları birebir engine çıktısından gelir. Kesinlik iddiası yok: koşullu olasılık + maliyet sonrası beklenti."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from ..core import stable_id


@dataclass
class EvidencePacket:
    decision_id: str
    symbol: str
    market: str
    timestamp: str
    timeframes: list[str]
    regime: str
    pattern_id: str
    side: str
    neighbor_ids: list[str]
    independent_sample_count: int
    win_rate: float
    win_rate_ci: list[float]
    net_expectancy_r: float
    expectancy_ci: list[float]
    mae_pct: float
    mfe_pct: float
    fee_slippage_funding_r: float
    recency: dict
    edge_decay: float
    evidence: list[str] = field(default_factory=list)
    counter_evidence: list[str] = field(default_factory=list)
    veto_reasons: list[str] = field(default_factory=list)
    ok: bool = False
    schema_version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def packet_from_query(res: dict, *, decision_id: str = "", timestamp: str = "", timeframes: list[str] | None = None) -> EvidencePacket:
    st = res.get("stats") or {}
    q = res.get("query") or {}
    nb = res.get("neighbors") or []
    pattern_id = stable_id("pattern", q.get("symbol"), q.get("market"), q.get("tf"), q.get("side"), q.get("window"), q.get("regime"))
    ev, cev = [], []
    n = int(res.get("n") or 0)
    if n:
        ev.append(f"{n} bağımsız benzer geçmiş olay (seviye: {res.get('levels')})")
        ev.append(f"kazanan {st.get('wins', 0)} / kaybeden {st.get('losses', 0)} / başabaş {st.get('breakeven', 0)}; ort. net {st.get('mean_net_r', 0):+.2f}R")
        if st.get("mean_gross_r", 0) > 0 and st.get("cost_drag_r", 0) > 0:
            cev.append(f"maliyet (komisyon+slippage+funding) ort. {st.get('cost_drag_r', 0):.2f}R götürüyor")
        if st.get("edge_decay", 0) < 0:
            cev.append(f"son 90 gün ortalaması bütün geçmişten {abs(st.get('edge_decay', 0)):.2f}R daha kötü")
        if st.get("max_drawdown_r", 0) > 3:
            cev.append(f"komşu dizisinde maks. düşüş {st.get('max_drawdown_r', 0):.1f}R")
    for c in res.get("codes") or []:
        cev.append(c)
    return EvidencePacket(decision_id=decision_id or stable_id("evidence", pattern_id, timestamp), symbol=str(q.get("symbol")), market=str(q.get("market")),
                          timestamp=timestamp, timeframes=list(timeframes or [q.get("tf")]), regime=str(q.get("regime")), pattern_id=pattern_id,
                          side=str(q.get("side")), neighbor_ids=[stable_id("event", x["symbol"], x["event_ts"]) for x in nb],
                          independent_sample_count=n, win_rate=float(st.get("p_win_posterior", 0.5)), win_rate_ci=list(st.get("p_win_ci") or [0, 1]),
                          net_expectancy_r=float(st.get("mean_net_r", 0.0)), expectancy_ci=list(st.get("expectancy_ci") or [0, 0]),
                          mae_pct=float(st.get("mae_pct_mean", 0.0)), mfe_pct=float(st.get("mfe_pct_mean", 0.0)),
                          fee_slippage_funding_r=float(st.get("cost_drag_r", 0.0)), recency=dict(st.get("windows") or {}),
                          edge_decay=float(st.get("edge_decay", 0.0)), evidence=ev, counter_evidence=cev, veto_reasons=list(res.get("codes") or []),
                          ok=bool(res.get("ok")))


_CODE_TR = {"INSUFFICIENT_SAMPLE": "örneklem yetersiz", "LOW_CONFIDENCE": "beklenti güven aralığının alt sınırı pozitif değil",
            "NEGATIVE_EXPECTANCY": "maliyet sonrası beklenti pozitif değil", "EDGE_DECAY": "son dönem performansı geçmiş ortalamadan belirgin kötü",
            "REGIME_MISMATCH": "komşuların çoğu farklı rejimde", "COST_ERODED_EDGE": "brüt kenar var ama maliyet siliyor", "DATA_INVALID": "veri geçersiz"}


def explain_tr(p: EvidencePacket) -> str:
    """Deterministik Türkçe açıklama (LLM noop). Kesin tahmin iddiası içermez."""
    if p.independent_sample_count == 0:
        return f"{p.symbol} ({p.market}, {p.regime}): benzer geçmiş olay bulunamadı → kanıt yok, işlem gerekçesi üretilmedi."
    lo, hi = p.win_rate_ci
    elo, ehi = p.expectancy_ci
    parts = [f"{p.symbol} ({p.market}, rejim {p.regime}, {p.side}): aynı rejimde {p.independent_sample_count} bağımsız geçmiş olay bulundu.",
             f"Komisyon, slippage ve funding sonrası P(kazanç) ≈ {p.win_rate:.2f} (%95 aralık {lo:.2f}–{hi:.2f}); ortalama net beklenti {p.net_expectancy_r:+.2f}R (aralık {elo:+.2f}…{ehi:+.2f}R).",
             f"Ortalama MAE {p.mae_pct:.2f} %, MFE {p.mfe_pct:.2f} %; maliyet ort. {p.fee_slippage_funding_r:.2f}R."]
    r90 = (p.recency.get("90d") or {}).get("mean_net_r")
    if r90 is not None:
        parts.append(f"Son 90 gün ({(p.recency.get('90d') or {}).get('n', 0)} olay) ort. {r90:+.2f}R; edge değişimi {p.edge_decay:+.2f}R.")
    if p.veto_reasons:
        parts.append("Kısıt: " + "; ".join(_CODE_TR.get(c, c) for c in p.veto_reasons) + " → boyut küçültülür ya da NO_TRADE.")
    else:
        parts.append("Kısıt yok; bu koşullu istatistik geleceği garanti etmez, yalnız maliyet sonrası beklentiyi tanımlar.")
    return " ".join(parts)


__all__ = ["EvidencePacket", "packet_from_query", "explain_tr"]
