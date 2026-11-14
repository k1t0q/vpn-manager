from pathlib import Path
import hashlib,json
root=Path('/opt/vpn-manager-production')
for folder,names in [('server',['production_app.py','staging_api.py','requirements.txt']),('public',['index.html','sqlite-manager.js','sw.js','manifest.webmanifest'])]:
 for name in names:
  p=root/folder/name;b=p.read_bytes()
  print(json.dumps(dict(file=folder+'/'+name,blob=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest())))
print((root/'server/production_app.py').read_text())
