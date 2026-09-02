"""Çıkış politikaları (`exit_policy_v1`) — champion ve challenger'lar, versiyonlu ve saf.

Bu modül KARAR ÜRETİR, karar UYGULAMAZ. Hiçbir fonksiyonu defteri, emir yolunu ya da pozisyonu
değiştirmez; hepsi bir yol snapshot'ını alıp bir niyet sözlüğü döndürür.

Politikalar:

* `CHAMPION` — bugünkü davranış: statik stop, mevcut TP dizisi, TP1 sonrası gerçek başabaş
  stop. Motor bunu ZATEN `FuturesLedgerV2.tick()` içinde uyguluyor; burada yalnız temsil edilir
  ki challenger'la AYNI yol üzerinde karşılaştırılabilsin.
* `CHALLENGER_A` (R tabanlı kâr kilidi) — belirli MFE_R eşiklerinden sonra stop'u sıkıştırır.
* `CHALLENGER_B` (geri verme azaltması) — MFE'den belirli R geri verilince kalanın bir kısmını
  azaltmayı önerir.
* `CHALLENGER_C` (zaman/taşıma çıkışı) — yaş + kalan avantaj + funding sürüklemesi. Ekonomi
  `UNKNOWN` ise **çıkış üretmez**.

Eşikler burada dağınık sabit DEĞİLDİR: `ExitPolicyConfig` versiyonlu bir yapıdır, `config.yaml`
üzerinden gelir ve `policy_version` her karara yazılır. Eşik ayarı yalnız geçmiş train fold'undan
yapılabilir; bu modül kendi başına hiçbir eşik ÖĞRENMEZ.

Güvenlik değişmezleri (kod düzeyinde zorunlu, testle kilitli):
* Stop yalnız SIKILAŞIR. Gevşetme önerisi üretilemez.
* Stop markın yanlış tarafına konamaz (anında tetiklenecek stop önerilmez).
* Kaldıraç, pozisyon büyüklüğü artışı ve yeni giriş bu modülün kapsamı DIŞINDADIR.
* Aynı snapshot iki kez aynı aksiyonu üretmez (deterministik idempotency anahtarı).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from typing import Any

from ..core import stable_id

SCHEMA_VERSION = "exit_policy_v1"

CHAMPION = "champion"
CHALLENGER_A = "challenger_a_profit_lock"
CHALLENGER_B = "challenger_b_giveback_reduce"
CHALLENGER_C = "challenger_c_time_carry"
POLICIES = (CHAMPION, CHALLENGER_A, CHALLENGER_B, CHALLENGER_C)

#: Niyet türleri. `HOLD` bir eylemsizlik bildirimidir, eylem değildir.
HOLD, TIGHTEN_STOP, REDUCE, EXIT = "HOLD", "TIGHTEN_STOP", "REDUCE", "EXIT"

#: Gerekçe kodları — panel ve öğrenme AYNI kodları okur.
R_NO_PATH = "NO_PATH_DATA"
R_NO_RISK = "RISK_NOT_MEASURABLE"
R_BELOW_TRIGGER = "BELOW_TRIGGER"
R_LOCK_ARMED = "PROFIT_LOCK_ARMED"
R_LOCK_ALREADY = "STOP_ALREADY_TIGHTER"
R_WRONG_SIDE = "STOP_WOULD_BE_WRONG_SIDE_OF_MARK"
R_GIVEBACK = "GIVEBACK_EXCEEDED"
R_MIN_REMAINING = "MIN_REMAINING_BLOCKED"
R_ALREADY_REDUCED = "ALREADY_REDUCED"
R_UNKNOWN_ECON = "ECONOMICS_UNKNOWN"
R_AGE = "AGE_AND_CARRY"
R_COOLDOWN = "COOLDOWN"
R_CHAMPION_STATIC = "CHAMPION_STATIC_STOP_TP"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@dataclass
class ProfitLockStep:
    """`mfe_r` eşiği aşılınca stop'u `lock_r` seviyesine (R cinsinden) çeker."""
    mfe_r: float
    lock_r: float

    def validate(self) -> None:
        if not math.isfinite(self.mfe_r) or not math.isfinite(self.lock_r):
            raise ValueError("profit lock adımı sonlu olmalı")
        if self.lock_r >= self.mfe_r:
            raise ValueError(f"lock_r ({self.lock_r}) < mfe_r ({self.mfe_r}) olmalı — "
                             "kilit, ulaşılan en iyi noktanın ALTINDA kalmalıdır")


@dataclass
class ExitPolicyConfig:
    """Versiyonlu çıkış politikası ayarları. Eşikler koda GÖMÜLMEZ."""
    policy_version: str = "exit_v1.0.0"
    enabled: bool = True

    # --- Challenger A: R tabanlı kâr kilidi -------------------------------------------
    # Merdiven: MFE 1.0R'ye ulaşınca stop başabaşa, 1.5R'de +0.5R, 2.5R'de +1.5R.
    # Kilit DAİMA ulaşılan MFE'nin altındadır; aksi hâlde stop anında tetiklenirdi.
    profit_lock_steps: list[ProfitLockStep] = field(default_factory=lambda: [
        ProfitLockStep(mfe_r=1.0, lock_r=0.0),
        ProfitLockStep(mfe_r=1.5, lock_r=0.5),
        ProfitLockStep(mfe_r=2.5, lock_r=1.5),
    ])
    #: Stop ile mark arasında bırakılacak asgari tampon (giriş riskinin oranı).
    min_stop_buffer_r: float = 0.10

    # --- Challenger B: geri verme azaltması -------------------------------------------
    #: Azaltma düşünülmeden önce ulaşılması gereken asgari lehte hareket.
    giveback_min_mfe_r: float = 1.5
    #: MFE'den bu kadar R geri verilirse azaltma önerilir.
    giveback_trigger_r: float = 0.75
    #: Kalanın azaltılacak oranı.
    giveback_reduce_fraction: float = 0.5
    #: Azaltma sonrası kalması gereken asgari oran ve asgari notional (USDT).
    min_remaining_fraction: float = 0.25
    min_remaining_notional_usdt: float = 5.0
    #: Pozisyon başına azami azaltma sayısı.
    max_reduces_per_position: int = 1

    # --- Challenger C: zaman/taşıma çıkışı --------------------------------------------
    #: Bu yaşın altındaki pozisyon için zaman gerekçesiyle çıkış ÖNERİLMEZ.
    max_position_age_hours: float = 168.0
    #: Kalan avantaj bu eşiğin altındaysa (ve ekonomi ÖLÇÜLMÜŞSE) çıkış önerilir.
    min_remaining_edge_r: float = 0.0
    #: Funding sürüklemesi bu eşiği aşarsa taşıma maliyeti gerekçe sayılır.
    max_funding_drag_r: float = 0.25

    # --- Ortak -------------------------------------------------------------------------
    #: Aynı pozisyonda iki aksiyon arasındaki asgari süre.
    action_cooldown_s: float = 900.0
    #: Tur başına pozisyon başına azami aksiyon (sert sınır, config ile artırılamaz).
    max_actions_per_position_per_tour: int = 1

    def validate(self) -> None:
        for s in self.profit_lock_steps:
            s.validate()
        rs = [s.mfe_r for s in self.profit_lock_steps]
        if rs != sorted(rs):
            raise ValueError("profit_lock_steps mfe_r'ye göre ARTAN sırada olmalı")
        if not (0.0 < self.giveback_reduce_fraction < 1.0):
            raise ValueError("giveback_reduce_fraction (0,1) aralığında olmalı")
        if not (0.0 <= self.min_remaining_fraction < 1.0):
            raise ValueError("min_remaining_fraction [0,1) aralığında olmalı")
        if self.giveback_trigger_r <= 0:
            raise ValueError("giveback_trigger_r pozitif olmalı")
        if self.min_stop_buffer_r < 0:
            raise ValueError("min_stop_buffer_r negatif olamaz")
        if self.max_actions_per_position_per_tour != 1:
            raise ValueError("max_actions_per_position_per_tour yalnız 1 olabilir "
                             "(tur başına tek aksiyon SERT sınırdır)")
        if self.max_reduces_per_position < 1:
            raise ValueError("max_reduces_per_position >= 1 olmalı")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name == "profit_lock_steps":
                v = [{"mfe_r": s.mfe_r, "lock_r": s.lock_r} for s in v]
            out[f.name] = v
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ExitPolicyConfig":
        d = dict(d or {})
        steps = d.pop("profit_lock_steps", None)
        allowed = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in d.items() if k in allowed})
        if steps is not None:
            cfg.profit_lock_steps = [
                ProfitLockStep(mfe_r=float(s["mfe_r"]), lock_r=float(s["lock_r"]))
                for s in steps]
        cfg.validate()
        return cfg

    @property
    def config_id(self) -> str:
        """Eşik kümesinin deterministik kimliği — hangi ayarla karar verildiği kayda geçer."""
        return stable_id("exitcfg", self.policy_version, self.to_dict())


def _intent(policy: str, action: str, *, snap: dict[str, Any], cfg: ExitPolicyConfig,
            reasons: list[str], **extra: Any) -> dict[str, Any]:
    """Ortak niyet zarfı. `applied` DAİMA False'tur: bu modül hiçbir şey uygulamaz."""
    tid = str(snap.get("trade_id") or "")
    sid = str(snap.get("snapshot_id") or "")
    rec = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": policy,
        "policy_version": cfg.policy_version,
        "config_id": cfg.config_id,
        # DETERMİNİSTİK IDEMPOTENCY ANAHTARI: aynı snapshot + aynı politika + aynı aksiyon
        # ikinci kez uygulanamaz. Restart duplicate üretmez.
        "idempotency_key": stable_id("exitact", policy, tid, sid, action),
        "trade_id": tid,
        "snapshot_id": sid,
        "symbol": snap.get("symbol"),
        "side": snap.get("side"),
        "ts": snap.get("ts"),
        "action": action,
        "reason_codes": list(reasons),
        "action_price": snap.get("mark"),
        "stop_before": snap.get("current_stop"),
        "stop_after": None,
        "qty_before": snap.get("qty"),
        "qty_after": snap.get("qty"),
        "applied": False,
        "blocker": None,
    }
    rec.update(extra)
    return rec


def _stop_price_for_r(target_r: float, snap: dict[str, Any]) -> float | None:
    """`target_r` seviyesine karşılık gelen stop fiyatı. LONG/SHORT simetrik."""
    from .position_path import side_sign
    entry, stop0 = _f(snap.get("entry")), _f(snap.get("initial_stop"))
    if entry is None or stop0 is None:
        return None
    dist = abs(entry - stop0)
    if dist <= 0:
        return None
    return entry + side_sign(snap.get("side")) * target_r * dist


def _is_tighter(new_stop: float, cur_stop: float | None, side: Any) -> bool:
    """Yeni stop mevcut stoptan daha SIKI mı. `None` mevcut stop her zaman gevşektir."""
    from .position_path import side_sign
    if cur_stop is None:
        return True
    return (new_stop > cur_stop) if side_sign(side) > 0 else (new_stop < cur_stop)


def _wrong_side_of_mark(new_stop: float, mark: float, side: Any, *, buffer_abs: float) -> bool:
    """Stop markın yanlış tarafında mı (anında tetiklenir mi). Tampon dahil kontrol edilir."""
    from .position_path import side_sign
    if side_sign(side) > 0:
        return new_stop >= (mark - buffer_abs)
    return new_stop <= (mark + buffer_abs)


def champion_decision(snap: dict[str, Any], cfg: ExitPolicyConfig) -> dict[str, Any]:
    """Bugünkü davranışın temsili: yönetim aksiyonu YOK.

    Champion stop/TP'yi defterin tick'inde uygular; snapshot düzeyinde ek bir eylem üretmez.
    Bu yüzden karşılaştırmada champion'ın aksiyonu daima `HOLD`tur ve sonucu gerçek defter
    kapanışıdır.
    """
    return _intent(CHAMPION, HOLD, snap=snap, cfg=cfg, reasons=[R_CHAMPION_STATIC])


def challenger_a(snap: dict[str, Any], cfg: ExitPolicyConfig) -> dict[str, Any]:
    """R tabanlı kâr kilidi: MFE eşiği aşılınca stop sıkıştırılır. ASLA gevşetilmez."""
    mfe_r = _f(snap.get("mfe_r"))
    mark = _f(snap.get("mark"))
    cur = _f(snap.get("current_stop"))
    entry, stop0 = _f(snap.get("entry")), _f(snap.get("initial_stop"))
    if mfe_r is None or mark is None or entry is None or stop0 is None:
        return _intent(CHALLENGER_A, HOLD, snap=snap, cfg=cfg, reasons=[R_NO_RISK])
    dist = abs(entry - stop0)
    if dist <= 0:
        return _intent(CHALLENGER_A, HOLD, snap=snap, cfg=cfg, reasons=[R_NO_RISK])
    step = None
    for s in cfg.profit_lock_steps:                 # artan sıralı; en yükseği kazanır
        if mfe_r >= s.mfe_r:
            step = s
    if step is None:
        return _intent(CHALLENGER_A, HOLD, snap=snap, cfg=cfg, reasons=[R_BELOW_TRIGGER],
                       mfe_r=mfe_r)
    new_stop = _stop_price_for_r(step.lock_r, snap)
    if new_stop is None:
        return _intent(CHALLENGER_A, HOLD, snap=snap, cfg=cfg, reasons=[R_NO_RISK])
    if not _is_tighter(new_stop, cur, snap.get("side")):
        return _intent(CHALLENGER_A, HOLD, snap=snap, cfg=cfg, reasons=[R_LOCK_ALREADY],
                       proposed_stop=round(new_stop, 10), mfe_r=mfe_r)
    if _wrong_side_of_mark(new_stop, mark, snap.get("side"),
                           buffer_abs=cfg.min_stop_buffer_r * dist):
        # Markın yanlış tarafına stop koymak, "kâr kilidi" adı altında ANINDA çıkış demektir.
        return _intent(CHALLENGER_A, HOLD, snap=snap, cfg=cfg, reasons=[R_WRONG_SIDE],
                       proposed_stop=round(new_stop, 10), mfe_r=mfe_r)
    return _intent(CHALLENGER_A, TIGHTEN_STOP, snap=snap, cfg=cfg,
                   reasons=[R_LOCK_ARMED], stop_after=round(new_stop, 10),
                   locked_r=step.lock_r, trigger_mfe_r=step.mfe_r, mfe_r=mfe_r)


def challenger_b(snap: dict[str, Any], cfg: ExitPolicyConfig, *,
                 reduces_done: int = 0) -> dict[str, Any]:
    """Geri verme azaltması: anlamlı lehte hareketten sonra belirli R geri verilirse azalt."""
    mfe_r, cur_r = _f(snap.get("mfe_r")), _f(snap.get("gross_r"))
    give = _f(snap.get("giveback_r"))
    qty = _f(snap.get("qty"))
    mark = _f(snap.get("mark"))
    if mfe_r is None or cur_r is None or give is None or qty is None or mark is None:
        return _intent(CHALLENGER_B, HOLD, snap=snap, cfg=cfg, reasons=[R_NO_RISK])
    if reduces_done >= cfg.max_reduces_per_position:
        return _intent(CHALLENGER_B, HOLD, snap=snap, cfg=cfg, reasons=[R_ALREADY_REDUCED],
                       reduces_done=reduces_done)
    if mfe_r < cfg.giveback_min_mfe_r or give < cfg.giveback_trigger_r:
        return _intent(CHALLENGER_B, HOLD, snap=snap, cfg=cfg, reasons=[R_BELOW_TRIGGER],
                       mfe_r=mfe_r, giveback_r=give)
    frac = cfg.giveback_reduce_fraction
    qty_after = qty * (1.0 - frac)
    init_qty = _f(snap.get("initial_qty"))
    remaining_fraction = (qty_after / init_qty) if init_qty else None
    notional_after = qty_after * mark
    if (remaining_fraction is not None and remaining_fraction < cfg.min_remaining_fraction) \
            or notional_after < cfg.min_remaining_notional_usdt:
        # Kalan pozisyon borsa/defter asgarisinin altına düşerse azaltma YAPILMAZ: yarım
        # kapatılmış ama yönetilemeyen bir artık pozisyon üretmek çözüm değildir.
        return _intent(CHALLENGER_B, HOLD, snap=snap, cfg=cfg, reasons=[R_MIN_REMAINING],
                       qty_after=qty_after, notional_after=round(notional_after, 8),
                       remaining_fraction=remaining_fraction)
    return _intent(CHALLENGER_B, REDUCE, snap=snap, cfg=cfg, reasons=[R_GIVEBACK],
                   reduce_fraction=frac, qty_after=round(qty_after, 12),
                   notional_after=round(notional_after, 8),
                   remaining_fraction=remaining_fraction,
                   mfe_r=mfe_r, giveback_r=give)


def challenger_c(snap: dict[str, Any], cfg: ExitPolicyConfig) -> dict[str, Any]:
    """Zaman/taşıma çıkışı. Ekonomi ölçülmemişse ÇIKIŞ ÜRETMEZ (sahte gerekçe yasak)."""
    if not snap.get("economics_evaluated"):
        # Kalan avantaj bilinmiyorken "avantaj bitti" demek uydurmadır.
        return _intent(CHALLENGER_C, HOLD, snap=snap, cfg=cfg, reasons=[R_UNKNOWN_ECON])
    age = _f(snap.get("position_age_hours"))
    edge = _f(snap.get("remaining_edge_r"))
    fdrag = _f(snap.get("funding_drag_r"))
    if age is None or edge is None:
        return _intent(CHALLENGER_C, HOLD, snap=snap, cfg=cfg, reasons=[R_UNKNOWN_ECON])
    reasons: list[str] = []
    if age >= cfg.max_position_age_hours:
        reasons.append(R_AGE)
    if edge <= cfg.min_remaining_edge_r:
        reasons.append("REMAINING_EDGE_EXHAUSTED")
    if fdrag is not None and fdrag >= cfg.max_funding_drag_r:
        reasons.append("CARRY_COST_HIGH")
    if not reasons:
        return _intent(CHALLENGER_C, HOLD, snap=snap, cfg=cfg, reasons=[R_BELOW_TRIGGER],
                       position_age_hours=age, remaining_edge_r=edge)
    return _intent(CHALLENGER_C, EXIT, snap=snap, cfg=cfg, reasons=reasons,
                   position_age_hours=age, remaining_edge_r=edge, funding_drag_r=fdrag,
                   qty_after=0.0)


def evaluate_all(snap: dict[str, Any], cfg: ExitPolicyConfig, *,
                 reduces_done: int = 0) -> dict[str, dict[str, Any]]:
    """Bütün politikaların kararı — champion ve challenger'lar AYRI kaydedilir."""
    return {
        CHAMPION: champion_decision(snap, cfg),
        CHALLENGER_A: challenger_a(snap, cfg),
        CHALLENGER_B: challenger_b(snap, cfg, reduces_done=reduces_done),
        CHALLENGER_C: challenger_c(snap, cfg),
    }


__all__ = ["SCHEMA_VERSION", "CHAMPION", "CHALLENGER_A", "CHALLENGER_B", "CHALLENGER_C",
           "POLICIES", "HOLD", "TIGHTEN_STOP", "REDUCE", "EXIT", "ProfitLockStep",
           "ExitPolicyConfig", "champion_decision", "challenger_a", "challenger_b",
           "challenger_c", "evaluate_all",
           "R_NO_PATH", "R_NO_RISK", "R_BELOW_TRIGGER", "R_LOCK_ARMED", "R_LOCK_ALREADY",
           "R_WRONG_SIDE", "R_GIVEBACK", "R_MIN_REMAINING", "R_ALREADY_REDUCED",
           "R_UNKNOWN_ECON", "R_AGE", "R_COOLDOWN", "R_CHAMPION_STATIC"]
