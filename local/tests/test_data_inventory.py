from pathlib import Path

from src.data.audit_current_data import _classify_source, _csv_summary


def test_csv_summary_reads_temp_fixture(tmp_path: Path):
    csv_path = tmp_path / "ebola_data_db_format.csv"
    csv_path.write_text("Indicator,Country,Date,value\nCases,A,2014-01-01,1\n", encoding="utf-8")
    summary = _csv_summary(csv_path)
    assert summary["rows"] == 1
    assert summary["columns"] == ["Indicator", "Country", "Date", "value"]


def test_ebola_africa_classified_background_only(tmp_path: Path):
    path = tmp_path / "ebola_africa.csv"
    path.write_text("country,cases_2019,rate_2019\nA,1,0.1\n", encoding="utf-8")
    assert _classify_source(path) == "annual_background_only"
