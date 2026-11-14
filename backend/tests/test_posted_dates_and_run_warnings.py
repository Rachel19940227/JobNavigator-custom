import asyncio
from datetime import datetime, timezone

import pytest

from backend.scraper._shared.dates import parse_posted_at


def test_parse_posted_at_supports_board_formats_without_inventing_dates():
    now = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)
    assert parse_posted_at("2 hours ago", now=now) == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    assert parse_posted_at("09/24/2026", now=now) == datetime(2026, 9, 24, tzinfo=timezone.utc)
    assert parse_posted_at(1_795_000_000_000, now=now).tzinfo == timezone.utc
    assert parse_posted_at("not supplied", now=now) is None


@pytest.mark.asyncio
async def test_background_run_can_finish_as_warning(test_db):
    import backend.job_monitor as monitor
    from backend.models.db import JobRun

    async def worker():
        monitor.mark_run_warning("0 jobs seen")
        return "Board - 0 seen, +0 new"

    run_id = monitor.launch_background("warning_test", worker, trigger="manual")
    for _ in range(20):
        await asyncio.sleep(0.01)
        test_db.expire_all()
        row = test_db.query(JobRun).filter(JobRun.id == run_id).first()
        if row and row.status != "running":
            break

    assert row.status == "warning"
    assert "0 jobs seen" in row.result_summary
    assert row.error is None
