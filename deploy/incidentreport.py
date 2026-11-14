"""Read only selected non-secret incident summaries."""
from pathlib import Path
import ast,json,subprocess,sys,time
root=Path('/root/vpn-incident-latest').read_text().strip();p=Path(root)
def saved(name):return json.loads((p/(name+'.json')).read_text())['out']
mode=sys.argv[1] if len(sys.argv)>1 else 'network'
if mode=='network':
 print('BACKUP '+root)
 for name in ['rules','route-client','route-external','sysctl']:print(name+': '+saved(name).strip())
 print('FIREWALL')
 for line in json.loads((p/'firewall-readable.json').read_text()):print(line)
elif mode=='services':
 print(saved('listeners'))
 print('\n'.join(line for line in saved('services').splitlines() if any(x in line.lower() for x in ['vpn','awg','wg-','xray','ssh','clash','mihomo','network','caddy'])))
 print((p/'scripts-summary.json').read_text())
elif mode=='scripts':
 for name in ['vpn-clashinstall.py','vpn-clashuninstall.py']:
  file=Path('/root')/name
  if not file.exists():continue
  tree=ast.parse(file.read_text())
  lists=[]
  for node in ast.walk(tree):
   if isinstance(node,(ast.List,ast.Tuple)) and node.elts and isinstance(node.elts[0],ast.Constant) and node.elts[0].value in ['ip','iptables','iptables-restore','nft','sysctl','wg','awg','systemctl']:
    parts=[x.value if isinstance(x,ast.Constant) and isinstance(x.value,(str,int)) else '<dynamic>' for x in node.elts]
    lists.append(dict(line=node.lineno,command=parts))
  print(json.dumps(dict(file=name,commands=lists)))
