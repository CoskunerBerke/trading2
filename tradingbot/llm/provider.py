"""LLM sağlayıcı soyutlaması.

* `LLMProvider` (Protocol): `complete(req: LLMRequest) -> LLMResponse`
* `NoOpLLMProvider`     : ağsız, sabit NEUTRAL/WAIT_CONFIRMATION yanıt (varsayılan, fail-closed)
* `FakeProvider`        : testler için sıralı yanıt kuyruğu (str | dict | Exception)
* `AnthropicProvider`   : `anthropic` SDK'sını TEMBEL import eder; API anahtarını YALNIZCA `complete()`
                          içinde ortamdan okur, hiçbir yere yazmaz/loglamaz. Anahtar yoksa ağa çıkmadan
                          açık hata verir.
* `BatchStub`           : gece toplu (Message Batches) çağrı için tasarım notu + arayüz iskeleti (ağsız).

Model kimlikleri/fiyatlar `budget.py` içindeki tabloda; burada yalnızca varsayılan id sabitleri var.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..core import TradingBotError

# Model kimlikleri — kaynak: claude-api skill model tablosu (cache 2026-06-24). Fiyatlar budget.PriceTable'da.
MODEL_CHEAP = "claude-haiku-4-5-20251001"     # ucuz/hızlı; alias: claude-haiku-4-5
MODEL_STRONG = "claude-opus-5"                # varsayılan güçlü model
MODEL_FRONTIER = "claude-fable-5"             # var, ama Opus'un 2 katı fiyat; sadece açıkça istenirse


class LLMProviderError(TradingBotError):
    """Sağlayıcı seviyesinde hata (ağ, kimlik doğrulama, kota, sağlayıcı yok)."""


class LLMAuthError(LLMProviderError):
    """API anahtarı yok/geçersiz — ağa çıkmadan fırlatılır."""


@dataclass
class LLMRequest:
    system: str
    messages: list[dict[str, Any]]
    schema: dict[str, Any] | None = None
    max_tokens: int = 1024
    model: str = MODEL_STRONG
    temperature: float | None = None       # Opus 4.7+ modellerde gönderilmez (400 verir); sadece eski modeller
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"system": self.system, "messages": self.messages, "schema": self.schema, "max_tokens": self.max_tokens,
                "model": self.model, "temperature": self.temperature, "metadata": self.metadata}


@dataclass
class LLMResponse:
    text: str
    parsed: Any = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""
    stop_reason: str | None = None
    raw_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "parsed": self.parsed, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens, "latency_ms": self.latency_ms, "model": self.model,
                "stop_reason": self.stop_reason, "raw_meta": self.raw_meta}


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(self, req: LLMRequest) -> LLMResponse: ...


# ------------------------------------------------------------------ JSON çıkarımı
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_first_json_object(text: str) -> Any:
    """Metinden ilk geçerli JSON nesnesini sağlam biçimde çıkarır.

    Sıra: tam metin → ```json``` çiti → ilk `{` ile dengeli `}` arası (string/escape farkında).
    Bulunamazsa `ValueError`.
    """
    if text is None:
        raise ValueError("empty text")
    s = text.strip()
    if not s:
        raise ValueError("empty text")
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    for m in _FENCE_RE.finditer(s):
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
    start = s.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    cand = s[start:i + 1]
                    try:
                        return json.loads(cand)
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    raise ValueError("no JSON object found in text")


# ------------------------------------------------------------------ NoOp
NOOP_ADVICE_JSON: dict[str, Any] = {
    "decision_support": "NEUTRAL", "bull_case": [], "bear_case": [], "key_uncertainties": ["llm_disabled"],
    "contradictions": [], "veto": False, "veto_reasons": [], "confidence": 0.0, "evidence_ids": [],
    "historical_lessons": [], "recommended_action": "WAIT_CONFIRMATION",
}


class NoOpLLMProvider:
    """Ağsız varsayılan: her zaman NEUTRAL / veto=False / WAIT_CONFIRMATION / confidence 0 döndürür."""

    name = "noop"

    def __init__(self, model: str = "noop") -> None:
        self.model = model
        self.calls = 0

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        text = json.dumps(NOOP_ADVICE_JSON)
        return LLMResponse(text=text, parsed=dict(NOOP_ADVICE_JSON), input_tokens=0, output_tokens=0,
                           latency_ms=0.0, model=self.model, stop_reason="end_turn", raw_meta={"provider": "noop"})


# ------------------------------------------------------------------ Fake (test)
class FakeProvider:
    """Testler için: `responses` sırayla tüketilir. Öğe `str` → ham metin, `dict` → JSON metni,
    `Exception` → complete() içinde fırlatılır. Kuyruk biterse son öğe tekrarlanır (boşsa NoOp yanıtı)."""

    name = "fake"

    def __init__(self, responses: list[Any] | None = None, model: str = "fake-model",
                 usage: tuple[int, int, int] = (100, 50, 0), latency_ms: float = 1.0) -> None:
        self.responses = list(responses or [])
        self.model = model
        self.usage = usage
        self.latency_ms = latency_ms
        self.calls: list[LLMRequest] = []

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        if not self.responses:
            item: Any = NOOP_ADVICE_JSON
        elif len(self.responses) == 1:
            item = self.responses[0]
        else:
            item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        text = json.dumps(item) if isinstance(item, dict) else str(item)
        try:
            parsed = extract_first_json_object(text)
        except ValueError:
            parsed = None
        i, o, c = self.usage
        return LLMResponse(text=text, parsed=parsed, input_tokens=i, output_tokens=o, cache_read_tokens=c,
                           latency_ms=self.latency_ms, model=req.model or self.model, stop_reason="end_turn",
                           raw_meta={"provider": "fake"})


# ------------------------------------------------------------------ Anthropic
class AnthropicProvider:
    """Anthropic Messages API sağlayıcısı.

    * `anthropic` paketi tembel import edilir (kurulu değilse `LLMProviderError`).
    * API anahtarı YALNIZCA `complete()` içinde `os.environ[api_key_env]`'den okunur; nesnede saklanmaz,
      loglanmaz, `to_dict`/repr içinde görünmez. Anahtar yoksa ağa çıkmadan `LLMAuthError`.
    * `client` parametresi test/enjeksiyon içindir (`client.messages.create(...)` arayüzü yeterli).
    * Sistem istemi `cache_control: ephemeral` ile gönderilir (prompt caching; istem sabit tutulmalı).
    * `temperature` yalnızca `req.temperature is not None` ise gönderilir (Opus 4.7+ modeller reddeder).
    """

    name = "anthropic"

    def __init__(self, model: str = MODEL_STRONG, api_key_env: str = "ANTHROPIC_API_KEY", client: Any = None,
                 max_retries: int = 1, timeout_s: float = 60.0) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self._client = client
        self.max_retries = int(max_retries)
        self.timeout_s = float(timeout_s)

    def __repr__(self) -> str:  # anahtar asla görünmez
        return f"AnthropicProvider(model={self.model!r}, api_key_env={self.api_key_env!r})"

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        key = os.environ.get(self.api_key_env, "")
        if not key or not key.strip():
            raise LLMAuthError(
                f"{self.api_key_env} ortam değişkeni boş/yok — AnthropicProvider ağa çıkmadan durdu. "
                f"LLM istemiyorsanız LLMMode.OFF / NoOpLLMProvider kullanın.")
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise LLMProviderError("`anthropic` paketi kurulu değil (pip install anthropic)") from exc
        # anahtar doğrudan SDK'ya verilir; bu nesnede tutulmaz
        self._client = anthropic.Anthropic(api_key=key, max_retries=self.max_retries, timeout=self.timeout_s)
        del key
        return self._client

    def build_kwargs(self, req: LLMRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": req.model or self.model,
            "max_tokens": int(req.max_tokens),
            "system": [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
            "messages": list(req.messages),
        }
        if req.temperature is not None:
            kwargs["temperature"] = float(req.temperature)
        return kwargs

    def complete(self, req: LLMRequest) -> LLMResponse:
        client = self._get_client()
        kwargs = self.build_kwargs(req)
        t0 = time.perf_counter()
        try:
            resp = client.messages.create(**kwargs)
        except Exception as exc:  # SDK hata sınıfları çeşitli; alan hatasına sar (mesajda anahtar yok)
            raise LLMProviderError(f"anthropic çağrısı başarısız: {type(exc).__name__}: {str(exc)[:200]}") from exc
        latency = (time.perf_counter() - t0) * 1000.0
        text = _text_of(resp)
        usage = getattr(resp, "usage", None)
        parsed: Any = None
        try:
            parsed = extract_first_json_object(text)
        except ValueError:
            parsed = None
        return LLMResponse(
            text=text, parsed=parsed,
            input_tokens=int(_uget(usage, "input_tokens", 0)),
            output_tokens=int(_uget(usage, "output_tokens", 0)),
            cache_read_tokens=int(_uget(usage, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(_uget(usage, "cache_creation_input_tokens", 0) or 0),
            latency_ms=latency, model=str(getattr(resp, "model", kwargs["model"])),
            stop_reason=getattr(resp, "stop_reason", None),
            raw_meta={"request_id": getattr(resp, "_request_id", None), "provider": "anthropic"},
        )


def _uget(obj: Any, key: str, default: Any = 0) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _text_of(resp: Any) -> str:
    content = getattr(resp, "content", None)
    if content is None and isinstance(resp, dict):
        content = resp.get("content")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        btype = _uget(block, "type", None)
        if btype == "text":
            parts.append(str(_uget(block, "text", "")))
    return "\n".join(parts)


# ------------------------------------------------------------------ Batch stub
class BatchStub:
    """Gece toplu işleme (Message Batches API, %50 indirim) için ağsız iskelet.

    Tasarım:
      * gün içinde `enqueue(custom_id, req)` ile istekler biriktirilir (bellek/JSONL),
      * gece bir kez `submit()` → `client.messages.batches.create(requests=[Request(custom_id, params)])`,
      * `poll()` `processing_status == "ended"` olana kadar; sonuçlar `custom_id` ile eşlenir (sıra garanti yok),
      * her sonuç `validate_postmortem`/`validate_response` süzgecinden geçer; bütçe `LLMBudget.record` ile düşülür.
    Bu sınıf ağa ÇIKMAZ; `submit()` çağrılırsa `LLMProviderError` fırlatır (lead entegrasyonu için yer tutucu).
    """

    name = "batch-stub"

    def __init__(self) -> None:
        self.queue: list[tuple[str, LLMRequest]] = []

    def enqueue(self, custom_id: str, req: LLMRequest) -> None:
        self.queue.append((custom_id, req))

    def to_requests(self) -> list[dict[str, Any]]:
        """Batches API `requests` gövdesi (anahtar/istemci gerekmez)."""
        out = []
        for cid, req in self.queue:
            params: dict[str, Any] = {
                "model": req.model, "max_tokens": req.max_tokens,
                "system": [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
                "messages": list(req.messages),
            }
            out.append({"custom_id": cid, "params": params})
        return out

    def submit(self) -> None:
        raise LLMProviderError("BatchStub: toplu gönderim henüz bağlanmadı (ağsız iskelet)")

    def complete(self, req: LLMRequest) -> LLMResponse:
        raise LLMProviderError("BatchStub senkron complete() desteklemez; enqueue()/submit() kullanın")


__all__ = ["LLMRequest", "LLMResponse", "LLMProvider", "NoOpLLMProvider", "FakeProvider", "AnthropicProvider",
           "BatchStub", "LLMProviderError", "LLMAuthError", "extract_first_json_object", "NOOP_ADVICE_JSON",
           "MODEL_CHEAP", "MODEL_STRONG", "MODEL_FRONTIER"]
