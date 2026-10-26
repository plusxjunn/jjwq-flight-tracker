"""Unit tests for rules.py using synthetic price series."""
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rules  # noqa: E402
from rules import Insights, Point  # noqa: E402

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 12, 1, 9, 7, tzinfo=SGT)
TYPICAL = Insights("typical", 305, 455)


def series(prices, end=NOW, step=timedelta(days=1)):
    """Daily points ending the day before `end` (oldest first)."""
    n = len(prices)
    return [Point(end - step * (n - i), p) for i, p in enumerate(prices)]


def kinds(reasons):
    return {r.kind for r in reasons}


class GoogleLow(unittest.TestCase):
    def test_fires_when_low_and_below_range(self):
        r = rules.evaluate_deal(280, NOW, [], Insights("low", 305, 455))
        self.assertEqual(kinds(r), {"google_low"})
        self.assertIn("8% below", r[0].text)

    def test_needs_both_conditions(self):
        self.assertEqual(rules.evaluate_deal(310, NOW, [], Insights("low", 305, 455)), [])   # inside range
        self.assertEqual(rules.evaluate_deal(280, NOW, [], Insights("typical", 305, 455)), [])  # not "low"
        self.assertEqual(rules.evaluate_deal(280, NOW, [], Insights("low", None, None)), [])


class HistoryLow(unittest.TestCase):
    def test_fires_after_14_days_at_30_day_low_and_10pct_below_median(self):
        hist = series([400] * 20 + [380] * 10)
        r = rules.evaluate_deal(350, NOW, hist, TYPICAL)
        self.assertIn("history_low", kinds(r))
        self.assertIn("12% below usual", next(x.text for x in r if x.kind == "history_low"))

    def test_needs_14_days_of_data(self):
        hist = series([400] * 10)
        self.assertNotIn("history_low", kinds(rules.evaluate_deal(300, NOW, hist, TYPICAL)))

    def test_not_below_rolling_min(self):
        hist = series([400] * 25 + [340] + [400] * 4)   # someone saw 340 recently
        self.assertNotIn("history_low", kinds(rules.evaluate_deal(350, NOW, hist, TYPICAL)))

    def test_min_but_not_10pct_below_median(self):
        hist = series([400] * 30)
        self.assertNotIn("history_low", kinds(rules.evaluate_deal(370, NOW, hist, TYPICAL)))

    def test_only_last_30_days_count(self):
        hist = series([300] * 10 + [400] * 30)   # old lows are >30 days ago
        self.assertIn("history_low", kinds(rules.evaluate_deal(350, NOW, hist, TYPICAL)))


class SharpDrop(unittest.TestCase):
    def test_fires_at_12pct_drop_vs_previous_run(self):
        hist = series([400, 400])
        r = rules.evaluate_deal(352, NOW, hist, TYPICAL)
        self.assertEqual(kinds(r), {"sharp_drop"})
        self.assertIn("was S$400", r[0].text)

    def test_smaller_drop_does_not_fire(self):
        self.assertEqual(rules.evaluate_deal(353, NOW, series([400]), TYPICAL), [])

    def test_compares_with_most_recent_point(self):
        hist = series([500, 400])   # previous run was 400, not 500
        self.assertEqual(rules.evaluate_deal(380, NOW, hist, TYPICAL), [])


class Target(unittest.TestCase):
    def test_off_when_unset(self):
        self.assertEqual(rules.evaluate_deal(100, NOW, [], TYPICAL), [])

    def test_fires_at_or_below_target(self):
        cfg = {"target_price": 250}
        self.assertEqual(kinds(rules.evaluate_deal(250, NOW, [], TYPICAL, cfg)), {"target"})
        self.assertEqual(rules.evaluate_deal(251, NOW, [], TYPICAL, cfg), [])


class Cooldown(unittest.TestCase):
    def test_no_previous_alert(self):
        self.assertTrue(rules.cooldown_allows(NOW, 300, None, None))

    def test_blocks_within_72h(self):
        self.assertFalse(rules.cooldown_allows(NOW, 300, NOW - timedelta(hours=71), 300))

    def test_allows_after_72h(self):
        self.assertTrue(rules.cooldown_allows(NOW, 300, NOW - timedelta(hours=72), 300))

    def test_further_5pct_drop_breaks_cooldown(self):
        self.assertTrue(rules.cooldown_allows(NOW, 285, NOW - timedelta(hours=1), 300))
        self.assertFalse(rules.cooldown_allows(NOW, 286, NOW - timedelta(hours=1), 300))


class Climbing(unittest.TestCase):
    def test_three_consecutive_days_15pct_above_median(self):
        hist = series([300] * 30, end=NOW - timedelta(days=2)) + [
            Point(NOW - timedelta(days=2), 350), Point(NOW - timedelta(days=1), 360), Point(NOW, 370)]
        up, above = rules.climbing(hist, NOW.date())
        self.assertTrue(up)
        self.assertGreaterEqual(above, 15)

    def test_two_days_not_enough(self):
        hist = series([300] * 30, end=NOW - timedelta(days=1)) + [
            Point(NOW - timedelta(days=1), 360), Point(NOW, 370)]
        self.assertFalse(rules.climbing(hist, NOW.date())[0])

    def test_counts_checks_not_calendar_days(self):
        # checked every few days (like the other SQ pairs): three high checks in a row still count
        hist = series([300] * 30, end=NOW - timedelta(days=4)) + [
            Point(NOW - timedelta(days=4), 360), Point(NOW - timedelta(days=2), 360), Point(NOW, 370)]
        self.assertTrue(rules.climbing(hist, NOW.date())[0])

    def test_one_normal_check_breaks_streak(self):
        hist = series([300] * 30, end=NOW - timedelta(days=2)) + [
            Point(NOW - timedelta(days=2), 360), Point(NOW - timedelta(days=1), 300), Point(NOW, 370)]
        self.assertFalse(rules.climbing(hist, NOW.date())[0])

    def test_needs_a_check_today(self):
        hist = series([300] * 30, end=NOW - timedelta(days=3)) + [
            Point(NOW - timedelta(days=3), 360), Point(NOW - timedelta(days=2), 360),
            Point(NOW - timedelta(days=1), 370)]
        self.assertFalse(rules.climbing(hist, NOW.date())[0])

    def test_needs_history(self):
        hist = [Point(NOW - timedelta(days=i), 500) for i in range(3)]
        self.assertFalse(rules.climbing(hist, NOW.date())[0])


class Verdict(unittest.TestCase):
    hist = series([400] * 30)

    def v(self, price, now=NOW, hist=None, ins=TYPICAL, reasons=None, up=False):
        h = self.hist if hist is None else hist
        r = rules.evaluate_deal(price, now, h, ins) if reasons is None else reasons
        return rules.verdict(price, now, h, ins, r, up)

    def test_book_now_when_rule_fires(self):
        self.assertEqual(self.v(340), "BOOK NOW")

    def test_good_price_at_or_below_median(self):
        self.assertEqual(self.v(395), "GOOD PRICE")

    def test_wait_above_median(self):
        self.assertEqual(self.v(420), "WAIT")

    def test_rising_when_climbing(self):
        self.assertEqual(self.v(470, up=True), "RISING")

    def test_good_price_on_google_low_without_history(self):
        self.assertEqual(self.v(320, hist=[], ins=Insights("low", 305, 455)), "GOOD PRICE")


class HardDeadline(unittest.TestCase):
    after = datetime(2027, 6, 1, 9, 7, tzinfo=SGT)
    hist = series([400] * 30, end=after)

    def test_book_now_after_deadline_at_or_below_median(self):
        self.assertEqual(rules.verdict(400, self.after, self.hist, TYPICAL, [], False), "BOOK NOW")
        self.assertEqual(rules.verdict(390, self.after, self.hist, TYPICAL, [], False), "BOOK NOW")

    def test_not_book_now_after_deadline_above_median(self):
        self.assertEqual(rules.verdict(420, self.after, self.hist, TYPICAL, [], False), "WAIT")
        self.assertEqual(rules.verdict(470, self.after, self.hist, TYPICAL, [], True), "RISING")

    def test_deadline_day_itself_uses_normal_rules(self):
        on = datetime(2027, 5, 31, 9, 7, tzinfo=SGT)
        hist = series([400] * 30, end=on)
        self.assertEqual(rules.verdict(400, on, hist, TYPICAL, [], False), "GOOD PRICE")

    def test_deadline_from_config_string(self):
        cfg = {"hard_deadline": "2026-11-01"}
        self.assertEqual(rules.verdict(400, NOW, series([400] * 30), TYPICAL, [], False, cfg), "BOOK NOW")


if __name__ == "__main__":
    unittest.main()
