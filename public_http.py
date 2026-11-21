"""Bounded fetches for article and asset URLs supplied by public feeds."""
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests

HEADERS = {"User-Agent": "robo-daily-digest/1.0 (robotics news reader)"}


def public_url(url):
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
            return False
        if port not in (80, 443):
            return False
        addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(info[4][0]).is_global for info in addresses)
    except (ValueError, OSError):
        return False


def fetch_public(url, byte_limit, content_types):
    for _ in range(4):
        if not public_url(url):
            return None
        with requests.get(url, headers=HEADERS, timeout=(5, 8), stream=True, allow_redirects=False) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("Location", ""))
                continue
            response.raise_for_status()
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if not any(mime.startswith(prefix) for prefix in content_types):
                return None
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > byte_limit:
                return None
            buffer = bytearray()
            for chunk in response.iter_content(64 * 1024):
                buffer.extend(chunk)
                if len(buffer) > byte_limit:
                    return None
            return url, bytes(buffer)
    return None
