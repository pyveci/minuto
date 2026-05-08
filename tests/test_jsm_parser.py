"""Parser tests for the JSM Ops timeline payload."""

from datetime import datetime
import pytest
import pytz

from minuto.jsm import (
    UnresolvedAccountError,
    collect_account_ids,
    parse_jsm_timeline,
)


def fixture_payload():
    """A timeline payload covering every parser branch.

    Shape mirrors the real JSM Ops response observed against the Crate tenant.
    Account IDs are synthetic (not real Crate users) to keep test data clean.
    """
    return {
        "startDate": "2025-10-31T23:00:00Z",
        "endDate": "2026-04-30T22:00:00Z",
        "finalTimeline": {
            "rotations": [
                {
                    "id": "rot-1",
                    "name": "Rot2",
                    "periods": [
                        # in window, historical, user -> kept in both modes
                        {
                            "startDate": "2025-11-01T00:00:00Z",
                            "endDate": "2025-11-08T00:00:00Z",
                            "type": "historical",
                            "responder": {"id": "acct-alice", "type": "user"},
                        },
                        # historical but well before window -> always dropped
                        {
                            "startDate": "2024-01-01T00:00:00Z",
                            "endDate": "2024-01-08T00:00:00Z",
                            "type": "historical",
                            "responder": {"id": "acct-alice", "type": "user"},
                        },
                        # active, overlaps window -> kept by default, dropped if historical_only
                        {
                            "startDate": "2026-04-29T00:00:00Z",
                            "endDate": "2026-05-06T00:00:00Z",
                            "type": "active",
                            "responder": {"id": "acct-bob", "type": "user"},
                        },
                        # forecast outside window -> always dropped
                        {
                            "startDate": "2026-05-06T00:00:00Z",
                            "endDate": "2026-05-13T00:00:00Z",
                            "type": "forecast",
                            "responder": {"id": "acct-bob", "type": "user"},
                        },
                        # team responder -> always dropped (only humans get paid)
                        {
                            "startDate": "2025-12-01T00:00:00Z",
                            "endDate": "2025-12-08T00:00:00Z",
                            "type": "historical",
                            "responder": {"id": "team-x", "type": "team"},
                        },
                        # second user, in window -> kept in both modes
                        {
                            "startDate": "2025-12-15T00:00:00Z",
                            "endDate": "2025-12-22T00:00:00Z",
                            "type": "historical",
                            "responder": {"id": "acct-bob", "type": "user"},
                        },
                        # repeat alice -> kept in both modes
                        {
                            "startDate": "2026-01-05T00:00:00Z",
                            "endDate": "2026-01-12T00:00:00Z",
                            "type": "historical",
                            "responder": {"id": "acct-alice", "type": "user"},
                        },
                    ],
                }
            ]
        },
    }


def make_resolver():
    """Resolver that records each call so tests can assert cache behavior."""
    calls = []
    table = {
        "acct-alice": "alice@example.com",
        "acct-bob": "bob@example.com",
    }

    def resolver(account_id):
        calls.append(account_id)
        return table[account_id]

    return resolver, calls


WINDOW_START = pytz.UTC.localize(datetime(2025, 11, 1))
WINDOW_END = pytz.UTC.localize(datetime(2026, 4, 30))


def test_parses_all_user_periods_in_window_by_default():
    """Default behavior matches OpsGenie: include active periods that overlap
    the window. The active period straddles the end of the window, so it
    counts. The forecast period sits entirely after the window, so it doesn't.
    """
    resolver, _ = make_resolver()
    shifts = parse_jsm_timeline(
        fixture_payload(), WINDOW_START, WINDOW_END, user_resolver=resolver,
    )
    assert [s.user for s in shifts] == [
        "alice@example.com",   # 2025-11-01 historical
        "bob@example.com",     # 2026-04-29 active (kept by default)
        "bob@example.com",     # 2025-12-15 historical
        "alice@example.com",   # 2026-01-05 historical
    ]


def test_historical_only_excludes_active_and_forecast():
    """The opt-in flag for payroll runs: only periods that have actually
    been served are returned.
    """
    resolver, _ = make_resolver()
    shifts = parse_jsm_timeline(
        fixture_payload(), WINDOW_START, WINDOW_END,
        user_resolver=resolver, historical_only=True,
    )
    assert [s.user for s in shifts] == [
        "alice@example.com",
        "bob@example.com",
        "alice@example.com",
    ]
    assert all(s.hours == 168.0 for s in shifts)  # one week each


def test_filters_pre_window_period():
    """The 2024-01-01 historical period is well outside the window and must
    not appear regardless of the historical_only flag.
    """
    resolver, _ = make_resolver()
    for historical_only in (False, True):
        shifts = parse_jsm_timeline(
            fixture_payload(), WINDOW_START, WINDOW_END,
            user_resolver=resolver, historical_only=historical_only,
        )
        assert all(s.start.year >= 2025 for s in shifts)


def test_skips_non_user_responder():
    """team-x is a team responder, not a person — it never gets paid."""
    resolver, calls = make_resolver()
    parse_jsm_timeline(
        fixture_payload(), WINDOW_START, WINDOW_END, user_resolver=resolver,
    )
    assert "team-x" not in calls


def test_resolver_is_called_per_period_when_caller_doesnt_cache():
    """The parser doesn't cache by itself — fetch_shifts_from_jsm pre-resolves
    all account IDs and passes a dict-lookup resolver. This test documents
    the parser's contract: it calls the resolver once per kept period.
    """
    resolver, calls = make_resolver()
    parse_jsm_timeline(
        fixture_payload(), WINDOW_START, WINDOW_END,
        user_resolver=resolver, historical_only=True,
    )
    # Three periods kept (alice, bob, alice) -> three resolver calls.
    assert calls == ["acct-alice", "acct-bob", "acct-alice"]


def test_malformed_payload_raises():
    resolver, _ = make_resolver()
    with pytest.raises(RuntimeError, match="Malformed JSM timeline"):
        parse_jsm_timeline(
            {"unexpected": "shape"}, WINDOW_START, WINDOW_END,
            user_resolver=resolver,
        )


def test_naive_window_dates_raise():
    """Parser requires tz-aware datetimes; caller (fetch_shifts_from_jsm)
    is responsible for localizing.
    """
    resolver, _ = make_resolver()
    with pytest.raises(TypeError):
        parse_jsm_timeline(
            fixture_payload(),
            start_date=datetime(2025, 11, 1),  # naive
            end_date=datetime(2026, 4, 30),
            user_resolver=resolver,
        )


def test_collect_account_ids_returns_unique_user_ids_only():
    """Pre-resolve helper: extract every distinct user accountId. Team
    responders and missing IDs must not appear.
    """
    ids = collect_account_ids(fixture_payload())
    assert ids == {"acct-alice", "acct-bob"}


def test_collect_account_ids_raises_on_malformed_payload():
    with pytest.raises(RuntimeError, match="Malformed JSM timeline"):
        collect_account_ids({"unexpected": "shape"})


def test_unresolved_account_error_lists_all_ids():
    """The error must surface every unresolved ID, not just the first —
    so users can fix all the privacy/scope issues in one round-trip.
    """
    err = UnresolvedAccountError(["acct-foo", "acct-bar"])
    assert err.account_ids == ["acct-foo", "acct-bar"]
    msg = str(err)
    assert "acct-foo" in msg and "acct-bar" in msg
    assert "2 account ID(s)" in msg
