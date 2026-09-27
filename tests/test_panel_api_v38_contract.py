import json
import unittest
from pathlib import Path
from unittest.mock import patch

from services import panel_api
from test_panel_api_v3_contract import Response, full_user


SPEC = json.loads(Path('docs/remnawave-openapi.json').read_text(encoding='utf-8'))
URL = 'https://panel.example/api'
HEADERS = {'Authorization': 'Bearer test-token'}
NODE = '11111111-1111-4111-8111-111111111111'


class TestPanelApiV38Contract(unittest.IsolatedAsyncioTestCase):
    async def capture(self, call, payload=None, status=200):
        calls = []

        async def request(method, path, panel_url, headers, verify_tls=True, json_data=None,
                          params=None, retry=True):
            calls.append((method, path, json_data, params, retry))
            return Response(status, payload)

        with patch.object(panel_api, 'safe_api_request', new=request):
            result = await call()
        return result, calls

    def assert_contract(self, request, method, path, schema_path):
        self.assertEqual(request[:2], (method, path))
        operation = SPEC['paths']['/api' + schema_path][method.lower()]
        body = request[2]
        if body is not None:
            ref = operation['requestBody']['content']['application/json']['schema']['$ref']
            schema = SPEC['components']['schemas'][ref.split('/')[-1]]
            self.assertLessEqual(set(schema.get('required', [])), set(body))
            self.assertLessEqual(set(body), set(schema['properties']))
        self.assertEqual(set((request[3] or {}).keys()),
                         {p['name'] for p in operation.get('parameters', []) if p.get('in') == 'query'})

    async def test_single_user_actions_and_non_retry(self):
        user = full_user()
        for call, path, body, contract in [
            (lambda: panel_api.revoke_user_subscription(42, URL, HEADERS),
             '/users/42/actions/revoke', {'revokeOnlyPasswords': False}, '/users/{userId}/actions/revoke'),
            (lambda: panel_api.extend_user_expiration(42, 30, URL, HEADERS),
             '/users/42/actions/extend', {'days': 30}, '/users/{userId}/actions/extend'),
        ]:
            with self.subTest(path=path):
                result, calls = await self.capture(call, user)
                self.assertEqual(result['id'], 42)
                self.assertEqual(calls[0][2], body)
                self.assertFalse(calls[0][4])
                self.assert_contract(calls[0], 'POST', path, contract)

    async def test_bulk_status_and_numeric_dtos(self):
        cases = [
            (lambda: panel_api.bulk_extend_expiration([42, 43], 30, URL, HEADERS),
             '/users/bulk/extend-expiration-date', {'userIds': [42, 43], 'extendDays': 30}, 204),
            (lambda: panel_api.bulk_revoke_subscriptions([42], URL, HEADERS),
             '/users/bulk/revoke-subscription', {'userIds': [42]}, 202),
        ]
        for call, path, body, status in cases:
            with self.subTest(path=path):
                result, calls = await self.capture(call, status=status)
                self.assertEqual(result.status_code, status)
                self.assertEqual(calls[0][2], body)
                self.assertFalse(calls[0][4])
                self.assert_contract(calls[0], 'POST', path, path)
        with self.assertRaises(ValueError):
            await panel_api.bulk_revoke_subscriptions(['old-uuid'], URL, HEADERS)

    async def test_hwid_geocheck_and_resolve_shapes(self):
        devices = {'total': 1, 'devices': [{'hwid': 'secret', 'userId': 42}]}
        cases = [
            (lambda: panel_api.get_user_hwid_devices(42, URL, HEADERS),
             'GET', '/hwid/devices/42', '/hwid/devices/{userId}', None, devices, 200),
            (lambda: panel_api.delete_user_hwid_device(42, 'secret', URL, HEADERS),
             'POST', '/hwid/devices/delete', '/hwid/devices/delete', {'userId': 42, 'hwid': 'secret'}, devices, 200),
            (lambda: panel_api.delete_all_user_hwid_devices(42, URL, HEADERS),
             'POST', '/hwid/devices/delete-all', '/hwid/devices/delete-all', {'userId': 42}, devices, 200),
            (lambda: panel_api.start_node_geocheck(NODE, URL, HEADERS),
             'POST', '/connections/geocheck/' + NODE, '/connections/geocheck/{nodeUuid}', {}, {'jobId': 'job-1'}, 201),
            (lambda: panel_api.get_node_geocheck_result('job-1', URL, HEADERS),
             'GET', '/connections/geocheck/job-1', '/connections/geocheck/{jobId}', None,
             {'isCompleted': True, 'isFailed': False, 'result': None}, 200),
            (lambda: panel_api.resolve_panel_user('id', 42, URL, HEADERS),
             'POST', '/users/resolve', '/users/resolve', {'id': 42},
             {'id': 42, 'username': 'alice', 'shortUuid': 'short'}, 200),
        ]
        for call, method, path, contract, body, payload, status in cases:
            with self.subTest(path=path):
                _, requests = await self.capture(call, payload, status)
                self.assertEqual(requests[0][2], body)
                self.assert_contract(requests[0], method, path, contract)

    async def test_bulk_worker_does_not_replay_204_or_202(self):
        for action, expected in [('extend', 204), ('revoke', 202)]:
            calls = []

            async def request(method, path, panel_url, headers, verify_tls=True, json_data=None,
                              params=None, retry=True):
                calls.append((path, json_data, retry))
                return Response(expected)

            with patch.object(panel_api, 'safe_api_request', new=request):
                accepted, failed = await panel_api.run_bulk_action(
                    action, [42], {'days': 30}, URL, HEADERS)
            self.assertEqual((accepted, failed), (1, 0))
            self.assertEqual(len(calls), 1)
            self.assertFalse(calls[0][2])

    async def test_stats_and_tags_read_contract(self):
        calls = [
            (lambda: panel_api.get_hwid_devices_stats(URL, HEADERS), '/hwid/devices/stats',
             {'stats': {}, 'byPlatform': []}, None),
            (lambda: panel_api.get_top_users_by_hwid_devices(URL, HEADERS, size=10),
             '/hwid/devices/top-users', {'users': [], 'total': 0}, {'start': 0, 'size': 10}),
            (lambda: panel_api.get_system_stats_digest('2026-09-01T00:00:00Z',
                                                       '2026-09-08T00:00:00Z', URL, HEADERS),
             '/system/stats/digest', {'users': {}, 'traffic': {}, 'hwidDevices': {}},
             {'start': '2026-09-01T00:00:00Z', 'end': '2026-09-08T00:00:00Z'}),
            (lambda: panel_api.get_system_http_stats(URL, HEADERS), '/system/stats/http',
             {'routes': [], 'total': 0}, None),
            (lambda: panel_api.get_system_nodes_metrics(URL, HEADERS), '/system/nodes/metrics',
             {'nodes': []}, None),
            (lambda: panel_api.get_panel_user_tags(URL, HEADERS), '/users/tags',
             {'tags': ['VIP']}, None),
        ]
        for call, path, payload, params in calls:
            with self.subTest(path=path):
                _, requests = await self.capture(call, payload)
                self.assertEqual(requests[0][3], params)
                self.assert_contract(requests[0], 'GET', path, path)

    async def test_geocheck_requires_official_job_id(self):
        with self.assertRaises(panel_api.PanelContractError):
            await self.capture(lambda: panel_api.start_node_geocheck(NODE, URL, HEADERS),
                               payload={}, status=201)
