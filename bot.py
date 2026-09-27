import logging
import time
import datetime
import json
import os
import asyncio
import qrcode
from io import BytesIO
from collections import defaultdict
from services.panel_api import get_panel_user as api_get_panel_user, get_user_by_telegram_id as api_get_user_by_telegram_id, get_user_by_username as api_get_user_by_username, get_user_by_short_uuid as api_get_user_by_short_uuid, get_nodes_status as api_get_nodes_status, get_subscription_history_stats as api_get_subscription_history_stats, get_user_subscription_history as api_get_user_subscription_history, get_subscription_settings as api_get_subscription_settings, patch_subscription_settings as api_patch_subscription_settings, get_internal_squads as api_get_internal_squads, get_internal_squad_accessible_nodes as api_get_internal_squad_accessible_nodes, get_bandwidth_nodes_usage as api_get_bandwidth_nodes_usage, bulk_move_users_to_squad as api_bulk_move_users_to_squad, create_user as api_create_user, patch_user as api_patch_user, delete_user as api_delete_user, enable_user as api_enable_user, disable_user as api_disable_user, reset_user_traffic as api_reset_user_traffic, get_subscription_request_history as api_get_subscription_request_history, bulk_delete_users as api_bulk_delete_users, bulk_update_users as api_bulk_update_users, get_contract_capabilities as api_get_contract_capabilities, set_user_metadata as api_set_user_metadata, block_ip_address as api_block_ip_address, get_system_health as api_get_system_health, get_system_stats as api_get_system_stats, get_system_stats_recap as api_get_system_stats_recap, get_snippet_by_key as api_get_snippet_by_key, get_subscription_page_configs as api_get_subscription_page_configs, get_external_squads as api_get_external_squads, get_config_profiles as api_get_config_profiles, get_user_accessible_nodes as api_get_user_accessible_nodes, close_all_clients, extract_payload
from services.panel_api import PanelApiError, PanelContractError, AmbiguousPanelUserError, api_base_url
from services.panel_api import get_users_by_telegram_id as api_get_users_by_telegram_id
from services.panel_api import subscription_settings_patch_from_current
from services.panel_api import run_bulk_action as api_run_bulk_action
from services import panel_api as v38_api
from services.orders import (
    create_order,
    get_order,
    update_order_status,
    attach_payment_text,
    attach_admin_message,
    append_order_audit_log,
    classify_order_failure,
    get_pending_order_for_user,
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_UNKNOWN,
    STATUS_EXTENSION_APPLIED,
)
from storage.db import init_db as storage_init_db, db_query as storage_db_query, db_execute as storage_db_execute, bind_legacy_subscription, create_action_request, get_action_request, claim_action_request, finish_action_request
from utils.formatting import escape_markdown_v2
from handlers.bulk_actions import parse_user_ids, parse_user_ids_strict, parse_extend_days_and_user_ids, parse_expire_days_and_user_ids, parse_traffic_and_user_ids
from handlers.admin import format_order_detail, format_order_row, order_status_label
from handlers.client import build_nodes_status_message
from handlers.v38_views import device_summary, dashboard_summary, node_metrics_summary, http_stats_summary, geocheck_summary, top_hwid_users_summary, fit_message
from jobs.anomaly import build_anomaly_incidents
from jobs.expiry import should_send_expire_notice
from utils.constants import APP_VERSION, USER_STATUS_ACTIVE, USER_STATUS_LIMITED, USER_STATUS_DISABLED
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.getenv("REMNASHOP_CONFIG", os.path.join(BASE_DIR, 'config.json'))
DB_FILE = os.getenv("REMNASHOP_DB", os.path.join(BASE_DIR, 'starlight.db'))

ANOMALY_IP_THRESHOLD = 50


def parse_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"配置文件缺失: {CONFIG_FILE}")
        exit(1)
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

config = load_config()

ADMIN_ID = int(config['admin_id'])
BOT_TOKEN = config['bot_token']
PANEL_URL = api_base_url(config.get('panel_url'))
PANEL_TOKEN = config.get('panel_token', '')
SUB_DOMAIN = (config.get('sub_domain') or '').rstrip('/')
TARGET_GROUP_UUID = config.get('group_uuid', '')
PANEL_VERIFY_TLS = parse_bool(config.get('panel_verify_tls', True), default=True)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

user_cooldowns = {}
COOLDOWN_SECONDS = 1.0
user_id_map = {}
order_payment_method_cache = {}
panel_capabilities_cache = {}
panel_capabilities_runtime_success = {}
dynamic_snippets_cache = {}
SUPPORT_REPLY_TTL_SECONDS = 1800


def _get_support_session_store(application):
    store = application.bot_data.get('support_reply_sessions')
    if not isinstance(store, dict):
        store = {}
        application.bot_data['support_reply_sessions'] = store
    return store


def set_support_reply_session(context: ContextTypes.DEFAULT_TYPE, user_id: int, source: str, admin_id: int | None = None):
    store = _get_support_session_store(context.application)
    now_ts = int(time.time())
    current = store.get(int(user_id)) if isinstance(store.get(int(user_id)), dict) else {}
    store[int(user_id)] = {
        'active': True,
        'source': source,
        'admin_id': int(admin_id) if admin_id else None,
        'updated_at': now_ts,
        'expire_at': now_ts + SUPPORT_REPLY_TTL_SECONDS,
        'control_message_id': current.get('control_message_id'),
    }


def get_support_reply_session(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    store = _get_support_session_store(context.application)
    sess = store.get(int(user_id))
    if not isinstance(sess, dict):
        return None
    now_ts = int(time.time())
    expire_at = int(sess.get('expire_at') or 0)
    if expire_at and expire_at < now_ts:
        store.pop(int(user_id), None)
        logger.info("support reply context expired: user=%s expire_at=%s", user_id, expire_at)
        return None
    return sess


def clear_support_reply_session(context: ContextTypes.DEFAULT_TYPE, user_id: int, reason: str):
    store = _get_support_session_store(context.application)
    existed = store.pop(int(user_id), None)
    if existed:
        logger.info("support reply context cleared: user=%s reason=%s", user_id, reason)


async def delete_message_if_possible(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id):
    if not message_id:
        return False
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=int(message_id))
        return True
    except Exception as exc:
        logger.debug("delete message skipped: chat=%s msg=%s err=%s", chat_id, message_id, exc)
        return False


async def upsert_support_control_message(context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str, reply_markup):
    store = _get_support_session_store(context.application)
    sess = store.get(int(user_id)) if isinstance(store.get(int(user_id)), dict) else {}
    msg_id = sess.get('control_message_id')
    if msg_id:
        try:
            await context.bot.edit_message_text(chat_id=user_id, message_id=int(msg_id), text=text, parse_mode='Markdown', reply_markup=reply_markup)
            return int(msg_id)
        except Exception as exc:
            logger.debug("edit support control message failed, fallback send new: user=%s msg=%s err=%s", user_id, msg_id, exc)
    sent = await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown', reply_markup=reply_markup)
    sess = sess if isinstance(sess, dict) else {}
    sess['control_message_id'] = sent.message_id
    sess['updated_at'] = int(time.time())
    store[int(user_id)] = sess
    return sent.message_id


async def cleanup_admin_reply_prompt(context: ContextTypes.DEFAULT_TYPE, admin_id: int, admin_state: dict, reason: str):
    prompt_id = admin_state.pop('reply_prompt_message_id', None)
    if prompt_id:
        ok = await delete_message_if_possible(context, admin_id, prompt_id)
        logger.info("cleanup admin reply prompt: admin=%s prompt=%s reason=%s deleted=%s", admin_id, prompt_id, reason, ok)

def get_short_id(panel_user_id):
    for sid, uid in user_id_map.items():
        if uid == panel_user_id: return sid
    short_id = str(len(user_id_map) + 1)
    user_id_map[short_id] = panel_user_id
    return short_id

def get_panel_user_id_from_short(short_id):
    return user_id_map.get(short_id)

def check_cooldown(user_id):
    if user_id == ADMIN_ID: return True
    now = time.time()
    last_time = user_cooldowns.get(user_id, 0)
    if now - last_time < COOLDOWN_SECONDS: return False
    user_cooldowns[user_id] = now
    return True

def get_strategy_label(strategy):
    mapping = {'NO_RESET': '总流量', 'DAY': '每日重置', 'WEEK': '每周重置', 'MONTH': '每月重置', 'MONTH_ROLLING': '按开通日每月重置'}
    return mapping.get(strategy, '总流量')

def draw_progress_bar(used, total, length=10):
    if total == 0: return "♾️ 无限制"
    percent = used / total
    if percent > 1: percent = 1
    filled_length = int(length * percent)
    bar = "█" * filled_length + "░" * (length - filled_length)
    return f"{bar} {round(percent * 100)}%"

def format_time(iso_str):
    if not iso_str: return "未知"
    try:
        clean_str = iso_str.split('.')[0].replace('Z', '')
        dt = datetime.datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception as exc:
        logger.debug("failed to parse time %s: %s", iso_str, exc)
        return iso_str

def generate_qr(text):
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio)
    bio.seek(0)
    return bio

def init_db():
    storage_init_db(DB_FILE)


def db_query(query, args=(), one=False):
    return storage_db_query(DB_FILE, query, args=args, one=one)


def db_execute(query, args=()):
    return storage_db_execute(DB_FILE, query, args=args)


def ensure_local_subscription_sync(tg_id, panel_user):
    if not isinstance(panel_user, dict):
        return None
    panel_user_id = panel_user.get('id')
    if not isinstance(panel_user_id, int) or panel_user_id <= 0 or panel_user.get('telegramId') != int(tg_id):
        return None
    exists = db_query("SELECT id FROM subscriptions WHERE user_id = ?", (panel_user_id,), one=True)
    if exists:
        return panel_user_id
    pending = db_query(
        "SELECT id FROM subscriptions WHERE tg_id=? AND user_id IS NULL ORDER BY id", (int(tg_id),)
    )
    if len(pending) == 1:
        bind_legacy_subscription(DB_FILE, pending[0]['id'], panel_user_id, int(tg_id))
        return panel_user_id
    if pending:
        return None
    now_ts = int(time.time())
    db_execute(
        "INSERT INTO subscriptions (tg_id, user_id, migration_status, created_at) VALUES (?, ?, 'resolved', ?)",
        (int(tg_id), panel_user_id, now_ts),
    )
    return panel_user_id


def get_setting_value(key, default=None):
    row = db_query("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return row['value'] if row else default


def set_setting_value(key, value):
    db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

def get_setting_bool(key, default=True):
    raw = str(get_setting_value(key, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on", "开启", "开"}


def mark_panel_capability_success(name: str):
    panel_capabilities_runtime_success[str(name)] = True


def capability_enabled(name: str, default=False):
    key = str(name)
    if panel_capabilities_runtime_success.get(key):
        return True
    explicit = get_setting_value(f"panel_capability_{key}", "")
    if explicit != "":
        return str(explicit).strip().lower() in {"1", "true", "yes", "on", "开启", "开"}
    return bool(panel_capabilities_cache.get(key, default))

def get_plan_price(plan_dict, payment_method='manual_review'):
    if payment_method == 'usdt':
        usdt_price = (plan_dict.get('usdt_price') or '').strip()
        if usdt_price:
            return f"{usdt_price} USDT"
    return str(plan_dict.get('price') or '')

def resolve_payment_state(payment_method):
    usdt_enabled = get_setting_bool("usdt_enabled", False)
    if payment_method == "manual_review":
        return {'available': True, 'method_label': "人工审核", 'should_send_qr': False, 'qr_file_id': None, 'pay_tip': "提交凭证后由管理员人工审核。"}

    if payment_method == "usdt":
        method_label = "USDT"
        custom_tip = dynamic_snippets_cache.get("payment_usdt_tip", "")
        network = (get_setting_value('usdt_network', 'TRC20') or 'TRC20').strip().upper()
        address = (get_setting_value('usdt_address', '') or '').strip()
        qr_file_id = get_setting_value('usdt_qr_file_id')
        if usdt_enabled and address:
            tip = custom_tip or f"请使用 **{network}** 网络向以下地址转账，完成后发送 **TXID/截图** 给机器人。\n`{address}`"
            return {'available': True, 'method_label': method_label, 'should_send_qr': bool(qr_file_id), 'qr_file_id': qr_file_id, 'pay_tip': tip}
        return {'available': False, 'method_label': method_label, 'should_send_qr': False, 'qr_file_id': qr_file_id, 'pay_tip': "USDT 收款未配置完成，请等待管理员配置。"}

    return {'available': False, 'method_label': payment_method, 'should_send_qr': False, 'qr_file_id': None, 'pay_tip': "不支持的支付方式。"}
def is_any_payment_available():
    manual_review = resolve_payment_state('manual_review')['available']
    usdt = resolve_payment_state('usdt')['available']
    return manual_review or usdt


def get_json_setting(key, default):
    raw = get_setting_value(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def set_json_setting(key, value):
    set_setting_value(key, json.dumps(value, ensure_ascii=False))


def append_ops_timeline(event_type, title, detail, actor='系统', target='-'):
    rows = get_json_setting('ops_timeline', [])
    if not isinstance(rows, list):
        rows = []
    rows.append({
        'ts': int(time.time()),
        'type': event_type,
        'title': title,
        'detail': detail[:240],
        'actor': str(actor),
        'target': str(target),
    })
    set_json_setting('ops_timeline', rows[-120:])


def push_subscription_settings_snapshot(payload, source='手动变更前快照'):
    hist = get_json_setting('subscription_settings_history', [])
    if not isinstance(hist, list):
        hist = []
    hist.append({
        'ts': int(time.time()),
        'source': source,
        'payload': subscription_settings_patch_from_current(payload),
    })
    set_json_setting('subscription_settings_history', hist[-10:])


def pop_subscription_settings_snapshot():
    hist = get_json_setting('subscription_settings_history', [])
    if not isinstance(hist, list) or not hist:
        return None
    item = hist.pop()
    set_json_setting('subscription_settings_history', hist)
    return item


def get_risk_watchlist():
    items = get_json_setting('risk_watchlist', [])
    if not isinstance(items, list):
        return set()
    return {str(x) for x in items if x}


def set_risk_watchlist(items):
    set_json_setting('risk_watchlist', sorted({str(x) for x in items if x}))


def enqueue_bulk_job(action, user_ids, extra, created_by):
    now = int(time.time())
    for batch in (user_ids[i:i + 500] for i in range(0, len(user_ids), 500)):
        payload = {'userIds': batch, 'extra': extra or {}}
        db_execute(
            "INSERT INTO bulk_jobs (action, payload_json, status, created_by, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?)",
            (action, json.dumps(payload, ensure_ascii=False), int(created_by or 0), now, now),
        )


def save_ops_template(name, payload, created_by):
    now = int(time.time())
    db_execute(
        "INSERT INTO ops_templates (name, payload_json, created_by, created_at) VALUES (?, ?, ?, ?)",
        (str(name)[:60], json.dumps(payload, ensure_ascii=False), int(created_by or 0), now),
    )


def get_builtin_templates():
    return {
        'tpl_strict': {'name': '严格风控模板', 'settings': {'risk_enforce_mode': 'enforce', 'risk_low_score': '70', 'risk_high_score': '120', 'anomaly_interval': '0.5'}},
        'tpl_stable': {'name': '稳定运营模板', 'settings': {'risk_enforce_mode': 'gray', 'risk_low_score': '80', 'risk_high_score': '130', 'anomaly_interval': '1'}},
        'tpl_growth': {'name': '增长推广模板', 'settings': {'risk_enforce_mode': 'observe', 'risk_low_score': '90', 'risk_high_score': '160', 'anomaly_interval': '1'}},
    }


def apply_template_payload(payload, actor='系统'):
    settings = payload.get('settings', {}) if isinstance(payload, dict) else {}
    for k, v in settings.items():
        set_setting_value(k, v)
    append_ops_timeline('模板', '应用运营模板', json.dumps(settings, ensure_ascii=False)[:180], actor=actor)


async def sync_user_metadata(user_id, tg_id, plan_key="", order_id="", risk_level=""):
    if not user_id:
        return
    payload = {
        "tg_id": str(tg_id),
        "plan_key": str(plan_key or ""),
        "last_order_id": str(order_id or ""),
        "risk_level": str(risk_level or ""),
        "updated_at": int(time.time()),
    }
    try:
        resp = await set_panel_user_metadata(user_id, payload)
        if resp.status_code != 200:
            logger.warning("sync_user_metadata panel rejected for %s: status=%s", user_id, resp.status_code)
    except Exception as exc:
        logger.warning("sync_user_metadata failed for %s: %s", user_id, exc)


def panel_config_ready():
    return bool(PANEL_URL and PANEL_TOKEN)


def save_runtime_config(**kwargs):
    global PANEL_URL, PANEL_TOKEN, SUB_DOMAIN, TARGET_GROUP_UUID, PANEL_VERIFY_TLS, config
    for k, v in kwargs.items():
        config[k] = v
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=4)
    if 'panel_url' in kwargs:
        PANEL_URL = api_base_url(kwargs.get('panel_url'))
    if 'panel_token' in kwargs:
        PANEL_TOKEN = kwargs.get('panel_token', '')
    if 'sub_domain' in kwargs:
        SUB_DOMAIN = kwargs.get('sub_domain', '').rstrip('/')
    if 'group_uuid' in kwargs:
        TARGET_GROUP_UUID = kwargs.get('group_uuid', '')
    if 'panel_verify_tls' in kwargs:
        PANEL_VERIFY_TLS = parse_bool(kwargs.get('panel_verify_tls'), default=True)


init_db()


def get_headers():
    return {"Authorization": f"Bearer {PANEL_TOKEN}", "Content-Type": "application/json"}


async def get_panel_user(user_id):
    return await api_get_panel_user(user_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_user_by_telegram_id(telegram_id):
    return await api_get_user_by_telegram_id(telegram_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_user_by_username(username):
    return await api_get_user_by_username(username, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_user_by_short_uuid(short_uuid):
    return await api_get_user_by_short_uuid(short_uuid, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_nodes_status():
    return await api_get_nodes_status(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)

async def get_subscription_history_stats():
    return await api_get_subscription_history_stats(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_user_subscription_history(user_id):
    return await api_get_user_subscription_history(user_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_subscription_settings():
    return await api_get_subscription_settings(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def patch_subscription_settings(payload):
    return await api_patch_subscription_settings(PANEL_URL, get_headers(), payload, PANEL_VERIFY_TLS)


async def get_internal_squads():
    return await api_get_internal_squads(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_internal_squad_accessible_nodes(uuid):
    return await api_get_internal_squad_accessible_nodes(uuid, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)

async def get_internal_squad_accessible_nodes_verbose(uuid):
    if not PANEL_URL or not PANEL_TOKEN:
        return [], 'config_missing'
    try:
        return await get_internal_squad_accessible_nodes(uuid), None
    except PanelApiError as exc:
        return [], str(exc)


async def get_bandwidth_nodes_usage():
    return await api_get_bandwidth_nodes_usage(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def bulk_move_users_to_squad(user_ids, squad_uuid):
    return await api_bulk_move_users_to_squad(user_ids, squad_uuid, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def create_panel_user(payload):
    return await api_create_user(payload, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def patch_panel_user(payload, *, retry=True):
    return await api_patch_user(payload, PANEL_URL, get_headers(), PANEL_VERIFY_TLS, retry=retry)


async def delete_panel_user(user_id):
    return await api_delete_user(user_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def enable_panel_user(user_id):
    return await api_enable_user(user_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def disable_panel_user(user_id):
    return await api_disable_user(user_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def reset_panel_user_traffic(user_id):
    return await api_reset_user_traffic(user_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_subscription_request_history():
    return await api_get_subscription_request_history(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def bulk_delete_panel_users(user_ids):
    return await api_bulk_delete_users(user_ids, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def bulk_update_panel_users(user_ids, fields):
    return await api_bulk_update_users(user_ids, fields, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def set_panel_user_metadata(user_id, metadata):
    resp = await api_set_user_metadata(user_id, metadata, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
    if resp.status_code == 200:
        mark_panel_capability_success("metadata")
    return resp


async def block_panel_ip(ip, reason):
    resp = await api_block_ip_address(ip, reason, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
    if resp.status_code == 202:
        mark_panel_capability_success("connections_drop")
    return resp


async def get_panel_system_health():
    return await api_get_system_health(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_panel_system_stats():
    return await api_get_system_stats(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_panel_system_stats_recap():
    return await api_get_system_stats_recap(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_panel_snippet(key):
    return await api_get_snippet_by_key(key, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_panel_subscription_page_configs():
    return await api_get_subscription_page_configs(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_panel_external_squads():
    return await api_get_external_squads(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_panel_config_profiles():
    return await api_get_config_profiles(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def get_user_accessible_nodes(user_id):
    return await api_get_user_accessible_nodes(user_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


def owned_subscription(tg_id, panel_user_id):
    if isinstance(panel_user_id, bool) or not isinstance(panel_user_id, int) or panel_user_id <= 0:
        return None
    return db_query("SELECT * FROM subscriptions WHERE tg_id=? AND user_id=?",
                    (int(tg_id), panel_user_id), one=True)


async def checked_owned_panel_user(tg_id, panel_user_id):
    if not owned_subscription(tg_id, panel_user_id):
        return None
    user = await get_panel_user(panel_user_id)
    if user and user.get('telegramId') not in (None, int(tg_id)):
        return None
    return user


async def get_user_devices(panel_user_id):
    return await v38_api.get_user_hwid_devices(panel_user_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def delete_user_device(panel_user_id, hwid):
    return await v38_api.delete_user_hwid_device(panel_user_id, hwid, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def delete_all_user_devices(panel_user_id):
    return await v38_api.delete_all_user_hwid_devices(panel_user_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def revoke_subscription(panel_user_id):
    return await v38_api.revoke_user_subscription(panel_user_id, PANEL_URL, get_headers(), False, PANEL_VERIFY_TLS)


async def extend_subscription(panel_user_id, days):
    return await v38_api.extend_user_expiration(panel_user_id, days, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)


async def poll_node_geocheck(job_id):
    for attempt in range(6):
        if attempt:
            await asyncio.sleep(5)
        result = await v38_api.get_node_geocheck_result(job_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
        if result['isCompleted'] or result['isFailed']:
            return result
    return None


async def refresh_panel_capabilities():
    global panel_capabilities_cache
    panel_capabilities_cache = await api_get_contract_capabilities(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
    logger.info(
        "Panel capabilities static=%s runtime_success=%s",
        panel_capabilities_cache,
        panel_capabilities_runtime_success,
    )
    return panel_capabilities_cache


async def refresh_dynamic_snippets():
    global dynamic_snippets_cache
    keys = ["payment_usdt_tip", "support_contact_tip"]
    rows = {}
    for key in keys:
        payload = await get_panel_snippet(key)
        if isinstance(payload, dict):
            value = payload.get('snippet')
            if isinstance(value, str) and value.strip():
                rows[key] = value.strip()
    dynamic_snippets_cache = rows
    if rows:
        logger.info("Loaded dynamic snippets from panel: %s", ",".join(sorted(rows.keys())))
    return rows


async def warmup_panel_runtime_data():
    if not panel_config_ready():
        return
    await refresh_panel_capabilities()
    try:
        health = await get_panel_system_health()
        logger.info("Panel health loaded: keys=%s", ",".join(sorted(list(health.keys()))[:12]))
        await refresh_dynamic_snippets()
        page_cfg = await get_panel_subscription_page_configs()
        logger.info("Panel subscription page configs=%s", len(page_cfg['configs']))
        squads = await get_panel_external_squads()
        profiles = await get_panel_config_profiles()
        logger.info("Panel inventory external_squads=%s config_profiles=%s", len(squads), len(profiles))
    except Exception as exc:
        logger.warning("Panel inventory warmup failed: %s", exc)


async def warmup_panel_runtime_job(context: ContextTypes.DEFAULT_TYPE):
    await warmup_panel_runtime_data()


def schedule_panel_warmup(context: ContextTypes.DEFAULT_TYPE):
    if not panel_config_ready():
        return
    for job in context.application.job_queue.get_jobs_by_name('warmup_panel_runtime_job'):
        job.schedule_removal()
    context.application.job_queue.run_once(
        warmup_panel_runtime_job, when=1, name='warmup_panel_runtime_job'
    )


async def apply_user_status_bulk(user_ids, status):
    if not user_ids:
        return
    target = sorted(set(int(value) for value in user_ids))
    for start in range(0, len(target), 500):
        resp = await bulk_update_panel_users(target[start:start + 500], {"status": status})
        if resp.status_code != 202:
            raise PanelApiError(f"Bulk status update failed: HTTP {resp.status_code}")


async def build_squad_capacity_summary(max_users=60):
    rows = db_query("SELECT DISTINCT user_id FROM subscriptions WHERE user_id IS NOT NULL ORDER BY id DESC LIMIT ?", (max_users,))
    user_ids = [dict(r)['user_id'] for r in rows]
    if not user_ids:
        return "暂无订阅样本", None
    infos = await asyncio.gather(*[get_panel_user(user_id) for user_id in user_ids])
    counts = defaultdict(int)
    for info in infos:
        if not isinstance(info, dict):
            continue
        squads = info['activeInternalSquads']
        squad = squads[0]['uuid'] if squads else None
        counts[squad or '未分组'] += 1
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    lines = [f"样本用户数: {len(user_ids)}"]
    for sid, cnt in top[:5]:
        lines.append(f"- `{sid}`：{cnt}")
    suggestion = None
    if len(top) >= 2 and top[0][1] - top[-1][1] >= max(5, len(user_ids) // 5):
        suggestion = {'from': top[0][0], 'to': top[-1][0], 'count': min(10, (top[0][1]-top[-1][1])//2)}
        lines.append(f"\n建议迁移：从 `{suggestion['from']}` 向 `{suggestion['to']}` 迁移约 {suggestion['count']} 人")
    return "\n".join(lines), suggestion


async def build_top_users_traffic(max_users=50):
    rows = db_query("SELECT tg_id, user_id FROM subscriptions WHERE user_id IS NOT NULL ORDER BY id DESC LIMIT ?", (max_users,))
    if not rows:
        return []
    pairs = [(dict(r)['tg_id'], dict(r)['user_id']) for r in rows]
    infos = await asyncio.gather(*[get_panel_user(u) for _, u in pairs])
    data = []
    for (tg_id, uid), info in zip(pairs, infos):
        if not isinstance(info, dict):
            continue
        used = int((info.get('userTraffic') or {}).get('usedTrafficBytes', 0) or 0)
        data.append((tg_id, uid, used))
    return sorted(data, key=lambda x: x[2], reverse=True)[:5]


async def send_or_edit_menu(update, context, text, reply_markup, parse_mode='Markdown'):
    async def _safe_send(chat_id, body, markup, mode):
        try:
            await context.bot.send_message(chat_id=chat_id, text=body, reply_markup=markup, parse_mode=mode)
        except Exception as exc:
            if mode is not None:
                logger.warning("send_message failed with parse_mode=%s, fallback plain text: %s", mode, exc)
                await context.bot.send_message(chat_id=chat_id, text=body, reply_markup=markup)
            else:
                raise

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as exc:
            if parse_mode is not None:
                logger.warning("edit_message_text failed with parse_mode=%s, fallback plain text: %s", parse_mode, exc)
                try:
                    await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup)
                    return
                except Exception:
                    pass
            try: await update.callback_query.delete_message()
            except Exception as exc:
                logger.debug("delete callback message failed: %s", exc)
            await _safe_send(update.effective_chat.id, text, reply_markup, parse_mode)
    else:
        await _safe_send(update.effective_chat.id, text, reply_markup, parse_mode)


async def send_subscription_card(context, tg_id, panel_user, panel_user_id):
    """Build the QR only from the freshly returned Panel subscription URL."""
    url = panel_user.get('subscriptionUrl') or ''
    local = owned_subscription(tg_id, panel_user_id)
    back = InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回订阅', callback_data='client_status')]])
    if not local:
        await context.bot.send_message(tg_id, '⚠️ 本地订阅绑定已变化，请联系管理员。', reply_markup=back)
        return
    summary = fit_message(f"📃 订阅详情\n状态：{panel_user.get('status', '-')}\n到期：{format_time(panel_user.get('expireAt'))}", 900)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton('💳 续费此订阅', callback_data=f"selrenew_{get_short_id(panel_user_id)}")],
        [InlineKeyboardButton('📱 我的设备', callback_data=f"client_devices_{local['id']}"),
         InlineKeyboardButton('🔐 重置订阅', callback_data=f"client_revoke_{local['id']}")],
        [InlineKeyboardButton('🔙 返回列表', callback_data='client_status')],
    ])
    if url.startswith(('https://', 'http://')):
        await context.bot.send_photo(tg_id, photo=generate_qr(url), caption=summary,
                                     parse_mode=None, reply_markup=keyboard)
        await context.bot.send_message(tg_id, fit_message(f'🔗 最新订阅链接：\n{url}', 4000), parse_mode=None)
    else:
        await context.bot.send_message(tg_id, summary + '\n订阅链接：暂不可用', parse_mode=None, reply_markup=keyboard)


async def show_client_devices(update, context, local_id):
    tg_id = update.effective_user.id
    local = db_query('SELECT * FROM subscriptions WHERE id=? AND tg_id=? AND user_id IS NOT NULL',
                     (local_id, tg_id), one=True)
    if not local:
        await send_or_edit_menu(update, context, '⚠️ 订阅绑定不存在或无权限。',
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='client_status')]]), parse_mode=None)
        return
    panel_user_id = int(local['user_id'])
    user = await checked_owned_panel_user(tg_id, panel_user_id)
    if not user:
        await send_or_edit_menu(update, context, '⚠️ 面板用户绑定需要管理员核对。',
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='client_status')]]), parse_mode=None)
        return
    payload = await get_user_devices(panel_user_id)
    rows = []
    for i, device in enumerate(payload['devices'][:8], 1):
        token = create_action_request(DB_FILE, tg_id, panel_user_id, 'user_delete_one', {'hwid': device['hwid']})
        rows.append([InlineKeyboardButton(f'🗑 清除设备 #{i}', callback_data=f'client_device_confirm_{token}')])
    if payload['devices']:
        rows.append([InlineKeyboardButton('🗑 清除全部设备', callback_data=f'client_clear_devices_{local_id}')])
    rows.append([InlineKeyboardButton('🔙 返回订阅', callback_data='client_status')])
    await send_or_edit_menu(update, context, device_summary(payload, user.get('hwidDeviceLimit')),
                            InlineKeyboardMarkup(rows), parse_mode=None)


def _action_failure_status(exc):
    message = str(exc)
    return 'failed' if any(f'HTTP {code}' in message for code in (400, 401, 403, 404, 409)) else 'unknown'


async def execute_confirmed_action(update, context, *, admin=False):
    query = update.callback_query
    actor = query.from_user.id
    token = query.data.split('_', 1)[1]
    row = get_action_request(DB_FILE, token)
    allowed = {'admin_delete_one', 'admin_delete_all', 'admin_revoke'} if admin else {
        'user_delete_one', 'user_delete_all', 'user_revoke'}
    if not row or row['tg_id'] != actor or row['action'] not in allowed or (admin and actor != ADMIN_ID):
        await query.answer('无权限或操作已过期', show_alert=True)
        return
    panel_user_id = int(row['user_id'])
    if not admin and not await checked_owned_panel_user(actor, panel_user_id):
        await query.answer('订阅绑定已变化，操作已停止', show_alert=True)
        return
    if not claim_action_request(DB_FILE, token, actor, row['action']):
        await query.answer('操作已处理、正在处理或已过期，请勿重复提交', show_alert=True)
        return
    await query.answer()
    try:
        if row['action'].endswith('delete_one'):
            hwid = json.loads(row['payload_json'])['hwid']
            payload = await delete_user_device(panel_user_id, hwid)
            result = f"✅ 设备已清除。当前设备数：{int(payload['total'])}。"
        elif row['action'].endswith('delete_all'):
            payload = await delete_all_user_devices(panel_user_id)
            result = f"✅ 设备已清空。当前设备数：{int(payload['total'])}。"
        else:
            await revoke_subscription(panel_user_id)
            result = '✅ 订阅已重置。旧凭据可能失效，请使用最新订阅信息。'
        finish_action_request(DB_FILE, token, 'done')
    except Exception as exc:
        status = _action_failure_status(exc)
        finish_action_request(DB_FILE, token, status)
        logger.warning('V3.8 action %s for user %s ended %s (%s)', row['action'], panel_user_id,
                       status, type(exc).__name__)
        result = ('⚠️ 结果不确定，请联系管理员核对后再操作。' if status == 'unknown'
                  else '❌ 面板拒绝了操作，请检查权限或用户状态。')
        await send_or_edit_menu(update, context, result, InlineKeyboardMarkup([
            [InlineKeyboardButton('🔙 返回', callback_data='back_home')]]), parse_mode=None)
        return
    await send_or_edit_menu(update, context, result, InlineKeyboardMarkup([
        [InlineKeyboardButton('🔙 返回', callback_data='back_home')]]), parse_mode=None)
    if row['action'].endswith('revoke'):
        try:
            refreshed = await get_panel_user(panel_user_id)
        except PanelApiError:
            refreshed = None
        if not refreshed:
            await context.bot.send_message(actor, '⚠️ 暂时无法读取新订阅链接。请稍后从“我的订阅”重新打开；不要使用旧二维码。')
            return
        if admin:
            local = db_query('SELECT tg_id FROM subscriptions WHERE user_id=?', (panel_user_id,), one=True)
            if local and refreshed.get('telegramId') in (None, int(local['tg_id'])):
                try:
                    await send_subscription_card(context, int(local['tg_id']), refreshed, panel_user_id)
                except Exception as exc:
                    logger.warning('Could not notify user after admin revoke: %s', type(exc).__name__)
        else:
            if await checked_owned_panel_user(actor, panel_user_id):
                await send_subscription_card(context, actor, refreshed, panel_user_id)
            else:
                await context.bot.send_message(actor, '⚠️ 订阅绑定已变化，请联系管理员获取新的订阅信息。')


async def show_admin_devices(update, context, panel_user_id):
    user = await get_panel_user(panel_user_id)
    if not user:
        await send_or_edit_menu(update, context, '⚠️ 面板用户不存在。',
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='admin_panel_user_lookup')]]), parse_mode=None)
        return
    devices = await get_user_devices(panel_user_id)
    rows = []
    for i, device in enumerate(devices['devices'][:8], 1):
        token = create_action_request(DB_FILE, ADMIN_ID, panel_user_id, 'admin_delete_one', {'hwid': device['hwid']})
        rows.append([InlineKeyboardButton(f'🗑 清除设备 #{i}', callback_data=f'admin_device_confirm_{token}')])
    rows.extend([
        [InlineKeyboardButton('🗑 清除全部', callback_data=f'admin_clear_devices_{panel_user_id}')],
        [InlineKeyboardButton('🔢 修改设备上限', callback_data=f'admin_limit_{panel_user_id}')],
        [InlineKeyboardButton('🔙 返回用户', callback_data=f'manage_user_{panel_user_id}')],
    ])
    await send_or_edit_menu(update, context, device_summary(devices, user.get('hwidDeviceLimit'), admin=True),
                            InlineKeyboardMarkup(rows), parse_mode=None)


async def show_admin_dashboard(update, context):
    if not panel_config_ready():
        await send_or_edit_menu(update, context, '⚠️ 请先配置面板地址和 Token。',
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='back_home')]]), parse_mode=None)
        return
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=7)
    stamp = lambda value: value.isoformat(timespec='seconds').replace('+00:00', 'Z')
    stats, recap, digest = await asyncio.gather(
        get_panel_system_stats(), get_panel_system_stats_recap(),
        v38_api.get_system_stats_digest(stamp(start), stamp(end), PANEL_URL, get_headers(), PANEL_VERIFY_TLS),
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('🔄 刷新', callback_data='admin_system_dashboard')],
        [InlineKeyboardButton('🌐 节点指标', callback_data='admin_dashboard_nodes'),
         InlineKeyboardButton('📈 HTTP 统计', callback_data='admin_dashboard_http')],
        [InlineKeyboardButton('🔙 返回', callback_data='back_home')],
    ])
    await send_or_edit_menu(update, context, dashboard_summary(stats, recap, digest), kb, parse_mode=None)


async def show_admin_geocheck_nodes(update, context):
    nodes = await get_nodes_status()
    rows = []
    for node in nodes[:15]:
        node_uuid = node.get('uuid')
        if node_uuid:
            rows.append([InlineKeyboardButton(str(node.get('name') or node_uuid)[:40], callback_data=f'admin_node_{node_uuid}')])
    rows.append([InlineKeyboardButton('🔙 返回', callback_data='back_home')])
    await send_or_edit_menu(update, context,
                            fit_message(f'🩺 请选择 GeoCheck 节点（显示 {min(len(nodes), 15)}/{len(nodes)}）。\n需要兼容的 Remnawave Node。'),
                            InlineKeyboardMarkup(rows), parse_mode=None)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = update.effective_user.id
    support_ctx = get_support_reply_session(context, user_id)
    if support_ctx and support_ctx.get('control_message_id'):
        try:
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=int(support_ctx.get('control_message_id')),
                text="✅ 客服会话已结束。若需继续，请点击“联系客服”。",
                parse_mode='Markdown',
                reply_markup=None,
            )
        except Exception as exc:
            logger.debug("close support control prompt failed: user=%s err=%s", user_id, exc)
    clear_support_reply_session(context, user_id, reason='enter_start_menu')
    args = getattr(context, 'args', None) or []
    if args:
        raw = str(args[0]).strip()
        if raw:
            channel_code = raw[2:] if raw.startswith('c_') else raw
            context.user_data['channel_code'] = channel_code[:32]
    if user_id == ADMIN_ID:
        try:
            val_notify = db_query("SELECT value FROM settings WHERE key='notify_days'", one=True)
            notify_days = int(val_notify['value']) if val_notify else 3
            val_cleanup = db_query("SELECT value FROM settings WHERE key='cleanup_days'", one=True)
            cleanup_days = int(val_cleanup['value']) if val_cleanup else 7
        except Exception as exc:
            logger.warning("failed to load admin settings, using defaults: %s", exc)
            notify_days = 3
            cleanup_days = 7
        try:
            pending_cnt = db_query("SELECT COUNT(*) AS c FROM orders WHERE status='pending'", one=True)['c']
            failed_cnt = db_query("SELECT COUNT(*) AS c FROM orders WHERE status='failed'", one=True)['c']
            today_ts = int(datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            today_cnt = db_query("SELECT COUNT(*) AS c FROM orders WHERE created_at>=?", (today_ts,), one=True)['c']
        except Exception:
            pending_cnt = failed_cnt = today_cnt = 0
        msg_text = (
            f"👮‍♂️ **管理员控制台**\n"
            f"🔔 提醒设置：提前 {notify_days} 天\n"
            f"🗑 清理设置：过期 {cleanup_days} 天\n"
            f"📊 今日订单：{today_cnt} | 待审核：{pending_cnt} | 失败：{failed_cnt}"
        )
        keyboard = [
            [InlineKeyboardButton("📦 套餐管理", callback_data="admin_plans_list")],
            [InlineKeyboardButton("👥 用户列表", callback_data="admin_users_list")],
            [InlineKeyboardButton("🔔 提醒设置", callback_data="admin_notify"), InlineKeyboardButton("🗑 清理设置", callback_data="admin_cleanup")],
            [InlineKeyboardButton("🛡️ 异常设置", callback_data="admin_anomaly_menu")],
            [InlineKeyboardButton("📚 批量操作", callback_data="admin_bulk_menu")],
            [InlineKeyboardButton("🧾 订单审计", callback_data="admin_orders_menu"), InlineKeyboardButton("🧾 风控回溯", callback_data="admin_risk_audit")],
            [InlineKeyboardButton("⚙️ 订阅设置", callback_data="admin_subscription_settings"), InlineKeyboardButton("🧩 用户分组", callback_data="admin_squads_menu")],
            [InlineKeyboardButton("📈 带宽看板", callback_data="admin_bandwidth_dashboard"), InlineKeyboardButton("🛡️ 风控策略", callback_data="admin_risk_policy")],
            [InlineKeyboardButton("🕒 操作时间线", callback_data="admin_ops_timeline"), InlineKeyboardButton("📢 群发通知", callback_data="admin_broadcast_start")],
            [InlineKeyboardButton("💳 收款设置", callback_data="admin_pay_settings"), InlineKeyboardButton("🔌 面板配置", callback_data="admin_panel_config")],
            [InlineKeyboardButton("🧩 模板中心", callback_data="admin_template_center"), InlineKeyboardButton("🗂 批量任务", callback_data="admin_bulk_jobs")],
            [InlineKeyboardButton("🔎 面板用户检索", callback_data="admin_panel_user_lookup"), InlineKeyboardButton("📊 数据统计", callback_data="admin_system_dashboard")],
            [InlineKeyboardButton("📱 HWID 统计", callback_data="admin_hwid_stats"), InlineKeyboardButton("🩺 Node GeoCheck", callback_data="admin_geocheck_nodes")]
        ]
    else:
        msg_text = "👋 **欢迎使用自助服务！**\n请选择操作："
        keyboard = [
            [InlineKeyboardButton("🛒 购买新订阅", callback_data="client_buy_new")],
            [InlineKeyboardButton("🔍 我的订阅 / 续费", callback_data="client_status")],
            [InlineKeyboardButton("📄 我的订单", callback_data="client_orders")],
            [InlineKeyboardButton("🌍 节点状态", callback_data="client_nodes"), InlineKeyboardButton("🆘 联系客服", callback_data="contact_support")]
        ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_or_edit_menu(update, context, msg_text, reply_markup)

async def client_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not check_cooldown(query.from_user.id):
        await query.answer("⏳ 操作太快了...", show_alert=False)
        return
    data = query.data
    user_id = query.from_user.id
    if data.startswith('v38u_'):
        await execute_confirmed_action(update, context)
        return
    await query.answer()

    if data.startswith('client_devices_'):
        try:
            await show_client_devices(update, context, int(data.removeprefix('client_devices_')))
        except (PanelApiError, ValueError, KeyError) as exc:
            logger.warning('GET /hwid/devices/{userId} failed: %s', type(exc).__name__)
            await send_or_edit_menu(update, context, '⚠️ 设备信息暂不可用，请稍后重试。',
                                    InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='client_status')]]), parse_mode=None)
        return
    if data.startswith('client_device_confirm_'):
        token = data.removeprefix('client_device_confirm_')
        row = get_action_request(DB_FILE, token)
        if not row or row['tg_id'] != user_id or row['action'] != 'user_delete_one' or not owned_subscription(user_id, row['user_id']):
            await query.answer('无权限或操作已过期', show_alert=True)
            return
        await send_or_edit_menu(update, context, '⚠️ 确认清除这台设备？该设备可能需要重新连接。',
                                InlineKeyboardMarkup([[InlineKeyboardButton('确认清除', callback_data=f'v38u_{token}')],
                                                      [InlineKeyboardButton('取消', callback_data='client_status')]]), parse_mode=None)
        return
    if data.startswith(('client_clear_devices_', 'client_revoke_')):
        clear_all = data.startswith('client_clear_devices_')
        prefix = 'client_clear_devices_' if clear_all else 'client_revoke_'
        try:
            local_id = int(data.removeprefix(prefix))
        except ValueError:
            await query.answer('无效订阅', show_alert=True)
            return
        local = db_query('SELECT * FROM subscriptions WHERE id=? AND tg_id=? AND user_id IS NOT NULL',
                         (local_id, user_id), one=True)
        if not local or not await checked_owned_panel_user(user_id, int(local['user_id'])):
            await query.answer('订阅绑定不存在或无权限', show_alert=True)
            return
        action = 'user_delete_all' if clear_all else 'user_revoke'
        token = create_action_request(DB_FILE, user_id, int(local['user_id']), action)
        warning = ('⚠️ 确认清除全部设备？所有设备可能需要重新连接。' if clear_all
                   else '⚠️ 确认重置订阅？旧订阅凭据和二维码可能立即失效。')
        await send_or_edit_menu(update, context, warning,
                                InlineKeyboardMarkup([[InlineKeyboardButton('确认操作', callback_data=f'v38u_{token}')],
                                                      [InlineKeyboardButton('取消', callback_data='client_status')]]), parse_mode=None)
        return

    if data == "back_home":
        await start(update, context)
        return

    if data == "client_nodes":
        try: await query.edit_message_text("🔄 正在获取节点状态...")
        except Exception as exc:
            logger.debug("node status loading hint message failed: %s", exc)
        nodes = await get_nodes_status()
        msg_list = ["🌍 **节点状态**\n"]
        if not nodes:
            msg_list.append("⚠️ 暂无节点信息")
        else:
            for node in nodes:
                name = node.get('name', '未知节点')
                is_online = node['isConnected']
                icon = "🟢" if is_online else "🔴"
                stat_text = "在线" if is_online else "离线"
                msg_list.append(f"{icon} **{name}** | {stat_text}")
        msg_list.append(f"\n_更新时间: {datetime.datetime.now().strftime('%H:%M:%S')}_")
        kb = [[InlineKeyboardButton("🔄 刷新", callback_data="client_nodes")], [InlineKeyboardButton("🔙 返回", callback_data="back_home")]]
        await send_or_edit_menu(update, context, "\n".join(msg_list), InlineKeyboardMarkup(kb))
        return

    if data == "contact_support":
        context.user_data['chat_mode'] = 'support'
        support_ctx = {'source': 'user_initiated', 'updated_at': int(time.time())}
        context.user_data['support_reply_context'] = support_ctx
        set_support_reply_session(context, user_id, source='user_initiated')
        if query.message:
            store = _get_support_session_store(context.application)
            sess = store.get(int(user_id), {})
            if isinstance(sess, dict):
                sess['control_message_id'] = query.message.message_id
                store[int(user_id)] = sess
        logger.info("user entered support mode: user=%s source=user_initiated", user_id)
        msg = dynamic_snippets_cache.get("support_contact_tip") or "📞 **客服模式已开启**\n请直接发送文字、图片或文件。\n🚪 结束咨询请点击下方按钮。"
        keyboard = [[InlineKeyboardButton("🚪 结束咨询", callback_data="back_home")]]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(keyboard))
        return

    if data == "client_pay_done_upload":
        await query.answer("✅ 已切换为“发送凭证即提交审核”，请直接发送支付凭证。", show_alert=True)
        return


    if data == "client_orders":
        rows = db_query("SELECT * FROM orders WHERE tg_id=? ORDER BY created_at DESC LIMIT 12", (user_id,))
        if not rows:
            await send_or_edit_menu(update, context, "📄 **我的订单**\n暂无订单记录。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
            return
        keyboard = []
        for row in rows:
            item = dict(row)
            ts = datetime.datetime.fromtimestamp(int(item['created_at'])).strftime('%m-%d %H:%M')
            keyboard.append([InlineKeyboardButton(f"{order_status_label(item['status'])} | {item['order_id']} | {ts}", callback_data=f"client_order_{item['order_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
        await send_or_edit_menu(update, context, "📄 **我的订单（最近12条）**", InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("client_order_cancel_"):
        order_id = data.replace("client_order_cancel_", "")
        order = get_order(db_query, order_id)
        if not order or int(order.get('tg_id', 0)) != int(user_id):
            await query.answer("订单不存在", show_alert=True)
            return
        ok = update_order_status(db_execute, order_id, [STATUS_PENDING], STATUS_REJECTED, error_message='cancelled_by_user')
        if ok:
            append_order_audit_log(db_execute, order_id, 'cancel_by_user', user_id, 'user_cancel_pending_order')
            await query.answer("✅ 已取消订单", show_alert=True)
        else:
            await query.answer("⚠️ 仅待审核订单可取消", show_alert=True)
        rows = db_query("SELECT * FROM orders WHERE tg_id=? ORDER BY created_at DESC LIMIT 12", (user_id,))
        keyboard = []
        for row in rows:
            item = dict(row)
            ts = datetime.datetime.fromtimestamp(int(item['created_at'])).strftime('%m-%d %H:%M')
            keyboard.append([InlineKeyboardButton(f"{order_status_label(item['status'])} | {item['order_id']} | {ts}", callback_data=f"client_order_{item['order_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
        await send_or_edit_menu(update, context, "📄 **我的订单（最近12条）**", InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("client_order_"):
        order_id = data.replace("client_order_", "")
        order = get_order(db_query, order_id)
        if not order or int(order.get('tg_id', 0)) != int(user_id):
            await send_or_edit_menu(update, context, "⚠️ 订单不存在或无权限查看", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="client_orders")]]))
            return
        plan = db_query("SELECT * FROM plans WHERE key = ?", (order['plan_key'],), one=True)
        plan_name = dict(plan)['name'] if plan else order['plan_key']
        created = datetime.datetime.fromtimestamp(int(order['created_at'])).strftime('%Y-%m-%d %H:%M')
        lines = [
            "📄 **订单详情**",
            f"订单号: `{order['order_id']}`",
            f"状态: `{order_status_label(order['status'])}`",
            f"类型: `{ '续费' if order['order_type'] == 'renew' else '新购' }`",
            f"套餐: `{plan_name}`",
            f"渠道: `{order.get('channel_code') or '-'}`",
            f"创建时间: `{created}`",
        ]
        if order.get('delivered_user_id'):
            lines.append(f"面板用户ID: `{order['delivered_user_id']}`")
        if order.get('error_message'):
            lines.append(f"失败原因: `{order['error_message']}`")
        kb = []
        if order['status'] == STATUS_PENDING:
            kb.append([InlineKeyboardButton("❌ 取消该订单", callback_data=f"client_order_cancel_{order['order_id']}")])
        kb.append([InlineKeyboardButton("🔙 返回订单列表", callback_data="client_orders")])
        await send_or_edit_menu(update, context, "\n".join(lines), InlineKeyboardMarkup(kb))
        return

    if data == "client_buy_new":
        keyboard = []
        plans = db_query("SELECT * FROM plans")
        for p in plans:
            p_dict = dict(p) 
            strategy = p_dict.get('reset_strategy', 'NO_RESET')
            strategy_label = get_strategy_label(strategy)
            btn_text = f"{p_dict['name']} | ¥{p_dict['price']} / {get_plan_price(p_dict, 'usdt')} | {p_dict['gb']}G ({strategy_label})"
            action = f"order_{p_dict['key']}_new_0"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=action)])
        keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="back_home")])
        await send_or_edit_menu(update, context, "🛒 **请选择新购套餐：**", InlineKeyboardMarkup(keyboard))

    elif data == "client_status":
        subs = db_query("SELECT * FROM subscriptions WHERE tg_id = ?", (user_id,))
        if not subs:
            try:
                panel_user = await get_user_by_telegram_id(user_id)
            except AmbiguousPanelUserError:
                await send_or_edit_menu(update, context, "⚠️ 多个面板用户使用此 Telegram ID，请联系管理员核对绑定。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
                return
            except PanelApiError:
                await send_or_edit_menu(update, context, "⚠️ 面板查询失败，请稍后重试。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
                return
            synced_uuid = ensure_local_subscription_sync(user_id, panel_user)
            if synced_uuid:
                append_ops_timeline('数据修复', '按TG ID自动补齐订阅映射', f'tg_id={user_id},user_id={synced_uuid}', actor='system')
                subs = db_query("SELECT * FROM subscriptions WHERE tg_id = ?", (user_id,))
        mapped_subs = [sub for sub in subs if sub['user_id'] is not None]
        pending_count = len(subs) - len(mapped_subs)
        subscription_list_title = "👤 **我的订阅列表**\n请点击下方按钮查看详情："
        if pending_count:
            subscription_list_title += f"\n⚠️ 另有 {pending_count} 条旧订阅等待管理员核对迁移。"
        if not mapped_subs:
            message = "⚠️ 旧订阅正在等待管理员核对迁移，请勿重复购买。" if subs else "❌ 您名下没有订阅。\n请点击“购买新订阅”。"
            await send_or_edit_menu(update, context, message, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
            return
        try: await query.edit_message_text("🔄 正在加载订阅列表...")
        except Exception as exc:
            logger.debug("failed to delete view_sub message: %s", exc)
        tasks = [get_panel_user(sub['user_id']) for sub in mapped_subs]
        results = await asyncio.gather(*tasks)
        keyboard = []
        valid_count = 0
        for i, info in enumerate(results):
            sub_db = mapped_subs[i]
            if not info: continue
            valid_count += 1
            limit = info.get('trafficLimitBytes', 0)
            used = info.get('userTraffic', {}).get('usedTrafficBytes', 0)
            remain_gb = round((limit - used) / (1024**3), 1)
            sid = get_short_id(sub_db['user_id'])
            btn_text = f"📦 订阅 #{valid_count} | 剩余 {remain_gb} GB"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_sub_{sid}")])
        if valid_count == 0:
            try:
                panel_user = await get_user_by_telegram_id(user_id)
            except AmbiguousPanelUserError:
                await send_or_edit_menu(update, context, "⚠️ 多个面板用户使用此 Telegram ID，请联系管理员核对绑定。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
                return
            except PanelApiError:
                await send_or_edit_menu(update, context, "⚠️ 面板查询失败，请稍后重试。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
                return
            synced_uuid = ensure_local_subscription_sync(user_id, panel_user)
            if synced_uuid:
                info = await get_panel_user(synced_uuid)
                if info:
                    limit = info.get('trafficLimitBytes', 0)
                    used = info.get('userTraffic', {}).get('usedTrafficBytes', 0)
                    remain_gb = round((limit - used) / (1024**3), 1)
                    sid = get_short_id(synced_uuid)
                    keyboard = [[InlineKeyboardButton(f"📦 订阅 #1 | 剩余 {remain_gb} GB", callback_data=f"view_sub_{sid}")], [InlineKeyboardButton("🔙 返回主菜单", callback_data="back_home")]]
                    append_ops_timeline('数据修复', '按TG ID恢复订阅入口', f'tg_id={user_id},user_id={synced_uuid}', actor='system')
                    await send_or_edit_menu(update, context, subscription_list_title, InlineKeyboardMarkup(keyboard))
                    return
            await send_or_edit_menu(update, context, "⚠️ 您的所有订阅似乎都已失效。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
            return
        keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="back_home")])
        await send_or_edit_menu(update, context, subscription_list_title, InlineKeyboardMarkup(keyboard))

    elif data.startswith("view_sub_"):
        short_id = data.split("_")[2]
        target_user_id = get_panel_user_id_from_short(short_id)
        if not target_user_id or not owned_subscription(user_id, target_user_id):
            await query.answer("❌ 按钮已过期")
            return
        await query.answer("🔄 加载详情中...")
        try: await query.delete_message()
        except Exception as exc:
            logger.debug("delete stale sub detail message failed: %s", exc)
        info = await checked_owned_panel_user(user_id, target_user_id)
        if not info:
            await context.bot.send_message(user_id, "⚠️ 订阅不存在或绑定需要管理员核对。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回列表", callback_data="client_status")]]))
            return
        await send_subscription_card(context, user_id, info, target_user_id)

    elif data.startswith("selrenew_"):
        short_id = data.split("_")[1]
        target_uuid = get_panel_user_id_from_short(short_id)
        if not target_uuid or not owned_subscription(user_id, target_uuid):
            await query.answer("❌ 信息过期")
            return
        
        sub_record = db_query("SELECT * FROM subscriptions WHERE user_id = ?", (target_uuid,), one=True)
        original_plan_key = None
        if sub_record:
            sub_dict = dict(sub_record)
            original_plan_key = sub_dict.get('plan_key')
        
        if original_plan_key:
            plan = db_query("SELECT * FROM plans WHERE key = ?", (original_plan_key,), one=True)
            if plan:
                await show_payment_method_menu(update, context, original_plan_key, 'renew', short_id)
                return

        keyboard = []
        plans = db_query("SELECT * FROM plans")
        for p in plans:
            p_dict = dict(p)
            strategy = p_dict.get('reset_strategy', 'NO_RESET')
            strategy_label = get_strategy_label(strategy)
            btn_text = f"{p_dict['name']} | ¥{p_dict['price']} / {get_plan_price(p_dict, 'usdt')} | {p_dict['gb']}G ({strategy_label})"
            action = f"order_{p_dict['key']}_renew_{short_id}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=action)])
        keyboard.append([InlineKeyboardButton("🔙 返回列表", callback_data="client_status")])
        await send_or_edit_menu(update, context, "🔄 **请选择要续费的时长：**\n(流量和时间将自动叠加)", InlineKeyboardMarkup(keyboard))

    elif data.startswith("order_"):
        parts = data.split("_")
        if len(parts) < 4:
            logger.warning("Invalid order callback payload: %s", data)
            await query.answer("参数错误，请重试", show_alert=True)
            return
        plan_key = parts[1]
        order_type = parts[2]
        if order_type == 'renew':
            short_id = parts[3]
        else:
            short_id = "0"
        
        await show_payment_method_menu(update, context, plan_key, order_type, short_id)

    elif data.startswith("manualreview_"):
        parts = data.split("_", 3)
        if len(parts) < 4:
            logger.warning("Invalid manualreview callback payload: %s", data)
            await query.answer("参数错误", show_alert=True)
            return
        _, plan_key, order_type, short_id = parts
        await handle_order_confirmation(update, context, plan_key, order_type, short_id, payment_method='manual_review')

    elif data.startswith("paymethod_"):
        parts = data.split("_", 4)
        if len(parts) < 5:
            logger.warning("Invalid paymethod callback payload: %s", data)
            await query.answer("参数错误", show_alert=True)
            return
        _, pay_method, plan_key, order_type, short_id = parts
        if pay_method != "usdt":
            await query.answer("当前客户端仅支持 人工审核 / USDT。", show_alert=True)
            return
        await handle_order_confirmation(update, context, plan_key, order_type, short_id, payment_method='usdt')

    elif data == "cancel_order":
        pending = get_pending_order_for_user(db_query, user_id)
        if pending:
            update_order_status(db_execute, pending['order_id'], [STATUS_PENDING], STATUS_REJECTED, error_message='cancelled_by_user')
        context.user_data.pop('pending_payment_proof', None)
        await start(update, context)

async def show_payment_method_menu(update, context, plan_key, order_type, short_id):
    plan = db_query("SELECT * FROM plans WHERE key = ?", (plan_key,), one=True)
    if not plan:
        await send_or_edit_menu(update, context, "⚠️ 套餐不存在或已下架，请返回重新选择。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
        return

    if not panel_config_ready() or (order_type == 'new' and not TARGET_GROUP_UUID):
        await send_or_edit_menu(
            update, context, "⚠️ 面板地址、Token 或新购所需默认内部组尚未配置，请联系管理员。",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]),
        )
        return

    plan_dict = dict(plan)

    type_str = "续费" if order_type == 'renew' else "新购"

    msg = (
        f"🧾 **下单确认（{type_str}）**\n"
        f"📦 套餐：{plan_dict['name']}\n"
        f"💰 金额：{plan_dict.get('price')}\n"
        f"📡 流量：{plan_dict.get('gb')} GB\n\n"
        "请选择下一步："
    )
    kb = [[InlineKeyboardButton("🧾 人工审核", callback_data=f"manualreview_{plan_key}_{order_type}_{short_id}")]]
    if resolve_payment_state('usdt')['available'] and str(plan_dict.get('usdt_price') or '').strip():
        kb.append([InlineKeyboardButton("💠 USDT 转账", callback_data=f"paymethod_usdt_{plan_key}_{order_type}_{short_id}")])
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
    await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb))

async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(update, Update) and update.callback_query:
        callback_data = update.callback_query.data
        user_id = update.callback_query.from_user.id
        logger.exception("Unhandled callback exception: user_id=%s callback_data=%s error=%s", user_id, callback_data, err)
        try:
            await update.callback_query.answer("⚠️ 系统异常，请稍后重试。", show_alert=True)
        except Exception:
            pass
        return
    logger.exception("Unhandled telegram update exception: %s", err)

async def submit_manual_review_proof(update: Update, context: ContextTypes.DEFAULT_TYPE, pending_order: dict, proof: dict):
    user_id = int(pending_order['tg_id'])
    order_id = pending_order['order_id']
    plan = db_query("SELECT * FROM plans WHERE key = ?", (pending_order['plan_key'],), one=True)
    if not plan:
        update_order_status(db_execute, order_id, [STATUS_PENDING], STATUS_FAILED, error_message='plan_deleted')
        await update.message.reply_text("❌ 套餐已失效，订单已关闭，请重新下单。")
        return

    plan_dict = dict(plan)
    strategy_label = get_strategy_label(plan_dict.get('reset_strategy', 'NO_RESET'))
    type_str = "续费" if pending_order['order_type'] == 'renew' else "新购"
    selected_path = order_payment_method_cache.get(order_id, 'manual_review')
    selected_path_label = "USDT" if selected_path == "usdt" else "人工审核"
    target_user_id = pending_order.get('target_user_id')
    sid = get_short_id(target_user_id) if target_user_id else "0"
    username = update.effective_user.username or "-"
    proof_type = proof.get('type')
    proof_text = (proof.get('text') or '').strip()
    if not proof_text:
        proof_text = "[图片/文件]"

    kb = [
        [InlineKeyboardButton("✅ 通过", callback_data=f"ap_{order_id}_{sid}")],
        [InlineKeyboardButton("❌ 拒绝", callback_data=f"rj_{order_id}")],
        [InlineKeyboardButton("📨 给用户发消息", callback_data=f"reply_user_{user_id}_{order_id}")],
    ]
    caption = (
        f"🧾 人工审核订单\n"
        f"🆔 订单号: {order_id}\n"
        f"👤 用户ID: {user_id} (@{username})\n"
        f"📦 套餐: {plan_dict['name']}\n"
        f"💰 金额: {plan_dict.get('price')}\n"
        f"📡 流量: {plan_dict.get('gb')} GB ({strategy_label})\n"
        f"🧩 类型: {type_str}\n"
        f"🛣 路径: {selected_path_label}\n"
        f"🏷 渠道码: {pending_order.get('channel_code') or '-'}\n"
        f"📝 用户凭证: {proof_text}"
    )

    admin_message = None
    if proof_type == 'text':
        admin_message = await context.bot.send_message(ADMIN_ID, caption, reply_markup=InlineKeyboardMarkup(kb))
    elif proof_type == 'photo' and proof.get('file_id'):
        admin_message = await context.bot.send_photo(ADMIN_ID, photo=proof['file_id'], caption=caption, reply_markup=InlineKeyboardMarkup(kb))
    elif proof_type == 'document' and proof.get('file_id'):
        admin_message = await context.bot.send_document(ADMIN_ID, document=proof['file_id'], caption=caption, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text("⚠️ 未识别的凭证格式，请发送文字、图片或文件。")
        return

    append_order_audit_log(db_execute, order_id, 'submit_manual_review', user_id, f"proof_type={proof_type}")
    attach_admin_message(db_execute, order_id, admin_message.message_id)
    attach_payment_text(db_execute, order_id, f"方式:{selected_path_label}|{proof_text}")
    context.user_data.pop('pending_payment_proof', None)
    context.user_data.pop('awaiting_manual_review_proof_order_id', None)
    await update.message.reply_text(
        "✅ 已提交人工审核，请等待管理员处理。",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回主菜单", callback_data="back_home")]]),
    )


async def cleanup_panelcfg_prompt_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    msg_id = context.user_data.pop('panelcfg_prompt_message_id', None)
    if not msg_id:
        return
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception as exc:
        logger.debug("failed to cleanup panelcfg prompt message %s: %s", msg_id, exc)


async def handle_order_confirmation(update, context, plan_key, order_type, short_id, payment_method='manual_review'):
    user_id = update.effective_user.id
    target_user_id = get_panel_user_id_from_short(short_id) if short_id != "0" else None
    if order_type == 'renew' and not target_user_id:
        await send_or_edit_menu(update, context, "⚠️ 旧订阅尚未完成迁移，暂不能续费。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="client_status")]]))
        return
    if order_type == 'renew' and not owned_subscription(user_id, target_user_id):
        await send_or_edit_menu(update, context, "⚠️ 此订阅不属于当前用户，无法创建续费订单。",
                                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="client_status")]]))
        return
    if order_type == 'new' and db_query(
        "SELECT 1 FROM subscriptions WHERE tg_id=? AND user_id IS NULL LIMIT 1", (user_id,), one=True
    ):
        await send_or_edit_menu(update, context, "⚠️ 旧订阅正在等待迁移核对，请先联系管理员，避免重复开通。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
        return

    plan = db_query("SELECT * FROM plans WHERE key = ?", (plan_key,), one=True)
    if not plan:
        return

    plan_dict = dict(plan)
    strategy = plan_dict.get('reset_strategy', 'NO_RESET')
    strategy_label = get_strategy_label(strategy)
    type_str = "续费" if order_type == 'renew' else "新购"

    msg_id = None
    if update.callback_query and update.callback_query.message:
        msg_id = update.callback_query.message.message_id

    order, created = create_order(db_query, db_execute, user_id, plan_key, order_type, target_user_id, menu_message_id=msg_id, channel_code=context.user_data.get('channel_code'))
    if created:
        append_order_audit_log(db_execute, order['order_id'], 'create', user_id, f"type={order_type};plan={plan_key};channel={context.user_data.get('channel_code') or '-'}")
        selected_path = "usdt" if payment_method == "usdt" else "manual_review"
        order_payment_method_cache[order["order_id"]] = selected_path
        context.user_data['awaiting_manual_review_proof_order_id'] = order['order_id']
        context.user_data.pop('pending_payment_proof', None)
    else:
        msg = "⚠️ 你已有一个待审核订单，请先等待管理员处理，或取消后重新下单。"
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
        return
    path_label = "USDT" if payment_method == "usdt" else "人工审核"
    extra_tip = "👇 请直接在聊天中发送支付凭证（文字说明、截图、图片或文件），发送后会自动提交人工审核。"
    usdt_qr_file_id = None
    if payment_method == "usdt":
        usdt_info = resolve_payment_state('usdt')
        usdt_price = str(plan_dict.get('usdt_price') or '').strip()
        if not usdt_info['available'] or not usdt_price:
            await send_or_edit_menu(update, context, "⚠️ 当前 USDT 未配置完整，请选择人工审核。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
            return
        usdt_network = (get_setting_value('usdt_network', 'TRC20') or 'TRC20').strip().upper()
        usdt_address = (get_setting_value('usdt_address', '') or '').strip()
        usdt_qr_file_id = usdt_info.get('qr_file_id') if usdt_info.get('should_send_qr') else None
        tip_body = usdt_info['pay_tip'] if usdt_info.get('pay_tip') else "请完成转账后提交凭证。"
        extra_tip = (
            f"💰 USDT 金额：**{usdt_price} USDT**\n"
            f"🌐 转账网络：`{usdt_network}`\n"
            f"🏦 收款地址：`{usdt_address or '未配置'}`\n"
            f"{tip_body}\n\n"
            "👇 完成后请发送 TXID/截图/说明，发送后会自动提交人工审核。"
        )
    msg = (
        f"📝 **订单已创建**\n"
        f"🆔 订单号：`{order['order_id']}`\n"
        f"📦 套餐：{plan_dict['name']}\n"
        f"💰 金额：**{plan_dict.get('price')}**\n"
        f"📡 流量：**{plan_dict['gb']} GB ({strategy_label})**\n"
        f"🧩 类型：**{type_str}**\n"
        f"🧾 路径：**{path_label}**\n\n"
        f"🆔 系统将自动使用当前 Telegram ID：`{user_id}`\n"
        f"{extra_tip}"
    )
    kb = [[InlineKeyboardButton("❌ 取消订单", callback_data="cancel_order")], [InlineKeyboardButton("🔙 返回主菜单", callback_data="back_home")]]
    await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb))
    if payment_method == "usdt" and usdt_qr_file_id:
        usdt_price = str(plan_dict.get('usdt_price') or '').strip()
        usdt_network = (get_setting_value('usdt_network', 'TRC20') or 'TRC20').strip().upper()
        usdt_address = (get_setting_value('usdt_address', '') or '').strip()
        qr_caption = (
            "📷 **USDT 收款码**\n"
            f"💰 金额：**{usdt_price} USDT**\n"
            f"🌐 网络：`{usdt_network}`\n"
            f"🏦 地址：`{usdt_address or '未配置'}`\n"
            "转账完成后请发送 TXID / 截图 / 备注。"
        )
        try:
            await context.bot.send_photo(chat_id=user_id, photo=usdt_qr_file_id, caption=qr_caption, parse_mode='Markdown')
        except Exception as exc:
            logger.warning("failed to send usdt qr image: user=%s order=%s err=%s", user_id, order['order_id'], exc)

async def show_plans_menu(update, context):
    plans = db_query("SELECT * FROM plans")
    keyboard = []
    for p in plans:
        p_dict = dict(p)
        btn_text = f"{p_dict['name']} | ¥{p_dict['price']} / {get_plan_price(p_dict, 'usdt')} | {p_dict['gb']}G"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"plan_detail_{p_dict['key']}")])
    keyboard.append([InlineKeyboardButton("➕ 添加新套餐", callback_data="add_plan_start")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
    await send_or_edit_menu(update, context, "📦 **套餐管理**\n点击套餐查看详情或删除。", InlineKeyboardMarkup(keyboard))

async def reschedule_anomaly_job(application, interval_hours):
    try:
        current_jobs = application.job_queue.get_jobs_by_name('check_anomalies_job')
        for job in current_jobs:
            job.schedule_removal()
        interval_seconds = float(interval_hours) * 3600
        application.job_queue.run_repeating(check_anomalies_job, interval=interval_seconds, first=10, name='check_anomalies_job')
    except Exception as e:
        logger.error(f"Reschedule failed: {e}")



async def show_orders_menu(update, context, status_filter=None, page=0):
    page = max(int(page or 0), 0)
    page_size = 20
    offset = page * page_size

    if status_filter:
        rows = db_query("SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?", (status_filter, page_size, offset))
        total_row = db_query("SELECT COUNT(*) AS c FROM orders WHERE status=?", (status_filter,), one=True)
        total = int(total_row['c']) if total_row else 0
        title = f"🧾 **订单审计 - {order_status_label(status_filter)}**"
    else:
        rows = db_query("SELECT * FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?", (page_size, offset))
        total_row = db_query("SELECT COUNT(*) AS c FROM orders", one=True)
        total = int(total_row['c']) if total_row else 0
        title = "🧾 **订单审计 - 最近订单**"

    total_pages = max((total + page_size - 1) // page_size, 1)
    current_page = min(page + 1, total_pages)
    title += f"\n📄 第 {current_page}/{total_pages} 页"

    keyboard = []
    for row in rows:
        item = dict(row)
        keyboard.append([
            InlineKeyboardButton(
                format_order_row(item),
                callback_data=f"admin_order_{item['order_id']}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🟡 待审核", callback_data="admin_orders_status_pending"),
        InlineKeyboardButton("🟠 处理中", callback_data="admin_orders_status_approved"),
    ])
    keyboard.append([
        InlineKeyboardButton("✅ 已发货", callback_data="admin_orders_status_delivered"),
        InlineKeyboardButton("⛔ 已拒绝", callback_data="admin_orders_status_rejected"),
    ])
    keyboard.append([
        InlineKeyboardButton("❌ 失败", callback_data="admin_orders_status_failed"),
        InlineKeyboardButton("📋 全部", callback_data="admin_orders_menu"),
    ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"admin_orders_page_{status_filter or 'all'}_{page-1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"admin_orders_page_{status_filter or 'all'}_{page+1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
    await send_or_edit_menu(update, context, title, InlineKeyboardMarkup(keyboard))


async def show_anomaly_whitelist_menu(update, context):
    rows = db_query("SELECT * FROM anomaly_whitelist ORDER BY created_at DESC LIMIT 20")
    keyboard = [[InlineKeyboardButton("➕ 添加面板用户ID", callback_data="anomaly_whitelist_add")]]
    for row in rows:
        item = dict(row)
        label = str(item['user_id']) if item['user_id'] is not None else f"旧UUID {item['user_uuid'][:8]}（待迁移）"
        keyboard.append([InlineKeyboardButton(f"❌ 删除 {label}", callback_data=f"anomaly_whitelist_del_{item['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="admin_anomaly_menu")])
    await send_or_edit_menu(update, context, "📋 **异常检测白名单**", InlineKeyboardMarkup(keyboard))

async def admin_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("无管理员权限", show_alert=True)
        return
    if query.data.startswith('v38a_'):
        await execute_confirmed_action(update, context, admin=True)
        return
    await query.answer()
    data = query.data
    if not data.startswith('admin_limit_'):
        context.user_data.pop('hwid_limit_user', None)

    # 只要离开“回复输入模式”，就清理回复上下文，避免串场
    if not data.startswith("reply_user_"):
        if 'reply_to_uid' in context.user_data:
            logger.info("admin reply mode cleared by callback: admin=%s callback=%s", query.from_user.id, data)
        await cleanup_admin_reply_prompt(context, query.from_user.id, context.user_data, reason=f'callback:{data}')
        context.user_data.pop('reply_to_uid', None)
        context.user_data.pop('reply_back_cb', None)
    
    if data == "back_home":
        await start(update, context)
        return

    if data.startswith('admin_devices_'):
        try:
            await show_admin_devices(update, context, int(data.removeprefix('admin_devices_')))
        except (PanelApiError, ValueError, KeyError) as exc:
            logger.warning('admin GET /hwid/devices/{userId} failed: %s', type(exc).__name__)
            await send_or_edit_menu(update, context, '⚠️ 设备信息暂不可用。',
                                    InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='admin_panel_user_lookup')]]), parse_mode=None)
        return
    if data.startswith('admin_device_confirm_'):
        token = data.removeprefix('admin_device_confirm_')
        row = get_action_request(DB_FILE, token)
        if not row or row['tg_id'] != ADMIN_ID or row['action'] != 'admin_delete_one':
            await send_or_edit_menu(update, context, '⚠️ 操作已过期。',
                                    InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='back_home')]]), parse_mode=None)
            return
        await send_or_edit_menu(update, context, '⚠️ 确认清除该用户的这台设备？',
                                InlineKeyboardMarkup([[InlineKeyboardButton('确认清除', callback_data=f'v38a_{token}')],
                                                      [InlineKeyboardButton('取消', callback_data=f"admin_devices_{row['user_id']}")]]), parse_mode=None)
        return
    if data.startswith(('admin_clear_devices_', 'admin_revoke_')):
        clear_all = data.startswith('admin_clear_devices_')
        prefix = 'admin_clear_devices_' if clear_all else 'admin_revoke_'
        try:
            panel_user_id = int(data.removeprefix(prefix))
            if not await get_panel_user(panel_user_id):
                raise ValueError('missing user')
        except (ValueError, PanelApiError):
            await send_or_edit_menu(update, context, '⚠️ 面板用户不存在或暂不可用。',
                                    InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='back_home')]]), parse_mode=None)
            return
        token = create_action_request(DB_FILE, ADMIN_ID, panel_user_id,
                                      'admin_delete_all' if clear_all else 'admin_revoke')
        warning = ('⚠️ 确认清除该用户全部设备？' if clear_all else
                   '⚠️ 确认完整重置该用户订阅？旧凭据和二维码可能失效。')
        await send_or_edit_menu(update, context, warning,
                                InlineKeyboardMarkup([[InlineKeyboardButton('确认操作', callback_data=f'v38a_{token}')],
                                                      [InlineKeyboardButton('取消', callback_data=f'admin_devices_{panel_user_id}')]]), parse_mode=None)
        return
    if data.startswith('admin_limit_'):
        try:
            panel_user_id = int(data.removeprefix('admin_limit_'))
        except ValueError:
            return
        context.user_data['hwid_limit_user'] = panel_user_id
        await send_or_edit_menu(update, context, '请输入设备上限（0 或正整数；输入 null 清除限制）。',
                                InlineKeyboardMarkup([[InlineKeyboardButton('取消', callback_data=f'admin_devices_{panel_user_id}')]]), parse_mode=None)
        return
    if data == 'admin_hwid_stats':
        try:
            payload = await v38_api.get_hwid_devices_stats(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
            stats = payload['stats']
            body = (f"📱 HWID 统计\n总设备：{stats['totalHwidDevices']}\n唯一设备：{stats['totalUniqueDevices']}\n"
                    f"人均设备：{stats['averageHwidDevicesPerUser']}")
        except (PanelApiError, KeyError, TypeError) as exc:
            logger.warning('GET /hwid/devices/stats failed: %s', type(exc).__name__)
            body = '⚠️ HWID 统计暂不可用。'
        await send_or_edit_menu(update, context, body,
                                InlineKeyboardMarkup([[InlineKeyboardButton('👥 Top Users', callback_data='admin_hwid_top')],
                                                      [InlineKeyboardButton('🔙 返回', callback_data='back_home')]]), parse_mode=None)
        return
    if data == 'admin_hwid_top':
        try:
            payload = await v38_api.get_top_users_by_hwid_devices(PANEL_URL, get_headers(), size=10,
                                                                    verify_tls=PANEL_VERIFY_TLS)
            body = top_hwid_users_summary(payload)
        except (PanelApiError, ValueError) as exc:
            logger.warning('GET /hwid/devices/top-users failed: %s', type(exc).__name__)
            body = '⚠️ Top Users 暂不可用。'
        await send_or_edit_menu(update, context, body,
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='admin_hwid_stats')]]), parse_mode=None)
        return
    if data == 'admin_panel_user_tags':
        try:
            tags = await v38_api.get_panel_user_tags(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
            body = fit_message('🏷 面板用户标签\n' + ('\n'.join(str(tag)[:16] for tag in tags[:50]) if tags else '暂无标签'))
        except PanelApiError as exc:
            logger.warning('GET /users/tags failed: %s', type(exc).__name__)
            body = '⚠️ 标签暂不可用。'
        await send_or_edit_menu(update, context, body,
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='admin_panel_user_lookup')]]), parse_mode=None)
        return
    if data == 'admin_geocheck_nodes':
        try:
            await show_admin_geocheck_nodes(update, context)
        except PanelApiError as exc:
            logger.warning('GET /nodes for GeoCheck failed: %s', type(exc).__name__)
            await send_or_edit_menu(update, context, '⚠️ 节点列表暂不可用。',
                                    InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='back_home')]]), parse_mode=None)
        return
    if data.startswith('admin_node_'):
        node_uuid = data.removeprefix('admin_node_')
        try:
            nodes = await get_nodes_status()
            node = next((item for item in nodes if item.get('uuid') == node_uuid), None)
            if not node:
                raise ValueError('node not found')
            body = fit_message('\n'.join([
                '🌐 节点详情', f"名称：{str(node['name'])[:80]}",
                f"UUID：{node_uuid}",
                f"状态：{'在线' if node['isConnected'] else '离线'}",
                f"已禁用：{'是' if node['isDisabled'] else '否'}",
                f"最近状态：{str(node.get('lastStatusMessage') or '-')[:180]}",
            ]))
        except (PanelApiError, ValueError, KeyError) as exc:
            logger.warning('node detail failed: %s', type(exc).__name__)
            body = '⚠️ 节点详情暂不可用。'
        await send_or_edit_menu(update, context, body, InlineKeyboardMarkup([
            [InlineKeyboardButton('🩺 GeoCheck', callback_data=f'admin_geo_{node_uuid}')],
            [InlineKeyboardButton('🔙 返回节点', callback_data='admin_geocheck_nodes')],
        ]), parse_mode=None)
        return
    if data.startswith('admin_geo_'):
        node_uuid = data.removeprefix('admin_geo_')
        await send_or_edit_menu(update, context, '🩺 正在检测节点，请稍候…',
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回', callback_data='admin_geocheck_nodes')]]), parse_mode=None)
        try:
            job_id = await v38_api.start_node_geocheck(node_uuid, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
            result = await asyncio.wait_for(poll_node_geocheck(job_id), timeout=75)
            body = geocheck_summary(result) if result else '🩺 GeoCheck 尚未完成，请稍后重新检测。'
        except (PanelApiError, ValueError, asyncio.TimeoutError) as exc:
            logger.warning('GeoCheck node operation failed: %s', type(exc).__name__)
            body = ('当前节点可能不支持 GeoCheck，请检查 Remnawave Node 版本。'
                    if 'HTTP 400' in str(exc) or 'HTTP 404' in str(exc) or 'HTTP 409' in str(exc)
                    else '⚠️ GeoCheck 尚未完成或暂不可用，请稍后重试。')
        await send_or_edit_menu(update, context, body,
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回节点', callback_data=f'admin_node_{node_uuid}')]]), parse_mode=None)
        return
    if data == 'admin_dashboard_nodes':
        try:
            payload = await v38_api.get_system_nodes_metrics(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
            body = node_metrics_summary(payload)
        except PanelApiError as exc:
            logger.warning('GET /system/nodes/metrics failed: %s', type(exc).__name__)
            body = '⚠️ 节点指标暂不可用。'
        await send_or_edit_menu(update, context, body,
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回统计', callback_data='admin_system_dashboard')]]), parse_mode=None)
        return
    if data == 'admin_dashboard_http':
        try:
            payload = await v38_api.get_system_http_stats(PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
            body = http_stats_summary(payload)
        except PanelApiError as exc:
            logger.warning('GET /system/stats/http failed: %s', type(exc).__name__)
            body = '⚠️ HTTP 统计暂不可用。'
        await send_or_edit_menu(update, context, body,
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回统计', callback_data='admin_system_dashboard')]]), parse_mode=None)
        return

    if data.startswith("reply_user_"):
        raw = data.replace("reply_user_", "", 1)
        if "_" in raw:
            uid_part, back_cb = raw.split("_", 1)
        else:
            uid_part, back_cb = raw, "back_home"
        target_uid = int(uid_part)
        await cleanup_admin_reply_prompt(context, query.from_user.id, context.user_data, reason='enter_new_reply_mode')
        context.user_data['reply_to_uid'] = target_uid
        context.user_data['reply_back_cb'] = back_cb
        logger.info("admin enter send-to-user mode: admin=%s target_user=%s back_cb=%s", query.from_user.id, target_uid, back_cb)
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回上一页", callback_data=back_cb)]])
        prompt_msg = await query.message.reply_text(f"✍️ 请输入回复给用户 `{target_uid}` 的内容 (文字/图片)：", parse_mode='Markdown', reply_markup=cancel_kb)
        context.user_data['reply_prompt_message_id'] = prompt_msg.message_id
        return
    if data == "cancel_op":
        context.user_data.clear()
        await start(update, context)
        return
    if data == "admin_panel_config":
        context.user_data.pop('panelcfg_prompt_message_id', None)
        masked = "已配置" if PANEL_TOKEN else "未配置"
        msg = (
            "🔌 **面板对接配置**\n"
            f"面板地址: `{PANEL_URL or '未配置'}`\n"
            f"面板Token: `{masked}`\n"
            f"订阅域名: `{SUB_DOMAIN or '未配置'}`\n"
            f"默认组UUID: `{TARGET_GROUP_UUID or '未配置'}`\n"
            f"TLS校验: `{PANEL_VERIFY_TLS}`\n"
            "\n"
            "首次安装只需机器人信息，面板参数可在这里随时修改。"
        )
        kb = [
            [InlineKeyboardButton("🌐 设置面板地址", callback_data="panelcfg_set_url")],
            [InlineKeyboardButton("🔑 设置面板Token", callback_data="panelcfg_set_token")],
            [InlineKeyboardButton("🔗 设置订阅域名", callback_data="panelcfg_set_subdomain")],
            [InlineKeyboardButton("🧩 设置默认组UUID", callback_data="panelcfg_set_group")],
            [InlineKeyboardButton("🔒 切换TLS校验", callback_data="panelcfg_toggle_tls")],
            [InlineKeyboardButton("🔙 返回", callback_data="back_home")],
        ]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb))
        return
    if data in {"panelcfg_set_url", "panelcfg_set_token", "panelcfg_set_subdomain", "panelcfg_set_group"}:
        mode_map = {
            "panelcfg_set_url": ("panelcfg_input_url", "请输入面板地址（例如 https://panel.com ）"),
            "panelcfg_set_token": ("panelcfg_input_token", "请输入面板 API Token"),
            "panelcfg_set_subdomain": ("panelcfg_input_subdomain", "请输入订阅域名（例如 https://sub.com ）"),
            "panelcfg_set_group": ("panelcfg_input_group", "请输入默认用户组 UUID"),
        }
        key, tip = mode_map[data]
        context.user_data[key] = True
        if query.message:
            context.user_data['panelcfg_prompt_message_id'] = query.message.message_id
        await send_or_edit_menu(update, context, f"✍️ {tip}", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="admin_panel_config")]]))
        return
    if data == "panelcfg_toggle_tls":
        new_val = not PANEL_VERIFY_TLS
        save_runtime_config(panel_verify_tls=new_val)
        schedule_panel_warmup(context)
        append_ops_timeline('配置', '切换TLS校验', f'panel_verify_tls={new_val}', actor=query.from_user.id)
        await query.answer(f"已切换为 {new_val}", show_alert=True)
        await send_or_edit_menu(update, context, "✅ TLS 配置已更新。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_panel_config")]]))
        return
    if data == "admin_template_center":
        builtins = get_builtin_templates()
        rows = db_query("SELECT * FROM ops_templates ORDER BY created_at DESC LIMIT 8")
        kb = [
            [InlineKeyboardButton("⚡ 严格风控模板", callback_data="tpl_apply_tpl_strict"), InlineKeyboardButton("⚖️ 稳定运营模板", callback_data="tpl_apply_tpl_stable")],
            [InlineKeyboardButton("📈 增长推广模板", callback_data="tpl_apply_tpl_growth")],
            [InlineKeyboardButton("💾 保存当前为自定义模板", callback_data="tpl_save_current")],
        ]
        for r in rows:
            it = dict(r)
            kb.append([InlineKeyboardButton(f"📌 应用自定义模板 #{it['id']} {it['name']}", callback_data=f"tpl_apply_saved_{it['id']}")])
        kb.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
        msg = "🧩 **模板中心**\n可将多个运营设置打包为流程模板，一键应用。"
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb))
        return
    if data == "tpl_save_current":
        payload = {
            'settings': {
                'risk_enforce_mode': get_setting_value('risk_enforce_mode', 'enforce'),
                'risk_low_score': get_setting_value('risk_low_score', '80'),
                'risk_high_score': get_setting_value('risk_high_score', '130'),
                'anomaly_interval': get_setting_value('anomaly_interval', '1'),
            }
        }
        save_ops_template('当前运营配置', payload, query.from_user.id)
        await query.answer("✅ 已保存模板", show_alert=True)
        return
    if data.startswith("tpl_apply_"):
        key = data.replace("tpl_apply_", "")
        if key.startswith('saved_'):
            sid = key.replace('saved_', '')
            row = db_query("SELECT * FROM ops_templates WHERE id=?", (sid,), one=True)
            if not row:
                await query.answer("模板不存在", show_alert=True)
                return
            payload = json.loads(dict(row).get('payload_json') or '{}')
            apply_template_payload(payload, actor=query.from_user.id)
            await send_or_edit_menu(
                update,
                context,
                f"✅ 已应用自定义模板：{dict(row).get('name', f'#{sid}')}\n相关参数已写入设置。",
                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回模板中心", callback_data="admin_template_center")], [InlineKeyboardButton("🏠 返回主页", callback_data="back_home")]]),
            )
            return
        builtins = get_builtin_templates()
        tpl = builtins.get(key)
        if not tpl:
            await query.answer("模板不存在", show_alert=True)
            return
        apply_template_payload(tpl, actor=query.from_user.id)
        await send_or_edit_menu(
            update,
            context,
            f"✅ 已应用模板：{tpl.get('name', key)}\n相关参数已写入设置。",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回模板中心", callback_data="admin_template_center")], [InlineKeyboardButton("🏠 返回主页", callback_data="back_home")]]),
        )
        return
    if data == "admin_panel_user_lookup":
        context.user_data['panel_user_lookup_mode'] = True
        tip = (
            "🔎 **面板用户检索/绑定**\n"
            "请输入以下任一格式：\n"
            "- `tg:123456789`（按 Telegram ID）\n"
            "- `username:alice`（按用户名）\n"
            "- `id:123`（面板数值用户 ID）\n"
            "- `short:短UUID`（短 UUID）\n"
            "纯数字按面板用户 ID；Telegram ID 请明确使用 tg:。\n"
            "- `bind:TG_ID:PANEL_ID:本地订阅记录ID`（多条旧记录时精确绑定）。"
        )
        kb = [[InlineKeyboardButton("🏷 查看 Panel Tags", callback_data="admin_panel_user_tags")],
              [InlineKeyboardButton("🔙 取消", callback_data="back_home")]]
        await send_or_edit_menu(update, context, tip, InlineKeyboardMarkup(kb))
        return
    if data.startswith("bind_panel_user_"):
        parts = data.split("_", 4)
        if len(parts) < 5:
            await query.answer("参数错误", show_alert=True)
            return
        _, _, _, tg_text, panel_user_id_text = parts
        try:
            target_tg_id = int(tg_text)
            panel_user_id = int(panel_user_id_text)
        except ValueError:
            await query.answer("用户 ID 格式错误", show_alert=True)
            return
        panel_user = await get_panel_user(panel_user_id)
        if not panel_user or panel_user.get('telegramId') != target_tg_id:
            await query.answer("面板用户的 Telegram ID 不匹配，未绑定", show_alert=True)
            return
        pending = db_query(
            "SELECT id FROM subscriptions WHERE tg_id=? AND user_id IS NULL ORDER BY id", (target_tg_id,)
        )
        if len(pending) > 1:
            await query.answer("存在多条旧订阅，请用 bind:TG_ID:PANEL_ID:本地记录ID 精确绑定", show_alert=True)
            return
        if pending:
            bind_legacy_subscription(DB_FILE, pending[0]['id'], panel_user_id, target_tg_id)
        else:
            ensure_local_subscription_sync(target_tg_id, panel_user)
        await send_or_edit_menu(
            update,
            context,
            f"✅ 绑定完成\nTG ID: `{target_tg_id}`\n面板用户ID: `{panel_user_id}`",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔎 继续检索", callback_data="admin_panel_user_lookup")], [InlineKeyboardButton("🏠 返回主页", callback_data="back_home")]]),
        )
        return
    if data == "admin_system_dashboard":
        try:
            await show_admin_dashboard(update, context)
        except (PanelApiError, ValueError, TypeError) as exc:
            logger.warning('GET /system/stats dashboard failed: %s', type(exc).__name__)
            await send_or_edit_menu(update, context, '⚠️ 统计暂不可用，请检查面板连接与权限。',
                                    InlineKeyboardMarkup([[InlineKeyboardButton('🔄 重试', callback_data='admin_system_dashboard')],
                                                          [InlineKeyboardButton('🔙 返回', callback_data='back_home')]]), parse_mode=None)
        return
    if data == "admin_bulk_jobs":
        rows = db_query("SELECT * FROM bulk_jobs ORDER BY created_at DESC LIMIT 20")
        lines = ["🗂 **批量任务队列（最近20条）**"]
        if not rows:
            lines.append("暂无任务")
        for r in rows:
            it = dict(r)
            ts = datetime.datetime.fromtimestamp(int(it['created_at'])).strftime('%m-%d %H:%M')
            lines.append(f"- #{it['id']} | {it['action']} | {it['status']} | {ts}")
        await send_or_edit_menu(update, context, "\n".join(lines), InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
        return
    if data == "admin_pay_settings":
        usdt_enabled = get_setting_bool("usdt_enabled", False)
        msg = (
            "💳 **收款设置**\n"
            "🧾 人工审核：始终可用（用户提交凭证后人工审核）\n"
            f"🟨 USDT：{'已开启' if usdt_enabled else '已关闭'}\n\n"
            "请选择下方配置项。"
        )
        kb = [
            [InlineKeyboardButton("🟨 USDT 配置", callback_data="admin_pay_usdt_cfg")],
            [InlineKeyboardButton("🧪 支付设置自检", callback_data="admin_pay_self_check")],
            [InlineKeyboardButton("🔙 返回", callback_data="back_home")],
        ]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb))
        return

    if data == "admin_pay_usdt_cfg":
        usdt_enabled = get_setting_bool("usdt_enabled", False)
        usdt_network = (get_setting_value("usdt_network", "TRC20") or "TRC20").strip().upper()
        usdt_address = (get_setting_value("usdt_address", "") or "").strip()
        usdt_qr = "已上传" if get_setting_value("usdt_qr_file_id") else "未上传"
        msg = (
            "🟨 **USDT 配置**\n"
            f"开关：{'开启' if usdt_enabled else '关闭'}\n"
            f"网络：`{usdt_network}`\n"
            f"地址：`{usdt_address or '未设置'}`\n"
            f"收款码图片：{usdt_qr}"
        )
        kb = [
            [InlineKeyboardButton("🔘 切换USDT开关", callback_data="toggle_pay_usdt")],
            [InlineKeyboardButton("🌐 设置USDT网络", callback_data="set_pay_usdt_network")],
            [InlineKeyboardButton("🏦 设置USDT地址", callback_data="set_pay_usdt_address")],
            [InlineKeyboardButton("⬆️ 上传USDT收款码", callback_data="set_payimg_usdt")],
            [InlineKeyboardButton("🔙 返回收款设置", callback_data="admin_pay_settings")],
        ]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb))
        return

    if data == "toggle_pay_usdt":
        next_val = not get_setting_bool("usdt_enabled", False)
        set_setting_value("usdt_enabled", "1" if next_val else "0")
        await query.answer(f"✅ USDT已{'开启' if next_val else '关闭'}", show_alert=True)
        await send_or_edit_menu(update, context, "✅ 已更新USDT开关。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回USDT配置", callback_data="admin_pay_usdt_cfg")]]))
        return

    if data == "set_pay_usdt_network":
        context.user_data['paycfg_input_usdt_network'] = True
        await send_or_edit_menu(update, context, "✍️ 请输入 USDT 网络（例如 TRC20 / ERC20 / BEP20）", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="admin_pay_usdt_cfg")]]))
        return

    if data == "set_pay_usdt_address":
        context.user_data['paycfg_input_usdt_address'] = True
        await send_or_edit_menu(update, context, "✍️ 请输入 USDT 收款地址", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="admin_pay_usdt_cfg")]]))
        return

    if data == "admin_pay_self_check":
        usdt_enabled = get_setting_bool("usdt_enabled", False)
        usdt_address_ready = bool((get_setting_value("usdt_address", "") or '').strip())

        checks = [
            ("人工审核路径", "✅ 可用（无需额外配置）"),
            ("USDT 开关", "✅ 开启" if usdt_enabled else "⚠️ 关闭"),
            ("USDT 地址", "✅ 已设置" if usdt_address_ready else "⚠️ 未设置"),
        ]

        lines = ["🧪 **支付设置自检报告**", ""]
        for name, result in checks:
            lines.append(f"• {name}：{result}")

        suggestions = []
        if usdt_enabled and not usdt_address_ready:
            suggestions.append("USDT 已开启，但未设置收款地址。")
        if not usdt_enabled:
            suggestions.append("USDT 未开启；客户端将仅显示人工审核路径。")

        if suggestions:
            lines.extend(["", "🔧 建议修复："])
            lines.extend([f"- {x}" for x in suggestions])
        else:
            lines.extend(["", "🎉 配置检查通过，支付流程可正常使用。"])

        kb = [
            [InlineKeyboardButton("🔙 返回收款设置", callback_data="admin_pay_settings")],
            [InlineKeyboardButton("🏠 返回主页", callback_data="back_home")],
        ]
        await send_or_edit_menu(update, context, "\n".join(lines), InlineKeyboardMarkup(kb))
        return

    if data == "set_payimg_usdt":
        context.user_data['set_payimg'] = 'usdt'
        back_cb = "admin_pay_usdt_cfg"
        await send_or_edit_menu(update, context, "📷 请发送收款二维码图片（可发送照片或图片文件）", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data=back_cb)]]))
        return

    if data == "admin_broadcast_start":
        context.user_data['broadcast_mode'] = True
        await send_or_edit_menu(update, context, "📢 **群发通知模式**\n请发送要广播的内容（文字/图片/文件）。\n发送后将自动群发给所有用户。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="cancel_op")]]))
        return
    if data == "admin_subscription_settings":
        settings_payload = await get_subscription_settings()
        preview = json.dumps(settings_payload, ensure_ascii=False, indent=2)[:1200] if settings_payload else '{}'
        history = get_json_setting('subscription_settings_history', [])
        latest_ts = history[-1]['ts'] if isinstance(history, list) and history else None
        latest_text = datetime.datetime.fromtimestamp(latest_ts).strftime('%m-%d %H:%M') if latest_ts else '暂无'
        msg = (
            "⚙️ **订阅设置（可视化）**\n"
            "当前配置（截断显示）：\n"
            "```json\n"
            f"{preview}\n"
            "```\n\n"
            f"最近回滚点：`{latest_text}`\n"
            "仅可提交 Remnawave 3.4.4 契约定义的字段。"
        )
        kb = [
            [InlineKeyboardButton("✍️ 修改订阅设置(JSON)", callback_data="admin_subscription_settings_edit")],
            [InlineKeyboardButton("💾 保存回滚点", callback_data="admin_subsettings_snapshot"), InlineKeyboardButton("↩️ 回滚最近一次", callback_data="admin_subsettings_rollback")],
            [InlineKeyboardButton("🔙 返回", callback_data="back_home")],
        ]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb))
        return
    if data == "admin_subsettings_snapshot":
        payload = await get_subscription_settings()
        push_subscription_settings_snapshot(payload, source='手动保存')
        append_ops_timeline('配置', '订阅设置保存回滚点', '管理员保存当前订阅设置快照', actor=query.from_user.id)
        await query.answer("✅ 已保存回滚点", show_alert=True)
        await send_or_edit_menu(update, context, "✅ 已保存当前订阅设置为回滚点。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_subscription_settings")]]))
        return
    if data == "admin_subsettings_rollback":
        history = get_json_setting('subscription_settings_history', [])
        snap = history[-1] if history else None
        if not snap:
            await query.answer("⚠️ 暂无可回滚快照", show_alert=True)
            return
        payload = subscription_settings_patch_from_current(snap.get('payload'))
        resp = await patch_subscription_settings(payload)
        if resp and resp.status_code == 200:
            pop_subscription_settings_snapshot()
            append_ops_timeline('配置', '订阅设置回滚', f"来源={snap.get('source', '-')}", actor=query.from_user.id)
            await query.answer("✅ 回滚成功", show_alert=True)
            await send_or_edit_menu(update, context, "✅ 已按最近回滚点恢复设置。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_subscription_settings")]]))
        else:
            await query.answer("❌ 回滚失败", show_alert=True)
        return
    if data == "admin_subscription_settings_edit":
        context.user_data['edit_subscription_settings'] = True
        await send_or_edit_menu(update, context, "✍️ 请发送含 uuid 与正式字段的 PATCH JSON（如 randomizeHosts）。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="cancel_op")]]))
        return
    if data == "admin_squads_menu":
        squads = await get_internal_squads()
        summary, suggestion = await build_squad_capacity_summary()
        kb = []
        for s in squads[:20]:
            suuid = s.get('uuid') or ''
            sname = s.get('name') or suuid[:8]
            kb.append([InlineKeyboardButton(f"🧩 {sname}", callback_data=f"admin_squad_{suuid}")])
        if suggestion and suggestion['from'] != '未分组' and suggestion['to'] != '未分组':
            kb.append([InlineKeyboardButton("🚚 一键迁移建议", callback_data=f"admin_squad_suggest_{suggestion['from']}__{suggestion['to']}__{suggestion['count']}")])
        kb.append([InlineKeyboardButton("🚚 批量迁移到分组", callback_data="admin_squad_bulk_move")])
        kb.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
        await send_or_edit_menu(update, context, f"🧩 **用户分组（内部组）**\n{summary}", InlineKeyboardMarkup(kb))
        return
    if data.startswith("admin_squad_suggest_"):
        parts = data.replace("admin_squad_suggest_", "").split("__")
        if len(parts) != 3:
            await query.answer("建议参数错误", show_alert=True)
            return
        from_squad, to_squad, cnt_text = parts
        try:
            move_n = max(1, min(int(cnt_text), 20))
        except ValueError:
            move_n = 5
        rows = db_query("SELECT user_id FROM subscriptions WHERE user_id IS NOT NULL ORDER BY id DESC LIMIT 120")
        pool = [dict(r)['user_id'] for r in rows]
        infos = await asyncio.gather(*[get_panel_user(u) for u in pool])
        candidates = []
        for uid, info in zip(pool, infos):
            if not isinstance(info, dict):
                continue
            squad_ids = {item['uuid'] for item in info['activeInternalSquads']}
            if from_squad in squad_ids:
                candidates.append(uid)
            if len(candidates) >= move_n:
                break
        if not candidates:
            await query.answer("暂无可迁移候选用户", show_alert=True)
            return
        resp = await bulk_move_users_to_squad(candidates, to_squad)
        if resp.status_code == 204:
            append_ops_timeline('分组', '执行迁移建议', f'from={from_squad},to={to_squad},count={len(candidates)}', actor=query.from_user.id)
            await query.answer(f"✅ 已迁移 {len(candidates)} 人", show_alert=True)
        else:
            await query.answer("❌ 迁移失败", show_alert=True)
        return
    if data == "admin_squad_bulk_move":
        context.user_data['squad_bulk_move'] = True
        await send_or_edit_menu(update, context, "✍️ 请按以下格式发送：\n第一行：目标内部组 UUID\n后续行：面板数值用户 ID 列表", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="admin_squads_menu")]]))
        return
    if data.startswith("admin_squad_"):
        squad_uuid = data.replace("admin_squad_", "")
        nodes, node_err = await get_internal_squad_accessible_nodes_verbose(squad_uuid)
        lines = ["🧩 **分组详情**", f"UUID: `{squad_uuid}`", "", "可访问节点："]
        if not nodes and node_err:
            reason_map = {
                'config_missing': "面板地址或 Token 未配置。",
                'auth_unauthorized': "面板鉴权失败（401），请检查 Token。",
                'auth_forbidden': "当前 Token 无权限访问该接口（403）。",
                'endpoint_or_squad_not_found': "接口或分组不存在（404）。",
                'network_error': "请求失败，请检查面板连通性。",
                'empty_payload': "接口返回为空。",
            }
            lines.append(f"- ⚠️ 无法加载节点：{reason_map.get(node_err, node_err)}")
        elif not nodes:
            lines.append("- 暂无可访问节点")
        else:
            for n in nodes[:20]:
                node_name = n['nodeName']
                lines.append(f"- {node_name}")
        kb = [[InlineKeyboardButton("🔙 返回分组", callback_data="admin_squads_menu")]]
        await send_or_edit_menu(update, context, "\n".join(lines), InlineKeyboardMarkup(kb))
        return
    if data == "admin_bandwidth_dashboard":
        nodes_rt = await get_bandwidth_nodes_usage()
        top = []
        for it in nodes_rt[:5]:
            name = it['name']
            val = it['total']
            top.append((name, int(val) if isinstance(val, (int, float)) else 0))
        top.sort(key=lambda x: x[1], reverse=True)
        lines = ["📈 **带宽看板（近 7 日）**", "TOP节点："]
        if not top:
            lines.append("- 暂无数据")
        for name, val in top:
            lines.append(f"- {name}: {round(val / 1024**3, 2)} GB")
        top_users = await build_top_users_traffic()
        lines.append("\nTOP用户流量：")
        if not top_users:
            lines.append("- 暂无")
        for tg_id, uid, used in top_users:
            lines.append(f"- 用户`{tg_id}` / `{uid[:8]}`: {round(used / 1024**3, 2)} GB")
        stats = await get_subscription_history_stats()
        hourly = stats.get('hourlyRequestStats') if isinstance(stats, dict) else []
        recent = int(hourly[-1].get('requestCount', 0)) if hourly else 0
        lines.append(f"\n最近1小时请求数：`{recent}`")
        await send_or_edit_menu(update, context, "\n".join(lines), InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
        return
    if data == "admin_risk_policy":
        low = get_setting_value('risk_low_score', '80')
        high = get_setting_value('risk_high_score', '130')
        unfreeze_hours = get_setting_value('risk_auto_unfreeze_hours', '12')
        watchlist = sorted(list(get_risk_watchlist()))[:8]
        watch_preview = '、'.join(x[:8] for x in watchlist) if watchlist else '暂无'
        msg = (
            "🛡️ **风控策略（多级）**\n"
            f"低风险阈值: {low}\n"
            f"高风险阈值: {high}\n"
            f"自动解封时长(小时): {unfreeze_hours}\n"
            f"执行模式: {get_setting_value('risk_enforce_mode', 'enforce')}\n"
            f"观察名单(预览): {watch_preview}\n\n"
            "请通过下方按钮进入修改流程。"
        )
        kb = [
            [InlineKeyboardButton("✍️ 修改阈值", callback_data="admin_risk_policy_edit")],
            [InlineKeyboardButton("⏱ 设置自动解封时长", callback_data="admin_risk_unfreeze_edit")],
            [InlineKeyboardButton("👀 查看观察名单", callback_data="admin_risk_watchlist")],
            [InlineKeyboardButton("🧪 切换灰度模式", callback_data="admin_risk_mode_cycle")],
            [InlineKeyboardButton("🔙 返回", callback_data="back_home")],
        ]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb))
        return
    if data == "admin_risk_policy_edit":
        context.user_data['edit_risk_policy'] = True
        await send_or_edit_menu(update, context, "✍️ 请发送：低阈值,高阈值（例如 80,130）", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="admin_risk_policy")]]))
        return
    if data == "admin_risk_unfreeze_edit":
        context.user_data['edit_risk_unfreeze_hours'] = True
        await send_or_edit_menu(update, context, "⏱ 请输入自动解封时长（小时，整数，例如 12）", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="admin_risk_policy")]]))
        return
    if data == "admin_risk_watchlist":
        watchlist = sorted(list(get_risk_watchlist()))
        lines = ["👀 **观察名单**"]
        if not watchlist:
            lines.append("暂无记录")
        else:
            for uid in watchlist[:30]:
                lines.append(f"- `{uid}`")
        kb = [[InlineKeyboardButton("🧹 清空观察名单", callback_data="admin_risk_watchlist_clear")], [InlineKeyboardButton("🔙 返回", callback_data="admin_risk_policy")]]
        await send_or_edit_menu(update, context, "\n".join(lines), InlineKeyboardMarkup(kb))
        return
    if data == "admin_risk_watchlist_clear":
        set_risk_watchlist(set())
        append_ops_timeline('风控', '清空观察名单', '管理员手动清空', actor=query.from_user.id)
        await query.answer("✅ 已清空", show_alert=True)
        await send_or_edit_menu(update, context, "✅ 观察名单已清空。", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_risk_policy")]]))
        return
    if data == "admin_risk_mode_cycle":
        curr = get_setting_value('risk_enforce_mode', 'enforce')
        nxt = {'enforce': 'gray', 'gray': 'observe', 'observe': 'enforce'}.get(curr, 'enforce')
        set_setting_value('risk_enforce_mode', nxt)
        append_ops_timeline('风控', '切换执行模式', f'{curr}->{nxt}', actor=query.from_user.id)
        await query.answer(f"已切换: {nxt}", show_alert=True)
        await send_or_edit_menu(update, context, f"✅ 风控执行模式已切换为 {nxt}", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_risk_policy")]]))
        return
    if data == "admin_risk_audit":
        rows = db_query("SELECT * FROM anomaly_events ORDER BY created_at DESC LIMIT 20")
        lines = ["🧾 **风控回溯（最近20条）**"]
        if not rows:
            lines.append("暂无记录")
        for r in rows:
            it = dict(r)
            ts = datetime.datetime.fromtimestamp(int(it['created_at'])).strftime('%m-%d %H:%M')
            user_label = str(it['user_id']) if it['user_id'] is not None else f"旧 {it['user_uuid'][:8]}"
            lines.append(f"- {ts} | {it['risk_level']} | {user_label} | 分数{it['risk_score']} | 动作:{it['action_taken']}")
        await send_or_edit_menu(update, context, "\n".join(lines), InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
        return
    if data == "admin_ops_timeline":
        lines = ["🕒 **操作时间线（订单+风控+配置）**"]
        events = []
        order_logs = db_query("SELECT order_id, action, actor_id, detail, created_at FROM order_audit_logs ORDER BY created_at DESC LIMIT 15")
        for r in order_logs:
            it = dict(r)
            events.append((int(it['created_at']), f"订单 | {it['action']} | {it['order_id']} | {it.get('detail') or '-'}"))
        risk_logs = db_query("SELECT user_uuid, user_id, risk_level, risk_score, action_taken, created_at FROM anomaly_events ORDER BY created_at DESC LIMIT 15")
        for r in risk_logs:
            it = dict(r)
            user_label = str(it['user_id']) if it['user_id'] is not None else f"旧 {it['user_uuid'][:8]}"
            events.append((int(it['created_at']), f"风控 | {it['risk_level']} | {user_label} | {it['action_taken']}"))
        for item in get_json_setting('ops_timeline', [])[-20:]:
            events.append((int(item.get('ts', 0)), f"{item.get('type','系统')} | {item.get('title','-')} | {item.get('detail','-')}"))
        events.sort(key=lambda x: x[0], reverse=True)
        if not events:
            lines.append('暂无记录')
        for ts, text_line in events[:25]:
            ts_text = datetime.datetime.fromtimestamp(ts).strftime('%m-%d %H:%M') if ts else '--'
            lines.append(f"- {ts_text} | {text_line[:120]}")
        await send_or_edit_menu(update, context, "\n".join(lines), InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
        return
    if data == "admin_bulk_menu":
        msg = """📚 **批量用户操作**

请选择操作类型：
- 批量重置流量
- 批量禁用
- 批量删除
- 批量改到期日
- 批量改流量包
- 批量续期 / 批量撤销订阅"""
        kb = [
            [InlineKeyboardButton("🔄 批量重置流量", callback_data="bulk_reset")],
            [InlineKeyboardButton("⛔ 批量禁用", callback_data="bulk_disable")],
            [InlineKeyboardButton("🗑 批量删除", callback_data="bulk_delete")],
            [InlineKeyboardButton("📅 批量改到期日", callback_data="bulk_expire")],
            [InlineKeyboardButton("📡 批量改流量包", callback_data="bulk_traffic")],
            [InlineKeyboardButton("⏳ 批量续期", callback_data="bulk_extend")],
            [InlineKeyboardButton("🔐 批量撤销订阅", callback_data="bulk_revoke")],
            [InlineKeyboardButton("🔙 返回", callback_data="back_home")],
        ]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb))
        return
    if data in {"bulk_reset", "bulk_disable", "bulk_delete", "bulk_revoke"}:
        context.user_data.pop('bulk_pending', None)
        context.user_data['bulk_action'] = data.replace('bulk_', '')
        tip = "每行一个面板数值用户 ID，或使用空格/逗号分隔。"
        await send_or_edit_menu(update, context, f"✍️ 请输入面板用户 ID 列表\n{tip}", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="admin_bulk_menu")]]))
        return
    if data == 'bulk_extend':
        context.user_data.pop('bulk_pending', None)
        context.user_data['bulk_action'] = 'extend'
        await send_or_edit_menu(update, context, '✍️ 第一行输入续期天数（1–9999），后续输入选定的面板数值用户 ID。',
                                InlineKeyboardMarkup([[InlineKeyboardButton('🔙 取消', callback_data='admin_bulk_menu')]]), parse_mode=None)
        return
    if data == "bulk_expire":
        context.user_data['bulk_action'] = 'expire'
        tip = "第一行输入天数（例如 30），从第二行开始输入面板用户 ID 列表。"
        await send_or_edit_menu(update, context, f"✍️ 批量改到期日\n{tip}", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="admin_bulk_menu")]]))
        return
    if data == "bulk_traffic":
        context.user_data['bulk_action'] = 'traffic'
        tip = "第一行输入流量GB（例如 200），从第二行开始输入面板用户 ID 列表。"
        await send_or_edit_menu(update, context, f"✍️ 批量改流量包\n{tip}", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="admin_bulk_menu")]]))
        return
    if data == "admin_orders_menu":
        await show_orders_menu(update, context)
        return
    if data.startswith("admin_orders_status_"):
        status_filter = data.replace("admin_orders_status_", "")
        await show_orders_menu(update, context, status_filter=status_filter)
        return
    if data.startswith("admin_orders_page_"):
        _, _, _, status_raw, page_raw = data.split("_", 4)
        status_filter = None if status_raw == 'all' else status_raw
        try:
            page = int(page_raw)
        except ValueError:
            page = 0
        await show_orders_menu(update, context, status_filter=status_filter, page=page)
        return
    if data.startswith("admin_order_"):
        order_id = data.replace("admin_order_", "")
        order = db_query("SELECT * FROM orders WHERE order_id = ?", (order_id,), one=True)
        if not order:
            await send_or_edit_menu(update, context, "⚠️ 订单不存在", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_orders_menu")]]))
            return
        item = dict(order)
        logs = db_query("SELECT * FROM order_audit_logs WHERE order_id=? ORDER BY created_at DESC LIMIT 5", (item['order_id'],))
        txt = format_order_detail(item, [dict(x) for x in logs])
        kb = [[InlineKeyboardButton("🔙 返回", callback_data="admin_orders_menu")]]
        if item.get('status') == STATUS_FAILED:
            kb.insert(0, [InlineKeyboardButton("♻️ 重试发货", callback_data=f"rt_{item['order_id']}")])
        await send_or_edit_menu(update, context, txt, InlineKeyboardMarkup(kb))
        return
    if data == "anomaly_whitelist_menu":
        await show_anomaly_whitelist_menu(update, context)
        return
    if data == "anomaly_whitelist_add":
        context.user_data['add_anomaly_whitelist'] = True
        await send_or_edit_menu(update, context, "✍️ 请输入要加入白名单的面板数值用户 ID", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="anomaly_whitelist_menu")]]))
        return
    if data.startswith("anomaly_whitelist_del_"):
        row_id = int(data.replace("anomaly_whitelist_del_", ""))
        db_execute("DELETE FROM anomaly_whitelist WHERE id = ?", (row_id,))
        await show_anomaly_whitelist_menu(update, context)
        return
    if data.startswith("anomaly_quick_whitelist_"):
        uid = int(data.replace("anomaly_quick_whitelist_", ""))
        db_execute("INSERT OR IGNORE INTO anomaly_whitelist (user_id, created_at) VALUES (?, ?)", (uid, int(time.time())))
        await query.answer("✅ 已加入白名单", show_alert=False)
        return
    if data.startswith("anomaly_quick_enable_"):
        uid = int(data.replace("anomaly_quick_enable_", ""))
        await enable_panel_user(uid)
        await query.answer("✅ 已尝试解封该用户", show_alert=False)
        return
    if data == "admin_plans_list":
        await show_plans_menu(update, context)
    elif data.startswith("plan_detail_"):
        key = data.split("_")[2]
        p = db_query("SELECT * FROM plans WHERE key = ?", (key,), one=True)
        if not p: return
        try:
            p_dict = dict(p)
            strategy = p_dict.get('reset_strategy', 'NO_RESET')
            s_text = get_strategy_label(strategy)
        except Exception as exc:
            logger.warning("failed to read plan strategy for %s: %s", key, exc)
            s_text = '总流量'
        msg = f"📦 **套餐详情**\n\n🏷 名称：`{p_dict['name']}`\n💰 人民币：`{p_dict['price']}`\n🪙 USDT：`{(p_dict.get('usdt_price') or '未设置')} USDT`\n⏳ 时长：`{p_dict['days']} 天`\n📡 流量：`{p_dict['gb']} GB`\n🔄 策略：`{s_text}`"
        keyboard = [[InlineKeyboardButton("🗑 删除此套餐", callback_data=f"del_plan_{key}")], [InlineKeyboardButton("🔙 返回列表", callback_data="admin_plans_list")]]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(keyboard))
    elif data.startswith("del_plan_"):
        key = data.split("_")[2]
        db_execute("DELETE FROM plans WHERE key = ?", (key,))
        await query.answer("✅ 套餐已删除", show_alert=True)
        await show_plans_menu(update, context)
    elif data == "admin_users_list":
        users = db_query("SELECT DISTINCT tg_id, MAX(created_at) as created_at FROM subscriptions GROUP BY tg_id ORDER BY created_at DESC LIMIT 20")
        keyboard = []
        for u in users:
            u_dict = dict(u)
            ts = u_dict['created_at']
            date_str = datetime.datetime.fromtimestamp(int(ts)).strftime('%m-%d')
            btn_text = f"🆔 {u_dict['tg_id']} | {date_str}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"list_user_subs_{u_dict['tg_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
        await send_or_edit_menu(update, context, "👥 **用户管理 (最近20名)**\n点击ID查看其名下订阅：", InlineKeyboardMarkup(keyboard))
        
    elif data.startswith("list_user_subs_"):
        target_uid = int(data.split("_")[3])
        subs = db_query("SELECT * FROM subscriptions WHERE tg_id = ?", (target_uid,))
        keyboard = []
        for s in subs:
            s_dict = dict(s)
            if s_dict['user_id'] is None:
                keyboard.append([InlineKeyboardButton(f"⚠️ 本地记录 #{s_dict['id']} 待迁移", callback_data="admin_panel_user_lookup")])
            else:
                keyboard.append([InlineKeyboardButton(f"面板ID: {s_dict['user_id']}（本地 #{s_dict['id']}）", callback_data=f"manage_user_{s_dict['user_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 返回列表", callback_data="admin_users_list")])
        await send_or_edit_menu(update, context, f"👤 用户 `{target_uid}` 的订阅列表：", InlineKeyboardMarkup(keyboard))

    elif data.startswith("manage_user_"):
        target_user_id = int(data.replace("manage_user_", ""))
        sub = db_query("SELECT * FROM subscriptions WHERE user_id = ?", (target_user_id,), one=True)
        if not sub:
            await send_or_edit_menu(update, context, "⚠️ 记录不存在", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_users_list")]]))
            return
        panel_info = await get_panel_user(target_user_id)
        status = "🟢 面板正常" if panel_info else "🔴 面板已删"
        user_nodes = await get_user_accessible_nodes(target_user_id)
        node_lines = ["可访问节点："]
        if user_nodes:
            for n in user_nodes[:10]:
                node_name = n['nodeName']
                node_lines.append(f"- {node_name}")
        else:
            node_lines.append("- 暂无可访问节点")
        tag = (panel_info or {}).get('tag') or '-'
        msg = (f"👤 **用户详情**\nTG ID: `{dict(sub)['tg_id']}`\n状态: {status}\n面板用户ID: `{target_user_id}`\nPanel tag: `{tag}`\n\n" + "\n".join(node_lines))
        keyboard = [
            [InlineKeyboardButton("📱 设备管理", callback_data=f"admin_devices_{target_user_id}")],
            [InlineKeyboardButton("🔐 重置订阅", callback_data=f"admin_revoke_{target_user_id}")],
            [InlineKeyboardButton("🔄 重置流量", callback_data=f"reset_traffic_{target_user_id}")],
            [InlineKeyboardButton("📜 最近请求记录", callback_data=f"user_reqhist_{target_user_id}")],
            [InlineKeyboardButton("🗑 确认删除用户", callback_data=f"confirm_del_user_{target_user_id}")],
            [InlineKeyboardButton("🔙 返回列表", callback_data=f"list_user_subs_{dict(sub)['tg_id']}")],
        ]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(keyboard))
    elif data.startswith("user_reqhist_"):
        target_user_id = int(data.replace("user_reqhist_", ""))
        sub = db_query("SELECT * FROM subscriptions WHERE user_id = ?", (target_user_id,), one=True)
        history = await get_user_subscription_history(target_user_id)
        records = history['records']
        total = history['total']
        lines = [f"📜 **请求记录（最近{len(records)}条）**", f"面板用户ID: `{target_user_id}`"]
        lines.append(f"总记录数: `{total}`")
        lines.append("")
        if not records:
            lines.append("暂无请求记录")
        else:
            for rec in records[:10]:
                req_at = format_time(rec.get('requestAt'))
                req_ip = rec.get('requestIp') or '未知IP'
                ua = (rec.get('userAgent') or '未知UA')[:40]
                lines.append(f"• `{req_at}` | `{req_ip}` | `{ua}`")
        back_tg = dict(sub)['tg_id'] if sub else ADMIN_ID
        kb = [[InlineKeyboardButton("🔙 返回用户", callback_data=f"manage_user_{target_user_id}")], [InlineKeyboardButton("🔙 返回列表", callback_data=f"list_user_subs_{back_tg}")]]
        await send_or_edit_menu(update, context, "\n".join(lines), InlineKeyboardMarkup(kb))
    elif data.startswith("reset_traffic_"):
        target_user_id = int(data.replace("reset_traffic_", ""))
        resp = await reset_panel_user_traffic(target_user_id)
        if resp.status_code == 200: await query.answer("✅ 流量已重置", show_alert=True)
        else: await query.answer("❌ 操作失败", show_alert=True)
    elif data.startswith("confirm_del_user_"):
        target_user_id = int(data.replace("confirm_del_user_", ""))
        resp = await delete_panel_user(target_user_id)
        if resp.status_code == 204:
            db_execute("DELETE FROM subscriptions WHERE user_id = ?", (target_user_id,))
            await query.answer("✅ 用户已删除", show_alert=True)
        else:
            await query.answer(f"❌ 面板删除失败（HTTP {resp.status_code}），本地绑定已保留", show_alert=True)
        await show_users_list(update, context)
    elif data == "admin_notify":
        try:
            val = db_query("SELECT value FROM settings WHERE key='notify_days'", one=True)
            day = val['value'] if val else 3
        except Exception as exc:
            logger.warning("failed to load notify_days setting: %s", exc)
            day = 3
        kb = [[InlineKeyboardButton("🔙 取消", callback_data="cancel_op")]]
        await send_or_edit_menu(update, context, f"🔔 **提醒设置**\n当前：到期前 {day} 天发送提醒\n\n**⬇️ 请回复新的天数（纯数字）：**", InlineKeyboardMarkup(kb))
        context.user_data['setting_notify'] = True
    elif data == "admin_cleanup":
        try:
            val = db_query("SELECT value FROM settings WHERE key='cleanup_days'", one=True)
            day = val['value'] if val else 7
        except Exception as exc:
            logger.warning("failed to load cleanup_days setting: %s", exc)
            day = 7
        kb = [[InlineKeyboardButton("🔙 取消", callback_data="cancel_op")]]
        await send_or_edit_menu(update, context, f"🗑 **清理设置**\n当前：过期后 {day} 天自动删除\n(过期1天将只禁用)\n\n**⬇️ 请回复新的天数（纯数字）：**", InlineKeyboardMarkup(kb))
        context.user_data['setting_cleanup'] = True
    elif data == "admin_anomaly_menu":
        try:
            val_int = db_query("SELECT value FROM settings WHERE key='anomaly_interval'", one=True)
            interval = val_int['value'] if val_int else 1
            val_thr = db_query("SELECT value FROM settings WHERE key='anomaly_threshold'", one=True)
            threshold = val_thr['value'] if val_thr else 50
        except Exception as exc:
            logger.warning("failed to load anomaly settings: %s", exc)
            interval=1; threshold=50
        stats = await get_subscription_history_stats()
        by_app = stats.get('byParsedApp') if isinstance(stats, dict) else None
        app_top = "暂无"
        if isinstance(by_app, list) and by_app:
            top = sorted(by_app, key=lambda x: x.get('count', 0), reverse=True)[:3]
            app_top = ", ".join(f"{(x.get('app') or 'unknown')}:{int(x.get('count', 0))}" for x in top)
        hourly = stats.get('hourlyRequestStats') if isinstance(stats, dict) else None
        hourly_last = int(hourly[-1].get('requestCount', 0)) if isinstance(hourly, list) and hourly else 0
        msg = (
            f"🛡️ **异常检测设置**\n\n"
            f"⏱️ 检测周期：每 {interval} 小时\n"
            f"🔢 封禁阈值：单周期 > {threshold} 个IP\n"
            f"📊 最近1小时请求量：`{hourly_last}`\n"
            f"📱 TOP客户端：`{app_top}`\n\n"
            "检测支持多级处置：低风险告警入观察名单，中风险限速，高风险禁用。"
        )
        kb = [[InlineKeyboardButton("⏱️ 设置周期", callback_data="set_anomaly_interval"), InlineKeyboardButton("🔢 设置阈值", callback_data="set_anomaly_threshold")],[InlineKeyboardButton("📋 白名单", callback_data="anomaly_whitelist_menu"), InlineKeyboardButton("🛡️ 风控策略", callback_data="admin_risk_policy")],[InlineKeyboardButton("🧾 风控回溯", callback_data="admin_risk_audit")],[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb))
    elif data == "set_anomaly_interval":
        kb = [[InlineKeyboardButton("🔙 取消", callback_data="admin_anomaly_menu")]]
        await send_or_edit_menu(update, context, "⏱️ **请输入检测周期 (小时)**\n例如：0.5 (半小时) 或 1 (一小时)", InlineKeyboardMarkup(kb))
        context.user_data['setting_anomaly_interval'] = True
    elif data == "set_anomaly_threshold":
        kb = [[InlineKeyboardButton("🔙 取消", callback_data="admin_anomaly_menu")]]
        await send_or_edit_menu(update, context, "🔢 **请输入封禁阈值 (IP数量)**\n例如：50", InlineKeyboardMarkup(kb))
        context.user_data['setting_anomaly_threshold'] = True
    elif data.startswith("set_strategy_"):
        strategy = data.replace("set_strategy_", "")
        new_plan = context.user_data['new_plan']
        key = f"p{int(time.time())}"
        db_execute("INSERT INTO plans (key, name, price, usdt_price, days, gb, reset_strategy) VALUES (?, ?, ?, ?, ?, ?, ?)", (key, new_plan['name'], new_plan['price'], new_plan['usdt_price'], new_plan['days'], new_plan['gb'], strategy))
        del context.user_data['add_plan_step']
        strategy_label = get_strategy_label(strategy)
        msg = (
            "✅ 套餐添加成功！\n"
            f"套餐名称：{new_plan['name']}\n"
            f"重置策略：{strategy_label} ({strategy})"
        )
        kb = [
            [InlineKeyboardButton("📦 返回套餐管理", callback_data="admin_plans_list")],
            [InlineKeyboardButton("🏠 返回主菜单", callback_data="back_home")],
        ]
        await send_or_edit_menu(update, context, msg, InlineKeyboardMarkup(kb), parse_mode=None)

async def show_users_list(update, context):
    users = db_query("SELECT DISTINCT tg_id, MAX(created_at) as created_at FROM subscriptions GROUP BY tg_id ORDER BY created_at DESC LIMIT 20")
    keyboard = []
    for u in users:
        u_dict = dict(u)
        ts = u_dict['created_at']
        date_str = datetime.datetime.fromtimestamp(int(ts)).strftime('%m-%d')
        btn_text = f"🆔 {u_dict['tg_id']} | {date_str}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"list_user_subs_{u_dict['tg_id']}")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
    await send_or_edit_menu(update, context, "👥 **用户管理 (最近20名)**\n点击ID查看其名下订阅：", InlineKeyboardMarkup(keyboard))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        logger.debug("skip message update without effective_user or message")
        return
    user_id = update.effective_user.id
    text = update.message.text
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data="cancel_op")]])

    if user_id == ADMIN_ID and context.user_data.get('hwid_limit_user') is not None and text:
        panel_user_id = int(context.user_data['hwid_limit_user'])
        value = text.strip().lower()
        if value == 'null':
            limit = None
        elif value.isdigit() and int(value) <= 9007199254740991:
            limit = int(value)
        else:
            await update.message.reply_text('❌ 请输入 0 或正整数；输入 null 清除限制。')
            return
        try:
            response = await patch_panel_user({'id': panel_user_id, 'hwidDeviceLimit': limit})
            if response.status_code != 200:
                raise PanelApiError(f'Panel returned HTTP {response.status_code}')
        except PanelApiError as exc:
            logger.warning('PATCH /users HWID limit failed: %s', type(exc).__name__)
            await update.message.reply_text('⚠️ 修改失败，请检查面板权限与用户状态。')
            return
        context.user_data.pop('hwid_limit_user', None)
        await update.message.reply_text(f'✅ 设备上限已更新：{limit if limit is not None else "无限制"}',
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔙 返回设备', callback_data=f'admin_devices_{panel_user_id}')]]))
        return

    if user_id == ADMIN_ID and context.user_data.get('set_payimg'):
        pay_type = context.user_data.get('set_payimg')
        file_id = None
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
        elif update.message.document and (update.message.document.mime_type or '').startswith('image/'):
            file_id = update.message.document.file_id
        if not file_id:
            await update.message.reply_text("❌ 请发送图片文件", reply_markup=cancel_kb)
            return
        key_map = {'usdt': 'usdt_qr_file_id'}
        key = key_map.get(pay_type, 'usdt_qr_file_id')
        set_setting_value(key, file_id)
        context.user_data.pop('set_payimg', None)
        label_map = {'usdt': 'USDT'}
        back_map = {'usdt': 'admin_pay_usdt_cfg'}
        label = label_map.get(pay_type, 'USDT')
        await update.message.reply_text(f"✅ 已更新{label}收款码。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=back_map.get(pay_type, 'admin_pay_settings'))]]))
        return

    if user_id == ADMIN_ID and context.user_data.get('paycfg_input_usdt_network') and text:
        set_setting_value('usdt_network', text.strip().upper()[:12])
        context.user_data.pop('paycfg_input_usdt_network', None)
        await update.message.reply_text("✅ USDT 网络已更新。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_pay_usdt_cfg")]]))
        return

    if user_id == ADMIN_ID and context.user_data.get('paycfg_input_usdt_address') and text:
        set_setting_value('usdt_address', text.strip())
        context.user_data.pop('paycfg_input_usdt_address', None)
        await update.message.reply_text("✅ USDT 地址已更新。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_pay_usdt_cfg")]]))
        return

    if user_id == ADMIN_ID and context.user_data.get('panel_user_lookup_mode') and text:
        raw = text.strip()
        pending_bind = context.user_data.pop('pending_legacy_bind', None)
        if pending_bind:
            if raw != '确认绑定':
                await update.message.reply_text("已取消旧订阅绑定。")
                return
            tg_id, panel_id, local_id = pending_bind
            panel_user = await get_panel_user(panel_id)
            if not panel_user or panel_user.get('telegramId') not in (None, tg_id):
                await update.message.reply_text("❌ 面板用户校验发生变化，未绑定。")
                return
            try:
                bind_legacy_subscription(DB_FILE, local_id, panel_id, tg_id)
            except ValueError as exc:
                await update.message.reply_text(f"❌ 绑定失败：{exc}")
                return
            await update.message.reply_text(f"✅ 已绑定本地记录 #{local_id} 到面板用户 ID {panel_id}。")
            return
        lookup_type = "auto"
        lookup_value = raw
        if ":" in raw:
            lookup_type, lookup_value = [x.strip() for x in raw.split(":", 1)]
            lookup_type = lookup_type.lower()
        if lookup_type == "bind":
            parts = lookup_value.split(":")
            if len(parts) != 3 or not all(part.isdigit() for part in parts):
                await update.message.reply_text("❌ 格式：bind:TG_ID:PANEL_ID:本地订阅记录ID")
                return
            tg_id, panel_id, local_id = map(int, parts)
            panel_user = await get_panel_user(panel_id)
            if not panel_user or panel_user.get('telegramId') not in (None, tg_id):
                await update.message.reply_text("❌ 面板用户不存在或 Telegram ID 不匹配，未修改本地数据。")
                return
            if panel_user.get('telegramId') is None:
                context.user_data['pending_legacy_bind'] = (tg_id, panel_id, local_id)
                await update.message.reply_text(
                    f"⚠️ 面板用户 {panel_id}（{panel_user['username']}）没有 Telegram ID。"
                    f"请人工核对本地记录 #{local_id} 后回复“确认绑定”；其他回复将取消。"
                )
                return
            try:
                bind_legacy_subscription(DB_FILE, local_id, panel_id, tg_id)
            except ValueError as exc:
                await update.message.reply_text(f"❌ 绑定失败：{exc}")
                return
            await update.message.reply_text(f"✅ 已绑定本地记录 #{local_id} 到面板用户 ID {panel_id}。")
            return
        panel_user = None
        try:
            if lookup_type in {"tg", "telegram", "telegram_id"}:
                if not lookup_value.isdigit():
                    raise ValueError('Telegram ID must be numeric')
                panel_user = await get_user_by_telegram_id(int(lookup_value))
                lookup_type = "telegramId"
            else:
                if lookup_type in {"id", "user_id"} or (lookup_type == 'auto' and lookup_value.isdigit()):
                    field, value = 'id', int(lookup_value)
                elif lookup_type in {"short", "short_uuid"} or (
                    lookup_type == 'auto' and len(lookup_value) == 16 and lookup_value.isalnum()
                ):
                    field, value = 'shortUuid', lookup_value
                elif lookup_type in {"username", "user", "auto"}:
                    field, value = 'username', lookup_value
                else:
                    raise ValueError('Unsupported lookup prefix')
                resolved = await v38_api.resolve_panel_user(field, value, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
                panel_user = await get_panel_user(resolved['id']) if resolved else None
                lookup_type = field
        except AmbiguousPanelUserError:
            await update.message.reply_text("⚠️ 该 Telegram ID 对应多个面板用户。请使用 id:数值ID 精确检索。")
            return
        except (PanelApiError, ValueError, KeyError) as exc:
            logger.warning('Panel identity lookup failed: %s', type(exc).__name__)
            await update.message.reply_text("⚠️ 面板检索失败，请检查输入、连通性和权限。")
            return

        if not isinstance(panel_user, dict):
            await update.message.reply_text(
                "❌ 未找到面板用户，或当前 Token 无权限访问该检索接口。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔎 继续检索", callback_data="admin_panel_user_lookup")], [InlineKeyboardButton("🏠 返回主页", callback_data="back_home")]]),
            )
            return

        panel_id = panel_user.get('id')
        puser = panel_user.get('username') or '-'
        ptg = panel_user.get('telegramId')
        pstatus = panel_user.get('status') or '-'
        pstrategy = panel_user.get('trafficLimitStrategy') or '-'
        lines = [
            "✅ 检索到面板用户",
            f"检索方式: {lookup_type}",
            f"面板用户ID: {panel_id}",
            f"用户名: {puser}",
            f"Telegram ID: {ptg if ptg is not None else '-'}",
            f"状态: {pstatus}",
            f"重置策略: {pstrategy}",
            f"Panel tag: {panel_user.get('tag') or '-'}",
        ]
        kb = [[InlineKeyboardButton("📱 设备管理", callback_data=f"admin_devices_{panel_id}")],
              [InlineKeyboardButton("🔐 重置订阅", callback_data=f"admin_revoke_{panel_id}")],
              [InlineKeyboardButton("🔎 继续检索", callback_data="admin_panel_user_lookup")],
              [InlineKeyboardButton("🏠 返回主页", callback_data="back_home")]]
        if isinstance(panel_id, int) and isinstance(ptg, int):
            kb.insert(0, [InlineKeyboardButton("🔗 绑定到本地订阅", callback_data=f"bind_panel_user_{ptg}_{panel_id}")])
        await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return

    if user_id == ADMIN_ID and context.user_data.get('broadcast_mode'):
        user_rows = db_query("SELECT DISTINCT tg_id FROM subscriptions")
        order_rows = db_query("SELECT DISTINCT tg_id FROM orders")
        targets = {int(dict(r)['tg_id']) for r in user_rows} | {int(dict(r)['tg_id']) for r in order_rows}
        ok = 0
        fail = 0
        for uid in targets:
            try:
                await context.bot.copy_message(chat_id=uid, from_chat_id=user_id, message_id=update.message.message_id)
                ok += 1
            except Exception:
                fail += 1
        context.user_data.pop('broadcast_mode', None)
        await update.message.reply_text(f"📢 群发完成\n成功: {ok}\n失败: {fail}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回主菜单", callback_data="back_home")]]))
        return
    if user_id == ADMIN_ID and context.user_data.get('panelcfg_input_url') and text:
        save_runtime_config(panel_url=text.strip())
        schedule_panel_warmup(context)
        context.user_data.pop('panelcfg_input_url', None)
        await cleanup_panelcfg_prompt_message(context, user_id)
        await update.message.reply_text("✅ 面板地址已更新", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_panel_config")]]))
        return
    if user_id == ADMIN_ID and context.user_data.get('panelcfg_input_token') and text:
        managed_by_env = os.getenv('PANEL_TOKEN') not in (None, '', 'your_panel_api_token')
        if not managed_by_env:
            save_runtime_config(panel_token=text.strip())
            schedule_panel_warmup(context)
        context.user_data.pop('panelcfg_input_token', None)
        await cleanup_panelcfg_prompt_message(context, user_id)
        try:
            await update.message.delete()
        except Exception:
            logger.warning("Could not delete administrator token input message")
        result = "⚠️ PANEL_TOKEN 由 .env 管理，请在服务器更新后重建容器。" if managed_by_env else "✅ 面板 Token 已更新"
        await context.bot.send_message(user_id, result, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_panel_config")]]))
        return
    if user_id == ADMIN_ID and context.user_data.get('panelcfg_input_subdomain') and text:
        save_runtime_config(sub_domain=text.strip())
        schedule_panel_warmup(context)
        context.user_data.pop('panelcfg_input_subdomain', None)
        await cleanup_panelcfg_prompt_message(context, user_id)
        await update.message.reply_text("✅ 订阅域名已更新", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_panel_config")]]))
        return
    if user_id == ADMIN_ID and context.user_data.get('panelcfg_input_group') and text:
        save_runtime_config(group_uuid=text.strip())
        schedule_panel_warmup(context)
        context.user_data.pop('panelcfg_input_group', None)
        await cleanup_panelcfg_prompt_message(context, user_id)
        await update.message.reply_text("✅ 默认组 UUID 已更新", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_panel_config")]]))
        return
    if user_id == ADMIN_ID and context.user_data.get('edit_subscription_settings') and text:
        try:
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError('必须是JSON对象')
            current = await get_subscription_settings()
            push_subscription_settings_snapshot(current, source='手工JSON变更前自动备份')
            resp = await patch_subscription_settings(payload)
            context.user_data.pop('edit_subscription_settings', None)
            if resp.status_code == 200:
                append_ops_timeline('配置', '手动更新订阅设置', json.dumps(payload, ensure_ascii=False)[:180], actor=user_id)
                await update.message.reply_text("✅ 订阅设置已更新", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_subscription_settings")]]))
            else:
                await update.message.reply_text("❌ 更新失败，请检查字段", reply_markup=cancel_kb)
        except Exception as exc:
            await update.message.reply_text(f"❌ JSON解析或更新失败: {exc}", reply_markup=cancel_kb)
        return

    if user_id == ADMIN_ID and context.user_data.get('squad_bulk_move') and text:
        try:
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            if len(lines) < 2:
                raise ValueError('格式不正确，至少需要内部组UUID和1个面板用户ID')
            squad_uuid = lines[0]
            user_ids = parse_user_ids("\n".join(lines[1:]))
            if not user_ids:
                raise ValueError('未解析到有效面板用户ID')
            resp = await bulk_move_users_to_squad(user_ids, squad_uuid)
            context.user_data.pop('squad_bulk_move', None)
            if resp.status_code == 204:
                await update.message.reply_text(f"✅ 已提交批量迁移，目标{len(user_ids)}个用户", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_squads_menu")]]))
            else:
                await update.message.reply_text("❌ 迁移失败，请检查内部组UUID与面板用户ID", reply_markup=cancel_kb)
        except Exception as exc:
            await update.message.reply_text(f"❌ 迁移失败: {exc}", reply_markup=cancel_kb)
        return

    if user_id == ADMIN_ID and context.user_data.get('edit_risk_policy') and text:
        try:
            low_text, high_text = [x.strip() for x in text.split(',', 1)]
            low = int(low_text)
            high = int(high_text)
            if low <= 0 or high <= low:
                raise ValueError('要求 低阈值>0 且 高阈值>低阈值')
            set_setting_value('risk_low_score', low)
            set_setting_value('risk_high_score', high)
            context.user_data.pop('edit_risk_policy', None)
            await update.message.reply_text(f"✅ 风控策略已更新：低={low} 高={high}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_anomaly_menu")]]))
        except Exception as exc:
            await update.message.reply_text(f"❌ 参数错误: {exc}", reply_markup=cancel_kb)
        return
    if user_id == ADMIN_ID and context.user_data.get('edit_risk_unfreeze_hours') and text:
        try:
            val = int(text.strip())
            if val <= 0:
                raise ValueError('必须大于0')
            set_setting_value('risk_auto_unfreeze_hours', val)
            context.user_data.pop('edit_risk_unfreeze_hours', None)
            append_ops_timeline('风控', '修改自动解封时长', f'hours={val}', actor=user_id)
            await update.message.reply_text(f"✅ 自动解封时长已更新为 {val} 小时", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_risk_policy")]]))
        except Exception as exc:
            await update.message.reply_text(f"❌ 参数错误: {exc}", reply_markup=cancel_kb)
        return
    if user_id == ADMIN_ID and 'reply_to_uid' in context.user_data:
        target_uid = context.user_data['reply_to_uid']
        back_cb = context.user_data.get('reply_back_cb', 'back_home')
        try:
            await context.bot.copy_message(chat_id=target_uid, from_chat_id=user_id, message_id=update.message.message_id)
            set_support_reply_session(context, target_uid, source='admin_direct_reply', admin_id=user_id)
            logger.info("admin message delivered and support context activated: admin=%s target_user=%s", user_id, target_uid)
            await upsert_support_control_message(
                context,
                target_uid,
                "👆 **来自客服/管理员的回复**\n你现在处于客服会话模式，下一条消息将直接发送给客服。",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("✉️ 继续回复客服", callback_data="contact_support")],
                    [InlineKeyboardButton("🚪 结束会话", callback_data="back_home")],
                ]),
            )
            await cleanup_admin_reply_prompt(context, user_id, context.user_data, reason='send_success')
            await update.message.reply_text("✅ 回复已送达，已进入会话状态。")
        except Exception as e:
            await update.message.reply_text(f"❌ 发送失败：{e}")
            admin_done_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回上一页", callback_data=back_cb)]])
            await update.message.reply_text("你可以重试，或返回上一页。", reply_markup=admin_done_kb)
        del context.user_data['reply_to_uid']
        context.user_data.pop('reply_back_cb', None)
        return
    support_ctx = get_support_reply_session(context, user_id)
    if not support_ctx and context.user_data.get('chat_mode') == 'support':
        fallback_ctx = context.user_data.get('support_reply_context') or {}
        source = fallback_ctx.get('source', 'user_initiated')
        set_support_reply_session(context, user_id, source=source)
        support_ctx = get_support_reply_session(context, user_id)
    support_mode_active = bool(support_ctx)
    if support_mode_active:
        source = support_ctx.get('source', 'user_initiated')
        context.user_data['chat_mode'] = 'support'
        context.user_data['support_reply_context'] = {'source': source, 'updated_at': int(time.time())}
        set_support_reply_session(context, user_id, source=source, admin_id=support_ctx.get('admin_id'))
        logger.info("user message routed to support first: user=%s source=%s", user_id, source)
        admin_header = f"📨 <b>新客服消息</b>\n来自：{update.effective_user.mention_html()} ({user_id})\n会话来源：{source}"
        reply_kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ 回复此用户", callback_data=f"reply_user_{user_id}_back_home")]])
        await context.bot.send_message(ADMIN_ID, admin_header, reply_markup=reply_kb, parse_mode='HTML')
        await context.bot.copy_message(chat_id=ADMIN_ID, from_chat_id=user_id, message_id=update.message.message_id)
        await upsert_support_control_message(
            context,
            user_id,
            "✅ 已转发给客服。你可继续发送消息，或点击下方结束会话。",
            InlineKeyboardMarkup([[InlineKeyboardButton("🚪 结束会话", callback_data="back_home")]]),
        )
        return
    if user_id == ADMIN_ID and context.user_data.get('setting_notify') and text:
        if text.isdigit():
            db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('notify_days', ?)", (text,))
            context.user_data['setting_notify'] = False
            await update.message.reply_text(f"✅ 已设置：到期前 {text} 天提醒。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
        else: await update.message.reply_text("❌ 请输入数字", reply_markup=cancel_kb)
        return
    if user_id == ADMIN_ID and context.user_data.get('setting_cleanup') and text:
        if text.isdigit():
            db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cleanup_days', ?)", (text,))
            context.user_data['setting_cleanup'] = False
            await update.message.reply_text(f"✅ 已设置：过期后 {text} 天自动删除。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
        else: await update.message.reply_text("❌ 请输入数字", reply_markup=cancel_kb)
        return
    if user_id == ADMIN_ID and context.user_data.get('setting_anomaly_interval') and text:
        try:
            val = float(text)
            if val <= 0: raise ValueError
            db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('anomaly_interval', ?)", (text,))
            context.user_data['setting_anomaly_interval'] = False
            await reschedule_anomaly_job(context.application, val)
            await update.message.reply_text(f"✅ 周期已更新：每 {val} 小时检测一次。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_anomaly_menu")]]))
        except (ValueError, TypeError):
            await update.message.reply_text("❌ 请输入有效的数字 (例如 0.5 或 1)", reply_markup=cancel_kb)
        return
    if user_id == ADMIN_ID and context.user_data.get('setting_anomaly_threshold') and text:
        if text.isdigit():
            db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('anomaly_threshold', ?)", (text,))
            context.user_data['setting_anomaly_threshold'] = False
            await update.message.reply_text(f"✅ 阈值已更新：> {text} IP 封禁。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_anomaly_menu")]]))
        else: await update.message.reply_text("❌ 请输入整数", reply_markup=cancel_kb)
        return

    if user_id == ADMIN_ID and context.user_data.get('add_anomaly_whitelist') and text:
        value = text.strip()
        if not value.isdigit() or int(value) <= 0:
            await update.message.reply_text("❌ 请输入有效的面板数值用户 ID")
            return
        db_execute("INSERT OR IGNORE INTO anomaly_whitelist (user_id, created_at) VALUES (?, ?)", (int(value), int(time.time())))
        context.user_data['add_anomaly_whitelist'] = False
        await update.message.reply_text("✅ 白名单已添加。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="anomaly_whitelist_menu")]]))
        return
    if user_id == ADMIN_ID and context.user_data.get('bulk_action') and text:
        action = context.user_data.get('bulk_action')
        try:
            pending = context.user_data.get('bulk_pending')
            if pending:
                if text.strip() != '确认执行':
                    context.user_data.pop('bulk_pending', None)
                    context.user_data.pop('bulk_action', None)
                    await update.message.reply_text(
                        '已取消批量执行。',
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_bulk_menu")]]),
                    )
                    return
                user_ids = pending['userIds']
                extra = pending.get('extra')
                enqueue_bulk_job(action, user_ids, extra, user_id)
                context.user_data.pop('bulk_action', None)
                context.user_data.pop('bulk_pending', None)
                await update.message.reply_text(
                    f"✅ 已加入批量任务队列，目标 {len(user_ids)} 个用户。请在任务列表查看结果。",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="admin_bulk_menu")]]),
                )
                return

            if action in {'reset', 'disable', 'delete', 'revoke'}:
                user_ids = parse_user_ids_strict(text) if action == 'revoke' else parse_user_ids(text)
                extra = None
                preview = {'reset': '批量重置流量', 'disable': '批量禁用', 'delete': '批量删除',
                           'revoke': '批量撤销订阅（旧凭据可能失效）'}[action]
            elif action == 'extend':
                days, user_ids = parse_extend_days_and_user_ids(text)
                extra = {'days': days}
                preview = f'批量续期 +{days} 天'
            elif action == 'expire':
                expire_at, user_ids = parse_expire_days_and_user_ids(text)
                extra = {'expireAt': expire_at}
                preview = f"批量改到期时间 -> {expire_at}"
            elif action == 'traffic':
                traffic_bytes, user_ids = parse_traffic_and_user_ids(text)
                extra = {'trafficLimitBytes': traffic_bytes}
                preview = f"批量改流量包 -> {traffic_bytes // (1024**3)}GB"
            else:
                await update.message.reply_text("❌ 未知操作类型", reply_markup=cancel_kb)
                return

            if not user_ids:
                await update.message.reply_text("❌ 未解析到有效面板用户 ID，请检查输入格式", reply_markup=cancel_kb)
                return

            context.user_data['bulk_pending'] = {'userIds': user_ids, 'extra': extra}
            await update.message.reply_text(
                f"🧪 预检查完成\n操作: {preview}\n目标数量: {len(user_ids)}\n\n如确认执行，请回复：确认执行\n回复其他任意内容将取消。",
                reply_markup=cancel_kb,
            )
        except Exception as exc:
            context.user_data.pop('bulk_pending', None)
            await update.message.reply_text(f"❌ 批量操作失败: {exc}", reply_markup=cancel_kb)
        return
    if user_id == ADMIN_ID and 'add_plan_step' in context.user_data and text:
        step = context.user_data['add_plan_step']
        if step == 'name':
            context.user_data['new_plan'] = {'name': text}
            context.user_data['add_plan_step'] = 'price'
            await update.message.reply_text("📝 **步骤 2/6：请输入人民币价格**\n(例如: 200元)", reply_markup=cancel_kb, parse_mode='Markdown')
        elif step == 'price':
            context.user_data['new_plan']['price'] = text
            context.user_data['add_plan_step'] = 'usdt_price'
            await update.message.reply_text("🪙 **步骤 3/6：请输入 USDT 价格**\n(例如: 28)", reply_markup=cancel_kb, parse_mode='Markdown')
        elif step == 'usdt_price':
            if not text or not text.strip():
                return await update.message.reply_text("❌ USDT 价格不能为空", reply_markup=cancel_kb)
            context.user_data['new_plan']['usdt_price'] = text.strip()
            context.user_data['add_plan_step'] = 'days'
            await update.message.reply_text("📅 **步骤 4/6：请输入有效期天数**\n(请输入纯数字，例如: 30)", reply_markup=cancel_kb, parse_mode='Markdown')
        elif step == 'days':
            if not text.isdigit(): return await update.message.reply_text("❌ 请输入数字", reply_markup=cancel_kb)
            context.user_data['new_plan']['days'] = int(text)
            context.user_data['add_plan_step'] = 'gb'
            await update.message.reply_text("📡 **步骤 5/6：请输入流量限制 GB**\n(请输入纯数字，例如: 100)", reply_markup=cancel_kb, parse_mode='Markdown')
        elif step == 'gb':
            if not text.isdigit(): return await update.message.reply_text("❌ 请输入数字", reply_markup=cancel_kb)
            context.user_data['new_plan']['gb'] = int(text)
            keyboard = [[InlineKeyboardButton("🚫 永不重置", callback_data="set_strategy_NO_RESET")], [InlineKeyboardButton("📅 每日重置", callback_data="set_strategy_DAY")], [InlineKeyboardButton("🗓 每周重置", callback_data="set_strategy_WEEK")], [InlineKeyboardButton("🌝 每月重置", callback_data="set_strategy_MONTH")], [InlineKeyboardButton("🌙 按开通日每月重置", callback_data="set_strategy_MONTH_ROLLING")], [InlineKeyboardButton("❌ 取消", callback_data="cancel_op")]]
            await update.message.reply_text("🔄 **步骤 6/6：请选择流量重置策略**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return
    pending_order = get_pending_order_for_user(db_query, user_id)
    if pending_order:
        logger.info("user message routed to pending-order proof: user=%s order=%s", user_id, pending_order.get('order_id'))
        awaiting_order_id = context.user_data.get('awaiting_manual_review_proof_order_id')
        if awaiting_order_id and awaiting_order_id != pending_order['order_id']:
            logger.warning("awaiting order mismatch: user=%s expected=%s actual=%s", user_id, awaiting_order_id, pending_order['order_id'])
        proof = None
        if text and text.strip():
            proof = {'type': 'text', 'text': text.strip()}
        elif update.message.photo:
            proof = {'type': 'photo', 'file_id': update.message.photo[-1].file_id, 'text': (update.message.caption or '').strip()}
        elif update.message.document:
            proof = {'type': 'document', 'file_id': update.message.document.file_id, 'text': (update.message.caption or '').strip()}

        if not proof:
            await update.message.reply_text(
                "⚠️ 检测到你有待支付订单，请发送文字口令、支付截图或支付文件。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消订单", callback_data="cancel_order")]]),
            )
            return

        proof['order_id'] = pending_order['order_id']
        context.user_data['pending_payment_proof'] = proof
        await submit_manual_review_proof(update, context, pending_order, proof)
        return

async def add_plan_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("无管理员权限", show_alert=True)
        return
    await query.answer()
    context.user_data['add_plan_step'] = 'name'
    await query.edit_message_text("📝 **步骤 1/6：开始添加套餐**\n\n请输入套餐名称:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data="cancel_op")]]), parse_mode='Markdown')

async def process_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("无管理员权限", show_alert=True)
        return
    await query.answer()
    data = query.data
    client_return_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回主菜单", callback_data="back_home")]])
    admin_return_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回主菜单", callback_data="back_home")]])
    async def clean_user_waiting_msg(order_record):
        uid = int(order_record.get('tg_id', 0) or 0)
        waiting_message_id = order_record.get('waiting_message_id')
        menu_message_id = order_record.get('menu_message_id')
        if waiting_message_id:
            try:
                await context.bot.delete_message(chat_id=uid, message_id=waiting_message_id)
            except Exception as exc:
                logger.debug("failed to delete waiting message for uid=%s order=%s: %s", uid, order_record.get('order_id'), exc)
        if menu_message_id:
            try:
                await context.bot.delete_message(chat_id=uid, message_id=menu_message_id)
            except Exception as exc:
                logger.debug("failed to delete menu message for uid=%s order=%s: %s", uid, order_record.get('order_id'), exc)
        db_execute(
            "UPDATE orders SET waiting_message_id=NULL, menu_message_id=NULL, updated_at=? WHERE order_id=?",
            (int(time.time()), order_record.get('order_id')),
        )
    if data.startswith("review_"):
        parts = data.split("_")
        if len(parts) >= 5:
            uid = parts[1]
            plan_key = parts[2]
            order_type = parts[3]
            sid = parts[4]
            kb = [
                [InlineKeyboardButton("✅ 通过", callback_data=f"ap_{uid}_{plan_key}_{order_type}_{sid}")],
                [InlineKeyboardButton("❌ 拒绝", callback_data=f"rj_{uid}_{plan_key}_{order_type}_{sid}")]
            ]
            await query.edit_message_text("🧾 已重新进入审核，请选择操作：", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.edit_message_text("⚠️ 订单数据不完整，无法重新审核。", reply_markup=admin_return_btn)
        return
    if data.startswith("rj_"):
        parts = data.split("_")
        order_id = parts[1]
        order = get_order(db_query, order_id)
        if not order:
            await query.edit_message_text("⚠️ 订单不存在", reply_markup=admin_return_btn)
            return
        if order['status'] != STATUS_PENDING:
            await query.edit_message_text("⚠️ 订单已进入处理或终态，不能再拒绝；请先核对状态。", reply_markup=admin_return_btn)
            return
        uid = int(order['tg_id'])
        retry_markup = admin_return_btn
        if len(parts) >= 5:
            retry_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🧾 再次审核", callback_data=f"review_{parts[1]}_{parts[2]}_{parts[3]}_{parts[4]}")],
                [InlineKeyboardButton("🔙 返回主菜单", callback_data="back_home")]
            ])
        update_order_status(db_execute, order_id, [STATUS_PENDING], STATUS_REJECTED, error_message='rejected_by_admin')
        append_order_audit_log(db_execute, order_id, 'reject', query.from_user.id, 'admin_rejected')
        await query.edit_message_text("❌ 已拒绝", reply_markup=retry_markup)
        await clean_user_waiting_msg(order)
        try:
            await context.bot.send_message(uid, "❌ 您的订单已被管理员拒绝。", reply_markup=client_return_btn)
        except Exception as exc:
            logger.warning("failed to send reject notice uid=%s order=%s: %s", uid, order_id, exc)
        return

    if data.startswith("rt_"):
        order_id = data.split("_", 1)[1]
        order = get_order(db_query, order_id)
        if not order:
            await query.edit_message_text("⚠️ 订单不存在", reply_markup=admin_return_btn)
            return
        if order.get('status') != STATUS_FAILED:
            await query.edit_message_text("⚠️ 仅允许重试失败订单", reply_markup=admin_return_btn)
            return
        switched = update_order_status(db_execute, order_id, [STATUS_FAILED], STATUS_PENDING, error_message='retry_by_admin')
        append_order_audit_log(db_execute, order_id, 'retry', query.from_user.id, 'retry_by_admin')
        if not switched:
            await query.edit_message_text("⚠️ 订单状态更新失败，请重试", reply_markup=admin_return_btn)
            return
        sid = "0"
        if order.get('target_user_id'):
            sid = get_short_id(order['target_user_id'])
        data = f"ap_{order_id}_{sid}"

    if not data.startswith("ap_"):
        return

    _, order_id, short_id = data.split("_", 2)
    order = get_order(db_query, order_id)
    if not order:
        await query.edit_message_text("⚠️ 订单不存在或已过期", reply_markup=admin_return_btn)
        return

    if order.get('status') == STATUS_DELIVERED:
        await query.edit_message_text("ℹ️ 该订单已发货（幂等保护）", reply_markup=admin_return_btn)
        return

    if order.get('status') != STATUS_PENDING:
        await query.edit_message_text(f"⚠️ 当前订单状态不可处理: {order.get('status')}", reply_markup=admin_return_btn)
        return

    claimed = update_order_status(db_execute, order_id, [STATUS_PENDING], STATUS_APPROVED)
    if not claimed:
        await query.edit_message_text("⚠️ 订单正在被其他操作处理，请稍后重试", reply_markup=admin_return_btn)
        return

    uid = order['tg_id']
    plan_key = order['plan_key']
    order_type = order['order_type']
    target_user_id = order.get('target_user_id')
    if order_type == 'renew' and not target_user_id:
        update_order_status(db_execute, order_id, [STATUS_APPROVED], STATUS_FAILED,
                            error_message='legacy_subscription_requires_id_migration')
        await query.edit_message_text("⚠️ 旧订阅尚未完成数值用户 ID 迁移，请先联系管理员绑定。", reply_markup=admin_return_btn)
        return

    plan = db_query("SELECT * FROM plans WHERE key = ?", (plan_key,), one=True)
    if not plan:
        update_order_status(db_execute, order_id, [STATUS_APPROVED], STATUS_FAILED, error_message='reason:business_validation|plan_deleted')
        await query.edit_message_text("❌ 套餐已删除", reply_markup=admin_return_btn)
        return

    if not panel_config_ready() or (order_type == 'new' and not TARGET_GROUP_UUID):
        update_order_status(db_execute, order_id, [STATUS_APPROVED], STATUS_FAILED,
                            error_message='panel_configuration_incomplete')
        await query.edit_message_text("⚠️ 面板地址、Token 或新购所需默认内部组尚未配置。", reply_markup=admin_return_btn)
        return

    await query.edit_message_text("🔄 处理中...")
    plan_dict = dict(plan)
    add_traffic = plan_dict['gb'] * 1024 * 1024 * 1024
    add_days = plan_dict['days']
    reset_strategy = plan_dict.get('reset_strategy', 'NO_RESET')
    strategy_label = get_strategy_label(reset_strategy)

    extension_started = False
    try:
        if order_type == 'renew':
            if not target_user_id:
                update_order_status(db_execute, order_id, [STATUS_APPROVED], STATUS_FAILED, error_message='reason:business_validation|missing_target_uuid')
                await query.edit_message_text("⚠️ 订单数据已过期", reply_markup=admin_return_btn)
                return
            if not owned_subscription(uid, int(target_user_id)):
                raise ValueError('续费目标已不属于该 Telegram 用户')
            user_info = await get_panel_user(target_user_id)
            if not user_info:
                update_order_status(db_execute, order_id, [STATUS_APPROVED], STATUS_FAILED, error_message='user_not_found')
                await query.edit_message_text("⚠️ 用户不存在", reply_markup=admin_return_btn)
                return
            if user_info.get('telegramId') not in (None, int(uid)):
                raise ValueError('Panel 用户绑定与续费订单不一致')
            if not isinstance(add_days, int) or add_days < 1:
                raise ValueError('套餐续期天数必须为正整数')
            # Once submitted, a lost response may still mean Panel applied Extend.
            # The approved order is never replayed automatically or by retry UI.
            extension_started = True
            extended_user = await extend_subscription(target_user_id, add_days)
            if not update_order_status(db_execute, order_id, [STATUS_APPROVED], STATUS_EXTENSION_APPLIED,
                                       delivered_user_id=target_user_id):
                raise RuntimeError('Could not persist extension_applied for approved order')
            new_limit = extended_user['trafficLimitBytes']
            if reset_strategy == 'NO_RESET':
                new_limit += add_traffic
            update_payload = {
                "id": target_user_id,
                "trafficLimitBytes": new_limit,
                "trafficLimitStrategy": reset_strategy,
            }
            r = await patch_panel_user(update_payload, retry=False)
            if r.status_code == 200:
                refreshed = await get_panel_user(target_user_id)
                if not refreshed:
                    raise PanelContractError('Panel user disappeared after renewal')
                if not update_order_status(db_execute, order_id, [STATUS_EXTENSION_APPLIED], STATUS_DELIVERED,
                                           delivered_user_id=target_user_id):
                    raise RuntimeError('Could not persist delivered renewal state')
                append_order_audit_log(db_execute, order_id, 'deliver_success', query.from_user.id, 'renew')
                try:
                    await sync_user_metadata(target_user_id, uid, plan_key=plan_key, order_id=order_id)
                except Exception as exc:
                    logger.warning('renewal metadata sync failed for order %s: %s', order_id, type(exc).__name__)
                await query.edit_message_text(f"✅ 续费成功\n用户: {uid}", reply_markup=admin_return_btn)
                await clean_user_waiting_msg(order)
                await send_subscription_card(context, uid, refreshed, target_user_id)
            else:
                raise PanelApiError(f'Panel returned HTTP {r.status_code} after Extend')
        else:
            new_expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=add_days)
            expire_iso = new_expire.strftime("%Y-%m-%dT%H:%M:%SZ")
            username = f"tg_{uid}_{order_id}"
            existing_panel_user = await get_user_by_username(username)
            if existing_panel_user and existing_panel_user.get('telegramId') != int(uid):
                raise PanelApiError("Order username is already assigned to another Telegram user")
            payload = {
                "username": username,
                "status": USER_STATUS_ACTIVE,
                "telegramId": int(uid),
                "trafficLimitBytes": add_traffic,
                "trafficLimitStrategy": reset_strategy,
                "expireAt": expire_iso,
                "activeInternalSquads": [TARGET_GROUP_UUID],
            }
            r = None if existing_panel_user else await create_panel_user(payload)
            if existing_panel_user or (r and r.status_code == 201):
                resp_data = existing_panel_user or extract_payload(r)
                panel_user_id = resp_data.get('id')
                if not isinstance(panel_user_id, int) or panel_user_id <= 0:
                    raise PanelApiError("Create user response is missing numeric id")
                linked = db_query("SELECT id FROM subscriptions WHERE user_id=?", (panel_user_id,), one=True)
                if not linked:
                    db_execute(
                        "INSERT INTO subscriptions (tg_id, user_id, migration_status, created_at, plan_key) VALUES (?, ?, 'resolved', ?, ?)",
                        (uid, panel_user_id, int(time.time()), plan_key),
                    )
                update_order_status(db_execute, order_id, [STATUS_APPROVED], STATUS_DELIVERED, delivered_user_id=panel_user_id)
                append_order_audit_log(db_execute, order_id, 'deliver_success', query.from_user.id, 'new')
                await sync_user_metadata(panel_user_id, uid, plan_key=plan_key, order_id=order_id)
                await query.edit_message_text(f"✅ 开通成功\n用户: {uid}", reply_markup=admin_return_btn)
                sub_url = resp_data['subscriptionUrl']
                display_expire = format_time(expire_iso)
                msg = (
                    f"🎉 *订阅开通成功\\!*\n\n"
                    f"📦 套餐: {escape_markdown_v2(plan_dict['name'])}\n"
                    f"⏳ 到期时间: `{escape_markdown_v2(display_expire)}`\n"
                    f"📡 包含流量: `{escape_markdown_v2(str(plan_dict['gb']))} GB \\({escape_markdown_v2(strategy_label)}\\)`\n\n"
                    f"🔗 订阅链接:\n`{escape_markdown_v2(sub_url)}`"
                )
                await clean_user_waiting_msg(order)
                if sub_url and sub_url.startswith('http'):
                    qr = generate_qr(sub_url)
                    await context.bot.send_photo(uid, photo=qr, caption=msg, parse_mode='MarkdownV2', reply_markup=client_return_btn)
                else:
                    await context.bot.send_message(uid, msg, parse_mode='MarkdownV2', reply_markup=client_return_btn)
            else:
                update_order_status(db_execute, order_id, [STATUS_APPROVED], STATUS_FAILED, error_message='reason:network|panel_api_error_new')
                await query.edit_message_text("❌ 失败", reply_markup=admin_return_btn)
    except Exception as exc:
        logger.exception("Order processing failed for %s", order_id)
        reason = classify_order_failure(str(exc))
        detail = f"reason:{reason}|{str(exc)[:320]}"
        if order_type == 'renew' and extension_started:
            update_order_status(db_execute, order_id, [STATUS_APPROVED, STATUS_EXTENSION_APPLIED],
                                STATUS_UNKNOWN, error_message=detail)
        else:
            update_order_status(db_execute, order_id, [STATUS_APPROVED], STATUS_FAILED, error_message=detail)
        append_order_audit_log(db_execute, order_id, 'deliver_failed', query.from_user.id, detail)
        await query.edit_message_text(
            '⚠️ 续期结果不确定，请人工核对面板，禁止重复审核。' if order_type == 'renew' and extension_started
            else '❌ 订单处理失败，请查看服务日志。', reply_markup=admin_return_btn)

async def process_bulk_jobs_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query(
        "SELECT * FROM bulk_jobs WHERE status IN ('pending','retry') AND next_attempt_at<=? ORDER BY created_at ASC LIMIT 1",
        (int(time.time()),),
    )
    if not rows:
        return
    job = dict(rows[0])
    try:
        payload = json.loads(job['payload_json'])
    except (ValueError, TypeError):
        db_execute(
            "UPDATE bulk_jobs SET status='failed', result_json=?, updated_at=? WHERE id=?",
            (json.dumps({'reason': 'invalid_payload_json'}), int(time.time()), job['id']),
        )
        return
    if not isinstance(payload, dict):
        db_execute(
            "UPDATE bulk_jobs SET status='failed', result_json=?, updated_at=? WHERE id=?",
            (json.dumps({'reason': 'payload_must_be_object'}), int(time.time()), job['id']),
        )
        return
    user_ids = payload.get('userIds')
    if not isinstance(user_ids, list) or not user_ids or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in user_ids
    ):
        db_execute(
            "UPDATE bulk_jobs SET status='migration_required', result_json=?, updated_at=? WHERE id=?",
            (json.dumps({'reason': 'legacy_or_invalid_user_ids'}), int(time.time()), job['id']),
        )
        return
    attempts = int(job['attempts']) + 1
    db_execute("UPDATE bulk_jobs SET status='running', attempts=?, updated_at=? WHERE id=?",
               (attempts, int(time.time()), job['id']))
    try:
        accepted, failed = await api_run_bulk_action(
            job['action'], user_ids, payload.get('extra') or {}, PANEL_URL, get_headers(), PANEL_VERIFY_TLS
        )
    except PanelApiError as exc:
        if job['action'] in {'reset', 'delete', 'extend', 'revoke'}:
            status = 'unknown'
        else:
            status = 'retry' if attempts < 3 else 'failed'
        db_execute(
            "UPDATE bulk_jobs SET status=?, result_json=?, next_attempt_at=?, updated_at=? WHERE id=?",
            (status, json.dumps({'error': str(exc)}), int(time.time()) + 30 * attempts,
             int(time.time()), job['id']),
        )
        logger.warning("bulk job %s %s after transport failure: %s", job['id'], status, exc)
        return
    except (ValueError, KeyError, TypeError) as exc:
        db_execute(
            "UPDATE bulk_jobs SET status='failed', result_json=?, updated_at=? WHERE id=?",
            (json.dumps({'reason': str(exc)}), int(time.time()), job['id']),
        )
        return
    result = {'accepted': accepted, 'failed': failed}
    status = 'submitted' if failed == 0 else (
        'unknown' if job['action'] in {'reset', 'delete', 'extend', 'revoke'} else 'failed'
    )
    db_execute("UPDATE bulk_jobs SET status=?, result_json=?, updated_at=? WHERE id=?",
               (status, json.dumps(result), int(time.time()), job['id']))
    append_ops_timeline('批量', '批量任务已提交', f"job={job['id']},action={job['action']},accepted={accepted},failed={failed}", actor='系统')


async def reconcile_legacy_subscriptions_job(context: ContextTypes.DEFAULT_TYPE):
    """Resolve only one-to-one legacy links using the documented Telegram filter."""
    if not PANEL_URL or not PANEL_TOKEN:
        return
    rows = db_query(
        "SELECT id, tg_id FROM subscriptions WHERE user_id IS NULL ORDER BY tg_id, id"
    )
    grouped = defaultdict(list)
    for row in rows:
        grouped[int(row['tg_id'])].append(int(row['id']))
    for tg_id, local_ids in grouped.items():
        if len(local_ids) != 1:
            db_execute(
                "UPDATE subscriptions SET migration_status='ambiguous' WHERE tg_id=? AND user_id IS NULL",
                (tg_id,),
            )
            continue
        try:
            users = await api_get_users_by_telegram_id(tg_id, PANEL_URL, get_headers(), PANEL_VERIFY_TLS)
            if len(users) == 1 and users[0].get('telegramId') == tg_id:
                bind_legacy_subscription(DB_FILE, local_ids[0], users[0]['id'], tg_id)
            elif len(users) > 1:
                db_execute(
                    "UPDATE subscriptions SET migration_status='ambiguous' WHERE id=?",
                    (local_ids[0],),
                )
        except (PanelApiError, ValueError) as exc:
            logger.warning("legacy subscription %s remains pending: %s", local_ids[0], exc)

async def check_expiry_job(context: ContextTypes.DEFAULT_TYPE):
    try: 
        val = db_query("SELECT value FROM settings WHERE key='notify_days'", one=True)
        notify_days = int(val['value']) if val else 3
        val_clean = db_query("SELECT value FROM settings WHERE key='cleanup_days'", one=True)
        cleanup_days = int(val_clean['value']) if val_clean else 7
    except Exception as exc:
        logger.warning("failed to load expiry job settings: %s", exc)
        notify_days = 3
        cleanup_days = 7
    subs = db_query("SELECT * FROM subscriptions WHERE user_id IS NOT NULL")
    if not subs: return
    now = datetime.datetime.now(datetime.timezone.utc)
    sem = asyncio.Semaphore(10)
    async def check_single_sub(sub):
        async with sem:
            u_dict = dict(sub)
            panel_user_id = u_dict['user_id']
            info = await get_panel_user(panel_user_id)
            if not info:
                logger.warning("Panel user %s is absent; local mapping retained for review",
                               panel_user_id)
                return
            try:
                ex_str = info['expireAt']
                ex_dt = datetime.datetime.fromisoformat(ex_str.replace('Z', '+00:00'))
                days_left = (ex_dt - now).days
                if 0 <= days_left <= notify_days:
                    last_notify_expire = u_dict.get('last_notify_expire_at')
                    last_notify_days_left = u_dict.get('last_notify_days_left')
                    last_notify_at = int(u_dict.get('last_notify_at') or 0)
                    now_ts = int(time.time())
                    can_send_by_daily_limit = should_send_expire_notice(last_notify_at, now_ts)
                    if (str(last_notify_expire or '') != ex_str or int(last_notify_days_left or -999) != days_left) and can_send_by_daily_limit:
                        sid = get_short_id(panel_user_id)
                        kb = [[InlineKeyboardButton("💳 立即续费", callback_data=f"selrenew_{sid}")]]
                        msg = f"⚠️ **续费提醒**\n\n您的订阅 (面板用户ID: `{panel_user_id}`) \n将在 **{days_left}** 天后到期。\n请及时续费以免服务中断。"
                        try:
                            await context.bot.send_message(u_dict['tg_id'], msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
                            db_execute(
                                "UPDATE subscriptions SET last_notify_expire_at = ?, last_notify_days_left = ?, last_notify_at = ? WHERE id = ?",
                                (ex_str, days_left, int(time.time()), u_dict['id']),
                            )
                        except Exception as exc:
                            logger.warning("Failed to send expiry notice to %s: %s", u_dict['tg_id'], exc)
                if days_left < 0 and info['status'] == USER_STATUS_ACTIVE:
                    disabled = await disable_panel_user(panel_user_id)
                    if disabled.status_code != 200:
                        logger.warning("Failed to disable expired Panel user %s: HTTP %s", panel_user_id, disabled.status_code)
                if days_left < -cleanup_days:
                    deleted = await delete_panel_user(panel_user_id)
                    if deleted.status_code == 204:
                        db_execute("DELETE FROM subscriptions WHERE id = ?", (u_dict['id'],))
                        try:
                            await context.bot.send_message(u_dict['tg_id'], f"🗑 您的订阅因过期超过 {cleanup_days} 天已被系统回收。")
                        except Exception as exc:
                            logger.warning("Failed to notify cleanup to %s: %s", u_dict['tg_id'], exc)
                    else:
                        logger.warning("Panel deletion failed for user %s: HTTP %s; local mapping retained",
                                       panel_user_id, deleted.status_code)
            except Exception as e:
                logger.warning("check_single_sub failed for %s: %s", panel_user_id, e)
    tasks = [check_single_sub(sub) for sub in subs]
    await asyncio.gather(*tasks)

async def check_anomalies_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        # 自动解封（中风险限速后，低风险持续一段时间自动恢复）
        auto_hours = int(get_setting_value('risk_auto_unfreeze_hours', '12') or '12')
        candidates = get_json_setting('risk_unfreeze_candidates', {})
        if isinstance(candidates, dict) and candidates:
            now_ts = int(time.time())
            changed = False
            for uid, ts in list(candidates.items()):
                try:
                    added_ts = int(ts)
                except Exception:
                    added_ts = now_ts
                if now_ts - added_ts >= auto_hours * 3600:
                    resp = await patch_panel_user({"id": int(uid), "status": USER_STATUS_ACTIVE})
                    if resp.status_code == 200:
                        changed = True
                        candidates.pop(uid, None)
                        append_ops_timeline('风控', '自动解封', f'uid={uid},after={auto_hours}h', actor='系统', target=uid)
            if changed:
                set_json_setting('risk_unfreeze_candidates', candidates)

        val_thr = db_query("SELECT value FROM settings WHERE key='anomaly_threshold'", one=True)
        limit = int(val_thr['value']) if val_thr else 50
        logs = await get_subscription_request_history()
        if not isinstance(logs, list) or not logs:
            return

        val_scan = db_query("SELECT value FROM settings WHERE key='anomaly_last_scan_ts'", one=True)
        last_scan_ts = int(val_scan['value']) if val_scan else 0
        whitelist_rows = db_query("SELECT user_id FROM anomaly_whitelist WHERE user_id IS NOT NULL")
        whitelist = {int(r['user_id']) for r in whitelist_rows}

        def _extract_log_ts(log):
            value = log['requestAt']
            return int(datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())

        prepared = []
        for row in logs:
            rec = dict(row)
            ts = _extract_log_ts(rec)
            rec['_ts'] = ts
            rec['_fmt_time'] = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%m-%d %H:%M') if ts else '-'
            prepared.append(rec)

        incidents, max_seen_ts = build_anomaly_incidents(prepared, last_scan_ts, whitelist, limit)

        low_score = int(get_setting_value('risk_low_score', '80'))
        high_score = int(get_setting_value('risk_high_score', '130'))
        enforce_mode = get_setting_value('risk_enforce_mode', 'enforce')
        watchlist = get_risk_watchlist()
        unfreeze_candidates = get_json_setting('risk_unfreeze_candidates', {})
        if not isinstance(unfreeze_candidates, dict):
            unfreeze_candidates = {}
        high_risk_disable_user_ids = []
        mid_risk_limited_user_ids = []
        ip_control_enabled = capability_enabled("connections_drop", default=False)

        for item in incidents:
            uid = item['uid']
            score = int(item.get('score', 0))
            if score >= high_score:
                risk_level = '高'
                if enforce_mode == 'enforce':
                    action_taken = '禁用'
                    high_risk_disable_user_ids.append(uid)
                    unfreeze_candidates.pop(str(uid), None)
                elif enforce_mode == 'gray':
                    action_taken = '限速(灰度)'
                    mid_risk_limited_user_ids.append(uid)
                    unfreeze_candidates[str(uid)] = int(time.time())
                else:
                    action_taken = '仅告警(观察)'
                    watchlist.add(str(uid))
            elif score >= low_score:
                risk_level = '中'
                if enforce_mode == 'enforce':
                    action_taken = '限速'
                    mid_risk_limited_user_ids.append(uid)
                    unfreeze_candidates[str(uid)] = int(time.time())
                else:
                    action_taken = '仅告警(灰度/观察)'
                    watchlist.add(str(uid))
            else:
                risk_level = '低'
                action_taken = '告警'
                watchlist.add(str(uid))

            evidence_summary = '; '.join(f"{e['ip']}@{e['ts']}" for e in item['evidence'][:3])
            db_execute(
                "INSERT INTO anomaly_events (user_uuid, user_id, risk_level, risk_score, ip_count, ua_diversity, density, action_taken, evidence_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ('', uid, risk_level, score, int(item['ip_count']), int(item['ua_diversity']), int(item['density']), action_taken, evidence_summary[:400], int(time.time())),
            )
            append_ops_timeline('风控', '异常处置', f'uid={uid},level={risk_level},action={action_taken},score={score}', actor='系统', target=uid)
            await sync_user_metadata(uid, tg_id="-", risk_level=risk_level)

            if ip_control_enabled and risk_level == '高':
                for ev in item.get('evidence', [])[:3]:
                    ip = str(ev.get('ip') or '').strip()
                    if ip and ip not in {"-", "unknown"}:
                        await block_panel_ip(ip, f"anomaly_high_risk_score_{score}")

            try:
                lines = [
                    "🚨 *异常检测（可解释）*",
                    f"风险等级: `{risk_level}` \\| 处置: `{action_taken}`",
                    f"用户: `{escape_markdown_v2(uid)}`",
                    f"风险评分: `{score}`",
                    f"IP数量: `{item['ip_count']}` \\| UA分散: `{item['ua_diversity']}` \\| 请求密度: `{item['density']}`",
                    "证据（最近10条）:",
                ]
                for ev in item['evidence'][:10]:
                    lines.append(
                        f"- `{escape_markdown_v2(str(ev['ts']))}` \\| `{escape_markdown_v2(str(ev['ip']))}` \\| `{escape_markdown_v2(str(ev['ua']))}`"
                    )
                quick_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ 加入白名单", callback_data=f"anomaly_quick_whitelist_{uid}")],
                    [InlineKeyboardButton("✅ 尝试解封", callback_data=f"anomaly_quick_enable_{uid}")],
                ])
                await context.bot.send_message(ADMIN_ID, "\n".join(lines), parse_mode='MarkdownV2', reply_markup=quick_kb)
            except Exception as exc:
                logger.warning("Failed to notify anomaly admin: %s", exc)

        if high_risk_disable_user_ids:
            await apply_user_status_bulk(high_risk_disable_user_ids, USER_STATUS_DISABLED)
        if mid_risk_limited_user_ids:
            await apply_user_status_bulk(mid_risk_limited_user_ids, USER_STATUS_LIMITED)

        set_risk_watchlist(watchlist)
        set_json_setting('risk_unfreeze_candidates', unfreeze_candidates)

        if max_seen_ts > last_scan_ts:
            db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('anomaly_last_scan_ts', ?)", (str(max_seen_ts),))
    except Exception as exc:
        logger.exception("check_anomalies_job failed: %s", exc)

if __name__ == '__main__':
    import urllib3
    urllib3.disable_warnings()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^v38a_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^del_plan_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^plan_detail_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^cancel_op$"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^manage_user_")) 
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^user_reqhist_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^list_user_subs_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^confirm_del_user_")) 
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^reset_traffic_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^set_strategy_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^set_payimg_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^set_pay_usdt_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^toggle_pay_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^reply_user_")) 
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^set_anomaly_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^admin_orders_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^admin_order_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^panelcfg_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^anomaly_whitelist_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^anomaly_quick_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^bulk_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^bind_panel_user_"))
    app.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^tpl_"))
    app.add_handler(CallbackQueryHandler(add_plan_start, pattern="^add_plan_start$"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^client_"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^v38u_"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^selrenew_"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^order_"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^manualreview_"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^paymethod_"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^cancel_order"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^back_home$"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^contact_support$"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^client_nodes$"))
    app.add_handler(CallbackQueryHandler(client_menu_handler, pattern="^view_sub_"))
    app.add_handler(CallbackQueryHandler(process_order, pattern="^(ap|rj|review)_"))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message))
    app.add_error_handler(telegram_error_handler)
    
    app.job_queue.run_daily(check_expiry_job, time=datetime.time(hour=12, minute=0, second=0))
    anomaly_interval_seconds = 3600
    try:
        saved_interval = db_query("SELECT value FROM settings WHERE key='anomaly_interval'", one=True)
        if saved_interval and float(saved_interval['value']) > 0:
            anomaly_interval_seconds = float(saved_interval['value']) * 3600
    except (ValueError, TypeError) as exc:
        logger.warning("Invalid anomaly interval; using one hour: %s", exc)
    app.job_queue.run_repeating(check_anomalies_job, interval=anomaly_interval_seconds, first=60, name='check_anomalies_job')
    app.job_queue.run_repeating(process_bulk_jobs_job, interval=30, first=10, name='process_bulk_jobs_job')
    app.job_queue.run_repeating(reconcile_legacy_subscriptions_job, interval=3600, first=20, name='reconcile_legacy_subscriptions_job')

    if panel_config_ready():
        app.job_queue.run_once(warmup_panel_runtime_job, when=5, name='warmup_panel_runtime_job')

    print(f"🚀 RemnaShop-Pro {APP_VERSION} 已启动 | 监听中...")
    app.run_polling()
