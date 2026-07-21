#!/usr/bin/env python3
"""Smoke tests for the XLSX export."""

import contextlib
import io
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytz
from openpyxl import load_workbook

import minuto.main

from minuto.main import (
    MONTHLY_PREPAY_AMOUNT,
    NIGHT_SHORT_SHIFT_RATE,
    STANDARD_RATE,
    WEEKEND_SHORT_SHIFT_RATE,
    CompensationPeriod,
    CompensationReport,
    CompensationType,
)


def _periods():
    """Two shifts for one user across two consecutive months."""
    user = "alice@example.com"
    base = datetime(2025, 1, 31, 17, 0, tzinfo=pytz.UTC)
    return [
        CompensationPeriod(
            user=user, start=base, end=base + timedelta(hours=14),
            hours=14.0, compensated_hours=14.0, amount=14.0 * 5.56,
            compensation_type=CompensationType.STANDARD,
        ),
        CompensationPeriod(
            user=user,
            start=datetime(2025, 2, 5, 9, 0, tzinfo=pytz.UTC),
            end=datetime(2025, 2, 5, 12, 0, tzinfo=pytz.UTC),
            hours=3.0, compensated_hours=3.0, amount=3.0 * 5.56,
            compensation_type=CompensationType.STANDARD,
            holiday_info={'name': 'Some Holiday', 'source': 'test'},
        ),
    ]


class TestExcelExport(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.out = Path(self._tmp.name) / 'out.xlsx'
        self.report = CompensationReport(_periods())
        self.report.export_to_excel(self.out)
        self.wb = load_workbook(self.out)  # values only; formulas stay as text

    def tearDown(self):
        self.wb.close()
        self._tmp.cleanup()

    def test_file_exists_and_is_valid_xlsx(self):
        self.assertTrue(self.out.exists())
        self.assertGreater(self.out.stat().st_size, 0)

    def test_sheets_in_expected_order(self):
        self.assertEqual(
            self.wb.sheetnames,
            ['Overview', 'Monthly Per User', 'Daily Summary', 'Detailed Shifts'],
        )

    def test_overview_shows_period_and_prepay(self):
        ws = self.wb['Overview']
        self.assertEqual(ws['A1'].value, 'On-Call Compensation Report')
        self.assertEqual(ws['A3'].value, 'Period from')
        self.assertEqual(ws['A8'].value, 'Monthly pre-pay (Pauschale)')
        self.assertEqual(ws['B8'].value, MONTHLY_PREPAY_AMOUNT)

    def test_overview_lists_all_rates(self):
        # Every yearly rate should be written into the report so a reviewer can
        # audit which figures produced the numbers.
        ws = self.wb['Overview']
        self.assertEqual(ws['B9'].value, STANDARD_RATE)
        self.assertEqual(ws['B10'].value, WEEKEND_SHORT_SHIFT_RATE)
        self.assertEqual(ws['B11'].value, NIGHT_SHORT_SHIFT_RATE)

    def test_overview_uses_live_formulas(self):
        ws = self.wb['Overview']
        # Cells in the totals block (A15:B21) are formulas referencing tables.
        formula_values = [ws.cell(row=r, column=2).value for r in range(15, 22)]
        for v in formula_values:
            self.assertIsInstance(v, str)
            self.assertTrue(v.startswith('='), f"expected formula, got {v!r}")

    def test_monthly_uses_sumifs_against_detailed_sheet(self):
        ws = self.wb['Monthly Per User']
        # Header at row 1; first data row at 2. Column E = Compensated Hours formula.
        f = ws['E2'].value
        self.assertTrue(f.startswith('=SUMIFS('), f)
        self.assertIn("'Detailed Shifts'!", f)
        # Pre-Paid uses the named cell PrePay
        self.assertIn('PrePay', ws['G2'].value)

    def test_tables_exclude_totals_row(self):
        # The totals row must sit BELOW each table. A formula inside a table's
        # totals row that isn't declared as totalsRowFunction="custom" makes Excel
        # flag the file as corrupt and "repair" it on open.
        for sheet, tname in [('Monthly Per User', 'tbl_monthly'),
                             ('Daily Summary', 'tbl_daily'),
                             ('Detailed Shifts', 'tbl_shifts')]:
            ws = self.wb[sheet]
            ref = ws.tables[tname].ref            # e.g. 'A1:H31'
            table_last = int(re.search(r'(\d+)$', ref).group(1))
            total_rows = [c.row for c in ws['A'] if c.value == 'Total']
            self.assertTrue(total_rows, f'{sheet}: no Total row')
            self.assertGreater(total_rows[0], table_last,
                               f'{sheet}: total row {total_rows[0]} inside table {ref}')

    def test_no_structured_table_references_in_formulas(self):
        # Structured references ([@Col] / tbl_name[Col]) resolve unreliably in
        # calculated columns and surfaced as #REF!/#N/A on recalculation. Every
        # formula must use plain sheet-qualified / cell references instead.
        for sheet in ('Overview', 'Monthly Per User', 'Daily Summary'):
            ws = self.wb[sheet]
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if isinstance(v, str) and v.startswith('='):
                        self.assertNotIn('[@', v, f'{sheet}!{cell.coordinate}: {v}')
                        self.assertNotIn('tbl_', v, f'{sheet}!{cell.coordinate}: {v}')

    def test_detailed_has_helper_columns_for_audit(self):
        ws = self.wb['Detailed Shifts']
        headers = [c.value for c in ws[1]]
        for required in ('Year-Month', 'Day', 'Is Weekend', 'Is Holiday'):
            self.assertIn(required, headers)

    def test_prepaid_total_respects_eligibility(self):
        # tbl_monthly[Pre-Paid Amount] should equal MONTHLY_PREPAY_AMOUNT * (eligible months).
        # Both periods are within the only user's data range and the user has no profile,
        # so both months are eligible → 2 * MONTHLY_PREPAY_AMOUNT.
        ws = self.wb['Monthly Per User']
        eligible_col = 4  # 'Eligible'
        eligible_rows = [
            ws.cell(row=r, column=eligible_col).value
            for r in range(2, ws.max_row + 1)
            if ws.cell(row=r, column=1).value  # skip totals row (no User)
        ]
        self.assertEqual(eligible_rows.count('Yes'), 2)
        self.assertEqual(eligible_rows.count('No'), 0)


class TestExcelExportCachedValues(unittest.TestCase):
    """Regression: formula cells must carry the precomputed result as their cached
    value, so the workbook reads correctly in viewers that don't recalculate
    (Slack preview, Numbers, Google Sheets, LibreOffice in manual mode). Before
    this, every SUMIFS/pre-pay cell cached 0 and those sheets showed all zeros.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        out = Path(self._tmp.name) / 'out.xlsx'
        CompensationReport(_periods()).export_to_excel(out)
        # data_only=True reads the cached results, i.e. what a non-recalculating
        # viewer displays.
        self.wb = load_workbook(out, data_only=True)

    def tearDown(self):
        self.wb.close()
        self._tmp.cleanup()

    def test_daily_amounts_are_not_zero(self):
        ws = self.wb['Daily Summary']
        amounts = [ws.cell(r, 5).value for r in range(2, ws.max_row)]  # Amount col, skip total
        self.assertTrue(all(a not in (None, 0) for a in amounts), amounts)
        self.assertAlmostEqual(sum(amounts), 14.0 * 5.56 + 3.0 * 5.56, places=2)

    def test_monthly_values_and_prepay_are_cached(self):
        ws = self.wb['Monthly Per User']
        data = [row for row in ws.iter_rows(min_row=2, values_only=True)
                if row[0] and row[0] != 'Total']
        # Amount (col idx 5) and Pre-Paid (col idx 6) must be real numbers, not 0.
        self.assertAlmostEqual(sum(r[5] for r in data), 14.0 * 5.56 + 3.0 * 5.56, places=2)
        for r in data:
            self.assertEqual(r[6], MONTHLY_PREPAY_AMOUNT)

    def test_totals_row_is_cached(self):
        ws = self.wb['Monthly Per User']
        total_row = [row for row in ws.iter_rows(min_row=2, values_only=True)
                     if row[0] == 'Total'][0]
        self.assertAlmostEqual(total_row[5], 14.0 * 5.56 + 3.0 * 5.56, places=2)

    def test_overview_total_hours_cached(self):
        ws = self.wb['Overview']
        # 'Total compensated hours' is the first Totals row (B15).
        self.assertEqual(ws['B15'].value, 17.0)


class TestPrepayConsistency(unittest.TestCase):
    """Regression: the text report hardcoded the pre-pay (510.0) in six places
    while the XLSX used MONTHLY_PREPAY_AMOUNT, so the two outputs disagreed after
    the rate changed. The text report must derive the pre-pay from the constant.
    """

    def test_text_report_reflects_prepay_constant(self):
        buf = io.StringIO()
        # Patch the constant to a distinctive value; the report must use it, so a
        # re-introduced hardcoded literal would fail this test.
        with mock.patch.object(minuto.main, 'MONTHLY_PREPAY_AMOUNT', 999.0), \
                contextlib.redirect_stdout(buf):
            CompensationReport(_periods()).print_report()
        out = buf.getvalue()
        self.assertIn('999', out)
        self.assertNotIn('510', out)

    def test_grand_total_uses_per_month_floor(self):
        # One user, one month above the pre-pay and one below. Per-month floor:
        # (800-536) + max(0, 100-536) = 264. Aggregate netting would give
        # max(0, 900 - 2*536) = 0. The grand total must be 264.
        user = 'x@crate.io'
        periods = [
            CompensationPeriod(
                user=user,
                start=datetime(2026, 1, 10, 9, 0, tzinfo=pytz.UTC),
                end=datetime(2026, 1, 10, 17, 0, tzinfo=pytz.UTC),
                hours=8.0, compensated_hours=8.0, amount=800.0,
                compensation_type=CompensationType.STANDARD),
            CompensationPeriod(
                user=user,
                start=datetime(2026, 2, 10, 9, 0, tzinfo=pytz.UTC),
                end=datetime(2026, 2, 10, 17, 0, tzinfo=pytz.UTC),
                hours=8.0, compensated_hours=8.0, amount=100.0,
                compensation_type=CompensationType.STANDARD),
        ]
        buf = io.StringIO()
        with mock.patch.object(minuto.main, 'MONTHLY_PREPAY_AMOUNT', 536.0), \
                contextlib.redirect_stdout(buf):
            CompensationReport(periods).print_report()
        m = re.search(r'Total additional compensation: ([\d.]+)', buf.getvalue())
        self.assertIsNotNone(m)
        self.assertAlmostEqual(float(m.group(1)), 264.0, places=2)


class TestExcelExportMixedTimezones(unittest.TestCase):
    """Regression: users spanning different timezones produce an object-dtype
    Start/End column that pandas can't hold as a single tz dtype. The export
    must still strip tz per element (Excel rejects tz-aware datetimes) and keep
    each user's local wall-clock time.
    """

    def test_export_succeeds_and_keeps_local_wall_clock(self):
        base = datetime(2026, 4, 6, 18, 0)
        periods = [
            CompensationPeriod(
                user="walter@crate.io",
                start=pytz.timezone("Europe/Vienna").localize(base),
                end=pytz.timezone("Europe/Vienna").localize(base + timedelta(hours=4)),
                hours=4.0, compensated_hours=4.0, amount=22.84,
                compensation_type=CompensationType.STANDARD,
            ),
            CompensationPeriod(
                user="shyukri.shyukriev@crate.io",
                start=pytz.timezone("Europe/Sofia").localize(base),
                end=pytz.timezone("Europe/Sofia").localize(base + timedelta(hours=4)),
                hours=4.0, compensated_hours=4.0, amount=22.84,
                compensation_type=CompensationType.STANDARD,
            ),
        ]
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / 'mixed.xlsx'
            CompensationReport(periods).export_to_excel(out)  # must not raise
            wb = load_workbook(out)
            ws = wb['Detailed Shifts']
            starts = {ws.cell(r, 1).value: ws.cell(r, 3).value for r in (2, 3)}
            wb.close()
        # Local wall-clock time preserved and tz dropped (naive datetime).
        for value in starts.values():
            self.assertEqual(value, base)
            self.assertIsNone(value.tzinfo)


if __name__ == '__main__':
    unittest.main()
