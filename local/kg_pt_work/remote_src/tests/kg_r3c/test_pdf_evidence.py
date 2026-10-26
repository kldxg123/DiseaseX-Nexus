"""Evidence extraction tests: page spans, offsets, disease links, params, provenance."""
import io

import pytest

from epidemic_core.kg_r3c.evidence import extract_from_page, EXTRACTOR_VERSION


def minimal_pdf(text: str) -> bytes:
    """Hand-built one-page PDF with extractable text."""
    stream = f"BT /F1 10 Tf 50 700 Td ({text}) Tj ET".encode()
    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objs) + 1))
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objs) + 1, xref))
    return out.getvalue()


class TestMinimalPDFFixture:
    def test_fixture_parses(self):
        pypdf = pytest.importorskip("pypdf")
        r = pypdf.PdfReader(io.BytesIO(minimal_pdf("negative binomial model")))
        assert len(r.pages) == 1
        assert "negative binomial" in r.pages[0].extract_text()


class TestSpanExtraction:
    def test_model_family_detected(self):
        hits = extract_from_page("We fit a negative binomial DGLM to weekly dengue counts in Brazil.")
        fams = {h[1] for h in hits}
        assert "Negative-Binomial DGLM" in fams

    def test_disease_link_on_same_page(self):
        hits = extract_from_page("A renewal equation estimate of R_t for the Ebola outbreak.")
        for kind, family, diseases, generic, span, s, e, extra in hits:
            if family == "Renewal (Cori R_t)":
                assert "ebola-disease" in diseases

    def test_span_offsets_valid(self):
        text = "prefix " * 50 + "gamma-poisson model" + " suffix" * 50
        hits = extract_from_page(text)
        gp = next(h for h in hits if h[1] == "Gamma-Poisson DGLM")
        assert 0 <= gp[5] < gp[6] <= len(text)
        assert "gamma-poisson" in gp[4].lower()

    def test_parameter_declaration(self):
        hits = extract_from_page("The serial interval had a mean of 4.5 days for influenza.")
        params = [h for h in hits if h[0] == "PARAMETER_DECLARATION"]
        assert params and params[0][7]["param_name"] == "si_mean"
        assert params[0][7]["param_value"] == 4.5

    def test_discount_param(self):
        hits = extract_from_page("We used a discount factor of 0.95 in the DGLM.")
        params = [h for h in hits if h[0] == "PARAMETER_DECLARATION"]
        assert any(p[7]["param_name"] == "discount" and p[7]["param_value"] == 0.95
                   for p in params)

    def test_no_model_no_hits(self):
        assert extract_from_page("The weather was nice and the control group did well.") == []

    def test_metadata_like_text_no_disease_overreach(self):
        # title-only style text without disease keywords must not link diseases
        hits = extract_from_page("Kalman filtering bibliography review of methods.")
        for h in hits:
            assert h[2] == []

    def test_span_length_bounded(self):
        hits = extract_from_page("x " * 500 + "zero-inflated hurdle model" + " y" * 500)
        h = hits[0]
        assert len(h[4]) <= 500  # windowed anchor, not the whole page

    def test_extractor_version_frozen(self):
        assert EXTRACTOR_VERSION == "kg_r3c.evidence.v1"
