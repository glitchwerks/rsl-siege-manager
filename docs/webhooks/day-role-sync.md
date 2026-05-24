# Day-Role Sync Webhook Contract

**Version:** 1.0
**Status:** Normative
**Repo:** `glitchwerks/rsl-siege-manager` (producer)
**Related:** `glitchwerks/mom-bot#6` (first conforming receiver — see §11)

> **RFC 2119 keywords.** Throughout this document the terms MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY carry the meanings defined in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119). These keywords appear in **ALL CAPS** to distinguish normative requirements from informative guidance.

---

## 1. Overview

The day-role-sync webhook is the wire contract by which `rsl-siege-manager` notifies an external receiver whenever a member's attack-day assignment changes. When a member is assigned to or removed from an attack day, this repo emits an HTTP POST to the configured receiver URL carrying a structured JSON payload. The receiver is expected to translate that payload into appropriate role operations (for example, toggling Discord roles) and respond with a structured JSON response describing what it did.

This contract is bot-agnostic. `rsl-siege-manager` makes no assumptions about which receiver is on the other end — it does not know or care whether the receiver is a Discord bot, a webhook relay, or a test harness. Any conforming implementation that accepts the payload described in §2, applies the auth scheme in §4, and returns the response described in §3 satisfies this contract.

`glitchwerks/mom-bot` is the first conforming receiver. It is documented in §11. The contract does not depend on, reference, or couple to any implementation detail of mom-bot beyond what is described in the normative sections below.

---

## 2. Payload Schema

The producer sends an HTTP POST request to the configured receiver URL (see §9 for configuration). The request body is JSON-encoded and MUST set `Content-Type: application/json`.

### Fields

| Field | Type | Description |
|---|---|---|
| `discord_id` | string | Discord snowflake ID of the member whose assignment changed. Receivers MUST treat this as an opaque string; do not cast to integer. |
| `siege_id` | integer | Primary key of the siege record in the producer database. Provided for correlation and audit; receivers MAY ignore it beyond logging. |
| `day_number` | integer | The attack-day number being assigned or unassigned. Currently `1` or `2`; the contract allows any positive integer so that future days require no schema change. |
| `action` | string (enum) | `"assign"` — the member has been placed on this day. `"unassign"` — the member has been removed from this day. No other values are legal. |
| `assigned_at` | string | ISO-8601 UTC timestamp representing when the assignment change was recorded. Producers MUST provide millisecond precision at minimum — the form with `.SSS` suffix, e.g. `"2026-05-14T13:52:18.247Z"`. Producers MAY use microsecond precision if higher resolution is needed. Receivers use this as a monotonic ordering token (see §7). |
| `correlation_id` | string | UUID v4. Scoping and retry semantics are defined in §8. |

### Example Payload

```json
{
  "discord_id": "123456789012345678",
  "siege_id": 42,
  "day_number": 1,
  "action": "assign",
  "assigned_at": "2026-05-14T13:52:18.247Z",
  "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

> `discord_id` `123456789012345678` is illustrative only. Real values are 17–19 digit decimal strings issued by Discord.

---

## 3. Response Schema

The receiver MUST respond with HTTP `200` and a JSON body for all outcomes that the producer can meaningfully act on (including `skipped` and `partial`). Non-`200` responses trigger the retry logic in §5.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `status` | string (enum) | Yes | One of `"applied"`, `"partial"`, `"skipped"`, `"failed"`. See semantics below. |
| `added` | list[string] | Yes | Role names (strings) that were successfully added. Empty list if none were added. |
| `removed` | list[string] | Yes | Role names (strings) that were successfully removed. Empty list if none were removed. |
| `reason` | string (enum) | No | Present when `status` is not `"applied"`. See full enumeration below. |
| `last_assigned_at` | string | No | ISO-8601 UTC. MUST be present when `reason` is `"stale_write"`. Contains the receiver's stored `assigned_at` for this `discord_id`. |

### `status` Semantics

- **`applied`** — all role operations completed successfully. `reason` MUST NOT be present.
- **`partial`** — at least one role operation succeeded and at least one failed. `added` and `removed` reflect only what actually happened. `reason` MUST be present and MUST name the failure. Receivers MUST NOT report `failed` when any operation succeeded.
- **`skipped`** — no role operations were attempted. The receiver deliberately declined to act (e.g. stale write, exact replay). `reason` MUST be present.
- **`failed`** — no role operations succeeded and none were attempted or all that were attempted failed. `reason` SHOULD be present.

**`skipped` vs `failed` disambiguation:** `skipped` means the receiver **deliberately declined to attempt** any role operation (e.g. member not in guild, role not seeded, stale write, already in target state). `failed` means the receiver **attempted at least one** role operation and got an unrecoverable error from Discord with zero successful operations (use `partial` if at least one succeeded). If no role operation is attempted, the response is never `failed`.

### `reason` Enumeration

The following are the only legal values. No other values are defined by this contract.

| Value | Meaning |
|---|---|
| `member_not_in_guild` | The `discord_id` is not a member of the receiver's configured guild. |
| `role_not_seeded` | The receiver has no entry in its day-role map for `day_number`. |
| `already_has_role` | `action` was `"assign"` but the member already holds the target role. |
| `already_lacks_role` | `action` was `"unassign"` but the member does not hold the target role. |
| `stale_write` | `assigned_at` is older than the receiver's stored `last_assigned_at` for this `discord_id` and the idempotency key differs (not an exact replay — see §6 and §7). `last_assigned_at` MUST be included in the response. |
| `remove_of_other_day_failed_403` | Partial outcome: the assign operation for the requested day succeeded, but removing the other day's role failed with HTTP 403 (typically a role hierarchy issue — see §10 for preflight guidance). |

### Example Responses

**Example 1 — `applied`**

```json
{
  "status": "applied",
  "added": ["Day 1"],
  "removed": ["Day 2"]
}
```

**Example 2 — `partial` with `remove_of_other_day_failed_403`**

```json
{
  "status": "partial",
  "added": ["Day 1"],
  "removed": [],
  "reason": "remove_of_other_day_failed_403"
}
```

**Example 3 — `skipped` with `stale_write`**

```json
{
  "status": "skipped",
  "added": [],
  "removed": [],
  "reason": "stale_write",
  "last_assigned_at": "2026-05-14T20:00:00Z"
}
```

---

## 4. Authentication

Every request from the producer to the receiver MUST include an `Authorization` header of the form:

```
Authorization: Bearer <token>
```

The token is a shared secret. The producer reads its outbound token from its own environment; the receiver reads its expected token from its own environment. This contract places no constraint on storage mechanism, rotation policy, or naming convention on either side — those are implementation concerns. The recommended env var name is `DAY_ROLE_SYNC_TOKEN` on both sides for symmetry, but this is not normative.

The receiver MUST respond `401 Unauthorized` if the header is absent or the token does not match. A `401` response MUST NOT carry a `WWW-Authenticate` challenge that reveals the expected scheme beyond what is already public via this contract.

The producer MUST transmit requests over HTTPS. Plaintext HTTP connections MUST NOT be used in production.

---

## 5. Retry Semantics

On receiving a `5xx` response, the producer MUST attempt exactly one retry after a 500ms delay. The retry MUST carry the same payload and the same `correlation_id` as the original request (see §8).

**4xx responses are not retried.** A `4xx` response indicates a client-side error (malformed payload, auth failure, or a permanent receiver-side rejection) that the producer cannot resolve by resending. The producer MUST treat `4xx` as a terminal failure for this delivery attempt.

There is no retry queue, no dead-letter queue, and no scheduled redrive. Delivery drops are accepted under this contract. Out-of-band reconciliation (e.g. periodic full-sync) is out of scope for this contract and should be revisited if observability data reveals unacceptable drift.

**Observability on exhaustion (SHOULD).** Because the contract drops on retry exhaustion with no DLQ or reconciliation path, drift between producer state and receiver state is otherwise silent. Producers SHOULD emit a structured event (metric, log, or alert) when the single retry is exhausted, including at minimum `correlation_id`, `discord_id`, `assigned_at`, `action`, and the final HTTP status/error from both attempts. Operators of receivers SHOULD likewise emit structured events for `status: failed` and `status: partial` responses so drift is detectable from either side.

---

## 6. Idempotency Rules

The idempotency key for a delivery is the tuple:

```
(discord_id, assigned_at, action, day_number)
```

If the receiver receives a payload where all four components match the receiver's stored last entry for that `discord_id`, the delivery is an **exact replay**. On an exact replay the receiver MUST:

1. Return the original stored response unchanged.
2. NOT invoke any role operations.
3. NOT update any stored state.

Receivers SHOULD persist idempotency state across restarts so that a receiver reboot does not cause duplicate role operations on re-delivery of the same payload.

---

## 7. Ordering Rules

`assigned_at` is the monotonic ordering token for a given `discord_id`. The receiver applies the following decision tree on every incoming payload:

1. **Exact replay** — all four components of the idempotency key match the stored last entry for this `discord_id`. Apply §6 (return cached response, no ops, no state update).
2. **Stale write** — `assigned_at` is strictly less than the receiver's stored `last_assigned_at` for this `discord_id`, AND it is not an exact replay (i.e. at least one of `action` or `day_number` differs). The receiver MUST return `{status: "skipped", reason: "stale_write", last_assigned_at: <stored>, added: [], removed: []}` without invoking role operations and without updating stored state.
3. **Fresh write** — `assigned_at` is greater than or equal to the stored `last_assigned_at` (and it is not an exact replay). The receiver MUST apply role operations and update stored state.

**Producer monotonicity guarantee.** For a given `discord_id`, the producer MUST generate strictly monotonically increasing `assigned_at` values across distinct payloads — i.e. each new payload's `assigned_at` is strictly greater than the previous one for that `discord_id`. Producers SHOULD source `assigned_at` from a monotonic clock (e.g. database `clock_timestamp()` in PostgreSQL, or `time.monotonic_ns()` rounded to the chosen precision) rather than wall-clock time, to avoid violations under clock skew or backwards adjustment. Millisecond precision (§2) gives sufficient headroom for typical bulk fan-out; producers expecting >1,000 distinct payloads per `discord_id` per second MUST use microsecond precision.

---

## 8. `correlation_id` Conventions

`correlation_id` is a UUID v4 string generated by the producer per user action, not per HTTP call.

- **Shared across bulk fan-out.** If one user action (e.g. an admin bulk-assign) causes the producer to emit N webhook calls for N distinct members, all N calls MUST carry the same `correlation_id`. This allows downstream operators to correlate all effects of a single action in receiver logs.
- **Preserved across retries.** The single 5xx retry (§5) MUST carry the same `correlation_id` as the original attempt. Do not generate a new UUID for the retry.
- **Receivers MUST log `correlation_id`** in every structured log line emitted during processing of a call. See §10 for recommended observability conventions.

**Relationship to producer-internal batch identifiers (informative).** Some producers maintain their own internal batch/transaction identifiers (for example, `rsl-siege-manager`'s `NotificationBatch.id` row used for the existing reminder-posting flow). `correlation_id` on this contract is **independent of any such internal identifier** — the producer generates one `uuid4` when the operator initiates a user action, and that same value is attached to every webhook call in the resulting fan-out (consistent with the normative rules above). A new `uuid4` is generated only when a new user action begins. Producers MAY include their internal batch identifier as an additional structured-log field for cross-referencing, but MUST NOT substitute it for `correlation_id` on the wire.

---

## 9. Configuration

The following env vars govern the webhook feature on the producer side.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DAY_ROLE_SYNC_ENABLED` | No | `false` | Cross-repo feature gate and rollback kill switch. MUST default to `false`. When `false`, the producer MUST NOT send any webhook calls regardless of other configuration. Set to `true` to enable. |
| `DAY_ROLE_SYNC_URL` | Yes (when enabled) | — | Full URL of the receiver endpoint (e.g. `https://mom-bot.example.com/api/internal/role-sync`). If unset or empty while `DAY_ROLE_SYNC_ENABLED=true`, the producer SHOULD log a warning and treat the feature as disabled. |
| Bearer secret (producer side) | Yes (when enabled) | — | Env var holding the outbound bearer token. Name is implementation-defined; `DAY_ROLE_SYNC_TOKEN` is recommended. |

Receiver-side configuration (env var names, guild IDs, role map population) is outside the scope of this contract. Each receiver implementation documents its own configuration requirements.

`DAY_ROLE_SYNC_ENABLED` defaulting to `false` is a deliberate safety property: a deployment that has not yet configured a receiver will not attempt webhook delivery and will not produce spurious errors.

> **Implementer note — `rsl-siege-manager` (this repo) only.** The following guidance is normative for contributors to this repo and non-normative for any other producer that adopts this contract.
>
> The producer implementation lives in the `backend/` service, not `bot/`. The "backend ↔ bot HTTP-API-only" boundary is preserved — the existing `bot/app/http_api.py` inbound surface is untouched. `DAY_ROLE_SYNC_URL` and `DAY_ROLE_SYNC_ENABLED` are read by `backend/`; the outbound HTTP call follows the existing `backend/app/services/bot_client.py` httpx + Bearer pattern. This guidance is specific to this repo and does not constrain other producers that may adopt this contract in the future.

---

## 10. Implementer Guidance (Non-Normative)

> **This section is non-normative.** Nothing here is a contract requirement. These are recommendations derived from experience building the first conforming receiver (`glitchwerks/mom-bot`). Implementers MAY deviate from any guidance here without violating the contract.

### `discord_id = None` — skip at sender layer

Producers fanning out from member records SHOULD skip records where `discord_id` is null or empty rather than sending a malformed payload. Sending a payload with a missing `discord_id` produces a `400` from any well-implemented receiver, which wastes a retry slot and pollutes logs. Filter upstream.

### Partial-response dwell

When the producer receives `status: "partial"` from the receiver, it SHOULD NOT immediately retry the same payload. A retry of the same payload would hit the exact-replay path (§6) and return the same cached partial response — it would not resolve the underlying failure. Treat `partial` as a soft failure and route it to operator alerting for manual investigation.

### Role-hierarchy preflight (receiver-side)

A common cause of `remove_of_other_day_failed_403` is that the bot's own highest role does not outrank the day roles it needs to remove. This is a Discord permission constraint: a bot can only modify roles that are lower in the hierarchy than its own highest role. See [Discord Permissions documentation](https://docs.discord.com/developers/topics/permissions) for the role-ordering rules.

Receivers SHOULD verify at startup that the bot's highest role outranks every role present in the day-role map. If this check fails, the receiver SHOULD refuse to start (or log a fatal-level warning) rather than discovering the hierarchy problem at runtime as a 403 during a live assignment. Catching this at startup avoids silent partial failures that are difficult to diagnose under load.

### Observability conventions

Receivers SHOULD emit one structured log entry per webhook call. The log entry SHOULD include at minimum:

- `correlation_id` — from the payload (§8)
- `discord_id` — from the payload
- `siege_id` — from the payload
- `day_number` — from the payload
- `action` — from the payload
- `assigned_at` — from the payload
- `status` — from the response
- `added` — role names added
- `removed` — role names removed
- `attempt` — `1` for a fresh delivery, `2` or higher for a replay (exact-replay path under §6)

Emitting the full correlation between input and output in a single log line makes cross-repo incident correlation tractable.

### Idempotency state persistence (receiver-side)

Receivers SHOULD persist idempotency state (the last processed `(discord_id, assigned_at, action, day_number)` tuple and the corresponding response) in durable storage across restarts. `glitchwerks/mom-bot`'s first implementation uses a SQLite table named `member_role_sync_state` for this purpose.

---

## 11. Current Implementer

`glitchwerks/mom-bot` is the first conforming receiver of this webhook contract. It exposes an HTTP endpoint that accepts the payload defined in §2, applies the role operations described in §3, and responds with the structured response schema. Implementation details are tracked under the mom-bot epic at [glitchwerks/mom-bot#6](https://github.com/glitchwerks/mom-bot/issues/6).

**Switching receivers on the wire is a producer-side config change only** — `DAY_ROLE_SYNC_URL` and the bearer secret. **The new receiver, however, has its own provisioning work** that the contract does not eliminate: at minimum, the receiver must be a member of the same Discord guild as members it will toggle roles for, must have seeded its own `day_role_map`-equivalent table (or equivalent role→day_number lookup), and must have completed any role-hierarchy preflight applicable to its environment (§10). This contract guarantees wire portability, not operational portability.
