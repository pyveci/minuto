"""Parser tests for the JSM Ops timeline payload."""

from datetime import datetime
import pytest
import pytz

from minuto.jsm import parse_jsm_timeline


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
                        # in window, historical, user -> kept
                        {
                            "startDate": "2025-11-01T00:00:00Z",
                            "endDate": "2025-11-08T00:00:00Z",
                            "type": "historical",
                            "responder": {"id": "acct-alice", "type": "user"},
                        },
                        # historical but well before window -> dropped
                        {
                            "startDate": "2024-01-01T00:00:00Z",
                            "endDate": "2024-01-08T00:00:00Z",
                            "type": "historical",
                            "responder": {"id": "acct-alice", "type": "user"},
                        },
                        # current period -> dropped (not yet served)
                        {
                            "startDate": "2026-04-29T00:00:00Z",
                            "endDate": "2026-05-06T00:00:00Z",
                            "type": "active",
                            "responder": {"id": "acct-bob", "type": "user"},
                        },
                        # forecast -> dropped
                        {
                            "startDate": "2026-05-06T00:00:00Z",
                            "endDate": "2026-05-13T00:00:00Z",
                            "type": "forecast",
                            "responder": {"id": "acct-bob", "type": "user"},
                        },
                        # team responder -> dropped (only humans get paid)
                        {
                            "startDate": "2025-12-01T00:00:00Z",
                            "endDate": "2025-12-08T00:00:00Z",
                            "type": "historical",
                            "responder": {"id": "team-x", "type": "team"},
                        },
                        # second user, in window -> kept (verifies cache reuse)
                        {
                            "startDate": "2025-12-15T00:00:00Z",
                            "endDate": "2025-12-22T00:00:00Z",
                            "type": "historical",
                            "responder": {"id": "acct-bob", "type": "user"},
                        },
                        # repeat alice -> resolver should hit cache
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


def test_parses_only_historical_user_periods_in_window():
    resolver, _ = make_resolver()
    shifts = parse_jsm_timeline(
        fixture_payload(),
        start_date=pytz.UTC.localize(datetime(2025, 11, 1)),
        end_date=pytz.UTC.localize(datetime(2026, 4, 30)),
        user_resolver=resolver,
    )
    assert [s.user for s in shifts] == [
        "alice@example.com",
        "bob@example.com",
        "alice@example.com",
    ]
    assert all(s.hours == 168.0 for s in shifts)  # one week each


def test_filters_pre_window_period():
    resolver, _ = make_resolver()
    shifts = parse_jsm_timeline(
        fixture_payload(),
        start_date=pytz.UTC.localize(datetime(2025, 11, 1)),
        end_date=pytz.UTC.localize(datetime(2026, 4, 30)),
        user_resolver=resolver,
    )
    # The 2024-01-01 period must not appear
    assert all(s.start.year >= 2025 for s in shifts)


def test_skips_active_and_forecast_periods():
    resolver, _ = make_resolver()
    shifts = parse_jsm_timeline(
        fixture_payload(),
        start_date=pytz.UTC.localize(datetime(2025, 11, 1)),
        end_date=pytz.UTC.localize(datetime(2026, 5, 31)),
        user_resolver=resolver,
    )
    # Even with an end date that covers active/forecast windows, those types
    # must not produce shifts — they haven't actually been served.
    for s in shifts:
        assert s.start < pytz.UTC.localize(datetime(2026, 4, 1))


def test_skips_non_user_responder():
    resolver, calls = make_resolver()
    parse_jsm_timeline(
        fixture_payload(),
        start_date=pytz.UTC.localize(datetime(2025, 11, 1)),
        end_date=pytz.UTC.localize(datetime(2026, 4, 30)),
        user_resolver=resolver,
    )
    # Resolver must never be asked about team-x
    assert "team-x" not in calls


def test_resolver_called_per_unique_user_when_caller_caches():
    """The parser itself does not cache, but fetch_shifts_from_jsm wraps the
    resolver in a caching closure. Simulate that here to assert the contract:
    when the resolver caches, repeat accountIds don't trigger extra lookups.
    """
    base_resolver, calls = make_resolver()
    cache = {}

    def caching_resolver(account_id):
        if account_id not in cache:
            cache[account_id] = base_resolver(account_id)
        return cache[account_id]

    parse_jsm_timeline(
        fixture_payload(),
        start_date=pytz.UTC.localize(datetime(2025, 11, 1)),
        end_date=pytz.UTC.localize(datetime(2026, 4, 30)),
        user_resolver=caching_resolver,
    )
    # alice appears in two kept periods, bob in one — the underlying resolver
    # should have been called exactly twice (once per unique accountId).
    assert sorted(calls) == ["acct-alice", "acct-bob"]


def test_malformed_payload_raises():
    resolver, _ = make_resolver()
    with pytest.raises(RuntimeError, match="Malformed JSM timeline"):
        parse_jsm_timeline(
            {"unexpected": "shape"},
            start_date=pytz.UTC.localize(datetime(2025, 11, 1)),
            end_date=pytz.UTC.localize(datetime(2026, 4, 30)),
            user_resolver=resolver,
        )


def test_naive_window_dates_are_utc_localized_by_caller():
    """Parser requires tz-aware datetimes; caller (fetch_shifts_from_jsm)
    is responsible for localizing. This test documents the contract: passing
    a naive datetime to the parser will raise on comparison.
    """
    resolver, _ = make_resolver()
    with pytest.raises(TypeError):
        parse_jsm_timeline(
            fixture_payload(),
            start_date=datetime(2025, 11, 1),  # naive
            end_date=datetime(2026, 4, 30),
            user_resolver=resolver,
        )
