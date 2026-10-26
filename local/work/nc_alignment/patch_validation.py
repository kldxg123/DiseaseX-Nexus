from pathlib import Path

p = Path(r"F:\作业\aibola\work\nc_alignment\validate_and_seal.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    '"si_removed_old_ar2_metrics": "11.34" not in si_text and "12.80" not in si_text,',
    '"si_old_ar2_values_only_in_audit_map": "Published AR(2) reconstruction" not in si_text,',
)
p.write_text(s, encoding="utf-8")
print("patched")
