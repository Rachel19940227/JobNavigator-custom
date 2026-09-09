"""Client, schemas and failure policy for the host-side Codex subscription runner."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator


CODEX_QUOTA_EXCEEDED = "CODEX_QUOTA_EXCEEDED"
CODEX_AUTH_REQUIRED = "CODEX_AUTH_REQUIRED"
CODEX_TIMEOUT = "CODEX_TIMEOUT"
CODEX_PROCESS_FAILED = "CODEX_PROCESS_FAILED"
CODEX_INVALID_OUTPUT = "CODEX_INVALID_OUTPUT"
USER_CANCELLED = "USER_CANCELLED"
BUSINESS_VALIDATION_FAILED = "BUSINESS_VALIDATION_FAILED"
FALLBACK_CATEGORIES = {
    CODEX_QUOTA_EXCEEDED, CODEX_AUTH_REQUIRED, CODEX_TIMEOUT,
    CODEX_PROCESS_FAILED, CODEX_INVALID_OUTPUT,
}
RETRYABLE_CATEGORIES = {CODEX_TIMEOUT, CODEX_PROCESS_FAILED, CODEX_INVALID_OUTPUT}


@dataclass
class CodexProviderError(RuntimeError):
    category: str
    detail: str

    def __str__(self) -> str:
        return f"{self.category}: {self.detail}"


STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
SCORE_LIGHT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["scores", "best_cv"],
    "properties": {
        "scores": {"type": "object",
                   "additionalProperties": {"type": "number", "minimum": 0, "maximum": 100}},
        "best_cv": {"type": "string", "minLength": 1},
    },
}
SCORE_FULL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["scores", "best_cv", "job_category", "summary", "requirement_mapping",
                 "breakdown", "keyword_coverage_pct", "matched_keywords", "missing_keywords",
                 "experience_gaps", "potential_concerns", "hard_blockers", "ats_tip"],
    "properties": {
        **SCORE_LIGHT_SCHEMA["properties"],
        "job_category": {"type": "string", "enum": ["Business Analyst", "Project", "Product", "Data", "Software Development", "IT Support", "Testing / QA"]},
        "summary": {"type": "string"},
        "breakdown": {"type": "object", "additionalProperties": False,
                      "required": ["skills", "experience", "domain", "role", "requirements"],
                      "properties": {k: {"type": "number", "minimum": 0, "maximum": 20}
                                     for k in ["skills", "experience", "domain", "role", "requirements"]}},
        "requirement_mapping": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["requirement", "cv_match", "matched", "severity"],
            "properties": {"requirement": {"type": "string"},
                           "cv_match": {"type": ["string", "null"]},
                           "matched": {"type": "boolean"},
                           "severity": {"type": "string", "enum": ["required", "preferred"]}},
        }},
        "keyword_coverage_pct": {"type": "number", "minimum": 0, "maximum": 100},
        "matched_keywords": STRING_ARRAY, "missing_keywords": STRING_ARRAY,
        "experience_gaps": STRING_ARRAY, "potential_concerns": STRING_ARRAY,
        "hard_blockers": STRING_ARRAY, "ats_tip": {"type": "string"},
    },
}
TAILOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "experience", "skills"],
    "properties": {
        "summary": {"type": "string"},
        "experience": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["company", "title", "location", "date", "description", "bullets", "suggested_bullets"],
            "properties": {
                "company": {"type": "string"}, "title": {"type": "string"},
                "location": {"type": "string"}, "date": {"type": "string"},
                "description": {"type": ["string", "null"]},
                "bullets": STRING_ARRAY, "suggested_bullets": STRING_ARRAY,
            },
        }},
        "skills": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}
SCHEMAS = {"score_light": SCORE_LIGHT_SCHEMA, "score_full": SCORE_FULL_SCHEMA,
           "resume_tailoring": TAILOR_SCHEMA}
_semaphore = None
_semaphore_limit = None


def _runtime_settings() -> tuple[int, int]:
    from backend.models.db import SessionLocal, Setting
    db = SessionLocal()
    try:
        rows = {r.key: r.value for r in db.query(Setting).filter(
            Setting.key.in_(["codex_timeout_seconds", "codex_max_concurrency"])).all()}
        return max(30, min(1800, int(rows.get("codex_timeout_seconds", "600")))), \
            max(1, min(2, int(rows.get("codex_max_concurrency", "1"))))
    finally:
        db.close()


def reset_codex_semaphore() -> None:
    global _semaphore, _semaphore_limit
    _semaphore = None; _semaphore_limit = None


def _get_codex_semaphore(limit: int):
    global _semaphore, _semaphore_limit
    if _semaphore is None or _semaphore_limit != limit:
        _semaphore = asyncio.Semaphore(limit); _semaphore_limit = limit
    return _semaphore


def validate_result(operation: str, value) -> dict:
    schema = SCHEMAS.get(operation)
    if not schema:
        raise CodexProviderError(BUSINESS_VALIDATION_FAILED, f"unsupported operation: {operation}")
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.path))
    if errors:
        detail = errors[0].message[:240]
        raise CodexProviderError(CODEX_INVALID_OUTPUT, detail)
    return value


def _token() -> str:
    path = Path(os.getenv("CODEX_RUNNER_TOKEN_FILE", "/run/secrets/codex_runner_token"))
    try:
        return path.read_text().strip()
    except OSError as exc:
        raise CodexProviderError(CODEX_PROCESS_FAILED, "runner token is unavailable") from exc


async def call_runner(operation: str, prompt: str, system: str, max_tokens: int) -> dict:
    url = os.getenv("CODEX_RUNNER_URL", "http://host.docker.internal:8765").rstrip("/")
    started = time.monotonic()
    process_timeout, concurrency = _runtime_settings()
    timeout = process_timeout + 60
    try:
        async with _get_codex_semaphore(concurrency):
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{url}/v1/run", headers={"Authorization": f"Bearer {_token()}"},
                    json={"operation": operation, "prompt": prompt, "system": system,
                          "max_tokens": max_tokens, "timeout_seconds": process_timeout},
                )
    except asyncio.CancelledError:
        raise
    except httpx.TimeoutException as exc:
        raise CodexProviderError(CODEX_TIMEOUT, "host runner request timed out") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise CodexProviderError(CODEX_PROCESS_FAILED, "host runner is unavailable") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise CodexProviderError(CODEX_INVALID_OUTPUT, "runner returned invalid JSON") from exc
    if response.status_code != 200 or not payload.get("ok"):
        raise CodexProviderError(payload.get("error_code", CODEX_PROCESS_FAILED),
                                 str(payload.get("message", "Codex runner failed"))[:300])
    result = payload.get("result")
    validate_result(operation, result)
    return {
        "text": json.dumps(result, ensure_ascii=False),
        "usage": payload.get("usage") or {"input_tokens": 0, "output_tokens": 0,
                                           "cache_read_tokens": 0, "cache_write_tokens": 0},
        "duration_ms": payload.get("duration_ms") or int((time.monotonic() - started) * 1000),
    }


async def runner_status() -> dict:
    url = os.getenv("CODEX_RUNNER_URL", "http://host.docker.internal:8765").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{url}/health", headers={"Authorization": f"Bearer {_token()}"})
        response.raise_for_status()
        return response.json()
    except Exception:
        return {"reachable": False, "installed": False, "logged_in": False,
                "version": "", "message": "Host Codex Runner is not reachable"}
