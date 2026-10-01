"""LLM katmanı — şema, sağlayıcılar, bütçe, kesici, önbellek, redaksiyon, servis modları (ağsız)."""
from __future__ import annotations

import json

import pytest

from tradingbot.core import LLMSchemaError
from tradingbot.llm import (AnthropicProvider, CircuitBreaker, FakeProvider, LLMAdvice, LLMAuthError, LLMBudget, LLMMode,
                            LLMRequest, LLMResearchService, NoOpLLMProvider, PriceTable, SemanticCache, estimate_cost,
                            extract_first_json_object, redact, validate_postmortem, validate_response)
from tradingbot.llm.provider import LLMProviderError
from tradingbot.llm.service import merge_council

FAKE_KEY = "sk-ant-api03-FAKEKEYFAKEKEYFAKEKEYFAKEKEY0123456789"


def good_obj(**over):
    base = {
        "decision_support": "SUPPORT", "bull_case": ["trend up"], "bear_case": ["funding high"],
        "key_uncertainties": ["low volume"], "contradictions": [], "veto": False, "veto_reasons": [],
        "confidence": 0.7, "evidence_ids": ["e1"], "historical_lessons": [], "recommended_action": "PROCEED",
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ schema
def test_validate_response_ok_invalid_and_evidence():
    adv = validate_response(good_obj(), ["e1", "e2"])
    assert adv.decision_support == "SUPPORT" and adv.recommended_action == "PROCEED" and not adv.failed
    with pytest.raises(LLMSchemaError):
        validate_response(good_obj(confidence=1.5), ["e1"])
    with pytest.raises(LLMSchemaError):
        validate_response(good_obj(extra_field=1), ["e1"])
    with pytest.raises(LLMSchemaError):
        validate_response({k: v for k, v in good_obj().items() if k != "veto"}, ["e1"])
    with pytest.raises(LLMSchemaError):
        validate_response(good_obj(evidence_ids=["e1", "made_up"]), ["e1"])
    with pytest.raises(LLMSchemaError):
        validate_response(good_obj(veto=True, veto_reasons=[]), ["e1"])
    with pytest.raises(LLMSchemaError):
        validate_response("not a dict", ["e1"])


def test_validate_postmortem_normalizes_codes():
    pm = validate_postmortem({"summary": " ok ", "what_was_right": [], "what_was_wrong": ["late"],
                              "lesson_codes": ["stop too tight", "STOP_TOO_TIGHT"], "hypotheses": []})
    assert pm["lesson_codes"] == ["STOP_TOO_TIGHT"] and pm["summary"] == "ok"
    with pytest.raises(LLMSchemaError):
        validate_postmortem({"summary": "x"})


def test_extract_json_robust():
    assert extract_first_json_object('```json\n{"a": 1}\n```')["a"] == 1
    assert extract_first_json_object('Sure! Here: {"a": {"b": "}"}} trailing')["a"]["b"] == "}"
    with pytest.raises(ValueError):
        extract_first_json_object("no json here")


# ------------------------------------------------------------------ providers
def test_noop_provider_fail_closed():
    svc = LLMResearchService(NoOpLLMProvider(), LLMBudget(None), mode=LLMMode.ADVISORY)
    adv = svc.advise({"symbol": "BTCUSDT"}, ["e1"], [])
    assert adv is not None and adv.decision_support == "NEUTRAL" and adv.veto is False
    assert adv.recommended_action == "WAIT_CONFIRMATION" and adv.confidence == 0.0 and adv.provider == "noop"
    assert not adv.allows_open


def test_fake_invalid_json_twice_gives_failed_advice(tmp_path):
    prov = FakeProvider(["not json at all", "{\"still\": \"bad\"}"])
    svc = LLMResearchService(prov, LLMBudget(tmp_path / "b.json"), mode=LLMMode.ADVISORY, max_retries=1)
    adv = svc.advise({"symbol": "ETHUSDT"}, ["e1"], [])
    assert adv.failed and adv.schema_invalid and adv.veto is False
    assert adv.decision_support == "NEUTRAL" and adv.recommended_action == "WAIT_CONFIRMATION"
    assert len(prov.calls) == 2                       # 1 + 1 retry
    assert svc.calls[-1]["failure_kind"] == "schema_invalid" and svc.calls[-1]["retries"] == 1
    # retry message contains validator feedback and no assistant prefill at the tail
    assert prov.calls[1].messages[-1]["role"] == "user" and "rejected" in prov.calls[1].messages[-1]["content"]


def test_fake_valid_second_try_succeeds():
    prov = FakeProvider(["garbage", good_obj()])
    svc = LLMResearchService(prov, LLMBudget(None), mode=LLMMode.ADVISORY)
    adv = svc.advise({"symbol": "ETHUSDT"}, ["e1"], [])
    assert not adv.failed and adv.recommended_action == "PROCEED" and adv.allows_open
    assert svc.calls[-1]["retries"] == 1 and svc.calls[-1]["output_hash"]


def test_anthropic_provider_without_key_raises_without_network(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = AnthropicProvider(model="claude-opus-5")
    with pytest.raises(LLMAuthError) as ei:
        p.complete(LLMRequest(system="s", messages=[{"role": "user", "content": "x"}]))
    assert "ANTHROPIC_API_KEY" in str(ei.value)
    assert "sk-" not in repr(p)


def test_anthropic_provider_with_injected_client_builds_cached_system(monkeypatch):
    class _Usage:
        input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 120, 30, 100, 0

    class _Block:
        type, text = "text", json.dumps(good_obj())

    class _Resp:
        content, usage, model, stop_reason, _request_id = [_Block()], _Usage(), "claude-opus-5", "end_turn", "req_1"

    captured = {}

    class _Msgs:
        def create(self, **kw):
            captured.update(kw)
            return _Resp()

    class _Client:
        messages = _Msgs()

    p = AnthropicProvider(client=_Client())
    resp = p.complete(LLMRequest(system="SYS", messages=[{"role": "user", "content": "x"}], max_tokens=512))
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"} and captured["max_tokens"] == 512
    assert "temperature" not in captured
    assert resp.parsed["decision_support"] == "SUPPORT" and resp.cache_read_tokens == 100


# ------------------------------------------------------------------ budget / breaker / cache
def test_budget_exhaustion_returns_failed_and_logs(tmp_path):
    rows = []
    prov = FakeProvider([good_obj()], usage=(1000, 500, 0))
    b = LLMBudget(tmp_path / "budget.json", daily_usd=0.00001, daily_tokens=10_000_000)
    svc = LLMResearchService(prov, b, mode=LLMMode.ADVISORY, log_sink=rows.append, calls_path=tmp_path / "calls.jsonl")
    adv = svc.advise({"symbol": "BTCUSDT"}, ["e1"], [])
    assert adv.failed and adv.failure_kind.startswith("budget:") and adv.veto is False
    assert prov.calls == []                           # sağlayıcıya hiç gidilmedi
    assert rows and rows[-1]["failure_kind"].startswith("budget:")
    assert (tmp_path / "calls.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_budget_persist_and_rollover(tmp_path):
    from datetime import datetime, timedelta, timezone
    now = [datetime(2026, 8, 18, 23, 59, tzinfo=timezone.utc)]
    b = LLMBudget(tmp_path / "b.json", daily_usd=1.0, daily_tokens=1000, per_tour_candidates=2, clock=lambda: now[0])
    b.record({"input_tokens": 100, "output_tokens": 50}, 0.4, tour_id="t1")
    b2 = LLMBudget(tmp_path / "b.json", daily_usd=1.0, daily_tokens=1000, per_tour_candidates=2, clock=lambda: now[0])
    assert b2.state.spent_tokens == 150 and abs(b2.remaining()["usd"] - 0.6) < 1e-9
    assert b2.can_spend(10, 0.7)[0] is False
    b2.record({"input_tokens": 1, "output_tokens": 1}, 0.0, tour_id="t1")
    assert b2.can_spend(1, 0.0, tour_id="t1") == (False, "per_tour_candidates_exceeded (2 >= 2)")
    now[0] += timedelta(minutes=2)                     # yeni UTC günü
    assert b2.can_spend(10, 0.7)[0] is True and b2.remaining()["tokens"] == 1000


def test_price_table_and_estimate():
    t = PriceTable()
    assert t.get("claude-opus-5").verified_at == "2026-06-24"
    assert estimate_cost("claude-opus-5", 1_000_000, 0) == 5.0
    assert estimate_cost("claude-haiku-4-5-20251001", 0, 1_000_000) == 5.0
    unknown = t.get("some-future-model")
    assert unknown.verified_at == "unknown-fallback" and unknown.output_per_m == 50.0   # en pahalı satır


def test_circuit_breaker_opens_and_cools_down():
    clock = [1000.0]
    prov = FakeProvider([LLMProviderError("boom")])
    br = CircuitBreaker(failures_to_open=2, cooldown_s=60, clock=lambda: clock[0])
    svc = LLMResearchService(prov, LLMBudget(None), breaker=br, mode=LLMMode.ADVISORY)
    svc.advise({"s": 1}, [], []); svc.advise({"s": 2}, [], [])
    assert br.state == CircuitBreaker.OPEN and len(prov.calls) == 2
    a = svc.advise({"s": 3}, [], [])
    assert a.failure_kind == "breaker_open" and len(prov.calls) == 2      # çağrı yapılmadı
    clock[0] += 61
    prov.responses = [good_obj(evidence_ids=[])]
    a = svc.advise({"s": 4}, [], [])
    assert not a.failed and br.state == CircuitBreaker.CLOSED


def test_semantic_cache_hit_skips_provider(tmp_path):
    prov = FakeProvider([good_obj()])
    cache = SemanticCache(tmp_path / "cache.json", ttl_s=100)
    svc = LLMResearchService(prov, LLMBudget(None), cache=cache, mode=LLMMode.ADVISORY)
    snap = {"symbol": "SOLUSDT", "features": {"rsi": 55}}
    a1 = svc.advise(snap, ["e1"], [])
    a2 = svc.advise(dict(snap), ["e1"], [])
    assert len(prov.calls) == 1 and a2.cache_hit and not a1.cache_hit
    assert a2.recommended_action == a1.recommended_action and svc.calls[-1]["cache_hit"] is True
    a3 = svc.advise({**snap, "features": {"rsi": 56}}, ["e1"], [])
    assert len(prov.calls) == 2 and not a3.cache_hit


# ------------------------------------------------------------------ redact / logs
def test_redact_strips_secrets_nested():
    obj = {"api_key": "abc", "nested": {"Authorization": "Bearer x", "list": [{"secret_token": 1}, FAKE_KEY]},
           "hexy": "0123456789abcdef0123456789abcdef", "ok": "short value", "n": 3}
    r = redact(obj)
    assert r["api_key"] == "[REDACTED]" and r["nested"]["Authorization"] == "[REDACTED]"
    assert r["nested"]["list"][0]["secret_token"] == "[REDACTED]" and r["nested"]["list"][1] == "[REDACTED]"
    assert r["hexy"] == "[REDACTED]" and r["ok"] == "short value" and r["n"] == 3
    assert obj["api_key"] == "abc"                     # orijinal değişmedi


def test_log_rows_never_contain_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    rows = []
    prov = FakeProvider([good_obj()])
    svc = LLMResearchService(prov, LLMBudget(None), mode=LLMMode.ADVISORY, log_sink=rows.append,
                             calls_path=tmp_path / "calls.jsonl")
    snap = {"symbol": "BTCUSDT", "api_key": FAKE_KEY, "note": f"key={FAKE_KEY}", "cfg": {"token": "zzz"}}
    svc.advise(snap, ["e1"], [{"lesson": "x", "password": "p"}])
    dumped = json.dumps(rows) + (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    assert FAKE_KEY not in dumped and "zzz" not in dumped
    # istem gövdesine de sızmaz
    assert FAKE_KEY not in prov.calls[0].messages[0]["content"] and "zzz" not in prov.calls[0].messages[0]["content"]
    for k in ("provider", "model", "mode", "prompt_version", "prompt_hash", "snapshot_id", "output_hash", "input_tokens",
              "output_tokens", "cache_read_tokens", "est_cost_usd", "latency_ms", "cache_hit", "failure_kind", "retries",
              "request_redacted"):
        assert k in rows[0]


# ------------------------------------------------------------------ modes
def test_mode_off_and_postmortem_only():
    prov = FakeProvider([good_obj()])
    svc = LLMResearchService(prov, LLMBudget(None), mode=LLMMode.OFF)
    assert svc.advise({"s": 1}, ["e1"], []) is None and svc.postmortem({"trade_id": "t"}) is None
    assert prov.calls == []
    pm_prov = FakeProvider([{"summary": "late entry", "what_was_right": [], "what_was_wrong": ["late"],
                             "lesson_codes": ["late_entry"], "hypotheses": ["h1"]}])
    svc2 = LLMResearchService(pm_prov, LLMBudget(None), mode="POSTMORTEM_ONLY")
    assert svc2.advise({"s": 1}, ["e1"], []) is None
    pm = svc2.postmortem({"trade_id": "t1", "pnl": -1.2}, {"outcome_class": "LOSS"})
    assert pm["lesson_codes"] == ["LATE_ENTRY"] and len(pm_prov.calls) == 1


def test_veto_only_passes_veto_through():
    prov = FakeProvider([good_obj(veto=True, veto_reasons=["news risk"], recommended_action="PROCEED"),
                         good_obj(veto=False, recommended_action="PROCEED")])
    svc = LLMResearchService(prov, LLMBudget(None), mode=LLMMode.VETO_ONLY)
    a = svc.advise({"s": 1}, ["e1"], [])
    assert a.veto is True and a.recommended_action == "SKIP" and a.veto_reasons == ["news risk"]
    b = svc.advise({"s": 2}, ["e1"], [])
    assert b.veto is False and b.recommended_action == "WAIT_CONFIRMATION"   # VETO_ONLY asla PROCEED üretmez


def test_council_merge_conservative():
    prov = FakeProvider([good_obj(recommended_action="PROCEED"),
                         good_obj(decision_support="OPPOSE", recommended_action="SKIP", veto=True, veto_reasons=["r"]),
                         good_obj(decision_support="NEUTRAL", recommended_action="WAIT_CONFIRMATION")])
    svc = LLMResearchService(prov, LLMBudget(None), mode=LLMMode.RESEARCH_COUNCIL)
    a = svc.council({"s": 1}, ["e1"], [])
    assert len(prov.calls) == 3 and a.veto and a.recommended_action == "SKIP"
    m = merge_council({"bull": LLMAdvice(decision_support="SUPPORT", confidence=0.9, recommended_action="PROCEED"),
                       "bear": LLMAdvice(decision_support="SUPPORT", confidence=0.6, recommended_action="REDUCE_SIZE")})
    assert m.recommended_action == "WAIT_CONFIRMATION" and m.decision_support == "SUPPORT" and m.confidence < 0.6
