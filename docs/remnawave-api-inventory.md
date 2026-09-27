# Remnawave 3.4.4 runtime API inventory

This inventory is generated from every direct `safe_api_request` call in
`services/panel_api.py` and matched against the vendored official Panel 3.4.4
OpenAPI. That module is the sole runtime Remnawave HTTP boundary.
`bot.py` uses its wrappers; `run_bulk_action` orchestrates its documented bulk
operations. Contract behavior is checked in `tests/test_panel_api_v3_contract.py`.

| Function | Method | Path | Path parameters | Query parameters | JSON body schema | Success response | Contract status | Test |
|---|---|---|---|---|---|---|---|---|
| get_panel_user | GET | `/api/users/{userId}` | userId (number) | — | — | 200 UserResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_users_by_telegram_id | GET | `/api/users/stream` | — | cursor, size, status, trafficLimitStrategy, telegramId, email, tag, externalSquadUuid | — | 200 GetUsersStreamResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_user_by_username | GET | `/api/users/by-username/{username}` | username (string) | — | — | 200 UserResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_user_by_short_uuid | GET | `/api/users/by-short-uuid/{shortUuid}` | shortUuid (string) | — | — | 200 UserResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_nodes_status | GET | `/api/nodes` | — | — | — | 200 GetNodesResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_subscription_history_stats | GET | `/api/subscription-request-history/stats` | — | — | — | 200 GetSubscriptionRequestHistoryStatsResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_user_subscription_history | GET | `/api/users/{userId}/subscription-request-history` | userId (number) | — | — | 200 GetUserSubscriptionRequestHistoryResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_subscription_settings | GET | `/api/subscription-settings` | — | — | — | 200 GetSubscriptionSettingsResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| patch_subscription_settings | PATCH | `/api/subscription-settings` | — | — | UpdateSubscriptionSettingsBodyDto | 200 UpdateSubscriptionSettingsResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_internal_squads | GET | `/api/internal-squads` | — | — | — | 200 GetInternalSquadsResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_internal_squad_accessible_nodes | GET | `/api/internal-squads/{uuid}/accessible-nodes` | uuid (string) | — | — | 200 GetInternalSquadAccessibleNodesResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_bandwidth_nodes_usage | GET | `/api/bandwidth-stats/nodes` | — | start, end, topNodesLimit | — | 200 GetStatsNodesUsageResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| bulk_move_users_to_squad | POST | `/api/users/bulk/update-squads` | — | — | BulkUpdateUsersSquadsBodyDto | 204 empty | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| create_user | POST | `/api/users` | — | — | CreateUserBodyDto | 201 UserResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| patch_user | PATCH | `/api/users` | — | — | UpdateUserBodyDto | 200 UserResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| delete_user | DELETE | `/api/users/{userId}` | userId (number) | — | — | 204 empty | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| enable_user | POST | `/api/users/{userId}/actions/enable` | userId (number) | — | — | 200 UserResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| disable_user | POST | `/api/users/{userId}/actions/disable` | userId (number) | — | — | 200 UserResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| reset_user_traffic | POST | `/api/users/{userId}/actions/reset-traffic` | userId (number) | — | — | 200 UserResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_subscription_request_history | GET | `/api/subscription-request-history` | — | start, size, filters, filterModes, globalFilterMode, sorting | — | 200 GetSubscriptionRequestHistoryResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| bulk_delete_users | POST | `/api/users/bulk/delete` | — | — | BulkDeleteUsersBodyDto | 204 empty | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| bulk_reset_traffic_users | POST | `/api/users/bulk/reset-traffic` | — | — | BulkResetTrafficUsersBodyDto | 202 empty | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| bulk_update_users | POST | `/api/users/bulk/update` | — | — | BulkUpdateUsersBodyDto | 202 empty | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| set_user_metadata | PUT | `/api/metadata/user/{userId}` | userId (number) | — | UpsertUserMetadataBodyDto | 200 UpsertUserMetadataResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| block_ip_address | POST | `/api/connections/drop` | — | — | DropConnectionsBodyDto | 202 empty | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_system_health | GET | `/api/system/health` | — | — | — | 200 GetRemnawaveHealthResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_system_stats | GET | `/api/system/stats` | — | — | — | 200 GetStatsResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_system_stats_recap | GET | `/api/system/stats/recap` | — | — | — | 200 GetRecapResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_snippet_by_key | GET | `/api/snippets` | — | — | — | 200 GetSnippetsResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_subscription_page_configs | GET | `/api/subscription-page-configs` | — | — | — | 200 GetSubpageConfigsResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_external_squads | GET | `/api/external-squads` | — | — | — | 200 GetExternalSquadsResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_config_profiles | GET | `/api/config-profiles` | — | — | — | 200 GetConfigProfilesResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |
| get_user_accessible_nodes | GET | `/api/users/{userId}/accessible-nodes` | userId (number) | — | — | 200 GetUserAccessibleNodesResponseDto | VERIFIED 3.4.4 | test_panel_api_v3_contract.py |

The obsolete `GET /api/bandwidth-stats/nodes/realtime` has no replacement with
identical realtime semantics. The dashboard now displays the official
`GET /api/bandwidth-stats/nodes` seven-day totals with required `start` and
`end` dates. The former `GET /api/snippets/{key}`, `GET /api/ip-control`, and
`PATCH/POST /api/metadata/user/{uuid}` probes were removed. No runtime call
uses a legacy user UUID route or a `uuids` bulk request field.
