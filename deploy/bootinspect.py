"""Read-only post-boot AWG startup inspection; no secret config output."""
from pathlib import Path
import json,shutil,subprocess
def cmd(args):
 r=subprocess.run(args,capture_output=True,text=True,timeout=15);return r.returncode,r.stdout
units=['awg-quick@awg-test.service','vpn-manager-live-bridge.service','vpn-manager-live-api.service','vpn-manager-connect.service','ssh.service','caddy.service','xray.service']
out={}
for unit in units:
 code,text=cmd(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','UnitFileState','-p','ExecMainStatus','-p','FragmentPath'])
 out[unit]=text.splitlines()
config=Path('/etc/amnezia/amneziawg/awg-test.conf')
keys=[];hooks=[]
for line in config.read_text().splitlines():
 if '=' not in line or line.strip().startswith('#'):continue
 key,value=(x.strip() for x in line.split('=',1));keys.append(key)
 if key in ['PreUp','PostUp','PreDown','PostDown']:
  hooks.append(dict(name=key,network_commands_only=all(x in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .:/%-_+;!=$<>\\\"\'[]&|{}()*,' for x in value),mentions_flush=any(x in value for x in ['-F','--flush','flush ruleset']),mentions_vpn_test='awg-test' in value or '%i' in value))
print(json.dumps(dict(units=out,binaries={x:shutil.which(x) for x in ['awg','awg-quick','wg-quick']},config_keys=sorted(set(keys)),hooks=hooks),indent=1))
