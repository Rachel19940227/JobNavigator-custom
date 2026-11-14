"""Conservative second-layer dedup for multi-location and cross-board copies."""
from datetime import datetime, timedelta, timezone

from backend.models.db import Job
from backend.scraper._shared.dedup import (
    find_duplicate_posting,
    make_content_hash,
)


NOW = datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc)
LONG_BODY = " ".join(f"requirement{i}" for i in range(100))


def _job(key, *, source="jobspy_linkedin", location="toronto", body=LONG_BODY,
         posted=NOW, title="Business Analyst"):
    return Job(
        external_id=key,
        content_hash=make_content_hash("Acme", title),
        company="Acme",
        title=title,
        url=f"https://jobs.example/{key}",
        source=source,
        description=body,
        location=f"{location}, Ontario, Canada",
        loc_country="CA",
        loc_region="ON",
        loc_city=location,
        posted_at=posted,
        status="new",
    )


def test_identical_full_description_dedups_multi_location_copy(test_db):
    existing = _job("linkedin-toronto")
    test_db.add(existing)
    test_db.commit()

    candidate = _job("linkedin-ottawa", location="ottawa")
    assert find_duplicate_posting(test_db, candidate).id == existing.id


def test_cross_source_shortened_description_dedups_same_recent_city(test_db):
    existing = _job("linkedin", body=LONG_BODY + " " + " ".join(f"boilerplate{i}" for i in range(30)))
    test_db.add(existing)
    test_db.commit()

    candidate = _job(
        "workday",
        source="jobright",
        body=LONG_BODY,
        posted=NOW + timedelta(minutes=40),
    )
    assert find_duplicate_posting(test_db, candidate).id == existing.id


def test_same_source_different_descriptions_are_not_merged(test_db):
    test_db.add(_job("first", body=" ".join(f"alpha{i}" for i in range(100))))
    test_db.commit()

    candidate = _job("second", body=" ".join(f"beta{i}" for i in range(100)))
    assert find_duplicate_posting(test_db, candidate) is None


def test_cross_source_different_city_is_not_merged(test_db):
    test_db.add(_job("toronto"))
    test_db.commit()

    candidate = _job("ottawa", source="jobright", location="ottawa")
    # An exact full description remains conclusive even across locations; use a
    # shortened but highly overlapping body to exercise the location guard.
    candidate.description = " ".join(f"requirement{i}" for i in range(95))
    assert find_duplicate_posting(test_db, candidate) is None


def test_cross_source_same_explicit_requisition_id_is_merged(test_db):
    existing = _job("linkedin", body=" ".join(f"alpha{i}" for i in range(100)))
    existing.url = "https://employer.example/jobs/REQ-12345"
    test_db.add(existing)
    test_db.commit()

    candidate = _job(
        "aggregator", source="jobright",
        body=" ".join(f"beta{i}" for i in range(100)),
    )
    candidate.url = "https://other.example/opening?requisition=12345"
    assert find_duplicate_posting(test_db, candidate).id == existing.id


def test_old_cross_source_same_title_is_not_merged(test_db):
    test_db.add(_job("old", posted=NOW - timedelta(days=30)))
    test_db.commit()

    candidate = _job("new", source="jobright", body=" ".join(f"requirement{i}" for i in range(95)))
    assert find_duplicate_posting(test_db, candidate) is None
