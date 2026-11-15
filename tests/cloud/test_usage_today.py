"""Today's usage window, including days when the clocks change."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from mysa_client.cloud import (
    MysaAuth,
    MysaClient,
    UsageDay,
    UsageToday,
    local_day_start,
)
from tests.cloud_helpers import DEVICE_ID, REFRESH_TOKEN, FakeCloud

USAGE_PATH = f"/telemetry/usage/{DEVICE_ID}"


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:  # pragma: no cover - depends on the host
        pytest.skip(f"time zone data has no {name}")


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _day(start: datetime, seconds: float | None) -> UsageDay:
    return UsageDay(
        start=start, heat_seconds=seconds, average_temperature=None, buckets=()
    )


def _api_day(start: datetime, seconds: float) -> dict[str, object]:
    stamp = int(start.timestamp())
    return {"T": stamp, "summary": {"T": stamp, "a": {"oH1": seconds}}}


def _client(session) -> MysaClient:
    auth = MysaAuth(session, REFRESH_TOKEN, user_agent="t")
    return MysaClient(session, auth, user_agent="t")


def test_ordinary_day_starts_at_local_midnight() -> None:
    """Without a clock change the window starts at 00:00 local."""
    toronto = _zone("America/Toronto")
    start = local_day_start(_utc(2026, 1, 15, 3, 30), toronto)
    # 03:30Z is still 22:30 on the 14th in Toronto.
    assert start == datetime(2026, 1, 14, tzinfo=toronto)
    assert start.astimezone(UTC) == _utc(2026, 1, 14, 5)
    assert start.tzinfo is toronto


def test_23_hour_day() -> None:
    """Spring forward at 02:00: the window is midnight-based and 1 h shorter."""
    toronto = _zone("America/Toronto")
    assert datetime(2026, 3, 8, 1, tzinfo=toronto).utcoffset() == timedelta(hours=-5)
    assert datetime(2026, 3, 8, 4, tzinfo=toronto).utcoffset() == timedelta(hours=-4)
    late = datetime(2026, 3, 8, 23, 30, tzinfo=toronto)
    start = local_day_start(late, toronto)
    assert start.astimezone(UTC) == _utc(2026, 3, 8, 5)
    assert late.astimezone(UTC) - start.astimezone(UTC) == timedelta(
        hours=22, minutes=30
    )
    # The next day starts 23 hours after this one.
    tomorrow = local_day_start(late + timedelta(hours=1), toronto)
    assert tomorrow.astimezone(UTC) - start.astimezone(UTC) == timedelta(hours=23)


def test_25_hour_day() -> None:
    """Fall back at 02:00: both passes through 01:xx belong to the same day."""
    toronto = _zone("America/Toronto")
    first = _utc(2026, 11, 1, 5, 30)  # 01:30 EDT
    second = _utc(2026, 11, 1, 6, 30)  # 01:30 EST, the repeated hour
    assert first.astimezone(toronto).fold == 0
    assert second.astimezone(toronto).fold == 1
    expected = _utc(2026, 11, 1, 4)
    for now in (first, second):
        assert local_day_start(now, toronto).astimezone(UTC) == expected
    late = _utc(2026, 11, 2, 4, 30)  # 23:30 EST on the 1st
    start = local_day_start(late, toronto)
    assert start.astimezone(UTC) == expected
    assert late - start.astimezone(UTC) == timedelta(hours=24, minutes=30)
    tomorrow = local_day_start(late + timedelta(hours=1), toronto)
    assert tomorrow.astimezone(UTC) - expected == timedelta(hours=25)


def test_skipped_midnight() -> None:
    """Spring forward at 00:00: the day starts at the change, shown as 01:00."""
    azores = _zone("Atlantic/Azores")
    change = _utc(2026, 3, 29, 1)
    assert (change - timedelta(seconds=1)).astimezone(azores).day == 28
    assert change.astimezone(azores) == datetime(2026, 3, 29, 1, tzinfo=azores)
    start = local_day_start(_utc(2026, 3, 29, 12), azores)
    assert start.astimezone(UTC) == change
    assert (start.hour, start.minute) == (1, 0)
    assert start.utcoffset() == timedelta(0)


def test_repeated_midnight() -> None:
    """Fall back to 00:00: the first of the two midnights starts the day."""
    azores = _zone("Atlantic/Azores")
    now = _utc(2026, 10, 25, 1, 30)  # 00:30, second pass
    local = now.astimezone(azores)
    assert (local.hour, local.fold) == (0, 1)
    start = local_day_start(now, azores)
    assert start.astimezone(UTC) == _utc(2026, 10, 25, 0)
    # Mysa Local 0.9's ``now.replace(hour=0, ...)`` keeps fold=1 and starts
    # the window an hour late while the repeated hour lasts.
    naive_rule = local.replace(hour=0, minute=0, second=0, microsecond=0)
    assert naive_rule.astimezone(UTC) == _utc(2026, 10, 25, 1)


def test_fixed_offset_and_naive_now() -> None:
    """Any tzinfo works; a naive ``now`` is refused rather than guessed."""
    minus_five = timezone(timedelta(hours=-5))
    start = local_day_start(_utc(2026, 1, 15, 3), minus_five)
    assert start.astimezone(UTC) == _utc(2026, 1, 14, 5)
    with pytest.raises(ValueError, match="aware"):
        local_day_start(datetime(2026, 1, 15), UTC)  # noqa: DTZ001


def test_sum_matches_the_day_start_rule() -> None:
    """Days from one minute before the start count; earlier ones and None don't."""
    start = datetime(2026, 1, 14, tzinfo=_zone("America/Toronto"))
    begin = start.astimezone(UTC)
    days = [
        _day(begin - timedelta(days=1), 999.0),
        _day(begin - timedelta(minutes=2), 50.0),
        _day(begin - timedelta(seconds=30), 100.0),
        _day(begin + timedelta(hours=6), None),
        _day(begin + timedelta(hours=7), 0.0),
        _day(begin + timedelta(hours=8), 20.5),
    ]
    end = begin + timedelta(hours=9)
    usage = UsageToday.from_days(days, start, end)
    assert usage.heat_seconds == 120.5
    assert usage.start is start
    assert usage.end is end
    assert usage.days == tuple(days)
    assert UsageToday.from_days([], start, end).heat_seconds == 0.0


def test_sum_tolerance_is_measured_in_real_time() -> None:
    """On a skipped-midnight day, 'one minute before 01:00' is 00:59Z, not later.

    Wall-clock arithmetic would land on 00:59 local, inside the gap, which
    ``zoneinfo`` reads as 01:59Z and so drops the day Mysa reports at 01:00Z.
    """
    azores = _zone("Atlantic/Azores")
    start = local_day_start(_utc(2026, 3, 29, 12), azores)
    assert (start - timedelta(minutes=1)).astimezone(UTC) == _utc(2026, 3, 29, 1, 59)
    usage = UsageToday.from_days([_day(_utc(2026, 3, 29, 1), 60.0)], start, start)
    assert usage.heat_seconds == 60.0


async def test_get_usage_today_request_and_total(session, cloud: FakeCloud) -> None:
    """One usage request from local midnight to now, with the zone's name."""
    toronto = _zone("America/Toronto")
    now = _utc(2026, 11, 2, 4, 30)  # 23:30 on the 25-hour day
    midnight = _utc(2026, 11, 1, 4)
    cloud.usage = [
        _api_day(midnight - timedelta(days=1), 500.0),
        _api_day(midnight, 1800.0),
    ]
    usage = await _client(session).get_usage_today(DEVICE_ID, toronto, now=now)
    assert usage.heat_seconds == 1800.0
    assert usage.start.astimezone(UTC) == midnight
    assert usage.end == now
    assert len(usage.days) == 2
    (body,) = cloud.calls("POST", USAGE_PATH)
    assert body["timezone"] == "America/Toronto"
    assert body["start"] == int(midnight.timestamp())
    assert body["end"] == int(now.timestamp())


async def test_get_usage_today_name_override_and_default_now(
    session, cloud: FakeCloud
) -> None:
    """The zone name can differ from the tzinfo; ``now`` defaults to the clock."""
    cloud.usage = []
    before = datetime.now(UTC)
    usage = await _client(session).get_usage_today(
        DEVICE_ID, UTC, time_zone_name="Not/A_Zone"
    )
    assert before <= usage.end <= datetime.now(UTC)
    assert usage.start == datetime.combine(usage.end.date(), datetime.min.time(), UTC)
    assert usage.heat_seconds == 0.0
    (body,) = cloud.calls("POST", USAGE_PATH)
    assert body["timezone"] == "Not/A_Zone"
    # Without an override, a fixed-offset zone sends its str(), here "UTC".
    await _client(session).get_usage_today(DEVICE_ID, UTC)
    assert cloud.calls("POST", USAGE_PATH)[-1]["timezone"] == "UTC"
