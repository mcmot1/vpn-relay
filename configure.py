#!/usr/bin/python3
"""Configure the relay roles and provision AmneziaWG clients."""

import argparse
from contextlib import contextmanager
import getpass
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


CONFIG_DIR = Path('/etc/vpn-relay')
AWG_SETTINGS = (
    'Jc', 'Jmin', 'Jmax', 'S1', 'S2', 'S3', 'S4', 'H1', 'H2', 'H3', 'H4',
    'I1', 'I2', 'I3', 'I4', 'I5', 'HeaderProtectionKey', 'ContentPaddingAddition',
    'RekeyAfterTime', 'RekeyTimeout', 'RejectAfterTime', 'KeepaliveTimeout',
    'MaxHandshakeAttempts', 'RandomTrailers', 'DisableCookies',
)


def run(*command, input_text=None):
    result = subprocess.run(command, input=input_text, text=True, stdout=subprocess.PIPE, check=True)
    return result.stdout.strip()


def port(value):
    if not re.fullmatch(r'[0-9]{1,5}', value) or not 1 <= int(value) <= 65535:
        raise argparse.ArgumentTypeError('port must be between 1 and 65535')
    return int(value)


def hostname(value):
    label = r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?'
    if len(value) > 253 or not re.fullmatch(rf'{label}(?:\.{label})*', value):
        raise argparse.ArgumentTypeError('enter a hostname without a scheme, port, or path')
    return value


def endpoint(value):
    host, separator, number = value.rpartition(':')
    if not separator:
        raise argparse.ArgumentTypeError('endpoint must include a port')
    if host.startswith('[') and host.endswith(']'):
        try:
            host = f'[{ipaddress.IPv6Address(host[1:-1])}]'
        except ValueError as error:
            raise argparse.ArgumentTypeError('invalid IPv6 endpoint') from error
    else:
        host = hostname(host)
    return f'{host}:{port(number)}'


@contextmanager
def staged_file(destination, content):
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.' + destination.name + '.', delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
            file.close()
            yield temporary
        finally:
            temporary.unlink(missing_ok=True)


def replace_file(destination, content):
    with staged_file(destination, content) as temporary:
        os.replace(temporary, destination)


def read_secret():
    if sys.stdin.isatty():
        secret = getpass.getpass('Shared relay secret (64 hex characters): ')
    else:
        secret = sys.stdin.readline().strip()
    if not re.fullmatch(r'[0-9a-fA-F]{64}', secret):
        raise ValueError('expected the same 64-character relay secret on both hosts; generate it with openssl rand -hex 32')
    return secret


def configure_relay(args):
    installed_role = (CONFIG_DIR / 'role').read_text().strip()
    if installed_role != args.role:
        raise ValueError(f'this host has the {installed_role} role; requested {args.role}')
    secret = read_secret()
    config = (CONFIG_DIR / 'config.example.yaml').read_text()
    if (f'name: awg-{args.role}' not in config or 'REPLACE_WITH_SHARED_SECRET' not in config
            or '127.0.0.1:51821' not in config):
        raise ValueError('invalid relay template; run install.sh again for this role')
    config = config.replace('REPLACE_WITH_SHARED_SECRET', secret)
    config = config.replace('127.0.0.1:51821', f'127.0.0.1:{args.awg_port}')
    if args.role == 'ingress':
        if config.count('vpn-egress.example.com') != 2 or args.remote_host == 'vpn-egress.example.com':
            raise ValueError('use the real egress hostname and the installed ingress template')
        config = config.replace('vpn-egress.example.com:443', f'{args.remote_host}:{args.wss_port}')
        config = config.replace('vpn-egress.example.com', args.remote_host)
    else:
        config = config.replace('0.0.0.0:443', f'0.0.0.0:{args.wss_port}')
        tls_dir = CONFIG_DIR / 'tls'
        certificate = args.fullchain.read_bytes()
        private_key = args.privkey.read_bytes()
        with tempfile.TemporaryDirectory(dir=CONFIG_DIR) as temp_dir:
            cert_path, key_path = Path(temp_dir) / 'cert.pem', Path(temp_dir) / 'key.pem'
            cert_path.write_bytes(certificate)
            key_path.write_bytes(private_key)
            run('openssl', 'x509', '-in', str(cert_path), '-checkend', '0', '-noout')
            cert_public = run('openssl', 'x509', '-in', str(cert_path), '-pubkey', '-noout')
            key_public = run('openssl', 'pkey', '-in', str(key_path), '-passin', 'pass:', '-pubout')
            if cert_public != key_public:
                raise ValueError('TLS certificate and private key do not match')
        tls_dir.mkdir(mode=0o700, exist_ok=True)
        replace_file(tls_dir / 'fullchain.pem', certificate)
        replace_file(tls_dir / 'privkey.pem', private_key)

    destination = CONFIG_DIR / 'config.yaml'
    if destination.exists():
        replace_file(CONFIG_DIR / 'config.yaml.previous', destination.read_bytes())
    replace_file(destination, config.encode())
    print(f'Wrote {destination}')
    if args.role == 'ingress':
        print(f'Remote egress: {args.remote_host}:{args.wss_port} via WSS')
    print('To apply: sudo systemctl enable vpn-relay.service && sudo systemctl restart vpn-relay.service')


def read_awg_config(text):
    interface, allowed_ips, section = {}, [], ''
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].lower()
        elif '=' in line:
            key, value = (part.strip() for part in line.split('=', 1))
            key = key.lower()
            if section == 'interface':
                if key == 'address' and key in interface:
                    value = f'{interface[key]},{value}'
                interface[key] = value
            elif section == 'peer' and key == 'allowedips' and value:
                allowed_ips.extend(ipaddress.ip_network(item.strip(), strict=False) for item in value.split(','))
    return interface, allowed_ips


def create_client(args):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', args.interface) or args.interface.startswith('-'):
        raise ValueError('invalid AWG interface name')
    address = ipaddress.ip_interface(args.address)
    if address.network.prefixlen != address.max_prefixlen:
        raise ValueError('client address must be a single host (/32 for IPv4 or /128 for IPv6)')
    routes = ', '.join(str(ipaddress.ip_network(item.strip())) for item in args.allowed_ips.split(','))
    dns = ', '.join(str(ipaddress.ip_address(item.strip())) for item in args.dns.split(',')) if args.dns else ''
    server_path = args.server_config.resolve(strict=True)
    output = args.output.absolute()
    if output.suffix != '.conf' or output.exists():
        raise ValueError('choose a new output file ending in .conf')
    stored_text = server_path.read_text()
    stored, stored_peers = read_awg_config(stored_text)
    active, active_peers = read_awg_config(run('awg', 'showconf', args.interface))
    server_public = run('awg', 'show', args.interface, 'public-key')
    if not stored.get('privatekey') or run('awg', 'pubkey', input_text=stored['privatekey'] + '\n') != server_public:
        raise ValueError('the server config does not belong to the running AWG interface')
    if any(address.ip in network for network in stored_peers + active_peers):
        raise ValueError(f'{address.ip} overlaps an existing peer AllowedIPs range')
    if stored.get('address'):
        server_addresses = [ipaddress.ip_interface(item.strip()) for item in stored['address'].split(',')]
        if address.ip in [item.ip for item in server_addresses]:
            raise ValueError('client address is already assigned to the server')
        networks = [item.network for item in server_addresses
                    if item.version == address.version and item.network.prefixlen < item.max_prefixlen]
        if networks and not any(address.ip in network for network in networks):
            raise ValueError('client address is outside the server tunnel networks')

    private = run('awg', 'genkey')
    public = run('awg', 'pubkey', input_text=private + '\n')
    psk = run('awg', 'genpsk')
    client = ['[Interface]', f'PrivateKey = {private}', f'Address = {address}']
    if dns:
        client.append(f'DNS = {dns}')
    if stored.get('mtu'):
        client.append(f'MTU = {stored["mtu"]}')
    client.extend(f'{key} = {active[key.lower()]}' for key in AWG_SETTINGS if key.lower() in active)
    client.extend(('', '[Peer]', f'PublicKey = {server_public}', f'PresharedKey = {psk}',
                   f'AllowedIPs = {routes}', f'Endpoint = {args.endpoint}', 'PersistentKeepalive = 25', ''))
    peer = f'\n\n[Peer]\nPublicKey = {public}\nPresharedKey = {psk}\nAllowedIPs = {address}\n'

    with staged_file(output, '\n'.join(client).encode()) as profile_path, \
            staged_file(server_path, (stored_text.rstrip() + peer).encode()) as new_server, \
            staged_file(CONFIG_DIR / 'client.psk', (psk + '\n').encode()) as psk_path:
        os.link(profile_path, output)  # Publish exclusively; never overwrite another client profile.
        command = ('awg', 'set', args.interface, 'peer', public)
        try:
            run(*command, 'preshared-key', str(psk_path), 'allowed-ips', str(address))
            if server_path.read_text() != stored_text:
                raise ValueError('server config changed during provisioning; retry')
            os.replace(new_server, server_path)
        except BaseException:
            try:
                run(*command, 'remove')
            except (OSError, subprocess.CalledProcessError) as error:
                raise ValueError(f'could not roll back peer {public}; check {args.interface}. Profile kept at {output}') from error
            output.unlink(missing_ok=True)
            raise
    print(f'Created {output} and registered {address} on {args.interface}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='role', required=True)
    ingress = subparsers.add_parser('ingress', help='configure the public entry relay')
    ingress.add_argument('--remote-host', required=True, type=hostname, help='egress TLS hostname')
    egress = subparsers.add_parser('egress', help='configure the relay next to the VPN server')
    egress.add_argument('--fullchain', required=True, type=Path)
    egress.add_argument('--privkey', required=True, type=Path)
    for role in (ingress, egress):
        role.add_argument('--wss-port', type=port, default=443)
        role.add_argument('--awg-port', type=port, default=51821)
    client = subparsers.add_parser('client', help='create a client profile and register its peer')
    client.add_argument('--interface', required=True)
    client.add_argument('--server-config', required=True, type=Path)
    client.add_argument('--address', required=True, help='unused tunnel address, such as 10.8.0.2/32')
    client.add_argument('--endpoint', required=True, type=endpoint, help='public ingress address with UDP port')
    client.add_argument('--output', required=True, type=Path)
    client.add_argument('--allowed-ips', default='0.0.0.0/0')
    client.add_argument('--dns')
    args = parser.parse_args()

    if sys.platform != 'linux':
        parser.exit(1, 'Run on the Linux VPS.\n')
    if os.geteuid() != 0:
        parser.exit(1, 'Run with sudo.\n')
    import fcntl

    os.umask(0o077)
    try:
        with (CONFIG_DIR / '.configure.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.role == 'client':
                create_client(args)
            else:
                configure_relay(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Configuration failed: {error}\n')


if __name__ == '__main__':
    main()
