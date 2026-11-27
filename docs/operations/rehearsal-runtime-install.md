# Isolated rehearsal VM runtime setup

This runbook applies only to the empty Debian 13 rehearsal VM. It does not
prepare or modify the production VPS. The operator must first have a VMware
snapshot and working console login, record the VM's Tailscale address and
console-verified ED25519 SSH host-key fingerprint privately, and run the
reviewed read-only host inspection and runtime preflight from an exact commit.
Both reports must pass. Keep an existing admin session open throughout setup.

The VM has SSH, Tailscale, NetworkManager, a desktop session, printing services,
and VMware guest tools. Docker package installation may affect firewall rules.
The setup script checks that these services, active timers, and Tailscale-owned
firewall chains survive the install. It does not use the production VPS firewall
helper, change routing, or restart SSH or Tailscale.

Run `scripts/portable-vps-rehearsal-runtime-install.py` only from the same
reviewed checkout as its imported host inspector and runtime preflight. Use a
new `/root` report filename for **each** phase, and keep all reports on the VM.
The CLI requires a literal phase confirmation, `--snapshot-confirmed`, the
expected private VM address, and the console-verified fingerprint. Do not put
these values or unsanitized host logs in a public issue. Each phase returns a
single sanitized JSON report and stops before mutation if its prerequisites
fail. A STOP after mutation requires investigation and recovery before any
retry; do not skip to a later phase.

| Phase | Change | Recovery boundary |
| --- | --- | --- |
| `prepare-repo` | Add only the exact Docker signing key and Debian trixie apt source, run `apt-get update`, and simulate the fixed Docker package install. The key fingerprint, official source of every package candidate, and absence of package removals/upgrades must pass. | `rollback-repo` can remove these exact files while Docker is absent. A partially written or changed file requires snapshot/manual review. |
| `install-runtime` | Write a bounded Docker log configuration, then install Docker Engine, CLI, containerd, Buildx, and Compose at the exact verified candidate versions from the official source. Verify Docker, Compose, zero containers, host services, timers, and Tailscale firewall ownership. | Restore the VMware snapshot if installation or post-install checks fail. Do not broadly uninstall packages or reset the firewall. |
| `prepare-paths` | Create only the new private deployment, runtime, evidence, and backup directories. Verify mode and host baseline. | If this phase fails, inspect the new paths and restore the snapshot before retrying. Do not recursively delete unknown content. |
| `rollback-repo` | Remove only the exact source and key added by `prepare-repo`, before Docker installation. | The original apt source inventory remains available in the preflight report. |

For each phase, pass `--confirm` with that phase's name. Example shape:

```sh
sudo python3 -I -B scripts/portable-vps-rehearsal-runtime-install.py prepare-repo \
  --confirm prepare-repo --snapshot-confirmed \
  --expected-ip "$REHEARSAL_VM_TAILSCALE_IP" \
  --expected-fingerprint "$REHEARSAL_VM_HOST_FINGERPRINT" \
  --report /root/rehearsal-prepare-repo-1.json
```

Use `install-runtime` and `prepare-paths` in that order with their matching
confirmations and fresh report paths. Between phases, check that SSH and
Tailscale still reach the VM through the existing private route. Preserve the
preflight, phase reports, apt transaction evidence, and snapshot identifier in
private operator records. The scripts do not start a backup, install a deploy
key, deploy application containers, or promote a release. Those have separate
gates and runbooks.
