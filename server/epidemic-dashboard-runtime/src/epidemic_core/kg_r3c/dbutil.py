"""Shared helpers for kg_r3c pipeline: DB access, stable UUIDs, hashing."""
from __future__ import annotations

import hashlib
import json
import os
import uuid

import psycopg

ROOT = "USER_HOME/projects/openclaw"
SOCK = os.path.join(ROOT, "runtime/postgres-run")
DSN = f"postgresql://postgres@/epidemic_openclaw?host={SOCK}&port=55432"
DSN = os.environ.get("KG_R3C_DSN", DSN)

NS = uuid.UUID("a3c1e7d2-5b6f-4a8b-9c0d-1e2f3a4b5c6d")  # kg_r3c namespace


def connect(autocommit=False):
    return psycopg.connect(DSN, autocommit=autocommit)


def stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(NS, "|".join(str(p) for p in parts)))


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_json(obj) -> str:
    return sha256_text(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def norm_text(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(s.split()).strip().lower()
