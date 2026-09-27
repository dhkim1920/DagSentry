from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from tests.test_incident_api import request

WEB = Path(__file__).resolve().parents[1] / "src/dagsentry/web"
TABLER = "vendor/tabler-1.6.0/tabler.min.css"


class ShellParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.nodes: list[tuple[str, dict[str, str | None]]] = []
        self.feed(source)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes.append((tag, dict(attrs)))


def test_tabler_is_exact_self_hosted_release_with_no_remote_runtime_assets(
    settings: Settings, session_factory: SessionFactory
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/").text
    response = request(app, "GET", f"/ui/{TABLER}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert hashlib.sha256(response.content).hexdigest() == (
        "60bc8b4432e7778016e3675b79181f441fa617ac8b5387f8372a860cbb459a82"
    )
    assert shell.index(f'href="/ui/{TABLER}"') < shell.index('href="/ui/app.css')
    assert 'data-bs-theme="dark"' in shell
    assert "@import" not in response.text
    assert "@font-face" not in response.text
    urls = re.findall(r"url\((.*?)\)", response.text)
    assert urls
    assert all(url.strip("\"'").startswith("data:image/svg+xml") for url in urls)
    for tag, attrs in ShellParser(shell).nodes:
        if tag in {"script", "link"}:
            assert (attrs.get("src") or attrs.get("href") or "").startswith("/ui/")
        assert "style" not in attrs
        assert not any(key.startswith("on") for key in attrs)
    scripts = [attrs for tag, attrs in ShellParser(shell).nodes if tag == "script"]
    assert len(scripts) == 1
    assert (scripts[0]["src"] or "").startswith("/ui/app.js?")


def test_tabler_license_notices_are_served_with_packaged_assets(
    settings: Settings, session_factory: SessionFactory
) -> None:
    app = create_app(settings, session_factory)
    for name, owner in [
        ("tabler", "The Tabler Authors"),
        ("bootstrap", "The Bootstrap Authors"),
        ("normalize", "Nicolas Gallagher and Jonathan Neal"),
    ]:
        response = request(app, "GET", f"/ui/vendor/tabler-1.6.0/LICENSE.{name}")
        assert response.status_code == 200
        assert owner in response.text
        assert "Permission is hereby granted, free of charge" in response.text
        assert 'THE SOFTWARE IS PROVIDED "AS IS"' in response.text
    notices = WEB.parents[2] / "THIRD_PARTY_NOTICES.md"
    assert "Tabler Core 1.6.0" in notices.read_text()
    assert "ApexCharts" in notices.read_text()


def test_tabler_controls_preserve_dom_hooks_labels_and_native_dialogs() -> None:
    source = (WEB / "index.html").read_text()
    nodes = ShellParser(source).nodes
    ids = [attrs["id"] for _, attrs in nodes if "id" in attrs]
    assert len(ids) == len(set(ids)), "Duplicate event/label targets"
    script = (WEB / "app.js").read_text()
    for target in re.findall(r'document.querySelector\("#([\w-]+)"\)', script):
        assert target in ids, f"Missing JavaScript target: {target}"
    labels = {attrs["for"] for tag, attrs in nodes if tag == "label" and "for" in attrs}
    for tag, attrs in nodes:
        classes = (attrs.get("class") or "").split()
        if tag == "table":
            assert {"table", "table-vcenter", "card-table"} <= set(classes)
        if tag in {"input", "textarea", "select"} and attrs.get("type") != "hidden":
            expected = (
                "form-select"
                if tag == "select"
                else ("form-check-input" if attrs.get("type") == "checkbox" else "form-control")
            )
            assert expected in classes, attrs
            assert attrs.get("id") in labels or attrs.get("aria-label"), attrs
        if tag == "button":
            assert "btn" in classes or "btn-close" in classes, attrs
        for attr in ("aria-labelledby", "aria-describedby", "aria-controls"):
            for target in (attrs.get(attr) or "").split():
                assert target in ids, (attrs, target)
    dialogs = [attrs for tag, attrs in nodes if tag == "dialog"]
    assert {attrs["id"] for attrs in dialogs} == {
        "transition-dialog",
        "human-diagnosis-dialog",
        "password-reset-dialog",
    }
    for attrs in dialogs:
        assert {"modal", "modal-blur"} <= set((attrs.get("class") or "").split())
        assert "open" not in attrs
    assert source.count('class="modal-dialog"') == 3
    assert source.count('class="modal-body"') == 3
    assert source.count('class="transition-form modal-content"') == 3
    assert source.count('class="dialog-actions modal-footer"') == 3
    assert ".showModal()" in script
    assert "innerHTML" not in script
