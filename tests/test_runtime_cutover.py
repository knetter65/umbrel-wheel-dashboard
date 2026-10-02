from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import app, database
from src.fixtures import load_snapshot


class RuntimeCutoverTests(unittest.TestCase):
    def test_default_database_switches_when_active_appears_after_startup(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            fixture = root / "fixture.db"
            active = root / "active-readonly.db"
            fixture.write_bytes(b"fixture")
            with (
                patch.object(database, "_DB_OVERRIDE", None),
                patch.object(database, "FIXTURE_DB_PATH", fixture),
                patch.object(database, "ACTIVE_DB_PATH", active),
            ):
                self.assertEqual(database.default_dashboard_database(), fixture)
                active.write_bytes(b"active")
                self.assertEqual(database.default_dashboard_database(), active)

    def test_private_source_labels_canonicalize_to_public_contract(self):
        self.assertEqual(database.canonical_source_kind("IBKR_FLEX"), "BROKER_HISTORY")
        self.assertEqual(database.canonical_source_kind("IBKR_TWS"), "BROKER_SNAPSHOT")
        self.assertEqual(database.canonical_source_kind("MARKET_HISTORY"), "MARKET_HISTORY")
        self.assertEqual(database.canonical_source_kind("FIXTURE"), "FIXTURE")

    def test_multisource_gate_accepts_private_aliases_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "active.db"
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    """
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE source_snapshot(source_kind TEXT NOT NULL);
                    CREATE TABLE reconciliation_issue(severity TEXT NOT NULL);
                    INSERT INTO source_snapshot VALUES ('IBKR_FLEX');
                    INSERT INTO source_snapshot VALUES ('IBKR_TWS');
                    INSERT INTO source_snapshot VALUES ('MARKET_HISTORY');
                    """
                )
            self.assertEqual(database._validate_multisource_database(path, "test"), path)
            with sqlite3.connect(path) as connection:
                connection.execute("DELETE FROM source_snapshot WHERE source_kind='IBKR_TWS'")
                connection.commit()
            with self.assertRaisesRegex(RuntimeError, "lacks required sources"):
                database._validate_multisource_database(path, "test")

    def test_page_load_reresolves_database_path(self):
        fixture = Path("/tmp/fixture.db")
        active = app.ACTIVE_DB_PATH
        fixture_snapshot = load_snapshot()
        active_snapshot = load_snapshot()
        active_snapshot["sources"] = [
            {"kind": "BROKER_HISTORY", "quality": "DELAYED", "cutoff_at": "2026-10-01", "warnings": []},
            {"kind": "BROKER_SNAPSHOT", "quality": "LIVE", "cutoff_at": "2026-10-02T18:00:12+00:00", "warnings": []},
            {"kind": "MARKET_HISTORY", "quality": "DELAYED", "cutoff_at": "2026-10-01", "warnings": []},
        ]
        with (
            patch.object(app, "resolve_dashboard_database", side_effect=[fixture, active]),
            patch.object(app, "read_dashboard_snapshot", side_effect=[fixture_snapshot, active_snapshot]),
        ):
            app.refresh_runtime_state()
            self.assertTrue(app.IS_FIXTURE)
            self.assertEqual(app.DB_PATH, fixture)
            app.refresh_runtime_state()
            self.assertFalse(app.IS_FIXTURE)
            self.assertTrue(app.IS_ACTIVE)
            self.assertEqual(app.DB_PATH, active)


if __name__ == "__main__":
    unittest.main()
