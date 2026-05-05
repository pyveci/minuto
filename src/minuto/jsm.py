"""
Jira Service Management (JSM) Operations API client.

JSM Ops is the post-migration home of Atlassian on-call schedules (formerly
OpsGenie). Schedule UUIDs carry over 1:1 from OpsGenie. JSM responses contain
Atlassian account IDs rather than emails, so we resolve them via the Jira
platform user endpoint to keep downstream code email-keyed.
"""

import logging
from datetime import datetime
from typing import Callable, Dict, List

import pytz
import requests
from dateutil import parser

from minuto.main import OnCallShift

logger = logging.getLogger(__name__)

JSM_TIMELINE_URL = (
    "https://api.atlassian.com/jsm/ops/api/{cloud_id}/v1"
    "/schedules/{schedule_id}/timeline"
)
JIRA_USER_URL = "https://{site_host}/rest/api/3/user"


def resolve_account_id_to_email(
    site_host: str,
    email: str,
    api_token: str,
    account_id: str,
    cache: Dict[str, str],
) -> str:
    """Resolve an Atlassian accountId to its email via the Jira REST user lookup.

    The cache dict is mutated in place so repeated calls within one fetch only
    hit the network once per unique account.
    """
    if account_id in cache:
        return cache[account_id]

    response = requests.get(
        JIRA_USER_URL.format(site_host=site_host),
        params={"accountId": account_id},
        auth=(email, api_token),
    )
    if response.status_code != 200:
        logger.warning(
            "Could not resolve accountId %s (HTTP %s); using placeholder email",
            account_id, response.status_code,
        )
        resolved = f"unknown-{account_id}@unresolved.local"
    else:
        # emailAddress is only returned when the token holder has the right
        # privacy scope. Fall back to a synthetic per-id email so distinct
        # users don't get silently merged in compensation reports.
        resolved = response.json().get("emailAddress") \
            or f"unknown-{account_id}@unresolved.local"

    cache[account_id] = resolved
    return resolved


def parse_jsm_timeline(
    payload: dict,
    start_date: datetime,
    end_date: datetime,
    user_resolver: Callable[[str], str],
) -> List[OnCallShift]:
    """Convert a JSM Ops timeline payload into OnCallShift records.

    Pure function — no I/O. Tests inject a fake resolver to exercise the
    parsing rules without hitting the network.

    Filters applied:
      - period.type must be "historical" (skip current/forecast)
      - shift must overlap [start_date, end_date]
      - responder must be a user (skip team/escalation responders)
    """
    try:
        rotations = payload["finalTimeline"]["rotations"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"Malformed JSM timeline response: {e}")

    shifts: List[OnCallShift] = []
    for rotation in rotations:
        for item in rotation.get("periods", []):
            if item.get("type") != "historical":
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
) -> List[OnCallShift]:
    """Fetch on-call shifts from JSM Ops for a schedule and date window."""
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

    cache: Dict[str, str] = {}

    def resolver(account_id: str) -> str:
        return resolve_account_id_to_email(
            site_host, email, api_token, account_id, cache,
        )

    return parse_jsm_timeline(response.json(), start_date, end_date, resolver)
