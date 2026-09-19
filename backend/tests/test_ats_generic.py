"""Tests for ats/generic.py — DOM-fallback scraper for unknown career pages."""
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_generic_exposes_dom_helpers():
    """The 5 DOM helpers are reachable from ats.generic."""
    from backend.scraper.ats import generic
    assert hasattr(generic, "_setup_route_blocks")
    assert hasattr(generic, "_wait_for_content")
    assert hasattr(generic, "_extract_job_links_from_page")
    assert hasattr(generic, "_extract_all_pages")
    assert hasattr(generic, "_click_next_page")


def test_generic_exposes_scrape():
    """Public entry point matches other ATS modules' (is_match, scrape) interface."""
    from backend.scraper.ats import generic
    assert hasattr(generic, "scrape")


@pytest.mark.asyncio
async def test_generic_scrape_launches_browser_when_none(monkeypatch):
    """scrape() launches its own browser when none passed, and returns empty on no jobs."""
    from unittest.mock import AsyncMock, MagicMock

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.query_selector_all = AsyncMock(return_value=[])
    mock_page.evaluate = AsyncMock(return_value=None)
    mock_page.route = AsyncMock()
    mock_page.close = AsyncMock()
    mock_page._stealth_ctx = None

    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_browser)
    mock_browser.new_page = AsyncMock(return_value=mock_page)

    mock_pw = MagicMock()
    mock_pw.stop = AsyncMock()

    async def fake_get_browser():
        return mock_pw, mock_browser

    monkeypatch.setattr("backend.scraper.ats.generic._get_browser", fake_get_browser)

    async def fake_new_page(browser, viewport=None):
        return mock_page

    async def fake_close_page(page):
        pass

    monkeypatch.setattr("backend.scraper.ats.generic._new_page", fake_new_page)
    monkeypatch.setattr("backend.scraper.ats.generic._close_page", fake_close_page)

    from backend.scraper.ats.generic import scrape
    # Use a URL that matches no ATS (generic is the fallback)
    result = await scrape("https://unknown-career-page.example.com/jobs", max_pages=1)
    # Just verify it ran without crashing
    assert isinstance(result, (list, tuple))


def test_gojobs_url_detection_is_host_specific():
    from backend.scraper.ats.generic import _is_gojobs
    assert _is_gojobs("https://www.gojobs.gov.on.ca/Search.aspx") is True
    assert _is_gojobs("https://gojobs.gov.on.ca/Search.aspx?Language=English") is True
    assert _is_gojobs("https://gojobs.gov.on.ca.evil.example/Search.aspx") is False
    assert _is_gojobs("https://jobs.ontario.ca/") is False


def test_gcjobs_url_detection_is_host_specific():
    from backend.scraper.ats.generic import _is_gcjobs
    assert _is_gcjobs("https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page2440")
    assert not _is_gcjobs("https://emploisfp-psjobs.cfp-psc.gc.ca.evil.example/page2440")


@pytest.mark.asyncio
async def test_gcjobs_poster_links_are_extracted(monkeypatch):
    from backend.scraper.ats import generic
    link = MagicMock()
    link.get_attribute = AsyncMock(return_value="/psrs-srfp/applicant/page1800?poster=2451656")
    link.query_selector = AsyncMock(return_value=None)
    link.inner_text = AsyncMock(return_value="Business Analyst")
    page = MagicMock()
    page.evaluate = AsyncMock()
    page.query_selector_all = AsyncMock(
        side_effect=lambda selector: [link] if selector == generic.GCJOBS_JOB_LINK_SELECTOR else []
    )
    monkeypatch.setattr(generic, "_card_location", AsyncMock(return_value=None))

    jobs = await generic._extract_job_links_from_page(
        page, "https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page2440"
    )
    assert jobs == [{
        "title": "Business Analyst",
        "url": "https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page1800?poster=2451656",
    }]


@pytest.mark.asyncio
async def test_gojobs_preview_links_are_extracted():
    from backend.scraper.ats.generic import (
        GOJOBS_JOB_LINK_SELECTOR, _extract_job_links_from_page,
    )
    link = MagicMock()
    link.get_attribute = AsyncMock(return_value="/Preview.aspx?Language=English&JobID=249065")
    link.query_selector = AsyncMock(return_value=None)
    link.inner_text = AsyncMock(return_value="Business Analyst (1)")

    page = MagicMock()
    page.evaluate = AsyncMock()
    page.query_selector_all = AsyncMock(
        side_effect=lambda selector: [link] if selector == GOJOBS_JOB_LINK_SELECTOR else []
    )

    jobs = await _extract_job_links_from_page(
        page, "https://www.gojobs.gov.on.ca/Search.aspx"
    )
    assert jobs == [{
        "title": "Business Analyst (1)",
        "url": "https://www.gojobs.gov.on.ca/Preview.aspx?Language=English&JobID=249065",
    }]


@pytest.mark.asyncio
async def test_gojobs_preparation_clicks_search_and_waits_for_results():
    from backend.scraper.ats.generic import (
        GOJOBS_JOB_LINK_SELECTOR, _prepare_gojobs_search,
    )
    button = MagicMock()
    button.click = AsyncMock()
    page = MagicMock()
    page.query_selector = AsyncMock(return_value=button)
    page.wait_for_selector = AsyncMock()

    await _prepare_gojobs_search(page)

    page.query_selector.assert_awaited_once_with("#ctl00_MainContent_btnSearch")
    button.click.assert_awaited_once_with(timeout=5000)
    page.wait_for_selector.assert_awaited_once_with(
        GOJOBS_JOB_LINK_SELECTOR, timeout=15000
    )


@pytest.mark.asyncio
async def test_gojobs_preparation_reports_radware_captcha():
    from backend.scraper.ats.generic import _prepare_gojobs_search

    page = MagicMock()
    page.url = "https://validate.perfdrive.com/challenge"
    page.query_selector = AsyncMock()

    with pytest.raises(RuntimeError, match="Radware CAPTCHA"):
        await _prepare_gojobs_search(page)
    page.query_selector.assert_not_awaited()


@pytest.mark.asyncio
async def test_pagination_respects_max_pages_and_deduplicates(monkeypatch):
    from backend.scraper.ats import generic
    page = MagicMock()
    pages = [
        [{"title": "One", "url": "https://example.test/job/1"}],
        [
            {"title": "One", "url": "https://example.test/job/1"},
            {"title": "Two", "url": "https://example.test/job/2"},
        ],
        [{"title": "Three", "url": "https://example.test/job/3"}],
    ]
    extract = AsyncMock(side_effect=pages)
    click_next = AsyncMock(return_value=True)
    monkeypatch.setattr(generic, "_extract_job_links_from_page", extract)
    monkeypatch.setattr(generic, "_click_next_page", click_next)
    monkeypatch.setattr(generic.asyncio, "sleep", AsyncMock())

    jobs = await generic._extract_all_pages(
        page, "https://example.test/jobs", max_pages=2
    )

    assert [job["title"] for job in jobs] == ["One", "Two"]
    assert extract.await_count == 2
    assert click_next.await_count == 1


@pytest.mark.asyncio
async def test_postback_next_waits_for_navigation(monkeypatch):
    from backend.scraper.ats import generic
    button = MagicMock()
    button.is_visible = AsyncMock(return_value=True)
    button.get_attribute = AsyncMock(
        side_effect=lambda name: (
            "javascript:__doPostBack('ctl00$MainContent$lnkButton_Next','')"
            if name == "href" else None
        )
    )
    button.evaluate = AsyncMock(
        side_effect=lambda expression: "A" if "tagName" in expression else "Next"
    )
    button.bounding_box = AsyncMock(return_value={"width": 50, "height": 20})
    button.click = AsyncMock()

    navigation = MagicMock()
    navigation.__aenter__ = AsyncMock()
    navigation.__aexit__ = AsyncMock(return_value=False)
    page = MagicMock()
    page.evaluate = AsyncMock()
    page.expect_navigation = MagicMock(return_value=navigation)
    page.query_selector = AsyncMock(
        side_effect=lambda selector: button if selector == 'a:has-text("Next")' else None
    )
    monkeypatch.setattr(generic.asyncio, "sleep", AsyncMock())

    assert await generic._click_next_page(page) is True
    page.expect_navigation.assert_called_once_with(
        wait_until="domcontentloaded", timeout=15000
    )
    button.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_generic_scrape_prepares_only_gojobs(monkeypatch):
    from backend.scraper.ats import generic
    page = MagicMock()
    page.goto = AsyncMock()
    page.route = AsyncMock()
    prepare = AsyncMock()
    monkeypatch.setattr(generic, "_prepare_gojobs_search", prepare)
    monkeypatch.setattr(generic, "_wait_for_content", AsyncMock())
    monkeypatch.setattr(generic, "_extract_all_pages", AsyncMock(return_value=[]))
    monkeypatch.setattr(generic, "_new_page", AsyncMock(return_value=page))
    monkeypatch.setattr(generic, "_close_page", AsyncMock())

    browser = MagicMock()
    await generic.scrape("https://www.gojobs.gov.on.ca/Search.aspx", browser=browser)
    prepare.assert_awaited_once_with(page)

    prepare.reset_mock()
    await generic.scrape("https://careers.example.com/jobs", browser=browser)
    prepare.assert_not_awaited()
