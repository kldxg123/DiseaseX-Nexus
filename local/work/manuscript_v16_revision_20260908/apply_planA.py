# -*- coding: utf-8 -*-
"""Plan A: fix citation ordering without renumbering the reference list.

1. Remove the "Machine-learning models ... dengue forecasting 26." sentence from
   Introduction paragraph 2 (ref 26 was cited before refs 12-25).
2. Re-insert the same sentence in the Discussion "Data heterogeneity" paragraph,
   right before the "Richer covariate profiles ..." sentence (after ref 25's first
   appearance, before refs 27-32).
3. Cite the previously uncited refs 44,45,46 at the end of the "Planned prospective
   analyses ... family of primary comparisons" sentence in Methods (between the
   first appearances of refs 41-43 and refs 47,48).
"""
import re, zipfile, sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(r"F:\作业\aibola")
F = ROOT / "DiseaseX_Nexus_NC_Manuscript_v16_textonly_20260908.docx"

zin = zipfile.ZipFile(F)
xml = zin.read("word/document.xml").decode("utf-8")
log = []

def para_containing(x, anchor):
    i = x.find(anchor)
    assert i > 0, f"anchor missing: {anchor[:50]!r}"
    s = max(x.rfind('<w:p ', 0, i), x.rfind('<w:p>', 0, i))
    e = x.find('</w:p>', i) + 6
    return s, e

# ---------- 1. delete the ML sentence + its citation from Introduction ----------
s, e = para_containing(xml, "Machine-learning models have also combined")
para = xml[s:e]
runs = list(re.finditer(r'<w:r\b[^>]*>(?:(?!</w:r>).)*?</w:r>', para, re.S))
texts = []
for rm in runs:
    tm = re.search(r'<w:t[^>]*>((?:(?!</w:t>).)*)</w:t>', rm.group(0), re.S)
    texts.append(tm.group(1) if tm else "")
idx = next(i for i, t in enumerate(texts) if "Machine-learning models have also combined" in t)
# expect following runs: ' ', SUP '26', '. '
assert texts[idx + 1].strip() == "" and texts[idx + 2] == "26" and "vertAlign" in runs[idx + 2].group(0), texts[idx:idx + 4]
assert texts[idx + 3] == ". ", repr(texts[idx + 3])
start_del = runs[idx].start()
end_del = runs[idx + 3].end()
para_new = para[:start_del] + para[end_del:]
xml = xml[:s] + para_new + xml[e:]
log.append("OK  intro-ML-sentence-removed")

# ---------- 2. insert the sentence into Discussion before 'Richer covariate profiles' ----------
s, e = para_containing(xml, "Data heterogeneity remains a major constraint")
para = xml[s:e]
# rPr templates from this paragraph
sup_run = re.search(r'<w:r\b[^>]*>(?:(?!</w:r>).)*?<w:vertAlign w:val="superscript"/>(?:(?!</w:r>).)*?</w:r>', para, re.S).group(0)
sup_rpr = re.search(r'<w:rPr>.*?</w:rPr>', sup_run, re.S).group(0)
tm = re.search(r'(<w:t[^>]*>)((?:(?!</w:t>).)*)(</w:t>)', para, re.S)  # first text run
first_run = re.search(r'<w:r\b[^>]*>(?:(?!</w:r>).)*?</w:r>', para, re.S).group(0)
txt_rpr_m = re.search(r'<w:rPr>.*?</w:rPr>', first_run, re.S)
txt_rpr = txt_rpr_m.group(0) if txt_rpr_m else ""
anchor2 = "Richer covariate profiles may therefore be required"
i = para.find(anchor2)
assert i > 0
# split the w:t that contains anchor2
tm2 = re.search(r'(<w:t[^>]*>)((?:(?!</w:t>).)*Richer covariate profiles may therefore be required(?:(?!</w:t>).)*)(</w:t>)', para, re.S)
assert tm2, "target w:t not found in discussion para"
full = tm2.group(2)
cut = full.find(anchor2)
before, after = full[:cut], full[cut:]
run_m = re.search(r'<w:r\b[^>]*>(?:(?!</w:r>).)*Richer covariate profiles may therefore be required(?:(?!</w:r>).)*?</w:r>', para, re.S)
run_xml = run_m.group(0)
rattr = re.match(r'<w:r\b[^>]*>', run_xml).group(0)
new_parts = (
    f'{rattr}{txt_rpr}<w:t xml:space="preserve">{before}Machine-learning models have also combined '
    f'epidemiological and meteorological predictors for dengue forecasting </w:t></w:r>'
    f'<w:r>{sup_rpr}<w:t xml:space="preserve">26</w:t></w:r>'
    f'{rattr}{txt_rpr}<w:t xml:space="preserve"> </w:t></w:r>'
    f'{rattr}{txt_rpr}<w:t xml:space="preserve">{after}</w:t></w:r>'
)
para_new = para[:run_m.start()] + new_parts + para[run_m.end():]
xml = xml[:s] + para_new + xml[e:]
log.append("OK  ml-sentence-moved-to-discussion")

# ---------- 3. cite 44,45,46 at end of 'primary comparisons' sentence ----------
s, e = para_containing(xml, "Planned prospective analyses will therefore use")
para = xml[s:e]
sup_rpr2 = sup_rpr  # same superscript rPr shape (Times New Roman inherited)
anchor3 = "and adjust for the prespecified family of primary comparisons."
assert anchor3 in para
run_m = re.search(r'<w:r\b[^>]*>(?:(?!</w:r>).)*and adjust for the prespecified family of primary comparisons\.(?:(?!</w:r>).)*?</w:r>', para, re.S)
run_xml = run_m.group(0)
rattr = re.match(r'<w:r\b[^>]*>', run_xml).group(0)
tm3 = re.search(r'(<w:t[^>]*>)((?:(?!</w:t>).)*)(</w:t>)', run_xml, re.S)
full = tm3.group(2)
before, after = full.split(anchor3, 1)
new_parts = (
    f'{rattr}{txt_rpr}<w:t xml:space="preserve">{before}and adjust for the prespecified family of primary comparisons </w:t></w:r>'
    f'<w:r>{sup_rpr2}<w:t xml:space="preserve">44,45,46</w:t></w:r>'
    f'{rattr}{txt_rpr}<w:t xml:space="preserve">.{after}</w:t></w:r>'
)
para_new = para[:run_m.start()] + new_parts + para[run_m.end():]
xml = xml[:s] + para_new + xml[e:]
log.append("OK  refs-44-46-cited")

# ---------- write back ----------
tmp = F.with_suffix(".tmp.docx")
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        if item.filename == "word/document.xml":
            zout.writestr(item, xml.encode("utf-8"))
        else:
            zout.writestr(item, zin.read(item.filename))
zin.close()
tmp.replace(F)
print("\n".join(log))

# ---------- verify: citation first-appearance order ----------
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
z = zipfile.ZipFile(F)
root = ET.fromstring(z.read("word/document.xml"))
paras = ["".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p")]
ref_start = next(i for i, t in enumerate(paras) if t.strip() == "References")
first = {}
order = []
for i, p in enumerate(root.iter(W + "p")):
    if i >= ref_start:
        break
    for r in p.iter(W + "r"):
        va = r.find(f"{W}rPr/{W}vertAlign")
        t = "".join(x.text or "" for x in r.iter(W + "t")).strip()
        if va is not None and va.get(W + "val") == "superscript":
            for num in t.split(","):
                num = num.strip()
                if re.fullmatch(r"\d{1,2}", num):
                    n = int(num)
                    if n not in first:
                        first[n] = i
                        order.append(n)
assert order == sorted(order), f"order broken: {order}"
assert sorted(first) == list(range(1, 49)), f"uncited refs remain: {[n for n in range(1,49) if n not in first]}"
print("VERIFY PASS: first-appearance order strictly 1..48, all refs cited")
import hashlib
print("sha256:", hashlib.sha256(F.read_bytes()).hexdigest())
