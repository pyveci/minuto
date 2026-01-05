#!/usr/bin/env python3
"""
On-call Shift Rules Compliance Checker

This standalone script analyzes on-call shifts data from a CSV file
and verifies compliance with the following rules (Kollektivvertrag):

1. Maximum 10 Rufbereitschaften per month (= 10 calendar days with on-call duty)
2. Maximum 168 hours of on-call per month
3. Maximum 30 on-call days in any three-month period

Note: One "Rufbereitschaft" = one calendar day where the person had on-call duty,
regardless of how many hours or how many separate shifts occurred on that day.

The script generates a report that highlights any violations and
provides a summary of compliance status for each user.
"""

from datetime import datetime, timedelta
import sys
from typing import Dict, Set, Any

import click
import pandas as pd
import pytz


def load_shifts_from_csv(csv_path: str) -> pd.DataFrame:
    """
    Load on-call shifts from a CSV file into a pandas DataFrame

    Args:
        csv_path: Path to the CSV file

    Returns:
        DataFrame with shifts data
    """
    try:
        # Read the CSV file
        df = pd.read_csv(csv_path)

        # Check if required columns exist
        required_cols = ['start', 'end', 'hours', 'user']
        missing_cols = [col for col in required_cols if col not in df.columns]

        if missing_cols:
            click.echo(f"Error: CSV file is missing required columns: {', '.join(missing_cols)}", err=True)
            sys.exit(1)

        # Convert start and end strings to datetime objects
        df['start'] = pd.to_datetime(df['start'])
        df['end'] = pd.to_datetime(df['end'])

        # Sort by start time
        df = df.sort_values('start')

        return df

    except Exception as e:
        click.echo(f"Error loading CSV file: {str(e)}", err=True)
        sys.exit(1)


def get_date_coverage(row, timezone: str) -> Set[datetime.date]:
    """
    Get all dates covered by a shift in the specified timezone

    Args:
        row: DataFrame row with start and end times
        timezone: Timezone string

    Returns:
        Set of dates covered by the shift
    """
    # Localize to specified timezone
    tz = pytz.timezone(timezone)
    start = row['start'].replace(tzinfo=pytz.UTC).astimezone(tz)
    end = row['end'].replace(tzinfo=pytz.UTC).astimezone(tz)

    # Generate all dates from start to end
    dates = set()
    current = start.date()
    end_date = end.date()

    while current <= end_date:
        dates.add(current)
        current += timedelta(days=1)

    return dates


def merge_consecutive_shifts(user_df: pd.DataFrame, max_gap_hours: float = 4.0) -> pd.DataFrame:
    """
    Merge consecutive shifts for the same user into continuous on-call periods.

    This handles cases where OpsGenie splits one continuous on-call assignment
    into multiple CSV rows due to overrides or schedule changes.

    Args:
        user_df: DataFrame with shifts for a single user
        max_gap_hours: Maximum gap in hours to consider shifts as continuous (default: 4.0)

    Returns:
        DataFrame with merged periods, each representing one Rufbereitschaft
    """
    if user_df.empty:
        return user_df

    # Sort by start time
    user_df = user_df.sort_values('start').reset_index(drop=True)

    merged_periods = []
    current_period = {
        'start': user_df.iloc[0]['start'],
        'end': user_df.iloc[0]['end'],
        'hours': user_df.iloc[0]['hours'],
        'dates': user_df.iloc[0]['dates'].copy(),
        'user': user_df.iloc[0]['user']
    }

    for i in range(1, len(user_df)):
        shift = user_df.iloc[i]
        gap_hours = (shift['start'] - current_period['end']).total_seconds() / 3600

        # If gap is small enough, merge this shift into current period
        if gap_hours <= max_gap_hours:
            current_period['end'] = max(current_period['end'], shift['end'])
            current_period['hours'] += shift['hours']
            current_period['dates'].update(shift['dates'])
        else:
            # Gap too large, save current period and start new one
            merged_periods.append(current_period)
            current_period = {
                'start': shift['start'],
                'end': shift['end'],
                'hours': shift['hours'],
                'dates': shift['dates'].copy(),
                'user': shift['user']
            }

    # Don't forget the last period
    merged_periods.append(current_period)

    # Convert back to DataFrame
    return pd.DataFrame(merged_periods)


def analyze_shifts(df: pd.DataFrame, timezone: str) -> Dict[str, Dict[str, Any]]:
    """
    Analyze on-call shifts to check compliance with rules

    Args:
        df: DataFrame with shifts data
        timezone: Timezone to use for date calculations

    Returns:
        Dictionary with compliance analysis results for each user
    """
    if df.empty:
        click.echo("No shifts data to analyze")
        return {}

    # Initialize results dictionary
    results = {}

    # Group data by user
    users = df['user'].unique()

    for user in users:
        user_df = df[df['user'] == user].copy()

        # Get all dates covered by each shift
        user_df['dates'] = user_df.apply(lambda row: get_date_coverage(row, timezone), axis=1)

        # Extract year and month from start dates
        user_df['year'] = user_df['start'].dt.year
        user_df['month'] = user_df['start'].dt.month
        user_df['year_month'] = user_df['start'].dt.strftime('%Y-%m')

        # Initialize user results
        # Calculate unique days across all shifts (deduplicating overlaps)
        all_unique_days = set()
        for dates in user_df['dates']:
            all_unique_days.update(dates)

        results[user] = {
            'monthly_violations': [],
            'monthly_data': [],  # All months (violations and compliant)
            'quarterly_violations': [],
            'summary': {},
            'all_shifts': len(user_df),
            'all_days': len(all_unique_days),
            'all_hours': user_df['hours'].sum()
        }

        # Check monthly limits (max 10 shifts per month, max 168 hours per month)
        monthly_data = {}

        for _, row in user_df.iterrows():
            year_month = row['year_month']
            if year_month not in monthly_data:
                monthly_data[year_month] = {
                    'shifts': 0,
                    'hours': 0,
                    'days': set(),
                    'year': row['year'],
                    'month': row['month']
                }

            monthly_data[year_month]['shifts'] += 1
            monthly_data[year_month]['hours'] += row['hours']
            monthly_data[year_month]['days'].update(row['dates'])

        # Process all monthly data and check for violations
        for ym, data in monthly_data.items():
            month_days = len(data['days'])
            month_name = datetime(data['year'], data['month'], 1).strftime('%Y %B')

            # A "Rufbereitschaft" = one calendar day with on-call duty
            # Max 10 Rufbereitschaften (days) per month, max 168 hours per month
            has_violation = month_days > 10 or data['hours'] > 168

            month_info = {
                'year': data['year'],
                'month': data['month'],
                'month_name': month_name,
                'shifts': data['shifts'],
                'days': month_days,
                'hours': data['hours'],
                'max_days': 10,
                'max_hours': 168,
                'has_violation': has_violation
            }

            # Store in both lists for easy access
            results[user]['monthly_data'].append(month_info)
            if has_violation:
                results[user]['monthly_violations'].append(month_info)

        # Check quarterly limits (max 30 days in any 3-month period)
        # Find the date range covered by all shifts
        if monthly_data:
            all_dates = set()
            for data in monthly_data.values():
                all_dates.update(data['days'])

            if all_dates:
                min_date = min(all_dates)
                max_date = max(all_dates)

                # Generate all possible 3-month calendar windows
                # Start from the month containing min_date
                start_year = min_date.year
                start_month = min_date.month
                end_year = max_date.year
                end_month = max_date.month

                # Iterate through each possible 3-month window
                current_year = start_year
                current_month = start_month

                while True:
                    # Define the 3-month window
                    window_start = datetime(current_year, current_month, 1).date()

                    # Calculate end of 3-month period (first day of 4th month)
                    third_month_year = current_year
                    third_month_num = current_month + 2
                    if third_month_num > 12:
                        third_month_year += 1
                        third_month_num -= 12

                    # End of window is last day of third month
                    fourth_month_year = third_month_year
                    fourth_month_num = third_month_num + 1
                    if fourth_month_num > 12:
                        fourth_month_year += 1
                        fourth_month_num -= 12

                    window_end = datetime(fourth_month_year, fourth_month_num, 1).date() - timedelta(days=1)

                    # Stop if window starts after max_date
                    if window_start > max_date:
                        break

                    # Count days with on-call in this window
                    days_in_window = {d for d in all_dates if window_start <= d <= window_end}

                    if len(days_in_window) > 30:
                        # Create readable period string
                        month1 = datetime(current_year, current_month, 1).strftime('%Y %B')

                        month2_year = current_year
                        month2_num = current_month + 1
                        if month2_num > 12:
                            month2_year += 1
                            month2_num -= 12
                        month2 = datetime(month2_year, month2_num, 1).strftime('%Y %B')

                        month3 = datetime(third_month_year, third_month_num, 1).strftime('%Y %B')

                        results[user]['quarterly_violations'].append({
                            'period': f'{month1} → {month2} → {month3}',
                            'days': len(days_in_window),
                            'max_days': 30
                        })

                    # Move to next month
                    current_month += 1
                    if current_month > 12:
                        current_year += 1
                        current_month = 1

                    # Stop if we've passed the range
                    if current_year > end_year or (current_year == end_year and current_month > end_month):
                        break

        # Build summary
        total_months = len(monthly_data)
        months_with_violations = len(results[user]['monthly_violations'])

        # Calculate total number of 3-month windows checked
        total_quarters = 0
        if monthly_data and all_dates:
            # Count months from first to last shift
            months_diff = (end_year - start_year) * 12 + (end_month - start_month) + 1
            # Number of 3-month windows = months - 2 (if at least 3 months)
            total_quarters = max(0, months_diff - 2)

        quarters_with_violations = len(results[user]['quarterly_violations'])

        results[user]['summary'] = {
            'total_months': total_months,
            'months_with_violations': months_with_violations,
            'total_quarters': total_quarters,
            'quarters_with_violations': quarters_with_violations,
            'compliant': (months_with_violations == 0 and quarters_with_violations == 0)
        }

    return results


def render_bar(value: float, max_value: float, width: int) -> str:
    """
    Render a single horizontal bar with block characters

    Args:
        value: The value to represent
        max_value: Maximum value for scaling
        width: Maximum bar width in characters

    Returns:
        String with block characters representing the value
    """
    if value <= 0:
        return ""

    # Calculate proportional width
    ratio = value / max_value
    full_blocks = int(ratio * width)
    remainder = (ratio * width) - full_blocks

    # Build bar with progressive characters
    bar = "█" * full_blocks

    # Add partial block based on remainder
    if remainder >= 0.75:
        bar += "▓"
    elif remainder >= 0.5:
        bar += "▒"
    elif remainder >= 0.25:
        bar += "░"

    return bar


def generate_violation_chart(results: Dict[str, Dict[str, Any]], chart_width: int = 60) -> str:
    """
    Generate ASCII horizontal bar chart showing hours violations per user per month

    Args:
        results: Analysis results dictionary from analyze_shifts()
        chart_width: Maximum width of bars in characters (default: 60)

    Returns:
        Formatted string containing the ASCII chart
    """
    # Step 1: Collect all violations data
    violation_matrix = {}  # {user: {month_key: violation_data}}
    all_months = set()

    for user, data in sorted(results.items()):
        violation_matrix[user] = {}
        for v in data['monthly_violations']:
            month_key = f"{v['year']}-{v['month']:02d}"
            all_months.add(month_key)

            # Calculate excess hours over the 168-hour limit
            excess_hours = max(0, v['hours'] - v['max_hours'])
            if excess_hours > 0:
                violation_matrix[user][month_key] = {
                    'excess_hours': excess_hours,
                    'total_hours': v['hours'],
                    'month_name': v['month_name']
                }

    # Handle edge case: no violations
    if not any(violation_matrix.values()):
        return "\n--- VIOLATIONS VISUALIZATION ---\n✓ No violations to display\n"

    # Step 2: Find max excess hours for scaling
    max_excess = max(
        data['excess_hours']
        for user_data in violation_matrix.values()
        for data in user_data.values()
    )

    # Step 3: Build chart
    lines = []
    lines.append("\n--- VIOLATIONS VISUALIZATION ---")
    lines.append("Hours over 168-hour monthly limit:")
    lines.append("")

    # For each user with violations
    sorted_months = sorted(all_months)
    for user in sorted(violation_matrix.keys()):
        user_data = violation_matrix[user]
        if not user_data:
            continue

        lines.append(f"{user}:")

        # For each month with violations
        for month_key in sorted_months:
            if month_key not in user_data:
                continue

            v = user_data[month_key]
            excess = v['excess_hours']
            total = v['total_hours']
            month_name = v['month_name']

            # Render and color the bar
            bar = render_bar(excess, max_excess, chart_width)
            colored_bar = click.style(bar, fg='red', bold=True)

            # Format line: "  2025 September  ███████░ 2.5h over (170.5h total)"
            lines.append(f"  {month_name:15} {colored_bar} {excess:.1f}h over ({total:.1f}h total)")

        lines.append("")  # Blank line between users

    # Add scale reference
    lines.append(f"Scale: Each full bar (60 chars) = {max_excess:.1f} hours over limit")
    lines.append("")

    return "\n".join(lines)


def print_report(results: Dict[str, Dict[str, Any]], verbose: bool = False):
    """
    Print compliance report to stdout

    Args:
        results: Analysis results dictionary
        verbose: Whether to print detailed information
    """
    if not results:
        click.echo("No data to report")
        return

    click.echo("\n=== ON-CALL RULES COMPLIANCE REPORT ===")
    click.echo("Rules (Kollektivvertrag):")
    click.echo("1. Maximum 10 Rufbereitschaften per month (= 10 days with on-call duty)")
    click.echo("2. Maximum 168 hours of on-call per month")
    click.echo("3. Maximum 30 on-call days in any 3-month period")
    click.echo("=" * 80)

    # Print summary table
    click.echo("\n--- COMPLIANCE SUMMARY ---")
    click.echo(f"{'User':<40} {'Status':<15} {'Months':<8} {'Violations':<12} {'Quarters':<8} {'Violations'}")
    click.echo("-" * 100)

    compliant_users = 0
    non_compliant_users = 0

    for user, data in sorted(results.items()):
        summary = data['summary']
        status = "✓ Compliant" if summary['compliant'] else "✗ Non-compliant"
        months = f"{summary['months_with_violations']}/{summary['total_months']}"
        quarters = f"{summary['quarters_with_violations']}/{summary['total_quarters']}"

        if summary['compliant']:
            compliant_users += 1
        else:
            non_compliant_users += 1

        click.echo(f"{user:<40} {status:<15} {months:<8} {'':<12} {quarters:<8}")

    click.echo(f"\nUsers in compliance: {compliant_users}")
    click.echo(f"Users with violations: {non_compliant_users}")

    # Display violations chart if there are any violations
    if non_compliant_users > 0:
        chart = generate_violation_chart(results)
        click.echo(chart)

    # Print detailed violations if any
    any_violations = False
    for user, data in results.items():
        if data['monthly_violations'] or data['quarterly_violations']:
            any_violations = True
            break

    # Always show detailed breakdown for all users
    click.echo("\n--- DETAILED MONTHLY BREAKDOWN ---")

    for user, data in sorted(results.items()):
        monthly_data_list = data['monthly_data']
        quarterly_violations = data['quarterly_violations']

        if monthly_data_list or quarterly_violations:
            click.echo(f"\nUser: {user}")
            click.echo(f"Total tracked: {data['all_shifts']} CSV rows, {data['all_days']} unique days, {data['all_hours']:.1f} hours")

            if monthly_data_list:
                click.echo("  Monthly breakdown:")
                # Sort by year and month
                sorted_months = sorted(monthly_data_list, key=lambda x: (x['year'], x['month']))

                for m in sorted_months:
                    # Check what triggered the violation
                    violations = []
                    if m['days'] > m['max_days']:
                        violations.append(f"days: {m['days']}/{m['max_days']}")
                    if m['hours'] > m['max_hours']:
                        violations.append(f"hours: {m['hours']:.1f}/{m['max_hours']}")

                    if violations:
                        status = f"✗ VIOLATION ({', '.join(violations)})"
                        color = 'red'
                    else:
                        status = "✓ Compliant"
                        color = 'green'

                    status_colored = click.style(status, fg=color, bold=True)
                    click.echo(f"    {m['month_name']:15} {status_colored}")
                    click.echo(f"      → {m['days']} Rufbereitschaften (days), {m['hours']:.1f} hours ({m['shifts']} CSV rows)")

            if quarterly_violations:
                click.echo("  Three-month period limit violations:")
                for v in quarterly_violations:
                    click.echo(f"    ✗ Period {v['period']}: {v['days']} days (limit: 30 days)")

    # Print detailed shift information if verbose mode is enabled
    if verbose:
        click.echo("\n--- DETAILED SHIFT INFORMATION ---")
        for user, data in sorted(results.items()):
            click.echo(f"\nUser: {user}")
            # You would add more detailed information about each shift here


@click.command()
@click.argument('csv_file', type=click.Path(exists=True))
@click.option('--timezone', default='UTC', help='Timezone to use for date calculations (default: UTC)')
@click.option('--verbose', is_flag=True, help='Print detailed information about each shift')
def main(csv_file, timezone, verbose):
    """
    Check on-call shift schedule compliance with Kollektivvertrag rules.

    This tool analyzes on-call shifts from a CSV file and verifies compliance with:

    \b
    1. Maximum 10 Rufbereitschaften per month (= 10 days with on-call duty)
    2. Maximum 168 hours of on-call per month
    3. Maximum 30 on-call days in any three-month period

    Note: One "Rufbereitschaft" = one calendar day where the person had on-call duty.

    The CSV file must contain columns: start, end, hours, user
    """
    # Load shifts data
    click.echo(f"Loading shifts data from {csv_file}...")
    df = load_shifts_from_csv(csv_file)
    click.echo(f"Loaded {len(df)} shifts for {df['user'].nunique()} users")

    # Display time range of the shifts
    if not df.empty:
        start_date = df['start'].min().strftime('%Y-%m-%d %H:%M')
        end_date = df['end'].max().strftime('%Y-%m-%d %H:%M')
        click.echo(f"Time range: {start_date} to {end_date}")

    # Analyze shifts
    click.echo(f"Analyzing shifts using timezone: {timezone}...")
    results = analyze_shifts(df, timezone)

    # Print report
    print_report(results, verbose)


if __name__ == "__main__":
    main()
