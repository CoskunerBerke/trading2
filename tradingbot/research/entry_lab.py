"""Giriş kalitesi laboratuvarı (`research_entry_lab_v1`).

Üç ayrı iş yapar ve üçünü ASLA karıştırmaz:

1. **Skor tanımı izi (POINT_IN_TIME, aritmetik kimlik).** `entry_snapshot.jsonl` kayıtlarında
   hangi olasılığın hangi büyüklüğü ürettiği ÖLÇÜLÜR. Formül geriye dönük uydurulmaz; kayıtlı
   alanlar üzerinde kimlik SINANIR.
2. **Reddedilen fırsatların ileri simülasyonu (RETROSPECTIVE_RESEARCH).** Şampiyon-yalnız deney
   reddedilenleri göremez; burada her fırsat GERÇEK barlar üzerinde, karar anındaki plan
   geometrisiyle ve mevcut `quant/exit_challenger.simulate_exit` motoruyla yürütülür.
3. **Sıralama karşılaştırması (parametresiz).** Aynı fırsat havuzunda, aynı sermaye kısıtıyla,
   yalnız SIRALAMA ölçütü değiştirilir. Eşik FİT EDİLMEZ → train/test bölmesi gerektirmez.

Sızıntı korumaları:
* Özellikler yalnız kayıttaki karar anı alanlarından okunur; sonuç alanı GİRDİ OLAMAZ.
* Simülasyon karar barının KAPANIŞINDAN SONRAKİ barlarla başlar.
* Ufuk HERKES için eşittir; tam ufku olmayan fırsat kohort dışıdır (kısa-sonuç yanlılığı yok).
"""
from __future__ import annotations

import math
from collections import OrderedDict
from typing import Any, Callable

from ..quant.exit_challenger import CHAMPION_POLICY, simulate_exit
from . import POINT_IN_TIME, RETROSPECTIVE_RESEARCH
from .bars import MAPPING_OK, BarCache, verify_mapping

SCHEMA_VERSION = "research_entry_lab_v1"

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
         "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}

#: EŞİT ufuk (saat) — kohortun HER üyesi tam olarak bu kadar bar görür. Kanonik ortalama tutma
#: süresi ≈66 saattir; 48 saat, kohortu makul büyüklükte tutarken çoğu sonucu yakalar.
HORIZON_HOURS = 48.0
#: Dayanıklılık varyantı.
HORIZON_HOURS_ALT = 72.0

FILL_NEXT_BAR_OPEN = "NEXT_BAR_OPEN_FILL"
FILL_PLAN_TRIGGER = "PLAN_TRIGGER_FILL"
#: Plan tetiğinin gerçekleşmesi için tanınan azami bar sayısı.
TRIGGER_WINDOW_BARS = 16

#: Kural ile çözülmüş (stop/hedef gerçekten tetiklendi).
RESOLVED = "RESOLVED"
#: Ufuk sonunda piyasa fiyatıyla kapatıldı — SONUÇ TANIMLIDIR, "çözülmedi" değildir.
HORIZON_CLOSE = "HORIZON_CLOSE"
#: Kohort dışı: karar anından itibaren TAM ufuk verisi yok (kesilmiş pencere).
OUT_OF_COHORT = "OUT_OF_COHORT"
UNFILLED = "UNFILLED"
NO_BARS = "NO_BARS"
#: Sonucu olan durumlar — beklenti hesabına YALNIZ bunlar girer.
OUTCOME_STATES = (RESOLVED, HORIZON_CLOSE)


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# --------------------------------------------------------------------------- 1) fırsat havuzu
def build_opportunities(entry_rows: list[dict[str, Any]],
                        links: dict[str, str]) -> dict[str, Any]:
    """Aynı sinyalin tur tur tekrarlarını TEKİLLEŞTİRİR.

    Motorun kendi benzersiz-sinyal kuralıyla hizalı anahtar:
    `symbol|direction|timeframe|kapanmış bar|setup`. Aynı bar içindeki ilk kayıt temsilcidir
    (en erken karar anı); "hiç kabul edildi mi" bilgisi bar boyunca birleştirilir.

    Aynı barın tekrarları BAĞIMSIZ örnek DEĞİLDİR — tekilleştirme bunu düzeltir.
    """
    groups: "OrderedDict[tuple, dict[str, Any]]" = OrderedDict()
    for r in entry_rows:
        ts = _f(r.get("ts_ms"))
        tf = str(r.get("timeframe") or "4h")
        step = TF_MS.get(tf, TF_MS["4h"])
        if ts is None or not r.get("symbol") or not r.get("direction"):
            continue
        key = (str(r["symbol"]), str(r["direction"]), tf, int(ts // step * step),
               str(r.get("setup") or "-"))
        g = groups.get(key)
        if g is None:
            groups[key] = {"row": r, "n_repeats": 1, "ever_accepted": bool(r.get("baseline_accepted")),
                           "reject_reasons": {str(r.get("baseline_reject_reason") or "-")},
                           "bar_open_ms": key[3], "bar_close_ms": key[3] + step, "tf": tf}
        else:
            g["n_repeats"] += 1
            g["ever_accepted"] = g["ever_accepted"] or bool(r.get("baseline_accepted"))
            g["reject_reasons"].add(str(r.get("baseline_reject_reason") or "-"))

    out: list[dict[str, Any]] = []
    for key, g in groups.items():
        r = g["row"]
        feats = r.get("features") or {}
        cand = str(r.get("candidate_id") or "")
        out.append({
            "opportunity_key": "|".join(str(x) for x in key),
            "candidate_id": cand, "decision_id": r.get("decision_id"),
            "linked_trade_id": links.get(cand),
            "symbol": r["symbol"], "direction": r["direction"], "timeframe": g["tf"],
            "setup": r.get("setup"), "regime": r.get("regime"),
            "ts": r.get("ts"), "ts_ms": _f(r.get("ts_ms")),
            "bar_open_ms": g["bar_open_ms"], "bar_close_ms": g["bar_close_ms"],
            "entry_price": _f(r.get("entry_price")), "stop_price": _f(r.get("stop_price")),
            "decision_close": _f(feats.get("close")),
            "rr": _f(feats.get("rr")), "n_warnings": _f(feats.get("n_warnings")),
            "targets": [t for t in (_f(x) for x in (r.get("targets") or [])) if t is not None],
            "policy_version": r.get("policy_version"),
            "p_win_model": _f(r.get("p_win")),
            "p_win_prior": _f(feats.get("p_win_prior")),
            "avg_win_r": _f(r.get("avg_win_r")), "avg_loss_r": _f(r.get("avg_loss_r")),
            "gross_expectancy_r": _f(r.get("gross_expectancy_r")),
            "net_expectancy_r": _f(r.get("net_expectancy_r")),
            "conservative_net_edge_r": _f(r.get("conservative_net_edge_r")),
            "uncertainty_penalty_r": _f(r.get("uncertainty_penalty_r")),
            "sample_size": _f(r.get("sample_size")),
            "expected_r": _f(r.get("expected_r")),
            "confidence": _f(r.get("confidence")), "consensus_score": _f(r.get("consensus_score")),
            "n_dissent": _f(r.get("n_dissent")), "n_vetoes": _f(r.get("n_vetoes")),
            "n_missing": _f(r.get("n_missing")),
            "expected_cost_pct": _f(r.get("expected_cost_pct")),
            "stop_distance_pct": _f(r.get("stop_distance_pct")),
            "planned_leverage": _f(r.get("planned_leverage")),
            "size_multiplier": _f(r.get("size_multiplier")),
            "baseline_accepted": g["ever_accepted"],
            "reject_reasons": sorted(g["reject_reasons"]),
            "n_repeats": g["n_repeats"],
            "evidence_class": POINT_IN_TIME,
        })
    return {"schema_version": SCHEMA_VERSION, "opportunities": out,
            "n_raw_rows": len(entry_rows), "n_unique": len(out),
            "dedup_rule": "symbol|direction|timeframe|closed_bar|setup — ilk kayıt temsilci",
            "note": ("aynı barın tur tekrarları BAĞIMSIZ kanıt değildir; ham satır sayısı "
                     "örneklem büyüklüğü olarak KULLANILAMAZ")}


# ------------------------------------------------------------- 2) skor tanımı izi (kimlik testi)
def score_definition_trace(opps: list[dict[str, Any]]) -> dict[str, Any]:
    """`gross_expectancy_r` hangi olasılıkla üretilmiş? Kimlik SINANIR, varsayılmaz.

    Sınanan kimlik: `gross = p·W − (1−p)·|L|`. `p` için iki aday vardır:
    kayıttaki `p_win` (model çıktısı) ve `p_win_prior` (planın taşıdığı ön tahmin).
    Hangisi kimliği sağlıyorsa ekonomik kapıyı O üretmiştir.
    """
    def _check(pkey: str) -> dict[str, Any]:
        n = ok = 0
        worst = 0.0
        for o in opps:
            p, w, ell, g = o.get(pkey), o.get("avg_win_r"), o.get("avg_loss_r"), \
                o.get("gross_expectancy_r")
            if None in (p, w, ell, g):
                continue
            n += 1
            d = abs(g - (p * abs(w) - (1.0 - p) * abs(ell)))
            worst = max(worst, d)
            if d <= 1e-4:
                ok += 1
        return {"n_testable": n, "n_identity_holds": ok,
                "fraction": (round(ok / n, 6) if n else None),
                "max_abs_deviation": round(worst, 8) if n else None}

    model = _check("p_win_model")
    prior = _check("p_win_prior")
    pairs = [(o["p_win_prior"], o["p_win_model"]) for o in opps
             if o.get("p_win_prior") is not None and o.get("p_win_model") is not None]
    corr = None
    gap = None
    if len(pairs) >= 3:
        xs = [a for a, _ in pairs]
        ys = [b for _, b in pairs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sx = math.sqrt(sum((a - mx) ** 2 for a in xs) / len(xs))
        sy = math.sqrt(sum((b - my) ** 2 for b in ys) / len(ys))
        if sx > 0 and sy > 0:
            corr = round(sum((a - mx) * (b - my) for a, b in pairs) / len(pairs) / (sx * sy), 4)
        gap = round(mx - my, 4)
    driver = None
    if prior["fraction"] == 1.0 and (model["fraction"] or 0) < 1.0:
        driver = "p_win_prior"
    elif model["fraction"] == 1.0 and (prior["fraction"] or 0) < 1.0:
        driver = "p_win_model"
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": POINT_IN_TIME,
        "identity_tested": "gross_expectancy_r == p*avg_win_r - (1-p)*|avg_loss_r|",
        "with_p_win_model": model, "with_p_win_prior": prior,
        "economic_gate_driver": driver,
        "p_win_prior_mean_minus_model_mean": gap,
        "corr_prior_model": corr, "n_pairs": len(pairs),
        "note": ("iki büyüklük AYNI dağılımı tanımlamıyorsa beklenti karşılaştırması "
                 "geçersizdir; bu bölüm hangisinin kullanıldığını ÖLÇER, kusur ilan etmez"),
    }


# ------------------------------------------------- 3) reddedilen fırsatların ileri simülasyonu
def simulate_opportunity(opp: dict[str, Any], cache: BarCache, *, interval: str = "15m",
                         cutoff_ms: float, cost_per_fill_r: float,
                         fill_mode: str = FILL_NEXT_BAR_OPEN,
                         horizon_hours: float = HORIZON_HOURS,
                         fetch: bool = True) -> dict[str, Any]:
    """Karar anındaki planı GERÇEK barlar üzerinde şampiyon çıkış politikasıyla yürütür.

    İki dolum varsayımı:
    * ``NEXT_BAR_OPEN_FILL`` (birincil, MUHAFAZAKÂR) — karar barından sonraki ilk barın
      açılışında piyasa emri. Hiçbir tetik varsayımı yoktur.
    * ``PLAN_TRIGGER_FILL``  — plandaki giriş seviyesi ilk ``TRIGGER_WINDOW_BARS`` bar içinde
      GERÇEKTEN işlem gördüyse orada dolar; görmediyse fırsat ``UNFILLED``dır (tetiklenmemiştir).
      Bu varsayım daha İYİMSERdir ve yalnız dayanıklılık kontrolü olarak raporlanır.

    Her iki durumda plan GEOMETRİSİ (stop/hedef mutlak uzaklıkları) korunur — R tanımı büyümez.
    Sonuç `RETROSPECTIVE_RESEARCH`tir; orijinal snapshot DEĞİLDİR ve ileri kanıt sayılmaz.
    """
    entry, stop = opp.get("entry_price"), opp.get("stop_price")
    ts = opp.get("ts_ms")
    base = {"opportunity_key": opp["opportunity_key"], "symbol": opp["symbol"],
            "direction": opp["direction"], "fill_mode": fill_mode,
            "evidence_class": RETROSPECTIVE_RESEARCH}
    if entry is None or stop is None or ts is None or abs(entry - stop) <= 0:
        return {**base, "state": "MISSING_PLAN"}
    # Karar ANINDA hareket edilir (motor da öyle yapar); ileriye bakış yoktur çünkü yalnız
    # `ts_ms`den SONRA AÇILAN barlar kullanılır.
    start = float(ts)
    end = start + horizon_hours * 3_600_000.0
    # EŞİT UFUK KOHORTU: ufku cutoff ile KISALTMAK, hızlı çözülen fırsatları sistematik olarak
    # öne çıkarırdı (kısa-sonuç seçilim yanlılığı). Tam ufku olmayan fırsat kohort DIŞIdır.
    if end > cutoff_ms:
        return {**base, "state": OUT_OF_COHORT,
                "reason": "karar anından itibaren tam ufuk verisi yok (export cutoff)"}
    # Eşleme doğrulaması için karar barından ÖNCEKİ kapanmış bara da bakılır (yalnız doğrulama).
    verify_at = float(opp.get("bar_open_ms") or start) - 1.0
    all_rows = cache.bars(opp["symbol"], interval, verify_at - TF_MS["4h"], end, fetch=fetch)
    rows = [b for b in all_rows if b["timestamp"] >= start]
    if not rows:
        return {**base, "state": NO_BARS}

    # Enstrüman eşlemesi, kararın kullandığı KAPANMIŞ 4h barın kapanışıyla doğrulanır.
    # `entry_price` bir TETİK seviyesidir (medyan %0.8, uçta %27 uzakta) — referans OLAMAZ.
    ref = opp.get("decision_close")
    if ref is not None and all_rows:
        vmap = verify_mapping(all_rows, reference_price=ref, reference_ms=verify_at,
                              tolerance_pct=0.5)
    else:
        vmap = {"state": MAPPING_OK, "detail": "referans kapanış yok — doğrulanamadı",
                "unverified": True}
    if vmap["state"] != MAPPING_OK:
        return {**base, "state": "MAPPING_" + vmap["state"], "mapping": vmap}

    if fill_mode == FILL_NEXT_BAR_OPEN:
        fill = rows[0]["open"]
        path_rows = rows
    else:
        window = rows[:TRIGGER_WINDOW_BARS]
        idx = next((i for i, b in enumerate(window) if b["low"] <= entry <= b["high"]), None)
        if idx is None:
            return {**base, "state": UNFILLED,
                    "reason": ("plan giriş seviyesi ilk "
                               f"{TRIGGER_WINDOW_BARS} barda işlem GÖRMEDİ — tetiklenmemiş")}
        fill = entry
        path_rows = rows[idx:]
    if not path_rows:
        return {**base, "state": NO_BARS}

    # Dolum fiyatı kaysa bile plan GEOMETRİSİ korunur: stop ve hedefler aynı mutlak uzaklıkta
    # kaydırılır (R tanımı değişmez, iyimser bir R büyümesi olmaz).
    stop_fill = fill + (stop - entry)
    targets = [fill + (t - entry) for t in (opp.get("targets") or [])]

    trade = {"id": opp["opportunity_key"], "trade_id": opp["opportunity_key"],
             "symbol": opp["symbol"], "direction": opp["direction"],
             "entry_price": fill, "initial_stop": stop_fill, "targets": targets,
             "price_path": [{"high": b["high"], "low": b["low"], "close": b["close"]}
                            for b in path_rows]}
    sim = simulate_exit(trade, CHAMPION_POLICY, cost_per_fill_r=cost_per_fill_r)
    if sim is None:
        return {**base, "state": "SIM_FAILED"}
    resolved = sim["exit_reason"] in ("stop", "breakeven_stop", "target")
    return {**base, "state": (RESOLVED if resolved else HORIZON_CLOSE),
            "net_r": sim["net_r"], "gross_r": sim["gross_r"], "mfe_r": sim["mfe_r"],
            "mae_r": sim["mae_r"], "exit_reason": sim["exit_reason"],
            "bars_held": sim["bars_held"], "n_bars_available": len(path_rows),
            "fill_price": fill, "horizon_hours": horizon_hours,
            "win": sim["net_r"] > 0}


def run_opportunity_simulation(opps: list[dict[str, Any]], cache: BarCache, *,
                               cutoff_ms: float, cost_per_fill_r: float,
                               interval: str = "15m",
                               horizon_hours: float = HORIZON_HOURS,
                               fill_modes: tuple[str, ...] = (FILL_NEXT_BAR_OPEN,
                                                              FILL_PLAN_TRIGGER),
                               fetch: bool = True) -> dict[str, Any]:
    """Bütün fırsatları EŞİT ufukla ve iki dolum varsayımıyla yürütür."""
    results: dict[str, list[dict[str, Any]]] = {}
    for mode in fill_modes:
        results[mode] = [simulate_opportunity(o, cache, interval=interval, cutoff_ms=cutoff_ms,
                                              cost_per_fill_r=cost_per_fill_r, fill_mode=mode,
                                              horizon_hours=horizon_hours, fetch=fetch)
                         for o in opps]
    summary = {}
    for mode, rows in results.items():
        states: dict[str, int] = {}
        for r in rows:
            states[r["state"]] = states.get(r["state"], 0) + 1
        out = [r for r in rows if r["state"] in OUTCOME_STATES]
        wins = [r for r in out if r["win"]]
        exp = (sum(r["net_r"] for r in out) / len(out)) if out else None
        summary[mode] = {
            "n_opportunities": len(rows), "states": states,
            "n_with_outcome": len(out),
            "n_rule_resolved": states.get(RESOLVED, 0),
            "n_horizon_closed": states.get(HORIZON_CLOSE, 0),
            "cohort_fraction": (round(len(out) / len(rows), 4) if rows else None),
            "expectancy_r": (round(exp, 4) if exp is not None else None),
            "win_rate": (round(len(wins) / len(out), 4) if out else None)}
    return {"schema_version": SCHEMA_VERSION, "evidence_class": RETROSPECTIVE_RESEARCH,
            "interval": interval, "horizon_hours": horizon_hours,
            "cost_per_fill_r": cost_per_fill_r,
            "results": results, "summary": summary,
            "cohort_rule": ("yalnız karar anından itibaren TAM ufuk verisi olan fırsatlar; "
                            "kesilmiş pencere kohort DIŞI — kısa-sonuç seçilim yanlılığı yok"),
            "note": ("ufuk sonunda kapatılan pozisyon TANIMLI bir sonuçtur (piyasa fiyatı); "
                     "sıfır sayılmaz ve 'çözülmedi' diye atılmaz")}


# --------------------------------------------------------------------- 4) kalibrasyon
def calibration(opps: list[dict[str, Any]], sim_rows: list[dict[str, Any]], *,
                score_key: str, bins: int = 5) -> dict[str, Any]:
    """Bir olasılık skorunun gerçekleşen kazanma oranıyla uyumu (reliability + Brier)."""
    by_key = {r["opportunity_key"]: r for r in sim_rows if r.get("state") in OUTCOME_STATES}
    pts = []
    for o in opps:
        r = by_key.get(o["opportunity_key"])
        p = o.get(score_key)
        if r is None or p is None or r.get("win") is None:
            continue
        pts.append((float(p), 1.0 if r["win"] else 0.0))
    if len(pts) < 10:
        return {"score": score_key, "n": len(pts), "state": "INSUFFICIENT_SAMPLE"}
    pts.sort(key=lambda t: t[0])
    brier = sum((p - y) ** 2 for p, y in pts) / len(pts)
    per = max(1, len(pts) // bins)
    buckets = []
    for i in range(0, len(pts), per):
        chunk = pts[i:i + per]
        if len(chunk) < 3:
            if buckets:
                buckets[-1]["items"].extend(chunk)
            continue
        buckets.append({"items": chunk})
    out_b = []
    for b in buckets:
        ch = b["items"]
        mp = sum(p for p, _ in ch) / len(ch)
        my = sum(y for _, y in ch) / len(ch)
        out_b.append({"n": len(ch), "mean_score": round(mp, 4), "observed_win_rate": round(my, 4),
                      "gap": round(mp - my, 4),
                      "score_range": [round(ch[0][0], 4), round(ch[-1][0], 4)]})
    mean_p = sum(p for p, _ in pts) / len(pts)
    mean_y = sum(y for _, y in pts) / len(pts)
    return {"score": score_key, "n": len(pts), "state": "RUN",
            "brier": round(brier, 6), "mean_score": round(mean_p, 4),
            "observed_win_rate": round(mean_y, 4),
            "calibration_gap": round(mean_p - mean_y, 4),
            "buckets": out_b, "evidence_class": RETROSPECTIVE_RESEARCH}


# --------------------------------------------------- 5) sıralama karşılaştırması (parametresiz)
def corrected_edge(opp: dict[str, Any]) -> float | None:
    """Aynı formül, YALNIZ olasılık değişir: `p_model·W − (1−p_model)·|L| − belirsizlik`.

    Eşik FİT EDİLMEZ; bu bir yeniden hesaplamadır, yeni bir model değildir.
    """
    p, w, ell = opp.get("p_win_model"), opp.get("avg_win_r"), opp.get("avg_loss_r")
    unc = opp.get("uncertainty_penalty_r") or 0.0
    if None in (p, w, ell):
        return None
    return round(p * abs(w) - (1.0 - p) * abs(ell) - unc, 6)


def portfolio_replay(opps: list[dict[str, Any]], sim_rows: list[dict[str, Any]], *,
                     rank_key: Callable[[dict[str, Any]], float | None],
                     label: str, max_concurrent: int = 11) -> dict[str, Any]:
    """Sermaye KISITLI seçim: her karar barında en iyi K fırsat alınır.

    Neden gerekli: hayatta kalan işlemlerin beklentisi tek başına yetersizdir. Boşta kalan
    sermaye, örtüşen pozisyonlar ve kaçırılan fırsatlar bu replay'de görünür.

    Basitleştirme (AÇIK): pozisyon süresi simülasyondan gelen `bars_held` ile modellenir; kaldıraç,
    marj ve funding modellenmez — bu yüzden sonuç R cinsindendir, USDT değildir.
    """
    by_key = {r["opportunity_key"]: r for r in sim_rows if r.get("state") in OUTCOME_STATES}
    cand = []
    for o in opps:
        r = by_key.get(o["opportunity_key"])
        s = rank_key(o)
        if r is None or s is None:
            continue
        cand.append((float(o.get("bar_close_ms") or 0), -float(s), o, r))
    cand.sort(key=lambda t: (t[0], t[1], t[2]["opportunity_key"]))

    open_until: list[float] = []
    taken, skipped_capacity = [], 0
    for t_ms, _neg, o, r in cand:
        open_until = [u for u in open_until if u > t_ms]
        if len(open_until) >= max_concurrent:
            skipped_capacity += 1
            continue
        bar_ms = TF_MS.get(str(o.get("timeframe") or "4h"), TF_MS["4h"])
        hold_ms = float(r.get("bars_held") or 1) * 900_000.0
        open_until.append(t_ms + max(hold_ms, bar_ms))
        taken.append((o, r))
    rs = [x[1]["net_r"] for x in taken]
    wins = sum(1 for x in taken if x[1]["win"])
    n = len(rs)
    total = sum(rs)
    gross_p = sum(r for r in rs if r > 0)
    gross_l = -sum(r for r in rs if r < 0)
    peak = cum = mdd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {"label": label, "n_candidates": len(cand), "n_taken": n,
            "n_skipped_capacity": skipped_capacity,
            "max_concurrent": max_concurrent,
            "expectancy_r": (round(total / n, 4) if n else None),
            "total_r": round(total, 4),
            "win_rate": (round(wins / n, 4) if n else None),
            "profit_factor": (round(gross_p / gross_l, 4) if gross_l > 0 else None),
            "max_drawdown_r": round(mdd, 4),
            "n_symbols": len({x[0]["symbol"] for x in taken}),
            "evidence_class": RETROSPECTIVE_RESEARCH,
            "simplification": ("kaldıraç/marj/funding modellenmedi — sonuç R cinsindendir, "
                               "USDT portföy sonucu DEĞİLDİR")}


def verify_gate_probability_order(engine_source: str) -> dict[str, Any]:
    """Ekonomik kapı, öğrenilmiş olasılıktan ÖNCE mi hesaplanıyor? KAYNAKTAN doğrulanır.

    İddia sabitlenmez — motor kaynağı her koşuda yeniden okunur. `_assess_opportunities`
    çağrısı `d.p_win = ...` atamasından ÖNCE geliyorsa ekonomik kapı, öğrenme katmanının
    ürettiği olasılığı GÖREMEZ. Sıra değişirse bu bulgu kendiliğinden ÇÜRÜR.
    """
    lines = engine_source.splitlines()
    assess_line = gate_assign = None
    for i, ln in enumerate(lines, start=1):
        stripped = ln.strip()
        if assess_line is None and stripped.startswith("self._assess_opportunities("):
            assess_line = i
        if gate_assign is None and stripped == "d.p_win = b.p_win":
            gate_assign = i
    if assess_line is None or gate_assign is None:
        return {"state": "ANCHORS_NOT_FOUND", "assess_call_line": assess_line,
                "model_p_win_assign_line": gate_assign,
                "note": "kaynak yapısı değişmiş — bu doğrulama elle gözden geçirilmeli"}
    before = assess_line < gate_assign
    return {"state": "RUN", "assess_call_line": assess_line,
            "model_p_win_assign_line": gate_assign,
            "economic_gate_runs_before_model_p_win": before,
            "conclusion": ("ekonomik kapı öğrenilmiş p_win'i GÖRMÜYOR" if before
                           else "ekonomik kapı öğrenilmiş p_win'den SONRA çalışıyor — "
                                "RL-01 bu tarafıyla ÇÜRÜR"),
            "evidence_class": POINT_IN_TIME}


def ranking_null_test(opps: list[dict[str, Any]], sim_rows: list[dict[str, Any]], *,
                      rank_key: Callable[[dict[str, Any]], float | None], label: str,
                      max_concurrent: int, n_null: int = 200, seed: int = 7) -> dict[str, Any]:
    """Sıralama ölçütünün SEÇİM BECERİSİ var mı? Permütasyon (null) testi.

    Aynı fırsat havuzu, aynı sermaye kısıtı, aynı sonuçlar; YALNIZ sıralama rastgeleleştirilir.
    Gerçek ölçütün beklentisi null dağılımın neresinde? Yüksek yüzdelik BECERİ KANITI DEĞİLDİR —
    yalnız "rastgeleden ayırt edilebilir mi" sorusunu yanıtlar.
    """
    import random as _random
    actual = portfolio_replay(opps, sim_rows, rank_key=rank_key, label=label,
                              max_concurrent=max_concurrent)
    a = actual.get("expectancy_r")
    nulls: list[float] = []
    for i in range(n_null):
        rnd = _random.Random(seed * 1000 + i)
        scores = {o["opportunity_key"]: rnd.random() for o in opps}
        rep = portfolio_replay(opps, sim_rows, label=f"null_{i}",
                               rank_key=lambda o: scores.get(o["opportunity_key"]),
                               max_concurrent=max_concurrent)
        v = rep.get("expectancy_r")
        if v is not None:
            nulls.append(float(v))
    if a is None or len(nulls) < 20:
        return {"label": label, "state": "INSUFFICIENT_NULL", "actual": actual,
                "n_null": len(nulls)}
    nulls.sort()
    below = sum(1 for v in nulls if v < a)
    pct = below / len(nulls)
    lo = nulls[int(0.025 * len(nulls))]
    hi = nulls[min(len(nulls) - 1, int(0.975 * len(nulls)))]
    return {"label": label, "state": "RUN", "max_concurrent": max_concurrent,
            "actual_expectancy_r": a, "n_taken": actual.get("n_taken"),
            "n_skipped_capacity": actual.get("n_skipped_capacity"),
            "null_n": len(nulls), "null_median": round(nulls[len(nulls) // 2], 4),
            "null_ci95": [round(lo, 4), round(hi, 4)],
            "percentile_of_actual": round(pct, 4),
            "distinguishable_from_random": bool(a > hi or a < lo),
            "evidence_class": RETROSPECTIVE_RESEARCH,
            "note": ("yüksek yüzdelik tek başına beceri kanıtı DEĞİLDİR; küçük örneklemde "
                     "null dağılım geniştir")}


__all__ = ["FILL_NEXT_BAR_OPEN", "FILL_PLAN_TRIGGER", "HORIZON_CLOSE", "HORIZON_HOURS",
           "HORIZON_HOURS_ALT", "NO_BARS", "OUTCOME_STATES", "OUT_OF_COHORT", "RESOLVED",
           "SCHEMA_VERSION", "TF_MS", "UNFILLED", "build_opportunities",
           "calibration", "corrected_edge", "portfolio_replay", "run_opportunity_simulation",
           "ranking_null_test", "score_definition_trace", "verify_gate_probability_order", "simulate_opportunity",
           "TRIGGER_WINDOW_BARS"]
