"""İstem (prompt) şablonları — sürümlü, sabit sistem istemi (prompt caching için değişmez), kanıt id'li mesajlar.

Kurallar (sistem isteminde açıkça yazılır):
  * LLM emir VEREMEZ, risk/stop/kaldıraç/boyut DEĞİŞTİREMEZ; sadece araştırma/veto görüşü verir.
  * Yanıt YALNIZCA JSON şemasıdır; serbest metin yok.
  * Kanıta yalnızca verilen `evidence_ids` ile atıf yapılır; uydurma id = geçersiz yanıt.
`redact()` sırlara benzeyen anahtar/değerleri (api_key, secret, token, password, authorization; 32+ hex/base64
diziler) loglanmadan/istem içine girmeden temizler.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .schema import POSTMORTEM_SCHEMA, RESPONSE_SCHEMA

PROMPT_VERSION = "v3.0"

_SECRET_KEY_RE = re.compile(r"(api[_-]?key|secret|token|password|authorization|passphrase|private[_-]?key)", re.I)
_SECRET_VALUE_RE = re.compile(r"(?<![A-Za-z0-9+/=_-])(?:[0-9a-fA-F]{32,}|[A-Za-z0-9+/_-]{32,}={0,2})(?![A-Za-z0-9+/=_-])")
_SK_ANT_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
REDACTED = "[REDACTED]"


def redact(obj: Any, _depth: int = 0) -> Any:
    """Sözlük/liste/metin içinde sırları temizler (iç içe). Orijinali değiştirmez, kopya döndürür."""
    if _depth > 50:
        return REDACTED
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = REDACTED
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v, _depth + 1) for v in obj]
    if isinstance(obj, str):
        s = _SK_ANT_RE.sub(REDACTED, obj)
        return _SECRET_VALUE_RE.sub(REDACTED, s)
    return obj


# --------------------------------------------------------------------------- system prompts (SABİT — cache dostu)
_COMMON_RULES = f"""You are the research analyst inside an automated crypto PAPER-trading system (Binance USDⓈ-M futures and spot).
Prompt version: {PROMPT_VERSION}.

HARD LIMITS (non-negotiable):
1. You CANNOT execute, place, cancel or modify orders. You CANNOT change risk limits, stop losses, take profits, leverage or position size. The deterministic engine owns all of that. Your output is advisory research only; the engine may ignore it.
2. Your only "power" is `veto`: if you see a decisive reason the trade should NOT be opened, set `veto=true` and list `veto_reasons`. A veto can only shrink or block a trade — it can never enlarge one.
3. Answer ONLY with a single JSON object that matches the schema below. No prose, no markdown fences, no comments. Any extra field or missing field makes the answer invalid and it will be discarded.
4. Cite evidence ONLY by the `id` values given in the EVIDENCE section (`evidence_ids`). Never invent ids, prices, dates or news. If evidence is insufficient, say so in `key_uncertainties` and lower `confidence`.
5. `confidence` is 0..1 and reflects how well the evidence supports `decision_support`, not how strongly you feel.
6. `recommended_action` ∈ PROCEED | REDUCE_SIZE | SKIP | WAIT_CONFIRMATION. Prefer WAIT_CONFIRMATION when the picture is mixed.
7. Be adversarial to the trade thesis: list contradictions between indicators/agents explicitly.
"""

RESEARCH_SYSTEM = _COMMON_RULES + "\nRESPONSE JSON SCHEMA:\n" + json.dumps(RESPONSE_SCHEMA, separators=(",", ":"), sort_keys=True)

POSTMORTEM_SYSTEM = f"""You are the post-trade reviewer inside an automated crypto PAPER-trading system. Prompt version: {PROMPT_VERSION}.
You CANNOT change any trading rule, risk limit or model; you only write a structured post-mortem that a deterministic
learning engine will store. Answer ONLY with one JSON object matching the schema. `lesson_codes` are short UPPER_SNAKE
codes (e.g. ENTERED_INTO_RESISTANCE, FUNDING_DRAG, STOP_TOO_TIGHT, TREND_CONFIRMED). Do not invent numbers not present
in the record. Keep `summary` under 60 words.

RESPONSE JSON SCHEMA:
""" + json.dumps(POSTMORTEM_SCHEMA, separators=(",", ":"), sort_keys=True)

COUNCIL_ROLES: dict[str, str] = {
    "bull": "ROLE: You argue the strongest honest BULL case for the proposed trade, but you must still report contradictions and set veto if you find a disqualifying flaw.",
    "bear": "ROLE: You argue the strongest honest BEAR case against the proposed trade (or in favour of the opposite side), and set veto if the trade is clearly unjustified.",
    "skeptic": "ROLE: You are the SKEPTIC. You do not take sides; you audit data quality, look-ahead risk, stale evidence, contradictions between agents and hidden assumptions. Prefer WAIT_CONFIRMATION unless evidence is unusually clean.",
}


def _fmt_evidence(evidence: dict[str, Any]) -> str:
    """Kanıt bloğunu deterministik (sıralı anahtar) JSON olarak yazar; id'ler açıkça listelenir."""
    ev = redact(evidence)
    ids = ev.get("evidence_ids") or ev.get("ids") or []
    lines = ["EVIDENCE (cite only these ids):", "allowed_evidence_ids=" + json.dumps(list(ids), sort_keys=True)]
    lines.append(json.dumps(ev, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
    return "\n".join(lines)


def _fmt_lessons(retrieved: list[dict[str, Any]] | None) -> str:
    if not retrieved:
        return "RETRIEVED_LESSONS: none"
    rows = [json.dumps(redact(r), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str) for r in retrieved[:20]]
    return "RETRIEVED_LESSONS (from the learning store, may be noisy):\n" + "\n".join(rows)


def build_research_prompt(evidence: dict[str, Any], retrieved_lessons: list[dict[str, Any]] | None, mode: str) -> tuple[str, list[dict[str, Any]]]:
    """(system, messages). `mode` ADVISORY|VETO_ONLY|RESEARCH_COUNCIL bilgi amaçlıdır (istem gövdesine yazılır)."""
    user = "\n\n".join([
        f"MODE={mode}. Task: evaluate the candidate described in EVIDENCE and answer with the JSON schema only.",
        _fmt_evidence(evidence),
        _fmt_lessons(retrieved_lessons),
        "Return the JSON object now.",
    ])
    return RESEARCH_SYSTEM, [{"role": "user", "content": user}]


def build_postmortem_prompt(record: dict[str, Any], postmortem_dict: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """(system, messages) — kapanmış işlem kaydı + deterministik otopsi (L2) → LLM özet/ders kodları."""
    parts = [
        "Task: write the structured post-mortem JSON for this closed PAPER trade.",
        "TRADE_RECORD:\n" + json.dumps(redact(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str),
    ]
    if postmortem_dict:
        parts.append("DETERMINISTIC_POSTMORTEM (computed by the engine, trust these numbers):\n"
                     + json.dumps(redact(postmortem_dict), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
    parts.append("Return the JSON object now.")
    return POSTMORTEM_SYSTEM, [{"role": "user", "content": "\n\n".join(parts)}]


def build_council_prompts(evidence: dict[str, Any], retrieved: list[dict[str, Any]] | None) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    """bull/bear/skeptic için (system, messages). Sistem istemi sabittir; rol kullanıcı mesajının başında verilir
    (prompt cache'i bozmamak için)."""
    out: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for role, text in COUNCIL_ROLES.items():
        system, msgs = build_research_prompt(evidence, retrieved, mode=f"RESEARCH_COUNCIL/{role}")
        msgs = [{"role": "user", "content": text + "\n\n" + msgs[0]["content"]}]
        out[role] = (system, msgs)
    return out


def repair_message(validator_error: str, previous_text: str) -> list[dict[str, Any]]:
    """Şema hatası sonrası tek yeniden deneme için eklenecek mesajlar (asistan yanıtı + hata geri bildirimi)."""
    return [
        {"role": "assistant", "content": (previous_text or "")[:4000] or "{}"},
        {"role": "user", "content": "Your previous answer was rejected by the JSON schema validator: "
                                    f"{validator_error[:500]}\nReturn ONLY a corrected JSON object that satisfies the schema."},
    ]


__all__ = ["PROMPT_VERSION", "RESEARCH_SYSTEM", "POSTMORTEM_SYSTEM", "COUNCIL_ROLES", "build_research_prompt",
           "build_postmortem_prompt", "build_council_prompts", "repair_message", "redact", "REDACTED"]
