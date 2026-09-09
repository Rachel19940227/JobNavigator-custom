"""One-off/safe backfill for missing JobSpy LinkedIn descriptions."""
import asyncio
import json
import re
from types import SimpleNamespace

from jobspy.linkedin import LinkedIn
from jobspy.model import DescriptionFormat

from backend.analyzer.h1b_checker import scan_jd_for_h1b_flags
from backend.models.db import Job, SessionLocal, Setting


async def main():
    db = SessionLocal()
    client = LinkedIn()
    client.scraper_input = SimpleNamespace(description_format=DescriptionFormat.MARKDOWN)
    try:
        row = db.query(Setting).filter(Setting.key == "body_exclusion_phrases").first()
        phrases = json.loads(row.value) if row and row.value else []
        jobs = db.query(Job).filter(
            Job.source == "jobspy_linkedin",
            (Job.description.is_(None)) | (Job.description == ""),
        ).all()
        updated = excluded = failed = 0
        for job in jobs:
            match = re.search(r"/jobs/view/(\d+)", job.url or "")
            if not match:
                failed += 1
                continue
            details = await asyncio.to_thread(client._get_job_details, match.group(1))
            description = (details or {}).get("description")
            if not description:
                failed += 1
                continue
            job.description = description
            result = scan_jd_for_h1b_flags(description, phrases)
            if result["jd_flag"]:
                job.h1b_jd_flag = True
                job.h1b_jd_snippet = result["jd_snippet"]
                job.status = "ignored"
                excluded += 1
            updated += 1
            db.commit()
        print({"updated": updated, "excluded": excluded, "failed": failed})
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
