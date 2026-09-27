import json
import tempfile
import unittest
from pathlib import Path

from docker.config import ensure_config


class TestEntrypointConfig(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_first_install_writes_json_without_shell_interpolation(self):
        env = {
            "ADMIN_ID": "123",
            "BOT_TOKEN": '123:token-with-"quotes"',
            "PANEL_URL": "https://panel.example",
            "PANEL_VERIFY_TLS": "false",
        }
        result = ensure_config(self.path, env)
        self.assertEqual(result["bot_token"], env["BOT_TOKEN"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), result)
        self.assertIs(result["panel_verify_tls"], False)

    def test_update_overrides_deployment_secrets_and_preserves_admin_settings(self):
        before = {
            "admin_id": "100",
            "bot_token": "old-bot",
            "panel_token": "admin-managed",
            "panel_url": "https://ui-panel.example",
            "group_uuid": "ui-squad",
            "sub_domain": "https://ui-sub.example",
            "panel_verify_tls": False,
        }
        self.path.write_text(json.dumps(before), encoding="utf-8")
        changed = ensure_config(self.path, {
            "ADMIN_ID": "200", "BOT_TOKEN": "new-bot",
            "PANEL_TOKEN": "new-panel", "PANEL_URL": "https://ignored.example",
        })
        self.assertEqual((changed["admin_id"], changed["bot_token"], changed["panel_token"]),
                         ("200", "new-bot", "new-panel"))
        self.assertEqual((changed["panel_url"], changed["group_uuid"], changed["sub_domain"],
                          changed["panel_verify_tls"]),
                         (before["panel_url"], before["group_uuid"], before["sub_domain"], False))
        unchanged = ensure_config(self.path, {"PANEL_TOKEN": "", "BOT_TOKEN": "", "ADMIN_ID": ""})
        self.assertEqual(unchanged, changed)

    def test_old_template_panel_token_does_not_replace_ui_secret(self):
        self.path.write_text(json.dumps({
            "admin_id": "123", "bot_token": "real-bot", "panel_token": "ui-secret",
        }), encoding="utf-8")
        result = ensure_config(self.path, {"PANEL_TOKEN": "your_panel_api_token"})
        self.assertEqual(result["panel_token"], "ui-secret")

    def test_missing_required_values_does_not_create_config(self):
        with self.assertRaises(ValueError):
            ensure_config(self.path, {"ADMIN_ID": "123"})
        self.assertFalse(self.path.exists())

    def test_invalid_tls_value_never_disables_verification_silently(self):
        with self.assertRaises(ValueError):
            ensure_config(self.path, {
                "ADMIN_ID": "123", "BOT_TOKEN": "test", "PANEL_VERIFY_TLS": "maybe",
            })
        self.assertFalse(self.path.exists())
