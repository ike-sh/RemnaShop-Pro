import asyncio
import datetime
import ipaddress
import logging
import uuid as uuid_module
from urllib.parse import quote
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_HISTORY_RECORDS = 10_000
HISTORY_PAGE_SIZE = 1_000
_CLIENTS: dict[bool, httpx.AsyncClient] = {}
SUBSCRIPTION_SETTINGS_PATCH_FIELDS = frozenset({
    'uuid', 'serveJsonAtBaseSubscription', 'isShowCustomRemarks',
    'customRemarks', 'customResponseHeaders', 'randomizeHosts',
    'responseRules', 'hwidSettings',
})
USER_RESPONSE_REQUIRED = frozenset({
    'id', 'shortUuid', 'username', 'status', 'trafficLimitBytes',
    'trafficLimitStrategy', 'expireAt', 'telegramId', 'email', 'description',
    'tag', 'hwidDeviceLimit', 'externalSquadUuid', 'trojanPassword',
    'vlessUuid', 'ssPassword', 'lastTriggeredThreshold', 'subRevokedAt',
    'lastTrafficResetAt', 'createdAt', 'updatedAt', 'subscriptionUrl',
    'activeInternalSquads', 'userTraffic',
})
USER_TRAFFIC_REQUIRED = frozenset({
    'usedTrafficBytes', 'lifetimeUsedTrafficBytes', 'onlineAt',
    'firstConnectedAt', 'lastConnectedNodeUuid',
})
CREATE_USER_FIELDS = frozenset({
    'username', 'status', 'shortUuid', 'trojanPassword', 'vlessUuid',
    'ssPassword', 'trafficLimitBytes', 'trafficLimitStrategy', 'expireAt',
    'createdAt', 'lastTrafficResetAt', 'description', 'tag', 'telegramId',
    'email', 'hwidDeviceLimit', 'activeInternalSquads', 'externalSquadUuid',
})
UPDATE_USER_FIELDS = frozenset({
    'username', 'id', 'status', 'trafficLimitBytes', 'trafficLimitStrategy',
    'expireAt', 'description', 'tag', 'telegramId', 'email', 'hwidDeviceLimit',
    'activeInternalSquads', 'externalSquadUuid',
})
BULK_UPDATE_FIELDS = frozenset({
    'status', 'trafficLimitBytes', 'trafficLimitStrategy', 'expireAt',
    'description', 'telegramId', 'email', 'tag', 'hwidDeviceLimit',
    'externalSquadUuid',
})

IP_CONTROL_ENDPOINT_SPECS: tuple[tuple[str, str], ...] = (
    ('POST', '/connections/drop'),
)


class PanelApiError(RuntimeError):
    pass


class PanelContractError(PanelApiError):
    pass


class AmbiguousPanelUserError(PanelApiError):
    pass


def api_base_url(panel_url: str) -> str:
    base = (panel_url or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/api") else base + "/api"


def require_user_id(value) -> int:
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise ValueError("Remnawave 3.4.4 requires a positive numeric userId")
    return int(value)


def require_user_ids(values) -> list[int]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 500:
        raise ValueError("userIds must contain 1 to 500 numeric IDs")
    return [require_user_id(value) for value in values]


def require_uuid(value) -> str:
    try:
        return str(uuid_module.UUID(str(value)))
    except ValueError as exc:
        raise ValueError("Expected a UUID defined by the Panel contract") from exc


def _get_client(verify_tls: bool) -> httpx.AsyncClient:
    client = _CLIENTS.get(verify_tls)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=20.0, verify=verify_tls)
        _CLIENTS[verify_tls] = client
    return client


def extract_payload(resp: httpx.Response):
    try:
        data = resp.json()
    except ValueError as exc:
        raise PanelContractError("Panel returned invalid JSON") from exc
    if not isinstance(data, dict) or 'response' not in data:
        raise PanelContractError("Panel response envelope is missing response")
    return data['response']


def _expect_payload(resp, expected_type, *, allow_404=False):
    if allow_404 and resp.status_code == 404:
        return None
    if not 200 <= resp.status_code < 300:
        raise PanelApiError(f"Panel returned HTTP {resp.status_code}")
    value = extract_payload(resp)
    if not isinstance(value, expected_type):
        raise PanelContractError(f"Unexpected Panel response type: {type(value).__name__}")
    return value


def _required_list(payload, field):
    value = payload.get(field)
    if not isinstance(value, list):
        raise PanelContractError(f"Panel response is missing {field} array")
    return value


def _validate_user(user):
    if not isinstance(user, dict):
        raise PanelContractError("Panel user response must be an object")
    missing = USER_RESPONSE_REQUIRED - user.keys()
    if missing:
        raise PanelContractError(f"Panel user response is missing fields: {sorted(missing)}")
    user_id = user.get('id')
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise PanelContractError("Panel user response is missing numeric id")
    if not isinstance(user['userTraffic'], dict) or not isinstance(user['activeInternalSquads'], list):
        raise PanelContractError("Panel user response has invalid nested fields")
    if USER_TRAFFIC_REQUIRED - user['userTraffic'].keys():
        raise PanelContractError("Panel user traffic response is missing required fields")
    used = user['userTraffic']['usedTrafficBytes']
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        raise PanelContractError("Panel user response has invalid usedTrafficBytes")
    for key in ('trafficLimitBytes',):
        value = user[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PanelContractError(f"Panel user response has invalid {key}")
    if not isinstance(user['subscriptionUrl'], str) or not isinstance(user['expireAt'], str):
        raise PanelContractError("Panel user subscription fields are invalid")
    return user


def _user_payload(response):
    user = _expect_payload(response, dict, allow_404=True)
    return _validate_user(user) if user is not None else None


async def close_all_clients() -> None:
    for client in list(_CLIENTS.values()):
        if not client.is_closed:
            await client.aclose()
    _CLIENTS.clear()




def _calc_retry_delay(resp: Optional[httpx.Response], attempt: int, base: float = 0.6) -> float:
    if resp is not None and resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
    return base * attempt


def _build_request_kwargs(json_data: Optional[dict[str, Any]] = None, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if json_data is not None:
        kwargs["json"] = json_data
    if params is not None:
        kwargs["params"] = params
    return kwargs

async def safe_api_request(method, endpoint, panel_url, headers, verify_tls=True, json_data=None, params=None, retry=True):
    if not panel_url or not panel_url.startswith(('http://', 'https://')):
        raise PanelApiError("Panel URL is not configured")
    if not headers.get('Authorization', '').removeprefix('Bearer ').strip():
        raise PanelApiError("Panel token is not configured")
    url = f"{panel_url.rstrip('/')}{endpoint}"
    client = _get_client(verify_tls)
    # Non-idempotent actions opt out: a lost response must not replay a write.
    max_attempts = 3 if retry else 1

    for attempt in range(1, max_attempts + 1):
        resp = None
        try:
            req_kwargs = _build_request_kwargs(json_data=json_data, params=params)
            resp = await client.request(method.upper(), url, headers=headers, **req_kwargs)

            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_attempts:
                await asyncio.sleep(_calc_retry_delay(resp, attempt))
                continue

            if resp.status_code >= 400:
                logger.warning("Panel API returned %s [%s %s]", resp.status_code, method, endpoint)
            return resp
        except httpx.RequestError as exc:
            if attempt < max_attempts:
                await asyncio.sleep(_calc_retry_delay(resp, attempt))
                continue
            raise PanelApiError(f"Panel request failed: {method} {endpoint}") from exc


async def get_panel_user(user_id, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', f"/users/{require_user_id(user_id)}", panel_url, headers, verify_tls)
    return _user_payload(resp)


async def get_user_by_telegram_id(telegram_id, panel_url, headers, verify_tls=True):
    users = await get_users_by_telegram_id(telegram_id, panel_url, headers, verify_tls)
    if len(users) > 1:
        raise AmbiguousPanelUserError("Multiple Panel users share this Telegram ID")
    return users[0] if users else None


async def get_users_by_telegram_id(telegram_id, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/users/stream', panel_url, headers, verify_tls,
                                  params={'telegramId': str(telegram_id), 'size': 1000})
    payload = _expect_payload(resp, dict)
    users = _required_list(payload, 'users')
    if payload.get('hasMore') is not False:
        raise PanelApiError("Telegram user lookup cannot confirm a complete result page")
    for user in users:
        _validate_user(user)
    if any(user['telegramId'] != int(telegram_id) for user in users):
        raise PanelContractError("Telegram-filtered users/stream response is inconsistent")
    return users


async def get_user_by_username(username, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', f"/users/by-username/{quote(username, safe='')}", panel_url, headers, verify_tls)
    return _user_payload(resp)


async def get_user_by_short_uuid(short_uuid, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', f"/users/by-short-uuid/{quote(short_uuid, safe='')}", panel_url, headers, verify_tls)
    return _user_payload(resp)


async def get_nodes_status(panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/nodes', panel_url, headers, verify_tls)
    return _expect_payload(resp, list)


async def get_subscription_history_stats(panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/subscription-request-history/stats', panel_url, headers, verify_tls)
    return _expect_payload(resp, dict)


async def get_user_subscription_history(user_id, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', f"/users/{require_user_id(user_id)}/subscription-request-history", panel_url, headers, verify_tls)
    return _expect_payload(resp, dict)


async def get_subscription_settings(panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/subscription-settings', panel_url, headers, verify_tls)
    return _expect_payload(resp, dict)


async def patch_subscription_settings(panel_url, headers, payload, verify_tls=True):
    if not isinstance(payload, dict) or 'uuid' not in payload:
        raise ValueError("UpdateSubscriptionSettingsBodyDto requires uuid")
    unknown = set(payload) - SUBSCRIPTION_SETTINGS_PATCH_FIELDS
    if unknown:
        raise ValueError(f"Unsupported subscription settings fields: {sorted(unknown)}")
    return await safe_api_request('PATCH', '/subscription-settings', panel_url, headers, verify_tls, json_data=payload)


def subscription_settings_patch_from_current(current):
    if not isinstance(current, dict) or 'uuid' not in current:
        raise PanelContractError("Subscription settings response is missing uuid")
    return {key: current[key] for key in SUBSCRIPTION_SETTINGS_PATCH_FIELDS if key in current}


async def get_internal_squads(panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/internal-squads', panel_url, headers, verify_tls)
    return _required_list(_expect_payload(resp, dict), 'internalSquads')


async def get_internal_squad_accessible_nodes(uuid, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', f'/internal-squads/{require_uuid(uuid)}/accessible-nodes', panel_url, headers, verify_tls)
    return _required_list(_expect_payload(resp, dict), 'accessibleNodes')


async def get_bandwidth_nodes_usage(panel_url, headers, verify_tls=True):
    end = datetime.datetime.now(datetime.timezone.utc).date()
    start = end - datetime.timedelta(days=7)
    resp = await safe_api_request('GET', '/bandwidth-stats/nodes', panel_url, headers, verify_tls,
                                  params={'start': start.isoformat(), 'end': end.isoformat()})
    return _required_list(_expect_payload(resp, dict), 'topNodes')


async def bulk_move_users_to_squad(user_ids, squad_uuid, panel_url, headers, verify_tls=True):
    payload = {'userIds': require_user_ids(user_ids), 'activeInternalSquads': [require_uuid(squad_uuid)] if squad_uuid else []}
    return await safe_api_request('POST', '/users/bulk/update-squads', panel_url, headers, verify_tls, json_data=payload)


async def create_user(payload, panel_url, headers, verify_tls=True):
    if not isinstance(payload, dict) or not {'username', 'expireAt'} <= payload.keys() or set(payload) - CREATE_USER_FIELDS:
        raise ValueError("CreateUserBodyDto requires username and expireAt and has no user ID")
    return await safe_api_request('POST', '/users', panel_url, headers, verify_tls, json_data=payload)


async def patch_user(payload, panel_url, headers, verify_tls=True, *, retry=True):
    if not isinstance(payload, dict) or 'id' not in payload or set(payload) - UPDATE_USER_FIELDS:
        raise ValueError("UpdateUserBodyDto requires a numeric id")
    payload = {**payload, 'id': require_user_id(payload['id'])}
    kwargs = {} if retry else {'retry': False}
    return await safe_api_request('PATCH', '/users', panel_url, headers, verify_tls, json_data=payload,
                                  **kwargs)


async def delete_user(user_id, panel_url, headers, verify_tls=True):
    return await safe_api_request('DELETE', f"/users/{require_user_id(user_id)}", panel_url, headers, verify_tls)


async def enable_user(user_id, panel_url, headers, verify_tls=True):
    return await safe_api_request('POST', f"/users/{require_user_id(user_id)}/actions/enable", panel_url, headers, verify_tls)


async def disable_user(user_id, panel_url, headers, verify_tls=True):
    return await safe_api_request('POST', f"/users/{require_user_id(user_id)}/actions/disable", panel_url, headers, verify_tls)


async def reset_user_traffic(user_id, panel_url, headers, verify_tls=True):
    return await safe_api_request('POST', f"/users/{require_user_id(user_id)}/actions/reset-traffic", panel_url, headers, verify_tls)


async def get_subscription_request_history(panel_url, headers, verify_tls=True):
    records = []
    start = 0
    while True:
        if start >= MAX_HISTORY_RECORDS:
            raise PanelApiError("Subscription history exceeds the bounded scan limit")
        resp = await safe_api_request('GET', '/subscription-request-history', panel_url, headers,
                                      verify_tls, params={'start': start, 'size': HISTORY_PAGE_SIZE})
        payload = _expect_payload(resp, dict)
        batch = _required_list(payload, 'records')
        total = payload.get('total')
        if isinstance(total, bool) or not isinstance(total, (int, float)):
            raise PanelContractError("History response is missing numeric total")
        if total > MAX_HISTORY_RECORDS:
            raise PanelApiError("Subscription history exceeds the bounded scan limit")
        if len(batch) > HISTORY_PAGE_SIZE:
            raise PanelContractError("History page exceeds the requested size")
        records.extend(batch)
        if len(records) >= total:
            return records
        if not batch:
            raise PanelContractError("History pagination stopped before total")
        start += len(batch)


async def bulk_delete_users(user_ids, panel_url, headers, verify_tls=True):
    return await safe_api_request('POST', '/users/bulk/delete', panel_url, headers, verify_tls,
                                  json_data={"userIds": require_user_ids(user_ids)})


async def bulk_reset_traffic_users(user_ids, panel_url, headers, verify_tls=True):
    return await safe_api_request('POST', '/users/bulk/reset-traffic', panel_url, headers, verify_tls,
                                  json_data={"userIds": require_user_ids(user_ids)})


async def bulk_update_users(user_ids, fields, panel_url, headers, verify_tls=True):
    if not isinstance(fields, dict) or not fields or set(fields) - BULK_UPDATE_FIELDS:
        raise ValueError("BulkUpdateUsersBodyDto contains unsupported fields")
    return await safe_api_request('POST', '/users/bulk/update', panel_url, headers, verify_tls,
                                  json_data={"userIds": require_user_ids(user_ids), "fields": fields})


async def run_bulk_action(action, user_ids, extra, panel_url, headers, verify_tls=True):
    accepted = failed = 0
    for start in range(0, len(user_ids), 500):
        batch = require_user_ids(user_ids[start:start + 500])
        if action == 'delete':
            response = await bulk_delete_users(batch, panel_url, headers, verify_tls)
            expected = 204
        elif action == 'reset':
            response = await bulk_reset_traffic_users(batch, panel_url, headers, verify_tls)
            expected = 202
        elif action == 'extend':
            response = await bulk_extend_expiration(batch, extra['days'], panel_url, headers, verify_tls)
            expected = 204
        elif action == 'revoke':
            response = await bulk_revoke_subscriptions(batch, panel_url, headers, verify_tls)
            expected = 202
        else:
            if action == 'disable':
                fields = {'status': 'DISABLED'}
            elif action == 'expire':
                fields = {'expireAt': extra['expireAt']}
            elif action == 'traffic':
                fields = {'trafficLimitBytes': extra['trafficLimitBytes']}
            else:
                raise ValueError(f'Unknown bulk action: {action}')
            response = await bulk_update_users(batch, fields, panel_url, headers, verify_tls)
            expected = 202
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise PanelApiError(f'Bulk operation transient failure: HTTP {response.status_code}')
        if response.status_code == expected:
            accepted += len(batch)
        else:
            failed += len(batch)
    return accepted, failed


async def get_contract_capabilities(panel_url, headers, verify_tls=True):
    # These capabilities are locked by the 3.4.4 contract. Mutating endpoints
    # must never be probed with invalid empty requests.
    return {"metadata": True, "connections_drop": True}


async def set_user_metadata(user_id, metadata: dict[str, Any], panel_url, headers, verify_tls=True):
    return await safe_api_request('PUT', f'/metadata/user/{require_user_id(user_id)}',
                                  panel_url, headers, verify_tls, json_data={"metadata": metadata})


async def block_ip_address(ip: str, reason: str, panel_url, headers, verify_tls=True):
    ipaddress.ip_address(ip)
    payload = {
        "dropBy": {"by": "ipAddresses", "ipAddresses": [ip]},
        "targetNodes": {"target": "allNodes"},
    }
    return await safe_api_request('POST', '/connections/drop', panel_url, headers,
                                  verify_tls, json_data=payload)


async def get_system_health(panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/system/health', panel_url, headers, verify_tls)
    return _expect_payload(resp, dict)


async def get_system_stats(panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/system/stats', panel_url, headers, verify_tls)
    return _expect_payload(resp, dict)


async def get_system_stats_recap(panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/system/stats/recap', panel_url, headers, verify_tls)
    return _expect_payload(resp, dict)


async def get_snippet_by_key(key: str, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/snippets', panel_url, headers, verify_tls)
    for row in _required_list(_expect_payload(resp, dict), 'snippets'):
        if row['name'] == key:
            return row
    return None


async def get_subscription_page_configs(panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/subscription-page-configs', panel_url, headers, verify_tls)
    return _expect_payload(resp, dict)


async def get_external_squads(panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/external-squads', panel_url, headers, verify_tls)
    return _required_list(_expect_payload(resp, dict), 'externalSquads')


async def get_config_profiles(panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/config-profiles', panel_url, headers, verify_tls)
    return _required_list(_expect_payload(resp, dict), 'configProfiles')


async def get_user_accessible_nodes(user_id, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', f'/users/{require_user_id(user_id)}/accessible-nodes',
                                  panel_url, headers, verify_tls)
    return _required_list(_expect_payload(resp, dict), 'activeNodes')


def _positive_days(days: int, *, maximum: int | None = None) -> int:
    if isinstance(days, bool) or not isinstance(days, int) or days < 1 or (maximum is not None and days > maximum):
        raise ValueError('days must be a positive integer within the contract range')
    return days


def _hwid_devices_payload(resp):
    payload = _expect_payload(resp, dict)
    _required_list(payload, 'devices')
    if isinstance(payload.get('total'), bool) or not isinstance(payload.get('total'), (int, float)):
        raise PanelContractError('HWID response is missing numeric total')
    return payload


async def get_user_hwid_devices(user_id, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', f'/hwid/devices/{require_user_id(user_id)}', panel_url, headers, verify_tls)
    return _hwid_devices_payload(resp)


async def delete_user_hwid_device(user_id, hwid, panel_url, headers, verify_tls=True):
    if not isinstance(hwid, str) or not hwid:
        raise ValueError('HWID must be a nonempty string')
    resp = await safe_api_request('POST', '/hwid/devices/delete', panel_url, headers, verify_tls,
                                  json_data={'userId': require_user_id(user_id), 'hwid': hwid}, retry=False)
    return _hwid_devices_payload(resp)


async def delete_all_user_hwid_devices(user_id, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('POST', '/hwid/devices/delete-all', panel_url, headers, verify_tls,
                                  json_data={'userId': require_user_id(user_id)}, retry=False)
    return _hwid_devices_payload(resp)


async def get_hwid_devices_stats(panel_url, headers, verify_tls=True):
    payload = _expect_payload(await safe_api_request('GET', '/hwid/devices/stats', panel_url, headers, verify_tls), dict)
    _required_list(payload, 'byPlatform')
    if not isinstance(payload.get('stats'), dict):
        raise PanelContractError('HWID stats response is missing stats')
    return payload


async def get_top_users_by_hwid_devices(panel_url, headers, size=5, verify_tls=True):
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 100:
        raise ValueError('HWID top users size must be 1 to 100')
    payload = _expect_payload(await safe_api_request('GET', '/hwid/devices/top-users', panel_url, headers,
                                                      verify_tls, params={'start': 0, 'size': size}), dict)
    _required_list(payload, 'users')
    return payload


async def revoke_user_subscription(user_id, panel_url, headers, revoke_only_passwords=False, verify_tls=True):
    if not isinstance(revoke_only_passwords, bool):
        raise ValueError('revokeOnlyPasswords must be boolean')
    resp = await safe_api_request('POST', f'/users/{require_user_id(user_id)}/actions/revoke',
                                  panel_url, headers, verify_tls,
                                  json_data={'revokeOnlyPasswords': revoke_only_passwords}, retry=False)
    return _validate_user(_expect_payload(resp, dict))


async def extend_user_expiration(user_id, days, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('POST', f'/users/{require_user_id(user_id)}/actions/extend',
                                  panel_url, headers, verify_tls, json_data={'days': _positive_days(days)}, retry=False)
    return _validate_user(_expect_payload(resp, dict))


async def bulk_extend_expiration(user_ids, days, panel_url, headers, verify_tls=True):
    return await safe_api_request('POST', '/users/bulk/extend-expiration-date', panel_url, headers,
                                  verify_tls, json_data={'userIds': require_user_ids(user_ids),
                                                         'extendDays': _positive_days(days, maximum=9999)}, retry=False)


async def bulk_revoke_subscriptions(user_ids, panel_url, headers, verify_tls=True):
    return await safe_api_request('POST', '/users/bulk/revoke-subscription', panel_url, headers,
                                  verify_tls, json_data={'userIds': require_user_ids(user_ids)}, retry=False)


async def start_node_geocheck(node_uuid, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('POST', f'/connections/geocheck/{require_uuid(node_uuid)}',
                                  panel_url, headers, verify_tls, json_data={}, retry=False)
    if resp.status_code != 201:
        raise PanelApiError(f'Panel returned HTTP {resp.status_code} for GeoCheck start')
    payload = _expect_payload(resp, dict)
    if not isinstance(payload.get('jobId'), str) or not payload['jobId']:
        raise PanelContractError('GeoCheck response is missing jobId')
    return payload['jobId']


async def get_node_geocheck_result(job_id, panel_url, headers, verify_tls=True):
    if not isinstance(job_id, str) or not job_id:
        raise ValueError('GeoCheck jobId must be nonempty')
    resp = await safe_api_request('GET', f'/connections/geocheck/{quote(job_id, safe="")}',
                                  panel_url, headers, verify_tls, retry=False)
    payload = _expect_payload(resp, dict)
    if not isinstance(payload.get('isCompleted'), bool) or not isinstance(payload.get('isFailed'), bool):
        raise PanelContractError('GeoCheck result is missing completion flags')
    if payload.get('result') is not None and not isinstance(payload['result'], dict):
        raise PanelContractError('GeoCheck result has invalid result data')
    return payload


async def get_system_stats_digest(start, end, panel_url, headers, verify_tls=True):
    resp = await safe_api_request('GET', '/system/stats/digest', panel_url, headers, verify_tls,
                                  params={'start': start, 'end': end})
    return _expect_payload(resp, dict)


async def get_system_http_stats(panel_url, headers, verify_tls=True):
    payload = _expect_payload(await safe_api_request('GET', '/system/stats/http', panel_url, headers, verify_tls), dict)
    _required_list(payload, 'routes')
    return payload


async def get_system_nodes_metrics(panel_url, headers, verify_tls=True):
    payload = _expect_payload(await safe_api_request('GET', '/system/nodes/metrics', panel_url, headers, verify_tls), dict)
    _required_list(payload, 'nodes')
    return payload


async def resolve_panel_user(field, value, panel_url, headers, verify_tls=True):
    if field not in {'id', 'shortUuid', 'username'}:
        raise ValueError('ResolveUserBodyDto accepts exactly one identity field')
    if field == 'id':
        value = require_user_id(value)
    elif not isinstance(value, str) or not value:
        raise ValueError('Resolve identity must be nonempty')
    resp = await safe_api_request('POST', '/users/resolve', panel_url, headers, verify_tls,
                                  json_data={field: value})
    payload = _expect_payload(resp, dict, allow_404=True)
    if payload is not None and (isinstance(payload.get('id'), bool) or not isinstance(payload.get('id'), int)):
        raise PanelContractError('Resolve response is missing numeric id')
    return payload


async def get_panel_user_tags(panel_url, headers, verify_tls=True):
    payload = _expect_payload(await safe_api_request('GET', '/users/tags', panel_url, headers, verify_tls), dict)
    return _required_list(payload, 'tags')
