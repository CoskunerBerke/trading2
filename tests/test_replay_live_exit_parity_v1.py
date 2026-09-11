# -*- coding: utf-8 -*-
"""Replay <-> canli PAPER CIKIS POLITIKASI paritesi.

Olculmus kusur (e1af166): `engine_v3` defteri `breakeven_at_mfe_r`yi yapilandirmadan gecirir,
`replay/engine` GECIRMEZDI. config.yaml `1.0` derken backtest 0 ile calisiyordu: canlida acik
olan MFE tabanli basa-bas korumasi backtest'te YOKTU. Ayni kurulum iki motorda farkli cikis
politikasiyla olculuyordu; bu, strateji karsilastirmasini gecersiz kilar.

Bu test iki motorun AYNI kaynaktan (config) besledigi cikis parametrelerini karsilastirir.
Yeni bir cikis parametresi eklenip yalniz bir motora baglanirsa burada DUSER.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

#: Defterin CIKIS davranisini belirleyen parametreler. Ikisi de config'ten gelmelidir.
EXIT_POLICY_KWARGS = ("tp1_fraction", "breakeven_at_mfe_r")


def _ledger_kwargs(path: Path, func_names: tuple[str, ...]) -> dict[str, str]:
    """Kaynak AST'inden `FuturesLedgerV2(...)`/`.load(...)` cagrisinin kwarg ifadelerini cikarir."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = getattr(f, "id", None) or getattr(f, "attr", None)
        base = getattr(getattr(f, "value", None), "id", None)
        if name in func_names or (base == "FuturesLedgerV2" and name == "load"):
            kw = {k.arg: ast.unparse(k.value) for k in node.keywords if k.arg}
            if any(a in kw for a in EXIT_POLICY_KWARGS):
                return kw
    raise AssertionError(f"{path}: FuturesLedgerV2 cagrisi bulunamadi")


def test_replay_and_live_share_every_exit_policy_parameter():
    live = _ledger_kwargs(ROOT / "tradingbot" / "engine_v3.py", ("FuturesLedgerV2", "load"))
    replay = _ledger_kwargs(ROOT / "tradingbot" / "replay" / "engine.py", ("FuturesLedgerV2",))
    missing = [a for a in EXIT_POLICY_KWARGS if a in live and a not in replay]
    assert not missing, f"replay motoruna baglanmamis cikis parametreleri: {missing}"
    for a in EXIT_POLICY_KWARGS:
        assert live[a] == replay[a], f"{a}: canli={live[a]!r} replay={replay[a]!r}"


def test_breakeven_is_taken_from_config_not_hardcoded():
    replay = _ledger_kwargs(ROOT / "tradingbot" / "replay" / "engine.py", ("FuturesLedgerV2",))
    expr = replay["breakeven_at_mfe_r"]
    assert "futures_v3" in expr and "breakeven_at_mfe_r" in expr
    assert expr.strip() not in ('Decimal("0")', "Decimal('0')", "0", "0.0")


def test_breakeven_rule_actually_protects_a_giveback_trade():
    """Olculmus ornek: SOL LONG, giris 93.74, ilk stop 90.0273 (risk %3.961), MFE %4.93 = 1.24R.

    breakeven_at_mfe_r=1.0 ile bu islem TAM STOP yiyemez; kapali (0) iken -1R'ye kadar duser.
    """
    from decimal import Decimal

    from tradingbot.accounting.futures_ledger import FuturesLedgerV2
    from tradingbot.accounting.models import SizeSpec

    entry, stop = Decimal("93.74"), Decimal("90.02731968092719")
    risk_pct = (entry - stop) / entry * 100
    mfe_pct = Decimal("4.928525709409003")
    assert mfe_pct / risk_pct > 1                          # MFE 1R esigini GECTI

    def _run(be: str) -> Decimal:
        led = FuturesLedgerV2(Decimal("100"), breakeven_at_mfe_r=Decimal(be))
        led.open("SOL/USDT", "LONG", entry, SizeSpec(Decimal("30")), stop=stop,
                 targets=[Decimal("101.17")], mark_price=entry)
        high = entry * (1 + mfe_pct / 100)
        led.tick({"SOL/USDT": {"last": high, "high": high, "low": entry}})      # kar bari
        led.tick({"SOL/USDT": {"last": stop, "high": entry, "low": stop - 1}})  # geri verme bari
        assert led.history, "pozisyon kapanmadi"
        return Decimal(str(led.history[-1].r_multiple))

    protected, unprotected = _run("1.0"), _run("0")
    assert unprotected < Decimal("-0.9")                   # koruma yokken tam stop
    assert protected > unprotected                          # koruma kar geri vermeyi keser
    assert protected > Decimal("-0.2")


def test_same_bar_stop_and_target_resolve_pessimistically():
    """Ayni barda stop ve hedefin ikisi de degerse STOP once islenir (kotumser yurutme).

    PROTOCOL.md sec. 4 bu varsayima dayanir; varsayim burada ispatlanir.
    """
    from decimal import Decimal

    from tradingbot.accounting.futures_ledger import FuturesLedgerV2
    from tradingbot.accounting.models import SizeSpec

    led = FuturesLedgerV2(Decimal("100"), breakeven_at_mfe_r=Decimal("0"))
    led.open("X/USDT", "LONG", Decimal("100"), SizeSpec(Decimal("30")),
             stop=Decimal("95"), targets=[Decimal("110")], mark_price=Decimal("100"))
    # Bar hem 95'in altina hem 110'un ustune gidiyor: ikisi de degiyor.
    led.tick({"X/USDT": {"last": Decimal("108"), "high": Decimal("112"), "low": Decimal("94")}})
    assert led.history, "pozisyon kapanmadi"
    rec = led.history[-1]
    assert Decimal(str(rec.r_multiple)) < 0, "ayni barda stop degdigi halde hedefle kapandi"
    assert rec.exit_reason.startswith("stop")
