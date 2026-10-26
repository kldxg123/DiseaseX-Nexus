from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import a100_p1_12 as p112  # noqa: E402


def redact(text: str, project: str = "", data_root: str = "") -> str:
    if project:
        text = text.replace(project, "[project]")
    if data_root:
        text = text.replace(data_root, "[data-root]")
    text = re.sub(r"postgresql(?:\+\w+)?://[^\s'\"<>]+", "[DATABASE_URL_REDACTED]", text)
    text = re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[REDACTED]", text)
    return text


def run_remote(script: str, timeout: int = 1800) -> dict[str, object]:
    a100 = p112.p111.p110r3.previous.p19.a100
    client, gate = a100.connect()
    try:
        data_root, project = a100.paths(client)
        rc0, _out0, err0 = a100.run(client, "mkdir -p /tmp/p1_14b_ebola_expansion && chmod 700 /tmp/p1_14b_ebola_expansion", timeout=60)
        if rc0:
            return {"returncode": rc0, "stdout": "", "stderr": redact(err0 or "", project, data_root), "ok": False}
        sftp = client.open_sftp()
        remote_script = "/tmp/p1_14b_ebola_expansion/runner.sh"
        tmp_script = f"{remote_script}.tmp"
        try:
            with sftp.open(tmp_script, "wb") as handle:
                handle.write(script.encode("utf-8"))
            sftp.chmod(tmp_script, 0o700)
            sftp.posix_rename(tmp_script, remote_script)
        finally:
            sftp.close()
        cmd = f"bash {shlex.quote(remote_script)} {shlex.quote(project)} {shlex.quote(data_root)} {shlex.quote(str(bool(gate.get('stable_second_connection'))).lower())}"
        rc, out, err = a100.run(client, cmd, timeout=timeout)
        return {
            "returncode": rc,
            "stdout": redact(out or "", project, data_root),
            "stderr": redact(err or "", project, data_root),
            "ok": rc == 0,
        }
    finally:
        client.close()


GATE_AND_DRYRUN = r'''
set -euo pipefail
PROJECT="$1"
DATA_ROOT="$2"
IDENTITY_OK="$3"
cd "$PROJECT"
mkdir -p /tmp/p1_14b_ebola_expansion/{baseline,sources,workbooks,reports,screenshots,backups}
PYTHON_BIN="../conda/envs/epidemic-core/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then PYTHON_BIN="python3"; fi
"$PYTHON_BIN" - <<'PY'
from __future__ import annotations
import hashlib, json, os, re, subprocess, sys, time, urllib.parse, urllib.request, zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import xml.etree.ElementTree as ET

project=Path.cwd()
root=Path("/tmp/p1_14b_ebola_expansion")
baseline=root/"baseline"
sources=root/"sources"
workbooks=root/"workbooks"
reports=root/"reports"
for p in (baseline,sources,workbooks,reports): p.mkdir(parents=True,exist_ok=True)

LANDINGS={
 "guinea":"https://stacks.cdc.gov/view/cdc/37575",
 "liberia":"https://stacks.cdc.gov/view/cdc/37469",
 "sierra_leone":"https://stacks.cdc.gov/view/cdc/37757",
}
PROTECTED=[
 "src/epidemic_core/p1_12/dashboard_app.py",
 "src/epidemic_core/discovery/review_app.py",
]

def sha_bytes(data: bytes)->str: return hashlib.sha256(data).hexdigest()
def sha_file(path: Path)->str: return sha_bytes(path.read_bytes())
def run(args, timeout=180):
    p=subprocess.run(args,cwd=project,text=True,capture_output=True,timeout=timeout)
    return {"rc":p.returncode,"stdout":p.stdout.strip(),"stderr":p.stderr.strip()}

def fetch(url: str, max_bytes=60_000_000):
    chain=[]
    class Handler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            chain.append({"from":req.full_url,"to":newurl,"status":code})
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    opener=urllib.request.build_opener(Handler)
    req=urllib.request.Request(url,headers={
        "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*;q=0.8",
        "Accept-Language":"en-US,en;q=0.9",
        "Cache-Control":"no-cache",
    })
    t0=time.monotonic()
    with opener.open(req,timeout=45) as r:
        data=r.read(max_bytes+1)
        if len(data)>max_bytes: raise RuntimeError("RESPONSE_TOO_LARGE")
        return {"url":url,"final_url":r.geturl(),"status":r.status,"headers":dict(r.headers),"payload":data,"elapsed_ms":round((time.monotonic()-t0)*1000),"redirect_chain":chain}

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.meta=[]; self.title=[]; self._title=False
    def handle_starttag(self, tag, attrs):
        d={k.lower():v for k,v in attrs}
        if tag.lower()=="a" and d.get("href"):
            self.links.append({"href":d.get("href"),"text":""})
        if tag.lower()=="meta":
            self.meta.append(d)
        if tag.lower()=="title": self._title=True
    def handle_endtag(self, tag):
        if tag.lower()=="title": self._title=False
    def handle_data(self, data):
        if self._title: self.title.append(data.strip())
        if self.links and data.strip():
            self.links[-1]["text"]=(self.links[-1].get("text","")+" "+data.strip()).strip()

def discover_xlsx(landing_url: str, html: str)->list[str]:
    parser=LinkParser(); parser.feed(html)
    out=[]
    for item in parser.links:
        href=item["href"] or ""
        text=(item.get("text") or "").lower()
        abs_url=urllib.parse.urljoin(landing_url,href)
        low=abs_url.lower()
        if ".xlsx" in low or ("download" in low and ("xlsx" in text or "supporting" in text or "file" in text)):
            out.append(abs_url)
    # CDC Stacks pages often expose /view/cdc/<id>/cdc_<id>_DS1.xlsx in supporting links; only use href discovered above.
    return sorted(dict.fromkeys(out))

def safe_xlsx(path: Path, content_type: str):
    data=path.read_bytes()
    result={"magic_zip":data[:4]==b"PK\x03\x04","content_type":content_type,"bytes":len(data),"sha256":sha_bytes(data),"zip_ok":False,"has_content_types":False,"entries":0,"uncompressed_bytes":0,"warnings":[]}
    if data.lstrip().startswith(b"<html") or b"<html" in data[:300].lower():
        result["warnings"].append("HTML_RESPONSE")
        return result
    try:
        with zipfile.ZipFile(path) as zf:
            infos=zf.infolist()
            result["zip_ok"]=True
            result["entries"]=len(infos)
            result["has_content_types"]=any(i.filename=="[Content_Types].xml" for i in infos)
            result["uncompressed_bytes"]=sum(i.file_size for i in infos)
            if result["entries"]>10000: result["warnings"].append("TOO_MANY_ZIP_ENTRIES")
            if result["uncompressed_bytes"]>250_000_000: result["warnings"].append("UNCOMPRESSED_SIZE_HIGH")
            external=[i.filename for i in infos if "externalLink" in i.filename or i.filename.endswith(".bin")]
            if external: result["warnings"].append("EXTERNAL_LINK_OR_BINARY_PARTS_PRESENT")
    except Exception as exc:
        result["warnings"].append("ZIP_ERROR:"+type(exc).__name__)
        return result
    return result

NS_MAIN="{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL="{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG="{http://schemas.openxmlformats.org/package/2006/relationships}"

def col_to_int(ref: str)->int:
    n=0
    for ch in re.sub(r"[^A-Z]","",ref.upper()):
        n=n*26+ord(ch)-64
    return n

def cell_row(ref: str)->int:
    m=re.search(r"(\d+)",ref)
    return int(m.group(1)) if m else 0

def sheet_dryrun(path: Path):
    with zipfile.ZipFile(path) as zf:
        names=set(zf.namelist())
        shared=[]
        if "xl/sharedStrings.xml" in names:
            root_ss=ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root_ss.findall(f".//{NS_MAIN}si"):
                texts=[t.text or "" for t in si.findall(f".//{NS_MAIN}t")]
                shared.append("".join(texts))
        rels={}
        if "xl/_rels/workbook.xml.rels" in names:
            relroot=ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            for rel in relroot:
                rid=rel.attrib.get("Id") or rel.attrib.get("id")
                target=rel.attrib.get("Target","")
                if rid:
                    rels[rid]="xl/"+target.lstrip("/")
        wbroot=ET.fromstring(zf.read("xl/workbook.xml"))
        epoch="1904" if wbroot.find(f".//{NS_MAIN}workbookPr") is not None and wbroot.find(f".//{NS_MAIN}workbookPr").attrib.get("date1904") in ("1","true","TRUE") else "1900"
        out={"epoch":epoch,"sheet_count":0,"sheets":[]}
        sheets=wbroot.findall(f".//{NS_MAIN}sheet")
        out["sheet_count"]=len(sheets)
        for s in sheets:
            title=s.attrib.get("name","")
            state=s.attrib.get("state","visible")
            rid=s.attrib.get(f"{NS_REL}id")
            target=rels.get(rid,"")
            sheet={"name":title,"state":state,"target":target,"max_row":0,"max_column":0,"dimension":None,"merged_ranges":[],"formula_cells":[],"headers":[],"sample_rows":[],"date_min":None,"date_max":None,"warnings":[]}
            if state!="visible": sheet["warnings"].append("HIDDEN_SHEET")
            if not target or target not in names:
                sheet["warnings"].append("MISSING_WORKSHEET_XML")
                out["sheets"].append(sheet); continue
            root_ws=ET.fromstring(zf.read(target))
            dim=root_ws.find(f".//{NS_MAIN}dimension")
            sheet["dimension"]=dim.attrib.get("ref") if dim is not None else None
            merges=root_ws.findall(f".//{NS_MAIN}mergeCell")
            sheet["merged_ranges"]=[m.attrib.get("ref") for m in merges[:20]]
            rows=[]
            for row in root_ws.findall(f".//{NS_MAIN}sheetData/{NS_MAIN}row"):
                rnum=int(row.attrib.get("r","0") or "0")
                vals={}
                for c in row.findall(f"{NS_MAIN}c"):
                    ref=c.attrib.get("r","")
                    t=c.attrib.get("t")
                    v=c.find(f"{NS_MAIN}v")
                    f=c.find(f"{NS_MAIN}f")
                    val=v.text if v is not None else None
                    if t=="s" and val is not None:
                        try: val=shared[int(val)]
                        except Exception: pass
                    elif t=="inlineStr":
                        texts=[x.text or "" for x in c.findall(f".//{NS_MAIN}t")]
                        val="".join(texts)
                    vals[ref]={"value":val,"formula":f.text if f is not None else None,"type":t}
                    sheet["max_column"]=max(sheet["max_column"],col_to_int(ref))
                    if f is not None and len(sheet["formula_cells"])<20:
                        sheet["formula_cells"].append({"cell":ref,"formula":(f.text or "")[:160],"cached":str(val)[:120] if val is not None else None})
                if vals:
                    sheet["max_row"]=max(sheet["max_row"],rnum)
                    if len(rows)<40: rows.append((rnum,vals))
            for rnum, vals in rows[:20]:
                ordered=[vals[k]["value"] for k in sorted(vals, key=lambda x:(cell_row(x), col_to_int(x)))[:30]]
                nonempty=[str(v) for v in ordered if v not in (None,"")]
                if len(nonempty)>=2 and not sheet["headers"]:
                    sheet["headers"]=nonempty[:30]
            for rnum, vals in rows[:8]:
                row=[]
                for ref in sorted(vals, key=lambda x:(cell_row(x), col_to_int(x)))[:12]:
                    row.append({"cell":ref,"raw":str(vals[ref].get("formula") or vals[ref].get("value"))[:120] if (vals[ref].get("formula") or vals[ref].get("value")) is not None else None,"cached":str(vals[ref].get("value"))[:120] if vals[ref].get("value") is not None else None})
                sheet["sample_rows"].append(row)
            if sheet["formula_cells"]: sheet["warnings"].append("FORMULA_CELLS_PRESENT_NOT_EXECUTED")
            if sheet["merged_ranges"]: sheet["warnings"].append("MERGED_CELLS_PRESENT")
            out["sheets"].append(sheet)
        return out

def baseline_probe():
    res={}
    res["pwd"]=str(project)
    res["branch"]=run(["git","branch","--show-current"])
    res["head"]=run(["git","rev-parse","HEAD"])
    res["status_porcelain_v2"]=run(["git","status","--porcelain=v2","-uall"])
    res["protected_sha256"]={p:sha_file(project/p) for p in PROTECTED}
    res["alembic_heads"]=run(["bash","-lc","../conda/envs/epidemic-core/bin/python -m alembic heads 2>/dev/null || alembic heads"],300)
    res["readyz"]={}
    for name,url in {"dashboard":"http://127.0.0.1:8020/readyz","review":"http://127.0.0.1:8021/readyz"}.items():
        try:
            f=fetch(url,1_000_000)
            res["readyz"][name]={"status":f["status"],"body":json.loads(f["payload"].decode())}
        except Exception as exc:
            res["readyz"][name]={"error":repr(exc)}
    res["processes"]=run(["bash","-lc","ps -eo pid,comm,args,cwd 2>/dev/null | grep -E 'uvicorn|postgres|vllm|review|dashboard' | grep -v grep | head -80"],60)
    return res

def validate_sources():
    results=[]
    for key,url in LANDINGS.items():
        item={"key":key,"landing_url":url,"retrieved_at":datetime.now(timezone.utc).isoformat()}
        try:
            landing=fetch(url,5_000_000)
            html=landing["payload"].decode("utf-8",errors="replace")
            (sources/f"{key}_landing.html").write_bytes(landing["payload"])
            parser=LinkParser(); parser.feed(html)
            xlsx_links=discover_xlsx(landing["final_url"], html)
            item.update({"landing_final_url":landing["final_url"],"landing_status":landing["status"],"landing_content_type":landing["headers"].get("Content-Type"),"landing_bytes":len(landing["payload"]),"landing_sha256":sha_bytes(landing["payload"]),"landing_title":" ".join(parser.title)[:500],"landing_meta":parser.meta[:30],"xlsx_links":xlsx_links,"landing_redirect_chain":landing["redirect_chain"]})
            if not xlsx_links:
                item["status"]="DISCOVERY_ONLY_NO_XLSX_LINK"
                results.append(item); continue
            # pick first actual xlsx link by content
            for idx,xurl in enumerate(xlsx_links):
                try:
                    x=fetch(xurl,80_000_000)
                    ctype=x["headers"].get("Content-Type","")
                    ext=Path(urllib.parse.urlsplit(x["final_url"]).path).suffix.lower()
                    suffix=".xlsx" if ext!=".xlsx" else ext
                    path=workbooks/f"{key}_{idx}{suffix}"
                    path.write_bytes(x["payload"])
                    security=safe_xlsx(path,ctype)
                    if security["magic_zip"] and security["zip_ok"] and security["has_content_types"]:
                        dry=sheet_dryrun(path)
                    else:
                        dry=None
                    item["selected_xlsx"]={"url":xurl,"final_url":x["final_url"],"redirect_chain":x["redirect_chain"],"status":x["status"],"content_type":ctype,"bytes":len(x["payload"]),"sha256":sha_bytes(x["payload"]),"path":str(path),"security":security,"dryrun":dry}
                    item["status"]="XLSX_VERIFIED" if dry else "XLSX_REJECTED_SECURITY"
                    break
                except Exception as exc:
                    item.setdefault("xlsx_attempt_errors",[]).append({"url":xurl,"error":repr(exc)})
            if "selected_xlsx" not in item:
                item["status"]="XLSX_DOWNLOAD_FAILED"
        except Exception as exc:
            item["status"]="LANDING_FAILED"; item["error"]=repr(exc)
        results.append(item)
    return results

baseline_data=baseline_probe()
(baseline/"baseline.json").write_text(json.dumps(baseline_data,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
sources_data=validate_sources()
(reports/"source_dryrun.json").write_text(json.dumps(sources_data,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
print(json.dumps({"stage":"P1_14B_GATE_AND_DRYRUN","baseline":baseline_data,"sources":sources_data},ensure_ascii=False,default=str))
PY
'''


if __name__ == "__main__":
    result = run_remote(GATE_AND_DRYRUN, timeout=1800)
    print(json.dumps(result, ensure_ascii=False, indent=2))
