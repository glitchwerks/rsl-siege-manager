# Bootstrap a dedicated Debian VPS

This is the host preparation phase, before application activation or migration.
Review the target, SSH recovery access, workload inventory, ports and storage
first. Do not apply it to an infrastructure host with unrelated workloads. It is
provider-neutral; do not invoke Incus provisioning for an ordinary VPS or VMware VM.
Keep Azure running and the developer bot off during preparation.

## Access and baseline

Verify the SSH ED25519 host fingerprint against the provider's trusted console
before saving a host key. Use strict checking and an explicit deployment key.
Keep the private key outside Git; do not copy an operator's Windows private key
to the server. Preserve the working login until any replacement account and its
privilege policy are explicitly approved and tested through a new connection.

Record distribution/architecture, CPU/RAM/swap, disks, routes, listening ports,
existing services/timers/containers and provider monitoring. The chosen starting
profile is Debian 13 amd64, 2 vCPU, 4 GiB RAM and an 80 GiB disk. This remains a
capacity hypothesis pending real-data/load acceptance. Provider firewall state
requires separate inspection; a host check cannot prove cloud firewall rules.

The initial VPS setup retains its existing key-only root access. Creating a named
administrator, adding sudo rights and changing SSH access are separate reviewed
changes. Do not add unrestricted passwordless sudo as an implicit dependency of
installing Docker. Never overwrite authorized keys or disable the last working
login. DigitalOcean's console remains the independent recovery path.

## Container runtime and protected paths

Follow Docker's [official Debian apt installation](https://docs.docker.com/engine/install/debian/).
Use the signed stable apt repository for `trixie`/`amd64`, not a convenience shell
installer. Install Docker Engine/CLI, containerd.io, Buildx and the Compose plugin.
Record exact installed versions, repo configuration and signing-key source in
private deployment evidence. Patch versions can differ from the test VM; repeat
Compose/preflight and runtime checks on the real host.

Before the first application starts, configure `/etc/docker/daemon.json` with
bounded JSON logs (`max-size=10m`, `max-file=3`). Preserve and review any existing
configuration rather than overwriting it. Do not disable Docker's firewall
management. Enable Docker at boot and verify daemon/Compose commands. A restricted,
networkless official `hello-world` smoke container may be run by its resolved
digest and removed afterward; this does not activate the application stack.

Install the backup clients `age` and `rclone` and verify their transport support
against the intended NAS policy. Do not copy production passwords, bot tokens or
OAuth secrets merely to complete the host phase.

Create root-owned 0700 deployment/config/evidence directories:

- `/srv/rsl-siege`, `/srv/rsl-siege/runtime`, `/srv/rsl-siege/evidence`
- `/etc/rsl-backup`, `/var/lib/rsl-backup`

Use 0600 for credential and private evidence files. Do not render Compose secrets
to public logs. Keep reviewed code separate from mutable configuration.
The environment-based `init-databases.sql` contains no passwords and must be
readable by the PostgreSQL container user (for example, mode 0644 inside the
protected deployment directory). Applying 0600 indiscriminately to that bind mount
prevents the first-boot application roles and databases from being created.

On a fresh 4 GiB host with no swap, a 2 GiB `/swapfile` reserve and
`vm.swappiness=10` can provide host headroom. Check space and existing swap/fstab
before creating it; do not replace an existing file or duplicate an fstab entry.
Restrict the file to 0600. Swap is not extra RAM or a substitute for measuring
container peaks. Record the change and its persistence explicitly.

## Host and Docker ingress

Review the [firewall helper](../../deploy/portable/host/firewall.py) before using it.
It assumes the inspected public interface is **eth0**; review/adapt it on another
host rather than assuming interface names. It is intended for the dedicated fresh
host, not an existing router or VPN gateway. Cloud firewall restrictions remain
an additional independent layer.

The helper uses iptables/ip6tables, with its own `RSL-HOST` and `RSL-CONTAINERS`
chains. Host ingress preserves loopback, established/related flows, ICMP/IPv6 ICMP,
and TCP 22/80/443; other host ingress is dropped. SSH stays on port 22 and keeps
its existing key authentication. Later narrowing SSH sources must account for
operator access and the approved private VPN path.

Docker-published traffic bypasses ordinary host INPUT filtering. A `DOCKER-USER`
jump for traffic arriving on eth0 permits established/related flows and original
TCP destination ports 80/443, dropping other new public forwarded connections.
Do not publish database or sidecar ports. The helper does not flush Docker's own
chains or change global chain policies. Review router/VPN forwarding separately
before using this policy for subnet routing. See [Docker firewall behavior](https://docs.docker.com/engine/network/packet-filtering-firewalls/).

Install the reviewed helper root-owned at `/usr/local/sbin/rsl-host-firewall`, mode
0700. Before applying it, schedule a bounded automatic rollback that invokes only
its `remove` action. Preserve an independent provider console. For example:

```sh
systemd-run --unit=rsl-firewall-rollback --on-active=3min /usr/local/sbin/rsl-host-firewall remove
/usr/local/sbin/rsl-host-firewall apply
```

Require successful rollback scheduling before application. Verify a **new** SSH
connection, IPv4/IPv6 INPUT jumps and Docker ingress rules before cancelling the
rollback timer. Do not accept only an old established session as access proof.

Persist the helper with a oneshot `rsl-host-firewall.service`:

```ini
[Unit]
Description=Siege host and Docker public ingress baseline
Requires=docker.service
After=docker.service
PartOf=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/rsl-host-firewall apply
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
```

Validate the unit, reload systemd, enable/start it and recheck a new login. Cancel
only `rsl-firewall-rollback.timer` after successful checks. The service follows
Docker restarts; no reboot or unrelated service restart is required for initial
acceptance. Preserve evidence before later rollback/removal. Do not flush the
entire firewall as cleanup.

## Private NAS and administration connectivity with Tailscale

On the VPS, use the signed stable Debian trixie apt repository from
[Tailscale's package instructions](https://pkgs.tailscale.com/stable/#debian-trixie).
Record the installed version, enable/start `tailscaled`, and enroll interactively:

```sh
tailscale up --hostname=REVIEWED_HOSTNAME --accept-dns=false --accept-routes=false --ssh=false --timeout=10s
```

An authentication timeout while awaiting the browser step is not an installation
failure. Preserve a private report, open the generated sign-in link, and approve
only the intended server in the intended tailnet. Never commit enrollment URLs,
auth keys or node state. Use a fresh sign-in URL if the original expires.

This setup preserves the VPS's existing DNS, does not accept advertised subnet
routes, does not advertise home-network routes or an exit node, and keeps ordinary
OpenSSH rather than enabling Tailscale SSH. Existing public SSH access remains
until an independent private connection and recovery path have been verified.
Tailscale's node state is sensitive and must stay protected on the server.

On both the Windows administration PC and the Windows NAS, follow the
[official Windows installer instructions](https://tailscale.com/docs/install/windows)
and sign in to the same tailnet used to approve the VPS. Windows Server 2019 is
supported. On the NAS, enable **Preferences → Run unattended** so the connection
survives sign-out/reboot. Its equivalent in an elevated PowerShell session is:

```powershell
& "$env:ProgramFiles\Tailscale\tailscale.exe" set --unattended=true --accept-dns=false
& "$env:ProgramFiles\Tailscale\tailscale.exe" ip -4
```

The DNS setting preserves the NAS's existing resolver rather than replacing home
or directory-service DNS. See [unattended mode](https://tailscale.com/docs/how-to/run-unattended).
Record node identity and its Tailscale address privately. Review key-expiry and
revocation/recovery policy for the two unattended servers; do not blindly disable
expiry on every administration device.

After enrollment, inspect status and tailnet policy before claiming restricted
access. The installer alone does not establish least-privilege rules. Limit the
administration devices to VPS SSH and the VPS to NAS TCP 445 using the reviewed
tailnet grants and Windows firewall/share permissions. Use the NAS's verified
Tailscale address/name in the production backup configuration. Preserve age file
encryption and the restricted Windows backup account. Keep the private decryption
key outside the application VPS.

Tailscale can add its own firewall chains; preserve them and Docker's chains.
Verify their ordering with the host firewall, new private SSH access, encrypted
SMB read/write/readback, DNS behavior and ordinary public SSH recovery before
narrowing public access. Direct UDP transport and relay fallback require their
own connectivity check; do not disable the host firewall to make enrollment work.
Do not expose NAS SMB on the public internet. No router port forwarding, subnet
router, exit node, public Funnel or web-service exposure is part of this setup.

## Host phase completion and migration prerequisites

Record runtime versions, boot enablement, log bounds, swap, directory permissions,
firewall state, new SSH access and zero application containers. Save reports
privately on the target and preserve a copy outside it. Document any approval
block or deliberately deferred setting rather than claiming it was applied.

Before application activation, establish private NAS connectivity (VPN or another
reviewed private path). The home LAN address is not reachable from a public VPS;
do not publish SMB/445 to the internet. Select a production hostname, registry
read access, a separate external monitor and independently recoverable backup
configuration. Do not reuse a pilot heartbeat URL for production or copy the age
private recovery key onto the application host.

Then follow [the VM transfer runbook](portable-vm-transfer.md). Host preparation
alone does not authorize starting a second production bot, changing public DNS,
pausing Azure writers or retiring Azure resources.

## Apply and verify the three-device tailnet policy

After reviewing the actual existing policy, the three-device setup can use the
[policy template](../../deploy/portable/host/tailnet-policy.json.example).
Replace every placeholder with the verified IPv4 address for the desktop, VPS
and NAS. The example deliberately has invalid placeholders and cannot be applied
unchanged. Preserve the previous policy before replacing an allow-all default.
Do not replace intentional rules for other users/services without review.

The template permits desktop → VPS TCP 22, VPS → NAS TCP 445, and desktop → NAS
TCP 445. The last path preserves private file-share access from the admin PC.
It permits no other new tailnet connections. Its empty `ssh` list disables the
separate Tailscale SSH feature; ordinary OpenSSH key authentication still uses the
TCP 22 network grant. Built-in tests protect these paths and assert representative
unwanted access stays denied. Do not skip validation errors or retain a broad
allow-all rule alongside the narrow rules.

After the user saves the policy, inspect only the VPS's received `PacketFilter`
from `tailscale debug netmap` (never publish the full netmap/node secrets). Verify
that inbound TCP is limited to the desktop's address and the VPS's port 22.
Repeat a real encrypted NAS upload/readback using the protected backup account.
Then have the user open a **new** desktop SSH connection through the private
address; an existing session alone is insufficient acceptance. Preserve public
SSH until the new connection and the operator/deployment recovery path work.
Recheck these addresses and grants when replacing or re-enrolling devices.
