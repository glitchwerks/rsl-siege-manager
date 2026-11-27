# Private CI route to the rehearsal VM

This guide completes the separate `vps-rehearsal` route used by the protected
manual Siege release workflows. It does not authorize a workflow dispatch or
change the production target. Keep the VM's address, verified SSH host key,
OAuth credentials, deploy private key, and candidate reader credentials out of
Git, issue comments, workflow inputs, and reports.

## Existing GitHub environment

The protected environment allows `main` only and requires operator approval.
It already has the rehearsal VM host, pinned host-key fingerprint, known-hosts
entry, and distinct deploy SSH key stored under the `REHEARSAL_VPS_*` secret
names. The SSH login variable is `REHEARSAL_VPS_SSH_USER`. Verify the names and
the current environment rules in GitHub before a dispatch; secret values are
not readable through GitHub's API.

Create a **separate** Tailscale OAuth client with only the `auth_keys` scope and
only the `tag:github-rehearsal` tag. The GitHub-hosted action uses that client
to register an ephemeral runner. Store its ID and secret in the protected
environment as `REHEARSAL_TAILSCALE_OAUTH_CLIENT_ID` and
`REHEARSAL_TAILSCALE_OAUTH_SECRET`. Do not reuse the production OAuth client.
Tailscale's [OAuth client](https://tailscale.com/docs/features/oauth-clients)
and [GitHub Action](https://tailscale.com/docs/integrations/github/github-action)
documentation describe this flow.

The candidate-validation job runs **before** environment approval. Give Siege
Manager selected organization or repository Actions secrets named
`REHEARSAL_VPS_RELEASE_REPO_READ_TOKEN` and
`REHEARSAL_VPS_RELEASE_PACKAGE_READ_TOKEN`, plus variable
`REHEARSAL_VPS_RELEASE_READ_USER`. The repository token needs only the same
cross-repository Contents/Actions read scope as the production candidate
validator; the package token needs only `read:packages`. Verify these names
where they are stored. Organization secret names may be invisible to a token
without organization administration permission; absence from a local listing
does not prove absence from GitHub Actions.

## Tailnet policy change

Inspect and preserve the **current** policy, its tests, existing desktop,
production VPS, NAS, and `tag:github-deploy` access before editing it. The
following is an additive shape, not a full replacement policy. Substitute the
private addresses in the Tailscale admin console; do not commit them here:

```jsonc
// Add to tagOwners, retaining every existing entry.
"tag:github-rehearsal": []

// Add to grants, retaining every existing grant.
{"src":["tag:github-rehearsal"],"dst":["REPLACE_REHEARSAL_VM_IP"],"ip":["tcp:22"]}
{"src":["REPLACE_ADMIN_DESKTOP_IP"],"dst":["REPLACE_REHEARSAL_VM_IP"],"ip":["tcp:22"]}
{"src":["REPLACE_REHEARSAL_VM_IP"],"dst":["REPLACE_NAS_IP"],"ip":["tcp:445"]}

// Add tests using the real private addresses.
{"src":"tag:github-rehearsal","accept":["REPLACE_REHEARSAL_VM_IP:22"],
 "deny":["REPLACE_PRODUCTION_VPS_IP:22","REPLACE_NAS_IP:445"]}
{"src":"REPLACE_REHEARSAL_VM_IP","accept":["REPLACE_NAS_IP:445"],
 "deny":["REPLACE_PRODUCTION_VPS_IP:22","REPLACE_NAS_IP:22"]}
```

Review the effective policy and require its tests to pass **before saving**.
Tailscale validates the policy and embedded tests on save. Verify the existing
production and desktop tests still pass afterward. The tailnet grant controls
network reachability; the VM still needs its own reviewed restricted SSH key,
backup timer, and installed host command before a CI access check can pass.
[Tailscale grants documentation](https://tailscale.com/docs/features/access-control/grants/grants-explained)
explains policy tests.

## Verification and recovery

First verify the admin desktop can reach VM SSH, the VM can reach the NAS SMB
port needed for its dedicated share, and the policy's denied paths remain denied.
Verify the production VPS and NAS
paths remain healthy. After the root-private checkout, Docker runtime, backup
service, and restricted SSH key each pass their separate host gates, dispatch
only the read-only rehearsal access workflow from `main` with an approved
candidate. Preserve its sanitized candidate and access reports. A PASS grants
no permission to stage or promote; each later phase has a separate approval.

If the CI route fails, revoke only the rehearsal OAuth client and remove only
its new grants after preserving the prior policy and tests. Remove the new tag
owner only if no other node or client uses it. Keep the production OAuth client,
`tag:github-deploy`, production environment, and existing host keys unchanged.
