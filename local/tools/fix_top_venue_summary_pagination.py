from pathlib import Path

from docx import Document


path = Path(r"F:\作业\aibola\reports\top_venue_paper_benchmark_summary_20260820\正式论文对比实验结果总结_RSOS_2024.docx")
doc = Document(path)
for paragraph in doc.paragraphs:
    if paragraph.text.strip() == "1. 论文身份与发表等级":
        paragraph.paragraph_format.page_break_before = True
        break
else:
    raise RuntimeError("target heading not found")
doc.save(path)
