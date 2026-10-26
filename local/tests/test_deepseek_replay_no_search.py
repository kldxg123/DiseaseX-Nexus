from __future__ import annotations

from common.db import get_session, init_db
from deepseek_web_search_agent.extraction_replay import run_structured_extraction_replay

from tests.test_deepseek_structured_extraction_replay import make_safe_external_run_dir


def test_replay_from_existing_summary_never_runs_search_or_page_read(tmp_path) -> None:
    run_dir = make_safe_external_run_dir(tmp_path)
    database_url = f"sqlite:///{(tmp_path / 'no-search.sqlite').as_posix()}"
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        replay = run_structured_extraction_replay(
            session=session,
            run_dir=run_dir,
            allow_deepseek_api=False,
            output_dir=str(tmp_path / "reports"),
        )

    assert replay["no_tavily_rerun"] is True
    assert replay["page_read_request_count"] == 0
    assert replay["source_run_id"] == "run_deepseek_existing"
    assert replay["warnings"] == ["deepseek_extraction_skipped_not_authorized"]

