"""Today's heating on-time in the thermostat's own time zone.

All window arithmetic is done on UTC instants, so days with a daylight-saving
change (23 or 25 hours long, or with no local midnight at all) get the right
window. Time-zone lookups are the caller's: pass a ready ``tzinfo`` object.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo

from .models import UsageDay

# Mysa's day start can be a few seconds off the computed midnight.
_DAY_START_TOLERANCE = timedelta(minutes=1)


def local_day_start(now: datetime, time_zone: tzinfo) -> datetime:
    """The first instant of ``now``'s calendar day in ``time_zone``.

    Usually local midnight. When the clocks skip midnight (spring forward at
    00:00), it is the moment of the change, shown as the first wall time that
    exists (01:00). When midnight happens twice (fall back to 00:00), it is the
    first of the two. The result is aware, in ``time_zone``.

    Raises ``ValueError`` if ``now`` is naive.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    day = now.astimezone(time_zone).date()
    starts = []
    for fold in (0, 1):
        midnight = datetime.combine(day, time(0, fold=fold), tzinfo=time_zone)
        # Round-trip through UTC: this normalises a wall time that the clocks
        # skipped, and tells a skipped midnight from a real one.
        normalised = midnight.astimezone(UTC).astimezone(time_zone)
        if normalised.date() == day:
            starts.append(normalised)
    if not starts:  # a zone that skipped the whole day; ``now`` can't be in it
        return datetime.combine(day, time(0), tzinfo=time_zone)
    return min(starts, key=lambda start: start.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class UsageToday:
    """Heating on-time from local midnight until ``end``.

    ``start`` is the local day start in the requested time zone (see
    ``local_day_start``) and ``end`` the ``now`` the window was read up to.
    ``heat_seconds`` sums ``UsageDay.heat_seconds`` over the days Mysa returned
    that start at or after ``start`` (with one minute of tolerance); days with
    no value count as zero. ``days`` holds the parsed response unchanged.
    """

    start: datetime
    end: datetime
    heat_seconds: float
    days: tuple[UsageDay, ...]

    @classmethod
    def from_days(
        cls, days: Iterable[UsageDay], start: datetime, end: datetime
    ) -> UsageToday:
        """Sum a ``get_usage()`` response for the window ``start`` to ``end``."""
        kept = tuple(days)
        earliest = start.astimezone(UTC) - _DAY_START_TOLERANCE
        seconds = 0.0
        for day in kept:
            if day.start >= earliest and day.heat_seconds:
                seconds += day.heat_seconds
        return cls(start=start, end=end, heat_seconds=seconds, days=kept)


__all__ = ["UsageToday", "local_day_start"]
