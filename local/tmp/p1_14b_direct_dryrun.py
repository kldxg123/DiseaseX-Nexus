from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import a100_p1_12 as p112  # noqa: E402


SCRIPT = r'''
set -euo pipefail
PROJECT="$1"
cd "$PROJECT"
mkdir -p /tmp/p1_14b_ebola_expansion/workbooks /tmp/p1_14b_ebola_expansion/reports
python3 - <<'PY'
from __future__ import annotations
import hashlib,json,re,urllib.request,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime,timezone

urls={
 "guinea":"https://stacks.cdc.gov/view/cdc/37575/cdc_37575_DS2.xlsx",
 "liberia":"https://stacks.cdc.gov/view/cdc/37469/cdc_37469_DS2.xlsx",
 "sierra_leone":"https://stacks.cdc.gov/view/cdc/37757/cdc_37757_DS2.xlsx",
}
root=Path("/tmp/p1_14b_ebola_expansion")
work=root/"workbooks"; reports=root/"reports"
NS="{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL="{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG="{http://schemas.openxmlformats.org/package/2006/relationships}"
def sha(b):return hashlib.sha256(b).hexdigest()
def col(ref):
 n=0
 for ch in re.sub("[^A-Z]","",ref.upper()): n=n*26+ord(ch)-64
 return n
def rown(ref):
 m=re.search(r"(\d+)",ref); return int(m.group(1)) if m else 0
def get(url):
 req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0","Accept":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*"})
 with urllib.request.urlopen(req,timeout=60) as r:
  data=r.read()
  return r.status,r.geturl(),r.headers.get("Content-Type"),data
def workbook(path):
 with zipfile.ZipFile(path) as zf:
  names=set(zf.namelist()); shared=[]
  if "xl/sharedStrings.xml" in names:
   ss=ET.fromstring(zf.read("xl/sharedStrings.xml"))
   for si in ss.findall(f".//{NS}si"): shared.append("".join(t.text or "" for t in si.findall(f".//{NS}t")))
  rels={}
  if "xl/_rels/workbook.xml.rels" in names:
   rr=ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
   for r in rr:
    rid=r.attrib.get("Id"); target=r.attrib.get("Target","")
    if rid: rels[rid]="xl/"+target.lstrip("/")
  wb=ET.fromstring(zf.read("xl/workbook.xml"))
  out={"entries":len(names),"has_content_types":"[Content_Types].xml" in names,"sheets":[]}
  for s in wb.findall(f".//{NS}sheet"):
   name=s.attrib.get("name"); rid=s.attrib.get(f"{REL}id"); target=rels.get(rid); state=s.attrib.get("state","visible")
   sh={"name":name,"state":state,"target":target,"dimension":None,"max_row":0,"max_column":0,"merged_ranges":[],"formula_cells":[],"headers":[],"sample_rows":[],"candidate_rows":[]}
   if not target or target not in names:
    out["sheets"].append(sh); continue
   ws=ET.fromstring(zf.read(target))
   dim=ws.find(f".//{NS}dimension"); sh["dimension"]=dim.attrib.get("ref") if dim is not None else None
   sh["merged_ranges"]=[m.attrib.get("ref") for m in ws.findall(f".//{NS}mergeCell")[:30]]
   rows=[]
   for r in ws.findall(f".//{NS}sheetData/{NS}row"):
    rn=int(r.attrib.get("r","0") or 0); vals={}
    for c in r.findall(f"{NS}c"):
     ref=c.attrib.get("r",""); t=c.attrib.get("t"); v=c.find(f"{NS}v"); f=c.find(f"{NS}f")
     val=v.text if v is not None else None
     if t=="s" and val is not None:
      try: val=shared[int(val)]
      except Exception: pass
     if t=="inlineStr": val="".join(x.text or "" for x in c.findall(f".//{NS}t"))
     vals[ref]={"value":val,"formula":f.text if f is not None else None}
     sh["max_row"]=max(sh["max_row"],rn); sh["max_column"]=max(sh["max_column"],col(ref))
     if f is not None and len(sh["formula_cells"])<20: sh["formula_cells"].append({"cell":ref,"formula":f.text,"cached":val})
    if vals: rows.append((rn,vals))
   for rn,vals in rows[:20]:
    cells=[vals[k]["value"] for k in sorted(vals,key=lambda x:(rown(x),col(x)))[:30]]
    non=[str(x) for x in cells if x not in (None,"")]
    if len(non)>=2 and not sh["headers"]: sh["headers"]=non
   for rn,vals in rows[:12]:
    sh["sample_rows"].append([{"cell":k,"value":vals[k]["value"],"formula":vals[k]["formula"]} for k in sorted(vals,key=lambda x:(rown(x),col(x)))[:15]])
   # derive simple row objects for rows after header: first 60 with same number columns
   if sh["headers"]:
    header_row=None
    for rn,vals in rows[:30]:
     non=[str(vals[k]["value"]) for k in sorted(vals,key=lambda x:(rown(x),col(x))) if vals[k]["value"] not in (None,"")]
     if non[:len(sh["headers"])]==sh["headers"][:len(non)] or len(non)>=len(sh["headers"])//2:
      header_row=rn; break
    header=sh["headers"]
    for rn,vals in rows:
     if header_row and rn<=header_row: continue
     ordered=[vals.get(f"{chr(64+i)}{rn}",{}).get("value") for i in range(1,min(len(header),26)+1)]
     if sum(x not in (None,"") for x in ordered)>=2:
      sh["candidate_rows"].append({"row":rn,"values":dict(zip(header,ordered))})
      if len(sh["candidate_rows"])>=20: break
   out["sheets"].append(sh)
  return out
res=[]
for k,u in urls.items():
 status,final,ctype,data=get(u)
 p=work/f"{k}.xlsx"; p.write_bytes(data)
 item={"key":k,"url":u,"final_url":final,"status":status,"content_type":ctype,"bytes":len(data),"sha256":sha(data),"magic":data[:8].hex(),"retrieved_at":datetime.now(timezone.utc).isoformat(),"workbook":workbook(p)}
 res.append(item)
(reports/"direct_xlsx_dryrun.json").write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(res,ensure_ascii=False))
PY
'''


def main() -> None:
    a100 = p112.p111.p110r3.previous.p19.a100
    client, _gate = a100.connect()
    try:
        data_root, project = a100.paths(client)
        sftp = client.open_sftp()
        remote = "/tmp/p1_14b_ebola_expansion/direct_dryrun.sh"
        try:
            with sftp.open(remote + ".tmp", "wb") as h:
                h.write(SCRIPT.encode("utf-8"))
            sftp.chmod(remote + ".tmp", 0o700)
            sftp.posix_rename(remote + ".tmp", remote)
        finally:
            sftp.close()
        rc, out, err = a100.run(client, f"bash {shlex.quote(remote)} {shlex.quote(project)}", timeout=600)
        safe = ((out or "") + ("\n" + err if err else "")).replace(project, "[project]").replace(data_root, "[data-root]")
        print(json.dumps({"returncode": rc, "output": safe}, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
