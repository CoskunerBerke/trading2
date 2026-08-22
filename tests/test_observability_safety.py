"""Gözlemlenebilirlik paketinin GÜVENLİK sözleşmeleri.

Bu paket (panel, canlı PnL, 2x–5x kaldıraç, Telegram) strateji davranışını değiştirmez ve
PAPER dışına çıkmaz. Aşağıdakiler regresyon kapılarıdır.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.core import ConfigError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_FILES = ("coinhead/head.py", "coinhead/factors.py", "coinhead/specialists.py",
                  "coinhead/redteam.py", "agents/technical.py", "opportunity.py")


# ===================================================================== PAPER / LIVE
def test_paper_mode_and_live_order_path_stay_closed():
    cfg = load_v3({"mode": "PAPER"})
    assert cfg.mode.mode == "PAPER" and cfg.mode.live_trading is False
    for bad in ("LIVE", "LIVE_LIMITED"):
        with pytest.raises(ConfigError):
            load_v3({"mode": bad})
    raw = (ROOT / "config.yaml").read_text(encoding="utf-8")
    assert "mode: PAPER" in raw and "live_trading: false" in raw


def test_notification_layer_can_never_place_an_order():
    """Bildirim/panel katmanı emir API'si ÇAĞIRMAZ — yalnız okur ve mesaj üretir."""
    for rel in ("notify/service.py", "notify/events.py", "notify/outbox.py", "pnl.py",
                "dashboard/views.py"):
        src = (ROOT / "tradingbot" / rel).read_text(encoding="utf-8")
        for forbidden in ("ledger2.open(", "market_buy(", "place_order(", "submit(", "create_order"):
            assert forbidden not in src, f"{rel}: {forbidden}"


def test_dashboard_is_read_only():
    src = (ROOT / "tradingbot" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "@app.post" not in src and "@app.put" not in src and "@app.delete" not in src
    assert (ROOT / "tradingbot" / "dashboard" / "config.py").read_text(encoding="utf-8").count("read_only: bool = True") == 1


# ===================================================================== strateji değişmedi
def test_strategy_decision_files_are_untouched_by_this_feature():
    """Bu paket sinyal/indikatör/veto/eşik mantığına DOKUNMAZ.

    `head.py` yalnızca kaldıraç kararı ile ilgili bir alan taşımaz; kaldıraç seçimi motorda,
    plan üretimi tamamlandıktan SONRA yapılır. Bu test o sınırı korur.
    """
    head = (ROOT / "tradingbot" / "coinhead" / "head.py").read_text(encoding="utf-8")
    assert "select_leverage" not in head, "kaldıraç seçimi karar üretimine SIZMAMALI"
    assert "TradeNotifier" not in head and "telegram" not in head.lower()
    for rel in STRATEGY_FILES:
        src = (ROOT / "tradingbot" / rel).read_text(encoding="utf-8")
        assert "telegram" not in src.lower(), rel
        assert "TradeNotifier" not in src, rel


def _code_only(path: Path) -> str:
    """Yalnız ÇALIŞAN kod: docstring ve yorumlar çıkarılır (açıklama metni yanlış eşleşmesin)."""
    import ast
    import io
    import tokenize
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    src = ast.unparse(tree)
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            out.append(tok.string)
    return " ".join(out)


def test_leverage_module_does_not_touch_risk_budget():
    """Kaldıraç modülü risk yüzdesi/bütçesi HESAPLAMAZ; yalnız seviye seçer."""
    src = _code_only(ROOT / "tradingbot" / "risk" / "leverage.py")
    for forbidden in ("risk_per_trade_pct", "max_total_open_risk_pct", "size_position"):
        assert forbidden not in src, forbidden


def test_risk_ceilings_unchanged():
    from tradingbot.risk.profiles import resolve_profile
    p = resolve_profile("PAPER_RESEARCH")
    assert (p.risk_per_trade_pct, p.max_total_open_risk_pct, p.futures_max_leverage) == (2.0, 6.0, 5)
    for name, exp in (("TESTNET", (0.5, 2.0, 2)), ("SHADOW_LIVE", (0.5, 2.0, 2)),
                      ("LIVE", (0.5, 2.0, 2)), ("LIVE_LIMITED", (0.25, 1.0, 1))):
        q = resolve_profile(name)
        assert (q.risk_per_trade_pct, q.max_total_open_risk_pct, q.futures_max_leverage) == exp, name


# ===================================================================== sırlar
def test_no_real_secret_is_committed():
    """Kaynakta gerçek Telegram token'ı biçiminde bir dize BULUNMAMALI."""
    import re
    token_re = re.compile(r"\b\d{8,}:[A-Za-z0-9_\-]{30,}\b")
    for path in list((ROOT / "tradingbot").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        for m in token_re.finditer(path.read_text(encoding="utf-8", errors="replace")):
            # test fixture'ları bilinçli olarak SENTETİKtir ("x" tekrarlı)
            assert "xxxxx" in m.group(0), f"{path.name}: gerçek görünümlü token {m.group(0)[:12]}…"
    cfg = (ROOT / "config.yaml").read_text(encoding="utf-8")
    assert not token_re.search(cfg), "config.yaml'da token olamaz"
    assert "TRADINGBOT_TELEGRAM_BOT_TOKEN" not in cfg or ":" not in cfg.split("TRADINGBOT_TELEGRAM_BOT_TOKEN")[1][:40]


def test_config_rejects_a_token_value_in_place_of_an_env_name():
    with pytest.raises(ConfigError):
        load_v3({"mode": "PAPER", "telegram": {"bot_token_env": "9876543210:AAH" + "y" * 30}})


def test_state_and_data_files_are_not_tracked_by_git():
    r = subprocess.run(["git", "ls-files", "state", "data", "logs", "backups"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("git yok")
    tracked = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert tracked == [], f"gerçek state/data dosyaları izlenmemeli: {tracked[:5]}"


def test_notify_outbox_is_state_not_source():
    """Outbox `state_path` altına yazılır (repo'ya değil)."""
    src = (ROOT / "tradingbot" / "engine_v3.py").read_text(encoding="utf-8")
    assert "TradeNotifier.from_config(v3.telegram, st)" in src
    r = subprocess.run(["git", "ls-files", "--error-unmatch", "state/notify_outbox.json"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode != 0, "outbox dosyası repoda olmamalı"
