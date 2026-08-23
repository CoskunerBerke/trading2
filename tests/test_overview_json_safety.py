"""`/api/overview` JSON SINIRI — bozuk state'te bile RFC-uyumlu cevap.

KÖK NEDEN (gerçek VPS state'inde doğrulandı): `StateReader.overview()` HAM coin-head sözlüklerini
doğrudan JSON'a veriyordu. Ham sözlük `specialist_reports[].levels.ema100_1d / ema200_1d` altında
ÇIPLAK `NaN` taşıyabilir (worker `coin_heads.json`'a `NaN` literali yazar) → `JSONResponse`'un
`allow_nan=False` serileştirmesi `ValueError` → HTTP 500. `/api/live/coin-heads` yalnız NORMALİZE
satır/meta sözleşmesini döndürdüğü için 200 veriyordu.

Sözleşme: ölçülemeyen sayı SAHTE `0` değil `null`; gerekçesi `unavailable_reason`; sağlam komşu
kayıtlar korunur; ham model çıktısı JSON'a kontrolsüz verilmez.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient          # noqa: E402

from tradingbot.dashboard.app import create_app    # noqa: E402
from tradingbot.dashboard.config import DashboardConfig  # noqa: E402
from tradingbot.dashboard.views import (NO_DECISION_VERDICT, coin_head_sort_key,  # noqa: E402
                                        json_safe)

NAN, INF, NINF = float("nan"), float("inf"), float("-inf")
OPEN5 = [("BZ/USDT", "F1", "LONG"), ("XAUT/USDT", "F2", "LONG"), ("LDO/USDT", "F3", "SHORT"),
         ("AAVE/USDT", "F4", "LONG"), ("ETH/USDT", "F5", "SHORT")]


def _pos(sym, pid, side, last="104"):
    return {"id": pid, "symbol": sym, "market_type": "USDM_PERP", "side": side, "qty": "1",
            "entry_avg": "100", "leverage": 2, "isolated_margin": "50", "stop": "90",
            "targets": ["120"], "last_price": last, "entry_fee": "0",
            "opened_at": "2026-08-20T00:00:00+00:00", "fills": [{"id": "f-" + pid}]}


def _head(sym, **over):
    h = {"symbol": sym, "verdict": "FUTURES_LONG", "direction": "LONG",
         "confidence_calibrated": 0.7, "p_win": 0.55, "expected_return_net": 0.01,
         "expected_r": 1.5, "regime": "TREND", "vetoes": [], "no_trade_reason": "",
         "generated_at": "2026-08-20T00:00:00+00:00",
         "spot_plan": {"valid": False}, "futures_plan": {"valid": True},
         # GERÇEK state'teki yapı: ic ice gecmis model ciktisi
         "specialist_reports": [{"agent_name": "trend", "usable": True, "metrics": {}},
                                {"agent_name": "levels", "usable": True,
                                 "levels": {"ema50_1d": 100.0, "ema100_1d": 99.0, "ema200_1d": 98.0}}]}
    h.update(over)
    return h


def _write(tmp_path: Path, name: str, heads: list[dict], positions=None, trades=None) -> Path:
    d = tmp_path / name
    d.mkdir()
    pos = positions if positions is not None else [_pos(*o) for o in OPEN5]
    (d / "futures_ledger.json").write_text(json.dumps({
        "schema_version": 2, "kind": "futures", "equity": "500", "wallet_balance": "500",
        "fees": {"taker_pct": 0.0, "maker_pct": 0.0},
        "positions": {p["symbol"]: p for p in pos}, "history": trades or []}), encoding="utf-8")
    # `json.dumps` VARSAYILAN `allow_nan=True` -> dosyaya ÇIPLAK `NaN`/`Infinity` yazar.
    # Worker'in uretim davranisi tam olarak budur; state'i DUZELTMEK cozum degildir.
    (d / "coin_heads.json").write_text(json.dumps({
        "generated_at": "2026-08-20T00:00:00+00:00", "heads": heads,
        "chief": {"breadth": {"long": 1, "short": 0, "no_trade": 0, "hold": 0, "data_invalid": 0}}}),
        encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "history": []}), encoding="utf-8")
    return d


def _client(state_dir: Path, tmp_path: Path) -> TestClient:
    return TestClient(create_app(state_dir, tmp_path / "market", None, DashboardConfig()),
                      raise_server_exceptions=False)


def _reject(tok):
    raise AssertionError("RFC disi sabit: %s" % tok)


def _nonfinite(obj, path="root"):
    bad = []
    if isinstance(obj, float) and not math.isfinite(obj):
        bad.append(path)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            bad += _nonfinite(v, "%s.%s" % (path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += _nonfinite(v, "%s[%d]" % (path, i))
    return bad


def _assert_contract(c: TestClient, *, open_syms=(), healthy_symbol=None):
    """12 maddelik zorunlu sozlesme — her senaryoda calisir."""
    ro = c.get("/api/overview")
    rc = c.get("/api/live/coin-heads")
    assert ro.status_code == 200, ro.text[:300]                       # 1
    assert rc.status_code == 200                                      # 2
    ov = json.loads(ro.text, parse_constant=_reject)                  # 3
    ch = json.loads(rc.text, parse_constant=_reject)
    assert _nonfinite(ov) == [] and _nonfinite(ch) == []              # 4
    for raw in (ro.text, rc.text):                                    # 5
        assert not re.search(r'(?<![\w"])(-?Infinity|NaN)(?![\w"])', raw)
    for k in ("open_positions_total", "open_positions_shown", "missing_open_symbols",
              "coverage_complete"):
        assert ov[k] == ch[k], k                                      # 9
    assert ov["open_positions_total"] == len(open_syms)               # 8
    assert ov["coverage_complete"] is True and ov["missing_open_symbols"] == []
    shown = {r[0] for r in ch["rows"]}
    for s in open_syms:
        assert s in shown, s
    if healthy_symbol:                                                # 7
        hh = next(h for h in ov["top_heads"] if h["symbol"] == healthy_symbol)
        assert hh["confidence_calibrated"] == 0.7 and hh["p_win"] == 0.55
    html = c.get("/")                                                 # 10
    assert html.status_code == 200
    body = re.sub(r"<script.*?</script>", "", html.text, flags=re.S)
    assert not re.search(r"\bNaN\b|[+-]?\bInfinity\b|\binf\b(?!o)", body)
    for verb in ("post", "put", "patch", "delete"):                   # 12
        assert getattr(c, verb)("/api/live/coin-heads").status_code == 405
    return ov, ch


# ===================================================================== 1) BOZUK DEGER MATRISI
_CASES = {
    "temiz": {},
    "confidence NaN string": {"confidence_calibrated": "NaN"},
    "confidence ciplak NaN": {"confidence_calibrated": NAN},
    "confidence +Infinity": {"confidence_calibrated": INF},
    "confidence -Infinity": {"confidence_calibrated": NINF},
    "p_win NaN": {"p_win": NAN},
    "p_win Infinity": {"p_win": INF},
    "expected_return NaN": {"expected_return_net": NAN},
    "expected_return -Infinity": {"expected_return_net": NINF},
    "expected_r NaN": {"expected_r": NAN},
    "expected_r Infinity": {"expected_r": INF},
    "bozuk numeric string": {"confidence_calibrated": "abc", "expected_r": ""},
    "eksik alan": {"confidence_calibrated": None, "p_win": None, "expected_r": None},
    "ic ice model ciktisi NaN": {"specialist_reports": [
        {"agent_name": "levels", "usable": True,
         "levels": {"ema50_1d": 1.0, "ema100_1d": NAN, "ema200_1d": NINF}}]},
}


@pytest.mark.parametrize("label,over", sorted(_CASES.items()))
def test_overview_survives_every_corrupt_head_field(label, over, tmp_path):
    heads = [_head("BZ/USDT", **over), _head("SAGLAM/USDT")]
    c = _client(_write(tmp_path, "s", heads), tmp_path)
    ov, _ = _assert_contract(c, open_syms=[o[0] for o in OPEN5], healthy_symbol="SAGLAM/USDT")
    bad = next(h for h in ov["top_heads"] if h["symbol"] == "BZ/USDT")
    for k in ("confidence_calibrated", "p_win", "expected_return_net", "expected_r"):
        v = bad[k]
        assert v is None or math.isfinite(v), (k, v)
        if k in over or over.get(k) is None and k in over:
            assert v != 0, "%s SAHTE SIFIR olarak gosterilmis" % k      # 6


def test_position_pnl_non_finite_does_not_break_the_endpoint(tmp_path):
    """Anlık PnL hesabı bozuk fiyattan ötürü ölçülemezse uç yine 200 döner."""
    pos = [_pos(*OPEN5[0], last="NaN")] + [_pos(*o) for o in OPEN5[1:]]
    c = _client(_write(tmp_path, "s", [_head("SAGLAM/USDT")], positions=pos), tmp_path)
    ov, ch = _assert_contract(c, open_syms=[o[0] for o in OPEN5], healthy_symbol="SAGLAM/USDT")
    row = next(r for r in ch["rows"] if r[0] == OPEN5[0][0])
    assert row[11] in ("—", "") or not re.search(r"nan|inf", row[11], re.I)


def test_all_heads_corrupt(tmp_path):
    heads = [_head("A/USDT", confidence_calibrated=NAN, p_win=INF, expected_r=NAN,
                   expected_return_net=NINF),
             _head("B/USDT", confidence_calibrated="NaN", p_win="", expected_r="abc")]
    c = _client(_write(tmp_path, "s", heads), tmp_path)
    ov, _ = _assert_contract(c, open_syms=[o[0] for o in OPEN5])
    for h in ov["top_heads"]:
        for k in ("confidence_calibrated", "p_win", "expected_r", "expected_return_net"):
            assert h[k] is None or math.isfinite(h[k])


def test_empty_heads_still_shows_every_open_position(tmp_path):
    c = _client(_write(tmp_path, "s", []), tmp_path)
    ov, ch = _assert_contract(c, open_syms=[o[0] for o in OPEN5])
    assert [r[0] for r in ch["rows"]] == [o[0] for o in OPEN5]
    assert sorted(ov["coin_head_scope"]["no_decision_symbols"]) == sorted(o[0] for o in OPEN5)


def test_five_open_only_two_valid_heads_with_fallback_rows(tmp_path):
    heads = [_head("XAUT/USDT"), _head("AAVE/USDT", confidence_calibrated=NAN)]
    c = _client(_write(tmp_path, "s", heads), tmp_path)
    ov, ch = _assert_contract(c, open_syms=[o[0] for o in OPEN5])
    assert ov["open_positions_shown"] == 5
    fb = [r for r, m in zip(ch["rows"], ch["meta"]) if m["no_decision"]]
    assert {r[0] for r in fb} == {"BZ/USDT", "LDO/USDT", "ETH/USDT"}
    for r in fb:
        assert r[1] == NO_DECISION_VERDICT and r[2] == "AÇIK"


def test_no_position_ledger(tmp_path):
    c = _client(_write(tmp_path, "s", [_head("SAGLAM/USDT")], positions=[]), tmp_path)
    _assert_contract(c, open_syms=[], healthy_symbol="SAGLAM/USDT")


# ===================================================================== 2) json_safe / siralama
def test_json_safe_maps_non_finite_to_null_with_a_reason():
    out, reasons = json_safe({"a": NAN, "b": [1.0, INF], "c": {"d": Decimal("NaN")},
                              "e": Decimal("2.5"), "f": "ok", "g": {"h": set()}})
    assert out == {"a": None, "b": [1.0, None], "c": {"d": None}, "e": 2.5, "f": "ok",
                   "g": {"h": None}}
    assert set(reasons) == {"a", "b[1]", "c.d", "g.h"}
    assert "JSON'a çevrilemeyen tip: set" in reasons["g.h"]
    json.dumps(out, allow_nan=False)                      # RFC uyumlu
    assert out["a"] is None                               # SAHTE SIFIR DEGIL, null


def test_confidence_sort_key_is_deterministic_with_non_finite():
    hs = [{"symbol": "A", "confidence_calibrated": NAN},
          {"symbol": "B", "confidence_calibrated": 0.5},
          {"symbol": "C", "confidence_calibrated": "NaN"},
          {"symbol": "D", "confidence_calibrated": 0.9},
          {"symbol": "E", "confidence_calibrated": INF}]
    order = [h["symbol"] for h in sorted(hs, key=coin_head_sort_key)]
    assert order == ["D", "B", "A", "C", "E"]             # sonlu azalan, olculemeyen SONA (sembol)
    assert [h["symbol"] for h in sorted(hs[::-1], key=coin_head_sort_key)] == order   # kararli


def test_overview_never_publishes_raw_model_output(tmp_path):
    """HAM `specialist_reports` bloğu API'ye SIZMAZ (normalize sözleşme yayımlanır)."""
    c = _client(_write(tmp_path, "s", [_head("A/USDT", specialist_reports=[
        {"agent_name": "levels", "levels": {"ema200_1d": NAN}}])]), tmp_path)
    ov = c.get("/api/overview").json()
    assert "specialist_reports" not in json.dumps(ov)
    assert "coin_head_table" in ov and "coin_head_scope" in ov
    assert isinstance(ov["top_heads"], list)
    if ov["top_heads"]:
        assert set(ov["top_heads"][0]) >= {"symbol", "status", "confidence_calibrated"}


# ===================================================================== 3) salt-okunurluk
def test_get_requests_do_not_write_state(tmp_path):
    d = _write(tmp_path, "s", [_head("A/USDT", confidence_calibrated=NAN)])

    def fp():
        return {p.name: (p.stat().st_size, p.stat().st_mtime_ns,
                         hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(d.iterdir())}

    c = _client(d, tmp_path)
    before = fp()
    for path in ("/", "/api/overview", "/api/live/coin-heads", "/api/live/positions",
                 "/api/live/summary", "/api/live/health", "/health/live"):
        assert c.get(path).status_code == 200, path
    assert fp() == before                                  # 11: path + size + mtime_ns + sha256
