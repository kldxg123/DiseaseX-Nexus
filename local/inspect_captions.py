from pathlib import Path
from docx import Document
p=Path(r'F:\作业\aibola\reports\nc_submission_revision_20260824\NC_Submission_Updated_20260824\DiseaseX_Nexus_NC_Manuscript_v4_20260824.docx')
d=Document(p)
for i,x in enumerate(d.paragraphs):
    if x.style.name=='Caption' or x.text.strip().startswith(('Fig.','Figure')):
        print(i,x.style.name,repr(x.text))
