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
from telegram.error import BadRequest
from handlers.telegram_ui import edit_callback_message
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
                                     message=SimpleNamespace(text='order', photo=None, document=None),
                                     answer=AsyncMock(), edit_message_text=AsyncMock(),
                                     edit_message_caption=AsyncMock(), edit_message_reply_markup=AsyncMock(),
                                     delete_message=AsyncMock())
        self.update = SimpleNamespace(callback_query=self.query, effective_chat=SimpleNamespace(id=123))
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
        self.query.edit_message_text.assert_awaited()
        self.query.edit_message_caption.assert_not_awaited()

    async def test_new_photo_order_approve_only_creates_once(self):
        now = int(time.time())
        db_execute(self.db, "INSERT OR REPLACE INTO plans (key,name,days,gb,reset_strategy) VALUES (?,?,?,?,?)",
                   ('p1', 'Plan', 30, 10, 'NO_RESET'))
        db_execute(self.db, """INSERT INTO orders
                   (order_id,tg_id,plan_key,order_type,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)""", ('order1', 777, 'p1', 'new', 'pending', now, now))
        self.query.data = 'ap_order1_0'
        self.query.message = SimpleNamespace(text=None, photo=['proof'], document=None, caption='proof')
        create = AsyncMock(return_value=SimpleNamespace(status_code=201))
        with patch.object(self.bot, 'panel_config_ready', return_value=True), \
             patch.object(self.bot, 'TARGET_GROUP_UUID', 'group'), \
             patch.object(self.bot, 'get_user_by_username', new=AsyncMock(return_value=None)), \
             patch.object(self.bot, 'create_panel_user', new=create), \
             patch.object(self.bot, 'extract_payload', return_value={'id': 42, 'subscriptionUrl': 'https://example.test/sub'}), \
             patch.object(self.bot, 'sync_user_metadata', new=AsyncMock()), \
             patch.object(self.bot, 'generate_qr', return_value=b'qr'):
            await self.bot.process_order(self.update, self.context)
            await self.bot.process_order(self.update, self.context)
        self.assertEqual(self.order_status(), 'delivered')
        create.assert_awaited_once()
        self.query.edit_message_text.assert_not_awaited()
        self.query.edit_message_caption.assert_awaited()

    async def test_media_order_approve_and_duplicate_are_caption_aware(self):
        for kind in ('photo', 'document'):
            with self.subTest(kind=kind):
                self.seed_renewal()
                self.query.message = SimpleNamespace(text=None, photo=['proof'] if kind == 'photo' else None,
                                                     document='proof' if kind == 'document' else None,
                                                     caption='payment proof')
                before = {'id': 42, 'telegramId': 777, 'trafficLimitBytes': 100}
                extend = AsyncMock(return_value=before)
                with patch.object(self.bot, 'panel_config_ready', return_value=True), \
                     patch.object(self.bot, 'get_panel_user', new=AsyncMock(side_effect=[before, before])), \
                     patch.object(self.bot, 'extend_subscription', new=extend), \
                     patch.object(self.bot, 'patch_panel_user', new=AsyncMock(return_value=SimpleNamespace(status_code=200))), \
                     patch.object(self.bot, 'sync_user_metadata', new=AsyncMock()), \
                     patch.object(self.bot, 'send_subscription_card', new=AsyncMock()):
                    await self.bot.process_order(self.update, self.context)
                    await self.bot.process_order(self.update, self.context)
                    self.query.data = 'rj_order1'
                    await self.bot.process_order(self.update, self.context)
                self.assertEqual(self.order_status(), 'delivered')
                extend.assert_awaited_once()
                self.assertGreaterEqual(self.query.edit_message_caption.await_count, 2)
                self.query.edit_message_text.assert_not_awaited()
                db_execute(self.db, "DELETE FROM orders WHERE order_id='order1'")
                db_execute(self.db, "DELETE FROM subscriptions WHERE user_id=42")
                db_execute(self.db, "DELETE FROM plans WHERE key='p1'")
                self.query.data = 'ap_order1_42'
                self.query.edit_message_caption.reset_mock()

    async def test_media_reject_and_cross_click_preserve_rejected(self):
        self.seed_renewal()
        self.query.data = 'rj_order1'
        self.query.message = SimpleNamespace(text=None, photo=['proof'], document=None, caption=None)
        await self.bot.process_order(self.update, self.context)
        await self.bot.process_order(self.update, self.context)
        self.query.data = 'ap_order1_42'
        await self.bot.process_order(self.update, self.context)
        self.assertEqual(self.order_status(), 'rejected')
        self.query.edit_message_text.assert_not_awaited()
        self.assertEqual(self.query.edit_message_caption.await_count, 3)

    async def test_text_reject_and_cross_click_use_text_edit(self):
        self.seed_renewal()
        self.query.data = 'rj_order1'
        await self.bot.process_order(self.update, self.context)
        self.query.data = 'ap_order1_42'
        await self.bot.process_order(self.update, self.context)
        self.assertEqual(self.order_status(), 'rejected')
        self.assertEqual(self.query.edit_message_text.await_count, 2)
        self.query.edit_message_caption.assert_not_awaited()

    async def test_caption_limit_fallback_clears_keyboard(self):
        self.query.message = SimpleNamespace(text=None, photo=['proof'], document=None, caption=None)
        result = await edit_callback_message(self.query, self.context.bot, 123, 'x' * 1025)
        self.assertEqual(result, 'fallback')
        self.query.edit_message_caption.assert_not_awaited()
        self.query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
        self.context.bot.send_message.assert_awaited_once()

    async def test_caption_bad_request_fallback_and_stale_keyboard(self):
        self.query.message = SimpleNamespace(text=None, photo=['proof'], document=None, caption='proof')
        self.query.edit_message_caption.side_effect = BadRequest("Message can't be edited")
        self.query.edit_message_reply_markup.side_effect = BadRequest("Message can't be edited")
        self.query.delete_message.side_effect = BadRequest("Message can't be deleted")
        result = await edit_callback_message(self.query, self.context.bot, 123, 'result')
        self.assertEqual(result, 'fallback')
        self.assertIn('原卡片按钮无法移除', self.context.bot.send_message.await_args.kwargs['text'])

    async def test_unsafe_failed_order_cannot_retry_or_cancel(self):
        self.seed_renewal()
        db_execute(self.db, "UPDATE orders SET status='failed', error_message='reason:network|PanelApiError' WHERE order_id='order1'")
        self.query.data = 'rt_order1'
        await self.bot.process_order(self.update, self.context)
        self.query.data = 'order_cancel_yes_order1'
        await self.bot.process_order(self.update, self.context)
        self.assertEqual(self.order_status(), 'failed')

    async def test_safe_failed_order_cancel_requires_confirmation(self):
        self.seed_renewal()
        db_execute(self.db, "UPDATE orders SET status='failed', error_message='panel_configuration_incomplete' WHERE order_id='order1'")
        self.query.data = 'order_cancel_confirm_order1'
        await self.bot.process_order(self.update, self.context)
        self.assertEqual(self.order_status(), 'failed')
        self.query.data = 'order_cancel_yes_order1'
        await self.bot.process_order(self.update, self.context)
        self.assertEqual(self.order_status(), 'rejected')

    async def test_ui_edit_failure_does_not_interrupt_renewal(self):
        self.seed_renewal()
        self.query.message = SimpleNamespace(text=None, photo=['proof'], document=None, caption='proof')
        self.query.edit_message_caption.side_effect = BadRequest("Message can't be edited")
        before = {'id': 42, 'telegramId': 777, 'trafficLimitBytes': 100}
        extend = AsyncMock(return_value=before)
        with patch.object(self.bot, 'panel_config_ready', return_value=True), \
             patch.object(self.bot, 'get_panel_user', new=AsyncMock(side_effect=[before, before])), \
             patch.object(self.bot, 'extend_subscription', new=extend), \
             patch.object(self.bot, 'patch_panel_user', new=AsyncMock(return_value=SimpleNamespace(status_code=200))), \
             patch.object(self.bot, 'sync_user_metadata', new=AsyncMock()), \
             patch.object(self.bot, 'send_subscription_card', new=AsyncMock()):
            await self.bot.process_order(self.update, self.context)
            await self.bot.process_order(self.update, self.context)
        self.assertEqual(self.order_status(), 'delivered')
        extend.assert_awaited_once()
        self.query.edit_message_reply_markup.assert_awaited()
        self.context.bot.send_message.assert_awaited()

    async def test_new_order_create_timeout_is_unknown_and_not_retried(self):
        now = int(time.time())
        db_execute(self.db, "INSERT OR REPLACE INTO plans (key,name,days,gb,reset_strategy) VALUES (?,?,?,?,?)",
                   ('p1', 'Plan', 30, 10, 'NO_RESET'))
        db_execute(self.db, """INSERT INTO orders
                   (order_id,tg_id,plan_key,order_type,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)""", ('order1', 777, 'p1', 'new', 'pending', now, now))
        self.query.data = 'ap_order1_0'
        create = AsyncMock(side_effect=PanelApiError('timeout'))
        with patch.object(self.bot, 'panel_config_ready', return_value=True), \
             patch.object(self.bot, 'TARGET_GROUP_UUID', 'group'), \
             patch.object(self.bot, 'get_user_by_username', new=AsyncMock(return_value=None)), \
             patch.object(self.bot, 'create_panel_user', new=create):
            await self.bot.process_order(self.update, self.context)
            await self.bot.process_order(self.update, self.context)
        self.assertEqual(self.order_status(), 'unknown')
        create.assert_awaited_once()

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

    def test_restart_marks_new_approval_unknown(self):
        self.seed_renewal()
        db_execute(self.db, "UPDATE orders SET order_type='new', status='approved' WHERE order_id='order1'")
        init_db(self.db)
        self.assertEqual(self.order_status(), 'unknown')
