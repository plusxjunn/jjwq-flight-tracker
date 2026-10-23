"""Tests for the backup-schedule guard in store.py."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import Store  # noqa: E402

SGT = timezone(timedelta(hours=8))
MORNING = datetime(2026, 10, 23, 9, 7, tzinfo=SGT)


class SlotDone(unittest.TestCase):
    def setUp(self):
        self.store = Store(Path(tempfile.mkdtemp()) / "t.db")

    def tearDown(self):
        self.store.close()

    def run_with(self, status, ts=MORNING, slot="morning"):
        self.store.finish_run(self.store.start_run(slot, ts), status, 6)

    def test_nothing_ran(self):
        self.assertFalse(self.store.slot_done("morning", MORNING.date()))

    def test_successful_run_counts(self):
        for status in ("ok", "partial", "quota_skip"):
            with self.subTest(status=status):
                self.setUp()
                self.run_with(status)
                self.assertTrue(self.store.slot_done("morning", MORNING.date()))

    def test_failed_run_lets_backup_retry(self):
        self.run_with("api_error")
        self.run_with("error")
        self.assertFalse(self.store.slot_done("morning", MORNING.date()))

    def test_other_slot_or_day_does_not_count(self):
        self.run_with("ok", slot="evening")
        self.run_with("ok", ts=MORNING - timedelta(days=1))
        self.assertFalse(self.store.slot_done("morning", MORNING.date()))


if __name__ == "__main__":
    unittest.main()
