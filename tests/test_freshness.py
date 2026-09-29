from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.freshness import effective_source, source_label, validate_fresh_refresh


class FreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)
        self.snapshot = {
            "kind": "BROKER_SNAPSHOT",
            "quality": "LIVE",
            "cutoff_at": "2026-09-25T15:40:11+00:00",
        }

    def test_snapshot_capture_quality_becomes_cached_without_rewriting_source(self) -> None:
        item = effective_source(self.snapshot, self.now)
        self.assertEqual(item["captured_quality"], "LIVE")
        self.assertEqual(item["effective_quality"], "CACHED")
        self.assertIn("captured LIVE", source_label(self.snapshot, self.now))

    def test_recent_snapshot_capture_remains_live(self) -> None:
        source = dict(self.snapshot, cutoff_at="2026-09-27T06:50:00+00:00")
        self.assertEqual(effective_source(source, self.now)["effective_quality"], "LIVE")

    def test_old_snapshot_capture_becomes_stale(self) -> None:
        source = dict(self.snapshot, cutoff_at="2026-09-20T12:00:00+00:00")
        self.assertEqual(effective_source(source, self.now)["effective_quality"], "STALE")

    def test_fresh_refresh_gate_uses_run_timestamps_and_market_session_age(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "latest.xml"
            marker.write_text("test")
            timestamp = datetime(2026, 9, 27, 6, 59, 30, tzinfo=timezone.utc).timestamp()
            marker.touch()
            import os
            os.utime(marker, (timestamp, timestamp))
            result = validate_fresh_refresh(
                run_started_at=datetime(2026, 9, 27, 6, 59, tzinfo=timezone.utc),
                snapshot_generated_at="2026-09-27T06:59:40+00:00",
                market_fetched_at="2026-09-27T06:59:45+00:00",
                market_cutoff_at="2026-09-25",
                history_raw_mtime=marker.stat().st_mtime,
                now=self.now,
            )
        self.assertTrue(result["passed"])
        self.assertEqual(result["failures"], [])

    def test_refresh_gate_rejects_stale_market_session(self) -> None:
        result = validate_fresh_refresh(
            run_started_at=datetime(2026, 9, 27, 6, 59, tzinfo=timezone.utc),
            snapshot_generated_at="2026-09-27T06:59:40+00:00",
            market_fetched_at="2026-09-27T06:59:45+00:00",
            market_cutoff_at="2026-09-20",
            history_raw_mtime=datetime(2026, 9, 27, 6, 59, 30, tzinfo=timezone.utc).timestamp(),
            now=self.now,
        )
        self.assertFalse(result["passed"])
        self.assertIn("market-history completed-session cutoff is stale", result["failures"])


if __name__ == "__main__":
    unittest.main()
