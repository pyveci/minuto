#!/usr/bin/env python3
"""
Tests for timezone conversion functionality in minuto.

These tests specifically verify that UTC times from OpsGenie shifts are
correctly converted to user's local timezone, with a focus on Asia/Bangkok.
"""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pytz

from minuto.main import CompensationCalculator, OnCallShift, UserProfile


class TestTimezoneConversion(unittest.TestCase):
    """Tests for timezone conversion in the CompensationCalculator"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a temporary directory for test data
        self.test_dir = Path('test_data')
        self.test_dir.mkdir(exist_ok=True)

        # Create a test profile for a user in Bangkok
        self.bangkok_profile = {
            "email": "bangkok.user@example.com",
            "timezone": "Asia/Bangkok",  # UTC+7
            "working_days": [0, 1, 2, 3, 4],  # Monday to Friday
            "working_hours_start": "09:00:00",
            "working_hours_end": "17:00:00",
            "country_code": "TH"  # Thailand
        }

        # Save test profile to a file
        self.profiles_path = self.test_dir / 'bangkok_profile.json'
        with open(self.profiles_path, 'w') as f:
            json.dump([self.bangkok_profile], f)

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

    def test_bangkok_timezone_conversion(self):
        """
        Test that UTC times are correctly converted to Asia/Bangkok timezone.
        Bangkok is UTC+7, so 12:00 UTC should be 19:00 Bangkok time.
        """
        # Create a test shift at 12:00 UTC
        utc_time = datetime(2024, 7, 15, 12, 0, 0, tzinfo=pytz.UTC)  # Monday, 12:00 UTC
        shift = OnCallShift(
            start=utc_time,
            end=utc_time + timedelta(hours=2),
            hours=2.0,
            user="bangkok.user@example.com"
        )

        # Get the local time for this user
        bangkok_time = self.calculator.get_user_local_time(utc_time, shift.user)

        # Bangkok is UTC+7, so 12:00 UTC should be 19:00 Bangkok time
        self.assertEqual(bangkok_time.hour, 19, "UTC 12:00 should be 19:00 in Bangkok")
        self.assertEqual(bangkok_time.tzinfo.zone, "Asia/Bangkok", "Timezone should be Asia/Bangkok")

    def test_bangkok_working_hours_check(self):
        """
        Test that working hours are correctly evaluated based on Bangkok time,
        not UTC time.
        """
        # Create times for testing
        utc_time_during_bkk_work = datetime(2024, 7, 15, 3, 0, 0, tzinfo=pytz.UTC)  # 10:00 Bangkok
        utc_time_outside_bkk_work = datetime(2024, 7, 15, 12, 0, 0, tzinfo=pytz.UTC)  # 19:00 Bangkok

        # Check if these times are considered working hours
        is_working_during = self.calculator.is_working_hours(
            self.calculator.get_user_local_time(utc_time_during_bkk_work, "bangkok.user@example.com"),
            "bangkok.user@example.com"
        )
        is_working_outside = self.calculator.is_working_hours(
            self.calculator.get_user_local_time(utc_time_outside_bkk_work, "bangkok.user@example.com"),
            "bangkok.user@example.com"
        )

        # Assertions
        self.assertTrue(is_working_during, "10:00 Bangkok time should be within working hours")
        self.assertFalse(is_working_outside, "19:00 Bangkok time should be outside working hours")

    def test_bangkok_compensation_calculation(self):
        """
        Test that compensation is correctly calculated based on Bangkok timezone.
        A shift during Bangkok working hours should have no compensated hours,
        while a shift outside working hours should be fully compensated.
        """
        # Shift during Bangkok working hours (10:00-12:00 Bangkok time)
        utc_work_shift = OnCallShift(
            start=datetime(2024, 7, 15, 3, 0, 0, tzinfo=pytz.UTC),  # 10:00 Bangkok
            end=datetime(2024, 7, 15, 5, 0, 0, tzinfo=pytz.UTC),    # 12:00 Bangkok
            hours=2.0,
            user="bangkok.user@example.com"
        )

        # Shift outside Bangkok working hours (19:00-21:00 Bangkok time)
        utc_evening_shift = OnCallShift(
            start=datetime(2024, 7, 15, 12, 0, 0, tzinfo=pytz.UTC),  # 19:00 Bangkok
            end=datetime(2024, 7, 15, 14, 0, 0, tzinfo=pytz.UTC),    # 21:00 Bangkok
            hours=2.0,
            user="bangkok.user@example.com"
        )

        # Calculate compensation for both shifts
        work_periods = self.calculator.calculate_compensation(utc_work_shift)
        evening_periods = self.calculator.calculate_compensation(utc_evening_shift)

        # Assertions for work shift
        work_compensated_hours = sum(p.compensated_hours for p in work_periods)
        self.assertAlmostEqual(work_compensated_hours, 0.0, places=1,
                              msg="Shift during Bangkok working hours should have 0 compensated hours")

        # Assertions for evening shift
        evening_compensated_hours = sum(p.compensated_hours for p in evening_periods)
        self.assertAlmostEqual(evening_compensated_hours, 2.0, places=1,
                              msg="Shift outside Bangkok working hours should have 2 compensated hours")

    def test_crossing_midnight_in_bangkok(self):
        """
        Test a shift that crosses midnight in Bangkok but not in UTC.

        UTC 15:00-18:00 = Bangkok 22:00-01:00 (next day)
        This tests correct date handling in the Asia/Bangkok timezone.
        """
        # Create a shift that crosses midnight in Bangkok
        utc_shift = OnCallShift(
            start=datetime(2024, 7, 15, 15, 0, 0, tzinfo=pytz.UTC),  # 22:00 Bangkok
            end=datetime(2024, 7, 15, 18, 0, 0, tzinfo=pytz.UTC),    # 01:00 Bangkok (next day)
            hours=3.0,
            user="bangkok.user@example.com"
        )

        # Calculate compensation
        periods = self.calculator.calculate_compensation(utc_shift)

        # The shift should be fully compensated (outside working hours)
        total_compensated_hours = sum(p.compensated_hours for p in periods)
        self.assertAlmostEqual(total_compensated_hours, 3.0, places=1,
                              msg="Shift crossing midnight in Bangkok should have 3 compensated hours")

        # Check that dates are correctly handled
        bangkok_tz = pytz.timezone("Asia/Bangkok")
        start_bangkok = utc_shift.start.astimezone(bangkok_tz)
        end_bangkok = utc_shift.end.astimezone(bangkok_tz)

        # Verify we've crossed to the next day in Bangkok
        self.assertEqual(start_bangkok.day, 15, "Start day should be the 15th in Bangkok")
        self.assertEqual(end_bangkok.day, 16, "End day should be the 16th in Bangkok")


if __name__ == '__main__':
    unittest.main()
