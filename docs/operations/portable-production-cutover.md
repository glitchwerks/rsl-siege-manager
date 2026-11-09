# Portable production cutover and rollback

This procedure prepares a maintenance window after the
[portable VM rehearsal](portable-vm-transfer.md). It does not authorize stopping
Azure, starting production gateways, changing public DNS or retiring resources.
Keep an operator-specific plan and evidence outside Git in protected storage.
Budget 30–60 minutes for the initial window, then refine it from measured final
export/restore, certificate, DNS and acceptance work. This is a budget, not an RTO
guarantee after a failed activation.

## Readiness before asking to begin downtime

- Record the current source app revisions/images, active replicas, jobs and callers.
  Confirm the public source origin's health and version independently of an edge
  proxy's response. Freeze production releases, infrastructure changes and manual
  migration jobs for the window. Keep the old Azure deployment workflows from
  restarting retained production services after handoff; do not push production
  tags until the production deployment target is deliberately updated or a reviewed
  manual portable release procedure is in place.
- Verify bot credentials with GET-only identity, guild, role and channel requests.
  Record whether source processes share an application; container count is not
  identity count. Do not connect a second gateway or send a test message as a
  credential probe. Check existing development sidecars too: if one uses the
  production application, include its pause explicitly in the cutover scope.
  Leave it stopped on rollback unless restarting it is separately authorized.
  Preserve unrelated development services; never start a developer bot for this
  preparation. A retained legacy Deploy workflow can restart a shared-identity
  development gateway on a later main-branch push. Record every workflow capable
  of changing these apps, including deployment and Bicep workflows in both repos,
  and arrange a verified freeze before pausing writers. Disabling a workflow does
  not substitute for reviewing any deployment run that is already in progress.
- Select one supported topology and reviewed image/source pair. For a shared
  identity, a tested portable Mom may replace the bundled Siege gateway and Mom
  gateway while preserving the Siege HTTP APIs, reminders and activity handling.
  Verify database revision compatibility and call-key pairing. Do not layer both
  mutually exclusive topology overlays or silently drop an independent identity.
- Render and validate the exact configuration, including any opt-in gateway guard.
  Keep production activation blocked while preparing. Prove a start request is
  refused without the separate cutover approval; do not actually launch a gateway
  to test that guard. Account for both application databases, UUID restore targets,
  least-privilege owners, trusted proxy address and no public sidecar/database ports.
- Keep the approved host size while measuring actual memory, swap, OOMs and latency.
  Source Azure WorkingSetBytes is useful comparative evidence, not a target-host
  capacity certification. Do not sum configured memory ceilings and declare the
  host insufficient. Repeat target observation when its real gateway and workload
  start; define a hold/rollback response to sustained pressure or failed health.
- Confirm exact public DNS type, content, proxy status and TTL from the operator's
  provider. Public answers for a proxied Cloudflare hostname do not reveal the
  origin record. Preserve all existing records before changing the intended web
  record; review explicit AAAA records separately from generated edge addresses.
  Pi-hole's rehearsal record is a separate local override, not production DNS.
- Prepare a separate production backup monitor using the intended Slack integration,
  proposed hourly period and 40-minute grace. Store its secret URL privately and
  reject reuse of the pilot check. A paused check is not active monitoring. Keep
  service/timer templates disabled until the real production job succeeds under
  the installed restrictions and the operator activates the production check.
- Verify independent NAS ciphertext and receipt readback, retain the database
  restore evidence and recoverable age key outside the VPS. Database dumps do not
  contain application passwords, session keys or upload/monitor settings. Seal a
  separately identified configuration recovery package and publish/verify only its
  ciphertext and receipt. Keep it outside automatic database retention. Include an
  encrypted file manifest and exact code/image references, not Azure administrator
  caches, source database credentials, SSH private keys or the age private key.
  Distinguish encryption/readback acceptance from an independently decrypted restore.
- Record existing findings and operator deferrals. Preserve existing Discord
  permissions unless a separate change was approved. A bot can have permission to
  post reminders while lacking permission to notify a non-mentionable role.
  [Discord's role-mention requirements](https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx#allowed-mentions-object)
  explain the separate notification permission.

## Pause and final export: maintenance begins here

1. Recheck the source revisions, identities, job execution state, target image cache,
   protected configuration and DNS rollback record. Obtain explicit approval for
   the full scope: source writer pause, final exports, protected transfer/restore,
   public origin/TLS change and normal production gateway activation. Unpause the
   distinct production monitor at the agreed activation point; do not send fake
   success pings while waiting for a real job.
2. Block new user writes through the old public frontend. For an Azure frontend
   that is the only public API entry, stopping that frontend also prevents cached
   browser clients from reaching its API proxy. Confirm that no other ingress or
   client bypasses it. Wait for pending notification batches and in-flight requests
   to finish before stopping the API and gateways. Completed batches with unknown
   delivery results require review; never blindly replay them.
3. Stop the source API and every gateway/scheduled writer for the production
   identity. Stop the manual migration job only if explicitly approved and active;
   do not run it. Poll until the apps report stopped and old replicas are gone.
   A control-plane request accepted with HTTP 202 is not proof of a stopped writer.
4. Use the supported app-level Azure stop operation rather than deleting apps,
   changing replica limits or improvising revision-mode changes. The current API is
   `POST .../Microsoft.App/containerApps/APP/stop?api-version=2025-07-01`.
   Its paired `/start` operation is the pre-write rollback path. Keep the existing
   definitions/revisions and record the exact apps and outcomes.
   [Azure stop API](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/container-apps/stop?view=rest-resource-manager-containerapps-2025-07-01),
   [Azure start API](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/container-apps/start?view=rest-resource-manager-containerapps-2025-07-01).
5. Take fresh PostgreSQL 16 custom dumps and controls of both databases only after
   every writer is paused. These per-database snapshots now share the logical
   paused boundary. A snapshot from the earlier running-source rehearsal is not
   final cutover data. Refresh short-lived Entra database access when needed; do
   not copy it or the source passwords to the VPS or change source authentication.
6. If an operator source firewall exception is needed, create only a unique rule
   for that operator's current public IPv4 address, verify it, and remove that exact
   rule in cleanup even on failure. Verify absence rather than assuming a delete
   request succeeded. Keep the final manifest/archives/reports private and verify
   all transferred hashes. Publish age ciphertext and its receipt to the NAS and
   independently read both back.

## Restore and public TLS before enabling target writers

1. Stop only the VPS rehearsal web/relay services that conflict with production
   ports/resources. Stop the temporary operator-side SSH membership tunnel. Preserve
   its test data, evidence, CA and volumes; do not prune Docker or remove volumes.
   Production must not depend on that relay or test application/guild.
2. Start only the production PostgreSQL service on its private network. Require the
   exact project/service/image and the reviewed fresh volume. The standard bootstrap
   creates empty runtime databases and least-privilege roles. Verify both roles
   and empty databases explicitly; a healthy restarted PostgreSQL process alone
   does not prove initialization succeeded. Its nonsecret environment-based init
   SQL bind mount must be readable by the container user. Preserve a failed
   initialization volume and diagnose it before retrying; do not prune it.
   Restore the final
   snapshots first into fresh UUID-named databases owned by those roles through
   the canonical restore checks, without source ownership/ACLs or automatic seeds.
3. Compare every control total and schema head before any runtime connects. If the
   exact source/candidate schema revisions match, do not run migrations merely as
   a startup ritual. When renaming UUID targets to runtime names, first prove any
   bootstrap databases are this new instance's empty targets; park those empty
   databases under unique names rather than overwriting or dropping unrelated data.
   Preserve the original dumps and the restored volumes on any failed check.
4. Start API/frontend explicitly with migrations and gateway profiles excluded.
   Verify database and API reads privately. Keep public traffic behind maintenance
   while establishing the real hostname and public certificate. The source remains
   write-blocked throughout, including clients with cached DNS.
5. Coordinate the saved public DNS record change and Caddy public TLS. If direct
   HTTP validation is selected, ensure the authoritative web record actually reaches
   the VPS and no stale explicit AAAA or edge rule diverts the challenge. For a
   proxied Cloudflare origin, a planned DNS-only issuance phase can avoid dependence
   on a pre-existing origin certificate; restore the accepted proxy setting only
   after verifying the new origin certificate. Never bypass certificate validation
   or weaken strict TLS verification. Preserve non-web DNS records.
6. Verify the real hostname/chain and maintenance response from outside the VPS.
   Restore the reviewed public routing when ready. Do not carry the rehearsal's
   internal CA, literal metadata patch or private membership relay into production.
   An operator-deferred metadata issue can remain recorded without silently fixing
   or upgrading the application during the infrastructure move.

## Gateway activation is the rollback boundary

Before starting Mom or any other target producer, confirm again that all source
writers for its identity are stopped, final restore checks passed and public TLS
and rollback records are ready. Starting a production bot can immediately create
scheduled database writes or Discord deliveries **before any browser user writes**.
Treat that start as crossing the boundary; do not wait until DNS/user traffic to
classify rollback risk.

Start exactly the approved gateway service for each identity. For a single-Mom
plan, leave the bundled and independent-Mom alternatives inactive. Check gateway
connection and scheduler-progress health without forcing reminders or sending
unapproved messages. Collect host/container memory, swap, OOM and restart evidence.
Open web writes only after health qualifies. Ask the operator to verify production
login, board and image previews; a controlled real Discord delivery requires its
own intended recipient/channel and authorization.

Run the complete production backup through the staged service's actual account
and restrictions. Require database dump, archive verification, encryption, NAS
ciphertext readback, receipt readback and acknowledged production monitoring to
pass. Confirm the production check is active/unpaused and assigned to Slack.
Only then enable the hourly timer; inspect the first regular scheduled job.
Automatic local/remote deletion stays off until its distinct recovery/retention
acceptance is complete. Keep source Azure definitions and data intact during
observation, including a reminder cycle, and retire resources only on later
explicit approval.

## Rollback

**Before target producers or user writes:** keep the VPS in maintenance, stop any
new target services involved, and restore the exact saved public DNS record. Start
only the approved original source apps (API/sidecars/scheduler, then public
frontend), preserving their original configuration. Verify source health and old
origin routing before reopening. Retain target dumps/volumes for diagnosis. Do not
resume source and target gateways together.

**After target producer activation or any possible new delivery/write:** keep the
public site in maintenance and stop target writers. Preserve fresh exports and
ledgers from both environments, then choose a reviewed forward fix or a separately
approved reverse data migration. Reconcile notification/reminder deliveries and
unknown results before retrying. A source Azure database may need its original
Entra ownership/grants reconstructed on a reverse import; that is not proven by a
VPS-to-standalone-PostgreSQL restore. Do not overwrite source databases, claim a
fast DNS-only rollback, or discard target writes without that explicit recovery
plan and approval. Recovery time in this branch may exceed the initial window.
