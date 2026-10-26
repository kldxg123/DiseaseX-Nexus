from pathlib import Path

from docx import Document


docx_path = Path(r"F:\作业\aibola\reports\sci_q1_ewnet_benchmark_optimization_20260822\SCI一区论文EWNet对比试验优化结果报告_20260822.docx")
doc = Document(docx_path)
for paragraph in doc.paragraphs:
    if paragraph.text.strip() == "8. 下一步优化方案（不污染本次测试）":
        paragraph.paragraph_format.page_break_before = True
        paragraph.paragraph_format.keep_with_next = True
        break
doc.save(docx_path)
