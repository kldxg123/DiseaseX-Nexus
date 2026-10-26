import hashlib
from pathlib import Path


root = Path(r"F:\作业\aibola\reports\sci_q1_ewnet_benchmark_optimization_20260822")
manifest = root / "MANIFEST.sha256"
docx = root / "SCI一区论文EWNet对比试验优化结果报告_20260822.docx"
lines = manifest.read_text(encoding="utf-8").splitlines()
lines = [line for line in lines if not line.endswith(f"  {docx.name}")]
lines.append(f"{hashlib.sha256(docx.read_bytes()).hexdigest()}  {docx.name}")
manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
