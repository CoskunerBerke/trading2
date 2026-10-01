"""FAZ 1A — giriş bağı (`link_trade`) yazımının gözlenebilirliği.

Sözleşme: `_entry_flush` artık `store.link_trade(...)` dönüşünü YUTMAZ.

* "İşlem açılmadı" (`link_not_needed`) ile "bağ yazılamadı" (`link_failed`) ASLA aynı
  görünemez.
* Bağ arızası aktif tur döngüsünü DURDURMAZ ama açık bir uyarı + gerekçe kodu üretir.
* Eksik bağ UYDURULMAZ; yalnız raporlanır.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from tradingbot.engine_v3 import TradingEngineV3
from tradingbot.learn.entry_snapshot import EntrySnapshotStore


class _Plan:
    valid = True
    entry = 100.0
    stop = 95.0
    targets = (110.0,)
    entry_type = "kirilim"
    expected_r = 2.0
    rr = 2.0


class _Decision:
    direction = "LONG"
    specialist_reports = ()
    opportunity: dict = {}
    score = 0.4


def _engine(store: EntrySnapshotStore) -> types.SimpleNamespace:
    """`_entry_flush` için asgari sahte motor. Aktif hiçbir yol çağrılmaz."""
    return types.SimpleNamespace(
        entry_snapshot_store=store,
        _entry_pending=[],
        run_id="testrun",
        _tour_no=1,
        _journal_cycle=1,
        entry_cfg=types.SimpleNamespace(policy_version="entry_v1.0.0"),
        entry_mode="SHADOW",
        weekly_cfg=None,                      # haftalık bağlam kapalı → erken döner
        mtf_cfg=None,                         # H kapalı → bağ sayaçları H'den BAĞIMSIZ ölçülür
        mtf_mode="SHADOW",
        code_sha=lambda: "deadbeef",
        config_hash=lambda: "cfg123",
        _attach_weekly_context=lambda snap, rec: None,
        _attach_mtf_context=lambda snap, rec: None,
    )


def _cand(symbol: str, *, trade_id: str | None) -> dict:
    return {"symbol": symbol, "direction": "LONG", "decision": _Decision(), "plan": _Plan(),
            "chief": {}, "market": "USDM_PERP", "rank": 0, "specialists": None,
            "features": {"atr_pct": 1.0}, "daily_frame": None, "intraweek_frame": None,
            "ts": None,
            "entry_log": {"risk_allowed": True, "risk_reasons": [],
                          "trade_id": trade_id, "block_code": None}}


def _flush(eng, now=None) -> dict:
    from tradingbot.core import utc_now
    return TradingEngineV3._entry_flush(eng, now or utc_now())


def _store(tmp_path: Path) -> EntrySnapshotStore:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return EntrySnapshotStore(tmp_path / "entry_snapshot.jsonl", max_per_cycle=50)


# ---------------------------------------------------------------- sayaçların varlığı

def test_1a_all_five_counters_are_reported(tmp_path: Path):
    eng = _engine(_store(tmp_path))
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1")]
    out = _flush(eng)
    for k in ("link_attempted", "link_written", "link_failed", "link_duplicate",
              "link_not_needed"):
        assert k in out["links"], k
    assert out["link_health"] == "OK"


def test_1a_successful_link_is_counted_as_written(tmp_path: Path):
    st = _store(tmp_path)
    eng = _engine(st)
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1")]
    out = _flush(eng)
    assert out["links"]["link_attempted"] == 1
    assert out["links"]["link_written"] == 1
    assert out["links"]["link_failed"] == 0
    assert out["links"]["link_not_needed"] == 0
    assert st.trade_links() == {"F1": next(iter(st.by_candidate()))}
    assert any(e["code"] == "LINK_OK" for e in out["link_events"])


def test_1a_no_trade_opened_is_not_needed_not_a_failure(tmp_path: Path):
    eng = _engine(_store(tmp_path))
    eng._entry_pending = [_cand("ETH/USDT", trade_id=None)]
    out = _flush(eng)
    assert out["links"]["link_not_needed"] == 1
    assert out["links"]["link_attempted"] == 0
    assert out["links"]["link_failed"] == 0
    assert out["link_health"] == "OK", "islem acilmamasi bir ariza DEGILDIR"


def test_1a_no_trade_and_write_failure_are_distinguishable(tmp_path: Path):
    """Sözleşmenin çekirdeği: iki olay AYNI görünemez."""
    st = _store(tmp_path)
    eng = _engine(st)
    st.link_trade = lambda *a, **k: False          # yazım BAŞARISIZ
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1"), _cand("ETH/USDT", trade_id=None)]
    out = _flush(eng)
    assert out["links"]["link_failed"] == 1
    assert out["links"]["link_not_needed"] == 1
    assert out["link_health"] == "LINK_WRITE_FAILED"
    codes = {e["code"] for e in out["link_events"]}
    assert "LINK_WRITE_FAILED" in codes
    assert "LINK_OK" not in codes


def test_1a_link_failure_does_not_stop_the_cycle(tmp_path: Path):
    st = _store(tmp_path)
    eng = _engine(st)
    st.link_trade = lambda *a, **k: False
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1"),
                          _cand("ETH/USDT", trade_id="F2"),
                          _cand("SOL/USDT", trade_id="F3")]
    out = _flush(eng)
    assert out["candidates"] == 3
    assert out["written"] == 3, "bag arizasi snapshot yazimini DURDURMAZ"
    assert out["links"]["link_failed"] == 3


def test_1a_raising_link_writer_is_caught_and_counted(tmp_path: Path):
    st = _store(tmp_path)
    eng = _engine(st)

    def _boom(*a, **k):
        raise OSError("disk full")

    st.link_trade = _boom
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1")]
    out = _flush(eng)
    assert out["links"]["link_failed"] == 1
    assert out["written"] == 1
    assert out["link_health"] == "LINK_WRITE_FAILED"


def test_1a_already_present_link_is_a_duplicate_not_a_failure(tmp_path: Path):
    st = _store(tmp_path)
    eng = _engine(st)
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1")]
    _flush(eng)
    # Aynı trade_id ikinci turda YENİDEN bağlanmaz.
    eng2 = _engine(st)
    eng2._entry_pending = [_cand("ETH/USDT", trade_id="F1")]
    out = _flush(eng2)
    assert out["links"]["link_duplicate"] == 1
    assert out["links"]["link_attempted"] == 0
    assert out["links"]["link_failed"] == 0
    assert out["link_health"] == "OK"


def test_1a_skipped_link_when_snapshot_not_written_is_visible(tmp_path: Path):
    """Üçüncü durum: snapshot yinelenmişse bağ da yazılmaz — artık SESSİZ DEĞİL."""
    st = _store(tmp_path)
    eng = _engine(st)
    st.append = lambda rec: False              # snapshot YAZILMADI
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1")]
    out = _flush(eng)
    assert out["links"]["link_skipped_no_snapshot"] == 1
    assert out["links"]["link_attempted"] == 0
    assert out["links"]["link_not_needed"] == 0
    assert any(e["code"] == "LINK_SKIPPED_SNAPSHOT_NOT_WRITTEN" for e in out["link_events"])


def test_1a_missing_links_are_never_synthesised(tmp_path: Path):
    st = _store(tmp_path)
    eng = _engine(st)
    st.link_trade = lambda *a, **k: False
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1")]
    _flush(eng)
    assert st.trade_links() == {}, "basarisiz bag UYDURULMAZ"


def test_1a_existing_state_is_untouched_by_the_new_counters(tmp_path: Path):
    """F00030 benzeri mevcut satırlar DEĞİŞMEZ; yalnız yeni sayaçlar eklenir."""
    p = tmp_path / "entry_snapshot.jsonl"
    st = EntrySnapshotStore(p, max_per_cycle=50)
    st.append({"candidate_id": "abc", "symbol": "NATGAS/USDT"})
    st.link_trade("abc", "F00030")
    before = p.read_text(encoding="utf-8")
    eng = _engine(st)
    eng._entry_pending = [_cand("BTC/USDT", trade_id=None)]
    _flush(eng)
    assert p.read_text(encoding="utf-8").startswith(before)
    assert st.trade_links()["F00030"] == "abc"


@pytest.mark.parametrize("tid,expect", [(None, "link_not_needed"), ("F9", "link_written")])
def test_1a_counter_selection_is_exclusive(tmp_path: Path, tid, expect):
    eng = _engine(_store(tmp_path))
    eng._entry_pending = [_cand("BTC/USDT", trade_id=tid)]
    ctr = _flush(eng)["links"]
    assert ctr[expect] == 1
    assert sum(ctr.values()) == (1 if expect == "link_not_needed" else 2)   # attempted+written


def test_1a_counters_are_identical_with_h_enabled(tmp_path: Path):
    """Bağ gözlenebilirliği H'den BAĞIMSIZDIR: H açıkken sayaçlar DEĞİŞMEZ."""
    from tradingbot.learn.multitimeframe_context import MultiTimeframeConfig

    def run(mtf_cfg):
        st = _store(tmp_path / f"h_{bool(mtf_cfg)}")
        eng = _engine(st)
        eng.mtf_cfg = mtf_cfg
        if mtf_cfg is not None:
            from tradingbot.engine_v3 import TradingEngineV3
            eng._atr_from_frame = TradingEngineV3._atr_from_frame
            eng._attach_mtf_context = (
                lambda s_, r_: TradingEngineV3._attach_mtf_context(eng, s_, r_))
        eng._entry_pending = [_cand("BTC/USDT", trade_id="F1"),
                              _cand("ETH/USDT", trade_id=None)]
        return _flush(eng)["links"]

    assert run(None) == run(MultiTimeframeConfig())
