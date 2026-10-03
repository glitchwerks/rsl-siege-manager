# Reduce Azure registry and advisory monitoring costs

This change reduces recurring costs without changing application deployments,
database servers, Discord bot topology, credentials, or public access. Keep the
Azure templates and normal release pipeline available for continued Azure use
and a future return from portable hosting.

## Changes and expected impact

| Target | Change | Recovery |
| --- | --- | --- |
| Production registry `siegeacrprod` | Standard to Basic | Restore the previous SKU |
| Production latency warning | Evaluate every five minutes instead of every minute | Restore the recorded frequency |
| Production slow image warning | Evaluate every five minutes instead of every minute | Restore the recorded frequency |

The advisory queries and lookback windows remain five minutes. Enabled state,
thresholds, notification destinations, and the latency alert's automatic
resolution behavior stay as deployed. Error, database, bot restart, and crash
loop alerts are outside this change and retain their existing frequency.
Advisory detection can take up to approximately five minutes plus ingestion and
notification delay; shorter spikes may aggregate differently than before.

The October 3 inspection found approximately 29 GiB in the production registry.
Basic includes 10 GiB, compared with Standard's 100 GiB, so a tier change adds
excess-storage charges. The billing export implies approximately $14.71 of
registry-unit savings for its reporting window, before excess storage. Budget
approximately $12–$14 per month net at current volume; verify actual rates and
the next invoice. Alert savings require separate confirmation against the
account's pricing. Do not delete release images to obtain these savings.

Sources: [registry tiers](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-skus),
[registry pricing](https://azure.microsoft.com/en-us/pricing/details/container-registry/),
[monitoring pricing](https://azure.microsoft.com/en-us/pricing/details/monitor/).

## Scope and deployment gates

The normal full infrastructure deployment remains manual and unchanged. For
this stage, review the script and preflight report, then explicitly approve a
targeted configuration update. This narrowly scoped operation avoids a full
template deployment changing applications, secrets, certificates, or current
image versions because of unrelated template drift. Reconcile the parameter
changes into main through a reviewed PR before routine infrastructure deploys.

Do not run during an application deployment or registry purge. Preserve the
report outside Git; it holds rollback values and fingerprints, not secrets or
workload logs. No extensions or system packages are installed by the script.
Use existing Python 3, Azure CLI, and curl installations, the existing Azure
login, and the intended subscription. The script refuses
a different active subscription and a preflight older than one hour.

All phases run the same fixed production-only allowlist of one registry and
two alerts. Development settings are observed but never modified.
Before mutation, the script checks service health, current app images/revisions,
registry tag inventory, alert windows, and unchanged target configuration.
After mutation, it checks those again. HTTP probes use existing production and
unrestricted mom-bot health routes, preferring the configured public custom domain;
they do not send member messages or test notification deliveries. Private-only
health routes and sustained registry pull throughput still need operator
validation; control-plane health is not proof of every user workflow.
Development health routes are not probed to avoid waking idle applications.
If mom-bot has an ingress IP allowlist, preserve it and record management-plane
verification only. Its private functional health must be checked from an
already authorized source during migration rehearsal; this stage does not
change mom-bot. Ingress configuration hashes detect unrelated access changes.

## Phase scoped commands

Set `TRIM_SUBSCRIPTION` to the intended subscription ID and `TRIM_REPORT` to a
new absolute path outside the checkout. Do not put credentials in either.

Read-only preflight:

```bash
python3 scripts/azure-cost-trim.py preflight \
  --subscription "$TRIM_SUBSCRIPTION" --report "$TRIM_REPORT"
```

After reviewing the report and explicitly approving the listed changes:

```bash
python3 scripts/azure-cost-trim.py apply --confirm \
  --subscription "$TRIM_SUBSCRIPTION" --report "$TRIM_REPORT"
```

Separate read-only verification:

```bash
python3 scripts/azure-cost-trim.py verify \
  --subscription "$TRIM_SUBSCRIPTION" --report "$TRIM_REPORT"
```

Recovery, only when explicitly requested:

```bash
python3 scripts/azure-cost-trim.py rollback --confirm \
  --subscription "$TRIM_SUBSCRIPTION" --report "$TRIM_REPORT"
```

Apply records each attempted mutation before issuing it. On failure, it attempts
to restore the recorded prior values, including uncertain request outcomes.
Recovery refuses to overwrite concurrent configuration changes. Any incomplete
recovery produces a failing report and requires focused operator diagnosis.
There is no resource deletion, data migration, cleanup of images, or host restart.
Keep the report until the configuration and billing change are verified.

For infrastructure-as-code recovery, restore `acrSku = 'Standard'` in production
and `advisoryAlertEvaluationFrequency = 'PT1M'` in the production file through
a PR. The new parameter defaults to `PT1M` for existing callers that do not opt
into the lower-frequency configuration.
