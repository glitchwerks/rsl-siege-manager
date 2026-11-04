#!/usr/bin/env python3
"""Read-only PostgreSQL 16 client transport for an explicitly configured local Docker pilot.

Invoked by psql/pg_dump/pg_restore launchers. No database mutations, network mounts,
registry access or application lifecycle operations. Docker access remains privileged.
"""
import configparser
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

DOCKER = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']


def require(value):
    if not value:
        raise ValueError('client_transport_refused')


def private(path, directory=False):
    path = Path(path)
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)))
    s = path.stat()
    require(s.st_uid == os.geteuid() and not s.st_mode & 0o077
            and (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)))
    return path


def settings(path):
    path = private(path)
    private(path.parent, True)
    cfg = json.loads(path.read_text())
    require(set(cfg) == {'project', 'image', 'services', 'state'})
    require(re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', cfg['project']))
    require(re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', cfg['image']))
    cfg['services'] = private(cfg['services'])
    cfg['state'] = private(cfg['state'], True)
    return cfg


def arguments(cfg, program, args):
    require(program in {'psql', 'pg_dump', 'pg_restore'})
    if args == ['--version']:
        return args, None
    if program == 'pg_restore':
        require(len(args) == 2 and args[0] == '--list')
        p = private(args[1])
        require(p.name in {'siege.dump', 'mom.dump'} and p.parent.name == 'source'
                and p.parent.parent.parent == cfg['state'] / 'runs')
        require(str(uuid.UUID(p.parent.parent.name)) == p.parent.parent.name)
        return ['--list'], p
    allowed = {'--no-password', '--no-psqlrc', '--quiet', '--tuples-only', '--no-align',
               '--set=ON_ERROR_STOP=1'} if program == 'psql' else {
                   '--no-password', '--format=custom', '--no-acl', '--lock-wait-timeout=10000'}
    mapped = []
    services = configparser.ConfigParser(interpolation=None)
    services.read(cfg['services'])
    require(services.sections() == ['siege', 'mom'])
    selected = []
    for arg in args:
        if arg.startswith('--dbname=service='):
            app = arg.removeprefix('--dbname=service=')
            require(app in {'siege', 'mom'})
            selected.append(app)
            v = services[app]
            require(v['host'] == '/var/run/postgresql' and v['port'] == '5432'
                    and v['sslmode'] == 'disable'
                    and v['dbname'] == {'siege': 'siege', 'mom': 'mom_bot'}[app]
                    and v['user'] == {'siege': 'siege_app', 'mom': 'mom_app'}[app])
            mapped += ['--host=/var/run/postgresql', '--dbname=' + v['dbname'], '--username=' + v['user']]
        else:
            require(arg in allowed or (program == 'pg_dump' and
                    re.fullmatch(r'--snapshot=[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+', arg)))
            mapped.append(arg)
    require(len(selected) == 1 and os.environ.get('PGSERVICEFILE') == str(cfg['services']))
    return mapped, None


def source(cfg):
    r = subprocess.run(DOCKER + ['ps', '-q', '--filter', 'label=com.docker.compose.project=' + cfg['project'],
        '--filter', 'label=com.docker.compose.service=postgres'], capture_output=True, text=True, timeout=15)
    require(r.returncode == 0)
    ids = r.stdout.splitlines()
    require(len(ids) == 1 and re.fullmatch('[0-9a-f]{12,64}', ids[0]))
    r = subprocess.run(DOCKER + ['inspect', ids[0]], capture_output=True, text=True, timeout=15)
    require(r.returncode == 0)
    rows = json.loads(r.stdout)
    require(len(rows) == 1)
    row = rows[0]
    require(row['Config']['Image'] == cfg['image'] and row['State']['Running']
            and row['Config']['Labels']['com.docker.compose.project'] == cfg['project']
            and row['Config']['Labels']['com.docker.compose.service'] == 'postgres')
    return ids[0]


def main(argv):
    try:
        require(len(argv) >= 3)
        cfg = settings(argv[0])
        args, archive = arguments(cfg, argv[1], argv[2:])
        container = source(cfg)
        options = os.environ.get('PGOPTIONS', '') + ' -c default_transaction_read_only=on'
        cmd = DOCKER + ['exec', '-i', '-e', 'PGOPTIONS=' + options, container, argv[1], *args]
        if archive:
            with archive.open('rb') as data:
                return subprocess.run(cmd, stdin=data).returncode
        os.execv(cmd[0], cmd)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print('client_transport_refused', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
