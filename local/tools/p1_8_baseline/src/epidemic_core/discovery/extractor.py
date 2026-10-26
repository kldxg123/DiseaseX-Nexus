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

MAX_ROWS = 10_000
MAX_COLUMNS = 100
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


def profile_csv(payload: bytes) -> dict[str, object]:
    value = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(value))
    headers = list(reader.fieldnames or [])
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
    for row_count, row in enumerate(reader, 1):
        if row_count > MAX_ROWS:
            raise ValueError("ROW_LIMIT")
        clean = {str(key)[:200]: (str(item)[:MAX_FIELD] if item is not None else None) for key, item in row.items()}
        for item in clean.values():
            if item:
                formula_cells += int(item.lstrip().startswith(("=", "+", "-", "@")))
                pii_hits += int(bool(email.search(item) or cpf.search(item)))
                prompt_hits += int(bool(prompt.search(item)))
        if len(samples) < MAX_SAMPLE_ROWS:
            samples.append(clean)
    return {
        "schema_version": "quarantine-extractor-v1",
        "headers": headers,
        "samples": samples,
        "quality": {
            "formula_cells": formula_cells,
            "pii_hits": pii_hits,
            "prompt_injection_hits": prompt_hits,
            "rows": row_count,
            "columns": len(headers),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--format", choices=("csv",), required=True)
    parser.add_argument("--enforce-no-network", action="store_true", required=True)
    args = parser.parse_args()
    block_network()
    with os.fdopen(args.fd, "rb", closefd=False) as source:
        payload = source.read(50 * 1024 * 1024 + 1)
    if len(payload) > 50 * 1024 * 1024:
        raise SystemExit("FILE_TOO_LARGE")
    result = profile_csv(payload)
    result["network_policy"] = "seccomp_socket_syscalls_denied"
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
