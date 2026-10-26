from pathlib import Path

p = Path(r"F:\作业\aibola\work\nc_alignment\build_alignment_package.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    "    for ri, row in enumerate(rows):\n        for ci, value in enumerate(row):\n            if ci >= len(table.rows[ri].cells):",
    "    for ri, row in enumerate(rows):\n        tr_pr = table.rows[ri]._tr.get_or_add_trPr()\n        tr_pr.append(OxmlElement(\"w:cantSplit\"))\n        if ri == 0:\n            tbl_header = OxmlElement(\"w:tblHeader\")\n            tbl_header.set(qn(\"w:val\"), \"true\")\n            tr_pr.append(tbl_header)\n        for ci, value in enumerate(row):\n            if ci >= len(table.rows[ri].cells):",
    1,
)
s = s.replace(
    "    for ri, row in enumerate(rows):\n        for ci, value in enumerate(row):\n            cell = table.cell(ri, ci)",
    "    for ri, row in enumerate(rows):\n        tr_pr = table.rows[ri]._tr.get_or_add_trPr()\n        tr_pr.append(OxmlElement(\"w:cantSplit\"))\n        if ri == 0:\n            tbl_header = OxmlElement(\"w:tblHeader\")\n            tbl_header.set(qn(\"w:val\"), \"true\")\n            tr_pr.append(tbl_header)\n        for ci, value in enumerate(row):\n            cell = table.cell(ri, ci)",
    1,
)
s = s.replace(
    "    set_picture(doc.paragraphs[40], assets[\"fig5\"], 6.4)\n    replace_table",
    "    set_picture(doc.paragraphs[40], assets[\"fig5\"], 6.4)\n    doc.paragraphs[35].paragraph_format.page_break_before = True\n    doc.paragraphs[42].paragraph_format.page_break_before = True\n    replace_table",
    1,
)
s = s.replace(
    "    for label, title, key in table_items:\n        doc.add_heading(f\"Supplementary Table {label}. {title}\", level=2)\n        add_table",
    "    for index, (label, title, key) in enumerate(table_items):\n        if index:\n            doc.add_page_break()\n        heading = doc.add_heading(f\"Supplementary Table {label}. {title}\", level=2)\n        heading.paragraph_format.keep_with_next = True\n        add_table",
    1,
)
p.write_text(s, encoding="utf-8")
print("patched")
