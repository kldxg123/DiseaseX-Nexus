"""Export a stationary project cluster through a private disposable PostgreSQL copy.

Only the disposable copy is started or modified. No original service is stopped.
"""


def recover_database_snapshot():
    import ctypes
    import ctypes.util
    import gzip
    import io
    import json
    import os
    import re
    import shutil
    import subprocess
    import tarfile
    import urllib.request
    from pathlib import Path
    import hashlib

    # These globals are supplied by the existing snapshot controller on the server.
    global ASSET_PREFIX
    tools=OUT/'private-recovery-tools';tools.mkdir(mode=0o700,exist_ok=True)
    package_records=[]
    indexes=[]

    def read_index(url):
        with urllib.request.urlopen(url,timeout=60) as r:data=r.read()
        if url.endswith('.gz'):data=gzip.decompress(data)
        elif url.endswith('.xz'):
            import lzma
            data=lzma.decompress(data)
        entries={}
        for block in data.decode().split('\n\n'):
            fields={}
            for line in block.splitlines():
                if line and not line[0].isspace() and ': ' in line:
                    key,value=line.split(': ',1);fields[key]=value
            if all(k in fields for k in ('Package','Filename','SHA256')):entries.setdefault(fields['Package'],fields)
        return entries

    official='https://apt.postgresql.org/pub/repos/apt/'
    indexes.append((official,read_index(official+'dists/noble-pgdg/main/binary-amd64/Packages.gz')))
    installed=set()

    def extract_deb(data):
        if not data.startswith(b'!<arch>\n'):raise RuntimeError('Invalid official package archive')
        offset=8
        while offset+60<=len(data):
            header=data[offset:offset+60];name=header[:16].decode().strip().rstrip('/');size=int(header[48:58]);start=offset+60;payload=data[start:start+size]
            if name.startswith('data.tar'):
                if name.endswith('.zst'):
                    lib=ctypes.CDLL(ctypes.util.find_library('zstd'))
                    lib.ZSTD_getFrameContentSize.argtypes=[ctypes.c_void_p,ctypes.c_size_t];lib.ZSTD_getFrameContentSize.restype=ctypes.c_ulonglong
                    source=ctypes.create_string_buffer(payload)
                    length=lib.ZSTD_getFrameContentSize(source,len(payload))
                    capacity=int(length) if length<512*1024**2 else 256*1024**2
                    output=ctypes.create_string_buffer(capacity)
                    lib.ZSTD_decompress.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t];lib.ZSTD_decompress.restype=ctypes.c_size_t
                    n=lib.ZSTD_decompress(output,capacity,source,len(payload))
                    lib.ZSTD_isError.argtypes=[ctypes.c_size_t];lib.ZSTD_isError.restype=ctypes.c_uint
                    if lib.ZSTD_isError(n):raise RuntimeError('Official package decompression failed')
                    payload=output.raw[:n]
                with tarfile.open(fileobj=io.BytesIO(payload),mode='r:*') as archive:
                    archive.extractall(tools,filter='data')
                return
            offset=start+size+(size%2)
        raise RuntimeError('Official package payload missing')

    def install_private(name):
        if name in installed:return
        selected=None
        for base,index in indexes:
            if name in index:selected=(base,index[name]);break
        if selected is None:
            base='https://archive.ubuntu.com/ubuntu/'
            if not any(url==base for url,_ in indexes):
                indexes.append((base,read_index(base+'dists/noble/main/binary-amd64/Packages.xz')))
                indexes.append((base,read_index(base+'dists/noble/universe/binary-amd64/Packages.xz')))
            for base,index in indexes:
                if name in index:selected=(base,index[name]);break
        if selected is None:raise RuntimeError('Required official package unavailable')
        base,entry=selected
        with urllib.request.urlopen(base+entry['Filename'],timeout=120) as response:data=response.read()
        if hashlib.sha256(data).hexdigest()!=entry['SHA256']:raise RuntimeError('Official package checksum mismatch')
        extract_deb(data);installed.add(name)
        package_records.append({'package':name,'version':entry.get('Version'),'url':base+entry['Filename'],'sha256':entry['SHA256']})

    for name in ['postgresql-18','postgresql-client-18','libpq5']:install_private(name)
    bindir=tools/'usr/lib/postgresql/18/bin'
    env=dict(os.environ)
    env['LD_LIBRARY_PATH']=str(tools/'usr/lib/x86_64-linux-gnu')+':'+str(tools/'usr/lib')+':'+env.get('LD_LIBRARY_PATH','')
    dependencies={'libicu':'libicu74','libreadline':'libreadline8t64','libldap':'libldap2','liblber':'libldap2','libxml2':'libxml2','libxslt':'libxslt1.1','liblz4':'liblz4-1','libzstd':'libzstd1','libpam':'libpam0g','libsasl2':'libsasl2-2','libgssapi':'libgssapi-krb5-2','libtinfo':'libtinfo6','libssl':'libssl3t64','libcrypto':'libssl3t64','libsystemd':'libsystemd0','libuuid':'libuuid1','libselinux':'libselinux1'}
    for attempt in range(8):
        missing=set()
        for program in ['postgres','psql','pg_dump','pg_ctl']:
            checked=subprocess.run([str(bindir/program),'--version'],env=env,capture_output=True,text=True)
            if checked.returncode:
                for library in re.findall(r'(lib[A-Za-z0-9_-]+\.so(?:\.[0-9]+)*)',checked.stderr):
                    for prefix,package in dependencies.items():
                        if library.startswith(prefix):missing.add(package);break
        if not missing:break
        for package in missing:install_private(package)
    for program in ['postgres','psql','pg_dump','pg_ctl']:
        if subprocess.run([str(bindir/program),'--version'],env=env,capture_output=True).returncode:raise RuntimeError('Private database tool dependency verification failed')
    emit({'phase':'private_database_tools_verified','official_packages':len(package_records)})

    main=next(Path(r) for r in ROOTS if Path(r).name=='epidemic-openclaw')
    candidates=[]
    for here,dirs,files in os.walk(main/'storage'):
        if 'PG_VERSION' in files:
            control=Path(here)/'global/pg_control'
            candidates.append((int((Path(here)/'postmaster.pid').exists()),control.stat().st_mtime if control.exists() else 0,Path(here)));dirs[:]=[]
    if not candidates:raise RuntimeError('Project database cluster is unavailable')
    source=max(candidates,key=lambda v:(v[0],v[1]))[2]
    if (source/'PG_VERSION').read_text().strip()!='18':raise RuntimeError('Project cluster major version mismatch')
    work=OUT/'private-database-recovery';work.mkdir(mode=0o700,exist_ok=True)
    clone=work/'cluster'
    if clone.exists():raise RuntimeError('Recovery copy already exists; review before reuse')
    def source_state():
        result={}
        for here,dirs,files in os.walk(source):
            for name in files:
                p=Path(here,name)
                try:
                    stat=p.stat();result[str(p.relative_to(source))]=(stat.st_size,stat.st_mtime_ns)
                except OSError:pass
        return result
    before=source_state();size=sum(v[0] for v in before.values())
    if shutil.disk_usage(work).free<size*2+1024**3:raise RuntimeError('Insufficient private recovery disk space')
    ignore=shutil.ignore_patterns('postmaster.pid','postmaster.opts','*.key','*.pem','*.log','pg_hba.conf','pg_ident.conf','postgresql.conf','postgresql.auto.conf')
    shutil.copytree(source,clone,symlinks=False,ignore=ignore)
    if source_state()!=before:raise RuntimeError('Original database changed during copying; refusing inconsistent export')
    clone.chmod(0o700)
    (clone/'postgresql.auto.conf').write_text('')
    hba=work/'private_hba.conf';hba.write_text('local all all trust\n');hba.chmod(0o600)
    socket=work/'socket';socket.mkdir(mode=0o700)
    config=work/'recovery.conf'
    config.write_text("listen_addresses = ''\nport = 56439\nunix_socket_directories = '"+str(socket)+"'\nhba_file = '"+str(hba)+"'\nssl = off\nshared_buffers = '32MB'\nmax_connections = 10\nmax_worker_processes = 0\nautovacuum = off\nshared_preload_libraries = ''\njit = off\nlog_min_messages = fatal\nlog_min_error_statement = panic\nlog_statement = none\n")
    emit({'phase':'stationary_database_copy_verified','source_files':len(before),'source_bytes':size,'original_files_unchanged':True})
    query="COPY (SELECT json_build_object('roles',(SELECT json_agg(rolname) FROM pg_roles WHERE rolsuper),'databases',(SELECT json_agg(datname) FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres'))) TO STDOUT;\n"
    single=subprocess.run([str(bindir/'postgres'),'--single','-D',str(clone),'-c','config_file='+str(config),'postgres'],input=query,env=env,capture_output=True,text=True,timeout=180)
    marker=single.stdout.find('{"roles"')
    if single.returncode or marker<0:raise RuntimeError('Isolated database recovery inspection failed')
    info,_=json.JSONDecoder().raw_decode(single.stdout[marker:])
    if not info.get('roles') or not info.get('databases'):raise RuntimeError('Project database identity unavailable')
    candidates=info['databases']
    chosen=[v for v in candidates if re.search('epidemic|aibola|disease|intel',v,re.I)]
    if len(chosen)==1:dbname=chosen[0]
    elif len(candidates)==1:dbname=candidates[0]
    else:raise RuntimeError('Project database target is ambiguous')
    env.update(PGHOST=str(socket),PGPORT='56439',PGUSER=info['roles'][0],PGDATABASE=dbname,PGCONNECT_TIMEOUT='10')
    started=False
    try:
        begin=subprocess.run([str(bindir/'pg_ctl'),'-D',str(clone),'-l',str(work/'private_startup.log'),'-o','-c config_file='+str(config),'-w','start'],env=env,capture_output=True,timeout=90)
        if begin.returncode:raise RuntimeError('Private recovery database could not start')
        started=True
        table_query="SELECT table_schema || '.' || table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') AND table_type='BASE TABLE' ORDER BY 1"
        result=subprocess.run([str(bindir/'psql'),'-XAt','-w','-c',table_query],env=env,capture_output=True,text=True,timeout=30)
        if result.returncode:raise RuntimeError('Private database metadata export failed')
        names=result.stdout.splitlines();exclude=[v for v in names if re.search(r'(^|[_.])(auth|users|sessions|credentials|secrets|api_keys|tokens)([_.]|$)',v,re.I)]
        command=[str(bindir/'pg_dump'),'--format=plain','--no-owner','--no-privileges','--no-comments','-w']
        for table in exclude:command.append('--exclude-table='+table)
        dumped=subprocess.run(command,env=env,capture_output=True,timeout=300)
        if dumped.returncode:raise RuntimeError('Private logical database export failed')
    finally:
        if started:
            stopped=subprocess.run([str(bindir/'pg_ctl'),'-D',str(clone),'-m','fast','-w','stop'],env=env,capture_output=True,timeout=90)
            if stopped.returncode:raise RuntimeError('Private recovery process did not stop cleanly')
    ASSET_PREFIX='server-db-part'
    writer=SnapshotWriter()
    folder=writer.repo/'snapshot'
    writer.manifest=[json.loads(v) for v in (folder/'manifest.jsonl').read_text(encoding='utf-8').splitlines()]
    writer.excluded=json.loads((folder/'exclusions.json').read_text())
    writer.syntax_warnings=json.loads((folder/'syntax-reference-files.json').read_text())
    writer.blobs={v['sha256']:{'asset':v['asset'],'blob':v['blob']} for v in writer.manifest if v['storage']=='release'}
    writer.add('database/business_database.sql',dumped.stdout)
    metadata=json.loads((folder/'server-metadata.json').read_text())
    metadata['database']={'status':'exported','business_tables':len(names)-len(exclude),'excluded_auth_tables':len(exclude),'bytes':len(dumped.stdout),'consistent_snapshot':True,'source':'verified stationary physical cluster recovered only in a private disposable copy','original_files_unchanged':source_state()==before,'private_recovery_process_stopped':True,'source_cluster_files':len(before),'source_cluster_bytes':size}
    metadata['database_recovery_official_packages']=package_records
    status=finish_writer(writer,metadata)
    emit({'phase':'business_database_recovery_export_verified','business_tables':status['database']['business_tables'],'original_files_unchanged':metadata['database']['original_files_unchanged']})
