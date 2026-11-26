import argparse
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import configure


PROJECT = Path(__file__).resolve().parents[1]
SERVER_CONFIG = '[Interface]\nPrivateKey = server-private\nAddress = 10.8.0.1/24\nMTU = 1280\n'
ACTIVE_CONFIG = '[Interface]\nPrivateKey = server-private\nH1 = 10-20\nHeaderProtectionKey = awg-key\n'


class ConfigureTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(configure, 'CONFIG_DIR', self.directory))

    def load_role(self, role):
        (self.directory / 'role').write_text(role)
        (self.directory / 'config.example.yaml').write_text((PROJECT / f'{role}.yaml').read_text())

    def test_ingress_writes_remote_address_and_tls_name(self):
        self.load_role('ingress')
        parameters = argparse.Namespace(role='ingress', remote_host='exit.example.net', wss_port=8443, awg_port=51822)
        config_path = self.directory / 'config.yaml'
        config_path.write_text('previous config')
        with patch.object(configure, 'read_secret', return_value='a' * 64):
            configure.configure_relay(parameters)
        config = config_path.read_text()
        self.assertIn('addr: "exit.example.net:8443"', config)
        self.assertIn('serverName: exit.example.net', config)
        self.assertIn('addr: "127.0.0.1:51822"', config)
        self.assertIn('secure: true', config)
        self.assertIn('a' * 64, config)
        self.assertEqual((self.directory / 'config.yaml.previous').read_text(), 'previous config')

    def test_egress_writes_tls_files_and_local_port(self):
        self.load_role('egress')
        cert, key = self.directory / 'input.pem', self.directory / 'input.key'
        cert.write_bytes(b'certificate')
        key.write_bytes(b'private key')
        parameters = argparse.Namespace(role='egress', fullchain=cert, privkey=key, wss_port=8443, awg_port=51822)
        with patch.object(configure, 'read_secret', return_value='a' * 64), \
                patch.object(configure, 'run', side_effect=['', 'public-key', 'public-key']):
            configure.configure_relay(parameters)
        config = (self.directory / 'config.yaml').read_text()
        self.assertIn('addr: "0.0.0.0:8443"', config)
        self.assertIn('addr: "127.0.0.1:51822"', config)
        self.assertEqual((self.directory / 'tls' / 'fullchain.pem').read_bytes(), b'certificate')
        self.assertEqual((self.directory / 'tls' / 'privkey.pem').read_bytes(), b'private key')

    def client_args(self):
        server = self.directory / 'awg0.conf'
        server.write_text(SERVER_CONFIG)
        return argparse.Namespace(interface='awg0', address='10.8.0.2/32', allowed_ips='0.0.0.0/0',
                                  dns='1.1.1.1', server_config=server, output=self.directory / 'phone.conf',
                                  endpoint='entry.example.net:51820')

    def test_client_writes_both_configs_and_registers_peer(self):
        args = self.client_args()
        outputs = [ACTIVE_CONFIG, 'server-public', 'server-public', 'client-private', 'client-public', 'psk', '']
        with patch.object(configure, 'run', side_effect=outputs) as awg:
            configure.create_client(args)
        profile = args.output.read_text()
        self.assertIn('Endpoint = entry.example.net:51820', profile)
        self.assertIn('PublicKey = server-public', profile)
        self.assertIn('HeaderProtectionKey = awg-key', profile)
        self.assertIn('PrivateKey = client-private', profile)
        self.assertNotIn('server-private', profile)
        self.assertIn('PublicKey = client-public\nPresharedKey = psk\nAllowedIPs = 10.8.0.2/32',
                      args.server_config.read_text())
        self.assertEqual(awg.call_args.args[:5], ('awg', 'set', 'awg0', 'peer', 'client-public'))

    def test_client_rejects_existing_active_route(self):
        args = self.client_args()
        active = ACTIVE_CONFIG + '\n[Peer]\nAllowedIPs = 10.8.0.0/28\n'
        with patch.object(configure, 'run', side_effect=[active, 'server-public', 'server-public']):
            with self.assertRaisesRegex(ValueError, 'overlaps'):
                configure.create_client(args)
        self.assertFalse(args.output.exists())
        self.assertEqual(args.server_config.read_text(), SERVER_CONFIG)

    def test_client_failed_registration_leaves_no_profile_or_config_change(self):
        args = self.client_args()
        failure = subprocess.CalledProcessError(1, 'awg set')
        outputs = [ACTIVE_CONFIG, 'server-public', 'server-public', 'client-private', 'client-public', 'psk', failure, '']
        with patch.object(configure, 'run', side_effect=outputs) as awg:
            with self.assertRaises(subprocess.CalledProcessError):
                configure.create_client(args)
        self.assertFalse(args.output.exists())
        self.assertEqual(args.server_config.read_text(), SERVER_CONFIG)
        awg.assert_called_with('awg', 'set', 'awg0', 'peer', 'client-public', 'remove')

    def test_client_retains_profile_if_live_peer_cannot_be_removed(self):
        args = self.client_args()
        failure = subprocess.CalledProcessError(1, 'awg set')
        outputs = [ACTIVE_CONFIG, 'server-public', 'server-public', 'client-private', 'client-public', 'psk', failure, failure]
        with patch.object(configure, 'run', side_effect=outputs):
            with self.assertRaisesRegex(ValueError, 'could not roll back peer client-public'):
                configure.create_client(args)
        self.assertTrue(args.output.exists())
        self.assertEqual(args.server_config.read_text(), SERVER_CONFIG)
