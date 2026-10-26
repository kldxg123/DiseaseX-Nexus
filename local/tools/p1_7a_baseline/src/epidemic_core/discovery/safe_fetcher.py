from __future__ import annotations
# mypy: disable-error-code="no-untyped-def,attr-defined"

import hashlib
import http.client
import ipaddress
import socket
import ssl
import urllib.parse
from dataclasses import dataclass


class FetchBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    canonical_url: str
    endpoint_sha256: str
    payload: bytes
    content_type: str
    redirects: tuple[str, ...]


def canonicalize(url: str) -> tuple[str, urllib.parse.SplitResult]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443):
        raise FetchBlocked("UNSAFE_URL")
    if parsed.username or parsed.password:
        raise FetchBlocked("URL_USERINFO_BLOCKED")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise FetchBlocked("IP_LITERAL_BLOCKED")
    if parsed.fragment:
        parsed = parsed._replace(fragment="")
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    sensitive = {"token", "key", "apikey", "api_key", "signature", "sig", "password", "credential"}
    if any(k.casefold() in sensitive for k, _ in query):
        raise FetchBlocked("CREDENTIAL_URL_BLOCKED")
    assert parsed.hostname is not None
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    parsed = parsed._replace(netloc=host, path=parsed.path or "/", query=urllib.parse.urlencode(sorted(query)))
    return urllib.parse.urlunsplit(parsed), parsed


def public_addresses(host: str, resolver=socket.getaddrinfo) -> tuple[str, ...]:
    addresses = sorted({x[4][0] for x in resolver(host, 443, 0, socket.SOCK_STREAM)})
    if not addresses:
        raise FetchBlocked("DNS_EMPTY")
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise FetchBlocked("SSRF_BLOCKED")
    return tuple(addresses)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, pinned: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.pinned = pinned

    def connect(self) -> None:
        raw = socket.create_connection((self.pinned, 443), self.timeout)
        peer = raw.getpeername()[0]
        if ipaddress.ip_address(peer) != ipaddress.ip_address(self.pinned):
            raw.close()
            raise FetchBlocked("DNS_REBINDING_BLOCKED")
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class SafeFetcher:
    def __init__(self, maximum_bytes: int = 50 * 1024 * 1024):
        self.maximum_bytes = maximum_bytes

    def fetch(self, url: str) -> FetchResult:
        redirects: list[str] = []
        for _ in range(4):
            canonical, parsed = canonicalize(url)
            addresses = public_addresses(parsed.hostname or "")
            conn = _PinnedHTTPS(parsed.hostname or "", addresses[0], 30)
            path = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
            conn.request("GET", path, headers={"Host": parsed.hostname or "", "Accept":"text/csv,application/json", "Accept-Encoding":"identity", "User-Agent":"epidemic-openclaw-discovery/1.0"})
            response = conn.getresponse()
            if 300 <= response.status < 400:
                location = response.getheader("location")
                response.read(); conn.close()
                if not location:
                    raise FetchBlocked("UNSAFE_REDIRECT")
                target = urllib.parse.urljoin(canonical, location)
                target_canonical, target_parsed = canonicalize(target)
                if target_parsed.scheme != "https":
                    raise FetchBlocked("HTTPS_DOWNGRADE_BLOCKED")
                redirects.append(hashlib.sha256(target_canonical.encode()).hexdigest())
                url = target_canonical
                continue
            if response.status != 200:
                response.read(); conn.close()
                raise FetchBlocked("HTTP_STATUS")
            content_type = (response.getheader("content-type") or "").split(";", 1)[0].lower()
            if content_type not in {"text/csv", "application/csv", "application/json", "text/plain"}:
                response.read(); conn.close()
                raise FetchBlocked("UNSUPPORTED_FORMAT")
            chunks=[]; size=0
            while True:
                chunk=response.read(65536)
                if not chunk: break
                size += len(chunk)
                if size > self.maximum_bytes:
                    conn.close(); raise FetchBlocked("FILE_TOO_LARGE")
                chunks.append(chunk)
            conn.close(); payload=b"".join(chunks)
            return FetchResult(canonical, hashlib.sha256(canonical.encode()).hexdigest(), payload, content_type, tuple(redirects))
        raise FetchBlocked("TOO_MANY_REDIRECTS")
