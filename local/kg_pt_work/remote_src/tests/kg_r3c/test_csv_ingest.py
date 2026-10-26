"""CSV ingest tests: encoding, schema, duplicates, entity linking."""
import pytest

from epidemic_core.kg_r3c.csv_ingest import (
    decode_bytes, parse_zotero_csv, normalize_doi, title_fingerprint,
)

HEADER = "Key,Item Type,Publication Year,Author,Title,Publication Title,DOI,Url,Abstract Note,Date,Extra,File Attachments"
ROW1 = 'K1,journalArticle,2024,"Smith, John; Doe, Jane",A study of dengue forecasting,Journal,10.1234/abc,http://x,,2024,,"C:\\Zotero\\storage\\AA\\paper one.pdf; "'
ROW2 = 'K2,journalArticle,2025,"Wang, Li",新冠预测模型研究,期刊,,,摘要,2025,PMID: 12345,'


class TestEncoding:
    def test_utf8_sig(self):
        text, enc, _ = decode_bytes(("\ufeff" + HEADER).encode("utf-8"))
        assert enc == "utf-8-sig" and text.startswith("Key")

    def test_utf8_plain(self):
        text, enc, _ = decode_bytes(HEADER.encode("utf-8"))
        assert enc in ("utf-8-sig", "utf-8")

    def test_gb18030_chinese(self):
        raw = HEADER.encode("ascii") + b"\n" + ROW2.split(",")[0].encode("ascii") + b",gb," + "新冠预测".encode("gb18030") + b"\n"
        text, enc, _ = decode_bytes(raw)
        assert "新冠预测" in text and "?" not in text.splitlines()[1]

    def test_no_replacement_chars(self):
        raw = (HEADER + "\n" + ROW2).encode("utf-8-sig")
        text, enc, _ = decode_bytes(raw)
        assert "\uFFFD" not in text


class TestParsing:
    def test_basic_rows(self):
        csv_bytes = (HEADER + "\n" + ROW1 + "\n" + ROW2).encode("utf-8-sig")
        recs, meta = parse_zotero_csv(csv_bytes, "test.csv")
        assert meta["row_count_valid"] == 2
        assert meta["row_count_invalid"] == 0
        assert recs[0].doi == "10.1234/abc"
        assert recs[1].pmid == "12345"

    def test_attachment_basenames(self):
        csv_bytes = (HEADER + "\n" + ROW1).encode("utf-8")
        recs, _ = parse_zotero_csv(csv_bytes, "t.csv")
        assert recs[0].attachments == ["paper one.pdf"]

    def test_missing_title_invalid(self):
        bad = HEADER + "\nK3,journalArticle,2024,NoTitleAuthor,,Journal,,,,\n"
        recs, meta = parse_zotero_csv(bad.encode(), "t.csv")
        assert meta["row_count_valid"] == 0 and meta["row_count_invalid"] == 1

    def test_empty_lines_skipped(self):
        data = (HEADER + "\n" + ROW1 + "\n,,,,\n").encode()
        recs, meta = parse_zotero_csv(data, "t.csv")
        assert meta["row_count_valid"] == 1

    def test_chinese_title_preserved(self):
        recs, _ = parse_zotero_csv((HEADER + "\n" + ROW2).encode("utf-8-sig"), "t.csv")
        assert recs[0].title == "新冠预测模型研究"
        assert recs[0].authors == ["Wang, Li"]


class TestIdentity:
    def test_doi_normalization(self):
        assert normalize_doi("https://doi.org/10.1/X") == "10.1/x"
        assert normalize_doi("DOI: 10.1/Y ") == "10.1/y"
        assert normalize_doi("") == ""

    def test_identity_priority_doi(self):
        recs, _ = parse_zotero_csv((HEADER + "\n" + ROW1 + "\n" + ROW2).encode(), "t.csv")
        assert recs[0].identity_method == "DOI"
        assert recs[1].identity_method == "PMID"

    def test_identity_title_fallback(self):
        row = 'K9,journalArticle,2020,"Solo, A",Some Title,J,,,2020,,'
        recs, _ = parse_zotero_csv((HEADER + "\n" + row).encode(), "t.csv")
        assert recs[0].identity_method == "TITLE_YEAR_AUTHOR"
        assert recs[0].identity.startswith("title:some title|year:2020|fa:solo")

    def test_fingerprint_stability(self):
        a = title_fingerprint("The  Study!", "2024", "Smith, John")
        b = title_fingerprint("the study", "2024", "smith")
        assert a == b

    def test_duplicate_same_doi_same_identity(self):
        dup = ROW1.replace("K1", "K1B")
        recs, _ = parse_zotero_csv((HEADER + "\n" + ROW1 + "\n" + dup).encode(), "t.csv")
        assert recs[0].identity == recs[1].identity
