"""
Jira Service Management (JSM) Operations API client.

JSM Ops is the post-migration home of Atlassian on-call schedules (formerly
OpsGenie). Schedule UUIDs carry over 1:1 from OpsGenie. JSM responses contain
Atlassian account IDs rather than emails, so we resolve them via the Jira
platform user endpoint to keep downstream code email-keyed.

Resolution is done in two passes — first collect all unique account IDs from
the timeline payload, then resolve them in one go — so that any users we can't
resolve (e.g. because the API token lacks the privacy scope to read their
emailAddress) are reported all at once instead of being silently substituted
with placeholder emails that would land in compensation reports.
"""

import logging
from datetime import datetime
from typing import Callable, Dict, List, Set

import pytz
import requests
from dateutil import parser

from minuto.models import OnCallShift

logger = logging.getLogger(__name__)

JSM_TIMELINE_URL = (
    "https://api.atlassian.com/jsm/ops/api/{cloud_id}/v1"
    "/schedules/{schedule_id}/timeline"
)
JIRA_USER_URL = "https://{site_host}/rest/api/3/user"


class UnresolvedAccountError(Exception):
    """Raised when one or more JSM account IDs can't be resolved to an email.

    Carries the full list of unresolved IDs so callers (and the CLI) can
    display them in a single actionable error rather than failing at the
    first one.
    """

    def __init__(self, account_ids: List[str]):
        self.account_ids = list(account_ids)
        super().__init__(
            f"Could not resolve {len(self.account_ids)} account ID(s) "
            f"to email addresses: {', '.join(self.account_ids)}"
        )


def resolve_account_id_to_email(
    site_host: str,
    email: str,
    api_token: str,
    account_id: str,
) -> str:
    """Resolve an Atlassian accountId to its email via the Jira REST user lookup.

    Raises RuntimeError on HTTP failure or missing emailAddress. Callers do
    their own caching (we expect them to call this at most once per unique
    accountId per run).
    """
    response = requests.get(
        JIRA_USER_URL.format(site_host=site_host),
        params={"accountId": account_id},
        auth=(email, api_token),
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Jira user lookup returned HTTP {response.status_code} "
            f"for accountId {account_id}"
        )
    resolved = response.json().get("emailAddress")
    if not resolved:
        # Atlassian privacy controls (or scope-limited tokens) cause emailAddress
        # to be omitted. Don't synthesize a fallback — the caller needs to know.
        raise RuntimeError(
            f"No emailAddress returned for accountId {account_id} "
            "(token may lack the required privacy scope, or the user has "
            "hidden their email in their Atlassian profile)"
        )
    return resolved


def collect_account_ids(payload: dict) -> Set[str]:
    """Extract every user accountId referenced in a JSM timeline payload.

    Used to pre-resolve all users before parsing, so the parser's resolver
    is guaranteed to succeed and any unresolvable IDs surface as a single
    UnresolvedAccountError listing all of them.
    """
    try:
        rotations = payload["finalTimeline"]["rotations"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"Malformed JSM timeline response: {e}")

    ids: Set[str] = set()
    for rotation in rotations:
        for item in rotation.get("periods", []):
            responder = item.get("responder") or {}
            if responder.get("type") == "user" and responder.get("id"):
                ids.add(responder["id"])
    return ids


def parse_jsm_timeline(
    payload: dict,
    start_date: datetime,
    end_date: datetime,
    user_resolver: Callable[[str], str],
    historical_only: bool = False,
) -> List[OnCallShift]:
    """Convert a JSM Ops timeline payload into OnCallShift records.

    Pure function — no I/O. Tests inject a fake resolver to exercise the
    parsing rules without hitting the network.

    Filters applied:
      - shift must overlap [start_date, end_date]
      - responder must be a user (skip team/escalation responders)
      - if historical_only=True, drop "active" and "forecast" periods

    `historical_only` defaults to False to match the OpsGenie command's
    behavior. Set it (or pass `--historical-only` on the CLI) when running
    payroll calculations mid-period: an active shift hasn't been fully
    served yet, and a forecast shift might be reassigned.
    """
    try:
        rotations = payload["finalTimeline"]["rotations"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"Malformed JSM timeline response: {e}")

    shifts: List[OnCallShift] = []
    for rotation in rotations:
        for item in rotation.get("periods", []):
            if historical_only and item.get("type") != "historical":
                continue

            shift_start = parser.parse(item["startDate"])
            if shift_start.tzinfo is None:
                shift_start = pytz.UTC.localize(shift_start)
            shift_end = parser.parse(item["endDate"])
            if shift_end.tzinfo is None:
                shift_end = pytz.UTC.localize(shift_end)

            if shift_end < start_date or shift_start > end_date:
                continue

            responder = item.get("responder") or {}
            if responder.get("type") != "user" or not responder.get("id"):
                continue

            shifts.append(OnCallShift(
                start=shift_start,
                end=shift_end,
                hours=round((shift_end - shift_start).total_seconds() / 3600, 2),
                user=user_resolver(responder["id"]),
            ))
    return shifts


def fetch_shifts_from_jsm(
    cloud_id: str,
    site_host: str,
    email: str,
    api_token: str,
    schedule_id: str,
    start_date: datetime,
    end_date: datetime,
    historical_only: bool = False,
) -> List[OnCallShift]:
    """Fetch on-call shifts from JSM Ops for a schedule and date window.

    Raises UnresolvedAccountError if any user account ID in the response
    can't be resolved to an email — better to fail loudly than to land
    placeholder emails in a compensation report.
    """
    if start_date.tzinfo is None:
        start_date = pytz.UTC.localize(start_date)
    if end_date.tzinfo is None:
        end_date = pytz.UTC.localize(end_date)

    months_diff = (
        (end_date.year - start_date.year) * 12
        + end_date.month - start_date.month
        + 1
    )

    response = requests.get(
        JSM_TIMELINE_URL.format(cloud_id=cloud_id, schedule_id=schedule_id),
        params={
            "identifierType": "id",
            "intervalUnit": "months",
            "interval": months_diff,
            "date": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        auth=(email, api_token),
    )
    if response.status_code != 200:
        msg = f"Error from JSM Ops API: {response.status_code}"
        try:
            msg += f" - {response.json()}"
        except Exception:
            pass
        raise RuntimeError(msg)

    payload = response.json()

    # Pre-resolve all accountIds so unresolvable users are reported together.
    emails: Dict[str, str] = {}
    unresolved: List[str] = []
    for account_id in sorted(collect_account_ids(payload)):
        try:
            emails[account_id] = resolve_account_id_to_email(
                site_host, email, api_token, account_id,
            )
        except RuntimeError as e:
            logger.warning("Failed to resolve %s: %s", account_id, e)
            unresolved.append(account_id)

    if unresolved:
        raise UnresolvedAccountError(unresolved)

    return parse_jsm_timeline(
        payload, start_date, end_date,
        user_resolver=lambda aid: emails[aid],
        historical_only=historical_only,
    )
