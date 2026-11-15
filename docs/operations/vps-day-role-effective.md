# Read-only portable day-role running-state check

This is the observation phase for [coordination #16](https://github.com/glitchwerks/rsl-mom-apps/issues/16).
It does not stage or activate a configuration change, recreate a container,
send a webhook, or modify Discord roles. A passing report is not approval to
enable the feature or resume writers.

Use the reviewed checkout on the target host after a separately approved
configuration-only activation or recovery. The protected project directory
must contain the exact candidate `stack.env`, runtime files, bind sources, and
Mom image approval. Run the existing portable preflight first. Keep the
report directory root-owned and mode 0700; choose a new report filename for
every run. The command requires root because it reads the protected runtime
configuration and Docker's container metadata, both of which contain secrets
internally. Neither the rendered configuration nor raw Docker inspection
output may be copied into logs or evidence.

```sh
python3 scripts/portable-vps-day-role-effective.py \
  --project-directory /srv/rsl-siege \
  --expected-enabled true \
  --report /srv/rsl-siege/evidence/day-role-effective-001.json \
  --confirm-readonly
```

Use `--expected-enabled false` to verify the current disabled baseline or a
later approved kill switch. The checker validates the reviewed Mom topology,
requires exactly one healthy backend container with the expected immutable
image, compares every rendered backend environment value with that container's
creation environment, and checks the expected flag. When enabled, both role
IDs must be present and numerically distinct. It rechecks the container and
service inventory after inspection to detect a concurrent recreation or
restart. The report contains only fixed booleans, time, phase, and PASS/STOP;
an interruption leaves INCOMPLETE. It omits environment values, URLs, secrets,
container IDs, and Discord IDs.

This observes the running container's creation environment, not the in-memory
settings object inside Uvicorn, and it does not prove the receiver's guild
mapping or role hierarchy. Complete isolated authenticated assign/unassign
and disabled negative-control tests under their separate authorization before
accepting role-sync behavior. If the check stops, keep writers paused, inspect
private host evidence, and use the reviewed configuration recovery path. Do
not run `docker compose restart` to apply an `env_file` change; it does not
reload that file. The [configuration-only mutation and recovery phases](vps-day-role-config.md)
are implemented as an opt-in host runner. They require isolated rehearsal and
separate approval before live use.
