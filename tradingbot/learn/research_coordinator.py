"""ResearchCoordinator — canlı PAPER kapanışlarını araştırma döngüsüne bağlayan EKSİK HALKA.

Motor yalnız `evaluate_active()`/`maybe_activate()` çağırıyordu; `propose`/`record_offline`/
`start_shadow` hiçbir çalışma yolunda yoktu, dolayısıyla boş `research_policy.json` kendiliğinden
asla dolmuyor ve durum makinesi kilitleniyordu. Bu modül o zinciri kapatır:

    canlı LIVE_PAPER hafızası (entry+outcome join)
    → coverage gate + leakage kontrolü
    → loss attribution
    → sınırlı/açıklanabilir aday üretimi
    → kronolojik anchored walk-forward (train < purge < embargo < test)
    → offline verdict → SHADOW
    (aktivasyon ve emeklilik `ResearchPolicyBook` kapılarında)

Sözleşme:
* Her turda ağır iş YAPILMAZ: asgari yeni kapanış + cooldown kapısı birlikte geçilmelidir.
* Durum atomik ve restart'a dayanıklıdır (`state/research_coordinator.json`).
* MUTLAK MOD KAPISI: üretim, gözlem, aktivasyon ve uygulama yalnız
  `mode == PAPER` + `execution.gateway == paper` + `live_order_path_enabled == false`
  koşullarının TAMAMINDA mümkündür. Diğer modlarda katman salt-okunurdur; durum geçişi yapılmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, from_iso, iso, read_json, utc_now
from .attribution import attribution_report
from .coverage import coverage_report
from .memory import TradeMemory
from .policy import candidates_from_attribution
from .research_policy import ResearchPolicyBook

SCHEMA = "research_coordinator_v1"
STATE_FILENAME = "research_coordinator.json"
DEFAULT_BAR_MS = 14_400_000                      # 4h


@dataclass
class CoordinatorConfig:
    enabled: bool = True
    min_new_closed: int = 20            # döngüyü tetiklemek için gereken YENİ kapanış
    cooldown_hours: float = 12.0        # iki araştırma turu arasındaki asgari süre
    min_rows: int = 40                  # walk-forward için gereken asgari kronolojik kapanış
    n_folds: int = 4
    bar_ms: int = DEFAULT_BAR_MS
    purge_bars: int = 1
    embargo_bars: int = 1
    max_candidates: int = 12
    min_bucket: int = 8
    seed: int = 7
    min_test_trades: int = 8


# --------------------------------------------------------------------------- mod kapısı
def mode_gate(mode_value: str, gateway: str, live_order_path_enabled: bool) -> tuple[bool, str]:
    """Araştırma katmanının çalışabileceği TEK bileşim. Aksi halde salt-okunur baseline."""
    if str(mode_value or "").upper() != "PAPER":
        return False, f"MODE_NOT_PAPER:{mode_value}"
    if str(gateway or "").lower() != "paper":
        return False, f"GATEWAY_NOT_PAPER:{gateway}"
    if bool(live_order_path_enabled):
        return False, "LIVE_ORDER_PATH_ENABLED"
    return True, "OK"


# --------------------------------------------------------------------------- kronolojik fold sınırları
def anchored_bounds(rows: list[dict], *, n_folds: int = 4, bar_ms: int = DEFAULT_BAR_MS,
                    purge_bars: int = 1, embargo_bars: int = 1) -> list[dict]:
    """Deterministik anchored walk-forward: train DAİMA testten önce, aralarında purge+embargo.

    Test pencereleri giriş zamanına göre AYRIKTIR → aynı `trade_id` en fazla bir test fold'una girer
    (`_fold_rows` giriş zamanına bakar). Yetersiz örnekte boş liste döner (çağıran BLOCKED üretir).
    """
    ts = sorted(int(r["_open_ms"]) for r in rows if r.get("_open_ms") is not None)
    n_folds = max(1, int(n_folds))
    if len(ts) < (n_folds + 1) * 2:
        return []
    purge_ms, emb_ms = int(purge_bars) * int(bar_ms), int(embargo_bars) * int(bar_ms)
    chunk = len(ts) // (n_folds + 1)
    out: list[dict] = []
    for k in range(n_folds):
        train_end = ts[chunk * (k + 1)]
        purge_end = train_end + purge_ms
        emb_end = purge_end + emb_ms
        last = (k == n_folds - 1)
        test_end = (ts[-1] + 1) if last else ts[chunk * (k + 2)]
        if test_end <= emb_end:
            continue                                  # embargo test penceresini yuttu → fold atlanır
        out.append({"idx": k, "train_start_ms": ts[0], "train_end_ms": train_end,
                    "purge_start_ms": train_end, "purge_end_ms": purge_end,
                    "embargo_start_ms": purge_end, "embargo_end_ms": emb_end,
                    "test_start_ms": emb_end, "test_end_ms": test_end,
                    "purge_bars": int(purge_bars), "embargo_bars": int(embargo_bars), "bar_ms": int(bar_ms)})
    return out


def _ms(v: Any) -> int | None:
    if not v:
        return None
    try:
        return int(from_iso(str(v)).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def join_live_rows(memory_path: Path | str) -> list[dict]:
    """Canlı LIVE_PAPER hafızasını `trade_id` üzerinden entry+outcome olarak birleştirir.

    `TradeMemory.trades(closed_only=True)` join'i yapar; burada yalnız walk-forward için gereken
    kronolojik alanlar eklenir ve zamanı çözülemeyen kayıtlar DIŞLANIR (uydurma zaman yok).
    """
    mem = TradeMemory(Path(memory_path), source="LIVE_PAPER")
    rows = []
    for r in mem.trades(closed_only=True):
        out = r.get("outcome") or {}
        o = _ms(out.get("opened_at") or r.get("recorded_at"))
        c = _ms(out.get("closed_at") or r.get("recorded_at"))
        if o is None or c is None or c < o:
            continue
        r["_open_ms"], r["_close_ms"] = o, c
        rows.append(r)
    rows.sort(key=lambda r: r["_open_ms"])
    return rows


class ResearchCoordinator:
    """Araştırma döngüsünün tek orkestrasyon noktası. Motor her turda `tick()` çağırır."""

    def __init__(self, book: ResearchPolicyBook, *, memory_path: Path | str, state_path: Path | str,
                 cfg: CoordinatorConfig | None = None, risk_profile_max_leverage: float = 1.0,
                 bot_cfg: Any = None):
        self.book = book
        self.memory_path = Path(memory_path)
        self.state_path = Path(state_path)
        self.cfg = cfg or CoordinatorConfig()
        self.bot_cfg = bot_cfg                      # `evaluate_policies` risk profili tavanını buradan okur
        self.risk_profile_max_leverage = float(risk_profile_max_leverage)
        d = read_json(self.state_path / STATE_FILENAME, default=None) or {}
        self.last_run_at: str = str(d.get("last_run_at") or "")
        self.closes_since_run: int = int(d.get("closes_since_run") or 0)
        self.last_result: dict = dict(d.get("last_result") or {})

    # ------------------------------------------------------------ kalıcılık
    def save(self) -> None:
        atomic_write_json(self.state_path / STATE_FILENAME, {
            "schema": SCHEMA, "updated_at": iso(utc_now()), "last_run_at": self.last_run_at,
            "closes_since_run": self.closes_since_run, "config": self.cfg.__dict__,
            "last_result": self.last_result})

    def _due(self, now: Any) -> bool:
        """Her turda ağır iş yok: asgari yeni kapanış VE cooldown birlikte geçilmeli."""
        if self.closes_since_run < self.cfg.min_new_closed:
            return False
        if self.last_run_at:
            try:
                if (now - from_iso(self.last_run_at)).total_seconds() < self.cfg.cooldown_hours * 3600:
                    return False
            except (ValueError, TypeError):
                pass
        return True

    # ------------------------------------------------------------ ana giriş
    def tick(self, *, now: Any, mode_value: str, gateway: str, live_order_path_enabled: bool,
             n_new_closes: int = 0) -> dict:
        """Motorun her turda çağırdığı tek nokta. Mod kapısı geçilmezse HİÇBİR geçiş yapılmaz."""
        allowed, reason = mode_gate(mode_value, gateway, live_order_path_enabled)
        if not (self.cfg.enabled and allowed):
            res = {"ran": False, "mode_allowed": allowed, "reason": (reason if allowed else reason),
                   "read_only": True, "at": iso(now)}
            self.last_result = res
            self.save()
            return res
        self.closes_since_run += int(n_new_closes)
        out: dict = {"ran": False, "mode_allowed": True, "reason": "OK", "read_only": False, "at": iso(now)}
        # 1) aktif adayı denetle (kötüleşme → atomik RETIRED, anında baseline)
        out["evaluate_active"] = self.book.evaluate_active(now=now)
        # 2) tıkanmış gölge adayı topla (aksi halde döngü tek adayda kilitlenirdi)
        out["reaped_shadow"] = self._reap_shadow(now=now)
        # 3) cooldown + yeni kapanış kapısı geçildiyse yeni aday üret ve offline değerlendir
        if self._due(now) and self.book.shadow() is None and self.book.active() is None:
            out |= self._propose_cycle(now=now)
            self.last_run_at, self.closes_since_run = iso(now), 0
            out["ran"] = True
        # 3) SHADOW yeterli eşleşmiş gözlem + istatistik kapılarını geçtiyse aktifleş
        out["activated"] = self.book.maybe_activate(now=now)
        self.last_result = out
        self.save()
        return out

    def _reap_shadow(self, *, now: Any) -> str | None:
        """Aktifleşemeyecek gölge adayı emekli eder ki döngü yeni bir adaya geçebilsin.

        İki durum: (a) `RESEARCH_ONLY` — tanım gereği asla aktifleşemez; (b) yeterli gözlemin iki
        katına ulaşmış ama farkı emeklilik eşiğinin altında kalmış aday (gölgede kanıtlanmış kötü).
        """
        rec = self.book.shadow()
        if rec is None or not self.book.cooldown_ok(rec, now):
            return None
        g, st = self.book.gates, rec.stats()
        if rec.research_only and st["n_obs"] >= g.min_shadow_obs:
            self.book._set_state(rec, "RETIRED", at=now,
                                 reason="RESEARCH_ONLY aday aktifleşemez → yeni adaya geçiliyor")
            self.book.save()
            return rec.policy_id
        if st["n_obs"] >= 2 * g.min_shadow_obs and st["delta_r"] is not None and st["delta_r"] <= g.retire_delta_r:
            self.book._set_state(rec, "RETIRED", at=now,
                                 reason=(f"gölgede kanıtlanmış kötü: {st['n_obs']} gözlemde "
                                         f"fark {st['delta_r']:+.4f}R"))
            self.book.save()
            return rec.policy_id
        return None

    # ------------------------------------------------------------ üretim turu
    def _propose_cycle(self, *, now: Any) -> dict:
        """Canlı hafızadan aday üretip offline değerlendirir. Her kapı hatası BLOCKED üretir."""
        c = self.cfg
        rows = join_live_rows(self.memory_path)
        if len(rows) < c.min_rows:
            return {"status": "BLOCKED", "code": "INSUFFICIENT_CLOSED_TRADES",
                    "detail": f"{len(rows)} < {c.min_rows}"}
        cov = coverage_report(rows, source="LIVE_PAPER")
        if not cov["ok"]:
            return {"status": "BLOCKED", "code": cov["code"], "detail": cov["problems"][:4],
                    "coverage": {k: cov[k] for k in ("required_available_pct", "overall_available_pct")}}
        if cov["invalid_timestamps"]:
            return {"status": "BLOCKED", "code": "TIMESTAMP_LEAKAGE",
                    "detail": f"{cov['invalid_timestamps']} kayıtta son bar karar anından sonra"}
        if cov["join"]["broken"]:
            return {"status": "BLOCKED", "code": "JOIN_BROKEN", "detail": cov["join"]}
        bounds = anchored_bounds(rows, n_folds=c.n_folds, bar_ms=c.bar_ms,
                                 purge_bars=c.purge_bars, embargo_bars=c.embargo_bars)
        if len(bounds) < 2:
            return {"status": "BLOCKED", "code": "INSUFFICIENT_FOLDS", "detail": len(bounds)}
        attrib = attribution_report(rows, min_bucket=c.min_bucket)
        cands = candidates_from_attribution(attrib, seed=c.seed, max_candidates=c.max_candidates,
                                            risk_profile_max_leverage=self.risk_profile_max_leverage)
        # daha önce denenmiş (deftere girmiş) adayları tekrar önerme
        seen = {r.policy_id for r in self.book.records}
        cands = [p for p in cands if p.policy_id not in seen]
        if not cands:
            return {"status": "BLOCKED", "code": "NO_NEW_CANDIDATE",
                    "detail": len(attrib.get("negative_findings") or [])}
        report = self._evaluate(rows, bounds, cands, now=now)
        if report is None:
            return {"status": "BLOCKED", "code": "EVALUATION_FAILED"}
        winner_id = report.get("most_selected") or (report.get("selected_policies") or [None])[0]
        winner = next((p for p in cands if p.policy_id == winner_id), cands[0])
        self.book.propose(winner, now=now)
        state = self.book.record_offline(winner.policy_id, report, now=now)
        return {"status": "OK", "n_rows": len(rows), "n_folds": len(bounds), "n_candidates": len(cands),
                "proposed": winner.policy_id, "changed_params": winner.changed_params(),
                "rationale": winner.rationale, "verdict": report.get("verdict"), "state": state,
                "coverage": {k: cov[k] for k in ("required_available_pct", "overall_available_pct",
                                                 "prediction_available_pct")}}

    def _evaluate(self, rows: list[dict], bounds: list[dict], cands: list, *, now: Any) -> dict | None:
        """Walk-forward: aday YALNIZ train/validation'da seçilir; test fold'u seçime GİRMEZ.

        Rapor canlı state'in ALTINDA ayrı bir `research/` klasörüne yazılır; replay run dizinlerine
        ve eski Core-4 artifact'lerine DOKUNULMAZ.
        """
        from ..replay.policy_eval import evaluate_policies
        out_dir = self.state_path / "research"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            return evaluate_policies(self.bot_cfg, out_dir, rows, bounds,
                                     seed=self.cfg.seed, min_test_trades=self.cfg.min_test_trades,
                                     candidates=list(cands), point_in_time=True,
                                     survivorship_present=False)
        except Exception:  # noqa: BLE001 — değerlendirme hatası araştırma turunu bloklar, motoru DURDURMAZ
            return None


__all__ = ["CoordinatorConfig", "DEFAULT_BAR_MS", "ResearchCoordinator", "STATE_FILENAME",
           "anchored_bounds", "join_live_rows", "mode_gate"]
