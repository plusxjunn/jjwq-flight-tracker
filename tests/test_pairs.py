"""Tests for flight-pair pricing, search scheduling and guest-message wording."""
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
import notify  # noqa: E402
from fetch import Flight, SearchResult, SearchSpec  # noqa: E402
from store import Store  # noqa: E402

SGT = timezone(timedelta(hours=8))
CFG = main.load_config(ROOT / "config.yaml")


def result(spec_id, flights, url=None):
    spec = SearchSpec(id=spec_id, type="one_way", origin="SIN", destination="DPS", outbound_date="2027-09-03")
    return SearchResult(spec=spec, flights=[Flight(f[0][:2], "", f[0], f"2027-09-03 {f[1]}", f"2027-09-03 {f[2]}",
                                                   f[3]) for f in flights], url=url)


class Pairs(unittest.TestCase):
    def test_every_option_and_trip(self):
        ids = {p["pair_id"] for p in main.pairs(CFG)}
        self.assertIn("main:SQ:after_work", ids)
        self.assertNotIn("fri_mon:SQ:after_work", ids)      # after-work flights are main weekend only
        self.assertEqual(len([i for i in ids if i.endswith(":day")]), 3 * len(CFG["trips"]))

    def test_scoot_is_two_one_way_fares_on_the_planned_flights(self):
        results = {
            "ow_out_0903": result("ow_out_0903", [("TR 282", "15:55", "18:40", 150), ("TR 288", "10:15", "13:05", 228)]),
            "ow_back_0905": result("ow_back_0905", [("TR 347", "01:05", "03:50", 99), ("TR 283", "19:40", "22:35", 195)]),
        }
        quotes, warnings = main.price_pairs(CFG, results)
        q = next(q for q in quotes if q["pair_id"] == "main:TR:day")
        self.assertEqual(q["price"], 228 + 195)              # not the cheaper TR 282 / TR 347
        self.assertEqual((q["out_dep"], q["back_dep"]), ("10:15", "19:40"))

    def test_missing_planned_flight_is_reported_not_guessed(self):
        results = {
            "ow_out_0903": result("ow_out_0903", [("TR 282", "15:55", "18:40", 150)]),
            "ow_back_0905": result("ow_back_0905", [("TR 283", "19:40", "22:35", 195)]),
        }
        quotes, warnings = main.price_pairs(CFG, results)
        self.assertFalse([q for q in quotes if q["pair_id"] == "main:TR:day"])
        self.assertTrue(any("TR 288" in w for w in warnings))

    def test_return_ticket_uses_the_filtered_search(self):
        results = {"rt_main_SQ_day": result("rt_main_SQ_day", [("SQ 938", "09:15", "12:00", 380)], url="u")}
        quotes, _ = main.price_pairs(CFG, results)
        q = next(q for q in quotes if q["pair_id"] == "main:SQ:day")
        self.assertEqual((q["price"], q["source"], q["url"]), (380, "return", "u"))

    def test_return_spec_filters_airline_and_hours(self):
        p = next(p for p in main.pairs(CFG) if p["pair_id"] == "main:GA:day")
        s = main.return_spec(CFG, p)
        self.assertEqual((s.include_airlines, s.outbound_times, s.return_times), ("GA", "9,9", "14,14"))


class Schedule(unittest.TestCase):
    def test_main_pairs_every_2_days_others_weekly(self):
        store = Store(Path(tempfile.mkdtemp()) / "t.db")
        start = date(2026, 11, 2)
        counts, steady = {}, 0
        for k in range(35):
            day = start + timedelta(days=k)
            for spec in main.due_return_specs(CFG, store, day):
                counts[spec.id] = counts.get(spec.id, 0) + 1
                steady += k >= 7                         # day 0 prices everything once
                store.db.execute("INSERT INTO searches (ts, day, search_id) VALUES (?,?,?)",
                                 (f"{day}T09:07:00+08:00", day.isoformat(), spec.id))
        self.assertIn(counts["rt_main_SQ_day"], (17, 18, 19))
        self.assertIn(counts["rt_fri_wed_GA_day"], (5, 6))
        per_month = (steady + 5 * 28) / 28 * 31         # + 5 one-way searches a day
        self.assertLessEqual(per_month, CFG["quota"]["monthly_limit"])


class Wording(unittest.TestCase):
    def test_12_hour_times(self):
        self.assertEqual([notify.t12(t) for t in ("00:05", "09:15", "12:00", "13:05", "21:35")],
                         ["12:05am", "9:15am", "12:00pm", "1:05pm", "9:35pm"])

    def closing(self, **v):
        return notify.closing_line([{"airline": a, "verdict": x} for a, x in v.items()], CFG)

    def test_rising_price_never_says_good_time_for_that_airline(self):
        line = self.closing(TR="GOOD PRICE", SQ="WAIT", GA="GOOD PRICE")
        self.assertIn("Good time to book Scoot or Garuda", line)
        self.assertIn("Singapore Airlines is above usual", line)

    def test_all_above_usual(self):
        self.assertIn("No action needed this week", self.closing(TR="WAIT", SQ="WAIT", GA="WAIT"))

    def test_two_waiting_airlines(self):
        self.assertIn("Scoot and Singapore Airlines are above usual; we'll post when they drop",
                      self.closing(TR="WAIT", SQ="WAIT", GA="BOOK NOW"))

    def test_still_tracking(self):
        self.assertIn("No action needed yet", self.closing(TR="TRACKING", SQ="TRACKING", GA="TRACKING"))

    def test_rising_takes_priority(self):
        self.assertIn("Book soon if your dates are fixed", self.closing(TR="RISING", SQ="GOOD PRICE", GA="WAIT"))


if __name__ == "__main__":
    unittest.main()
