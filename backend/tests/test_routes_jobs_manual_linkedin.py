from datetime import datetime, timezone

from backend.api.routes_jobs import _indeed_job_key_from_url, _linkedin_job_id_from_url
from backend.models.db import Job, Setting


def _seed_first_run(test_db):
    test_db.add(Setting(key="dashboard_api_key", value=""))
    test_db.commit()


def test_linkedin_job_id_accepts_job_urls_and_rejects_other_hosts():
    assert _linkedin_job_id_from_url("https://www.linkedin.com/jobs/view/1234567890") == "1234567890"
    assert _linkedin_job_id_from_url("https://ca.linkedin.com/jobs/view/analyst-at-example-1234567890/") == "1234567890"
    assert _linkedin_job_id_from_url("https://www.linkedin.com/jobs/search/?currentJobId=1234567890") == "1234567890"
    assert _linkedin_job_id_from_url("https://example.com/jobs/view/1234567890") is None


def test_indeed_job_key_accepts_viewjob_and_rejects_other_hosts():
    url = "https://ca.indeed.com/viewjob?jk=bdd9e8a50a2629ff"
    assert _indeed_job_key_from_url(url) == "bdd9e8a50a2629ff"
    assert _indeed_job_key_from_url("https://indeed.com.evil.example/viewjob?jk=bdd9e8a50a2629ff") is None


def test_manual_indeed_import_uses_jobspy_lookup(api_client, test_db, monkeypatch):
    _seed_first_run(test_db)
    monkeypatch.setattr(
        "backend.api.routes_jobs._fetch_indeed_job",
        lambda job_key, url: {
            "title": "Business Systems Analyst",
            "company": "Example Co",
            "location": "Toronto, ON",
            "description": "Business systems analysis, CIAM requirements, stakeholders, and delivery. " * 3,
            "url": url,
            "posted_at": datetime(2026, 9, 24, tzinfo=timezone.utc),
        },
    )
    response = api_client.post(
        "/api/jobs/import-url",
        json={"url": "https://ca.indeed.com/viewjob?jk=bdd9e8a50a2629ff"},
    )
    assert response.status_code == 200
    assert response.json()["source"] == "manual_url"
    assert response.json()["title"] == "Business Systems Analyst"


def test_manual_linkedin_import_creates_normal_job(api_client, test_db, monkeypatch):
    _seed_first_run(test_db)
    monkeypatch.setattr(
        "backend.api.routes_jobs._fetch_public_linkedin_job",
        lambda linkedin_id: {
            "linkedin_job_id": linkedin_id,
            "title": "Business Analyst",
            "company": "Example Co",
            "location": "Toronto, Ontario, Canada",
            "description": "Analyze requirements and work with stakeholders.",
            "url": f"https://www.linkedin.com/jobs/view/{linkedin_id}",
            "posted_at": datetime(2026, 9, 6, tzinfo=timezone.utc),
        },
    )

    response = api_client.post(
        "/api/jobs/import-linkedin-url",
        json={"url": "https://www.linkedin.com/jobs/view/1234567890"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["new"] is True
    assert data["title"] == "Business Analyst"
    assert data["description"].startswith("Analyze requirements")
    assert data["source"] == "manual_linkedin"
    assert data["status"] == "new"
    assert test_db.query(Job).filter(Job.linkedin_job_id == "1234567890").count() == 1

    duplicate = api_client.post(
        "/api/jobs/import-linkedin-url",
        json={"url": "https://ca.linkedin.com/jobs/view/analyst-1234567890"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["new"] is False
    assert test_db.query(Job).count() == 1


def test_manual_linkedin_import_rejects_non_linkedin_url(api_client, test_db):
    _seed_first_run(test_db)
    response = api_client.post(
        "/api/jobs/import-linkedin-url",
        json={"url": "https://example.com/jobs/view/1234567890"},
    )
    assert response.status_code == 400
