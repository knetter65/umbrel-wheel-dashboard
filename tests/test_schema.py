from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]


class SQLiteSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "dashboard.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript((ROOT / "schema" / "wheel_dashboard.sql").read_text())

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def test_schema_enables_foreign_keys_and_wal(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")

    def test_expected_canonical_tables_exist(self):
        actual = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        expected = {
            "source_snapshot",
            "underlying",
            "wheel_cycle",
            "option_leg",
            "cash_ledger",
            "current_position",
            "cycle_metric_snapshot",
            "account_metric_snapshot",
            "market_bar",
            "technical_snapshot",
            "reconciliation_issue",
        }
        self.assertTrue(expected.issubset(actual))

    def _insert_source(self, reference: str = "sanitized-history-export") -> None:
        self.conn.execute(
            """
            INSERT INTO source_snapshot
              (snapshot_id, source_kind, quality, generated_at, cutoff_at,
               imported_at, source_reference, content_sha256)
            VALUES (?, 'BROKER_HISTORY', 'STALE', ?, ?, ?, ?, ?)
            """,
            (
                "snap-test",
                "2026-09-25T12:00:00Z",
                "2026-09-24T20:00:00Z",
                "2026-09-25T12:00:00Z",
                reference,
                "a" * 64,
            ),
        )

    def test_transfer_cannot_be_classified_as_trading_pnl(self):
        self._insert_source()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO cash_ledger
                  (ledger_id, occurred_at, entry_kind, realization_state,
                   currency, amount_micros, snapshot_id)
                VALUES ('transfer-1', ?, 'TRANSFER', 'REALIZED', 'USD', 1000000, 'snap-test')
                """,
                ("2026-09-25T12:00:00Z",),
            )

    def test_source_reference_rejects_account_identifier_label(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_source("payload-with-accountId-field")


class SnapshotContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(
            (ROOT / "schema" / "dashboard_snapshot.schema.json").read_text()
        )

    def test_namespace_is_hard_pinned_to_wheel_dashboard(self):
        self.assertEqual(
            self.schema["properties"]["namespace"], {"const": "wheel-dashboard"}
        )

    def test_root_and_objects_reject_unknown_fields(self):
        self.assertFalse(self.schema["additionalProperties"])
        for definition in self.schema["$defs"].values():
            if definition.get("type") == "object":
                self.assertFalse(definition.get("additionalProperties", True))

    def test_contract_has_no_secret_or_account_identifier_fields(self):
        serialized = json.dumps(self.schema).lower()
        for forbidden in (
            '"account_id"',
            '"accountid"',
            '"credential"',
            '"password"',
            '"broker_token"',
            '"session_cookie"',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_fixture_snapshot_validates_against_contract(self):
        fixture = json.loads(
            (ROOT / "fixtures" / "dashboard_snapshot.json").read_text()
        )
        jsonschema.Draft202012Validator(self.schema).validate(fixture)


if __name__ == "__main__":
    unittest.main()