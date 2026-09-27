import json
import os
import pathlib
import sqlite3
import sys


def fail(message: str) -> None:
    print(message)
    raise SystemExit(1)


config_path = pathlib.Path(os.getenv("REMNASHOP_CONFIG", "/app/config.json"))
db_path = pathlib.Path(os.getenv("REMNASHOP_DB", "/app/starlight.db"))

if not config_path.exists():
    fail(f"missing config: {config_path}")

try:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
except Exception as exc:
    fail(f"invalid config json: {exc}")

for key in ("admin_id", "bot_token"):
    if not str(cfg.get(key, "")).strip():
        fail(f"missing config key: {key}")

if not db_path.exists():
    fail(f"missing database: {db_path}")
try:
    conn = sqlite3.connect(db_path)
    result = conn.execute("PRAGMA quick_check;").fetchone()
    conn.close()
    if not result or result[0] != "ok":
        fail("database integrity check failed")
except Exception as exc:
    fail(f"database check failed: {exc}")

print("ok")
sys.exit(0)
