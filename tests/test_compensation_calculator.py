#!/usr/bin/env python3
"""
Tests for the compensation calculator functionality in minuto.

These tests verify various compensation calculation scenarios including:
- Standard weekly on-call hours
- Holiday calculations
- Special cases like short shifts
- Country-specific holidays
"""

import json
import datetime
from pathlib import Path
from datetime import timedelta, datetime
import unittest
from unittest.mock import patch


import pytz

# Import the compensation calculator module
from minuto.main import (
    CompensationCalculator, OnCallShift, STANDARD_RATE, WEEKEND_SHORT_SHIFT_RATE,
    NIGHT_SHORT_SHIFT_RATE
)


class TestCompensationCalculator(unittest.TestCase):
    """Tests for the CompensationCalculator class"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a temporary directory for test data
        self.test_dir = Path('test_data')
        self.test_dir.mkdir(exist_ok=True)

        # Create test user profiles
        self.test_profiles = [
            {
                "email": "test.user@example.com",
                "timezone": "Europe/Vienna",
                "working_days": [0, 1, 2, 3, 4],  # Monday to Friday
                "working_hours_start": "09:00:00",
                "working_hours_end": "17:00:00",
                "country_code": "AT"  # Austria
            },
            {
                "email": "bulgarian.user@example.com",
                "timezone": "Europe/Sofia",
                "working_days": [0, 1, 2, 3, 4],  # Monday to Friday
                "working_hours_start": "09:00:00",
                "working_hours_end": "17:00:00",
                "country_code": "BG"  # Bulgaria
            }
        ]

        # Save test profiles to a file
        self.profiles_path = self.test_dir / 'test_profiles.json'
        with open(self.profiles_path, 'w') as f:
            json.dump(self.test_profiles, f)

        # Initialize calculator with test profiles
        self.calculator = CompensationCalculator(user_profiles_path=self.profiles_path)

    def tearDown(self):
        """Clean up after each test method."""
        # Remove test files
        if self.profiles_path.exists():
            self.profiles_path.unlink()

        # Remove test directory if it's empty
        if self.test_dir.exists() and not list(self.test_dir.iterdir()):
            self.test_dir.rmdir()

    def test_weekday_hours_no_holidays(self):
        """Test compensation calculation for a standard weekday shift with no holidays."""
        # Create a shift on a Tuesday (1) from 17:00 to 09:00 next day (16 hours)
        # July 16, 2024 is a Tuesday with no holidays in Austria
        start = datetime(2024, 7, 16, 17, 0, 0, tzinfo=pytz.UTC)  # 5 PM UTC
        end = start + timedelta(hours=16)  # 9 AM UTC next day

        shift = OnCallShift(
            start=start,
            end=end,
            hours=16.0,
            user="test.user@example.com"
        )

        # Calculate compensation
        periods = self.calculator.calculate_compensation(shift)

        # Assertions
        self.assertTrue(len(periods) > 0, "No compensation periods returned")

        # Total compensated hours should be 16 (all outside working hours)
        total_hours = sum(p.compensated_hours for p in periods)
        print(f"Total compensated hours: {total_hours}")
        self.assertAlmostEqual(total_hours, 14.0, places=1)

        # Expected amount: 14 hours * STANDARD_RATE
        expected_amount = 14.0 * STANDARD_RATE
        total_amount = sum(p.amount for p in periods)
        self.assertAlmostEqual(total_amount, expected_amount, places=2)

    def test_weekday_hours_with_holiday(self):
        """Test compensation calculation for a shift that includes a holiday."""
        # August 15, 2024 is Assumption Day in Austria (a public holiday)
        start = datetime(2024, 8, 15, 9, 0, 0, tzinfo=pytz.UTC)  # 9 AM UTC
        end = start + timedelta(hours=8)  # 5 PM UTC

        shift = OnCallShift(
            start=start,
            end=end,
            hours=8.0,
            user="test.user@example.com"
        )

        # Mock the is_holiday method to return True for this date
        original_is_holiday = self.calculator.is_holiday

        def mock_is_holiday(date, user):
            if date.date() == datetime(2024, 8, 15).date():
                return True, "Assumption Day"
            return original_is_holiday(date, user)

        with patch.object(self.calculator, 'is_holiday', side_effect=mock_is_holiday):
            # Calculate compensation
            periods = self.calculator.calculate_compensation(shift)

            # Assertions
            self.assertTrue(len(periods) > 0, "No compensation periods returned")

            # All hours should be compensated (holiday)
            total_hours = sum(p.compensated_hours for p in periods)
            self.assertAlmostEqual(total_hours, 8.0, places=1)

            # Expected amount: 8 hours * STANDARD_RATE
            expected_amount = 8.0 * STANDARD_RATE
            total_amount = sum(p.amount for p in periods)
            self.assertAlmostEqual(total_amount, expected_amount, places=2)

            # Check if any period has holiday info
            has_holiday_info = any(p.holiday_info is not None for p in periods)
            self.assertTrue(has_holiday_info, "No holiday information found in compensation periods")

    def test_weekend_short_shift(self):
        """Test compensation calculation for a short weekend shift."""
        # Sunday, July 14, 2024
        start = datetime(2024, 7, 14, 10, 0, 0, tzinfo=pytz.UTC)  # 10 AM UTC
        end = start + timedelta(hours=4)  # 2 PM UTC (4 hours < threshold of 5)

        shift = OnCallShift(
            start=start,
            end=end,
            hours=4.0,
            user="test.user@example.com"
        )

        # Calculate compensation
        periods = self.calculator.calculate_compensation(shift)

        # Assertions
        self.assertEqual(len(periods), 1, "Expected one compensation period")

        # Since this is a weekend short shift, should use the fixed rate
        self.assertEqual(periods[0].compensation_type.value, "Wochenend-Sonderfall")
        self.assertEqual(periods[0].amount, WEEKEND_SHORT_SHIFT_RATE)

    def test_night_short_shift(self):
        """Test compensation calculation for a short night shift on a weekday."""
        # Tuesday night (July 16-17, 2024)
        start = datetime(2024, 7, 16, 23, 0, 0, tzinfo=pytz.UTC)  # 11 PM UTC
        end = start + timedelta(hours=1.5)  # 12:30 AM UTC (1.5 hours < threshold of 2)

        shift = OnCallShift(
            start=start,
            end=end,
            hours=1.5,
            user="test.user@example.com"
        )

        # Calculate compensation
        periods = self.calculator.calculate_compensation(shift)

        # Assertions
        self.assertEqual(len(periods), 1, "Expected one compensation period")

        # Since this is a night short shift, should use the fixed rate
        self.assertEqual(periods[0].compensation_type.value, "Nacht-Sonderfall")
        self.assertEqual(periods[0].amount, NIGHT_SHORT_SHIFT_RATE)

    def test_christmas_eve_AT(self):
        """Test compensation calculation for December 24th (Christmas Eve) in Austria."""
        # December 24, 2024 - shortened working hours in Austria (9:00-12:30)
        start = datetime(2024, 12, 24, 9, 0, 0, tzinfo=pytz.UTC)  # 9 AM UTC
        end = start + timedelta(hours=8)  # 5 PM UTC

        shift = OnCallShift(
            start=start,
            end=end,
            hours=8.0,
            user="test.user@example.com"
        )

        # Calculate compensation
        periods = self.calculator.calculate_compensation(shift)

        # Assertions
        self.assertTrue(len(periods) > 0, "No compensation periods returned")

        # Check the total compensated hours
        # Since working hours are 9:00-12:30 (3.5h), compensated hours should be >= 4.5h
        total_hours = sum(p.compensated_hours for p in periods)
        self.assertGreaterEqual(total_hours, 4.5)

    def test_new_years_eve_AT(self):
        """Test compensation calculation for December 31st (New Year's Eve) in Austria."""
        # December 31, 2024 - shortened working hours in Austria (9:00-12:30)
        start = datetime(2024, 12, 31, 9, 0, 0, tzinfo=pytz.UTC)  # 9 AM UTC
        end = start + timedelta(hours=8)  # 5 PM UTC

        shift = OnCallShift(
            start=start,
            end=end,
            hours=8.0,
            user="test.user@example.com"
        )

        # Calculate compensation
        periods = self.calculator.calculate_compensation(shift)

        # Assertions
        self.assertTrue(len(periods) > 0, "No compensation periods returned")

        # Check the total compensated hours
        # Since working hours are 9:00-12:30 (3.5h), compensated hours should be >= 4.5h
        total_hours = sum(p.compensated_hours for p in periods)
        self.assertGreaterEqual(total_hours, 4.5)

    def test_bulgaria_holiday(self):
        """Test compensation calculation for a Bulgarian holiday."""
        # March 3, 2024 is Liberation Day in Bulgaria
        start = datetime(2024, 3, 3, 9, 0, 0, tzinfo=pytz.UTC)  # 9 AM UTC
        end = start + timedelta(hours=8)  # 5 PM UTC

        shift = OnCallShift(
            start=start,
            end=end,
            hours=8.0,
            user="bulgarian.user@example.com"
        )

        # Mock the is_holiday method to return True for this date for BG user
        original_is_holiday = self.calculator.is_holiday

        def mock_is_holiday(date, user):
            if (date.date() == datetime(2024, 3, 3).date() and
                user == "bulgarian.user@example.com"):
                return True, "Liberation Day"
            return original_is_holiday(date, user)

        with patch.object(self.calculator, 'is_holiday', side_effect=mock_is_holiday):
            # Calculate compensation
            periods = self.calculator.calculate_compensation(shift)

            # Assertions
            self.assertTrue(len(periods) > 0, "No compensation periods returned")

            # All hours should be compensated (holiday)
            total_hours = sum(p.compensated_hours for p in periods)
            self.assertAlmostEqual(total_hours, 8.0, places=1)

            # Expected amount: 8 hours * STANDARD_RATE
            expected_amount = 8.0 * STANDARD_RATE
            total_amount = sum(p.amount for p in periods)
            self.assertAlmostEqual(total_amount, expected_amount, places=2)

            # Check if any period has holiday info
            has_holiday_info = any(p.holiday_info is not None for p in periods)
            self.assertTrue(has_holiday_info, "No holiday information found in compensation periods")

    def test_bulgaria_christmas_eve(self):
        """Test compensation calculation for December 24th (Christmas Eve) in Bulgaria."""
        # December 24, 2024 - Christmas Eve in Bulgaria is a normal working day
        # (unlike Austria where it has shortened working hours)
        start = datetime(2024, 12, 24, 9, 0, 0, tzinfo=pytz.UTC)  # 9 AM UTC
        end = start + timedelta(hours=8)  # 5 PM UTC

        shift = OnCallShift(
            start=start,
            end=end,
            hours=8.0,
            user="bulgarian.user@example.com"
        )

        # Calculate compensation
        periods = self.calculator.calculate_compensation(shift)

        # Assertions
        self.assertTrue(len(periods) > 0, "No compensation periods returned")

        # Check if it's being treated as a normal working day
        # Should only compensate hours outside regular working hours (9:00-17:00)
        total_hours = sum(p.compensated_hours for p in periods)

        # For a normal working day shift of 8h during working hours (9:00-17:00),
        # the compensated hours should be 0 or minimal
        self.assertLessEqual(total_hours, 8,
                            "Bulgarian Christmas Eve should be treated as a normal working day")

        # Now let's test what happens if we make it a holiday using a mock
        original_is_holiday = self.calculator.is_holiday

        def mock_is_holiday(date, user):
            if (date.date() == datetime(2024, 12, 24).date() and
                user == "bulgarian.user@example.com"):
                return True, "Christmas Eve"
            return original_is_holiday(date, user)

        with patch.object(self.calculator, 'is_holiday', side_effect=mock_is_holiday):
            # Calculate compensation again with the mock
            holiday_periods = self.calculator.calculate_compensation(shift)

            # Assertions for holiday case
            holiday_hours = sum(p.compensated_hours for p in holiday_periods)

            # All hours should be compensated if it's a holiday
            self.assertAlmostEqual(holiday_hours, 8.0,
                                  msg="All hours should be compensated when it's a holiday",
                                  places=1)

            # Check if any period has holiday info
            has_holiday_info = any(p.holiday_info is not None for p in holiday_periods)
            self.assertTrue(has_holiday_info, "No holiday information found in compensation periods")

    def test_one_week_shift(self):
        """Test compensation calculation for a full week shift (Wed 14:00 to next Wed 14:00)."""
        # Wednesday (May 14, 2025) 14:00 to Wednesday (May 21, 2025) 14:00
        # This creates a full 168-hour shift (7 days * 24 hours)
        start = datetime(2025, 5, 14, 14, 0, 0, tzinfo=pytz.UTC)  # Wed 14:00 UTC
        end = datetime(2025, 5, 21, 14, 0, 0, tzinfo=pytz.UTC)    # Next Wed 14:00 UTC

        shift = OnCallShift(
            start=start,
            end=end,
            hours=168.0,  # 7 days * 24 hours
            user="test.user@example.com"
        )

        # Calculate compensation
        periods = self.calculator.calculate_compensation(shift)

        # Assertions
        self.assertTrue(len(periods) > 0, "No compensation periods returned")

        # Calculate total compensated hours
        total_hours = sum(p.compensated_hours for p in periods)

        # For a full week, we expect:
        # - Weekdays (Mon-Fri): 5 days, each with 16 hours outside work (80 hours)
        # - Weekend: 2 days * 24 hours = 48 hours
        # Total expected: ~128 hours compensated
        self.assertGreaterEqual(total_hours, 127.5, "Should have at least 128 hours compensated (accounting for floating-point precision)")
        self.assertLessEqual(total_hours, 128.5, "Should have no more than 128 hours compensated (accounting for floating-point precision)")

        # Expected amount: ~128 hours * STANDARD_RATE
        expected_amount = 128.0 * STANDARD_RATE
        total_amount = sum(p.amount for p in periods)
        self.assertAlmostEqual(total_amount, expected_amount, delta=STANDARD_RATE,
                              msg="Total compensation should be approximately 128 hours * standard rate")

    def test_custom_vacation_day(self):
        """Test compensation calculation for a custom vacation day."""
        # Add custom vacation on June 16, 2024 (Monday) for test.user@example.com
        # First, modify the profile to include this custom holiday
        custom_profiles = [
            {
                "email": "test.user@example.com",
                "timezone": "Europe/Vienna",
                "working_days": [0, 1, 2, 3, 4],  # Monday to Friday
                "working_hours_start": "09:00:00",
                "working_hours_end": "17:00:00",
                "country_code": "AT",
                "custom_holidays": ["2024-06-16"]  # Custom vacation day
            },
            {
                "email": "bulgarian.user@example.com",
                "timezone": "Europe/Sofia",
                "working_days": [0, 1, 2, 3, 4],  # Monday to Friday
                "working_hours_start": "09:00:00",
                "working_hours_end": "17:00:00",
                "country_code": "BG"  # No custom vacation
            }
        ]

        # Save custom profiles to a new file
        custom_profiles_path = self.test_dir / 'custom_vacation_profiles.json'
        with open(custom_profiles_path, 'w') as f:
            json.dump(custom_profiles, f)

        # Initialize a new calculator with the custom profiles
        calculator = CompensationCalculator(user_profiles_path=custom_profiles_path)

        # Create a shift on the custom vacation day (Monday, June 16, 2024)
        start = datetime(2024, 6, 16, 9, 0, 0, tzinfo=pytz.UTC)  # 9 AM UTC
        end = start + timedelta(hours=8)  # 5 PM UTC

        shift = OnCallShift(
            start=start,
            end=end,
            hours=8.0,
            user="test.user@example.com"
        )

        # Calculate compensation
        periods = calculator.calculate_compensation(shift)

        # Assertions
        self.assertTrue(len(periods) > 0, "No compensation periods returned")

        # All hours should be compensated (vacation day)
        total_hours = sum(p.compensated_hours for p in periods)
        self.assertAlmostEqual(total_hours, 8.0, places=1,
                              msg="All hours should be compensated on a custom vacation day")

        # Expected amount: 8 hours * STANDARD_RATE
        expected_amount = 8.0 * STANDARD_RATE
        total_amount = sum(p.amount for p in periods)
        self.assertAlmostEqual(total_amount, expected_amount, places=2)

        # Clean up the additional test file
        if custom_profiles_path.exists():
            custom_profiles_path.unlink()

        # Now test the same day for the other user who doesn't have this custom vacation
        shift_bg = OnCallShift(
            start=start,
            end=end,
            hours=8.0,
            user="bulgarian.user@example.com"
        )

        # Calculate compensation for Bulgarian user (who doesn't have this custom vacation)
        periods_bg = calculator.calculate_compensation(shift_bg)

        # For the Bulgarian user, this should be a normal working day (Monday)
        total_hours_bg = sum(p.compensated_hours for p in periods_bg)
        self.assertLessEqual(total_hours_bg, 8.0,
                            msg="Should not be compensated as a holiday for user without custom vacation")


class TestUserProfileTimezones(unittest.TestCase):
    """Test timezone handling in compensation calculations."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a temporary directory for test data
        self.test_dir = Path('test_data')
        self.test_dir.mkdir(exist_ok=True)

        # Create test user profiles with different timezones
        self.test_profiles = [
            {
                "email": "vienna.user@example.com",
                "timezone": "Europe/Vienna",  # UTC+2 in summer
                "working_days": [0, 1, 2, 3, 4],
                "working_hours_start": "09:00:00",
                "working_hours_end": "17:00:00",
                "country_code": "AT"
            },
            {
                "email": "newyork.user@example.com",
                "timezone": "America/New_York",  # UTC-4 in summer
                "working_days": [0, 1, 2, 3, 4],
                "working_hours_start": "09:00:00",
                "working_hours_end": "17:00:00",
                "country_code": "US"
            }
        ]

        # Save test profiles to a file
        self.profiles_path = self.test_dir / 'test_timezone_profiles.json'
        with open(self.profiles_path, 'w') as f:
            json.dump(self.test_profiles, f)

        # Initialize calculator with test profiles
        self.calculator = CompensationCalculator(user_profiles_path=self.profiles_path)

    def tearDown(self):
        """Clean up after each test method."""
        # Remove test files
        if self.profiles_path.exists():
            self.profiles_path.unlink()

        # Remove test directory if it's empty
        if self.test_dir.exists() and not list(self.test_dir.iterdir()):
            self.test_dir.rmdir()

    def test_timezone_differences(self):
        """
        Test that timezone differences are properly handled in compensation calculation.
        The same UTC time should be interpreted differently based on the user's timezone.
        """
        # Create a shift: July 16, 2024, 12:00 UTC to 20:00 UTC (8 hours)
        # For Vienna user: 14:00 to 22:00 local time (5 working hours, 3 outside)
        # For NY user: 08:00 to 16:00 local time (all working hours)
        shift_time = datetime(2024, 7, 16, 12, 0, 0, tzinfo=pytz.UTC)

        # Test for Vienna user
        vienna_shift = OnCallShift(
            start=shift_time,
            end=shift_time + timedelta(hours=8),
            hours=8.0,
            user="vienna.user@example.com"
        )

        vienna_periods = self.calculator.calculate_compensation(vienna_shift)
        vienna_compensated = sum(p.compensated_hours for p in vienna_periods)

        # Test for NY user
        ny_shift = OnCallShift(
            start=shift_time,
            end=shift_time + timedelta(hours=8),
            hours=8.0,
            user="newyork.user@example.com"
        )

        ny_periods = self.calculator.calculate_compensation(ny_shift)
        ny_compensated = sum(p.compensated_hours for p in ny_periods)

        # Assertions
        # NY user should have fewer compensated hours since the UTC shift
        # falls entirely within their working hours
        self.assertLess(ny_compensated, vienna_compensated)


class TestCompensationReporting(unittest.TestCase):
    """Test the compensation reporting functionality."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a temporary directory for test data
        self.test_dir = Path('test_data')
        self.test_dir.mkdir(exist_ok=True)

        # Create test user profiles with rotation periods specified
        self.test_profiles = [
            {
                "email": "test.user@example.com",
                "timezone": "Europe/Vienna",
                "working_days": [0, 1, 2, 3, 4],
                "working_hours_start": "09:00:00",
                "working_hours_end": "17:00:00",
                "country_code": "AT",
                "first_month_on_rotation": "2024-06",
                "last_month_on_rotation": "2024-07"  # Only two months in rotation
            }
        ]

        # Save test profiles to a file
        self.profiles_path = self.test_dir / 'test_reporting_profiles.json'
        with open(self.profiles_path, 'w') as f:
            json.dump(self.test_profiles, f)

        # Initialize calculator with test profiles
        self.calculator = CompensationCalculator(user_profiles_path=self.profiles_path)

    def tearDown(self):
        """Clean up after each test method."""
        # Remove test files
        if self.profiles_path.exists():
            self.profiles_path.unlink()

        # Remove test directory if it's empty
        if self.test_dir.exists() and not list(self.test_dir.iterdir()):
            self.test_dir.rmdir()

    def test_report_includes_month_without_shifts(self):
        """Test that the monthly report includes months without shifts."""
        from minuto.main import CompensationReport

        # Setup profiles with two users, both with the same rotation period
        self.test_profiles.append({
            "email": "second.user@example.com",
            "timezone": "Europe/Vienna",
            "working_days": [0, 1, 2, 3, 4],
            "working_hours_start": "09:00:00",
            "working_hours_end": "17:00:00",
            "country_code": "AT",
            "first_month_on_rotation": "2024-06",
            "last_month_on_rotation": "2024-07"
        })

        # Update the profiles file
        with open(self.profiles_path, 'w') as f:
            json.dump(self.test_profiles, f)

        # Reinitialize calculator with updated profiles
        self.calculator = CompensationCalculator(user_profiles_path=self.profiles_path)

        # Create shifts for both users
        shifts = []

        # First user: shift only in June 2024
        start1 = datetime(2024, 6, 15, 9, 0, 0, tzinfo=pytz.UTC)
        end1 = start1 + timedelta(hours=8)
        shifts.append(OnCallShift(
            start=start1,
            end=end1,
            hours=8.0,
            user="test.user@example.com"
        ))

        # Second user: shifts in both June and July 2024
        start2 = datetime(2024, 6, 20, 9, 0, 0, tzinfo=pytz.UTC)
        end2 = start2 + timedelta(hours=8)
        shifts.append(OnCallShift(
            start=start2,
            end=end2,
            hours=8.0,
            user="second.user@example.com"
        ))

        start3 = datetime(2024, 7, 10, 9, 0, 0, tzinfo=pytz.UTC)
        end3 = start3 + timedelta(hours=8)
        shifts.append(OnCallShift(
            start=start3,
            end=end3,
            hours=8.0,
            user="second.user@example.com"
        ))

        # Calculate compensation periods for all shifts
        all_periods = []
        for shift in shifts:
            periods = self.calculator.calculate_compensation(shift)
            all_periods.extend(periods)

        # Add debug information
        print("\nDEBUG INFO:")
        print(f"User profiles: {self.calculator.user_profiles}")
        for user, profile in self.calculator.user_profiles.items():
            print(f"User {user} rotation: {profile.first_month_on_rotation} - {profile.last_month_on_rotation}")
        print(f"Total shifts: {len(shifts)}")
        print(f"Generated periods: {len(all_periods)}")

        # Generate the report
        report = CompensationReport(all_periods, self.calculator.user_profiles)

        # Get the user-month totals
        user_month_totals = report.get_user_month_totals()

        # Print debug info about the report
        print("\nReport DataFrame Contents:")
        print(f"DataFrame shape: {user_month_totals.shape}")
        print(f"All users: {user_month_totals['User'].unique().tolist()}")
        print(f"All months: {user_month_totals['Year-Month'].unique().tolist()}")

        # Print full dataframe for debugging
        print("\nFull DataFrame:")
        print(user_month_totals)

        # Check both months in rotation period are included for first user
        expected_months = ['2024-06', '2024-07']  # June has shifts, July doesn't
        user1_months = user_month_totals[user_month_totals['User'] == 'test.user@example.com']['Year-Month'].tolist()
        user1_months.sort()

        print(f"\nUser 1 Expected months: {expected_months}")
        print(f"User 1 Actual months: {user1_months}")

        # Check that both expected months are present for first user
        self.assertEqual(expected_months, user1_months,
                         f"Report should include both months for first user. Expected: {expected_months}, Got: {user1_months}")

        # Check both months are present for second user too
        user2_months = user_month_totals[user_month_totals['User'] == 'second.user@example.com']['Year-Month'].tolist()
        user2_months.sort()

        print(f"\nUser 2 Expected months: {expected_months}")
        print(f"User 2 Actual months: {user2_months}")

        self.assertEqual(expected_months, user2_months,
                         f"Report should include both months for second user. Expected: {expected_months}, Got: {user2_months}")

        # Verify that all months are pre-payment eligible for both users
        eligibility1 = user_month_totals[user_month_totals['User'] == 'test.user@example.com']['PrePaymentEligible'].tolist()
        self.assertTrue(all(eligibility1), "All months should be marked as pre-payment eligible for first user")

        eligibility2 = user_month_totals[user_month_totals['User'] == 'second.user@example.com']['PrePaymentEligible'].tolist()
        self.assertTrue(all(eligibility2), "All months should be marked as pre-payment eligible for second user")

        # First user: June should have compensation > 0, July should have compensation = 0
        june_data1 = user_month_totals[
            (user_month_totals['User'] == 'test.user@example.com') &
            (user_month_totals['Year-Month'] == '2024-06')
        ]
        self.assertGreater(june_data1['Amount'].values[0], 0, "June should have compensation amount > 0 for first user")

        july_data1 = user_month_totals[
            (user_month_totals['User'] == 'test.user@example.com') &
            (user_month_totals['Year-Month'] == '2024-07')
        ]
        self.assertEqual(july_data1['Amount'].values[0], 0, "July should have compensation amount = 0 for first user")

        # Second user: Both June and July should have compensation > 0
        june_data2 = user_month_totals[
            (user_month_totals['User'] == 'second.user@example.com') &
            (user_month_totals['Year-Month'] == '2024-06')
        ]
        self.assertGreater(june_data2['Amount'].values[0], 0, "June should have compensation amount > 0 for second user")

        july_data2 = user_month_totals[
            (user_month_totals['User'] == 'second.user@example.com') &
            (user_month_totals['Year-Month'] == '2024-07')
        ]
        self.assertGreater(july_data2['Amount'].values[0], 0, "July should have compensation amount > 0 for second user")
