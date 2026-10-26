from __future__ import annotations

# ruff: noqa: E501
import datetime as dt
import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from pypdf import PdfReader


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class EbolaObservation:
    country_iso3: str
    source_as_of: dt.date
    metric_code: str
    value: int | None
    case_classification: str
    import_classification: str = "all"
    local_transmission: bool | None = None
    geography_scope: str = "national_reported"
    quality_flags: tuple[str, ...] = ()
    reporting_country: str | None = None
    exposure_country: str | None = None
    diagnosis_country: str | None = None
    treatment_country: str | None = None


@dataclass(frozen=True)
class ParsedReport:
    publication_key: str
    publication_date: dt.date | None
    source_as_of: dt.date
    observations: tuple[EbolaObservation, ...]
    quality_flags: tuple[str, ...] = ()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.ignored:
            self.ignored -= 1
        elif not self.ignored and tag.lower() in {"p", "div", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored and data.strip():
            self.parts.append(data)


def html_text(payload: bytes) -> str:
    parser = _TextParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    return " ".join(" ".join(parser.parts).split())


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
NUMBER = r"(?:\d[\d ,]*|" + "|".join(WORDS) + r")"
DATE = r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(2026)"


def _number(value: str) -> int:
    normalized = value.strip().lower()
    if normalized in WORDS:
        return WORDS[normalized]
    return int(re.sub(r"[ ,]", "", normalized))


def _date(match: re.Match[str]) -> dt.date:
    return dt.date(int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1)))


def _metric(
    country: str, date: dt.date, code: str, value: int | None, classification: str, **kwargs: object
) -> EbolaObservation:
    kwargs.setdefault("reporting_country", country)
    kwargs.setdefault("exposure_country", country)
    kwargs.setdefault("diagnosis_country", country)
    return EbolaObservation(country, date, code, value, classification, **kwargs)  # type: ignore[arg-type]


def parse_who_don(payload: bytes, publication_key: str) -> ParsedReport:
    text = html_text(payload)
    section_matches = list(re.finditer(r"Democratic Republic of the Congo\s+(?:Since|As of)", text, re.IGNORECASE))
    if not section_matches:
        raise ParseError("WHO_DON_COUNTRY_SECTION_MISSING")
    section_match = next(
        (match for match in section_matches if match.group(0).lower().endswith("since")), section_matches[-1]
    )
    section = text[section_match.start() :]
    uganda_match = re.search(r"\sUganda\s+(?:Since|The last|As of)", section, re.IGNORECASE)
    if uganda_match is not None:
        section = section[: uganda_match.start()]
    value = r"(?:\d(?:[\d ]*?\d)?|" + "|".join(WORDS) + r")"
    pattern = re.compile(
        rf"As of\s+{DATE}.{{0,100}}?({value})\s+(?:laboratory-)?confirmed cases?.{{0,100}}?({value})\s+(?:confirmed\s+)?deaths?",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(section))
    if not matches:
        raise ParseError("WHO_DON_AGGREGATE_PATTERN_MISSING")
    match = matches[-1]
    source_as_of = _date(match)
    confirmed, deaths = _number(match.group(4)), _number(match.group(5))
    tail = section[match.start() : match.start() + 1800]
    recovered = None
    recovery_patterns = (
        rf"({NUMBER})\s+(?:patients?\s+have\s+)?recovered",
        rf"({NUMBER})\s+recoveries(?:\s+have been reported)?",
    )
    for expression in recovery_patterns:
        found = re.search(expression, tail, re.IGNORECASE)
        if found:
            recovered = _number(found.group(1))
            break
    contacts = None
    found_contacts = re.search(
        rf"({NUMBER})\s+contacts?\s+(?:has been|had been|have been|were)\s+(?:identified|listed)", tail, re.IGNORECASE
    )
    if found_contacts:
        contacts = _number(found_contacts.group(1))
    observations = [
        _metric("COD", source_as_of, "ebola_confirmed_cases_cumulative", confirmed, "confirmed"),
        _metric("COD", source_as_of, "ebola_confirmed_deaths_cumulative", deaths, "confirmed"),
    ]
    if recovered is not None:
        observations.append(_metric("COD", source_as_of, "ebola_recoveries_cumulative", recovered, "not_applicable"))
    if contacts is not None:
        observations.append(
            _metric("COD", source_as_of, "ebola_contacts_listed_cumulative", contacts, "not_applicable")
        )
    flags = ("BACKLOG_MAY_AFFECT_APPARENT_DAILY_CHANGE",)
    return ParsedReport(publication_key, None, source_as_of, tuple(observations), flags)


def parse_uganda_dashboard(payload: bytes) -> ParsedReport:
    text = html_text(payload)
    date_match = re.search(
        rf"As of\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+{DATE}", text, re.IGNORECASE
    )
    if not date_match:
        raise ParseError("UGANDA_SOURCE_AS_OF_MISSING")
    source_as_of = _date(date_match)

    def value(label: str) -> int:
        found = re.search(rf"(?<![\d,])(\d+(?:,\d{{3}})*)\s+{label}", text, re.IGNORECASE)
        if not found:
            raise ParseError(f"UGANDA_METRIC_MISSING_{label.upper().replace(' ', '_')}")
        return _number(found.group(1))

    confirmed = value("Cumulative confirmed cases")
    imported = value("Imported Cases")
    local = value("Local cases")
    deaths = value("Cumulative deaths")
    recovered = value("Recoveries")
    tests = value("Total persons tested")
    contacts = value("All-time contacts listed")
    active_contacts = value("Active contacts")
    completed_contacts = value("Completed 21-day follow-up")
    current_admissions = value("Current admissions")
    alerts = value("Total alerts")
    alerts_verified = value("Alerts verified")
    travellers_screened = value("Travellers screened")
    travellers_inbound = value("Inbound travellers")
    travellers_outbound = value("Outbound travellers")
    observations = (
        _metric(
            "UGA",
            source_as_of,
            "ebola_confirmed_cases_cumulative",
            confirmed,
            "confirmed",
            import_classification="all",
            local_transmission=local > 0,
            exposure_country=None,
        ),
        _metric(
            "UGA",
            source_as_of,
            "ebola_confirmed_cases_cumulative",
            imported,
            "confirmed",
            import_classification="imported",
            local_transmission=False,
            exposure_country="COD",
        ),
        _metric(
            "UGA",
            source_as_of,
            "ebola_confirmed_cases_cumulative",
            local,
            "confirmed",
            import_classification="local",
            local_transmission=local > 0,
        ),
        _metric(
            "UGA", source_as_of, "ebola_confirmed_deaths_cumulative", deaths, "confirmed", local_transmission=local > 0
        ),
        _metric("UGA", source_as_of, "ebola_recoveries_cumulative", recovered, "not_applicable"),
        _metric("UGA", source_as_of, "ebola_tests_cumulative", tests, "not_applicable"),
        _metric("UGA", source_as_of, "ebola_contacts_listed_cumulative", contacts, "not_applicable"),
        _metric("UGA", source_as_of, "ebola_contacts_active", active_contacts, "not_applicable"),
        _metric(
            "UGA",
            source_as_of,
            "ebola_contacts_completed_cumulative",
            completed_contacts,
            "not_applicable",
        ),
        _metric("UGA", source_as_of, "ebola_current_admissions", current_admissions, "not_applicable"),
        _metric("UGA", source_as_of, "ebola_alerts_cumulative", alerts, "not_applicable"),
        _metric("UGA", source_as_of, "ebola_alerts_verified_cumulative", alerts_verified, "not_applicable"),
        _metric("UGA", source_as_of, "ebola_travellers_screened_cumulative", travellers_screened, "not_applicable"),
        _metric("UGA", source_as_of, "ebola_travellers_inbound_cumulative", travellers_inbound, "not_applicable"),
        _metric("UGA", source_as_of, "ebola_travellers_outbound_cumulative", travellers_outbound, "not_applicable"),
    )
    return ParsedReport("uganda-ebola-daily", source_as_of, source_as_of, observations)


def pdf_text(payload: bytes) -> str:
    reader = PdfReader(io.BytesIO(payload), strict=True)
    if not reader.pages or len(reader.pages) > 50:
        raise ParseError("WHO_AFRO_PDF_PAGE_COUNT_INVALID")
    return " ".join(" ".join((page.extract_text() or "") for page in reader.pages).split())


def parse_who_afro_pdf(payload: bytes, publication_key: str) -> ParsedReport:
    text = pdf_text(payload)
    date_match = re.search(rf"(?:data\s+)?as of\s+{DATE}", text, re.IGNORECASE)
    if not date_match:
        raise ParseError("WHO_AFRO_SOURCE_AS_OF_MISSING")
    source_as_of = _date(date_match)
    summary = re.search(r"Democratic Republic of the Congo\s+([\d ]+?)\s+-\s+-", text, re.IGNORECASE)
    if summary:
        tokens = re.findall(r"\d+", summary.group(1))
        if len(tokens) >= 5:
            confirmed, deaths = int("".join(tokens[2:-1])), int(tokens[-1])
        elif len(tokens) == 4:
            confirmed, deaths = int(tokens[2]), int(tokens[3])
        elif len(tokens) == 3 and len(tokens[0]) == 1 and len(tokens[1]) == 3:
            confirmed, deaths = int(tokens[0] + tokens[1]), int(tokens[2])
        elif len(tokens) == 2:
            confirmed, deaths = int(tokens[0]), int(tokens[1])
        else:
            raise ParseError("WHO_AFRO_DRC_SUMMARY_AMBIGUOUS")
    else:
        narrative = re.search(
            rf"cumulative total of\s+({NUMBER})\s+(?:laboratory-)?confirmed cases,?\s+including\s+({NUMBER})\s+confirmed deaths",
            text,
            re.IGNORECASE,
        )
        if not narrative:
            raise ParseError("WHO_AFRO_DRC_SUMMARY_MISSING")
        confirmed, deaths = _number(narrative.group(1)), _number(narrative.group(2))
    contacts = None
    contact_match = re.search(
        rf"As of\s+{DATE}.{{0,120}}?total of\s+({NUMBER})\s+contacts?\s+were under follow", text, re.IGNORECASE
    )
    if contact_match:
        contacts = _number(contact_match.group(4))
    observations = [
        _metric("COD", source_as_of, "ebola_confirmed_cases_cumulative", confirmed, "confirmed"),
        _metric("COD", source_as_of, "ebola_confirmed_deaths_cumulative", deaths, "confirmed"),
    ]
    if contacts is not None:
        observations.append(_metric("COD", source_as_of, "ebola_contacts_active", contacts, "not_applicable"))
    return ParsedReport(publication_key, None, source_as_of, tuple(observations), ("WEEKLY_EXTERNAL_SITREP",))
