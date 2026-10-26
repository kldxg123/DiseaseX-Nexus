from __future__ import annotations

import argparse
import csv
import ctypes
import ctypes.util
import errno
import io
import json
import os
import re
import socket
import zipfile
from collections.abc import Sequence
from html.parser import HTMLParser
from pathlib import PurePosixPath
from xml.etree import ElementTree

MAX_ROWS = 10_000
MAX_COLUMNS = 256
MAX_FIELD = 2_000
MAX_SAMPLE_ROWS = 200


def block_network() -> None:
    library = ctypes.util.find_library("seccomp")
    if not library:
        raise RuntimeError("SECCOMP_UNAVAILABLE")
    seccomp = ctypes.CDLL(library, use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_load.restype = ctypes.c_int
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.restype = None
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_rule_add.restype = ctypes.c_int
    context = seccomp.seccomp_init(0x7FFF0000)
    if not context:
        raise RuntimeError("SECCOMP_INIT_FAILED")
    deny = 0x00050000 | errno.EPERM
    try:
        for name in (
            "socket",
            "socketpair",
            "connect",
            "accept",
            "accept4",
            "bind",
            "listen",
            "sendto",
            "sendmsg",
            "sendmmsg",
            "recvfrom",
            "recvmsg",
            "recvmmsg",
            "getsockname",
            "getpeername",
            "setsockopt",
            "getsockopt",
            "shutdown",
        ):
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and seccomp.seccomp_rule_add(context, deny, number, 0) != 0:
                raise RuntimeError("SECCOMP_RULE_FAILED")
        if seccomp.seccomp_load(context) != 0:
            raise RuntimeError("SECCOMP_LOAD_FAILED")
    finally:
        seccomp.seccomp_release(context)
    try:
        socket.socket()
    except PermissionError:
        return
    raise RuntimeError("NETWORK_SELF_TEST_FAILED")


def _decode(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("TEXT_ENCODING_UNSUPPORTED")


def _records(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> dict[str, object]:
    if not headers or len(headers) > MAX_COLUMNS or any(len(x) > 200 for x in headers):
        raise ValueError("SOURCE_SCHEMA_MISMATCH")
    samples: list[dict[str, str | None]] = []
    formula_cells = 0
    pii_hits = 0
    prompt_hits = 0
    row_count = 0
    prompt = re.compile(
        r"ignore previous instructions|approve this source|call localhost|"
        r"read environment variables|reveal system prompt",
        re.I,
    )
    email = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
    cpf = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
    for row_count, row_values in enumerate(rows, 1):
        if row_count > MAX_ROWS:
            raise ValueError("ROW_LIMIT")
        clean = {
            str(key)[:200]: (
                str(row_values[index])[:MAX_FIELD]
                if index < len(row_values) and row_values[index] is not None
                else None
            )
            for index, key in enumerate(headers)
        }
        for item in clean.values():
            if item:
                formula_cells += int(item.lstrip().startswith(("=", "+", "-", "@")))
                pii_hits += int(bool(email.search(item) or cpf.search(item)))
                prompt_hits += int(bool(prompt.search(item)))
        if len(samples) < MAX_SAMPLE_ROWS:
            samples.append(clean)
    return {
        "schema_version": "quarantine-extractor-v1",
        "headers": list(headers),
        "samples": samples,
        "quality": {
            "formula_cells": formula_cells,
            "pii_hits": pii_hits,
            "prompt_injection_hits": prompt_hits,
            "rows": row_count,
            "columns": len(headers),
        },
    }


def profile_csv(payload: bytes, delimiter: str | None = None) -> dict[str, object]:
    value = _decode(payload)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(value[:65536], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(io.StringIO(value), delimiter=delimiter)
    try:
        headers = [str(item).strip() for item in next(reader)]
    except StopIteration as exc:
        raise ValueError("EMPTY_DATASET") from exc
    return _records(headers, [list(row) for row in reader])


def profile_json(payload: bytes, ndjson: bool = False) -> dict[str, object]:
    if ndjson:
        value: object = [json.loads(line) for line in _decode(payload).splitlines() if line.strip()]
    else:
        value = json.loads(_decode(payload))
    if isinstance(value, dict):
        for key in ("value", "data", "results", "records", "parametros"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if not isinstance(value, list) or not value or not all(isinstance(item, dict) for item in value):
        raise ValueError("JSON_RECORDS_REQUIRED")
    headers: list[str] = []
    for item in value:
        assert isinstance(item, dict)
        for key in item:
            if str(key) not in headers:
                headers.append(str(key))
    rows = [[item.get(header) for header in headers] for item in value if isinstance(item, dict)]
    return _records(headers, rows)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "table" and not self.rows:
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.current_row = []
        elif self.in_table and tag in {"th", "td"}:
            self.in_cell = True
            self.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_table and tag in {"th", "td"}:
            self.current_row.append(" ".join("".join(self.current_cell).split()))
            self.in_cell = False
        elif self.in_table and tag == "tr" and self.current_row:
            self.rows.append(self.current_row)
        elif self.in_table and tag == "table":
            self.in_table = False


def profile_html(payload: bytes) -> dict[str, object]:
    parser = _TableParser()
    parser.feed(_decode(payload))
    if len(parser.rows) < 2:
        raise ValueError("HTML_TABLE_REQUIRED")
    return _records(parser.rows[0], parser.rows[1:])


def _xlsx_rows(payload: bytes) -> tuple[list[str], list[list[object]]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if any(PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts for name in names):
            raise ValueError("ZIP_PATH_TRAVERSAL")
        if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError("ZIP_EXPANSION_LIMIT")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in root]
        sheets = sorted(name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        if not sheets:
            raise ValueError("XLSX_SHEET_REQUIRED")
        root = ElementTree.fromstring(archive.read(sheets[0]))
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        output: list[list[object]] = []
        for row in root.iter(namespace + "row"):
            values: list[object] = []
            for cell in row.findall(namespace + "c"):
                formula = cell.find(namespace + "f")
                value = cell.find(namespace + "v")
                raw = value.text if value is not None else ""
                if cell.get("t") == "s" and raw:
                    raw = shared[int(raw)]
                values.append(raw if formula is None else raw)
            output.append(values)
            if len(output) > MAX_ROWS + 1:
                raise ValueError("ROW_LIMIT")
    if len(output) < 2:
        raise ValueError("EMPTY_DATASET")
    return [str(item) for item in output[0]], output[1:]


def profile_zip(payload: bytes) -> dict[str, object]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        candidates = []
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("ZIP_PATH_TRAVERSAL")
            if item.file_size > 50 * 1024 * 1024 or (item.compress_size and item.file_size / item.compress_size > 100):
                raise ValueError("ZIP_EXPANSION_LIMIT")
            if path.suffix.casefold() in {".csv", ".tsv", ".json", ".ndjson", ".jsonl", ".html", ".htm", ".xlsx"}:
                candidates.append(item)
        if not candidates or sum(item.file_size for item in candidates) > 100 * 1024 * 1024:
            raise ValueError("ZIP_SUPPORTED_MEMBER_REQUIRED")
        selected = sorted(candidates, key=lambda item: (item.file_size, item.filename))[0]
        return profile_payload(archive.read(selected), selected.filename.rsplit(".", 1)[-1].casefold())


def profile_payload(payload: bytes, data_format: str) -> dict[str, object]:
    if data_format == "csv":
        return profile_csv(payload)
    if data_format == "tsv":
        return profile_csv(payload, "\t")
    if data_format in {"json", "json-stat", "odata", "ckan", "socrata", "arcgis"}:
        return profile_json(payload)
    if data_format in {"ndjson", "jsonl"}:
        return profile_json(payload, True)
    if data_format in {"html", "htm"}:
        return profile_html(payload)
    if data_format == "xlsx":
        headers, rows = _xlsx_rows(payload)
        return _records(headers, rows)
    if data_format == "zip":
        return profile_zip(payload)
    raise ValueError("FORMAT_UNSUPPORTED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument(
        "--format",
        choices=(
            "csv",
            "tsv",
            "json",
            "ndjson",
            "jsonl",
            "json-stat",
            "html",
            "htm",
            "xlsx",
            "zip",
            "odata",
            "ckan",
            "socrata",
            "arcgis",
        ),
        required=True,
    )
    parser.add_argument("--enforce-no-network", action="store_true", required=True)
    args = parser.parse_args()
    block_network()
    with os.fdopen(args.fd, "rb", closefd=False) as source:
        payload = source.read(50 * 1024 * 1024 + 1)
    if len(payload) > 50 * 1024 * 1024:
        raise SystemExit("FILE_TOO_LARGE")
    result = profile_payload(payload, args.format)
    result["network_policy"] = "seccomp_socket_syscalls_denied"
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
