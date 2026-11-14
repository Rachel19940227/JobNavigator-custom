"""Gap-based re-tailoring reuses the normal tailor worker and score worker."""
import uuid

import pytest
from fastapi import HTTPException

from backend.models.db import Job, Resume, Setting


def test_enhancement_validation_preserves_unicode_and_rejects_bad_shapes():
    import backend.api.routes_resumes as rr

    rows = rr._validate_enhancement_gaps([
        {"gap": "API ownership", "fact": "在讯飞负责接口需求、字段 mapping 和 API 测试。"},
        {"gap": "Salesforce.com experience", "fact": ""},
    ])
    assert rows[0]["fact"].startswith("在讯飞")
    assert rows[1] == {"gap": "Salesforce.com experience", "fact": ""}
    with pytest.raises(HTTPException):
        rr._validate_enhancement_gaps({"gap": "not a list"})


def test_enhancement_prompt_distinguishes_facts_from_skill_only_and_omits_unselected():
    import backend.api.routes_resumes as rr

    prompt = rr._enhancement_prompt([
        {"gap": "API / middleware specification ownership", "fact": "在讯飞负责 API 对接和测试"},
        {"gap": "Salesforce.com experience", "fact": ""},
    ])
    assert "在讯飞负责 API 对接和测试" in prompt
    assert "WITHOUT a fact" in prompt and "Skills" in prompt
    assert "Do not create or alter work experience" in prompt
    assert "Veeva Vault" not in prompt


@pytest.mark.asyncio
async def test_enhanced_tailor_uses_current_copy_and_chains_one_full_rescore(test_db, monkeypatch):
    import backend.api.routes_resumes as rr

    job = Job(external_id="enhanced-1", content_hash="enhanced-c1", company="Acme",
              title="Integration Analyst", description="Own APIs, middleware, and Salesforce enablement.")
    base = Resume(name="Business Analyst", is_base=True, template="inter",
                  json_data={"summary": "Original", "experience": [{"company": "iFLYTEK", "bullets": ["Original evidence"]}], "skills": {"Tools": ["SQL"]}})
    test_db.add_all([job, base])
    test_db.flush()
    current = Resume(name="Business Analyst → Acme — Integration Analyst", is_base=False,
                     parent_id=base.id, job_id=job.id, template="inter",
                     json_data={"summary": "Current tailored", "experience": [{"company": "iFLYTEK", "bullets": ["Current evidence"]}], "skills": {"Tools": ["SQL"]}})
    test_db.add_all([
        current,
        Setting(key="cv_tailor_prompt", value="JD {job_description}\nRESUME {resume_json}"),
        Setting(key="tailor_auto_quick_score", value="off"),
    ])
    test_db.commit()

    captured = {}

    async def fake_llm(prompt, system, max_tokens=3000):
        captured["prompt"] = prompt
        return {"text": '{"summary":"Enhanced","experience":[{"bullets":["Defined API integration requirements, data mappings, and interface specifications between client systems and the enterprise AI platform, supporting integration testing and cross-system delivery."]}],"skills":{"Tools":["SQL","Salesforce"]}}', "usage": {}}

    launched = []

    def fake_launch(job_type, *args, **kwargs):
        launched.append((job_type, kwargs))
        return str(uuid.uuid4())

    monkeypatch.setattr("backend.analyzer.llm_client.call_cv_tailor_llm", fake_llm)
    monkeypatch.setattr(rr, "launch_background", fake_launch)
    monkeypatch.setattr(rr, "_tailoring_semaphore", None, raising=False)

    await rr._tailor_impl(str(current.id), str(job.id), None, [
        {"gap": "API / middleware specification ownership", "fact": "在讯飞负责客户系统和AI平台API对接，包括接口需求、字段mapping和API测试。"},
        {"gap": "Salesforce.com experience", "fact": ""},
    ])

    assert "Current tailored" in captured["prompt"]
    assert "在讯飞负责客户系统和AI平台API对接" in captured["prompt"]
    assert "Veeva Vault" not in captured["prompt"]

    test_db.expire_all()
    enhanced = test_db.query(Resume).filter(Resume.job_id == job.id).order_by(Resume.created_at.desc()).first()
    assert enhanced.id != current.id
    assert enhanced.parent_id == base.id
    assert enhanced.name == "Business Analyst → Acme — Integration Analyst"
    assert "Defined API integration requirements" in enhanced.json_data["experience"][0]["bullets"][0]
    assert "Salesforce" in enhanced.json_data["skills"]["Tools"]

    score_runs = [kwargs for job_type, kwargs in launched if job_type == "analyze_job"]
    assert len(score_runs) == 1
    assert score_runs[0]["func_kwargs"]["cv_ids"] == [str(enhanced.id)]
    assert score_runs[0]["func_kwargs"]["depth"] == "full"


@pytest.mark.asyncio
async def test_enhanced_tailor_endpoint_reports_full_score_without_changing_normal_default(test_db, monkeypatch):
    import backend.api.routes_resumes as rr

    job = Job(external_id="enhanced-2", content_hash="enhanced-c2", company="Acme",
              title="Analyst", description="A usable job description")
    base = Resume(name="Base", is_base=True, json_data={"summary": "s"})
    test_db.add_all([job, base, Setting(key="cv_tailor_prompt", value="{resume_json} {job_description}"),
                     Setting(key="tailor_auto_quick_score", value="off")])
    test_db.commit()
    calls = []
    monkeypatch.setattr(rr, "launch_background", lambda *args, **kwargs: calls.append(kwargs) or str(uuid.uuid4()))

    enhanced = await rr.tailor_resume({"base_resume_id": str(base.id), "job_id": str(job.id),
                                        "enhancement_gaps": [{"gap": "Salesforce", "fact": ""}]}, db=test_db)
    normal = await rr.tailor_resume({"base_resume_id": str(base.id), "job_id": str(job.id)}, db=test_db)

    assert enhanced["chain_score"] == "full"
    assert normal["chain_score"] == "off"
    assert calls[0]["func_kwargs"]["enhancement_gaps"] == [{"gap": "Salesforce", "fact": ""}]
    assert calls[1]["func_kwargs"]["enhancement_gaps"] is None
