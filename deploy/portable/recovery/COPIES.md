# Encrypted independent backup copies

This prepares #557, without activating backups on any host. The existing database
runner makes a protected bundle; `scripts/portable-backup-copy.py` adds separate
`seal`, `publish`, and `recover` phases using already-installed `age`, `rclone`,
and PostgreSQL 16 clients. It never installs tools, creates accounts/buckets,
schedules jobs, deletes backups, starts apps or connects to databases. The existing
isolated restore runner remains the separately approved database-write phase.

## Approval and recovery prerequisites

Before publication, review the destination, account, credential scope, storage and
transfer budget, endpoint/TLS/SSH trust, host dependencies and access recovery.
The destination must survive losing the application VM and its disks. A separate
storage account/failure domain is preferable; a folder or service on the same VM
is insufficient. Backend selection cannot prove independence. S3-compatible,
Azure Blob and SFTP destinations are supported without changing Azure deployment
files or coupling deployment to a VPS provider. No destination has been purchased
or selected by this change.

Use a protected operator-owned 0700 root and 0600 credential, receipt and evidence
files. Symlinks in copy-runner paths are refused. Prepare native age public
recipients in `recipients.txt`; only native `age1…` recipients are supported.
Keep corresponding `AGE-SECRET-KEY-1…` identities on an independently protected
recovery machine/store, separate from upload credentials. The backup VM only needs
public recipients. Do not generate keys on this infrastructure host as an implicit
setup step, commit keys/configs, paste credentials into commands, or use shell tracing.

Back up the trusted receipt independently alongside the key recovery procedure.
It contains copy/bundle UUIDs, creation time, ciphertext size and SHA-256, without
schema names, endpoint or credentials. A receipt hash detects corruption but does
not authenticate a receipt obtained from an attacker. Use an approved trusted
receipt; possession of the public recipient allows anyone to encrypt a new file.
Losing all receipt copies prevents this runner from recovering the object. Keep
private bundle manifests/configuration and immutable application images separately.

## Destination configuration

Provide one reviewed private rclone configuration section named `[backup]`.
Allowed backend settings are deliberately narrow:

- `s3`: `provider`, `access_key_id`, `secret_access_key`, `region`, `endpoint`,
  `location_constraint`. Explicit access keys are required; custom endpoints must
  use HTTPS. Default service endpoints use rclone's normal HTTPS behavior.
- `azureblob`: `account`, `key`, `sas_url`. Supply account/key or an HTTPS SAS URL.
- `sftp`: `host`, `user`, `port`, `pass`, `key_file`, `known_hosts_file`,
  `disable_hashcheck`. Supply password (in rclone's obscured format) or private key,
  and a protected, pre-reviewed known-hosts file. Key/known-hosts paths must be
  absolute and private. Never accept an unknown host key merely to pass validation.

Other settings/backends, environment authentication, agents, plugins, aliases and
local filesystem destinations are refused. The command clears ambient rclone,
cloud, SSH-agent and PostgreSQL overrides; config files remain secret-bearing.
Use credentials limited to the reviewed prefix. Prefer upload/read permissions
without delete permission, and separate read-only recovery credentials. Configure
object versioning/immutability and lifecycle only as independently reviewed storage
changes; this script is not a provider-enforced object lock. It uses random object
UUIDs and rclone `--immutable`, never sync/move/purge/delete. Concurrent publication
to the same copy UUID is not supported; serialize runs before activation.

The prefix is relative, such as `bucket/rsl` for object storage or `rsl` for SFTP.
No leading slash, `..`, colon, spaces or connection strings. Remote object names
are `<prefix>/<copy-UUID>.tar.age`. Endpoint, prefix and credentials stay out of reports.

## Seal: local encryption only

After the approved database backup passes, keep its files immutable while sealing.
The bundle must contain exactly the two dumps and manifest; extra secret files are
refused. Every phase requires a fresh workspace and fresh report filename under
pre-existing private directories. Commands below are phase-scoped examples, not
approval to execute on a live host.

```sh
python3 scripts/portable-backup-copy.py seal \
  --bundle /protected/backups/source-001 \
  --recipients /protected/config/recipients.txt \
  --work /protected/copies/seal-001 \
  --report /protected/evidence/seal-001.json
```

The result contains `bundle.tar.age`, `receipt.json`, and a **plaintext** `bundle.tar`.
Only the encrypted object is ever uploaded. Source bundles, local tar files and
recovered data remain sensitive, protected artifacts. The script preserves them on
success/failure and does not apply retention or promise secure erasure. Review
local retention/cleanup separately before unattended activation. Storage checks
reserve headroom, but do not protect against other workloads consuming free space.

## Publish: explicitly approved remote write and readback

```sh
python3 scripts/portable-backup-copy.py publish \
  --package /protected/copies/seal-001 \
  --config /protected/config/upload.conf --prefix bucket/rsl \
  --work /protected/copies/publish-001 \
  --report /protected/evidence/publish-001.json --confirm-publish
```

Confirmation records the operator's independently approved upload intent; merging
this code does not authorize publication. The runner checks local ciphertext,
uploads only that object, downloads it into the private workspace, then checks
exact size/SHA-256. A matching download proves transport integrity, not key recovery
or database recovery. Preserve the receipt independently after PASS. A failed
readback may leave an uploaded object; do not delete it or overwrite evidence
implicitly. Diagnose privately and use a fresh workspace/report for a controlled
retry. Existing remote objects are not intentionally replaced.

## Recover: remote read, key recovery and offline database archive checks

On the approved recovery machine, using a recovered key and trusted receipt:

```sh
python3 scripts/portable-backup-copy.py recover \
  --receipt /protected/catalog/receipt-001.json \
  --identity /protected/keys/recovered-identity.txt \
  --config /protected/config/read-only.conf --prefix bucket/rsl \
  --work /protected/rehearsal/copy-001 \
  --report /protected/evidence/recover-copy-001.json
```

The remote object must match the trusted receipt before decryption. Extraction
accepts only three unique flat regular files; links, devices, nested/traversal
paths, extra/duplicate/missing files and oversized tar payloads are refused. The
result is `/protected/rehearsal/copy-001/bundle`. Both dump hashes, PostgreSQL 16
archive readability, and bundle identity must pass. No database is contacted.
Then run the separately approved [isolated restore phase](README.md#isolated-restore-phase)
against that recovered bundle, never against the original local copy. Rehearse
recovered identity/receipt access independently of the lost VM/account, not merely
a key still present on the original machine.

Each report has fixed checks and PASS/STOP, identifiers, time and duration. Raw tool
output is suppressed; interrupted evidence remains INCOMPLETE/STOP. Commands have
bounded timeouts; partial private artifacts are preserved. STOP/INCOMPLETE is never
acceptance, even if an object exists. Preserve evidence before separately approved,
precisely scoped cleanup. Do not prune volumes, delete databases or remove remote
objects as an automatic recovery action.

## Remaining stages

Disposable CI rehearses actual encryption, SFTP upload/readback, wrong-key and
corruption refusal, decryption and PostgreSQL restore from the remote copy. This
proves tool integration; its same-runner server is **not** independent production
storage or production-sized recovery evidence. S3/Blob configurations need their
own approved destination acceptance before activation.

Hourly scheduling, bounded concurrency, retention and independent overdue/failure
alerts are the next stage. Before activation, measure runtime, capacity/transfer
cost, restore duration and key/receipt recovery with the real destination; approve
retention and storage access, then activate separately. The proposed one-hour
loss window is not yet an accepted guarantee. Existing Azure infra remains supported.

Primary references: [age CLI](https://github.com/FiloSottile/age#usage),
[rclone copyto](https://rclone.org/commands/rclone_copyto/),
[rclone SFTP trust](https://rclone.org/sftp/).
