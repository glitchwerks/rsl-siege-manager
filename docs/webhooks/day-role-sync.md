# Day-Role Sync Webhook Contract — Moved

**This spec's canonical home is [`glitchwerks/rsl-mom-apps`](https://github.com/glitchwerks/rsl-mom-apps) — the coordination repo for `rsl-siege-manager` ↔ `mom-bot` wire contracts.**

The authoritative version of this contract lives at:

- **Latest:** [`contracts/day-role-sync.md`](https://github.com/glitchwerks/rsl-mom-apps/blob/main/contracts/day-role-sync.md)
- **Pinned to the move commit:** [`contracts/day-role-sync.md @ 5576807`](https://github.com/glitchwerks/rsl-mom-apps/blob/5576807101c04a9b595192cee2b9a02aed1c9c12/contracts/day-role-sync.md)

## Why this file still exists

Code and docs in this repo reference the spec by its path (`docs/webhooks/day-role-sync.md`) — for example, [`backend/app/api/_role_sync.py`](../../backend/app/api/_role_sync.py), [`backend/app/services/bot_client.py`](../../backend/app/services/bot_client.py), and [`.env.example`](../../.env.example). This pointer file preserves those references and directs readers to the new canonical location.

## Contract changes

Open changes against `glitchwerks/rsl-mom-apps`, not this repo. Per the rsl-mom-apps coord protocol:

1. Open a coord issue in `rsl-mom-apps` describing the proposed change.
2. Bump the contract version in the canonical file.
3. Update consumer repos (this one + `glitchwerks/mom-bot`) in lockstep, referencing the coord issue (`refs glitchwerks/rsl-mom-apps#N`).

## History

The spec was authored in this repo at v1.0 (Normative). It moved to `rsl-mom-apps` on 2026-05-21 as part of establishing that repo as the cross-repo coordination point. See [`glitchwerks/rsl-mom-apps#2`](https://github.com/glitchwerks/rsl-mom-apps/pull/2) for the move PR and [`glitchwerks/rsl-siege-manager#456`](https://github.com/glitchwerks/rsl-siege-manager/issues/456) for the pointer-replacement issue.
