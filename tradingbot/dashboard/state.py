"""State dizini okuyucu — panel yalnızca okur; eksik/bozuk dosyalar None döner (asla istisna sızdırmaz)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..core import from_iso, read_json, utc_now

# /api/state/{name} beyaz listesi (uzantısız)
STATE_FILES: dict[str, str] = {
    "agents": "agents.json", "futures_ledger": "futures_ledger.json", "portfolio": "portfolio.json", "scan": "scan.json",
    "learning": "learning.json", "signals": "signals.json", "coin_heads": "coin_heads.json", "risk": "risk.json",
    "killswitch": "killswitch.json", "mode": "mode.json", "health": "health.json", "llm_budget": "llm_budget.json",
    "models": "models.json", "universe": "universe.json", "shadow_book": "shadow_book.json", "heartbeat": "heartbeat.json",
    "orders": "orders.json", "triggers": "triggers.json",
    "snapshot_telemetry": "snapshot_telemetry.json", "research_policy": "research_policy.json",
    "decision_funnel": "decision_funnel.json",
}
JSONL_FILES: dict[str, str] = {"llm_calls": "llm_calls.jsonl", "trade_memory": "trade_memory.jsonl", "signals_log": "signals_log.jsonl"}


def _age(ts: Any) -> float | None:
    if not ts:
        return None
    try:
        return max(0.0, (utc_now() - from_iso(str(ts))).total_seconds())
    except (ValueError, TypeError):
        return None


class StateReader:
    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)

    # ---- ham okuma
    def get(self, name: str) -> Any:
        fn = STATE_FILES.get(name)
        if not fn:
            return None
        return read_json(self.state_dir / fn, default=None)

    def tail_jsonl(self, name: str, n: int = 200) -> list[dict]:
        fn = JSONL_FILES.get(name)
        if not fn:
            return []
        p = self.state_dir / fn
        if not p.exists():
            return []
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out: list[dict] = []
        for ln in lines[-n:]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
                if isinstance(d, dict):
                    out.append(d)
            except json.JSONDecodeError:
                continue
        return out

    def mtimes(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, fn in {**STATE_FILES, **JSONL_FILES}.items():
            try:
                out[k] = os.stat(self.state_dir / fn).st_mtime
            except OSError:
                continue
        return out

    def readable(self) -> bool:
        return self.state_dir.exists() and os.access(self.state_dir, os.R_OK)

    def heartbeat_age(self) -> float | None:
        hb = self.get("heartbeat")
        return _age(hb.get("ts") or hb.get("at")) if isinstance(hb, dict) else None

    # ---- türetilmiş görünümler
    def futures_positions(self) -> list[dict]:
        led = self.get("futures_ledger") or {}
        pos = led.get("positions") or {}
        out: list[dict] = []
        items = pos.values() if isinstance(pos, dict) else pos
        for p in items:
            if not isinstance(p, dict):
                continue
            q = dict(p)
            q.setdefault("qty", q.get("units"))
            q.setdefault("entry_avg", q.get("entry"))
            q.setdefault("isolated_margin", q.get("margin"))
            if q.get("notional") in (None, 0, "0") and q.get("qty") and q.get("entry_avg"):
                try:
                    q["notional"] = float(q["qty"]) * float(q["entry_avg"])
                except (TypeError, ValueError):
                    pass
            if not q.get("liquidation_price") and q.get("qty") and q.get("isolated_margin") and q.get("entry_avg"):
                try:  # izole marj yaklaşımı (paper_futures.liq_price ile aynı)
                    move = float(q["isolated_margin"]) * 0.95 / max(float(q["qty"]), 1e-12)
                    e = float(q["entry_avg"])
                    q["liquidation_price"] = e - move if str(q.get("side", "LONG")).upper() == "LONG" else e + move
                except (TypeError, ValueError):
                    pass
            q.setdefault("amount_type", "notional")
            q.setdefault("fees_paid", q.get("fees", 0))
            if "funding_net" not in q:
                if "funding" in q:
                    q["funding_net"] = q.get("funding")
                else:
                    try:
                        q["funding_net"] = float(q.get("funding_received") or 0) - float(q.get("funding_paid") or 0)
                    except (TypeError, ValueError):
                        q["funding_net"] = 0
            out.append(q)
        return out

    def futures_equity(self) -> float | None:
        led = self.get("futures_ledger") or {}
        for k in ("equity", "wallet_balance"):
            if led.get(k) is not None:
                try:
                    return float(led[k])
                except (TypeError, ValueError):
                    continue
        return None

    def spot_equity(self) -> float | None:
        pf = self.get("portfolio") or {}
        if not pf:
            return None
        try:
            cash = float(pf.get("cash", 0) or 0)
        except (TypeError, ValueError):
            cash = 0.0
        val = 0.0
        for p in (pf.get("positions") or {}).values() if isinstance(pf.get("positions"), dict) else []:
            try:
                val += float(p.get("units", 0)) * float(p.get("last_price") or p.get("entry_price") or 0)
            except (TypeError, ValueError):
                continue
        return round(cash + val, 4)

    def trades(self) -> list[dict]:
        out: list[dict] = []
        led = self.get("futures_ledger") or {}
        for h in led.get("history") or []:
            if isinstance(h, dict):
                q = dict(h); q.setdefault("market_type", "futures"); out.append(q)
        pf = self.get("portfolio") or {}
        for h in pf.get("history") or []:
            if isinstance(h, dict):
                q = dict(h); q.setdefault("market_type", "spot"); q.setdefault("id", q.get("trade_id") or f"spot-{q.get('symbol', '')}-{q.get('exit_time') or q.get('closed_at') or ''}"); out.append(q)
        out.sort(key=lambda t: str(t.get("closed_at") or t.get("exit_time") or ""), reverse=True)
        return out

    def trade(self, trade_id: str) -> dict | None:
        for t in self.trades():
            if str(t.get("id")) == trade_id:
                return t
        return None

    def orders(self) -> list[dict]:
        o = self.get("orders")
        if isinstance(o, list):
            return [x for x in o if isinstance(x, dict)]
        if isinstance(o, dict) and isinstance(o.get("orders"), list):
            return [x for x in o["orders"] if isinstance(x, dict)]
        led = self.get("futures_ledger") or {}
        ent = led.get("entries") or []
        return [e for e in ent if isinstance(e, dict)][-300:][::-1]

    def coin_heads(self) -> list[dict]:
        ch = self.get("coin_heads") or {}
        return [h for h in (ch.get("heads") or []) if isinstance(h, dict)]

    def coin_head(self, base: str) -> dict | None:
        b = base.upper()
        for h in self.coin_heads():
            if str(h.get("symbol", "")).upper().split("/")[0] == b:
                return h
        return None

    def brief(self, base: str) -> dict | None:
        ag = self.get("agents") or {}
        for b in ag.get("briefs") or []:
            if str(b.get("symbol", "")).upper().split("/")[0] == base.upper():
                return b
        return None

    def killswitch_state(self) -> str:
        ks = self.get("killswitch")
        if isinstance(ks, dict) and ks.get("state"):
            return str(ks["state"])
        r = self.get("risk") or {}
        return str((r.get("killswitch") or {}).get("state") or "ARMED")

    def mode(self) -> str:
        m = self.get("mode")
        return str(m.get("mode")) if isinstance(m, dict) and m.get("mode") else "PAPER"

    # ---- canlılık / tazelik
    def file_age(self, name: str) -> float | None:
        """State dosyasının disk yaşı (sn). Fiyat tazeliği bundan gelir — tarayıcı Binance'a GİTMEZ."""
        fn = {**STATE_FILES, **JSONL_FILES}.get(name)
        if not fn:
            return None
        try:
            return max(0.0, utc_now().timestamp() - os.stat(self.state_dir / fn).st_mtime)
        except OSError:
            return None

    def price_age_s(self) -> float | None:
        """Mark fiyatlarının yaşı = defterin son yazılma yaşı (worker'ın güvenli/önbellekli kaynağı).

        STRATEJİ TURU yaşıyla KARIŞTIRILMAZ: tur 4 saatte bir, tick/defter yazımı çok daha sık olur.
        """
        return self.file_age("futures_ledger")

    def heads_age_s(self) -> float | None:
        return _age((self.get("coin_heads") or {}).get("generated_at"))

    def risk_age_s(self) -> float | None:
        """`risk.json` anlık görüntüsünün yaşı (sn). Fiyat yaşından AYRI kavramdır.

        `risk.json` STRATEJİ TURUNDA yazılır (`engine_v3._persist_risk_state` → kök `generated_at`),
        fiyatlar ise her tickte tazelenir; ikisi dakikalarca ayrışabilir. Damga yoksa `None` döner
        ve panel «Veri yaşı bilinmiyor» yazar — taze GİBİ gösterilmez.
        """
        return _age((self.get("risk") or {}).get("generated_at"))

    def marks(self) -> dict[str, Any]:
        """Sembol → worker'ın kaydettiği son fiyat. Panel ASLA borsaya doğrudan bağlanmaz."""
        out: dict[str, Any] = {}
        for p in self.futures_positions():
            lp = p.get("last_price")
            if lp not in (None, "", 0, "0"):
                out[str(p.get("symbol") or "")] = lp
        return out

    def max_drawdown_pct(self) -> Any:
        """`risk.json` içindeki drawdown. Risk motoru bunu `exposure` ALTINA yazar
        (`RiskEngine.snapshot()` → `{"exposure": {"drawdown_pct": ...}}`); eski kod yalnız kök
        seviyeye baktığı için değer bulunamıyor ve panelde «Veri yok» görünüyordu."""
        r = self.get("risk") or {}
        for scope in (r.get("exposure"), r.get("state"), r):
            if not isinstance(scope, dict):
                continue
            for key in ("drawdown_pct", "max_drawdown_pct"):
                v = scope.get(key)
                if v is not None:
                    return v
        return None

    def last_run_age(self) -> float | None:
        ages = [a for a in (
            _age((self.get("coin_heads") or {}).get("generated_at")),
            _age((self.get("agents") or {}).get("generated_at")),
            _age((self.get("scan") or {}).get("generated_at")),
        ) if a is not None]
        return min(ages) if ages else None

    def snapshot_telemetry(self) -> dict[str, Any]:
        """FeatureSnapshotV3 uretim sayaclari (bkz. learn/telemetry.py). Dosya yoksa sifir sayaclar."""
        from ..learn.telemetry import COUNTER_NAMES
        d = self.get("snapshot_telemetry") or {}
        return {"counters": {k: int((d.get("counters") or {}).get(k, 0) or 0) for k in COUNTER_NAMES},
                "last_failure_code": str(d.get("last_failure_code") or ""),
                "last_failure_at": str(d.get("last_failure_at") or "")}

    def learning_research(self) -> dict[str, Any]:
        """PAPER araştırma politikası özeti — hangi aday aktif, neyi değiştirdi, sonucu ne.

        `auto_promotion_possible` her zaman False: bu katman CHAMPION/LIVE üretemez.
        """
        d = self.get("research_policy") or {}
        recs = d.get("records") or []
        return {"active_policy_id": d.get("active_policy_id"),
                "active_rationale": d.get("active_rationale"),
                "active_changed_params": d.get("active_changed_params") or [],
                "active_stats": d.get("active_stats"),
                "shadow_policy_id": d.get("shadow_policy_id"),
                "shadow_stats": d.get("shadow_stats"),
                "quarantined": d.get("quarantined") or [],
                "counts": d.get("counts") or {},
                "auto_promotion_possible": bool(d.get("auto_promotion_possible", False)),
                "retired": [{"policy_id": r.get("policy_id"), "reason": r.get("retired_reason")}
                            for r in recs if r.get("state") == "RETIRED" and r.get("retired_reason")][-5:],
                "gates": d.get("gates") or {}}

    def decision_funnel(self) -> dict[str, Any]:
        """Karar hunisi + kayan 24 saat. `trades_opened_24h` YALNIZ gözlem metriğidir, kapı değildir.

        `daily_trade_cap`/`per_run_trade_cap` her zaman None: sistemde sabit işlem sayısı kotası YOK.
        """
        d = self.get("decision_funnel") or {}
        return {"run": d.get("run") or {}, "rolling_24h": d.get("rolling_24h") or {},
                "trades_opened_24h": int(d.get("trades_opened_24h", 0) or 0),
                "hard_block_rate": d.get("hard_block_rate"), "no_trade_rate": d.get("no_trade_rate"),
                "opportunity_cost_count": int(d.get("opportunity_cost_count", 0) or 0),
                "opportunity_cost": d.get("opportunity_cost") or [],
                "daily_trade_cap": None, "per_run_trade_cap": None, "at": d.get("at")}

    def overview(self) -> dict[str, Any]:
        health = self.get("health") or {}
        llm = self.get("llm_budget") or {}
        heads = self.coin_heads()
        positions = self.futures_positions()
        # «Coin head'ler» tablosu AÇIK POZİSYON LİSTESİ DEĞİLDİR; fakat açık pozisyonların hepsi
        # ZORUNLU olarak yer alır. Eski `sorted(heads)[:10]` top-N kesimi açık pozisyonları
        # düşürüyordu (bkz. views.coin_head_scope). HTML ve API AYNI kaynaktan beslenir.
        from .views import coin_head_scope
        scope = coin_head_scope(heads, positions)
        return {
            "generated_at": utc_now().isoformat(timespec="seconds"),
            "equity_futures": self.futures_equity(),
            "equity_spot": self.spot_equity(),
            "open_positions": positions,
            "killswitch": self.killswitch_state(),
            "mode": self.mode(),
            "health": health.get("state") or "UNKNOWN",
            "health_summary": health.get("summary") or "",
            "heartbeat_age_s": self.heartbeat_age(),
            "last_run_age_s": self.last_run_age(),
            "llm_spent_usd_today": llm.get("spent_usd"),
            "snapshot_telemetry": self.snapshot_telemetry(),
            "learning_research": self.learning_research(),
            "decision_funnel": self.decision_funnel(),
            "chief": (self.get("coin_heads") or {}).get("chief") or (self.get("agents") or {}).get("chief") or {},
            "top_heads": scope["heads"],
            "coin_head_scope": scope,
            "open_positions_total": scope["open_positions_total"],
            "open_positions_shown": scope["open_positions_shown"],
            "missing_open_symbols": scope["missing_open_symbols"],
            "coverage_complete": scope["coverage_complete"],
            "price_age_s": self.price_age_s(),
            "heads_age_s": self.heads_age_s(),
        }

    def fee_schedule(self) -> Any:
        """Defterin ücret tarifesi — tahmini kapanış ücreti bundan hesaplanır."""
        return (self.get("futures_ledger") or {}).get("fees")

    def view_model(self, *, stale_price_s: int = 90, stale_run_s: int = 2400,
                   tz_label: str = "UTC") -> dict[str, Any]:
        """Panel + Telegram için TEK kanonik görünüm (bkz. `dashboard.views.build`)."""
        from .views import Freshness, build
        fresh = Freshness(price_age_s=self.price_age_s(), run_age_s=self.last_run_age(),
                          heads_age_s=self.heads_age_s(), heartbeat_age_s=self.heartbeat_age(),
                          stale_price_s=stale_price_s, stale_run_s=stale_run_s, tz_label=tz_label)
        return build(self.futures_positions(), self.trades(),
                     (self.get("coin_heads") or {}).get("chief") or (self.get("agents") or {}).get("chief") or {},
                     marks=self.marks(), fees=self.fee_schedule(),
                     today=utc_now().date().isoformat(), max_drawdown_pct=self.max_drawdown_pct(),
                     freshness=fresh,
                     futures_equity=self.futures_equity(), spot_equity=self.spot_equity(),
                     risk_state=self.get("risk"), as_of=utc_now().isoformat(timespec="seconds"),
                     # Risk anlık görüntüsü de strateji turunda yazılır → AYNI tazelik eşiği
                     # (`stale_run_s`) kullanılır; yeni/keyfî bir eşik UYDURULMAZ.
                     risk_age_s=self.risk_age_s(), risk_stale_s=stale_run_s)


def _evidence(self, base: str) -> dict | None:
    import json as _j
    b = base.upper().replace("/", "_")
    for name in (b, f"{b}_USDT"):
        p = Path(self.state_dir) / "evidence" / f"{name}.json"
        if p.exists():
            try:
                return _j.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return None
    return None


StateReader.evidence = _evidence

__all__ = ["StateReader", "STATE_FILES", "JSONL_FILES"]
