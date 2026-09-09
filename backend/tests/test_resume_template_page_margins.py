"""Regression tests for repeated PDF page margins in resume templates."""

from pathlib import Path

from backend.api.routes_resumes import _render_html


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "resume_templates"


def test_all_resume_templates_use_print_page_margins():
    templates = list(TEMPLATES_DIR.glob("*/template.html.j2"))
    assert templates

    for template in templates:
        css = template.read_text()
        page_rule = css.split("@page", 1)[1].split("}", 1)[0]

        assert "margin: 0;" not in page_rule, template
        assert "@media print" in css, template
        assert ".page { width: auto; padding: 0; margin: 0; }" in css, template


def test_resume_spacing_overrides_are_injected_when_configured():
    html = _render_html(
        {
            "header": {"name": "Test", "contact_items": []},
            "layout": {"line_spacing": 1.35, "paragraph_spacing": 6},
        },
        "garamond_alt",
        "a4",
    )

    assert "line-height: 1.35 !important" in html
    assert "margin-bottom: 6pt !important" in html


def test_resume_spacing_defaults_and_invalid_values_do_not_override_template():
    default_html = _render_html(
        {"header": {"name": "Test", "contact_items": []}},
        "garamond_alt",
        "a4",
    )
    invalid_html = _render_html(
        {
            "header": {"name": "Test", "contact_items": []},
            "layout": {"line_spacing": "bad", "paragraph_spacing": 100},
        },
        "garamond_alt",
        "a4",
    )

    assert ".page, .page * { line-height:" not in default_html
    assert ".page, .page * { line-height:" not in invalid_html
    assert ".page p, .page li" not in invalid_html
