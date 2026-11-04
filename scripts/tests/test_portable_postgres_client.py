"""Guard against routing the backup client into an unintended DB or restore write."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

spec = importlib.util.spec_from_file_location('client', Path(__file__).parents[1] / 'portable-postgres-client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.services = self.root / 'services'
        self.services.write_text(''.join(f'[{app}]\nhost=/var/run/postgresql\nport=5432\nsslmode=disable\ndbname={db}\nuser={role}\n' for app, db, role in [('siege', 'siege', 'siege_app'), ('mom', 'mom_bot', 'mom_app')]))
        self.cfg = {'services': self.services, 'state': self.root}

    def test_role_mapping_and_mutation_flags_refused(self):
        with patch.dict(os.environ, PGSERVICEFILE=str(self.services)):
            args, stream = client.arguments(self.cfg, 'psql', ['--no-password', '--dbname=service=mom'])
            self.assertIn('--username=mom_app', args)
            self.assertIsNone(stream)
            for flags in [['--dbname=service=postgres'], ['--dbname=service=mom', '-c', 'DROP DATABASE siege'], ['--dbname=service=mom', '--username=postgres']]:
                with self.assertRaises(ValueError):
                    client.arguments(self.cfg, 'psql', flags)

    def test_restore_cannot_connect_or_write(self):
        for flags in [['--dbname=service=siege', '--clean'], ['--create'], ['--list', '/etc/passwd']]:
            with self.assertRaises(ValueError):
                client.arguments(self.cfg, 'pg_restore', flags)

    def test_private_managed_archive_only(self):
        archive = self.root / 'runs' / str(uuid.uuid4()) / 'source' / 'siege.dump'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b'PGDMP')
        archive.chmod(0o600)
        self.assertEqual(client.arguments(self.cfg, 'pg_restore', ['--list', str(archive)]), (['--list'], archive))
        archive.chmod(0o644)
        with self.assertRaises(ValueError):
            client.arguments(self.cfg, 'pg_restore', ['--list', str(archive)])

    def test_source_role_or_ambient_service_change_refused(self):
        with patch.dict(os.environ, PGSERVICEFILE='/unreviewed'):
            with self.assertRaises(ValueError):
                client.arguments(self.cfg, 'pg_dump', ['--dbname=service=siege'])
        self.services.write_text(self.services.read_text().replace('user=siege_app', 'user=postgres'))
        with patch.dict(os.environ, PGSERVICEFILE=str(self.services)):
            with self.assertRaises(ValueError):
                client.arguments(self.cfg, 'pg_dump', ['--dbname=service=siege'])
