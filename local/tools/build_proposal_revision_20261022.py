"""Patch the retained school form, preserving unrelated OOXML parts."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree as E

from proposal_revision_content_20261022 import ABSTRACT, KEYWORDS, BLOCKS, REFERENCES, COMMENT_MAP

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "output/proposal_revision_20261022"
SOURCE = Path("F:/微信聊天/xwechat_files/wxid_hmoavx3grubj22_8875/temp/RWTemp/2026-09/809eeb85455e208eef7937d1d45cc860/安浩然_开题报告_余乐审阅批注版.docx")
OUTPUT = WORK / "安浩然_开题报告_依据批注修订完整版_20261022.docx"
SOURCE_SHA = "0b5a419c6760ed9b274d882367f390a5d20722f55f7e013f8519f98a87a3bdaa"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NS = {"w": W, "m": M}


def w(name, **attrs):
    node = E.Element(f"{{{W}}}{name}")
    for key, value in attrs.items(): node.set(f"{{{W}}}{key}", str(value))
    return node


def m(name, text=None, **attrs):
    node = E.Element(f"{{{M}}}{name}")
    for key, value in attrs.items(): node.set(f"{{{M}}}{key}", str(value))
    if text is not None: node.text = text
    return node


def mr(text):
    run = m("r")
    run.append(m("t", text))
    return run


def sub(base, idx):
    node = m("sSub")
    e = m("e"); e.append(mr(base) if isinstance(base, str) else base)
    lower = m("sub"); lower.append(mr(idx))
    node.extend([e, lower]); return node


def sigma(terms, lower="i=1", upper="n"):
    node = m("nary"); pr=m("naryPr")
    pr.extend([m("chr", val="∑"), m("limLoc", val="subSup")])
    lo=m("sub"); lo.append(mr(lower)); hi=m("sup"); hi.append(mr(upper))
    body=m("e"); body.extend(terms)
    node.extend([pr,lo,hi,body]); return node


def equation(which):
    para = paragraph("", "equation")
    omathp=m("oMathPara"); props=m("oMathParaPr"); props.append(m("jc", val="center")); omathp.append(props)
    omath=m("oMath")
    if which == "score":
        omath.append(mr("S(m,x)=100"))
        omath.append(sigma([sub("w","j"), sub("s","j"), mr("(m,x)")], "j=1", "6"))
        omath.extend([mr("，"),sub("w","j"),mr("≥0，")])
        omath.append(sigma([sub("w","j")], "j=1", "6")); omath.append(mr("=1"))
    else:
        hat=m("acc"); ap=m("accPr"); ap.append(m("chr",val="̂")); hat.append(ap)
        he=m("e"); he.append(mr("y")); hat.append(he)
        numerator=sigma([mr("|"),sub("y","i"),mr("−"),sub(hat,"i"),mr("|")])
        denominator=sigma([mr("|"),sub("y","i"),mr("|")])
        fraction=m("f"); num=m("num"); num.append(numerator); den=m("den"); den.append(denominator)
        fraction.extend([num,den]); omath.extend([mr("WAPE="),fraction,mr("×100%")])
    omathp.append(omath); para.append(omathp); return para


STYLES={"p":"ProposalBody", "h1":"ProposalHeading1", "h2":"ProposalHeading2", "h3":"ProposalHeading3", "caption":"ProposalCaption", "ref":"ProposalReference", "outline":"ProposalOutline", "equation":"ProposalEquation", "table":"ProposalTableText"}


def paragraph(text, kind="p"):
    para=w("p"); pr=w("pPr"); pr.append(w("pStyle",val=STYLES[kind]))
    pr.append(w("keepNext",val="1" if kind in {"h1","h2","h3","caption"} else "0"))
    pr.append(w("keepLines",val="0"))
    pr.append(w("pageBreakBefore",val="0"))
    pr.append(w("widowControl"))
    para.append(pr)
    if text:
        run=w("r"); t=w("t"); t.text=text; t.set("{http://www.w3.org/XML/1998/namespace}space","preserve")
        run.append(t); para.append(run)
    return para


def create_table(headers, rows, widths):
    tbl=w("tbl"); pr=w("tblPr")
    pr.extend([w("tblW",w=sum(widths),type="dxa"),w("tblLayout",type="fixed"),w("jc",val="center")])
    borders=w("tblBorders")
    for edge in ["top","left","bottom","right","insideH","insideV"]:
        borders.append(w(edge,val="single",sz="4",color="D9D9D9"))
    pr.append(borders)
    margins=w("tblCellMar")
    for edge,size in [("top",70),("bottom",70),("left",80),("right",80)]: margins.append(w(edge,w=size,type="dxa"))
    pr.append(margins); tbl.append(pr)
    grid=w("tblGrid")
    for width in widths: grid.append(w("gridCol",w=width))
    tbl.append(grid)
    for idx,values in enumerate([headers]+rows):
        row=w("tr"); rp=w("trPr"); rp.append(w("cantSplit"))
        if idx==0: rp.append(w("tblHeader"))
        row.append(rp)
        for width,value in zip(widths,values):
            cell=w("tc"); cp=w("tcPr"); cp.extend([w("tcW",w=width,type="dxa"),w("vAlign",val="center")])
            if idx==0: cp.append(w("shd",val="clear",color="auto",fill="E7E6E6"))
            cell.append(cp); para=paragraph(value,"table")
            # Word does not repeat headers for a nested table spanning pages.
            # Keep these compact comparison tables intact inside the school form.
            para.find(f"{{{W}}}pPr").find(f"{{{W}}}keepNext").set(f"{{{W}}}val","1" if idx<len(rows) else "0")
            if idx==0:
                para.find(f"{{{W}}}pPr").append(w("jc",val="center"))
                run=para.find(f"{{{W}}}r"); rpr=w("rPr"); rpr.append(w("b")); run.insert(0,rpr)
            cell.append(para); row.append(cell)
        tbl.append(row)
    return tbl


def style_node(style_id, name, size, east="宋体", bold=False, before=0, after=0, first=0, hanging=False, level=None, alignment=None):
    node=w("style",type="paragraph",styleId=style_id); node.append(w("name",val=name)); node.append(w("basedOn",val="Normal"))
    pr=w("pPr"); pr.append(w("spacing",line=240,lineRule="auto",before=before,after=after))
    if first: pr.append(w("ind",firstLine=first))
    if hanging: pr.append(w("ind",left=420,hanging=420))
    if level is not None: pr.extend([w("outlineLvl",val=level),w("keepNext")])
    if alignment: pr.append(w("jc",val=alignment))
    pr.append(w("widowControl")); node.append(pr)
    rp=w("rPr"); rp.extend([w("rFonts",ascii="Times New Roman",hAnsi="Times New Roman",eastAsia=east),w("color",val="000000"),w("sz",val=int(size*2)),w("szCs",val=int(size*2))])
    rp.append(w("b",val="1" if bold else "0")); node.append(rp)
    return node


def serialize(root): return E.tostring(root,encoding="UTF-8",xml_declaration=True,standalone=True)


def main():
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==SOURCE_SHA
    assert len(ABSTRACT)<=400 and len(COMMENT_MAP)==21
    WORK.mkdir(parents=True, exist_ok=True)
    with ZipFile(SOURCE) as package:
        parts={name:package.read(name) for name in package.namelist()}
    original_parts={name:hashlib.sha256(data).hexdigest() for name,data in parts.items()}
    doc=E.fromstring(parts["word/document.xml"])
    top_tables=doc.xpath("./w:body/w:tbl",namespaces=NS)
    assert len(top_tables)==2
    original_review=E.tostring(top_tables[1].findall(f"{{{W}}}tr")[1])
    bodycell=top_tables[1].xpath("./w:tr[1]/w:tc[1]",namespaces=NS)[0]
    for child in list(bodycell):
        if child.tag!=f"{{{W}}}tcPr": bodycell.remove(child)
    for block in BLOCKS:
        kind=block[0]
        if kind=="table": bodycell.append(create_table(block[1],block[2],block[3]))
        elif kind=="equation": bodycell.append(equation(block[1]))
        else: bodycell.append(paragraph(block[1],kind))
    if bodycell[-1].tag!=f"{{{W}}}p": bodycell.append(paragraph(""))
    # Update the existing abstract and keyword slots while preserving their cells.
    abstract_cell=top_tables[0].xpath("./w:tr[7]/w:tc[2]",namespaces=NS)[0]
    aps=abstract_cell.findall(f"{{{W}}}p")
    content=next(x for x in aps if "本研究" in "".join(x.xpath(".//w:t/text()",namespaces=NS)))
    oldpr=deepcopy(content.find(f"{{{W}}}pPr"))
    for child in list(content): content.remove(child)
    if oldpr is not None: content.append(oldpr)
    run=w("r"); rp=w("rPr"); rp.extend([w("rFonts",ascii="Times New Roman",hAnsi="Times New Roman",eastAsia="宋体"),w("sz",val=21),w("color",val="000000")]); run.append(rp)
    t=w("t"); t.text=ABSTRACT; run.append(t); content.append(run)
    keywords_cell=top_tables[0].xpath("./w:tr[8]/w:tc[last()]",namespaces=NS)[0]
    for x in list(keywords_cell):
        if x.tag!=f"{{{W}}}tcPr": keywords_cell.remove(x)
    keywords_cell.append(paragraph(KEYWORDS,"table"))
    # Clean final copy: remove comment ranges, not the supplied supervisor assessment.
    for node in doc.xpath("//w:commentRangeStart|//w:commentRangeEnd|//w:commentReference",namespaces=NS): node.getparent().remove(node)
    assert E.tostring(top_tables[1].findall(f"{{{W}}}tr")[1])==original_review
    # The retained form holds all narrative in one giant cell. Word otherwise
    # pushes that cell after an isolated heading and splits nested tables without
    # repeating their headers. Use contiguous borderless internal rows, preserving
    # the outer school form, so each compact comparison table can stay together.
    report_table=top_tables[1]
    outer_heading=next(x for x in doc.xpath("./w:body/w:p",namespaces=NS) if "".join(x.xpath(".//w:t/text()",namespaces=NS)).strip()=="二、报告正文")
    outer_heading.getparent().remove(outer_heading)
    contents=[paragraph("二、报告正文","h1")]+[deepcopy(x) for x in bodycell if x.tag!=f"{{{W}}}tcPr"]
    cellpr=deepcopy(bodycell.find(f"{{{W}}}tcPr"))
    original_row=report_table.findall(f"{{{W}}}tr")[0]
    report_table.remove(original_row)
    review_row=report_table.findall(f"{{{W}}}tr")[0]
    inner=report_table.find(f"{{{W}}}tblPr").find(f"{{{W}}}tblBorders").find(f"{{{W}}}insideH")
    inner.set(f"{{{W}}}val","nil")
    groups=[]; narrative=[]; idx=0
    while idx<len(contents):
        item=contents[idx]
        if idx+1<len(contents) and contents[idx+1].tag==f"{{{W}}}tbl":
            lead=[]
            while narrative and narrative[-1].tag==f"{{{W}}}p":
                style=narrative[-1].find("w:pPr/w:pStyle",NS)
                if style is None or style.get(f"{{{W}}}val") not in {STYLES["h1"],STYLES["h2"],STYLES["h3"]}: break
                lead.insert(0,narrative.pop())
            if narrative: groups.append((False,narrative)); narrative=[]
            end=paragraph("","table")
            end.find(f"{{{W}}}pPr").append(w("spacing",line=20,lineRule="exact",before=0,after=0))
            groups.append((True,lead+[item,contents[idx+1],end])); idx+=2
        else:
            narrative.append(item); idx+=1
    if narrative: groups.append((False,narrative))
    for pos,(keep,elements) in enumerate(groups):
        row=w("tr"); rp=w("trPr")
        if keep: rp.append(w("cantSplit"))
        row.append(rp); cell=w("tc"); cp=deepcopy(cellpr)
        borders=w("tcBorders")
        if pos==len(groups)-1: borders.append(w("bottom",val="single",color="000000",sz=12))
        cp.append(borders); cell.append(cp); cell.extend(elements); row.append(cell)
        report_table.insert(list(report_table).index(review_row),row)
    assert E.tostring(review_row)==original_review
    # Retain the cover but make its document-title semantic explicit.
    cover=doc.xpath("./w:body/w:p",namespaces=NS)[4]
    cp=cover.find(f"{{{W}}}pPr")
    if cp is None: cp=w("pPr"); cover.insert(0,cp)
    ps=cp.find(f"{{{W}}}pStyle")
    if ps is None: ps=w("pStyle",val="Title"); cp.insert(0,ps)
    else: ps.set(f"{{{W}}}val","Title")
    styles=E.fromstring(parts["word/styles.xml"])
    base_style=next((x.get(f"{{{W}}}styleId") for x in styles.findall(f"{{{W}}}style") if x.find(f"{{{W}}}name") is not None and x.find(f"{{{W}}}name").get(f"{{{W}}}val","").lower()=="normal"),None)
    definitions=[
        style_node("ProposalBody","Proposal Body",10.5,first=420),
        style_node("ProposalHeading1","Proposal Heading 1",14,"黑体",True,240,120,level=0),
        style_node("ProposalHeading2","Proposal Heading 2",12,"黑体",True,120,60,level=1),
        style_node("ProposalHeading3","Proposal Heading 3",10.5,"黑体",True,60,0,level=2),
        style_node("ProposalCaption","Proposal Caption",10.5,bold=True,before=120,after=60,alignment="center"),
        style_node("ProposalReference","Proposal Reference",10.5,hanging=True),
        style_node("ProposalOutline","Proposal Outline",10.5,first=420),
        style_node("ProposalEquation","Proposal Equation",10.5,before=100,after=100,alignment="center"),
        style_node("ProposalTableText","Proposal Table Text",10.5,alignment="left"),
    ]
    if not styles.xpath("./w:style[@w:styleId='Title']",namespaces=NS): definitions.append(style_node("Title","Title",24,bold=True,alignment="center"))
    if base_style:
        for node in definitions: node.find(f"{{{W}}}basedOn").set(f"{{{W}}}val",base_style)
    styles.extend(definitions)
    settings=E.fromstring(parts["word/settings.xml"])
    update=settings.find(f"{{{W}}}updateFields")
    if update is None: update=w("updateFields",val="true"); settings.append(update)
    else: update.set(f"{{{W}}}val","true")
    parts["word/document.xml"]=serialize(doc); parts["word/styles.xml"]=serialize(styles); parts["word/settings.xml"]=serialize(settings)
    for name in list(parts):
        if re.fullmatch(r"word/comments[^/]*\.xml",name): del parts[name]
    rels=E.fromstring(parts["word/_rels/document.xml.rels"])
    for rel in list(rels):
        if "comments" in rel.get("Type","").lower(): rels.remove(rel)
    parts["word/_rels/document.xml.rels"]=serialize(rels)
    ct=E.fromstring(parts["[Content_Types].xml"])
    for node in list(ct):
        if "/comments" in node.get("PartName",""): ct.remove(node)
    parts["[Content_Types].xml"]=serialize(ct)
    with ZipFile(OUTPUT,"w",ZIP_DEFLATED) as output:
        for name,data in parts.items(): output.writestr(name,data)
    mutable={"word/document.xml","word/styles.xml","word/settings.xml","word/comments.xml","word/_rels/document.xml.rels","[Content_Types].xml"}
    assert all(hashlib.sha256(data).hexdigest()==original_parts[name] for name,data in parts.items() if name not in mutable)
    text="\n".join(doc.xpath("//w:body//w:t/text()",namespaces=NS))
    assert "第三世界" not in ABSTRACT and "上海" not in ABSTRACT
    assert all(f"[{i}]" in text for i in range(1,len(REFERENCES)+1))
    assert not doc.xpath("//w:commentRangeStart|//w:commentReference",namespaces=NS)
    assert len(text)>10000
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==SOURCE_SHA
    (WORK/"comment_resolution_map.json").write_text(json.dumps(COMMENT_MAP,ensure_ascii=False,indent=2),encoding="utf-8")
    (WORK/"final_text.txt").write_text(text,encoding="utf-8")
    (WORK/"structure_qa.json").write_text(json.dumps({"source_sha256":SOURCE_SHA,"output":str(OUTPUT),"abstract_characters":len(ABSTRACT),"text_characters":len(text),"reviewer_comments_addressed":len(COMMENT_MAP),"references":len(REFERENCES),"new_tables":sum(b[0]=="table" for b in BLOCKS),"equations":2,"original_supervisor_assessment_preserved":True,"preserve_only_parts_identical":True},ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(OUTPUT),"abstract_characters":len(ABSTRACT),"text_characters":len(text),"references":len(REFERENCES)},ensure_ascii=False))


if __name__=="__main__": main()
