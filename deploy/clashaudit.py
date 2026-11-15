"""Read-only deployment preflight; prints option names and booleans only."""
import configparser,json,subprocess
from pathlib import Path
def run(*args):return subprocess.run(args,capture_output=True,text=True,timeout=30,check=True).stdout
p=configparser.ConfigParser(interpolation=None);p.optionxform=str;p.read('/opt/vpn-manager/awg-clients/92/client.conf')
caddy=Path('/etc/caddy/Caddyfile').read_text()
adapt=json.loads(run('caddy','adapt','--config','/etc/caddy/Caddyfile','--adapter','caddyfile'))
pid=run('systemctl','show','vpn-manager-connect.service','-p','MainPID','--value').strip()
cmd=Path('/proc/'+pid+'/cmdline').read_bytes().decode().replace(chr(0),' ')
print(json.dumps(dict(pilot_options={section:list(p[section]) for section in p.sections()},awg_present=Path('/sys/class/net/awg-test').exists(),ip_correct='185.23.239.142' in run('ip','-4','addr','show'),caddy_global_logging=adapt.get('logging',{}),caddy_config_starts_global=caddy.lstrip().startswith('{'),gunicorn_access_logging='access-logfile' in cmd,portal_active=pid!='0')))
