"""Canlı config'in HAM, hoşgörülü okunması (§2.3) — `load_config` / `load_v3` HİÇ çağrılmaz.

Neden: `config_v3.load_v3`, `learning_mode` ve `shared_experience` bölümlerinde bilinmeyen anahtarda `ConfigError`
atar (config_v3.py `load_v3` başı). Sabitlenmiş eski bir motor, yeni bir app sürümünün eklediği anahtar yüzünden her
gece kırılırdı (tersi de olabilir). Bu yüzden motor `/opt/tradingbot/app/config.yaml`'ı salt-okunur açar, baytların
sha256'sını kaydeder ve `yaml.safe_load` ile yalnız aşağıdaki İHTİYAÇ LİSTESİNİ hoşgörülü çeker: bilinmeyen anahtar
yok sayılır, eksik anahtar `None` olur, tip uyuşmazlığı `None` olur (istisna yok).

İhtiyaç listesi (`NEEDS`, testli; §2.3): defter `enabled` / `min_stop_pct`, `learning_mode.extra_entries`,
`risk.starting_equity_usdt`. Defterlerin state klasörü ve başlangıç bakiyesi de (`strategy_paper`, `.extra[]`,
`pattern_trader`) yalnız GÖSTERİM ve config dönemi için okunur; hiçbir hesap bunlara dayanmaz (bakiye ölçülmüş
anlık görüntüden gelir). P1b: `entry_universe.enabled` / `entry_universe.symbols` yalnız araştırma evreninin (U_R,
§3.3) "40 giriş evreni" bileşeni için okunur (veri birimi; hiçbir karar yolu bu değeri motordan almaz).

Bu modül `config_v3`'ü ve `config.load_config`'i import ETMEZ (AST testi).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Varsayılan canlı config yolu (VPS app ağacı; motor buraya yalnız okumak için bakar).
DEFAULT_CONFIG_PATH = Path("/opt/tradingbot/app/config.yaml")

#: İHTİYAÇ LİSTESİ (§2.3) — motorun canlı config'ten okuduğu anahtarların TAMAMI. `*` defter adı yerine geçer.
NEEDS: tuple[str, ...] = (
    "risk.starting_equity_usdt",
    "learning_mode.extra_entries",
    "learning_mode.books.*.enabled",
    "learning_mode.books.*.min_stop_pct",
    "strategy_paper.enabled", "strategy_paper.name", "strategy_paper.state_dir", "strategy_paper.starting_equity_usdt",
    "strategy_paper.extra[].name", "strategy_paper.extra[].enabled", "strategy_paper.extra[].state_dir",
    "strategy_paper.extra[].starting_equity_usdt", "strategy_paper.extra[].rule_params.min_stop_pct",
    "pattern_trader.enabled", "pattern_trader.state_dir", "pattern_trader.starting_equity_usdt",
    "entry_universe.enabled", "entry_universe.symbols",
    # P2 (rehydrate, §5.2): kural geometrisinin parametreleri — yalnız kayıtlı işlemin hedefini/bağlamını yeniden kurmak
    # için okunur; hiçbir karar yolu bu değerleri motordan almaz.
    "strategy_paper.atr_mult", "strategy_paper.rule_params",
    "strategy_paper.extra[].atr_mult", "strategy_paper.extra[].rule_params",
)


@dataclass
class RawConfig:
    path: str
    ok: bool
    sha256: str | None = None
    size: int | None = None
    error: str | None = None
    #: düz ihtiyaç değerleri ("risk.starting_equity_usdt" → değer); defterler `books` altında
    values: dict[str, Any] = field(default_factory=dict)
    #: defter adı (config adı, ör. `b1_box_fade`) → {state_dir, enabled, learning_enabled, min_stop_pct, ...}
    books: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "ok": self.ok, "sha256": self.sha256, "size": self.size, "error": self.error,
                "values": dict(self.values), "books": {k: dict(v) for k, v in self.books.items()}}

    def book_by_state_dir(self, state_dir: str) -> dict[str, Any] | None:
        """State klasör adına (ör. `strategy_paper_box`; ana bot için "") göre defter girdisi."""
        for b in self.books.values():
            if b.get("state_dir") == state_dir:
                return b
        return None


def _get(d: Any, *keys: str) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _bool(x: Any) -> bool | None:
    return x if isinstance(x, bool) else None


def _str(x: Any) -> str | None:
    return x if isinstance(x, str) else None


def _plain(x: Any) -> dict[str, Any] | None:
    """Kural parametre sözlüğü (P2 rehydrate): yalnız düz skaler/liste değerler, anahtar sırasıyla; değilse None."""
    if not isinstance(x, dict):
        return None
    out: dict[str, Any] = {}
    for k in sorted(x, key=str):
        v = x[k]
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[str(k)] = v
        elif isinstance(v, list) and all(isinstance(i, (str, int, float, bool)) for i in v):
            out[str(k)] = list(v)
    return out


def extract(doc: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Ayrıştırılmış YAML belgesinden ihtiyaç listesini hoşgörülü çek. Dönen: (düz değerler, defterler)."""
    syms = _get(doc, "entry_universe", "symbols")
    values = {"risk.starting_equity_usdt": _num(_get(doc, "risk", "starting_equity_usdt")),
              "learning_mode.extra_entries": _str(_get(doc, "learning_mode", "extra_entries")),
              "entry_universe.enabled": _bool(_get(doc, "entry_universe", "enabled")),
              "entry_universe.symbols": ([x for x in syms if isinstance(x, str) and x.strip()]
                                         if isinstance(syms, list) else None)}
    books: dict[str, dict[str, Any]] = {}

    def _book(name: str | None) -> dict[str, Any] | None:
        if not name:
            return None
        return books.setdefault(name, {"name": name, "state_dir": None, "enabled": None, "learning_enabled": None,
                                       "min_stop_pct": None, "starting_equity_usdt": None})

    main = _book("main")
    main["state_dir"] = ""
    main["enabled"] = True
    main["starting_equity_usdt"] = values["risk.starting_equity_usdt"]
    sp = _get(doc, "strategy_paper")
    if isinstance(sp, dict):
        b = _book(_str(sp.get("name")) or "t2_trend_regime")
        b["state_dir"] = _str(sp.get("state_dir"))
        b["enabled"] = _bool(sp.get("enabled"))
        b["starting_equity_usdt"] = _num(sp.get("starting_equity_usdt"))
        b["atr_mult"] = _num(sp.get("atr_mult"))
        b["rule_params"] = _plain(sp.get("rule_params"))
        extra = sp.get("extra")
        for ex in (extra if isinstance(extra, list) else []):
            if not isinstance(ex, dict):
                continue
            eb = _book(_str(ex.get("name")))
            if eb is None:
                continue
            eb["state_dir"] = _str(ex.get("state_dir"))
            en = _bool(ex.get("enabled"))
            eb["enabled"] = True if en is None else en          # `extra` girdisinde enabled yazılmamışsa açık
            eb["starting_equity_usdt"] = _num(ex.get("starting_equity_usdt"))
            eb["rule_min_stop_pct"] = _num(_get(ex, "rule_params", "min_stop_pct"))
            eb["atr_mult"] = _num(ex.get("atr_mult"))
            eb["rule_params"] = _plain(ex.get("rule_params"))
    pt = _get(doc, "pattern_trader")
    if isinstance(pt, dict):
        b = _book("pattern_trader")
        b["state_dir"] = _str(pt.get("state_dir")) or "pattern_trader"
        b["enabled"] = _bool(pt.get("enabled"))
        b["starting_equity_usdt"] = _num(pt.get("starting_equity_usdt"))
    lmb = _get(doc, "learning_mode", "books")
    if isinstance(lmb, dict):
        for name, v in lmb.items():
            if not isinstance(name, str) or not isinstance(v, dict):
                continue
            b = _book(name)
            b["learning_enabled"] = _bool(v.get("enabled"))
            b["min_stop_pct"] = _num(v.get("min_stop_pct"))
    return values, books


def read_raw_config(path: Path | str | None = None) -> RawConfig:
    """Config'i salt-okunur oku. Dosya yoksa/okunamazsa/YAML bozuksa `ok=False` (istisna yok; sha mümkünse yazılır)."""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        with open(p, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return RawConfig(path=str(p), ok=False, error=f"{type(exc).__name__}: {exc}")
    sha = hashlib.sha256(raw).hexdigest()
    try:
        import yaml  # PyYAML (venv'de mevcut; yeni bağımlılık değil)
        doc = yaml.safe_load(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — bozuk YAML motoru durdurmaz; yalnız sha ve hata kaydedilir
        return RawConfig(path=str(p), ok=False, sha256=sha, size=len(raw), error=f"{type(exc).__name__}: {exc}")
    values, books = extract(doc if isinstance(doc, dict) else {})
    return RawConfig(path=str(p), ok=True, sha256=sha, size=len(raw), values=values, books=books)


__all__ = ["DEFAULT_CONFIG_PATH", "NEEDS", "RawConfig", "extract", "read_raw_config"]
