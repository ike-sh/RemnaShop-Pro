import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from docker.verify_backup import verify_backup


class TestBackupSnapshot(unittest.TestCase):
    def test_requires_complete_integral_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            database = root / "starlight.db"
            with self.assertRaises(ValueError):
                verify_backup(root)
            config.write_text(
                json.dumps({"admin_id": "123", "bot_token": "test-token"}),
                encoding="utf-8",
            )
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE marker (value TEXT)")
            connection.execute("INSERT INTO marker VALUES ('preserved')")
            connection.commit()
            connection.close()
            verify_backup(root)
            database.write_bytes(b"corrupt")
            with self.assertRaises(sqlite3.DatabaseError):
                verify_backup(root)
