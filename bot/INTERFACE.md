# Bot HTTP Sidecar — Interface Contract

This document is the human-readable contract between `backend/` and the Discord bot
sidecar (`bot/`). It is specific enough that an alternative sidecar implementation
(e.g. mom-bot, or an integration-test stub) can be built against it without reading
the bundled bot's source. Motivated by umbrella issue
[#347](https://github.com/glitchwerks/rsl-siege-manager/issues/347); deployment
shapes, URL resolution, auth, ingress posture, and Key Vault secrets are in
[`docs/bot-seam.md`](../docs/bot-seam.md).

> **Authority.** This document is human-readable documentation. The normative source
> of truth is the integration test suite at
> `backend/tests/integration/sidecar/`. When this document and the tests disagree,
> the tests win and this document gets updated.

---

## Quick start: implementing a sidecar bot

A navigation checklist for anyone building an alternative sidecar (e.g. mom-bot or
an integration-test stub). Each item links to the normative section that elaborates.

- **Implement Bearer auth** — validate the shared `BOT_API_KEY` on every protected
  endpoint. Two distinct failure modes are required: 401 when the `Authorization`
  header is present with a wrong token, 403 when the header is absent entirely.
  See [§ Authentication](#authentication).

- **Implement the 7 sidecar endpoints** — `GET /api/version`, `GET /api/health`,
  `POST /api/notify`, `POST /api/post-message`, `POST /api/post-image`,
  `GET /api/members`, and `GET /api/members/{discord_user_id}` — with the exact
  request and response shapes documented. See [§ Endpoint reference](#endpoint-reference).

- **Match the `id` vs `discord_id` field naming** — `GET /api/members` returns `id`
  for the Discord snowflake; `GET /api/members/{discord_user_id}` returns `discord_id`.
  This distinction is load-bearing and not arbitrary; renaming either field breaks
  existing consumers. See the [rationale note in § Endpoint reference](#get-apimembers).

- **Honour the singleton Discord-token constraint** — the bundled bot and any
  alternate sidecar sharing the same Discord token cannot coexist. Operators must
  exclude the bundled bot when deploying an alternate sidecar.
  See [§ Discord coupling](#discord-coupling).

- **Translate Discord errors to HTTP status codes** — `discord.Forbidden` → 403,
  Discord 4xx → 502, Discord 5xx or timeout → 503. Correct translation is required
  for `BotClient` error-handling paths to behave as designed.
  See [§ Error semantics](#error-semantics).

- **Verify against the integration test suite** — `backend/tests/integration/sidecar/`
  is the executable contract. When this document and the tests disagree, the tests win.

- _(Optional)_ **Implement the 2 backend preference endpoint callers** —
  `GET /api/members/me/preferences` and `PUT /api/members/me/preferences` live on the
  backend, not the sidecar. If the sidecar exposes member-preference UI, implement the
  callers; otherwise sidecar conformance does not require them.
  See [§ Endpoint reference — preferences](#get-apimembersmepreferences-and-put-apimembersmepreferences).

_For everything else (versioning, conformance table, glossary), see the full doc below._

---

## Process model

`SiegeBot` (discord.py client) and the FastAPI HTTP sidecar run concurrently in the
same process via `asyncio.TaskGroup` (`bot/app/main.py:43-45`). The HTTP server
listens on `0.0.0.0:8001` (uvicorn). Backend communicates with the sidecar exclusively
over HTTP — it never imports or calls discord.py directly.

If either task in the TaskGroup crashes, its sibling is cancelled and the process
exits; the orchestrator (systemd, Container Apps, docker-compose) is responsible for
restart.

---

## Authentication

Every endpoint except `GET /api/version` and `GET /api/health` requires an HTTP
`Authorization` header carrying a Bearer token.

```
Authorization: Bearer <token>
```

| Side | Environment variable | Notes |
|---|---|---|
| Backend (caller) | `DISCORD_BOT_API_KEY` | Sent as the Bearer token |
| Bot sidecar (validator) | `BOT_API_KEY` | Compared using `secrets.compare_digest` |

The bundled sidecar uses `HTTPBearer(auto_error=True)` (FastAPI/Starlette default).
This produces **two distinct failure modes**:

**403 Forbidden — `Authorization` header absent entirely.**

```http
HTTP/1.1 403 Forbidden

{"detail": "Not authenticated"}
```

No `WWW-Authenticate` header is returned. This is the FastAPI `HTTPBearer` default
when no header is present.

**401 Unauthorized — header present but scheme is not `Bearer`, or token is wrong.**

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer

{"detail": "Invalid API key"}
```

The `WWW-Authenticate: Bearer` header is set by `verify_api_key`
(`bot/app/http_api.py:161-168`), which only runs when the header is present.

> **Conformance note.** Alternative sidecars MUST return 401 on a wrong token and
> MAY return either 401 or 403 on a missing header. The backend's `BotClient` does
> not inspect the specific auth-failure code — it treats any 4xx as an auth failure.

Source: `bot/app/http_api.py:142`, `bot/app/http_api.py:161-168`

---

## Discord coupling

The sidecar operates against a single Discord guild identified by the `DISCORD_GUILD_ID`
environment variable. Callers should be aware of the following coupling:

- `discord_id` values are Discord snowflakes (numeric strings, e.g. `"123456789012345678"`).
- `channel_name` and `username` are resolved within the configured guild only.
- Image CDN URLs returned by `POST /api/post-image` are Discord CDN URLs; their
  lifetime and access policy are governed by Discord, not siege-web.
- The bundled `bot/` and any alternate sidecar sharing the same Discord token cannot
  coexist. An operator replacing the sidecar must exclude the bundled bot via the
  `useExternalSidecar` Bicep parameter (prod) or the `sidecar-external` docker-compose
  profile (local dev). See Step 4 of #347 for enforcement details.

---

## Error semantics

The sidecar produces two distinct error body shapes depending on where the error
originates.

**Handler-raised errors (401, 403, 404, 503).** These are raised explicitly by route
handlers or FastAPI dependencies via `HTTPException`. The body is:

```json
{"detail": "<human-readable string>"}
```

Example:

```json
{"detail": "Member 'SomeMember' not found in guild"}
```

**FastAPI request-validation errors (422).** When a request body, form field, or path
parameter is missing, the wrong type, or malformed JSON/form data, FastAPI's
request-validation layer raises `RequestValidationError` before the handler runs. The
body is a **list of objects**, not a string:

```json
{
  "detail": [
    {
      "loc": ["body", "username"],
      "msg": "field required",
      "type": "missing"
    }
  ]
}
```

Each element in the `detail` array has three keys: `loc` (path to the invalid field),
`msg` (human-readable description), and `type` (machine-readable error code).

Consumers that parse error bodies MUST handle both shapes. The status code is the
reliable discriminator: 422 always uses the list shape; 401/403/404/503 always use
the string shape.

| Status | Common trigger | Backend (`BotClient`) behaviour |
|---|---|---|
| 401 | Wrong or malformed Bearer token | `httpx.HTTPStatusError` propagates; most methods return `False` or `[]`; `get_member` re-raises |
| 403 | `Authorization` header absent; OR Discord-side permission denied (DM blocked, channel send permission missing) | Same as 401 |
| 404 | Channel or member not found in guild (see per-endpoint notes) | Same as 401 |
| 422 | Missing required field, wrong type, malformed JSON or form data, or invalid path parameter | Same as 401 |
| 502 | Discord API returned a 4xx error other than Forbidden or NotFound | Same as 401 |
| 503 | Bot not connected (`is_ready()` false), guild unavailable, Discord API 5xx error or timeout | Same as 401 |

`BotClient` swallows `httpx.HTTPError` in `notify`, `post_message`, `post_image`, and
`get_members`, returning `False`, `False`, `None`, and `[]` respectively on any HTTP
error. `get_member` does **not** swallow — it propagates `httpx.HTTPError` and raises
`AssertionError` if the response body violates the discriminated shape. Callers must
distinguish between a `None`/`False` return (transient sidecar failure) and a missing
`url` key (contract violation) when consuming `post_image`.

---

## Endpoint reference

### `GET /api/version`

Returns the sidecar version string. No authentication required.

**Request.** No parameters.

**Response — 200 OK.**

```json
{"version": "1.0.1+42.abc1234"}
```

The value is `<semver>+<BUILD_NUMBER>.<GIT_SHA[:7]>` when both `BUILD_NUMBER` and
`GIT_SHA` environment variables are present (CI-built images), or bare semver in
local development. Semver is read from `bot/VERSION` at startup; falls back to
`"unknown"` if the file is missing.

**Status codes.**

| Code | Condition |
|---|---|
| 200 | Always (file missing returns `{"version": "unknown"}`) |

Source: `bot/app/http_api.py:56-73`

---

### `GET /api/health`

Health probe. No authentication required.

**Request.** No parameters.

**Response — 200 OK.**

```json
{"status": "healthy", "bot_connected": true}
```

**Semantic.** `bot_connected` reflects the result of `is_ready()` on the discord.py
client at the moment the health handler runs (see `bot/app/http_api.py`'s health
handler). `is_ready()` returns `true` when the gateway WebSocket has completed the
identify handshake and the bot has received the initial guild state from Discord. It
returns `false` during startup and during any gateway reconnect or resume sequence.

**Advisory-only SLA.** `/api/health` is not a TOCTOU-free probe. `is_ready()` can
transition from `true` to `false` between the health check and the next call —
reconnects, gateway resumes, and transient network drops all cause this transition.
Container Apps liveness and readiness probes MAY use this endpoint as a coarse signal,
but consumers MUST still handle 503 on any subsequent protected-endpoint call.

**Consumer guidance when `bot_connected` is `false`.**

- Treat the value as a hint that the next authenticated call is likely to fail, not
  as a hard gate.
- Do not block the call — attempt it and apply the same retry-with-backoff strategy
  you would use on any 503 response.
- The `_get_bot()` dependency returns 503 with `{"detail": "Bot is not connected"}` on
  any protected endpoint when the bot is not ready. The global Discord exception handler
  returns 503 with `{"detail": "Discord temporarily unavailable"}` on Discord-side
  outages.

**Status codes.**

| Code | Condition |
|---|---|
| 200 | Always |

Source: `bot/app/http_api.py:76-79`

---

### `POST /api/notify`

Send a direct message (DM) to a guild member by username. Requires authentication.

**Request body — JSON.**

```json
{"username": "SomeMember", "message": "Your siege assignment is ready."}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | `string` | yes | Discord username of the target member (not display name) |
| `message` | `string` | yes | DM content |

**Response — 200 OK.**

```json
{"status": "sent"}
```

**Status codes.**

| Code | Condition |
|---|---|
| 200 | DM delivered successfully |
| 401 | Wrong or malformed Bearer token |
| 403 | `Authorization` header absent (`{"detail": "Not authenticated"}`); OR Discord-side permission denied — DM closed, bot lacks permissions (`{"detail": "Discord permission denied"}`) — see body to distinguish |
| 404 | See below |
| 422 | Missing required field, wrong type, or malformed JSON |
| 502 | Discord API returned a 4xx error other than Forbidden or NotFound |
| 503 | Bot is not connected to Discord; OR Discord API returned a 5xx error or timed out |

**404 causes.** The bundled sidecar raises 404 for any `ValueError` from `bot.send_dm`
(`bot/app/http_api.py:216-217`). The only `ValueError` that `send_dm` raises is:

- **Username not found in guild cache.** The member's `name` (exact, case-insensitive)
  does not match any member in the locally cached guild member list
  (`bot/app/discord_client.py:25-29`).

There is exactly one 404 cause. Delivery failures after the member is found are
translated by global exception handlers (`bot/app/http_api.py:51-139`):

- **DM blocked / member has DMs closed:** `discord.Forbidden` → **403** with
  `{"detail": "Discord permission denied"}`.
- **Other Discord 4xx:** `discord.HTTPException` (status < 500) → **502** with
  `{"detail": "Upstream Discord error"}`.
- **Discord 5xx or timeout:** `discord.HTTPException` (status >= 500) or
  `asyncio.TimeoutError` → **503** with `{"detail": "Discord temporarily unavailable"}`.

Consumers CAN now distinguish DM-blocked / permission-denied (403) from name-not-in-cache
(404): the 403 is a send-time failure after the member was resolved; the 404 is a
name-resolution failure before any send attempt.

Source: `bot/app/http_api.py:207-218`, `bot/app/http_api.py:51-139`, `bot/app/discord_client.py:22-31`

---

### `POST /api/post-message`

Post a text message to a guild channel by name. Requires authentication.

**Request body — JSON.**

```json
{"channel_name": "siege-assignments", "message": "Day 1 roster is locked."}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `channel_name` | `string` | yes | Discord channel name (exact match, without `#`) |
| `message` | `string` | yes | Message content |

**Response — 200 OK.**

```json
{"status": "sent"}
```

**Status codes.**

| Code | Condition |
|---|---|
| 200 | Message posted successfully |
| 401 | Wrong or malformed Bearer token |
| 403 | `Authorization` header absent (`{"detail": "Not authenticated"}`); OR Discord-side permission denied — bot lacks channel send permission (`{"detail": "Discord permission denied"}`) — see body to distinguish |
| 404 | See below |
| 422 | Missing required field, wrong type, or malformed JSON |
| 502 | Discord API returned a 4xx error other than Forbidden or NotFound |
| 503 | Bot is not connected to Discord; OR Discord API returned a 5xx error or timed out |

**404 causes.** The bundled sidecar raises 404 for any `ValueError` from `bot.post_message`
(`bot/app/http_api.py:230-231`). The only `ValueError` that `post_message` raises is:

- **Channel not found by name.** No `TextChannel` in the guild's channel list has a
  name exactly matching `channel_name` (`bot/app/discord_client.py:36-41`).

**Channel-resolution failures collapse to 404.** The 404 fires on name resolution,
before any send attempt. Alternative sidecars MUST also collapse all
channel-resolution-class failures to 404 and MUST NOT split them into separate 403/404
codes. However, send-time failures (after the channel is resolved) are translated by
global exception handlers (`bot/app/http_api.py:51-139`):

- **Channel found but bot lacks send permission:** `discord.Forbidden` → **403** with
  `{"detail": "Discord permission denied"}`. This is a send-time failure,
  not a name-resolution failure.
- **Other Discord 4xx:** `discord.HTTPException` (status < 500) → **502** with
  `{"detail": "Upstream Discord error"}`.
- **Discord 5xx or timeout:** `discord.HTTPException` (status >= 500) or
  `asyncio.TimeoutError` → **503** with `{"detail": "Discord temporarily unavailable"}`.

Consumers CAN now distinguish send-time permission denial (403) from name-not-in-guild
(404): the 403 is a send-time failure after the channel was resolved; the 404 is a
name-resolution failure before any send attempt.

Source: `bot/app/http_api.py:221-232`, `bot/app/http_api.py:51-139`, `bot/app/discord_client.py:33-42`

---

### `POST /api/post-image`

Upload an image and post it to a guild channel. Returns the Discord CDN URL of the
posted attachment. Requires authentication.

**Request body — `multipart/form-data`.** The `channel_name` field and the `file`
field must both be sent as multipart form parts in the same request body. `channel_name`
is a `Form(...)` field — it is **not** a query parameter.

| Part | Type | Required | Description |
|---|---|---|---|
| `file` | binary (UploadFile) | yes | PNG image bytes; filename defaults to `image.png` if not provided |
| `channel_name` | `string` | yes | Discord channel name to post into |

Example using `httpx`:

```python
client.post(
    "/api/post-image",
    data={"channel_name": "siege-images"},
    files={"file": ("assignment.png", image_bytes, "image/png")},
)
```

**Response — 200 OK.**

```json
{"status": "sent", "url": "https://cdn.discordapp.com/attachments/.../assignment.png"}
```

| Field | Type | Description |
|---|---|---|
| `status` | `string` | Always `"sent"` |
| `url` | `string` | Discord CDN URL of the uploaded attachment |

**Status codes.**

| Code | Condition |
|---|---|
| 200 | Image posted; `url` is the Discord CDN link |
| 401 | Wrong or malformed Bearer token |
| 403 | `Authorization` header absent (`{"detail": "Not authenticated"}`); OR Discord-side permission denied — bot lacks channel send permission (`{"detail": "Discord permission denied"}`) — see body to distinguish |
| 404 | See below |
| 422 | Missing `channel_name` form field, missing `file` part, or malformed multipart data |
| 502 | Discord API returned a 4xx error other than Forbidden or NotFound |
| 503 | Bot is not connected to Discord; OR Discord API returned a 5xx error or timed out |

**404 causes.** The bundled sidecar raises 404 for any `ValueError` from `bot.post_image`
(`bot/app/http_api.py:246-247`). The only `ValueError` that `post_image` raises is:

- **Channel not found by name.** No `TextChannel` in the guild's channel list has a
  name exactly matching `channel_name` (`bot/app/discord_client.py:52-57`).

The same collapse rule applies as for `POST /api/post-message`: all channel-resolution-class
failures (name not found, and any other pre-send resolution failure) collapse to 404.
Alternative sidecars MUST conform to this collapse. However, send-time failures (after
the channel is resolved) are translated by global exception handlers
(`bot/app/http_api.py:51-139`):

- **Channel found but bot lacks send permission:** `discord.Forbidden` → **403** with
  `{"detail": "Discord permission denied"}`.
- **Other Discord 4xx:** `discord.HTTPException` (status < 500) → **502** with
  `{"detail": "Upstream Discord error"}`.
- **Discord 5xx or timeout:** `discord.HTTPException` (status >= 500) or
  `asyncio.TimeoutError` → **503** with `{"detail": "Discord temporarily unavailable"}`.

Consumers CAN now distinguish send-time permission denial (403) from name-not-in-guild
(404).

Source: `bot/app/http_api.py:235-248`, `bot/app/http_api.py:51-139`, `bot/app/discord_client.py:44-59`

---

### `GET /api/members`

Retrieve the full guild member list. Requires authentication.

**Request.** No parameters.

**Response — 200 OK.**

A JSON array. Each element has exactly three keys as returned by `SiegeBot.get_members()`
(`bot/app/discord_client.py:61-71`):

```json
[
  {
    "id": "123456789012345678",
    "username": "SomeMember",
    "display_name": "Some Member"
  }
]
```

**Element shape.**

| Key | Type | Description |
|---|---|---|
| `id` | `string` | Discord snowflake (numeric string) |
| `username` | `string` | Discord username (`member.name`) |
| `display_name` | `string` | Guild display name (`member.display_name`) |

All three keys are always present for every element. The array may be empty if the
guild has no cached members. Roles are not included in this endpoint's response — use
`GET /api/members/{discord_user_id}` for role data.

> **Note.** The key for the Discord ID in this response is `id`, not `discord_id`.
> The `GET /api/members/{discord_user_id}` endpoint uses `discord_id`. This
> inconsistency is load-bearing: `GET /api/members` returns a lightweight roster
> (no roles) whose `id` field follows the generic "primary identifier" naming
> convention from the original schema, while `GET /api/members/{discord_user_id}`
> returns the full member envelope introduced when the discriminated shape was added
> (PR #415 / Step 1 of #347), where `discord_id` is the explicit, self-documenting
> form. Renaming either field would break existing consumers. Alternative sidecars
> MUST use `id` here and `discord_id` in the single-member endpoint, exactly as
> documented.

**Response size.** The bundled sidecar returns the full guild member list in one
response with no pagination. Guilds with thousands of members produce responses
several MB in size; consumers MUST size their HTTP client timeout and memory
accordingly. An alternate sidecar MAY implement pagination, but the bundled bot does
not, and `BotClient.get_members()` assumes one-shot delivery.

**Status codes.**

| Code | Condition |
|---|---|
| 200 | Member list returned (may be empty) |
| 401 | Wrong or malformed Bearer token |
| 403 | `Authorization` header absent |
| 503 | Bot is not connected to Discord |

Source: `bot/app/http_api.py:126-132`, `bot/app/discord_client.py:61-71`

---

### `GET /api/members/{discord_user_id}`

Look up a single guild member by Discord user ID. Requires authentication.

**Path parameter.**

| Parameter | Type | Description |
|---|---|---|
| `discord_user_id` | `string` | Discord snowflake (numeric string, e.g. `"123456789012345678"`) |

The path parameter is validated against the pattern `^\d+$` by FastAPI before the
handler runs. A non-numeric value (e.g. `"abc"`) returns 422 with the standard
validation envelope — it is never passed to `int()` in the handler.

**Response — 200 OK (member found).**

```json
{
  "is_member": true,
  "discord_id": "123456789012345678",
  "username": "SomeMember",
  "display_name": "Some Member",
  "roles": ["987654321098765432"],
  "role_names": ["Clan Deputies"]
}
```

**Response — 200 OK (not a guild member).**

```json
{
  "is_member": false,
  "discord_id": null,
  "username": null,
  "display_name": null,
  "roles": null,
  "role_names": null
}
```

The `is_member: bool` field is the discriminator. All six keys are always present
regardless of membership status. When `is_member` is `false`, the remaining five
fields are `null`. The `@everyone` role is excluded from `roles` and `role_names`.

**Required key set** (asserted by `BotClient.get_member` — `backend/app/services/bot_client.py:120-131`):

| Key | Type when member | Type when not member |
|---|---|---|
| `is_member` | `bool` (`true`) | `bool` (`false`) |
| `discord_id` | `string` | `null` |
| `username` | `string` | `null` |
| `display_name` | `string` | `null` |
| `roles` | `string[]` | `null` |
| `role_names` | `string[]` | `null` |

**Status codes.**

| Code | Condition |
|---|---|
| 200 | Always when the Discord API responds (including "not a member") |
| 401 | Wrong or malformed Bearer token |
| 403 | `Authorization` header absent |
| 422 | Non-numeric `discord_user_id` path parameter (e.g. `"abc"`) |
| 503 | Bot is not connected, guild is unavailable, or Discord API returns an unexpected error |

Source: `bot/app/http_api.py:135-172`

---

### `GET /api/members/me/preferences` and `PUT /api/members/me/preferences`

Read or replace the post-condition preferences for the caller's associated
siege-web `Member` record. These endpoints live on the backend (`backend/`),
not the bot sidecar — they are documented here because the bot is the primary
caller and the `X-Acting-Discord-Id` header is the mechanism by which the bot
identifies which member's preferences to operate on.

#### Auth model

Two auth paths are accepted.

**Service-token path (bot callers).** The request must carry both:

```
Authorization: Bearer <token>
X-Acting-Discord-Id: <discord_snowflake>
```

The backend resolves the `Member` whose `discord_id` matches the header value.
A service-token request **without** `X-Acting-Discord-Id` returns 401 on
`/me/*` endpoints. Other (non-`/me`) endpoints used by the bot are unaffected —
they do not require the header and continue to work as before.

**Cookie-session path (browser callers).** The `X-Acting-Discord-Id` header is
ignored entirely when a valid session cookie is present. The cookie's member ID
is authoritative; the header is not consulted and does not change the subject.

#### Subject resolution

| Auth method | Subject used |
|---|---|
| Cookie session | Session member's `member_id` |
| Service token + `X-Acting-Discord-Id` | `Member.id` whose `discord_id` matches the header |
| Service token, no header | 401 — no unambiguous subject |
| Cookie session + `X-Acting-Discord-Id` | Cookie wins; header ignored |

#### Error codes

| Code | Condition |
|---|---|
| 200 | Success |
| 401 | Service-token request missing `X-Acting-Discord-Id` |
| 404 | `X-Acting-Discord-Id` value does not match any `Member.discord_id` |
| 404 | `PUT` includes a `post_condition_id` that does not exist |

#### Replace-all semantics on `PUT`

`PUT /api/members/me/preferences` replaces the member's full preference set
with exactly the IDs submitted. There is no `PATCH` endpoint. Clients needing
add/remove UX **should** implement a multi-select flow (e.g. Discord
select-menu component) rather than read-modify-write, which is subject to race
conditions.

**Request body.**

```json
{"post_condition_ids": [1, 3, 7]}
```

An empty list clears all preferences:

```json
{"post_condition_ids": []}
```

**Response — 200 OK.** `list[PostConditionResponse]` — the member's updated
preference set (may be `[]`).

`PostConditionResponse` fields:

| Field | Type | Description |
|---|---|---|
| `id` | `int` | Stable identifier, never reused |
| `description` | `string` | Human-readable condition text |
| `stronghold_level` | `int` | One of: `1`, `2`, `3` |
| `condition_type` | `string` | One of: `role`, `affinity`, `faction`, `league`, `rarity`, `effect`, `other` — enforced by a CHECK constraint; the set is closed |

```json
[
  {"id": 5,  "description": "Only HP Champions can be used.",        "stronghold_level": 1, "condition_type": "role"},
  {"id": 12, "description": "Only Barbarian Champions can be used.", "stronghold_level": 1, "condition_type": "faction"}
]
```

#### Stronghold-level filtering (client concern)

The backend stores preferences with no level constraint. Clients **should**
filter `GET /api/post-conditions?stronghold_level=N` by the clan's current
stronghold level before populating select menus so members only see conditions
relevant to their siege tier. Level-filtering is a client UX concern, not a
backend constraint on stored preferences.

#### Response presentation for Discord

The backend returns `list[PostConditionResponse]`. Clients **should** format
the list as a bullet list in Discord ephemeral replies (one condition per line)
for readability.

---

## Concurrency and idempotency

The bundled sidecar provides no ordering, deduplication, or idempotency guarantees.
Consumers are responsible for deduplication, retry-on-failure, and post-mortem
reconciliation. Rate-limit information is not surfaced through the contract —
consumers MUST treat 502s as opaque upstream failures.

**`POST /api/post-image` — ordering.** No ordering guarantee. Two simultaneous calls
targeting the same channel may be posted in any order. If message ordering matters,
consumers MUST serialize calls at the call site.

**`POST /api/notify` — deduplication.** No deduplication. Two identical calls arriving
within milliseconds result in two DMs being sent. The bundled bot does not track
recently-dispatched notifications; consumers that require deduplication MUST track
sent IDs themselves.

**`GET /api/members` — coherence.** Best-effort snapshot of the local guild member
cache. Reading during a member-cache resync may return a partial or stale result.
Consumers MUST treat a single response as a point-in-time snapshot, not a
transactionally consistent view.

**`/api/health` and the call it predicts.** The advisory-only SLA described in the
`GET /api/health` section above covers this case. See that section for consumer
guidance.

**Discord rate-limit pass-through.** When Discord returns HTTP 429 (rate-limited),
the global `_handle_discord_http_exception` handler in `bot/app/http_api.py`
translates it to **502** with `{"detail": "Upstream Discord error"}`. The
`Retry-After` header that Discord includes in its 429 response is **not propagated** —
the sidecar returns a generic 502 with no timing information. Consumers MUST treat 502
as an opaque upstream failure and apply their own backoff. If surfacing rate-limit
timing to callers becomes necessary, that is a follow-up enhancement outside this
contract version.

---

## Replaceability requirements

An alternative sidecar conforming to this contract MUST satisfy every row in the
following conformance table. Each row names a concrete stimulus and the expected
observable response. A conformance test can implement this table mechanically.

### `GET /api/version`

| Stimulus | Expected status | Expected body | Expected headers |
|---|---|---|---|
| `GET /api/version` (no auth header) | 200 | `{"version": "<non-empty string>"}` | — |

### `GET /api/health`

| Stimulus | Expected status | Expected body | Expected headers |
|---|---|---|---|
| `GET /api/health` (no auth header) | 200 | `{"status": "healthy", "bot_connected": <bool>}` | — |
| Consumer calls `/api/health` immediately followed by a protected endpoint | No guarantee | `bot_connected: true` in the health response does not imply the next call returns 2xx | — |

### Authentication (all protected endpoints)

| Stimulus | Expected status | Expected body | Expected headers |
|---|---|---|---|
| Valid-path request, `Authorization` header absent | 401 or 403 | `{"detail": "<string>"}` | — |
| Valid-path request, header present with wrong token | 401 | `{"detail": "<string>"}` | `WWW-Authenticate: Bearer` |

### `POST /api/notify`

| Stimulus | Expected status | Expected body | Expected headers |
|---|---|---|---|
| Valid auth, body `{"username": "<existing-member>", "message": "x"}` | 200 | `{"status": "sent"}` | — |
| Valid auth, body `{"username": "<non-member>", "message": "x"}` | 404 | `{"detail": "<string>"}` | — |
| Valid auth, body missing `username` field | 422 | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | — |
| Valid auth, body missing `message` field | 422 | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | — |
| Valid auth, bot not connected | 503 | `{"detail": "<string>"}` | — |
| Valid auth, body valid, member found but DMs blocked | 403 | `{"detail": "Discord permission denied"}` | — |
| Valid auth, body valid, Discord API returns 4xx other than 403/404 | 502 | `{"detail": "Upstream Discord error"}` | — |
| Valid auth, body valid, Discord API returns 5xx or times out | 503 | `{"detail": "Discord temporarily unavailable"}` | — |

### `POST /api/post-message`

| Stimulus | Expected status | Expected body | Expected headers |
|---|---|---|---|
| Valid auth, body `{"channel_name": "<existing-channel>", "message": "x"}` | 200 | `{"status": "sent"}` | — |
| Valid auth, body `{"channel_name": "<non-existent-channel>", "message": "x"}` | 404 | `{"detail": "<string>"}` | — |
| Valid auth, body missing `channel_name` field | 422 | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | — |
| Valid auth, bot not connected | 503 | `{"detail": "<string>"}` | — |
| Valid auth, body valid, channel found but bot lacks send permission | 403 | `{"detail": "Discord permission denied"}` | — |
| Valid auth, body valid, Discord API returns 4xx other than 403/404 | 502 | `{"detail": "Upstream Discord error"}` | — |
| Valid auth, body valid, Discord API returns 5xx or times out | 503 | `{"detail": "Discord temporarily unavailable"}` | — |

### `POST /api/post-image`

| Stimulus | Expected status | Expected body | Expected headers |
|---|---|---|---|
| Valid auth, valid multipart (`channel_name` + PNG file), existing channel | 200 | `{"status": "sent", "url": "<non-empty string>"}` | — |
| Valid auth, valid multipart, non-existent `channel_name` | 404 | `{"detail": "<string>"}` | — |
| Valid auth, `channel_name` form field missing | 422 | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | — |
| Valid auth, `channel_name` sent as query parameter instead of form field | 422 | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | — |
| Valid auth, bot not connected | 503 | `{"detail": "<string>"}` | — |
| Valid auth, multipart valid, channel found but bot lacks send permission | 403 | `{"detail": "Discord permission denied"}` | — |
| Valid auth, multipart valid, Discord API returns 4xx other than 403/404 | 502 | `{"detail": "Upstream Discord error"}` | — |
| Valid auth, multipart valid, Discord API returns 5xx or times out | 503 | `{"detail": "Discord temporarily unavailable"}` | — |

### `GET /api/members`

| Stimulus | Expected status | Expected body | Expected headers |
|---|---|---|---|
| Valid auth, bot connected | 200 | JSON array; each element has `id` (string), `username` (string), `display_name` (string) | — |
| Valid auth, bot not connected | 503 | `{"detail": "<string>"}` | — |

### `GET /api/members/{discord_user_id}`

| Stimulus | Expected status | Expected body | Expected headers |
|---|---|---|---|
| Valid auth, Discord snowflake (numeric string) of guild member | 200 | All six keys present; `is_member: true`; non-null string fields | — |
| Valid auth, Discord snowflake (numeric string) of non-member or unknown ID | 200 | All six keys present; `is_member: false`; remaining five keys `null` | — |
| Valid auth, bot not connected or guild unavailable | 503 | `{"detail": "<string>"}` | — |
| Valid auth, non-numeric `discord_user_id` (e.g. `"abc123"`) | 422 | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | — |

**Additional shape constraints (all member endpoints):**

- `GET /api/members` element key for Discord ID is `id`, not `discord_id`.
- `GET /api/members/{discord_user_id}` key for Discord ID is `discord_id`.
- `@everyone` role is excluded from `roles` and `role_names` in all member responses.
- `POST /api/post-image` response `url` field must be a non-empty string on 200.
- All six keys (`is_member`, `discord_id`, `username`, `display_name`, `roles`,
  `role_names`) must be present in every `GET /api/members/{discord_user_id}` response,
  regardless of membership status.

---

## Versioning

The `/api/version` endpoint returns a version string for observability. There is no
runtime version handshake — the backend does not check this value before calling other
endpoints. The string is informational only.

**Breaking vs. additive changes.**

| Change | Classification |
|---|---|
| Adding a key to a response body | Additive — safe |
| Removing or renaming a key | Breaking — update doc, tests, and `BotClient` in the same PR |
| Changing a path, method, or status code | Breaking — same |
| Changing `channel_name` from form field back to query param | Breaking |
| Changing `is_member` semantics or key set | Breaking |
| Tightening an existing value's format (e.g. adding a regex constraint to `username`) | Breaking — previously-valid values are now rejected with 422; existing callers fail without warning. Example: constraining `username` to match the Discord username regex rejects any caller that sent display names. |
| Changing the status code for the same logical condition (e.g. "username cache miss" was 404, becomes 503) | Breaking — consumers branch on status codes; reassigning a code silently changes their error-handling path. Example: `BotClient.notify` treats 4xx as user error and 5xx as transient; flipping the code flips the retry strategy. |
| Adding a required request field | Breaking — older callers omit the field and hit 422 instead of the previous success response. Example: adding a required `idempotency_key` to `POST /api/notify` breaks every existing caller until they update. |
| Tightening a 200-response field's type (e.g. `roles: string[]` → `roles: NonEmptyArray<Snowflake>`) | Breaking — consumers that don't already conform fail at parse time. Example: a sidecar that returned `[]` for guild-less members would need to change. |
| Splitting one endpoint into two | Additive if the original endpoint is preserved with identical behavior; breaking if the original endpoint is removed. The split itself is harmless — the removal is what breaks callers. Example: splitting `GET /api/members` into a filtered variant is additive; removing the original after some callers migrate is breaking. |

Cases not covered by the table above should be treated as breaking by default unless
explicitly classified as additive in the change PR.

Per Step 2 of #347: `INTERFACE.md` is updated in the same PR as any change to the
seam. No deprecation window is provided for breaking changes because the only consumers
are within this repository and the interface is internal (not public/versioned).

The 403/502/503 paths added in PR #421 are additive over the pre-#421 contract. Any
client built against the old contract — where discord.py exceptions collapsed to 500 —
continues to work correctly: it will receive more-precise status codes (403, 502, or
503) instead of 500, which are still non-200 and are already treated as failures by
`BotClient`.

The `GET /api/members/me/preferences` and `PUT /api/members/me/preferences`
endpoints added in PR #322 are additive. Existing callers (including bot callers
that use service-token auth on all other endpoints) are unaffected — the header is
`/me/*`-specific and its absence only triggers 401 on those two routes. Any bot caller
that does not send `X-Acting-Discord-Id` continues to reach all pre-existing endpoints
without change.

---

## Glossary

**Discord snowflake.** A 64-bit unsigned integer assigned by Discord to every entity
(user, channel, guild, message). Represented in this API as a numeric string (e.g.
`"123456789012345678"`) because JSON cannot represent 64-bit integers without precision
loss. Alternative sidecars MUST return snowflakes as strings, not integers.

**`bot_connected`.** The boolean field returned by `GET /api/health`. Its value
reflects `is_ready()` on the discord.py client at the moment the handler runs:
`true` means the gateway WebSocket has completed the identify handshake and the bot
has received initial guild state; `false` means the bot is starting up, reconnecting,
or resuming after a gateway drop. See the `GET /api/health` section for the
advisory-only SLA and consumer guidance. Alternative sidecars MUST include this key
in every health response.

**`is_member`.** The boolean discriminator in `GET /api/members/{discord_user_id}`
responses. `true` means the queried Discord user ID belongs to the configured guild
at the time of the request; `false` means the user is not in the guild or the ID is
unknown. All six response keys are always present regardless of this value.

---

## Cross-references

- **Seam overview (URL resolution, auth, deployment shapes, ingress, KV secrets):** [`docs/bot-seam.md`](../docs/bot-seam.md)
- **Umbrella issue:** [#347](https://github.com/glitchwerks/rsl-siege-manager/issues/347)
- **Step 1 cleanup PR:** [#415](https://github.com/glitchwerks/rsl-siege-manager/pull/415)
  — moved `/version` → `/api/version`, moved `channel_name` to multipart form body,
  added `is_member` discriminator with full nullable key set
- **Exception-translation PR:** [#421](https://github.com/glitchwerks/rsl-siege-manager/pull/421)
  — translated discord.py exceptions to 403/502/503; documented here
- **Step 3:** `backend/tests/integration/sidecar/` (integration tests, normative source
  of truth for this contract)
- **Backend consumer:** `backend/app/services/bot_client.py`
- **Sidecar implementation:** `bot/app/http_api.py`, `bot/app/discord_client.py`
