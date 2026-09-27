import asyncio
import importlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.panel_api import PanelApiError
from storage import db as storage_db
from storage.db import bind_legacy_subscription, db_query, init_db


class TestLegacyDatabaseMigration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "legacy.db")
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER,
                uuid TEXT, created_at TIMESTAMP
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT UNIQUE,
                tg_id INTEGER NOT NULL, plan_key TEXT NOT NULL,
                order_type TEXT NOT NULL, target_uuid TEXT, status TEXT NOT NULL,
                payment_text TEXT, admin_message_id INTEGER, menu_message_id INTEGER,
                waiting_message_id INTEGER, delivered_uuid TEXT, error_message TEXT,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE anomaly_whitelist (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_uuid TEXT UNIQUE,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE anomaly_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_uuid TEXT NOT NULL,
                risk_level TEXT NOT NULL, risk_score INTEGER NOT NULL,
                ip_count INTEGER NOT NULL, ua_diversity INTEGER NOT NULL,
                density INTEGER NOT NULL, action_taken TEXT NOT NULL,
                evidence_summary TEXT, created_at INTEGER NOT NULL
            );
            INSERT INTO subscriptions (tg_id, uuid, created_at)
                VALUES (123, 'legacy-user-uuid', 1);
            INSERT INTO orders
                (order_id, tg_id, plan_key, order_type, target_uuid,
                 status, delivered_uuid, created_at, updated_at)
                VALUES ('old-order', 123, 'p1', 'renew', 'legacy-user-uuid',
                        'delivered', 'legacy-user-uuid', 1, 1);
            INSERT INTO anomaly_whitelist (user_uuid, created_at)
                VALUES ('legacy-user-uuid', 1);
            INSERT INTO anomaly_events
                (user_uuid, risk_level, risk_score, ip_count, ua_diversity,
                 density, action_taken, created_at)
                VALUES ('legacy-user-uuid', '高', 100, 2, 2, 1, '禁用', 1);
        """)
        conn.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_old_records_survive_upgrade_and_bind_atomically(self):
        init_db(self.db)
        init_db(self.db)
        row = db_query(self.db, "SELECT * FROM subscriptions WHERE id=1", one=True)
        self.assertEqual(row["uuid"], "legacy-user-uuid")
        self.assertIsNone(row["user_id"])
        self.assertEqual(row["migration_status"], "pending")
        with self.assertRaises(ValueError):
            bind_legacy_subscription(self.db, 1, 42, 999)
        self.assertIsNone(db_query(self.db, "SELECT user_id FROM subscriptions WHERE id=1", one=True)["user_id"])
        bind_legacy_subscription(self.db, 1, 42, 123)
        bind_legacy_subscription(self.db, 1, 42, 123)
        row = db_query(self.db, "SELECT * FROM subscriptions WHERE id=1", one=True)
        self.assertEqual((row["uuid"], row["user_id"], row["migration_status"]),
                         ("legacy-user-uuid", 42, "resolved"))
        with self.assertRaises(sqlite3.IntegrityError):
            connection = sqlite3.connect(self.db)
            try:
                connection.execute(
                    "INSERT INTO subscriptions (tg_id,user_id,migration_status,created_at) VALUES (123,42,'resolved',2)"
                )
            finally:
                connection.close()
        order = db_query(self.db, "SELECT * FROM orders WHERE order_id='old-order'", one=True)
        self.assertEqual((order["target_uuid"], order["target_user_id"], order["delivered_user_id"]),
                         ("legacy-user-uuid", 42, 42))
        whitelist = db_query(self.db, "SELECT * FROM anomaly_whitelist", one=True)
        self.assertEqual((whitelist["user_uuid"], whitelist["user_id"]), ("legacy-user-uuid", 42))
        event = db_query(self.db, "SELECT * FROM anomaly_events", one=True)
        self.assertEqual(event["user_id"], 42)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIn("idx_subscriptions_user_id_unique",
                      {row[1] for row in conn.execute("PRAGMA index_list(subscriptions)")})
        conn.close()

    def test_interrupted_upgrade_rolls_back_and_can_retry(self):
        real_add_column = storage_db._add_column
        calls = 0

        def interrupted_add_column(conn, table, column, definition):
            nonlocal calls
            calls += 1
            real_add_column(conn, table, column, definition)
            if calls == 2:
                raise RuntimeError("injected migration interruption")

        with patch.object(storage_db, "_add_column", new=interrupted_add_column):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                init_db(self.db)
        conn = sqlite3.connect(self.db)
        self.assertNotIn("user_id",
                         {row[1] for row in conn.execute("PRAGMA table_info(subscriptions)")})
        self.assertEqual(conn.execute("SELECT uuid FROM subscriptions").fetchone()[0],
                         "legacy-user-uuid")
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        conn.close()
        init_db(self.db)
        self.assertIsNone(db_query(self.db, "SELECT user_id FROM subscriptions", one=True)["user_id"])


class TestBotJobs(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.module_temp = tempfile.TemporaryDirectory()
        root = Path(cls.module_temp.name)
        config = root / "config.json"
        config.write_text(json.dumps({"admin_id": "123", "bot_token": "123:test-token"}), encoding="utf-8")
        with patch.dict(os.environ, {
            "REMNASHOP_CONFIG": str(config),
            "REMNASHOP_DB": str(root / "initial.db"),
        }):
            cls.bot_module = importlib.import_module("bot")

    @classmethod
    def tearDownClass(cls):
        cls.module_temp.cleanup()

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "test.db")
        init_db(self.db)
        self.db_patch = patch.object(self.bot_module, "DB_FILE", self.db)
        self.db_patch.start()
        self.context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_expiry_keeps_mapping_until_panel_confirms_deletion(self):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO subscriptions (tg_id,user_id,migration_status,created_at) VALUES (123,42,'resolved',1)"
        )
        conn.commit()
        conn.close()
        user = {"id": 42, "expireAt": "2020-01-01T00:00:00Z", "status": "DISABLED"}
        with patch.object(self.bot_module, "get_panel_user", new=AsyncMock(return_value=user)), \
             patch.object(self.bot_module, "delete_panel_user",
                          new=AsyncMock(return_value=SimpleNamespace(status_code=503))):
            await self.bot_module.check_expiry_job(self.context)
        self.assertIsNotNone(db_query(self.db, "SELECT id FROM subscriptions WHERE user_id=42", one=True))
        with patch.object(self.bot_module, "get_panel_user", new=AsyncMock(return_value=user)), \
             patch.object(self.bot_module, "delete_panel_user",
                          new=AsyncMock(return_value=SimpleNamespace(status_code=404))):
            await self.bot_module.check_expiry_job(self.context)
        self.assertIsNotNone(db_query(self.db, "SELECT id FROM subscriptions WHERE user_id=42", one=True))
        with patch.object(self.bot_module, "get_panel_user", new=AsyncMock(return_value=user)), \
             patch.object(self.bot_module, "delete_panel_user",
                          new=AsyncMock(side_effect=PanelApiError("timeout"))):
            await self.bot_module.check_expiry_job(self.context)
        self.assertIsNotNone(db_query(self.db, "SELECT id FROM subscriptions WHERE user_id=42", one=True))
        with patch.object(self.bot_module, "get_panel_user", new=AsyncMock(return_value=user)), \
             patch.object(self.bot_module, "delete_panel_user",
                          new=AsyncMock(return_value=SimpleNamespace(status_code=204))):
            await self.bot_module.check_expiry_job(self.context)
        self.assertIsNone(db_query(self.db, "SELECT id FROM subscriptions WHERE user_id=42", one=True))

    async def test_expiry_logs_local_delete_failure_without_claiming_cleanup(self):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO subscriptions (tg_id,user_id,migration_status,created_at)"
            " VALUES (123,42,'resolved',1)"
        )
        conn.commit()
        conn.close()
        user = {"id": 42, "expireAt": "2020-01-01T00:00:00Z", "status": "DISABLED"}
        real_execute = self.bot_module.db_execute

        def fail_local_delete(query, args=()):
            if query.startswith("DELETE FROM subscriptions"):
                raise sqlite3.OperationalError("injected local write failure")
            return real_execute(query, args)

        with patch.object(self.bot_module, "get_panel_user", new=AsyncMock(return_value=user)), \
             patch.object(self.bot_module, "delete_panel_user",
                          new=AsyncMock(return_value=SimpleNamespace(status_code=204))), \
             patch.object(self.bot_module, "db_execute", new=fail_local_delete), \
             self.assertLogs("bot", level="WARNING") as captured:
            await self.bot_module.check_expiry_job(self.context)
        self.assertTrue(any("injected local write failure" in line for line in captured.output))
        self.assertIsNotNone(db_query(self.db, "SELECT id FROM subscriptions WHERE user_id=42", one=True))
        self.context.bot.send_message.assert_not_awaited()

    async def test_absent_panel_user_keeps_local_mapping_for_review(self):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO subscriptions (tg_id,user_id,migration_status,created_at)"
            " VALUES (123,42,'resolved',1)"
        )
        conn.commit()
        conn.close()
        remove = AsyncMock()
        with patch.object(self.bot_module, "get_panel_user", new=AsyncMock(return_value=None)), \
             patch.object(self.bot_module, "delete_panel_user", new=remove), \
             self.assertLogs("bot", level="WARNING") as captured:
            await self.bot_module.check_expiry_job(self.context)
        remove.assert_not_awaited()
        self.assertTrue(any("retained for review" in line for line in captured.output))
        self.assertIsNotNone(db_query(self.db, "SELECT id FROM subscriptions", one=True))

    async def test_bulk_job_submits_and_retries_transient_failure(self):
        now = int(time.time())
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO bulk_jobs (action,payload_json,status,created_at,updated_at) VALUES (?,?,?,?,?)",
            ("disable", json.dumps({"userIds": [42], "extra": {}}), "pending", now, now),
        )
        conn.commit()
        conn.close()
        with patch.object(self.bot_module, "api_run_bulk_action",
                          new=AsyncMock(side_effect=PanelApiError("timeout"))):
            await self.bot_module.process_bulk_jobs_job(self.context)
        row = db_query(self.db, "SELECT * FROM bulk_jobs", one=True)
        self.assertEqual((row["status"], row["attempts"]), ("retry", 1))
        self.assertGreater(row["next_attempt_at"], now)
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE bulk_jobs SET next_attempt_at=0")
        conn.commit()
        conn.close()
        with patch.object(self.bot_module, "api_run_bulk_action",
                          new=AsyncMock(return_value=(1, 0))):
            await self.bot_module.process_bulk_jobs_job(self.context)
        row = db_query(self.db, "SELECT * FROM bulk_jobs", one=True)
        self.assertEqual((row["status"], row["attempts"]), ("submitted", 2))

    async def test_bulk_retry_stops_after_three_attempts(self):
        now = int(time.time())
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO bulk_jobs (action,payload_json,status,attempts,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?)",
            ("disable", json.dumps({"userIds": [42]}), "pending", 2, now, now),
        )
        conn.commit()
        conn.close()
        with patch.object(self.bot_module, "api_run_bulk_action",
                          new=AsyncMock(side_effect=PanelApiError("timeout"))):
            await self.bot_module.process_bulk_jobs_job(self.context)
        row = db_query(self.db, "SELECT status,attempts FROM bulk_jobs", one=True)
        self.assertEqual((row["status"], row["attempts"]), ("failed", 3))

    async def test_uncertain_reset_is_not_replayed(self):
        now = int(time.time())
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO bulk_jobs (action,payload_json,status,created_at,updated_at)"
            " VALUES (?,?,?,?,?)",
            ("reset", json.dumps({"userIds": [42]}), "pending", now, now),
        )
        conn.commit()
        conn.close()
        with patch.object(self.bot_module, "api_run_bulk_action",
                          new=AsyncMock(side_effect=PanelApiError("timeout"))):
            await self.bot_module.process_bulk_jobs_job(self.context)
        self.assertEqual(db_query(self.db, "SELECT status FROM bulk_jobs", one=True)["status"],
                         "unknown")
        init_db(self.db)
        self.assertEqual(db_query(self.db, "SELECT status FROM bulk_jobs", one=True)["status"],
                         "unknown")

    async def test_restart_requeues_safe_job_but_holds_uncertain_delete(self):
        now = int(time.time())
        conn = sqlite3.connect(self.db)
        conn.executemany(
            "INSERT INTO bulk_jobs (action,payload_json,status,created_at,updated_at)"
            " VALUES (?,?,?,?,?)",
            [
                ("disable", json.dumps({"userIds": [42]}), "running", now, now),
                ("delete", json.dumps({"userIds": [43]}), "running", now, now),
            ],
        )
        conn.commit()
        conn.close()
        init_db(self.db)
        rows = db_query(self.db, "SELECT action,status FROM bulk_jobs ORDER BY id")
        self.assertEqual([(row["action"], row["status"]) for row in rows],
                         [("disable", "pending"), ("delete", "unknown")])

    async def test_legacy_bulk_payload_never_calls_panel(self):
        now = int(time.time())
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO bulk_jobs (action,payload_json,status,created_at,updated_at) VALUES (?,?,?,?,?)",
            ("delete", json.dumps({"uuids": ["legacy-uuid"]}), "pending", now, now),
        )
        conn.commit()
        conn.close()
        request = AsyncMock()
        with patch.object(self.bot_module, "api_run_bulk_action", new=request):
            await self.bot_module.process_bulk_jobs_job(self.context)
        request.assert_not_awaited()
        self.assertEqual(db_query(self.db, "SELECT status FROM bulk_jobs", one=True)["status"],
                         "migration_required")

    async def test_unique_legacy_match_resolves_without_creating_user(self):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO subscriptions (tg_id,uuid,created_at,migration_status) VALUES (123,'old-uuid',1,'pending')"
        )
        conn.commit()
        conn.close()
        lookup = AsyncMock(return_value=[{"id": 42, "telegramId": 123}])
        with patch.object(self.bot_module, "PANEL_URL", "https://panel.example/api"), \
             patch.object(self.bot_module, "PANEL_TOKEN", "test-token"), \
             patch.object(self.bot_module, "api_get_users_by_telegram_id", new=lookup):
            await self.bot_module.reconcile_legacy_subscriptions_job(self.context)
        row = db_query(self.db, "SELECT * FROM subscriptions", one=True)
        self.assertEqual((row["uuid"], row["user_id"]), ("old-uuid", 42))
        self.assertEqual(len(db_query(self.db, "SELECT * FROM subscriptions")), 1)

    async def test_ambiguous_legacy_links_remain_unmapped(self):
        conn = sqlite3.connect(self.db)
        conn.executemany(
            "INSERT INTO subscriptions (tg_id,uuid,created_at,migration_status) VALUES (123,?,1,'pending')",
            [("old-a",), ("old-b",)],
        )
        conn.commit()
        conn.close()
        lookup = AsyncMock()
        with patch.object(self.bot_module, "PANEL_URL", "https://panel.example/api"), \
             patch.object(self.bot_module, "PANEL_TOKEN", "test-token"), \
             patch.object(self.bot_module, "api_get_users_by_telegram_id", new=lookup):
            await self.bot_module.reconcile_legacy_subscriptions_job(self.context)
        lookup.assert_not_awaited()
        rows = db_query(self.db, "SELECT user_id,migration_status FROM subscriptions")
        self.assertEqual([(row["user_id"], row["migration_status"]) for row in rows],
                         [(None, "ambiguous"), (None, "ambiguous")])

    async def test_ambiguous_remote_matches_require_admin_mapping(self):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO subscriptions (tg_id,uuid,created_at,migration_status) VALUES (123,'old-a',1,'pending')"
        )
        conn.commit()
        conn.close()
        lookup = AsyncMock(return_value=[
            {"id": 42, "telegramId": 123},
            {"id": 43, "telegramId": 123},
        ])
        with patch.object(self.bot_module, "PANEL_URL", "https://panel.example/api"), \
             patch.object(self.bot_module, "PANEL_TOKEN", "test-token"), \
             patch.object(self.bot_module, "api_get_users_by_telegram_id", new=lookup):
            await self.bot_module.reconcile_legacy_subscriptions_job(self.context)
        row = db_query(self.db, "SELECT user_id,migration_status FROM subscriptions", one=True)
        self.assertEqual((row["user_id"], row["migration_status"]), (None, "ambiguous"))

    async def test_missing_remote_match_preserves_legacy_uuid(self):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO subscriptions (tg_id,uuid,created_at,migration_status) VALUES (123,'old-a',1,'pending')"
        )
        conn.commit()
        conn.close()
        with patch.object(self.bot_module, "PANEL_URL", "https://panel.example/api"), \
             patch.object(self.bot_module, "PANEL_TOKEN", "test-token"), \
             patch.object(self.bot_module, "api_get_users_by_telegram_id",
                          new=AsyncMock(return_value=[])):
            await self.bot_module.reconcile_legacy_subscriptions_job(self.context)
        row = db_query(self.db, "SELECT uuid,user_id,migration_status FROM subscriptions", one=True)
        self.assertEqual((row["uuid"], row["user_id"], row["migration_status"]),
                         ("old-a", None, "pending"))

    async def test_admin_callbacks_reject_non_admin_before_mutation(self):
        query = SimpleNamespace(from_user=SimpleNamespace(id=999), answer=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})
        for handler in (
            self.bot_module.admin_menu_handler,
            self.bot_module.add_plan_start,
            self.bot_module.process_order,
        ):
            with self.subTest(handler=handler.__name__):
                await handler(update, context)
        self.assertEqual(query.answer.await_count, 3)

    async def test_purchase_menu_blocks_missing_panel_configuration(self):
        send = AsyncMock()
        with patch.object(self.bot_module, "PANEL_URL", ""), \
             patch.object(self.bot_module, "PANEL_TOKEN", ""), \
             patch.object(self.bot_module, "send_or_edit_menu", new=send):
            await self.bot_module.show_payment_method_menu(
                SimpleNamespace(), SimpleNamespace(), "p1", "new", "0"
            )
        self.assertIn("尚未配置", send.call_args.args[2])

    async def test_anomaly_job_uses_v3_user_id_history(self):
        self.bot_module.set_setting_value("anomaly_threshold", "0")
        record = {
            "userId": 42,
            "requestAt": "2026-09-23T00:00:00Z",
            "requestIp": "192.0.2.1",
            "userAgent": "test-client",
        }
        with patch.object(self.bot_module, "get_subscription_request_history",
                          new=AsyncMock(return_value=[record])), \
             patch.object(self.bot_module, "sync_user_metadata", new=AsyncMock()):
            await self.bot_module.check_anomalies_job(self.context)
        event = db_query(self.db, "SELECT * FROM anomaly_events", one=True)
        self.assertIsNotNone(event)
        self.assertEqual(event["user_id"], 42)
        self.assertEqual(event["user_uuid"], "")

    async def test_admin_can_bind_exact_legacy_record_by_numeric_id(self):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO subscriptions (tg_id,uuid,created_at,migration_status) VALUES (123,'old-uuid',1,'pending')"
        )
        conn.commit()
        conn.close()
        message = SimpleNamespace(text="bind:123:42:1", reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=self.bot_module.ADMIN_ID),
            message=message,
        )
        context = SimpleNamespace(user_data={"panel_user_lookup_mode": True})
        panel_user = {"id": 42, "telegramId": 123, "username": "alice"}
        with patch.object(self.bot_module, "get_panel_user",
                          new=AsyncMock(return_value=panel_user)):
            await self.bot_module.handle_message(update, context)
        row = db_query(self.db, "SELECT * FROM subscriptions", one=True)
        self.assertEqual((row["uuid"], row["user_id"]), ("old-uuid", 42))
