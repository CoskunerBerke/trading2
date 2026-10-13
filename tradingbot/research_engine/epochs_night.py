"""P3g — config dönemlerinin gece kurulumu ve mühürden sonraki dönemler (ana belge §6.2 "yeni config = yeni dönem";
docs/ENGINE_BRAIN_V1.md §8.4 P3a notu (b), (c)). Yalnız PAPER araştırma kodu: config'i SALT-OKUNUR okur, yalnız
`data/research/library/` altına yazar; mühürlü `CONFIG_EPOCHS_V1` dosyası (paket) DEĞİŞMEZ.

* **Kurulum:** gece başlangıcı mühür anından (`covered_until` = `T_SEAL_MS`) SONRAYSA, `library/config_epochs.json`
  (araştırma kökü) = mühürlü V1'in bütün alanları ve dönemleri AYNEN + gece birimince açılan dönemler (sona eklenir) +
  `night` bilgisi. P2 okuyucusu (`rehydrate.config_epoch`) bu dosyayı okur: `covered_until`'dan sonra açılan işlem YALNIZ
  o andan sonra başlayan (gece) dönemlerle örtülür. Mühürden önceki gece (yalnız testler) kurulum yapılmaz (P2'nin geçici
  sınırları sürer). Kurulan dosyanın V1 kısmı her gece paketteki mühürlü dosyayla karşılaştırılır (sha); tutmazsa kurulmaz.
* **Kural (V1'in kuralıyla aynı, ileriye):** her AÇIK gece canlı `config.yaml` okunur, her defter kuralının görünümü
  (`epochs.view`; başka defterlerin yalnız-kendi bölümleri ve belgelenmiş YALNIZ-KAYIT yolları dışarıda) hesaplanır.
  - İlk gözlem: görünüm V1'in o kuraldaki son döneminin görünümüyle AYNIYSA, `covered_until`'dan başlayan devam dönemi
    (`CONFIG`, `basis: SAME_VIEW_AS_SEALED`); FARKLIYSA `[covered_until, şimdi)` `TRANSITION` (dağıtım anı bilinmiyor;
    o aralıkta açılan işlem hiçbir döneme atanmaz) + şimdiden başlayan `CONFIG`.
  - Sonraki geceler: görünüm aynıysa yalnız `seen_until` ilerler; değiştiyse önceki dönem son görüldüğü anda kapanır,
    `[seen_until, şimdi)` `TRANSITION`, şimdiden yeni `CONFIG` (aynı ihtiyatlı kural: değişikliğin anı yalnız iki gece
    arasıyla bilinir).
* Görünümler `library/config_views_night.json`'da (view_sha → görünüm) tutulur: parite (b) gece bağlantısı dönem
  kopyasını bunlardan kurar (V1'in eski dönemlerinin görünümleri git geçmişindedir; gece git'e bakmaz → o dönemler
  `VIEW_UNAVAILABLE`/`NOT_COMPARABLE` raporlanır).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import epochs as EP
from . import registry as RG
from .ledgers import iso, parse_ts

SCHEMA = "config_epochs_night_v1"
INSTALLED_NAME = "config_epochs.json"
STATE_NAME = "config_epochs_night.json"
VIEWS_NAME = "config_views_night.json"
UTC = timezone.utc
ST_INSTALLED, ST_BEFORE_SEAL, ST_SEAL_MISMATCH = "OK", "BEFORE_SEAL", "SEALED_FILE_MISMATCH"


class SealedEpochsMismatch(RuntimeError):
    """Paketteki `library/config_epochs.json` mühürlü sha'yı tutmuyor (kurulmaz)."""


def library_dir(paths: Any) -> Path:
    return Path(paths.research) / "library"


def sealed_doc() -> dict[str, Any]:
    with open(RG.CONFIG_EPOCHS_FILE, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if RG.sha256_canonical(doc) != RG.CONFIG_EPOCHS_V1_SHA:
        raise SealedEpochsMismatch("library/config_epochs.json mühürlü CONFIG_EPOCHS_V1 sha'sını tutmuyor")
    return doc


def live_views(config_bytes: bytes) -> dict[str, tuple[str, dict[str, Any]]]:
    """Canlı config baytlarından kural → (view_sha, görünüm)."""
    import yaml
    flat = EP.flatten(yaml.safe_load(config_bytes.decode("utf-8")))
    out = {}
    for rule in EP.RULES:
        v = EP.view(flat, rule)
        out[rule] = (EP.view_sha(v), v)
    return out


def _last_sealed(doc: Mapping[str, Any], rule: str) -> dict[str, Any] | None:
    best = None
    for e in doc.get("epochs") or ():
        if e.get("rule") == rule and e.get("kind") != "TRANSITION":
            if best is None or str(e.get("from")) >= str(best.get("from")):
                best = e
    return best


def _read(p: Path) -> Any:
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _epoch(kind: str, rule: str, books: list[str], at: str, *, view_sha: str | None, cfg_sha: str | None,
           basis: str) -> dict[str, Any]:
    eid = ("X" if kind == "TRANSITION" else "E") + EP._compact(at)
    e = {"id": eid, "kind": kind, "rule": rule, "books": list(books), "from": at, "to": None,
         "opened_by": "engine-night", "basis": basis, "config_sha256": cfg_sha}
    if view_sha is not None:
        e["view_sha"] = view_sha
    return e


def update(paths: Any, *, config_bytes: bytes | None, config_sha256: str | None, now: datetime) -> dict[str, Any]:
    """Bir gecelik gözlem + kurulum (modül belgesi). `config_bytes` None ise (config okunamadı) gözlem yapılmaz; var olan
    dönemler kurulmaya devam eder. Dönen: kısa rapor."""
    doc = sealed_doc()
    covered = parse_ts(doc.get("covered_until"))
    now = now.astimezone(UTC)
    if covered is None or now < covered:
        return {"status": ST_BEFORE_SEAL, "covered_until": doc.get("covered_until")}
    lib = library_dir(paths)
    st = _read(lib / STATE_NAME)
    if not isinstance(st, dict) or st.get("schema") != SCHEMA or st.get("sealed_sha256") != RG.CONFIG_EPOCHS_V1_SHA:
        st = {"schema": SCHEMA, "sealed_sha256": RG.CONFIG_EPOCHS_V1_SHA, "covered_until": doc.get("covered_until"),
              "rules": {}}
    views = _read(lib / VIEWS_NAME)
    views = views if isinstance(views, dict) else {}
    rep: dict[str, Any] = {"status": ST_INSTALLED, "opened": [], "observed": bool(config_bytes is not None)}
    at = iso(now)
    if config_bytes is not None:
        try:
            lv = live_views(config_bytes)
        except Exception as exc:  # noqa: BLE001 — ayrıştırılamayan config: gözlem yok (dönem açılmaz), nedeni yazılır
            lv = None
            rep["observed"] = False
            rep["error"] = f"{type(exc).__name__}: {exc}"[:200]
        for rule, books in (doc.get("book_rules") or {}).items():
            if lv is None or rule not in lv:
                continue
            vsha, view = lv[rule]
            views[vsha] = view
            rs = st["rules"].get(rule)
            if rs is None:
                last = _last_sealed(doc, rule)
                cu = str(doc.get("covered_until"))
                if last is not None and last.get("view_sha") == vsha:
                    eps = [_epoch("CONFIG", rule, books, cu, view_sha=vsha, cfg_sha=config_sha256,
                                  basis="SAME_VIEW_AS_SEALED")]
                else:
                    eps = [_epoch("TRANSITION", rule, books, cu, view_sha=None, cfg_sha=None,
                                  basis="VIEW_CHANGED_SINCE_SEAL"),
                           _epoch("CONFIG", rule, books, at, view_sha=vsha, cfg_sha=config_sha256,
                                  basis="FIRST_NIGHT_OBSERVATION")]
                    eps[0]["to"] = at
                rs = st["rules"][rule] = {"epochs": eps, "view_sha": vsha, "seen_until": at}
                rep["opened"] += [e["id"] + ":" + rule for e in eps]
                continue
            if rs.get("view_sha") == vsha:
                rs["seen_until"] = at
                continue
            prev = rs["epochs"][-1]
            seen = str(rs.get("seen_until") or prev.get("from"))
            prev["to"] = seen
            tr = _epoch("TRANSITION", rule, books, seen, view_sha=None, cfg_sha=None, basis="VIEW_CHANGED")
            tr["to"] = at
            new = _epoch("CONFIG", rule, books, at, view_sha=vsha, cfg_sha=config_sha256, basis="VIEW_CHANGED")
            rs["epochs"] += [tr, new]
            rs["view_sha"], rs["seen_until"] = vsha, at
            rep["opened"] += [tr["id"] + ":" + rule, new["id"] + ":" + rule]
    night_eps = [e for rule in sorted(st["rules"]) for e in st["rules"][rule]["epochs"]]
    merged = dict(doc)
    merged["epochs"] = list(doc.get("epochs") or []) + night_eps
    merged["night"] = {"schema": SCHEMA, "sealed_sha256": RG.CONFIG_EPOCHS_V1_SHA, "epochs": len(night_eps),
                       "rule_tr": "mühürden sonra görünümü değişen defterde yeni dönem (V1 kuralı); değişiklik anı iki "
                                  "gece arasıyla bilinir → TRANSITION"}
    target = lib / INSTALLED_NAME
    data = (json.dumps(merged, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    old = target.read_bytes() if target.exists() else None
    if old != data:
        paths.write_bytes(target, data)
    paths.write_json(lib / STATE_NAME, st)
    paths.write_json(lib / VIEWS_NAME, dict(sorted(views.items())))
    rep.update(installed_changed=old != data, night_epochs=len(night_eps), sealed_epochs=len(doc.get("epochs") or []),
               rules=len(st["rules"]))
    rep["opened"] = rep["opened"][:20]
    return rep


def views(paths: Any) -> dict[str, dict[str, Any]]:
    """Gece birimince görülmüş görünümler (view_sha → görünüm); parite (b) gece bağlantısı."""
    v = _read(library_dir(paths) / VIEWS_NAME)
    return v if isinstance(v, dict) else {}


__all__ = ["INSTALLED_NAME", "SCHEMA", "STATE_NAME", "ST_BEFORE_SEAL", "ST_INSTALLED", "ST_SEAL_MISMATCH",
           "SealedEpochsMismatch", "VIEWS_NAME", "library_dir", "live_views", "sealed_doc", "update", "views"]
