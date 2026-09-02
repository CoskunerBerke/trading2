"""Giriş seçiciliği sonuç atfı ve terfi kapıları (`entry_eval_v1`) — SHADOW, `applied` DAİMA False.

Kapanmış her işlem için, o işlemin GİRİŞ ANINDAKİ snapshot'ı üzerinde beş challenger ailesi ayrı
ayrı çalıştırılır ve "bu aile bu işlemi engelleseydi ne olurdu" sorusu ölçülür:

* engellenen kaybeden → kaçınılan zarar (R ve USDT),
* engellenen kazanan → kaçırılan kâr (R ve USDT),
* karşı-olgusal beklenti / profit factor / drawdown / CVaR5,
* maliyet (komisyon + funding) duyarlılığı,
* sembol / yön / rejim yoğunlaşması.

**Karşı-olgusal sözleşme.** Bir aile VETO derse o işlem AÇILMAMIŞ sayılır: karşı-olgusal R'si
`0.0`dır. Açılmamış bir işlemin "daha iyi bir fiyattan" açılabileceği VARSAYILMAZ; seçicilik
ölçümü yalnız gerçekten alınmış sonuçların alt kümesi üzerinden yapılır.

**Sızıntı sözleşmesi.** Challenger A'nın kırılma noktası gerçekleşmiş ödeme oranından türetilir;
fakat bu oran DAİMA o işlemden ÖNCE kapanmış işlemlerden hesaplanır (genişleyen pencere). Bütün
örneklemin ödeme oranını her işlemde kullanmak, sonucu bilerek eşik seçmek olurdu.

**`LEGACY_MEMORY` sözleşmesi.** `trade_memory` giriş kaydından türetilmiş snapshot'lar gözlem
olarak raporlanır fakat terfi kapılarının HİÇBİRİNE sayılmaz: bu kayıtlar bu özellik için
tasarlanmamıştır, likidite/maliyet alanları yoktur ve `candidate_id`leri karar anında değil
sonradan üretilmiştir.

**Terfi.** Bütün kapılar geçilene kadar `verdict = INSUFFICIENT_ENTRY_SAMPLE` ve `applied=false`.
Kapılar geçilse bile bu modül hiçbir şeyi aktive ETMEZ; terfi ayrı ve açık bir operatör kararıdır.
"""
from __future__ import annotations

import math
import random
from typing import Any, Iterable

from ..core import from_iso, iso, stable_id, utc_now
from .entry_challenger import ACCEPT, FAMILIES, VETO, EntryChallengerConfig, evaluate_all
from .entry_snapshot import LEGACY_MEMORY, LINKED

SCHEMA_VERSION = "entry_eval_v1"

INSUFFICIENT_ENTRY_SAMPLE = "INSUFFICIENT_ENTRY_SAMPLE"
ELIGIBLE_FOR_PAPER_BOUNDED = "ELIGIBLE_FOR_PAPER_BOUNDED"
NO_SNAPSHOT = "NO_SNAPSHOT"
NO_OUTCOME = "NO_OUTCOME"
OK = "OK"

#: --- Terfi kapıları. Hiçbiri bu görevde otomatik AÇILMAZ. ---
#: `exit_eval.GATE_MIN_CLOSED` ile AYNI eşik: iki hattın kanıt çıtası ayrışmamalı.
GATE_MIN_LINKED_CLOSES = 50
GATE_MIN_DAYS = 30
#: Bir katmanın (yön/rejim) kapsam sayılabilmesi için asgari kapanış. 10'un altında gözlenen
#: oranın %95 güven aralığının yarı genişliği ±0,31'i aşar; o katman hiçbir gerçekçi etkiyi
#: ayırt edemez, dolayısıyla "kapsandı" sayılamaz.
GATE_MIN_PER_STRATUM = 10
#: Tek sembol payı tavanı — tek bir sembolün şansı bütün kanıtı taşıyamaz.
GATE_MAX_SYMBOL_SHARE = 0.5
#: Güven aralığı için bootstrap tekrarı. Tohum veriden türetilir → AYNI veri AYNI aralık.
BOOTSTRAP_N = 2000
BOOTSTRAP_ALPHA = 0.05

#: --- Çalışma modları. `exit_executor.ALLOWED_MODES` ile AYNI fail-closed ilkesi. ---
#: Bu sürümde YALNIZ `SHADOW` izinlidir: giriş filtresi gerçek emir yolunu ETKİLEYEMEZ.
#: `PAPER_BOUNDED` ancak terfi kapıları geçildikten SONRA ve açık operatör onayıyla açılabilir;
#: config ile açılması `ConfigError` üretir (bkz. `config_v3.validate_v3`).
MODE_SHADOW = "SHADOW"
MODE_PAPER_BOUNDED = "PAPER_BOUNDED"
MODE_ACTIVE = "ACTIVE"
ALLOWED_MODES = (MODE_SHADOW,)
KNOWN_MODES = (MODE_SHADOW, MODE_PAPER_BOUNDED, MODE_ACTIVE)

#: Snapshot'ta ASLA bulunmaması gereken sonuç alanları (sızıntı denetimi).
FORBIDDEN_OUTCOME_FIELDS = ("r_multiple", "net_pnl", "pnl", "closed_at", "exit_reason",
                            "won", "outcome", "realized_r", "mfe_pct", "mae_pct")


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _s(x: Any) -> str | None:
    if x is None:
        return None
    t = str(getattr(x, "value", x))
    return t or None


def outcome_id(close_event_id: Any, candidate_id: Any) -> str:
    """Değerlendirme kimliği — aynı kapanış + aynı aday DAİMA aynı kimliği üretir.

    Tekilleştirme anahtarı budur: restart, yeniden tur ya da rapor yeniden üretimi aynı
    sonucu iki kez saydırmaz.
    """
    return stable_id("entryeval", str(close_event_id), str(candidate_id))


def _risk_usdt(close: dict[str, Any]) -> float | None:
    """İşlemin stoptaki azami zararı (USDT). Ölçülemezse `None` — sıfır KABUL EDİLMEZ.

    Önce defterin kendi `risk_snapshot`ı okunur. Yoksa `net_pnl / r_multiple` kimliğinden
    türetilir; bu bir uydurma değil, R'nin tanımının ta kendisidir.
    """
    raw = close.get("raw") if isinstance(close.get("raw"), dict) else {}
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    rs = meta.get("risk_snapshot") if isinstance(meta.get("risk_snapshot"), dict) else {}
    for k in ("max_loss_at_stop_usdt", "applied_risk_usdt", "requested_risk_usdt"):
        v = _f(rs.get(k))
        if v and v > 0:
            return v
    r, pnl = _f(close.get("r_multiple")), _f(close.get("net_pnl"))
    if r and pnl is not None and abs(r) > 1e-9:
        v = abs(pnl / r)
        return v if v > 0 else None
    return None


def _cost_r(close: dict[str, Any], risk: float | None) -> float | None:
    """Komisyon + funding toplamının R cinsinden değeri. Risk bilinmiyorsa `None`."""
    if not risk:
        return None
    fees, funding = _f(close.get("fees")), _f(close.get("funding"))
    if fees is None and funding is None:
        return None
    return (abs(fees or 0.0) + abs(funding or 0.0)) / risk


def expanding_payoff(prior_r: list[float]) -> float | None:
    """Yalnız GEÇMİŞ kapanışlardan ödeme oranı (`ort. kazanç / ort. kayıp`).

    Genişleyen penceredir: bir işlemin eşiği o işlemden SONRAKİ hiçbir sonucu göremez.
    Kazanç ya da kayıp örneği yoksa `None` döner ve challenger kendi varsayılanına düşer.
    """
    wins = [v for v in prior_r if v > 0]
    losses = [v for v in prior_r if v < 0]
    if not wins or not losses:
        return None
    aw = sum(wins) / len(wins)
    al = -sum(losses) / len(losses)
    return (aw / al) if al > 1e-9 else None


def evaluate_trade(*, snapshot: dict[str, Any], close: dict[str, Any],
                   cfg: EntryChallengerConfig, realized_payoff: float | None = None,
                   risk_budget_usdt: float | None = None) -> dict[str, Any]:
    """Tek kapanmış işlem için beş ailenin karşı-olgusal sonucu.

    `snapshot` giriş anında yazılmış kayıttır; bu fonksiyon onu DEĞİŞTİRMEZ ve içine hiçbir
    sonuç alanı yazmaz. Sonuç yalnız atıf hesabında kullanılır.
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    cev = _s(close.get("close_event_id")) or ""
    cand = _s(snap.get("candidate_id")) or ""
    link = _s(snap.get("link_status")) or LINKED
    r = _f(close.get("r_multiple"))
    pnl = _f(close.get("net_pnl"))
    risk = _risk_usdt(close)
    base = {
        "schema_version": SCHEMA_VERSION,
        "outcome_id": outcome_id(cev, cand),
        "close_event_id": cev or None,
        "trade_id": _s(close.get("trade_id")),
        "candidate_id": cand or None,
        "decision_id": _s(snap.get("decision_id")),
        "symbol": _s(close.get("symbol")) or _s(snap.get("symbol")),
        "direction": _s(snap.get("direction")) or _s(close.get("side")),
        "regime": _s(snap.get("regime")),
        "opened_at": _s(close.get("opened_at")),
        "closed_at": _s(close.get("closed_at")),
        "exit_reason": _s(close.get("exit_reason")),
        "link_status": link,
        "evidence_grade": ("PROMOTION" if link == LINKED else "OBSERVATION_ONLY"),
        "actual_r": r,
        "actual_net_pnl": pnl,
        "initial_risk_usdt": (round(risk, 6) if risk is not None else None),
        "cost_r": None,
        "policy_version": cfg.policy_version,
        "config_id": cfg.config_id,
        "payoff_used": (round(realized_payoff, 6) if realized_payoff is not None else None),
        "applied": False,
    }
    cr = _cost_r(close, risk)
    base["cost_r"] = (round(cr, 6) if cr is not None else None)
    if not cand:
        base.update({"status": NO_SNAPSHOT, "families": {},
                     "note_tr": "Giriş snapshot'ı yok — karşı-olgusal sonuç ÜRETİLMEDİ."})
        return base
    if r is None:
        base.update({"status": NO_OUTCOME, "families": {},
                     "note_tr": ("Kapanış R'si ölçülemedi — atıf yapılamaz. Sıfır R "
                                 "VARSAYILMAZ.")})
        return base
    verdicts = evaluate_all(snap, cfg, realized_payoff=realized_payoff,
                            risk_budget_usdt=risk_budget_usdt)
    fams: dict[str, dict[str, Any]] = {}
    for fam, v in verdicts.items():
        blocked = (v.get("decision") == VETO)
        cf_r = 0.0 if blocked else r
        cf_pnl = 0.0 if (blocked or pnl is None) else pnl
        fams[fam] = {
            "decision": v.get("decision"),
            "reason_codes": v.get("reason_codes"),
            "blockers": v.get("blockers"),
            "evidence": v.get("evidence"),
            "blocked": blocked,
            # engellenen KAYBEDEN → kaçınılan zarar; engellenen KAZANAN → kaçırılan kâr.
            # İkisi aynı sayının işaretleri değildir ve AYRI raporlanır.
            "blocked_loser": bool(blocked and r < 0),
            "blocked_winner": bool(blocked and r > 0),
            "avoided_loss_r": round(-r, 6) if (blocked and r < 0) else 0.0,
            "missed_gain_r": round(r, 6) if (blocked and r > 0) else 0.0,
            "avoided_loss_usdt": (round(-pnl, 6) if (blocked and pnl is not None and pnl < 0)
                                  else 0.0),
            "missed_gain_usdt": (round(pnl, 6) if (blocked and pnl is not None and pnl > 0)
                                 else 0.0),
            "counterfactual_r": round(cf_r, 6),
            "counterfactual_net_pnl": (None if pnl is None else round(cf_pnl, 6)),
            "delta_r": round(cf_r - r, 6),
            # Engellenen işlemin komisyon/funding maliyeti de OLUŞMAZ; duyarlılık bunun üzerinden
            # ölçülür (bkz. `aggregate` → `cost_sensitivity`).
            "avoided_cost_r": (round(cr, 6) if (blocked and cr is not None) else 0.0),
            "applied": False,
        }
    base.update({"status": OK, "families": fams,
                 "note_tr": "SHADOW karşı-olgusal atıf; aktif giriş kararı DEĞİŞMEDİ."})
    return base


def evaluate_closes(*, closes: Iterable[dict[str, Any]], snapshots: dict[str, dict[str, Any]],
                    links: dict[str, str], cfg: EntryChallengerConfig,
                    risk_budget_usdt: float | None = None) -> list[dict[str, Any]]:
    """Bütün kanonik kapanışları snapshot'larıyla eşleyip kronolojik değerlendirir.

    `links`: `trade_id → candidate_id`. Snapshot'ı olmayan kapanış `NO_SNAPSHOT` ile geçilir;
    sahte bir snapshot türetilmez. Ödeme oranı her adımda YALNIZ o ana kadar kapanmış
    işlemlerden hesaplanır (genişleyen pencere → sızıntı yok).
    """
    rows = [c for c in (closes or []) if isinstance(c, dict)]
    rows.sort(key=lambda c: (str(c.get("closed_at") or ""), str(c.get("trade_id") or "")))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    prior_r: list[float] = []
    for c in rows:
        tid = _s(c.get("trade_id")) or ""
        cid = links.get(tid) or ""
        snap = snapshots.get(cid) if cid else None
        ev = evaluate_trade(snapshot=snap or {}, close=c, cfg=cfg,
                            realized_payoff=expanding_payoff(prior_r),
                            risk_budget_usdt=risk_budget_usdt)
        # TEKİLLEŞTİRME: aynı kapanış+aday ikinci kez sayılmaz.
        oid = str(ev.get("outcome_id"))
        if oid in seen:
            continue
        seen.add(oid)
        out.append(ev)
        rv = _f(c.get("r_multiple"))
        if rv is not None:
            prior_r.append(rv)
    return out


def _stats(values: list[float]) -> dict[str, Any]:
    """Bir R serisinin özeti. Boş seride sayı UYDURULMAZ (`exit_eval._stats` ile aynı şema)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "expectancy_r": None, "profit_factor": None,
                "profit_factor_state": "no_data", "max_drawdown_r": None,
                "tail_loss_r_cvar5": None, "payoff_ratio": None, "win_rate": None,
                "total_r": None}
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    gp, gl = sum(wins), -sum(losses)
    if gl > 0:
        pf, pf_state = gp / gl, "ok"
    elif gp > 0:
        pf, pf_state = None, "no_losses"
    else:
        pf, pf_state = None, "no_data"
    eq = peak = dd = 0.0
    for v in values:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    k = max(1, int(round(n * 0.05)))
    tail = sorted(values)[:k]
    return {
        "n": n,
        "total_r": round(sum(values), 6),
        "expectancy_r": round(sum(values) / n, 6),
        "profit_factor": round(pf, 6) if pf is not None else None,
        "profit_factor_state": pf_state,
        "max_drawdown_r": round(dd, 6),
        "tail_loss_r_cvar5": round(sum(tail) / len(tail), 6) if tail else None,
        "payoff_ratio": (round((gp / len(wins)) / (gl / len(losses)), 6)
                         if wins and losses else None),
        "win_rate": round(len(wins) / n, 6),
    }


def bootstrap_ci(deltas: list[float], *, n_boot: int = BOOTSTRAP_N,
                 alpha: float = BOOTSTRAP_ALPHA) -> dict[str, Any]:
    """Ortalama farkın yüzdelik bootstrap güven aralığı — DETERMİNİSTİK.

    Tohum verinin kendisinden türetilir; aynı seri aynı aralığı verir. Normal yaklaşım yerine
    bootstrap seçildi: R dağılımı çarpıktır ve küçük örneklemde normal aralık dar çıkar.
    """
    vals = [v for v in deltas if isinstance(v, (int, float)) and math.isfinite(v)]
    n = len(vals)
    if n < 2:
        return {"n": n, "mean": (round(vals[0], 6) if n == 1 else None),
                "lo": None, "hi": None, "excludes_zero": False, "state": "insufficient_sample"}
    mean = sum(vals) / n
    if max(vals) - min(vals) < 1e-12:
        # Bütün farklar aynı → dağılım dejenere; bootstrap bilgi eklemez.
        return {"n": n, "mean": round(mean, 6), "lo": round(mean, 6), "hi": round(mean, 6),
                "excludes_zero": abs(mean) > 1e-12, "state": "degenerate"}
    seed = int(stable_id("entryboot", [round(v, 9) for v in vals]), 16) % (2 ** 32)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(max(200, int(n_boot))):
        s = 0.0
        for _ in range(n):
            s += vals[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(math.floor((alpha / 2.0) * len(means)))]
    hi = means[min(len(means) - 1, int(math.ceil((1.0 - alpha / 2.0) * len(means))) - 1)]
    return {"n": n, "mean": round(mean, 6), "lo": round(lo, 6), "hi": round(hi, 6),
            "excludes_zero": bool(lo > 0.0 or hi < 0.0), "state": "ok",
            "n_boot": max(200, int(n_boot)), "alpha": alpha, "seed": seed}


def leakage_report(evaluations: Iterable[dict[str, Any]],
                   snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Point-in-time ve sızıntı denetimi.

    Üç şey aranır: (1) snapshot sonucu görmüş olduğunu bildiriyor mu, (2) snapshot içine
    yasak bir sonuç alanı sızmış mı, (3) snapshot kapanıştan SONRA mı yazılmış. Herhangi biri
    varsa kapı DÜŞER; "bilinmiyor" geçti sayılmaz.
    """
    sees_outcome: list[str] = []
    forbidden: list[str] = []
    wrong_stage: list[str] = []
    after_close: list[str] = []
    checked = 0
    for e in evaluations:
        if not isinstance(e, dict) or e.get("status") != OK:
            continue
        cid = str(e.get("candidate_id") or "")
        snap = snapshots.get(cid)
        if not isinstance(snap, dict):
            continue
        checked += 1
        prov = snap.get("provenance") if isinstance(snap.get("provenance"), dict) else {}
        if prov.get("sees_outcome"):
            sees_outcome.append(cid)
        if str(prov.get("written_at_stage") or "") != "RANKING":
            wrong_stage.append(cid)
        for k in FORBIDDEN_OUTCOME_FIELDS:
            if k in snap:
                forbidden.append(f"{cid}:{k}")
        st, ct = str(snap.get("ts") or ""), str(e.get("closed_at") or "")
        if st and ct:
            try:
                if from_iso(st) > from_iso(ct):
                    after_close.append(cid)
            except (ValueError, TypeError):
                pass
    clean = not (sees_outcome or forbidden or wrong_stage or after_close)
    return {"checked": checked, "clean": bool(clean and checked > 0),
            "sees_outcome": sorted(set(sees_outcome))[:20],
            "forbidden_fields": sorted(set(forbidden))[:20],
            "wrong_stage": sorted(set(wrong_stage))[:20],
            "snapshot_after_close": sorted(set(after_close))[:20],
            "state": ("ok" if clean and checked else
                      ("no_linked_snapshot" if not checked else "violation"))}


def _family_report(evs: list[dict[str, Any]], fam: str) -> dict[str, Any]:
    """Tek ailenin karşı-olgusal ekonomisi + ayrım gücü."""
    base_r = [_f(e.get("actual_r")) or 0.0 for e in evs]
    cf_r: list[float] = []
    blocked = blocked_loser = blocked_winner = 0
    avoided_r = missed_r = avoided_usdt = missed_usdt = avoided_cost_r = 0.0
    kept: list[float] = []
    missing_only = 0
    for e in evs:
        f = (e.get("families") or {}).get(fam) or {}
        r = _f(e.get("actual_r")) or 0.0
        cf_r.append(_f(f.get("counterfactual_r")) or 0.0)
        if f.get("blocked"):
            blocked += 1
            blocked_loser += int(bool(f.get("blocked_loser")))
            blocked_winner += int(bool(f.get("blocked_winner")))
            avoided_r += _f(f.get("avoided_loss_r")) or 0.0
            missed_r += _f(f.get("missed_gain_r")) or 0.0
            avoided_usdt += _f(f.get("avoided_loss_usdt")) or 0.0
            missed_usdt += _f(f.get("missed_gain_usdt")) or 0.0
            avoided_cost_r += _f(f.get("avoided_cost_r")) or 0.0
        else:
            kept.append(r)
        if f.get("blockers") and f.get("decision") == ACCEPT:
            missing_only += 1
    n = len(evs)
    n_loss = sum(1 for r in base_r if r < 0)
    n_win = sum(1 for r in base_r if r > 0)
    blocked_loser_rate = (blocked_loser / n_loss) if n_loss else None
    missed_winner_rate = (blocked_winner / n_win) if n_win else None
    youden = ((blocked_loser_rate - missed_winner_rate)
              if (blocked_loser_rate is not None and missed_winner_rate is not None) else None)
    b_stats, c_stats, k_stats = _stats(base_r), _stats(cf_r), _stats(kept)
    deltas = [c - b for c, b in zip(cf_r, base_r)]
    ci = bootstrap_ci(deltas)
    pay = k_stats.get("payoff_ratio")
    wr = k_stats.get("win_rate")
    if pay is not None and pay > 0:
        breakeven = 1.0 / (1.0 + pay)
    elif k_stats.get("n") and k_stats.get("profit_factor_state") == "no_losses":
        # Hayatta kalanların HİÇ kaybedeni yok: ödeme oranı tanımsızdır ama kırılma noktası
        # sıfırdır. Bunu "ölçülemedi" saymak, mükemmel ayrımı başarısız göstermek olurdu.
        breakeven = 0.0
    else:
        breakeven = None
    return {
        "family": fam,
        "n_evaluated": n,
        "n_blocked": blocked,
        "block_rate": round(blocked / n, 6) if n else None,
        "n_blocked_loser": blocked_loser,
        "n_blocked_winner": blocked_winner,
        "blocked_loser_rate": (round(blocked_loser_rate, 6)
                               if blocked_loser_rate is not None else None),
        "missed_winner_rate": (round(missed_winner_rate, 6)
                               if missed_winner_rate is not None else None),
        "discrimination_youden_j": (round(youden, 6) if youden is not None else None),
        "avoided_loss_r": round(avoided_r, 6),
        "missed_gain_r": round(missed_r, 6),
        "avoided_loss_usdt": round(avoided_usdt, 6),
        "missed_gain_usdt": round(missed_usdt, 6),
        "avoided_cost_r": round(avoided_cost_r, 6),
        "n_accept_by_missing_data": missing_only,
        "baseline": b_stats,
        "counterfactual": c_stats,
        "survivors": k_stats,
        "survivor_breakeven_p": (round(breakeven, 6) if breakeven is not None else None),
        "survivors_above_breakeven": (bool(wr > breakeven)
                                      if (wr is not None and breakeven is not None) else None),
        "survivors_state": k_stats.get("profit_factor_state"),
        "delta_expectancy_r": (
            round((c_stats["expectancy_r"] or 0.0) - (b_stats["expectancy_r"] or 0.0), 6)
            if (c_stats["expectancy_r"] is not None and b_stats["expectancy_r"] is not None)
            else None),
        "delta_total_r": (round((c_stats["total_r"] or 0.0) - (b_stats["total_r"] or 0.0), 6)
                          if (c_stats["total_r"] is not None and b_stats["total_r"] is not None)
                          else None),
        "delta_ci": ci,
        "applied": False,
    }


def _cost_sensitivity(evs: list[dict[str, Any]], fam: str,
                      multipliers: tuple[float, ...] = (0.5, 1.0, 2.0)) -> list[dict[str, Any]]:
    """Maliyet (komisyon+funding) çarpanına göre karşı-olgusal kazancın duyarlılığı.

    Engellenen işlemin maliyeti de OLUŞMAZ; bu yüzden maliyet büyüdükçe engellemenin değeri
    artar. Maliyeti ölçülemeyen işlem sıfır maliyetli SAYILMAZ, duyarlılıktan DIŞLANIR ve
    `n_cost_unknown` ile bildirilir.
    """
    out = []
    unknown = sum(1 for e in evs if _f(e.get("cost_r")) is None)
    for m in multipliers:
        tot = 0.0
        for e in evs:
            f = (e.get("families") or {}).get(fam) or {}
            if not f.get("blocked"):
                continue
            c = _f(e.get("cost_r"))
            if c is None:
                continue
            tot += c * m
        out.append({"cost_multiplier": m, "avoided_cost_r": round(tot, 6),
                    "n_cost_unknown": unknown})
    return out


def _concentration(evs: list[dict[str, Any]], key: str) -> dict[str, int]:
    d: dict[str, int] = {}
    for e in evs:
        k = str(e.get(key) or "UNKNOWN")
        d[k] = d.get(k, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))


def _observation_days(evs: list[dict[str, Any]]) -> float | None:
    ts = sorted(str(e.get("closed_at") or "") for e in evs if e.get("closed_at"))
    if len(ts) < 2:
        return None
    try:
        return round((from_iso(ts[-1]) - from_iso(ts[0])).total_seconds() / 86400.0, 2)
    except (ValueError, TypeError):
        return None


def _walk_forward(evs: list[dict[str, Any]], fam: str) -> dict[str, Any]:
    """Kronolojik iki katlı örneklem-dışı denetim.

    Eşikler bu veriye UYDURULMADIĞI için "eğitim" katı yoktur; buradaki soru şudur: ilk yarıda
    görülen iyileşme İKİNCİ yarıda da duruyor mu? İkinci yarı örneklem-dışı kanıttır.
    """
    rows = sorted(evs, key=lambda e: str(e.get("closed_at") or ""))
    if len(rows) < 4:
        return {"state": "insufficient_sample", "n": len(rows), "in_sample": None,
                "out_of_sample": None, "improves_out_of_sample": False}
    mid = len(rows) // 2
    a, b = _family_report(rows[:mid], fam), _family_report(rows[mid:], fam)
    d_out = b.get("delta_expectancy_r")
    return {
        "state": "ok",
        "n": len(rows),
        "split_at": str(rows[mid].get("closed_at") or ""),
        "in_sample": {"n": a["n_evaluated"], "delta_expectancy_r": a.get("delta_expectancy_r")},
        "out_of_sample": {"n": b["n_evaluated"], "delta_expectancy_r": d_out},
        "improves_out_of_sample": bool(d_out is not None and d_out > 0.0),
    }


def walk_forward_folds(evs: list[dict[str, Any]], fam: str, k: int = 3) -> dict[str, Any]:
    """Üç yönlü (k katlı) KRONOLOJİK walk-forward denetimi.

    Eşikler veriye uydurulmadığı için burada bir "eğitim" katı yoktur; sorulan şey tutarlılıktır:
    aynı sabit eşik ÜÇ ardışık zaman diliminde de aynı yönde mi çalışıyor? Bir dilimde pozitif,
    ötekilerde negatif bir iyileşme, kanıt değil gürültüdür.
    """
    rows = sorted([e for e in evs if isinstance(e, dict)],
                  key=lambda e: str(e.get("closed_at") or ""))
    k = max(2, int(k))
    if len(rows) < k * 2:
        return {"state": "insufficient_sample", "k": k, "n": len(rows), "folds": [],
                "all_folds_positive": False, "n_positive": 0}
    size = len(rows) // k
    folds = []
    for i in range(k):
        lo = i * size
        hi = len(rows) if i == k - 1 else (i + 1) * size
        rep = _family_report(rows[lo:hi], fam)
        folds.append({"fold": i + 1, "n": rep["n_evaluated"],
                      "from": str(rows[lo].get("closed_at") or ""),
                      "to": str(rows[hi - 1].get("closed_at") or ""),
                      "delta_expectancy_r": rep.get("delta_expectancy_r"),
                      "n_blocked": rep.get("n_blocked"),
                      "discrimination_youden_j": rep.get("discrimination_youden_j")})
    pos = [f for f in folds if (f.get("delta_expectancy_r") or 0.0) > 0.0]
    return {"state": "ok", "k": k, "n": len(rows), "folds": folds,
            "n_positive": len(pos), "all_folds_positive": len(pos) == len(folds)}


def _gates(fam_rep: dict[str, Any], *, n_linked: int, days: float | None,
           dir_counts: dict[str, int], regime_counts: dict[str, int],
           top_symbol_share: float | None, wf: dict[str, Any],
           folds: dict[str, Any], leakage: dict[str, Any]) -> list[dict[str, Any]]:
    """Tek ailenin terfi kapıları. `None` (ölçülemedi) DAİMA `passed=False` sayılır."""
    b, c = fam_rep.get("baseline") or {}, fam_rep.get("counterfactual") or {}
    ci = fam_rep.get("delta_ci") or {}
    covered_dirs = [k for k, v in dir_counts.items() if v >= GATE_MIN_PER_STRATUM]
    covered_regimes = [k for k, v in regime_counts.items() if v >= GATE_MIN_PER_STRATUM]
    dexp = fam_rep.get("delta_expectancy_r")
    pf_b, pf_c = b.get("profit_factor"), c.get("profit_factor")
    # `no_losses`: karşı-olgusal kümede hiç kaybeden kalmadı → PF tanımsız ama sonuç
    # baseline'dan kesinlikle iyidir. Tanımsızlığı "kapı düştü" saymak yanlış olurdu.
    pf_improved = ((pf_b is not None and pf_c is not None and pf_c > pf_b)
                   or (pf_b is not None and pf_c is None
                       and c.get("profit_factor_state") == "no_losses"
                       and (c.get("total_r") or 0.0) > 0.0))
    pf_detail = (f"{pf_b} → {pf_c}" if (pf_b is not None and pf_c is not None)
                 else f"{b.get('profit_factor_state')} → {c.get('profit_factor_state')}"
                      f" (PF {pf_b} → {pf_c})")
    dd_b, dd_c = b.get("max_drawdown_r"), c.get("max_drawdown_r")
    cv_b, cv_c = b.get("tail_loss_r_cvar5"), c.get("tail_loss_r_cvar5")
    j = fam_rep.get("discrimination_youden_j")

    def gate(code: str, passed: Any, detail: str) -> dict[str, Any]:
        return {"code": code, "passed": bool(passed), "detail": detail}

    return [
        gate("MIN_LINKED_CLOSES", n_linked >= GATE_MIN_LINKED_CLOSES,
             f"{n_linked}/{GATE_MIN_LINKED_CLOSES} (yalnız {LINKED}; {LEGACY_MEMORY} sayılmaz)"),
        gate("MIN_OBSERVATION_DAYS", days is not None and days >= GATE_MIN_DAYS,
             f"{days}/{GATE_MIN_DAYS}" if days is not None else "ölçülemedi"),
        gate("DIRECTION_COVERAGE", len(covered_dirs) >= 2,
             f"≥{GATE_MIN_PER_STRATUM} kapanışlı yön: {', '.join(covered_dirs) or 'yok'}"),
        gate("REGIME_COVERAGE", len(covered_regimes) >= 2,
             f"≥{GATE_MIN_PER_STRATUM} kapanışlı rejim: {', '.join(covered_regimes) or 'yok'}"),
        gate("POSITIVE_EXPECTANCY_IMPROVEMENT", dexp is not None and dexp > 0.0,
             f"Δbeklenti {dexp}" if dexp is not None else "ölçülemedi"),
        gate("OUT_OF_SAMPLE_IMPROVEMENT", wf.get("improves_out_of_sample"),
             f"örneklem-dışı Δ {((wf.get('out_of_sample') or {}) or {}).get('delta_expectancy_r')}"
             if wf.get("state") == "ok" else "yetersiz örnek"),
        gate("WALK_FORWARD_CONSISTENCY", folds.get("all_folds_positive"),
             (f"{folds.get('n_positive')}/{folds.get('k')} kat pozitif"
              if folds.get("state") == "ok" else "yetersiz örnek — 'bilinmiyor' geçti sayılmaz")),
        gate("CONFIDENCE_INTERVAL_EXCLUDES_ZERO", ci.get("excludes_zero"),
             f"[{ci.get('lo')}, {ci.get('hi')}] ({ci.get('state')})"),
        gate("PROFIT_FACTOR_IMPROVEMENT", pf_improved, pf_detail),
        gate("DRAWDOWN_NOT_WORSE", dd_b is not None and dd_c is not None and dd_c >= dd_b,
             f"{dd_b} → {dd_c}" if (dd_b is not None and dd_c is not None) else "ölçülemedi"),
        gate("TAIL_RISK_NOT_WORSE", cv_b is not None and cv_c is not None and cv_c >= cv_b,
             f"CVaR5 {cv_b} → {cv_c}" if (cv_b is not None and cv_c is not None) else "ölçülemedi"),
        gate("DISCRIMINATION_POSITIVE", j is not None and j > 0.0,
             f"engellenen kaybeden oranı − kaçırılan kazanan oranı = {j}"
             if j is not None else "ölçülemedi"),
        gate("SURVIVORS_ABOVE_BREAKEVEN", fam_rep.get("survivors_above_breakeven"),
             f"kazanma {((fam_rep.get('survivors') or {}) or {}).get('win_rate')} vs kırılma "
             f"{fam_rep.get('survivor_breakeven_p')}"),
        gate("SYMBOL_CONCENTRATION",
             top_symbol_share is not None and top_symbol_share <= GATE_MAX_SYMBOL_SHARE,
             (f"en yoğun sembol payı {top_symbol_share:.2f} (tavan {GATE_MAX_SYMBOL_SHARE})"
              if top_symbol_share is not None else "ölçülemedi")),
        gate("NO_LEAKAGE_POINT_IN_TIME", leakage.get("clean"),
             f"{leakage.get('state')}; denetlenen {leakage.get('checked')}"),
    ]


def aggregate(evaluations: Iterable[dict[str, Any]], *, cfg: EntryChallengerConfig,
              now=None) -> dict[str, Any]:
    """Aile bazlı özet + terfi kapıları + dürüstlük bölümü.

    Terfi kanıtı YALNIZ `LINKED` snapshot'lı kapanışlardan hesaplanır. `LEGACY_MEMORY`
    kapanışları ayrı bir gözlem bölümünde raporlanır ve HİÇBİR kapıya sayılmaz.
    """
    evs = [e for e in evaluations if isinstance(e, dict)]
    ok = [e for e in evs if e.get("status") == OK]
    linked = [e for e in ok if e.get("link_status") == LINKED]
    legacy = [e for e in ok if e.get("link_status") == LEGACY_MEMORY]
    no_snap = [e for e in evs if e.get("status") == NO_SNAPSHOT]
    no_out = [e for e in evs if e.get("status") == NO_OUTCOME]
    days = _observation_days(linked)
    sym_counts = _concentration(linked, "symbol")
    dir_counts = _concentration(linked, "direction")
    reg_counts = _concentration(linked, "regime")
    n = len(linked)
    top_share = (max(sym_counts.values()) / n) if (n and sym_counts) else None
    families: dict[str, Any] = {}
    for fam in FAMILIES:
        rep = _family_report(linked, fam) if linked else _family_report([], fam)
        rep["walk_forward"] = _walk_forward(linked, fam)
        rep["walk_forward_folds"] = walk_forward_folds(linked, fam)
        rep["cost_sensitivity"] = _cost_sensitivity(linked, fam)
        rep["observation_only"] = (_family_report(legacy, fam) if legacy else None)
        families[fam] = rep
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now or utc_now()),
        "policy_version": cfg.policy_version,
        "config_id": cfg.config_id,
        "n_evaluated": len(evs),
        "n_linked": n,
        "n_legacy_memory": len(legacy),
        "n_no_snapshot": len(no_snap),
        "n_no_outcome": len(no_out),
        "no_snapshot_trade_ids": sorted(str(e.get("trade_id")) for e in no_snap)[:50],
        "observation_days": days,
        "concentration": {"symbol": sym_counts, "direction": dir_counts, "regime": reg_counts,
                          "top_symbol_share": (round(top_share, 4)
                                               if top_share is not None else None)},
        "families": families,
        "promotion_gates": {},
        "verdict": INSUFFICIENT_ENTRY_SAMPLE,
        "auto_promotion": False,
        "applied_total": 0,
    }


def finalize(doc: dict[str, Any], *, snapshots: dict[str, dict[str, Any]],
             evaluations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """`aggregate` çıktısını sızıntı denetimi ve kapılarla tamamlar.

    Ayrı bir adım olmasının nedeni: kapılar snapshot'ların KENDİSİNİ görmek zorundadır
    (point-in-time denetimi), oysa özet yalnız değerlendirme kayıtlarını taşır.
    """
    evs = [e for e in evaluations if isinstance(e, dict)]
    linked = [e for e in evs if e.get("status") == OK and e.get("link_status") == LINKED]
    leak = leakage_report(linked, snapshots)
    conc = doc.get("concentration") or {}
    gates_by_family: dict[str, list[dict[str, Any]]] = {}
    eligible: list[str] = []
    for fam, rep in (doc.get("families") or {}).items():
        g = _gates(rep, n_linked=int(doc.get("n_linked") or 0),
                   days=doc.get("observation_days"),
                   dir_counts=dict(conc.get("direction") or {}),
                   regime_counts=dict(conc.get("regime") or {}),
                   top_symbol_share=conc.get("top_symbol_share"),
                   wf=rep.get("walk_forward") or {},
                   folds=rep.get("walk_forward_folds") or {}, leakage=leak)
        gates_by_family[fam] = g
        rep["gates_passed"] = sum(1 for x in g if x["passed"])
        rep["gates_total"] = len(g)
        rep["verdict"] = (ELIGIBLE_FOR_PAPER_BOUNDED if all(x["passed"] for x in g)
                          else INSUFFICIENT_ENTRY_SAMPLE)
        if rep["verdict"] == ELIGIBLE_FOR_PAPER_BOUNDED:
            eligible.append(fam)
    doc["leakage"] = leak
    doc["promotion_gates"] = gates_by_family
    doc["eligible_families"] = sorted(eligible)
    doc["verdict"] = (ELIGIBLE_FOR_PAPER_BOUNDED if eligible else INSUFFICIENT_ENTRY_SAMPLE)
    doc["auto_promotion"] = False
    doc["applied_total"] = 0
    doc["note_tr"] = (
        "SHADOW: hiçbir aile aktif giriş kararını etkilemez. Kapılar geçilse bile terfi "
        "OTOMATİK DEĞİLDİR; açık operatör onayı gerekir. `LEGACY_MEMORY` kapanışları yalnız "
        "gözlemdir ve hiçbir kapıya sayılmaz.")
    return doc


def build_report(*, closes: Iterable[dict[str, Any]], snapshots: dict[str, dict[str, Any]],
                 links: dict[str, str], cfg: EntryChallengerConfig,
                 risk_budget_usdt: float | None = None, now=None) -> dict[str, Any]:
    """Uçtan uca rapor: eşle → değerlendir → özetle → kapıları uygula."""
    evs = evaluate_closes(closes=closes, snapshots=snapshots, links=links, cfg=cfg,
                          risk_budget_usdt=risk_budget_usdt)
    doc = aggregate(evs, cfg=cfg, now=now)
    doc = finalize(doc, snapshots=snapshots, evaluations=evs)
    doc["evaluations"] = evs
    return doc


__all__ = ["SCHEMA_VERSION", "INSUFFICIENT_ENTRY_SAMPLE", "ELIGIBLE_FOR_PAPER_BOUNDED",
           "MODE_SHADOW", "MODE_PAPER_BOUNDED", "MODE_ACTIVE", "ALLOWED_MODES", "KNOWN_MODES",
           "NO_SNAPSHOT", "NO_OUTCOME", "OK", "GATE_MIN_LINKED_CLOSES", "GATE_MIN_DAYS",
           "GATE_MIN_PER_STRATUM", "GATE_MAX_SYMBOL_SHARE", "FORBIDDEN_OUTCOME_FIELDS",
           "outcome_id", "expanding_payoff", "evaluate_trade", "evaluate_closes",
           "bootstrap_ci", "leakage_report", "walk_forward_folds", "aggregate", "finalize",
           "build_report"]
