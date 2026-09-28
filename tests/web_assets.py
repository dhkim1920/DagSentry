"""Read the shipped asset graph for cross-file UI contract assertions."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from fastapi import FastAPI

from tests.test_incident_api import request


def asset_source(app: FastAPI, path: str) -> str:
    seen: set[str] = set()

    def read(asset: str) -> str:
        if asset in seen:
            return ""
        seen.add(asset)
        response = request(app, "GET", asset)
        assert response.status_code == 200, asset
        assert response.headers["cache-control"] == "no-store", asset
        content_type = response.headers["content-type"].split(";")[0]
        expected_types = (
            {"text/css"}
            if asset.endswith(".css")
            else {"text/javascript", "application/javascript"}
        )
        assert content_type in expected_types, asset
        source = response.text
        dependencies = re.findall(
            r'^import .*? from "([^"]+)";|@import url\("([^"]+)"\);', source, re.M
        )
        parts = [source]
        for module, stylesheet in dependencies:
            parts.append(read(urljoin(asset, module or stylesheet)))
        return "\n".join(parts)

    return read(path)
