"""Kayıp büyüklüğü onarımının DONMUŞ üretim adayları üzerindeki ÖLÇÜLEN etkisi.

Ne ölçülür
----------
`entry_snapshot.jsonl` içindeki her `LINKED` giriş adayı için `conservative_net_edge_r` iki kez
hesaplanır. Aradaki TEK fark kayıp büyüklüğüdür:

* **OLD** — sabit ``|avg_loss_r| = 1.0`` (bugünkü üretim davranışı).
* **NEW** — learner'ın KENDİ kapanışlarından belirsizlik daraltmalı kestirim
  (`LearnerV2.loss_magnitude_r`, donmuş `learn_v2.json` üzerinden).

Yumuşak ceza, belirsizlik cezası, p_win, plan geometrisi, örneklem: HEPSİ sabit tutulur. Maliyet
ÇİFT SAYILMAZ — tüm kayıtlar `NET_OUTCOME` tabanlıdır, `cost_r = 0`.

Üç ölçüm kolu
-------------
``exact_prefix``
    ÜRETİME BİREBİR BAĞLI ve CEBİRSEL OLARAK TAM. Donmuş kayıtlar 1c4cba1'den ÖNCE yazıldığı için
    o kod yolunda ekonomi kapısı hiyerarşik p_win'i HEM kazanç ters çözümünde HEM brüt formülde
    kullanır; dolayısıyla `p`, `w`, `realised_win_r` ve taban kazanç kaydın KENDİSİNDEN tam olarak
    geri çözülebilir (yeniden kurulan brüt, kaydedilenle ~1e-16 farkla aynıdır; `reconstruction`
    alanı bunu kanıtlar). Learner durumundan YALNIZ ΔL alınır. Başlık sayısı budur.

``exact_head``
    Aynı cebirsel geri çözüm, ama BUGÜNKÜ kod yolu: `engine_v3._assess_opportunities`
    `stats["p_win"]`i kalibre `d.p_win` ile EZER; kazanç ters çözümü ise hâlâ hiyerarşik p ile
    yapılır. Bu kolun seviyeleri üretimde kaydedilenlerden farklıdır (1c4cba1'in etkisi), fakat
    ΔL etkisi bu yolda ölçülür.

``replay``
    Donmuş learner durumuyla `hierarchical_expectancy` + `assess` zincirinin bütün olarak yeniden
    koşulması. NOT: durum, kayıtların yazıldığı andan SONRA da güncellendiği için bu kolun MUTLAK
    seviyesi üretimi yeniden üretmez (`replay_reproduction_error` alanı sapmayı açıkça verir);
    yalnız kontrollü A/B farkı için anlamlıdır.

Kullanım
--------
    python scripts/measure_loss_magnitude_v1.py --snapshot <donmuş_dizin> [--json <çıktı.json>]

Donmuş dizine HİÇBİR ŞEY YAZILMAZ: durum dosyası geçici bir dizine kopyalanır.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import statistics as st
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.learn.learner_v2 import LearnConfig, LearnerV2  # noqa: E402
from tradingbot.learn.memory import TradeMemory  # noqa: E402
from tradingbot.learn.registry import ModelRegistry  # noqa: E402
from tradingbot.opportunity import (BLEND_N, FULL_SIZE_EDGE_R, MIN_TRADE_MULTIPLIER,  # noqa: E402
                                    RESEARCH_MULTIPLIER, hierarchical_expectancy,
                                    uncertainty_penalty_r)

#: `hierarchical_expectancy` içindeki taban kazanç varsayılanı (fallback_win_r yoksa).
DEFAULT_WIN_R = 1.6
#: `realised_win_r = max(0.1, ...)` tabanı — geri çözümde de aynı taban kullanılır.
WIN_FLOOR_R = 0.1


class _NoLossLearner:
    """OLD kolu için vekil: `loss_magnitude_r` HİÇ yokmuş gibi davranır.

    Yeni bir sabit yapıştırmaz — `hierarchical_expectancy` içindeki GERÇEK geri düşüş yolunu
    (öznitelik yok → `default_loss_r`) tetikler, yani OLD kolu bugünkü üretim davranışıdır.
    """

    def __init__(self, inner) -> None:
        self.win = inner.win
        self.exp_r = inner.exp_r


def _size_multiplier(conservative: float, net: float) -> float:
    """`opportunity.assess` ile AYNI boyut merdiveni (donmuş kümede engellenen aday YOKTUR)."""
    if conservative > 0:
        score = max(0.0, min(1.0, conservative / FULL_SIZE_EDGE_R))
        return round(max(MIN_TRADE_MULTIPLIER, min(1.0, MIN_TRADE_MULTIPLIER + (1.0 - MIN_TRADE_MULTIPLIER) * score)), 6)
    if net > 0:
        return RESEARCH_MULTIPLIER
    return 0.0


def _economics(*, p: float, win_r: float, loss_r: float, unc: float, soft: float) -> dict:
    """`assess`in NET_OUTCOME kolu: cost_r = 0 (maliyet gerçekleşmiş R'lerin İÇİNDE, tekrar düşülmez)."""
    p = max(0.0, min(1.0, float(p)))
    gross = p * abs(win_r) - (1.0 - p) * abs(loss_r)
    net = gross
    cons = net - unc - soft
    mult = _size_multiplier(cons, net)
    return {"gross": gross, "net": net, "cons": cons, "mult": mult, "tradeable": bool(cons > 0 and mult > 0)}


def _pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def _describe(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(st.mean(xs), 6), "median": round(st.median(xs), 6),
            "sd": round(st.pstdev(xs), 6), "min": round(min(xs), 6), "max": round(max(xs), 6),
            "p05": round(_pct(xs, 0.05), 6), "p95": round(_pct(xs, 0.95), 6)}


class _Arm:
    """Tek kol için biriktirici: Δ dağılımı, seviye dağılımları ve `tradeable` geçişleri."""

    def __init__(self) -> None:
        self.delta: list[float] = []
        self.old: list[float] = []
        self.new: list[float] = []
        self.t_old = self.t_new = self.to_false = self.to_true = 0

    def add(self, a: dict, b: dict) -> None:
        self.delta.append(b["cons"] - a["cons"])
        self.old.append(a["cons"])
        self.new.append(b["cons"])
        self.t_old += int(a["tradeable"])
        self.t_new += int(b["tradeable"])
        if a["tradeable"] and not b["tradeable"]:
            self.to_false += 1
        elif b["tradeable"] and not a["tradeable"]:
            self.to_true += 1

    def to_dict(self) -> dict:
        return {"delta_conservative_net_edge_r": _describe(self.delta),
                # Δ'nın TAMAMI negatif olduğundan yüzdelikler kafa karıştırabilir: BÜYÜKLÜK
                # yüzdelikleri ayrıca verilir (p95 = adayların %95'inin altında kaldığı kayıp).
                "abs_delta_conservative_net_edge_r": _describe([abs(x) for x in self.delta]),
                "level_old": _describe(self.old), "level_new": _describe(self.new),
                "tradeable_old": self.t_old, "tradeable_new": self.t_new,
                "flip_to_false": self.to_false, "flip_to_true": self.to_true}


def load_frozen_learner(snapshot_dir: Path) -> LearnerV2:
    """Donmuş `learn_v2.json`u geçici dizine KOPYALAYIP yükler (donmuş dizine yazılmaz)."""
    tmp = Path(tempfile.mkdtemp(prefix="loss_mag_"))
    shutil.copy(snapshot_dir / "learn_v2.json", tmp / "learn_v2.json")
    return LearnerV2(TradeMemory(tmp), ModelRegistry(tmp / "models.json"), LearnConfig(), tmp / "learn_v2.json")


def iter_candidates(path: Path):
    """93 MB'lık dosya SATIR SATIR akıtılır; belleğe tamamı alınmaz."""
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("link_status") == "LINKED":
                yield rec


def reconstruct(rec: dict) -> dict | None:
    """Kaydın karar-anı ekonomi girdilerini CEBİRSEL olarak geri çöz.

    Kaydedilen kod yolunda (1c4cba1 ÖNCESİ) brüt formül ile kazanç ters çözümü AYNI `p`yi
    kullandığı için::

        p            = (gross + L) / (W + L)                 # L = 1.0 kayıtlarda sabit
        w            = n / (n + BLEND_N)
        base         = max(0.1, plan.expected_r)
        realised_old = (W − (1 − w)·base) / w                # w > 0 iken
        soft         = net − unc − conservative

    `realised_old` 0.1 tabanına oturduysa gerçek (kırpılmamış) değer BİLİNEMEZ; bu kayıtlar
    `win_floor_clamped` ile işaretlenir ve Δ onlarda ÜST SINIR (en az negatif) olur.
    """
    need = ("avg_win_r", "avg_loss_r", "gross_expectancy_r", "net_expectancy_r",
            "conservative_net_edge_r", "uncertainty_penalty_r", "sample_size")
    if any(rec.get(k) is None for k in need):
        return None
    win_r = float(rec["avg_win_r"])
    loss_r = abs(float(rec["avg_loss_r"]))
    gross = float(rec["gross_expectancy_r"])
    n = float(rec["sample_size"])
    p = (gross + loss_r) / (win_r + loss_r)
    w = n / (n + BLEND_N)
    exp_r_plan = rec.get("expected_r")
    base = max(WIN_FLOOR_R, float(exp_r_plan) if exp_r_plan else DEFAULT_WIN_R)
    realised = (win_r - (1.0 - w) * base) / w if w > 0 else None
    soft = float(rec["net_expectancy_r"]) - float(rec["uncertainty_penalty_r"]) - float(rec["conservative_net_edge_r"])
    # Learner'ın gerçekleşmiş beklentisi: ters çözümün TERSİ (yalnız kırpılmamışsa geçerli).
    exp_r = (p * realised - (1.0 - p) * loss_r) if realised is not None else None
    return {"p": p, "w": w, "base": base, "realised": realised, "exp_r": exp_r, "win_r": win_r,
            "loss_r": loss_r, "gross": gross, "soft": soft, "unc": float(rec["uncertainty_penalty_r"]),
            "cons": float(rec["conservative_net_edge_r"]), "n": n,
            "win_floor_clamped": bool(realised is not None and realised <= WIN_FLOOR_R + 1e-6)}


def win_r_under(rc: dict, loss_r: float, *, p: float | None = None) -> float:
    """Verilen kayıp büyüklüğünde `avg_win_r` — `hierarchical_expectancy` ile AYNI formül.

    `p` verilmezse kaydın hiyerarşik olasılığı kullanılır (üretim davranışı). Farklı bir `p`
    verilirse ters çözüm O olasılıkla yapılır; bu, p_win EZME tutarsızlığını ölçmek içindir.
    """
    if rc["w"] <= 0:                                     # örneklem yok → yalnız plan geometrisi
        return rc["base"]
    if p is None:
        realised = max(WIN_FLOOR_R, rc["realised"] + (1.0 - rc["p"]) / rc["p"] * (loss_r - rc["loss_r"]))
    else:
        realised = max(WIN_FLOOR_R, (rc["exp_r"] + (1.0 - p) * loss_r) / p)
    return rc["w"] * realised + (1.0 - rc["w"]) * rc["base"]


def run(snapshot_dir: Path) -> dict:
    learner = load_frozen_learner(snapshot_dir)
    loss_new, loss_meta = learner.loss_magnitude_r()
    old_learner = _NoLossLearner(learner)

    arms = {"exact_prefix": _Arm(), "exact_head": _Arm(), "replay": _Arm()}
    recon_err: list[float] = []
    repro_err: list[float] = []
    d_win_r: list[float] = []
    blend_w: list[float] = []
    softs: list[float] = []
    p_gap: list[float] = []
    incon_d: list[float] = []
    anchor_err: list[float] = []
    t_rec: list[bool] = []
    incon_t = {"mixed": 0, "consistent": 0, "differs": 0, "n": 0}
    clamped = skipped = total = 0

    for rec in iter_candidates(snapshot_dir / "entry_snapshot.jsonl"):
        rc = reconstruct(rec)
        if rc is None:
            skipped += 1
            continue
        total += 1
        clamped += int(rc["win_floor_clamped"])
        blend_w.append(rc["w"])
        softs.append(rc["soft"])

        w_old = win_r_under(rc, rc["loss_r"])
        w_new = win_r_under(rc, loss_new)
        recon_err.append(rc["p"] * w_old - (1.0 - rc["p"]) * rc["loss_r"] - rc["gross"])
        d_win_r.append(w_new - w_old)

        # --- kol 1: kayıtların yazıldığı kod yolu (p HER İKİ yerde de hiyerarşik) --------------
        a = _economics(p=rc["p"], win_r=w_old, loss_r=rc["loss_r"], unc=rc["unc"], soft=rc["soft"])
        b = _economics(p=rc["p"], win_r=w_new, loss_r=loss_new, unc=rc["unc"], soft=rc["soft"])
        arms["exact_prefix"].add(a, b)
        anchor_err.append(a["cons"] - rc["cons"])        # OLD kolu KAYDEDİLENİ yeniden üretmeli
        t_rec.append(bool(rc["cons"] > 0 and float(rec.get("size_multiplier") or 0.0) > 0))

        # --- kol 2: BUGÜNKÜ kod yolu — brüt formülde kalibre d.p_win, ters çözümde hiyerarşik p -
        p_dec = rec.get("p_win")
        p_head = max(0.05, min(0.95, float(p_dec))) if p_dec else rc["p"]
        p_gap.append(p_head - rc["p"])
        a2 = _economics(p=p_head, win_r=w_old, loss_r=rc["loss_r"], unc=rc["unc"], soft=rc["soft"])
        b2 = _economics(p=p_head, win_r=w_new, loss_r=loss_new, unc=rc["unc"], soft=rc["soft"])
        arms["exact_head"].add(a2, b2)

        # --- p_win EZME TUTARSIZLIĞI ----------------------------------------------------------
        # `_assess_opportunities` `stats["p_win"]`i kalibre `d.p_win` ile ezer ama `avg_win_r`
        # BAŞKA bir olasılıkla (hiyerarşik p) ters çözülmüş halde KALIR. Karşılaştırma: aynı
        # olasılığın HER İKİ yerde de kullanıldığı tutarlı hesap. (İki hiyerarşi farklı yaprak
        # kullanır: kazanç `SYM|setup`, exp_r `setup|YÖN` — yani p'ler yapısal olarak da ayrıdır.)
        if rc["exp_r"] is not None and not rc["win_floor_clamped"]:
            w_cons = win_r_under(rc, loss_new, p=p_head)
            mixed = _economics(p=p_head, win_r=w_new, loss_r=loss_new, unc=rc["unc"], soft=rc["soft"])
            cons_p = _economics(p=p_head, win_r=w_cons, loss_r=loss_new, unc=rc["unc"], soft=rc["soft"])
            incon_d.append(mixed["cons"] - cons_p["cons"])
            incon_t["n"] += 1
            incon_t["mixed"] += int(mixed["tradeable"])
            incon_t["consistent"] += int(cons_p["tradeable"])
            incon_t["differs"] += int(mixed["tradeable"] != cons_p["tradeable"])

        # --- kol 3: donmuş learner durumuyla bütün zincirin yeniden koşumu (çapraz kontrol) ----
        sym, side = str(rec.get("symbol") or ""), str(rec.get("direction") or "")
        setup = str(rec.get("setup") or "-")
        fb = rec.get("expected_r")
        s_old = hierarchical_expectancy(learner=old_learner, symbol=sym, side=side, setup=setup,
                                        regime=rec.get("regime") or None, fallback_win_r=fb)
        s_new = hierarchical_expectancy(learner=learner, symbol=sym, side=side, setup=setup,
                                        regime=rec.get("regime") or None, fallback_win_r=fb)
        if abs(float(s_old["avg_loss_r"]) - 1.0) > 1e-12:
            raise AssertionError("OLD kolu 1.0 kullanmalı — geri düşüş yolu bozuk")
        unc_rep = uncertainty_penalty_r(s_old["sample_size"])
        a3 = _economics(p=s_old["p_win"], win_r=s_old["avg_win_r"], loss_r=s_old["avg_loss_r"], unc=unc_rep, soft=rc["soft"])
        b3 = _economics(p=s_new["p_win"], win_r=s_new["avg_win_r"], loss_r=s_new["avg_loss_r"], unc=unc_rep, soft=rc["soft"])
        arms["replay"].add(a3, b3)
        repro_err.append(a3["cons"] - rc["cons"])

    return {
        "candidates": total, "skipped": skipped,
        "loss_magnitude": {"old": 1.0, "new": loss_new, "delta": round(loss_new - 1.0, 6), "meta": loss_meta},
        "arms": {k: v.to_dict() for k, v in arms.items()},
        "reconstruction": {"max_abs_gross_error": max((abs(e) for e in recon_err), default=0.0),
                           "win_floor_clamped": clamped,
                           "max_abs_conservative_error": max((abs(e) for e in anchor_err), default=0.0),
                           "tradeable_recorded": sum(t_rec),
                           "note": "kol 1'in OLD tarafı KAYDEDİLEN brüt beklenti ve muhafazakâr "
                                   "edge'i birebir yeniden üretir (bkz. hata alanları)"},
        "replay_reproduction_error": _describe(repro_err),
        "delta_avg_win_r": _describe(d_win_r),
        "geometry_blend_w": _describe(blend_w),
        "soft_penalty_r_recovered": _describe(softs),
        "p_win_overwrite_gap": _describe(p_gap),
        "p_win_inconsistency": {
            "note": "kalibre p_win brüt formülde, hiyerarşik p ise avg_win_r ters çözümünde — "
                    "aynı p iki yerde kullanılsaydı edge ne kadar farklı olurdu",
            "delta_conservative_net_edge_r": _describe(incon_d),
            "abs_delta": _describe([abs(x) for x in incon_d]),
            "tradeable_mixed": incon_t["mixed"], "tradeable_consistent": incon_t["consistent"],
            "tradeable_differs": incon_t["differs"], "measured_on": incon_t["n"],
            "excluded_win_floor_clamped": total - incon_t["n"],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True, help="donmuş üretim anlık görüntüsü dizini (salt okunur)")
    ap.add_argument("--json", default=None, help="sonucu bu dosyaya da yaz")
    ns = ap.parse_args()
    res = run(Path(ns.snapshot))
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    sys.stdout.reconfigure(encoding="utf-8")
    print(txt)
    if ns.json:
        Path(ns.json).write_text(txt, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
