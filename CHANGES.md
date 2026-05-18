# Changelog

## 2026-05-08

### Changed

- **XLSX export rewritten for HR audit**: every aggregated number in the workbook is now a live Excel formula (`=SUM`, `=SUMIFS`, `=SUBTOTAL`) over named tables (`tbl_shifts`, `tbl_monthly`, `tbl_daily`), so HR can click any total and see how it was derived. New `Overview` sheet leads with the report period, the monthly pre-pay base (`PrePay = € 510.00`, exposed as a workbook-named cell and referenced by the Monthly sheet's formulas), and a totals block driven entirely by formulas against the data tables. Sheets reordered top-down: Overview → Monthly Per User → Daily Summary → Detailed Shifts.
- **Excel polish**: native Excel Tables (banded rows + autofilter), frozen header rows, page setup for printing (landscape, fit-to-width, repeat header row, footer with page number), conditional row highlighting on Detailed Shifts (light yellow for holidays, light gray for weekends), proper number formats (`€ #,##0.00`, `0.00`, `yyyy-mm-dd`, `yyyy-mm-dd hh:mm`), and native Excel datetimes (no more stringified dates).
- **`MONTHLY_PREPAY_AMOUNT` constant**: the monthly pre-pay (set yearly by collective agreement) is now a single module-level constant in `main.py`, replacing two hardcoded `510.0` literals. Update at year boundary.

### Fixed

- **Total Pre-Paid on the Summary sheet ignored eligibility**: the previous code multiplied `510 × len(unique_user_months)`, counting months where `PrePaymentEligible=False`. Now the Overview's "Total pre-paid" is `=SUM(tbl_monthly[Pre-Paid Amount])`, which by construction respects the per-row eligibility flag.

## 2026-05-05

### Added

- **JSM Ops support**: New `jsm` CLI subcommand fetches on-call shifts from the post-migration Jira Service Management Operations API. Mirrors the existing `opsgenie` command flag-for-flag and produces identical CSV output for the migration window — verified end-to-end against the live tenant.
- **`--historical-only` flag** on the `jsm` command: skips `active` and `forecast` periods (only counts shifts that have actually been served). Recommended for mid-period payroll runs. Default behavior includes all periods overlapping the window, matching the `opsgenie` command.
- **`OPSGENIE_API_KEY` envvar fallback**: The `opsgenie` command now reads `OPSGENIE_API_KEY` in addition to `OPSGENIE_API_TOKEN`, aligning env var naming with the rest of the Crate infra tooling. README now documents both names.
- **`JSM_*` envvars for JSM command options**: `JSM_SAVE_CSV`, `JSM_USER_PROFILES`, `JSM_OUTPUT_PLOT`, `JSM_EXPORT_EXCEL`, `JSM_HISTORICAL_ONLY` are now recognized, each falling back to the `OPSGENIE_*` equivalent when unset. Lets users adopt JSM-only naming without reusing OpsGenie env vars.

### Changed

- **Unresolved-user handling is now fail-fast**: if any Atlassian account ID returned by JSM can't be resolved to an email (e.g. API token lacks the privacy scope, or the user has hidden their email), the command exits non-zero with the full list of unresolved IDs. Previously a placeholder email was substituted, which risked landing fake users in compensation reports.
- **`OnCallShift` moved to `src/minuto/models.py`**: the data-exchange model now lives in its own module so source-specific clients can import it without going through `main`. Eliminates the deferred-import workaround that the JSM command was using. `from minuto.main import OnCallShift` continues to work via re-export — no change needed in tests or external consumers.
- **CLI date-window parsing extracted to `_parse_window`**: the `--start-date` / `--end-date` parsing logic (including the end-of-day default for date-only input) is now a single helper used by both `opsgenie` and `jsm` commands, instead of being duplicated.

### Notes

- Schedule UUIDs from OpsGenie carry over 1:1 to JSM, so `JSM_SCHEDULE_ID` defaults to `OPSGENIE_SCHEDULE_ID` when unset.
- JSM responses contain Atlassian account IDs instead of emails; the new module resolves them via the Jira `/rest/api/3/user` endpoint, in a single pre-pass so unresolvable users surface together rather than failing one at a time.

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
