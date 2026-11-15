# Portable paired bearer rotation (draft)

This runbook is under development for #638. Its script is not an authorization
to change the production VPS. The shared [credential contract](https://github.com/glitchwerks/rsl-mom-apps/blob/main/docs/portable-credential-rotation.md)
defines the two independent bearer pairs and the separate approval boundaries.
Do not use this draft to perform a live rotation. The Azure Key Vault adapter
remains available for a separately reviewed return path.

## Phases and evidence

`scripts/portable-vps-bearer-rotation.py` has six phase selectors. Each run
requires root on the already reviewed host, an absolute protected project path,
and a separate confirmation for any phase that stages or changes state.

| Phase | Changes | Expected evidence |
| --- | --- | --- |
| `inspect` | Writes one private report; reads the current project and containers. | `bearer-inspect` PASS/STOP with fixed check names only. |
| `stage` | Creates one private stage directory with complete rollback and candidate copies of `backend.env` and `mom.env`; leaves live files and services alone. | UUID run ID, `accepted.json` only after validation. |
| `activate` | Takes the existing release project lock; stops only backend and Mom, replaces both files, recreates those containers, then checks both bearer directions. | `activation-incomplete.json` followed by `activation-result.json`. A failure keeps the path stopped. |
| `recover-routine` | Takes the same lock and restores both old files, then recreates and verifies both peers. Reconfirm and rerun after a stopped attempt only while the old bearer remains safe. | Separate `recovery-incomplete.json` and `recovery-result.json`; retries retain reports under `routine-attempts/`. |
| `stage-fresh` | After failed activation of an exposed old bearer, stages a different new pair while both peers remain stopped. Reconfirm after an interrupted stage or failed fresh activation. | Protected `fresh-source-*`, `fresh-candidate-*`, and atomic `fresh-accepted.json`; earlier files move to `fresh-interrupted-*/`. |
| `activate-fresh` | Separately confirmed use of that fresh pair; refuses drift, recreates both peers and rejects both earlier selected bearers. | `fresh-activation-incomplete.json` and `fresh-activation-result.json`. |

The stage directory and all evidence files must remain root-owned and private.
It includes a protected exact copy of `stack.env`; activation and both recovery
paths reject changes to that file even when images and project name stay the
same. The copy contains configuration secrets and must remain on the host.
Candidate and rollback files contain actual credentials and must never be
uploaded to CI, attached to an issue, printed, or copied into a report. The
sanitized JSON reports contain only fixed check names, PASS/STOP, a UUID and
the selected pair name. Complete result reports are published atomically. An
`INCOMPLETE` marker means the phase may have been interrupted; inspect the
live state before any further operation. A stopped routine recovery can be
retried with the same explicit confirmation only while the old bearer remains
safe. The runner creates a new private attempt directory and preserves the
earlier result. A stopped fresh activation requires a new `stage-fresh` call;
the runner archives earlier files and creates a different token. It can also
restage after interrupted fresh staging without removing partial evidence.
Stage creates a new random bearer internally rather than accepting it on a
command line or in an environment variable.

The selected pair is either `sidecar` (`DISCORD_BOT_API_KEY` paired with
`MOM_BOT_SECRET_DISCORD_BOT_API_KEY`) or `reverse-api` (`BOT_SERVICE_TOKEN`
paired with `MOM_BOT_SECRET_SIEGE_WEB_BOT_TOKEN`). The other pair and every
unrelated byte of both runtime files must remain unchanged. The startup
configuration and exact running images must match the reviewed Compose state.
Inspection requires exactly one running Compose `mom` container on the host;
the Mom health probe checks its gateway and scheduler progress. The Discord
application owner must also confirm that no separately hosted bot uses the
production token before an isolated rehearsal or live operation.
The script uses targeted `up --no-deps --no-build --pull never --force-recreate
--wait backend mom`; a Compose restart would retain old `env_file` values.

Before `activate`, run the separately confirmed maintenance `pause` phase
from `scripts/portable-vps-maintenance-control.py` and retain its PASS report.
The rotation runner checks the shared root-owned state and repeats the
authenticated two-peer drain under the release lock immediately before it
stops either writer. Routine and fresh recovery also require that state to
remain paused. Do not resume from a rotation report alone: the maintenance
controller requires successful activation evidence for both bearer directions
and a later independent monitor report. If activation or recovery stops,
keep the control state paused and follow the separate recovery phase.

Activation probes the Mom sidecar with a read-only member-list request and
the Siege protected API with an invalid acting ID that distinguishes an
accepted service token from an unauthenticated request. It discards response
bodies, checks the replacement and unchanged direction, and requires the old
selected bearer to be rejected. It does not send a Discord message or write
database state. A healthy container alone does not satisfy this gate.
The three phases that start containers also require a private public-signal
configuration. Its HTTPS host must equal the selected project's `PUBLIC_HOST`;
the bounded public signal checks TLS, frontend, API health and exact component
versions before the operation reports PASS. The disposable CI rehearsal
substitutes a fixed public check because it has no public DNS or certificate.

Routine recovery is only valid if the old bearer was not exposed. If it was
exposed, do not declare it safe or invoke `recover-routine`; keep both peers
isolated, then use the separately confirmed fresh stage and activation phases.
Never restore an exposed old bearer. Before continuing a stopped recovery,
verify the shared pause, both isolated writers, and known runtime files. A
failed recovery keeps the path stopped. Reconfirm the applicable phase only
after the cause is understood; the runner rejects unexpected state and does
not delete earlier evidence.

## Required before production use

The draft still needs completed disposable Docker rehearsal for normal, drift,
partial, interrupted, routine rollback, and exposed-value paths. It also needs
one-bot and independent alerting acceptance, plus the final
operator procedure and rollback review. Production activation is a separate
gate after the full deployment window is approved.
