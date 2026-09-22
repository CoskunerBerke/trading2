# -*- coding: utf-8 -*-
"""ORTAK YAPI — GERİYE BOYAMA YOK, KİMLİK SÜREKLİ, TERMİNAL DURUM KALICI (2026-09-23).

Gerçek arşiv örneklemi (docs/review/evidence-2026-09-22-shared/real_archive_structures.py) üç kusur buldu: (1) süresi
dolmuş (EXPIRED) teyitli kayıt sonraki bir kapanışla BROKEN'a dönüyordu, (2) eğik tetikli kayıtların seviyesi olaydan
sonra da her barda değişiyordu, (3) oluşan bayrak kırılış barında başka bir direk yorumuna (başka kimliğe) geçiyordu.
Bu dosya düzeltmelerin birim kanıtıdır; ileri yürüyüş denetimi (`structures.audit`) kanıt betiğiyle AYNI koddur.
Veri SENTETİKTİR (tohumlu), etiketlidir.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from structure_fixtures import _bar, breakout_bar, neutral_trend, pole_and_consolidation  # noqa: E402

from tradingbot.structures import analysis as A  # noqa: E402
from tradingbot.structures import catalog as K  # noqa: E402
from tradingbot.structures import policy as P  # noqa: E402
from tradingbot.structures.audit import walk_forward_audit  # noqa: E402

M15 = 900_000
H4 = 14_400_000
T0 = 1_780_000_000_000 // 86_400_000 * 86_400_000


def _walk(seed: int, n: int = 700, step: int = H4) -> list[dict]:
    """Tohumlu rejim değiştiren rastgele yürüyüş: trend/yatay/sıkışma evreleri + fitiller (sentetik)."""
    rnd = random.Random(seed)
    rows, px, drift, vol = [], 100.0, 0.0, 0.01
    for i in range(n):
        if i % 60 == 0:
            drift = rnd.choice([-0.004, -0.002, 0.0, 0.002, 0.004])
            vol = rnd.choice([0.004, 0.008, 0.014])
        o = px
        c = o * math.exp(drift + rnd.gauss(0, vol))
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol * 0.6)))
        lo = min(o, c) * (1 - abs(rnd.gauss(0, vol * 0.6)))
        rows.append(_bar(T0 + i * step, o, h, lo, c))
        px = c
    return rows


@pytest.mark.parametrize("seed", [3, 11, 29])
def test_walk_forward_audit_finds_no_repaint_identity_or_status_violation(seed):
    rows = _walk(seed)
    r = walk_forward_audit(rows, market="USDM_PERP", symbol="SYN/USDT", timeframe="4h", step_ms=H4, n_eval=300, window=400)
    assert r["distinct_records"] > 50, "denetim gerçekten kayıt görüyor"
    assert r["violations"] == {}, (r["violations"], r["examples"][:3])


def test_the_audit_is_sensitive_it_catches_the_old_stale_then_broken_flip(monkeypatch):
    """Eski davranış geri getirilirse (teyitten sonra HER kapanışta bozulma → BROKEN) denetim ihlali YAKALAR."""
    def old_after_confirm(st, *, n, fresh_bars, beyond):
        ci = int(st["confirm_idx"])
        for q in range(ci + 1, n):
            if beyond(q):
                st.update(status=K.ST_BROKEN, broken_idx=q)
                st["reasons"].append("FAILED_AFTER_CONFIRMATION")
                return st
        if n - 1 - ci > fresh_bars:
            st.update(status=K.ST_EXPIRED, expired_idx=ci + fresh_bars + 1)
        return st
    monkeypatch.setattr(A, "_after_confirm", old_after_confirm)
    A.clear_cache()
    hits = 0
    for seed in (3, 11, 29):
        r = walk_forward_audit(_walk(seed), market="USDM_PERP", symbol="SYN/USDT", timeframe="4h", step_ms=H4, n_eval=300, window=400)
        hits += r["violations"].get("TERMINAL_STATUS_CHANGED", 0)
    A.clear_cache()
    assert hits > 0


def test_after_confirm_first_terminal_event_is_final_and_a_late_break_is_a_separate_event():
    st = {"status": K.ST_CONFIRMED, "confirm_idx": 5, "broken_idx": None, "expired_idx": None, "reasons": []}
    A._after_confirm(st, n=20, fresh_bars=2, beyond=lambda q: q == 10)
    assert st["status"] == K.ST_EXPIRED and st["expired_idx"] == 8 and st["broken_idx"] is None and st["late_break_idx"] == 10
    st2 = {"status": K.ST_CONFIRMED, "confirm_idx": 5, "broken_idx": None, "expired_idx": None, "reasons": []}
    A._after_confirm(st2, n=20, fresh_bars=2, beyond=lambda q: q == 7)
    assert st2["status"] == K.ST_BROKEN and st2["broken_idx"] == 7 and "late_break_idx" not in st2
    st3 = {"status": K.ST_CONFIRMED, "confirm_idx": 5, "broken_idx": None, "expired_idx": None, "reasons": []}
    A._after_confirm(st3, n=8, fresh_bars=2, beyond=lambda q: False)   # son bar 7 < 8: hâlâ taze
    assert st3["status"] == K.ST_CONFIRMED
    rows = [_bar(T0 + i * M15, 1, 1, 1, 1) for i in range(20)]
    rec: dict = {}
    A._finish(rec, rows, st, step=M15, n=20, window=3, fresh_bars=2, detected_idx=4)
    assert rec["status"] == K.ST_EXPIRED and rec["broken_after_expiry"] is True
    assert rec["broken_at_ms"] == T0 + 11 * M15 and rec["confirmed_at_ms"] == T0 + 6 * M15


def _compression_after_pole():
    base = neutral_trend(100, start_ms=T0, step=M15, px0=50.0, up=True, scale=3.0)
    p0, t = base[-1]["close"], base[-1]["timestamp"]
    seq = [(1.000, 1.030, 0.975, 1.010), (1.010, 1.025, 0.980, 0.990), (0.990, 1.020, 0.978, 1.005),
           (1.005, 1.028, 0.982, 0.995), (0.995, 1.022, 0.979, 1.010), (1.010, 1.026, 0.985, 1.000)]
    rows = list(base) + [_bar(t + (i + 1) * M15, p0 * o, p0 * h, p0 * lo, p0 * c) for i, (o, h, lo, c) in enumerate(seq)]
    return rows, rows + [_bar(rows[-1]["timestamp"] + M15, p0, p0 * 1.039, p0 * 0.998, p0 * 1.035)]


@pytest.mark.parametrize("fixture", ["flag", "flag_after_strong_pole"])
def test_the_forming_flag_is_the_record_that_confirms_same_identity_same_trigger(fixture):
    if fixture == "flag":
        base = neutral_trend(100, start_ms=T0, step=M15, px0=50.0, up=True, scale=0.3)
        forming = pole_and_consolidation(base, step=M15, l_off=0.045, slope=0.005)
        confirmed = breakout_bar(forming, step=M15, above=max(r["high"] for r in forming[-3:]), pct=0.004)
    else:
        forming, confirmed = _compression_after_pole()
    A.clear_cache()

    def flags(rows):
        an = A.analyze(market="USDM_PERP", symbol="SYN/USDT", timeframe="15m", bars=rows, as_of_ms=rows[-1]["timestamp"] + M15)
        return [r for r in an["records"] if r["name"] in ("BULL_FLAG", "BULL_PENNANT")]
    f, c = flags(forming), flags(confirmed)
    assert len(f) == 1 and f[0]["status"] == K.ST_FORMING, [(r["name"], r["status"]) for r in f]
    assert len(c) == 1 and c[0]["status"] == K.ST_CONFIRMED, [(r["name"], r["status"]) for r in c]
    assert c[0]["pattern_id"] == f[0]["pattern_id"], "teyitte kimlik DEĞİŞMEZ"
    assert c[0]["trigger"]["level"] == pytest.approx(f[0]["trigger"]["level"]), "oluşan kaydın tetiği teyit olan tetiktir"
    assert [a["role"] for a in c[0]["anchors"]] == ["direk_zirvesi"] and c[0]["pole"]["end"]["ts"] >= c[0]["anchors"][0]["ts"]


def test_a_flag_whose_interpretations_reach_past_the_scan_window_start_is_not_emitted():
    """Zirve, taranan pencerenin başına direk uzunluğundan (8 bar) yakınsa bu kimliği paylaşan yorumların bir kısmı
    pencere dışındadır → kayıt ÜRETİLMEZ (gerekçe: FLAG_IDENTITY_AT_WINDOW_EDGE). Aynı yapı daha uzun geçmişle üretilir."""
    base = neutral_trend(12, start_ms=T0, step=M15, px0=50.0, up=True, scale=0.3)
    full = pole_and_consolidation(base, step=M15, n=10, l_off=0.015, slope=0.001)   # sığ konsolidasyon: iç yorumlar geçerli
    peak = max(range(len(full)), key=lambda i: full[i]["high"])
    assert peak >= 12

    def flags(rows):
        A.clear_cache()
        an = A.analyze(market="USDM_PERP", symbol="SYN/USDT", timeframe="15m", bars=rows, as_of_ms=rows[-1]["timestamp"] + M15)
        return [r for r in an["records"] if r["name"] in ("BULL_FLAG", "BULL_PENNANT")], an["rejects"]
    ok, _ = flags(full[peak - 12:])
    edge, rej = flags(full[peak - 7:])                      # zirve pencere indeksi 7 < direk uzunluğu 8
    assert ok and ok[0]["status"] == K.ST_FORMING
    assert not edge and any(rj.get("reason") == "FLAG_IDENTITY_AT_WINDOW_EDGE" for rj in rej), rej


def _rec(status, **kw):
    base = {"pattern_id": "p1", "family": K.FAMILY_CHART, "name": "BULL_FLAG", "side": K.LONG, "timeframe": "1d",
            "status": status, "confirmed_at_ms": T0, "broken_at_ms": None, "expired_at_ms": None, "trigger": {"level": 1.0},
            "invalidation": {"level": 0.9}, "stop": 0.89, "targets": [], "anchors": [], "atr": 0.05}
    base.update(kw)
    return base


def test_m2_exits_when_its_entry_structure_fails_even_after_the_record_went_stale():
    pol = P.POLICIES[P.BOT_M2]
    opened = T0 + 86_400_000
    late = _rec(K.ST_EXPIRED, expired_at_ms=T0 + 3 * 86_400_000, broken_at_ms=T0 + 9 * 86_400_000, broken_after_expiry=True)
    d = P.hold_decision(pol, position_side="LONG", opened_at_ms=opened, entry_pattern_id="p1",
                        analyses={"1d": {"records": [late]}}, as_of_ms=T0 + 10 * 86_400_000)
    assert d["action"] == P.ACT_EXIT and d["reason_code"] == "ENTRY_STRUCTURE_FAILED"
    stale = _rec(K.ST_EXPIRED, expired_at_ms=T0 + 3 * 86_400_000)
    d2 = P.hold_decision(pol, position_side="LONG", opened_at_ms=opened, entry_pattern_id="p1",
                         analyses={"1d": {"records": [stale]}}, as_of_ms=T0 + 10 * 86_400_000)
    assert d2["action"] != P.ACT_EXIT, "bozulma yoksa süresi dolmuş kayıt çıkış üretmez"


def test_a_compatible_structure_that_failed_late_still_makes_the_entry_wait_while_the_failure_is_recent():
    pol = P.POLICIES[P.BOT_T2]
    now = T0 + 10 * 86_400_000
    late = _rec(K.ST_EXPIRED, expired_at_ms=T0 + 3 * 86_400_000, broken_at_ms=now, broken_after_expiry=True)
    d = P.entry_decision(pol, intended_side="LONG", analyses={"1d": {"records": [late]}}, as_of_ms=now)
    assert d["action"] == P.ACT_WAIT and d["reason_code"] == "COMPATIBLE_BROKEN"
    old = _rec(K.ST_EXPIRED, expired_at_ms=T0 + 3 * 86_400_000, broken_at_ms=T0 + 4 * 86_400_000, broken_after_expiry=True)
    d2 = P.entry_decision(pol, intended_side="LONG", analyses={"1d": {"records": [old]}}, as_of_ms=now)
    assert d2["reason_code"] != "COMPATIBLE_BROKEN", "eski bozulma bekletmez"


def test_structures_enforce_cannot_be_enabled_with_real_money_and_validation_has_one_source():
    from tradingbot.config_v3 import load_v3, validate_v3
    from tradingbot.core import ConfigError
    with pytest.raises(ValueError, match="STRUCTURES_NOT_VALIDATED_FOR_LIVE"):
        K.validate_settings(policy_version=K.POLICY_VERSION, modes={"main": "ENFORCE"}, app_mode="LIVE")
    with pytest.raises(ValueError, match="geçersiz mod"):
        K.validate_settings(policy_version=K.POLICY_VERSION, modes={"t2_trend_regime": "ON"}, app_mode="PAPER")
    with pytest.raises(ValueError, match="policy_version"):
        K.validate_settings(policy_version="structures_v0", modes={}, app_mode="PAPER")
    assert K.validate_settings(policy_version=K.POLICY_VERSION, modes={"m2_tsmom28": "shadow"}, app_mode="LIVE")["m2_tsmom28"] == "SHADOW"
    cfg = load_v3({"mode": "PAPER", "structures": {"enabled": True, "pattern_trader": "enforce"}})
    validate_v3(cfg)
    assert cfg.structures.pattern_trader == "ENFORCE"
    src = (Path(__file__).resolve().parents[1] / "tradingbot" / "config_v3.py").read_text(encoding="utf-8")
    assert "from .structures.catalog import BOT_KEYS as _ST_BOTS, validate_settings" in src
    bad = load_v3({"mode": "PAPER", "structures": {"enabled": True, "main": "ENFORCE"}})
    bad.mode.mode = "LIVE"
    with pytest.raises(ConfigError):
        validate_v3(bad)
