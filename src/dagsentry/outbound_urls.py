"""Validation for outbound endpoints that receive credentials."""

from __future__ import annotations

import ipaddress
from collections.abc import Collection
from urllib.parse import urlsplit


def require_credential_endpoint_security(
    value: str,
    *,
    provider: str,
    trusted_http_hosts: Collection[str] = (),
) -> None:
    """Require HTTPS except for localhost or an explicitly trusted HTTP host."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError(f"{provider} endpoint must be an HTTP(S) URL")
    if parsed.scheme == "https":
        return

    host = parsed.hostname.rstrip(".").lower()
    trusted_hosts = {item.rstrip(".").lower() for item in trusted_http_hosts}
    if _is_loopback_host(host) or host in trusted_hosts:
        return
    raise ValueError(
        f"{provider} endpoint must use HTTPS unless it is localhost or explicitly trusted"
    )


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
