#!/usr/bin/env python3
"""Tests for coverage-gap filling.

On-call coverage is gapless (there is always an engineer on shift), so a gap
between consecutive shifts is a data artifact — e.g. a JSM Ops outage that split
one continuous shift. fill_coverage_gaps closes each gap by extending the
earlier shift to the next shift's start so the uncovered time is still paid.
"""

import unittest
from datetime import datetime

from minuto.main import OnCallShift, fill_coverage_gaps


def _shift(start, end, user="a@crate.io"):
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    return OnCallShift(start=s, end=e,
                       hours=round((e - s).total_seconds() / 3600, 2), user=user)


class TestCoverageGaps(unittest.TestCase):
    def test_same_engineer_gap_is_closed(self):
        # Shift ends 01:00, same engineer "restarts" 02:15 — the JSM outage case.
        shifts = [
            _shift("2026-06-26T00:00:00", "2026-06-26T01:00:00"),
            _shift("2026-06-26T02:15:00", "2026-06-26T04:00:00"),
        ]
        filled = fill_coverage_gaps(shifts)
        self.assertEqual(len(filled), 1)
        self.assertAlmostEqual(filled[0][2], 1.25, places=2)
        # The earlier shift now runs straight into the next one.
        self.assertEqual(shifts[0].end, datetime.fromisoformat("2026-06-26T02:15:00"))
        self.assertAlmostEqual(shifts[0].hours, 2.25, places=2)  # 1.0 + 1.25 gap

    def test_gap_credited_to_previous_engineer(self):
        shifts = [
            _shift("2026-01-01T00:00:00", "2026-01-01T08:00:00", "before@crate.io"),
            _shift("2026-01-01T09:00:00", "2026-01-01T17:00:00", "after@crate.io"),
        ]
        filled = fill_coverage_gaps(shifts)
        self.assertEqual(filled[0][3], "before@crate.io")
        self.assertEqual(shifts[0].end, datetime.fromisoformat("2026-01-01T09:00:00"))

    def test_no_cap_large_gap_filled(self):
        shifts = [
            _shift("2026-01-01T00:00:00", "2026-01-01T01:00:00"),
            _shift("2026-01-02T01:00:00", "2026-01-02T02:00:00"),  # ~24h gap
        ]
        filled = fill_coverage_gaps(shifts)
        self.assertEqual(len(filled), 1)
        self.assertAlmostEqual(filled[0][2], 24.0, places=2)

    def test_contiguous_shifts_untouched(self):
        shifts = [
            _shift("2026-01-01T00:00:00", "2026-01-01T08:00:00"),
            _shift("2026-01-01T08:00:00", "2026-01-01T16:00:00"),
        ]
        filled = fill_coverage_gaps(shifts)
        self.assertEqual(filled, [])
        self.assertAlmostEqual(shifts[0].hours, 8.0, places=2)

    def test_overlaps_left_untouched(self):
        # Next shift starts before the previous ends: not a gap, leave it alone.
        shifts = [
            _shift("2026-01-01T00:00:00", "2026-01-01T10:00:00"),
            _shift("2026-01-01T08:00:00", "2026-01-01T16:00:00"),
        ]
        filled = fill_coverage_gaps(shifts)
        self.assertEqual(filled, [])
        self.assertEqual(shifts[0].end, datetime.fromisoformat("2026-01-01T10:00:00"))

    def test_idempotent(self):
        shifts = [
            _shift("2026-01-01T00:00:00", "2026-01-01T01:00:00"),
            _shift("2026-01-01T02:00:00", "2026-01-01T03:00:00"),
        ]
        self.assertEqual(len(fill_coverage_gaps(shifts)), 1)
        self.assertEqual(fill_coverage_gaps(shifts), [])

    def test_unsorted_input(self):
        # Gaps must be found regardless of the input order.
        shifts = [
            _shift("2026-01-01T02:00:00", "2026-01-01T03:00:00"),
            _shift("2026-01-01T00:00:00", "2026-01-01T01:00:00"),
        ]
        filled = fill_coverage_gaps(shifts)
        self.assertEqual(len(filled), 1)
        self.assertAlmostEqual(filled[0][2], 1.0, places=2)


if __name__ == '__main__':
    unittest.main()
