"""Live inspection only; never prints actual key/config values."""
import ast,configparser,json,pathlib,re,sqlite3,subprocess,sys
P=pathlib.Path
mode=sys.argv[1] if len(sys.argv)>1 else 'check'
if mode=='check':
 c=sqlite3.connect('file:/opt/vpn-manager/data/clients-staging.db?mode=ro',uri=True)
 rows=c.execute('select id,wg_client_id from clients').fetchall()
 metas=[json.loads(p.read_text()) for p in P('/opt/vpn-manager/awg-clients').glob('*/metadata.json')]
 text=P('/etc/amnezia/amneziawg/awg-test.conf').read_text()
 blocks=text.split('[Peer]')[1:]
 peers={}
 for block in blocks:
  fields={}
  for line in block.splitlines():
   if '=' in line and not line.strip().startswith('#'):
    k,v=line.split('=',1);fields[k.strip()]=v.strip()
  peers[fields.get('PublicKey')]=fields.get('AllowedIPs')
 live=set(subprocess.check_output(['awg','show','awg-test','peers'],text=True).split())
 keys={m['public_key'] for m in metas}
 ids={str(m['client_id']) for m in metas}
 print('DB_LINKS_MATCH_METADATA',all(str(w) in ids for _,w in rows))
 print('METADATA_KEYS_MATCH_DISK',keys==set(peers))
 print('ALL_MANAGED_KEYS_LIVE',keys<=live,'LIVE_ONLY_EXTRA',len(live-keys))
 print('ADDRESS_MATCH',all(m['address'].split('/')[0]==str(peers.get(m['public_key'],'')).split('/')[0] for m in metas))
 print('LIVE_EXTRA_MATCH_IPHONE_TEST',end=' ')
 t=P('/etc/amnezia/amneziawg/iphone-test.conf')
 if t.exists():
  cfg=configparser.ConfigParser();cfg.read(t)
  private=cfg.get('Interface','PrivateKey',fallback='')
  r=subprocess.run(['awg','pubkey'],input=private,text=True,capture_output=True)
  print(set(r.stdout.split())==live-keys)
 else: print(False)
 print(subprocess.check_output(['systemctl','show','awg-quick@awg-test','-p','ActiveState','-p','Result','-p','ExecMainStatus'],text=True))
elif mode=='source':
 path=P('/opt/vpn-manager/awg_adapter.py')
 source=path.read_text()
 # Remove assignment string literals with sensitive variable names, and literal keys.
 tree=ast.parse(source)
 lines=source.splitlines()
 for node in ast.walk(tree):
  if isinstance(node,(ast.Assign,ast.AnnAssign)):
   segment=ast.get_source_segment(source,node) or ''
   if re.search(r'(?i)(token|password|secret)\s*=',segment):
    for n in range(node.lineno-1,node.end_lineno):lines[n]='# sensitive assignment omitted'
 source='\n'.join(lines)
 source=re.sub(r'(?<![A-Za-z0-9])[A-Za-z0-9+/]{43}=', '<key omitted>',source)
 page=int(sys.argv[2]) if len(sys.argv)>2 else 0
 for i,line in list(enumerate(source.splitlines(),1))[page*38:(page+1)*38]: print(str(i)+': '+line)
elif mode=='routes':
 for name in ['app.py','server/staging_api.py']:
  p=P('/opt/vpn-manager')/name if name=='app.py' else P('/opt/vpn-manager-panel-test')/name
  print('FILE',str(p))
  if p.exists():
   t=ast.parse(p.read_text())
   for n in ast.walk(t):
    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
     print(n.name,','.join(ast.unparse(d) for d in n.decorator_list))
