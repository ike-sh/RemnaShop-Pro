import json
import secrets
import sqlite3
import time
from typing import Any, Iterable


def _connect(db_file: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(db_file: str) -> None:
    conn = _connect(db_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _init_db_on_connection(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_db_on_connection(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS plans (key TEXT PRIMARY KEY, name TEXT, price TEXT, usdt_price TEXT, days INTEGER, gb INTEGER, reset_strategy TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, uuid TEXT, created_at TIMESTAMP)''')
    _add_column(conn, "subscriptions", "user_id", "INTEGER")
    _add_column(conn, "subscriptions", "migration_status", "TEXT")
    try:
        c.execute("ALTER TABLE subscriptions ADD COLUMN plan_key TEXT")
    except sqlite3.OperationalError:
        pass

    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')

    try:
        c.execute("ALTER TABLE subscriptions ADD COLUMN last_notify_expire_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE subscriptions ADD COLUMN last_notify_days_left INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE subscriptions ADD COLUMN last_notify_at INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE plans ADD COLUMN reset_strategy TEXT DEFAULT 'NO_RESET'")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE plans ADD COLUMN usdt_price TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    c.execute(
        '''CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            tg_id INTEGER NOT NULL,
            plan_key TEXT NOT NULL,
            order_type TEXT NOT NULL,
            target_uuid TEXT,
            status TEXT NOT NULL,
            payment_text TEXT,
            admin_message_id INTEGER,
            menu_message_id INTEGER,
            waiting_message_id INTEGER,
            delivered_uuid TEXT,
            error_message TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )'''
    )
    _add_column(conn, "orders", "target_user_id", "INTEGER")
    _add_column(conn, "orders", "delivered_user_id", "INTEGER")


    try:
        c.execute("ALTER TABLE orders ADD COLUMN channel_code TEXT")
    except sqlite3.OperationalError:
        pass

    c.execute('''CREATE TABLE IF NOT EXISTS anomaly_whitelist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_uuid TEXT UNIQUE,
        created_at INTEGER NOT NULL
    )''')
    _add_column(conn, "anomaly_whitelist", "user_id", "INTEGER")

    c.execute('''CREATE TABLE IF NOT EXISTS order_audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT NOT NULL,
        action TEXT NOT NULL,
        actor_id INTEGER,
        detail TEXT,
        created_at INTEGER NOT NULL
    )''')


    c.execute('''CREATE TABLE IF NOT EXISTS bulk_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        result_json TEXT,
        created_by INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )''')
    _add_column(conn, "bulk_jobs", "attempts", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "bulk_jobs", "next_attempt_at", "INTEGER NOT NULL DEFAULT 0")
    # A lost response can mean a destructive request was already accepted.
    # Never replay reset/delete automatically after a crash or timeout.
    c.execute("""UPDATE bulk_jobs SET status='unknown'
                 WHERE status IN ('running','retry') AND action IN ('reset','delete','extend','revoke')""")
    c.execute("""UPDATE bulk_jobs SET status='pending'
                 WHERE status='running' AND action NOT IN ('reset','delete','extend','revoke')""")

    # A resumed approval or destructive callback may already have reached Panel.
    # Preserve the record for manual review instead of replaying the write.
    c.execute("UPDATE orders SET status='unknown' WHERE status='approved' OR (order_type='renew' AND status='extension_applied')")
    c.execute('''CREATE TABLE IF NOT EXISTS action_requests (
        id TEXT PRIMARY KEY,
        tg_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )''')
    c.execute("UPDATE action_requests SET status='unknown' WHERE status='running'")
    c.execute("CREATE INDEX IF NOT EXISTS idx_action_requests_actor_created ON action_requests (tg_id, created_at DESC)")

    c.execute('''CREATE TABLE IF NOT EXISTS ops_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_by INTEGER,
        created_at INTEGER NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS anomaly_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_uuid TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        risk_score INTEGER NOT NULL,
        ip_count INTEGER NOT NULL,
        ua_diversity INTEGER NOT NULL,
        density INTEGER NOT NULL,
        action_taken TEXT NOT NULL,
        evidence_summary TEXT,
        created_at INTEGER NOT NULL
    )''')
    _add_column(conn, "anomaly_events", "user_id", "INTEGER")

    # Preserve every legacy UUID. Resolution to a Panel v3 numeric ID occurs
    # only after a unique, verified match; ambiguous records remain visible.
    c.execute("""UPDATE subscriptions SET migration_status='pending'
                 WHERE user_id IS NULL AND migration_status IS NULL""")
    c.execute("""UPDATE subscriptions SET migration_status='resolved'
                 WHERE user_id IS NOT NULL AND migration_status IS NULL""")

    c.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_tg_id ON subscriptions (tg_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_uuid ON subscriptions (uuid)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_user_id_unique ON subscriptions (user_id) WHERE user_id IS NOT NULL")
    c.execute("CREATE INDEX IF NOT EXISTS idx_orders_tg_id_status ON orders (tg_id, status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders (order_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders (status, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_order_audit_order_id ON order_audit_logs (order_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_events_user_created ON anomaly_events (user_uuid, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_events_user_id_created ON anomaly_events (user_id, created_at DESC)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_anomaly_whitelist_user_id ON anomaly_whitelist (user_id) WHERE user_id IS NOT NULL")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bulk_jobs_status_created ON bulk_jobs (status, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ops_templates_created ON ops_templates (created_at DESC)")

    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('notify_days', '3')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('cleanup_days', '7')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('anomaly_interval', '1')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('anomaly_threshold', '50')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('risk_low_score', '80')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('risk_high_score', '130')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('risk_enforce_mode', 'enforce')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('anomaly_last_scan_ts', '0')")

    c.execute("SELECT count(*) FROM plans")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO plans (key, name, price, usdt_price, days, gb, reset_strategy) VALUES (?, ?, ?, ?, ?, ?, ?)", ('p1', '1个月', '200元', '28', 30, 100, 'NO_RESET'))
        c.execute("INSERT INTO plans (key, name, price, usdt_price, days, gb, reset_strategy) VALUES (?, ?, ?, ?, ?, ?, ?)", ('p2', '3个月', '580元', '82', 90, 500, 'NO_RESET'))

def db_query(db_file: str, query: str, args: Iterable[Any] = (), one: bool = False):
    conn = _connect(db_file)
    cur = conn.cursor()
    cur.execute(query, tuple(args))
    rv = cur.fetchall()
    conn.close()
    return (rv[0] if rv else None) if one else rv


def db_execute(db_file: str, query: str, args: Iterable[Any] = ()) -> int:
    conn = _connect(db_file)
    cur = conn.cursor()
    cur.execute(query, tuple(args))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed


def bind_legacy_subscription(db_file: str, subscription_id: int, user_id: int, tg_id: int) -> None:
    """Atomically attach a verified Panel v3 ID without discarding legacy references."""
    if isinstance(user_id, bool) or int(user_id) <= 0:
        raise ValueError("Panel user ID must be positive")
    conn = _connect(db_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT tg_id, uuid, user_id FROM subscriptions WHERE id=?", (subscription_id,)
        ).fetchone()
        if row is None or int(row["tg_id"]) != int(tg_id):
            raise ValueError("Legacy subscription does not belong to this Telegram user")
        if row["user_id"] is not None:
            if int(row["user_id"]) == int(user_id):
                conn.commit()
                return
            raise ValueError("Subscription is already bound to a different Panel user")
        duplicate = conn.execute(
            "SELECT id FROM subscriptions WHERE user_id=? AND id<>?", (user_id, subscription_id)
        ).fetchone()
        if duplicate:
            raise ValueError("Panel user is already linked to another subscription")
        legacy_uuid = row["uuid"]
        conn.execute(
            "UPDATE subscriptions SET user_id=?, migration_status='resolved' WHERE id=?",
            (user_id, subscription_id),
        )
        if legacy_uuid:
            conn.execute("UPDATE orders SET target_user_id=? WHERE target_uuid=?", (user_id, legacy_uuid))
            conn.execute("UPDATE orders SET delivered_user_id=? WHERE delivered_uuid=?", (user_id, legacy_uuid))
            conn.execute("UPDATE OR IGNORE anomaly_whitelist SET user_id=? WHERE user_uuid=?", (user_id, legacy_uuid))
            conn.execute("UPDATE anomaly_events SET user_id=? WHERE user_uuid=?", (user_id, legacy_uuid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_action_request(db_file: str, tg_id: int, user_id: int, action: str, payload: dict | None = None) -> str:
    action_id = secrets.token_hex(8)
    now = int(time.time())
    db_execute(
        db_file,
        """INSERT INTO action_requests
        (id, tg_id, user_id, action, payload_json, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (action_id, int(tg_id), int(user_id), action, json.dumps(payload or {}), now, now),
    )
    return action_id


def get_action_request(db_file: str, action_id: str):
    row = db_query(db_file, "SELECT * FROM action_requests WHERE id=?", (action_id,), one=True)
    return dict(row) if row else None


def claim_action_request(db_file: str, action_id: str, tg_id: int, action: str) -> bool:
    now = int(time.time())
    return bool(db_execute(
        db_file,
        """UPDATE action_requests SET status='running', updated_at=?
        WHERE id=? AND tg_id=? AND action=? AND status='pending' AND created_at>=?""",
        (now, action_id, int(tg_id), action, now - 900),
    ))


def finish_action_request(db_file: str, action_id: str, status: str) -> None:
    if status not in {'done', 'failed', 'unknown'}:
        raise ValueError('invalid action completion state')
    db_execute(db_file, "UPDATE action_requests SET status=?, updated_at=? WHERE id=? AND status='running'",
               (status, int(time.time()), action_id))
