"""Verify a stopped-volume snapshot without printing configuration values."""

import json
import pathlib
import sqlite3
import sys
from contextlib import closing


def verify_backup(directory: pathlib.Path) -> None:
    directory = pathlib.Path(directory)
    config_path = directory / "config.json"
    database_path = directory / "starlight.db"
    if not config_path.is_file() or not database_path.is_file():
        raise ValueError("Backup must contain config.json and starlight.db")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not all(
        str(config.get(key, "")).strip() for key in ("admin_id", "bot_token")
    ):
        raise ValueError("Backup configuration is incomplete")

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
        if rows != [("ok",)]:
            raise ValueError("Backup database integrity check failed")


if __name__ == "__main__":
    verify_backup(pathlib.Path(sys.argv[1]))
    print("[backup] snapshot verified")
