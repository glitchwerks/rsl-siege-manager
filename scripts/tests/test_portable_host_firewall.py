"""Check the firewall's access preservation and cleanup scope without host changes."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).parents[2] / 'deploy/portable/host/firewall.py'


class FirewallTests(unittest.TestCase):
    def invoke(self, mode):
        calls = []
        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 1 if '-C' in args else 0, b'', b'')
        with patch('sys.argv', [str(SCRIPT), mode]), patch('subprocess.run', side_effect=fake_run), redirect_stdout(io.StringIO()):
            runpy.run_path(str(SCRIPT), run_name='__main__')
        return calls

    def test_apply_preserves_ssh_established_and_ipv6_control(self):
        calls = self.invoke('apply')
        for binary in ['/usr/sbin/iptables', '/usr/sbin/ip6tables']:
            selected = [x for x in calls if x[0] == binary]
            self.assertTrue(any('--dports' in x and '22,80,443' in x for x in selected))
            self.assertTrue(any('ESTABLISHED,RELATED' in x for x in selected))
            self.assertTrue(any('RSL-HOST' in x and x[-1] == 'DROP' for x in selected))
        self.assertTrue(any('ipv6-icmp' in x for x in calls))
        self.assertFalse(any('-P' in x for x in calls))

    def test_public_container_rules_check_original_destination(self):
        calls = self.invoke('apply')
        for port in ['80', '443']:
            self.assertTrue(any('--ctorigdstport' in x and port in x for x in calls))
        self.assertTrue(any('DOCKER-USER' in x and 'eth0' in x and 'RSL-CONTAINERS' in x for x in calls))

    def test_remove_flushes_only_own_chains(self):
        calls = self.invoke('remove')
        for args in calls:
            if '-F' in args or '-X' in args:
                self.assertIn(args[-1], {'RSL-HOST', 'RSL-CONTAINERS'})
        self.assertFalse(any('-P' in x for x in calls))
