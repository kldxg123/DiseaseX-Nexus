from __future__ import annotations

import calendar
import csv
import datetime as dt
import hashlib
import io
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from epidemic_core.retrieval.dengue import FIELDS, validate_envelope
from epidemic_core.retrieval.http import DownloadError
from epidemic_core.retrieval.registry import COVID_URL, DENGUE_URL, SourceSpec

LATEST_ROLLING_YEAR = "latest_rolling_year"
LATEST_COMPLETE_CALENDAR_YEAR = "latest_complete_calendar_year"
CURRENT_YEAR_TO_LATEST_PERIOD = "current_year_to_latest_period"
EXPLICIT_RANGE = "explicit_range"
WINDOW_KINDS = {
    LATEST_ROLLING_YEAR,
    LATEST_COMPLETE_CALENDAR_YEAR,
    CURRENT_YEAR_TO_LATEST_PERIOD,
    EXPLICIT_RANGE,
}
FRESHNESS_POLICY_VERSION = "source-freshness-policy-v1"
WINDOW_CONTRACT_VERSION = "latest-window-contract-v1"
REGRESSION_WITNESS = {
    "who_covid19_global_weekly": dt.date(2026, 6, 21),
    "who_dengue_global_validated": dt.date(2026, 6, 7),
}


class WindowResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowResolution:
    time_window_kind: str
    duration_iso8601: str | None
    resolved_start: dt.date
    resolved_end: dt.date
    source_declared_max_date: dt.date
    source_data_through: dt.date
    cases_new_data_through: dt.date | None
    deaths_new_data_through: dt.date | None
    cases_cumulative_data_through: dt.date | None
    deaths_cumulative_data_through: dt.date | None
    source_http_last_modified: str | None
    retrieved_at: dt.datetime
    cadence: str
    observed_period_count: int
    expected_period_count: int
    missing_period_count: int
    coverage_ratio: float
    reporting_lag_days: int
    freshness_status: str
    cases_freshness_status: str
    deaths_freshness_status: str
    probe_evidence_sha256: str


def parse_time_expression(value: str) -> str:
    normalized = "".join(value.lower().split())
    aliases = {
        LATEST_ROLLING_YEAR: ("latestrollingyear", "最近一年", "最新一年", "过去一年", "过去12个月"),
        LATEST_COMPLETE_CALENDAR_YEAR: (
            "latestcompletecalendaryear",
            "最新完整自然年",
            "上一个完整年度",
        ),
        CURRENT_YEAR_TO_LATEST_PERIOD: ("currentyeartolatestperiod", "今年以来", "本年度至今"),
    }
    for kind, values in aliases.items():
        if normalized in values:
            return kind
    if value in WINDOW_KINDS:
        return value
    raise WindowResolutionError("UNSUPPORTED_TIME_WINDOW")


def subtract_calendar_year(value: dt.date) -> dt.date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def resolve_range(kind: str, source_data_through: dt.date) -> tuple[dt.date, dt.date, str | None]:
    if kind == LATEST_ROLLING_YEAR:
        return subtract_calendar_year(source_data_through), source_data_through, "P1Y"
    if kind == LATEST_COMPLETE_CALENDAR_YEAR:
        year = source_data_through.year - 1
        return dt.date(year, 1, 1), dt.date(year, 12, 31), None
    if kind == CURRENT_YEAR_TO_LATEST_PERIOD:
        return dt.date(source_data_through.year, 1, 1), source_data_through, None
    raise WindowResolutionError("UNSUPPORTED_TIME_WINDOW")


def _date(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _reported(value: object) -> bool:
    if value is None or value == "":
        return False
    try:
        float(str(value).replace(",", ""))
        return True
    except ValueError:
        return False


def _expected(cadence: str, start: dt.date, end: dt.date) -> int:
    if cadence == "month":
        return (end.year - start.year) * 12 + end.month - start.month
    return max(0, (end - start).days // 7)


def _freshness(lag: int, cadence: str) -> str:
    warn, stale = (14, 35) if cadence == "week" else (45, 90)
    return "FRESH" if lag <= warn else "WARN" if lag <= stale else "STALE"


def _metric_freshness(metric: dt.date | None, overall: dt.date, cadence: str) -> str:
    if metric is None:
        return "NO_DATA"
    lag = (overall - metric).days
    warn, stale = (7, 28) if cadence == "week" else (31, 62)
    return "CURRENT" if lag <= warn else "DELAYED" if lag <= stale else "STALE"


def _resolution(
    *,
    kind: str,
    declared: dt.date,
    overall: dt.date,
    cases: dt.date | None,
    deaths: dt.date | None,
    cases_cumulative: dt.date | None,
    deaths_cumulative: dt.date | None,
    periods: Iterable[dt.date],
    cadence: str,
    last_modified: str | None,
    retrieved_at: dt.datetime,
    evidence: bytes,
) -> WindowResolution:
    start, end, duration = resolve_range(kind, overall)
    selected = {x for x in periods if start < x <= end}
    expected = _expected(cadence, start, end)
    observed = len(selected)
    missing = max(0, expected - observed)
    lag = max(0, (retrieved_at.date() - overall).days)
    return WindowResolution(
        kind,
        duration,
        start,
        end,
        declared,
        overall,
        cases,
        deaths,
        cases_cumulative,
        deaths_cumulative,
        last_modified,
        retrieved_at,
        cadence,
        observed,
        expected,
        missing,
        min(1.0, round(observed / expected, 6)) if expected else 0.0,
        lag,
        _freshness(lag, cadence),
        _metric_freshness(cases, overall, cadence),
        _metric_freshness(deaths, overall, cadence),
        hashlib.sha256(evidence).hexdigest(),
    )


def resolve_covid_bytes(
    raw: bytes,
    country_alpha2: str,
    kind: str,
    retrieved_at: dt.datetime,
    last_modified: str | None = None,
) -> WindowResolution:
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    except UnicodeDecodeError as exc:
        raise WindowResolutionError("SOURCE_SCHEMA_MISMATCH") from exc
    rows = [x for x in rows if x.get("Country_code") == country_alpha2]
    dates = [(_date(x.get("Date_reported")), x) for x in rows]
    valid_dates = [x for x, _ in dates if x is not None]
    if not valid_dates:
        raise WindowResolutionError("NO_VALID_SOURCE_DATA")

    def latest(field: str) -> dt.date | None:
        values = [day for day, row in dates if day is not None and _reported(row.get(field))]
        return max(values) if values else None

    cases = latest("New_cases")
    deaths = latest("New_deaths")
    cumulative_cases = latest("Cumulative_cases")
    cumulative_deaths = latest("Cumulative_deaths")
    new_metric_dates = [x for x in (cases, deaths) if x is not None]
    if not new_metric_dates:
        raise WindowResolutionError("NO_VALID_SOURCE_DATA")
    return _resolution(
        kind=kind,
        declared=max(valid_dates),
        overall=max(new_metric_dates),
        cases=cases,
        deaths=deaths,
        cases_cumulative=cumulative_cases,
        deaths_cumulative=cumulative_deaths,
        periods=(
            day
            for day, row in dates
            if day is not None and (_reported(row.get("New_cases")) or _reported(row.get("New_deaths")))
        ),
        cadence="week",
        last_modified=last_modified,
        retrieved_at=retrieved_at,
        evidence=raw,
    )


def resolve_dengue_bytes(
    raw: bytes,
    country_alpha3: str,
    kind: str,
    retrieved_at: dt.datetime,
    last_modified: str | None = None,
) -> WindowResolution:
    envelope, _count, _types = validate_envelope(raw, country_alpha3)
    prepared: list[tuple[dt.date, dt.date, dict[str, Any]]] = []
    for row in envelope["value"]:
        start = _date(row.get("START_DATE"))
        if start is None:
            continue
        dtype = row.get("DATE_TYPE")
        if dtype == "month":
            end = dt.date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        elif dtype in {"epiweek", "isoweek"}:
            end = start + dt.timedelta(days=6)
        else:
            continue
        prepared.append((start, end, row))
    if not prepared:
        raise WindowResolutionError("NO_VALID_SOURCE_DATA")
    declared = max(start for start, _end, _row in prepared)
    valid = [
        (start, end, row) for start, end, row in prepared if _reported(row.get("CASES")) or _reported(row.get("DEATHS"))
    ]
    if not valid:
        raise WindowResolutionError("NO_VALID_SOURCE_DATA")
    available_types = {row.get("DATE_TYPE") for _start, _end, row in valid}
    native_type = "epiweek" if "epiweek" in available_types else "isoweek" if "isoweek" in available_types else "month"
    native = [(start, end, row) for start, end, row in valid if row.get("DATE_TYPE") == native_type]
    # xMart's native reporting key is START_DATE; preserve one controlled cadence.
    cases = max((start for start, _end, row in native if _reported(row.get("CASES"))), default=None)
    deaths = max((start for start, _end, row in native if _reported(row.get("DEATHS"))), default=None)
    overall = max(x for x in (cases, deaths) if x is not None)
    cadence = "month" if native_type == "month" else "week"
    return _resolution(
        kind=kind,
        declared=declared,
        overall=overall,
        cases=cases,
        deaths=deaths,
        cases_cumulative=None,
        deaths_cumulative=None,
        periods=(start for start, _end, _row in native),
        cadence=cadence,
        last_modified=last_modified,
        retrieved_at=retrieved_at,
        evidence=raw,
    )


def probe_source(spec: SourceSpec, country_alpha2: str, country_alpha3: str, kind: str) -> WindowResolution:
    now = dt.datetime.now(dt.timezone.utc)
    headers = {"Accept-Encoding": "identity", "User-Agent": "epidemic-openclaw/1.0 controlled-watermark-probe"}
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(90, connect=20)) as client:
            if spec.source_key == "who_covid19_global_weekly":
                response = client.get(COVID_URL, headers={**headers, "Accept": "text/csv"})
                response.raise_for_status()
                result = resolve_covid_bytes(
                    response.content, country_alpha2, kind, now, response.headers.get("last-modified")
                )
            elif spec.source_key == "who_dengue_global_validated":
                params = {
                    "$select": ",".join(FIELDS),
                    "$filter": f"ISO3 eq '{country_alpha3}'",
                    "$orderby": "START_DATE asc,DATE_TYPE asc,DATE_NUM asc,ISO3 asc",
                    "$top": "10000",
                    "$count": "true",
                    "excludeSysColumns": "0",
                }
                response = client.get(DENGUE_URL, params=params, headers={**headers, "Accept": "application/json"})
                response.raise_for_status()
                result = resolve_dengue_bytes(
                    response.content, country_alpha3, kind, now, response.headers.get("last-modified")
                )
            else:
                raise WindowResolutionError("NO_COMPATIBLE_SOURCE")
    except (httpx.HTTPError, DownloadError) as exc:
        raise WindowResolutionError("SOURCE_PROBE_FAILED") from exc
    witness = REGRESSION_WITNESS.get(spec.source_key)
    if witness is not None and result.source_data_through < witness:
        raise WindowResolutionError("SOURCE_REGRESSED")
    return result
