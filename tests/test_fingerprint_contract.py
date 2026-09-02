"""Parmak izi sözleşmesi regresyonları — vacuous kanıt üretilemez.

Deployment denetimlerinde kullanılan alan kümeleri elle kopyalanıyordu ve içlerinde
`take_profit` vardı; oysa `accounting.models.Position` içinde böyle bir alan YOK. Sonuç:
her pozisyon için sabit `None` hash'lenip "pozisyonlar değişmedi" deniyordu. Aynı hata spot
defterinde `positions` anahtarıyla da yapılabilirdi (o anahtar da yok).
"""
from __future__ import annotations

from dataclasses import fields as dc_fields
from decimal import Decimal

import pytest

from tradingbot.accounting.models import Position
from tradingbot.ops.fingerprint import (FUTURES_ECONOMICS_FIELDS, FUTURES_POSITION_FIELDS,
                                        SPOT_LEDGER_FIELDS, FingerprintError,
                                        assert_schema, capital_fingerprint,
                                        futures_economics_fingerprint, futures_fingerprint,
                                        spot_fingerprint)


def _prod_position(**over):
    """Üretim şekilli pozisyon sözlüğü — gerçek `Position.to_dict()` çıktısı."""
    p = Position(id="F00099", symbol="ETH/USDT", market_type=Position.__dataclass_fields__[
        "market_type"].default, side=Position.__dataclass_fields__["side"].default,
        qty=Decimal("0.5"), entry_avg=Decimal("2500"))
    p.stop = Decimal("2400")
    p.targets = [Decimal("2600"), Decimal("2700")]
    p.targets_hit = 1
    p.leverage = 3
    p.isolated_margin = Decimal("416.67")
    p.tp1_done = True
    p.initial_stop = Decimal("2400")
    p.initial_qty = Decimal("1.0")
    p.realized_pnl = Decimal("12.5")
    p.fees_paid = Decimal("0.9")
    d = p.to_dict()
    d.update(over)
    return d


def test_01_take_profit_is_not_a_real_position_field():
    """Kök neden testi: `take_profit` şemada YOK."""
    names = {f.name for f in dc_fields(Position)}
    assert "take_profit" not in names, "şema değişmiş — alan kümesi gözden geçirilmeli"
    assert {"targets", "targets_hit", "tp1_done"} <= names, "TP bilgisi bu alanlarda"
    assert "take_profit" not in FUTURES_POSITION_FIELDS


def test_02_every_declared_field_exists_in_the_real_schema():
    names = {f.name for f in dc_fields(Position)}
    for f in FUTURES_POSITION_FIELDS:
        assert f in names, f"{f} `Position` şemasında yok"
    for f in FUTURES_ECONOMICS_FIELDS:
        assert f in names, f"{f} `Position` şemasında yok"
    info = assert_schema()
    assert info["verified"] is True and info["known_fields"] > 20


def test_03_fingerprint_over_production_shaped_positions_is_stable_and_not_vacuous():
    pos = {"ETH/USDT": _prod_position()}
    a = futures_fingerprint(pos)
    b = futures_fingerprint({"ETH/USDT": dict(_prod_position())})
    assert a["fingerprint"] == b["fingerprint"]
    assert a["vacuous"] is False and a["n_positions"] == 1
    assert a["missing_fields"] == {}
    assert set(a["fields_used"]) == set(FUTURES_POSITION_FIELDS)


@pytest.mark.parametrize("field,new", [
    ("qty", "0.6"), ("stop", "2350"), ("leverage", 5), ("targets_hit", 2),
    ("tp1_done", False), ("initial_qty", "2.0"), ("entry_avg", "2501"),
])
def test_04_fingerprint_actually_changes_when_a_tracked_field_changes(field, new):
    """Vacuous olmadığının kanıtı: izlenen her alan hash'i DEĞİŞTİRMELİ."""
    base = futures_fingerprint({"ETH/USDT": _prod_position()})["fingerprint"]
    moved = futures_fingerprint({"ETH/USDT": _prod_position(**{field: new})})["fingerprint"]
    assert moved != base, f"{field} değişti ama parmak izi aynı kaldı"


def test_05_mark_only_movement_does_not_change_the_fingerprint():
    """MTM alanları izlenmez: doğal fiyat hareketi 'regresyon' gibi görünmemeli."""
    base = futures_fingerprint({"ETH/USDT": _prod_position()})["fingerprint"]
    drift = futures_fingerprint({"ETH/USDT": _prod_position(
        last_price="2555", mfe_pct="3.1", mae_pct="1.2", bars_held=9)})["fingerprint"]
    assert drift == base


def test_06_missing_required_field_raises_instead_of_hashing_none():
    broken = _prod_position()
    broken.pop("stop")
    with pytest.raises(FingerprintError) as e:
        futures_fingerprint({"ETH/USDT": broken})
    assert "stop" in str(e.value)
    soft = futures_fingerprint({"ETH/USDT": broken}, require_all=False)
    assert soft["missing_fields"] == {"ETH/USDT": ["stop"]}


def test_07_phantom_field_in_the_projection_is_rejected_by_schema_assert():
    with pytest.raises(FingerprintError) as e:
        futures_fingerprint({"ETH/USDT": _prod_position()},
                            fields=FUTURES_POSITION_FIELDS + ("take_profit",))
    assert "take_profit" in str(e.value) or "OLMAYAN" in str(e.value)


def test_08_empty_ledger_is_marked_vacuous_not_silently_valid():
    out = futures_fingerprint({})
    assert out["n_positions"] == 0 and out["vacuous"] is True
    assert out["fingerprint"], "hash yine de üretilir ama kanıt sayılmaz"


def test_09_spot_fingerprint_does_not_expect_a_positions_key():
    led = {"assets": {"BTC": "0.01"}, "lots": [{"qty": "0.01"}], "locked_assets": {},
           "position_meta": {"BTC": {}}, "cash": "91.76", "open_orders": []}
    out = spot_fingerprint(led)
    assert out["vacuous"] is False
    assert out["has_positions_key"] is False
    assert set(out["fields_used"]) == set(SPOT_LEDGER_FIELDS)
    assert "positions" not in SPOT_LEDGER_FIELDS
    moved = spot_fingerprint(led | {"cash": "80.00"})
    assert moved["fingerprint"] != out["fingerprint"]


def test_10_spot_fingerprint_over_an_unrelated_dict_is_vacuous():
    out = spot_fingerprint({"positions": {}})
    assert out["vacuous"] is True and out["fields_present"] == []
    assert out["has_positions_key"] is True


def test_11_economics_fingerprint_tracks_realized_values_only():
    a = futures_economics_fingerprint({"ETH/USDT": _prod_position()})
    b = futures_economics_fingerprint({"ETH/USDT": _prod_position(last_price="9999")})
    assert a["fingerprint"] == b["fingerprint"], "mark ekonomiyi değiştirmemeli"
    c = futures_economics_fingerprint({"ETH/USDT": _prod_position(fees_paid="1.9")})
    assert c["fingerprint"] != a["fingerprint"]
    assert a["kind"] == "futures_economics"


def test_12_capital_fingerprint_flags_absent_inputs():
    ok = capital_fingerprint({"starting_equity": 100.0}, {"starting_equity": 100.0},
                             [{"adjustment_id": "cap_200"}])
    assert ok["vacuous"] is False
    moved = capital_fingerprint({"starting_equity": 150.0}, {"starting_equity": 100.0},
                                [{"adjustment_id": "cap_200"}])
    assert moved["fingerprint"] != ok["fingerprint"]
    assert capital_fingerprint({}, {})["vacuous"] is True


def test_13_object_positions_are_accepted_not_only_dicts():
    p = Position(id="F1", symbol="SOL/USDT",
                 market_type=Position.__dataclass_fields__["market_type"].default,
                 side=Position.__dataclass_fields__["side"].default,
                 qty=Decimal("1"), entry_avg=Decimal("100"))
    out = futures_fingerprint({"SOL/USDT": p})
    assert out["n_positions"] == 1 and out["missing_fields"] == {}
    assert out["vacuous"] is False
