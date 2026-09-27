import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestContainerHealthcheck(unittest.TestCase):
    def test_local_config_and_sqlite_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            database = root / "starlight.db"
            config.write_text(json.dumps({"admin_id": "123", "bot_token": "test"}), encoding="utf-8")
            env = {**os.environ, "REMNASHOP_CONFIG": str(config), "REMNASHOP_DB": str(database)}
            command = [sys.executable, "docker/healthcheck.py"]
            missing = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)
            sqlite3.connect(database).close()
            healthy = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(healthy.returncode, 0, healthy.stderr + healthy.stdout)
            database.write_bytes(b"not a sqlite database")
            corrupt = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(corrupt.returncode, 0)
