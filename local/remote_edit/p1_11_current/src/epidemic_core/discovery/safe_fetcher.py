from __future__ import annotations

# mypy: disable-error-code="no-untyped-def,attr-defined"
import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass


class FetchBlocked(RuntimeError):
    def __init__(self, code: str, *, http_status: int | None = None, network_attempted: bool = False):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.network_attempted = network_attempted


@dataclass(frozen=True)
class FetchResult:
    canonical_url: str
    endpoint_sha256: str
    payload: bytes
    content_type: str
    redirects: tuple[str, ...]
    domain: str
    http_status: int
    elapsed_ms: int
    response_sha256: str
    etag: str | None = None
    last_modified: str | None = None


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
    def __init__(self, maximum_bytes: int = 50 * 1024 * 1024, timeout_seconds: float = 30):
        self.maximum_bytes = maximum_bytes
        self.timeout_seconds = timeout_seconds

    def fetch(self, url: str, headers: Mapping[str, str] | None = None) -> FetchResult:
        started = time.monotonic()
        redirects: list[str] = []
        allowed_headers = {"Accept", "X-Subscription-Token"}
        supplied = dict(headers or {})
        if set(supplied) - allowed_headers:
            raise FetchBlocked("UNSAFE_REQUEST_HEADER")
        for _ in range(4):
            canonical, parsed = canonicalize(url)
            try:
                addresses = public_addresses(parsed.hostname or "")
            except OSError as exc:
                raise FetchBlocked("DNS_ERROR") from exc
            conn = _PinnedHTTPS(parsed.hostname or "", addresses[0], self.timeout_seconds)
            path = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
            request_headers = {
                "Host": parsed.hostname or "",
                "Accept": (
                    "text/csv,text/tab-separated-values,application/json,application/x-ndjson,"
                    "application/vnd.api+json,"
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
                    "application/zip,text/html,text/plain"
                ),
                "Accept-Encoding": "identity",
                "User-Agent": "epidemic-openclaw-discovery/1.1",
                **supplied,
            }
            try:
                conn.request("GET", path, headers=request_headers)
                response = conn.getresponse()
            except (OSError, TimeoutError, ssl.SSLError) as exc:
                conn.close()
                code = "REQUEST_TIMEOUT" if isinstance(exc, (TimeoutError, socket.timeout)) else "NETWORK_ERROR"
                raise FetchBlocked(code, network_attempted=True) from exc
            if 300 <= response.status < 400:
                location = response.getheader("location")
                response.read()
                conn.close()
                if not location:
                    raise FetchBlocked("UNSAFE_REDIRECT", http_status=response.status, network_attempted=True)
                target = urllib.parse.urljoin(canonical, location)
                target_canonical, target_parsed = canonicalize(target)
                if target_parsed.scheme != "https":
                    raise FetchBlocked("HTTPS_DOWNGRADE_BLOCKED")
                if "X-Subscription-Token" in supplied and target_parsed.hostname != parsed.hostname:
                    raise FetchBlocked(
                        "CREDENTIAL_REDIRECT_BLOCKED", http_status=response.status, network_attempted=True
                    )
                redirects.append(hashlib.sha256(target_canonical.encode()).hexdigest())
                url = target_canonical
                continue
            if response.status != 200:
                status = response.status
                response.read()
                conn.close()
                code = "RATE_LIMITED" if status == 429 else "HTTP_STATUS"
                raise FetchBlocked(code, http_status=status, network_attempted=True)
            content_type = (response.getheader("content-type") or "").split(";", 1)[0].lower()
            if content_type not in {
                "text/csv",
                "text/tab-separated-values",
                "application/csv",
                "application/json",
                "application/x-ndjson",
                "application/vnd.api+json",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/zip",
                "application/octet-stream",
                "text/html",
                "text/plain",
            }:
                response.read()
                conn.close()
                raise FetchBlocked("UNSUPPORTED_FORMAT", http_status=response.status, network_attempted=True)
            etag = response.getheader("etag")
            last_modified = response.getheader("last-modified")
            chunks = []
            size = 0
            try:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.maximum_bytes:
                        conn.close()
                        raise FetchBlocked("FILE_TOO_LARGE", http_status=response.status, network_attempted=True)
                    chunks.append(chunk)
            except (OSError, TimeoutError) as exc:
                conn.close()
                raise FetchBlocked("REQUEST_TIMEOUT", http_status=response.status, network_attempted=True) from exc
            conn.close()
            payload = b"".join(chunks)
            lower_path = parsed.path.casefold()
            if content_type in {"application/json", "application/vnd.api+json"} and not payload.lstrip().startswith(
                (b"{", b"[")
            ):
                raise FetchBlocked("CONTENT_TYPE_MAGIC_MISMATCH", http_status=response.status, network_attempted=True)
            if content_type in {
                "application/zip",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            } and not payload.startswith(b"PK\x03\x04"):
                raise FetchBlocked("CONTENT_TYPE_MAGIC_MISMATCH", http_status=response.status, network_attempted=True)
            if content_type == "application/octet-stream":
                if lower_path.endswith((".csv", ".tsv", ".txt")):
                    content_type = "text/tab-separated-values" if lower_path.endswith(".tsv") else "text/csv"
                elif lower_path.endswith(".xlsx") and payload.startswith(b"PK\x03\x04"):
                    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                elif lower_path.endswith(".zip") and payload.startswith(b"PK\x03\x04"):
                    content_type = "application/zip"
                elif lower_path.endswith((".json", ".jsonl", ".ndjson")) and payload.lstrip().startswith((b"{", b"[")):
                    content_type = "application/json"
                else:
                    raise FetchBlocked(
                        "CONTENT_TYPE_MAGIC_MISMATCH", http_status=response.status, network_attempted=True
                    )
            if content_type.startswith("text/") and b"\x00" in payload[:65536]:
                raise FetchBlocked("CONTENT_TYPE_MAGIC_MISMATCH", http_status=response.status, network_attempted=True)
            return FetchResult(
                canonical,
                hashlib.sha256(canonical.encode()).hexdigest(),
                payload,
                content_type,
                tuple(redirects),
                parsed.hostname or "",
                response.status,
                int((time.monotonic() - started) * 1000),
                hashlib.sha256(payload).hexdigest(),
                etag,
                last_modified,
            )
        raise FetchBlocked("TOO_MANY_REDIRECTS")
