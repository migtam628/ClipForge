"""Regression checks for ClipForge's responsive navigation and creator UI.

These are intentionally static: they run without a browser, media files, or a
running job. Visual acceptance is covered by the manual mobile QA checklist.
"""

from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "clipforge" / "static"
HTML = (STATIC / "index.html").read_text()
CSS = (STATIC / "style.css").read_text()
THEMES = (STATIC / "themes.css").read_text()
JS = (STATIC / "app.js").read_text()


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attributes):
        self.tags.append((tag, dict(attributes)))


ELEMENTS = Elements()
ELEMENTS.feed(HTML)


def by_id(identifier):
    return [(tag, attr) for tag, attr in ELEMENTS.tags
            if attr.get("id") == identifier]


def test_mobile_shell_has_complete_navigation_and_accessible_dialog():
    assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in HTML
    assert len(by_id("mobile-more-button")) == 1
    assert len(by_id("mobile-more-dialog")) == 1
    assert len(by_id("mobile-more-close")) == 1
    tag, more_button = by_id("mobile-more-button")[0]
    assert tag == "button" and more_button["aria-haspopup"] == "dialog"
    assert more_button["aria-expanded"] == "false"
    assert more_button["aria-controls"] == "mobile-more-dialog"
    assert by_id("mobile-more-dialog")[0][0] == "dialog"

    # Dedicated routes must remain reachable on phones, not just on desktop.
    mobile_links = {attr["href"] for tag, attr in ELEMENTS.tags
                    if tag == "a" and ("mobile-nav-link" in attr.get("class", "")
                                      or "mobile-more-link" in attr.get("class", ""))}
    assert {"/studio", "/library", "/publishing", "/activity",
            "/posting-settings", "/analytics", "/settings", "/api/docs"} <= mobile_links


def test_mobile_appearance_keeps_all_three_supported_themes():
    assert len(by_id("mobile-appearance-theme")) == 1
    assert by_id("mobile-appearance-theme")[0][1]["data-theme-select"] is None
    assert "document.querySelectorAll('[data-theme-select]')" in (STATIC / "theme.js").read_text()
    assert "mobile-page-title" in JS


def test_mobile_navigation_tracks_route_and_dialog_lifecycle():
    assert "mobileMoreDialog.showModal()" in JS
    assert "mobileMoreDialog.close()" in JS
    assert "mobileMoreButton.setAttribute('aria-expanded'" in JS
    assert "mobile-nav-link[data-page], .mobile-more-link[data-page]" in JS
    assert "morePages.has(currentPage)" in JS
    assert "min-width: 901px" in JS


def test_small_screen_media_workbench_and_bottom_action_bar():
    assert "--mobile-nav-height" in CSS
    assert "env(safe-area-inset-bottom)" in CSS
    assert ".mobile-bottom-nav" in CSS
    assert ".studio-journey { display: flex; overflow-x: auto" in CSS
    assert "scrollIntoView({block: 'start'" in JS
    assert "position: sticky; bottom: calc(var(--mobile-nav-height)" in CSS
    assert "[hidden] { display: none!important; }" in CSS
    assert ".framing-workbench,.framing-workbench.has-presets { grid-template-columns: minmax(0,1fr); }" in CSS
    assert ".timeline-workspace,.clip-editor.timeline-expanded .timeline-workspace { grid-template-columns: minmax(0,1fr); }" in CSS
    assert "html[data-theme=\"control\"] .creator-panel {padding:14px;}" in THEMES
