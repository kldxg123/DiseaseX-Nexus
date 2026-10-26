from __future__ import annotations

from pathlib import Path

import pytest

from epidemic_core.errors import ArtifactIntegrityError
from epidemic_core.storage import ArtifactStore


def test_content_addressed_idempotent_and_corruption(tmp_path:Path)->None:
    store=ArtifactStore(tmp_path);digest,path,created=store.put(b"fixture")
    assert created and store.read(digest)==b"fixture"
    assert store.put(b"fixture")[2] is False
    path.write_bytes(b"damaged")
    with pytest.raises(ArtifactIntegrityError):store.read(digest)


def test_path_traversal_rejected(tmp_path:Path)->None:
    store=ArtifactStore(tmp_path)
    for value in ("../escape","g"*64,"a"*63):
        with pytest.raises(ValueError):store.path_for(value)
