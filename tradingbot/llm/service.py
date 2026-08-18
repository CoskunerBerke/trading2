"""LLMResearchService — mod kapıları, bütçe, önbellek, devre kesici, doğrulama, tek yeniden deneme, günlükleme.

Tasarım gereği bu servis ledger/gateway/config referansı TUTMAZ: girdi bir `snapshot` dict + izinli kanıt id'leri,
çıktı `LLMAdvice` (veya None). Emir/riske dokunmak mimari olarak imkânsızdır.

Fail-closed: her hata (bütçe, kesici, sağlayıcı, şema, 2. deneme) → `LLMAdvice.failed_closed(...)`:
    failed=True, veto=False, decision_support=NEUTRAL, recommended_action=WAIT_CONFIRMATION, confidence=0
Bu tavsiye pozisyon AÇMAZ/BÜYÜTMEZ; motor bunu "LLM görüşü yok" gibi ele almalı.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ..core import LLMSchemaError, iso, payload_hash, stable_id, utc_now
from .budget import CircuitBreaker, LLMBudget, PriceTable, SemanticCache
from .prompts import PROMPT_VERSION, build_council_prompts, build_postmortem_prompt, build_research_prompt, redact, repair_message
from .provider import (MODEL_CHEAP, MODEL_STRONG, LLMProvider, LLMProviderError, LLMRequest, LLMResponse,
                       extract_first_json_object)
from .schema import LLMAdvice, validate_postmortem, validate_response


class LLMMode(str, Enum):
    OFF = "OFF"                          # hiç çağrı yok
    POSTMORTEM_ONLY = "POSTMORTEM_ONLY"  # yalnız kapanmış işlem otopsisi (gece/batch)
    ADVISORY = "ADVISORY"                # tam tavsiye (bilgi amaçlı, veto dahil)
    VETO_ONLY = "VETO_ONLY"              # sadece veto alanı anlamlı
    RESEARCH_COUNCIL = "RESEARCH_COUNCIL"  # bull/bear/skeptic üçlüsü + birleştirme

    @classmethod
    def parse(cls, value: "str | LLMMode | None") -> "LLMMode":
        if isinstance(value, cls):
            return value
        if not value:
            return cls.OFF
        return cls(str(value).strip().upper())


@dataclass
class LLMConfig:
    """Lead'in config.yaml'dan dolduracağı ayarlar (varsayılanlar güvenli/ucuz)."""
    mode: str = "OFF"
    model_cheap: str = MODEL_CHEAP
    model_strong: str = MODEL_STRONG
    daily_usd: float = 2.0
    daily_tokens: int = 400_000
    per_tour_candidates: int = 3
    max_output_tokens: int = 1024
    cache_ttl_s: float = 3600.0
    breaker_failures: int = 3
    breaker_cooldown_s: float = 900.0
    max_retries: int = 1
    budget_path: str = "state/llm_budget.json"
    cache_path: str = "state/llm_cache.json"
    calls_path: str = "state/llm_calls.jsonl"

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class LLMResearchService:
    """Sağlayıcıyı çağıran tek yer. `advise` / `postmortem` / `council`."""

    def __init__(self, provider: LLMProvider, budget: LLMBudget, cache: SemanticCache | None = None,
                 breaker: CircuitBreaker | None = None, mode: "LLMMode | str" = LLMMode.ADVISORY,
                 log_sink: Callable[[dict[str, Any]], None] | None = None, model_cheap: str = MODEL_CHEAP,
                 model_strong: str = MODEL_STRONG, max_retries: int = 1, prices: PriceTable | None = None,
                 calls_path: Path | str | None = None, clock: Callable[[], float] = time.time) -> None:
        self.provider = provider
        self.budget = budget
        self.cache = cache
        self.breaker = breaker or CircuitBreaker()
        self.mode = LLMMode.parse(mode)
        self.log_sink = log_sink
        self.model_cheap = model_cheap
        self.model_strong = model_strong
        self.max_retries = max(0, int(max_retries))
        self.prices = prices or PriceTable()
        self.calls_path = Path(calls_path) if calls_path else None
        self._clock = clock
        self.calls: list[dict[str, Any]] = []   # son çağrı kayıtları (bellek, test/inceleme)

    # ------------------------------------------------------------------ helpers
    def _model_for(self, tier: str) -> str:
        return self.model_cheap if tier == "cheap" else self.model_strong

    def _log(self, row: dict[str, Any]) -> None:
        row = dict(row)
        row.setdefault("ts", iso(utc_now()))
        row.setdefault("provider", getattr(self.provider, "name", "unknown"))
        row.setdefault("mode", self.mode.value)
        row.setdefault("prompt_version", PROMPT_VERSION)
        row["request_redacted"] = redact(row.get("request_redacted"))
        self.calls.append(row)
        if len(self.calls) > 200:
            del self.calls[:-200]
        if self.log_sink is not None:
            try:
                self.log_sink(row)
            except Exception:  # log sink asla akışı bozmasın
                pass
        if self.calls_path is not None:
            try:
                self.calls_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.calls_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass

    def _base_row(self, model: str, snapshot_id: str, prompt_hash: str, req: LLMRequest | None) -> dict[str, Any]:
        return {
            "provider": getattr(self.provider, "name", "unknown"), "model": model, "mode": self.mode.value,
            "prompt_version": PROMPT_VERSION, "prompt_hash": prompt_hash, "snapshot_id": snapshot_id,
            "output_hash": None, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "est_cost_usd": 0.0, "latency_ms": 0.0, "cache_hit": False, "failure_kind": None, "retries": 0,
            "request_redacted": {"system_len": len(req.system), "messages": req.messages, "max_tokens": req.max_tokens} if req else None,
        }

    def _call_with_validation(self, req: LLMRequest, validator: Callable[[Any], Any], row: dict[str, Any],
                              tour_id: str | None) -> tuple[Any | None, str | None]:
        """Sağlayıcıyı çağır, doğrula, şema hatasında `max_retries` kez düzeltme iste. (obj|None, failure_kind|None)."""
        est_in = _est_tokens(req.system) + sum(_est_tokens(str(m.get("content", ""))) for m in req.messages)
        est_out = req.max_tokens
        est_cost = self.prices.estimate(req.model, est_in, est_out)
        ok, reason = self.budget.can_spend(est_in + est_out, est_cost, tour_id=tour_id)
        if not ok:
            row["failure_kind"] = f"budget:{reason}"
            return None, row["failure_kind"]
        if not self.breaker.allow():
            row["failure_kind"] = "breaker_open"
            return None, "breaker_open"

        attempts = 0
        last_err = ""
        cur_req = req
        while True:
            attempts += 1
            try:
                resp: LLMResponse = self.provider.complete(cur_req)
            except LLMProviderError as exc:
                self.breaker.record_failure()
                row["failure_kind"] = f"provider:{type(exc).__name__}"
                row["retries"] = attempts - 1
                return None, row["failure_kind"]
            except Exception as exc:  # sağlayıcı bilinmeyen hata → yine fail-closed, ama kesiciye say
                self.breaker.record_failure()
                row["failure_kind"] = f"provider:{type(exc).__name__}"
                row["retries"] = attempts - 1
                return None, row["failure_kind"]
            usage = {"input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens, "cache_read_tokens": resp.cache_read_tokens}
            cost = self.prices.estimate(resp.model or req.model, resp.input_tokens, resp.output_tokens, resp.cache_read_tokens)
            self.budget.record(usage, cost, tour_id=tour_id)
            row["input_tokens"] += resp.input_tokens
            row["output_tokens"] += resp.output_tokens
            row["cache_read_tokens"] += resp.cache_read_tokens
            row["est_cost_usd"] += cost
            row["latency_ms"] += resp.latency_ms
            row["model"] = resp.model or req.model
            row["retries"] = attempts - 1
            try:
                obj = resp.parsed if resp.parsed is not None else extract_first_json_object(resp.text)
                validated = validator(obj)
            except (LLMSchemaError, ValueError, TypeError) as exc:
                last_err = str(exc)
                if attempts <= self.max_retries:
                    cur_req = LLMRequest(system=req.system, messages=list(req.messages) + repair_message(last_err, resp.text),
                                         schema=req.schema, max_tokens=req.max_tokens, model=req.model,
                                         temperature=req.temperature, metadata=req.metadata)
                    continue
                self.breaker.record_failure()
                row["failure_kind"] = "schema_invalid"
                row["output_hash"] = payload_hash(resp.text)
                return None, "schema_invalid"
            self.breaker.record_success()
            row["output_hash"] = payload_hash(obj)
            return validated, None

    # ------------------------------------------------------------------ advise
    def advise(self, snapshot: dict[str, Any], evidence_ids: list[str], retrieved: list[dict[str, Any]] | None = None,
               tier: str = "strong", tour_id: str | None = None) -> LLMAdvice | None:
        """Mod OFF/POSTMORTEM_ONLY → None. Aksi halde LLMAdvice (başarısızsa failed_closed)."""
        if self.mode in (LLMMode.OFF, LLMMode.POSTMORTEM_ONLY):
            return None
        model = self._model_for(tier)
        evidence = dict(snapshot)
        evidence["evidence_ids"] = list(evidence_ids)
        system, messages = build_research_prompt(evidence, retrieved, mode=self.mode.value)
        req = LLMRequest(system=system, messages=messages, schema=None, max_tokens=self.budget.max_output_tokens, model=model,
                         metadata={"kind": "advise", "tier": tier})
        prompt_hash = payload_hash({"s": system, "m": messages, "model": model, "v": PROMPT_VERSION})
        snapshot_id = str(snapshot.get("snapshot_id") or stable_id(payload_hash(snapshot)))
        row = self._base_row(model, snapshot_id, prompt_hash, req)

        cache_key = SemanticCache.key_for(snapshot, sorted(evidence_ids), model, PROMPT_VERSION, self.mode.value) if self.cache is not None else None
        if self.cache is not None and cache_key is not None:
            hit = self.cache.get(cache_key)
            if isinstance(hit, dict):
                try:
                    adv = validate_response(hit, list(evidence_ids))
                except LLMSchemaError:
                    adv = None
                if adv is not None:
                    adv = self._finish_advice(adv, model, prompt_hash, cache_hit=True)
                    row.update({"cache_hit": True, "output_hash": payload_hash(hit)})
                    self._log(row)
                    return adv

        allowed = list(evidence_ids)
        validated, failure = self._call_with_validation(req, lambda o: validate_response(o, allowed), row, tour_id)
        if validated is None:
            adv = LLMAdvice.failed_closed(provider=getattr(self.provider, "name", ""), model=model,
                                          failure_kind=failure or "unknown", schema_invalid=(failure == "schema_invalid"))
            adv.prompt_hash = prompt_hash
            self._log(row)
            return adv
        adv = self._finish_advice(validated, model, prompt_hash, cache_hit=False)
        adv.output_hash = row.get("output_hash")
        if self.cache is not None and cache_key is not None:
            self.cache.put(cache_key, _advice_payload(adv))
        self._log(row)
        return adv

    def _finish_advice(self, adv: LLMAdvice, model: str, prompt_hash: str, cache_hit: bool) -> LLMAdvice:
        adv.provider = getattr(self.provider, "name", "")
        adv.model = model
        adv.prompt_hash = prompt_hash
        adv.cache_hit = cache_hit
        if self.mode == LLMMode.VETO_ONLY:
            # sadece veto anlamlı: veto varsa SKIP, yoksa WAIT_CONFIRMATION (asla PROCEED üretmez)
            adv.recommended_action = "SKIP" if adv.veto else "WAIT_CONFIRMATION"
            adv.decision_support = "OPPOSE" if adv.veto else "NEUTRAL"
        return adv

    # ------------------------------------------------------------------ postmortem
    def postmortem(self, record: dict[str, Any], pm: dict[str, Any] | None = None, tier: str = "cheap",
                   tour_id: str | None = None) -> dict[str, Any] | None:
        """POSTMORTEM_ONLY ve üstü modlarda çalışır; OFF → None. Başarısızlık → None (otopsi tavsiye değildir)."""
        if self.mode == LLMMode.OFF:
            return None
        model = self._model_for(tier)
        system, messages = build_postmortem_prompt(record, pm)
        req = LLMRequest(system=system, messages=messages, max_tokens=self.budget.max_output_tokens, model=model,
                         metadata={"kind": "postmortem"})
        prompt_hash = payload_hash({"s": system, "m": messages, "model": model, "v": PROMPT_VERSION})
        snapshot_id = str(record.get("trade_id") or record.get("id") or stable_id(payload_hash(record)))
        row = self._base_row(model, snapshot_id, prompt_hash, req)
        cache_key = SemanticCache.key_for(record, pm, model, PROMPT_VERSION, "postmortem") if self.cache is not None else None
        if self.cache is not None and cache_key is not None:
            hit = self.cache.get(cache_key)
            if isinstance(hit, dict):
                row.update({"cache_hit": True, "output_hash": payload_hash(hit)})
                self._log(row)
                return dict(hit)
        validated, _ = self._call_with_validation(req, validate_postmortem, row, tour_id)
        self._log(row)
        if validated is None:
            return None
        if self.cache is not None and cache_key is not None:
            self.cache.put(cache_key, validated)
        return validated

    # ------------------------------------------------------------------ council
    def council(self, snapshot: dict[str, Any], evidence_ids: list[str], retrieved: list[dict[str, Any]] | None = None,
                tour_id: str | None = None) -> LLMAdvice | None:
        """RESEARCH_COUNCIL: bull/bear/skeptic üç çağrı → birleştirilmiş tavsiye. Diğer modlarda `advise` ile aynı."""
        if self.mode in (LLMMode.OFF, LLMMode.POSTMORTEM_ONLY):
            return None
        if self.mode != LLMMode.RESEARCH_COUNCIL:
            return self.advise(snapshot, evidence_ids, retrieved, tour_id=tour_id)
        model = self.model_strong
        evidence = dict(snapshot)
        evidence["evidence_ids"] = list(evidence_ids)
        prompts = build_council_prompts(evidence, retrieved)
        allowed = list(evidence_ids)
        snapshot_id = str(snapshot.get("snapshot_id") or stable_id(payload_hash(snapshot)))
        members: dict[str, LLMAdvice] = {}
        failures: list[str] = []
        for role, (system, messages) in prompts.items():
            req = LLMRequest(system=system, messages=messages, max_tokens=self.budget.max_output_tokens, model=model,
                             metadata={"kind": "council", "role": role})
            prompt_hash = payload_hash({"s": system, "m": messages, "model": model, "v": PROMPT_VERSION, "role": role})
            row = self._base_row(model, snapshot_id, prompt_hash, req)
            row["role"] = role
            validated, failure = self._call_with_validation(req, lambda o: validate_response(o, allowed), row, tour_id)
            self._log(row)
            if validated is None:
                failures.append(f"{role}:{failure}")
                continue
            members[role] = validated
        if not members:
            adv = LLMAdvice.failed_closed(provider=getattr(self.provider, "name", ""), model=model,
                                          failure_kind="council:" + ",".join(failures))
            return adv
        return merge_council(members, provider=getattr(self.provider, "name", ""), model=model, failures=failures)


def _advice_payload(adv: LLMAdvice) -> dict[str, Any]:
    """Önbelleğe yazılacak saf şema nesnesi (meta alanlar hariç)."""
    return {
        "decision_support": adv.decision_support, "bull_case": adv.bull_case, "bear_case": adv.bear_case,
        "key_uncertainties": adv.key_uncertainties, "contradictions": adv.contradictions, "veto": adv.veto,
        "veto_reasons": adv.veto_reasons, "confidence": adv.confidence, "evidence_ids": adv.evidence_ids,
        "historical_lessons": adv.historical_lessons, "recommended_action": adv.recommended_action,
    }


_ACTION_RANK = {"SKIP": 0, "WAIT_CONFIRMATION": 1, "REDUCE_SIZE": 2, "PROCEED": 3}
_SUPPORT_SCORE = {"SUPPORT": 1.0, "NEUTRAL": 0.0, "OPPOSE": -1.0}


def merge_council(members: dict[str, LLMAdvice], provider: str = "", model: str = "", failures: list[str] | None = None) -> LLMAdvice:
    """Muhafazakâr birleştirme: herhangi bir veto → veto; aksiyon = üyelerin EN TEMKİNLİSİ; destek = güven ağırlıklı
    ortalama işareti; confidence = min(üyeler) × (üye sayısı/3)."""
    if not members:
        return LLMAdvice.failed_closed(provider=provider, model=model, failure_kind="council:empty")
    veto = any(m.veto for m in members.values())
    veto_reasons: list[str] = []
    for role, m in members.items():
        veto_reasons += [f"[{role}] {r}" for r in m.veto_reasons]
    action = min((m.recommended_action for m in members.values()), key=lambda a: _ACTION_RANK.get(a, 0))
    if veto:
        action = "SKIP"
    wsum = sum(_SUPPORT_SCORE.get(m.decision_support, 0.0) * max(m.confidence, 1e-9) for m in members.values())
    support = "SUPPORT" if wsum > 0.25 else ("OPPOSE" if wsum < -0.25 else "NEUTRAL")
    conf = min(m.confidence for m in members.values()) * (len(members) / 3.0)
    if len(members) < 3:
        action = min(action, "WAIT_CONFIRMATION", key=lambda a: _ACTION_RANK.get(a, 0))
    def _cat(attr: str) -> list[str]:
        out: list[str] = []
        for role, m in members.items():
            out += [f"[{role}] {x}" for x in getattr(m, attr)]
        return out
    ids: list[str] = []
    for m in members.values():
        for e in m.evidence_ids:
            if e not in ids:
                ids.append(e)
    adv = LLMAdvice(decision_support=support, bull_case=_cat("bull_case"), bear_case=_cat("bear_case"),
                    key_uncertainties=_cat("key_uncertainties") + ([f"council_failures={failures}"] if failures else []),
                    contradictions=_cat("contradictions"), veto=veto, veto_reasons=veto_reasons,
                    confidence=round(min(1.0, max(0.0, conf)), 4), evidence_ids=ids,
                    historical_lessons=_cat("historical_lessons"), recommended_action=action, provider=provider, model=model)
    return adv


__all__ = ["LLMMode", "LLMConfig", "LLMResearchService", "merge_council"]
