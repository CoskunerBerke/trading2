"""LLM yanıt şemaları + katı doğrulama (fail-closed).

* `RESPONSE_SCHEMA`   : araştırma/danışma yanıtı (decision_support, veto, evidence_ids ...)
* `POSTMORTEM_SCHEMA` : kapanmış işlem otopsisi (summary, lesson_codes ...)
* `validate_response` / `validate_postmortem`: kendi doğrulayıcımız; `jsonschema` kuruluysa
  ek olarak onunla da doğrulanır (isteğe bağlı bağımlılık, tembel import).

Şemaya uymayan her şey `LLMSchemaError` fırlatır — servis bunu yakalayıp güvenli (NEUTRAL /
WAIT_CONFIRMATION) tavsiyeye çevirir. LLM hiçbir zaman emir açamaz, risk/stop/kaldıraç
değiştiremez; sadece `veto` ile küçültebilir/durdurabilir.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core import LLMSchemaError

DECISION_SUPPORT = ("SUPPORT", "NEUTRAL", "OPPOSE")
RECOMMENDED_ACTION = ("PROCEED", "REDUCE_SIZE", "SKIP", "WAIT_CONFIRMATION")

_STR_LIST = {"type": "array", "items": {"type": "string"}}

RESPONSE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "LLMResearchAdvice",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision_support", "bull_case", "bear_case", "key_uncertainties", "contradictions",
        "veto", "veto_reasons", "confidence", "evidence_ids", "historical_lessons", "recommended_action",
    ],
    "properties": {
        "decision_support": {"type": "string", "enum": list(DECISION_SUPPORT)},
        "bull_case": _STR_LIST,
        "bear_case": _STR_LIST,
        "key_uncertainties": _STR_LIST,
        "contradictions": _STR_LIST,
        "veto": {"type": "boolean"},
        "veto_reasons": _STR_LIST,
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_ids": _STR_LIST,
        "historical_lessons": _STR_LIST,
        "recommended_action": {"type": "string", "enum": list(RECOMMENDED_ACTION)},
    },
}

POSTMORTEM_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "LLMPostmortem",
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "what_was_right", "what_was_wrong", "lesson_codes", "hypotheses"],
    "properties": {
        "summary": {"type": "string"},
        "what_was_right": _STR_LIST,
        "what_was_wrong": _STR_LIST,
        "lesson_codes": _STR_LIST,
        "hypotheses": _STR_LIST,
    },
}

_LESSON_CODE_RE = re.compile(r"^[A-Z0-9_]{2,64}$")


@dataclass
class LLMAdvice:
    """Doğrulanmış LLM tavsiyesi. `failed=True` ise içerik güvenli varsayılanlardır (fail-closed)."""

    decision_support: str = "NEUTRAL"
    bull_case: list[str] = field(default_factory=list)
    bear_case: list[str] = field(default_factory=list)
    key_uncertainties: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    veto: bool = False
    veto_reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    historical_lessons: list[str] = field(default_factory=list)
    recommended_action: str = "WAIT_CONFIRMATION"
    # meta
    failed: bool = False
    schema_invalid: bool = False
    provider: str = ""
    model: str = ""
    failure_kind: str | None = None
    prompt_hash: str | None = None
    output_hash: str | None = None
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def allows_open(self) -> bool:
        """Sadece bilgi: bu tavsiye pozisyon açmaya 'engel değil' mi? Emir kararı asla burada verilmez."""
        return (not self.failed) and (not self.veto) and self.recommended_action in ("PROCEED", "REDUCE_SIZE")

    @classmethod
    def failed_closed(cls, provider: str = "", model: str = "", failure_kind: str = "unknown",
                      schema_invalid: bool = False) -> "LLMAdvice":
        """Herhangi bir hata → NEUTRAL, veto False, WAIT_CONFIRMATION, confidence 0. Asla büyütmez/açmaz."""
        return cls(decision_support="NEUTRAL", veto=False, confidence=0.0, recommended_action="WAIT_CONFIRMATION",
                   failed=True, schema_invalid=schema_invalid, provider=provider, model=model,
                   failure_kind=failure_kind)


# ------------------------------------------------------------------ own validator
def _type_ok(value: Any, typ: str) -> bool:
    if typ == "string":
        return isinstance(value, str)
    if typ == "boolean":
        return isinstance(value, bool)
    if typ == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if typ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "array":
        return isinstance(value, list)
    if typ == "object":
        return isinstance(value, dict)
    if typ == "null":
        return value is None
    return True


def _check(value: Any, schema: dict, path: str, errors: list[str]) -> None:
    typ = schema.get("type")
    if typ is not None and not _type_ok(value, typ):
        errors.append(f"{path}: expected {typ}, got {type(value).__name__}")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if typ in ("number", "integer"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum {schema['maximum']}")
        if value != value:  # NaN
            errors.append(f"{path}: NaN not allowed")
    if typ == "array":
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value):
                _check(item, item_schema, f"{path}[{i}]", errors)
    if typ == "object":
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required '{req}'")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value.keys()) - set(props.keys()))
            if extra:
                errors.append(f"{path}: additional properties not allowed: {extra}")
        for k, sub in props.items():
            if k in value:
                _check(value[k], sub, f"{path}.{k}", errors)


def validate_against(obj: Any, schema: dict) -> list[str]:
    """Kendi doğrulayıcımızla hata listesi döndürür (boş liste = geçerli). `jsonschema` varsa onunla da dener."""
    errors: list[str] = []
    _check(obj, schema, "$", errors)
    if not errors:
        try:  # isteğe bağlı çapraz kontrol
            import jsonschema  # type: ignore
        except ImportError:
            jsonschema = None  # type: ignore
        if jsonschema is not None:
            try:
                jsonschema.validate(obj, schema)  # type: ignore[attr-defined]
            except Exception as exc:  # jsonschema.ValidationError / SchemaError
                errors.append(f"jsonschema: {getattr(exc, 'message', str(exc))[:200]}")
    return errors


def validate_response(obj: Any, allowed_evidence_ids: list[str] | set[str] | None = None) -> LLMAdvice:
    """Ham nesneyi (dict) doğrula → `LLMAdvice`. Hatalı ise `LLMSchemaError`.

    * `allowed_evidence_ids` verilmişse, yanıt yalnızca bu id'lere atıf yapabilir (uydurma kanıt = hata).
    * Tekrarlı id'ler tekilleştirilir; boş string id reddedilir.
    """
    if not isinstance(obj, dict):
        raise LLMSchemaError(f"response must be a JSON object, got {type(obj).__name__}")
    errors = validate_against(obj, RESPONSE_SCHEMA)
    if errors:
        raise LLMSchemaError("; ".join(errors[:8]))
    ids: list[str] = []
    for eid in obj["evidence_ids"]:
        if not eid.strip():
            raise LLMSchemaError("evidence_ids: empty id")
        if eid not in ids:
            ids.append(eid)
    if allowed_evidence_ids is not None:
        allowed = set(allowed_evidence_ids)
        bad = [e for e in ids if e not in allowed]
        if bad:
            raise LLMSchemaError(f"evidence_ids not allowed: {bad[:5]}")
    if obj["veto"] and not obj["veto_reasons"]:
        raise LLMSchemaError("veto=true requires at least one veto_reason")
    return LLMAdvice(
        decision_support=obj["decision_support"],
        bull_case=list(obj["bull_case"]),
        bear_case=list(obj["bear_case"]),
        key_uncertainties=list(obj["key_uncertainties"]),
        contradictions=list(obj["contradictions"]),
        veto=bool(obj["veto"]),
        veto_reasons=list(obj["veto_reasons"]),
        confidence=float(obj["confidence"]),
        evidence_ids=ids,
        historical_lessons=list(obj["historical_lessons"]),
        recommended_action=obj["recommended_action"],
    )


def validate_postmortem(obj: Any) -> dict[str, Any]:
    """Otopsi yanıtını doğrula → normalize dict. `lesson_codes` büyük harf/`_` biçimine zorlanır."""
    if not isinstance(obj, dict):
        raise LLMSchemaError(f"postmortem must be a JSON object, got {type(obj).__name__}")
    errors = validate_against(obj, POSTMORTEM_SCHEMA)
    if errors:
        raise LLMSchemaError("; ".join(errors[:8]))
    codes: list[str] = []
    for c in obj["lesson_codes"]:
        norm = re.sub(r"[^A-Z0-9_]", "_", c.strip().upper())
        if not _LESSON_CODE_RE.match(norm):
            raise LLMSchemaError(f"lesson_codes: invalid code {c!r}")
        if norm not in codes:
            codes.append(norm)
    return {
        "summary": obj["summary"].strip(),
        "what_was_right": list(obj["what_was_right"]),
        "what_was_wrong": list(obj["what_was_wrong"]),
        "lesson_codes": codes,
        "hypotheses": list(obj["hypotheses"]),
    }


__all__ = ["RESPONSE_SCHEMA", "POSTMORTEM_SCHEMA", "LLMAdvice", "validate_response", "validate_postmortem",
           "validate_against", "DECISION_SUPPORT", "RECOMMENDED_ACTION"]
