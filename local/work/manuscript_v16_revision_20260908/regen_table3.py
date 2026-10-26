import sys, types, importlib.util, hashlib
from pathlib import Path

# stub reportlab (only used for optional PDF compilation)
for name in ("reportlab", "reportlab.lib", "reportlab.lib.utils", "reportlab.pdfgen", "reportlab.pdfgen.canvas"):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
sys.modules["reportlab.lib.utils"].ImageReader = object
sys.modules["reportlab.pdfgen"].canvas = types.SimpleNamespace(Canvas=None)
sys.modules["reportlab"].lib = sys.modules["reportlab.lib"]
sys.modules["reportlab"].pdfgen = sys.modules["reportlab.pdfgen"]
sys.modules["reportlab.lib"].utils = sys.modules["reportlab.lib.utils"]

STAGE = Path(__file__).resolve().parent
GEN = Path(r"F:\作业\aibola\reports\nc_submission_revision_20260824\table_style_review_20260824\generate_journal_tables_pillow.py")

spec = importlib.util.spec_from_file_location("gen", GEN)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
gen.SOURCE = STAGE / "table3"
gen.OUTPUT = STAGE

cols, rows = gen.read_csv("Main_Table_3_Evaluation_Tracks.csv")
out = gen.render_table(
    number=3,
    caption="Governance-aware evaluation tracks and certification decisions.",
    columns=cols,
    rows=rows,
    widths=[0.055, 0.18, 0.20, 0.105, 0.12, 0.10, 0.24],
    numeric_columns={3, 4, 5},
    note=(
        "Freshness is an evaluation identity rather than a recency label. Historical or repeatedly opened windows cannot support fresh blind-test certification. "
        "Track C was consumed once but failed the prespecified WAPE gate (equivalent to the agreement gate under the display definition); "
        "Track D is registered and locked as a fresh held-out target but has never been opened; "
        "Track E is retrospective and did not confirm post-COVID generalization."
    ),
    stem="Table_3_Governance_Aware_Evaluation_Tracks_Journal_Style",
)
print(out, out.stat().st_size, hashlib.sha256(out.read_bytes()).hexdigest()[:16])
