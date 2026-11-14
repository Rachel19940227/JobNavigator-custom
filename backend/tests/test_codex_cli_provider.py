import asyncio
import json
import pytest


class DummyDB:
    def close(self): pass


def light(score=75):
    return {"text": json.dumps({"scores": {"Resume": score}, "best_cv": "Resume"}),
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def configure(monkeypatch, enabled="true"):
    import backend.analyzer.llm_client as L
    values = {"codex_fallback_enabled": enabled, "codex_fallback_provider": "ollama",
              "codex_fallback_model": "qwen3:8b"}
    monkeypatch.setattr(L, "SessionLocal", lambda: DummyDB())
    monkeypatch.setattr(L, "_get_setting", lambda db, key, default="": values.get(key, default))
    return L


@pytest.mark.asyncio
async def test_codex_score_success_never_calls_ollama(monkeypatch):
    L = configure(monkeypatch)
    monkeypatch.setattr(L, "_call_codex_runner", lambda *a, **k: asyncio.sleep(0, result=light()))
    async def forbidden(*a, **k): raise AssertionError("Ollama called")
    monkeypatch.setattr(L, "_call_ollama", forbidden)
    result = await L._call_codex_with_local_fallback("score_light", "p", "s", 600, "subscription-default")
    assert result["actual_provider"] == "codex_cli" and not result["fallback_used"]


@pytest.mark.parametrize("category", ["CODEX_QUOTA_EXCEEDED", "CODEX_AUTH_REQUIRED"])
@pytest.mark.asyncio
async def test_quota_and_auth_fall_back_immediately(monkeypatch, category):
    L = configure(monkeypatch)
    from backend.analyzer.codex_gateway import CodexProviderError
    calls = 0
    async def fail(*a):
        nonlocal calls; calls += 1; raise CodexProviderError(category, "safe")
    monkeypatch.setattr(L, "_call_codex_runner", fail)
    monkeypatch.setattr(L, "_call_ollama", lambda *a, **k: asyncio.sleep(0, result=light()))
    result = await L._call_codex_with_local_fallback("score_light", "p", "s", 600, "subscription-default")
    assert calls == 1 and result["actual_provider"] == "ollama"
    assert result["codex_error_category"] == category


@pytest.mark.parametrize("category", ["CODEX_TIMEOUT", "CODEX_PROCESS_FAILED", "CODEX_INVALID_OUTPUT"])
@pytest.mark.asyncio
async def test_transient_codex_damage_retries_once_then_falls_back(monkeypatch, category):
    L = configure(monkeypatch)
    from backend.analyzer.codex_gateway import CodexProviderError
    calls = 0
    async def fail(*a):
        nonlocal calls; calls += 1; raise CodexProviderError(category, "safe")
    monkeypatch.setattr(L, "_call_codex_runner", fail)
    monkeypatch.setattr(L, "_call_ollama", lambda *a, **k: asyncio.sleep(0, result=light()))
    result = await L._call_codex_with_local_fallback("score_light", "p", "s", 600, "subscription-default")
    assert calls == 2 and result["fallback_used"]


@pytest.mark.asyncio
async def test_low_valid_score_does_not_fallback(monkeypatch):
    L = configure(monkeypatch)
    monkeypatch.setattr(L, "_call_codex_runner", lambda *a, **k: asyncio.sleep(0, result=light(4)))
    async def forbidden(*a, **k): raise AssertionError("Ollama called for low score")
    monkeypatch.setattr(L, "_call_ollama", forbidden)
    result = await L._call_codex_with_local_fallback("score_light", "p", "s", 600, "subscription-default")
    assert json.loads(result["text"])["scores"]["Resume"] == 4


@pytest.mark.asyncio
async def test_user_cancel_never_falls_back(monkeypatch):
    L = configure(monkeypatch)
    async def cancel(*a): raise asyncio.CancelledError()
    monkeypatch.setattr(L, "_call_codex_runner", cancel)
    async def forbidden(*a, **k): raise AssertionError("Ollama called after cancellation")
    monkeypatch.setattr(L, "_call_ollama", forbidden)
    with pytest.raises(asyncio.CancelledError):
        await L._call_codex_with_local_fallback("score_light", "p", "s", 600, "subscription-default")


@pytest.mark.asyncio
async def test_both_providers_fail_clearly(monkeypatch):
    L = configure(monkeypatch)
    from backend.analyzer.codex_gateway import CodexProviderError, CODEX_PROCESS_FAILED
    async def codex_fail(*a): raise CodexProviderError(CODEX_PROCESS_FAILED, "crash")
    async def ollama_fail(*a, **k): raise RuntimeError("ollama unavailable")
    monkeypatch.setattr(L, "_call_codex_runner", codex_fail)
    monkeypatch.setattr(L, "_call_ollama", ollama_fail)
    with pytest.raises(RuntimeError, match="Codex failed.*Ollama.*failed"):
        await L._call_codex_with_local_fallback("score_light", "p", "s", 600, "subscription-default")


@pytest.mark.asyncio
async def test_tailoring_uses_codex_and_preserves_operation(monkeypatch):
    L = configure(monkeypatch)
    payload = {"summary": "s", "experience": [], "skills": {"Data": "SQL"}}
    seen = []
    async def ok(operation, *a): seen.append(operation); return {"text": json.dumps(payload), "usage": {}}
    monkeypatch.setattr(L, "_call_codex_runner", ok)
    result = await L._call_codex_with_local_fallback("resume_tailoring", "p", "s", 3000, "subscription-default")
    assert seen == ["resume_tailoring"] and result["actual_provider"] == "codex_cli"


@pytest.mark.asyncio
async def test_cover_letter_uses_its_own_codex_operation(monkeypatch):
    L = configure(monkeypatch)
    monkeypatch.setattr(L, "resolve_llm_config", lambda feature: {
        "provider": "codex_cli", "model": "gpt-6-astra", "api_key": "",
    })
    payload = {"greeting": "Dear Team,", "body_paragraphs": ["Body."],
               "closing": "Sincerely,", "signature": "Candidate"}
    seen = []

    async def ok(operation, prompt, *args):
        seen.append((operation, prompt))
        return {"text": json.dumps(payload), "usage": {}}

    monkeypatch.setattr(L, "_call_codex_runner", ok)
    result = await L.call_cover_letter_llm(
        "LETTER PROMPT", "system", 1500, cached_prefix="RESUME PREFIX")

    assert seen == [("cover_letter", "RESUME PREFIX\n\nLETTER PROMPT")]
    assert result["actual_provider"] == "codex_cli"


def test_cover_letter_codex_schema_accepts_letter_and_rejects_empty_body():
    from backend.analyzer.codex_gateway import validate_result, CodexProviderError

    valid = {"greeting": "Dear Team,", "body_paragraphs": ["Body."],
             "closing": "Sincerely,", "signature": ""}
    assert validate_result("cover_letter", valid) == valid

    with pytest.raises(CodexProviderError, match="non-empty|should be non-empty"):
        validate_result("cover_letter", {**valid, "body_paragraphs": [""]})


@pytest.mark.asyncio
async def test_openai_api_key_cannot_route_codex_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    L = configure(monkeypatch)
    monkeypatch.setattr(L, "_call_codex_runner", lambda *a, **k: asyncio.sleep(0, result=light()))
    async def forbidden(*a, **k): raise AssertionError("OpenAI provider called")
    monkeypatch.setattr(L, "_call_openai", forbidden)
    result = await L.call_llm("p", "s", 600, provider="codex_cli",
                              model="subscription-default", api_key="")
    assert result["actual_provider"] == "codex_cli"


@pytest.mark.asyncio
async def test_scoring_cached_prefix_reaches_codex(monkeypatch):
    L = configure(monkeypatch)
    seen = []
    async def ok(operation, prompt, *a): seen.append(prompt); return light()
    monkeypatch.setattr(L, "_call_codex_runner", ok)
    await L.call_llm("JOB DESCRIPTION", "system", 600, cached_prefix="RUBRIC AND RESUMES",
                     provider="codex_cli", model="subscription-default", api_key="")
    assert seen == ["RUBRIC AND RESUMES\n\nJOB DESCRIPTION"]
