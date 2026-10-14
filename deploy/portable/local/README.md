# Local pilot preparation: isolated Docker VM and HTTPS

This opt-in preparation supports #557/#430 and the v1.6.0 milestone. Production
Compose/Caddy/Azure definitions remain the default. No live provisioning, package
installation, certificate trust, Azure handoff, bot activation, Slack message or
schedule is performed by merging this work. Incus tooling is a local-host adapter;
the application Compose stack remains independent of a VM provider.

## What is implemented

- Separate `local/compose.yml` overlay: fixed `rsl-siege-pilot` project, mom
  topology, guest loopback ports 80/443, localhost OAuth callback on browser port
  18443, and disabled role sync. No sidecar/database port publication.
- Reviewed `local/Caddyfile`: `localhost` with internal TLS, existing private
  upstream/proxy trust and `skip_install_trust`. The internal CA private key stays
  inside the guest's proxy volume. It is never copied by these tools.
- `--local-pilot` configuration/observation/integration gates. Production rejects
  localhost. Local observation accepts only `https://localhost` and the fixed mom
  project; local integration additionally requires `http://127.0.0.1:8001` for
  sidecar access. No general private HTTP or custom-port allowance is added.
- `--ca-file` requires local mode and a private operator-owned public CA PEM file;
  it adds trust only to that process's client context. System roots, hostname
  verification and certificate verification remain enabled. No host/guest/browser
  trust store is edited. Observer context is cleared after the invocation.
- A separately confirmed foreground tunnel for host `127.0.0.1:18443` to only
  guest `127.0.0.1:443` via Incus's management channel, not a network/proxy device.
  Four connections maximum, 64 KiB per-direction queues, 15-second idle waits,
  60-second connection lifetime, a 30–1800 second session, and ownership checks
  every 15 seconds. Session cleanup can take up to about 65 additional seconds.
  Traffic stays encrypted; payloads, paths, headers and URLs are never logged.

[Caddy's local TLS](https://caddyserver.com/docs/automatic-https#local-https) uses
an internal CA; [skip-install-trust](https://caddyserver.com/docs/caddyfile/options#skip-install-trust)
prevents its automatic trust-install attempt. A loopback listener alone cannot
attest what application is behind it: approved image/proxy/guild identity review
and the existing preflight still precede testing.

## Consolidated phase runner

Prepare one protected operator-owned 0700 directory with a 0600 `setup.json`
using `setup.json.example`. Supply the actual current host name, reviewed
fingerprint for an official Debian 13 x86-64 VM image, and a fresh PASS baseline
from `scripts/local-pilot-readiness.py`. Baselines expire after one hour; refresh
before a later phase. Use one configuration directory for all invocations so
phase locks work. Never replace the directory/lock while a phase is running.

Reports reserve fresh private files before any checks/mutation and never
replace earlier evidence. Partial resources and an owner journal are preserved
on failure. All output contains fixed phase/check labels/status/time only;
UUID ownership/image references remain in a protected journal, not the report.
No secret values are required by the setup runner. Do not publish journal,
rendered Compose output, runtime files or provider credentials.

Use the single runner for each phase below, with common arguments:

```sh
python3 scripts/local-pilot.py PHASE \
  --config /protected/pilot/setup.json \
  --state /protected/pilot/ownership.json \
  --report /protected/evidence/NEW-PHASE-report.json
```

| Phase | Additional explicit flag | Scope |
| --- | --- | --- |
| `plan` | none | Validate private config and consolidated current host baseline; no resource creation |
| `provision` | `--confirm-provision` | Create only the new named restricted project/profile and 2-vCPU/4-GiB/80-GiB VM; copy pinned official image and start the empty guest |
| `guest-tools` | `--confirm-guest-tools` | Install Docker/Compose, Python, CA package, age/rclone inside the owned Debian 13 guest; verify Docker and Compose ≥2.26 |
| `tunnel` | `--confirm-tunnel --lifetime-seconds 600` | Foreground loopback tunnel only; requires tools-ready ownership and running guest |
| `stop` | `--confirm-stop` | Stop only the UUID-owned VM and preserve all disks/configuration |

Confirmation flags represent a separately reviewed operator approval for that
phase; they are not supplied by implementation CI or by a repository merge.
The provisioning image remote must be the configured `images` remote pointing
at `https://images.linuxcontainers.org`, and target `local` must be a Unix
endpoint. The runner forces local Incus transport for host operations. It refuses
an existing project, occupied tunnel port, mismatched image/ownership, extra guest
devices or changed shared host baseline. No remote SSH or arbitrary command,
port, project, storage pool or network is accepted in configuration.

The new project uses `agent-pool`, a 100-GiB project budget and existing
`agent-untrusted` network with IPv4 filtering/port isolation. Guest API and
host-path/device access are not enabled. There are no host mounts or socket
mounts, GPU/USB/PCI passthrough, new bridges, ACL edits, host runtime installation,
reboot, DNS/route/firewall or existing profile edits. Existing VMs and host
services are compared before/after phases. Partial provisioning is not adopted or
automatically deleted; preserve the ownership file and request targeted diagnosis.

Guest tools use Debian's signed distro packages; its
[Compose package includes the Docker CLI plugin](https://packages.debian.org/trixie/amd64/docker-compose/filelist).
This does not prepare the PostgreSQL 16 client prerequisites for backup/recovery;
those tools remain a separate guest preparation gate. No PG17 replacement is
silently installed. No application image, migration, token or runtime is started.
If installation fails/times out, that approval includes stopping only the owned
VM, invalidating tools-ready and preserving its disk, because guest package work
may outlive a disconnected exec session. Inspect/recover it before retrying.

Recovery stop deliberately does not require an unchanged baseline or resource
limits: it still requires the exact project/instance UUID ownership and VM type.
It stops the owned VM first, then reports shared-state drift rather than blocking
recovery. Tunnel and stop have separate locks so an open browser session cannot
prevent emergency stop. There is no force-delete, prune, snapshot deletion or
project deletion command. If ownership cannot be established, stop and diagnose;
never stop a similarly named foreign instance.

## Local application preparation and tests

After separate approval of guest database/runtime configuration, copy the
reviewed local Caddyfile as the private deployment directory's `Caddyfile` and
the standard `init-databases.sql` unchanged. Render/preflight with the existing
mom overlay plus the reviewed local overlay, using `--local-pilot`. Preflight
requires the correct bind bytes, all existing secret/auth/image checks and the
approved mom capability record; a changed file is not a bypass. The root public
Caddyfile remains production-only. Compose must support `!override` (checked
Compose version ≥2.26); do not merge the local ports with public host bindings.

Runtime activation and migrations are not part of the setup runner. Follow the
separate pilot activation/handoff plan, including zero active/terminated Azure
developer replicas and developer bot offline before local connection. Only
`siege-web-bot-dev` is the selected source; `ca-mom-bot` and production apps remain
excluded. Freeze dev deployments during the test window and provide the reverse
handoff before beginning. This PR performs no Azure commands at all. A periodic
source/gateway guard and credential-loading procedure still require review
before bot activation; this setup runner is not that guard.

Once the proxy runs, an approved guest-only certificate extraction step can copy
**only** `/data/caddy/pki/authorities/local/root.crt` from the selected proxy into
a 0600 file in the private guest operator directory. Never export CA private
keys. Compare the expected proxy/CA identity before accepting it. This extraction
is not implemented as an automatic Docker exec/copy by these tools.

Inside the guest, add `--local-pilot --ca-file /protected/pilot/root.crt` to the
observer/integration command. Their origin is `https://localhost`; browser-facing
callback/allowed origin is `https://localhost:18443`. Run the existing read-only
or full consolidated sequence with its existing additional approval flags.
The monitor wrapper forwards the same opt-in flags and still requires separate
notification approval. Do not use the tunnel's browser port as the observer's
origin or expose the private sidecar to the host/LAN.

Chromium browser trust and developer OAuth redirect registration are **not
configured here**. First API checks can run in the guest without changing host
trust. A reviewed browser-session trust method and developer OAuth callback
setup are still prerequisites for interactive login; do not disable global TLS
verification or alter the normal browser's trust database as a shortcut. These
local tests do not prove public certificate issuance or external public probing.

## Rollback and evidence

End the tunnel with its lifetime or interrupt the foreground runner; it closes
its listener and terminates only its own relay process groups. Guest relays have
a separate hard 60-second lifetime. On application recovery, stop the local bot
first; a separately confirmed `stop` phase is a fallback to stop the whole owned
pilot guest. Only after local gateway termination is established may the captured
Azure developer revision be restored through its separate approved handoff.

Preserve STOP/INCOMPLETE reports, owner journal, guest disk and test delivery/
cleanup evidence. After each phase, recheck shared host state. A fresh PASS report
means only that phase completed with shared-state checks; it is not pilot
acceptance, a token-handoff authorization or a release/cutover approval. Destructive
cleanup is not provided. Existing Azure recovery definitions remain unchanged.

Testing uses mocked Incus/package operations, synthetic credentials, a Unix
socketpair/private echo child for bidirectional backpressure, temporary fixture
certificates, and disposable CI-only Compose/Caddy validation. No live VM,
package installation, listener or host trust modification was used for testing.
