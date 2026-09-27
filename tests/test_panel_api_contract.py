import unittest
from unittest.mock import patch

from services import panel_api


class _Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code


class TestPanelApiMetadataContract(unittest.IsolatedAsyncioTestCase):
    async def test_set_user_metadata_uses_numeric_id_path_and_metadata_only_body(self):
        captured = {}

        async def fake_request(method, endpoint, panel_url, headers, verify_tls=True, json_data=None, params=None):
            captured["method"] = method
            captured["endpoint"] = endpoint
            captured["json_data"] = json_data
            return _Resp(200)

        with patch("services.panel_api.safe_api_request", new=fake_request):
            resp = await panel_api.set_user_metadata(
                123,
                {"k": "v"},
                "https://panel.example/api",
                {"Authorization": "Bearer token"},
                True,
            )

        self.assertIsNotNone(resp)
        self.assertEqual(captured["method"], "PUT")
        self.assertEqual("/metadata/user/123", captured["endpoint"])
        self.assertEqual(captured["json_data"], {"metadata": {"k": "v"}})
        self.assertNotIn("userUuid", captured["json_data"])

if __name__ == "__main__":
    unittest.main()
