#!/usr/bin/env python3
"""Read-only host baseline/recheck for the proposed isolated Incus pilot.

No runtime initialization, network probes, cloud credentials or infrastructure
mutation. The only write is a fresh private sanitized evidence file.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess


class Stop(Exception):
    pass


def require(value):
    if not value:
        raise Stop()


def command(args):
    if args[0] == "incus":
        args = ["incus", "--force-local", *args[1:]]
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
    require(result.returncode == 0 and len(result.stdout) <= 2 * 1024 * 1024)
    return result.stdout


def active(unit, user=False):
    args = ["systemctl", *(["--user"] if user else []), "is-active", unit]
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
    return result.returncode == 0 and result.stdout.strip() == "active"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def inspect():
    memory = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in {'MemTotal', 'MemAvailable'}:
            memory[key] = int(value.split()[0]) * 1024
    disk = os.statvfs('/var/lib/incus')
    available = disk.f_bavail * disk.f_frsize
    services = ('incus', 'ssh', 'NetworkManager', 'systemd-resolved', 'halo-lp', 'lemond', 'xrdp')
    checks = {f'{name}_running': active(name) for name in services}
    checks['user_podman_socket_running'] = active('podman.socket', user=True)
    checks['memory_reserve'] = memory['MemAvailable'] >= 16 * 1024 ** 3
    checks['disk_reserve'] = available >= 120 * 1024 ** 3
    instances = json.loads(command(['incus', 'list', '--all-projects', '--format', 'json']))
    existing = [{k:row.get(k) for k in ('name', 'project', 'status', 'type', 'profiles')}
                for row in instances if row.get('project') != 'rsl-siege-pilot']
    for row, source in zip(existing, [r for r in instances if r.get('project') != 'rsl-siege-pilot']):
        row['limits'] = {k:v for k,v in source.get('expanded_config', {}).items() if k in ('limits.cpu', 'limits.memory', 'security.guestapi', 'security.nesting')}
        row['devices'] = {name:{k:v for k,v in device.items() if k in ('type', 'path', 'pool', 'size', 'network', 'nictype', 'parent', 'source', 'security.port_isolation', 'security.ipv4_filtering')} for name,device in source.get('expanded_devices', {}).items()}
    existing.sort(key=lambda row: (row.get('project') or '', row['name']))
    routes = json.loads(command(['ip', '-json', '-4', 'route', 'show']))
    routes.sort(key=lambda row: json.dumps(row, sort_keys=True))
    bridge = json.loads(command(['incus', 'query', '/1.0/networks/agent-untrusted']))
    selected = {key: value for key, value in bridge.get('config', {}).items()
                if key.startswith(('ipv4.', 'ipv6.', 'security.acls'))}
    acl = json.loads(command(['incus', 'query', '/1.0/network-acls/agent-untrusted']))
    rules = {direction:[{key:value for key,value in row.items() if key in
             ('action', 'state', 'protocol', 'source', 'destination', 'source_port', 'destination_port')}
             for row in acl.get(direction, [])] for direction in ('ingress', 'egress')}
    checks['bridge_policy_present'] = selected.get('security.acls') == 'agent-untrusted'
    checks['default_ingress_rejected'] = selected.get('security.acls.default.ingress.action') == 'reject'
    return {'checks':checks, 'memory_total_bytes':memory['MemTotal'],
            'memory_available_bytes':memory['MemAvailable'], 'storage_available_bytes':available,
            'existing_instance_count':len(existing),
            'existing_instances_sha256':digest(existing), 'host_routes_sha256':digest(routes),
            'bridge_policy_sha256':digest({'config':selected, 'rules':rules})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    report = {'schema':1, 'phase':'local-pilot-readiness', 'timestamp':datetime.now(timezone.utc).isoformat(),
              'result':'STOP', 'activation_approved':False, 'checks':{}}
    try:
        parent = args.report.parent.lstat()
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.geteuid() and not parent.st_mode & 0o077)
        if args.baseline:
            require(args.report.resolve() != args.baseline.resolve())
        with os.fdopen(os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as output:
            json.dump({'result':'INCOMPLETE', 'activation_approved':False}, output)
            output.flush()
            os.fsync(output.fileno())
            try:
                report.update(inspect())
                if args.baseline:
                    meta = args.baseline.lstat()
                    require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                            and not meta.st_mode & 0o077 and meta.st_size <= 65536)
                    with os.fdopen(os.open(args.baseline, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
                        meta = os.fstat(source.fileno())
                        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                                and not meta.st_mode & 0o077 and meta.st_size <= 65536)
                        raw = source.read(65537)
                        require(len(raw) <= 65536)
                    baseline = json.loads(raw)
                    require(baseline.get('schema') == 1 and baseline.get('phase') == report['phase']
                            and baseline.get('result') == 'PASS')
                    for key in ('existing_instances_sha256', 'host_routes_sha256', 'bridge_policy_sha256'):
                        report['checks']['unchanged_' + key.removesuffix('_sha256')] = report[key] == baseline.get(key)
                report['result'] = 'PASS' if all(report['checks'].values()) else 'STOP'
            except (Stop, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                report['error'] = 'inspection_incomplete'
            output.seek(0)
            output.truncate()
            json.dump(report, output, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
    except (Stop, OSError):
        report.update(result='STOP', error='report_unavailable')
    print(json.dumps(report))
    return 0 if report['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
