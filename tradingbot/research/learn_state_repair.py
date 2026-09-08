"""`learn_v2.json` ata-düğüm sayaç onarımı — DRY-RUN dönüştürücü (`learn_state_repair_v1`).

Kusur: `on_trade_closed` bir nihai kapanış için `win.add`'i iki kez çağırıyordu; her çağrı
ORTAK ATALARI (`""` ve `regime:X`) da yazdığı için o düğümler iki kez sayıldı. Yaprak
düğümler (iki farklı granülerlik) DOĞRU sayıldı; `exp_r`, `agent_hit`, `lessons`,
`calibrator`, `n_closed` etkilenmedi.

Bu modül **yalnız yerel bir kopya** üzerinde çalışır ve varsayılan olarak DRY-RUN'dır.
Ters çevirme ancak aşağıdaki değişmezlerin HEPSİ kanıtlanırsa uygulanır; herhangi biri
düşerse sonuç `BLOCKED`tır ve hiçbir şey yazılmaz.

Ters çevirmenin geçerliliği şuna dayanır: ağırlıksız (half-life yok) modda her `add` tam
olarak `w=1` ekler; dolayısıyla eski durum, yeni durumun ata düğümlerinde tam iki katıdır.
`n`'yi tek başına bölmek YETERSİZDİR — `n`, `s` ve `ss` birlikte dönüştürülür.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_VERSION = "learn_state_repair_v1"

#: Dönüştürülen dosyaya konan işaret — ikinci kez dönüştürme reddedilsin.
MARKER_KEY = "ancestor_count_repair"

REPAIRABLE = "REPAIRABLE"
ALREADY_CONVERTED = "ALREADY_CONVERTED"
BLOCKED = "BLOCKED"

#: Bu sürümde beklenen state şeması.
EXPECTED_SCHEMA = 2


def sha256_of(doc: Any) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _is_ancestor(key: str) -> bool:
    """Ata düğüm: global ya da yalnız-rejim. Yaprak ve rejim|yaprak ATA DEĞİLDİR."""
    return key == "" or (key.startswith("regime:") and "|leaf:" not in key)


def _int_like(x: float, tol: float = 1e-9) -> bool:
    return abs(x - round(x)) <= tol


def analyse(doc: dict[str, Any], *, canonical_closes: int | None = None,
            canonical_wins: int | None = None) -> dict[str, Any]:
    """Ters çevirmenin geçerli olup olmadığını KANITLAR. Belirsizlikte `BLOCKED` döner."""
    checks: list[dict[str, Any]] = []

    def chk(name: str, ok: bool, detail: str) -> bool:
        checks.append({"check": name, "passed": bool(ok), "detail": detail})
        return bool(ok)

    if not isinstance(doc, dict):
        return {"state": BLOCKED, "checks": [{"check": "SHAPE", "passed": False,
                                              "detail": "belge sözlük değil"}]}
    win = doc.get("win") or {}
    exp_r = doc.get("exp_r") or {}
    wstats = win.get("stats") or {}
    ok = True

    ok &= chk("SCHEMA_VERSION", doc.get("schema_version") == EXPECTED_SCHEMA,
              f"schema_version={doc.get('schema_version')} (beklenen {EXPECTED_SCHEMA})")
    ok &= chk("NOT_ALREADY_CONVERTED", MARKER_KEY not in doc,
              f"{MARKER_KEY} işareti {'VAR' if MARKER_KEY in doc else 'yok'}")
    ok &= chk("NO_DECAY_WEIGHTS",
              win.get("half_life_days") is None and exp_r.get("half_life_days") is None,
              f"win.half_life_days={win.get('half_life_days')}, "
              f"exp_r.half_life_days={exp_r.get('half_life_days')} "
              "(ağırlıklı ekleme varsa tam iki kat ilişkisi kanıtlanamaz)")
    ok &= chk("WIN_STATS_PRESENT", bool(wstats), f"{len(wstats)} win düğümü")

    # Bernoulli + w=1 imzası: s == ss (x∈{0,1} ve w=1 iken zorunlu).
    bad_ss = [k for k, v in wstats.items()
              if abs(float(v.get("s", 0.0)) - float(v.get("ss", 0.0))) > 1e-9]
    ok &= chk("BERNOULLI_UNWEIGHTED", not bad_ss,
              f"s != ss olan düğüm: {len(bad_ss)} {bad_ss[:3]}")

    # on_trade_closed hiç `market:`/`cluster:` üretmez — varsa başka bir yazar vardır.
    foreign = [k for k in wstats
               if k.startswith("market:") or k.startswith("cluster:")]
    ok &= chk("NO_FOREIGN_NODE_KINDS", not foreign, f"beklenmeyen düğüm: {foreign[:3]}")

    # legacy_bridge imzası: sembolsüz yaprak ve LEGACY kodlu ders.
    legacy_leaves = [k for k in wstats if k.startswith("leaf:") and "/" not in k]
    legacy_lessons = [ls for ls in (doc.get("lessons") or [])
                      if "LEGACY" in (ls.get("codes") or [])]
    ok &= chk("NO_LEGACY_IMPORT", not legacy_leaves and not legacy_lessons,
              f"sembolsüz yaprak {len(legacy_leaves)}, LEGACY ders {len(legacy_lessons)} "
              "(legacy_bridge `win`e TEK KEZ yazar → tekdüze iki kat bozulur)")

    anc = {k: v for k, v in wstats.items() if _is_ancestor(k)}
    leaves = {k: v for k, v in wstats.items() if not _is_ancestor(k)}
    ok &= chk("ANCESTORS_PRESENT", bool(anc), f"{len(anc)} ata, {len(leaves)} yaprak")

    odd = [(k, v.get("n"), v.get("s")) for k, v in anc.items()
           if not (_int_like(float(v.get("n", 0))) and _int_like(float(v.get("s", 0)))
                   and int(round(float(v.get("n", 0)))) % 2 == 0
                   and int(round(float(v.get("s", 0)))) % 2 == 0)]
    ok &= chk("ANCESTOR_PARITY_EVEN", not odd,
              f"tek/kesirli ata düğüm: {len(odd)} {odd[:3]} "
              "(tekdüze iki kat için ZORUNLU koşul)")

    g = wstats.get("")
    reg = {k: v for k, v in anc.items() if k != ""}
    if g is not None:
        sn = sum(float(v.get("n", 0)) for v in reg.values())
        ss_ = sum(float(v.get("s", 0)) for v in reg.values())
        ok &= chk("GLOBAL_EQUALS_REGIME_SUM",
                  abs(sn - float(g.get("n", 0))) < 1e-9 and abs(ss_ - float(g.get("s", 0))) < 1e-9,
                  f"Σregime n={sn} vs global n={g.get('n')}; Σregime s={ss_} vs "
                  f"global s={g.get('s')} (eşit değilse rejimsiz kapanış var)")
        nc = doc.get("n_closed")
        if isinstance(nc, int) and nc > 0:
            ok &= chk("GLOBAL_IS_TWICE_N_CLOSED",
                      abs(float(g.get("n", 0)) - 2 * nc) < 1e-9,
                      f"global n={g.get('n')} vs 2*n_closed={2 * nc}")
            if abs(float(g.get("n", 0)) - nc) < 1e-9:
                checks.append({"check": "LOOKS_ALREADY_SINGLE_COUNTED", "passed": False,
                               "detail": "global n == n_closed → zaten tek sayılmış olabilir"})
                return {"state": ALREADY_CONVERTED, "checks": checks,
                        "input_sha256": sha256_of(doc)}
        if canonical_closes is not None:
            ok &= chk("MATCHES_CANONICAL_CLOSES",
                      abs(float(g.get("n", 0)) - 2 * canonical_closes) < 1e-9,
                      f"global n={g.get('n')} vs 2*kanonik kapanış={2 * canonical_closes}")
        if canonical_wins is not None:
            ok &= chk("MATCHES_CANONICAL_WINS",
                      abs(float(g.get("s", 0)) - 2 * canonical_wins) < 1e-9,
                      f"global s={g.get('s')} vs 2*kanonik kazanç={2 * canonical_wins}")
    else:
        ok &= chk("GLOBAL_NODE_PRESENT", False, "global düğüm yok")

    return {"schema_version": SCHEMA_VERSION,
            "state": (REPAIRABLE if ok else BLOCKED),
            "checks": checks,
            "n_ancestor_nodes": len(anc), "n_leaf_nodes": len(leaves),
            "input_sha256": sha256_of(doc),
            "invariant": ("ağırlıksız modda her add w=1 ekler → eski ata kütlesi yeni ata "
                          "kütlesinin TAM İKİ KATIDIR; n, s ve ss birlikte yarılanır"),
            "untouched": ["win yaprak düğümleri", "exp_r", "agent_hit", "lessons",
                          "calibrator", "n_closed", "alpha", "prior_mean", "half_life_days",
                          "last_metrics", "baseline_metrics", "LearnedIndex (ayrı dosya)"]}


def convert(doc: dict[str, Any], *, canonical_closes: int | None = None,
            canonical_wins: int | None = None) -> dict[str, Any]:
    """DRY-RUN dönüşüm: yeni belge + düğüm bazında fark. Hiçbir dosyaya YAZMAZ."""
    rep = analyse(doc, canonical_closes=canonical_closes, canonical_wins=canonical_wins)
    if rep["state"] != REPAIRABLE:
        return {"analysis": rep, "converted": None, "diff": [],
                "reason": f"dönüşüm YAPILMADI (durum {rep['state']})"}
    new = json.loads(json.dumps(doc))          # derin kopya — girdi DEĞİŞMEZ
    diff = []
    for key, st in new["win"]["stats"].items():
        if not _is_ancestor(key):
            continue
        before = {"n": st.get("n"), "s": st.get("s"), "ss": st.get("ss")}
        st["n"] = st["n"] / 2 if not _int_like(st["n"]) else int(round(st["n"])) // 2
        st["s"] = st["s"] / 2.0
        st["ss"] = st["ss"] / 2.0
        diff.append({"node": key, "kind": ("GLOBAL" if key == "" else "REGIME"),
                     "before": before,
                     "after": {"n": st["n"], "s": st["s"], "ss": st["ss"]}})
    new[MARKER_KEY] = {
        "schema_version": SCHEMA_VERSION,
        "applied": True,
        "what": "win ata düğümlerinde (global + regime) n, s, ss yarılandı",
        "reason": ("on_trade_closed iki `win.add` çağrısı ortak ataları iki kez sayıyordu "
                   "(bkz. docs/LEARNING_COUNT_INTEGRITY_V3.md)"),
        "input_sha256": rep["input_sha256"],
        "n_nodes_changed": len(diff),
        "untouched": rep["untouched"],
    }
    return {"analysis": rep, "converted": new, "diff": diff,
            "output_sha256": sha256_of(new),
            "n_nodes_changed": len(diff),
            "equivalence_claim": ("dönüştürülmüş durum, düzeltilmiş semantikle sıfırdan "
                                  "kurulmuş duruma BİREBİR eşit olmalıdır (test ile sabit)")}


def diff_summary(conv: dict[str, Any]) -> str:
    if not conv.get("converted"):
        return f"DÖNÜŞÜM YOK — {conv.get('reason')}"
    lines = [f"{'düğüm':38s} {'n (önce→sonra)':22s} {'s (önce→sonra)':22s}"]
    for d in conv["diff"]:
        b, a = d["before"], d["after"]
        node = d["node"] or "(global)"
        lines.append(f"{node:38s} {str(b['n']) + ' → ' + str(a['n']):22s} "
                     f"{str(b['s']) + ' → ' + str(a['s']):22s}")
    return "\n".join(lines)


__all__ = ["ALREADY_CONVERTED", "BLOCKED", "EXPECTED_SCHEMA", "MARKER_KEY", "REPAIRABLE",
           "SCHEMA_VERSION", "analyse", "convert", "diff_summary", "sha256_of"]


# --------------------------------------------------------------- A/B seçenek karşılaştırması
def option_comparison(doc: dict[str, Any], *, future_closes: tuple[int, ...] = (0, 10, 25, 50, 100),
                      scenario_win_rate: float = 0.28) -> dict[str, Any]:
    """A (eski toplamları koru) ile B (ata istatistiklerini onar) arasındaki farkı ÖLÇER.

    `future_closes` dizileri **SENARYODUR, piyasa tahmini DEĞİLDİR**: yalnız kirlenmiş kütlenin
    ileride nasıl davrandığını gösterir. Kazanma oranı gözlenen orandan alınır ve sabit tutulur;
    bu bir beklenti iddiası değil, ARİTMETİK bir gösterimdir.

    Kritik girdi: `half_life_days is None` → yenilik ağırlığı YOK → kirlenme KENDİLİĞİNDEN
    SEYRELMEZ; yalnız yeni gözlemlerle oransal olarak küçülür ve HİÇ kaybolmaz.
    """
    import math
    win = doc.get("win") or {}
    g = (win.get("stats") or {}).get("") or {}
    alpha = float(win.get("alpha") or 10.0)
    prior = float(win.get("prior_mean") or 0.5)
    n_a, s_a = float(g.get("n", 0.0)), float(g.get("s", 0.0))
    n_b, s_b = n_a / 2.0, s_a / 2.0
    q = float(scenario_win_rate)

    def post(n: float, s: float) -> float:
        return (s + alpha * prior) / (n + alpha) if (n + alpha) > 0 else prior

    def unc(n: float) -> float:
        return 0.20 / math.sqrt(max(0.0, n) + 1.0)

    rows = []
    for k in future_closes:
        na, sa = n_a + k, s_a + q * k
        nb, sb = n_b + k, s_b + q * k
        rows.append({
            "future_closes": k,
            "A_global_n": round(na, 4), "A_posterior": round(post(na, sa), 6),
            "A_uncertainty_penalty_r": round(unc(na), 6),
            "B_global_n": round(nb, 4), "B_posterior": round(post(nb, sb), 6),
            "B_uncertainty_penalty_r": round(unc(nb), 6),
            "delta_posterior_B_minus_A": round(post(nb, sb) - post(na, sa), 6),
            "delta_uncertainty_B_minus_A": round(unc(nb) - unc(na), 6),
            "excess_mass_in_A": round(na - nb, 4),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "label": "SENARYO — piyasa tahmini DEĞİL; sabit kazanma oranıyla aritmetik gösterim",
        "scenario_win_rate": q,
        "decay_present": win.get("half_life_days") is not None,
        "permanence": ("half_life_days None → yenilik ağırlığı YOK: A'da fazla kütle asla "
                       "kaybolmaz, yalnız yeni gözlemler karşısında oransal olarak küçülür"),
        "rows": rows,
        "note": ("bu tablo yalnız GLOBAL düğümü gösterir; 7 rejim düğümü de aynı biçimde "
                 "kirlidir ve az örnekli rejimlerde etki daha büyüktür"),
    }


# ------------------------------------------------- bağımsız doğrulama: derslerden tam replay
def replay_from_lessons(doc: dict[str, Any], *, semantics: str = "OLD") -> dict[str, Any]:
    """`lessons` kayıtlarından `win`/`exp_r` hiyerarşisini YENİDEN KURAR.

    Dersler kapanış ANINDAKİ rejimi (`lesson.regime`) taşır — bu, `on_trade_closed`'ın düğüm
    anahtarını kurarken kullandığı DEĞERİN TA KENDİSİDİR. Bu yüzden tam olay replay'i
    mümkündür ve dönüştürücünün çıktısı BAĞIMSIZ olarak doğrulanabilir.

    Sınır: `save()` dersleri son 500 ile sınırlar; 500'den fazla kapanış birikince eski
    dersler düşer ve bu replay eksik kalır (bkz. provenans notu).
    """
    from ..learn.model import HierarchicalRate
    win_cfg, exp_cfg = doc.get("win") or {}, doc.get("exp_r") or {}
    win = HierarchicalRate(float(win_cfg.get("alpha", 10.0)),
                           float(win_cfg.get("prior_mean", 0.5)),
                           win_cfg.get("half_life_days"))
    exp_r = HierarchicalRate(float(exp_cfg.get("alpha", 10.0)),
                             float(exp_cfg.get("prior_mean", 0.0)),
                             exp_cfg.get("half_life_days"))
    for ls in (doc.get("lessons") or []):
        sym = str(ls.get("symbol") or "")
        setup = str(ls.get("setup") or "-")
        side = str(ls.get("side") or "")
        regime = (str(ls.get("regime")) or None) if ls.get("regime") else None
        won = 1.0 if ls.get("won") else 0.0
        if semantics == "OLD":
            win.add(won, regime=regime, leaf=f"{sym}|{setup}")
            win.add(won, regime=regime, leaf=sym)
        else:
            win.add(won, regime=regime, leaves=(f"{sym}|{setup}", sym))
        try:
            exp_r.add(float(ls.get("r") or 0.0), regime=regime, leaf=f"{setup}|{side}")
        except (TypeError, ValueError):
            continue
    return {"semantics": semantics,
            "win": {k: v.to_dict() for k, v in win.stats.items()},
            "exp_r": {k: v.to_dict() for k, v in exp_r.stats.items()},
            "n_lessons": len(doc.get("lessons") or []),
            "lessons_window_note": ("`lessons` son 500 ile sınırlıdır; daha fazla kapanışta "
                                    "bu replay EKSİK olur"),
            "evidence_class": "POINT_IN_TIME"}


def _cmp_stats(a: dict[str, Any], b: dict[str, Any], *, tol: float = 1e-9) -> dict[str, Any]:
    keys = set(a) | set(b)
    mism = []
    for k in sorted(keys):
        x, y = a.get(k), b.get(k)
        if x is None or y is None:
            mism.append({"node": k, "left": x, "right": y, "why": "yalnız bir tarafta"})
            continue
        if abs(float(x["n"]) - float(y["n"])) > tol or abs(float(x["s"]) - float(y["s"])) > tol:
            mism.append({"node": k, "left": x, "right": y, "why": "değer farkı"})
    return {"n_nodes": len(keys), "n_mismatch": len(mism), "identical": not mism,
            "mismatches": mism[:10]}


def verify_conversion(doc: dict[str, Any], conv: dict[str, Any]) -> dict[str, Any]:
    """Dönüştürücü çıktısını BAĞIMSIZ bir kanıtla (derslerden replay) karşılaştırır.

    İki bağımsız yol aynı sonucu veriyorsa onarım kanıtlanmıştır:
    1. ata düğümleri yarılama (tekdüze iki kat ters çevirme),
    2. `lessons` üzerinden düzeltilmiş semantikle sıfırdan kurulum.
    """
    old_replay = replay_from_lessons(doc, semantics="OLD")
    new_replay = replay_from_lessons(doc, semantics="NEW")
    stored_matches_old = _cmp_stats(doc.get("win", {}).get("stats", {}), old_replay["win"])
    converted = (conv or {}).get("converted")
    if converted is None:
        return {"state": "NO_CONVERSION", "stored_matches_old_replay": stored_matches_old}
    conv_matches_new = _cmp_stats(converted.get("win", {}).get("stats", {}), new_replay["win"])
    exp_untouched = _cmp_stats(doc.get("exp_r", {}).get("stats", {}),
                               converted.get("exp_r", {}).get("stats", {}))
    return {
        "schema_version": SCHEMA_VERSION,
        "state": ("VERIFIED" if (stored_matches_old["identical"]
                                 and conv_matches_new["identical"]
                                 and exp_untouched["identical"]) else "NOT_VERIFIED"),
        "stored_matches_old_replay": stored_matches_old,
        "converted_matches_new_replay": conv_matches_new,
        "exp_r_untouched": exp_untouched,
        "independent_paths": ["ata yarılama (tekdüze iki kat tersi)",
                              "derslerden düzeltilmiş semantikle tam replay"],
        "conclusion": ("iki bağımsız yol aynı durumu veriyorsa onarım KANITLANMIŞTIR; "
                       "eksik kapanış-anı rejimi iddiası GEÇERSİZDİR (dersler taşıyor)"),
    }
