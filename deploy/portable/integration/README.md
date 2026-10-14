# Isolated external-sidecar acceptance

Preparation for #430/#557 under v1.6.0. The runner does not purchase a host,
activate Compose, install a scheduler, dispatch deployments, change Azure
pairing, configure Slack or start a second production Discord gateway session.
It is an operator-run acceptance capability, not an automatic release gate yet.

## Target review before execution

Use an already reviewed isolated portable pilot with immutable images,
configuration preflight, the mom image capability record, and a **dedicated
Discord application/token, test guild, consenting DM recipient and channels**.
The test bot token must not be held by any other running gateway process. Review
its guild binding and permissions from the approved deployment configuration.
The sidecar API does not expose its guild/application identity; a version match
alone cannot establish isolation. `dedicated_test_identity=true` records that
operator review and cannot discover or prove it automatically.

Make one operator-owned 0700 configuration directory outside Git/evidence. Copy
`pilot.json.example` to a 0600 regular file there and replace placeholders with
the reviewed public origin, exact `/api/version` identities, recipient snowflake
and exact Discord username. Store the **sidecar bearer API key** in a separate
0600 file in that same private directory. This is not the Discord gateway token.
No credentials belong on command lines, in CI logs or reports. Use that single
configuration directory for all runs so its overlap lock serializes readonly
and full execution; do not replace the directory or lock during a run.

Use a reviewed private HTTPS sidecar route or an **already separately approved**
loopback-only forwarding path at `http://127.0.0.1:8001`. The runner creates no
forwarding, publishes no ports and mounts no Docker socket. HTTPS uses system
trust; custom ports, paths, query strings, fragments, URL credentials, redirects
and ambient proxies are refused. Plain HTTP is accepted only for that exact
loopback endpoint. Review where the forwarding actually terminates: loopback
and matching version do not by themselves prove the correct pilot container.

The public backend's reported bot version must match the directly probed sidecar
version and the reviewed build. This validates reported pairing, not the
integrity of a substituted server. Review image provenance and network routing
before running. Keep approved image references in private deployment records,
not the acceptance report. No host/network changes are authorized here.

## Read-only phase

After explicit approval of the isolated target, run the complete sequence:

```sh
python3 scripts/portable-integration.py readonly \
  --config /protected/integration/pilot.json \
  --token-file /protected/integration/sidecar-key \
  --report /protected/evidence/integration-readonly-001.json \
  --confirm-isolated-target
```

Checks include public Siege database health/exact component identity, sidecar
version/gateway readiness, absent/wrong bearer rejection, member-list contract
shape, one unique approved username/ID match, and live individual membership.
Member roster and role data remain in memory only. The phase issues GETs only;
no application database mutation or Discord send occurs. The public database
health endpoint and individual lookup perform their existing read operations.
Scheduler progress/resource observations remain covered separately by the
portable observer and approved mom health probe, not this runner.

## Full phase — separate Discord-write approval

Use a test channel with a unique name beginning `rsl-smoke-`. Review uniqueness
in the test guild manually: sidecar sends resolve the first exact channel name,
and the API does not expose channels for automated disambiguation. Review the
consenting recipient's exact username; duplicate usernames in the returned
roster stop the runner before any write.

Full mode reruns the entire read-only sequence before any POST. After separate
explicit approval of the recipient/channel and the three test messages:

```sh
python3 scripts/portable-integration.py full \
  --config /protected/integration/pilot.json \
  --token-file /protected/integration/sidecar-key \
  --report /protected/evidence/integration-full-001.json \
  --confirm-isolated-target --confirm-discord-writes
```

First an invalid empty post-message payload must return structured HTTP 422
before any Discord send. Then the runner sends one synthetic DM, one synthetic
channel text, and one generated 1×1 PNG. Text and the image filename contain the
run's UUID correlation marker. No real siege/member content is included. These
requests exercise the existing sidecar wire contract used by Siege's BotClient;
this script sends them directly, not through application assignment/notification
workflows. It performs no role-sync, reminder changes or production writes.
It verifies accepted-send responses and an HTTPS attachment URL without
requesting/storing the CDN URL. API acceptance is not proof the recipient read
or visually received the messages: `delivery_observed` remains false.

## Evidence, timeouts and cleanup

Use a fresh report path for every run. Reports reserve a private file before
network access, are never overwritten, and contain fixed checks, UTC time, mode,
correlation UUID, attempted-write count and cleanup flag. They exclude URLs,
versions, credentials, headers, rosters, usernames, channel/member snowflakes,
message bodies, CDN references and raw errors. Results are PASS/STOP; interrupted
runs preserve INCOMPLETE. A ten-second whole-request deadline and socket timeout,
1 MiB response limit, and 150-second between-request budget bound the sequence.
The final in-flight request can finish up to ten seconds after that budget.
No request is retried. A send timeout may have delivered a message, so even an
unacknowledged attempted write marks cleanup required.

PASS is acceptance of these checks only: `pilot_accepted` and
`delivery_observed` always remain false. Full PASS requires nine read-only
checks, controlled validation failure, and three accepted sends. Manually verify
that the marked DM, text and image reached the reviewed test destinations and
record a separate sanitized receipt report before declaring live integration
accepted. Use pass/fail flags, time and correlation UUID; exclude private content.

There is no deletion endpoint in the current contract. Inspect and manually
remove only the three marked test artifacts where the test account/channel
permissions permit. An INCOMPLETE full report means cleanup may be required,
even if its attempted count is unknown. Do not rerun a failed full test until
possible deliveries are inspected; retries can duplicate messages. Record
cleanup outcome in separate sanitized evidence. Do not delete unrelated data,
stop shared bots or restart host services as part of cleanup.

On STOP, diagnose the fixed failed check locally without publishing response
bodies/credentials. Keep evidence and use a new filename after correction.
Rollback this preparation with a repository revert. Any already approved pilot
activation has its own recovery runbook; these scripts change no deployment
configuration. Live receiver/Slack notification tests remain separate.

#430 remains open: an approved execution target, exercised delivery/cleanup
report, protected manual workflow and release-gate wiring are still needed.
A permanent Azure integration deployment or automatic Azure dev pairing is not
required for this provider-neutral pilot. No live Discord test was executed as
part of implementation or disposable fixture CI.
