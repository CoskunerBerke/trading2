# -*- coding: utf-8 -*-
"""BOX THEORY V15 — PANEL.

Kullanıcının isteği: yeni kural panelde EKSİKSİZ görünsün. "Görünsün" burada ölçülebilir üç şeydir:
defter listelenmeli, 5m grafiğinde KUTU çizilmeli (tepe/dip/bant/orta) ve açıklama satırları kuralın
karşılaştırdığı sayıları vermeli — ayrıca ÇIKIŞIN videoda olmadığını açıkça söylemeli.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_chart_analysis_v1_fixes import _client, _env  # noqa: E402

BOOK = "strategy_paper_box"
RULE_PARAMS = {"near_frac": 0.10, "trigger": "break_prev", "long_stop": "day_low",
               "exit_kind": "box_opposite", "eod_close": True, "leverage": 3}


def _with_box_book(tmp_path):
    st, data = _env(tmp_path)
    idx = json.loads((st / "strategy_paper_index.json").read_text(encoding="utf-8"))
    idx["books"].append({"key": BOOK, "name": "b1_box_fade", "summary_file": "%s.json" % BOOK})
    (st / "strategy_paper_index.json").write_text(json.dumps(idx), encoding="utf-8")
    (st / BOOK).mkdir()
    (st / BOOK / "futures_ledger.json").write_text(
        json.dumps({"schema_version": 2, "positions": {}, "history": []}), encoding="utf-8")
    (st / ("%s.json" % BOOK)).write_text(json.dumps({
        "name": "b1_box_fade", "key": BOOK, "atr_mult": 3.0, "positions": {}, "counters": {},
        "rule_family": "box", "rule_params": RULE_PARAMS,
        "data_policy": {"rule_timeframes": ["1d", "5m"],
                        "intraday": {"tf": "5m", "tour_interval_min": 15, "bars_seen_per_tour": 1}}}),
        encoding="utf-8")
    return st, data


def _chart(tmp_path, tf: str = "5m"):
    st, data = _with_box_book(tmp_path)
    c = _client(st, data)
    r = c.get("/api/chart/BTC?tf=%s&market=futures&book=%s&n=200" % (tf, BOOK))
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _elements(j: dict) -> dict:
    return {e["id"]: e for e in ((j.get("analysis") or {}).get("elements") or [])}


def test_the_box_book_is_listed_with_a_readable_label(tmp_path):
    st, data = _with_box_book(tmp_path)
    from tradingbot.dashboard.state import StateReader
    books = {b["book_id"]: b for b in StateReader(st).books()}
    assert BOOK in books, sorted(books)
    assert books[BOOK]["name"] == "b1_box_fade"
    assert "Box" in books[BOOK]["label"], books[BOOK]["label"]


def test_the_five_minute_chart_serves_the_box_rule_state(tmp_path):
    j = _chart(tmp_path)
    rs = (j.get("analysis") or {}).get("rule_state")
    assert rs is not None and rs.get("variant") == "b1_box_fade", rs
    assert rs.get("ok") is True, rs
    for k in ("box_high", "box_low", "box_mid", "location", "near_frac", "exit_kind"):
        assert rs.get(k) is not None, (k, rs)
    assert rs["box_high"] > rs["box_low"] > 0
    assert rs["location"] in ("TOP", "BOTTOM", "MIDDLE", "OUTSIDE")


def test_the_chart_draws_the_box_its_band_and_its_middle(tmp_path):
    els = _elements(_chart(tmp_path))
    for eid in ("ind:box_high", "ind:box_low", "ind:box_top_band", "ind:box_bottom_band", "ind:box_mid"):
        assert eid in els, sorted(els)
    hi, lo = els["ind:box_high"]["price"], els["ind:box_low"]["price"]
    assert hi > els["ind:box_top_band"]["price"] > els["ind:box_mid"]["price"]
    assert els["ind:box_mid"]["price"] > els["ind:box_bottom_band"]["price"] > lo
    assert els["ind:box_high"]["decision_impact"] == "USED_IN_DECISION"
    assert els["ind:box_high"]["source"]["module"] == "box_theory"


def test_the_explanation_states_the_box_the_location_and_that_the_exit_is_our_choice(tmp_path):
    lines = {x["k"]: x["v"] for x in ((_chart(tmp_path).get("analysis") or {}).get("explanation") or [])}
    assert "kutu" in lines and "konum" in lines, sorted(lines)
    assert "Önceki günün aralığı" in lines["kutu"]
    ex = lines.get("çıkış", "")
    assert "videoda YOKTUR" in ex or "videoda yoktur" in ex.lower(), ex
    assert "box_opposite" in ex


def test_the_trend_books_are_untouched_by_the_box_wiring(tmp_path):
    """GERİLEME KAPISI: kural kaydı eklenince T2/M2'nin panel yolu DEĞİŞMEMELİ."""
    st, data = _with_box_book(tmp_path)
    c = _client(st, data)
    r = c.get("/api/chart/BTC?tf=4h&market=futures&book=strategy_paper&n=200")
    assert r.status_code == 200
    rs = (r.json().get("analysis") or {}).get("rule_state")
    assert rs and rs["variant"] == "t2_trend_regime" and rs.get("ema200") is not None
    assert rs.get("box_high") is None, "trend defterinde kutu alanı OLMAMALI"


def test_an_invalid_rule_param_surfaces_instead_of_silently_falling_back(tmp_path):
    """Bozuk defter ayarı panelde SESSİZCE varsayılana düşmez; kural durumu 'ok değil' der."""
    st, data = _with_box_book(tmp_path)
    doc = json.loads((st / ("%s.json" % BOOK)).read_text(encoding="utf-8"))
    doc["rule_params"] = {"exit_kind": "nope"}
    (st / ("%s.json" % BOOK)).write_text(json.dumps(doc), encoding="utf-8")
    c = _client(st, data)
    r = c.get("/api/chart/BTC?tf=5m&market=futures&book=%s&n=200" % BOOK)
    assert r.status_code == 200
    rs = (r.json().get("analysis") or {}).get("rule_state")
    assert rs and rs.get("ok") is False and "RULE_PARAMS_INVALID" in str(rs.get("reason")), rs
