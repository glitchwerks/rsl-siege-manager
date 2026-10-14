"""Fixtures only: no Incus operations, VM, host listener or package installation."""
from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parents[1]

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name,ROOT/filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

pilot = load('local_tools','local-pilot.py')
preflight = load('local_preflight','portable-preflight.py')
fixtures = load('preflight_fixtures','tests/test_portable_preflight.py')
observer = load('local_observer','portable-pilot.py')
integration = load('local_integration','portable-integration.py')


def state():
    return {'schema':1,'owner':'12345678-1234-1234-1234-123456789abc',
            'image_fingerprint':'a'*64,'provisioned':True,'tools_ready':True}


def guest(s):
    return {'type':'virtual-machine','profiles':[pilot.NAME],'status':'Running',
            'config':{'user.rsl-pilot-owner':s['owner'],'volatile.base_image':s['image_fingerprint']},
            'expanded_config':{'limits.cpu':'2','limits.memory':'4GiB','security.guestapi':'false'},
            'expanded_devices':pilot.profile(s['owner'])['devices']}


class LocalPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=self.root/'config.json'
        self.ownership=self.root/'ownership.json'
        self.report=self.root/'report.json'
        self.cfg={'schema':1,'hostname':socket.gethostname(),'image_fingerprint':'a'*64,
                  'image_source_reviewed':True,'baseline_file':str(self.root/'baseline.json')}
        self.config.write_text(json.dumps(self.cfg));self.config.chmod(0o600)
        self.ownership.write_text(json.dumps(state()));self.ownership.chmod(0o600)
        self.args=['--config',str(self.config),'--state',str(self.ownership),'--report',str(self.report)]

    def run_main(self, phase, gate=None):
        with redirect_stdout(io.StringIO()) as output:
            code=pilot.main([phase,*self.args,*([gate] if gate else [])])
        result=json.loads(output.getvalue())
        self.assertNotIn('secret-private',output.getvalue())
        self.assertEqual(result,json.loads(self.report.read_text()))
        return code,result

    def test_each_mutation_gate_before_any_inspection(self):
        for phase in ('provision','guest-tools','tunnel','stop'):
            self.report.unlink(missing_ok=True)
            with patch.object(pilot,'check_baseline') as baseline,patch.object(pilot,'command') as command:
                code,result=self.run_main(phase)
            self.assertEqual(code,1)
            self.assertEqual(result['error'],'phase_confirmation_required')
            baseline.assert_not_called();command.assert_not_called()

    def test_plan_is_readonly_and_refuses_evidence_overwrite(self):
        with patch.object(pilot,'check_baseline'),patch.object(pilot,'command') as command:
            code,result=self.run_main('plan')
        self.assertEqual(code,0);command.assert_not_called()
        old=self.report.read_bytes()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(pilot.main(['plan',*self.args]),1)
        self.assertEqual(old,self.report.read_bytes())

    def test_wrong_host_and_unsafe_config_stop_before_baseline(self):
        self.cfg['hostname']='different-host'
        self.config.write_text(json.dumps(self.cfg))
        with patch.object(pilot,'check_baseline') as baseline:
            self.assertEqual(self.run_main('plan')[0],1)
        baseline.assert_not_called()
        self.config.chmod(0o644)
        with self.assertRaises(pilot.Stop):pilot.settings(self.config)

    def test_ownership_drift_refuses_runtime_actions(self):
        s=state()
        project={'description':'rsl-local-pilot:'+s['owner']}
        instance=guest(s)
        for mutate in ('owner','image','disk'):
            bad=copy.deepcopy(instance)
            if mutate=='owner':bad['config']['user.rsl-pilot-owner']='different'
            elif mutate=='image':bad['config']['volatile.base_image']='b'*64
            else:bad['expanded_devices']['root']['size']='160GiB'
            with patch.object(pilot,'query',side_effect=[project,bad]),self.assertRaises(pilot.Stop):
                pilot.assert_owned(s)
        with patch.object(pilot,'query',side_effect=[project,instance]):
            self.assertEqual(pilot.assert_owned(s),instance)

    def test_recovery_can_stop_owned_vm_despite_baseline_or_limit_drift(self):
        instance=guest(state());instance['expanded_config']['limits.memory']='6GiB'
        with patch.object(pilot,'query',side_effect=[{'description':'rsl-local-pilot:'+state()['owner']},instance,{'status':'Stopped'}]),patch.object(pilot,'command') as command,patch.object(pilot,'check_baseline',side_effect=pilot.Stop('host_baseline_changed')) as baseline:
            code,result=self.run_main('stop','--confirm-stop')
        self.assertEqual(code,1) # Shared-state drift remains visible after recovery.
        self.assertTrue(result['checks']['owned_guest_stopped'])
        command.assert_called_once_with(['stop',pilot.NAME,'--project',pilot.NAME,'--timeout','30'],timeout=45)
        baseline.assert_called_once()

    def test_ownership_update_is_atomic_and_preserves_old_record_on_failure(self):
        before=self.ownership.read_bytes()
        updated={**state(),'tools_ready':False}
        with patch.object(pilot.os,'replace',side_effect=OSError('fixture interruption')),self.assertRaises(OSError):
            pilot.update_state(self.ownership,updated)
        self.assertEqual(self.ownership.read_bytes(),before)
        self.assertFalse(list(self.root.glob('.pilot-state-*')))
        pilot.update_state(self.ownership,updated)
        self.assertEqual(json.loads(self.ownership.read_text()),updated)
        self.assertEqual(self.ownership.stat().st_mode & 0o777,0o600)

    def test_stop_rejects_foreign_owner(self):
        with patch.object(pilot,'query',return_value={'description':'other-owner'}),patch.object(pilot,'command') as command:
            code,result=self.run_main('stop','--confirm-stop')
        self.assertEqual(code,1);command.assert_not_called()

    def test_provision_creates_only_bounded_target_and_private_journal(self):
        self.ownership.unlink()
        calls=[]
        def command(args,data=None,timeout=30):
            calls.append((args,data))
            if args[:2]==['remote','list']:
                return json.dumps({'images':{'Addr':'https://images.linuxcontainers.org'},'local':{'Addr':'unix://'}})
            if args[:2]==['image','list']:
                return json.dumps([{'fingerprint':'a'*64,'type':'virtual-machine','architecture':'x86_64','properties':{'os':'Debian','release':'13'}}])
            if args[:2]==['project','list']:return '[]'
            return ''
        report={'checks':{}}
        with patch.object(pilot,'command',side_effect=command),patch.object(pilot,'assert_owned',return_value={'status':'Running'}):
            pilot.provision(self.cfg,self.ownership,report)
        self.assertTrue(json.loads(self.ownership.read_text())['provisioned'])
        self.assertEqual(self.ownership.stat().st_mode & 0o777,0o600)
        creates=[(args,data) for args,data in calls if args[:2]==['project','create']]
        limits=json.loads(creates[0][1])['config']
        self.assertEqual(limits['limits.memory'],'4GiB')
        self.assertEqual(limits['limits.disk.pool.agent-pool'],'100GiB')
        self.assertEqual(limits['restricted.networks.access'],'agent-untrusted')
        self.assertFalse(any('delete' in args or 'network' in args or 'sudo' in args for args,data in calls))
        profile=[json.loads(data) for args,data in calls if args[:2]==['profile','edit']][0]
        self.assertEqual(profile['devices']['root']['size'],'80GiB')
        self.assertTrue(all('agent-workers' not in args for args,data in calls))

    def test_existing_project_or_unapproved_image_remote_never_mutates(self):
        for remote_ok in (True,False):
            calls=[]
            def command(args,data=None,timeout=30):
                calls.append(args)
                if args[:2]==['remote','list']:return json.dumps({'images':{'Addr':'https://images.linuxcontainers.org' if remote_ok else 'https://unknown.invalid'},'local':{'Addr':'unix://'}})
                if args[:2]==['image','list']:return json.dumps([{'fingerprint':'a'*64,'type':'virtual-machine','architecture':'x86_64','properties':{'os':'debian','release':'13'}}])
                return json.dumps([{'name':pilot.NAME}])
            with patch.object(pilot,'command',side_effect=command),self.assertRaises(pilot.Stop):
                pilot.provision(self.cfg,self.root/'new-state',{'checks':{}})
            self.assertFalse(any('create' in args or 'start' in args for args in calls))
            self.assertFalse((self.root/'new-state').exists())

    def test_failed_install_stops_only_owned_vm_preserves_state(self):
        with patch.object(pilot,'check_baseline'),patch.object(pilot,'assert_owned'),patch.object(pilot,'install_tools',side_effect=subprocess.TimeoutExpired('guest',10)),patch.object(pilot,'command') as command:
            code,result=self.run_main('guest-tools','--confirm-guest-tools')
        self.assertEqual(code,1)
        self.assertTrue(result['checks']['failed_install_guest_stopped'])
        command.assert_called_once_with(['stop',pilot.NAME,'--project',pilot.NAME,'--timeout','30'],timeout=45)
        self.assertEqual(json.loads(self.ownership.read_text()),{**state(),"tools_ready":False})

    def test_guest_tools_never_runs_host_package_manager(self):
        calls=[]
        def cmd(args,timeout=30):
            calls.append(args)
            if args==['cat','/etc/os-release']:return 'ID=debian\nVERSION_ID="13"\n'
            if args[:3]==['docker','compose','version']:return '2.26.1'
            if args[0]=='systemctl':return 'active'
            return ''
        with patch.object(pilot,'assert_owned'),patch.object(pilot,'guest_command',side_effect=cmd):
            pilot.install_tools(state(),self.ownership,{'checks':{}})
        self.assertTrue(any('apt-get' in args for args in calls))
        self.assertFalse(any('postgresql-client-17' in args for args in calls))

    def test_tunnel_is_loopback_bounded_and_never_opens_sidecar(self):
        server=MagicMock();server.__enter__.return_value=server
        def tick():
            clock[0]+=31
        clock=[0]
        server.handle_request.side_effect=tick
        with patch.object(pilot.socketserver.ThreadingTCPServer,'__new__',return_value=server) as constructor,patch.object(pilot,'assert_owned',return_value={'status':'Running'}),patch.object(pilot.time,'monotonic',side_effect=lambda:clock[0]):
            report={'checks':{}}
            pilot.tunnel(state(),30,report)
        self.assertEqual(constructor.call_args.args[1],('127.0.0.1',18443))
        self.assertIn('("127.0.0.1",443)',pilot.RELAY)
        self.assertNotIn('8001',pilot.RELAY)
        self.assertTrue(report['checks']['bounded_tunnel_closed'])

    def test_pipe_relay_preserves_large_bidirectional_bytes(self):
        # Unix socketpair + a private echo child only; no listener or Incus call.
        caller,endpoint=socket.socketpair()
        self.addCleanup(caller.close);self.addCleanup(endpoint.close)
        child=subprocess.Popen([sys.executable,'-u','-c','import os\nwhile True:\n d=os.read(0,4096)\n if not d: break\n os.write(1,d)'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
        processes=set();lock=threading.Lock();errors=[]
        def worker():
            try:
                with patch.object(pilot.subprocess,'Popen',return_value=child):
                    pilot.relay(endpoint,processes,lock)
            except Exception as error:errors.append(type(error).__name__)
        thread=threading.Thread(target=worker);thread.start()
        payload=bytes(range(256))*1024
        sender=threading.Thread(target=lambda:caller.sendall(payload));sender.start()
        caller.settimeout(10);received=b''
        try:
            while len(received)<len(payload):
                chunk=caller.recv(65536)
                self.assertTrue(chunk,"relay ended before all bytes were delivered")
                received+=chunk
        finally:
            caller.close();sender.join(5);thread.join(10)
            if child.poll() is None:child.kill();child.wait()
        self.assertEqual(received,payload);self.assertFalse(errors);self.assertFalse(thread.is_alive())

    def test_ca_is_explicit_private_and_does_not_change_default_trust(self):
        ca=self.root/'ca.pem';ca.write_text('-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----');ca.chmod(0o600)
        context=MagicMock()
        with patch.object(pilot.ssl,'create_default_context',return_value=context):
            self.assertIs(pilot.tls_context(ca),context)
        context.load_verify_locations.assert_called_once()
        ca.write_text('-----BEGIN PRIVATE KEY-----\nsecret-private')
        with self.assertRaises(pilot.Stop):pilot.tls_context(ca)
        with self.assertRaises(pilot.Stop):pilot.tls_context(None)

    def test_local_preflight_is_optin_and_rejects_nonloopback_ports(self):
        model=fixtures.model()
        model['services']['mom']=model['services'].pop('bot')
        model['services']['migrate-mom']=copy.deepcopy(model['services']['migrate-siege'])
        # The complete real-render CI below supplies a valid mom model. Here only
        # origin/TLS boundary checks are evaluated independently of unrelated checks.
        proxy=model['services']['proxy'];proxy['environment']['PUBLIC_HOST']='localhost'
        for port in proxy['ports']:port['host_ip']='127.0.0.1'
        for service in ('backend','migrate-siege'):
            env=model['services'][service]['environment']
            env.update(DISCORD_REDIRECT_URI='https://localhost:18443/api/auth/callback',ALLOWED_ORIGINS='https://localhost:18443',DAY_ROLE_SYNC_ENABLED='false')
        self.assertFalse(preflight.validate(model,'mom')['public_hostname'])
        checks=preflight.validate(model,'mom',local_pilot=True)
        self.assertTrue(checks['public_hostname']);self.assertTrue(checks['oauth_public_origin'])
        self.assertTrue(checks['local_pilot_loopback_ports'])
        proxy['ports'][0]['host_ip']='0.0.0.0'
        self.assertFalse(preflight.validate(model,'mom',local_pilot=True)['local_pilot_loopback_ports'])

    def test_local_bind_file_must_match_reviewed_alternative(self):
        (self.root/'Caddyfile').write_bytes((preflight.PORTABLE/'local/Caddyfile').read_bytes())
        (self.root/'init-databases.sql').write_bytes((preflight.PORTABLE/'init-databases.sql').read_bytes())
        self.assertTrue(preflight.deployment_bind_sources_current(self.root,True))
        self.assertFalse(preflight.deployment_bind_sources_current(self.root))

    def test_real_ca_load_is_scoped_to_client_context(self):
        import ssl
        ca=self.root/'fixture-ca.pem'
        key=self.root/'fixture-key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                        '-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost',
                        '-keyout',str(key),'-out',str(ca),'-days','1'],
                       check=True,capture_output=True,timeout=15)
        ca.chmod(0o600)
        original=ssl.create_default_context().cert_store_stats()
        context=pilot.tls_context(ca)
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.cert_store_stats()['x509_ca'],original['x509_ca']+1)
        self.assertEqual(ssl.create_default_context().cert_store_stats(),original)

    def test_local_observer_origin_is_explicit_and_context_is_cleared(self):
        observer_fixtures=load('observer_fixtures','tests/test_portable_pilot.py')
        cfg=observer_fixtures.config(self.root);cfg['origin']='https://localhost';cfg['project']=pilot.NAME
        path=self.root/'observer.json';path.write_text(json.dumps(cfg));path.chmod(0o600)
        with self.assertRaises(observer.Stop):observer.settings(path)
        self.assertEqual(observer.settings(path,True)['origin'],'https://localhost')
        context=MagicMock()
        def observe(cfg,report):
            self.assertIs(observer.TLS_CONTEXT,context)
            report['result']='PASS'
        with patch.object(observer.local,'tls_context',return_value=context),patch.object(observer,'observe',side_effect=observe),redirect_stdout(io.StringIO()):
            code=observer.main(['--config',str(path),'--report',str(self.report),
                                '--confirm-readonly-pilot','--local-pilot','--ca-file',str(self.root/'ca.pem')])
        self.assertEqual(code,0);self.assertIsNone(observer.TLS_CONTEXT)

    def test_render_overlay_order_and_local_only_origins(self):
        completed=MagicMock(returncode=0,stdout='{}')
        with patch.object(preflight.subprocess,'run',return_value=completed) as run:
            preflight.render('mom',self.config,self.root,True)
        args=run.call_args.args[0]
        self.assertLess(args.index(str(preflight.PORTABLE/'local/compose.yml')),args.index('--profile'))
        with self.assertRaises(integration.Stop):integration.origin('https://localhost')
        self.assertEqual(integration.origin('https://localhost',local_pilot=True),'https://localhost')
        for target in ('https://127.0.0.1','https://localhost:18443','https://remote.fixture-domain.net'):
            with self.assertRaises(integration.Stop):integration.origin(target,local_pilot=True)


@unittest.skipUnless(os.environ.get('PORTABLE_COMPOSE_TEST')=='1','disposable CI Compose/Caddy only')
class ActualLocalRender(unittest.TestCase):
    def test_local_overlay_and_caddy(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);stack=fixtures.fixture(root)
            stack.write_text(stack.read_text().replace('COMPOSE_PROJECT_NAME=siege-pilot','COMPOSE_PROJECT_NAME=rsl-siege-pilot'))
            (root/'Caddyfile').write_bytes((preflight.PORTABLE/'local/Caddyfile').read_bytes())
            model=preflight.render('mom',stack,root,True)
            approval=preflight.read_mom_approval(root/'runtime/mom-image.json')
            checks=preflight.validate(model,'mom',approval,True)
            self.assertTrue(all(checks.values()),[key for key,value in checks.items() if not value])
            self.assertFalse(preflight.validate(model,'mom',approval)['public_hostname'])
            self.assertTrue(preflight.deployment_bind_sources_current(root,True))
        result=subprocess.run(['docker','run','--rm','--network','none',
            '-e','ROLE_SYNC_UPSTREAM=mom:8001','-e','ROLE_SYNC_PATH=/api/internal/role-sync',
            '-v',str(preflight.PORTABLE/'local/Caddyfile')+':/etc/caddy/Caddyfile:ro',
            'caddy:2','caddy','adapt','--config','/etc/caddy/Caddyfile'],capture_output=True,text=True,check=True,timeout=120)
        config=json.loads(result.stdout)
        self.assertIn('internal',json.dumps(config['apps']['tls']['automation']['policies']))
        self.assertNotIn('acme',json.dumps(config['apps']['tls']['automation']['policies']))


if __name__=='__main__':unittest.main()
