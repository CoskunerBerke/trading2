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
    "spot_ledger": "spot_ledger.json",
    "orders": "orders.json", "triggers": "triggers.json",
    "snapshot_telemetry": "snapshot_telemetry.json", "research_policy": "research_policy.json",
    "decision_funnel": "decision_funnel.json",
    "quant_eval": "quant_eval.json",
    "universe_eval": "universe_eval.json",
    # PAPER LEARNING LOOP INTEGRITY V3 — ikisi de SALT OKUNUR gözlem belgesidir.
    "learning_chain": "learning_chain.json",
    "position_management": "position_management.json",
    # EXIT GIVEBACK & PROFIT PROTECTION V1 — salt okunur karşı-olgusal rapor.
    "exit_eval": "exit_eval.json",
    # ENTRY SELECTIVITY CHALLENGER V1 — salt okunur karşı-olgusal giriş raporu.
    "entry_selectivity": "entry_selectivity.json",
    "mtf_eval": "mtf_eval.json",
    # LLM alt sisteminin GERÇEK durumu (DISABLED / NOT_CONFIGURED / NO_CALLS / ACTIVE).
    "llm_status": "llm_status.json",
}
JSONL_FILES: dict[str, str] = {"llm_calls": "llm_calls.jsonl", "trade_memory": "trade_memory.jsonl", "signals_log": "signals_log.jsonl",
                               "decision_journal": "decision_journal.jsonl",
                               "position_path": "position_path.jsonl",
                               "entry_snapshot": "entry_snapshot.jsonl"}


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

    def count_jsonl_lines(self, name: str) -> int:
        """Boş olmayan fiziksel satır sayısı — JSON AYRIŞTIRMAZ (ucuz akış sayımı)."""
        fn = JSONL_FILES.get(name)
        if not fn:
            return 0
        p = self.state_dir / fn
        if not p.exists():
            return 0
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                return sum(1 for ln in fh if ln.strip())
        except OSError:
            return 0

    def experience_index(self, dirname: str = "experience_index",
                         shadow_archive_dirname: str = "shadow_archive") -> dict[str, Any]:
        """Uzun vadeli deneyim indeksinin SALT OKUNUR durumu — shard/segment AÇMAZ.

        `retrieval_scope` DÜRÜSTTÜR: indeks yoksa/boşsa `HOT_ONLY`, bozuksa `DEGRADED`;
        yalnız gerçekten indekslenmiş satır varsa `HOT_PLUS_INDEXED_HISTORY`. Eksik/bozuk
        manifest 500 ÜRETMEZ.
        """
        base: dict[str, Any] = {"available": False, "indexed_experiences": 0,
                                "indexed_real": 0, "indexed_shadow": 0,
                                "processed_segments": 0, "corrupt_segments": 0,
                                "skipped_rows": 0, "oldest_label_ms": None,
                                "newest_label_ms": None, "index_lag_segments": 0,
                                "last_refresh_at": None, "last_rebuild_at": None,
                                "index_health": "ABSENT", "last_index_error": None,
                                "retrieval_scope": "HOT_ONLY",
                                "no_lookahead": "AS_OF_ENFORCED_FAIL_CLOSED",
                                "rebuildable_from_archive": True}
        mpath = self.state_dir / dirname / "manifest.json"
        if not mpath.exists():
            return base
        try:
            doc = read_json(mpath, default=None)
        except Exception:  # noqa: BLE001
            doc = None
        if not isinstance(doc, dict):
            base.update({"index_health": "DEGRADED", "retrieval_scope": "DEGRADED",
                         "last_index_error": "INDEX_MANIFEST_UNREADABLE"})
            return base

        def _i(v: Any) -> int:
            return int(v) if isinstance(v, (int, float)) else 0

        t = doc.get("totals") if isinstance(doc.get("totals"), dict) else {}
        processed = [s for s in (doc.get("processed") or []) if isinstance(s, dict)]
        corrupt = [s for s in (doc.get("corrupt_segments") or [])]
        health = str(doc.get("health") or "EMPTY")
        # Gecikme: arşivde olup indekste olmayan segment sayısı (iki manifest, segment açılmaz).
        lag = 0
        try:
            adoc = read_json(self.state_dir / shadow_archive_dirname / "manifest.json",
                             default=None)
            if isinstance(adoc, dict):
                n_arc = len([s for s in (adoc.get("segments") or []) if isinstance(s, dict)])
                lag = max(0, n_arc - len(processed))
        except Exception:  # noqa: BLE001
            lag = 0
        if lag > 0 and health == "OK":
            health = "STALE"
        rows = _i(t.get("rows"))
        # Aggregate senkronu: aggregates.json'daki uygulanmış segment kümesi, manifestteki
        # işlenmiş kümeyle birebir aynıysa tam-geçmiş toplam hafızası SAĞLIKLIDIR.
        agg_synced = False
        agg_outcomes = 0
        try:
            adoc2 = read_json(self.state_dir / dirname / "aggregates.json", default=None)
            if isinstance(adoc2, dict):
                applied = {str(x) for x in (adoc2.get("applied_segments") or [])}
                agg_synced = applied == {str(x.get("segment_id")) for x in processed}
                agg_outcomes = _i((adoc2.get("book") or {}).get("total_added"))
        except Exception:  # noqa: BLE001
            agg_synced = False
        if health in ("DEGRADED", "FAILED"):
            scope = "DEGRADED"
        elif rows > 0 and health == "OK" and lag == 0 and agg_synced:
            scope = "FULL_HISTORY_BOUNDED"
        elif rows > 0 and health in ("OK", "STALE"):
            scope = "HOT_PLUS_RECENT_INDEX"
        else:
            scope = "HOT_ONLY"
        base.update({"available": True, "indexed_experiences": rows,
                     "indexed_real": _i(t.get("real")), "indexed_shadow": _i(t.get("shadow")),
                     "processed_segments": len(processed), "corrupt_segments": len(corrupt),
                     "skipped_rows": sum(_i(s.get("n_skipped")) for s in processed),
                     "oldest_label_ms": t.get("oldest_label_ms"),
                     "newest_label_ms": t.get("newest_label_ms"),
                     "index_lag_segments": lag,
                     "last_refresh_at": doc.get("last_refresh_at"),
                     "last_rebuild_at": doc.get("last_rebuild_at"),
                     "index_health": health, "last_index_error": doc.get("last_error"),
                     "aggregate_synced": agg_synced, "aggregate_outcomes": agg_outcomes,
                     "retrieval_scope": scope})
        return base

    def decision_retention(self, dirname: str = "decision_archive") -> dict[str, Any]:
        """Aktif günlük + kayıpsız arşivin SALT OKUNUR saklama özeti.

        Manifest eksik/eski/bozuk olsa bile `available=False` ile döner; ASLA istisna sızdırmaz
        (dashboard 500 üretmemelidir). Segment dosyaları AÇILMAZ — maliyet O(1).
        """
        hot = self.count_jsonl_lines("decision_journal")
        base: dict[str, Any] = {"hot_records": hot, "archived_records": 0,
                                "lifetime_records": hot, "n_segments": 0,
                                "oldest_ts": None, "newest_ts": None,
                                "last_rotation_at": None, "archive_health": "ABSENT",
                                "last_archive_error": None,
                                "retention_policy": "UNKNOWN", "deleted_segments": 0,
                                "silent_deletion": False, "archive_available": False}
        # Retrieval kapsamı KARAR ARŞİVİNİN varlığından TÜRETİLEMEZ: canlı retrieval'ın kaynağı
        # deneyim indeksidir. Kapsam gerçek indeks durumundan okunur (dürüst raporlama).
        base["retrieval_scope"] = self.experience_index().get("retrieval_scope", "HOT_ONLY")
        mpath = self.state_dir / dirname / "manifest.json"
        if not mpath.exists():
            return base
        try:
            doc = read_json(mpath, default=None)
        except Exception:  # noqa: BLE001 — bozuk manifest 500 ÜRETMEZ
            doc = None
        if not isinstance(doc, dict):
            base["archive_health"] = "DEGRADED"
            base["last_archive_error"] = "MANIFEST_UNREADABLE"
            return base
        t = doc.get("totals") if isinstance(doc.get("totals"), dict) else {}

        def _i(v: Any) -> int:
            return int(v) if isinstance(v, (int, float)) else 0

        archived = _i(t.get("records"))
        base.update({"archived_records": archived, "lifetime_records": hot + archived,
                     "archived_decisions": _i(t.get("decisions")),
                     "archived_outcomes": _i(t.get("outcomes")),
                     "archive_bytes_compressed": _i(t.get("bytes_compressed")),
                     "n_segments": _i(t.get("segments")),
                     "oldest_ts": t.get("first_ts"), "newest_ts": t.get("last_ts"),
                     "last_rotation_at": doc.get("last_rotation_at"),
                     "archive_health": doc.get("health") or "EMPTY",
                     "last_archive_error": doc.get("last_error"),
                     "retention_policy": doc.get("retention_policy") or "UNKNOWN",
                     "deleted_segments": _i(doc.get("deleted_segments")),
                     "pending_trim": bool(doc.get("pending_trim")),
                     "archive_available": True})
        return base

    def coin_memory(self, base: str) -> dict[str, Any]:
        """Coin'e özel hiyerarşik bellek özeti — SALT OKUNUR, eksik veride 500 YOK.

        Gerçek kapanışlar (TradeMemory) + gölge sonuçlar (aktif defter) + tam-geçmiş
        toplamları (experience_index/aggregates.json L2/L3 hücreleri) + son karar etkisi.
        """
        b = str(base).upper().split("/")[0]
        sym = f"{b}/USDT"
        out: dict[str, Any] = {"symbol": sym, "available": False,
                               "real": {"n": 0}, "shadow": {"n": 0},
                               "aggregate": None, "by_direction": {},
                               "last_influence": None, "date_range": {},
                               "consistency": None}

        def _num(x: Any) -> float | None:
            try:
                v = float(x)
                return v if v == v else None
            except (TypeError, ValueError):
                return None

        # --- gerçek kapanışlar
        rs: list[float] = []
        maes: list[float] = []
        mfes: list[float] = []
        exits: dict[str, int] = {}
        first = last = None
        for row in self.tail_jsonl("trade_memory", 4000):
            if str(row.get("symbol") or "") != sym:
                continue
            if row.get("kind") == "exit":
                o = row.get("outcome") or {}
                r = _num(o.get("r_multiple"))
                if r is not None:
                    rs.append(r)
                if _num(o.get("mae_pct")) is not None:
                    maes.append(float(o["mae_pct"]))
                if _num(o.get("mfe_pct")) is not None:
                    mfes.append(float(o["mfe_pct"]))
                er = str(o.get("exit_reason") or "?")
                exits[er] = exits.get(er, 0) + 1
            ts = str(row.get("recorded_at") or "")
            if ts:
                first = ts if first is None or ts < first else first
                last = ts if last is None or ts > last else last
        out["real"] = {"n": len(rs),
                       "avg_r": round(sum(rs) / len(rs), 4) if rs else None,
                       "wins": sum(1 for r in rs if r > 0),
                       "losses": sum(1 for r in rs if r < 0),
                       "avg_mae_pct": round(sum(maes) / len(maes), 3) if maes else None,
                       "avg_mfe_pct": round(sum(mfes) / len(mfes), 3) if mfes else None,
                       "exit_reasons": exits, "weight": 1.0}
        # --- aktif gölge
        sh = self.get("shadow_book") or {}
        sh_rows = [t for t in (sh.get("trades") or [])
                   if isinstance(t, dict) and str(t.get("symbol")) == sym]
        sh_lab = [t for t in sh_rows if isinstance(t.get("outcome"), dict)]
        sh_rs = [_num((t.get("outcome") or {}).get("r_multiple")) for t in sh_lab]
        sh_rs = [r for r in sh_rs if r is not None]
        out["shadow"] = {"n": len(sh_rows), "labeled": len(sh_lab),
                         "avg_r": round(sum(sh_rs) / len(sh_rs), 4) if sh_rs else None,
                         "weight": "shadow_weight×fidelity (gerçekten DAİMA düşük)"}
        # --- tam-geçmiş toplamları (aggregates.json — L3 sembol, L2 sembol|yön|setup)
        try:
            adoc = read_json(self.state_dir / "experience_index" / "aggregates.json",
                             default=None)
            cells = ((adoc or {}).get("book") or {}).get("cells") or {}
            l3 = cells.get(f"3|{sym.replace('|', '_')}") or {}
            n = w = wr = 0.0
            months = sorted(l3)
            for st_ in l3.values():
                n += float(st_.get("n") or 0)
                w += float(st_.get("w") or 0)
                wr += float(st_.get("wr") or 0)
            if n:
                out["aggregate"] = {"n": int(n), "mean_r": round(wr / w, 4) if w else None,
                                    "months": len(months),
                                    "first_month": months[0] if months else None,
                                    "last_month": months[-1] if months else None}
            by_dir: dict[str, Any] = {}
            for key, mrows in cells.items():
                if not key.startswith("2|"):
                    continue
                parts = key.split("|")
                if len(parts) >= 4 and parts[1] == sym.replace("|", "_"):
                    dn, wn, wrn = 0.0, 0.0, 0.0
                    for st_ in mrows.values():
                        dn += float(st_.get("n") or 0)
                        wn += float(st_.get("w") or 0)
                        wrn += float(st_.get("wr") or 0)
                    d = by_dir.setdefault(parts[2], {"n": 0, "setups": {}})
                    d["n"] += int(dn)
                    d["setups"][parts[3]] = {"n": int(dn),
                                             "mean_r": round(wrn / wn, 4) if wn else None}
            out["by_direction"] = by_dir
        except Exception:  # noqa: BLE001
            pass
        # --- son karar etkisi + tarih aralığı + tutarlılık
        for row in reversed(self.tail_jsonl("decision_journal", 2000)):
            if row.get("symbol") == sym and row.get("learning_influence"):
                out["last_influence"] = row["learning_influence"]
                out["last_decision_ts"] = row.get("decision_ts")
                out["last_why_tr"] = row.get("why_summary_tr")
                break
        out["date_range"] = {"first": first, "last": last}
        all_rs = rs + sh_rs
        if all_rs:
            pos = sum(1 for r in all_rs if r > 0)
            out["consistency"] = round(abs(2 * pos / len(all_rs) - 1), 4)
        out["available"] = bool(rs or sh_rows or out["aggregate"])
        return out

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
                     futures_ledger_doc=self.get("futures_ledger"),
                     spot_ledger_doc=self.get("spot_ledger"),
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
