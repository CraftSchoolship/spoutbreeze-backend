"""Tests for the Jinja2 email-template renderer."""

from app.services.email_template_renderer import render_email


def test_organizer_added_renders_event_name():
    html = render_email(
        "organizer_added",
        {
            "title": "You were added as an organizer",
            "body": "ignored when event_name is set",
            "data": {
                "event_name": "Demo Stream",
                "event_url": "https://example.com/events/abc",
            },
        },
    )
    assert "<html" in html.lower()
    assert "You were added as an organizer" in html
    assert "Demo Stream" in html
    assert "https://example.com/events/abc" in html


def test_unknown_type_falls_back_to_default():
    html = render_email(
        "totally_made_up_type",
        {"title": "Hello", "body": "World", "data": {}},
    )
    assert "Hello" in html
    assert "World" in html
    assert "<html" in html.lower()


def test_event_reminder_renders_with_minimal_data():
    html = render_email(
        "event_reminder",
        {
            "title": "Event reminder",
            "body": "Starts soon",
            "data": {},
        },
    )
    assert "Event reminder" in html
    assert "Starts soon" in html


def test_html_is_autoescaped():
    html = render_email(
        "totally_unknown",
        {
            "title": "<script>alert(1)</script>",
            "body": "safe body",
            "data": {},
        },
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
