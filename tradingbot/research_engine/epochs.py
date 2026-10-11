"""P3a — config dönemleri üreticisi: `library/config_epochs.json` (mühür `CONFIG_EPOCHS_V1`; ana belge §6.2 "Config
dönemleri", ENGINE_BRAIN §8.1 P3a).

Girdi (belirlenimci): dağıtılmış her worker sürüm SHA'sındaki `config.yaml` baytları (git geçmişi; okuyucu
`scripts/gen_config_epochs.py`, bu modül git ÇAĞIRMAZ) + bu dosyadaki BELGELENMİŞ kayıtlar:

* `DEPLOYS` — hangi sürüm ne zaman canlıya çıktı. Kesin saat yalnız depoda yazılıysa (`at`); yoksa kanıtla sınırlanmış
  bir PENCERE (`window` [alt, üst)): alt sınır = dağıtımın olamayacağı son an (sürüm betiğinin commit'i ya da önceki
  sürümün çalıştığı belgelenmiş son an), üst sınır = sürümün çalıştığı belgelenmiş ilk an. Her kaydın `source`'u vardır.
* `LOCAL_EDITS` — VPS'te git dışı, belgelenmiş config düzenlemeleri (sahip kararı).
* `DECISION_NEUTRAL` — belgelenmiş YALNIZ-KAYIT bölümleri (karar değiştirmediği bayt testleriyle gösterilmiş); bunlar
  dönem açmaz, `releases[].changed_paths`'te görünür.

Kurallar:
* Config ayrıştırılır (yorumlar dönem açmaz) ve yollara düzleştirilir; `strategy_paper` ve `.extra[]` girdileri
  `[name=…]` ile adlandırılır.
* Defter görünümü = bütün yollar − BAŞKA defterlerin yalnız-kendi bölümleri (`strategy_paper[name=X]`,
  `strategy_paper.extra[name=X]`, `pattern_trader`, `learning_mode.books.X`, `structures.X`, `m2x_aggressive`) −
  `DECISION_NEUTRAL`. Paylaşılan her bölüm (ve tanınmayan defter adı) HER defterin görünümüne girer (muhafazakâr).
* Görünüm bir dağıtımda değişirse o defter için yeni dönem açılır: kesin saatte sınır; pencerede `TRANSITION` dönemi
  (o aralıkta açılan işlem hiçbir config dönemine atanmaz, dönem içi kıyasa girmez). Bitişik/örtüşen pencereler
  birleşir. İlk dönem `E0` (`PRE_HISTORY`): bilinen taban sürümün (3b0ae8e) config'i; daha önceki değişiklikler
  belgelenmemiştir.
* Biçim P2 okuyucusuyla (`rehydrate.config_epoch`) uyumludur: `{"epochs": [{"id", "from", "to", "books"}]}`; `E0`'ın
  `from`'u `UNBOUNDED_FROM`'dur.

Yalnız standart kütüphane + PyYAML (yalnız `build` içinde); ağ ve dış süreç yok.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SCHEMA = "config_epochs_v1"
UNBOUNDED_FROM = "1970-01-01T00:00:00+00:00"
KIND_PRE, KIND_CONFIG, KIND_TRANSITION = "PRE_HISTORY", "CONFIG", "TRANSITION"
UTC = timezone.utc

#: kural adı (config adı) → motorun defter kimlikleri (state klasörü; ana bot iki alt defter). Sıra = çıktı sırası.
BOOK_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("main", ("main_fut", "main_spot")),
    ("t2_trend_regime", ("strategy_paper",)),
    ("m2_tsmom28", ("strategy_paper_m2",)),
    ("b1_box_fade", ("strategy_paper_box",)),
    ("pattern_trader", ("pattern_trader",)),
    ("d4_donchian_20_10", ("strategy_paper_trend4h",)),
    ("c4_candle_variations", ("strategy_paper_candle4h",)),
    ("c4s_candle_variations_strict", ("strategy_paper_candle4h_strict",)),
    ("m2x_aggressive", ("strategy_paper_m2x",)),
)
RULES = tuple(r for r, _ in BOOK_RULES)

#: Belgelenmiş dağıtımlar (worker sürümleri; `deploy/releases/tb-deploy-<sha7>.sh`), zaman sırasıyla. `lo_commit` /
#: `hi_commit`: pencere sınırının dayandığı commit (üretici betik commit zamanını git'ten DOĞRULAR).
DEPLOYS: tuple[dict, ...] = (
    {"release": "3b0ae8e", "status": "BASE",
     "source": "deploy/releases/tb-deploy-4931c73.sh:22 PREREQ (bundle tabanı: VPS'te bulunması gereken son bilinen commit); "
               "dağıtım saati ve daha önceki config değişiklikleri depoda yok",
     "decision_tr": "bilinen en eski taban"},
    {"release": "4931c73", "status": "DEPLOYED",
     "window": ["2026-09-25T17:35:20+00:00", "2026-09-27T09:05:12+00:00"], "lo_commit": "ece2edd", "hi_commit": "95735f7",
     "source": "alt: sürüm betiğinin ilk commit'i ece2edd; üst: tb-deploy-a8fe2a5.sh:22 (95735f7) 'VPS'te çalışan sürüm "
               "(2026-09-26 dağıtımı)'",
     "decision_tr": "200 USDT bakiye ve ihtiyaç kadar kaldıraç (kullanıcı kararı 2026-09-21), yapı kataloğu ENFORCE "
                    "(2026-09-22), fonlama settlement v2, Formasyon protokol v3 ve D4 gözlem defteri (kullanıcı onayı "
                    "2026-09-25)"},
    {"release": "a8fe2a5", "status": "DEPLOYED",
     "window": ["2026-09-27T09:05:12+00:00", "2026-09-27T22:55:33+00:00"], "lo_commit": "95735f7", "hi_commit": "6662d5b",
     "source": "alt: sürüm betiğinin commit'i 95735f7; üst: tb-deploy-7ad8832.sh:22 (6662d5b) 'VPS'te çalışan sürüm "
               "(2026-09-27 dağıtımı)'",
     "decision_tr": "Box yapı katmanı SHADOW (kullanıcı kararı B, 2026-09-27); C4/C4S boş gözlem defterleri"},
    {"release": "7ad8832", "status": "NOT_DEPLOYED",
     "source": "docs/ogrenme_modu/DURUM.md:113 'VPS a8fe2a5 → c0b8c94' (7ad8832 ayrıca dağıtılmadı; içeriği c0b8c94'te)",
     "decision_tr": "C4 CV001–CV008 gözlem (kullanıcı onayı 2026-09-27) — c0b8c94 ile canlıya çıktı"},
    {"release": "c0b8c94", "status": "DEPLOYED", "at": "2026-09-28T21:20:00+00:00",
     "source": "docs/ogrenme_modu/DURUM.md:113 'DAĞITILDI (2026-09-28 21:20 UTC, kullanıcı): VPS a8fe2a5 → c0b8c94'",
     "decision_tr": "öğrenme modu L1 (YALNIZ PAPER) + C4 CV001–CV008 (7ad8832) + ana bot leverage_max 5"},
    {"release": "f9a61dd", "status": "DEPLOYED", "at": "2026-09-29T21:56:00+00:00",
     "source": "docs/ogrenme_modu/DURUM.md:81 'DAĞITILDI (2026-09-29 21:56 UTC, kullanıcı): VPS c0b8c94 → f9a61dd'",
     "decision_tr": "Ortak Deneyim Katmanı v1 YALNIZ KAYIT (karar değiştirmez)"},
    {"release": "f6e6119", "status": "DEPLOYED", "at": "2026-09-29T22:43:00+00:00",
     "source": "docs/ogrenme_modu/DURUM.md:79 'DAĞITILDI (2026-09-29 22:43 UTC, kullanıcı): VPS f9a61dd → f6e6119'",
     "decision_tr": "Box öğrenme slotu 20 → 40 (kullanıcı onayı; ana belge §6.2'nin '2026-09-30' örnek sınırı = TR tarihi)"},
    {"release": "188cf22", "status": "DEPLOYED", "at": "2026-09-30T20:05:00+00:00",
     "source": "docs/ogrenme_modu/DURUM.md:75 'DAĞITILDI (2026-09-30 20:05 UTC, kullanıcı): VPS f6e6119 → 188cf22'",
     "decision_tr": "gölge danışman YALNIZ KAYIT (karar değiştirmez)"},
    {"release": "fc63481", "status": "DEPLOYED",
     "window": ["2026-10-02T11:24:52+00:00", "2026-10-03T04:56:00+00:00"], "lo_commit": "943345c",
     "source": "alt: sürüm betiğinin commit'i 943345c; üst: docs/TOUR_CONTENTION_V1.md §1 tablosu (2026-10-03 07:55 TR "
               "biten tur fc63481); tb-deploy-f8b05fb.sh:4 '2026-10-02 dağıtımı'",
     "decision_tr": "config 188cf22'dekiyle bayt bayt aynı (karar değiştirmeyen tur hızlandırmaları)"},
    {"release": "f8b05fb", "status": "DEPLOYED",
     "window": ["2026-10-04T00:56:00+00:00", "2026-10-05T04:55:00+00:00"],
     "source": "dağıtım saati depoda yok (docs/M2_AGGRESSIVE_V1.md:28); alt: docs/TOUR_CONTENTION_V1.md §1 tablosu "
               "(2026-10-04 03:56 TR biten tur fc63481); üst: aynı tablo (2026-10-05 07:54 TR biten tur f8b05fb). "
               "tb-deploy-8db1faf.sh:3'teki '2026-10-03 dağıtımı' bu iki kayıtla çelişir; geniş pencere alınır",
     "decision_tr": "learning_mode.extra_entries: record_selectivity + Box öğrenme stop tabanı 0,32 → 0,5 (sahip kararı "
                    "2026-10-03)"},
    {"release": "8db1faf", "status": "DEPLOYED",
     "window": ["2026-10-05T22:21:04+00:00", "2026-10-06T13:56:00+00:00"], "lo_commit": "257ef8e",
     "source": "alt: sürüm betiğinin commit'i 257ef8e; üst: tb-deploy-db5db96.sh başlığı (2026-10-06 13:56 UTC yerel config "
               "düzenlemesi tb-deploy-8db1faf.sh'in satırıyla, 8db1faf çalışırken)",
     "decision_tr": "config f8b05fb'dekiyle aynı (pattern kanıtı alt süreci; karar değiştirmez)"},
    {"release": "db5db96", "status": "DEPLOYED", "at": "2026-10-07T19:31:00+00:00",
     "source": "docs/SYSTEM_LEARNING_ENGINE_V1.md:1874-1875 'app db5db96 … 2026-10-07 19:31 UTC; "
               "deploy-logs/db5db96-restart-at.txt'; docs/OWNER_RUNBOOK_2026-10.md:60 'KURULDU — 7 Ekim 22:31' (TR)",
     "decision_tr": "M2X bölümü (kod ve config varsayılanı KAPALI); yerel evidence_subprocess satırı dağıtımda geri alındı"},
)

#: VPS'te git dışı, belgelenmiş config düzenlemeleri. `until_release`: düzenlemeyi geri alan dağıtım.
LOCAL_EDITS: tuple[dict, ...] = (
    {"id": "evidence_subprocess_off", "path": "history.evidence_subprocess", "value": False,
     "at": "2026-10-06T13:56:00+00:00", "until_release": "db5db96",
     "source": "deploy/releases/tb-deploy-db5db96.sh başlığı: 'YEREL config.yaml DEĞİŞİKLİĞİ (2026-10-06 13:56 UTC, sahibin "
               "alt süreç kill switch'i)'; dağıtımda git checkout -- config.yaml ile geri alınır",
     "decision_tr": "sahibin pattern kanıtı alt süreci kapatması (karar değiştirmez)"},
)

#: belgelenmiş YALNIZ-KAYIT yollar (önek) → kaynak. Dönem açmaz.
DECISION_NEUTRAL: tuple[tuple[str, str], ...] = (
    ("history.evidence_subprocess", "tb-deploy-8db1faf.sh başlığı 'KARAR DEĞİŞTİRMEYEN TUR DÜZELTMESİ'; "
                                    "docs/TOUR_CONTENTION_V2.md"),
    ("shared_experience", "config.yaml shared_experience yorumu 'YALNIZ KAYIT … KARAR DEĞİŞMEZ'; tb-deploy-f9a61dd.sh ve "
                          "tb-deploy-188cf22.sh başlıkları (bayt-özdeşlik değişmezleri); docs/ortak_deneyim/SPEC_V1.md, "
                          "DANISMAN_V1.md"),
)

_NAMED = re.compile(r"^strategy_paper(?:\.extra)?\[name=([^\]]+)\]")


# ============================================================================ düzleştirme ve sahiplik
def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def flatten(doc: Any) -> dict[str, Any]:
    """Ayrıştırılmış config → {yol: yaprak değer}. `strategy_paper` girdileri `strategy_paper[name=X]` /
    `strategy_paper.extra[name=X]`; adlı sözlük listeleri `[name=X]`; diğer listeler tek yaprak."""
    out: dict[str, Any] = {}

    def rec(x: Any, p: str) -> None:
        if isinstance(x, dict):
            if not x:
                out[p] = {}
            for k in sorted(x, key=str):
                rec(x[k], f"{p}.{k}" if p else str(k))
        elif isinstance(x, list) and x and all(isinstance(i, dict) and isinstance(i.get("name"), str) for i in x):
            for i in x:
                rec(i, f"{p}[name={i['name']}]")
        else:
            out[p] = x

    d = dict(doc) if isinstance(doc, dict) else {}
    sp = d.pop("strategy_paper", None)
    rec(d, "")
    if isinstance(sp, dict):
        extra = sp.get("extra")
        top = {k: v for k, v in sp.items() if k != "extra"}
        name = top.get("name") if isinstance(top.get("name"), str) else "t2_trend_regime"
        rec(top, f"strategy_paper[name={name}]")
        if extra is not None:
            if isinstance(extra, list) and all(isinstance(i, dict) and isinstance(i.get("name"), str) for i in extra):
                for i in extra:
                    rec(i, f"strategy_paper.extra[name={i['name']}]")
            else:
                out["strategy_paper.extra"] = extra
    return out


def owner(path: str) -> str | None:
    """Yolun yalnız-kendi defteri (kural adı) ya da None (paylaşılan). Tanınmayan defter adı → None (muhafazakâr)."""
    m = _NAMED.match(path)
    rule = None
    if m:
        rule = m.group(1)
    elif path == "pattern_trader" or path.startswith("pattern_trader."):
        rule = "pattern_trader"
    elif path == "m2x_aggressive" or path.startswith("m2x_aggressive."):
        rule = "m2x_aggressive"
    elif path.startswith("learning_mode.books."):
        rule = path.split(".")[2]
    elif path.startswith("structures."):
        rule = path.split(".")[1]
    return rule if rule in RULES else None


def neutral(path: str) -> bool:
    return any(path == p or path.startswith(p + ".") or path.startswith(p + "[") for p, _ in DECISION_NEUTRAL)


def view(flat: Mapping[str, Any], rule: str) -> dict[str, Any]:
    return {p: v for p, v in flat.items() if owner(p) in (None, rule) and not neutral(p)}


def view_sha(v: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canon(dict(v)).encode("utf-8")).hexdigest()


def changed(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    return sorted(p for p in set(a) | set(b) if (p in a) != (p in b) or _canon(a.get(p)) != _canon(b.get(p)))


# ============================================================================ zaman
def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(UTC)


def _compact(s: str) -> str:
    t = _ts(s)
    return t.strftime("%Y-%m-%dT%H%M") + (t.strftime("%S") if t.second else "") + "Z"


# ============================================================================ üretim
def events(configs: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    """Zaman sıralı config olayları: dağıtılmış sürümler + yerel düzenlemeler (ve onları geri alan dağıtım).
    `configs`: sürüm → düzleştirilmiş config."""
    out: list[dict] = []
    edits_by_until = {e["until_release"]: e for e in LOCAL_EDITS}
    active: dict[str, Any] = {}
    seq = [d for d in DEPLOYS if d["status"] == "DEPLOYED"]
    timeline: list[tuple[str, str, dict]] = []                  # (sıralama anı, tür, kayıt)
    for d in seq:
        timeline.append((d["window"][0] if "window" in d else d["at"], "DEPLOY", d))
    for e in LOCAL_EDITS:
        timeline.append((e["at"], "LOCAL", e))
    timeline.sort(key=lambda t: (_ts(t[0]), t[1]))
    for _at, kind, rec in timeline:
        if kind == "DEPLOY":
            rel = rec["release"]
            if rel in edits_by_until:
                active.pop(edits_by_until[rel]["path"], None)       # dağıtım yerel düzenlemeyi geri alır
            flat = dict(configs[rel])
            flat.update(active)
            out.append({"id": rel, "kind": "DEPLOY", "lo": rec.get("window", [rec.get("at")])[0],
                        "hi": rec["window"][1] if "window" in rec else rec["at"], "flat": flat,
                        "decision_tr": rec["decision_tr"]})
        else:
            active[rec["path"]] = rec["value"]
            flat = dict(out[-1]["flat"]) if out else {}
            flat.update(active)
            out.append({"id": rec["id"], "kind": "LOCAL_EDIT", "lo": rec["at"], "hi": rec["at"], "flat": flat,
                        "decision_tr": rec["decision_tr"]})
    return out


def _merge(bounds: list[dict]) -> list[dict]:
    """Bitişik/örtüşen pencereleri birleştir (aradaki config dönemi boş kalırdı)."""
    out: list[dict] = []
    for b in bounds:
        if out and _ts(b["lo"]) <= _ts(out[-1]["hi"]) and out[-1]["hi"] != out[-1]["lo"]:
            last = out[-1]
            last["hi"] = max(last["hi"], b["hi"], key=_ts)
            last["ids"] = last["ids"] + b["ids"]
            last["changed"] = sorted(set(last["changed"]) | set(b["changed"]))
            last["decisions"] = last["decisions"] + b["decisions"]
            last["to_release"] = b["to_release"]
            last["view_sha"] = b["view_sha"]
        else:
            out.append(dict(b))
    return out


def book_epochs(rule: str, books: tuple[str, ...], base_flat: Mapping[str, Any], evs: list[dict]) -> list[dict]:
    prev = view(base_flat, rule)
    prev_release = "3b0ae8e"
    bounds: list[dict] = []
    for ev in evs:
        v = view(ev["flat"], rule)
        if v != prev:
            bounds.append({"lo": ev["lo"], "hi": ev["hi"], "ids": [ev["id"]], "changed": changed(prev, v),
                           "decisions": [ev["decision_tr"]], "from_release": prev_release, "to_release": ev["id"],
                           "view_sha": view_sha(v)})
            prev = v
        prev_release = ev["id"]
    bounds = _merge(bounds)
    eps: list[dict] = [{"id": "E0", "kind": KIND_PRE, "from": UNBOUNDED_FROM, "release": "3b0ae8e",
                        "view_sha": view_sha(view(base_flat, rule)),
                        "note_tr": "taban 3b0ae8e config'i; daha önceki değişiklikler ve tabanın dağıtım saati belgelenmemiştir"}]
    for b in bounds:
        if b["lo"] != b["hi"]:
            eps.append({"id": "X" + _compact(b["lo"]), "kind": KIND_TRANSITION, "from": b["lo"],
                        "from_release": b["from_release"], "to_release": b["to_release"], "events": b["ids"],
                        "opened_by": b["changed"], "decisions_tr": b["decisions"],
                        "note_tr": "dağıtım anı yalnız pencereyle biliniyor: bu aralıkta açılan işlem hiçbir config dönemine "
                                   "atanmaz"})
        eps.append({"id": "E" + _compact(b["hi"]), "kind": KIND_CONFIG, "from": b["hi"], "release": b["to_release"],
                    "events": b["ids"], "view_sha": b["view_sha"], "opened_by": b["changed"],
                    "decisions_tr": b["decisions"]})
    for a, b in zip(eps, eps[1:]):
        a["to"] = b["from"]
    eps[-1]["to"] = None
    for e in eps:
        e["rule"] = rule
        e["books"] = list(books)
    return eps


def build(config_texts: Mapping[str, str], full_shas: Mapping[str, str] | None = None) -> dict:
    """`config_texts`: sürüm (sha7) → o sürümdeki `config.yaml` metni (her `DEPLOYS` kaydı için). Saf ve belirlenimci."""
    import yaml  # PyYAML (venv'de mevcut; rawconfig ile aynı)

    missing = [d["release"] for d in DEPLOYS if d["release"] not in config_texts]
    if missing:
        raise ValueError(f"config metni eksik: {missing}")
    flats = {rel: flatten(yaml.safe_load(txt)) for rel, txt in config_texts.items()}
    evs = events(flats)
    releases = []
    prev = flats["3b0ae8e"]
    for d in DEPLOYS:
        rec = {"release": d["release"], "status": d["status"], "source": d["source"], "decision_tr": d["decision_tr"],
               "config_sha256": hashlib.sha256(config_texts[d["release"]].encode("utf-8")).hexdigest()}
        if full_shas:
            rec["sha"] = full_shas[d["release"]]
        for k in ("at", "window"):
            if k in d:
                rec[k] = d[k]
        if d["status"] == "DEPLOYED":
            rec["changed_paths"] = changed(prev, flats[d["release"]])
            prev = flats[d["release"]]
        releases.append(rec)
    epochs: list[dict] = []
    for rule, books in BOOK_RULES:
        epochs.extend(book_epochs(rule, books, flats["3b0ae8e"], evs))
    return {
        "schema": SCHEMA, "doc": "docs/SYSTEM_LEARNING_ENGINE_V1.md §6.2; docs/ENGINE_BRAIN_V1.md §8.1 P3a",
        "generator": "tradingbot/research_engine/epochs.py (scripts/gen_config_epochs.py; git geçmişi + belgelenmiş kayıtlar)",
        "rules_tr": {
            "view": "defter görünümü = bütün config yolları − başka defterlerin yalnız-kendi bölümleri − karar-nötr yollar",
            "boundary": "görünüm değişen dağıtımda yeni dönem; kesin saat yoksa TRANSITION penceresi (o aralıkta açılan işlem "
                        "hiçbir config dönemine atanmaz)",
            "pre_history": "E0 = taban 3b0ae8e config'i ve öncesi (belgelenmemiş)",
            "new_epoch_at_night": "her gece rawconfig canlı config sha'sını son dönemle karşılaştırır; yeni sha = yeni dönem "
                                  "(ana belge §6.2)",
            "reader": "rehydrate.config_epoch: kitap eşleşen, from ≤ an < to olan dönem",
        },
        "decision_neutral": [{"path": p, "source": s} for p, s in DECISION_NEUTRAL],
        "book_rules": {rule: list(books) for rule, books in BOOK_RULES},
        "releases": releases,
        "local_edits": [dict(e) for e in LOCAL_EDITS],
        "epochs": epochs,
    }


def render(doc: Mapping[str, Any]) -> str:
    """Depodaki dosyanın baytları (sıralı anahtar, girintili, UTF-8). Mühür sha'sı kanonik içerikten (`registry`)."""
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1) + "\n"


def releases_needed() -> list[str]:
    return [d["release"] for d in DEPLOYS]


def bound_commits() -> Iterable[tuple[str, str, str]]:
    """(sürüm, sınır, commit) — üretici betik commit zamanının pencere sınırına eşit olduğunu git'ten doğrular."""
    for d in DEPLOYS:
        if "lo_commit" in d:
            yield d["release"], d["window"][0], d["lo_commit"]
        if "hi_commit" in d:
            yield d["release"], d["window"][1], d["hi_commit"]


__all__ = ["BOOK_RULES", "DECISION_NEUTRAL", "DEPLOYS", "KIND_CONFIG", "KIND_PRE", "KIND_TRANSITION", "LOCAL_EDITS", "RULES",
           "SCHEMA", "UNBOUNDED_FROM", "book_epochs", "bound_commits", "build", "changed", "events", "flatten", "neutral",
           "owner", "releases_needed", "render", "view", "view_sha"]
