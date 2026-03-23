"""Optional restrictions on admin-configured upstream base URLs (SSRF mitigation)."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def validate_upstream_base_url(url: str, *, block_private_hosts: bool) -> None:
    """
    Validate ``url`` for use as Tautulli / Sonarr / Plex base URL.

    When ``block_private_hosts`` is true, reject loopback, link-local, private, and
    similar addresses when the host is a literal IP. Hostnames that only resolve
    privately are not detected (DNS rebinding); use network segmentation for that case.

    Raises:
        ValueError: If the URL is unusable or blocked.
    """
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("URL is empty")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https")
    host = parsed.hostname
    if not host:
        raise ValueError("URL must include a host")
    host_l = host.lower().strip()
    if not block_private_hosts:
        return
    if host_l == "localhost" or host_l.endswith(".localhost"):
        raise ValueError("localhost is not allowed when BLOCK_PRIVATE_UPSTREAM_URLS is enabled")
    try:
        ip = ipaddress.ip_address(host_l)
    except ValueError:
        return
    if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved:
        raise ValueError("That IP range is not allowed when BLOCK_PRIVATE_UPSTREAM_URLS is enabled")
    if ip.is_multicast or ip.is_unspecified:
        raise ValueError("That IP range is not allowed when BLOCK_PRIVATE_UPSTREAM_URLS is enabled")
