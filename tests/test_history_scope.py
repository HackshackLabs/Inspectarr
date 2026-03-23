"""History scope / upstream date resolution tests."""

import unittest
from datetime import datetime, timedelta, timezone

from tautulli_inspector.history_scope import (
    crawl_trim_cutoff_epoch,
    resolve_upstream_history_dates,
    utc_date_days_ago,
)


class HistoryScopeTests(unittest.TestCase):
    def test_utc_date_days_ago_format(self) -> None:
        s = utc_date_days_ago(0)
        self.assertRegex(s, r"^\d{4}-\d{2}-\d{2}$")

    def test_resolve_week_default_sets_after_to_roughly_last_week(self) -> None:
        after, before = resolve_upstream_history_dates("week", "", "", week_days=7)
        self.assertIsNone(before)
        self.assertIsNotNone(after)
        parsed = datetime.fromisoformat(after or "").date()
        today = datetime.now(timezone.utc).date()
        self.assertLessEqual(parsed, today)
        self.assertGreaterEqual(parsed, today - timedelta(days=8))

    def test_resolve_week_respects_explicit_start_date(self) -> None:
        after, before = resolve_upstream_history_dates("week", "2020-01-01", "", week_days=7)
        self.assertEqual("2020-01-01", after)

    def test_resolve_all_no_dates(self) -> None:
        after, before = resolve_upstream_history_dates("all", "", "")
        self.assertIsNone(after)
        self.assertIsNone(before)

    def test_resolve_all_with_end_only(self) -> None:
        after, before = resolve_upstream_history_dates("all", "", "2024-06-01")
        self.assertIsNone(after)
        self.assertEqual("2024-06-01", before)

    def test_crawl_trim_cutoff_epoch(self) -> None:
        ep = crawl_trim_cutoff_epoch("2024-06-15")
        self.assertIsNotNone(ep)
        self.assertEqual(ep, int(datetime(2024, 6, 15, tzinfo=timezone.utc).timestamp()))

    def test_crawl_trim_cutoff_epoch_invalid(self) -> None:
        self.assertIsNone(crawl_trim_cutoff_epoch("not-a-date"))


if __name__ == "__main__":
    unittest.main()
