# Remnawave 2.x to 3.4.4 migration

RemnaShop-Pro targets Remnawave Panel **3.4.4** and
`@remnawave/backend-contract` **3.4.15**. See
[contract provenance](remnawave-contract-source.md) and the
[complete runtime API inventory](remnawave-api-inventory.md). The
[full operation diff](remnawave-openapi-diff.md) covers all 2.7.4 and 3.4.4
OpenAPI paths, including operations outside this bot's runtime surface.

## Contract changes that affect this bot

| Area | 2.7.4 behavior | 3.4.4 behavior |
|---|---|---|
| User identity | User `uuid` in paths and persisted mappings | Positive numeric `id` / `userId`; paths use `{userId}` |
| User lookup | `/users/by-telegram-id/{telegramId}` | `GET /users/stream?telegramId=...` with a `users` response array |
| User update | `uuid` in PATCH body | `id` in `UpdateUserBodyDto` |
| Bulk users | `uuids` in request bodies | `userIds` in the matching bulk DTOs |
| User nodes | `/users/{uuid}/accessible-nodes` | `/users/{userId}/accessible-nodes`; response contains `activeNodes` |
| User metadata | `PUT /metadata/user/{uuid}` | `PUT /metadata/user/{userId}` |
| Connection control | `/ip-control/drop-connections` | `POST /connections/drop` |
| Snippets | Key probing had no defined route | `GET /snippets`, then select by documented `name` |
| Bandwidth | Old realtime route was absent | Seven-day totals use `GET /bandwidth-stats/nodes` with required dates |
| Collections | Several callers expected bare lists | Read `response.records`, `response.externalSquads`, `response.configProfiles`, etc. |

The OpenAPI explicitly defines the paths, DTOs, path parameter types, query
parameters and response envelopes listed in the inventory. There is no
request-failure route guessing.

## SQLite upgrade

The upgrade is additive. Existing `subscriptions.uuid`,
`orders.target_uuid`, `orders.delivered_uuid`,
`anomaly_whitelist.user_uuid` and `anomaly_events.user_uuid` remain intact
as **legacy evidence**. New numeric columns are added:

- `subscriptions.user_id` and `subscriptions.migration_status`
- `orders.target_user_id` and `orders.delivered_user_id`
- `anomaly_whitelist.user_id` and `anomaly_events.user_id`
- Batch job retry state: `attempts` and `next_attempt_at`

A partial unique index prevents two local subscriptions from being bound to
the same non-null Panel user ID.

Existing subscriptions start as `pending`. When exactly one local unresolved
subscription and exactly one Panel user match the same Telegram ID, the bot
can bind the numeric ID. It uses the documented
`GET /api/users/stream?telegramId=...` filter. If several local or remote
records match, the status is `ambiguous`; no ID is guessed. Legacy orders,
whitelist entries and events are updated in the same SQLite transaction when
a binding is confirmed. The legacy UUID values remain unchanged.

An administrator can inspect pending local record numbers in the user list,
look up a Panel user by `id:<numeric ID>`, then use
`bind:<Telegram ID>:<Panel user ID>:<local subscription record ID>`.
If the Panel user has no Telegram ID, the bot requires an extra explicit
`确认绑定` message. A conflicting Panel Telegram ID is rejected.
Unresolved subscriptions cannot be renewed or deleted through a UUID route.
Old batch jobs containing `uuids` are retained as `migration_required`;
the administrator must resubmit them with numeric IDs.

## Accepted bulk work and bounded reconciliation

The 3.4.4 contract returns `202 Accepted` without a job ID for bulk update
and traffic reset. It defines no general job-status endpoint. A documented
`GET /api/users/{userId}` can observe a user's later status, traffic limit or
expiry, but it cannot prove which asynchronous bulk request produced that
state or how many requested rows were affected. Traffic reset is especially
ambiguous when usage resumes immediately. For this RC, the internal state
`submitted` means only that Panel accepted the request. No speculative
`/jobs` endpoint or misleading `completed` state is used.

Idempotent field updates may be retried up to three times after a transient
failure. A lost response to reset or delete can mean that Panel already acted;
those jobs become `unknown` and require an administrator to inspect the
numeric user IDs before deciding whether to resubmit. An interrupted
`running` job follows the same rule. A future bounded reconciliation feature
would need a defined post-condition, maximum reads and time limit; it is not
part of this RC.

The anomaly history scan is capped at 10,000 records in pages of 1,000. If
Panel reports a larger history, the job reports an explicit bounded-scan
error and does not advance its local last-scan marker.

## Deployment and rollback

Back up the RemnaShop-Pro data volume before upgrading a production instance.
Stop the `remnashop` service first, then copy the volume into a new directory
**outside the repository** and verify both `config.json` and SQLite
`PRAGMA integrity_check`. The README contains exact V3.6 pre-upgrade commands.
The V3.7 Docker management helper enforces a stopped service and verifies the
snapshot automatically. Protect the copy because it contains configuration
secrets and user data. The installer never removes an existing non-Git
directory while installing. Installation succeeds only after the container
becomes healthy.

The upgraded database keeps legacy columns, but **downgrading the bot against
a 3.4.4 Panel is unsupported**: the old runtime uses removed user UUID routes.
Keep a pre-upgrade volume backup for a controlled rollback of bot data and
configuration. Restoring a backup overwrites later local changes, so perform
it only after reviewing those changes.

`.env` is authoritative for nonempty `ADMIN_ID`, `BOT_TOKEN`, and
`PANEL_TOKEN` at container startup. When `PANEL_TOKEN` is empty, the bot
administrator may manage it in `config.json`. Other optional Panel settings
are seeded from the environment only when `config.json` is first created;
later changes made in the bot UI remain intact.

Container health checks local configuration and SQLite integrity. Panel
connectivity and Telegram behavior are external integration diagnostics; this
migration has no production credentials or live Panel integration result.
