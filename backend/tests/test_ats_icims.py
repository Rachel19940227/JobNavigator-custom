import pytest


def test_icims_detection_is_host_specific():
    from backend.scraper.ats.icims import is_icims
    assert is_icims("https://careers-peelregion.icims.com/jobs/search")
    assert not is_icims("https://icims.com.evil.example/jobs/search")


@pytest.mark.asyncio
async def test_icims_reads_server_rendered_cards(monkeypatch):
    from backend.scraper.ats import icims

    html = """
      <html><head><title>Jobs</title></head><body>
      <ul><li class="iCIMS_JobCardItem">
        <a href="/jobs/123/business-analyst/job?in_iframe=1"><h3>Business Analyst</h3></a>
        <div>Location: Mississauga, ON Hours of Work: 35</div>
      </li></ul></body></html>
    """

    class Response:
        text = html
        url = "https://careers.example.icims.com/jobs/search?in_iframe=1"
        def raise_for_status(self): pass

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return Response()

    monkeypatch.setattr(icims.httpx, "AsyncClient", lambda **kwargs: Client())
    jobs = await icims.scrape("https://careers.example.icims.com/jobs/search")
    assert jobs == [{
        "title": "Business Analyst",
        "url": "https://careers.example.icims.com/jobs/123/business-analyst/job?in_iframe=1",
        "location": "Mississauga, ON",
    }]
