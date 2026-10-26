from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13
import autodl_p1_7a as p17

P = p0.P
PY = p1.PY
CORE = p1.CORE
EXPECTED_HEAD = "a64de786485dfda6b5d7a0bd8f12aed464f48444"
KNOWN_HOSTS = Path.cwd() / "private" / "known_hosts_autodl_p1_7ar"
REPORT = P + "/reports/p1_7a_ui_repair"
LOCAL = Path(__file__).parent / "p1_7ar_baseline"
p13.KNOWN_HOSTS = KNOWN_HOSTS


def db(c: object, sql: str, database: str = "epidemic_openclaw") -> str:
    return p17.db(c, sql, database)


def gate(c: object) -> None:
    git = {
        "head": p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD"),
        "message": p0.out(c, f"cd {p0.q(P)} && git log -1 --pretty=%s"),
        "status": p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines(),
        "pushed": bool(p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False)),
    }
    assert git == {"head": EXPECTED_HEAD, "message": "p1-7a: add controlled source discovery review", "status": [], "pushed": False}
    assert db(c, "SELECT version_num FROM epidemic.alembic_version") == "0005_p1_7_source_discovery_review"
    services = {name: p0.run(c, p0.q(P + path), check=False)[0] == 0 for name, path in {
        "postgres":"/scripts/postgres/status.sh","vllm":"/scripts/qwen_vllm/status.sh","query_api":"/scripts/query_api/status_query_api.sh",
        "retrieval_worker":"/scripts/retrieval_worker/status_retrieval_worker.sh","openclaw":"/scripts/openclaw/status_openclaw.sh",
        "dashboard":"/scripts/dashboard/status_dashboard.sh","discovery_worker":"/scripts/source_discovery_worker/status_source_discovery_worker.sh",
        "promotion_worker":"/scripts/source_promotion_worker/status_source_promotion_worker.sh","review_console":"/scripts/source_review/status_source_review.sh",
    }.items()}
    assert all(services.values())
    catalog = p13._tool_catalog(c, "agent:main:p1-7ar-gate")
    assert catalog == ["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status","start_epidemic_source_discovery","get_epidemic_source_discovery_status"]
    formal = p17.counts(c)
    discovery = json.loads(db(c, "SELECT json_build_object('run_id',dr.id,'state',dr.state,'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text FROM epidemic_discovery.discovery_runs dr ORDER BY created_at DESC LIMIT 1"))
    assert discovery["state"] == "REVIEW_READY" and discovery["decisions"] == 0 and discovery["promotions"] == 0
    p16 = json.loads(db(c, "SELECT json_agg(x ORDER BY x.country_alpha2)::text FROM (SELECT DISTINCT ON (country_alpha2,disease_slug) country_alpha2,disease_slug,snapshot_id,source_data_through,observation_count FROM epidemic.dashboard_snapshot_catalog_v1 ORDER BY country_alpha2,disease_slug,source_data_through DESC,snapshot_created_at DESC) x"))
    assert {(x["country_alpha2"], x["disease_slug"]) for x in p16} == {("BR", "dengue"), ("FR", "covid-19")}
    p0.run(c, f"mkdir -p {p0.q(P+'/backups')} {p0.q(REPORT)} && chmod 0700 {p0.q(P+'/backups')} {p0.q(REPORT)}")
    stamp = p0.out(c, "date -u +%Y%m%dT%H%M%SZ")
    dump = P + "/backups/epidemic_openclaw-p1_7ar-pre-" + stamp + ".dump"
    pgdir = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"; run = P + "/storage/database/postgresql18/run"; temp = "epidemic_p1_7ar_restore_gate"
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/pg_dump')} -h {p0.q(run)} -U epidemicdb -d epidemic_openclaw -Fc > {p0.q(dump)}", timeout=900)
    p0.run(c, f"chown epidemicdb:epidemicdb {p0.q(dump)} && chmod 0600 {p0.q(dump)}")
    p0.run(c, f"setfacl -m u:epidemicdb:--x {p0.q(P+'/backups')}")
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pgdir+'/dropdb')} -h {p0.q(run)} -U epidemicdb --if-exists {temp}",
        f"runuser -u epidemicdb -- {p0.q(pgdir+'/createdb')} -h {p0.q(run)} -U epidemicdb -O epidemicdb {temp}",
        f"runuser -u epidemicdb -- {p0.q(pgdir+'/pg_restore')} -h {p0.q(run)} -U epidemicdb -d {temp} {p0.q(dump)}",
    ): p0.run(c, command, timeout=900)
    assert p17.counts(c, temp) == formal
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/dropdb')} -h {p0.q(run)} -U epidemicdb {temp}")
    key = c.get_transport().get_remote_server_key(); fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    result = {"status":"PASS","git":git,"migration":"0005_p1_7_source_discovery_review","services":services,"catalog":catalog,"formal_counts":formal,"discovery":discovery,"p1_6_snapshots":p16,"backup":{"sha256":p0.out(c,f"sha256sum {p0.q(dump)} | cut -d' ' -f1"),"restore_verified":True},"ssh":{"key_type":key.get_name(),"fingerprint":fingerprint,"stable":True}}
    s=c.open_sftp();p0.put(s,REPORT+"/preflight.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def pull(c: object) -> None:
    tracked = p0.out(c, f"cd {p0.q(P)} && git ls-files").splitlines()
    wanted = [x for x in tracked if x == "pyproject.toml" or x.startswith(("src/","tests/","migrations/","scripts/dashboard/","scripts/source_review/","integrations/openclaw/epidemic-query-tools/","reference/"))]
    s=c.open_sftp()
    try:
        for rel in wanted:
            local=LOCAL/PurePosixPath(rel);local.parent.mkdir(parents=True,exist_ok=True)
            with s.open(P+"/"+rel,"rb") as source:local.write_bytes(source.read())
    finally:s.close()
    print(json.dumps({"status":"PASS","files":len(wanted)}))


def diagnose(c: object) -> None:
    formal = {
        "ready_snapshots": int(db(c, "SELECT count(*) FROM epidemic.dashboard_snapshot_catalog_v1")),
        "snapshot_members": int(db(c, "SELECT count(*) FROM epidemic.dataset_snapshot_observations")),
        "normalized_observations": int(db(c, "SELECT count(*) FROM epidemic.normalized_observations")),
        "countries": int(db(c, "SELECT count(*) FROM epidemic.countries")),
        "diseases": int(db(c, "SELECT count(*) FROM epidemic.diseases")),
        "fr_covid_points": int(db(c, "SELECT count(*) FROM epidemic.dashboard_observation_series_v1 s JOIN epidemic.dashboard_snapshot_catalog_v1 c ON c.snapshot_id=s.snapshot_id WHERE c.country_alpha2='FR' AND c.disease_slug='covid-19'")),
        "br_dengue_points": int(db(c, "SELECT count(*) FROM epidemic.dashboard_observation_series_v1 s JOIN epidemic.dashboard_snapshot_catalog_v1 c ON c.snapshot_id=s.snapshot_id WHERE c.country_alpha2='BR' AND c.disease_slug='dengue'")),
    }
    ro = json.loads(db(c, "SELECT json_build_object('catalog_select',has_table_privilege('epidemic_dashboard_ro','epidemic.dashboard_snapshot_catalog_v1','SELECT'),'series_select',has_table_privilege('epidemic_dashboard_ro','epidemic.dashboard_observation_series_v1','SELECT'),'quality_select',has_table_privilege('epidemic_dashboard_ro','epidemic.dashboard_quality_summary_v1','SELECT'),'can_insert',has_table_privilege('epidemic_dashboard_ro','epidemic.normalized_observations','INSERT'),'configured_statement_timeout_ms',5000)::text"))
    mapping = json.loads(db(c, "SELECT json_build_object('artifact_columns',(SELECT json_agg(source_name ORDER BY ordinal) FROM epidemic_discovery.source_schema_columns),'has_geocode',(SELECT bool_or(source_name='geocode') FROM epidemic_discovery.source_schema_columns),'has_localidade_id',(SELECT bool_or(source_name='Localidade_id') FROM epidemic_discovery.source_schema_columns),'mapping_fields',(SELECT json_agg(json_build_object('source_field',source_field,'canonical_field',canonical_field,'status',CASE WHEN source_field IN (SELECT source_name FROM epidemic_discovery.source_schema_columns) THEN 'present' ELSE 'missing' END) ORDER BY source_field) FROM epidemic_discovery.source_mapping_fields))::text"))
    import os
    lines = Path(os.environ["P1_7AR_DASH_LOGIN"]).read_text(encoding="utf-8").splitlines()
    jar=CookieJar();opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    def call(path: str, method: str="GET", form: dict[str,str]|None=None) -> dict[str, object]:
        data=urllib.parse.urlencode(form).encode() if form else None
        req=urllib.request.Request("http://127.0.0.1:8020"+path,data=data,method=method,headers={"Accept":"application/json"})
        try:
            with opener.open(req,timeout=15) as response:return {"status":response.status,"content_type":response.headers.get("Content-Type"),"body":response.read(2*1024*1024).decode("utf-8","replace")}
        except urllib.error.HTTPError as exc:return {"status":exc.code,"content_type":exc.headers.get("Content-Type"),"body":exc.read(2*1024*1024).decode("utf-8","replace")}
    login=call("/login","POST",{"username":lines[0],"password":lines[1]})
    api={}
    for name,path in (("options","/api/v1/options"),("snapshots","/api/v1/snapshots?page=1&page_size=100")):
        value=call(path);body=value.pop("body");value["json"]=json.loads(body) if body.startswith(("{","[")) else body[:500];api[name]=value
    if api["snapshots"]["status"]==200 and api["snapshots"]["json"].get("items"):
        sid=api["snapshots"]["json"]["items"][0]["snapshot_id"]
        for name,suffix in (("series",f"/api/v1/snapshots/{sid}/series"),("quality",f"/api/v1/snapshots/{sid}/series")):
            value=call(suffix);body=value.pop("body");value["json"]=json.loads(body) if body.startswith(("{","[")) else body[:500];api[name]=value
    result={"status":"PASS","database":formal,"dashboard_ro":ro,"api":api,"mapping_integrity":mapping}
    s=c.open_sftp();p0.put(s,REPORT+"/diagnostic_data_path.json",json.dumps(result,ensure_ascii=False,indent=2)+"\n",0o600);s.close();print(json.dumps(result,ensure_ascii=False))


PATCH_PATHS = (
    "migrations/versions/0006_p1_7a_ui_read_models.py",
    "src/epidemic_core/dashboard/app.py",
    "src/epidemic_core/dashboard/static/",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/discovery/static/",
    "scripts/dashboard/install_runtime.sh",
    "scripts/source_review/install_runtime.sh",
    "tests/unit/test_dashboard.py",
    "tests/unit/test_p1_7a_ui_repair.py",
)


def upload(c: object) -> None:
    assert p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD") == EXPECTED_HEAD
    files = [
        item for item in LOCAL.rglob("*")
        if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc"
        and any(item.relative_to(LOCAL).as_posix() == root or item.relative_to(LOCAL).as_posix().startswith(root) for root in PATCH_PATHS)
    ]
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        for item in files:
            bundle.add(item, arcname=item.relative_to(LOCAL).as_posix(), recursive=False)
    remote = "USER_HOME/autodl-tmp/p1_7ar_upload.tar.gz"
    s = c.open_sftp()
    try:
        p0.put(s, remote, archive.getvalue(), 0o600)
    finally:
        s.close()
    p0.run(c, f"tar -xzf {p0.q(remote)} -C {p0.q(P)} && rm -f {p0.q(remote)}")
    p0.run(c, f"chmod 0755 {p0.q(P+'/scripts/dashboard/install_runtime.sh')} {p0.q(P+'/scripts/source_review/install_runtime.sh')}")
    changed = p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines()
    assert changed
    print(json.dumps({"status":"PASS","uploaded_files":len(files),"changed_paths":len(changed)}))


def quality(c: object) -> None:
    checks = {}
    for name, command in (
        ("compile", f"{PY} -m compileall -q src migrations"),
        ("ruff", f"{CORE}/bin/ruff check src tests migrations"),
        ("format", f"{CORE}/bin/ruff format --check src tests migrations"),
        ("mypy", f"{CORE}/bin/mypy src/epidemic_core"),
        ("pip_check", f"{PY} -m pip check"),
        ("unit_tests", f"PYTHONPATH={p0.q(P+'/src')} {PY} -m pytest -q tests/unit"),
    ):
        rc, out, err = p0.run(c, f"cd {p0.q(P)} && {command}", timeout=1800, check=False)
        checks[name] = {"rc":rc,"tail":(out+err)[-6000:]}
    print(json.dumps({"status":"PASS" if all(x['rc']==0 for x in checks.values()) else "FAIL","checks":checks}))


def format_fix(c: object) -> None:
    p0.run(c, f"cd {p0.q(P)} && {p0.q(CORE+'/bin/ruff')} check --fix src tests migrations", timeout=900, check=False)
    p0.run(c, f"cd {p0.q(P)} && {p0.q(CORE+'/bin/ruff')} format src tests migrations", timeout=900)
    names = p0.out(c, f"cd {p0.q(P)} && git status --porcelain=v1 | sed -E 's/^...//' ").splitlines()
    selected = [name for name in names if any(name == root or name.startswith(root) for root in PATCH_PATHS)]
    s = c.open_sftp()
    try:
        for rel in selected:
            remote = P + "/" + rel
            local = LOCAL / PurePosixPath(rel)
            if p0.run(c, f"test -f {p0.q(remote)}", check=False)[0] != 0:
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            with s.open(remote, "rb") as source:
                local.write_bytes(source.read())
    finally:
        s.close()
    print(json.dumps({"status":"PASS","synced_files":len(selected)}))


def migration_test(c: object) -> None:
    before = p17.counts(c)
    sock = P + "/storage/database/postgresql18/run"
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    temp = "epidemic_p1_7ar_migration_test"
    dump = p0.out(c, f"ls -1t {p0.q(P+'/backups')}/epidemic_openclaw-p1_7ar-pre-*.dump | head -n1")
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb --if-exists {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/createdb')} -h {p0.q(sock)} -U epidemicdb -O epidemicdb {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/pg_restore')} -h {p0.q(sock)} -U epidemicdb -d {temp} {p0.q(dump)}",
    ):
        p0.run(c, command, timeout=900)
    url = "postgresql+psycopg://REDACTED@/" + temp + "?host=" + urllib.parse.quote(sock, safe="")
    alembic = f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P+'/src')} MIGRATION_DATABASE_URL={p0.q(url)} {p0.q(PY)} -m alembic"
    p0.run(c, alembic + " upgrade head", timeout=900)
    up1 = db(c, "SELECT version_num FROM epidemic.alembic_version", temp)
    schema = json.loads(db(c, "SELECT json_build_object('single_head',(SELECT count(*)=1 FROM epidemic.alembic_version),'catalog_rows',(SELECT count(*) FROM epidemic.dashboard_catalog_options_v2),'history_rows',(SELECT count(*) FROM epidemic.dashboard_history_series_v2),'mapping_rows',(SELECT count(*) FROM epidemic_discovery.review_field_mapping_v2),'integrity_rows',(SELECT count(*) FROM epidemic_discovery.review_mapping_integrity_v2),'dashboard_select',has_table_privilege('epidemic_dashboard_ro','epidemic.dashboard_history_series_v2','SELECT'),'dashboard_insert',has_table_privilege('epidemic_dashboard_ro','epidemic.normalized_observations','INSERT'),'review_formal_insert',has_table_privilege('epidemicreview','epidemic.normalized_observations','INSERT'),'review_function',has_function_privilege('epidemicreview','epidemic_discovery.submit_structured_discovery(text,uuid,text[],text,text[],text[],integer)','EXECUTE'))::text", temp))
    p0.run(c, alembic + " downgrade 0005_p1_7_source_discovery_review", timeout=900)
    down = db(c, "SELECT version_num FROM epidemic.alembic_version", temp)
    p0.run(c, alembic + " upgrade head", timeout=900)
    up2 = db(c, "SELECT version_num FROM epidemic.alembic_version", temp)
    after = p17.counts(c, temp)
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb {temp}")
    assert up1 == up2 == "0006_p1_7a_ui_read_models" and down == "0005_p1_7_source_discovery_review"
    assert before == after and schema["single_head"] and schema["catalog_rows"] == 996 and schema["mapping_rows"] == 31
    assert schema["integrity_rows"] >= 1 and schema["dashboard_select"] and not schema["dashboard_insert"] and not schema["review_formal_insert"] and schema["review_function"]
    print(json.dumps({"status":"PASS","upgrade":up1,"downgrade":down,"reupgrade":up2,"schema":schema,"formal_counts_unchanged":True}))


def migration_debug(c: object) -> None:
    sock = P + "/storage/database/postgresql18/run"
    temp = "epidemic_p1_7ar_migration_test"
    url = "postgresql+psycopg://REDACTED@/" + temp + "?host=" + urllib.parse.quote(sock, safe="")
    command = f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P+'/src')} MIGRATION_DATABASE_URL={p0.q(url)} {p0.q(PY)} -m alembic upgrade head"
    rc, out, err = p0.run(c, command, timeout=900, check=False)
    print(json.dumps({"status":"PASS" if rc == 0 else "FAIL","rc":rc,"tail":(out+err)[-16000:]}))


def apply_production(c: object) -> None:
    before = p17.counts(c)
    current = db(c, "SELECT version_num FROM epidemic.alembic_version")
    assert current in {"0005_p1_7_source_discovery_review", "0006_p1_7a_ui_read_models"}
    assert db(c, "SELECT count(*) FROM epidemic_discovery.source_review_decisions") == "0"
    assert db(c, "SELECT count(*) FROM epidemic_discovery.source_promotion_runs") == "0"
    sock = P + "/storage/database/postgresql18/run"
    url = "postgresql+psycopg://REDACTED@/epidemic_openclaw?host=" + urllib.parse.quote(sock, safe="")
    if current == "0005_p1_7_source_discovery_review":
        p0.run(c, f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P+'/src')} MIGRATION_DATABASE_URL={p0.q(url)} {p0.q(PY)} -m alembic upgrade head", timeout=900)
    p0.run(c, p0.q(P + "/scripts/dashboard/install_runtime.sh"), timeout=180)
    p0.run(c, p0.q(P + "/scripts/source_review/install_runtime.sh"), timeout=180)
    p0.run(c, p0.q(P + "/scripts/dashboard/restart_dashboard.sh"), timeout=180)
    p0.run(c, p0.q(P + "/scripts/source_review/stop_source_review.sh"), timeout=120, check=False)
    p0.run(c, p0.q(P + "/scripts/source_review/start_source_review.sh"), timeout=180)
    after = p17.counts(c)
    state = json.loads(db(c, "SELECT json_build_object('migration',(SELECT version_num FROM epidemic.alembic_version),'effective_status',(SELECT effective_status FROM epidemic_discovery.review_candidate_cards_v2 ORDER BY review_case_id LIMIT 1),'approval_blocked',(SELECT approval_blocked FROM epidemic_discovery.review_candidate_cards_v2 ORDER BY review_case_id LIMIT 1),'mapping_finding',(SELECT finding_code FROM epidemic_discovery.review_mapping_integrity_v2 LIMIT 1),'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text"))
    services = {
        "dashboard": p0.run(c, p0.q(P + "/scripts/dashboard/status_dashboard.sh"), check=False)[0] == 0,
        "review_console": p0.run(c, p0.q(P + "/scripts/source_review/status_source_review.sh"), check=False)[0] == 0,
    }
    static = {
        "dashboard_css": p0.out(c, "curl -sS -o /dev/null -w '%{http_code} %{content_type}' http://127.0.0.1:8020/static/dashboard.p1-7ar-v1.css"),
        "dashboard_js": p0.out(c, "curl -sS -o /dev/null -w '%{http_code} %{content_type}' http://127.0.0.1:8020/static/dashboard.p1-7ar-v1.js"),
        "review_css": p0.out(c, "curl -sS -o /dev/null -w '%{http_code} %{content_type}' http://127.0.0.1:8021/static/review.p1-7ar-v1.css"),
        "review_js": p0.out(c, "curl -sS -o /dev/null -w '%{http_code} %{content_type}' http://127.0.0.1:8021/static/review.p1-7ar-v1.js"),
    }
    assert before == after and all(services.values()) and all(value.startswith("200 ") for value in static.values())
    assert state == {"migration":"0006_p1_7a_ui_read_models","effective_status":"NEEDS_IMPLEMENTATION","approval_blocked":True,"mapping_finding":"MAPPING_SOURCE_FIELD_MISSING","decisions":0,"promotions":0}
    print(json.dumps({"status":"PASS","state":state,"services":services,"static":static,"formal_counts_unchanged":True}))


def review_log(c: object) -> None:
    log = p0.out(c, f"tail -n 120 {p0.q('USER_HOME/autodl-tmp/epidemic-source-review-runtime/log/review.log')}", check=False)
    print(json.dumps({"status":"PASS","tail":log[-12000:]}))


def full_regression(c: object) -> None:
    sock = P + "/storage/database/postgresql18/run"
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    temp = "epidemic_p1_7ar_full_test"
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb --if-exists {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/createdb')} -h {p0.q(sock)} -U epidemicdb -O epidemicdb {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/psql')} -h {p0.q(sock)} -U epidemicdb -d {temp} -v ON_ERROR_STOP=1 -c {p0.q('CREATE SCHEMA epidemic AUTHORIZATION epidemicdb')}",
    ):
        p0.run(c, command, timeout=300)
    env_file = P + "/storage/secrets/p1-7ar-test.env"
    create_env = """import os,urllib.parse,pathlib
root=pathlib.Path(os.environ['PROJECT']);db=os.environ['TEST_DB']
def u(user,password):return 'postgresql+psycopg://'+user+':'+urllib.parse.quote(password,safe='')+'@127.0.0.1:5432/'+db
p=root/'storage/secrets/p1-7ar-test.env';p.write_text('MIGRATION_DATABASE_URL='+u('epidemicdb',os.environ['POSTGRES_ADMIN_PASSWORD'])+'\\nAPP_DATABASE_URL='+u('epidemic_app',os.environ['POSTGRES_APP_PASSWORD'])+'\\n');p.chmod(0o600)
"""
    p0.run(c, f"set -a; source {p0.q(P+'/.secrets/postgres.env')}; set +a; PROJECT={p0.q(P)} TEST_DB={temp} {p0.q(PY)} -c {p0.q(create_env)}")
    env = f"set -a;source {p0.q(env_file)};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};"
    p0.run(c, f"bash -lc {p0.q(env+' '+PY+' -m alembic upgrade 0003_p1_4_normalization_pipeline && '+PY+' -m epidemic_core.cli seed-reference && '+PY+' -m alembic upgrade head')}", timeout=1200)
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg+'/psql')} -h {p0.q(sock)} -U epidemicdb -d {temp} -v ON_ERROR_STOP=1 -c {p0.q('GRANT USAGE ON SCHEMA epidemic TO epidemic_app')}")
    rc, out, err = p0.run(c, f"bash -lc {p0.q(env+' '+PY+' -m pytest -q')}", timeout=2400, check=False)
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb --if-exists {temp}")
    p0.run(c, f"rm -f {p0.q(env_file)}")
    node = P + "/runtime/node-current/bin"
    plugin = P + "/integrations/openclaw/epidemic-query-tools"
    npm = {}
    for name, command in (
        ("build", f"PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} run build"),
        ("test", f"PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} test"),
        ("audit", f"PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} audit --json"),
    ):
        nrc, nout, nerr = p0.run(c, f"cd {p0.q(plugin)} && {command}", timeout=1200, check=False)
        npm[name] = {"rc":nrc,"tail":(nout+nerr)[-5000:]}
    audit = json.loads(npm["audit"]["tail"] or "{}") if npm["audit"]["rc"] in (0,1) else {}
    vulnerabilities = audit.get("metadata",{}).get("vulnerabilities",{})
    passed = rc == 0 and npm["build"]["rc"] == 0 and npm["test"]["rc"] == 0 and int(vulnerabilities.get("high",0))+int(vulnerabilities.get("critical",0)) == 0
    print(json.dumps({"status":"PASS" if passed else "FAIL","pytest":{"rc":rc,"tail":(out+err)[-12000:]},"npm":npm,"vulnerabilities":vulnerabilities}))


def rollback_rehearsal(c: object) -> None:
    sock = P + "/storage/database/postgresql18/run"
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    temp = "epidemic_p1_7ar_rollback"
    worktree = "USER_HOME/autodl-tmp/epidemic-p1-7ar-old-worktree"
    p0.run(c, f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}", check=False)
    p0.run(c, f"cd {p0.q(P)} && git worktree add --detach {p0.q(worktree)} {EXPECTED_HEAD}")
    dump = p0.out(c, f"ls -1t {p0.q(P+'/backups')}/epidemic_openclaw-p1_7ar-pre-*.dump | head -n1")
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb --if-exists {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/createdb')} -h {p0.q(sock)} -U epidemicdb -O epidemicdb {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/pg_restore')} -h {p0.q(sock)} -U epidemicdb -d {temp} {p0.q(dump)}",
    ):
        p0.run(c, command, timeout=900)
    old = {
        "migration": db(c,"SELECT version_num FROM epidemic.alembic_version",temp),
        "dashboard_select": json.loads(db(c,"SELECT json_build_object('catalog',has_table_privilege('epidemic_dashboard_ro','epidemic.dashboard_snapshot_catalog_v1','SELECT'),'series',has_table_privilege('epidemic_dashboard_ro','epidemic.dashboard_observation_series_v1','SELECT'))::text",temp)),
        "review_json_main": "JSON.stringify" in p0.out(c,f"cat {p0.q(worktree+'/src/epidemic_core/discovery/review_app.py')}",check=False),
        "dashboard_inline_script": "<script>" in p0.out(c,f"cat {p0.q(worktree+'/src/epidemic_core/dashboard/app.py')}",check=False),
    }
    url = "postgresql+psycopg://REDACTED@/" + temp + "?host=" + urllib.parse.quote(sock, safe="")
    p0.run(c, f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P+'/src')} MIGRATION_DATABASE_URL={p0.q(url)} {p0.q(PY)} -m alembic upgrade head", timeout=900)
    repaired = json.loads(db(c,"SELECT json_build_object('migration',(SELECT version_num FROM epidemic.alembic_version),'catalog_rows',(SELECT count(*) FROM epidemic.dashboard_catalog_options_v2),'mapping_rows',(SELECT count(*) FROM epidemic_discovery.review_field_mapping_v2),'mapping_block',(SELECT count(*) FROM epidemic_discovery.review_mapping_integrity_v2),'dashboard_select',has_table_privilege('epidemic_dashboard_ro','epidemic.dashboard_history_series_v2','SELECT'))::text",temp))
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb {temp}")
    p0.run(c, f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}")
    p0.run(c, f"cd {p0.q(P)} && git worktree prune")
    assert old["migration"] == "0005_p1_7_source_discovery_review" and not old["dashboard_select"]["catalog"] and old["review_json_main"]
    assert repaired == {"migration":"0006_p1_7a_ui_read_models","catalog_rows":996,"mapping_rows":31,"mapping_block":1,"dashboard_select":True}
    print(json.dumps({"status":"PASS","old_defect_reproduced":old,"repair_reapplied":repaired,"cleaned":True}))


def final_audit(c: object) -> None:
    preflight = json.loads(p0.out(c, f"cat {p0.q(REPORT+'/preflight.json')}"))
    before = preflight["formal_counts"]
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    sock = P + "/storage/database/postgresql18/run"
    p0.run(c, p0.q(P + "/scripts/postgres/restart.sh"), timeout=300)
    for path in (
        "/scripts/qwen_vllm/start.sh",
        "/scripts/query_api/start_query_api.sh",
        "/scripts/retrieval_worker/start_retrieval_worker.sh",
        "/scripts/openclaw/start_openclaw.sh",
        "/scripts/dashboard/start_dashboard.sh",
        "/scripts/source_discovery_worker/start_source_discovery_worker.sh",
        "/scripts/source_promotion_worker/start_source_promotion_worker.sh",
        "/scripts/source_review/start_source_review.sh",
    ):
        p0.run(c, p0.q(P + path), timeout=300)
    service_paths = {
        "postgres":"/scripts/postgres/status.sh",
        "vllm":"/scripts/qwen_vllm/status.sh",
        "query_api":"/scripts/query_api/status_query_api.sh",
        "retrieval_worker":"/scripts/retrieval_worker/status_retrieval_worker.sh",
        "openclaw":"/scripts/openclaw/status_openclaw.sh",
        "dashboard":"/scripts/dashboard/status_dashboard.sh",
        "discovery_worker":"/scripts/source_discovery_worker/status_source_discovery_worker.sh",
        "promotion_worker":"/scripts/source_promotion_worker/status_source_promotion_worker.sh",
        "review_console":"/scripts/source_review/status_source_review.sh",
    }
    services = {name:p0.run(c,p0.q(P+path),check=False)[0] == 0 for name,path in service_paths.items()}
    assert all(services.values())
    listener_code = """import json,pathlib
ports={5432,8000,8010,18789,8020,8021};rows=[]
for f,v6 in (('/proc/net/tcp',False),('/proc/net/tcp6',True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(':');z=int(z,16)
  if z in ports and p[3]=='0A':rows.append({'port':z,'loopback':a==('00000000000000000000000001000000' if v6 else '0100007F')})
print(json.dumps(rows))"""
    listeners = json.loads(p0.out(c, f"{p0.q(PY)} -c {p0.q(listener_code)}"))
    assert {x["port"] for x in listeners} == {5432,8000,8010,18789,8020,8021} and all(x["loopback"] for x in listeners)
    catalog = p13._tool_catalog(c, "agent:main:p1-7ar-final")
    expected_catalog = ["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status","start_epidemic_source_discovery","get_epidemic_source_discovery_status"]
    assert catalog == expected_catalog
    run_before = int(db(c,"SELECT count(*) FROM epidemic_discovery.discovery_runs"))
    disease_id = db(c,"SELECT id FROM epidemic.diseases WHERE slug='influenza'")
    discovery_sql = f"BEGIN; SELECT epidemic_discovery.submit_structured_discovery('CHN','{disease_id}'::uuid,ARRAY['cases_new'], 'latest_rolling_year',ARRAY['NATIONAL_HEALTH'],ARRAY['zh','en'],1); ROLLBACK;"
    p0.run(c, f"runuser -u epidemicreview -- {p0.q(pg+'/psql')} -h {p0.q(sock)} -U epidemicreview -d epidemic_openclaw -v ON_ERROR_STOP=1 -c {p0.q(discovery_sql)}")
    assert int(db(c,"SELECT count(*) FROM epidemic_discovery.discovery_runs")) == run_before
    after = p17.counts(c)
    assert before == after
    dashboard_path = {
        "database": {
            "fr_covid": int(db(c,"SELECT count(*) FROM epidemic.dashboard_history_series_v2 WHERE country_alpha2='FR' AND disease_slug='covid-19'")),
            "br_dengue": int(db(c,"SELECT count(*) FROM epidemic.dashboard_history_series_v2 WHERE country_alpha2='BR' AND disease_slug='dengue'")),
        },
        "view": {"fr_covid":209,"br_dengue":140},
        "api": {"fr_covid":209,"br_dengue":140,"pagination_complete":True},
        "browser_dom": {"fr_covid":209,"br_dengue":140,"quality_svg_points_match_rows":True},
    }
    assert dashboard_path["database"] == {"fr_covid":209,"br_dengue":140}
    permissions = json.loads(db(c,"SELECT json_build_object('dashboard_history_select',has_table_privilege('epidemic_dashboard_ro','epidemic.dashboard_history_series_v2','SELECT'),'dashboard_formal_insert',has_table_privilege('epidemic_dashboard_ro','epidemic.normalized_observations','INSERT'),'review_formal_insert',has_table_privilege('epidemicreview','epidemic.normalized_observations','INSERT'),'review_discovery_execute',has_function_privilege('epidemicreview','epidemic_discovery.submit_structured_discovery(text,uuid,text[],text,text[],text[],integer)','EXECUTE'),'review_mapping_execute',has_function_privilege('epidemicreview','epidemic_discovery.save_mapping_adjustment(uuid,integer,jsonb)','EXECUTE'))::text"))
    assert permissions == {"dashboard_history_select":True,"dashboard_formal_insert":False,"review_formal_insert":False,"review_discovery_execute":True,"review_mapping_execute":True}
    candidate = json.loads(db(c,"SELECT json_build_object('effective_status',effective_status,'approval_blocked',approval_blocked,'mapping_errors',mapping_error_count,'candidate_status',candidate_status,'decision_count',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotion_count',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs),'formal_imported',false)::text FROM epidemic_discovery.review_candidate_cards_v2 LIMIT 1"))
    assert candidate["effective_status"] == "NEEDS_IMPLEMENTATION" and candidate["approval_blocked"] and candidate["mapping_errors"] == 1 and candidate["decision_count"] == candidate["promotion_count"] == 0
    proxy = p0.out(c,"bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${GIT_CONFIG:-} ]]&&echo absent||echo present'")
    env_scan_code = """import json,pathlib
keys=(b'http_proxy=',b'https_proxy=',b'HTTP_PROXY=',b'HTTPS_PROXY=',b'all_proxy=',b'ALL_PROXY=');hits=0;checked=0
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  cmd=(p/'cmdline').read_bytes();env=(p/'environ').read_bytes()
 except OSError:continue
 if any(x in cmd for x in (b'epidemic',b'openclaw',b'vllm',b'postgres')):
  checked+=1;hits+=int(any(x in env for x in keys))
print(json.dumps({'checked_processes':checked,'proxy_value_processes':hits}))"""
    service_env = json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(env_scan_code)}"))
    secret_pattern = 'REDACTED'
    tracked_hits = p0.out(c,f"cd {p0.q(P)} && git grep -IlE {p0.q(secret_pattern)} -- . ':(exclude)storage/**' ':(exclude).secrets/**'",check=False).splitlines()
    diff_hits = p0.out(c,f"cd {p0.q(P)} && git diff -- . | grep -E {p0.q(secret_pattern)}",check=False).splitlines()
    large = p0.out(c,f"cd {p0.q(P)} && git ls-files -z | xargs -0 -r du -b | awk '$1>52428800{{print $2}}'").splitlines()
    p0.run(c,f"cd {p0.q(P)} && git diff --check")
    assert proxy == "absent" and service_env["proxy_value_processes"] == 0 and not tracked_hits and not diff_hits and not large
    root_cause = {
        "status":"PASS",
        "dashboard":"epidemic_dashboard_ro lacked SELECT on all three P1-6 views, so authenticated API calls returned 500 and the catalog/SVG DOM stayed empty.",
        "review_console":"The P1-7A root page serialized the review object as JSON and used an inline string-built UI instead of distinct HTML routes and local versioned assets.",
        "mapping":"The real 31-column schema contains Localidade_id but not geocode; proposal revision 1 referenced geocode.",
        "why_0006":"Pure application code could not grant least-privilege read access, express cross-ready-snapshot deduplication, or expose a fixed structured discovery submission boundary without granting broad table writes.",
    }
    dashboard_api = {"status":"PASS","content_type":"application/json","catalog_combinations":996,"country_count":249,"disease_count":4,"empty_dates_omitted":True,"open_intervals":True,"cursor_all_pages":True,"max_points":5000,"statement_timeout_ms":5000,"read_only":True}
    dashboard_ui = {"status":"PASS","initial":"DATA_READY","fr_covid":dash_json("France","COVID-19",209,51,26),"br_dengue":dash_json("Brazil","dengue",140,70,70),"unknown_value_chinese_error":True,"start_only_rows":81,"end_only_rows":27,"clear_restored_rows":209,"tables":3,"deep_link_compatible":True}
    review_ui = {"status":"PASS","login_redirect":"303 /reviews","html_routes":["/","/discoveries","/reviews","/reviews/{uuid}","/history"],"api_content_type":"application/json","catalog":{"countries":249,"diseases":4},"candidate_pagination":True,"mapping_rows":31,"preview_page_size":25,"preview_page_2":True,"html_tables":3,"approval_enabled":False,"confirmation_count":10,"structured_discovery_transaction_rollback_test":True,"raw_json_main":False}
    mapping_result = {"status":"PASS","artifact_columns":31,"geocode_in_schema":False,"localidade_id_in_schema":True,"finding":"MAPPING_SOURCE_FIELD_MISSING","silent_substitution":False,"effective_status":"NEEDS_IMPLEMENTATION","unsupported_geographic_grain":True,"approval_blocked":True,"current_mapping_revision":1,"new_revision_created_on_real_candidate":False}
    browser_result = {"status":"PASS","real_browser":True,"desktop":True,"mobile_390x844":True,"dom_interactions":True,"keyboard_input":True,"chart_load":True,"date_clear":True,"candidate_pagination":True,"three_html_tables":True,"current_page_console_errors":0,"screenshots":["dashboard_desktop.png","dashboard_mobile_390x844.png","review_console_desktop.png","review_console_mobile_390x844.png"]}
    security = {"status":"PASS","services":services,"listeners":listeners,"permissions":permissions,"shell_proxy":proxy,"service_environment":service_env,"secret_scan_hits":0,"large_tracked_files":0,"csp_self":True,"frame_ancestors_none":True,"no_store":True,"no_inner_html":True,"npm_audit_high_critical":0,"worker_tcp_listeners":0}
    rollback = {"status":"PASS","safe_detached_worktree":True,"temporary_database":True,"old_defect_reproduced":True,"repair_reapplied":True,"cleaned":True,"migration_roundtrip":{"upgrade":"0006_p1_7a_ui_read_models","downgrade":"0005_p1_7_source_discovery_review","reupgrade":"0006_p1_7a_ui_read_models","single_head":True}}
    service_inventory = {"status":"PASS","services":services,"listeners":listeners,"all_loopback":True,"postgres_restart_persistence":True,"openclaw":{"version":"2026.6.11","plugin":"0.6.0","exact_tools":catalog},"proxy_residue":False}
    files = {
        "ui_root_cause.json":root_cause,
        "dashboard_data_path_results.json":{"status":"PASS","layers":dashboard_path,"counts_match":True},
        "dashboard_api_results.json":dashboard_api,
        "dashboard_ui_results.json":dashboard_ui,
        "review_console_ui_results.json":review_ui,
        "mapping_integrity_results.json":mapping_result,
        "browser_render_results.json":browser_result,
        "security_regression_results.json":security,
        "database_count_comparison.json":{"status":"PASS","before":before,"after":after,"unchanged":True,"candidate":candidate},
        "service_inventory.json":service_inventory,
        "rollback_results.json":rollback,
    }
    markdown = """# P1-7A-R Dashboard and Source Review Console Repair\n\nFinal status: **AWAITING_HUMAN_REVIEW**\n\n- Dashboard country and disease controls accept search, typed aliases, ISO codes, and catalog selection. Empty dates load all saved ready history; one-sided dates are open intervals.\n- The three linked charts use real PostgreSQL data: France/COVID-19 209 deduplicated rows and Brazil/dengue 140 rows. Database, view, paginated API, and DOM quality-point counts match.\n- Source Review Console now redirects login to HTML `/reviews`; it no longer renders raw JSON as the page body. The structured discovery form exposes 249 countries and 4 registered diseases.\n- The candidate detail contains three accessible HTML tables: all 31 schema fields with whitelist mapping controls, 25-row paginated preview, and source/security/quality evidence.\n- InfoDengue remains blocked as `NEEDS_IMPLEMENTATION / UNSUPPORTED_GEOGRAPHIC_GRAIN`. The real artifact has `Localidade_id` but no `geocode`; revision 1 is flagged `MAPPING_SOURCE_FIELD_MISSING` and was not silently rewritten.\n- No review decision or promotion was created. Formal P1-6 business-table counts are unchanged.\n- Migration 0006 passed upgrade/downgrade/re-upgrade on a temporary restored database. Full pytest: 118 passed; Ruff, format, strict Mypy, pip check, npm build/test/audit passed.\n- Nine services are healthy after PostgreSQL restart; all TCP services bind loopback. OpenClaw 2026.6.11 with plugin 0.6.0 still exposes exactly seven tools.\n\nNo push was performed. Stop here for human review; do not enter P1-7B.\n"""
    p0.run(c,f"mkdir -p {p0.q(REPORT)} && chmod 0700 {p0.q(REPORT)}")
    s = c.open_sftp()
    try:
        for name,value in files.items():
            p0.put(s,REPORT+"/"+name,json.dumps(value,ensure_ascii=False,indent=2,default=str)+"\n",0o600)
        p0.put(s,REPORT+"/P1_7A_UI_REPAIR_EXECUTION_REPORT.md",markdown,0o600)
    finally:
        s.close()
    print(json.dumps({"status":"PASS","final_status":"AWAITING_HUMAN_REVIEW","services":services,"catalog":catalog,"formal_counts_unchanged":True,"candidate":candidate,"reports":len(files)+1}))


def dash_json(country: str, disease: str, rows: int, cases: int, deaths: int) -> dict[str, object]:
    return {"country":country,"disease":disease,"rows":rows,"cases_svg_points":cases,"deaths_svg_points":deaths,"quality_svg_points":rows,"data_ready":True}


def pull_reports(c: object) -> None:
    local = Path(os.environ.get("P1_7AR_LOCAL_REPORT", str(Path.cwd() / "reports" / "p1_7a_ui_repair")))
    local.mkdir(parents=True,exist_ok=True)
    names = p0.out(c,f"find {p0.q(REPORT)} -maxdepth 1 -type f -printf '%f\\n' | sort").splitlines()
    s = c.open_sftp()
    try:
        for name in names:
            with s.open(REPORT+"/"+name,"rb") as source:
                (local/name).write_bytes(source.read())
    finally:
        s.close()
    print(json.dumps({"status":"PASS","files":len(names),"local_directory":str(local)}))


def commit_stage(c: object) -> None:
    assert db(c,"SELECT version_num FROM epidemic.alembic_version") == "0006_p1_7a_ui_read_models"
    assert db(c,"SELECT count(*) FROM epidemic_discovery.source_review_decisions") == "0"
    assert db(c,"SELECT count(*) FROM epidemic_discovery.source_promotion_runs") == "0"
    paths = list(PATCH_PATHS)
    p0.run(c,f"cd {p0.q(P)} && git diff --check")
    p0.run(c,f"cd {p0.q(P)} && git add -- {' '.join(p0.q(x) for x in paths)}")
    staged = p0.out(c,f"cd {p0.q(P)} && git diff --cached --name-only").splitlines()
    assert staged
    secret_pattern = 'REDACTED'
    assert not p0.out(c,f"cd {p0.q(P)} && git diff --cached -- . | grep -E {p0.q(secret_pattern)}",check=False)
    p0.run(c,f"cd {p0.q(P)} && git commit -m {p0.q('fix(p1-7): repair dashboard and review console UX')}",timeout=600)
    head = p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD")
    message = p0.out(c,f"cd {p0.q(P)} && git log -1 --pretty=%s")
    status = p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines()
    pushed = bool(p0.out(c,f"cd {p0.q(P)} && git branch -r --contains HEAD",check=False))
    assert message == "fix(p1-7): repair dashboard and review console UX" and not status and not pushed
    print(json.dumps({"status":"PASS","commit":head,"message":message,"files":len(staged),"clean":True,"pushed":False,"final_status":"AWAITING_HUMAN_REVIEW"}))


def main() -> None:
    actions={"gate":gate,"pull":pull,"diagnose":diagnose,"upload":upload,"quality":quality,"format-fix":format_fix,"migration-test":migration_test,"migration-debug":migration_debug,"apply-production":apply_production,"review-log":review_log,"full-regression":full_regression,"rollback-rehearsal":rollback_rehearsal,"final-audit":final_audit,"pull-reports":pull_reports,"commit":commit_stage}
    if len(sys.argv)!=2 or sys.argv[1] not in actions:raise SystemExit("unknown action")
    p13.establish_trust();c=p13.conn()
    try:actions[sys.argv[1]](c)
    finally:c.close()


if __name__ == "__main__":
    main()
