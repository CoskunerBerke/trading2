"""Quant Evaluation V1 — point-in-time eligibility artifact ve journal coverage testleri.

Kritik ispatlar:
* `lookup` GELECEK metadata döndürmez; `as_of` öncesi snapshot yoksa None (bugünkü bilgi geçmişe
  taşınmaz).
* Strict modda eksik eligibility backtest geçerliliğini DÜŞÜRÜR.
* Seans kapısı METADATA iledir; hiçbir sembol adı kodda özel-durum değildir.
* Journal kapsaması düşükse terfi kapanır ve rapor açık uyarı gösterir.
"""
from __future__ import annotations

import json

import pytest

from tradingbot.quant.coverage import CoverageGates, journal_coverage
from tradingbot.quant.eligibility import (CURRENT_AS_HISTORICAL, DELISTED, MISSING, SCHEMA_VERSION,
                                          STALE, TRADING, EligibilityStore, SymbolEligibility,
                                          TradingSession, build_artifact, coverage_report,
                                          from_exchange_info, load_artifact, load_store,
                                          write_artifact)

DAY = 86_400_000
T0 = 1_760_000_000_000


def _snap(symbol="ETH/USDT", as_of=T0, status=TRADING, **kw):
    kw.setdefault("market_type", "USDM_PERP")
    return SymbolEligibility(symbol=symbol, as_of_ms=as_of, trading_status=status,
                             tick_size=0.01, step_size=0.001, min_qty=0.001, min_notional=5.0,
                             source="test", source_timestamp_ms=as_of, provenance="test_fixture",
                             **kw)


# --------------------------------------------------------------- point-in-time lookup

def test_lookup_never_returns_future_metadata():
    store = EligibilityStore([_snap(as_of=T0 + 10 * DAY)])
    assert store.lookup("ETH/USDT", "USDM_PERP", T0) is None          # gelecekteki snapshot GİZLİ
    assert store.lookup("ETH/USDT", "USDM_PERP", T0 + 10 * DAY) is not None
    assert store.lookup("ETH/USDT", "USDM_PERP", T0 + 99 * DAY) is not None


def test_missing_snapshot_is_unknown_not_current_metadata():
    store = EligibilityStore([_snap(as_of=T0 + 10 * DAY)])
    res = store.check("ETH/USDT", "USDM_PERP", T0)
    assert res["eligible"] is None                                    # BİLİNMİYOR — True değil
    assert MISSING in res["flags"] and CURRENT_AS_HISTORICAL in res["flags"]
    assert res["valid"] is True                                       # non-strict: geçerli sayılır
    strict = store.check("ETH/USDT", "USDM_PERP", T0, strict=True)
    assert strict["valid"] is False                                   # strict: backtest geçersiz


def test_most_recent_prior_snapshot_wins():
    store = EligibilityStore([_snap(as_of=T0), _snap(as_of=T0 + 5 * DAY, status=DELISTED)])
    at3 = store.check("ETH/USDT", "USDM_PERP", T0 + 3 * DAY)
    at7 = store.check("ETH/USDT", "USDM_PERP", T0 + 7 * DAY)
    assert at3["eligible"] is True
    assert at7["eligible"] is False and "DELISTED" in at7["reasons"]


def test_stale_snapshot_flagged_and_strict_invalidates():
    store = EligibilityStore([_snap(as_of=T0)])
    fresh = store.check("ETH/USDT", "USDM_PERP", T0 + DAY, max_age_ms=7 * DAY)
    assert STALE not in fresh["flags"] and fresh["valid"] is True
    old = store.check("ETH/USDT", "USDM_PERP", T0 + 30 * DAY, max_age_ms=7 * DAY)
    assert STALE in old["flags"]
    old_strict = store.check("ETH/USDT", "USDM_PERP", T0 + 30 * DAY, max_age_ms=7 * DAY, strict=True)
    assert old_strict["valid"] is False


def test_listing_window_respected():
    store = EligibilityStore([_snap(as_of=T0, listing_ms=T0 + 2 * DAY)])
    before = store.check("ETH/USDT", "USDM_PERP", T0 + DAY)
    after = store.check("ETH/USDT", "USDM_PERP", T0 + 3 * DAY)
    assert before["eligible"] is False and "NOT_YET_LISTED" in before["reasons"]
    assert after["eligible"] is True


# --------------------------------------------------------------- seans (metadata ile)

def test_session_gate_is_metadata_driven_not_hardcoded():
    # 7/24 kripto
    crypto = EligibilityStore([_snap(symbol="BTC/USDT")])
    assert crypto.check("BTC/USDT", "USDM_PERP", T0)["eligible"] is True
    # seanslı ürün: yalnız hafta içi 13:30–20:00 UTC — sembol adı kodda GEÇMEZ
    sessions = TradingSession(always_open=False,
                              windows={d: [[810, 1200]] for d in range(0, 5)})
    # 1970-01-01 Perşembe → weekday = (gün + 3) % 7. Snapshot GÜN BAŞINA alınır ki aynı günün
    # hem açık hem kapalı saatleri `as_of`tan sonra kalsın (aksi halde lookup doğru olarak None döner).
    days = T0 // DAY
    weekday = (days + 3) % 7
    store = EligibilityStore([_snap(symbol="SOME.EQ", market_type="EQUITY", session=sessions,
                                    as_of=days * DAY)])
    open_ts = days * DAY + 900 * 60_000        # 15:00 UTC
    closed_ts = days * DAY + 60 * 60_000       # 01:00 UTC
    res_open = store.check("SOME.EQ", "EQUITY", open_ts)
    res_closed = store.check("SOME.EQ", "EQUITY", closed_ts)
    if weekday < 5:
        assert res_open["eligible"] is True
        assert res_closed["eligible"] is False and "SESSION_CLOSED" in res_closed["reasons"]
    else:                                       # hafta sonu: her iki an da kapalı
        assert res_open["eligible"] is False and res_closed["eligible"] is False
    weekend_ts = (days + (5 - weekday) % 7) * DAY + 900 * 60_000
    if ((weekend_ts // DAY) + 3) % 7 >= 5:
        assert store.check("SOME.EQ", "EQUITY", weekend_ts)["eligible"] is False


# --------------------------------------------------------------- kapsama raporu

def test_coverage_report_partial_and_strict_invalidation():
    store = EligibilityStore([_snap(symbol="ETH/USDT", as_of=T0)])
    times = [T0 + i * DAY for i in range(5)]
    full = coverage_report(store, ["ETH/USDT"], "USDM_PERP", times)
    assert full["status"] == "OK" and full["point_in_time"] is True
    assert full["backtest_valid"] is True and full["coverage_ratio"] == 1.0
    partial = coverage_report(store, ["ETH/USDT", "YOK/USDT"], "USDM_PERP", times)
    assert partial["status"] == "PARTIAL" and partial["point_in_time"] is False
    assert MISSING in partial["flags"]
    strict = coverage_report(store, ["ETH/USDT", "YOK/USDT"], "USDM_PERP", times, strict=True)
    assert strict["backtest_valid"] is False                          # strict → geçersiz
    none_store = coverage_report(EligibilityStore(), ["ETH/USDT"], "USDM_PERP", times)
    assert none_store["status"] == "UNAVAILABLE" and none_store["backtest_valid"] is False


# --------------------------------------------------------------- artifact I/O

def test_artifact_deterministic_roundtrip_and_forward_archivable(tmp_path):
    a1 = build_artifact([_snap()], as_of_ms=T0, source="test", source_timestamp_ms=T0)
    a2 = build_artifact([_snap()], as_of_ms=T0, source="test", source_timestamp_ms=T0)
    assert a1 == a2 and a1["artifact_sha"] == a2["artifact_sha"]
    assert a1["schema_version"] == SCHEMA_VERSION and a1["forward_archivable"] is True
    p = write_artifact(tmp_path / "elig.json", a1)
    back = load_artifact(p)
    assert len(back) == 1 and back.symbols == ["ETH/USDT"]
    assert back.check("ETH/USDT", "USDM_PERP", T0)["eligible"] is True
    # ileriye dönük arşiv: iki farklı as_of dosyası tek store'da birleşir
    p2 = write_artifact(tmp_path / "elig2.json",
                        build_artifact([_snap(as_of=T0 + 30 * DAY, status=DELISTED)],
                                       as_of_ms=T0 + 30 * DAY, source="test"))
    merged = load_store([p, p2])
    assert len(merged) == 2
    assert merged.check("ETH/USDT", "USDM_PERP", T0 + 40 * DAY)["eligible"] is False
    assert load_artifact(tmp_path / "yok.json").symbols == []          # eksik dosya crash etmez


def test_from_exchange_info_is_valid_only_from_as_of():
    payload = {"symbols": [{"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
                            "status": "TRADING", "onboardDate": T0 - 100 * DAY,
                            "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                                        {"filterType": "LOT_SIZE", "stepSize": "0.001",
                                         "minQty": "0.001"},
                                        {"filterType": "MIN_NOTIONAL", "notional": "5"}]}]}
    snaps = from_exchange_info(payload, as_of_ms=T0, source="binance", source_timestamp_ms=T0)
    assert len(snaps) == 1
    s = snaps[0]
    assert s.symbol == "BTC/USDT" and s.trading_status == TRADING
    assert s.tick_size == pytest.approx(0.10) and s.min_notional == pytest.approx(5.0)
    assert s.session.always_open is True                              # kripto 7/24
    assert "valid_from_as_of" in s.provenance
    store = EligibilityStore(snaps)
    assert store.lookup("BTC/USDT", "USDM_PERP", T0 - DAY) is None     # as_of ÖNCESİNE uygulanmaz
    assert store.lookup("BTC/USDT", "USDM_PERP", T0 + DAY) is not None
    assert json.dumps(s.to_dict())                                     # JSON serileşebilir


def test_unknown_fields_stay_none_not_zero():
    payload = {"symbols": [{"symbol": "XUSDT", "baseAsset": "X", "quoteAsset": "USDT",
                            "status": "WEIRD", "filters": []}]}
    s = from_exchange_info(payload, as_of_ms=T0)[0]
    assert s.tick_size is None and s.min_notional is None              # sahte 0 YOK
    assert s.trading_status == "UNKNOWN"
    av = s.availability()
    assert av["tick_size"] is False and av["trading_status"] is False
    ok, reasons = s.tradeable_at(T0)
    assert ok is False and "STATUS_UNKNOWN" in reasons                 # fail-closed


# --------------------------------------------------------------- journal coverage

def _rec(i, *, labeled=True, feat=True, spec=True, regime=True, cost=True, accepted=True):
    return {"accepted": accepted, "outcome_labeled": labeled,
            "feature_snapshot": {"ema": 1.0} if feat else None,
            "specialist_scores": {"trend": 0.5} if spec else None,
            "regime": "trend_up" if regime else None,
            "fees": 1.0 if cost else None, "funding": -0.2 if cost else None,
            "mae_pct": -1.0, "mfe_pct": 3.0, "policy_id": "champion",
            "event_ts_utc": f"2026-01-{(i % 27) + 1:02d}T12:00:00+00:00",
            "quality_flags": []}


def test_full_coverage_passes_gates():
    rows = [_rec(i) for i in range(40)]
    rep = journal_coverage(rows)
    assert rep["gates_passed"] is True and rep["promotion_allowed"] is True
    assert rep["coverage"]["outcome_labeled"] == 1.0
    assert rep["coverage"]["feature_snapshot"] == 1.0
    assert rep["n_accepted"] == 40 and rep["n_rejected_shadow"] == 0
    assert rep["warnings"] == []


def test_low_coverage_closes_promotion_with_warning():
    rows = [_rec(i, feat=False, spec=False, regime=False) for i in range(40)]
    rep = journal_coverage(rows)
    assert rep["gates_passed"] is False and rep["promotion_allowed"] is False
    assert "düşük kapsama" in rep["verdict"]
    assert any("terfisi kapalı" in w for w in rep["warnings"])
    assert any("eski şema" in w for w in rep["warnings"])
    assert {"FEATURE_SNAPSHOT", "REGIME"} <= {c["code"] for c in rep["checks"] if not c["passed"]}


def test_too_few_records_fails_closed():
    rep = journal_coverage([_rec(i) for i in range(5)])
    assert rep["gates_passed"] is False
    assert any(c["code"] == "MIN_RECORDS" and not c["passed"] for c in rep["checks"])


def test_configurable_gates_and_age():
    rows = [_rec(i, feat=False) for i in range(40)]
    loose = journal_coverage(rows, gates=CoverageGates(min_feature_snapshot=0.0))
    assert loose["gates_passed"] is True
    now = 1_800_000_000_000
    aged = journal_coverage(rows, gates=CoverageGates(min_feature_snapshot=0.0, max_age_days=1.0),
                            now_ms=now)
    assert aged["gates_passed"] is False
    assert any(c["code"] == "DATA_AGE" and not c["passed"] for c in aged["checks"])
    assert aged["data_age_days"] is not None and aged["span_days"] is not None


def test_empty_journal_is_not_silently_ok():
    rep = journal_coverage([])
    assert rep["n_records"] == 0 and rep["gates_passed"] is False
    assert rep["coverage"]["outcome_labeled"] is None                  # uydurma 1.0 YOK
    assert journal_coverage([]) == journal_coverage([])                # deterministik
