# Changelog

## 2026-01-05

### Added

- **ASCII bar chart visualization** in compliance checker (`check_oncall_rules.py`): Added horizontal bar chart showing hours over the 168-hour monthly limit, with color-coded bars (red for violations) and automatic scaling. Appears between compliance summary and detailed breakdown.

### Fixed

- **End-of-year boundary handling**: Date-only input for `--end-date` now defaults to end-of-day (23:59:59) instead of midnight (00:00:00), ensuring shifts starting later on the end date are properly included in reports.

- **Holiday detection**: Resolved critical issue where country-specific holidays from the Python `holidays` package were not being detected due to improper handling of lazy-loaded holiday data. All holidays are now correctly identified and displayed in compensation reports.

- **File validation**: Added validation for `--user-profiles` parameter to display clear error message when specified file does not exist, preventing silent failures that resulted in missing holiday calculations.

- **Rufbereitschaften counting** in compliance checker: Fixed critical misinterpretation of Kollektivvertrag rules. Now correctly counts unique calendar days with on-call duty (not CSV rows) as Rufbereitschaften. One Rufbereitschaft = one calendar day where the person had on-call duty, regardless of hours or number of CSV entries for that day.

- **Three-month period check**: Fixed quarterly compliance check to examine all consecutive 3-month calendar periods (e.g., Jan-Feb-Mar, Feb-Mar-Apr) instead of only checking months where shifts occurred. Now correctly identifies violations even when shifts span only 1-2 months in the period.

- **Unique days calculation**: Fixed `all_days` summary statistic to count unique calendar days instead of summing overlapping days from consecutive shifts, providing accurate day counts when OpsGenie splits continuous periods into multiple CSV rows.

### Changed

- Updated documentation to consistently reference `user_profiles.json` as the user profiles filename throughout README.md examples.

- **Compliance checker report format**: Renamed "DETAILED VIOLATIONS" section to "DETAILED MONTHLY BREAKDOWN" and now displays all months for each user with clear visual indicators (✓ green for compliant, ✗ red for violations). Shows exact violation reasons (days/hours over limit) and includes CSV row counts for transparency.

- **Compliance checker documentation**: Updated all documentation strings and output messages to clarify that "10 Rufbereitschaften per month" means 10 calendar days with on-call duty, not 10 shifts or CSV rows. Added note explaining the Rufbereitschaften concept from the Kollektivvertrag.
