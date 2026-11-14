"""JobSpy-backed keyword search source — searches LinkedIn, Indeed, ZipRecruiter, Google Jobs via a single multi-board request."""
import asyncio
import hashlib
import json
import logging
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.db import Application, SessionLocal, Search, Job, Setting, get_existing_external_ids
from backend.scraper._shared.dedup import (
    find_duplicate_posting,
    get_recent_applied_content_hashes,
    make_external_id,
    make_content_hash,
)

logger = logging.getLogger("jobnavigator.scraper.sources.jobspy")
_PRECISE_LINKEDIN_POSTED_AT = {}


def _description_fingerprint(description: str | None) -> str:
    """Stable full-JD fingerprint; layout-only whitespace changes do not mint a new posting."""
    normalized = re.sub(r"\s+", " ", description or "").strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest() if normalized else ""


def _applied_posting_fingerprints(db) -> set[tuple[str, str]]:
    """Company/title + full-JD fingerprints for jobs already in the application funnel."""
    rows = (db.query(Job.content_hash, Job.description)
            .join(Application, Application.job_id == Job.id)
            .filter(Job.content_hash.isnot(None), Job.description.isnot(None))
            .all())
    return {(content_hash, fingerprint) for content_hash, description in rows
            if (fingerprint := _description_fingerprint(description))}


def _patch_linkedin_new_date_class():
    """JobSpy 1.1.82 only recognizes LinkedIn's old date CSS class.

    LinkedIn currently emits ``job-search-card__listdate--new``. Add the old
    class before JobSpy parses each card so its existing date parser works.
    """
    try:
        from jobspy.linkedin import LinkedIn
        if getattr(LinkedIn, "_jobnavigator_date_patch", False):
            return
        original = LinkedIn._process_job

        def patched(self, job_card, job_id, full_descr):
            tag = job_card.find("time", class_="job-search-card__listdate--new")
            if tag is not None:
                classes = list(tag.get("class", []))
                if "job-search-card__listdate" not in classes:
                    classes.append("job-search-card__listdate")
                    tag["class"] = classes
                label = tag.get_text(" ", strip=True).lower()
                match = re.search(r"(\d+)\s+(minute|hour|day)s?\s+ago", label)
                if match:
                    amount = int(match.group(1))
                    unit = match.group(2)
                    delta = timedelta(**{f"{unit}s": amount})
                    _PRECISE_LINKEDIN_POSTED_AT[f"li-{job_id}"] = datetime.now(timezone.utc) - delta
            return original(self, job_card, job_id, full_descr)

        LinkedIn._process_job = patched
        LinkedIn._jobnavigator_date_patch = True
    except Exception as exc:
        logger.warning("Could not apply LinkedIn posted-date compatibility patch: %s", exc)


def _clean(v):
    """Null-safe scalar → clean string, or None when empty; a bare str(cell) on a pandas NaN yields the literal text 'nan', which passes emptiness checks downstream and broke resume tailoring."""
    import pandas as pd
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass  # non-scalar (list/array) — fall through and stringify
    s = str(v).strip()
    return s or None


def _apply_h1b_inline(job, db=None, company_lookup=None, phrases=None, loop=None) -> None:
    """Sync-safe H-1B JD scan: runs check_job_h1b in an event loop for asyncio.to_thread() workers; batch callers should pass a shared `loop` plus `company_lookup`/`phrases` to skip per-job DB lookups."""
    import asyncio as _asyncio
    from backend.analyzer.h1b_checker import check_job_h1b

    try:
        own_loop = loop is None
        if own_loop:
            loop = _asyncio.new_event_loop()
        try:
            loop.run_until_complete(check_job_h1b(job, db=db, company_lookup=company_lookup, phrases=phrases))
        finally:
            if own_loop:
                loop.close()
    except Exception as e:
        logger.warning(f"_apply_h1b_inline failed for job {getattr(job, 'id', '?')}: {e}")


# jobspy swallows per-board failures into its own loggers instead of the return
# value, so a hard-failed board looks identical to one that found nothing; capture those records here.

_JOBSPY_SITE_KEYS = {
    "linkedin": "linkedin",
    "indeed": "indeed",
    "ziprecruiter": "zip_recruiter",
    "google": "google",
    "glassdoor": "glassdoor",
    "bayt": "bayt",
    "naukri": "naukri",
    "bdjobs": "bdjobs",
}


# jobspy logs each board under two spellings (e.g. "LinkedIn" vs "Linkedin"); _site_key()
# lowercases both to the same key, so this map only needs the site values we configure.
_JOBSPY_LOGGER_DISPLAY = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "zip_recruiter": "ZipRecruiter",
    "google": "Google",
    "glassdoor": "Glassdoor",
    "bayt": "Bayt",
    "naukri": "Naukri",
    "bdjobs": "BDJobs",
}


def _site_key(logger_name: str):
    """"JobSpy:ZipRecruiter" -> "zip_recruiter". None for non-site loggers."""
    name = str(logger_name or "")
    if ":" not in name:
        return None
    suffix = name.split(":", 1)[1].strip()
    if not suffix:
        return None
    flat = suffix.lower().replace("_", "").replace("-", "")
    return _JOBSPY_SITE_KEYS.get(flat, suffix.lower())


def _condense_error(msg: str) -> str:
    """"ZipRecruiter response status code 403" -> "403"; anything else trimmed."""
    text = " ".join(str(msg).split())
    m = re.search(r"\b([45]\d\d)\b", text)
    if m:
        return m.group(1)
    return text[:120]


class _SourceLogCapture(logging.Handler):
    """Keeps the first WARNING+ record each JobSpy board logger emits."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.errors = {}

    def emit(self, record):
        try:
            if not str(record.name).startswith("JobSpy"):
                return
            key = _site_key(record.name)
            if not key or key in self.errors:
                return
            self.errors[key] = _condense_error(record.getMessage())
        except Exception:  # a logging handler must never break the scrape
            pass


def _capture_targets(sites=None):
    """Every logger the capture handler must attach to for one scrape_jobs() call; jobspy's create_logger() sets propagate=False, so a handler on the root logger alone would miss board records."""
    names = set()
    for raw in list(logging.Logger.manager.loggerDict):
        if str(raw).startswith("JobSpy"):
            names.add(str(raw))
    for site in (sites or []):
        display = _JOBSPY_LOGGER_DISPLAY.get(str(site).lower())
        if display:
            names.add(f"JobSpy:{display}")
    return [logging.getLogger()] + [logging.getLogger(n) for n in sorted(names)]


@contextmanager
def _capture_source_errors(sites=None):
    """Attach the capture handler to every JobSpy board logger for one call, without touching level/propagate since that would leak into other requests sharing the process."""
    handler = _SourceLogCapture()
    targets = _capture_targets(sites)
    for lg in targets:
        lg.addHandler(handler)
    try:
        yield handler
    finally:
        for lg in targets:
            lg.removeHandler(handler)


def _merge_source_errors(breakdown: dict, errors: dict) -> dict:
    """Fold captured per-board errors into the seen/new breakdown."""
    for key, err in (errors or {}).items():
        breakdown.setdefault(key, {"seen": 0, "new": 0})["error"] = err
    return breakdown


def get_setting_value(db: Session, key: str, default: str = "") -> str:
    """Read a single Setting row's value by key, returning ``default`` if not set."""
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else default


def apply_title_filters(jobs_df, include_keywords: list, exclude_keywords: list):
    """Filter jobs by title include/exclude keywords (whole-word matching); returns (kept_df, rejected_df)."""
    import pandas as pd

    if jobs_df is None or jobs_df.empty:
        return jobs_df, pd.DataFrame()

    mask = pd.Series(True, index=jobs_df.index)

    if include_keywords:
        pattern = "|".join(include_keywords)
        mask &= jobs_df["title"].str.contains(pattern, case=False, na=False)

    if exclude_keywords:
        pattern = "|".join(r'\b' + re.escape(kw) + r'\b' for kw in exclude_keywords)
        mask &= ~jobs_df["title"].str.contains(pattern, case=False, na=False, regex=True)

    return jobs_df[mask], jobs_df[~mask]


def apply_company_filter(jobs_df, company_filter: list):
    """Filter to specific companies if filter is non-empty (exact match, case-insensitive)."""
    if not company_filter or jobs_df is None or jobs_df.empty:
        return jobs_df
    cf_set = {cf.lower() for cf in company_filter}
    return jobs_df[jobs_df["company"].str.lower().isin(cf_set)]


def _run_sync(search, proxy_url: str = None) -> dict:
    """Execute a single JobSpy search and return results dict."""
    start_time = time.time()
    # One entry per configured board so a failure is visible next to boards that worked;
    # seeded here (not after the call) so failure paths can still say what was asked for.
    breakdown = {}

    try:
        from jobspy import scrape_jobs
        _patch_linkedin_new_date_class()

        # Build source list — filter out 'direct' which is Playwright
        sources = [s for s in (search.sources or []) if s != "direct"]
        if not sources:
            return {"jobs_found": 0, "new_jobs": 0, "ignored_jobs": 0,
                    "error": "No JobSpy sources configured", "source_breakdown": {}}
        breakdown = {s: {"seen": 0, "new": 0} for s in sources}

        kwargs = {
            "site_name": sources,
            "search_term": search.search_term or "",
            "location": search.location or "United States",
            "results_wanted": search.results_wanted or 50,
            "hours_old": search.hours_old or 24,
            "job_type": search.job_type or "fulltime",
            "country_indeed": "USA",
            "linkedin_fetch_description": "linkedin" in sources,
            "verbose": 2,
        }

        if search.is_remote is not None:
            kwargs["is_remote"] = search.is_remote

        if proxy_url:
            kwargs["proxies"] = [proxy_url]

        logger.info(f"Running JobSpy search: {search.name} — term='{search.search_term}', sources={sources}")
        with _capture_source_errors(sources) as capture:
            jobs_df = scrape_jobs(**kwargs)
        _merge_source_errors(breakdown, capture.errors)

        if jobs_df is None or jobs_df.empty:
            duration = time.time() - start_time
            return {"jobs_found": 0, "new_jobs": 0, "ignored_jobs": 0, "error": None,
                    "duration": duration, "source_breakdown": breakdown}

        # Apply filters (merge global title exclude with per-search)
        db_excl = SessionLocal()
        try:
            global_title_excl = json.loads(get_setting_value(db_excl, "title_exclude_global", "[]"))
        except Exception:
            global_title_excl = []
        finally:
            db_excl.close()
        merged_exclude = list(set((search.title_exclude_keywords or []) + global_title_excl))
        jobs_df, rejected_df = apply_title_filters(
            jobs_df,
            search.title_include_keywords or [],
            merged_exclude,
        )
        jobs_df = apply_company_filter(jobs_df, search.company_filter or [])

        # Company exclude (global=full match, per-search=full match,
        # plus active companies when search.exclude_active_companies is on)
        db_excl = SessionLocal()
        try:
            from backend.scraper._shared.filters import build_search_exclude_sets
            global_exclude_set, search_exclude_set = build_search_exclude_sets(db_excl, search)
            if (global_exclude_set or search_exclude_set) and jobs_df is not None and not jobs_df.empty:
                before = len(jobs_df)
                def _excl(name):
                    nl = str(name).lower()
                    if nl in global_exclude_set:
                        return True
                    return nl in search_exclude_set
                mask = jobs_df["company"].apply(_excl)
                jobs_df = jobs_df[~mask]
                if len(jobs_df) < before:
                    logger.info(f"Company exclude removed {before - len(jobs_df)} jobs")
        finally:
            db_excl.close()

        jobs_found = len(jobs_df)

        # Per-board `seen` counted after filtering, so sum(seen) == jobs_found.
        if jobs_df is not None and not jobs_df.empty and "site" in jobs_df.columns:
            for site_name, count in jobs_df["site"].value_counts().items():
                key = str(site_name).lower()
                breakdown.setdefault(key, {"seen": 0, "new": 0})["seen"] = int(count)

        # Save to DB, dedup via external_id
        db = SessionLocal()
        new_jobs = 0
        # Rejected rows are still written as status "ignored" so the next run dedups them;
        # count them too, since otherwise storing 8 rows could report only "6 seen, +6 new".
        ignored_jobs = 0
        # Hoisted per run (not per job): one event loop, one company lookup, one parsed phrase list —
        # avoids a fresh loop + full company scan + Settings JSON-parse thousands of times per scrape.
        import asyncio as _asyncio
        from backend.models.db import build_company_lookup
        from backend.analyzer.h1b_checker import load_exclusion_phrases
        h1b_loop = _asyncio.new_event_loop()
        try:
            existing_ids = get_existing_external_ids(db)
            applied_fingerprints = _applied_posting_fingerprints(db)
            recent_applied_hashes = get_recent_applied_content_hashes(db)
            existing_posted = dict(db.query(Job.external_id, Job.posted_at).all())
            company_lookup = build_company_lookup(db)
            phrases = load_exclusion_phrases(db)

            for _, row in jobs_df.iterrows():
                company = _clean(row.get("company")) or ""
                title = _clean(row.get("title")) or ""
                url = _clean(row.get("job_url")) or ""
                ext_id = make_external_id(company, title, url)

                if ext_id in existing_ids:
                    parsed_posted = _row_posted_at(row)
                    description = _clean(row.get("description"))
                    updates = {}
                    if parsed_posted:
                        updates[Job.posted_at] = parsed_posted
                    if description:
                        updates[Job.description] = description
                        from backend.analyzer.h1b_checker import scan_jd_for_h1b_flags
                        body_result = scan_jd_for_h1b_flags(description, phrases)
                        if body_result["jd_flag"]:
                            updates[Job.h1b_jd_flag] = True
                            updates[Job.h1b_jd_snippet] = body_result["jd_snippet"]
                            updates[Job.status] = "ignored"
                    if updates:
                        db.query(Job).filter(Job.external_id == ext_id).update(updates)
                    continue

                content_hash = make_content_hash(company, title)
                description = _clean(row.get("description"))
                site = str(row.get("site", "")).lower()
                if site == "linkedin" and content_hash in recent_applied_hashes:
                    logger.info(
                        "Skipping recently applied LinkedIn duplicate: %s @ %s (%s)",
                        title, company, url,
                    )
                    continue
                if (content_hash, _description_fingerprint(description)) in applied_fingerprints:
                    logger.info(
                        "Skipping already-applied duplicate: %s @ %s (%s)",
                        title, company, url,
                    )
                    continue

                source_map = {
                    "linkedin": "jobspy_linkedin",
                    "indeed": "jobspy_indeed",
                    "zip_recruiter": "jobspy_zip_recruiter",
                    "google": "jobspy_google",
                }
                source = source_map.get(site, f"jobspy_{site}")

                job = Job(
                    external_id=ext_id,
                    content_hash=content_hash,
                    company=company,
                    title=title,
                    url=url,
                    source=source,
                    search_id=search.id,
                    description=description,
                    location=_clean(row.get("location")),
                    # JobSpy's own `is_remote` is a substring test over the whole
                    # description, so "remote state" (Terraform) and "remote dev
                    # environments" mark a job remote. It is deliberately unused;
                    # `work_from_home_type` below is Indeed's structured field and
                    # is trustworthy. Everything else falls to the JD cascade.
                    remote=None,
                    status="new",
                    posted_at=_row_posted_at(row),
                    seen=False,
                    saved=False,
                )

                # Extract salary if present in JobSpy results
                min_amount = row.get("min_amount")
                max_amount = row.get("max_amount")
                if min_amount and str(min_amount) != "nan":
                    try:
                        job.salary_min = int(float(min_amount))
                        job.salary_source = "posting"
                    except (ValueError, TypeError):
                        pass
                if max_amount and str(max_amount) != "nan":
                    try:
                        job.salary_max = int(float(max_amount))
                        job.salary_source = "posting"
                    except (ValueError, TypeError):
                        pass

                # H-1B check + salary extraction inline, using the shared loop/lookup/phrases hoisted above.
                _apply_h1b_inline(job, db, company_lookup=company_lookup, phrases=phrases, loop=h1b_loop)
                try:
                    from backend.analyzer.salary_extractor import apply_salary_to_job
                    from backend.analyzer.work_arrangement import apply_arrangement_to_job
                    from backend.analyzer.location import apply_location_to_job
                    company_obj = company_lookup.get(company.strip().lower())
                    apply_salary_to_job(job, getattr(job, "_h1b_median", None))
                    wfh = row.get("work_from_home_type")
                    apply_arrangement_to_job(
                        job, structured=wfh if wfh and str(wfh) != "nan" else None)
                    apply_location_to_job(job)
                except Exception as analysis_err:
                    logger.warning(f"Inline salary analysis failed for {title}: {analysis_err}")

                duplicate = find_duplicate_posting(db, job)
                if duplicate:
                    logger.info(
                        "Skipping duplicate posting: %s @ %s (%s duplicates %s)",
                        title, company, url, duplicate.url,
                    )
                    continue

                if job.h1b_jd_flag:
                    _phrase = getattr(job, "_h1b_matched_phrase", None) or "?"
                    logger.info(f"Skipping job (body exclusion): {title} @ {company} — matched phrase: {_phrase!r}")
                    continue

                try:
                    with db.begin_nested():
                        db.add(job)
                        db.flush()
                    new_jobs += 1
                    breakdown.setdefault(site or "unknown", {"seen": 0, "new": 0})["new"] += 1
                    existing_ids.add(ext_id)
                except IntegrityError:
                    logger.debug(f"Duplicate external_id for '{title}' at {company}, skipping")
                    continue
                except Exception as e:
                    logger.warning(f"Insert failed for '{title}' at {company} ({url}): {e}")
                    continue

            # Save filtered-out jobs as "ignored" for dedup purposes
            if rejected_df is not None and not rejected_df.empty:
                for _, row in rejected_df.iterrows():
                    company = _clean(row.get("company")) or ""
                    title = _clean(row.get("title")) or ""
                    url = _clean(row.get("job_url")) or ""
                    ext_id = make_external_id(company, title, url)

                    if ext_id in existing_ids:
                        continue

                    site = str(row.get("site", "")).lower()
                    source_map = {
                        "linkedin": "jobspy_linkedin",
                        "indeed": "jobspy_indeed",
                        "zip_recruiter": "jobspy_zip_recruiter",
                        "google": "jobspy_google",
                    }
                    source = source_map.get(site, f"jobspy_{site}")

                    job = Job(
                        external_id=ext_id,
                        company=company,
                        title=title,
                        url=url,
                        source=source,
                        search_id=search.id,
                        description=_clean(row.get("description")),
                        location=_clean(row.get("location")),
                        status="ignored",
                        seen=False,
                        saved=False,
                    )
                    try:
                        with db.begin_nested():
                            db.add(job)
                            db.flush()
                        existing_ids.add(ext_id)
                        ignored_jobs += 1
                        entry = breakdown.setdefault(site or "unknown", {"seen": 0, "new": 0})
                        entry["filtered"] = entry.get("filtered", 0) + 1
                    except IntegrityError:
                        continue
                    except Exception as e:
                        logger.warning(f"Insert failed for ignored job '{title}' at {company} ({url}): {e}")
                        continue

            db.commit()

            search_obj = db.query(Search).filter(Search.id == search.id).first()
            if search_obj:
                search_obj.last_run_at = datetime.now(timezone.utc)
                db.commit()

        finally:
            h1b_loop.close()
            db.close()

        duration = time.time() - start_time

        from backend.activity import log_activity
        log_activity("scrape", f"JobSpy search '{search.name}': {new_jobs} new / {jobs_found} found"
                     + (f" ({ignored_jobs} filtered out)" if ignored_jobs else "")
                     + f" in {duration:.1f}s")

        return {"jobs_found": jobs_found, "new_jobs": new_jobs, "ignored_jobs": ignored_jobs,
                "error": None, "duration": duration, "source_breakdown": breakdown}

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"JobSpy search failed for '{search.name}': {e}")

        from backend.activity import log_activity
        log_activity("scrape", f"JobSpy search '{search.name}' failed: {e}")

        return {"jobs_found": 0, "new_jobs": 0, "ignored_jobs": 0, "error": str(e),
                "duration": duration, "source_breakdown": breakdown}


async def run(search, proxy_url: str = None) -> dict:
    """Async entry point — offloads the synchronous JobSpy call to a thread."""
    return await asyncio.to_thread(_run_sync, search, proxy_url)


def _parse_posted_at(value):
    """Normalize JobSpy's pandas/datetime/string date_posted value to UTC."""
    if value is None:
        return None
    try:
        import pandas as pd
        if pd.isna(value):
            return None
        parsed = pd.to_datetime(value, utc=True)
        return parsed.to_pydatetime()
    except Exception:
        return None


def _row_posted_at(row):
    """Prefer LinkedIn's relative timestamp, which includes hours/minutes."""
    precise = _PRECISE_LINKEDIN_POSTED_AT.get(str(row.get("id", "")))
    return precise or _parse_posted_at(row.get("date_posted"))
