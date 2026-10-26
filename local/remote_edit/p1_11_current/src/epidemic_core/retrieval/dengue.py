from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import random
import re
import socket
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from epidemic_core.retrieval.http import DownloadError, validate_public_dns
from epidemic_core.retrieval.registry import DENGUE_URL, SourceSpec

FIELDS = (
    "COUNTRY",
    "ISO3",
    "WHO_REGION",
    "YEAR",
    "DATE_NUM",
    "START_DATE",
    "DATE_TYPE",
    "CASES",
    "CONFIRMED_CASES",
    "SEVERE_CASES",
    "DEATHS",
    "SERO_1",
    "SERO_2",
    "SERO_3",
    "SERO_4",
    "POPULATION",
)
ALLOWED_TYPES = {"epiweek", "isoweek", "month"}


@dataclass(frozen=True)
class DengueResult:
    http_status: int
    sha256: str
    byte_size: int
    media_type: str
    path: Path
    attempt_number: int
    response_date: str | None
    etag: str | None
    last_modified: str | None
    cache_control: str | None
    odata_context: str
    odata_count: int
    returned_rows: int
    date_types: tuple[str, ...]
    request_fingerprint: str
    not_modified: bool = False


def build_params(iso3: str, start: dt.date, end: dt.date) -> dict[str, str]:
    if not re.fullmatch(r"[A-Z]{3}", iso3) or start > end:
        raise DownloadError("INVALID_CONTROLLED_QUERY")
    expression = (
        f"ISO3 eq '{iso3}' and ((START_DATE ge {start.isoformat()} and START_DATE le {end.isoformat()}) "
        f"or (START_DATE eq null and YEAR ge {start.year - 1} and YEAR le {end.year + 1}))"
    )
    return {
        "$select": ",".join(FIELDS),
        "$filter": expression,
        "$orderby": "START_DATE asc,DATE_TYPE asc,DATE_NUM asc,ISO3 asc",
        "$top": "10000",
        "$count": "true",
        "excludeSysColumns": "0",
    }


def validate_envelope(raw: bytes, iso3: str) -> tuple[dict[str, Any], int, tuple[str, ...]]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadError("NON_JSON_RESPONSE") from exc
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("@odata.context"), str)
        or "V_DENGUE_GLOBAL_VALIDATED_PUBLIC" not in value["@odata.context"]
    ):
        raise DownloadError("ODATA_CONTEXT_MISMATCH")
    count = value.get("@odata.count")
    rows = value.get("value")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0 or not isinstance(rows, list):
        raise DownloadError("ODATA_ENVELOPE_MISMATCH")
    if count > 10000:
        raise DownloadError("SOURCE_RESULT_LIMIT_EXCEEDED")
    if any(k in value for k in ("@odata.nextLink", "odata.nextLink")):
        raise DownloadError("PAGINATION_REQUIRED_NOT_IMPLEMENTED")
    if len(rows) != count:
        raise DownloadError("TRUNCATED_SOURCE_RESPONSE")
    allowed = set(FIELDS)
    types = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - allowed:
            raise DownloadError("SOURCE_SCHEMA_MISMATCH")
        if row.get("ISO3") != iso3 or not re.fullmatch(r"[A-Z]{3}", row.get("ISO3") or ""):
            raise DownloadError("SOURCE_SCHEMA_MISMATCH")
        dtype = row.get("DATE_TYPE")
        if dtype is not None:
            if dtype not in ALLOWED_TYPES:
                raise DownloadError("UNKNOWN_DATE_TYPE")
            types.add(dtype)
        for key in ("YEAR", "DATE_NUM", "CASES", "CONFIRMED_CASES", "SEVERE_CASES", "DEATHS", "POPULATION"):
            item = row.get(key)
            if item is not None and (not isinstance(item, int) or isinstance(item, bool)):
                raise DownloadError("SOURCE_SCHEMA_MISMATCH")
        for key in ("COUNTRY", "ISO3", "WHO_REGION", "START_DATE", "DATE_TYPE"):
            if row.get(key) is not None and not isinstance(row[key], str):
                raise DownloadError("SOURCE_SCHEMA_MISMATCH")
        for key in ("SERO_1", "SERO_2", "SERO_3", "SERO_4"):
            if row.get(key) not in (None, "TRUE", "FALSE"):
                raise DownloadError("SOURCE_SCHEMA_MISMATCH")
    return value, count, tuple(sorted(types))


class DengueDownloader:
    def __init__(
        self,
        spec: SourceSpec,
        raw_root: Path,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
        resolver: Callable[..., Sequence[tuple[Any, ...]]] = socket.getaddrinfo,
    ):
        if spec.download_url != DENGUE_URL or spec.artifact_scope != "query_scoped":
            raise DownloadError("UNSAFE_TARGET")
        self.spec = spec
        self.raw_root = raw_root
        self.transport = transport
        self.sleep = sleep
        self.rng = rng
        self.resolver = resolver

    def fetch(self, iso3: str, start: dt.date, end: dt.date) -> DengueResult:
        validate_public_dns("xmart-api-public.who.int", self.resolver)
        params = build_params(iso3, start, end)
        fingerprint = hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        last = None
        for attempt in range(1, 4):
            try:
                return self._once(iso3, params, fingerprint, attempt)
            except DownloadError as exc:
                last = exc
                if attempt == 3 or not exc.retryable:
                    raise
                self.sleep(min(30, 2 ** (attempt - 1) + self.rng()))
        raise last or DownloadError("DOWNLOAD_FAILED")

    def _once(self, iso3: str, params: dict[str, str], fingerprint: str, attempt: int) -> DengueResult:
        headers = {
            "User-Agent": "epidemic-openclaw/1.0 controlled-who-dengue-retrieval",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        self.raw_root.mkdir(parents=True, exist_ok=True)
        tmpdir = self.raw_root / ".tmp"
        tmpdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="dengue-", suffix=".part", dir=tmpdir)
        os.chmod(name, 0o600)
        digest = hashlib.sha256()
        size = 0
        try:
            with (
                httpx.Client(
                    transport=self.transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(60, connect=20),
                ) as client,
                client.stream("GET", DENGUE_URL, params=params, headers=headers) as response,
            ):
                status = response.status_code
                if 300 <= status < 400:
                    raise DownloadError("UNSAFE_REDIRECT", status=status)
                if status != 200:
                    raise DownloadError(
                        "HTTP_STATUS", status=status, retryable=status in {408, 425, 429, 500, 502, 503, 504}
                    )
                media = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if media != "application/json":
                    raise DownloadError("UNEXPECTED_CONTENT_TYPE", status=status)
                with os.fdopen(fd, "wb") as handle:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.spec.maximum_bytes:
                            raise DownloadError("RESPONSE_TOO_LARGE", status=status)
                        digest.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                raw = Path(name).read_bytes()
                envelope, count, types = validate_envelope(raw, iso3)
                sha = digest.hexdigest()
                target = self.raw_root / sha[:2] / (sha + ".json")
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if target.exists():
                    if target.read_bytes() != raw:
                        raise DownloadError("ARTIFACT_COLLISION")
                    os.unlink(name)
                else:
                    os.replace(name, target)
                return DengueResult(
                    status,
                    sha,
                    size,
                    media,
                    target,
                    attempt,
                    response.headers.get("date"),
                    response.headers.get("etag"),
                    response.headers.get("last-modified"),
                    response.headers.get("cache-control"),
                    envelope["@odata.context"],
                    count,
                    len(envelope["value"]),
                    types,
                    fingerprint,
                )
        except httpx.TimeoutException as exc:
            raise DownloadError("HTTP_TIMEOUT", retryable=True) from exc
        except httpx.TransportError as exc:
            raise DownloadError("HTTP_TRANSPORT", retryable=True) from exc
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name)
