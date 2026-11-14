"""Tests for /api/applications endpoints + dedup + transition + company auto-create."""
import pytest

# Bound at import time, before the autouse fixture below replaces the module attribute.
from backend.api.routes_applications import (
    _fetch_and_store_description as real_fetch_and_store_description,
)


def _seed_first_run(test_db):
    """Seed an empty dashboard_api_key row so the auth middleware allows requests (first-run mode)."""
    from backend.models.db import Setting
    test_db.add(Setting(key="dashboard_api_key", value=""))
    test_db.commit()


@pytest.fixture(autouse=True)
def _stub_background_tasks(monkeypatch):
    """Stub slow / network-hitting background work so tests are fast + isolated."""
    # _cache_job_page is what the route schedules in background; no-op it so tests don't hit the network.
    async def _noop_cache(*a, **kw):
        return None
    monkeypatch.setattr(
        "backend.api.routes_applications._cache_job_page",
        _noop_cache,
        raising=False,
    )
    # _fetch_and_store_description is the second task the route schedules.
    monkeypatch.setattr(
        "backend.api.routes_applications._fetch_and_store_description",
        _noop_cache,
        raising=False,
    )

    # fetch_h1b_for_company_id — scheduled when a new Company is auto-created.
    def _noop_h1b(*a, **kw):
        return None
    monkeypatch.setattr(
        "backend.analyzer.h1b_checker.fetch_h1b_for_company_id",
        _noop_h1b,
        raising=False,
    )


def test_create_application_returns_id(api_client, test_db):
    """POST /api/applications with valid body → 200 + id."""
    _seed_first_run(test_db)
    resp = api_client.post("/api/applications", json={
        "company": "Acme",
        "title": "Senior Product Manager",
        "url": "https://careers.acme.com/jobs/123",
    })
    assert resp.status_code in (200, 201), f"Unexpected status {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "id" in data
    assert data.get("status") == "applied"
    assert data.get("company") == "Acme"


def test_create_application_dedups_by_url(api_client, test_db):
    """Same URL with different UTM params → single Job row (external_id collision)."""
    _seed_first_run(test_db)
    api_client.post("/api/applications", json={
        "company": "Acme",
        "title": "Senior PM",
        "url": "https://careers.acme.com/jobs/123?utm_source=linkedin",
    })
    api_client.post("/api/applications", json={
        "company": "Acme",
        "title": "Senior PM",
        "url": "https://careers.acme.com/jobs/123?utm_campaign=x",
    })

    from backend.models.db import Job
    jobs = test_db.query(Job).filter(Job.company == "Acme").all()
    assert len(jobs) == 1, (
        f"Expected UTM-dedup to collapse both URLs into 1 job, got {len(jobs)}"
    )


def test_create_application_auto_creates_company(api_client, test_db):
    """POST with an unknown company name → Company row auto-created."""
    _seed_first_run(test_db)
    api_client.post("/api/applications", json={
        "company": "NewCoTestAbc",
        "title": "Senior PM",
        "url": "https://example.com/jobs/456",
    })

    from backend.models.db import Company
    co = test_db.query(Company).filter(Company.name == "NewCoTestAbc").first()
    assert co is not None, "Company should have been auto-created on apply"


def test_patch_application_status_records_transition(api_client, test_db):
    """PATCH status → Application.status_transitions has an entry with source='ui'."""
    _seed_first_run(test_db)
    created = api_client.post("/api/applications", json={
        "company": "Acme",
        "title": "Senior PM",
        "url": "https://x.com/1",
    }).json()
    aid = created["id"]

    resp = api_client.patch(f"/api/applications/{aid}", json={"status": "interview"})
    assert resp.status_code == 200

    from backend.models.db import Application
    app = test_db.query(Application).filter(Application.id == aid).first()
    assert app is not None
    assert app.status == "interview"
    assert any(
        t.get("to") == "interview" and t.get("source") == "ui"
        for t in (app.status_transitions or [])
    ), f"Expected ui-sourced transition to interview, got: {app.status_transitions}"


def test_list_applications_filters_by_status(api_client, test_db):
    """GET /api/applications?status=rejected returns only rejected apps."""
    _seed_first_run(test_db)
    api_client.post("/api/applications", json={
        "company": "Acme",
        "title": "PM1",
        "url": "https://x.com/1",
    })
    a2 = api_client.post("/api/applications", json={
        "company": "Acme",
        "title": "PM2",
        "url": "https://x.com/2",
    }).json()
    api_client.patch(f"/api/applications/{a2['id']}", json={"status": "rejected"})

    resp = api_client.get("/api/applications?status=rejected")
    assert resp.status_code == 200
    data = resp.json()
    # Response shape: {"total": N, "applications": [...]}
    apps = data.get("applications") if isinstance(data, dict) else data
    assert apps is not None, f"Unexpected response shape: {data}"
    titles = [a.get("title") for a in apps]
    assert "PM2" in titles, f"Expected PM2 in rejected list, got titles: {titles}"
    assert "PM1" not in titles, f"PM1 should not be in rejected list, got titles: {titles}"


def _add_application(test_db, external_id, title, company, source, status="applied"):
    from backend.models.db import Application, Job
    job = Job(external_id=external_id, title=title, company=company,
              source=source, url=f"https://example.com/{external_id}")
    test_db.add(job)
    test_db.flush()
    test_db.add(Application(job_id=job.id, status=status))
    test_db.commit()


def test_list_applications_filters_by_job_source_and_combines_filters(api_client, test_db):
    """Source uses Job.source and composes with status/company without changing totals."""
    _seed_first_run(test_db)
    _add_application(test_db, "direct-acme", "Direct role", "Acme", "direct")
    _add_application(test_db, "linkedin-acme", "LinkedIn role", "Acme", "jobspy_linkedin", "interview")
    _add_application(test_db, "indeed-other", "Indeed role", "Other", "jobspy_indeed", "interview")

    all_data = api_client.get("/api/applications").json()
    assert all_data["total"] == 3

    direct = api_client.get("/api/applications?source=direct").json()
    assert direct["total"] == 1
    assert {a["title"] for a in direct["applications"]} == {"Direct role"}

    jobspy = api_client.get("/api/applications?source=jobspy_linkedin,jobspy_indeed").json()
    assert jobspy["total"] == 2
    assert {a["source"] for a in jobspy["applications"]} == {"jobspy_linkedin", "jobspy_indeed"}

    combined = api_client.get(
        "/api/applications?status=interview&source=jobspy_linkedin&company=Acme"
    ).json()
    assert combined["total"] == 1
    assert combined["applications"][0]["title"] == "LinkedIn role"


def test_list_applications_source_filter_respects_pagination(api_client, test_db):
    _seed_first_run(test_db)
    _add_application(test_db, "direct-1", "First", "Acme", "direct")
    _add_application(test_db, "direct-2", "Second", "Acme", "direct")
    _add_application(test_db, "jobspy-1", "Other source", "Acme", "jobspy_linkedin")

    page = api_client.get("/api/applications?source=direct&limit=1&offset=1").json()
    assert page["total"] == 2
    assert len(page["applications"]) == 1
    assert page["applications"][0]["source"] == "direct"


def test_create_application_twice_returns_409(api_client, test_db):
    """A second POST for the same posting must not overwrite the first."""
    _seed_first_run(test_db)
    body = {"company": "Acme", "title": "PM", "url": "https://acme.example/jobs/1", "notes": "first"}
    first = api_client.post("/api/applications", json=body)
    assert first.status_code == 200
    second = api_client.post("/api/applications", json={**body, "notes": "second", "status": "interview"})
    assert second.status_code == 409
    assert second.json()["detail"]["application_id"] == first.json()["id"]
    apps = api_client.get("/api/applications").json()
    apps = apps if isinstance(apps, list) else apps.get("applications", [])
    mine = [a for a in apps if a.get("id") == first.json()["id"]]
    assert len(mine) == 1 and mine[0]["status"] == "applied"
    assert mine[0].get("notes", "first") == "first"


# ── JD fetched at log time ───────────────────────────────────────────────────

def test_log_schedules_a_description_fetch(api_client, test_db, monkeypatch):
    """A hand-logged job has no description, so the route queues one fetch."""
    import backend.api.routes_applications as ra
    seen = []

    async def _spy(job_id, url):
        seen.append((job_id, url))

    _seed_first_run(test_db)
    monkeypatch.setattr(ra, "_fetch_and_store_description", _spy, raising=False)
    resp = api_client.post("/api/applications", json={
        "url": "https://acme.com/jobs/7", "title": "Senior PM", "company": "Acme"})
    assert resp.status_code == 200
    assert len(seen) == 1
    assert seen[0][1] == "https://acme.com/jobs/7"


def test_log_skips_the_fetch_when_the_job_already_has_a_description(api_client, test_db, monkeypatch):
    """The job row exists from a scrape and carries a JD, so nothing is fetched."""
    import uuid
    import backend.api.routes_applications as ra
    from backend.models.db import Job
    from backend.scraper._shared.dedup import make_external_id

    url = "https://acme.com/jobs/8"
    test_db.add(Job(id=uuid.uuid4(), external_id=make_external_id("Acme", "Senior PM", url),
                    company="Acme", title="Senior PM", url=url, status="saved",
                    description="Scraped JD"))
    test_db.commit()

    seen = []

    async def _spy(job_id, url):
        seen.append(job_id)

    _seed_first_run(test_db)
    monkeypatch.setattr(ra, "_fetch_and_store_description", _spy, raising=False)
    resp = api_client.post("/api/applications", json={
        "url": url, "title": "Senior PM", "company": "Acme"})
    assert resp.status_code == 200
    assert seen == []


@pytest.mark.asyncio
async def test_fetch_and_store_description_writes_the_job(test_db, monkeypatch):
    """The task delegates to the tailoring resolver, which persists the fetched text."""
    import uuid
    from backend.models.db import Job

    job = Job(id=uuid.uuid4(), external_id=uuid.uuid4().hex, company="Acme",
              title="Senior PM", url="https://acme.com/jobs/9", status="applied")
    test_db.add(job)
    test_db.commit()

    async def fake_fetch(url):
        return "Clean ATS JD for a Senior PM."

    monkeypatch.setattr("backend.scraper.ats._descriptions._fetch_job_description", fake_fetch)
    await real_fetch_and_store_description(str(job.id), job.url)

    test_db.expire_all()
    assert test_db.query(Job).filter(Job.id == job.id).first().description == "Clean ATS JD for a Senior PM."
