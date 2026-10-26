# ruff: noqa: E501
"""Shared setup for kg_qwen tests.

The tests in this directory are pure (no database, no Qwen). The pipeline
module imports every kg_qwen sub-module LAZILY inside functions, so importing
``epidemic_core.kg_qwen.pipeline`` works even while the sibling modules
(loop/qwen_client/agent_tools/plan_contract/contracts_ext/adapters_ext) are
still being written by other agents. No stubs are installed here on purpose:
nothing under test touches those modules. If a future test does need them
before they land, register minimal sys.modules stubs HERE (test-local only,
never in src/) and delete this note once the real modules exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
