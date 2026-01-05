# Changelog

## 2026-01-05

### Fixed

- **End-of-year boundary handling**: Date-only input for `--end-date` now defaults to end-of-day (23:59:59) instead of midnight (00:00:00), ensuring shifts starting later on the end date are properly included in reports.

- **Holiday detection**: Resolved critical issue where country-specific holidays from the Python `holidays` package were not being detected due to improper handling of lazy-loaded holiday data. All holidays are now correctly identified and displayed in compensation reports.

- **File validation**: Added validation for `--user-profiles` parameter to display clear error message when specified file does not exist, preventing silent failures that resulted in missing holiday calculations.

### Changed

- Updated documentation to consistently reference `user_profiles.json` as the user profiles filename throughout README.md examples.
