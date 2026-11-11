# GitHub Actions to VPS access gate

The production VPS already uses ordinary OpenSSH over Tailscale. The reviewed
deployment directory is `/srv/rsl-siege`; the installed production backup timer
is `rsl-production-backup.timer` (the repository template is named differently).
The workflow in this change is **manual and read-only**. It does not deploy,
install a runner, modify SSH, run backups or migrations, or change Compose.

## Protection and route

Use the separate `vps-prod` GitHub environment in the public Siege Manager
repository. It must require an operator review and allow only the `main` branch.
Do not rely on the existing `prod` environment: it has no approval rules. Keep
the same `vps-production-promotion` concurrency group for future promotion
jobs, so an access check cannot overlap a release.

The workflow uses a GitHub-hosted Ubuntu runner. The pinned Tailscale action
joins it as an ephemeral node with only `tag:github-deploy`; the tailnet policy
must grant that tag TCP 22 to the one VPS and no NAS, subnet, or other host access.
Preserve the current desktop-to-VPS and VPS-to-NAS grants when making that
separate tailnet change. Create a Tailscale OAuth client limited to generating
ephemeral auth keys for that tag. Review the current tailnet policy, device
identity, provider console recovery, and rollback before applying the grant.

In the `vps-prod` environment, configure these values after reviewing the
target. Do not place them in repository files or PR comments:

| Type | Name | Meaning |
| --- | --- | --- |
| Variable | `VPS_SSH_USER` | Existing approved login; initially `root` only if its restricted key is reviewed |
| Secret | `VPS_TAILSCALE_HOST` | Verified VPS tailnet IPv4 or MagicDNS name |
| Secret | `VPS_SSH_HOST_FINGERPRINT` | Trusted ED25519 or ECDSA P-256 host-key fingerprint from the provider console |
| Secret | `TAILSCALE_OAUTH_CLIENT_ID` | Tailnet OAuth client ID |
| Secret | `TAILSCALE_OAUTH_SECRET` | Tailnet OAuth client secret |
| Secret | `VPS_DEPLOY_SSH_KEY` | Dedicated passphrase-free ED25519 private key for the forced command; currently a selected organization Actions secret |
| Secret | `VPS_SSH_KNOWN_HOSTS` | One `ssh-ed25519` or `ecdsa-sha2-nistp256` known-hosts entry for the exact tailnet host |

The private key must be new and dedicated to this workflow, not the operator's
existing admin key. Before adding its public half to the VPS, preserve the
existing `authorized_keys`, current login, host-key fingerprint, SSH service and
provider-console recovery. Install the reviewed `portable-vps-remote.py` at
`/opt/rsl-siege-manager/scripts/portable-vps-remote.py` under root ownership.
Append exactly one new key entry using the OpenSSH `restrict` option and a forced
command to that absolute script path. Do not replace existing keys or change
`sshd_config`, the firewall, Tailscale SSH mode, or public SSH in this phase.

The entry's configuration shape is:

```text
restrict,command="/usr/bin/python3 /opt/rsl-siege-manager/scripts/portable-vps-remote.py" ssh-ed25519 <dedicated-public-key> github-vps-deploy
```

Use `scripts/portable-vps-host-access.py` from a reviewed checkout staged on the
VPS to perform the host change. Keep a separate, existing admin SSH session
open and confirm provider-console recovery first. The script requires root,
an existing private `/root/.ssh/authorized_keys`, and the reviewed remote
script beside it. It refuses to replace an existing remote script or deploy
key. Each invocation writes one root-private JSON report at the specified
new path; reports contain booleans only, not keys or host data. The dedicated
**public** key is committed at `deploy/portable/host/github-vps-deploy.pub`;
its private half is supplied through the selected organization Actions secret.
An environment or repository secret of the same name would override it.
When `/opt` is root-owned and protected but the dedicated destination does not
exist, inspect accepts that baseline and install creates only
`/opt/rsl-siege-manager` and its `scripts` child under root ownership. An
existing symlink or writable directory fails closed. Rollback leaves the
new directories and script as inert evidence after removing the key.

Run the phases separately, using a distinct report path for each:

```text
sudo python3 scripts/portable-vps-host-access.py inspect --report /root/vps-access-inspect.json
sudo python3 scripts/portable-vps-host-access.py install --public-key deploy/portable/host/github-vps-deploy.pub --confirm install --report /root/vps-access-install.json
sudo python3 scripts/portable-vps-host-access.py verify --public-key deploy/portable/host/github-vps-deploy.pub --report /root/vps-access-verify.json
```

Inspect must pass before install. Install preserves an exact root-private
`authorized_keys.before-github-deploy.*` copy, writes the reviewed script, and
atomically appends the restricted key. Verify checks the exact entry, script
contents, local read-only baseline, and rejection of arbitrary commands.
Verify the original admin login still works in a **new** session before closing
the old one. If the new key must be revoked, run the separately confirmed
`rollback` phase with the same public key and a new report path; it removes
only that exact key entry and retains the backup and script for inspection.

The forced command currently accepts only the literal `access-check` request.
It checks the protected directories, Docker access, and active backup timer,
then returns `PASS` or `STOP`. Timer activity only confirms scheduling; release
promotion must separately verify recent successful backup and recovery evidence.
An arbitrary SSH command must fail. Install and
key activation are a separately reviewed host change; first verify the existing
admin connection remains available, then dispatch the access-check workflow
from `main` and approve the `vps-prod` job. Preserve its sanitized report. Also
verify the desktop and NAS paths remain healthy.

If the new path fails, remove only the new authorized-key entry and revoke only
the new OAuth client/grant. Restore the preserved `authorized_keys` copy if its
contents changed unexpectedly. Keep the original admin access and provider
console available throughout. No release promotion is authorized by an access
check PASS; backup, migration and service activation need separate gates.

## Correct an already installed access command

The first production access verification exposed a template-versus-installed
timer name mismatch. From the reviewed updated checkout, use the independent
`update-script` phase with `--confirm update-script` and a new report path.
This phase requires the exact previously reviewed script and restricted key,
preserves the old script in a root-private backup beside it, and atomically
replaces only the forced-command script. It does not change the timer, SSH
configuration, authorized keys, Compose or services. Run `verify` again with a
new report path before dispatching the protected GitHub workflow. If the new
script fails, `restore-script --confirm restore-script` restores only the
preserved old script after checking both versions; use a fresh report path.

## Rotate the dedicated GitHub SSH key

The Tailscale OAuth `tskey` joins the runner to the tailnet; it is not the
OpenSSH private key used by `VPS_DEPLOY_SSH_KEY`. If that separate SSH secret is
unusable, generate a new passphrase-free ED25519 pair, validate the private
half locally with `ssh-keygen -y -P ''`, and verify that its derived public key
matches `deploy/portable/host/github-vps-deploy.pub`. Store the private half
as the selected organization Actions secret; never commit or print it. The former
public key remains in `github-vps-deploy-previous.pub` for exact matching.

From the pinned reviewed checkout, run `rotate-key` with the new
`--public-key`, `--old-public-key` pointing to the previous public file,
`--confirm rotate-key`, and a fresh root-private report path. The phase
preserves an exact `authorized_keys` backup and atomically replaces only the
matching restricted key line; all operator keys remain. Keep existing admin
SSH and provider-console recovery open, then run `verify` with the new public
key and a new report before dispatching the protected workflow. If that path
fails, `restore-key --confirm restore-key` with the same two public-key paths
reinstates only the prior restricted line, again preserving a backup. The
prior private key remains unusable for CI, so restore protects host state and
admin access while diagnosing; it is not a successful deployment route.
