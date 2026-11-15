"""Read-only, strict AWG v1/v2 export for the isolated Clash Mi pilot."""
import base64
import configparser
import ipaddress
import json
import re


def convert(contents, label):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(contents.decode('utf-8-sig'))
    if parser.defaults() or parser.sections() != ['Interface', 'Peer']:
        raise ValueError('Unsupported profile structure')
    interface, peer = parser['Interface'], parser['Peer']
    numeric = {'Jc', 'Jmin', 'Jmax', 'S1', 'S2', 'S3', 'S4'}
    headers = {'H1', 'H2', 'H3', 'H4'}
    strings = {'I1', 'I2', 'I3', 'I4', 'I5'}
    if set(interface) - ({'PrivateKey', 'Address', 'DNS', 'MTU'} | numeric | headers | strings):
        raise ValueError('Unsupported interface option')
    if set(peer) - {'PublicKey', 'PresharedKey', 'Endpoint', 'AllowedIPs', 'PersistentKeepalive'}:
        raise ValueError('Unsupported peer option')
    def key(value):
        if len(base64.b64decode(value, validate=True)) != 32:
            raise ValueError('Invalid key')
        return value
    addresses = [ipaddress.ip_interface(x.strip()) for x in interface['Address'].split(',')]
    if not addresses or len({x.version for x in addresses}) != len(addresses):
        raise ValueError('Unsupported addresses')
    allowed = [str(ipaddress.ip_network(x.strip())) for x in peer['AllowedIPs'].split(',')]
    if '0.0.0.0/0' not in allowed or set(allowed) - {'0.0.0.0/0', '::/0'}:
        raise ValueError('Only full-tunnel profiles are supported by the pilot')
    endpoint = re.fullmatch(r'(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+):([0-9]{1,5})', peer['Endpoint'])
    if not endpoint or not 0 < int(endpoint[2]) < 65536:
        raise ValueError('Invalid endpoint')
    proxy = dict(name=label, type='wireguard', server=endpoint[1].strip('[]'), port=int(endpoint[2]),
                 udp=True, **{'private-key':key(interface['PrivateKey']), 'public-key':key(peer['PublicKey']), 'allowed-ips':allowed})
    for address in addresses:
        proxy['ip' if address.version == 4 else 'ipv6'] = str(address.ip)
    if 'PresharedKey' in peer:proxy['pre-shared-key'] = key(peer['PresharedKey'])
    for section, source, target, low, high in [(interface,'MTU','mtu',576,9000),(peer,'PersistentKeepalive','persistent-keepalive',0,65535)]:
        if source in section:
            value = int(section[source])
            if not low <= value <= high:raise ValueError('Invalid numeric option')
            proxy[target] = value
    options = {}
    for name in numeric & set(interface):
        value = int(interface[name])
        if not 0 <= value <= 65535:raise ValueError('Invalid AWG option')
        options[name.lower()] = value
    for name in headers & set(interface):
        value = interface[name]
        values = value.split('-')
        if not 1 <= len(values) <= 2 or any(not x.isdigit() or not 0 <= int(x) <= 4294967295 for x in values):
            raise ValueError('Invalid AWG header')
        if int(values[0]) > int(values[-1]):raise ValueError('Invalid AWG range')
        options[name.lower()] = int(value) if len(values) == 1 else value
    for name in strings & set(interface):options[name.lower()] = interface[name]
    if not (numeric | headers) <= set(interface):
        # S3/S4 are optional in AWG v1; the basic fields must all exist.
        if not ({'Jc','Jmin','Jmax','S1','S2'} | headers) <= set(interface):raise ValueError('Missing AWG options')
    if options['jmin'] > options['jmax']:raise ValueError('Invalid AWG padding')
    proxy['amnezia-wg-option'] = options
    dns = [str(ipaddress.ip_address(x.strip())) for x in interface['DNS'].split(',')]
    proxy.update({'remote-dns-resolve':True, 'dns':dns})
    # No providers, geodata, analytics, remote rules, controller or LAN listener.
    config = {'mixed-port':7890,'allow-lan':False,'bind-address':'127.0.0.1', 'mode':'rule','log-level':'silent',
              'ipv6':any(x.version == 6 for x in addresses),
              'tun':{'enable':True,'stack':'mixed','auto-route':True,'auto-detect-interface':True,'dns-hijack':['any:53']},
              'dns':{'enable':True,'enhanced-mode':'fake-ip','nameserver':dns},
              'proxies':[proxy],'proxy-groups':[{'name':'VPN','type':'select','proxies':[label]}], 'rules':['MATCH,VPN']}
    # JSON flow values are valid YAML; do not interpolate keys into YAML strings.
    return ('\n'.join(name+': '+json.dumps(value,ensure_ascii=False) for name,value in config.items())+'\n').encode()
