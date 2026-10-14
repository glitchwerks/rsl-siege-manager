# Independent runtime signals and Slack delivery

Preparation under #557 and the v1.6.0 release milestone. Azure workflows/Bicep
remain preserved. This is an opt-in sender and reviewed activation procedure,
not an installed schedule or activated monitor. No Slack webhook/token belongs
on the monitored VPS. The monitor's notification transport must remain available
when the VPS and its bot are unavailable.

## Receiver contract

Choose an independently hosted missed-signal monitor accepting empty HTTPS POSTs
to a private success URL and its `/fail` suffix. The receiver must acknowledge
with HTTP 200 and a body containing only `OK` (optional whitespace, at most 32
bytes). No redirects, credentials in the authority, custom port, query string or
fragment are supported. Its success URL is a bearer-like credential. The sender
uses system TLS trust, disables ambient proxies, sends no diagnostic payload,
and makes one request with a ten-second timeout. Failed delivery returns failure;
it cannot itself notify Slack. The receiver detects subsequent missing signals.

[Healthchecks' ping API](https://healthchecks.io/docs/http_api/) implements this
protocol; an independent compatible receiver can be substituted. Its unknown-
check/rate-limited HTTP 200 responses are rejected. Configure UUID URLs with POST
only and without keyword filtering. Do not use start/log/fail URLs as the base.
The receiver needs no access to private sidecar ports or the Docker socket.

Connect the selected operator Slack channel using the receiver's independently
hosted [notification integration](https://healthchecks.io/docs/configuring_notifications/).
Configure both down and recovery notifications, assign the integration to each
check, and keep credentials out of repository/evidence. These are operator setup
steps requiring separate approval, not actions performed by this code. No paid
plan or provider account is selected here.

Create separate runtime and backup checks. The existing backup job sends success
only after encrypted object and independent receipt readback; use its separate
endpoint file and hourly/40-minute-grace policy. Runtime success must never reset
backup age. External HTTPS health/identity and certificate-expiry monitoring
remain separately configured requirements in `monitor-policy.json.example`.
A runtime heartbeat passing is not a certificate-expiry check.

## One consolidated, separately approved runtime observation

Before running, approve the isolated pilot target and **notification to the
selected receiver**. Use the existing private `pilot.json` procedure, immutable
images and capability approval record. Place the success URL alone in an
operator-owned 0600 regular file under a 0700 directory, outside git and evidence.
The parent directory also holds a private overlap lock. Do not replace the
endpoint file/directory during a run or share one endpoint across multiple pilots.

After those approvals, the complete command is:

```sh
python3 scripts/portable-monitor.py \
  --config /protected/pilot/pilot.json \
  --endpoint-file /protected/monitor/runtime-url \
  --observation-report /protected/evidence/observation-001.json \
  --report /protected/evidence/signal-001.json \
  --confirm-readonly-pilot --confirm-notification
```

Both report paths must be fresh and distinct from each other and all inputs.
Reports are private, never overwritten and preserved on failure. No previous
PASS can be replayed: the sender runs the bounded observer itself. That observer
checks the reviewed project/images, container running/health/OOM/restart state,
pressure, public database health and exact component identity. Mom's approved
probe checks gateway and scheduler progress; bundled topology still checks only
gateway connectivity and requires separate scheduler acceptance. Container health
can lag the probe/retry interval. The observer creates no workload.

A fresh observation PASS sends success; any completed observer failure sends
`/fail`. Pre-observation safety failures and overlap rejection send nothing,
allowing the independent missed-signal deadline to detect absence. An interrupted
or killed process may leave INCOMPLETE evidence and sends no guaranteed signal.
Receiver acknowledgement proves receipt only, **not delivery to Slack**. The
sanitized signal report exposes fixed booleans/status/time only. PASS requires
both observation success and receiver acknowledgement, and is never full pilot
acceptance. No restart, deployment, database query, Discord write, remote account
mutation or direct Slack message is performed by this command.

## Activation and delivery evidence

Scheduling is a separate activation phase after the isolated pilot is accepted.
Do not install timers or start repeated observations as part of this PR. A
provisional runtime policy is one observation every 15 minutes with a ten-minute
grace, accommodating the observer's ten-minute budget plus sender timeout.
Review actual durations and acceptable outage-detection delay before activation;
this policy detects missing completion in approximately 25 minutes after the
last accepted success. Do not overlap runs. Backups retain their separate policy.

Before accepting the chosen monitor, separately approve and record one
consolidated, sanitized delivery report for a **dedicated test check**:

- A fresh accepted observation reaches the external receiver.
- A deliberate synthetic failure reaches the selected Slack channel.
- A subsequent success delivers recovery to that same channel.
- Silence beyond the configured period plus grace delivers a missed-signal alert
  while the monitored host is unavailable, followed by recovery on resumption.
- A synthetic overdue backup alerts independently of healthy runtime signals.

Use receiver-side test controls or a disposable fixture; do not stop live
services, change production DNS/firewalls or attach production Discord identities
to induce faults. Preserve delivery timestamps and pass/fail booleans, excluding
URLs, webhook credentials, channel/member IDs, workload content and raw errors.
No alert delivery claim is made until this separately approved report passes.

Rollback of repository preparation is a revert. After separately approved
activation, stop only the dedicated sender schedule and pause its matching
receiver check using the reviewed activation runbook; keep backups, external
public probes and Azure recovery definitions intact. There is no automatic
service recovery/watchdog here; bounded recovery remains a separate work item.
