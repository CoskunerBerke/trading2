"""LLM araştırma katmanı (fail-closed, PAPER, ağsız varsayılan).

Kullanım (lead entegrasyonu):
    from tradingbot.llm import LLMResearchService, LLMMode, LLMBudget, NoOpLLMProvider, AnthropicProvider
    budget = LLMBudget("state/llm_budget.json", daily_usd=2.0)
    svc = LLMResearchService(NoOpLLMProvider(), budget, mode=LLMMode.OFF)   # varsayılan: kapalı
    advice = svc.advise(snapshot, evidence_ids, retrieved)   # None (OFF) ya da LLMAdvice
LLM asla emir vermez; `advice.veto` yalnızca küçültür/durdurur.
"""
from .budget import DEFAULT_PRICES, CircuitBreaker, LLMBudget, PriceTable, SemanticCache, estimate_cost
from .prompts import PROMPT_VERSION, build_council_prompts, build_postmortem_prompt, build_research_prompt, redact
from .provider import (MODEL_CHEAP, MODEL_FRONTIER, MODEL_STRONG, AnthropicProvider, BatchStub, FakeProvider, LLMAuthError,
                       LLMProvider, LLMProviderError, LLMRequest, LLMResponse, NoOpLLMProvider, extract_first_json_object)
from .schema import POSTMORTEM_SCHEMA, RESPONSE_SCHEMA, LLMAdvice, validate_postmortem, validate_response
from .service import LLMConfig, LLMMode, LLMResearchService, merge_council

__all__ = [
    "DEFAULT_PRICES", "CircuitBreaker", "LLMBudget", "PriceTable", "SemanticCache", "estimate_cost",
    "PROMPT_VERSION", "build_council_prompts", "build_postmortem_prompt", "build_research_prompt", "redact",
    "MODEL_CHEAP", "MODEL_FRONTIER", "MODEL_STRONG", "AnthropicProvider", "BatchStub", "FakeProvider", "LLMAuthError",
    "LLMProvider", "LLMProviderError", "LLMRequest", "LLMResponse", "NoOpLLMProvider", "extract_first_json_object",
    "POSTMORTEM_SCHEMA", "RESPONSE_SCHEMA", "LLMAdvice", "validate_postmortem", "validate_response",
    "LLMConfig", "LLMMode", "LLMResearchService", "merge_council",
]
