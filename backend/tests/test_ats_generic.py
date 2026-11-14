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


def test_ottawa_url_detection_and_result_endpoint_are_host_specific():
    from backend.scraper.ats.generic import _is_ottawa_jobs, _ottawa_search_url
    source = "https://jobs-emplois.ottawa.ca/city-jobs/viewalljobs/"
    assert _is_ottawa_jobs(source)
    assert _ottawa_search_url(source) == (
        "https://jobs-emplois.ottawa.ca/city-jobs/search/?q="
    )
    evil = "https://jobs-emplois.ottawa.ca.evil.example/city-jobs/viewalljobs/"
    assert not _is_ottawa_jobs(evil)
    assert _ottawa_search_url(evil) == evil


def test_mississauga_view_all_uses_search_results_endpoint():
    from backend.scraper.ats.generic import _ottawa_search_url
    assert _ottawa_search_url("https://jobs.mississauga.ca/viewalljobs/") == (
        "https://jobs.mississauga.ca/search/?q="
    )


def test_icims_search_uses_the_real_iframe_listing():
    from backend.scraper.ats.generic import _icims_listing_url
    source = "https://careers-peelregion.icims.com/jobs/search"
    assert _icims_listing_url(source) == source + "?in_iframe=1"
    assert _icims_listing_url(source + "?in_iframe=1") == source + "?in_iframe=1"


@pytest.mark.asyncio
async def test_peoplesoft_signin_page_is_an_explicit_error():
    from backend.scraper.ats.generic import _raise_if_access_blocked
    page = MagicMock()
    page.title = AsyncMock(return_value="Careers")
    page.query_selector = AsyncMock(return_value=MagicMock())
    with pytest.raises(RuntimeError, match="sign-in or verification"):
        await _raise_if_access_blocked(
            page,
            "https://jobposting.ottawahospital.on.ca/psc/jobposting/EMPLOYEE/HRMS/c/jobs",
        )


def test_peoplesoft_detail_url_preserves_site_and_sets_opening():
    from backend.scraper.ats.generic import _peoplesoft_job_url
    url = _peoplesoft_job_url(
        "https://jobposting.ottawahospital.on.ca/psc/x?Page=HRS_APP_SCHJOB_FL&SiteId=2",
        "112982",
    )
    assert "JobOpeningId=112982" in url
    assert "SiteId=2" in url
    assert "Page=HRS_APP_JBPST_FL" in url


@pytest.mark.asyncio
async def test_ottawa_extracts_only_job_title_links(monkeypatch):
    from backend.scraper.ats import generic
    job = MagicMock()
    job.get_attribute = AsyncMock(
        return_value="/city-jobs/job/Ottawa-ON-Business-Analyst/123/"
    )
    job.query_selector = AsyncMock(return_value=None)
    job.inner_text = AsyncMock(return_value="Business Analyst")
    language = MagicMock()
    language.get_attribute = AsyncMock(return_value="?locale=fr_CA")
    language.query_selector = AsyncMock(return_value=None)
    language.inner_text = AsyncMock(return_value="Français")

    page = MagicMock()
    page.evaluate = AsyncMock()
    page.query_selector_all = AsyncMock(
        side_effect=lambda selector: (
            [job] if selector == generic.OTTAWA_JOB_LINK_SELECTOR else
            [language] if selector == '[role="listitem"] a' else []
        )
    )
    monkeypatch.setattr(generic, "_card_location", AsyncMock(return_value=None))

    jobs = await generic._extract_job_links_from_page(
        page, "https://jobs-emplois.ottawa.ca/city-jobs/viewalljobs/"
    )
    assert jobs == [{
        "title": "Business Analyst",
        "url": "https://jobs-emplois.ottawa.ca/city-jobs/job/Ottawa-ON-Business-Analyst/123/",
    }]


@pytest.mark.asyncio
async def test_ottawa_pagination_uses_next_numbered_page():
    from backend.scraper.ats.generic import _click_next_page
    link = MagicMock()
    link.click = AsyncMock()
    navigation = MagicMock()
    navigation.__aenter__ = AsyncMock()
    navigation.__aexit__ = AsyncMock(return_value=False)
    page = MagicMock()
    page.url = "https://jobs-emplois.ottawa.ca/city-jobs/search/?q="
    page.query_selector = AsyncMock(return_value=link)
    page.expect_navigation = MagicMock(return_value=navigation)

    result = await _click_next_page(page, debug=True)

    assert result["clicked"] is True
    assert result["clicked_via"] == "City of Ottawa next numbered page"
    page.query_selector.assert_any_await(
        'ul.pagination li.active + li a[title^="Page "]'
    )
    link.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_successfactors_search_trigger_submits_empty_search():
    from backend.scraper.ats.generic import _prepare_successfactors_search
    button = MagicMock()
    button.wait_for = AsyncMock()
    button.click = AsyncMock()
    page = MagicMock()
    page.url = "https://career17.sapsf.com/career?company=TTCPRODUCTION"
    page.query_selector = AsyncMock(return_value=None)
    page.get_by_role = MagicMock(return_value=button)
    page.wait_for_selector = AsyncMock()

    await _prepare_successfactors_search(page)

    page.get_by_role.assert_called_once_with(
        "button", name="Search Jobs", exact=True
    )
    button.click.assert_awaited_once_with(timeout=5000)


@pytest.mark.asyncio
async def test_successfactors_numbered_pagination_ignores_show_more():
    from backend.scraper.ats import generic
    marker = MagicMock()
    next_link = MagicMock()
    next_link.click = AsyncMock()
    navigation = MagicMock()
    navigation.__aenter__ = AsyncMock()
    navigation.__aexit__ = AsyncMock(return_value=False)
    page = MagicMock()
    page.url = "https://jobs.hydroone.com/search/"
    page.expect_navigation = MagicMock(return_value=navigation)
    page.query_selector = AsyncMock(
        side_effect=lambda selector: (
            marker if selector == generic.SUCCESSFACTORS_JOB_LINK_SELECTOR else
            next_link if selector == 'ul.pagination li.active + li a[title^="Page "]' else
            None
        )
    )

    result = await generic._click_next_page(page, debug=True)

    assert result["clicked_via"] == "SuccessFactors next numbered page"
    next_link.click.assert_awaited_once()


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
        GOJOBS_JOB_LINK_SELECTOR, timeout=25000
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
