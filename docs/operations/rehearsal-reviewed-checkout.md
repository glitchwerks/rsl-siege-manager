# Exact source checkout on the rehearsal VM

The backup service, restricted SSH command, and release preflight share one
root-owned `/opt/rsl-siege-manager` checkout on the isolated VMware VM. The
checkout must be at the exact `configuration.source_sha` in the reviewed
release candidate and reachable from Siege `main`. It contains source only:
no runtime credentials, deploy key, candidate manifest, or database data.

Before changing the VM, preserve a VMware snapshot, confirm console and admin
SSH access, and run the reviewed read-only host inspector and Docker runtime
preflight. Keep their sanitized reports. Fetch this installer from an exact
reviewed commit, verify its digest, and use a fresh root-private report path
for each phase. The installer checks the expected tailnet address and
console-verified SSH host-key fingerprint without printing them.

1. Run `inspect` with `--expected-ip`, `--expected-fingerprint`,
   `--source-sha`, and `--report /root/rehearsal-checkout-inspect-1.json`.
   Require PASS. It checks that `/opt` is protected and both the final and
   staging paths are unused.
2. Run `install` with the same exact identity and source SHA, a new report
   path, `--confirm install`, and `--snapshot-confirmed`. It clones the fixed
   public Siege remote into a private staging directory, checks that the SHA
   is on remote `main`, detaches the checkout there, requires a clean tree,
   then renames the staging directory to the final path. It never executes
   code from the cloned checkout.
3. Run `verify` with the same identity and SHA and a third report path.
   Require PASS and recheck the existing admin SSH session and Tailscale
   route. Verification checks root-owned protected directories, exact `HEAD`,
   the configured origin, a clean worktree, and required script paths.

The fixed staging path is `/opt/.rsl-siege-manager-install`. A STOP before the
rename leaves any partial staging checkout for private diagnosis; a retry
stops while it exists. Do not erase it blindly. A STOP after the rename leaves
the checkout in place and requires verification and snapshot recovery before
retrying. No automatic recursive delete or replacement is provided.

This phase does not install the restricted SSH key, backup units, application
containers, or configuration. The later key installer accepts the exact
reviewed forced-command script already in this checkout. The backup service
and release preflight must use this same source identity; changing the
checkout after either is configured requires a separate reviewed change and
fresh preflight evidence. Never copy this checkout onto the production VPS as
part of the rehearsal.
