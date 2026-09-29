from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import jsonschema

from src.database import (
    ensure_fixture_database,
    import_snapshot,
    read_dashboard_snapshot,
    read_market_bars,
)
from src.fixtures import ROOT, load_snapshot


class DatabaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "fixture.db"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_bootstrap_persists_normalized_fixture_and_wal(self):
        ensure_fixture_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM wheel_cycle").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM current_position").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_bar").fetchone()[0], 780)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM technical_snapshot").fetchone()[0], 3)
        self.assertEqual(self.db_path.stat().st_mode & 0o777, 0o600)

    def test_bootstrap_is_idempotent(self):
        ensure_fixture_database(self.db_path)
        ensure_fixture_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM wheel_cycle").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM option_leg").fetchone()[0], 4)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_bar").fetchone()[0], 780)

    def test_database_roundtrip_validates_against_snapshot_contract(self):
        ensure_fixture_database(self.db_path)
        snapshot = read_dashboard_snapshot(self.db_path)
        schema = json.loads((ROOT / "schema" / "dashboard_snapshot.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(snapshot)
        self.assertEqual(snapshot["namespace"], "wheel-dashboard")
        self.assertEqual(len(snapshot["cycles"]), 3)
        self.assertEqual(len(snapshot["technicals"]), 3)
        self.assertNotEqual(snapshot["sources"][0]["content_sha256"], "f" * 64)

    def test_market_bars_are_loaded_from_database(self):
        ensure_fixture_database(self.db_path)
        bars = read_market_bars(self.db_path, "ALFA")
        self.assertEqual(len(bars), 260)
        self.assertLessEqual(bars[-1]["low"], bars[-1]["close"])
        self.assertGreaterEqual(bars[-1]["high"], bars[-1]["close"])

    def test_import_rejects_cross_namespace_data(self):
        ensure_fixture_database(self.db_path)
        bad = load_snapshot()
        bad["namespace"] = "other-strategy"
        with self.assertRaises(ValueError):
            import_snapshot(self.db_path, bad, content_sha256="a" * 64)

    def test_fixture_bootstrap_refuses_non_fixture_database(self):
        ensure_fixture_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE source_snapshot SET source_kind='MANUAL'")
            connection.commit()
        with self.assertRaises(RuntimeError):
            ensure_fixture_database(self.db_path)


if __name__ == "__main__":
    unittest.main()
