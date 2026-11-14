"""Every installed résumé template renders the optional certifications section."""
from pathlib import Path

import pytest

pytest.importorskip("jinja2")

from backend.api.routes_resumes import _render_html


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "resume_templates"


@pytest.mark.parametrize("template_name", sorted(
    path.name for path in TEMPLATES_DIR.iterdir() if (path / "template.html.j2").exists()
))
def test_template_renders_certification_name_issuer_and_month(template_name):
    html = _render_html({
        "header": {"name": "Rachel Zu", "contact_items": []},
        "certifications": [{
            "name": "Project Management Professional",
            "date": "2024-09",
            "issuer": "Project Management Institute",
        }],
    }, template_name, "letter")

    assert "Certifications" in html
    assert "Project Management Professional" in html
    assert "Project Management Institute" in html
    assert "09/2024" in html
