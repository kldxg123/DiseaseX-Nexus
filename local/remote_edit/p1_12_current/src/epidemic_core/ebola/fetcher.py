from __future__ import annotations

import hashlib
import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    final_url: str
    status: int
    mime_type: str
    payload: bytes
    sha256: str
    etag: str | None
    last_modified: str | None
    redirect_hashes: tuple[str, ...]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class OfficialSourceFetcher:
    def __init__(
        self,
        allowed_hosts: set[str],
        *,
        max_bytes: int = 24 * 1024 * 1024,
        max_retries: int = 2,
        min_host_interval: float = 0.25,
        total_timeout: float = 90.0,
        use_environment_proxy: bool = False,
        user_agent: str = "epidemic-openclaw-p1-11/1.0",
    ) -> None:
        self.allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts}
        self.max_bytes = max_bytes
        self.max_retries = max_retries
        self.min_host_interval = min_host_interval
        self.total_timeout = total_timeout
        context = ssl.create_default_context()
        proxy_handler = urllib.request.ProxyHandler() if use_environment_proxy else urllib.request.ProxyHandler({})
        self.opener = urllib.request.build_opener(
            proxy_handler, urllib.request.HTTPSHandler(context=context), _NoRedirect()
        )
        self.user_agent = user_agent
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.last_request_at: dict[str, float] = {}

    def _canonical(self, url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or host not in self.allowed_hosts:
            raise FetchError("SOURCE_URL_NOT_ALLOWLISTED")
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            raise FetchError("SOURCE_AUTHORITY_REJECTED")
        return urllib.parse.urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))

    @staticmethod
    def _resolve(host: str) -> None:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise FetchError("DNS_PUBLIC_ADDRESS_REQUIRED")

    def _wait_for_host(self, host: str) -> None:
        remaining = self.min_host_interval - (time.monotonic() - self.last_request_at.get(host, 0.0))
        if remaining > 0:
            time.sleep(remaining)
        self.last_request_at[host] = time.monotonic()

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None) -> float:
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 10.0)
        return float(min(0.5 * (2**attempt), 4.0) + 0.05 * (attempt + 1))

    def _request(
        self,
        url: str,
        *,
        accept: str,
        max_redirects: int = 5,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        current = self._canonical(url)
        redirect_hashes: list[str] = []
        deadline = time.monotonic() + self.total_timeout
        for _ in range(max_redirects + 1):
            parsed = urllib.parse.urlsplit(current)
            host = str(parsed.hostname)
            self._resolve(host)
            headers = {"User-Agent": self.user_agent, "Accept": accept, "Accept-Encoding": "identity"}
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
            request = urllib.request.Request(current, headers=headers)
            redirected = False
            for attempt in range(self.max_retries + 1):
                if time.monotonic() >= deadline:
                    raise FetchError("TOTAL_TIMEOUT")
                self._wait_for_host(host)
                try:
                    response = self.opener.open(request, timeout=min(30.0, max(1.0, deadline - time.monotonic())))
                except urllib.error.HTTPError as exc:
                    if exc.code == 304:
                        return FetchResult(
                            current,
                            304,
                            "",
                            b"",
                            hashlib.sha256(b"").hexdigest(),
                            etag,
                            last_modified,
                            tuple(redirect_hashes),
                        )
                    if exc.code in {301, 302, 303, 307, 308}:
                        location = exc.headers.get("Location")
                        if not location:
                            raise FetchError("REDIRECT_LOCATION_MISSING") from exc
                        next_url = self._canonical(urllib.parse.urljoin(current, location))
                        redirect_hashes.append(hashlib.sha256(next_url.encode()).hexdigest())
                        current = next_url
                        redirected = True
                        break
                    if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                        time.sleep(self._retry_delay(attempt, exc.headers.get("Retry-After")))
                        continue
                    raise FetchError(f"HTTP_{exc.code}") from exc
                except urllib.error.URLError as exc:
                    if isinstance(exc.reason, ssl.SSLError):
                        raise FetchError("TLS_FAILURE") from exc
                    if attempt < self.max_retries:
                        time.sleep(self._retry_delay(attempt, None))
                        continue
                    raise FetchError("NETWORK_RETRY_EXHAUSTED") from exc
                except (TimeoutError, ConnectionError) as exc:
                    if attempt < self.max_retries:
                        time.sleep(self._retry_delay(attempt, None))
                        continue
                    raise FetchError("NETWORK_RETRY_EXHAUSTED") from exc
                with response:
                    length = response.headers.get("Content-Length")
                    if length and int(length) > self.max_bytes:
                        raise FetchError("RESPONSE_TOO_LARGE")
                    encoding = (response.headers.get("Content-Encoding") or "identity").lower()
                    if encoding not in {"identity", ""}:
                        raise FetchError("COMPRESSED_RESPONSE_REJECTED")
                    payload = response.read(self.max_bytes + 1)
                    if len(payload) > self.max_bytes:
                        raise FetchError("RESPONSE_TOO_LARGE")
                    mime = response.headers.get_content_type().lower()
                    if not (
                        mime.startswith("text/")
                        or mime
                        in {
                            "application/pdf",
                            "application/json",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            "application/vnd.oasis.opendocument.spreadsheet",
                        }
                    ):
                        raise FetchError("CONTENT_TYPE_REJECTED")
                    if mime == "application/pdf" and not payload.startswith(b"%PDF-"):
                        raise FetchError("CONTENT_TYPE_MAGIC_MISMATCH")
                    if mime == "application/vnd.oasis.opendocument.spreadsheet" and not payload.startswith(
                        b"PK\x03\x04"
                    ):
                        raise FetchError("CONTENT_TYPE_MAGIC_MISMATCH")
                    if (
                        mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        and not payload.startswith(b"PK\x03\x04")
                    ):
                        raise FetchError("CONTENT_TYPE_MAGIC_MISMATCH")
                    return FetchResult(
                        current,
                        int(response.status),
                        mime,
                        payload,
                        hashlib.sha256(payload).hexdigest(),
                        response.headers.get("ETag"),
                        response.headers.get("Last-Modified"),
                        tuple(redirect_hashes),
                    )
            if redirected:
                continue
            raise FetchError("RETRY_STATE_INVALID")
        raise FetchError("TOO_MANY_REDIRECTS")

    def _robots(self, url: str) -> urllib.robotparser.RobotFileParser:
        host = str(urllib.parse.urlsplit(url).hostname)
        cached = self.robots.get(host)
        if cached is not None:
            return cached
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(f"https://{host}/robots.txt")
        try:
            result = self._request(f"https://{host}/robots.txt", accept="text/plain,*/*;q=0.1")
        except FetchError as exc:
            if str(exc) != "HTTP_404":
                raise
            parser.parse([])
        else:
            parser.parse(result.payload.decode("utf-8", errors="replace").splitlines())
        self.robots[host] = parser
        return parser

    def fetch(
        self,
        url: str,
        *,
        check_robots: bool = True,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        canonical = self._canonical(url)
        if check_robots and not self._robots(canonical).can_fetch(self.user_agent, canonical):
            raise FetchError("ROBOTS_DISALLOWED")
        return self._request(
            canonical,
            accept=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
                "text/html,application/pdf,application/json,"
                "application/vnd.oasis.opendocument.spreadsheet;q=0.9,*/*;q=0.1"
            ),
            etag=etag,
            last_modified=last_modified,
        )
