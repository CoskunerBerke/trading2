"""Loglama kurulumu — JSON satırları, döner dosya (20MB×5) ve gizli bilgi maskeleme.

`setup_logging(level, json_lines, log_dir, run_id)` kök logger'ı yapılandırır. Ortamdaki bilinen gizli değerler
(API anahtarları, tokenlar) ve `key=value` biçimli sırlar log satırlarında `***` ile maskelenir.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_BYTES = 20 * 1024 * 1024
BACKUP_COUNT = 5

# ortam değişkeni adları — değerleri loglarda asla görünmemeli
_SECRET_ENV_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "WEBHOOK")
# key=value / key: value / "key": "value" biçimli sırlar
_KV_RE = re.compile(
    r"(?i)((?:api[_-]?key|secret(?:[_-]?key)?|token|password|passwd|authorization|bearer|webhook(?:[_-]?url)?)"
    r"[\"']?\s*[=:]\s*[\"']?)([^\s\"',;&]{4,})"
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9\-._~+/]{8,}=*)")
_ANTHROPIC_RE = re.compile(r"sk-ant-[A-Za-z0-9\-_]{8,}")


def _env_secret_values() -> list[str]:
    vals: list[str] = []
    for k, v in os.environ.items():
        ku = k.upper()
        if any(h in ku for h in _SECRET_ENV_HINTS) and v and len(v) >= 6:
            vals.append(v)
    # uzun değerler önce (kısa bir sırrın uzun bir sırrın içinde geçmesi durumunda)
    return sorted(set(vals), key=len, reverse=True)


class RedactionFilter(logging.Filter):
    """Log mesajı ve argümanlardaki sırları `***` yapar. Ortam sırları başlangıçta okunur; `refresh()` ile yenilenir."""

    def __init__(self, extra_secrets: list[str] | None = None, name: str = "") -> None:
        super().__init__(name)
        self._extra = [s for s in (extra_secrets or []) if s]
        self.refresh()

    def refresh(self) -> None:
        self._secrets = sorted(set(self._extra + _env_secret_values()), key=len, reverse=True)

    def redact(self, text: str) -> str:
        if not text:
            return text
        for s in self._secrets:
            if s in text:
                text = text.replace(s, "***")
        text = _ANTHROPIC_RE.sub("sk-ant-***", text)
        text = _BEARER_RE.sub(r"\1***", text)
        text = _KV_RE.sub(r"\1***", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except (TypeError, ValueError):
            msg = str(record.msg)
        record.msg = self.redact(msg)
        record.args = ()
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True


_STD_ATTRS = set(vars(logging.LogRecord("x", 0, "x", 0, "", (), None)).keys()) | {"message", "asctime"}


class JsonLineFormatter(logging.Formatter):
    """Her kayıt tek satır JSON: ts (UTC ISO), level, logger, msg, run_id, ekstra alanlar, exc."""

    def __init__(self, run_id: str | None = None) -> None:
        super().__init__()
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        d: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        run_id = getattr(record, "run_id", None) or self.run_id
        if run_id:
            d["run_id"] = run_id
        for k, v in record.__dict__.items():
            if k in _STD_ATTRS or k.startswith("_") or k == "run_id":
                continue
            try:
                json.dumps(v)
                d[k] = v
            except (TypeError, ValueError):
                d[k] = str(v)
        if record.exc_info:
            d["exc"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        d["pid"] = record.process
        return json.dumps(d, ensure_ascii=False, default=str)


class _RunIdFilter(logging.Filter):
    def __init__(self, run_id: str | None) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        if self.run_id and not getattr(record, "run_id", None):
            record.run_id = self.run_id
        return True


def setup_logging(level: str | int = "INFO", json_lines: bool = True, log_dir: Path | str | None = None,
                  run_id: str | None = None, *, filename: str = "tradingbot.log", stream=None,
                  extra_secrets: list[str] | None = None) -> logging.Logger:
    """Kök logger'ı kurar (mevcut handler'ları kaldırır). Döner dosya: `log_dir/filename` 20MB×5.

    Dönen: kök logger. Handler'lar `_tradingbot_handler=True` ile işaretlenir (tekrar çağrıda temizlenir)."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_tradingbot_handler", False):
            root.removeHandler(h)
            try:
                h.close()
            except OSError:
                pass
    lvl = logging.getLevelName(level.upper()) if isinstance(level, str) else level
    if not isinstance(lvl, int):
        lvl = logging.INFO
    root.setLevel(lvl)
    redact = RedactionFilter(extra_secrets)
    run_filter = _RunIdFilter(run_id)
    fmt: logging.Formatter = JsonLineFormatter(run_id) if json_lines else logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    handlers: list[logging.Handler] = []
    sh = logging.StreamHandler(stream or sys.stderr)
    handlers.append(sh)
    if log_dir:
        p = Path(log_dir)
        p.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(p / filename, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
        handlers.append(fh)
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(run_filter)
        h.addFilter(redact)
        h._tradingbot_handler = True  # type: ignore[attr-defined]
        root.addHandler(h)
    # gürültülü kütüphaneler
    for noisy in ("urllib3", "httpx", "matplotlib", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(max(lvl, logging.WARNING))
    return root


__all__ = ["setup_logging", "RedactionFilter", "JsonLineFormatter", "MAX_BYTES", "BACKUP_COUNT"]
