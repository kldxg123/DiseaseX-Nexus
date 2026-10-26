from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import mimetypes
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path


ALLOWED_HOSTS = {
    "www.who.int",
    "who.int",
    "evd-daily.health.go.ug",
    "health.go.ug",
    "insp.cd",
    "www.insp.cd",
    "www.afro.who.int",
    "afro.who.int",
    "iris.who.int",
}
SEEDS = {
    "who_collection": "https://www.who.int/emergencies/emergency-events/item/2026-e000253",
    "uganda_dashboard": "https://evd-daily.health.go.ug/",
    "insp_listing": "https://insp.cd/category/sitrep/",
    "who_afro_fallback": "https://www.afro.who.int/countries/democratic-republic-of-congo/publication/ebola-bundibugyo-virus-disease-outbreak-2",
    "who_afro_report_08": "https://www.afro.who.int/countries/democratic-republic-of-congo/publication/ebola-bundibugyo-virus-disease-outbreak-1",
    "who_afro_report_07": "https://www.afro.who.int/countries/uganda/publication/ebola-bundibugyo-virus-disease-outbreak-democratic-republic-congo-3",
    "who_afro_report_06": "https://www.afro.who.int/countries/uganda/publication/ebola-bundibugyo-virus-disease-outbreak-democratic-republic-congo-2",
    "who_afro_report_05": "https://www.afro.who.int/countries/uganda/publication/ebola-bundibugyo-virus-disease-outbreak-democratic-republic-congo-1",
}
WHO_DONS = tuple(
    f"https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON{number}"
    for number in (602, 603, 605, 606, 607, 608, 612, 613)
)
MAX_BYTES = 24 * 1024 * 1024
MAX_REDIRECTS = 5
USER_AGENT = "epidemic-openclaw-p1-11/1.0 (+controlled-official-source-monitor)"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.images: list[str] = []
        self.scripts: list[str] = []
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value}
        if tag.lower() == "a" and values.get("href"):
            self.links.append(str(values["href"]))
        if tag.lower() in {"img", "source"} and values.get("src"):
            self.images.append(str(values["src"]))
        if tag.lower() == "script" and values.get("src"):
            self.scripts.append(str(values["src"]))

    def handle_data(self, data: str) -> None:
        stripped = " ".join(data.split())
        if stripped:
            self._text.append(stripped)

    @property
    def text(self) -> str:
        return " ".join(self._text)


@dataclass(frozen=True)
class FetchRecord:
    label: str
    domain: str
    canonical_path: str
    status: int
    mime_type: str
    byte_size: int
    sha256: str
    elapsed_ms: int
    redirect_count: int
    storage_key: str
    etag_present: bool
    last_modified_present: bool


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS:
        raise ValueError("SOURCE_URL_NOT_ALLOWLISTED")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("SOURCE_URL_AUTHORITY_REJECTED")
    return urllib.parse.urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _resolve_public(host: str) -> list[str]:
    values = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not values:
        raise ValueError("DNS_EMPTY")
    for value in values:
        address = ipaddress.ip_address(value)
        if not address.is_global:
            raise ValueError("NON_PUBLIC_DNS_ADDRESS")
    return values


def _fetch(label: str, url: str, root: Path) -> tuple[FetchRecord, bytes, str]:
    current = _canonical_url(url)
    redirects = 0
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect()
    )
    started = time.monotonic()
    while True:
        parsed = urllib.parse.urlsplit(current)
        _resolve_public(str(parsed.hostname))
        request = urllib.request.Request(
            current,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json,image/*,application/pdf;q=0.9,*/*;q=0.1"},
        )
        try:
            response = opener.open(request, timeout=25)
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise
            location = exc.headers.get("Location")
            if not location or redirects >= MAX_REDIRECTS:
                raise ValueError("REDIRECT_POLICY_REJECTED") from exc
            current = _canonical_url(urllib.parse.urljoin(current, location))
            redirects += 1
            continue
        with response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_BYTES:
                raise ValueError("RESPONSE_TOO_LARGE")
            payload = response.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError("RESPONSE_TOO_LARGE")
            mime = response.headers.get_content_type().lower()
            if not (
                mime.startswith("text/")
                or mime.startswith("image/")
                or mime in {"application/json", "application/pdf", "application/octet-stream"}
            ):
                raise ValueError("CONTENT_TYPE_REJECTED")
            digest = hashlib.sha256(payload).hexdigest()
            suffix = mimetypes.guess_extension(mime) or ".bin"
            target = root / "artifacts" / digest[:2] / f"{digest}{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(payload)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            final = urllib.parse.urlsplit(current)
            record = FetchRecord(
                label=label,
                domain=str(final.hostname),
                canonical_path=final.path,
                status=int(response.status),
                mime_type=mime,
                byte_size=len(payload),
                sha256=digest,
                elapsed_ms=elapsed_ms,
                redirect_count=redirects,
                storage_key=str(target.relative_to(root)),
                etag_present=bool(response.headers.get("ETag")),
                last_modified_present=bool(response.headers.get("Last-Modified")),
            )
            return record, payload, current


def _parse_html(payload: bytes) -> LinkParser:
    parser = LinkParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    return parser


def _safe_links(base: str, values: list[str]) -> list[str]:
    output: list[str] = []
    for raw in values:
        try:
            url = _canonical_url(urllib.parse.urljoin(base, html.unescape(raw)))
        except ValueError:
            continue
        if url not in output:
            output.append(url)
    return output


def _pii_flags(text: str) -> list[str]:
    checks = {
        "email": r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "phone": r"(?<!\d)(?:\+?\d[\d .()/-]{7,}\d)(?!\d)",
        "patient_identifier": r"\b(?:patient|case|contact)[ _-]?(?:id|number|no\.?)[ :=#-]*[A-Z0-9-]{4,}\b",
    }
    return sorted(key for key, pattern in checks.items() if re.search(pattern, text, re.IGNORECASE))


def _aggregate_evidence(text: str) -> dict[str, object]:
    normalized = " ".join(text.split())
    keywords = ("confirmed", "deaths", "recover", "contacts", "tests", "imported", "local")
    return {
        "keyword_presence": {key: bool(re.search(rf"\b{key}\w*\b", normalized, re.IGNORECASE)) for key in keywords},
        "pii_flags": _pii_flags(normalized),
        "text_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
    }


def _ocr_image(path: Path, root: Path, digest: str) -> dict[str, object]:
    try:
        version = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, timeout=10, check=True)
    except (FileNotFoundError, subprocess.SubprocessError):
        return {"available": False, "reason": "TESSERACT_UNAVAILABLE"}
    outbase = root / "ocr" / digest
    outbase.parent.mkdir(parents=True, exist_ok=True)
    language = "fra+eng"
    command = ["tesseract", str(path), str(outbase), "-l", language, "tsv"]
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=120, check=True)
    except subprocess.CalledProcessError:
        language = "eng"
        command = ["tesseract", str(path), str(outbase), "-l", language, "tsv"]
        try:
            subprocess.run(command, capture_output=True, text=True, timeout=120, check=True)
        except subprocess.SubprocessError:
            return {"available": True, "status": "FAILED", "engine": version.stdout.splitlines()[0]}
    tsv = outbase.with_suffix(".tsv")
    text_parts: list[str] = []
    confidences: list[float] = []
    bbox_count = 0
    for index, line in enumerate(tsv.read_text(encoding="utf-8", errors="replace").splitlines()):
        if index == 0:
            continue
        columns = line.split("\t")
        if len(columns) < 12 or not columns[11].strip():
            continue
        bbox_count += 1
        text_parts.append(columns[11].strip())
        try:
            confidence = float(columns[10])
        except ValueError:
            continue
        if confidence >= 0:
            confidences.append(confidence)
    text = " ".join(text_parts)
    pii = _pii_flags(text)
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return {
        "available": True,
        "status": "PASS" if mean_confidence >= 70.0 and not pii else "QUARANTINED",
        "engine": version.stdout.splitlines()[0],
        "language": language,
        "mean_confidence": round(mean_confidence, 2),
        "bbox_count": bbox_count,
        "pii_flags": pii,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "aggregate_evidence": _aggregate_evidence(text),
    }


def _extract_pdf(path: Path, root: Path, digest: str) -> dict[str, object]:
    try:
        version = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True, timeout=10, check=True)
    except (FileNotFoundError, subprocess.SubprocessError):
        return {"available": False, "reason": "PDFTOTEXT_UNAVAILABLE"}
    target = root / "pdf_text" / f"{digest}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["pdftotext", "-layout", str(path), str(target)], capture_output=True, text=True, timeout=120, check=True)
    except subprocess.SubprocessError:
        return {"available": True, "status": "FAILED"}
    text = target.read_text(encoding="utf-8", errors="replace")
    flags = _pii_flags(text)
    return {
        "available": True,
        "status": "PASS" if not flags else "QUARANTINED",
        "engine": (version.stderr or version.stdout).splitlines()[0],
        "pii_flags": flags,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "aggregate_evidence": _aggregate_evidence(text),
    }


def main() -> int:
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    records: list[FetchRecord] = []
    findings: dict[str, object] = {}
    failures: list[dict[str, str]] = []

    targets = list(SEEDS.items()) + [(f"who_don_{url.rsplit('DON', 1)[1]}", url) for url in WHO_DONS]
    pages: dict[str, tuple[bytes, str]] = {}
    for label, url in targets:
        try:
            record, payload, final_url = _fetch(label, url, root)
            records.append(record)
            pages[label] = (payload, final_url)
            if record.mime_type.startswith("text/html"):
                findings[label] = _aggregate_evidence(_parse_html(payload).text)
        except Exception as exc:
            failures.append({"label": label, "error_code": type(exc).__name__})

    insp_articles: list[str] = []
    listing = pages.get("insp_listing")
    if listing:
        parser = _parse_html(listing[0])
        insp_articles = [
            url
            for url in _safe_links(listing[1], parser.links)
            if re.search(r"/sitrep-n?0?(?:5[0-9]|6[0-9])", urllib.parse.urlsplit(url).path, re.IGNORECASE)
        ][:6]
    insp_assets: list[str] = []
    for index, url in enumerate(insp_articles):
        label = f"insp_article_{index + 1}"
        try:
            record, payload, final_url = _fetch(label, url, root)
            records.append(record)
            parser = _parse_html(payload)
            findings[label] = _aggregate_evidence(parser.text)
            for asset in _safe_links(final_url, parser.images + parser.links):
                path = urllib.parse.urlsplit(asset).path.lower()
                if path.endswith((".png", ".jpg", ".jpeg", ".webp", ".pdf")) and asset not in insp_assets:
                    insp_assets.append(asset)
        except Exception as exc:
            failures.append({"label": label, "error_code": type(exc).__name__})

    ocr: list[dict[str, object]] = []
    for index, url in enumerate(insp_assets[:8]):
        label = f"insp_asset_{index + 1}"
        try:
            record, _payload, _final_url = _fetch(label, url, root)
            records.append(record)
            if record.mime_type.startswith("image/"):
                path = root / record.storage_key
                ocr.append({"sha256": record.sha256, "mime_type": record.mime_type, **_ocr_image(path, root, record.sha256)})
        except Exception as exc:
            failures.append({"label": label, "error_code": type(exc).__name__})

    afro_assets: list[str] = []
    for label, (payload, final_url) in pages.items():
        if not label.startswith("who_afro_"):
            continue
        parser = _parse_html(payload)
        for asset in _safe_links(final_url, parser.links):
            if urllib.parse.urlsplit(asset).hostname == "iris.who.int" and "/bitstreams/" in asset and asset not in afro_assets:
                afro_assets.append(asset)
    pdf_text: list[dict[str, object]] = []
    for index, url in enumerate(afro_assets[:8]):
        label = f"who_afro_pdf_{index + 1}"
        try:
            record, _payload, _final_url = _fetch(label, url, root)
            records.append(record)
            if record.mime_type == "application/pdf":
                pdf_text.append({"sha256": record.sha256, **_extract_pdf(root / record.storage_key, root, record.sha256)})
        except Exception as exc:
            failures.append({"label": label, "error_code": type(exc).__name__})

    result = {
        "probe_version": "p1-11-live-probe-v1",
        "proxy_present_during_probe": False,
        "tls_verification": True,
        "allowlisted_domains": sorted({record.domain for record in records}),
        "fetches": [asdict(record) for record in records],
        "findings": findings,
        "insp_article_count": len(insp_articles),
        "insp_asset_count": len(insp_assets),
        "ocr": ocr,
        "who_afro_asset_count": len(afro_assets),
        "pdf_text": pdf_text,
        "failures": failures,
    }
    output = root / "live_probe_manifest.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if records else 2


if __name__ == "__main__":
    raise SystemExit(main())
