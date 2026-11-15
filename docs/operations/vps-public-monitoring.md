# Independent public web signal contract

This is the read-only signal producer for #584. Its optional sender uses the
private missed-signal receiver contract in #579. As of 2026-11-15, the
Healthchecks.io project has three separate paused checks: `RSL public
website/TLS`, `RSL public API health`, and `RSL public release identity`.
The original combined check was renamed for website/TLS; the two additional
checks have never been pinged. A separate
disposable check proved failure, recovery and missed-signal delivery to Slack
`#alerts` and fallback email. There is no scheduled public-signal job, live
production probe or production-check activation.
Run it only from a separately approved machine outside the production VPS.
Its exit status and one private JSON report can feed the independent receiver
tracked by #579. That receiver must send down, recovery and missed-signal
alerts to the approved Slack-capable destination even when the VPS is offline.

## Reviewed target and checks

Copy `deploy/portable/monitoring/public-signal.json.example` to a private
operator path. Set the exact approved apex HTTPS origin and all four expected
`/api/version` values from the release manifest and installed-state evidence.
Review that the deployed proxy uses `Caddyfile.www-redirect`; this checker
requires the approved HTTPS `www` to apex redirect at `/`. It does not accept
an arbitrary destination or follow redirects. The canonical-only Caddy
configuration needs a separately reviewed monitoring rule before activation.

One invocation makes five public `GET` requests: apex `/` and `/version.json`,
`www` `/`, and apex `/api/health` and `/api/version`. It checks exact 200
HTML-route status, the frontend marker's exact version/SHA, the 301/308 `www`
redirect target, exact `{"status":"healthy"}`, and exact component versions.
TLS uses the system trust store and hostname verification;
the served apex and `www` certificates must have at least 14 days remaining.
No private route, cookie, auth header, member data or Discord API is accessed.
Ambient proxy settings are not used. Each connection has a five-second socket
timeout; the whole observation has a 30-second deadline. The API response is
limited to 4 KiB. A failed route produces its own fixed false check so a site
failure is distinguishable from API or identity failure.

After separate authorization of the target, run from the independent machine:

```sh
python3 scripts/portable-vps-public-signal.py \
  --config /protected/public-signal.json \
  --report /protected/evidence/public-signal-001.json \
  --confirm-external-readonly
```

The report path must be fresh. The script writes it with mode 0600 and leaves
an `INCOMPLETE` marker if interrupted. Final evidence has a UTC timestamp,
fixed check labels and PASS/STOP/INCOMPLETE; it omits URLs, versions, response bodies,
certificate details and raw exceptions. Retain STOP/INCOMPLETE reports for
triage and use a new path for another attempt.
An exhausted whole-observation deadline is `INCOMPLETE`, distinct from a
completed failed check, and must not send a failure heartbeat.

## Optional independent receiver delivery

Place three distinct private success URLs in mode 0600 files named
`website-url`, `api-url` and `identity-url` under one mode 0700 directory on
the independent runner. Map them to separate paused receiver checks with the
same approved Slack and email integrations. Keep the public signal
configuration in a separate private directory and use fresh, distinct
observation and delivery reports. Each URL follows the same
[empty POST/`OK` contract](../../deploy/portable/monitoring/signals.md) as
the runtime sender. No Slack credential is stored on the runner.

One observation produces seven fixed checks. The website receiver gets apex
home, `www` redirect and both TLS-expiry results; the API receiver gets
`/api/health`; the identity receiver gets both frontend and API version
markers. Each receives success or `/fail` independently, so an API failure
does not mark the website or identity check down. An invalid, interrupted or
incomplete observation sends nothing to all three, allowing each independent
missed-signal deadline to expire. A failed delivery to one receiver does not
suppress delivery to the others.

With separate approval of the target and receiver notification, run one bounded
invocation from outside the VPS:

```sh
python3 scripts/portable-vps-public-monitor.py \
  --config /protected/public/public-signal.json \
  --endpoint-directory /protected/receiver/public \
  --observation-report /protected/evidence/public-observation-001.json \
  --report /protected/evidence/public-delivery-001.json \
  --confirm-external-readonly --confirm-notification
```

The delivery report contains only a UTC timestamp, status and fixed per-signal
booleans for observation and receiver acknowledgement. It never records URLs, versions,
response bodies or raw errors. An acknowledged signal proves receiver receipt,
not Slack/email delivery. Preserve failed or incomplete reports. A receiver
failure does not turn a successful public observation into a monitoring PASS.

## Receiver policy and activation boundary

For sustained off-host operation, request each public signal at five-minute
intervals, open an incident on the first completed failed observation, and
resolve it after a successful check. The sender's `/fail` is immediate;
receiver grace applies to missing signals. A 14-day certificate threshold is
an incident even when HTTPS still works. A missing signal after 15 minutes
requires an independent missed-signal alert. These are proposed initial
thresholds to validate before activation. The three paused receiver checks
are configured separately; their distinct down/recovery alerts identify the
affected surface. The private fixed-label observation report gives the exact
failed route or certificate check during triage. Slack and email delivery of
the disposable check does not establish live public-signal delivery.

Use disposable monitor-side responses to exercise PASS, site-only failure,
API-only failure, wrong redirect, version drift and near-expiry TLS. Test down,
recovery and missed-signal delivery at the receiver without changing DNS,
certificate or services on the production VPS. Document the alert owner and
record sanitized delivery evidence. If false positives occur, pause only the
affected public receiver check, preserve the other signal checks, and inspect the
fixed-label report; do not restart the VPS stack as an alert response.

Scheduling, receiver assignment for this producer and live production probes
need separate approval. This change can be rolled back by reverting the
repository PR; it creates no live monitor or host state.
