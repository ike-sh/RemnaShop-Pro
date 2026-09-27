import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.panel_api import PanelApiError
from storage.db import (create_action_request, db_execute, db_query, get_action_request,
                        init_db)


class TestV38Business(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.module_temp = tempfile.TemporaryDirectory()
        root = Path(cls.module_temp.name)
        config = root / 'config.json'
        config.write_text(json.dumps({'admin_id': '123', 'bot_token': '123:test-token'}), encoding='utf-8')
        with patch.dict(os.environ, {'REMNASHOP_CONFIG': str(config),
                                      'REMNASHOP_DB': str(root / 'module.db')}):
            cls.bot = importlib.import_module('bot')

    @classmethod
    def tearDownClass(cls):
        cls.module_temp.cleanup()

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / 'data.db')
        init_db(self.db)
        self.db_patch = patch.object(self.bot, 'DB_FILE', self.db)
        self.db_patch.start()
        self.query = SimpleNamespace(data='ap_order1_42', from_user=SimpleNamespace(id=123),
                                     answer=AsyncMock(), edit_message_text=AsyncMock())
        self.update = SimpleNamespace(callback_query=self.query)
        self.context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(),
                                                           send_photo=AsyncMock(), delete_message=AsyncMock()),
                                       user_data={})

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def seed_renewal(self):
        now = int(time.time())
        db_execute(self.db, "INSERT OR REPLACE INTO plans (key,name,days,gb,reset_strategy) VALUES (?,?,?,?,?)",
                   ('p1', 'Plan', 30, 10, 'NO_RESET'))
        db_execute(self.db, "INSERT INTO subscriptions (tg_id,user_id,migration_status,created_at) VALUES (?,?,?,?)",
                   (777, 42, 'resolved', now))
        db_execute(self.db, """INSERT INTO orders
                   (order_id,tg_id,plan_key,order_type,target_user_id,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                   ('order1', 777, 'p1', 'renew', 42, 'pending', now, now))

    def order_status(self):
        return db_query(self.db, "SELECT status FROM orders WHERE order_id='order1'", one=True)['status']

    async def test_renewal_extends_once_and_preserves_panel_status(self):
        self.seed_renewal()
        before = {'id': 42, 'status': 'DISABLED', 'trafficLimitBytes': 100,
                  'subscriptionUrl': 'https://old.example', 'expireAt': '2026-01-01T00:00:00Z'}
        extended = {**before, 'trafficLimitBytes': 100, 'expireAt': '2026-10-27T00:00:00Z'}
        refreshed = {**extended, 'trafficLimitBytes': 100 + 10 * 1024**3,
                     'subscriptionUrl': 'https://new.example'}
        extend = AsyncMock(return_value=extended)
        patch_user = AsyncMock(return_value=SimpleNamespace(status_code=200))
        card = AsyncMock()
        with patch.object(self.bot, 'panel_config_ready', return_value=True), \
             patch.object(self.bot, 'get_panel_user', new=AsyncMock(side_effect=[before, refreshed])), \
             patch.object(self.bot, 'extend_subscription', new=extend), \
             patch.object(self.bot, 'patch_panel_user', new=patch_user), \
             patch.object(self.bot, 'sync_user_metadata', new=AsyncMock()), \
             patch.object(self.bot, 'send_subscription_card', new=card):
            await self.bot.process_order(self.update, self.context)
            await self.bot.process_order(self.update, self.context)
        self.assertEqual(self.order_status(), 'delivered')
        extend.assert_awaited_once_with(42, 30)
        self.assertEqual(patch_user.await_args.kwargs, {'retry': False})
        self.assertEqual(patch_user.await_args.args[0], {
            'id': 42, 'trafficLimitBytes': refreshed['trafficLimitBytes'],
            'trafficLimitStrategy': 'NO_RESET'})
        card.assert_awaited_once_with(self.context, 777, refreshed, 42)

    async def test_renewal_timeout_and_patch_failure_are_unknown(self):
        for fail_at in ('extend', 'patch'):
            with self.subTest(fail_at=fail_at):
                self.seed_renewal()
                before = {'id': 42, 'trafficLimitBytes': 100}
                extend = AsyncMock(side_effect=PanelApiError('timeout')) if fail_at == 'extend' else AsyncMock(return_value=before)
                patch_user = AsyncMock(side_effect=PanelApiError('HTTP 503'))
                with patch.object(self.bot, 'panel_config_ready', return_value=True), \
                     patch.object(self.bot, 'get_panel_user', new=AsyncMock(return_value=before)), \
                     patch.object(self.bot, 'extend_subscription', new=extend), \
                     patch.object(self.bot, 'patch_panel_user', new=patch_user):
                    await self.bot.process_order(self.update, self.context)
                    await self.bot.process_order(self.update, self.context)
                self.assertEqual(self.order_status(), 'unknown')
                extend.assert_awaited_once()
                db_execute(self.db, "DELETE FROM orders WHERE order_id='order1'")
                db_execute(self.db, "DELETE FROM subscriptions WHERE user_id=42")
                db_execute(self.db, "DELETE FROM plans WHERE key='p1'")

    async def test_action_callback_rechecks_actor_and_binding(self):
        token = create_action_request(self.db, 777, 42, 'user_revoke')
        self.query.data = 'v38u_' + token
        revoke = AsyncMock()
        with patch.object(self.bot, 'revoke_subscription', new=revoke):
            self.query.from_user.id = 778
            await self.bot.execute_confirmed_action(self.update, self.context)
            self.assertEqual(get_action_request(self.db, token)['status'], 'pending')
            self.query.from_user.id = 777
            with patch.object(self.bot, 'checked_owned_panel_user', new=AsyncMock(return_value=None)):
                await self.bot.execute_confirmed_action(self.update, self.context)
        revoke.assert_not_awaited()
        self.assertEqual(get_action_request(self.db, token)['status'], 'pending')

    async def test_confirmed_revoke_runs_once_and_refreshes_card(self):
        token = create_action_request(self.db, 777, 42, 'user_revoke')
        self.query.data = 'v38u_' + token
        self.query.from_user.id = 777
        user = {'id': 42, 'subscriptionUrl': 'https://new.example'}
        revoke = AsyncMock(return_value=user)
        card = AsyncMock()
        with patch.object(self.bot, 'checked_owned_panel_user', new=AsyncMock(return_value=user)), \
             patch.object(self.bot, 'get_panel_user', new=AsyncMock(return_value=user)), \
             patch.object(self.bot, 'revoke_subscription', new=revoke), \
             patch.object(self.bot, 'send_subscription_card', new=card):
            await self.bot.execute_confirmed_action(self.update, self.context)
            await self.bot.execute_confirmed_action(self.update, self.context)
        revoke.assert_awaited_once_with(42)
        card.assert_awaited_once_with(self.context, 777, user, 42)
        self.assertEqual(get_action_request(self.db, token)['status'], 'done')

    async def test_admin_revoke_notifies_bound_owner_with_new_url(self):
        db_execute(self.db, "INSERT INTO subscriptions (tg_id,user_id,migration_status,created_at) VALUES (?,?,?,?)",
                   (777, 42, 'resolved', 1))
        token = create_action_request(self.db, 123, 42, 'admin_revoke')
        self.query.data = 'v38a_' + token
        user = {'id': 42, 'telegramId': 777, 'subscriptionUrl': 'https://new.example'}
        card = AsyncMock()
        with patch.object(self.bot, 'revoke_subscription', new=AsyncMock(return_value=user)), \
             patch.object(self.bot, 'get_panel_user', new=AsyncMock(return_value=user)), \
             patch.object(self.bot, 'send_subscription_card', new=card):
            await self.bot.execute_confirmed_action(self.update, self.context, admin=True)
        card.assert_awaited_once_with(self.context, 777, user, 42)

    async def test_subscription_card_uses_new_url_and_short_caption(self):
        db_execute(self.db, "INSERT INTO subscriptions (tg_id,user_id,migration_status,created_at) VALUES (?,?,?,?)",
                   (777, 42, 'resolved', 1))
        url = 'https://new.example/' + 'a' * 1800
        with patch.object(self.bot, 'generate_qr', return_value=b'qr') as qr:
            await self.bot.send_subscription_card(self.context, 777,
                                                  {'subscriptionUrl': url, 'status': 'ACTIVE'}, 42)
        qr.assert_called_once_with(url)
        caption = self.context.bot.send_photo.await_args.kwargs['caption']
        self.assertLessEqual(len(caption.encode('utf-16-le')) // 2, 1024)
        self.assertIn(url, self.context.bot.send_message.await_args.args[1])

    async def test_geocheck_polling_is_bounded(self):
        pending = {'isCompleted': False, 'isFailed': False, 'result': None}
        get_result = AsyncMock(return_value=pending)
        with patch.object(self.bot.v38_api, 'get_node_geocheck_result', new=get_result), \
             patch.object(self.bot.asyncio, 'sleep', new=AsyncMock()):
            self.assertIsNone(await self.bot.poll_node_geocheck('job-1'))
        self.assertEqual(get_result.await_count, 6)

    def test_restart_marks_inflight_writes_unknown(self):
        self.seed_renewal()
        db_execute(self.db, "UPDATE orders SET status='extension_applied' WHERE order_id='order1'")
        init_db(self.db)
        self.assertEqual(self.order_status(), 'unknown')
