# Remnawave OpenAPI 2.7.4 to 3.4.4 operation diff

Compared against the repository baseline at `8c1393cf` and the vendored
official Panel 3.4.4 specification. This covers every OpenAPI path and HTTP
operation. The [runtime inventory](remnawave-api-inventory.md) identifies
which operations RemnaShop-Pro actually calls.

- Old: 141 paths, 185 operations.
- New: 162 paths, 218 operations.
- Added operations: 61.
- Removed operations: 28.
- Shared operations with changed parameters or top-level DTO/schema: 150.

## Removed operations

| Method | Path |
|---|---|
| GET | `/api/bandwidth-stats/nodes/{uuid}/users/legacy` |
| GET | `/api/bandwidth-stats/users/{uuid}` |
| GET | `/api/bandwidth-stats/users/{uuid}/legacy` |
| POST | `/api/hosts/bulk/set-inbound` |
| POST | `/api/hosts/bulk/set-port` |
| GET | `/api/hwid/devices/{userUuid}` |
| POST | `/api/ip-control/drop-connections` |
| GET | `/api/ip-control/fetch-ips/result/{jobId}` |
| POST | `/api/ip-control/fetch-ips/{uuid}` |
| GET | `/api/ip-control/fetch-users-ips/result/{jobId}` |
| POST | `/api/ip-control/fetch-users-ips/{nodeUuid}` |
| GET | `/api/metadata/user/{uuid}` |
| PUT | `/api/metadata/user/{uuid}` |
| GET | `/api/subscriptions/by-uuid/{uuid}` |
| GET | `/api/subscriptions/connection-keys/{uuid}` |
| POST | `/api/system/tools/happ/encrypt` |
| GET | `/api/users/by-email/{email}` |
| GET | `/api/users/by-id/{id}` |
| GET | `/api/users/by-tag/{tag}` |
| GET | `/api/users/by-telegram-id/{telegramId}` |
| DELETE | `/api/users/{uuid}` |
| GET | `/api/users/{uuid}` |
| GET | `/api/users/{uuid}/accessible-nodes` |
| POST | `/api/users/{uuid}/actions/disable` |
| POST | `/api/users/{uuid}/actions/enable` |
| POST | `/api/users/{uuid}/actions/reset-traffic` |
| POST | `/api/users/{uuid}/actions/revoke` |
| GET | `/api/users/{uuid}/subscription-request-history` |

## Added operations

| Method | Path |
|---|---|
| GET | `/api/bandwidth-stats/internal-squads/{squadUuid}/users/{userId}/usage` |
| GET | `/api/bandwidth-stats/internal-squads/{uuid}/usage` |
| POST | `/api/bandwidth-stats/nodes/usage` |
| POST | `/api/bandwidth-stats/nodes/users` |
| GET | `/api/bandwidth-stats/users/{userId}` |
| GET | `/api/config-profiles/tags` |
| PATCH | `/api/config-profiles/tags` |
| GET | `/api/connections/by-node/{jobId}` |
| POST | `/api/connections/by-node/{nodeUuid}` |
| GET | `/api/connections/by-user/{jobId}` |
| POST | `/api/connections/by-user/{userId}` |
| POST | `/api/connections/drop` |
| GET | `/api/connections/geocheck/{jobId}` |
| POST | `/api/connections/geocheck/{nodeUuid}` |
| GET | `/api/external-squads/tags` |
| PATCH | `/api/external-squads/tags` |
| POST | `/api/hosts/actions/clone` |
| PATCH | `/api/hosts/bulk/update` |
| GET | `/api/hwid/devices/{userId}` |
| GET | `/api/internal-squads/tags` |
| PATCH | `/api/internal-squads/tags` |
| POST | `/api/internal-squads/{uuid}/bulk-actions/add-many-users` |
| DELETE | `/api/internal-squads/{uuid}/bulk-actions/remove-many-users` |
| GET | `/api/internal-squads/{uuid}/usage` |
| GET | `/api/metadata/user/{userId}` |
| PUT | `/api/metadata/user/{userId}` |
| GET | `/api/node-integrations` |
| PATCH | `/api/node-integrations` |
| POST | `/api/node-integrations` |
| DELETE | `/api/node-integrations/{uuid}` |
| GET | `/api/node-integrations/{uuid}` |
| POST | `/api/node-plugins/actions/sync` |
| DELETE | `/api/node-plugins/shared-lists` |
| GET | `/api/node-plugins/shared-lists` |
| PATCH | `/api/node-plugins/shared-lists` |
| POST | `/api/node-plugins/shared-lists` |
| POST | `/api/node-plugins/shared-lists/actions/sync` |
| GET | `/api/node-plugins/shared-lists/by-name` |
| GET | `/api/node-plugins/tags` |
| PATCH | `/api/node-plugins/tags` |
| POST | `/api/snippets/actions/sync` |
| GET | `/api/subscription-page-configs/tags` |
| PATCH | `/api/subscription-page-configs/tags` |
| GET | `/api/subscription-templates/tags` |
| PATCH | `/api/subscription-templates/tags` |
| GET | `/api/subscriptions/by-id/{userId}` |
| GET | `/api/subscriptions/connection-keys/{userId}` |
| GET | `/api/system/configuration` |
| GET | `/api/system/stats/digest` |
| GET | `/api/system/stats/http` |
| GET | `/api/tokens/scopes` |
| GET | `/api/users/stream` |
| DELETE | `/api/users/{userId}` |
| GET | `/api/users/{userId}` |
| GET | `/api/users/{userId}/accessible-nodes` |
| POST | `/api/users/{userId}/actions/disable` |
| POST | `/api/users/{userId}/actions/enable` |
| POST | `/api/users/{userId}/actions/extend` |
| POST | `/api/users/{userId}/actions/reset-traffic` |
| POST | `/api/users/{userId}/actions/revoke` |
| GET | `/api/users/{userId}/subscription-request-history` |

## Shared operations with contract changes

| Method | Path | Changes | Old body | New body | Old success response | New success response |
|---|---|---|---|---|---|---|
| POST | `/api/auth/login` | request DTO, response DTO | LoginRequestDto | LoginBodyDto | — | 200:LoginResponseDto |
| POST | `/api/auth/oauth2/authorize` | request DTO, response DTO | OAuth2AuthorizeRequestDto | OAuth2AuthorizeBodyDto | — | 200:OAuth2AuthorizeResponseDto |
| POST | `/api/auth/oauth2/callback` | request DTO, response DTO | OAuth2CallbackRequestDto | OAuth2CallbackBodyDto | — | 200:OAuth2CallbackResponseDto |
| GET | `/api/auth/passkey/authentication/options` | response DTO | — | — | — | 200:GetPasskeyAuthenticationOptionsResponseDto |
| POST | `/api/auth/passkey/authentication/verify` | request DTO, response DTO | VerifyPasskeyAuthenticationRequestDto | VerifyPasskeyAuthenticationBodyDto | — | 200:VerifyPasskeyAuthenticationResponseDto |
| POST | `/api/auth/register` | request DTO, response DTO | RegisterRequestDto | RegisterBodyDto | — | 201:RegisterResponseDto |
| GET | `/api/auth/status` | response DTO | — | — | — | 200:GetStatusResponseDto |
| GET | `/api/bandwidth-stats/nodes` | parameters, response schema | — | — | 200:GetStatsNodesUsageResponseDto | 200:GetStatsNodesUsageResponseDto |
| GET | `/api/bandwidth-stats/nodes/{uuid}/users` | parameters | — | — | 200:GetStatsNodeUsersUsageResponseDto | 200:GetStatsNodeUsersUsageResponseDto |
| GET | `/api/config-profiles` | response schema | — | — | 200:GetConfigProfilesResponseDto | 200:GetConfigProfilesResponseDto |
| PATCH | `/api/config-profiles` | request DTO, response schema | UpdateConfigProfileRequestDto | UpdateConfigProfileBodyDto | 200:UpdateConfigProfileResponseDto | 200:UpdateConfigProfileResponseDto |
| POST | `/api/config-profiles` | request DTO, response schema | CreateConfigProfileRequestDto | CreateConfigProfileBodyDto | 201:CreateConfigProfileResponseDto | 201:CreateConfigProfileResponseDto |
| POST | `/api/config-profiles/actions/reorder` | request DTO, response schema | ReorderConfigProfilesRequestDto | ReorderConfigProfilesBodyDto | 200:ReorderConfigProfilesResponseDto | 200:ReorderConfigProfilesResponseDto |
| GET | `/api/config-profiles/inbounds` | response schema | — | — | 200:GetAllInboundsResponseDto | 200:GetAllInboundsResponseDto |
| DELETE | `/api/config-profiles/{uuid}` | parameters, response DTO | — | — | 200:DeleteConfigProfileResponseDto | 204:empty |
| GET | `/api/config-profiles/{uuid}` | parameters, response schema | — | — | 200:GetConfigProfileByUuidResponseDto | 200:GetConfigProfileByUuidResponseDto |
| GET | `/api/config-profiles/{uuid}/computed-config` | parameters, response schema | — | — | 200:GetComputedConfigProfileByUuidResponseDto | 200:GetComputedConfigProfileByUuidResponseDto |
| GET | `/api/config-profiles/{uuid}/inbounds` | parameters, response schema | — | — | 200:GetInboundsByProfileUuidResponseDto | 200:GetInboundsByProfileUuidResponseDto |
| GET | `/api/external-squads` | response schema | — | — | 200:GetExternalSquadsResponseDto | 200:GetExternalSquadsResponseDto |
| PATCH | `/api/external-squads` | request DTO, response schema | UpdateExternalSquadRequestDto | UpdateExternalSquadBodyDto | 200:UpdateExternalSquadResponseDto | 200:UpdateExternalSquadResponseDto |
| POST | `/api/external-squads` | request DTO, response schema | CreateExternalSquadRequestDto | CreateExternalSquadBodyDto | 201:CreateExternalSquadResponseDto | 201:CreateExternalSquadResponseDto |
| POST | `/api/external-squads/actions/reorder` | request DTO, response schema | ReorderExternalSquadsRequestDto | ReorderExternalSquadsBodyDto | 200:ReorderExternalSquadsResponseDto | 200:ReorderExternalSquadsResponseDto |
| DELETE | `/api/external-squads/{uuid}` | parameters, response DTO | — | — | 200:DeleteExternalSquadResponseDto | 204:empty |
| GET | `/api/external-squads/{uuid}` | parameters, response schema | — | — | 200:GetExternalSquadByUuidResponseDto | 200:GetExternalSquadByUuidResponseDto |
| POST | `/api/external-squads/{uuid}/bulk-actions/add-users` | parameters, response DTO | — | — | 200:AddUsersToExternalSquadResponseDto | 202:empty |
| DELETE | `/api/external-squads/{uuid}/bulk-actions/remove-users` | parameters, response DTO | — | — | 200:RemoveUsersFromExternalSquadResponseDto | 202:empty |
| GET | `/api/hosts` | response DTO | — | — | 200:GetAllHostsResponseDto | 200:GetHostsResponseDto |
| PATCH | `/api/hosts` | request DTO, response DTO | UpdateHostRequestDto | UpdateHostBodyDto | 200:UpdateHostResponseDto | 200:HostResponseDto |
| POST | `/api/hosts` | request DTO, response DTO | CreateHostRequestDto | CreateHostBodyDto | 201:CreateHostResponseDto | 201:HostResponseDto |
| POST | `/api/hosts/actions/reorder` | request DTO, response DTO | ReorderHostRequestDto | ReorderHostsBodyDto | 200:ReorderHostResponseDto | 200:ReorderHostsResponseDto |
| POST | `/api/hosts/bulk/delete` | request DTO, response DTO | BulkDeleteHostsRequestDto | BulkDeleteHostsBodyDto | 200:BulkDeleteHostsResponseDto | 204:empty |
| POST | `/api/hosts/bulk/disable` | request DTO, response DTO | BulkDisableHostsRequestDto | BulkDisableHostsBodyDto | 200:BulkDisableHostsResponseDto | 204:empty |
| POST | `/api/hosts/bulk/enable` | request DTO, response DTO | BulkEnableHostsRequestDto | BulkEnableHostsBodyDto | 200:BulkEnableHostsResponseDto | 204:empty |
| GET | `/api/hosts/tags` | response DTO | — | — | 200:GetAllHostTagsResponseDto | 200:GetHostsTagsResponseDto |
| DELETE | `/api/hosts/{uuid}` | parameters, response DTO | — | — | 200:DeleteHostResponseDto | 204:empty |
| GET | `/api/hosts/{uuid}` | parameters, response DTO | — | — | 200:GetOneHostResponseDto | 200:HostResponseDto |
| GET | `/api/hwid/devices` | parameters, response DTO | — | — | 200:GetAllHwidDevicesResponseDto | 200:GetHwidDevicesQueryResponseDto |
| POST | `/api/hwid/devices` | request DTO, response schema | CreateUserHwidDeviceRequestDto | CreateUserHwidDeviceBodyDto | 200:CreateUserHwidDeviceResponseDto | 200:CreateUserHwidDeviceResponseDto |
| POST | `/api/hwid/devices/delete` | request DTO, response schema | DeleteUserHwidDeviceRequestDto | DeleteUserHwidDeviceBodyDto | 200:DeleteUserHwidDeviceResponseDto | 200:DeleteUserHwidDeviceResponseDto |
| POST | `/api/hwid/devices/delete-all` | request DTO, response schema | DeleteAllUserHwidDevicesRequestDto | DeleteAllUserHwidDevicesBodyDto | 200:DeleteAllUserHwidDevicesResponseDto | 200:DeleteAllUserHwidDevicesResponseDto |
| GET | `/api/hwid/devices/stats` | response schema | — | — | 200:GetHwidDevicesStatsResponseDto | 200:GetHwidDevicesStatsResponseDto |
| GET | `/api/hwid/devices/top-users` | parameters, response schema | — | — | 200:GetTopUsersByHwidDevicesResponseDto | 200:GetTopUsersByHwidDevicesResponseDto |
| GET | `/api/infra-billing/history` | parameters, response DTO | — | — | 200:GetInfraBillingHistoryRecordsResponseDto | 200:GetInfraBillingRecordsResponseDto |
| POST | `/api/infra-billing/history` | request DTO, response DTO | CreateInfraBillingHistoryRecordRequestDto | CreateInfraBillingRecordBodyDto | 201:CreateInfraBillingHistoryRecordResponseDto | 201:CreateInfraBillingRecordResponseDto |
| DELETE | `/api/infra-billing/history/{uuid}` | parameters, response DTO | — | — | 200:DeleteInfraBillingHistoryRecordByUuidResponseDto | 204:empty |
| GET | `/api/infra-billing/nodes` | response schema | — | — | 200:GetInfraBillingNodesResponseDto | 200:GetInfraBillingNodesResponseDto |
| PATCH | `/api/infra-billing/nodes` | request DTO, response schema | UpdateInfraBillingNodeRequestDto | UpdateInfraBillingNodeBodyDto | 200:UpdateInfraBillingNodeResponseDto | 200:UpdateInfraBillingNodeResponseDto |
| POST | `/api/infra-billing/nodes` | request DTO, response schema | CreateInfraBillingNodeRequestDto | CreateInfraBillingNodeBodyDto | 201:CreateInfraBillingNodeResponseDto | 201:CreateInfraBillingNodeResponseDto |
| DELETE | `/api/infra-billing/nodes/{uuid}` | parameters, response DTO | — | — | 200:DeleteInfraBillingNodeByUuidResponseDto | 204:empty |
| GET | `/api/infra-billing/providers` | response schema | — | — | 200:GetInfraProvidersResponseDto | 200:GetInfraProvidersResponseDto |
| PATCH | `/api/infra-billing/providers` | request DTO, response schema | UpdateInfraProviderRequestDto | UpdateInfraProviderBodyDto | 200:UpdateInfraProviderResponseDto | 200:UpdateInfraProviderResponseDto |
| POST | `/api/infra-billing/providers` | request DTO, response schema | CreateInfraProviderRequestDto | CreateInfraProviderBodyDto | 201:CreateInfraProviderResponseDto | 201:CreateInfraProviderResponseDto |
| DELETE | `/api/infra-billing/providers/{uuid}` | parameters, response DTO | — | — | 200:DeleteInfraProviderByUuidResponseDto | 204:empty |
| GET | `/api/infra-billing/providers/{uuid}` | parameters, response DTO | — | — | 200:GetInfraProviderByUuidResponseDto | 200:GetInfraProviderResponseDto |
| GET | `/api/internal-squads` | response schema | — | — | 200:GetInternalSquadsResponseDto | 200:GetInternalSquadsResponseDto |
| PATCH | `/api/internal-squads` | request DTO, response schema | UpdateInternalSquadRequestDto | UpdateInternalSquadBodyDto | 200:UpdateInternalSquadResponseDto | 200:UpdateInternalSquadResponseDto |
| POST | `/api/internal-squads` | request DTO, response schema | CreateInternalSquadRequestDto | CreateInternalSquadBodyDto | 201:CreateInternalSquadResponseDto | 201:CreateInternalSquadResponseDto |
| POST | `/api/internal-squads/actions/reorder` | request DTO, response schema | ReorderInternalSquadsRequestDto | ReorderInternalSquadsBodyDto | 200:ReorderInternalSquadsResponseDto | 200:ReorderInternalSquadsResponseDto |
| DELETE | `/api/internal-squads/{uuid}` | parameters, response DTO | — | — | 200:DeleteInternalSquadResponseDto | 204:empty |
| GET | `/api/internal-squads/{uuid}` | parameters, response DTO | — | — | 200:GetInternalSquadByUuidResponseDto | 200:GetInternalSquadResponseDto |
| GET | `/api/internal-squads/{uuid}/accessible-nodes` | parameters, response schema | — | — | 200:GetInternalSquadAccessibleNodesResponseDto | 200:GetInternalSquadAccessibleNodesResponseDto |
| POST | `/api/internal-squads/{uuid}/bulk-actions/add-users` | parameters, response DTO | — | — | 200:AddUsersToInternalSquadResponseDto | 202:empty |
| DELETE | `/api/internal-squads/{uuid}/bulk-actions/remove-users` | parameters, response DTO | — | — | 200:RemoveUsersFromInternalSquadResponseDto | 202:empty |
| GET | `/api/keygen` | response DTO | — | — | 200:GetPubKeyResponseDto | 200:GetNodeSecretKeyResponseDto |
| GET | `/api/metadata/node/{uuid}` | parameters, response schema | — | — | 200:GetNodeMetadataResponseDto | 200:GetNodeMetadataResponseDto |
| PUT | `/api/metadata/node/{uuid}` | parameters, request DTO, response schema | UpsertNodeMetadataRequestBodyDto | UpsertNodeMetadataBodyDto | 200:UpsertNodeMetadataResponseDto | 200:UpsertNodeMetadataResponseDto |
| GET | `/api/node-plugins` | response schema | — | — | 200:GetNodePluginsResponseDto | 200:GetNodePluginsResponseDto |
| PATCH | `/api/node-plugins` | request DTO, response schema | UpdateNodePluginRequestDto | UpdateNodePluginBodyDto | 200:UpdateNodePluginResponseDto | 200:UpdateNodePluginResponseDto |
| POST | `/api/node-plugins` | request DTO, response DTO | CreateNodePluginRequestDto | CreateNodePluginBodyDto | 200:CreateNodePluginResponseDto | 201:CreateNodePluginResponseDto |
| POST | `/api/node-plugins/actions/clone` | request DTO, response schema | CloneNodePluginRequestDto | CloneNodePluginBodyDto | 200:CloneNodePluginResponseDto | 200:CloneNodePluginResponseDto |
| POST | `/api/node-plugins/actions/reorder` | request DTO, response schema | ReorderNodePluginsRequestDto | ReorderNodePluginsBodyDto | 200:ReorderNodePluginsResponseDto | 200:ReorderNodePluginsResponseDto |
| POST | `/api/node-plugins/executor` | request DTO, response DTO | PluginExecutorRequestDto | PluginExecutorBodyDto | 200:PluginExecutorResponseDto | 202:empty |
| GET | `/api/node-plugins/torrent-blocker` | parameters, response schema | — | — | 200:GetTorrentBlockerReportsResponseDto | 200:GetTorrentBlockerReportsResponseDto |
| GET | `/api/node-plugins/torrent-blocker/stats` | response schema | — | — | 200:GetTorrentBlockerReportsStatsResponseDto | 200:GetTorrentBlockerReportsStatsResponseDto |
| DELETE | `/api/node-plugins/torrent-blocker/truncate` | response DTO | — | — | 200:TruncateTorrentBlockerReportsResponseDto | 204:empty |
| DELETE | `/api/node-plugins/{uuid}` | parameters, response DTO | — | — | 200:DeleteNodePluginResponseDto | 204:empty |
| GET | `/api/node-plugins/{uuid}` | parameters, response schema | — | — | 200:GetNodePluginResponseDto | 200:GetNodePluginResponseDto |
| GET | `/api/nodes` | response DTO | — | — | 200:GetAllNodesResponseDto | 200:GetNodesResponseDto |
| PATCH | `/api/nodes` | request DTO, response DTO | UpdateNodeRequestDto | UpdateNodeBodyDto | 200:UpdateNodeResponseDto | 200:NodeResponseDto |
| POST | `/api/nodes` | request DTO, response DTO | CreateNodeRequestDto | CreateNodeBodyDto | 201:CreateNodeResponseDto | 201:NodeResponseDto |
| POST | `/api/nodes/actions/reorder` | request DTO, response DTO | ReorderNodeRequestDto | ReorderNodesBodyDto | 200:ReorderNodeResponseDto | 200:ReorderNodesResponseDto |
| POST | `/api/nodes/actions/restart-all` | request DTO, response DTO | RestartAllNodesRequestBodyDto | RestartAllNodesBodyDto | 200:RestartAllNodesResponseDto | 202:empty |
| POST | `/api/nodes/bulk-actions` | request DTO, response DTO | BulkNodesActionsRequestDto | BulkNodesActionsBodyDto | 200:BulkNodesActionsResponseDto | 204:empty |
| POST | `/api/nodes/bulk-actions/profile-modification` | request DTO, response DTO | ProfileModificationRequestDto | ProfileModificationBodyDto | 200:ProfileModificationResponseDto | 204:empty |
| POST | `/api/nodes/bulk-actions/update` | request DTO, response DTO | BulkNodesUpdateRequestDto | BulkNodesUpdateBodyDto | 200:BulkNodesUpdateResponseDto | 204:empty |
| GET | `/api/nodes/tags` | response DTO | — | — | 200:GetAllNodesTagsResponseDto | 200:GetNodesTagsResponseDto |
| DELETE | `/api/nodes/{uuid}` | parameters, response DTO | — | — | 200:DeleteNodeResponseDto | 204:empty |
| GET | `/api/nodes/{uuid}` | parameters, response DTO | — | — | 200:GetOneNodeResponseDto | 200:NodeResponseDto |
| POST | `/api/nodes/{uuid}/actions/disable` | parameters, response DTO | — | — | 200:DisableNodeResponseDto | 200:NodeResponseDto |
| POST | `/api/nodes/{uuid}/actions/enable` | parameters, response DTO | — | — | 200:EnableNodeResponseDto | 200:NodeResponseDto |
| POST | `/api/nodes/{uuid}/actions/reset-traffic` | parameters, response DTO | — | — | 200:ResetNodeTrafficResponseDto | 204:empty |
| POST | `/api/nodes/{uuid}/actions/restart` | parameters, request DTO, response DTO | — | RestartNodeBodyDto | 200:RestartNodeResponseDto | 202:empty |
| DELETE | `/api/passkeys` | request DTO, response DTO | DeletePasskeyRequestDto | DeletePasskeyBodyDto | — | 204:empty |
| GET | `/api/passkeys` | response DTO | — | — | — | 200:GetPasskeysResponseDto |
| PATCH | `/api/passkeys` | request DTO, response DTO | UpdatePasskeyRequestDto | UpdatePasskeyBodyDto | — | 200:UpdatePasskeyResponseDto |
| GET | `/api/passkeys/registration/options` | response DTO | — | — | — | 200:GetPasskeyRegistrationOptionsResponseDto |
| POST | `/api/passkeys/registration/verify` | request DTO, response DTO | VerifyPasskeyRegistrationRequestDto | VerifyPasskeyRegistrationBodyDto | — | 200:VerifyPasskeyRegistrationResponseDto |
| GET | `/api/remnawave-settings` | response schema | — | — | 200:GetRemnawaveSettingsResponseDto | 200:GetRemnawaveSettingsResponseDto |
| PATCH | `/api/remnawave-settings` | request DTO, response schema | UpdateRemnawaveSettingsRequestDto | UpdateRemnawaveSettingsBodyDto | 200:UpdateRemnawaveSettingsResponseDto | 200:UpdateRemnawaveSettingsResponseDto |
| DELETE | `/api/snippets` | request DTO, response DTO | DeleteSnippetRequestDto | DeleteSnippetBodyDto | 200:DeleteSnippetResponseDto | 204:empty |
| PATCH | `/api/snippets` | request DTO | UpdateSnippetRequestDto | UpdateSnippetBodyDto | 200:UpdateSnippetResponseDto | 200:UpdateSnippetResponseDto |
| POST | `/api/snippets` | request DTO | CreateSnippetRequestDto | CreateSnippetBodyDto | 201:CreateSnippetResponseDto | 201:CreateSnippetResponseDto |
| GET | `/api/sub/{shortUuid}/info` | response schema | — | — | 200:GetSubscriptionInfoResponseDto | 200:GetSubscriptionInfoResponseDto |
| GET | `/api/sub/{shortUuid}/{clientType}` | parameters | — | — | 200:empty | 200:empty |
| GET | `/api/subscription-page-configs` | response DTO | — | — | 200:GetSubscriptionPageConfigsResponseDto | 200:GetSubpageConfigsResponseDto |
| PATCH | `/api/subscription-page-configs` | request DTO, response DTO | UpdateSubscriptionPageConfigRequestDto | UpdateSubpageConfigBodyDto | 200:UpdateSubscriptionPageConfigResponseDto | 200:UpdateSubpageConfigResponseDto |
| POST | `/api/subscription-page-configs` | request DTO, response DTO | CreateSubscriptionPageConfigRequestDto | CreateSubpageConfigBodyDto | 200:CreateSubscriptionPageConfigResponseDto | 201:CreateSubpageConfigResponseDto |
| POST | `/api/subscription-page-configs/actions/clone` | request DTO, response DTO | CloneSubscriptionPageConfigRequestDto | CloneSubpageConfigBodyDto | 200:CloneSubscriptionPageConfigResponseDto | 200:CloneSubpageConfigResponseDto |
| POST | `/api/subscription-page-configs/actions/reorder` | request DTO, response DTO | ReorderSubscriptionPageConfigsRequestDto | ReorderSubpageConfigsBodyDto | 200:ReorderSubscriptionPageConfigsResponseDto | 200:ReorderSubpageConfigsResponseDto |
| DELETE | `/api/subscription-page-configs/{uuid}` | parameters, response DTO | — | — | 200:DeleteSubscriptionPageConfigResponseDto | 204:empty |
| GET | `/api/subscription-page-configs/{uuid}` | parameters, response DTO | — | — | 200:GetSubscriptionPageConfigResponseDto | 200:GetSubpageConfigResponseDto |
| GET | `/api/subscription-request-history` | parameters, response schema | — | — | 200:GetSubscriptionRequestHistoryResponseDto | 200:GetSubscriptionRequestHistoryResponseDto |
| GET | `/api/subscription-request-history/stats` | response schema | — | — | 200:GetSubscriptionRequestHistoryStatsResponseDto | 200:GetSubscriptionRequestHistoryStatsResponseDto |
| GET | `/api/subscription-settings` | response schema | — | — | 200:GetSubscriptionSettingsResponseDto | 200:GetSubscriptionSettingsResponseDto |
| PATCH | `/api/subscription-settings` | request DTO, response schema | UpdateSubscriptionSettingsRequestDto | UpdateSubscriptionSettingsBodyDto | 200:UpdateSubscriptionSettingsResponseDto | 200:UpdateSubscriptionSettingsResponseDto |
| GET | `/api/subscription-templates` | response schema | — | — | 200:GetTemplatesResponseDto | 200:GetTemplatesResponseDto |
| PATCH | `/api/subscription-templates` | request DTO, response schema | UpdateTemplateRequestDto | UpdateTemplateBodyDto | 200:UpdateTemplateResponseDto | 200:UpdateTemplateResponseDto |
| POST | `/api/subscription-templates` | request DTO, response DTO | CreateSubscriptionTemplateRequestDto | CreateSubscriptionTemplateBodyDto | 200:CreateSubscriptionTemplateResponseDto | 201:CreateSubscriptionTemplateResponseDto |
| POST | `/api/subscription-templates/actions/reorder` | request DTO, response schema | ReorderSubscriptionTemplatesRequestDto | ReorderSubscriptionTemplatesBodyDto | 200:ReorderSubscriptionTemplatesResponseDto | 200:ReorderSubscriptionTemplatesResponseDto |
| DELETE | `/api/subscription-templates/{uuid}` | parameters, response DTO | — | — | 200:DeleteSubscriptionTemplateResponseDto | 204:empty |
| GET | `/api/subscription-templates/{uuid}` | parameters, response schema | — | — | 200:GetTemplateResponseDto | 200:GetTemplateResponseDto |
| GET | `/api/subscriptions` | parameters, response DTO | — | — | 200:GetAllSubscriptionsResponseDto | 200:GetSubscriptionsResponseDto |
| GET | `/api/subscriptions/by-short-uuid/{shortUuid}` | response schema | — | — | 200:GetSubscriptionByShortUuidProtectedResponseDto | 200:GetSubscriptionByShortUuidProtectedResponseDto |
| GET | `/api/subscriptions/by-short-uuid/{shortUuid}/raw` | parameters, response schema | — | — | 200:GetRawSubscriptionByShortUuidResponseDto | 200:GetRawSubscriptionByShortUuidResponseDto |
| GET | `/api/subscriptions/by-username/{username}` | response schema | — | — | 200:GetSubscriptionByUsernameResponseDto | 200:GetSubscriptionByUsernameResponseDto |
| GET | `/api/subscriptions/subpage-config/{shortUuid}` | request DTO, response schema | GetSubpageConfigByShortUuidRequestBodyDto | GetSubpageConfigByShortUuidBodyDto | 200:GetSubpageConfigByShortUuidResponseDto | 200:GetSubpageConfigByShortUuidResponseDto |
| GET | `/api/system/stats` | response schema | — | — | 200:GetStatsResponseDto | 200:GetStatsResponseDto |
| GET | `/api/system/stats/bandwidth` | parameters | — | — | 200:GetBandwidthStatsResponseDto | 200:GetBandwidthStatsResponseDto |
| GET | `/api/system/stats/recap` | response schema | — | — | 200:GetRecapResponseDto | 200:GetRecapResponseDto |
| POST | `/api/system/testers/srr-matcher` | request DTO, response DTO | DebugSrrMatcherRequestDto | DebugSrrMatcherBodyDto | 201:DebugSrrMatcherResponseDto | 200:DebugSrrMatcherResponseDto |
| GET | `/api/tokens` | response DTO | — | — | 200:FindAllApiTokensResponseDto | 200:GetApiTokensResponseDto |
| POST | `/api/tokens` | request DTO, response schema | CreateApiTokenRequestDto | CreateApiTokenBodyDto | 201:CreateApiTokenResponseDto | 201:CreateApiTokenResponseDto |
| DELETE | `/api/tokens/{uuid}` | parameters, response DTO | — | — | 200:DeleteApiTokenResponseDto | 204:empty |
| GET | `/api/users` | parameters, response DTO | — | — | 200:GetAllUsersResponseDto | 200:GetUsersResponseDto |
| PATCH | `/api/users` | request DTO, response DTO | UpdateUserRequestDto | UpdateUserBodyDto | 200:UpdateUserResponseDto | 200:UserResponseDto |
| POST | `/api/users` | request DTO, response DTO | CreateUserRequestDto | CreateUserBodyDto | 201:CreateUserResponseDto | 201:UserResponseDto |
| POST | `/api/users/bulk/all/extend-expiration-date` | request DTO, response DTO | BulkAllExtendExpirationDateRequestDto | BulkAllExtendExpirationDateBodyDto | 200:BulkAllExtendExpirationDateResponseDto | 202:empty |
| POST | `/api/users/bulk/all/reset-traffic` | response DTO | — | — | 200:BulkAllResetTrafficUsersResponseDto | 202:empty |
| POST | `/api/users/bulk/all/update` | request DTO, response DTO | BulkAllUpdateUsersRequestDto | BulkAllUpdateUsersBodyDto | 200:BulkAllUpdateUsersResponseDto | 202:empty |
| POST | `/api/users/bulk/delete` | request DTO, response DTO | BulkDeleteUsersRequestDto | BulkDeleteUsersBodyDto | 200:BulkDeleteUsersResponseDto | 204:empty |
| POST | `/api/users/bulk/delete-by-status` | request DTO, response DTO | BulkDeleteUsersByStatusRequestDto | BulkDeleteUsersByStatusBodyDto | 200:BulkDeleteUsersByStatusResponseDto | 202:empty |
| POST | `/api/users/bulk/extend-expiration-date` | request DTO, response DTO | BulkExtendExpirationDateRequestDto | BulkExtendExpirationDateBodyDto | 200:BulkExtendExpirationDateResponseDto | 204:empty |
| POST | `/api/users/bulk/reset-traffic` | request DTO, response DTO | BulkResetTrafficUsersRequestDto | BulkResetTrafficUsersBodyDto | 200:BulkResetTrafficUsersResponseDto | 202:empty |
| POST | `/api/users/bulk/revoke-subscription` | request DTO, response DTO | BulkRevokeUsersSubscriptionRequestDto | BulkRevokeUsersSubscriptionBodyDto | 200:BulkRevokeUsersSubscriptionResponseDto | 202:empty |
| POST | `/api/users/bulk/update` | request DTO, response DTO | BulkUpdateUsersRequestDto | BulkUpdateUsersBodyDto | 200:BulkUpdateUsersResponseDto | 202:empty |
| POST | `/api/users/bulk/update-squads` | request DTO, response DTO | BulkUpdateUsersSquadsRequestDto | BulkUpdateUsersSquadsBodyDto | 200:BulkUpdateUsersSquadsResponseDto | 204:empty |
| GET | `/api/users/by-short-uuid/{shortUuid}` | response DTO | — | — | 200:GetUserByShortUuidResponseDto | 200:UserResponseDto |
| GET | `/api/users/by-username/{username}` | response DTO | — | — | 200:GetUserByUsernameResponseDto | 200:UserResponseDto |
| POST | `/api/users/resolve` | request DTO, response schema | ResolveUserRequestBodyDto | ResolveUserBodyDto | 200:ResolveUserResponseDto | 200:ResolveUserResponseDto |
| GET | `/api/users/tags` | response DTO | — | — | 200:GetAllTagsResponseDto | 200:GetUsersTagsResponseDto |

This table reports changes at the OpenAPI operation and referenced top-level
DTO level. Nested model definitions are preserved in the two full JSON files;
the runtime migration and data migration consequences are explained in
[the migration guide](remnawave-v3-migration.md).
