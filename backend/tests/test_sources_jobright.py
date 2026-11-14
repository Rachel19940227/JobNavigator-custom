"""Tests for sources/jobright.py."""
import asyncio
from datetime import datetime, timedelta, timezone


def test_sources_jobright_exposes_run_and_preview():
    from backend.scraper.sources import jobright
    assert hasattr(jobright, "run")
    assert hasattr(jobright, "preview")


def test_run_is_async():
    from backend.scraper.sources import jobright
    assert asyncio.iscoroutinefunction(jobright.run)


def test_preview_is_async():
    from backend.scraper.sources import jobright
    assert asyncio.iscoroutinefunction(jobright.preview)


def test_jobright_location_filter_accepts_target_cities_or_fully_remote():
    from backend.scraper.sources.jobright import _matches_location_filter

    configured = "Toronto, Ottawa, Remote"
    assert _matches_location_filter({"location": "Toronto, ON", "work_model": "Hybrid"}, configured)
    assert _matches_location_filter({"location": "Ottawa, Ontario", "work_model": "Onsite"}, configured)
    assert _matches_location_filter({"location": "Canada", "work_model": "Remote"}, configured)
    assert not _matches_location_filter({"location": "Vancouver, BC", "work_model": "Hybrid"}, configured)
    assert not _matches_location_filter({"location": "Montreal, QC", "work_model": "Onsite"}, configured)


def test_jobright_parses_exact_publish_time_and_filters_24_hours():
    from backend.scraper.sources.jobright import _parse_job, _within_hours

    parsed = _parse_job({"jobResult": {
        "jobId": "j1", "jobTitle": "Business Analyst",
        "publishTime": "2026-09-25 12:00:00", "publishTimeDesc": "1 hour ago",
    }})
    assert parsed["posted_at"] == datetime(2026, 9, 25, 12, tzinfo=timezone.utc)

    now = datetime(2026, 9, 25, 13, tzinfo=timezone.utc)
    assert _within_hours(parsed, 24, now)
    assert not _within_hours({"posted_at": now - timedelta(hours=24, seconds=1)}, 24, now)
    assert not _within_hours({"posted_at": None}, 24, now)
    assert _within_hours({"posted_at": None}, 0, now)
