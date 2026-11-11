"""The host access installer preserves existing SSH keys and removes only its own."""

import importlib.util
import hashlib
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-host-access.py"
spec = importlib.util.spec_from_file_location("vps_host_access", SCRIPT)
host_access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host_access)


def test_install_then_rollback_preserves_existing_key(tmp_path):
    ssh = tmp_path / "root" / ".ssh"
    ssh.mkdir(parents=True)
    authorized = ssh / "authorized_keys"
    original = b"ssh-ed25519 EXISTING operator\n"
    authorized.write_bytes(original)
    source = tmp_path / "source.py"
    source.write_text("print('STOP')\n")
    destination = tmp_path / "opt" / "scripts" / "portable-vps-remote.py"
    destination.parent.mkdir(parents=True)
    key = "ssh-ed25519 DEDICATED github-vps-deploy"
    with mock.patch.object(host_access, "AUTHORIZED_KEYS", authorized), mock.patch.object(
        host_access, "REMOTE_SCRIPT", destination
    ), mock.patch.object(host_access, "SOURCE_SCRIPT", source), mock.patch.object(
        host_access, "preflight", side_effect=lambda: {"baseline": True}
    ), mock.patch.object(host_access, "checked_file", return_value=True), mock.patch.object(
        host_access, "prepare_destination", return_value=True
    ):
        with mock.patch.object(host_access, "FORCED_COMMAND", f'restrict,command="/usr/bin/python3 {destination}"'):
            installed = host_access.install(key)
            assert all(installed.values())
            assert authorized.read_bytes().startswith(original)
            assert authorized.read_bytes().splitlines()[-1] == host_access.key_entry(key).encode()
            assert destination.read_bytes() == source.read_bytes()
            assert list(ssh.glob("authorized_keys.before-github-deploy.*"))[0].read_bytes() == original
            assert not all(host_access.install(key).values())
            removed = host_access.rollback(key)
            assert all(removed.values())
            assert authorized.read_bytes() == original


def test_install_stops_on_existing_deploy_marker(tmp_path):
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    authorized = ssh / "authorized_keys"
    original = b"ssh-ed25519 EXISTING operator-github-vps-deploy\n"
    authorized.write_bytes(original)
    destination = tmp_path / "scripts" / "portable-vps-remote.py"
    destination.parent.mkdir()
    source = tmp_path / "source.py"
    source.write_text("print('STOP')\n")
    with mock.patch.object(host_access, "AUTHORIZED_KEYS", authorized), mock.patch.object(
        host_access, "REMOTE_SCRIPT", destination
    ), mock.patch.object(host_access, "SOURCE_SCRIPT", source), mock.patch.object(
        host_access, "preflight", return_value={"baseline": True}
    ):
        checks = host_access.install("ssh-ed25519 DEDICATED github-vps-deploy")
    assert checks["no_existing_deploy_key"] is False
    assert authorized.read_bytes() == original
    assert not destination.exists()


def test_destination_path_creation_is_limited_to_missing_directories(tmp_path):
    opt = tmp_path / "opt"
    opt.mkdir()
    manager = opt / "rsl-siege-manager"
    scripts = manager / "scripts"
    with mock.patch.object(
        host_access, "destination_directories", return_value=(opt, manager, scripts)
    ), mock.patch.object(
        host_access, "protected_directory", side_effect=lambda path: path.is_dir() and not path.is_symlink()
    ):
        assert host_access.destination_path_safe()
        assert host_access.prepare_destination()
        assert manager.is_dir() and scripts.is_dir()
        assert host_access.destination_path_safe()
        scripts.rmdir()
        manager.rmdir()
        manager.symlink_to(tmp_path)
        assert not host_access.destination_path_safe()


def test_reviewed_remote_script_update_and_restore(tmp_path):
    authorized = tmp_path / "authorized_keys"
    remote = tmp_path / "portable-vps-remote.py"
    source = tmp_path / "new-remote.py"
    old_data = b"old reviewed forced command\n"
    new_data = b"new reviewed forced command\n"
    remote.write_bytes(old_data)
    source.write_bytes(new_data)
    key = "ssh-ed25519 DEDICATED github-vps-deploy"
    with mock.patch.object(host_access, "AUTHORIZED_KEYS", authorized), mock.patch.object(
        host_access, "REMOTE_SCRIPT", remote
    ), mock.patch.object(host_access, "SOURCE_SCRIPT", source), mock.patch.object(
        host_access, "OLD_REMOTE_SHA256", hashlib.sha256(old_data).hexdigest()
    ), mock.patch.object(
        host_access, "NEW_REMOTE_SHA256", hashlib.sha256(new_data).hexdigest()
    ), mock.patch.object(host_access, "preflight", side_effect=lambda: {"baseline": True}
    ), mock.patch.object(host_access, "checked_file", side_effect=lambda path: path.is_file()
    ), mock.patch.object(host_access, "private_file", side_effect=lambda path: path.is_file()
    ), mock.patch.object(host_access, "FORCED_COMMAND", f'restrict,command="/usr/bin/python3 {remote}"'):
        authorized.write_text(host_access.key_entry(key) + "\n")
        result = host_access.update_script(key)
        assert all(result.values())
        assert remote.read_bytes() == new_data
        assert host_access.script_backup_path().read_bytes() == old_data
        assert not all(host_access.update_script(key).values())
        restored = host_access.restore_script(key)
        assert all(restored.values())
        assert remote.read_bytes() == old_data
