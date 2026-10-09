"""P2 — gerçek işlemin yeniden oynatma doğruluğu kapısı (`fidelity`; §5.3).

Her gerçek vadeli işlem, defterin KENDİ yürütme modeliyle (`learning_cf.ExecModel`; ücret tablosu, kayma, TP1 oranı,
MFE başa-baş, TP maker, likidasyon, en-kötü-durum — ledger JSON'undan, `ExecModel.of_ledger`'ın okuduğu AYNI alanlar)
atılabilir bir `FuturesLedgerV2` içinde gerçek yol üzerinde yeniden oynatılır. Kod yolu `cf_label_v3` ile AYNIDIR:
`learning_cf._net_replay`'in kancalı kopyası `cfgrid._replay_ext` (kancasız hâli bayt-özdeş, test; bar içi nedensel yol
açılış → ters uç → lehte uç → kapanış, seviyeden stop dolumu, yalnız bar açılışında boşluk) — tek kanca kaydın gözlemden
stop dolumudur (aşağıda). Atılık defter hiçbir yere yazılmaz.

* Girdiler: giriş referansı = kaydın giriş dolumunun `ref_price`'ı (canlı mark; defter kaymayı yine kendisi uygular),
  ilk stop = `features.initial_stop`, hedefler = günlüğün hedefleri (`xp_entry`/provenance ÖLÇÜLMÜŞ, yoksa rehydrate),
  yol = `pathrec` işlem barları (`opened < ts < closed`), funding = kaydın KENDİ settlement'ları
  (`features.funding_settlements`: oran + settlement mark'ı; sıfır oranlı dönemler watermark'a kadar 0).
* **R paydası iki tarafta da kaydın `features.risk_usdt`'idir.** `_net_replay` R'yi kendi (1000 USDT, 1x) boyutunun
  riskine böler; birim başına net aynı olduğundan gerçek paydaya `r_replay = r_net × |dolum_replay − stop| /
  |dolum_gerçek − stop|` ile çevrilir (gerçek risk = |dolum − ilk stop| × miktar, `futures_ledger._finalize`).
* **Gözlemden dolum (2026-10-08, inceleme B1).** Canlı defter stopu seviyeden değil, seviyenin ötesindeki İLK gözlemden
  doldurur (60 sn örnek `GAP_FILL_AT_FIRST_OBSERVATION`/`PRICE`, kural barı açılışı `…/BAR_OPEN`, ihtiyatlı kapanış
  `STOP_CLOSE_BEYOND_LEVEL_PRUDENT`). Yeniden oynatma bunu MODELLER: stop, kaydın çıkış anından geriye bir kural dilimi
  penceresi (`rule_tf_ms`; `(kapanış − pencere, kapanış]` ile kesişen bar) içinde tetiklenirse dolum referansı kaydın gözlem
  fiyatıdır (`cfgrid._replay_ext(stop_fill=…)`, `fill_model = RECORDED_FILL`; kayma modeli yine uygulanır). Pencere dışında
  (örneklerin kaçırdığı eski bir fitil) yeniden oynatma seviyeden doldurur ve fark gerçek bir farktır (`FILL_BASIS`).
* `|r_replay − r_actual| ≤ 0,05R` → `ok` (işlem P2b'nin karşı-olgusal ayrıştırmasına girer). Değilse yalnız kural kodları
  alır ve nedenleri yazılır: `PATH_GAP` (yol boşluğu), `AMBIGUOUS_BAR` (belirsiz bar içi sıra), `FILL_BASIS` (farklı
  dolum tabanı: gerçek boşluk/kapanış dolumu ↔ yeniden oynatmada seviye), `EXIT_REASON_DIFFERS`, `BACKDATED_CLOSE`
  (geriye tarihli kapanış), `ENTRY_FILL` (giriş dolumu farkı > 1 tick), kalan `UNEXPLAINED`.
* Uygun olmayan işlemler ayrı sayılır (oranın paydasına girmez): `SPOT` (R yok/defter modeli farklı), `NO_RISK` (eski
  kayıt), `NO_PATH`, `NO_EXEC_MODEL`, `TARGETS_MISSING` (hedefle çıkmış ama hedef bilinmiyor).
* Kapı (§5.3): doğruluk oranı ≥ %90 (`summarize`).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

import pandas as pd

from .ledgers import dec, dec_or_none, iso, parse_ts

FIDELITY_VERSION = "fidelity_v2"   # 2026-10-08: gözlemden stop dolumu modellenir (B1)
TOLERANCE_R = Decimal("0.05")
TARGET_RATE = 0.90
ST_OK, ST_FAIL, ST_NOT_ELIGIBLE = "OK", "FAILED", "NOT_ELIGIBLE"
#: başarısızlık nedenleri — birincil neden bu sırayla seçilir
REASON_ORDER = ("PATH_GAP", "AMBIGUOUS_BAR", "FILL_BASIS", "EXIT_REASON_DIFFERS", "BACKDATED_CLOSE", "ENTRY_FILL",
                "REPLAY_ERROR", "UNEXPLAINED")
NOT_ELIGIBLE = ("SPOT", "NO_RISK", "NO_PATH", "NO_EXEC_MODEL", "TARGETS_MISSING", "RECORD_UNREADABLE")
_TARGET_EXITS = ("hedef1", "hedef2")
_GAP_BASES = ("GAP_FILL_AT_FIRST_OBSERVATION", "STOP_CLOSE_BEYOND_LEVEL_PRUDENT", "FIRST_OBSERVATION_BEYOND_LIQUIDATION",
              "GAP_FILL_AT_BAR_OPEN")
#: kaydın gözlemden (seviyenin ötesinde) dolan stop tabanları — yeniden oynatmada `RECORDED_FILL` ile modellenir
_OBSERVED_STOP_BASES = ("GAP_FILL_AT_FIRST_OBSERVATION", "STOP_CLOSE_BEYOND_LEVEL_PRUDENT", "GAP_FILL_AT_BAR_OPEN")
RECORDED_FILL = "RECORDED_FILL"
_STOP_EXITS = ("stop", "başa-baş stop")
EXEC_KEYS = ("fees", "slippage", "liq_params", "tp1_fraction", "breakeven_at_mfe_r", "tp_maker", "worst_case")


# ============================================================================ yürütme modeli (ledger JSON'undan)
def exec_params(doc: dict | None) -> dict[str, Any] | None:
    """Ledger belgesinden yürütme modelinin ham alanları (küçük, JSON'a yazılabilir; `ExecModel.of_ledger`'ın okuduğu
    alanlar). Belge yoksa/eksikse None."""
    if not isinstance(doc, dict) or not isinstance(doc.get("fees"), dict):
        return None
    return {k: doc.get(k) for k in EXEC_KEYS}


def exec_model(params: dict | None, *, hours: tuple[int, ...] | None = None) -> Any:
    """`ExecModel` (learning_cf) — `exec_params` çıktısından. `hours`: kaydın `funding_hours_utc`'si (sembolün aralığı)."""
    if not params:
        return None
    from ..accounting import FeeSchedule, LiquidationParams, SlippageModel
    from ..accounting.funding import FundingSchedule
    from ..core import D
    from ..learning_cf import ExecModel
    lp = params.get("liq_params") or {}
    fd = FundingSchedule()
    hrs = tuple(int(h) for h in hours) if hours else None
    return ExecModel(fees=FeeSchedule.from_dict(params.get("fees") or {}),
                     slippage=SlippageModel.from_dict(params.get("slippage") or {}),
                     tp1_fraction=D(params.get("tp1_fraction") if params.get("tp1_fraction") is not None else "0.5"),
                     breakeven_at_mfe_r=D(params.get("breakeven_at_mfe_r") if params.get("breakeven_at_mfe_r") is not None else "0"),
                     tp_maker=bool(params.get("tp_maker", False)),
                     liq_params=LiquidationParams(**{k: v for k, v in lp.items() if k in ("liq_fee_pct", "fee_cushion_pct", "use_brackets")}),
                     brackets=None, worst_case=bool(params.get("worst_case", True)),
                     funding_hours_utc=tuple(fd.hours_utc), funding_fallback=False,
                     funding_hours_for=(lambda _s, _h=hrs: _h) if hrs is not None else None)


class RecordedFunding:
    """Kaydın kendi settlement'larından oran + settlement mark'ı (yeniden oynatma funding'i GERÇEKLEŞENLE aynı tabanda).
    Kayıtta olmayan ama watermark'a (`funding_settled_until`) kadar olan dönem sıfır oranlıdır (defter sıfır oranlı dönemi
    olay olarak yazmaz); watermark'tan sonrası bilinmez (None → dönem bekler, defterle aynı)."""

    def __init__(self, rec: dict) -> None:
        f = rec.get("features") if isinstance(rec.get("features"), dict) else {}
        self.rows: dict[str, tuple[Decimal, Decimal | None]] = {}
        for r in f.get("funding_settlements") or []:
            if not isinstance(r, dict) or r.get("reversed_at") or r.get("path") == "REVERSAL":
                continue
            t = parse_ts(r.get("settlement"))
            rate = dec_or_none(r.get("rate"))
            if t is None or rate is None:
                continue
            self.rows[iso(t)] = (rate, dec_or_none(r.get("mark")))
        self.until = parse_ts(f.get("funding_settled_until"))

    def __call__(self, symbol: str, when: datetime) -> Decimal | None:
        k = iso(when)
        if k in self.rows:
            return self.rows[k][0]
        if self.until is not None and when <= self.until:
            return Decimal(0)
        return None

    def settlement_mark(self, symbol: str, when: datetime) -> Decimal | None:
        r = self.rows.get(iso(when))
        return r[1] if r else None


# ============================================================================ tek işlem
def _exit_basis(rec: dict) -> str | None:
    ef = (rec.get("features") or {}).get("exit_fill") if isinstance(rec.get("features"), dict) else None
    return str(ef.get("basis")) if isinstance(ef, dict) and ef.get("basis") else None


def _entry_fill(rec: dict) -> tuple[Decimal | None, Decimal | None]:
    fills = [f for f in (rec.get("fills") or []) if isinstance(f, dict) and f.get("kind") == "entry"]
    if not fills:
        return None, dec_or_none(rec.get("entry"))
    return dec_or_none(fills[0].get("ref_price")), dec_or_none(fills[0].get("price"))


def recorded_stop_fill(rec: dict, *, tf: str | None, rule_tf_ms: int | None) -> tuple[Any, str | None]:
    """Kaydın gözlemden stop dolumunun yeniden oynatma kancası: (`stop_fill(bar açılışı ms)` | None, gözlem fiyatı)."""
    f = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    ef = f.get("exit_fill") if isinstance(f.get("exit_fill"), dict) else {}
    b = str(ef.get("basis") or "")
    if str(rec.get("exit_reason") or "") not in _STOP_EXITS or b not in _OBSERVED_STOP_BASES:
        return None, None
    ref = dec_or_none(ef.get("close_price") if b == "STOP_CLOSE_BEYOND_LEVEL_PRUDENT" else ef.get("first_price"))
    c = parse_ts(rec.get("closed_at"))
    if ref is None or c is None:
        return None, None
    cm = int(c.timestamp() * 1000)
    step = 60_000 if tf == "1m" else 300_000
    win = max(int(rule_tf_ms or step), step)

    def hook(bar_ts: int) -> Decimal | None:
        return ref if (int(bar_ts) + step > cm - win and int(bar_ts) <= cm) else None
    return hook, format(ref, "f")


def replay(rec: dict, *, kind: str, bars: pd.DataFrame | None, tf: str | None, path_gaps: int, targets: list[float] | None,
           targets_known: bool, exec_par: dict | None, backdated: bool = False, tick: Decimal | None = None,
           rule_tf_ms: int | None = None) -> dict[str, Any]:
    """Tek kaydın fidelity sonucu (§5.3). Dönen: {status, ok, delta_r, r_replay, r_actual, reasons, primary_reason,
    fill_model, ...}. `rule_tf_ms`: defterin kural (bar işleme) dilimi — gözlem dolumu modelleme penceresi."""
    out: dict[str, Any] = {"version": FIDELITY_VERSION, "status": ST_NOT_ELIGIBLE, "ok": False, "delta_r": None,
                           "r_replay": None, "r_actual": None, "reasons": [], "primary_reason": None,
                           "tolerance_r": format(TOLERANCE_R, "f")}

    def _ne(why: str) -> dict[str, Any]:
        out.update(reasons=[why], primary_reason=why)
        return out
    if kind != "futures":
        return _ne("SPOT")
    f = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    risk = dec_or_none(f.get("risk_usdt"))
    stop = dec_or_none(f.get("initial_stop"))
    if risk is None or risk <= 0 or stop is None:
        return _ne("NO_RISK")
    o, c = parse_ts(rec.get("opened_at")), parse_ts(rec.get("closed_at"))
    ref, fill = _entry_fill(rec)
    if o is None or c is None or ref is None or fill is None:
        return _ne("RECORD_UNREADABLE")
    if bars is None or tf not in ("1m", "5m"):
        return _ne("NO_PATH")
    tb = bars[bars["phase"] == 0] if "phase" in bars.columns else bars
    if not len(tb):
        return _ne("NO_PATH")
    if exec_par is None:
        return _ne("NO_EXEC_MODEL")
    exit_reason = str(rec.get("exit_reason") or "")
    if not targets_known and (exit_reason in _TARGET_EXITS or bool(rec.get("tp1_done"))):
        return _ne("TARGETS_MISSING")
    from ..learn.shadow import ShadowTrade
    from .cfgrid import _replay_ext
    hours = f.get("funding_hours_utc") if isinstance(f.get("funding_hours_utc"), list) else None
    model = exec_model(exec_par, hours=tuple(hours) if hours else None)
    view = ShadowTrade(id=str(rec.get("id")), plan_id=str(rec.get("id")), symbol=str(rec.get("symbol")), market_type="USDM_PERP",
                       direction=str(rec.get("side") or "LONG").upper(), created_at=iso(o), entry=float(ref), stop=float(stop),
                       targets=[float(t) for t in (targets or [])], horizon_bars=int(len(tb)), variant="as_planned",
                       reason_not_opened=[], label_ts=iso(c), tf_minutes=1 if tf == "1m" else 5)
    df = tb[["timestamp", "open", "high", "low", "close"]].reset_index(drop=True)
    r_actual = dec(rec.get("net_pnl")) / risk
    out["r_actual"] = format(r_actual, "f")
    hook, rec_fill = recorded_stop_fill(rec, tf=tf, rule_tf_ms=rule_tf_ms)
    out["recorded_fill"] = rec_fill
    try:
        res = _replay_ext(view, df, {"bars": int(len(df)), "exit_reason": exit_reason}, model=model,
                          funding_lookup=RecordedFunding(rec), stop_fill=hook)
    except Exception as exc:  # noqa: BLE001 — yeniden oynatma arızası: başarısız sayılır (neden yazılır)
        out.update(status=ST_FAIL, reasons=["REPLAY_ERROR"], primary_reason="REPLAY_ERROR", error=f"{type(exc).__name__}: {exc}"[:200])
        return out
    if res.get("r_net") is None:
        out.update(status=ST_FAIL, reasons=["REPLAY_ERROR"], primary_reason="REPLAY_ERROR", net_status=res.get("net_status"))
        return out
    ef_rep = Decimal(repr(float(res.get("entry_fill"))))
    den = abs(fill - stop)
    r_rep = Decimal(repr(float(res["r_net"]))) * (abs(ef_rep - stop) / den) if den > 0 else None
    if r_rep is None:
        out.update(status=ST_FAIL, reasons=["REPLAY_ERROR"], primary_reason="REPLAY_ERROR")
        return out
    delta = r_rep - r_actual
    pb = str(res.get("net_exit_basis") or "")
    out.update(r_replay=format(r_rep.quantize(Decimal("1e-6")) + 0, "f"), delta_r=format(delta.quantize(Decimal("1e-6")) + 0, "f"),
               fill_model={RECORDED_FILL: RECORDED_FILL, "GAP_FILL_AT_BAR_OPEN": "BAR_OPEN"}.get(pb, "LEVEL"),
               replay_exit_reason=res.get("net_exit_reason"), replay_exit_basis=res.get("net_exit_basis"),
               replay_entry_fill=format(ef_rep, "f"), intrabar_ambiguous_bars=int(res.get("intrabar_ambiguous_bars") or 0),
               bars=int(len(df)), tf=tf, exec_model=res.get("exec_model"))
    if abs(delta) <= TOLERANCE_R:
        out.update(status=ST_OK, ok=True, reasons=[], primary_reason=None)
        return out
    reasons: list[str] = []
    if path_gaps > 0:
        reasons.append("PATH_GAP")
    if int(res.get("intrabar_ambiguous_bars") or 0) > 0:
        reasons.append("AMBIGUOUS_BAR")
    rb = _exit_basis(rec)
    if pb != RECORDED_FILL and (rb in _GAP_BASES) != (pb in _GAP_BASES) and (rb is not None or pb in _GAP_BASES):
        reasons.append("FILL_BASIS")
    if str(res.get("net_exit_reason") or "") != exit_reason and not (
            exit_reason not in ("stop", "başa-baş stop", "hedef1", "hedef2", "likidasyon")
            and str(res.get("net_exit_reason")) == "horizon"):
        reasons.append("EXIT_REASON_DIFFERS")
    if backdated:
        reasons.append("BACKDATED_CLOSE")
    t = tick if tick is not None else Decimal("1e-8")
    if abs(ef_rep - fill) > t:
        reasons.append("ENTRY_FILL")
    if not reasons:
        reasons.append("UNEXPLAINED")
    reasons = [r for r in REASON_ORDER if r in reasons]
    out.update(status=ST_FAIL, ok=False, reasons=reasons, primary_reason=reasons[0])
    return out


# ============================================================================ kapı
def summarize(rows: Iterable[dict[str, Any]], *, target: float = TARGET_RATE) -> dict[str, Any]:
    """Fidelity kapısı (§5.3): uygun işlemlerde doğruluk oranı ≥ %90; başarısızlar nedenleriyle, uygun olmayanlar ayrı."""
    n = ok = 0
    failures: list[dict[str, Any]] = []
    by_reason: dict[str, int] = {}
    not_elig: dict[str, int] = {}
    by_book: dict[str, dict[str, int]] = {}
    for r in rows:
        fd = r.get("fidelity") or {}
        st = fd.get("status")
        book = str(r.get("book"))
        if st == ST_NOT_ELIGIBLE or st is None:
            why = str(fd.get("primary_reason") or "UNKNOWN")
            not_elig[why] = not_elig.get(why, 0) + 1
            continue
        n += 1
        b = by_book.setdefault(book, {"n": 0, "ok": 0})
        b["n"] += 1
        if st == ST_OK:
            ok += 1
            b["ok"] += 1
            continue
        for why in fd.get("reasons") or ["UNEXPLAINED"]:
            by_reason[why] = by_reason.get(why, 0) + 1
        failures.append({"trade_key": r.get("trade_key"), "book": book, "delta_r": fd.get("delta_r"),
                         "reasons": list(fd.get("reasons") or []), "primary_reason": fd.get("primary_reason")})
    rate = ok / n if n else None
    failures.sort(key=lambda x: (str(x["primary_reason"]), str(x["trade_key"])))
    return {"version": FIDELITY_VERSION, "eligible": n, "ok": ok, "rate": round(rate, 4) if rate is not None else None,
            "target": target, "gate_pass": bool(rate is not None and rate >= target), "tolerance_r": format(TOLERANCE_R, "f"),
            "failures": failures, "failures_by_reason": dict(sorted(by_reason.items())),
            "not_eligible": dict(sorted(not_elig.items())),
            "by_book": {b: {**v, "rate": round(v["ok"] / v["n"], 4) if v["n"] else None} for b, v in sorted(by_book.items())}}


__all__ = ["EXEC_KEYS", "FIDELITY_VERSION", "NOT_ELIGIBLE", "REASON_ORDER", "RECORDED_FILL", "RecordedFunding", "ST_FAIL",
           "ST_NOT_ELIGIBLE", "ST_OK", "TARGET_RATE", "TOLERANCE_R", "exec_model", "exec_params", "recorded_stop_fill", "replay",
           "summarize"]
