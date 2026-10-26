from __future__ import annotations

import gettext
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pycountry
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import Country,CountryAlias,Disease,DiseaseAlias,MetricDefinition,ReferenceDataset
from epidemic_core.normalization.text import normalize_alias

NAMESPACE=uuid.UUID("98d7ec7d-9e2f-4a91-8a42-b8f78d5cb7f6")
MANUAL={"CN":[("中国","zh","manual"),("中华人民共和国","zh","manual")],"US":[("美国","zh","manual"),("美國","zh-Hant","manual")],"BR":[("巴西","zh","manual")]}


def _upsert(session:Session,model:Any,values:dict[str,Any],keys:list[str]) -> None:
    stmt=insert(model).values(**values);session.execute(stmt.on_conflict_do_update(index_elements=[getattr(model,k) for k in keys],set_={k:v for k,v in values.items() if k not in keys}))


def seed(session:Session,root:Path) -> dict[str,Any]:
    countries=[]
    translators={lang:gettext.translation("iso3166-1",pycountry.LOCALES_DIR,languages=[lang],fallback=True) for lang in ("zh_CN","zh_TW","zh_HK")}
    for item in sorted(pycountry.countries,key=lambda x:x.alpha_2):
        row={"alpha2":item.alpha_2,"alpha3":item.alpha_3,"numeric_code":item.numeric,"name":item.name,"official_name":getattr(item,"official_name",None)};countries.append(row)
    content=json.dumps(countries,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode();digest=hashlib.sha256(content).hexdigest();key="iso3166-pycountry-26.2.16"
    _upsert(session,ReferenceDataset,{"dataset_key":key,"version":"26.2.16","source_name":"pycountry / Debian iso-codes snapshot","source_url":"https://pypi.org/project/pycountry/26.2.16/","license_expression":"LGPL-2.1-or-later","content_sha256":digest,"metadata_json":{"semantics":"ISO 3166-1","record_count":len(countries)}},["dataset_key"])
    aliases=0
    for row in countries:
        _upsert(session,Country,{**row,"active":True,"reference_dataset_key":key},["alpha2"])
        values=[(row["alpha2"],"und","alpha2"),(row["alpha3"],"und","alpha3"),(row["numeric_code"],"und","numeric"),(row["name"],"en","name")]
        if row["official_name"]:values.append((row["official_name"],"en","official_name"))
        for lang,tr in translators.items():values.append((tr.gettext(row["name"]),lang,"translation"))
        values.extend(MANUAL.get(row["alpha2"],[]))
        for alias,locale,kind in values:
            stmt=insert(CountryAlias).values(country_alpha2=row["alpha2"],alias=alias,normalized_alias=normalize_alias(alias),locale=locale,alias_type=kind,reference_dataset_key=key).on_conflict_do_nothing(constraint="uq_country_alias_identity");session.execute(stmt);aliases+=1
    disease_path=root/"reference/diseases.v1.json";metric_path=root/"reference/metrics.v1.json"
    for path,dataset_key,source,license_exp in ((disease_path,"diseases-v1","project disease catalog","CC0-1.0"),(metric_path,"metrics-v1","project metric catalog","CC0-1.0")):
        raw=path.read_bytes();_upsert(session,ReferenceDataset,{"dataset_key":dataset_key,"version":"1","source_name":source,"source_url":"project://"+path.name,"license_expression":license_exp,"content_sha256":hashlib.sha256(raw).hexdigest(),"metadata_json":{}},["dataset_key"])
    diseases=json.loads(disease_path.read_text())
    for d in diseases:
        did=uuid.uuid5(NAMESPACE,d["slug"]);_upsert(session,Disease,{"id":did,"slug":d["slug"],"canonical_name":d["canonical_name"],"description":d.get("description"),"ontology_system":None,"ontology_code":None,"ontology_version":None,"catalog_version":d["catalog_version"],"active":d["active"]},["slug"])
        for a in d["aliases"]:
            session.execute(insert(DiseaseAlias).values(disease_id=did,alias=a["value"],normalized_alias=normalize_alias(a["value"]),language_tag=a["language_tag"],alias_type=a["alias_type"],catalog_version=d["catalog_version"]).on_conflict_do_nothing(constraint="uq_disease_alias_identity"))
    metrics=json.loads(metric_path.read_text())
    for m in metrics:_upsert(session,MetricDefinition,m,["metric_code"])
    return {"countries":len(countries),"country_alias_attempts":aliases,"diseases":len(diseases),"metrics":len(metrics),"country_content_sha256":digest,"disease_file_sha256":hashlib.sha256(disease_path.read_bytes()).hexdigest(),"metric_file_sha256":hashlib.sha256(metric_path.read_bytes()).hexdigest()}
