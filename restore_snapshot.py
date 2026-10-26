"""Restore a verified private snapshot into a new, empty workspace."""
import argparse,hashlib,json,shutil,zipfile
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument('--assets',type=Path,required=True);p.add_argument('--destination',type=Path,required=True);a=p.parse_args()
 root=Path(__file__).resolve().parent;dest=a.destination.resolve()
 if dest.exists() and any(dest.iterdir()):raise SystemExit('Destination must be empty')
 dest.mkdir(parents=True,exist_ok=True)
 metadata=root/'snapshot'
 for asset in json.loads((metadata/'assets.json').read_text()):
  with (a.assets/asset['name']).open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
  if digest!=asset['sha256']:raise SystemExit('Asset hash mismatch')
 archives={}
 try:
  for line in (metadata/'manifest.jsonl').read_text(encoding='utf-8').splitlines():
   item=json.loads(line);target=(dest/item['path']).resolve()
   if not target.is_relative_to(dest):raise SystemExit('Invalid manifest path')
   if target.exists():raise SystemExit('Refusing to overwrite an existing file')
   target.parent.mkdir(parents=True,exist_ok=True)
   if item['storage']=='git':payload=(root/item['path']).read_bytes()
   else:
    name=item['asset']
    if name not in archives:archives[name]=zipfile.ZipFile(a.assets/name)
    payload=archives[name].read(item['blob'])
   if hashlib.sha256(payload).hexdigest()!=item['sha256']:raise SystemExit('File hash mismatch')
   target.write_bytes(payload)
   if target.suffix=='.sh':target.chmod(0o755)
 finally:
  for archive in archives.values():archive.close()
 print('Restored and verified snapshot')
if __name__=='__main__':main()
