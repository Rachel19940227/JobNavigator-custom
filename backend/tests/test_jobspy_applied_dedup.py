"""Applied jobs suppress same-company/title/full-JD reposts with a different URL."""
import sys
import types
import uuid

from backend.models.db import Application, Job, Search
from backend.scraper._shared.dedup import make_content_hash
from backend.scraper.sources.jobspy import (
    _applied_posting_fingerprints,
    _description_fingerprint,
)


def _job(url, description, *, company="Softchoice", title="Associate Project Manager - Bilingual"):
    return Job(
        id=uuid.uuid4(), external_id=str(uuid.uuid4()),
        content_hash=make_content_hash(company, title), company=company,
        title=title, url=url, description=description, status="applied",
    )


def test_applied_repost_matches_despite_url_location_and_jd_whitespace(test_db):
    original = _job("https://www.linkedin.com/jobs/view/4441360653", "Same full JD\nwith spacing")
    test_db.add(original)
    test_db.flush()
    test_db.add(Application(job_id=original.id, status="applied"))
    test_db.commit()

    known = _applied_posting_fingerprints(test_db)
    candidate = (
        make_content_hash("Softchoice", "Associate Project Manager - Bilingual"),
        _description_fingerprint(" SAME full JD   with spacing "),
    )
    assert candidate in known


def test_same_title_with_different_jd_is_not_suppressed(test_db):
    original = _job("https://example.test/one", "Ottawa project role")
    test_db.add(original)
    test_db.flush()
    test_db.add(Application(job_id=original.id, status="applied"))
    test_db.commit()

    known = _applied_posting_fingerprints(test_db)
    candidate = (original.content_hash, _description_fingerprint("Toronto project role"))
    assert candidate not in known


def test_unapplied_job_does_not_suppress_later_posting(test_db):
    original = _job("https://example.test/one", "Same JD")
    original.status = "new"
    test_db.add(original)
    test_db.commit()

    assert _applied_posting_fingerprints(test_db) == set()


def test_jobspy_run_skips_applied_repost_with_new_linkedin_id(test_db, monkeypatch):
    import pandas as pd
    from backend.scraper.sources.jobspy import _run_sync

    original = _job("https://www.linkedin.com/jobs/view/4441360653", "Identical full JD")
    test_db.add(original)
    test_db.flush()
    test_db.add(Application(job_id=original.id, status="applied"))
    search = Search(
        name="Toronto", search_mode="keyword", active=True, sources=["linkedin"],
        search_term="project manager", title_include_keywords=[],
        title_exclude_keywords=[], company_filter=[], company_exclude=[],
        auto_scoring_depth="off",
    )
    test_db.add(search)
    test_db.commit()

    fake = types.ModuleType("jobspy")
    fake.scrape_jobs = lambda **kwargs: pd.DataFrame([{
        "site": "linkedin", "title": original.title, "company": original.company,
        "job_url": "https://www.linkedin.com/jobs/view/4441369289",
        "description": "Identical full JD", "location": "Toronto, ON",
        "min_amount": None, "max_amount": None,
    }])
    monkeypatch.setitem(sys.modules, "jobspy", fake)
    monkeypatch.setattr("backend.scraper.sources.jobspy._apply_h1b_inline", lambda *a, **k: None)

    result = _run_sync(search)

    assert result["new_jobs"] == 0
    assert test_db.query(Job).filter(Job.company == "Softchoice").count() == 1
