"""Small HTTP adapter for public iCIMS career listings.

The normal iCIMS URL is an iframe shell and its browser endpoint currently
returns a Human Verification page to Playwright.  The public ``in_iframe=1``
HTML is server rendered and contains the same job cards, so reading that page
is both smaller and more reliable than automating the shell.
"""
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

import httpx
from bs4 import BeautifulSoup

from backend.scraper._shared.filters import _validate_job


def is_icims(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "").lower().endswith(".icims.com")
    except (TypeError, ValueError):
        return False


def _listing_url(url: str, page: int = 0) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["in_iframe"] = "1"
    if page:
        query["pr"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


async def scrape(url: str, debug: bool = False, max_pages: int = 5):
    jobs = []
    rejected = []
    seen = set()
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
        for page_num in range(max_pages):
            response = await client.get(_listing_url(url, page_num))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select(".iCIMS_JobCardItem")
            if not cards:
                # An empty Brampton board is valid; a challenge/error page is not.
                title = (soup.title.get_text(" ", strip=True) if soup.title else "")
                if "verification" in title.lower():
                    raise RuntimeError("iCIMS returned a Human Verification page")
                break
            added = 0
            for card in cards:
                anchor = card.select_one('a[href*="/jobs/"][href*="/job"]')
                if not anchor:
                    continue
                href = urljoin(str(response.url), anchor.get("href") or "")
                title_el = anchor.select_one("h1, h2, h3, h4")
                title = (title_el or anchor).get_text(" ", strip=True)
                reason = _validate_job(title, href, structural=True)
                if reason:
                    if debug:
                        rejected.append({"title": title, "url": href, "reason": reason})
                    continue
                if href in seen:
                    continue
                seen.add(href)
                job = {"title": title, "url": href}
                date_el = card.select_one(".header.right span[title], time[datetime]")
                if date_el:
                    job["posted_at"] = date_el.get("datetime") or date_el.get("title") or date_el.get_text(" ", strip=True)
                text = card.get_text(" ", strip=True)
                marker = "Location:"
                if marker in text:
                    location = text.split(marker, 1)[1].split("Hours of Work", 1)[0].strip()
                    if location and len(location) <= 120:
                        job["location"] = location
                jobs.append(job)
                added += 1
            if added == 0 or not soup.find("link", rel="next"):
                break
    return (jobs, rejected) if debug else jobs
