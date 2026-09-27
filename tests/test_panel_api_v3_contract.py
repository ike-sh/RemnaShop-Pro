import ast
import datetime
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from services import panel_api


SPEC = json.loads(Path("docs/remnawave-openapi.json").read_text(encoding="utf-8"))
URL = "https://panel.example/api"
HEADERS = {"Authorization": "Bearer test-token"}
SQUAD_UUID = "11111111-1111-4111-8111-111111111111"


def full_user(user_id=42, telegram_id=123):
    return {
        "id": user_id,
        "shortUuid": "short",
        "username": "alice",
        "status": "ACTIVE",
        "trafficLimitBytes": 1000,
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": "2026-12-01T00:00:00Z",
        "telegramId": telegram_id,
        "email": None,
        "description": None,
        "tag": None,
        "hwidDeviceLimit": None,
        "externalSquadUuid": None,
        "trojanPassword": "",
        "vlessUuid": "00000000-0000-0000-0000-000000000000",
        "ssPassword": "",
        "lastTriggeredThreshold": 0,
        "subRevokedAt": None,
        "lastTrafficResetAt": None,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "subscriptionUrl": "https://sub.example/short",
        "activeInternalSquads": [],
        "userTraffic": {
            "usedTrafficBytes": 0,
            "lifetimeUsedTrafficBytes": 0,
            "onlineAt": None,
            "firstConnectedAt": None,
            "lastConnectedNodeUuid": None,
        },
    }


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        return {"response": self.payload}


class TestPanelApiV3Contract(unittest.IsolatedAsyncioTestCase):
    async def capture(self, call, payload=None, status=200):
        requests = []

        async def request(method, path, panel_url, headers, verify_tls=True, json_data=None, params=None):
            requests.append((method, path, json_data, params))
            return Response(status, payload)

        with patch.object(panel_api, "safe_api_request", new=request):
            result = await call()
        return result, requests

    def assert_operation(self, request, method, concrete_path, contract_path, body=None):
        actual_method, actual_path, actual_body, _ = request
        self.assertEqual((actual_method, actual_path), (method, concrete_path))
        operation = SPEC["paths"][f"/api{contract_path}"][method.lower()]
        self.assertEqual(actual_body, body)
        if body is not None:
            ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            schema = SPEC["components"]["schemas"][ref.split("/")[-1]]
            self.assertTrue(set(schema.get("required", [])) <= set(body))
            self.assertTrue(set(body) <= set(schema.get("properties", {})))

    async def test_user_id_routes_and_response(self):
        user = full_user()
        cases = [
            (lambda: panel_api.get_panel_user(42, URL, HEADERS), "GET", "/users/42", "/users/{userId}", 200, user),
            (lambda: panel_api.delete_user(42, URL, HEADERS), "DELETE", "/users/42", "/users/{userId}", 204, None),
            (lambda: panel_api.enable_user(42, URL, HEADERS), "POST", "/users/42/actions/enable", "/users/{userId}/actions/enable", 200, user),
            (lambda: panel_api.disable_user(42, URL, HEADERS), "POST", "/users/42/actions/disable", "/users/{userId}/actions/disable", 200, user),
            (lambda: panel_api.reset_user_traffic(42, URL, HEADERS), "POST", "/users/42/actions/reset-traffic", "/users/{userId}/actions/reset-traffic", 200, user),
        ]
        for call, method, path, contract, status, payload in cases:
            with self.subTest(path=path):
                result, requests = await self.capture(call, payload, status)
                self.assertEqual(len(requests), 1)
                self.assert_operation(requests[0], method, path, contract)
                if method == "GET":
                    self.assertEqual(result, user)
        with self.assertRaises(ValueError):
            await panel_api.get_panel_user("a8b62499-056b-4126-98af-b5cf0501c8e3", URL, HEADERS)
        for path in ("/api/users/{userId}", "/api/metadata/user/{userId}"):
            operation = next(iter(SPEC["paths"][path].values()))
            self.assertEqual(operation["parameters"][0]["schema"]["type"], "number")

    async def test_create_update_and_bulk_bodies(self):
        create = {"username": "alice", "expireAt": "2026-12-01T00:00:00Z"}
        _, requests = await self.capture(lambda: panel_api.create_user(create, URL, HEADERS), {"id": 42}, 201)
        self.assert_operation(requests[0], "POST", "/users", "/users", create)
        update = {"id": 42, "status": "ACTIVE"}
        _, requests = await self.capture(lambda: panel_api.patch_user(update, URL, HEADERS), {"id": 42})
        self.assert_operation(requests[0], "PATCH", "/users", "/users", update)
        bulk = [
            (lambda: panel_api.bulk_delete_users([42], URL, HEADERS), "/users/bulk/delete", {"userIds": [42]}, 204),
            (lambda: panel_api.bulk_reset_traffic_users([42], URL, HEADERS), "/users/bulk/reset-traffic", {"userIds": [42]}, 202),
            (lambda: panel_api.bulk_update_users([42], {"status": "DISABLED"}, URL, HEADERS), "/users/bulk/update", {"userIds": [42], "fields": {"status": "DISABLED"}}, 202),
            (lambda: panel_api.bulk_move_users_to_squad([42], SQUAD_UUID, URL, HEADERS), "/users/bulk/update-squads", {"userIds": [42], "activeInternalSquads": [SQUAD_UUID]}, 204),
        ]
        for call, path, body, status in bulk:
            with self.subTest(path=path):
                _, requests = await self.capture(call, None, status)
                self.assert_operation(requests[0], "POST", path, path, body)
        with self.assertRaises(ValueError):
            await panel_api.bulk_delete_users(["legacy-uuid"], URL, HEADERS)

    async def test_lookup_history_and_nodes_wrappers(self):
        payload = {"users": [full_user()], "hasMore": False}
        result, requests = await self.capture(
            lambda: panel_api.get_user_by_telegram_id(123, URL, HEADERS), payload
        )
        self.assertEqual(result["id"], 42)
        self.assert_operation(requests[0], "GET", "/users/stream", "/users/stream")
        self.assertEqual(requests[0][3], {"telegramId": "123", "size": 1000})
        result, requests = await self.capture(
            lambda: panel_api.get_user_accessible_nodes(42, URL, HEADERS),
            {"userId": 42, "activeNodes": [{"nodeName": "N1"}]},
        )
        self.assertEqual(result, [{"nodeName": "N1"}])
        self.assert_operation(requests[0], "GET", "/users/42/accessible-nodes", "/users/{userId}/accessible-nodes")
        result, requests = await self.capture(
            lambda: panel_api.get_user_subscription_history(42, URL, HEADERS),
            {"total": 1, "records": [{"userId": 42}]},
        )
        self.assertEqual(result["records"][0]["userId"], 42)
        self.assert_operation(requests[0], "GET", "/users/42/subscription-request-history",
                              "/users/{userId}/subscription-request-history")
        result, requests = await self.capture(lambda: panel_api.get_nodes_status(URL, HEADERS),
                                              [{"name": "N1", "isConnected": True}])
        self.assertEqual(len(result), 1)
        self.assert_operation(requests[0], "GET", "/nodes", "/nodes")

    async def test_history_pagination_and_metadata(self):
        calls = []

        async def request(method, path, panel_url, headers, verify_tls=True, json_data=None, params=None):
            calls.append((method, path, json_data, params))
            if path == "/subscription-request-history":
                return Response(200, {"records": [{"userId": 42, "requestAt": "2026-09-01T00:00:00Z"}], "total": 1})
            return Response(200, {"metadata": {}})

        with patch.object(panel_api, "safe_api_request", new=request):
            history = await panel_api.get_subscription_request_history(URL, HEADERS)
            await panel_api.set_user_metadata(42, {"k": "v"}, URL, HEADERS)
        self.assertEqual(len(history), 1)
        self.assertEqual(calls[0][3], {"start": 0, "size": 1000})
        self.assert_operation(calls[0], "GET", "/subscription-request-history", "/subscription-request-history")
        self.assert_operation(calls[1], "PUT", "/metadata/user/42", "/metadata/user/{userId}",
                              {"metadata": {"k": "v"}})

    async def test_history_scan_has_a_hard_limit_and_paginates(self):
        starts = []

        async def two_pages(method, path, panel_url, headers, verify_tls=True,
                            json_data=None, params=None):
            starts.append(params["start"])
            return Response(200, {"records": [{"userId": 42, "requestAt": "2026-09-01T00:00:00Z"}],
                                  "total": 2})

        with patch.object(panel_api, "safe_api_request", new=two_pages):
            self.assertEqual(len(await panel_api.get_subscription_request_history(URL, HEADERS)), 2)
        self.assertEqual(starts, [0, 1])

        count = 0

        async def oversized(method, path, panel_url, headers, verify_tls=True,
                            json_data=None, params=None):
            nonlocal count
            count += 1
            return Response(200, {"records": [{"userId": 42}], "total": 10_001})

        with patch.object(panel_api, "safe_api_request", new=oversized):
            with self.assertRaisesRegex(panel_api.PanelApiError, "bounded scan limit"):
                await panel_api.get_subscription_request_history(URL, HEADERS)
        self.assertEqual(count, 1)

    async def test_squads_snippets_inventory_and_bandwidth(self):
        cases = [
            (lambda: panel_api.get_internal_squads(URL, HEADERS), "/internal-squads", {"internalSquads": [1]}, [1]),
            (lambda: panel_api.get_internal_squad_accessible_nodes(SQUAD_UUID, URL, HEADERS), f"/internal-squads/{SQUAD_UUID}/accessible-nodes", {"accessibleNodes": [2]}, [2]),
            (lambda: panel_api.get_external_squads(URL, HEADERS), "/external-squads", {"externalSquads": [3]}, [3]),
            (lambda: panel_api.get_config_profiles(URL, HEADERS), "/config-profiles", {"configProfiles": [4]}, [4]),
            (lambda: panel_api.get_snippet_by_key("welcome", URL, HEADERS), "/snippets", {"snippets": [{"name": "welcome", "snippet": "hi"}]}, {"name": "welcome", "snippet": "hi"}),
            (lambda: panel_api.get_bandwidth_nodes_usage(URL, HEADERS), "/bandwidth-stats/nodes", {"topNodes": [{"name": "N1", "total": 12}]}, [{"name": "N1", "total": 12}]),
        ]
        for call, path, payload, expected in cases:
            with self.subTest(path=path):
                result, requests = await self.capture(call, payload)
                self.assertEqual(result, expected)
                contract_path = path.replace(SQUAD_UUID, "{uuid}")
                self.assert_operation(requests[0], "GET", path, contract_path)
                if path == "/bandwidth-stats/nodes":
                    datetime.date.fromisoformat(requests[0][3]["start"])
                    datetime.date.fromisoformat(requests[0][3]["end"])

    async def test_strict_envelope_and_no_route_fallback(self):
        class BadResponse(Response):
            def json(self):
                return {"data": []}
        with self.assertRaises(panel_api.PanelContractError):
            panel_api.extract_payload(BadResponse())
        _, requests = await self.capture(lambda: panel_api.get_snippet_by_key("missing", URL, HEADERS),
                                         {"snippets": []})
        self.assertEqual(len(requests), 1)

    async def test_other_read_operations_use_declared_wrappers(self):
        cases = [
            (lambda: panel_api.get_user_by_username("alice", URL, HEADERS),
             "/users/by-username/alice", "/users/by-username/{username}", full_user(), full_user()),
            (lambda: panel_api.get_user_by_short_uuid("short", URL, HEADERS),
             "/users/by-short-uuid/short", "/users/by-short-uuid/{shortUuid}", full_user(), full_user()),
            (lambda: panel_api.get_subscription_history_stats(URL, HEADERS),
             "/subscription-request-history/stats", "/subscription-request-history/stats",
             {"hourlyRequestStats": []}, {"hourlyRequestStats": []}),
            (lambda: panel_api.get_subscription_settings(URL, HEADERS),
             "/subscription-settings", "/subscription-settings", {"uuid": "settings-id"}, {"uuid": "settings-id"}),
            (lambda: panel_api.get_subscription_page_configs(URL, HEADERS),
             "/subscription-page-configs", "/subscription-page-configs", {"configs": [], "total": 0},
             {"configs": [], "total": 0}),
            (lambda: panel_api.get_system_health(URL, HEADERS),
             "/system/health", "/system/health", {"status": "ok"}, {"status": "ok"}),
            (lambda: panel_api.get_system_stats(URL, HEADERS),
             "/system/stats", "/system/stats", {}, {}),
            (lambda: panel_api.get_system_stats_recap(URL, HEADERS),
             "/system/stats/recap", "/system/stats/recap", {}, {}),
        ]
        for call, path, contract_path, payload, expected in cases:
            with self.subTest(path=path):
                result, requests = await self.capture(call, payload)
                self.assertEqual(result, expected)
                self.assert_operation(requests[0], "GET", path, contract_path)

    async def test_settings_patch_and_connections_drop(self):
        settings = {"uuid": "settings-id", "randomizeHosts": True}
        _, requests = await self.capture(
            lambda: panel_api.patch_subscription_settings(URL, HEADERS, settings), {}, 200
        )
        self.assert_operation(requests[0], "PATCH", "/subscription-settings",
                              "/subscription-settings", settings)
        with self.assertRaises(ValueError):
            await panel_api.patch_subscription_settings(
                URL, HEADERS, {"uuid": "settings-id", "allowInsecure": True}
            )
        _, requests = await self.capture(
            lambda: panel_api.block_ip_address("192.0.2.1", "incident", URL, HEADERS), {}, 202
        )
        self.assert_operation(requests[0], "POST", "/connections/drop", "/connections/drop",
                              {"dropBy": {"by": "ipAddresses", "ipAddresses": ["192.0.2.1"]},
                               "targetNodes": {"target": "allNodes"}})

    async def test_bulk_action_acceptance_status_matches_contract(self):
        calls = []

        async def request(method, path, panel_url, headers, verify_tls=True, json_data=None, params=None):
            calls.append((method, path, json_data, params))
            return Response(204 if path.endswith("/delete") else 202)

        with patch.object(panel_api, "safe_api_request", new=request):
            accepted, failed = await panel_api.run_bulk_action(
                "disable", [42], {}, URL, HEADERS
            )
        self.assertEqual((accepted, failed), (1, 0))
        self.assert_operation(calls[0], "POST", "/users/bulk/update", "/users/bulk/update",
                              {"userIds": [42], "fields": {"status": "DISABLED"}})

    async def test_transport_base_url_auth_query_and_error_mapping(self):
        captured = []

        def handler(request):
            captured.append(request)
            return httpx.Response(200, json={"response": {"id": 42}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.object(panel_api, "_get_client", return_value=client):
                response = await panel_api.safe_api_request(
                    "GET", "/users/42", URL, HEADERS, params={"size": 1}
                )
        self.assertEqual(panel_api.extract_payload(response), {"id": 42})
        self.assertEqual(captured[0].url.path, "/api/users/42")
        self.assertEqual(captured[0].url.params["size"], "1")
        self.assertEqual(captured[0].headers["authorization"], "Bearer test-token")
        with self.assertRaises(panel_api.PanelApiError):
            await panel_api.safe_api_request("GET", "/users/42", "", HEADERS)

    def test_every_runtime_route_exists_in_vendored_contract(self):
        self.assertEqual(panel_api.api_base_url("https://panel.example/api/"),
                         "https://panel.example/api")
        self.assertEqual(panel_api.api_base_url("https://panel.example/"),
                         "https://panel.example/api")
        required_user_fields = SPEC["components"]["schemas"]["UserResponseDto"]["properties"]["response"]["required"]
        self.assertEqual(panel_api.USER_RESPONSE_REQUIRED, set(required_user_fields))
        required_traffic_fields = SPEC["components"]["schemas"]["UserResponseDto"]["properties"]["response"]["properties"]["userTraffic"]["required"]
        self.assertEqual(panel_api.USER_TRAFFIC_REQUIRED, set(required_traffic_fields))
        schemas = SPEC["components"]["schemas"]
        self.assertEqual(panel_api.CREATE_USER_FIELDS, set(schemas["CreateUserBodyDto"]["properties"]))
        self.assertEqual(panel_api.UPDATE_USER_FIELDS, set(schemas["UpdateUserBodyDto"]["properties"]))
        self.assertEqual(panel_api.BULK_UPDATE_FIELDS,
                         set(schemas["BulkUpdateUsersBodyDto"]["properties"]["fields"]["properties"]))
        self.assertEqual(SPEC["info"]["version"], "3.4.4")
        tree = ast.parse(Path("services/panel_api.py").read_text(encoding="utf-8"))
        operations = {
            (method.upper(), path.removeprefix("/api"))
            for path, methods in SPEC["paths"].items()
            for method in methods if method in {"get", "post", "put", "patch", "delete"}
        }
        unknown = []
        inventory = Path("docs/remnawave-api-inventory.md").read_text(encoding="utf-8")
        verified_count = 0
        for function in tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "safe_api_request" and len(node.args) >= 2):
                    continue
                method = ast.literal_eval(node.args[0])
                path_node = node.args[1]
                if isinstance(path_node, ast.Constant):
                    path = path_node.value
                elif isinstance(path_node, ast.JoinedStr):
                    path = "".join(part.value if isinstance(part, ast.Constant) else "{}"
                                   for part in path_node.values)
                    if path.startswith("/users/{}"):
                        self.assertTrue(all(
                            isinstance(part.value, ast.Call)
                            and isinstance(part.value.func, ast.Name)
                            and part.value.func.id == "require_user_id"
                            for part in path_node.values if isinstance(part, ast.FormattedValue)
                        ), function.name)
                else:
                    unknown.append((function.name, "dynamic path"))
                    continue
                runtime_segments = path.split("/")
                matches = [
                    actual_path for actual_method, actual_path in operations
                    if method == actual_method
                    and len(runtime_segments) == len(actual_path.split("/"))
                    and all(left == right or (
                        left == "{}" and right.startswith("{") and right.endswith("}")
                    ) for left, right in zip(runtime_segments, actual_path.split("/")))
                ]
                if len(matches) != 1:
                    unknown.append((function.name, method, path))
                else:
                    verified_count += 1
                    self.assertIn(
                        f"| {function.name} | {method} | `/api{matches[0]}` |",
                        inventory,
                    )
        self.assertEqual(unknown, [])
        self.assertEqual(verified_count, 33)
        source = Path("services/panel_api.py").read_text(encoding="utf-8")
        self.assertNotIn('"uuids"', source)
        self.assertNotIn("'/ip-control", source)
        for path in (Path("bot.py"), *Path("handlers").glob("*.py"),
                     *Path("jobs").glob("*.py")):
            self.assertNotIn("safe_api_request(", path.read_text(encoding="utf-8"), str(path))
