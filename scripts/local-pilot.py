#!/usr/bin/env python3
"""Explicitly gated local Incus guest preparation; never performs Azure handoff."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import socketserver
import ssl
import stat
import subprocess
import threading
import time
import uuid

NAME = 'rsl-siege-pilot'
POOL = 'agent-pool'
NETWORK = 'agent-untrusted'
ROOT = Path(__file__).resolve().parents[1]


class Stop(Exception):
    pass


def require(value, label):
    if not value:
        raise Stop(label)


def private(path, directory=False):
    meta = path.lstat()
    require((stat.S_ISDIR(meta.st_mode) if directory else stat.S_ISREG(meta.st_mode))
            and meta.st_uid == os.geteuid() and not meta.st_mode & 0o077, 'unsafe_private_path')


def read_private(path, limit=65536):
    private(path.parent, True)
    private(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
        meta = os.fstat(source.fileno())
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                and not meta.st_mode & 0o077 and meta.st_size <= limit, 'unsafe_private_path')
        raw = source.read(limit + 1)
    require(len(raw) <= limit, 'oversized_input')
    return raw


def tls_context(path):
    require(path is not None, 'local_ca_required')
    raw = read_private(path)
    require(raw.count(b'-----BEGIN CERTIFICATE-----') == 1 and b'PRIVATE KEY' not in raw, 'invalid_local_ca')
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=raw.decode('ascii'))
    return context


def command(args, data=None, timeout=30):
    result = subprocess.run(['incus', '--force-local', *args], input=data,
                            capture_output=True, text=True, timeout=timeout, check=False)
    require(result.returncode == 0 and len(result.stdout) <= 2 * 1024 ** 2, 'incus_operation_failed')
    return result.stdout


def query(path):
    return json.loads(command(['query', path]))


def settings(path):
    cfg = json.loads(read_private(path))
    require(isinstance(cfg, dict) and set(cfg) == {'schema', 'hostname', 'image_fingerprint',
            'image_source_reviewed', 'baseline_file'}, 'invalid_configuration')
    require(type(cfg['schema']) is int and cfg['schema'] == 1 and cfg['hostname'] == socket.gethostname()
            and cfg['image_source_reviewed'] is True
            and isinstance(cfg['image_fingerprint'], str)
            and re.fullmatch('[0-9a-f]{64}', cfg['image_fingerprint'])
            and isinstance(cfg['baseline_file'], str) and Path(cfg['baseline_file']).is_absolute(),
            'target_review_required')
    return cfg


def check_baseline(cfg):
    baseline = json.loads(read_private(Path(cfg['baseline_file'])))
    require(baseline.get('schema') == 1 and baseline.get('phase') == 'local-pilot-readiness'
            and baseline.get('result') == 'PASS', 'passing_baseline_required')
    spec = importlib.util.spec_from_file_location('host_readiness', ROOT / 'scripts/local-pilot-readiness.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stamp = datetime.fromisoformat(baseline['timestamp'])
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    require(0 <= age <= 3600, 'fresh_baseline_required')
    try:
        current = module.inspect()
    except (module.Stop,OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        raise Stop('host_readiness_unavailable') from None
    require(all(current['checks'].values()), 'host_readiness_failed')
    for key in ('existing_instances_sha256', 'host_routes_sha256', 'bridge_policy_sha256'):
        require(current[key] == baseline.get(key), 'host_baseline_changed')


def profile(owner):
    return {'name':NAME, 'description':'Dedicated isolated Siege pilot', 'config':{
        'limits.cpu':'2', 'limits.memory':'4GiB', 'security.guestapi':'false'}, 'devices':{
        'root':{'type':'disk', 'path':'/', 'pool':POOL, 'size':'80GiB'},
        'eth0':{'type':'nic', 'network':NETWORK, 'security.port_isolation':'true',
                'security.ipv4_filtering':'true'}}}


def assert_owned(state, strict=True):
    require(set(state) == {'schema', 'owner', 'image_fingerprint', 'provisioned', 'tools_ready'}
            and type(state['schema']) is int and state['schema'] == 1
            and re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', state.get('owner', ''))
            and re.fullmatch('[0-9a-f]{64}', state.get('image_fingerprint', ''))
            and state['provisioned'] is True, 'invalid_ownership_record')
    project = query('/1.0/projects/' + NAME)
    require(project.get('description') == 'rsl-local-pilot:' + state['owner'], 'project_ownership_changed')
    guest = query('/1.0/instances/' + NAME + '?project=' + NAME)
    require(guest.get('type') == 'virtual-machine'
            and guest.get('config', {}).get('user.rsl-pilot-owner') == state['owner'],
            'guest_ownership_changed')
    if not strict:
        return guest
    require(guest.get('profiles') == [NAME]
            and guest.get('config', {}).get('volatile.base_image') == state['image_fingerprint'], 'guest_image_changed')
    require(guest.get('expanded_config', {}).get('limits.cpu') == '2'
            and guest.get('expanded_config', {}).get('limits.memory') == '4GiB'
            and guest.get('expanded_config', {}).get('security.guestapi') == 'false', 'guest_limits_changed')
    require(guest.get('expanded_devices') == profile(state['owner'])['devices'], 'guest_devices_changed')
    return guest


def save(output, value):
    output.seek(0)
    output.truncate()
    json.dump(value, output, indent=2)
    output.write('\n')
    output.flush()
    os.fsync(output.fileno())


def update_state(path, value):
    # Never truncate the only recovery record. Atomically replace it with a
    # private fsynced record, preserving the old one on interruption/failure.
    private(path.parent, True)
    private(path)
    original = path.lstat()
    temporary = path.parent / ('.pilot-state-' + str(uuid.uuid4()))
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as output:
            save(output, value)
        current = path.lstat()
        require((current.st_dev,current.st_ino) == (original.st_dev,original.st_ino), 'ownership_file_changed')
        os.replace(temporary,path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(directory, name='.local-pilot.lock'):
    fd = os.open(directory / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid() and not meta.st_mode & 0o077, 'unsafe_lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('local_pilot_already_running') from None
        yield
    finally:
        os.close(fd)


def provision(cfg, state_path, report):
    # Remote metadata lookup only after target/source review and explicit gate.
    remotes = json.loads(command(['remote', 'list', '--format', 'json']))
    remote = remotes.get('images', {})
    require(remote.get('Addr', remote.get('addr')) == 'https://images.linuxcontainers.org'
            and remotes.get('local', {}).get('Addr', remotes.get('local', {}).get('addr', '')).startswith('unix:'), 'unapproved_image_remote')
    images = json.loads(command(['image', 'list', 'images:', cfg['image_fingerprint'], '--format', 'json']))
    require(len(images) == 1 and images[0].get('fingerprint') == cfg['image_fingerprint']
            and images[0].get('type') == 'virtual-machine' and images[0].get('architecture') == 'x86_64'
            and images[0].get('properties', {}).get('os', '').lower() == 'debian'
            and images[0].get('properties', {}).get('release') in {'13', 'trixie'}, 'unapproved_vm_image')
    projects = json.loads(command(['project', 'list', '--format', 'json']))
    require(not any(row['name'] == NAME for row in projects), 'project_already_exists')
    private(state_path.parent, True)
    with os.fdopen(os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as output:
        state = {'schema':1, 'owner':str(uuid.uuid4()), 'image_fingerprint':cfg['image_fingerprint'],
                 'provisioned':False, 'tools_ready':False}
        save(output, state)
        project_config = {'features.images':'true', 'features.profiles':'true', 'features.networks':'false',
            'features.storage.volumes':'true', 'features.storage.buckets':'false',
            'features.networks.zones':'false', 'restricted':'true', 'limits.instances':'1',
            'limits.virtual-machines':'1', 'limits.containers':'0', 'limits.cpu':'2',
            'limits.memory':'4GiB', 'limits.disk.pool.' + POOL:'100GiB', 'limits.networks':'0',
            'restricted.devices.disk':'managed', 'restricted.devices.nic':'managed',
            'restricted.devices.proxy':'block', 'restricted.devices.gpu':'block',
            'restricted.devices.pci':'block', 'restricted.devices.usb':'block',
            'restricted.devices.unix-char':'block', 'restricted.devices.unix-block':'block',
            'restricted.devices.unix-hotplug':'block', 'restricted.networks.access':NETWORK,
            'restricted.virtual-machines.lowlevel':'block', 'restricted.snapshots':'block',
            'restricted.backups':'block'}
        command(['project', 'create', NAME, '--description', 'rsl-local-pilot:' + state['owner']],
                json.dumps({'description':'rsl-local-pilot:' + state['owner'], 'config':project_config}))
        report['checks']['project_created'] = True
        command(['profile', 'create', NAME, '--project', NAME])
        command(['profile', 'edit', NAME, '--project', NAME], json.dumps(profile(state['owner'])))
        command(['image', 'copy', 'images:' + cfg['image_fingerprint'], 'local:', '--target-project', NAME, '--vm'], timeout=600)
        command(['init', cfg['image_fingerprint'], NAME, '--vm', '--project', NAME, '--profile', NAME,
                 '-c', 'user.rsl-pilot-owner=' + state['owner']], timeout=120)
        # Persist recoverable ownership before starting the guest. Partial failures
        # remain preserved; no force-delete or rollback of shared host state.
        state['provisioned'] = True
        update_state(state_path, state)
        assert_owned(state)
        command(['start', NAME, '--project', NAME], timeout=120)
        require(assert_owned(state).get('status') == 'Running', 'guest_not_running')
        report['checks']['guest_started'] = True


def guest_command(args, timeout=30):
    return command(['exec', NAME, '--project', NAME, '--mode=non-interactive', '--', *args], timeout=timeout)


def install_tools(state, state_path, report):
    assert_owned(state)
    # OS detection is data parsing, never shell-source an untrusted file.
    release = guest_command(['cat', '/etc/os-release'])
    fields = dict(line.split('=', 1) for line in release.splitlines() if '=' in line)
    require(fields.get('ID', '').strip('"') == 'debian' and fields.get('VERSION_ID', '').strip('"') == '13', 'unexpected_guest_os')
    guest_command(['env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', 'update'], timeout=300)
    guest_command(['env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', 'install', '-y', '--no-install-recommends',
                   'docker.io', 'docker-compose', 'python3', 'ca-certificates', 'age', 'rclone'], timeout=600)
    guest_command(['systemctl', 'enable', '--now', 'docker.service'])
    version = guest_command(['docker', 'compose', 'version', '--short']).strip().lstrip('v')
    require(re.fullmatch(r'2\.[0-9]+\.[0-9]+(?:[-+].*)?', version)
            and tuple(map(int, version.split('-')[0].split('+')[0].split('.')[:2])) >= (2,26), 'compose_v2_required')
    require(guest_command(['systemctl', 'is-active', 'docker.service']).strip() == 'active', 'guest_docker_unavailable')
    report['checks']['guest_tools_ready'] = True
    # Caller holds the owner-file fd; refuse aliases/symlinks and update in place.
    state['tools_ready'] = True


def pump(left_read, left_write, right_read, right_write, seconds=60):
    """Bounded simultaneous byte relay; no payload logging or unbounded queues."""
    reads = [left_read, right_read]
    writes = [right_write, left_write]
    for fd in set(reads + writes):
        os.set_blocking(fd, False)
    buffers = [b'', b'']
    closed = [False, False]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if any(closed) and not any(buffers):
            return
        waiting_reads = [fd for index,fd in enumerate(reads) if not closed[index] and len(buffers[index]) < 65536]
        waiting_writes = [fd for index,fd in enumerate(writes) if buffers[index]]
        readable,writable,_ = select.select(waiting_reads,waiting_writes,[],min(15,max(0,deadline-time.monotonic())))
        if not readable and not writable:
            return
        for index,fd in enumerate(reads):
            if fd in readable:
                try:
                    data = os.read(fd,min(4096,65536-len(buffers[index])))
                except BlockingIOError:
                    continue
                if data:
                    buffers[index] += data
                else:
                    closed[index] = True
        for index,fd in enumerate(writes):
            if fd in writable:
                try:
                    count = os.write(fd,buffers[index][:4096])
                except BlockingIOError:
                    continue
                buffers[index] = buffers[index][count:]


# Same reviewed byte pump executes inside the guest. Fixed loopback destination,
# no socket mounts, shell interpolation, arbitrary endpoint or payload logs.
RELAY = ("import os,select,signal,socket,time\n" + inspect.getsource(pump) +
         '\nsignal.alarm(60)\ns=socket.create_connection(("127.0.0.1",443),timeout=5)\n'
         'try: pump(0,1,s.fileno(),s.fileno())\nfinally: s.close()\n')


def relay(client, processes, lock):
    process = subprocess.Popen(['incus', '--force-local', 'exec', NAME, '--project', NAME,
        '--mode=non-interactive', '--', 'python3', '-u', '-c', RELAY],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    with lock:
        processes.add(process)
    try:
        pump(client.fileno(),client.fileno(),process.stdout.fileno(),process.stdin.fileno())
    finally:
        process.stdin.close()
        try:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        process.stdout.close()
        with lock:
            processes.discard(process)


def tunnel(state, lifetime, report):
    require(state['tools_ready'] is True and type(lifetime) is int and 30 <= lifetime <= 1800, 'tunnel_not_ready')
    require(assert_owned(state).get('status') == 'Running', 'guest_not_running')
    slots, processes, lock = threading.BoundedSemaphore(4), set(), threading.Lock()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                relay(self.request, processes, lock)
            except (OSError, Stop, subprocess.SubprocessError):
                pass
            finally:
                slots.release()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = False
        daemon_threads = False
        block_on_close = True

        def verify_request(self, request, address):
            return address[0] == '127.0.0.1' and slots.acquire(blocking=False)

        def handle_error(self, request, address):
            pass  # Never log encrypted bytes, headers, URLs or tracebacks.

    # Bind failure preserves the existing listener; never kill or replace it.
    with Server(('127.0.0.1',18443), Handler) as server:
        server.timeout = 0.5
        deadline, next_check = time.monotonic() + lifetime, time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                server.handle_request()
                if time.monotonic() >= next_check:
                    require(assert_owned(state).get('status') == 'Running', 'guest_not_running')
                    next_check = time.monotonic() + 15
        finally:
            with lock:
                for process in processes:
                    try:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
    report['checks']['bounded_tunnel_closed'] = True


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['plan','provision','guest-tools','tunnel','stop'])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--lifetime-seconds', type=int, default=600)
    for gate in ('provision','guest-tools','tunnel','stop'):
        parser.add_argument('--confirm-' + gate, action='store_true')
    args = parser.parse_args(argv)
    report = {'schema':1, 'phase':'local-pilot-' + args.phase,
        'timestamp':datetime.now(timezone.utc).isoformat(), 'checks':{}, 'result':'STOP',
        'azure_handoff_performed':False, 'application_activated':False}
    try:
        private(args.report.parent, True)
        with os.fdopen(os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as output:
            save(output, {**report, 'result':'INCOMPLETE'})
            try:
                require(args.phase == 'plan' or getattr(args, 'confirm_' + args.phase.replace('-', '_')), 'phase_confirmation_required')
                require(len({p.resolve() for p in (args.config,args.state,args.report)}) == 3, 'distinct_paths_required')
                cfg = settings(args.config)
                lock_name = '.local-pilot-' + args.phase + '.lock' if args.phase in {'tunnel', 'stop'} else '.local-pilot.lock'
                with locked(args.config.parent, lock_name):
                    if args.phase != 'stop':
                        check_baseline(cfg)
                        report['checks']['host_baseline_matches'] = True
                    if args.phase == 'plan':
                        report['checks']['configuration_reviewed'] = True
                    elif args.phase == 'provision':
                        provision(cfg,args.state,report)
                    else:
                        state = json.loads(read_private(args.state))
                        assert_owned(state, strict=args.phase != 'stop')
                        require(args.phase == 'stop' or state['image_fingerprint'] == cfg['image_fingerprint'], 'image_selection_changed')
                        if args.phase == 'guest-tools':
                            state['tools_ready'] = False
                            update_state(args.state,state)
                            try:
                                install_tools(state,args.state,report)
                            except (Stop,OSError,subprocess.SubprocessError,KeyboardInterrupt):
                                # The installation approval includes stopping only
                                # this owned guest on uncertain package completion.
                                assert_owned(state, strict=False)
                                command(['stop',NAME,'--project',NAME,'--timeout','30'], timeout=45)
                                report['checks']['failed_install_guest_stopped'] = True
                                raise
                            update_state(args.state,state)
                        elif args.phase == 'tunnel':
                            tunnel(state,args.lifetime_seconds,report)
                        elif args.phase == 'stop':
                            command(['stop',NAME,'--project',NAME,'--timeout','30'], timeout=45)
                            require(query('/1.0/instances/' + NAME + '?project=' + NAME).get('status') == 'Stopped', 'guest_not_stopped')
                            report['checks']['owned_guest_stopped'] = True
                    # Recheck shared state after every phase, including mutations.
                    check_baseline(cfg)
                    report['checks']['shared_state_preserved'] = True
                    report['result'] = 'PASS'
            except (Stop,OSError,ValueError,KeyError,TypeError,AttributeError,subprocess.SubprocessError,KeyboardInterrupt) as error:
                report['error'] = str(error) if isinstance(error,Stop) else 'local_pilot_failed'
            save(output,report)
    except (Stop,OSError):
        report.update(result='STOP',error='report_unavailable')
    print(json.dumps(report))
    return 0 if report['result'] == 'PASS' else 1


def main(argv=None):
    def terminated(*args):
        raise KeyboardInterrupt()
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM,terminated)
    try:
        return run(argv)
    finally:
        signal.signal(signal.SIGTERM,previous)


if __name__ == '__main__':
    raise SystemExit(main())
